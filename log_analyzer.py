"""
log_analyzer.py — Audit Log Summarizer for Personal AI Employee (Gold Tier)

Reads Logs/YYYY-MM-DD.json (NDJSON) and produces:
  - Summary report to stdout
  - Markdown summary to Logs/analysis_YYYY-MM-DD.md

Usage:
    python log_analyzer.py                      # Analyze today's log
    python log_analyzer.py --date 2026-02-24    # Specific date
    python log_analyzer.py --range 2026-02-17 2026-02-24  # Date range
    python log_analyzer.py --tail 50            # Last N events (any date)
    python log_analyzer.py --grep error         # Filter events containing text
    python log_analyzer.py --actor fb_ig_mcp    # Filter by actor
    python log_analyzer.py --errors-only        # Show ERROR + CRITICAL only
    python log_analyzer.py --no-report          # Stdout only, skip .md file
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_VAULT_ROOT  = Path(__file__).resolve().parent
_LOGS_DIR    = _VAULT_ROOT / "Logs"
_WATCHERS    = _VAULT_ROOT / "watchers"
sys.path.insert(0, str(_WATCHERS))

try:
    from audit_logger import list_log_dates, read_log, read_logs_range
    _AUDIT_READER = True
except ImportError:
    _AUDIT_READER = False

# ---------------------------------------------------------------------------
# NDJSON reader (fallback if audit_logger not importable)
# ---------------------------------------------------------------------------


def _read_ndjson(date_str: str) -> list[dict]:
    if _AUDIT_READER:
        return read_log(date_str)
    path = _LOGS_DIR / f"{date_str}.json"
    if not path.exists():
        return []
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def _read_range(start: str, end: str) -> list[dict]:
    if _AUDIT_READER:
        return read_logs_range(start, end)
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    all_events: list[dict] = []
    cur = s
    while cur <= e:
        all_events.extend(_read_ndjson(cur.isoformat()))
        cur += timedelta(days=1)
    return all_events


def _available_dates() -> list[str]:
    if _AUDIT_READER:
        return list_log_dates()
    return sorted(p.stem for p in _LOGS_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"))


# ---------------------------------------------------------------------------
# Analysis engine
# ---------------------------------------------------------------------------


def analyse(events: list[dict]) -> dict[str, Any]:
    """
    Compute summary statistics from a list of audit events.

    Returns a dict with keys:
      total, by_severity, by_actor, by_action, by_result, by_approval_status,
      error_types, top_errors, approval_denied, timeline_by_hour,
      first_event, last_event, critical_events, error_events
    """
    if not events:
        return {"total": 0}

    total = len(events)
    by_severity: Counter        = Counter()
    by_actor: Counter           = Counter()
    by_action: Counter          = Counter()
    by_result: Counter          = Counter()
    by_approval: Counter        = Counter()
    error_messages: Counter     = Counter()
    by_hour: Counter            = Counter()
    critical_events: list[dict] = []
    error_events: list[dict]    = []
    denied_events: list[dict]   = []

    for ev in events:
        sev     = ev.get("severity", "INFO")
        actor   = ev.get("actor", "unknown")
        action  = ev.get("action", "unknown")
        result  = ev.get("result", "unknown")
        approv  = ev.get("approval_status", "unknown")
        err     = ev.get("error")
        ts_str  = ev.get("timestamp", "")

        by_severity[sev]   += 1
        by_actor[actor]    += 1
        by_action[action]  += 1
        by_result[result]  += 1
        by_approval[approv]+= 1

        if err:
            # Extract just the error type prefix (e.g. "ConnectionError" from "ConnectionError: …")
            error_key = err.split(":")[0].strip()[:80]
            error_messages[error_key] += 1

        # Hour-of-day bucketing for timeline
        if ts_str:
            try:
                hour = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).hour
                by_hour[f"{hour:02d}:00"] += 1
            except (ValueError, AttributeError):
                pass

        if sev == "CRITICAL":
            critical_events.append(ev)
        if sev in ("ERROR", "CRITICAL"):
            error_events.append(ev)
        if approv == "denied":
            denied_events.append(ev)

    # Sort timestamps to find first / last
    ts_values = [e.get("timestamp", "") for e in events if e.get("timestamp")]
    ts_values.sort()

    return {
        "total":              total,
        "by_severity":        dict(by_severity.most_common()),
        "by_actor":           dict(by_actor.most_common(10)),
        "by_action":          dict(by_action.most_common(10)),
        "by_result":          dict(by_result.most_common()),
        "by_approval_status": dict(by_approval.most_common()),
        "top_errors":         dict(error_messages.most_common(10)),
        "timeline_by_hour":   dict(sorted(by_hour.items())),
        "first_event":        ts_values[0]  if ts_values else "—",
        "last_event":         ts_values[-1] if ts_values else "—",
        "critical_events":    critical_events,
        "error_events":       error_events,
        "denied_events":      denied_events,
    }


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _bar(value: int, max_val: int, width: int = 20) -> str:
    filled = int(value / max_val * width) if max_val else 0
    return "#" * filled + "." * (width - filled)


def format_report_text(stats: dict, date_label: str = "") -> str:
    """Return a human-readable text summary."""
    if stats.get("total", 0) == 0:
        return f"No audit events found for {date_label or 'requested range'}.\n"

    total    = stats["total"]
    sev      = stats.get("by_severity", {})
    result   = stats.get("by_result", {})
    approv   = stats.get("by_approval_status", {})
    errors   = stats.get("top_errors", {})
    actors   = stats.get("by_actor", {})
    actions  = stats.get("by_action", {})
    crits    = stats.get("critical_events", [])
    err_evs  = stats.get("error_events", [])

    lines: list[str] = []
    sep = "=" * 60

    lines += [
        sep,
        f"  AUDIT LOG ANALYSIS  {date_label}",
        sep,
        f"  Total events : {total}",
        f"  First event  : {stats.get('first_event', '—')}",
        f"  Last event   : {stats.get('last_event', '—')}",
        "",
    ]

    # Severity breakdown
    lines += ["SEVERITY BREAKDOWN", "-" * 40]
    max_sev = max(sev.values(), default=1)
    for level in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
        count = sev.get(level, 0)
        if count:
            lines.append(f"  {level:<10} {count:>5}  {_bar(count, max_sev)}")
    lines.append("")

    # Result breakdown
    lines += ["RESULT BREAKDOWN", "-" * 40]
    max_res = max(result.values(), default=1)
    for res, cnt in sorted(result.items(), key=lambda x: -x[1]):
        lines.append(f"  {res:<20} {cnt:>5}  {_bar(cnt, max_res)}")
    lines.append("")

    # Approval status
    lines += ["APPROVAL STATUS", "-" * 40]
    for status, cnt in sorted(approv.items(), key=lambda x: -x[1]):
        lines.append(f"  {status:<20} {cnt:>5}")
    lines.append("")

    # Top actors
    lines += ["TOP ACTORS (by event count)", "-" * 40]
    max_act = max(actors.values(), default=1)
    for actor, cnt in list(actors.items())[:8]:
        lines.append(f"  {actor:<25} {cnt:>5}  {_bar(cnt, max_act)}")
    lines.append("")

    # Top actions
    lines += ["TOP ACTIONS", "-" * 40]
    for action, cnt in list(actions.items())[:10]:
        lines.append(f"  {action:<30} {cnt:>5}")
    lines.append("")

    # Top error types
    if errors:
        lines += ["TOP ERROR TYPES", "-" * 40]
        for err_type, cnt in list(errors.items())[:8]:
            lines.append(f"  {err_type:<40} {cnt:>5}")
        lines.append("")

    # Recent criticals
    if crits:
        lines += [f"CRITICAL EVENTS ({len(crits)})", "-" * 40]
        for ev in crits[-5:]:  # last 5
            ts     = ev.get("timestamp", "")[:19]
            actor  = ev.get("actor", "?")
            action = ev.get("action", "?")
            err    = (ev.get("error") or "")[:60]
            lines.append(f"  [{ts}] {actor}/{action}  {err}")
        lines.append("")

    # Recent errors
    non_crit_errors = [e for e in err_evs if e.get("severity") != "CRITICAL"]
    if non_crit_errors:
        lines += [f"RECENT ERRORS ({len(non_crit_errors)})", "-" * 40]
        for ev in non_crit_errors[-5:]:
            ts     = ev.get("timestamp", "")[:19]
            actor  = ev.get("actor", "?")
            action = ev.get("action", "?")
            err    = (ev.get("error") or "")[:60]
            lines.append(f"  [{ts}] {actor}/{action}  {err}")
        lines.append("")

    lines.append(sep)
    return "\n".join(lines) + "\n"


def format_report_markdown(stats: dict, date_label: str = "") -> str:
    """Return a Markdown summary for writing to Logs/analysis_*.md."""
    if stats.get("total", 0) == 0:
        return f"# Audit Analysis — {date_label}\n\n_No events found._\n"

    total   = stats["total"]
    sev     = stats.get("by_severity", {})
    result  = stats.get("by_result", {})
    approv  = stats.get("by_approval_status", {})
    errors  = stats.get("top_errors", {})
    actors  = stats.get("by_actor", {})
    actions = stats.get("by_action", {})
    crits   = stats.get("critical_events", [])

    lines = [
        f"---",
        f"type: log_analysis",
        f"date: {date_label}",
        f"generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"total_events: {total}",
        f"errors: {sev.get('ERROR', 0) + sev.get('CRITICAL', 0)}",
        f"---",
        f"",
        f"# Audit Log Analysis — {date_label}",
        f"",
        f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"Total events: {total} · "
        f"First: {stats.get('first_event','—')[:19]} · "
        f"Last: {stats.get('last_event','—')[:19]}_",
        f"",
        f"## Severity Breakdown",
        f"",
        f"| Level | Count | % |",
        f"|-------|-------|---|",
    ]
    for level in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
        cnt = sev.get(level, 0)
        if cnt:
            pct = f"{cnt/total*100:.1f}%"
            lines.append(f"| {level} | {cnt} | {pct} |")

    lines += [
        "",
        "## Result Breakdown",
        "",
        "| Result | Count |",
        "|--------|-------|",
    ]
    for res, cnt in sorted(result.items(), key=lambda x: -x[1]):
        lines.append(f"| {res} | {cnt} |")

    lines += [
        "",
        "## Approval Status",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status, cnt in sorted(approv.items(), key=lambda x: -x[1]):
        lines.append(f"| {status} | {cnt} |")

    lines += [
        "",
        "## Top Actors",
        "",
        "| Actor | Events |",
        "|-------|--------|",
    ]
    for actor, cnt in list(actors.items())[:8]:
        lines.append(f"| {actor} | {cnt} |")

    lines += [
        "",
        "## Top Actions",
        "",
        "| Action | Count |",
        "|--------|-------|",
    ]
    for action, cnt in list(actions.items())[:10]:
        lines.append(f"| {action} | {cnt} |")

    if errors:
        lines += [
            "",
            "## Top Error Types",
            "",
            "| Error Type | Count |",
            "|-----------|-------|",
        ]
        for err_type, cnt in list(errors.items())[:8]:
            lines.append(f"| `{err_type}` | {cnt} |")

    if crits:
        lines += ["", f"## Critical Events ({len(crits)})", ""]
        for ev in crits[-10:]:
            ts     = ev.get("timestamp", "")[:19]
            actor  = ev.get("actor", "?")
            action = ev.get("action", "?")
            err    = ev.get("error") or ""
            lines.append(f"- `[{ts}]` **{actor}/{action}** — {err}")

    denied = stats.get("denied_events", [])
    if denied:
        lines += ["", f"## Denied Approvals ({len(denied)})", ""]
        for ev in denied[-5:]:
            ts     = ev.get("timestamp", "")[:19]
            actor  = ev.get("actor", "?")
            action = ev.get("action", "?")
            lines.append(f"- `[{ts}]` {actor}/{action}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Write analysis markdown
# ---------------------------------------------------------------------------


def write_analysis(stats: dict, date_label: str) -> Path:
    """Write Markdown analysis to Logs/analysis_{date_label}.md."""
    safe_label = date_label.replace(" ", "_").replace("/", "-")
    out_path   = _LOGS_DIR / f"analysis_{safe_label}.md"
    md_text    = format_report_markdown(stats, date_label)
    out_path.write_text(md_text, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def filter_events(
    events: list[dict],
    actor:       str  | None = None,
    grep:        str  | None = None,
    errors_only: bool = False,
    tail:        int  | None = None,
) -> list[dict]:
    out = events
    if errors_only:
        out = [e for e in out if e.get("severity") in ("ERROR", "CRITICAL")]
    if actor:
        out = [e for e in out if e.get("actor", "") == actor]
    if grep:
        g = grep.lower()
        out = [e for e in out
               if g in json.dumps(e, ensure_ascii=False).lower()]
    if tail is not None:
        out = out[-tail:]
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze structured audit logs from Logs/YYYY-MM-DD.json"
    )
    parser.add_argument("--date",        default=None,
                        help="Date to analyze (YYYY-MM-DD). Default: today.")
    parser.add_argument("--range",       nargs=2, metavar=("START", "END"),
                        help="Date range: START END (YYYY-MM-DD).")
    parser.add_argument("--tail",        type=int, default=None,
                        help="Show last N events only.")
    parser.add_argument("--grep",        default=None,
                        help="Filter events containing this text (case-insensitive).")
    parser.add_argument("--actor",       default=None,
                        help="Filter events by actor name.")
    parser.add_argument("--errors-only", action="store_true",
                        help="Show ERROR and CRITICAL events only.")
    parser.add_argument("--no-report",   action="store_true",
                        help="Skip writing analysis Markdown file.")
    parser.add_argument("--list-dates",  action="store_true",
                        help="List all available log dates and exit.")
    args = parser.parse_args()

    # --list-dates
    if args.list_dates:
        dates = _available_dates()
        if dates:
            print("Available log dates:")
            for d in dates:
                path = _LOGS_DIR / f"{d}.json"
                size = path.stat().st_size if path.exists() else 0
                print(f"  {d}  ({size:,} bytes)")
        else:
            print("No audit log files found in Logs/.")
        return

    # Load events
    if args.range:
        start, end = args.range
        events     = _read_range(start, end)
        date_label = f"{start} to {end}"
    else:
        target     = args.date or date.today().isoformat()
        events     = _read_ndjson(target)
        date_label = target

    # Apply filters
    events = filter_events(
        events,
        actor=args.actor,
        grep=args.grep,
        errors_only=args.errors_only,
        tail=args.tail,
    )

    # Analyse
    stats = analyse(events)

    # Print text report
    print(format_report_text(stats, date_label))

    # Write Markdown report
    if not args.no_report and stats.get("total", 0) > 0:
        out_path = write_analysis(stats, date_label)
        print(f"Markdown report written to: {out_path}")


if __name__ == "__main__":
    main()
