#!/usr/bin/env bash
# ==============================================================================
# NovOS Clean-Room 6-Stage Installer ISO Build Orchestrator
# ==============================================================================
# Usage:
#   sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso
# ==============================================================================
set -euo pipefail

ORIG_ARGS=("$@")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${REPO_ROOT}/config/build.env"
source "${SCRIPT_DIR}/lib/verify-deps.sh"

TARGET_MODE="${TARGET_MODE_DEFAULT:-iso}"
FROM_STAGE=1
CLI_PROFILES=""

usage() {
    cat << EOF
Usage: sudo $0 [OPTIONS]

Target Options (defaults to --target-iso ${ISO_PATH}):
  --target-iso [path.iso]       Build Clean-Room Hybrid Live + Calamares Installer ISO (default: ${ISO_PATH})

Build Options:
  --from-stage <1..6>           Resume build starting at stage N (default: 1)
  --profiles <list>             Comma-separated profiles to install at Stage 5:
                                gaming,dev,llm,network (default: ${BUILD_PROFILES})
  --build-iso                   Compatibility flag (ISO mode is always active)
  --help                        Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target-iso)
            TARGET_MODE="iso"
            if [[ $# -ge 2 && -n "${2:-}" && "${2:-}" != -* ]]; then
                export ISO_PATH="$2"
                shift 2
            else
                shift
            fi
            ;;
        --from-stage)
            FROM_STAGE="$2"
            shift 2
            ;;
        --profiles)
            CLI_PROFILES="$2"
            shift 2
            ;;
        --build-iso)
            TARGET_MODE="iso"
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
done

export TARGET_MODE="iso"
export NOVOS_TARGET_MODE="iso"

if [[ "$EUID" -ne 0 ]]; then
    echo "[ERROR] Must run as root (use sudo)." >&2
    exit 1
fi

if [[ "${NOVOS_IN_CLEAN_NS:-0}" != "1" ]]; then
    echo "==> Entering isolated Linux namespace (mount, pid, ipc, uts; private mount propagation)..."
    exec env NOVOS_IN_CLEAN_NS=1 unshare --mount --pid --fork --ipc --uts --propagation private -- "$0" "${ORIG_ARGS[@]}"
fi

if [[ -n "$CLI_PROFILES" ]]; then
    export BUILD_PROFILES="$CLI_PROFILES"
fi

cleanup() {
    echo "==> Cleaning up chroot pseudo-filesystems and staging mounts..."
    if [[ -d "${MOUNT_ROOT}" ]]; then
        kill_chroot_processes "${MOUNT_ROOT}"
        unmount_chroot_pseudo_fs "${MOUNT_ROOT}"
    fi
    sync || true
    if mountpoint -q "${MOUNT_ROOT}" 2>/dev/null; then
        mount --make-rslave "${MOUNT_ROOT}" 2>/dev/null || true
        umount -R "${MOUNT_ROOT}" 2>/dev/null || umount -R -lf "${MOUNT_ROOT}" 2>/dev/null || true
    fi
    udevadm settle 2>/dev/null || true
}
trap cleanup EXIT

# 1. Verify Host Build Dependencies
verify_host_deps "iso"

# 2. Purge Stale Artifacts Upfront
mkdir -p "$(dirname "$ISO_PATH")"
echo "==> Purging stale build artifacts prior to build..."
rm -f "$ISO_PATH" \
      "${REPO_ROOT}/build/NovOS-sid-amd64.iso" \
      "${REPO_ROOT}/build/novos.img" \
      "${REPO_ROOT}/build/novos-calamares-target.img" \
      "./build/novos.img" \
      "./build/novos-calamares-target.img" 2>/dev/null || true
rm -rf "${REPO_ROOT}/build/iso_work" "./build/iso_work" 2>/dev/null || true

echo "==> Preparing directory-staged Clean-Room ISO build at ${MOUNT_ROOT} (Output ISO: ${ISO_PATH})..."

# Helper to mount existing staging directory when resuming from stage > 1
mount_existing_tree() {
    mkdir -p "$MOUNT_ROOT"
    if ! mountpoint -q "$MOUNT_ROOT"; then
        mount --bind "$MOUNT_ROOT" "$MOUNT_ROOT"
        mount --make-rprivate "$MOUNT_ROOT"
    fi
    mkdir -p "${MOUNT_ROOT}/root/novos-build/scripts/lib"
    cp -rf "${REPO_ROOT}/config" "${REPO_ROOT}/scripts" "${REPO_ROOT}/branding" "${REPO_ROOT}/calamares" "${MOUNT_ROOT}/root/novos-build/"
    cp -f "${SCRIPT_DIR}/lib/verify-deps.sh" "${MOUNT_ROOT}/root/novos-build/scripts/lib/verify-deps.sh"
    chmod +x "${MOUNT_ROOT}/root/novos-build/scripts/stages/"*.sh "${MOUNT_ROOT}/root/novos-build/scripts/lib/verify-deps.sh"
}

# Host-agnostic chroot execution wrapper inside private namespace
run_in_chroot() {
    local stage_script="$1"
    mount_chroot_pseudo_fs "${MOUNT_ROOT}"

    chroot "${MOUNT_ROOT}" /usr/bin/env -i \
        HOME=/root \
        TERM="${TERM:-xterm-256color}" \
        PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        DEBIAN_FRONTEND=noninteractive \
        RUNLEVEL=3 \
        BUILD_PROFILES="${BUILD_PROFILES}" \
        NOVOS_TARGET_MODE="${TARGET_MODE}" \
        /bin/bash -euo pipefail "$stage_script"
}

# 3. Execute Stages 1 through 6 Sequentially
if [[ "$FROM_STAGE" -le 1 ]]; then
    bash "${SCRIPT_DIR}/stages/01-partition-mount.sh" --staging-dir "${MOUNT_ROOT}"
else
    mount_existing_tree
fi

if [[ "$FROM_STAGE" -le 2 ]]; then
    bash "${SCRIPT_DIR}/stages/02-bootstrap-chroot.sh" --staging-dir "${MOUNT_ROOT}"
fi

if [[ "$FROM_STAGE" -le 3 ]]; then
    run_in_chroot "/root/novos-build/scripts/stages/03-core-system.sh"
fi

if [[ "$FROM_STAGE" -le 4 ]]; then
    run_in_chroot "/root/novos-build/scripts/stages/04-cosmic-desktop.sh"
fi

if [[ "$FROM_STAGE" -le 5 ]]; then
    run_in_chroot "/root/novos-build/scripts/stages/05-profiles-installer.sh"
fi

if [[ "$FROM_STAGE" -le 6 ]]; then
    bash "${SCRIPT_DIR}/stages/06-snapshot-finalize.sh"
    echo "==> Cleaning up ISO staging directory ${MOUNT_ROOT}..."
    safe_remove_staging_dir "${MOUNT_ROOT}"
fi

# Restore invoking user's ownership on the built ISO so QEMU can run without root
rm -f "${REPO_ROOT}/build/ovmf_vars.fd" 2>/dev/null || true
if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    chown "${SUDO_UID}:${SUDO_GID}" "$(dirname "$ISO_PATH")" "$ISO_PATH" 2>/dev/null || true
fi

echo "=================================================================="
echo " NovOS Clean-Room Build Complete!"
echo " Live + Installer ISO: ${ISO_PATH}"
echo " Test in QEMU with: ./scripts/test-qemu.sh --iso ${ISO_PATH} --reset-vars"
echo "=================================================================="
