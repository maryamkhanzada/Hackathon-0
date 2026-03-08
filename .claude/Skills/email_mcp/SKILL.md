---
id: SKILL_email_mcp
version: 1.0
tier: silver
created: 2026-02-19
status: active
tags:
  - mcp
  - email
  - gmail
  - HITL
---

# SKILL: Email MCP Server

## Overview

Node.js MCP (Model Context Protocol) server that gives Claude Code the ability
to send and draft emails via Gmail OAuth2. Integrated with the vault's HITL
approval workflow.

## Tools Provided

### `send_email`
Send an email via Gmail. Supports:
- Single or multiple recipients (to, cc, bcc)
- Plain text or HTML body
- Reply-to header
- File attachments from vault paths
- Dry-run mode (DRY_RUN=true logs instead of sending)
- Automatic retry (max 3 attempts with exponential backoff)

### `draft_email`
Create an email draft as a Markdown file in `Plans/`. Does NOT send anything.
Use this to prepare emails for human review. Includes approval checklist.

## Prerequisites

1. Node.js 18+ installed
2. npm dependencies installed:
   ```bash
   cd D:/Hackathon-0
   npm install @modelcontextprotocol/sdk nodemailer dotenv
   ```
3. Gmail OAuth2 credentials configured in `.env`

## Setup

### Step 1: Create .env file

```bash
cp .env.example .env
# Edit .env with your Gmail OAuth2 credentials
```

### Step 2: Get Gmail OAuth2 Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use existing)
3. Enable **Gmail API**
4. Go to **Credentials** → Create **OAuth 2.0 Client ID** (Desktop app)
5. Download the JSON → note `client_id` and `client_secret`
6. Go to [OAuth Playground](https://developers.google.com/oauthplayground):
   - Settings gear → Check "Use your own OAuth credentials"
   - Enter your client_id and client_secret
   - Authorize scope: `https://www.googleapis.com/auth/gmail.send`
   - Exchange code for tokens → copy the **refresh_token**
7. Fill in `.env`:
   ```
   GMAIL_USER=your.email@gmail.com
   GMAIL_CLIENT_ID=xxxxx.apps.googleusercontent.com
   GMAIL_CLIENT_SECRET=xxxxx
   GMAIL_REFRESH_TOKEN=xxxxx
   ```

### Step 3: Add to Claude Code MCP Config

Add to `~/.claude/settings.json` (or project `.claude/settings.local.json`):

```json
{
  "mcpServers": {
    "email": {
      "command": "node",
      "args": ["D:/Hackathon-0/email_mcp.mjs"],
      "env": {
        "VAULT_PATH": "D:/Hackathon-0",
        "DRY_RUN": "true"
      }
    }
  }
}
```

**Set `DRY_RUN` to `"false"` only after testing.**

### Step 4: Test

```bash
# Test dry-run mode (safe)
DRY_RUN=true node email_mcp.mjs

# Verify in Claude Code
# Claude should now see send_email and draft_email tools
```

## Security

- Credentials in `.env` only — NEVER hardcoded
- `.env` is in `.gitignore` — never committed
- DRY_RUN=true by default — explicitly opt-in to real sending
- Attachments restricted to vault directory (path traversal blocked)
- All actions logged to `Logs/email_{date}.json`
- HITL: `send_email` should only be used after human approval

## Logging

Every action (send, draft, dry-run, retry, error) is logged to:
```
Logs/email_YYYY-MM-DD.json
```

Each entry includes: timestamp, action, status, recipients, subject, errors.

## HITL Integration

1. Agent uses `draft_email` to create draft in `Plans/`
2. Human reviews the draft
3. Human moves approval to `/Approved/`
4. Agent uses `send_email` to send the approved email
5. All actions logged to `Logs/`

## Acceptance Criteria

- [x] MCP server with stdio transport
- [x] `send_email` tool with full Gmail OAuth2 support
- [x] `draft_email` tool creating Markdown drafts in Plans/
- [x] Dry-run mode (DRY_RUN=true)
- [x] Retry logic (max 3 attempts)
- [x] Vault-path attachments with security check
- [x] JSON logging to Logs/
- [x] No hardcoded credentials
- [x] HTML and plain text body support
- [x] CC, BCC, Reply-To support
