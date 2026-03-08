#!/usr/bin/env python3
"""
Post to social media using browser_evaluate (pure JS, no browser_run_code).
Uses execCommand('insertText') for typing into contenteditable elements.
"""
import json, subprocess, sys, time, re

MCP = "http://localhost:8808/mcp"
SKILL = "D:/Hackathon-0/.claude/Skills/browsing-with-playwright/scripts/mcp-client.py"

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
    "Not in classrooms — in every meeting, every setback, every conversation.\n\n"
    "They ask better questions. They reflect on what went wrong. "
    "They turn every experience into a lesson.\n\n"
    "What is the one thing you taught yourself — outside of any school or course — "
    "that changed your career or life? Drop it in the comments.\n\n"
    "#Education #LifelongLearning #ProfessionalDevelopment #GrowthMindset #Leadership"
)


def call_tool(tool, params, timeout=25):
    """Call MCP tool via mcp-client.py (handles session internally)."""
    cmd = [sys.executable, SKILL, "call",
           "-u", MCP, "-t", tool, "-p", json.dumps(params)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = r.stdout.decode("utf-8", errors="replace").strip()
        if out:
            return json.loads(out)
        err = r.stderr.decode("utf-8", errors="replace").strip()
        return {"_err": err or "(empty)"}
    except subprocess.TimeoutExpired:
        return {"_err": f"timeout after {timeout}s"}
    except json.JSONDecodeError:
        return {"_err": f"invalid JSON: {out[:100]}"}
    except Exception as e:
        return {"_err": str(e)}


def txt(resp):
    return "\n".join(
        c.get("text", "") for c in resp.get("content", [])
        if c.get("type") == "text"
    )


def evaluate(js, timeout=20):
    """Quick JS evaluation on current page."""
    r = call_tool("browser_evaluate", {"function": js}, timeout=timeout)
    if "_err" in r:
        return f"ERR: {r['_err']}"
    return txt(r)


def wait_ms(ms):
    call_tool("browser_wait_for", {"time": ms}, timeout=ms // 1000 + 10)


def snapshot(timeout=25):
    r = call_tool("browser_snapshot", {}, timeout=timeout)
    if "_err" in r:
        return ""
    return txt(r)


def click_ref(element_desc, ref, timeout=20):
    r = call_tool("browser_click", {"element": element_desc, "ref": ref}, timeout=timeout)
    return txt(r)


def type_text(element_desc, ref, text, timeout=60):
    r = call_tool("browser_type", {"element": element_desc, "ref": ref, "text": text}, timeout=timeout)
    return txt(r)


def find_ref(snap_text, keywords, prefer="button"):
    """Find element ref in snapshot near keyword match."""
    lines = snap_text.split("\n")
    for i, line in enumerate(lines):
        lw = line.lower()
        if any(k.lower() in lw for k in keywords) and "ref=" in line:
            m = re.search(r'\[ref=(e\d+)\]', line)
            if m:
                if prefer in lw:
                    return m.group(1), line.strip()
                # Check adjacent lines for parent button
                for j in range(max(0, i-3), i):
                    pm = re.search(r'button \[ref=(e\d+)\]', lines[j])
                    if pm:
                        return pm.group(1), lines[j].strip()
                return m.group(1), line.strip()
    return None, None


def navigate_fast(url):
    """Navigate by setting location.href (returns immediately, nav happens async)."""
    evaluate(f'() => {{ window.location.href = "{url}"; return "ok"; }}', timeout=5)
    time.sleep(5)  # Let page start loading


def post_facebook():
    print("\n=== FACEBOOK ===")

    # Check current title
    title = evaluate("() => document.title", timeout=15)
    print(f"  Page: {title[:60]}")

    if "facebook" not in title.lower():
        print("  Not on Facebook. Getting snapshot first...")
        snap = snapshot(timeout=20)
        if not snap:
            print("  ERROR: empty snapshot, browser may be stuck")
            return False

    # Take snapshot to find composer button
    snap = snapshot(timeout=25)
    if not snap:
        print("  ERROR: empty snapshot")
        return False
    print(f"  Snapshot: {len(snap)} chars")

    # Find "What's on your mind" button
    btn_ref, btn_line = find_ref(snap, ["what's on your mind", "mind, maryam", "mind,"])
    if not btn_ref:
        # Try finding button near "Create a post"
        btn_ref, btn_line = find_ref(snap, ["create a post"], prefer="button")

    if not btn_ref:
        print("  ERROR: composer button not found in snapshot")
        # Print relevant lines
        for line in snap.split("\n"):
            if any(k in line.lower() for k in ["mind", "create", "compose", "post"]):
                print(f"    {line.strip()[:100]}")
        return False

    print(f"  Composer button: {btn_ref} — {btn_line[:80]}")

    # Click the button
    print("  Clicking composer...")
    click_ref("What's on your mind", btn_ref, timeout=20)
    wait_ms(3000)

    # Snapshot to find textbox
    snap2 = snapshot(timeout=25)
    if not snap2 or len(snap2) < 100:
        print(f"  WARNING: post-click snapshot empty ({len(snap2)} chars)")
        # Try using JS to insert text directly
        print("  Trying JS insertText approach...")
        result = evaluate("""() => {
            const ed = document.querySelector('[contenteditable="true"][role="textbox"]');
            if (!ed) return 'editor not found';
            ed.focus();
            document.execCommand('selectAll');
            document.execCommand('insertText', false, arguments[0]);
            return 'typed via execCommand';
        }""".replace('arguments[0]', json.dumps(FB_TEXT)), timeout=20)
        print(f"  JS result: {result[:100]}")
        wait_ms(1500)
        # Try clicking Post button via JS
        post_result = evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('[aria-label="Post"]'));
            const btn = btns[btns.length - 1];
            if (!btn) return 'post button not found';
            btn.click();
            return 'clicked post';
        }""", timeout=15)
        print(f"  Post button: {post_result[:100]}")
        return "post" in post_result.lower() or "typed" in result.lower()

    print(f"  Dialog snapshot: {len(snap2)} chars")

    # Find textbox ref
    txt_ref, txt_line = find_ref(snap2, ["textbox", "contenteditable"], prefer="textbox")
    if not txt_ref:
        txt_ref, txt_line = find_ref(snap2, ["what's on your mind", "mind,"])

    if not txt_ref:
        print("  ERROR: textbox not found")
        for line in snap2.split("\n"):
            if any(k in line.lower() for k in ["textbox", "dialog", "editor", "compose"]):
                print(f"    {line.strip()[:100]}")
        return False

    print(f"  Textbox: {txt_ref} — {txt_line[:80]}")

    # Type the post text
    print("  Typing post...")
    type_result = type_text("Post editor", txt_ref, FB_TEXT, timeout=90)
    print(f"  Type result: {type_result[:80]}")
    wait_ms(2000)

    # Snapshot for Post button
    snap3 = snapshot(timeout=25)
    post_btn_ref, post_btn_line = find_ref(snap3, ["[aria-label=\"post\"", "post button"], prefer="button")
    if not post_btn_ref:
        # Try by aria-label via JS
        print("  Clicking Post via JS...")
        post_r = evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('[aria-label="Post"]'));
            const btn = btns[btns.length - 1];
            if (!btn) return 'not found';
            btn.click(); return 'clicked';
        }""", timeout=15)
        print(f"  Post click: {post_r[:80]}")
    else:
        print(f"  Post button: {post_btn_ref}")
        click_ref("Post", post_btn_ref, timeout=20)

    wait_ms(3000)
    final_title = evaluate("() => document.title", timeout=15)
    print(f"  After post title: {final_title[:60]}")
    print("  FACEBOOK: DONE")
    return True


def post_x():
    print("\n=== X (TWITTER) ===")

    # Navigate to compose
    title = evaluate("() => document.title", timeout=10)
    if "x.com" not in title.lower() and "twitter" not in title.lower():
        print("  Navigating to X...")
        navigate_fast("https://x.com")
        time.sleep(8)

    title = evaluate("() => document.title", timeout=15)
    print(f"  Page: {title[:60]}")

    # Click compose button or go directly to compose
    snap = snapshot(timeout=25)
    print(f"  Snapshot: {len(snap)} chars")

    # Find compose/new tweet button
    compose_ref, compose_line = find_ref(snap, ["compose", "tweet", "post"], prefer="button")
    if compose_ref:
        print(f"  Compose button: {compose_ref}")
        click_ref("Compose", compose_ref, timeout=15)
        wait_ms(2000)

    # Find text area
    snap2 = snapshot(timeout=25)
    txt_ref, _ = find_ref(snap2, ["tweet", "what's happening", "post"])
    if not txt_ref:
        txt_ref, _ = find_ref(snap2, ["textbox"])

    if not txt_ref:
        print("  ERROR: X text input not found")
        return False

    print(f"  Text area: {txt_ref}")
    type_text("Tweet editor", txt_ref, X_TEXT, timeout=60)
    wait_ms(1500)

    # Click Post/Tweet button
    snap3 = snapshot(timeout=20)
    post_ref, _ = find_ref(snap3, ["post", "tweet"], prefer="button")
    if post_ref:
        click_ref("Post", post_ref, timeout=15)
        wait_ms(2000)
        print("  X: DONE")
        return True

    print("  ERROR: X post button not found")
    return False


def post_linkedin():
    print("\n=== LINKEDIN ===")

    print("  Navigating to LinkedIn...")
    navigate_fast("https://www.linkedin.com/feed/")
    time.sleep(8)

    title = evaluate("() => document.title", timeout=15)
    print(f"  Page: {title[:60]}")

    snap = snapshot(timeout=25)
    print(f"  Snapshot: {len(snap)} chars")

    # Click "Start a post"
    start_ref, _ = find_ref(snap, ["start a post", "create a post"], prefer="button")
    if not start_ref:
        start_ref, _ = find_ref(snap, ["post"], prefer="button")

    if start_ref:
        click_ref("Start a post", start_ref, timeout=15)
        wait_ms(2500)

    # Find editor
    snap2 = snapshot(timeout=25)
    ed_ref, _ = find_ref(snap2, ["textbox", "editor", "what do you want to talk about"])

    if not ed_ref:
        print("  ERROR: LinkedIn editor not found")
        return False

    print(f"  Editor: {ed_ref}")
    type_text("LinkedIn editor", ed_ref, LI_TEXT, timeout=90)
    wait_ms(2000)

    # Click Post
    snap3 = snapshot(timeout=20)
    post_ref, _ = find_ref(snap3, ["post", "share"], prefer="button")
    if post_ref:
        click_ref("Post", post_ref, timeout=15)
        wait_ms(2000)
        print("  LINKEDIN: DONE")
        return True

    print("  ERROR: LinkedIn post button not found")
    return False


def main():
    print("=== SOCIAL MEDIA EDUCATION POST ===\n")

    # Check server
    title = evaluate("() => document.title", timeout=15)
    print(f"Server alive. Current page: {title[:60]}")
    if "ERR" in title:
        print("ERROR: Cannot connect to Playwright MCP server")
        sys.exit(1)

    results = {}

    # Facebook (should already be at facebook.com or about:blank)
    results["facebook"] = post_facebook()

    # X/Twitter
    results["x"] = post_x()

    # LinkedIn
    results["linkedin"] = post_linkedin()

    print("\n=== FINAL SUMMARY ===")
    for p, ok in results.items():
        print(f"  {p}: {'SUCCESS' if ok else 'FAILED/PARTIAL'}")


if __name__ == "__main__":
    main()
