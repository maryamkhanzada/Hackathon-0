"""
base_watcher.py — Abstract base class for all Personal AI Employee watchers.

Watchers are long-running processes that poll external sources (email, calendar,
bank feeds, etc.) and write structured Markdown notes into the Obsidian vault.

Usage:
    Subclass BaseWatcher, implement `check()`, then call `run()`.
"""

import abc
import hashlib
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

# Make resilience importable when running from any working directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from resilience import (  # noqa: E402
    DiskFullError,
    LocalCache,
    RetryExhausted,
    clear_pid,
    disk_check,
    with_retry,
    write_pid,
)
from audit_logger import AuditLogger  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load the central YAML config.  Falls back to sane defaults."""
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# ---------------------------------------------------------------------------
# Base Watcher
# ---------------------------------------------------------------------------


class BaseWatcher(abc.ABC):
    """Abstract watcher that all concrete watchers extend."""

    def __init__(self, name: str, config_path: Path = DEFAULT_CONFIG_PATH):
        self.name = name
        self.config = load_config(config_path)

        # Vault paths
        vault_root = Path(self.config.get("vault_path", Path(__file__).resolve().parent.parent))
        self.vault_root = vault_root
        self.inbox_dir = vault_root / "Inbox"
        self.needs_action_dir = vault_root / "Needs_Action"
        self.done_dir = vault_root / "Done"
        self.logs_dir = vault_root / "Logs"

        # Timing
        self.poll_interval: int = self.config.get("poll_interval_seconds", 120)

        # Logging
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.logs_dir / f"{self.name}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(),
            ],
        )
        self.logger = logging.getLogger(self.name)

        # Resilience: PID file for watchdog + local cache for API-down fallback
        write_pid(self.name)
        self._cache: LocalCache = LocalCache(self.name, ttl_seconds=3600)

        # Unified structured audit logger
        self._alog = AuditLogger(self.name, source_file="base_watcher.py")

    # ------------------------------------------------------------------
    # Abstract — subclasses MUST implement
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def check(self) -> list[dict]:
        """Poll the external source.

        Returns a list of dicts, each with at minimum:
            {
                "id":       unique identifier (used for dedup),
                "title":    short summary,
                "body":     markdown body,
                "priority": "critical" | "high" | "medium" | "low",
                "tags":     ["#email", ...],
                "actions":  ["Reply to sender", "Archive", ...],
            }
        """
        ...

    # ------------------------------------------------------------------
    # Note creation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_filename(text: str, max_len: int = 80) -> str:
        """Sanitise a string into a filesystem-safe filename."""
        text = re.sub(r'[<>:"/\\|?*\n\r]', "", text)
        text = re.sub(r"\s+", "_", text.strip())
        return text[:max_len]

    def _note_exists(self, note_id: str) -> bool:
        """Check if a note with this ID has already been written anywhere."""
        id_hash = hashlib.sha256(note_id.encode()).hexdigest()[:12]
        for folder in (self.inbox_dir, self.needs_action_dir, self.done_dir):
            for path in folder.glob("*.md"):
                # Quick scan first few lines for the id hash
                try:
                    head = path.read_text(encoding="utf-8")[:500]
                    if id_hash in head:
                        return True
                except OSError:
                    continue
        return False

    def create_note(
        self,
        item: dict,
        target_dir: Path | None = None,
    ) -> Path | None:
        """Write a Markdown note into the vault.

        Skips if a note with the same `id` already exists (dedup).
        Returns the path of the created file, or None if skipped.
        """
        note_id: str = item["id"]
        if self._note_exists(note_id):
            self.logger.info("Skipping duplicate: %s", note_id)
            return None

        target = target_dir or self.needs_action_dir
        target.mkdir(parents=True, exist_ok=True)

        id_hash = hashlib.sha256(note_id.encode()).hexdigest()[:12]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        safe_title = self._safe_filename(item.get("title", "Untitled"))
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M')}_{safe_title}.md"
        filepath = target / filename

        tags_list = item.get("tags", [])
        tags_str = ", ".join(f'"{t}"' for t in tags_list)
        actions = item.get("actions", [])
        actions_block = "\n".join(f"- [ ] {a}" for a in actions) if actions else "- [ ] Review"

        content = (
            f"---\n"
            f"id: {id_hash}\n"
            f"source: {self.name}\n"
            f"priority: {item.get('priority', 'medium')}\n"
            f"created: {timestamp}\n"
            f"status: open\n"
            f"tags: [{tags_str}]\n"
            f"---\n\n"
            f"# {item.get('title', 'Untitled')}\n\n"
            f"**Priority:** {item.get('priority', 'medium')}  \n"
            f"**Source:** {self.name}  \n"
            f"**Received:** {timestamp}\n\n"
            f"---\n\n"
            f"{item.get('body', '')}\n\n"
            f"---\n\n"
            f"## Suggested Actions\n\n"
            f"{actions_block}\n"
        )

        filepath.write_text(content, encoding="utf-8")
        self.logger.info("Created note: %s", filepath.name)
        return filepath

    def create_action_file(
        self,
        item: dict,
        *,
        prefix: str = "",
        extra_frontmatter: dict | None = None,
        target_dir: Path | None = None,
    ) -> Path | None:
        """Write a structured action file into Needs_Action/.

        Enhanced version of create_note with:
        - Custom filename prefix (e.g. EMAIL_, WHATSAPP_, LINKEDIN_)
        - Extra YAML frontmatter fields (type, from, subject, received, etc.)
        - Dedup via _note_exists()

        Args:
            item: dict with id, title, body, priority, tags, actions keys.
            prefix: Filename prefix like "EMAIL_", "WHATSAPP_".
            extra_frontmatter: Additional YAML fields merged into frontmatter.
            target_dir: Override output directory (default: Needs_Action/).

        Returns:
            Path of created file, or None if duplicate.
        """
        note_id: str = item["id"]
        if self._note_exists(note_id):
            self.logger.info("Skipping duplicate: %s", note_id)
            return None

        target = target_dir or self.needs_action_dir
        target.mkdir(parents=True, exist_ok=True)

        id_hash = hashlib.sha256(note_id.encode()).hexdigest()[:12]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Build filename: PREFIX_safeid.md
        safe_id = self._safe_filename(note_id, max_len=60)
        filename = f"{prefix}{safe_id}.md"
        filepath = target / filename

        # Build YAML frontmatter
        tags_list = item.get("tags", [])
        tags_str = ", ".join(f'"{t}"' for t in tags_list)

        fm_lines = [
            "---",
            f"id: {id_hash}",
            f"source: {self.name}",
            f"priority: {item.get('priority', 'medium')}",
            f"created: {timestamp}",
            f"status: pending",
            f"tags: [{tags_str}]",
        ]
        # Merge extra frontmatter fields
        if extra_frontmatter:
            for key, value in extra_frontmatter.items():
                # Quote strings that contain special YAML chars
                if isinstance(value, str) and any(c in value for c in ":#{}[]|>&"):
                    fm_lines.append(f'{key}: "{value}"')
                else:
                    fm_lines.append(f"{key}: {value}")
        fm_lines.append("---")

        # Build actions block
        actions = item.get("actions", [])
        actions_block = "\n".join(f"- [ ] {a}" for a in actions) if actions else "- [ ] Review"

        content = (
            "\n".join(fm_lines) + "\n\n"
            f"# {item.get('title', 'Untitled')}\n\n"
            f"**Priority:** {item.get('priority', 'medium')}  \n"
            f"**Source:** {self.name}  \n"
            f"**Received:** {timestamp}\n\n"
            f"---\n\n"
            f"{item.get('body', '')}\n\n"
            f"---\n\n"
            f"## Suggested Actions\n\n"
            f"{actions_block}\n"
        )

        filepath.write_text(content, encoding="utf-8")
        self.logger.info("Created action file: %s", filepath.name)
        self.log_activity("CREATE_ACTION", f"{filepath.name} [{item.get('priority', 'medium')}]")
        return filepath

    # ------------------------------------------------------------------
    # Log helper
    # ------------------------------------------------------------------

    def log_activity(self, action: str, detail: str = "") -> None:
        """Append a line to the daily activity log AND to the unified audit log."""
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.logs_dir / f"activity_{today}.log"
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp} | {self.name} | {action} | {detail}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)

        # Map activity action strings to structured audit events
        _ACTION_MAP = {
            "START":           ("watcher_start",          "INFO",     "started"),
            "STOP":            ("watcher_stop",           "INFO",     "stopped"),
            "CHECK":           ("check_cycle",            "INFO",     "success"),
            "ERROR":           ("check_cycle",            "ERROR",    "failure"),
            "DISK_FULL":       ("disk_check",             "CRITICAL", "failure"),
            "RETRY_EXHAUSTED": ("check_retry_exhausted",  "ERROR",    "failure"),
            "CREATE_ACTION":   ("create_action_file",     "INFO",     "success"),
        }
        mapped_action, severity, result = _ACTION_MAP.get(
            action, (action.lower(), "INFO", "success")
        )
        self._alog.log(
            mapped_action,
            params={"detail": detail},
            result=result,
            severity=severity,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the polling loop.  Runs forever until interrupted.

        Resilience features:
        - Disk space checked before each cycle; cycle skipped if disk critical.
        - check() is wrapped in with_retry() (max 3 attempts, 2**n backoff).
        - On RetryExhausted, falls back to last cached result from LocalCache.
        - PID file written at startup; cleared in finally block for watchdog.
        """
        self.logger.info("Starting %s (poll every %ds)", self.name, self.poll_interval)
        self.log_activity("START", f"poll_interval={self.poll_interval}")

        try:
            while True:
                try:
                    # --- Disk check -------------------------------------------
                    try:
                        disk_check(self.vault_root)
                    except DiskFullError as exc:
                        self.logger.critical("Disk full — skipping cycle: %s", exc)
                        self.log_activity("DISK_FULL", str(exc)[:120])
                        time.sleep(self.poll_interval)
                        continue

                    # --- check() with automatic retry -------------------------
                    try:
                        items = with_retry(
                            self.check,
                            label=f"{self.name}.check",
                        )
                        # Persist successful result so fallback has fresh data
                        if items:
                            self._cache.save(items)
                    except RetryExhausted as exc:
                        self.logger.error(
                            "check() retries exhausted — falling back to cache: %s", exc
                        )
                        self.log_activity("RETRY_EXHAUSTED", str(exc)[:120])
                        cached = self._cache.load()
                        if cached:
                            items = cached
                            self.logger.warning(
                                "Using %d cached item(s) as fallback", len(items)
                            )
                        else:
                            items = []
                            self.logger.warning(
                                "No cached data available; skipping cycle."
                            )

                    # --- Process results --------------------------------------
                    created = 0
                    for item in items:
                        path = self.create_note(item)
                        if path:
                            created += 1
                    if items:
                        self.log_activity("CHECK", f"found={len(items)} created={created}")
                    else:
                        self.logger.debug("No new items.")

                except KeyboardInterrupt:
                    self.logger.info("Shutting down %s", self.name)
                    self.log_activity("STOP", "keyboard interrupt")
                    break
                except Exception:
                    self.logger.exception("Error during check cycle")
                    self.log_activity("ERROR", "see log for traceback")

                time.sleep(self.poll_interval)

        finally:
            # Always clear PID file on exit so watchdog knows we stopped cleanly
            clear_pid(self.name)
            self.logger.info("%s stopped; PID file cleared.", self.name)
