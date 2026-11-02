#!/usr/bin/env bash
# ==============================================================================
# Stage 2: Host-Agnostic Debian Unstable (sid) Bootstrap & Live/Calamares fstab
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/config/build.env"
source "${SCRIPT_DIR}/../lib/verify-deps.sh"

if [[ "${1:-}" == "--staging-dir" ]]; then
    if [[ -n "${2:-}" ]]; then
        export MOUNT_ROOT="$2"
    fi
elif [[ -n "${1:-}" ]]; then
    export MOUNT_ROOT="$1"
fi

if [[ ! -x "${MOUNT_ROOT}/bin/bash" ]]; then
    echo "==> [Stage 2] Bootstrapping Debian (${DEBIAN_SUITE}) into ${MOUNT_ROOT}..."
    debootstrap --arch=amd64 \
        --components=main,contrib,non-free,non-free-firmware \
        --include=systemd,systemd-sysv,libpam-systemd,dbus,dbus-user-session,locales,sudo,ca-certificates,curl,wget,gpg,apt-utils \
        "$DEBIAN_SUITE" "$MOUNT_ROOT" "$DEBIAN_MIRROR"
else
    echo "==> [Stage 2] Existing debootstrap rootfs detected at ${MOUNT_ROOT}; skipping debootstrap download."
fi

echo "==> [Stage 2] Writing Live/Calamares placeholder /etc/fstab for directory-staged build..."
cat << 'EOF' > "${MOUNT_ROOT}/etc/fstab"
# /etc/fstab: static file system information for NovOS (Live / Calamares template)
# Populated dynamically by live-boot in Live ISO sessions or by Calamares during target installation.
EOF

echo "==> [Stage 2] Staging build scripts inside ${MOUNT_ROOT}/root/novos-build..."
mkdir -p "${MOUNT_ROOT}/root/novos-build/scripts/lib"
cp -rf "${REPO_ROOT}/config" "${REPO_ROOT}/scripts" "${REPO_ROOT}/branding" "${MOUNT_ROOT}/root/novos-build/"
if [[ -d "${REPO_ROOT}/calamares" ]]; then
    cp -rf "${REPO_ROOT}/calamares" "${MOUNT_ROOT}/root/novos-build/"
fi
cp -f "${SCRIPT_DIR}/../lib/verify-deps.sh" "${MOUNT_ROOT}/root/novos-build/scripts/lib/verify-deps.sh"
chmod +x "${MOUNT_ROOT}/root/novos-build/scripts/stages/"*.sh "${MOUNT_ROOT}/root/novos-build/scripts/lib/verify-deps.sh"

verify_stage2_post "$MOUNT_ROOT"
echo "==> [Stage 2] Bootstrap and /etc/fstab generation complete."
