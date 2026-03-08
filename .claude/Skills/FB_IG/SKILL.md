# SKILL: Integrate_FB_IG_With_Post_Summary

**Version:** 1.0
**Tier:** Gold
**Last Updated:** 2026-02-23
**Author:** Claude Code (Gold AI Employee)

---

## Trigger

Run this skill when ANY of the following are true:

- User requests a Facebook or Instagram post (any channel)
- A cross-domain item in `/Needs_Action` or `/In_Progress` has `business_trigger` containing `linkedin_post`, `fb_post`, or `ig_post`
- Scheduled daily at 08:00 — fetch FB/IG engagement summary
- Scheduled weekly on Monday — full engagement report
- Manual: user types `run FB/IG integration` or `draft social post`

---

## Purpose

End-to-end Facebook and Instagram management:
- **Draft**: Create post drafts from vault items (never publish directly)
- **HITL**: Route all posts through Pending_Approval/ → Approved/ gate
- **Publish**: Post only after human approval, respecting daily limits
- **Summarize**: Fetch engagement metrics, write audit reports to Audits/
- **Fallback**: Playwright scrape if Graph API quota exceeded
- **Log**: Every action to `Logs/social_{date}.json`

---

## MCP Tools Available

| Tool | Action | HITL Required? |
|------|--------|----------------|
| `draft_fb_post` | Save FB post draft to Plans/ + approval request to Pending_Approval/ | No (draft only) |
| `draft_ig_post` | Save IG post draft to Plans/ + approval request to Pending_Approval/ | No (draft only) |
| `post_fb` | Publish approved draft to Facebook | **Yes — Approved/ file required** |
| `post_ig` | Publish approved draft to Instagram | **Yes — Approved/ file required** |
| `fetch_fb_summary` | Fetch FB metrics → Audits/ | No |
| `fetch_ig_summary` | Fetch IG metrics → Audits/ | No |

---

## Full Post Flow

```
Trigger item arrives (Needs_Action or cross-domain plan)
        │
        ▼
1. draft_fb_post / draft_ig_post
        │  Creates:
        │    Plans/FB_IG_DRAFT_{platform}_{timestamp}.md
        │    Pending_Approval/APPROVAL_FB_IG_DRAFT_{platform}_{timestamp}.md
        │
        ▼
2. Human reviews Plans/ draft
        │
        ├── Approve → move APPROVAL file to Approved/
        │
        └── Reject  → move APPROVAL file to Rejected/
                 Agent logs rejection, does NOT post
        │
        ▼ (only if approved)
3. post_fb / post_ig
        │  Checks: APPROVAL file in Approved/? ✓
        │  Checks: posts today < 5? ✓
        │  Retries: 3x with 30s backoff on API error
        │  Fallback: Playwright if API fails
        │
        ▼
4. Log to Logs/social_{date}.json
   Update Dashboard.md
```

---

## Summary / Audit Flow

```
Scheduled or manual trigger
        │
        ▼
fetch_fb_summary / fetch_ig_summary
        │  Try: Facebook/Instagram Graph API (3 retries, 30s backoff)
        │  Fallback: Playwright headless scrape
        │
        ▼
Write Audits/FB_IG_Summary_{platform}_{date}.md
        │  Tables: Metric | Value
        │  Engagement rate = (likes + comments) / posts
        │
        ▼
Log to Logs/social_{date}.json
Update Dashboard.md "## Social Analytics"
```

---

## Error Handling & Retry Policy

```python
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 30

with_retry(fn, label):
    attempt 1 → success or log + wait 30s
    attempt 2 → success or log + wait 60s
    attempt 3 → success or ABORT
    on abort:
        if fetch_*_summary: switch to Playwright fallback
        if post_*: log error, create SOCIAL_ERROR note in Needs_Action
```

All retry events appended to `Logs/social_{date}.json`.

---

## Handbook Rules (FB/IG)

- **Max 5 posts/day** across FB + IG combined
- All posts require human approval before publishing
- Never post personal/sensitive information
- Summarize engagements daily; full analytics weekly
- Credentials stored in `.env` only — never in markdown files

_(Full rule text in Company_Handbook.md § Gold Tier Additions)_

---

## Setup

### 1. Install dependencies

```bash
pip install facebook-sdk instabot python-dotenv pyyaml playwright mcp
playwright install chromium
```

### 2. Configure credentials in `.env`

```bash
# Facebook
FB_PAGE_ACCESS_TOKEN=your_page_access_token_here
FB_PAGE_ID=your_page_id_here

# Instagram (Graph API — requires Business account)
IG_ACCESS_TOKEN=your_ig_access_token_here
IG_ACCOUNT_ID=your_ig_business_account_id_here

# Instagram (instabot fallback)
IG_USERNAME=your_ig_username
IG_PASSWORD=your_ig_password
```

### 3. Get Facebook Page Access Token

1. Go to [Facebook Developers](https://developers.facebook.com/) → Your App
2. Tools → Graph API Explorer
3. Select your Page → Generate Page Access Token (pages_manage_posts, pages_read_engagement)
4. For long-lived token: exchange via `/oauth/access_token?grant_type=fb_exchange_token`

### 4. Get Instagram Graph API Token

1. Requires Facebook Business Page linked to Instagram Professional account
2. In Graph API Explorer: select permissions `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`
3. Get IG Account ID: `GET /me/accounts` → find your page → `GET /{page-id}?fields=instagram_business_account`

### 5. MCP registration

MCP config is at `.claude/mcp.json` — `fb_ig` server entry already added.
Credentials are injected via environment variables (not hardcoded).

---

## Reusable Prompt Template

```
Run Integrate_FB_IG_With_Post_Summary:

Context: {describe the trigger — e.g. "New pricing inquiries from WhatsApp, need a promo post"}

Post content: {draft the post text here}
Platform: {fb | ig | both}
Hashtags: {#tag1 #tag2 ...}
Image: {path or "none"}
Source item: {vault filename that triggered this}

Action: draft_only | draft_and_fetch_summary | fetch_summary_only
```

---

## Example

**Trigger:** WhatsApp from Sarah Client — "pricing for enterprise plan?"
**Cross-domain link:** Personal pricing query → business FB/IG post

**Step 1 — Draft FB post:**
```
draft_fb_post({
  "content": "Our Enterprise Plan is here! 50-seat licenses with full support and custom onboarding. DM us or visit the link in bio for pricing.",
  "caption": "Enterprise Plan Launch",
  "hashtags": "#SaaS #Enterprise #B2B #BusinessGrowth",
  "source_item": "WHATSAPP_whatsapp_demo_002.md"
})
```
Output: `Plans/FB_IG_DRAFT_FB_20260223_091500.md` created + approval request in `Pending_Approval/`

**Step 2 — Human approves:**
Move `Pending_Approval/APPROVAL_FB_IG_DRAFT_FB_20260223_091500.md` → `Approved/`

**Step 3 — Post:**
```
post_fb({"draft_filename": "FB_IG_DRAFT_FB_20260223_091500.md"})
```
Output: Posted to Facebook. Post ID: 123456789_987654321

**Step 4 — Next morning summary:**
```
fetch_fb_summary({})
```
Output: `Audits/FB_IG_Summary_FB_2026-02-24.md` with engagement metrics table

---

## Acceptance Criteria

- [ ] `fb_ig_mcp.py` starts and lists 6 tools via MCP protocol
- [ ] `draft_fb_post` creates file in Plans/ and Pending_Approval/
- [ ] `draft_ig_post` creates file in Plans/ and Pending_Approval/
- [ ] `post_fb` is BLOCKED if no approval file in Approved/
- [ ] `post_fb` is BLOCKED if daily post count >= 5
- [ ] `fetch_fb_summary` writes Audits/FB_IG_Summary_FB_{date}.md with metrics table
- [ ] `fetch_ig_summary` writes Audits/FB_IG_Summary_IG_{date}.md with metrics table
- [ ] All API errors retried 3x with 30s backoff
- [ ] Playwright fallback activates on API failure for fetch_*_summary
- [ ] All events logged to `Logs/social_{date}.json`
- [ ] No external posts made without approval (DRY_RUN or HITL)

---

## Related Skills

- `SKILL_Cross_Integration.md` — Cross-domain trigger classification
- `SKILL_linkedin_watcher.md` — LinkedIn lead monitoring
- `SKILL_hitl_enforcer.md` — Human-in-the-loop approval gate
- `SKILL_vault_write.md` — Writing plans and notes to vault
