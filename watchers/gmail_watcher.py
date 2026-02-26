"""
gmail_watcher.py — Silver Tier Gmail Watcher for Personal AI Employee.

Polls the Gmail API for unread important messages, extracts headers + snippet,
generates suggested actions, and writes structured Markdown action files
into the vault's Needs_Action/ folder as EMAIL_{msg_id}.md.

Setup:
    1. Enable Gmail API in Google Cloud Console.
    2. Create OAuth 2.0 Desktop credentials -> download as credentials.json.
    3. Place credentials.json at the path specified in config.yaml.
    4. First run will open a browser for consent -> saves token.json.
    5. pip install google-auth google-auth-oauthlib google-api-python-client pyyaml

Usage:
    python gmail_watcher.py              # Run continuous watcher (120s poll)
    python gmail_watcher.py --once       # Single check cycle then exit
    python gmail_watcher.py --demo       # Use fake data (no credentials needed)
    python gmail_watcher.py --vault /path/to/vault   # Override vault path
"""

import argparse
import base64
import email.utils
import html
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from base_watcher import BaseWatcher

# Gmail API scope — read-only access to messages + labels.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Default polling interval (seconds).
DEFAULT_POLL_INTERVAL = 120

# Maximum emails to fetch per poll cycle.
MAX_RESULTS = 10

# Keywords that trigger high/critical priority.
URGENT_KEYWORDS = ["urgent", "asap", "critical", "action required", "invoice", "payment"]

logger = logging.getLogger(__name__)


class GmailWatcher(BaseWatcher):
    """Watches Gmail for unread important messages via the Gmail API.

    Creates EMAIL_{msg_id}.md action files in Needs_Action/ with:
    - YAML frontmatter: type, from, subject, received, priority, status
    - Email snippet/body
    - Suggested actions checkboxes
    """

    def __init__(self, config_path: Path | None = None, vault_path: str | None = None):
        kw = {"config_path": config_path} if config_path else {}
        super().__init__(name="gmail_watcher", **kw)

        # Override vault path from CLI/env if provided
        if vault_path:
            self.vault_root = Path(vault_path)
            self.needs_action_dir = self.vault_root / "Needs_Action"
            self.done_dir = self.vault_root / "Done"
            self.logs_dir = self.vault_root / "Logs"
            self.inbox_dir = self.vault_root / "Inbox"

        gmail_cfg = self.config.get("gmail", {})
        self.credentials_path = Path(
            os.environ.get(
                "GMAIL_CREDENTIALS_PATH",
                gmail_cfg.get("credentials_path", self.vault_root / "credentials.json"),
            )
        )
        self.token_path = Path(
            os.environ.get(
                "GMAIL_TOKEN_PATH",
                gmail_cfg.get("token_path", self.vault_root / "token.json"),
            )
        )
        # Silver Tier: query is:unread is:important
        self.query: str = gmail_cfg.get("query", "is:unread is:important")
        self.max_results: int = gmail_cfg.get("max_results", MAX_RESULTS)
        self.poll_interval = gmail_cfg.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)

        # Dedup tracking: set of processed message IDs (in-memory)
        self._processed_ids: set[str] = set()

        self.service = None  # lazily built

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def _authenticate(self) -> Credentials:
        """Return valid credentials, refreshing or running OAuth flow as needed."""
        creds = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                self.logger.info("Refreshing expired token ...")
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    self.logger.error(
                        "credentials.json not found at %s", self.credentials_path,
                    )
                    self.logger.error("")
                    self.logger.error("=== HOW TO FIX ===")
                    self.logger.error("Option A: Run with --demo flag (no Google account needed):")
                    self.logger.error("    python gmail_watcher.py --demo")
                    self.logger.error("")
                    self.logger.error("Option B: Set up real Gmail credentials:")
                    self.logger.error("  1. Go to https://console.cloud.google.com/")
                    self.logger.error("  2. Enable Gmail API")
                    self.logger.error("  3. Create OAuth 2.0 Desktop credentials")
                    self.logger.error("  4. Download JSON -> save as %s", self.credentials_path)
                    self.logger.error("  5. Full guide: Skills/SKILL_gmail_watcher.md")
                    sys.exit(1)
                self.logger.info("Running OAuth consent flow (browser will open) ...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Persist for next run.
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
            self.logger.info("Token saved to %s", self.token_path)

        return creds

    def _get_service(self):
        """Build (or return cached) Gmail API service."""
        if self.service is None:
            creds = self._authenticate()
            self.service = build("gmail", "v1", credentials=creds)
        return self.service

    # ------------------------------------------------------------------
    # Gmail helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_body(payload: dict) -> str:
        """Recursively extract the plain-text body from a Gmail payload."""
        if payload.get("mimeType", "").startswith("text/plain"):
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        parts = payload.get("parts", [])
        plain = ""
        html_body = ""
        for part in parts:
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    plain = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            elif mime == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    raw = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    html_body = GmailWatcher._html_to_plain(raw)
            elif mime.startswith("multipart/"):
                nested = GmailWatcher._decode_body(part)
                if nested:
                    plain = plain or nested

        return plain or html_body or "_No readable body._"

    @staticmethod
    def _html_to_plain(raw_html: str) -> str:
        """Rough HTML->plain-text conversion (no external deps)."""
        text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _get_header(headers: list[dict], name: str) -> str:
        """Extract a header value by name."""
        for h in headers:
            if h.get("name", "").lower() == name.lower():
                return h.get("value", "")
        return ""

    def _classify_priority(self, headers: list[dict], label_ids: list[str]) -> str:
        """Heuristic priority based on labels, subject, and urgent keywords."""
        subject = self._get_header(headers, "Subject").lower()

        # Critical: urgent keywords in subject
        if any(w in subject for w in ("urgent", "asap", "critical")):
            return "critical"

        # High: IMPORTANT label or action-required keywords
        if "IMPORTANT" in label_ids:
            return "high"
        if any(w in subject for w in ("action required", "invoice", "payment")):
            return "high"

        # Low: promotions/social
        if "CATEGORY_PROMOTIONS" in label_ids or "CATEGORY_SOCIAL" in label_ids:
            return "low"

        return "medium"

    def _suggest_actions(self, headers: list[dict], priority: str, subject: str) -> list[str]:
        """Generate suggested actions based on email metadata."""
        sender = self._get_header(headers, "From")
        actions = []

        if priority in ("high", "critical"):
            actions.append(f"Reply to {sender}")

        actions.append("Read full email in Gmail")
        actions.append("Archive if no action needed")

        subj_lower = subject.lower()
        if any(w in subj_lower for w in ("meeting", "invite", "calendar")):
            actions.append("Check calendar for conflicts")
        if any(w in subj_lower for w in ("invoice", "payment", "receipt", "bill")):
            actions.append("Log in finance tracker")
        if any(w in subj_lower for w in ("deadline", "due", "overdue")):
            actions.append("Update project timeline")

        return actions

    # ------------------------------------------------------------------
    # Core: check_for_updates (implements BaseWatcher.check)
    # ------------------------------------------------------------------

    def check_for_updates(self) -> list[dict]:
        """Poll Gmail for unread important messages. Alias for check()."""
        return self.check()

    def check(self) -> list[dict]:
        """Poll Gmail for unread important messages and return structured items."""
        service = self._get_service()

        self.logger.info("Querying Gmail: %s", self.query)
        results = (
            service.users()
            .messages()
            .list(userId="me", q=self.query, maxResults=self.max_results)
            .execute()
        )
        messages = results.get("messages", [])

        if not messages:
            self.logger.info("No new messages.")
            return []

        self.logger.info("Found %d message(s) to process.", len(messages))
        items: list[dict] = []

        for msg_stub in messages:
            msg_id = msg_stub["id"]

            # Skip already-processed IDs (in-memory dedup)
            if msg_id in self._processed_ids:
                self.logger.debug("Skipping already-processed: %s", msg_id)
                continue

            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=msg_id, format="full")
                    .execute()
                )
            except Exception:
                self.logger.exception("Failed to fetch message %s", msg_id)
                continue

            headers = msg.get("payload", {}).get("headers", [])
            label_ids = msg.get("labelIds", [])
            snippet = msg.get("snippet", "")

            sender = self._get_header(headers, "From")
            subject = self._get_header(headers, "Subject") or "(no subject)"
            date_raw = self._get_header(headers, "Date")
            to = self._get_header(headers, "To")

            # Parse date
            try:
                parsed = email.utils.parsedate_to_datetime(date_raw)
                date_str = parsed.strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_str = date_raw

            # Decode full body
            body_text = self._decode_body(msg.get("payload", {}))
            if len(body_text) > 3000:
                body_text = body_text[:3000] + "\n\n_... (truncated -- open in Gmail for full text)_"

            priority = self._classify_priority(headers, label_ids)
            actions = self._suggest_actions(headers, priority, subject)

            # Build body markdown with snippet
            body_md = (
                f"**From:** {sender}  \n"
                f"**To:** {to}  \n"
                f"**Date:** {date_str}  \n"
                f"**Subject:** {subject}  \n"
                f"**Labels:** {', '.join(label_ids)}\n\n"
                f"### Snippet\n\n"
                f"> {snippet}\n\n"
                f"---\n\n"
                f"### Full Body\n\n"
                f"{body_text}"
            )

            # Extra frontmatter fields required by Silver Tier
            extra_fm = {
                "type": "email",
                "from": sender,
                "subject": subject,
                "received": date_str,
            }

            items.append(
                {
                    "id": f"gmail_{msg_id}",
                    "title": f"Email: {subject}",
                    "body": body_md,
                    "priority": priority,
                    "tags": ["#email", "#gmail", f"#priority-{priority}"],
                    "actions": actions,
                    "_extra_frontmatter": extra_fm,
                }
            )

            # Mark as processed
            self._processed_ids.add(msg_id)

        return items

    # ------------------------------------------------------------------
    # Override: use create_action_file with EMAIL_ prefix
    # ------------------------------------------------------------------

    def process_items(self, items: list[dict]) -> int:
        """Process check results into action files. Returns count created."""
        created = 0
        for item in items:
            extra_fm = item.pop("_extra_frontmatter", {})
            path = self.create_action_file(
                item,
                prefix="EMAIL_",
                extra_frontmatter=extra_fm,
            )
            if path:
                created += 1
        return created


# ---------------------------------------------------------------------------
# Demo data for testing without credentials
# ---------------------------------------------------------------------------

def _demo_items() -> list[dict]:
    """Return fake email items for testing without Google credentials."""
    return [
        {
            "id": "gmail_demo_001",
            "title": "Email: Project deadline moved to Friday",
            "body": (
                "**From:** manager@company.com  \n"
                "**To:** you@gmail.com  \n"
                "**Date:** 2026-02-19 14:00  \n"
                "**Subject:** Project deadline moved to Friday  \n"
                "**Labels:** UNREAD, IMPORTANT, INBOX\n\n"
                "### Snippet\n\n"
                "> The client asked to push the deadline to Friday. Please update your timeline.\n\n"
                "---\n\n"
                "### Full Body\n\n"
                "Hi,\n\n"
                "The client asked to push the deadline to Friday.\n"
                "Please update your timeline accordingly.\n\n"
                "Thanks"
            ),
            "priority": "high",
            "tags": ["#email", "#gmail", "#priority-high"],
            "actions": [
                "Reply to manager@company.com",
                "Update project timeline",
                "Check calendar for conflicts",
            ],
            "_extra_frontmatter": {
                "type": "email",
                "from": "manager@company.com",
                "subject": "Project deadline moved to Friday",
                "received": "2026-02-19 14:00",
            },
        },
        {
            "id": "gmail_demo_002",
            "title": "Email: URGENT - Invoice #4521 Payment Due",
            "body": (
                "**From:** billing@vendor.com  \n"
                "**To:** you@gmail.com  \n"
                "**Date:** 2026-02-19 09:30  \n"
                "**Subject:** URGENT - Invoice #4521 Payment Due  \n"
                "**Labels:** UNREAD, IMPORTANT, INBOX\n\n"
                "### Snippet\n\n"
                "> Invoice #4521 for $2,450 is due by end of day Friday.\n\n"
                "---\n\n"
                "### Full Body\n\n"
                "Dear Customer,\n\n"
                "Invoice #4521 for $2,450.00 is due by end of day Friday.\n"
                "Please arrange payment at your earliest convenience.\n\n"
                "Regards,\nBilling Team"
            ),
            "priority": "critical",
            "tags": ["#email", "#gmail", "#priority-critical", "#finance"],
            "actions": [
                "Reply to billing@vendor.com",
                "Log in finance tracker",
                "Read full email in Gmail",
            ],
            "_extra_frontmatter": {
                "type": "email",
                "from": "billing@vendor.com",
                "subject": "URGENT - Invoice #4521 Payment Due",
                "received": "2026-02-19 09:30",
            },
        },
        {
            "id": "gmail_demo_003",
            "title": "Email: Your AWS bill is ready",
            "body": (
                "**From:** billing@aws.amazon.com  \n"
                "**To:** you@gmail.com  \n"
                "**Date:** 2026-02-19 12:30  \n"
                "**Subject:** Your AWS bill is ready  \n"
                "**Labels:** UNREAD, INBOX\n\n"
                "### Snippet\n\n"
                "> Your AWS bill for February is $142.37.\n\n"
                "---\n\n"
                "### Full Body\n\n"
                "Your AWS bill for February is $142.37.\n"
                "View your bill at the AWS billing console."
            ),
            "priority": "medium",
            "tags": ["#email", "#gmail", "#priority-medium", "#finance"],
            "actions": [
                "Read full email in Gmail",
                "Log in finance tracker",
                "Archive if no action needed",
            ],
            "_extra_frontmatter": {
                "type": "email",
                "from": "billing@aws.amazon.com",
                "subject": "Your AWS bill is ready",
                "received": "2026-02-19 12:30",
            },
        },
    ]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Gmail Watcher (Silver Tier) — Personal AI Employee"
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to config.yaml (default: vault_root/config.yaml)",
    )
    parser.add_argument(
        "--vault", type=str, default=os.environ.get("VAULT_PATH"),
        help="Override vault root path (or set VAULT_PATH env var).",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single check cycle then exit.",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Use fake test data instead of real Gmail (no credentials needed).",
    )
    args = parser.parse_args()

    watcher = GmailWatcher(config_path=args.config, vault_path=args.vault)

    if args.demo:
        watcher.logger.info("Running in DEMO mode (no Google credentials needed)")
        items = _demo_items()
        created = watcher.process_items(items)
        watcher.logger.info("Demo done. %d action file(s) created.", created)
        watcher.log_activity("DEMO_RUN", f"created={created}")
    elif args.once:
        watcher.logger.info("Running single check (query: %s) ...", watcher.query)
        items = watcher.check()
        created = watcher.process_items(items)
        watcher.logger.info("Done. %d item(s) found, %d file(s) created.", len(items), created)
    else:
        # Continuous polling loop — uses BaseWatcher.run() with overridden processing
        watcher.logger.info(
            "Starting continuous Gmail watcher (poll every %ds, query: %s)",
            watcher.poll_interval, watcher.query,
        )
        watcher.log_activity("START", f"poll_interval={watcher.poll_interval}")
        while True:
            try:
                items = watcher.check()
                created = watcher.process_items(items)
                if items:
                    watcher.log_activity("CHECK", f"found={len(items)} created={created}")
                else:
                    watcher.logger.debug("No new items.")
            except KeyboardInterrupt:
                watcher.logger.info("Shutting down gmail_watcher")
                watcher.log_activity("STOP", "keyboard interrupt")
                break
            except Exception:
                watcher.logger.exception("Error during check cycle")
                watcher.log_activity("ERROR", "see log for traceback")

            import time
            time.sleep(watcher.poll_interval)


if __name__ == "__main__":
    main()
