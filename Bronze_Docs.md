# Bronze Tier — Architecture & Documentation

> Personal AI Employee — Bronze Tier Complete
> Built: 2026-02-18

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     EXTERNAL WORLD                               │
│                   (Gmail, APIs, etc.)                             │
└──────────┬───────────────────────────────────────────────────────┘
           │
           v
┌──────────────────────┐
│   Python Watchers    │   gmail_watcher.py (polls Gmail API)
│   (base_watcher.py)  │   future: slack_watcher, bank_watcher...
└──────────┬───────────┘
           │ writes .md notes
           v
┌──────────────────────────────────────────────────────────────────┐
│                     OBSIDIAN VAULT                                │
│                                                                   │
│  ┌───────────┐   ┌───────────────┐   ┌──────────┐               │
│  │  Inbox/   │──>│ Needs_Action/ │──>│  Done/   │               │
│  │  (raw)    │   │  (pending)    │   │ (archive)│               │
│  └───────────┘   └───────┬───────┘   └──────────┘               │
│                          │                                        │
│  ┌───────────────────────┼──────────────────────────────┐        │
│  │            Dashboard.md (live summary)                │        │
│  │  Company_Handbook.md (rules)    config.yaml (config) │        │
│  └──────────────────────────────────────────────────────┘        │
│                          │                                        │
│  Skills/                 │         Logs/                          │
│    SKILL_vault_setup     │           activity_YYYY-MM-DD.log     │
│    SKILL_vault_read      │           ralph_loop_*.log            │
│    SKILL_vault_write     │           gmail_watcher.log           │
│    SKILL_gmail_watcher   │                                        │
│    SKILL_ralph_loop      │                                        │
│    SKILL_bronze_test     │                                        │
└──────────────────────────┼───────────────────────────────────────┘
                           │
                           v
┌──────────────────────────────────────┐
│          CLAUDE CODE (Brain)          │
│                                       │
│  Reads Needs_Action/ via Glob/Read   │
│  Triages items per Company_Handbook  │
│  Updates Dashboard.md via Edit       │
│  Moves done items to Done/           │
│  Logs actions to Logs/               │
└──────────────────┬───────────────────┘
                   │
                   v
┌──────────────────────────────────────┐
│     ralph_loop.sh (Persistence)       │
│                                       │
│  Re-invokes Claude until done        │
│  Checks <promise>TASK_COMPLETE       │
│  Max-loop safety cap                 │
│  Independent vault state verify      │
└──────────────────────────────────────┘
```

---

## 2. Component Inventory

### Core Files

| File | Lines | Purpose |
|------|-------|---------|
| `CLAUDE.md` | 65 | Project config — tells Claude Code how to work with the vault |
| `Company_Handbook.md` | 107 | Operating rules, approval thresholds, escalation policy |
| `Dashboard.md` | 70 | Live status summary — updated by vault_processor |
| `config.yaml` | 19 | Central configuration (paths, poll intervals) |
| `setup.sh` | 127 | Idempotent vault bootstrap script |

### Python Watchers

| File | Lines | Purpose |
|------|-------|---------|
| `watchers/base_watcher.py` | 212 | Abstract base — poll loop, dedup, note creation, logging |
| `watchers/gmail_watcher.py` | 294 | Gmail API poller with OAuth, priority classification |
| `watchers/vault_processor.py` | 369 | Read/summarize/write orchestrator — updates Dashboard, moves Done |

### Persistence Loop

| File | Lines | Purpose |
|------|-------|---------|
| `ralph_loop.sh` | 310 | Ralph Wiggum loop — re-invokes Claude until promise fulfilled |
| `Templates/ralph_prompt.md` | 78 | Prompt template injected each iteration |

### Agent Skills

| File | Lines | Purpose |
|------|-------|---------|
| `Skills/SKILL_vault_setup.md` | 105 | Bootstraps vault structure from scratch |
| `Skills/SKILL_vault_read.md` | 125 | Scan folders, parse frontmatter, build summaries |
| `Skills/SKILL_vault_write.md` | 204 | Update Dashboard, move done items, log activity |
| `Skills/SKILL_gmail_watcher.md` | 200 | Gmail OAuth setup and watcher usage |
| `Skills/SKILL_ralph_loop.md` | 234 | Ralph Wiggum persistence pattern |
| `Skills/SKILL_bronze_test.md` | — | End-to-end testing skill |

### Tests

| File | Lines | Tests | Purpose |
|------|-------|-------|---------|
| `tests/test_bronze_e2e.py` | ~580 | 25 | Full integration test suite |

---

## 3. Data Flow

### Normal Processing Cycle

```
1. INGEST    gmail_watcher.py polls Gmail API
             Creates .md note in Needs_Action/ with YAML frontmatter
             (id, source, priority, status:open, tags)

2. SCAN      vault_processor.py reads all .md in Needs_Action/ + Inbox/
             Parses frontmatter, sorts by priority
             Produces structured scan report

3. UPDATE    vault_processor.py edits Dashboard.md:
             - Timestamp
             - Message counts (from #email tags)
             - Needs Reply (high/critical items)
             - Inbox Queue counters
             - Recent Activity table row

4. MOVE      Items with status:done get:
             - completed: timestamp added to frontmatter
             - File moved from Needs_Action/ to Done/

5. LOG       Every action appended to Logs/activity_YYYY-MM-DD.log
```

### Ralph Wiggum Loop Cycle

```
1. INIT      ralph_loop.sh counts Needs_Action/*.md
2. PROMPT    Builds prompt from Templates/ralph_prompt.md
3. INVOKE    claude --print --dangerously-skip-permissions "$prompt"
4. CHECK     grep output for <promise>TASK_COMPLETE</promise>
5. VERIFY    Re-count Needs_Action/*.md independently
6. DECIDE    Promise found? -> exit 0.  No? -> cooldown -> goto 2
7. SAFETY    Max iterations reached? -> exit 1
```

### Frontmatter Contract

Every vault note follows this schema:

```yaml
---
id: <12-char-sha256-hash>          # Unique, used for dedup
source: <watcher_name>              # gmail_watcher, manual, etc.
priority: critical|high|medium|low  # Triage level
created: YYYY-MM-DD HH:MM          # When the note was created
status: open|in_progress|done       # Lifecycle state
tags: ["#email", "#finance", ...]   # Obsidian-compatible tags
completed: YYYY-MM-DD HH:MM        # Added when moved to Done/
---
```

---

## 4. Test Results

```
25 tests, 25 passed, 0 failed
Runtime: ~7 seconds
```

### Test Coverage

| Test Class | Tests | What It Covers |
|-----------|-------|----------------|
| TestFrontmatterParsing | 5 | YAML parsing, priority/status/tags extraction, malformed input |
| TestVaultScanner | 6 | Folder scanning, counts, priority sort, empty vault, report format |
| TestDashboardUpdate | 5 | Timestamp, email count, needs reply, counters, activity row |
| TestMoveDoneItems | 4 | Move done, completed timestamp, open stays, mixed batch |
| TestFullPipeline | 2 | Full cycle (inject->scan->update->move->verify), subprocess mode |
| TestRalphLoop | 2 | Bash syntax check, --help flag parsing |
| TestBaseWatcherDedup | 1 | ID hashing and duplicate detection |

### What the tests prove

1. **Frontmatter parsing** handles valid YAML, quoted tags, and malformed files gracefully.
2. **Vault scanning** correctly counts items by priority, source, and status across folders.
3. **Dashboard update** performs targeted regex edits without destroying the rest of the file.
4. **Done movement** only moves `status: done` items, adds `completed:` timestamp, leaves open items untouched.
5. **Full pipeline** works as a single function call chain AND as a subprocess invocation.
6. **Ralph loop** passes bash syntax validation and argument parsing.

---

## 5. Lessons Learned

### What Worked

1. **YAML frontmatter as the data contract.** Every component reads the same schema. Watchers write it, vault_processor reads it, Claude Code edits it, the ralph loop verifies it. One format, many consumers.

2. **Obsidian as the GUI layer.** Zero frontend code. The vault IS the UI — users see Dashboard.md update in real time, browse Needs_Action/ like a task queue, and check Done/ as an archive. Dataview queries work out of the box.

3. **Dedup via content hashing.** SHA-256 of the source ID, embedded in frontmatter, checked by scanning file headers. Simple, no database required, survives renames.

4. **Targeted Dashboard edits.** Using regex replacements on specific sections instead of regenerating the whole file means users can add custom content to Dashboard.md (new sections, manual notes) and the processor won't destroy it.

5. **The Ralph Wiggum pattern.** Intercepting exit and re-injecting the prompt is crude but effective. The agent gets fresh context each iteration and the independent vault-state check prevents false completions.

### What Bit Us

1. **YAML `#` comments.** Tags like `[#email #gmail]` silently fail in YAML because `#` starts a comment. The entire frontmatter block parses as empty. **Fix:** Quote all hash-prefixed strings: `["#email", "#gmail"]`.

2. **Unicode arrows on Windows.** `print("→")` crashes on cp1252 console. Python's default stdout encoding on Windows is the system codepage, not UTF-8. **Fix:** Use ASCII alternatives (`->`) in print statements, or set `PYTHONIOENCODING=utf-8`.

3. **Bash subprocess timeout on Windows.** `subprocess.run(["bash", ...], timeout=10)` times out because Windows resolves `bash` slowly through PATH. **Fix:** Use the full Git Bash path (`C:\Program Files\Git\usr\bin\bash.exe`) and increase timeout to 30s.

4. **Dashboard activity table newline collapse.** String concatenation after `split()` dropped the newline between the table header and separator rows, producing `| Time | Action | Result || ---- |`. **Fix:** Explicitly prepend `\n` to the reassembled block.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Local-first (no cloud DB) | Privacy, speed, works offline, Obsidian renders it |
| File-based task queue | No message broker needed; filesystem IS the queue |
| Status in frontmatter | Machine-readable, grep-able, Obsidian Dataview compatible |
| Read-only Gmail scope | Minimum viable permission — watcher can never send/delete |
| Max-loop safety cap | Prevents runaway API costs if agent gets confused |
| Promise-based completion | Agent self-reports, loop independently verifies |

---

## 6. Known Limitations (Bronze Tier)

| Limitation | Impact | Silver Tier Fix |
|-----------|--------|-----------------|
| Gmail only | No Slack, calendar, bank watchers | Add more watcher subclasses |
| No real-time push | Polling every 120s | Use Gmail push notifications / webhooks |
| Manual triage | Claude suggests actions but doesn't auto-execute | MCP tool use for sending replies |
| No encryption | Vault data is plaintext on disk | Add age/gpg encryption for sensitive notes |
| Single-user | One vault, one agent | Multi-vault orchestration |
| No MCP servers | Claude uses file I/O only | MCP servers for Gmail, calendar, finance APIs |

---

## 7. File Tree (Final Bronze State)

```
D:/Hackathon-0/
  CLAUDE.md                          # Project config for Claude Code
  Company_Handbook.md                # Agent operating rules
  Dashboard.md                       # Live status (auto-updated)
  Bronze_Docs.md                     # This file
  config.yaml                        # Central config
  ralph_loop.sh                      # Persistence loop
  setup.sh                           # Vault bootstrap
  requirements.txt                   # Python deps
  .gitignore                         # Protects secrets

  Inbox/                             # Raw incoming items
  Needs_Action/                      # Pending items (task queue)
    20260218_0900_Email_*.md          # Test data
    20260218_1015_Email_*.md
    20260218_1130_Email_*.md
  Done/                              # Archive

  Skills/                            # Agent skill definitions
    SKILL_vault_setup.md
    SKILL_vault_read.md
    SKILL_vault_write.md
    SKILL_gmail_watcher.md
    SKILL_ralph_loop.md
    SKILL_bronze_test.md

  Templates/
    ralph_prompt.md                  # Loop prompt template

  watchers/
    base_watcher.py                  # Abstract watcher base class
    gmail_watcher.py                 # Gmail API watcher
    vault_processor.py               # Read/write orchestrator

  tests/
    test_bronze_e2e.py               # 25 integration tests

  Logs/
    activity_YYYY-MM-DD.log          # Daily activity log
    ralph_loop_*.log                 # Per-run loop transcripts
    gmail_watcher.log                # Watcher log
```

---

## 8. How to Run the Full Bronze Demo

```bash
# Step 0: Install dependencies
pip install -r requirements.txt

# Step 1: Bootstrap vault (idempotent)
bash setup.sh

# Step 2: Run tests (25 tests, all should pass)
python -m unittest tests.test_bronze_e2e -v

# Step 3: Simulate watcher (inject test data already present)
python watchers/vault_processor.py --scan-only

# Step 4: Process + update dashboard
python watchers/vault_processor.py

# Step 5: Ralph loop dry run
bash ralph_loop.sh --dry-run --max-loops 2

# Step 6: Check results
cat Dashboard.md
ls Needs_Action/
ls Done/
cat Logs/activity_$(date +%Y-%m-%d).log

# Step 7 (optional): Live Gmail watcher (needs credentials.json)
python watchers/gmail_watcher.py --once
```

---

## 9. Next Steps (Silver Tier)

- [ ] Add MCP servers for Gmail send, Calendar read/write, Finance APIs
- [ ] Implement Slack watcher (`watchers/slack_watcher.py`)
- [ ] Add Calendar watcher for meeting conflicts
- [ ] Build approval UI: Obsidian plugin or web dashboard for human-in-the-loop
- [ ] Encrypted notes for sensitive data (API keys, financial info)
- [ ] Multi-vault support for work/personal separation
- [ ] Scheduled ralph_loop via cron/Task Scheduler
- [ ] Metrics collection (items processed/day, avg triage time)
