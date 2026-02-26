---
id: handbook_amend_x_001
source: x_mcp
priority: medium
created: 2026-02-23 09:30
status: open
tags: ["#handbook-amendment", "#x-twitter", "#social", "#gold-tier"]
---

# Handbook Amendment Proposal: X (Twitter) Rules (Gold Tier)

**Priority:** medium
**Source:** Integrate_X_With_Post_Summary skill
**Requires:** Human owner approval to modify Company_Handbook.md

---

## Proposed Addition

Add the following section to `Company_Handbook.md` under **"Gold Tier Additions"**:

---

### Gold Tier Additions — X (Twitter) Rules

> Added by human owner instruction via `Integrate_X_With_Post_Summary` skill.

- **X Post Drafts (Draft-Only Autonomous):** The agent may auto-generate tweet drafts and save them to `Plans/` with corresponding approval requests in `Pending_Approval/`. No tweet is ever published without explicit human approval.
- **No Spam Policy:** All posts must be relevant, value-adding, and non-repetitive. The agent will not draft the same or substantially similar tweet twice within 24 hours. No follower spam, keyword stuffing, or engagement baiting.
- **Daily Post Limit:** Maximum **10 posts per day** (tweets + replies combined). The agent enforces this limit in code — any post attempt beyond the limit is blocked and logged.
- **Daily Engagement Summary:** The agent fetches and summarizes X timeline metrics (likes, retweets, replies, impressions, engagement rate) daily and writes structured reports to `Audits/X_Summary_{date}.md`.
- **Rate Limit Compliance:** When the X API returns a 429 rate-limit error, the agent automatically waits 15 minutes before retrying. No more than 3 retry cycles per action.
- **Token Security:** All X API keys and tokens stored in `.env` and `.x_refresh_token` only. These files are gitignored. Never written to any markdown file.
- **Thread Posts:** Multi-tweet threads must be fully drafted and approved before any tweet in the thread is published.

---

## Approval Instructions

To approve: move this file to `Approved/` folder.
To reject: move this file to `Rejected/` folder.

Once approved, the agent will insert the text above into `Company_Handbook.md`.

**The agent has NOT modified Company_Handbook.md yet — awaiting your approval.**
