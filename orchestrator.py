#!/usr/bin/env python3
"""
orchestrator.py — Master Control Script for Personal AI Employee (Silver Tier)

The single entry point that runs the entire system:
  1. Launches watcher subprocesses (Gmail, WhatsApp, LinkedIn, Approval Loop)
  2. Runs periodic processing cycles:
     - Scan Needs_Action/ → process new items, create reasoning plans
     - Check Approved/ → execute approved actions
     - Expire stale Pending_Approval/ requests
     - Update Dashboard.md with live status
  3. Generates daily summary at configurable time
  4. Gracefully handles shutdown (SIGINT/SIGTERM)

Usage:
    python orchestrator.py                  # Full system (watchers + processing loop)
    python orchestrator.py --no-watchers    # Processing loop only (watchers run separately)
    python orchestrator.py --once           # Single processing cycle, no watchers
    python orchestrator.py --status         # Print system status and exit
    python orchestrator.py --daily-summary  # Generate daily summary and exit
    python orchestrator.py --vault /path    # Override vault path

Scheduling (cron / Task Scheduler):
    # Run full system at 8am daily:
    0 8 * * * cd /d/Hackathon-0 && python orchestrator.py --once >> Logs/cron.log 2>&1

    # Or run continuously as a service (recommended):
    python orchestrator.py &
"""

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VAULT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = VAULT_ROOT / "config.yaml"
WATCHERS_DIR = VAULT_ROOT / "watchers"

# Processing cycle interval (seconds)
PROCESS_INTERVAL = 60

# Watcher definitions: name → script + args
WATCHER_DEFS = {
    "gmail_watcher": {
        "script": "gmail_watcher.py",
        "description": "Gmail API poller (120s cycle)",
        "enabled": True,
    },
    "whatsapp_watcher": {
        "script": "whatsapp_watcher.py",
        "description": "WhatsApp Web monitor (30s cycle)",
        "enabled": False,  # Requires Playwright + manual QR login
    },
    "linkedin_watcher": {
        "script": "linkedin_watcher.py",
        "description": "LinkedIn notifications/messages (120s cycle)",
        "enabled": False,  # Requires Playwright + manual login
    },
    "approval_loop": {
        "script": "approval_loop.py",
        "description": "HITL approval scanner (30s cycle)",
        "enabled": True,
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(vault_root: Path) -> logging.Logger:
    logs_dir = vault_root / "Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "orchestrator.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [orchestrator] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("orchestrator")


def log_activity(vault_root: Path, action: str, detail: str = "") -> None:
    logs_dir = vault_root / "Logs"
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = logs_dir / f"activity_{today}.log"
    ts = datetime.now().strftime("%H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{ts} | orchestrator | {action} | {detail}\n")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(config_path: Path = CONFIG_PATH) -> dict:
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# ---------------------------------------------------------------------------
# Watcher Subprocess Manager
# ---------------------------------------------------------------------------

class WatcherManager:
    """Manages watcher subprocesses lifecycle."""

    def __init__(self, vault_root: Path, logger: logging.Logger):
        self.vault_root = vault_root
        self.watchers_dir = vault_root / "watchers"
        self.logger = logger
        self.processes: dict[str, subprocess.Popen] = {}

    def start_watcher(self, name: str, definition: dict) -> bool:
        """Start a single watcher subprocess."""
        if not definition.get("enabled", False):
            self.logger.info("Watcher '%s' is disabled — skipping", name)
            return False

        if name in self.processes and self.processes[name].poll() is None:
            self.logger.debug("Watcher '%s' already running (PID %d)", name, self.processes[name].pid)
            return True

        script = self.watchers_dir / definition["script"]
        if not script.exists():
            self.logger.error("Watcher script not found: %s", script)
            return False

        cmd = [
            sys.executable, str(script),
            "--vault", str(self.vault_root),
        ]

        self.logger.info("Starting watcher: %s (%s)", name, definition["description"])

        try:
            log_file = self.vault_root / "Logs" / f"{name}_subprocess.log"
            fh = open(log_file, "a", encoding="utf-8")
            proc = subprocess.Popen(
                cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=str(self.watchers_dir),
            )
            self.processes[name] = proc
            self.logger.info("Started %s (PID %d)", name, proc.pid)
            return True
        except Exception:
            self.logger.exception("Failed to start watcher: %s", name)
            return False

    def start_all(self) -> dict[str, bool]:
        """Start all defined watchers. Returns name → success map."""
        results = {}
        for name, defn in WATCHER_DEFS.items():
            results[name] = self.start_watcher(name, defn)
        return results

    def check_health(self) -> dict[str, str]:
        """Check status of all watcher processes."""
        status = {}
        for name, defn in WATCHER_DEFS.items():
            if not defn.get("enabled"):
                status[name] = "disabled"
            elif name not in self.processes:
                status[name] = "not_started"
            else:
                proc = self.processes[name]
                if proc.poll() is None:
                    status[name] = f"running (PID {proc.pid})"
                else:
                    status[name] = f"exited (code {proc.returncode})"
        return status

    def restart_dead(self) -> list[str]:
        """Restart any watchers that have died."""
        restarted = []
        for name, defn in WATCHER_DEFS.items():
            if not defn.get("enabled"):
                continue
            if name in self.processes:
                proc = self.processes[name]
                if proc.poll() is not None:
                    self.logger.warning(
                        "Watcher '%s' died (code %d). Restarting ...",
                        name, proc.returncode
                    )
                    if self.start_watcher(name, defn):
                        restarted.append(name)
        return restarted

    def stop_all(self) -> None:
        """Gracefully stop all watcher subprocesses."""
        for name, proc in self.processes.items():
            if proc.poll() is None:
                self.logger.info("Stopping %s (PID %d) ...", name, proc.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.logger.warning("Force-killing %s", name)
                    proc.kill()
        self.processes.clear()


# ---------------------------------------------------------------------------
# Processing Engine
# ---------------------------------------------------------------------------

class ProcessingEngine:
    """Runs periodic processing cycles on the vault."""

    def __init__(self, vault_root: Path, logger: logging.Logger):
        self.vault_root = vault_root
        self.logger = logger
        self.needs_action_dir = vault_root / "Needs_Action"
        self.pending_dir = vault_root / "Pending_Approval"
        self.approved_dir = vault_root / "Approved"
        self.rejected_dir = vault_root / "Rejected"
        self.in_progress_dir = vault_root / "In_Progress"
        self.done_dir = vault_root / "Done"
        self.plans_dir = vault_root / "Plans"
        self.logs_dir = vault_root / "Logs"
        self.dashboard_path = vault_root / "Dashboard.md"

    def run_cycle(self) -> dict:
        """Run a single processing cycle. Returns summary."""
        self.logger.info("=== Processing cycle started ===")
        summary = {
            "needs_action": 0,
            "pending_approvals": 0,
            "approved_executed": 0,
            "expired": 0,
            "in_progress": 0,
            "done_today": 0,
            "plans": 0,
        }

        # 1. Count items in each folder
        summary["needs_action"] = self._count_md(self.needs_action_dir)
        summary["pending_approvals"] = self._count_md(self.pending_dir)
        summary["in_progress"] = self._count_md(self.in_progress_dir)
        summary["plans"] = self._count_md(self.plans_dir)
        today_prefix = datetime.now().strftime("%Y%m%d")
        summary["done_today"] = len(list(self.done_dir.glob(f"{today_prefix}*.md"))) + \
                                len(list(self.done_dir.glob("ACTION_*.md")))

        # 2. Process approved actions via HITL enforcer
        try:
            sys.path.insert(0, str(self.vault_root / "watchers"))
            from approval_loop import ApprovalLoop
            loop = ApprovalLoop(vault_path=str(self.vault_root))
            results = loop.scan_once()
            summary["approved_executed"] = results["approved_processed"]
            summary["expired"] = results["expired"]
            summary["pending_approvals"] = results["pending"]
        except Exception:
            self.logger.exception("Error running approval loop")

        # 3. Run vault processor (scan + dashboard update)
        try:
            from vault_processor import scan_vault, update_dashboard, \
                move_done_items, log_activity as vp_log
            scan = scan_vault(self.vault_root)
            update_dashboard(self.vault_root, scan)
            moved = move_done_items(self.vault_root, scan)
            vp_log(self.vault_root, "ORCHESTRATOR_CYCLE",
                   f"scanned={scan['counts']['total']} moved={moved}")
        except Exception:
            self.logger.exception("Error running vault processor")

        # 4. Update dashboard with orchestrator status
        self._update_dashboard_status(summary)

        self.logger.info(
            "Cycle complete: needs_action=%d, pending=%d, executed=%d, "
            "expired=%d, in_progress=%d",
            summary["needs_action"], summary["pending_approvals"],
            summary["approved_executed"], summary["expired"],
            summary["in_progress"],
        )

        return summary

    def generate_daily_summary(self) -> str:
        """Generate a daily summary report and save to Logs/."""
        self.logger.info("Generating daily summary ...")
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        today_prefix = now.strftime("%Y%m%d")

        # Count everything
        needs_action = self._count_md(self.needs_action_dir)
        pending = self._count_md(self.pending_dir)
        in_progress = self._count_md(self.in_progress_dir)
        done_today = len(list(self.done_dir.glob(f"{today_prefix}*.md"))) + \
                     len(list(self.done_dir.glob("ACTION_*.md")))
        total_done = self._count_md(self.done_dir)
        plans = self._count_md(self.plans_dir)

        # Read activity log
        activity_log = self.logs_dir / f"activity_{today}.log"
        activity_lines = []
        if activity_log.exists():
            activity_lines = activity_log.read_text(encoding="utf-8").strip().split("\n")

        # Parse priorities from Needs_Action
        priority_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for md in self.needs_action_dir.glob("*.md"):
            try:
                head = md.read_text(encoding="utf-8")[:500]
                for p in priority_counts:
                    if f"priority: {p}" in head:
                        priority_counts[p] += 1
                        break
            except OSError:
                pass

        summary = (
            f"---\n"
            f"type: daily_summary\n"
            f"date: {today}\n"
            f"created: {now.strftime('%Y-%m-%d %H:%M')}\n"
            f"---\n\n"
            f"# Daily Summary — {today}\n\n"
            f"## Queue Status\n\n"
            f"| Folder | Count |\n"
            f"|--------|-------|\n"
            f"| Needs_Action | {needs_action} |\n"
            f"| In_Progress | {in_progress} |\n"
            f"| Pending_Approval | {pending} |\n"
            f"| Plans | {plans} |\n"
            f"| Done (today) | {done_today} |\n"
            f"| Done (total) | {total_done} |\n\n"
            f"## Priority Breakdown (Needs_Action)\n\n"
            f"| Priority | Count |\n"
            f"|----------|-------|\n"
            f"| Critical | {priority_counts['critical']} |\n"
            f"| High | {priority_counts['high']} |\n"
            f"| Medium | {priority_counts['medium']} |\n"
            f"| Low | {priority_counts['low']} |\n\n"
            f"## Activity Log ({len(activity_lines)} entries)\n\n"
            f"```\n"
        )
        # Include last 20 activity lines
        for line in activity_lines[-20:]:
            summary += f"{line}\n"
        summary += (
            f"```\n\n"
            f"---\n\n"
            f"_Generated by orchestrator.py at {now.strftime('%H:%M')}_\n"
        )

        # Write summary file
        summary_path = self.logs_dir / f"daily_summary_{today}.md"
        summary_path.write_text(summary, encoding="utf-8")
        self.logger.info("Daily summary written to %s", summary_path.name)

        return str(summary_path)

    def _count_md(self, folder: Path) -> int:
        """Count .md files in a folder (excluding .gitkeep)."""
        if not folder.is_dir():
            return 0
        return len([f for f in folder.glob("*.md") if f.name != ".gitkeep"])

    def _update_dashboard_status(self, summary: dict) -> None:
        """Update Dashboard.md counters and orchestrator status."""
        if not self.dashboard_path.exists():
            return

        text = self.dashboard_path.read_text(encoding="utf-8")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Update timestamp
        text = re.sub(
            r">\s*\*\*Last Updated:\*\*.*",
            f"> **Last Updated:** {now}",
            text,
        )

        # Update counters
        text = re.sub(
            r"\*\*Items Needs_Action:\*\*\s*\d+",
            f"**Items Needs_Action:** {summary['needs_action']}",
            text,
        )
        text = re.sub(
            r"\*\*Items In_Progress:\*\*\s*\d+",
            f"**Items In_Progress:** {summary['in_progress']}",
            text,
        )
        text = re.sub(
            r"\*\*Items Pending Approval:\*\*\s*\d+",
            f"**Items Pending Approval:** {summary['pending_approvals']}",
            text,
        )
        text = re.sub(
            r"\*\*Completed Today:\*\*\s*\d+",
            f"**Completed Today:** {summary['done_today']}",
            text,
        )

        self.dashboard_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# System Status
# ---------------------------------------------------------------------------

def print_system_status(vault_root: Path):
    """Print full system status."""
    def count_md(folder):
        if not folder.is_dir():
            return 0
        return len([f for f in folder.glob("*.md") if f.name != ".gitkeep"])

    na = count_md(vault_root / "Needs_Action")
    ip = count_md(vault_root / "In_Progress")
    pa = count_md(vault_root / "Pending_Approval")
    ap = count_md(vault_root / "Approved")
    rj = count_md(vault_root / "Rejected")
    dn = count_md(vault_root / "Done")
    pl = count_md(vault_root / "Plans")
    ib = count_md(vault_root / "Inbox")

    mcp_ready = (vault_root / "email_mcp.mjs").exists()
    env_exists = (vault_root / ".env").exists()

    print()
    print("=" * 62)
    print("  PERSONAL AI EMPLOYEE — SILVER TIER — SYSTEM STATUS")
    print("=" * 62)
    print()
    print("  QUEUE STATUS")
    print(f"    Inbox:              {ib}")
    print(f"    Needs_Action:       {na}")
    print(f"    In_Progress:        {ip}")
    print(f"    Pending_Approval:   {pa}")
    print(f"    Approved:           {ap}")
    print(f"    Rejected:           {rj}")
    print(f"    Done:               {dn}")
    print(f"    Plans:              {pl}")
    print()
    print("  COMPONENTS")
    print(f"    Gmail Watcher:      {'ready' if (vault_root / 'watchers/gmail_watcher.py').exists() else 'missing'}")
    print(f"    WhatsApp Watcher:   {'ready' if (vault_root / 'watchers/whatsapp_watcher.py').exists() else 'missing'}")
    print(f"    LinkedIn Watcher:   {'ready' if (vault_root / 'watchers/linkedin_watcher.py').exists() else 'missing'}")
    print(f"    HITL Enforcer:      {'ready' if (vault_root / 'watchers/hitl_enforcer.py').exists() else 'missing'}")
    print(f"    Approval Loop:      {'ready' if (vault_root / 'watchers/approval_loop.py').exists() else 'missing'}")
    print(f"    Vault Processor:    {'ready' if (vault_root / 'watchers/vault_processor.py').exists() else 'missing'}")
    print(f"    Email MCP:          {'ready' if mcp_ready else 'missing'}")
    print(f"    .env config:        {'found' if env_exists else 'NOT FOUND — copy .env.example'}")
    print()

    # Watcher configs
    for name, defn in WATCHER_DEFS.items():
        status = "ENABLED" if defn["enabled"] else "disabled"
        print(f"    {name}: {status} — {defn['description']}")
    print()
    print("=" * 62)
    print()


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Master controller that runs the entire Personal AI Employee system."""

    def __init__(self, vault_root: Path, run_watchers: bool = True):
        self.vault_root = vault_root
        self.run_watchers = run_watchers
        self.logger = setup_logging(vault_root)
        self.watcher_mgr = WatcherManager(vault_root, self.logger) if run_watchers else None
        self.engine = ProcessingEngine(vault_root, self.logger)
        self._shutdown = False
        self._last_daily_summary = None

    def _handle_signal(self, signum, frame):
        self.logger.info("Shutdown signal received (signal %d)", signum)
        self._shutdown = True

    def run_once(self) -> dict:
        """Run a single processing cycle."""
        return self.engine.run_cycle()

    def run(self) -> None:
        """Run the full system: watchers + processing loop."""
        self.logger.info("=" * 60)
        self.logger.info("PERSONAL AI EMPLOYEE — ORCHESTRATOR STARTING")
        self.logger.info("Vault: %s", self.vault_root)
        self.logger.info("Watchers: %s", "enabled" if self.run_watchers else "disabled")
        self.logger.info("Process interval: %ds", PROCESS_INTERVAL)
        self.logger.info("=" * 60)

        log_activity(self.vault_root, "ORCHESTRATOR_START",
                     f"watchers={'on' if self.run_watchers else 'off'}")

        # Register signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # Start watchers
        if self.watcher_mgr:
            results = self.watcher_mgr.start_all()
            started = sum(1 for v in results.values() if v)
            self.logger.info("Watchers started: %d/%d", started, len(results))

        # Main loop
        cycle_count = 0
        try:
            while not self._shutdown:
                cycle_count += 1
                self.logger.info("--- Cycle #%d ---", cycle_count)

                # Processing cycle
                try:
                    summary = self.engine.run_cycle()
                    log_activity(
                        self.vault_root, "CYCLE",
                        f"#{cycle_count} na={summary['needs_action']} "
                        f"pending={summary['pending_approvals']} "
                        f"executed={summary['approved_executed']}"
                    )
                except Exception:
                    self.logger.exception("Error in processing cycle #%d", cycle_count)

                # Restart dead watchers
                if self.watcher_mgr:
                    restarted = self.watcher_mgr.restart_dead()
                    if restarted:
                        self.logger.info("Restarted watchers: %s", restarted)

                # Daily summary (once per day at first cycle after midnight)
                today = datetime.now().strftime("%Y-%m-%d")
                if self._last_daily_summary != today:
                    try:
                        self.engine.generate_daily_summary()
                        self._last_daily_summary = today
                    except Exception:
                        self.logger.exception("Error generating daily summary")

                # Health check logging every 10 cycles
                if self.watcher_mgr and cycle_count % 10 == 0:
                    health = self.watcher_mgr.check_health()
                    self.logger.info("Health: %s", health)

                # Sleep until next cycle
                for _ in range(PROCESS_INTERVAL):
                    if self._shutdown:
                        break
                    time.sleep(1)

        finally:
            self.logger.info("Shutting down orchestrator ...")
            if self.watcher_mgr:
                self.watcher_mgr.stop_all()
            log_activity(self.vault_root, "ORCHESTRATOR_STOP",
                         f"cycles={cycle_count}")
            self.logger.info("Orchestrator stopped after %d cycles.", cycle_count)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Personal AI Employee — Master Orchestrator (Silver Tier)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python orchestrator.py                  # Full system (watchers + loop)
  python orchestrator.py --no-watchers    # Processing only
  python orchestrator.py --once           # Single cycle
  python orchestrator.py --status         # System status
  python orchestrator.py --daily-summary  # Generate daily report

Cron (single cycle every hour):
  0 * * * * cd /d/Hackathon-0 && python orchestrator.py --once >> Logs/cron.log 2>&1

Cron (daily summary at 11pm):
  0 23 * * * cd /d/Hackathon-0 && python orchestrator.py --daily-summary >> Logs/cron.log 2>&1
        """,
    )
    parser.add_argument(
        "--vault", type=str, default=os.environ.get("VAULT_PATH"),
        help="Override vault root path",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single processing cycle and exit",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print system status and exit",
    )
    parser.add_argument(
        "--daily-summary", action="store_true",
        help="Generate daily summary and exit",
    )
    parser.add_argument(
        "--no-watchers", action="store_true",
        help="Run processing loop without starting watcher subprocesses",
    )
    parser.add_argument(
        "--enable", type=str, nargs="*",
        help="Enable specific watchers by name (e.g., --enable whatsapp_watcher linkedin_watcher)",
    )
    args = parser.parse_args()

    vault_root = Path(args.vault) if args.vault else VAULT_ROOT

    # Enable/disable watchers from CLI
    if args.enable:
        for name in args.enable:
            if name in WATCHER_DEFS:
                WATCHER_DEFS[name]["enabled"] = True
                print(f"Enabled: {name}")
            else:
                print(f"Unknown watcher: {name}")
                print(f"Available: {', '.join(WATCHER_DEFS.keys())}")

    if args.status:
        print_system_status(vault_root)
        return

    if args.daily_summary:
        logger = setup_logging(vault_root)
        engine = ProcessingEngine(vault_root, logger)
        path = engine.generate_daily_summary()
        print(f"Daily summary written to: {path}")
        return

    if args.once:
        orch = Orchestrator(vault_root, run_watchers=False)
        summary = orch.run_once()
        print(f"\nCycle complete:")
        print(f"  Needs_Action:     {summary['needs_action']}")
        print(f"  Pending_Approval: {summary['pending_approvals']}")
        print(f"  Approved (exec):  {summary['approved_executed']}")
        print(f"  Expired:          {summary['expired']}")
        print(f"  In_Progress:      {summary['in_progress']}")
        print(f"  Done Today:       {summary['done_today']}")
        return

    # Full system run
    orch = Orchestrator(vault_root, run_watchers=not args.no_watchers)
    orch.run()


if __name__ == "__main__":
    main()
