#!/usr/bin/env python3
"""
fb_ig_mcp.py — Facebook & Instagram MCP Server for Personal AI Employee (Gold Tier)

Provides MCP tools to Claude Code for Facebook/Instagram integration:
  - draft_fb_post     : Save a Facebook post draft to Plans/ (no publish)
  - draft_ig_post     : Save an Instagram post draft to Plans/ (no publish)
  - post_fb           : Publish to Facebook (HITL required — approval file must exist)
  - post_ig           : Publish to Instagram (HITL required — approval file must exist)
  - fetch_fb_summary  : Fetch FB page posts/likes/comments → Audits/
  - fetch_ig_summary  : Fetch IG account posts/likes/comments → Audits/

Authentication:
  - Facebook Graph API via facebook-sdk (page access token)
  - Instagram Basic Display API or Graph API (business account)
  - Credentials loaded from .env — NEVER hardcoded

Error handling:
  - Retry 3x with 30s exponential backoff on API failure
  - Playwright fallback for fetch_*_summary if API quota exceeded
  - All events logged to Logs/social_{date}.json

Security:
  - post_fb / post_ig verify approval file in Approved/ before executing
  - DRY_RUN=true mode logs all actions without calling any API
  - Max 5 posts/day enforced (FB/IG rule from Handbook)

Setup:
  1. pip install facebook-sdk instabot python-dotenv pyyaml playwright mcp
  2. playwright install chromium
  3. Copy .env.example -> .env and fill in credentials
  4. Add fb_ig entry to .claude/mcp.json (see SKILL_FB_IG.md)

Usage (standalone test):
  DRY_RUN=true python fb_ig_mcp.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# MCP SDK import (graceful fallback for environments without mcp installed)
# ---------------------------------------------------------------------------

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("[fb_ig_mcp] WARNING: mcp package not installed. Install with: pip install mcp", file=sys.stderr)

# ---------------------------------------------------------------------------
# Optional API libraries (graceful fallback to dry-run / Playwright mode)
# ---------------------------------------------------------------------------

try:
    import facebook
    FB_SDK_AVAILABLE = True
except ImportError:
    FB_SDK_AVAILABLE = False

try:
    from instabot import Bot as InstaBot
    INSTABOT_AVAILABLE = True
except ImportError:
    INSTABOT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Environment & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

VAULT_PATH = Path(os.environ.get("VAULT_PATH", SCRIPT_DIR))
PLANS_DIR = VAULT_PATH / "Plans"
AUDITS_DIR = VAULT_PATH / "Audits"
LOGS_DIR = VAULT_PATH / "Logs"
PENDING_DIR = VAULT_PATH / "Pending_Approval"
APPROVED_DIR = VAULT_PATH / "Approved"
IN_PROGRESS_DIR = VAULT_PATH / "In_Progress"

for d in (PLANS_DIR, AUDITS_DIR, LOGS_DIR, PENDING_DIR):
    d.mkdir(parents=True, exist_ok=True)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 30
MAX_POSTS_PER_DAY = 5  # Handbook rule

# Facebook credentials
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "")

# Instagram credentials
IG_USERNAME = os.environ.get("IG_USERNAME", "")
IG_PASSWORD = os.environ.get("IG_PASSWORD", "")
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
IG_ACCOUNT_ID = os.environ.get("IG_ACCOUNT_ID", "")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [fb_ig_mcp] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("fb_ig_mcp")

# Unified structured audit logger
sys.path.insert(0, str(SCRIPT_DIR / "watchers"))
try:
    from audit_logger import audit_log as _audit_log  # noqa: E402
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False


def log_social(entry: dict) -> None:
    """Append an event to the daily social log JSON AND to the unified audit log."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"social_{today}.json"
    existing: list = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append({"timestamp": datetime.now().isoformat(), **entry})
    log_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    # Mirror to unified audit log
    if _AUDIT_AVAILABLE:
        _audit_log(
            actor="fb_ig_mcp",
            action=entry.get("action", "social_event"),
            params={k: v for k, v in entry.items()
                    if k not in ("timestamp", "action", "status")},
            result=entry.get("status", "unknown"),
            approval_status=("approved" if entry.get("approval_file") else "not_required"),
            severity=("ERROR" if entry.get("status") == "failed" else "INFO"),
            source_file="fb_ig_mcp.py",
            error=entry.get("error"),
        )


# ---------------------------------------------------------------------------
# Daily post counter (enforce MAX_POSTS_PER_DAY)
# ---------------------------------------------------------------------------

def _posts_today() -> int:
    """Count actual posts made today from the social log."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"social_{today}.json"
    if not log_path.exists():
        return 0
    try:
        entries = json.loads(log_path.read_text(encoding="utf-8"))
        return sum(
            1 for e in entries
            if e.get("action") in ("post_fb", "post_ig") and e.get("status") == "posted"
        )
    except (json.JSONDecodeError, OSError):
        return 0


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def with_retry(fn, label: str, max_retries: int = MAX_RETRIES, backoff: int = RETRY_BACKOFF_SECONDS):
    """Call fn() up to max_retries times with exponential backoff."""
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            log_social({
                "action": label,
                "status": "retry",
                "attempt": attempt,
                "max_retries": max_retries,
                "error": str(exc),
            })
            logger.warning("[%s] attempt %d/%d failed: %s", label, attempt, max_retries, exc)
            if attempt < max_retries:
                wait = backoff * attempt
                logger.info("[%s] retrying in %ds...", label, wait)
                time.sleep(wait)
    raise RuntimeError(f"{label} failed after {max_retries} attempts: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# HITL approval guard
# ---------------------------------------------------------------------------

def _check_approval(draft_filename: str) -> bool:
    """
    Return True only if an approval file for this draft exists in Approved/.
    The approval file must be named APPROVAL_{draft_filename} or contain
    the draft filename in its body.
    """
    if not APPROVED_DIR.exists():
        return False
    approval_name = f"APPROVAL_{draft_filename}"
    if (APPROVED_DIR / approval_name).exists():
        return True
    # Fallback: scan Approved/ for any file referencing this draft
    for f in APPROVED_DIR.glob("*.md"):
        if draft_filename in f.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


# ---------------------------------------------------------------------------
# Draft helpers
# ---------------------------------------------------------------------------

def _write_draft(platform: str, content: str, caption: str, hashtags: str,
                 image_path: str, notes: str, source_item: str) -> dict:
    """Write a draft post MD file to Plans/ and a matching approval request to Pending_Approval/."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    draft_filename = f"FB_IG_DRAFT_{platform.upper()}_{ts}.md"
    draft_path = PLANS_DIR / draft_filename
    approval_filename = f"APPROVAL_{draft_filename}"
    approval_path = PENDING_DIR / approval_filename

    draft_md = f"""---
type: {platform}_post_draft
status: draft
created: {datetime.now().strftime('%Y-%m-%d %H:%M')}
platform: {platform}
requires_approval: yes
approval_file: Pending_Approval/{approval_filename}
source_item: "{source_item}"
tags: ["#{platform}-draft", "#HITL", "#social"]
---

# {platform.upper()} Post Draft — {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Content

{content}

## Caption / Headline

{caption}

## Hashtags

{hashtags if hashtags else "_none specified_"}

## Image / Media Path

{image_path if image_path else "_no image_"}

## Notes

{notes if notes else "_none_"}

---

## Approval Checklist

- [ ] Review content for brand voice
- [ ] Check hashtags relevance
- [ ] Verify image/media is appropriate
- [ ] Approve: move `Pending_Approval/{approval_filename}` → `Approved/`
- [ ] Reject:  move to `Rejected/`

**WARNING:** This draft will NOT be posted until explicitly approved.
"""

    approval_md = f"""---
type: hitl_approval_request
action: post_{platform}
status: pending
created: {datetime.now().strftime('%Y-%m-%d %H:%M')}
draft_file: Plans/{draft_filename}
platform: {platform}
priority: medium
tags: ["#HITL", "#approval", "#{platform}"]
---

# Approval Request: {platform.upper()} Post

**Action:** Publish post to {platform.upper()}
**Draft:** `Plans/{draft_filename}`
**Created:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Post Preview

> {content[:300]}{'...' if len(content) > 300 else ''}

## To Approve

Move this file to `Approved/` folder.

## To Reject

Move this file to `Rejected/` folder.

**Agent will NOT post without this file in Approved/.**
"""

    draft_path.write_text(draft_md, encoding="utf-8")
    approval_path.write_text(approval_md, encoding="utf-8")

    log_social({
        "action": f"draft_{platform}_post",
        "status": "created",
        "draft_file": str(draft_path),
        "approval_file": str(approval_path),
        "platform": platform,
        "dry_run": DRY_RUN,
    })
    logger.info("Draft created: %s", draft_path.name)

    return {
        "draft_file": f"Plans/{draft_filename}",
        "approval_file": f"Pending_Approval/{approval_filename}",
        "status": "draft_created",
        "message": (
            f"{platform.upper()} post draft saved to Plans/{draft_filename}.\n"
            f"Approval request created at Pending_Approval/{approval_filename}.\n"
            f"Move the approval file to Approved/ to authorize posting."
        ),
    }


# ---------------------------------------------------------------------------
# Playwright fallback for summary fetch
# ---------------------------------------------------------------------------

def _playwright_fetch_summary(platform: str) -> dict:
    """
    Fallback: use Playwright headless browser to scrape basic public stats
    when the Graph API quota is exceeded.  Returns a minimal dict.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "playwright not installed", "fallback": "playwright"}

    logger.info("[%s] Using Playwright fallback for summary fetch", platform)
    log_social({"action": f"fetch_{platform}_summary", "status": "playwright_fallback"})

    # Playwright scraping of public pages — read-only, no auth required for public data.
    # Returns stub structure; extend URL + selectors per your page.
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            if platform == "fb":
                url = f"https://www.facebook.com/{FB_PAGE_ID}" if FB_PAGE_ID else "https://www.facebook.com"
            else:
                url = f"https://www.instagram.com/{IG_USERNAME}/" if IG_USERNAME else "https://www.instagram.com"
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            title = page.title()
        except Exception as exc:
            browser.close()
            return {"error": str(exc), "fallback": "playwright"}
        browser.close()

    return {
        "platform": platform,
        "source": "playwright_fallback",
        "page_title": title,
        "note": "Playwright fallback — only public page title retrieved. Configure Graph API for full metrics.",
    }


# ---------------------------------------------------------------------------
# Summary fetch helpers
# ---------------------------------------------------------------------------

def _fetch_fb_summary_api() -> dict:
    """Call Facebook Graph API for page posts, likes, comments."""
    if not FB_SDK_AVAILABLE:
        raise ImportError("facebook-sdk not installed. Run: pip install facebook-sdk")
    if not FB_PAGE_TOKEN:
        raise ValueError("FB_PAGE_ACCESS_TOKEN not set in .env")

    graph = facebook.GraphAPI(access_token=FB_PAGE_TOKEN, version="17.0")
    posts = graph.get_connections(FB_PAGE_ID, "posts", fields="message,created_time,likes.summary(true),comments.summary(true)")
    items = posts.get("data", [])[:10]  # cap at 10 recent posts
    total_likes = sum(p.get("likes", {}).get("summary", {}).get("total_count", 0) for p in items)
    total_comments = sum(p.get("comments", {}).get("summary", {}).get("total_count", 0) for p in items)
    engagement_rate = round((total_likes + total_comments) / max(len(items), 1), 2)

    return {
        "platform": "facebook",
        "posts_analyzed": len(items),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "avg_engagement_per_post": engagement_rate,
        "posts": [
            {
                "id": p.get("id"),
                "message_preview": (p.get("message", "")[:80] + "...") if p.get("message") else "(no text)",
                "created_time": p.get("created_time"),
                "likes": p.get("likes", {}).get("summary", {}).get("total_count", 0),
                "comments": p.get("comments", {}).get("summary", {}).get("total_count", 0),
            }
            for p in items
        ],
    }


def _fetch_ig_summary_api() -> dict:
    """Call Instagram Graph API for media posts, likes, comments."""
    if not IG_ACCESS_TOKEN or not IG_ACCOUNT_ID:
        raise ValueError("IG_ACCESS_TOKEN and IG_ACCOUNT_ID must be set in .env")

    import urllib.request
    url = (
        f"https://graph.facebook.com/v17.0/{IG_ACCOUNT_ID}/media"
        f"?fields=id,caption,timestamp,like_count,comments_count"
        f"&access_token={IG_ACCESS_TOKEN}&limit=10"
    )
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    items = data.get("data", [])
    total_likes = sum(p.get("like_count", 0) for p in items)
    total_comments = sum(p.get("comments_count", 0) for p in items)
    engagement_rate = round((total_likes + total_comments) / max(len(items), 1), 2)

    return {
        "platform": "instagram",
        "posts_analyzed": len(items),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "avg_engagement_per_post": engagement_rate,
        "posts": [
            {
                "id": p.get("id"),
                "caption_preview": (p.get("caption", "")[:80] + "...") if p.get("caption") else "(no caption)",
                "timestamp": p.get("timestamp"),
                "likes": p.get("like_count", 0),
                "comments": p.get("comments_count", 0),
            }
            for p in items
        ],
    }


def _write_summary_audit(platform: str, data: dict) -> str:
    """Write a Markdown audit file to Audits/ and return the path string."""
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"FB_IG_Summary_{platform.upper()}_{today}.md"
    audit_path = AUDITS_DIR / filename

    rows = "\n".join(
        f"| {p.get('id', 'n/a')} | {p.get('message_preview') or p.get('caption_preview', 'n/a')} "
        f"| {p.get('created_time') or p.get('timestamp', 'n/a')} "
        f"| {p.get('likes', 0)} | {p.get('comments', 0)} |"
        for p in data.get("posts", [])
    )

    source_note = "(Playwright fallback — Graph API quota exceeded)" if data.get("source") == "playwright_fallback" else "(Graph API)"

    md = f"""---
type: social_audit
platform: {platform}
created: {datetime.now().strftime('%Y-%m-%d %H:%M')}
period: daily
tags: ["#audit", "#{platform}", "#social-analytics"]
---

# {platform.upper()} Engagement Summary — {today}

> Source: {source_note}

## Overview Metrics

| Metric | Value |
|--------|-------|
| Posts Analyzed | {data.get('posts_analyzed', 'N/A')} |
| Total Likes | {data.get('total_likes', 'N/A')} |
| Total Comments | {data.get('total_comments', 'N/A')} |
| Avg Engagement / Post | {data.get('avg_engagement_per_post', 'N/A')} |
| Platform | {platform.upper()} |
| Report Date | {today} |

## Post-Level Breakdown

| Post ID | Preview | Date | Likes | Comments |
|---------|---------|------|-------|----------|
{rows if rows else "| — | No posts returned | — | — | — |"}

## Engagement Rate Formula

```
Avg Engagement / Post = (Total Likes + Total Comments) / Posts Analyzed
```

---

_Generated by fb_ig_mcp.py — Gold Tier Personal AI Employee_
"""

    audit_path.write_text(md, encoding="utf-8")
    return str(audit_path)


# ---------------------------------------------------------------------------
# MCP Tool Handlers
# ---------------------------------------------------------------------------

def handle_draft_fb_post(args: dict) -> str:
    result = _write_draft(
        platform="fb",
        content=args.get("content", ""),
        caption=args.get("caption", ""),
        hashtags=args.get("hashtags", ""),
        image_path=args.get("image_path", ""),
        notes=args.get("notes", ""),
        source_item=args.get("source_item", ""),
    )
    return result["message"]


def handle_draft_ig_post(args: dict) -> str:
    result = _write_draft(
        platform="ig",
        content=args.get("content", ""),
        caption=args.get("caption", ""),
        hashtags=args.get("hashtags", ""),
        image_path=args.get("image_path", ""),
        notes=args.get("notes", ""),
        source_item=args.get("source_item", ""),
    )
    return result["message"]


def handle_post_fb(args: dict) -> str:
    draft_filename = args.get("draft_filename", "")
    if not draft_filename:
        return "Error: draft_filename is required."

    # HITL guard
    if not _check_approval(draft_filename):
        msg = (
            f"BLOCKED: No approval found for '{draft_filename}'.\n"
            f"Move the corresponding file from Pending_Approval/ to Approved/ first."
        )
        log_social({"action": "post_fb", "status": "blocked_no_approval", "draft": draft_filename})
        return msg

    # Daily post limit
    if _posts_today() >= MAX_POSTS_PER_DAY:
        msg = f"BLOCKED: Daily post limit ({MAX_POSTS_PER_DAY}) reached. Try again tomorrow."
        log_social({"action": "post_fb", "status": "blocked_daily_limit"})
        return msg

    if DRY_RUN:
        log_social({"action": "post_fb", "status": "dry_run", "draft": draft_filename})
        return f"[DRY RUN] Would post '{draft_filename}' to Facebook. Logged."

    if not FB_SDK_AVAILABLE or not FB_PAGE_TOKEN:
        return "Error: facebook-sdk not installed or FB_PAGE_ACCESS_TOKEN not set."

    # Read message from draft file
    draft_path = PLANS_DIR / draft_filename
    message = draft_path.read_text(encoding="utf-8") if draft_path.exists() else args.get("message", "")

    def _do_post():
        graph = facebook.GraphAPI(access_token=FB_PAGE_TOKEN, version="17.0")
        return graph.put_object(parent_object=FB_PAGE_ID, connection_name="feed", message=message[:63206])

    try:
        result = with_retry(_do_post, "post_fb")
        log_social({"action": "post_fb", "status": "posted", "post_id": result.get("id"), "draft": draft_filename})
        return f"Posted to Facebook. Post ID: {result.get('id')}"
    except RuntimeError as exc:
        log_social({"action": "post_fb", "status": "failed", "error": str(exc)})
        return f"Failed to post to Facebook: {exc}"


def handle_post_ig(args: dict) -> str:
    draft_filename = args.get("draft_filename", "")
    if not draft_filename:
        return "Error: draft_filename is required."

    if not _check_approval(draft_filename):
        msg = (
            f"BLOCKED: No approval found for '{draft_filename}'.\n"
            f"Move the corresponding file from Pending_Approval/ to Approved/ first."
        )
        log_social({"action": "post_ig", "status": "blocked_no_approval", "draft": draft_filename})
        return msg

    if _posts_today() >= MAX_POSTS_PER_DAY:
        log_social({"action": "post_ig", "status": "blocked_daily_limit"})
        return f"BLOCKED: Daily post limit ({MAX_POSTS_PER_DAY}) reached."

    if DRY_RUN:
        log_social({"action": "post_ig", "status": "dry_run", "draft": draft_filename})
        return f"[DRY RUN] Would post '{draft_filename}' to Instagram. Logged."

    if not INSTABOT_AVAILABLE or not IG_USERNAME or not IG_PASSWORD:
        return "Error: instabot not installed or IG_USERNAME/IG_PASSWORD not set."

    draft_path = PLANS_DIR / draft_filename
    caption = draft_path.read_text(encoding="utf-8") if draft_path.exists() else args.get("caption", "")
    image_path = args.get("image_path", "")

    def _do_post():
        bot = InstaBot()
        bot.login(username=IG_USERNAME, password=IG_PASSWORD)
        return bot.upload_photo(image_path, caption=caption[:2200])

    try:
        with_retry(_do_post, "post_ig")
        log_social({"action": "post_ig", "status": "posted", "draft": draft_filename})
        return "Posted to Instagram."
    except RuntimeError as exc:
        log_social({"action": "post_ig", "status": "failed", "error": str(exc)})
        return f"Failed to post to Instagram: {exc}"


def handle_fetch_fb_summary(args: dict) -> str:
    if DRY_RUN:
        # Return mock data in dry-run
        data = {
            "platform": "facebook",
            "posts_analyzed": 3,
            "total_likes": 142,
            "total_comments": 18,
            "avg_engagement_per_post": 53.3,
            "posts": [
                {"id": "mock_001", "message_preview": "Excited to announce our new enterprise plan...", "created_time": "2026-02-22T10:00:00+0000", "likes": 75, "comments": 8},
                {"id": "mock_002", "message_preview": "Q4 results are in! Record growth quarter...", "created_time": "2026-02-21T14:30:00+0000", "likes": 42, "comments": 6},
                {"id": "mock_003", "message_preview": "Join us for a free demo this Friday...", "created_time": "2026-02-20T09:15:00+0000", "likes": 25, "comments": 4},
            ],
        }
        audit_path = _write_summary_audit("fb", data)
        log_social({"action": "fetch_fb_summary", "status": "dry_run_mock", "audit_file": audit_path})
        return f"[DRY RUN] Mock FB summary generated.\nAudit written to: {audit_path}"

    try:
        data = with_retry(_fetch_fb_summary_api, "fetch_fb_summary")
    except (RuntimeError, ImportError, ValueError) as exc:
        logger.warning("FB API failed, switching to Playwright fallback: %s", exc)
        data = _playwright_fetch_summary("fb")

    audit_path = _write_summary_audit("fb", data)
    log_social({"action": "fetch_fb_summary", "status": "success", "audit_file": audit_path})
    return f"Facebook summary fetched.\nAudit written to: {audit_path}\nPosts analyzed: {data.get('posts_analyzed', 'N/A')}"


def handle_fetch_ig_summary(args: dict) -> str:
    if DRY_RUN:
        data = {
            "platform": "instagram",
            "posts_analyzed": 3,
            "total_likes": 310,
            "total_comments": 27,
            "avg_engagement_per_post": 112.3,
            "posts": [
                {"id": "ig_mock_001", "caption_preview": "New product launch — swipe to see features...", "timestamp": "2026-02-22T11:00:00+0000", "likes": 180, "comments": 15},
                {"id": "ig_mock_002", "caption_preview": "Behind the scenes at HQ this week...", "timestamp": "2026-02-21T16:00:00+0000", "likes": 95, "comments": 8},
                {"id": "ig_mock_003", "caption_preview": "Client spotlight: how Ahmed scaled 3x...", "timestamp": "2026-02-20T13:00:00+0000", "likes": 35, "comments": 4},
            ],
        }
        audit_path = _write_summary_audit("ig", data)
        log_social({"action": "fetch_ig_summary", "status": "dry_run_mock", "audit_file": audit_path})
        return f"[DRY RUN] Mock IG summary generated.\nAudit written to: {audit_path}"

    try:
        data = with_retry(_fetch_ig_summary_api, "fetch_ig_summary")
    except (RuntimeError, ImportError, ValueError) as exc:
        logger.warning("IG API failed, switching to Playwright fallback: %s", exc)
        data = _playwright_fetch_summary("ig")

    audit_path = _write_summary_audit("ig", data)
    log_social({"action": "fetch_ig_summary", "status": "success", "audit_file": audit_path})
    return f"Instagram summary fetched.\nAudit written to: {audit_path}\nPosts analyzed: {data.get('posts_analyzed', 'N/A')}"


# ---------------------------------------------------------------------------
# MCP Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "draft_fb_post",
        "description": (
            "Create a Facebook post draft as a Markdown file in Plans/. "
            "Also creates an approval request in Pending_Approval/. "
            "Does NOT publish anything. Human must approve before posting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content":      {"type": "string", "description": "Full post text"},
                "caption":      {"type": "string", "description": "Short caption/headline"},
                "hashtags":     {"type": "string", "description": "Hashtags (optional)"},
                "image_path":   {"type": "string", "description": "Path to image file relative to vault (optional)"},
                "notes":        {"type": "string", "description": "Internal notes (optional)"},
                "source_item":  {"type": "string", "description": "Vault file that triggered this draft (optional)"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "draft_ig_post",
        "description": (
            "Create an Instagram post draft as a Markdown file in Plans/. "
            "Also creates an approval request in Pending_Approval/. "
            "Does NOT publish anything."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content":      {"type": "string", "description": "Full post caption text"},
                "caption":      {"type": "string", "description": "Short headline"},
                "hashtags":     {"type": "string", "description": "Hashtags (optional)"},
                "image_path":   {"type": "string", "description": "Path to image file (required for real posting)"},
                "notes":        {"type": "string", "description": "Internal notes (optional)"},
                "source_item":  {"type": "string", "description": "Vault file that triggered this draft (optional)"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "post_fb",
        "description": (
            "Publish an approved draft to Facebook. "
            "REQUIRES: approval file must exist in Approved/ for this draft. "
            "Enforces max 5 posts/day. Retries 3x with 30s backoff on API error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_filename": {"type": "string", "description": "Filename of the draft in Plans/ (e.g. FB_IG_DRAFT_FB_20260223_091500.md)"},
            },
            "required": ["draft_filename"],
        },
    },
    {
        "name": "post_ig",
        "description": (
            "Publish an approved draft to Instagram. "
            "REQUIRES: approval file must exist in Approved/ for this draft. "
            "Enforces max 5 posts/day. Retries 3x with 30s backoff."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_filename": {"type": "string", "description": "Filename of the draft in Plans/"},
                "image_path":     {"type": "string", "description": "Path to image file (required for Instagram)"},
            },
            "required": ["draft_filename"],
        },
    },
    {
        "name": "fetch_fb_summary",
        "description": (
            "Fetch recent Facebook page posts, likes, and comments via Graph API. "
            "Analyzes engagement metrics and writes an audit report to Audits/. "
            "Falls back to Playwright if API quota exceeded."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to look back (optional, default 7)", "default": 7},
            },
            "required": [],
        },
    },
    {
        "name": "fetch_ig_summary",
        "description": (
            "Fetch recent Instagram posts, likes, and comments via Graph API. "
            "Analyzes engagement metrics and writes an audit report to Audits/. "
            "Falls back to Playwright if API quota exceeded."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to look back (optional, default 7)", "default": 7},
            },
            "required": [],
        },
    },
]

HANDLERS = {
    "draft_fb_post":    handle_draft_fb_post,
    "draft_ig_post":    handle_draft_ig_post,
    "post_fb":          handle_post_fb,
    "post_ig":          handle_post_ig,
    "fetch_fb_summary": handle_fetch_fb_summary,
    "fetch_ig_summary": handle_fetch_ig_summary,
}

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

async def run_mcp_server():
    if not MCP_AVAILABLE:
        print("[fb_ig_mcp] Cannot start MCP server: mcp package not installed.", file=sys.stderr)
        sys.exit(1)

    server = Server("fb-ig-mcp")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        handler = HANDLERS.get(name)
        if not handler:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = handler(arguments or {})
            return [TextContent(type="text", text=result)]
        except Exception as exc:
            log_social({"action": name, "status": "error", "error": str(exc)})
            return [TextContent(type="text", text=f"Error in {name}: {exc}")]

    async with stdio_server() as (read_stream, write_stream):
        logger.info("FB/IG MCP Server v1.0 started (DRY_RUN=%s)", DRY_RUN)
        await server.run(read_stream, write_stream, server.create_initialization_options())


# ---------------------------------------------------------------------------
# CLI / standalone test
# ---------------------------------------------------------------------------

def _run_standalone_test():
    """Quick smoke-test without MCP — exercises all handlers in DRY_RUN mode."""
    print("[fb_ig_mcp] Running standalone DRY_RUN test...\n")

    print("--- draft_fb_post ---")
    print(handle_draft_fb_post({
        "content": "Excited to announce our new Enterprise Plan is now live! 50-seat licenses available. DM for pricing.",
        "caption": "Enterprise Plan Launch",
        "hashtags": "#SaaS #Enterprise #Launch #B2B",
        "notes": "Triggered by WhatsApp pricing queries from Sarah + David",
        "source_item": "WHATSAPP_whatsapp_demo_002.md",
    }))

    print("\n--- draft_ig_post ---")
    print(handle_draft_ig_post({
        "content": "New enterprise plan just dropped 🚀 50 seats, full support, custom onboarding. Link in bio.",
        "caption": "Enterprise Plan Live",
        "hashtags": "#startup #saas #enterprise #productlaunch",
        "notes": "IG companion post to FB draft",
        "source_item": "WHATSAPP_whatsapp_demo_002.md",
    }))

    print("\n--- fetch_fb_summary (mock) ---")
    print(handle_fetch_fb_summary({}))

    print("\n--- fetch_ig_summary (mock) ---")
    print(handle_fetch_ig_summary({}))

    print("\n--- post_fb (no approval → expect BLOCKED) ---")
    print(handle_post_fb({"draft_filename": "nonexistent_draft.md"}))

    print("\n[fb_ig_mcp] Standalone test complete. Check Plans/, Audits/, Pending_Approval/, Logs/")


if __name__ == "__main__":
    if "--test" in sys.argv or not MCP_AVAILABLE:
        _run_standalone_test()
    else:
        import asyncio
        asyncio.run(run_mcp_server())
