# NovOS — Claude Code Governance (authoritative)

NovOS is a Debian sid OS/ISO build (`scripts/build-novos.sh`, Stages 1–6). This file and `.claude/` are the whole agent governance. `AGENTS.md` is only a pointer here; the former `.agents/` tree is retired (see the **Parity Table** at the end).

Enforcement layers:
- **Hooks** (`.claude/hooks/`, registered in `.claude/settings.json`): `step-lock.sh` (UserPromptSubmit), `guard-bash.sh` (PreToolUse Bash), `guard-edit.sh` (PreToolUse Edit|Write|NotebookEdit), `guard-diff.sh` (PostToolUse Edit|Write). `test-guards.sh` is their self-test. A hook block (exit 2) is final: report it (hook, path, message) and halt; never work around a guard.
- **Permissions** (`.claude/settings.json` `permissions.deny` / `ask` / `allow`).
- **Skills** (`.claude/skills/`): `/plan-argon`, `/goal`, `/boost` (workflow); `novos-linux-toolchain`, `novos-build-automation`, `novos-debugging` (domain).
- **MCP** (`.mcp.json`): `nebulous_ci`.

## 1. Fresh Stage 1–6 builds only
- Every build and verification test runs fresh, Stages 1 through 6, start to finish. Never build, test, recommend or plan an intermediate resume (`--from-stage 3` or any partial stage run). Resuming masks regressions in earlier stages and cross-stage state. If the system does not build cleanly start to finish every time, the implementation is not done.
- Checkpoint directories (e.g. `build/base-rootfs.tar.gz`, `build/kernel-out/`) may speed inner-loop investigation; they never count as verification.
- Standard fresh build and verification sequence (run by the user):
  ```bash
  sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso
  ./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars
  ```

## 2. Privilege and destructive-operation hard stops
- **Zero host root.** Never invoke `sudo`, `su`, `doas`, `pkexec`, `run0`, `runuser`, or anything needing host root. Output the exact command in its own code block and halt until the user runs and verifies it.
- **No destructive disk/boot/firmware operations**, including but not limited to: `fdisk`, `gdisk`, `sgdisk`, `parted`, `mkfs.*`, `wipefs`, `flashrom`, `dd` targeting `/dev/*`, writes to block devices, raw rewrites of `/etc/fstab`, EFI NVRAM variables (`efibootmgr`, `efivar` writes), bootloader installs (`grub-install`, `bootctl`/systemd-boot, `limine bios-install /dev/...`). Authoring partitioning scripts (non-interactive `sfdisk`/`parted` heredocs) is fine; executing them is the user's.
- **Isolation.** Builds beyond ordinary workspace edits run in ephemeral containers/VMs (Podman, Docker, microVM), never as ad-hoc scripts against the host root filesystem. `docker`/`podman` require a permission prompt; privileged containers and host-device passthrough are user-run.
- **Network egress.** No arbitrary socket creation or egress scanning; build network access only to the declared package mirrors (Debian sid / snapshot.debian.org and the sources already in the build scripts).
- **Propose-and-yield flow** (propose, then yield) for any destructive or irreversible operation:
  1. Detect the intended operation.
  2. Output the planned mutation, its blast radius, the targeted UUIDs/paths, and a dry-run or diff.
  3. Write the exact manual command for the user.
  4. Halt and wait for explicit approval before any dependent task.

## 3. Typed MCP over arbitrary shell
- When a domain MCP server is active, route the action through its typed tool instead of raw shell. Configured: `nebulous_ci` (`python3 scripts/mcp_nebulous_ci.py`, stdio; tools such as `nebulous_run_docker_build`, `nebulous_run_qemu_test`, `nebulous_verify_variance`, `nebulous_get_status`). Its calls require a permission prompt. `kernel_tools` and `qemu_runner` are preferred when installed; they are not configured because their binaries are not installed. Example typed equivalents: `kernel_tools:compile_kernel_module`, `sysconfig:edit_fstab`, `qemu_runner:run_qemu_test`.
- Disallowed when a typed tool covers the action: e.g. `bash -c "make -C /usr/src/linux ..."`, `sed -i` on system tables.
- Structured parameters must satisfy the tool's schema before the call is emitted.
- Shell fallback only when no typed tool covers the action, and only if it obeys §2 (non-root, non-destructive).

## 4. Step scope lock, 4-phase gates, single-step halt
Plan steps live in `STEPPED_IMPLEMENTATION_PLAN.md`. Execute with `/goal STEP <ID>` (bounded, static gates) or `/boost STEP <ID>` (cross-subsystem; adds an independent read-only verification subagent before POST_GATE). Plan with `/plan-argon` (or `/plan`).
- **Scope lock.** During a step, create, edit or delete only the files in its `ALLOWED_FILES` (plus the roadmap status in the plan file). `READ_ONLY_REFS` are read, never written. Never touch `FORBIDDEN_SCOPE` or files of earlier/later steps, even for adjacent improvements. An upstream defect that blocks the step: report `file:line` and halt.
- **4-phase gates:** `PRE_GATE` (run exactly; non-zero → halt and report the missing dependency) → `PARALLEL_READ` (only scoped files, one parallel batch, no repo-wide exploration when paths are given) → `ATOMIC_MUTATION` (deterministic Edit/Write; preserve unrelated logic and comments; create new files with Write, not `mkdir`) → `POST_GATE` (run exactly; e.g. `bash -n`, `python3 -m py_compile`, YAML/RON validation, `verify-deps.sh`, cargo gates).
- Never mark a step COMPLETED until its `POST_GATE` exits 0.
- **Single-step HALT** (`HALT_AFTER_STEP`): at most one step per user turn; after `POST_GATE` passes, report and halt. Never roll into STEP N+1.
- **Plan format** (`/plan-argon`): write every step's full Argon block into `STEPPED_IMPLEMENTATION_PLAN.md` during the planning turn itself, before approval; the repo plan file is the single record (no separate `implementation_plan.md` artifact under Claude). Steps are atomic, single-concern, 1–3 closely coupled files. Each step names exactly one command, `/goal` or `/boost`, never "A or B". Each block has all six sections: `[STEP_ID]`, `[SCOPE_LOCK]`, `[PRE_GATE]`, `[MUTATION_SPEC]`, `[POST_GATE]`, `[HALT_INVARIANT]`.

## 5. Mode separation and user–agent hand-off
- **Diagnose** ("review", "troubleshoot", "locate the cause", "report"): read logs/code, report root cause. Zero file edits, no builds.
- **Plan** (`/plan`, `/plan-argon`, "create a plan"): only `STEPPED_IMPLEMENTATION_PLAN.md` changes; halt for approval.
- **Execute** (`/goal`, `/boost`, "Proceed"/"Run STEP <ID>"): check `git status` and the roadmap first so completed work is never repeated; apply scoped mutations; wait for all subagents; run `POST_GATE`.
- **Hand-off.** The user runs privileged host builds (`sudo ./scripts/build-novos.sh`) and VM boot validation (QEMU / GNOME Boxes) and puts results in `logs/`. Read them there. Never re-run builds or VM tests the user already ran, and never start them unless the user explicitly asks in that prompt.
- Commit only when the user asks.

## 6. Anti-lazy / anti-over-fix barriers
- **No suppression or faked success** in code/config: no `|| true`, `|| :`, `set +e`, `2>/dev/null ||`, skipped tests, `exit 0 # skip`, `continue # skip`, Rust `#[allow(...)]`, unless the same line carries `# novos-allow: <reason>` the user agrees with.
- **No faked packages**: no `equivs-build`, marker/fallback `.deb`s, `--force-depends`, `--force-all`, `trusted=yes` sources.
- **No dropping, downgrading or disabling** packages, tools or verification gates (manifest/tracker entries, `enabled: true` → `false`, lowered versions, removed `check_file`/`check_cmd`/`verify_*` lines) without explicit user approval. Approval is a marker line `## APPROVED <step>` written by the user in the plan file (by convention at its end); agents never write it.
- **Protected files (user-only, always):** `.nebulous/golden-snapshot.toml`, `.nebulous/approvals.toml`, `.nebulous/manifest.toml`, `config/releases/*` (except its README), `.tracker/release`, `.claude/state/`, `.claude/settings.local.json`, user/global Claude Code settings. **Conditionally protected** (only inside a step that lists them): `.claude/settings.json`, `.claude/hooks/*`, `config/suppression-baseline.toml`, `config/app-health.toml`.
- **User-only commands:** `nebulous-ctl` `approve-variance` / `bless-golden` / `unlock-loop`; `neb release approve|bless|unlock|import`.

## 7. Communication discipline
- Direct, factual, non-expressive statements. No filler, exclamations or pleasantries.
- Status and gate reports: minimal, tied to file mutations, command exit codes, or blocking defects. Extended detail only for diagnosing failures or non-obvious constraints.
- Protect the context window: redirect verbose build output to log files (`make -j$(nproc) > compile.log 2>&1`) and surface only relevant windows with `grep`/`tail`; never dump a full build log.

## 8. Rust gates
Before a Rust task is done, all must pass:
```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

## Domain knowledge (skills)
Load the matching skill before work in its area: kernel config (no `menuconfig`; `scripts/config`, kconfiglib, `merge_config.sh`), rootfs, Limine/ESP, dracut, Btrfs, systemd/serial getty → `novos-linux-toolchain`; stage scripts, ISO/squashfs, `guestfish`/`e2tools` disk images, QEMU harnesses → `novos-build-automation`; build/boot failure analysis from `dmesg`, serial traces and `logs/` → `novos-debugging`.

---

## Parity Table

Every rule from the retired `.agents/Locks.md`, `.agents/rules/argon-step-scope-lock.md`, `.agents/skills/Skills.md`, `.agents/permissions.json`, `.agents/mcp_config.json` and the former `AGENTS.md` (including its WorkspaceSettings), with its new home. Originals: `git show c0deace:<path>`. Enforcement: **mechanical** = a hook, permission rule or harness blocks it; **instruction** = this file or a skill states it.

### Locks.md
| Original | New home | Enforcement |
|---|---|---|
| §1 Zero host root privilege; output commands and halt | CLAUDE.md §2; `guard-bash.sh` (sudo/doas/pkexec/run0/runuser, interpreter payloads); `permissions.deny` `Bash(sudo:*)`, `Bash(su:*)`, `Bash(doas:*)` | mechanical |
| §1 Antigravity terminal sandbox (`proceed-in-sandbox`) | Claude Code permission prompts + PreToolUse hooks (`guard-bash.sh`, `guard-edit.sh`); CLAUDE.md §2. Weaker than before: no sandbox is configured; the hooks are static parsers (guard-bash.sh header) | partial (prompts + static hooks; no sandbox) |
| §1 Isolated build runtimes (Podman/Docker/microVM; no ad-hoc scripts on host root) | CLAUDE.md §2; `permissions.ask` `Bash(docker:*)`, `Bash(podman:*)`; `guard-bash.sh` blocks privileged containers / host devices and agent-run host builds | mechanical (prompt) + instruction |
| §1 Network egress boundary | CLAUDE.md §2 | instruction |
| §2 Hard stop: fdisk, gdisk, parted, mkfs.*, wipefs, dd → /dev/*, flashrom, sgdisk, /etc/fstab, EFI NVRAM, grub-install, systemd-boot | CLAUDE.md §2; `guard-bash.sh` (destructive tool list, dd/block-device writes, fstab, efibootmgr/efivar, bootctl, limine bios-install, cryptsetup/nvme/hdparm/mdadm/dmsetup); `guard-edit.sh` (/etc/fstab, /etc/default/grub, /boot, EFI vars, block devices); `permissions.deny` | mechanical |
| §2 Propose-and-yield flow (4 steps) | CLAUDE.md §2; every `guard-bash.sh` block message restates it | instruction (+ hook message) |
| §3 Typed MCP over arbitrary shell | CLAUDE.md §3; `novos-build-automation` skill (prefer `nebulous_ci`) | instruction |
| §3 Parameter schema validation | CLAUDE.md §3 (nothing in this repo enforces it) | instruction |
| §3 Shell fallback only if §1/§2 compliant | CLAUDE.md §3; `guard-bash.sh` enforces the §1/§2 half; "only when no typed tool covers it" is not checked | mechanical (§1/§2) + instruction |
| §4 Fresh Stage 1–6 builds; never `--from-stage` | CLAUDE.md §1; `guard-bash.sh` (blocks `--from-stage`); `novos-build-automation`, `novos-debugging` skills | mechanical |
| §5 Scope lock (`ALLOWED_FILES`) | CLAUDE.md §4; `step-lock.sh` sets `.claude/state/active-step`; `guard-edit.sh` + `guard-bash.sh` (file-mutating shell) block out-of-scope paths | mechanical |
| §5 Dependency gate lock (no mutation if PRE_GATE fails; COMPLETED only after POST_GATE 0) | CLAUDE.md §4; `/goal`, `/boost` skills; `step-lock.sh` injects the step's POST_GATE | instruction |
| §5 Single-step halt | CLAUDE.md §4; `/goal`, `/boost` skills | instruction |

### rules/argon-step-scope-lock.md
| Original | New home | Enforcement |
|---|---|---|
| §1 Explicit file allowlist | CLAUDE.md §4; `guard-edit.sh` | mechanical |
| §1 `FORBIDDEN_SCOPE`; report upstream defect `file:line` and halt | CLAUDE.md §4; `/goal`, `/boost` hard rules; `guard-edit.sh` | mechanical + instruction |
| §1 One step per user turn | CLAUDE.md §4; `/goal`, `/boost` | instruction |
| §2.1 PRE_GATE | CLAUDE.md §4; `/goal`, `/boost` step 2 | instruction |
| §2.2 PARALLEL_READ (scoped files only, no repo-wide sweeps) | CLAUDE.md §4; `/goal`, `/boost` step 3 | instruction |
| §2.3 ATOMIC_MUTATION (preserve unrelated logic/comments) | CLAUDE.md §4; `/goal`, `/boost` step 4 | instruction |
| §2.4 POST_GATE (`bash -n`, `py_compile`, YAML/RON, `verify-deps.sh`) and HALT | CLAUDE.md §4; `/goal`, `/boost`; `permissions.allow` `Bash(bash -n:*)`, `Bash(./scripts/lib/verify-deps.sh:*)` | instruction |
| §3.1 Persist steps to the repo `.md` plan during `/plan`; `implementation_plan.md` artifact | CLAUDE.md §4; `/plan-argon`; `step-lock.sh` PLAN mode (only the plan file editable). Artifact retired: the repo plan file is the single record | mechanical (PLAN mode) + instruction |
| §3.2 Maximum atomic granularity (1–3 files) | CLAUDE.md §4; `/plan-argon` | instruction |
| §3.3 Exactly one slash command (`/goal` vs `/boost`) | CLAUDE.md §4; `/plan-argon` (choosing rules) | instruction |
| §3.4 Six-section Argon block | CLAUDE.md §4; `/plan-argon` (block template) | instruction |
| §4 Zero filler / concise reporting / relevant detail only | CLAUDE.md §7; Reporting sections of `/plan-argon`, `/goal`, `/boost`, `novos-debugging` | instruction |

### skills/Skills.md (§3.1–3.3)
| Original | New home | Enforcement |
|---|---|---|
| §3.1 Kernel config (no menuconfig; kconfiglib, `scripts/config`, `merge_config.sh`; olddefconfig dependency check) | `novos-linux-toolchain` skill | instruction |
| §3.1 Package management & rootfs (bwrap/fakeroot/userns, debootstrap…, FHS, device nodes, pruning) | `novos-linux-toolchain` | instruction |
| §3.1 Bootloader & firmware (sfdisk/parted heredocs, GPT/ESP/BIOS boot, GRUB2/systemd-boot/Limine, UUID roots, dracut `--add-drivers`) | `novos-linux-toolchain` (+ NovOS Limine/dracut/Btrfs specifics) | instruction |
| §3.1 Init systems (systemd/runit/OpenRC ordering; agetty on ttyS0) | `novos-linux-toolchain` | instruction |
| §3.2 Build pipeline orchestration (modular, idempotent, fail-fast stages) | `novos-build-automation` (+ stage map); `guard-diff.sh` (no error swallowing) | mechanical + instruction |
| §3.2 Filesystem & ISO generation (xorriso, genisoimage, live-build, mksquashfs; guestfish/e2tools, no losetup) | `novos-build-automation` | instruction |
| §3.2 QEMU/KVM harnessing (headless QEMU, pexpect/asyncio, serial milestones, timeouts, screendump) | `novos-build-automation` (+ `test-qemu.sh`, `test-docker-qemu.py`) | instruction |
| §3.3 Log & panic parsing (dmesg, serial traces, symbol maps, minimal diff) | `novos-debugging` | instruction |
| §3.3 State management & checkpointing | `novos-debugging`; CLAUDE.md §1 (checkpoints never verify) | instruction |
| §3.3 Context window protection (redirect logs, grep/tail) | `novos-debugging`; CLAUDE.md §7 | instruction |

### permissions.json
| Original | New home | Enforcement |
|---|---|---|
| deny `sudo`, `su` | `permissions.deny` `Bash(sudo:*)`, `Bash(su:*)` (+ `Bash(doas:*)`); `guard-bash.sh` | mechanical |
| deny `fdisk`, `parted`, `mkfs.*`, `wipefs`, `flashrom` | `permissions.deny` `Bash(fdisk:*)`, `Bash(parted:*)`, `Bash(mkfs:*)`, `Bash(mkfs.*)`, `Bash(wipefs:*)`, `Bash(flashrom:*)` (+ gdisk, sgdisk, grub-install); `guard-bash.sh` | mechanical |
| deny `dd if=.* of=/dev/.*` | `permissions.deny` `Bash(dd *of=/dev/*)`; `guard-bash.sh` | mechanical |
| deny `nebulous-ctl.py approve-variance\|bless-golden\|unlock-loop` | `guard-bash.sh` (also `neb release approve/bless/unlock/import`); CLAUDE.md §6 | mechanical |
| deny write golden-snapshot / approvals / manifest | `guard-edit.sh` ALWAYS_PROTECTED (Edit/Write and shell writes); `permissions.deny` `Edit(/.nebulous/...)` ×3 (path-form resolution unverified; guard-edit is the effective barrier); CLAUDE.md §6 | mechanical |
| deny `unsandboxed(*)` | Claude Code permission prompts + PreToolUse hooks; CLAUDE.md §2. No sandbox is configured | partial (prompts + static hooks; no sandbox) |
| ask `docker`, `podman` | `permissions.ask` `Bash(docker:*)`, `Bash(podman:*)` | mechanical |
| ask `write_file(/etc/*)` | `permissions.deny` `Edit(//etc/**)` (stricter: deny); `guard-edit.sh` (/etc/fstab, /etc/default/grub) | mechanical |
| ask `mcp(*)` | `permissions.ask` `mcp__nebulous_ci__*` (the only project server). Narrower: MCP servers configured outside the project fall to default handling | mechanical (project server only) |
| allow `cargo .*` | `permissions.allow` `cargo fmt`/`clippy`/`test`/`build` (other cargo subcommands prompt) | mechanical |
| allow `make .*` | `permissions.allow` `Bash(make:*)` | mechanical |
| allow `python3 .*` | not auto-allowed (omitted from the 8C allow list): `python3` falls to the default permission prompt; `guard-bash.sh` still inspects `python3 -c` payloads | mechanical (prompt) |
| allow `mcp(kernel_tools/*)`, `mcp(qemu_runner/*)` | not configured (servers not installed); CLAUDE.md §3 | instruction |
| allow `mcp(nebulous_ci/*)` | `permissions.ask` `mcp__nebulous_ci__*` (stricter: ask) | mechanical |

### mcp_config.json
| Original | New home | Enforcement |
|---|---|---|
| `nebulous_ci`: `python3 scripts/mcp_nebulous_ci.py`, timeout 1800 s | `.mcp.json` `nebulous_ci` (stdio); no per-server timeout field, Claude Code's MCP tool timeout applies | mechanical (config) |
| `kernel_tools`: `mcp-server-kernel`, timeout 300 s | omitted (binary not installed); CLAUDE.md §3 "preferred when installed" | instruction |
| `qemu_runner`: `mcp-server-qemu`, timeout 600 s | omitted (binary not installed); CLAUDE.md §3 | instruction |

### Former AGENTS.md (sections not covered above) and WorkspaceSettings
| Original | New home | Enforcement |
|---|---|---|
| §2.1–§2.3, §2.4 bullets 1–3, §3.1–§3.2, §3.3 bullets 1–3 (copies of Locks §1–§5, rules §1–§2, Skills §3.1–§3.3) | same homes as the Locks.md, rules and Skills.md rows above | as above |
| §1 Fresh build policy + standard build/test commands | CLAUDE.md §1 | mechanical (`guard-bash.sh`) + instruction |
| §2.4 Planning invariants 1–4 | CLAUDE.md §4; `/plan-argon` | instruction |
| §2.5 Mode separation (diagnose / plan / execute) | CLAUDE.md §5; `step-lock.sh` (PLAN mode, step mode, conversation mode) | mechanical + instruction |
| §2.5 Host build & VM test boundary (user runs; logs in `logs/`) | CLAUDE.md §5; `guard-bash.sh` blocks `build-novos.sh`, `test-qemu.sh`, `test-docker-qemu.py` unless the user's prompt asks (`allow_vm_test`); `novos-debugging`. The `nebulous_ci` build/QEMU tools bypass that hook and hit only the `permissions.ask` prompt | partial (hook + MCP prompt) + instruction |
| §3.3 Final verification fresh Stage 1–6 | CLAUDE.md §1; `novos-debugging` | instruction |
| §3.3 Direct & non-expressive communication | CLAUDE.md §7 | instruction |
| §5 permissions.json copy | see permissions.json table | — |
| WorkspaceSettings `agentConfig.model: gemini-4-argon` | retired: the model is the user's Claude Code choice | — |
| WorkspaceSettings `agentMode: auto` | retired: Claude Code permission mode is the user's choice; hooks apply in every mode | mechanical (hooks) |
| WorkspaceSettings `enableTerminalSandbox: true`, `toolPermission: proceed-in-sandbox` | Claude Code permissions + PreToolUse hooks (`.claude/settings.json`). No sandbox is configured | partial (prompts + static hooks; no sandbox) |
| WorkspaceSettings `allowNonWorkspaceAccess: false` | Claude Code working-directory boundary (edits outside the project prompt); `guard-edit.sh` blocks host boot/config paths and Claude Code settings | mechanical (partial) |
| Old CLAUDE.md notes (AGENTS.md authoritative; `/goal`/`/boost` mapping; protected `.nebulous` files; user runs builds) | this file §4–§6 | mechanical + instruction |
