#!/usr/bin/env python3
"""
Post to Instagram, X/Twitter, LinkedIn.
Facebook already done. Restarts server if needed.
Navigates ONE platform at a time with generous waits.
"""
import http.client, json, time, sys, os, subprocess

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
    "The most powerful thing you can do today: learn one new thing.\n\n"
    "Education compounds. Every single day.\n\n"
    "What did you learn this week?\n\n"
    "#Education #Learning #GrowthMindset"
)

LI_TEXT = (
    "Education is the highest-ROI investment any individual or organisation can make.\n\n"
    "Here is what consistent learning has taught me:\n\n"
    "1. The best learners aren't the smartest \u2014 they're the most consistent.\n"
    "15 minutes a day adds up to 91 hours a year.\n\n"
    "2. Teaching others is the fastest way to master a skill yourself.\n"
    "If you can't explain it simply, you don't understand it yet.\n\n"
    "3. Curiosity is a competitive advantage.\n"
    "In a world where information is free, the people who ask better questions win.\n\n"
    "4. The most in-demand skill in 2026?\n"
    "The ability to learn fast and apply faster. Not a degree \u2014 a habit.\n\n"
    "What's one learning habit that's made the biggest difference in your career?\n\n"
    "#Education #ProfessionalDevelopment #Leadership #GrowthMindset #LifelongLearning"
)


# ── core MCP caller ────────────────────────────────────────────────────────────
def _call(tool, args, timeout=40):
    try:
        conn = http.client.HTTPConnection(MCP_HOST, MCP_PORT, timeout=timeout + 5)
        conn.request("POST", "/mcp",
            json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                "params":{"protocolVersion":"2024-11-05","capabilities":{},
                          "clientInfo":{"name":"poster","version":"1"}}}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
        r = conn.getresponse()
        sid = r.getheader("Mcp-Session-Id") or r.getheader("mcp-session-id") or ""
        r.read()
        if not sid:
            conn.close(); return {"_err": "no_sid"}

        conn.request("POST", "/mcp",
            json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream",
             "Mcp-Session-Id": sid})
        conn.getresponse().read()
        time.sleep(0.2)

        conn.request("POST", "/mcp",
            json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call",
                "params":{"name":tool,"arguments":args}}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream",
             "Mcp-Session-Id": sid})
        r3 = conn.getresponse()
        data = b""
        while True:
            c = r3.read(4096)
            if not c: break
            data += c
        conn.close()
    except Exception as e:
        return {"_err": str(e)[:60]}

    raw = data.decode("utf-8", "replace")
    for line in raw.split("\n"):
        if line.startswith("data:"):
            try: return json.loads(line[5:].strip())
            except: pass
    return {"_err": f"no_data len={len(raw)}"}


def ev(js, timeout=40):
    d = _call("browser_evaluate", {"function": js}, timeout=timeout)
    if "_err" in d: return "ERR:" + d["_err"]
    content = d.get("result", {}).get("content", [])
    full = "\n".join(c.get("text","") for c in content if c.get("type")=="text")
    for ln in full.split("\n"):
        s = ln.strip()
        if s and not s.startswith("#") and not s.startswith("```") and not s.startswith("-"):
            return s.strip('"')
    return "(empty)"


def nav_to(url):
    """Trigger navigation via window.location.replace (non-blocking evaluate)."""
    safe = url.replace('"', '%22')
    _call("browser_evaluate",
          {"function": f'() => {{ window.location.replace("{safe}"); return "nav"; }}'},
          timeout=8)
    print(f"  -> navigating to {url}")


def wait_for_page(wait_s=80, check_interval=20, label="page"):
    """Wait in intervals, checking title each time. Returns final title."""
    title = "(loading)"
    for elapsed in range(check_interval, wait_s + check_interval, check_interval):
        time.sleep(check_interval)
        title = ev("() => document.title", timeout=35)
        print(f"  {elapsed}s: {title[:60]}")
        if not title.startswith("ERR:") and title != "(empty)":
            return title
    return title


def click_js(js, label="", timeout=20):
    r = ev(js, timeout=timeout)
    if label: print(f"  [{label}] {r[:80]}")
    return r


def type_text(js_template, text, timeout=25):
    encoded = json.dumps(text)
    js = js_template.replace("__TEXT__", encoded)
    return ev(js, timeout=timeout)


# ── restart server ─────────────────────────────────────────────────────────────
def restart_server():
    print("Killing old node processes...")
    subprocess.run(
        ["powershell", "-Command",
         "Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force"],
        capture_output=True, timeout=10
    )
    time.sleep(3)

    print("Starting Playwright MCP server...")
    subprocess.Popen(
        ["npx", "@playwright/mcp@latest", "--port", "8808", "--shared-browser-context"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print("Waiting 20s for server startup...")
    time.sleep(20)

    # Verify
    try:
        c = http.client.HTTPConnection(MCP_HOST, MCP_PORT, timeout=8)
        c.request("POST","/mcp",
            json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                "params":{"protocolVersion":"2024-11-05","capabilities":{},
                          "clientInfo":{"name":"t","version":"1"}}}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
        r = c.getresponse()
        sid = r.getheader("Mcp-Session-Id") or ""
        r.read(); c.close()
        if sid:
            print(f"Server UP (sid={sid[:12]}..)"); return True
    except Exception as e:
        print(f"Server not responding: {e}")
    return False


def server_alive():
    try:
        c = http.client.HTTPConnection(MCP_HOST, MCP_PORT, timeout=6)
        c.request("POST","/mcp",
            json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                "params":{"protocolVersion":"2024-11-05","capabilities":{},
                          "clientInfo":{"name":"t","version":"1"}}}).encode(),
            {"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
        r = c.getresponse(); sid = r.getheader("Mcp-Session-Id") or ""
        r.read(); c.close()
        return bool(sid)
    except:
        return False


# ── Instagram ──────────────────────────────────────────────────────────────────
def post_instagram():
    print("\n" + "="*55)
    print("INSTAGRAM")
    print("="*55)

    if not os.path.exists(IG_IMAGE):
        print(f"  Image not found: {IG_IMAGE}")
        return False

    nav_to("https://www.instagram.com/")
    print("  Waiting 90s for Instagram to load (heavy page)...")
    title = wait_for_page(wait_s=120, check_interval=30, label="instagram")
    print(f"  Final title: {title[:70]}")

    if any(k in title.lower() for k in ["log in","login","sign in","accounts"]):
        print("  => Instagram login page — NOT logged in. Skipping.")
        return "needs_login"

    if "instagram" not in title.lower() and title.startswith("ERR:"):
        print("  => Instagram page did not load (Instagram may block automation).")
        print("  => Please manually open instagram.com in the Playwright browser and post.")
        print(f"  => Image to upload: {IG_IMAGE}")
        print(f"  => Caption:\n{IG_CAPTION[:200]}...")
        return "blocked"

    print("  => Instagram accessible. Proceeding to post...")
    time.sleep(2)

    # Click "+" new post
    r1 = click_js(
        '() => {'
        '  const b = Array.from(document.querySelectorAll("svg,a,button,[role=link],[role=button]"))'
        '    .find(el => el.getAttribute && el.getAttribute("aria-label") && '
        '               el.getAttribute("aria-label").toLowerCase().includes("new post"));'
        '  if (b) { b.click(); return "new_post_clicked"; }'
        '  const allLabels = Array.from(document.querySelectorAll("[aria-label]"))'
        '    .map(el => el.getAttribute("aria-label")).filter(Boolean).slice(0,20);'
        '  return "not_found labels: " + allLabels.join("|");'
        '}', "click_create")
    time.sleep(2)

    # Click "Post" from dropdown if appeared
    click_js(
        '() => {'
        '  const items = Array.from(document.querySelectorAll("[role=menuitem],[role=button],a"))'
        '    .find(el => el.innerText && el.innerText.trim().toLowerCase() === "post");'
        '  if (items) { items.click(); return "post_option"; }'
        '  return "no_post_menu";'
        '}', "post_menu")
    time.sleep(2)

    # Upload image
    print("  [upload] Uploading image...")
    r_file = _call("browser_file_upload", {"paths": [IG_IMAGE]}, timeout=20)
    upload_ok = "error" not in str(r_file).lower() and "_err" not in r_file
    print(f"  -> upload result: {str(r_file)[:80]}")
    time.sleep(4)

    # Click Next through steps
    for i in range(3):
        rn = click_js(
            '() => {'
            '  const b = Array.from(document.querySelectorAll("button,[role=button]"))'
            '    .find(b => b.innerText && b.innerText.trim().toLowerCase() === "next");'
            '  if (b) { b.click(); return "next"; }'
            '  return "no_next";'
            '}', f"next_{i+1}")
        if "no_next" in rn: break
        time.sleep(2)

    # Add caption
    cap = json.dumps(IG_CAPTION)
    r3 = ev(
        f'() => {{'
        f'  const ta = document.querySelector("textarea[aria-label]") || '
        f'             document.querySelector("textarea") || '
        f'             document.querySelector("[contenteditable=true]") ||'
        f'             document.querySelector("[aria-label*=caption i]");'
        f'  if (!ta) return "no_caption_field";'
        f'  ta.focus();'
        f'  document.execCommand("selectAll",false,null);'
        f'  document.execCommand("insertText",false,{cap});'
        f'  return "captioned len=" + (ta.value||ta.innerText||"").length;'
        f'}}', timeout=20)
    print(f"  [caption] {r3[:70]}")
    time.sleep(2)

    # Share
    r4 = click_js(
        '() => {'
        '  const b = Array.from(document.querySelectorAll("button,[role=button]"))'
        '    .find(b => b.innerText && '
        '               b.innerText.trim().toLowerCase().match(/^(share|post)$/));'
        '  if (b) { b.click(); return "shared: " + b.innerText.trim(); }'
        '  const btns = Array.from(document.querySelectorAll("button"))'
        '    .map(b=>b.innerText.trim()).filter(Boolean).slice(0,8);'
        '  return "no_share. btns: " + btns.join("|");'
        '}', "share")
    time.sleep(5)

    ok = "shared" in r4 or "captioned" in r3
    print(f"  INSTAGRAM: {'POSTED' if ok else 'CHECK BROWSER'}")
    return ok


# ── X / Twitter ────────────────────────────────────────────────────────────────
def post_x():
    print("\n" + "="*55)
    print("X (TWITTER)")
    print("="*55)

    nav_to("https://x.com/home")
    title = wait_for_page(wait_s=80, check_interval=20, label="x")
    print(f"  Final title: {title[:60]}")

    if any(k in title.lower() for k in ["log in","login","sign in"]):
        print("  => X not logged in.")
        print("  => Please log in to x.com in the Playwright browser window.")
        print(f"  => Then paste this tweet:\n{X_TEXT}")
        return "needs_login"

    # Compose
    r1 = click_js(
        '() => {'
        '  const b = document.querySelector("[data-testid=SideNav_NewTweet_Button]") ||'
        '             document.querySelector("[aria-label=Post]");'
        '  if (b) { b.click(); return "compose_open"; }'
        '  return "no_compose";'
        '}', "compose")
    time.sleep(2)

    # Type
    txt = json.dumps(X_TEXT)
    r2 = ev(
        f'() => {{'
        f'  const e = document.querySelector("[data-testid=tweetTextarea_0]") ||'
        f'             document.querySelector("[role=textbox][contenteditable=true]");'
        f'  if (!e) return "no_editor";'
        f'  e.focus();'
        f'  document.execCommand("selectAll",false,null);'
        f'  document.execCommand("insertText",false,{txt});'
        f'  return "typed len=" + (e.innerText||"").length;'
        f'}}', timeout=20)
    print(f"  [type] {r2[:70]}")
    time.sleep(1)

    # Post
    r3 = click_js(
        '() => {'
        '  const b = document.querySelector("[data-testid=tweetButtonInline]") ||'
        '             document.querySelector("[data-testid=tweetButton]");'
        '  if (b && !b.disabled) { b.click(); return "tweeted"; }'
        '  return b ? "btn_disabled" : "no_btn";'
        '}', "tweet")
    time.sleep(4)

    ok = "tweeted" in r3
    print(f"  X: {'POSTED' if ok else 'CHECK BROWSER'}")
    return ok


# ── LinkedIn ──────────────────────────────────────────────────────────────────
def post_linkedin():
    print("\n" + "="*55)
    print("LINKEDIN")
    print("="*55)

    nav_to("https://www.linkedin.com/feed/")
    title = wait_for_page(wait_s=80, check_interval=20, label="linkedin")
    print(f"  Final title: {title[:60]}")

    if any(k in title.lower() for k in ["login","sign in","join linkedin"]):
        print("  => LinkedIn not logged in.")
        print("  => Please log in to linkedin.com in the Playwright browser window.")
        print(f"  => Then paste this post:\n{LI_TEXT[:200]}...")
        return "needs_login"

    # Start a post
    r1 = click_js(
        '() => {'
        '  const b = document.querySelector(".share-box-feed-entry__trigger") ||'
        '    Array.from(document.querySelectorAll("button"))'
        '      .find(b => b.innerText && b.innerText.toLowerCase().includes("start a post"));'
        '  if (b) { b.click(); return "start_clicked"; }'
        '  return "no_start";'
        '}', "start_post")
    time.sleep(3)

    # Type
    txt = json.dumps(LI_TEXT)
    r2 = ev(
        f'() => {{'
        f'  const e = document.querySelector(".ql-editor") ||'
        f'             document.querySelector("[role=textbox][contenteditable=true]") ||'
        f'             document.querySelector("[contenteditable=true]");'
        f'  if (!e) return "no_editor";'
        f'  e.focus();'
        f'  document.execCommand("selectAll",false,null);'
        f'  document.execCommand("insertText",false,{txt});'
        f'  return "typed len=" + (e.innerText||"").length;'
        f'}}', timeout=20)
    print(f"  [type] {r2[:70]}")
    time.sleep(2)

    # Post
    r3 = click_js(
        '() => {'
        '  const b = document.querySelector(".share-actions__primary-action") ||'
        '    Array.from(document.querySelectorAll("button"))'
        '      .find(b => b.innerText && b.innerText.trim().toLowerCase() === "post");'
        '  if (b) { b.click(); return "posted"; }'
        '  return "no_post_btn";'
        '}', "post")
    time.sleep(4)

    ok = "posted" in r3
    print(f"  LINKEDIN: {'POSTED' if ok else 'CHECK BROWSER'}")
    return ok


# ── log & update dashboard ─────────────────────────────────────────────────────
def update_vault(results):
    import datetime
    today = datetime.date.today().isoformat()
    log_path = f"D:/Hackathon-0/Logs/social_{today}.json"

    os.makedirs("D:/Hackathon-0/Logs", exist_ok=True)
    log = []
    for platform, status in results.items():
        log.append({
            "timestamp": datetime.datetime.now().isoformat(),
            "platform": platform,
            "action": "post",
            "status": str(status),
        })

    existing = []
    if os.path.exists(log_path):
        try: existing = json.loads(open(log_path).read())
        except: pass
    with open(log_path, "w") as f:
        json.dump(existing + log, f, indent=2)
    print(f"\nLogged to {log_path}")

    # Update Dashboard
    dash_path = "D:/Hackathon-0/Dashboard.md"
    lines = []
    try: lines = open(dash_path).readlines()
    except: pass

    summary_lines = ["\n## Social Media Posts — Education Campaign\n",
                     f"*Updated: {today}*\n\n",
                     "| Platform | Status |\n", "|----------|--------|\n",
                     "| Facebook | POSTED (done) |\n"]
    for p, s in results.items():
        status_str = "POSTED" if s is True else ("NEEDS LOGIN" if s == "needs_login" else str(s).upper())
        summary_lines.append(f"| {p} | {status_str} |\n")

    with open(dash_path, "a") as f:
        f.writelines(summary_lines)
    print(f"Dashboard updated: {dash_path}")


# ── main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("="*55)
    print("SOCIAL MEDIA POSTER — Instagram, X, LinkedIn")
    print("Facebook: already posted")
    print("="*55)

    # Ensure server is running
    if not server_alive():
        if not restart_server():
            print("FATAL: Could not start Playwright MCP server")
            sys.exit(1)
    else:
        print("Server already running.")

    # Quick page check
    cur = ev("() => document.title + ' | ' + location.hostname", timeout=20)
    print(f"\nBrowser currently on: {cur[:80]}\n")

    results = {}

    # Post in order: X, LinkedIn, Instagram (Instagram last — may take longest)
    results["X/Twitter"] = post_x()
    results["LinkedIn"]  = post_linkedin()
    results["Instagram"] = post_instagram()

    print("\n" + "="*55)
    print("SUMMARY")
    print("="*55)
    for p, s in results.items():
        if s is True:
            print(f"  {p}: POSTED OK")
        elif s == "needs_login":
            print(f"  {p}: Needs login — please log in to the Playwright browser")
        elif s == "blocked":
            print(f"  {p}: Blocked by platform — manual posting required")
        else:
            print(f"  {p}: {s}")

    update_vault(results)
    print("\nDone.")
