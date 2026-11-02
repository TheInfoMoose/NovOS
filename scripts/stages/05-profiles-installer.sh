#!/usr/bin/env bash
# ==============================================================================
# Stage 5 (Inside Chroot): Modular Profile Provisioner (novos-setup)
# Profiles: Gaming | Dev & Antigravity | Local AI / ComfyUI / LLM | Network & Samba
# ==============================================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export DEBIAN_FRONTEND=noninteractive

source /root/novos-build/config/build.env
source /root/novos-build/scripts/lib/verify-deps.sh

echo "==> [Stage 5.1] Configuring Anti-Bloat LLM & Developer Environment Paths..."
mkdir -p /srv/llm/{ollama,huggingface,comfyui,open-webui,models} /srv/devshare
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

echo "==> [Stage 5.3] Installing Modular Profile Manager (/usr/local/bin/novos-setup)..."
cat << 'EOF' > /usr/local/bin/novos-setup
#!/usr/bin/env bash
# ==============================================================================
# NovOS Modular Software Profile Installer (TUI, CLI & Calamares Post-Install)
# Profiles: gaming, dev, llm, network, browsers, media, utilities
# Usage:
#   Interactive TUI:   sudo novos-setup
#   CLI Profiles:      sudo novos-setup --install gaming,dev,llm,network,browsers,media,utilities --user devuser
#   Granular APT:      sudo novos-setup --packages fastfetch,tmux,ripgrep --user devuser
#   Calamares Hook:    sudo novos-setup --post-calamares --user devuser
# ==============================================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export DEBIAN_FRONTEND=noninteractive

ORIG_ARGS=("$@")
TARGET_USER="${SUDO_USER:-}"
USER_SPECIFIED="false"
SELECTED_PROFILES=""
SELECTED_PACKAGES=""
POST_CALAMARES="false"
INTERACTIVE="true"
APT_UPDATED="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install|-i)
            if [[ $# -lt 2 ]]; then
                echo "[ERROR] Option $1 requires a profile list argument." >&2
                exit 1
            fi
            SELECTED_PROFILES="${2:-}"
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
        --post-calamares)
            POST_CALAMARES="true"
            INTERACTIVE="false"
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
  --install, -i <profiles>   Comma-separated profiles to install:
                             gaming,dev,llm,network,browsers,media,utilities (or 'all')
  --packages, -p <pkgs>      Comma-separated list of individual APT packages to install safely
  --post-calamares           Detect Calamares netinstall marker packages and configure
                             Flathub gaming apps, Rust/uv, Ollama/ComfyUI (/srv/llm), and Samba [DevShare]
  --user, -u <username>      Target non-root user to configure (default: primary regular user)
  --help, -h                 Show this help message
HELP_EOF
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

if [[ "$EUID" -ne 0 ]]; then
    echo "Elevating privileges with sudo..."
    if [[ "$USER_SPECIFIED" == "true" ]]; then
        exec sudo -E "$0" "${ORIG_ARGS[@]}"
    else
        exec sudo -E "$0" --user "${USER:-devuser}" "${ORIG_ARGS[@]}"
    fi
fi

# Resolve target user; prefer explicit --user or SUDO_USER if valid, else latest regular user (UID 1000-59999)
if [[ -z "$TARGET_USER" ]] || ! id "$TARGET_USER" >/dev/null 2>&1; then
    DETECTED_USER="$(awk -F: '$3 >= 1000 && $3 < 60000 {u=$1} END {print u}' /etc/passwd || true)"
    if [[ -n "$DETECTED_USER" ]]; then
        TARGET_USER="$DETECTED_USER"
    else
        TARGET_USER="devuser"
    fi
fi

USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6 || true)"
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

    dpkg --configure -a >/dev/null 2>&1 || true

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
    local pkg
    for pkg in "$@"; do
        [[ -z "$pkg" ]] && continue
        if apt-cache show "$pkg" >/dev/null 2>&1; then
            available_pkgs+=("$pkg")
        else
            echo "[WARN] Optional/requested package '${pkg}' not found in APT cache; skipping." >&2
        fi
    done

    if [[ "${#available_pkgs[@]}" -gt 0 ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-remove "${available_pkgs[@]}"
    else
        echo "[WARN] No installable packages matched in current batch." >&2
    fi
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
        gaming)
            bins=(gamemoded gamescope mangohud lutris wine flatpak)
            ;;
        dev)
            bins=(docker python3 clang cmake ninja ghc cabal)
            services=(docker)
            ;;
        llm)
            bins=(docker nvtop btop novos-llm-stack)
            ;;
        network)
            bins=(smbd sshd wg remmina)
            services=(smbd ssh)
            ;;
        browsers)
            bins=(firefox-esr chromium)
            ;;
        media)
            bins=(vlc mpv obs kdenlive gimp inkscape blender)
            ;;
        utilities)
            bins=(libreoffice fastfetch gparted keepassxc tmux rg fdfind)
            ;;
        *)
            return 0
            ;;
    esac

    local bin
    for bin in "${bins[@]}"; do
        if command -v "$bin" >/dev/null 2>&1 || [[ -x "/usr/sbin/${bin}" || -x "/usr/local/bin/${bin}" ]]; then
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
# Interactive Whiptail Menu (All 7 Categories from netinstall.yaml)
# ------------------------------------------------------------------------------
if [[ "$INTERACTIVE" == "true" ]]; then
    CHOICES=$(whiptail --title "NovOS Profile Setup" \
        --checklist "Select the software profiles to install (Space to toggle, Enter to confirm):" 22 92 10 \
        "gaming"    "Steam, Lutris, Heroic, ProtonPlus, Bottles, Gamescope, MangoHud" ON \
        "dev"       "Antigravity/agy, VS Code, Docker, Rust, uv, Haskell, alien/AppImage" ON \
        "llm"       "Ollama, llama.cpp, ComfyUI & Open WebUI (Docker), HuggingFace CLI" ON \
        "network"   "Samba (DevShare), Remmina (RDP/VNC), Tailscale, WireGuard, SSH" ON \
        "browsers"  "Firefox ESR and Chromium Web Browsers" OFF \
        "media"     "VLC, mpv, OBS Studio, Kdenlive, GIMP, Inkscape, Blender" OFF \
        "utilities" "LibreOffice, Fastfetch, GParted, KeePassXC, tmux, ripgrep, fd-find" OFF \
        3>&1 1>&2 2>&3) || exit 0
    SELECTED_PROFILES="$(echo "$CHOICES" | tr -d '"' | tr ' ' ',')"
fi

# ------------------------------------------------------------------------------
# Post-Install Configuration Helpers (Shared by Profile Installers & --post-calamares)
# ------------------------------------------------------------------------------
configure_gaming_flathub() {
    echo "==> [Gaming] Configuring gaming groups, permissions & Flathub..."
    for grp in gamemode render video input audio; do
        groupadd -f "$grp" 2>/dev/null || true
    done
    if id "$TARGET_USER" >/dev/null 2>&1; then
        for grp in gamemode render video input audio; do
            getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$TARGET_USER" || true
        done
    fi

    if [[ "$POST_CALAMARES" == "true" ]]; then
        echo "==> [Gaming] Post-Calamares offline mode: skipping online Flathub remote/app downloads."
        return 0
    fi

    if ! command -v flatpak >/dev/null 2>&1; then
        safe_apt_install flatpak
    fi
    if command -v flatpak >/dev/null 2>&1; then
        flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true
        flatpak install -y flathub \
            com.valvesoftware.Steam \
            com.heroicgameslauncher.hgl \
            com.vysp3r.ProtonPlus \
            com.github.Matoking.protontricks \
            com.usebottles.bottles || true

        if [[ -d "/mnt/raid0" ]] && id "$TARGET_USER" >/dev/null 2>&1; then
            sudo -u "$TARGET_USER" flatpak override --user --filesystem=/mnt/raid0 com.valvesoftware.Steam || true
            sudo -u "$TARGET_USER" flatpak override --user --filesystem=/mnt/raid0 com.heroicgameslauncher.hgl || true
            sudo -u "$TARGET_USER" flatpak override --user --filesystem=/mnt/raid0 com.github.Matoking.protontricks || true
            sudo -u "$TARGET_USER" flatpak override --user --filesystem=/mnt/raid0 com.usebottles.bottles || true
        fi
    fi
}

configure_dev_runtime() {
    echo "==> [Dev] Configuring Docker/libvirt services, user groups, Rust (rustup) & Astral uv..."
    for grp in docker libvirt kvm video render input audio; do
        groupadd -f "$grp" 2>/dev/null || true
    done
    if id "$TARGET_USER" >/dev/null 2>&1; then
        for grp in docker libvirt kvm video render input audio; do
            getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$TARGET_USER" || true
        done
    fi

    if [[ "$POST_CALAMARES" == "true" ]] || systemd-detect-virt --chroot >/dev/null 2>&1; then
        systemctl enable docker containerd libvirtd 2>/dev/null || true
    else
        systemctl enable --now docker containerd libvirtd 2>/dev/null || true
    fi

    if [[ "$POST_CALAMARES" == "true" ]]; then
        echo "==> [Dev] Post-Calamares offline mode: skipping online rustup/uv network installers."
        return 0
    fi

    if id "$TARGET_USER" >/dev/null 2>&1; then
        sudo -u "$TARGET_USER" bash -c '
            set -e
            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
            if [ ! -d "$HOME/.cargo" ]; then
                curl --connect-timeout 15 --max-time 300 --retry 2 --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --component rust-analyzer clippy rustfmt || true
            fi
            if [ ! -f "$HOME/.local/bin/uv" ]; then
                curl --connect-timeout 15 --max-time 300 --retry 2 -LsSf https://astral.sh/uv/install.sh | sh || true
            fi
            if [ -x "$HOME/.local/bin/uv" ]; then
                "$HOME/.local/bin/uv" tool install --force appimage-builder || true
            fi
        ' || true
    fi
}

configure_llm_runtime() {
    echo "==> [LLM] Configuring /srv/llm, sysctl, Docker, Hugging Face CLI, Ollama & ComfyUI Stack..."
    for grp in docker video render; do
        groupadd -f "$grp" 2>/dev/null || true
    done

    if [[ "$POST_CALAMARES" != "true" ]] && ! command -v docker >/dev/null 2>&1; then
        safe_apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    if [[ "$POST_CALAMARES" == "true" ]] || systemd-detect-virt --chroot >/dev/null 2>&1; then
        systemctl enable docker containerd 2>/dev/null || true
    else
        systemctl enable --now docker containerd 2>/dev/null || true
    fi

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
        for grp in docker video render; do
            getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$TARGET_USER" || true
        done
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

    if id "$TARGET_USER" >/dev/null 2>&1; then
        sudo -u "$TARGET_USER" bash -c '
            set -e
            export PATH="$HOME/.local/bin:$PATH"
            if [ ! -f "$HOME/.local/bin/uv" ]; then
                curl --connect-timeout 15 --max-time 300 --retry 2 -LsSf https://astral.sh/uv/install.sh | sh || true
            fi
            if [ -x "$HOME/.local/bin/uv" ]; then
                "$HOME/.local/bin/uv" tool install --force "huggingface_hub[cli,hf_transfer]" || true
            fi
        ' || true
    fi

    if ! command -v ollama >/dev/null 2>&1; then
        curl --connect-timeout 15 --max-time 300 --retry 2 -fsSL https://ollama.com/install.sh | sh || true
    fi
    systemctl daemon-reload 2>/dev/null || true

    if id "$TARGET_USER" >/dev/null 2>&1 && [[ -x "${USER_HOME}/.cargo/bin/cargo" ]]; then
        sudo -u "$TARGET_USER" "${USER_HOME}/.cargo/bin/cargo" install amdgpu_top || true
    fi

    echo "==> Containerized ComfyUI & Open WebUI ready! Launch anytime with: novos-llm-stack up"
}

configure_network_runtime() {
    echo "==> [Network] Configuring Samba [DevShare], Tailscale & Remote Services..."
    for grp in sambashare; do
        groupadd -f "$grp" 2>/dev/null || true
    done
    if id "$TARGET_USER" >/dev/null 2>&1; then
        for grp in sambashare; do
            getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$TARGET_USER" || true
        done
    fi

    if [[ "$POST_CALAMARES" != "true" ]] && ! command -v tailscale >/dev/null 2>&1; then
        curl --connect-timeout 15 --max-time 300 --retry 2 -fsSL https://tailscale.com/install.sh | sh || true
    fi

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

    systemctl enable smbd nmbd ssh 2>/dev/null || true
}

# ------------------------------------------------------------------------------
# Profile Installers (All 7 Categories)
# ------------------------------------------------------------------------------
install_gaming_profile() {
    echo "==> [Profile: Gaming] Installing Gaming Stack (APT + Flathub)..."
    safe_apt_install \
        gamemode gamescope mangohud goverlay lutris vulkan-tools \
        wine wine64 wine32:i386 libwine:i386 winetricks flatpak
    configure_gaming_flathub
    verify_profile_deps gaming
}

install_dev_profile() {
    echo "==> [Profile: Dev] Installing Docker, VS Code, Antigravity, Haskell, Rust, Python (uv) & Packaging Tools..."
    safe_apt_install \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
        code antigravity antigravity-cli \
        python3 python3-pip python3-venv python3-dev python3-full \
        pkg-config libssl-dev libudev-dev clang lld llvm cmake ninja-build \
        ghc cabal-install haskell-stack \
        alien rpm2cpio cpio patchelf desktop-file-utils squashfs-tools zsync strace fakeroot \
        ghostty alacritty
    configure_dev_runtime
    verify_profile_deps dev
}

install_llm_profile() {
    echo "==> [Profile: Local AI / LLM] Installing Ollama, llama.cpp, Hugging Face CLI, GPU Monitors & ComfyUI Stack..."
    safe_apt_install \
        nvtop radeontop btop rocm-smi libcurl4-openssl-dev libvulkan-dev glslc
    configure_llm_runtime
    verify_profile_deps llm
}

install_network_profile() {
    echo "==> [Profile: Network / Remote] Installing Samba (DevShare), Remmina, WireGuard, Tailscale & SSH..."
    safe_apt_install \
        remmina remmina-plugin-rdp remmina-plugin-vnc \
        samba smbclient cifs-utils wireguard-tools openssh-server
    configure_network_runtime
    verify_profile_deps network
}

install_browsers_profile() {
    echo "==> [Profile: Browsers] Installing Web Browsers (Firefox ESR & Chromium)..."
    safe_apt_install \
        firefox-esr chromium
    verify_profile_deps browsers
}

install_media_profile() {
    echo "==> [Profile: Media & Creative] Installing Media Players, Streaming & Design Suite..."
    safe_apt_install \
        vlc mpv obs-studio kdenlive gimp inkscape blender
    verify_profile_deps media
}

install_utilities_profile() {
    echo "==> [Profile: Utilities & Office] Installing LibreOffice & Workstation Utilities..."
    safe_apt_install \
        libreoffice fastfetch gparted keepassxc tmux ripgrep fd-find
    verify_profile_deps utilities
}

run_post_calamares() {
    echo "==> [Post-Calamares] Detecting netinstall marker packages for user '${TARGET_USER}'..."
    if any_pkg_installed steam-installer lutris mangohud vkbasalt goverlay gamescope wine winetricks retroarch dolphin-emu ppsspp; then
        configure_gaming_flathub
        verify_profile_deps gaming
    fi
    if any_pkg_installed docker-ce podman buildah skopeo kubectl helm qemu-system-x86 libvirt-daemon-system virt-manager sqlite3 postgresql-client redis-tools code antigravity neovim helix; then
        configure_dev_runtime
        verify_profile_deps dev
    fi
    if any_pkg_installed python3-venv python3-pip clinfo rocm-smi nvtop; then
        configure_llm_runtime
        verify_profile_deps llm
    fi
    if any_pkg_installed tailscale wireguard wireguard-tools network-manager-openvpn remmina remmina-plugin-rdp remmina-plugin-vnc samba cifs-utils nfs-common avahi-daemon openssh-server rclone syncthing; then
        configure_network_runtime
        verify_profile_deps network
    fi
    if any_pkg_installed firefox-esr chromium; then
        verify_profile_deps browsers
    fi
    if any_pkg_installed vlc mpv obs-studio kdenlive gimp inkscape blender; then
        verify_profile_deps media
    fi
    if any_pkg_installed libreoffice fastfetch gparted keepassxc tmux ripgrep fd-find; then
        verify_profile_deps utilities
    fi
}

# ------------------------------------------------------------------------------
# Execution Dispatch
# ------------------------------------------------------------------------------
if [[ -n "$SELECTED_PACKAGES" ]]; then
    echo "==> [Packages] Installing granular APT packages: ${SELECTED_PACKAGES}..."
    IFS=', ' read -ra GRANULAR_PKGS <<< "$SELECTED_PACKAGES"
    safe_apt_install "${GRANULAR_PKGS[@]}"
fi

if [[ -n "$SELECTED_PROFILES" ]]; then
    IFS=', ' read -ra PROFILES <<< "$SELECTED_PROFILES"
    for prof in "${PROFILES[@]}"; do
        case "$prof" in
            gaming)    install_gaming_profile ;;
            dev)       install_dev_profile ;;
            llm)       install_llm_profile ;;
            network)   install_network_profile ;;
            browsers)  install_browsers_profile ;;
            media)     install_media_profile ;;
            utilities) install_utilities_profile ;;
            all)
                install_gaming_profile
                install_dev_profile
                install_llm_profile
                install_network_profile
                install_browsers_profile
                install_media_profile
                install_utilities_profile
                ;;
            none|"") ;;
            *)         echo "[WARN] Unknown profile: $prof" >&2 ;;
        esac
    done
fi

if [[ "$POST_CALAMARES" == "true" ]]; then
    run_post_calamares
fi

echo "=== NovOS Profile Provisioning Complete! ==="
EOF
chmod +x /usr/local/bin/novos-setup

# Backward-compatible symlink so `setup-workstation` launches `novos-setup`
ln -sfn /usr/local/bin/novos-setup /usr/local/bin/setup-workstation

# If BUILD_PROFILES is set in config/build.env (not "none"), run them now inside chroot
if [[ -n "${BUILD_PROFILES:-}" && "${BUILD_PROFILES}" != "none" ]]; then
    echo "==> [Stage 5.4] Pre-installing configured build profiles (${BUILD_PROFILES})..."
    /usr/local/bin/novos-setup --install "${BUILD_PROFILES}" --user "${DEFAULT_USER}"
fi

echo "==> [Stage 5.5] Provisioning Calamares fstab.conf into /etc/calamares/modules/fstab.conf..."
REPO_ROOT="/root/novos-build"
mkdir -p /etc/calamares/modules
cp -f "${REPO_ROOT}/calamares/modules/fstab.conf" /etc/calamares/modules/fstab.conf
test -f /etc/calamares/modules/fstab.conf

verify_stage5_post
echo "==> [Stage 5] Profile manager and LLM/Dev environment wiring complete."
