#!/usr/bin/env bash
# ============================================================================
# ralph_loop.sh — Ralph Wiggum persistence loop for Personal AI Employee
#
# The "Ralph Wiggum" pattern: keep re-invoking the AI agent until the job is
# actually done.  The agent signals completion by outputting a promise tag:
#
#     <promise>TASK_COMPLETE</promise>
#
# The loop independently verifies by checking that Needs_Action/ is empty
# (all items moved to Done/).
#
# Usage:
#     ./ralph_loop.sh                    # process all Needs_Action items
#     ./ralph_loop.sh --max-loops 5      # cap at 5 iterations
#     ./ralph_loop.sh --dry-run          # print what would happen, don't call Claude
#     ./ralph_loop.sh --prompt "custom"  # override the default prompt
#     ./ralph_loop.sh --task-id abc123   # track a specific task file
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VAULT_ROOT="$(cd "$(dirname "$0")" && pwd)"
NEEDS_ACTION="$VAULT_ROOT/Needs_Action"
DONE_DIR="$VAULT_ROOT/Done"
LOGS_DIR="$VAULT_ROOT/Logs"
PROMPT_TEMPLATE="$VAULT_ROOT/Templates/ralph_prompt.md"

MAX_LOOPS=10              # safety cap — never loop more than this
COOLDOWN=5                # seconds between iterations
PROMISE_TAG="TASK_COMPLETE"
DRY_RUN=false
CUSTOM_PROMPT=""
TASK_ID=""                # optional: wait for a specific file ID in Done/

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-loops)  MAX_LOOPS="$2";    shift 2 ;;
        --cooldown)   COOLDOWN="$2";     shift 2 ;;
        --dry-run)    DRY_RUN=true;      shift   ;;
        --prompt)     CUSTOM_PROMPT="$2"; shift 2 ;;
        --task-id)    TASK_ID="$2";      shift 2 ;;
        --help|-h)
            echo "Usage: ralph_loop.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --max-loops N   Maximum loop iterations (default: 10)"
            echo "  --cooldown N    Seconds between loops (default: 5)"
            echo "  --dry-run       Print actions without calling Claude"
            echo "  --prompt TEXT   Override the processing prompt"
            echo "  --task-id ID    Wait for specific task ID to appear in Done/"
            echo "  -h, --help      Show this help"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

mkdir -p "$LOGS_DIR"
LOOP_LOG="$LOGS_DIR/ralph_loop_$(date +%Y%m%d_%H%M%S).log"

log() {
    local msg="[$(date '+%H:%M:%S')] $1"
    echo "$msg"
    echo "$msg" >> "$LOOP_LOG"
}

log_activity() {
    local today
    today=$(date +%Y-%m-%d)
    local ts
    ts=$(date +%H:%M:%S)
    echo "$ts | ralph_loop | $1 | $2" >> "$LOGS_DIR/activity_${today}.log"
}

# ---------------------------------------------------------------------------
# Vault state helpers
# ---------------------------------------------------------------------------

count_needs_action() {
    # Count .md files in Needs_Action/ (excluding .gitkeep)
    find "$NEEDS_ACTION" -maxdepth 1 -name "*.md" 2>/dev/null | wc -l
}

count_done_today() {
    local today
    today=$(date +%Y%m%d)
    find "$DONE_DIR" -maxdepth 1 -name "${today}*.md" 2>/dev/null | wc -l
}

task_in_done() {
    # Check if a specific task ID (hash) appears in any Done/ file's frontmatter
    if [[ -z "$TASK_ID" ]]; then
        return 1  # no specific task — can't confirm
    fi
    grep -rl "id: $TASK_ID" "$DONE_DIR"/*.md 2>/dev/null && return 0
    return 1
}

is_work_remaining() {
    local count
    count=$(count_needs_action)
    if [[ "$count" -gt 0 ]]; then
        return 0  # yes, work remaining
    fi
    return 1  # no work
}

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

build_prompt() {
    local iteration="$1"
    local remaining
    remaining=$(count_needs_action)

    # Use custom prompt if provided.
    if [[ -n "$CUSTOM_PROMPT" ]]; then
        echo "$CUSTOM_PROMPT"
        return
    fi

    # Use template if it exists.
    if [[ -f "$PROMPT_TEMPLATE" ]]; then
        # Substitute variables in the template.
        sed \
            -e "s|{{VAULT_ROOT}}|$VAULT_ROOT|g" \
            -e "s|{{ITERATION}}|$iteration|g" \
            -e "s|{{MAX_LOOPS}}|$MAX_LOOPS|g" \
            -e "s|{{REMAINING}}|$remaining|g" \
            -e "s|{{TIMESTAMP}}|$(date '+%Y-%m-%d %H:%M')|g" \
            "$PROMPT_TEMPLATE"
        return
    fi

    # Fallback: inline prompt.
    cat <<PROMPT
You are the Personal AI Employee processing the vault at $VAULT_ROOT.
This is loop iteration $iteration of $MAX_LOOPS.

There are $remaining item(s) remaining in Needs_Action/.

Your task:
1. Read every .md file in $VAULT_ROOT/Needs_Action/
2. For each item:
   a. Parse the YAML frontmatter (id, priority, source, status)
   b. Summarize it in one line
   c. If you can handle it autonomously (per Company_Handbook.md), update
      its frontmatter to status: done
   d. If it needs human approval, leave it as status: open
3. Run: python $VAULT_ROOT/watchers/vault_processor.py
   This updates Dashboard.md and moves status:done items to Done/
4. Verify: check that items you marked done have moved to Done/

IMPORTANT — When all items are processed (Needs_Action/ is empty OR only
items requiring human approval remain), output exactly:

    <promise>TASK_COMPLETE</promise>

If items still need processing that you CAN handle, do NOT output the promise.
The loop will re-invoke you.
PROMPT
}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

log "=========================================="
log "Ralph Wiggum Loop — Starting"
log "Vault:      $VAULT_ROOT"
log "Max loops:  $MAX_LOOPS"
log "Cooldown:   ${COOLDOWN}s"
log "Dry run:    $DRY_RUN"
log "Task ID:    ${TASK_ID:-<none>}"
log "=========================================="
log_activity "LOOP_START" "max=$MAX_LOOPS cooldown=$COOLDOWN"

# Pre-flight check.
initial_count=$(count_needs_action)
log "Items in Needs_Action: $initial_count"

if [[ "$initial_count" -eq 0 ]]; then
    log "Nothing to process. Exiting."
    log_activity "LOOP_SKIP" "needs_action=0"
    echo "<promise>$PROMISE_TAG</promise>"
    exit 0
fi

iteration=0
promise_fulfilled=false

while [[ $iteration -lt $MAX_LOOPS ]]; do
    iteration=$((iteration + 1))
    remaining=$(count_needs_action)

    log "------------------------------------------"
    log "Iteration $iteration/$MAX_LOOPS  |  Remaining: $remaining"
    log "------------------------------------------"

    # --- Check if already done ---
    if [[ "$remaining" -eq 0 ]]; then
        log "Needs_Action is empty. Work complete."
        promise_fulfilled=true
        break
    fi

    # If tracking a specific task and it's in Done, we're done.
    if [[ -n "$TASK_ID" ]] && task_in_done; then
        log "Task $TASK_ID found in Done/. Work complete."
        promise_fulfilled=true
        break
    fi

    # --- Build the prompt ---
    prompt=$(build_prompt "$iteration")

    if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY RUN] Would invoke Claude Code with prompt:"
        log "--- PROMPT START ---"
        echo "$prompt" | tee -a "$LOOP_LOG"
        log "--- PROMPT END ---"
        log "[DRY RUN] Simulating vault_processor.py instead..."
        python "$VAULT_ROOT/watchers/vault_processor.py" --scan-only 2>&1 | tee -a "$LOOP_LOG"
        log "[DRY RUN] Simulating promise output."
        promise_fulfilled=true
        break
    fi

    # --- Invoke Claude Code ---
    log "Invoking Claude Code..."
    output_file=$(mktemp)

    # Run Claude Code with the prompt, piped in via --print flag
    # Use --print for non-interactive mode (output only, no TUI)
    if claude --print --dangerously-skip-permissions \
       "$prompt" \
       > "$output_file" 2>&1; then
        log "Claude Code exited successfully."
    else
        log "Claude Code exited with error (code $?)."
    fi

    # --- Check output for promise tag ---
    if grep -q "<promise>$PROMISE_TAG</promise>" "$output_file"; then
        log "Promise tag found in output."
        promise_fulfilled=true
        cat "$output_file" >> "$LOOP_LOG"
        rm -f "$output_file"
        break
    fi

    # Log the output
    log "No promise tag found. Appending output to log."
    cat "$output_file" >> "$LOOP_LOG"
    rm -f "$output_file"

    # --- Post-iteration vault check ---
    post_remaining=$(count_needs_action)
    done_count=$(count_done_today)
    log "Post-iteration: Needs_Action=$post_remaining  Done_today=$done_count"
    log_activity "LOOP_ITER" "iteration=$iteration remaining=$post_remaining done=$done_count"

    # If nothing moved between iterations, avoid an infinite loop.
    if [[ "$post_remaining" -eq "$remaining" ]]; then
        log "WARNING: No progress made this iteration."
        # Still continue — Claude might need a second pass to verify.
    fi

    # --- Cooldown ---
    if [[ $iteration -lt $MAX_LOOPS ]]; then
        log "Cooling down for ${COOLDOWN}s..."
        sleep "$COOLDOWN"
    fi
done

# ---------------------------------------------------------------------------
# Final status
# ---------------------------------------------------------------------------

final_remaining=$(count_needs_action)
final_done=$(count_done_today)

log "=========================================="
if [[ "$promise_fulfilled" == "true" ]]; then
    log "RESULT: Task complete."
    log "<promise>$PROMISE_TAG</promise>"
    echo "<promise>$PROMISE_TAG</promise>"
    log_activity "LOOP_DONE" "iterations=$iteration remaining=$final_remaining done=$final_done"
else
    log "RESULT: Max iterations ($MAX_LOOPS) reached."
    log "Remaining in Needs_Action: $final_remaining"
    log "This may require human intervention."
    log_activity "LOOP_MAX" "iterations=$iteration remaining=$final_remaining done=$final_done"
    exit 1
fi
log "=========================================="
