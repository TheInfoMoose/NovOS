#!/usr/bin/env bash
# ==============================================================================
# NovOS step-lock — UserPromptSubmit hook.
# Derives the active step from the USER's prompt (the model cannot set it).
# State: .claude/state/active-step (one line: <ID> or PLAN; guard-edit enforces
# ALLOWED_FILES), active-step.owner (session that set it), active-step.delegates
# (proxy sessions, one per line). A session is the owner when there is no owner
# file, no session_id, or the owner file equals its session_id (STEP 16A/16E).
#
# Every prompt first: if a roadmap table row naming `**STEP <ID>**` has a cell
# starting with COMPLETED (or **COMPLETED**), the lock is released (all three files).
# Commands, matched at the start of the first line (case-insensitive, optional
# "please"):
#   step:    /goal|/boost|run|execute|start|begin|do|proceed [with]|continue [with]
#            [on] STEP <ID>                    -> active-step = <ID>, owner = this session
#   next:    same verbs [on|with] [the] next step
#            -> the first Up Next row that is not complete, set as "step"
#   release: abort|release|cancel|stop [the] step [<ID>]  -> lock released (owner only)
#   plan:    "/plan", "/plan-argon", "create a plan"       -> active-step = PLAN
#   keep:    "continue" / "go on" / "proceed" (alone)      -> unchanged
#   event:   "<side-sessions-event", "<task-notification", "<cross-session-message",
#            "<ci-monitor-event"                -> unchanged; never sets a step
#   delegation: first line is the app header "Background (from the session that
#            started this one):" and a later line starts with /goal|/boost STEP <ID>
#            -> if <ID> is the active step, this session joins active-step.delegates
#               (a proxy under the owner's lock); otherwise nothing changes.
# While another session owns the lock, step / next / plan / release commands
# change nothing; a non-owner /goal|/boost naming the active step joins as a
# delegate. Delegates never set, switch, release or clear the lock.
# Any other prompt keeps a step lock (replies no longer clear it); the owner's
# non-command prompt still clears a PLAN lock.
# An un-negated "run the VM test" / "run the build" sets allow_vm_test=1 for
# this prompt only (every prompt rewrites the flag; event prompts write 0).
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
DELEGATION_HEADER="Background (from the session that started this one):"

input="$(cat)"
prompt="$(jq -r '.prompt // empty' <<<"$input")"
session_id="$(jq -r '.session_id // empty' <<<"$input")"
mkdir -p "$STATE_DIR"
STEP_FILE="${STATE_DIR}/active-step"
OWNER_FILE="${STATE_DIR}/active-step.owner"
DELEGATES_FILE="${STATE_DIR}/active-step.delegates"

active=""
owner=""
load_state() {
    active=""
    owner=""
    if [[ -f "$STEP_FILE" ]]; then
        active="$(tr -d '[:space:]' < "$STEP_FILE")"
    fi
    if [[ -f "$OWNER_FILE" ]]; then
        owner="$(tr -d '[:space:]' < "$OWNER_FILE")"
    fi
}

# Releases and owner switches remove all three state files together.
release_all() {
    rm -f "$STEP_FILE" "$OWNER_FILE" "$DELEGATES_FILE"
    active=""
    owner=""
}

is_owner() {
    [[ -z "$owner" || -z "$session_id" || "$owner" == "$session_id" ]]
}

# plan/step record the setting session; without a session_id there is no owner.
set_lock() {
    printf '%s\n' "$1" > "$STEP_FILE"
    if [[ -n "$session_id" ]]; then
        printf '%s\n' "$session_id" > "$OWNER_FILE"
    else
        rm -f "$OWNER_FILE"
    fi
    active="$1"
}

add_delegate() {
    [[ -n "$session_id" ]] || return 0
    if [[ -f "$DELEGATES_FILE" ]] && grep -Fxq -- "$session_id" "$DELEGATES_FILE"; then
        return 0
    fi
    printf '%s\n' "$session_id" >> "$DELEGATES_FILE"
}

# A roadmap table row is complete when one of its cells starts with COMPLETED
# (or **COMPLETED**); Scope text that merely mentions the word does not count.
ROW_DONE_AWK='
    function row_done(   i, c) {
        for (i = 2; i < NF; i++) {
            c = $i
            gsub(/^[ \t]+|[ \t]+$/, "", c)
            if (c ~ /^(\*\*)?COMPLETED/) return 1
        }
        return 0
    }'

step_completed() {
    [[ -f "$PLAN" ]] || return 1
    awk -F'|' -v pat="**STEP $1**" "${ROW_DONE_AWK}"'
        /^\|/ && index($0, pat) && row_done() { found = 1; exit }
        END { exit !found }
    ' "$PLAN"
}

step_defined() {
    [[ -f "$PLAN" ]] && grep -q "^### STEP $1:" "$PLAN"
}

post_gate_for_step() {
    awk -v id="$1" '
        $0 ~ ("^### STEP " id ":") { inb = 1; next }
        inb && /^### STEP / { exit }
        inb && /^\[POST_GATE\]/ { pg = 1; next }
        inb && pg && /^\[HALT_INVARIANT\]/ { exit }
        inb && pg && NF { print }
    ' "$PLAN"
}

# First Up Next row that is not complete: "<ID><TAB><Waits on>".
next_step_row() {
    [[ -f "$PLAN" ]] || return 0
    awk -F'|' "${ROW_DONE_AWK}"'
        /^### Up Next/ { inb = 1; next }
        inb && /^### / { exit }
        inb && /^\|/ && /\*\*STEP [0-9]+[A-Za-z]\*\*/ && !row_done() {
            match($0, /\*\*STEP [0-9]+[A-Za-z]\*\*/)
            id = substr($0, RSTART + 7, RLENGTH - 9)
            w = $(NF - 1)
            gsub(/^[ \t]+|[ \t]+$/, "", w)
            print toupper(id) "\t" w
            exit
        }
    ' "$PLAN"
}

step_context() {  # step_context <ID> <prefix>
    local gate
    if step_defined "$1"; then
        gate="$(post_gate_for_step "$1")"
        printf '%s' "$2 Edits are limited to its ALLOWED_FILES (plus the plan's roadmap status). Run PRE_GATE first; halt after this POST_GATE passes:
${gate}"
    else
        printf '%s' "NovOS step-lock: STEP $1 is not defined in STEPPED_IMPLEMENTATION_PLAN.md; all repository edits are blocked. Report this and halt."
    fi
}

load_state

# Completion release runs before anything else, for every prompt.
context=""
if [[ -n "$active" && "$active" != "PLAN" ]] && step_completed "$active"; then
    context="NovOS step-lock: STEP ${active} is COMPLETED; lock released."
    release_all
fi

# Only the first line of the prompt is a command; later lines are context
# (except the step line of a delegated brief).
first_line="${prompt%%$'\n'*}"

# The delegation header must be the exact first line (case-sensitive, nothing trimmed).
is_header=0
if [[ "$first_line" == "$DELEGATION_HEADER" ]]; then
    is_header=1
fi

shopt -s nocasematch
mode="other"
step=""
verbs='(/goal|/boost|run|execute|start|begin|do|proceed([[:space:]]+with)?|continue([[:space:]]+with)?)'
step_re="^[[:space:]]*(please[[:space:]]+)?${verbs}([[:space:]]+on)?[[:space:]]+step[[:space:]]+([0-9]+[a-z])([^a-z0-9]|\$)"
next_re='^[[:space:]]*(please[[:space:]]+)?(/goal|/boost|run|execute|start|begin|do|proceed|continue)([[:space:]]+(on|with))?([[:space:]]+the)?[[:space:]]+next[[:space:]]+step([^a-z0-9]|$)'
# Any word after "step" is the ID to release; it must equal the active step.
release_re='^[[:space:]]*(please[[:space:]]+)?(abort|release|cancel|stop)([[:space:]]+the)?[[:space:]]+step([[:space:]]+([0-9a-z]+))?([^a-z0-9]|$)'
event_re='^[[:space:]]*<(side-sessions-event|task-notification|cross-session-message|ci-monitor-event)([^a-z0-9-]|$)'
brief_step_re='^[[:space:]]*(/goal|/boost)[[:space:]]+step[[:space:]]+([0-9]+[a-z])([^a-z0-9]|$)'
if [[ "$first_line" =~ $event_re ]]; then
    mode="event"
elif [[ "$is_header" -eq 1 ]]; then
    # A delegated brief: the first /goal|/boost STEP line counts; none -> other.
    while IFS= read -r line; do
        if [[ "$line" =~ $brief_step_re ]]; then
            mode="delegate"
            step="${BASH_REMATCH[2]^^}"
            break
        fi
    done <<<"${prompt#*$'\n'}"
elif [[ "$first_line" =~ $release_re ]]; then
    mode="release"
    step="${BASH_REMATCH[5]^^}"
elif [[ "$first_line" =~ $next_re ]]; then
    mode="next"
elif [[ "$first_line" =~ $step_re ]]; then
    mode="step"
    step="${BASH_REMATCH[6]^^}"
    # A non-owner /goal|/boost naming the active step joins as a delegate.
    if [[ "${BASH_REMATCH[2]}" == /goal || "${BASH_REMATCH[2]}" == /boost ]]; then
        explicit_slash=1
    fi
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

add_context() {
    if [[ -n "$context" ]]; then
        context="${context}
$1"
    else
        context="$1"
    fi
}

held_by_other=0
if [[ -n "$active" ]] && ! is_owner; then
    held_by_other=1
fi
owned_msg="NovOS step-lock: STEP ${active} stays active (owned by another session)"

# Owner (or free lock): set <ID>. Re-setting the active ID keeps owner and
# delegates; a switch to another ID releases the old lock first.
take_step() {  # take_step <ID> <prefix>
    if [[ "$active" != "$1" ]]; then
        release_all
        set_lock "$1"
    fi
    add_context "$(step_context "$1" "$2")"
}

join_as_delegate() {
    add_delegate
    add_context "$(step_context "$active" "NovOS step-lock: proxy for STEP ${active} (owned by another session).")"
}

case "$mode" in
    event)
        # Automated event prompts never set, clear or re-own the step.
        vm=0
        if [[ -n "$active" ]]; then
            add_context "NovOS step-lock: continuing STEP ${active}."
        fi
        ;;
    delegate)
        if [[ -n "$active" && "$active" != "PLAN" && "$active" == "$step" ]]; then
            if is_owner && [[ -n "$owner" ]]; then
                add_context "$(step_context "$active" "NovOS step-lock: active STEP ${active}.")"
            else
                join_as_delegate
            fi
        else
            add_context "NovOS step-lock: delegated STEP ${step} has no matching lock from the initiating session; make no edits, report and halt."
        fi
        ;;
    release)
        if [[ -z "$active" ]]; then
            add_context "NovOS step-lock: no active step to release."
        elif [[ "$held_by_other" -eq 1 ]]; then
            add_context "$owned_msg"
        elif [[ -n "$step" && "$step" != "$active" ]]; then
            add_context "NovOS step-lock: STEP ${step} is not the active step (STEP ${active}); nothing released."
        else
            add_context "NovOS step-lock: STEP ${active} released."
            release_all
        fi
        ;;
    next)
        if [[ "$held_by_other" -eq 1 ]]; then
            add_context "$owned_msg"
        else
            row="$(next_step_row)"
            if [[ -z "$row" ]]; then
                add_context "NovOS step-lock: no open row in Up Next; nothing set."
            else
                next_id="${row%%$'\t'*}"
                waits="${row#*$'\t'}"
                take_step "$next_id" "NovOS step-lock: active STEP ${next_id} (next step in Up Next; Waits on: ${waits})."
            fi
        fi
        ;;
    step)
        if [[ "$held_by_other" -eq 1 ]]; then
            if [[ "${explicit_slash:-0}" -eq 1 && "$step" == "$active" && "$active" != "PLAN" ]]; then
                join_as_delegate
            else
                add_context "$owned_msg"
            fi
        else
            take_step "$step" "NovOS step-lock: active STEP ${step}."
        fi
        ;;
    plan)
        if [[ "$held_by_other" -eq 1 ]]; then
            add_context "$owned_msg"
        else
            if [[ "$active" != "PLAN" ]]; then
                release_all
                set_lock "PLAN"
            fi
            add_context "NovOS step-lock: PLAN mode. Only STEPPED_IMPLEMENTATION_PLAN.md may be edited; write full Argon step blocks, then halt for approval."
        fi
        ;;
    keep)
        if [[ -n "$active" ]]; then
            add_context "NovOS step-lock: continuing STEP ${active}."
        fi
        ;;
    other)
        if [[ -z "$active" ]]; then
            :
        elif [[ "$held_by_other" -eq 1 ]]; then
            add_context "$owned_msg"
        elif [[ "$active" == "PLAN" ]]; then
            # The owner's non-command prompt still ends PLAN mode (16A behaviour).
            release_all
        else
            add_context "NovOS step-lock: continuing STEP ${active}."
        fi
        ;;
esac

printf '%s\n' "$vm" > "${STATE_DIR}/allow_vm_test"

if [[ -n "$context" ]]; then
    jq -n --arg ctx "$context" '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'
fi
exit 0
