#!/usr/bin/env bash
# ==============================================================================
# Stage 3 (Inside Chroot): Unified Debian Sid Base, APT Hardening, PAM Fix,
# Dual Kernels (XanMod x64v3 + Debian Fallback), Mesa 26+ RDNA4 & Limine Sync
# ==============================================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export DEBIAN_FRONTEND=noninteractive

source /root/novos-build/config/build.env
source /root/novos-build/scripts/lib/verify-deps.sh

if ! ([[ -f /root/novos-build/config/build.env ]] && command -v apt-get >/dev/null 2>&1); then
    echo "[FATAL] Stage 3 must be executed inside the Debian sid chroot (/root/novos-build/config/build.env and apt-get required)." >&2
    exit 1
fi

if ! grep -q "/usr/local/sbin" /root/.bashrc 2>/dev/null; then
    echo 'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' >> /root/.bashrc
fi

echo "==> [Stage 3.1] Configuring Unified Debian Unstable (sid) APT Sources & Safety Locks..."
rm -f /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/unstable-leaf.list /etc/apt/preferences.d/*

cat << EOF > /etc/apt/sources.list
deb ${DEBIAN_MIRROR} ${DEBIAN_SUITE} main contrib non-free non-free-firmware
EOF

# Prevent APT from ever removing essential system/session packages during conflict resolution
cat << 'EOF' > /etc/apt/apt.conf.d/01-novos-safety
APT::Get::AutomaticRemove "false";
APT::Get::HideAutoRemove "1";
Acquire::Retries "5";
Acquire::http::Pipeline-Depth "0";
Acquire::http::No-Cache "true";
Acquire::https::No-Cache "true";
Acquire::BrokenProxy "true";
Acquire::By-Hash "yes";
DPkg::NoTriggers "false";
APT::NeverAutoRemove {
  "^firmware-.*";
  "^linux-image-.*";
  "^linux-headers-.*";
  "^linux-xanmod-.*";
};
EOF

cat << 'EOF' > /etc/apt/preferences.d/00-protect-core
Package: apt apt-utils dpkg systemd systemd-sysv libpam-systemd dbus dbus-user-session login passwd sudo util-linux util-linux-extra btrfs-progs dracut kmod greetd
Pin: release *
Pin-Priority: 1000
EOF

echo "==> [Stage 3.2] Installing Core Base Utilities, PAM Session Stack & Pre-Configuring Third-Party Keyrings..."
dpkg --add-architecture i386
apt-get update
apt-get install -y --no-remove \
    tzdata locales passwd adduser sudo curl wget gpg ca-certificates git build-essential \
    kmod xz-utils zstd initramfs-tools-core udev klibc-utils busybox \
    btrfs-progs dosfstools parted fdisk gdisk efibootmgr snapper dracut network-manager inotify-tools gawk coreutils \
    util-linux util-linux-extra systemd-timesyncd libpam-systemd dbus-user-session polkitd \
    whiptail dialog pciutils usbutils lsb-release

# Suppress build-time Snapper APT hooks inside staging chroot/Live environment
mkdir -p /usr/share/novos
if [ -f /etc/apt/apt.conf.d/80snapper ]; then
    mv -f /etc/apt/apt.conf.d/80snapper /usr/share/novos/80snapper.disabled
fi
if [ -f /usr/share/novos/80snapper.disabled ]; then
    sed -i -E 's/snapper --no-dbus/snapper/g; s/\bsnapper (create|cleanup)\b/snapper --no-dbus \1/g' /usr/share/novos/80snapper.disabled
fi
rm -f /etc/apt/apt.conf.d/80snapper

# Ensure system D-Bus daemon is running inside chroot for subsequent package maintainer scripts
mkdir -p /run/dbus
if [ -x /usr/bin/dbus-daemon ] && [ ! -e /run/dbus/system_bus_socket ]; then
    rm -f /run/dbus/pid 2>/dev/null || true
    /usr/bin/dbus-daemon --system --fork || true
fi

# Pre-configure Third-Party Repositories (XanMod, Debian COSMIC, Docker, VS Code, Antigravity)
mkdir -p /etc/apt/keyrings

# 1. XanMod Repository
wget -qO - https://dl.xanmod.org/archive.key | gpg --dearmor --yes -o /etc/apt/keyrings/xanmod-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/xanmod-archive-keyring.gpg] http://deb.xanmod.org ${DEBIAN_SUITE} main" > /etc/apt/sources.list.d/xanmod-release.list

# 2. Debian COSMIC Repository (Built against Debian Unstable/sid)
curl -fsSL https://rickysarraf.github.io/debian-cosmic/debian-cosmic.gpg | gpg --dearmor --yes -o /etc/apt/keyrings/debian-cosmic.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/debian-cosmic.gpg] https://rickysarraf.github.io/debian-cosmic/ unstable main" > /etc/apt/sources.list.d/debian-cosmic.list

# 3. Docker Engine Repository (Pre-configured for Dev & LLM profiles)
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian trixie stable" > /etc/apt/sources.list.d/docker.list

# 4. VS Code Repository (Pre-configured for Dev profile)
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor --yes -o /etc/apt/keyrings/packages.microsoft.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" > /etc/apt/sources.list.d/vscode.list

# 5. Google Antigravity Repository (Pre-configured for Antigravity IDE, App & agy CLI)
if curl -fsSL https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg | gpg --dearmor --yes -o /etc/apt/keyrings/antigravity-repo-key.gpg 2>/dev/null; then
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/antigravity-repo-key.gpg] https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/ antigravity-debian main" > /etc/apt/sources.list.d/antigravity.list
fi

apt-get update

echo "==> [Stage 3.3] Enforcing pam_systemd.so in /etc/pam.d/common-session (\$XDG_RUNTIME_DIR guarantee)..."
pam-auth-update --package --force
if ! grep -q "pam_systemd.so" /etc/pam.d/common-session; then
    echo "session optional pam_systemd.so" >> /etc/pam.d/common-session
fi

echo "==> [Stage 3.4] Configuring NovOS Identity (/etc/os-release, /etc/lsb-release, /etc/issue), Locales, Timezone, Hostname & Users..."
cat << EOF > /usr/lib/os-release
NAME="NovOS"
PRETTY_NAME="NovOS 2026.10 (Nova)"
ID=novos
ID_LIKE=debian
VERSION_ID="2026.10"
VERSION="2026.10 (Nova)"
VERSION_CODENAME=sid
ANSI_COLOR="38;2;255;107;26"
HOME_URL="https://github.com/novos-linux/novos"
SUPPORT_URL="https://github.com/novos-linux/novos/issues"
BUG_REPORT_URL="https://github.com/novos-linux/novos/issues"
EOF
ln -sfn ../usr/lib/os-release /etc/os-release

cat << EOF > /etc/lsb-release
DISTRIB_ID=NovOS
DISTRIB_RELEASE="2026.10"
DISTRIB_CODENAME=${DISTRO_CODENAME:-nova}
DISTRIB_DESCRIPTION="NovOS 2026.10 (Nova)"
EOF

cat << 'EOF' > /etc/issue
NovOS 2026.10 (Nova) \n \l

EOF

cat << 'EOF' > /etc/issue.net
NovOS 2026.10 (Nova)
EOF

echo "${TARGET_HOSTNAME}" > /etc/hostname
cat << EOF > /etc/hosts
127.0.0.1   localhost
127.0.1.1   ${TARGET_HOSTNAME}
::1         localhost ip6-localhost ip6-loopback
EOF

sed -i "s/^# *${TARGET_LOCALE}/${TARGET_LOCALE}/" /etc/locale.gen
locale-gen "${TARGET_LOCALE}"
update-locale LANG="${TARGET_LOCALE}"

ln -snf "/usr/share/zoneinfo/${TARGET_TIMEZONE}" /etc/localtime
echo "${TARGET_TIMEZONE}" > /etc/timezone
dpkg-reconfigure -f noninteractive tzdata

# Create standard hardware access groups (explicitly NO 'seat' group so systemd-logind owns the seat)
for grp in video render input audio plugdev netdev; do
    groupadd -f "$grp"
done

if ! id -u "${DEFAULT_USER}" >/dev/null 2>&1; then
    useradd -m -G sudo,adm,video,render,input,audio,plugdev,netdev -s /bin/bash "${DEFAULT_USER}"
fi

echo "root:${DEFAULT_ROOT_PASS}" | chpasswd
echo "${DEFAULT_USER}:${DEFAULT_USER_PASS}" | chpasswd

echo "==> [Stage 3.5] Configuring Dracut (Btrfs + NVMe + SATA/IDE + ISO9660 + USB + VirtIO + VirtualBox + AMDGPU)..."
mkdir -p /etc/dracut.conf.d
cat << 'EOF' > /etc/dracut.conf.d/10-novos.conf
hostonly="no"
add_dracutmodules+=" btrfs "
add_drivers+=" btrfs nvme ahci ata_piix ata_generic sd_mod sr_mod cdrom isofs uas usb_storage xhci_pci xhci_hcd ehci_pci ehci_hcd uhci_hcd ohci_pci ohci_hcd usbhid hid_generic vfat fat nls_cp437 nls_ascii virtio_blk virtio_scsi virtio_pci virtio_net virtio_gpu vmwgfx vboxvideo vboxguest qxl bochs amdgpu drm_kms_helper "
install_items+=" /bin/kmod /usr/bin/zstd /usr/bin/xz "
compress="zstd"
EOF

echo "==> [Stage 3.6] Installing Dual Kernels (XanMod x64v3 Primary + Locked Debian Fallback)..."
apt-get install -y --no-remove linux-image-amd64 linux-headers-amd64
apt-mark manual linux-image-amd64 linux-headers-amd64 $(dpkg-query -W -f='${Package}\n' 'linux-image-[0-9]*-amd64' 'linux-headers-[0-9]*-amd64' 2>/dev/null || true)

XANMOD_INSTALLED="false"
for attempt in 1 2 3; do
    echo "==> [Stage 3.6] Installing linux-xanmod-x64v3 (attempt ${attempt}/3)..."
    if apt-get -o Acquire::http::Pipeline-Depth=0 -o Acquire::http::No-Cache=true -o Acquire::https::No-Cache=true install -y --no-remove linux-xanmod-x64v3; then
        XANMOD_INSTALLED="true"
        break
    fi
    echo "[WARN] linux-xanmod-x64v3 install attempt ${attempt} failed; purging partial APT cache and refreshing lists..." >&2
    apt-get clean
    rm -f /var/cache/apt/archives/partial/* /var/lib/apt/lists/*xanmod* 2>/dev/null || true
    apt-get -o Acquire::http::Pipeline-Depth=0 -o Acquire::http::No-Cache=true -o Acquire::https::No-Cache=true update || true
done

if [[ "$XANMOD_INSTALLED" != "true" ]]; then
    echo "[WARN] linux-xanmod-x64v3 meta-package failed (e.g. Hash Sum mismatch); resolving concrete XanMod image & headers via apt-cache..." >&2
    XANMOD_IMG="$(apt-cache search --names-only '^linux-image-[0-9].*-x64v3-xanmod' | awk '{print $1}' | sort -V | tail -n1 || true)"
    XANMOD_HDR="$(apt-cache search --names-only '^linux-headers-[0-9].*-x64v3-xanmod' | awk '{print $1}' | sort -V | tail -n1 || true)"
    if [[ -n "$XANMOD_IMG" && -n "$XANMOD_HDR" ]]; then
        apt-get -o Acquire::http::Pipeline-Depth=0 -o Acquire::http::No-Cache=true -o Acquire::https::No-Cache=true install -y --no-remove "$XANMOD_IMG" "$XANMOD_HDR" || true
    fi
fi

apt-get install -y --no-install-recommends dkms libelf-dev clang lld llvm

echo "==> [Stage 3.7] Installing AMD/Intel Firmware & Unified Mesa 26+ RDNA4 (RX 9070 XT) Stack..."
apt-get install -y --no-remove \
    firmware-amd-graphics amd64-microcode intel-microcode \
    libgbm1 libegl-mesa0 libglx-mesa0 libgl1-mesa-dri libglx0 libegl1 \
    mesa-vulkan-drivers mesa-vulkan-drivers:i386 libvulkan1 libvulkan1:i386 \
    mesa-va-drivers mesa-opencl-icd vulkan-tools radeontop gamemode

echo "==> [Stage 3.8] Installing Limine Bootloader (UEFI + BIOS), Versioned Kernel Hook & Read-Write Snapshot Manager..."
rm -rf /tmp/limine
git clone https://github.com/limine-bootloader/limine.git --branch=v8.x-binary --depth=1 /tmp/limine
make -C /tmp/limine
mkdir -p /boot/efi/EFI/BOOT /boot/efi/limine /boot/efi/boot/limine /boot/efi/kernels /usr/share/limine /usr/local/bin
install -m 0755 /tmp/limine/limine /usr/local/bin/limine
install -m 0755 /tmp/limine/limine /usr/share/limine/limine
cp -f /tmp/limine/BOOTX64.EFI /usr/share/limine/BOOTX64.EFI
cp -f /tmp/limine/BOOTX64.EFI /boot/efi/EFI/BOOT/BOOTX64.EFI
if [ -f /tmp/limine/BOOTIA32.EFI ]; then
    cp -f /tmp/limine/BOOTIA32.EFI /usr/share/limine/BOOTIA32.EFI
    cp -f /tmp/limine/BOOTIA32.EFI /boot/efi/EFI/BOOT/BOOTIA32.EFI
fi
cp -f /tmp/limine/limine-bios.sys /usr/share/limine/limine-bios.sys
cp -f /tmp/limine/limine-bios-cd.bin /usr/share/limine/limine-bios-cd.bin
cp -f /tmp/limine/limine-uefi-cd.bin /usr/share/limine/limine-uefi-cd.bin
cp -f /tmp/limine/limine-bios.sys /boot/efi/limine/limine-bios.sys
cp -f /tmp/limine/limine-bios.sys /boot/efi/boot/limine/limine-bios.sys
cp -f /tmp/limine/limine-bios.sys /boot/efi/limine-bios.sys
cp -f /root/novos-build/branding/nova-wallpaper.png /boot/efi/limine/nova-wallpaper.png
rm -rf /tmp/limine

mkdir -p /etc/kernel/postinst.d
cat << 'EOF' > /etc/kernel/postinst.d/zz-sync-limine
#!/bin/sh
set -e
VERSION="$1"
VMLINUZ="/boot/vmlinuz-${VERSION}"
INITRD="/boot/initrd.img-${VERSION}"
DEST="/boot/efi/kernels"

mkdir -p "$DEST"

if [ ! -f "$INITRD" ] && command -v dracut >/dev/null 2>&1; then
    dracut --force --kver "$VERSION" "$INITRD"
fi

# Always archive the exact versioned kernel + initrd so snapshots stay paired with their /lib/modules/<VERSION>
cp -f "$VMLINUZ" "${DEST}/vmlinuz-${VERSION}"
cp -f "$INITRD" "${DEST}/initrd.img-${VERSION}"

if echo "$VERSION" | grep -qE "xanmod|liquorix"; then
    cp -f "$VMLINUZ" "${DEST}/vmlinuz-primary"
    cp -f "$INITRD" "${DEST}/initrd-primary.img"
    echo "$VERSION" > "${DEST}/primary.version"
else
    cp -f "$VMLINUZ" "${DEST}/vmlinuz-lts-fallback"
    cp -f "$INITRD" "${DEST}/initrd-lts-fallback.img"
    echo "$VERSION" > "${DEST}/fallback.version"
fi
EOF
chmod +x /etc/kernel/postinst.d/zz-sync-limine

for kver in $(ls -1 /lib/modules/ | sort -V); do
    dracut --force --kver "$kver" "/boot/initrd.img-${kver}"
    /etc/kernel/postinst.d/zz-sync-limine "$kver"
done

if [[ ! -f /boot/efi/kernels/vmlinuz-primary && -f /boot/efi/kernels/vmlinuz-lts-fallback ]]; then
    echo "[WARN] Primary XanMod kernel absent; seeding vmlinuz-primary and initrd-primary.img from Debian fallback kernel..." >&2
    cp -f /boot/efi/kernels/vmlinuz-lts-fallback /boot/efi/kernels/vmlinuz-primary
    cp -f /boot/efi/kernels/initrd-lts-fallback.img /boot/efi/kernels/initrd-primary.img
    if [[ -f /boot/efi/kernels/fallback.version ]]; then
        cp -f /boot/efi/kernels/fallback.version /boot/efi/kernels/primary.version
    fi
fi

# Prepare Snapper root config template with strict 5-snapshot retention limit,
# keeping SNAPPER_CONFIGS="" during build/Live session (Calamares enables SNAPPER_CONFIGS="root" on target install)
mkdir -p /etc/snapper/configs /usr/share/novos
cp -f /usr/share/snapper/config-templates/default /etc/snapper/configs/root
if [ -f /etc/default/snapper ]; then
    sed -i 's/^SNAPPER_CONFIGS=.*/SNAPPER_CONFIGS=""/' /etc/default/snapper
fi
sed -i 's/TIMELINE_CREATE="yes"/TIMELINE_CREATE="no"/' /etc/snapper/configs/root
sed -i "s/NUMBER_LIMIT=\"50\"/NUMBER_LIMIT=\"${MAX_SNAPSHOTS}\"/" /etc/snapper/configs/root
sed -i "s/NUMBER_LIMIT_IMPORTANT=\"10\"/NUMBER_LIMIT_IMPORTANT=\"${MAX_SNAPSHOTS}\"/" /etc/snapper/configs/root
if [ -f /etc/apt/apt.conf.d/80snapper ]; then
    mv -f /etc/apt/apt.conf.d/80snapper /usr/share/novos/80snapper.disabled
fi
if [ -f /usr/share/novos/80snapper.disabled ]; then
    sed -i -E 's/snapper --no-dbus/snapper/g; s/\bsnapper (create|cleanup)\b/snapper --no-dbus \1/g' /usr/share/novos/80snapper.disabled
fi
rm -f /etc/apt/apt.conf.d/80snapper

# Disable unsolicited boot/timeline snapper timers so they do not create unsynced RO snapshots or evict Golden Master
systemctl disable snapper-boot.timer snapper-timeline.timer 2>/dev/null || true
systemctl mask snapper-boot.timer snapper-timeline.timer 2>/dev/null || true

systemd-machine-id-setup

cat << EOF > /etc/novos-limine.conf
MAX_SNAPSHOTS=${MAX_SNAPSHOTS}
MAX_HISTORICAL_KERNELS=${MAX_HISTORICAL_KERNELS}
EOF

cat << 'EOF' > /usr/local/sbin/update-limine-snapshots
#!/usr/bin/env bash
set -euo pipefail

MAX_SNAPSHOTS=5
MAX_HISTORICAL_KERNELS=2
[ -f /etc/novos-limine.conf ] && source /etc/novos-limine.conf

if [ "${NOVOS_TARGET_MODE:-}" = "iso" ] || grep -q "live-boot" /etc/fstab 2>/dev/null; then
    ROOT_UUID="NOVOS_LIVE"
else
    ROOT_UUID="$(printf '%s' "${NOVOS_ROOT_UUID:-}" | tr -d '"\047[:space:]')"
    if [ -z "${ROOT_UUID:-}" ] && [ -f /etc/fstab ]; then
        ROOT_UUID="$(awk '!/^[[:space:]]*#/ && $2 == "/" && $1 ~ /^UUID=/ { sub(/^UUID=/, "", $1); print $1; exit }' /etc/fstab | tr -d '"\047[:space:]')"
    fi
    if [ -z "${ROOT_UUID:-}" ] && [ -f /etc/fstab ]; then
        FSTAB_ROOT_DEV="$(awk '!/^[[:space:]]*#/ && $2 == "/" && $1 ~ /^\/dev\// { print $1; exit }' /etc/fstab | tr -d '"\047[:space:]')"
        if [ -n "${FSTAB_ROOT_DEV:-}" ]; then
            ROOT_UUID="$(blkid -s UUID -o value "$FSTAB_ROOT_DEV" 2>/dev/null | tr -d '"\047[:space:]' || true)"
        fi
    fi
    if [ -z "${ROOT_UUID:-}" ]; then
        ROOT_SOURCE="$(findmnt -n -o SOURCE / 2>/dev/null | sed 's/\[.*\]//' || true)"
        ROOT_UUID="$(blkid -s UUID -o value "$ROOT_SOURCE" 2>/dev/null || findmnt -n -o UUID / 2>/dev/null || true)"
        ROOT_UUID="$(printf '%s' "${ROOT_UUID:-}" | tr -d '"\047[:space:]')"
    fi
    if [ -z "${ROOT_UUID:-}" ]; then
        ROOT_UUID="NOVOS_LIVE"
    fi
fi
MACHINE_ID="$(cat /etc/machine-id)"
CONF="/boot/efi/limine/limine.conf"
KERNEL_DIR="/boot/efi/kernels"

mkdir -p /boot/efi/limine /boot/efi/boot/limine /boot/efi/EFI/BOOT "$KERNEL_DIR"
if [ ! -f /boot/efi/EFI/BOOT/BOOTX64.EFI ] && [ -f /usr/share/limine/BOOTX64.EFI ]; then
    cp -f /usr/share/limine/BOOTX64.EFI /boot/efi/EFI/BOOT/BOOTX64.EFI
fi
if [ ! -f /boot/efi/limine/limine-bios.sys ] && [ -f /usr/share/limine/limine-bios.sys ]; then
    cp -f /usr/share/limine/limine-bios.sys /boot/efi/limine/limine-bios.sys
fi
if [ ! -f /boot/efi/boot/limine/limine-bios.sys ] && [ -f /usr/share/limine/limine-bios.sys ]; then
    cp -f /usr/share/limine/limine-bios.sys /boot/efi/boot/limine/limine-bios.sys
fi
if [ ! -f /boot/efi/limine-bios.sys ] && [ -f /usr/share/limine/limine-bios.sys ]; then
    cp -f /usr/share/limine/limine-bios.sys /boot/efi/limine-bios.sys
fi
if [ ! -f /boot/efi/limine/nova-wallpaper.png ] && [ -f /usr/share/backgrounds/novos/nova-wallpaper.png ]; then
    cp -f /usr/share/backgrounds/novos/nova-wallpaper.png /boot/efi/limine/nova-wallpaper.png
fi

# 1. Prune excess Snapper snapshots beyond MAX_SNAPSHOTS
if [ -d "/.snapshots" ]; then
    ALL_SNAPS=($(ls -1d /.snapshots/[0-9]* 2>/dev/null | sort -t/ -k3 -n || true))
    SNAP_COUNT="${#ALL_SNAPS[@]}"
    if [ "$SNAP_COUNT" -gt "$MAX_SNAPSHOTS" ]; then
        DEL_COUNT=$((SNAP_COUNT - MAX_SNAPSHOTS))
        for ((i=0; i<DEL_COUNT; i++)); do
            old_snap="${ALL_SNAPS[$i]}"
            old_num="$(basename "$old_snap")"
            echo "[Limine-Sync] Pruning old snapshot #${old_num} (limit: ${MAX_SNAPSHOTS})..."
            btrfs subvolume delete "${old_snap}/snapshot" 2>/dev/null || true
            rm -rf "$old_snap"
        done
    fi
fi

# 2. Generate Limine Configuration Header
cat << HEADER > "$CONF"
timeout: 5
default_entry: 1
wallpaper: boot():/limine/nova-wallpaper.png
term_backend: canvas
term_palette: 0b0c10;ff6b1a;ff9e44;ffb347;5294e2;d3869b;8ec07c;ebdbb2

/+NovOS (Debian Sid | XanMod + RDNA4)
comment: machine-id=${MACHINE_ID}
    //Primary (XanMod x64v3 Performance Kernel)
    protocol: linux
    path: boot():/kernels/vmlinuz-primary
    cmdline: root=UUID=${ROOT_UUID} rw rootflags=subvol=/@ quiet systemd.gpt_auto=0 split_lock_detect=off nowatchdog clearcpuid=rdseed loglevel=3
    module_path: boot():/kernels/initrd-primary.img

    //Fallback (Debian Stable Kernel)
    protocol: linux
    path: boot():/kernels/vmlinuz-lts-fallback
    cmdline: root=UUID=${ROOT_UUID} rw rootflags=subvol=/@ quiet systemd.gpt_auto=0 clearcpuid=rdseed loglevel=3
    module_path: boot():/kernels/initrd-lts-fallback.img

    /+Bootable Btrfs Snapshots (Read-Write Ready)
HEADER

declare -A REFERENCED_KERNELS=()
[ -f "${KERNEL_DIR}/primary.version" ] && REFERENCED_KERNELS["$(cat "${KERNEL_DIR}/primary.version")"]=1
[ -f "${KERNEL_DIR}/fallback.version" ] && REFERENCED_KERNELS["$(cat "${KERNEL_DIR}/fallback.version")"]=1

# 3. Populate Snapshot Boot Entries (Clearing ro=true and matching exact kernel version)
if [ -d "/.snapshots" ]; then
    for snap_dir in $(ls -1d /.snapshots/[0-9]* 2>/dev/null | sort -t/ -k3 -nr | head -n "$MAX_SNAPSHOTS"); do
        [ -d "${snap_dir}/snapshot" ] || continue
        num="$(basename "$snap_dir")"
        info="${snap_dir}/info.xml"

        # CRITICAL FIX: Clear Btrfs read-only flag so booting into snapshot works immediately in RW mode
        btrfs property set -ts "${snap_dir}/snapshot" ro false 2>/dev/null || true

        date_str="$(grep -oPm1 '(?<=<date>)[^<]+' "$info" 2>/dev/null || echo 'Snapshot')"
        desc="$(grep -oPm1 '(?<=<description>)[^<]+' "$info" 2>/dev/null || echo 'APT')"

        # Detect the primary XanMod (or fallback) kernel present inside this snapshot's /lib/modules
        snap_kver="$(ls -1 "${snap_dir}/snapshot/lib/modules" 2>/dev/null | grep -E 'xanmod|liquorix' | sort -V | tail -n1 || true)"
        if [ -z "$snap_kver" ]; then
            snap_kver="$(ls -1 "${snap_dir}/snapshot/lib/modules" 2>/dev/null | sort -V | tail -n1 || true)"
        fi

        if [ -n "$snap_kver" ] && [ -f "${KERNEL_DIR}/vmlinuz-${snap_kver}" ] && [ -f "${KERNEL_DIR}/initrd.img-${snap_kver}" ]; then
            k_path="boot():/kernels/vmlinuz-${snap_kver}"
            i_path="boot():/kernels/initrd.img-${snap_kver}"
            REFERENCED_KERNELS["$snap_kver"]=1
        else
            k_path="boot():/kernels/vmlinuz-primary"
            i_path="boot():/kernels/initrd-primary.img"
        fi

        cat << SNAP >> "$CONF"
        //Snapshot #${num} (${date_str} - ${desc}) [${snap_kver:-primary}]
        protocol: linux
        path: ${k_path}
        cmdline: root=UUID=${ROOT_UUID} rw rootflags=subvol=/@snapshots/${num}/snapshot quiet systemd.gpt_auto=0 split_lock_detect=off nowatchdog clearcpuid=rdseed loglevel=3
        module_path: ${i_path}
SNAP
    done
fi

# 4. Garbage-collect unreferenced versioned kernels on the EFI partition beyond MAX_HISTORICAL_KERNELS
UNREF_KERNELS=()
for vmlinuz_file in $(ls -1t "${KERNEL_DIR}"/vmlinuz-[0-9]* 2>/dev/null || true); do
    ver="${vmlinuz_file#${KERNEL_DIR}/vmlinuz-}"
    if [ -z "${REFERENCED_KERNELS[$ver]:-}" ]; then
        UNREF_KERNELS+=("$ver")
    fi
done

if [ "${#UNREF_KERNELS[@]}" -gt "$MAX_HISTORICAL_KERNELS" ]; then
    for ((i=MAX_HISTORICAL_KERNELS; i<${#UNREF_KERNELS[@]}; i++)); do
        old_kver="${UNREF_KERNELS[$i]}"
        echo "[Limine-Sync] Removing unreferenced historical kernel ${old_kver} from ESP..."
        rm -f "${KERNEL_DIR}/vmlinuz-${old_kver}" "${KERNEL_DIR}/initrd.img-${old_kver}"
    done
fi

cp -f "$CONF" /boot/efi/limine.conf
cp -f "$CONF" /boot/efi/boot/limine/limine.conf
cp -f "$CONF" /boot/efi/EFI/BOOT/limine.conf
for cfg_path in /boot/efi/limine/limine.conf /boot/efi/boot/limine/limine.conf /boot/efi/limine.conf /boot/efi/EFI/BOOT/limine.conf; do
    if [ ! -s "$cfg_path" ]; then
        echo "[Limine-Sync] ERROR: Missing or empty Limine configuration at ${cfg_path}" >&2
        exit 1
    fi
done
sync
EOF
chmod +x /usr/local/sbin/update-limine-snapshots

mkdir -p /usr/share/novos
cat << 'EOF' > /usr/share/novos/99-limine-snapshots.disabled
DPkg::Post-Invoke { "if [ -x /usr/local/sbin/update-limine-snapshots ]; then /usr/local/sbin/update-limine-snapshots || true; fi"; };
EOF
rm -f /etc/apt/apt.conf.d/99-limine-snapshots

/usr/local/sbin/update-limine-snapshots
gpgconf --kill all 2>/dev/null || true
verify_stage3_post
echo "==> [Stage 3] Core system, Dual Kernels, Mesa 26+, and Limine snapshot sync complete."
