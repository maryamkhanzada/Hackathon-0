---
id: SKILL_hitl_enforcer
version: 1.0
tier: silver
created: 2026-02-20
status: active
tags:
  - HITL
  - approval
  - security
  - core
---

# SKILL: HITL Enforcer

## Overview

The HITL (Human-in-the-Loop) Enforcer is the **central security gate** for the
Personal AI Employee. Every sensitive external action MUST pass through it.
No email, payment, social post, or external API call happens without explicit
human approval via file movement.

## How It Works

```
Skill/Watcher → hitl.guard() → Creates ACTION_*.md in Pending_Approval/
                                ↓
                 Human reviews in Obsidian
                                ↓
         ┌──── Moves to /Approved/ ────→ approval_loop.py executes → /Done/
         │
         └──── Moves to /Rejected/ ────→ approval_loop.py logs → stays in /Rejected/
                                ↓
                 (or expires after 24h → auto-rejected)
```

## Components

### 1. `hitl_enforcer.py` — Reusable Module
Import into any skill or watcher:
```python
from hitl_enforcer import HITLEnforcer

hitl = HITLEnforcer(vault_path="/path/to/vault")

# The guard() function — call before ANY sensitive action
result = hitl.guard(
    action_type="email_send",
    details={"to": "client@example.com", "subject": "Proposal"},
    reason="Reply to sales lead",
    priority="high",
    source_skill="gmail_watcher",
)

if result["allowed"]:
    # Human approved — safe to proceed
    execute_action()
    hitl.mark_executed(result["approval_file"])
else:
    # Not yet approved — do nothing, check again next cycle
    print(f"Awaiting approval: {result['approval_file']}")
```

### 2. `approval_loop.py` — Background Scanner
Runs continuously alongside watchers:
```bash
python watchers/approval_loop.py              # Continuous (30s)
python watchers/approval_loop.py --once       # Single scan
python watchers/approval_loop.py --status     # Print status
```

## Sensitive Action Types

| Action Type | Trigger |
|-------------|---------|
| `email_send` | Sending any email |
| `email_reply` | Replying to an email |
| `social_post` | Publishing to social media |
| `linkedin_post` | LinkedIn post specifically |
| `linkedin_message` | Direct message on LinkedIn |
| `whatsapp_send` | Sending WhatsApp message |
| `payment` | Any financial transaction |
| `api_call_external` | Calling third-party APIs |
| `file_delete` | Deleting any vault file |
| `contact_new` | Adding new contacts |
| `bulk_action` | Any batch operation |

## Approval File Format

`Pending_Approval/ACTION_{type}_{YYYYMMDD_HHMMSS}.md`:

```yaml
---
id: abc123
type: approval_request
action_type: email_send
status: pending_approval
priority: high
created: 2026-02-20 13:44
expires: 2026-02-21 13:44
source_skill: gmail_watcher
tags: ["#approval", "#HITL", "#action-email_send", "#priority-high"]
---
```

## CLI Usage

```bash
# Check status of all approvals
python watchers/hitl_enforcer.py --vault D:/Hackathon-0 status

# Expire stale requests
python watchers/hitl_enforcer.py --vault D:/Hackathon-0 expire

# Create demo approval
python watchers/hitl_enforcer.py --vault D:/Hackathon-0 demo --type payment
```

## Acceptance Criteria

- [x] Reusable module importable by all skills/watchers
- [x] guard() function as single entry point
- [x] ACTION_{type}_{timestamp}.md format with full YAML frontmatter
- [x] 24-hour default expiry with auto-rejection
- [x] approval_loop.py scans Approved/Rejected/Expired
- [x] Full cycle tested: Pending → Approved → Executed → Done/
- [x] Dashboard auto-updated with pending items
- [x] Activity logging for all state transitions
- [x] Payment amounts > $100 flagged per Company Handbook
- [x] CLI tools for status/expire/demo
