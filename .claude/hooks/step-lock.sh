#!/usr/bin/env bash
# ==============================================================================
# NovOS step-lock — UserPromptSubmit hook.
# Derives the active step from the USER's prompt (the model cannot set it):
# Matched only at the start of the prompt's first line:
#   "/goal STEP 9B", "/boost STEP 8A", "[Please] Run|Execute|Proceed [with] STEP 7C"
#       -> .claude/state/active-step = <ID>  (guard-edit enforces ALLOWED_FILES)
#   "/plan", "/plan-argon", "create a plan"
#       -> active-step = PLAN  (only STEPPED_IMPLEMENTATION_PLAN.md editable)
#   "continue" / "go on" / "proceed" (alone)
#       -> active step unchanged
#   anything else -> cleared (conversation mode; protected files stay locked)
# An un-negated "run the VM test" / "run the build" sets allow_vm_test=1 for
# this prompt only (every prompt rewrites the flag).
# Emits additionalContext naming the active step and its POST_GATE.
# ==============================================================================
set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
    ROOT="$CLAUDE_PROJECT_DIR"
    STATE_DIR="${ROOT}/.claude/state"
else
    ROOT="${NOVOS_GUARD_ROOT:-$(cd "${HOOK_DIR}/../.." && pwd)}"
    STATE_DIR="${NOVOS_GUARD_STATE:-${ROOT}/.claude/state}"
fi
PLAN="${ROOT}/STEPPED_IMPLEMENTATION_PLAN.md"

input="$(cat)"
prompt="$(jq -r '.prompt // empty' <<<"$input")"
mkdir -p "$STATE_DIR"

# Only the first line of the prompt is a command; later lines are context.
first_line="${prompt%%$'\n'*}"

shopt -s nocasematch
mode="clear"
step=""
step_re='^[[:space:]]*(please[[:space:]]+)?(/goal|/boost|run|execute|proceed([[:space:]]+with)?|continue([[:space:]]+with)?)[[:space:]]+step[[:space:]]+([0-9]+[a-z])([^a-z0-9]|$)'
if [[ "$first_line" =~ $step_re ]]; then
    mode="step"
    step="${BASH_REMATCH[5]^^}"
elif [[ "$first_line" =~ ^[[:space:]]*/plan(-argon)?([[:space:]]|$) || "$first_line" =~ ^[[:space:]]*(please[[:space:]]+)?create[[:space:]]+an?[[:space:]]+(stepped[[:space:]]+)?plan ]]; then
    mode="plan"
elif [[ "$prompt" =~ ^[[:space:]]*(continue|go[[:space:]]+on|proceed)[[:space:].!]*$ ]]; then
    mode="keep"
fi

# VM/host-test permission: explicit, un-negated request in this prompt only.
vm=0
vm_re='(^|[.!?;][[:space:]]*|^[[:space:]]*please[[:space:]]+)(run|start|launch)[[:space:]]+(the[[:space:]]+)?((vm|qemu)[[:space:]]+tests?|build)([^a-z]|$)'
neg_re="(don'?t|do[[:space:]]+not|never|no[[:space:]]+need[[:space:]]+to|not[[:space:]]+yet|without)[[:space:]]+(to[[:space:]]+)?(run|start|launch)"
if [[ "$prompt" =~ $vm_re && ! "$prompt" =~ $neg_re ]]; then
    vm=1
fi
shopt -u nocasematch

post_gate_for_step() {
    awk -v id="$1" '
        $0 ~ ("^### STEP " id ":") { inb = 1; next }
        inb && /^### STEP / { exit }
        inb && /^\[POST_GATE\]/ { pg = 1; next }
        inb && pg && /^\[HALT_INVARIANT\]/ { exit }
        inb && pg && NF { print }
    ' "$PLAN"
}

context=""
case "$mode" in
    plan)
        printf 'PLAN\n' > "${STATE_DIR}/active-step"
        printf '%s\n' "$vm" > "${STATE_DIR}/allow_vm_test"
        context="NovOS step-lock: PLAN mode. Only STEPPED_IMPLEMENTATION_PLAN.md may be edited; write full Argon step blocks, then halt for approval."
        ;;
    step)
        printf '%s\n' "$step" > "${STATE_DIR}/active-step"
        printf '%s\n' "$vm" > "${STATE_DIR}/allow_vm_test"
        if [[ -f "$PLAN" ]] && grep -q "^### STEP ${step}:" "$PLAN"; then
            gate="$(post_gate_for_step "$step")"
            context="NovOS step-lock: active STEP ${step}. Edits are limited to its ALLOWED_FILES (plus the plan's roadmap status). Run PRE_GATE first; halt after this POST_GATE passes:
${gate}"
        else
            context="NovOS step-lock: STEP ${step} is not defined in STEPPED_IMPLEMENTATION_PLAN.md; all repository edits are blocked. Report this and halt."
        fi
        ;;
    keep)
        printf '%s\n' "$vm" > "${STATE_DIR}/allow_vm_test"
        if [[ -f "${STATE_DIR}/active-step" ]]; then
            context="NovOS step-lock: continuing STEP $(tr -d '[:space:]' < "${STATE_DIR}/active-step")."
        fi
        ;;
    clear)
        rm -f "${STATE_DIR}/active-step"
        printf '%s\n' "$vm" > "${STATE_DIR}/allow_vm_test"
        ;;
esac

if [[ -n "$context" ]]; then
    jq -n --arg ctx "$context" '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'
fi
exit 0
