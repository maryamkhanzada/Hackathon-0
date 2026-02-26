# SKILL: Vault Setup

> **Trigger:** Manual invocation or first-run detection
> **Purpose:** Bootstrap a complete Personal AI Employee vault from scratch
> **Tier:** Bronze

---

## Description

This skill creates the full Obsidian vault structure for the Personal AI
Employee system. It is idempotent -- running it multiple times will not
duplicate or overwrite existing content.

---

## Steps

### 1. Create Folder Structure

Create the following directories if they do not exist:

```
Vault_Root/
  Inbox/          # Incoming tasks, messages, raw captures
  Needs_Action/   # Items awaiting human approval or decision
  Done/           # Completed and archived items
  Skills/         # Agent skill definitions (like this file)
  Templates/      # Reusable note templates
  Logs/           # Agent activity logs
```

**Command:**
```bash
mkdir -p Inbox Needs_Action Done Skills Templates Logs
```

### 2. Generate Dashboard.md

Create `Dashboard.md` at the vault root with sections for:
- Finances (account balances table)
- Messages (unread counts by source)
- Active Projects (status table)
- Inbox Queue (Dataview query for Inbox/)
- Recent Activity log
- Quick Actions checklist

Only create if `Dashboard.md` does not already exist.

### 3. Generate Company_Handbook.md

Create `Company_Handbook.md` at the vault root with sections for:
- Communication Rules (politeness, approval before sending)
- Approval Thresholds (autonomous vs. requires-human table)
- Escalation Policy (priority levels and response times)
- Operating Hours
- Data Handling (local-first policy)
- Task Processing Workflow (Inbox -> triage -> Done or Needs_Action)
- Error Handling
- Amendment Process (only human can modify)

Only create if `Company_Handbook.md` does not already exist.

### 4. Create Placeholder Notes

Place a `.gitkeep` or starter note in each empty folder so Obsidian recognizes
them:

```bash
touch Inbox/.gitkeep Needs_Action/.gitkeep Done/.gitkeep Templates/.gitkeep Logs/.gitkeep
```

### 5. Verify

After setup, confirm:
- [ ] All 6 directories exist
- [ ] Dashboard.md exists and is valid markdown
- [ ] Company_Handbook.md exists and is valid markdown
- [ ] SKILL_vault_setup.md exists in Skills/

---

## Acceptance Criteria

- Running `ls` at vault root shows: `Dashboard.md`, `Company_Handbook.md`,
  `Inbox/`, `Needs_Action/`, `Done/`, `Skills/`, `Templates/`, `Logs/`
- All files are well-formed Markdown renderable in Obsidian
- No data is overwritten on re-run

---

## Usage

To invoke manually from Claude Code:

```
Read Skills/SKILL_vault_setup.md, then execute each step.
```

To invoke from a Python watcher:

```python
import subprocess
subprocess.run(["bash", "setup.sh"], cwd="/path/to/vault")
```
