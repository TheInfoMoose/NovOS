# ==============================================================================
# NovOS Clean-Room 6-Stage ISO Builder Container (Debian Sid)
# ==============================================================================
FROM debian:sid-slim

ENV DEBIAN_FRONTEND=noninteractive \
    MOUNT_APT_CACHE=/cache/apt \
    CARGO_HOME=/cache/cargo \
    MOUNT_ROOT=/mnt/novos

RUN apt-get update && apt-get install -y --no-install-recommends \
    debootstrap \
    util-linux \
    coreutils \
    gawk \
    curl \
    wget \
    ca-certificates \
    gnupg \
    dosfstools \
    mtools \
    squashfs-tools \
    zstd \
    xz-utils \
    kmod \
    xorriso \
    isolinux \
    syslinux-common \
    ovmf \
    btrfs-progs \
    e2fsprogs \
    parted \
    udev \
    python3 \
    python3-yaml \
    cargo \
    rustc \
    git \
    pkg-config \
    libxkbcommon-dev \
    libwayland-dev \
    && rm -rf /var/lib/apt/lists/*

RUN cat <<'EOF' > /usr/local/bin/novos-builder-entrypoint.sh && chmod 0755 /usr/local/bin/novos-builder-entrypoint.sh
#!/usr/bin/env bash
set -euo pipefail

cd /workspace

echo "==> [Nebulous Pre-Gate] Verifying repository variance against .nebulous/golden-snapshot.toml..."
./scripts/nebulous-ctl.py verify-variance --repo-only

export MOUNT_APT_CACHE="${MOUNT_APT_CACHE:-/cache/apt}"
export CARGO_HOME="${CARGO_HOME:-/cache/cargo}"
export MOUNT_ROOT="${MOUNT_ROOT:-/mnt/novos}"
mkdir -p "${MOUNT_APT_CACHE}" "${CARGO_HOME}"

echo "==> Wiping ${MOUNT_ROOT} and ./build/iso_work for a 100% fresh Stage 1-6 clean-room build..."
if [[ -f ./scripts/lib/verify-deps.sh ]]; then
    # shellcheck source=/dev/null
    source ./scripts/lib/verify-deps.sh
    safe_remove_staging_dir "${MOUNT_ROOT}" || true
fi
rm -rf --one-file-system "${MOUNT_ROOT}" ./build/iso_work

mkdir -p "${MOUNT_ROOT}/var/cache/apt/archives"
if ! mountpoint -q "${MOUNT_ROOT}/var/cache/apt/archives" 2>/dev/null; then
    mount --bind "${MOUNT_APT_CACHE}" "${MOUNT_ROOT}/var/cache/apt/archives" || true
fi
umount "${MOUNT_ROOT}/var/cache/apt/archives" 2>/dev/null || true
rm -rf --one-file-system "${MOUNT_ROOT}"

echo "==> Executing fresh Stages 1-6 Clean-Room ISO Build..."
./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso "$@"

echo "==> [Nebulous Post-Gate] Verifying built ISO variance against .nebulous/golden-snapshot.toml..."
./scripts/nebulous-ctl.py verify-variance --iso ./build/NovOS-sid-amd64.iso
EOF

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/novos-builder-entrypoint.sh"]
