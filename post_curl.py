#!/usr/bin/env python3
"""
Post education content using curl directly (bypasses mcp-client.py timeout issues).
One new session per call. Shared browser context preserves state.
"""
import json, subprocess, sys, time, re

MCP = "http://localhost:8808/mcp"

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


def curl_call(tool: str, args: dict, timeout: int = 25) -> str:
    """
    Create a fresh MCP session and call one tool.
    Returns the result text or error string.
    """
    # Step 1: Initialize (get session ID)
    init_payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "poster", "version": "1"}}
    })
    init_cmd = ["curl", "-si", "--max-time", "15",
                "-X", "POST", MCP,
                "-H", "Content-Type: application/json",
                "-H", "Accept: application/json, text/event-stream",
                "-d", init_payload]
    try:
        init_r = subprocess.run(init_cmd, capture_output=True, timeout=20)
        init_out = init_r.stdout.decode("utf-8", "replace")
        sid = ""
        for line in init_out.split("\n"):
            if "mcp-session-id" in line.lower():
                sid = line.split(":", 1)[1].strip()
                break
        if not sid:
            return "ERR:no_session"
    except Exception as e:
        return f"ERR:init:{e}"

    # Step 2: Send initialized notification (fire and forget)
    notif_payload = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    notif_cmd = ["curl", "-sN", "--max-time", "5",
                 "-X", "POST", MCP,
                 "-H", "Content-Type: application/json",
                 "-H", "Accept: application/json, text/event-stream",
                 "-H", f"Mcp-Session-Id: {sid}",
                 "-d", notif_payload]
    subprocess.Popen(notif_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)

    # Step 3: Call tool
    tool_payload = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool, "arguments": args}
    })
    tool_cmd = ["curl", "-N", "--max-time", str(timeout),
                "-X", "POST", MCP,
                "-H", "Content-Type: application/json",
                "-H", "Accept: application/json, text/event-stream",
                "-H", f"Mcp-Session-Id: {sid}",
                "-d", tool_payload]
    try:
        tool_r = subprocess.run(tool_cmd, capture_output=True, timeout=timeout + 10)
        out = tool_r.stdout.decode("utf-8", "replace")
        # Parse SSE data
        for line in out.split("\n"):
            if line.startswith("data:"):
                try:
                    d = json.loads(line[5:].strip())
                    content = d.get("result", {}).get("content", [])
                    full = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
                    # Extract first non-header line (the actual result)
                    for l in full.split("\n"):
                        s = l.strip()
                        if s and not s.startswith("#") and not s.startswith("```") and not s.startswith("-"):
                            return s.strip('"')
                    return full[:100] if full else "(empty)"
                except Exception:
                    pass
        return f"ERR:no_data:{out[:80]}"
    except Exception as e:
        return f"ERR:tool:{e}"


def ev(js: str, timeout: int = 25) -> str:
    return curl_call("browser_evaluate", {"function": js}, timeout=timeout)


def wait(s: int):
    print(f"  ... {s}s")
    time.sleep(s)


def nav(url: str):
    """Navigate async - response expected to fail as page changes."""
    js = f'() => {{ window.location.replace("{url}"); return "nav"; }}'
    # Use very short timeout - we don't need the response
    try:
        curl_call("browser_evaluate", {"function": js}, timeout=8)
    except Exception:
        pass


# ── FACEBOOK ──────────────────────────────────────────────────────────────────
def post_facebook():
    print("\n=== FACEBOOK ===")
    title = ev("() => document.title")
    print(f"  page: {title[:60]}")

    if "facebook" not in title.lower():
        print("  navigating to Facebook...")
        nav("https://www.facebook.com")
        wait(25)
        title = ev("() => document.title")
        print(f"  page: {title[:60]}")

    # 1. Click composer
    print("  [1] Click composer button...")
    r1 = ev('() => { const b=Array.from(document.querySelectorAll("[role=button]")).find(b=>b.innerText&&b.innerText.includes("mind")); if(!b) return "not found"; b.click(); return "clicked:"+b.innerText.substring(0,30); }')
    print(f"  -> {r1[:80]}")

    if "not found" in r1:
        # Scroll up first and try again
        ev('() => { window.scrollTo(0,0); return "scrolled"; }')
        wait(2)
        r1b = ev('() => { const b=Array.from(document.querySelectorAll("[role=button]")).find(b=>b.innerText&&b.innerText.includes("mind")); if(!b) return "not found after scroll"; b.click(); return "clicked:"+b.innerText.substring(0,30); }')
        print(f"  -> retry: {r1b[:80]}")
    wait(4)

    # 2. Type text
    print("  [2] Type post text...")
    txt = json.dumps(FB_TEXT)
    r2 = ev(f'() => {{ const e=document.querySelector("[contenteditable=true][role=textbox]")||document.querySelector("[contenteditable=true]"); if(!e) return "no editor"; e.focus(); document.execCommand("selectAll",false,null); const ok=document.execCommand("insertText",false,{txt}); return ok?"typed:"+e.innerText.length:"fallback:"+e.innerText.length; }}')
    print(f"  -> {r2[:80]}")

    if "no editor" in r2:
        print("  WARNING: editor not found after click")
        return False
    wait(2)

    # 3. Click Post
    print("  [3] Click Post...")
    r3 = ev('() => { const b=Array.from(document.querySelectorAll("[aria-label=Post]")); if(b.length){b[b.length-1].click();return "Post clicked n="+b.length;} const p=Array.from(document.querySelectorAll("[role=button]")).find(b=>b.innerText&&b.innerText.trim()==="Post"); if(p){p.click();return "Post by text";} return "no Post btn: "+Array.from(document.querySelectorAll("[role=button]")).slice(0,5).map(b=>b.innerText.substring(0,12)).join("|"); }')
    print(f"  -> {r3[:100]}")
    wait(4)

    final = ev("() => document.title")
    print(f"  final: {final[:60]}")
    ok = "clicked" in r3.lower()
    print(f"  [FB] {'SUCCESS' if ok else 'CHECK RESULT'}")
    return ok


# ── X / TWITTER ───────────────────────────────────────────────────────────────
def post_x():
    print("\n=== X (TWITTER) ===")
    nav("https://x.com/home")
    wait(20)
    title = ev("() => document.title")
    print(f"  page: {title[:60]}")

    if not any(k in title.lower() for k in ["home", "x.com", "twitter"]):
        print("  [X] Need login")
        return False

    r1 = ev('() => { const b=document.querySelector("[data-testid=SideNav_NewTweet_Button]")||document.querySelector("a[href=\\"compose/tweet\\"]"); if(b){b.click();return "compose clicked";} return "no compose"; }')
    print(f"  compose: {r1[:60]}")
    wait(2)

    txt = json.dumps(X_TEXT)
    r2 = ev(f'() => {{ const e=document.querySelector("[data-testid=tweetTextarea_0]")||document.querySelector("[role=textbox]"); if(!e) return "no editor"; e.focus(); document.execCommand("insertText",false,{txt}); return "typed:"+(e.innerText||"").length; }}')
    print(f"  type: {r2[:60]}")
    wait(1)

    r3 = ev('() => { const b=document.querySelector("[data-testid=tweetButtonInline]")||document.querySelector("[data-testid=tweetButton]"); if(b){b.click();return "tweeted";} return "no btn"; }')
    print(f"  post: {r3[:60]}")
    wait(3)
    ok = "tweeted" in r3
    print(f"  [X] {'SUCCESS' if ok else 'CHECK RESULT'}")
    return ok


# ── LINKEDIN ──────────────────────────────────────────────────────────────────
def post_linkedin():
    print("\n=== LINKEDIN ===")
    nav("https://www.linkedin.com/feed/")
    wait(20)
    title = ev("() => document.title")
    print(f"  page: {title[:60]}")

    if "login" in title.lower() or "linkedin" not in title.lower():
        print("  [LI] Need login")
        return False

    r1 = ev('() => { const b=document.querySelector(".share-box-feed-entry__trigger")||Array.from(document.querySelectorAll("button")).find(b=>b.innerText&&b.innerText.toLowerCase().includes("start a post")); if(b){b.click();return "start clicked";} return "not found"; }')
    print(f"  start: {r1[:60]}")
    wait(3)

    txt = json.dumps(LI_TEXT)
    r2 = ev(f'() => {{ const e=document.querySelector(".ql-editor")||document.querySelector("[contenteditable=true]")||document.querySelector("[role=textbox]"); if(!e) return "no editor"; e.focus(); document.execCommand("insertText",false,{txt}); return "typed:"+(e.innerText||"").length; }}')
    print(f"  type: {r2[:60]}")
    wait(1)

    r3 = ev('() => { const b=document.querySelector(".share-actions__primary-action")||Array.from(document.querySelectorAll("button")).find(b=>b.innerText&&b.innerText.trim()==="Post"); if(b){b.click();return "posted";} return "no btn"; }')
    print(f"  post: {r3[:60]}")
    wait(3)
    ok = "posted" in r3
    print(f"  [LI] {'SUCCESS' if ok else 'CHECK RESULT'}")
    return ok


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== EDUCATION POST - ALL PLATFORMS ===\n")
    title = ev("() => document.title")
    print(f"Server OK. Page: {title[:60]}")

    results = {}
    results["Facebook"] = post_facebook()
    results["X/Twitter"] = post_x()
    results["LinkedIn"] = post_linkedin()

    print("\n=== RESULTS ===")
    for p, ok in results.items():
        print(f"  {p}: {'POSTED' if ok else 'CHECK BROWSER'}")
    print()
