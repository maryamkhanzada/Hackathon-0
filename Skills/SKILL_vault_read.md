# SKILL: Vault Read

> **Trigger:** On demand, or as first step of any processing cycle
> **Purpose:** Scan vault folders and return structured summaries of all items
> **Tier:** Bronze

---

## Description

Reads all Markdown notes from one or more vault folders, parses their YAML
frontmatter and content, and produces a structured summary.  This is the
"eyes" of the AI Employee — every processing cycle starts here.

---

## Steps for Claude Code

### Step 1 — List target folder

Use the Glob tool to find all `.md` files in the target folder:

```
Glob: Needs_Action/*.md
Glob: Inbox/*.md
```

### Step 2 — Read each file

For every file returned, use the Read tool to get its full content.

### Step 3 — Parse frontmatter

Extract YAML frontmatter (between `---` markers) from each file.  The
standard fields are:

| Field      | Type   | Example                    |
| ---------- | ------ | -------------------------- |
| `id`       | string | `a1b2c3d4e5f6`             |
| `source`   | string | `gmail_watcher`            |
| `priority` | string | `high`                     |
| `created`  | string | `2026-02-18 09:00`         |
| `status`   | string | `open`, `in_progress`, `done` |
| `tags`     | list   | `[#email, #priority-high]` |

### Step 4 — Build summary

For each note, produce a one-line summary:

```
[PRIORITY] Title (source, created) — first sentence of body
```

Example:
```
[HIGH] Email: Quarterly Report Due Friday (gmail_watcher, 2026-02-18 09:00) — Q4 report due this Friday, department numbers needed by Thursday.
[MEDIUM] Email: Invoice #4821 from CloudHost (gmail_watcher, 2026-02-18 10:15) — February hosting invoice for $249.00, due March 1.
[LOW] Email: Team Lunch Poll (gmail_watcher, 2026-02-18 11:30) — Vote for Friday lunch spot by Wednesday.
```

### Step 5 — Aggregate counts

Compute:
- Total items per folder
- Count by priority (critical / high / medium / low)
- Count by status (open / in_progress / done)
- Count by source

---

## Output Format

Return a structured report like:

```markdown
## Vault Scan — 2026-02-18 14:30

### Needs_Action (3 items)
| # | Priority | Title | Source | Created |
|---|----------|-------|--------|---------|
| 1 | high     | Quarterly Report Due Friday | gmail_watcher | 2026-02-18 09:00 |
| 2 | medium   | Invoice #4821 from CloudHost | gmail_watcher | 2026-02-18 10:15 |
| 3 | low      | Team Lunch Poll | gmail_watcher | 2026-02-18 11:30 |

### Summary
- High: 1 | Medium: 1 | Low: 1
- All sources: gmail_watcher
- Oldest item: 2026-02-18 09:00
```

---

## Example Prompt for Claude Code

Paste this into Claude Code to execute the skill:

```
Read all .md files in D:/Hackathon-0/Needs_Action/ and D:/Hackathon-0/Inbox/.
For each file, parse the YAML frontmatter and extract: id, priority, source,
created, status, and the note title (first # heading).
Produce a summary table sorted by priority (critical > high > medium > low).
Include aggregate counts at the bottom.
```

---

## Acceptance Criteria

- [ ] All `.md` files in target folders are read (none skipped)
- [ ] Frontmatter is correctly parsed
- [ ] Output is valid Markdown renderable in Obsidian
- [ ] Items are sorted by priority descending
- [ ] Counts match actual file count in each folder

---

## Integration

This skill's output feeds directly into **SKILL_vault_write** which uses
the summary to update Dashboard.md.

```
SKILL_vault_read  ──→  structured summary  ──→  SKILL_vault_write
     (read)                                          (write)
```
