#!/usr/bin/env bash
# ==============================================================================
# Stage 6 (Host / Clean-Namespace Orchestrated):
# Unified Pre-Flight Verification & Clean-Room Debian Sid Live ISO Builder
# Packages the staged rootfs (at ${MOUNT_ROOT}) into a bootable UEFI/BIOS
# Live ISO with Calamares pre-configured for the 10-subvolume Btrfs + Limine layout.
# ==============================================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/config/build.env"
source "${REPO_ROOT}/scripts/lib/verify-deps.sh"

ISO_WORK="${REPO_ROOT}/build/iso_work"
ISO_OUT="${ISO_PATH:-${REPO_ROOT}/build/NovOS-sid-amd64.iso}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output|-o|--iso)
            ISO_OUT="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if [[ "$ISO_OUT" != /* ]]; then
    ISO_OUT="${REPO_ROOT}/${ISO_OUT#./}"
fi

if [[ "$EUID" -ne 0 ]]; then
    echo "[ERROR] Stage 6 (06-snapshot-finalize.sh) must run as root (use sudo)." >&2
    exit 1
fi

verify_host_deps "iso"

GREETD_BACKUP="${REPO_ROOT}/build/.greetd-config.toml.bak"
FSTAB_BACKUP="${REPO_ROOT}/build/.fstab.bak"

echo "==> [Stage 6.1] Running Pre-Flight Integrity Verification on ${MOUNT_ROOT}..."
if [[ ! -d "${MOUNT_ROOT}/usr" ]] || [[ ! -f "${MOUNT_ROOT}/boot/efi/kernels/vmlinuz-primary" ]]; then
    echo "[FATAL] Staged rootfs check failed: ${MOUNT_ROOT}/usr or ${MOUNT_ROOT}/boot/efi/kernels/vmlinuz-primary missing." >&2
    exit 1
fi

mount_chroot_pseudo_fs "${MOUNT_ROOT}"

mkdir -p "${REPO_ROOT}/build"
if [[ -f "${MOUNT_ROOT}/etc/greetd/config.toml" ]]; then
    cp -f "${MOUNT_ROOT}/etc/greetd/config.toml" "$GREETD_BACKUP"
fi

restore_target_state() {
    echo "==> [Stage 6] Cleaning up chroot mounts and temporary Live state on ${MOUNT_ROOT}..."
    if [[ -f "$FSTAB_BACKUP" ]]; then
        cp -f "$FSTAB_BACKUP" "${MOUNT_ROOT}/etc/fstab" 2>/dev/null || true
        rm -f "$FSTAB_BACKUP" 2>/dev/null || true
    fi
    if [[ -f "$GREETD_BACKUP" ]]; then
        cp -f "$GREETD_BACKUP" "${MOUNT_ROOT}/etc/greetd/config.toml" 2>/dev/null || true
        rm -f "$GREETD_BACKUP" 2>/dev/null || true
    elif [[ -d "${MOUNT_ROOT}/etc/greetd" ]]; then
        cat << 'EOF' > "${MOUNT_ROOT}/etc/greetd/config.toml"
[terminal]
vt = 7

[default_session]
command = "/usr/local/bin/cosmic-greeter-launch"
user = "_greetd"
EOF
    fi
    rm -f "${MOUNT_ROOT}/etc/sudoers.d/99-novos-live" \
          "${MOUNT_ROOT}/etc/polkit-1/rules.d/49-novos-live-calamares.rules" \
          "${MOUNT_ROOT}/usr/share/applications/install-novos.desktop" \
          "${MOUNT_ROOT}/home/${DEFAULT_USER}/Desktop/install-novos.desktop" \
          "${MOUNT_ROOT}/etc/skel/Desktop/install-novos.desktop" 2>/dev/null || true

    unmount_chroot_pseudo_fs "${MOUNT_ROOT}"
}
trap restore_target_state EXIT

ERRORS=0

check_cmd() {
    if ! _check_target_bin "${MOUNT_ROOT}" "$1"; then
        echo "[FAIL] Missing binary in ${MOUNT_ROOT}: $1" >&2
        ERRORS=$((ERRORS + 1))
    else
        echo "[OK]   Binary verified in ${MOUNT_ROOT}: $1"
    fi
}

check_file() {
    if [[ ! -f "${MOUNT_ROOT}$1" ]]; then
        echo "[FAIL] Missing file in ${MOUNT_ROOT}: $1" >&2
        ERRORS=$((ERRORS + 1))
    else
        echo "[OK]   File verified in ${MOUNT_ROOT}: $1"
    fi
}

check_cmd apt
check_cmd dpkg
check_cmd start-cosmic
check_cmd cosmic-comp
check_cmd cosmic-greeter
check_cmd tuigreet
check_cmd snapper
check_cmd btrfs
check_cmd zstd
check_cmd fsck.vfat
check_cmd hwclock

check_file /boot/efi/EFI/BOOT/BOOTX64.EFI
check_file /usr/share/limine/BOOTX64.EFI
check_file /boot/efi/kernels/vmlinuz-primary
check_file /boot/efi/kernels/initrd-primary.img
check_file /boot/efi/kernels/vmlinuz-lts-fallback
check_file /boot/efi/kernels/initrd-lts-fallback.img
check_file /etc/pam.d/cosmic-greeter
check_file /etc/pam.d/greetd
check_file /usr/share/dbus-1/system.d/com.system76.CosmicGreeter.conf
check_file /usr/local/bin/cosmic-greeter-launch
check_file /usr/local/bin/cosmic-session-launch
check_file /usr/local/bin/novos-setup
check_file /usr/local/bin/novos-llm-stack
check_file /etc/os-release
check_file /etc/lsb-release
check_file /etc/issue

if ! grep -q '^ID=novos' "${MOUNT_ROOT}/etc/os-release"; then
    echo "[FAIL] ${MOUNT_ROOT}/etc/os-release does not identify as ID=novos!" >&2
    ERRORS=$((ERRORS + 1))
else
    echo "[OK]   ${MOUNT_ROOT}/etc/os-release verified (ID=novos)"
fi

if ! grep -q "pam_systemd.so" "${MOUNT_ROOT}/etc/pam.d/common-session"; then
    echo "[FAIL] pam_systemd.so is missing from ${MOUNT_ROOT}/etc/pam.d/common-session!" >&2
    ERRORS=$((ERRORS + 1))
else
    echo "[OK]   pam_systemd.so active in ${MOUNT_ROOT}/etc/pam.d/common-session"
fi

if [[ -d "${MOUNT_ROOT}/.snapshots/1" ]]; then
    echo "[FAIL] Build-time snapshot (${MOUNT_ROOT}/.snapshots/1) must not exist!" >&2
    ERRORS=$((ERRORS + 1))
else
    echo "[OK]   Zero build-time snapshots in ${MOUNT_ROOT}/.snapshots"
fi

if [[ -e "${MOUNT_ROOT}/etc/apt/apt.conf.d/80snapper" ]]; then
    echo "[FAIL] /etc/apt/apt.conf.d/80snapper must be disabled during build/Live ISO staging!" >&2
    ERRORS=$((ERRORS + 1))
else
    echo "[OK]   /etc/apt/apt.conf.d/80snapper is disabled"
fi

check_file /usr/share/novos/80snapper.disabled
check_file /usr/share/novos/99-limine-snapshots.disabled

if [[ "$ERRORS" -gt 0 ]]; then
    echo "[FATAL] Pre-flight check detected ${ERRORS} error(s)! Refusing to finalize broken image." >&2
    exit 1
fi

chroot "${MOUNT_ROOT}" /usr/bin/env -i \
    HOME=/root \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    NOVOS_TARGET_MODE="${NOVOS_TARGET_MODE:-iso}" \
    /usr/local/sbin/update-limine-snapshots

check_file /boot/efi/limine/limine.conf
check_file /boot/efi/limine.conf
check_file /boot/efi/EFI/BOOT/limine.conf

verify_stage6_post "${MOUNT_ROOT}"

echo "==> [Stage 6.2] Installing Calamares, Live-Boot & Polkit inside Staged Rootfs..."
chroot "${MOUNT_ROOT}" /usr/bin/env -i \
    HOME=/root \
    TERM="${TERM:-xterm-256color}" \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    DEBIAN_FRONTEND=noninteractive \
    RUNLEVEL=3 \
    NOVOS_TARGET_MODE="${NOVOS_TARGET_MODE:-iso}" \
    /bin/bash -euo pipefail -c '
        mkdir -p /run/dbus
        if [[ -x /usr/bin/dbus-daemon && ! -e /run/dbus/system_bus_socket ]]; then
            rm -f /run/dbus/pid 2>/dev/null || true
            /usr/bin/dbus-daemon --system --fork 2>/dev/null || true
        fi

        apt-get update
        apt-get -o DPkg::Pre-Invoke::="" -o DPkg::Post-Invoke::="" install -y --no-remove \
            calamares initramfs-tools-core busybox rsync uuid-runtime eject \
            squashfs-tools xorriso mtools dosfstools parted fdisk gdisk efibootmgr polkitd pkexec \
            klibc-utils libklibc udev kmod xz-utils zstd live-config live-config-systemd dbus-x11

        # In Debian sid, live-boot-initramfs-tools depends on initramfs-tools which conflicts
        # with dracut (pinned in Stage 3). Attempt direct install first; if blocked by dracut,
        # download and extract live-boot + live-boot-initramfs-tools payloads so mkinitramfs
        # (from initramfs-tools-core) has the live hooks while dracut and dpkg stay clean.
        if ! apt-get -o DPkg::Pre-Invoke::="" -o DPkg::Post-Invoke::="" install -y --no-remove --no-install-recommends \
            live-boot live-boot-initramfs-tools live-config live-config-systemd 2>/dev/null; then
            echo "==> [Stage 6.2] Provisioning live-boot & live-boot-initramfs-tools alongside dracut..."
            rm -rf /tmp/novos-live-debs /tmp/novos-live-extract
            mkdir -p /tmp/novos-live-debs /tmp/novos-live-extract
            (
                cd /tmp/novos-live-debs
                dl_pkgs=(live-boot live-boot-initramfs-tools)
                for lc_pkg in live-config live-config-systemd; do
                    if ! dpkg -s "$lc_pkg" >/dev/null 2>&1; then
                        dl_pkgs+=("$lc_pkg")
                    fi
                done
                apt-get -o APT::Sandbox::User=root download "${dl_pkgs[@]}"
                for deb in ./*.deb; do
                    dpkg-deb -x "$deb" /tmp/novos-live-extract
                done
            )
            rsync -aK /tmp/novos-live-extract/ /
            rm -rf /tmp/novos-live-debs /tmp/novos-live-extract
        fi

        for live_dir in /usr/share/initramfs-tools/hooks /usr/share/initramfs-tools/scripts /lib/live/boot /usr/lib/live/boot; do
            if [[ -d "$live_dir" ]]; then
                chmod -R a+rx "$live_dir"
            fi
        done

        # Ensure /etc/apt/apt.conf.d/80snapper remains disabled in /usr/share/novos/80snapper.disabled
        mkdir -p /usr/share/novos
        if [[ -f /etc/apt/apt.conf.d/80snapper ]]; then
            cp -f /etc/apt/apt.conf.d/80snapper /usr/share/novos/80snapper.disabled
            rm -f /etc/apt/apt.conf.d/80snapper
        fi
        rm -f /etc/apt/apt.conf.d/99-limine-snapshots
    '

echo "==> [Stage 6.3] Installing NovOS Calamares Configuration, Branding & Limine Module..."
mkdir -p "${MOUNT_ROOT}/etc/calamares/modules" \
         "${MOUNT_ROOT}/etc/calamares/branding/novos" \
         "${MOUNT_ROOT}/usr/lib/calamares/modules/limine-bootloader"

cp -f "${REPO_ROOT}/calamares/settings.conf" "${MOUNT_ROOT}/etc/calamares/settings.conf"
cp -f "${REPO_ROOT}/calamares/modules/"*.conf "${MOUNT_ROOT}/etc/calamares/modules/"
cp -f "${REPO_ROOT}/calamares/modules/"*.yaml "${MOUNT_ROOT}/etc/calamares/modules/"
cp -rf "${REPO_ROOT}/calamares/modules/limine-bootloader/"* "${MOUNT_ROOT}/usr/lib/calamares/modules/limine-bootloader/"
chmod +x "${MOUNT_ROOT}/usr/lib/calamares/modules/limine-bootloader/main.py"

cp -rf "${REPO_ROOT}/calamares/branding/novos/"* "${MOUNT_ROOT}/etc/calamares/branding/novos/"
cp -f "${REPO_ROOT}/calamares/branding/novos/stylesheet.qss" "${MOUNT_ROOT}/etc/calamares/branding/novos/stylesheet.qss"
cp -f "${REPO_ROOT}/branding/novos-logo.png" "${MOUNT_ROOT}/etc/calamares/branding/novos/novos-logo.png"
cp -f "${REPO_ROOT}/branding/nova-wallpaper.png" "${MOUNT_ROOT}/etc/calamares/branding/novos/nova-wallpaper.png"

if [[ ! -f "${MOUNT_ROOT}/etc/calamares/branding/novos/show.qml" ]]; then
    cat << 'EOF' > "${MOUNT_ROOT}/etc/calamares/branding/novos/show.qml"
import QtQuick 2.0;
import calamares.slideshow 1.0;

Presentation
{
    id: presentation

    Slide {
        Image {
            id: background
            source: "nova-wallpaper.png"
            width: 800; height: 440
            fillMode: Image.PreserveAspectCrop
            anchors.centerIn: parent
        }
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 24
            width: slideLabel.implicitWidth + 36
            height: slideLabel.implicitHeight + 16
            radius: 6
            color: "#CC0B0C10"
            border.color: "#FF6B1A"
            border.width: 1

            Text {
                id: slideLabel
                anchors.centerIn: parent
                text: "NovOS 2026.10 (Nova) — Debian Sid + XanMod + COSMIC + RDNA4"
                color: "#F4F6FB"
                font.pixelSize: 18
                font.bold: true
            }
        }
    }
}
EOF
fi

echo "==> [Stage 6.4] Configuring Live Session UX (Passwordless Sudo, Polkit, Desktop Shortcut & Auto-Login)..."
mkdir -p "${MOUNT_ROOT}/etc/sudoers.d"
cat << EOF > "${MOUNT_ROOT}/etc/sudoers.d/99-novos-live"
${DEFAULT_USER} ALL=(ALL) NOPASSWD: ALL
EOF
chmod 0440 "${MOUNT_ROOT}/etc/sudoers.d/99-novos-live"

mkdir -p "${MOUNT_ROOT}/etc/polkit-1/rules.d"
cat << EOF > "${MOUNT_ROOT}/etc/polkit-1/rules.d/49-novos-live-calamares.rules"
polkit.addRule(function(action, subject) {
    if ((action.id == "com.github.calamares.calamares.pkexec" ||
         action.id == "org.freedesktop.policykit.exec") &&
        subject.user == "${DEFAULT_USER}") {
        return polkit.Result.YES;
    }
});
EOF

mkdir -p "${MOUNT_ROOT}/usr/share/applications" \
         "${MOUNT_ROOT}/home/${DEFAULT_USER}/Desktop" \
         "${MOUNT_ROOT}/etc/skel/Desktop"

cat << 'EOF' > "${MOUNT_ROOT}/usr/share/applications/install-novos.desktop"
[Desktop Entry]
Type=Application
Version=1.0
Name=Install NovOS
GenericName=System Installer
Comment=Install NovOS 2026.10 (Nova) to disk with 10-subvolume Btrfs & Limine
Exec=sudo -E calamares
Icon=novos-logo
Terminal=false
StartupNotify=true
Categories=Qt;System;
Keywords=installer;calamares;system;novos;
EOF
chmod 0755 "${MOUNT_ROOT}/usr/share/applications/install-novos.desktop"
cp -f "${MOUNT_ROOT}/usr/share/applications/install-novos.desktop" "${MOUNT_ROOT}/home/${DEFAULT_USER}/Desktop/install-novos.desktop"
cp -f "${MOUNT_ROOT}/usr/share/applications/install-novos.desktop" "${MOUNT_ROOT}/etc/skel/Desktop/install-novos.desktop"
chmod 0755 "${MOUNT_ROOT}/home/${DEFAULT_USER}/Desktop/install-novos.desktop" \
           "${MOUNT_ROOT}/etc/skel/Desktop/install-novos.desktop"
chroot "${MOUNT_ROOT}" chown -R "${DEFAULT_USER}:${DEFAULT_USER}" "/home/${DEFAULT_USER}/Desktop"

mkdir -p "${MOUNT_ROOT}/etc/greetd"
cat << EOF > "${MOUNT_ROOT}/etc/greetd/config.toml"
[terminal]
vt = 7

[default_session]
command = "/usr/local/bin/cosmic-session-launch"
user = "${DEFAULT_USER}"

[initial_session]
command = "/usr/local/bin/cosmic-session-launch"
user = "${DEFAULT_USER}"
EOF

echo "==> [Stage 6.5] Generating Live Initramfs (with live-boot / dmsquash-live support)..."
rm -rf "$ISO_WORK"
mkdir -p "${ISO_WORK}/live" "${ISO_WORK}/EFI/BOOT" "${ISO_WORK}/boot/limine"

PRIMARY_KVER=""
if [[ -f "${MOUNT_ROOT}/boot/efi/kernels/primary.version" ]]; then
    PRIMARY_KVER="$(cat "${MOUNT_ROOT}/boot/efi/kernels/primary.version")"
fi
if [[ -z "$PRIMARY_KVER" ]]; then
    PRIMARY_KVER="$(ls -1 "${MOUNT_ROOT}/lib/modules" | grep -E 'xanmod|liquorix' | sort -V | tail -n1 || true)"
fi
if [[ -z "$PRIMARY_KVER" ]]; then
    PRIMARY_KVER="$(ls -1 "${MOUNT_ROOT}/lib/modules" | sort -V | tail -n1)"
fi

chroot "${MOUNT_ROOT}" /usr/bin/env -i \
    HOME=/root \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    DEBIAN_FRONTEND=noninteractive \
    RUNLEVEL=3 \
    PRIMARY_KVER="$PRIMARY_KVER" \
    /bin/bash -euo pipefail -c '
        LIVE_DRACUT_DRIVERS="squashfs overlay loop iso9660 isofs cdrom sr_mod sd_mod uas usb_storage virtio_blk virtio_scsi virtio_pci nvme ahci btrfs xhci_pci xhci_hcd ehci_pci ehci_hcd uhci_hcd ohci_pci ohci_hcd usbhid hid_generic vfat fat nls_cp437 nls_iso8859_1 nls_ascii virtio_gpu qxl bochs drm_kms_helper ata_piix ata_generic vmwgfx vboxvideo vboxguest"
        if command -v mkinitramfs >/dev/null 2>&1; then
            mkdir -p /etc/initramfs-tools/conf.d
            if [[ -f /etc/initramfs-tools/initramfs.conf ]]; then
                sed -i "s/^COMPRESS=.*/COMPRESS=zstd/" /etc/initramfs-tools/initramfs.conf
                grep -q "^COMPRESS=" /etc/initramfs-tools/initramfs.conf || echo "COMPRESS=zstd" >> /etc/initramfs-tools/initramfs.conf
            fi
            cat << "EOC" > /etc/initramfs-tools/conf.d/novos-live.conf
COMPRESS=zstd
BUSYBOX=y
EOC
            mkinitramfs -o /tmp/initrd-live.img "$PRIMARY_KVER"
            initrd_contents="$(lsinitramfs /tmp/initrd-live.img)"
            if ! grep -qE "(^|/)scripts/live$" <<< "$initrd_contents"; then
                echo "[FATAL] Live initramfs (/tmp/initrd-live.img) is missing scripts/live!" >&2
                exit 1
            fi
            if ! grep -qE "(^|/)(s?bin)/run-init$" <<< "$initrd_contents"; then
                echo "[FATAL] Live initramfs (/tmp/initrd-live.img) is missing run-init!" >&2
                exit 1
            fi
        elif command -v dracut >/dev/null 2>&1; then
            dracut --force --no-hostonly \
                --add "dmsquash-live btrfs" \
                --add-drivers "$LIVE_DRACUT_DRIVERS" \
                /tmp/initrd-live.img "$PRIMARY_KVER"
        else
            cp -f /boot/efi/kernels/initrd-primary.img /tmp/initrd-live.img
        fi
    '

cp -f "${MOUNT_ROOT}/boot/efi/kernels/vmlinuz-primary" "${ISO_WORK}/live/vmlinuz"
mv -f "${MOUNT_ROOT}/tmp/initrd-live.img" "${ISO_WORK}/live/initrd.img"
cp -f "${MOUNT_ROOT}/boot/efi/EFI/BOOT/BOOTX64.EFI" "${ISO_WORK}/EFI/BOOT/BOOTX64.EFI"

echo "==> [Stage 6.6] Cleaning APT Caches & Packing Staged Rootfs into SquashFS (${ISO_WORK}/live/filesystem.squashfs)..."
chroot "${MOUNT_ROOT}" apt-get clean 2>/dev/null || true
rm -rf "${MOUNT_ROOT}/var/lib/apt/lists/"* \
       "${MOUNT_ROOT}/var/cache/apt/archives/"*.deb \
       "${MOUNT_ROOT}/root/novos-build" 2>/dev/null || true

# Back up build-time /etc/fstab and write a clean Live /etc/fstab so systemd-fstab-generator
# in the Live ISO never blocks on build-time UUID= entries
if [[ -f "${MOUNT_ROOT}/etc/fstab" ]]; then
    cp -f "${MOUNT_ROOT}/etc/fstab" "$FSTAB_BACKUP"
fi
cat << 'EOF' > "${MOUNT_ROOT}/etc/fstab"
# Configured by live-boot
EOF

# Unmount pseudo-filesystems cleanly (and remove policy-rc.d guard) before mksquashfs
unmount_chroot_pseudo_fs "${MOUNT_ROOT}"
for sub in dev sys proc run; do
    if [[ -n "$(list_submounts "${MOUNT_ROOT}/${sub}")" ]]; then
        echo "[FATAL] Failed to unmount ${MOUNT_ROOT}/${sub} before mksquashfs!" >&2
        exit 1
    fi
done

mkdir -p "${MOUNT_ROOT}"/{dev,proc,sys,run,tmp,boot/efi,.snapshots}
chmod 1777 "${MOUNT_ROOT}/tmp"
chmod 0755 "${MOUNT_ROOT}"/{dev,proc,sys,run,boot/efi,.snapshots}

mksquashfs "${MOUNT_ROOT}" "${ISO_WORK}/live/filesystem.squashfs" \
    -comp zstd -Xcompression-level 6 -noappend \
    -wildcards \
    -e 'boot/efi/*' \
    -e '.snapshots/*' \
    -e 'proc/*' \
    -e 'sys/*' \
    -e 'dev/*' \
    -e 'run/*' \
    -e 'tmp/*'

# Restore non-live fstab, greetd & sudoers state immediately after mksquashfs completes
if [[ -f "$FSTAB_BACKUP" ]]; then
    cp -f "$FSTAB_BACKUP" "${MOUNT_ROOT}/etc/fstab"
    rm -f "$FSTAB_BACKUP"
fi
if [[ -f "$GREETD_BACKUP" ]]; then
    cp -f "$GREETD_BACKUP" "${MOUNT_ROOT}/etc/greetd/config.toml"
    rm -f "$GREETD_BACKUP"
fi
rm -f "${MOUNT_ROOT}/etc/sudoers.d/99-novos-live" \
      "${MOUNT_ROOT}/etc/polkit-1/rules.d/49-novos-live-calamares.rules" \
      "${MOUNT_ROOT}/usr/share/applications/install-novos.desktop" \
      "${MOUNT_ROOT}/home/${DEFAULT_USER}/Desktop/install-novos.desktop" \
      "${MOUNT_ROOT}/etc/skel/Desktop/install-novos.desktop"

echo "==> [Stage 6.7] Writing Limine Live ISO Configuration & Nova Branding..."
cp -f "${REPO_ROOT}/branding/nova-wallpaper.png" "${ISO_WORK}/boot/limine/nova-wallpaper.png"
cat << 'EOF' > "${ISO_WORK}/boot/limine/limine.conf"
timeout: 5
default_entry: 1
wallpaper: boot():/boot/limine/nova-wallpaper.png
term_backend: canvas
term_palette: 0b0c10;ff6b1a;ff9e44;ffb347;5294e2;d3869b;8ec07c;ebdbb2

/+NovOS 2026.10 (Nova) Live Installer
    //Boot NovOS Live (XanMod x64v3 + COSMIC DE + Calamares Installer)
    protocol: linux
    path: boot():/live/vmlinuz
    cmdline: boot=live components quiet noeject systemd.gpt_auto=0 split_lock_detect=off nowatchdog
    module_path: boot():/live/initrd.img

    //Boot NovOS Live (Safe Graphics / Software KMS)
    protocol: linux
    path: boot():/live/vmlinuz
    cmdline: boot=live components noeject systemd.gpt_auto=0 systemd.setenv=LIBGL_ALWAYS_SOFTWARE=1 systemd.setenv=COSMIC_DISABLE_DIRECT_SCANOUT=1 split_lock_detect=off nowatchdog
    module_path: boot():/live/initrd.img
EOF
cp -f "${ISO_WORK}/boot/limine/limine.conf" "${ISO_WORK}/limine.conf"
cp -f "${ISO_WORK}/boot/limine/limine.conf" "${ISO_WORK}/EFI/BOOT/limine.conf"

cp -f "${MOUNT_ROOT}/usr/share/limine/limine-bios.sys" "${ISO_WORK}/boot/limine/limine-bios.sys"
cp -f "${MOUNT_ROOT}/usr/share/limine/limine-bios-cd.bin" "${ISO_WORK}/boot/limine/limine-bios-cd.bin"
if [[ -f "${MOUNT_ROOT}/usr/share/limine/limine-uefi-cd.bin" ]]; then
    cp -f "${MOUNT_ROOT}/usr/share/limine/limine-uefi-cd.bin" "${ISO_WORK}/boot/limine/limine-uefi-cd.bin"
else
    dd if=/dev/zero of="${ISO_WORK}/boot/limine/limine-uefi-cd.bin" bs=1M count=4
    mkfs.vfat "${ISO_WORK}/boot/limine/limine-uefi-cd.bin"
    mmd -i "${ISO_WORK}/boot/limine/limine-uefi-cd.bin" ::/EFI ::/EFI/BOOT
    mcopy -i "${ISO_WORK}/boot/limine/limine-uefi-cd.bin" "${ISO_WORK}/EFI/BOOT/BOOTX64.EFI" ::/EFI/BOOT/BOOTX64.EFI
fi

echo "==> [Stage 6.8] Building Bootable Hybrid BIOS+UEFI Live ISO at ${ISO_OUT}..."
mkdir -p "$(dirname "$ISO_OUT")"
rm -f "$ISO_OUT"
xorriso -as mkisofs \
    -iso-level 3 \
    -full-iso9660-filenames \
    -volid "NOVOS_LIVE" \
    -b boot/limine/limine-bios-cd.bin \
    -no-emul-boot \
    -boot-load-size 4 \
    -boot-info-table \
    --efi-boot boot/limine/limine-uefi-cd.bin \
    -efi-boot-part \
    --efi-boot-image \
    --protective-msdos-label \
    -output "$ISO_OUT" \
    "$ISO_WORK"

"${MOUNT_ROOT}/usr/local/bin/limine" bios-install "$ISO_OUT"

if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    chown "${SUDO_UID}:${SUDO_GID}" "$ISO_OUT" 2>/dev/null || true
fi

verify_stage7_post "$ISO_OUT"
rm -rf "$ISO_WORK"

echo "=================================================================="
echo " [Stage 6] NovOS Live ISO Built Successfully: ${ISO_OUT}"
echo " Test in QEMU with: ./scripts/test-qemu.sh --iso ${ISO_OUT} --reset-vars"
echo "=================================================================="
