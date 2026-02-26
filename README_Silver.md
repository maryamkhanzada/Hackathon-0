# Personal AI Employee — Silver Tier

> An autonomous AI agent that manages personal and business affairs 24/7
> using Obsidian as its memory/dashboard and file-based HITL workflows.

---

## System Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │           orchestrator.py                    │
                    │        (Master Controller)                   │
                    │                                             │
                    │   • Launches watcher subprocesses           │
                    │   • Runs 60s processing cycles              │
                    │   • Auto-restarts dead watchers             │
                    │   • Generates daily summaries               │
                    └──────────┬──────────────────────────────────┘
                               │
            ┌──────────────────┼──────────────────────┐
            │                  │                      │
            ▼                  ▼                      ▼
   ┌─────────────────┐ ┌──────────────┐  ┌───────────────────┐
   │  gmail_watcher   │ │  whatsapp_   │  │  linkedin_        │
   │  (Gmail API)     │ │  watcher     │  │  watcher          │
   │  120s poll       │ │  (Playwright)│  │  (Playwright)     │
   │  is:unread       │ │  30s poll    │  │  120s poll        │
   │  is:important    │ │  keywords    │  │  sales keywords   │
   └────────┬─────────┘ └──────┬───────┘  └────────┬──────────┘
            │                  │                    │
            └──────────────────┼────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Needs_Action/      │
                    │                     │
                    │  EMAIL_*.md         │
                    │  WHATSAPP_*.md      │
                    │  LINKEDIN_*.md      │
                    └────────┬────────────┘
                             │
                    ┌────────▼────────────┐
                    │   Processing Engine  │
                    │   (vault_processor)  │
                    │                     │
                    │  • Parse frontmatter │
                    │  • Classify priority │
                    │  • Create Plans      │
                    │  • Update Dashboard  │
                    └────────┬────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
     ┌────────────┐  ┌──────────────┐  ┌────────────┐
     │  Plans/     │  │ In_Progress/ │  │   Done/    │
     │            │  │              │  │            │
     │ PLAN_*.md  │  │ (working on) │  │ (archived) │
     │ POST_*.md  │  │              │  │            │
     │ DRAFT_*.md │  │              │  │            │
     └──────┬─────┘  └──────────────┘  └────────────┘
            │
            │  (external action needed?)
            │
            ▼
   ┌──────────────────────────────────────────────────┐
   │              HITL GATE                            │
   │         (hitl_enforcer.py)                        │
   │                                                  │
   │   guard() → ACTION_*.md in Pending_Approval/     │
   │                                                  │
   │   ┌──────────────────────────────────────┐       │
   │   │       Pending_Approval/               │       │
   │   │                                      │       │
   │   │  ACTION_email_send_*.md              │       │
   │   │  ACTION_payment_*.md                 │       │
   │   │  ACTION_social_post_*.md             │       │
   │   │  APPROVAL_LINKEDIN_*.md              │       │
   │   │  APPROVAL_PAYMENT_*.md               │       │
   │   └──────────┬───────────────────────────┘       │
   │              │                                    │
   │      HUMAN DECISION (move file)                   │
   │              │                                    │
   │     ┌────────┴────────┐                           │
   │     │                 │                           │
   │     ▼                 ▼                           │
   │  Approved/        Rejected/                       │
   │     │                 │                           │
   │     │                 └──→ Log + archive          │
   │     ▼                                             │
   │  approval_loop.py                                 │
   │  (executes action)                                │
   │     │                                             │
   │     ▼                                             │
   │  ┌──────────────┐                                 │
   │  │ Email MCP    │  ← send_email / draft_email     │
   │  │ (email_mcp)  │                                 │
   │  └──────────────┘                                 │
   │     │                                             │
   │     ▼                                             │
   │   Done/ (archived with execution log)             │
   └──────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Install Python dependencies
pip install pyyaml google-auth google-auth-oauthlib google-api-python-client playwright python-dotenv

# 2. Install Node.js dependencies (for Email MCP)
cd D:/Hackathon-0
npm install

# 3. Install Playwright browsers (for WhatsApp/LinkedIn)
python -m playwright install chromium

# 4. Configure credentials
cp .env.example .env
# Edit .env with your Gmail OAuth2 credentials

# 5. Check system status
python orchestrator.py --status

# 6. Run a single processing cycle (safe test)
python orchestrator.py --once

# 7. Run full system
python orchestrator.py
```

---

## Vault Layout

```
D:/Hackathon-0/
├── orchestrator.py          # Master controller (start here)
├── email_mcp.mjs            # MCP server for Claude Code email tools
├── config.yaml              # Central configuration
├── .env                     # Credentials (never committed)
├── .env.example             # Credential template
├── Company_Handbook.md      # Operating rules (agent obeys)
├── Dashboard.md             # Live status (auto-updated)
├── CLAUDE.md                # Claude Code project config
├── README_Silver.md         # This file
│
├── Inbox/                   # Raw incoming items
├── Needs_Action/            # Items awaiting processing
├── In_Progress/             # Items being worked on
├── Plans/                   # Multi-step plans + drafts
│   ├── PLAN_*.md            # Reasoning plans with checkboxes
│   ├── LINKEDIN_POST_*.md   # Social post drafts
│   └── EMAIL_DRAFT_*.md     # Email drafts
│
├── Pending_Approval/        # HITL: awaiting human decision
│   ├── ACTION_*.md          # HITL enforcer requests
│   └── APPROVAL_*.md        # Skill-generated requests
├── Approved/                # Human approved → agent executes
├── Rejected/                # Human rejected → logged, not executed
├── Done/                    # Completed + archived items
│
├── Skills/                  # Agent skill definitions
│   ├── SKILL_gmail_watcher.md
│   ├── SKILL_whatsapp_watcher.md
│   ├── SKILL_linkedin_watcher.md
│   ├── SKILL_hitl_enforcer.md
│   └── SKILL_email_mcp.md
│
├── Logs/                    # Activity logs
│   ├── activity_YYYY-MM-DD.log
│   ├── email_YYYY-MM-DD.json
│   ├── daily_summary_YYYY-MM-DD.md
│   └── orchestrator.log
│
├── Templates/               # Reusable note templates
│
└── watchers/                # Python watcher scripts
    ├── base_watcher.py      # Abstract base class
    ├── gmail_watcher.py     # Gmail API (OAuth2)
    ├── whatsapp_watcher.py  # WhatsApp Web (Playwright)
    ├── linkedin_watcher.py  # LinkedIn (Playwright)
    ├── hitl_enforcer.py     # HITL approval gate
    ├── approval_loop.py     # Approved action executor
    └── vault_processor.py   # Vault scan + dashboard updater
```

---

## Components

### Watchers (Input Sensors)

| Watcher | Source | Poll | Output | Keywords |
|---------|--------|------|--------|----------|
| gmail_watcher | Gmail API | 120s | `EMAIL_{id}.md` | urgent, asap, invoice, payment |
| whatsapp_watcher | WhatsApp Web | 30s | `WHATSAPP_{hash}.md` | urgent, invoice, payment, pricing, help |
| linkedin_watcher | LinkedIn Web | 120s | `LINKEDIN_{ts}.md` | interested, buy, quote, pricing, demo |

### Processing Engine

| Component | Purpose |
|-----------|---------|
| vault_processor.py | Scans vault, updates Dashboard, moves done items |
| orchestrator.py | Master loop: watchers + processing + summaries |

### HITL (Human-in-the-Loop)

| Component | Purpose |
|-----------|---------|
| hitl_enforcer.py | Creates approval requests, checks status, `guard()` gate |
| approval_loop.py | Scans Approved/Rejected, executes actions, expires stale |

### MCP Server

| Component | Tools | Purpose |
|-----------|-------|---------|
| email_mcp.mjs | `send_email`, `draft_email` | Claude Code email integration |

---

## HITL Approval Flow

Every external action follows this exact path:

```
1. Agent wants to send email / post / payment
2. hitl.guard() → Creates ACTION_*.md in Pending_Approval/
3. Agent STOPS. Does NOT proceed.
4. Dashboard shows "Awaiting Your Approval"
5. Human reviews in Obsidian
6. Human moves file:
   → /Approved/  = Agent executes the action
   → /Rejected/  = Agent logs rejection, does nothing
   → (no action) = Auto-expires after 24 hours
```

---

## Orchestrator Modes

```bash
# Full system: watchers + processing loop
python orchestrator.py

# Processing only (watchers run separately)
python orchestrator.py --no-watchers

# Single cycle (for cron)
python orchestrator.py --once

# System status check
python orchestrator.py --status

# Daily summary
python orchestrator.py --daily-summary

# Enable Playwright watchers
python orchestrator.py --enable whatsapp_watcher linkedin_watcher
```

---

## Scheduling

### Linux/Mac (cron)

```bash
crontab -e

# Process cycle every 15 minutes
*/15 * * * * cd /d/Hackathon-0 && python orchestrator.py --once >> Logs/cron.log 2>&1

# Daily summary at 11pm
0 23 * * * cd /d/Hackathon-0 && python orchestrator.py --daily-summary >> Logs/cron.log 2>&1

# Full system at boot (continuous)
@reboot cd /d/Hackathon-0 && python orchestrator.py >> Logs/orchestrator.log 2>&1 &
```

### Windows (Task Scheduler)

```
Program: python
Arguments: D:\Hackathon-0\orchestrator.py --once
Start in: D:\Hackathon-0
Trigger: Every 15 minutes
```

Or run as a background process:

```powershell
Start-Process -NoNewWindow python -ArgumentList "D:\Hackathon-0\orchestrator.py"
```

---

## MCP Integration (Claude Code)

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "email": {
      "command": "node",
      "args": ["D:/Hackathon-0/email_mcp.mjs"],
      "env": {
        "VAULT_PATH": "D:/Hackathon-0",
        "DRY_RUN": "true"
      }
    }
  }
}
```

---

## Security Rules (from Company Handbook)

- **Autonomous (no approval):** Read files, summarize, create notes, update Dashboard, write logs
- **Requires approval:** Send emails, social posts, payments, new contacts, bulk actions, file deletion
- **Never:** Write secrets to markdown, transmit data externally without approval, modify handbook
- **Flagged:** Payments > $100, new contacts, bulk operations
- **Keywords auto-escalate:** urgent, invoice, payment, asap

---

## Demo Mode

Every watcher supports `--demo` for testing without credentials:

```bash
python watchers/gmail_watcher.py --demo      # 3 test emails
python watchers/whatsapp_watcher.py --demo   # 2 test chats
python watchers/linkedin_watcher.py --demo   # 2 test leads
python watchers/hitl_enforcer.py demo        # 1 test approval
```

---

_Built for the Personal AI Employee Hackathon — Silver Tier_
_Powered by Claude Code + Obsidian_
