#!/usr/bin/env python3
"""
Post to Instagram, X, LinkedIn using Playwright MCP.
Facebook is already done. This handles the remaining 3 platforms.
Uses http.client directly (reads chunked SSE correctly).
"""
import http.client, json, time, sys, os

MCP_HOST = "localhost"
MCP_PORT = 8808
IG_IMAGE  = "D:/Hackathon-0/ig_post.png"

IG_CAPTION = (
    "\U0001f4da The best investment you will ever make is in your own education.\n\n"
    "Every book you read, every course you take, every question you ask "
    "\u2014 it compounds over time.\n\n"
    "Small steps forward every single day lead to results that seem impossible at the start.\n\n"
    "What are you learning right now? Tell us below \U0001f447\n\n"
    "#Education #Learning #StudyMotivation #GrowthMindset #LearnEveryday "
    "#KnowledgeIsPower #LifelongLearning #NeverStopLearning #OnlineLearning #Curiosity"
)

X_TEXT = (
    "The most powerful thing you can do today: learn one new thing you didn't know yesterday.\n\n"
    "Education compounds. Every day. No exceptions.\n\n"
    "What did you learn this week?\n\n"
    "#Education #Learning #GrowthMindset"
)

LI_TEXT = (
    "Education is the highest-ROI investment any individual or organisation can make.\n\n"
    "Here is what consistent learning has taught me:\n\n"
    "1. The best learners aren't the smartest \u2014 they're the most consistent.\n"
    "Small daily inputs beat weekend cramming every single time. "
    "15 minutes a day adds up to 91 hours a year.\n\n"
    "2. Teaching others is the fastest way to master a skill yourself.\n"
    "If you can't explain it simply, you don't understand it yet. Find someone to teach.\n\n"
    "3. Curiosity is a competitive advantage.\n"
    "In a world where information is free, the people who ask better questions win.\n\n"
    "4. The most in-demand skill in 2026?\n"
    "The ability to learn fast and apply faster. Not a degree \u2014 a habit.\n\n"
    "Whether you're building a team or building yourself: invest in education first. "
    "Everything else follows.\n\n"
    "---\n"
    "What's one learning habit that has made the biggest difference in your career?\n\n"
    "#Education #ProfessionalDevelopment #Leadership #GrowthMindset #Career "
    "#LifelongLearning #PersonalGrowth"
)


# ── MCP helper ─────────────────────────────────────────────────────────────────

def _call(tool: str, args: dict, timeout: int = 40) -> dict:
    """Fresh MCP session → call one tool → return raw result dict."""
    try:
        conn = http.client.HTTPConnection(MCP_HOST, MCP_PORT, timeout=timeout + 5)

        # Init
        conn.request("POST", "/mcp",
            json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                        "params":{"protocolVersion":"2024-11-05","capabilities":{},
                                  "clientInfo":{"name":"poster","version":"1"}}}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
        r = conn.getresponse()
        sid = r.getheader("Mcp-Session-Id") or r.getheader("mcp-session-id") or ""
        r.read()
        if not sid:
            conn.close(); return {"_err": "no_session"}

        # Notif
        conn.request("POST", "/mcp",
            json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream",
             "Mcp-Session-Id": sid})
        conn.getresponse().read()
        time.sleep(0.2)

        # Tool call
        conn.request("POST", "/mcp",
            json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call",
                        "params":{"name":tool,"arguments":args}}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream",
             "Mcp-Session-Id": sid})
        r3 = conn.getresponse()
        data = b""
        while True:
            chunk = r3.read(4096)
            if not chunk: break
            data += chunk
        conn.close()
    except Exception as e:
        return {"_err": str(e)[:80]}

    raw = data.decode("utf-8", "replace")
    for line in raw.split("\n"):
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except Exception:
                pass
    return {"_err": f"no_data len={len(raw)}"}


def ev(js: str, timeout: int = 40) -> str:
    """Evaluate JS and return the first result line."""
    d = _call("browser_evaluate", {"function": js}, timeout=timeout)
    if "_err" in d:
        return "ERR:" + d["_err"]
    content = d.get("result", {}).get("content", [])
    full = "\n".join(c.get("text","") for c in content if c.get("type")=="text")
    for line in full.split("\n"):
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("```") and not s.startswith("-"):
            return s.strip('"')
    return "(empty)"


def ev_retry(js: str, max_tries: int = 6, wait: int = 20, timeout: int = 40) -> str:
    """Evaluate with retry — for calls that might hit a loading page."""
    for i in range(max_tries):
        r = ev(js, timeout=timeout)
        if not r.startswith("ERR:") and r != "(empty)":
            return r
        if i < max_tries - 1:
            print(f"    (retry {i+1}/{max_tries} in {wait}s — got: {r[:50]})")
            time.sleep(wait)
    return r


def tool(name: str, args: dict, timeout: int = 30) -> str:
    """Call any MCP tool and return first result line."""
    d = _call(name, args, timeout=timeout)
    if "_err" in d:
        return "ERR:" + d["_err"]
    content = d.get("result", {}).get("content", [])
    full = "\n".join(c.get("text","") for c in content if c.get("type")=="text")
    for line in full.split("\n"):
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("```") and not s.startswith("-"):
            return s.strip('"')
    return "(ok)"


def nav(url: str):
    """Navigate browser using window.location.replace (non-blocking evaluate)."""
    safe_url = url.replace('"', '%22')
    # This triggers navigation; response may be empty — that is expected
    _call("browser_evaluate",
          {"function": f'() => {{ window.location.replace("{safe_url}"); return "nav"; }}'},
          timeout=8)
    print(f"  nav triggered -> {url}")


def wait(s: int, msg: str = ""):
    print(f"  waiting {s}s {msg}")
    time.sleep(s)


# ── INSTAGRAM ─────────────────────────────────────────────────────────────────

def post_instagram():
    print("\n" + "="*50)
    print("INSTAGRAM")
    print("="*50)

    if not os.path.exists(IG_IMAGE):
        print("  ERROR: image not found at", IG_IMAGE)
        return False

    # Navigate to Instagram
    cur = ev("() => document.title", timeout=15)
    print(f"  current page: {cur[:60]}")
    if "instagram" not in cur.lower():
        nav("https://www.instagram.com/")
        wait(70, "(loading Instagram...)")

    title = ev_retry("() => document.title", max_tries=5, wait=20)
    print(f"  page: {title[:60]}")

    if any(w in title.lower() for w in ["log in","login","sign in","accounts"]):
        print("  => Instagram not logged in. Please log in to instagram.com in the browser first.")
        return False

    if "instagram" not in title.lower():
        print(f"  => Could not reach Instagram (got: {title[:60]})")
        return False

    print("  => Instagram logged in! Proceeding...")
    time.sleep(2)

    # Click the create / "+" button
    print("  [1] Clicking Create button...")
    r1 = ev(
        '() => {'
        '  const svgs = document.querySelectorAll("svg");'
        '  const plusBtn = Array.from(document.querySelectorAll("a,button,[role=button]"))'
        '    .find(el => el.getAttribute("aria-label") && '
        '              el.getAttribute("aria-label").toLowerCase().includes("new post") ||'
        '              el.getAttribute("aria-label") === "New post");'
        '  if (plusBtn) { plusBtn.click(); return "create_clicked"; }'
        '  const allBtns = Array.from(document.querySelectorAll("[role=button],[role=link]"))'
        '    .map(b => b.getAttribute("aria-label") || b.innerText).filter(Boolean).slice(0,15);'
        '  return "not_found: " + allBtns.join("|");'
        '}',
        timeout=20
    )
    print(f"  -> {r1[:100]}")
    time.sleep(3)

    # Try to click Post option if a menu appeared
    if "create_clicked" in r1 or "not_found" not in r1:
        r_post = ev(
            '() => {'
            '  const items = Array.from(document.querySelectorAll("[role=menuitem],[role=button],a"))'
            '    .find(el => el.innerText && el.innerText.trim().toLowerCase() === "post");'
            '  if (items) { items.click(); return "post_option_clicked"; }'
            '  return "no_post_menu_item";'
            '}',
            timeout=15
        )
        print(f"  -> menu: {r_post[:60]}")
        time.sleep(2)

    # Look for file input and upload image
    print("  [2] Uploading image...")
    r2 = ev(
        '() => {'
        '  const inp = document.querySelector("input[type=file]");'
        '  return inp ? "file_input_found" : "no_file_input";'
        '}',
        timeout=15
    )
    print(f"  -> {r2[:60]}")

    if "file_input_found" in r2:
        upload_r = tool("browser_file_upload", {"paths": [IG_IMAGE]}, timeout=20)
        print(f"  -> upload: {upload_r[:80]}")
        time.sleep(4)
    else:
        # Try clicking the image area which should open file picker
        r_click = ev(
            '() => {'
            '  const area = document.querySelector(".IaP2_") || '
            '               document.querySelector("[class*=upload]") || '
            '               document.querySelector("[class*=drag]");'
            '  if (area) { area.click(); return "area_clicked"; }'
            '  return "no_upload_area";'
            '}',
            timeout=15
        )
        print(f"  -> click area: {r_click[:60]}")
        time.sleep(1)
        upload_r = tool("browser_file_upload", {"paths": [IG_IMAGE]}, timeout=20)
        print(f"  -> upload: {upload_r[:80]}")
        time.sleep(4)

    # Click Next (may appear multiple times)
    for step in ["Next_1", "Next_2"]:
        print(f"  [Next] clicking Next ({step})...")
        r_next = ev(
            '() => {'
            '  const b = Array.from(document.querySelectorAll("button,[role=button]"))'
            '    .find(b => b.innerText && b.innerText.trim().toLowerCase() === "next");'
            '  if (b) { b.click(); return "next_clicked"; }'
            '  return "no_next";'
            '}',
            timeout=15
        )
        print(f"  -> {r_next[:60]}")
        if "no_next" in r_next:
            break
        time.sleep(3)

    # Write caption
    print("  [3] Adding caption...")
    cap_js = json.dumps(IG_CAPTION)
    r3 = ev(
        f'() => {{'
        f'  const ta = document.querySelector("textarea") || '
        f'             document.querySelector("[contenteditable=true]") || '
        f'             document.querySelector("[aria-label*=caption i],[aria-label*=Caption i]");'
        f'  if (!ta) return "no_caption_box";'
        f'  ta.focus();'
        f'  document.execCommand("selectAll", false, null);'
        f'  document.execCommand("insertText", false, {cap_js});'
        f'  return "caption_typed len=" + (ta.value || ta.innerText || "").length;'
        f'}}',
        timeout=20
    )
    print(f"  -> {r3[:80]}")
    time.sleep(2)

    # Click Share button
    print("  [4] Clicking Share...")
    r4 = ev(
        '() => {'
        '  const b = Array.from(document.querySelectorAll("button,[role=button]"))'
        '    .find(b => b.innerText && '
        '              (b.innerText.trim().toLowerCase() === "share" || '
        '               b.innerText.trim().toLowerCase() === "post"));'
        '  if (b) { b.click(); return "share_clicked: " + b.innerText.trim(); }'
        '  const all = Array.from(document.querySelectorAll("button,[role=button]"))'
        '    .map(b => b.innerText.trim()).filter(Boolean).slice(0,10);'
        '  return "no_share. buttons: " + all.join("|");'
        '}',
        timeout=20
    )
    print(f"  -> {r4[:100]}")
    time.sleep(5)

    ok = "share_clicked" in r4 or "clicked" in r4
    final = ev("() => document.title", timeout=15)
    print(f"  final page: {final[:60]}")
    print(f"  INSTAGRAM: {'SUCCESS' if ok else 'CHECK BROWSER'}")
    return ok


# ── X / TWITTER ────────────────────────────────────────────────────────────────

def post_x():
    print("\n" + "="*50)
    print("X (TWITTER)")
    print("="*50)

    cur = ev("() => document.title", timeout=15)
    print(f"  current page: {cur[:60]}")

    if not any(k in cur.lower() for k in ["x.com", "twitter", "home"]):
        nav("https://x.com/home")
        wait(60, "(loading X...)")

    title = ev_retry("() => document.title", max_tries=5, wait=20)
    print(f"  page: {title[:60]}")

    if any(k in title.lower() for k in ["log in","login","sign in"]):
        print("  => X not logged in. Please log in to x.com in the browser first.")
        return False

    # Click compose button
    print("  [1] Opening compose...")
    r1 = ev(
        '() => {'
        '  const b = document.querySelector("[data-testid=SideNav_NewTweet_Button]") || '
        '             document.querySelector("[aria-label=Post]") || '
        '             document.querySelector("[href*=compose]");'
        '  if (b) { b.click(); return "compose_clicked"; }'
        '  return "no_compose_btn";'
        '}',
        timeout=20
    )
    print(f"  -> {r1[:80]}")
    time.sleep(2)

    # Type tweet
    print("  [2] Typing tweet...")
    txt = json.dumps(X_TEXT)
    r2 = ev(
        f'() => {{'
        f'  const e = document.querySelector("[data-testid=tweetTextarea_0]") || '
        f'             document.querySelector(".public-DraftEditor-content") || '
        f'             document.querySelector("[role=textbox][contenteditable=true]") || '
        f'             document.querySelector("[contenteditable=true]");'
        f'  if (!e) return "no_editor";'
        f'  e.focus();'
        f'  document.execCommand("selectAll", false, null);'
        f'  document.execCommand("insertText", false, {txt});'
        f'  return "typed len=" + (e.innerText || "").length;'
        f'}}',
        timeout=20
    )
    print(f"  -> {r2[:80]}")
    time.sleep(1)

    # Click Post / Tweet button
    print("  [3] Posting...")
    r3 = ev(
        '() => {'
        '  const b = document.querySelector("[data-testid=tweetButtonInline]") || '
        '             document.querySelector("[data-testid=tweetButton]");'
        '  if (b && !b.disabled) { b.click(); return "posted"; }'
        '  if (b && b.disabled) return "btn_disabled";'
        '  return "no_post_btn";'
        '}',
        timeout=20
    )
    print(f"  -> {r3[:80]}")
    time.sleep(4)

    ok = "posted" in r3
    print(f"  X: {'SUCCESS' if ok else 'CHECK BROWSER'}")
    return ok


# ── LINKEDIN ──────────────────────────────────────────────────────────────────

def post_linkedin():
    print("\n" + "="*50)
    print("LINKEDIN")
    print("="*50)

    cur = ev("() => document.title", timeout=15)
    print(f"  current page: {cur[:60]}")

    if "linkedin" not in cur.lower() or any(k in cur.lower() for k in ["login","sign in"]):
        nav("https://www.linkedin.com/feed/")
        wait(60, "(loading LinkedIn...)")

    title = ev_retry("() => document.title", max_tries=5, wait=20)
    print(f"  page: {title[:60]}")

    if any(k in title.lower() for k in ["login","sign in","join"]):
        print("  => LinkedIn not logged in. Please log in to linkedin.com in the browser first.")
        return False

    # Click "Start a post"
    print("  [1] Clicking Start a post...")
    r1 = ev(
        '() => {'
        '  const b = document.querySelector(".share-box-feed-entry__trigger") || '
        '    document.querySelector("[aria-label*=post i]") || '
        '    Array.from(document.querySelectorAll("button"))'
        '      .find(b => b.innerText && b.innerText.toLowerCase().includes("start a post"));'
        '  if (b) { b.click(); return "start_clicked"; }'
        '  return "no_start_btn";'
        '}',
        timeout=20
    )
    print(f"  -> {r1[:80]}")
    time.sleep(3)

    # Type post
    print("  [2] Typing post...")
    txt = json.dumps(LI_TEXT)
    r2 = ev(
        f'() => {{'
        f'  const e = document.querySelector(".ql-editor") || '
        f'             document.querySelector("[role=textbox][contenteditable=true]") || '
        f'             document.querySelector("[contenteditable=true]");'
        f'  if (!e) return "no_editor";'
        f'  e.focus();'
        f'  document.execCommand("selectAll", false, null);'
        f'  document.execCommand("insertText", false, {txt});'
        f'  return "typed len=" + (e.innerText || "").length;'
        f'}}',
        timeout=20
    )
    print(f"  -> {r2[:80]}")
    time.sleep(2)

    # Click Post
    print("  [3] Clicking Post...")
    r3 = ev(
        '() => {'
        '  const b = document.querySelector(".share-actions__primary-action") || '
        '    document.querySelector("[data-control-name=share.post]") || '
        '    Array.from(document.querySelectorAll("button"))'
        '      .find(b => b.innerText && b.innerText.trim().toLowerCase() === "post");'
        '  if (b) { b.click(); return "posted"; }'
        '  const btns = Array.from(document.querySelectorAll("button"))'
        '    .map(b => b.innerText.trim()).filter(Boolean).slice(0,10);'
        '  return "no_post_btn. btns=" + btns.join("|");'
        '}',
        timeout=20
    )
    print(f"  -> {r3[:100]}")
    time.sleep(4)

    ok = "posted" in r3
    print(f"  LINKEDIN: {'SUCCESS' if ok else 'CHECK BROWSER'}")
    return ok


# ── MAIN ──────────────────────────────────────────────────────────────────────

def ensure_server():
    """Check server, start if needed."""
    import subprocess
    try:
        conn = http.client.HTTPConnection(MCP_HOST, MCP_PORT, timeout=6)
        conn.request("POST", "/mcp",
            json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                        "params":{"protocolVersion":"2024-11-05","capabilities":{},
                                  "clientInfo":{"name":"t","version":"1"}}}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
        r = conn.getresponse()
        sid = r.getheader("Mcp-Session-Id") or ""
        r.read(); conn.close()
        if sid:
            print(f"Server UP (sid={sid[:16]}..)")
            return True
    except Exception:
        pass

    print("Server DOWN — starting Playwright MCP...")
    subprocess.Popen(
        ["npx", "@playwright/mcp@latest", "--port", "8808", "--shared-browser-context"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print("Waiting 18s for server to start...")
    time.sleep(18)
    # Verify
    try:
        conn = http.client.HTTPConnection(MCP_HOST, MCP_PORT, timeout=8)
        conn.request("POST", "/mcp",
            json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                        "params":{"protocolVersion":"2024-11-05","capabilities":{},
                                  "clientInfo":{"name":"t","version":"1"}}}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
        r = conn.getresponse()
        sid = r.getheader("Mcp-Session-Id") or ""
        r.read(); conn.close()
        if sid:
            print(f"Server started (sid={sid[:16]}..)")
            return True
    except Exception as e:
        print(f"Server still not up: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "="*50)
    print("SOCIAL MEDIA POSTER — Remaining Platforms")
    print("Platforms: Instagram, X/Twitter, LinkedIn")
    print("="*50 + "\n")

    if not ensure_server():
        print("FATAL: Could not start Playwright MCP server")
        sys.exit(1)

    # Quick state check
    cur_page = ev("() => document.title + ' | ' + location.hostname", timeout=20)
    print(f"Browser currently on: {cur_page[:80]}\n")

    results = {}
    results["Instagram"] = post_instagram()
    results["X/Twitter"] = post_x()
    results["LinkedIn"]  = post_linkedin()

    print("\n" + "="*50)
    print("FINAL RESULTS")
    print("="*50)
    for platform, ok in results.items():
        if ok is True:
            print(f"  {platform}: POSTED OK")
        elif ok is False:
            print(f"  {platform}: NEEDS ATTENTION (not logged in or check browser)")
        else:
            print(f"  {platform}: UNKNOWN")
    print()
