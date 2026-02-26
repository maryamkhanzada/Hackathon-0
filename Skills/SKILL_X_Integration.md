# SKILL: Integrate_X_With_Post_Summary

**Version:** 1.0
**Tier:** Gold
**Last Updated:** 2026-02-23
**Author:** Claude Code (Gold AI Employee)

---

## Trigger

Run this skill when ANY of the following are true:

- User requests a tweet, X post, or Twitter update (any channel)
- A cross-domain item in `/Plans` or `/In_Progress` has `business_trigger` containing `x_post`, `tweet`, or `twitter`
- Scheduled daily at 08:30 — fetch X engagement summary
- Scheduled weekly on Monday — full 7-day analytics report
- Manual: user types `run X integration` or `draft tweet`

---

## Purpose

End-to-end X/Twitter management:
- **Draft**: Create tweet drafts from vault triggers — never post directly
- **Threads**: Support multi-tweet thread drafts in one file
- **HITL**: Route all posts through Pending_Approval/ → Approved/ gate
- **Publish**: Tweet only after human approval, enforce 10 posts/day cap
- **Reply**: Approve and publish replies to specific tweet IDs
- **Summarize**: Fetch timeline metrics, compute engagement trends, write audit reports
- **Resilient**: Handle rate limits (15-min auto-sleep), expired tokens (auto-refresh), large histories (cursor pagination)

---

## MCP Tools

| Tool | Action | HITL Required? |
|------|--------|----------------|
| `draft_x_post` | Save tweet draft to Plans/ + approval request to Pending_Approval/ | No (draft only) |
| `post_x` | Publish approved draft as a tweet | **Yes — Approved/ file required** |
| `reply_x` | Publish approved draft as a reply to a tweet | **Yes — Approved/ file required** |
| `fetch_x_summary` | Fetch timeline metrics → Audits/X_Summary_{date}.md | No |

---

## Full Post Flow

```
Trigger (Needs_Action item, cross-domain plan, or manual)
        │
        ▼
1. draft_x_post
        │  Validates: text ≤ 280 chars
        │  Creates:
        │    Plans/X_DRAFT_{timestamp}.md        ← tweet text, hashtags, thread
        │    Pending_Approval/APPROVAL_X_DRAFT_{timestamp}.md
        │
        ▼
2. Human reviews Plans/X_DRAFT_*.md
        │
        ├── Approve → move APPROVAL file to Approved/
        │
        └── Reject  → move APPROVAL file to Rejected/
                   Agent logs rejection, does NOT tweet
        │
        ▼ (only if approved)
3. post_x  (or reply_x)
        │  Checks: APPROVAL file in Approved/? ✓
        │  Checks: posts today < 10? ✓
        │  Calls:  tweepy.Client.create_tweet()
        │  On 429: sleep 15 min, retry up to 3×
        │  On 401: auto-refresh OAuth2 token, retry once
        │
        ▼
4. Log to Logs/social_x_{date}.json
   Update Dashboard.md
```

---

## Summary / Audit Flow

```
Scheduled or manual trigger
        │
        ▼
fetch_x_summary(max_tweets=100)
        │
        ├── get_me() → resolve authenticated user ID
        │
        ├── Paginator loop (tweepy.Paginator):
        │     page 1 (≤100 tweets)  →  next_token?
        │     page 2 (≤100 tweets)  →  next_token?  ...until max reached
        │
        ├── On 429 per page: sleep 15 min, retry page
        ├── On 401 per page: refresh token, retry page
        │
        ▼
_compute_trends():
  total likes / retweets / replies / impressions
  avg per tweet
  engagement rate = (likes+RTs+replies) / impressions × 100
  top performing tweet
  trend labels (Strong / Good / Below target)
        │
        ▼
Write Audits/X_Summary_{date}.md
  ## Overview Metrics table
  ## Top Performing Tweet
  ## Engagement Trends  (enumerated list)
  ## Recent Tweets Breakdown table
        │
        ▼
Log to Logs/social_x_{date}.json
Update Dashboard.md "## X (Twitter)"
```

---

## Resilience — Rate Limits, Token Expiry, Pagination

### Rate Limit Handling (429)

```python
MAX_RL_RETRIES     = 3
RATE_LIMIT_SLEEP   = 900  # 15 minutes

call_with_rate_limit_handling(fn, label):
    for attempt in 1..3:
        try: return fn()
        except TooManyRequests:
            log_x(rate_limited, attempt, sleep=900)
            if attempt < 3: sleep(900 * attempt)   # 15m, 30m
    raise RuntimeError after 3 cycles
```

X API free tier limits (as of 2026):
- POST /tweets: 17/24h
- GET /users/:id/tweets: 1,500/15min (app auth) / 100/15min (user auth)
- Rate limit window reset: 15 minutes (most endpoints)

### Token Expiry Handling (401)

```python
TOKEN_REFRESH_GRACE = 120  # refresh 2 min before expiry

_get_client_v2():
    if cached_client and now < expiry - 120:
        return cached_client          # still valid
    refresh_oauth2_token():
        POST /2/oauth2/token  grant_type=refresh_token
        save new refresh_token to .x_refresh_token
        update _client_cache with new expiry
```

OAuth 2.0 access tokens expire every **2 hours**. Refresh tokens are rotated on use — the new refresh token is persisted to `.x_refresh_token` (gitignored).

### Pagination (>100 tweets)

```python
tweepy.Paginator(
    client.get_users_tweets,
    id=user_id,
    tweet_fields=["public_metrics", "created_at", ...],
    max_results=100,          # per page
    limit=max_tweets // 100,  # pages to fetch
)
# cursor (next_token) handled automatically by tweepy.Paginator
```

---

## Setup

### 1. Install dependencies

```bash
pip install tweepy python-dotenv mcp
```

### 2. Create X Developer App

1. Go to [developer.twitter.com](https://developer.twitter.com)
2. Create a Project → App
3. In App Settings, enable **OAuth 1.0a** (Read and Write) + **OAuth 2.0** (PKCE)
4. Copy all keys and tokens

### 3. Configure `.env`

```bash
# OAuth 1.0a (for posting tweets as user)
X_API_KEY=your_api_key
X_API_SECRET=your_api_key_secret
X_ACCESS_TOKEN=your_access_token
X_ACCESS_TOKEN_SECRET=your_access_token_secret

# OAuth 2.0 Bearer Token (for read-only timeline/search)
X_BEARER_TOKEN=your_bearer_token

# OAuth 2.0 PKCE (for token refresh)
X_CLIENT_ID=your_oauth2_client_id
X_CLIENT_SECRET=your_oauth2_client_secret
X_OAUTH2_REFRESH_TOKEN=your_initial_refresh_token
```

> **Security:** Never write these values to any `.md` file. The `.env` and `.x_refresh_token` files are in `.gitignore`.

### 4. Obtain OAuth 2.0 initial refresh token

```python
import tweepy

oauth2_user_handler = tweepy.OAuth2UserHandler(
    client_id="YOUR_CLIENT_ID",
    redirect_uri="https://localhost",
    scope=["tweet.read", "tweet.write", "users.read", "offline.access"],
    client_secret="YOUR_CLIENT_SECRET",
)
print(oauth2_user_handler.get_authorization_url())
# Visit URL, authorize, paste callback URL:
access_token = oauth2_user_handler.fetch_token("CALLBACK_URL")
print(access_token["refresh_token"])  # save this to .env
```

The `offline.access` scope is required for refresh tokens.

### 5. MCP registration

Already added to `.claude/mcp.json` as the `"x"` server.
Credentials injected via `${ENV_VAR}` — not hardcoded.

---

## Reusable Prompt Template

```
Run Integrate_X_With_Post_Summary:

Trigger: {describe — e.g. "New enterprise plan live, announce on X"}

Tweet text: {≤280 chars}
Thread: {optional — list continuation tweets}
Hashtags: {#tag1 #tag2}
Reply to: {tweet ID or "none"}
Source item: {vault filename}

Action: draft_only | draft_and_fetch_summary | fetch_summary_only | reply
```

---

## Example

**Trigger:** LinkedIn hot lead John Smith "50 seats, buy enterprise plan"
**Cross-domain link:** LinkedIn lead → announce on X

**Step 1 — Draft tweet:**
```
draft_x_post({
  "text": "Big news: enterprise plans are live for teams of 50+. Full onboarding, dedicated support, custom pricing. DM or link in bio. #Enterprise #SaaS #B2B",
  "hashtags": "#Enterprise #SaaS #B2B",
  "source_item": "LINKEDIN_linkedin_20260219_213636_demo001.md"
})
```
Output: `Plans/X_DRAFT_20260223_091500.md` + `Pending_Approval/APPROVAL_X_DRAFT_20260223_091500.md`

**Step 2 — Human approves:**
Move `Pending_Approval/APPROVAL_X_DRAFT_20260223_091500.md` → `Approved/`

**Step 3 — Post:**
```
post_x({"draft_filename": "X_DRAFT_20260223_091500.md"})
```
Output: `Tweet posted. ID: 1762345678901234567`

**Step 4 — Next day summary:**
```
fetch_x_summary({"max_tweets": 100})
```
Output: `Audits/X_Summary_2026-02-24.md` — 100 tweets, 3.48% engagement rate

---

## Acceptance Criteria

- [ ] `x_mcp.py` starts and lists 4 tools via MCP protocol
- [ ] `draft_x_post` rejects text >280 chars with clear error
- [ ] `draft_x_post` creates file in Plans/ and Pending_Approval/
- [ ] `post_x` is BLOCKED if no approval file in Approved/
- [ ] `post_x` is BLOCKED if daily post count ≥ 10
- [ ] `post_x` auto-sleeps 15 min on 429, retries up to 3×
- [ ] `post_x` auto-refreshes OAuth2 token on 401, retries once
- [ ] `fetch_x_summary` uses tweepy.Paginator for >100 tweets
- [ ] `fetch_x_summary` writes Audits/X_Summary_{date}.md with metrics table + trends list
- [ ] All events logged to `Logs/social_x_{date}.json`
- [ ] No external posts made without approval (HITL gate)
- [ ] `.x_refresh_token` updated on disk after each refresh

---

## Related Skills

- `SKILL_Cross_Integration.md` — Cross-domain trigger classification
- `SKILL_FB_IG.md` — Facebook & Instagram integration
- `SKILL_linkedin_watcher.md` — LinkedIn lead monitoring
- `SKILL_hitl_enforcer.md` — Human-in-the-loop approval gate
- `SKILL_vault_write.md` — Writing plans and notes to vault
