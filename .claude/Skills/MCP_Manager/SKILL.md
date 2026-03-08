# SKILL: Setup_Multiple_MCP_Servers

**Version:** 1.0
**Tier:** Gold
**Last Updated:** 2026-02-23
**Author:** Claude Code (Gold AI Employee)

---

## Trigger

Run this skill when ANY of the following are true:

- A new MCP server is added to `.claude/mcp.json`
- An MCP server becomes unresponsive (logged in `Logs/mcp_{date}.json`)
- User requests "MCP status", "check servers", or "restart MCP"
- Orchestrator startup (`python mcp_orchestrator.py`)
- Daily health audit at 08:00

---

## Purpose

Unified management of all MCP server subprocesses:
- **Health monitoring**: JSON-RPC ping every 60s per server; flag degraded instances
- **Auto-restart**: Dead process respawned within one health cycle (60s max latency)
- **Load balancing**: Round-robin across multiple instances of the same server type
- **Offline queuing**: If all instances offline, tasks queued to `Plans/MCP_QUEUE_*.md` and retried on recovery
- **Usage logging**: Every call, ping, restart, and queue event to `Logs/mcp_{date}.json`
- **Dashboard sync**: `## MCP Status` table updated after every health cycle

---

## Architecture

```
mcp_orchestrator.py
        │
        ├── McpServerProcess     ← one subprocess: spawn / ping / restart / call_tool
        │     stdin/stdout JSON-RPC transport (MCP wire protocol)
        │
        ├── McpPool              ← N instances of one server (round-robin LB)
        │     get_server() -> next alive instance
        │     restart_dead() -> respawn failed instances
        │
        ├── McpTaskQueue         ← offline task buffer
        │     enqueue() -> Plans/MCP_QUEUE_{id}.md + in-memory deque
        │     retry on next health cycle when server recovers
        │
        └── McpOrchestrator      ← master coordinator
              run()              ← main loop (HEALTH_INTERVAL = 60s)
              call(server, tool, args)  ← dispatch with queue fallback
              run_health_cycle() ← ping + restart + retry + dashboard update
```

---

## Registered Servers

| Server | Command | Primary Tools | Auth |
|--------|---------|---------------|------|
| `email` | `node email_mcp.mjs` | `send_email`, `draft_email` | Gmail OAuth2 |
| `fb_ig` | `python fb_ig_mcp.py` | `draft_fb_post`, `draft_ig_post`, `post_fb`, `post_ig`, `fetch_fb_summary`, `fetch_ig_summary` | FB Graph API + instabot |
| `x` | `python x_mcp.py` | `draft_x_post`, `post_x`, `reply_x`, `fetch_x_summary` | Tweepy OAuth1a + OAuth2 |

---

## Health Monitoring Loop

```
every 60 seconds:
  for each pool (email, fb_ig, x):
    1. ping_all()
         send {"jsonrpc":"2.0","id":N,"method":"tools/list","params":{}}
         wait 5s for valid JSON response
         ok=True if response has "result" or "error" key
    2. if instance not alive or ping failed:
         status = "degraded"
         restart_dead() -> stop() + sleep(10) + start()
    3. if task_queue.size() > 0 and pool has alive instance:
         retry_queued() -> dispatch buffered tasks
  log_mcp(health_cycle event)
  update Dashboard.md "## MCP Status" table
```

---

## Load Balancing (Round-Robin)

```
McpPool.get_server():
    n = len(instances)
    for i in range(n):             # try each instance once
        inst = instances[self._index % n]
        self._index += 1
        if inst.is_alive():
            return inst
    return None                    # all offline -> trigger queue
```

For horizontal scale, increase `num_instances` in `McpPool.__init__`:
```python
self.pools["fb_ig"] = McpPool("fb_ig", cfg, num_instances=2)
```

---

## Offline Graceful Handling (Task Queue)

```
McpOrchestrator.call(server, tool, args):
    inst = pool.get_server()
    if inst is None:               # all offline
        task_queue.enqueue(...)    # buffer to Plans/MCP_QUEUE_{id}.md
        return None                # caller gets None, does not crash

# On next health cycle when server recovers:
    _retry_queued():
        for task in pending:
            if pool.get_server() exists:
                dispatch task
                delete Plans/MCP_QUEUE_{id}.md
            else:
                re-enqueue for next cycle
```

---

## Log Schema (`Logs/mcp_{date}.json`)

```json
[
  {
    "timestamp": "2026-02-23T09:15:00",
    "event": "server_start",
    "server": "fb_ig#0",
    "pid": 12345,
    "status": "ok"
  },
  {
    "timestamp": "2026-02-23T09:15:05",
    "event": "health_ping",
    "server": "x#0",
    "ok": true,
    "ping_num": 1
  },
  {
    "timestamp": "2026-02-23T09:15:06",
    "event": "tool_call",
    "server": "fb_ig",
    "instance": "fb_ig#0",
    "tool": "draft_fb_post",
    "status": "ok",
    "elapsed_s": 0.042
  },
  {
    "timestamp": "2026-02-23T09:15:07",
    "event": "server_restart",
    "server": "x#0",
    "restart_count": 1,
    "status": "ok"
  },
  {
    "timestamp": "2026-02-23T09:15:08",
    "event": "task_queued",
    "task_id": "fb_ig_draft_fb_post_20260223_091508",
    "server": "fb_ig",
    "tool": "draft_fb_post"
  }
]
```

---

## Dashboard Table Format

```markdown
## MCP Status

_Updated: 2026-02-23 09:15_  **Cycle:** #1  **Servers:** 3

| Instance | Status  | PID   | Last Ping   | Restarts | Started              |
|----------|---------|-------|-------------|----------|----------------------|
| email#0  | running | 12345 | ✓ 09:15:00  | 0        | 2026-02-23 09:14:58  |
| fb_ig#0  | running | 12346 | ✓ 09:15:01  | 0        | 2026-02-23 09:14:59  |
| x#0      | running | 12347 | ✓ 09:15:02  | 0        | 2026-02-23 09:14:59  |
```

---

## Setup

### 1. Install dependencies

```bash
pip install tweepy facebook-sdk instabot python-dotenv pyyaml mcp playwright
npm install @modelcontextprotocol/sdk nodemailer dotenv
playwright install chromium
```

### 2. Register a new MCP server

Add to `.claude/mcp.json`:
```json
"my_server": {
  "command": "python",
  "args": ["D:/Hackathon-0/my_server_mcp.py"],
  "env": {
    "VAULT_PATH": "D:/Hackathon-0",
    "DRY_RUN": "false",
    "MY_CRED": "${MY_CRED}"
  }
}
```
Then restart the orchestrator — it auto-discovers all entries.

### 3. Run modes

```bash
# Full monitor loop (production):
python mcp_orchestrator.py

# Single health check (cron):
python mcp_orchestrator.py --once

# Dry-run test all servers:
DRY_RUN=true python mcp_orchestrator.py --test

# Status table:
python mcp_orchestrator.py --status

# Don't auto-launch processes (monitoring only):
python mcp_orchestrator.py --no-launch
```

### 4. Cron / Task Scheduler

```bash
# Health check every minute (Windows Task Scheduler):
python D:\Hackathon-0\mcp_orchestrator.py --once >> D:\Hackathon-0\Logs\mcp_cron.log 2>&1

# Or run continuously as a daemon:
python D:\Hackathon-0\mcp_orchestrator.py
```

---

## Reusable Prompt Template

```
Run Setup_Multiple_MCP_Servers:

Action: [check_health | restart_server | add_server | test_all | queue_status]

Server: [email | fb_ig | x | all]
Tool (if test): [draft_email | draft_fb_post | draft_x_post | ...]
Arguments (if test): {key: value, ...}

Expected outcome: [all healthy | restart completed | new server registered | ...]
```

---

## Acceptance Criteria

- [ ] `mcp_orchestrator.py` starts and registers all 3 servers from mcp.json
- [ ] Health ping (tools/list) sent to each server every 60s
- [ ] Dead server detected and restarted within one health cycle
- [ ] Round-robin load balancing returns next alive instance
- [ ] Offline task queued to `Plans/MCP_QUEUE_*.md` when all instances down
- [ ] Queued task retried and queue file deleted when server recovers
- [ ] Every event (start, ping, call, restart, queue) logged to `Logs/mcp_{date}.json`
- [ ] `Dashboard.md ## MCP Status` table updated every health cycle
- [ ] `--test` successfully invokes draft tool on each server
- [ ] `--once` runs single cycle and exits cleanly
- [ ] `--status` prints current server state without starting anything
- [ ] SIGINT/SIGTERM handled gracefully (all processes stopped)

---

## Related Skills

- `SKILL_FB_IG.md` — Facebook & Instagram MCP tools
- `SKILL_X_Integration.md` — X (Twitter) MCP tools
- `SKILL_email_mcp.md` — Email MCP tools
- `SKILL_hitl_enforcer.md` — Human-in-the-loop approval gate
- `SKILL_Cross_Integration.md` — Cross-domain trigger classification
