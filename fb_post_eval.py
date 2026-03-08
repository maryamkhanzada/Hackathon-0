#!/usr/bin/env python3
"""
Post to Facebook using ONLY browser_evaluate (pure DOM JavaScript).
No browser_run_code, no browser_snapshot, no browser_click.
Uses execCommand('insertText') for contenteditable typing.
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
    "What is the one thing you taught yourself outside of any school or course "
    "that changed your career or life? Drop it in the comments.\n\n"
    "#Education #LifelongLearning #ProfessionalDevelopment #GrowthMindset #Leadership"
)


def evaluate(js_func: str, timeout: int = 25) -> str:
    """Call browser_evaluate with a JS function string. Returns text output."""
    params = json.dumps({"function": js_func})
    cmd = [sys.executable, MCP_CLIENT, "call",
           "-u", MCP_URL, "-t", "browser_evaluate", "-p", params]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = r.stdout.decode("utf-8", errors="replace").strip()
        err = r.stderr.decode("utf-8", errors="replace").strip()
        if not out:
            return f"ERR:{err[:80]}"
        try:
            d = json.loads(out)
            return "\n".join(c.get("text", "") for c in d.get("content", [])
                             if c.get("type") == "text")
        except Exception:
            return out[:200]
    except subprocess.TimeoutExpired:
        return f"ERR:timeout{timeout}s"
    except Exception as e:
        return f"ERR:{e}"


def navigate_to(url: str) -> None:
    """Trigger navigation by setting location.href. Returns immediately."""
    # This JS runs synchronously and returns before page changes
    js = f'() => {{ try {{ window.location.replace("{url}"); }} catch(e) {{}} return "nav"; }}'
    result = evaluate(js, timeout=10)
    print(f"  Nav trigger: {result[:50]}")
    print(f"  Waiting 20s for {url[:40]} to load...")
    time.sleep(20)


def wait(seconds: int) -> None:
    print(f"  Waiting {seconds}s...")
    time.sleep(seconds)


def post_facebook() -> bool:
    print("\n=== FACEBOOK ===")

    # Check current page
    title_result = evaluate("() => document.title + ' @ ' + location.href", timeout=20)
    print(f"  Page: {title_result[:80]}")
    if "ERR:" in title_result:
        print("  ERROR: Server not responding")
        return False

    if "facebook.com" not in title_result.lower():
        navigate_to("https://www.facebook.com")
        title_result = evaluate("() => document.title", timeout=20)
        print(f"  After nav: {title_result[:60]}")

    # Step 1: Click "What's on your mind" button via JS
    print("  Step 1: Click composer...")
    click_result = evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('[role="button"]'));
        const btn = btns.find(b => b.innerText && b.innerText.toLowerCase().includes('mind'));
        if (!btn) {
            const allBtns = btns.slice(0, 5).map(b => b.innerText.substring(0, 20));
            return 'not found. first 5 btns: ' + allBtns.join('||');
        }
        btn.click();
        return 'clicked: ' + btn.innerText.substring(0, 50);
    }""", timeout=20)
    print(f"  Click: {click_result[:100]}")

    if "not found" in click_result.lower():
        # Try alternative: click by aria-label or data-testid
        click_result2 = evaluate("""() => {
            // Try "Create a post" section
            const createArea = document.querySelector('[data-pagelet="FeedComposer"]');
            if (createArea) {
                const btn = createArea.querySelector('[role="button"]');
                if (btn) { btn.click(); return 'clicked via FeedComposer'; }
            }
            // Try any button in the first section
            const firstSection = document.querySelector('div[role="region"]');
            if (firstSection) {
                const btn = firstSection.querySelector('[role="button"]');
                if (btn) { btn.click(); return 'clicked via region: ' + btn.innerText.substring(0,30); }
            }
            return 'no alternative found';
        }""", timeout=20)
        print(f"  Alt click: {click_result2[:100]}")

    wait(4)

    # Step 2: Check if editor appeared
    editor_check = evaluate("""() => {
        const ed = document.querySelector('[contenteditable="true"][role="textbox"]');
        if (!ed) return 'editor not found';
        return 'editor found: ' + ed.getAttribute('aria-label');
    }""", timeout=20)
    print(f"  Editor: {editor_check[:100]}")

    if "not found" in editor_check.lower():
        print("  WARNING: Editor not found. Trying JS click via XPath...")
        click3 = evaluate("""() => {
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.getAttribute && el.getAttribute('placeholder') &&
                    el.getAttribute('placeholder').toLowerCase().includes('mind')) {
                    el.click(); el.focus();
                    return 'clicked placeholder element';
                }
            }
            // Try contenteditable
            const ce = document.querySelector('[contenteditable]');
            if (ce) { ce.click(); ce.focus(); return 'clicked contenteditable'; }
            return 'nothing found';
        }""", timeout=20)
        print(f"  Click3: {click3[:100]}")
        wait(3)

        editor_check2 = evaluate("""() => {
            const ed = document.querySelector('[contenteditable="true"]');
            return ed ? 'editor found' : 'still no editor';
        }""", timeout=20)
        print(f"  Editor2: {editor_check2[:60]}")
        if "not found" in editor_check2.lower():
            return False

    # Step 3: Type text using execCommand
    print("  Step 3: Typing post text...")
    text_escaped = json.dumps(FB_TEXT)
    type_result = evaluate(f"""() => {{
        const ed = document.querySelector('[contenteditable="true"][role="textbox"]') ||
                   document.querySelector('[contenteditable="true"]');
        if (!ed) return 'no editor for typing';
        ed.focus();
        document.execCommand('selectAll', false, null);
        const inserted = document.execCommand('insertText', false, {text_escaped});
        if (!inserted) {{
            // Fallback: set innerHTML
            ed.innerHTML = '';
            ed.textContent = {text_escaped};
            ed.dispatchEvent(new Event('input', {{bubbles: true}}));
        }}
        return 'typed OK, len=' + ed.innerText.length;
    }}""", timeout=20)
    print(f"  Type: {type_result[:100]}")

    wait(2)

    # Step 4: Click Post button
    print("  Step 4: Click Post button...")
    post_result = evaluate("""() => {
        // Try aria-label="Post"
        const postBtns = Array.from(document.querySelectorAll('[aria-label="Post"]'));
        if (postBtns.length > 0) {
            postBtns[postBtns.length - 1].click();
            return 'clicked Post (aria-label), count=' + postBtns.length;
        }
        // Try button with text "Post"
        const allBtns = Array.from(document.querySelectorAll('[role="button"]'));
        const postBtn = allBtns.find(b => b.innerText && b.innerText.trim() === 'Post');
        if (postBtn) { postBtn.click(); return 'clicked Post (text)'; }
        // Log all buttons found
        const btnTexts = allBtns.slice(0, 10).map(b => b.innerText.substring(0, 20));
        return 'Post button not found. Buttons: ' + btnTexts.join('||');
    }""", timeout=20)
    print(f"  Post: {post_result[:150]}")

    wait(3)

    # Verify
    final = evaluate("() => document.title + ' @ ' + location.href", timeout=15)
    print(f"  Final: {final[:80]}")
    print("  FACEBOOK: DONE")
    return "Post" in post_result or "clicked" in post_result.lower()


def post_x() -> bool:
    print("\n=== X (TWITTER) ===")

    navigate_to("https://x.com")
    title = evaluate("() => document.title", timeout=15)
    print(f"  Page: {title[:60]}")

    if "x.com" not in title.lower() and "twitter" not in title.lower() and "home" not in title.lower():
        print("  Not on X, login required")
        return False

    # Click compose
    print("  Clicking compose...")
    compose = evaluate("""() => {
        const btn = document.querySelector('[data-testid="SideNav_NewTweet_Button"]') ||
                    document.querySelector('[aria-label="Post"]') ||
                    document.querySelector('[href="/compose/tweet"]');
        if (btn) { btn.click(); return 'compose clicked'; }
        return 'compose not found';
    }""", timeout=20)
    print(f"  Compose: {compose[:80]}")
    wait(2)

    # Find textarea and type
    text_escaped = json.dumps(X_TEXT)
    type_r = evaluate(f"""() => {{
        const ed = document.querySelector('[data-testid="tweetTextarea_0"]') ||
                   document.querySelector('.public-DraftEditor-content') ||
                   document.querySelector('[role="textbox"]');
        if (!ed) return 'no editor';
        ed.focus();
        document.execCommand('insertText', false, {text_escaped});
        return 'typed, len=' + (ed.innerText || ed.textContent).length;
    }}""", timeout=20)
    print(f"  Type: {type_r[:100]}")
    wait(1)

    # Post
    post_r = evaluate("""() => {
        const btn = document.querySelector('[data-testid="tweetButtonInline"]') ||
                    document.querySelector('[data-testid="tweetButton"]');
        if (btn) { btn.click(); return 'posted'; }
        return 'post btn not found';
    }""", timeout=15)
    print(f"  Post: {post_r[:80]}")
    wait(2)
    print("  X: DONE")
    return "posted" in post_r


def post_linkedin() -> bool:
    print("\n=== LINKEDIN ===")

    navigate_to("https://www.linkedin.com/feed/")
    title = evaluate("() => document.title", timeout=15)
    print(f"  Page: {title[:60]}")

    if "linkedin" not in title.lower():
        print("  Not on LinkedIn, login required")
        return False

    # Click Start a post
    print("  Clicking Start a post...")
    start = evaluate("""() => {
        const btn = document.querySelector('.share-box-feed-entry__trigger') ||
                    document.querySelector('[data-control-name="share.sharebox_focus"]') ||
                    Array.from(document.querySelectorAll('button')).find(b =>
                        b.innerText && b.innerText.toLowerCase().includes('start a post'));
        if (btn) { btn.click(); return 'start clicked: ' + btn.innerText.substring(0,30); }
        return 'start not found';
    }""", timeout=20)
    print(f"  Start: {start[:100]}")
    wait(2)

    # Type in editor
    text_escaped = json.dumps(LI_TEXT)
    type_r = evaluate(f"""() => {{
        const ed = document.querySelector('.ql-editor') ||
                   document.querySelector('[contenteditable="true"]') ||
                   document.querySelector('[role="textbox"]');
        if (!ed) return 'no editor';
        ed.focus();
        document.execCommand('insertText', false, {text_escaped});
        return 'typed, len=' + (ed.innerText || ed.textContent).length;
    }}""", timeout=20)
    print(f"  Type: {type_r[:100]}")
    wait(1)

    # Post
    post_r = evaluate("""() => {
        const btn = document.querySelector('.share-actions__primary-action') ||
                    Array.from(document.querySelectorAll('button')).find(b =>
                        b.innerText && b.innerText.trim() === 'Post');
        if (btn) { btn.click(); return 'posted'; }
        return 'post btn not found';
    }""", timeout=15)
    print(f"  Post: {post_r[:80]}")
    wait(2)
    print("  LINKEDIN: DONE")
    return "posted" in post_r


def main():
    print("=== SOCIAL MEDIA EDUCATION POST ===")
    print("Using browser_evaluate (DOM JS) only\n")

    # Sanity check
    title = evaluate("() => document.title", timeout=20)
    print(f"Server: OK. Page: {title[:60]}")
    if "ERR:" in title:
        print("FATAL: Server not responding")
        sys.exit(1)

    results = {}
    results["facebook"] = post_facebook()
    results["x"] = post_x()
    results["linkedin"] = post_linkedin()

    print("\n=== SUMMARY ===")
    for p, ok in results.items():
        print(f"  {p}: {'SUCCESS' if ok else 'FAILED/PARTIAL'}")


if __name__ == "__main__":
    main()
