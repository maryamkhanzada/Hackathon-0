# SKILL: Vault Write

> **Trigger:** After SKILL_vault_read completes, or on demand
> **Purpose:** Update Dashboard.md with live data and move completed items to Done/
> **Tier:** Bronze

---

## Description

Takes the structured summary produced by SKILL_vault_read (or an equivalent
scan) and performs two write operations:

1. **Update Dashboard.md** — replace dynamic sections with current counts,
   item summaries, and activity log entries.
2. **Move to Done/** — relocate notes whose `status` frontmatter is `done`.

This is the "hands" of the AI Employee — it writes results back into the vault.

---

## Steps for Claude Code

### Step 1 — Read current Dashboard.md

```
Read D:/Hackathon-0/Dashboard.md
```

### Step 2 — Update the Messages section

Replace the Messages table rows with actual counts from the scan.  For
example, if SKILL_vault_read found 2 emails with `#email` tag:

```markdown
| Source   | Unread | Oldest Pending       |
| -------- | ------ | -------------------- |
| Email    | 2      | 2026-02-18 09:00     |
| Slack    | 0      | --                   |
| SMS      | 0      | --                   |
```

### Step 3 — Update the Needs Reply subsection

Replace `### Needs Reply` content with high-priority items:

```markdown
### Needs Reply
- **[HIGH]** Quarterly Report Due Friday — Reply to Sarah Chen
```

### Step 4 — Update the Inbox Queue counters

Replace the counters with live values:

```markdown
**Items in Inbox:** 0
**Items Needs_Action:** 3
**Completed Today:** 0
```

### Step 5 — Append to Recent Activity

Add a new row to the Recent Activity table (keep last 10 entries):

```markdown
| 14:30 | Vault scan + dashboard update | 3 items processed |
```

### Step 6 — Update the timestamp

Replace the `Last Updated` line:

```markdown
> **Last Updated:** 2026-02-18 14:30
```

### Step 7 — Write Dashboard.md

Use the Edit tool to apply each section change.  Do NOT overwrite the entire
file — use targeted edits to preserve any manual additions the user made.

### Step 8 — Move completed items to Done/

For any note in `Needs_Action/` or `Inbox/` where the frontmatter has
`status: done`:

1. Read the file.
2. Update frontmatter: set `completed: YYYY-MM-DD HH:MM`.
3. Move (rename) the file from its current folder to `Done/`.
4. Log the move.

**Claude Code commands:**
```
Read the file
Edit: change "status: open" to "status: done" and add "completed:" field
Bash: mv "Needs_Action/filename.md" "Done/filename.md"
```

### Step 9 — Log the activity

Append to today's activity log in `Logs/`:

```
14:30:00 | vault_write | DASHBOARD_UPDATE | items=3 moved=0
```

---

## Output Format

After execution, Dashboard.md should reflect live data.  Example result:

```markdown
# Dashboard

> **Last Updated:** 2026-02-18 14:30
> **Status:** Online

---

## Messages

| Source   | Unread | Oldest Pending   |
| -------- | ------ | ---------------- |
| Email    | 3      | 2026-02-18 09:00 |
| Slack    | 0      | --               |
| SMS      | 0      | --               |

### Needs Reply
- **[HIGH]** Quarterly Report Due Friday — Reply to Sarah Chen by Thursday EOD

...

**Items in Inbox:** 0
**Items Needs_Action:** 3
**Completed Today:** 0

## Recent Activity

| Time  | Action                        | Result             |
| ----- | ----------------------------- | ------------------ |
| 14:30 | Vault scan + dashboard update | 3 items processed  |
| --    | Vault initialized             | OK                 |
```

---

## Example Prompt for Claude Code

Paste this into Claude Code to execute the full read→write cycle:

```
Execute the vault read/write cycle:

1. Glob all .md files in D:/Hackathon-0/Needs_Action/ and D:/Hackathon-0/Inbox/
2. Read each file and parse the YAML frontmatter (id, priority, source, created, status)
3. Count items by priority and source
4. Read D:/Hackathon-0/Dashboard.md
5. Edit Dashboard.md:
   - Update "Last Updated" to current time
   - Update Messages table with email count from scan
   - Update "Needs Reply" with high-priority items
   - Update Inbox Queue counters
   - Add a row to Recent Activity
6. If any items have status: done, move them to D:/Hackathon-0/Done/
7. Append a summary line to today's log in D:/Hackathon-0/Logs/
```

---

## Safety Rules

Per Company_Handbook.md:
- **NEVER** modify `Company_Handbook.md`
- **NEVER** delete files (move to Done/ instead)
- **NEVER** send external messages without human approval
- Use the Edit tool for targeted changes — do not blindly overwrite files
- Log every write action

---

## Acceptance Criteria

- [ ] Dashboard.md timestamp is updated to current time
- [ ] Message counts match actual files in Needs_Action/ and Inbox/
- [ ] High-priority items appear in "Needs Reply"
- [ ] Inbox Queue counters are accurate
- [ ] Recent Activity has a new entry
- [ ] Items with `status: done` are moved to Done/
- [ ] Activity is logged to Logs/

---

## Integration

```
SKILL_vault_read  ──→  structured summary  ──→  SKILL_vault_write
     (scan)                                      (update + move)
         │                                             │
         └──── feeds ─────────────────────────→  Dashboard.md
                                                   Done/
                                                   Logs/
```
