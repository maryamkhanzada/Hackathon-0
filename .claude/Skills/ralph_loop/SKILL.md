---
id: SKILL_Ralph_Loop
version: "2.0"
created: 2026-02-24
status: active
tags: ["#skill", "#ralph", "#loop", "#multi-step", "#orchestration"]
---

# SKILL: Implement_Ralph_Wiggum_Loops

## Trigger

Activate when:
- A task in `Needs_Action/` requires more than one sequential stage
  (e.g., plan → human approval → execution)
- A task must survive agent restarts and resume where it left off
- You need to drive a task to completion with a hard timeout/iteration cap
- `mcp_orchestrator.py` detects a task with `ralph_loop: true` in frontmatter

---

## Architecture

```
mcp_orchestrator.py               Needs_Action/task.md
  |  every health cycle              ralph_loop: true
  |  _scan_needs_action()
  |                                         |
  +-------> ralph_loop.py <----------------+
               |
               | State machine (per-task)
               v
            pending
               |
           [plan step]    -> writes Plans/PLAN_{task_id}.md
               |             completion: file exists
               v
           [approve step] -> waits for Approved/{task_id}.md
               |             human (or dry-run: auto-written)
               v
           [execute step] -> runs command or emits <promise>TAG</promise>
               |             completion: promise tag detected
               v
            done           -> task file moved to Done/
                              loop exits with <promise>RALPH_LOOPS_ENABLED</promise>
```

---

## Enabling a Ralph Loop on Any Task

Add these keys to the task file's YAML frontmatter:

```yaml
---
ralph_loop: true                         # Required: enables loop detection
ralph_steps:                             # Step order (default: plan/approve/execute)
  - plan
  - approve
  - execute
ralph_max_iter: 15                       # Safety: max iterations (default 15)
ralph_timeout: 1800                      # Safety: timeout seconds (default 1800 = 30 min)
ralph_promise_tag: TASK_COMPLETE         # Token to look for in execute output
---
```

### Per-Step Customisation

```yaml
# Custom completion file for plan step:
ralph_plan_done_file: Plans/MY_PLAN.md

# Custom completion tag for execute step:
ralph_execute_done_tag: MY_CUSTOM_TAG

# Custom command for execute step:
ralph_execute_command: ["python", "my_script.py", "--task", "my_task"]
```

---

## Running a Loop

```bash
# Standard (check every 10s):
python ralph_loop.py --task Needs_Action/20260224_2100_My_Task.md

# Fast-poll (e.g., for CI):
python ralph_loop.py --task ... --check-interval 2

# Dry-run (simulate all steps — no real side effects):
python ralph_loop.py --task ... --dry-run

# Custom limits:
python ralph_loop.py --task ... --max-iter 5 --timeout 300

# Check active loops:
python ralph_loop.py --status
```

---

## Termination Conditions

The loop stops as soon as the **first** condition is met:

| Priority | Condition | `termination_reason` | `success` |
|----------|-----------|----------------------|-----------|
| 1st | Task file found in `Done/` | `done` | True |
| 2nd | `<promise>TAG</promise>` in step output | `promise` | True |
| 3rd | All steps complete (engine moves file) | `done` | True |
| 4th | `max_iterations` reached | `max_iter` | False |
| 5th | `timeout_secs` elapsed | `timeout` | False |
| 6th | Unrecoverable exception | `error` | False |

---

## Safety Limits

| Limit | Default | Frontmatter override |
|-------|---------|----------------------|
| Max iterations | 15 | `ralph_max_iter: N` |
| Timeout | 30 min | `ralph_timeout: N` (seconds) |
| Check interval | 10 s | `ralph_check_interval: N` |
| Lock prevents concurrency | always | — |

- **Lock file**: `.pids/ralph_{task_id}.lock` — prevents two loops on the same task
- **Idempotent restart**: `ralph_current_step` in frontmatter resumes at correct step after crash
- **Audit logging**: every iteration logged to `Logs/ralph_{date}.json` AND unified `Logs/{date}.json`

---

## Multi-Step Flow Examples

### Plan → Approve → Execute (default)

```
Step 1 plan:    Agent writes Plans/PLAN_{task_id}.md
                Loop detects file -> advances
Step 2 approve: Loop waits for Approved/{task_id}.md
                Human reviews plan, creates approval file
                Loop detects file -> advances
Step 3 execute: Agent runs command or emits <promise>TASK_COMPLETE</promise>
                Loop detects tag -> moves task to Done/
```

### Two-step: Generate → Verify

```yaml
ralph_steps: [generate, verify]
ralph_generate_done_file: Plans/REPORT_{task_id}.md
ralph_verify_done_tag: VERIFIED_OK
```

### Single-step: Execute Only

```yaml
ralph_steps: [execute]
ralph_promise_tag: AUDIT_COMPLETE
```
This is the pattern used by `audit.py --ralph`.

### Five-step Research Pipeline

```yaml
ralph_steps: [gather, analyse, draft, review, publish]
ralph_max_iter: 15
ralph_timeout: 3600
```

---

## Orchestrator Auto-Trigger

`mcp_orchestrator.py` scans `Needs_Action/` at every health cycle (every 60 s).
Items with `ralph_loop: true` that are not already running are spawned automatically.

The orchestrator's `## MCP Status` Dashboard section shows:
- `Ralph loops active: N`
- `Just launched: task_id_1, task_id_2`

To disable auto-trigger for a specific task, set `ralph_loop: false` or remove the key.

---

## State Written Back to Task File

After each iteration the loop updates the task file's frontmatter:

```yaml
ralph_current_step: execute    # current step name
ralph_iteration: 3             # iteration count
ralph_last_update: "2026-02-24 21:01"
ralph_status: step_complete    # step_complete | waiting | step_started | promise_tag_found
```

This allows human inspection of loop progress and safe re-entry after a crash.

---

## Loop Log Schema (`Logs/ralph_{date}.json`)

```json
[
  {
    "timestamp": "2026-02-24T21:01:00",
    "task_id": "20260224_2100_Process_Test_Task_Until_Done",
    "success": true,
    "iterations": 3,
    "elapsed_secs": 0.9,
    "termination_reason": "promise",
    "final_step": "execute",
    "error": null,
    "history": [
      {"iteration": 1, "step": "plan",    "outcome": "step_complete",    "detail": "Completion file found: Plans/PLAN_...md"},
      {"iteration": 2, "step": "approve", "outcome": "step_complete",    "detail": "Completion file found: Approved/...md"},
      {"iteration": 3, "step": "execute", "outcome": "promise_tag_found","detail": "<promise>TASK_COMPLETE</promise> detected"}
    ]
  }
]
```

---

## Integration with audit.py (Ralph pattern)

The `--ralph` flag in `audit.py` emits a promise tag when the briefing is ready.
Configure as a single-step Ralph loop:

```yaml
ralph_steps: [execute]
ralph_execute_command: ["python", "audit.py", "--ralph"]
ralph_promise_tag: AUDIT_COMPLETE
```

---

## Programmatic Usage

```python
from pathlib import Path
from ralph_loop import RalphLoop, build_config_from_task

config = build_config_from_task(
    Path("Needs_Action/20260224_2100_My_Task.md"),
    max_iterations=10,
    timeout_secs=600,
    dry_run=False,
)
loop   = RalphLoop(config)
result = loop.run()

print(f"Success: {result.success}")
print(f"Reason:  {result.termination_reason}")
print(f"Steps:   {result.iterations} iterations in {result.elapsed_secs:.1f}s")
```

---

## Acceptance Criteria

- [x] 3-step dry-run test: plan -> approve -> execute in 3 iterations, ~1s
- [x] Promise tag `<promise>TASK_COMPLETE</promise>` detected; loop exits success
- [x] Lock file written on start, removed on completion
- [x] `ralph_current_step` + `ralph_iteration` persisted to task frontmatter after each iteration
- [x] Plan file created in `Plans/`, approval file in `Approved/` (dry-run)
- [x] Loop log written to `Logs/ralph_{date}.json` with full iteration history
- [x] `is_loop_running()` and `list_active_loops()` return correct results
- [x] Orchestrator `_scan_needs_action()` detects `ralph_loop: true` tasks
- [x] `_reap_ralph_loops()` tracks and removes completed subprocesses
- [x] Dashboard `## MCP Status` shows Ralph loop counts
- [x] Test: 15/15 passed

---

## Related Skills

- `SKILL_Error_Recovery.md` — resilience.py retry/cache used within step execution
- `SKILL_Logging.md` — every iteration logged to unified audit log via AuditLogger
- `SKILL_MCP_Manager.md` — orchestrator auto-triggers loops via `_scan_needs_action()`
- `SKILL_Weekly_Audit.md` — `audit.py --ralph` is a single-step Ralph loop
