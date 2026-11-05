
---
trigger: always_on
description: "Core safety, sandboxing, and execution locks enforced across all agentic turns."
---

# Agent Locks & Safety Governance

## 1. Privilege Separation & Sandboxing
* **Zero Host Root Privilege:** Under no circumstances may the agent invoke `sudo`, `su`, or execute commands requiring root privileges directly on the host. If host root changes are required, output the deterministic commands in an isolated code block and halt execution until the user manually executes and verifies them.
* **Antigravity Terminal Sandbox Enforcement:** All shell operations, compilation runs, package installations, and artifact builds must execute strictly within Antigravity's isolated Terminal Sandbox container (`proceed-in-sandbox`).
* **Isolated Build Runtimes:** For workloads exceeding standard workspace modifications, builds must execute within ephemeral sub-containers (Podman, Docker-in-sandbox, or microVM instances). Never run ad-hoc build scripts directly against the root filesystem.
* **Network Egress Boundary:** Outbound network calls during builds are restricted to pre-declared domain allowlists via Antigravity proxy rules. Arbitrary socket creation or unauthorized egress scanning is strictly prohibited.

## 2. State-Mutation Approval Gates
* **Hard Stop on Destructive Operations:** The agent is strictly prohibited from executing destructive block-level, partition-table, filesystem initialization, or hardware-flashing commands autonomously. Prohibited commands include, but are not limited to:
  * Partitioning & formatting: `fdisk`, `gdisk`, `parted`, `mkfs.*`, `wipefs`
  * Block writing & imaging: `dd` targeting `/dev/*`, `flashrom`, `sgdisk`
  * Critical mount point mutations: direct raw rewrites of `/etc/fstab`, EFI NVRAM variables, or the bootloader (`grub-install`, `systemd-boot`)
* **Propose-and-Yield Escalation Flow:**
  1. Detect intended destructive/irreversible operation.
  2. Compute and output the planned mutation, expected blast radius, targeted UUIDs/paths, and a dry-run or diff.
  3. Formulate the exact manual command the user should run.
  4. Yield control (`halt`) and await explicit approval before proceeding with dependent tasks.

## 3. Typed Tool Interfaces (MCP Preference)
* **Typed MCP Over Arbitrary Shell:** When domain-specific Model Context Protocol (MCP) servers are active, the agent must route actions through typed RPC functions rather than raw shell commands.
  * *Disallowed:* Arbitrary shell invocation (e.g., executing `bash -c "make -C /usr/src/linux..."` or `sed -i` on system tables).
  * *Mandated:* Equivalent typed MCP tools (e.g., `kernel_tools:compile_kernel_module`, `sysconfig:edit_fstab`, `qemu_runner:run_qemu_test`).
* **Parameter Validation:** When dispatching typed MCP tools, the agent must ensure all structured parameters pass strict schema validation before emitting the tool call.
* **Fallback to Shell:** Raw shell commands may only be used when no typed MCP function covers the required action, and only if the command adheres to Section 1 (Sandbox/Non-Root) and Section 2 (Non-Destructive).

## 4. Fresh End-to-End Build Verification Lock
* **Fresh Builds Only:** All builds and verification tests must run fresh from start to finish (Stages 1–6). Never recommend or run intermediate stage resumes (e.g., `--from-stage 3`) to verify fixes. If the system does not build seamlessly from start to finish every time, the implementation has not been performed correctly.

## 5. Atomic Step Scope & Halt Lock
* **Scope Lock (`ALLOWED_FILES`):** Never create, edit, or delete any file outside the active prompt's declared `ALLOWED_FILES`.
* **Dependency Gate Lock (`PRE_GATE` / `POST_GATE`):** Never mutate files if `PRE_GATE` fails, and never mark a step complete until `POST_GATE` exits `0`.
* **Single-Step Halt (`HALT_AFTER_STEP`):** Stop immediately after the active step's `POST_GATE` succeeds; never advance to the next step unprompted.
