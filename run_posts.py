#!/usr/bin/env python3
"""Post education content to all social media platforms via Playwright MCP."""
import json
import subprocess

MCP = "http://localhost:8808/mcp"

FB = (
    "The world has never had more access to education, and yet learning has never felt harder to sustain.\n\n"
    "Here is why: access to information is no longer the problem. Focus is.\n\n"
    "The students and professionals winning in 2026 are those who show up consistently, "
    "ask better questions, and turn every experience into a lesson.\n\n"
    "Education is not an event you attend. It is a practice you build.\n\n"
    "What is one thing you taught yourself outside of school that changed your life? "
    "Tell us in the comments.\n\n"
    "#Education #LifelongLearning #FutureOfLearning #GrowthMindset #Learning"
)

X = (
    "The most underrated investment in 2026:\n\n"
    "Spending 15 minutes a day learning something you did not know yesterday.\n\n"
    "No cost. No degree required. Just consistency.\n\n"
    "What are you learning this month?\n\n"
    "#Education #Learning #GrowthMindset"
)

LI = (
    "After years of working with high-performing teams, the single biggest differentiator "
    "is not IQ, background, or credentials.\n\n"
    "It is the commitment to keep learning after formal education ends.\n\n"
    "1. They treat curiosity as a professional skill.\n"
    "2. They learn in public - sharing accelerates growth.\n"
    "3. They apply new knowledge within 48 hours - 5x higher retention.\n"
    "4. They invest in education even when things are going well.\n\n"
    "The return on education compounds year over year, habit over habit.\n\n"
    "What does your learning habit look like this year?\n\n"
    "#Education #Leadership #ProfessionalDevelopment #GrowthMindset #LifelongLearning"
)

IG = (
    "Learning does not stop when school ends.\n\n"
    "The most successful people treat education as a daily habit, not a destination. "
    "15 minutes a day. One new concept. One better question.\n\n"
    "Your future self is built by what you learn today.\n\n"
    "What are you learning right now? Drop it below!\n\n"
    "#Education #LearnEveryDay #GrowthMindset #NeverStopLearning #Knowledge #StudyMotivation"
)


def init_session():
    r = subprocess.run(
        ["curl", "-si", "--max-time", "30", "-X", "POST", MCP,
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                      "clientInfo": {"name": "poster", "version": "1.0"}}})],
        capture_output=True, text=True
    )
    sid = ""
    for line in r.stdout.split("\n"):
        if "mcp-session-id" in line.lower():
            sid = line.split(":", 1)[1].strip()
            break
    # notifications/initialized
    subprocess.run(
        ["curl", "-N", "-s", "--max-time", "10", "-X", "POST", MCP,
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "-H", f"Mcp-Session-Id: {sid}",
         "-d", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})],
        capture_output=True, text=True
    )
    return sid


def run_js(sid, rid, code, timeout=50):
    r = subprocess.run(
        ["curl", "-N", "-s", f"--max-time", str(timeout), "-X", "POST", MCP,
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "-H", f"Mcp-Session-Id: {sid}",
         "-d", json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                           "params": {"name": "browser_run_code",
                                      "arguments": {"code": code}}})],
        capture_output=True, text=True, timeout=timeout + 5
    )
    for line in r.stdout.split("\n"):
        if line.startswith("data:"):
            d = line[5:].strip()
            if d:
                try:
                    j = json.loads(d)
                    content = j.get("result", {}).get("content", [])
                    return "\n".join(c.get("text", "") for c in content
                                     if c.get("type") == "text")
                except Exception:
                    pass
    return r.stdout[:300] or "[no response]"


def extract_result(text):
    """Pull the return value out of the ### Result block."""
    lines = text.split("\n")
    in_result = False
    for line in lines:
        if line.strip() == "### Result":
            in_result = True
            continue
        if in_result and line.strip():
            return line.strip().strip('"')
    # fallback: first non-header line
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("```"):
            return s
    return text[:80]


def main():
    sid = init_session()
    print(f"[OK] Session: {sid[:8]}...")

    # ── Facebook (browser already here) ──────────────────────────────────────
    print("\n=== FACEBOOK ===")
    fb_code = """async (page) => {
        const text = """ + json.dumps(FB) + """;
        try {
            // Click composer trigger
            let clicked = false;
            for (const sel of [
                '[aria-label="What\\'s on your mind?"]',
                '[data-pagelet="FeedComposer"] [role="button"]',
                'div.x1i10hfl[role="button"]'
            ]) {
                const el = page.locator(sel).first();
                if (await el.isVisible({timeout:2000}).catch(()=>false)) {
                    await el.click(); clicked = true; break;
                }
            }
            if (clicked) await page.waitForTimeout(1500);

            const ed = page.locator('[contenteditable="true"][role="textbox"]').first();
            if (await ed.isVisible({timeout:4000}).catch(()=>false)) {
                await ed.click();
                await ed.fill(text);
                await page.waitForTimeout(2000);
                const btn = page.locator('[aria-label="Post"]').last();
                if (await btn.isVisible({timeout:3000}).catch(()=>false)) {
                    await btn.click();
                    await page.waitForTimeout(3000);
                    return 'Facebook post published!';
                }
                return 'Facebook: text typed, Post button not found';
            }
            return 'Facebook: editor not found at ' + page.url();
        } catch(e) { return 'FB error: ' + e.message; }
    }"""
    r = run_js(sid, 2, fb_code, timeout=45)
    fb_res = extract_result(r)
    print(f"  {'[OK]' if 'published' in fb_res.lower() else '[??]'} {fb_res[:100]}")

    # ── X / Twitter ───────────────────────────────────────────────────────────
    print("\n=== X (TWITTER) ===")
    x_code = """async (page) => {
        const text = """ + json.dumps(X) + """;
        try {
            await page.goto('https://x.com/home', {waitUntil:'domcontentloaded',timeout:25000});
            await page.waitForTimeout(3000);
            const ed = page.locator('[data-testid="tweetTextarea_0"]').first();
            if (await ed.isVisible({timeout:6000}).catch(()=>false)) {
                await ed.click();
                await ed.fill(text);
                await page.waitForTimeout(1500);
                const btn = page.locator('[data-testid="tweetButtonInline"]').first();
                if (await btn.isVisible({timeout:3000}).catch(()=>false)) {
                    await btn.click();
                    await page.waitForTimeout(3000);
                    return 'X Tweet posted!';
                }
                return 'X: typed but tweetButtonInline not found';
            }
            return 'X: textarea not found, URL=' + page.url();
        } catch(e) { return 'X error: ' + e.message; }
    }"""
    r = run_js(sid, 3, x_code, timeout=55)
    x_res = extract_result(r)
    print(f"  {'[OK]' if 'posted' in x_res.lower() else '[??]'} {x_res[:100]}")

    # ── LinkedIn ──────────────────────────────────────────────────────────────
    print("\n=== LINKEDIN ===")
    li_code = """async (page) => {
        const text = """ + json.dumps(LI) + """;
        try {
            await page.goto('https://www.linkedin.com/feed/', {waitUntil:'domcontentloaded',timeout:25000});
            await page.waitForTimeout(3000);
            // Click "Start a post"
            for (const sel of ['button.share-box-feed-entry__trigger',
                                '.share-box-feed-entry__top-bar']) {
                const el = page.locator(sel).first();
                if (await el.isVisible({timeout:2000}).catch(()=>false)) {
                    await el.click(); await page.waitForTimeout(2000); break;
                }
            }
            if (!await page.locator('.share-creation-state__main').isVisible({timeout:2000}).catch(()=>false)) {
                const txt = page.getByText('Start a post', {exact:false}).first();
                if (await txt.isVisible({timeout:3000}).catch(()=>false)) {
                    await txt.click(); await page.waitForTimeout(2000);
                }
            }
            const ed = page.locator('.ql-editor,[contenteditable="true"][role="textbox"]').first();
            if (await ed.isVisible({timeout:5000}).catch(()=>false)) {
                await ed.click();
                await ed.fill(text);
                await page.waitForTimeout(2000);
                // Post button
                for (const sel of [
                    'button.share-actions__primary-action',
                    'button[class*="primary"][class*="share"]'
                ]) {
                    const btn = page.locator(sel).last();
                    if (await btn.isVisible({timeout:2000}).catch(()=>false)) {
                        await btn.click(); await page.waitForTimeout(3000);
                        return 'LinkedIn post published!';
                    }
                }
                const postBtn = page.getByRole('button', {name:'Post'}).last();
                if (await postBtn.isVisible({timeout:2000}).catch(()=>false)) {
                    await postBtn.click(); await page.waitForTimeout(3000);
                    return 'LinkedIn post published (by text)!';
                }
                return 'LinkedIn: filled, Post button not found';
            }
            return 'LinkedIn: editor not found, URL=' + page.url();
        } catch(e) { return 'LinkedIn error: ' + e.message; }
    }"""
    r = run_js(sid, 4, li_code, timeout=65)
    li_res = extract_result(r)
    print(f"  {'[OK]' if 'published' in li_res.lower() else '[??]'} {li_res[:100]}")

    # ── Instagram ─────────────────────────────────────────────────────────────
    print("\n=== INSTAGRAM ===")
    ig_code = """async (page) => {
        try {
            await page.goto('https://www.instagram.com', {waitUntil:'domcontentloaded',timeout:25000});
            await page.waitForTimeout(3000);
            for (const sel of ['[aria-label="New post"]','[aria-label="Create"]','a[href*="create"]']) {
                const el = page.locator(sel).first();
                if (await el.isVisible({timeout:3000}).catch(()=>false)) {
                    await el.click(); await page.waitForTimeout(2000);
                    return 'Instagram: New post dialog opened - attach image then submit caption';
                }
            }
            return 'Instagram: New post button not found - manual post required (image needed). Caption in Plans/FB_IG_DRAFT_IG_20260303_0900.md';
        } catch(e) { return 'IG error: ' + e.message; }
    }"""
    r = run_js(sid, 5, ig_code, timeout=40)
    ig_res = extract_result(r)
    print(f"  {'[OK]' if 'opened' in ig_res.lower() else '[!!]'} {ig_res[:120]}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("SUMMARY")
    print("=" * 55)
    for name, res in [("Facebook", fb_res), ("X/Twitter", x_res),
                      ("LinkedIn", li_res), ("Instagram", ig_res)]:
        ok = any(w in res.lower() for w in ["published", "posted", "opened", "click"])
        print(f"  [{'OK' if ok else '!!'}] {name:12s}: {res[:75]}")


if __name__ == "__main__":
    main()
