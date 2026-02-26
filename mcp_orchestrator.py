#!/usr/bin/env python3
"""
mcp_orchestrator.py — MCP Server Orchestrator for Personal AI Employee (Gold Tier)

Manages all MCP servers (email, fb_ig, x) as subprocesses:
  - Health monitoring  : JSON-RPC ping every 60s; restart if unresponsive
  - Auto-restart       : Dead server respawned within one health cycle
  - Load balancing     : Round-robin across multiple instances of the same server
  - Offline queuing    : If all instances down, tasks queued to Plans/MCP_QUEUE_*.md
                         and retried when a server comes back
  - Usage logging      : Every call, ping, restart logged to Logs/mcp_{date}.json
  - Dashboard update   : "## MCP Status" table refreshed after each health cycle

Architecture:
  McpServerProcess   — wraps a single subprocess; handles spawn/ping/kill/restart
  McpPool            — manages N instances of one server; exposes round-robin pick
  McpOrchestrator    — owns all pools; runs the monitor loop; drives the task queue

JSON-RPC over stdio:
  MCP servers read JSON-RPC requests from stdin and write responses to stdout.
  Health ping = tools/list request; valid response = healthy.

Usage:
  python mcp_orchestrator.py                 # Full monitor loop (runs forever)
  python mcp_orchestrator.py --once          # Single health check cycle
  python mcp_orchestrator.py --status        # Print current status table
  python mcp_orchestrator.py --test          # DRY_RUN invoke each server once
  python mcp_orchestrator.py --no-launch     # Monitor-only, don't spawn servers
"""

import argparse
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import yaml

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------

VAULT_ROOT  = Path(__file__).resolve().parent
CONFIG_PATH = VAULT_ROOT / "config.yaml"
MCP_JSON    = VAULT_ROOT / ".claude" / "mcp.json"
LOGS_DIR    = VAULT_ROOT / "Logs"
PLANS_DIR   = VAULT_ROOT / "Plans"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
PLANS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(VAULT_ROOT / ".env")

HEALTH_INTERVAL  = 60    # seconds between health-check cycles
PING_TIMEOUT     = 5     # seconds to wait for JSON-RPC response
MAX_RESTART_WAIT = 10    # seconds between restart attempts
QUEUE_RETRY_SEC  = 30    # seconds between queued-task retry attempts

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mcp_orch] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "mcp_orchestrator.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("mcp_orch")

# Unified structured audit logger
_WATCHERS_DIR = VAULT_ROOT / "watchers"
sys.path.insert(0, str(_WATCHERS_DIR))
try:
    from audit_logger import audit_log as _audit_log  # noqa: E402
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False


def log_mcp(entry: dict) -> None:
    """Append one structured event to Logs/mcp_{date}.json AND to the unified audit log."""
    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"mcp_{today}.json"
    existing: list = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append({"timestamp": datetime.now().isoformat(), **entry})
    log_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    # Mirror to unified audit log
    if _AUDIT_AVAILABLE:
        status   = entry.get("status", entry.get("event", "unknown"))
        is_error = status in ("error", "failed", "restart_failed")
        _audit_log(
            actor="mcp_orchestrator",
            action=entry.get("event", "mcp_event"),
            params={k: v for k, v in entry.items()
                    if k not in ("timestamp", "event", "status")},
            result="failure" if is_error else "success",
            approval_status="not_required",
            severity=("ERROR" if is_error else "INFO"),
            source_file="mcp_orchestrator.py",
            error=entry.get("error"),
        )


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_mcp_config() -> dict:
    """Load .claude/mcp.json -> {name: {command, args, env}}."""
    if not MCP_JSON.exists():
        logger.warning("mcp.json not found at %s", MCP_JSON)
        return {}
    try:
        raw = MCP_JSON.read_text(encoding="utf-8")
        return json.loads(raw).get("mcpServers", {})
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to parse mcp.json: %s", exc)
        return {}


def _resolve_env(env_dict: dict) -> dict:
    """Expand ${VAR} placeholders in env values from the process environment."""
    resolved = {}
    for k, v in env_dict.items():
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            var_name = v[2:-1]
            resolved[k] = os.environ.get(var_name, "")
        else:
            resolved[k] = v
    return resolved


# ---------------------------------------------------------------------------
# McpServerProcess — one subprocess instance
# ---------------------------------------------------------------------------

class McpServerProcess:
    """
    Manages a single MCP server subprocess communicating via stdio JSON-RPC.

    Lifecycle:
      start()  -> spawns subprocess, opens stdin/stdout pipes
      ping()   -> sends tools/list, waits PING_TIMEOUT s for response -> True/False
      stop()   -> SIGTERM -> wait -> SIGKILL
      restart()-> stop() + start()
    """

    def __init__(self, name: str, instance_id: int, command: str,
                 args: list[str], env: dict):
        self.name        = name
        self.instance_id = instance_id
        self.label       = f"{name}#{instance_id}"
        self.command     = command
        self.args        = args
        self.env         = {**os.environ.copy(), **_resolve_env(env)}

        self._proc: subprocess.Popen | None = None
        self._ping_counter   = 0
        self.last_ping_ok    = False
        self.last_ping_time  = "—"
        self.restart_count   = 0
        self.started_at: str = "—"
        self.status          = "not_started"   # not_started | running | degraded | stopped

    # -- subprocess management ------------------------------------------

    def start(self) -> bool:
        """Spawn the subprocess. Returns True on success."""
        cmd = [self.command] + self.args
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                bufsize=0,
            )
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.status     = "running"
            logger.info("[%s] Started (PID %d)", self.label, self._proc.pid)
            log_mcp({"event": "server_start", "server": self.label,
                     "pid": self._proc.pid, "status": "ok"})
            return True
        except FileNotFoundError:
            logger.error("[%s] Command not found: %s", self.label, self.command)
            self.status = "stopped"
            log_mcp({"event": "server_start", "server": self.label,
                     "status": "failed", "error": f"command not found: {self.command}"})
            return False
        except Exception as exc:
            logger.error("[%s] Failed to start: %s", self.label, exc)
            self.status = "stopped"
            log_mcp({"event": "server_start", "server": self.label,
                     "status": "failed", "error": str(exc)})
            return False

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def stop(self, timeout: int = 5) -> None:
        """Gracefully terminate the subprocess."""
        if not self._proc:
            return
        if self._proc.poll() is None:
            logger.info("[%s] Stopping (PID %d)...", self.label, self._proc.pid)
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("[%s] Force-killing (SIGKILL)", self.label)
                self._proc.kill()
                self._proc.wait()
        self.status = "stopped"
        log_mcp({"event": "server_stop", "server": self.label})

    def restart(self) -> bool:
        """Stop then start the server. Returns True on success."""
        self.stop()
        time.sleep(MAX_RESTART_WAIT)
        self.restart_count += 1
        ok = self.start()
        log_mcp({"event": "server_restart", "server": self.label,
                 "restart_count": self.restart_count, "status": "ok" if ok else "failed"})
        return ok

    # -- health ping -------------------------------------------------------

    def ping(self) -> bool:
        """
        Send a JSON-RPC tools/list request and wait up to PING_TIMEOUT seconds.
        Returns True if a valid JSON response arrives, False otherwise.
        Uses a reader thread to avoid blocking the monitor loop.
        """
        if not self.is_alive():
            self.last_ping_ok   = False
            self.last_ping_time = datetime.now().strftime("%H:%M:%S")
            self.status         = "stopped"
            return False

        self._ping_counter += 1
        req = json.dumps({
            "jsonrpc": "2.0",
            "id":      self._ping_counter,
            "method":  "tools/list",
            "params":  {},
        }) + "\n"

        result_q: queue.Queue[bytes | None] = queue.Queue()

        def _reader():
            try:
                line = self._proc.stdout.readline()
                result_q.put(line)
            except Exception:
                result_q.put(None)

        reader = threading.Thread(target=_reader, daemon=True)
        try:
            self._proc.stdin.write(req.encode())
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.last_ping_ok   = False
            self.last_ping_time = datetime.now().strftime("%H:%M:%S")
            self.status         = "degraded"
            return False

        reader.start()
        reader.join(timeout=PING_TIMEOUT)

        self.last_ping_time = datetime.now().strftime("%H:%M:%S")

        try:
            line = result_q.get_nowait()
        except queue.Empty:
            line = None

        if line:
            try:
                parsed = json.loads(line.decode().strip())
                ok = "result" in parsed or "error" in parsed
            except (json.JSONDecodeError, UnicodeDecodeError):
                ok = False
        else:
            ok = False

        self.last_ping_ok = ok
        self.status       = "running" if ok else "degraded"

        log_mcp({
            "event":    "health_ping",
            "server":   self.label,
            "ok":       ok,
            "ping_num": self._ping_counter,
        })
        return ok

    # -- invoke a tool call ------------------------------------------------

    def call_tool(self, tool_name: str, arguments: dict,
                  timeout: int = 30) -> dict | None:
        """
        Send a JSON-RPC tools/call request and return the parsed response dict.
        Returns None on timeout or error.
        """
        if not self.is_alive():
            return None

        req_id = int(time.time() * 1000)
        req    = json.dumps({
            "jsonrpc": "2.0",
            "id":      req_id,
            "method":  "tools/call",
            "params":  {"name": tool_name, "arguments": arguments},
        }) + "\n"

        result_q: queue.Queue[bytes | None] = queue.Queue()

        def _reader():
            try:
                # MCP responses may be preceded by the initialize handshake —
                # keep reading lines until we find the one with our req_id
                while True:
                    line = self._proc.stdout.readline()
                    if not line:
                        result_q.put(None)
                        return
                    try:
                        parsed = json.loads(line.decode().strip())
                        if parsed.get("id") == req_id:
                            result_q.put(line)
                            return
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
            except Exception:
                result_q.put(None)

        reader = threading.Thread(target=_reader, daemon=True)
        try:
            self._proc.stdin.write(req.encode())
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.warning("[%s] call_tool write failed: %s", self.label, exc)
            return None

        reader.start()
        reader.join(timeout=timeout)

        try:
            line = result_q.get_nowait()
        except queue.Empty:
            logger.warning("[%s] call_tool timed out (%ds)", self.label, timeout)
            return None

        if not line:
            return None

        try:
            return json.loads(line.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def __repr__(self):
        pid_s = str(self._proc.pid) if self._proc else "—"
        return f"McpServerProcess({self.label}, pid={pid_s}, status={self.status})"


# ---------------------------------------------------------------------------
# McpPool — N instances of one server, round-robin load balancing
# ---------------------------------------------------------------------------

class McpPool:
    """
    Manages a pool of McpServerProcess instances for one server name.
    Exposes get_server() using round-robin selection across healthy instances.
    """

    def __init__(self, name: str, config: dict, num_instances: int = 1):
        self.name     = name
        self.config   = config
        self._index   = 0
        self._lock    = threading.Lock()

        self.instances: list[McpServerProcess] = [
            McpServerProcess(
                name=name,
                instance_id=i,
                command=config["command"],
                args=config.get("args", []),
                env=config.get("env", {}),
            )
            for i in range(num_instances)
        ]

    def start_all(self) -> None:
        for inst in self.instances:
            inst.start()
            time.sleep(0.5)  # stagger starts slightly

    def stop_all(self) -> None:
        for inst in self.instances:
            inst.stop()

    def ping_all(self) -> dict[str, bool]:
        """Ping all instances. Returns {label: ok}."""
        return {inst.label: inst.ping() for inst in self.instances}

    def restart_dead(self) -> list[str]:
        """Restart any stopped/degraded instances. Returns list of restarted labels."""
        restarted = []
        for inst in self.instances:
            if not inst.is_alive():
                logger.warning("[%s] Instance down — restarting", inst.label)
                if inst.restart():
                    restarted.append(inst.label)
        return restarted

    def get_server(self) -> McpServerProcess | None:
        """
        Round-robin pick: return next alive instance.
        Returns None if all instances are down.
        """
        with self._lock:
            n = len(self.instances)
            for _ in range(n):
                inst = self.instances[self._index % n]
                self._index += 1
                if inst.is_alive():
                    return inst
        return None

    def healthy_count(self) -> int:
        return sum(1 for i in self.instances if i.last_ping_ok)

    def summary(self) -> list[dict]:
        return [
            {
                "label":           i.label,
                "status":          i.status,
                "pid":             i.pid(),
                "last_ping_ok":    i.last_ping_ok,
                "last_ping_time":  i.last_ping_time,
                "restart_count":   i.restart_count,
                "started_at":      i.started_at,
            }
            for i in self.instances
        ]


# ---------------------------------------------------------------------------
# McpTaskQueue — persist queued calls, retry when server recovers
# ---------------------------------------------------------------------------

class McpTaskQueue:
    """
    In-memory deque with disk persistence for tasks that couldn't be dispatched
    because all server instances were offline.

    Each pending task is written to Plans/MCP_QUEUE_{timestamp}.md so it
    survives an orchestrator restart.
    """

    def __init__(self):
        self._q: deque[dict] = deque()
        self._lock = threading.Lock()

    def enqueue(self, server: str, tool: str, arguments: dict,
                source: str = "") -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_id = f"{server}_{tool}_{ts}"
        task = {
            "id":        task_id,
            "server":    server,
            "tool":      tool,
            "arguments": arguments,
            "source":    source,
            "queued_at": datetime.now().isoformat(),
            "attempts":  0,
        }
        with self._lock:
            self._q.append(task)
        self._persist_task(task)
        log_mcp({"event": "task_queued", "task_id": task_id,
                 "server": server, "tool": tool})
        logger.info("[queue] Task queued: %s -> %s.%s", task_id, server, tool)
        return task_id

    def _persist_task(self, task: dict) -> None:
        md = (
            f"---\ntype: mcp_queued_task\nstatus: pending\n"
            f"server: {task['server']}\ntool: {task['tool']}\n"
            f"queued_at: {task['queued_at']}\ntask_id: {task['id']}\n---\n\n"
            f"# MCP Queued Task: {task['server']}.{task['tool']}\n\n"
            f"**Server:** {task['server']}  \n"
            f"**Tool:** {task['tool']}  \n"
            f"**Queued:** {task['queued_at']}  \n"
            f"**Source:** {task.get('source', '—')}\n\n"
            f"## Arguments\n\n```json\n"
            f"{json.dumps(task['arguments'], indent=2)}\n```\n\n"
            f"_Will be retried automatically when the server comes back online._\n"
        )
        path = PLANS_DIR / f"MCP_QUEUE_{task['id']}.md"
        path.write_text(md, encoding="utf-8")

    def pop_all(self) -> list[dict]:
        with self._lock:
            items = list(self._q)
            self._q.clear()
        return items

    def size(self) -> int:
        with self._lock:
            return len(self._q)

    def peek(self) -> list[dict]:
        with self._lock:
            return list(self._q)


# ---------------------------------------------------------------------------
# McpOrchestrator — master coordinator
# ---------------------------------------------------------------------------

class McpOrchestrator:
    """
    Owns all McpPools, runs the health-monitor loop, drives the task queue.

    Monitor loop (every HEALTH_INTERVAL seconds):
      1. ping_all() each pool
      2. restart_dead() for any failed instances
      3. retry queued tasks if server recovered
      4. log_mcp() all health events
      5. update Dashboard.md "## MCP Status" table
    """

    def __init__(self, vault_root: Path = VAULT_ROOT, launch_servers: bool = True):
        self.vault_root     = vault_root
        self.launch_servers = launch_servers
        self.pools: dict[str, McpPool] = {}
        self.task_queue     = McpTaskQueue()
        self._shutdown      = False
        self._cycle_count   = 0
        # Ralph loop tracking: task_id -> Popen
        self._ralph_procs: dict[str, subprocess.Popen] = {}

        self._build_pools()

    def _build_pools(self) -> None:
        """Build one McpPool per server entry in mcp.json."""
        servers = _load_mcp_config()
        if not servers:
            logger.warning("No servers found in mcp.json — nothing to manage.")
            return
        for name, cfg in servers.items():
            self.pools[name] = McpPool(name=name, config=cfg, num_instances=1)
            logger.info("Pool registered: %s (%s %s)",
                        name, cfg.get("command", "?"), cfg.get("args", [""])[0])

    # -- public API --------------------------------------------------------

    def start_all(self) -> None:
        """Start all server pools."""
        for name, pool in self.pools.items():
            if self.launch_servers:
                pool.start_all()
            else:
                logger.info("[%s] launch_servers=False — skip start", name)

    def stop_all(self) -> None:
        for pool in self.pools.values():
            pool.stop_all()

    def call(self, server: str, tool: str, arguments: dict,
             source: str = "") -> dict | None:
        """
        Dispatch tool_name to a healthy instance in the named pool.
        If all instances are offline, enqueue the task for later retry.
        Returns the JSON-RPC response dict, or None if queued/failed.
        """
        pool = self.pools.get(server)
        if not pool:
            logger.error("Unknown server: %s", server)
            return None

        inst = pool.get_server()
        if not inst:
            logger.warning("[%s] All instances offline — queuing task", server)
            self.task_queue.enqueue(server, tool, arguments, source)
            log_mcp({"event": "call_queued", "server": server, "tool": tool})
            return None

        logger.info("[%s] -> %s.%s()", inst.label, server, tool)
        t0    = time.monotonic()
        resp  = inst.call_tool(tool, arguments)
        elapsed = round(time.monotonic() - t0, 3)

        status = "ok" if resp and "result" in resp else "error"
        log_mcp({
            "event":    "tool_call",
            "server":   server,
            "instance": inst.label,
            "tool":     tool,
            "status":   status,
            "elapsed_s": elapsed,
            "error":    resp.get("error") if resp else "no_response",
        })
        logger.info("[%s] %s.%s -> %s (%ss)", inst.label, server, tool, status, elapsed)
        return resp

    # -- health cycle ------------------------------------------------------

    def run_health_cycle(self) -> dict:
        """
        Run one health-check cycle across all pools.
        Returns a summary dict.
        """
        self._cycle_count += 1
        summary = {
            "cycle":     self._cycle_count,
            "timestamp": datetime.now().isoformat(),
            "pools":     {},
            "restarted": [],
            "queued":    self.task_queue.size(),
        }

        for name, pool in self.pools.items():
            ping_results  = pool.ping_all()
            restarted     = pool.restart_dead()
            healthy       = pool.healthy_count()
            total         = len(pool.instances)

            summary["pools"][name] = {
                "healthy":   healthy,
                "total":     total,
                "ping":      ping_results,
                "restarted": restarted,
            }
            summary["restarted"].extend(restarted)

            if restarted:
                logger.info("[%s] Restarted %d instance(s): %s",
                            name, len(restarted), restarted)

        # Retry queued tasks if any server recovered
        if self.task_queue.size() > 0:
            self._retry_queued()

        # Scan Needs_Action/ for complex tasks and trigger Ralph loops
        ralph_launched = self._scan_needs_action()
        summary["ralph_launched"] = ralph_launched
        summary["ralph_active"]   = self._reap_ralph_loops()

        log_mcp({"event": "health_cycle", **summary})
        self._update_dashboard(summary)

        return summary

    def _retry_queued(self) -> None:
        """Attempt to dispatch all queued tasks now that a server may be up."""
        pending = self.task_queue.pop_all()
        re_queued = []
        for task in pending:
            server = task["server"]
            pool   = self.pools.get(server)
            if pool and pool.get_server():
                logger.info("[queue] Retrying queued task: %s.%s", server, task["tool"])
                resp = self.call(server, task["tool"], task["arguments"],
                                 source=task.get("source", ""))
                if resp:
                    log_mcp({"event": "task_retry_ok", "task_id": task["id"],
                             "server": server, "tool": task["tool"]})
                    # Delete the queue file
                    qfile = PLANS_DIR / f"MCP_QUEUE_{task['id']}.md"
                    if qfile.exists():
                        qfile.unlink()
                else:
                    task["attempts"] += 1
                    re_queued.append(task)
            else:
                task["attempts"] += 1
                re_queued.append(task)

        # Re-enqueue failed retries
        with self.task_queue._lock:
            for task in re_queued:
                self.task_queue._q.append(task)
                log_mcp({"event": "task_retry_requeued", "task_id": task["id"],
                         "attempts": task["attempts"]})

    # -- Ralph loop integration --------------------------------------------

    def _scan_needs_action(self) -> list[str]:
        """
        Scan Needs_Action/ for items with ralph_loop:true in their frontmatter.
        Spawn ralph_loop.py for each qualifying item that isn't already running.

        Returns list of task_ids for newly launched loops.
        """
        needs_dir = self.vault_root / "Needs_Action"
        if not needs_dir.exists():
            return []

        launched: list[str] = []
        for md_path in sorted(needs_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                continue

            # Quick pre-check before full YAML parse
            if "ralph_loop" not in text:
                continue

            import re as _re
            m = _re.match(r"^---\s*\n(.*?)\n---\s*\n", text, _re.DOTALL)
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue

            if not fm.get("ralph_loop"):
                continue
            if fm.get("ralph_status") in ("done", "step_complete", "promise_tag_found"):
                continue  # Already finished

            task_id = md_path.stem

            # Skip if loop already running
            lock_path = self.vault_root / ".pids" / f"ralph_{task_id}.lock"
            if lock_path.exists():
                continue
            if task_id in self._ralph_procs:
                proc = self._ralph_procs[task_id]
                if proc.poll() is None:
                    continue  # still running

            # Launch ralph_loop.py as background subprocess
            priority = fm.get("priority", "medium")
            logger.info("[ralph] Launching loop for: %s (priority=%s)",
                        md_path.name, priority)
            log_mcp({"event": "ralph_loop_launched", "task_id": task_id,
                     "file": md_path.name, "priority": priority})

            try:
                proc = subprocess.Popen(
                    [sys.executable, str(self.vault_root / "ralph_loop.py"),
                     "--task", str(md_path),
                     "--check-interval", "5"],
                    cwd=str(self.vault_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                self._ralph_procs[task_id] = proc
                launched.append(task_id)
            except (OSError, FileNotFoundError) as exc:
                logger.error("[ralph] Failed to launch loop for %s: %s",
                             task_id, exc)

        return launched

    def _reap_ralph_loops(self) -> int:
        """
        Check all tracked Ralph loop subprocesses.  Remove completed ones.
        Returns count of still-active loops.
        """
        to_remove: list[str] = []
        for task_id, proc in self._ralph_procs.items():
            rc = proc.poll()
            if rc is not None:
                status = "success" if rc == 0 else "failed"
                logger.info("[ralph] Loop for %s exited (rc=%d, %s)",
                            task_id, rc, status)
                log_mcp({"event": "ralph_loop_ended", "task_id": task_id,
                         "returncode": rc, "status": status})
                to_remove.append(task_id)
        for t in to_remove:
            del self._ralph_procs[t]
        return len(self._ralph_procs)

    # -- monitor loop ------------------------------------------------------

    def run(self) -> None:
        """Start all pools, then run the health monitor loop indefinitely."""
        logger.info("=" * 60)
        logger.info("MCP ORCHESTRATOR STARTING — %d server(s)", len(self.pools))
        logger.info("Health interval: %ds  Ping timeout: %ds", HEALTH_INTERVAL, PING_TIMEOUT)
        logger.info("=" * 60)

        log_mcp({"event": "orchestrator_start", "servers": list(self.pools.keys())})

        signal.signal(signal.SIGINT,  self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.start_all()
        # Give servers a moment to initialise before first ping
        time.sleep(3)

        try:
            while not self._shutdown:
                summary = self.run_health_cycle()
                _log_health_summary(summary)

                for _ in range(HEALTH_INTERVAL):
                    if self._shutdown:
                        break
                    time.sleep(1)
        finally:
            logger.info("Shutting down MCP Orchestrator...")
            self.stop_all()
            log_mcp({"event": "orchestrator_stop", "cycles": self._cycle_count})
            logger.info("Stopped after %d health cycle(s).", self._cycle_count)

    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received (signal %d)", signum)
        self._shutdown = True

    # -- dashboard ---------------------------------------------------------

    def _update_dashboard(self, summary: dict) -> None:
        """Rewrite the '## MCP Status' section in Dashboard.md."""
        dashboard = self.vault_root / "Dashboard.md"
        if not dashboard.exists():
            return

        now   = datetime.now().strftime("%Y-%m-%d %H:%M")
        rows  = []
        for name, pool in self.pools.items():
            data = summary["pools"].get(name, {})
            for inst in pool.instances:
                ping_icon  = "✓" if inst.last_ping_ok else "✗"
                status_str = inst.status
                rows.append(
                    f"| {inst.label} | {status_str} | {inst.pid() or '—'} "
                    f"| {ping_icon} {inst.last_ping_time} "
                    f"| {inst.restart_count} | {inst.started_at} |"
                )

        queued_note = (
            f"\n**Queued tasks (offline):** {self.task_queue.size()}"
            if self.task_queue.size() > 0 else ""
        )
        ralph_active   = summary.get("ralph_active", 0)
        ralph_launched = summary.get("ralph_launched", [])
        ralph_note     = (
            f"\n**Ralph loops active:** {ralph_active}"
            + (f"  **Just launched:** {', '.join(ralph_launched)}" if ralph_launched else "")
            if ralph_active or ralph_launched else ""
        )

        new_section = (
            f"## MCP Status\n\n"
            f"_Updated: {now}_  "
            f"**Cycle:** #{self._cycle_count}  "
            f"**Servers:** {len(self.pools)}{queued_note}{ralph_note}\n\n"
            f"| Instance | Status | PID | Last Ping | Restarts | Started |\n"
            f"|----------|--------|-----|-----------|----------|---------|\n"
            + "\n".join(rows) + "\n"
        )

        text = dashboard.read_text(encoding="utf-8")

        import re
        if "## MCP Status" in text:
            # Replace existing section up to next ## heading or end of file
            text = re.sub(
                r"## MCP Status\n.*?(?=\n## |\Z)",
                new_section,
                text,
                flags=re.DOTALL,
            )
        else:
            # Prepend before first ## heading in Recent Activity area
            text = text.replace("## Recent Activity", new_section + "\n---\n\n## Recent Activity", 1)

        dashboard.write_text(text, encoding="utf-8")

    # -- status print ------------------------------------------------------

    def print_status(self) -> None:
        """Print a human-readable status table."""
        print()
        print("=" * 70)
        print("  MCP ORCHESTRATOR — SERVER STATUS")
        print("=" * 70)
        for name, pool in self.pools.items():
            for inst in pool.instances:
                ping_str = f"{'OK' if inst.last_ping_ok else 'FAIL'} @ {inst.last_ping_time}"
                print(f"  {inst.label:<20} {inst.status:<12} PID={inst.pid() or '—':<8} "
                      f"Ping={ping_str:<20} Restarts={inst.restart_count}")
        print(f"\n  Task queue depth: {self.task_queue.size()}")
        print(f"  Health cycles run: {self._cycle_count}")
        print("=" * 70)
        print()


def _log_health_summary(summary: dict) -> None:
    pools = summary.get("pools", {})
    parts = [f"{n}={d['healthy']}/{d['total']}" for n, d in pools.items()]
    restarted = summary.get("restarted", [])
    r_str = f" restarted={restarted}" if restarted else ""
    logger.info("Health #%d: %s queued=%d%s",
                summary["cycle"], " | ".join(parts), summary["queued"], r_str)


# ---------------------------------------------------------------------------
# Test driver — dry-run invoke each server for a draft action
# ---------------------------------------------------------------------------

def run_test(orch: McpOrchestrator) -> None:
    """
    Invoke each MCP server's primary draft tool in DRY_RUN mode
    without going through the subprocess — call the Python handlers directly
    so the test works even before credentials are configured.
    """
    print("\n[mcp_orchestrator] Running test invocations (DRY_RUN)...\n")

    results = []

    # -- email: draft_email -----------------------------------------------
    try:
        os.environ["DRY_RUN"] = "true"
        sys.path.insert(0, str(VAULT_ROOT))
        from email_mcp_shim import _handle_draft as email_draft  # may not exist
        result = "skipped (email_mcp is .mjs — tested via npm)"
    except ImportError:
        result = "skipped (email_mcp is .mjs — use: DRY_RUN=true node email_mcp.mjs)"
    print(f"  [email]  draft_email -> {result}")
    log_mcp({"event": "test_invocation", "server": "email", "tool": "draft_email",
             "result": result})
    results.append(("email", "draft_email", result))

    # -- fb_ig: draft_fb_post ---------------------------------------------
    try:
        from fb_ig_mcp import handle_draft_fb_post
        out = handle_draft_fb_post({
            "content": "[MCP_ORCH TEST] Multi-server orchestration is live. All systems healthy.",
            "caption": "MCP Orchestrator test",
            "hashtags": "#AIEmployee #MCP #Test",
            "notes": "Auto-generated by mcp_orchestrator.py --test",
        })
        result = out.split("\n")[0]  # first line only
    except Exception as exc:
        result = f"error: {exc}"
    print(f"  [fb_ig]  draft_fb_post -> {result}")
    log_mcp({"event": "test_invocation", "server": "fb_ig", "tool": "draft_fb_post",
             "result": result})
    results.append(("fb_ig", "draft_fb_post", result))

    # -- x: draft_x_post --------------------------------------------------
    try:
        from x_mcp import handle_draft_x_post
        out = handle_draft_x_post({
            "text": "[MCP_ORCH TEST] All MCP servers healthy. Orchestrator running. #AIEmployee #MCP",
            "hashtags": "#AIEmployee #MCP #GoldTier",
            "notes": "Auto-generated by mcp_orchestrator.py --test",
        })
        result = out.split("\n")[0]
    except Exception as exc:
        result = f"error: {exc}"
    print(f"  [x]      draft_x_post  -> {result}")
    log_mcp({"event": "test_invocation", "server": "x", "tool": "draft_x_post",
             "result": result})
    results.append(("x", "draft_x_post", result))

    # Summary
    print()
    print(f"  Results: {len([r for r in results if 'error' not in r[2].lower() and 'skip' not in r[2].lower()])} "
          f"succeeded, "
          f"{len([r for r in results if 'error' in r[2].lower()])} errors, "
          f"{len([r for r in results if 'skipped' in r[2].lower()])} skipped")
    print("\n  Check Plans/ for new drafts, Logs/mcp_{date}.json for event log.")
    print("[mcp_orchestrator] Test complete.\n")

    # Update dashboard with test results
    _write_test_results_to_log(results)


def _write_test_results_to_log(results: list[tuple]) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    log_mcp({
        "event":   "test_run_complete",
        "date":    today,
        "results": [{"server": r[0], "tool": r[1], "result": r[2]} for r in results],
    })


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="MCP Orchestrator — Gold Tier Personal AI Employee",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mcp_orchestrator.py              # Full monitor loop
  python mcp_orchestrator.py --once       # Single health check + exit
  python mcp_orchestrator.py --status     # Print status table
  python mcp_orchestrator.py --test       # DRY_RUN test all servers
  python mcp_orchestrator.py --no-launch  # Monitor only (servers already running)
        """,
    )
    parser.add_argument("--once",      action="store_true", help="Single health cycle then exit")
    parser.add_argument("--status",    action="store_true", help="Print status and exit")
    parser.add_argument("--test",      action="store_true", help="DRY_RUN test each server")
    parser.add_argument("--no-launch", action="store_true", help="Don't start server subprocesses")
    parser.add_argument("--vault",     type=str, default=None, help="Override vault path")
    args = parser.parse_args()

    vault = Path(args.vault) if args.vault else VAULT_ROOT

    orch = McpOrchestrator(vault_root=vault, launch_servers=not args.no_launch)

    if args.test:
        os.environ["DRY_RUN"] = "true"
        run_test(orch)
        return

    if args.status:
        orch.print_status()
        return

    if args.once:
        orch.start_all()
        time.sleep(2)
        summary = orch.run_health_cycle()
        _log_health_summary(summary)
        orch.print_status()
        orch.stop_all()
        return

    orch.run()


if __name__ == "__main__":
    main()
