---
id: SKILL_E2E_Gold_Test
version: "1.0"
created: 2026-02-25
status: active
tier: gold
tags: ["#skill", "#testing", "#e2e", "#gold", "#qa"]
---

# SKILL: Run_Gold_E2E_Tests

**Version:** 1.0
**Tier:** Gold
**Last Updated:** 2026-02-25
**Author:** Claude Code (Gold AI Employee)

---

## Trigger

Activate when ANY of the following are true:

- Any Gold-tier Python file is modified (`ralph_loop.py`, `audit_logger.py`, `fb_ig_mcp.py`, `x_mcp.py`, `mcp_orchestrator.py`, `export_pdf.py`)
- Before a demo or client presentation
- After adding a new skill or watcher
- CI pipeline on push to main branch
- User types `run tests`, `test gold pipeline`, or `validate system`

---

## Purpose

Run the full Gold Tier end-to-end test harness to verify the complete pipeline:

1. **Cross-domain classification** — WhatsApp item parsed, keywords detected, CROSS_PLAN created
2. **Social post drafting with HITL gate** — FB draft + approval request, gate enforced
3. **Audit logging** — NDJSON schema validation, secret scrubbing, CRITICAL alerts
4. **Ralph loop dry-run** — plan → approve → execute, 3 steps, ≤10 iterations
5. **Full pipeline chain** — all stages in sequence, audit events verified

---

## Test Files

| File | Coverage | Tests |
|------|----------|-------|
| `tests/test_gold_e2e.py` | Gold Tier full pipeline | 17 test cases |
| `tests/test_bronze_e2e.py` | Bronze Tier vault processor | 25 test cases |

---

## Test Suites

### TestCrossDomainClassification (4 tests)

| Test | What It Verifies |
|------|-----------------|
| `test_item_created` | Item written to Needs_Action/ |
| `test_yaml_frontmatter_parsed` | YAML: source, priority, keywords_matched |
| `test_business_trigger_detected` | Business keywords present in item body |
| `test_cross_plan_written` | CROSS_PLAN file created in Plans/ |

### TestSocialDraftCreation (5 tests)

| Test | What It Verifies |
|------|-----------------|
| `test_fb_draft_created` | FB draft file written to Plans/ |
| `test_approval_request_created` | Approval file in Pending_Approval/ |
| `test_draft_blocked_before_approval` | No file in Approved/ before HITL gate |
| `test_hitl_approval_flow` | Moving file to Approved/ unblocks post |
| `test_draft_references_source_item` | Draft links back to triggering item |

### TestAuditLogging (4 tests)

| Test | What It Verifies |
|------|-----------------|
| `test_audit_log_written` | NDJSON line written to `Logs/YYYY-MM-DD.json` |
| `test_audit_log_schema_complete` | All 9 schema fields present |
| `test_secret_scrubbing` | `api_key` redacted to `***REDACTED***` |
| `test_critical_alert_file_created` | CRITICAL event writes alert to Needs_Action/ |

### TestRalphLoopDryRun (5 tests)

| Test | What It Verifies |
|------|-----------------|
| `test_dry_run_succeeds` | Loop returns `result.success == True` |
| `test_iterations_within_limit` | `result.iterations <= 10` |
| `test_termination_reason` | Reason is `"promise"` or `"done"` |
| `test_plan_file_created` | `Plans/PLAN_{task_id}.md` created |
| `test_approval_file_created` | `Approved/{task_id}.md` created |

### TestFullGoldPipeline (1 integration test)

Full 7-step chain in sequence:

```
Step 1: WhatsApp item written to Needs_Action/
Step 2: audit_log("cross_domain_classifier", "item_classified") emitted
Step 3: CROSS_PLAN written to Plans/
Step 4: FB draft + approval request created
Step 5: audit_log("fb_ig_mcp", "draft_fb_post") emitted
Step 6: Both audit events verified in Logs/YYYY-MM-DD.json
Step 7: HITL gate verified — Approved/ empty before human review
```

---

## Running Tests

```bash
# All Gold Tier tests:
python tests/test_gold_e2e.py

# Verbose (show each test name and result):
python tests/test_gold_e2e.py -v

# Single suite only:
python tests/test_gold_e2e.py TestAuditLogging
python tests/test_gold_e2e.py TestRalphLoopDryRun

# All tiers (Bronze + Gold combined):
python tests/test_bronze_e2e.py && python tests/test_gold_e2e.py

# Expected output (all pass):
# Ran 19 tests in X.XXXs
# OK
```

---

## Environment

- Cross-domain and social draft tests use isolated `tempfile.mkdtemp()` vaults — no real vault files touched
- Audit logging tests write to the real `Logs/YYYY-MM-DD.json` (append only, safe for dev)
- Ralph loop dry-run writes to real `Plans/` and `Approved/`, then cleans up in `tearDown()`
- No live API calls (all external services DRY_RUN or mocked)
- Tests pass on Windows (Path handling, UTF-8 encoding enforced)

---

## Cleanup Contract

`TestRalphLoopDryRun.tearDown()` always removes:

- `Needs_Action/20260225_0900_Gold_E2E_Ralph_Test.md`
- `Plans/PLAN_20260225_0900_Gold_E2E_Ralph_Test.md`
- `Approved/20260225_0900_Gold_E2E_Ralph_Test.md`
- `.pids/ralph_20260225_0900_Gold_E2E_Ralph_Test.lock`
- `Done/20260225_0900_Gold_E2E_Ralph_Test.md` (if moved there)

---

## CI Integration

```yaml
# GitHub Actions / CI example:
- name: Install Python dependencies
  run: pip install pyyaml python-dotenv reportlab markdown

- name: Run Bronze E2E Tests
  run: python tests/test_bronze_e2e.py -v

- name: Run Gold E2E Tests
  run: python tests/test_gold_e2e.py -v
```

---

## Reusable Prompt Template

```
Run Run_Gold_E2E_Tests:

Scope:  [gold_only | bronze_only | all]
Mode:   [verbose | summary]
Suite:  [TestCrossDomain | TestSocialDraft | TestAuditLogging |
         TestRalph | TestFullPipeline | all]

Expected output:
  - Test counts: X passed, 0 failed
  - "OK" or failure traceback with line reference
  - [GOLD E2E] Full pipeline PASSED summary for TestFullPipeline
```

---

## Acceptance Criteria

- [x] All 5 test suites run independently with correct isolation
- [x] `TestRalphLoopDryRun` uses real vault but cleans up in `tearDown()`
- [x] `TestAuditLogging` appends-only — never corrupts existing log entries
- [x] `TestFullGoldPipeline` passes all 7 steps in sequence
- [x] No live API calls during any test
- [x] Tests pass on Windows (UTF-8 paths, `unlink(missing_ok=True)`)
- [x] `python tests/test_gold_e2e.py -v` runs in < 30 seconds

---

## Related Skills

- `SKILL_ralph_loop.md` — Tests `ralph_loop.py` dry-run engine
- `SKILL_Logging.md` — Tests `audit_logger.py` NDJSON schema
- `SKILL_Cross_Integration.md` — Tests cross-domain classification logic
- `SKILL_FB_IG.md` — Tests social draft creation + HITL gate
- `SKILL_Gold_Documentation.md` — PDF export tested via `--test-diagram`
