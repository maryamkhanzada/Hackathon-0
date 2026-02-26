"""
watchdog.py — Process Monitor for Personal AI Employee (Gold Tier)

Monitors all registered processes (orchestrator, watchers, MCP servers) every
60 seconds.  If a process is dead, it is automatically restarted.

Additional duties:
  - Disk-full detection → write Needs_Action alert + trigger degraded mode
  - Queue drain: replay Pending/ tasks when target processes recover
  - Dashboard update: writes ## Watchdog Status section

Usage:
    python watchdog.py                  # Run forever (60 s monitor loop)
    python watchdog.py --once           # Single health check and exit
    python watchdog.py --status         # Print current process table and exit
    python watchdog.py --drain-queue    # Replay Pending/ tasks, then exit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve vault root and ensure resilience module is importable
# ---------------------------------------------------------------------------

_VAULT_ROOT = Path(__file__).resolve().parent
_WATCHERS_DIR = _VAULT_ROOT / "watchers"
sys.path.insert(0, str(_WATCHERS_DIR))

from resilience import (  # noqa: E402
    DiskFullError,
    LocalCache,
    RetryExhausted,
    _append_resilience_log,
    clear_pid,
    disk_check,
    drain_queue,
    enter_degraded,
    exit_degraded,
    is_degraded,
    mark_queue_attempt,
    pid_is_alive,
    queue_for_retry,
    read_pid,
    write_pid,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOGS_DIR = _VAULT_ROOT / "Logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(
            _LOGS_DIR / f"watchdog_{datetime.now().strftime('%Y-%m-%d')}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("watchdog")

# Unified structured audit logger
try:
    from audit_logger import AuditLogger as _AuditLogger  # noqa: E402
    _alog = _AuditLogger("watchdog", source_file="watchdog.py")
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONITOR_INTERVAL_SECONDS = 60
RESTART_COOLDOWN_SECONDS = 10  # wait before restarting a dead process

# Process registry: name → launch command (relative to vault root)
# Each entry MUST correspond to a .pids/{name}.pid file written by the process.
PROCESS_REGISTRY: dict[str, list[str]] = {
    # Core orchestrator
    "mcp_orchestrator": [sys.executable, str(_VAULT_ROOT / "mcp_orchestrator.py")],
    # Watchers
    "gmail_watcher":    [sys.executable, str(_WATCHERS_DIR / "gmail_watcher.py")],
    "whatsapp_watcher": [sys.executable, str(_WATCHERS_DIR / "whatsapp_watcher.py")],
    "linkedin_watcher": [sys.executable, str(_WATCHERS_DIR / "linkedin_watcher.py")],
}

# Processes whose restart should trigger a disk check before they come up
DISK_CHECK_ON_RESTART = {"mcp_orchestrator"}

# ---------------------------------------------------------------------------
# Watchdog state
# ---------------------------------------------------------------------------

_restart_counts: dict[str, int] = {name: 0 for name in PROCESS_REGISTRY}
_last_seen_alive: dict[str, str] = {}
_child_procs: dict[str, subprocess.Popen] = {}  # name → Popen for processes we spawned


# ---------------------------------------------------------------------------
# Process management helpers
# ---------------------------------------------------------------------------


def _is_alive(name: str) -> bool:
    """Return True if the named process has a valid, living PID."""
    # Check our own spawned child first (most reliable)
    proc = _child_procs.get(name)
    if proc is not None:
        if proc.poll() is None:
            return True
        # Process exited — clean up reference
        del _child_procs[name]

    # Fall back to PID file (for externally-launched processes)
    pid = read_pid(name)
    if pid is None:
        return False
    return pid_is_alive(pid)


def _restart_process(name: str) -> bool:
    """
    Restart a dead process.  Returns True on success, False on error.
    Enforces RESTART_COOLDOWN_SECONDS between attempts.
    """
    cmd = PROCESS_REGISTRY.get(name)
    if not cmd:
        log.error("[%s] No command registered — cannot restart.", name)
        return False

    if name in DISK_CHECK_ON_RESTART:
        try:
            disk_check(_VAULT_ROOT)
        except DiskFullError as exc:
            log.critical("[%s] Restart blocked — disk full: %s", name, exc)
            return False

    log.info("[%s] Restarting (attempt #%d)...", name, _restart_counts[name] + 1)
    time.sleep(RESTART_COOLDOWN_SECONDS)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_VAULT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _child_procs[name] = proc
        _restart_counts[name] += 1
        _last_seen_alive[name] = datetime.now().isoformat()
        log.info("[%s] Restarted — PID %d", name, proc.pid)
        _append_resilience_log({
            "event":        "process_restarted",
            "process":      name,
            "new_pid":      proc.pid,
            "restart_count": _restart_counts[name],
        })
        if _AUDIT_AVAILABLE:
            _alog.log("process_restarted", result="success",
                      params={"process": name, "pid": proc.pid,
                              "restart_count": _restart_counts[name]})
        return True
    except (OSError, FileNotFoundError) as exc:
        log.error("[%s] Failed to restart: %s", name, exc)
        _append_resilience_log({
            "event":   "restart_failed",
            "process": name,
            "error":   str(exc),
        })
        if _AUDIT_AVAILABLE:
            _alog.error("process_restart_failed",
                        params={"process": name}, error=exc)
        return False


# ---------------------------------------------------------------------------
# Disk-full handler
# ---------------------------------------------------------------------------


def _handle_disk_full(exc: DiskFullError) -> None:
    """Enter degraded mode and queue an alert task."""
    log.critical("DISK FULL: %s", exc)
    enter_degraded(f"disk_full: {exc}")
    queue_for_retry(
        task_name="send_disk_alert_email",
        payload={
            "subject": "CRITICAL: Disk Full on AI Employee Server",
            "body": str(exc),
            "priority": "critical",
        },
        source="watchdog",
        priority="critical",
    )
    if _AUDIT_AVAILABLE:
        _alog.critical("disk_full_detected", error=exc,
                       params={"error": str(exc)})


# ---------------------------------------------------------------------------
# Queue drain
# ---------------------------------------------------------------------------


def drain_pending_queue() -> int:
    """
    Attempt to replay tasks from Pending/.
    Returns the count of tasks successfully processed (or removed as exhausted).
    """
    tasks = drain_queue()
    if not tasks:
        log.info("[queue] Pending queue is empty.")
        return 0

    log.info("[queue] Draining %d pending task(s)...", len(tasks))
    processed = 0
    for task in tasks:
        name = task.get("task_name", "unknown")
        path = task.get("_path", "")
        log.info("[queue] Replaying: %s (attempt %d/%d)",
                 name, task.get("attempts", 0) + 1, task.get("max_attempts", 3))
        # In a real system this would route to the appropriate MCP tool.
        # For now: log the attempt and mark it (success=False keeps it for later).
        mark_queue_attempt(path, success=False)
        processed += 1

    return processed


# ---------------------------------------------------------------------------
# Dashboard update
# ---------------------------------------------------------------------------


def _update_dashboard(status_rows: list[dict]) -> None:
    """
    Rewrite the '## Watchdog Status' section of Dashboard.md.
    Creates the section if it does not exist.
    """
    dashboard_path = _VAULT_ROOT / "Dashboard.md"
    if not dashboard_path.exists():
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"## Watchdog Status",
        f"",
        f"_Updated: {now}_  "
        f"**Interval:** {MONITOR_INTERVAL_SECONDS}s  "
        f"**Degraded:** {'YES' if is_degraded() else 'No'}",
        f"",
        f"| Process | Alive | Restarts | Last Seen |",
        f"|---------|-------|----------|-----------|",
    ]
    for row in status_rows:
        alive_str = "OK" if row["alive"] else "DEAD"
        lines.append(
            f"| {row['name']} | {alive_str} | {row['restarts']} | {row['last_seen']} |"
        )
    lines.append("")

    new_section = "\n".join(lines)
    content = dashboard_path.read_text(encoding="utf-8")

    pattern = r"## Watchdog Status\n.*?(?=\n## |\Z)"
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, new_section, content, flags=re.DOTALL)
    else:
        content = content.rstrip() + "\n\n---\n\n" + new_section + "\n"

    dashboard_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Single health cycle
# ---------------------------------------------------------------------------


def run_once() -> list[dict]:
    """
    Perform one monitor pass over all registered processes.
    Returns a list of status dicts for dashboard / reporting.
    """
    # Disk check first
    try:
        disk_info = disk_check(_VAULT_ROOT)
        log.info(
            "Disk: %.2f GB free / %.1f GB total (%.1f%% used)",
            disk_info["free_gb"], disk_info["total_gb"], disk_info["used_pct"],
        )
        # If we were degraded due to disk and it's now OK, exit degraded mode
        if is_degraded() and disk_info["free_gb"] >= disk_info["alert_threshold_gb"]:
            exit_degraded()
    except DiskFullError as exc:
        _handle_disk_full(exc)

    status_rows: list[dict] = []

    for name in PROCESS_REGISTRY:
        alive = _is_alive(name)

        if alive:
            _last_seen_alive[name] = datetime.now().isoformat()
        else:
            log.warning("[%s] Process is DEAD — restarting...", name)
            _restart_process(name)
            alive = _is_alive(name)  # re-check after restart attempt

        status_rows.append({
            "name":      name,
            "alive":     alive,
            "restarts":  _restart_counts[name],
            "last_seen": _last_seen_alive.get(name, "--"),
        })

    # Log cycle summary
    dead_count = sum(1 for r in status_rows if not r["alive"])
    log.info(
        "Health check: %d/%d alive, %d dead, degraded=%s",
        len(status_rows) - dead_count, len(status_rows), dead_count, is_degraded(),
    )
    _append_resilience_log({
        "event":      "watchdog_cycle",
        "alive":      len(status_rows) - dead_count,
        "total":      len(status_rows),
        "dead":       dead_count,
        "degraded":   is_degraded(),
    })

    return status_rows


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_forever() -> None:
    """Monitor loop — runs indefinitely until KeyboardInterrupt."""
    log.info("Watchdog starting. Monitoring %d process(es) every %ds.",
             len(PROCESS_REGISTRY), MONITOR_INTERVAL_SECONDS)
    write_pid("watchdog")

    try:
        while True:
            rows = run_once()
            _update_dashboard(rows)
            time.sleep(MONITOR_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log.info("Watchdog stopped by user.")
    finally:
        clear_pid("watchdog")
        log.info("Watchdog PID file cleared.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_status() -> None:
    """Print a human-readable process status table."""
    rows = run_once()
    _update_dashboard(rows)
    header = f"{'Process':<22} {'Alive':<8} {'Restarts':<10} {'Last Seen'}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['name']:<22} {'OK' if r['alive'] else 'DEAD':<8} "
            f"{r['restarts']:<10} {r['last_seen']}"
        )
    print(f"\nDisk degraded: {is_degraded()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watchdog — monitors and auto-restarts AI Employee processes."
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single health check cycle and exit.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print process status table and exit.",
    )
    parser.add_argument(
        "--drain-queue", action="store_true",
        help="Replay all tasks in Pending/ and exit.",
    )
    args = parser.parse_args()

    if args.status:
        _print_status()
    elif args.once:
        rows = run_once()
        _update_dashboard(rows)
        log.info("Single health check complete.")
    elif args.drain_queue:
        n = drain_pending_queue()
        log.info("Queue drain complete: %d task(s) processed.", n)
    else:
        run_forever()


if __name__ == "__main__":
    main()
