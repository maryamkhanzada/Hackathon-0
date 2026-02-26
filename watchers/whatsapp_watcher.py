"""
whatsapp_watcher.py — Silver Tier WhatsApp Watcher for Personal AI Employee.

Uses Playwright with a persistent browser context to monitor WhatsApp Web
for unread messages containing priority keywords. Creates structured
WHATSAPP_{chat_id}.md action files in Needs_Action/.

Setup:
    1. pip install playwright
    2. python -m playwright install chromium
    3. First run: log into WhatsApp Web via QR code (session persists).
    4. Configure session_path in config.yaml or WHATSAPP_SESSION_PATH env var.

Usage:
    python whatsapp_watcher.py                  # Continuous (30s poll)
    python whatsapp_watcher.py --once           # Single check cycle
    python whatsapp_watcher.py --demo           # Fake data, no browser
    python whatsapp_watcher.py --vault /path    # Override vault path
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

# Keywords that trigger action file creation.
TRIGGER_KEYWORDS = ["urgent", "invoice", "payment", "pricing", "help"]

# Default polling interval (seconds) — faster than email since chat is real-time.
DEFAULT_POLL_INTERVAL = 30


class WhatsAppWatcher(BaseWatcher):
    """Watches WhatsApp Web for unread messages with priority keywords.

    Uses Playwright persistent context so the WhatsApp Web session
    stays authenticated across restarts (QR code only needed once).

    Creates WHATSAPP_{chat_hash}.md in Needs_Action/ with:
    - YAML frontmatter: type, from, keywords_matched, received, priority
    - Message content
    - Suggested actions checkboxes
    """

    def __init__(self, config_path: Path | None = None, vault_path: str | None = None):
        kw = {"config_path": config_path} if config_path else {}
        super().__init__(name="whatsapp_watcher", **kw)

        # Override vault path
        if vault_path:
            self.vault_root = Path(vault_path)
            self.needs_action_dir = self.vault_root / "Needs_Action"
            self.done_dir = self.vault_root / "Done"
            self.logs_dir = self.vault_root / "Logs"
            self.inbox_dir = self.vault_root / "Inbox"

        wa_cfg = self.config.get("whatsapp", {})

        # Persistent browser session directory
        self.session_path = Path(
            os.environ.get(
                "WHATSAPP_SESSION_PATH",
                wa_cfg.get("session_path", self.vault_root / ".whatsapp_session"),
            )
        )
        self.session_path.mkdir(parents=True, exist_ok=True)

        self.poll_interval = wa_cfg.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
        self.keywords: list[str] = wa_cfg.get("keywords", TRIGGER_KEYWORDS)

        # In-memory dedup: set of message hashes already processed
        self._processed_hashes: set[str] = set()

        # Playwright objects (lazily initialized)
        self._playwright = None
        self._browser = None
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
            headless=False,  # Must be visible for QR code login
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

        # Navigate to WhatsApp Web
        self._page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
        self.logger.info("Navigated to WhatsApp Web.")
        self.logger.info("If this is the first run, scan the QR code in the browser window.")

        # Wait for the main chat list to load (indicates successful login)
        try:
            self._page.wait_for_selector(
                'div[aria-label="Chat list"], #pane-side',
                timeout=120_000,  # 2 min for QR scan
            )
            self.logger.info("WhatsApp Web loaded successfully.")
        except Exception:
            self.logger.warning(
                "Chat list not detected within 120s. "
                "Continuing anyway — may need QR scan."
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
    # WhatsApp scraping helpers
    # ------------------------------------------------------------------

    def _extract_unread_chats(self) -> list[dict]:
        """Extract unread chat previews from the WhatsApp Web sidebar.

        Returns list of dicts with keys: chat_name, last_message, timestamp, unread_count.
        """
        chats = []

        try:
            # WhatsApp Web uses aria-label on chat list items with unread badges
            # Look for chat rows that have an unread indicator
            unread_spans = self._page.query_selector_all(
                'span[data-testid="icon-unread-count"], '
                'div[class*="unread"] span[aria-label]'
            )

            if not unread_spans:
                # Fallback: look for any unread badge indicators
                unread_spans = self._page.query_selector_all(
                    'span[aria-label*="unread message"]'
                )

            # Get the parent chat row for each unread indicator
            processed_names = set()
            for badge in unread_spans:
                try:
                    # Navigate up to the chat row container
                    chat_row = badge.evaluate_handle(
                        """el => {
                            let node = el;
                            for (let i = 0; i < 10; i++) {
                                node = node.parentElement;
                                if (!node) return null;
                                if (node.getAttribute('role') === 'listitem' ||
                                    node.getAttribute('data-testid') === 'cell-frame-container' ||
                                    node.classList.contains('_ak8l')) {
                                    return node;
                                }
                            }
                            return node;
                        }"""
                    )

                    if not chat_row:
                        continue

                    # Extract chat name
                    name_el = chat_row.as_element().query_selector(
                        'span[title], span[data-testid="cell-frame-title"] span'
                    )
                    chat_name = name_el.inner_text().strip() if name_el else "Unknown"

                    if chat_name in processed_names:
                        continue
                    processed_names.add(chat_name)

                    # Extract last message preview
                    msg_el = chat_row.as_element().query_selector(
                        'span[title][class*="matched-text"], '
                        'div[data-testid="cell-frame-secondary"] span[title], '
                        'span[class*="_ao3e"]'
                    )
                    last_message = msg_el.inner_text().strip() if msg_el else ""

                    # Extract timestamp
                    time_el = chat_row.as_element().query_selector(
                        'div[class*="timestamp"], div[class*="_ak8i"]'
                    )
                    timestamp = time_el.inner_text().strip() if time_el else ""

                    # Extract unread count
                    unread_text = badge.inner_text().strip()
                    try:
                        unread_count = int(unread_text)
                    except (ValueError, TypeError):
                        unread_count = 1

                    chats.append({
                        "chat_name": chat_name,
                        "last_message": last_message,
                        "timestamp": timestamp,
                        "unread_count": unread_count,
                    })

                except Exception as e:
                    self.logger.debug("Error extracting chat row: %s", e)
                    continue

        except Exception:
            self.logger.exception("Error scanning unread chats")

        return chats

    def _match_keywords(self, text: str) -> list[str]:
        """Return list of trigger keywords found in text."""
        text_lower = text.lower()
        return [kw for kw in self.keywords if kw in text_lower]

    def _classify_priority(self, matched_keywords: list[str]) -> str:
        """Determine priority based on matched keywords."""
        if "urgent" in matched_keywords:
            return "critical"
        if any(kw in matched_keywords for kw in ("payment", "invoice")):
            return "high"
        if "pricing" in matched_keywords:
            return "high"
        return "medium"

    def _suggest_actions(self, chat_name: str, matched_keywords: list[str]) -> list[str]:
        """Generate suggested actions based on chat context."""
        actions = [f"Reply to {chat_name} on WhatsApp"]

        if "invoice" in matched_keywords or "payment" in matched_keywords:
            actions.append("Log in finance tracker")
            actions.append("Check payment status")
        if "pricing" in matched_keywords:
            actions.append("Prepare pricing quote")
        if "help" in matched_keywords:
            actions.append("Review support request details")
        if "urgent" in matched_keywords:
            actions.append("Escalate — mark as critical")

        actions.append("Archive if no action needed")
        return actions

    # ------------------------------------------------------------------
    # Core: check_for_updates
    # ------------------------------------------------------------------

    def check_for_updates(self) -> list[dict]:
        """Poll WhatsApp Web for keyword-matching unread chats. Alias for check()."""
        return self.check()

    def check(self) -> list[dict]:
        """Poll WhatsApp Web for unread chats with trigger keywords."""
        self._ensure_browser()

        self.logger.info("Scanning WhatsApp Web for unread chats ...")
        unread_chats = self._extract_unread_chats()

        if not unread_chats:
            self.logger.info("No unread chats found.")
            return []

        self.logger.info("Found %d unread chat(s). Checking keywords ...", len(unread_chats))
        items: list[dict] = []

        for chat in unread_chats:
            chat_name = chat["chat_name"]
            last_message = chat["last_message"]
            combined_text = f"{chat_name} {last_message}"

            # Check for keyword matches
            matched = self._match_keywords(combined_text)
            if not matched:
                self.logger.debug("No keywords matched for chat: %s", chat_name)
                continue

            # Generate dedup hash
            msg_hash = hashlib.sha256(
                f"{chat_name}:{last_message}:{chat['timestamp']}".encode()
            ).hexdigest()[:16]

            if msg_hash in self._processed_hashes:
                self.logger.debug("Skipping already-processed chat: %s", chat_name)
                continue
            self._processed_hashes.add(msg_hash)

            priority = self._classify_priority(matched)
            actions = self._suggest_actions(chat_name, matched)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            body_md = (
                f"**From:** {chat_name}  \n"
                f"**Unread Messages:** {chat['unread_count']}  \n"
                f"**Chat Timestamp:** {chat['timestamp']}  \n"
                f"**Keywords Matched:** {', '.join(matched)}  \n\n"
                f"---\n\n"
                f"### Last Message Preview\n\n"
                f"> {last_message}\n\n"
                f"---\n\n"
                f"_Open WhatsApp to read full conversation._"
            )

            extra_fm = {
                "type": "whatsapp",
                "from": chat_name,
                "keywords_matched": ", ".join(matched),
                "received": now_str,
                "unread_count": chat["unread_count"],
            }

            items.append({
                "id": f"whatsapp_{msg_hash}",
                "title": f"WhatsApp: {chat_name} — {', '.join(matched)}",
                "body": body_md,
                "priority": priority,
                "tags": ["#whatsapp", f"#priority-{priority}"] + [f"#kw-{kw}" for kw in matched],
                "actions": actions,
                "_extra_frontmatter": extra_fm,
            })

        self.logger.info("Keyword matches: %d actionable chat(s).", len(items))
        return items

    def process_items(self, items: list[dict]) -> int:
        """Create WHATSAPP_ action files from check results."""
        created = 0
        for item in items:
            extra_fm = item.pop("_extra_frontmatter", {})
            path = self.create_action_file(
                item,
                prefix="WHATSAPP_",
                extra_frontmatter=extra_fm,
            )
            if path:
                created += 1
        return created

    # ------------------------------------------------------------------
    # Main loop override (with browser cleanup)
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start continuous polling. Handles browser lifecycle."""
        self.logger.info(
            "Starting WhatsApp watcher (poll every %ds, keywords: %s)",
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
            self.logger.info("Shutting down whatsapp_watcher")
            self.log_activity("STOP", "keyboard interrupt")
        finally:
            self._close_browser()


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

def _demo_items() -> list[dict]:
    """Return fake WhatsApp items for testing without browser."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    return [
        {
            "id": "whatsapp_demo_001",
            "title": "WhatsApp: Ahmed Khan — urgent, payment",
            "body": (
                f"**From:** Ahmed Khan  \n"
                f"**Unread Messages:** 3  \n"
                f"**Chat Timestamp:** 2:45 PM  \n"
                f"**Keywords Matched:** urgent, payment  \n\n"
                f"---\n\n"
                f"### Last Message Preview\n\n"
                f"> Bhai urgent payment bhejo $500 invoice ka, client wait kar raha hai\n\n"
                f"---\n\n"
                f"_Open WhatsApp to read full conversation._"
            ),
            "priority": "critical",
            "tags": ["#whatsapp", "#priority-critical", "#kw-urgent", "#kw-payment"],
            "actions": [
                "Reply to Ahmed Khan on WhatsApp",
                "Log in finance tracker",
                "Check payment status",
                "Escalate — mark as critical",
            ],
            "_extra_frontmatter": {
                "type": "whatsapp",
                "from": "Ahmed Khan",
                "keywords_matched": "urgent, payment",
                "received": now_str,
                "unread_count": 3,
            },
        },
        {
            "id": "whatsapp_demo_002",
            "title": "WhatsApp: Sarah Client — pricing",
            "body": (
                f"**From:** Sarah Client  \n"
                f"**Unread Messages:** 1  \n"
                f"**Chat Timestamp:** 1:20 PM  \n"
                f"**Keywords Matched:** pricing  \n\n"
                f"---\n\n"
                f"### Last Message Preview\n\n"
                f"> Hi, can you send me the pricing for the enterprise plan?\n\n"
                f"---\n\n"
                f"_Open WhatsApp to read full conversation._"
            ),
            "priority": "high",
            "tags": ["#whatsapp", "#priority-high", "#kw-pricing"],
            "actions": [
                "Reply to Sarah Client on WhatsApp",
                "Prepare pricing quote",
                "Archive if no action needed",
            ],
            "_extra_frontmatter": {
                "type": "whatsapp",
                "from": "Sarah Client",
                "keywords_matched": "pricing",
                "received": now_str,
                "unread_count": 1,
            },
        },
    ]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="WhatsApp Watcher (Silver Tier) — Personal AI Employee"
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

    watcher = WhatsAppWatcher(config_path=args.config, vault_path=args.vault)

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
                "Done. %d chat(s) matched, %d file(s) created.", len(items), created
            )
        finally:
            watcher._close_browser()
    else:
        watcher.run()


if __name__ == "__main__":
    main()
