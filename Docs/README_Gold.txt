# Personal AI Employee — Gold Tier

> **Tier:** Gold  |  **Version:** 1.0  |  **Date:** 2026-02-24  |  **Status:** Production-Ready

A fully autonomous, self-healing AI back-office employee running locally on your machine.
It monitors your communications, manages tasks end-to-end, posts to social media with
human-in-the-loop approval, generates weekly CEO briefings, and recovers automatically
from failures — all while keeping a tamper-evident structured audit trail.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Component Reference](#2-component-reference)
3. [Step-by-Step Setup](#3-step-by-step-setup)
4. [Security Disclosure](#4-security-disclosure)
5. [Demo Flow](#5-demo-flow)
6. [Lessons Learned](#6-lessons-learned)
7. [Troubleshooting FAQ](#7-troubleshooting-faq)
8. [Scalability & Cloud Migration](#8-scalability--cloud-migration)
9. [PDF Export](#9-pdf-export)

---

## 1. Architecture Overview

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                         EXTERNAL WORLD                                       ║
║                                                                              ║
║  Gmail    WhatsApp   LinkedIn   Facebook   Instagram   X/Twitter   Calendar  ║
╚═══╤══════════╤══════════╤═══════════╤══════════╤══════════╤══════════╤══════╝
    │          │          │           │          │          │          │
    ▼          ▼          ▼           ╰──────────┴──────────╯          ▼
╔═══╧══════════╧══════════╧════╗    ╔════════════════════════╗   (future)
║         WATCHERS              ║    ║      MCP SERVERS        ║
║  gmail_watcher.py             ║    ║  fb_ig_mcp.py  (Python) ║
║  whatsapp_watcher.py          ║    ║  x_mcp.py      (Python) ║
║  linkedin_watcher.py          ║    ║  email_mcp.mjs (Node)   ║
║                               ║    ║                         ║
║  base_watcher.py (abstract)   ║    ║  [HITL approval guard]  ║
║  └─ @retry + LocalCache       ║    ║  [daily post cap]       ║
║  └─ disk_check each cycle     ║    ║  [Playwright fallback]  ║
║  └─ PID file on start         ║    ╚══════════╤═════════════╝
╚════════════════╤══════════════╝               │
                 │                              │
    ┌────────────┴──────────────────────────────┘
    │
    ▼
╔═══╧══════════════════════════════════════════════════════════════════════════╗
║                        VAULT  (Obsidian Markdown)                            ║
║                                                                              ║
║  Inbox/          ──► Needs_Action/  ──► In_Progress/  ──► Done/             ║
║  Plans/               Pending_Approval/    Approved/                        ║
║  Briefings/      Audits/       Accounting/    Logs/    .cache/  .pids/      ║
║                                                                              ║
║  Dashboard.md  (live status)    Company_Handbook.md  (operating rules)      ║
╚══╤══════════════════╤═════════════════╤══════════════════╤═══════════════════╝
   │                  │                 │                  │
   ▼                  ▼                 ▼                  ▼
╔══╧══════╗    ╔══════╧══════╗   ╔═════╧═══════╗   ╔═════╧═══════════════╗
║  RALPH  ║    ║   MCP ORCH  ║   ║  WATCHDOG   ║   ║  AUDIT & REPORTING  ║
║  LOOP   ║    ║             ║   ║             ║   ║                     ║
║         ║    ║ orchestrator║   ║ watchdog.py ║   ║ audit.py            ║
║ ralph_  ║    ║ .py         ║   ║             ║   ║ log_analyzer.py     ║
║ loop.py ║    ║             ║   ║ 60s monitor ║   ║                     ║
║         ║    ║ 60s health  ║   ║ PID restart ║   ║ CEO_Briefing_*.md   ║
║ plan    ║    ║ round-robin ║   ║ disk check  ║   ║ WoW revenue delta   ║
║  ▼      ║    ║ LB          ║   ║ queue drain ║   ║ bottleneck detect   ║
║ approve ║    ║ offline Q   ║   ╚═════════════╝   ║ 10 suggestion rules ║
║  ▼      ║    ║ Ralph scan  ║                     ╚═════════════════════╝
║ execute ║    ╚═════════════╝
╚═════════╝
   │                  │                 │                  │
   └──────────────────┴─────────────────┴──────────────────┘
                               │
                               ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║                      RESILIENCE LAYER  (cross-cutting)                       ║
║                                                                              ║
║  watchers/resilience.py                 watchers/audit_logger.py            ║
║  ├─ @retry(max=3, backoff=2**n)         ├─ NDJSON Logs/YYYY-MM-DD.json      ║
║  ├─ LocalCache  (.cache/ pickle)        ├─ 9-field schema per event         ║
║  ├─ DegradedMode  (essential-only)      ├─ Secret auto-redaction            ║
║  ├─ disk_check()  (alert + degrade)     ├─ 90-day rotation                  ║
║  ├─ queue_for_retry() Pending/ Q        └─ CRITICAL → Needs_Action/ alert   ║
║  └─ write_pid / clear_pid / watchdog                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### Data Flow (request lifecycle)

```
External event (email arrives)
  │
  ├─ gmail_watcher.py polls Gmail API  ──[retry x3]──► Inbox/YYYYMMDD_HHMM_Subject.md
  │                                         │ (on fail)► LocalCache fallback
  │
  ├─ orchestrator.py classifies item ──────► Needs_Action/ (priority tagged)
  │
  ├─ If ralph_loop:true ──────────────────► ralph_loop.py spawned
  │     plan   ──► Plans/PLAN_{id}.md
  │     approve──► waits Approved/{id}.md   (human reviews Dashboard)
  │     execute──► MCP tool call ──[HITL]──► external action
  │                                └──────► Done/{id}.md
  │
  ├─ If social post needed ───────────────► fb_ig_mcp.py / x_mcp.py
  │     draft  ──► Plans/DRAFT_{id}.md
  │     approve──► Pending_Approval/APPROVAL_{id}.md
  │     post   ──► live platform API
  │
  └─ Every Monday 07:00 ──────────────────► audit.py --ralph
        reads Accounting/, Done/, Logs/
        writes Briefings/CEO_Briefing_{week}.md
        emits <promise>AUDIT_COMPLETE</promise>
```

---

## 2. Component Reference

### Core Scripts (vault root)

| Script | Role | Key Classes/Functions |
|--------|------|-----------------------|
| `mcp_orchestrator.py` | Manages MCP server pools, health pings, round-robin LB, offline task queue, Ralph loop auto-trigger | `McpOrchestrator`, `McpPool`, `McpServerProcess`, `McpTaskQueue` |
| `ralph_loop.py` | Multi-step task state machine with file-movement + promise-tag completion | `RalphLoop`, `build_config_from_task()` |
| `audit.py` | Weekly CEO briefing from accounting/task data | `run_audit()`, `generate_suggestions()`, `write_briefing()` |
| `watchdog.py` | 60-second process monitor; auto-restarts dead PIDs | `run_forever()`, `_restart_process()` |
| `log_analyzer.py` | Reads NDJSON audit logs; generates text + Markdown summary reports | `analyse()`, `format_report_text()` |
| `orchestrator.py` | Silver-tier master control (base orchestration) | `Orchestrator` |
| `export_pdf.py` | Converts README_Gold.md to PDF | `export_pdf()` |

### Watchers (`watchers/`)

| Script | Data Source | Output |
|--------|------------|--------|
| `gmail_watcher.py` | Gmail API (OAuth2) | `Needs_Action/EMAIL_*.md` |
| `whatsapp_watcher.py` | WhatsApp Business API | `Needs_Action/WA_*.md` |
| `linkedin_watcher.py` | LinkedIn API | `Needs_Action/LI_*.md` |
| `base_watcher.py` | Abstract base | Provides `run()`, `create_note()`, retry, cache, PID |

### Shared Libraries

| Script | Purpose |
|--------|---------|
| `watchers/resilience.py` | `@retry`, `LocalCache`, `DegradedMode`, `disk_check`, `queue_for_retry`, PID helpers |
| `watchers/audit_logger.py` | `audit_log()`, `AuditLogger`, NDJSON writer, 90-day rotation, CRITICAL alerter |
| `watchers/hitl_enforcer.py` | Human-in-the-Loop approval gates |
| `watchers/approval_loop.py` | Continuous approval scanner |
| `watchers/vault_processor.py` | Vault read/summarise/write orchestration |

### MCP Servers

| Server | Language | Tools | Auth |
|--------|----------|-------|------|
| `fb_ig_mcp.py` | Python | `draft_fb_post`, `draft_ig_post`, `post_fb`, `post_ig`, `fetch_fb_summary`, `fetch_ig_summary` | FB Graph API, Instagram Basic Display |
| `x_mcp.py` | Python | `draft_x_post`, `post_x`, `reply_x`, `fetch_x_summary` | Twitter OAuth2 PKCE + token refresh |
| `email_mcp.mjs` | Node.js | `send_email`, `draft_email` | Gmail SMTP App Password |

### Vault Directories

| Directory | Purpose |
|-----------|---------|
| `Inbox/` | Raw incoming items (auto-created by watchers) |
| `Needs_Action/` | Items awaiting agent or human processing |
| `Plans/` | Agent-generated plans and drafts |
| `Pending_Approval/` | Items awaiting HITL approval |
| `Approved/` | Human-approved items ready for execution |
| `Done/` | Completed/archived items |
| `Briefings/` | Weekly CEO briefing files |
| `Audits/` | Social media engagement audit reports |
| `Accounting/` | Weekly transaction JSON files |
| `Logs/` | All structured logs (NDJSON + per-script JSON) |
| `.cache/` | LocalCache pickle files (API-down fallback) |
| `.pids/` | PID files and Ralph loop lock files |
| `Pending/` | Retry queue JSON files (max 50 items) |
| `Skills/` | Agent skill definitions (17 skills) |

---

## 3. Step-by-Step Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ (for email MCP)
- Git
- A Windows/Linux/macOS machine with at least 4 GB RAM and 10 GB free disk

### 3.1 Clone and Install

```bash
git clone <your-repo-url> D:/Hackathon-0
cd D:/Hackathon-0

# Python dependencies
pip install -r requirements.txt

# Node dependencies (email MCP)
npm install
```

`requirements.txt` includes:
```
pyyaml>=6.0
python-dotenv>=1.0
google-auth-oauthlib>=1.0
google-auth-httplib2>=0.1
google-api-python-client>=2.0
playwright>=1.40
facebook-sdk>=3.1
instabot>=0.117.0
mcp>=1.0
tweepy>=4.14
markdown>=3.5
weasyprint>=60.0
```

### 3.2 Configure Credentials

Create `.env` in the vault root (never commit this file):

```bash
# Facebook / Instagram
FB_PAGE_ACCESS_TOKEN=your_token_here
FB_PAGE_ID=your_page_id
IG_ACCESS_TOKEN=your_token_here
IG_ACCOUNT_ID=your_account_id

# X / Twitter
X_API_KEY=your_key
X_API_SECRET=your_secret
X_ACCESS_TOKEN=your_token
X_ACCESS_TOKEN_SECRET=your_secret
X_BEARER_TOKEN=your_bearer
X_CLIENT_ID=your_client_id
X_CLIENT_SECRET=your_client_secret

# Gmail
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=your_app_password
```

For Gmail watcher OAuth2, run once to generate `credentials.json` and `token.json`:

```bash
python watchers/gmail_watcher.py --auth
```

### 3.3 Configure `config.yaml`

```yaml
vault_path: "D:/Hackathon-0"
poll_interval_seconds: 120
dry_run: false          # Set true for testing without live API calls

facebook:
  max_posts_per_day: 5

x_twitter:
  max_posts_per_day: 10
  rate_limit_sleep: 900   # 15 minutes on HTTP 429

audit:
  age_threshold_days: 5   # Items older than this flagged as bottlenecks
```

### 3.4 Register MCP Servers

`.claude/mcp.json` is pre-configured. Verify it contains your vault path:

```json
{
  "mcpServers": {
    "email":  { "command": "node",   "args": ["D:/Hackathon-0/email_mcp.mjs"],  ... },
    "fb_ig":  { "command": "python", "args": ["D:/Hackathon-0/fb_ig_mcp.py"],   ... },
    "x":      { "command": "python", "args": ["D:/Hackathon-0/x_mcp.py"],       ... }
  }
}
```

### 3.5 Start the System

Open **four terminals** (or use a process manager like PM2):

```bash
# Terminal 1 — Watchers (inbound data)
python watchers/gmail_watcher.py
python watchers/whatsapp_watcher.py
python watchers/linkedin_watcher.py

# Terminal 2 — MCP Orchestrator (server pool + health monitor + Ralph trigger)
python mcp_orchestrator.py

# Terminal 3 — Watchdog (process monitor + disk check + queue drain)
python watchdog.py

# Terminal 4 — Weekly audit (cron-style, runs Monday 07:00)
# Option A: manual
python audit.py --week 2026-W08 --ralph
# Option B: Windows Task Scheduler
schtasks /create /tn "AI Employee Audit" /tr "python D:\Hackathon-0\audit.py --ralph" /sc WEEKLY /d MON /st 07:00
```

### 3.6 Verify Installation

```bash
# Check all components are running
python watchdog.py --status
python mcp_orchestrator.py --status

# Run a dry-run loop test
python ralph_loop.py --task Needs_Action/any_task.md --dry-run

# Analyze today's audit log
python log_analyzer.py

# Generate a test briefing
python audit.py --week 2026-W08 --dry-run
```

Open `Dashboard.md` in Obsidian to see the live status view.

---

## 4. Security Disclosure

### Secrets Management

| Rule | Implementation |
|------|---------------|
| No secrets in Markdown | `audit_logger.py` auto-redacts keys matching `token`, `password`, `api_key`, etc. in all log params |
| No secrets in git | `.gitignore` excludes `.env`, `credentials.json`, `token.json`, `.x_refresh_token`, `.cache/`, `.pids/` |
| Credentials via env vars | All tokens read from environment variables; `.env` loaded at runtime only |
| MCP server isolation | Each MCP server runs as a separate subprocess; Claude Code passes env vars per-server |

### Human-in-the-Loop (HITL)

All external actions require explicit human approval:

```
Agent drafts action
  └─► Plans/DRAFT_{id}.md          (human reads)
       └─► Pending_Approval/APPROVAL_{id}.md   (agent creates)
            └─► human moves/copies to Approved/{id}.md
                 └─► agent executes
```

The `hitl_enforcer.py` module blocks execution if no approval file exists. This cannot
be bypassed by the agent — it is a filesystem gate, not a code flag.

### Permissions Matrix

| Action | Agent Autonomous | Requires Approval |
|--------|-----------------|-------------------|
| Read vault files | Yes | — |
| Write to Inbox/, Plans/, Logs/ | Yes | — |
| Move items to Done/ | Yes | — |
| Update Dashboard.md | Yes | — |
| Send email | No | Yes — human approves via Approved/ |
| Post to social media | No | Yes — human approves draft |
| Delete any file | No | Yes — explicit instruction only |
| Modify Company_Handbook.md | No | Yes — handbook amendment proposal flow |
| Access new external service | No | Yes |
| Spend money / authorize payments | No | Yes |

### Network Exposure

- The system makes **outbound HTTPS calls only** (no inbound ports)
- All API traffic goes through official SDKs (Google, Tweepy, Facebook SDK)
- No data leaves the machine except via explicitly approved MCP tool calls

### Data Retention

- Vault markdown files: retained indefinitely (human decides when to archive)
- NDJSON audit logs: 90 days automatic rotation (configurable: `LOG_RETENTION_DAYS`)
- Per-script JSON logs: no automatic rotation (manual cleanup recommended)
- LocalCache `.pkl` files: TTL 1 hour, refreshed on each successful API call

---

## 5. Demo Flow

This end-to-end demo shows the full Gold Tier capability in under 5 minutes.

### Demo: Process a Sales Lead to Social Post

**Step 1 — Inbound lead arrives**

```bash
# Simulate a WhatsApp message (dry-run watcher)
python watchers/whatsapp_watcher.py --dry-run --once
# Creates: Needs_Action/YYYYMMDD_HHMM_WA_David_Levi_pricing.md
```

**Step 2 — Cross-domain classification**

The orchestrator classifies it as a cross-domain trigger (personal→business):
- Keyword `pricing` + `enterprise` → sales pipeline
- Creates: `Plans/SALES_QUOTE_david_levi.md`
- Creates: `Needs_Action/ALERT_new_lead_david_levi.md`

**Step 3 — Ralph loop drives the lead**

```bash
# The task has ralph_loop:true; orchestrator auto-triggers, or run manually:
python ralph_loop.py --task Needs_Action/20260224_david_levi_lead.md --dry-run
```

Output:
```
[ralph] Steps: plan -> approve -> execute
[ralph] Iter 1 | Step: plan     -> Plans/PLAN_david_levi_lead.md created
[ralph] Iter 2 | Step: approve  -> Approved/david_levi_lead.md written (dry-run)
[ralph] Iter 3 | Step: execute  -> <promise>TASK_COMPLETE</promise>
[ralph] [OK] Loop terminated: promise | 3 iterations | 0.9s
<promise>RALPH_LOOPS_ENABLED</promise>
```

**Step 4 — Post a LinkedIn update about the deal**

```bash
# X MCP draft (dry-run)
python x_mcp.py --test
# Creates: Plans/X_DRAFT_enterprise_deal.md
# Creates: Pending_Approval/APPROVAL_X_enterprise_deal.md
```

Human reviews draft in Obsidian → moves to `Approved/` → agent posts live.

**Step 5 — Weekly briefing**

```bash
python audit.py --week 2026-W08 --ralph
```

Output: `Briefings/CEO_Briefing_2026-W08.md`
```
Gross Revenue: $9,877.00  (+21.2% WoW)
Net Profit:    $8,046.23  (81.5% margin)
Bottlenecks:   10 items flagged
Top Action:    Cancel Unused SaaS Tool Z — save $220/period
<promise>AUDIT_COMPLETE</promise>
```

**Step 6 — Analyze the audit trail**

```bash
python log_analyzer.py
```

```
Total events: 47 | Errors: 2 | Criticals: 0
Top actor: mcp_orchestrator (18 events)
Top action: check_cycle (12 events)
Markdown report: Logs/analysis_2026-02-24.md
```

---

## 6. Lessons Learned

These are real insights from building the Gold Tier system, not theoretical best practices.

### "Ralph loops reduced lazy agent issues"

**Problem:** Early iterations had the agent mark tasks as "done" after creating a draft,
without verifying the actual execution completed. This led to items stuck in Plans/ forever.

**Fix:** The Ralph loop's file-movement completion check (`_task_in_done()`) makes it
impossible for the agent to claim success until the task file physically appears in `Done/`.
Promise tags (`<promise>TAG</promise>`) in output provide a secondary signal.

**Lesson:** Don't rely on agent self-reporting. Use filesystem state as ground truth.

### "HITL gates must be in the filesystem, not in code flags"

**Problem:** An early version checked a `human_approved` boolean in memory. The agent
could set it to `True` itself, bypassing the review step entirely.

**Fix:** The `hitl_enforcer.py` checks for a physical file in `Approved/`. The agent
can write to `Pending_Approval/` but has no vault permission to write to `Approved/`
directly — that requires the human to move the file.

**Lesson:** Security boundaries must be enforced by the operating environment, not by
trusting the agent's own checks.

### "Structured NDJSON beats append-only JSON arrays"

**Problem:** All early log files used JSON arrays (`[{...}, {...}]`). Reading them required
loading the entire file into memory, and any write failure mid-update would corrupt the
entire log.

**Fix:** Switched to NDJSON (one JSON object per line). Appending is an atomic `fh.write(line + "\n")`.
Reads are streamed line by line. `grep '"severity":"CRITICAL"' Logs/*.json` works directly.

**Lesson:** For append-heavy logs, NDJSON is strictly better than JSON arrays.

### "The 50-item queue cap prevents silent backpressure buildup"

**Problem:** During a 2-hour API outage, the Pending/ queue grew to 200+ items. When
the API recovered, the agent tried to replay all of them simultaneously, causing a new
rate-limit storm.

**Fix:** `MAX_QUEUE_SIZE=50` enforced in `queue_for_retry()`. Tasks beyond the cap are
dropped (logged). The human receives a Dashboard alert when queue depth approaches 40.

**Lesson:** Unbounded queues are a reliability anti-pattern. Backpressure must be explicit.

### "Exponential backoff base=2 is slower than you think"

**Problem:** `2**3 = 8` seconds felt fast in tests but in production with 3 watchers
each retrying 3 times, the compound delay was acceptable. However, for rate-limited
social APIs (Twitter's 15-minute window), the base=2 backoff is irrelevant.

**Fix:** Social MCPs use a dedicated `RATE_LIMIT_SLEEP=900` (15 minutes) override.
The general `@retry` decorator uses base=2 for transient errors only.

**Lesson:** Retry strategy must match the error type. Network blips → short backoff.
API rate limits → respect the `Retry-After` header or vendor-specified window.

### "DegradedMode needs explicit exit conditions"

**Problem:** The first `DegradedMode` implementation entered degraded mode on any
`DiskFullError` but had no automatic exit. The system stayed degraded indefinitely
even after the human freed up disk space.

**Fix:** `watchdog.py` calls `exit_degraded()` when `disk_check()` passes cleanly on
the next health cycle. The condition is re-evaluated every 60 seconds.

**Lesson:** Every mode entry needs a corresponding automatic exit condition.

### "Windows cp1252 will silently corrupt your Unicode logs"

**Problem:** `mcp_orchestrator.py` printed status messages with `→` (U+2192). On the
Windows cp1252 terminal, this raised `UnicodeEncodeError` and crashed the orchestrator's
test run.

**Fix:** All print statements use ASCII-safe characters (`->` instead of `→`).
All file writes use `encoding="utf-8"` explicitly.

**Lesson:** On Windows, always specify `encoding="utf-8"` in `open()` calls.
Never rely on the system locale for terminal output.

### "Vault file naming conventions are load-bearing"

**Problem:** Early files used arbitrary names. The `_note_exists()` dedup check in
`base_watcher.py` scans file contents for an ID hash. Without consistent naming, files
were easy to miss and duplicates appeared.

**Fix:** The convention `YYYYMMDD_HHMM_Short_Title.md` means files sort chronologically
in any file browser, the timestamp is embedded in the name, and prefix filters work
reliably (`glob("EMAIL_*.md")`).

**Lesson:** File naming is part of your architecture. Choose a convention early and
enforce it everywhere.

---

## 7. Troubleshooting FAQ

### Q: Watcher isn't creating any notes

**Check 1:** Is the API credential valid?
```bash
python watchers/gmail_watcher.py --dry-run --once
# Should print "DRY_RUN: would create note..."
```

**Check 2:** Is poll_interval_seconds too long? Default is 120s.
```bash
grep poll_interval config.yaml
```

**Check 3:** Is the item already in Done/ (dedup check prevents re-creation)?
```bash
ls Done/ | grep <item-keywords>
```

---

### Q: MCP server won't start / orchestrator shows "dead"

**Check 1:** Is the command in `.claude/mcp.json` valid?
```bash
python mcp_orchestrator.py --status
```

**Check 2:** Is the environment variable set?
```bash
echo $FB_PAGE_ACCESS_TOKEN     # should not be empty
```

**Check 3:** Run the server manually to see its error:
```bash
DRY_RUN=true python fb_ig_mcp.py --test
```

---

### Q: Ralph loop is stuck waiting for approval

The `approve` step waits for `Approved/{task_id}.md`. In production, a human
must create this file. In dry-run mode it is auto-created.

```bash
# Manually approve a task:
cp "Pending_Approval/APPROVAL_task_id.md" "Approved/task_id.md"

# Or check the lock file to confirm loop is running:
python ralph_loop.py --status
```

---

### Q: Disk alert keeps firing

**Check:** How much free space is actually available?
```bash
python -c "import shutil; u=shutil.disk_usage('D:/'); print(f'{u.free/1e9:.2f} GB free')"
```

The alert threshold is 1 GB (`DISK_ALERT_GB=1.0`). Adjust:
```bash
set DISK_ALERT_GB=0.5
python watchdog.py
```

Clear old logs to free space:
```bash
python -c "from watchers.audit_logger import prune_old_logs; print(prune_old_logs(30))"
```

---

### Q: log_analyzer.py shows 0 events

NDJSON logs are written to `Logs/YYYY-MM-DD.json`. Check that at least one script
has run today:
```bash
python log_analyzer.py --list-dates
```

If today's file is missing, run a test event:
```bash
python -c "import sys; sys.path.insert(0,'watchers'); from audit_logger import audit_log; audit_log('test','ping')"
python log_analyzer.py
```

---

### Q: audit.py shows wrong week

The ISO week calculation is system-locale dependent. Always pass `--week` explicitly:
```bash
python audit.py --week 2026-W08
```

Check what week today falls in:
```bash
python -c "from datetime import datetime; ic=datetime.now().isocalendar(); print(f'{ic.year}-W{ic.week:02d}')"
```

---

### Q: "Lock held by PID XXXX" when starting ralph_loop.py

A previous loop crashed without cleaning up its lock file.
```bash
# Check if the PID is still running
python -c "import os; os.kill(XXXX, 0)"  # replace XXXX

# If not running, remove the stale lock
del .pids\ralph_<task_id>.lock
```

---

### Q: Social post appears twice / draft duplicated

The `_posts_today()` counter reads from today's social log. If the log was corrupted:
```bash
# FB/IG
type Logs\social_2026-02-24.json | python -m json.tool > nul
# X
type Logs\social_x_2026-02-24.json | python -m json.tool > nul
```

If the file is corrupt, the counter resets to 0. The daily cap (5 / 10 posts) will
still prevent runaway posting within one day.

---

## 8. Scalability & Cloud Migration

### Current Architecture Limits

| Component | Current Limit | Bottleneck |
|-----------|--------------|-----------|
| Watchers | ~10 active | All in one process; poll-based |
| MCP servers | 3 (1 instance each) | Single subprocess per server |
| Vault storage | ~10 GB practical | Local filesystem I/O |
| Audit log throughput | ~10,000 events/day | NDJSON append to single file per day |
| Task queue | 50 items max | Handbook rule + filesystem-based |

### Phase 1: Multi-Instance Scaling (same machine)

Increase `num_instances` in `McpPool` to run multiple server copies behind the
round-robin load balancer in `mcp_orchestrator.py`:

```python
# In mcp_orchestrator.py _build_pools():
self.pools[name] = McpPool(name=name, config=cfg, num_instances=3)
```

Run multiple watcher processes pointing at the same vault:
```bash
python watchers/gmail_watcher.py --poll-interval 60 &
python watchers/gmail_watcher.py --poll-interval 60 &
```

Dedup via `_note_exists()` (content hash) prevents duplicate notes.

### Phase 2: Cloud Storage Backend (Azure / S3)

Replace the local filesystem vault with a cloud-backed store:

```
Local filesystem      →     Azure Blob Storage / S3
Logs/YYYY-MM-DD.json        s3://my-vault/Logs/YYYY-MM-DD.json
Needs_Action/               s3://my-vault/Needs_Action/
```

Required changes:
1. Abstract `PathLike` in `base_watcher.py` to support `fsspec` or boto3 paths
2. Replace `Path.write_text()` calls with `s3.put_object()`
3. Use S3 event notifications instead of filesystem polling for watchers
4. Replace `.pids/` with DynamoDB or Redis for distributed PID tracking

### Phase 3: Event-Driven Architecture

Replace polling watchers with event-driven triggers:

```
Gmail push notifications  →  AWS Lambda  →  SQS  →  Processor
Twitter webhook           →  AWS Lambda  →  SQS  →  Processor
WhatsApp webhook          →  AWS Lambda  →  SQS  →  Processor
```

The `BaseWatcher.check()` interface stays the same; only the trigger mechanism changes.

### Phase 4: Multi-Tenant (Team Deployment)

Each team member gets their own vault path prefix:

```
s3://company-vault/users/alice/Needs_Action/
s3://company-vault/users/bob/Needs_Action/
s3://company-vault/shared/Briefings/
```

Add a `tenant_id` field to the audit log schema for cross-tenant analysis.

### Cloud Migration Checklist

- [ ] Externalize all secrets to AWS Secrets Manager / Azure Key Vault
- [ ] Replace local `LocalCache` with Redis / ElastiCache
- [ ] Replace filesystem queue (`Pending/`) with SQS FIFO queue
- [ ] Replace `.pids/` lock files with DynamoDB conditional writes (atomic)
- [ ] Replace `Dashboard.md` with a real-time web dashboard (Streamlit / Grafana)
- [ ] Move NDJSON logs to CloudWatch Logs or Elasticsearch for searchability
- [ ] Container-ize each component (Docker) for independent scaling
- [ ] Add OpenTelemetry instrumentation using the existing `AuditLogger` schema

---

## 9. PDF Export

Use `export_pdf.py` (included in this vault):

```bash
# Generate PDF from this README:
python export_pdf.py

# Generate from any Markdown file:
python export_pdf.py --input README_Gold.md --output Docs/README_Gold.pdf

# Check available backends:
python export_pdf.py --check
```

### Manual Export Options

**Option A — Obsidian PDF export** (recommended, preserves formatting):
1. Open `README_Gold.md` in Obsidian
2. `Ctrl+P` → "Export to PDF"
3. Save to `Docs/README_Gold.pdf`

**Option B — Pandoc** (if installed):
```bash
pandoc README_Gold.md -o Docs/README_Gold.pdf --pdf-engine=wkhtmltopdf
```

**Option C — Browser** (universal fallback):
```bash
python -m http.server 8000
# Open http://localhost:8000/README_Gold.md in Chrome/Edge
# Print → Save as PDF
```

**Option D — VS Code** (Markdown PDF extension):
Install `yzane.markdown-pdf` extension → Right-click README_Gold.md → "Markdown PDF: Export (pdf)"

---

## Appendix A: Skill Index

| Skill File | Trigger | Key Output |
|------------|---------|------------|
| `SKILL_vault_setup.md` | New vault | Folder structure, config.yaml |
| `SKILL_vault_read.md` | Read request | Summarised Needs_Action items |
| `SKILL_vault_write.md` | Write request | Structured Markdown note |
| `SKILL_gmail_watcher.md` | Email arrives | Needs_Action/EMAIL_*.md |
| `SKILL_whatsapp_watcher.md` | WA message | Needs_Action/WA_*.md |
| `SKILL_linkedin_watcher.md` | LinkedIn event | Needs_Action/LI_*.md |
| `SKILL_email_mcp.md` | Send email | Approved draft → Gmail |
| `SKILL_FB_IG.md` | Social post request | Draft → approve → post |
| `SKILL_X_Integration.md` | X post request | Draft → approve → tweet |
| `SKILL_MCP_Manager.md` | MCP health issue | Server restart, queue drain |
| `SKILL_Weekly_Audit.md` | Monday 07:00 | CEO_Briefing_{week}.md |
| `SKILL_Error_Recovery.md` | Any failure | Retry → cache → queue → degrade |
| `SKILL_Logging.md` | Any event | Logs/YYYY-MM-DD.json NDJSON |
| `SKILL_ralph_loop.md` | Complex task | plan → approve → execute → Done/ |
| `SKILL_Cross_Integration.md` | Cross-domain | CROSS_PLAN, unified links |
| `SKILL_hitl_enforcer.md` | External action | Approval gate |
| `SKILL_bronze_test.md` | Bronze test | Basic vault operations |

---

## Appendix B: Environment Variables

| Variable | Default | Component |
|----------|---------|-----------|
| `VAULT_PATH` | Script parent dir | All scripts |
| `DRY_RUN` | `false` | All MCP servers |
| `RETRY_MAX` | `3` | resilience.py |
| `RETRY_BACKOFF_BASE` | `2` | resilience.py |
| `DISK_ALERT_GB` | `1.0` | resilience.py |
| `DISK_CRITICAL_GB` | `0.2` | resilience.py |
| `MAX_QUEUE_SIZE` | `50` | resilience.py |
| `LOG_RETENTION_DAYS` | `90` | audit_logger.py |
| `RATE_LIMIT_SLEEP` | `900` | x_mcp.py |
| `FB_PAGE_ACCESS_TOKEN` | — | fb_ig_mcp.py |
| `X_API_KEY` | — | x_mcp.py |
| `GMAIL_USER` | — | email_mcp.mjs |
| `GMAIL_APP_PASSWORD` | — | email_mcp.mjs |

---

*Generated by the Personal AI Employee — Gold Tier*
*`python export_pdf.py` to produce a PDF version of this document.*
