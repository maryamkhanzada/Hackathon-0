#!/usr/bin/env bash
# Social media education post automation via Playwright MCP
set -e

MCP="http://localhost:8808/mcp"

# ── Init session ──────────────────────────────────────────────────────────────
SESSION=$(curl -si --max-time 30 -X POST "$MCP" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"social-poster","version":"1.0"}}}' 2>&1 \
  | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\r\n')

echo "[OK] Session: ${SESSION:0:8}..."

# Send initialized notification
curl -s --max-time 15 -X POST "$MCP" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' > /dev/null 2>&1

REQ=2

mcp_tool() {
  local tool="$1"
  local args="$2"
  REQ=$((REQ+1))
  local payload="{\"jsonrpc\":\"2.0\",\"id\":$REQ,\"method\":\"tools/call\",\"params\":{\"name\":\"$tool\",\"arguments\":$args}}"
  curl -N -s --max-time 60 -X POST "$MCP" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Mcp-Session-Id: $SESSION" \
    -d "$payload" 2>/dev/null \
    | grep '^data:' | head -1 | sed 's/^data: //'
}

run_js() {
  local code="$1"
  local args="{\"code\":$(echo "$code" | python -c "import sys,json; print(json.dumps(sys.stdin.read()))")}"
  mcp_tool "browser_run_code" "$args"
}

navigate() {
  local url="$1"
  echo "  -> navigate: $url"
  mcp_tool "browser_navigate" "{\"url\":\"$url\"}" | python -c "
import sys,json
try:
  d=json.load(sys.stdin)
  txt=d.get('result',{}).get('content',[{}])[0].get('text','')
  print(txt[:80])
except: pass
" 2>/dev/null || true
}

wait_ms() {
  mcp_tool "browser_wait_for" "{\"time\":$1}" > /dev/null 2>&1 || true
}

# ── Facebook post content ─────────────────────────────────────────────────────
FB_TEXT="The world has never had more access to education — and yet learning has never felt harder to sustain.

Here's why: access to information is no longer the problem. Focus is.

The students and professionals winning in 2026 are those who show up consistently, ask better questions, and turn every experience into a lesson.

Education isn't an event you attend. It's a practice you build.

🎓 What's one thing you've taught yourself outside of school that changed your life?

Tell us in the comments — let's build a resource list together.

#Education #LifelongLearning #FutureOfLearning #GrowthMindset #Learning"

# ── Twitter/X post content ────────────────────────────────────────────────────
X_TEXT="The most underrated investment in 2026:

Spending 15 minutes a day learning something you don't know yet.

No cost. No degree required. Just consistency.

What are you learning this month?

#Education #Learning #GrowthMindset"

# ── LinkedIn post content ─────────────────────────────────────────────────────
LI_TEXT="After years of working with high-performing teams, the single biggest differentiator isn't IQ, background, or credentials.

It's the commitment to keep learning after formal education ends.

1. They treat curiosity as a professional skill.
2. They learn in public — sharing accelerates growth.
3. They apply new knowledge within 48 hours (5x retention).
4. They invest in education even when things are going well.

The return on education compounds — year over year, habit over habit.

What does your learning habit look like this year?

#Education #Leadership #ProfessionalDevelopment #GrowthMindset #LifelongLearning"

# ── IG caption ────────────────────────────────────────────────────────────────
IG_TEXT="Learning doesn't stop when school ends. 📚

The most successful people treat education as a daily habit, not a destination. 15 minutes a day. One new concept. One better question.

Your future self is built by what you learn today.

What are you learning right now? Drop it below 👇

#Education #LearnEveryDay #GrowthMindset #NeverStopLearning #Knowledge #StudyMotivation"

# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== FACEBOOK ==="
navigate "https://www.facebook.com"
wait_ms 3000

FB_RESULT=$(run_js "
async (page) => {
  const text = $(python -c "import json,sys; sys.stdout.write(json.dumps(open('/dev/stdin').read()))" <<< "$FB_TEXT");
  try {
    // Click composer box
    const comp = page.locator('[aria-label=\"What\\'s on your mind?\"]').first();
    if (await comp.isVisible({timeout:4000}).catch(()=>false)) {
      await comp.click();
      await page.waitForTimeout(1500);
    }
    const editor = page.locator('[contenteditable=\"true\"][role=\"textbox\"]').first();
    if (await editor.isVisible({timeout:4000}).catch(()=>false)) {
      await editor.click();
      await editor.fill(text);
      await page.waitForTimeout(1500);
      const postBtn = page.locator('[aria-label=\"Post\"]').last();
      if (await postBtn.isVisible({timeout:3000}).catch(()=>false)) {
        await postBtn.click();
        await page.waitForTimeout(2000);
        return 'Facebook post published!';
      }
      return 'Facebook: text typed, Post button not found';
    }
    return 'Facebook: composer not found';
  } catch(e) { return 'FB error: '+e.message; }
}
")
echo "  Result: $(echo "$FB_RESULT" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('content',[{}])[0].get('text','')[:100])" 2>/dev/null || echo "$FB_RESULT" | head -c 100)"

# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== X (TWITTER) ==="
navigate "https://x.com/home"
wait_ms 3000

X_RESULT=$(run_js "
async (page) => {
  const text = $(python -c "import json,sys; sys.stdout.write(json.dumps(open('/dev/stdin').read()))" <<< "$X_TEXT");
  try {
    const editor = page.locator('[data-testid=\"tweetTextarea_0\"]').first();
    if (await editor.isVisible({timeout:5000}).catch(()=>false)) {
      await editor.click();
      await editor.fill(text);
      await page.waitForTimeout(1500);
      const btn = page.locator('[data-testid=\"tweetButtonInline\"]').first();
      if (await btn.isVisible({timeout:3000}).catch(()=>false)) {
        await btn.click();
        await page.waitForTimeout(2000);
        return 'X: Tweet posted!';
      }
      return 'X: typed, post button not found';
    }
    return 'X: tweet editor not found';
  } catch(e) { return 'X error: '+e.message; }
}
")
echo "  Result: $(echo "$X_RESULT" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('content',[{}])[0].get('text','')[:100])" 2>/dev/null || echo "$X_RESULT" | head -c 100)"

# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== LINKEDIN ==="
navigate "https://www.linkedin.com/feed/"
wait_ms 3000

LI_RESULT=$(run_js "
async (page) => {
  const text = $(python -c "import json,sys; sys.stdout.write(json.dumps(open('/dev/stdin').read()))" <<< "$LI_TEXT");
  try {
    const startBtn = page.getByText('Start a post', {exact:false}).first();
    if (await startBtn.isVisible({timeout:5000}).catch(()=>false)) {
      await startBtn.click();
      await page.waitForTimeout(2000);
    }
    const editor = page.locator('.ql-editor, [role=\"textbox\"][contenteditable=\"true\"]').first();
    if (await editor.isVisible({timeout:5000}).catch(()=>false)) {
      await editor.click();
      await editor.fill(text);
      await page.waitForTimeout(1500);
      const btn = page.getByRole('button', {name:/^Post\$/}).last();
      if (await btn.isVisible({timeout:3000}).catch(()=>false)) {
        await btn.click();
        await page.waitForTimeout(2000);
        return 'LinkedIn: Post published!';
      }
      return 'LinkedIn: filled, Post button not found';
    }
    return 'LinkedIn: editor not found';
  } catch(e) { return 'LinkedIn error: '+e.message; }
}
")
echo "  Result: $(echo "$LI_RESULT" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('content',[{}])[0].get('text','')[:100])" 2>/dev/null || echo "$LI_RESULT" | head -c 100)"

# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== INSTAGRAM ==="
navigate "https://www.instagram.com"
wait_ms 3000

IG_RESULT=$(run_js "
async (page) => {
  try {
    const btn = page.locator('[aria-label=\"New post\"], a[href*=\"create\"]').first();
    if (await btn.isVisible({timeout:5000}).catch(()=>false)) {
      await btn.click();
      return 'Instagram: New post opened - attach image then paste caption: $(echo "${IG_TEXT:0:60}" | tr '\n' ' ')...';
    }
    return 'Instagram: New post button not found - manual post required (image needed)';
  } catch(e) { return 'IG error: '+e.message; }
}
")
echo "  Result: $(echo "$IG_RESULT" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('content',[{}])[0].get('text','')[:120])" 2>/dev/null || echo "$IG_RESULT" | head -c 120)"

echo ""
echo "========================================"
echo "DONE — check the browser for results"
echo "========================================"
