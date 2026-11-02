# NovOS Build & QEMU Verification Guide

[**← Back to README**](../README.md) · [**System Architecture**](ARCHITECTURE.md) · [**Modular Profiles**](PROFILES.md) · [**Branding & Nova Theme**](BRANDING_AND_THEME.md)

---

## 1. Fresh End-to-End Build Policy (Mandatory Invariant)

> [!IMPORTANT]
> **All builds and verification tests MUST run fresh from Stage 1 through Stage 6.**
> Never validate architectural or script changes by resuming from an intermediate stage (`--from-stage`). Resuming from a partial stage masks regressions in earlier stages and cross-stage state transitions.

### Standard Build & Verification Sequence

```bash
# 1. Build a fresh Clean-Room Hybrid Live + Calamares Installer ISO (Stages 1-6)
sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso

# 2. Boot and verify the ISO in QEMU/KVM (UEFI OVMF + fresh NVRAM variables)
./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars
```

---

## 2. Host Build Dependencies

[`scripts/lib/verify-deps.sh`](../scripts/lib/verify-deps.sh) (`verify_host_deps "iso"`) automatically checks for and installs any missing host dependencies via `apt-get` at the start of `build-novos.sh`.

| Category | Required Host Packages | Required Host Binaries |
| :--- | :--- | :--- |
| **Bootstrap & Chroot** | `debootstrap`, `util-linux`, `coreutils`, `gawk`, `curl`, `wget`, `ca-certificates`, `gnupg` | `debootstrap`, `unshare`, `chroot`, `mount`, `umount`, `findmnt`, `blkid` |
| **Filesystem & Compression** | `dosfstools`, `mtools`, `squashfs-tools`, `zstd`, `xz-utils`, `kmod` | `mkfs.vfat`, `mcopy`, `mksquashfs`, `zstd`, `xz` |
| **ISO Packaging** | `xorriso`, `isolinux`, `syslinux-common`, `ovmf` | `xorriso` |

---

## 3. Declarative Build Configuration ([`config/build.env`](../config/build.env))

All build-time constants are centralized in [`config/build.env`](../config/build.env):

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `DISTRO_NAME` | `"NovOS"` | Human-readable distribution name |
| `DISTRO_ID` | `"novos"` | `/etc/os-release` `ID` token |
| `DISTRO_VERSION` | `"2026.10"` | Release version string |
| `DISTRO_CODENAME` | `"nova"` | Release codename |
| `DEBIAN_SUITE` | `"sid"` | Upstream Debian suite bootstrapped via `debootstrap` |
| `DEBIAN_MIRROR` | `"http://deb.debian.org/debian"` | Upstream Debian APT mirror |
| `TARGET_HOSTNAME` | `"novos"` | Default system hostname |
| `TARGET_TIMEZONE` | `"America/Chicago"` | Default timezone configured in Stage 3 |
| `TARGET_LOCALE` | `"en_US.UTF-8"` | Default UTF-8 locale |
| `DEFAULT_USER` | `"devuser"` | Default live/initial user account |
| `DEFAULT_ROOT_PASS` | `"novos"` | Default `root` password in Live environment |
| `DEFAULT_USER_PASS` | `"novos"` | Default `devuser` password in Live environment |
| `ESP_SIZE_MIB` | `"6144"` | Reference EFI System Partition size (`6 GiB`) |
| `ESP_LABEL` | `"NOVOS_EFI"` | FAT32 boot partition filesystem label |
| `ROOT_LABEL` | `"NOVOS_ROOT"` | Btrfs root filesystem label |
| `MOUNT_ROOT` | `"/mnt/novos"` | Host staging directory for clean-room chroot assembly |
| `ISO_PATH` | `"./build/NovOS-sid-amd64.iso"` | Default output path for the compiled hybrid ISO |
| `MAX_SNAPSHOTS` | `"5"` | Maximum retained Snapper root snapshots in Limine |
| `MAX_HISTORICAL_KERNELS` | `"2"` | Maximum unreferenced historical kernels kept on `/boot/efi` |
| `BUILD_PROFILES` | `"none"` | Comma-separated profiles (`gaming,dev,llm,network` or `none`) pre-installed during Stage 5 |

---

## 4. Clean-Room 6-Stage Build Pipeline

[`scripts/build-novos.sh`](../scripts/build-novos.sh) re-executes itself inside an isolated Linux mount, PID, IPC, and UTS namespace (`unshare --mount --pid --fork --ipc --uts --propagation private`) so chroot bind mounts (`/proc`, `/sys`, `/dev`, `/dev/pts`, `/run`) never pollute the host operating system and are deterministically torn down on exit.

```mermaid
flowchart LR
    S0["Pre-Build<br/>verify_host_deps<br/>Purge stale ./build/*"] --> S1["Stage 1<br/>01-partition-mount.sh<br/>Directory staging at /mnt/novos"]
    S1 --> S2["Stage 2<br/>02-bootstrap-chroot.sh<br/>debootstrap sid + scripts"]
    S2 --> S3["Stage 3 (Chroot)<br/>03-core-system.sh<br/>APT locks, Kernels, Mesa 26+, Limine"]
    S3 --> S4["Stage 4 (Chroot)<br/>04-cosmic-desktop.sh<br/>COSMIC DE, cosmic-greeter, Nova Theme"]
    S4 --> S5["Stage 5 (Chroot)<br/>05-profiles-installer.sh<br/>novos-setup & novos-llm-stack"]
    S5 --> S6["Stage 6 (Host/NS)<br/>06-snapshot-finalize.sh<br/>Calamares, SquashFS & xorriso ISO"]
```

### Stage-by-Stage Breakdown & Post-Condition Gates

Every stage ends with a strict verification gate defined in [`scripts/lib/verify-deps.sh`](../scripts/lib/verify-deps.sh):

| Stage | Script | Execution Context | Responsibilities | Post-Condition Gate (`verify-deps.sh`) |
| :---: | :--- | :--- | :--- | :--- |
| **1** | [`01-partition-mount.sh`](../scripts/stages/01-partition-mount.sh) | Host (Clean Namespace) | Cleans `/mnt/novos`, creates private bind mount, provisions FHS + 10 subvolume directory hierarchy (`/srv/llm/*`, `/srv/devshare`, `/var/lib/docker`, etc.) and `/var/lib/ollama` symlink. | `verify_stage1_post`: Confirms `/mnt/novos` is a mountpoint and all required directories exist. |
| **2** | [`02-bootstrap-chroot.sh`](../scripts/stages/02-bootstrap-chroot.sh) | Host (Clean Namespace) | Runs `debootstrap --arch=amd64 sid` with `systemd-sysv`, `libpam-systemd`, and `dbus-user-session` baked in; writes placeholder `/etc/fstab`; copies `config/`, `scripts/`, `branding/`, and `calamares/` into `/mnt/novos/root/novos-build/`. | `verify_stage2_post`: Verifies `/bin/bash`, `/usr/bin/apt-get`, `/etc/fstab`, and staged build scripts. |
| **3** | [`03-core-system.sh`](../scripts/stages/03-core-system.sh) | Chroot (`/mnt/novos`) | Configures `00-protect-core` & `01-novos-safety`; adds 3rd-party GPG keyrings; enforces `pam_systemd.so`; configures `/etc/os-release` (`ID=novos`); installs `linux-xanmod-x64v3` + `linux-image-amd64`; builds `dracut` initramfs images; installs Mesa 26+ RDNA 4 drivers; builds Limine `v8.x-binary` and installs `zz-sync-limine` + `update-limine-snapshots`. | `verify_stage3_post`: Verifies `ID=novos`, `pam_systemd.so`, `dracut`, `btrfs`, `snapper`, `BOOTX64.EFI`, `vmlinuz-primary`, `vmlinuz-lts-fallback`, and `limine.conf`. |
| **4** | [`04-cosmic-desktop.sh`](../scripts/stages/04-cosmic-desktop.sh) | Chroot (`/mnt/novos`) | Installs `cosmic-epoch-monorepo`, `cosmic-utils-monorepo`, `greetd`, `tuigreet`, `pipewire-audio`, `flatpak`; masks `seatd.service`; configures `/etc/pam.d/cosmic-greeter`, D-Bus policy, `cosmic-greeter-launch`, and `cosmic-session-launch`; backfills `CosmicTheme` `v2` (`list_button`, `is_frosted`); provisions Nova wallpaper and COSMIC Dark accent. | `verify_stage4_post`: Verifies `start-cosmic`, `cosmic-comp`, `cosmic-greeter`, `tuigreet`, `cosmic-greeter-launch`, `nova-wallpaper.png`, and `novos-logo.svg`. |
| **5** | [`05-profiles-installer.sh`](../scripts/stages/05-profiles-installer.sh) | Chroot (`/mnt/novos`) | Configures `/etc/profile.d/novos-env.sh` (`HF_HOME`, `OLLAMA_MODELS`), `/srv/llm/docker-compose.yml` (ComfyUI + Open WebUI with ROCm `/dev/kfd` & `/dev/dri` passthrough), `/usr/local/bin/novos-llm-stack`, and `/usr/local/bin/novos-setup`. Optionally pre-installs profiles if `--profiles` was passed. | `verify_stage5_post`: Verifies `novos-setup`, `novos-llm-stack`, and `/srv/llm/docker-compose.yml`. |
| **6** | [`06-snapshot-finalize.sh`](../scripts/stages/06-snapshot-finalize.sh) | Host (Clean Namespace) | Runs 25+ pre-flight binary/file checks; installs `live-boot`, `calamares`, and local `novos-profile-*` APT repository (`/var/local/novos-profile-repo`); installs Calamares `novos` branding (`stylesheet.qss`, `branding.desc`, `show.qml`) and `limine-bootloader` module; builds live `dracut` initrd (`dmsquash-live`); packs `filesystem.squashfs` (`zstd`); generates dual BIOS/UEFI `NovOS-sid-amd64.iso` via `xorriso` + `limine bios-install`. | `verify_stage6_post`: Verifies ISO file exists and is `> 100 MiB`. |

---

## 5. CLI Reference: `scripts/build-novos.sh`

```text
Usage: sudo ./scripts/build-novos.sh [OPTIONS]

Target Options (defaults to --target-iso ./build/NovOS-sid-amd64.iso):
  --target-iso [path.iso]       Build Clean-Room Hybrid Live + Calamares Installer ISO

Build Options:
  --profiles <list>             Comma-separated profiles to pre-install at Stage 5:
                                gaming,dev,llm,network (default: none)
  --help                        Show help message
```

---

## 6. QEMU/KVM Verification Harness (`scripts/test-qemu.sh`)

[`scripts/test-qemu.sh`](../scripts/test-qemu.sh) boots the generated Live ISO inside a hardware-accelerated QEMU/KVM virtual machine using OVMF UEFI firmware (`OVMF_CODE_4M.fd` + local `build/ovmf_vars.fd`), a 64 GiB persistent virtual target disk (`build/novos-calamares-target.img`) for testing Calamares installation, and VirtIO GPU graphics.

### Usage & Options

```bash
# Default verification run (8 GiB RAM, 4 vCPUs, fresh UEFI NVRAM vars):
./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --reset-vars

# Custom RAM/CPU allocation or OpenGL 3D acceleration (--gl):
./scripts/test-qemu.sh --iso ./build/NovOS-sid-amd64.iso --ram 16384 --cpus 8 --gl --reset-vars
```

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--iso <path>` | `./build/NovOS-sid-amd64.iso` | Path to the Hybrid Live ISO to boot |
| `--ram <MB>` | `8192` | Guest memory allocation in MiB |
| `--cpus <N>` | `4` | Number of guest vCPUs (`-cpu host -smp <N>`) |
| `--reset-vars` | `false` | Resets `./build/ovmf_vars.fd` from the clean OVMF template |
| `--gl` | `false` | Enables `virtio-vga-gl` (`-display gtk,gl=on`) instead of standard `virtio-vga` |

### Static Syntax & Self-Test Gates (Sandboxed / Non-Root)
Before running a full privileged build, verify all scripts and Python modules statically:

```bash
# 1. Verify bash syntax across all orchestrator and stage scripts:
bash -n scripts/build-novos.sh scripts/test-qemu.sh scripts/lib/verify-deps.sh scripts/stages/*.sh

# 2. Verify Python syntax and run the Calamares Limine module unit self-test:
python3 -m py_compile scripts/generate-branding-assets.py calamares/modules/limine-bootloader/main.py
python3 calamares/modules/limine-bootloader/main.py --self-test
```
