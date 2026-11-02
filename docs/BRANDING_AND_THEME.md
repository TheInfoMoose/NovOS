# NovOS Branding & Nova Dark Design System

[**← Back to README**](../README.md) · [**System Architecture**](ARCHITECTURE.md) · [**Build & QEMU Verification**](BUILD_AND_TEST.md) · [**Modular Profiles**](PROFILES.md)

---

## 1. Visual Identity ("Nova" 2026.10)

NovOS 2026.10 ("Nova") uses a cohesive **Deep Obsidian Space & Supernova Accretion** visual theme across every layer of the operating system—from the Limine bootloader and COSMIC desktop down to the Calamares installer and terminal ANSI colors.

<div align="center">

| Supernova Emblem (`branding/novos-logo.svg`) | Cosmic Space & Horizon Nebula (`branding/nova-wallpaper.svg`) |
| :---: | :---: |
| <img src="../branding/novos-logo.svg" alt="NovOS Logo" width="200" /> | <img src="../branding/nova-wallpaper.svg" alt="NovOS Nova Wallpaper" width="440" /> |

</div>

### Emblem Geometry
The **NovOS Supernova Emblem** consists of four geometric layers:
1. **Deep Obsidian Circular Badge:** Radial gradient (`#121521` inner → `#0B0C10` mid → `#07080B` outer rim) framed by a `#FF6B1A` outer glow ring and `#1E2233` inner bezel.
2. **Tilted Accretion Rings (`-28°`):** Dual elliptical accretion rings sweeping from **Nova Orange (`#FF6B1A`)** through **Solar Flare (`#FF9E44`)** to **Warm Amber (`#FFB347`)**, paired with a dashed inner orbital ring.
3. **Supernova Core Halo & Diagonal Rays:** Radial core glow beneath `45°` secondary star rays (`#FF9E44`).
4. **Radiant 4-Point Star Flare Nucleus:** High-luminance **Starlight White (`#FFF3E0`)** 4-point flare and central core.

---

## 2. Nova Dark Color Palette Tokens

| Token Name | Hex Code | RGB / Normalized Float | Usage Across NovOS |
| :--- | :--- | :--- | :--- |
| **Obsidian Void** | `#0B0C10` | `rgb(11, 12, 16)` | Primary backdrop (Wallpaper top-left, Limine canvas, Calamares sidebar) |
| **Cosmic Surface** | `#0F121C` | `rgb(15, 18, 28)` | Calamares main window canvas (`#mainApp`), scrollbar troughs |
| **Nebula Indigo** | `#121521` | `rgb(18, 21, 33)` | Wallpaper bottom-right gradient, badge interior |
| **Elevated Control** | `#161B29` | `rgb(22, 27, 41)` | Calamares inputs, dropdowns, tree views, progress bars, sidebar highlight |
| **Interactive Surface** | `#1C2233` | `rgb(28, 34, 51)` | Action buttons (`QPushButton`), combo-box drop-down arrows |
| **Muted Border** | `#2D354D` | `rgb(45, 53, 77)` | Widget borders, group-box frames, table headers |
| **Nova Orange (Primary)** | `#FF6B1A` | `rgb(255, 107, 26)` / `(1.0, 0.4196, 0.1020)` | Primary brand accent, COSMIC Dark accent, `/etc/os-release` `ANSI_COLOR`, active focus/selection |
| **Solar Flare** | `#FF9E44` | `rgb(255, 158, 68)` | Accretion ring midpoint, button hover border, Limine palette |
| **Warm Amber** | `#FFB347` | `rgb(255, 179, 71)` | Horizon nebula arc peak, inner accretion ring |
| **Starlight Core** | `#FFF3E0` | `rgb(255, 243, 224)` | Supernova 4-point star flare, bright starfield nodes |
| **Crisp Foreground** | `#F4F6FB` | `rgb(244, 246, 251)` | Primary UI typography (`>15:1` contrast ratio against `#0B0C10` / `#0F121C`) |
| **Warm Secondary Text** | `#EBDBB2` | `rgb(235, 219, 178)` | Calamares sidebar inactive text, Limine terminal foreground |

---

## 3. Deterministic Zero-Dependency Asset Generator

All branding files in [`branding/`](../branding/) are synthesized deterministically by [`scripts/generate-branding-assets.py`](../scripts/generate-branding-assets.py) using only Python's standard library (`math`, `struct`, `zlib`, `pathlib`)—requiring zero external packages (`Pillow`, `cairosvg`, or `imagemagick` are not needed).

```bash
# Regenerate all SVG and RGBA PNG branding assets deterministically:
python3 scripts/generate-branding-assets.py
```

| Generated File | Resolution / Format | Description |
| :--- | :--- | :--- |
| [`branding/novos-logo.svg`](../branding/novos-logo.svg) | `256×256` SVG | Vector Supernova emblem for scalable desktop icons and documentation |
| [`branding/novos-logo.png`](../branding/novos-logo.png) | `256×256` 8-bit RGBA PNG | Anti-aliased raster Supernova emblem for Calamares and pixmaps |
| [`branding/nova-wallpaper.svg`](../branding/nova-wallpaper.svg) | `1920×1080` SVG | Vector cosmic space backdrop, 140-star deterministic starfield (`seed=0x4E4F5641` `"NOVA"`), horizon nebula arc, and center-right emblem |
| [`branding/nova-wallpaper.png`](../branding/nova-wallpaper.png) | `1920×1080` 8-bit RGBA PNG | Scanline-synthesized Full HD wallpaper used by Limine, `cosmic-bg`, and Calamares slideshow |

---

## 4. OS-Wide Theme Integration

### 4.1 OS Identity & Terminal ANSI Accent (`/usr/lib/os-release`)
Configured in [`scripts/stages/03-core-system.sh`](../scripts/stages/03-core-system.sh):
* `PRETTY_NAME="NovOS 2026.10 (Nova)"`
* `ANSI_COLOR="38;2;255;107;26"` (TrueColor `#FF6B1A` Nova Orange in `fastfetch`, `neofetch`, and `systemd` boot banners)

### 4.2 Limine Bootloader Canvas Theme (`limine.conf`)
Configured in [`scripts/stages/03-core-system.sh`](../scripts/stages/03-core-system.sh) and [`scripts/stages/06-snapshot-finalize.sh`](../scripts/stages/06-snapshot-finalize.sh):
```ini
timeout: 5
default_entry: 1
wallpaper: boot():/limine/nova-wallpaper.png
term_backend: canvas
term_palette: 0b0c10;ff6b1a;ff9e44;ffb347;5294e2;d3869b;8ec07c;ebdbb2
```

### 4.3 COSMIC Desktop & `cosmic-greeter` Theme Provisioning
Configured in [`scripts/stages/04-cosmic-desktop.sh`](../scripts/stages/04-cosmic-desktop.sh) (`provision_cosmic_theme`):
* Installed across `/etc/skel/.config/cosmic`, `/home/devuser/.config/cosmic`, `/var/lib/cosmic-greeter/.config/cosmic`, and `/var/lib/greetd/.config/cosmic`:
  * **Dark Mode Enforced:** `com.system76.CosmicTheme.Mode/v1/is_dark` → `true`
  * **Nova Orange Accent (`#FF6B1A`):** `com.system76.CosmicTheme.Dark.Builder/v1/accent` → `(red: 1.0, green: 0.41960785, blue: 0.101960786)`
  * **Nova Wallpaper Binding:** Provisioned in both `com.system76.CosmicBackground/v1/all` and `com.system76.CosmicBg/v1/all` pointing to `/usr/share/backgrounds/novos/nova-wallpaper.png` (`Lanczos` scaling, `Zoom` mode).
* **System Icons & Pixmaps:** Installed to `/usr/share/pixmaps/novos-logo.{svg,png}` and `/usr/share/icons/hicolor/scalable/apps/novos-logo.svg`.

### 4.4 Calamares Installer High-Contrast Nova Dark Theme
Configured in [`calamares/branding/novos/branding.desc`](../calamares/branding/novos/branding.desc) and [`calamares/branding/novos/stylesheet.qss`](../calamares/branding/novos/stylesheet.qss):
* **Sidebar Palette (`branding.desc`):** `#0B0C10` background, `#F4F6FB` text, `#FF6B1A` active step accent, and `#161B29` current-step highlight (supports both Calamares 3.2 and 3.3+ style keys).
* **Qt Stylesheet (`stylesheet.qss`):** Overrides default light Qt/Fusion widgets when Calamares runs under `sudo -E calamares`, painting `#0F121C` window surfaces, `#F4F6FB` high-contrast labels, `#161B29` input controls, and `#FF6B1A` Nova Orange buttons, focus rings, checkboxes, and progress bars.
* **Installation Slideshow (`show.qml`):** Displays `nova-wallpaper.png` with a translucent `#0B0C10` (`opacity: 0.82`) rounded card overlay bordered in `#FF6B1A` during filesystem unpacking and bootloader installation.
