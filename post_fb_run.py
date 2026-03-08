#!/usr/bin/env python3
"""Post to Facebook using browser_run_code - single atomic call."""
import json, subprocess, sys

MCP = "http://localhost:8808/mcp"

FB_TEXT = (
    "Education is not an event you attend. It is a practice you build.\n\n"
    "The professionals winning in 2026 show up consistently, ask better questions, "
    "and turn every experience into a lesson.\n\n"
    "What is one thing you taught yourself outside school that changed your life? "
    "Share in the comments.\n\n"
    "#Education #LifelongLearning #GrowthMindset #Learning #FutureOfLearning"
)

# Escape the text for JS string embedding
FB_TEXT_JS = FB_TEXT.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

CODE = r"""async (page) => {
  try {
    // Click composer via JS evaluate to avoid Playwright navigation detection
    await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('[role="button"]'));
      const btn = btns.find(b => b.innerText && b.innerText.includes("mind"));
      if (btn) btn.click();
    });
    await page.waitForTimeout(3500);

    // Find contenteditable textbox
    const ed = page.locator('[contenteditable="true"][role="textbox"]').first();
    const vis = await ed.isVisible({timeout: 8000}).catch(() => false);
    if (!vis) {
      const url = page.url();
      return 'editor not visible, URL=' + url;
    }

    await ed.click();
    await page.waitForTimeout(500);
    """ + f'await page.keyboard.type({json.dumps(FB_TEXT)}, {{delay: 8}});' + r"""
    await page.waitForTimeout(1500);

    // Find Post button
    const postBtn = page.locator('[aria-label="Post"]').last();
    const btnVis = await postBtn.isVisible({timeout: 5000}).catch(() => false);
    if (!btnVis) return 'Post button not visible, text was typed OK';
    await postBtn.click();
    await page.waitForTimeout(2500);
    return 'Facebook: Education post published!';
  } catch(e) { return 'FB error: ' + e.message; }
}"""


def curl(sid, payload, timeout=30):
    cmd = ["curl", "-N", "-s", "--max-time", str(timeout),
           "-X", "POST", MCP,
           "-H", "Content-Type: application/json",
           "-H", "Accept: application/json, text/event-stream"]
    if sid:
        cmd += ["-H", f"Mcp-Session-Id: {sid}"]
    cmd += ["-d", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    stdout = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
    for line in stdout.split("\n"):
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except Exception:
                pass
    return {"_raw": stdout[:500]}


def get_text(resp):
    return "\n".join(
        c.get("text", "") for c in resp.get("result", {}).get("content", [])
        if c.get("type") == "text"
    )


def init():
    r = subprocess.run(
        ["curl", "-si", "--max-time", "30", "-X", "POST", MCP,
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "clientInfo": {"name": "fbrunner", "version": "1"}}})],
        capture_output=True)
    stdout = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
    sid = ""
    for line in stdout.split("\n"):
        if "mcp-session-id" in line.lower():
            sid = line.split(":", 1)[1].strip()
            break
    curl(sid, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return sid


def main():
    sid = init()
    print(f"Session: {sid[:8]}")

    # Verify session is alive
    ping = curl(sid, {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                      "params": {"name": "browser_evaluate",
                                 "arguments": {"function": "return document.title"}}},
                timeout=15)
    title = get_text(ping)
    print(f"Page title: {title[:80]}")
    if "Session not found" in str(ping) or not title:
        print("ERROR: Session invalid or page title empty")
        print("Raw:", ping)
        sys.exit(1)

    print("\nRunning FB post code (90s timeout)...")
    resp = curl(sid, {"jsonrpc": "2.0", "id": 20, "method": "tools/call",
                      "params": {"name": "browser_run_code",
                                 "arguments": {"code": CODE}}},
                timeout=90)
    result = get_text(resp)
    print(f"Result: {result}")

    if "published" in result.lower():
        print("\nFACEBOOK POST: SUCCESS")
    else:
        print("\nFACEBOOK POST: Check result above")
        raw = resp.get("_raw", "")
        if raw:
            print("Raw response:", raw[:300])


if __name__ == "__main__":
    main()
