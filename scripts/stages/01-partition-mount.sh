#!/usr/bin/env bash
# ==============================================================================
# Stage 1: Prepare Directory-Backed Rootfs Staging Tree (Clean-Room ISO Builder)
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

echo "==> [Stage 1] Preparing directory-backed rootfs staging tree at ${MOUNT_ROOT} (ISO mode)..."
safe_remove_staging_dir "$MOUNT_ROOT"
mkdir -p "$MOUNT_ROOT"
mount --bind "$MOUNT_ROOT" "$MOUNT_ROOT"
mount --make-rprivate "$MOUNT_ROOT"

mkdir -p \
    "${MOUNT_ROOT}/dev" \
    "${MOUNT_ROOT}/proc" \
    "${MOUNT_ROOT}/sys" \
    "${MOUNT_ROOT}/run" \
    "${MOUNT_ROOT}/tmp" \
    "${MOUNT_ROOT}/boot/efi" \
    "${MOUNT_ROOT}/home" \
    "${MOUNT_ROOT}/.snapshots" \
    "${MOUNT_ROOT}/var/log" \
    "${MOUNT_ROOT}/var/cache" \
    "${MOUNT_ROOT}/var/lib/docker" \
    "${MOUNT_ROOT}/var/lib/containerd" \
    "${MOUNT_ROOT}/var/lib/flatpak" \
    "${MOUNT_ROOT}/srv/llm/"{ollama,huggingface,comfyui,open-webui,models} \
    "${MOUNT_ROOT}/srv/devshare" \
    "${MOUNT_ROOT}/mnt/raid0"
chmod 1777 "${MOUNT_ROOT}/tmp"

ln -sfn /srv/llm/ollama "${MOUNT_ROOT}/var/lib/ollama"

verify_stage1_post "$MOUNT_ROOT"
echo "==> [Stage 1] Directory staging hierarchy at ${MOUNT_ROOT} complete."
