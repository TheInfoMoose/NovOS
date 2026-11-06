---
name: novos-linux-toolchain
description: Load when touching NovOS kernel configuration, rootfs bootstrapping, package pruning, bootloader/ESP layout (Limine), initramfs (dracut), Btrfs subvolumes, or systemd/serial-console units — e.g. editing scripts/stages/01–04, calamares/modules/limine-bootloader, or diagnosing missing drivers in the initramfs.
---

# NovOS Low-Level Linux Architecture & Toolchain

Source: former Skills.md §3.1 (retired in STEP 8F); provenance in the CLAUDE.md Parity Table. Applies to all agents, not only Argon.

## Kernel Configuration & Compiling (make menuconfig / Kconfig)
- Never run `make menuconfig` (or `nconfig`/`xconfig`): interactive curses menus hang headless agent loops.
- Manage kernel trees programmatically:
  - Python `kconfiglib`;
  - the kernel's `scripts/config` CLI, e.g. `scripts/config --enable CONFIG_VIRTIO_BLK`;
  - declarative config fragments merged with `scripts/kconfig/merge_config.sh`.
- Cross-reference target hardware requirements (QEMU VirtIO drivers, NVMe controllers, `CONFIG_DEVTMPFS_MOUNT`) against the Kconfig dependency graph so dependent flags are not silently unset by `make olddefconfig`. Re-check the resulting `.config` after `olddefconfig`.

## Package Management & Rootfs Construction
- Assemble root filesystems without privileged host access: use unprivileged isolation (`bubblewrap`/`bwrap`, `fakeroot`, user namespaces).
- Drive bootstrapping tools — `debootstrap` (NovOS: Debian sid), `pacstrap`, `dnf --installroot`, Yocto/BitBake recipes — into staging directories.
- Validate the Filesystem Hierarchy Standard (FHS) layout, provision essential device nodes, and prune unused documentation and locales to keep artifact sizes minimal.
- NovOS: the host build is run by the user (`sudo ./scripts/build-novos.sh`); agents never invoke `sudo`.

## Bootloader & Firmware Integration
- Write deterministic disk-partitioning scripts with `sfdisk` or `parted` using non-interactive heredocs. Agents author these scripts; they never execute partitioning/formatting commands themselves (CLAUDE.md §2).
- Standardize on GPT: dedicated EFI System Partition (ESP) for UEFI targets, BIOS boot partition for legacy systems.
- Manage bootloader configuration (generic: GRUB2, Limine, systemd-boot) with UUID-pinned root mappings.
- Orchestrate `dracut` or `mkinitramfs` with explicit `--add-drivers` manifests so all essential storage, bus, and filesystem modules are baked into the initramfs.

### NovOS specifics
- **Bootloader is Limine v8** (UEFI). ESP mounted at `/boot/efi`; layout verified by Stage 6:
  - `/boot/efi/EFI/BOOT/BOOTX64.EFI` (copied from `/usr/share/limine/BOOTX64.EFI`)
  - `/boot/efi/limine/limine.conf`, `/boot/efi/limine.conf`, `/boot/efi/EFI/BOOT/limine.conf`
  - Kernels/initrds on the ESP: `/boot/efi/kernels/vmlinuz-primary`, `initrd-primary.img`, `vmlinuz-lts-fallback`, `initrd-lts-fallback.img`
  - Snapshot entries via `/usr/local/sbin/update-limine-snapshots`; installer module `calamares/modules/limine-bootloader/`.
- **dracut is generic, never host-only**: `hostonly="no"` (Stage 3 dracut config) and `dracut --force --no-hostonly` (Stage 6). The image must boot on hardware other than the build host/VM.
- **Btrfs, 10 subvolumes** (Calamares mount config): `@`, `@home`, `@snapshots`, `@var_log`, `@var_cache`, `@flatpak`, `@docker`, `@containerd`, `@llm`, `@devshare`. Keep fstab, Calamares, and snapshot tooling in agreement when changing this set.

## Init Systems & Service Management
- Author declarative service definitions — systemd unit files (NovOS default), runit run scripts, or OpenRC runscripts — with rigorous dependency ordering (`After=`, `Requires=`, `WantedBy=`).
- In headless/sandbox environments, configure a non-blocking serial console getty (e.g. `agetty` on `ttyS0`) so VMs expose a diagnostic interface over serial I/O.
