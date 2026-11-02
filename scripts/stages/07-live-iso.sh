#!/usr/bin/env bash
# ==============================================================================
# Stage 7 Compatibility Wrapper -> Unified Stage 6 Live ISO Builder
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/06-snapshot-finalize.sh" "$@"
