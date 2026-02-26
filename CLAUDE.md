# Personal AI Employee — Claude Code Project Config

This is an Obsidian vault that serves as the memory and task queue for a
Personal AI Employee system.  Claude Code is the "brain" that reads tasks,
processes them, and writes results.

## Vault Layout

```
D:/Hackathon-0/
  Dashboard.md          # Live status summary — agent updates this
  Company_Handbook.md   # Operating rules — agent MUST obey, NEVER modify
  config.yaml           # Central configuration
  Inbox/                # Raw incoming items (auto-created by watchers)
  Needs_Action/         # Items awaiting human decision or agent processing
  Done/                 # Completed / archived items
  Skills/               # Agent skill definitions (SKILL_*.md)
  Templates/            # Reusable note templates
  Logs/                 # Activity logs
  watchers/             # Python watcher scripts
```

## Permissions (from Company_Handbook)

**Autonomous (no approval needed):**
- Read any file in the vault
- Summarize documents
- Create notes in Inbox/
- Move items from Inbox/ or Needs_Action/ to Done/
- Update Dashboard.md with summaries and counts
- Write to Logs/

**Requires human approval:**
- Send any message externally
- Spend money or authorize payments
- Delete any file
- Modify Company_Handbook.md
- Access new external services

## How to Process Items

1. Read all `.md` files in `Needs_Action/`.
2. Parse YAML frontmatter for priority, source, status.
3. Summarize each item (title + priority + one-line summary).
4. Update `Dashboard.md` with current counts and summaries.
5. Items marked `status: done` can be moved to `Done/`.
6. Log every action to `Logs/`.

## File Conventions

- Filenames: `YYYYMMDD_HHMM_Short_Title.md`
- Frontmatter fields: `id`, `source`, `priority`, `created`, `status`, `tags`
- Priority values: `critical`, `high`, `medium`, `low`
- Status values: `open`, `in_progress`, `done`

## Skills

Read `Skills/SKILL_*.md` for available agent skills.  Each skill has a
trigger, steps, and acceptance criteria.

## Security

- Never write secrets (passwords, API keys, tokens) into markdown files.
- `credentials.json` and `token.json` are in `.gitignore`.
- All data stays local — no external transmission without approval.
