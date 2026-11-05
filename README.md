<div align="center">

<img src="branding/novos-logo.svg" alt="NovOS Supernova Emblem" width="148" height="148" />

# NovOS 2026.10 ("Nova")

**Bleeding-Edge Debian Unstable (`sid`) · XanMod `x64v3` + Mesa 26+ RDNA 4 · COSMIC Wayland Desktop · 10-Subvolume Anti-Bloat Btrfs + Limine Snapshot Rollbacks**

[![Distro: NovOS 2026.10 Nova](https://img.shields.io/badge/NovOS-2026.10%20Nova-FF6B1A?style=for-the-badge&labelColor=0B0C10)](#)
[![Base: Debian Unstable (sid)](https://img.shields.io/badge/Base-Debian%20Sid-D70A53?style=for-the-badge&logo=debian&logoColor=FFF3E0&labelColor=0B0C10)](docs/ARCHITECTURE.md)
[![Kernel: XanMod x64v3 + LTS Fallback](https://img.shields.io/badge/Kernel-XanMod%20x64v3-FF9E44?style=for-the-badge&logo=linux&logoColor=FFF3E0&labelColor=0B0C10)](docs/ARCHITECTURE.md#dual-kernel--version-archived-snapshot-architecture)
[![Desktop: COSMIC Epoch Wayland](https://img.shields.io/badge/Desktop-COSMIC%20Wayland-FFB347?style=for-the-badge&labelColor=0B0C10)](docs/ARCHITECTURE.md#display-manager--cosmic-desktop-stack)
[![Filesystem: 10-Subvol Btrfs + Limine v8](https://img.shields.io/badge/Storage-Btrfs%20%2B%20Limine%20v8-5294E2?style=for-the-badge&labelColor=0B0C10)](docs/ARCHITECTURE.md#anti-bloat-10-subvolume-btrfs-layout)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL--3.0--or--later-8EC07C?style=for-the-badge&labelColor=0B0C10)](#license)

<br />

<img src="branding/nova-wallpaper.svg" alt="NovOS 2026.10 Nova Desktop Wallpaper" width="100%" />

<br />

[**System Architecture**](docs/ARCHITECTURE.md) · [**Build & QEMU Verification**](docs/BUILD_AND_TEST.md) · [**Modular Profiles (`novos-setup`)**](docs/PROFILES.md) · [**Branding & Nova Theme**](docs/BRANDING_AND_THEME.md) · [**Agent Governance (`AGENTS.md`)**](AGENTS.md)

</div>

---

## Overview & Core Philosophy

**NovOS** is a clean-room, declaratively built Linux distribution engineered for modern AMD Zen 5 (`9800X3D`) and RDNA 4 (`Radeon RX 9070 XT` / `gfx1201`) workstation hardware. It pairs the latest upstream packages from **Debian Unstable (`sid`)** with deterministic Btrfs snapshot rollbacks and a hybrid UEFI/BIOS **Calamares Live Installer ISO**.

1. **Performance > Stability — Counterbalanced by Atomic Btrfs Rollbacks:**
   Runs a unified **Debian Unstable (`sid`)** userland with the **XanMod (`linux-xanmod-x64v3`)** primary performance kernel, locked Debian fallback kernel (`linux-image-amd64`), and native **Mesa 26+ Vulkan/OpenCL (`gfx1201`)** graphics stack. Every APT transaction and Golden Master install state is protected by bootable read-write Btrfs snapshots synced directly into the **Limine v8** boot menu with version-paired kernels.
2. **Zero "Frankendebian" Release Mixing:**
   Bootstraps directly into unified Debian `sid` and enforces strict APT pin-priority locks (`Pin-Priority: 1000` in `/etc/apt/preferences.d/00-protect-core`) and `APT::NeverAutoRemove` guards (`/etc/apt/apt.conf.d/01-novos-safety`) so core session and package binaries (`systemd`, `libpam-systemd`, `dbus-user-session`, `apt`, `dracut`, `btrfs-progs`, `greetd`) can never be removed during dependency resolution.
3. **Anti-Bloat 10-Subvolume Btrfs Isolation:**
   Root snapshots (`/@`) remain compact (~3–5 GiB) and never capture Docker layers, containerd images, Flatpaks, APT caches, system logs, or multi-gigabyte LLM weights (`/srv/llm`).
4. **Lean Base Image + On-Demand Modular Profiles:**
   The base ISO installs the core OS, dual kernels, RDNA 4 graphics stack, and the **COSMIC Desktop Environment**. Heavy workload stacks (**Gaming**, **Dev & Google Antigravity**, **Local AI / ComfyUI / LLM**, and **Network & Samba**) are selectable during Calamares installation or anytime post-install via `sudo novos-setup`.

---

## System Specifications at a Glance

| Subsystem | Implementation | Details |
| :--- | :--- | :--- |
| **Distribution Identity** | `NovOS 2026.10 (Nova)` | `ID=novos`, `ID_LIKE=debian`, `ANSI_COLOR="38;2;255;107;26"` |
| **Base OS & Package Protection** | Unified Debian Unstable (`sid`) | `00-protect-core` (`Pin-Priority: 1000`) + `01-novos-safety` |
| **Bootloader** | Limine (`v8.x-binary`) | Dual UEFI (`BOOTX64.EFI` + `efibootmgr`) & Legacy BIOS (`limine-bios.sys` + `limine bios-install`) with Nova Dark canvas theme |
| **Kernels & Initramfs** | `linux-xanmod-x64v3` + `linux-image-amd64` | `dracut` (`zstd`, `hostonly="no"`) + `/etc/kernel/postinst.d/zz-sync-limine` versioned kernel archiving |
| **Root Filesystem** | Btrfs (`NOVOS_ROOT`) + 6 GiB FAT32 ESP (`NOVOS_EFI`) | 10 isolated subvolumes (`zstd:3`, `noatime`, `space_cache=v2`, `chattr +C` on `@docker`, `@containerd`, `@llm`) |
| **Snapshot Engine** | Snapper + `update-limine-snapshots` | Capped at `MAX_SNAPSHOTS=5` RW snapshots & `MAX_HISTORICAL_KERNELS=2` on ESP |
| **Desktop & Greeter** | COSMIC Epoch (`wayland`) + `cosmic-greeter` | Exclusively `systemd-logind` (`seatd` masked); automatic hardware EGL -> software GL -> `tuigreet` fallback |
| **Live ISO & Installer** | `live-boot` + Calamares (`novos` branding) | SquashFS (`zstd`) hybrid UEFI/BIOS ISO with custom `limine-bootloader` Python job module |

---

## Quick Start: Clean-Room Build & QEMU Verification

> [!IMPORTANT]
> **Mandatory Fresh Build Invariant:** All builds and verification tests must run fresh from Stage 1 through Stage 6 (`./scripts/build-novos.sh`). Never validate changes by resuming from an intermediate stage (`--from-stage`).

### 1. Build the Hybrid Live + Calamares Installer ISO
Run from a Linux host with `sudo` privileges:

```bash
sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso
```

### 2. Boot & Verify in QEMU/KVM (UEFI / OVMF)

```bash
./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars
```

* **Live Session Auto-Login:** Automatically logs into the COSMIC Wayland session as `devuser` (password: `novos`, root password: `novos`).
* **Calamares Installation:** Launch **Install NovOS 2026.10 (Nova)** from the desktop shortcut or application menu to partition the target disk with the 10-subvolume Btrfs layout, select optional software profiles, install Limine (BIOS + UEFI), and create the **Golden Master Base** snapshot (`Snapshot #1`).

See [**Build & QEMU Verification Guide (`docs/BUILD_AND_TEST.md`)**](docs/BUILD_AND_TEST.md) for host dependencies, configuration variables, and stage-by-stage verification gates.

---

## Anti-Bloat 10-Subvolume Btrfs Architecture

In Btrfs, snapshots of `@` (`/`) stop at subvolume boundaries. NovOS partitions target storage with a **FAT32 Boot/EFI System Partition (`NOVOS_EFI`)** and a **Btrfs Root Partition (`NOVOS_ROOT`)** divided into 10 dedicated subvolumes:

| Subvolume | Mountpoint | CoW | Purpose |
| :--- | :--- | :---: | :--- |
| `@` | `/` | Yes | Pure OS & system packages (Snapshotted by Snapper, capped at 5 RW snapshots) |
| `@home` | `/home` | Yes | User home directories (preserved across OS rollbacks) |
| `@snapshots` | `/.snapshots` | Yes | Snapper snapshot storage (`/@snapshots/<N>/snapshot`) |
| `@var_log` | `/var/log` | Yes | Persistent system & journal logs (survives root rollback for debugging) |
| `@var_cache` | `/var/cache` | Yes | APT & system package caches (excluded from root snapshots) |
| `@docker` | `/var/lib/docker` | No (`+C`) | Docker Engine storage |
| `@containerd` | `/var/lib/containerd` | No (`+C`) | Containerd image and layer storage |
| `@flatpak` | `/var/lib/flatpak` | Yes | System-wide Flatpak installations (Steam, Bottles, Heroic, runtimes) |
| `@llm` | `/srv/llm` | No (`+C`) | Local AI weights & workspaces (`ollama`, `huggingface`, `comfyui`, `open-webui`, `models`) |
| `@devshare` | `/srv/devshare` | Yes | Samba `[DevShare]` shared network workspace |

Full details on partition layout, BIOS/GPT dynamic boot partition provisioning, and snapshot kernel pairing are documented in [**System Architecture (`docs/ARCHITECTURE.md`)**](docs/ARCHITECTURE.md).

---

## Modular Software Profiles (`novos-setup`)

Optional workload stacks can be selected during Calamares installation (`netinstall` module) or installed at any time inside the booted OS via the `novos-setup` TUI/CLI utility:

```bash
# Launch interactive whiptail checklist:
sudo novos-setup

# Or provision specific profiles non-interactively:
sudo novos-setup --install gaming,dev,llm,network --user devuser
```

| Profile | Key Components | Storage Binding |
| :--- | :--- | :--- |
| **`gaming`** | Steam, Heroic, ProtonPlus, Protontricks, Bottles (Flathub) + Lutris, GameMode, Gamescope, MangoHud, GOverlay, Wine (64/32-bit), Vulkan tools | `/var/lib/flatpak` (`@flatpak`) + automatic `/mnt/raid0` Flathub override if mounted |
| **`dev`** | **Google Antigravity** (`antigravity` IDE & `antigravity-cli` / `agy`), Docker Engine (`docker-ce`, `buildx`, `compose`), VS Code, Rust (`rustup`), Python (`uv`), Haskell (`ghc`/`cabal`/`stack`), C/C++/LLVM (`clang`, `lld`, `cmake`, `ninja`), AppImage/RPM/DEB packaging tools | `/var/lib/docker` (`@docker`) & `/var/lib/containerd` (`@containerd`) with `chattr +C` |
| **`llm`** | Native **Ollama** (`HSA_OVERRIDE_GFX_VERSION=12.0.1`), Hugging Face CLI (`hf` + `hf_transfer`), containerized **ComfyUI** (`:8188`) & **Open WebUI** (`:3000`) via `novos-llm-stack`, `amdgpu_top`, `nvtop`, `rocm-smi` | `/srv/llm` (`@llm` with `chattr +C`), `HF_HOME=/srv/llm/huggingface`, `OLLAMA_MODELS=/srv/llm/ollama/models` |
| **`network`** | Samba `[DevShare]` file server (`smb.conf`), Remmina (`RDP`/`VNC`), Tailscale, WireGuard, OpenSSH Server | `/srv/devshare` (`@devshare`) |

See [**Modular Profiles Reference (`docs/PROFILES.md`)**](docs/PROFILES.md) for usage and service management commands.

---

## Repository Structure

```text
NovOS/
├── AGENTS.md                          # Agent governance, sandbox locks & Argon execution protocol
├── CLAUDE.md                          # Claude Code notes (defers to AGENTS.md)
├── README.md                          # Repository overview & quick-start guide
├── STEPPED_IMPLEMENTATION_PLAN.md     # Atomic stepped implementation plan (Argon prompts)
├── Cargo.toml / Cargo.lock            # Rust workspace root (novos-custom-tools)
├── .agents/                           # Agent locks, permissions, MCP config, rules & skills
├── .nebulous/                         # Golden OS snapshot, variance manifest & CI phase items
├── .tracker/                          # Nebulous (`neb`) project/phase/domain tracking state
├── branding/                          # Deterministic Nova visual assets (SVG & RGBA PNG)
│   ├── novos-logo.svg                 # Vector Supernova emblem (256x256)
│   ├── novos-logo.png                 # Raster Supernova emblem (256x256 RGBA)
│   ├── nova-wallpaper.svg             # Vector Cosmic Space & Horizon Nebula wallpaper (1920x1080)
│   └── nova-wallpaper.png             # Raster wallpaper for COSMIC, Limine & Calamares (1920x1080)
├── calamares/                         # Calamares Live Installer configuration & custom modules
│   ├── settings.conf                  # Master sequence & module orchestration
│   ├── branding/novos/                # Nova Dark branding descriptor, QML slideshow & Qt stylesheet
│   └── modules/                       # Partition, 10-subvol mount/fstab, netinstall & limine-bootloader
├── config/
│   └── build.env                      # Declarative build identity, storage & retention parameters
├── crates/
│   └── novos-custom-tools/            # Rust manifest-driven custom tool builder/installer
├── custom-tools/
│   └── manifest.yaml                  # Declarative custom tool sources (local / github_release / github_source)
├── docker/                            # Containerized builder & QEMU tester images + CI compose file
├── docs/                              # Comprehensive OS documentation suite
│   ├── ARCHITECTURE.md                # Kernel, Btrfs subvolumes, Limine, COSMIC & Calamares internals
│   ├── BRANDING_AND_THEME.md          # Nova color palette, asset generator & theme provisioning
│   ├── BUILD_AND_TEST.md              # 6-stage Clean-Room ISO pipeline, verification gates & QEMU harness
│   └── PROFILES.md                    # Modular profiles (novos-setup & novos-llm-stack)
└── scripts/
    ├── build-novos.sh                 # Primary Clean-Room 6-stage ISO build orchestrator
    ├── test-qemu.sh                   # QEMU/KVM UEFI (OVMF) + VirtIO GPU verification harness
    ├── test-docker-qemu.py            # Containerized QEMU install/E2E harness with local-LLM diagnostics
    ├── setup-popos-test-env.sh        # Host setup for /srv/docker-vms & /srv/docker-llms test environment
    ├── nebulous-ctl.py                # Golden snapshot extraction, variance lock & loop-breaker CLI
    ├── mcp_nebulous_ci.py             # Stdio MCP server exposing Nebulous CI tools
    ├── generate-branding-assets.py    # Zero-dependency pure-Python SVG/PNG branding generator
    ├── lib/verify-deps.sh             # Host dependency & cross-stage post-condition gate library
    └── stages/                        # Build stages 01-06 (06 finalizes and packages the Live ISO)
```

---

## Documentation Suite

* [**`docs/ARCHITECTURE.md`**](docs/ARCHITECTURE.md) — Deep dive into the unified Debian `sid` base, APT pinning locks, dual kernels, `dracut` initramfs, 10-subvolume Btrfs architecture, Limine snapshot synchronization, COSMIC greeter/session pipeline, and Calamares hybrid BIOS/UEFI installation flow.
* [**`docs/BUILD_AND_TEST.md`**](docs/BUILD_AND_TEST.md) — Step-by-step reference for [`config/build.env`](config/build.env), the 6-stage clean-room build orchestrator ([`scripts/build-novos.sh`](scripts/build-novos.sh)), pre/post stage verification gates ([`scripts/lib/verify-deps.sh`](scripts/lib/verify-deps.sh)), and QEMU/KVM UEFI testing ([`scripts/test-qemu.sh`](scripts/test-qemu.sh)).
* [**`docs/PROFILES.md`**](docs/PROFILES.md) — Complete guide to `novos-setup`, `novos-llm-stack`, RDNA 4 (`gfx1201`) ROCm container passthrough, and `/srv/llm` storage layout.
* [**`docs/BRANDING_AND_THEME.md`**](docs/BRANDING_AND_THEME.md) — Visual design system for NovOS 2026.10 ("Nova"), color palette tokens, pure-Python deterministic asset synthesis ([`scripts/generate-branding-assets.py`](scripts/generate-branding-assets.py)), and theme integration across Limine, Plymouth, COSMIC, and Calamares.
* [**`AGENTS.md`**](AGENTS.md) — Agent safety locks, privilege boundaries, fresh end-to-end build policy, and the Argon Atomic Step Scope & Gate Protocol.

---

## License

NovOS build scripts, installer modules, branding generators, and documentation are released under the **GNU General Public License v3.0 or later (`GPL-3.0-or-later`)**. Individual Debian packages and upstream components included in compiled ISO images are governed by their respective upstream licenses.
