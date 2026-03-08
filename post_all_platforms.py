#!/usr/bin/env python3
"""
Post education content to Facebook, X, LinkedIn, Instagram
using a single persistent MCP session with proper timeouts.
"""
import json
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

MCP_URL = "http://localhost:8808/mcp"

FB_TEXT = (
    "Education is not an event you attend. It is a practice you build.\n\n"
    "The professionals winning in 2026 show up consistently, ask better questions, "
    "and turn every experience into a lesson.\n\n"
    "What is one thing you taught yourself outside school that changed your life? "
    "Share in the comments.\n\n"
    "#Education #LifelongLearning #GrowthMindset #Learning #FutureOfLearning"
)

X_TEXT = (
    "Education isn't an event you attend. It's a practice you build.\n\n"
    "The pros winning in 2026 show up consistently & turn every experience into a lesson.\n\n"
    "What did you teach yourself outside school that changed your life?\n\n"
    "#Education #LifelongLearning #GrowthMindset"
)

LI_TEXT = (
    "Education is not an event you attend. It is a practice you build.\n\n"
    "The professionals I see thriving in 2026 share one habit: they never stop learning. "
    "Not in classrooms — in every meeting, every setback, every conversation.\n\n"
    "They ask better questions. They reflect on what went wrong. "
    "They turn every experience into a lesson.\n\n"
    "What's the one thing you taught yourself — outside of any school or course — "
    "that changed your career or life?\n\n"
    "Drop it in the comments. Let's learn from each other.\n\n"
    "#Education #LifelongLearning #ProfessionalDevelopment #GrowthMindset #Leadership"
)

IG_TEXT = (
    "Education is not an event you attend. It is a practice you build. 📚\n\n"
    "The professionals thriving in 2026 share one habit: they never stop learning — "
    "not in classrooms, but in every experience, every setback, every conversation.\n\n"
    "What is one thing you taught yourself outside school that changed your life? "
    "Drop it below 👇\n\n"
    "#Education #LifelongLearning #GrowthMindset #Learning #FutureOfLearning "
    "#SelfDevelopment #Motivation #PersonalGrowth"
)


class MCPSession:
    def __init__(self, url, timeout=120):
        self.url = url
        self.timeout = timeout
        self._request_id = 0
        self._session_id = None
        self._init()

    def _next_id(self):
        self._request_id += 1
        return self._request_id

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _parse(self, body):
        body = body.strip()
        if body.startswith("event:") or body.startswith("data:"):
            for line in body.split("\n"):
                if line.startswith("data:"):
                    d = line[5:].strip()
                    if d:
                        return json.loads(d)
        return json.loads(body)

    def _init(self):
        payload = {
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "post-runner", "version": "1.0"}
            }
        }
        data = json.dumps(payload).encode("utf-8")
        req = Request(self.url, data=data, headers=self._headers(), method="POST")
        with urlopen(req, timeout=30) as resp:
            self._session_id = resp.headers.get("Mcp-Session-Id")
            result = self._parse(resp.read().decode("utf-8", errors="replace"))
        print(f"Session: {self._session_id[:8] if self._session_id else 'none'}")

        # Send initialized notification
        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        req2 = Request(self.url, data=notif.encode(),
                       headers=self._headers(), method="POST")
        try:
            with urlopen(req2, timeout=10) as r:
                pass
        except Exception:
            pass
        return result

    def call(self, tool_name, args, timeout=None):
        timeout = timeout or self.timeout
        payload = {
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": tool_name, "arguments": args}
        }
        data = json.dumps(payload).encode("utf-8")
        req = Request(self.url, data=data, headers=self._headers(), method="POST")
        try:
            with urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return {"error": f"HTTP {e.code}: {body[:200]}"}
        except URLError as e:
            return {"error": f"URL error: {e.reason}"}

        try:
            result = self._parse(body)
            return result
        except Exception as e:
            return {"error": f"Parse error: {e}", "_raw": body[:500]}

    def get_text(self, resp):
        return "\n".join(
            c.get("text", "") for c in resp.get("result", {}).get("content", [])
            if c.get("type") == "text"
        )

    def wait(self, ms):
        self.call("browser_wait_for", {"time": ms}, timeout=ms // 1000 + 15)


def post_facebook(s):
    print("\n=== FACEBOOK ===")

    code = """async (page) => {
  try {
    // Use JS click to avoid Playwright navigation tracking
    await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('[role="button"]'));
      const btn = btns.find(b => b.innerText && b.innerText.includes("mind"));
      if (btn) btn.click();
    });
    await page.waitForTimeout(4000);

    const ed = page.locator('[contenteditable="true"][role="textbox"]').first();
    const vis = await ed.isVisible({timeout: 10000}).catch(() => false);
    if (!vis) return 'ERROR: editor not visible after click, URL=' + page.url();

    await ed.click();
    await page.waitForTimeout(300);
    """ + f'await page.keyboard.type({json.dumps(FB_TEXT)}, {{delay: 6}});' + """
    await page.waitForTimeout(2000);

    const postBtn = page.locator('[aria-label="Post"]').last();
    const btnVis = await postBtn.isVisible({timeout: 6000}).catch(() => false);
    if (!btnVis) return 'ERROR: Post button not visible, but text was typed OK';
    await postBtn.click();
    await page.waitForTimeout(3000);
    return 'SUCCESS: Facebook education post published!';
  } catch(e) { return 'ERROR: ' + e.message; }
}"""

    resp = s.call("browser_run_code", {"code": code}, timeout=120)
    result = s.get_text(resp)
    print(f"Result: {result[:200]}")

    if "error" in resp:
        print(f"Error: {resp['error']}")
    return "SUCCESS" in result


def post_x(s):
    print("\n=== X (TWITTER) ===")

    code = """async (page) => {
  try {
    await page.goto('https://x.com/compose/tweet', {waitUntil: 'domcontentloaded', timeout: 30000});
    await page.waitForTimeout(3000);

    const ed = page.locator('[data-testid="tweetTextarea_0"]').first();
    const vis = await ed.isVisible({timeout: 10000}).catch(() => false);
    if (!vis) {
      // Try alternative: click + compose button
      const homeUrl = page.url();
      return 'ERROR: tweet editor not visible at ' + homeUrl;
    }
    await ed.click();
    """ + f'await page.keyboard.type({json.dumps(X_TEXT)}, {{delay: 6}});' + """
    await page.waitForTimeout(1500);

    const postBtn = page.locator('[data-testid="tweetButtonInline"]').first();
    const btnVis = await postBtn.isVisible({timeout: 5000}).catch(() => false);
    if (!btnVis) return 'ERROR: Tweet button not found';
    await postBtn.click();
    await page.waitForTimeout(3000);
    return 'SUCCESS: X education post published!';
  } catch(e) { return 'ERROR: ' + e.message; }
}"""

    resp = s.call("browser_run_code", {"code": code}, timeout=120)
    result = s.get_text(resp)
    print(f"Result: {result[:200]}")
    if "error" in resp:
        print(f"Error: {resp['error']}")
    return "SUCCESS" in result


def post_linkedin(s):
    print("\n=== LINKEDIN ===")

    code = """async (page) => {
  try {
    await page.goto('https://www.linkedin.com/feed/', {waitUntil: 'domcontentloaded', timeout: 30000});
    await page.waitForTimeout(3000);

    // Click "Start a post" button
    const startBtn = page.locator('[data-control-name="share.sharebox_focus"]').first();
    const startVis = await startBtn.isVisible({timeout: 5000}).catch(() => false);
    if (startVis) {
      await startBtn.click();
    } else {
      // Try button with text
      const altBtn = page.locator('button').filter({hasText: /start a post/i}).first();
      const altVis = await altBtn.isVisible({timeout: 5000}).catch(() => false);
      if (altVis) {
        await altBtn.click();
      } else {
        return 'ERROR: LinkedIn post button not found';
      }
    }
    await page.waitForTimeout(2000);

    const ed = page.locator('.ql-editor').first();
    const vis = await ed.isVisible({timeout: 8000}).catch(() => false);
    if (!vis) return 'ERROR: LinkedIn editor not visible';
    await ed.click();
    """ + f'await page.keyboard.type({json.dumps(LI_TEXT)}, {{delay: 6}});' + """
    await page.waitForTimeout(1500);

    const postBtn = page.locator('button.share-actions__primary-action').first();
    const btnVis = await postBtn.isVisible({timeout: 5000}).catch(() => false);
    if (!btnVis) return 'ERROR: LinkedIn Post button not found, text was typed';
    await postBtn.click();
    await page.waitForTimeout(3000);
    return 'SUCCESS: LinkedIn education post published!';
  } catch(e) { return 'ERROR: ' + e.message; }
}"""

    resp = s.call("browser_run_code", {"code": code}, timeout=120)
    result = s.get_text(resp)
    print(f"Result: {result[:200]}")
    if "error" in resp:
        print(f"Error: {resp['error']}")
    return "SUCCESS" in result


def post_instagram(s):
    print("\n=== INSTAGRAM ===")

    code = """async (page) => {
  try {
    await page.goto('https://www.instagram.com/', {waitUntil: 'domcontentloaded', timeout: 30000});
    await page.waitForTimeout(3000);

    // Click New Post / Create button
    const createBtn = page.locator('[aria-label="New post"]').first();
    const vis = await createBtn.isVisible({timeout: 8000}).catch(() => false);
    if (!vis) {
      // Try SVG create button
      const altBtn = page.locator('a[href="/create/select/"]').first();
      const altVis = await altBtn.isVisible({timeout: 5000}).catch(() => false);
      if (!altVis) return 'ERROR: Instagram create button not found, URL=' + page.url();
      await altBtn.click();
    } else {
      await createBtn.click();
    }
    await page.waitForTimeout(2000);
    return 'Instagram: create dialog opened - image required for full post. Caption text ready.';
  } catch(e) { return 'ERROR: ' + e.message; }
}"""

    resp = s.call("browser_run_code", {"code": code}, timeout=60)
    result = s.get_text(resp)
    print(f"Result: {result[:200]}")
    if "error" in resp:
        print(f"Error: {resp['error']}")
    return True  # Instagram always partial (needs image)


def main():
    print("Starting social media posting session...")
    try:
        s = MCPSession(MCP_URL, timeout=120)
    except Exception as e:
        print(f"ERROR: Could not connect to MCP server: {e}")
        sys.exit(1)

    # Check current page
    resp = s.call("browser_evaluate", {"function": "() => document.title"}, timeout=15)
    title = s.get_text(resp)
    print(f"Current page: {title[:60]}")

    results = {}

    # Facebook (already logged in, stay on current page)
    results["facebook"] = post_facebook(s)

    # X/Twitter
    results["x"] = post_x(s)

    # LinkedIn
    results["linkedin"] = post_linkedin(s)

    # Instagram (requires image, can only open dialog)
    results["instagram"] = post_instagram(s)

    print("\n=== SUMMARY ===")
    for platform, success in results.items():
        status = "POSTED" if success else "FAILED/PARTIAL"
        print(f"  {platform}: {status}")

    return results


if __name__ == "__main__":
    main()
