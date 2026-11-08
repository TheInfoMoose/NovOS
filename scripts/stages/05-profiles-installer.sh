#!/usr/bin/env bash
# ==============================================================================
# Stage 5 (Inside Chroot): Modular Profile Provisioner (novos-setup),
# Custom-Tools Integration (novos-custom-tools) & DevSecOps Kali Box (novos-sec-box)
# ==============================================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export DEBIAN_FRONTEND=noninteractive
# Image-build context: novos-setup run from this stage (5.4b) treats a missing package as fatal.
export NOVOS_CONTEXT=build

source /root/novos-build/config/build.env
source /root/novos-build/scripts/lib/verify-deps.sh

REPO_ROOT="/root/novos-build"

echo "==> [Stage 5.1] Configuring Anti-Bloat LLM & Developer Environment Paths..."
mkdir -p /srv/llm/{ollama,huggingface,comfyui,open-webui,models} /srv/devshare /etc/novos /etc/novos/custom-tools /usr/share/novos/custom-tools
ln -sfn /srv/llm/ollama /var/lib/ollama
chown -R "${DEFAULT_USER}:${DEFAULT_USER}" /srv/llm /srv/devshare
chmod 2775 /srv/llm /srv/devshare

# Global environment variables so Hugging Face & Ollama models always land on @llm (/srv/llm)
cat << 'EOF' > /etc/profile.d/novos-env.sh
export HF_HOME="/srv/llm/huggingface"
export OLLAMA_MODELS="/srv/llm/ollama/models"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.ghcup/bin:/usr/local/sbin:/usr/local/bin:$PATH"
EOF
chmod +x /etc/profile.d/novos-env.sh

echo "==> [Stage 5.1b] Provisioning novos-custom-tools Binary, /etc/novos/custom-tools.yaml & /etc/novos/packages.yaml..."
# A staged binary that predates the `packages` subcommands cannot serve novos-setup; rebuild it.
if ! ([[ -x "${REPO_ROOT}/target/release/novos-custom-tools" ]] && \
      "${REPO_ROOT}/target/release/novos-custom-tools" --help >/dev/null 2>&1 && \
      "${REPO_ROOT}/target/release/novos-custom-tools" packages validate "${REPO_ROOT}/config/packages.yaml" >/dev/null 2>&1); then
    if ! command -v cargo >/dev/null 2>&1; then
        apt-get update
        apt-get -o APT::Get::AutomaticRemove=false -o APT::AutoRemove::SuggestsImportant=false install -y cargo rustc
    fi
    cargo build --release --offline --manifest-path "${REPO_ROOT}/Cargo.toml" || \
        cargo build --release --manifest-path "${REPO_ROOT}/Cargo.toml"
fi

install -m 0755 "${REPO_ROOT}/target/release/novos-custom-tools" /usr/local/bin/novos-custom-tools
cp -f "${REPO_ROOT}/custom-tools/manifest.yaml" /etc/novos/custom-tools.yaml
cp -f "${REPO_ROOT}/custom-tools/manifest.yaml" /etc/novos/custom-tools/manifest.yaml
cp -f "${REPO_ROOT}/custom-tools/manifest.yaml" /usr/share/novos/custom-tools/manifest.yaml
# Unified package manifest: novos-setup profile installers read their package lists from here.
install -m 0644 "${REPO_ROOT}/config/packages.yaml" /etc/novos/packages.yaml
/usr/local/bin/novos-custom-tools packages validate /etc/novos/packages.yaml

echo "==> [Stage 5.1d] Build-Time Package Availability Gate (every declared apt package must resolve)..."
# i386 entries (wine32:i386, libwine:i386) resolve only when Stage 3 registered the foreign architecture.
if ! dpkg --print-foreign-architectures | grep -qx i386; then
    echo "[FATAL] dpkg foreign architecture i386 is not registered (Stage 3 'dpkg --add-architecture i386'); i386 manifest entries cannot resolve." >&2
    exit 1
fi
# Fresh package indexes (amd64 + i386) for the configured sid/third-party sources; failure is fatal.
apt-get update
# check-apt exit codes: 0 all resolvable, 3 unresolvable packages (listed on stdout), 1/2 runtime/usage error.
CHECK_APT_RC=0
UNRESOLVABLE_PKGS="$(/usr/local/bin/novos-custom-tools packages check-apt /etc/novos/packages.yaml --all)" || CHECK_APT_RC=$?
case "$CHECK_APT_RC" in
    0)
        echo "[OK]   All declared apt packages in /etc/novos/packages.yaml resolve."
        ;;
    3)
        echo "[FATAL] Declared packages unavailable in sid/third-party repos:" >&2
        while IFS= read -r unresolvable_pkg; do
            [[ -n "$unresolvable_pkg" ]] && echo "  - ${unresolvable_pkg}" >&2
        done <<< "$UNRESOLVABLE_PKGS"
        exit 1
        ;;
    *)
        echo "[FATAL] novos-custom-tools packages check-apt /etc/novos/packages.yaml --all failed (exit ${CHECK_APT_RC})." >&2
        exit 1
        ;;
esac

echo "==> [Stage 5.1c] Building Offline APT Repository (/usr/share/novos/apt-markers) for the novos-profile-wifi / novos-profile-bluetooth Meta Packages..."
MARKER_REPO="/usr/share/novos/apt-markers"
MARKER_BUILD_TMP="$(mktemp -d)"
# rm -f is already a no-op for absent paths; any other failure is a real build error.
rm -f /etc/apt/sources.list.d/novos-markers.list /var/lib/apt/lists/*apt-markers*
mkdir -p -m 0755 "$MARKER_REPO"
rm -f "${MARKER_REPO}/"*.deb "${MARKER_REPO}/Packages" "${MARKER_REPO}/Packages.gz"
: > "${MARKER_REPO}/Packages"

build_marker_deb() {
    local pkg_name="$1"
    local pkg_ver="$2"
    local pkg_desc="$3"
    local pkg_dir="${MARKER_BUILD_TMP}/${pkg_name}"
    mkdir -p "${pkg_dir}/DEBIAN" "${pkg_dir}/usr/share/novos/markers"
    printf 'enabled\n' > "${pkg_dir}/usr/share/novos/markers/${pkg_name}"
    cat <<CTRL > "${pkg_dir}/DEBIAN/control"
Package: ${pkg_name}
Version: ${pkg_ver}
Section: metapackages
Priority: optional
Architecture: all
Maintainer: NovOS Team <dev@novos.local>
Description: ${pkg_desc}
CTRL
    chmod -R 0755 "${pkg_dir}"
    chmod 0644 "${pkg_dir}/DEBIAN/control" "${pkg_dir}/usr/share/novos/markers/${pkg_name}"
    local deb_file="${pkg_name}_${pkg_ver}_all.deb"
    dpkg-deb --root-owner-group --build "${pkg_dir}" "${MARKER_REPO}/${deb_file}" >/dev/null
    local deb_size deb_md5 deb_sha256
    deb_size="$(stat -c '%s' "${MARKER_REPO}/${deb_file}")"
    deb_md5="$(md5sum "${MARKER_REPO}/${deb_file}" | awk '{print $1}')"
    deb_sha256="$(sha256sum "${MARKER_REPO}/${deb_file}" | awk '{print $1}')"
    cat <<PKG >> "${MARKER_REPO}/Packages"
Package: ${pkg_name}
Version: ${pkg_ver}
Section: metapackages
Priority: optional
Architecture: all
Maintainer: NovOS Team <dev@novos.local>
Filename: ./${deb_file}
Size: ${deb_size}
MD5sum: ${deb_md5}
SHA256: ${deb_sha256}
Description: ${pkg_desc}

PKG
}

for spec in \
    "novos-profile-wifi:NovOS Calamares WiFi Profile Selection Marker" \
    "novos-profile-bluetooth:NovOS Calamares Bluetooth Profile Selection Marker"; do
    build_marker_deb "${spec%%:*}" "1.0" "${spec#*:}"
done

gzip -9c "${MARKER_REPO}/Packages" > "${MARKER_REPO}/Packages.gz"
chmod 0644 "${MARKER_REPO}/"*
rm -rf "$MARKER_BUILD_TMP"

mkdir -p /etc/apt/sources.list.d
cat << 'EOF' > /etc/apt/sources.list.d/novos-markers.list
deb [trusted=yes] file:/usr/share/novos/apt-markers ./
EOF
chmod 0644 /etc/apt/sources.list.d/novos-markers.list
# The marker repo must index cleanly; a failed update is fatal.
apt-get update -q

echo "==> [Stage 5.2] Writing Containerized ComfyUI & Open WebUI Stack (RX 9070 XT RDNA4 Ready)..."
cat << 'EOF' > /srv/llm/docker-compose.yml
name: novos-llm-stack

services:
  comfyui:
    image: yanwk/comfyui-boot:rocm
    container_name: novos-comfyui
    restart: unless-stopped
    ports:
      - "8188:8188"
    devices:
      - "/dev/kfd:/dev/kfd"
      - "/dev/dri:/dev/dri"
    group_add:
      - video
      - render
    ipc: host
    security_opt:
      - seccomp:unconfined
    environment:
      - HSA_OVERRIDE_GFX_VERSION=12.0.1
      - PYTORCH_ROCM_ARCH=gfx1201
      - HF_HOME=/root/.cache/huggingface
      - CLI_ARGS=--listen 0.0.0.0 --port 8188
    volumes:
      - /srv/llm/comfyui/storage:/root
      - /srv/llm/huggingface:/root/.cache/huggingface
      - /srv/llm/models:/srv/models

  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: novos-open-webui
    restart: unless-stopped
    ports:
      - "3000:8080"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
    volumes:
      - /srv/llm/open-webui:/app/backend/data
EOF
chown "${DEFAULT_USER}:${DEFAULT_USER}" /srv/llm/docker-compose.yml

cat << 'EOF' > /usr/local/bin/novos-llm-stack
#!/usr/bin/env bash
set -euo pipefail
COMPOSE_FILE="/srv/llm/docker-compose.yml"
ACTION="${1:-status}"

case "$ACTION" in
    up|start)
        mkdir -p /srv/llm/comfyui/storage /srv/llm/open-webui /srv/llm/huggingface /srv/llm/models
        docker compose -f "$COMPOSE_FILE" up -d
        echo "ComfyUI:    http://localhost:8188"
        echo "Open WebUI: http://localhost:3000"
        ;;
    down|stop)
        docker compose -f "$COMPOSE_FILE" down
        ;;
    pull|update)
        docker compose -f "$COMPOSE_FILE" pull
        ;;
    logs)
        docker compose -f "$COMPOSE_FILE" logs -f
        ;;
    *)
        docker compose -f "$COMPOSE_FILE" ps
        ;;
esac
EOF
chmod +x /usr/local/bin/novos-llm-stack

echo "==> [Stage 5.2b] Installing DevSecOps Kali Rolling Container Manager (/usr/local/bin/novos-sec-box)..."
cat << 'EOF' > /usr/local/bin/novos-sec-box
#!/usr/bin/env bash
# ==============================================================================
# NovOS DevSecOps Kali Rolling Container Manager (Distrobox + Rootless Podman)
# Usage:
#   novos-sec-box up              Create/start the kalilinux/kali-rolling container
#   novos-sec-box enter [cmd...]  Enter interactive shell or execute command inside Kali
#   novos-sec-box install-top10   Provision Kali Top 10 security & pentesting toolkit
#   novos-sec-box export <bin>    Export a Kali tool binary to ~/.local/bin on host
#   novos-sec-box status          Show Distrobox container status
#   novos-sec-box stop            Stop the Kali Rolling container
# ==============================================================================
set -euo pipefail

BOX_NAME="${NOVOS_SEC_BOX_NAME:-novos-kali}"
BOX_IMAGE="${NOVOS_SEC_BOX_IMAGE:-docker.io/kalilinux/kali-rolling:latest}"
ACTION="${1:-help}"

ensure_runtime() {
    if ! command -v distrobox >/dev/null 2>&1 || ! command -v podman >/dev/null 2>&1; then
        echo "[ERROR] 'distrobox' and 'podman' are required for novos-sec-box." >&2
        echo "        Install them via: sudo novos-setup --install devsecops" >&2
        exit 1
    fi
}

ensure_box_created() {
    ensure_runtime
    if ! distrobox list --no-color 2>/dev/null | awk -F'|' 'NR>1 {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}' | grep -qx "$BOX_NAME"; then
        echo "==> [novos-sec-box] Creating Distrobox '${BOX_NAME}' from ${BOX_IMAGE}..."
        distrobox create --name "$BOX_NAME" --image "$BOX_IMAGE" --yes
    fi
}

case "$ACTION" in
    up|create|init)
        ensure_box_created
        echo "==> [novos-sec-box] Initializing '${BOX_NAME}' container..."
        distrobox enter "$BOX_NAME" -- true
        echo "[OK]   Kali Rolling box '${BOX_NAME}' is up. Enter with: novos-sec-box enter"
        ;;
    enter|shell)
        ensure_box_created
        shift
        if [[ $# -gt 0 ]]; then
            exec distrobox enter "$BOX_NAME" -- "$@"
        else
            exec distrobox enter "$BOX_NAME"
        fi
        ;;
    install-top10|top10)
        ensure_box_created
        echo "==> [novos-sec-box] Installing Kali Top 10 & offensive security suite inside '${BOX_NAME}'..."
        distrobox enter "$BOX_NAME" -- bash -c '
            set -euo pipefail
            sudo apt-get update
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
                kali-tools-top10 nmap wireshark tshark tcpdump netcat-traditional \
                john hashcat aircrack-ng hydra sqlmap gobuster ffuf nikto \
                metasploit-framework radare2 gdb responder impacket-scripts
        '
        echo "[OK]   Kali Top 10 toolkit installed in '${BOX_NAME}'."
        ;;
    export)
        ensure_box_created
        TARGET_BIN="${2:-}"
        if [[ -z "$TARGET_BIN" ]]; then
            echo "[ERROR] Usage: novos-sec-box export <binary-name>" >&2
            exit 1
        fi
        mkdir -p "$HOME/.local/bin"
        distrobox enter "$BOX_NAME" -- bash -c '
            set -euo pipefail
            export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
            bin_arg="$1"
            export_dir="$2"
            if [[ "$bin_arg" == /* ]]; then
                bin_path="$bin_arg"
            else
                if ! bin_path="$(command -v "$bin_arg")" || [[ -z "$bin_path" ]]; then
                    bin_path="/usr/bin/${bin_arg}"
                fi
            fi
            distrobox-export --bin "$bin_path" --export-path "$export_dir"
        ' _ "$TARGET_BIN" "$HOME/.local/bin"
        echo "[OK]   Exported '${TARGET_BIN}' from '${BOX_NAME}' to $HOME/.local/bin"
        ;;
    status|list)
        ensure_runtime
        distrobox list
        ;;
    stop|down)
        ensure_runtime
        distrobox stop --yes "$BOX_NAME"
        ;;
    --help|-h|help|*)
        cat << 'HELP_EOF'
Usage: novos-sec-box <command> [args...]

Commands:
  up              Create and initialize the kalilinux/kali-rolling Distrobox container
  enter [cmd...]  Enter the Kali Rolling environment (or run a command inside it)
  install-top10   Install Kali Top 10 security tools inside the container
  export <bin>    Export a binary from the Kali container to ~/.local/bin on the host
  status          Display Distrobox container status
  stop            Stop the Kali Rolling container
HELP_EOF
        if [[ "$ACTION" != "--help" && "$ACTION" != "-h" && "$ACTION" != "help" ]]; then
            exit 1
        fi
        ;;
esac
EOF
chmod +x /usr/local/bin/novos-sec-box

echo "==> [Stage 5.3] Installing Modular Profile Manager (/usr/local/bin/novos-setup)..."
cat << 'EOF' > /usr/local/bin/novos-setup
#!/usr/bin/env bash
# ==============================================================================
# NovOS Modular Software Profile Installer (TUI, CLI & Calamares Post-Install)
# Profiles: wifi, bluetooth, no-wifi, no-bluetooth, gaming, dev, devsecops,
#           llm, network, comms (or browsers), media, utilities
# Usage:
#   Interactive TUI:   sudo novos-setup
#   CLI Profiles:      sudo novos-setup --install wifi,bluetooth,gaming,dev,devsecops,llm,network,comms,media,utilities --user devuser
#   Hardware Opt-Out:  sudo novos-setup --install no-wifi,no-bluetooth
#   Custom Tools:      sudo novos-setup --custom-tools
#   Granular APT:      sudo novos-setup --packages fastfetch,tmux,ripgrep --user devuser
#   Calamares Pre-Pkg: sudo novos-setup --pre-packages
#   Calamares Hook:    sudo novos-setup --post-calamares --user devuser
#   Failure Report:    novos-setup --report [--log-only | --notify]
# ==============================================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export DEBIAN_FRONTEND=noninteractive

# Install-time failure record (NOVOS_CONTEXT unset = runtime): one row per failed item,
# `profile<TAB>item<TAB>reason`. NOVOS_CONTEXT=build (Stage 5) never records: failures there are fatal.
FAILURE_TSV="/var/log/novos/package-failures.tsv"
INSTALL_REPORT_DESKTOP="/etc/xdg/autostart/novos-install-report.desktop"
# Profile attributed to recorded failures; load_manifest_pkgs sets it to the profile being installed.
CURRENT_PROFILE="packages"
REPORT_MODE="false"
REPORT_OUTPUT="terminal"

ORIG_ARGS=("$@")
TARGET_USER="${SUDO_USER:-}"
USER_SPECIFIED="false"
SELECTED_PROFILES=""
SELECTED_PACKAGES=""
PRE_PACKAGES_MODE="false"
POST_CALAMARES="false"
RUN_CUSTOM_TOOLS="false"
INTERACTIVE="true"
APT_UPDATED="false"

append_profile() {
    local p="$1"
    if [[ -z "$SELECTED_PROFILES" ]]; then
        SELECTED_PROFILES="$p"
    else
        SELECTED_PROFILES="${SELECTED_PROFILES},${p}"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install|-i)
            if [[ $# -lt 2 ]]; then
                echo "[ERROR] Option $1 requires a profile list argument." >&2
                exit 1
            fi
            append_profile "${2:-}"
            INTERACTIVE="false"
            shift 2
            ;;
        --packages|-p)
            if [[ $# -lt 2 ]]; then
                echo "[ERROR] Option $1 requires a package list argument." >&2
                exit 1
            fi
            SELECTED_PACKAGES="${2:-}"
            INTERACTIVE="false"
            shift 2
            ;;
        --custom-tools)
            RUN_CUSTOM_TOOLS="true"
            INTERACTIVE="false"
            shift
            ;;
        --wifi)
            append_profile "wifi"
            INTERACTIVE="false"
            shift
            ;;
        --no-wifi)
            append_profile "no-wifi"
            INTERACTIVE="false"
            shift
            ;;
        --bluetooth)
            append_profile "bluetooth"
            INTERACTIVE="false"
            shift
            ;;
        --no-bluetooth)
            append_profile "no-bluetooth"
            INTERACTIVE="false"
            shift
            ;;
        --pre-packages)
            PRE_PACKAGES_MODE="true"
            INTERACTIVE="false"
            shift
            ;;
        --post-calamares)
            POST_CALAMARES="true"
            INTERACTIVE="false"
            shift
            ;;
        --report)
            REPORT_MODE="true"
            INTERACTIVE="false"
            shift
            ;;
        --log-only)
            REPORT_OUTPUT="log"
            shift
            ;;
        --notify)
            REPORT_OUTPUT="notify"
            shift
            ;;
        --user|-u)
            if [[ $# -lt 2 ]]; then
                echo "[ERROR] Option $1 requires a username argument." >&2
                exit 1
            fi
            TARGET_USER="${2:-}"
            USER_SPECIFIED="true"
            shift 2
            ;;
        --help|-h)
            cat << 'HELP_EOF'
Usage: sudo novos-setup [OPTIONS]

Options:
  --install, -i <profiles>   Comma-separated profiles to install or apply:
                             wifi,bluetooth,no-wifi,no-bluetooth,gaming,dev,devsecops,
                             llm,network,comms,browsers,media,utilities (or 'all')
  --packages, -p <pkgs>      Comma-separated list of individual APT packages to install safely
  --custom-tools             Build and install enabled tools from /etc/novos/custom-tools.yaml
  --wifi / --no-wifi         Opt-in (install/enable) or opt-out (purge/disable) WiFi stack & firmware
  --bluetooth / --no-bluetooth Opt-in (install/enable) or opt-out (purge/mask) Bluetooth stack
  --pre-packages             Prepare Calamares target chroot before APT packages module runs
  --post-calamares           Apply Calamares netinstall selections, WiFi/Bluetooth opt-outs,
                             Flathub apps, Rust/uv, Ollama/ComfyUI (/srv/llm), and Samba [DevShare]
  --user, -u <username>      Target non-root user to configure (default: primary regular user)
  --report                   Print install-time package failures (/var/log/novos/package-failures.tsv)
                             grouped by profile, with retry commands
    --log-only               With --report: print only (Calamares log); no desktop/user state changes
    --notify                 With --report: desktop notification (login autostart), then hide the autostart
  --help, -h                 Show this help message
HELP_EOF
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

# Prints the failure TSV grouped by profile (duplicate rows collapsed) with a retry command per profile.
print_failure_report() {
    if [[ ! -s "$FAILURE_TSV" ]]; then
        echo "[OK]   No install-time package failures recorded (${FAILURE_TSV})."
        return 0
    fi
    echo "==> NovOS install-time failure report (${FAILURE_TSV}):"
    # Groups that are not installable profiles (pre-packages, post-calamares, apt) get no --install retry.
    awk -F'\t' -v installable='^(wifi|no-wifi|bluetooth|no-bluetooth|gaming|dev|devsecops|llm|network|comms|browsers|media|utilities|custom-tools)$' '
        NF >= 3 {
            key = $1 FS $2 FS $3
            if (seen[key]++) next
            if (!($1 in cnt)) order[++n] = $1
            cnt[$1]++
            items[$1] = items[$1] "  - " $2 ": " $3 "\n"
            names[$1] = (names[$1] == "" ? $2 : names[$1] "," $2)
        }
        END {
            for (i = 1; i <= n; i++) {
                p = order[i]
                printf "[Profile: %s] %d failure(s)\n%s", p, cnt[p], items[p]
                if (p == "packages") {
                    printf "  Retry: sudo novos-setup --packages %s\n", names[p]
                } else if (p ~ installable) {
                    printf "  Retry: sudo novos-setup --install %s\n", p
                } else {
                    printf "  Retry: as root, re-run the command shown in each reason above\n"
                }
            }
        }
    ' "$FAILURE_TSV"
}

# Hides the system autostart entry for the current desktop user (XDG override, Hidden=true).
hide_install_report_autostart() {
    if [[ "$EUID" -eq 0 || -z "${HOME:-}" ]]; then
        return 0
    fi
    local user_autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
    mkdir -p "$user_autostart"
    printf '[Desktop Entry]\nType=Application\nName=NovOS Install Report\nHidden=true\n' \
        > "${user_autostart}/novos-install-report.desktop"
}

# Runtime only: appends `profile<TAB>item<TAB>reason` to the failure TSV and warns; the caller continues.
record_install_failure() {
    local profile="$1"
    local item="$2"
    local reason="$3"
    mkdir -p "$(dirname "$FAILURE_TSV")"
    printf '%s\t%s\t%s\n' "$profile" "$item" "$reason" >> "$FAILURE_TSV"
    chmod 0644 "$FAILURE_TSV"
    echo "[WARN] [Profile: ${profile}] '${item}': ${reason}; recorded in ${FAILURE_TSV} (NOVOS_CONTEXT=${NOVOS_CONTEXT:-runtime})." >&2
}

# Usage: run_or_record <profile> <item> <command> [args...]
# Runs a configuration command that is not a package install. Image build (NOVOS_CONTEXT=build):
# a failure is fatal. Runtime / Calamares: the failure is recorded in the failure TSV (with the
# command and exit code as the reason) and the caller continues, so the install still completes.
run_or_record() {
    local profile="$1"
    local item="$2"
    shift 2
    local rc=0
    "$@" || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        return 0
    fi
    local cmd_text="$*"
    cmd_text="${cmd_text//$'\n'/ }"
    cmd_text="${cmd_text//$'\t'/ }"
    if [[ "${NOVOS_CONTEXT:-}" == "build" ]]; then
        echo "[FATAL] [Profile: ${profile}] '${item}': '${cmd_text}' failed (exit ${rc}) (NOVOS_CONTEXT=build)." >&2
        exit 1
    fi
    record_install_failure "$profile" "$item" "exit ${rc}: ${cmd_text:0:160}"
}

if [[ "$REPORT_MODE" == "true" ]]; then
    print_failure_report
    case "$REPORT_OUTPUT" in
        log)
            ;;
        notify)
            if [[ -s "$FAILURE_TSV" ]]; then
                if ! command -v notify-send >/dev/null 2>&1; then
                    echo "[WARN] notify-send not found; run 'novos-setup --report' in a terminal to view the failure report." >&2
                    exit 0
                fi
                fail_count="$(cut -f1,2 "$FAILURE_TSV" | sort -u | wc -l)"
                fail_profiles="$(cut -f1 "$FAILURE_TSV" | sort -u | paste -sd, -)"
                notify-send -u critical -a NovOS \
                    "NovOS setup: ${fail_count} item(s) failed to install" \
                    "Profiles: ${fail_profiles}. Run 'novos-setup --report' in a terminal for details and retry commands."
            fi
            hide_install_report_autostart
            ;;
        *)
            hide_install_report_autostart
            ;;
    esac
    exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
    echo "Elevating privileges with sudo..."
    if [[ "$USER_SPECIFIED" == "true" || "$PRE_PACKAGES_MODE" == "true" ]]; then
        exec sudo -E "$0" "${ORIG_ARGS[@]}"
    else
        exec sudo -E "$0" --user "${USER:-devuser}" "${ORIG_ARGS[@]}"
    fi
fi

if [[ "$PRE_PACKAGES_MODE" == "true" ]]; then
    echo "==> [Pre-Packages] Preparing Calamares target chroot for APT package installation..."
    mkdir -p /dev /dev/pts /dev/shm /run/udev /usr/sbin /etc/apt/apt.conf.d
    chmod 1777 /dev/shm
    for devspec in 'null:1:3' 'zero:1:5' 'full:1:7' 'random:1:8' 'urandom:1:9' 'tty:5:0'; do
        dev_name="${devspec%%:*}"
        dev_rest="${devspec#*:}"
        dev_maj="${dev_rest%%:*}"
        dev_min="${dev_rest#*:}"
        if [[ ! -c "/dev/${dev_name}" ]]; then
            rm -f "/dev/${dev_name}"
            mknod -m 666 "/dev/${dev_name}" c "$dev_maj" "$dev_min"
        fi
        run_or_record pre-packages "/dev/${dev_name} mode" chmod 0666 "/dev/${dev_name}"
    done
    if ! mountpoint -q /dev/pts; then
        run_or_record pre-packages "/dev/pts mount" mount -t devpts devpts /dev/pts -o gid=5,mode=620,ptmxmode=0666
    fi
    if [[ ! -c /dev/ptmx ]]; then
        rm -f /dev/ptmx
        if [[ -c /dev/pts/ptmx ]]; then
            ln -sf pts/ptmx /dev/ptmx
        else
            mknod -m 666 /dev/ptmx c 5 2
        fi
    fi
    # /dev/pts/ptmx exists only when devpts is mounted (a failed mount is already recorded above).
    for ptmx_path in /dev/ptmx /dev/pts/ptmx; do
        if [[ -e "$ptmx_path" ]]; then
            run_or_record pre-packages "${ptmx_path} mode" chmod 0666 "$ptmx_path"
        fi
    done

    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
    chmod 0755 /usr/sbin/policy-rc.d

    if [[ -f /usr/sbin/aspell-autobuildhash ]] && ! grep -q 'sub subst' /usr/sbin/aspell-autobuildhash; then
        perl -0777 -i -pe 'my $g = "sub subst { my (\$s) = \@_; return defined(\$s) ? \$s : \"\"; }\nif ( ! -c \"/dev/null\" ) { unlink(\"/dev/null\"); system(\"mknod\", \"-m\", \"666\", \"/dev/null\", \"c\", \"1\", \"3\"); }\n"; if (!s/\A(#![^\n]*\n)/$1$g/) { $_ = $g . $_; }' /usr/sbin/aspell-autobuildhash
        chmod 0755 /usr/sbin/aspell-autobuildhash
    fi

    if ! grep -q -- '--force-confdef' /etc/apt/apt.conf.d/01-novos-safety 2>/dev/null; then
        printf 'DPkg::Options {\n  "--force-confdef";\n  "--force-confold";\n};\n' >> /etc/apt/apt.conf.d/01-novos-safety
    fi

    if command -v debconf-set-selections >/dev/null 2>&1; then
        # Here-string, not a pipe: no pipeline/SIGPIPE status can abort --pre-packages.
        run_or_record pre-packages debconf-selections debconf-set-selections <<< $'debconf debconf/frontend select Noninteractive\ndebconf debconf/priority select critical\nwireshark-common wireshark-common/install-setuid boolean true\nsamba-common samba-common/workgroup string WORKGROUP\nsamba-common samba-common/dhcp boolean false\nsamba-common samba-common/do_debconf boolean true'
    fi

    if [[ ! -s /etc/resolv.conf ]] || ! grep -qE '^[[:space:]]*nameserver[[:space:]]+' /etc/resolv.conf 2>/dev/null; then
        rm -f /etc/resolv.conf
        if [[ -s /run/systemd/resolve/resolv.conf ]]; then
            cp -fL /run/systemd/resolve/resolv.conf /etc/resolv.conf
        else
            echo 'nameserver 1.1.1.1' > /etc/resolv.conf
            echo 'nameserver 8.8.8.8' >> /etc/resolv.conf
        fi
    fi

    run_or_record pre-packages "dpkg --configure -a" env DEBIAN_FRONTEND=noninteractive dpkg --configure -a
    run_or_record pre-packages "apt-get update" env DEBIAN_FRONTEND=noninteractive apt-get update -q
    exit 0
fi

# Resolve target user; prefer explicit --user or SUDO_USER if valid, else primary non-devuser regular user (UID 1000-59999)
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" || "$TARGET_USER" == @@* ]] || ! id -u "$TARGET_USER" >/dev/null 2>&1; then
    DETECTED_USER="$(awk -F: '$3 >= 1000 && $3 < 60000 && $1 != "devuser" {u=$1; exit} END {print u}' /etc/passwd)"
    if [[ -z "$DETECTED_USER" ]]; then
        DETECTED_USER="$(awk -F: '$3 >= 1000 && $3 < 60000 {u=$1} END {print u}' /etc/passwd)"
    fi
    if [[ -n "$DETECTED_USER" ]]; then
        TARGET_USER="$DETECTED_USER"
    else
        TARGET_USER="devuser"
    fi
fi

# A target user without a passwd entry (the devuser fallback on a system without it) has no home yet.
USER_HOME=""
if USER_ENTRY="$(getent passwd "$TARGET_USER")"; then
    USER_HOME="$(cut -d: -f6 <<< "$USER_ENTRY")"
fi
if [[ -z "$USER_HOME" ]]; then
    USER_HOME="/home/${TARGET_USER}"
fi

# ------------------------------------------------------------------------------
# Pre-Install Dependency & APT Readiness Helpers
# ------------------------------------------------------------------------------
verify_apt_ready() {
    local retries=5
    local wait_sec=3
    local attempt=1

    while command -v fuser >/dev/null 2>&1 && fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1; do
        if [[ "$attempt" -ge "$retries" ]]; then
            echo "[WARN] dpkg/apt lock still active after ${retries} checks; proceeding..." >&2
            break
        fi
        echo "==> Waiting for dpkg/apt lock to clear (attempt ${attempt}/${retries})..."
        sleep "$wait_sec"
        attempt=$((attempt + 1))
    done

    run_or_record apt "dpkg --configure -a" dpkg --configure -a

    if [[ "$APT_UPDATED" != "true" ]]; then
        attempt=1
        while [[ "$attempt" -le 3 ]]; do
            if DEBIAN_FRONTEND=noninteractive apt-get update -y; then
                APT_UPDATED="true"
                break
            fi
            echo "[WARN] apt-get update failed (attempt ${attempt}/3); retrying in 2s..." >&2
            sleep 2
            attempt=$((attempt + 1))
        done
    fi
}

safe_apt_install() {
    verify_apt_ready
    local available_pkgs=()
    local missing_pkgs=()
    local pkg
    for pkg in "$@"; do
        [[ -z "$pkg" ]] && continue
        if apt-cache show "$pkg" 2>/dev/null | grep -q '^Package:'; then
            available_pkgs+=("$pkg")
        elif [[ "${NOVOS_CONTEXT:-}" == "build" ]]; then
            missing_pkgs+=("$pkg")
        else
            record_install_failure "$CURRENT_PROFILE" "$pkg" "not found in APT cache"
        fi
    done

    # Image build (NOVOS_CONTEXT=build): a missing package fails the build instead of being skipped.
    if [[ "${#missing_pkgs[@]}" -gt 0 ]]; then
        echo "[FATAL] Declared packages unavailable in sid/third-party repos (NOVOS_CONTEXT=build):" >&2
        printf '  - %s\n' "${missing_pkgs[@]}" >&2
        exit 1
    fi

    if [[ "${#available_pkgs[@]}" -eq 0 ]]; then
        echo "[WARN] No installable packages matched in current batch." >&2
        return 0
    fi

    local apt_install=(apt-get -o APT::Get::AutomaticRemove=false -o APT::AutoRemove::SuggestsImportant=false install -y)
    # Image build: an apt failure stays fatal (set -e).
    if [[ "${NOVOS_CONTEXT:-}" == "build" ]]; then
        DEBIAN_FRONTEND=noninteractive "${apt_install[@]}" "${available_pkgs[@]}"
        return 0
    fi
    # Runtime: on a batch failure retry each package alone and record the ones that still fail.
    if ! DEBIAN_FRONTEND=noninteractive "${apt_install[@]}" "${available_pkgs[@]}"; then
        echo "[WARN] [Profile: ${CURRENT_PROFILE}] apt batch install failed; retrying ${#available_pkgs[@]} package(s) individually..." >&2
        for pkg in "${available_pkgs[@]}"; do
            if ! DEBIAN_FRONTEND=noninteractive "${apt_install[@]}" "$pkg"; then
                record_install_failure "$CURRENT_PROFILE" "$pkg" "apt-get install failed"
            fi
        done
    fi
}

# Usage: safe_flatpak_install <profile> <app-id>...
# A failed Flathub install is fatal in every context (user decision, STEP 9D; reaffirmed for 9F):
# it is not recorded-and-skipped like apt packages and custom tools.
safe_flatpak_install() {
    local profile="$1"
    shift
    if ! flatpak install -y flathub "$@"; then
        echo "[FATAL] [Profile: ${profile}] flatpak install -y flathub $* failed." >&2
        exit 1
    fi
}

# Usage: safe_apt_purge <profile> <pkg>...
# Purges the installed subset with apt-get only (no forced dpkg fallback). Image build: a failed purge is
# fatal. Runtime / Calamares: on a batch failure each package is purged alone and the ones that still
# fail are recorded in the failure TSV; the install continues.
safe_apt_purge() {
    local profile="$1"
    shift
    local installed_pkgs=()
    local pkg
    for pkg in "$@"; do
        [[ -z "$pkg" ]] && continue
        if is_pkg_installed "$pkg"; then
            installed_pkgs+=("$pkg")
        fi
    done

    if [[ "${#installed_pkgs[@]}" -eq 0 ]]; then
        return 0
    fi
    if [[ "${NOVOS_CONTEXT:-}" == "build" ]]; then
        if ! DEBIAN_FRONTEND=noninteractive apt-get purge -y "${installed_pkgs[@]}"; then
            echo "[FATAL] [Profile: ${profile}] apt-get purge -y ${installed_pkgs[*]} failed (NOVOS_CONTEXT=build)." >&2
            exit 1
        fi
        return 0
    fi
    if ! DEBIAN_FRONTEND=noninteractive apt-get purge -y "${installed_pkgs[@]}"; then
        echo "[WARN] [Profile: ${profile}] apt batch purge failed; retrying ${#installed_pkgs[@]} package(s) individually..." >&2
        for pkg in "${installed_pkgs[@]}"; do
            if ! is_pkg_installed "$pkg"; then
                continue
            fi
            if ! DEBIAN_FRONTEND=noninteractive apt-get purge -y "$pkg"; then
                record_install_failure "$profile" "$pkg" "apt-get purge failed"
            fi
        done
    fi
}

# ------------------------------------------------------------------------------
# Service, Group, Flathub & rfkill Helpers (explicit state tests; failures via run_or_record)
# ------------------------------------------------------------------------------
# Unit names without a suffix are services (docker -> docker.service).
unit_file_name() {
    if [[ "$1" == *.* ]]; then
        printf '%s\n' "$1"
    else
        printf '%s.service\n' "$1"
    fi
}

unit_installed() {
    local unit
    unit="$(unit_file_name "$1")"
    [[ -e "/etc/systemd/system/${unit}" || -e "/usr/lib/systemd/system/${unit}" || -e "/lib/systemd/system/${unit}" ]]
}

unit_masked() {
    local unit
    unit="$(unit_file_name "$1")"
    [[ -L "/etc/systemd/system/${unit}" && "$(readlink "/etc/systemd/system/${unit}")" == "/dev/null" ]]
}

# --now only on a booted system: post-Calamares and chroot runs only change the unit links.
units_act_now() {
    [[ "$POST_CALAMARES" != "true" ]] && ! systemd-detect-virt --chroot >/dev/null 2>&1
}

# Usage: enable_units <profile> <start|nostart> <unit>...
# A unit whose package is not installed is skipped (that package's own failure is already recorded or fatal).
enable_units() {
    local profile="$1"
    local mode="$2"
    shift 2
    local now=()
    if [[ "$mode" == "start" ]] && units_act_now; then
        now=(--now)
    fi
    local unit
    for unit in "$@"; do
        if ! unit_installed "$unit"; then
            echo "[INFO] [Profile: ${profile}] ${unit}: unit not installed; not enabled."
            continue
        fi
        run_or_record "$profile" "enable ${unit}" systemctl enable "${now[@]}" "$unit"
    done
}

# Usage: disable_units <profile> <unit>...   (absent units need no disabling)
disable_units() {
    local profile="$1"
    shift
    local now=()
    if units_act_now; then
        now=(--now)
    fi
    local unit
    for unit in "$@"; do
        if unit_installed "$unit" && ! unit_masked "$unit"; then
            run_or_record "$profile" "disable ${unit}" systemctl disable "${now[@]}" "$unit"
        fi
    done
}

# Usage: mask_units <profile> <unit>...   (masking also covers units installed later)
mask_units() {
    local profile="$1"
    shift
    local unit
    for unit in "$@"; do
        if ! unit_masked "$unit"; then
            run_or_record "$profile" "mask ${unit}" systemctl mask "$unit"
        fi
    done
}

# Usage: unmask_units <profile> <unit>...   (only masked units are touched)
unmask_units() {
    local profile="$1"
    shift
    local unit
    for unit in "$@"; do
        if unit_masked "$unit"; then
            run_or_record "$profile" "unmask ${unit}" systemctl unmask "$unit"
        fi
    done
}

# Usage: ensure_user_groups <profile> <group>...
# groupadd -f is a no-op for an existing group; the target user joins each group that exists.
ensure_user_groups() {
    local profile="$1"
    shift
    local grp
    for grp in "$@"; do
        run_or_record "$profile" "group ${grp}" groupadd -f "$grp"
    done
    if ! id "$TARGET_USER" >/dev/null 2>&1; then
        return 0
    fi
    for grp in "$@"; do
        if getent group "$grp" >/dev/null; then
            run_or_record "$profile" "group ${grp} membership" usermod -aG "$grp" "$TARGET_USER"
        fi
    done
}

# Usage: ensure_flathub_remote <profile>
# Fatal like safe_flatpak_install: without the remote the profile's Flathub installs cannot run.
ensure_flathub_remote() {
    local profile="$1"
    if ! flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo; then
        echo "[FATAL] [Profile: ${profile}] flatpak remote-add --if-not-exists flathub failed." >&2
        exit 1
    fi
}

# Usage: rfkill_set <profile> <block|unblock> <type>
# Without /dev/rfkill (chroot without the device, no radio hardware) there is no switch to set.
rfkill_set() {
    local profile="$1"
    local action="$2"
    local type="$3"
    if command -v rfkill >/dev/null 2>&1 && [[ -e /dev/rfkill ]]; then
        run_or_record "$profile" "rfkill ${action} ${type}" rfkill "$action" "$type"
    fi
}

# Package lists come from the unified manifest (config/packages.yaml -> /etc/novos/packages.yaml).
# Usage: load_manifest_pkgs <profile> <source>...   (fills the global MANIFEST_PKGS array)
# --with-arch keeps foreign-arch entries qualified (wine32:i386, libwine:i386).
# A failed lookup or an empty source list is fatal: a profile never installs a silently shortened list.
# Also sets CURRENT_PROFILE, the profile that following safe_apt_install failures are recorded under.
load_manifest_pkgs() {
    local profile="$1"
    shift
    local src out
    local batch=()
    CURRENT_PROFILE="$profile"
    MANIFEST_PKGS=()
    for src in "$@"; do
        if ! out="$(novos-custom-tools packages list /etc/novos/packages.yaml --profile "$profile" --source "$src" --with-arch)"; then
            echo "[ERROR] novos-custom-tools packages list /etc/novos/packages.yaml --profile ${profile} --source ${src} --with-arch failed." >&2
            exit 1
        fi
        if [[ -z "$out" ]]; then
            echo "[ERROR] /etc/novos/packages.yaml declares no '${src}' packages for profile '${profile}'." >&2
            exit 1
        fi
        mapfile -t batch <<< "$out"
        MANIFEST_PKGS+=("${batch[@]}")
    done
}

is_pkg_installed() {
    local pkg="$1"
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"
}

any_pkg_installed() {
    local pkg
    for pkg in "$@"; do
        if is_pkg_installed "$pkg"; then
            return 0
        fi
    done
    return 1
}

verify_profile_deps() {
    local profile="$1"
    local bins=()
    local services=()

    case "$profile" in
        wifi)
            bins=(iw rfkill wpa_supplicant)
            ;;
        bluetooth)
            bins=(bluetoothctl blueman-manager)
            services=(bluetooth)
            ;;
        gaming)
            bins=(gamemoded gamescope mangohud lutris wine retroarch flatpak)
            ;;
        dev)
            bins=(docker podman distrobox python3 clang cmake ninja ghc cabal zig)
            services=(docker)
            ;;
        devsecops)
            bins=(nmap tshark tcpdump john hashcat aircrack-ng sqlmap gobuster radare2 gdb ufw distrobox podman novos-sec-box)
            ;;
        llm)
            bins=(docker nvtop btop novos-llm-stack)
            ;;
        network)
            bins=(smbd sshd wg remmina syncthing rclone)
            services=(smbd ssh)
            ;;
        comms|browsers)
            bins=(firefox-esr chromium thunderbird)
            ;;
        media)
            bins=(vlc mpv obs kdenlive gimp inkscape krita darktable blender freecad audacity ardour)
            ;;
        utilities)
            bins=(libreoffice fastfetch gparted keepassxc tmux rg fdfind timeshift btrfs-assistant)
            ;;
        *)
            return 0
            ;;
    esac

    local bin
    for bin in "${bins[@]}"; do
        if command -v "$bin" >/dev/null 2>&1 || [[ -x "/usr/sbin/${bin}" || -x "/usr/local/bin/${bin}" || -x "/usr/libexec/iwd" ]]; then
            echo "[OK]   [Profile: ${profile}] Verified binary: ${bin}"
        else
            echo "[WARN] [Profile: ${profile}] Binary not present (optional or skipped): ${bin}" >&2
        fi
    done

    local svc
    for svc in "${services[@]+"${services[@]}"}"; do
        [[ -z "$svc" ]] && continue
        if systemctl is-enabled "$svc" >/dev/null 2>&1 || [[ -f "/lib/systemd/system/${svc}.service" || -f "/usr/lib/systemd/system/${svc}.service" ]]; then
            echo "[OK]   [Profile: ${profile}] Verified service unit: ${svc}"
        else
            echo "[WARN] [Profile: ${profile}] Service unit not enabled/present: ${svc}" >&2
        fi
    done

    if [[ "$profile" == "llm" ]]; then
        if [[ -f "/srv/llm/docker-compose.yml" ]]; then
            echo "[OK]   [Profile: llm] Verified /srv/llm/docker-compose.yml"
        else
            echo "[WARN] [Profile: llm] Missing /srv/llm/docker-compose.yml" >&2
        fi
        if command -v ollama >/dev/null 2>&1; then
            echo "[OK]   [Profile: llm] Verified binary: ollama"
        else
            echo "[WARN] [Profile: llm] Binary not present: ollama" >&2
        fi
    elif [[ "$profile" == "network" ]]; then
        if grep -q "\[DevShare\]" /etc/samba/smb.conf 2>/dev/null; then
            echo "[OK]   [Profile: network] Verified Samba [DevShare] configuration"
        else
            echo "[WARN] [Profile: network] Missing Samba [DevShare] block in /etc/samba/smb.conf" >&2
        fi
    fi

    return 0
}

# ------------------------------------------------------------------------------
# Interactive Whiptail Menu (All 10 Categories from netinstall.yaml)
# ------------------------------------------------------------------------------
if [[ "$INTERACTIVE" == "true" ]]; then
    CHOICES=$(whiptail --title "NovOS Profile Setup" \
        --checklist "Select the software & hardware profiles to install (Space to toggle, Enter to confirm):" 24 96 12 \
        "wifi"      "Wireless & WiFi Connectivity Stack (iwd, wpasupplicant, WiFi firmware)" ON \
        "bluetooth" "Bluetooth Audio & Peripherals Stack (BlueZ, Blueman, PipeWire SPA)" ON \
        "gaming"    "Steam, Lutris, Heroic, Bottles, RPCS3, RetroArch, Dolphin, PCSX2" ON \
        "dev"       "Antigravity/agy, VS Code, Neovim, Helix, Docker, Podman, Distrobox, KVM" ON \
        "devsecops" "Nmap, Wireshark, Hashcat, Aircrack-ng, Radare2 & Kali Box (novos-sec-box)" OFF \
        "llm"       "Ollama, llama.cpp, ComfyUI & Open WebUI (Docker), HuggingFace CLI" ON \
        "network"   "Samba (DevShare), Remmina, Tailscale, WireGuard, Syncthing, Rclone, SSH" ON \
        "comms"     "Firefox ESR, Chromium, Thunderbird, Brave, LibreWolf, Discord, Signal" OFF \
        "media"     "VLC, mpv, OBS, Kdenlive, GIMP, Krita, Darktable, Blender, Ardour, FreeCAD" OFF \
        "utilities" "LibreOffice, Timeshift, Btrfs Assistant, GParted, KeePassXC, Mission Center" OFF \
        3>&1 1>&2 2>&3) || exit 0
    SELECTED_PROFILES="$(echo "$CHOICES" | tr -d '"' | tr ' ' ',')"
    if [[ ",${SELECTED_PROFILES}," != *",wifi,"* ]]; then
        append_profile "no-wifi"
    fi
    if [[ ",${SELECTED_PROFILES}," != *",bluetooth,"* ]]; then
        append_profile "no-bluetooth"
    fi
fi

# ------------------------------------------------------------------------------
# Post-Install Configuration Helpers (Shared by Profile Installers & --post-calamares)
# ------------------------------------------------------------------------------
configure_gaming_flathub() {
    echo "==> [Gaming] Configuring gaming groups, permissions & Flathub..."
    ensure_user_groups gaming gamemode render video input audio plugdev

    if [[ "$POST_CALAMARES" == "true" ]]; then
        echo "==> [Gaming] Post-Calamares offline mode: skipping online Flathub remote/app downloads."
        return 0
    fi

    if ! command -v flatpak >/dev/null 2>&1; then
        safe_apt_install flatpak
    fi
    if command -v flatpak >/dev/null 2>&1; then
        ensure_flathub_remote gaming
        load_manifest_pkgs gaming flatpak
        safe_flatpak_install "$CURRENT_PROFILE" "${MANIFEST_PKGS[@]}"

        if [[ -d "/mnt/raid0" ]] && id "$TARGET_USER" >/dev/null 2>&1; then
            for app_id in com.valvesoftware.Steam com.heroicgameslauncher.hgl com.github.Matoking.protontricks com.usebottles.bottles net.rpcs3.RPCS3; do
                run_or_record gaming "flatpak override ${app_id}" sudo -u "$TARGET_USER" flatpak override --user --filesystem=/mnt/raid0 "$app_id"
            done
        fi
    fi
}

configure_dev_runtime() {
    echo "==> [Dev] Configuring Docker/Podman/libvirt services, user groups, Rust (rustup) & Astral uv..."
    ensure_user_groups dev docker libvirt kvm video render input audio
    enable_units dev start docker containerd libvirtd

    if [[ "$POST_CALAMARES" == "true" ]]; then
        echo "==> [Dev] Post-Calamares offline mode: skipping online rustup/uv network installers."
        return 0
    fi

    # Online per-user installers: each one is its own recorded item (fatal in the image build).
    if id "$TARGET_USER" >/dev/null 2>&1; then
        run_or_record dev rustup sudo -u "$TARGET_USER" bash -c '
            set -euo pipefail
            if [ ! -d "$HOME/.cargo" ]; then
                curl --connect-timeout 15 --max-time 300 --retry 2 --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --component rust-analyzer clippy rustfmt
            fi
        '
        run_or_record dev uv sudo -u "$TARGET_USER" bash -c '
            set -euo pipefail
            if [ ! -f "$HOME/.local/bin/uv" ]; then
                curl --connect-timeout 15 --max-time 300 --retry 2 -LsSf https://astral.sh/uv/install.sh | sh
            fi
        '
        # appimage-builder needs uv; a missing uv is the recorded 'uv' item above.
        run_or_record dev appimage-builder sudo -u "$TARGET_USER" bash -c '
            set -euo pipefail
            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
            if [ -x "$HOME/.local/bin/uv" ]; then
                "$HOME/.local/bin/uv" tool install --force appimage-builder
            fi
        '
    fi
}

configure_devsecops_runtime() {
    echo "==> [DevSecOps] Configuring wireshark/pcap permissions & Kali Rolling box (novos-sec-box)..."
    ensure_user_groups devsecops wireshark
    if [[ -x /usr/bin/dumpcap ]]; then
        run_or_record devsecops "dumpcap group" chgrp wireshark /usr/bin/dumpcap
        run_or_record devsecops "dumpcap mode" chmod 0750 /usr/bin/dumpcap
        run_or_record devsecops "dumpcap capabilities" setcap cap_net_raw,cap_net_admin=eip /usr/bin/dumpcap
    fi
    echo "==> [DevSecOps] Kali Rolling Distrobox ready! Run: novos-sec-box up && novos-sec-box enter"
}

configure_llm_runtime() {
    echo "==> [LLM] Configuring /srv/llm, sysctl, Docker, Hugging Face CLI, Ollama & ComfyUI Stack..."
    ensure_user_groups llm docker video render

    if [[ "$POST_CALAMARES" != "true" ]] && ! command -v docker >/dev/null 2>&1; then
        safe_apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    enable_units llm start docker containerd

    mkdir -p /srv/llm/{ollama,huggingface,comfyui,open-webui,models} /etc/sysctl.d
    ln -sfn /srv/llm/ollama /var/lib/ollama
    cat << 'SYSCTL_EOF' > /etc/sysctl.d/99-novos-llm.conf
vm.max_map_count = 1048576
vm.swappiness = 10
SYSCTL_EOF
    if [[ ! -f /srv/llm/docker-compose.yml ]]; then
        cat << 'COMPOSE_EOF' > /srv/llm/docker-compose.yml
name: novos-llm-stack

services:
  comfyui:
    image: yanwk/comfyui-boot:rocm
    container_name: novos-comfyui
    restart: unless-stopped
    ports:
      - "8188:8188"
    devices:
      - "/dev/kfd:/dev/kfd"
      - "/dev/dri:/dev/dri"
    group_add:
      - video
      - render
    ipc: host
    security_opt:
      - seccomp:unconfined
    environment:
      - HSA_OVERRIDE_GFX_VERSION=12.0.1
      - PYTORCH_ROCM_ARCH=gfx1201
      - HF_HOME=/root/.cache/huggingface
      - CLI_ARGS=--listen 0.0.0.0 --port 8188
    volumes:
      - /srv/llm/comfyui/storage:/root
      - /srv/llm/huggingface:/root/.cache/huggingface
      - /srv/llm/models:/srv/models

  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: novos-open-webui
    restart: unless-stopped
    ports:
      - "3000:8080"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
    volumes:
      - /srv/llm/open-webui:/app/backend/data
COMPOSE_EOF
    fi
    if id "$TARGET_USER" >/dev/null 2>&1; then
        chown -R "${TARGET_USER}:${TARGET_USER}" /srv/llm
    fi
    chmod 2775 /srv/llm

    mkdir -p /etc/systemd/system/ollama.service.d
    cat << 'OLLAMA_EOF' > /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_MODELS=/srv/llm/ollama/models"
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="HSA_OVERRIDE_GFX_VERSION=12.0.1"
OLLAMA_EOF

    if [[ "$POST_CALAMARES" == "true" ]]; then
        echo "==> [LLM] Post-Calamares offline mode: skipping online uv/Ollama/cargo downloads."
        return 0
    fi

    # Online installers: each one is its own recorded item (fatal in the image build).
    if id "$TARGET_USER" >/dev/null 2>&1; then
        run_or_record llm uv sudo -u "$TARGET_USER" bash -c '
            set -euo pipefail
            if [ ! -f "$HOME/.local/bin/uv" ]; then
                curl --connect-timeout 15 --max-time 300 --retry 2 -LsSf https://astral.sh/uv/install.sh | sh
            fi
        '
        # huggingface_hub needs uv; a missing uv is the recorded 'uv' item above.
        run_or_record llm huggingface_hub sudo -u "$TARGET_USER" bash -c '
            set -euo pipefail
            export PATH="$HOME/.local/bin:$PATH"
            if [ -x "$HOME/.local/bin/uv" ]; then
                "$HOME/.local/bin/uv" tool install --force "huggingface_hub[cli,hf_transfer]"
            fi
        '
    fi

    if ! command -v ollama >/dev/null 2>&1; then
        run_or_record llm ollama bash -c 'set -euo pipefail; curl --connect-timeout 15 --max-time 300 --retry 2 -fsSL https://ollama.com/install.sh | sh'
    fi
    # Only a systemd-booted system has a manager to reload (sd_booted: /run/systemd/system).
    if [[ -d /run/systemd/system ]]; then
        run_or_record llm "systemctl daemon-reload" systemctl daemon-reload
    fi

    if id "$TARGET_USER" >/dev/null 2>&1 && [[ -x "${USER_HOME}/.cargo/bin/cargo" ]]; then
        run_or_record llm amdgpu_top sudo -u "$TARGET_USER" "${USER_HOME}/.cargo/bin/cargo" install amdgpu_top
    fi

    echo "==> Containerized ComfyUI & Open WebUI ready! Launch anytime with: novos-llm-stack up"
}

configure_network_runtime() {
    echo "==> [Network] Configuring Samba [DevShare], Tailscale, Syncthing & Remote Services..."
    ensure_user_groups network sambashare

    mkdir -p /srv/devshare /etc/samba
    if id "$TARGET_USER" >/dev/null 2>&1; then
        chown -R "${TARGET_USER}:${TARGET_USER}" /srv/devshare
    fi
    chmod 2775 /srv/devshare

    if ! grep -q "\[DevShare\]" /etc/samba/smb.conf 2>/dev/null; then
        cat << SMB_EOF >> /etc/samba/smb.conf

[DevShare]
   comment = Development Shared Directory
   path = /srv/devshare
   browseable = yes
   read only = no
   guest ok = no
   valid users = ${TARGET_USER}
   force user = ${TARGET_USER}
   force group = ${TARGET_USER}
   create mask = 0664
   directory mask = 2775
SMB_EOF
    fi

    enable_units network nostart smbd nmbd ssh
}

configure_comms_flathub() {
    if [[ "$POST_CALAMARES" == "true" ]]; then
        echo "==> [Comms] Post-Calamares offline mode: skipping online Flathub browser/comms downloads."
        return 0
    fi
    if ! command -v flatpak >/dev/null 2>&1; then
        safe_apt_install flatpak
    fi
    if command -v flatpak >/dev/null 2>&1; then
        ensure_flathub_remote comms
        load_manifest_pkgs comms flatpak
        safe_flatpak_install "$CURRENT_PROFILE" "${MANIFEST_PKGS[@]}"
    fi
}

configure_utilities_flathub() {
    if [[ "$POST_CALAMARES" == "true" ]]; then
        echo "==> [Utilities] Post-Calamares offline mode: skipping online Flathub utility downloads."
        return 0
    fi
    if ! command -v flatpak >/dev/null 2>&1; then
        safe_apt_install flatpak
    fi
    if command -v flatpak >/dev/null 2>&1; then
        ensure_flathub_remote utilities
        load_manifest_pkgs utilities flatpak
        safe_flatpak_install "$CURRENT_PROFILE" "${MANIFEST_PKGS[@]}"
    fi
}

# ------------------------------------------------------------------------------
# Profile Installers & Hardware Opt-In/Out Handlers
# ------------------------------------------------------------------------------
install_wifi_profile() {
    echo "==> [Profile: WiFi] Enabling & installing Wireless Connectivity Stack + Firmware..."
    rm -f /etc/novos/no-wifi /etc/novos/wifi-disabled
    unmask_units wifi wpa_supplicant.service iwd.service
    load_manifest_pkgs wifi novos-meta apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    rfkill_set wifi unblock wifi
    verify_profile_deps wifi
}

install_no_wifi_profile() {
    echo "==> [Profile: No-WiFi] Opting out of Wireless Stack: disabling services & purging WiFi firmware..."
    mkdir -p /etc/novos
    touch /etc/novos/no-wifi
    rfkill_set no-wifi block wifi
    disable_units no-wifi iwd.service wpa_supplicant.service
    mask_units no-wifi iwd.service wpa_supplicant.service
    safe_apt_purge no-wifi \
        novos-profile-wifi \
        iwd wpasupplicant wireless-regdb iw \
        firmware-iwlwifi firmware-realtek firmware-atheros firmware-mediatek firmware-brcm80211
}

install_bluetooth_profile() {
    echo "==> [Profile: Bluetooth] Enabling & installing Bluetooth Audio & Peripherals Stack..."
    rm -f /etc/novos/no-bluetooth /etc/novos/bluetooth-disabled
    unmask_units bluetooth bluetooth.service
    load_manifest_pkgs bluetooth novos-meta apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    rfkill_set bluetooth unblock bluetooth
    enable_units bluetooth start bluetooth.service
    verify_profile_deps bluetooth
}

install_no_bluetooth_profile() {
    echo "==> [Profile: No-Bluetooth] Opting out of Bluetooth Stack: masking bluetooth.service & purging packages..."
    mkdir -p /etc/novos
    touch /etc/novos/no-bluetooth
    rfkill_set no-bluetooth block bluetooth
    disable_units no-bluetooth bluetooth.service
    mask_units no-bluetooth bluetooth.service
    safe_apt_purge no-bluetooth \
        novos-profile-bluetooth \
        bluez bluez-obexd blueman
}

install_gaming_profile() {
    echo "==> [Profile: Gaming] Installing Gaming & Emulation Suite (APT + Flathub)..."
    load_manifest_pkgs gaming apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    configure_gaming_flathub
    verify_profile_deps gaming
}

# Installs a profile's `source: custom-tool` manifest entries (e.g. zig) through novos-custom-tools --tool.
# Each entry is a pinned online download. A failure is fatal, except post-Calamares, where it warns
# with a retry command and continues so the install itself still completes.
install_manifest_custom_tools() {
    local profile="$1"
    local name
    load_manifest_pkgs "$profile" custom-tool
    for name in "${MANIFEST_PKGS[@]}"; do
        echo "==> [Profile: ${profile}] Installing custom tool '${name}' via novos-custom-tools --tool..."
        if [[ "$POST_CALAMARES" == "true" ]]; then
            if ! /usr/local/bin/novos-custom-tools --manifest /etc/novos/custom-tools.yaml --tool "$name"; then
                echo "[WARN] [Profile: ${profile}] custom tool '${name}' failed to install; retry with: sudo novos-setup --install ${profile}" >&2
                record_install_failure "$profile" "$name" "custom-tool install failed"
                continue
            fi
        else
            /usr/local/bin/novos-custom-tools --manifest /etc/novos/custom-tools.yaml --tool "$name"
        fi
    done
}

install_dev_profile() {
    echo "==> [Profile: Dev] Installing Antigravity, Editors, Docker/Podman/Distrobox, KVM & Polyglot Toolchains..."
    load_manifest_pkgs dev apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    install_manifest_custom_tools dev
    configure_dev_runtime
    verify_profile_deps dev
}

install_devsecops_profile() {
    echo "==> [Profile: DevSecOps] Installing Pentesting Suite, Firewalls & Kali Rolling Box (novos-sec-box)..."
    load_manifest_pkgs devsecops apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    configure_devsecops_runtime
    verify_profile_deps devsecops
}

install_llm_profile() {
    echo "==> [Profile: Local AI / LLM] Installing Ollama, llama.cpp, Hugging Face CLI, GPU Monitors & ComfyUI Stack..."
    load_manifest_pkgs llm apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    configure_llm_runtime
    verify_profile_deps llm
}

install_network_profile() {
    echo "==> [Profile: Network / Remote] Installing Samba (DevShare), Remmina, WireGuard, Tailscale, Syncthing & SSH..."
    load_manifest_pkgs network apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    configure_network_runtime
    verify_profile_deps network
}

install_comms_profile() {
    echo "==> [Profile: Browsers, Email & Comms] Installing Firefox ESR, Chromium, Thunderbird & Flathub Comms..."
    load_manifest_pkgs comms apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    configure_comms_flathub
    verify_profile_deps comms
}

install_browsers_profile() {
    install_comms_profile
}

install_media_profile() {
    echo "==> [Profile: Media, Pro Audio & Creative] Installing Media Players, DAW, 2D/3D & CAD Suite..."
    load_manifest_pkgs media apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    verify_profile_deps media
}

install_utilities_profile() {
    echo "==> [Profile: System Admin, Btrfs & Office] Installing LibreOffice, Btrfs Tools & Workstation Utilities..."
    load_manifest_pkgs utilities apt
    safe_apt_install "${MANIFEST_PKGS[@]}"
    configure_utilities_flathub
    verify_profile_deps utilities
}

install_custom_tools_profile() {
    echo "==> [Custom Tools] Running novos-custom-tools --manifest /etc/novos/custom-tools.yaml --install-enabled..."
    if [[ -x /usr/local/bin/novos-custom-tools && -f /etc/novos/custom-tools.yaml ]]; then
        /usr/local/bin/novos-custom-tools --manifest /etc/novos/custom-tools.yaml --install-enabled
    else
        echo "[WARN] /usr/local/bin/novos-custom-tools or /etc/novos/custom-tools.yaml not found." >&2
    fi
}

run_post_calamares() {
    echo "==> [Post-Calamares] Detecting netinstall marker packages & hardware opt-outs for user '${TARGET_USER}'..."
    if [[ -n "$TARGET_USER" && "$TARGET_USER" != "devuser" ]] && id -u "$TARGET_USER" >/dev/null 2>&1; then
        # Explicit state tests: userdel usually removes the devuser group with the user.
        if id -u devuser >/dev/null 2>&1; then
            run_or_record post-calamares "remove user devuser" userdel -r -f devuser
        fi
        if getent group devuser >/dev/null; then
            run_or_record post-calamares "remove group devuser" groupdel devuser
        fi
        run_or_record post-calamares "remove devuser leftovers" rm -rf /home/devuser /var/spool/mail/devuser /var/lib/AccountsService/users/devuser /var/lib/AccountsService/icons/devuser
    fi
    if id -u "$TARGET_USER" >/dev/null 2>&1; then
        run_or_record post-calamares "chown /srv/llm /srv/devshare" chown -R "${TARGET_USER}:${TARGET_USER}" /srv/llm /srv/devshare
    fi

    # 1. Check WiFi opt-out state (explicit opt-out marker or deselected wireless stack marker package)
    if [[ -f /etc/novos/no-wifi || -f /etc/novos/wifi-disabled || "${NOVOS_NO_WIFI:-0}" == "1" ]] || \
       ! is_pkg_installed novos-profile-wifi || \
       ! any_pkg_installed iwd wpasupplicant; then
        install_no_wifi_profile
    else
        verify_profile_deps wifi
    fi

    # 2. Check Bluetooth opt-out state (explicit opt-out marker or deselected bluetooth stack marker package)
    if [[ -f /etc/novos/no-bluetooth || -f /etc/novos/bluetooth-disabled || "${NOVOS_NO_BLUETOOTH:-0}" == "1" ]] || \
       ! is_pkg_installed novos-profile-bluetooth || \
       ! any_pkg_installed bluez bluez-obexd blueman; then
        install_no_bluetooth_profile
    else
        unmask_units bluetooth bluetooth.service
        enable_units bluetooth nostart bluetooth.service
        verify_profile_deps bluetooth
    fi

    if any_pkg_installed steam-installer lutris mangohud vkbasalt goverlay gamescope wine winetricks retroarch dolphin-emu pcsx2 openrgb corectrl; then
        configure_gaming_flathub
        verify_profile_deps gaming
    fi
    if any_pkg_installed docker-ce podman buildah skopeo distrobox qemu-system-x86 libvirt-daemon-system virt-manager code antigravity neovim hx; then
        install_manifest_custom_tools dev
        configure_dev_runtime
        verify_profile_deps dev
    fi
    if any_pkg_installed nmap wireshark tshark john hashcat aircrack-ng hydra sqlmap gobuster radare2 ufw firewalld; then
        configure_devsecops_runtime
        verify_profile_deps devsecops
    fi
    if any_pkg_installed nvtop btop rocm-smi libcurl4-openssl-dev libvulkan-dev glslang-tools spirv-tools; then
        configure_llm_runtime
        verify_profile_deps llm
    fi
    if any_pkg_installed tailscale wireguard wireguard-tools network-manager-openvpn remmina remmina-plugin-rdp remmina-plugin-vnc samba cifs-utils nfs-common avahi-daemon openssh-server rclone syncthing; then
        configure_network_runtime
        verify_profile_deps network
    fi
    if any_pkg_installed firefox-esr chromium thunderbird; then
        configure_comms_flathub
        verify_profile_deps comms
    fi
    if any_pkg_installed vlc mpv handbrake obs-studio kdenlive gimp inkscape krita darktable blender freecad audacity ardour pipewire-jack; then
        verify_profile_deps media
    fi
    if any_pkg_installed libreoffice fastfetch gparted keepassxc tmux ripgrep fd-find timeshift btrfs-assistant; then
        configure_utilities_flathub
        verify_profile_deps utilities
    fi

    # Surface recorded failures: login autostart for the desktop user; the Calamares log copy is
    # printed by the next shellprocess-novos.conf command (novos-setup --report --log-only).
    if [[ -s "$FAILURE_TSV" ]]; then
        mkdir -p "$(dirname "$INSTALL_REPORT_DESKTOP")"
        cat << 'REPORT_DESKTOP_EOF' > "$INSTALL_REPORT_DESKTOP"
[Desktop Entry]
Type=Application
Name=NovOS Install Report
Comment=Lists packages that failed to install during NovOS setup
Exec=/usr/local/bin/novos-setup --report --notify
Terminal=false
NoDisplay=true
REPORT_DESKTOP_EOF
        chmod 0644 "$INSTALL_REPORT_DESKTOP"
        echo "[WARN] [Post-Calamares] Install-time failures recorded in ${FAILURE_TSV}; login autostart ${INSTALL_REPORT_DESKTOP} written." >&2
    fi
}

# ------------------------------------------------------------------------------
# Execution Dispatch
# ------------------------------------------------------------------------------
if [[ -n "$SELECTED_PACKAGES" ]]; then
    echo "==> [Packages] Installing granular APT packages: ${SELECTED_PACKAGES}..."
    IFS=', ' read -ra GRANULAR_PKGS <<< "$SELECTED_PACKAGES"
    CURRENT_PROFILE="packages"
    safe_apt_install "${GRANULAR_PKGS[@]}"
fi

if [[ -n "$SELECTED_PROFILES" ]]; then
    IFS=', ' read -ra PROFILES <<< "$SELECTED_PROFILES"
    for prof in "${PROFILES[@]}"; do
        case "$prof" in
            wifi)          install_wifi_profile ;;
            no-wifi)       install_no_wifi_profile ;;
            bluetooth)     install_bluetooth_profile ;;
            no-bluetooth)  install_no_bluetooth_profile ;;
            gaming)        install_gaming_profile ;;
            dev)           install_dev_profile ;;
            devsecops)     install_devsecops_profile ;;
            llm)           install_llm_profile ;;
            network)       install_network_profile ;;
            comms|browsers) install_comms_profile ;;
            media)         install_media_profile ;;
            utilities)     install_utilities_profile ;;
            custom-tools)  install_custom_tools_profile ;;
            all)
                install_wifi_profile
                install_bluetooth_profile
                install_gaming_profile
                install_dev_profile
                install_devsecops_profile
                install_llm_profile
                install_network_profile
                install_comms_profile
                install_media_profile
                install_utilities_profile
                install_custom_tools_profile
                ;;
            none|"") ;;
            *)         echo "[WARN] Unknown profile: $prof" >&2 ;;
        esac
    done
fi

if [[ "$RUN_CUSTOM_TOOLS" == "true" ]]; then
    install_custom_tools_profile
fi

if [[ "$POST_CALAMARES" == "true" ]]; then
    run_post_calamares
fi

echo "=== NovOS Profile Provisioning Complete! ==="
EOF
chmod +x /usr/local/bin/novos-setup

# Backward-compatible symlink so `setup-workstation` launches `novos-setup`
ln -sfn /usr/local/bin/novos-setup /usr/local/bin/setup-workstation

echo "==> [Stage 5.4] Executing novos-custom-tools & Pre-Configured Build Profiles..."
(
    cd "${REPO_ROOT}"
    /usr/local/bin/novos-custom-tools --manifest /etc/novos/custom-tools.yaml --install-enabled
)

# If BUILD_PROFILES is set in config/build.env (not "none"), run them now inside chroot
if [[ -n "${BUILD_PROFILES:-}" && "${BUILD_PROFILES}" != "none" ]]; then
    echo "==> [Stage 5.4b] Pre-installing configured build profiles (${BUILD_PROFILES})..."
    /usr/local/bin/novos-setup --install "${BUILD_PROFILES}" --user "${DEFAULT_USER}"
fi

echo "==> [Stage 5.5] Provisioning Calamares module configurations into /etc/calamares/modules/ and /usr/share/calamares/modules/..."
mkdir -p /etc/calamares/modules /usr/share/calamares/modules
cp -f "${REPO_ROOT}/calamares/modules/"*.conf /etc/calamares/modules/
cp -f "${REPO_ROOT}/calamares/modules/"*.yaml /etc/calamares/modules/
cp -f "${REPO_ROOT}/calamares/modules/"*.conf /usr/share/calamares/modules/
cp -f "${REPO_ROOT}/calamares/modules/"*.yaml /usr/share/calamares/modules/
test -f /etc/calamares/modules/fstab.conf
test -f /etc/calamares/modules/machineid.conf

verify_stage5_post
echo "==> [Stage 5] Profile manager, custom-tools, DevSecOps Kali box, and LLM/Dev environment wiring complete."
