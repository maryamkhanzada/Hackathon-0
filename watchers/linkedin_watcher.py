"""
linkedin_watcher.py — Silver Tier LinkedIn Watcher for Personal AI Employee.

Uses Playwright with a persistent browser context to monitor LinkedIn
notifications and messages for sales leads. Creates structured
LINKEDIN_{timestamp}.md action files in Needs_Action/.

Setup:
    1. pip install playwright
    2. python -m playwright install chromium
    3. First run: log into LinkedIn in the browser window (session persists).
    4. Configure session_path in config.yaml or LINKEDIN_SESSION_PATH env var.

Usage:
    python linkedin_watcher.py                  # Continuous (120s poll)
    python linkedin_watcher.py --once           # Single check cycle
    python linkedin_watcher.py --demo           # Fake data, no browser
    python linkedin_watcher.py --vault /path    # Override vault path
"""

import argparse
import hashlib
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from base_watcher import BaseWatcher

logger = logging.getLogger(__name__)

# Keywords indicating a sales lead or business opportunity.
SALES_KEYWORDS = ["interested", "buy", "quote", "pricing", "purchase", "proposal", "demo", "trial"]

# Default polling interval (seconds).
DEFAULT_POLL_INTERVAL = 120


class LinkedInWatcher(BaseWatcher):
    """Watches LinkedIn notifications and messages for sales leads.

    Uses Playwright persistent context so the LinkedIn session
    stays authenticated across restarts.

    Creates LINKEDIN_{timestamp_hash}.md in Needs_Action/ with:
    - YAML frontmatter: type, from, keywords_matched, received, priority, lead_type
    - Notification/message content
    - Suggested actions checkboxes
    """

    def __init__(self, config_path: Path | None = None, vault_path: str | None = None):
        kw = {"config_path": config_path} if config_path else {}
        super().__init__(name="linkedin_watcher", **kw)

        # Override vault path
        if vault_path:
            self.vault_root = Path(vault_path)
            self.needs_action_dir = self.vault_root / "Needs_Action"
            self.done_dir = self.vault_root / "Done"
            self.logs_dir = self.vault_root / "Logs"
            self.inbox_dir = self.vault_root / "Inbox"

        li_cfg = self.config.get("linkedin", {})

        # Persistent browser session
        self.session_path = Path(
            os.environ.get(
                "LINKEDIN_SESSION_PATH",
                li_cfg.get("session_path", self.vault_root / ".linkedin_session"),
            )
        )
        self.session_path.mkdir(parents=True, exist_ok=True)

        self.poll_interval = li_cfg.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
        self.keywords: list[str] = li_cfg.get("keywords", SALES_KEYWORDS)

        # In-memory dedup
        self._processed_hashes: set[str] = set()

        # Playwright objects
        self._playwright = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------
    # Playwright lifecycle
    # ------------------------------------------------------------------

    def _ensure_browser(self):
        """Launch Playwright persistent context if not already running."""
        if self._page is not None:
            return

        from playwright.sync_api import sync_playwright

        self.logger.info("Launching Playwright persistent context ...")
        self.logger.info("Session path: %s", self.session_path)

        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.session_path),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

        # Navigate to LinkedIn
        self._page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        self.logger.info("Navigated to LinkedIn.")

        # Wait for feed to confirm login
        try:
            self._page.wait_for_selector(
                'div.feed-shared-update-v2, main[aria-label], div[data-test-id="feed"]',
                timeout=120_000,
            )
            self.logger.info("LinkedIn loaded successfully.")
        except Exception:
            self.logger.warning(
                "Feed not detected within 120s. "
                "You may need to log in manually in the browser window."
            )

    def _close_browser(self):
        """Gracefully close Playwright."""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._playwright = None

    # ------------------------------------------------------------------
    # LinkedIn scraping helpers
    # ------------------------------------------------------------------

    def _extract_notifications(self) -> list[dict]:
        """Navigate to notifications page and extract recent items."""
        notifications = []

        try:
            self._page.goto(
                "https://www.linkedin.com/notifications/",
                wait_until="domcontentloaded",
            )
            time.sleep(3)  # Let notifications render

            # LinkedIn notification cards
            cards = self._page.query_selector_all(
                'div.nt-card, '
                'article[data-test-id="notification-card"], '
                'div[class*="notification-card"], '
                'section.nt-card-list div[class*="nt-card"]'
            )

            for card in cards[:20]:  # Limit to most recent 20
                try:
                    # Extract notification text
                    text_el = card.query_selector(
                        'span[class*="nt-card__text"], '
                        'p[class*="nt-card__text"], '
                        'a[class*="nt-card__headline"]'
                    )
                    text = text_el.inner_text().strip() if text_el else ""

                    # Extract name of the person
                    name_el = card.query_selector(
                        'span.nt-card__name, '
                        'strong, '
                        'a[class*="nt-card__name-link"]'
                    )
                    name = name_el.inner_text().strip() if name_el else "Unknown"

                    # Extract timestamp
                    time_el = card.query_selector(
                        'time, span[class*="nt-card__time-ago"]'
                    )
                    timestamp = time_el.inner_text().strip() if time_el else ""

                    if text:
                        notifications.append({
                            "source_type": "notification",
                            "person": name,
                            "text": text,
                            "timestamp": timestamp,
                        })
                except Exception as e:
                    self.logger.debug("Error extracting notification: %s", e)
                    continue

        except Exception:
            self.logger.exception("Error loading notifications page")

        return notifications

    def _extract_messages(self) -> list[dict]:
        """Navigate to messaging page and extract unread message previews."""
        messages = []

        try:
            self._page.goto(
                "https://www.linkedin.com/messaging/",
                wait_until="domcontentloaded",
            )
            time.sleep(3)

            # LinkedIn message thread items with unread indicator
            threads = self._page.query_selector_all(
                'li.msg-conversation-listitem, '
                'div[class*="msg-conversation-card"], '
                'li[class*="msg-conversation-listitem"]'
            )

            for thread in threads[:15]:
                try:
                    # Check if unread (has unread indicator/badge)
                    unread_badge = thread.query_selector(
                        'span[class*="notification-badge"], '
                        'span[class*="msg-conversation-card__unread-count"], '
                        'div[class*="unread"]'
                    )
                    # Also check for bold text (unread styling)
                    is_unread = unread_badge is not None

                    if not is_unread:
                        # Check for bold/unread class on the text
                        bold_el = thread.query_selector('[class*="--unread"], [class*="font-weight"]')
                        is_unread = bold_el is not None

                    if not is_unread:
                        continue

                    # Extract sender name
                    name_el = thread.query_selector(
                        'h3[class*="msg-conversation-card__participant-names"], '
                        'span[class*="msg-conversation-listitem__participant-names"], '
                        'h3 span'
                    )
                    name = name_el.inner_text().strip() if name_el else "Unknown"

                    # Extract message preview
                    preview_el = thread.query_selector(
                        'p[class*="msg-conversation-card__message-snippet"], '
                        'span[class*="msg-conversation-card__message-snippet-body"], '
                        'p[class*="msg-conversation-listitem__message-snippet"]'
                    )
                    preview = preview_el.inner_text().strip() if preview_el else ""

                    # Extract timestamp
                    time_el = thread.query_selector(
                        'time, span[class*="msg-conversation-card__time-stamp"]'
                    )
                    timestamp = time_el.inner_text().strip() if time_el else ""

                    if name and preview:
                        messages.append({
                            "source_type": "message",
                            "person": name,
                            "text": preview,
                            "timestamp": timestamp,
                        })

                except Exception as e:
                    self.logger.debug("Error extracting message thread: %s", e)
                    continue

        except Exception:
            self.logger.exception("Error loading messaging page")

        return messages

    def _match_keywords(self, text: str) -> list[str]:
        """Return list of sales keywords found in text."""
        text_lower = text.lower()
        return [kw for kw in self.keywords if kw in text_lower]

    def _classify_priority(self, matched_keywords: list[str], source_type: str) -> str:
        """Determine priority based on keywords and source."""
        if any(kw in matched_keywords for kw in ("buy", "purchase")):
            return "critical"
        if any(kw in matched_keywords for kw in ("quote", "pricing", "proposal")):
            return "high"
        if source_type == "message":
            return "high"  # Direct messages are higher priority
        return "medium"

    def _classify_lead_type(self, matched_keywords: list[str]) -> str:
        """Classify the type of sales lead."""
        if any(kw in matched_keywords for kw in ("buy", "purchase")):
            return "hot_lead"
        if any(kw in matched_keywords for kw in ("quote", "pricing", "proposal")):
            return "warm_lead"
        if any(kw in matched_keywords for kw in ("interested", "demo", "trial")):
            return "interested_prospect"
        return "general_inquiry"

    def _suggest_actions(self, person: str, lead_type: str, source_type: str) -> list[str]:
        """Generate suggested actions for a LinkedIn lead."""
        actions = []

        if source_type == "message":
            actions.append(f"Reply to {person} on LinkedIn Messages")
        else:
            actions.append(f"Send connection request / message to {person}")

        if lead_type == "hot_lead":
            actions.append("Prepare proposal/contract")
            actions.append("Schedule call ASAP")
        elif lead_type == "warm_lead":
            actions.append("Send pricing sheet / quote")
            actions.append("Schedule discovery call")
        elif lead_type == "interested_prospect":
            actions.append("Send product info / demo link")
            actions.append("Add to CRM pipeline")

        actions.append("Draft LinkedIn sales post in Plans/ (needs approval)")
        actions.append("Log lead in sales tracker")
        return actions

    # ------------------------------------------------------------------
    # Core: check_for_updates
    # ------------------------------------------------------------------

    def check_for_updates(self) -> list[dict]:
        """Poll LinkedIn for sales leads. Alias for check()."""
        return self.check()

    def check(self) -> list[dict]:
        """Poll LinkedIn notifications and messages for sales keywords."""
        self._ensure_browser()

        self.logger.info("Scanning LinkedIn for sales leads (keywords: %s) ...", self.keywords)

        # Gather from both sources
        all_entries = []
        all_entries.extend(self._extract_notifications())
        all_entries.extend(self._extract_messages())

        if not all_entries:
            self.logger.info("No notifications or unread messages found.")
            return []

        self.logger.info(
            "Found %d item(s). Checking for sales keywords ...", len(all_entries)
        )
        items: list[dict] = []

        for entry in all_entries:
            text = entry["text"]
            person = entry["person"]
            source_type = entry["source_type"]

            matched = self._match_keywords(f"{person} {text}")
            if not matched:
                continue

            # Generate dedup hash
            content_hash = hashlib.sha256(
                f"{person}:{text}:{entry['timestamp']}".encode()
            ).hexdigest()[:16]

            if content_hash in self._processed_hashes:
                continue
            self._processed_hashes.add(content_hash)

            priority = self._classify_priority(matched, source_type)
            lead_type = self._classify_lead_type(matched)
            actions = self._suggest_actions(person, lead_type, source_type)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")

            body_md = (
                f"**From:** {person}  \n"
                f"**Source:** LinkedIn {source_type}  \n"
                f"**Lead Type:** {lead_type}  \n"
                f"**Keywords Matched:** {', '.join(matched)}  \n"
                f"**LinkedIn Timestamp:** {entry['timestamp']}  \n\n"
                f"---\n\n"
                f"### Content\n\n"
                f"> {text}\n\n"
                f"---\n\n"
                f"_Open LinkedIn to view full context and respond._"
            )

            extra_fm = {
                "type": "linkedin",
                "from": person,
                "lead_type": lead_type,
                "keywords_matched": ", ".join(matched),
                "received": now_str,
                "linkedin_source": source_type,
            }

            items.append({
                "id": f"linkedin_{ts_slug}_{content_hash}",
                "title": f"LinkedIn Lead: {person} — {lead_type}",
                "body": body_md,
                "priority": priority,
                "tags": [
                    "#linkedin", "#sales-lead", f"#priority-{priority}",
                    f"#lead-{lead_type}",
                ] + [f"#kw-{kw}" for kw in matched],
                "actions": actions,
                "_extra_frontmatter": extra_fm,
            })

        self.logger.info("Sales lead matches: %d actionable item(s).", len(items))
        return items

    def process_items(self, items: list[dict]) -> int:
        """Create LINKEDIN_ action files from check results."""
        created = 0
        for item in items:
            extra_fm = item.pop("_extra_frontmatter", {})
            path = self.create_action_file(
                item,
                prefix="LINKEDIN_",
                extra_frontmatter=extra_fm,
            )
            if path:
                created += 1
        return created

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start continuous polling with browser lifecycle management."""
        self.logger.info(
            "Starting LinkedIn watcher (poll every %ds, keywords: %s)",
            self.poll_interval, self.keywords,
        )
        self.log_activity("START", f"poll_interval={self.poll_interval}")

        try:
            while True:
                try:
                    items = self.check()
                    created = self.process_items(items)
                    if items:
                        self.log_activity("CHECK", f"found={len(items)} created={created}")
                except KeyboardInterrupt:
                    raise
                except Exception:
                    self.logger.exception("Error during check cycle")
                    self.log_activity("ERROR", "see log for traceback")

                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.logger.info("Shutting down linkedin_watcher")
            self.log_activity("STOP", "keyboard interrupt")
        finally:
            self._close_browser()


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

def _demo_items() -> list[dict]:
    """Return fake LinkedIn lead items for testing without browser."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    return [
        {
            "id": f"linkedin_{ts_slug}_demo001",
            "title": "LinkedIn Lead: John Smith — hot_lead",
            "body": (
                f"**From:** John Smith  \n"
                f"**Source:** LinkedIn message  \n"
                f"**Lead Type:** hot_lead  \n"
                f"**Keywords Matched:** buy, pricing  \n"
                f"**LinkedIn Timestamp:** 2h ago  \n\n"
                f"---\n\n"
                f"### Content\n\n"
                f"> Hi, we'd like to buy your enterprise plan. Can you send pricing for 50 seats?\n\n"
                f"---\n\n"
                f"_Open LinkedIn to view full context and respond._"
            ),
            "priority": "critical",
            "tags": ["#linkedin", "#sales-lead", "#priority-critical", "#lead-hot_lead", "#kw-buy", "#kw-pricing"],
            "actions": [
                "Reply to John Smith on LinkedIn Messages",
                "Prepare proposal/contract",
                "Schedule call ASAP",
                "Draft LinkedIn sales post in Plans/ (needs approval)",
                "Log lead in sales tracker",
            ],
            "_extra_frontmatter": {
                "type": "linkedin",
                "from": "John Smith",
                "lead_type": "hot_lead",
                "keywords_matched": "buy, pricing",
                "received": now_str,
                "linkedin_source": "message",
            },
        },
        {
            "id": f"linkedin_{ts_slug}_demo002",
            "title": "LinkedIn Lead: Maria Garcia — interested_prospect",
            "body": (
                f"**From:** Maria Garcia  \n"
                f"**Source:** LinkedIn notification  \n"
                f"**Lead Type:** interested_prospect  \n"
                f"**Keywords Matched:** interested, demo  \n"
                f"**LinkedIn Timestamp:** 5h ago  \n\n"
                f"---\n\n"
                f"### Content\n\n"
                f"> Maria Garcia commented on your post: "
                f"\"Really interested in this! Can I get a demo?\"\n\n"
                f"---\n\n"
                f"_Open LinkedIn to view full context and respond._"
            ),
            "priority": "medium",
            "tags": ["#linkedin", "#sales-lead", "#priority-medium", "#lead-interested_prospect", "#kw-interested", "#kw-demo"],
            "actions": [
                "Send connection request / message to Maria Garcia",
                "Send product info / demo link",
                "Add to CRM pipeline",
                "Draft LinkedIn sales post in Plans/ (needs approval)",
                "Log lead in sales tracker",
            ],
            "_extra_frontmatter": {
                "type": "linkedin",
                "from": "Maria Garcia",
                "lead_type": "interested_prospect",
                "keywords_matched": "interested, demo",
                "received": now_str,
                "linkedin_source": "notification",
            },
        },
    ]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LinkedIn Watcher (Silver Tier) — Personal AI Employee"
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to config.yaml",
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
        help="Use fake data (no browser needed).",
    )
    args = parser.parse_args()

    watcher = LinkedInWatcher(config_path=args.config, vault_path=args.vault)

    if args.demo:
        watcher.logger.info("Running in DEMO mode (no browser needed)")
        items = _demo_items()
        created = watcher.process_items(items)
        watcher.logger.info("Demo done. %d action file(s) created.", created)
        watcher.log_activity("DEMO_RUN", f"created={created}")
    elif args.once:
        watcher.logger.info("Running single check (keywords: %s) ...", watcher.keywords)
        try:
            items = watcher.check()
            created = watcher.process_items(items)
            watcher.logger.info(
                "Done. %d lead(s) matched, %d file(s) created.", len(items), created
            )
        finally:
            watcher._close_browser()
    else:
        watcher.run()


if __name__ == "__main__":
    main()
