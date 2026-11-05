# NovOS Modular Software Profiles, Hardware Opt-In/Out & Custom Tools (`novos-setup`)

[**← Back to README**](../README.md) · [**System Architecture**](ARCHITECTURE.md) · [**Build & QEMU Verification**](BUILD_AND_TEST.md) · [**Branding & Nova Theme**](BRANDING_AND_THEME.md)

---

## 1. Overview

NovOS combines an out-of-the-box hardware-ready Live ISO with a modular profile manager (`/usr/local/bin/novos-setup`, provisioned in [`scripts/stages/05-profiles-installer.sh`](../scripts/stages/05-profiles-installer.sh)) and a declarative Rust custom-tools engine (`/usr/local/bin/novos-custom-tools`).

All 10 top-level categories in [`calamares/modules/netinstall.yaml`](../calamares/modules/netinstall.yaml) map to `novos-setup` profiles:

1. **`wifi` / `no-wifi`** (`selected: true`) — Wireless & WiFi Connectivity Stack (`iwd`, `wpasupplicant`, `wireless-regdb`, `iw`, `rfkill`, and Intel/Realtek/Atheros/MediaTek/Broadcom WiFi firmware)
2. **`bluetooth` / `no-bluetooth`** (`selected: true`) — Bluetooth Audio & Peripherals Stack (`bluez`, `bluez-obexd`, `blueman`, `libspa-0.2-bluetooth`)
3. **`gaming`** (`selected: true`) — Gaming & Emulation Suite (`gamemode`, `gamescope`, `mangohud`, `goverlay`, `lutris`, `wine`, `winetricks`, `retroarch`, `dolphin-emu`, `pcsx2`, `openrgb`, `corectrl`, plus Flathub Steam, Heroic, ProtonPlus, Protontricks, Bottles, RPCS3)
4. **`dev`** (`selected: true`) — Development, Antigravity, Containers & Virtualization (`antigravity`, `antigravity-cli`, `code`, `neovim`, `helix`, `docker-ce`, `podman`, `buildah`, `skopeo`, `distrobox`, `qemu-system-x86`, `libvirt-daemon-system`, `virt-manager`, LLVM/Clang, Rust, Python/uv, Haskell, Node.js, Go, Zig, `gh`, `lazygit`, `alacritty`, `ghostty`, and packaging tools)
5. **`devsecops`** (`selected: false`) — DevSecOps, Pentesting & Kali Rolling Box (`nmap`, `wireshark`, `tshark`, `tcpdump`, `netcat-openbsd`, `john`, `hashcat`, `aircrack-ng`, `hydra`, `sqlmap`, `gobuster`, `radare2`, `gdb`, `ufw`, `firewalld`, plus `/usr/local/bin/novos-sec-box`)
6. **`llm`** (`selected: true`) — Local AI, Hosted LLM & ComfyUI Stack (`/srv/llm` bound to `@llm`, RDNA 4 `RX 9070 XT` / `gfx1201` ROCm/Vulkan accelerated Ollama, Hugging Face CLI, `nvtop`, `radeontop`, `btop`, `rocm-smi`, and containerized ComfyUI + Open WebUI via `novos-llm-stack`)
7. **`network`** (`selected: true`) — Workstation Network, Remote & NAS (`[DevShare]` Samba NAS at `/srv/devshare`, Remmina RDP/VNC, WireGuard, OpenSSH Server, Tailscale, `syncthing`, `rclone`)
8. **`comms`** (`selected: false`, alias `browsers`) — Web Browsers, Email & Team Communications (`firefox-esr`, `chromium`, `thunderbird`, plus Flathub Brave, LibreWolf, Zen Browser, Discord, Vesktop, Signal, Element)
9. **`media`** (`selected: false`) — Media, Pro Audio & 2D/3D Creative Suite (`vlc`, `mpv`, `handbrake`, `obs-studio`, `kdenlive`, `gimp`, `inkscape`, `krita`, `darktable`, `blender`, `freecad`, `audacity`, `ardour`, `pipewire-jack`)
10. **`utilities`** (`selected: false`) — System Administration, Btrfs & Office Productivity (`libreoffice`, `timeshift`, `btrfs-assistant`, `gparted`, `keepassxc`, `fastfetch`, `tmux`, `ripgrep`, `fd-find`, plus Flathub Mission Center, LocalSend, Bitwarden)

---

## 2. WiFi & Bluetooth Opt-In / Opt-Out Lifecycle

### 2.1 Live ISO Default (Enabled Out-of-the-Box)
During Stage 3.7 and Stage 4.1, NovOS pre-installs the complete wireless and Bluetooth stacks into the Live SquashFS image so laptops, wireless keyboards/mice, and Bluetooth headsets work immediately in the Live session.

### 2.2 Calamares Installation Opt-Out (`--post-calamares`)
In the Calamares **Software Profiles** (`netinstall`) step, both **Wireless & WiFi Connectivity Stack** and **Bluetooth Audio & Peripherals Stack** default to `selected: true` and include local offline APT marker packages (`novos-profile-wifi` and `novos-profile-bluetooth` hosted in `/usr/share/novos/apt-markers`):
- **Opt-Out on Wired-Only / Hardened Workstations:** If the user deselects WiFi or Bluetooth in Calamares (so `novos-profile-wifi` or `novos-profile-bluetooth` is not installed, or `/etc/novos/no-wifi` / `/etc/novos/no-bluetooth` is set), `novos-setup --post-calamares` automatically purges the corresponding packages (`iwd`, `wpasupplicant`, wireless firmware, or `bluez`, `bluez-obexd`, `blueman`) and masks the systemd units (`iwd.service`, `wpa_supplicant.service`, `bluetooth.service`).

### 2.3 Anytime Post-Install Toggling via `novos-setup`

```bash
# Purge and mask WiFi and/or Bluetooth on a wired workstation:
sudo novos-setup --no-wifi --no-bluetooth
# Or via profile names:
sudo novos-setup --install no-wifi,no-bluetooth

# Re-install and unmask WiFi and/or Bluetooth later:
sudo novos-setup --wifi --bluetooth
# Or via profile names:
sudo novos-setup --install wifi,bluetooth
```

---

## 3. Installing Software Profiles

```bash
# Launch interactive whiptail checklist (all 10 profiles):
sudo novos-setup

# Install specific profiles non-interactively for a target user:
sudo novos-setup --install gaming,dev,devsecops,llm,network,comms,media,utilities --user devuser

# Install granular APT packages safely:
sudo novos-setup --packages fastfetch,tmux,ripgrep,btrfs-assistant --user devuser

# Build & install enabled custom tools from /etc/novos/custom-tools.yaml:
sudo novos-setup --custom-tools
```

---

## 4. Expanded Application Suite Specifications

### 4.1 `gaming` — Gaming & Emulation Suite (XanMod + RDNA 4 Optimized)

| Category | Packages & Components |
| :--- | :--- |
| **Core Runtime & Overlays** | `gamemode`, `gamescope`, `mangohud`, `goverlay`, `vulkan-tools` |
| **Lutris & Wine** | `lutris`, `wine`, `wine64`, `wine32:i386`, `libwine:i386`, `winetricks` |
| **Console & Handheld Emulators** | `retroarch`, `dolphin-emu`, `pcsx2` |
| **Hardware & GPU Control** | `openrgb`, `corectrl` |
| **Flathub Gaming Launchers (`@flatpak`)** | `com.valvesoftware.Steam`, `com.heroicgameslauncher.hgl`, `com.vysp3r.ProtonPlus`, `com.github.Matoking.protontricks`, `com.usebottles.bottles`, `net.rpcs3.RPCS3` |

---

### 4.2 `dev` — Development, Antigravity, Containers & Virtualization

| Category | Packages & Components |
| :--- | :--- |
| **Google Antigravity** | `antigravity` & `antigravity-cli` (`agy` CLI) |
| **Code Editors** | `code` (VS Code), `neovim`, `helix` |
| **Docker, Podman & Distrobox** | `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, `docker-compose-plugin`, `podman`, `buildah`, `skopeo`, `distrobox` |
| **KVM / QEMU Virtualization** | `qemu-system-x86`, `libvirt-daemon-system`, `virt-manager` |
| **Languages & Compilers** | `clang`, `lld`, `llvm`, `cmake`, `ninja-build`, `python3-full`, `ghc`, `cabal-install`, `haskell-stack`, `nodejs`, `npm`, `golang-go`, `zig`, plus Rust (`rustup`) and Astral `uv` |
| **Git & GPU Terminals** | `gh`, `lazygit`, `alacritty`, `ghostty` |
| **Packaging Tools** | `alien`, `rpm2cpio`, `cpio`, `patchelf`, `squashfs-tools`, `desktop-file-utils`, `zsync`, `appimage-builder` |

---

### 4.3 `devsecops` & `novos-sec-box` — Security Auditing & Kali Rolling Container

| Category | Packages & Components |
| :--- | :--- |
| **Network Discovery & Packet Analysis** | `nmap`, `wireshark`, `tshark`, `tcpdump`, `netcat-openbsd` |
| **Password & Wireless Auditing** | `john`, `hashcat`, `aircrack-ng`, `hydra` |
| **Web Assessment & Reverse Engineering** | `sqlmap`, `gobuster`, `radare2`, `gdb` |
| **Host Firewalls** | `ufw`, `firewalld` |
| **Kali Rolling Container (`novos-sec-box`)** | Rootless `podman` + `distrobox` wrapper managing `docker.io/kalilinux/kali-rolling:latest` |

#### Using `novos-sec-box`

```bash
# Create and initialize the kalilinux/kali-rolling Distrobox container:
novos-sec-box up

# Enter an interactive Kali Rolling shell:
novos-sec-box enter

# Provision the Kali Top 10 security toolkit inside the container:
novos-sec-box install-top10

# Export a Kali tool binary directly to ~/.local/bin on the NovOS host:
novos-sec-box export msfconsole

# Check container status or stop the container:
novos-sec-box status
novos-sec-box stop
```

---

### 4.4 `llm` — Local AI, Hosted LLM & ComfyUI Stack (`/srv/llm`)

| Component | Configuration & Paths |
| :--- | :--- |
| **Global Environment (`/etc/profile.d/novos-env.sh`)** | `HF_HOME="/srv/llm/huggingface"` and `OLLAMA_MODELS="/srv/llm/ollama/models"` |
| **GPU Monitors & Compute Headers** | `nvtop`, `radeontop`, `btop`, `rocm-smi`, `libcurl4-openssl-dev`, `libvulkan-dev`, `glslang-tools`, `spirv-tools`, `amdgpu_top` |
| **Containerized AI Stack (`novos-llm-stack`)** | Orchestrates **ComfyUI** (`http://localhost:8188`) and **Open WebUI** (`http://localhost:3000`) via `/srv/llm/docker-compose.yml` with RDNA 4 (`gfx1201`) ROCm device passthrough. |

---

### 4.5 `network`, `comms`, `media` & `utilities`

| Profile | Included Packages & Flathub Apps |
| :--- | :--- |
| **`network`** | `samba`, `smbclient`, `cifs-utils` (`[DevShare]` at `/srv/devshare`), `remmina`, `remmina-plugin-rdp`, `remmina-plugin-vnc`, `wireguard-tools`, `openssh-server`, `tailscale`, `syncthing`, `rclone` |
| **`comms`** (`browsers`) | APT: `firefox-esr`, `chromium`, `thunderbird`<br/>Flathub: `Brave`, `LibreWolf`, `Zen Browser`, `Discord`, `Vesktop`, `Signal`, `Element` |
| **`media`** | `vlc`, `mpv`, `handbrake`, `obs-studio`, `kdenlive`, `gimp`, `inkscape`, `krita`, `darktable`, `blender`, `freecad`, `audacity`, `ardour`, `pipewire-jack` |
| **`utilities`** | APT: `libreoffice`, `fastfetch`, `gparted`, `keepassxc`, `tmux`, `ripgrep`, `fd-find`, `timeshift`, `btrfs-assistant`<br/>Flathub: `Mission Center`, `LocalSend`, `Bitwarden` |

---

## 5. Declarative Custom Tools (`novos-custom-tools`)

### 5.1 `/usr/local/bin/novos-custom-tools` (`custom-tools/manifest.yaml`)
NovOS includes a native Rust declarative builder (`/usr/local/bin/novos-custom-tools`, symlinked at `/usr/local/bin/novos-ct`) backed by `/etc/novos/custom-tools.yaml` (copied from [`custom-tools/manifest.yaml`](../custom-tools/manifest.yaml)). It supports three tool types (`local`, `github_release`, `github_source`):

```bash
# Validate manifest schema and required per-type fields:
novos-custom-tools --validate /etc/novos/custom-tools.yaml

# List all configured tools and their enabled status:
novos-custom-tools --manifest /etc/novos/custom-tools.yaml --list

# Build and install all enabled tools (executed automatically in Stage 5.4):
sudo novos-custom-tools --manifest /etc/novos/custom-tools.yaml --install-enabled

# Install or rebuild a single tool by name:
sudo novos-custom-tools --manifest /etc/novos/custom-tools.yaml --tool novos-custom-tools
```

### 5.2 Standalone `slippery-tiles` Repository (`/srv/devshare/slippery-tiles`)
> [!NOTE]
> The `slippery-tiles` adaptive grid window tiling engine has been branched off into its own standalone project repository (`/srv/devshare/slippery-tiles`) and removed from the active NovOS distro tree. Once its standalone implementation is finalized, it can be re-integrated declaratively via [`custom-tools/manifest.yaml`](../custom-tools/manifest.yaml) (`github_release` or `github_source`).
