---
objective: Follow up on 3 sales leads across LinkedIn and WhatsApp
created: 2026-02-19 21:45
status: pending
priority: high
requires_approval: yes
estimated_time: 45 minutes
triggered_by:
  - LINKEDIN_linkedin_20260219_213636_demo001.md (John Smith)
  - LINKEDIN_linkedin_20260219_213636_demo002.md (Maria Garcia)
  - WHATSAPP_whatsapp_demo_002.md (Sarah Client)
related_plans:
  - Plans/LINKEDIN_POST_20260219_2140_Enterprise.md (already drafted)
  - Plans/LINKEDIN_POST_20260219_2140_Demo.md (already drafted)
tags:
  - "#plan"
  - "#sales"
  - "#linkedin"
  - "#whatsapp"
  - "#HITL"
---

# PLAN: Sales Lead Follow-Up — 3 Active Leads

## Objective

Respond to 3 sales leads detected across LinkedIn and WhatsApp. Each requires a direct reply (external message = human approval needed). LinkedIn posts have already been drafted separately.

## Context

### Lead 1: John Smith (HOT LEAD — critical)
- **Source:** LinkedIn message
- **Type:** hot_lead
- **Request:** "We'd like to buy your enterprise plan. Can you send pricing for 50 seats?"
- **Action needed:** Send enterprise pricing for 50 seats, schedule call

### Lead 2: Maria Garcia (INTERESTED PROSPECT — medium)
- **Source:** LinkedIn notification (comment on post)
- **Type:** interested_prospect
- **Request:** "Really interested in this! Can I get a demo?"
- **Action needed:** Send demo link/schedule demo

### Lead 3: Sarah Client (PRICING INQUIRY — high)
- **Source:** WhatsApp message
- **Type:** pricing_inquiry
- **Request:** "Can you send me the pricing for the enterprise plan?"
- **Action needed:** Send enterprise pricing sheet

### Already completed:
- 2 LinkedIn sales post drafts created (pending approval in `/Pending_Approval/`)

## Chain of Thought

1. John Smith is a HOT LEAD — 50-seat enterprise deal is high-value, respond FIRST
2. Sarah Client (WhatsApp) also wants enterprise pricing — could share same pricing sheet
3. Maria Garcia wants a demo — lower urgency but still important for pipeline
4. All 3 require external messages → 3 separate approval requests
5. Should prepare pricing sheet / materials BEFORE creating approval requests
6. John Smith + Sarah Client want the same thing (pricing) — can prepare once, send twice
7. LinkedIn posts are supplementary — direct replies are the priority

## Step-by-Step Plan

- [ ] **Step 1:** Prepare enterprise pricing sheet (50-seat tier) — check for existing pricing doc
- [ ] **Step 2:** Draft personalized LinkedIn reply to John Smith: "Thanks for your interest! Here's our enterprise pricing for 50 seats. Would love to schedule a call to discuss your needs."
- [ ] **Step 3:** Draft WhatsApp reply to Sarah Client: "Hi Sarah! Here's our enterprise plan pricing. Happy to answer any questions."
- [ ] **Step 4:** Draft LinkedIn reply/DM to Maria Garcia: "Thanks for your interest! Here's a link to book a live demo."
- [ ] **Step 5:** Create approval request #1 in `/Pending_Approval/`: Reply to John Smith (LinkedIn)
- [ ] **Step 6:** Create approval request #2 in `/Pending_Approval/`: Reply to Sarah Client (WhatsApp)
- [ ] **Step 7:** Create approval request #3 in `/Pending_Approval/`: Reply to Maria Garcia (LinkedIn)
- [ ] **Step 8:** WAIT for human to approve each (can be batch-approved)
- [ ] **Step 9:** Send approved messages via respective platforms
- [ ] **Step 10:** Log leads in sales tracker, move notes to `/Done/`, update Dashboard

## Dependencies

| Step | Depends On | Blocker? |
|------|-----------|----------|
| Steps 2-4 | Step 1 (pricing ready) | Partial — drafts possible without |
| Steps 5-7 | Steps 2-4 (drafts ready) | YES |
| Step 8 | Human approval | **YES — blocking (3x)** |
| Step 9 | Step 8 | **YES** |

## Risks & Notes

- **PRIORITY:** John Smith first — hot lead, 50-seat deal could be significant revenue
- **EFFICIENCY:** John Smith + Sarah Client want same pricing → prepare once, personalize twice
- **NOTE:** LinkedIn posts (already drafted) are supplementary and don't replace direct replies
- **RISK:** Delay in responding to hot leads loses momentum — human should approve quickly
- **RECOMMENDATION:** Create a reusable enterprise pricing template for future leads
- **COMPLIANCE:** All 3 replies are external messages — each needs explicit human approval
