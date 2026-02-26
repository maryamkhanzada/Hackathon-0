"""
approval_loop.py — Continuous approval scanner for the Personal AI Employee.

Runs as a background process alongside the watchers. Every cycle it:
  1. Scans /Approved/ for files the human has approved → executes the action
  2. Scans /Rejected/ for files the human has rejected → logs and archives
  3. Scans /Pending_Approval/ for expired requests → moves to Rejected/
  4. Updates Dashboard.md with current approval status

This is the "execution arm" that completes the HITL loop.

Usage:
    python approval_loop.py              # Continuous (30s poll)
    python approval_loop.py --once       # Single scan
    python approval_loop.py --status     # Print status only
    python approval_loop.py --vault /path
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hitl_enforcer import HITLEnforcer

logger = logging.getLogger("approval_loop")

# Poll interval for the approval scanner
DEFAULT_POLL_INTERVAL = 30


class ApprovalLoop:
    """Scans approval folders and executes approved actions."""

    def __init__(self, vault_path: str | None = None):
        self.hitl = HITLEnforcer(vault_path=vault_path)
        self.vault_root = self.hitl.vault_root
        self.logs_dir = self.hitl.logs_dir
        self.poll_interval = DEFAULT_POLL_INTERVAL

        # Set up logging
        log_file = self.logs_dir / "approval_loop.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(),
            ],
        )

    # ------------------------------------------------------------------
    # Action executors (dispatch by action_type)
    # ------------------------------------------------------------------

    def _execute_action(self, meta: dict, filepath: Path) -> str:
        """Execute an approved action based on its type. Returns result string."""
        action_type = meta.get("action_type", "unknown")
        filename = filepath.name

        logger.info("Executing approved action: %s (%s)", action_type, filename)

        try:
            if action_type in ("email_send", "email_reply"):
                return self._execute_email(meta, filepath)
            elif action_type in ("social_post", "linkedin_post"):
                return self._execute_social_post(meta, filepath)
            elif action_type == "payment":
                return self._execute_payment(meta, filepath)
            elif action_type in ("linkedin_message", "whatsapp_send"):
                return self._execute_message(meta, filepath)
            else:
                logger.warning("No executor for action_type: %s", action_type)
                return f"no_executor_for_{action_type} — manual action required"
        except Exception as e:
            logger.exception("Error executing %s", filename)
            return f"error: {e}"

    def _execute_email(self, meta: dict, filepath: Path) -> str:
        """Execute an approved email send via the Email MCP server or log."""
        text = filepath.read_text(encoding="utf-8")

        # Extract email details from the file content
        # Try to use MCP if available, otherwise log for manual action
        mcp_script = self.vault_root / "email_mcp.mjs"

        if mcp_script.exists():
            # Log that MCP is available — actual MCP call happens via Claude Code
            logger.info(
                "Email MCP server available. The email will be sent when Claude Code "
                "invokes the send_email tool with the approved details."
            )
            return "mcp_ready — email queued for send_email tool invocation"
        else:
            logger.info("Email MCP not available. Logging for manual send.")
            return "manual_action_needed — send email manually"

    def _execute_social_post(self, meta: dict, filepath: Path) -> str:
        """Execute an approved social media post."""
        # Social posts require the LinkedIn watcher / Playwright to actually post
        # For now, log that it's approved and ready for posting
        logger.info("Social post approved. Ready for manual posting or Playwright execution.")
        return "approved_for_posting — execute via linkedin_watcher or manually"

    def _execute_payment(self, meta: dict, filepath: Path) -> str:
        """Execute an approved payment — ALWAYS requires manual confirmation."""
        text = filepath.read_text(encoding="utf-8")
        logger.info("Payment approved. MANUAL ACTION REQUIRED — process payment now.")
        return "payment_approved — manual processing required (no auto-payment)"

    def _execute_message(self, meta: dict, filepath: Path) -> str:
        """Execute an approved direct message (WhatsApp/LinkedIn)."""
        logger.info("Message approved. Ready for manual sending or Playwright execution.")
        return "message_approved — send via appropriate platform"

    # ------------------------------------------------------------------
    # Main scan cycle
    # ------------------------------------------------------------------

    def scan_once(self) -> dict:
        """Run a single scan cycle. Returns summary dict."""
        results = {
            "approved_processed": 0,
            "rejected_processed": 0,
            "expired": 0,
            "pending": 0,
            "details": [],
        }

        # 1. Process approved actions
        approved = self.hitl.get_approved()
        for item in approved:
            filename = item["filename"]
            filepath = item["filepath"]
            meta = item

            result = self._execute_action(meta, filepath)
            self.hitl.mark_executed(filename, result=result)
            results["approved_processed"] += 1
            results["details"].append({
                "action": "executed",
                "file": filename,
                "type": meta.get("action_type", "?"),
                "result": result,
            })
            logger.info("Executed + archived: %s → %s", filename, result)

        # 2. Process rejected actions
        rejected = self.hitl.get_rejected()
        for item in rejected:
            filename = item["filename"]
            # Only process if not already annotated with rejection log
            text = item["filepath"].read_text(encoding="utf-8")
            if "## Rejection Log" not in text:
                self.hitl.mark_rejected(filename, reason="human rejected")
                results["rejected_processed"] += 1
                results["details"].append({
                    "action": "rejected",
                    "file": filename,
                    "type": item.get("action_type", "?"),
                })
                logger.info("Rejection processed: %s", filename)

        # 3. Expire stale pending requests
        expired_files = self.hitl.expire_stale()
        results["expired"] = len(expired_files)
        for f in expired_files:
            results["details"].append({"action": "expired", "file": f})

        # 4. Count remaining pending
        pending = self.hitl.get_pending()
        active_pending = [p for p in pending if not p.get("is_expired")]
        results["pending"] = len(active_pending)

        # 5. Update dashboard
        self.hitl.update_dashboard_approvals()

        # 6. Log summary
        if any(results[k] for k in ("approved_processed", "rejected_processed", "expired")):
            self._log_cycle(results)

        return results

    def _log_cycle(self, results: dict) -> None:
        """Log scan cycle results to the daily log."""
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.logs_dir / f"activity_{today}.log"
        ts = datetime.now().strftime("%H:%M:%S")
        summary = (
            f"approved={results['approved_processed']} "
            f"rejected={results['rejected_processed']} "
            f"expired={results['expired']} "
            f"pending={results['pending']}"
        )
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{ts} | approval_loop | SCAN_CYCLE | {summary}\n")

    # ------------------------------------------------------------------
    # Print status
    # ------------------------------------------------------------------

    def print_status(self) -> None:
        """Print current status of all approval folders."""
        pending = self.hitl.get_pending()
        approved = self.hitl.get_approved()
        rejected = self.hitl.get_rejected()
        active = [p for p in pending if not p.get("is_expired")]
        expired = [p for p in pending if p.get("is_expired")]

        print("=" * 60)
        print("HITL APPROVAL STATUS")
        print("=" * 60)
        print(f"  Pending:  {len(active)}")
        print(f"  Expired:  {len(expired)}")
        print(f"  Approved: {len(approved)} (awaiting execution)")
        print(f"  Rejected: {len(rejected)}")
        print()

        if active:
            print("--- PENDING (awaiting your decision) ---")
            for p in active:
                action = p.get("action_type", "?").replace("_", " ").title()
                priority = p.get("priority", "?").upper()
                expires = p.get("expires", "?")
                print(f"  [{priority}] {p['filename']}")
                print(f"         Action: {action} | Expires: {expires}")
            print()

        if approved:
            print("--- APPROVED (will execute on next cycle) ---")
            for a in approved:
                print(f"  → {a['filename']}")
            print()

        if expired:
            print("--- EXPIRED (will be auto-rejected) ---")
            for e in expired:
                print(f"  ✗ {e['filename']}")
            print()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Continuous approval scanning loop."""
        logger.info("Starting approval loop (poll every %ds)", self.poll_interval)

        try:
            while True:
                try:
                    results = self.scan_once()
                    acted = (
                        results["approved_processed"]
                        + results["rejected_processed"]
                        + results["expired"]
                    )
                    if acted:
                        logger.info(
                            "Cycle: %d approved, %d rejected, %d expired, %d pending",
                            results["approved_processed"],
                            results["rejected_processed"],
                            results["expired"],
                            results["pending"],
                        )
                except KeyboardInterrupt:
                    raise
                except Exception:
                    logger.exception("Error during approval scan cycle")

                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("Approval loop stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Approval Loop Scanner — Personal AI Employee HITL"
    )
    parser.add_argument("--vault", type=str, default=os.environ.get("VAULT_PATH"))
    parser.add_argument("--once", action="store_true", help="Single scan cycle")
    parser.add_argument("--status", action="store_true", help="Print status only")
    args = parser.parse_args()

    loop = ApprovalLoop(vault_path=args.vault)

    if args.status:
        loop.print_status()
    elif args.once:
        results = loop.scan_once()
        print(f"Scan complete: {results['approved_processed']} executed, "
              f"{results['rejected_processed']} rejected, "
              f"{results['expired']} expired, "
              f"{results['pending']} pending")
    else:
        loop.run()


if __name__ == "__main__":
    main()
