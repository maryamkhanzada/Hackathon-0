/**
 * email_mcp.mjs — Email MCP Server for Personal AI Employee (Silver Tier)
 *
 * Provides send_email and draft_email tools to Claude Code via MCP protocol.
 * Uses nodemailer with Gmail OAuth2. Supports dry-run mode, attachments from
 * vault paths, retry logic, and JSON logging.
 *
 * Security:
 *   - Credentials loaded from .env (never hardcoded)
 *   - DRY_RUN=true logs instead of sending (safe testing)
 *   - All actions logged to Logs/email_{date}.json
 *
 * Setup:
 *   1. npm install @modelcontextprotocol/sdk nodemailer dotenv
 *   2. Copy .env.example -> .env and fill in Gmail OAuth2 credentials
 *   3. Add to Claude Code MCP config (see SKILL_email_mcp.md)
 *
 * Usage (standalone test):
 *   DRY_RUN=true node email_mcp.mjs
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import nodemailer from "nodemailer";
import {
  readFileSync,
  writeFileSync,
  appendFileSync,
  mkdirSync,
  existsSync,
  statSync,
} from "fs";
import { join, resolve, basename, extname } from "path";
import { config as dotenvConfig } from "dotenv";

// ---------------------------------------------------------------------------
// Environment & Configuration
// ---------------------------------------------------------------------------

// Load .env from vault root
const SCRIPT_DIR = new URL(".", import.meta.url).pathname.replace(/^\/([A-Z]:)/, "$1");
dotenvConfig({ path: join(SCRIPT_DIR, ".env") });

const VAULT_PATH = process.env.VAULT_PATH || SCRIPT_DIR;
const LOGS_DIR = join(VAULT_PATH, "Logs");
const PLANS_DIR = join(VAULT_PATH, "Plans");
const DRY_RUN = process.env.DRY_RUN === "true";
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;

// Gmail OAuth2
const GMAIL_USER = process.env.GMAIL_USER || "";
const GMAIL_CLIENT_ID = process.env.GMAIL_CLIENT_ID || "";
const GMAIL_CLIENT_SECRET = process.env.GMAIL_CLIENT_SECRET || "";
const GMAIL_REFRESH_TOKEN = process.env.GMAIL_REFRESH_TOKEN || "";

// Ensure directories exist
mkdirSync(LOGS_DIR, { recursive: true });
mkdirSync(PLANS_DIR, { recursive: true });

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

function getLogPath() {
  const date = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
  return join(LOGS_DIR, `email_${date}.json`);
}

function logAction(action) {
  const entry = {
    timestamp: new Date().toISOString(),
    dry_run: DRY_RUN,
    ...action,
  };

  const logPath = getLogPath();
  let existing = [];
  if (existsSync(logPath)) {
    try {
      existing = JSON.parse(readFileSync(logPath, "utf-8"));
    } catch {
      existing = [];
    }
  }
  existing.push(entry);
  writeFileSync(logPath, JSON.stringify(existing, null, 2), "utf-8");

  // Also log to stderr for MCP debugging
  process.stderr.write(`[email_mcp] ${action.action}: ${action.status}\n`);
}

// ---------------------------------------------------------------------------
// Nodemailer Transport
// ---------------------------------------------------------------------------

function createTransport() {
  if (!GMAIL_CLIENT_ID || !GMAIL_CLIENT_SECRET || !GMAIL_REFRESH_TOKEN || !GMAIL_USER) {
    throw new Error(
      "Missing Gmail OAuth2 credentials. Set GMAIL_USER, GMAIL_CLIENT_ID, " +
      "GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN in .env"
    );
  }

  return nodemailer.createTransport({
    service: "gmail",
    auth: {
      type: "OAuth2",
      user: GMAIL_USER,
      clientId: GMAIL_CLIENT_ID,
      clientSecret: GMAIL_CLIENT_SECRET,
      refreshToken: GMAIL_REFRESH_TOKEN,
    },
  });
}

// ---------------------------------------------------------------------------
// Retry helper
// ---------------------------------------------------------------------------

async function withRetry(fn, label, maxRetries = MAX_RETRIES) {
  let lastError;
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      logAction({
        action: label,
        status: "retry",
        attempt,
        max_retries: maxRetries,
        error: err.message,
      });
      if (attempt < maxRetries) {
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS * attempt));
      }
    }
  }
  throw lastError;
}

// ---------------------------------------------------------------------------
// Attachment resolver
// ---------------------------------------------------------------------------

function resolveAttachments(attachmentPaths) {
  if (!attachmentPaths || !Array.isArray(attachmentPaths)) return [];

  return attachmentPaths.map((p) => {
    // Resolve relative to vault root
    const absPath = resolve(VAULT_PATH, p);

    // Security: only allow files within vault
    if (!absPath.startsWith(resolve(VAULT_PATH))) {
      throw new Error(`Attachment path outside vault: ${p}`);
    }

    if (!existsSync(absPath) || !statSync(absPath).isFile()) {
      throw new Error(`Attachment not found: ${absPath}`);
    }

    return {
      filename: basename(absPath),
      path: absPath,
    };
  });
}

// ---------------------------------------------------------------------------
// MIME type helper
// ---------------------------------------------------------------------------

function isHtml(body) {
  return /<\/?[a-z][\s\S]*>/i.test(body);
}

// ---------------------------------------------------------------------------
// Tool: send_email
// ---------------------------------------------------------------------------

async function handleSendEmail(args) {
  const { to, subject, body, cc, bcc, attachments, reply_to } = args;

  if (!to || !subject || !body) {
    return {
      content: [{ type: "text", text: "Error: 'to', 'subject', and 'body' are required." }],
      isError: true,
    };
  }

  const resolvedAttachments = resolveAttachments(attachments);

  const mailOptions = {
    from: GMAIL_USER,
    to: Array.isArray(to) ? to.join(", ") : to,
    subject,
    ...(isHtml(body) ? { html: body } : { text: body }),
    ...(cc ? { cc: Array.isArray(cc) ? cc.join(", ") : cc } : {}),
    ...(bcc ? { bcc: Array.isArray(bcc) ? bcc.join(", ") : bcc } : {}),
    ...(reply_to ? { replyTo: reply_to } : {}),
    attachments: resolvedAttachments,
  };

  // DRY RUN — log and return without sending
  if (DRY_RUN) {
    logAction({
      action: "send_email",
      status: "dry_run",
      to: mailOptions.to,
      subject: mailOptions.subject,
      body_preview: body.substring(0, 200),
      attachments: resolvedAttachments.map((a) => a.filename),
    });

    return {
      content: [
        {
          type: "text",
          text: `[DRY RUN] Email would be sent:\n` +
            `  To: ${mailOptions.to}\n` +
            `  Subject: ${mailOptions.subject}\n` +
            `  Body preview: ${body.substring(0, 100)}...\n` +
            `  Attachments: ${resolvedAttachments.map((a) => a.filename).join(", ") || "none"}\n` +
            `Logged to ${getLogPath()}`,
        },
      ],
    };
  }

  // REAL SEND — with retries
  try {
    const transport = createTransport();
    const info = await withRetry(
      () => transport.sendMail(mailOptions),
      "send_email"
    );

    logAction({
      action: "send_email",
      status: "sent",
      message_id: info.messageId,
      to: mailOptions.to,
      subject: mailOptions.subject,
      response: info.response,
    });

    return {
      content: [
        {
          type: "text",
          text: `Email sent successfully.\n` +
            `  Message ID: ${info.messageId}\n` +
            `  To: ${mailOptions.to}\n` +
            `  Subject: ${mailOptions.subject}\n` +
            `Logged to ${getLogPath()}`,
        },
      ],
    };
  } catch (err) {
    logAction({
      action: "send_email",
      status: "failed",
      error: err.message,
      to: mailOptions.to,
      subject: mailOptions.subject,
    });

    return {
      content: [{ type: "text", text: `Failed to send email after ${MAX_RETRIES} attempts: ${err.message}` }],
      isError: true,
    };
  }
}

// ---------------------------------------------------------------------------
// Tool: draft_email
// ---------------------------------------------------------------------------

async function handleDraftEmail(args) {
  const { to, subject, body, cc, bcc, attachments, reply_to, notes } = args;

  if (!to || !subject || !body) {
    return {
      content: [{ type: "text", text: "Error: 'to', 'subject', and 'body' are required." }],
      isError: true,
    };
  }

  const timestamp = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15);
  const safeSubject = subject.replace(/[<>:"/\\|?*\n\r]/g, "").replace(/\s+/g, "_").slice(0, 60);
  const draftFilename = `EMAIL_DRAFT_${timestamp}_${safeSubject}.md`;
  const draftPath = join(PLANS_DIR, draftFilename);

  const attachmentList = attachments && attachments.length > 0
    ? attachments.map((a) => `  - ${a}`).join("\n")
    : "  - none";

  const draftContent =
    `---\n` +
    `type: email_draft\n` +
    `status: draft\n` +
    `created: ${new Date().toISOString().slice(0, 16).replace("T", " ")}\n` +
    `to: "${Array.isArray(to) ? to.join(", ") : to}"\n` +
    `subject: "${subject}"\n` +
    `${cc ? `cc: "${Array.isArray(cc) ? cc.join(", ") : cc}"\n` : ""}` +
    `${bcc ? `bcc: "${Array.isArray(bcc) ? bcc.join(", ") : bcc}"\n` : ""}` +
    `${reply_to ? `reply_to: "${reply_to}"\n` : ""}` +
    `priority: medium\n` +
    `requires_approval: yes\n` +
    `tags:\n  - "#email-draft"\n  - "#HITL"\n` +
    `---\n\n` +
    `# Email Draft: ${subject}\n\n` +
    `## Recipients\n\n` +
    `- **To:** ${Array.isArray(to) ? to.join(", ") : to}\n` +
    `${cc ? `- **CC:** ${Array.isArray(cc) ? cc.join(", ") : cc}\n` : ""}` +
    `${bcc ? `- **BCC:** ${Array.isArray(bcc) ? bcc.join(", ") : bcc}\n` : ""}` +
    `${reply_to ? `- **Reply-To:** ${reply_to}\n` : ""}` +
    `\n## Subject\n\n${subject}\n\n` +
    `## Body\n\n${body}\n\n` +
    `## Attachments\n\n${attachmentList}\n\n` +
    `${notes ? `## Notes\n\n${notes}\n\n` : ""}` +
    `---\n\n` +
    `## Approval\n\n` +
    `- [ ] Review content\n` +
    `- [ ] Approve for sending (move approval file to /Approved)\n\n` +
    `**WARNING:** This draft will NOT be sent until explicitly approved.\n`;

  writeFileSync(draftPath, draftContent, "utf-8");

  logAction({
    action: "draft_email",
    status: "created",
    draft_path: draftPath,
    to: Array.isArray(to) ? to.join(", ") : to,
    subject,
  });

  return {
    content: [
      {
        type: "text",
        text: `Email draft created:\n` +
          `  File: ${draftFilename}\n` +
          `  Location: Plans/${draftFilename}\n` +
          `  To: ${Array.isArray(to) ? to.join(", ") : to}\n` +
          `  Subject: ${subject}\n\n` +
          `Draft saved. Requires human approval before sending.\n` +
          `Logged to ${getLogPath()}`,
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Tool definitions (JSON Schema)
// ---------------------------------------------------------------------------

const TOOLS = [
  {
    name: "send_email",
    description:
      "Send an email via Gmail. IMPORTANT: Only use this after human approval. " +
      "Supports plain text and HTML bodies, CC/BCC, reply-to, and file attachments " +
      "from the vault. In DRY_RUN mode, logs the email without sending.",
    inputSchema: {
      type: "object",
      properties: {
        to: {
          oneOf: [
            { type: "string", description: "Recipient email address" },
            { type: "array", items: { type: "string" }, description: "Multiple recipients" },
          ],
        },
        subject: { type: "string", description: "Email subject line" },
        body: { type: "string", description: "Email body (plain text or HTML)" },
        cc: {
          oneOf: [
            { type: "string" },
            { type: "array", items: { type: "string" } },
          ],
          description: "CC recipients (optional)",
        },
        bcc: {
          oneOf: [
            { type: "string" },
            { type: "array", items: { type: "string" } },
          ],
          description: "BCC recipients (optional)",
        },
        reply_to: { type: "string", description: "Reply-to address (optional)" },
        attachments: {
          type: "array",
          items: { type: "string" },
          description: "File paths relative to vault root to attach (optional)",
        },
      },
      required: ["to", "subject", "body"],
    },
  },
  {
    name: "draft_email",
    description:
      "Create an email draft as a Markdown file in Plans/. Does NOT send anything. " +
      "Use this to prepare emails for human review before sending. " +
      "The draft includes all email fields and an approval checklist.",
    inputSchema: {
      type: "object",
      properties: {
        to: {
          oneOf: [
            { type: "string", description: "Recipient email address" },
            { type: "array", items: { type: "string" }, description: "Multiple recipients" },
          ],
        },
        subject: { type: "string", description: "Email subject line" },
        body: { type: "string", description: "Email body (plain text or HTML)" },
        cc: {
          oneOf: [
            { type: "string" },
            { type: "array", items: { type: "string" } },
          ],
          description: "CC recipients (optional)",
        },
        bcc: {
          oneOf: [
            { type: "string" },
            { type: "array", items: { type: "string" } },
          ],
          description: "BCC recipients (optional)",
        },
        reply_to: { type: "string", description: "Reply-to address (optional)" },
        attachments: {
          type: "array",
          items: { type: "string" },
          description: "File paths relative to vault root to attach (optional)",
        },
        notes: {
          type: "string",
          description: "Internal notes about why this email is being drafted (optional)",
        },
      },
      required: ["to", "subject", "body"],
    },
  },
];

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const server = new Server(
  { name: "email-mcp", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// Handle tools/list
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return { tools: TOOLS };
});

// Handle tools/call
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case "send_email":
      return await handleSendEmail(args || {});

    case "draft_email":
      return await handleDraftEmail(args || {});

    default:
      return {
        content: [{ type: "text", text: `Unknown tool: ${name}` }],
        isError: true,
      };
  }
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function main() {
  process.stderr.write(`[email_mcp] Starting Email MCP Server v1.0.0\n`);
  process.stderr.write(`[email_mcp] Vault: ${VAULT_PATH}\n`);
  process.stderr.write(`[email_mcp] DRY_RUN: ${DRY_RUN}\n`);
  process.stderr.write(`[email_mcp] Gmail user: ${GMAIL_USER || "(not configured)"}\n`);

  const transport = new StdioServerTransport();
  await server.connect(transport);

  process.stderr.write(`[email_mcp] Server connected via stdio. Ready for requests.\n`);
}

main().catch((err) => {
  process.stderr.write(`[email_mcp] Fatal error: ${err.message}\n`);
  process.exit(1);
});
