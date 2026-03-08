#!/usr/bin/env python3
"""
Post education content to social media using ONLY browser_evaluate (pure DOM JS).
NO browser_snapshot, NO browser_run_code.
"""
import json, subprocess, sys, time

MCP_CLIENT = "D:/Hackathon-0/.claude/Skills/browsing-with-playwright/scripts/mcp-client.py"
MCP_URL = "http://localhost:8808"

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


def ev(js: str, timeout: int = 20) -> str:
    """Call browser_evaluate and return just the Result line."""
    params = json.dumps({"function": js})
    cmd = [sys.executable, MCP_CLIENT, "call",
           "-u", MCP_URL, "-t", "browser_evaluate", "-p", params]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = r.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            err = r.stderr.decode("utf-8", errors="replace").strip()
            return f"EVALERR:{err[:60]}"
        d = json.loads(out)
        full = "\n".join(c["text"] for c in d.get("content", []) if c.get("type") == "text")
        # Extract just the Result section (before any ### headers)
        for line in full.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("```") and not line.startswith("-"):
                return line.strip('"')
        return full[:100]
    except subprocess.TimeoutExpired:
        return f"EVALERR:timeout{timeout}s"
    except Exception as e:
        return f"EVALERR:{e}"


def wait(s):
    print(f"  wait {s}s...")
    time.sleep(s)


def nav(url):
    """Navigate asynchronously via location.replace, then wait."""
    js = 'window.location.replace("' + url + '"); return "nav";'
    ev(f'() => {{ {js} }}', timeout=10)
    wait(20)


# ─── FACEBOOK ────────────────────────────────────────────────────────────────
def post_facebook():
    print("\n[FACEBOOK]")
    title = ev("() => document.title", timeout=20)
    print(f"  page: {title[:60]}")
    if "EVALERR" in title:
        print("  SKIP: server not responding")
        return False

    if "facebook" not in title.lower():
        print("  navigating to facebook...")
        nav("https://www.facebook.com")
        title = ev("() => document.title", timeout=20)
        print(f"  page after nav: {title[:60]}")

    # Click composer
    text_esc = json.dumps(FB_TEXT)
    print("  clicking composer...")
    r1 = ev("""() => {
        const btns = Array.from(document.querySelectorAll('[role="button"]'));
        const btn = btns.find(b => b.innerText && b.innerText.toLowerCase().includes('mind'));
        if (!btn) return 'composer not found';
        btn.click(); return 'clicked: ' + btn.innerText.substring(0,40);
    }""", timeout=20)
    print(f"  click: {r1[:80]}")
    wait(4)

    # Type via execCommand (fastest, no Playwright page needed)
    print("  typing text...")
    type_js = f"""() => {{
        const ed = document.querySelector('[contenteditable="true"][role="textbox"]') ||
                   document.querySelector('[contenteditable="true"]');
        if (!ed) return 'no editor found after click';
        ed.focus();
        document.execCommand('selectAll', false, null);
        const ok = document.execCommand('insertText', false, {text_esc});
        if (!ok) {{
            // Fallback: use clipboard API approach via DataTransfer
            ed.innerHTML = '';
            const lines = {text_esc}.split('\\n');
            lines.forEach((line, i) => {{
                const t = document.createTextNode(line);
                ed.appendChild(t);
                if (i < lines.length-1) ed.appendChild(document.createElement('br'));
            }});
            ed.dispatchEvent(new InputEvent('input', {{bubbles:true, inputType:'insertText'}}));
        }}
        return 'typed length=' + ed.innerText.length;
    }}"""
    r2 = ev(type_js, timeout=20)
    print(f"  type: {r2[:80]}")
    wait(2)

    # Click Post
    print("  clicking Post...")
    r3 = ev("""() => {
        const btns = Array.from(document.querySelectorAll('[aria-label="Post"]'));
        if (btns.length) { btns[btns.length-1].click(); return 'Post clicked (aria-label), n='+btns.length; }
        const allBtns = Array.from(document.querySelectorAll('[role="button"]'));
        const postBtn = allBtns.find(b => b.innerText && b.innerText.trim() === 'Post');
        if (postBtn) { postBtn.click(); return 'Post clicked (text)'; }
        return 'Post btn not found. buttons: ' + allBtns.slice(0,8).map(b=>b.innerText.substring(0,15)).join('|');
    }""", timeout=20)
    print(f"  post: {r3[:120]}")
    wait(4)

    final = ev("() => document.title", timeout=15)
    print(f"  final page: {final[:60]}")
    success = "clicked" in r3.lower()
    print(f"  [FACEBOOK] {'SUCCESS' if success else 'FAILED'}")
    return success


# ─── X / TWITTER ─────────────────────────────────────────────────────────────
def post_x():
    print("\n[X / TWITTER]")
    nav("https://x.com")
    title = ev("() => document.title", timeout=20)
    print(f"  page: {title[:60]}")
    if "x.com" not in title.lower() and "twitter" not in title.lower() and "home" not in title.lower():
        print("  [X] Login required or page not loaded")
        return False

    # Click compose button
    r1 = ev("""() => {
        const btn = document.querySelector('[data-testid="SideNav_NewTweet_Button"]') ||
                    document.querySelector('[aria-label="Post"]') ||
                    document.querySelector('a[href="/compose/tweet"]');
        if (btn) { btn.click(); return 'compose clicked: '+btn.tagName; }
        return 'compose not found';
    }""", timeout=20)
    print(f"  compose: {r1[:80]}")
    wait(2)

    # Type text
    text_esc = json.dumps(X_TEXT)
    r2 = ev(f"""() => {{
        const ed = document.querySelector('[data-testid="tweetTextarea_0"]') ||
                   document.querySelector('.DraftEditor-editorContainer [contenteditable]') ||
                   document.querySelector('[role="textbox"]');
        if (!ed) return 'no editor';
        ed.focus();
        document.execCommand('insertText', false, {text_esc});
        return 'typed len=' + (ed.innerText||ed.textContent||'').length;
    }}""", timeout=20)
    print(f"  type: {r2[:80]}")
    wait(1)

    # Post
    r3 = ev("""() => {
        const btn = document.querySelector('[data-testid="tweetButtonInline"]') ||
                    document.querySelector('[data-testid="tweetButton"]');
        if (btn) { btn.click(); return 'tweeted'; }
        return 'tweet btn not found';
    }""", timeout=15)
    print(f"  post: {r3[:80]}")
    wait(3)
    success = "tweeted" in r3
    print(f"  [X] {'SUCCESS' if success else 'FAILED/PARTIAL'}")
    return success


# ─── LINKEDIN ─────────────────────────────────────────────────────────────────
def post_linkedin():
    print("\n[LINKEDIN]")
    nav("https://www.linkedin.com/feed/")
    title = ev("() => document.title", timeout=20)
    print(f"  page: {title[:60]}")
    if "linkedin" not in title.lower():
        print("  [LI] Login required or page not loaded")
        return False

    # Click Start a post
    r1 = ev("""() => {
        const btn = document.querySelector('.share-box-feed-entry__trigger') ||
                    document.querySelector('[data-control-name="share.sharebox_focus"]') ||
                    Array.from(document.querySelectorAll('button')).find(b =>
                        b.innerText && b.innerText.toLowerCase().includes('start a post'));
        if (btn) { btn.click(); return 'start clicked: ' + btn.innerText.substring(0,30); }
        return 'start btn not found';
    }""", timeout=20)
    print(f"  start: {r1[:80]}")
    wait(3)

    # Type in editor
    text_esc = json.dumps(LI_TEXT)
    r2 = ev(f"""() => {{
        const ed = document.querySelector('.ql-editor') ||
                   document.querySelector('[contenteditable="true"]') ||
                   document.querySelector('[role="textbox"]');
        if (!ed) return 'no editor';
        ed.focus();
        document.execCommand('insertText', false, {text_esc});
        return 'typed len=' + (ed.innerText||ed.textContent||'').length;
    }}""", timeout=20)
    print(f"  type: {r2[:80]}")
    wait(1)

    # Post
    r3 = ev("""() => {
        const btn = document.querySelector('.share-actions__primary-action') ||
                    Array.from(document.querySelectorAll('button')).find(b =>
                        b.innerText && b.innerText.trim() === 'Post');
        if (btn) { btn.click(); return 'posted'; }
        return 'post btn not found';
    }""", timeout=15)
    print(f"  post: {r3[:80]}")
    wait(3)
    success = "posted" in r3
    print(f"  [LI] {'SUCCESS' if success else 'FAILED/PARTIAL'}")
    return success


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("=== EDUCATION POST — ALL PLATFORMS ===")
    title = ev("() => document.title", timeout=20)
    print(f"Server OK. Current: {title[:60]}\n")
    if "EVALERR" in title:
        print("FATAL: server not responding"); sys.exit(1)

    results = {}
    results["Facebook"] = post_facebook()
    results["X/Twitter"] = post_x()
    results["LinkedIn"] = post_linkedin()

    print("\n╔══════════════════════════════╗")
    print("║  FINAL RESULTS               ║")
    print("╠══════════════════════════════╣")
    for p, ok in results.items():
        status = "✓ POSTED" if ok else "✗ FAILED"
        print(f"║  {p:<20} {status:<8}║")
    print("╚══════════════════════════════╝")


if __name__ == "__main__":
    main()
