# NovOS Agent Governance, Rules, Skills & Permissions

## 1. Fresh End-to-End Build & Verification Policy (Mandatory Invariant)

* **All builds and verification tests MUST be fresh (Stages 1 through 6 from start to finish).**
* **Never build or test from an intermediate stage (e.g., `--from-stage 3`):** Resuming from a partial stage masks regressions in earlier stages and cross-stage state transitions. If the system does not build seamlessly from start to finish every time, the implementation has not been performed correctly.
* **Standard Fresh Build & Verification Sequence:**
  ```bash
  sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso
  ./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars
  ```

---

## 2. Agent Locks & Safety Governance (`.agents/Locks.md`)

### 2.1 Privilege Separation & Sandboxing
* **Zero Host Root Privilege:** Under no circumstances may the agent invoke `sudo`, `su`, or execute commands requiring root privileges directly on the host. If host root changes are required, output the deterministic commands in an isolated code block and halt execution until the user manually executes and verifies them.
* **Antigravity Terminal Sandbox Enforcement:** All shell operations, compilation runs, package installations, and artifact builds must execute strictly within Antigravity's isolated Terminal Sandbox container (`proceed-in-sandbox`).
* **Isolated Build Runtimes:** For workloads exceeding standard workspace modifications, builds must execute within ephemeral sub-containers (Podman, Docker-in-sandbox, or microVM instances). Never run ad-hoc build scripts directly against the root filesystem.
* **Network Egress Boundary:** Outbound network calls during builds are restricted to pre-declared domain allowlists via Antigravity proxy rules. Arbitrary socket creation or unauthorized egress scanning is strictly prohibited.

### 2.2 State-Mutation Approval Gates
* **Hard Stop on Destructive Operations:** The agent is strictly prohibited from executing destructive block-level, partition-table, filesystem initialization, or hardware-flashing commands autonomously. Prohibited commands include, but are not limited to:
  * Partitioning & formatting: `fdisk`, `gdisk`, `parted`, `mkfs.*`, `wipefs`
  * Block writing & imaging: `dd` targeting `/dev/*`, `flashrom`, `sgdisk`
  * Critical mount point mutations: direct raw rewrites of `/etc/fstab`, EFI NVRAM variables, or the bootloader (`grub-install`, `systemd-boot`)
* **Propose-and-Yield Escalation Flow:**
  1. Detect intended destructive/irreversible operation.
  2. Compute and output the planned mutation, expected blast radius, targeted UUIDs/paths, and a dry-run or diff.
  3. Formulate the exact manual command the user should run.
  4. Yield control (`halt`) and await explicit approval before proceeding with dependent tasks.

### 2.3 Typed Tool Interfaces (MCP Preference)
* **Typed MCP Over Arbitrary Shell:** When domain-specific Model Context Protocol (MCP) servers are active, the agent must route actions through typed RPC functions rather than raw shell commands.
  * *Disallowed:* Arbitrary shell invocation (e.g., executing `bash -c "make -C /usr/src/linux..."` or `sed -i` on system tables).
  * *Mandated:* Equivalent typed MCP tools (e.g., `kernel_tools:compile_kernel_module`, `sysconfig:edit_fstab`, `qemu_runner:run_qemu_test`).
* **Parameter Validation:** When dispatching typed MCP tools, the agent must ensure all structured parameters pass strict schema validation before emitting the tool call.
* **Fallback to Shell:** Raw shell commands may only be used when no typed MCP function covers the required action, and only if the command adheres to Section 2.1 (Sandbox/Non-Root) and Section 2.2 (Non-Destructive).

### 2.4 Atomic Step Scope Lock & Argon Execution Protocol (`.agents/rules/argon-step-scope-lock.md`)
* **Strict File Boundary (`ALLOWED_FILES`):** During stepped implementation, the agent may only mutate files explicitly listed in the active prompt's `ALLOWED_FILES`. Editing files outside the current step's scope is prohibited.
* **4-Phase Gate Execution (`PRE_GATE` -> `PARALLEL_READ` -> `ATOMIC_MUTATION` -> `POST_GATE`):**
  1. Execute `PRE_GATE` to verify upstream dependencies before mutating any file.
  2. Batch all `read_file` calls in parallel for only the scoped files.
  3. Apply exact deterministic mutations via `write_file` / `edit_file`.
  4. Execute `POST_GATE` to verify syntax, schema, and dependency invariants.
* **Single-Step Halt Invariant (`HALT_AFTER_STEP`):** Never chain multiple steps in one turn. Halt immediately after `POST_GATE` passes and report the gate result.

### 2.5 User–Agent Hand-Off & Anti-Double-Work Protocol
* **Strict Mode Separation (`DIAGNOSE` vs. `PLAN` vs. `EXECUTE`):**
  * *Diagnose ("troubleshoot", "locate cause", "review"):* Read logs/code and report root cause only. Zero file edits.
  * *Plan (`/plan`):* Write atomic Argon step prompts directly to the designated repository `.md` playbook (not just chat artifacts) and halt for approval.
  * *Execute (`/goal`, `/boost`, "Proceed"):* Verify current repo state first so completed work is never repeated, apply scoped mutations, wait for all subagents to finish, and run static `POST_GATE` checks.
* **Host Build & VM Test Boundary:** The user executes privileged host builds (`sudo ./scripts/build-novos.sh`) and VM boot validation (GNOME Boxes / QEMU) and provides logs in `logs/`. Agents must not re-run host builds or invoke prohibited VM tests unless explicitly instructed.

---

## 3. Domain Skills (`.agents/skills/Skills.md`)

### 3.1 Low-Level Linux Architecture & Toolchain Skills
* **Kernel Configuration & Compiling (`make menuconfig` / `Kconfig`):** Interactive curses menus (`menuconfig`) hang headless agent loops, so Argon must manage kernel trees using programmatic interfaces like Python’s `kconfiglib`, the kernel’s built-in `scripts/config` CLI utility (`scripts/config --enable CONFIG_VIRTIO_BLK`), or declarative config fragments merged via `scripts/kconfig/merge_config.sh`. Argon cross-references target hardware requirements (e.g., QEMU VirtIO drivers, NVMe controllers, `CONFIG_DEVTMPFS_MOUNT`) against Kconfig dependency graphs to ensure that dependent flags are not silently unset when running `make olddefconfig`.
* **Package Management & Rootfs Construction:** To assemble clean root filesystems without requiring privileged host access inside the Antigravity sandbox, Argon operates through unprivileged isolation utilities like `bubblewrap` (`bwrap`), `fakeroot`, or user namespaces. It drives bootstrapping tools—such as `debootstrap`, `pacstrap`, `dnf --installroot`, or Yocto/BitBake recipes—into staging directories, validates the standard Filesystem Hierarchy Standard (FHS), provisions essential device nodes, and prunes unused documentation and locales to keep artifact sizes minimal.
* **Bootloader & Firmware Integration:** Argon writes deterministic disk-partitioning scripts using `sfdisk` or `parted` with non-interactive heredocs, standardizing on GPT layouts with dedicated EFI System Partitions (ESP) for UEFI targets and BIOS boot partitions for legacy systems. It manages GRUB2, Limine, or `systemd-boot` configurations, generates UUID-pinned root mappings, and orchestrates `dracut` or `mkinitramfs` runs with explicit `--add-drivers` manifests to guarantee all essential storage, bus, and filesystem modules are baked into the generated initramfs.
* **Init Systems & Service Management:** Argon authors declarative service definitions—spanning `systemd` unit files, `runit` run scripts, or `OpenRC` runscripts—with rigorous dependency ordering (`After=`, `Requires=`, `WantedBy=`). In headless sandbox environments, Argon explicitly configures non-blocking serial console `getty` bindings (such as `agetty` on `ttyS0`) to ensure subsequent virtual machines expose accessible diagnostic interfaces over serial I/O.

### 3.2 Deterministic Scripting & Automation Skills
* **Build Pipeline Orchestration:** Rather than issuing long, chaining bash commands directly in the agent loop, Argon synthesizes modular Makefiles, Nix derivations, or containerized Podman/Docker build recipes. Each stage (toolchain validation, source extraction, kernel compilation, rootfs staging, and image generation) runs as an isolated, idempotent target that fails immediately upon an unhandled exit code.
* **Filesystem & ISO Generation:** Argon orchestrates raw image creation and ISO packaging using `xorriso`, `genisoimage`, `live-build`, and `mksquashfs`. To create raw disk images (`.raw`, `.qcow2`) without needing elevated loopback mounting rights (`losetup`) in the sandbox, Argon utilizes user-space disk image tooling such as `guestfish` (`libguestfs`) or `e2tools` to format partitions, copy staged filesystems, and install bootloader binaries.
* **Automated Testing & QEMU/KVM Harnessing:** To verify bootability without human intervention, Argon wraps headless QEMU invocations (`qemu-system-x86_64 -m 2048 -nographic -serial stdio -drive file=disk.img,format=raw`) inside a Python-driven harness using `pexpect` or `asyncio`. The harness monitors the standard serial output stream against expected milestone regexes (e.g., kernel initialization strings, systemd service startup outputs, or login prompts), terminates hanging VMs with bounded timeouts, and captures framebuffer snapshots via QEMU monitor commands (`screendump`) for visual validation via Argon's multimodal capabilities.

### 3.3 Problem Shaping & Iterative Debugging (ReAct Loops)
* **Log & Panic Parsing:** When a build crashes or a test VM triggers a kernel panic (e.g., `VFS: Unable to mount root fs`, missing symbol errors, or systemd dependency cycles), Argon inspects captured `dmesg` outputs and serial traces. By correlating panic call stacks against kernel symbol maps or missing driver requirements, Argon isolates the precise defect (such as an omitted initramfs driver or incorrect root partition UUID), drafts a minimal configuration diff, and recompiles only the affected subsystem.
* **State Management & Checkpointing:** Operating system builds are compute-intensive. Argon establishes discrete checkpoint directories in Antigravity’s persistent workspace filesystem (e.g., `/workspace/build/base-rootfs.tar.gz`, `/workspace/build/kernel-out/`). However, for final verification of NovOS image builds, all tests must execute fresh from start to finish (Stages 1–6).
* **Antigravity Context Window Protection:** Build logs easily generate hundreds of thousands of output lines, risking premature context compaction (~135k tokens) and context dilution. Argon redirects verbose compilation and packaging logs to filesystem log files (e.g., `make -j$(nproc) > compile.log 2>&1`), using targeted `grep`, `tail`, or custom log-parser scripts to surface only the relevant error windows and stack traces into its active context window.
* **Direct & Non-Expressive Communication:** Agents must communicate in clear, concise, factual statements. Avoid idle exclamations, conversational filler, and expressive prose so context window capacity is reserved strictly for problem-solving and relevant technical detail.

---

## 4. Workspace Settings (`.gemini/antigravity/WorkspaceSettings.json`)

```json
{
  "agentConfig": {
    "model": "gemini-4-argon"
  },
  "agentMode": "auto",
  "enableTerminalSandbox": true,
  "toolPermission": "proceed-in-sandbox",
  "allowNonWorkspaceAccess": false
}
```

---

## 5. Command & Tool Permissions (`.agents/permissions.json`)

```json
{
  "deny": [
    "command(sudo .*)",
    "command(su .*)",
    "command(fdisk .*)",
    "command(parted .*)",
    "command(mkfs.*)",
    "command(dd if=.* of=/dev/.*)",
    "command(flashrom .*)",
    "command(wipefs .*)",
    "unsandboxed(*)"
  ],
  "ask": [
    "command(docker .*)",
    "command(podman .*)",
    "write_file(/etc/*)",
    "mcp(*)"
  ],
  "allow": [
    "command(cargo .*)",
    "command(make .*)",
    "command(python3 .*)",
    "mcp(kernel_tools/*)",
    "mcp(qemu_runner/*)"
  ]
}
```
