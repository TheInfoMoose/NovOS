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

# Stage 1 only creates empty mountpoint directories; any existing rootfs content means a reused tree.
if [[ -e "${MOUNT_ROOT}/bin/bash" || -e "${MOUNT_ROOT}/usr/bin" || -e "${MOUNT_ROOT}/etc" ]]; then
    echo "[FATAL] Staging dir ${MOUNT_ROOT} is not empty (existing rootfs found); fresh Stage 1-6 builds only" >&2
    exit 1
fi
echo "==> [Stage 2] Bootstrapping Debian (${DEBIAN_SUITE}) into ${MOUNT_ROOT}..."
debootstrap --arch=amd64 \
    --components=main,contrib,non-free,non-free-firmware \
    --include=systemd,systemd-sysv,libpam-systemd,dbus,dbus-user-session,locales,sudo,ca-certificates,curl,wget,gpg,apt-utils \
    "$DEBIAN_SUITE" "$MOUNT_ROOT" "$DEBIAN_MIRROR"

echo "==> [Stage 2] Writing Live/Calamares placeholder /etc/fstab for directory-staged build..."
cat << 'EOF' > "${MOUNT_ROOT}/etc/fstab"
# /etc/fstab: static file system information for NovOS (Live / Calamares template)
# Populated dynamically by live-boot in Live ISO sessions or by Calamares during target installation.
EOF

echo "==> [Stage 2] Staging build scripts, Rust crates & custom-tools manifest inside ${MOUNT_ROOT}/root/novos-build..."
mkdir -p "${MOUNT_ROOT}/root/novos-build/scripts/lib" "${MOUNT_ROOT}/root/novos-build/target/release"
cp -rf "${REPO_ROOT}/config" "${REPO_ROOT}/scripts" "${REPO_ROOT}/branding" \
       "${REPO_ROOT}/crates" "${REPO_ROOT}/custom-tools" "${REPO_ROOT}/Cargo.toml" "${MOUNT_ROOT}/root/novos-build/"
if [[ -f "${REPO_ROOT}/Cargo.lock" ]]; then
    cp -f "${REPO_ROOT}/Cargo.lock" "${MOUNT_ROOT}/root/novos-build/Cargo.lock"
fi
if [[ -d "${REPO_ROOT}/calamares" ]]; then
    cp -rf "${REPO_ROOT}/calamares" "${MOUNT_ROOT}/root/novos-build/"
fi
# build-novos.sh (ensure_host_rust_binaries) always builds novos-custom-tools fresh with --locked
# before Stage 1 and fails the build if it cannot; Stage 4 requires the staged copy. A missing
# binary here is a broken orchestrator run, never something to rebuild or skip.
if [[ ! -x "${REPO_ROOT}/target/release/novos-custom-tools" ]]; then
    echo "[FATAL] ${REPO_ROOT}/target/release/novos-custom-tools is missing; build-novos.sh builds it before Stage 1." >&2
    exit 1
fi
install -m 0755 "${REPO_ROOT}/target/release/novos-custom-tools" "${MOUNT_ROOT}/root/novos-build/target/release/novos-custom-tools"
cp -f "${SCRIPT_DIR}/../lib/verify-deps.sh" "${MOUNT_ROOT}/root/novos-build/scripts/lib/verify-deps.sh"
chmod +x "${MOUNT_ROOT}/root/novos-build/scripts/stages/"*.sh "${MOUNT_ROOT}/root/novos-build/scripts/lib/verify-deps.sh"

verify_stage2_post "$MOUNT_ROOT"
echo "==> [Stage 2] Bootstrap and /etc/fstab generation complete."
