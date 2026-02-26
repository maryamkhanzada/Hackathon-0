"""
resilience.py — Shared Error-Recovery Utilities for Personal AI Employee (Gold Tier)

Provides a single import for all resilience primitives used across every script:

  @retry                 — decorator: max 3 attempts, backoff = 2**attempt seconds
  with_retry()           — functional form of the same retry logic
  LocalCache             — file-based cache; serve stale data when API is down
  DegradedMode           — context manager: activate essential-only mode
  disk_check()           — raise DiskFullError when free space < threshold
  queue_for_retry()      — persist a failed task to Pending/ for later replay
  write_pid() / clear_pid() — PID-file helpers for watchdog integration

Usage in any script:
    from resilience import retry, LocalCache, disk_check, queue_for_retry

    @retry(max_retries=3, backoff_base=2, label="gmail_fetch")
    def fetch_emails():
        ...                     # retried up to 3 times on any exception

    cache = LocalCache("gmail")
    data  = cache.load()        # returns last good data if API is down
    cache.save(data)            # call after every successful fetch

    disk_check()                # raises DiskFullError if < DISK_ALERT_GB free

    queue_for_retry(            # write failed task to Pending/ for watchdog replay
        task_name="send_email",
        payload={"to": "x@y.com"},
        source="gmail_watcher",
    )
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import pickle
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR  = Path(__file__).resolve().parent
_VAULT_ROOT  = _SCRIPT_DIR.parent
_CACHE_DIR   = _VAULT_ROOT / ".cache"
_PIDS_DIR    = _VAULT_ROOT / ".pids"
_PENDING_DIR = _VAULT_ROOT / "Pending"
_LOGS_DIR    = _VAULT_ROOT / "Logs"

for _d in (_CACHE_DIR, _PIDS_DIR, _PENDING_DIR, _LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Constants (override via env)
# ---------------------------------------------------------------------------

RETRY_MAX        = int(os.environ.get("RETRY_MAX", 3))
RETRY_BACKOFF    = int(os.environ.get("RETRY_BACKOFF_BASE", 2))   # seconds; delay = base**attempt
DISK_ALERT_GB    = float(os.environ.get("DISK_ALERT_GB", 1.0))    # alert when free < 1 GB
DISK_CRITICAL_GB = float(os.environ.get("DISK_CRITICAL_GB", 0.2)) # degrade when free < 200 MB
MAX_QUEUE_SIZE   = int(os.environ.get("MAX_QUEUE_SIZE", 50))       # Handbook: max 50 queued items

logger = logging.getLogger("resilience")

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RetryExhausted(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, label: str, attempts: int, last_exc: Exception):
        self.label    = label
        self.attempts = attempts
        self.last_exc = last_exc
        super().__init__(
            f"[{label}] Failed after {attempts} attempt(s): {last_exc}"
        )


class DiskFullError(Exception):
    """Raised when available disk space falls below DISK_ALERT_GB."""
    pass


class DegradedModeError(Exception):
    """Raised when an operation is not permitted in degraded mode."""
    pass


# ---------------------------------------------------------------------------
# Retry decorator / function
# ---------------------------------------------------------------------------


def retry(
    max_retries: int = RETRY_MAX,
    backoff_base: int = RETRY_BACKOFF,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    label: str = "",
    reraise: bool = True,
):
    """
    Decorator factory. Retries the wrapped function up to `max_retries` times.
    Delay between attempts = backoff_base ** attempt_number seconds.

    Args:
        max_retries:  Maximum number of attempts (default 3).
        backoff_base: Base for exponential backoff (default 2 → 2s, 4s, 8s).
        exceptions:   Tuple of exception types to catch and retry on.
        label:        Human-readable label for log messages.
        reraise:      If True, raise RetryExhausted after all attempts fail.

    Example:
        @retry(max_retries=3, backoff_base=2, label="gmail_fetch")
        def fetch_emails(since: str) -> list:
            return api.list_messages(since)

        # Functional form:
        result = retry(label="send")(my_fn)(arg1, arg2)
    """
    def decorator(fn: Callable) -> Callable:
        fn_label = label or fn.__qualname__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    wait = backoff_base ** attempt
                    _log_retry_event(fn_label, attempt, max_retries, exc, wait)
                    if attempt < max_retries:
                        time.sleep(wait)
            if reraise:
                raise RetryExhausted(fn_label, max_retries, last_exc)
            return None
        return wrapper
    return decorator


def with_retry(
    fn: Callable,
    *args,
    max_retries: int = RETRY_MAX,
    backoff_base: int = RETRY_BACKOFF,
    label: str = "",
    exceptions: tuple[type[Exception], ...] = (Exception,),
    reraise: bool = True,
    **kwargs,
) -> Any:
    """
    Functional (non-decorator) form of the retry logic.

    Example:
        result = with_retry(api.fetch, user_id, max_retries=3, label="api_fetch")
    """
    wrapped = retry(
        max_retries=max_retries,
        backoff_base=backoff_base,
        exceptions=exceptions,
        label=label or getattr(fn, "__name__", "fn"),
        reraise=reraise,
    )(fn)
    return wrapped(*args, **kwargs)


def _log_retry_event(label: str, attempt: int, max_retries: int,
                     exc: Exception, wait_secs: float) -> None:
    """Log a retry event to stderr and to Logs/resilience_{date}.json."""
    logger.warning(
        "[%s] Attempt %d/%d failed (%s: %s). Retrying in %.0fs...",
        label, attempt, max_retries, type(exc).__name__, exc, wait_secs,
    )
    _append_resilience_log({
        "event":       "retry",
        "label":       label,
        "attempt":     attempt,
        "max_retries": max_retries,
        "error_type":  type(exc).__name__,
        "error":       str(exc),
        "wait_secs":   wait_secs,
    })


# ---------------------------------------------------------------------------
# Local cache (pickle-based; keyed by name + optional cache_key)
# ---------------------------------------------------------------------------


class LocalCache:
    """
    File-based cache. Stores the last successful result of any API call.
    Serves stale cached data as a fallback when the API is unreachable.

    Usage:
        cache = LocalCache("gmail_messages")
        messages = cache.load()          # None if no cache exists
        if messages is None:
            messages = fetch_from_api()
            cache.save(messages)
    """

    def __init__(self, name: str, cache_key: str = "default",
                 ttl_seconds: int = 86400):
        self.name         = name
        self.cache_key    = hashlib.sha256(cache_key.encode()).hexdigest()[:12]
        self.ttl_seconds  = ttl_seconds
        self._path        = _CACHE_DIR / f"{name}_{self.cache_key}.pkl"
        self._meta_path   = _CACHE_DIR / f"{name}_{self.cache_key}.json"

    def save(self, data: Any) -> None:
        """Persist data to disk cache."""
        try:
            with open(self._path, "wb") as f:
                pickle.dump(data, f)
            meta = {
                "name":       self.name,
                "saved_at":   datetime.now().isoformat(),
                "ttl_seconds": self.ttl_seconds,
            }
            self._meta_path.write_text(json.dumps(meta), encoding="utf-8")
            logger.debug("[cache] Saved: %s", self._path.name)
        except OSError as exc:
            logger.warning("[cache] Could not save %s: %s", self.name, exc)

    def load(self) -> Any | None:
        """
        Load cached data. Returns None if:
          - No cache file exists
          - Cache is older than ttl_seconds
          - Cache file is corrupt
        """
        if not self._path.exists():
            return None
        try:
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            saved_at = datetime.fromisoformat(meta["saved_at"])
            age = (datetime.now() - saved_at).total_seconds()
            if age > self.ttl_seconds:
                logger.info("[cache] Stale (%ds old, TTL=%ds): %s",
                            int(age), self.ttl_seconds, self.name)
                # Still return stale data as fallback — caller decides
            with open(self._path, "rb") as f:
                data = pickle.load(f)
            logger.info("[cache] Loaded %s (age=%ds, stale=%s)",
                        self.name, int(age), age > self.ttl_seconds)
            return data
        except (OSError, pickle.UnpicklingError, json.JSONDecodeError, KeyError) as exc:
            logger.warning("[cache] Could not load %s: %s", self.name, exc)
            return None

    def is_fresh(self) -> bool:
        """Return True if cached data is within TTL."""
        if not self._meta_path.exists():
            return False
        try:
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            age = (datetime.now() - datetime.fromisoformat(meta["saved_at"])).total_seconds()
            return age <= self.ttl_seconds
        except Exception:
            return False

    def clear(self) -> None:
        for p in (self._path, self._meta_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Degraded mode
# ---------------------------------------------------------------------------

# Module-level flag — when True, non-essential operations should be skipped
_DEGRADED = False
_DEGRADED_REASON = ""


def is_degraded() -> bool:
    return _DEGRADED


def enter_degraded(reason: str = "unknown") -> None:
    global _DEGRADED, _DEGRADED_REASON
    _DEGRADED        = True
    _DEGRADED_REASON = reason
    logger.critical("[degraded] ENTERING DEGRADED MODE: %s", reason)
    _append_resilience_log({"event": "degraded_enter", "reason": reason})


def exit_degraded() -> None:
    global _DEGRADED, _DEGRADED_REASON
    logger.info("[degraded] Exiting degraded mode (was: %s)", _DEGRADED_REASON)
    _append_resilience_log({"event": "degraded_exit", "previous_reason": _DEGRADED_REASON})
    _DEGRADED        = False
    _DEGRADED_REASON = ""


@contextmanager
def DegradedMode(reason: str = "unknown"):
    """
    Context manager that activates degraded mode for the duration of the block.

    Usage:
        with DegradedMode("disk_full"):
            # only essential operations here
            update_dashboard()
    """
    enter_degraded(reason)
    try:
        yield
    finally:
        exit_degraded()


def require_normal_mode(label: str = ""):
    """
    Decorator: raise DegradedModeError if the system is currently degraded.
    Use on non-essential operations (social posting, email sends, etc.)

    Example:
        @require_normal_mode("post_to_facebook")
        def post_fb(content): ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if _DEGRADED:
                op = label or fn.__qualname__
                raise DegradedModeError(
                    f"[{op}] Blocked — system is in degraded mode ({_DEGRADED_REASON}). "
                    f"Only essential operations are permitted."
                )
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Disk space check
# ---------------------------------------------------------------------------


def disk_check(path: str | Path = "D:/", alert_gb: float = DISK_ALERT_GB) -> dict:
    """
    Check available disk space. Returns a dict with metrics.
    Raises DiskFullError if free space < alert_gb.
    Enters degraded mode if free space < DISK_CRITICAL_GB.

    Example:
        info = disk_check()   # raises DiskFullError if low
        print(info["free_gb"])
    """
    usage    = shutil.disk_usage(str(path))
    free_gb  = usage.free  / (1024 ** 3)
    total_gb = usage.total / (1024 ** 3)
    used_pct = usage.used  / usage.total * 100

    info = {
        "path":      str(path),
        "free_gb":   round(free_gb, 2),
        "total_gb":  round(total_gb, 2),
        "used_pct":  round(used_pct, 1),
        "alert_threshold_gb": alert_gb,
        "critical_threshold_gb": DISK_CRITICAL_GB,
    }

    _append_resilience_log({"event": "disk_check", **info})

    if free_gb < DISK_CRITICAL_GB:
        enter_degraded(f"disk_critical: {free_gb:.2f} GB free < {DISK_CRITICAL_GB} GB threshold")
        raise DiskFullError(
            f"CRITICAL: Only {free_gb:.2f} GB free on {path}. "
            f"System entering degraded mode. Essential functions only."
        )

    if free_gb < alert_gb:
        logger.warning(
            "[disk] LOW DISK SPACE: %.2f GB free (threshold: %.1f GB). "
            "Alert will be sent.",
            free_gb, alert_gb
        )
        _queue_disk_alert(free_gb, total_gb, used_pct)

    return info


def _queue_disk_alert(free_gb: float, total_gb: float, used_pct: float) -> None:
    """Write a disk-low alert to Needs_Action/ for the agent to send via email MCP."""
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = (_VAULT_ROOT / "Needs_Action") / f"{ts}_ALERT_DiskLow.md"
    body = (
        f"---\ntype: system_alert\npriority: critical\ncreated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"status: open\ntags: [\"#alert\", \"#disk\", \"#critical\"]\n---\n\n"
        f"# ALERT: Low Disk Space\n\n"
        f"**Free:** {free_gb:.2f} GB / {total_gb:.1f} GB total ({used_pct:.1f}% used)\n"
        f"**Threshold:** {DISK_ALERT_GB} GB\n\n"
        f"## Required Action\n\n"
        f"- [ ] Review large files (check Logs/, .cache/, Plans/)\n"
        f"- [ ] Delete or archive old logs and Done/ items\n"
        f"- [ ] Send alert email to system owner via email MCP\n"
        f"- [ ] Confirm disk issue resolved\n"
    )
    try:
        path.write_text(body, encoding="utf-8")
        logger.critical("[disk] Alert written to Needs_Action: %s", path.name)
    except OSError as exc:
        logger.critical("[disk] Could not write disk alert: %s", exc)


# ---------------------------------------------------------------------------
# Queue for retry (Pending/ folder)
# ---------------------------------------------------------------------------


def queue_for_retry(
    task_name: str,
    payload: dict,
    source: str = "",
    priority: str = "medium",
) -> str | None:
    """
    Persist a failed task to Pending/{timestamp}_{task_name}.json so it can
    be replayed later (by watchdog or the next processing cycle).

    Enforces MAX_QUEUE_SIZE: if the queue is full, logs a warning and returns None.

    Returns the path of the written file, or None if queue is full.

    Example:
        queue_for_retry(
            task_name="send_email",
            payload={"to": "boss@co.com", "subject": "Report", "body": "..."},
            source="gmail_watcher",
        )
    """
    # Count existing queue files
    existing = list(_PENDING_DIR.glob("*.json"))
    if len(existing) >= MAX_QUEUE_SIZE:
        logger.warning(
            "[queue] MAX_QUEUE_SIZE (%d) reached — dropping task: %s. "
            "Clear Pending/ to resume queueing.",
            MAX_QUEUE_SIZE, task_name,
        )
        _append_resilience_log({
            "event":     "queue_dropped",
            "task_name": task_name,
            "reason":    f"queue_full ({len(existing)}/{MAX_QUEUE_SIZE})",
        })
        return None

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{task_name}.json"
    path     = _PENDING_DIR / filename

    record = {
        "task_name":   task_name,
        "payload":     payload,
        "source":      source,
        "priority":    priority,
        "queued_at":   datetime.now().isoformat(),
        "attempts":    0,
        "max_attempts": RETRY_MAX,
    }

    try:
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("[queue] Task queued: %s → %s", task_name, filename)
        _append_resilience_log({
            "event":     "task_queued",
            "task_name": task_name,
            "file":      filename,
            "source":    source,
            "queue_depth": len(existing) + 1,
        })
        return str(path)
    except OSError as exc:
        logger.error("[queue] Could not write queue file: %s", exc)
        return None


def drain_queue() -> list[dict]:
    """
    Load all tasks from Pending/ that have not exceeded max_attempts.
    Returns a list of task dicts. Does NOT delete files — caller decides.
    """
    tasks = []
    for path in sorted(_PENDING_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record["_path"] = str(path)
            if record.get("attempts", 0) < record.get("max_attempts", RETRY_MAX):
                tasks.append(record)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[queue] Could not read %s: %s", path.name, exc)
    return tasks


def mark_queue_attempt(path: str, success: bool) -> None:
    """Increment attempt counter; delete file if succeeded or max reached."""
    p = Path(path)
    if not p.exists():
        return
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    record["attempts"] = record.get("attempts", 0) + 1
    record["last_attempt"] = datetime.now().isoformat()

    if success or record["attempts"] >= record.get("max_attempts", RETRY_MAX):
        reason = "success" if success else "max_attempts_reached"
        logger.info("[queue] Removing task %s (%s)", p.name, reason)
        _append_resilience_log({
            "event":    "task_removed",
            "file":     p.name,
            "reason":   reason,
            "attempts": record["attempts"],
        })
        p.unlink(missing_ok=True)
    else:
        p.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# PID file helpers (for watchdog)
# ---------------------------------------------------------------------------


def write_pid(name: str) -> Path:
    """
    Write the current process PID to .pids/{name}.pid.
    Call this at startup of every monitored script.

    Example (add to top of __main__ block):
        from resilience import write_pid
        write_pid("gmail_watcher")
    """
    pid_path = _PIDS_DIR / f"{name}.pid"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    logger.debug("[pid] Wrote PID %d → %s", os.getpid(), pid_path.name)
    return pid_path


def clear_pid(name: str) -> None:
    """Remove the PID file on clean shutdown."""
    pid_path = _PIDS_DIR / f"{name}.pid"
    pid_path.unlink(missing_ok=True)


def read_pid(name: str) -> int | None:
    """Read a PID file. Returns None if missing or invalid."""
    pid_path = _PIDS_DIR / f"{name}.pid"
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def pid_is_alive(pid: int) -> bool:
    """Check if a process with the given PID is currently running.

    Note (Windows): os.kill(pid, 0) may still succeed for a recently-terminated
    process while an open handle to it exists (e.g. from subprocess.Popen).
    The watchdog's _is_alive() prefers proc.poll() for its own children, falling
    back here only for externally-launched processes.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)   # signal 0 = probe; raises if process does not exist
        return True
    except (ProcessLookupError, PermissionError) as exc:
        # ProcessLookupError: PID does not exist
        # PermissionError on POSIX: PID exists but we can't signal it (still alive)
        return isinstance(exc, PermissionError)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Resilience log
# ---------------------------------------------------------------------------


def _append_resilience_log(entry: dict) -> None:
    """Append a structured event to Logs/resilience_{date}.json."""
    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = _LOGS_DIR / f"resilience_{today}.json"
    existing: list = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append({"timestamp": datetime.now().isoformat(), **entry})
    try:
        log_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # can't log if disk is truly full
