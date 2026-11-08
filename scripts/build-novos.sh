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
CLI_PROFILES=""

usage() {
    cat << EOF
Usage: sudo $0 [OPTIONS]

Target Options (defaults to --target-iso ${ISO_PATH}):
  --target-iso [path.iso]       Build Clean-Room Hybrid Live + Calamares Installer ISO (default: ${ISO_PATH})

Build Options (every build runs Stages 1-6 fresh; resuming is not supported):
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

# Always compile novos-custom-tools fresh from the checked-out sources (never ship a stale binary).
ensure_host_rust_binaries() {
    local cargo_bin=""
    local sudo_home=""
    local run_as=()
    local cargo_env=()
    local bin_path="${REPO_ROOT}/target/release/novos-custom-tools"

    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        sudo_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
        run_as=(runuser -u "${SUDO_USER}" --)
    fi
    if [[ -n "$sudo_home" && -x "${sudo_home}/.cargo/bin/cargo" ]]; then
        cargo_bin="${sudo_home}/.cargo/bin/cargo"
    elif command -v cargo >/dev/null 2>&1; then
        cargo_bin="$(command -v cargo)"
    else
        echo "[FATAL] cargo not found (checked ~${SUDO_USER:-root}/.cargo/bin and PATH); cannot build novos-custom-tools." >&2
        exit 1
    fi
    if [[ -n "$sudo_home" ]]; then
        [[ -d "${sudo_home}/.cargo" && -z "${CARGO_HOME:-}" ]] && cargo_env+=("CARGO_HOME=${sudo_home}/.cargo")
        [[ -d "${sudo_home}/.rustup" && -z "${RUSTUP_HOME:-}" ]] && cargo_env+=("RUSTUP_HOME=${sudo_home}/.rustup")
    fi
    # Earlier root-run builds may have left root-owned artifacts; hand target/ back to the invoking user.
    if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" && -d "${REPO_ROOT}/target" ]]; then
        chown -R "${SUDO_UID}:${SUDO_GID}" "${REPO_ROOT}/target"
    fi

    local build_start
    build_start="$(date +%s)"
    echo "==> Building novos-custom-tools fresh on host (${cargo_bin}, --locked)..."
    if ! "${run_as[@]}" env "${cargo_env[@]}" "$cargo_bin" clean --release -p novos-custom-tools \
            --manifest-path "${REPO_ROOT}/Cargo.toml" \
        || ! "${run_as[@]}" env "${cargo_env[@]}" "$cargo_bin" build --release --locked -p novos-custom-tools \
            --manifest-path "${REPO_ROOT}/Cargo.toml"; then
        echo "[FATAL] Rust workspace build failed" >&2
        exit 1
    fi
    if [[ ! -x "$bin_path" || "$(stat -c %Y "$bin_path")" -lt "$build_start" ]]; then
        echo "[FATAL] ${bin_path} was not freshly built by this run" >&2
        exit 1
    fi
}
ensure_host_rust_binaries

# 2. Purge Stale Artifacts Upfront
mkdir -p "$(dirname "$ISO_PATH")"
echo "==> Purging stale build artifacts prior to build..."
rm -f "$ISO_PATH" \
      "${ISO_PATH%.iso}.REJECTED.iso" \
      "${REPO_ROOT}/build/NovOS-sid-amd64.iso" \
      "${REPO_ROOT}/build/novos.img" \
      "${REPO_ROOT}/build/novos-calamares-target.img" \
      "./build/novos.img" \
      "./build/novos-calamares-target.img" 2>/dev/null || true
rm -rf "${REPO_ROOT}/build/iso_work" "./build/iso_work" 2>/dev/null || true

# Third-party .deb artifact cache: Stage 6 exports every installed non-Debian .deb into it.
# It is never purged; it accumulates across builds so later builds can hold a component.
export ARTIFACT_CACHE_DIR="${ARTIFACT_CACHE_DIR:-${REPO_ROOT}/build/artifact-cache}"
if [[ "$ARTIFACT_CACHE_DIR" != /* ]]; then
    ARTIFACT_CACHE_DIR="${REPO_ROOT}/${ARTIFACT_CACHE_DIR#./}"
fi
mkdir -p "${ARTIFACT_CACHE_DIR}/debs"
if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    chown "${SUDO_UID}:${SUDO_GID}" "$ARTIFACT_CACHE_DIR" "${ARTIFACT_CACHE_DIR}/debs"
fi

echo "==> Preparing directory-staged Clean-Room ISO build at ${MOUNT_ROOT} (Output ISO: ${ISO_PATH})..."

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

    chroot "${MOUNT_ROOT}" /usr/bin/gpgconf --kill all 2>/dev/null || true
}

# 3. Execute Stages 1 through 6 Sequentially (always fresh; no resume path)
bash "${SCRIPT_DIR}/stages/01-partition-mount.sh" --staging-dir "${MOUNT_ROOT}"
bash "${SCRIPT_DIR}/stages/02-bootstrap-chroot.sh" --staging-dir "${MOUNT_ROOT}"
run_in_chroot "/root/novos-build/scripts/stages/03-core-system.sh"
run_in_chroot "/root/novos-build/scripts/stages/04-cosmic-desktop.sh"
run_in_chroot "/root/novos-build/scripts/stages/05-profiles-installer.sh"
bash "${SCRIPT_DIR}/stages/06-snapshot-finalize.sh"
echo "==> Cleaning up ISO staging directory ${MOUNT_ROOT}..."
safe_remove_staging_dir "${MOUNT_ROOT}"

# Restore invoking user's ownership on the built ISO so QEMU can run without root
rm -f "${REPO_ROOT}/build/ovmf_vars.fd" 2>/dev/null || true
if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    chown "${SUDO_UID}:${SUDO_GID}" "$(dirname "$ISO_PATH")" "$ISO_PATH" 2>/dev/null || true
fi

# 4. ISO Variance Lock (always runs; no skip path). Any package/binary drop or downgrade against
#    .nebulous/golden-snapshot.toml rejects the ISO. Upgrades are reported, never failures.
print_variance_report() {
    python3 -I - "$1" << 'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    report = json.load(fh)
print(f"==> Variance lock: {report['status']} (violations={len(report['violations'])}, "
      f"upgrades={report['upgraded_count']}, packages={report['checked_packages_count']}, "
      f"binaries={report['checked_binaries_count']})")
for u in report["upgrades"]:
    print(f"  [UPGRADE] {u['item']}: {u['from_version']} -> {u['to_version']}")
for v in report["violations"]:
    print(f"  [VIOLATION] {v['type']} ({v['item']}): {v['detail']}")
PY
}

VARIANCE_REPORT="${REPO_ROOT}/logs/variance-$(date -u +%Y%m%dT%H%M%SZ).json"
mkdir -p "${REPO_ROOT}/logs"
variance_run_as=()
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    variance_run_as=(runuser -u "${SUDO_USER}" --)
fi
echo "==> Running ISO variance lock against golden snapshot (report: ${VARIANCE_REPORT})..."
variance_rc=0
if "${variance_run_as[@]}" python3 "${SCRIPT_DIR}/nebulous-ctl.py" verify-variance --iso "$ISO_PATH" --json > "$VARIANCE_REPORT"; then
    variance_rc=0
else
    variance_rc=$?
fi
if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    chown "${SUDO_UID}:${SUDO_GID}" "$VARIANCE_REPORT"
fi
if [[ "$variance_rc" -ne 0 ]]; then
    REJECTED_ISO_PATH="${ISO_PATH%.iso}.REJECTED.iso"
    if [[ -f "$ISO_PATH" ]]; then
        mv -f "$ISO_PATH" "$REJECTED_ISO_PATH"
    fi
    echo "[FATAL] ISO variance lock failed (exit ${variance_rc}); a package or binary was dropped or downgraded against the golden snapshot." >&2
    if ! print_variance_report "$VARIANCE_REPORT" >&2; then
        echo "[FATAL] Variance report ${VARIANCE_REPORT} is not a valid JSON report; see verify-variance stderr above." >&2
    fi
    echo "[FATAL] ISO rejected: ${REJECTED_ISO_PATH}" >&2
    echo "[FATAL] Report: ${VARIANCE_REPORT}" >&2
    echo "[FATAL] Intended drops/downgrades are accepted only by the user: ./scripts/nebulous-ctl.py approve-variance --item-name <pkg> --reason <why> (or bless-golden)." >&2
    exit "$variance_rc"
fi
print_variance_report "$VARIANCE_REPORT"

echo "=================================================================="
echo " NovOS Clean-Room Build Complete!"
echo " Live + Installer ISO: ${ISO_PATH}"
echo " Variance report: ${VARIANCE_REPORT}"
echo " To accept intended changes run: ./scripts/nebulous-ctl.py approve-variance --item-name <pkg> --reason <why> / bless-golden --iso ${ISO_PATH} (user only)"
echo " Test in QEMU with: ./scripts/test-qemu.sh --iso ${ISO_PATH} --reset-vars"
echo "=================================================================="
