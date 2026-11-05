#!/usr/bin/env bash
# ==============================================================================
# NovOS Standalone Slippery Tiles Project Exporter
# ==============================================================================
# Extracts crates/novos-slippery-tiles into a standalone Rust repository layout
# in <staging-dir> (default: ${REPO_ROOT}/build/slippery-tiles-standalone) and
# syncs to <dest> (default: /srv/devshare/slippery-tiles) when writable.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEST_DIR="/srv/devshare/slippery-tiles"
STAGING_DIR="${REPO_ROOT}/build/slippery-tiles-standalone"
SELF_TEST=0

print_usage() {
    cat <<'EOF'
Usage: scripts/export-slippery-tiles.sh [OPTIONS]

Options:
  --dest <dir>         Target standalone repository path (default: /srv/devshare/slippery-tiles)
  --staging-dir <dir>  Local staging directory (default: ${REPO_ROOT}/build/slippery-tiles-standalone)
  --self-test          Generate standalone repo and run cargo fmt, clippy, and test checks
  -h, --help           Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --dest requires a directory path" >&2
                exit 2
            fi
            DEST_DIR="$2"
            shift 2
            ;;
        --dest=*)
            DEST_DIR="${1#*=}"
            shift
            ;;
        --staging-dir)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --staging-dir requires a directory path" >&2
                exit 2
            fi
            STAGING_DIR="$2"
            shift 2
            ;;
        --staging-dir=*)
            STAGING_DIR="${1#*=}"
            shift
            ;;
        --self-test)
            SELF_TEST=1
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            print_usage >&2
            exit 2
            ;;
    esac
done

SRC_MAIN="${REPO_ROOT}/crates/novos-slippery-tiles/src/main.rs"
if [[ ! -f "${SRC_MAIN}" && -f "${DEST_DIR}/src/main.rs" ]]; then
    SRC_MAIN="${DEST_DIR}/src/main.rs"
elif [[ ! -f "${SRC_MAIN}" && -f "${STAGING_DIR}/src/main.rs" ]]; then
    SRC_MAIN="${STAGING_DIR}/src/main.rs"
elif [[ ! -f "${SRC_MAIN}" && -f "/media/infomoose/Bonus/Projects/NovOS/crates/novos-slippery-tiles/src/main.rs" ]]; then
    SRC_MAIN="/media/infomoose/Bonus/Projects/NovOS/crates/novos-slippery-tiles/src/main.rs"
fi
if [[ ! -f "${SRC_MAIN}" ]]; then
    echo "ERROR: Source implementation not found at ${SRC_MAIN}" >&2
    exit 1
fi

mkdir -p "${STAGING_DIR}/src"

cat > "${STAGING_DIR}/Cargo.toml" <<'EOF'
[package]
name = "slippery-tiles"
version = "0.1.0"
edition = "2021"
description = "Standalone Slippery Tile Engine & Interactive gTile Grid Overlay Manager (12x4 / 8x4)"

[workspace]

[[bin]]
name = "slippery-tiles"
path = "src/main.rs"

[dependencies]
EOF

if [[ "$(readlink -f "${SRC_MAIN}" 2>/dev/null || echo "${SRC_MAIN}")" != "$(readlink -f "${STAGING_DIR}/src/main.rs" 2>/dev/null || echo "${STAGING_DIR}/src/main.rs")" ]]; then
    {
        if ! head -n 1 "${SRC_MAIN}" | grep -q "clippy::too_many_arguments"; then
            echo "#![allow(clippy::too_many_arguments, clippy::type_complexity)]"
        fi
        sed 's/nb_right + 1 <= cols/nb_right < cols/g' "${SRC_MAIN}"
    } > "${STAGING_DIR}/src/main.rs"
fi

if command -v cargo >/dev/null 2>&1; then
    cargo fmt --manifest-path "${STAGING_DIR}/Cargo.toml" --all
fi

cat > "${STAGING_DIR}/.gitignore" <<'EOF'
/target
Cargo.lock
EOF

cat > "${STAGING_DIR}/README.md" <<'EOF'
# Slippery Tiles (`slippery-tiles`)

Adaptive window grid manager and deterministic 2D collision-slip solver for COSMIC Desktop and Wayland/X11 environments on ultrawide (`32:9` -> `12x4` grid) and standard (`16:9` -> `8x4` grid) displays.

## Features

- **Adaptive Display Grid Profiles**:
  - **Ultrawide (`32:9`, e.g., `5120x1440`)**: `12x4` grid (`1` col = `426px`, default block = `3x4` = `1280x1440` or `3x2` = `1280x720`).
  - **Standard (`16:9`, e.g., `3840x2160` / `1920x1080`)**: `8x4` grid (default block = `4x4` or `2x4`).
- **Non-Fullscreen Building Blocks**: Spawns windows anchored to `left`, `right`, or `center` (`--anchor left|right|center`, default `left`) without auto-fullscreening single windows.
- **Deterministic 2D Slippery Reflow Solver**: Neighbouring tiles slip out of the way or compress down to `DEFAULT_MIN_SPAN` (`2` columns/rows) before rolling back if blocked, and reclaim space when shrunk.
- **Interactive gTile Grid Overlay (`overlay`)**: Launches an interactive grid selector and preset bar (`1/4 Col`, `1/3 Col`, `Center 1/2`, `2x2 Stack`, `Pack Left`, `Pack Right`).

## CLI Usage

```bash
# Move active window across the grid
slippery-tiles move <left|right|up|down>

# Grow active window and slip/compress colliding neighbors
slippery-tiles grow <left|right|up|down>

# Shrink active window and allow neighbors to reclaim space
slippery-tiles shrink <left|right|up|down>

# Pack active window flush against the left or right boundary/neighbor
slippery-tiles pack <left|right>

# Launch the interactive gTile grid overlay
slippery-tiles overlay

# Initialize or query daemon/grid status JSON
slippery-tiles daemon
slippery-tiles status

# Run built-in deterministic self-test suite
slippery-tiles --self-test
```

## Build & Test Commands

```bash
# Build optimized release binary
cargo build --release

# Run full unit test suite
cargo test

# Run formatting and lint verification gates
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
```
EOF

sync_to_dest() {
    local target="$1"
    local parent
    parent="$(dirname "${target}")"

    if [[ -d "${target}" && -w "${target}" ]] || [[ ! -e "${target}" && -d "${parent}" && -w "${parent}" ]]; then
        mkdir -p "${target}/src"
        cp -f "${STAGING_DIR}/Cargo.toml" "${target}/Cargo.toml"
        cp -f "${STAGING_DIR}/src/main.rs" "${target}/src/main.rs"
        cp -f "${STAGING_DIR}/README.md" "${target}/README.md"
        cp -f "${STAGING_DIR}/.gitignore" "${target}/.gitignore"
        echo "[export-slippery-tiles] Synced standalone project to ${target}"
    else
        echo "[export-slippery-tiles] Staged standalone project in ${STAGING_DIR}"
        echo "[export-slippery-tiles] Destination ${target} is not writable in this sandbox; run on host:"
        echo "  mkdir -p \"${target}\" && cp -rf \"${STAGING_DIR}/.\" \"${target}/\""
    fi
}

sync_to_dest "${DEST_DIR}"

if [[ "${SELF_TEST}" -eq 1 ]]; then
    echo "[export-slippery-tiles] Running standalone Rust verification gates in ${STAGING_DIR}..."
    cargo fmt --manifest-path "${STAGING_DIR}/Cargo.toml" --all -- --check
    cargo clippy --manifest-path "${STAGING_DIR}/Cargo.toml" --all-targets -- -D warnings
    cargo test --manifest-path "${STAGING_DIR}/Cargo.toml"
    echo "[export-slippery-tiles] SELF-TEST PASSED"
fi
