#!/usr/bin/env python3
"""Social media education post automation via Playwright MCP."""
import json
import subprocess
import sys

MCP = "http://localhost:8808/mcp"
SESSION = None
RID = 0

# ── Post content ──────────────────────────────────────────────────────────────

FB_POST = """The world has never had more access to education — and yet learning has never felt harder to sustain.

Here's why: access to information is no longer the problem. Focus is.

The students and professionals winning in 2026 are those who show up consistently, ask better questions, and turn every experience into a lesson.

Education isn't an event you attend. It's a practice you build.

🎓 What's one thing you've taught yourself outside of school that changed your life?

Tell us in the comments — let's build a resource list together.

#Education #LifelongLearning #FutureOfLearning #GrowthMindset #Learning"""

X_POST = """The most underrated investment in 2026:

Spending 15 minutes a day learning something you don't know yet.

No cost. No degree required. Just consistency.

What are you learning this month?

#Education #Learning #GrowthMindset"""

LINKEDIN_POST = """After years of working with high-performing teams, the single biggest differentiator isn't IQ, background, or credentials.

It's the commitment to keep learning after formal education ends.

Here's what that looks like in practice:

1. They treat curiosity as a professional skill.
The best people in any room ask more questions than they answer.

2. They learn in public.
Sharing what you're figuring out accelerates growth faster than private study.

3. They connect learning to application immediately.
New knowledge applied within 48 hours has 5x higher retention.

4. They invest in education even when things are going well.
The biggest learning gaps form during periods of success.

The return on education isn't measured in a single quarter. It compounds — year over year, habit over habit.

What does your learning habit look like this year?

#Education #Leadership #ProfessionalDevelopment #GrowthMindset #Upskilling #LifelongLearning"""

IG_CAPTION = """Learning doesn't stop when school ends. 📚

The most successful people treat education as a daily habit, not a destination. 15 minutes a day. One new concept. One better question.

Your future self is built by what you learn today.

What are you learning right now? Drop it below 👇

#Education #LearnEveryDay #GrowthMindset #NeverStopLearning #Knowledge #StudyMotivation #PersonalGrowth"""

# ── MCP transport ─────────────────────────────────────────────────────────────

def curl_mcp(payload, timeout=60):
    global SESSION, RID
    RID += 1
    if "id" in payload:
        payload["id"] = RID

    cmd = [
        "curl", "-N", "-s", f"--max-time", str(timeout),
        "-X", "POST", MCP,
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json, text/event-stream",
    ]
    if SESSION:
        cmd += ["-H", f"Mcp-Session-Id: {SESSION}"]
    cmd += ["-d", json.dumps(payload)]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    body = r.stdout.strip()
    if not body:
        return {}
    for line in body.split("\n"):
        if line.startswith("data:"):
            d = line[5:].strip()
            if d:
                try:
                    return json.loads(d)
                except Exception:
                    pass
    try:
        return json.loads(body)
    except Exception:
        return {}


def init_session():
    global SESSION
    r = subprocess.run([
        "curl", "-si", "--max-time", "30",
        "-X", "POST", MCP,
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json, text/event-stream",
        "-d", json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "social-poster", "version": "1.0"}}
        })
    ], capture_output=True, text=True)
    for line in r.stdout.split("\n"):
        if "mcp-session-id" in line.lower():
            SESSION = line.split(":", 1)[1].strip()
            break
    print(f"[OK] Session: {SESSION[:8] if SESSION else 'NONE'}...")
    # initialized notification
    curl_mcp({"jsonrpc": "2.0", "method": "notifications/initialized"})


def tool(name, args, timeout=60):
    resp = curl_mcp({
        "jsonrpc": "2.0", "id": 0,
        "method": "tools/call",
        "params": {"name": name, "arguments": args}
    }, timeout=timeout)
    res = resp.get("result", {})
    content = res.get("content", [])
    text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    is_err = res.get("isError", False)
    return text, is_err


def navigate(url):
    print(f"  -> {url}")
    text, _ = tool("browser_navigate", {"url": url}, timeout=30)
    # Extract page title from response
    for line in text.split("\n"):
        if "Page Title:" in line:
            print(f"     Page: {line.strip()}")
            break


def wait(ms=2000):
    tool("browser_wait_for", {"time": ms}, timeout=30)


def run_js(code, timeout=45):
    text, err = tool("browser_run_code", {"code": code}, timeout=timeout)
    # Extract the result line
    for line in text.split("\n"):
        if line.startswith("### Result"):
            pass
        elif line.startswith('"') or line.startswith("'"):
            return line.strip().strip('"\''), err
    # Fallback: return first meaningful line
    lines = [l for l in text.split("\n") if l.strip() and not l.startswith("#") and not l.startswith("```")]
    return lines[0] if lines else text[:100], err


# ── Platform posting ──────────────────────────────────────────────────────────

def post_facebook():
    print("\n=== FACEBOOK ===")
    navigate("https://www.facebook.com")
    wait(3000)

    result, err = run_js(f"""async (page) => {{
        const text = {json.dumps(FB_POST)};
        // Click "What's on your mind?" composer
        const comp = page.locator('[aria-label="What\\'s on your mind?"]').first();
        if (await comp.isVisible({{timeout:4000}}).catch(()=>false)) {{
            await comp.click();
            await page.waitForTimeout(1500);
        }}
        // Fill the contenteditable
        const ed = page.locator('[contenteditable="true"][role="textbox"]').first();
        if (await ed.isVisible({{timeout:4000}}).catch(()=>false)) {{
            await ed.click();
            await ed.fill(text);
            await page.waitForTimeout(1500);
            const btn = page.locator('[aria-label="Post"]').last();
            if (await btn.isVisible({{timeout:3000}}).catch(()=>false)) {{
                await btn.click();
                await page.waitForTimeout(2000);
                return 'Facebook post published!';
            }}
            return 'Facebook: filled, Post button not found';
        }}
        return 'Facebook: composer not found';
    }}""", timeout=50)
    print(f"  [{('ERR' if err else 'OK')}] {result[:100]}")
    return result


def post_x():
    print("\n=== X (TWITTER) ===")
    navigate("https://x.com/home")
    wait(3000)

    result, err = run_js(f"""async (page) => {{
        const text = {json.dumps(X_POST)};
        const ed = page.locator('[data-testid="tweetTextarea_0"]').first();
        if (await ed.isVisible({{timeout:5000}}).catch(()=>false)) {{
            await ed.click();
            await ed.fill(text);
            await page.waitForTimeout(1500);
            const btn = page.locator('[data-testid="tweetButtonInline"]').first();
            if (await btn.isVisible({{timeout:3000}}).catch(()=>false)) {{
                await btn.click();
                await page.waitForTimeout(2000);
                return 'X Tweet posted!';
            }}
            return 'X: filled, post button not found';
        }}
        return 'X: tweet editor not found';
    }}""", timeout=50)
    print(f"  [{('ERR' if err else 'OK')}] {result[:100]}")
    return result


def post_linkedin():
    print("\n=== LINKEDIN ===")
    navigate("https://www.linkedin.com/feed/")
    wait(3000)

    result, err = run_js(f"""async (page) => {{
        const text = {json.dumps(LINKEDIN_POST)};
        const startBtn = page.getByText('Start a post', {{exact:false}}).first();
        if (await startBtn.isVisible({{timeout:5000}}).catch(()=>false)) {{
            await startBtn.click();
            await page.waitForTimeout(2000);
        }}
        const ed = page.locator('.ql-editor, [role="textbox"][contenteditable="true"]').first();
        if (await ed.isVisible({{timeout:5000}}).catch(()=>false)) {{
            await ed.click();
            await ed.fill(text);
            await page.waitForTimeout(1500);
            const btn = page.getByRole('button', {{name: /^Post$/}}).last();
            if (await btn.isVisible({{timeout:3000}}).catch(()=>false)) {{
                await btn.click();
                await page.waitForTimeout(2000);
                return 'LinkedIn post published!';
            }}
            const btn2 = page.locator('button.share-actions__primary-action').last();
            if (await btn2.isVisible({{timeout:2000}}).catch(()=>false)) {{
                await btn2.click();
                return 'LinkedIn post published (alt button)!';
            }}
            return 'LinkedIn: filled, Post button not found';
        }}
        return 'LinkedIn: editor not found';
    }}""", timeout=60)
    print(f"  [{('ERR' if err else 'OK')}] {result[:100]}")
    return result


def post_instagram():
    print("\n=== INSTAGRAM ===")
    navigate("https://www.instagram.com")
    wait(3000)

    result, err = run_js(f"""async (page) => {{
        const btn = page.locator('[aria-label="New post"], svg[aria-label="New post"]').first();
        if (await btn.isVisible({{timeout:5000}}).catch(()=>false)) {{
            await btn.click();
            await page.waitForTimeout(2000);
            return 'Instagram: New post dialog opened. Attach image, then paste caption.';
        }}
        const createLink = page.locator('a[href*="create"]').first();
        if (await createLink.isVisible({{timeout:3000}}).catch(()=>false)) {{
            await createLink.click();
            return 'Instagram: create page opened';
        }}
        return 'Instagram requires image upload — manual post needed. Caption ready in Plans/FB_IG_DRAFT_IG_20260303_0900.md';
    }}""", timeout=30)
    print(f"  [{('ERR' if err else 'OK')}] {result[:120]}")
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Starting education post automation (Playwright MCP)...")
    init_session()

    fb = post_facebook()
    x  = post_x()
    li = post_linkedin()
    ig = post_instagram()

    print("\n" + "="*55)
    print("SUMMARY")
    print("="*55)
    for name, res in [("Facebook", fb), ("X/Twitter", x), ("LinkedIn", li), ("Instagram", ig)]:
        ok = any(w in str(res).lower() for w in ["published", "posted", "click", "opened"])
        print(f"  [{'✓' if ok else '?'}] {name:12s}: {str(res)[:70]}")


if __name__ == "__main__":
    main()
