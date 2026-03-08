# SKILL: Gmail Watcher

> **Trigger:** Manual start, or launched by orchestrator on boot
> **Purpose:** Monitor Gmail for unread messages and create actionable notes
> **Tier:** Bronze
> **Source file:** `watchers/gmail_watcher.py`

---

## Description

Polls Gmail via the official API for unread messages matching a configurable
query.  For each new email it creates a structured Markdown note in
`Needs_Action/` with full headers, body preview, priority classification, and
suggested next actions.  Deduplication prevents the same email from creating
multiple notes.

---

## Prerequisites

### 1. Python environment

```bash
pip install -r requirements.txt
```

Required packages: `google-auth`, `google-auth-oauthlib`,
`google-api-python-client`, `pyyaml`.

### 2. Google Cloud project & OAuth credentials

Follow these steps **once**:

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or use an existing one).
3. Navigate to **APIs & Services → Library**.
4. Search for **Gmail API** and click **Enable**.
5. Go to **APIs & Services → Credentials**.
6. Click **Create Credentials → OAuth client ID**.
7. Application type: **Desktop app**.
8. Download the JSON file and save it as:
   ```
   D:/Hackathon-0/credentials.json
   ```
9. Go to **APIs & Services → OAuth consent screen**.
10. Add your Gmail address as a **Test user** (required while app is in
    "Testing" status).

### 3. First-run authorization

```bash
cd D:/Hackathon-0/watchers
python gmail_watcher.py --once
```

A browser window will open for Google consent.  After approval, `token.json`
is saved automatically.  Subsequent runs use the token without a browser.

### 4. Configuration

Edit `config.yaml` at the vault root:

```yaml
gmail:
  credentials_path: "D:/Hackathon-0/credentials.json"
  token_path: "D:/Hackathon-0/token.json"
  query: "is:unread category:primary"
  max_results: 10
```

| Key                | Default                     | Description                        |
| ------------------ | --------------------------- | ---------------------------------- |
| `credentials_path` | `vault_root/credentials.json` | Path to OAuth client secret JSON |
| `token_path`       | `vault_root/token.json`     | Auto-generated after consent       |
| `query`            | `is:unread category:primary`| Gmail search query to filter mail  |
| `max_results`      | `10`                        | Max messages fetched per cycle     |

**Useful query variations:**

| Query                                     | Behavior                          |
| ----------------------------------------- | --------------------------------- |
| `is:unread category:primary`              | Primary inbox, unread only        |
| `is:unread is:important`                  | Gmail's Important marker          |
| `is:unread from:boss@company.com`         | Specific sender                   |
| `is:unread newer_than:1d`                 | Last 24 hours only                |
| `is:unread -category:promotions -category:social` | Skip promo & social      |

---

## Running

### Continuous mode (recommended for production)

```bash
python watchers/gmail_watcher.py
```

Polls every `poll_interval_seconds` (default 120s).  Ctrl+C to stop.

### Single check (for testing / cron)

```bash
python watchers/gmail_watcher.py --once
```

### Custom config path

```bash
python watchers/gmail_watcher.py --config /path/to/config.yaml
```

---

## Output Format

Each email produces a note in `Needs_Action/` like this:

```
Needs_Action/20260218_1430_Email_Meeting_tomorrow.md
```

```markdown
---
id: a1b2c3d4e5f6
source: gmail_watcher
priority: high
created: 2026-02-18 14:30
status: open
tags: [#email #gmail #priority-high]
---

# Email: Meeting tomorrow

**Priority:** high
**Source:** gmail_watcher
**Received:** 2026-02-18 14:30

---

**From:** Alice <alice@example.com>
**To:** you@gmail.com
**Date:** 2026-02-18 14:25
**Subject:** Meeting tomorrow
**Labels:** UNREAD, IMPORTANT, INBOX

---

Hi, can we move the meeting to 3pm? Let me know.

---

## Suggested Actions

- [ ] Reply to Alice <alice@example.com>
- [ ] Read full email in Gmail
- [ ] Archive if no action needed
- [ ] Check calendar for conflicts
```

---

## Architecture

```
gmail_watcher.py
  └─ extends BaseWatcher (base_watcher.py)
       ├─ poll loop (check → create_note → sleep)
       ├─ dedup via SHA-256 hash of gmail message ID
       ├─ writes to Needs_Action/
       └─ logs to Logs/gmail_watcher.log
```

---

## Security Notes

- **credentials.json** and **token.json** contain secrets.  They are
  referenced by path only — never committed to git or written into Markdown.
- Add both to `.gitignore`:
  ```
  credentials.json
  token.json
  ```
- The watcher uses **read-only** Gmail scope (`gmail.readonly`).  It cannot
  send, modify, or delete emails.
- All data stays local in the Obsidian vault.

---

## Troubleshooting

| Problem                          | Solution                                       |
| -------------------------------- | ---------------------------------------------- |
| `credentials.json not found`     | Download from Google Cloud Console, place at configured path |
| Browser doesn't open on first run| Run from a terminal with display access         |
| `Token has been expired or revoked` | Delete `token.json` and re-run `--once`      |
| `HttpError 403: insufficient permissions` | Re-check scopes; delete token and re-auth |
| No messages returned             | Adjust `query` in config.yaml                  |
| Duplicate notes appearing        | Check that the `id:` field in frontmatter is intact |
