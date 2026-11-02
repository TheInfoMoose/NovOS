#!/usr/bin/env bash
# ==============================================================================
# NovOS Live ISO + Calamares Builder Wrapper
# Thin wrapper that delegates to Stage 7 (scripts/stages/07-live-iso.sh).
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/stages/07-live-iso.sh" "$@"
