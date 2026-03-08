# SKILL: Bronze Tier End-to-End Test

> **Trigger:** After any code change, or before a demo
> **Purpose:** Verify the entire Bronze Tier pipeline works correctly
> **Tier:** Bronze
> **Source file:** `tests/test_bronze_e2e.py`

---

## Description

Runs 25 automated integration tests that exercise every Bronze Tier component
without touching the live vault.  Each test creates a temporary vault, injects
test data, runs the pipeline, and verifies the result.

---

## Quick Run

```bash
cd D:/Hackathon-0
python -m unittest tests.test_bronze_e2e -v
```

Expected output: `Ran 25 tests ... OK`

---

## What the Tests Cover

| Test Group | Count | Components Tested |
|-----------|-------|-------------------|
| Frontmatter Parsing | 5 | YAML parser, priority/status/tag extraction, malformed input |
| Vault Scanner | 6 | Folder scan, item counts, priority sort, empty vault, report generation |
| Dashboard Update | 5 | Timestamp, email count, needs-reply section, queue counters, activity row |
| Move Done Items | 4 | Done movement, completed timestamp, open-items stay, mixed batches |
| Full Pipeline | 2 | End-to-end cycle as function calls AND as subprocess |
| Ralph Loop | 2 | Bash syntax validation, --help argument parsing |
| Watcher Dedup | 1 | SHA-256 ID hashing, duplicate detection |

---

## Manual Verification Steps

If you prefer a hands-on check instead of (or in addition to) automated tests:

### Step 1 — Verify vault structure

```bash
ls D:/Hackathon-0/{Inbox,Needs_Action,Done,Skills,Templates,Logs}
```

All six directories should exist.

### Step 2 — Verify test data

```bash
ls D:/Hackathon-0/Needs_Action/*.md
```

Should show 3 test email notes (high, medium, low priority).

### Step 3 — Run scan-only

```bash
python watchers/vault_processor.py --scan-only
```

Should print a table with 3 items sorted by priority (high > medium > low).

### Step 4 — Run full processor

```bash
python watchers/vault_processor.py
```

Check Dashboard.md — should show:
- Email count: 3
- Needs Reply: [HIGH] Quarterly Report Due Friday
- Items Needs_Action: 3
- New activity row

### Step 5 — Test done-item movement

Edit any Needs_Action note, change `status: open` to `status: done`, then:

```bash
python watchers/vault_processor.py
ls Done/
```

The edited file should appear in Done/ with a `completed:` timestamp.

### Step 6 — Ralph loop dry run

```bash
bash ralph_loop.sh --dry-run --max-loops 1
```

Should print the full prompt template with substituted variables, run a
scan-only pass, and output `<promise>TASK_COMPLETE</promise>`.

### Step 7 — Check logs

```bash
cat Logs/activity_$(date +%Y-%m-%d).log
```

Should show timestamped entries for every vault_processor and ralph_loop action.

---

## Acceptance Criteria

- [ ] All 25 automated tests pass
- [ ] Manual steps 1-7 complete without errors
- [ ] Dashboard.md reflects live data after processing
- [ ] Done/ receives moved items with completed timestamp
- [ ] Logs/ has entries for every action
- [ ] ralph_loop.sh dry-run completes with promise tag

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: yaml` | pyyaml not installed | `pip install pyyaml` |
| `UnicodeEncodeError: charmap` | Windows cp1252 console | Set `PYTHONIOENCODING=utf-8` |
| Bash tests timeout | Windows PATH resolution slow | Tests use full Git Bash path |
| Frontmatter not parsed | `#` in YAML tags | Quote tags: `["#email"]` |
| Dashboard sections missing | Regex didn't match | Check section headers match exactly |
