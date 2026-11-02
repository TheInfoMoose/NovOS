#!/usr/bin/env bash
# ==============================================================================
# Stage 4 (Inside Chroot): COSMIC Desktop Environment + Native Graphical
# cosmic-greeter (with Debian PAM, D-Bus, systemd-logind & tuigreet Fallback)
# ==============================================================================
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export DEBIAN_FRONTEND=noninteractive
export RUNLEVEL=3

source /root/novos-build/config/build.env
source /root/novos-build/scripts/lib/verify-deps.sh

echo "==> [Stage 4.1] Installing COSMIC Desktop, greetd, tuigreet, PipeWire & Desktop Portals..."
mkdir -p /run/dbus /usr/share/novos
if [ -x /usr/bin/dbus-daemon ] && [ ! -e /run/dbus/system_bus_socket ]; then
    rm -f /run/dbus/pid 2>/dev/null || true
    /usr/bin/dbus-daemon --system --fork || true
fi
if [ -f /etc/apt/apt.conf.d/80snapper ]; then
    mv -f /etc/apt/apt.conf.d/80snapper /usr/share/novos/80snapper.disabled
    sed -i -E 's/snapper --no-dbus/snapper/g; s/\bsnapper (create|cleanup)\b/snapper --no-dbus \1/g' /usr/share/novos/80snapper.disabled
fi
rm -f /etc/apt/apt.conf.d/80snapper

apt-get update
apt-get install -y --no-remove \
    cosmic-epoch-monorepo cosmic-utils-monorepo libdisplay-info3 \
    greetd tuigreet xwayland ffmpeg libsm6 libxext6 rtkit upower geoclue-2.0 \
    xdg-desktop-portal xdg-user-dirs dbus-x11 libpam-systemd libpam-gnome-keyring polkitd \
    gnome-keyring gvfs gvfs-backends gvfs-fuse flatpak packagekit appstream \
    pipewire-audio wireplumber libspa-0.2-bluetooth \
    fonts-noto-core fonts-noto-color-emoji fonts-dejavu

if [ -f /etc/apt/apt.conf.d/80snapper ]; then
    mv -f /etc/apt/apt.conf.d/80snapper /usr/share/novos/80snapper.disabled
    sed -i -E 's/snapper --no-dbus/snapper/g; s/\bsnapper (create|cleanup)\b/snapper --no-dbus \1/g' /usr/share/novos/80snapper.disabled
fi
rm -f /etc/apt/apt.conf.d/80snapper

for theme_dir in /usr/share/cosmic/com.system76.CosmicTheme.Dark /usr/share/cosmic/com.system76.CosmicTheme.Light; do
    if [[ -d "${theme_dir}/v1" ]]; then
        mkdir -p "${theme_dir}/v2"
        for key_file in "${theme_dir}/v1/"*; do
            [[ -f "$key_file" ]] || continue
            base_key="$(basename "$key_file")"
            if [[ ! -f "${theme_dir}/v2/${base_key}" ]]; then
                cp -a "$key_file" "${theme_dir}/v2/${base_key}"
            fi
        done
    fi
done

# CRITICAL FIX: Ensure seatd is NEVER running so libseat1/cosmic-comp uses systemd-logind exclusively
systemctl disable --now seatd.service 2>/dev/null || true
systemctl mask seatd.service 2>/dev/null || true

echo "==> [Stage 4.2] Wiring COSMIC Wayland Session Wrapper (\$XDG_RUNTIME_DIR & User D-Bus Guarantee)..."
cat << 'EOF' > /usr/local/bin/cosmic-session-launch
#!/usr/bin/env bash
export XDG_SESSION_TYPE="wayland"
export XDG_CURRENT_DESKTOP="COSMIC"
export XDG_SESSION_DESKTOP="cosmic"
export MOZ_ENABLE_WAYLAND=1
export QT_QPA_PLATFORM="wayland;xcb"
export ELECTRON_OZONE_PLATFORM_HINT="auto"
export COSMIC_DISABLE_DIRECT_SCANOUT=1
export WLR_NO_HARDWARE_CURSORS=1
export COSMIC_DATA_CONTROL_ENABLED=1

udevadm settle --timeout=5 2>/dev/null || true
for _ in 1 2 3 4 5; do
    ls /dev/dri/card* >/dev/null 2>&1 && break
    sleep 1
done

# Fallback safety if launched manually from a raw shell without PAM systemd hook
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [ ! -d "${XDG_RUNTIME_DIR}" ]; then
    mkdir -p -m 0700 "${XDG_RUNTIME_DIR}" 2>/dev/null || true
else
    chmod 0700 "${XDG_RUNTIME_DIR}" 2>/dev/null || true
fi

if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "${XDG_RUNTIME_DIR}/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi

if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && command -v dbus-run-session >/dev/null 2>&1; then
    start_ts=$SECONDS
    rc=0
    dbus-run-session -- /usr/bin/start-cosmic || rc=$?
    elapsed=$((SECONDS - start_ts))
    if [[ $rc -ne 0 && $elapsed -lt 10 ]]; then
        export LIBGL_ALWAYS_SOFTWARE=1
        export ICED_BACKEND=tiny-skia
        export WGPU_BACKEND=gl
        exec dbus-run-session -- /usr/bin/start-cosmic
    fi
    exit "$rc"
fi

start_ts=$SECONDS
rc=0
/usr/bin/start-cosmic || rc=$?
elapsed=$((SECONDS - start_ts))
if [[ $rc -ne 0 && $elapsed -lt 10 ]]; then
    export LIBGL_ALWAYS_SOFTWARE=1
    export ICED_BACKEND=tiny-skia
    export WGPU_BACKEND=gl
    exec /usr/bin/start-cosmic
fi
exit "$rc"
EOF
chmod +x /usr/local/bin/cosmic-session-launch

mkdir -p /usr/share/wayland-sessions
cat << 'EOF' > /usr/share/wayland-sessions/cosmic.desktop
[Desktop Entry]
Name=COSMIC
Comment=COSMIC Desktop Environment
Exec=/usr/local/bin/cosmic-session-launch
TryExec=/usr/bin/start-cosmic
Type=Application
DesktopNames=COSMIC
EOF

echo "==> [Stage 4.3] Configuring Debian PAM, D-Bus Policy & Systemd Unit for Graphical cosmic-greeter..."
# Ensure both _greetd (Debian default) and cosmic-greeter user/group exist and have GPU/input access
groupadd -f cosmic-greeter
if ! id -u cosmic-greeter >/dev/null 2>&1; then
    useradd -r -g cosmic-greeter -G video,render,input,audio -d /var/lib/cosmic-greeter -s /usr/sbin/nologin cosmic-greeter
fi
usermod -d /var/lib/cosmic-greeter cosmic-greeter 2>/dev/null || true
for grp in video render input audio _greetd; do
    if getent group "$grp" >/dev/null 2>&1; then
        usermod -aG "$grp" cosmic-greeter 2>/dev/null || true
    fi
done
mkdir -p /var/lib/cosmic-greeter /var/lib/greetd /var/cache/tuigreet
if id -u _greetd >/dev/null 2>&1; then
    usermod -aG video,render,input,audio,cosmic-greeter _greetd 2>/dev/null || true
    chown -R _greetd:_greetd /var/lib/greetd
fi
chown -R cosmic-greeter:cosmic-greeter /var/lib/cosmic-greeter /var/cache/tuigreet
chmod 0755 /var/lib/cosmic-greeter /var/cache/tuigreet

# 1. Missing Debian PAM profiles for cosmic-greeter and greetd
cat << 'EOF' > /etc/pam.d/cosmic-greeter
#%PAM-1.0
auth       substack     common-auth
-auth      optional     pam_gnome_keyring.so
account    include      common-account
password   substack     common-password
session    required     pam_loginuid.so
session    include      common-session
-session   optional     pam_gnome_keyring.so auto_start
EOF

cat << 'EOF' > /etc/pam.d/greetd
#%PAM-1.0
auth       substack     common-auth
-auth      optional     pam_gnome_keyring.so
account    include      common-account
password   substack     common-password
session    required     pam_loginuid.so
session    include      common-session
-session   optional     pam_gnome_keyring.so auto_start
EOF

# 2. Missing D-Bus System Bus Policy for com.system76.CosmicGreeter
mkdir -p /usr/share/dbus-1/system.d
cat << 'EOF' > /usr/share/dbus-1/system.d/com.system76.CosmicGreeter.conf
<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="root">
    <allow own="com.system76.CosmicGreeter"/>
    <allow send_destination="com.system76.CosmicGreeter"/>
    <allow receive_sender="com.system76.CosmicGreeter"/>
  </policy>
  <policy user="cosmic-greeter">
    <allow send_destination="com.system76.CosmicGreeter"/>
    <allow receive_sender="com.system76.CosmicGreeter"/>
  </policy>
  <policy user="_greetd">
    <allow send_destination="com.system76.CosmicGreeter"/>
    <allow receive_sender="com.system76.CosmicGreeter"/>
  </policy>
  <policy context="default">
    <allow send_destination="com.system76.CosmicGreeter"/>
  </policy>
</busconfig>
EOF

# 3. Missing systemd service for cosmic-greeter-daemon
if [ -x /usr/bin/cosmic-greeter-daemon ]; then
    cat << 'EOF' > /etc/systemd/system/cosmic-greeter-daemon.service
[Unit]
Description=COSMIC Greeter Daemon
After=dbus.service systemd-logind.service

[Service]
Type=dbus
BusName=com.system76.CosmicGreeter
ExecStart=/usr/bin/cosmic-greeter-daemon
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    systemctl enable cosmic-greeter-daemon.service
fi

# 4. Resilient Greeter App Wrapper & Launcher: Native Graphical cosmic-greeter -> Automatic Fallback to tuigreet
cat << 'EOF' > /usr/local/bin/cosmic-greeter-app
#!/usr/bin/env bash
export COSMIC_DATA_CONTROL_ENABLED=1
BG_PID=""
if [ -x /usr/bin/cosmic-bg ]; then
    /usr/bin/cosmic-bg &
    BG_PID=$!
fi
RC=0
/usr/bin/cosmic-greeter "$@" || RC=$?
if [ -n "$BG_PID" ]; then
    kill "$BG_PID" 2>/dev/null || true
fi
if [ "$RC" -eq 0 ]; then
    if [ -n "${PPID:-}" ] && [ "$PPID" -gt 1 ] && kill -0 "$PPID" 2>/dev/null; then
        kill -TERM "$PPID" 2>/dev/null || true
    fi
    exit 0
else
    exit "$RC"
fi
EOF
chmod 0755 /usr/local/bin/cosmic-greeter-app

cat << 'EOF' > /usr/local/bin/cosmic-greeter-launch
#!/usr/bin/env bash
# Attempts graphical cosmic-greeter on KMS/DRM hardware; automatically falls back to tuigreet if GPU init fails
export XDG_SESSION_TYPE="wayland"
export XDG_CURRENT_DESKTOP="COSMIC"
export XDG_SESSION_CLASS="greeter"
export RUST_LOG="warn"
export COSMIC_DISABLE_DIRECT_SCANOUT=1
export WLR_NO_HARDWARE_CURSORS=1
export COSMIC_DATA_CONTROL_ENABLED=1

udevadm settle --timeout=5 2>/dev/null || true
for _ in 1 2 3 4 5; do
    ls /dev/dri/card* >/dev/null 2>&1 && break
    sleep 1
done

if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [ ! -d "${XDG_RUNTIME_DIR}" ]; then
    mkdir -p -m 0700 "${XDG_RUNTIME_DIR}" 2>/dev/null || true
else
    chmod 0700 "${XDG_RUNTIME_DIR}" 2>/dev/null || true
fi

if ls /dev/dri/card* >/dev/null 2>&1 && [ -x /usr/bin/cosmic-comp ] && [ -x /usr/local/bin/cosmic-greeter-app ]; then
    if dbus-run-session -- /usr/bin/cosmic-comp /usr/local/bin/cosmic-greeter-app; then
        exit 0
    fi
    echo "[cosmic-greeter-launch] Hardware EGL cosmic-greeter exited non-zero; retrying with LIBGL_ALWAYS_SOFTWARE=1..." >&2
    if LIBGL_ALWAYS_SOFTWARE=1 ICED_BACKEND=tiny-skia WGPU_BACKEND=gl dbus-run-session -- /usr/bin/cosmic-comp /usr/local/bin/cosmic-greeter-app; then
        exit 0
    fi
    echo "[cosmic-greeter-launch] Graphical cosmic-greeter exited non-zero; falling back to tuigreet..." >&2
fi

exec /usr/bin/tuigreet \
    --time \
    --remember \
    --remember-user-session \
    --sessions /usr/share/wayland-sessions \
    --cmd /usr/local/bin/cosmic-session-launch
EOF
chmod +x /usr/local/bin/cosmic-greeter-launch

mkdir -p /etc/greetd
cat << 'EOF' > /etc/greetd/config.toml
[terminal]
vt = 7

[default_session]
command = "/usr/local/bin/cosmic-greeter-launch"
user = "cosmic-greeter"
EOF

mkdir -p /etc/systemd/system/greetd.service.d
cat << 'EOF' > /etc/systemd/system/greetd.service.d/override.conf
[Unit]
After=systemd-user-sessions.service plymouth-quit-wait.service systemd-logind.service dbus.service getty@tty1.service
Wants=systemd-logind.service dbus.service

[Service]
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardOutput=journal
StandardError=journal
EOF

echo "==> [Stage 4.4] Registering Flathub & Enabling Graphical Target Services..."
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true

systemctl enable greetd.service NetworkManager.service
systemctl set-default graphical.target

echo "==> [Stage 4.5] Provisioning NovOS Branding Assets, Nova Wallpaper & COSMIC Nova Orange Theme..."
mkdir -p /usr/share/backgrounds/novos /usr/share/pixmaps /usr/share/icons/hicolor/scalable/apps
cp -f /root/novos-build/branding/nova-wallpaper.png /usr/share/backgrounds/novos/nova-wallpaper.png
cp -f /root/novos-build/branding/nova-wallpaper.svg /usr/share/backgrounds/novos/nova-wallpaper.svg
cp -f /root/novos-build/branding/novos-logo.svg /usr/share/pixmaps/novos-logo.svg
cp -f /root/novos-build/branding/novos-logo.png /usr/share/pixmaps/novos-logo.png
cp -f /root/novos-build/branding/novos-logo.svg /usr/share/icons/hicolor/scalable/apps/novos-logo.svg

mkdir -p /usr/share/cosmic/com.system76.CosmicBackground/v1
cat << 'RON' > /usr/share/cosmic/com.system76.CosmicBackground/v1/all
(
    output: "all",
    source: Path("/usr/share/backgrounds/novos/nova-wallpaper.png"),
    filter_by_theme: true,
    rotation_frequency: 300,
    filter_method: Lanczos,
    scaling_mode: Zoom,
    sampling_method: Alphanumeric,
)
RON
printf 'true\n' > /usr/share/cosmic/com.system76.CosmicBackground/v1/same-on-all

provision_cosmic_theme() {
    local cfg_base="$1"
    mkdir -p \
        "${cfg_base}/com.system76.CosmicBackground/v1" \
        "${cfg_base}/com.system76.CosmicBg/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Mode/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Dark.Builder/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Dark.Builder/v2" \
        "${cfg_base}/com.system76.CosmicTheme.Dark/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Dark/v2"

    if [[ -d /usr/share/cosmic/com.system76.CosmicTheme.Dark ]]; then
        cp -a /usr/share/cosmic/com.system76.CosmicTheme.Dark/v1/* "${cfg_base}/com.system76.CosmicTheme.Dark/v1/" 2>/dev/null || true
        cp -a /usr/share/cosmic/com.system76.CosmicTheme.Dark/v2/* "${cfg_base}/com.system76.CosmicTheme.Dark/v2/" 2>/dev/null || true
    fi

    printf 'true\n' > "${cfg_base}/com.system76.CosmicTheme.Mode/v1/is_dark"
    printf '(red: 1.0, green: 0.41960785, blue: 0.101960786)\n' > "${cfg_base}/com.system76.CosmicTheme.Dark.Builder/v1/accent"
    printf '(red: 1.0, green: 0.41960785, blue: 0.101960786)\n' > "${cfg_base}/com.system76.CosmicTheme.Dark.Builder/v2/accent"
    cat << 'RON' > "${cfg_base}/com.system76.CosmicBackground/v1/all"
(
    output: "all",
    source: Path("/usr/share/backgrounds/novos/nova-wallpaper.png"),
    filter_by_theme: true,
    rotation_frequency: 300,
    filter_method: Lanczos,
    scaling_mode: Zoom,
    sampling_method: Alphanumeric,
)
RON
    printf 'true\n' > "${cfg_base}/com.system76.CosmicBackground/v1/same-on-all"
    cat << 'RON' > "${cfg_base}/com.system76.CosmicBg/v1/all"
(
    output: "all",
    source: Path("/usr/share/backgrounds/novos/nova-wallpaper.png"),
    filter_by_theme: true,
    rotation_frequency: 300,
    filter_method: Lanczos,
    scaling_mode: Zoom,
    sampling_method: Alphanumeric,
)
RON
}

provision_cosmic_theme "/etc/skel/.config/cosmic"
provision_cosmic_theme "/home/${DEFAULT_USER}/.config/cosmic"
provision_cosmic_theme "/var/lib/cosmic-greeter/.config/cosmic"
provision_cosmic_theme "/var/lib/greetd/.config/cosmic"

chown -R "${DEFAULT_USER}:${DEFAULT_USER}" "/home/${DEFAULT_USER}/.config"
chown -R cosmic-greeter:cosmic-greeter /var/lib/cosmic-greeter /var/cache/tuigreet
chmod 0755 /var/lib/cosmic-greeter /var/cache/tuigreet
if id -u _greetd >/dev/null 2>&1; then
    chown -R _greetd:_greetd /var/lib/greetd
fi

verify_stage4_post
echo "==> [Stage 4] COSMIC Desktop & cosmic-greeter (+ tuigreet fallback) setup complete."
