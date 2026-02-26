"""
hitl_enforcer.py — Reusable Human-in-the-Loop enforcement module.

Any skill or watcher can import this module to:
  1. Create approval requests in Pending_Approval/
  2. Check if an action was approved or rejected
  3. Process the approval/rejection (execute, log, archive)

This is the SINGLE gate through which ALL external actions must pass.
No email, social post, payment, or API call happens without a file
appearing in Approved/.

Usage:
    from hitl_enforcer import HITLEnforcer

    hitl = HITLEnforcer(vault_path="/path/to/vault")

    # Request approval (blocks the action)
    req_path = hitl.request_approval(
        action_type="email_send",
        details={
            "to": "client@example.com",
            "subject": "Proposal",
            "body_preview": "Dear Client, please find...",
        },
        reason="Replying to sales lead from LinkedIn",
        priority="high",
        source_skill="gmail_watcher",
    )

    # Later — check if approved
    status = hitl.check_status(req_path.name)
    # Returns: "pending" | "approved" | "rejected" | "expired"
"""

import hashlib
import logging
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger("hitl_enforcer")

# Actions that ALWAYS require approval (Company Handbook rules)
SENSITIVE_ACTIONS = {
    "email_send",
    "email_reply",
    "social_post",
    "linkedin_post",
    "linkedin_message",
    "whatsapp_send",
    "payment",
    "api_call_external",
    "file_delete",
    "contact_new",
    "bulk_action",
}

DEFAULT_EXPIRY_HOURS = 24


class HITLEnforcer:
    """Human-in-the-loop enforcement engine for the Personal AI Employee."""

    def __init__(self, vault_path: str | Path | None = None):
        if vault_path:
            self.vault_root = Path(vault_path)
        else:
            self.vault_root = Path(__file__).resolve().parent.parent

        self.pending_dir = self.vault_root / "Pending_Approval"
        self.approved_dir = self.vault_root / "Approved"
        self.rejected_dir = self.vault_root / "Rejected"
        self.done_dir = self.vault_root / "Done"
        self.logs_dir = self.vault_root / "Logs"
        self.dashboard_path = self.vault_root / "Dashboard.md"

        # Ensure all directories exist
        for d in (self.pending_dir, self.approved_dir, self.rejected_dir,
                  self.done_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Set up logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

    # ------------------------------------------------------------------
    # Core: Request Approval
    # ------------------------------------------------------------------

    def request_approval(
        self,
        action_type: str,
        details: dict,
        reason: str,
        priority: str = "medium",
        source_skill: str = "agent",
        expiry_hours: int = DEFAULT_EXPIRY_HOURS,
        related_plan: str | None = None,
    ) -> Path:
        """Create an approval request file in Pending_Approval/.

        Args:
            action_type: Type of action (email_send, payment, social_post, etc.)
            details: Dict with action-specific fields (to, subject, amount, etc.)
            reason: Why this action is being requested
            priority: critical | high | medium | low
            source_skill: Which skill/watcher triggered this
            expiry_hours: Hours until the request expires (default 24)
            related_plan: Optional path to a related plan file

        Returns:
            Path to the created approval request file.
        """
        if action_type not in SENSITIVE_ACTIONS:
            logger.warning(
                "Action type '%s' not in SENSITIVE_ACTIONS list. "
                "Creating approval request anyway for safety.", action_type
            )

        now = datetime.now()
        ts = now.strftime("%Y%m%d_%H%M%S")
        expires = (now + timedelta(hours=expiry_hours)).strftime("%Y-%m-%d %H:%M")
        created_str = now.strftime("%Y-%m-%d %H:%M")

        # Generate unique ID
        id_seed = f"{action_type}:{ts}:{reason}"
        req_id = hashlib.sha256(id_seed.encode()).hexdigest()[:12]

        # Build filename
        safe_type = re.sub(r"[^a-zA-Z0-9_]", "", action_type)
        filename = f"ACTION_{safe_type}_{ts}.md"
        filepath = self.pending_dir / filename

        # Build detail lines for markdown
        detail_lines = []
        for key, value in details.items():
            display_key = key.replace("_", " ").title()
            # Truncate long values
            display_val = str(value)
            if len(display_val) > 500:
                display_val = display_val[:500] + "..."
            detail_lines.append(f"| **{display_key}** | {display_val} |")
        detail_table = "\n".join(detail_lines)

        # Payment flag
        amount = details.get("amount", "")
        payment_warning = ""
        if amount:
            try:
                amt_num = float(str(amount).replace("$", "").replace(",", ""))
                if amt_num > 100:
                    payment_warning = (
                        "\n> **COMPANY HANDBOOK ALERT:** This payment exceeds $100. "
                        "Manual verification required.\n"
                    )
            except (ValueError, TypeError):
                pass

        # Build YAML frontmatter
        fm_dict = {
            "id": req_id,
            "type": "approval_request",
            "action_type": action_type,
            "status": "pending_approval",
            "priority": priority,
            "created": created_str,
            "expires": expires,
            "source_skill": source_skill,
            "tags": ["#approval", "#HITL", f"#action-{safe_type}", f"#priority-{priority}"],
        }
        if related_plan:
            fm_dict["related_plan"] = related_plan

        # Manually build YAML for clean output
        tags_str = ", ".join(f'"{t}"' for t in fm_dict["tags"])
        fm_lines = [
            "---",
            f"id: {fm_dict['id']}",
            f"type: {fm_dict['type']}",
            f"action_type: {fm_dict['action_type']}",
            f"status: {fm_dict['status']}",
            f"priority: {fm_dict['priority']}",
            f"created: {fm_dict['created']}",
            f"expires: {fm_dict['expires']}",
            f"source_skill: {fm_dict['source_skill']}",
            f"tags: [{tags_str}]",
        ]
        if related_plan:
            fm_lines.append(f"related_plan: {related_plan}")
        fm_lines.append("---")

        content = (
            "\n".join(fm_lines) + "\n\n"
            f"# Approval Request: {action_type.replace('_', ' ').title()}\n\n"
            f"**Priority:** {priority}  \n"
            f"**Requested by:** {source_skill}  \n"
            f"**Created:** {created_str}  \n"
            f"**Expires:** {expires}  \n\n"
            f"{payment_warning}"
            f"---\n\n"
            f"## Action Details\n\n"
            f"| Field | Value |\n"
            f"|-------|-------|\n"
            f"{detail_table}\n\n"
            f"**Reason:** {reason}\n\n"
            f"---\n\n"
            f"## To Approve\n\n"
            f"Move this file to **`/Approved/`** folder.\n\n"
            f"```\n"
            f"Pending_Approval/{filename}  →  Approved/{filename}\n"
            f"```\n\n"
            f"## To Reject\n\n"
            f"Move this file to **`/Rejected/`** folder.\n\n"
            f"```\n"
            f"Pending_Approval/{filename}  →  Rejected/{filename}\n"
            f"```\n\n"
            f"---\n\n"
            f"The agent will **NOT** execute this action until the file is moved.\n"
            f"If no action is taken by **{expires}**, this request expires automatically.\n"
        )

        filepath.write_text(content, encoding="utf-8")
        logger.info("Approval request created: %s", filename)

        # Log the action
        self._log(
            "APPROVAL_REQUESTED",
            f"action={action_type} file={filename} priority={priority} "
            f"expires={expires} source={source_skill}",
        )

        return filepath

    # ------------------------------------------------------------------
    # Core: Check Status
    # ------------------------------------------------------------------

    def check_status(self, filename: str) -> str:
        """Check the status of an approval request by filename.

        Returns: 'pending' | 'approved' | 'rejected' | 'expired' | 'not_found'
        """
        if (self.approved_dir / filename).exists():
            return "approved"
        if (self.rejected_dir / filename).exists():
            return "rejected"
        if (self.pending_dir / filename).exists():
            # Check expiry
            filepath = self.pending_dir / filename
            try:
                text = filepath.read_text(encoding="utf-8")
                match = re.search(r"expires:\s*(.+)", text)
                if match:
                    expiry_str = match.group(1).strip()
                    expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M")
                    if datetime.now() > expiry_dt:
                        return "expired"
            except Exception:
                pass
            return "pending"
        return "not_found"

    # ------------------------------------------------------------------
    # Core: Process Approved Actions
    # ------------------------------------------------------------------

    def get_approved(self) -> list[dict]:
        """Scan Approved/ folder and return list of approved requests with metadata."""
        approved = []
        for filepath in sorted(self.approved_dir.glob("ACTION_*.md")):
            meta = self._parse_frontmatter(filepath)
            meta["filepath"] = filepath
            meta["filename"] = filepath.name
            approved.append(meta)
        return approved

    def get_rejected(self) -> list[dict]:
        """Scan Rejected/ folder and return list of rejected requests."""
        rejected = []
        for filepath in sorted(self.rejected_dir.glob("ACTION_*.md")):
            meta = self._parse_frontmatter(filepath)
            meta["filepath"] = filepath
            meta["filename"] = filepath.name
            rejected.append(meta)
        return rejected

    def get_pending(self) -> list[dict]:
        """Scan Pending_Approval/ for all pending requests."""
        pending = []
        for filepath in sorted(self.pending_dir.glob("ACTION_*.md")):
            meta = self._parse_frontmatter(filepath)
            meta["filepath"] = filepath
            meta["filename"] = filepath.name
            # Check expiry
            expires_str = meta.get("expires", "")
            if expires_str:
                try:
                    expiry_dt = datetime.strptime(expires_str, "%Y-%m-%d %H:%M")
                    meta["is_expired"] = datetime.now() > expiry_dt
                except Exception:
                    meta["is_expired"] = False
            else:
                meta["is_expired"] = False
            pending.append(meta)
        return pending

    def get_expired(self) -> list[dict]:
        """Return pending requests that have expired."""
        return [p for p in self.get_pending() if p.get("is_expired")]

    # ------------------------------------------------------------------
    # Core: Complete (archive after execution)
    # ------------------------------------------------------------------

    def mark_executed(self, filename: str, result: str = "success") -> Path | None:
        """Move an approved file to Done/ after execution. Log the result."""
        src = self.approved_dir / filename
        if not src.exists():
            logger.warning("Cannot mark executed — file not in Approved/: %s", filename)
            return None

        # Add execution metadata to the file
        text = src.read_text(encoding="utf-8")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        execution_note = (
            f"\n\n---\n\n"
            f"## Execution Log\n\n"
            f"- **Executed at:** {now}\n"
            f"- **Result:** {result}\n"
            f"- **Archived to:** Done/{filename}\n"
        )
        text += execution_note

        dst = self.done_dir / filename
        dst.write_text(text, encoding="utf-8")
        src.unlink()

        logger.info("Action executed and archived: %s → Done/", filename)
        self._log("ACTION_EXECUTED", f"file={filename} result={result}")
        return dst

    def mark_rejected(self, filename: str, reason: str = "human rejected") -> Path | None:
        """Process a rejected file — log and keep in Rejected/."""
        filepath = self.rejected_dir / filename
        if not filepath.exists():
            logger.warning("Cannot process rejection — file not in Rejected/: %s", filename)
            return None

        # Add rejection note
        text = filepath.read_text(encoding="utf-8")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        rejection_note = (
            f"\n\n---\n\n"
            f"## Rejection Log\n\n"
            f"- **Rejected at:** {now}\n"
            f"- **Reason:** {reason}\n"
            f"- **Action:** NOT executed\n"
        )
        text += rejection_note
        filepath.write_text(text, encoding="utf-8")

        logger.info("Rejection processed: %s", filename)
        self._log("ACTION_REJECTED", f"file={filename} reason={reason}")
        return filepath

    def expire_stale(self) -> list[str]:
        """Move expired pending requests to Rejected/ with expiry reason."""
        expired_files = []
        for item in self.get_expired():
            filepath = item["filepath"]
            dst = self.rejected_dir / filepath.name
            shutil.move(str(filepath), str(dst))
            self.mark_rejected(filepath.name, reason="expired — no response within deadline")
            expired_files.append(filepath.name)
            logger.info("Expired request moved to Rejected/: %s", filepath.name)
        return expired_files

    # ------------------------------------------------------------------
    # Dashboard integration
    # ------------------------------------------------------------------

    def update_dashboard_approvals(self) -> None:
        """Update the 'Awaiting Your Approval' section in Dashboard.md."""
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

        # Build approval notification block
        pending = self.get_pending()
        active_pending = [p for p in pending if not p.get("is_expired")]

        if active_pending:
            lines = []
            for p in active_pending:
                action = p.get("action_type", "unknown").replace("_", " ").title()
                priority = p.get("priority", "medium")
                filename = p.get("filename", "?")
                expires = p.get("expires", "?")
                icon = {"critical": "!!!", "high": "!!"}.get(priority, "")
                lines.append(
                    f"- {icon} **[{priority.upper()}]** {action} — "
                    f"`{filename}` (expires {expires})"
                )
            approval_block = "\n".join(lines)
        else:
            approval_block = "- _No pending approvals._"

        # Replace or insert the awaiting section
        section_header = "## Awaiting Your Approval"
        if section_header in text:
            # Replace existing section content (up to next ## or ---)
            text = re.sub(
                r"## Awaiting Your Approval\n.*?(?=\n## |\n---)",
                f"## Awaiting Your Approval\n\n{approval_block}\n\n",
                text,
                flags=re.DOTALL,
            )
        else:
            # Insert after the Status line
            insert_point = text.find("\n---\n")
            if insert_point > 0:
                text = (
                    text[:insert_point] +
                    f"\n\n{section_header}\n\n{approval_block}\n" +
                    text[insert_point:]
                )

        # Update pending approval count
        text = re.sub(
            r"\*\*Items Pending Approval:\*\*\s*\d+",
            f"**Items Pending Approval:** {len(active_pending)}",
            text,
        )

        self.dashboard_path.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------------
    # Guard function — call this before ANY external action
    # ------------------------------------------------------------------

    def guard(
        self,
        action_type: str,
        details: dict,
        reason: str,
        **kwargs,
    ) -> dict:
        """The primary HITL gate. Call this before any sensitive action.

        Returns:
            dict with:
                "allowed": bool — True only if an approved file exists
                "status": str — pending | approved | rejected | expired | new
                "approval_file": str — filename of the approval request
                "filepath": Path — full path to the file

        Workflow:
            1. First call → creates approval request, returns allowed=False
            2. Subsequent calls → checks status, returns allowed=True only if approved
        """
        # Check if there's already an approval request for this action
        # by searching pending/approved/rejected for matching action_type + details
        existing = self._find_existing_request(action_type, details)

        if existing:
            filename = existing["filename"]
            status = self.check_status(filename)

            if status == "approved":
                return {
                    "allowed": True,
                    "status": "approved",
                    "approval_file": filename,
                    "filepath": self.approved_dir / filename,
                }
            elif status == "rejected":
                return {
                    "allowed": False,
                    "status": "rejected",
                    "approval_file": filename,
                    "filepath": self.rejected_dir / filename,
                }
            elif status == "expired":
                return {
                    "allowed": False,
                    "status": "expired",
                    "approval_file": filename,
                    "filepath": self.pending_dir / filename,
                }
            else:
                return {
                    "allowed": False,
                    "status": "pending",
                    "approval_file": filename,
                    "filepath": self.pending_dir / filename,
                }

        # No existing request → create one
        filepath = self.request_approval(
            action_type=action_type,
            details=details,
            reason=reason,
            **kwargs,
        )
        self.update_dashboard_approvals()

        return {
            "allowed": False,
            "status": "new",
            "approval_file": filepath.name,
            "filepath": filepath,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_existing_request(self, action_type: str, details: dict) -> dict | None:
        """Look for an existing request matching action_type in all folders."""
        safe_type = re.sub(r"[^a-zA-Z0-9_]", "", action_type)
        pattern = f"ACTION_{safe_type}_*.md"

        for folder in (self.pending_dir, self.approved_dir, self.rejected_dir):
            for filepath in folder.glob(pattern):
                meta = self._parse_frontmatter(filepath)
                if meta.get("action_type") == action_type:
                    # Verify it matches by checking key details
                    text = filepath.read_text(encoding="utf-8")
                    match_score = 0
                    for key, val in details.items():
                        if str(val)[:50] in text:
                            match_score += 1
                    # If at least half the details match, it's the same request
                    if match_score >= len(details) / 2:
                        return {"filepath": filepath, "filename": filepath.name, **meta}
        return None

    @staticmethod
    def _parse_frontmatter(filepath: Path) -> dict:
        """Extract YAML frontmatter from a markdown file."""
        try:
            text = filepath.read_text(encoding="utf-8")
            match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            if match:
                return yaml.safe_load(match.group(1)) or {}
        except Exception:
            pass
        return {}

    def _log(self, action: str, detail: str = "") -> None:
        """Append to daily activity log."""
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.logs_dir / f"activity_{today}.log"
        ts = datetime.now().strftime("%H:%M:%S")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{ts} | hitl_enforcer | {action} | {detail}\n")


# ---------------------------------------------------------------------------
# Convenience: module-level singleton
# ---------------------------------------------------------------------------

_default_enforcer: HITLEnforcer | None = None


def get_enforcer(vault_path: str | Path | None = None) -> HITLEnforcer:
    """Get or create the default HITLEnforcer singleton."""
    global _default_enforcer
    if _default_enforcer is None:
        _default_enforcer = HITLEnforcer(vault_path=vault_path)
    return _default_enforcer


# ---------------------------------------------------------------------------
# CLI: self-test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HITL Enforcer — test & manage approvals")
    parser.add_argument("--vault", type=str, default=None, help="Override vault root path")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show all pending/approved/rejected counts")
    sub.add_parser("expire", help="Expire stale pending requests")
    demo_p = sub.add_parser("demo", help="Create a demo approval request")
    demo_p.add_argument("--type", default="email_send", help="Action type")

    args = parser.parse_args()
    hitl = HITLEnforcer(vault_path=args.vault)

    if args.command == "status":
        pending = hitl.get_pending()
        approved = hitl.get_approved()
        rejected = hitl.get_rejected()
        expired = hitl.get_expired()
        print(f"Pending:  {len(pending)} ({len(expired)} expired)")
        print(f"Approved: {len(approved)}")
        print(f"Rejected: {len(rejected)}")
        for p in pending:
            exp = " [EXPIRED]" if p.get("is_expired") else ""
            print(f"  - [{p.get('priority','?').upper()}] {p['filename']}{exp}")

    elif args.command == "expire":
        expired = hitl.expire_stale()
        print(f"Expired {len(expired)} request(s).")
        for f in expired:
            print(f"  → {f}")

    elif args.command == "demo":
        result = hitl.guard(
            action_type=args.type,
            details={
                "to": "demo@example.com",
                "subject": "Test HITL Enforcement",
                "body_preview": "This is a test of the HITL approval system.",
            },
            reason="Demo/test of HITL enforcement module",
            priority="medium",
            source_skill="hitl_enforcer_demo",
        )
        print(f"Guard result: allowed={result['allowed']}, status={result['status']}")
        print(f"Approval file: {result['approval_file']}")

    else:
        parser.print_help()
