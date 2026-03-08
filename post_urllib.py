#!/usr/bin/env python3
"""
Post education content using Python http.client directly.
Bypasses mcp-client.py timeout and subprocess curl issues.
Fresh MCP session per tool call, shared browser context preserves login.
"""
import http.client, json, time, sys

MCP_HOST = "localhost"
MCP_PORT = 8808

FB_TEXT = (
    "Education is not an event you attend. It is a practice you build.\n\n"
    "The professionals winning in 2026 show up consistently, ask better questions, "
    "and turn every experience into a lesson.\n\n"
    "What is one thing you taught yourself outside school that changed your life? "
    "Share in the comments.\n\n"
    "#Education #LifelongLearning #GrowthMindset #Learning #FutureOfLearning"
)

X_TEXT = (
    "Education is not an event you attend. It is a practice you build.\n\n"
    "Pros winning in 2026 show up consistently and turn every experience into a lesson.\n\n"
    "What did you teach yourself outside school that changed your life?\n\n"
    "#Education #LifelongLearning #GrowthMindset"
)

LI_TEXT = (
    "Education is not an event you attend. It is a practice you build.\n\n"
    "The professionals I see thriving in 2026 share one habit: they never stop learning. "
    "Not in classrooms - in every meeting, every setback, every conversation.\n\n"
    "They ask better questions. They reflect on what went wrong. "
    "They turn every experience into a lesson.\n\n"
    "What is the one thing you taught yourself outside any school or course "
    "that changed your career or life? Drop it in the comments.\n\n"
    "#Education #LifelongLearning #ProfessionalDevelopment #GrowthMindset #Leadership"
)


def mcp_call(tool: str, args: dict, timeout: int = 45) -> str:
    """Make a fresh MCP session, call one tool, return result text."""
    try:
        conn = http.client.HTTPConnection(MCP_HOST, MCP_PORT, timeout=timeout + 5)

        # Step 1: Initialize
        init_body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "poster", "version": "1"}}
        }).encode()
        conn.request("POST", "/mcp", body=init_body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        })
        resp = conn.getresponse()
        sid = resp.getheader("Mcp-Session-Id") or resp.getheader("mcp-session-id") or ""
        resp.read()  # consume body

        if not sid:
            conn.close()
            return "ERR:no_session"

        # Step 2: Initialized notification
        notif_body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
        conn.request("POST", "/mcp", body=notif_body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": sid
        })
        resp2 = conn.getresponse()
        resp2.read()
        time.sleep(0.2)

        # Step 3: Tool call
        tool_body = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": args}
        }).encode()
        conn.request("POST", "/mcp", body=tool_body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": sid
        })
        resp3 = conn.getresponse()

        # Read chunked SSE response
        data = b""
        while True:
            chunk = resp3.read(4096)
            if not chunk:
                break
            data += chunk
        conn.close()

    except Exception as e:
        return f"ERR:conn:{e}"

    raw = data.decode("utf-8", "replace")

    # Parse SSE: find "data:" line
    for line in raw.split("\n"):
        if line.startswith("data:"):
            try:
                d = json.loads(line[5:].strip())
                content = d.get("result", {}).get("content", [])
                full = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
                # Return first non-header/non-code/non-list line (the actual result)
                for l in full.split("\n"):
                    s = l.strip()
                    if s and not s.startswith("#") and not s.startswith("```") and not s.startswith("-"):
                        return s.strip('"')
                return full[:200] if full else "(empty)"
            except Exception:
                pass
    return f"ERR:no_data:{raw[:80]}"


def ev(js: str, timeout: int = 45) -> str:
    return mcp_call("browser_evaluate", {"function": js}, timeout=timeout)


def wait(s: int):
    print(f"  ... {s}s")
    time.sleep(s)


def nav(url: str, timeout: int = 80):
    """Navigate browser to URL using browser_navigate tool. Waits for page load."""
    result = mcp_call("browser_navigate", {"url": url}, timeout=timeout)
    print(f"  nav result: {result[:80]}")
    return result


# ── FACEBOOK ──────────────────────────────────────────────────────────────────
def post_facebook():
    print("\n=== FACEBOOK ===")

    title = ev("() => document.title")
    print(f"  page: {title[:70]}")

    if "facebook" not in title.lower():
        print("  navigating to Facebook...")
        nav("https://www.facebook.com")
        title = ev("() => document.title")
        print(f"  page after nav: {title[:70]}")

    if "facebook" not in title.lower():
        print("  [FB] Not on Facebook — may need login")
        return False

    # 1. Click composer ("What's on your mind")
    print("  [1] Click composer...")
    r1 = ev(
        '() => {'
        '  const b = Array.from(document.querySelectorAll("[role=button]"))'
        '    .find(b => b.innerText && b.innerText.toLowerCase().includes("mind"));'
        '  if (!b) return "not found n=" + document.querySelectorAll("[role=button]").length;'
        '  b.click(); return "clicked: " + b.innerText.substring(0,40);'
        '}'
    )
    print(f"  -> {r1[:80]}")

    if "not found" in r1:
        print("  scrolling to top and retrying...")
        ev('() => { window.scrollTo(0,0); return "scrolled"; }')
        wait(2)
        r1 = ev(
            '() => {'
            '  const b = Array.from(document.querySelectorAll("[role=button]"))'
            '    .find(b => b.innerText && b.innerText.toLowerCase().includes("mind"));'
            '  if (!b) return "still not found";'
            '  b.click(); return "clicked: " + b.innerText.substring(0,40);'
            '}'
        )
        print(f"  -> retry: {r1[:80]}")

    if "not found" in r1 or "ERR" in r1:
        print("  [FB] Composer not found")
        return False

    wait(4)

    # 2. Type text
    print("  [2] Typing text...")
    txt = json.dumps(FB_TEXT)
    r2 = ev(
        f'() => {{'
        f'  const e = document.querySelector("[contenteditable=true][role=textbox]") ||'
        f'             document.querySelector("[contenteditable=true]");'
        f'  if (!e) return "no editor";'
        f'  e.focus();'
        f'  document.execCommand("selectAll", false, null);'
        f'  const ok = document.execCommand("insertText", false, {txt});'
        f'  return ok ? "typed len=" + e.innerText.length : "execCmd failed len=" + e.innerText.length;'
        f'}}'
    )
    print(f"  -> {r2[:80]}")

    if "no editor" in r2:
        print("  [FB] Editor not found after click")
        return False

    wait(2)

    # 3. Click Post
    print("  [3] Clicking Post...")
    r3 = ev(
        '() => {'
        '  const btns = Array.from(document.querySelectorAll("[aria-label=Post]"));'
        '  if (btns.length) { btns[btns.length-1].click(); return "Post clicked n=" + btns.length; }'
        '  const p = Array.from(document.querySelectorAll("[role=button]"))'
        '    .find(b => b.innerText && b.innerText.trim() === "Post");'
        '  if (p) { p.click(); return "Post by text"; }'
        '  return "no Post btn. btns: " + Array.from(document.querySelectorAll("[role=button]"))'
        '    .slice(0,8).map(b=>b.innerText.substring(0,15)).join("|");'
        '}'
    )
    print(f"  -> {r3[:100]}")
    wait(4)

    final = ev("() => document.title")
    print(f"  final page: {final[:60]}")
    ok = "clicked" in r3.lower()
    print(f"  [FB] {'SUCCESS' if ok else 'CHECK RESULT'}")
    return ok


# ── X / TWITTER ───────────────────────────────────────────────────────────────
def post_x():
    print("\n=== X (TWITTER) ===")
    nav("https://x.com/home")
    title = ev("() => document.title")
    print(f"  page: {title[:60]}")

    if any(k in title.lower() for k in ["log in", "login", "sign in"]):
        print(f"  [X] Need to log in to X first — page: {title[:60]}")
        return False

    # Click compose
    r1 = ev(
        '() => {'
        '  const b = document.querySelector("[data-testid=SideNav_NewTweet_Button]") ||'
        '             document.querySelector("a[href=\\"/compose/tweet\\"]");'
        '  if (b) { b.click(); return "compose clicked"; }'
        '  return "no compose btn";'
        '}'
    )
    print(f"  compose: {r1[:60]}")
    wait(2)

    # Type
    txt = json.dumps(X_TEXT)
    r2 = ev(
        f'() => {{'
        f'  const e = document.querySelector("[data-testid=tweetTextarea_0]") ||'
        f'             document.querySelector("[role=textbox]");'
        f'  if (!e) return "no editor";'
        f'  e.focus();'
        f'  document.execCommand("insertText", false, {txt});'
        f'  return "typed len=" + (e.innerText||"").length;'
        f'}}'
    )
    print(f"  type: {r2[:60]}")
    wait(1)

    # Post
    r3 = ev(
        '() => {'
        '  const b = document.querySelector("[data-testid=tweetButtonInline]") ||'
        '             document.querySelector("[data-testid=tweetButton]");'
        '  if (b) { b.click(); return "tweeted"; }'
        '  return "no tweet btn";'
        '}'
    )
    print(f"  post: {r3[:60]}")
    wait(3)
    ok = "tweeted" in r3
    print(f"  [X] {'SUCCESS' if ok else 'CHECK RESULT'}")
    return ok


# ── LINKEDIN ──────────────────────────────────────────────────────────────────
def post_linkedin():
    print("\n=== LINKEDIN ===")
    nav("https://www.linkedin.com/feed/")
    title = ev("() => document.title")
    print(f"  page: {title[:60]}")

    if "linkedin" not in title.lower() or "login" in title.lower() or "sign in" in title.lower():
        print("  [LI] Need to log in to LinkedIn first — skipping")
        return False

    # Click Start a post
    r1 = ev(
        '() => {'
        '  const b = document.querySelector(".share-box-feed-entry__trigger") ||'
        '    Array.from(document.querySelectorAll("button"))'
        '      .find(b => b.innerText && b.innerText.toLowerCase().includes("start a post"));'
        '  if (b) { b.click(); return "start clicked"; }'
        '  return "start not found";'
        '}'
    )
    print(f"  start: {r1[:60]}")
    wait(3)

    # Type
    txt = json.dumps(LI_TEXT)
    r2 = ev(
        f'() => {{'
        f'  const e = document.querySelector(".ql-editor") ||'
        f'             document.querySelector("[contenteditable=true]") ||'
        f'             document.querySelector("[role=textbox]");'
        f'  if (!e) return "no editor";'
        f'  e.focus();'
        f'  document.execCommand("insertText", false, {txt});'
        f'  return "typed len=" + (e.innerText||"").length;'
        f'}}'
    )
    print(f"  type: {r2[:60]}")
    wait(1)

    # Post
    r3 = ev(
        '() => {'
        '  const b = document.querySelector(".share-actions__primary-action") ||'
        '    Array.from(document.querySelectorAll("button"))'
        '      .find(b => b.innerText && b.innerText.trim() === "Post");'
        '  if (b) { b.click(); return "posted"; }'
        '  return "no post btn";'
        '}'
    )
    print(f"  post: {r3[:60]}")
    wait(3)
    ok = "posted" in r3
    print(f"  [LI] {'SUCCESS' if ok else 'CHECK RESULT'}")
    return ok


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== EDUCATION POST - ALL PLATFORMS ===\n")

    # Verify server
    title = ev("() => document.title")
    print(f"Server OK. Current page: {title[:70]}\n")
    if title.startswith("ERR:"):
        print("FATAL: MCP server not responding")
        sys.exit(1)

    results = {}
    results["Facebook"] = post_facebook()
    results["X/Twitter"] = post_x()
    results["LinkedIn"] = post_linkedin()

    print("\n=== RESULTS ===")
    for p, ok in results.items():
        status = "POSTED" if ok else "CHECK BROWSER / NEEDS LOGIN"
        print(f"  {p}: {status}")
    print()
