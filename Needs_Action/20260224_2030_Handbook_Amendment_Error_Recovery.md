---
id: HANDBOOK_AMEND_20260224_RECOVERY
source: agent_skill
priority: high
created: 2026-02-24 20:30
status: open
tags: ["#handbook", "#amendment", "#resilience", "#watchdog"]
type: handbook_amendment_proposal
---

# Handbook Amendment Proposal: Recovery Rules (§9)

## Proposed Addition to `Company_Handbook.md`

Add a new section **§9 — Error Recovery & Resilience Rules** with the
following rules:

---

### §9.1 — Retry Policy

All external API calls MUST be wrapped with the `@retry` decorator from
`watchers/resilience.py`.

- **Max attempts:** 3
- **Backoff:** `2 ** attempt_number` seconds (2 s, 4 s, 8 s)
- **On exhaustion:** fall back to `LocalCache`; queue task to `Pending/`
- Inline retry logic is **not permitted** — use the shared module only

### §9.2 — Pending Queue Cap

The `Pending/` retry queue MUST NOT exceed **50 items** at any time.

- Tasks beyond this cap are **dropped** (logged to `Logs/resilience_*.json`)
- The watchdog is responsible for draining the queue on process recovery
- Human review is required if queue depth approaches 40 items (80% full)

### §9.3 — PID Files

Every long-running process (watchers, orchestrator, MCP servers) MUST:

1. Call `write_pid(name)` at startup
2. Call `clear_pid(name)` in a `finally` block on shutdown
3. Never delete another process's PID file

### §9.4 — Degraded Mode

When disk free space falls below `DISK_CRITICAL_GB` (0.2 GB):

- The system MUST enter degraded mode via `enter_degraded()`
- Non-essential operations (social posting, email sends, briefings) are blocked
- Only essential operations (Dashboard update, PID writes, log writes) are allowed
- A `Needs_Action/ALERT_DiskLow.md` file MUST be written immediately
- The human owner MUST be notified via the email MCP as soon as possible

### §9.5 — Watchdog

`watchdog.py` MUST run continuously alongside all other processes.

- Monitor interval: 60 seconds
- Dead processes are restarted after a 10-second cooldown
- Disk check performed every cycle
- Dashboard `## Watchdog Status` section updated every cycle

### §9.6 — Cache Staleness

`LocalCache` stale data (beyond TTL) MAY be served as a fallback but MUST:

- Log a warning indicating the data is stale
- NOT be used for financial transactions or outbound communications
- Be replaced by fresh data as soon as the API recovers

---

## Rationale

The `Enable_Error_Recovery_Degradation` skill (implemented 2026-02-24) added
shared resilience infrastructure (`watchers/resilience.py`, `watchdog.py`,
patches to `base_watcher.py`).  These handbook rules codify the operating
constraints already enforced in code, ensuring all future scripts written by
the agent follow the same patterns.

## Required Action

- [ ] Human review of proposed §9 rules above
- [ ] Approve or amend rule wording
- [ ] Agent to insert approved text into `Company_Handbook.md`
  (requires explicit human approval per Handbook §2)
- [ ] Move this file to `Done/` once amendment is applied
