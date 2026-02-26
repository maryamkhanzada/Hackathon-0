"""
ralph_loop.py — Multi-Step Loop Persistence Engine for Personal AI Employee (Gold Tier)

Named after Ralph Wiggum's persistence loop from The Simpsons, this module
implements a resilient re-invocation loop that drives complex tasks through
multiple stages until a completion condition is satisfied.

Termination conditions (first one hit wins):
  1. Task file found in /Done/     (primary: file-movement completion)
  2. <promise>TAG</promise> found  (inline promise-tag in a result file)
  3. max_iterations reached        (safety: default 15)
  4. timeout_secs elapsed          (safety: default 1800 = 30 min)
  5. Unrecoverable error           (safety: hard stop)

Multi-step flow example:
  steps: [plan, approve, execute]

  plan     → Agent writes Plans/PLAN_{task_id}.md
  approve  → Agent writes Pending_Approval/APPROVAL_{task_id}.md;
             loop waits until Approved/{task_id}.md appears
  execute  → Agent executes plan, moves task file to Done/

Usage:
    # Run a loop on a task file:
    python ralph_loop.py --task Needs_Action/20260224_2100_My_Task.md

    # With custom limits:
    python ralph_loop.py --task ... --max-iter 10 --timeout 900

    # Dry-run (simulate steps, no real execution):
    python ralph_loop.py --task ... --dry-run

    # List active loops:
    python ralph_loop.py --status

    # Import and run programmatically:
    from ralph_loop import RalphLoop, RalphLoopConfig, build_config_from_task
    config = build_config_from_task(Path("Needs_Action/task.md"))
    loop   = RalphLoop(config)
    result = loop.run()
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_VAULT_ROOT    = Path(__file__).resolve().parent
_WATCHERS_DIR  = _VAULT_ROOT / "watchers"
_DONE_DIR      = _VAULT_ROOT / "Done"
_PLANS_DIR     = _VAULT_ROOT / "Plans"
_PENDING_DIR   = _VAULT_ROOT / "Pending_Approval"
_APPROVED_DIR  = _VAULT_ROOT / "Approved"
_LOGS_DIR      = _VAULT_ROOT / "Logs"
_PIDS_DIR      = _VAULT_ROOT / ".pids"
_NEEDS_DIR     = _VAULT_ROOT / "Needs_Action"

for _d in (_DONE_DIR, _PLANS_DIR, _PENDING_DIR, _APPROVED_DIR,
           _LOGS_DIR, _PIDS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(_WATCHERS_DIR))

try:
    from audit_logger import AuditLogger
    _alog = AuditLogger("ralph_loop", source_file="ralph_loop.py")
    _AUDIT = True
except ImportError:
    _AUDIT = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_ITERATIONS  = 15
DEFAULT_TIMEOUT_SECS    = 1800   # 30 minutes
DEFAULT_CHECK_INTERVAL  = 10     # seconds between completion polls
PROMISE_PATTERN         = re.compile(r"<promise>(.*?)</promise>", re.DOTALL)
STEP_ORDER_DEFAULT      = ["plan", "approve", "execute"]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class StepConfig:
    """Per-step configuration extracted from task frontmatter or defaults."""
    name:            str
    # Completion is met when ANY of these conditions is True:
    done_when_file:  str | None = None   # relative path that must exist
    done_when_tag:   str | None = None   # <promise>TAG</promise> token
    command:         list[str]  = field(default_factory=list)  # subprocess
    max_wait_secs:   int        = 600    # per-step timeout
    requires_human:  bool       = False  # wait for file in Approved/


@dataclass
class RalphLoopConfig:
    """Full configuration for one Ralph loop instance."""
    task_file:       Path
    task_id:         str                         # derived from filename
    steps:           list[StepConfig]
    max_iterations:  int  = DEFAULT_MAX_ITERATIONS
    timeout_secs:    int  = DEFAULT_TIMEOUT_SECS
    check_interval:  float = DEFAULT_CHECK_INTERVAL
    promise_tag:     str  = "TASK_COMPLETE"
    done_dir:        Path = _DONE_DIR
    dry_run:         bool = False


@dataclass
class LoopIteration:
    """Record of a single loop iteration."""
    iteration:   int
    step:        str
    started_at:  str
    ended_at:    str
    outcome:     str   # "step_complete" | "waiting" | "done" | "timeout" |
                       # "max_iter" | "error"
    detail:      str   = ""
    elapsed_secs: float = 0.0


@dataclass
class LoopResult:
    """Final result returned from RalphLoop.run()."""
    task_id:            str
    success:            bool
    iterations:         int
    elapsed_secs:       float
    termination_reason: str   # "done" | "promise" | "max_iter" | "timeout" | "error"
    final_step:         str
    history:            list[LoopIteration] = field(default_factory=list)
    error:              str | None = None


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def _parse_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def build_config_from_task(
    task_file: Path,
    *,
    max_iterations: int  = DEFAULT_MAX_ITERATIONS,
    timeout_secs:   int  = DEFAULT_TIMEOUT_SECS,
    dry_run:        bool = False,
) -> RalphLoopConfig:
    """
    Build a RalphLoopConfig from a task file's YAML frontmatter.

    Frontmatter keys recognised:
      ralph_loop: true              # must be present to enable looping
      ralph_steps: [plan, approve, execute]
      ralph_max_iter: 10
      ralph_timeout: 600
      ralph_promise_tag: MY_TAG
      ralph_plan_command: ["python", "some_script.py"]

    Defaults apply for any missing keys.
    """
    fm = _parse_frontmatter(task_file)
    task_id = task_file.stem

    # Step list from frontmatter or default
    step_names: list[str] = fm.get("ralph_steps", STEP_ORDER_DEFAULT)

    # Build StepConfig objects
    steps: list[StepConfig] = []
    for name in step_names:
        sc = StepConfig(name=name)
        # Per-step overrides from frontmatter: ralph_{step}_command, etc.
        sc.command        = fm.get(f"ralph_{name}_command", [])
        sc.max_wait_secs  = fm.get(f"ralph_{name}_timeout", 600)
        sc.requires_human = (name == "approve")

        # Default completion signals per step type
        if name == "plan":
            sc.done_when_file = f"Plans/PLAN_{task_id}.md"
        elif name == "approve":
            sc.done_when_file = f"Approved/{task_id}.md"
            sc.requires_human = True
        elif name == "execute":
            sc.done_when_tag  = fm.get("ralph_promise_tag", "TASK_COMPLETE")
        # Custom override
        if fm.get(f"ralph_{name}_done_file"):
            sc.done_when_file = fm[f"ralph_{name}_done_file"]
        if fm.get(f"ralph_{name}_done_tag"):
            sc.done_when_tag = fm[f"ralph_{name}_done_tag"]

        steps.append(sc)

    return RalphLoopConfig(
        task_file      = task_file,
        task_id        = task_id,
        steps          = steps,
        max_iterations = fm.get("ralph_max_iter", max_iterations),
        timeout_secs   = fm.get("ralph_timeout", timeout_secs),
        check_interval = fm.get("ralph_check_interval", DEFAULT_CHECK_INTERVAL),
        promise_tag    = fm.get("ralph_promise_tag", "TASK_COMPLETE"),
        done_dir       = _DONE_DIR,
        dry_run        = dry_run,
    )


# ---------------------------------------------------------------------------
# Ralph Loop engine
# ---------------------------------------------------------------------------


class RalphLoop:
    """
    Multi-step loop that drives a task through stages until completion.

    State machine:
      pending → plan → approve → execute → done

    Each stage transition is persisted back to the task file's frontmatter
    so the loop can be resumed after a crash (idempotent re-entry).
    """

    def __init__(self, config: RalphLoopConfig):
        self.cfg       = config
        self._start_ts = time.monotonic()
        self._history: list[LoopIteration] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> LoopResult:
        """
        Execute the loop.  Returns a LoopResult describing the outcome.
        """
        cfg      = self.cfg
        task_id  = cfg.task_id
        lock_path = _PIDS_DIR / f"ralph_{task_id}.lock"

        # Acquire lock (prevent concurrent loops on same task)
        if lock_path.exists():
            existing_pid = lock_path.read_text(encoding="utf-8").strip()
            return LoopResult(
                task_id=task_id, success=False, iterations=0,
                elapsed_secs=0, termination_reason="already_running",
                final_step="none",
                error=f"Lock held by PID {existing_pid}",
            )
        lock_path.write_text(str(os.getpid()), encoding="utf-8")

        self._log_event("loop_start", {
            "task": str(cfg.task_file),
            "steps": [s.name for s in cfg.steps],
            "max_iter": cfg.max_iterations,
            "timeout_secs": cfg.timeout_secs,
            "dry_run": cfg.dry_run,
        })
        print(f"[ralph] Starting loop for: {cfg.task_file.name}")
        print(f"[ralph] Steps: {' -> '.join(s.name for s in cfg.steps)}")
        print(f"[ralph] Limits: max_iter={cfg.max_iterations}, "
              f"timeout={cfg.timeout_secs}s")

        current_step_idx = self._resume_step_index()
        iteration        = 0
        result           = None

        try:
            while True:
                iteration += 1
                elapsed   = time.monotonic() - self._start_ts

                # --- Safety checks ----------------------------------------
                if iteration > cfg.max_iterations:
                    result = self._finish("max_iter", iteration - 1, current_step_idx,
                                         "Maximum iterations reached")
                    break

                if elapsed > cfg.timeout_secs:
                    result = self._finish("timeout", iteration - 1, current_step_idx,
                                         f"Timeout after {elapsed:.0f}s")
                    break

                # --- Primary completion: task file in Done/ ----------------
                if self._task_in_done():
                    result = self._finish("done", iteration, current_step_idx,
                                         "Task file found in Done/")
                    break

                step = cfg.steps[current_step_idx]
                iter_start = datetime.now(timezone.utc).isoformat()
                print(f"\n[ralph] Iter {iteration}/{cfg.max_iterations} | "
                      f"Step: {step.name} | Elapsed: {elapsed:.0f}s")

                # --- Execute the step -------------------------------------
                step_output = self._execute_step(step, iteration)

                # --- Check promise tag in output --------------------------
                if step_output and self._has_promise(step_output, cfg.promise_tag):
                    rec = LoopIteration(
                        iteration=iteration, step=step.name,
                        started_at=iter_start,
                        ended_at=datetime.now(timezone.utc).isoformat(),
                        outcome="promise_tag_found",
                        detail=f"<promise>{cfg.promise_tag}</promise> detected",
                        elapsed_secs=time.monotonic() - self._start_ts,
                    )
                    self._history.append(rec)
                    result = self._finish("promise", iteration, current_step_idx,
                                         f"Promise tag '{cfg.promise_tag}' detected")
                    break

                # --- Check step completion --------------------------------
                step_done, detail = self._check_step_complete(step)
                iter_outcome      = "step_complete" if step_done else "waiting"

                rec = LoopIteration(
                    iteration    = iteration,
                    step         = step.name,
                    started_at   = iter_start,
                    ended_at     = datetime.now(timezone.utc).isoformat(),
                    outcome      = iter_outcome,
                    detail       = detail,
                    elapsed_secs = time.monotonic() - self._start_ts,
                )
                self._history.append(rec)
                self._log_iteration(rec)
                self._update_task_frontmatter(step.name, iteration, iter_outcome)

                if step_done:
                    print(f"[ralph]   Step '{step.name}' complete: {detail}")
                    current_step_idx += 1

                    if current_step_idx >= len(cfg.steps):
                        # All steps complete → move task to Done/
                        moved = self._move_to_done()
                        result = self._finish(
                            "done", iteration, current_step_idx - 1,
                            f"All steps complete. Moved to Done/: {moved}"
                        )
                        break
                    else:
                        next_step = cfg.steps[current_step_idx].name
                        print(f"[ralph]   Advancing to step: {next_step}")
                        self._update_task_frontmatter(next_step, iteration, "step_started")
                else:
                    print(f"[ralph]   Waiting for step '{step.name}': {detail}")
                    time.sleep(cfg.check_interval)

        except KeyboardInterrupt:
            result = self._finish("interrupted", iteration, current_step_idx,
                                  "Keyboard interrupt")
        except Exception as exc:
            result = self._finish("error", iteration, current_step_idx,
                                  str(exc), error=str(exc))
        finally:
            lock_path.unlink(missing_ok=True)

        if result is None:
            result = self._finish("error", iteration, current_step_idx, "Unknown exit")

        self._log_event("loop_end", {
            "success":      result.success,
            "iterations":   result.iterations,
            "elapsed_secs": round(result.elapsed_secs, 1),
            "reason":       result.termination_reason,
            "final_step":   result.final_step,
        })
        self._write_loop_log(result)
        return result

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def _execute_step(self, step: StepConfig, iteration: int) -> str | None:
        """
        Execute a step.  Returns captured output (for promise-tag detection),
        or None if no command is defined.

        In dry-run mode, execution is simulated.
        """
        if self.cfg.dry_run:
            return self._dry_run_step(step, iteration)

        if not step.command:
            # No command → rely on external actor / human to complete the step
            if step.requires_human:
                print(f"[ralph]   [{step.name}] Waiting for human action "
                      f"(approval file: Approved/{self.cfg.task_id}.md)")
            return None

        try:
            proc = subprocess.run(
                step.command,
                cwd=str(_VAULT_ROOT),
                capture_output=True,
                text=True,
                timeout=step.max_wait_secs,
            )
            output = proc.stdout + proc.stderr
            if proc.returncode != 0:
                print(f"[ralph]   [{step.name}] Command exited with code "
                      f"{proc.returncode}")
            return output
        except subprocess.TimeoutExpired:
            print(f"[ralph]   [{step.name}] Command timed out after "
                  f"{step.max_wait_secs}s")
            return None
        except (OSError, FileNotFoundError) as exc:
            print(f"[ralph]   [{step.name}] Command error: {exc}")
            return None

    def _dry_run_step(self, step: StepConfig, iteration: int) -> str | None:
        """
        Simulate step execution in dry-run mode.

        - plan:    writes a mock Plans/PLAN_{id}.md
        - approve: writes a mock Approved/{id}.md (bypasses human gate)
        - execute: returns a promise tag in output
        """
        task_id = self.cfg.task_id
        if step.name == "plan":
            plan_path = _PLANS_DIR / f"PLAN_{task_id}.md"
            plan_path.write_text(
                f"---\ntask: {task_id}\nstep: plan\n"
                f"created: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n---\n\n"
                f"# Plan for {task_id}\n\n"
                f"_[DRY_RUN] Auto-generated plan — iteration {iteration}_\n\n"
                f"1. Analyse task requirements\n"
                f"2. Identify dependencies\n"
                f"3. Execute action items\n",
                encoding="utf-8",
            )
            print(f"[ralph]   [dry_run] Plan written: {plan_path.name}")
            return None

        elif step.name == "approve":
            # In dry-run, auto-approve by creating the approval file
            _APPROVED_DIR.mkdir(parents=True, exist_ok=True)
            approved_path = _APPROVED_DIR / f"{task_id}.md"
            approved_path.write_text(
                f"---\ntask: {task_id}\napproval: auto\n"
                f"approved_at: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n---\n\n"
                f"# Auto-Approval (DRY_RUN)\n\nTask: {task_id}\n",
                encoding="utf-8",
            )
            print(f"[ralph]   [dry_run] Approval written: {approved_path.name}")
            return None

        elif step.name == "execute":
            # Return a promise tag so the loop terminates cleanly
            promise_tag = self.cfg.promise_tag
            print(f"[ralph]   [dry_run] Execute step emitting promise tag: "
                  f"<promise>{promise_tag}</promise>")
            return f"Execution complete.\n<promise>{promise_tag}</promise>\n"

        return None

    # ------------------------------------------------------------------
    # Completion checks
    # ------------------------------------------------------------------

    def _task_in_done(self) -> bool:
        """Check if the task file (by stem) has been moved to Done/."""
        task_stem = self.cfg.task_file.stem
        # Check for exact filename match
        if (self.cfg.done_dir / self.cfg.task_file.name).exists():
            return True
        # Check for any file with same stem (extension may differ)
        return any(self.cfg.done_dir.glob(f"{task_stem}*"))

    def _check_step_complete(self, step: StepConfig) -> tuple[bool, str]:
        """
        Return (is_complete, detail_message) for the given step.
        """
        # File-based completion signal
        if step.done_when_file:
            target = _VAULT_ROOT / step.done_when_file
            if target.exists():
                return True, f"Completion file found: {step.done_when_file}"
            return False, f"Waiting for: {step.done_when_file}"

        # Promise-tag in a specific result file (execute step)
        if step.done_when_tag:
            # Search recent files in Plans/ and Logs/ for the tag
            tag_pattern = f"<promise>{step.done_when_tag}</promise>"
            for search_dir in (_PLANS_DIR, _LOGS_DIR, _VAULT_ROOT):
                for md in sorted(search_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                    try:
                        if tag_pattern in md.read_text(encoding="utf-8"):
                            return True, f"Promise tag '{step.done_when_tag}' found in {md.name}"
                    except OSError:
                        pass
            return False, f"Waiting for <promise>{step.done_when_tag}</promise>"

        # Human-approval gate
        if step.requires_human:
            approval = _APPROVED_DIR / f"{self.cfg.task_id}.md"
            if approval.exists():
                return True, f"Human approval received: {approval.name}"
            return False, "Waiting for human approval in Approved/"

        # No explicit condition → assume complete after first check
        return True, "No explicit completion condition (auto-advance)"

    @staticmethod
    def _has_promise(output: str, tag: str) -> bool:
        """Return True if <promise>tag</promise> appears in output."""
        return f"<promise>{tag}</promise>" in output

    # ------------------------------------------------------------------
    # State persistence (task frontmatter)
    # ------------------------------------------------------------------

    def _resume_step_index(self) -> int:
        """
        Read current ralph_current_step from task frontmatter.
        Returns the index of the step to resume from (0 if fresh start).
        """
        fm          = _parse_frontmatter(self.cfg.task_file)
        current     = fm.get("ralph_current_step", self.cfg.steps[0].name)
        step_names  = [s.name for s in self.cfg.steps]
        try:
            idx = step_names.index(current)
            if idx > 0:
                print(f"[ralph] Resuming from step '{current}' (index {idx})")
            return idx
        except ValueError:
            return 0

    def _update_task_frontmatter(
        self, step: str, iteration: int, status: str
    ) -> None:
        """Update ralph_* keys in the task file's YAML frontmatter."""
        try:
            text  = self.cfg.task_file.read_text(encoding="utf-8")
            now   = datetime.now().strftime("%Y-%m-%d %H:%M")
            # Inject or replace ralph_* keys
            updates = {
                "ralph_current_step": step,
                "ralph_iteration":    iteration,
                "ralph_last_update":  now,
                "ralph_status":       status,
            }
            # Find existing frontmatter block
            m = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)(.*)", text, re.DOTALL)
            if not m:
                return
            fm_text, body = m.group(2), m.group(4)
            fm_dict = yaml.safe_load(fm_text) or {}
            fm_dict.update(updates)
            new_fm   = yaml.dump(fm_dict, default_flow_style=False,
                                 allow_unicode=True).rstrip()
            new_text = f"---\n{new_fm}\n---\n{body}"
            self.cfg.task_file.write_text(new_text, encoding="utf-8")
        except (OSError, yaml.YAMLError):
            pass  # Non-fatal; loop continues even if frontmatter update fails

    # ------------------------------------------------------------------
    # Task movement
    # ------------------------------------------------------------------

    def _move_to_done(self) -> str:
        """Move the task file to Done/. Returns the destination filename."""
        if self.cfg.dry_run:
            print(f"[ralph]   [dry_run] Would move {self.cfg.task_file.name} to Done/")
            return f"[dry_run] {self.cfg.task_file.name}"

        if not self.cfg.task_file.exists():
            return "(already moved)"

        dest = self.cfg.done_dir / self.cfg.task_file.name
        # Avoid collisions
        if dest.exists():
            ts   = datetime.now().strftime("%H%M%S")
            dest = dest.with_stem(f"{dest.stem}_{ts}")

        shutil.move(str(self.cfg.task_file), str(dest))
        print(f"[ralph]   Moved task to Done/: {dest.name}")
        return dest.name

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_event(self, action: str, params: dict,
                   result: str = "success", severity: str = "INFO",
                   error: str | None = None) -> None:
        if _AUDIT:
            _alog.log(action, params=params, result=result,
                      severity=severity, error=error)

    def _log_iteration(self, rec: LoopIteration) -> None:
        if _AUDIT:
            _alog.log(
                "loop_iteration",
                params={
                    "iteration": rec.iteration,
                    "step":      rec.step,
                    "outcome":   rec.outcome,
                    "detail":    rec.detail,
                },
                result  = "success" if rec.outcome in
                           ("step_complete", "promise_tag_found", "done") else "skipped",
                severity = "INFO",
            )

    def _write_loop_log(self, result: LoopResult) -> None:
        """Append the full loop result to Logs/ralph_{date}.json."""
        today    = datetime.now().strftime("%Y-%m-%d")
        log_path = _LOGS_DIR / f"ralph_{today}.json"
        existing: list = []
        if log_path.exists():
            try:
                existing = json.loads(log_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = []
        entry = {
            "timestamp":          datetime.now().isoformat(),
            "task_id":            result.task_id,
            "success":            result.success,
            "iterations":         result.iterations,
            "elapsed_secs":       round(result.elapsed_secs, 1),
            "termination_reason": result.termination_reason,
            "final_step":         result.final_step,
            "error":              result.error,
            "history": [
                {k: v for k, v in vars(rec).items()}
                for rec in result.history
            ],
        }
        existing.append(entry)
        log_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _finish(
        self,
        reason:   str,
        iteration: int,
        step_idx:  int,
        detail:    str,
        error:     str | None = None,
    ) -> LoopResult:
        elapsed    = time.monotonic() - self._start_ts
        success    = reason in ("done", "promise")
        final_step = (
            self.cfg.steps[min(step_idx, len(self.cfg.steps) - 1)].name
            if self.cfg.steps else "none"
        )
        status_sym = "OK" if success else "!!"
        print(f"\n[ralph] [{status_sym}] Loop terminated: {reason} — {detail}")
        print(f"[ralph]     Iterations: {iteration}  |  Elapsed: {elapsed:.1f}s  |  "
              f"Final step: {final_step}")

        if error:
            self._log_event("loop_error", {"reason": reason, "detail": detail},
                            result="failure", severity="ERROR", error=error)

        return LoopResult(
            task_id            = self.cfg.task_id,
            success            = success,
            iterations         = iteration,
            elapsed_secs       = elapsed,
            termination_reason = reason,
            final_step         = final_step,
            history            = self._history,
            error              = error,
        )


# ---------------------------------------------------------------------------
# Lock / status helpers (module-level)
# ---------------------------------------------------------------------------


def list_active_loops() -> list[dict]:
    """Return info about all running Ralph loops (from lock files)."""
    active: list[dict] = []
    for lock in _PIDS_DIR.glob("ralph_*.lock"):
        try:
            pid_str  = lock.read_text(encoding="utf-8").strip()
            task_id  = lock.stem[len("ralph_"):]
            active.append({
                "task_id":  task_id,
                "pid":      pid_str,
                "lock_file": lock.name,
            })
        except OSError:
            pass
    return active


def is_loop_running(task_id: str) -> bool:
    return (_PIDS_DIR / f"ralph_{task_id}.lock").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_status() -> None:
    active = list_active_loops()
    print(f"Active Ralph loops: {len(active)}")
    for loop in active:
        print(f"  task_id={loop['task_id']}  pid={loop['pid']}")
    if not active:
        print("  (none)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ralph Loop — multi-step task persistence engine"
    )
    parser.add_argument("--task",        default=None,
                        help="Path to task file in Needs_Action/ to process.")
    parser.add_argument("--max-iter",    type=int, default=DEFAULT_MAX_ITERATIONS,
                        help=f"Max iterations (default {DEFAULT_MAX_ITERATIONS}).")
    parser.add_argument("--timeout",     type=int, default=DEFAULT_TIMEOUT_SECS,
                        help=f"Timeout seconds (default {DEFAULT_TIMEOUT_SECS}).")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Simulate all step execution (no real side effects).")
    parser.add_argument("--status",      action="store_true",
                        help="List active loops and exit.")
    parser.add_argument("--check-interval", type=float, default=DEFAULT_CHECK_INTERVAL,
                        help="Seconds between completion polls (default 10).")
    args = parser.parse_args()

    if args.status:
        _print_status()
        return

    if not args.task:
        parser.error("--task is required unless --status is used.")

    task_path = Path(args.task)
    if not task_path.is_absolute():
        task_path = _VAULT_ROOT / task_path
    if not task_path.exists():
        print(f"[ralph] ERROR: Task file not found: {task_path}")
        sys.exit(1)

    config        = build_config_from_task(
        task_path,
        max_iterations = args.max_iter,
        timeout_secs   = args.timeout,
        dry_run        = args.dry_run,
    )
    config.check_interval = args.check_interval

    loop   = RalphLoop(config)
    result = loop.run()

    if result.success:
        print(f"\n<promise>RALPH_LOOPS_ENABLED</promise>")
        sys.exit(0)
    else:
        print(f"\n[ralph] Loop did not complete successfully: "
              f"{result.termination_reason}")
        sys.exit(1)


if __name__ == "__main__":
    main()
