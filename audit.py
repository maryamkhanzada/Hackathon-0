#!/usr/bin/env python3
"""
audit.py — Weekly CEO Briefing Generator for Personal AI Employee (Gold Tier)

Triggered weekly (or on-demand), this script reads from every data source in
the vault, computes business metrics, and writes a concise, actionable CEO
Briefing to /Briefings/CEO_Briefing_{ISO-week}.md.

Data sources consumed:
  /Accounting/transactions_{week}.json  — income and expense records
  /Done/*.md                            — completed tasks with frontmatter
  /In_Progress/*.md                     — pipeline items (bottleneck detection)
  /Audits/FB_IG_Summary_*.md            — social media engagement metrics
  /Audits/X_Summary_*.md                — X/Twitter engagement metrics
  /Logs/cross_domain_*.json             — cross-domain integration events
  /Logs/mcp_*.json                      — MCP server health and call logs
  /Logs/social_*.json                   — social posting activity

Analysis performed:
  1. Revenue — total income, expenses, net profit, week-over-week delta
  2. Revenue by category (SaaS, consulting, affiliate, etc.)
  3. Task throughput — Done count, avg priority, source breakdown
  4. Bottlenecks — In_Progress items older than AGE_THRESHOLD days, by priority
  5. Social performance — cross-platform engagement rates and trends
  6. MCP health — uptime, restart count, tool call volume
  7. Suggestions — rule-based actionable items with severity ratings

Real-world resilience:
  - Missing Accounting data → fall back to last known week's averages (FALLBACK mode)
  - Missing Audits → skip social section, flag in briefing
  - Partial Done data → compute from available records, note incompleteness
  - File parse errors → log and continue, note in briefing errors section
  - All errors written to /Logs/audit_{date}.log and noted in briefing

Ralph integration:
  This script is designed to be called by ralph_loop.sh as a processing step.
  It outputs <promise>AUDIT_COMPLETE</promise> on success, allowing the Ralph
  loop to confirm completion and trigger downstream actions (e.g., email the
  briefing, post summary to Slack).

  To invoke via Ralph:
    ./ralph_loop.sh --prompt "Run python audit.py --week current && echo done"

Cron / Task Scheduler setup:
  # Windows Task Scheduler (runs every Monday at 07:00):
  #   Action: python D:\\Hackathon-0\\audit.py --week current
  #   Trigger: Weekly, Monday, 07:00
  #
  # Linux/macOS cron (every Monday at 07:00):
  #   0 7 * * 1 cd /path/to/vault && python audit.py --week current >> Logs/cron_audit.log 2>&1
  #
  # Run for a specific past week:
  #   python audit.py --week 2026-W07
  #
  # Dry-run (print briefing to stdout, don't write file):
  #   python audit.py --dry-run
  #
  # Test with mock data:
  #   python audit.py --test

Usage:
  python audit.py                   # Current ISO week
  python audit.py --week 2026-W08   # Specific week
  python audit.py --test            # Mock data test
  python audit.py --dry-run         # Print, don't save
  python audit.py --ralph           # Ralph-compatible output
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

VAULT_ROOT     = Path(__file__).resolve().parent
ACCOUNTING_DIR = VAULT_ROOT / "Accounting"
DONE_DIR       = VAULT_ROOT / "Done"
IN_PROGRESS_DIR = VAULT_ROOT / "In_Progress"
AUDITS_DIR     = VAULT_ROOT / "Audits"
LOGS_DIR       = VAULT_ROOT / "Logs"
BRIEFINGS_DIR  = VAULT_ROOT / "Briefings"
PLANS_DIR      = VAULT_ROOT / "Plans"
NEEDS_ACTION_DIR = VAULT_ROOT / "Needs_Action"

BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(VAULT_ROOT / ".env")

AGE_THRESHOLD_DAYS = 5     # items older than this in In_Progress = bottleneck
HIGH_EXPENSE_THRESHOLD = 200.0  # flag subscriptions above this if unused
FALLBACK_REVENUE = 8148.00     # last known weekly gross (W07 total income)
FALLBACK_EXPENSES = 1110.77    # last known weekly expenses (W07 total)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [audit] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("audit")

# Unified structured audit logger
_WATCHERS_DIR = VAULT_ROOT / "watchers"
sys.path.insert(0, str(_WATCHERS_DIR))
try:
    from audit_logger import audit_log as _audit_log  # noqa: E402
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False


def _setup_file_log(week_str: str) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    fh = logging.FileHandler(LOGS_DIR / f"audit_{today}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [audit] %(levelname)s: %(message)s"))
    logging.getLogger("audit").addHandler(fh)


# ---------------------------------------------------------------------------
# ISO week helpers
# ---------------------------------------------------------------------------

def current_iso_week() -> str:
    """Return current ISO week string, e.g. '2026-W08'."""
    d = datetime.now()
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"


def week_to_dates(week_str: str) -> tuple[datetime, datetime]:
    """Parse '2026-W08' → (Monday, Sunday) as datetime objects."""
    year, week_num = week_str.split("-W")
    # ISO week starts on Monday
    monday = datetime.strptime(f"{year}-W{week_num}-1", "%G-W%V-%u")
    sunday = monday + timedelta(days=6)
    return monday, sunday


def prior_week(week_str: str) -> str:
    """Return the ISO week string for the week before the given one."""
    monday, _ = week_to_dates(week_str)
    prior_monday = monday - timedelta(weeks=1)
    pw = prior_monday.isocalendar()
    return f"{pw[0]}-W{pw[1]:02d}"


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

class AuditError(Exception):
    pass


def _read_json_safe(path: Path) -> dict | list | None:
    """Read and parse a JSON file; return None and log on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.debug("Not found: %s", path)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error in %s: %s", path.name, exc)
        return None


def _read_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter from a Markdown file. Returns {} on failure."""
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}
        end = text.find("\n---", 3)
        if end == -1:
            return {}
        fm = yaml.safe_load(text[3:end]) or {}
        return fm if isinstance(fm, dict) else {}
    except Exception as exc:
        logger.debug("Frontmatter error in %s: %s", path.name, exc)
        return {}


def load_accounting(week_str: str) -> tuple[dict, bool]:
    """
    Load Accounting/transactions_{week}.json.
    Returns (data_dict, is_fallback).
    If missing, returns fallback estimates and marks is_fallback=True.
    """
    path = ACCOUNTING_DIR / f"transactions_{week_str}.json"
    data = _read_json_safe(path)
    if data and isinstance(data, dict) and "income" in data:
        logger.info("Loaded accounting data: %s", path.name)
        return data, False

    # Fallback: try prior week
    pw = prior_week(week_str)
    prior_path = ACCOUNTING_DIR / f"transactions_{pw}.json"
    prior_data = _read_json_safe(prior_path)
    if prior_data and isinstance(prior_data, dict) and "income" in prior_data:
        logger.warning("Using prior week (%s) accounting as fallback.", pw)
        prior_data["_fallback"] = True
        prior_data["_fallback_source"] = pw
        return prior_data, True

    # Last resort: synthetic fallback
    logger.warning("No accounting data found — using hardcoded fallback estimates.")
    return {
        "_fallback": True,
        "_fallback_source": "hardcoded_estimate",
        "income":   [{"source": "estimate", "category": "unknown",
                      "amount": FALLBACK_REVENUE, "date": "", "description": "estimated"}],
        "expenses": [{"vendor": "estimate", "category": "unknown",
                      "amount": FALLBACK_EXPENSES, "date": "", "description": "estimated"}],
    }, True


def load_done_items(week_str: str) -> list[dict]:
    """
    Load all Done/*.md files, filter to those created during the given week.
    Returns list of frontmatter dicts enriched with 'filename'.
    """
    if not DONE_DIR.is_dir():
        return []
    monday, sunday = week_to_dates(week_str)
    items = []
    for path in DONE_DIR.glob("*.md"):
        fm = _read_frontmatter(path)
        created_str = str(fm.get("created", fm.get("Executed at", "")))
        try:
            # Support "YYYY-MM-DD HH:MM" and "YYYY-MM-DD"
            created = datetime.strptime(created_str[:10], "%Y-%m-%d")
            if monday <= created <= sunday + timedelta(hours=23, minutes=59):
                fm["_filename"] = path.name
                items.append(fm)
        except ValueError:
            # Include all items if we can't parse the date
            fm["_filename"] = path.name
            items.append(fm)
    return items


def load_in_progress_items() -> list[dict]:
    """Load all In_Progress/*.md files. Returns list of enriched frontmatter dicts."""
    if not IN_PROGRESS_DIR.is_dir():
        return []
    items = []
    for path in IN_PROGRESS_DIR.glob("*.md"):
        fm = _read_frontmatter(path)
        fm["_filename"] = path.name
        fm["_path"] = str(path)
        items.append(fm)
    return items


def load_audit_files() -> dict[str, dict]:
    """
    Load the most recent FB/IG/X audit markdown files from Audits/.
    Parses metric tables into dicts. Returns {platform: metrics_dict}.
    """
    result = {}
    if not AUDITS_DIR.is_dir():
        return result

    for platform, prefix in [("fb", "FB_IG_Summary_FB_"), ("ig", "FB_IG_Summary_IG_"), ("x", "X_Summary_")]:
        matches = sorted(AUDITS_DIR.glob(f"{prefix}*.md"), reverse=True)
        if not matches:
            continue
        latest = matches[0]
        text = latest.read_text(encoding="utf-8", errors="ignore")
        metrics = _parse_metric_table(text)
        metrics["_file"] = latest.name
        result[platform] = metrics

    return result


def _parse_metric_table(text: str) -> dict:
    """Extract 'Metric | Value' table rows from an audit markdown file."""
    metrics = {}
    in_table = False
    for line in text.splitlines():
        line = line.strip()
        if "| Metric |" in line or "| Metric|" in line:
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2 and not set(parts[0]) <= set("-"):
                key = parts[0].lower().replace(" ", "_").replace("/", "_")
                val = parts[1]
                try:
                    # Try to parse as number
                    val_clean = val.replace("%", "").replace(",", "").strip()
                    metrics[key] = float(val_clean) if "." in val_clean else int(val_clean)
                except ValueError:
                    metrics[key] = val
    return metrics


def load_mcp_log(date_str: str) -> list[dict]:
    """Load Logs/mcp_{date}.json. Returns [] if missing."""
    path = LOGS_DIR / f"mcp_{date_str}.json"
    data = _read_json_safe(path)
    return data if isinstance(data, list) else []


def load_social_logs(date_str: str) -> tuple[list[dict], list[dict]]:
    """Load Logs/social_{date}.json and Logs/social_x_{date}.json."""
    fb_ig = _read_json_safe(LOGS_DIR / f"social_{date_str}.json") or []
    x     = _read_json_safe(LOGS_DIR / f"social_x_{date_str}.json") or []
    return (fb_ig if isinstance(fb_ig, list) else [],
            x if isinstance(x, list) else [])


# ---------------------------------------------------------------------------
# Metric computations
# ---------------------------------------------------------------------------

def compute_revenue(data: dict) -> dict:
    """Compute revenue metrics from accounting data."""
    income_items   = data.get("income", [])
    expense_items  = data.get("expenses", [])

    total_income   = sum(float(i.get("amount", 0)) for i in income_items)
    total_expenses = sum(float(e.get("amount", 0)) for e in expense_items)
    net_profit     = total_income - total_expenses
    margin_pct     = round(net_profit / total_income * 100, 1) if total_income else 0

    # Category breakdown
    income_by_cat: dict[str, float] = {}
    for item in income_items:
        cat = item.get("category", "other")
        income_by_cat[cat] = income_by_cat.get(cat, 0) + float(item.get("amount", 0))

    expense_by_cat: dict[str, float] = {}
    for item in expense_items:
        cat = item.get("category", "other")
        expense_by_cat[cat] = expense_by_cat.get(cat, 0) + float(item.get("amount", 0))

    # Subscription detection
    subscriptions = [
        e for e in expense_items
        if e.get("category") in ("tooling", "communication", "crm", "design")
    ]
    sub_total = sum(float(s.get("amount", 0)) for s in subscriptions)

    return {
        "total_income":    round(total_income, 2),
        "total_expenses":  round(total_expenses, 2),
        "net_profit":      round(net_profit, 2),
        "profit_margin":   margin_pct,
        "income_by_cat":   {k: round(v, 2) for k, v in sorted(income_by_cat.items(),
                                                                key=lambda x: -x[1])},
        "expense_by_cat":  {k: round(v, 2) for k, v in sorted(expense_by_cat.items(),
                                                                key=lambda x: -x[1])},
        "subscription_total": round(sub_total, 2),
        "subscriptions":   subscriptions,
        "income_items":    income_items,
        "expense_items":   expense_items,
        "is_fallback":     data.get("_fallback", False),
    }


def compute_wow_delta(current: dict, prior: dict) -> dict:
    """Compute week-over-week deltas between two revenue dicts."""
    def pct_change(new, old):
        if old == 0:
            return None
        return round((new - old) / old * 100, 1)

    return {
        "income_delta":   round(current["total_income"] - prior["total_income"], 2),
        "income_pct":     pct_change(current["total_income"], prior["total_income"]),
        "expenses_delta": round(current["total_expenses"] - prior["total_expenses"], 2),
        "net_delta":      round(current["net_profit"] - prior["net_profit"], 2),
        "net_pct":        pct_change(current["net_profit"], prior["net_profit"]),
    }


def detect_bottlenecks(in_progress: list[dict]) -> list[dict]:
    """
    Find items that have been in In_Progress too long or are high/critical priority.
    Returns list sorted by severity (critical first).
    """
    now  = datetime.now()
    bots = []

    for item in in_progress:
        created_str = str(item.get("created", ""))
        try:
            created = datetime.strptime(created_str[:16], "%Y-%m-%d %H:%M")
            age_days = (now - created).days
        except ValueError:
            age_days = 0

        priority = item.get("priority", "medium")
        is_aged  = age_days >= AGE_THRESHOLD_DAYS
        is_urgent = priority in ("critical", "high")

        if is_aged or is_urgent:
            bots.append({
                "filename": item.get("_filename", "unknown"),
                "priority": priority,
                "age_days": age_days,
                "source":   item.get("source", "unknown"),
                "status":   item.get("status", "unknown"),
                "severity": "critical" if priority == "critical" else
                            "high"     if (priority == "high" or age_days > 10) else "medium",
            })

    return sorted(bots, key=lambda x: (
        {"critical": 0, "high": 1, "medium": 2}.get(x["severity"], 3),
        -x["age_days"]
    ))


def generate_suggestions(revenue: dict, prior_revenue: dict,
                          bottlenecks: list[dict], social: dict,
                          in_progress: list[dict]) -> list[dict]:
    """
    Rule-based suggestion engine. Returns list of {severity, text, action} dicts.
    """
    suggestions = []

    # --- Finance rules ---

    # Unused subscriptions above threshold
    for exp in revenue.get("subscriptions", []):
        amt = float(exp.get("amount", 0))
        desc = exp.get("description", "").lower()
        if amt >= HIGH_EXPENSE_THRESHOLD and ("last login" in desc or "unused" in desc or "no activity" in desc):
            suggestions.append({
                "severity": "high",
                "category": "cost_savings",
                "text":     f"Cancel unused subscription: {exp.get('vendor')} (${amt:.2f}/period). {exp.get('description')}",
                "action":   f"Review {exp.get('vendor')} usage and cancel if confirmed unused",
                "saving":   amt,
            })

    # Profit margin below 40%
    margin = revenue.get("profit_margin", 100)
    if margin < 40:
        suggestions.append({
            "severity": "high",
            "category": "finance",
            "text":     f"Profit margin low at {margin}% (target: 40%+). Review expense categories.",
            "action":   "Audit top 3 expense categories for reduction opportunities",
            "saving":   None,
        })

    # High expense growth (>20% WoW)
    wow = compute_wow_delta(revenue, prior_revenue)
    if wow.get("expenses_delta", 0) > revenue["total_expenses"] * 0.20:
        suggestions.append({
            "severity": "medium",
            "category": "finance",
            "text":     f"Expenses grew {wow['expenses_delta']:+.2f} vs prior week. Investigate new charges.",
            "action":   "Review new expense line items added this week",
            "saving":   None,
        })

    # Revenue growth opportunity — consulting as % of revenue
    consulting = revenue.get("income_by_cat", {}).get("consulting", 0)
    saas = revenue.get("income_by_cat", {}).get("saas_subscription", 0)
    if saas > 0 and consulting / max(saas, 1) < 0.5:
        suggestions.append({
            "severity": "low",
            "category": "growth",
            "text":     f"SaaS (${saas:.0f}) dominates revenue. Consulting (${consulting:.0f}) is {consulting/max(saas,1)*100:.0f}% — upside potential.",
            "action":   "Package consulting offers for top 3 enterprise clients",
            "saving":   None,
        })

    # --- Task bottleneck rules ---

    critical_bots = [b for b in bottlenecks if b["severity"] == "critical"]
    if critical_bots:
        for b in critical_bots[:3]:
            suggestions.append({
                "severity": "critical",
                "category": "operations",
                "text":     f"Critical item stuck {b['age_days']}d: {b['filename']} ({b['source']})",
                "action":   f"Resolve or escalate {b['filename']} immediately",
                "saving":   None,
            })

    aged_bots = [b for b in bottlenecks if b["age_days"] >= AGE_THRESHOLD_DAYS]
    if len(aged_bots) > 5:
        suggestions.append({
            "severity": "high",
            "category": "operations",
            "text":     f"{len(aged_bots)} items have been In_Progress for 5+ days. Pipeline congestion risk.",
            "action":   "Review and re-triage In_Progress queue; close or escalate aged items",
            "saving":   None,
        })

    # Pending approvals piling up
    pending_count = len(list((VAULT_ROOT / "Pending_Approval").glob("*.md"))) if (VAULT_ROOT / "Pending_Approval").is_dir() else 0
    if pending_count >= 5:
        suggestions.append({
            "severity": "medium",
            "category": "operations",
            "text":     f"{pending_count} items awaiting approval in Pending_Approval/.",
            "action":   "Review and clear approval queue — some may be stale",
            "saving":   None,
        })

    # --- Social media rules ---

    for platform, metrics in social.items():
        rate = metrics.get("engagement_rate", metrics.get("engagement_rate_pct", 0))
        if isinstance(rate, (int, float)) and rate < 1.0:
            suggestions.append({
                "severity": "medium",
                "category": "marketing",
                "text":     f"{platform.upper()} engagement rate low at {rate}% (target: 1%+).",
                "action":   f"Review {platform.upper()} content mix — increase interactive/question posts",
                "saving":   None,
            })

    # --- Growth signals ---

    new_clients = [i for i in revenue.get("income_items", [])
                   if "first payment" in i.get("description", "").lower()]
    if new_clients:
        total_new = sum(float(i.get("amount", 0)) for i in new_clients)
        suggestions.append({
            "severity": "low",
            "category": "growth",
            "text":     f"{len(new_clients)} new client(s) onboarded this week (${total_new:.0f} new ARR). Assign dedicated CSM.",
            "action":   "Schedule onboarding calls for new clients; assign CSM within 48h",
            "saving":   None,
        })

    return sorted(suggestions, key=lambda s: (
        {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(s["severity"], 4)
    ))


def compute_task_throughput(done_items: list[dict]) -> dict:
    """Summarise task completion for the week."""
    if not done_items:
        return {"total": 0, "by_priority": {}, "by_source": {}, "note": "No done items found this week"}

    by_priority: dict[str, int] = {}
    by_source:   dict[str, int] = {}
    for item in done_items:
        p = item.get("priority", "unknown")
        s = item.get("source",   "unknown")
        by_priority[p] = by_priority.get(p, 0) + 1
        by_source[s]   = by_source.get(s, 0) + 1

    return {
        "total":       len(done_items),
        "by_priority": dict(sorted(by_priority.items())),
        "by_source":   dict(sorted(by_source.items(), key=lambda x: -x[1])),
    }


def compute_social_summary(audit_data: dict) -> dict:
    """Flatten social metrics into a clean summary dict per platform."""
    summary = {}
    for platform, metrics in audit_data.items():
        summary[platform] = {
            "posts_analyzed":   metrics.get("posts_analyzed", "N/A"),
            "total_likes":      metrics.get("total_likes", "N/A"),
            "total_retweets":   metrics.get("total_retweets", metrics.get("total_retweets", "N/A")),
            "total_comments":   metrics.get("total_comments", "N/A"),
            "engagement_rate":  metrics.get("engagement_rate", metrics.get("engagement_rate_pct", "N/A")),
            "avg_per_post":     metrics.get("avg_engagement_per_post", metrics.get("avg_likes_per_tweet", "N/A")),
            "source_file":      metrics.get("_file", "N/A"),
        }
    return summary


# ---------------------------------------------------------------------------
# Briefing writer
# ---------------------------------------------------------------------------

def _arrow(delta) -> str:
    """Return ASCII trend arrow for a numeric delta."""
    if delta is None: return " —"
    if delta > 0:  return f"+{delta:,.2f} ^"
    if delta < 0:  return f"{delta:,.2f} v"
    return " 0.00 ="


def _pct_str(pct) -> str:
    if pct is None: return "N/A"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def write_briefing(
    week_str:       str,
    revenue:        dict,
    prior_revenue:  dict,
    wow:            dict,
    throughput:     dict,
    bottlenecks:    list[dict],
    social:         dict,
    suggestions:    list[dict],
    mcp_events:     list[dict],
    errors:         list[str],
    dry_run:        bool = False,
) -> str:
    """
    Render and write /Briefings/CEO_Briefing_{week}.md.
    Returns the file path (or the rendered text if dry_run=True).
    """
    monday, sunday = week_to_dates(week_str)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ----- YAML frontmatter -----
    fallback_note = " (FALLBACK ESTIMATES — see notes)" if revenue.get("is_fallback") else ""
    fm = (
        f"---\n"
        f"type: ceo_briefing\n"
        f"period: \"{week_str}\"\n"
        f"week_start: \"{monday.strftime('%Y-%m-%d')}\"\n"
        f"week_end: \"{sunday.strftime('%Y-%m-%d')}\"\n"
        f"generated: \"{now}\"\n"
        f"generated_by: audit.py\n"
        f"data_fallback: {str(revenue.get('is_fallback', False)).lower()}\n"
        f"status: final\n"
        f"tags: [\"#briefing\", \"#ceo\", \"#weekly\", \"#audit\"]\n"
        f"---\n\n"
    )

    # ----- Executive Summary -----
    revenue_signal = "strong" if wow.get("income_pct", 0) > 5 else \
                     "flat"   if abs(wow.get("income_pct", 0)) <= 5 else "declining"
    critical_count = sum(1 for s in suggestions if s["severity"] == "critical")
    high_count     = sum(1 for s in suggestions if s["severity"] == "high")

    exec_summary = (
        f"## Executive Summary\n\n"
        f"**Period:** {monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')} ({week_str}){fallback_note}\n\n"
        f"Revenue is **{revenue_signal}** ({_pct_str(wow.get('income_pct'))} WoW). "
        f"Net profit: **${revenue['net_profit']:,.2f}** at **{revenue['profit_margin']}% margin**. "
        f"**{throughput['total']} tasks** completed this week. "
        f"**{len(bottlenecks)} bottleneck(s)** detected in pipeline. "
        f"**{critical_count} critical** and **{high_count} high** priority actions require attention.\n\n"
    )

    # ----- Revenue table -----
    # Category rows
    all_cats = sorted(set(
        list(revenue["income_by_cat"].keys()) +
        list(prior_revenue["income_by_cat"].keys())
    ))
    cat_rows = "\n".join(
        f"| {cat.replace('_', ' ').title()} | "
        f"${revenue['income_by_cat'].get(cat, 0):,.2f} | "
        f"${prior_revenue['income_by_cat'].get(cat, 0):,.2f} | "
        f"{_arrow(revenue['income_by_cat'].get(cat, 0) - prior_revenue['income_by_cat'].get(cat, 0))} |"
        for cat in all_cats
    )

    revenue_section = (
        f"## Revenue\n\n"
        f"| Metric | This Week ({week_str}) | Prior Week ({prior_week(week_str)}) | WoW Change |\n"
        f"|--------|----------------------|-----------------------------------|------------|\n"
        f"| **Gross Revenue** | **${revenue['total_income']:,.2f}** | ${prior_revenue['total_income']:,.2f} | "
        f"{_arrow(wow['income_delta'])} ({_pct_str(wow['income_pct'])}) |\n"
        f"| **Total Expenses** | **${revenue['total_expenses']:,.2f}** | ${prior_revenue['total_expenses']:,.2f} | "
        f"{_arrow(wow['expenses_delta'])} |\n"
        f"| **Net Profit** | **${revenue['net_profit']:,.2f}** | ${prior_revenue['net_profit']:,.2f} | "
        f"{_arrow(wow['net_delta'])} ({_pct_str(wow['net_pct'])}) |\n"
        f"| Profit Margin | {revenue['profit_margin']}% | {prior_revenue['profit_margin']}% | — |\n\n"
        f"### Revenue by Category\n\n"
        f"| Category | This Week | Prior Week | Change |\n"
        f"|----------|-----------|------------|--------|\n"
        f"{cat_rows}\n\n"
        f"### Top Expenses This Week\n\n"
    )

    # Top 5 expenses
    top_expenses = sorted(
        revenue.get("expense_items", []),
        key=lambda x: float(x.get("amount", 0)), reverse=True
    )[:5]
    exp_rows = "\n".join(
        f"| {e.get('vendor', '—')} | {e.get('category', '—')} | ${float(e.get('amount', 0)):,.2f} | {e.get('description', '—')[:60]} |"
        for e in top_expenses
    )
    revenue_section += (
        f"| Vendor | Category | Amount | Description |\n"
        f"|--------|----------|--------|-------------|\n"
        f"{exp_rows}\n\n"
    )

    # ----- Task throughput -----
    pri_rows = "\n".join(f"| {p.title()} | {c} |" for p, c in throughput.get("by_priority", {}).items())
    src_rows = "\n".join(f"| {s} | {c} |" for s, c in list(throughput.get("by_source", {}).items())[:5])
    throughput_section = (
        f"## Task Throughput\n\n"
        f"**Tasks completed this week:** {throughput['total']}\n\n"
        f"### By Priority\n\n"
        f"| Priority | Count |\n|----------|-------|\n{pri_rows if pri_rows else '| — | 0 |'}\n\n"
        f"### By Source\n\n"
        f"| Source | Count |\n|--------|-------|\n{src_rows if src_rows else '| — | 0 |'}\n\n"
    )

    # ----- Bottlenecks -----
    if bottlenecks:
        bot_rows = "\n".join(
            f"| {b['filename'][:50]} | {b['priority'].upper()} | {b['age_days']}d | {b['source']} | {b['status']} |"
            for b in bottlenecks[:10]
        )
        bottleneck_section = (
            f"## Bottlenecks\n\n"
            f"**{len(bottlenecks)} item(s)** are aged or high-priority in the pipeline "
            f"(threshold: {AGE_THRESHOLD_DAYS} days).\n\n"
            f"| Item | Priority | Age | Source | Status |\n"
            f"|------|----------|-----|--------|--------|\n"
            f"{bot_rows}\n\n"
        )
    else:
        bottleneck_section = "## Bottlenecks\n\n_No bottlenecks detected this week._\n\n"

    # ----- Social performance -----
    if social:
        soc_rows = "\n".join(
            f"| {p.upper()} | {m.get('posts_analyzed', '—')} | {m.get('total_likes', '—')} | "
            f"{m.get('total_comments', '—')} | {m.get('engagement_rate', '—')}% | {m.get('avg_per_post', '—')} |"
            for p, m in social.items()
        )
        social_section = (
            f"## Social Media Performance\n\n"
            f"| Platform | Posts | Likes | Comments | Eng. Rate | Avg/Post |\n"
            f"|----------|-------|-------|----------|-----------|----------|\n"
            f"{soc_rows}\n\n"
        )
    else:
        social_section = "## Social Media Performance\n\n_No social audit data available for this period._\n\n"

    # ----- MCP health -----
    mcp_calls    = sum(1 for e in mcp_events if e.get("event") == "tool_call")
    mcp_restarts = sum(1 for e in mcp_events if e.get("event") == "server_restart")
    mcp_queued   = sum(1 for e in mcp_events if e.get("event") == "task_queued")
    mcp_section  = (
        f"## MCP Infrastructure\n\n"
        f"| Metric | Value |\n|--------|-------|\n"
        f"| Tool Calls | {mcp_calls} |\n"
        f"| Server Restarts | {mcp_restarts} |\n"
        f"| Tasks Queued (offline) | {mcp_queued} |\n\n"
    )

    # ----- Suggestions (checkboxes, sorted by severity) -----
    sev_icons = {"critical": "[CRITICAL]", "high": "[HIGH]", "medium": "[MEDIUM]", "low": "[LOW]"}
    suggestion_items = "\n".join(
        f"- [ ] **{sev_icons.get(s['severity'], s['severity'].upper())} {s['category'].replace('_',' ').title()}:** "
        f"{s['text']}"
        + (f"  _(Save: ${s['saving']:.2f})_" if s.get("saving") else "")
        + f"\n  _Action: {s['action']}_"
        for s in suggestions
    ) or "- _No suggestions this week._"

    suggestions_section = f"## Suggestions\n\n{suggestion_items}\n\n"

    # ----- Next week priorities -----
    priorities = []
    for s in suggestions[:3]:
        priorities.append(f"- [ ] {s['action']}")
    if not priorities:
        priorities.append("- [ ] Review pipeline and clear Pending_Approval queue")
    next_week_section = f"## Next Week Priorities\n\n" + "\n".join(priorities) + "\n\n"

    # ----- Errors / notes -----
    errors_section = ""
    if errors:
        err_lines = "\n".join(f"- {e}" for e in errors)
        errors_section = f"## Data Notes\n\n{err_lines}\n\n"
    if revenue.get("is_fallback"):
        errors_section += (
            f"## Fallback Data Warning\n\n"
            f"Accounting data for {week_str} was not found. "
            f"Revenue figures are estimated from `{revenue.get('_fallback_source', 'prior data')}`. "
            f"Metrics marked with * are estimates.\n\n"
        )

    # ----- Footer -----
    footer = f"---\n\n_Generated by `audit.py` at {now}. Integrate with Ralph loop for automated delivery._\n"

    full_text = (
        fm
        + f"# CEO Briefing — {week_str}\n\n"
        + exec_summary
        + "---\n\n"
        + revenue_section
        + "---\n\n"
        + throughput_section
        + "---\n\n"
        + bottleneck_section
        + "---\n\n"
        + social_section
        + "---\n\n"
        + mcp_section
        + "---\n\n"
        + suggestions_section
        + "---\n\n"
        + next_week_section
        + ("---\n\n" + errors_section if errors_section else "")
        + footer
    )

    if dry_run:
        return full_text

    out_path = BRIEFINGS_DIR / f"CEO_Briefing_{week_str}.md"
    out_path.write_text(full_text, encoding="utf-8")
    logger.info("Briefing written to: %s", out_path)
    return str(out_path)


# ---------------------------------------------------------------------------
# Ralph integration helper
# ---------------------------------------------------------------------------

def log_audit_event(week_str: str, status: str, briefing_path: str = "",
                     error: str = "") -> None:
    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"audit_{today}.json"
    existing: list = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    existing.append({
        "timestamp":    datetime.now().isoformat(),
        "event":        "audit_run",
        "week":         week_str,
        "status":       status,
        "briefing":     briefing_path,
        "error":        error,
    })
    log_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    # Mirror to unified audit log
    if _AUDIT_AVAILABLE:
        _audit_log(
            actor="audit",
            action="weekly_briefing",
            params={"week": week_str, "briefing_path": briefing_path},
            result="success" if status == "ok" else "failure",
            approval_status="not_required",
            severity="ERROR" if error else "INFO",
            source_file="audit.py",
            error=error or None,
        )


# ---------------------------------------------------------------------------
# Main audit runner
# ---------------------------------------------------------------------------

def run_audit(week_str: str, dry_run: bool = False, ralph_mode: bool = False) -> str:
    """
    Full audit pipeline. Returns briefing path (or text if dry_run).
    Raises AuditError on unrecoverable failure.
    """
    _setup_file_log(week_str)
    logger.info("Starting audit for %s (dry_run=%s)", week_str, dry_run)
    errors: list[str] = []
    today  = datetime.now().strftime("%Y-%m-%d")
    pw     = prior_week(week_str)

    # 1. Load accounting data (with fallback)
    acc_current, is_fallback = load_accounting(week_str)
    acc_prior,   _           = load_accounting(pw)
    if is_fallback:
        errors.append(f"Accounting data for {week_str} not found — using fallback from {acc_current.get('_fallback_source')}")

    # 2. Compute revenue metrics
    revenue       = compute_revenue(acc_current)
    prior_revenue = compute_revenue(acc_prior)
    wow           = compute_wow_delta(revenue, prior_revenue)

    # 3. Done items throughput
    done_items = load_done_items(week_str)
    throughput = compute_task_throughput(done_items)
    if not done_items:
        errors.append("No Done/ items found for this week — throughput metrics unavailable")

    # 4. Bottleneck detection
    in_progress = load_in_progress_items()
    bottlenecks = detect_bottlenecks(in_progress)

    # 5. Social audit
    audit_files = load_audit_files()
    social      = compute_social_summary(audit_files)
    if not audit_files:
        errors.append("No social audit files found in Audits/ — social section omitted")

    # 6. MCP log
    mcp_events = load_mcp_log(today)

    # 7. Suggestions
    suggestions = generate_suggestions(revenue, prior_revenue, bottlenecks, social, in_progress)

    # 8. Write briefing
    result = write_briefing(
        week_str=week_str,
        revenue=revenue,
        prior_revenue=prior_revenue,
        wow=wow,
        throughput=throughput,
        bottlenecks=bottlenecks,
        social=social,
        suggestions=suggestions,
        mcp_events=mcp_events,
        errors=errors,
        dry_run=dry_run,
    )

    log_audit_event(week_str, "success", briefing_path=result if not dry_run else "dry_run")
    logger.info("Audit complete for %s", week_str)

    if ralph_mode:
        # Signal to ralph_loop.sh that the task is complete
        print(f"<promise>AUDIT_COMPLETE</promise>")
        print(f"Briefing: {result}")

    return result


# ---------------------------------------------------------------------------
# Mock data test
# ---------------------------------------------------------------------------

def run_test() -> None:
    """Run audit against the mock Accounting data and print results."""
    print("[audit.py] Running test with mock data (DRY_RUN=False, week=2026-W08)...\n")

    result = run_audit("2026-W08", dry_run=False, ralph_mode=False)

    print(f"\n[audit.py] CEO Briefing generated: {result}")
    print("[audit.py] Test complete.")
    print("\n<promise>AUDIT_COMPLETE</promise>")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Weekly CEO Briefing Generator — Gold Tier Personal AI Employee",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python audit.py                       # Current ISO week
  python audit.py --week 2026-W08       # Specific week
  python audit.py --test                # Mock data test
  python audit.py --dry-run             # Print briefing, don't save
  python audit.py --ralph               # Ralph-compatible (outputs promise tag)

Cron (every Monday 07:00):
  0 7 * * 1 cd /d/Hackathon-0 && python audit.py --ralph >> Logs/cron_audit.log 2>&1
        """,
    )
    parser.add_argument("--week",    type=str, default=None,  help="ISO week (e.g. 2026-W08)")
    parser.add_argument("--test",    action="store_true",      help="Run test with mock data")
    parser.add_argument("--dry-run", action="store_true",      help="Print briefing, don't write file")
    parser.add_argument("--ralph",   action="store_true",      help="Ralph loop mode (outputs promise tag)")
    parser.add_argument("--vault",   type=str, default=None,   help="Override vault path")
    args = parser.parse_args()

    if args.test:
        run_test()
        return

    week_str = args.week or current_iso_week()

    try:
        result = run_audit(week_str, dry_run=args.dry_run, ralph_mode=args.ralph)
        if args.dry_run:
            print(result)
        else:
            print(f"Briefing written to: {result}")
    except AuditError as exc:
        logger.error("Audit failed: %s", exc)
        log_audit_event(week_str, "failed", error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
