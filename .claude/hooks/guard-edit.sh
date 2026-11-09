#!/usr/bin/env bash
# ==============================================================================
# NovOS guard-edit — PreToolUse hook (Edit|Write|NotebookEdit) and path checker.
# Replicates Locks §5 (step scope lock / ALLOWED_FILES) and the protected-file
# rules (golden snapshot, approvals, manifest, release records, hook state).
#
# Modes:
#   hook:  JSON on stdin (Claude Code PreToolUse payload)
#   cli:   guard-edit.sh --check-path <path> [--cwd <dir>]   (used by guard-bash.sh)
# Exit 0 = allow, exit 2 = block (stderr is fed back to the model).
# ==============================================================================
set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
    # Live hook: never honour test overrides (they could redirect the guard).
    ROOT="$CLAUDE_PROJECT_DIR"
    STATE_DIR="${ROOT}/.claude/state"
else
    ROOT="${NOVOS_GUARD_ROOT:-$(cd "${HOOK_DIR}/../.." && pwd)}"
    STATE_DIR="${NOVOS_GUARD_STATE:-${ROOT}/.claude/state}"
fi
PLAN="${ROOT}/STEPPED_IMPLEMENTATION_PLAN.md"
VIA_BASH=0

block() {
    echo "BLOCKED by guard-edit ($1): $2" >&2
    echo "Report the defect (file:line) to the user and halt; do not work around this guard." >&2
    exit 2
}

# Always protected: no agent may mutate these, whatever the active step says.
ALWAYS_PROTECTED=(
    ".nebulous/golden-snapshot.toml"
    ".nebulous/approvals.toml"
    ".nebulous/manifest.toml"
    "config/novos-allow-approved.toml"
    "config/releases"
    ".tracker/release"
    ".claude/state"
    ".claude/settings.local.json"
)

# Conditionally protected: editable only while the active step lists them in ALLOWED_FILES.
is_conditional() {
    case "$1" in
        config/suppression-baseline.toml|config/app-health.toml) return 0 ;;
        .claude/settings.json|.claude/hooks|.claude/hooks/*) return 0 ;;
    esac
    return 1
}

# STEP 16B: config/suppression-baseline.toml may be lowered outside a step that
# lists it. Python builds the proposed file from the Edit/Write payload (stdin)
# and compares it with the current file (argv[1]): same tables and keys, every
# value an int >= 0 and <= its current value. Prints the first offence, exit 1.
BASELINE_REL="config/suppression-baseline.toml"
HOOK_INPUT=""
BASELINE_PY='
import json, sys, tomllib

def fail(msg):
    print(msg)
    sys.exit(1)

path = sys.argv[1]
payload = json.load(sys.stdin)
tool = payload.get("tool_name") or ""
ti = payload.get("tool_input") or {}
if tool not in ("", "Edit", "Write"):
    fail(f"{tool} is not an Edit or Write")
try:
    with open(path, encoding="utf-8") as fh:
        current_text = fh.read()
except OSError as exc:
    fail(f"cannot read the current file: {exc}")

if tool != "Edit" and isinstance(ti.get("content"), str):
    new_text = ti["content"]
elif tool != "Write" and isinstance(ti.get("old_string"), str):
    old = ti["old_string"]
    new = ti.get("new_string")
    if not isinstance(new, str):
        fail("Edit has no new_string")
    if not old or old not in current_text:
        fail("old_string not found in the current file")
    count = -1 if ti.get("replace_all") else 1
    new_text = current_text.replace(old, new, count)
else:
    fail("payload is neither a Write (content) nor an Edit (old_string)")

try:
    current = tomllib.loads(current_text)
except tomllib.TOMLDecodeError as exc:
    fail(f"current file does not parse: {exc}")
try:
    proposed = tomllib.loads(new_text)
except tomllib.TOMLDecodeError as exc:
    fail(f"proposed content does not parse: {exc}")

for table in current:
    if table not in proposed:
        fail(f"table [{table}] removed")
for table in proposed:
    if table not in current:
        fail(f"table [{table}] added")
for table, cur_keys in current.items():
    new_keys = proposed[table]
    if not isinstance(cur_keys, dict) or not isinstance(new_keys, dict):
        fail(f"[{table}] is not a table")
    for key in cur_keys:
        if key not in new_keys:
            fail(f"[{table}] key \"{key}\" removed")
    for key in new_keys:
        if key not in cur_keys:
            fail(f"[{table}] key \"{key}\" added")
    for key, cur in cur_keys.items():
        val = new_keys[key]
        if isinstance(val, bool) or not isinstance(val, int):
            fail(f"[{table}] \"{key}\" = {val!r} is not an integer")
        if val < 0:
            fail(f"[{table}] \"{key}\" = {val} is negative")
        if isinstance(cur, bool) or not isinstance(cur, int) or val > cur:
            fail(f"[{table}] \"{key}\" = {val} is higher than the current {cur!r}")
'

# Returns 1 when the decrease-only path does not apply (other file, shell write,
# no Edit/Write payload); blocks when it applies and the check fails; 0 = allowed.
baseline_decrease_only() {
    local rel="$1" abs="$2" out rc=0
    [[ "$rel" == "$BASELINE_REL" && "$VIA_BASH" -eq 0 && -n "$HOOK_INPUT" ]] || return 1
    out="$(python3 -I -c "$BASELINE_PY" "$abs" <<<"$HOOK_INPUT" 2>&1)" || rc=$?
    [[ "$rc" -eq 0 ]] && return 0
    block "suppression ratchet" "'$rel' outside a step that lists it may only lower counts (same tables and keys, integers >= 0): ${out}"
}

# True when rel is a protected path, lies inside one, or is an ancestor directory of one.
is_always_protected() {
    local rel="$1" p
    [[ "$rel" == "." ]] && return 0
    for p in "${ALWAYS_PROTECTED[@]}"; do
        if [[ "$p" == "config/releases" ]]; then
            # Release records are protected; the README describing them is not.
            [[ "$rel" == "config/releases/README.md" ]] && continue
            [[ "$rel" == config/releases/* || "$rel" == "config/releases" || "$p" == "$rel"/* ]] && return 0
            continue
        fi
        [[ "$rel" == "$p" || "$rel" == "$p"/* || "$p" == "$rel"/* ]] && return 0
    done
    return 1
}

active_step() {
    if [[ -f "${STATE_DIR}/active-step" ]]; then
        tr -d '[:space:]' < "${STATE_DIR}/active-step"
    fi
}

# Print the ALLOWED_FILES entries of a step from the plan, annotations stripped.
allowed_files_for_step() {
    local id="$1"
    [[ -f "$PLAN" ]] || return 0
    awk -v id="$id" '
        $0 ~ ("^### STEP " id ":") { inb = 1; next }
        inb && /^### STEP / { exit }
        inb && /ALLOWED_FILES:/ { af = 1; next }
        inb && af && (/READ_ONLY_REFS:/ || /FORBIDDEN_SCOPE:/ || /^\[/) { exit }
        inb && af && /^[[:space:]]*- / {
            sub(/^[[:space:]]*- /, "")
            sub(/[[:space:]]+\(.*$/, "")
            sub(/[[:space:]]+$/, "")
            if (length($0) > 0) print
        }
    ' "$PLAN"
}

matches_entry() {
    local rel="$1" e="$2"
    if [[ "$e" == */ ]]; then
        [[ "$rel" == "$e"* || "${rel}/" == "$e" ]] && return 0
    elif [[ "$e" == *'*'* ]]; then
        # shellcheck disable=SC2053  # intentional glob match
        [[ "$rel" == $e ]] && return 0
    else
        [[ "$rel" == "$e" ]] && return 0
    fi
    return 1
}

step_allows() {
    local rel="$1" step="$2" e found=1
    while IFS= read -r e; do
        found=0
        matches_entry "$rel" "$e" && return 0
    done < <(allowed_files_for_step "$step")
    return $((found == 0 ? 1 : 2))
}

check_path() {
    local p="$1" base="${2:-$PWD}" step abs rootabs rel rc
    step="$(active_step)"
    p="${p/#\~/$HOME}"

    if [[ "$p" == *'$'* ]]; then
        if [[ -n "$step" ]]; then
            block "Locks §5" "cannot resolve variable path '$p' during STEP ${step}; use a literal path"
        fi
        if [[ "$p" =~ golden-snapshot|approvals\.toml|manifest\.toml|novos-allow-approved\.toml|config/releases|\.tracker/release|\.claude/state ]]; then
            block "protected file" "variable path '$p' may target a protected file"
        fi
        return 0
    fi

    [[ "$p" != /* ]] && p="${base}/${p}"
    abs="$(realpath -m -- "$p")"
    rootabs="$(realpath -m -- "$ROOT")"

    # Host-critical paths outside the repo (Locks §2) and settings that could disable these hooks.
    case "$abs" in
        /etc/fstab|/etc/default/grub|/boot/*|/sys/firmware/efi/*)
            block "Locks §2" "'$abs' is a host boot/mount configuration; output the change for the user instead" ;;
        /dev/sd*|/dev/nvme*|/dev/vd*|/dev/hd*|/dev/xvd*|/dev/mmcblk*|/dev/loop*|/dev/dm-*|/dev/md*|/dev/mapper/*|/dev/sr*|/dev/disk/*)
            block "Locks §2" "'$abs' is a block device" ;;
        "${HOME}/.claude/settings.json"|"${HOME}/.claude/settings.local.json"|"${HOME}/.claude.json"|/etc/claude-code/*)
            block "protected file" "'$abs' is Claude Code configuration; ask the user to change it" ;;
    esac

    case "$abs" in
        "$rootabs") rel="." ;;
        "$rootabs"/*) rel="${abs#"$rootabs"/}" ;;
        *) return 0 ;;  # outside the repository (scratchpad, memory, /tmp)
    esac

    if [[ "$VIA_BASH" -eq 1 && "$rel" == "STEPPED_IMPLEMENTATION_PLAN.md" ]]; then
        block "approval gate" "edit STEPPED_IMPLEMENTATION_PLAN.md with the Edit/Write tools (their content is checked for approval markers), not via shell"
    fi

    if is_always_protected "$rel"; then
        block "protected file" "'$rel' is user-only (golden snapshot / approvals / manifest / novos-allow approval registry / release records / hook state)"
    fi

    if is_conditional "$rel"; then
        if [[ -z "$step" || "$step" == "PLAN" ]]; then
            baseline_decrease_only "$rel" "$abs" && return 0
            block "protected file" "'$rel' may only change inside a step that lists it in ALLOWED_FILES"
        fi
        rc=0
        step_allows "$rel" "$step" || rc=$?
        [[ "$rc" -eq 0 ]] && return 0
        baseline_decrease_only "$rel" "$abs" && return 0
        block "Locks §5" "'$rel' is not in STEP ${step} ALLOWED_FILES"
    fi

    [[ -z "$step" ]] && return 0  # conversation mode: only protected files are locked

    if [[ "$step" == "PLAN" ]]; then
        [[ "$rel" == "STEPPED_IMPLEMENTATION_PLAN.md" ]] && return 0
        block "plan mode" "only STEPPED_IMPLEMENTATION_PLAN.md may change while planning; '$rel' is code/config"
    fi

    # The plan file itself stays editable so the roadmap status can be updated
    # (guard-diff forbids adding approval markers).
    [[ "$rel" == "STEPPED_IMPLEMENTATION_PLAN.md" ]] && return 0

    rc=0
    step_allows "$rel" "$step" || rc=$?
    case "$rc" in
        0) return 0 ;;
        2) block "Locks §5" "STEP ${step} not found in STEPPED_IMPLEMENTATION_PLAN.md or has no ALLOWED_FILES" ;;
        *) block "Locks §5" "out of step scope: '$rel' is not in STEP ${step} ALLOWED_FILES" ;;
    esac
}

if [[ "${1:-}" == "--check-path" ]]; then
    [[ -n "${2:-}" ]] || { echo "usage: $0 --check-path <path> [--cwd <dir>] [--via-bash]" >&2; exit 64; }
    path_arg="$2"
    cwd_arg="$PWD"
    shift 2
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --cwd) cwd_arg="${2:-$PWD}"; shift 2 ;;
            --via-bash) VIA_BASH=1; shift ;;
            *) shift ;;
        esac
    done
    check_path "$path_arg" "$cwd_arg"
    exit 0
fi

input="$(cat)"
HOOK_INPUT="$input"
file_path="$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' <<<"$input")"
hook_cwd="$(jq -r '.cwd // empty' <<<"$input")"
[[ -z "$file_path" ]] && exit 0

# Approval markers are user-only, in any file and any mode.
new_text="$(jq -r '[.tool_input.content, .tool_input.new_string, .tool_input.new_source, (.tool_input.edits // [] | .[].new_string)] | map(select(. != null)) | join("\n")' <<<"$input")"
if grep -Eq '^[[:space:]]*#{1,6}[[:space:]]*APPROVED' <<<"$new_text"; then
    block "approval gate" "approval markers (## APPROVED <step>) are written only by the user"
fi

check_path "$file_path" "${hook_cwd:-$PWD}"
exit 0
