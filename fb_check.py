#!/usr/bin/env python3
import json, subprocess, sys, time

C = "D:/Hackathon-0/.claude/Skills/browsing-with-playwright/scripts/mcp-client.py"
U = "http://localhost:8808"


def ev(js, t=20):
    r = subprocess.run([sys.executable, C, "call", "-u", U, "-t", "browser_evaluate",
                        "-p", json.dumps({"function": js})],
                       capture_output=True, timeout=t)
    out = r.stdout.decode("utf-8", "replace").strip()
    if not out:
        return "ERR:" + r.stderr.decode("utf-8", "replace")[:60]
    try:
        d = json.loads(out)
        full = "\n".join(c["text"] for c in d.get("content", []) if c.get("type") == "text")
        for line in full.split("\n"):
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("-") and not s.startswith("```"):
                return s.strip('"')
        return full[:60]
    except Exception as e:
        return f"ERR:{e}:{out[:60]}"


# Navigate to Facebook
print("Navigating to Facebook...")
r = ev('() => { window.location.replace("https://www.facebook.com"); return "ok"; }', t=10)
print("Nav:", r)
time.sleep(25)

print("Title:", ev("() => document.title"))
print("URL:", ev("() => location.href"))

# List buttons
print("\nAll buttons on page:")
r2 = ev('() => Array.from(document.querySelectorAll("[role=button]")).map(b => b.innerText.substring(0, 30)).filter(t => t.trim()).slice(0, 20).join(" || ")')
print(" ", r2[:500])
