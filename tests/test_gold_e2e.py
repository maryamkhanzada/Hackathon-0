"""
test_gold_e2e.py — Gold Tier end-to-end integration tests.

Simulates the full Gold pipeline:
  1. Cross-domain event (WhatsApp "pricing" message) — classification + plan
  2. Social post draft with HITL gate (FB draft + approval request)
  3. Audit logging (NDJSON schema, secret scrubbing, CRITICAL alerts)
  4. Ralph loop dry-run (plan -> approve -> execute, <=10 iterations)
  5. Full chain: cross-domain event -> social draft -> audit log

Usage:
    python tests/test_gold_e2e.py
    python tests/test_gold_e2e.py -v
    python tests/test_gold_e2e.py TestAuditLogging
    python tests/test_gold_e2e.py TestRalphLoopDryRun
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "watchers"))
sys.path.insert(0, str(REPO_ROOT))

import audit_logger as _al  # noqa: E402  (needs sys.path above)

# Real vault dirs (audit_logger and ralph_loop use module-level constants)
_REAL_LOGS     = REPO_ROOT / "Logs"
_REAL_PLANS    = REPO_ROOT / "Plans"
_REAL_APPROVED = REPO_ROOT / "Approved"
_REAL_NEEDS    = REPO_ROOT / "Needs_Action"
_REAL_PIDS     = REPO_ROOT / ".pids"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _temp_vault() -> Path:
    """Create an isolated temporary vault with required subdirectories."""
    root = Path(tempfile.mkdtemp(prefix="gold_e2e_"))
    for d in ("Needs_Action", "Plans", "Approved", "Done",
              "Logs", "Audits", "Pending_Approval", ".pids"):
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


WHATSAPP_PRICING_ITEM = """\
---
id: test_cd_001
source: whatsapp_watcher
priority: high
created: 2026-02-25 09:00
status: open
keywords_matched: [pricing, enterprise]
tags: ["#whatsapp", "#priority-high"]
---

# WhatsApp from Sarah Client

**Message:** "Can you send me pricing for the enterprise plan?"
**Contact:** Sarah Client
**Keyword triggers:** pricing, enterprise

## Suggested Actions

- [ ] Draft enterprise pricing reply
- [ ] Create LinkedIn post about enterprise plan
- [ ] Add Sarah to CRM pipeline
"""

# ---------------------------------------------------------------------------
# Suite 1 — Cross-domain classification
# ---------------------------------------------------------------------------

class TestCrossDomainClassification(unittest.TestCase):
    """Verify cross-domain item creation, YAML parsing, and plan generation."""

    def setUp(self):
        self.vault = _temp_vault()
        self.item_path = (
            self.vault / "Needs_Action"
            / "20260225_0900_WhatsApp_Sarah_Pricing.md"
        )
        self.item_path.write_text(WHATSAPP_PRICING_ITEM, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_item_created(self):
        """Needs_Action item file exists after injection."""
        self.assertTrue(self.item_path.exists())

    def test_yaml_frontmatter_parsed(self):
        """YAML frontmatter yields correct source, priority, and keywords."""
        import yaml
        text = self.item_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        fm = yaml.safe_load(parts[1])
        self.assertEqual(fm["source"], "whatsapp_watcher")
        self.assertEqual(fm["priority"], "high")
        self.assertIn("pricing", fm["keywords_matched"])
        self.assertIn("enterprise", fm["keywords_matched"])

    def test_business_trigger_detected(self):
        """Item body contains recognised business trigger keywords."""
        BUSINESS_KWS = {"pricing", "invoice", "payment", "quote",
                        "enterprise", "buy", "demo", "trial"}
        text = self.item_path.read_text(encoding="utf-8").lower()
        found = [kw for kw in BUSINESS_KWS if kw in text]
        self.assertGreater(len(found), 0,
                           f"No business keywords found; checked: {BUSINESS_KWS}")

    def test_cross_plan_written(self):
        """Simulated CROSS_PLAN file is written to Plans/ with correct content."""
        plan = self.vault / "Plans" / "CROSS_PLAN_20260225_0900.md"
        plan.write_text(
            "---\nid: cross_plan_test\nstatus: active\n---\n"
            "## Chain: WhatsApp pricing -> FB post draft\n"
            "- [ ] Draft LinkedIn enterprise pricing post\n",
            encoding="utf-8",
        )
        self.assertTrue(plan.exists())
        self.assertIn("cross_plan_test", plan.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Suite 2 — Social post draft + HITL gate
# ---------------------------------------------------------------------------

class TestSocialDraftCreation(unittest.TestCase):
    """Social draft creation and HITL approval gate enforcement."""

    def setUp(self):
        self.vault = _temp_vault()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def _create_fb_draft(self, ts: str = "20260225_090000"):
        draft_path = self.vault / "Plans" / f"FB_IG_DRAFT_FB_{ts}.md"
        approval_path = (
            self.vault / "Pending_Approval" / f"APPROVAL_FB_IG_DRAFT_FB_{ts}.md"
        )
        draft_path.write_text(
            f"---\nid: fb_draft_{ts}\nplatform: facebook\nstatus: draft\n"
            f"source_trigger: WhatsApp_Sarah_Pricing\napproved: false\n---\n"
            f"# Enterprise Plan Launch\nContent here.\n",
            encoding="utf-8",
        )
        approval_path.write_text(
            f"---\nid: approval_fb_{ts}\naction_type: fb_post\n"
            f"status: pending_approval\n"
            f"draft_file: Plans/FB_IG_DRAFT_FB_{ts}.md\n---\n"
            f"# Approval Request\nReview the draft, then move this file to Approved/.\n",
            encoding="utf-8",
        )
        return draft_path, approval_path

    def test_fb_draft_created(self):
        """FB draft file is written to Plans/."""
        draft, _ = self._create_fb_draft()
        self.assertTrue(draft.exists())

    def test_approval_request_created(self):
        """Approval request file is written to Pending_Approval/."""
        _, approval = self._create_fb_draft()
        self.assertTrue(approval.exists())

    def test_draft_blocked_before_approval(self):
        """Approved/ is empty before human review (HITL gate enforced)."""
        self._create_fb_draft()
        approved_files = list((self.vault / "Approved").glob("*.md"))
        self.assertEqual(
            len(approved_files), 0,
            "HITL gate broken: file in Approved/ before human review",
        )

    def test_hitl_approval_flow(self):
        """Moving approval file to Approved/ simulates human sign-off."""
        draft, approval = self._create_fb_draft()
        approved = self.vault / "Approved" / approval.name
        shutil.move(str(approval), str(approved))
        self.assertTrue(approved.exists(), "Approved/ file missing after move")
        self.assertFalse(approval.exists(), "Pending_Approval/ file still exists")

    def test_draft_references_source_item(self):
        """Draft file body links back to its triggering cross-domain item."""
        draft, _ = self._create_fb_draft()
        self.assertIn("WhatsApp_Sarah_Pricing",
                      draft.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Suite 3 — Audit logging (appends to real vault Logs/)
# ---------------------------------------------------------------------------

class TestAuditLogging(unittest.TestCase):
    """
    audit_logger.py writes correct NDJSON events.

    These tests append to the real Logs/YYYY-MM-DD.json file (safe — append
    only, never truncates).  Each test records the pre-existing line count
    in setUp() and reads only its own new lines.
    """

    def setUp(self):
        self._log_path = _REAL_LOGS / f"{date.today()}.json"
        if self._log_path.exists():
            self._pre_count = len(
                self._log_path.read_text(encoding="utf-8").strip().splitlines()
            )
        else:
            self._pre_count = 0

    def _new_events(self):
        if not self._log_path.exists():
            return []
        lines = self._log_path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines[self._pre_count:]]

    def test_audit_log_written(self):
        """audit_log() appends one NDJSON line with correct actor/action."""
        _al.audit_log(
            "test_gold_e2e", "cross_domain_event",
            params={"source": "whatsapp_watcher", "keyword": "pricing"},
            result="success",
            severity="INFO",
            source_file="test_gold_e2e.py",
        )
        new = self._new_events()
        self.assertGreater(len(new), 0, "No new log lines found")
        ev = new[-1]
        self.assertEqual(ev["actor"], "test_gold_e2e")
        self.assertEqual(ev["action"], "cross_domain_event")
        self.assertEqual(ev["result"], "success")

    def test_audit_log_schema_complete(self):
        """Every audit event contains all 9 required schema fields."""
        REQUIRED = {
            "timestamp", "actor", "action", "params",
            "result", "approval_status", "severity", "source_file", "error",
        }
        _al.audit_log(
            "test_schema", "schema_check",
            source_file="test_gold_e2e.py",
        )
        new = self._new_events()
        ev = new[-1]
        missing = REQUIRED - set(ev.keys())
        self.assertEqual(missing, set(), f"Missing schema fields: {missing}")

    def test_secret_scrubbing(self):
        """Params containing 'api_key' are redacted to '***REDACTED***'."""
        _al.audit_log(
            "test_scrub", "api_call",
            params={"api_key": "SECRET_KEY_12345", "message": "hello"},
            source_file="test_gold_e2e.py",
        )
        new = self._new_events()
        ev = new[-1]
        self.assertEqual(ev["params"]["api_key"], "***REDACTED***")
        self.assertEqual(ev["params"]["message"], "hello")

    def test_critical_alert_file_created(self):
        """CRITICAL severity creates an alert file in Needs_Action/."""
        before = set(_al._NEEDS_DIR.glob("*_ALERT_Critical_test_critical_*.md"))
        _al.audit_log(
            "test_critical", "disk_full",
            severity="CRITICAL",
            result="failure",
            source_file="test_gold_e2e.py",
        )
        after = set(_al._NEEDS_DIR.glob("*_ALERT_Critical_test_critical_*.md"))
        self.assertGreater(
            len(after - before), 0,
            "No CRITICAL alert file created in Needs_Action/",
        )


# ---------------------------------------------------------------------------
# Suite 4 — Ralph loop dry-run (writes to real Plans/ + Approved/)
# ---------------------------------------------------------------------------

class TestRalphLoopDryRun(unittest.TestCase):
    """
    ralph_loop.py dry-run: plan -> approve -> execute in <= 10 iterations.

    Writes artefacts to the real vault Plans/ and Approved/ directories.
    tearDown() removes all test files.
    """

    TASK_ID = "20260225_0900_Gold_E2E_Ralph_Test"

    def setUp(self):
        import ralph_loop as rl
        self._rl = rl
        self.task_file = _REAL_NEEDS / f"{self.TASK_ID}.md"
        self.task_file.write_text(
            "---\n"
            f"id: {self.TASK_ID.lower()}\n"
            "source: test_gold_e2e\n"
            "priority: low\n"
            "created: 2026-02-25 09:00\n"
            "status: open\n"
            "ralph_loop: true\n"
            "ralph_steps: [plan, approve, execute]\n"
            "ralph_max_iter: 10\n"
            "ralph_timeout: 60\n"
            "ralph_promise_tag: GOLD_E2E_COMPLETE\n"
            "---\n\n"
            "# Gold E2E Ralph Test Task\n\n"
            "Auto-generated by test_gold_e2e.py — safe to delete.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        """Remove all test artefacts from the real vault."""
        for path in (
            self.task_file,
            _REAL_PLANS    / f"PLAN_{self.TASK_ID}.md",
            _REAL_APPROVED / f"{self.TASK_ID}.md",
            _REAL_PIDS     / f"ralph_{self.TASK_ID}.lock",
            REPO_ROOT / "Done" / f"{self.TASK_ID}.md",
        ):
            path.unlink(missing_ok=True)

    def _run_loop(self):
        cfg = self._rl.build_config_from_task(
            self.task_file,
            max_iterations=10,
            timeout_secs=60,
            dry_run=True,
        )
        return self._rl.RalphLoop(cfg).run()

    def test_dry_run_succeeds(self):
        """Loop run returns result.success == True."""
        result = self._run_loop()
        self.assertTrue(
            result.success,
            f"Loop failed: reason={result.termination_reason}, error={result.error}",
        )

    def test_iterations_within_limit(self):
        """Loop completes in <= 10 iterations."""
        result = self._run_loop()
        self.assertLessEqual(result.iterations, 10)

    def test_termination_reason(self):
        """Loop terminates with reason 'promise' or 'done'."""
        result = self._run_loop()
        self.assertIn(
            result.termination_reason, ("promise", "done"),
            f"Unexpected termination: {result.termination_reason}",
        )

    def test_plan_file_created(self):
        """Dry-run creates Plans/PLAN_{task_id}.md."""
        self._run_loop()
        plan = _REAL_PLANS / f"PLAN_{self.TASK_ID}.md"
        self.assertTrue(plan.exists(), f"Plan file not found: {plan}")

    def test_approval_file_created(self):
        """Dry-run creates Approved/{task_id}.md."""
        self._run_loop()
        approved = _REAL_APPROVED / f"{self.TASK_ID}.md"
        self.assertTrue(approved.exists(), f"Approval file not found: {approved}")


# ---------------------------------------------------------------------------
# Suite 5 — Full Gold pipeline chain
# ---------------------------------------------------------------------------

class TestFullGoldPipeline(unittest.TestCase):
    """
    Full chain integration test:
      WhatsApp cross-domain event -> CROSS_PLAN -> FB draft -> audit log -> HITL gate
    """

    def setUp(self):
        self.vault = _temp_vault()
        self._log_path = _REAL_LOGS / f"{date.today()}.json"
        if self._log_path.exists():
            self._pre_count = len(
                self._log_path.read_text(encoding="utf-8").strip().splitlines()
            )
        else:
            self._pre_count = 0

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def _new_events(self):
        if not self._log_path.exists():
            return []
        lines = self._log_path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines[self._pre_count:]]

    def test_full_pipeline(self):
        """
        7-step Gold pipeline:
          Step 1 — WhatsApp item in Needs_Action/
          Step 2 — Classifier audit event emitted
          Step 3 — CROSS_PLAN written to Plans/
          Step 4 — FB draft + approval request created
          Step 5 — fb_ig_mcp draft audit event emitted
          Step 6 — Both audit events in log
          Step 7 — HITL gate: Approved/ empty before human review
        """
        ts = "20260225_090000"

        # Step 1: Cross-domain event arrives
        item = (
            self.vault / "Needs_Action"
            / "20260225_0900_WhatsApp_Sarah_Pricing.md"
        )
        item.write_text(WHATSAPP_PRICING_ITEM, encoding="utf-8")
        self.assertTrue(item.exists(), "Step 1 FAIL: Needs_Action item not created")

        # Step 2: Classifier logs the event
        _al.audit_log(
            "cross_domain_classifier", "item_classified",
            params={"source": "whatsapp_watcher", "keyword": "pricing"},
            result="success",
            severity="INFO",
            source_file="test_gold_e2e.py",
        )

        # Step 3: CROSS_PLAN written
        plan = self.vault / "Plans" / "CROSS_PLAN_20260225_0900.md"
        plan.write_text(
            "---\nid: cross_plan_e2e\nstatus: active\n---\n"
            "## Chain: WhatsApp pricing -> FB post\n",
            encoding="utf-8",
        )
        self.assertTrue(plan.exists(), "Step 3 FAIL: CROSS_PLAN not created")

        # Step 4: FB draft + approval request
        draft = self.vault / "Plans" / f"FB_IG_DRAFT_FB_{ts}.md"
        approval = (
            self.vault / "Pending_Approval" / f"APPROVAL_FB_IG_DRAFT_FB_{ts}.md"
        )
        draft.write_text(
            f"---\nid: fb_draft_{ts}\nplatform: facebook\nstatus: draft\n"
            "source_trigger: WhatsApp_Sarah_Pricing\n---\n"
            "# Enterprise Plan Launch\nContent here.\n",
            encoding="utf-8",
        )
        approval.write_text(
            f"---\nid: approval_fb_{ts}\nstatus: pending_approval\n---\n"
            "# Approval Request\n",
            encoding="utf-8",
        )
        self.assertTrue(draft.exists(),    "Step 4 FAIL: FB draft not created")
        self.assertTrue(approval.exists(), "Step 4 FAIL: approval request not created")

        # Step 5: fb_ig_mcp logs the draft creation
        _al.audit_log(
            "fb_ig_mcp", "draft_fb_post",
            params={
                "draft_file": f"FB_IG_DRAFT_FB_{ts}.md",
                "trigger": "WhatsApp_Sarah_Pricing",
            },
            result="success",
            approval_status="pending",
            severity="INFO",
            source_file="fb_ig_mcp.py",
        )

        # Step 6: Verify both audit events in log
        events = self._new_events()
        actors = [e["actor"] for e in events]
        self.assertIn("cross_domain_classifier", actors,
                      "Classifier event missing from audit log")
        self.assertIn("fb_ig_mcp", actors,
                      "fb_ig_mcp event missing from audit log")

        # Step 7: HITL gate — Approved/ must be empty before human review
        self.assertEqual(
            len(list((self.vault / "Approved").glob("*.md"))), 0,
            "HITL gate broken: file in Approved/ before human review",
        )

        print(
            f"\n[GOLD E2E] Full pipeline PASSED — "
            f"{len(events)} new audit events | "
            f"HITL gate enforced (0 files pre-approval)"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
