# NovOS Stepped Implementation Plan (`STEPPED_IMPLEMENTATION_PLAN.md`)

> **Status (2026-11-05):** Steps 1–6G COMPLETED (6A–6G landed in commit `f6c235e`). Phase G (Steps 7A–14A) is **PLANNED — awaiting user approval**. Execute one step per turn; halt after `POST_GATE`.
>
> **Command mapping (Claude Code):** `/goal` = bounded deterministic step with static gates. `/boost` = cross-subsystem step that additionally requires an independent verification subagent before `POST_GATE`. Until Step 8E lands, these are executed manually per `AGENTS.md` §2.4.

## Root Cause Analysis & Architectural Design Summary

## Root Cause Analysis & Architectural Design Summary

### Part A — Completed Stage 2–6 Build Pipeline Fixes (`STEP 1`)
1. **Stage 4.1 APT Conflict (`E: Packages need to be removed but remove is disabled`):** Resolved by moving `bluez-obexd` and `blueman` from Stage 3.7 to Stage 4.1 alongside `pipewire-audio`, `wireplumber`, and `libspa-0.2-bluetooth`, and replacing `--no-remove` with `-o APT::Get::AutomaticRemove=false -o APT::AutoRemove::SuggestsImportant=false`.
2. **Stage 2 / Chroot Rust & Custom-Tools Staging:** Resolved by staging `Cargo.toml`, `Cargo.lock`, `crates/`, `custom-tools/`, and pre-built `target/release/*` into `${MOUNT_ROOT}/root/novos-build/`.
3. **Duplicate Chroot Process Termination:** Resolved by removing the redundant direct `kill_chroot_processes` call in `scripts/build-novos.sh` and adding wait-for-reap polling in `scripts/lib/verify-deps.sh`.

---

### Part B — Completed Calamares 85% Wireshark Debconf Stall, `/dev/null` + `dictionaries-common` Crash & `netinstall.yaml` Batch APT Fixes (`STEP 2`)
1. **85% Installer Stall on `wireshark` (`wireshark-common` Interactive Debconf Prompt):** Resolved by preseeding `debconf/frontend=Noninteractive`, `debconf/priority=critical`, `wireshark-common/install-setuid=true`, and `samba-common` in both `scripts/stages/03-core-system.sh` and `calamares/modules/shellprocess-prepackages.conf`, and adding `--force-confdef` / `--force-confold` to `/etc/apt/apt.conf.d/01-novos-safety`.
2. **`antigravity` & `dictionaries-common` Installation Failure at 86% (`&main::subst` & `/dev/null`):** Resolved by guarding `/dev/null` character device creation (`c 1 3`) and patching `/usr/sbin/aspell-autobuildhash` in Stage 4.1 and `shellprocess-prepackages.conf`.
3. **`/dev/pts` `posix_openpt` Error & `/run/udev` Mount Warning:** Resolved in `calamares/modules/mount.conf` by bind-mounting `/dev/pts` and `/run/udev` and creating `calamares/modules/machineid.conf`.
4. **`netinstall.yaml` Batch APT Fallback:** Resolved by replacing `helix` with `hx`, removing `antigravity-cli`, and generating fallback marker `.deb` packages in `/usr/share/novos/apt-markers` during Stage 5.1c.

---

### Part C — Completed `@@username@@`, Multiarch Module Path, 6 GiB ESP Sizing & QEMU Harness (`STEPS 3A–3D`)
1. **`@@USER@@` -> `@@username@@` (`STEP 3A`):** Replaced `@@USER@@` with `@@username@@` in `calamares/modules/shellprocess-novos.conf` and hardened `novos-setup` user detection.
2. **Debian Sid Multiarch Calamares Module Path (`STEP 3B`):** Updated `calamares/settings.conf` and `scripts/stages/06-snapshot-finalize.sh` to include `/usr/lib/x86_64-linux-gnu/calamares/modules`.
3. **6 GiB (`6144M` / `6144MiB`) EFI System Partition (`STEP 3C`):** Updated `calamares/modules/partition.conf`, `calamares/modules/limine-bootloader/main.py`, and `docs/ARCHITECTURE.md` to allocate 6 GiB for `NOVOS_EFI`.
4. **QEMU Harness (`STEP 3D`):** Updated `scripts/test-qemu.sh` and `scripts/lib/verify-deps.sh` for modular `virtio-vga` display packages and 64 GiB target disk attachment.

---

### Part D — Completed Calamares `shellprocess@prepackages` `DictionaryExpander` Zero-`$` Remediation (`STEPS 4A–4B`)
1. **Zero `$` Tokens in `calamares/modules/shellprocess-*.conf` (`STEP 4A`):** Moved pre-package chroot preparation into `/usr/local/bin/novos-setup --pre-packages` and removed all `$` / `${...}` expressions from `calamares/modules/shellprocess-prepackages.conf`.
2. **Static Regression Gate (`STEP 4B`):** Enforced zero-`$` and valid `@@ROOT@@`/`@@username@@` macro invariants in `scripts/lib/verify-deps.sh` and `scripts/stages/06-snapshot-finalize.sh`.

---

### Part E — Completed Dockerized Build/QEMU Verification Workflow, Nebulous Tiered TOML, Golden OS Snapshot Variance Lock & Loop-Breaker MCP (`STEPS 5A–5F`)
1. **Nebulous Tiered TOML Hierarchy & Manifest (`STEP 5A`):** Created `.nebulous/project.toml`, `.nebulous/manifest.toml`, and `.nebulous/phases/phase-01-ci-foundation.toml`.
2. **Golden OS Snapshot & Loop Breaker (`STEP 5B`):** Implemented `scripts/nebulous-ctl.py` and seeded `.nebulous/golden-snapshot.toml`.
3. **Dockerized Builder & QEMU Tester (`STEPS 5C–5D`):** Created `docker/Dockerfile.builder`, `docker/Dockerfile.qemu-tester`, `docker/docker-compose.ci.yml`, and `scripts/test-docker-qemu.py`.
4. **Stdio MCP Server & Self-Test Gates (`STEPS 5E–5F`):** Implemented `scripts/mcp_nebulous_ci.py` and wired self-test gates into `scripts/lib/verify-deps.sh`.

---

### Part F — Pop!_OS Migration: `/srv/docker-vms` + `/srv/docker-llms` (`llama.cpp` + `Qwen3-14B-Q4_K_M`) Self-Healing Test Environment & Standalone `slippery-tiles` Extraction (`STEPS 6A–6G`)

1. **Workspace Consolidation on Pop!_OS (`/srv/devshare/NovOS`) & Standalone `slippery-tiles` Extraction (`/srv/devshare/slippery-tiles`) (`STEPS 6A–6D`):**
   * **Primary Workspace Sync (`STEP 6A`):** Synchronizes `.agents/` governance and `STEPPED_IMPLEMENTATION_PLAN.md` into `/srv/devshare/NovOS`.
   * **Standalone Project Extraction (`STEP 6A`):** `scripts/export-slippery-tiles.sh` extracts `crates/novos-slippery-tiles` into a standalone Rust repository (`/srv/devshare/slippery-tiles`, with sandbox staging mirror at `./build/slippery-tiles-standalone/`) with its own root `Cargo.toml`, `README.md`, and `.gitignore`.
   * **Complete Removal from NovOS Distro (`STEPS 6B–6D`):**
     * Removes `crates/novos-slippery-tiles` from NovOS `Cargo.toml` and deletes `crates/novos-slippery-tiles/` from the distro repo.
     * Updates `custom-tools/manifest.yaml` and `crates/novos-custom-tools/src/main.rs` so `novos-custom-tools` is the local managed tool entry and zero references to `novos-slippery-tiles` remain.
     * Purges `novos-slippery-tiles` and `novos-gtile-overlay` from `scripts/build-novos.sh`, `scripts/stages/02-bootstrap-chroot.sh`, `scripts/stages/04-cosmic-desktop.sh` (binary install, symlink, `.desktop` file, systemd user unit, and COSMIC custom shortcuts), `scripts/stages/05-profiles-installer.sh`, and `scripts/stages/06-snapshot-finalize.sh`.
     * Removes `novos-slippery-tiles` and `novos-gtile-overlay` from `.nebulous/manifest.toml`, `.nebulous/golden-snapshot.toml`, `.tracker/`, `scripts/nebulous-ctl.py`, and `scripts/lib/verify-deps.sh`.

2. **Pop!_OS Containerized QEMU VM Storage (`/srv/docker-vms`) & Local LLM Self-Healing Host (`/srv/docker-llms`) (`STEPS 6E–6G`):**
   * **Hardware Alignment (Ryzen 7 9800X3D + Radeon RX 9070 XT 16 GB VRAM on Pop!_OS):**
     * **Containerized QEMU (`novos-qemu-tester` + `/srv/docker-vms`):** Mounts `/srv/docker-vms:/vms` (with `/dev/kvm` and `/dev/dri` passthrough) so 64 GiB `.qcow2` target disks (`novos-calamares-target.img`), writable OVMF NVRAM (`ovmf_vars.fd`), QMP sockets, and test artifacts persist cleanly in `/srv/docker-vms` outside the git working tree.
     * **Containerized LLM Host (`novos-llm-host` + `/srv/docker-llms`):** Runs `ghcr.io/ggml-org/llama.cpp:server-vulkan` (with `/dev/dri` and `/dev/kfd` passthrough) mounting `/srv/docker-llms:/models` and serving **`Qwen3-14B-Q4_K_M.gguf`** (~9.0 GiB, fitting 100% inside the RX 9070 XT's 16 GiB VRAM alongside a 16,384-token `q8_0` KV cache and Flash Attention) on `http://127.0.0.1:8081/v1`.
     * **Self-Healing Diagnostic Integration (`test-docker-qemu.py` & `mcp_nebulous_ci.py`):** Adds automated failure log root-cause analysis against the local `llama.cpp` OpenAI-compatible `/v1/chat/completions` endpoint with structured JSON grammar enforcement and `[NEGATIVE_CONSTRAINTS]` awareness, plus a new `nebulous_consult_local_llm` MCP tool.


---

### Part G — Release-Grade Hardening: Hygiene, Claude Governance, Anti-Regression Barriers, Package Truth, Pinning & `neb` Consolidation (`STEPS 7A–14A`)

**Direction (user, 2026-11-05):** Primary target is the user's 9800X3D / RX 9070 XT, but any modern AMD (x86-64-v3) system must work and the distro is intended to be shared. Rolling `sid` is the point, with a stability floor: verified releases are pinned as "last known good" and tracked by Nebulous. No Frankendebian. Python is tolerated only where it clearly wins; Rust is preferred. Agents and automation must be hard-barred from "over fixing" (dropping/downgrading/removing scope) and "lazy fixing" (suppressing errors, faking packages).

**Decisions:** (1) Rust `neb` owns all tracking incl. golden snapshot → retire `nebulous-ctl.py` + `.nebulous/` at parity. (2) Any package drop or version downgrade vs golden fails the build; only the user approves/blesses. (3) "Last known good" = `snapshot.debian.org` timestamp per verified release. (4) Declared package missing → image build fails; at install time continue and surface a failure report. (5) Governance is Claude-only. (6) Broken GUI apps: smoke-test in QEMU, swap to a declared alternative, re-check later. (7) Steam: native `steam-installer` + i386 libs. (8) Common admin/storage packages (mdadm, …) go in the base image.

**Root causes found during review:**
1. **Optional packages silently not installed.** `scripts/stages/05-profiles-installer.sh` Stage 5.1c builds *fallback marker `.deb`s* (`0.0.1~novos1`, served from a `trusted=yes` file repo) for every `netinstall.yaml` package missing upstream, so Calamares' batch `apt-get install` "succeeds" while installing empty placeholders. `safe_apt_install()` additionally drops any package absent from the APT cache with only a `[WARN]`.
2. **Two divergent package lists.** `calamares/modules/netinstall.yaml` (366 lines) and the `install_*_profile()` functions in `novos-setup` declare packages independently; nothing checks they agree.
3. **Regression lock never gates a build.** `nebulous-ctl.py verify-variance --iso` exists but is only invoked as `--repo-only` inside `verify-deps.sh --self-test`; no build compares the produced ISO against `.nebulous/golden-snapshot.toml` (1,093 pinned packages). `.nebulous/approvals.toml` does not exist. The golden's `[iso].path` points at another host.
4. **Errors are masked.** ~180 `|| true` across stages (75 in Stage 5), contrary to the fail-fast pipeline design.
5. **Stale binary shipped.** `target/` (598 files) is committed and `ensure_host_rust_binaries()` skips compiling when `target/release/novos-custom-tools` exists, so the ISO ships the committed binary.
6. **Resume paths still present.** `--from-stage` / `mount_existing_tree()` in `build-novos.sh`, and Stage 2 skips `debootstrap` when a rootfs exists — both violate the fresh Stage 1–6 invariant.
7. **Non-reproducible inputs.** Limine is built from the tip of the `v8.x-binary` branch; Debian is unpinned; third-party repos are unversioned.
8. **Governance is Antigravity-specific and unversioned.** `.agents/` and this plan are gitignored; `.agents/mcp_config.json` references `mcp-server-kernel` / `mcp-server-qemu`, which are not installed. Nothing mechanically enforces the deny list or `ALLOWED_FILES` under Claude Code.
9. **Leftovers.** `scripts/export-slippery-tiles.sh` (hard-coded `/media/infomoose/...`, still called by `verify-deps.sh` self-test), dead wrapper chain `build-live-iso.sh → 07-live-iso.sh → 06-snapshot-finalize.sh`.

**Phase layout:** G1 Hygiene (7A–7E) → G2 Claude governance & hook barriers (8A–8F) → G3 Single package source of truth (9A–9F) → G4 Pipeline anti-regression barriers (10A–10F) → G5 Package content fixes (11A–11D) → G6 Snapshot pinning (12A–12C) → G7 `neb` consolidation (13A–13D) → G8 Docs (14A).

**Verification boundary:** Agents run only static `POST_GATE`s. Steps marked **[USER BUILD]** in the roadmap require the user's fresh `sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso` + `./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars` with logs in `logs/` before the next phase begins.

**Approval markers:** Steps 11A, 11B, 11C, 11D and 13D have a `PRE_GATE` that requires a line `## APPROVED <step>` (e.g. `## APPROVED 11A`) at the end of this file. Only the user adds these; the guard hooks (8A) block agents from writing them in every mode (Edit/Write content check, and shell writes to this file are refused).

**Open questions (resolve before the named step):**
* **Before 11C/11D:** Which exact package/app is the broken "native COSMIC Firefox" (package name or `.desktop` file)? Any other COSMIC-native apps observed broken?
* **Before 11A:** Approve the proposed base admin/storage set listed in Step 11A (add/remove freely).
* **Before 11B:** Confirm preseeding the Steam license debconf answer at build time is acceptable for a shared distro (alternative: user accepts on first launch).
* **Decided 2026-11-06 (9G–9J):** the first build after 9E failed at Stage 5.1d on `zig`, `ghostty` and `tailscale` (none are in sid or the configured repos; zig and ghostty had been faked by `0.0.1~novos1` marker debs until 9D). The user chose: tailscale → official signed apt repo; zig → official ziglang.org tarball, pinned version + sha256, through `novos-custom-tools`; ghostty → removed from the image (alacritty stays). No further approval needed for these three.
* **Before 9I:** zig and ghostty leave the generated Calamares `netinstall.yaml` (the renderer emits apt/novos-meta only). zig still reaches the image through the dev profile (9J). Confirm that is acceptable, or say if zig must stay visible in the installer's package list. **Accepted by the user.** Correction found in 9J: `config/build.env` sets `BUILD_PROFILES="none"`, so the dev profile never runs at build time, and post-Calamares skipped online downloads, so zig reached no installed system. **Decided 2026-11-06:** post-Calamares downloads custom-tool entries when it detects the dev group; a failed download warns and continues (STEP 9K).
* **Before 14A:** `docs/PROFILES.md` still lists `ghostty` and apt `zig`; fold that into the docs refresh.
* **Decided 2026-11-07 (automatic promotion):** the build after bb26b23 was stopped by the variance lock because XanMod moved 7.2.15 → 7.3.3 and its ABI-named packages read as removals. The user set the policy that replaces "every change waits for a user bless":
  - A newer-or-equal version of any component (kernel, package, app) is automatically approved for testing. Only a recorded failure on that exact version holds it back.
  - Phases: fresh build, then VM `live-boot`, then VM `install-e2e`. Passing all three promotes the candidate to the golden snapshot automatically. Drops, downgrades and unlocks stay user-only.
  - A failure with several changed components is isolated by automatic bisection (fresh rebuilds with halves held at golden), so only the culprit (component, version) is held.
  - Home: the `neb release` contract first (13A amended, built in 13B). NovOS-side: 12B (amended), 15A–15D. Until 13C lands, a kernel ABI rename still trips the Python variance lock; unblock it with the user-only `bless-golden`.
* **Before 15B:** install-e2e cannot drive the Calamares UI; it reproduces Calamares's partition/mount/netinstall/shellprocess configuration from `calamares/modules/*`. Confirm that is an acceptable VM gate, with the manual Calamares install kept as an occasional spot check.
* **Before 13B:** `neb` feature work happens in `/srv/devshare/Nebulous` under that repo's own plan; confirm it may be scheduled there.

---

## Step Execution Roadmap

| Step ID | Status | Command | Scope | Target Files |
| :--- | :---: | :---: | :--- | :--- |
| **STEP 1–5F** | **COMPLETED** | — | Stages 1–6 pipeline, Calamares fixes, Nebulous/Docker CI foundation | `scripts/*`, `calamares/*`, `docker/*`, `.nebulous/*` |
| **STEP 6A–6G** | **COMPLETED** (`f6c235e`) | — | slippery-tiles extraction, Pop!_OS Docker QEMU + local LLM diagnostics | see Archive |
| **STEP 7A** | **COMPLETED** | `/goal` | Untrack `target/`; version `.agents/` + plan file | `.gitignore`, `target/` (index only) |
| **STEP 7B** | **COMPLETED** | `/goal` | Remove `--from-stage`, Stage 2 rootfs reuse; always compile Rust fresh | `scripts/build-novos.sh`, `scripts/stages/02-bootstrap-chroot.sh` |
| **STEP 7C** | **COMPLETED** | `/goal` | Delete dead wrappers + slippery-tiles export leftovers | `scripts/build-live-iso.sh` (DEL), `scripts/stages/07-live-iso.sh` (DEL), `scripts/export-slippery-tiles.sh` (DEL), `scripts/lib/verify-deps.sh` |
| **STEP 7D** | **COMPLETED** | `/goal` | Fix README structure + PROFILES slippery note | `README.md`, `docs/PROFILES.md` |
| **STEP 7E** | **COMPLETED** | `/goal` | Drop deleted paths from `.tracker` scopes | `.tracker/project.toml`, `.tracker/phases/01_core_engine/domains/scripts/scope.toml` |
| **STEP 8A** | **COMPLETED** | `/boost` | Claude hook guards (Bash deny, protected files, step scope lock) | `.claude/hooks/guard-bash.sh`, `.claude/hooks/guard-edit.sh`, `.claude/hooks/step-lock.sh` |
| **STEP 8B** | **COMPLETED** | `/goal` | Anti-lazy/over-fix diff guard hook + hook self-test | `.claude/hooks/guard-diff.sh`, `.claude/hooks/test-guards.sh` |
| **STEP 8C** | COMPLETED | `/goal` | Register hooks + permissions; project MCP config | `.claude/settings.json`, `.mcp.json` |
| **STEP 8D** | COMPLETED | `/goal` | Port domain skills (Skills.md §3.1–3.3) | `.claude/skills/novos-linux-toolchain/SKILL.md`, `.claude/skills/novos-build-automation/SKILL.md`, `.claude/skills/novos-debugging/SKILL.md` |
| **STEP 8E** | **COMPLETED** | `/goal` | Workflow skills `/plan-argon`, `/goal`, `/boost` | `.claude/skills/plan-argon/SKILL.md`, `.claude/skills/goal/SKILL.md`, `.claude/skills/boost/SKILL.md` |
| **STEP 8F** | **COMPLETED** | `/boost` | Rewrite `CLAUDE.md`, reduce `AGENTS.md` to pointer, retire `.agents/` with parity table | `CLAUDE.md`, `AGENTS.md`, `.agents/*` (DEL) |
| **STEP 9A** | **COMPLETED** | `/goal` | Author unified package manifest (lossless migration) | `config/packages.yaml` |
| **STEP 9B** | **COMPLETED** | `/boost` | `novos-custom-tools packages` subcommands + tests | `crates/novos-custom-tools/src/main.rs`, `crates/novos-custom-tools/src/packages.rs` |
| **STEP 9C** | **COMPLETED** | `/goal` | Render `netinstall.yaml` from manifest + drift gate | `calamares/modules/netinstall.yaml`, `scripts/lib/verify-deps.sh` |
| **STEP 9D** | **COMPLETED** | `/boost` | Remove fallback marker debs; profiles read manifest | `scripts/stages/05-profiles-installer.sh`, `scripts/lib/verify-deps.sh` |
| **STEP 9E** | **COMPLETED** | `/goal` | Build-time availability gate (fail on unresolvable package) | `scripts/stages/05-profiles-installer.sh` |
| **STEP 9F** | PLANNED | `/goal` | Install-time failure report (continue + surface) **[USER BUILD]** | `scripts/stages/05-profiles-installer.sh`, `calamares/modules/shellprocess-novos.conf` |
| **STEP 9G** | **COMPLETED** | `/goal` | Tailscale from its official signed apt repo; drop `curl \| sh` | `scripts/stages/03-core-system.sh`, `scripts/stages/05-profiles-installer.sh` |
| **STEP 9H** | **COMPLETED** | `/boost` | `tarball` custom-tool type (pinned sha256) + zig entry | `crates/novos-custom-tools/src/main.rs`, `custom-tools/manifest.yaml` |
| **STEP 9I** | **COMPLETED** | `/goal` | `custom-tool` manifest source; zig moved off apt; ghostty removed | `crates/novos-custom-tools/src/packages.rs`, `config/packages.yaml`, `calamares/modules/netinstall.yaml` |
| **STEP 9J** | **COMPLETED** | `/goal` | Dev profile installs `custom-tool` entries (zig) | `scripts/stages/05-profiles-installer.sh` |
| **STEP 9K** | **COMPLETED** | `/goal` | Post-Calamares installs dev `custom-tool` entries (zig) online | `scripts/stages/05-profiles-installer.sh` |
| **STEP 9L** | **COMPLETED** | `/goal` | Atomic binary install (temp + rename) so self-install never hits ETXTBSY | `crates/novos-custom-tools/src/main.rs` |
| **STEP 9M** | **COMPLETED** | `/goal` | Tarball installs owned by root (`tar --no-same-owner`) | `crates/novos-custom-tools/src/main.rs` |
| **STEP 10A** | PLANNED | `/goal` | Wire ISO variance lock into build (fail on drop/downgrade) | `scripts/build-novos.sh` |
| **STEP 10B** | PLANNED | `/goal` | Suppression ratchet + forbidden-pattern gate | `config/suppression-baseline.toml`, `scripts/lib/verify-deps.sh` |
| **STEP 10C** | PLANNED | `/goal` | De-mask Stage 3 | `scripts/stages/03-core-system.sh`, `config/suppression-baseline.toml` |
| **STEP 10D** | PLANNED | `/goal` | De-mask Stage 4 | `scripts/stages/04-cosmic-desktop.sh`, `config/suppression-baseline.toml` |
| **STEP 10E** | PLANNED | `/boost` | De-mask Stage 5 | `scripts/stages/05-profiles-installer.sh`, `config/suppression-baseline.toml` |
| **STEP 10F** | PLANNED | `/goal` | De-mask Stage 2/6 + orchestrator **[USER BUILD]** | `scripts/stages/02-bootstrap-chroot.sh`, `scripts/stages/06-snapshot-finalize.sh`, `scripts/build-novos.sh` (+ baseline) |
| **STEP 11A** | PLANNED | `/goal` | Base workstation admin/storage set | `config/packages.yaml`, `scripts/stages/03-core-system.sh` |
| **STEP 11B** | PLANNED | `/goal` | Native Steam + i386 libs; drop Flathub Steam | `config/packages.yaml`, `scripts/stages/05-profiles-installer.sh` |
| **STEP 11C** | PLANNED | `/boost` | GUI app launch smoke test in QEMU harness | `scripts/test-docker-qemu.py`, `config/packages.yaml` |
| **STEP 11D** | PLANNED | `/goal` | Broken-app swap via user-committed health file **[USER BUILD]** | `crates/novos-custom-tools/src/packages.rs`, `config/app-health.toml` |
| **STEP 12A** | PLANNED | `/goal` | `snapshot.debian.org` archive selection | `config/build.env`, `scripts/stages/02-bootstrap-chroot.sh`, `scripts/stages/03-core-system.sh` |
| **STEP 12B** | PLANNED (blocked on 13B, 15A) | `/boost` | Build against neb holds: `--hold-file`, `--pin-last-good`, `--pin-packages` (amended 2026-11-07) | `scripts/build-novos.sh`, `config/releases/README.md` |
| **STEP 12C** | PLANNED | `/goal` | Pin Limine to a verified tag + commit **[USER BUILD]** | `config/build.env`, `scripts/stages/03-core-system.sh` |
| **STEP 13A** | PLANNED | `/goal` | `neb release` interface contract + candidate lifecycle (amended 2026-11-07) | `docs/NEB_RELEASE_CONTRACT.md` |
| **STEP 13B** | EXTERNAL | — | Implement contract in `/srv/devshare/Nebulous` (own plan) | Nebulous repo |
| **STEP 13C** | PLANNED (blocked on 13B) | `/boost` | Switch callers to `neb release` | `scripts/build-novos.sh`, `scripts/lib/verify-deps.sh`, `.mcp.json` |
| **STEP 13D** | PLANNED (user-approved delete) | `/goal` | Migrate data to `.tracker`, retire Python Nebulous | `scripts/nebulous-ctl.py` (DEL), `scripts/mcp_nebulous_ci.py` (DEL), `.nebulous/*` (DEL) |
| **STEP 14A** | PLANNED | `/goal` | Docs refresh | `docs/BUILD_AND_TEST.md`, `docs/ARCHITECTURE.md`, `docs/PROFILES.md` |
| **STEP 15A** | PLANNED | `/boost` | Export third-party .debs to a host artifact cache each build | `scripts/stages/06-snapshot-finalize.sh`, `scripts/build-novos.sh` |
| **STEP 15B** | PLANNED | `/boost` | install-e2e follows the real Calamares path (10 subvolumes, netinstall groups, post-calamares) | `scripts/test-docker-qemu.py` |
| **STEP 15C** | PLANNED | `/goal` | install-e2e checks the installed system (packages, zig, tailscale, ownership) | `scripts/test-docker-qemu.py` |
| **STEP 15D** | PLANNED (blocked on 13B, 12B) | `/boost` | `release-candidate.sh`: build → VM → auto-promote, bisect on failure **[USER BUILD]** | `scripts/release-candidate.sh` |

---

## Argon Atomic Step Prompts (Phase G)

### STEP 7A: Untrack `target/`; Version `.agents/` and Plan File

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 7A: Untrack target/ and version .agents/ + STEPPED_IMPLEMENTATION_PLAN.md

[SCOPE_LOCK]
ALLOWED_FILES:
  - .gitignore
  - target/ (git index removal only: `git rm -r --cached target`; working tree untouched)
READ_ONLY_REFS:
  - .nebulous/golden-snapshot.toml (confirms target/ is not an anti-regression input)
FORBIDDEN_SCOPE:
  - Any other file. Do not commit.

[PRE_GATE]
git ls-files target | grep -q . && git check-ignore -q .agents/Locks.md && git check-ignore -q STEPPED_IMPLEMENTATION_PLAN.md

[MUTATION_SPEC]
1. .gitignore: add `/target/` under "Disk images and build artifacts". Remove the `.agents/` line and the `STEPPED_IMPLEMENTATION_PLAN.md` line (keep `verbot-prompt.md`). Add `.claude/settings.local.json` and `.claude/state/` to the agent section.
2. Run `git rm -r --cached --quiet target`. Leave files on disk.
3. Stage `.gitignore`, `.agents/`, `STEPPED_IMPLEMENTATION_PLAN.md` with `git add`. Do not commit.

[POST_GATE]
test -z "$(git ls-files target)" && git check-ignore -q target/release/novos-custom-tools && ! git check-ignore -q .agents/Locks.md && ! git check-ignore -q STEPPED_IMPLEMENTATION_PLAN.md && git check-ignore -q .claude/state/x

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 7B: Remove Resume Paths; Always Compile Rust Fresh

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 7B: Remove --from-stage / rootfs reuse and force a fresh Rust build in build-novos.sh

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/build-novos.sh
  - scripts/stages/02-bootstrap-chroot.sh
READ_ONLY_REFS:
  - scripts/lib/verify-deps.sh
  - AGENTS.md (§1 fresh-build invariant)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. scripts/mcp_nebulous_ci.py keeps its --from-stage rejection guard.

[PRE_GATE]
grep -q 'FROM_STAGE' scripts/build-novos.sh && grep -q 'skipping debootstrap' scripts/stages/02-bootstrap-chroot.sh

[MUTATION_SPEC]
1. scripts/build-novos.sh:
   a. Delete FROM_STAGE, the `--from-stage` usage line and case arm, and `mount_existing_tree()`. Passing `--from-stage` must hit the unknown-argument error.
   b. Run Stages 1–6 unconditionally in order (remove every `if [[ "$FROM_STAGE" -le N ]]` wrapper).
   c. ensure_host_rust_binaries(): remove the early `return 0` when the binary exists. Always run `cargo build --release --locked -p novos-custom-tools` as SUDO_USER when set (`runuser -u "$SUDO_USER" --`), else as current user. Remove the `2>/dev/null ||` offline fallback and the trailing `|| true`; a failed compile aborts the build with `[FATAL] Rust workspace build failed`.
   d. After compiling, assert `target/release/novos-custom-tools` mtime is newer than the build start timestamp; else `[FATAL]`.
2. scripts/stages/02-bootstrap-chroot.sh: replace the "Existing debootstrap rootfs detected ... skipping" branch with `[FATAL] Staging dir ${MOUNT_ROOT} is not empty; fresh Stage 1-6 builds only` and exit 1.

[POST_GATE]
bash -n scripts/build-novos.sh && bash -n scripts/stages/02-bootstrap-chroot.sh && ! grep -nE 'from-stage|FROM_STAGE|mount_existing_tree' scripts/build-novos.sh && ! grep -n 'skipping debootstrap' scripts/stages/02-bootstrap-chroot.sh && grep -q -- '--locked' scripts/build-novos.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 7C: Delete Dead Wrappers and slippery-tiles Export Leftovers

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 7C: Delete build-live-iso.sh, 07-live-iso.sh, export-slippery-tiles.sh and their self-test hook

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/build-live-iso.sh (DELETE)
  - scripts/stages/07-live-iso.sh (DELETE)
  - scripts/export-slippery-tiles.sh (DELETE)
  - scripts/lib/verify-deps.sh
  - build/slippery-tiles-standalone/ (DELETE; untracked staging dir)
READ_ONLY_REFS:
  - /srv/devshare/slippery-tiles (must exist; proves extraction is preserved)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. (4 files justified: pure deletions + one 2-line call-site removal.)

[PRE_GATE]
test -f /srv/devshare/slippery-tiles/Cargo.toml && test -f scripts/export-slippery-tiles.sh && grep -q 'export-slippery-tiles.sh' scripts/lib/verify-deps.sh

[MUTATION_SPEC]
1. `git rm` scripts/build-live-iso.sh, scripts/stages/07-live-iso.sh, scripts/export-slippery-tiles.sh.
2. scripts/lib/verify-deps.sh run_self_test(): remove the two lines invoking/echoing `export-slippery-tiles.sh --self-test`. No other changes.
3. rm -rf build/slippery-tiles-standalone.

[POST_GATE]
bash -n scripts/lib/verify-deps.sh && test ! -e scripts/build-live-iso.sh && test ! -e scripts/stages/07-live-iso.sh && test ! -e scripts/export-slippery-tiles.sh && ! grep -rnE 'export-slippery-tiles|build-live-iso|07-live-iso' scripts/

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 7D: Fix README Repository Structure and PROFILES slippery-tiles Note

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 7D: Align README.md structure tree and docs/PROFILES.md §5.2 with the cleaned repo

[SCOPE_LOCK]
ALLOWED_FILES:
  - README.md
  - docs/PROFILES.md
READ_ONLY_REFS:
  - Output of `git ls-files | cut -d/ -f1-2 | sort -u`
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
grep -q 'build-live-iso.sh' README.md && grep -q 'export-slippery-tiles.sh' docs/PROFILES.md

[MUTATION_SPEC]
1. README.md "Repository Structure": remove build-live-iso.sh; add entries for crates/novos-custom-tools, custom-tools/manifest.yaml, docker/, .tracker/, .nebulous/, .claude/ (one-line purpose each), and scripts nebulous-ctl.py, mcp_nebulous_ci.py, test-docker-qemu.py, setup-popos-test-env.sh. Change "stages/ (01 through 06)" wording to match files present.
2. docs/PROFILES.md §5.2: keep the note that slippery-tiles lives at /srv/devshare/slippery-tiles and can be re-integrated via custom-tools/manifest.yaml; remove the reference to scripts/export-slippery-tiles.sh.

[POST_GATE]
! grep -nE 'build-live-iso|export-slippery-tiles' README.md docs/PROFILES.md && grep -q 'crates/' README.md && grep -q '.tracker/' README.md

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 7E: Drop Deleted Paths from `.tracker` Scopes

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 7E: Remove build-live-iso.sh and 07-live-iso.sh from .tracker scope lists

[SCOPE_LOCK]
ALLOWED_FILES:
  - .tracker/project.toml
  - .tracker/phases/01_core_engine/domains/scripts/scope.toml
READ_ONLY_REFS:
  - /srv/devshare/Nebulous/README.md (TOML schema expectations)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Do not touch .nebulous/.

[PRE_GATE]
grep -q 'build-live-iso.sh' .tracker/project.toml && grep -q '07-live-iso.sh' .tracker/phases/01_core_engine/domains/scripts/scope.toml

[MUTATION_SPEC]
1. Remove the "scripts/build-live-iso.sh" and "scripts/stages/07-live-iso.sh" array entries from both files; fix trailing commas.

[POST_GATE]
! grep -rnE 'build-live-iso|07-live-iso' .tracker && python3 -c 'import tomllib,sys;[tomllib.load(open(p,"rb")) for p in sys.argv[1:]]' .tracker/project.toml .tracker/phases/01_core_engine/domains/scripts/scope.toml

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 8A: Claude Hook Guards — Bash Deny, Protected Files, Step Scope Lock

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 8A: Implement PreToolUse/UserPromptSubmit guard hooks (bash + jq) replicating Locks.md §1, §2, §4, §5

[SCOPE_LOCK]
ALLOWED_FILES:
  - .claude/hooks/guard-bash.sh
  - .claude/hooks/guard-edit.sh
  - .claude/hooks/step-lock.sh
READ_ONLY_REFS:
  - .agents/Locks.md
  - .agents/permissions.json
  - .agents/rules/argon-step-scope-lock.md
  - STEPPED_IMPLEMENTATION_PLAN.md (ALLOWED_FILES block format)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Do not register hooks yet (Step 8C).

[PRE_GATE]
command -v jq >/dev/null && test -f .agents/permissions.json && test ! -e .claude/settings.json

[MUTATION_SPEC]
All scripts: `#!/usr/bin/env bash`, `set -euo pipefail`, read hook JSON from stdin with jq, exit 0 = allow, exit 2 + stderr reason = block (fed back to the model). chmod +x.
1. guard-bash.sh (PreToolUse, matcher Bash): tokenize `.tool_input.command` on `;`, `&&`, `||`, `|`, `$(`, backticks and `bash -c`/`sh -c` payloads; block if any segment's command word (after env/VAR= prefixes) is: sudo, su, doas, pkexec, fdisk, gdisk, sgdisk, sfdisk (non --dump), parted, wipefs, flashrom, grub-install, bootctl install, efibootmgr (with -c/-B/-o), mkfs*, or `dd` with `of=/dev/`; or any write redirection to `/etc/fstab` or `/dev/sd*|nvme*|vd*`; or the token `--from-stage`; or `sudo ./scripts/build-novos.sh` / `./scripts/test-qemu.sh` unless the user prompt explicitly authorized it (step-lock state flag `allow_vm_test`). Block message names the rule (Locks §1/§2/§4) and says: output the exact command for the user instead (propose-and-yield).
2. guard-edit.sh (PreToolUse, matcher Edit|Write|NotebookEdit; also Bash `rm`/`mv`/`git rm` targets via guard-bash calling it): resolve `.tool_input.file_path` relative to repo root.
   a. Always block: .nebulous/golden-snapshot.toml, .nebulous/approvals.toml, .nebulous/manifest.toml, config/app-health.toml, config/releases/*.toml, config/suppression-baseline.toml (except when the active step lists it), .claude/state/**, .claude/hooks/** and .claude/settings.json (except when the active step lists them).
   b. If `.claude/state/active-step` exists: allow only paths listed in that step's ALLOWED_FILES (parsed from STEPPED_IMPLEMENTATION_PLAN.md between the step's `[SCOPE_LOCK]` and `READ_ONLY_REFS:`; trailing annotations in parentheses ignored; `dir/` entries match prefixes; `.agents/*` style globs supported). Block otherwise with "Out of step scope (Locks §5): <path>. Report the defect and halt."
   c. If no active step: allow (conversation mode), except rule (a).
3. step-lock.sh (UserPromptSubmit): if the user prompt matches `/(goal|boost)\b.*STEP ([0-9]+[A-Z])` or `^(Proceed|Execute) STEP ([0-9]+[A-Z])`, write the ID to .claude/state/active-step and `allow_vm_test=0`; if the prompt contains `run the VM test` / `run the build` set allow_vm_test=1; if the prompt is `/plan-argon` or starts with `/plan`, clear active-step (plan mode: only STEPPED_IMPLEMENTATION_PLAN.md editable — write the literal `PLAN` and have guard-edit allow only that file). Print a one-line additionalContext JSON naming the active step and its POST_GATE.

[POST_GATE]
for f in .claude/hooks/guard-bash.sh .claude/hooks/guard-edit.sh .claude/hooks/step-lock.sh; do bash -n "$f" && test -x "$f" || exit 1; done && echo '{"tool_input":{"command":"ls && sudo rm -rf /"}}' | .claude/hooks/guard-bash.sh; test $? -eq 2

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 8B: Anti-Lazy / Anti-Over-Fix Diff Guard and Hook Self-Test

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 8B: PostToolUse diff guard rejecting suppression/faking/scope-drop edits + guard test suite

[SCOPE_LOCK]
ALLOWED_FILES:
  - .claude/hooks/guard-diff.sh
  - .claude/hooks/test-guards.sh
READ_ONLY_REFS:
  - .claude/hooks/guard-bash.sh
  - .claude/hooks/guard-edit.sh
  - .claude/hooks/step-lock.sh
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test -x .claude/hooks/guard-bash.sh && test -x .claude/hooks/guard-edit.sh && test -x .claude/hooks/step-lock.sh

[MUTATION_SPEC]
1. guard-diff.sh (PostToolUse, matcher Edit|Write): run `git diff -U0 -- <file>` (or full content for new files). Exit 2 with a specific reason if ADDED lines contain, without an inline `# novos-allow: <reason>` annotation:
   - lazy fixing: `|| true`, `|| :`, `set +e`, `2>/dev/null ||`, `--force-depends`, `--force-all`, `trusted=yes`, `equivs-build`, `build_marker_deb`, `#[allow(` , `@pytest.mark.skip`, `exit 0  # skip`, `continue  # skip`;
   - over fixing: in config/packages.yaml, calamares/modules/netinstall.yaml, custom-tools/manifest.yaml or .tracker/**: any REMOVED package/tool name, or `enabled: true` → `enabled: false`, or a version string lowered; in scripts: removed `check_file`/`check_cmd`/`verify_*` gate lines.
   Message: "Lazy-fix/over-fix barrier: <rule>. Fix the root cause or ask the user for explicit approval." Reverts nothing itself.
2. test-guards.sh: table-driven cases feeding synthetic JSON to each hook and asserting exit codes: ≥12 bash cases (sudo, nested `bash -c "mkfs.ext4 x"`, `dd if=a of=/dev/sda`, `dd of=./file` allowed, `--from-stage`, `cargo test` allowed), ≥6 edit cases (golden blocked, in-scope allowed, out-of-scope blocked with a fake active step and a temp plan fixture), ≥6 diff cases using a temp git repo. Uses mktemp -d; never touches real .claude/state. Exit non-zero on any failure.

[POST_GATE]
bash -n .claude/hooks/guard-diff.sh && bash -n .claude/hooks/test-guards.sh && .claude/hooks/test-guards.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 8C: Register Hooks and Permissions; Project MCP Config

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 8C: Create .claude/settings.json (permissions + hooks) and .mcp.json replicating permissions.json and mcp_config.json

[SCOPE_LOCK]
ALLOWED_FILES:
  - .claude/settings.json
  - .mcp.json
READ_ONLY_REFS:
  - .agents/permissions.json
  - .agents/mcp_config.json
  - .claude/hooks/*.sh
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Never write .claude/settings.local.json.

[PRE_GATE]
.claude/hooks/test-guards.sh && test ! -e .claude/settings.json

[MUTATION_SPEC]
1. .claude/settings.json:
   - permissions.deny: Bash(sudo:*), Bash(su:*), Bash(doas:*), Bash(fdisk:*), Bash(gdisk:*), Bash(sgdisk:*), Bash(parted:*), Bash(wipefs:*), Bash(flashrom:*), Bash(grub-install:*), Bash(mkfs:*), Bash(mkfs.*:*), Bash(dd:*of=/dev/*), Edit(.nebulous/golden-snapshot.toml), Edit(.nebulous/approvals.toml), Edit(.nebulous/manifest.toml), Write(/etc/**).
   - permissions.ask: Bash(docker:*), Bash(podman:*), Bash(git push:*), Bash(git commit:*), mcp__nebulous_ci__*.
   - permissions.allow: Bash(cargo fmt:*), Bash(cargo clippy:*), Bash(cargo test:*), Bash(cargo build:*), Bash(bash -n:*), Bash(make:*), Bash(git status:*), Bash(git diff:*), Bash(.claude/hooks/test-guards.sh), Bash(./scripts/lib/verify-deps.sh:*).
   - hooks: UserPromptSubmit → step-lock.sh; PreToolUse Bash → guard-bash.sh; PreToolUse Edit|Write|NotebookEdit → guard-edit.sh; PostToolUse Edit|Write → guard-diff.sh. Commands use "$CLAUDE_PROJECT_DIR/.claude/hooks/<name>.sh".
   (Antigravity sandbox/egress rules map to Claude Code permission prompts + hooks; record this in CLAUDE.md at 8F.)
2. .mcp.json: `nebulous_ci` → `python3 scripts/mcp_nebulous_ci.py` (stdio). Omit kernel_tools/qemu_runner (binaries not installed); list them in CLAUDE.md as "typed MCP preferred when installed".

[POST_GATE]
jq -e '.hooks.PreToolUse and .hooks.PostToolUse and .hooks.UserPromptSubmit and (.permissions.deny|length>10)' .claude/settings.json >/dev/null && jq -e '.mcpServers.nebulous_ci' .mcp.json >/dev/null && .claude/hooks/test-guards.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Tell the user to restart the Claude session so hooks load.
```

---

### STEP 8D: Port Domain Skills (Skills.md §3.1–3.3)

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 8D: Convert .agents/skills/Skills.md into three Claude skills with no loss of content

[SCOPE_LOCK]
ALLOWED_FILES:
  - .claude/skills/novos-linux-toolchain/SKILL.md
  - .claude/skills/novos-build-automation/SKILL.md
  - .claude/skills/novos-debugging/SKILL.md
READ_ONLY_REFS:
  - .agents/skills/Skills.md
  - AGENTS.md §3
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test -f .agents/skills/Skills.md

[MUTATION_SPEC]
Each SKILL.md: YAML frontmatter `name`, `description` (trigger-oriented: when to load), body.
1. novos-linux-toolchain ← §3.1 (kconfig via scripts/config / merge_config.sh, never menuconfig; rootfs via debootstrap/bwrap/fakeroot; bootloader: Limine/sfdisk heredocs, dracut --add-drivers; init/systemd units, agetty ttyS0). Add NovOS specifics: dracut hostonly=no, Limine v8 layout under /boot/efi, 10-subvol Btrfs.
2. novos-build-automation ← §3.2 (idempotent fail-fast stages, xorriso/mksquashfs, guestfish/e2tools instead of losetup, headless QEMU harness with milestone regexes + screendump). Add: NovOS stage map 01–06, test-qemu.sh / test-docker-qemu.py usage, no `|| true` without `# novos-allow:`.
3. novos-debugging ← §3.3 (panic/log parsing, checkpoint dirs but final verification fresh 1–6, context protection: redirect logs, grep/tail windows; direct non-expressive reporting). Add: read user logs from logs/, never re-run host builds.

[POST_GATE]
for s in novos-linux-toolchain novos-build-automation novos-debugging; do head -1 ".claude/skills/$s/SKILL.md" | grep -q '^---$' && grep -q "^name: $s" ".claude/skills/$s/SKILL.md" || exit 1; done && grep -q 'menuconfig' .claude/skills/novos-linux-toolchain/SKILL.md && grep -q 'guestfish' .claude/skills/novos-build-automation/SKILL.md && grep -q 'dmesg' .claude/skills/novos-debugging/SKILL.md

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 8E: Workflow Skills `/plan-argon`, `/goal`, `/boost`

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 8E: Encode the Argon plan format and the 4-phase step execution protocol as Claude skills

[SCOPE_LOCK]
ALLOWED_FILES:
  - .claude/skills/plan-argon/SKILL.md
  - .claude/skills/goal/SKILL.md
  - .claude/skills/boost/SKILL.md
READ_ONLY_REFS:
  - .agents/rules/argon-step-scope-lock.md
  - AGENTS.md §2.4, §2.5
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test -f .agents/rules/argon-step-scope-lock.md && test -x .claude/hooks/step-lock.sh

[MUTATION_SPEC]
1. plan-argon: invoked by user `/plan-argon` or "/plan". Writes atomic steps directly to STEPPED_IMPLEMENTATION_PLAN.md in the same turn (no separate implementation_plan.md artifact under Claude); 1–3 files per step; full six-section block per step; exactly one of `/goal` or `/boost` per step (rule for choosing); roadmap table; open questions; halt for approval; no code edits (enforced by step-lock PLAN mode).
2. goal: argument `STEP <ID>`. Protocol: verify repo state (git status, step already done?) → PRE_GATE (halt on fail) → parallel read of ALLOWED_FILES + READ_ONLY_REFS only → mutate → POST_GATE → mark step COMPLETED in roadmap → report gate output → halt. On upstream defect outside scope: report file:line and halt. Never chain steps.
3. boost: same as goal, plus before POST_GATE spawn one independent verification subagent (read-only) that reviews the diff against MUTATION_SPEC and the anti-lazy/over-fix rules; findings must be resolved within scope or reported; wait for all subagents before reporting.
All three: `disable-model-invocation: true` (user-invoked only), concise factual reporting.

[POST_GATE]
for s in plan-argon goal boost; do grep -q "^name: $s" ".claude/skills/$s/SKILL.md" && grep -q 'disable-model-invocation: true' ".claude/skills/$s/SKILL.md" || exit 1; done && grep -q 'HALT_INVARIANT' .claude/skills/plan-argon/SKILL.md && grep -q 'subagent' .claude/skills/boost/SKILL.md

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 8F: Rewrite `CLAUDE.md`, Reduce `AGENTS.md`, Retire `.agents/`

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 8F: Consolidate governance into CLAUDE.md with a parity table; AGENTS.md becomes a pointer; delete .agents/

[SCOPE_LOCK]
ALLOWED_FILES:
  - CLAUDE.md
  - AGENTS.md
  - .agents/Locks.md (DELETE)
  - .agents/permissions.json (DELETE)
  - .agents/mcp_config.json (DELETE)
  - .agents/rules/argon-step-scope-lock.md (DELETE)
  - .agents/skills/Skills.md (DELETE)
READ_ONLY_REFS:
  - .claude/settings.json, .claude/hooks/*, .claude/skills/*/SKILL.md, .mcp.json
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. /srv/devshare/CLAUDE.md is out of scope.

[PRE_GATE]
test -f .claude/settings.json && test -f .claude/skills/goal/SKILL.md && test -f .claude/skills/novos-debugging/SKILL.md && .claude/hooks/test-guards.sh

[MUTATION_SPEC]
1. CLAUDE.md (authoritative, concise): §1 fresh Stage 1–6 build invariant + standard build/test commands; §2 privilege & destructive-op hard stops with propose-and-yield flow; §3 typed MCP preference + shell fallback rules; §4 step scope lock / 4-phase gates / single-step halt; §5 mode separation (diagnose/plan/execute) + user–agent hand-off (user runs sudo builds + VM tests, logs in logs/); §6 anti-lazy/over-fix barriers (no suppression without `# novos-allow:`, no faked packages, no dropping/downgrading/disabling without user approval, protected files list); §7 communication discipline; §8 Rust gates.
   Append a **Parity Table**: every original rule (Locks §1–5, rules §1–4, Skills §3.1–3.3, permissions deny/ask/allow, mcp_config servers, WorkspaceSettings) → its new home (CLAUDE.md section / hook / skill / settings.json key) → enforcement type (mechanical | instruction).
2. AGENTS.md: ≤15 lines: project one-liner, "Claude Code governance lives in CLAUDE.md and .claude/", list of hard stops, link to parity table. Remove Gemini/Antigravity WorkspaceSettings.
3. git rm the five .agents files.

[POST_GATE]
test ! -d .agents && grep -q 'Parity Table' CLAUDE.md && for k in 'sudo' 'from-stage' 'ALLOWED_FILES' 'PRE_GATE' 'POST_GATE' 'HALT' 'propose' 'golden-snapshot' 'novos-allow' 'menuconfig' 'guestfish' 'dmesg' 'permissions.deny' 'nebulous_ci'; do grep -q -- "$k" CLAUDE.md || { echo "missing $k"; exit 1; }; done && test "$(wc -l < AGENTS.md)" -le 15

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 9A: Author Unified Package Manifest (Lossless Migration)

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9A: Create config/packages.yaml as the single source of truth for base + profile packages

[SCOPE_LOCK]
ALLOWED_FILES:
  - config/packages.yaml
READ_ONLY_REFS:
  - calamares/modules/netinstall.yaml
  - scripts/stages/03-core-system.sh (base apt-get install lists)
  - scripts/stages/04-cosmic-desktop.sh (desktop apt-get install lists)
  - scripts/stages/05-profiles-installer.sh (install_*_profile, configure_*_flathub)
  - crates/novos-custom-tools/src/main.rs (supported YAML subset)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test -f calamares/modules/netinstall.yaml && test ! -e config/packages.yaml

[MUTATION_SPEC]
1. Schema (YAML subset parseable by novos-custom-tools' existing parser): `schema_version: 1`; `groups:` list, each with `id`, `name`, `description`, `layer` (base|desktop|profile), `profile` (wifi|bluetooth|gaming|dev|devsecops|llm|network|comms|browsers|media|utilities|custom_tools; profile layer only), `selected` (bool, netinstall default), `critical` (bool), optional `subgroups` (same shape) and `packages:` list of entries `{name, source: apt|flatpak, arch: amd64|i386|all (default amd64), required: true, alternative: <name|null>, smoke: null}`.
2. Migrate verbatim: every package in netinstall.yaml (preserving group/subgroup names, descriptions, selected/critical flags), every package passed to safe_apt_install in install_*_profile, every Flathub ref in configure_*_flathub (source: flatpak), and Stage 3/4 base/desktop install lists (layer base/desktop). Union on conflict; never drop a name. Record real meta markers novos-profile-wifi / novos-profile-bluetooth as `source: novos-meta`.
3. Header comment: "Single source of truth. Removing or disabling an entry requires explicit user approval (guard-diff)."

[POST_GATE]
python3 - <<'PY'
import re,yaml,subprocess,sys
new=yaml.safe_load(open("config/packages.yaml"))
def walk(g):
    for x in g.get("packages",[]) or []: yield (x["name"]+(":i386" if x.get("arch")=="i386" else "")) if isinstance(x,dict) else x
    for s in g.get("subgroups",[]) or []: yield from walk(s)
names={n for g in new["groups"] for n in walk(g)}
old=set()
for g in yaml.safe_load(open("calamares/modules/netinstall.yaml")): old|=set(walk(g))
src=open("scripts/stages/05-profiles-installer.sh").read()
for blk in re.findall(r"safe_apt_install \\\n((?:\s+[^\n]*\\\n)*\s+[^\n]*)",src):
    old|={t for t in blk.replace("\\"," ").split() if re.match(r"^[a-z0-9][a-z0-9+.:-]+$",t)}
missing=sorted(old-names)
print("missing:",missing); sys.exit(1 if missing else 0)
PY

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 9B: `novos-custom-tools packages` Subcommands

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 9B: Add packages module to novos-custom-tools (validate, list, render-netinstall, check-apt)

[SCOPE_LOCK]
ALLOWED_FILES:
  - crates/novos-custom-tools/src/main.rs
  - crates/novos-custom-tools/src/packages.rs
READ_ONLY_REFS:
  - config/packages.yaml
  - calamares/modules/netinstall.yaml (Calamares netinstall output format)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. No new crate dependencies (offline build); reuse the existing YAML parser.

[PRE_GATE]
test -f config/packages.yaml && cargo test -p novos-custom-tools --quiet

[MUTATION_SPEC]
1. packages.rs: typed model (Group, Subgroup, PackageEntry, Source, Arch, Layer) + loader from the existing YamlScalar tree; validation (unique group ids, known profiles, i386 only for apt, alternative must itself be declared, no empty groups).
2. CLI in main.rs: `packages validate <file>`; `packages list <file> [--layer L] [--profile P] [--source apt|flatpak] [--with-arch]` (one name per line, `name:i386` when arch=i386, alternative substituted when the name is listed broken in an optional `--health config/app-health.toml`); `packages render-netinstall <file>` (deterministic Calamares netinstall.yaml to stdout, apt-only, includes novos-meta markers); `packages check-apt <file> [--layer L|--all]` (runs `apt-cache policy` per apt package, prints every unresolvable name, exit 3 if any).
3. Unit tests: parse fixture, validation failures, list filters, render determinism (golden string), check-apt parser using captured `apt-cache policy` text. Keep existing 4 tests passing.

[POST_GATE]
cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && cargo run -q -p novos-custom-tools -- packages validate config/packages.yaml

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 9C: Render `netinstall.yaml` from Manifest + Drift Gate

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9C: Generate calamares/modules/netinstall.yaml from config/packages.yaml and fail verify-deps on drift

[SCOPE_LOCK]
ALLOWED_FILES:
  - calamares/modules/netinstall.yaml
  - scripts/lib/verify-deps.sh
READ_ONLY_REFS:
  - config/packages.yaml
  - crates/novos-custom-tools/src/packages.rs
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
cargo run -q -p novos-custom-tools -- packages validate config/packages.yaml

[MUTATION_SPEC]
1. Replace netinstall.yaml with `novos-custom-tools packages render-netinstall config/packages.yaml` output, prefixed by a "GENERATED — edit config/packages.yaml" comment.
2. verify-deps.sh run_self_test(): add `check_netinstall_drift` that diffs the committed file against fresh render output; non-empty diff → `[FAIL]` with diff and non-zero exit.

[POST_GATE]
bash -n scripts/lib/verify-deps.sh && diff <(cargo run -q -p novos-custom-tools -- packages render-netinstall config/packages.yaml) <(grep -v '^# GENERATED' calamares/modules/netinstall.yaml) && python3 -c 'import yaml;yaml.safe_load(open("calamares/modules/netinstall.yaml"))'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 9D: Remove Fallback Marker Debs; Profiles Read the Manifest

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 9D: Delete Stage 5.1c fallback marker generation; novos-setup profiles install from config/packages.yaml

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/05-profiles-installer.sh
  - scripts/lib/verify-deps.sh
READ_ONLY_REFS:
  - config/packages.yaml
  - crates/novos-custom-tools/src/packages.rs
  - calamares/modules/netinstall.yaml
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
grep -q 'NETINSTALL_CANDIDATE_PKGS' scripts/stages/05-profiles-installer.sh && cargo run -q -p novos-custom-tools -- packages list config/packages.yaml --profile gaming | grep -q .

[MUTATION_SPEC]
1. Stage 5.1c: keep building only the two genuine meta packages novos-profile-wifi / novos-profile-bluetooth (real dependency metapackages, versioned 1.0). Delete NETINSTALL_CANDIDATE_PKGS, the embedded python yaml reader, the fallback `0.0.1~novos1` loop. Keep the file repo for the two metas only.
2. Stage 5.1b: also install config/packages.yaml to /etc/novos/packages.yaml.
3. novos-setup: each install_<p>_profile replaces its hard-coded list with `mapfile -t pkgs < <(novos-custom-tools packages list /etc/novos/packages.yaml --profile <p> --source apt --with-arch)` and flatpak refs from `--source flatpak` (configure_*_flathub reads the same). Profile-specific configure_* runtime logic stays unchanged.
4. verify-deps.sh: post-condition list keeps the two meta debs; add assertion that /usr/share/novos/apt-markers/Packages contains exactly those two packages.

[POST_GATE]
bash -n scripts/stages/05-profiles-installer.sh && bash -n scripts/lib/verify-deps.sh && ! grep -nE 'NETINSTALL_CANDIDATE_PKGS|0\.0\.1~novos1|Fallback Marker' scripts/stages/05-profiles-installer.sh && grep -q 'packages list /etc/novos/packages.yaml' scripts/stages/05-profiles-installer.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 9E: Build-Time Availability Gate

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9E: Fail the image build when any declared package is unresolvable in the configured archives

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/05-profiles-installer.sh
READ_ONLY_REFS:
  - crates/novos-custom-tools/src/packages.rs (check-apt exit codes)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
grep -q 'packages list /etc/novos/packages.yaml' scripts/stages/05-profiles-installer.sh

[MUTATION_SPEC]
1. New Stage 5.0 (before 5.1, after `apt-get update`): run `novos-custom-tools packages check-apt /etc/novos/packages.yaml --all`; on exit 3 print the unresolvable list as `[FATAL] Declared packages unavailable in sid/third-party repos:` and exit 1. No skip flag.
2. safe_apt_install(): when `NOVOS_CONTEXT=build` (exported by Stage 5 header), a missing package is fatal instead of `[WARN] ... skipping`.

[POST_GATE]
bash -n scripts/stages/05-profiles-installer.sh && grep -q 'check-apt /etc/novos/packages.yaml --all' scripts/stages/05-profiles-installer.sh && ! grep -n "not found in APT cache; skipping" scripts/stages/05-profiles-installer.sh | grep -v NOVOS_CONTEXT

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 9F: Install-Time Failure Report

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9F: Calamares/novos-setup continue on package failure but record and surface a report

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/05-profiles-installer.sh
  - calamares/modules/shellprocess-novos.conf
READ_ONLY_REFS:
  - scripts/lib/verify-deps.sh (zero-$ rule for shellprocess-*.conf)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Keep zero `$` tokens in shellprocess-*.conf.

[PRE_GATE]
grep -q 'run_post_calamares' scripts/stages/05-profiles-installer.sh

[MUTATION_SPEC]
1. novos-setup (NOVOS_CONTEXT=install, the default at runtime): safe_apt_install installs per package batch; on batch failure retries each package individually; each failure (missing or apt error) appends `profile<TAB>package<TAB>reason` to /var/log/novos/package-failures.tsv. Flatpak failures likewise.
2. run_post_calamares: at the end, if the TSV is non-empty, write /etc/xdg/autostart/novos-install-report.desktop launching `novos-setup --report` (opens a COSMIC terminal / notify-send summary, self-removes after first view) and print the summary to the Calamares log.
3. `novos-setup --report`: prints the TSV grouped by profile and the retry command `sudo novos-setup --install <profile>`.
4. shellprocess-novos.conf: after the post-calamares command add `/usr/local/bin/novos-setup --report --log-only` (no `$`).

[POST_GATE]
bash -n scripts/stages/05-profiles-installer.sh && ! grep -n '\$' calamares/modules/shellprocess-novos.conf && grep -q 'package-failures.tsv' scripts/stages/05-profiles-installer.sh && grep -q -- '--report' calamares/modules/shellprocess-novos.conf

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD: fresh build + QEMU install with gaming,dev profiles; logs to logs/.
```

---

### STEP 9G: Tailscale from Its Official Signed Apt Repo

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9G: Add Tailscale's official signed apt repo for sid in Stage 3 and remove the curl | sh installer from Stage 5

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/03-core-system.sh
  - scripts/stages/05-profiles-installer.sh
READ_ONLY_REFS:
  - config/build.env (DEBIAN_SUITE="sid")
  - config/packages.yaml (tailscale: source apt, network profile)
  - https://pkgs.tailscale.com/stable/debian/sid.tailscale-keyring.list (verified 2026-11-06: `deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/debian sid main`)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
grep -q 'download.docker.com/linux/debian/gpg' scripts/stages/03-core-system.sh && grep -q 'tailscale.com/install.sh' scripts/stages/05-profiles-installer.sh

[MUTATION_SPEC]
1. 03-core-system.sh, Stage 3.2 "Pre-configure Third-Party Repositories" block: after the VS Code repo, add a numbered "Tailscale Repository" entry following the existing pattern:
   `curl -fsSL https://pkgs.tailscale.com/stable/debian/sid.noarmor.gpg -o /etc/apt/keyrings/tailscale-archive-keyring.gpg` (the .noarmor key is already binary: no gpg --dearmor), `chmod 0644` it, and
   `echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/debian ${DEBIAN_SUITE} main" > /etc/apt/sources.list.d/tailscale.list`.
   A failed key download is fatal (no `|| true`, no `if ... then` fallback). Add tailscale to the block's header comment list.
2. 05-profiles-installer.sh, network profile configuration: delete the `if [[ "$POST_CALAMARES" != "true" ]] && ! command -v tailscale ...` block that pipes https://tailscale.com/install.sh into sh. tailscale now installs through the manifest's network apt list. Leave every other line unchanged.

[POST_GATE]
bash -n scripts/stages/03-core-system.sh && bash -n scripts/stages/05-profiles-installer.sh && grep -q 'pkgs.tailscale.com/stable/debian/sid.noarmor.gpg' scripts/stages/03-core-system.sh && grep -q 'signed-by=/etc/apt/keyrings/tailscale-archive-keyring.gpg' scripts/stages/03-core-system.sh && ! grep -n 'tailscale.com/install.sh' scripts/stages/05-profiles-installer.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 9H: `tarball` Custom-Tool Type and zig Entry

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 9H: Add a checksum-pinned `tarball` tool type to novos-custom-tools and declare zig 0.17.1 with it

[SCOPE_LOCK]
ALLOWED_FILES:
  - crates/novos-custom-tools/src/main.rs
  - custom-tools/manifest.yaml
READ_ONLY_REFS:
  - https://ziglang.org/download/index.json (read 2026-11-06: latest stable 0.17.1, x86_64-linux tarball https://ziglang.org/download/0.17.1/zig-x86_64-linux-0.17.1.tar.xz, shasum 3581863e3cbcfd0fce0efc682ce7e9e93da96668a977791d356a26d2a7e9b17a)
  - crates/novos-custom-tools/Cargo.toml (no dependencies; keep it that way)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. No new crate dependencies (shell out to curl, sha256sum and tar like github_release does).

[PRE_GATE]
grep -q 'pub const VALID_TOOL_TYPES: \[&str; 3\]' crates/novos-custom-tools/src/main.rs && grep -q -- '"--tool"' crates/novos-custom-tools/src/main.rs

[MUTATION_SPEC]
1. main.rs: add type `tarball` to VALID_TOOL_TYPES (length 4) and the module doc comment. Required fields: `version`, `url` (https only), `sha256` (exactly 64 lowercase hex), `install_dir` (absolute), `binary` (relative path inside the extracted tree), `install_target` (absolute). Validation errors follow the existing `Tool {name:?} (tarball): ...` style.
2. main.rs: plan + install for `tarball`: download `url` with curl -fL to a temp file; verify with `sha256sum` and fail on mismatch before extracting; extract with `tar -xf` (handles .tar.xz and .tar.gz) and `--strip-components=1` into `install_dir`; then symlink `install_target` -> `install_dir/binary` (replace an existing symlink, refuse to overwrite a regular file). `--dry-run` prints the plan, including url and sha256, without network access.
3. main.rs tests: tarball validation passes for a well-formed entry; fails for http url, short/uppercase sha256, relative install_dir, missing binary; dry-run plan contains the sha256. No test touches the network. Keep all existing tests passing.
4. custom-tools/manifest.yaml: update the header comment to list `tarball`; add
   `- name: zig`, `enabled: false` (installed by the dev profile via `--tool zig`, not by `--install-enabled`), `type: tarball`, `version: "0.17.1"`, the url and sha256 above, `install_dir: "/opt/zig"`, `binary: "zig"`, `install_target: "/usr/local/bin/zig"`.

[POST_GATE]
cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && cargo run -q -p novos-custom-tools -- --validate custom-tools/manifest.yaml && cargo run -q -p novos-custom-tools -- --manifest custom-tools/manifest.yaml --tool zig --dry-run | grep -q '3581863e3cbcfd0fce0efc682ce7e9e93da96668a977791d356a26d2a7e9b17a'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 9I: `custom-tool` Manifest Source; zig Off Apt; ghostty Removed

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9I: Let config/packages.yaml declare custom-tool entries, move zig to it, remove ghostty, and regenerate netinstall.yaml

[SCOPE_LOCK]
ALLOWED_FILES:
  - crates/novos-custom-tools/src/packages.rs
  - config/packages.yaml
  - calamares/modules/netinstall.yaml
READ_ONLY_REFS:
  - custom-tools/manifest.yaml (zig entry from 9H)
  - crates/novos-custom-tools/src/main.rs (`--source` already parses through packages::Source::parse)
  - Open Questions: "Decided 2026-11-06 (9G–9J)" and "Before 9I"
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Do not hand-edit netinstall.yaml: regenerate it with render-netinstall.

[PRE_GATE]
grep -q 'name: zig' custom-tools/manifest.yaml && grep -q 'type: tarball' custom-tools/manifest.yaml && grep -q '{name: ghostty, source: apt' config/packages.yaml

[MUTATION_SPEC]
1. packages.rs: add `Source::CustomTool` parsed from and printed as `custom-tool`. Validation: custom-tool entries must be arch amd64 and must not set `alternative`. `list --source custom-tool` returns them; `check-apt` and `render-netinstall` keep their apt (and apt + novos-meta) filters, so custom-tool entries are excluded from both. Update the source error text in packages.rs. Add tests for parse, validation and both exclusions; keep existing tests passing.
2. config/packages.yaml: change zig in the Polyglot Compilers subgroup to `{name: zig, source: custom-tool, arch: amd64, required: true, alternative: null, smoke: null}`. Delete the ghostty entry from the GPU terminals subgroup (user-approved removal). No other entry changes.
3. calamares/modules/netinstall.yaml: regenerate exactly as 9C does (`# GENERATED` header + `novos-custom-tools packages render-netinstall config/packages.yaml`). Expected diff: only the `zig` and `ghostty` lines disappear.

[POST_GATE]
cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && cargo run -q -p novos-custom-tools -- packages validate config/packages.yaml && cargo run -q -p novos-custom-tools -- packages list config/packages.yaml --source custom-tool | grep -qx zig && ! grep -n 'ghostty' config/packages.yaml calamares/modules/netinstall.yaml && diff <(cargo run -q -p novos-custom-tools -- packages render-netinstall config/packages.yaml) <(grep -v '^# GENERATED' calamares/modules/netinstall.yaml)

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status, including the netinstall.yaml diff.
```

---

### STEP 9J: Dev Profile Installs `custom-tool` Entries

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9J: Install the dev profile's custom-tool manifest entries (zig) through novos-custom-tools --tool

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/05-profiles-installer.sh
READ_ONLY_REFS:
  - config/packages.yaml, custom-tools/manifest.yaml
  - CLAUDE.md §6 (no `|| true` without `# novos-allow:`)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
cargo run -q -p novos-custom-tools -- packages list config/packages.yaml --profile dev --source custom-tool | grep -qx zig && grep -q '^install_dev_profile()' scripts/stages/05-profiles-installer.sh

[MUTATION_SPEC]
1. Add `install_manifest_custom_tools <profile>`: calls `load_manifest_pkgs <profile> custom-tool`, then for each name runs `/usr/local/bin/novos-custom-tools --manifest /etc/novos/custom-tools.yaml --tool "$name"`. A failure is fatal (no `|| true`). When `POST_CALAMARES=true`, print that online custom-tool downloads are skipped and return 0, matching the Flathub offline handling.
2. install_dev_profile: call `install_manifest_custom_tools dev` after the apt install and before configure_dev_runtime.
3. verify_profile_deps: add `zig` to the dev profile's bins (create the dev case if it does not exist).

[POST_GATE]
bash -n scripts/stages/05-profiles-installer.sh && grep -q 'install_manifest_custom_tools dev' scripts/stages/05-profiles-installer.sh && grep -q -- '--tool "\$name"' scripts/stages/05-profiles-installer.sh && ! git diff -U0 scripts/stages/05-profiles-installer.sh | grep -E '^\+' | grep '|| true'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD: fresh `sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso` (Stage 5.1d must pass with tailscale resolving) + `./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars`; logs to logs/.
```

---

### STEP 9K: Post-Calamares Installs Dev `custom-tool` Entries

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9K: When post-Calamares detects the dev group, download its custom-tool entries (zig); a failure warns and continues

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/05-profiles-installer.sh
READ_ONLY_REFS:
  - config/build.env (BUILD_PROFILES="none")
  - calamares/modules/shellprocess-novos.conf (runs `novos-setup --post-calamares` after netinstall, which already needs network)
  - Open Questions: "Before 9I" (user decision 2026-11-06)
  - CLAUDE.md §6 (no `|| true` without `# novos-allow:`)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
grep -q '^install_manifest_custom_tools()' scripts/stages/05-profiles-installer.sh && grep -q 'skipping online custom-tool downloads' scripts/stages/05-profiles-installer.sh && grep -q '^run_post_calamares()' scripts/stages/05-profiles-installer.sh

[MUTATION_SPEC]
1. install_manifest_custom_tools: remove the POST_CALAMARES early return. In post-Calamares mode run each `--tool "$name"` inside `if ! ...; then` and on failure print `[WARN] [Profile: <profile>] custom tool '<name>' failed to install; retry with: sudo novos-setup --install <profile>` and continue with the next entry (no `|| true`). In every other mode a failure stays fatal. If the 9F failure recorder (`/var/log/novos/package-failures.tsv`) exists by then, append `profile<TAB>name<TAB>custom-tool install failed`.
2. run_post_calamares, dev branch (the `any_pkg_installed docker-ce podman ...` block): call `install_manifest_custom_tools dev` before `configure_dev_runtime`.

[POST_GATE]
bash -n scripts/stages/05-profiles-installer.sh && test "$(grep -c 'install_manifest_custom_tools dev' scripts/stages/05-profiles-installer.sh)" -ge 2 && ! grep -n 'skipping online custom-tool downloads' scripts/stages/05-profiles-installer.sh && grep -q 'retry with: sudo novos-setup --install' scripts/stages/05-profiles-installer.sh && ! git diff -U0 scripts/stages/05-profiles-installer.sh | grep -E '^\+' | grep '|| true'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD: fresh `sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso` (Stage 5.1d must pass) + `./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars` with a Calamares install that selects the dev group (`zig version` must work); logs to logs/.
```

---

### STEP 9L: Atomic Binary Install in novos-custom-tools

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9L: Make install_binary_mode_755 replace the target atomically (temp file + rename) so installing over a running binary works

[SCOPE_LOCK]
ALLOWED_FILES:
  - crates/novos-custom-tools/src/main.rs
READ_ONLY_REFS:
  - scripts/stages/05-profiles-installer.sh (Stage 5.4 runs /usr/local/bin/novos-custom-tools --install-enabled)
  - custom-tools/manifest.yaml (enabled `local` entry novos-custom-tools, install_target /usr/local/bin/novos-custom-tools)
  - Build after 1d10dae: Stage 5.4 failed with "Failed copying /root/novos-build/target/release/novos-custom-tools to /usr/local/bin/novos-custom-tools: Text file busy (os error 26)". The copy path is unchanged since 7b603d5; earlier builds stopped before 5.4.
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. No new crate dependencies. Do not skip or disable the self-install entry.

[PRE_GATE]
grep -q '^fn install_binary_mode_755' crates/novos-custom-tools/src/main.rs && grep -A6 '^fn install_binary_mode_755' crates/novos-custom-tools/src/main.rs | grep -q 'fs::copy(src, dst)'

[MUTATION_SPEC]
1. install_binary_mode_755(src, dst): create dst's parent; copy src to a temp file in the same directory (`.<file name>.novos-tmp.<pid>`); set mode 0755 on the temp file; `fs::rename(temp, dst)`. rename(2) swaps the directory entry, so a running dst keeps executing its old inode and no ETXTBSY occurs. On any error remove the temp file and return an error that names src, dst and the failing operation. Same-directory temp keeps rename on one filesystem.
2. Leave all six callers unchanged (local, github_release raw/AppImage/tar, github_source); they all go through this function.
3. Tests (no network, temp dirs only): installs into a fresh path with mode 0755; replaces an existing file; replaces a file that is currently being executed (spawn a copy of a short-lived binary such as `/bin/sleep`-equivalent copied into the temp dir, start it, install over it, assert success, then kill/wait the child); leaves no `.novos-tmp.` file behind on success or on a failing copy (missing src). Keep all existing tests passing.

[POST_GATE]
cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && grep -A20 '^fn install_binary_mode_755' crates/novos-custom-tools/src/main.rs | grep -q 'fs::rename' && ! grep -A20 '^fn install_binary_mode_755' crates/novos-custom-tools/src/main.rs | grep -q 'fs::copy(src, dst)'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD: fresh `sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso` (Stage 5.4 must pass) + `./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars` with a Calamares install that selects the dev group (`zig version` must work); logs to logs/.
```

---

### STEP 9M: Tarball Installs Owned by Root

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 9M: Extract `tarball` tools with tar --no-same-owner so installed files are owned by the installing user (root), not the uid recorded in the archive

[SCOPE_LOCK]
ALLOWED_FILES:
  - crates/novos-custom-tools/src/main.rs
READ_ONLY_REFS:
  - custom-tools/manifest.yaml (zig tarball entry, install_dir /opt/zig)
  - VM install after adeeebc (2026-11-07): `zig version` 0.17.1 works, but `/opt/zig/zig` is owned by 1000:1000 (the first user account), because tar run as root keeps the archive's recorded owner. A normal user could replace a compiler that root runs from PATH.
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. No new crate dependencies. Do not change the github_release tar.gz path (it extracts to a temp dir and installs through install_binary_mode_755, which already creates root-owned files).

[PRE_GATE]
grep -q '^fn install_tarball' crates/novos-custom-tools/src/main.rs && ! grep -n -- '--no-same-owner' crates/novos-custom-tools/src/main.rs

[MUTATION_SPEC]
1. Add `fn tarball_extract_args(archive: &Path, install_dir: &str) -> Vec<String>` returning `-xf <archive> --strip-components=1 --no-same-owner -C <install_dir>`. install_tarball builds its `tar` command from it; behavior is otherwise unchanged (sha256 still verified before extraction).
2. Update the tarball dry-run plan step text to show `--no-same-owner`.
3. Tests (no network): `tarball_extract_args` contains `--no-same-owner` and `--strip-components=1` in that order before `-C`; the tarball dry-run plan step mentions `--no-same-owner`. Keep all existing tests passing.
4. Systems already installed keep their 1000:1000-owned `/opt/zig` until zig is reinstalled; do not add migration code here. Note it in the report.

[POST_GATE]
cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && grep -A12 '^fn tarball_extract_args' crates/novos-custom-tools/src/main.rs | grep -q -- '--no-same-owner' && cargo run -q -p novos-custom-tools -- --manifest custom-tools/manifest.yaml --tool zig --dry-run | grep -q -- '--no-same-owner'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD: fresh `sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso` + `./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars`, install with the dev group, boot the installed disk (Esc → Boot Manager → VirtIO disk), then `ls -ln /opt/zig/zig` must show uid 0 gid 0; logs to logs/.
```

---

### STEP 10A: Wire ISO Variance Lock into the Build

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 10A: After Stage 6, run verify-variance against the produced ISO; any drop/downgrade fails the build

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/build-novos.sh
READ_ONLY_REFS:
  - scripts/nebulous-ctl.py (cmd_verify_variance, EXIT_VARIANCE_LOCK)
  - .nebulous/golden-snapshot.toml
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Never edit golden/approvals/manifest.

[PRE_GATE]
grep -q 'def cmd_verify_variance' scripts/nebulous-ctl.py && test -f .nebulous/golden-snapshot.toml

[MUTATION_SPEC]
1. After Stage 6 and before the "Build Complete" banner: run (as SUDO_USER) `scripts/nebulous-ctl.py verify-variance --iso "$ISO_PATH" --json > logs/variance-<UTC timestamp>.json`. Non-zero → print violations, rename the ISO to `${ISO_PATH%.iso}.REJECTED.iso`, exit with the same code. Upgrades are reported, not failures.
2. Banner on success prints the report path and: "To accept intended changes run: ./scripts/nebulous-ctl.py approve-variance … / bless-golden (user only)".
3. No flag to skip the check.

[POST_GATE]
bash -n scripts/build-novos.sh && grep -q 'verify-variance --iso' scripts/build-novos.sh && grep -q 'REJECTED.iso' scripts/build-novos.sh && ! grep -nE 'skip-variance|NO_VARIANCE' scripts/build-novos.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Note to user: re-bless the golden from their next verified ISO (golden [iso].path references another host).
```

---

### STEP 10B: Suppression Ratchet and Forbidden-Pattern Gate

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 10B: Count unannotated error suppressions per file; fail if any count rises; forbid faking patterns

[SCOPE_LOCK]
ALLOWED_FILES:
  - config/suppression-baseline.toml
  - scripts/lib/verify-deps.sh
READ_ONLY_REFS:
  - scripts/stages/*.sh, scripts/build-novos.sh, calamares/modules/*.conf
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test ! -e config/suppression-baseline.toml

[MUTATION_SPEC]
1. Suppression = line matching `\|\| (true|:)\b` or `set \+e` without `# novos-allow:`. Generate config/suppression-baseline.toml with current per-file counts for scripts/build-novos.sh, scripts/stages/*.sh, scripts/lib/verify-deps.sh, calamares/modules/*.conf. Header: "Ratchet: counts may only decrease. Editing requires the active step to list this file."
2. verify-deps.sh: `check_suppression_ratchet` (fail if any file exceeds baseline or a new file has >0) and `check_forbidden_patterns` (fail on `trusted=yes` except the novos-markers.list line, `--force-depends`, `equivs`, `0.0.1~novos`, `--from-stage` outside mcp_nebulous_ci.py's guard). Both run in run_self_test and in verify_host_deps (pre-build).

[POST_GATE]
bash -n scripts/lib/verify-deps.sh && python3 -c 'import tomllib;tomllib.load(open("config/suppression-baseline.toml","rb"))' && bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet && check_forbidden_patterns'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 10C: De-Mask Stage 3

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 10C: Replace or justify every suppression in 03-core-system.sh; lower its baseline

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/03-core-system.sh
  - config/suppression-baseline.toml
READ_ONLY_REFS:
  - scripts/lib/verify-deps.sh
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Do not remove packages, repos, or verification steps.

[PRE_GATE]
bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet'

[MUTATION_SPEC]
1. For each suppression: (a) legitimately optional/idempotent (e.g. rm -f of possibly absent file, teardown) → replace with an explicit existence test or annotate `# novos-allow: <reason>`; (b) masks a real failure (apt, gpg key fetch, debconf-set-selections, systemctl enable, Limine build) → remove suppression; add a clear `[FATAL]` message where helpful.
2. Set the file's baseline to the new unannotated count (target 0).

[POST_GATE]
bash -n scripts/stages/03-core-system.sh && bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet' && test "$(grep -E '\|\| (true|:)' scripts/stages/03-core-system.sh | grep -vc 'novos-allow:')" -eq 0

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 10D: De-Mask Stage 4

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 10D: Replace or justify every suppression in 04-cosmic-desktop.sh; lower its baseline

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/04-cosmic-desktop.sh
  - config/suppression-baseline.toml
READ_ONLY_REFS:
  - scripts/lib/verify-deps.sh
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Do not remove packages, greeter fallbacks, or verification steps.

[PRE_GATE]
bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet'

[MUTATION_SPEC]
1. Same rules as 10C. Keep intentional runtime fallbacks (hardware EGL → software GL → tuigreet) but express them as explicit `if` branches, not `|| true`.
2. Lower the file's baseline (target 0).

[POST_GATE]
bash -n scripts/stages/04-cosmic-desktop.sh && bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet' && test "$(grep -E '\|\| (true|:)' scripts/stages/04-cosmic-desktop.sh | grep -vc 'novos-allow:')" -eq 0

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 10E: De-Mask Stage 5

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 10E: Replace or justify every suppression in 05-profiles-installer.sh (incl. embedded novos-setup); lower baseline

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/05-profiles-installer.sh
  - config/suppression-baseline.toml
READ_ONLY_REFS:
  - scripts/lib/verify-deps.sh
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Install-time continue-on-failure must use the 9F TSV mechanism, never `|| true`.

[PRE_GATE]
bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet' && grep -q 'package-failures.tsv' scripts/stages/05-profiles-installer.sh

[MUTATION_SPEC]
1. Same rules as 10C. In novos-setup runtime paths, failures that must not abort an install are routed to the failure TSV with a reason. safe_apt_purge: drop the `dpkg --purge --force-depends` fallback.
2. Lower the file's baseline (target 0).

[POST_GATE]
bash -n scripts/stages/05-profiles-installer.sh && bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet && check_forbidden_patterns' && test "$(grep -E '\|\| (true|:)' scripts/stages/05-profiles-installer.sh | grep -vc 'novos-allow:')" -eq 0

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 10F: De-Mask Stage 2, Stage 6 and Orchestrator

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 10F: Replace or justify suppressions in 02-bootstrap-chroot.sh, 06-snapshot-finalize.sh, build-novos.sh

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/02-bootstrap-chroot.sh
  - scripts/stages/06-snapshot-finalize.sh
  - scripts/build-novos.sh
  - config/suppression-baseline.toml (baseline counter for the three files above)
READ_ONLY_REFS:
  - scripts/lib/verify-deps.sh
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. cleanup() trap teardown may keep annotated suppressions.

[PRE_GATE]
bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet'

[MUTATION_SPEC]
1. Same rules as 10C. Teardown in cleanup()/unmount paths: annotate `# novos-allow: best-effort teardown`.
2. Lower the three baselines (target 0 unannotated).

[POST_GATE]
for f in scripts/stages/02-bootstrap-chroot.sh scripts/stages/06-snapshot-finalize.sh scripts/build-novos.sh; do bash -n "$f" || exit 1; test "$(grep -E '\|\| (true|:)' "$f" | grep -vc 'novos-allow:')" -eq 0 || exit 1; done && bash -c 'source scripts/lib/verify-deps.sh && check_suppression_ratchet'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD (fresh 1–6 + QEMU install); errors previously masked are expected to surface.
```

---

### STEP 11A: Base Workstation Admin/Storage Set

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 11A: Add the approved admin/storage package set to the base layer and install base from the manifest

[SCOPE_LOCK]
ALLOWED_FILES:
  - config/packages.yaml
  - scripts/stages/03-core-system.sh
READ_ONLY_REFS:
  - crates/novos-custom-tools/src/packages.rs
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
cargo run -q -p novos-custom-tools -- packages validate config/packages.yaml && grep -q '^## APPROVED 11A' STEPPED_IMPLEMENTATION_PLAN.md

[MUTATION_SPEC]
Proposed set (user must approve/edit, then mark "## APPROVED 11A" under Open Questions):
  storage/RAID: mdadm, lvm2, cryptsetup, thin-provisioning-tools, dosfstools, exfatprogs, ntfs-3g, xfsprogs, e2fsprogs, gdisk, nvme-cli, smartmontools, hdparm, sg3-utils (initramfs integration via dracut, not cryptsetup-initramfs)
  hardware: lm-sensors, fwupd, pciutils, usbutils, dmidecode, ethtool, iw, rfkill
  admin: rsync, zstd, xz-utils, unzip, p7zip-full, lsof, strace, htop, btop, ncdu, tmux, bash-completion, man-db, plocate, openssh-client, curl, wget, jq
1. Add a `base-admin` group (layer base, critical true) with the approved names (skip any already declared elsewhere).
2. Stage 3: install `novos-custom-tools packages list /root/novos-build/config/packages.yaml --layer base --with-arch` in one apt-get call (the binary is staged by Stage 2). Hard-coded Stage 3 lists remain until 9A parity proves they are all in the manifest, then are replaced by this call.
3. dracut: add `add_dracutmodules+=" mdraid lvm crypt "` to the NovOS dracut conf in Stage 3 so installed systems on RAID/LVM/LUKS boot.

[POST_GATE]
bash -n scripts/stages/03-core-system.sh && cargo run -q -p novos-custom-tools -- packages list config/packages.yaml --layer base | grep -qx mdadm && grep -q 'mdraid' scripts/stages/03-core-system.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 11B: Native Steam + i386 Libraries

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 11B: Replace Flathub Steam with native steam-installer and full i386 runtime libraries

[SCOPE_LOCK]
ALLOWED_FILES:
  - config/packages.yaml
  - scripts/stages/05-profiles-installer.sh
READ_ONLY_REFS:
  - scripts/stages/03-core-system.sh (i386 multiarch, contrib/non-free enabled)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. The removal of the Flathub com.valvesoftware.Steam entry is user-approved by this step (decision 7); annotate the diff accordingly.

[PRE_GATE]
grep -q 'dpkg --add-architecture i386' scripts/stages/03-core-system.sh && grep -q '^## APPROVED 11B' STEPPED_IMPLEMENTATION_PLAN.md

[MUTATION_SPEC]
1. config/packages.yaml gaming group: add apt entries steam-installer, steam-devices, steam-libs-amd64, steam-libs-i386 (arch i386 where applicable), libgl1-mesa-dri:i386, libgl1:i386, libglx-mesa0:i386, mesa-vulkan-drivers:i386, libvulkan1:i386, libva2:i386, libgbm1:i386, libpulse0:i386, libnss3:i386. Remove flatpak com.valvesoftware.Steam (approved). Keep Heroic/Bottles/ProtonPlus flatpaks.
2. novos-setup: before gaming apt install, preseed `steam steam/question select I AGREE` and `steam steam/license note ''` (only if approved; otherwise leave interactive first-run). configure_gaming_flathub no longer installs Steam. verify_profile_deps gaming checks `steam` binary.

[POST_GATE]
bash -n scripts/stages/05-profiles-installer.sh && cargo run -q -p novos-custom-tools -- packages list config/packages.yaml --profile gaming --with-arch | grep -qx 'libgl1-mesa-dri:i386' && ! cargo run -q -p novos-custom-tools -- packages list config/packages.yaml --source flatpak | grep -q 'com.valvesoftware.Steam'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 11C: GUI App Launch Smoke Test in QEMU Harness

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 11C: Add --smoke-apps to test-docker-qemu.py driven by smoke fields in config/packages.yaml

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/test-docker-qemu.py
  - config/packages.yaml
READ_ONLY_REFS:
  - scripts/test-docker-qemu.py (run_guest_ssh, run_install_e2e_on_guest)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Agents do not run the VM test; the user does.

[PRE_GATE]
grep -q 'def run_guest_ssh' scripts/test-docker-qemu.py && grep -q '^## APPROVED 11C' STEPPED_IMPLEMENTATION_PLAN.md

[MUTATION_SPEC]
1. config/packages.yaml: set `smoke: {exec: <cmd>, alive_secs: 10}` for every shipped GUI app (COSMIC apps, browsers, the identified broken COSMIC Firefox item, Steam, editors) and `alternative:` where one exists (e.g. firefox-esr).
2. test-docker-qemu.py `--smoke-apps`: after install+login, read the installed /etc/novos/packages.yaml via SSH, for each smoke entry run it inside the user's graphical session (`systemd-run --user --machine=<user>@ --wait=no`, env from the running cosmic-session), wait alive_secs, record pass if the process is alive and no new coredump/journal error for that unit; capture a QMP screendump. Write logs/app-smoke-<UTC>.json `{app, package, result, reason, screenshot}` and a proposed config/app-health.toml diff to logs/app-health.proposed.toml. Never writes config/ itself.
3. Self-test covers JSON/TOML emission with a mocked SSH runner.

[POST_GATE]
python3 -m py_compile scripts/test-docker-qemu.py && python3 scripts/test-docker-qemu.py --self-test && cargo run -q -p novos-custom-tools -- packages validate config/packages.yaml

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 11D: Broken-App Swap via User-Committed Health File

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 11D: Consume config/app-health.toml so broken apps are replaced by their declared alternative

[SCOPE_LOCK]
ALLOWED_FILES:
  - crates/novos-custom-tools/src/packages.rs
  - config/app-health.toml
READ_ONLY_REFS:
  - logs/app-health.proposed.toml (from the user's smoke run)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. config/app-health.toml content must be copied verbatim from the user-approved proposal; agents never invent health entries.

[PRE_GATE]
test -f logs/app-health.proposed.toml && grep -q '^## APPROVED 11D' STEPPED_IMPLEMENTATION_PLAN.md

[MUTATION_SPEC]
1. config/app-health.toml schema: `[[broken]] package, verified_on (snapshot ts or date), reason, recheck = true`.
2. packages.rs: `list`/`render-netinstall` substitute `alternative` for broken packages when `--health` is passed (and by default when /etc/novos/app-health.toml exists); `validate` errors if a broken package has no alternative. Broken entries stay in packages.yaml (never deleted) so 11C re-tests them each release.
3. Tests for substitution and missing-alternative error.

[POST_GATE]
cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && python3 -c 'import tomllib;tomllib.load(open("config/app-health.toml","rb"))'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD.
```

---

### STEP 12A: `snapshot.debian.org` Archive Selection

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 12A: Add DEBIAN_SNAPSHOT to build.env; Stages 2–3 use snapshot.debian.org when set

[SCOPE_LOCK]
ALLOWED_FILES:
  - config/build.env
  - scripts/stages/02-bootstrap-chroot.sh
  - scripts/stages/03-core-system.sh
READ_ONLY_REFS:
  - scripts/build-novos.sh
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
grep -q 'DEBIAN_MIRROR=' config/build.env

[MUTATION_SPEC]
1. build.env: `export DEBIAN_SNAPSHOT="${DEBIAN_SNAPSHOT:-}"` (empty = live sid; format YYYYMMDDTHHMMSSZ) and derived `DEBIAN_ARCHIVE_URL` = mirror or `https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/`.
2. Stage 2 debootstrap uses DEBIAN_ARCHIVE_URL. Stage 3 sources.list uses it, adding `[check-valid-until=no]` when a snapshot is set, and writes /etc/novos/build-snapshot with the value (or `live:<UTC build time>`).
3. Installed systems always get the live mirror in /etc/apt/sources.list after the image is finalized (snapshot only governs build inputs) — document in a comment.

[POST_GATE]
bash -n config/build.env && bash -n scripts/stages/02-bootstrap-chroot.sh && bash -n scripts/stages/03-core-system.sh && grep -q 'snapshot.debian.org' config/build.env && grep -q 'check-valid-until=no' scripts/stages/03-core-system.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 12B: Release Records and `--pin-last-good` Fallback

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 12B: build-novos.sh builds fresh against neb release records and holds (--pin-last-good, --pin-packages, --hold-file)

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/build-novos.sh
  - config/releases/README.md
READ_ONLY_REFS:
  - config/build.env
  - docs/NEB_RELEASE_CONTRACT.md ("Candidate lifecycle": release records, holds file, artifact cache)
  - scripts/stages/06-snapshot-finalize.sh (15A third-party .deb export)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Agents never create config/releases/*.toml or .tracker/release/* (written only by `neb release promote` / `record-failure`). No `trusted=yes` sources.

[PRE_GATE]
grep -q 'DEBIAN_SNAPSHOT' config/build.env && grep -q 'artifact-cache' scripts/build-novos.sh && neb release --help | grep -q holds

[MUTATION_SPEC]
1. Amended 2026-11-07 (automatic promotion policy): release records are written by `neb release promote` after a candidate passes build + VM, not by bless-golden. This step no longer touches nebulous-ctl.py.
2. build-novos.sh `--hold-file <path>` (format from the contract: component, held_version, debian|third_party): Debian components are pinned to their held version via a snapshot.debian.org source at priority 100 plus per-package Pin-Priority 1001 (DEBIAN_SNAPSHOT from the release record that last carried that version); third-party components are installed from the 15A cache by path (`apt-get install ./<file>.deb`), never from a `trusted=yes` source. A held version missing from both the snapshot and the cache is fatal and names the component.
3. `--pin-last-good` = `--hold-file` generated by `neb release holds --all-last-good`; `--pin-packages a,b` = holds for only those components. Every mode is still a full fresh Stage 1–6 build.
4. config/releases/README.md: record format, written by `neb release promote` only, how holds and fallback work.

[POST_GATE]
bash -n scripts/build-novos.sh && grep -q -- '--hold-file' scripts/build-novos.sh && grep -q -- '--pin-last-good' scripts/build-novos.sh && ! grep -n 'trusted=yes' scripts/build-novos.sh && test -f config/releases/README.md

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 12C: Pin Limine to a Verified Tag + Commit

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 12C: Replace branch-tip Limine clone with LIMINE_REF + LIMINE_COMMIT verification

[SCOPE_LOCK]
ALLOWED_FILES:
  - config/build.env
  - scripts/stages/03-core-system.sh
READ_ONLY_REFS:
  - https://github.com/limine-bootloader/limine tags (user supplies chosen tag/commit if network blocked)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Do not change Limine major version.

[PRE_GATE]
grep -q 'branch=v8.x-binary' scripts/stages/03-core-system.sh

[MUTATION_SPEC]
1. build.env: `LIMINE_REF` (latest v8.x-binary release tag) and `LIMINE_COMMIT` (full sha).
2. Stage 3: `git clone --depth=1 --branch "$LIMINE_REF"`, then assert `git rev-parse HEAD == $LIMINE_COMMIT` else `[FATAL]`.

[POST_GATE]
bash -n config/build.env && bash -n scripts/stages/03-core-system.sh && grep -q 'LIMINE_COMMIT' scripts/stages/03-core-system.sh && ! grep -q 'branch=v8.x-binary' scripts/stages/03-core-system.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD.
```

---

### STEP 13A: `neb release` Interface Contract

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 13A: Specify the neb features required to replace nebulous-ctl.py and mcp_nebulous_ci.py

[SCOPE_LOCK]
ALLOWED_FILES:
  - docs/NEB_RELEASE_CONTRACT.md
READ_ONLY_REFS:
  - scripts/nebulous-ctl.py, scripts/mcp_nebulous_ci.py, .nebulous/*.toml, .tracker/*
  - scripts/test-docker-qemu.py (live-boot and install-e2e tiers, exit codes, artifacts)
  - /srv/devshare/Nebulous/README.md
  - Open Questions: "Decided 2026-11-07 (automatic promotion)"
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. No edits in /srv/devshare/Nebulous.

[PRE_GATE]
test -f scripts/nebulous-ctl.py && test -f /srv/devshare/Nebulous/Cargo.toml

[MUTATION_SPEC]
1. Map every nebulous-ctl subcommand (extract-golden, verify-variance, record-attempt, check-guardrail, bless-golden, approve-variance, unlock-loop, pins-from-release, --self-test) and every MCP tool in mcp_nebulous_ci.py to a `neb release …` / `neb mcp` equivalent: arguments, exit codes (keep EXIT_VARIANCE_LOCK value), JSON output schema, file locations under .tracker/release/ (golden.toml, approvals.toml, releases/*.toml, loop-state).
2. Non-functional requirements: user-only bless/approve (tty check), forward-only variance policy, no Python runtime.
3. Migration and acceptance checklist used by 13C/13D.
4. "Candidate lifecycle" section (user policy 2026-11-07):
   a. Component identity: Debian source package (dpkg `Source:`, else package name); a kernel line is its meta package (e.g. `linux-xanmod-x64v3`) plus its ABI-named image/headers. A renamed ABI package (linux-image-7.2.15-… → linux-image-7.3.3-…) is an upgrade, not a removal. A removal is a component absent from the new ISO; it and any downgrade still need user `approve-variance`.
   b. `neb release candidate --iso <iso>`: every component whose version is newer than golden, or new, is automatically approved for testing. Output: the changed set and the holds in force.
   c. Failure ledger `.tracker/release/failures.toml`: (component, version, phase, log path, UTC time). A version in the ledger is held at last-good on later builds; a newer version of that component is eligible again automatically. `neb release holds [--all-last-good] [--format hold-file]` emits the hold file consumed by build-novos.sh `--hold-file` (12B).
   d. Phases in order: `build`, `vm-live-boot`, `vm-install-e2e` (scripts/test-docker-qemu.py tiers). `neb release record-phase <phase> pass|fail --log <path>`. A failure is re-run once on identical inputs before it counts (flake guard).
   e. `neb release promote --iso <iso>`: only when all three phases passed for that candidate; atomically writes golden.toml, `releases/<version>-<UTC>.toml` (snapshot, iso sha256, golden sha256, component versions, third-party cache paths) and clears resolved holds. No tty requirement: automation promotes. bless-golden, approve-variance and unlock-loop stay user-only (tty).
   f. `neb release bisect start|next|record pass|fail`: on a confirmed failure with N>1 changed components, deterministic halving (kernel line counts as one component); each round emits a hold file for a fresh build. Ends when one component remains → `record-failure` on that (component, version), then the candidate minus the culprit is re-run through all phases and promoted if it passes. Both halves pass while the whole fails → record an "interaction" failure on the set, hold it, stop and report. Both halves fail → bisect each. Hard cap of 2·ceil(log2 N)+2 rounds, then stop and report. State in `.tracker/release/bisect.toml`.
   g. Exit codes and JSON for every new command; a candidate never blocks on an upgrade unless the ledger holds that exact version.

[POST_GATE]
for c in extract-golden verify-variance record-attempt check-guardrail bless-golden approve-variance unlock-loop pins-from-release candidate record-phase promote bisect holds record-failure failures.toml 'Candidate lifecycle'; do grep -q -- "$c" docs/NEB_RELEASE_CONTRACT.md || exit 1; done

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 13B: (External) Implement the Contract in Nebulous

* **Recommended Command:** — (executed under `/srv/devshare/Nebulous/STEPPED_IMPLEMENTATION_PLAN.md`)

```text
[STEP_ID]
STEP 13B: EXTERNAL — implement docs/NEB_RELEASE_CONTRACT.md in /srv/devshare/Nebulous

[SCOPE_LOCK]
ALLOWED_FILES:
  - None in NovOS.
READ_ONLY_REFS:
  - docs/NEB_RELEASE_CONTRACT.md
FORBIDDEN_SCOPE:
  - All NovOS files.

[PRE_GATE]
test -f docs/NEB_RELEASE_CONTRACT.md

[MUTATION_SPEC]
1. Plan and execute in the Nebulous repo under its own governance. NovOS resumes at 13C when `neb release --help` lists every contract command and `neb release self-test` passes.

[POST_GATE]
neb release --help | grep -q verify-variance && neb release self-test

[HALT_INVARIANT]
Halt; NovOS work on Phase G7 waits for this gate.
```

---

### STEP 13C: Switch Callers to `neb release`

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 13C: Replace nebulous-ctl.py / mcp_nebulous_ci.py invocations with neb equivalents

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/build-novos.sh
  - scripts/lib/verify-deps.sh
  - .mcp.json
READ_ONLY_REFS:
  - docs/NEB_RELEASE_CONTRACT.md
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
neb release --help | grep -q verify-variance && neb release self-test

[MUTATION_SPEC]
1. build-novos.sh: verify-variance, --pin-last-good, --pin-packages call `neb release …` (same semantics, same exit codes).
2. verify-deps.sh: self-test and host-dep checks require `neb`, call `neb release self-test` and `neb release verify-variance --repo-only`.
3. .mcp.json: `nebulous` → `neb mcp`.

[POST_GATE]
bash -n scripts/build-novos.sh && bash -n scripts/lib/verify-deps.sh && ! grep -nE 'nebulous-ctl|mcp_nebulous_ci' scripts/build-novos.sh scripts/lib/verify-deps.sh .mcp.json && jq -e . .mcp.json >/dev/null

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 13D: Migrate Data to `.tracker`, Retire Python Nebulous

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 13D: Move golden/approvals/manifest into .tracker/release via neb import; delete Python Nebulous (user-approved)

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/nebulous-ctl.py (DELETE)
  - scripts/mcp_nebulous_ci.py (DELETE)
  - .nebulous/ (DELETE — protected; requires "## APPROVED 13D" from the user)
  - .tracker/release/ (written only by `neb release import`)
READ_ONLY_REFS:
  - docs/NEB_RELEASE_CONTRACT.md
FORBIDDEN_SCOPE:
  - Any other file. No hand edits to imported data.

[PRE_GATE]
grep -q '^## APPROVED 13D' STEPPED_IMPLEMENTATION_PLAN.md && ! grep -rnE 'nebulous-ctl|mcp_nebulous_ci' scripts/build-novos.sh scripts/lib/verify-deps.sh .mcp.json

[MUTATION_SPEC]
1. Run `neb release import --from .nebulous` (user executes if guard blocks protected paths); verify `neb release verify-variance --repo-only` passes.
2. git rm scripts/nebulous-ctl.py scripts/mcp_nebulous_ci.py .nebulous/.

[POST_GATE]
test ! -e .nebulous && test ! -e scripts/nebulous-ctl.py && test -f .tracker/release/golden.toml && neb release verify-variance --repo-only

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 14A: Documentation Refresh

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 14A: Update BUILD_AND_TEST, ARCHITECTURE, PROFILES for Phase G behavior

[SCOPE_LOCK]
ALLOWED_FILES:
  - docs/BUILD_AND_TEST.md
  - docs/ARCHITECTURE.md
  - docs/PROFILES.md
READ_ONLY_REFS:
  - config/packages.yaml, config/build.env, config/releases/README.md, CLAUDE.md, scripts/build-novos.sh
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test -f config/packages.yaml && grep -q 'verify-variance' scripts/build-novos.sh

[MUTATION_SPEC]
1. BUILD_AND_TEST: candidate lifecycle (`scripts/release-candidate.sh`: build → live-boot → install-e2e → automatic promotion; bisection and the failure ledger; holds), variance lock + REJECTED ISO, suppression ratchet, availability gate, --pin-last-good / --pin-packages, DEBIAN_SNAPSHOT, smoke-apps run, how the user blesses a release.
2. ARCHITECTURE: package manifest → netinstall render → novos-setup flow; base-admin set and dracut mdraid/lvm/crypt; native Steam; Limine pin.
3. PROFILES: profiles sourced from config/packages.yaml; install failure report (`novos-setup --report`); app-health swaps.

[POST_GATE]
for k in 'verify-variance' 'packages.yaml' 'pin-last-good' 'novos-setup --report' 'app-health' 'release-candidate' 'bisect'; do grep -rq -- "$k" docs/ || exit 1; done

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

## Phase G8: Candidate Promotion (Decided 2026-11-07)

Order: 15B → 15C (same file) and 15A can run now; 12B needs 13B + 15A; 15D needs 13B, 12B and 15C.

### STEP 15A: Third-Party Artifact Cache

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 15A: Each build exports the exact third-party .debs it installed to a host cache, so later builds can hold a component at that version

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/stages/06-snapshot-finalize.sh
  - scripts/build-novos.sh
READ_ONLY_REFS:
  - scripts/stages/03-core-system.sh (third-party repos: XanMod, Debian COSMIC, Docker, VS Code, Tailscale, Antigravity; the XanMod retry path runs `apt-get clean`)
  - docs/NEB_RELEASE_CONTRACT.md if present ("Candidate lifecycle": cache paths in release records)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Do not change the existing `apt-get clean` line's behavior for the image. No `trusted=yes` sources.

[PRE_GATE]
grep -q 'apt-get clean' scripts/stages/06-snapshot-finalize.sh && ! grep -n 'artifact-cache' scripts/build-novos.sh

[MUTATION_SPEC]
1. build-novos.sh: `ARTIFACT_CACHE_DIR` (default `${REPO_ROOT}/build/artifact-cache`, owned by SUDO_USER), exported to Stage 6.
2. Stage 6, before the chroot `apt-get clean`: list installed packages whose installed version's apt origin is not a Debian mirror (chroot `apt-cache policy <pkg>`, the source of the `***` version). For each, copy `/var/cache/apt/archives/<pkg>_<ver>_<arch>.deb` to `${ARTIFACT_CACHE_DIR}/debs/`; if it is not cached, fetch exactly that version with chroot `apt-get download <pkg>=<ver>`. A package that can be neither copied nor downloaded is fatal and names it.
3. Write `${ARTIFACT_CACHE_DIR}/index.toml` entries (package, version, arch, file, sha256, source repo, ISO build UTC). Existing files are kept (the cache accumulates across builds); identical name+version with a different sha256 is fatal.
4. No new `|| true`.

[POST_GATE]
bash -n scripts/stages/06-snapshot-finalize.sh && bash -n scripts/build-novos.sh && grep -q 'ARTIFACT_CACHE_DIR' scripts/build-novos.sh && grep -q 'apt-get download' scripts/stages/06-snapshot-finalize.sh && grep -q 'index.toml' scripts/stages/06-snapshot-finalize.sh && ! git diff -U0 scripts/stages/06-snapshot-finalize.sh scripts/build-novos.sh | grep -E '^\+' | grep '|| true'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER BUILD: fresh build; `build/artifact-cache/index.toml` must list the XanMod 7.3.3 image/headers and the tailscale .deb.
```

---

### STEP 15B: install-e2e Follows the Real Calamares Path

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 15B: Make the install-e2e tier reproduce what Calamares does (partition/mount config, netinstall groups, shellprocess post-install) instead of its own simplified install

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/test-docker-qemu.py
READ_ONLY_REFS:
  - calamares/settings.conf, calamares/modules/*.conf, calamares/modules/netinstall.yaml, calamares/modules/limine-bootloader/main.py
  - Open Questions: "Before 15B"
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. Destructive commands run only inside the guest against the virtual /dev/vda, never on the host.

[PRE_GATE]
grep -q '^def run_install_e2e_on_guest' scripts/test-docker-qemu.py && test -f calamares/modules/netinstall.yaml

[MUTATION_SPEC]
1. run_install_e2e_on_guest: read the Btrfs subvolume list and mount points from the Calamares mount module config (all 10: `@`, `@home`, `@snapshots`, `@var_log`, `@var_cache`, `@flatpak`, `@docker`, `@containerd`, `@llm`, `@devshare`) instead of the hard-coded 7; ESP layout from the Calamares partition config.
2. After unpacking the rootfs: install the packages of the selected netinstall groups into the target (new `--groups` option, default `dev,network`, names as in netinstall.yaml), then run every command of `calamares/modules/shellprocess-novos.conf` in the target chroot with `@@username@@` replaced by the test user, then the limine-bootloader module.
3. Record each sub-step as its own milestone (partition, rootfs, netinstall, shellprocess, bootloader) with pass/fail and log excerpt; any failure fails the tier.
4. Keep `live-boot` unchanged; keep the existing exit codes.

[POST_GATE]
python3 -m py_compile scripts/test-docker-qemu.py && grep -q 'shellprocess-novos.conf' scripts/test-docker-qemu.py && grep -q -- '--groups' scripts/test-docker-qemu.py && grep -q '@devshare' scripts/test-docker-qemu.py

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 15C: install-e2e Checks the Installed System

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 15C: After booting the installed disk, install-e2e verifies the selected groups' packages, custom tools and ownership

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/test-docker-qemu.py
READ_ONLY_REFS:
  - config/packages.yaml, custom-tools/manifest.yaml
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
grep -q 'shellprocess-novos.conf' scripts/test-docker-qemu.py && grep -q -- '--groups' scripts/test-docker-qemu.py

[MUTATION_SPEC]
1. New milestone M6_INSTALLED_CHECKS over SSH on the booted installed disk: every apt package of the selected groups (`novos-custom-tools packages list --profile <p> --source apt --with-arch`) is installed (`dpkg-query -W`); every custom-tool entry of those profiles runs (`zig version` equals the manifest version); `tailscale version` succeeds when `network` is selected; `/opt/zig` and everything under it is owned by 0:0 (`find /opt/zig ! -uid 0 -o ! -gid 0` prints nothing); `/usr/local/bin/zig` resolves into `/opt/zig`.
2. Each failure names the profile, item and observed value; any failure fails the tier.

[POST_GATE]
python3 -m py_compile scripts/test-docker-qemu.py && grep -q 'M6_INSTALLED_CHECKS' scripts/test-docker-qemu.py && grep -q 'zig version' scripts/test-docker-qemu.py && grep -q 'tailscale version' scripts/test-docker-qemu.py

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 15D: `release-candidate.sh` Pipeline

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 15D: One user-run command takes a candidate through build, VM live-boot and VM install-e2e, promotes it on success, and bisects on failure

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/release-candidate.sh
READ_ONLY_REFS:
  - docs/NEB_RELEASE_CONTRACT.md ("Candidate lifecycle")
  - scripts/build-novos.sh (--hold-file), scripts/test-docker-qemu.py (tiers, --groups)
  - CLAUDE.md §1, §2, §5 (fresh builds only; the user runs it)
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES. No `--from-stage`, no `--skip-variance-check`, no `|| true`.

[PRE_GATE]
neb release --help | grep -q bisect && grep -q -- '--hold-file' scripts/build-novos.sh && grep -q 'M6_INSTALLED_CHECKS' scripts/test-docker-qemu.py

[MUTATION_SPEC]
1. New `scripts/release-candidate.sh` (run by the user with sudo; refuses without root and refuses when invoked by an agent, same check as nebulous-ctl's host-only commands):
   a. `neb release holds --format hold-file > <run>/holds.toml`; fresh `build-novos.sh --target-iso <run>/candidate.iso --hold-file <run>/holds.toml`; `neb release candidate --iso` and `record-phase build`.
   b. `test-docker-qemu.py --tier live-boot`, then `--tier install-e2e --groups dev,network`; `record-phase` after each with its log.
   c. All pass → `neb release promote --iso <run>/candidate.iso`; print the release record path.
   d. A confirmed failure (re-run once on identical inputs) with more than one changed component → `neb release bisect start`, then loop: hold file from `bisect next` → fresh build + the same phases → `bisect record pass|fail`, until neb reports culprit, interaction or cap. Then re-run the candidate minus the culprit through a, b, c.
   e. Every run writes `logs/release-candidate-<UTC>/` (per-round build log, VM artifacts, holds, neb JSON) and ends with a one-screen summary: promoted / culprit (component, version, phase) / interaction set / cap reached.
2. Fresh Stage 1–6 build every round; no partial resumes.

[POST_GATE]
bash -n scripts/release-candidate.sh && grep -q 'neb release promote' scripts/release-candidate.sh && grep -q 'neb release bisect' scripts/release-candidate.sh && grep -q -- '--hold-file' scripts/release-candidate.sh && ! grep -nE -- '--from-stage|--skip-variance-check|\|\| true' scripts/release-candidate.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status. Request USER RUN: `sudo ./scripts/release-candidate.sh`; logs in logs/release-candidate-<UTC>/.
```

---

## Archive: Completed Step Prompts (6A–6G, landed in `f6c235e`)

### STEP 6A: Sync `.agents/` Governance into `/srv/devshare/NovOS` & Extract Standalone `slippery-tiles` Project (`scripts/export-slippery-tiles.sh`)

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 6A: Sync .agents/ Governance into /srv/devshare/NovOS & Extract Standalone slippery-tiles Project (scripts/export-slippery-tiles.sh)

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/export-slippery-tiles.sh
  - .agents/Locks.md
  - .agents/mcp_config.json
  - .agents/permissions.json
  - .agents/rules/argon-step-scope-lock.md
  - .agents/skills/Skills.md
READ_ONLY_REFS:
  - /media/infomoose/Bonus/Projects/NovOS/.agents/Locks.md
  - /media/infomoose/Bonus/Projects/NovOS/.agents/mcp_config.json
  - /media/infomoose/Bonus/Projects/NovOS/.agents/permissions.json
  - /media/infomoose/Bonus/Projects/NovOS/.agents/rules/argon-step-scope-lock.md
  - /media/infomoose/Bonus/Projects/NovOS/.agents/skills/Skills.md
  - crates/novos-slippery-tiles/Cargo.toml
  - crates/novos-slippery-tiles/src/main.rs
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test -f crates/novos-slippery-tiles/Cargo.toml && test -f crates/novos-slippery-tiles/src/main.rs && test -f /media/infomoose/Bonus/Projects/NovOS/.agents/permissions.json

[MUTATION_SPEC]
1. Sync `.agents/Locks.md`, `.agents/mcp_config.json`, `.agents/permissions.json`, `.agents/rules/argon-step-scope-lock.md`, and `.agents/skills/Skills.md` into `/srv/devshare/NovOS/.agents/`.
2. Create `scripts/export-slippery-tiles.sh` (`chmod +x`):
   - Supports `--dest <dir>` (default: `/srv/devshare/slippery-tiles`), `--staging-dir <dir>` (default: `${REPO_ROOT}/build/slippery-tiles-standalone`), and `--self-test`.
   - Generates a complete standalone Rust repository layout in `<staging-dir>` (and copies to `<dest>` automatically if `<dest>` or its parent is writable, or prints the one-liner host sync command if running inside a restricted container sandbox):
     - `Cargo.toml`: Standalone package `name = "slippery-tiles"`, `version = "0.1.0"`, `edition = "2021"`, with `[[bin]] name = "slippery-tiles" path = "src/main.rs"`.
     - `src/main.rs`: Preserves the full `crates/novos-slippery-tiles/src/main.rs` implementation and unit test suite.
     - `README.md`: Documents the standalone Slippery Tiles project purpose, CLI usage (`move`, `grow`, `shrink`, `pack`, `overlay`, `daemon`, `--self-test`), and build/test commands (`cargo build --release`, `cargo test`).
     - `.gitignore`: Ignores `/target` and `Cargo.lock` artifacts as appropriate.
   - When `--self-test` is passed:
     - Populates `${REPO_ROOT}/build/slippery-tiles-standalone`, runs `cargo fmt --manifest-path ${REPO_ROOT}/build/slippery-tiles-standalone/Cargo.toml --all -- --check`, `cargo clippy --manifest-path ${REPO_ROOT}/build/slippery-tiles-standalone/Cargo.toml --all-targets -- -D warnings`, and `cargo test --manifest-path ${REPO_ROOT}/build/slippery-tiles-standalone/Cargo.toml`.

[POST_GATE]
bash -n scripts/export-slippery-tiles.sh && ./scripts/export-slippery-tiles.sh --self-test && test -f .agents/permissions.json && test -f .agents/mcp_config.json && test -f build/slippery-tiles-standalone/Cargo.toml && test -f build/slippery-tiles-standalone/src/main.rs

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 6B: Remove `novos-slippery-tiles` from NovOS Rust Workspace & Update `novos-custom-tools` Manifest and Self-Tests

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 6B: Remove novos-slippery-tiles from NovOS Rust Workspace & Update novos-custom-tools Manifest and Self-Tests

[SCOPE_LOCK]
ALLOWED_FILES:
  - Cargo.toml
  - Cargo.lock
  - custom-tools/manifest.yaml
  - crates/novos-custom-tools/src/main.rs
  - crates/novos-slippery-tiles/Cargo.toml
  - crates/novos-slippery-tiles/src/main.rs
READ_ONLY_REFS:
  - build/slippery-tiles-standalone/Cargo.toml
  - build/slippery-tiles-standalone/src/main.rs
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test -f build/slippery-tiles-standalone/Cargo.toml && test -f build/slippery-tiles-standalone/src/main.rs && test -f crates/novos-custom-tools/src/main.rs

[MUTATION_SPEC]
1. In root `Cargo.toml`:
   - Remove `"crates/novos-slippery-tiles"` from `members` so only `"crates/novos-custom-tools"` remains in the NovOS workspace.
2. Delete `crates/novos-slippery-tiles/Cargo.toml` and `crates/novos-slippery-tiles/src/main.rs`.
3. In `custom-tools/manifest.yaml`:
   - Replace the `novos-slippery-tiles` local tool entry with `novos-custom-tools`:
     ```yaml
       - name: novos-custom-tools
         enabled: true
         type: local
         description: "NovOS Declarative Custom Tools & GitHub Component Builder"
         source_path: "target/release/novos-custom-tools"
         build_command: "cargo build --release -p novos-custom-tools"
         fallback_repo: "https://github.com/theinfomoose/NovOS.git"
         fallback_branch: "main"
         install_target: "/usr/local/bin/novos-custom-tools"
         symlinks:
           - "/usr/local/bin/novos-ct"
     ```
   - Ensure zero references to `novos-slippery-tiles` or `novos-gtile-overlay` remain in `custom-tools/manifest.yaml`.
4. In `crates/novos-custom-tools/src/main.rs`:
   - In `test_default_manifest_validates()`: verify `novos-custom-tools` is present and `enabled == true` (instead of `novos-slippery-tiles`).
   - In `test_fallback_yaml_parser_parity()`: verify `symlinks[0]` is `"/usr/local/bin/novos-ct"` and update the inline test fixture strings to use `novos-custom-tools` / `novos-ct` instead of `novos-slippery-tiles` / `novos-gtile-overlay`.
5. Run `cargo fmt --all` and update `Cargo.lock`.

[POST_GATE]
test ! -e crates/novos-slippery-tiles && ! grep -q "slippery-tiles" Cargo.toml custom-tools/manifest.yaml crates/novos-custom-tools/src/main.rs && cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && cargo build --release --workspace && ./target/release/novos-custom-tools --self-test && ./target/release/novos-custom-tools --validate custom-tools/manifest.yaml

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 6C: Purge `novos-slippery-tiles` from Build Orchestrator & Stage 2/4/5/6 Scripts

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 6C: Purge novos-slippery-tiles from Build Orchestrator & Stage 2/4/5/6 Scripts

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/build-novos.sh
  - scripts/stages/02-bootstrap-chroot.sh
  - scripts/stages/04-cosmic-desktop.sh
  - scripts/stages/05-profiles-installer.sh
  - scripts/stages/06-snapshot-finalize.sh
READ_ONLY_REFS:
  - custom-tools/manifest.yaml
  - Cargo.toml
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test ! -e crates/novos-slippery-tiles && test -x target/release/novos-custom-tools

[MUTATION_SPEC]
1. In `scripts/build-novos.sh`:
   - In `ensure_host_rust_binaries()` and `mount_existing_tree()`: check and stage only `target/release/novos-custom-tools` (remove all references to `novos-slippery-tiles`).
2. In `scripts/stages/02-bootstrap-chroot.sh`:
   - Check and stage only `target/release/novos-custom-tools` into `${MOUNT_ROOT}/root/novos-build/target/release/novos-custom-tools` (remove all references to `novos-slippery-tiles`).
3. In `scripts/stages/04-cosmic-desktop.sh`:
   - In Stage 4.6:
     - Build/check only `/root/novos-build/target/release/novos-custom-tools` and install `/usr/local/bin/novos-custom-tools` plus `/etc/novos/custom-tools.yaml`.
     - Remove `install ... novos-slippery-tiles`, `ln -sfn ... /usr/local/bin/novos-gtile-overlay`, `/usr/share/applications/novos-gtile-overlay.desktop`, and `/etc/systemd/user/novos-slippery-tiles.service`.
     - Remove `provision_cosmic_shortcuts` `novos-slippery-tiles` keybindings while preserving `chown` permissions on `/home/${DEFAULT_USER}/.config`, `/var/lib/cosmic-greeter`, `/var/cache/tuigreet`, and `/var/lib/greetd`.
4. In `scripts/stages/05-profiles-installer.sh`:
   - In Stage 5.1b: check and install only `novos-custom-tools` (remove `novos-slippery-tiles` and `/usr/local/bin/novos-gtile-overlay`).
5. In `scripts/stages/06-snapshot-finalize.sh`:
   - Remove `check_file /usr/local/bin/novos-slippery-tiles`.

[POST_GATE]
bash -n scripts/build-novos.sh scripts/stages/02-bootstrap-chroot.sh scripts/stages/04-cosmic-desktop.sh scripts/stages/05-profiles-installer.sh scripts/stages/06-snapshot-finalize.sh && ! grep -E "slippery-tiles|gtile-overlay" scripts/build-novos.sh scripts/stages/02-bootstrap-chroot.sh scripts/stages/04-cosmic-desktop.sh scripts/stages/05-profiles-installer.sh scripts/stages/06-snapshot-finalize.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 6D: Remove `novos-slippery-tiles` from Nebulous Manifests, Tracker & Verification Gates (`verify-deps.sh`, `nebulous-ctl.py`)

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 6D: Remove novos-slippery-tiles from Nebulous Manifests, Tracker & Verification Gates (verify-deps.sh, nebulous-ctl.py)

[SCOPE_LOCK]
ALLOWED_FILES:
  - .nebulous/manifest.toml
  - .nebulous/golden-snapshot.toml
  - .tracker/project.toml
  - .tracker/phases/01_core_engine/phase.toml
  - .tracker/phases/01_core_engine/domains/novos_slippery_tiles/scope.toml
  - scripts/nebulous-ctl.py
  - scripts/lib/verify-deps.sh
READ_ONLY_REFS:
  - custom-tools/manifest.yaml
  - Cargo.toml
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
! grep -E "slippery-tiles|gtile-overlay" scripts/build-novos.sh scripts/stages/*.sh

[MUTATION_SPEC]
1. In `.nebulous/manifest.toml`:
   - Remove `"/usr/local/bin/novos-slippery-tiles"` and `"/usr/local/bin/novos-gtile-overlay"` from `[custom_binaries]`.
2. In `.nebulous/golden-snapshot.toml`:
   - Remove `novos-slippery-tiles` and `novos-gtile-overlay` from `[custom_binaries]`.
3. In `.tracker/project.toml` and `.tracker/phases/01_core_engine/phase.toml`:
   - Remove the `novos_slippery_tiles` component and scope entries, and delete `.tracker/phases/01_core_engine/domains/novos_slippery_tiles/scope.toml`.
4. In `scripts/nebulous-ctl.py`:
   - In `verify_repo_invariants()`: validate that `novos-custom-tools` is present and `enabled: true` in `custom-tools/manifest.yaml` (replacing `novos-slippery-tiles`).
   - In `NebulousCtlSelfTests.test_asymmetric_variance_upgrade_downgrade_and_removal()`: replace `novos-slippery-tiles` test fixture entries with `novos-custom-tools`.
5. In `scripts/lib/verify-deps.sh`:
   - Remove `novos-slippery-tiles` and `/usr/local/bin/novos-slippery-tiles` from `verify_stage4_post()`, `verify_stage5_post()`, and `verify_stage6_post()`.

[POST_GATE]
bash -n scripts/lib/verify-deps.sh && python3 -m py_compile scripts/nebulous-ctl.py && ./scripts/nebulous-ctl.py --self-test && ./scripts/nebulous-ctl.py verify-variance --repo-only && ! grep -E "slippery-tiles|gtile-overlay" .nebulous/manifest.toml .nebulous/golden-snapshot.toml .tracker/project.toml .tracker/phases/01_core_engine/phase.toml scripts/nebulous-ctl.py scripts/lib/verify-deps.sh

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 6E: Configure Containerized QEMU (`/srv/docker-vms`) & `llama.cpp` + `Qwen3-14B-Q4_K_M` LLM Host (`/srv/docker-llms`) in Docker CI

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 6E: Configure Containerized QEMU (/srv/docker-vms) & llama.cpp + Qwen3-14B-Q4_K_M LLM Host (/srv/docker-llms) in Docker CI

[SCOPE_LOCK]
ALLOWED_FILES:
  - docker/Dockerfile.qemu-tester
  - docker/docker-compose.ci.yml
  - scripts/setup-popos-test-env.sh
READ_ONLY_REFS:
  - docker/Dockerfile.builder
  - config/build.env
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
./scripts/nebulous-ctl.py verify-variance --repo-only

[MUTATION_SPEC]
1. Update `docker/Dockerfile.qemu-tester`:
   - Install `qemu-system-x86`, `qemu-utils`, `ovmf`, `swtpm`, `swtpm-tools`, `socat`, `openssh-client`, `sshpass`, `novnc`, `websockify`, `python3`, `python3-yaml`, `python3-pil`, `xorriso`, `squashfs-tools`, `iproute2`, `procps`, `curl`, and `ca-certificates`.
   - Set environment defaults:
     - `NOVOS_VM_STORAGE_DIR=/vms`
     - `NOVOS_LLM_ENDPOINT=http://novos-llm-host:8080/v1`
     - `NOVOS_LLM_MODEL=Qwen3-14B-Q4_K_M.gguf`
   - Create `/vms` directory inside image so container mounts cleanly.
2. Update `docker/docker-compose.ci.yml`:
   - Configure `novos-builder` as before (`privileged: true`, `/workspace`, `/cache/apt`, `/cache/cargo`).
   - Configure `novos-qemu-tester`:
     - `devices`: `["/dev/kvm:/dev/kvm", "/dev/dri:/dev/dri"]`
     - `volumes`: `["..:/workspace", "${NOVOS_VM_HOST_DIR:-/srv/docker-vms}:/vms"]`
     - `environment`: `NOVOS_VM_STORAGE_DIR=/vms`, `NOVOS_LLM_ENDPOINT=${NOVOS_LLM_ENDPOINT:-http://novos-llm-host:8080/v1}`, `NOVOS_LLM_MODEL=${NOVOS_LLM_MODEL:-Qwen3-14B-Q4_K_M.gguf}`
     - `ports`: `["127.0.0.1:6080:6080", "127.0.0.1:5900:5900"]`
   - Add service `novos-llm-host`:
     - `image: ghcr.io/ggml-org/llama.cpp:server-vulkan`
     - `container_name: novos-llm-host`
     - `restart: unless-stopped`
     - `devices`: `["/dev/dri:/dev/dri", "/dev/kfd:/dev/kfd"]`
     - `group_add`: `["video", "render"]`
     - `ipc: host`
     - `ports`: `["127.0.0.1:8081:8080"]`
     - `volumes`: `["${NOVOS_LLM_HOST_DIR:-/srv/docker-llms}:/models"]`
     - `environment`: `HSA_OVERRIDE_GFX_VERSION=12.0.1`, `GGML_VK_VISIBLE_DEVICES=0`
     - `command`:
       ```yaml
       [
         "-m", "/models/${NOVOS_LLM_MODEL:-Qwen3-14B-Q4_K_M.gguf}",
         "--host", "0.0.0.0",
         "--port", "8080",
         "-ngl", "99",
         "-c", "16384",
         "--cache-type-k", "q8_0",
         "--cache-type-v", "q8_0",
         "--flash-attn"
       ]
       ```
3. Create `scripts/setup-popos-test-env.sh` (`chmod +x`):
   - Supports `--llm-dir <dir>` (default `/srv/docker-llms`), `--vm-dir <dir>` (default `/srv/docker-vms`), `--model-url <url>` (default Hugging Face `bartowski/Qwen_Qwen3-14B-GGUF/resolve/main/Qwen_Qwen3-14B-Q4_K_M.gguf` saved as `Qwen3-14B-Q4_K_M.gguf`), `--pull-model`, `--start-llm`, `--status`, `--dry-run`, and `--self-test`.
   - Verifies `/dev/kvm`, `/dev/dri`, `/dev/kfd`, Docker Compose, `/srv/docker-vms`, and `/srv/docker-llms`, and provides a single command to download the quantized Qwen3-14B GGUF model and bring up `novos-llm-host` and `novos-qemu-tester`.

[POST_GATE]
bash -n scripts/setup-popos-test-env.sh && ./scripts/setup-popos-test-env.sh --self-test && ./scripts/setup-popos-test-env.sh --dry-run && python3 -c '
import yaml
dc = yaml.safe_load(open("docker/docker-compose.ci.yml", "r", encoding="utf-8"))
assert "novos-builder" in dc["services"]
assert "novos-qemu-tester" in dc["services"]
assert "novos-llm-host" in dc["services"]
assert any("/srv/docker-vms:/vms" in v for v in dc["services"]["novos-qemu-tester"]["volumes"])
assert any("/srv/docker-llms:/models" in v for v in dc["services"]["novos-llm-host"]["volumes"])
print("STEP 6E POST_GATE OK")
'

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 6F: Integrate `/srv/docker-vms` Storage & Local `Qwen3-14B` Self-Healing Diagnostics into `test-docker-qemu.py`, `test-qemu.sh` & `mcp_nebulous_ci.py`

* **Recommended Command:** `/boost`

```text
[STEP_ID]
STEP 6F: Integrate /srv/docker-vms Storage & Local Qwen3-14B Self-Healing Diagnostics into test-docker-qemu.py, test-qemu.sh & mcp_nebulous_ci.py

[SCOPE_LOCK]
ALLOWED_FILES:
  - scripts/test-docker-qemu.py
  - scripts/test-qemu.sh
  - scripts/mcp_nebulous_ci.py
READ_ONLY_REFS:
  - docker/docker-compose.ci.yml
  - scripts/nebulous-ctl.py
  - config/build.env
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
test -x scripts/setup-popos-test-env.sh && ./scripts/nebulous-ctl.py --self-test

[MUTATION_SPEC]
1. In `scripts/test-docker-qemu.py`:
   - Remove all references to `/usr/local/bin/novos-slippery-tiles --self-test` in docstrings, `build_milestone_plan()`, `verify_live_guest_invariants()`, and self-tests.
   - Add `resolve_vm_storage_dir(explicit_dir: str | None = None) -> pathlib.Path`:
     - Prefers `--vm-dir`, then `NOVOS_VM_STORAGE_DIR` (e.g., `/vms` in container), then `/srv/docker-vms` (when present and writable on host), falling back to `REPO_ROOT / "build"`.
     - Places default `novos-calamares-target.img` and `ovmf_vars.fd` inside the resolved VM storage directory so container and host runs share `/srv/docker-vms`.
   - Add `--vm-dir` and `--llm-endpoint` CLI arguments.
   - Add `query_local_llm_diagnostic(error_summary: str, serial_tail: str, negative_constraints: list[str], endpoint: str) -> dict[str, Any]`:
     - Uses standard library `urllib.request` to POST a bounded diagnostic prompt to `<endpoint>/chat/completions` (`model: "Qwen3-14B-Q4_K_M.gguf"`, `temperature: 0.1`, `response_format: {"type": "json_object"}`) requesting `{"root_cause": "...", "subsystem": "...", "recommended_fix": "...", "violates_negative_constraints": false}`.
     - Gracefully returns `{"available": False, ...}` if the local LLM container is offline so tests never crash when run without the LLM server.
   - Expand `--self-test` to unit-test `resolve_vm_storage_dir()` and `query_local_llm_diagnostic()` (using a local ephemeral HTTP test server).

2. In `scripts/test-qemu.sh`:
   - Add `--vm-dir <dir>` flag and resolve `VM_STORAGE_DIR`: if `/srv/docker-vms` exists and is writable, default `TARGET_DISK="/srv/docker-vms/novos-calamares-target.img"` and `VARS_DIR="/srv/docker-vms"`, else `${REPO_ROOT}/build`.
   - Export `NOVOS_VM_HOST_DIR="${VM_STORAGE_DIR}"` when invoking `docker compose -f docker/docker-compose.ci.yml`.
   - Prioritize `apt-get` over `pacman` for Debian/Pop!_OS hosts when checking host QEMU packages.

3. In `scripts/mcp_nebulous_ci.py`:
   - Add a 6th typed MCP tool `nebulous_consult_local_llm`:
     - Parameters: `item_id` (required string), `error_summary` (required string), `log_file` (optional string), `proposed_files` (optional array of strings), `llm_endpoint` (optional string, default `NOVOS_LLM_ENDPOINT` or `http://127.0.0.1:8081/v1`).
     - Loads active `negative_constraints` from `Item.toml`, reads the tail of `log_file` (if provided), queries the hosted `llama.cpp` `Qwen3-14B` server (`/v1/chat/completions`), records the resulting diagnostic hypothesis into `Item.toml` (`[[diagnostics]]`), and returns the structured diagnosis alongside active `NEGATIVE_CONSTRAINTS`.
   - Expand `--self-test` in `scripts/mcp_nebulous_ci.py` to test `nebulous_consult_local_llm` with a mock HTTP server.

[POST_GATE]
bash -n scripts/test-qemu.sh && python3 -m py_compile scripts/test-docker-qemu.py scripts/mcp_nebulous_ci.py && ./scripts/test-docker-qemu.py --self-test && ./scripts/test-docker-qemu.py --dry-run --tier live-boot && ./scripts/mcp_nebulous_ci.py --self-test && ! grep -q "slippery-tiles" scripts/test-docker-qemu.py scripts/test-qemu.sh scripts/mcp_nebulous_ci.py

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```

---

### STEP 6G: Update Documentation (`docs/BUILD_AND_TEST.md`, `docs/PROFILES.md`) & Finalize Self-Test Gates (`scripts/lib/verify-deps.sh`)

* **Recommended Command:** `/goal`

```text
[STEP_ID]
STEP 6G: Update Documentation (docs/BUILD_AND_TEST.md, docs/PROFILES.md) & Finalize Self-Test Gates (scripts/lib/verify-deps.sh)

[SCOPE_LOCK]
ALLOWED_FILES:
  - docs/BUILD_AND_TEST.md
  - docs/PROFILES.md
  - scripts/lib/verify-deps.sh
READ_ONLY_REFS:
  - scripts/setup-popos-test-env.sh
  - scripts/export-slippery-tiles.sh
  - docker/docker-compose.ci.yml
FORBIDDEN_SCOPE:
  - Any file not listed in ALLOWED_FILES.

[PRE_GATE]
./scripts/test-docker-qemu.py --self-test && ./scripts/mcp_nebulous_ci.py --self-test && ./scripts/setup-popos-test-env.sh --self-test

[MUTATION_SPEC]
1. In `docs/PROFILES.md`:
   - Update Section 5 (`Declarative Custom Tools (novos-custom-tools)`):
     - Remove `novos-slippery-tiles` and `novos-gtile-overlay` from the active distro documentation.
     - Note that `slippery-tiles` has been branched off into its own standalone project repository (`/srv/devshare/slippery-tiles`) and can be re-integrated later via `custom-tools/manifest.yaml` once its standalone implementation is finalized.
2. In `docs/BUILD_AND_TEST.md`:
   - Update Section 7 (`Dockerized Clean-Room Build, Containerized QEMU (/srv/docker-vms) & Local LLM Host (/srv/docker-llms)`):
     - Document the Pop!_OS directory mounts: `/srv/docker-vms` (persistent 64 GiB VirtIO target disks, OVMF NVRAM vars, and QMP artifacts) and `/srv/docker-llms` (`llama.cpp` Vulkan/ROCm server hosting `Qwen3-14B-Q4_K_M.gguf` on port `8081`).
     - Document `scripts/setup-popos-test-env.sh --pull-model` and `scripts/setup-popos-test-env.sh --start-llm`.
   - Update Section 8.3 (`Stdio MCP Server`) to document the 6th tool `nebulous_consult_local_llm`.
3. In `scripts/lib/verify-deps.sh`:
   - In `run_self_test()`:
     - Add `scripts/setup-popos-test-env.sh --self-test` and `scripts/export-slippery-tiles.sh --self-test`.
     - Add Rust workspace checks (`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace`) when `cargo` is available.

[POST_GATE]
bash -n scripts/lib/verify-deps.sh && ./scripts/lib/verify-deps.sh --self-test && ! grep -q "novos-slippery-tiles" docs/PROFILES.md docs/BUILD_AND_TEST.md && grep -q "/srv/docker-llms" docs/BUILD_AND_TEST.md && grep -q "/srv/docker-vms" docs/BUILD_AND_TEST.md

[HALT_INVARIANT]
Halt immediately after POST_GATE exits 0 and report verification status.
```
