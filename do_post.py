#!/usr/bin/env python3
"""Post education content step by step. Facebook first."""
import json, subprocess, sys, time

C = "D:/Hackathon-0/.claude/Skills/browsing-with-playwright/scripts/mcp-client.py"
U = "http://localhost:8808"

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


def ev(js: str, t: int = 25) -> str:
    try:
        r = subprocess.run(
            [sys.executable, C, "call", "-u", U, "-t", "browser_evaluate",
             "-p", json.dumps({"function": js})],
            capture_output=True, timeout=t
        )
    except subprocess.TimeoutExpired:
        return "FAIL:subprocess_timeout"
    out = r.stdout.decode("utf-8", "replace").strip()
    err = r.stderr.decode("utf-8", "replace").strip()
    if not out:
        return f"FAIL:{err[:80]}"
    try:
        d = json.loads(out)
        full = "\n".join(c["text"] for c in d.get("content", []) if c.get("type") == "text")
        # Return first non-header line
        for line in full.split("\n"):
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("```") and not s.startswith("-"):
                return s.strip('"')
        return "(empty result)"
    except Exception as e:
        return f"FAIL:{e}:{out[:60]}"


def wait(s: int):
    print(f"  ... waiting {s}s")
    time.sleep(s)


def nav_and_wait(url: str, wait_s: int = 25):
    """Trigger async navigation."""
    js = f'() => {{ window.location.replace("{url}"); return "nav"; }}'
    ev(js, t=30)  # May timeout as page changes, that's OK
    print(f"  waiting {wait_s}s for {url[:40]}...")
    time.sleep(wait_s)


# ── FACEBOOK ──────────────────────────────────────────────────────────────────
def post_facebook():
    print("\n=== FACEBOOK ===")
    title = ev("() => document.title", t=25)
    print(f"  page: {title[:60]}")

    if "facebook" not in title.lower():
        print("  NOT on Facebook — navigating...")
        nav_and_wait("https://www.facebook.com", 25)
        title = ev("() => document.title", t=25)
        print(f"  page now: {title[:60]}")

    # Step 1: Click composer
    print("  [1] Click composer button...")
    r1 = ev('() => { const btns=Array.from(document.querySelectorAll("[role=button]")); const b=btns.find(b=>b.innerText&&b.innerText.includes("mind")); if(!b) return "not found,n="+btns.length; b.click(); return "clicked:"+b.innerText.substring(0,30); }', t=25)
    print(f"  -> {r1[:80]}")
    wait(4)

    # Step 2: Check editor
    print("  [2] Check editor...")
    r2 = ev('() => { const e=document.querySelector("[contenteditable=true][role=textbox]"); return e?"editor found":"no editor (class="+document.activeElement.className.substring(0,30)+")"; }', t=20)
    print(f"  -> {r2[:80]}")

    # Step 3: Type text via execCommand
    print("  [3] Type text...")
    txt = json.dumps(FB_TEXT)  # properly JSON-encoded string
    r3 = ev(f'() => {{ const e=document.querySelector("[contenteditable=true][role=textbox]")||document.querySelector("[contenteditable=true]"); if(!e) return "no editor"; e.focus(); document.execCommand("selectAll",false,null); const ok=document.execCommand("insertText",false,{txt}); return ok?"typed len="+e.innerText.length:"execCmd failed,len="+e.innerText.length; }}', t=20)
    print(f"  -> {r3[:80]}")
    wait(2)

    # Step 4: Click Post
    print("  [4] Click Post button...")
    r4 = ev('() => { const btns=Array.from(document.querySelectorAll("[aria-label=Post]")); if(btns.length) { btns[btns.length-1].click(); return "Post clicked n="+btns.length; } const all=Array.from(document.querySelectorAll("[role=button]")); const p=all.find(b=>b.innerText&&b.innerText.trim()==="Post"); if(p){p.click();return "Post clicked by text";} return "not found,btns="+all.slice(0,5).map(b=>b.innerText.substring(0,10)).join("|"); }', t=20)
    print(f"  -> {r4[:100]}")
    wait(4)

    final = ev("() => document.title", t=20)
    print(f"  final: {final[:60]}")
    success = "clicked" in r4.lower()
    print(f"  [FB] {'OK' if success else 'CHECK RESULT'}")
    return success


# ── X / TWITTER ───────────────────────────────────────────────────────────────
def post_x():
    print("\n=== X (TWITTER) ===")
    nav_and_wait("https://x.com/home", 20)
    title = ev("() => document.title", t=20)
    print(f"  page: {title[:60]}")

    if not any(k in title.lower() for k in ["x.com", "twitter", "home"]):
        print("  [X] Need to login first")
        return False

    # Click New Tweet/Post
    r1 = ev('() => { const b=document.querySelector("[data-testid=SideNav_NewTweet_Button]")||document.querySelector("a[href=\"/compose/tweet\"]"); if(b){b.click();return "compose clicked";} return "no compose btn"; }', t=20)
    print(f"  compose: {r1[:60]}")
    wait(2)

    # Type
    txt = json.dumps(X_TEXT)
    r2 = ev(f'() => {{ const e=document.querySelector("[data-testid=tweetTextarea_0]")||document.querySelector("[role=textbox]"); if(!e) return "no editor"; e.focus(); document.execCommand("insertText",false,{txt}); return "typed len="+(e.innerText||"").length; }}', t=20)
    print(f"  type: {r2[:60]}")
    wait(1)

    # Post
    r3 = ev('() => { const b=document.querySelector("[data-testid=tweetButtonInline]")||document.querySelector("[data-testid=tweetButton]"); if(b){b.click();return "tweeted";} return "no btn"; }', t=15)
    print(f"  post: {r3[:60]}")
    wait(3)
    ok = "tweeted" in r3
    print(f"  [X] {'OK' if ok else 'CHECK RESULT'}")
    return ok


# ── LINKEDIN ──────────────────────────────────────────────────────────────────
def post_linkedin():
    print("\n=== LINKEDIN ===")
    nav_and_wait("https://www.linkedin.com/feed/", 20)
    title = ev("() => document.title", t=20)
    print(f"  page: {title[:60]}")

    if "linkedin" not in title.lower() or "login" in title.lower():
        print("  [LI] Need to login first")
        return False

    # Click Start a post
    r1 = ev('() => { const b=document.querySelector(".share-box-feed-entry__trigger")||Array.from(document.querySelectorAll("button")).find(b=>b.innerText&&b.innerText.toLowerCase().includes("start a post")); if(b){b.click();return "start clicked";} return "start not found"; }', t=20)
    print(f"  start: {r1[:60]}")
    wait(3)

    # Type
    txt = json.dumps(LI_TEXT)
    r2 = ev(f'() => {{ const e=document.querySelector(".ql-editor")||document.querySelector("[contenteditable=true]")||document.querySelector("[role=textbox]"); if(!e) return "no editor"; e.focus(); document.execCommand("insertText",false,{txt}); return "typed len="+(e.innerText||"").length; }}', t=20)
    print(f"  type: {r2[:60]}")
    wait(1)

    # Post
    r3 = ev('() => { const b=document.querySelector(".share-actions__primary-action")||Array.from(document.querySelectorAll("button")).find(b=>b.innerText&&b.innerText.trim()==="Post"); if(b){b.click();return "posted";} return "no post btn"; }', t=15)
    print(f"  post: {r3[:60]}")
    wait(3)
    ok = "posted" in r3
    print(f"  [LI] {'OK' if ok else 'CHECK RESULT'}")
    return ok


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== EDUCATION POST — SOCIAL MEDIA ===")
    title = ev("() => document.title", t=20)
    print(f"Current page: {title[:60]}")

    results = {}
    results["Facebook"] = post_facebook()
    results["X"] = post_x()
    results["LinkedIn"] = post_linkedin()

    print("\n=== RESULTS ===")
    for p, ok in results.items():
        print(f"  {p}: {'POSTED' if ok else 'CHECK BROWSER'}")
