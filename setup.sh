#!/usr/bin/env bash
# setup.sh — Bootstrap the Personal AI Employee vault (Bronze Tier)
# Idempotent: safe to run multiple times.

set -euo pipefail

VAULT_ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "=== Vault Setup ==="
echo "Root: $VAULT_ROOT"

# --- 1. Create directories ---
dirs=(Inbox Needs_Action Done Skills Templates Logs)
for d in "${dirs[@]}"; do
    mkdir -p "$VAULT_ROOT/$d"
    echo "[OK] $d/"
done

# --- 2. Create .gitkeep placeholders ---
for d in "${dirs[@]}"; do
    touch "$VAULT_ROOT/$d/.gitkeep"
done
echo "[OK] Placeholders created"

# --- 3. Dashboard.md ---
if [ ! -f "$VAULT_ROOT/Dashboard.md" ]; then
    cat > "$VAULT_ROOT/Dashboard.md" << 'DASHBOARD'
# Dashboard

> **Last Updated:** `{{date}}` `{{time}}`
> **Status:** Online

---

## Finances

| Account   | Balance   | Updated |
| --------- | --------- | ------- |
| Checking  | $0.00     | --      |
| Savings   | $0.00     | --      |
| **Total** | **$0.00** |         |

## Messages

| Source | Unread | Oldest Pending |
| ------ | ------ | -------------- |
| Email  | 0      | --             |
| Slack  | 0      | --             |

## Active Projects

| Project | Status | Next Action | Due |
| ------- | ------ | ----------- | --- |
| _None_  | --     | --          | --  |

## Quick Actions

- [ ] Check email
- [ ] Review Inbox
- [ ] Process Needs_Action items
DASHBOARD
    echo "[OK] Dashboard.md created"
else
    echo "[SKIP] Dashboard.md already exists"
fi

# --- 4. Company_Handbook.md ---
if [ ! -f "$VAULT_ROOT/Company_Handbook.md" ]; then
    cat > "$VAULT_ROOT/Company_Handbook.md" << 'HANDBOOK'
# Company Handbook

## 1. Communication Rules
- Always be polite in all outgoing messages.
- Never send messages without explicit human approval.

## 2. Approval Thresholds
| Action              | Rule          |
| ------------------- | ------------- |
| Read messages       | Auto-allowed  |
| Summarize documents | Auto-allowed  |
| Send any message    | Always ask    |
| Spend money         | Always ask    |
| Delete files        | Always ask    |

## 3. Escalation Policy
- Critical: Immediate notification
- High: < 1 hour
- Medium: < 4 hours
- Low: < 24 hours

## 4. Task Workflow
Inbox/ -> Triage -> (auto) Done/ or (needs approval) Needs_Action/
HANDBOOK
    echo "[OK] Company_Handbook.md created"
else
    echo "[SKIP] Company_Handbook.md already exists"
fi

# --- 5. Verify ---
echo ""
echo "=== Verification ==="
all_good=true
for d in "${dirs[@]}"; do
    if [ -d "$VAULT_ROOT/$d" ]; then
        echo "[PASS] $d/"
    else
        echo "[FAIL] $d/ missing"
        all_good=false
    fi
done

for f in Dashboard.md Company_Handbook.md; do
    if [ -f "$VAULT_ROOT/$f" ]; then
        echo "[PASS] $f"
    else
        echo "[FAIL] $f missing"
        all_good=false
    fi
done

if [ "$all_good" = true ]; then
    echo ""
    echo "=== Vault setup complete! ==="
else
    echo ""
    echo "=== Setup finished with errors ==="
    exit 1
fi
