"""
audit_logger.py — Structured Audit Logging for Personal AI Employee (Gold Tier)

Provides a single import for unified, structured audit logging across every script.

Format:  NDJSON (one JSON object per line) — grep-friendly, fast-append.
File:    Logs/YYYY-MM-DD.json  (one file per day)
Retain:  90 days  (older files pruned automatically)
Alert:   CRITICAL severity → writes Needs_Action/ALERT_Critical_*.md

Schema per event:
  {
    "timestamp":        "2026-02-24T20:30:00.123456",  # ISO-8601, microseconds
    "actor":            "fb_ig_mcp",                   # script / component name
    "action":           "post_fb",                     # what happened
    "params":           {"draft_file": "..."},         # key inputs (no secrets)
    "result":           "success",                     # success | failure | skipped | blocked
    "approval_status":  "approved",                    # approved | pending | not_required | denied
    "severity":         "INFO",                        # DEBUG | INFO | WARNING | ERROR | CRITICAL
    "source_file":      "fb_ig_mcp.py",                # file that logged the event
    "error":            null                           # error message string or null
  }

Usage (anywhere in the codebase):
    from audit_logger import audit_log, AuditLogger

    # Functional one-liner (recommended for simple events):
    audit_log("fb_ig_mcp", "post_fb", params={"draft": "x.md"},
              result="success", approval_status="approved")

    # Class-based (recommended for components that log many events):
    logger = AuditLogger("mcp_orchestrator")
    logger.log("tool_call", params={"tool": "draft_fb_post"}, result="success")
    logger.log("restart", params={"server": "x"}, result="failure",
               severity="ERROR", error="Connection refused")
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR  = Path(__file__).resolve().parent
_VAULT_ROOT  = _SCRIPT_DIR.parent
_LOGS_DIR    = _VAULT_ROOT / "Logs"
_NEEDS_DIR   = _VAULT_ROOT / "Needs_Action"

_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_NEEDS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", 90))

_VALID_RESULTS    = {"success", "failure", "skipped", "blocked", "error",
                     "dry_run", "queued", "started", "stopped", "unknown"}
_VALID_APPROVALS  = {"approved", "pending", "not_required", "denied", "unknown"}
_VALID_SEVERITIES = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

_stdlib_log = logging.getLogger("audit_logger")

# ---------------------------------------------------------------------------
# Core log function
# ---------------------------------------------------------------------------


def audit_log(
    actor:           str,
    action:          str,
    *,
    params:          dict[str, Any] | None = None,
    result:          str = "success",
    approval_status: str = "not_required",
    severity:        str = "INFO",
    source_file:     str = "",
    error:           str | Exception | None = None,
) -> None:
    """
    Write one structured audit event to today's NDJSON log file.

    Args:
        actor:           Originating component (e.g. "fb_ig_mcp", "watchdog").
        action:          What happened (e.g. "post_fb", "restart_process").
        params:          Key inputs / context.  Never include secrets.
        result:          Outcome: success | failure | skipped | blocked |
                         error | dry_run | queued | started | stopped
        approval_status: HITL state: approved | pending | not_required | denied
        severity:        Log level: DEBUG | INFO | WARNING | ERROR | CRITICAL
        source_file:     Calling filename (auto-detected if omitted).
        error:           Exception or error message string, or None.
    """
    # Auto-detect caller file if not provided
    if not source_file:
        try:
            frame = sys._getframe(1)
            source_file = Path(frame.f_code.co_filename).name
        except (AttributeError, ValueError):
            source_file = "unknown"

    # Normalise / clamp values
    result          = result          if result          in _VALID_RESULTS    else "unknown"
    approval_status = approval_status if approval_status in _VALID_APPROVALS  else "unknown"
    severity        = severity        if severity        in _VALID_SEVERITIES else "INFO"

    error_str: str | None = None
    if isinstance(error, Exception):
        error_str = f"{type(error).__name__}: {error}"
    elif error:
        error_str = str(error)

    event: dict[str, Any] = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "actor":           actor,
        "action":          action,
        "params":          _sanitise_params(params or {}),
        "result":          result,
        "approval_status": approval_status,
        "severity":        severity,
        "source_file":     source_file,
        "error":           error_str,
    }

    _write_event(event)

    if severity == "CRITICAL":
        _alert_critical(event)


# ---------------------------------------------------------------------------
# Class-based helper (for components that log many events)
# ---------------------------------------------------------------------------


class AuditLogger:
    """
    Thin wrapper around audit_log() that pre-fills `actor` and `source_file`.

    Usage:
        alog = AuditLogger("mcp_orchestrator", source_file="mcp_orchestrator.py")
        alog.log("server_start", params={"server": "fb_ig"}, result="success")
        alog.error("tool_call_failed", error=exc, params={"tool": "post_fb"})
    """

    def __init__(self, actor: str, source_file: str = ""):
        self.actor       = actor
        self.source_file = source_file or f"{actor}.py"

    def log(
        self,
        action:          str,
        *,
        params:          dict[str, Any] | None = None,
        result:          str = "success",
        approval_status: str = "not_required",
        severity:        str = "INFO",
        error:           str | Exception | None = None,
    ) -> None:
        audit_log(
            actor=self.actor,
            action=action,
            params=params,
            result=result,
            approval_status=approval_status,
            severity=severity,
            source_file=self.source_file,
            error=error,
        )

    # Convenience level shortcuts
    def info(self, action: str, **kwargs) -> None:
        self.log(action, severity="INFO", **kwargs)

    def warning(self, action: str, **kwargs) -> None:
        self.log(action, severity="WARNING", **kwargs)

    def error(self, action: str, **kwargs) -> None:
        self.log(action, severity="ERROR", result=kwargs.pop("result", "failure"), **kwargs)

    def critical(self, action: str, **kwargs) -> None:
        self.log(action, severity="CRITICAL", result=kwargs.pop("result", "failure"), **kwargs)


# ---------------------------------------------------------------------------
# Write + rotation
# ---------------------------------------------------------------------------


def _write_event(event: dict) -> None:
    """Append one NDJSON line to today's log file, then prune old logs."""
    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = _LOGS_DIR / f"{today}.json"

    try:
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        _stdlib_log.error("[audit_logger] Cannot write to %s: %s", log_path, exc)
        return

    # Lazy pruning: check once per process lifetime via module-level flag
    _maybe_prune()


# Module-level flag so we only prune once per process run
_pruned_this_session = False


def _maybe_prune() -> None:
    global _pruned_this_session
    if not _pruned_this_session:
        _pruned_this_session = True
        prune_old_logs()


def prune_old_logs(retention_days: int = LOG_RETENTION_DAYS) -> list[str]:
    """
    Delete NDJSON log files older than `retention_days` days.
    Returns list of deleted filenames.

    Matches files named YYYY-MM-DD.json in Logs/.
    """
    cutoff = datetime.now().timestamp() - retention_days * 86400
    deleted: list[str] = []

    for path in _LOGS_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted.append(path.name)
                _stdlib_log.info("[audit_logger] Pruned old log: %s", path.name)
        except OSError:
            pass

    return deleted


# ---------------------------------------------------------------------------
# Critical alerting
# ---------------------------------------------------------------------------


def _alert_critical(event: dict) -> None:
    """
    When a CRITICAL event is logged, write an alert to Needs_Action/
    so the human owner is notified on the next Dashboard check.
    """
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    actor    = event.get("actor", "unknown")
    action   = event.get("action", "unknown")
    filename = f"{ts}_ALERT_Critical_{actor}_{action}.md".replace(" ", "_")
    path     = _NEEDS_DIR / filename

    body = (
        f"---\n"
        f"type: critical_alert\n"
        f"priority: critical\n"
        f"created: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"status: open\n"
        f"tags: [\"#alert\", \"#critical\", \"#{actor}\"]\n"
        f"---\n\n"
        f"# CRITICAL: {actor} / {action}\n\n"
        f"**Time:** {event['timestamp']}  \n"
        f"**Actor:** {actor}  \n"
        f"**Action:** {action}  \n"
        f"**Result:** {event.get('result', '?')}  \n"
        f"**Error:** {event.get('error') or 'N/A'}\n\n"
        f"## Params\n\n"
        f"```json\n{json.dumps(event.get('params', {}), indent=2)}\n```\n\n"
        f"## Required Action\n\n"
        f"- [ ] Investigate the error shown above\n"
        f"- [ ] Check `Logs/{datetime.now().strftime('%Y-%m-%d')}.json` for surrounding context\n"
        f"- [ ] Confirm issue is resolved\n"
        f"- [ ] Move this file to Done/ when resolved\n"
    )

    try:
        path.write_text(body, encoding="utf-8")
        _stdlib_log.critical("[audit_logger] CRITICAL alert written: %s", filename)
    except OSError as exc:
        _stdlib_log.error("[audit_logger] Could not write critical alert: %s", exc)


# ---------------------------------------------------------------------------
# Parameter sanitiser (strip secrets)
# ---------------------------------------------------------------------------

_SECRET_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "access_token",
    "refresh_token", "private_key", "credential", "auth", "authorization",
    "x_api_key", "fb_page_access_token", "instagram_user_id",
    "gmail_user", "gmail_app_password",
})


def _sanitise_params(params: dict) -> dict:
    """Return a shallow copy of params with secret-looking keys redacted."""
    out: dict = {}
    for k, v in params.items():
        if any(secret in k.lower() for secret in _SECRET_KEYS):
            out[k] = "***REDACTED***"
        elif isinstance(v, dict):
            out[k] = _sanitise_params(v)
        else:
            # Truncate very long strings (e.g. full post bodies)
            if isinstance(v, str) and len(v) > 200:
                out[k] = v[:200] + "...[truncated]"
            else:
                out[k] = v
    return out


# ---------------------------------------------------------------------------
# Reader helpers (used by log_analyzer.py)
# ---------------------------------------------------------------------------


def read_log(date: str) -> list[dict]:
    """
    Read all events from Logs/{date}.json (NDJSON format).
    date format: 'YYYY-MM-DD'.
    Returns empty list if file missing or corrupt.
    """
    path = _LOGS_DIR / f"{date}.json"
    if not path.exists():
        return []
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                _stdlib_log.warning("[audit_logger] Skipping bad line %d in %s: %s",
                                    line_no, path.name, exc)
    return events


def read_logs_range(start: str, end: str) -> list[dict]:
    """
    Read all events between start and end dates (inclusive), YYYY-MM-DD format.
    """
    from datetime import date, timedelta
    s    = date.fromisoformat(start)
    e    = date.fromisoformat(end)
    all_events: list[dict] = []
    current = s
    while current <= e:
        all_events.extend(read_log(current.isoformat()))
        current += timedelta(days=1)
    return all_events


def list_log_dates() -> list[str]:
    """Return sorted list of dates for which a YYYY-MM-DD.json log exists."""
    dates = []
    for path in _LOGS_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"):
        dates.append(path.stem)
    return sorted(dates)
