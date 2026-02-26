# SKILL: Enable_Full_Cross_Domain_Integration

**Version:** 1.0
**Tier:** Gold
**Last Updated:** 2026-02-23
**Author:** Claude Code (Gold AI Employee)

---

## Trigger

Run this skill when ANY of the following are true:

- A new item arrives in `/Needs_Action` or `/In_Progress` with keywords:
  `pricing`, `invoice`, `payment`, `quote`, `enterprise`, `buy`, `demo`, `business inquiry`
- A personal channel item (WhatsApp, Gmail) references a business context
- A business channel item (LinkedIn) requires personal-channel follow-up
- Scheduled: run daily at vault start to process overnight queue
- Manual invocation: user types `run cross-domain integration`

---

## Purpose

Unified cross-domain classification that links personal (Gmail, WhatsApp) signals
to business actions (LinkedIn, Sales, Finance) and vice versa, creating a single
coherent plan instead of siloed per-channel responses.

---

## Steps

### Phase 1 — Scan with Retry

```python
FOLDERS = ["Needs_Action", "In_Progress"]
MAX_RETRIES = 3
BACKOFF = 5  # seconds

items = []
for folder in FOLDERS:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            items += scan_md_files(folder)
            break
        except Exception as e:
            log_error(f"Scan attempt {attempt} failed: {e}")
            if attempt == MAX_RETRIES:
                create_alert("CROSS_ERROR", folder)
            else:
                sleep(BACKOFF * attempt)
```

### Phase 2 — Classify Each Item

For each `.md` file read the YAML frontmatter:

| Field | Rule |
|-------|------|
| `source` | `whatsapp_watcher` / `gmail_watcher` → **personal** |
| `source` | `linkedin_watcher` → **business** |
| `keywords_matched` or `tags` contain business trigger words | Upgrade to **cross_domain** |
| `priority: critical` | Always include regardless of domain |

**Business trigger keywords:** `pricing`, `invoice`, `payment`, `quote`, `enterprise`,
`buy`, `demo`, `trial`, `proposal`, `purchase`, `business inquiry`, `interested`

### Phase 3 — Find Cross-Domain Links

Apply these link rules in order:

1. **Personal pricing query → LinkedIn lead + quote draft**
   - Source: WhatsApp/Email with `pricing` or `enterprise`
   - Action: Create lead entry + draft quote document

2. **Personal payment → Finance tracker + open invoice match**
   - Source: WhatsApp/Email with `payment` or `invoice`
   - Action: Log to finance tracker; attempt to match against open invoices in vault

3. **LinkedIn hot_lead → Sales proposal**
   - Source: LinkedIn with `lead_type: hot_lead`
   - Action: Draft proposal document; queue for HITL send

4. **Business milestone (report/launch) → LinkedIn post**
   - Source: Email with `report`, `quarterly`, `launch`, `shipped`
   - Action: Queue LinkedIn milestone post draft for HITL publish

5. **LinkedIn demo request → Demo invite + CRM entry**
   - Source: LinkedIn with `demo` keyword
   - Action: Draft demo invite; create CRM lead entry

### Phase 4 — Write Unified Plan

Create `/Plans/CROSS_PLAN_{YYYYMMDD_HHMM}.md` with:

```yaml
---
id: cross_plan_{timestamp}
created: {datetime}
status: active
plan_type: cross_domain_integration
domains:
  personal: [whatsapp, gmail]
  business: [linkedin, sales, finance]
cross_links:
  - personal_item: {filename}
    business_trigger: {action}
    link_reason: "{reason}"
---
```

Sections required:
- `## Chain-of-Thought Classification` — table of all items
- `## Personal Steps` — checkbox list
- `## Business Steps` — checkbox list
- `## Integration Notes` — table: trigger → action → HITL?
- `## Queued for Social Integration` — numbered list
- `## Retry / Failure Policy`

### Phase 5 — Move Test Item to In_Progress

```python
move_file("Needs_Action/test.md", "In_Progress/test.md")
log_action("Moved test.md → In_Progress")
```

For production items: move from `Needs_Action/` to `In_Progress/` after plan creation.

### Phase 6 — Update Dashboard.md

Append `## Cross-Domain Summary` section (see template below).

### Phase 7 — Log to JSON

Write `Logs/cross_domain_{YYYY-MM-DD}.json` (append if exists).

---

## Dashboard Update Template

```markdown
## Cross-Domain Summary

_Updated: {datetime}_

**Items Classified:** {n} personal · {m} business · {k} cross-domain

### Active Cross-Domain Links
{for each link}
- **{personal_item_title}** ({source}) → **{business_trigger}** · _{link_reason}_
{endfor}

### Queued Drafts (Awaiting HITL)
{for each queued}
- [ ] {draft_title} — {trigger_source}
{endfor}
```

---

## Log Schema

```json
{
  "timestamp": "2026-02-23T09:15:00",
  "skill": "Enable_Full_Cross_Domain_Integration",
  "scan": {
    "folders_scanned": ["Needs_Action", "In_Progress"],
    "items_found": 9,
    "retry_events": []
  },
  "classification": {
    "personal": ["item_id_1", "item_id_2"],
    "business": ["item_id_3"],
    "cross_domain": ["item_id_4", "item_id_5"]
  },
  "links": [
    {
      "personal_item": "WHATSAPP_whatsapp_demo_002",
      "business_trigger": "linkedin_post_pricing_draft",
      "link_reason": "Personal pricing query → business content opportunity"
    }
  ],
  "plan_created": "Plans/CROSS_PLAN_20260223_0915.md",
  "files_moved": ["Needs_Action/test.md → In_Progress/test.md"],
  "dashboard_updated": true,
  "errors": []
}
```

---

## Acceptance Criteria

- [ ] All items in `Needs_Action` and `In_Progress` are classified by domain
- [ ] Every personal item with business trigger keywords has at least one linked business action
- [ ] `CROSS_PLAN_{timestamp}.md` created in `/Plans` with all required sections
- [ ] Test item (`test.md`) processed and moved to `/In_Progress`
- [ ] `Dashboard.md` updated with `## Cross-Domain Summary`
- [ ] `Logs/cross_domain_{date}.json` written with full event log
- [ ] No external sends/posts executed without HITL approval
- [ ] All scan failures retried up to 3x with 5s backoff; errors logged

---

## Example

**Input:**
```
WHATSAPP from Sarah Client: "Hi, can you send me the pricing for the enterprise plan?"
```

**Classification:** personal (WhatsApp) + cross-domain trigger (`pricing`, `enterprise`)

**Linked business actions:**
1. Draft LinkedIn post: "Enterprise plan pricing — what's included for teams of 10–50"
2. Prepare enterprise quote PDF → queue for HITL send to Sarah
3. Add Sarah to CRM pipeline as `interested_prospect`

**Plan entry (Integration Notes row):**
```
| Sarah WhatsApp "pricing" | LinkedIn post draft + quote prepared | Auto | Yes — post + send |
```

---

## Related Skills

- `SKILL_linkedin_watcher.md` — LinkedIn lead monitoring
- `SKILL_whatsapp_watcher.md` — WhatsApp keyword monitoring
- `SKILL_gmail_watcher.md` — Gmail important email monitoring
- `SKILL_hitl_enforcer.md` — Human-in-the-loop approval gate
- `SKILL_vault_write.md` — Writing plans and notes to vault
