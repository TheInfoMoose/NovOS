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
        if [[ "$p" =~ golden-snapshot|approvals\.toml|manifest\.toml|config/releases|\.tracker/release|\.claude/state ]]; then
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
        block "protected file" "'$rel' is user-only (golden snapshot / approvals / manifest / release records / hook state)"
    fi

    if is_conditional "$rel"; then
        if [[ -z "$step" || "$step" == "PLAN" ]]; then
            block "protected file" "'$rel' may only change inside a step that lists it in ALLOWED_FILES"
        fi
        rc=0
        step_allows "$rel" "$step" || rc=$?
        [[ "$rc" -eq 0 ]] || block "Locks §5" "'$rel' is not in STEP ${step} ALLOWED_FILES"
        return 0
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
