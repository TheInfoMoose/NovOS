#!/usr/bin/env bash
# ==============================================================================
# NovOS Pop!_OS Host Environment Setup for Containerized QEMU & llama.cpp CI
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker/docker-compose.ci.yml"

LLM_DIR="${NOVOS_LLM_HOST_DIR:-/srv/docker-llms}"
VM_DIR="${NOVOS_VM_HOST_DIR:-/srv/docker-vms}"
MODEL_FILENAME="${NOVOS_LLM_MODEL:-Qwen3-14B-Q4_K_M.gguf}"
MODEL_URL="https://huggingface.co/bartowski/Qwen_Qwen3-14B-GGUF/resolve/main/Qwen_Qwen3-14B-Q4_K_M.gguf"

PULL_MODEL=0
START_LLM=0
SHOW_STATUS=0
DRY_RUN=0
SELF_TEST=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Configure and verify the Pop!_OS host environment for NovOS containerized QEMU
(/srv/docker-vms) and llama.cpp + Qwen3-14B-Q4_K_M LLM Host (/srv/docker-llms).

Options:
  --llm-dir <dir>       Host directory for GGUF models (default: /srv/docker-llms)
  --vm-dir <dir>        Host directory for QEMU VM storage (default: /srv/docker-vms)
  --model-url <url>     GGUF model download URL (default: Hugging Face Qwen3-14B-Q4_K_M)
  --pull-model          Download Qwen3-14B-Q4_K_M.gguf into --llm-dir if not present
  --start-llm           Start novos-llm-host and build novos-qemu-tester via Docker Compose
  --status              Check device nodes, storage paths, model presence, and container status
  --dry-run             Print planned verification and setup commands without mutating state
  --self-test           Run static self-test validation of configuration and compose files
  -h, --help            Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --llm-dir)
            LLM_DIR="${2:?Missing argument for --llm-dir}"
            shift 2
            ;;
        --vm-dir)
            VM_DIR="${2:?Missing argument for --vm-dir}"
            shift 2
            ;;
        --model-url)
            MODEL_URL="${2:?Missing argument for --model-url}"
            shift 2
            ;;
        --pull-model)
            PULL_MODEL=1
            shift
            ;;
        --start-llm)
            START_LLM=1
            shift
            ;;
        --status)
            SHOW_STATUS=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --self-test)
            SELF_TEST=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

run_self_test() {
    echo "[SELF-TEST] Validating docker/Dockerfile.qemu-tester and docker/docker-compose.ci.yml..."
    [[ -f "${REPO_ROOT}/docker/Dockerfile.qemu-tester" ]] || { echo "FAIL: Missing Dockerfile.qemu-tester" >&2; exit 1; }
    [[ -f "${COMPOSE_FILE}" ]] || { echo "FAIL: Missing docker-compose.ci.yml" >&2; exit 1; }

    grep -q "NOVOS_VM_STORAGE_DIR=/vms" "${REPO_ROOT}/docker/Dockerfile.qemu-tester"
    grep -q "NOVOS_LLM_ENDPOINT=http://novos-llm-host:8080/v1" "${REPO_ROOT}/docker/Dockerfile.qemu-tester"
    grep -q "NOVOS_LLM_MODEL=Qwen3-14B-Q4_K_M.gguf" "${REPO_ROOT}/docker/Dockerfile.qemu-tester"
    grep -q "swtpm" "${REPO_ROOT}/docker/Dockerfile.qemu-tester"

    python3 - "${COMPOSE_FILE}" <<'PYEOF'
import sys
import yaml

compose_path = sys.argv[1]
with open(compose_path, "r", encoding="utf-8") as fh:
    dc = yaml.safe_load(fh)

services = dc.get("services", {})
for svc in ("novos-builder", "novos-qemu-tester", "novos-llm-host"):
    assert svc in services, f"Missing service: {svc}"

tester_vols = services["novos-qemu-tester"].get("volumes", [])
assert any("/srv/docker-vms:/vms" in v for v in tester_vols), "Missing /srv/docker-vms:/vms volume in novos-qemu-tester"

llm_vols = services["novos-llm-host"].get("volumes", [])
assert any("/srv/docker-llms:/models" in v for v in llm_vols), "Missing /srv/docker-llms:/models volume in novos-llm-host"

llm_cmd = services["novos-llm-host"].get("command", [])
assert "--flash-attn" in llm_cmd, "Missing --flash-attn in novos-llm-host command"
PYEOF

    echo "[SELF-TEST] PASS: Pop!_OS containerized QEMU (/srv/docker-vms) & LLM host (/srv/docker-llms) configuration valid."
}

check_host_status() {
    echo "=== NovOS Pop!_OS Host Environment Status ==="
    for dev in /dev/kvm /dev/dri /dev/kfd; do
        if [[ -e "${dev}" ]]; then
            echo "  [OK] Device present: ${dev}"
        else
            echo "  [WARN] Device missing: ${dev}"
        fi
    done

    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        echo "  [OK] Docker Compose available: $(docker compose version --short 2>/dev/null || echo 'detected')"
    else
        echo "  [WARN] Docker Compose CLI not detected in PATH"
    fi

    for dir in "${VM_DIR}" "${LLM_DIR}"; do
        if [[ -d "${dir}" ]]; then
            echo "  [OK] Directory exists: ${dir}"
        else
            echo "  [WARN] Directory not yet created: ${dir}"
        fi
    done

    local model_path="${LLM_DIR}/${MODEL_FILENAME}"
    if [[ -f "${model_path}" ]]; then
        echo "  [OK] LLM GGUF model present: ${model_path}"
    else
        echo "  [INFO] LLM GGUF model not yet downloaded: ${model_path}"
    fi

    echo ""
    echo "=== Quick-Start Single Command ==="
    echo "  ./scripts/setup-popos-test-env.sh --llm-dir \"${LLM_DIR}\" --vm-dir \"${VM_DIR}\" --pull-model --start-llm"
}

if [[ "${SELF_TEST}" -eq 1 ]]; then
    run_self_test
    exit 0
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[DRY-RUN] Target VM directory:  ${VM_DIR}"
    echo "[DRY-RUN] Target LLM directory: ${LLM_DIR}"
    echo "[DRY-RUN] Target Model file:    ${LLM_DIR}/${MODEL_FILENAME}"
    echo "[DRY-RUN] Target Model URL:     ${MODEL_URL}"
    echo "[DRY-RUN] Would verify: /dev/kvm, /dev/dri, /dev/kfd, docker compose"
    echo "[DRY-RUN] Would ensure directories exist: mkdir -p \"${VM_DIR}\" \"${LLM_DIR}\""
    echo "[DRY-RUN] Single command to download model and bring up novos-llm-host + novos-qemu-tester:"
    echo "  curl -fL \"${MODEL_URL}\" -o \"${LLM_DIR}/${MODEL_FILENAME}\" && NOVOS_VM_HOST_DIR=\"${VM_DIR}:/vms\" NOVOS_LLM_HOST_DIR=\"${LLM_DIR}:/models\" docker compose -f \"${COMPOSE_FILE}\" up -d novos-llm-host && NOVOS_VM_HOST_DIR=\"${VM_DIR}:/vms\" NOVOS_LLM_HOST_DIR=\"${LLM_DIR}:/models\" docker compose -f \"${COMPOSE_FILE}\" build novos-qemu-tester"
    exit 0
fi

if [[ "${SHOW_STATUS}" -eq 1 ]]; then
    check_host_status
    exit 0
fi

check_host_status

if [[ "${PULL_MODEL}" -eq 1 ]]; then
    mkdir -p "${LLM_DIR}" "${VM_DIR}"
    target_model="${LLM_DIR}/${MODEL_FILENAME}"
    if [[ -f "${target_model}" ]]; then
        echo "[INFO] Model already exists at ${target_model}; skipping download."
    else
        echo "[INFO] Downloading ${MODEL_FILENAME} from ${MODEL_URL}..."
        curl -fL --progress-bar "${MODEL_URL}" -o "${target_model}.part"
        mv "${target_model}.part" "${target_model}"
        echo "[OK] Downloaded ${target_model}"
    fi
fi

if [[ "${START_LLM}" -eq 1 ]]; then
    mkdir -p "${LLM_DIR}" "${VM_DIR}"
    export NOVOS_VM_HOST_DIR="${VM_DIR}:/vms"
    export NOVOS_LLM_HOST_DIR="${LLM_DIR}:/models"
    export NOVOS_LLM_MODEL="${MODEL_FILENAME}"
    echo "[INFO] Starting novos-llm-host and building novos-qemu-tester via Docker Compose..."
    docker compose -f "${COMPOSE_FILE}" up -d novos-llm-host
    docker compose -f "${COMPOSE_FILE}" build novos-qemu-tester
fi
