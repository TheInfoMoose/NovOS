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
if [[ ! -x "${REPO_ROOT}/target/release/novos-slippery-tiles" || ! -x "${REPO_ROOT}/target/release/novos-custom-tools" ]]; then
    CARGO_BIN=""
    SUDO_HOME=""
    if [[ -n "${SUDO_USER:-}" ]]; then
        SUDO_HOME="$(getent passwd "${SUDO_USER}" 2>/dev/null | cut -d: -f6 || true)"
    fi
    if [[ -n "$SUDO_HOME" && -x "${SUDO_HOME}/.cargo/bin/cargo" ]]; then
        CARGO_BIN="${SUDO_HOME}/.cargo/bin/cargo"
    elif command -v cargo >/dev/null 2>&1; then
        CARGO_BIN="$(command -v cargo)"
    fi
    if [[ -n "$CARGO_BIN" ]]; then
        CARGO_ENV=()
        if [[ -n "$SUDO_HOME" ]]; then
            [[ -d "${SUDO_HOME}/.cargo" && -z "${CARGO_HOME:-}" ]] && CARGO_ENV+=("CARGO_HOME=${SUDO_HOME}/.cargo")
            [[ -d "${SUDO_HOME}/.rustup" && -z "${RUSTUP_HOME:-}" ]] && CARGO_ENV+=("RUSTUP_HOME=${SUDO_HOME}/.rustup")
        fi
        env "${CARGO_ENV[@]}" "$CARGO_BIN" build --release --offline --manifest-path "${REPO_ROOT}/Cargo.toml" 2>/dev/null || \
            env "${CARGO_ENV[@]}" "$CARGO_BIN" build --release --manifest-path "${REPO_ROOT}/Cargo.toml" || true
        if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" && -d "${REPO_ROOT}/target" ]]; then
            chown -R "${SUDO_UID}:${SUDO_GID}" "${REPO_ROOT}/target" 2>/dev/null || true
        fi
    fi
fi
if [[ -f "${REPO_ROOT}/target/release/novos-slippery-tiles" && -f "${REPO_ROOT}/target/release/novos-custom-tools" ]]; then
    install -m 0755 "${REPO_ROOT}/target/release/novos-slippery-tiles" "${MOUNT_ROOT}/root/novos-build/target/release/novos-slippery-tiles"
    install -m 0755 "${REPO_ROOT}/target/release/novos-custom-tools" "${MOUNT_ROOT}/root/novos-build/target/release/novos-custom-tools"
fi
cp -f "${SCRIPT_DIR}/../lib/verify-deps.sh" "${MOUNT_ROOT}/root/novos-build/scripts/lib/verify-deps.sh"
chmod +x "${MOUNT_ROOT}/root/novos-build/scripts/stages/"*.sh "${MOUNT_ROOT}/root/novos-build/scripts/lib/verify-deps.sh"

verify_stage2_post "$MOUNT_ROOT"
echo "==> [Stage 2] Bootstrap and /etc/fstab generation complete."
