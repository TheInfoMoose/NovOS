#!/usr/bin/env python3
"""
NovOS Two-Tier Automated QEMU/KVM Test Harness (scripts/test-docker-qemu.py)
============================================================================
Executes automated, headless or VNC-enabled UEFI (OVMF) QEMU/KVM verification
across two tiers:
  - Tier 1 (`live-boot`):
      1. UEFI OVMF + Limine boot entry handoff.
      2. Guest network/SSH or serial milestone reached (`systemctl is-system-running`
         or `cosmic-comp`/`greetd` active).
      3. Verify `/etc/os-release` (`ID=novos`),
         `/usr/local/bin/novos-custom-tools --self-test`,
         and `/etc/calamares/settings.conf` inside the live guest.
      4. Capture final QMP `screendump` to `logs/qemu-artifacts/live-boot-final.ppm`.
  - Tier 2 (`install-e2e`):
      1. Complete all `live-boot` checks.
      2. Trigger unattended/headless Calamares or partition/unpack/limine-bootloader
         verification against `/dev/vda`, verifying 6 GiB `NOVOS_EFI` (`vda1`) +
         `NOVOS_ROOT` Btrfs subvolumes (`@`, `@home`, `@snapshots`, `@srv_llm`, etc.) +
         `/boot/efi/limine.conf` + `/boot/efi/EFI/BOOT/BOOTX64.EFI`.
      3. Reboot VM from `/dev/vda` (without CD-ROM boot override) and verify
         installed system boots cleanly.

Enforces pre-flight Nebulous guardrails (`check-guardrail` and `verify-variance`)
prior to booting QEMU and records post-run telemetry via `nebulous-ctl.py record-attempt`
plus `logs/qemu-artifacts/last-test-verdict.json`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import urllib.error
import urllib.request
import zlib
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_VARIANCE_LOCK = 2
EXIT_LOOP_LOCK = 3

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
NEBULOUS_CTL = REPO_ROOT / "scripts" / "nebulous-ctl.py"
BUILD_ENV_PATH = REPO_ROOT / "config" / "build.env"
DEFAULT_ISO_PATH = "./build/NovOS-sid-amd64.iso"
DEFAULT_ITEM_TOML = ".nebulous/items/item-001-docker-qemu-ci.toml"
HOST_DOCKER_VMS_DIR = pathlib.Path("/srv/docker-vms")
DEFAULT_LLM_ENDPOINT = os.environ.get("NOVOS_LLM_ENDPOINT", "http://127.0.0.1:8081/v1")
DEFAULT_LLM_MODEL = os.environ.get("NOVOS_LLM_MODEL", "Qwen3-14B-Q4_K_M.gguf")
ARTIFACTS_DIR = REPO_ROOT / "logs" / "qemu-artifacts"

DEFAULT_TIMEOUT_BY_TIER = {
    "live-boot": 420,
    "install-e2e": 1200,
}

OVMF_SPLIT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/edk2/x64/OVMF_CODE.4m.fd", "/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
    ("/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd"),
    ("/usr/share/edk2-ovmf/x64/OVMF_CODE.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"),
)

OVMF_COMBINED_CANDIDATES: tuple[str, ...] = (
    "/usr/share/ovmf/OVMF.fd",
    "/usr/share/qemu/OVMF.fd",
)

MILESTONE_REGEXES: dict[str, re.Pattern[str]] = {
    "uefi_ovmf": re.compile(
        r"(?i)(BdsDxe:\s+(?:loading|starting)\s+Boot|TianoCore|UEFI\s+Interactive\s+Shell|Booting\s+.*\bNovOS\b|Limine\b)"
    ),
    "limine_handoff": re.compile(
        r"(?i)(Limine\s+v?\d|Booting\s+NovOS|Linux\s+version\s+\d+\.\d+|Command\s+line:.*boot=live)"
    ),
    "systemd_or_session_ready": re.compile(
        r"(?i)(Reached\s+target\s+.*(?:Graphical\s+Interface|Multi-User\s+System)|"
        r"systemd\[1\]:\s+Startup\s+finished|greetd|cosmic-comp|cosmic-greeter|"
        r"novos\s+login:|SERIAL_MILESTONE_READY)"
    ),
    "installed_boot_ready": re.compile(
        r"(?i)(root=LABEL=NOVOS_ROOT|rootflags=[^\n]*subvol=@|Reached\s+target\s+.*(?:Graphical\s+Interface|Multi-User\s+System)|"
        r"novos\s+login:|INSTALLED_SYSTEM_READY)"
    ),
}


# ==============================================================================
# 1. Configuration & OVMF Discovery
# ==============================================================================

def load_build_env(env_path: pathlib.Path = BUILD_ENV_PATH) -> dict[str, str]:
    """Parse key=value exports from config/build.env."""
    cfg = {
        "DISTRO_ID": "novos",
        "DEFAULT_USER": "devuser",
        "DEFAULT_USER_PASS": "novos",
        "DEFAULT_ROOT_PASS": "novos",
        "ESP_SIZE_MIB": "6144",
        "ESP_LABEL": "NOVOS_EFI",
        "ROOT_LABEL": "NOVOS_ROOT",
        "ISO_PATH": DEFAULT_ISO_PATH,
    }
    if not env_path.is_file():
        return cfg

    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        cfg[k] = v
    return cfg


def resolve_vm_storage_dir(
    explicit_dir: str | pathlib.Path | None = None,
    host_vm_dir: pathlib.Path = HOST_DOCKER_VMS_DIR,
    fallback_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """
    Resolve persistent VM storage directory for `novos-calamares-target.img` and `ovmf_vars.fd`:
      1. Explicit `--vm-dir` argument (`explicit_dir`)
      2. `NOVOS_VM_STORAGE_DIR` environment variable (e.g. `/vms` inside container)
      3. `/srv/docker-vms` on the host when present and writable
      4. `REPO_ROOT / "build"` fallback
    """
    if explicit_dir is not None and str(explicit_dir).strip():
        return pathlib.Path(explicit_dir).resolve()

    env_dir = os.environ.get("NOVOS_VM_STORAGE_DIR", "").strip()
    if env_dir:
        return pathlib.Path(env_dir).resolve()

    if host_vm_dir.is_dir() and os.access(host_vm_dir, os.W_OK | os.X_OK):
        return host_vm_dir.resolve()

    if fallback_dir is not None:
        return fallback_dir.resolve()

    return (REPO_ROOT / "build").resolve()


def locate_ovmf_firmware(
    split_candidates: tuple[tuple[str, str], ...] = OVMF_SPLIT_CANDIDATES,
    combined_candidates: tuple[str, ...] = OVMF_COMBINED_CANDIDATES,
    dry_run: bool = False,
) -> dict[str, str]:
    """
    Locate OVMF UEFI firmware on the host/container.
    Returns a dict with:
      - mode: 'split' or 'combined'
      - code: path to OVMF_CODE (or combined OVMF.fd)
      - vars_template: path to OVMF_VARS template (empty string if 'combined')
    """
    for code_cand, vars_cand in split_candidates:
        if os.path.isfile(code_cand) and os.path.isfile(vars_cand):
            return {
                "mode": "split",
                "code": code_cand,
                "vars_template": vars_cand,
            }

    for bios_cand in combined_candidates:
        if os.path.isfile(bios_cand):
            return {
                "mode": "combined",
                "code": bios_cand,
                "vars_template": "",
            }

    if dry_run:
        default_code, default_vars = split_candidates[0]
        return {
            "mode": "split",
            "code": default_code,
            "vars_template": default_vars,
        }

    raise FileNotFoundError(
        "Could not locate OVMF UEFI firmware (searched OVMF_CODE/OVMF_VARS and OVMF.fd)."
    )


def prepare_ovmf_vars(
    ovmf_info: dict[str, str],
    vars_dest: pathlib.Path,
    reset_vars: bool = True,
    dry_run: bool = False,
) -> pathlib.Path | None:
    """Prepare a writable OVMF_VARS pflash copy at `vars_dest`."""
    if ovmf_info.get("mode") != "split":
        return None
    if dry_run:
        return vars_dest
    vars_dest.parent.mkdir(parents=True, exist_ok=True)
    if reset_vars or not vars_dest.is_file():
        shutil.copyfile(ovmf_info["vars_template"], vars_dest)
    return vars_dest


def ensure_target_disk(
    disk_path: pathlib.Path,
    disk_size_gib: int = 64,
    reset_disk: bool = False,
    dry_run: bool = False,
) -> str:
    """Ensure the VirtIO target disk exists and return its format ('qcow2' or 'raw')."""
    if dry_run:
        return "qcow2" if shutil.which("qemu-img") else "raw"

    disk_path.parent.mkdir(parents=True, exist_ok=True)
    expected_bytes = disk_size_gib * 1073741824
    need_create = reset_disk or not disk_path.is_file()
    disk_format = "qcow2"

    if not need_create and disk_path.is_file():
        try:
            with open(disk_path, "rb") as f:
                hdr = f.read(32)
            if len(hdr) >= 32 and hdr[:4] == b"QFI\xfb":
                disk_format = "qcow2"
                vsize = struct.unpack(">Q", hdr[24:32])[0]
            else:
                disk_format = "raw"
                vsize = disk_path.stat().st_size
            if vsize != expected_bytes:
                need_create = True
        except OSError:
            need_create = True

    if need_create:
        if disk_path.exists():
            disk_path.unlink()
        if shutil.which("qemu-img"):
            disk_format = "qcow2"
            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2", str(disk_path), f"{disk_size_gib}G"],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            disk_format = "raw"
            with open(disk_path, "wb") as f:
                f.truncate(expected_bytes)

    return disk_format


# ==============================================================================
# 2. QMP Command Builder, Client & Framebuffer Analysis
# ==============================================================================

def build_qmp_command(execute: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Construct a structured QMP command payload."""
    cmd: dict[str, Any] = {"execute": execute}
    if arguments is not None:
        cmd["arguments"] = dict(arguments)
    return cmd


def build_qmp_json(execute: str, arguments: dict[str, Any] | None = None) -> str:
    """Serialize a QMP command payload as a single-line JSON string."""
    return json.dumps(build_qmp_command(execute, arguments), separators=(",", ":"))


class QmpClient:
    """Minimal synchronous QMP client over a Unix domain socket."""

    def __init__(self, socket_path: pathlib.Path, timeout: float = 10.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def execute(self, command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(str(self.socket_path))
            f = sock.makefile("r", encoding="utf-8", errors="replace")

            # Read QMP greeting
            greeting_line = f.readline()
            if not greeting_line:
                raise RuntimeError("Empty QMP greeting from socket")

            # Negotiate qmp_capabilities
            sock.sendall((build_qmp_json("qmp_capabilities") + "\r\n").encode("utf-8"))
            self._read_return(f)

            # Send target command
            sock.sendall((build_qmp_json(command, arguments) + "\r\n").encode("utf-8"))
            return self._read_return(f)

    @staticmethod
    def _read_return(stream: Any) -> dict[str, Any]:
        while True:
            line = stream.readline()
            if not line:
                raise RuntimeError("EOF while waiting for QMP response")
            msg = json.loads(line)
            if "return" in msg or "error" in msg:
                if "error" in msg:
                    raise RuntimeError(f"QMP error: {msg['error']}")
                return msg


def analyze_ppm_and_convert_png(
    ppm_path: pathlib.Path,
    png_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """
    Inspect a binary P6 PPM screendump produced by QMP `screendump`, compute basic
    non-blank frame metrics, and optionally write a lossless PNG copy using pure-Python
    zlib + struct (no external imaging libraries required).
    """
    if not ppm_path.is_file():
        return {"valid": False, "reason": "missing_file"}

    data = ppm_path.read_bytes()
    if not data.startswith(b"P6"):
        return {"valid": False, "reason": "not_p6_ppm"}

    # Parse P6 header: magic, width, height, maxval (skipping # comments)
    idx = 2
    tokens: list[bytes] = []
    n = len(data)
    while len(tokens) < 3 and idx < n:
        while idx < n and data[idx : idx + 1].isspace():
            idx += 1
        if idx < n and data[idx : idx + 1] == b"#":
            while idx < n and data[idx : idx + 1] != b"\n":
                idx += 1
            continue
        start = idx
        while idx < n and not data[idx : idx + 1].isspace():
            idx += 1
        if idx > start:
            tokens.append(data[start:idx])

    if len(tokens) < 3:
        return {"valid": False, "reason": "truncated_header"}

    # Skip single whitespace byte after maxval
    if idx < n and data[idx : idx + 1].isspace():
        idx += 1

    width = int(tokens[0])
    height = int(tokens[1])
    maxval = int(tokens[2])
    pixels = data[idx:]
    expected_len = width * height * 3
    if width <= 0 or height <= 0 or maxval != 255 or len(pixels) < expected_len:
        return {
            "valid": False,
            "reason": "invalid_dimensions_or_pixel_buffer",
            "width": width,
            "height": height,
        }

    pixels = pixels[:expected_len]
    non_zero_bytes = sum(1 for b in pixels[::max(1, len(pixels) // 4096)] if b > 0)

    out_png = None
    if png_path is not None:
        png_path.parent.mkdir(parents=True, exist_ok=True)
        _write_rgb_png(png_path, width, height, pixels)
        out_png = str(png_path)

    return {
        "valid": True,
        "width": width,
        "height": height,
        "non_blank": non_zero_bytes > 0,
        "ppm_path": str(ppm_path),
        "png_path": out_png,
    }


def _write_rgb_png(png_path: pathlib.Path, width: int, height: int, rgb_bytes: bytes) -> None:
    """Encode raw 24-bit RGB bytes into a standard PNG file using only stdlib struct + zlib."""
    def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    stride = width * 3
    raw_scanlines = bytearray()
    for row in range(height):
        raw_scanlines.append(0)  # Filter type 0 (None)
        start = row * stride
        raw_scanlines.extend(rgb_bytes[start : start + stride])

    idat = zlib.compress(bytes(raw_scanlines), level=6)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )
    png_path.write_bytes(png_bytes)


# ==============================================================================
# 3. QEMU Command Line & Tiered Milestone Plan Construction
# ==============================================================================

def build_qemu_command(
    iso_path: pathlib.Path,
    target_disk: pathlib.Path,
    disk_format: str,
    ovmf_info: dict[str, str],
    ovmf_vars_path: pathlib.Path | None,
    qmp_socket: pathlib.Path,
    serial_log: pathlib.Path,
    ram_mb: int = 8192,
    cpus: int = 4,
    use_vnc: bool = False,
    ssh_port: int = 2222,
    boot_from_disk_only: bool = False,
    kvm_available: bool | None = None,
) -> list[str]:
    """Construct the deterministic `qemu-system-x86_64` command vector."""
    if kvm_available is None:
        kvm_available = os.access("/dev/kvm", os.R_OK | os.W_OK)

    cmd: list[str] = ["qemu-system-x86_64"]
    if kvm_available:
        cmd.extend(["-enable-kvm", "-machine", "q35,accel=kvm", "-cpu", "host"])
    else:
        cmd.extend(["-machine", "q35,accel=tcg", "-cpu", "max"])

    cmd.extend(["-smp", str(cpus), "-m", str(ram_mb)])

    if ovmf_info.get("mode") == "split" and ovmf_vars_path is not None:
        cmd.extend(
            [
                "-drive",
                f"if=pflash,format=raw,readonly=on,file={ovmf_info['code']}",
                "-drive",
                f"if=pflash,format=raw,file={ovmf_vars_path}",
            ]
        )
    else:
        cmd.extend(["-bios", ovmf_info["code"]])

    if not boot_from_disk_only:
        cmd.extend(
            [
                "-cdrom",
                str(iso_path),
                "-global",
                "ide-cd.bootindex=1",
                "-boot",
                "order=d,menu=on",
            ]
        )
    else:
        cmd.extend(["-boot", "order=c,menu=off"])

    cmd.extend(
        [
            "-drive",
            f"file={target_disk},if=virtio,format={disk_format},discard=unmap",
            "-device",
            "virtio-vga",
        ]
    )

    if use_vnc:
        cmd.extend(["-vnc", "0.0.0.0:0"])
    else:
        cmd.extend(["-display", "none"])

    cmd.extend(
        [
            "-qmp",
            f"unix:{qmp_socket},server,nowait",
            "-serial",
            f"file:{serial_log}",
            "-netdev",
            f"user,id=net0,hostfwd=tcp::{ssh_port}-:22",
            "-device",
            "virtio-net-pci,netdev=net0",
        ]
    )
    return cmd


def build_milestone_plan(tier: str) -> list[dict[str, str]]:
    """Return the ordered list of verification milestones for the requested tier."""
    live_boot_milestones = [
        {
            "id": "M1_UEFI_LIMINE_HANDOFF",
            "tier": "live-boot",
            "description": "UEFI OVMF + Limine boot entry handoff",
        },
        {
            "id": "M2_GUEST_SESSION_READY",
            "tier": "live-boot",
            "description": "Guest network/SSH or serial milestone reached (systemctl is-system-running or cosmic-comp/greetd active)",
        },
        {
            "id": "M3_LIVE_GUEST_INVARIANTS",
            "tier": "live-boot",
            "description": (
                "Verify /etc/os-release (ID=novos), "
                "/usr/local/bin/novos-custom-tools --self-test, "
                "and /etc/calamares/settings.conf inside the live guest"
            ),
        },
        {
            "id": "M4_LIVE_FINAL_SCREENDUMP",
            "tier": "live-boot",
            "description": "Capture final QMP screendump to logs/qemu-artifacts/live-boot-final.ppm",
        },
    ]

    if tier == "live-boot":
        return live_boot_milestones

    if tier == "install-e2e":
        return live_boot_milestones + [
            {
                "id": "M5_CALAMARES_INSTALL_VDA",
                "tier": "install-e2e",
                "description": (
                    "Trigger unattended/headless Calamares or partition/unpack/limine-bootloader "
                    "verification against /dev/vda, verifying 6 GiB NOVOS_EFI (vda1) + "
                    "NOVOS_ROOT Btrfs subvolumes (@, @home, @snapshots, @srv_llm, etc.) + "
                    "/boot/efi/limine.conf + /boot/efi/EFI/BOOT/BOOTX64.EFI"
                ),
            },
            {
                "id": "M6_REBOOT_INSTALLED_VDA",
                "tier": "install-e2e",
                "description": "Reboot VM from /dev/vda (without CD-ROM boot override) and verify installed system boots cleanly",
            },
        ]

    raise ValueError(f"Unsupported tier: {tier}")


def match_serial_milestones(serial_text: str) -> dict[str, bool]:
    """Evaluate all compiled milestone regexes against captured serial log text."""
    return {
        name: bool(pattern.search(serial_text))
        for name, pattern in MILESTONE_REGEXES.items()
    }


# ==============================================================================
# 4. Pre-Flight Guardrails, Post-Run Telemetry & Local Qwen3-14B Diagnostics
# ==============================================================================

def _parse_llm_json_content(raw_content: Any) -> dict[str, Any]:
    """Extract a JSON object from LLM message content, stripping <think> blocks or markdown fences."""
    if isinstance(raw_content, dict):
        return raw_content
    if not isinstance(raw_content, str):
        return {}
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", raw_content).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    return {}


def query_local_llm_diagnostic(
    error_summary: str,
    serial_tail: str,
    negative_constraints: list[str],
    endpoint: str = DEFAULT_LLM_ENDPOINT,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """
    POST a bounded diagnostic prompt to `<endpoint>/chat/completions`
    (`model: "Qwen3-14B-Q4_K_M.gguf"`, `temperature: 0.1`,
    `response_format: {"type": "json_object"}`) requesting:
      `{"root_cause": "...", "subsystem": "...", "recommended_fix": "...", "violates_negative_constraints": false}`
    Gracefully returns `{"available": False, ...}` if the local LLM container is offline.
    """
    base_url = (endpoint or DEFAULT_LLM_ENDPOINT).strip().rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    model_name = os.environ.get("NOVOS_LLM_MODEL", DEFAULT_LLM_MODEL)

    req_payload = {
        "model": model_name,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a NovOS Linux kernel/UEFI/COSMIC CI diagnostic assistant. "
                    "Respond ONLY with a valid JSON object matching: "
                    '{"root_cause": "...", "subsystem": "...", "recommended_fix": "...", '
                    '"violates_negative_constraints": false}. '
                    "Never recommend dropping packages, downgrading versions, or repeating "
                    "approaches forbidden by negative_constraints."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "error_summary": str(error_summary)[:1000],
                        "serial_tail": str(serial_tail)[-4000:],
                        "negative_constraints": list(negative_constraints or []),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(req_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
        resp_json = json.loads(raw_body)

        content_obj: dict[str, Any] = {}
        choices = resp_json.get("choices")
        if isinstance(choices, list) and choices:
            msg_content = choices[0].get("message", {}).get("content", "")
            content_obj = _parse_llm_json_content(msg_content)
        elif isinstance(resp_json, dict) and "root_cause" in resp_json:
            content_obj = resp_json

        return {
            "available": True,
            "endpoint": url,
            "model": model_name,
            "root_cause": str(content_obj.get("root_cause", "unknown")),
            "subsystem": str(content_obj.get("subsystem", "unknown")),
            "recommended_fix": str(content_obj.get("recommended_fix", "")),
            "violates_negative_constraints": bool(
                content_obj.get("violates_negative_constraints", False)
            ),
        }
    except Exception as exc:
        return {
            "available": False,
            "endpoint": url,
            "model": model_name,
            "error": str(exc),
            "root_cause": "",
            "subsystem": "",
            "recommended_fix": "",
            "violates_negative_constraints": False,
        }


def run_preflight_guardrails(
    item_toml: pathlib.Path,
    iso_path: pathlib.Path,
    skip_variance_check: bool = False,
    nebulous_ctl: pathlib.Path = NEBULOUS_CTL,
) -> int:
    """
    Execute mandatory pre-flight guardrails before booting QEMU:
      1. `scripts/nebulous-ctl.py check-guardrail --item <item_toml>`
         (aborts immediately with exit code 3 if loop-locked)
      2. `scripts/nebulous-ctl.py verify-variance --iso <iso>`
         (aborts immediately with exit code 2 if the ISO violates manifest or golden snapshot)
    """
    chk_res = subprocess.run(
        [sys.executable, str(nebulous_ctl), "check-guardrail", "--item", str(item_toml)],
        check=False,
        capture_output=True,
        text=True,
    )
    if chk_res.stdout:
        print(chk_res.stdout.strip())
    if chk_res.stderr:
        print(chk_res.stderr.strip(), file=sys.stderr)
    if chk_res.returncode == EXIT_LOOP_LOCK:
        return EXIT_LOOP_LOCK
    if chk_res.returncode == EXIT_VARIANCE_LOCK:
        return EXIT_VARIANCE_LOCK
    if chk_res.returncode != EXIT_OK:
        return chk_res.returncode

    if not skip_variance_check:
        var_res = subprocess.run(
            [sys.executable, str(nebulous_ctl), "verify-variance", "--iso", str(iso_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if var_res.stdout:
            print(var_res.stdout.strip())
        if var_res.stderr:
            print(var_res.stderr.strip(), file=sys.stderr)
        if var_res.returncode != EXIT_OK:
            return EXIT_VARIANCE_LOCK

    return EXIT_OK


def record_test_telemetry(
    item_toml: pathlib.Path,
    tier: str,
    status: str,
    log_file: pathlib.Path,
    verdict_payload: dict[str, Any],
    error_summary: str = "",
    nebulous_ctl: pathlib.Path = NEBULOUS_CTL,
    verdict_path: pathlib.Path | None = None,
) -> int:
    """
    Invoke `scripts/nebulous-ctl.py record-attempt` and write structured verdict JSON to
    `logs/qemu-artifacts/last-test-verdict.json`.
    """
    if verdict_path is None:
        verdict_path = ARTIFACTS_DIR / "last-test-verdict.json"
    verdict_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(nebulous_ctl),
        "record-attempt",
        "--item",
        str(item_toml),
        "--stage",
        tier,
        "--status",
        status,
        "--log-file",
        str(log_file),
    ]
    if error_summary:
        cmd.extend(["--error-summary", error_summary])

    rec_res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if rec_res.stdout:
        print(rec_res.stdout.strip())
    if rec_res.stderr:
        print(rec_res.stderr.strip(), file=sys.stderr)

    verdict_payload["guardrail_record_exit_code"] = rec_res.returncode
    verdict_path.write_text(json.dumps(verdict_payload, indent=2) + "\n", encoding="utf-8")
    return rec_res.returncode


# ==============================================================================
# 5. Guest SSH & Live / Install E2E Probing Routines
# ==============================================================================

def run_guest_ssh(
    remote_cmd: str,
    user: str = "devuser",
    password: str = "novos",
    port: int = 2222,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Execute a shell command inside the QEMU guest over localhost SSH port forwarding."""
    ssh_base = [
        "ssh",
        "-p",
        str(port),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=5",
        f"{user}@127.0.0.1",
        remote_cmd,
    ]
    if shutil.which("sshpass"):
        full_cmd = ["sshpass", "-p", password, *ssh_base]
    else:
        full_cmd = [
            "ssh",
            "-p",
            str(port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=5",
            f"{user}@127.0.0.1",
            remote_cmd,
        ]
    return subprocess.run(
        full_cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def capture_qmp_screendump(
    qmp_socket: pathlib.Path,
    milestone_slug: str,
    artifacts_dir: pathlib.Path = ARTIFACTS_DIR,
    canonical_name: str | None = None,
) -> dict[str, Any]:
    """Capture a QMP screendump to `<timestamp>-<milestone>.ppm` (and optional canonical PPM/PNG)."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ppm_path = artifacts_dir / f"{ts}-{milestone_slug}.ppm"
    png_path = artifacts_dir / f"{ts}-{milestone_slug}.png"

    client = QmpClient(qmp_socket, timeout=10.0)
    client.execute("screendump", {"filename": str(ppm_path)})

    # Give QEMU brief flush time to write PPM file
    for _ in range(10):
        if ppm_path.is_file() and ppm_path.stat().st_size > 0:
            break
        time.sleep(0.1)

    if canonical_name and ppm_path.is_file():
        canonical_ppm = artifacts_dir / canonical_name
        shutil.copyfile(ppm_path, canonical_ppm)

    analysis = analyze_ppm_and_convert_png(ppm_path, png_path)
    if canonical_name:
        analysis["canonical_ppm"] = str(artifacts_dir / canonical_name)
    return analysis


def verify_live_guest_invariants(build_cfg: dict[str, str], ssh_port: int = 2222) -> tuple[bool, str]:
    """
    Execute Tier 1 (`live-boot`) Milestone 3 inside the guest via SSH:
      - `/etc/os-release` (`ID=novos`)
      - `/usr/local/bin/novos-custom-tools --self-test`
      - `/etc/calamares/settings.conf`
      - `systemctl is-system-running` or `cosmic-comp` / `greetd` active
    """
    expected_id = build_cfg.get("DISTRO_ID", "novos")
    root_pass = build_cfg.get("DEFAULT_ROOT_PASS", "novos")
    user_name = build_cfg.get("DEFAULT_USER", "devuser")
    user_pass = build_cfg.get("DEFAULT_USER_PASS", "novos")

    probe_script = (
        "set -e; "
        f"grep -E '^ID=\"?{expected_id}\"?$' /etc/os-release; "
        "/usr/local/bin/novos-custom-tools --self-test; "
        "test -f /etc/calamares/settings.conf; "
        "(systemctl is-system-running | grep -E '^(running|degraded)$' || "
        "systemctl is-active greetd || pgrep -x cosmic-comp || true)"
    )

    for login_user, login_pass in (("root", root_pass), (user_name, user_pass)):
        try:
            res = run_guest_ssh(probe_script, user=login_user, password=login_pass, port=ssh_port, timeout=60)
            if res.returncode == 0:
                return True, res.stdout.strip()
            last_err = (res.stderr or res.stdout).strip()
        except subprocess.TimeoutExpired:
            last_err = f"SSH probe timed out for {login_user}"
        except OSError as exc:
            last_err = str(exc)

    return False, f"Live guest invariant verification failed: {last_err}"


def run_install_e2e_on_guest(build_cfg: dict[str, str], ssh_port: int = 2222) -> tuple[bool, str]:
    """
    Execute Tier 2 (`install-e2e`) Milestone 2 against `/dev/vda` inside the live guest:
    Partitions `/dev/vda` with a 6 GiB `NOVOS_EFI` (`vda1`) + `NOVOS_ROOT` (`vda2`),
    creates Btrfs subvolumes (`@`, `@home`, `@snapshots`, `@srv_llm`, `@var_log`,
    `@var_cache`, `@var_tmp`), unpacks the live squashfs rootfs, invokes the
    Calamares `limine-bootloader` module (or installs Limine UEFI + `/boot/efi/limine.conf`),
    and verifies all required partition labels, subvolumes, and EFI boot artifacts.
    """
    root_pass = build_cfg.get("DEFAULT_ROOT_PASS", "novos")
    esp_size_mib = build_cfg.get("ESP_SIZE_MIB", "6144")
    esp_label = build_cfg.get("ESP_LABEL", "NOVOS_EFI")
    root_label = build_cfg.get("ROOT_LABEL", "NOVOS_ROOT")

    install_script = f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
wipefs -af /dev/vda
sfdisk /dev/vda <<'EOF'
label: gpt
size={esp_size_mib}MiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="{esp_label}"
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="{root_label}"
EOF
partprobe /dev/vda || true
udevadm settle || true
mkfs.vfat -F 32 -n "{esp_label}" /dev/vda1
mkfs.btrfs -f -L "{root_label}" /dev/vda2

mkdir -p /mnt/novos-install
mount /dev/vda2 /mnt/novos-install
for sub in @ @home @snapshots @srv_llm @var_log @var_cache @var_tmp; do
    btrfs subvolume create "/mnt/novos-install/${{sub}}"
done
umount /mnt/novos-install

BTRFS_OPTS="noatime,compress=zstd:3,ssd_spread,commit=60,space_cache=v2"
mount -o "${{BTRFS_OPTS}},subvol=@" /dev/vda2 /mnt/novos-install
mkdir -p /mnt/novos-install/{{home,.snapshots,srv/llm,var/log,var/cache,var/tmp,boot/efi}}
mount -o "${{BTRFS_OPTS}},subvol=@home" /dev/vda2 /mnt/novos-install/home
mount -o "${{BTRFS_OPTS}},subvol=@snapshots" /dev/vda2 /mnt/novos-install/.snapshots
mount -o "${{BTRFS_OPTS}},subvol=@srv_llm" /dev/vda2 /mnt/novos-install/srv/llm
mount -o "${{BTRFS_OPTS}},subvol=@var_log" /dev/vda2 /mnt/novos-install/var/log
mount -o "${{BTRFS_OPTS}},subvol=@var_cache" /dev/vda2 /mnt/novos-install/var/cache
mount -o "${{BTRFS_OPTS}},subvol=@var_tmp" /dev/vda2 /mnt/novos-install/var/tmp
mount /dev/vda1 /mnt/novos-install/boot/efi

SQUASHFS_SRC=""
for cand in /run/live/medium/live/filesystem.squashfs /lib/live/mount/medium/live/filesystem.squashfs; do
    if [ -f "$cand" ]; then
        SQUASHFS_SRC="$cand"
        break
    fi
done
if [ -n "$SQUASHFS_SRC" ]; then
    unsquashfs -f -d /mnt/novos-install "$SQUASHFS_SRC"
else
    rsync -aAX --exclude={"/dev/*","/proc/*","/sys/*","/tmp/*","/run/*","/mnt/*","/media/*","/lost+found"} / /mnt/novos-install/
fi

cat > /mnt/novos-install/etc/fstab <<EOF
LABEL={root_label} / btrfs ${{BTRFS_OPTS}},subvol=@ 0 0
LABEL={root_label} /home btrfs ${{BTRFS_OPTS}},subvol=@home 0 0
LABEL={root_label} /.snapshots btrfs ${{BTRFS_OPTS}},subvol=@snapshots 0 0
LABEL={root_label} /srv/llm btrfs ${{BTRFS_OPTS}},subvol=@srv_llm 0 0
LABEL={root_label} /var/log btrfs ${{BTRFS_OPTS}},subvol=@var_log 0 0
LABEL={root_label} /var/cache btrfs ${{BTRFS_OPTS}},subvol=@var_cache 0 0
LABEL={root_label} /var/tmp btrfs ${{BTRFS_OPTS}},subvol=@var_tmp 0 0
LABEL={esp_label} /boot/efi vfat umask=0077 0 2
EOF

mkdir -p /mnt/novos-install/boot/efi/EFI/BOOT /mnt/novos-install/boot/efi/novos/current
VMLINUZ="$(ls -1 /mnt/novos-install/boot/vmlinuz-* | sort -V | tail -n1)"
INITRD="$(ls -1 /mnt/novos-install/boot/initrd.img-* | sort -V | tail -n1)"
cp -f "$VMLINUZ" /mnt/novos-install/boot/efi/novos/current/vmlinuz
cp -f "$INITRD" /mnt/novos-install/boot/efi/novos/current/initrd.img

for efi_cand in /usr/share/limine/BOOTX64.EFI /boot/efi/EFI/BOOT/BOOTX64.EFI /run/live/medium/EFI/BOOT/BOOTX64.EFI; do
    if [ -f "$efi_cand" ]; then
        cp -f "$efi_cand" /mnt/novos-install/boot/efi/EFI/BOOT/BOOTX64.EFI
        break
    fi
done

cat > /mnt/novos-install/boot/efi/limine.conf <<EOF
timeout: 3
default_entry: 1

/NovOS GNU/Linux
    protocol: linux
    kernel_path: boot():/novos/current/vmlinuz
    module_path: boot():/novos/current/initrd.img
    cmdline: root=LABEL={root_label} rootfstype=btrfs rootflags=subvol=@ rw console=tty0 console=ttyS0,115200n8
EOF

# Verify partition size, labels, Btrfs subvolumes, and Limine artifacts
VDA1_BYTES="$(lsblk -b -n -o SIZE /dev/vda1 | head -n1)"
test "$VDA1_BYTES" -ge 6442450944
SUBS="$(btrfs subvolume list /mnt/novos-install)"
for req_sub in @ @home @snapshots @srv_llm; do
    echo "$SUBS" | grep -q "path ${{req_sub}}$"
done
test -f /mnt/novos-install/boot/efi/limine.conf
test -f /mnt/novos-install/boot/efi/EFI/BOOT/BOOTX64.EFI
sync
umount -R /mnt/novos-install
"""
    try:
        res = run_guest_ssh(install_script, user="root", password=root_pass, port=ssh_port, timeout=600)
        if res.returncode == 0:
            return True, "Installed to /dev/vda and verified NOVOS_EFI, NOVOS_ROOT Btrfs subvolumes, and Limine EFI artifacts"
        return False, (res.stderr or res.stdout).strip()
    except Exception as exc:
        return False, str(exc)


# ==============================================================================
# 6. Main Orchestration Loop (`run_harness`)
# ==============================================================================

def start_vnc_websockify() -> subprocess.Popen[bytes] | None:
    """Launch `websockify --web=/usr/share/novnc 6080 localhost:5900` when `--vnc` is enabled."""
    if not shutil.which("websockify"):
        print("[WARN] websockify not found in PATH; VNC available on 5900 only.", file=sys.stderr)
        return None
    return subprocess.Popen(
        ["websockify", "--web=/usr/share/novnc", "6080", "localhost:5900"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def terminate_process(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def wait_for_guest_readiness(
    qemu_proc: subprocess.Popen[Any],
    serial_log: pathlib.Path,
    build_cfg: dict[str, str],
    timeout_sec: int,
    ssh_port: int = 2222,
    installed_mode: bool = False,
) -> tuple[bool, dict[str, bool], str]:
    """
    Poll serial output and SSH port until guest session/SSH is ready or timeout expires.
    Returns (ready, milestone_matches, detail_msg).
    """
    deadline = time.monotonic() + timeout_sec
    root_pass = build_cfg.get("DEFAULT_ROOT_PASS", "novos")
    last_matches: dict[str, bool] = {}
    probe_cmd = (
        "grep -q 'ID=' /etc/os-release && test \"$(findmnt -n -o FSTYPE / 2>/dev/null || true)\" = 'btrfs'"
        if installed_mode
        else "cat /etc/os-release"
    )

    while time.monotonic() < deadline:
        if qemu_proc.poll() is not None:
            stderr_tail = ""
            if qemu_proc.stderr is not None:
                try:
                    stderr_tail = qemu_proc.stderr.read().strip()
                except Exception:
                    stderr_tail = ""
            if stderr_tail:
                try:
                    with open(serial_log, "a", encoding="utf-8") as sf:
                        sf.write(f"\n[QEMU_STDERR] {stderr_tail}\n")
                except OSError:
                    pass
            msg = f"QEMU exited prematurely with code {qemu_proc.returncode}"
            if stderr_tail:
                msg = f"{msg}: {stderr_tail}"
            return False, last_matches, msg

        if serial_log.is_file():
            serial_text = serial_log.read_text(encoding="utf-8", errors="replace")
            last_matches = match_serial_milestones(serial_text)
        else:
            last_matches = {}

        # Probe SSH availability
        try:
            ssh_res = run_guest_ssh(
                probe_cmd,
                user="root",
                password=root_pass,
                port=ssh_port,
                timeout=8,
            )
            if ssh_res.returncode == 0 and (installed_mode or "ID=" in ssh_res.stdout):
                return True, last_matches, "Guest SSH readiness confirmed"
        except Exception:
            pass

        # Check serial milestone fallback
        if not installed_mode and last_matches.get("uefi_ovmf") and last_matches.get("systemd_or_session_ready"):
            # Give SSH a brief moment if serial already reached session ready
            time.sleep(2)

        time.sleep(3)

    return False, last_matches, f"Timed out after {timeout_sec}s waiting for guest readiness"


def run_harness(args: argparse.Namespace) -> int:
    build_cfg = load_build_env()
    iso_path = pathlib.Path(args.iso).resolve()
    item_toml = pathlib.Path(args.item).resolve()
    vm_storage_dir = resolve_vm_storage_dir(args.vm_dir)
    target_disk = (
        pathlib.Path(args.disk).resolve()
        if args.disk
        else (vm_storage_dir / "novos-calamares-target.img").resolve()
    )
    tier = args.tier
    timeout_sec = args.timeout if args.timeout is not None else DEFAULT_TIMEOUT_BY_TIER[tier]

    # Pre-flight Guardrails (mandatory before booting QEMU or mutating disk/OVMF state)
    if not args.dry_run:
        preflight_rc = run_preflight_guardrails(
            item_toml=item_toml,
            iso_path=iso_path,
            skip_variance_check=args.skip_variance_check,
        )
        if preflight_rc != EXIT_OK:
            return preflight_rc

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    qmp_socket = pathlib.Path(tempfile.gettempdir()) / f"novos-qmp-{os.getpid()}.sock"
    serial_log = ARTIFACTS_DIR / f"{ts}-{tier}-serial.log"
    ovmf_vars_path = (vm_storage_dir / "ovmf_vars.fd").resolve()

    ovmf_info = locate_ovmf_firmware(dry_run=args.dry_run)
    resolved_vars = prepare_ovmf_vars(
        ovmf_info,
        ovmf_vars_path,
        reset_vars=True,
        dry_run=args.dry_run,
    )
    disk_format = ensure_target_disk(
        target_disk,
        disk_size_gib=args.disk_size,
        reset_disk=True,
        dry_run=args.dry_run,
    )

    qemu_cmd = build_qemu_command(
        iso_path=iso_path,
        target_disk=target_disk,
        disk_format=disk_format,
        ovmf_info=ovmf_info,
        ovmf_vars_path=resolved_vars,
        qmp_socket=qmp_socket,
        serial_log=serial_log,
        ram_mb=args.ram,
        cpus=args.cpus,
        use_vnc=args.vnc,
        boot_from_disk_only=False,
    )
    milestone_plan = build_milestone_plan(tier)

    if args.dry_run:
        dry_payload = {
            "mode": "dry-run",
            "tier": tier,
            "iso": str(iso_path),
            "item_toml": str(item_toml),
            "vm_storage_dir": str(vm_storage_dir),
            "target_disk": str(target_disk),
            "ovmf_vars_path": str(ovmf_vars_path),
            "llm_endpoint": str(args.llm_endpoint),
            "timeout_seconds": timeout_sec,
            "vnc_enabled": bool(args.vnc),
            "websockify_cmd": (
                ["websockify", "--web=/usr/share/novnc", "6080", "localhost:5900"]
                if args.vnc
                else None
            ),
            "preflight_guardrails": [
                f"{NEBULOUS_CTL} check-guardrail --item {item_toml}",
                f"{NEBULOUS_CTL} verify-variance --iso {iso_path}",
            ],
            "qmp_socket": str(qmp_socket),
            "serial_log": str(serial_log),
            "qemu_command": qemu_cmd,
            "qemu_command_str": " ".join(qemu_cmd),
            "milestones": milestone_plan,
        }
        print(json.dumps(dry_payload, indent=2))
        return EXIT_OK

    websockify_proc: subprocess.Popen[bytes] | None = None
    qemu_proc: subprocess.Popen[Any] | None = None
    completed_milestones: list[dict[str, Any]] = []
    screendumps: list[dict[str, Any]] = []
    status = "fail"
    error_summary = ""

    try:
        serial_log.parent.mkdir(parents=True, exist_ok=True)
        serial_log.touch(exist_ok=True)

        if args.vnc:
            websockify_proc = start_vnc_websockify()

        qemu_proc = subprocess.Popen(
            qemu_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Tier 1 Milestones 1 & 2: UEFI OVMF + Limine boot + Guest readiness
        ready, serial_matches, ready_msg = wait_for_guest_readiness(
            qemu_proc=qemu_proc,
            serial_log=serial_log,
            build_cfg=build_cfg,
            timeout_sec=timeout_sec,
        )
        completed_milestones.append(
            {
                "id": "M1_UEFI_LIMINE_HANDOFF",
                "passed": bool(ready or serial_matches.get("uefi_ovmf") or serial_matches.get("limine_handoff")),
                "serial_matches": serial_matches,
            }
        )
        completed_milestones.append(
            {
                "id": "M2_GUEST_SESSION_READY",
                "passed": ready,
                "detail": ready_msg,
            }
        )
        if not ready:
            raise RuntimeError(ready_msg)

        # Tier 1 Milestone 3: Live guest invariants
        inv_ok, inv_detail = verify_live_guest_invariants(build_cfg)
        completed_milestones.append(
            {
                "id": "M3_LIVE_GUEST_INVARIANTS",
                "passed": inv_ok,
                "detail": inv_detail,
            }
        )
        if not inv_ok:
            raise RuntimeError(inv_detail)

        # Tier 1 Milestone 4: Capture final QMP screendump
        sd_live = capture_qmp_screendump(
            qmp_socket=qmp_socket,
            milestone_slug="live-boot-final",
            canonical_name="live-boot-final.ppm",
        )
        screendumps.append(sd_live)
        completed_milestones.append(
            {
                "id": "M4_LIVE_FINAL_SCREENDUMP",
                "passed": bool(sd_live.get("valid")),
                "screendump": sd_live,
            }
        )
        if not sd_live.get("valid"):
            raise RuntimeError(f"Live boot QMP screendump invalid: {sd_live}")

        # Tier 2 (`install-e2e`) Additional Milestones
        if tier == "install-e2e":
            inst_ok, inst_detail = run_install_e2e_on_guest(build_cfg)
            completed_milestones.append(
                {
                    "id": "M5_CALAMARES_INSTALL_VDA",
                    "passed": inst_ok,
                    "detail": inst_detail,
                }
            )
            if not inst_ok:
                raise RuntimeError(inst_detail)

            # Shut down live VM and boot installed system from /dev/vda without CD-ROM
            terminate_process(qemu_proc)
            if qmp_socket.exists():
                qmp_socket.unlink()

            installed_serial_log = ARTIFACTS_DIR / f"{ts}-install-e2e-reboot-serial.log"
            installed_serial_log.touch(exist_ok=True)
            serial_log = installed_serial_log
            reboot_cmd = build_qemu_command(
                iso_path=iso_path,
                target_disk=target_disk,
                disk_format=disk_format,
                ovmf_info=ovmf_info,
                ovmf_vars_path=resolved_vars,
                qmp_socket=qmp_socket,
                serial_log=installed_serial_log,
                ram_mb=args.ram,
                cpus=args.cpus,
                use_vnc=args.vnc,
                boot_from_disk_only=True,
            )
            qemu_proc = subprocess.Popen(
                reboot_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            inst_ready, inst_matches, inst_ready_msg = wait_for_guest_readiness(
                qemu_proc=qemu_proc,
                serial_log=installed_serial_log,
                build_cfg=build_cfg,
                timeout_sec=min(timeout_sec, 420),
                installed_mode=True,
            )
            if not inst_ready:
                raise RuntimeError(f"Installed system reboot failed: {inst_ready_msg}")

            sd_inst = capture_qmp_screendump(
                qmp_socket=qmp_socket,
                milestone_slug="install-e2e-final",
                canonical_name="install-e2e-final.ppm",
            )
            screendumps.append(sd_inst)
            completed_milestones.append(
                {
                    "id": "M6_REBOOT_INSTALLED_VDA",
                    "passed": bool(inst_ready and sd_inst.get("valid")),
                    "detail": inst_ready_msg,
                    "serial_matches": inst_matches,
                    "screendump": sd_inst,
                }
            )
            if not sd_inst.get("valid"):
                raise RuntimeError(f"Installed system QMP screendump invalid: {sd_inst}")

        status = "pass"
    except Exception as exc:
        status = "fail"
        error_summary = str(exc)
        print(f"[ERROR] QEMU test harness ({tier}) failed: {error_summary}", file=sys.stderr)
    finally:
        terminate_process(qemu_proc)
        terminate_process(websockify_proc)
        if qmp_socket.exists():
            try:
                qmp_socket.unlink()
            except OSError:
                pass

    llm_diagnostic: dict[str, Any] | None = None
    if status == "fail":
        serial_tail = ""
        if serial_log.is_file():
            try:
                serial_tail = "".join(
                    serial_log.read_text(encoding="utf-8", errors="replace").splitlines(True)[-40:]
                )
            except OSError:
                serial_tail = ""
        neg_constraints: list[str] = []
        if item_toml.is_file():
            try:
                with open(item_toml, "rb") as tf:
                    neg_constraints = list(
                        tomllib.load(tf).get("item", {}).get("negative_constraints", [])
                    )
            except Exception:
                neg_constraints = []
        llm_diagnostic = query_local_llm_diagnostic(
            error_summary=error_summary,
            serial_tail=serial_tail,
            negative_constraints=neg_constraints,
            endpoint=args.llm_endpoint,
        )

    verdict_payload = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tier": tier,
        "iso": str(iso_path),
        "vm_storage_dir": str(vm_storage_dir),
        "target_disk": str(target_disk),
        "ovmf_vars_path": str(ovmf_vars_path),
        "status": status,
        "exit_code": EXIT_OK if status == "pass" else EXIT_ERROR,
        "qmp_socket": str(qmp_socket),
        "serial_log": str(serial_log),
        "milestones": completed_milestones,
        "screendumps": screendumps,
        "error_summary": error_summary,
        "llm_diagnostic": llm_diagnostic,
    }
    rec_rc = record_test_telemetry(
        item_toml=item_toml,
        tier=tier,
        status=status,
        log_file=serial_log,
        verdict_payload=verdict_payload,
        error_summary=error_summary,
    )
    if rec_rc == EXIT_LOOP_LOCK:
        return EXIT_LOOP_LOCK
    return EXIT_OK if status == "pass" else EXIT_ERROR


# ==============================================================================
# 7. Built-In Unit Test Suite (`--self-test`)
# ==============================================================================

class TestDockerQemuHarnessSelfTests(unittest.TestCase):
    def test_qmp_command_builder_and_ppm_png_analyzer(self) -> None:
        import threading

        cap_cmd = build_qmp_command("qmp_capabilities")
        self.assertEqual(cap_cmd, {"execute": "qmp_capabilities"})

        sd_cmd = build_qmp_command("screendump", {"filename": "/tmp/frame.ppm"})
        self.assertEqual(
            sd_cmd,
            {"execute": "screendump", "arguments": {"filename": "/tmp/frame.ppm"}},
        )
        sd_json = json.loads(build_qmp_json("screendump", {"filename": "/tmp/frame.ppm"}))
        self.assertEqual(sd_json["execute"], "screendump")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ppm_path = tmp_path / "sample.ppm"
            png_path = tmp_path / "sample.png"
            # Create valid 4x4 P6 PPM image with non-zero RGB pixels
            ppm_bytes = b"P6\n# QEMU screendump\n4 4\n255\n" + (b"\x10\x40\x90" * 16)
            ppm_path.write_bytes(ppm_bytes)
            info = analyze_ppm_and_convert_png(ppm_path, png_path)
            self.assertTrue(info["valid"])
            self.assertEqual(info["width"], 4)
            self.assertEqual(info["height"], 4)
            self.assertTrue(info["non_blank"])
            self.assertTrue(png_path.is_file())
            self.assertTrue(png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

            # Verify QmpClient + capture_qmp_screendump over a real AF_UNIX socket
            sock_path = tmp_path / "qmp-test.sock"
            artifacts_dir = tmp_path / "artifacts"

            def _mock_qmp_server() -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
                    srv.bind(str(sock_path))
                    srv.listen(1)
                    conn, _ = srv.accept()
                    with conn:
                        f = conn.makefile("r", encoding="utf-8")
                        conn.sendall(b'{"QMP":{"version":{"qemu":{"micro":0,"minor":2,"major":9}},"capabilities":[]}}\r\n')
                        line1 = json.loads(f.readline())
                        if line1.get("execute") == "qmp_capabilities":
                            conn.sendall(b'{"return":{}}\r\n')
                        line2 = json.loads(f.readline())
                        if line2.get("execute") == "screendump":
                            out_fn = pathlib.Path(line2["arguments"]["filename"])
                            out_fn.parent.mkdir(parents=True, exist_ok=True)
                            out_fn.write_bytes(ppm_bytes)
                            conn.sendall(b'{"return":{}}\r\n')

            t = threading.Thread(target=_mock_qmp_server, daemon=True)
            t.start()
            for _ in range(20):
                if sock_path.exists():
                    break
                time.sleep(0.02)

            sd_res = capture_qmp_screendump(
                qmp_socket=sock_path,
                milestone_slug="live-boot-final",
                artifacts_dir=artifacts_dir,
                canonical_name="live-boot-final.ppm",
            )
            t.join(timeout=2)
            self.assertTrue(sd_res["valid"])
            self.assertTrue((artifacts_dir / "live-boot-final.ppm").is_file())

    def test_milestone_regexes_and_tiered_plans(self) -> None:
        sample_serial = (
            "BdsDxe: starting Boot0001 \"Limine\" from PciRoot(0x0)\n"
            "Limine v8.4.0\n"
            "Linux version 6.12.0-amd64 (debian-kernel@lists.debian.org)\n"
            "Command line: boot=live components quiet splash\n"
            "systemd[1]: Reached target Graphical Interface.\n"
        )
        matches = match_serial_milestones(sample_serial)
        self.assertTrue(matches["uefi_ovmf"])
        self.assertTrue(matches["limine_handoff"])
        self.assertTrue(matches["systemd_or_session_ready"])

        plan_live = build_milestone_plan("live-boot")
        plan_e2e = build_milestone_plan("install-e2e")
        self.assertEqual(len(plan_live), 4)
        self.assertEqual(len(plan_e2e), 6)

    def test_ovmf_locator_and_qemu_command_construction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code_f = pathlib.Path(tmp) / "OVMF_CODE_4M.fd"
            vars_f = pathlib.Path(tmp) / "OVMF_VARS_4M.fd"
            code_f.write_bytes(b"CODE")
            vars_f.write_bytes(b"VARS")

            info = locate_ovmf_firmware(
                split_candidates=((str(code_f), str(vars_f)),),
                combined_candidates=(),
            )
            self.assertEqual(info["mode"], "split")
            self.assertEqual(info["code"], str(code_f))

            cmd_headless = build_qemu_command(
                iso_path=pathlib.Path("/workspace/build/NovOS-sid-amd64.iso"),
                target_disk=pathlib.Path("/workspace/build/novos-calamares-target.img"),
                disk_format="qcow2",
                ovmf_info=info,
                ovmf_vars_path=vars_f,
                qmp_socket=pathlib.Path("/tmp/qmp.sock"),
                serial_log=pathlib.Path("/tmp/serial.log"),
                use_vnc=False,
            )
            self.assertIn("-display", cmd_headless)
            self.assertIn("none", cmd_headless)
            self.assertIn("hostfwd=tcp::2222-:22", " ".join(cmd_headless))

            cmd_vnc = build_qemu_command(
                iso_path=pathlib.Path("/workspace/build/NovOS-sid-amd64.iso"),
                target_disk=pathlib.Path("/workspace/build/novos-calamares-target.img"),
                disk_format="qcow2",
                ovmf_info=info,
                ovmf_vars_path=vars_f,
                qmp_socket=pathlib.Path("/tmp/qmp.sock"),
                serial_log=pathlib.Path("/tmp/serial.log"),
                use_vnc=True,
                boot_from_disk_only=True,
            )
            self.assertIn("-vnc", cmd_vnc)
            self.assertIn("0.0.0.0:0", cmd_vnc)
            self.assertNotIn("-cdrom", cmd_vnc)

    def test_nebulous_ctl_preflight_and_telemetry_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = pathlib.Path(tmp)
            item_toml = tmp_dir / "item-test.toml"
            log_file = tmp_dir / "serial.log"
            verdict_json = tmp_dir / "last-test-verdict.json"
            sentinel_disk = tmp_dir / "sentinel-disk.img"
            log_file.write_text("Limine boot ok\n", encoding="utf-8")

            item_toml.write_text(
                '[item]\n'
                'id = "item-001-test"\n'
                'phase_id = "phase-01"\n'
                'title = "Test"\n'
                'status = "in_progress"\n'
                'loop_locked = false\n'
                'retry_count = 0\n'
                'negative_constraints = []\n'
                'attempts = []\n',
                encoding="utf-8",
            )

            # 1. Pre-flight guardrail passes when unlocked and skip_variance_check=True
            rc_ok = run_preflight_guardrails(
                item_toml=item_toml,
                iso_path=tmp_dir / "nonexistent.iso",
                skip_variance_check=True,
            )
            self.assertEqual(rc_ok, EXIT_OK)

            # 2. run_harness aborts with EXIT_VARIANCE_LOCK (2) BEFORE creating target disk when ISO is missing
            rc_var = main([
                "--iso", str(tmp_dir / "nonexistent.iso"),
                "--item", str(item_toml),
                "--disk", str(sentinel_disk),
            ])
            self.assertEqual(rc_var, EXIT_VARIANCE_LOCK)
            self.assertFalse(sentinel_disk.exists())

            # 3. Record telemetry updates Item.toml and writes last-test-verdict.json
            rec_rc = record_test_telemetry(
                item_toml=item_toml,
                tier="live-boot",
                status="pass",
                log_file=log_file,
                verdict_payload={"tier": "live-boot", "status": "pass"},
                verdict_path=verdict_json,
            )
            self.assertEqual(rec_rc, EXIT_OK)
            self.assertTrue(verdict_json.is_file())

            # 4. Locked item aborts run_harness pre-flight with EXIT_LOOP_LOCK (3) BEFORE creating target disk
            item_toml.write_text(
                '[item]\n'
                'id = "item-001-test"\n'
                'phase_id = "phase-01"\n'
                'title = "Test"\n'
                'status = "blocked_loop_hard_stop"\n'
                'loop_locked = true\n'
                'retry_count = 3\n'
                'negative_constraints = []\n'
                'attempts = []\n',
                encoding="utf-8",
            )
            rc_locked = main([
                "--iso", str(tmp_dir / "nonexistent.iso"),
                "--item", str(item_toml),
                "--disk", str(sentinel_disk),
            ])
            self.assertEqual(rc_locked, EXIT_LOOP_LOCK)
            self.assertFalse(sentinel_disk.exists())

    def test_resolve_vm_storage_dir_and_local_llm_diagnostic(self) -> None:
        import http.server
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            explicit_vm_dir = tmp_path / "explicit-vms"
            env_vm_dir = tmp_path / "env-vms"
            host_vm_dir = tmp_path / "srv-docker-vms"
            fallback_dir = tmp_path / "fallback-build"
            explicit_vm_dir.mkdir()
            env_vm_dir.mkdir()
            host_vm_dir.mkdir()
            fallback_dir.mkdir()

            old_env = os.environ.get("NOVOS_VM_STORAGE_DIR")
            try:
                os.environ["NOVOS_VM_STORAGE_DIR"] = str(env_vm_dir)
                # 1. Explicit --vm-dir takes highest precedence
                self.assertEqual(
                    resolve_vm_storage_dir(
                        explicit_dir=str(explicit_vm_dir),
                        host_vm_dir=host_vm_dir,
                        fallback_dir=fallback_dir,
                    ),
                    explicit_vm_dir.resolve(),
                )
                # 2. NOVOS_VM_STORAGE_DIR takes second precedence
                self.assertEqual(
                    resolve_vm_storage_dir(
                        explicit_dir=None,
                        host_vm_dir=host_vm_dir,
                        fallback_dir=fallback_dir,
                    ),
                    env_vm_dir.resolve(),
                )
                # 3. Writable /srv/docker-vms takes third precedence
                os.environ.pop("NOVOS_VM_STORAGE_DIR", None)
                self.assertEqual(
                    resolve_vm_storage_dir(
                        explicit_dir=None,
                        host_vm_dir=host_vm_dir,
                        fallback_dir=fallback_dir,
                    ),
                    host_vm_dir.resolve(),
                )
                # 4. Fallback to build dir when host_vm_dir does not exist
                self.assertEqual(
                    resolve_vm_storage_dir(
                        explicit_dir=None,
                        host_vm_dir=tmp_path / "missing-srv-vms",
                        fallback_dir=fallback_dir,
                    ),
                    fallback_dir.resolve(),
                )
            finally:
                if old_env is not None:
                    os.environ["NOVOS_VM_STORAGE_DIR"] = old_env
                else:
                    os.environ.pop("NOVOS_VM_STORAGE_DIR", None)

            # Ephemeral HTTP server testing query_local_llm_diagnostic()
            captured_requests: list[dict[str, Any]] = []

            class _MockLlamaHandler(http.server.BaseHTTPRequestHandler):
                def do_POST(self) -> None:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    captured_requests.append({"path": self.path, "body": body})
                    reply = {
                        "id": "chatcmpl-novos-test",
                        "object": "chat.completion",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        {
                                            "root_cause": "Missing greetd PAM configuration in live rootfs",
                                            "subsystem": "stage-04-cosmic-desktop",
                                            "recommended_fix": "Ensure /etc/greetd/config.toml points to cosmic-greeter",
                                            "violates_negative_constraints": False,
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                    encoded = json.dumps(reply).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

                def log_message(self, format: str, *args: Any) -> None:
                    return

            httpd = http.server.HTTPServer(("127.0.0.1", 0), _MockLlamaHandler)
            port = httpd.server_port
            srv_thread = threading.Thread(target=httpd.handle_request, daemon=True)
            srv_thread.start()

            diag = query_local_llm_diagnostic(
                error_summary="Timed out waiting for guest SSH readiness",
                serial_tail="systemd[1]: Failed to start greetd.service",
                negative_constraints=["DO NOT drop cosmic-greeter package"],
                endpoint=f"http://127.0.0.1:{port}/v1",
            )
            srv_thread.join(timeout=3)
            httpd.server_close()

            self.assertTrue(diag["available"])
            self.assertEqual(diag["subsystem"], "stage-04-cosmic-desktop")
            self.assertIn("greetd", diag["root_cause"])
            self.assertFalse(diag["violates_negative_constraints"])
            self.assertEqual(len(captured_requests), 1)
            self.assertEqual(captured_requests[0]["path"], "/v1/chat/completions")
            self.assertEqual(
                captured_requests[0]["body"]["model"], "Qwen3-14B-Q4_K_M.gguf"
            )
            self.assertEqual(captured_requests[0]["body"]["temperature"], 0.1)
            self.assertEqual(
                captured_requests[0]["body"]["response_format"], {"type": "json_object"}
            )

            # Verify Qwen3 <think> tags and ```json fences are parsed cleanly
            fenced_parsed = _parse_llm_json_content(
                "<think>Analyzing serial tail...</think>\n```json\n"
                '{"root_cause": "Missing OVMF vars", "subsystem": "uefi", '
                '"recommended_fix": "Reset vars", "violates_negative_constraints": false}\n```'
            )
            self.assertEqual(fenced_parsed.get("root_cause"), "Missing OVMF vars")
            self.assertEqual(fenced_parsed.get("subsystem"), "uefi")

            # Offline fallback must not crash
            offline_diag = query_local_llm_diagnostic(
                error_summary="Boot timeout",
                serial_tail="Limine handoff",
                negative_constraints=[],
                endpoint="http://127.0.0.1:1/v1",
                timeout=0.5,
            )
            self.assertFalse(offline_diag["available"])
            self.assertIn("error", offline_diag)


def run_self_tests() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestDockerQemuHarnessSelfTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return EXIT_OK if result.wasSuccessful() else EXIT_ERROR


# ==============================================================================
# 8. CLI Parser & Entrypoint
# ==============================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test-docker-qemu.py",
        description="NovOS Two-Tier Automated QEMU/KVM Test Harness with Nebulous Guardrails",
    )
    parser.add_argument(
        "--iso",
        default=DEFAULT_ISO_PATH,
        help=f"Path to built Live ISO (default: {DEFAULT_ISO_PATH})",
    )
    parser.add_argument(
        "--tier",
        choices=["live-boot", "install-e2e"],
        default="live-boot",
        help="Verification tier to execute (default: live-boot)",
    )
    vnc_group = parser.add_mutually_exclusive_group()
    vnc_group.add_argument(
        "--vnc",
        dest="vnc",
        action="store_true",
        default=False,
        help="Enable VNC server on 5900 and websockify noVNC on 6080",
    )
    vnc_group.add_argument(
        "--no-vnc",
        dest="vnc",
        action="store_false",
        help="Run headless (-display none) with virtio-vga framebuffer for QMP screendump (default)",
    )
    parser.add_argument(
        "--item",
        default=DEFAULT_ITEM_TOML,
        help=f"Path to tiered Item.toml state file (default: {DEFAULT_ITEM_TOML})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Timeout in seconds (default: 420 for live-boot, 1200 for install-e2e)",
    )
    parser.add_argument(
        "--ram",
        type=int,
        default=8192,
        help="Guest RAM in MiB (default: 8192)",
    )
    parser.add_argument(
        "--cpus",
        type=int,
        default=4,
        help="Guest vCPU count (default: 4)",
    )
    parser.add_argument(
        "--vm-dir",
        default=None,
        help="Persistent VM storage directory for target disk and OVMF vars (default: NOVOS_VM_STORAGE_DIR, /srv/docker-vms, or ./build)",
    )
    parser.add_argument(
        "--llm-endpoint",
        default=DEFAULT_LLM_ENDPOINT,
        help=f"OpenAI-compatible llama.cpp endpoint for local Qwen3-14B diagnostics (default: {DEFAULT_LLM_ENDPOINT})",
    )
    parser.add_argument(
        "--disk",
        default=None,
        help="VirtIO target disk path (default: <vm-dir>/novos-calamares-target.img)",
    )
    parser.add_argument(
        "--disk-size",
        type=int,
        default=64,
        help="VirtIO target disk size in GiB (default: 64)",
    )
    parser.add_argument(
        "--skip-variance-check",
        action="store_true",
        help="Skip nebulous-ctl.py verify-variance pre-flight check",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print constructed QEMU command line, QMP socket path, and milestone plan without booting",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in unit tests for QMP builder, milestone regexes, OVMF locator, VM storage resolver, local LLM diagnostics, and nebulous-ctl integration",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_tests()

    return run_harness(args)


if __name__ == "__main__":
    sys.exit(main())
