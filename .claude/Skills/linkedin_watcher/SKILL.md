---
id: SKILL_linkedin_watcher
version: 1.0
tier: silver
created: 2026-02-19
status: active
tags:
  - watcher
  - linkedin
  - playwright
  - sales
  - HITL
---

# SKILL: LinkedIn Watcher

## Trigger

Run continuously or on-demand to monitor LinkedIn notifications and messages
for sales leads and business opportunities.

## Prerequisites

1. Python 3.11+ with dependencies installed:
   ```
   pip install playwright pyyaml
   python -m playwright install chromium
   ```
2. First run requires logging into LinkedIn in the browser window (session persists).
3. `config.yaml` configured with `linkedin` section (session path, keywords, poll interval).

## How It Works

1. Launches a Playwright persistent Chromium context.
2. Opens LinkedIn and navigates to notifications and messaging pages.
3. Every 120 seconds:
   - Scrapes the notifications page for recent activity.
   - Scrapes the messaging page for unread conversations.
4. Checks all content against sales keywords: `interested`, `buy`, `quote`, `pricing`, `purchase`, `proposal`, `demo`, `trial`.
5. If keywords match, classifies the lead type and creates `LINKEDIN_{timestamp_hash}.md` in `Needs_Action/`.
6. Each action file includes:
   - YAML frontmatter: type, from, lead_type, keywords_matched, received, priority
   - Content preview
   - Suggested action checkboxes (reply, send quote, schedule call, etc.)
7. Deduplicates using content hashes.

## Usage

```bash
# Continuous monitoring (120s poll)
python watchers/linkedin_watcher.py

# Single check cycle
python watchers/linkedin_watcher.py --once

# Demo mode (no browser needed)
python watchers/linkedin_watcher.py --demo

# Override vault path
python watchers/linkedin_watcher.py --vault /path/to/vault
```

## Lead Classification

| Keywords Matched         | Lead Type            | Priority |
|--------------------------|---------------------|----------|
| buy, purchase            | hot_lead            | critical |
| quote, pricing, proposal | warm_lead           | high     |
| interested, demo, trial  | interested_prospect | medium   |
| (other matches)          | general_inquiry     | medium   |

## Output Format

File: `Needs_Action/LINKEDIN_{timestamp_hash}.md`

```yaml
---
id: abc123def456
source: linkedin_watcher
priority: critical
created: 2026-02-19 15:00
status: pending
tags: ["#linkedin", "#sales-lead", "#priority-critical", "#lead-hot_lead"]
type: linkedin
from: John Smith
lead_type: hot_lead
keywords_matched: buy, pricing
received: 2026-02-19 15:00
linkedin_source: message
---
```

## HITL Integration

- LinkedIn sales post drafts are NEVER published automatically.
- Agent creates draft posts in `Plans/` folder only.
- To send a LinkedIn message or post, agent creates `approval_request.md` in `Pending_Approval/`.
- Human must move to `Approved/` before any external action.

## Acceptance Criteria

- [x] Uses BaseWatcher pattern with check() and create_action_file()
- [x] Playwright persistent context (login only needed once)
- [x] 120-second polling interval
- [x] Monitors both notifications and messages
- [x] Sales keyword matching with lead type classification
- [x] LINKEDIN_ filename prefix
- [x] YAML frontmatter with all required fields including lead_type
- [x] Deduplication via content hashing
- [x] Graceful error handling with logging
- [x] --demo mode for testing without browser
- [x] HITL: no external actions without approval
- [x] Configurable via config.yaml and environment variables
