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
TARGET_DISK="${REPO_ROOT}/build/novos-calamares-target.img"
DISK_SIZE_GIB="64"
RAM_MB="8192"
CPUS="4"
RESET_VARS="false"
USE_GL="false"
USE_DOCKER="false"
USE_HEADLESS="false"
USE_VNC="false"
TIER="live-boot"
SKIP_VARIANCE_CHECK="false"
ITEM_TOML=""
TIMEOUT_SEC=""
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --iso)
            ISO_DRIVE="$2"
            shift 2
            ;;
        --disk)
            TARGET_DISK="$2"
            shift 2
            ;;
        --disk-size)
            DISK_SIZE_GIB="${2%[Gg]*}"
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
        --docker)
            USE_DOCKER="true"
            shift
            ;;
        --headless)
            USE_HEADLESS="true"
            shift
            ;;
        --vnc)
            USE_VNC="true"
            shift
            ;;
        --no-vnc)
            USE_VNC="false"
            shift
            ;;
        --tier)
            TIER="$2"
            shift 2
            ;;
        --item)
            ITEM_TOML="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT_SEC="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN="true"
            shift
            ;;
        --skip-variance-check)
            SKIP_VARIANCE_CHECK="true"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if [[ "$TIER" != "live-boot" && "$TIER" != "install-e2e" ]]; then
    echo "[ERROR] Invalid --tier '${TIER}' (expected 'live-boot' or 'install-e2e')." >&2
    exit 1
fi

HARNESS_EXTRA_ARGS=(
    --ram "$RAM_MB"
    --cpus "$CPUS"
    --disk-size "$DISK_SIZE_GIB"
)
if [[ "$USE_VNC" == "true" ]]; then
    HARNESS_EXTRA_ARGS+=(--vnc)
else
    HARNESS_EXTRA_ARGS+=(--no-vnc)
fi
if [[ -n "$ITEM_TOML" ]]; then
    HARNESS_EXTRA_ARGS+=(--item "$ITEM_TOML")
fi
if [[ -n "$TIMEOUT_SEC" ]]; then
    HARNESS_EXTRA_ARGS+=(--timeout "$TIMEOUT_SEC")
fi
if [[ "$DRY_RUN" == "true" ]]; then
    HARNESS_EXTRA_ARGS+=(--dry-run)
fi
if [[ "$SKIP_VARIANCE_CHECK" == "true" ]]; then
    HARNESS_EXTRA_ARGS+=(--skip-variance-check)
fi

if [[ "$USE_DOCKER" == "true" ]]; then
    if [[ "$SKIP_VARIANCE_CHECK" != "true" && "$DRY_RUN" != "true" ]]; then
        "${REPO_ROOT}/scripts/nebulous-ctl.py" verify-variance --iso "$ISO_DRIVE"
    fi
    DOCKER_ISO_DRIVE="${ISO_DRIVE/#${REPO_ROOT}\//./}"
    DOCKER_TARGET_DISK="${TARGET_DISK/#${REPO_ROOT}\//./}"
    exec docker compose -f "${REPO_ROOT}/docker/docker-compose.ci.yml" run --rm --service-ports novos-qemu-tester \
        --iso "$DOCKER_ISO_DRIVE" --disk "$DOCKER_TARGET_DISK" --tier "$TIER" "${HARNESS_EXTRA_ARGS[@]}"
fi

if [[ "$USE_HEADLESS" == "true" ]]; then
    if [[ "$SKIP_VARIANCE_CHECK" != "true" && "$DRY_RUN" != "true" ]]; then
        "${REPO_ROOT}/scripts/nebulous-ctl.py" verify-variance --iso "$ISO_DRIVE"
    fi
    exec python3 "${REPO_ROOT}/scripts/test-docker-qemu.py" \
        --iso "$ISO_DRIVE" --disk "$TARGET_DISK" --tier "$TIER" "${HARNESS_EXTRA_ARGS[@]}"
fi

if ! command -v qemu-system-x86_64 >/dev/null 2>&1 || { command -v pacman >/dev/null 2>&1 && ! pacman -Q qemu-ui-gtk qemu-hw-display-virtio-vga qemu-hw-display-virtio-vga-gl qemu-hw-display-virtio-gpu qemu-hw-display-virtio-gpu-pci >/dev/null 2>&1; }; then
    echo "Installing QEMU/KVM and OVMF UEFI firmware..."
    if command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm --needed qemu-system-x86 qemu-ui-gtk qemu-hw-display-virtio-vga qemu-hw-display-virtio-vga-gl qemu-hw-display-virtio-gpu qemu-hw-display-virtio-gpu-pci edk2-ovmf
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y qemu-system-x86 qemu-utils ovmf
    else
        echo "[ERROR] Missing QEMU/OVMF dependencies and no supported package manager (pacman/apt-get) found." >&2
        exit 1
    fi
fi

# Locate OVMF UEFI firmware (prefer split CODE + VARS pflash pair, fallback to combined OVMF.fd)
OVMF_CODE=""
OVMF_VARS_TEMPLATE=""
for pair in \
    "/usr/share/OVMF/OVMF_CODE_4M.fd:/usr/share/OVMF/OVMF_VARS_4M.fd" \
    "/usr/share/OVMF/OVMF_CODE.fd:/usr/share/OVMF/OVMF_VARS.fd" \
    "/usr/share/edk2/x64/OVMF_CODE.4m.fd:/usr/share/edk2/x64/OVMF_VARS.4m.fd" \
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd:/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd" \
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

DEV_HELP="$(qemu-system-x86_64 -device help 2>&1 || true)"
DISPLAY_ARGS=()
if [[ "$USE_VNC" == "true" ]]; then
    DISPLAY_ARGS=(-device virtio-vga -vnc 0.0.0.0:0)
elif [[ "$USE_GL" == "true" ]]; then
    if grep -q 'name "virtio-vga-gl"' <<<"$DEV_HELP"; then
        DISPLAY_ARGS=(-device virtio-vga-gl -display gtk,gl=on)
    elif grep -q 'name "virtio-gpu-gl-pci"' <<<"$DEV_HELP"; then
        DISPLAY_ARGS=(-device virtio-gpu-gl-pci -display gtk,gl=on)
    else
        echo "[WARN] OpenGL display device (virtio-vga-gl / virtio-gpu-gl-pci) not available in QEMU; falling back to non-GL display." >&2
    fi
fi
if [[ "${#DISPLAY_ARGS[@]}" -eq 0 ]]; then
    if grep -q 'name "virtio-vga"' <<<"$DEV_HELP"; then
        DISPLAY_ARGS=(-device virtio-vga -display gtk)
    elif grep -q 'name "virtio-gpu-pci"' <<<"$DEV_HELP"; then
        DISPLAY_ARGS=(-device virtio-gpu-pci -display gtk)
    else
        DISPLAY_ARGS=(-vga std -display gtk)
    fi
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

if [[ "$SKIP_VARIANCE_CHECK" != "true" ]]; then
    "${REPO_ROOT}/scripts/nebulous-ctl.py" verify-variance --iso "$ISO_DRIVE"
fi

TARGET_DISK_DIR="$(dirname "$TARGET_DISK")"
if [[ ! -d "$TARGET_DISK_DIR" ]] || [[ ! -w "$TARGET_DISK_DIR" ]]; then
    sudo mkdir -p "$TARGET_DISK_DIR"
    sudo chown "${USER}:$(id -gn)" "$TARGET_DISK_DIR"
fi

DISK_FORMAT="qcow2"
EXPECTED_DISK_BYTES="$(( DISK_SIZE_GIB * 1073741824 ))"
NEED_CREATE_DISK="false"
if [[ ! -f "$TARGET_DISK" || "$RESET_VARS" == "true" ]]; then
    NEED_CREATE_DISK="true"
else
    read -r DISK_FORMAT EXISTING_DISK_BYTES < <(python3 -c '
import os, struct, sys
p = sys.argv[1]
try:
    with open(p, "rb") as f:
        hdr = f.read(32)
    if len(hdr) >= 32 and hdr[:4] == b"QFI\xfb":
        vsize = struct.unpack(">Q", hdr[24:32])[0]
        print(f"qcow2 {vsize}")
    else:
        print(f"raw {os.path.getsize(p)}")
except Exception:
    print("unknown 0")
' "$TARGET_DISK" 2>/dev/null || echo "unknown 0")
    if [[ "$EXISTING_DISK_BYTES" != "$EXPECTED_DISK_BYTES" ]]; then
        NEED_CREATE_DISK="true"
    fi
fi

if [[ "$NEED_CREATE_DISK" == "true" ]]; then
    rm -f "$TARGET_DISK"
    if command -v qemu-img >/dev/null 2>&1; then
        DISK_FORMAT="qcow2"
        qemu-img create -f qcow2 "$TARGET_DISK" "${DISK_SIZE_GIB}G" >/dev/null
    else
        DISK_FORMAT="raw"
        truncate -s "${DISK_SIZE_GIB}G" "$TARGET_DISK"
    fi
fi

echo "==> Launching NovOS Live ISO (${ISO_DRIVE}) in QEMU/KVM UEFI..."
echo "    Target Disk: ${TARGET_DISK} (${DISK_SIZE_GIB} GiB, ${DISK_FORMAT})"
echo "    SSH Forward: ssh -p 2222 ${DEFAULT_USER}@localhost"

exec qemu-system-x86_64 \
    -enable-kvm \
    -machine q35,accel=kvm \
    -cpu host \
    -smp "$CPUS" \
    -m "$RAM_MB" \
    "${UEFI_ARGS[@]}" \
    -cdrom "$ISO_DRIVE" \
    -drive "file=${TARGET_DISK},if=virtio,format=${DISK_FORMAT},discard=unmap" \
    -global ide-cd.bootindex=1 \
    -boot order=d,menu=on \
    "${DISPLAY_ARGS[@]}" \
    -netdev user,id=net0,hostfwd=tcp::2222-:22 \
    -device virtio-net-pci,netdev=net0 \
    -serial stdio
