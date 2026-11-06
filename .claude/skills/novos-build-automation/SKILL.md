---
name: novos-build-automation
description: Load when writing or changing NovOS build pipeline code (scripts/build-novos.sh, scripts/stages/01–06, scripts/lib), ISO/squashfs/disk-image generation, or QEMU test harnesses (scripts/test-qemu.sh, scripts/test-docker-qemu.py).
---

# NovOS Deterministic Scripting & Automation

Source: former Skills.md §3.2 (retired in STEP 8F); provenance in the CLAUDE.md Parity Table.

## Build Pipeline Orchestration
- Do not issue long chained bash commands directly in the agent loop. Encode work in modular scripts, Makefiles, Nix derivations, or containerized Podman/Docker recipes.
- Each stage (toolchain validation, source extraction, kernel compilation, rootfs staging, image generation) is an isolated, idempotent target that fails immediately on an unhandled exit code (`set -euo pipefail`).
- **No error swallowing:** `|| true` (or equivalent) is forbidden unless the same line carries an inline `# novos-allow: <reason>` annotation. `.claude/hooks/guard-diff.sh` enforces this.

### NovOS stage map (`scripts/stages/`)
| Stage | Script | Purpose |
|---|---|---|
| 1 | `01-partition-mount.sh` | Prepare directory-backed rootfs staging tree (clean-room ISO builder) |
| 2 | `02-bootstrap-chroot.sh` | Host-agnostic Debian sid bootstrap, live/Calamares fstab |
| 3 | `03-core-system.sh` | (chroot) Unified sid base, APT hardening, PAM, kernels, dracut, Limine |
| 4 | `04-cosmic-desktop.sh` | (chroot) COSMIC desktop + graphical stack |
| 5 | `05-profiles-installer.sh` | (chroot) Profile provisioner (`novos-setup`), Calamares |
| 6 | `06-snapshot-finalize.sh` | (host/clean namespace) Verification, initramfs, squashfs + ISO |

- Every build is fresh, Stages 1→6. `--from-stage`/partial resumes do not exist and must not be reintroduced (CLAUDE.md §1).
- Orchestrator: `scripts/build-novos.sh` — run by the user only: `sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso`.

## Filesystem & ISO Generation
- ISO packaging with `xorriso`, `genisoimage`, `live-build`, `mksquashfs`.
- For raw disk images (`.raw`, `.qcow2`) avoid `losetup`/loop mounts (needs elevated rights): use user-space tooling — `guestfish` (libguestfs) or `e2tools` — to format partitions, copy staged filesystems, and install bootloader binaries.

## Automated Testing & QEMU/KVM Harnessing
- Verify bootability without human intervention: wrap headless QEMU (`qemu-system-x86_64 -m 2048 -nographic -serial stdio -drive file=disk.img,format=raw`) in a Python harness (`pexpect` or `asyncio`).
- Monitor the serial stream against milestone regexes (kernel init strings, systemd service startup, login prompt); kill hanging VMs with bounded timeouts; capture framebuffer snapshots via the QEMU monitor (`screendump`) for visual validation.

### NovOS harnesses
- `scripts/test-qemu.sh` — UEFI (OVMF) live-ISO boot with VirtIO GPU. Standard verification:
  `./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars`
  Other flags: `--vm-dir`, `--disk`, `--disk-size`, `--gl`, `--ram`, `--cpus`.
- `scripts/test-docker-qemu.py` — two-tier automated harness (Tier 1 `live-boot`: Limine handoff, serial/SSH milestone, `/etc/os-release` `ID=novos`, `novos-custom-tools --self-test`; Tier 2: install). Flags: `--iso`, `--tier`, `--vnc`/`--no-vnc`, `--item`, `--timeout`, `--ram`. Artifacts land in `logs/qemu-artifacts/`.
- Prefer the typed MCP route (`nebulous_ci` `nebulous_run_qemu_test` / `nebulous_run_docker_build`) when available (CLAUDE.md §3). VM tests are user-run unless explicitly requested.
