#!/usr/bin/env python3
"""Post to Facebook directly using browser_click + browser_type with snapshot refs."""
import json, subprocess, re, sys

MCP = "http://localhost:8808/mcp"

FB_TEXT = (
    "Education is not an event you attend. It is a practice you build.\n\n"
    "The professionals winning in 2026 show up consistently, ask better questions, "
    "and turn every experience into a lesson.\n\n"
    "What is one thing you taught yourself outside school that changed your life? "
    "Share in the comments.\n\n"
    "#Education #LifelongLearning #GrowthMindset #Learning #FutureOfLearning"
)

def curl(sid, payload, timeout=20):
    cmd = ["curl", "-N", "-s", f"--max-time", str(timeout),
           "-X", "POST", MCP,
           "-H", "Content-Type: application/json",
           "-H", "Accept: application/json, text/event-stream"]
    if sid:
        cmd += ["-H", f"Mcp-Session-Id: {sid}"]
    cmd += ["-d", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    stdout = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
    for line in stdout.split("\n"):
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except Exception:
                pass
    return {}

def get_content(resp):
    return "\n".join(c.get("text", "") for c in
                     resp.get("result", {}).get("content", [])
                     if c.get("type") == "text")

def tool(sid, name, args, timeout=20):
    return curl(sid, {"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                      "params": {"name": name, "arguments": args}}, timeout)

def init():
    r = subprocess.run(
        ["curl", "-si", "--max-time", "30", "-X", "POST", MCP,
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "clientInfo": {"name": "fb-poster", "version": "1"}}})],
        capture_output=True)
    stdout = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
    sid = ""
    for line in stdout.split("\n"):
        if "mcp-session-id" in line.lower():
            sid = line.split(":", 1)[1].strip()
            break
    curl(sid, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return sid

def find_ref(snapshot_text, keywords, prefer="button"):
    """Find a ref= value from snapshot text matching keywords, preferring 'prefer' element type."""
    best_ref, best_line = None, None
    for line in snapshot_text.split("\n"):
        lw = line.lower()
        if any(k in lw for k in keywords) and "ref=" in line:
            m = re.search(r"\[ref=(e\d+)\]", line)
            if m:
                if best_ref is None:
                    best_ref, best_line = m.group(1), line.strip()
                # Prefer button/textbox elements over generic divs
                if prefer in lw:
                    return m.group(1), line.strip()
    return best_ref, best_line

def main():
    sid = init()
    print(f"Session: {sid[:8]}")

    # Step 1: Snapshot to find composer button
    print("Step 1: Snapshot to find composer...")
    resp = tool(sid, "browser_snapshot", {}, timeout=15)
    snap = get_content(resp)
    if not snap:
        print("ERROR: Empty snapshot"); sys.exit(1)

    # Find "What's on your mind" button
    ref, line = find_ref(snap, ["what's on your mind", "mind, "])
    if not ref:
        # Try "Create a post"
        ref, line = find_ref(snap, ["create a post"])
    print(f"Composer ref: {ref} — {line[:80] if line else 'NOT FOUND'}")

    if not ref:
        print("ERROR: Composer button not found. Printing first 300 chars of snapshot:")
        print(snap[:300])
        sys.exit(1)

    # Step 2: Click the composer button
    print("Step 2: Clicking composer button...")
    resp = tool(sid, "browser_click",
                {"element": "What's on your mind", "ref": ref}, timeout=15)
    print("Click result:", get_content(resp)[:80])

    # Step 3: Wait for dialog to open
    tool(sid, "browser_wait_for", {"time": 2500})

    # Step 4: Snapshot to find textbox in dialog
    print("Step 3: Snapshot to find textbox in dialog...")
    resp = tool(sid, "browser_snapshot", {}, timeout=15)
    snap2 = get_content(resp)
    print(f"  Snapshot length: {len(snap2)} chars")

    # Print lines with dialog/textbox indicators
    for l in snap2.split("\n"):
        lw = l.lower()
        if any(k in lw for k in ["textbox", "dialog", "mind", "post", "contenteditable"]):
            print(" ", l[:120])

    txt_ref, txt_line = find_ref(snap2, ["textbox", "what's on your mind", "mind,"], prefer="textbox")
    print(f"Textbox ref: {txt_ref} — {txt_line[:80] if txt_line else 'NOT FOUND'}")

    if not txt_ref:
        print("First 40 snapshot lines:")
        for l in snap2.split("\n")[:40]:
            print(" ", l)
        sys.exit(1)

    # Step 5: Type text into the textbox
    print("Step 4: Typing post text...")
    resp = tool(sid, "browser_type",
                {"element": "Post text area", "ref": txt_ref,
                 "text": FB_TEXT, "submit": False},
                timeout=30)
    print("Type result:", get_content(resp)[:80])

    # Step 6: Wait
    tool(sid, "browser_wait_for", {"time": 1000})

    # Step 7: Snapshot to find Post button
    print("Step 5: Snapshot to find Post button...")
    resp = tool(sid, "browser_snapshot", {}, timeout=15)
    snap3 = get_content(resp)
    post_ref, post_line = find_ref(snap3, ['"post"'])
    if not post_ref:
        # Try button with label "Post"
        for line in snap3.split("\n"):
            if 'button "Post"' in line and "ref=" in line:
                m = re.search(r"\[ref=(e\d+)\]", line)
                if m:
                    post_ref, post_line = m.group(1), line.strip()
                    break
    print(f"Post button ref: {post_ref} — {post_line[:80] if post_line else 'NOT FOUND'}")

    if not post_ref:
        print("Post button not found. Looking for any button with 'post' text...")
        for l in snap3.split("\n"):
            if "post" in l.lower() and "button" in l.lower():
                print(" ", l[:100])
        sys.exit(1)

    # Step 8: Click Post
    print("Step 6: Clicking Post button...")
    resp = tool(sid, "browser_click",
                {"element": "Post", "ref": post_ref}, timeout=15)
    print("Post click result:", get_content(resp)[:100])

    tool(sid, "browser_wait_for", {"time": 3000})

    print("\nFACEBOOK: Education post submitted!")
    print(f"Session ID saved: {sid}")

    # Save session for other platforms
    with open("/tmp/mcp_active_session.txt", "w") as f:
        f.write(sid)

if __name__ == "__main__":
    main()
