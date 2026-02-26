---
objective: Acknowledge deadline change and update project timeline to Friday
created: 2026-02-19 21:45
status: pending
priority: high
requires_approval: yes
estimated_time: 30 minutes
triggered_by:
  - 20260218_2156_Email_Project_deadline_moved_to_Friday.md
  - EMAIL_gmail_demo_001.md
deadline: 2026-02-20 17:00
tags:
  - "#plan"
  - "#project"
  - "#deadline"
  - "#HITL"
---

# PLAN: Project Deadline Update — Moved to Friday

## Objective

Acknowledge the manager's notification that the client pushed the project deadline to Friday, update the project timeline accordingly, and send confirmation reply.

## Context

- **From:** manager@company.com
- **Message:** "The client asked to push the deadline to Friday. Please update your timeline accordingly."
- **New deadline:** Friday Feb 20 (TOMORROW)
- **Note:** 2 duplicate entries in Needs_Action (old + new watcher format) — same task

## Chain of Thought

1. Manager has notified of a deadline CHANGE (not a new task)
2. New deadline is Friday = TOMORROW — tight turnaround
3. Need to: (a) understand what project this refers to, (b) update timeline, (c) confirm
4. Could be related to the Q4 report deadline (also Friday) — or a separate project
5. Reply to manager requires human approval
6. Should check calendar for conflicts with the new Friday deadline

## Step-by-Step Plan

- [ ] **Step 1:** Identify which project's deadline moved — check context, recent projects, Active Projects in Dashboard
- [ ] **Step 2:** Review current project timeline — what was the original deadline? What tasks remain?
- [ ] **Step 3:** Assess feasibility — can remaining work be completed by Friday?
- [ ] **Step 4:** Update project timeline/schedule if a tracking doc exists
- [ ] **Step 5:** Check calendar for Friday conflicts (meetings, other deadlines)
- [ ] **Step 6:** Draft reply to manager confirming timeline update (or flagging concerns)
- [ ] **Step 7:** Create approval request in `/Pending_Approval/` for email reply
- [ ] **Step 8:** WAIT for human approval
- [ ] **Step 9:** Send reply to manager@company.com
- [ ] **Step 10:** Update Dashboard Active Projects section, move notes to `/Done/`

## Dependencies

| Step | Depends On | Blocker? |
|------|-----------|----------|
| Step 2 | Step 1 (project ID) | Possible |
| Step 4 | Steps 2-3 | YES |
| Step 7 | Step 6 | YES |
| Step 8 | Human approval | **YES — blocking** |

## Risks & Notes

- **RISK:** "Friday" is TOMORROW — if significant work remains, may need to negotiate
- **NOTE:** Consolidate with PLAN_QUARTERLY_REPORT if they're the same project
- **NOTE:** Duplicate Needs_Action files — clean up after processing
- **COMPLIANCE:** Email reply requires human approval
