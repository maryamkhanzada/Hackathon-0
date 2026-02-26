---
id: SKILL_whatsapp_watcher
version: 1.0
tier: silver
created: 2026-02-19
status: active
tags:
  - watcher
  - whatsapp
  - playwright
  - HITL
---

# SKILL: WhatsApp Watcher

## Trigger

Run continuously or on-demand to monitor WhatsApp Web for unread messages
containing priority keywords.

## Prerequisites

1. Python 3.11+ with dependencies installed:
   ```
   pip install playwright pyyaml
   python -m playwright install chromium
   ```
2. First run requires scanning WhatsApp Web QR code in the browser window.
3. `config.yaml` configured with `whatsapp` section (session path, keywords, poll interval).

## How It Works

1. Launches a Playwright persistent Chromium context (session survives restarts).
2. Opens WhatsApp Web (`web.whatsapp.com`).
3. Every 30 seconds, scans the chat sidebar for unread indicators.
4. For each unread chat, extracts: chat name, last message preview, unread count.
5. Checks the message text against trigger keywords: `urgent`, `invoice`, `payment`, `pricing`, `help`.
6. If keywords match, creates `WHATSAPP_{hash}.md` in `Needs_Action/` with:
   - YAML frontmatter: type, from, keywords_matched, received, priority, status
   - Message preview
   - Suggested action checkboxes
7. Deduplicates using content hashes (won't create duplicate files for same message).

## Usage

```bash
# Continuous monitoring (30s poll)
python watchers/whatsapp_watcher.py

# Single check cycle
python watchers/whatsapp_watcher.py --once

# Demo mode (no browser needed)
python watchers/whatsapp_watcher.py --demo

# Override vault path
python watchers/whatsapp_watcher.py --vault /path/to/vault
```

## Priority Classification

| Keywords Matched     | Priority |
|---------------------|----------|
| urgent              | critical |
| payment, invoice    | high     |
| pricing             | high     |
| help                | medium   |

## Output Format

File: `Needs_Action/WHATSAPP_{hash}.md`

```yaml
---
id: abc123def456
source: whatsapp_watcher
priority: high
created: 2026-02-19 14:30
status: pending
tags: ["#whatsapp", "#priority-high", "#kw-payment"]
type: whatsapp
from: Ahmed Khan
keywords_matched: payment, urgent
received: 2026-02-19 14:30
unread_count: 3
---
```

## Acceptance Criteria

- [x] Uses BaseWatcher pattern with check() and create_action_file()
- [x] Playwright persistent context (QR code only needed once)
- [x] 30-second polling interval
- [x] Keyword matching with configurable keyword list
- [x] WHATSAPP_ filename prefix
- [x] YAML frontmatter with all required fields
- [x] Deduplication via content hashing
- [x] Graceful error handling with logging
- [x] --demo mode for testing without browser
- [x] Configurable via config.yaml and environment variables
