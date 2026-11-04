#!/usr/bin/env bash
# ==============================================================================
# NovOS Shared Dependency & Stage Gate Verification Library
# ==============================================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

VERIFY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_REPO_ROOT="$(cd "${VERIFY_LIB_DIR}/../.." && pwd)"

if [[ -z "${DISTRO_ID:-}" && -f "${VERIFY_REPO_ROOT}/config/build.env" ]]; then
    _SAVED_MOUNT_ROOT="${MOUNT_ROOT:-}"
    # shellcheck source=/dev/null
    source "${VERIFY_REPO_ROOT}/config/build.env"
    if [[ -n "${_SAVED_MOUNT_ROOT}" ]]; then
        export MOUNT_ROOT="${_SAVED_MOUNT_ROOT}"
    fi
    unset _SAVED_MOUNT_ROOT
fi

# ------------------------------------------------------------------------------
# Helper: Map binary/requirement name to Debian (apt) and Arch (pacman) packages
# ------------------------------------------------------------------------------
_map_dep_to_pkg() {
    local dep="$1"
    local mgr="$2"
    case "$dep" in
        debootstrap)         echo "debootstrap" ;;
        parted)              echo "parted" ;;
        mkfs.vfat)           echo "dosfstools" ;;
        mkfs.btrfs|btrfs)    echo "btrfs-progs" ;;
        blkid|findmnt|losetup|unshare) echo "util-linux" ;;
        udevadm)
            if [[ "$mgr" == "apt" ]]; then echo "udev"; else echo "systemd"; fi
            ;;
        chattr)              echo "e2fsprogs" ;;
        python3)
            if [[ "$mgr" == "apt" ]]; then echo "python3"; else echo "python"; fi
            ;;
        mksquashfs|unsquashfs) echo "squashfs-tools" ;;
        xorriso)
            if [[ "$mgr" == "apt" ]]; then echo "xorriso"; else echo "libisoburn"; fi
            ;;
        mmd|mcopy)           echo "mtools" ;;
        qemu-system-x86_64)  echo "qemu-system-x86" ;;
        ovmf)
            if [[ "$mgr" == "apt" ]]; then echo "ovmf"; else echo "edk2-ovmf"; fi
            ;;
        *)                   echo "$dep" ;;
    esac
}

# ------------------------------------------------------------------------------
# Helper: Locate OVMF UEFI firmware on host
# ------------------------------------------------------------------------------
_check_ovmf_firmware() {
    local candidates=(
        "/usr/share/OVMF/OVMF_CODE.fd"
        "/usr/share/OVMF/OVMF_CODE_4M.fd"
        "/usr/share/OVMF/OVMF_CODE.secboot.fd"
        "/usr/share/ovmf/OVMF.fd"
        "/usr/share/edk2/x64/OVMF_CODE.fd"
        "/usr/share/edk2/x64/OVMF_CODE.4m.fd"
        "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd"
        "/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd"
        "/usr/share/qemu/OVMF.fd"
    )
    local path
    for path in "${candidates[@]}"; do
        if [[ -f "$path" ]]; then
            return 0
        fi
    done
    return 1
}

# ------------------------------------------------------------------------------
# 1. Host Dependency Verification
#    Usage: verify_host_deps <iso|qemu>
# ------------------------------------------------------------------------------
verify_host_deps() {
    local mode="${1:-iso}"
    local required_bins=()
    local require_ovmf="false"

    case "$mode" in
        iso)
            required_bins=(
                debootstrap unshare findmnt udevadm python3
                mksquashfs xorriso mmd mcopy
            )
            ;;
        qemu)
            required_bins=(
                qemu-system-x86_64 python3
            )
            require_ovmf="true"
            ;;
        *)
            echo "[ERROR] Unknown dependency verification mode: '${mode}' (expected: iso|qemu)" >&2
            exit 1
            ;;
    esac

    local missing=()
    local bin
    for bin in "${required_bins[@]}"; do
        if ! command -v "$bin" >/dev/null 2>&1; then
            missing+=("$bin")
        fi
    done

    if [[ "$require_ovmf" == "true" ]] && ! _check_ovmf_firmware; then
        missing+=("ovmf")
    fi

    local pacman_qemu_pkgs=(
        qemu-ui-gtk
        qemu-hw-display-virtio-vga
        qemu-hw-display-virtio-vga-gl
        qemu-hw-display-virtio-gpu
        qemu-hw-display-virtio-gpu-pci
    )
    local qpkg
    if [[ "$mode" == "qemu" ]] && command -v pacman >/dev/null 2>&1; then
        for qpkg in "${pacman_qemu_pkgs[@]}"; do
            if ! pacman -Q "$qpkg" >/dev/null 2>&1; then
                missing+=("$qpkg")
            fi
        done
    fi

    if [[ "${#missing[@]}" -eq 0 ]]; then
        echo "[OK]   All host dependencies satisfied for mode '${mode}'."
        return 0
    fi

    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        echo "[WARN] Missing host dependencies for mode '${mode}': ${missing[*]}"
        echo "==> Attempting automatic installation as root..."
        local pkgs=()
        local item pkg existing_pkg found
        if command -v apt-get >/dev/null 2>&1; then
            for item in "${missing[@]}"; do
                pkg="$(_map_dep_to_pkg "$item" "apt")"
                found="false"
                for existing_pkg in "${pkgs[@]+"${pkgs[@]}"}"; do
                    if [[ "$existing_pkg" == "$pkg" ]]; then
                        found="true"
                        break
                    fi
                done
                if [[ "$found" == "false" ]]; then
                    pkgs+=("$pkg")
                fi
            done
            DEBIAN_FRONTEND=noninteractive apt-get update -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${pkgs[@]}"
        elif command -v pacman >/dev/null 2>&1; then
            for item in "${missing[@]}"; do
                pkg="$(_map_dep_to_pkg "$item" "pacman")"
                found="false"
                for existing_pkg in "${pkgs[@]+"${pkgs[@]}"}"; do
                    if [[ "$existing_pkg" == "$pkg" ]]; then
                        found="true"
                        break
                    fi
                done
                if [[ "$found" == "false" ]]; then
                    pkgs+=("$pkg")
                fi
            done
            if [[ "$mode" == "qemu" ]]; then
                for qpkg in "${pacman_qemu_pkgs[@]}"; do
                    found="false"
                    for existing_pkg in "${pkgs[@]+"${pkgs[@]}"}"; do
                        if [[ "$existing_pkg" == "$qpkg" ]]; then
                            found="true"
                            break
                        fi
                    done
                    if [[ "$found" == "false" ]]; then
                        pkgs+=("$qpkg")
                    fi
                done
            fi
            pacman -Sy --noconfirm --needed "${pkgs[@]}"
        else
            echo "[FATAL] Missing dependencies (${missing[*]}) and no supported package manager (apt-get/pacman) found." >&2
            exit 1
        fi

        # Re-verify after installation attempt
        local still_missing=()
        for bin in "${required_bins[@]}"; do
            if ! command -v "$bin" >/dev/null 2>&1; then
                still_missing+=("$bin")
            fi
        done
        if [[ "$require_ovmf" == "true" ]] && ! _check_ovmf_firmware; then
            still_missing+=("ovmf")
        fi
        if [[ "$mode" == "qemu" ]] && command -v pacman >/dev/null 2>&1; then
            for qpkg in "${pacman_qemu_pkgs[@]}"; do
                if ! pacman -Q "$qpkg" >/dev/null 2>&1; then
                    still_missing+=("$qpkg")
                fi
            done
        fi

        if [[ "${#still_missing[@]}" -gt 0 ]]; then
            echo "[FATAL] Failed to provision missing host dependencies: ${still_missing[*]}" >&2
            exit 1
        fi
        echo "[OK]   Successfully installed and verified host dependencies for mode '${mode}'."
        return 0
    else
        echo "[FATAL] Missing required host dependencies for mode '${mode}': ${missing[*]}" >&2
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# Internal Gate Check Helpers
# ------------------------------------------------------------------------------
_check_root_prefix() {
    local root="${1:-}"
    if [[ -n "$root" ]]; then
        while [[ "$root" == */ ]]; do
            root="${root%/}"
        done
        echo "$root"
        return 0
    fi

    # No explicit argument provided: auto-detect host mount_root vs chroot/live system
    local candidate="${MOUNT_ROOT:-/mnt/novos}"
    while [[ "$candidate" == */ ]]; do
        candidate="${candidate%/}"
    done
    if [[ -n "$candidate" && ! -f "/root/novos-build/config/build.env" ]] && ! grep -q '^ID=novos' /etc/os-release 2>/dev/null; then
        if findmnt -rn -M "$candidate" >/dev/null 2>&1 || [[ -d "${candidate}/etc" ]]; then
            echo "$candidate"
            return 0
        fi
    fi
    echo ""
}

_check_target_bin() {
    local root="$1"
    local bin="$2"
    if [[ -z "$root" ]]; then
        command -v "$bin" >/dev/null 2>&1
    else
        local dir candidate link_target
        for dir in /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin; do
            candidate="${root}${dir}/${bin}"
            if [[ -L "$candidate" ]]; then
                link_target="$(readlink "$candidate" 2>/dev/null || true)"
                if [[ "$link_target" == /* ]]; then
                    if [[ -x "${root}${link_target}" ]]; then
                        return 0
                    fi
                    continue
                fi
            fi
            if [[ -x "$candidate" ]]; then
                return 0
            fi
        done
        return 1
    fi
}

# ------------------------------------------------------------------------------
# Chroot Process & Mount Isolation Helpers (Host Crash Prevention)
# ------------------------------------------------------------------------------
list_submounts() {
    local prefix="${1:-}"
    while [[ "$prefix" == */ && "$prefix" != "/" ]]; do
        prefix="${prefix%/}"
    done
    if [[ -z "$prefix" || "$prefix" == "/" ]]; then
        return 0
    fi
    local real_prefix
    real_prefix="$(readlink -f "$prefix" 2>/dev/null || echo "$prefix")"
    if [[ -z "$real_prefix" || "$real_prefix" == "/" ]]; then
        real_prefix="$prefix"
    fi

    findmnt -rn -o TARGET 2>/dev/null | awk -v p="$prefix" -v rp="$real_prefix" '
    {
        gsub(/\\x20/, " ", $0)
        if ($0 == p || index($0, p "/") == 1 || $0 == rp || index($0, rp "/") == 1) {
            n = split($0, parts, "/")
            printf "%05d\t%05d\t%s\n", n, length($0), $0
        }
    }
    ' | sort -r | cut -f3-
}

kill_chroot_processes() {
    local mount_root="${1:-${MOUNT_ROOT:-/mnt/novos}}"
    while [[ "$mount_root" == */ && "$mount_root" != "/" ]]; do
        mount_root="${mount_root%/}"
    done
    if [[ -z "$mount_root" || "$mount_root" == "/" || ! -d "$mount_root" ]]; then
        return 0
    fi

    local real_mount_root
    real_mount_root="$(readlink -f "$mount_root" 2>/dev/null || echo "$mount_root")"
    if [[ -z "$real_mount_root" || "$real_mount_root" == "/" ]]; then
        return 0
    fi

    local pids=()
    local proc_root pid root_target cwd_target
    for proc_root in /proc/[0-9]*/root; do
        [[ -e "$proc_root" || -L "$proc_root" ]] || continue
        pid="${proc_root%/root}"
        pid="${pid#/proc/}"
        [[ "$pid" == "$$" || "$pid" == "${BASHPID:-}" || "$pid" == "${PPID:-}" ]] && continue
        root_target="$(readlink -f "$proc_root" 2>/dev/null || true)"
        cwd_target="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
        if [[ -n "$root_target" && "$root_target" != "/" ]] && \
           [[ "$root_target" == "$mount_root" || "$root_target" == "${mount_root}/"* || \
              "$root_target" == "$real_mount_root" || "$root_target" == "${real_mount_root}/"* ]]; then
            pids+=("$pid")
        elif [[ -n "$cwd_target" && "$cwd_target" != "/" ]] && \
             [[ "$cwd_target" == "$mount_root" || "$cwd_target" == "${mount_root}/"* || \
                "$cwd_target" == "$real_mount_root" || "$cwd_target" == "${real_mount_root}/"* ]]; then
            pids+=("$pid")
        fi
    done

    if [[ "${#pids[@]}" -gt 0 ]]; then
        echo "==> Terminating ${#pids[@]} chroot process(es) inside ${mount_root}: ${pids[*]}"
        kill -TERM "${pids[@]}" 2>/dev/null || true
        local poll_idx still_alive
        for poll_idx in {1..10}; do
            still_alive="false"
            for pid in "${pids[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    still_alive="true"
                    break
                fi
            done
            [[ "$still_alive" == "false" ]] && break
            sleep 0.1
        done

        local alive_pids=()
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                alive_pids+=("$pid")
            fi
        done
        if [[ "${#alive_pids[@]}" -gt 0 ]]; then
            kill -KILL "${alive_pids[@]}" 2>/dev/null || true
            for poll_idx in {1..10}; do
                still_alive="false"
                for pid in "${alive_pids[@]}"; do
                    if kill -0 "$pid" 2>/dev/null; then
                        still_alive="true"
                        break
                    fi
                done
                [[ "$still_alive" == "false" ]] && break
                sleep 0.1
            done
        fi
    fi
    return 0
}

mount_chroot_pseudo_fs() {
    local mount_root="${1:-${MOUNT_ROOT:-/mnt/novos}}"
    while [[ "$mount_root" == */ && "$mount_root" != "/" ]]; do
        mount_root="${mount_root%/}"
    done
    local real_mount_root=""
    if [[ -n "$mount_root" ]]; then
        real_mount_root="$(readlink -f "$mount_root" 2>/dev/null || echo "$mount_root")"
    fi
    if [[ -z "$mount_root" || "$mount_root" == "/" || -z "$real_mount_root" || "$real_mount_root" == "/" ]]; then
        echo "[FATAL] Refusing to mount chroot pseudo-filesystems on invalid root '${mount_root}'." >&2
        return 1
    fi

    mkdir -p "${mount_root}/proc" "${mount_root}/sys" "${mount_root}/dev" "${mount_root}/run" "${mount_root}/usr/sbin" "${mount_root}/etc"

    if ! findmnt -rn -M "${mount_root}/proc" >/dev/null 2>&1; then
        mount -t proc proc "${mount_root}/proc"
    fi
    if ! findmnt -rn -M "${mount_root}/sys" >/dev/null 2>&1; then
        mount --rbind /sys "${mount_root}/sys"
        mount --make-rslave "${mount_root}/sys"
    fi
    if ! findmnt -rn -M "${mount_root}/dev" >/dev/null 2>&1; then
        mount --rbind /dev "${mount_root}/dev"
        mount --make-rslave "${mount_root}/dev"
    fi
    if ! findmnt -rn -M "${mount_root}/run" >/dev/null 2>&1; then
        mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "${mount_root}/run"
    fi
    mkdir -p "${mount_root}/run/lock" "${mount_root}/run/shm" "${mount_root}/run/systemd" "${mount_root}/run/dbus"
    chmod 1777 "${mount_root}/run/lock" "${mount_root}/run/shm" 2>/dev/null || true

    if [[ -f /etc/resolv.conf ]]; then
        rm -f "${mount_root}/etc/resolv.conf"
        cp -L /etc/resolv.conf "${mount_root}/etc/resolv.conf"
    fi

    cat <<'EOF' > "${mount_root}/usr/sbin/policy-rc.d"
#!/bin/sh
exit 101
EOF
    chmod 0755 "${mount_root}/usr/sbin/policy-rc.d"

    if [[ -x "${mount_root}/usr/bin/dbus-daemon" && ! -e "${mount_root}/run/dbus/system_bus_socket" ]]; then
        rm -f "${mount_root}/run/dbus/pid" 2>/dev/null || true
        chroot "${mount_root}" /usr/bin/dbus-daemon --system --fork 2>/dev/null || true
    fi
}

unmount_chroot_pseudo_fs() {
    local mount_root="${1:-${MOUNT_ROOT:-/mnt/novos}}"
    while [[ "$mount_root" == */ && "$mount_root" != "/" ]]; do
        mount_root="${mount_root%/}"
    done
    if [[ -z "$mount_root" || "$mount_root" == "/" || ! -d "$mount_root" ]]; then
        return 0
    fi
    local real_mount_root
    real_mount_root="$(readlink -f "$mount_root" 2>/dev/null || echo "$mount_root")"
    if [[ -z "$real_mount_root" || "$real_mount_root" == "/" ]]; then
        return 0
    fi

    kill_chroot_processes "$mount_root"
    rm -f "${mount_root}/run/dbus/pid" "${mount_root}/run/dbus/system_bus_socket" 2>/dev/null || true
    rm -f "${mount_root}/usr/sbin/policy-rc.d" 2>/dev/null || true

    local sub target mnt
    for sub in run dev sys proc; do
        target="${mount_root}/${sub}"
        if [[ -d "$target" ]]; then
            mount --make-rslave "$target" 2>/dev/null || true
            while IFS= read -r mnt; do
                [[ -z "$mnt" ]] && continue
                mount --make-rslave "$mnt" 2>/dev/null || true
                umount "$mnt" 2>/dev/null || umount -R "$mnt" 2>/dev/null || umount -lf "$mnt" 2>/dev/null || true
            done < <(list_submounts "$target")
        fi
    done
    return 0
}

safe_remove_staging_dir() {
    local mount_root="${1:-${MOUNT_ROOT:-/mnt/novos}}"
    while [[ "$mount_root" == */ && "$mount_root" != "/" ]]; do
        mount_root="${mount_root%/}"
    done
    local real_mount_root=""
    if [[ -n "$mount_root" ]]; then
        real_mount_root="$(readlink -f "$mount_root" 2>/dev/null || echo "$mount_root")"
    fi
    if [[ -z "$mount_root" || "$mount_root" == "/" || -z "$real_mount_root" || "$real_mount_root" == "/" ]]; then
        echo "[FATAL] Refusing to remove invalid staging directory '${mount_root}'." >&2
        return 1
    fi
    if [[ ! -e "$mount_root" ]]; then
        return 0
    fi

    unmount_chroot_pseudo_fs "$mount_root"
    sync
    udevadm settle --timeout=5 2>/dev/null || true

    local mnt
    while IFS= read -r mnt; do
        [[ -z "$mnt" ]] && continue
        mount --make-rslave "$mnt" 2>/dev/null || true
        umount "$mnt" 2>/dev/null || umount -R "$mnt" 2>/dev/null || umount -lf "$mnt" 2>/dev/null || true
    done < <(list_submounts "$mount_root")

    local attempt
    for attempt in 1 2 3 4 5; do
        if [[ -z "$(list_submounts "$mount_root")" ]] && ! findmnt -rn -R "$mount_root" >/dev/null 2>&1; then
            break
        fi
        sleep 1
        while IFS= read -r mnt; do
            [[ -z "$mnt" ]] && continue
            mount --make-rslave "$mnt" 2>/dev/null || true
            umount "$mnt" 2>/dev/null || umount -R "$mnt" 2>/dev/null || umount -lf "$mnt" 2>/dev/null || true
        done < <(list_submounts "$mount_root")
        umount -lf "$mount_root" 2>/dev/null || true
    done

    local remaining_mounts
    remaining_mounts="$(list_submounts "$mount_root")"
    if [[ -z "$remaining_mounts" ]] && findmnt -rn -R "$mount_root" >/dev/null 2>&1; then
        remaining_mounts="$(findmnt -rn -R -o TARGET "$mount_root" 2>/dev/null || true)"
    fi
    if [[ -n "$remaining_mounts" ]]; then
        echo "[FATAL] Refusing to rm -rf '${mount_root}': active mountpoints remain attached:" >&2
        echo "$remaining_mounts" >&2
        return 1
    fi

    rm -rf --one-file-system "$mount_root"
}

# ------------------------------------------------------------------------------
# 2. Per-Stage Post-Condition Gate Functions
# ------------------------------------------------------------------------------

# Stage 1 Post-Gate: Mountpoints under <mount_root> (or bind-mounted staging directories in --target-iso mode)
verify_stage1_post() {
    local mount_root="${1:-${MOUNT_ROOT:-/mnt/novos}}"
    while [[ "$mount_root" == */ ]]; do
        mount_root="${mount_root%/}"
    done
    local errors=0

    local mountpoints=(
        "/"
        "/home"
        "/.snapshots"
        "/var/log"
        "/var/cache"
        "/var/lib/docker"
        "/var/lib/containerd"
        "/var/lib/flatpak"
        "/srv/llm"
        "/srv/devshare"
        "/boot/efi"
    )

    local is_iso_staging="false"
    if [[ "${TARGET_MODE:-}" == "iso" ]] || { findmnt -rn -M "${mount_root:-/}" >/dev/null 2>&1 && ! findmnt -rn -M "${mount_root}/boot/efi" >/dev/null 2>&1 && [[ -d "${mount_root}/boot/efi" && -d "${mount_root}/.snapshots" ]]; }; then
        is_iso_staging="true"
    fi

    local mp target
    for mp in "${mountpoints[@]}"; do
        if [[ "$mp" == "/" ]]; then
            target="${mount_root:-/}"
        else
            target="${mount_root}${mp}"
        fi

        if findmnt -rn -M "$target" >/dev/null 2>&1; then
            echo "[OK]   [Stage 1 Gate] Mountpoint active: ${target}"
        elif [[ "$is_iso_staging" == "true" && "$mp" != "/" && -d "$target" ]]; then
            echo "[OK]   [Stage 1 Gate] Staged directory active (ISO mode): ${target}"
        else
            echo "[FAIL] [Stage 1 Gate] Missing mountpoint/directory: ${target} (${mp})" >&2
            errors=$((errors + 1))
        fi
    done

    if [[ "$errors" -gt 0 ]]; then
        echo "[FATAL] Stage 1 post-gate failed with ${errors} error(s)." >&2
        return 1
    fi
    echo "[OK]   Stage 1 post-gate passed."
    return 0
}

# Stage 2 Post-Gate: Base system bootstrap, fstab (11 UUID= entries or ISO staging fstab), staged build.env, Cargo.toml & manifest.yaml
verify_stage2_post() {
    local mount_root="${1:-${MOUNT_ROOT:-/mnt/novos}}"
    while [[ "$mount_root" == */ ]]; do
        mount_root="${mount_root%/}"
    done
    local errors=0

    if [[ ! -x "${mount_root}/bin/bash" && ! -x "${mount_root}/usr/bin/bash" ]]; then
        echo "[FAIL] [Stage 2 Gate] Missing executable ${mount_root}/bin/bash" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 2 Gate] Verified ${mount_root}/bin/bash"
    fi

    local is_iso_staging="false"
    if [[ "${TARGET_MODE:-}" == "iso" ]] || ! findmnt -rn -M "${mount_root}/boot/efi" >/dev/null 2>&1; then
        is_iso_staging="true"
    fi

    local fstab="${mount_root}/etc/fstab"
    if [[ ! -f "$fstab" ]]; then
        echo "[FAIL] [Stage 2 Gate] Missing ${fstab}" >&2
        errors=$((errors + 1))
    else
        local uuid_count
        uuid_count="$(grep -vE '^[[:space:]]*#' "$fstab" | grep -c 'UUID=' || true)"
        if [[ "$uuid_count" -eq 11 ]]; then
            echo "[OK]   [Stage 2 Gate] Verified 11 UUID= entries in ${fstab}"
        elif [[ "$is_iso_staging" == "true" ]]; then
            echo "[OK]   [Stage 2 Gate] Verified ${fstab} (directory-staged ISO mode without block UUIDs)"
        else
            echo "[FAIL] [Stage 2 Gate] Expected 11 UUID= entries in ${fstab}, found ${uuid_count}" >&2
            errors=$((errors + 1))
        fi
    fi

    local staged_file
    for staged_file in \
        "${mount_root}/root/novos-build/config/build.env" \
        "${mount_root}/root/novos-build/Cargo.toml" \
        "${mount_root}/root/novos-build/custom-tools/manifest.yaml"; do
        if [[ ! -f "$staged_file" ]]; then
            echo "[FAIL] [Stage 2 Gate] Missing staged file ${staged_file}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 2 Gate] Verified ${staged_file}"
        fi
    done

    if [[ "$errors" -gt 0 ]]; then
        echo "[FATAL] Stage 2 post-gate failed with ${errors} error(s)." >&2
        return 1
    fi
    echo "[OK]   Stage 2 post-gate passed."
    return 0
}

# Stage 3 Post-Gate: Identity, APT pins, PAM systemd, Limine EFI, Dual Kernels, Snapshot Sync Hook
verify_stage3_post() {
    local root
    root="$(_check_root_prefix "${1:-}")"
    local errors=0

    if [[ ! -f "${root}/etc/os-release" ]] || ! grep -q '^ID=novos' "${root}/etc/os-release" || ! grep -q '^ID_LIKE=debian' "${root}/etc/os-release"; then
        echo "[FAIL] [Stage 3 Gate] ${root}/etc/os-release missing or does not contain ID=novos and ID_LIKE=debian" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 3 Gate] Verified ID=novos and ID_LIKE=debian in ${root}/etc/os-release"
    fi

    if [[ ! -f "${root}/etc/apt/preferences.d/00-protect-core" ]]; then
        echo "[FAIL] [Stage 3 Gate] Missing ${root}/etc/apt/preferences.d/00-protect-core" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 3 Gate] Verified ${root}/etc/apt/preferences.d/00-protect-core"
    fi

    if [[ ! -f "${root}/etc/pam.d/common-session" ]] || ! grep -q 'pam_systemd\.so' "${root}/etc/pam.d/common-session"; then
        echo "[FAIL] [Stage 3 Gate] pam_systemd.so missing from ${root}/etc/pam.d/common-session" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 3 Gate] Verified pam_systemd.so in ${root}/etc/pam.d/common-session"
    fi

    local required_files=(
        "/boot/efi/EFI/BOOT/BOOTX64.EFI"
        "/boot/efi/limine/limine-bios.sys"
        "/usr/local/bin/limine"
        "/usr/share/limine/limine-bios.sys"
        "/usr/share/limine/limine-bios-cd.bin"
        "/usr/share/limine/limine-uefi-cd.bin"
        "/boot/efi/kernels/vmlinuz-primary"
        "/boot/efi/kernels/initrd-primary.img"
        "/boot/efi/kernels/vmlinuz-lts-fallback"
        "/boot/efi/kernels/initrd-lts-fallback.img"
        "/usr/local/sbin/update-limine-snapshots"
    )

    local f
    for f in "${required_files[@]}"; do
        if [[ ! -f "${root}${f}" ]]; then
            echo "[FAIL] [Stage 3 Gate] Missing required file: ${root}${f}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 3 Gate] Verified file: ${root}${f}"
        fi
    done

    if [[ -f "${root}/usr/local/sbin/update-limine-snapshots" && ! -x "${root}/usr/local/sbin/update-limine-snapshots" ]]; then
        echo "[FAIL] [Stage 3 Gate] ${root}/usr/local/sbin/update-limine-snapshots is not executable" >&2
        errors=$((errors + 1))
    fi

    if ! _check_target_bin "$root" "hwclock"; then
        echo "[FAIL] [Stage 3 Gate] Missing binary: hwclock" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 3 Gate] Verified binary: hwclock"
    fi

    if [[ "$errors" -gt 0 ]]; then
        echo "[FATAL] Stage 3 post-gate failed with ${errors} error(s)." >&2
        return 1
    fi
    echo "[OK]   Stage 3 post-gate passed."
    return 0
}

# Stage 4 Post-Gate: COSMIC Desktop, Greeter, PAM, D-Bus policy, seatd.service masked
verify_stage4_post() {
    local root
    root="$(_check_root_prefix "${1:-}")"
    local errors=0

    local bins=(start-cosmic cosmic-comp cosmic-greeter tuigreet novos-slippery-tiles novos-custom-tools)
    local bin
    for bin in "${bins[@]}"; do
        if ! _check_target_bin "$root" "$bin"; then
            echo "[FAIL] [Stage 4 Gate] Missing binary: ${bin}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 4 Gate] Verified binary: ${bin}"
        fi
    done

    local files=(
        "/usr/local/bin/cosmic-greeter-app"
        "/usr/local/bin/novos-wallpaper-auto"
        "/usr/local/bin/novos-slippery-tiles"
        "/usr/local/bin/novos-custom-tools"
        "/etc/pam.d/cosmic-greeter"
        "/usr/share/dbus-1/system.d/com.system76.CosmicGreeter.conf"
        "/usr/share/backgrounds/novos/nova-wallpaper.png"
        "/usr/share/backgrounds/novos/nova-realistic-16x9.png"
        "/usr/share/backgrounds/novos/nova-realistic-32x9.png"
        "/usr/share/backgrounds/novos/nova-minimal-faceted-dark-16x9.png"
        "/usr/share/backgrounds/novos/nova-minimal-faceted-dark-32x9.png"
        "/usr/share/backgrounds/novos/nova-minimal-faceted-light-16x9.png"
        "/usr/share/backgrounds/novos/nova-minimal-faceted-light-32x9.png"
        "/usr/share/backgrounds/novos/nova-minimal-silhouette-dark-16x9.png"
        "/usr/share/backgrounds/novos/nova-minimal-silhouette-dark-32x9.png"
        "/usr/share/backgrounds/novos/nova-minimal-silhouette-light-16x9.png"
        "/usr/share/backgrounds/novos/nova-minimal-silhouette-light-32x9.png"
        "/usr/share/novos/themes/Dark.ron"
        "/usr/share/novos/themes/Light.ron"
        "/usr/share/pixmaps/novos-logo.png"
        "/etc/skel/.config/cosmic/com.system76.CosmicTheme.Dark.Builder/v1/accent"
        "/usr/share/cosmic/com.system76.CosmicTheme.Dark/v2/list_button"
        "/usr/share/cosmic/com.system76.CosmicTheme.Dark/v2/is_frosted"
        "/etc/skel/.config/cosmic/com.system76.CosmicBackground/v1/all"
        "/var/lib/cosmic-greeter/.config/cosmic/com.system76.CosmicBackground/v1/all"
    )
    local f
    for f in "${files[@]}"; do
        if [[ ! -f "${root}${f}" ]]; then
            echo "[FAIL] [Stage 4 Gate] Missing file: ${root}${f}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 4 Gate] Verified file: ${root}${f}"
        fi
    done

    if [[ ! -f "${root}/etc/greetd/config.toml" ]] || ! grep -q 'user = "cosmic-greeter"' "${root}/etc/greetd/config.toml"; then
        echo "[FAIL] [Stage 4 Gate] ${root}/etc/greetd/config.toml missing or does not contain user = \"cosmic-greeter\"" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 4 Gate] Verified user = \"cosmic-greeter\" in ${root}/etc/greetd/config.toml"
    fi

    if [[ ! -f "${root}/usr/local/bin/cosmic-greeter-app" ]] || ! grep -q 'exec /usr/bin/cosmic-greeter' "${root}/usr/local/bin/cosmic-greeter-app" || grep -q 'kill -TERM' "${root}/usr/local/bin/cosmic-greeter-app"; then
        echo "[FAIL] [Stage 4 Gate] ${root}/usr/local/bin/cosmic-greeter-app must contain 'exec /usr/bin/cosmic-greeter' and must not contain 'kill -TERM'" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 4 Gate] Verified clean kiosk exit in ${root}/usr/local/bin/cosmic-greeter-app"
    fi

    local seatd_masked="false"
    local unit_path
    for unit_path in "/etc/systemd/system/seatd.service" "/lib/systemd/system/seatd.service" "/usr/lib/systemd/system/seatd.service"; do
        if [[ "$(readlink "${root}${unit_path}" 2>/dev/null || true)" == "/dev/null" ]]; then
            seatd_masked="true"
            break
        fi
    done
    if [[ "$seatd_masked" != "true" && -z "$root" ]] && [[ "$(systemctl is-enabled seatd.service 2>/dev/null || true)" == "masked" ]]; then
        seatd_masked="true"
    fi

    if [[ "$seatd_masked" != "true" ]]; then
        echo "[FAIL] [Stage 4 Gate] seatd.service is not masked" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 4 Gate] Verified seatd.service is masked"
    fi

    if [[ "$errors" -gt 0 ]]; then
        echo "[FATAL] Stage 4 post-gate failed with ${errors} error(s)." >&2
        return 1
    fi
    echo "[OK]   Stage 4 post-gate passed."
    return 0
}

# Stage 5 Post-Gate: novos-setup, novos-llm-stack, novos-sec-box, novos-custom-tools, novos-slippery-tiles, docker-compose.yml, novos-env.sh
verify_stage5_post() {
    local root
    root="$(_check_root_prefix "${1:-}")"
    local errors=0

    local exec_files=(
        "/usr/local/bin/novos-setup"
        "/usr/local/bin/novos-llm-stack"
        "/usr/local/bin/novos-sec-box"
        "/usr/local/bin/novos-custom-tools"
        "/usr/local/bin/novos-slippery-tiles"
    )
    local ef
    for ef in "${exec_files[@]}"; do
        if [[ ! -x "${root}${ef}" ]]; then
            echo "[FAIL] [Stage 5 Gate] Missing or non-executable: ${root}${ef}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 5 Gate] Verified executable: ${root}${ef}"
        fi
    done

    local cfg_files=(
        "/srv/llm/docker-compose.yml"
        "/etc/calamares/modules/fstab.conf"
        "/etc/calamares/modules/machineid.conf"
        "/etc/profile.d/novos-env.sh"
        "/etc/novos/custom-tools.yaml"
        "/usr/share/novos/apt-markers/novos-profile-wifi_1.0_all.deb"
        "/usr/share/novos/apt-markers/novos-profile-bluetooth_1.0_all.deb"
        "/usr/share/novos/apt-markers/Packages"
        "/etc/apt/sources.list.d/novos-markers.list"
    )
    local cf
    for cf in "${cfg_files[@]}"; do
        if [[ ! -f "${root}${cf}" ]]; then
            echo "[FAIL] [Stage 5 Gate] Missing file: ${root}${cf}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 5 Gate] Verified file: ${root}${cf}"
        fi
    done

    if [[ ! -f "${root}/usr/local/bin/novos-setup" ]] || ! grep -q -- '--pre-packages' "${root}/usr/local/bin/novos-setup"; then
        echo "[FAIL] [Stage 5 Gate] ${root}/usr/local/bin/novos-setup missing '--pre-packages' handler" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 5 Gate] Verified '--pre-packages' in ${root}/usr/local/bin/novos-setup"
    fi

    local sp_rel
    for sp_rel in "/etc/calamares/modules/shellprocess-prepackages.conf" "/etc/calamares/modules/shellprocess-novos.conf"; do
        local sp_target="${root}${sp_rel}"
        if [[ ! -f "$sp_target" && -f "${VERIFY_REPO_ROOT}/calamares/modules/$(basename "$sp_rel")" ]]; then
            sp_target="${VERIFY_REPO_ROOT}/calamares/modules/$(basename "$sp_rel")"
        fi
        if [[ ! -f "$sp_target" ]] || grep -q '\$' "$sp_target"; then
            echo "[FAIL] [Stage 5 Gate] Missing or contains forbidden '\$' in ${sp_target}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 5 Gate] Verified zero '\$' characters in ${sp_target}"
        fi
    done

    if [[ "$errors" -gt 0 ]]; then
        echo "[FATAL] Stage 5 post-gate failed with ${errors} error(s)." >&2
        return 1
    fi
    echo "[OK]   Stage 5 post-gate passed."
    return 0
}

# Stage 6 Post-Gate: Full binary/file integrity + Golden Master snapshot + Limine root=UUID=
verify_stage6_post() {
    local root
    root="$(_check_root_prefix "${1:-}")"
    local errors=0

    local required_bins=(
        apt dpkg start-cosmic cosmic-comp cosmic-greeter
        tuigreet snapper btrfs zstd fsck.vfat hwclock
    )
    local bin
    for bin in "${required_bins[@]}"; do
        if ! _check_target_bin "$root" "$bin"; then
            echo "[FAIL] [Stage 6 Gate] Missing binary: ${bin}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 6 Gate] Verified binary: ${bin}"
        fi
    done

    local required_files=(
        "/boot/efi/EFI/BOOT/BOOTX64.EFI"
        "/boot/efi/limine/limine-bios.sys"
        "/usr/local/bin/limine"
        "/usr/share/limine/BOOTX64.EFI"
        "/usr/share/limine/limine-bios.sys"
        "/usr/share/limine/limine-bios-cd.bin"
        "/usr/share/limine/limine-uefi-cd.bin"
        "/boot/efi/kernels/vmlinuz-primary"
        "/boot/efi/kernels/initrd-primary.img"
        "/boot/efi/kernels/vmlinuz-lts-fallback"
        "/boot/efi/kernels/initrd-lts-fallback.img"
        "/etc/pam.d/cosmic-greeter"
        "/etc/pam.d/greetd"
        "/usr/share/dbus-1/system.d/com.system76.CosmicGreeter.conf"
        "/usr/local/bin/cosmic-greeter-launch"
        "/usr/local/bin/cosmic-session-launch"
        "/usr/local/bin/novos-wallpaper-auto"
        "/usr/local/bin/novos-setup"
        "/usr/local/bin/novos-llm-stack"
        "/usr/local/bin/novos-slippery-tiles"
        "/usr/local/bin/novos-custom-tools"
        "/usr/local/bin/novos-sec-box"
        "/usr/local/sbin/update-limine-snapshots"
        "/srv/llm/docker-compose.yml"
        "/etc/profile.d/novos-env.sh"
        "/etc/novos/custom-tools.yaml"
        "/etc/apt/preferences.d/00-protect-core"
        "/etc/os-release"
        "/etc/lsb-release"
        "/etc/issue"
        "/boot/efi/limine/limine.conf"
        "/boot/efi/EFI/BOOT/limine.conf"
    )
    local f
    for f in "${required_files[@]}"; do
        if [[ ! -f "${root}${f}" ]]; then
            echo "[FAIL] [Stage 6 Gate] Missing file: ${root}${f}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 6 Gate] Verified file: ${root}${f}"
        fi
    done

    if [[ ! -f "${root}/etc/os-release" ]] || ! grep -q '^ID=novos' "${root}/etc/os-release"; then
        echo "[FAIL] [Stage 6 Gate] ${root}/etc/os-release does not identify as ID=novos" >&2
        errors=$((errors + 1))
    fi

    if [[ ! -f "${root}/etc/pam.d/common-session" ]] || ! grep -q 'pam_systemd\.so' "${root}/etc/pam.d/common-session"; then
        echo "[FAIL] [Stage 6 Gate] pam_systemd.so missing from ${root}/etc/pam.d/common-session" >&2
        errors=$((errors + 1))
    fi

    local is_iso_staging="false"
    if [[ "${TARGET_MODE:-}" == "iso" ]] || ! findmnt -rn -M "${root:-/}/boot/efi" >/dev/null 2>&1; then
        is_iso_staging="true"
    fi

    if [[ -d "${root}/.snapshots/1" ]]; then
        echo "[FAIL] [Stage 6 Gate] Build-time snapshot directory must not exist during build/Live state: ${root}/.snapshots/1" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 6 Gate] Verified zero build-time snapshots in ${root}/.snapshots"
    fi

    local active_hook
    for active_hook in "/etc/apt/apt.conf.d/80snapper" "/etc/apt/apt.conf.d/99-limine-snapshots"; do
        if [[ -e "${root}${active_hook}" ]]; then
            echo "[FAIL] [Stage 6 Gate] Snapshot APT hook must not be active during build/Live state: ${root}${active_hook}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 6 Gate] Verified snapshot APT hook is inactive: ${root}${active_hook}"
        fi
    done

    local disabled_hook
    for disabled_hook in "/usr/share/novos/80snapper.disabled" "/usr/share/novos/99-limine-snapshots.disabled"; do
        if [[ ! -f "${root}${disabled_hook}" ]]; then
            echo "[FAIL] [Stage 6 Gate] Missing staged disabled snapshot hook for target installation: ${root}${disabled_hook}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 6 Gate] Verified staged disabled snapshot hook: ${root}${disabled_hook}"
        fi
    done

    if [[ -f "${root}/boot/efi/limine/limine.conf" ]] && grep -qE 'root=UUID=[0-9a-fA-F-]+' "${root}/boot/efi/limine/limine.conf"; then
        echo "[OK]   [Stage 6 Gate] Verified root=UUID= in ${root}/boot/efi/limine/limine.conf"
    elif [[ "$is_iso_staging" == "true" && -s "${root}/boot/efi/limine/limine.conf" ]]; then
        echo "[OK]   [Stage 6 Gate] Verified ${root}/boot/efi/limine/limine.conf (directory-staged ISO mode)"
    else
        echo "[FAIL] [Stage 6 Gate] ${root}/boot/efi/limine/limine.conf missing or empty" >&2
        errors=$((errors + 1))
    fi

    if [[ ! -f "${root}/usr/local/bin/novos-setup" ]] || ! grep -q -- '--pre-packages' "${root}/usr/local/bin/novos-setup"; then
        echo "[FAIL] [Stage 6 Gate] ${root}/usr/local/bin/novos-setup missing '--pre-packages' handler" >&2
        errors=$((errors + 1))
    else
        echo "[OK]   [Stage 6 Gate] Verified '--pre-packages' in ${root}/usr/local/bin/novos-setup"
    fi

    local sp_rel
    for sp_rel in "/etc/calamares/modules/shellprocess-prepackages.conf" "/etc/calamares/modules/shellprocess-novos.conf"; do
        local sp_target="${root}${sp_rel}"
        if [[ ! -f "$sp_target" && -f "${VERIFY_REPO_ROOT}/calamares/modules/$(basename "$sp_rel")" ]]; then
            sp_target="${VERIFY_REPO_ROOT}/calamares/modules/$(basename "$sp_rel")"
        fi
        if [[ ! -f "$sp_target" ]] || grep -q '\$' "$sp_target"; then
            echo "[FAIL] [Stage 6 Gate] Missing or contains forbidden '\$' in ${sp_target}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 6 Gate] Verified zero '\$' characters in ${sp_target}"
        fi
    done

    if [[ "$errors" -gt 0 ]]; then
        echo "[FATAL] Stage 6 post-gate failed with ${errors} error(s)." >&2
        return 1
    fi
    echo "[OK]   Stage 6 post-gate passed."
    return 0
}

# Stage 7 Post-Gate: Live ISO non-empty size + SquashFS FHS mountpoint directories + required Calamares files in repository
verify_stage7_post() {
    local iso_path="${1:-${ISO_PATH:-${VERIFY_REPO_ROOT}/build/NovOS-sid-amd64.iso}}"
    local errors=0

    local calamares_files=(
        "calamares/settings.conf"
        "calamares/branding/novos/branding.desc"
        "calamares/branding/novos/stylesheet.qss"
        "calamares/modules/mount.conf"
        "calamares/modules/machineid.conf"
        "calamares/modules/fstab.conf"
        "calamares/modules/partition.conf"
        "calamares/modules/unpackfs.conf"
        "calamares/modules/users.conf"
        "calamares/modules/packages.conf"
        "calamares/modules/welcome.conf"
        "calamares/modules/netinstall.conf"
        "calamares/modules/netinstall.yaml"
        "calamares/modules/shellprocess-prepackages.conf"
        "calamares/modules/shellprocess-novos.conf"
        "calamares/modules/limine-bootloader/module.desc"
        "calamares/modules/limine-bootloader/main.py"
        "branding/novos-logo.png"
        "branding/nova-wallpaper.png"
    )

    local cf
    for cf in "${calamares_files[@]}"; do
        if [[ ! -f "${VERIFY_REPO_ROOT}/${cf}" ]]; then
            echo "[FAIL] [Stage 7 Gate] Missing required Calamares/branding file in repo: ${cf}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 7 Gate] Verified repo file: ${cf}"
        fi
    done

    local squashfs_path="${VERIFY_REPO_ROOT}/build/iso_work/live/filesystem.squashfs"
    if [[ -f "$squashfs_path" ]] && command -v unsquashfs >/dev/null 2>&1; then
        local sq_listing
        sq_listing="$(unsquashfs -ls "$squashfs_path" 2>/dev/null || true)"
        local req_dir
        for req_dir in "squashfs-root/dev" "squashfs-root/proc" "squashfs-root/sys" "squashfs-root/run" "squashfs-root/tmp"; do
            if ! grep -qxE "${req_dir}/?" <<<"$sq_listing"; then
                echo "[FAIL] [Stage 7 Gate] Missing required directory in SquashFS (${squashfs_path}): ${req_dir}" >&2
                errors=$((errors + 1))
            else
                echo "[OK]   [Stage 7 Gate] Verified directory in SquashFS: ${req_dir}"
            fi
        done
    fi

    if [[ ! -f "$iso_path" ]]; then
        echo "[FAIL] [Stage 7 Gate] Live ISO file not found: ${iso_path}" >&2
        errors=$((errors + 1))
    else
        local iso_size
        iso_size="$(stat -c '%s' "$iso_path" 2>/dev/null || echo 0)"
        # Minimum sanity size check (> 64 MiB for a bootable Live ISO)
        if [[ "$iso_size" -lt 67108864 ]]; then
            echo "[FAIL] [Stage 7 Gate] Live ISO file is unexpectedly small (${iso_size} bytes < 64 MiB): ${iso_path}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 7 Gate] Verified Live ISO (${iso_size} bytes): ${iso_path}"
        fi

        local mbr_sig
        mbr_sig="$(python3 -c '
import sys
with open(sys.argv[1], "rb") as f:
    f.seek(510)
    print(f.read(2).hex().lower())
' "$iso_path" 2>/dev/null || true)"
        if [[ "$mbr_sig" != "55aa" ]]; then
            echo "[FAIL] [Stage 7 Gate] Live ISO missing Hybrid MBR/GPT boot signature (expected 55aa at bytes 510..511, got '${mbr_sig:-none}'): ${iso_path}" >&2
            errors=$((errors + 1))
        else
            echo "[OK]   [Stage 7 Gate] Verified Hybrid MBR/GPT boot signature (55aa) at bytes 510..511: ${iso_path}"
        fi
    fi

    if [[ "$errors" -gt 0 ]]; then
        echo "[FATAL] Stage 7 post-gate failed with ${errors} error(s)." >&2
        return 1
    fi
    echo "[OK]   Stage 7 post-gate passed."
    return 0
}

# ------------------------------------------------------------------------------
# 3. Self-Test Runner (--self-test)
# ------------------------------------------------------------------------------
run_self_test() {
    echo "==> [Self-Test] Validating Bash syntax (bash -n) across scripts/..."
    local sh_files=()
    local f
    for f in "${VERIFY_REPO_ROOT}"/scripts/*.sh "${VERIFY_REPO_ROOT}"/scripts/stages/*.sh "${VERIFY_REPO_ROOT}"/scripts/lib/*.sh; do
        if [[ -f "$f" ]]; then
            sh_files+=("$f")
        fi
    done

    for f in "${sh_files[@]}"; do
        bash -n "$f"
        echo "[OK]   bash -n: ${f#"${VERIFY_REPO_ROOT}/"}"
    done

    echo "==> [Self-Test] Validating Python syntax (py_compile) across scripts/ and calamares/..."
    local py_files=()
    while IFS= read -r -d '' pyf; do
        py_files+=("$pyf")
    done < <(find "${VERIFY_REPO_ROOT}/scripts" "${VERIFY_REPO_ROOT}/calamares" -type f -name "*.py" -print0 2>/dev/null || true)

    local pycache_tmp="${TMPDIR:-/tmp}/novos-pycache-$$"
    for pyf in "${py_files[@]}"; do
        PYTHONPYCACHEPREFIX="$pycache_tmp" python3 -m py_compile "$pyf"
        echo "[OK]   py_compile: ${pyf#"${VERIFY_REPO_ROOT}/"}"
    done
    rm -rf "$pycache_tmp" 2>/dev/null || true

    echo "==> [Self-Test] Validating YAML syntax (yaml.safe_load) across calamares/ and custom-tools/..."
    if [[ -d "${VERIFY_REPO_ROOT}/calamares" ]]; then
        local yaml_files=()
        while IFS= read -r -d '' yf; do
            yaml_files+=("$yf")
        done < <(find "${VERIFY_REPO_ROOT}/calamares" "${VERIFY_REPO_ROOT}/custom-tools" -type f \( -name "*.yaml" -o -name "*.yml" -o -name "*.conf" \) -print0 2>/dev/null)

        for yf in "${yaml_files[@]}"; do
            python3 -c '
import sys, yaml
with open(sys.argv[1], "r", encoding="utf-8") as fh:
    yaml.safe_load(fh)
' "$yf"
            echo "[OK]   yaml.safe_load: ${yf#"${VERIFY_REPO_ROOT}/"}"
        done

        # Verify Calamares netinstall.yaml package names & shellprocess DictionaryExpander invariants
        python3 -c '
import glob, os, re, subprocess, sys, yaml
(
    netinstall_path,
    prepackages_path,
    mount_path,
    machineid_path,
    novos_sp_path,
    partition_path,
    limine_py_path,
    settings_path,
    test_qemu_path,
) = sys.argv[1:10]
with open(netinstall_path, "r", encoding="utf-8") as fh:
    groups = yaml.safe_load(fh) or []
pkgs = set()
for grp in groups:
    pkgs.update(grp.get("packages", []) or [])
    for sub in grp.get("subgroups", []) or []:
        pkgs.update(sub.get("packages", []) or [])
forbidden = {"antigravity-cli", "helix"} & pkgs
if forbidden:
    raise SystemExit(f"Forbidden invalid APT package(s) in netinstall.yaml: {sorted(forbidden)}")
if "hx" not in pkgs or "antigravity" not in pkgs:
    raise SystemExit("Expected hx and antigravity in netinstall.yaml")

sp_dir = os.path.dirname(prepackages_path)
sp_paths = sorted(set([prepackages_path, novos_sp_path] + glob.glob(os.path.join(sp_dir, "shellprocess*.conf"))))
for sp_path in sp_paths:
    with open(sp_path, "r", encoding="utf-8") as fh:
        text = fh.read()
        sp_cfg = yaml.safe_load(text) or {}
    assert "$" not in text, f"Forbidden $ character in {sp_path} (breaks Calamares DictionaryExpander)"
    assert set(re.findall(r"@@([^@]+)@@", text)) <= {"ROOT", "username"}, f"Forbidden Calamares macro in {sp_path}"
    for entry in sp_cfg.get("script") or []:
        cmd = entry.get("command") if isinstance(entry, dict) else entry
        if isinstance(cmd, str):
            subprocess.run(["sh", "-n", "-c", cmd], check=True)

with open(prepackages_path, "r", encoding="utf-8") as fh:
    pre_text = fh.read()
if "/usr/local/bin/novos-setup --pre-packages" not in pre_text:
    raise SystemExit("Missing required /usr/local/bin/novos-setup --pre-packages in shellprocess-prepackages.conf")

with open(mount_path, "r", encoding="utf-8") as fh:
    mount_cfg = yaml.safe_load(fh) or {}
mounts = {m.get("mountPoint"): m for m in (mount_cfg.get("extraMounts") or [])}
if mounts.get("/dev/pts", {}).get("fs") != "bind" or mounts.get("/run/udev", {}).get("fs") != "bind":
    raise SystemExit("mount.conf must bind-mount both /dev/pts and /run/udev")

with open(machineid_path, "r", encoding="utf-8") as fh:
    mid_cfg = yaml.safe_load(fh) or {}
if not mid_cfg.get("systemd"):
    raise SystemExit("machineid.conf must enable systemd: true")

with open(novos_sp_path, "r", encoding="utf-8") as fh:
    novos_sp_text = fh.read()
if "@@USER@@" in novos_sp_text:
    raise SystemExit("Forbidden @@USER@@ placeholder found in shellprocess-novos.conf")
if "@@username@@" not in novos_sp_text:
    raise SystemExit("Missing required @@username@@ placeholder in shellprocess-novos.conf")

with open(partition_path, "r", encoding="utf-8") as fh:
    part_cfg = yaml.safe_load(fh) or {}
if str(part_cfg.get("efiSystemPartitionSize", "")).strip() != "6144M":
    raise SystemExit("partition.conf must set efiSystemPartitionSize: 6144M")

with open(limine_py_path, "r", encoding="utf-8") as fh:
    limine_py_text = fh.read()
if "6144MiB" not in limine_py_text or "-6228M" not in limine_py_text:
    raise SystemExit("limine-bootloader/main.py must contain 6144MiB and -6228M")
if "2048MiB" in limine_py_text:
    raise SystemExit("limine-bootloader/main.py must not contain 2048MiB")

with open(settings_path, "r", encoding="utf-8") as fh:
    settings_cfg = yaml.safe_load(fh) or {}
mod_search = settings_cfg.get("modules-search") or []
if "/usr/lib/x86_64-linux-gnu/calamares/modules" not in mod_search:
    raise SystemExit("settings.conf modules-search must include /usr/lib/x86_64-linux-gnu/calamares/modules")

with open(test_qemu_path, "r", encoding="utf-8") as fh:
    tq_text = fh.read()
for required_tok in (
    "qemu-hw-display-virtio-vga",
    "qemu-hw-display-virtio-vga-gl",
    "qemu-hw-display-virtio-gpu",
    "qemu-hw-display-virtio-gpu-pci",
    "virtio-gpu-gl-pci",
    "-vga std",
    "novos-calamares-target.img",
    "discard=unmap",
):
    if required_tok not in tq_text:
        raise SystemExit(f"test-qemu.sh missing required token: {required_tok}")
' \
            "${VERIFY_REPO_ROOT}/calamares/modules/netinstall.yaml" \
            "${VERIFY_REPO_ROOT}/calamares/modules/shellprocess-prepackages.conf" \
            "${VERIFY_REPO_ROOT}/calamares/modules/mount.conf" \
            "${VERIFY_REPO_ROOT}/calamares/modules/machineid.conf" \
            "${VERIFY_REPO_ROOT}/calamares/modules/shellprocess-novos.conf" \
            "${VERIFY_REPO_ROOT}/calamares/modules/partition.conf" \
            "${VERIFY_REPO_ROOT}/calamares/modules/limine-bootloader/main.py" \
            "${VERIFY_REPO_ROOT}/calamares/settings.conf" \
            "${VERIFY_REPO_ROOT}/scripts/test-qemu.sh"
        echo "[OK]   Calamares and QEMU harness invariants verified (netinstall.yaml packages, prepackages debconf preseeds, mount.conf bind mounts, machineid.conf, shellprocess-novos.conf, partition.conf 6144M, limine-bootloader 6144MiB/-6228M, settings.conf modules-search, test-qemu.sh display/disk)."
    fi
    if [[ -x "${VERIFY_REPO_ROOT}/target/release/novos-custom-tools" && -f "${VERIFY_REPO_ROOT}/custom-tools/manifest.yaml" ]]; then
        "${VERIFY_REPO_ROOT}/target/release/novos-custom-tools" --validate "${VERIFY_REPO_ROOT}/custom-tools/manifest.yaml" >/dev/null
        echo "[OK]   novos-custom-tools --validate: custom-tools/manifest.yaml"
    fi

    echo "==> [Self-Test] Validating Host-Safety Invariants across scripts/..."
    local stage_and_top_scripts=()
    for f in "${VERIFY_REPO_ROOT}"/scripts/*.sh "${VERIFY_REPO_ROOT}"/scripts/stages/*.sh; do
        if [[ -f "$f" ]]; then
            stage_and_top_scripts+=("$f")
        fi
    done

    for f in "${stage_and_top_scripts[@]}"; do
        if grep -nE 'fuser[[:space:]]+-k[[:alpha:]]*m|fuser[[:space:]]+-m[[:alpha:]]*k' "$f" >/dev/null 2>&1; then
            echo "[FAIL] [Self-Test] Host-destabilizing 'fuser -km' detected in ${f#"${VERIFY_REPO_ROOT}/"}" >&2
            return 1
        fi
        if grep -nE 'mount[[:space:]]+--bind[[:space:]]+/run([[:space:]]|$)' "$f" >/dev/null 2>&1; then
            echo "[FAIL] [Self-Test] Host-destabilizing 'mount --bind /run' detected in ${f#"${VERIFY_REPO_ROOT}/"}" >&2
            return 1
        fi
    done
    kill_chroot_processes "/"
    kill_chroot_processes "/nonexistent-novos-chroot-$$"
    if [[ -n "$(list_submounts "/")" ]] || [[ -n "$(list_submounts "/nonexistent-novos-chroot-$$")" ]]; then
        echo "[FAIL] [Self-Test] list_submounts returned non-empty output for '/' or nonexistent path!" >&2
        return 1
    fi
    if [[ -d "/sys/fs" ]] && [[ -z "$(list_submounts "/sys/fs")" ]]; then
        echo "[FAIL] [Self-Test] list_submounts failed to detect nested submounts under non-mountpoint parent '/sys/fs'!" >&2
        return 1
    fi
    echo "[OK]   Host-safety invariants verified (no 'fuser -km', no 'mount --bind /run', safe chroot process guard, prefix submount detection)."

    echo "==> [Self-Test] All syntax and schema checks passed."
}

# ------------------------------------------------------------------------------
# CLI Entrypoint
# ------------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        --self-test)
            run_self_test
            ;;
        --host-deps)
            verify_host_deps "${2:-iso}"
            ;;
        --stage1-post)
            verify_stage1_post "${2:-}"
            ;;
        --stage2-post)
            verify_stage2_post "${2:-}"
            ;;
        --stage3-post)
            verify_stage3_post "${2:-}"
            ;;
        --stage4-post)
            verify_stage4_post "${2:-}"
            ;;
        --stage5-post)
            verify_stage5_post "${2:-}"
            ;;
        --stage6-post)
            verify_stage6_post "${2:-}"
            ;;
        --stage7-post)
            verify_stage7_post "${2:-}"
            ;;
        *)
            echo "Usage: $0 {--self-test|--host-deps <iso|qemu>|--stage[1-7]-post [mount_root|iso_path]}" >&2
            exit 1
            ;;
    esac
fi
