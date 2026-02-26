---
id: SKILL_Logging
version: "1.0"
created: 2026-02-24
status: active
tags: ["#skill", "#logging", "#audit", "#observability"]
---

# SKILL: Setup_Comprehensive_Audit_Logging

## Trigger

Activate when any of the following occur:
- A new script is added to the vault and needs structured logging
- An error is hard to trace due to missing context in existing logs
- Compliance, audit trail, or approval-chain investigation is required
- You need to search logs by actor, action, or error type

---

## Architecture Overview

```
Every script
    |
    v
audit_log() / AuditLogger class        <-- watchers/audit_logger.py
    |
    v
Logs/YYYY-MM-DD.json (NDJSON)         <-- one line per event; grep-friendly
    |
    +--> prune_old_logs()              <-- auto-deletes files > 90 days old
    +--> _alert_critical()             <-- CRITICAL --> Needs_Action/ALERT_*.md
    |
    v
log_analyzer.py                        <-- read + summarise + write analysis_*.md
```

Existing per-script log files (`social_{date}.json`, `mcp_{date}.json`, etc.)
are retained unchanged.  `audit_logger` **augments** them — every event is
also written to the daily unified `YYYY-MM-DD.json`.

---

## Unified Log Schema

Every event written to `Logs/YYYY-MM-DD.json` has these fields:

| Field             | Type    | Description                                    |
|-------------------|---------|------------------------------------------------|
| `timestamp`       | string  | ISO-8601 UTC, microsecond precision            |
| `actor`           | string  | Originating component (`fb_ig_mcp`, `watchdog`…)|
| `action`          | string  | What happened (`post_fb`, `restart_process`…)  |
| `params`          | object  | Key inputs/context. Secrets auto-redacted.     |
| `result`          | string  | `success` `failure` `skipped` `blocked` `queued` `dry_run` `started` `stopped` |
| `approval_status` | string  | `approved` `pending` `not_required` `denied`   |
| `severity`        | string  | `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL`    |
| `source_file`     | string  | Python filename that emitted the event         |
| `error`           | string? | Error message or `null`                        |

### Example Event

```json
{"timestamp":"2026-02-24T20:30:00.123456+00:00","actor":"fb_ig_mcp","action":"post_fb","params":{"platform":"facebook","draft_file":"FB_DRAFT_001.md"},"result":"success","approval_status":"approved","severity":"INFO","source_file":"fb_ig_mcp.py","error":null}
```

---

## Using the Audit Logger

### Functional Form (one-liner)

```python
from audit_logger import audit_log

audit_log(
    "my_script",                         # actor
    "send_email",                        # action
    params={"to": "boss@co.com"},        # context (secrets auto-redacted)
    result="success",
    approval_status="approved",
    severity="INFO",
    source_file="my_script.py",
)
```

### Class Form (for components with many events)

```python
from audit_logger import AuditLogger

alog = AuditLogger("gmail_watcher", source_file="gmail_watcher.py")

alog.info("check_cycle", params={"found": 5, "created": 3}, result="success")
alog.warning("api_slow", params={"latency_ms": 4500})
alog.error("fetch_failed", error=exc, params={"since": "2026-02-24"})
alog.critical("auth_revoked", error="OAuth token expired and refresh failed")
```

### Adding to an Existing Script

1. Add to imports block:
   ```python
   import sys
   sys.path.insert(0, str(Path(__file__).resolve().parent / "watchers"))
   try:
       from audit_logger import audit_log as _audit_log
       _AUDIT_AVAILABLE = True
   except ImportError:
       _AUDIT_AVAILABLE = False
   ```
2. Inside your existing log function (e.g. `log_social()`), add at the end:
   ```python
   if _AUDIT_AVAILABLE:
       _audit_log(actor="my_script", action=entry.get("action","event"), ...)
   ```

---

## Log Rotation and Retention

- **Format:** NDJSON — one JSON object per line in `Logs/YYYY-MM-DD.json`
- **Rotation:** automatic — a new file starts each calendar day (UTC)
- **Retention:** 90 days (configurable via `LOG_RETENTION_DAYS` env var)
- **Pruning:** lazy — runs once per process lifetime on first `audit_log()` call

```python
from audit_logger import prune_old_logs

deleted = prune_old_logs(retention_days=90)
print(f"Pruned {len(deleted)} old log files")
```

---

## Searching Logs (grep-friendly)

Because each event is one line, standard tools work directly:

```bash
# All CRITICAL events today
grep '"severity":"CRITICAL"' Logs/2026-02-24.json

# All fb_ig_mcp events today
grep '"actor":"fb_ig_mcp"' Logs/2026-02-24.json

# All failed post actions this week
grep '"action":"post_fb"' Logs/2026-02-2*.json | grep '"result":"failure"'

# All denied approvals
grep '"approval_status":"denied"' Logs/*.json

# All events mentioning a specific error
grep 'ConnectionRefused' Logs/*.json
```

---

## Running log_analyzer.py

```bash
# Analyze today's log (writes Logs/analysis_YYYY-MM-DD.md):
python log_analyzer.py

# Specific date:
python log_analyzer.py --date 2026-02-24

# Date range (weekly report):
python log_analyzer.py --range 2026-02-17 2026-02-24

# Show only errors and criticals:
python log_analyzer.py --errors-only

# Filter by actor:
python log_analyzer.py --actor fb_ig_mcp

# Full-text search:
python log_analyzer.py --grep "ConnectionRefused"

# Last 50 events:
python log_analyzer.py --tail 50

# List all dates with logs:
python log_analyzer.py --list-dates

# Stdout only (no .md file):
python log_analyzer.py --no-report
```

### Analyzer Output Sections

1. Severity breakdown (CRITICAL / ERROR / WARNING / INFO / DEBUG counts + bar chart)
2. Result breakdown (success / failure / skipped / blocked counts)
3. Approval status breakdown
4. Top actors by event count
5. Top actions by event count
6. Top error types (extracted from `error` field)
7. Recent CRITICAL events (last 5)
8. Recent ERROR events (last 5, non-critical)
9. Denied approvals

---

## Critical Alert Flow

When any `audit_log(severity="CRITICAL", ...)` is called:

1. Event written to `Logs/YYYY-MM-DD.json` (as normal)
2. `Needs_Action/YYYYMMDD_HHMMSS_ALERT_Critical_{actor}_{action}.md` created
3. Alert file contains: timestamp, actor, action, error, params, checklist
4. Human sees it on next Dashboard review
5. Once resolved → move to `Done/`

---

## Scripts Patched

| Script | Log Function Augmented | Actor Name |
|--------|------------------------|------------|
| `watchers/base_watcher.py` | `log_activity()` | watcher's `self.name` |
| `fb_ig_mcp.py` | `log_social()` | `fb_ig_mcp` |
| `x_mcp.py` | `log_x()` | `x_mcp` |
| `mcp_orchestrator.py` | `log_mcp()` | `mcp_orchestrator` |
| `audit.py` | `log_audit_event()` | `audit` |
| `watchdog.py` | inline + `_alog` class | `watchdog` |

---

## Acceptance Criteria

- [x] NDJSON format verified (each line is valid JSON)
- [x] All 9 schema fields present in every event
- [x] Secret keys (`x_api_key`, `token`, etc.) auto-redacted to `***REDACTED***`
- [x] Non-secret params preserved unchanged
- [x] CRITICAL severity triggers `Needs_Action/ALERT_Critical_*.md`
- [x] `prune_old_logs()` runs without error; deletes files > 90 days
- [x] `list_log_dates()` returns all available dates
- [x] `log_analyzer.py` reads NDJSON, produces text report + Markdown file
- [x] `--errors-only`, `--actor`, `--grep`, `--tail`, `--range` filters work
- [x] All 6 scripts patched; existing log functions continue to work
- [x] Test: 17/17 passed

---

## Environment Variables

| Variable              | Default | Description                             |
|-----------------------|---------|-----------------------------------------|
| `LOG_RETENTION_DAYS`  | 90      | Days to retain NDJSON log files         |

---

## File Layout

```
Logs/
  2026-02-24.json          # today's unified audit log (NDJSON)
  2026-02-23.json          # yesterday
  ...                      # up to 90 days retained
  analysis_2026-02-24.md   # generated by log_analyzer.py
  social_2026-02-24.json   # fb_ig_mcp per-script log (unchanged)
  social_x_2026-02-24.json # x_mcp per-script log (unchanged)
  mcp_2026-02-24.json      # mcp_orchestrator per-script log (unchanged)
  audit_2026-02-24.json    # audit.py per-script log (unchanged)
  resilience_2026-02-24.json # resilience.py per-script log (unchanged)
```

---

## Related Skills

- `SKILL_Error_Recovery.md` — resilience.py also writes to `resilience_{date}.json`
- `SKILL_MCP_Manager.md` — MCP events flow through `log_mcp()` → unified log
- `SKILL_FB_IG.md` — social posts flow through `log_social()` → unified log
- `SKILL_X_Integration.md` — X events flow through `log_x()` → unified log
