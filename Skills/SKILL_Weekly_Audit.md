# SKILL: Implement_Weekly_Audit_Briefing

**Version:** 1.0
**Tier:** Gold
**Last Updated:** 2026-02-23
**Author:** Claude Code (Gold AI Employee)

---

## Trigger

Run this skill when ANY of the following are true:

- **Scheduled:** Every Monday at 07:00 (cron / Task Scheduler)
- **Manual:** User types `run weekly audit`, `generate CEO briefing`, or `audit week {ISO}`
- **Ralph loop:** `ralph_loop.sh` invokes `python audit.py --ralph` as a processing step
- **On-demand for prior week:** `python audit.py --week 2026-W07`

---

## Purpose

Transform raw vault data into a concise, decision-ready CEO Briefing:
- **Revenue**: Income vs expenses, WoW trend, category breakdown, top expenses
- **Throughput**: Tasks completed, by priority and source channel
- **Bottlenecks**: Pipeline items aged >5 days or stuck critical/high items
- **Social**: Cross-platform engagement rate trends (FB/IG/X)
- **MCP Health**: Uptime, restart count, tool call volume
- **Suggestions**: Rule-based actionable items with severity and savings estimates
- **Fallbacks**: Missing data handled gracefully — prior week or estimates used

---

## Data Sources

| Source | Path | Content |
|--------|------|---------|
| Transactions | `Accounting/transactions_{week}.json` | Income and expense line items |
| Completed tasks | `Done/*.md` | YAML frontmatter: priority, source, created |
| Pipeline | `In_Progress/*.md` | Items to check for age / priority bottlenecks |
| Social metrics | `Audits/FB_IG_Summary_*.md` | FB/IG engagement tables |
| Twitter metrics | `Audits/X_Summary_*.md` | X engagement tables and trends |
| MCP logs | `Logs/mcp_{date}.json` | Tool calls, restarts, queue events |
| Cross-domain | `Logs/cross_domain_{date}.json` | Integration link events |

---

## Analysis Pipeline

```
run_audit(week_str)
        │
        ├── load_accounting(week) ──────────── fallback to prior week if missing
        │     → compute_revenue()             total, net, margin, by-category
        │     → compute_wow_delta()           WoW income/expense/net deltas
        │
        ├── load_done_items(week) ──────────── filter by created date in window
        │     → compute_task_throughput()     count, by_priority, by_source
        │
        ├── load_in_progress_items()
        │     → detect_bottlenecks()          age >= AGE_THRESHOLD or critical/high
        │
        ├── load_audit_files() ─────────────── parse metric tables from Audits/*.md
        │     → compute_social_summary()      per-platform engagement snapshot
        │
        ├── load_mcp_log(today) ─────────────  tool calls, restarts, queue depth
        │
        └── generate_suggestions() ──────────  rule engine (10 rules)
              → write_briefing()              render + write Briefings/CEO_Briefing_{week}.md
```

---

## Suggestion Rules

| Rule | Trigger | Severity |
|------|---------|----------|
| Unused subscription | `description` contains "unused"/"last login" AND amount ≥ $200 | HIGH |
| Low profit margin | margin < 40% | HIGH |
| Expense spike | expenses grew >20% WoW | MEDIUM |
| Consulting underutilised | consulting < 50% of SaaS revenue | LOW |
| Critical bottleneck | any In_Progress item with `priority: critical` | CRITICAL |
| Pipeline congestion | >5 aged items in In_Progress | HIGH |
| Approval backlog | >5 files in Pending_Approval/ | MEDIUM |
| Low social engagement | any platform engagement rate < 1% | MEDIUM |
| New client onboarding | income item contains "first payment" | LOW (growth signal) |
| SaaS revenue growth | SaaS income grew >50% WoW | LOW (positive signal) |

---

## Real-World Fallbacks

| Failure | Fallback | Briefing Note |
|---------|----------|---------------|
| `Accounting/{week}.json` missing | Load prior week's file | "FALLBACK ESTIMATES" warning in briefing |
| Prior week also missing | Use `FALLBACK_REVENUE` / `FALLBACK_EXPENSES` constants | Flag as estimate |
| `Done/` empty for week | Report 0 throughput | Note: "No done items found" |
| `Audits/` empty | Skip social section | Note: "Social audit data unavailable" |
| JSON parse error | Log + skip that file | Note in Data Notes section |
| Any unhandled error | Logged to `Logs/audit_{date}.log` | Noted in briefing |

---

## Ralph Loop Integration

`audit.py` is designed to be a processing step inside `ralph_loop.sh`:

```bash
# Trigger audit via Ralph (ralph_loop.sh detects <promise>AUDIT_COMPLETE</promise>)
./ralph_loop.sh --prompt "Run python audit.py --week current --ralph" --max-loops 3

# Or call directly with Ralph flag:
python audit.py --ralph
```

On success, `audit.py` outputs:
```
<promise>AUDIT_COMPLETE</promise>
Briefing: Briefings/CEO_Briefing_2026-W08.md
```

Ralph loop then:
1. Detects the promise tag — marks task done
2. Can trigger downstream: email briefing, post summary to Slack/X
3. Logs completion to `Logs/activity_{date}.log`

---

## Briefing Structure

```
Briefings/CEO_Briefing_2026-W08.md
  ├── YAML frontmatter (type, period, week_start, week_end, generated, status)
  ├── ## Executive Summary  (1 paragraph: revenue signal, net profit, bottlenecks, actions)
  ├── ## Revenue            (WoW comparison table + category breakdown + top expenses)
  ├── ## Task Throughput    (completed count, by priority, by source)
  ├── ## Bottlenecks        (aged/critical pipeline items table)
  ├── ## Social Media       (cross-platform engagement metrics table)
  ├── ## MCP Infrastructure (tool calls, restarts, queue depth)
  ├── ## Suggestions        (checkbox action items, sorted critical→low, with savings)
  ├── ## Next Week Priorities (top 3 actions as checkboxes)
  └── ## Data Notes         (fallback warnings, parse errors — only if present)
```

---

## Accounting File Format

`Accounting/transactions_{YYYY-W##}.json`:
```json
{
  "week": "2026-W08",
  "period_start": "2026-02-17",
  "period_end": "2026-02-23",
  "income": [
    {
      "date": "2026-02-18",
      "source": "Client Name",
      "category": "saas_subscription",
      "amount": 1200.00,
      "description": "50-seat enterprise plan"
    }
  ],
  "expenses": [
    {
      "date": "2026-02-19",
      "vendor": "AWS",
      "category": "infrastructure",
      "amount": 142.37,
      "description": "February AWS bill"
    }
  ]
}
```

**Income categories:** `saas_subscription`, `consulting`, `professional_services`, `affiliate`, `other`
**Expense categories:** `infrastructure`, `tooling`, `communication`, `crm`, `design`, `contractor`, `other`

---

## Setup

### 1. Install dependencies

```bash
pip install pyyaml python-dotenv
```

### 2. Create Accounting/ data file for current week

```bash
# Copy template and fill in:
cp Accounting/transactions_2026-W08.json Accounting/transactions_{current_week}.json
# Edit income and expense arrays
```

### 3. Cron / Task Scheduler

**Windows Task Scheduler:**
```
Program: python
Arguments: D:\Hackathon-0\audit.py --ralph
Start in: D:\Hackathon-0
Trigger: Weekly, Monday, 07:00
```

**Linux/macOS cron:**
```bash
# Every Monday at 07:00
0 7 * * 1 cd /path/to/vault && python audit.py --ralph >> Logs/cron_audit.log 2>&1
```

### 4. CLI reference

```bash
python audit.py                       # Current ISO week
python audit.py --week 2026-W08       # Specific past week
python audit.py --test                # Test with mock data
python audit.py --dry-run             # Print to stdout, don't save
python audit.py --ralph               # Ralph mode (outputs promise tag)
python audit.py --vault /other/path   # Override vault path
```

---

## Reusable Prompt Template

```
Run Implement_Weekly_Audit_Briefing:

Week: {ISO week string, e.g. 2026-W08, or "current"}
Mode: {full | dry-run | ralph}
Focus areas: {revenue | bottlenecks | social | all}

Expected output: Briefings/CEO_Briefing_{week}.md with
  - Executive summary paragraph
  - Revenue WoW comparison table
  - Bottleneck list with severity
  - Suggestions with checkbox actions and savings estimates
```

---

## Example Output (Executive Summary)

```
Revenue is strong (+21.2% WoW). Net profit: $8,046.23 at 81.5% margin.
1 tasks completed this week. 10 bottleneck(s) detected in pipeline.
3 critical and 1 high priority actions require attention.
```

**Top suggestions generated:**
- `[CRITICAL]` 3 items stuck in pipeline — escalate immediately
- `[HIGH]` Cancel Unused SaaS Tool Z ($220.00 saving) — last login 45 days ago
- `[MEDIUM]` 8 items in Pending_Approval — clear queue
- `[LOW]` 2 new clients onboarded ($3,360 ARR) — assign CSM

---

## Acceptance Criteria

- [ ] `audit.py --test` completes and writes `Briefings/CEO_Briefing_2026-W08.md`
- [ ] Briefing contains all 8 sections (Summary, Revenue, Throughput, Bottlenecks, Social, MCP, Suggestions, Next Week)
- [ ] WoW revenue delta computed correctly from two accounting files
- [ ] Bottleneck detection finds items aged ≥ 5 days OR critical/high priority
- [ ] Suggestions include cost-saving items from expense analysis
- [ ] Missing `Accounting/` file triggers fallback without crashing
- [ ] `--ralph` flag outputs `<promise>AUDIT_COMPLETE</promise>`
- [ ] `Logs/audit_{date}.json` written with event record
- [ ] Ralph loop can detect promise tag and trigger downstream actions

---

## Related Skills

- `SKILL_Cross_Integration.md` — cross-domain data sourcing for briefing context
- `SKILL_FB_IG.md` — generates Audits/FB_IG_Summary_*.md inputs
- `SKILL_X_Integration.md` — generates Audits/X_Summary_*.md inputs
- `SKILL_MCP_Manager.md` — generates Logs/mcp_*.json inputs
- `SKILL_hitl_enforcer.md` — Pending_Approval count used in suggestion engine
