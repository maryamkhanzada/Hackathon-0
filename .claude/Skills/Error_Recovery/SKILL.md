---
id: SKILL_Error_Recovery
version: "1.0"
created: 2026-02-24
status: active
tags: ["#skill", "#resilience", "#error-recovery", "#watchdog"]
---

# SKILL: Enable_Error_Recovery_Degradation

## Trigger

Activate when any of the following occur:
- A watcher, MCP server, or orchestrator crashes or stops responding
- An API call fails repeatedly (rate limit, timeout, network error)
- Disk space falls below 1 GB free
- A task cannot be completed and needs to be queued for later replay
- The system must continue operating with reduced capability (degraded mode)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Error Recovery Stack                         │
│                                                                 │
│  watchdog.py          60 s loop; monitors PIDs, restarts dead   │
│       |                                                         │
│  mcp_orchestrator.py  60 s loop; health-pings MCP servers       │
│       |                                                         │
│  base_watcher.py      with_retry(check); LocalCache fallback    │
│       |                                                         │
│  resilience.py        Shared primitives (imported by all above) │
└─────────────────────────────────────────────────────────────────┘
```

---

## Shared Module: `watchers/resilience.py`

All scripts import from this single module.  Never duplicate retry logic.

### Retry Decorator

```python
from resilience import retry, RetryExhausted

@retry(max_retries=3, backoff_base=2, label="gmail_fetch")
def fetch_emails(since: str) -> list:
    return api.list_messages(since)
    # On failure: waits 2s, 4s, 8s before raising RetryExhausted
```

**Backoff schedule** (default `backoff_base=2`):

| Attempt | Wait before next |
|---------|-----------------|
| 1       | 2 s             |
| 2       | 4 s             |
| 3       | 8 s (final)     |

### Functional Form

```python
from resilience import with_retry

items = with_retry(self.check, label="my_watcher.check")
```

### Local Cache (API-down fallback)

```python
from resilience import LocalCache

cache = LocalCache("gmail_messages", ttl_seconds=3600)

try:
    data = fetch_from_api()
    cache.save(data)          # always update on success
except RetryExhausted:
    data = cache.load() or [] # serve stale data rather than failing
```

Cache files live in `.cache/` (pickle + JSON metadata).  `is_fresh()` returns
`True` when age < TTL; stale data is still served as a last-resort fallback.

### Degraded Mode

```python
from resilience import DegradedMode, require_normal_mode, is_degraded

@require_normal_mode("post_to_facebook")
def post_fb(content):
    ...   # blocked automatically when disk_check() triggers degraded mode

with DegradedMode("disk_full"):
    update_dashboard()        # essential ops only inside this block
```

### Disk Check

```python
from resilience import disk_check, DiskFullError

try:
    info = disk_check("D:/")   # alerts at < 1 GB; degrades at < 200 MB
    print(f"{info['free_gb']:.1f} GB free")
except DiskFullError:
    # System already entered degraded mode; Needs_Action alert already written
    pass
```

**Thresholds** (overridable via env vars):

| Variable             | Default | Effect                                   |
|----------------------|---------|------------------------------------------|
| `DISK_ALERT_GB`      | 1.0 GB  | Write Needs_Action alert, queue email    |
| `DISK_CRITICAL_GB`   | 0.2 GB  | Enter degraded mode + raise DiskFullError|

### Queue for Retry

```python
from resilience import queue_for_retry, drain_queue, mark_queue_attempt

# When an operation fails and can't be retried now:
queue_for_retry(
    task_name="send_email",
    payload={"to": "boss@co.com", "subject": "Report", "body": "..."},
    source="gmail_watcher",
    priority="high",
)

# Watchdog drains the queue on recovery:
for task in drain_queue():
    # ... attempt task ...
    mark_queue_attempt(task["_path"], success=True)
```

**Max queue size:** 50 items (Handbook Rule §9).  Tasks beyond this are
dropped with a warning logged to `Logs/resilience_{date}.json`.

### PID Files

```python
from resilience import write_pid, clear_pid, read_pid, pid_is_alive

# At startup of every long-running script:
write_pid("gmail_watcher")   # writes .pids/gmail_watcher.pid

# Watchdog checks liveness:
pid = read_pid("gmail_watcher")
if not pid_is_alive(pid):
    restart_process("gmail_watcher")

# At clean shutdown (or in finally block):
clear_pid("gmail_watcher")
```

---

## Steps: Recovering from a Crashed Process

1. **Watchdog detects dead PID** (60 s cycle) → logs `process_restarted` event
2. **Disk check** before restart (for orchestrator) → skip if `DiskFullError`
3. **Restart** with `RESTART_COOLDOWN_SECONDS=10` pause between attempts
4. **Queue drain** → `drain_pending_queue()` replays `Pending/*.json` tasks
5. **Dashboard updated** → `## Watchdog Status` section rewritten

---

## Steps: Recovering from API Failure

1. `@retry` decorator retries up to 3× with 2/4/8 s backoff
2. On `RetryExhausted` → load `LocalCache` stale data (if available)
3. If no cache → log warning, skip cycle, sleep `poll_interval` seconds
4. Failed task written to `Pending/` via `queue_for_retry()`
5. Next successful cycle updates cache; queue replayed by watchdog

---

## Steps: Disk Full

1. `disk_check()` called at start of every watcher cycle and watchdog loop
2. `< DISK_ALERT_GB (1 GB)` → `_queue_disk_alert()` writes `Needs_Action/ALERT_DiskLow.md`
3. `< DISK_CRITICAL_GB (0.2 GB)` → `enter_degraded()` + `raise DiskFullError`
4. Non-essential functions (`@require_normal_mode`) blocked automatically
5. Watchdog queues `send_disk_alert_email` task for email MCP
6. Human resolves disk issue; watchdog calls `exit_degraded()` on next clean pass

---

## Running the Watchdog

```bash
# Start continuous monitor (60 s cycle):
python watchdog.py

# Single health check:
python watchdog.py --once

# Print process table:
python watchdog.py --status

# Replay Pending/ queue:
python watchdog.py --drain-queue
```

---

## Acceptance Criteria

- [x] `@retry` with `backoff_base=2` works across all scripts via `resilience.py`
- [x] `LocalCache` save/load round-trip verified (crash-sim test)
- [x] `DegradedMode` context manager blocks `@require_normal_mode` functions
- [x] `disk_check()` returns correct disk metrics; thresholds configurable via env
- [x] `queue_for_retry()` writes to `Pending/`; `drain_queue()` reads them back
- [x] `mark_queue_attempt(success=True)` deletes the queue file
- [x] `write_pid` / `clear_pid` / `pid_is_alive` helpers integrated into `base_watcher.py`
- [x] `watchdog.py` monitors all registered PIDs every 60 s
- [x] Watchdog auto-restarts dead processes with cooldown
- [x] Watchdog updates `Dashboard.md ## Watchdog Status` table
- [x] Crash-simulation test: 20/21 passed (1 skipped: Windows PID handle quirk)

---

## Log Schema (`Logs/resilience_{date}.json`)

```json
[
  {"timestamp": "2026-02-24T10:01:00", "event": "retry",
   "label": "gmail_fetch", "attempt": 1, "max_retries": 3,
   "error_type": "ConnectionError", "error": "...", "wait_secs": 2},

  {"timestamp": "2026-02-24T10:01:08", "event": "task_queued",
   "task_name": "send_email", "file": "20260224_100108_send_email.json",
   "source": "gmail_watcher", "queue_depth": 1},

  {"timestamp": "2026-02-24T10:02:00", "event": "watchdog_cycle",
   "alive": 3, "total": 4, "dead": 1, "degraded": false},

  {"timestamp": "2026-02-24T10:02:10", "event": "process_restarted",
   "process": "gmail_watcher", "new_pid": 9812, "restart_count": 1},

  {"timestamp": "2026-02-24T10:05:00", "event": "disk_check",
   "free_gb": 0.15, "alert_threshold_gb": 1.0, "critical_threshold_gb": 0.2},

  {"timestamp": "2026-02-24T10:05:00", "event": "degraded_enter",
   "reason": "disk_critical: 0.15 GB free < 0.2 GB threshold"}
]
```

---

## Environment Variables

| Variable              | Default | Description                              |
|-----------------------|---------|------------------------------------------|
| `RETRY_MAX`           | 3       | Max retry attempts for `@retry`          |
| `RETRY_BACKOFF_BASE`  | 2       | Backoff base; delay = base ** attempt    |
| `DISK_ALERT_GB`       | 1.0     | Free GB below which alert is written     |
| `DISK_CRITICAL_GB`    | 0.2     | Free GB below which degraded mode fires  |
| `MAX_QUEUE_SIZE`      | 50      | Max items in `Pending/` queue            |

---

## Related Skills

- `SKILL_MCP_Manager.md` — MCP server pool health + offline task queue
- `SKILL_Weekly_Audit.md` — Uses `@retry` for accounting data loading
- `SKILL_FB_IG.md` — Uses `with_retry` + `queue_for_retry` on post failures
- `SKILL_X_Integration.md` — Rate-limit sleep + OAuth2 token refresh pattern
