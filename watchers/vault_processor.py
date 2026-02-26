"""
vault_processor.py — Read/Summarize/Write orchestrator for the Obsidian vault.

Implements the full SKILL_vault_read → SKILL_vault_write cycle in Python:
  1. Scan Needs_Action/ and Inbox/ for .md files
  2. Parse YAML frontmatter + extract titles
  3. Build structured summary
  4. Update Dashboard.md with live counts
  5. Move status:done items to Done/
  6. Log everything

Can be run standalone, by a cron job, or called from Claude Code.

Usage:
    python vault_processor.py              # full cycle
    python vault_processor.py --scan-only  # just print summary, don't write
"""

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def parse_note(filepath: Path) -> dict | None:
    """Parse a vault note and return structured metadata + content."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except OSError:
        return None

    meta = {}
    fm_match = _FM_RE.match(text)
    if fm_match:
        try:
            meta = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}

    title_match = _TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else filepath.stem

    # Extract first meaningful body line after the title for summary.
    body_lines = text.split("\n")
    summary_line = ""
    past_title = False
    for line in body_lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            past_title = True
            continue
        if past_title and stripped and not stripped.startswith(("---", "**", "|", "```")):
            summary_line = stripped[:120]
            break

    return {
        "filepath": filepath,
        "filename": filepath.name,
        "folder": filepath.parent.name,
        "title": title,
        "summary": summary_line,
        "id": meta.get("id", ""),
        "source": meta.get("source", "unknown"),
        "priority": meta.get("priority", "medium"),
        "created": str(meta.get("created", "")),
        "status": meta.get("status", "open"),
        "tags": meta.get("tags", []),
    }


# ---------------------------------------------------------------------------
# Vault scanner  (SKILL_vault_read logic)
# ---------------------------------------------------------------------------

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def scan_folder(folder: Path) -> list[dict]:
    """Scan a single folder for .md notes and return parsed items."""
    if not folder.is_dir():
        return []
    items = []
    for md in sorted(folder.glob("*.md")):
        parsed = parse_note(md)
        if parsed:
            items.append(parsed)
    items.sort(key=lambda x: PRIORITY_ORDER.get(x["priority"], 99))
    return items


def scan_vault(vault_root: Path) -> dict:
    """Full vault scan — returns categorised items and aggregate counts."""
    needs_action = scan_folder(vault_root / "Needs_Action")
    inbox = scan_folder(vault_root / "Inbox")

    all_items = needs_action + inbox
    counts = {
        "total": len(all_items),
        "needs_action": len(needs_action),
        "inbox": len(inbox),
        "by_priority": {},
        "by_source": {},
        "by_status": {},
    }
    for item in all_items:
        p = item["priority"]
        counts["by_priority"][p] = counts["by_priority"].get(p, 0) + 1
        s = item["source"]
        counts["by_source"][s] = counts["by_source"].get(s, 0) + 1
        st = item["status"]
        counts["by_status"][st] = counts["by_status"].get(st, 0) + 1

    oldest = ""
    for item in all_items:
        if item["created"] and (not oldest or item["created"] < oldest):
            oldest = item["created"]

    return {
        "needs_action": needs_action,
        "inbox": inbox,
        "all": all_items,
        "counts": counts,
        "oldest": oldest,
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def format_scan_report(scan: dict) -> str:
    """Format the scan result as a Markdown report."""
    lines = [
        f"## Vault Scan — {scan['scanned_at']}",
        "",
    ]

    if scan["needs_action"]:
        lines.append(f"### Needs_Action ({len(scan['needs_action'])} items)")
        lines.append("")
        lines.append("| # | Priority | Title | Source | Created |")
        lines.append("|---|----------|-------|--------|---------|")
        for i, item in enumerate(scan["needs_action"], 1):
            lines.append(
                f"| {i} | {item['priority']} | {item['title']} "
                f"| {item['source']} | {item['created']} |"
            )
        lines.append("")

    if scan["inbox"]:
        lines.append(f"### Inbox ({len(scan['inbox'])} items)")
        lines.append("")
        lines.append("| # | Priority | Title | Source | Created |")
        lines.append("|---|----------|-------|--------|---------|")
        for i, item in enumerate(scan["inbox"], 1):
            lines.append(
                f"| {i} | {item['priority']} | {item['title']} "
                f"| {item['source']} | {item['created']} |"
            )
        lines.append("")

    c = scan["counts"]
    lines.append("### Summary")
    prios = " | ".join(f"{k}: {v}" for k, v in sorted(c["by_priority"].items()))
    lines.append(f"- Priority — {prios}")
    sources = " | ".join(f"{k}: {v}" for k, v in c["by_source"].items())
    lines.append(f"- Sources — {sources}")
    if scan["oldest"]:
        lines.append(f"- Oldest item: {scan['oldest']}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dashboard updater  (SKILL_vault_write logic)
# ---------------------------------------------------------------------------


def update_dashboard(vault_root: Path, scan: dict) -> None:
    """Edit Dashboard.md in place with live data from the scan."""
    dashboard = vault_root / "Dashboard.md"
    text = dashboard.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # --- Update timestamp ---
    text = re.sub(
        r">\s*\*\*Last Updated:\*\*.*",
        f"> **Last Updated:** {now}",
        text,
    )

    # --- Update Messages table ---
    email_count = sum(
        1 for i in scan["all"]
        if any("#email" in str(t) for t in (i.get("tags") or []))
    )
    oldest = scan["oldest"] or "--"
    new_msg_table = (
        "| Source   | Unread | Oldest Pending       |\n"
        "| -------- | ------ | -------------------- |\n"
        f"| Email    | {email_count}      | {oldest}     |\n"
        "| Slack    | 0      | --                   |\n"
        "| SMS      | 0      | --                   |"
    )
    text = re.sub(
        r"\| Source\s+\| Unread.*?(?=\n\n|\n###|\n---)",
        new_msg_table,
        text,
        flags=re.DOTALL,
    )

    # --- Update Needs Reply ---
    high_items = [i for i in scan["all"] if i["priority"] in ("critical", "high")]
    if high_items:
        reply_lines = "\n".join(
            f"- **[{i['priority'].upper()}]** {i['title']}"
            for i in high_items
        )
    else:
        reply_lines = "- _None_"
    text = re.sub(
        r"### Needs Reply\n.*?(?=\n---)",
        f"### Needs Reply\n{reply_lines}\n",
        text,
        flags=re.DOTALL,
    )

    # --- Update Inbox Queue counters ---
    done_today = len(list((vault_root / "Done").glob(
        f"{datetime.now().strftime('%Y%m%d')}*.md"
    )))
    text = re.sub(r"\*\*Items in Inbox:\*\*\s*\d+", f"**Items in Inbox:** {scan['counts']['inbox']}", text)
    text = re.sub(r"\*\*Items Needs_Action:\*\*\s*\d+", f"**Items Needs_Action:** {scan['counts']['needs_action']}", text)
    text = re.sub(r"\*\*Completed Today:\*\*\s*\d+", f"**Completed Today:** {done_today}", text)

    # --- Append to Recent Activity (keep last 10) ---
    activity_row = f"| {datetime.now().strftime('%H:%M')} | Vault scan + dashboard update | {scan['counts']['total']} items scanned |"
    # Find the activity table and insert a row after the header separator.
    activity_marker = "| Time | Action | Result |"
    if activity_marker in text:
        parts = text.split(activity_marker)
        if len(parts) == 2:
            after = parts[1]
            # Find the header separator line, then insert.
            sep_end = after.index("\n", after.index("| ----")) + 1
            after = after[:sep_end] + activity_row + "\n" + after[sep_end:]
            # Trim to 10 data rows.
            table_lines = after.strip().split("\n")
            header_sep = table_lines[0]  # | ---- | ---- | ---- |
            data_lines = table_lines[1:]
            if len(data_lines) > 10:
                data_lines = data_lines[:10]
            after = "\n" + header_sep + "\n" + "\n".join(data_lines) + "\n"
            text = parts[0] + activity_marker + after

    dashboard.write_text(text, encoding="utf-8")
    print(f"[OK] Dashboard.md updated at {now}")


# ---------------------------------------------------------------------------
# Move completed items
# ---------------------------------------------------------------------------


def move_done_items(vault_root: Path, scan: dict) -> int:
    """Move items with status:done to Done/.  Returns count moved."""
    done_dir = vault_root / "Done"
    done_dir.mkdir(exist_ok=True)
    moved = 0

    for item in scan["all"]:
        if item["status"] == "done":
            src: Path = item["filepath"]
            dst = done_dir / src.name
            # Add completed timestamp to frontmatter.
            text = src.read_text(encoding="utf-8")
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            text = text.replace("status: done", f"status: done\ncompleted: {now}", 1)
            dst.write_text(text, encoding="utf-8")
            src.unlink()
            print(f"[MOVED] {src.name} -> Done/")
            moved += 1

    return moved


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------


def log_activity(vault_root: Path, action: str, detail: str) -> None:
    """Append to today's activity log."""
    logs_dir = vault_root / "Logs"
    logs_dir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = logs_dir / f"activity_{today}.log"
    ts = datetime.now().strftime("%H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{ts} | vault_processor | {action} | {detail}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Vault Read/Write Processor")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Print summary without modifying any files.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config) if args.config else load_config()
    vault_root = Path(cfg.get("vault_path", Path(__file__).resolve().parent.parent))

    print(f"=== Vault Processor ===")
    print(f"Vault: {vault_root}")
    print()

    # --- Read ---
    scan = scan_vault(vault_root)
    report = format_scan_report(scan)
    print(report)

    if args.scan_only:
        print("[scan-only mode — no files modified]")
        return

    # --- Write ---
    update_dashboard(vault_root, scan)
    moved = move_done_items(vault_root, scan)
    log_activity(
        vault_root,
        "PROCESS_CYCLE",
        f"scanned={scan['counts']['total']} moved={moved}",
    )
    print(f"\n=== Done.  {scan['counts']['total']} scanned, {moved} moved. ===")


if __name__ == "__main__":
    main()
