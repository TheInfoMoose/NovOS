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

if [ ! -c /dev/null ]; then
    rm -f /dev/null && mknod -m 666 /dev/null c 1 3
fi

apt-get update
apt-get -o APT::Get::AutomaticRemove=false -o APT::AutoRemove::SuggestsImportant=false install -y \
    dictionaries-common aspell aspell-en || true

# Guard /usr/sbin/aspell-autobuildhash against Debian Sid's undefined &main::subst bug if /dev/null is ever
# non-empty or if aspell emits warnings during chroot/Calamares triggers
if [ -f /usr/sbin/aspell-autobuildhash ] && ! grep -q 'sub subst' /usr/sbin/aspell-autobuildhash; then
    perl -0777 -i -pe '
        my $guard = "sub subst { my (\$s) = \@_; return defined(\$s) ? \$s : \"\"; }\nif ( ! -c \"/dev/null\" ) { unlink(\"/dev/null\"); system(\"mknod\", \"-m\", \"666\", \"/dev/null\", \"c\", \"1\", \"3\"); }\n";
        if (!s/\A(#![^\n]*\n)/$1$guard/) {
            $_ = $guard . $_;
        }
    ' /usr/sbin/aspell-autobuildhash
    chmod 0755 /usr/sbin/aspell-autobuildhash
fi
dpkg --configure -a
apt-get -o APT::Get::AutomaticRemove=false -o APT::AutoRemove::SuggestsImportant=false install -y \
    dictionaries-common aspell aspell-en

apt-get -o APT::Get::AutomaticRemove=false -o APT::AutoRemove::SuggestsImportant=false install -y \
    cosmic-epoch-monorepo cosmic-utils-monorepo libdisplay-info3 \
    greetd tuigreet xwayland ffmpeg libsm6 libxext6 rtkit upower geoclue-2.0 \
    xdg-desktop-portal xdg-user-dirs dbus-x11 libpam-systemd libpam-gnome-keyring polkitd \
    gnome-keyring gvfs gvfs-backends gvfs-fuse flatpak packagekit appstream \
    pipewire-audio wireplumber libspa-0.2-bluetooth bluez-obexd blueman \
    fonts-noto-core fonts-noto-color-emoji fonts-dejavu \
    python3-tk python3-yaml wmctrl

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

echo "==> [Stage 4.2] Wiring COSMIC Wayland Session Wrapper (\$XDG_RUNTIME_DIR, Auto-Aspect Wallpaper & User D-Bus Guarantee)..."
cat << 'EOF' > /usr/local/bin/novos-wallpaper-auto
#!/usr/bin/env bash
set -euo pipefail

WATCH_MODE="false"
if [[ "${1:-}" == "--watch" ]]; then
    WATCH_MODE="true"
    shift
fi

USER_HOME="${HOME:-$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6 || echo "/home/devuser")}"
CFG_BASE="${1:-${XDG_CONFIG_HOME:-${USER_HOME}/.config}/cosmic}"
BG_DIR_1="${CFG_BASE}/com.system76.CosmicBackground/v1"
BG_DIR_2="${CFG_BASE}/com.system76.CosmicBg/v1"
WP_16X9="/usr/share/backgrounds/novos/nova-realistic-16x9.png"
WP_32X9="/usr/share/backgrounds/novos/nova-realistic-32x9.png"
WP_FALLBACK="/usr/share/backgrounds/novos/nova-wallpaper.png"

is_user_customized() {
    local bg_file same_file current_src
    for same_file in "${BG_DIR_1}/same-on-all" "${BG_DIR_2}/same-on-all"; do
        if [[ -f "$same_file" ]] && [[ "$(tr -d '[:space:]' < "$same_file" 2>/dev/null || true)" == "false" ]]; then
            return 0
        fi
    done
    for bg_file in "${BG_DIR_1}/all" "${BG_DIR_2}/all"; do
        if [[ -s "$bg_file" ]]; then
            current_src="$(grep -oE 'Path\("[^"]+"\)' "$bg_file" 2>/dev/null | sed -E 's/^Path\("(.*)"\)$/\1/' || true)"
            if [[ -z "$current_src" ]]; then
                # Non-empty config without Path("...") means user selected Color(...) or custom source
                return 0
            fi
            if [[ "$current_src" != "$WP_FALLBACK" && \
                  "$current_src" != "$WP_16X9" && \
                  "$current_src" != "$WP_32X9" ]]; then
                return 0
            fi
        fi
    done
    return 1
}

ensure_wayland_env() {
    if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
        export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    fi
    if [[ -z "${WAYLAND_DISPLAY:-}" && -d "${XDG_RUNTIME_DIR}" ]]; then
        local sock
        for sock in "${XDG_RUNTIME_DIR}"/wayland-*; do
            [[ -S "$sock" ]] || continue
            export WAYLAND_DISPLAY="$(basename "$sock")"
            break
        done
    fi
}

detect_ultrawide() {
    local mode w h
    local modes=()

    ensure_wayland_env

    if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        if command -v cosmic-randr >/dev/null 2>&1; then
            while IFS= read -r mode; do
                [[ -n "$mode" ]] && modes+=("$mode")
            done < <(cosmic-randr list 2>/dev/null | sed -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' | grep -i 'current' | grep -oE '[0-9]{3,5}x[0-9]{3,5}' || true)
        fi
        if [[ "${#modes[@]}" -eq 0 ]] && command -v wlr-randr >/dev/null 2>&1; then
            while IFS= read -r mode; do
                [[ -n "$mode" ]] && modes+=("$mode")
            done < <(wlr-randr 2>/dev/null | sed -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' | grep -i 'current' | grep -oE '[0-9]{3,5}x[0-9]{3,5}' || true)
        fi
    fi

    if [[ "${#modes[@]}" -eq 0 ]]; then
        local drm_dir status_file modes_file
        for drm_dir in /sys/class/drm/card*-*; do
            [[ -d "$drm_dir" ]] || continue
            status_file="${drm_dir}/status"
            modes_file="${drm_dir}/modes"
            [[ -r "$modes_file" ]] || continue
            if [[ -r "$status_file" ]] && [[ "$(cat "$status_file" 2>/dev/null || true)" != "connected" ]]; then
                continue
            fi
            mode="$(head -n 1 "$modes_file" 2>/dev/null | grep -oE '^[0-9]{3,5}x[0-9]{3,5}' || true)"
            [[ -n "$mode" ]] && modes+=("$mode")
        done
    fi

    if [[ "${#modes[@]}" -eq 0 ]]; then
        local modes_file
        for modes_file in /sys/class/drm/*/modes; do
            [[ -r "$modes_file" ]] || continue
            mode="$(head -n 1 "$modes_file" 2>/dev/null | grep -oE '^[0-9]{3,5}x[0-9]{3,5}' || true)"
            [[ -n "$mode" ]] && modes+=("$mode")
        done
    fi

    for mode in "${modes[@]}"; do
        w="${mode%x*}"
        h="${mode#*x}"
        if [[ "$w" =~ ^[0-9]+$ && "$h" =~ ^[0-9]+$ && "$h" -gt 0 ]]; then
            # Aspect ratio >= 2.2 (w / h >= 22 / 10)
            if (( w * 10 >= h * 22 )); then
                return 0
            fi
        fi
    done
    return 1
}

apply_auto_wallpaper() {
    if is_user_customized; then
        return 0
    fi

    local target_wp="$WP_16X9"
    if detect_ultrawide; then
        target_wp="$WP_32X9"
    fi
    if [[ ! -f "$target_wp" ]]; then
        target_wp="$WP_FALLBACK"
    fi

    mkdir -p "$BG_DIR_1" "$BG_DIR_2"
    local bg_dir cur_src
    for bg_dir in "$BG_DIR_1" "$BG_DIR_2"; do
        cur_src="$(grep -oE 'Path\("[^"]+"\)' "${bg_dir}/all" 2>/dev/null | sed -E 's/^Path\("(.*)"\)$/\1/' || true)"
        if [[ "$cur_src" != "$target_wp" ]] || [[ ! -f "${bg_dir}/same-on-all" ]]; then
            cat << RON > "${bg_dir}/all"
(
    output: "all",
    source: Path("${target_wp}"),
    filter_by_theme: true,
    rotation_frequency: 300,
    filter_method: Lanczos,
    scaling_mode: Zoom,
    sampling_method: Alphanumeric,
)
RON
            printf 'true\n' > "${bg_dir}/same-on-all"
        fi
    done
}

apply_auto_wallpaper

if [[ "$WATCH_MODE" == "true" ]]; then
    sleep 3
    apply_auto_wallpaper
    if command -v udevadm >/dev/null 2>&1; then
        udevadm monitor --udev --subsystem-match=drm 2>/dev/null | while read -r line; do
            if [[ "$line" == *"change"* || "$line" == *"add"* ]]; then
                sleep 1
                apply_auto_wallpaper || true
            fi
        done
    fi
fi
EOF
chmod 0755 /usr/local/bin/novos-wallpaper-auto

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

if [ -x /usr/local/bin/novos-wallpaper-auto ]; then
    /usr/local/bin/novos-wallpaper-auto >/dev/null 2>&1 || true
    /usr/local/bin/novos-wallpaper-auto --watch >/dev/null 2>&1 &
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
auth       requisite    pam_nologin.so
auth       required     pam_succeed_if.so user != root quiet_success
auth       substack     common-auth
-auth      optional     pam_gnome_keyring.so
account    include      common-account
password   substack     common-password
session    required     pam_loginuid.so
session    optional     pam_keyinit.so force revoke
session    required     pam_limits.so
session    required     pam_env.so readenv=1
session    required     pam_env.so readenv=1 user_readenv=1 envfile=/etc/default/locale
session    include      common-session
-session   optional     pam_gnome_keyring.so auto_start
EOF

cat << 'EOF' > /etc/pam.d/greetd
#%PAM-1.0
auth       requisite    pam_nologin.so
auth       required     pam_succeed_if.so user != root quiet_success
auth       substack     common-auth
-auth      optional     pam_gnome_keyring.so
account    include      common-account
password   substack     common-password
session    required     pam_loginuid.so
session    optional     pam_keyinit.so force revoke
session    required     pam_limits.so
session    required     pam_env.so readenv=1
session    required     pam_env.so readenv=1 user_readenv=1 envfile=/etc/default/locale
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
rm -rf "/run/user/$(id -u)/cosmic/com.system76.CosmicSettingsDaemon/v1/"* 2>/dev/null || true
exec /usr/bin/cosmic-greeter "$@"
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

if [ -x /usr/local/bin/novos-wallpaper-auto ]; then
    /usr/local/bin/novos-wallpaper-auto "/var/lib/cosmic-greeter/.config/cosmic" >/dev/null 2>&1 || true
fi

if ls /dev/dri/card* >/dev/null 2>&1 && [ -x /usr/bin/cosmic-comp ] && [ -x /usr/local/bin/cosmic-greeter-app ]; then
    start_ts=$SECONDS
    rc=0
    dbus-run-session -- /usr/bin/cosmic-comp /usr/local/bin/cosmic-greeter-app || rc=$?
    elapsed=$((SECONDS - start_ts))
    if [ "$rc" -eq 0 ] || [ "$elapsed" -ge 5 ]; then
        exit 0
    fi
    echo "[cosmic-greeter-launch] Hardware EGL cosmic-greeter failed on startup (rc=${rc}, elapsed=${elapsed}s); retrying with LIBGL_ALWAYS_SOFTWARE=1..." >&2
    start_ts=$SECONDS
    rc=0
    LIBGL_ALWAYS_SOFTWARE=1 ICED_BACKEND=tiny-skia WGPU_BACKEND=gl dbus-run-session -- /usr/bin/cosmic-comp /usr/local/bin/cosmic-greeter-app || rc=$?
    elapsed=$((SECONDS - start_ts))
    if [ "$rc" -eq 0 ] || [ "$elapsed" -ge 5 ]; then
        exit 0
    fi
    echo "[cosmic-greeter-launch] Graphical cosmic-greeter failed on startup (rc=${rc}, elapsed=${elapsed}s); falling back to tuigreet..." >&2
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
After=systemd-user-sessions.service plymouth-quit-wait.service systemd-logind.service dbus.service cosmic-greeter-daemon.service getty@tty1.service
Wants=systemd-logind.service dbus.service cosmic-greeter-daemon.service

[Service]
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
KeyringMode=shared
StandardOutput=journal
StandardError=journal
EOF

echo "==> [Stage 4.4] Registering Flathub & Enabling Graphical Target Services..."
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true

systemctl enable greetd.service NetworkManager.service
systemctl set-default graphical.target

echo "==> [Stage 4.5] Provisioning NovOS Branding Assets, 16:9/32:9 Wallpapers & COSMIC #FFAD00 Theme..."
mkdir -p /usr/share/backgrounds/novos /usr/share/backgrounds/cosmic/novos /usr/share/novos/themes /usr/share/pixmaps /usr/share/icons/hicolor/scalable/apps
cp -f /root/novos-build/branding/nova-wallpaper.svg /usr/share/backgrounds/novos/nova-wallpaper.svg
for wp_file in \
    nova-wallpaper.png \
    nova-realistic-16x9.png \
    nova-realistic-32x9.png \
    nova-minimal-faceted-dark-16x9.png \
    nova-minimal-faceted-dark-32x9.png \
    nova-minimal-faceted-light-16x9.png \
    nova-minimal-faceted-light-32x9.png \
    nova-minimal-silhouette-dark-16x9.png \
    nova-minimal-silhouette-dark-32x9.png \
    nova-minimal-silhouette-light-16x9.png \
    nova-minimal-silhouette-light-32x9.png; do
    if [[ -f "/root/novos-build/branding/${wp_file}" ]]; then
        cp -f "/root/novos-build/branding/${wp_file}" "/usr/share/backgrounds/novos/${wp_file}"
        ln -sfn "/usr/share/backgrounds/novos/${wp_file}" "/usr/share/backgrounds/cosmic/novos/${wp_file}"
    fi
done

cp -f /root/novos-build/branding/themes/Dark.ron /usr/share/novos/themes/Dark.ron
cp -f /root/novos-build/branding/themes/Light.ron /usr/share/novos/themes/Light.ron
cp -f /root/novos-build/branding/novos-logo.svg /usr/share/pixmaps/novos-logo.svg
cp -f /root/novos-build/branding/novos-logo.png /usr/share/pixmaps/novos-logo.png
cp -f /root/novos-build/branding/novos-logo.svg /usr/share/icons/hicolor/scalable/apps/novos-logo.svg

provision_cosmic_theme() {
    local cfg_base="$1"
    local nova_rgb='(red: 1.0, green: 0.6784314, blue: 0.0)'
    mkdir -p \
        "${cfg_base}/com.system76.CosmicBackground/v1" \
        "${cfg_base}/com.system76.CosmicBg/v1" \
        "${cfg_base}/com.system76.CosmicComp/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Mode/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Dark.Builder/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Dark.Builder/v2" \
        "${cfg_base}/com.system76.CosmicTheme.Light.Builder/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Light.Builder/v2" \
        "${cfg_base}/com.system76.CosmicTheme.Dark/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Dark/v2" \
        "${cfg_base}/com.system76.CosmicTheme.Light/v1" \
        "${cfg_base}/com.system76.CosmicTheme.Light/v2"

    if [[ "$cfg_base" != "/usr/share/cosmic" ]]; then
        if [[ -d /usr/share/cosmic/com.system76.CosmicTheme.Dark ]]; then
            cp -a /usr/share/cosmic/com.system76.CosmicTheme.Dark/v1/* "${cfg_base}/com.system76.CosmicTheme.Dark/v1/" 2>/dev/null || true
            cp -a /usr/share/cosmic/com.system76.CosmicTheme.Dark/v2/* "${cfg_base}/com.system76.CosmicTheme.Dark/v2/" 2>/dev/null || true
        fi
        if [[ -d /usr/share/cosmic/com.system76.CosmicTheme.Light ]]; then
            cp -a /usr/share/cosmic/com.system76.CosmicTheme.Light/v1/* "${cfg_base}/com.system76.CosmicTheme.Light/v1/" 2>/dev/null || true
            cp -a /usr/share/cosmic/com.system76.CosmicTheme.Light/v2/* "${cfg_base}/com.system76.CosmicTheme.Light/v2/" 2>/dev/null || true
        fi
    fi

    printf 'true\n' > "${cfg_base}/com.system76.CosmicTheme.Mode/v1/is_dark"
    printf 'true\n' > "${cfg_base}/com.system76.CosmicComp/v1/active_hint"

    local mode ver
    for mode in Dark Light; do
        for ver in v1 v2; do
            printf '%s\n' "$nova_rgb" > "${cfg_base}/com.system76.CosmicTheme.${mode}.Builder/${ver}/accent"
            printf '%s\n' "$nova_rgb" > "${cfg_base}/com.system76.CosmicTheme.${mode}.Builder/${ver}/window_hint"
            printf '3\n' > "${cfg_base}/com.system76.CosmicTheme.${mode}.Builder/${ver}/active_hint"
            printf '%s\n' "$nova_rgb" > "${cfg_base}/com.system76.CosmicTheme.${mode}/${ver}/accent"
            printf '%s\n' "$nova_rgb" > "${cfg_base}/com.system76.CosmicTheme.${mode}/${ver}/window_hint"
            printf '3\n' > "${cfg_base}/com.system76.CosmicTheme.${mode}/${ver}/active_hint"
        done
    done

    cat << 'RON' > "${cfg_base}/com.system76.CosmicBackground/v1/all"
(
    output: "all",
    source: Path("/usr/share/backgrounds/novos/nova-realistic-16x9.png"),
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
    source: Path("/usr/share/backgrounds/novos/nova-realistic-16x9.png"),
    filter_by_theme: true,
    rotation_frequency: 300,
    filter_method: Lanczos,
    scaling_mode: Zoom,
    sampling_method: Alphanumeric,
)
RON
    printf 'true\n' > "${cfg_base}/com.system76.CosmicBg/v1/same-on-all"
}

provision_cosmic_theme "/usr/share/cosmic"
provision_cosmic_theme "/etc/skel/.config/cosmic"
provision_cosmic_theme "/root/.config/cosmic"
provision_cosmic_theme "/home/${DEFAULT_USER}/.config/cosmic"
provision_cosmic_theme "/var/lib/cosmic-greeter/.config/cosmic"
provision_cosmic_theme "/var/lib/greetd/.config/cosmic"

echo "==> [Stage 4.6] Installing NovOS Slippery Tile Engine, gTile Overlay, Custom Tools & COSMIC Keybindings..."
mkdir -p /usr/local/bin /usr/share/applications /etc/systemd/user /etc/systemd/user/default.target.wants /etc/novos/custom-tools /usr/share/novos/custom-tools

if ! ([[ -x /root/novos-build/target/release/novos-slippery-tiles && -x /root/novos-build/target/release/novos-custom-tools ]] && \
      /root/novos-build/target/release/novos-slippery-tiles --help >/dev/null 2>&1 && \
      /root/novos-build/target/release/novos-custom-tools --help >/dev/null 2>&1); then
    if ! command -v cargo >/dev/null 2>&1; then
        apt-get -o APT::Get::AutomaticRemove=false -o APT::AutoRemove::SuggestsImportant=false install -y cargo rustc
    fi
    cargo build --release --offline --manifest-path /root/novos-build/Cargo.toml || \
        cargo build --release --manifest-path /root/novos-build/Cargo.toml
fi

install -m 0755 /root/novos-build/target/release/novos-slippery-tiles /usr/local/bin/novos-slippery-tiles
ln -sfn /usr/local/bin/novos-slippery-tiles /usr/local/bin/novos-gtile-overlay

install -m 0755 /root/novos-build/target/release/novos-custom-tools /usr/local/bin/novos-custom-tools
cp -f /root/novos-build/custom-tools/manifest.yaml /etc/novos/custom-tools.yaml
cp -f /root/novos-build/custom-tools/manifest.yaml /etc/novos/custom-tools/manifest.yaml
cp -f /root/novos-build/custom-tools/manifest.yaml /usr/share/novos/custom-tools/manifest.yaml

cat << 'EOF' > /usr/share/applications/novos-gtile-overlay.desktop
[Desktop Entry]
Name=NovOS gTile Grid Overlay
Comment=Interactive 12x4 Ultrawide & 8x4 Standard Slippery Tile Grid Selector (Super+G)
Exec=/usr/local/bin/novos-gtile-overlay overlay
Icon=novos-logo
Terminal=false
Type=Application
Categories=Utility;DesktopSettings;
Keywords=tile;gtile;ultrawide;grid;window;cosmic;
StartupNotify=false
EOF

cat << 'EOF' > /etc/systemd/user/novos-slippery-tiles.service
[Unit]
Description=NovOS Adaptive Slippery Tile & Ultrawide Grid Profile Service
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/novos-slippery-tiles daemon

[Install]
WantedBy=default.target graphical-session.target
EOF
ln -sfn /etc/systemd/user/novos-slippery-tiles.service /etc/systemd/user/default.target.wants/novos-slippery-tiles.service

provision_cosmic_shortcuts() {
    local cfg_base="$1"
    mkdir -p \
        "${cfg_base}/com.system76.CosmicSettings.Shortcuts/v1" \
        "${cfg_base}/com.system76.CosmicComp/v1"

    # Prevent single-window auto-fullscreening; manage windows as scaled building blocks
    printf 'false\n' > "${cfg_base}/com.system76.CosmicComp/v1/autotile"

    cat << 'RON' > "${cfg_base}/com.system76.CosmicSettings.Shortcuts/v1/custom"
{
    // Super+Shift+Left/Right/Up/Down -> novos-slippery-tiles move left|right|up|down
    (modifiers: [Super, Shift], key: "Left"): Spawn("novos-slippery-tiles move left"),
    (modifiers: [Super, Shift], key: "Right"): Spawn("novos-slippery-tiles move right"),
    (modifiers: [Super, Shift], key: "Up"): Spawn("novos-slippery-tiles move up"),
    (modifiers: [Super, Shift], key: "Down"): Spawn("novos-slippery-tiles move down"),

    // Super+Ctrl+Left/Right/Up/Down -> novos-slippery-tiles grow left|right|up|down
    (modifiers: [Super, Ctrl], key: "Left"): Spawn("novos-slippery-tiles grow left"),
    (modifiers: [Super, Ctrl], key: "Right"): Spawn("novos-slippery-tiles grow right"),
    (modifiers: [Super, Ctrl], key: "Up"): Spawn("novos-slippery-tiles grow up"),
    (modifiers: [Super, Ctrl], key: "Down"): Spawn("novos-slippery-tiles grow down"),

    // Super+Ctrl+Alt+Left/Right/Up/Down -> novos-slippery-tiles shrink left|right|up|down
    (modifiers: [Super, Ctrl, Alt], key: "Left"): Spawn("novos-slippery-tiles shrink left"),
    (modifiers: [Super, Ctrl, Alt], key: "Right"): Spawn("novos-slippery-tiles shrink right"),
    (modifiers: [Super, Ctrl, Alt], key: "Up"): Spawn("novos-slippery-tiles shrink up"),
    (modifiers: [Super, Ctrl, Alt], key: "Down"): Spawn("novos-slippery-tiles shrink down"),

    // Super+G -> novos-slippery-tiles overlay
    (modifiers: [Super], key: "g"): Spawn("novos-slippery-tiles overlay"),

    // Super+BracketLeft / Super+BracketRight -> novos-slippery-tiles pack left|right
    (modifiers: [Super], key: "bracketleft"): Spawn("novos-slippery-tiles pack left"),
    (modifiers: [Super], key: "bracketright"): Spawn("novos-slippery-tiles pack right"),
}
RON
}

provision_cosmic_shortcuts "/etc/skel/.config/cosmic"
provision_cosmic_shortcuts "/home/${DEFAULT_USER}/.config/cosmic"
provision_cosmic_shortcuts "/root/.config/cosmic"

chown -R "${DEFAULT_USER}:${DEFAULT_USER}" "/home/${DEFAULT_USER}/.config"
chown -R cosmic-greeter:cosmic-greeter /var/lib/cosmic-greeter /var/cache/tuigreet
chmod 0755 /var/lib/cosmic-greeter /var/cache/tuigreet
if id -u _greetd >/dev/null 2>&1; then
    chown -R _greetd:_greetd /var/lib/greetd
fi

verify_stage4_post
echo "==> [Stage 4] COSMIC Desktop & cosmic-greeter (+ tuigreet fallback) setup complete."
