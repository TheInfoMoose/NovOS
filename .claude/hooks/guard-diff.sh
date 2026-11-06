#!/usr/bin/env bash
# ==============================================================================
# NovOS guard-diff — PostToolUse hook (Edit|Write).
# Lazy-fix / over-fix barrier. Inspects the change to the edited file relative
# to HEAD (whole file for new files) and exits 2 (feedback to the model) when:
#   lazy fixing  - ADDED code lines suppress errors or fake success, unless the
#                  line carries an inline `novos-allow: <reason>` annotation;
#   over fixing  - package/tool names disappear from a package manifest or the
#                  tracker, `enabled: true` flips to false, a version is lowered,
#                  or a verification gate line (check_file/check_cmd/verify_*)
#                  is removed from a script. Allowed only when the active step
#                  carries a user-written `## APPROVED <step>` marker.
# It never reverts anything; the model must fix the root cause or ask the user.
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
file_path="$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' <<<"$input")"
hook_cwd="$(jq -r '.cwd // empty' <<<"$input")"
[[ -z "$file_path" ]] && exit 0
[[ "$file_path" != /* ]] && file_path="${hook_cwd:-$PWD}/${file_path}"

abs="$(realpath -m -- "$file_path")"
rootabs="$(realpath -m -- "$ROOT")"
case "$abs" in
    "$rootabs"/*) rel="${abs#"$rootabs"/}" ;;
    *) exit 0 ;;   # outside the repository
esac
[[ -f "$abs" ]] || exit 0
git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
if git -C "$ROOT" check-ignore -q -- "$rel" 2>/dev/null; then
    exit 0   # ignored files (scratch, build output) are not part of the repo
fi

# --- Unified diff of the file vs HEAD (or vs nothing for untracked files) -----
if git -C "$ROOT" rev-parse --verify -q HEAD >/dev/null && git -C "$ROOT" cat-file -e "HEAD:${rel}" 2>/dev/null; then
    diff_text="$(git -C "$ROOT" diff --no-color -U0 HEAD -- "$rel")"
else
    diff_text="$(git -C "$ROOT" diff --no-color --no-index -U0 -- /dev/null "$abs" || true)"  # novos-allow: --no-index exits 1 when files differ
fi
[[ -z "$diff_text" ]] && exit 0

added="$(grep -E '^\+' <<<"$diff_text" | grep -Ev '^\+\+\+ ' | cut -c2- || true)"     # novos-allow: no added lines is valid
removed="$(grep -E '^-' <<<"$diff_text" | grep -Ev '^--- ' | cut -c2- || true)"       # novos-allow: no removed lines is valid

violations=()

# --- Lazy fixing (code and config files only; docs may quote the patterns) ----
is_code=0
case "$rel" in
    .claude/hooks/*) is_code=0 ;;   # the guards contain these patterns as data
    *.md|*.txt|*.svg|*.png|*.ron|LICENSE*) is_code=0 ;;
    *.sh|*.bash|*.py|*.rs|*.conf|*.yaml|*.yml|*.toml|*.env|*.json|*.service|*.desktop|*.cfg|*.ini|Dockerfile*|*/Dockerfile*|Makefile|*.mk) is_code=1 ;;
    scripts/*|calamares/*|config/*|docker/*|custom-tools/*) is_code=1 ;;
esac
if [[ "$is_code" -eq 1 && -n "$added" ]]; then
    # Parallel arrays: extended regex and the reason shown to the model.
    lazy_re=(
        '\|\|[[:space:]]*true([^A-Za-z0-9_]|$)'
        '\|\|[[:space:]]*:([[:space:];)]|$)'
        '(^|[^A-Za-z0-9_])set[[:space:]]+\+e([^A-Za-z0-9_]|$)'
        '2>[[:space:]]*/dev/null[[:space:]]*\|\|'
        '--force-depends'
        '--force-all'
        'trusted=yes'
        'equivs-build'
        'build_marker_deb'
        '#!?\[allow\('
        '@pytest\.mark\.skip'
        'exit[[:space:]]+0[[:space:]]*#[[:space:]]*skip'
        'continue[[:space:]]*#[[:space:]]*skip'
    )
    lazy_why=(
        '`|| true` error suppression'
        '`|| :` error suppression'
        '`set +e` disables fail-fast'
        '`2>/dev/null ||` hides a failure and its message'
        '`--force-depends` breaks dpkg dependency integrity'
        '`--force-all` overrides every dpkg safety check'
        '`trusted=yes` unauthenticated APT source'
        '`equivs-build` fake package'
        'fallback marker package (faked install)'
        '`#[allow(...)]` silences a Rust lint'
        'skipped test'
        '`exit 0 # skip` fakes success'
        '`continue # skip` silently skips work'
    )
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == *novos-allow:* ]] && continue
        for idx in "${!lazy_re[@]}"; do
            if grep -Eq -- "${lazy_re[$idx]}" <<<"$line"; then
                violations+=("lazy fix: ${lazy_why[$idx]}: ${line#"${line%%[![:space:]]*}"}")
                break
            fi
        done
    done <<<"$added"
fi

# --- Over fixing ----------------------------------------------------------------
active_step=""
[[ -f "${STATE_DIR}/active-step" ]] && active_step="$(tr -d '[:space:]' < "${STATE_DIR}/active-step")"
approved=0
if [[ -n "$active_step" && "$active_step" != "PLAN" && -f "$PLAN" ]] && grep -Eq "^##[[:space:]]+APPROVED[[:space:]]+${active_step}([[:space:]]|$)" "$PLAN"; then
    approved=1
fi

word_present() {  # name still present as a whole token in the new file?
    grep -Fq -- "$1" "$abs" && grep -Eq -- "(^|[^A-Za-z0-9+._:@/-])$(sed 's#[][\.*^$+?(){}|/]#\\&#g' <<<"$1")([^A-Za-z0-9+._:@/-]|$)" "$abs"
}

is_manifest=0
case "$rel" in
    config/packages.yaml|calamares/modules/netinstall.yaml|custom-tools/manifest.yaml|.tracker/*) is_manifest=1 ;;
esac
if [[ "$is_manifest" -eq 1 && -n "$removed" ]]; then
    mapfile -t gone < <(
        while IFS= read -r line; do
            # YAML list items / name keys, TOML quoted strings
            if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*(name:[[:space:]]*)?[\"\']?([A-Za-z0-9][A-Za-z0-9+._:@/-]*) ]]; then
                printf '%s\n' "${BASH_REMATCH[2]}"
            elif [[ "$line" =~ ^[[:space:]]*name:[[:space:]]*[\"\']?([A-Za-z0-9][A-Za-z0-9+._:@/-]*) ]]; then
                printf '%s\n' "${BASH_REMATCH[1]}"
            fi
            grep -oE '"[^"]+"' <<<"$line" | tr -d '"' || true   # novos-allow: lines without quoted strings
        done <<<"$removed" | sort -u
    )
    for name in "${gone[@]}"; do
        [[ -z "$name" || "$name" =~ ^(true|false|null|name|id|apt|flatpak|amd64|i386|all)$ ]] && continue
        word_present "$name" || violations+=("over fix: '${name}' removed from ${rel}")
    done
    if grep -Eq 'enabled:[[:space:]]*true' <<<"$removed" && grep -Eq 'enabled:[[:space:]]*false' <<<"$added"; then
        violations+=("over fix: an entry was flipped from 'enabled: true' to 'enabled: false' in ${rel}")
    fi
fi

# Version downgrades in manifests/config (key = "x.y" / key: x.y pairs).
if [[ "$is_manifest" -eq 1 || "$rel" == *.toml || "$rel" == config/* ]] && [[ -n "$removed" && -n "$added" ]]; then
    ver_re='^[[:space:]]*-?[[:space:]]*"?([A-Za-z0-9_.+-]*version[A-Za-z0-9_.+-]*|[A-Za-z0-9][A-Za-z0-9_.+-]*)"?[[:space:]]*[:=][[:space:]]*"?([0-9][0-9A-Za-z.+~:-]*)"?[[:space:]]*,?[[:space:]]*$'
    declare -A old_ver=()
    while IFS= read -r line; do
        [[ "$line" =~ $ver_re ]] && old_ver["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
    done <<<"$removed"
    while IFS= read -r line; do
        if [[ "$line" =~ $ver_re ]]; then
            key="${BASH_REMATCH[1]}"; new="${BASH_REMATCH[2]}"
            old="${old_ver[$key]:-}"
            [[ -z "$old" || "$old" == "$new" ]] && continue
            lower=0
            if command -v dpkg >/dev/null 2>&1; then
                dpkg --compare-versions "$new" lt "$old" && lower=1
            elif [[ "$(printf '%s\n%s\n' "$old" "$new" | sort -V | head -n1)" == "$new" ]]; then
                lower=1
            fi
            (( lower )) && violations+=("over fix: version of '${key}' lowered ${old} -> ${new} in ${rel}")
        fi
    done <<<"$added"
fi

# Removed verification gates in scripts.
if [[ "$rel" == *.sh && -n "$removed" ]]; then
    while IFS= read -r line; do
        trimmed="${line#"${line%%[![:space:]]*}"}"
        trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
        [[ -z "$trimmed" || "$trimmed" == \#* ]] && continue
        if [[ "$trimmed" =~ (^|[^A-Za-z0-9_])(check_file|check_cmd|verify_[a-z0-9_]+)([^A-Za-z0-9_]|$) ]]; then
            grep -Fq -- "$trimmed" "$abs" || violations+=("over fix: verification gate removed from ${rel}: ${trimmed}")
        fi
    done <<<"$removed"
fi

# Lazy findings always block; over-fix findings are waived by a user approval marker.
lazy_count=0
for v in "${violations[@]}"; do [[ "$v" == lazy* ]] && lazy_count=$((lazy_count + 1)); done
if [[ "${#violations[@]}" -gt 0 && ( "$lazy_count" -gt 0 || "$approved" -eq 0 ) ]]; then
    {
        echo "Lazy-fix/over-fix barrier: ${#violations[@]} finding(s) in ${rel}. Fix the root cause or ask the user for explicit approval."
        n=0
        for v in "${violations[@]}"; do
            [[ "$approved" -eq 1 && "$v" == over* ]] && continue
            echo "  - ${v}"
            n=$((n + 1))
            [[ "$n" -ge 15 ]] && { echo "  ..."; break; }
        done
        echo "Lazy findings need the real fix or an inline '# novos-allow: <reason>' the user agrees with; over-fix findings need a user-written '## APPROVED <step>' marker."
    } >&2
    exit 2
fi
exit 0
