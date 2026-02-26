---
id: SKILL_Gold_Documentation
version: "1.0"
created: 2026-02-25
status: active
tier: gold
tags: ["#skill", "#docs", "#pdf", "#gold", "#architecture"]
---

# SKILL: Generate_Gold_Documentation

**Version:** 1.0
**Tier:** Gold
**Last Updated:** 2026-02-25
**Author:** Claude Code (Gold AI Employee)

---

## Trigger

Activate when ANY of the following are true:

- A Gold-tier component changes and docs are stale
- A new script, MCP server, or skill is added to the vault
- A demo or onboarding requires a packaged PDF reference
- Weekly documentation audit (every Monday, automated)
- User types `generate docs`, `update README`, `export PDF`, or `refresh architecture`

---

## Purpose

Produce and maintain the definitive Gold Tier system documentation:

- **`README_Gold.md`** — full architecture, setup guide, security disclosure, demo flow, lessons learned, troubleshooting FAQ, scalability notes
- **`Docs/README_Gold.pdf`** — PDF rendition for sharing and archiving
- **`export_pdf.py`** — multi-backend PDF exporter (4-backend cascade)

---

## Components

| File | Role | Status |
|------|------|--------|
| `README_Gold.md` | Primary architecture + setup document | Active |
| `export_pdf.py` | PDF exporter — weasyprint → pdfkit → reportlab → text_copy | Active |
| `Docs/README_Gold.pdf` | Generated PDF output (32 KB) | Generated |
| `Docs/README_Gold.txt` | Text fallback copy | Generated |

---

## README_Gold.md Required Sections

| # | Section | Content |
|---|---------|---------|
| 1 | Architecture | ASCII diagram: 9 components, data flow lifecycle |
| 2 | Component Reference | 18 scripts, 3 MCP servers, 14 vault directories |
| 3 | Setup Guide | 6-step install + credentials + cron config |
| 4 | Security Disclosure | HITL matrix, secrets rules, data retention |
| 5 | Demo Flow | 6-step end-to-end walkthrough (lead → briefing) |
| 6 | Lessons Learned | 7 insights: root cause + fix + lesson |
| 7 | Troubleshooting FAQ | 8 common issues with diagnostic commands |
| 8 | Scalability | 4-phase cloud migration path |
| 9 | Skill Index | All 19 skills: tier + file + trigger |
| 10 | Environment Variables | All configurable `env` overrides |

---

## PDF Backend Cascade

```
export_pdf.py
    │
    ├── 1. weasyprint   (ideal Linux/Mac — needs GTK/Pango on Windows)
    ├── 2. pdfkit       (needs wkhtmltopdf binary installed)
    ├── 3. reportlab    (pure Python — confirmed working on Windows ✓)
    └── 4. text_copy    (universal fallback — writes .txt copy)
```

**Recommended install (Windows):**

```bash
pip install reportlab markdown
```

**Full PDF stack (Linux/Mac):**

```bash
pip install weasyprint markdown pdfkit reportlab
```

---

## CLI Reference

```bash
# Generate PDF from README_Gold.md (auto-selects best backend):
python export_pdf.py

# Custom input/output:
python export_pdf.py --input README_Gold.md --output Docs/README_Gold.pdf

# Validate architecture diagram (20 checks):
python export_pdf.py --test-diagram

# Show available backends + install status:
python export_pdf.py --check
```

---

## Architecture Diagram Requirements

The `## Architecture` section must pass 20 automated checks:

- 9 named components: `Inbox Watchers`, `Needs_Action`, `HITL`, `MCP Orchestrator`, `MCP Servers`, `Ralph Loop`, `Audit Logger`, `Briefing Engine`, `Dashboard`
- 5 box-drawing symbols: `╔`, `║`, `╚`, `╗`, `╝`
- Data flow arrows (▼ or →) in the lifecycle section
- No malformed unclosed box lines
- Section headings: `## Data Flow`, `## Lessons Learned`, `## Troubleshooting`

Test with `--test-diagram` — must exit 0 with `20/20 checks passed`.

---

## Update Procedure

When a component changes:

1. Edit the relevant section in `README_Gold.md`
2. Run `python export_pdf.py` to regenerate the PDF
3. Confirm `Docs/README_Gold.pdf` is updated (check mtime + size)
4. Update `## Gold Documentation` table in `Dashboard.md`
5. Log the action: `audit_log("docs_agent", "documentation_updated", ...)`

---

## Reusable Prompt Template

```
Run Generate_Gold_Documentation:

Mode: [full_rebuild | update_section | pdf_only | test_only]
Section (if update_section): [Architecture | Setup | FAQ | Lessons | ...]
Changed component: {describe what changed}

Expected output:
  - README_Gold.md updated with new content
  - Docs/README_Gold.pdf regenerated
  - Dashboard.md ## Gold Documentation table updated
  - Diagram passes --test-diagram (20/20)
```

---

## Acceptance Criteria

- [x] `README_Gold.md` contains all 10 required sections
- [x] Architecture diagram passes `--test-diagram` (20/20 checks)
- [x] `export_pdf.py` generates `Docs/README_Gold.pdf` via reportlab
- [x] PDF file size > 0 bytes (confirmed: 32 KB)
- [x] `--check` lists all 4 backends with install status
- [x] `--test-diagram` exits 0 on valid diagram, non-zero on failure
- [x] Unicode box-drawing chars render without crash on Windows cp1252
- [x] Text fallback writes `.txt` copy without encoding error

---

## Related Skills

- `SKILL_Weekly_Audit.md` — Briefings/ referenced in README_Gold.md
- `SKILL_ralph_loop.md` — Loop architecture documented in README_Gold.md
- `SKILL_Logging.md` — Audit schema documented in README_Gold.md
- `SKILL_Error_Recovery.md` — Resilience architecture in README_Gold.md
- `SKILL_E2E_Gold_Test.md` — Test harness validates full pipeline
