#!/usr/bin/env bash
# ==============================================================================
# NovOS guard hook self-test (table-driven).
# Feeds synthetic hook payloads to guard-bash, guard-edit, guard-diff and
# step-lock and asserts their exit codes / state. Runs entirely inside a
# mktemp -d fixture (own plan file, own git repo, own state dir); never touches
# the real .claude/state or repository files.
# Usage: .claude/hooks/test-guards.sh [-v]
# ==============================================================================
set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE=0
[[ "${1:-}" == "-v" ]] && VERBOSE=1

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP:?}"' EXIT

# Isolate from any live Claude Code session and from the real repo.
unset CLAUDE_PROJECT_DIR
FIX="${TMP}/repo"
export NOVOS_GUARD_ROOT="$FIX"
export NOVOS_GUARD_STATE="${TMP}/state"
mkdir -p "$FIX" "$NOVOS_GUARD_STATE"

pass=0
fail=0
report() {  # report <ok 0|1> <label>
    if [[ "$1" -eq 1 ]]; then
        pass=$((pass + 1))
        [[ "$VERBOSE" -eq 1 ]] && printf 'PASS  %s\n' "$2"
    else
        fail=$((fail + 1))
        printf 'FAIL  %s\n' "$2"
        [[ -s "${TMP}/err" ]] && sed 's/^/      /' "${TMP}/err" | head -n 3
    fi
    return 0
}

set_step() {
    if [[ -n "$1" ]]; then printf '%s\n' "$1" > "${NOVOS_GUARD_STATE}/active-step"; else rm -f "${NOVOS_GUARD_STATE}/active-step"; fi
}

# ------------------------------------------------------------------------------
# Fixture: plan with two steps, protected files, a git repo with seed content.
# ------------------------------------------------------------------------------
mkdir -p "${FIX}/.nebulous" "${FIX}/scripts" "${FIX}/config" "${FIX}/docs"
cat > "${FIX}/STEPPED_IMPLEMENTATION_PLAN.md" <<'PLAN'
# Fixture plan

### STEP 1A: In-scope test step

```text
[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/a.sh
  - docs/ (whole directory)
  - config/suppression-baseline.toml
READ_ONLY_REFS:
  - README.md
FORBIDDEN_SCOPE:
  - Any other file.

[POST_GATE]
bash -n scripts/a.sh

[HALT_INVARIANT]
Halt.
```

### STEP 2B: Approved removal step

```text
[SCOPE_LOCK]
ALLOWED_FILES:
  - config/packages.yaml
READ_ONLY_REFS:
  - none
[POST_GATE]
true
```

## APPROVED 2B
PLAN
printf 'golden = 1\n' > "${FIX}/.nebulous/golden-snapshot.toml"
cat > "${FIX}/scripts/a.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
check_file /etc/os-release
verify_stage1_post /mnt
echo ok
SH
cat > "${FIX}/config/packages.yaml" <<'YAML'
schema_version: 1
groups:
  - id: base-admin
    packages:
      - name: mdadm
      - name: lvm2
      - name: smartmontools
tools:
  - name: helper
    enabled: true
    version: 1.4.0
YAML
printf '# readme\n' > "${FIX}/README.md"
git -C "$FIX" init -q
git -C "$FIX" add -A
git -C "$FIX" -c user.name=test -c user.email=test@example.invalid commit -q -m seed

# ------------------------------------------------------------------------------
# guard-bash cases: want|step|command
# ------------------------------------------------------------------------------
bash_case() {
    local want="$1" step="$2" cmd="$3" rc=0
    set_step "$step"
    jq -n --arg c "$cmd" --arg d "$FIX" '{tool_input:{command:$c},cwd:$d}' \
        | "${HOOK_DIR}/guard-bash.sh" >/dev/null 2>"${TMP}/err" || rc=$?
    report "$([[ "$rc" -eq "$want" ]] && echo 1 || echo 0)" "guard-bash want=${want} got=${rc} [${step:-none}] ${cmd//$'\n'/⏎}"
}

bash_case 2 "" 'sudo apt-get install foo'
bash_case 2 "" 'ls && sudo rm -rf /'
bash_case 2 "" 'bash -c "mkfs.ext4 x"'
bash_case 2 "" "sh -lc 'echo hi; wipefs -a /dev/sda'"
bash_case 2 "" 'dd if=a of=/dev/sda'
bash_case 0 "" 'dd if=/dev/zero of=./file bs=1M count=1'
bash_case 2 "" './scripts/build-novos.sh --from-stage 3'
bash_case 2 "" 'sudo ./scripts/build-novos.sh --target-iso ./build/x.iso'
bash_case 2 "" './scripts/test-qemu.sh --iso x.iso'
bash_case 0 "" 'bash -n scripts/build-novos.sh'
bash_case 0 "" 'cargo test --workspace'
bash_case 0 "" 'cargo clippy --workspace --all-targets -- -D warnings'
bash_case 2 "" 'echo x > /etc/fstab'
bash_case 2 "" '{ sudo ls; }'
bash_case 2 "" 'timeout -s KILL 5 mkfs.ext4 x'
bash_case 2 "" "python3 -c 'import os; os.system(\"sudo ls\")'"
bash_case 2 "" $'bash <<EOF\nsudo ls\nEOF'
bash_case 2 "" 'python3 scripts/nebulous-ctl.py bless-golden'
bash_case 2 "" 'cargo run -p neb -- release bless'
bash_case 2 "" 'rm -f .nebulous/golden-snapshot.toml'
bash_case 2 "" 'cd .nebulous && echo x > golden-snapshot.toml'
bash_case 2 "" 'chmod -x .claude/hooks/guard-bash.sh'
bash_case 2 "" 'echo "## APPROVED 1A" >> STEPPED_IMPLEMENTATION_PLAN.md'
bash_case 0 "" 'git commit -m "drop sudo; mkfs > /etc/fstab is blocked"'
bash_case 0 "" $'git commit -q -F - <<\'EOF\'\nsudo ./scripts/build-novos.sh\nit\'s mkfs.ext4 /dev/sda\nEOF'
bash_case 0 "" "grep -rn 'sudo' scripts/"
bash_case 0 "" 'echo hi > /tmp/guard-test-out 2>&1'
bash_case 0 "1A" 'sed -i s/ok/fine/ scripts/a.sh'
bash_case 2 "1A" 'sed -i s/x/y/ README.md'
bash_case 2 "1A" 'git reset --hard'

# ------------------------------------------------------------------------------
# guard-edit cases: want|step|path|new_string
# ------------------------------------------------------------------------------
edit_case() {
    local want="$1" step="$2" path="$3" text="${4:-}" rc=0
    set_step "$step"
    jq -n --arg f "$path" --arg t "$text" --arg d "$FIX" '{tool_input:{file_path:$f,new_string:$t},cwd:$d}' \
        | "${HOOK_DIR}/guard-edit.sh" >/dev/null 2>"${TMP}/err" || rc=$?
    report "$([[ "$rc" -eq "$want" ]] && echo 1 || echo 0)" "guard-edit want=${want} got=${rc} [${step:-none}] ${path}"
}

edit_case 2 ""   .nebulous/golden-snapshot.toml
edit_case 2 ""   .nebulous/approvals.toml
edit_case 2 ""   .claude/settings.local.json
edit_case 2 ""   config/suppression-baseline.toml
edit_case 0 ""   README.md
edit_case 0 ""   /tmp/guard-test-scratch.txt
edit_case 2 ""   STEPPED_IMPLEMENTATION_PLAN.md $'text\n## APPROVED 1A\n'
edit_case 0 "1A" scripts/a.sh
edit_case 0 "1A" docs/new-page.md
edit_case 0 "1A" config/suppression-baseline.toml
edit_case 2 "1A" README.md
edit_case 2 "1A" config/packages.yaml
edit_case 0 "1A" STEPPED_IMPLEMENTATION_PLAN.md 'roadmap status update'
edit_case 2 "9Z" scripts/a.sh
edit_case 0 "PLAN" STEPPED_IMPLEMENTATION_PLAN.md
edit_case 2 "PLAN" scripts/a.sh

# Live hook mode must ignore test overrides.
rc=0
set_step ""
jq -n --arg d "$FIX" '{tool_input:{file_path:".nebulous/approvals.toml"},cwd:$d}' \
    | CLAUDE_PROJECT_DIR="$FIX" NOVOS_GUARD_ROOT=/tmp "${HOOK_DIR}/guard-edit.sh" >/dev/null 2>"${TMP}/err" || rc=$?
report "$([[ "$rc" -eq 2 ]] && echo 1 || echo 0)" "guard-edit ignores NOVOS_GUARD_ROOT when CLAUDE_PROJECT_DIR is set (got ${rc})"

# ------------------------------------------------------------------------------
# guard-diff cases: mutate a file in the fixture repo, run the hook, restore.
# ------------------------------------------------------------------------------
diff_case() {  # diff_case <want> <step> <file> <label> ; file content must already be modified
    local want="$1" step="$2" file="$3" label="$4" rc=0
    set_step "$step"
    jq -n --arg f "$file" --arg d "$FIX" '{tool_input:{file_path:$f},cwd:$d}' \
        | "${HOOK_DIR}/guard-diff.sh" >/dev/null 2>"${TMP}/err" || rc=$?
    report "$([[ "$rc" -eq "$want" ]] && echo 1 || echo 0)" "guard-diff want=${want} got=${rc} [${step:-none}] ${label}"
    git -C "$FIX" checkout -q -- . 2>/dev/null || true   # novos-allow: restore fixture between cases
    git -C "$FIX" clean -qfd -- scripts config docs 2>/dev/null || true   # novos-allow: remove fixture scratch files
}

printf 'apt-get install foo || true\n' >> "${FIX}/scripts/a.sh"
diff_case 2 "" scripts/a.sh "added '|| true'"

printf 'apt-get install foo || true  # novos-allow: optional package probe\n' >> "${FIX}/scripts/a.sh"
diff_case 0 "" scripts/a.sh "annotated '|| true' allowed"

printf 'set +e\n' >> "${FIX}/scripts/a.sh"
diff_case 2 "" scripts/a.sh "added 'set +e'"

printf 'deb [trusted=yes] file:/x ./\n' > "${FIX}/config/extra.list"
diff_case 2 "" config/extra.list "new file with trusted=yes"

mkdir -p "${FIX}/docs"
printf 'Never use `|| true` in stage scripts.\n' >> "${FIX}/docs/rules.md"
diff_case 0 "" docs/rules.md "docs may quote patterns"

sed -i '/check_file/d' "${FIX}/scripts/a.sh"
diff_case 2 "" scripts/a.sh "removed check_file gate"

printf 'echo moved\n' >> "${FIX}/scripts/a.sh"
diff_case 0 "" scripts/a.sh "plain added line allowed"

sed -i '/name: lvm2/d' "${FIX}/config/packages.yaml"
diff_case 2 "" config/packages.yaml "package removed from manifest"

sed -i 's/enabled: true/enabled: false/' "${FIX}/config/packages.yaml"
diff_case 2 "" config/packages.yaml "tool disabled"

sed -i 's/version: 1.4.0/version: 1.3.9/' "${FIX}/config/packages.yaml"
diff_case 2 "" config/packages.yaml "version lowered"

sed -i 's/version: 1.4.0/version: 1.5.0/' "${FIX}/config/packages.yaml"
diff_case 0 "" config/packages.yaml "version raised"

sed -i '/name: lvm2/d' "${FIX}/config/packages.yaml"
diff_case 0 "2B" config/packages.yaml "removal waived by user APPROVED marker"

sed -i '/name: lvm2/d' "${FIX}/config/packages.yaml"
printf '      - name: lvm2\n' >> "${FIX}/config/packages.yaml"
diff_case 0 "" config/packages.yaml "moved package is not a removal"

printf 'x || true\n' >> "${FIX}/scripts/a.sh"
diff_case 2 "2B" scripts/a.sh "approval marker does not waive lazy fixes"

# ------------------------------------------------------------------------------
# step-lock cases: prompt -> expected active-step and allow_vm_test
# ------------------------------------------------------------------------------
lock_case() {  # lock_case <prompt> <want-step or -> <want-vm>
    local prompt="$1" want_step="$2" want_vm="$3" got_step got_vm
    jq -n --arg p "$prompt" '{prompt:$p}' | "${HOOK_DIR}/step-lock.sh" >/dev/null 2>"${TMP}/err"
    got_step="$(cat "${NOVOS_GUARD_STATE}/active-step" 2>/dev/null || echo -)"
    got_vm="$(cat "${NOVOS_GUARD_STATE}/allow_vm_test" 2>/dev/null || echo 0)"
    report "$([[ "$got_step" == "$want_step" && "$got_vm" == "$want_vm" ]] && echo 1 || echo 0)" \
        "step-lock '${prompt}' -> step=${got_step} vm=${got_vm} (want ${want_step}/${want_vm})"
}

set_step ""
lock_case 'Run STEP 1A' 1A 0
lock_case 'continue' 1A 0
lock_case '/boost STEP 2b' 2B 0
lock_case "Don't run STEP 1A yet, just review it" - 0
lock_case '/goal STEP 12AB' - 0
lock_case '/plan-argon add a step' PLAN 0
lock_case 'Please run the VM test.' - 1
lock_case 'Do not run the VM test' - 0
lock_case 'Commit the working tree changes' - 0

# ------------------------------------------------------------------------------
total=$((pass + fail))
echo "test-guards: ${pass}/${total} passed"
[[ "$fail" -eq 0 ]]
