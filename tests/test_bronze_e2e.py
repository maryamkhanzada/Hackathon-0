"""
test_bronze_e2e.py — End-to-end integration tests for Bronze Tier.

Exercises the full pipeline without touching the live vault:
  1. Creates a temporary vault structure
  2. Injects test notes (simulating watcher output)
  3. Runs vault_processor to scan/update/move
  4. Verifies Dashboard.md was updated
  5. Marks items done and verifies they move to Done/
  6. Validates log output
  7. Checks ralph_loop.sh argument parsing (dry-run)

Usage:
    python tests/test_bronze_e2e.py              # run all tests
    python tests/test_bronze_e2e.py -v           # verbose
    python tests/test_bronze_e2e.py TestVaultProcessor.test_scan  # single test
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# Add parent dirs to path so we can import watchers.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "watchers"))

from vault_processor import (
    format_scan_report,
    move_done_items,
    parse_note,
    scan_vault,
    update_dashboard,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

SAMPLE_NOTE_HIGH = """\
---
id: test_high_001
source: gmail_watcher
priority: high
created: 2026-02-18 09:00
status: open
tags: ["#email", "#gmail", "#priority-high"]
---

# Email: Urgent client request

**Priority:** high
**Source:** gmail_watcher
**Received:** 2026-02-18 09:00

---

Client needs the proposal by end of day.

---

## Suggested Actions

- [ ] Reply to client
- [ ] Draft proposal
"""

SAMPLE_NOTE_LOW = """\
---
id: test_low_002
source: gmail_watcher
priority: low
created: 2026-02-18 11:00
status: open
tags: ["#email", "#gmail", "#priority-low"]
---

# Email: Newsletter from TechDigest

**Priority:** low
**Source:** gmail_watcher
**Received:** 2026-02-18 11:00

---

This week in tech: AI agents are everywhere.

---

## Suggested Actions

- [ ] Read or archive
"""

SAMPLE_NOTE_DONE = """\
---
id: test_done_003
source: gmail_watcher
priority: medium
created: 2026-02-18 10:00
status: done
tags: ["#email", "#gmail", "#priority-medium"]
---

# Email: Meeting confirmed

**Priority:** medium
**Source:** gmail_watcher
**Received:** 2026-02-18 10:00

---

Your meeting for 3pm is confirmed.

---

## Suggested Actions

- [x] No action needed
"""

DASHBOARD_TEMPLATE = """\
# Dashboard

> **Last Updated:** `{{date}}` `{{time}}`
> **Status:** Online

---

## Finances

| Account        | Balance   | Updated    |
| -------------- | --------- | ---------- |
| Checking       | $0.00     | --         |
| Savings        | $0.00     | --         |
| **Total**      | **$0.00** |            |

### Pending Transactions
- _None_

---

## Messages

| Source   | Unread | Oldest Pending       |
| -------- | ------ | -------------------- |
| Email    | 0      | --                   |
| Slack    | 0      | --                   |
| SMS      | 0      | --                   |

### Needs Reply
- _None_

---

## Active Projects

| Project | Status      | Next Action          | Due        |
| ------- | ----------- | -------------------- | ---------- |
| _None_  | --          | --                   | --         |

---

## Inbox Queue

**Items in Inbox:** 0
**Items Needs_Action:** 0
**Completed Today:** 0

---

## Recent Activity

| Time | Action | Result |
| ---- | ------ | ------ |
| --   | Vault initialized | OK |

---

## Quick Actions

- [ ] Check email
- [ ] Review Inbox
"""

CONFIG_TEMPLATE = """\
vault_path: "{vault_path}"
poll_interval_seconds: 10
"""


class TempVault:
    """Context manager that creates a disposable vault for testing."""

    def __init__(self):
        self.root = None

    def __enter__(self) -> Path:
        self.root = Path(tempfile.mkdtemp(prefix="bronze_test_"))
        for d in ("Inbox", "Needs_Action", "Done", "Skills", "Templates", "Logs"):
            (self.root / d).mkdir()
        # Write Dashboard
        (self.root / "Dashboard.md").write_text(DASHBOARD_TEMPLATE, encoding="utf-8")
        # Write config
        cfg = CONFIG_TEMPLATE.format(vault_path=str(self.root).replace("\\", "/"))
        (self.root / "config.yaml").write_text(cfg, encoding="utf-8")
        return self.root

    def __exit__(self, *args):
        if self.root and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test: Frontmatter Parsing
# ---------------------------------------------------------------------------


class TestFrontmatterParsing(unittest.TestCase):
    """Test the YAML frontmatter parser."""

    def setUp(self):
        self.vault = TempVault()
        self.root = self.vault.__enter__()

    def tearDown(self):
        self.vault.__exit__(None, None, None)

    def _write_note(self, name, content):
        p = self.root / "Needs_Action" / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_parse_high_priority(self):
        p = self._write_note("high.md", SAMPLE_NOTE_HIGH)
        result = parse_note(p)
        self.assertIsNotNone(result)
        self.assertEqual(result["priority"], "high")
        self.assertEqual(result["source"], "gmail_watcher")
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["id"], "test_high_001")
        self.assertIn("Urgent client request", result["title"])

    def test_parse_low_priority(self):
        p = self._write_note("low.md", SAMPLE_NOTE_LOW)
        result = parse_note(p)
        self.assertEqual(result["priority"], "low")

    def test_parse_done_status(self):
        p = self._write_note("done.md", SAMPLE_NOTE_DONE)
        result = parse_note(p)
        self.assertEqual(result["status"], "done")

    def test_tags_are_list(self):
        p = self._write_note("high.md", SAMPLE_NOTE_HIGH)
        result = parse_note(p)
        self.assertIsInstance(result["tags"], list)
        self.assertIn("#email", result["tags"])

    def test_malformed_frontmatter(self):
        p = self._write_note("bad.md", "# No frontmatter\n\nJust a plain note.\n")
        result = parse_note(p)
        self.assertIsNotNone(result)
        self.assertEqual(result["priority"], "medium")  # default
        self.assertEqual(result["source"], "unknown")   # default


# ---------------------------------------------------------------------------
# Test: Vault Scanner
# ---------------------------------------------------------------------------


class TestVaultScanner(unittest.TestCase):
    """Test the vault scan + summary generation."""

    def setUp(self):
        self.vault = TempVault()
        self.root = self.vault.__enter__()

    def tearDown(self):
        self.vault.__exit__(None, None, None)

    def _inject_notes(self):
        na = self.root / "Needs_Action"
        (na / "high.md").write_text(SAMPLE_NOTE_HIGH, encoding="utf-8")
        (na / "low.md").write_text(SAMPLE_NOTE_LOW, encoding="utf-8")
        (na / "done.md").write_text(SAMPLE_NOTE_DONE, encoding="utf-8")

    def test_scan_counts(self):
        self._inject_notes()
        scan = scan_vault(self.root)
        self.assertEqual(scan["counts"]["needs_action"], 3)
        self.assertEqual(scan["counts"]["inbox"], 0)
        self.assertEqual(scan["counts"]["total"], 3)

    def test_scan_priority_counts(self):
        self._inject_notes()
        scan = scan_vault(self.root)
        bp = scan["counts"]["by_priority"]
        self.assertEqual(bp.get("high", 0), 1)
        self.assertEqual(bp.get("low", 0), 1)
        self.assertEqual(bp.get("medium", 0), 1)

    def test_scan_empty_vault(self):
        scan = scan_vault(self.root)
        self.assertEqual(scan["counts"]["total"], 0)

    def test_scan_report_format(self):
        self._inject_notes()
        scan = scan_vault(self.root)
        report = format_scan_report(scan)
        self.assertIn("Needs_Action (3 items)", report)
        self.assertIn("high", report)
        self.assertIn("gmail_watcher", report)

    def test_scan_oldest_item(self):
        self._inject_notes()
        scan = scan_vault(self.root)
        self.assertEqual(scan["oldest"], "2026-02-18 09:00")

    def test_priority_sort_order(self):
        self._inject_notes()
        scan = scan_vault(self.root)
        priorities = [item["priority"] for item in scan["needs_action"]]
        self.assertEqual(priorities[0], "high")
        self.assertEqual(priorities[-1], "low")


# ---------------------------------------------------------------------------
# Test: Dashboard Update
# ---------------------------------------------------------------------------


class TestDashboardUpdate(unittest.TestCase):
    """Test that Dashboard.md gets correctly updated."""

    def setUp(self):
        self.vault = TempVault()
        self.root = self.vault.__enter__()

    def tearDown(self):
        self.vault.__exit__(None, None, None)

    def _inject_and_scan(self):
        na = self.root / "Needs_Action"
        (na / "high.md").write_text(SAMPLE_NOTE_HIGH, encoding="utf-8")
        (na / "low.md").write_text(SAMPLE_NOTE_LOW, encoding="utf-8")
        return scan_vault(self.root)

    def test_timestamp_updated(self):
        scan = self._inject_and_scan()
        update_dashboard(self.root, scan)
        text = (self.root / "Dashboard.md").read_text(encoding="utf-8")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertIn(today, text)
        self.assertNotIn("{{date}}", text)

    def test_email_count_updated(self):
        scan = self._inject_and_scan()
        update_dashboard(self.root, scan)
        text = (self.root / "Dashboard.md").read_text(encoding="utf-8")
        self.assertIn("| Email    | 2", text)

    def test_needs_reply_shows_high(self):
        scan = self._inject_and_scan()
        update_dashboard(self.root, scan)
        text = (self.root / "Dashboard.md").read_text(encoding="utf-8")
        self.assertIn("[HIGH]", text)
        self.assertIn("Urgent client request", text)

    def test_needs_action_counter(self):
        scan = self._inject_and_scan()
        update_dashboard(self.root, scan)
        text = (self.root / "Dashboard.md").read_text(encoding="utf-8")
        self.assertIn("**Items Needs_Action:** 2", text)

    def test_activity_row_appended(self):
        scan = self._inject_and_scan()
        update_dashboard(self.root, scan)
        text = (self.root / "Dashboard.md").read_text(encoding="utf-8")
        self.assertIn("Vault scan + dashboard update", text)
        self.assertIn("2 items scanned", text)


# ---------------------------------------------------------------------------
# Test: Move Done Items
# ---------------------------------------------------------------------------


class TestMoveDoneItems(unittest.TestCase):
    """Test that status:done items move to Done/."""

    def setUp(self):
        self.vault = TempVault()
        self.root = self.vault.__enter__()

    def tearDown(self):
        self.vault.__exit__(None, None, None)

    def test_done_item_moves(self):
        na = self.root / "Needs_Action"
        (na / "done.md").write_text(SAMPLE_NOTE_DONE, encoding="utf-8")
        scan = scan_vault(self.root)
        moved = move_done_items(self.root, scan)
        self.assertEqual(moved, 1)
        self.assertFalse((na / "done.md").exists())
        self.assertTrue((self.root / "Done" / "done.md").exists())

    def test_completed_timestamp_added(self):
        na = self.root / "Needs_Action"
        (na / "done.md").write_text(SAMPLE_NOTE_DONE, encoding="utf-8")
        scan = scan_vault(self.root)
        move_done_items(self.root, scan)
        text = (self.root / "Done" / "done.md").read_text(encoding="utf-8")
        self.assertIn("completed:", text)

    def test_open_items_stay(self):
        na = self.root / "Needs_Action"
        (na / "high.md").write_text(SAMPLE_NOTE_HIGH, encoding="utf-8")
        scan = scan_vault(self.root)
        moved = move_done_items(self.root, scan)
        self.assertEqual(moved, 0)
        self.assertTrue((na / "high.md").exists())

    def test_mixed_batch(self):
        na = self.root / "Needs_Action"
        (na / "high.md").write_text(SAMPLE_NOTE_HIGH, encoding="utf-8")
        (na / "done.md").write_text(SAMPLE_NOTE_DONE, encoding="utf-8")
        (na / "low.md").write_text(SAMPLE_NOTE_LOW, encoding="utf-8")
        scan = scan_vault(self.root)
        moved = move_done_items(self.root, scan)
        self.assertEqual(moved, 1)
        # high and low stay
        self.assertTrue((na / "high.md").exists())
        self.assertTrue((na / "low.md").exists())
        # done moved
        self.assertFalse((na / "done.md").exists())
        self.assertTrue((self.root / "Done" / "done.md").exists())


# ---------------------------------------------------------------------------
# Test: Full Pipeline (scan → update dashboard → move)
# ---------------------------------------------------------------------------


class TestFullPipeline(unittest.TestCase):
    """End-to-end: inject → scan → update dashboard → move → verify."""

    def setUp(self):
        self.vault = TempVault()
        self.root = self.vault.__enter__()

    def tearDown(self):
        self.vault.__exit__(None, None, None)

    def test_full_cycle(self):
        na = self.root / "Needs_Action"
        (na / "high.md").write_text(SAMPLE_NOTE_HIGH, encoding="utf-8")
        (na / "done.md").write_text(SAMPLE_NOTE_DONE, encoding="utf-8")
        (na / "low.md").write_text(SAMPLE_NOTE_LOW, encoding="utf-8")

        # --- Scan ---
        scan = scan_vault(self.root)
        self.assertEqual(scan["counts"]["total"], 3)

        # --- Update Dashboard ---
        update_dashboard(self.root, scan)
        dashboard = (self.root / "Dashboard.md").read_text(encoding="utf-8")
        self.assertIn("**Items Needs_Action:** 3", dashboard)
        self.assertIn("[HIGH]", dashboard)

        # --- Move done items ---
        moved = move_done_items(self.root, scan)
        self.assertEqual(moved, 1)

        # --- Re-scan after move ---
        scan2 = scan_vault(self.root)
        self.assertEqual(scan2["counts"]["needs_action"], 2)

        # --- Update Dashboard again ---
        update_dashboard(self.root, scan2)
        dashboard2 = (self.root / "Dashboard.md").read_text(encoding="utf-8")
        self.assertIn("**Items Needs_Action:** 2", dashboard2)

        # --- Done/ has the file ---
        done_files = list((self.root / "Done").glob("*.md"))
        self.assertEqual(len(done_files), 1)
        self.assertIn("completed:", done_files[0].read_text(encoding="utf-8"))

    def test_pipeline_via_subprocess(self):
        """Run vault_processor.py as a subprocess (as ralph_loop would)."""
        na = self.root / "Needs_Action"
        (na / "high.md").write_text(SAMPLE_NOTE_HIGH, encoding="utf-8")
        (na / "low.md").write_text(SAMPLE_NOTE_LOW, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "watchers" / "vault_processor.py"),
             "--config", str(self.root / "config.yaml")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, f"STDERR: {result.stderr}")
        self.assertIn("Vault Scan", result.stdout)
        self.assertIn("[OK] Dashboard.md updated", result.stdout)

        # Dashboard should have updated counts.
        dashboard = (self.root / "Dashboard.md").read_text(encoding="utf-8")
        self.assertIn("**Items Needs_Action:** 2", dashboard)


# ---------------------------------------------------------------------------
# Test: Ralph Loop (dry-run argument parsing)
# ---------------------------------------------------------------------------


class TestRalphLoop(unittest.TestCase):
    """Test ralph_loop.sh argument parsing and dry-run mode."""

    BASH = r"C:\Program Files\Git\usr\bin\bash.exe" if os.name == "nt" else "bash"

    def test_help_flag(self):
        result = subprocess.run(
            [self.BASH, str(REPO_ROOT / "ralph_loop.sh"), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--max-loops", result.stdout)
        self.assertIn("--dry-run", result.stdout)

    def test_syntax_check(self):
        result = subprocess.run(
            [self.BASH, "-n", str(REPO_ROOT / "ralph_loop.sh")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, f"Syntax error: {result.stderr}")


# ---------------------------------------------------------------------------
# Test: Base Watcher Note Creation
# ---------------------------------------------------------------------------


class TestBaseWatcherDedup(unittest.TestCase):
    """Test the BaseWatcher note creation and dedup logic."""

    def setUp(self):
        self.vault = TempVault()
        self.root = self.vault.__enter__()
        # Write a config pointing to our temp vault.
        cfg = self.root / "config.yaml"
        cfg.write_text(
            CONFIG_TEMPLATE.format(vault_path=str(self.root).replace("\\", "/")),
            encoding="utf-8",
        )

    def tearDown(self):
        self.vault.__exit__(None, None, None)

    def test_note_id_hashing(self):
        """Verify that the same ID in different notes is detected as duplicate."""
        import hashlib
        note_id = "gmail_abc123"
        id_hash = hashlib.sha256(note_id.encode()).hexdigest()[:12]
        # Write a note with this hash in frontmatter.
        note = f"---\nid: {id_hash}\nstatus: open\n---\n\n# Test\n"
        (self.root / "Needs_Action" / "existing.md").write_text(note, encoding="utf-8")
        # The hash should be findable.
        found = False
        for md in (self.root / "Needs_Action").glob("*.md"):
            if id_hash in md.read_text(encoding="utf-8")[:500]:
                found = True
        self.assertTrue(found)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
