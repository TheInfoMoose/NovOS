# NovOS Modular Software Profiles (`novos-setup`)

[**← Back to README**](../README.md) · [**System Architecture**](ARCHITECTURE.md) · [**Build & QEMU Verification**](BUILD_AND_TEST.md) · [**Branding & Nova Theme**](BRANDING_AND_THEME.md)

---

## 1. Overview

To keep the base NovOS image lean, fast to install, and free of unwanted background services, optional workload suites are decoupled into four modular profiles managed by `/usr/local/bin/novos-setup` (provisioned in [`scripts/stages/05-profiles-installer.sh`](../scripts/stages/05-profiles-installer.sh)):

1. **`gaming`** — Steam, Heroic, Bottles, ProtonPlus, Lutris, Gamescope, GameMode, MangoHud, Wine & 32-bit Vulkan stack
2. **`dev`** — Google Antigravity (`antigravity` IDE & `agy` CLI), Docker Engine, VS Code, Rust, Python (`uv`), Haskell, C/C++/LLVM & packaging tools
3. **`llm`** — RDNA 4 (`RX 9070 XT` / `gfx1201`) accelerated Ollama, Hugging Face CLI, and containerized ComfyUI + Open WebUI (`novos-llm-stack`)
4. **`network`** — Samba `[DevShare]` file server (`/srv/devshare`), Remmina (`RDP`/`VNC`), Tailscale, WireGuard & OpenSSH Server

---

## 2. Installing Profiles

Profiles can be installed at three points in the NovOS lifecycle:

### A. During Calamares OS Installation (GUI Checkboxes)
On the **Software Profiles** (`netinstall`) step in the Calamares installer ([`calamares/modules/netinstall.yaml`](../calamares/modules/netinstall.yaml)), check any combination of the four profiles. Calamares marks the selected `novos-profile-*` packages and triggers `novos-setup` during the final `shellprocess@novos` stage.

### B. Anytime Inside the Installed OS (`novos-setup` TUI / CLI)

```bash
# Launch interactive whiptail checklist:
sudo novos-setup

# Install specific profiles non-interactively for a target user:
sudo novos-setup --install gaming,dev,llm,network --user devuser

# Install a single profile:
sudo novos-setup --install llm --user devuser
```

### C. Pre-Baked at ISO Build Time (`--profiles`)

```bash
sudo ./scripts/build-novos.sh --target-iso ./build/NovOS-sid-amd64.iso --profiles gaming,dev,llm,network
```

---

## 3. Profile Specifications

### 3.1 `gaming` — Gaming & Proton/Wine Stack

| Category | Packages & Components |
| :--- | :--- |
| **APT Native Packages** | `lutris`, `gamemode`, `gamescope`, `mangohud`, `goverlay`, `vulkan-tools`, `wine`, `wine64`, `wine32:i386`, `winetricks`, `mesa-vulkan-drivers:i386`, `libvulkan1:i386` |
| **Flathub Applications** (`@flatpak`) | `com.valvesoftware.Steam`, `com.heroicgameslauncher.hgl`, `com.vysp3r.ProtonPlus`, `com.github.Matoking.protontricks`, `com.usebottles.bottles` |
| **Storage Integration** | Installs into `/var/lib/flatpak` (`@flatpak` subvolume, excluded from `/` snapshots). Automatically grants Flatpak filesystem access (`flatpak override --filesystem=/mnt/raid0`) when a secondary game array is mounted at `/mnt/raid0`. |

---

### 3.2 `dev` — Developer Workstation, Containers & Google Antigravity

| Category | Packages & Components |
| :--- | :--- |
| **Google Antigravity** | `antigravity` (Antigravity IDE / 2.0) & `antigravity-cli` (`agy` CLI), installed from the pre-configured Google Antigravity APT repository (`antigravity.list`) |
| **Containers & Editor** | `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, `docker-compose-plugin`, `code` (Visual Studio Code) |
| **C / C++ / LLVM Toolchain** | `build-essential`, `clang`, `lld`, `llvm`, `lldb`, `cmake`, `ninja-build`, `pkg-config`, `gdb`, `valgrind`, `strace`, `ltrace` |
| **Rust Toolchain** | `rustup` initialized for `$TARGET_USER` with `stable` toolchain + `rust-analyzer`, `clippy`, and `rustfmt` (`~/.cargo/bin`) |
| **Python & Haskell** | `python3-full`, `python3-pip`, `python3-venv`, `pipx`, Astral `uv` (`/usr/local/bin/uv`), `ghc`, `cabal-install`, `haskell-stack` |
| **CLI Power Tools** | `git`, `git-lfs`, `gh`, `ripgrep`, `fd-find`, `bat`, `eza`, `fzf`, `jq`, `yq`, ` btop`, `htop`, `tmux`, `neovim` |
| **Packaging & Binary Utils** | `appimage-builder`, `alien`, `rpm`, `rpm2cpio`, `cpio`, `patchelf`, `squashfs-tools`, `desktop-file-utils`, `zsync`, `dpkg-dev`, `debhelper`, `devscripts`, `fakeroot`, `lintian` |
| **Storage & Permissions** | Adds `$TARGET_USER` to the `docker` group and isolates all container layers inside `@docker` (`/var/lib/docker`, `chattr +C`) and `@containerd` (`/var/lib/containerd`, `chattr +C`). |

---

### 3.3 `llm` — Local AI, Ollama, ComfyUI & Open WebUI (RDNA 4 Accelerated)

Engineered specifically for the **AMD Radeon RX 9070 XT (`gfx1201` / RDNA 4)** with all weights and workspaces isolated on the No-CoW (`chattr +C`) `@llm` subvolume at `/srv/llm`.

| Component | Configuration & Paths |
| :--- | :--- |
| **Global Environment (`/etc/profile.d/novos-env.sh`)** | `HF_HOME="/srv/llm/huggingface"`<br/>`OLLAMA_MODELS="/srv/llm/ollama/models"` |
| **Ollama Service (`ollama.service`)** | Symlinks `/var/lib/ollama` → `/srv/llm/ollama`. Configures `/etc/systemd/system/ollama.service.d/override.conf` with:<br/>`Environment="OLLAMA_MODELS=/srv/llm/ollama/models"`<br/>`Environment="OLLAMA_HOST=0.0.0.0:11434"`<br/>`Environment="HSA_OVERRIDE_GFX_VERSION=12.0.1"`<br/>`Environment="ROCR_VISIBLE_DEVICES=0"` |
| **Hugging Face CLI** | Installed via `uv tool install --force "huggingface_hub[cli,hf_transfer]"` into `/usr/local/bin/hf` and `/usr/local/bin/huggingface-cli` |
| **GPU & System Monitors** | `nvtop`, `radeontop`, `btop`, `rocm-smi` (if available in APT), `amdgpu_top` (compiled/installed via `cargo` when Rust is present) |
| **Containerized AI Stack (`novos-llm-stack`)** | Orchestrates **ComfyUI** (`yanwk/comfyui-boot:rocm` on port `8188`) and **Open WebUI** (`ghcr.io/open-webui/open-webui:main` on port `3000`) via `/srv/llm/docker-compose.yml` with `/dev/kfd` and `/dev/dri` ROCm passthrough (`HSA_OVERRIDE_GFX_VERSION=12.0.1`, `PYTORCH_ROCM_ARCH=gfx1201`). |

#### Managing ComfyUI & Open WebUI (`novos-llm-stack`)

```bash
# Start ComfyUI (http://localhost:8188) and Open WebUI (http://localhost:3000):
novos-llm-stack up

# Check container status:
novos-llm-stack status

# Follow container logs:
novos-llm-stack logs

# Pull latest container images:
novos-llm-stack pull

# Stop the stack:
novos-llm-stack down
```

#### `/srv/llm` Directory Hierarchy (`@llm` Subvolume)

```text
/srv/llm/                        # Btrfs @llm subvolume (chattr +C No-CoW)
├── docker-compose.yml           # ROCm ComfyUI + Open WebUI compose stack
├── ollama/
│   └── models/                  # Native Ollama GGUF & manifest store ($OLLAMA_MODELS)
├── huggingface/                 # Global Hugging Face cache ($HF_HOME)
├── comfyui/
│   └── storage/                 # Persistent ComfyUI custom nodes, workflows & outputs
├── open-webui/                  # Persistent Open WebUI backend database & RAG storage
└── models/                      # Shared checkpoint/safetensors directory mounted at /srv/models
```

---

### 3.4 `network` — Samba `[DevShare]`, Remote Desktop & Mesh VPN

| Category | Packages & Configuration |
| :--- | :--- |
| **Samba File Server** | Installs `samba`, `samba-common-bin`, `cifs-utils`, `avahi-daemon`, `wsdd`. Configures `[DevShare]` in `/etc/samba/smb.conf` pointing to `/srv/devshare` (`@devshare` subvolume) with `valid users = @sudo`, `force group = sudo`, `create mask = 0664`, `directory mask = 2775`. |
| **Remote Desktop Client** | `remmina`, `remmina-plugin-rdp`, `remmina-plugin-vnc`, `remmina-plugin-secret` |
| **VPN & SSH** | `tailscale` (from official Tailscale repository or Debian `sid`), `wireguard`, `wireguard-tools`, `resolvconf`, `openssh-server`, `net-tools`, `nmap`, `iperf3` |
