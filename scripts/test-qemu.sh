#!/usr/bin/env bash
# ==============================================================================
# NovOS QEMU/KVM UEFI Live ISO Verification Harness
# Boots the built Hybrid Live + Calamares Installer ISO in UEFI mode (OVMF)
# with VirtIO GPU acceleration to verify Limine, Kernels, Live Session,
# and Calamares installer readiness.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${REPO_ROOT}/config/build.env"

ISO_DRIVE="${ISO_PATH:-./build/NovOS-sid-amd64.iso}"
RAM_MB="8192"
CPUS="4"
RESET_VARS="false"
USE_GL="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --iso)
            ISO_DRIVE="$2"
            shift 2
            ;;
        --gl)
            USE_GL="true"
            shift
            ;;
        --ram)
            RAM_MB="$2"
            shift 2
            ;;
        --cpus)
            CPUS="$2"
            shift 2
            ;;
        --reset-vars)
            RESET_VARS="true"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if ! command -v qemu-system-x86_64 >/dev/null 2>&1; then
    echo "Installing QEMU/KVM and OVMF UEFI firmware..."
    sudo apt-get update && sudo apt-get install -y qemu-system-x86 qemu-utils ovmf
fi

# Locate OVMF UEFI firmware (prefer split CODE + VARS pflash pair, fallback to combined OVMF.fd)
OVMF_CODE=""
OVMF_VARS_TEMPLATE=""
for pair in \
    "/usr/share/OVMF/OVMF_CODE_4M.fd:/usr/share/OVMF/OVMF_VARS_4M.fd" \
    "/usr/share/OVMF/OVMF_CODE.fd:/usr/share/OVMF/OVMF_VARS.fd" \
    "/usr/share/edk2/x64/OVMF_CODE.4m.fd:/usr/share/edk2/x64/OVMF_VARS.4m.fd" \
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd:/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"; do
    code_cand="${pair%%:*}"
    vars_cand="${pair##*:}"
    if [[ -f "$code_cand" && -f "$vars_cand" ]]; then
        OVMF_CODE="$code_cand"
        OVMF_VARS_TEMPLATE="$vars_cand"
        break
    fi
done

UEFI_ARGS=()
if [[ -n "$OVMF_CODE" && -n "$OVMF_VARS_TEMPLATE" ]]; then
    VARS_DIR="${REPO_ROOT}/build"
    if [[ ! -d "$VARS_DIR" ]] || [[ ! -w "$VARS_DIR" ]]; then
        sudo mkdir -p "$VARS_DIR"
        sudo chown "${USER}:$(id -gn)" "$VARS_DIR"
    fi
    LOCAL_VARS="${VARS_DIR}/ovmf_vars.fd"
    if [[ ! -f "$LOCAL_VARS" || "$RESET_VARS" == "true" ]]; then
        cp -f "$OVMF_VARS_TEMPLATE" "$LOCAL_VARS"
    fi
    UEFI_ARGS=(
        -drive "if=pflash,format=raw,readonly=on,file=${OVMF_CODE}"
        -drive "if=pflash,format=raw,file=${LOCAL_VARS}"
    )
else
    for bios_cand in \
        /usr/share/ovmf/OVMF.fd \
        /usr/share/qemu/OVMF.fd; do
        if [[ -f "$bios_cand" ]]; then
            UEFI_ARGS=(-bios "$bios_cand")
            break
        fi
    done
fi

if [[ "${#UEFI_ARGS[@]}" -eq 0 ]]; then
    echo "[ERROR] Could not locate OVMF UEFI firmware. Install package 'ovmf'." >&2
    exit 1
fi

DISPLAY_ARGS=(
    -device virtio-vga
    -display gtk
)
if [[ "$USE_GL" == "true" ]]; then
    DISPLAY_ARGS=(
        -device virtio-vga-gl
        -display gtk,gl=on
    )
fi

if [[ ! -f "$ISO_DRIVE" ]]; then
    echo "[ERROR] Live ISO file does not exist: ${ISO_DRIVE}" >&2
    if [[ -d "${REPO_ROOT}/build/iso_work" ]]; then
        echo "[ERROR] Stale ${REPO_ROOT}/build/iso_work directory detected from an incomplete/failed build." >&2
    fi
    echo "        Run a fresh end-to-end build first:" >&2
    echo "        sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso" >&2
    exit 1
fi

if [[ -d "${REPO_ROOT}/build/iso_work" && "${REPO_ROOT}/build/iso_work" -nt "$ISO_DRIVE" ]]; then
    echo "[ERROR] Live ISO (${ISO_DRIVE}) is older than ${REPO_ROOT}/build/iso_work (stale ISO from a failed build)." >&2
    echo "        Run a fresh end-to-end build first:" >&2
    echo "        sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso" >&2
    exit 1
fi

ISO_SIZE="$(stat -c '%s' "$ISO_DRIVE" 2>/dev/null || echo 0)"
if [[ "$ISO_SIZE" -lt 67108864 ]]; then
    echo "[ERROR] Live ISO file is smaller than 64 MiB (${ISO_SIZE} bytes < 67108864): ${ISO_DRIVE}" >&2
    echo "        Run a fresh end-to-end build first:" >&2
    echo "        sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso" >&2
    exit 1
fi

ISO_MBR_SIG="$(python3 -c '
import sys
with open(sys.argv[1], "rb") as f:
    f.seek(510)
    print(f.read(2).hex().lower())
' "$ISO_DRIVE" 2>/dev/null || true)"
if [[ "$ISO_MBR_SIG" != "55aa" ]]; then
    echo "[ERROR] Live ISO is missing the 55aa MBR boot signature at bytes 510..511 (got '${ISO_MBR_SIG:-none}'): ${ISO_DRIVE}" >&2
    echo "        Run a fresh end-to-end build first:" >&2
    echo "        sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso" >&2
    exit 1
fi

echo "==> Launching NovOS Live ISO (${ISO_DRIVE}) in QEMU/KVM UEFI..."
echo "    SSH Forward: ssh -p 2222 ${DEFAULT_USER}@localhost"

exec qemu-system-x86_64 \
    -enable-kvm \
    -machine q35,accel=kvm \
    -cpu host \
    -smp "$CPUS" \
    -m "$RAM_MB" \
    "${UEFI_ARGS[@]}" \
    -cdrom "$ISO_DRIVE" \
    -global ide-cd.bootindex=1 \
    -boot order=d,menu=on \
    "${DISPLAY_ARGS[@]}" \
    -netdev user,id=net0,hostfwd=tcp::2222-:22 \
    -device virtio-net-pci,netdev=net0 \
    -serial stdio
