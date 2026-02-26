#!/usr/bin/env python3
"""
x_mcp.py — X (Twitter) MCP Server for Personal AI Employee (Gold Tier)

Provides MCP tools to Claude Code for X/Twitter integration:
  - draft_x_post      : Save a tweet draft to Plans/ + approval request (no publish)
  - post_x            : Publish approved draft as a tweet (HITL required)
  - reply_x           : Reply to a tweet (HITL required)
  - fetch_x_summary   : Fetch timeline/mentions, compute metrics → Audits/

Resilience:
  - Rate limits    : 429 detected → auto-sleep 15 min + retry, up to 3 cycles
  - Token expiry   : OAuth 2.0 access token refresh via refresh_token grant
  - Pagination     : tweepy.Paginator cursor loop for >100 tweets
  - DRY_RUN mode   : All actions logged only, no real API calls

Authentication:
  - OAuth 1.0a (user context) for posting as the authenticated account
  - OAuth 2.0 Bearer Token for read-only timeline/search calls
  - Credentials loaded from .env — NEVER hardcoded

Security:
  - post_x / reply_x verify approval file in Approved/ before executing
  - Max 10 posts/day enforced (X Handbook rule)
  - All events logged to Logs/social_x_{date}.json

Setup:
  1. pip install tweepy python-dotenv pyyaml mcp
  2. Create a project + app at developer.twitter.com
  3. Enable OAuth 1.0a + OAuth 2.0; copy keys to .env
  4. Add "x" entry to .claude/mcp.json (already done by SKILL_X_Integration)

Usage (standalone test):
  DRY_RUN=true python x_mcp.py --test
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# MCP SDK
# ---------------------------------------------------------------------------
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

# ---------------------------------------------------------------------------
# Tweepy (graceful fallback to DRY_RUN if absent)
# ---------------------------------------------------------------------------
try:
    import tweepy
    TWEEPY_AVAILABLE = True
except ImportError:
    TWEEPY_AVAILABLE = False

# ---------------------------------------------------------------------------
# Environment & vault paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

VAULT_PATH   = Path(os.environ.get("VAULT_PATH", SCRIPT_DIR))
PLANS_DIR    = VAULT_PATH / "Plans"
AUDITS_DIR   = VAULT_PATH / "Audits"
LOGS_DIR     = VAULT_PATH / "Logs"
PENDING_DIR  = VAULT_PATH / "Pending_Approval"
APPROVED_DIR = VAULT_PATH / "Approved"

for _d in (PLANS_DIR, AUDITS_DIR, LOGS_DIR, PENDING_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DRY_RUN            = os.environ.get("DRY_RUN", "true").lower() == "true"
MAX_POSTS_PER_DAY  = 10          # Handbook rule
RATE_LIMIT_SLEEP   = 15 * 60     # 15 minutes in seconds
MAX_RL_RETRIES     = 3           # rate-limit retry cycles
TOKEN_REFRESH_GRACE = 120        # refresh token this many seconds before expiry

# Twitter / X credentials
X_API_KEY             = os.environ.get("X_API_KEY", "")
X_API_SECRET          = os.environ.get("X_API_SECRET", "")
X_ACCESS_TOKEN        = os.environ.get("X_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET", "")
X_BEARER_TOKEN        = os.environ.get("X_BEARER_TOKEN", "")
X_CLIENT_ID           = os.environ.get("X_CLIENT_ID", "")
X_CLIENT_SECRET       = os.environ.get("X_CLIENT_SECRET", "")
# OAuth 2.0 refresh token (stored/updated at runtime)
_X_REFRESH_TOKEN_PATH = VAULT_PATH / ".x_refresh_token"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [x_mcp] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("x_mcp")

# Unified structured audit logger
sys.path.insert(0, str(SCRIPT_DIR / "watchers"))
try:
    from audit_logger import audit_log as _audit_log  # noqa: E402
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False


def log_x(entry: dict) -> None:
    """Append an event to Logs/social_x_{date}.json AND to the unified audit log."""
    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"social_x_{today}.json"
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
            actor="x_mcp",
            action=entry.get("action", "x_event"),
            params={k: v for k, v in entry.items()
                    if k not in ("timestamp", "action", "status")},
            result=entry.get("status", "unknown"),
            approval_status=("approved" if entry.get("approval_file") else "not_required"),
            severity=("ERROR" if entry.get("status") in ("failed", "error") else "INFO"),
            source_file="x_mcp.py",
            error=entry.get("error"),
        )


# ---------------------------------------------------------------------------
# Daily post counter
# ---------------------------------------------------------------------------

def _posts_today() -> int:
    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"social_x_{today}.json"
    if not log_path.exists():
        return 0
    try:
        entries = json.loads(log_path.read_text(encoding="utf-8"))
        return sum(
            1 for e in entries
            if e.get("action") in ("post_x", "reply_x") and e.get("status") == "posted"
        )
    except (json.JSONDecodeError, OSError):
        return 0


# ---------------------------------------------------------------------------
# OAuth 2.0 token refresh
# ---------------------------------------------------------------------------

def _load_refresh_token() -> str:
    """Load persisted OAuth2 refresh token from .x_refresh_token file."""
    if _X_REFRESH_TOKEN_PATH.exists():
        return _X_REFRESH_TOKEN_PATH.read_text(encoding="utf-8").strip()
    return os.environ.get("X_OAUTH2_REFRESH_TOKEN", "")


def _save_refresh_token(token: str) -> None:
    """Persist updated refresh token to disk."""
    _X_REFRESH_TOKEN_PATH.write_text(token, encoding="utf-8")
    logger.info("OAuth2 refresh token updated on disk.")


def _refresh_oauth2_token() -> tuple[str, int]:
    """
    Exchange refresh token for a new access token using OAuth 2.0 PKCE flow.
    Returns (new_access_token, expires_in_seconds).
    Raises RuntimeError if refresh fails.
    """
    import urllib.request
    import urllib.parse
    import base64

    refresh_token = _load_refresh_token()
    if not refresh_token:
        raise RuntimeError("No OAuth2 refresh token available. Re-authorise the app.")

    credentials  = base64.b64encode(f"{X_CLIENT_ID}:{X_CLIENT_SECRET}".encode()).decode()
    payload      = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }).encode()

    req = urllib.request.Request(
        "https://api.twitter.com/2/oauth2/token",
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"Token refresh HTTP error: {exc}") from exc

    if "error" in data:
        raise RuntimeError(f"Token refresh failed: {data.get('error_description', data['error'])}")

    new_access  = data["access_token"]
    new_refresh = data.get("refresh_token", refresh_token)  # rotation — save if provided
    expires_in  = data.get("expires_in", 7200)

    _save_refresh_token(new_refresh)
    log_x({"action": "oauth2_token_refresh", "status": "success", "expires_in": expires_in})
    logger.info("OAuth2 token refreshed (expires in %ds).", expires_in)
    return new_access, expires_in


# ---------------------------------------------------------------------------
# Tweepy client factory with token-expiry awareness
# ---------------------------------------------------------------------------

# Module-level cache: (client_v2, expiry_epoch)
_client_cache: tuple | None = None


def _get_client_v2() -> "tweepy.Client":
    """
    Return a tweepy.Client for Twitter API v2 (user-context OAuth2).
    Refreshes the access token if it's within TOKEN_REFRESH_GRACE seconds of expiry.
    """
    global _client_cache

    if not TWEEPY_AVAILABLE:
        raise ImportError("tweepy not installed. Run: pip install tweepy")

    now = time.time()
    if _client_cache:
        client, expiry = _client_cache
        if now < expiry - TOKEN_REFRESH_GRACE:
            return client  # still fresh

        logger.info("Access token near/past expiry — refreshing.")
        try:
            new_token, expires_in = _refresh_oauth2_token()
            client = tweepy.Client(
                bearer_token=X_BEARER_TOKEN,
                consumer_key=X_API_KEY,
                consumer_secret=X_API_SECRET,
                access_token=new_token,
                access_token_secret=X_ACCESS_TOKEN_SECRET,
                wait_on_rate_limit=False,  # we handle rate limits ourselves
            )
            _client_cache = (client, now + expires_in)
            return client
        except RuntimeError as exc:
            logger.warning("Token refresh failed (%s); re-using old client.", exc)
            return _client_cache[0]

    # First call — use env token (OAuth1.0a for posting, bearer for reads)
    client = tweepy.Client(
        bearer_token=X_BEARER_TOKEN,
        consumer_key=X_API_KEY,
        consumer_secret=X_API_SECRET,
        access_token=X_ACCESS_TOKEN,
        access_token_secret=X_ACCESS_TOKEN_SECRET,
        wait_on_rate_limit=False,
    )
    # Assume env token is fresh; set expiry to 2 hours from now as a baseline
    _client_cache = (client, now + 7200)
    return client


# ---------------------------------------------------------------------------
# Rate-limit aware call wrapper
# ---------------------------------------------------------------------------

def call_with_rate_limit_handling(fn, label: str):
    """
    Call fn(). On tweepy.errors.TooManyRequests (429):
      - Log the rate limit hit
      - Sleep RATE_LIMIT_SLEEP seconds (15 min)
      - Retry up to MAX_RL_RETRIES times
    On tweepy.errors.Unauthorized (401):
      - Attempt token refresh once, then retry
    """
    last_exc = None
    for attempt in range(1, MAX_RL_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            exc_name = type(exc).__name__

            # Rate limit
            if TWEEPY_AVAILABLE and isinstance(exc, tweepy.errors.TooManyRequests):
                log_x({
                    "action": label, "status": "rate_limited",
                    "attempt": attempt, "sleep_seconds": RATE_LIMIT_SLEEP,
                })
                logger.warning(
                    "[%s] Rate limited (429). Sleeping %ds (attempt %d/%d)...",
                    label, RATE_LIMIT_SLEEP, attempt, MAX_RL_RETRIES,
                )
                if attempt < MAX_RL_RETRIES:
                    time.sleep(RATE_LIMIT_SLEEP)
                continue

            # Unauthorized — try token refresh once
            if TWEEPY_AVAILABLE and isinstance(exc, tweepy.errors.Unauthorized) and attempt == 1:
                log_x({"action": label, "status": "unauthorized_refresh_attempt"})
                logger.warning("[%s] Unauthorized (401). Attempting token refresh.", label)
                try:
                    global _client_cache
                    _client_cache = None  # force re-init with fresh token
                    _refresh_oauth2_token()
                    continue  # retry with refreshed token
                except RuntimeError as refresh_exc:
                    logger.error("Token refresh failed: %s", refresh_exc)
                    raise RuntimeError(
                        f"{label} failed: 401 Unauthorized and token refresh failed: {refresh_exc}"
                    ) from exc

            # Other error — do not retry
            raise

    raise RuntimeError(
        f"{label} failed after {MAX_RL_RETRIES} rate-limit cycles: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# HITL approval guard
# ---------------------------------------------------------------------------

def _check_approval(draft_filename: str) -> bool:
    if not APPROVED_DIR.exists():
        return False
    if (APPROVED_DIR / f"APPROVAL_{draft_filename}").exists():
        return True
    for f in APPROVED_DIR.glob("*.md"):
        try:
            if draft_filename in f.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# Draft writer
# ---------------------------------------------------------------------------

def _write_draft(
    text: str,
    thread: list[str] | None = None,
    hashtags: str = "",
    media_path: str = "",
    reply_to_id: str = "",
    notes: str = "",
    source_item: str = "",
) -> dict:
    ts               = datetime.now().strftime("%Y%m%d_%H%M%S")
    draft_filename   = f"X_DRAFT_{ts}.md"
    approval_filename = f"APPROVAL_{draft_filename}"
    draft_path       = PLANS_DIR / draft_filename
    approval_path    = PENDING_DIR / approval_filename

    char_count = len(text)
    thread_section = ""
    if thread:
        thread_lines = "\n".join(f"{i+2}. {t}" for i, t in enumerate(thread))
        thread_section = f"\n## Thread Continuation\n\n{thread_lines}\n"

    draft_md = f"""---
type: x_post_draft
status: draft
created: {datetime.now().strftime('%Y-%m-%d %H:%M')}
platform: x_twitter
char_count: {char_count}
reply_to_id: "{reply_to_id}"
requires_approval: yes
approval_file: Pending_Approval/{approval_filename}
source_item: "{source_item}"
tags: ["#x-draft", "#HITL", "#social", "#twitter"]
---

# X (Twitter) Post Draft — {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Tweet Text ({char_count}/280 chars)

> {text}

## Hashtags

{hashtags if hashtags else "_none specified_"}

## Media Path

{media_path if media_path else "_no media attached_"}

## Reply To Tweet ID

{reply_to_id if reply_to_id else "_standalone tweet_"}
{thread_section}
## Notes

{notes if notes else "_none_"}

---

## Approval Checklist

- [ ] Review tweet text and tone
- [ ] Verify hashtags are relevant and not spammy
- [ ] Confirm character count ≤ 280 (current: {char_count})
- [ ] Check media path if applicable
- [ ] Approve: move `Pending_Approval/{approval_filename}` → `Approved/`
- [ ] Reject: move to `Rejected/`

**WARNING: Tweet will NOT be posted until the approval file is in Approved/.**
"""

    approval_md = f"""---
type: hitl_approval_request
action: post_x
status: pending
created: {datetime.now().strftime('%Y-%m-%d %H:%M')}
draft_file: Plans/{draft_filename}
platform: x_twitter
priority: medium
tags: ["#HITL", "#approval", "#x-twitter"]
---

# Approval Request: X (Twitter) Post

**Action:** Publish tweet
**Draft:** `Plans/{draft_filename}`
**Created:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Characters:** {char_count}/280

## Tweet Preview

> {text[:280]}

## To Approve

Move this file to `Approved/` folder.

## To Reject

Move this file to `Rejected/` folder.

**Agent will NOT tweet without this file in Approved/.**
"""

    draft_path.write_text(draft_md, encoding="utf-8")
    approval_path.write_text(approval_md, encoding="utf-8")

    log_x({
        "action": "draft_x_post",
        "status": "created",
        "draft_file": str(draft_path),
        "approval_file": str(approval_path),
        "char_count": char_count,
        "dry_run": DRY_RUN,
    })
    logger.info("X draft created: %s", draft_path.name)

    return {
        "draft_file": f"Plans/{draft_filename}",
        "approval_file": f"Pending_Approval/{approval_filename}",
        "char_count": char_count,
        "message": (
            f"X post draft saved to Plans/{draft_filename}\n"
            f"Approval request created at Pending_Approval/{approval_filename}\n"
            f"Characters: {char_count}/280\n"
            f"Move the approval file to Approved/ to authorize posting."
        ),
    }


# ---------------------------------------------------------------------------
# Summary helpers — pagination + metrics
# ---------------------------------------------------------------------------

def _paginate_timeline(client: "tweepy.Client", user_id: str, max_results: int = 100) -> list[dict]:
    """
    Fetch up to max_results recent tweets using tweepy.Paginator cursor loop.
    Handles pagination transparently. Returns list of tweet dicts.
    """
    tweets: list[dict] = []
    tweet_fields = ["public_metrics", "created_at", "text", "referenced_tweets"]

    paginator = tweepy.Paginator(
        client.get_users_tweets,
        id=user_id,
        tweet_fields=tweet_fields,
        max_results=min(max_results, 100),  # API max per page = 100
        limit=max(1, max_results // 100),   # number of pages
    )

    for page in paginator:
        if page.data:
            for tweet in page.data:
                metrics = tweet.public_metrics or {}
                tweets.append({
                    "id":           str(tweet.id),
                    "text":         tweet.text,
                    "created_at":   str(tweet.created_at) if tweet.created_at else "",
                    "likes":        metrics.get("like_count", 0),
                    "retweets":     metrics.get("retweet_count", 0),
                    "replies":      metrics.get("reply_count", 0),
                    "impressions":  metrics.get("impression_count", 0),
                    "is_reply":     bool(tweet.referenced_tweets),
                })
        if len(tweets) >= max_results:
            break

    return tweets[:max_results]


def _compute_trends(tweets: list[dict]) -> dict:
    """Compute aggregate engagement trends from a list of tweet dicts."""
    if not tweets:
        return {}

    n              = len(tweets)
    total_likes    = sum(t["likes"] for t in tweets)
    total_rts      = sum(t["retweets"] for t in tweets)
    total_replies  = sum(t["replies"] for t in tweets)
    total_impr     = sum(t["impressions"] for t in tweets)

    top_tweet = max(tweets, key=lambda t: t["likes"] + t["retweets"])
    engagement_rate = round((total_likes + total_rts + total_replies) / max(total_impr, 1) * 100, 2)

    return {
        "tweets_analyzed":        n,
        "total_likes":            total_likes,
        "total_retweets":         total_rts,
        "total_replies":          total_replies,
        "total_impressions":      total_impr,
        "avg_likes_per_tweet":    round(total_likes / n, 1),
        "avg_retweets_per_tweet": round(total_rts / n, 1),
        "engagement_rate_pct":    engagement_rate,
        "top_tweet_id":           top_tweet["id"],
        "top_tweet_preview":      top_tweet["text"][:100],
        "top_tweet_likes":        top_tweet["likes"],
        "top_tweet_rts":          top_tweet["retweets"],
    }


def _write_summary_audit(trends: dict, tweets: list[dict], source: str = "api") -> str:
    """Write Audits/X_Summary_{date}.md and return the path string."""
    today      = datetime.now().strftime("%Y-%m-%d")
    audit_path = AUDITS_DIR / f"X_Summary_{today}.md"
    source_note = "(API)" if source == "api" else "(mock / DRY_RUN)"

    tweet_rows = "\n".join(
        f"| {i+1} | {t['text'][:60].replace('|', '')}… | {t['created_at'][:10]} "
        f"| {t['likes']} | {t['retweets']} | {t['replies']} | {t['impressions']} |"
        for i, t in enumerate(tweets[:10])
    )

    md = f"""---
type: x_audit
platform: x_twitter
created: {datetime.now().strftime('%Y-%m-%d %H:%M')}
period: daily
source: {source}
tags: ["#audit", "#x-twitter", "#social-analytics"]
---

# X (Twitter) Engagement Summary — {today}

> Source: {source_note}

## Overview Metrics

| Metric | Value |
|--------|-------|
| Tweets Analyzed | {trends.get('tweets_analyzed', 'N/A')} |
| Total Likes | {trends.get('total_likes', 'N/A')} |
| Total Retweets | {trends.get('total_retweets', 'N/A')} |
| Total Replies | {trends.get('total_replies', 'N/A')} |
| Total Impressions | {trends.get('total_impressions', 'N/A')} |
| Avg Likes / Tweet | {trends.get('avg_likes_per_tweet', 'N/A')} |
| Avg Retweets / Tweet | {trends.get('avg_retweets_per_tweet', 'N/A')} |
| Engagement Rate | {trends.get('engagement_rate_pct', 'N/A')}% |
| Report Date | {today} |

## Top Performing Tweet

> "{trends.get('top_tweet_preview', 'N/A')}"

- Likes: {trends.get('top_tweet_likes', 'N/A')}
- Retweets: {trends.get('top_tweet_rts', 'N/A')}
- Tweet ID: {trends.get('top_tweet_id', 'N/A')}

## Engagement Trends

1. **Likes trend:** {_trend_label(trends.get('avg_likes_per_tweet', 0), 10)}
2. **Retweet trend:** {_trend_label(trends.get('avg_retweets_per_tweet', 0), 5)}
3. **Engagement rate:** {_engagement_label(trends.get('engagement_rate_pct', 0))}
4. **Top content type:** {"Replies" if sum(1 for t in tweets if t.get('is_reply')) > len(tweets) // 2 else "Original tweets"}

## Recent Tweets Breakdown

| # | Preview | Date | Likes | RTs | Replies | Impressions |
|---|---------|------|-------|-----|---------|-------------|
{tweet_rows if tweet_rows else "| — | No tweets in period | — | — | — | — | — |"}

## Engagement Rate Formula

```
Engagement Rate = (Likes + Retweets + Replies) / Impressions × 100
```

---

_Generated by x_mcp.py — Gold Tier Personal AI Employee_
"""

    audit_path.write_text(md, encoding="utf-8")
    return str(audit_path)


def _trend_label(value: float, threshold: float) -> str:
    if value >= threshold * 1.5:
        return f"Strong ({value})"
    if value >= threshold:
        return f"Good ({value})"
    return f"Below target ({value} vs target {threshold})"


def _engagement_label(rate: float) -> str:
    if rate >= 3.0:
        return f"Excellent ({rate}%)"
    if rate >= 1.0:
        return f"Average ({rate}%)"
    return f"Low ({rate}% — consider content mix review)"


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_draft_x_post(args: dict) -> str:
    text = args.get("text", "").strip()
    if not text:
        return "Error: 'text' is required."
    if len(text) > 280:
        return f"Error: Tweet exceeds 280 characters ({len(text)}). Please shorten."

    result = _write_draft(
        text=text,
        thread=args.get("thread"),
        hashtags=args.get("hashtags", ""),
        media_path=args.get("media_path", ""),
        reply_to_id=args.get("reply_to_id", ""),
        notes=args.get("notes", ""),
        source_item=args.get("source_item", ""),
    )
    return result["message"]


def handle_post_x(args: dict) -> str:
    draft_filename = args.get("draft_filename", "")
    if not draft_filename:
        return "Error: 'draft_filename' is required."

    # HITL guard
    if not _check_approval(draft_filename):
        msg = (
            f"BLOCKED: No approval found for '{draft_filename}'.\n"
            f"Move the APPROVAL file from Pending_Approval/ to Approved/ first."
        )
        log_x({"action": "post_x", "status": "blocked_no_approval", "draft": draft_filename})
        return msg

    # Daily limit
    if _posts_today() >= MAX_POSTS_PER_DAY:
        log_x({"action": "post_x", "status": "blocked_daily_limit"})
        return f"BLOCKED: Daily post limit ({MAX_POSTS_PER_DAY}) reached. Try again tomorrow."

    if DRY_RUN:
        log_x({"action": "post_x", "status": "dry_run", "draft": draft_filename})
        return f"[DRY RUN] Would post '{draft_filename}' to X/Twitter. Logged."

    if not TWEEPY_AVAILABLE:
        return "Error: tweepy not installed. Run: pip install tweepy"
    if not X_API_KEY:
        return "Error: X_API_KEY not set in .env."

    # Read tweet text from draft
    draft_path = PLANS_DIR / draft_filename
    tweet_text = args.get("text", "")
    if not tweet_text and draft_path.exists():
        raw = draft_path.read_text(encoding="utf-8")
        # Extract the quoted tweet text from the draft
        for line in raw.splitlines():
            if line.startswith("> ") and 10 < len(line) < 300:
                tweet_text = line[2:].strip()
                break

    reply_to_id = args.get("reply_to_id") or None

    def _do_tweet():
        client = _get_client_v2()
        return client.create_tweet(
            text=tweet_text[:280],
            in_reply_to_tweet_id=reply_to_id,
        )

    try:
        resp = call_with_rate_limit_handling(_do_tweet, "post_x")
        tweet_id = resp.data["id"] if resp and resp.data else "unknown"
        log_x({"action": "post_x", "status": "posted", "tweet_id": tweet_id, "draft": draft_filename})
        return f"Tweet posted. ID: {tweet_id}\nhttps://x.com/i/web/status/{tweet_id}"
    except RuntimeError as exc:
        log_x({"action": "post_x", "status": "failed", "error": str(exc)})
        return f"Failed to post tweet: {exc}"


def handle_reply_x(args: dict) -> str:
    draft_filename = args.get("draft_filename", "")
    reply_to_id    = args.get("reply_to_id", "")

    if not draft_filename or not reply_to_id:
        return "Error: 'draft_filename' and 'reply_to_id' are required."

    if not _check_approval(draft_filename):
        log_x({"action": "reply_x", "status": "blocked_no_approval", "draft": draft_filename})
        return f"BLOCKED: No approval found for '{draft_filename}'."

    if DRY_RUN:
        log_x({"action": "reply_x", "status": "dry_run", "reply_to": reply_to_id})
        return f"[DRY RUN] Would reply to {reply_to_id} using '{draft_filename}'. Logged."

    # delegate to handle_post_x with reply_to_id injected
    args_with_reply = {**args, "reply_to_id": reply_to_id}
    return handle_post_x(args_with_reply)


def handle_fetch_x_summary(args: dict) -> str:
    max_tweets = int(args.get("max_tweets", 100))
    max_tweets = max(10, min(max_tweets, 500))  # clamp 10–500

    if DRY_RUN:
        mock_tweets = [
            {"id": f"mock_{i:03d}", "text": t, "created_at": f"2026-02-2{i%3+1}T10:00:00Z",
             "likes": l, "retweets": r, "replies": rep, "impressions": impr, "is_reply": False}
            for i, (t, l, r, rep, impr) in enumerate([
                ("Update from AI Employee: New enterprise plan now live. DM for pricing. #SaaS #Enterprise", 47, 12, 8, 1850),
                ("Q4 results are in! Record growth — 3x revenue YoY. Thread below. #B2B #Growth", 89, 34, 15, 4200),
                ("Hot take: the best CRM is the one your team actually uses. What's yours? #Sales", 63, 21, 42, 3100),
                ("New blog post: 5 ways to automate your sales pipeline. Link in bio. #Automation", 28, 9, 3, 980),
                ("Client spotlight: Ahmed scaled from 5 to 50 seats in 6 months. How? Thread 👇 #CaseStudy", 112, 45, 19, 5600),
            ])
        ]
        trends    = _compute_trends(mock_tweets)
        audit_path = _write_summary_audit(trends, mock_tweets, source="dry_run_mock")
        log_x({"action": "fetch_x_summary", "status": "dry_run_mock", "audit_file": audit_path})
        return (
            f"[DRY RUN] Mock X summary generated ({len(mock_tweets)} tweets).\n"
            f"Audit written to: {audit_path}\n"
            f"Engagement rate: {trends.get('engagement_rate_pct')}%  "
            f"Top tweet: {trends.get('top_tweet_likes')} likes"
        )

    if not TWEEPY_AVAILABLE:
        return "Error: tweepy not installed. Run: pip install tweepy"
    if not X_BEARER_TOKEN and not X_API_KEY:
        return "Error: X_BEARER_TOKEN or X_API_KEY not set in .env."

    def _do_fetch():
        client  = _get_client_v2()
        me_resp = client.get_me()
        if not me_resp or not me_resp.data:
            raise RuntimeError("Could not fetch authenticated user ID.")
        user_id = str(me_resp.data.id)
        return _paginate_timeline(client, user_id, max_results=max_tweets)

    try:
        tweets = call_with_rate_limit_handling(_do_fetch, "fetch_x_summary")
    except RuntimeError as exc:
        log_x({"action": "fetch_x_summary", "status": "failed", "error": str(exc)})
        return f"Failed to fetch X timeline: {exc}"

    trends     = _compute_trends(tweets)
    audit_path = _write_summary_audit(trends, tweets, source="api")
    log_x({"action": "fetch_x_summary", "status": "success", "audit_file": audit_path, "tweets": len(tweets)})
    return (
        f"X summary fetched ({len(tweets)} tweets).\n"
        f"Audit written to: {audit_path}\n"
        f"Engagement rate: {trends.get('engagement_rate_pct')}%  "
        f"Top tweet: {trends.get('top_tweet_likes')} likes / {trends.get('top_tweet_rts')} RTs"
    )


# ---------------------------------------------------------------------------
# Tool schema definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "draft_x_post",
        "description": (
            "Create an X (Twitter) post draft in Plans/ and an approval request in Pending_Approval/. "
            "Does NOT post anything. Enforces 280-char limit. Human must approve before posting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text":         {"type": "string",  "description": "Tweet text (max 280 chars)"},
                "thread":       {"type": "array",   "items": {"type": "string"},
                                 "description": "Continuation tweets for a thread (optional)"},
                "hashtags":     {"type": "string",  "description": "Hashtags to include (optional)"},
                "media_path":   {"type": "string",  "description": "Path to media file relative to vault (optional)"},
                "reply_to_id":  {"type": "string",  "description": "Tweet ID to reply to (optional)"},
                "notes":        {"type": "string",  "description": "Internal notes (optional)"},
                "source_item":  {"type": "string",  "description": "Vault filename that triggered this (optional)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "post_x",
        "description": (
            "Publish an approved draft as a tweet. "
            "REQUIRES: approval file in Approved/ for this draft. "
            "Handles rate limits (auto-sleep 15 min, retry 3×) and token expiry (auto-refresh). "
            "Enforces max 10 posts/day."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_filename": {"type": "string", "description": "Filename of draft in Plans/"},
                "reply_to_id":    {"type": "string", "description": "Override reply-to tweet ID (optional)"},
            },
            "required": ["draft_filename"],
        },
    },
    {
        "name": "reply_x",
        "description": (
            "Reply to a specific tweet using an approved draft. "
            "REQUIRES: approval file in Approved/. "
            "Same resilience as post_x (rate limits, token refresh)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_filename": {"type": "string", "description": "Filename of draft in Plans/"},
                "reply_to_id":    {"type": "string", "description": "Tweet ID to reply to"},
            },
            "required": ["draft_filename", "reply_to_id"],
        },
    },
    {
        "name": "fetch_x_summary",
        "description": (
            "Fetch recent tweets from the authenticated account, compute engagement metrics "
            "(likes, retweets, replies, impressions, engagement rate), identify trends, "
            "and write a report to Audits/X_Summary_{date}.md. "
            "Handles rate limits, token expiry, and pagination for >100 tweets automatically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_tweets": {"type": "integer",
                               "description": "Max tweets to analyze (10–500, default 100)",
                               "default": 100},
            },
            "required": [],
        },
    },
]

HANDLERS = {
    "draft_x_post":    handle_draft_x_post,
    "post_x":          handle_post_x,
    "reply_x":         handle_reply_x,
    "fetch_x_summary": handle_fetch_x_summary,
}

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

async def run_mcp_server():
    if not MCP_AVAILABLE:
        print("[x_mcp] Cannot start MCP server: mcp package not installed.", file=sys.stderr)
        sys.exit(1)

    server = Server("x-mcp")

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
            log_x({"action": name, "status": "error", "error": str(exc)})
            return [TextContent(type="text", text=f"Error in {name}: {exc}")]

    async with stdio_server() as (read_stream, write_stream):
        logger.info("X MCP Server v1.0 started (DRY_RUN=%s)", DRY_RUN)
        await server.run(read_stream, write_stream, server.create_initialization_options())


# ---------------------------------------------------------------------------
# CLI / standalone test
# ---------------------------------------------------------------------------

def _run_standalone_test():
    print("[x_mcp] Running standalone DRY_RUN test...\n")

    print("--- draft_x_post: 'Update from AI Employee' ---")
    print(handle_draft_x_post({
        "text": "Update from AI Employee: Our enterprise plan is live. 50-seat licenses, full onboarding, 24/7 support. DM for pricing. #SaaS #AI #Enterprise",
        "hashtags": "#SaaS #AI #Enterprise #ProductLaunch",
        "notes": "Test tweet from Integrate_X_With_Post_Summary skill",
        "source_item": "Skills/SKILL_X_Integration.md",
    }))

    print("\n--- draft_x_post: thread example ---")
    print(handle_draft_x_post({
        "text": "How we automated our entire sales pipeline using AI (thread 🧵)",
        "thread": [
            "1/ The problem: our team was spending 4 hours/day on manual CRM updates.",
            "2/ The fix: Claude Code + WhatsApp watcher + LinkedIn lead detector.",
            "3/ Result: 0 manual CRM entries. Leads auto-qualify. Sales up 40%. /end",
        ],
        "hashtags": "#Sales #Automation #AI #Thread",
        "notes": "Thread draft — educate + generate leads",
    }))

    print("\n--- post_x: blocked (no approval file) ---")
    print(handle_post_x({"draft_filename": "nonexistent_draft.md"}))

    print("\n--- fetch_x_summary (mock) ---")
    print(handle_fetch_x_summary({"max_tweets": 5}))

    print("\n[x_mcp] Test complete. Check Plans/, Pending_Approval/, Audits/, Logs/")


if __name__ == "__main__":
    if "--test" in sys.argv or not MCP_AVAILABLE:
        _run_standalone_test()
    else:
        asyncio.run(run_mcp_server())
