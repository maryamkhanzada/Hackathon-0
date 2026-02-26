---
id: handbook_amend_fbig_001
source: fb_ig_mcp
priority: medium
created: 2026-02-23 09:15
status: open
tags: ["#handbook-amendment", "#facebook", "#instagram", "#social", "#gold-tier"]
---

# Handbook Amendment Proposal: FB/IG Rules (Gold Tier)

**Priority:** medium
**Source:** Enable_FB_IG_Integration skill
**Requires:** Human owner approval to modify Company_Handbook.md

---

## Proposed Addition

Add the following section to `Company_Handbook.md` under **"Gold Tier Additions"**:

---

### Gold Tier Additions — FB/IG Social Rules

> Added by human owner instruction via `Integrate_FB_IG_With_Post_Summary` skill.

- **FB/IG Post Drafts (Draft-Only Autonomous):** The agent may auto-generate Facebook and Instagram post drafts and save them to `Plans/` with corresponding approval requests in `Pending_Approval/`. No post is ever published without explicit human approval.
- **Daily Post Limit:** Maximum **5 posts per day** across Facebook and Instagram combined. The agent enforces this limit in code — any post attempt beyond the limit is blocked and logged.
- **Engagement Summaries:** The agent fetches and summarizes FB/IG engagement metrics (likes, comments, engagement rate) daily and writes structured reports to `Audits/FB_IG_Summary_{platform}_{date}.md`.
- **API Fallback:** If the Facebook Graph API or Instagram API is unavailable, the agent falls back to Playwright headless browser for read-only metric fetching. No writes or posts are made via Playwright.
- **Credential Security:** Facebook Page Access Token, Instagram Access Token, and all related credentials must be stored in `.env` only. Never written to any markdown file.
- **Error Handling:** API failures are retried 3x with 30-second backoff. All retry events and errors are logged to `Logs/social_{date}.json`.

---

## Approval Instructions

To approve: move this file to `Approved/` folder.
To reject: move this file to `Rejected/` folder.

Once approved, the agent will insert the text above into `Company_Handbook.md`
under the Silver Tier Additions section.

**The agent has NOT modified Company_Handbook.md yet — awaiting your approval.**
