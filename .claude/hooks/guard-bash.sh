#!/usr/bin/env bash
# ==============================================================================
# NovOS guard-bash — PreToolUse hook (Bash).
# Replicates Locks §1 (zero host root), §2 (no destructive disk/boot/firmware
# ops; propose-and-yield), §4 (fresh Stage 1-6 builds only; user runs host
# builds and VM tests), the permissions.json user-only nebulous-ctl commands,
# and forwards file-mutating commands to guard-edit.sh (Locks §5 scope lock).
# Exit 0 = allow, exit 2 = block (stderr is fed back to the model).
#
# Threat model: stops accidental, lazy or over-reaching agent actions. It is a
# static parser, not a sandbox: code generated at runtime (variables expanding
# to commands, decoded payloads, interpreters writing files) cannot be fully
# inspected. Those cases are covered by .claude/settings.json permissions.
# ==============================================================================
set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
    # Live hook: never honour test overrides (they could redirect the guard).
    ROOT="$CLAUDE_PROJECT_DIR"
    STATE_DIR="${ROOT}/.claude/state"
    unset NOVOS_GUARD_ROOT NOVOS_GUARD_STATE
else
    ROOT="${NOVOS_GUARD_ROOT:-$(cd "${HOOK_DIR}/../.." && pwd)}"
    STATE_DIR="${NOVOS_GUARD_STATE:-${ROOT}/.claude/state}"
    export NOVOS_GUARD_ROOT="$ROOT" NOVOS_GUARD_STATE="$STATE_DIR"
fi

input="$(cat)"
cmd="$(jq -r '.tool_input.command // empty' <<<"$input")"
cwd="$(jq -r '.cwd // empty' <<<"$input")"
[[ -z "$cmd" ]] && exit 0
[[ -z "$cwd" ]] && cwd="$PWD"

block() {
    echo "BLOCKED by guard-bash ($1): $2" >&2
    echo "Propose-and-yield: show the user the exact command, its blast radius and a dry-run/diff, then halt for approval." >&2
    exit 2
}

active_step=""
[[ -f "${STATE_DIR}/active-step" ]] && active_step="$(tr -d '[:space:]' < "${STATE_DIR}/active-step")"
allow_vm_test=0
if [[ -f "${STATE_DIR}/allow_vm_test" && "$(tr -d '[:space:]' < "${STATE_DIR}/allow_vm_test")" == "1" ]]; then
    allow_vm_test=1
fi

PRIV_RE='sudo|doas|pkexec|run0|runuser'
DESTRUCT_RE='mkfs(\.[a-z0-9]+)?|mke2fs|mkswap|mkdosfs|mkntfs|wipefs|fdisk|gdisk|sgdisk|parted|flashrom|grub2?-install|blkdiscard'

# ------------------------------------------------------------------------------
# Normalizer: one pass over the command that
#   - joins backslash-newline continuations,
#   - drops comments and heredoc bodies (detected only in unquoted text),
#   - copies $((...)) arithmetic verbatim (no heredoc detection inside),
#   - neutralizes quoted text (separators/whitespace -> `_`, backslashes
#     removed, $'..' decoded) so commit messages are inert, EXCEPT payloads
#     handed to an interpreter (`bash -c`, `python3 -c`, `perl -e`, `eval`,
#     "$(...)"), which are re-normalized and emitted inside ( ) so they are
#     segmented and checked like top-level commands.
# Records to HITS_FILE any interpreter payload naming a privileged or
# destructive tool anywhere (catches os.system("sudo ...") style calls).
# ------------------------------------------------------------------------------
# normalize() runs in command substitutions (subshells), so payload hits are
# collected in a temp file rather than a variable.
HITS_FILE="$(mktemp)"
trap 'rm -f "$HITS_FILE"' EXIT
normalize() {
    local s="$1" out="" tok="" last_word="" seg_words="" c q buf ref ansi i=0 n depth
    local -a heredocs=()
    s="${s//$'\\\n'/}"
    n=${#s}
    while (( i < n )); do
        c="${s:i:1}"

        # Heredoc bodies start after the newline that ends the introducing line.
        if [[ "$c" == $'\n' && "${#heredocs[@]}" -gt 0 ]]; then
            out+=$'\n'; i=$((i + 1))
            local hd line strip
            for hd in "${heredocs[@]}"; do
                strip=0
                [[ "$hd" == -* ]] && { strip=1; hd="${hd#-}"; }
                while (( i < n )); do
                    line="${s:i}"; line="${line%%$'\n'*}"
                    i=$((i + ${#line} + 1))
                    (( strip )) && line="${line#"${line%%[!$'\t']*}"}"
                    [[ "$line" == "$hd" ]] && break
                done
            done
            heredocs=()
            tok=""; last_word=""; seg_words=""
            continue
        fi

        if [[ "$c" == "\\" ]]; then
            out+="${s:i+1:1}"; tok+="${s:i+1:1}"; i=$((i + 2)); continue
        fi

        # Comment: unquoted `#` at the start of a word runs to end of line.
        if [[ "$c" == "#" && -z "$tok" ]]; then
            while (( i < n )) && [[ "${s:i:1}" != $'\n' ]]; do i=$((i + 1)); done
            continue
        fi

        # Arithmetic $(( ... )) / (( ... )): copy verbatim up to the matching )).
        if [[ "${s:i:3}" == '$((' || ( "${s:i:2}" == '((' && -z "$tok" ) ]]; then
            depth=0
            local opened=0
            while (( i < n )); do
                c="${s:i:1}"
                [[ "$c" == "(" ]] && { depth=$((depth + 1)); opened=1; }
                [[ "$c" == ")" ]] && depth=$((depth - 1))
                out+="$c"; i=$((i + 1))
                (( opened && depth == 0 )) && break
            done
            tok+="A"
            continue
        fi

        # Heredoc introducer (not a here-string <<<).
        if [[ "${s:i:2}" == "<<" && "${s:i:3}" != "<<<" ]] && { (( i == 0 )) || [[ "${s:i-1:1}" != "<" ]]; }; then
            local rest="${s:i+2}" dash="" word
            [[ "$rest" == -* ]] && { dash="-"; rest="${rest:1}"; }
            rest="${rest#"${rest%%[![:space:]]*}"}"
            if [[ "$rest" =~ ^[\"\']?([A-Za-z_][A-Za-z0-9_]*)[\"\']? ]]; then
                word="${BASH_REMATCH[1]}"
                heredocs+=("${dash}${word}")
                out+="<<${word}"
                i=$(( n - ${#rest} + ${#BASH_REMATCH[0]} ))
                tok+="H"
                continue
            fi
        fi

        ansi=0
        if [[ "$c" == '$' && ( "${s:i+1:1}" == "'" || "${s:i+1:1}" == '"' ) ]]; then
            [[ "${s:i+1:1}" == "'" ]] && ansi=1
            i=$((i + 1)); c="${s:i:1}"
        fi
        if [[ "$c" == "'" || "$c" == '"' ]]; then
            q="$c"; buf=""; i=$((i + 1))
            while (( i < n )) && [[ "${s:i:1}" != "$q" ]]; do
                if [[ ( "$q" == '"' || "$ansi" -eq 1 ) && "${s:i:1}" == "\\" ]]; then
                    buf+="${s:i:2}"; i=$((i + 2)); continue
                fi
                buf+="${s:i:1}"; i=$((i + 1))
            done
            i=$((i + 1))
            (( ansi )) && buf="$(printf '%b' "$buf")"
            ref="${tok:-$last_word}"
            if [[ ( "$ref" =~ ^-[a-zA-Z]*[ce]$ && " $seg_words " =~ [[:space:]](bash|sh|dash|zsh|ksh|python[0-9.]*|perl|ruby|node|php)[[:space:]] ) \
                  || ( "$ref" =~ ^(-S|--split-string)$ && " $seg_words " =~ [[:space:]]env[[:space:]] ) \
                  || "$ref" == "eval" \
                  || ( "$q" == '"' && ( "$buf" == *'$('* || "$buf" == *'`'* ) ) ]]; then
                # Interpreter code strings (not "$(...)" substitutions, whose heredoc
                # bodies are stripped by the recursive normalize) are grepped raw.
                if [[ "$ref" =~ ^-[a-zA-Z]*[ceS]$|^--split-string$|^eval$ ]] && \
                   grep -Eq "(^|[^A-Za-z0-9_.-])(${PRIV_RE}|${DESTRUCT_RE})([^A-Za-z0-9_-]|$)" <<<"$buf"; then
                    printf '%s\n' "$buf" >> "$HITS_FILE"
                fi
                out+=" ( $(normalize "$buf") ) "
            else
                buf="${buf//\\/}"
                buf="$(printf '%s' "$buf" | tr '[:space:];&|()`<>#{}' '_')"
                out+="Q:${buf}"   # Q: marks inert quoted text (stripped again per word)
            fi
            tok+="${buf:0:40}"
            continue
        fi

        if [[ "$c" =~ [[:space:]] ]]; then
            if [[ -n "$tok" ]]; then last_word="$tok"; seg_words+=" ${tok##*/}"; fi
            tok=""
            [[ "$c" == $'\n' ]] && seg_words=""
        elif [[ "$c" == ";" || "$c" == "|" || "$c" == "&" ]]; then
            [[ -n "$tok" ]] && last_word="$tok"
            tok=""; seg_words=""
        else
            tok+="$c"
        fi
        out+="$c"; i=$((i + 1))
    done
    printf '%s' "$out"
}

norm="$(normalize "$cmd")"
if [[ -s "$HITS_FILE" ]]; then
    block "Locks §1/§2" "interpreter payload invokes a privileged/destructive tool: $(head -c 160 "$HITS_FILE")"
fi
norm="${norm//>|/>}"     # clobber redirect >| is still a redirect

# --- Whole-command checks -------------------------------------------------------
if grep -Eq '(>>?|tee([[:space:]]+-[a-z]+)*)[[:space:]]*/etc/fstab' <<<"$norm"; then
    block "Locks §2" "raw write to /etc/fstab"
fi
if grep -Eq '(>>?|tee([[:space:]]+-[a-z]+)*)[[:space:]]*/dev/(sd|nvme|vd|hd|xvd|mmcblk|loop|disk/|mapper/|md|dm-|sr)' <<<"$norm"; then
    block "Locks §2" "write to a block device"
fi
if grep -Eq '/sys/firmware/efi/efivars' <<<"$norm" \
   && grep -Eq '(>|(^|[^A-Za-z0-9_])(tee|rm|chattr|cp|mv|dd|efivar)([^A-Za-z0-9_]|$))' <<<"$norm"; then
    block "Locks §2" "EFI NVRAM variable mutation"
fi
if grep -q 'APPROVED' <<<"$cmd" && grep -Eq '(>|(^|[^A-Za-z0-9_])(tee|sed|perl|awk|cp|mv|dd|install|python[0-9.]*|ruby|node|printf|echo)([^A-Za-z0-9_]|$))' <<<"$norm"; then
    block "approval gate" "approval markers (## APPROVED <step>) are written only by the user"
fi
# Unquoted privileged/destructive words anywhere (backstop for wrappers the
# segment parser does not know). Quote search patterns to avoid this.
if grep -Eq "(^|[^A-Za-z0-9_./:-])(${PRIV_RE})([^A-Za-z0-9_-]|$)" <<<"$norm"; then
    block "Locks §1" "privileged command word found (if this is only a search pattern, quote it)"
fi

# --- Segment ------------------------------------------------------------------
# find -exec/-execdir/-ok run a nested command: start a new segment there.
norm="$(sed -E 's/[[:space:]]-(exec|execdir|ok|okdir)[[:space:]]/\n/g' <<<"$norm")"
norm="$(sed -E 's/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/$\1/g' <<<"$norm")"   # ${VAR} -> $VAR before splitting on braces
mapfile -t segments < <(sed -E 's/\$\(|`|&&|\|\||[;|&(){}]/\n/g' <<<"$norm")

check_mutation_target() {
    local target="$1" t
    [[ -z "$target" ]] && return 0
    case "$target" in
        /dev/null|/dev/stdout|/dev/stderr|/dev/fd/*|/proc/self/*|-) return 0 ;;
    esac
    if [[ "$target" == *[\*\?\[]* && "$target" != *'$'* ]]; then
        local -a expanded=()
        mapfile -t expanded < <(cd "$cwd" 2>/dev/null && compgen -G "$target" || true)  # novos-allow: no glob match is valid
        if [[ "${#expanded[@]}" -eq 0 ]]; then
            "${HOOK_DIR}/guard-edit.sh" --check-path "$target" --cwd "$cwd" --via-bash || exit 2
        fi
        for t in "${expanded[@]}"; do
            "${HOOK_DIR}/guard-edit.sh" --check-path "$t" --cwd "$cwd" --via-bash || exit 2
        done
        return 0
    fi
    "${HOOK_DIR}/guard-edit.sh" --check-path "$target" --cwd "$cwd" --via-bash || exit 2
}

step_mode_block() {
    [[ -n "$active_step" ]] && block "Locks §5" "$1 rewrites files beyond STEP ${active_step} ALLOWED_FILES; edit scoped files directly"
    return 0
}

non_option_words() {
    # Words that are not options; everything after `--` counts as a path.
    local w dashdash=0
    for w in "$@"; do
        if [[ "$dashdash" -eq 1 ]]; then
            printf '%s\n' "$w"
        elif [[ "$w" == "--" ]]; then
            dashdash=1
        elif [[ "$w" != -* ]]; then
            printf '%s\n' "$w"
        fi
    done
}

resolve_dir() {
    local d="$1"
    d="${d/#\~/$HOME}"
    [[ "$d" != /* ]] && d="${cwd}/${d}"
    realpath -m -- "$d"
}

for seg in "${segments[@]}"; do
    read -r -a words <<<"$seg" || true  # novos-allow: read returns 1 at EOF without newline
    [[ "${#words[@]}" -eq 0 ]] && continue
    words=("${words[@]#Q:}")
    seg="${seg//Q:/}"

    # Redirection targets inside this segment (`> file`, `>> file`, `2>file`).
    while IFS= read -r redir; do
        redir="${redir#*>}"
        redir="${redir#>}"
        redir="${redir#"${redir%%[![:space:]]*}"}"
        [[ "$redir" == \&* ]] && continue
        check_mutation_target "$redir"
    done < <(grep -oE '[0-9]?>{1,2}[[:space:]]*[^[:space:]&>]+' <<<"$seg" || true)  # novos-allow: grep exits 1 when no redirection

    # Strip shell keywords and wrappers until the real command word is first.
    changed=1
    while [[ "$changed" -eq 1 && "${#words[@]}" -gt 0 ]]; do
        changed=0
        w0="${words[0]}"
        case "$w0" in
            '!'|then|do|else|elif|if|while|until|coproc|time|'[['|'[')
                words=("${words[@]:1}"); changed=1 ;;
            *=*)
                [[ "$w0" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && { words=("${words[@]:1}"); changed=1; } ;;
            env|/usr/bin/env)
                words=("${words[@]:1}")
                while [[ "${#words[@]}" -gt 0 ]]; do
                    case "${words[0]}" in
                        -u|-C|--unset|--chdir) words=("${words[@]:2}") ;;
                        -S|--split-string) words=("${words[@]:1}") ;;
                        -*|*=*) words=("${words[@]:1}") ;;
                        *) break ;;
                    esac
                done
                changed=1 ;;
            command|builtin|nohup|stdbuf|ionice|setsid|xargs|watch|strace|ltrace|unshare|busybox|parallel|nice|taskset|chrt|faketime|catchsegv)
                words=("${words[@]:1}")
                while [[ "${#words[@]}" -gt 0 && ( "${words[0]}" == -* || "${words[0]}" =~ ^[A-Za-z_][A-Za-z0-9_]*= || "${words[0]}" =~ ^[0-9.,]+$ ) ]]; do
                    words=("${words[@]:1}")
                done
                changed=1 ;;
            exec)
                words=("${words[@]:1}")
                while [[ "${#words[@]}" -gt 0 && "${words[0]}" == -* ]]; do
                    [[ "${words[0]}" == "-a" ]] && words=("${words[@]:1}")
                    words=("${words[@]:1}")
                done
                changed=1 ;;
            timeout)
                words=("${words[@]:1}")
                while [[ "${#words[@]}" -gt 0 && "${words[0]}" == -* ]]; do
                    [[ "${words[0]}" =~ ^-(s|k)$ || "${words[0]}" =~ ^--(signal|kill-after)$ ]] && words=("${words[@]:1}")
                    words=("${words[@]:1}")
                done
                [[ "${#words[@]}" -gt 0 ]] && words=("${words[@]:1}")  # duration
                changed=1 ;;
            flock|chroot|sg|ssh)
                # wrapper with one operand (lock file / new root / group / host) before the command
                words=("${words[@]:1}")
                while [[ "${#words[@]}" -gt 0 && "${words[0]}" == -* ]]; do words=("${words[@]:1}"); done
                [[ "${#words[@]}" -gt 0 ]] && words=("${words[@]:1}")
                changed=1 ;;
            bash|sh|dash|zsh|ksh|/bin/bash|/bin/sh|/usr/bin/bash)
                if [[ "${#words[@]}" -gt 1 && "${words[1]}" =~ ^-[a-zA-Z]*c[a-zA-Z]*$ ]]; then
                    words=("${words[@]:2}"); changed=1
                fi ;;
        esac
    done
    [[ "${#words[@]}" -eq 0 ]] && continue

    raw_w0="${words[0]}"
    w0="$(basename -- "$raw_w0")"
    args=("${words[@]:1}")
    seg_text=" ${words[*]} "

    # Command names coming from variables cannot be inspected.
    if [[ "$raw_w0" =~ ^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$ ]]; then
        block "Locks §1/§2" "command name '$raw_w0' comes from a variable and cannot be inspected; write the command literally"
    fi

    # cd/pushd change where later relative paths resolve.
    if [[ "$w0" == "cd" || "$w0" == "pushd" ]]; then
        target_dir="$(non_option_words "${args[@]}" | head -n1)"
        if [[ -z "$target_dir" || "$target_dir" == "-" || "$target_dir" == *'$'* ]]; then
            cwd="$HOME"
        else
            cwd="$(resolve_dir "$target_dir")"
        fi
        continue
    fi

    # Interpreter-run scripts: `bash -o pipefail scripts/x.sh`, `python3 scripts/y.py`, `. x.sh`.
    script_word="$w0"
    shell_reads_stdin=0
    if [[ "$w0" =~ ^(bash|sh|dash|zsh|ksh|python[0-9.]*|source|\.)$ ]]; then
        script_word=""
        skip_next=0
        for a in "${args[@]}"; do
            if (( skip_next )); then skip_next=0; continue; fi
            if [[ "$a" =~ ^[-+]o$ || "$a" =~ ^[-+]O$ ]]; then skip_next=1; continue; fi
            if [[ "$a" =~ ^[0-9]*\< ]]; then
                # input redirected before any script: the shell executes stdin
                [[ "$w0" =~ ^(bash|sh|dash|zsh|ksh)$ ]] && shell_reads_stdin=1
                [[ "$a" =~ ^[0-9]*\<+$ ]] && skip_next=1
                continue
            fi
            [[ "$a" == -* || "$a" == +* ]] && continue
            script_word="$(basename -- "$a")"
            break
        done
        if [[ -z "$script_word" && "$w0" =~ ^(bash|sh|dash|zsh|ksh)$ && "$seg_text" != *" --version "* && "$seg_text" != *" --help "* ]]; then
            shell_reads_stdin=1
        fi
        [[ "$seg_text" =~ [[:space:]](--version|--help)[[:space:]] ]] && shell_reads_stdin=0
    fi
    if (( shell_reads_stdin )); then
        block "Locks §1/§2" "shell reading commands from stdin/process substitution cannot be inspected; run the commands directly"
    fi
    static_check=0
    [[ "$seg_text" =~ [[:space:]](-n|--help|-h|--self-test|--version)[[:space:]] ]] && static_check=1

    # Locks §1 — zero host root privilege.
    case "$w0" in
        sudo|su|doas|pkexec|run0|runuser)
            block "Locks §1" "'$w0' requires host root; output the command for the user instead" ;;
    esac

    # Locks §2 — destructive block/partition/filesystem/firmware/bootloader ops.
    case "$w0" in
        fdisk|gdisk|cgdisk|sgdisk|parted|wipefs|flashrom|grub-install|grub2-install|mke2fs|mkswap|mkfs|mkfs.*|mkdosfs|mkntfs|mkexfatfs|blkdiscard)
            block "Locks §2" "'$w0' is a destructive disk/boot operation" ;;
        cryptsetup)
            [[ "$seg_text" =~ [[:space:]](luksFormat|erase|luksErase|reencrypt|luksKillSlot|luksRemoveKey|luksHeaderRestore|repair|convert)[[:space:]] ]] && \
                block "Locks §2" "destructive cryptsetup operation" ;;
        sfdisk)
            [[ "$seg_text" =~ [[:space:]](-d|--dump|-l|--list|-J|--json)[[:space:]] ]] || \
                block "Locks §2" "sfdisk is only allowed with --dump/--list" ;;
        bootctl)
            [[ "$seg_text" =~ [[:space:]](install|update|remove|set-default|set-oneshot)[[:space:]] ]] && \
                block "Locks §2" "bootctl mutation" ;;
        efibootmgr)
            for a in "${args[@]}"; do
                if [[ "$a" =~ ^-[a-zA-Z]*[cCBoNnaA] || "$a" =~ ^--(create|create-only|delete-bootnum|bootorder|bootnext|active|inactive|delete-bootorder)$ ]]; then
                    block "Locks §2" "efibootmgr NVRAM mutation"
                fi
            done ;;
        efivar)
            [[ "$seg_text" =~ [[:space:]](-w|--write|-A|--append)[[:space:]] ]] && block "Locks §2" "efivar NVRAM write" ;;
        nvme)
            [[ "$seg_text" =~ [[:space:]](format|sanitize|delete-ns|detach-ns)[[:space:]] ]] && block "Locks §2" "destructive nvme operation" ;;
        hdparm)
            [[ "$seg_text" =~ --(security-erase|security-erase-enhanced|trim-sector-ranges|write-sector|make-bad-sector|fwdownload) ]] && \
                block "Locks §2" "destructive hdparm operation" ;;
        mdadm)
            [[ "$seg_text" =~ [[:space:]](--zero-superblock|--create|-C|--grow|--fail|--remove)[[:space:]] ]] && block "Locks §2" "destructive mdadm operation" ;;
        dmsetup)
            [[ "$seg_text" =~ [[:space:]](remove|remove_all|wipe_table|load|reload)[[:space:]] ]] && block "Locks §2" "dmsetup mutation" ;;
        limine)
            [[ "$seg_text" =~ [[:space:]]bios-install[[:space:]]+/dev/ ]] && block "Locks §2" "limine bios-install to a device" ;;
        docker|podman)
            if [[ "$seg_text" =~ [[:space:]](--privileged|--device(=|[[:space:]])|--cap-add|--pid(=|[[:space:]])host|--security-opt) || "$seg_text" =~ [[:space:]](-v|--volume)(=|[[:space:]]+)/(dev[/:]|:) || "$seg_text" =~ source=/dev ]]; then
                block "Locks §1/§2" "privileged container / host device access is user-run"
            fi ;;
        dd)
            for a in "${args[@]}"; do
                if [[ "$a" == of=/dev/* && "$a" != of=/dev/null && "$a" != of=/dev/stdout && "$a" != of=/dev/stderr ]]; then
                    block "Locks §2" "dd targeting a device ($a)"
                fi
                [[ "$a" == of=* ]] && check_mutation_target "${a#of=}"
            done ;;
    esac

    # Locks §4 — fresh Stage 1-6 builds only; host builds and VM tests are user-run.
    if (( ! static_check )); then
        case "$script_word" in
            build-novos.sh)
                [[ "$seg_text" == *" --from-stage"* ]] && block "Locks §4" "partial/resumed builds (--from-stage) are forbidden"
                [[ "$allow_vm_test" -eq 1 ]] || block "Locks §4" "host builds are run by the user (sudo ./scripts/build-novos.sh ...)" ;;
            test-qemu.sh|test-docker-qemu.py)
                [[ "$allow_vm_test" -eq 1 ]] || block "Locks §4" "VM tests are run by the user unless they explicitly ask you to" ;;
        esac
    fi
    if [[ "$seg_text" == *" --from-stage"* && "$seg_text" == *build-novos* ]]; then
        block "Locks §4" "partial/resumed builds (--from-stage) are forbidden"
    fi

    # permissions.deny — user-only release/approval commands, however invoked.
    if [[ "$seg_text" == *nebulous-ctl* || "$seg_text" == *nebulous_ctl* ]] && \
       [[ "$seg_text" =~ [[:space:]](approve-variance|bless-golden|unlock-loop)([[:space:]]|$) ]]; then
        block "permissions.deny" "nebulous-ctl approve-variance/bless-golden/unlock-loop are user-only"
    fi
    if [[ "$seg_text" =~ (^|[[:space:]/])(neb|nebulous_cli|nebulous-cli)([[:space:]]|$) || "$seg_text" == *Nebulous* || "$seg_text" =~ -p[[:space:]]+neb ]] && \
       [[ "$seg_text" =~ [[:space:]]release[[:space:]]+(approve|approve-variance|bless|bless-golden|unlock|unlock-loop|import)([[:space:]]|$) ]]; then
        block "permissions.deny" "neb release approve/bless/unlock/import are user-only"
    fi

    # Locks §5 — file-mutating commands are checked against the scope lock.
    case "$w0" in
        rm|rmdir|unlink|shred|touch|tee|truncate|mv|mkdir)
            while IFS= read -r t; do check_mutation_target "$t"; done < <(non_option_words "${args[@]}")
            ;;
        chmod|chown|chgrp|chattr|setfacl|chcon)
            # Operands after the mode/owner/attribute spec are files. Modes such as
            # `-x` or attributes such as `-i` look like options, so parse explicitly.
            spec_seen=0
            [[ "$seg_text" == *" --reference"* || "$w0" == "chattr" ]] && spec_seen=1
            for a in "${args[@]}"; do
                case "$a" in
                    --*|-R|-v|-c|-f|-h|-H|-L|-P|-m|-x|-b|-k|-d|-n) 
                        # -x/-m etc. are chmod modes or setfacl options; treat chmod -x as the mode
                        if [[ "$w0" == "chmod" && "$a" =~ ^-[rwxXst]+$ && "$spec_seen" -eq 0 ]]; then spec_seen=1; fi
                        continue ;;
                esac
                if [[ "$w0" == "chattr" && "$a" =~ ^[-+=][a-zA-Z]+$ ]]; then continue; fi
                if [[ "$spec_seen" -eq 0 ]]; then
                    spec_seen=1   # mode (chmod), owner (chown/chgrp), acl spec (setfacl), context (chcon)
                    if [[ "$w0" == "chmod" && ! "$a" =~ ^([ugoa]*[-+=][rwxXst]*,?)+$|^[0-7]{3,4}$ ]]; then
                        check_mutation_target "$a"   # not a mode: must be a file
                    fi
                    continue
                fi
                [[ "$a" == -* ]] && { [[ "$w0" == "chmod" && "$a" =~ ^-[rwxXst]+$ ]] || continue; }
                [[ "$a" =~ ^-[rwxXst]+$ ]] && continue
                check_mutation_target "$a"
            done
            ;;
        cp|install|rsync|ln)
            mapfile -t paths < <(non_option_words "${args[@]}")
            target_dir=""
            for ((i = 0; i < ${#args[@]}; i++)); do
                case "${args[$i]}" in
                    -t) target_dir="${args[$((i + 1))]:-}" ;;
                    --target-directory=*) target_dir="${args[$i]#*=}" ;;
                esac
            done
            if [[ -n "$target_dir" ]]; then
                check_mutation_target "$target_dir"
            elif [[ "${#paths[@]}" -gt 0 ]]; then
                check_mutation_target "${paths[-1]}"
            fi
            if [[ "$w0" == "ln" && "${#paths[@]}" -gt 1 ]]; then
                # a link pointing INTO the repo can be used to write through it
                check_mutation_target "${paths[0]}"
            fi
            ;;
        sed|perl|awk|gawk)
            if [[ "$seg_text" =~ [[:space:]](-[a-zA-Z]*i|--in-place|-i[[:space:]]+inplace) ]]; then
                mapfile -t paths < <(non_option_words "${args[@]}")
                [[ "${paths[0]:-}" == "inplace" ]] && paths=("${paths[@]:1}")
                if [[ "$seg_text" =~ [[:space:]](-e|-f|--expression|--file)[[:space:]] ]]; then
                    start=0
                else
                    start=1  # first non-option word is the script
                fi
                for ((i = start; i < ${#paths[@]}; i++)); do check_mutation_target "${paths[$i]}"; done
            fi
            ;;
        tar)
            if [[ "$seg_text" =~ [[:space:]](-?[a-zA-Z]*x[a-zA-Z]*|--extract|--get)[[:space:]] ]]; then
                dest="."
                for ((i = 0; i < ${#args[@]}; i++)); do
                    [[ "${args[$i]}" == "-C" || "${args[$i]}" == "--directory" ]] && dest="${args[$((i + 1))]:-.}"
                    [[ "${args[$i]}" == --directory=* ]] && dest="${args[$i]#*=}"
                done
                check_mutation_target "$dest"
            fi
            ;;
        unzip)
            dest="."
            for ((i = 0; i < ${#args[@]}; i++)); do [[ "${args[$i]}" == "-d" ]] && dest="${args[$((i + 1))]:-.}"; done
            [[ "$seg_text" == *" -l "* ]] || check_mutation_target "$dest"
            ;;
        patch)
            [[ "$seg_text" =~ [[:space:]](--dry-run|-C|--check)[[:space:]] ]] || step_mode_block "patch"
            ;;
        find)
            if [[ "$seg_text" == *" -delete "* ]]; then
                mapfile -t paths < <(non_option_words "${args[@]}")
                check_mutation_target "${paths[0]:-.}"
            fi
            ;;
        git)
            gargs=("${args[@]}")
            gcwd="$cwd"
            while [[ "${#gargs[@]}" -gt 0 && "${gargs[0]}" == -* ]]; do
                case "${gargs[0]}" in
                    -C) gcwd="$(resolve_dir "${gargs[1]:-.}")"; gargs=("${gargs[@]:2}") ;;
                    -c|--git-dir|--work-tree) gargs=("${gargs[@]:2}") ;;
                    *) gargs=("${gargs[@]:1}") ;;
                esac
            done
            [[ "${#gargs[@]}" -gt 0 ]] || continue
            saved_cwd="$cwd"; cwd="$gcwd"
            case "${gargs[0]}" in
                rm|mv|restore)
                    while IFS= read -r t; do check_mutation_target "$t"; done < <(non_option_words "${gargs[@]:1}")
                    ;;
                checkout)
                    after=0
                    for a in "${gargs[@]:1}"; do
                        if [[ "$after" -eq 1 ]]; then
                            check_mutation_target "$a"
                        elif [[ "$a" == "--" ]]; then
                            after=1
                        elif [[ "$a" != -* && -e "${cwd}/${a}" ]]; then
                            check_mutation_target "$a"   # `git checkout <rev> <path>` without --
                        fi
                    done
                    ;;
                clean)
                    if [[ "$seg_text" != *" -n "* && "$seg_text" != *" --dry-run "* ]]; then
                        mapfile -t paths < <(non_option_words "${gargs[@]:1}")
                        [[ "${#paths[@]}" -eq 0 ]] && paths=(".")
                        for t in "${paths[@]}"; do check_mutation_target "$t"; done
                    fi
                    ;;
                reset)
                    [[ "$seg_text" == *" --hard"* || "$seg_text" == *" --merge"* || "$seg_text" == *" --keep"* ]] && step_mode_block "git reset --hard"
                    ;;
                stash|apply|am|cherry-pick|revert|merge|rebase|pull|switch)
                    [[ "$seg_text" =~ [[:space:]](--check|--stat|list|show)[[:space:]] ]] || step_mode_block "git ${gargs[0]}"
                    ;;
            esac
            cwd="$saved_cwd"
            ;;
    esac
done

exit 0
