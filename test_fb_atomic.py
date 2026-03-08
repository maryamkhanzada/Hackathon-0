#!/usr/bin/env python3
"""Test atomic tool calls to post to Facebook."""
import json, sys, re
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

MCP_URL = "http://localhost:8808/mcp"

class S:
    def __init__(self):
        self._id = 0
        self._sid = None
        payload = {"jsonrpc":"2.0","id":1,"method":"initialize",
                   "params":{"protocolVersion":"2024-11-05","capabilities":{},
                             "clientInfo":{"name":"atomic","version":"1"}}}
        req = Request(MCP_URL, json.dumps(payload).encode(),
                      {"Content-Type":"application/json","Accept":"application/json, text/event-stream"},
                      method="POST")
        with urlopen(req, timeout=30) as r:
            self._sid = r.headers.get("Mcp-Session-Id")
            r.read()
        print(f"Session: {self._sid[:8] if self._sid else 'NONE'}")
        # notify
        notif = json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})
        req2 = Request(MCP_URL, notif.encode(),
                       {"Content-Type":"application/json","Accept":"application/json, text/event-stream",
                        "Mcp-Session-Id": self._sid}, method="POST")
        try:
            with urlopen(req2, timeout=10) as r: r.read()
        except: pass

    def h(self):
        return {"Content-Type":"application/json","Accept":"application/json, text/event-stream",
                "Mcp-Session-Id": self._sid}

    def call(self, tool, args, timeout=30):
        self._id += 1
        payload = {"jsonrpc":"2.0","id":self._id,"method":"tools/call",
                   "params":{"name":tool,"arguments":args}}
        req = Request(MCP_URL, json.dumps(payload).encode(), self.h(), method="POST")
        try:
            with urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8","replace")
        except HTTPError as e:
            return {"_err": f"HTTP{e.code}:{e.read().decode()[:100]}"}
        except Exception as e:
            return {"_err": str(e)}
        body = body.strip()
        for line in body.split("\n"):
            if line.startswith("data:"):
                try: return json.loads(line[5:].strip())
                except: pass
        return {"_raw": body[:200]}

    def txt(self, r):
        return "\n".join(c.get("text","") for c in r.get("result",{}).get("content",[]) if c.get("type")=="text")

s = S()

# Verify alive
r = s.call("browser_evaluate", {"function":"() => document.title"}, timeout=20)
title = s.txt(r)[:60]
print(f"Title: {title}")
if "_err" in r:
    print(f"ERROR: {r['_err']}"); sys.exit(1)

# Snapshot to find button
print("\nStep 1: Snapshot...")
r = s.call("browser_snapshot", {}, timeout=30)
snap = s.txt(r)
print(f"  Snapshot: {len(snap)} chars")
if len(snap) == 0:
    print("  ERROR: empty snapshot"); sys.exit(1)

# Find the button just above e355 (the generic 'mind' text)
lines = snap.split("\n")
btn_ref = None
for i, line in enumerate(lines):
    if "e355" in line and "mind" in line.lower():
        for j in range(i-1, max(0,i-6), -1):
            m = re.search(r'button \[ref=(e\d+)\]', lines[j])
            if m:
                btn_ref = m.group(1)
                print(f"  Composer button: {btn_ref} (line: {lines[j].strip()[:80]})")
                break
        break

if not btn_ref:
    print("  Button not found by e355 proximity. Trying generic search...")
    for line in lines:
        if "mind" in line.lower() and "ref=" in line:
            m = re.search(r'\[ref=(e\d+)\]', line)
            if m:
                btn_ref = m.group(1)
                print(f"  Found via text: {btn_ref} — {line.strip()[:80]}")
                break

if not btn_ref:
    print("  ERROR: no button ref found")
    sys.exit(1)

# Click button
print(f"\nStep 2: Clicking {btn_ref}...")
r = s.call("browser_click", {"element":"What's on your mind", "ref": btn_ref}, timeout=20)
print(f"  Click: {s.txt(r)[:60] or repr(r)[:60]}")

# Wait for dialog
print("\nStep 3: Waiting 4s for dialog...")
s.call("browser_wait_for", {"time": 4000}, timeout=15)

# Check session alive
print("\nStep 4: Session alive check...")
r = s.call("browser_evaluate", {"function":"() => document.title"}, timeout=20)
t = s.txt(r)
print(f"  After click, title: {t[:60]}")
if "_err" in r:
    print(f"  ERROR: {r['_err']}")
    # Check if session expired
    if "404" in str(r["_err"]) or "not found" in str(r["_err"]).lower():
        print("  SESSION DIED after click!")
    sys.exit(1)

# Snapshot for textbox
print("\nStep 5: Snapshot for textbox...")
r = s.call("browser_snapshot", {}, timeout=30)
snap2 = s.txt(r)
print(f"  Snapshot2: {len(snap2)} chars")

if len(snap2) < 50:
    print("  Empty snapshot after click. Dumping raw:")
    print(repr(r)[:300])
    sys.exit(1)

# Look for textbox
txt_ref = None
for line in snap2.split("\n"):
    if "textbox" in line.lower() and "ref=" in line:
        m = re.search(r'\[ref=(e\d+)\]', line)
        if m:
            txt_ref = m.group(1)
            print(f"  Textbox: {txt_ref} — {line.strip()[:80]}")
            break

if not txt_ref:
    print("  No textbox found. Dialog lines:")
    for line in snap2.split("\n"):
        if any(k in line.lower() for k in ["dialog","textbox","contenteditable","composer","post"]):
            print(f"    {line.strip()[:100]}")
    sys.exit(1)

print(f"\nSUCCESS: Dialog opened! Textbox ref: {txt_ref}")
print("Would type post text here in final script.")
