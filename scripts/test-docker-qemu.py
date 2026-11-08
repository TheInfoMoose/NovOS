#!/usr/bin/env python3
"""
NovOS Two-Tier Automated QEMU/KVM Test Harness (scripts/test-docker-qemu.py)
============================================================================
Executes automated, headless or VNC-enabled UEFI (OVMF) QEMU/KVM verification
across two tiers:
  - Tier 1 (`live-boot`):
      1. UEFI OVMF + Limine boot entry handoff.
      2. Guest agent answers over the `org.qemu.guest_agent.0` virtio-serial channel
         (`systemctl is-system-running` or `cosmic-comp`/`greetd` active).
      3. Verify `/etc/os-release` (`ID=novos`),
         `/usr/local/bin/novos-custom-tools --self-test`,
         and `/etc/calamares/settings.conf` inside the live guest.
      4. Capture final QMP `screendump` to `logs/qemu-artifacts/live-boot-final.ppm`.
  - Tier 2 (`install-e2e`):
      1. Complete all `live-boot` checks.
      2. Reproduce the Calamares exec sequence from `calamares/` against `/dev/vda`, one
         milestone per sub-step: partition (partition.conf ESP + the 10 mount.conf Btrfs
         subvolumes `@` ... `@devshare`), rootfs (unpackfs, machineid, fstab, users),
         netinstall (shellprocess@prepackages + `--groups` packages, default `dev,network`),
         shellprocess (every shellprocess-novos.conf command, `@@username@@` = test user),
         bootloader (the limine-bootloader module), then the harness-only test-agent
         sub-step (qemu-guest-agent .debs from the test overlay into the target), then umount.
      3. Reboot VM from `/dev/vda` (without CD-ROM boot override) and verify
         installed system boots cleanly.

The guest is reached only through the QEMU guest agent (STEP 15G): `--iso` must be the
derived test ISO from `scripts/make-test-overlay-iso.py` (it carries
`/live/zz-novos-test.squashfs` with `qemu-guest-agent`). There is no SSH.

Enforces pre-flight Nebulous guardrails (`check-guardrail` and `verify-variance`)
prior to booting QEMU and records post-run telemetry via `nebulous-ctl.py record-attempt`
plus `logs/qemu-artifacts/last-test-verdict.json`.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import pathlib
import re
import shlex
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

# install-e2e reproduces the Calamares exec sequence from these configs (STEP 15B).
CALAMARES_DIR = REPO_ROOT / "calamares"
PACKAGES_MANIFEST_PATH = REPO_ROOT / "config" / "packages.yaml"
DEFAULT_INSTALL_GROUPS = "dev,network"
INSTALL_E2E_DISK = "/dev/vda"
INSTALL_E2E_TARGET = "/mnt/novos-install"
INSTALL_E2E_TEST_USER = "novostest"
INSTALL_E2E_LOG_EXCERPT_LINES = 40
GPT_TYPE_ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
GPT_TYPE_LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
LIMINE_MODULE_GUEST_PATHS: tuple[str, ...] = (
    "/usr/lib/x86_64-linux-gnu/calamares/modules/limine-bootloader/main.py",
    "/usr/lib/calamares/modules/limine-bootloader/main.py",
)
INSTALL_E2E_REPRODUCED_MODULES: tuple[str, ...] = (
    "partition",
    "mount",
    "unpackfs",
    "machineid",
    "fstab",
    "users",
    "networkcfg",
    "shellprocess@prepackages",
    "packages",
    "shellprocess@novos",
    "limine-bootloader",
    "umount",
)
INSTALL_E2E_SUBSTEP_MILESTONES: dict[str, str] = {
    "partition": "M5_INSTALL_PARTITION",
    "rootfs": "M5_INSTALL_ROOTFS",
    "netinstall": "M5_INSTALL_NETINSTALL",
    "shellprocess": "M5_INSTALL_SHELLPROCESS",
    "bootloader": "M5_INSTALL_BOOTLOADER",
    "test_agent": "M5_INSTALL_TEST_AGENT",
}
# Sub-steps that are harness-only, not part of the Calamares exec sequence.
INSTALL_E2E_HARNESS_ONLY_SUBSTEPS: frozenset[str] = frozenset({"test_agent"})

# QEMU guest agent channel and the STEP 15F test overlay layout (scripts/make-test-overlay-iso.py).
QGA_PORT_NAME = "org.qemu.guest_agent.0"
QGA_POLL_INTERVAL_SEC = 0.5
TEST_OVERLAY_LAYER_ISO_PATH = "/live/zz-novos-test.squashfs"
TEST_OVERLAY_SHARE = "/usr/share/novos-test"
TEST_OVERLAY_MANIFEST = TEST_OVERLAY_SHARE + "/overlay.json"
TEST_OVERLAY_DEBS_DIR = TEST_OVERLAY_SHARE + "/debs"
TEST_OVERLAY_SCRIPT = "scripts/make-test-overlay-iso.py"
LIVE_PROD_SQUASHFS_GUEST_PATH = "/run/live/medium/live/filesystem.squashfs"

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
    qga_socket: pathlib.Path,
    ram_mb: int = 8192,
    cpus: int = 4,
    use_vnc: bool = False,
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
            "-chardev",
            f"socket,id=qga0,path={qga_socket},server=on,wait=off",
            "-device",
            "virtio-serial-pci",
            "-device",
            f"virtserialport,chardev=qga0,name={QGA_PORT_NAME}",
            "-netdev",
            "user,id=net0",
            "-device",
            "virtio-net-pci,netdev=net0",
        ]
    )
    return cmd


def build_milestone_plan(tier: str) -> list[dict[str, Any]]:
    """Return the ordered list of verification milestones for the requested tier."""
    live_boot_milestones: list[dict[str, Any]] = [
        {
            "id": "M1_UEFI_LIMINE_HANDOFF",
            "tier": "live-boot",
            "description": "UEFI OVMF + Limine boot entry handoff",
        },
        {
            "id": "M2_GUEST_SESSION_READY",
            "tier": "live-boot",
            "description": f"Guest agent answers on {QGA_PORT_NAME} (guest-sync-delimited + guest-exec probe of /etc/os-release)",
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
                "id": INSTALL_E2E_SUBSTEP_MILESTONES["partition"],
                "tier": "install-e2e",
                "description": (
                    "Calamares partition + mount on /dev/vda: ESP from partition.conf, Btrfs root with "
                    "the mount.conf subvolumes (@, @home, @snapshots, @var_log, @var_cache, @flatpak, "
                    "@docker, @containerd, @llm, @devshare) mounted"
                ),
            },
            {
                "id": INSTALL_E2E_SUBSTEP_MILESTONES["rootfs"],
                "tier": "install-e2e",
                "description": "unpackfs + extraMounts, machineid, fstab, users (test user), networkcfg",
            },
            {
                "id": INSTALL_E2E_SUBSTEP_MILESTONES["netinstall"],
                "tier": "install-e2e",
                "description": "shellprocess@prepackages, then packages module with the --groups netinstall.yaml groups",
            },
            {
                "id": INSTALL_E2E_SUBSTEP_MILESTONES["shellprocess"],
                "tier": "install-e2e",
                "description": "Every shellprocess-novos.conf command in the target chroot (@@username@@ = test user)",
            },
            {
                "id": INSTALL_E2E_SUBSTEP_MILESTONES["bootloader"],
                "tier": "install-e2e",
                "description": "limine-bootloader Calamares module (ISO copy of main.py)",
            },
            {
                "id": INSTALL_E2E_SUBSTEP_MILESTONES["test_agent"],
                "tier": "install-e2e",
                "harness_only": True,
                "description": (
                    f"Harness-only, not part of Calamares: dpkg -i the {TEST_OVERLAY_DEBS_DIR}/*.deb "
                    "(qemu-guest-agent) in the target chroot after the Golden Master snapshot, "
                    "check qemu-ga in the target, then umount (Calamares umount module)"
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
# 5. Guest Agent Channel & Live / Install E2E Probing Routines
# ==============================================================================

class GuestAgentError(OSError):
    """The QEMU guest agent channel failed (no answer, closed socket, error reply, bad JSON)."""


class GuestAgentClient:
    """
    Minimal QEMU guest agent (qemu-ga) client over the host end of the
    `org.qemu.guest_agent.0` virtio-serial chardev (a QEMU server Unix socket).
    Replies are newline-delimited JSON; `guest-sync-delimited` replies are prefixed by
    the 0xFF sentinel, which is stripped before parsing.
    """

    def __init__(self, socket_path: pathlib.Path) -> None:
        self.socket_path = socket_path
        self.sock: socket.socket | None = None
        self.buf = b""

    def connect(self) -> None:
        self.close()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self.socket_path))
        except OSError:
            sock.close()
            raise
        self.sock = sock
        self.buf = b""

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        self.buf = b""

    def __enter__(self) -> GuestAgentClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _send(self, obj: dict[str, Any]) -> None:
        if self.sock is None:
            raise GuestAgentError("guest agent socket is not connected")
        self.sock.sendall(json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n")

    def _recv(self, timeout: float) -> dict[str, Any]:
        if self.sock is None:
            raise GuestAgentError("guest agent socket is not connected")
        deadline = time.monotonic() + timeout
        while True:
            while b"\n" not in self.buf:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GuestAgentError(f"guest agent reply not complete within {timeout}s")
                self.sock.settimeout(remaining)
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise GuestAgentError("guest agent socket closed")
                self.buf += chunk
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.lstrip(b"\xff").strip()  # guest-sync-delimited sentinel
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError as exc:
                raise GuestAgentError(f"guest agent sent invalid JSON: {line[:200]!r}") from exc
            if not isinstance(msg, dict):
                raise GuestAgentError(f"guest agent sent a non-object reply: {line[:200]!r}")
            return msg

    def sync(self, timeout: float = 5.0) -> None:
        """guest-sync-delimited: flush the agent's parser, then drop stale replies until our token."""
        if self.sock is None:
            raise GuestAgentError("guest agent socket is not connected")
        token = int(time.monotonic() * 1000) % 2**31
        self.sock.sendall(b"\xff")
        self._send({"execute": "guest-sync-delimited", "arguments": {"id": token}})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GuestAgentError(f"guest-sync-delimited: no reply with id {token} within {timeout}s")
            reply = self._recv(remaining)
            if reply.get("return") == token:
                return

    def call(self, command: str, arguments: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        msg: dict[str, Any] = {"execute": command}
        if arguments is not None:
            msg["arguments"] = arguments
        self._send(msg)
        reply = self._recv(timeout)
        if "error" in reply:
            raise GuestAgentError(f"{command}: {reply['error']}")
        if "return" not in reply:
            raise GuestAgentError(f"{command}: reply without 'return': {reply}")
        return reply["return"]


def run_guest_exec(
    cmd: str,
    timeout: float = 30,
    *,
    qga_socket: pathlib.Path,
    sync_timeout: float = 5.0,
) -> subprocess.CompletedProcess[str]:
    """
    Run `/bin/bash -c <cmd>` as root inside the guest through the QEMU guest agent:
    guest-sync-delimited, guest-exec (capture-output), then guest-exec-status every
    QGA_POLL_INTERVAL_SEC until the process exits. Output arrives base64-encoded.
    Raises subprocess.TimeoutExpired when the command outlives `timeout`, and
    GuestAgentError (an OSError) when the channel fails.
    """
    with GuestAgentClient(qga_socket) as agent:
        agent.connect()
        agent.sync(timeout=sync_timeout)
        started = agent.call(
            "guest-exec",
            {"path": "/bin/bash", "arg": ["-c", cmd], "capture-output": True},
            timeout=sync_timeout,
        )
        if not isinstance(started, dict) or "pid" not in started:
            raise GuestAgentError(f"guest-exec: reply without pid: {started}")
        pid = started["pid"]
        deadline = time.monotonic() + timeout
        while True:
            status = agent.call("guest-exec-status", {"pid": pid}, timeout=sync_timeout)
            if not isinstance(status, dict):
                raise GuestAgentError(f"guest-exec-status: unexpected reply: {status}")
            if status.get("exited"):
                break
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd, timeout)
            time.sleep(QGA_POLL_INTERVAL_SEC)

    stdout = base64.b64decode(status.get("out-data", "")).decode("utf-8", errors="replace")
    stderr = base64.b64decode(status.get("err-data", "")).decode("utf-8", errors="replace")
    for stream in ("out", "err"):
        if status.get(f"{stream}-truncated"):
            stderr += f"\n[guest-agent: std{stream} truncated by qemu-ga]"
    if "exitcode" in status:
        returncode = int(status["exitcode"])
    elif "signal" in status:
        returncode = -int(status["signal"])
    else:
        raise GuestAgentError(f"guest-exec-status: exited without exitcode or signal: {status}")
    return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)


def check_test_overlay_iso(iso_path: pathlib.Path) -> tuple[bool, str]:
    """
    Pre-flight (unprivileged xorriso): the --iso must be the STEP 15F derived test ISO,
    i.e. contain TEST_OVERLAY_LAYER_ISO_PATH with qemu-guest-agent; the production ISO has
    no guest channel endpoint and cannot be driven by this harness.
    """
    hint = (
        f"--iso {iso_path} has no {TEST_OVERLAY_LAYER_ISO_PATH}; build the test ISO with "
        f"`python3 {TEST_OVERLAY_SCRIPT} --iso <production ISO>` and pass the derived "
        "*-test.iso to this harness"
    )
    if not iso_path.is_file():
        return False, f"ISO not found: {iso_path}. {hint}"
    if shutil.which("xorriso") is None:
        return False, f"xorriso not found in PATH (needed to check for {TEST_OVERLAY_LAYER_ISO_PATH}). {hint}"
    res = subprocess.run(
        ["xorriso", "-indev", str(iso_path), "-find", TEST_OVERLAY_LAYER_ISO_PATH, "-type", "f"],
        check=False,
        capture_output=True,
        text=True,
    )
    listed = [ln.strip().strip("'") for ln in res.stdout.splitlines()]
    if res.returncode != 0 or TEST_OVERLAY_LAYER_ISO_PATH not in listed:
        detail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        return False, f"{hint} (xorriso exit {res.returncode}: {' | '.join(detail)})"
    return True, f"{TEST_OVERLAY_LAYER_ISO_PATH} present in {iso_path.name}"


def collect_test_overlay_record(qga_socket: pathlib.Path) -> dict[str, Any]:
    """
    Verdict record of the test overlay (read in the live guest): the overlay manifest
    and the sha256 of the production filesystem.squashfs on the boot medium, compared
    with the value the manifest recorded when the test ISO was derived.
    """
    record: dict[str, Any] = {"manifest_path": TEST_OVERLAY_MANIFEST}
    try:
        res = run_guest_exec(f"cat {shlex.quote(TEST_OVERLAY_MANIFEST)}", timeout=30, qga_socket=qga_socket)
        if res.returncode == 0:
            record["manifest"] = json.loads(res.stdout)
        else:
            record["manifest_error"] = f"exit {res.returncode}: {res.stderr.strip()}"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        record["manifest_error"] = str(exc)

    record["production_filesystem_squashfs"] = LIVE_PROD_SQUASHFS_GUEST_PATH
    try:
        res = run_guest_exec(
            f"sha256sum {shlex.quote(LIVE_PROD_SQUASHFS_GUEST_PATH)}", timeout=900, qga_socket=qga_socket
        )
        if res.returncode == 0 and res.stdout.split():
            record["production_filesystem_squashfs_sha256"] = res.stdout.split()[0]
        else:
            record["production_filesystem_squashfs_sha256_error"] = f"exit {res.returncode}: {res.stderr.strip()}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        record["production_filesystem_squashfs_sha256_error"] = str(exc)

    manifest = record.get("manifest")
    measured = record.get("production_filesystem_squashfs_sha256")
    if isinstance(manifest, dict) and measured:
        record["matches_manifest"] = manifest.get("production_filesystem_squashfs_sha256") == measured
    return record


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


def verify_live_guest_invariants(build_cfg: dict[str, str], qga_socket: pathlib.Path) -> tuple[bool, str]:
    """
    Execute Tier 1 (`live-boot`) Milestone 3 inside the guest (as root, via the guest agent):
      - `/etc/os-release` (`ID=novos`)
      - `/usr/local/bin/novos-custom-tools --self-test`
      - `/etc/calamares/settings.conf`
      - `systemctl is-system-running` or `cosmic-comp` / `greetd` active
    """
    expected_id = build_cfg.get("DISTRO_ID", "novos")

    probe_script = (
        "set -e; "
        f"grep -E '^ID=\"?{expected_id}\"?$' /etc/os-release; "
        "/usr/local/bin/novos-custom-tools --self-test; "
        "test -f /etc/calamares/settings.conf; "
        "(systemctl is-system-running | grep -E '^(running|degraded)$' || "
        "systemctl is-active greetd || pgrep -x cosmic-comp || true)"
    )

    try:
        res = run_guest_exec(probe_script, timeout=60, qga_socket=qga_socket)
        if res.returncode == 0:
            return True, res.stdout.strip()
        last_err = f"exit {res.returncode}: " + (res.stderr or res.stdout).strip()
    except subprocess.TimeoutExpired:
        last_err = "guest-agent probe timed out after 60s"
    except OSError as exc:
        last_err = str(exc)

    return False, f"Live guest invariant verification failed: {last_err}"


def _load_yaml_file(path: pathlib.Path) -> Any:
    """Parse one Calamares/manifest YAML file (PyYAML, `python3-yaml`, is required for install-e2e)."""
    try:
        import yaml  # Imported lazily: only the install-e2e tier parses Calamares YAML.
    except ImportError as exc:
        raise RuntimeError(
            "install-e2e requires PyYAML on the host (python3-yaml) to read calamares/modules/*.conf"
        ) from exc
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse_calamares_size_mib(size_text: Any) -> int:
    """Convert a Calamares partition size (`6144M`, `6144MiB`, `6G`, `6GiB`) to MiB."""
    m = re.fullmatch(r"\s*(\d+)\s*(MiB|M|GiB|G)\s*", str(size_text))
    if not m:
        raise ValueError(f"Unsupported Calamares partition size: {size_text!r}")
    value = int(m.group(1))
    return value * 1024 if m.group(2) in ("G", "GiB") else value


def _shellprocess_commands(conf: dict[str, Any], conf_name: str) -> list[dict[str, Any]]:
    """Return the `script` entries of a Calamares shellprocess config as {command, timeout}."""
    if conf.get("dontChroot", False):
        raise ValueError(f"{conf_name}: dontChroot=true is not reproduced by install-e2e")
    default_timeout = int(conf.get("timeout", 30))
    commands: list[dict[str, Any]] = []
    for entry in conf.get("script") or []:
        if isinstance(entry, str):
            commands.append({"command": entry, "timeout": default_timeout})
        elif isinstance(entry, dict) and isinstance(entry.get("command"), str):
            commands.append(
                {"command": entry["command"], "timeout": int(entry.get("timeout", default_timeout))}
            )
        else:
            raise ValueError(f"{conf_name}: unsupported script entry {entry!r}")
    if not commands:
        raise ValueError(f"{conf_name}: no script commands")
    return commands


def load_calamares_install_config(calamares_dir: pathlib.Path = CALAMARES_DIR) -> dict[str, Any]:
    """
    Read the Calamares configuration that install-e2e reproduces:
      - settings.conf: the exec sequence (checked for the modules reproduced here, in order)
        and the shellprocess instances (`shellprocess@novos` -> shellprocess-novos.conf).
      - partition.conf: ESP mount point, size and name; default filesystem (btrfs).
      - mount.conf: Btrfs subvolumes + mount points (`@` ... `@devshare`), extraMounts, mount options.
      - unpackfs.conf, machineid.conf, fstab.conf, users.conf, packages.conf, netinstall.yaml.
    """
    modules_dir = calamares_dir / "modules"
    settings = _load_yaml_file(calamares_dir / "settings.conf")
    exec_modules: list[str] = []
    for phase in settings.get("sequence") or []:
        if isinstance(phase, dict) and "exec" in phase:
            exec_modules.extend(str(mod) for mod in phase["exec"] or [])
    position = -1
    for mod in INSTALL_E2E_REPRODUCED_MODULES:
        if mod not in exec_modules:
            raise ValueError(f"calamares/settings.conf exec sequence lacks module {mod!r}")
        idx = exec_modules.index(mod)
        if idx < position:
            raise ValueError(f"calamares/settings.conf exec sequence order changed at {mod!r}")
        position = idx
    if settings.get("dont-chroot", False):
        raise ValueError("calamares/settings.conf: dont-chroot=true is not reproduced by install-e2e")
    instance_conf = {
        f"{inst['module']}@{inst['id']}": str(inst["config"])
        for inst in settings.get("instances") or []
        if isinstance(inst, dict) and {"id", "module", "config"} <= inst.keys()
    }
    prepackages_name = instance_conf["shellprocess@prepackages"]
    shellprocess_name = instance_conf["shellprocess@novos"]

    partition = _load_yaml_file(modules_dir / "partition.conf")
    if str(partition.get("defaultFileSystemType", "")).lower() != "btrfs":
        raise ValueError("calamares/modules/partition.conf: defaultFileSystemType is not btrfs")
    esp = {
        "mount_point": str(partition["efiSystemPartition"]),
        "size_mib": _parse_calamares_size_mib(partition["efiSystemPartitionSize"]),
        "name": str(partition["efiSystemPartitionName"]),
    }

    mount = _load_yaml_file(modules_dir / "mount.conf")
    subvolumes = [
        {"subvolume": str(sv["subvolume"]).lstrip("/"), "mount_point": str(sv["mountPoint"])}
        for sv in mount.get("btrfsSubvolumes") or []
    ]
    if not any(sv["mount_point"] == "/" for sv in subvolumes):
        raise ValueError("calamares/modules/mount.conf: no btrfsSubvolumes entry for /")
    # Mount parents before children ("/" first), as Calamares mounts by mount-point depth.
    subvolumes.sort(key=lambda sv: (sv["mount_point"] != "/", sv["mount_point"].count("/"), sv["mount_point"]))
    mount_options = {
        str(opt["filesystem"]): ",".join(str(o) for o in opt.get("options") or [])
        for opt in mount.get("mountOptions") or []
    }

    unpackfs = _load_yaml_file(modules_dir / "unpackfs.conf")
    unpack_entries = unpackfs.get("unpack") or []
    if len(unpack_entries) != 1 or str(unpack_entries[0].get("sourcefs")) != "squashfs":
        raise ValueError("calamares/modules/unpackfs.conf: expected exactly one squashfs unpack entry")

    fstab = _load_yaml_file(modules_dir / "fstab.conf")
    users = _load_yaml_file(modules_dir / "users.conf")
    packages = _load_yaml_file(modules_dir / "packages.conf")
    if str(packages.get("backend")) != "apt":
        raise ValueError("calamares/modules/packages.conf: backend is not apt")
    try_remove: list[str] = []
    for op in packages.get("operations") or []:
        if isinstance(op, dict):
            try_remove.extend(str(p) for p in op.get("try_remove") or [])

    netinstall = _load_yaml_file(modules_dir / "netinstall.yaml")
    if not isinstance(netinstall, list):
        raise ValueError("calamares/modules/netinstall.yaml: expected a list of groups")

    return {
        "esp": esp,
        "subvolumes": subvolumes,
        "btrfs_options": mount_options.get("btrfs", mount_options.get("default", "defaults")),
        "efi_options": mount_options.get("efi", mount_options.get("default", "defaults")),
        "extra_mounts": list(mount.get("extraMounts") or []),
        "unpack_source": str(unpack_entries[0]["source"]),
        "machineid": _load_yaml_file(modules_dir / "machineid.conf") or {},
        "fstab_options": dict(fstab.get("mountOptions") or {}),
        "fstab_ssd_options": dict(fstab.get("ssdExtraMountOptions") or {}),
        "user_groups": [str(g) for g in users.get("defaultGroups") or []],
        "user_shell": str(users.get("defaultShell", "/bin/bash")),
        "packages_update_db": bool(packages.get("update_db", False)),
        "packages_try_remove": try_remove,
        "prepackages_conf": prepackages_name,
        "prepackages": _shellprocess_commands(
            _load_yaml_file(modules_dir / prepackages_name), prepackages_name
        ),
        "shellprocess_conf": shellprocess_name,
        "shellprocess": _shellprocess_commands(
            _load_yaml_file(modules_dir / shellprocess_name), shellprocess_name
        ),
        "netinstall": netinstall,
    }


def _netinstall_group_packages(group: dict[str, Any]) -> list[str]:
    """All packages of a netinstall group and its subgroups (selecting a group selects its subgroups)."""
    found: list[str] = []
    for pkg in group.get("packages") or []:
        found.append(str(pkg["name"]) if isinstance(pkg, dict) else str(pkg))
    for sub in group.get("subgroups") or []:
        found.extend(_netinstall_group_packages(sub))
    return found


def resolve_install_groups(
    groups_spec: str,
    netinstall_groups: list[dict[str, Any]],
    manifest_path: pathlib.Path = PACKAGES_MANIFEST_PATH,
) -> list[dict[str, Any]]:
    """
    Resolve `--groups` to netinstall.yaml groups. A token is either a group name exactly as
    written in netinstall.yaml (names containing commas are re-joined), or the profile id
    (`dev`, `network`, ...) whose config/packages.yaml `name` is that netinstall group name
    (netinstall.yaml is rendered from config/packages.yaml and carries only the names).
    """
    by_name = {str(g["name"]): g for g in netinstall_groups if isinstance(g, dict) and "name" in g}
    id_to_name: dict[str, str] = {}
    if manifest_path.is_file():
        manifest = _load_yaml_file(manifest_path) or {}
        for g in manifest.get("groups") or []:
            if isinstance(g, dict) and g.get("layer") == "profile" and str(g.get("name")) in by_name:
                id_to_name[str(g["id"])] = str(g["name"])

    resolved: list[dict[str, Any]] = []
    pending = ""
    for piece in groups_spec.split(","):
        candidate = f"{pending}, {piece.strip()}" if pending else piece.strip()
        if not candidate:
            continue
        name = candidate if candidate in by_name else id_to_name.get(candidate)
        if name is None:
            pending = candidate
            continue
        pending = ""
        if all(r["name"] != name for r in resolved):
            resolved.append(
                {"token": candidate, "name": name, "packages": _netinstall_group_packages(by_name[name])}
            )
    if pending:
        raise ValueError(
            f"--groups: unknown netinstall group {pending!r}; use a netinstall.yaml group name "
            f"or one of the profile ids {sorted(id_to_name)}"
        )
    if not resolved:
        raise ValueError("--groups: no netinstall group selected")
    return resolved


def _dedupe(items: list[str]) -> list[str]:
    unique: list[str] = []
    for item in items:
        if item not in unique:
            unique.append(item)
    return unique


def _partition_device(index: int) -> str:
    return f"{INSTALL_E2E_DISK}{index}"


def build_install_partition_script(install_cfg: dict[str, Any], build_cfg: dict[str, str]) -> str:
    """partition + mount modules: GPT ESP + Btrfs root on /dev/vda, subvolumes from mount.conf."""
    q = shlex.quote
    esp = install_cfg["esp"]
    root_label = build_cfg.get("ROOT_LABEL", "NOVOS_ROOT")
    esp_dev, root_dev, tgt = _partition_device(1), _partition_device(2), INSTALL_E2E_TARGET
    lines = [
        "set -euo pipefail",
        f"wipefs -af {INSTALL_E2E_DISK}",
        f"sfdisk {INSTALL_E2E_DISK} <<'EOF'",
        "label: gpt",
        f'size={esp["size_mib"]}MiB, type={GPT_TYPE_ESP}, name="{esp["name"]}"',
        f'type={GPT_TYPE_LINUX}, name="{root_label}"',
        "EOF",
        "udevadm settle",
        f"for _ in $(seq 1 50); do [ -b {esp_dev} ] && [ -b {root_dev} ] && break; sleep 0.2; done",
        f"test -b {esp_dev}",
        f"test -b {root_dev}",
        f"mkfs.vfat -F 32 -n {q(esp['name'])} {esp_dev}",
        f"mkfs.btrfs -f -L {q(root_label)} {root_dev}",
        f"mkdir -p {tgt}",
        f"mount {root_dev} {tgt}",
    ]
    for sv in install_cfg["subvolumes"]:
        lines.append(f"btrfs subvolume create {q(tgt + '/' + sv['subvolume'])}")
    lines.append(f"umount {tgt}")
    for sv in install_cfg["subvolumes"]:
        mp = tgt + ("" if sv["mount_point"] == "/" else sv["mount_point"])
        opts = f"{install_cfg['btrfs_options']},subvol=/{sv['subvolume']}"
        lines.append(f"mkdir -p {q(mp)}")
        lines.append(f"mount -t btrfs -o {q(opts)} {root_dev} {q(mp)}")
    esp_mp = tgt + esp["mount_point"]
    lines += [
        f"mkdir -p {q(esp_mp)}",
        f"mount -t vfat -o {q(install_cfg['efi_options'])} {esp_dev} {q(esp_mp)}",
        "# Verify: ESP size, every subvolume exists and is mounted at its mount point",
        f'test "$(lsblk -b -n -o SIZE {esp_dev} | head -n1)" -ge {esp["size_mib"] * 1024 * 1024}',
        f'SUBS="$(btrfs subvolume list {tgt})"',
    ]
    for sv in install_cfg["subvolumes"]:
        mp = tgt + ("" if sv["mount_point"] == "/" else sv["mount_point"])
        lines.append(f'echo "$SUBS" | grep -qx {q(".* path " + sv["subvolume"])}')
        lines.append(f'findmnt -n -o SOURCE {q(mp)} | grep -qF {q("[/" + sv["subvolume"] + "]")}')
    lines.append(f"findmnt -n {q(esp_mp)}")
    lines.append('echo "partition: ESP + ' + str(len(install_cfg["subvolumes"])) + ' Btrfs subvolumes mounted"')
    return "\n".join(lines) + "\n"


def build_install_rootfs_script(
    install_cfg: dict[str, Any], build_cfg: dict[str, str], test_user: str
) -> str:
    """unpackfs, mount extraMounts, machineid, fstab, users and networkcfg (resolv.conf) modules."""
    q = shlex.quote
    tgt = INSTALL_E2E_TARGET
    src = install_cfg["unpack_source"]
    password = build_cfg.get("DEFAULT_ROOT_PASS", "novos")
    lines = [
        "set -euo pipefail",
        f"test -f {q(src)}",
        "SQ=/tmp/novos-e2e-squashfs",
        'mkdir -p "$SQ"',
        f'mount -t squashfs -o loop,ro {q(src)} "$SQ"',
        f'cp -a "$SQ"/. {tgt}/',
        'umount "$SQ"',
        f"test -x {tgt}/bin/sh",
        "# mount module extraMounts (after unpack so the copy never writes into /proc, /sys or /dev)",
    ]
    for em in install_cfg["extra_mounts"]:
        mp = tgt + str(em["mountPoint"])
        opts = [str(o) for o in em.get("options") or []]
        if str(em.get("fs")) == "bind":
            cmd = f"mount --bind {q(str(em['device']))} {q(mp)}"
        else:
            opt_arg = f" -o {q(','.join(opts))}" if opts else ""
            cmd = f"mount -t {q(str(em['fs']))}{opt_arg} {q(str(em['device']))} {q(mp)}"
        step = f"mkdir -p {q(mp)} && {cmd}"
        if em.get("efi"):
            lines.append(f"if [ -d /sys/firmware/efi ]; then {step}; fi")
        else:
            lines.append(step)

    mid = install_cfg["machineid"]
    if mid.get("systemd"):
        lines += [f"rm -f {tgt}/etc/machine-id", f"chroot {tgt} systemd-machine-id-setup"]
    if mid.get("dbus"):
        lines.append(f"mkdir -p {tgt}/var/lib/dbus && rm -f {tgt}/var/lib/dbus/machine-id")
        if mid.get("symlink"):
            lines.append(f"ln -s /etc/machine-id {tgt}/var/lib/dbus/machine-id")
        else:
            lines.append(f"chroot {tgt} dbus-uuidgen --ensure")

    fs_opts = install_cfg["fstab_options"]
    btrfs_opts = str(fs_opts.get("btrfs", fs_opts.get("default", "defaults")))
    vfat_opts = str(fs_opts.get("vfat", fs_opts.get("default", "defaults")))
    ssd_btrfs = str(install_cfg["fstab_ssd_options"].get("btrfs", ""))
    lines += [
        f'ROOT_UUID="$(blkid -s UUID -o value {_partition_device(2)})"',
        f'ESP_UUID="$(blkid -s UUID -o value {_partition_device(1)})"',
        'test -n "$ROOT_UUID" && test -n "$ESP_UUID"',
        f"BTRFS_FSTAB_OPTS={q(btrfs_opts)}",
    ]
    if ssd_btrfs:
        dev_name = INSTALL_E2E_DISK.rsplit("/", 1)[-1]
        lines.append(
            f'if [ "$(cat /sys/block/{dev_name}/queue/rotational)" = "0" ]; then '
            f'BTRFS_FSTAB_OPTS="$BTRFS_FSTAB_OPTS",{q(ssd_btrfs)}; fi'
        )
    lines.append(f": > {tgt}/etc/fstab")
    for sv in install_cfg["subvolumes"]:
        lines.append(
            f'echo "UUID=$ROOT_UUID {sv["mount_point"]} btrfs subvol=/{sv["subvolume"]},$BTRFS_FSTAB_OPTS 0 0"'
            f" >> {tgt}/etc/fstab"
        )
    lines.append(
        f'echo "UUID=$ESP_UUID {install_cfg["esp"]["mount_point"]} vfat {vfat_opts} 0 2" >> {tgt}/etc/fstab'
    )

    for group in install_cfg["user_groups"]:
        lines.append(
            f"if ! chroot {tgt} getent group {q(group)} > /dev/null; then chroot {tgt} groupadd {q(group)}; fi"
        )
    lines += [
        f"chroot {tgt} useradd -m -U -s {q(install_cfg['user_shell'])} "
        f"-G {q(','.join(install_cfg['user_groups']))} {q(test_user)}",
        # users.conf doReusePassword: root gets the user's password.
        f"printf '%s\\n' {q(test_user + ':' + password)} {q('root:' + password)} | chroot {tgt} chpasswd",
        # networkcfg (Calamares networkcfg/main.py:170-185): whenever the live /etc/resolv.conf exists,
        # remove the target's and copy the live one without dereferencing (follow_symlinks=False).
        "# networkcfg: replace the target's resolv.conf with the live system's (as Calamares networkcfg)",
        "if [ -e /etc/resolv.conf ] || [ -L /etc/resolv.conf ]; then "
        f"rm -f {tgt}/etc/resolv.conf; cp -P /etc/resolv.conf {tgt}/etc/resolv.conf; fi",
        f"if [ -e {tgt}/etc/resolv.conf ] || [ -L {tgt}/etc/resolv.conf ]; then ls -l {tgt}/etc/resolv.conf; "
        'else echo "networkcfg: target has no /etc/resolv.conf"; fi',
        f"if [ -f {tgt}/etc/resolv.conf ] && [ ! -L {tgt}/etc/resolv.conf ]; then cat {tgt}/etc/resolv.conf; fi",
        f"cat {tgt}/etc/fstab",
        f'echo "rootfs: unpacked {src}, user {test_user} created"',
    ]
    return "\n".join(lines) + "\n"


def _chroot_shell_line(command: str) -> str:
    """Run one Calamares shellprocess command in the target chroot, as `target_env_call(sh -c)`."""
    return f"chroot {INSTALL_E2E_TARGET} /bin/sh -c {shlex.quote(command)}"


def build_install_netinstall_script(install_cfg: dict[str, Any], groups: list[dict[str, Any]]) -> str:
    """shellprocess@prepackages, then the packages module: try_remove, then the selected groups."""
    q = shlex.quote
    tgt = INSTALL_E2E_TARGET
    lines = ["set -euo pipefail", "export DEBIAN_FRONTEND=noninteractive"]
    for entry in install_cfg["prepackages"]:
        lines.append(f"echo {q('prepackages: ' + entry['command'][:120])}")
        lines.append(f"timeout {int(entry['timeout'])} {_chroot_shell_line(entry['command'])}")
    # Harness-only diagnostic (not Calamares): the target chroot must resolve the archive host.
    lines.append(
        f"if ! chroot {tgt} getent ahosts deb.debian.org > /dev/null; then "
        "echo \"netinstall: target chroot cannot resolve deb.debian.org "
        f"(resolv.conf: $(grep -m1 '^nameserver' {tgt}/etc/resolv.conf))\" >&2; exit 1; fi"
    )
    if install_cfg["packages_update_db"]:
        lines.append(f"chroot {tgt} apt-get -q update 2>&1 | tee /tmp/novos-e2e-apt-update.log")
        # Harness-only diagnostic: apt-get update exits 0 when fetches fail with warnings; stop here
        # with the fetch errors instead of letting apt-get install report "Unable to locate package".
        lines.append(
            "if grep -E -A1 '^(Err:|W: Failed to fetch)' /tmp/novos-e2e-apt-update.log "
            "> /tmp/novos-e2e-apt-fetch-errors.log; then "
            'echo "netinstall: apt-get update could not fetch:" >&2; '
            "cat /tmp/novos-e2e-apt-fetch-errors.log >&2; exit 1; fi"
        )
    for pkg in install_cfg["packages_try_remove"]:
        # try_remove: only packages present are removed; a failed removal of a present package fails.
        lines.append(
            f"if chroot {tgt} dpkg-query -W -f='${{db:Status-Status}}' {q(pkg)} 2> /dev/null | grep -qx installed; "
            f"then chroot {tgt} apt-get --purge -q -y remove {q(pkg)}; fi"
        )
    packages = _dedupe([p for g in groups for p in g["packages"]])
    lines.append(f"chroot {tgt} apt-get -q -y install " + " ".join(q(p) for p in packages))
    lines.append(f'echo "netinstall: installed {len(packages)} packages from {len(groups)} groups"')
    return "\n".join(lines) + "\n"


def build_install_bootloader_script(install_cfg: dict[str, Any]) -> str:
    """limine-bootloader module (the ISO's own main.py) under a libcalamares stand-in; the target stays mounted."""
    q = shlex.quote
    tgt = INSTALL_E2E_TARGET
    esp_mp = install_cfg["esp"]["mount_point"]
    gs = {
        "rootMountPoint": tgt,
        "partitions": [
            {"device": _partition_device(1), "mountPoint": esp_mp, "fs": "fat32"},
            {"device": _partition_device(2), "mountPoint": "/", "fs": "btrfs"},
        ],
    }
    runner = "\n".join(
        [
            "import importlib.util, json, subprocess, sys, types",
            f"GS = json.loads({json.dumps(json.dumps(gs))})",
            "lc = types.ModuleType('libcalamares')",
            "lc.globalstorage = types.SimpleNamespace(value=GS.get, contains=GS.__contains__)",
            "def _warn(msg):",
            "    print('[CALAMARES-WARN] ' + str(msg), file=sys.stderr, flush=True)",
            "def _target_env_call(cmd, *args, **kwargs):",
            "    return subprocess.call(['chroot', GS['rootMountPoint'], *cmd])",
            "lc.utils = types.SimpleNamespace(warning=_warn, debug=_warn, target_env_call=_target_env_call)",
            "lc.job = types.SimpleNamespace(configuration={})",
            "sys.modules['libcalamares'] = lc",
            "spec = importlib.util.spec_from_file_location('limine_bootloader_main', sys.argv[1])",
            "mod = importlib.util.module_from_spec(spec)",
            "spec.loader.exec_module(mod)",
            "result = mod.run()",
            "if result is not None:",
            "    print('[CALAMARES-FAIL] ' + ': '.join(str(r) for r in result), file=sys.stderr, flush=True)",
            "    sys.exit(1)",
            "print('limine-bootloader: run() returned None')",
        ]
    )
    candidates = " ".join(q(p) for p in LIMINE_MODULE_GUEST_PATHS)
    lines = [
        "set -euo pipefail",
        'MAIN=""',
        f'for cand in {candidates}; do if [ -f "$cand" ]; then MAIN="$cand"; break; fi; done',
        'test -n "$MAIN"',
        'echo "limine-bootloader: $MAIN"',
        "cat > /tmp/novos-e2e-limine-runner.py <<'PYEOF'",
        runner,
        "PYEOF",
        'python3 /tmp/novos-e2e-limine-runner.py "$MAIN"',
        f"test -s {q(tgt + esp_mp + '/limine/limine.conf')}",
        f"test -s {q(tgt + esp_mp + '/EFI/BOOT/BOOTX64.EFI')}",
        "sync",
    ]
    return "\n".join(lines) + "\n"


def build_install_test_agent_script() -> str:
    """
    Harness-only sub-step (not part of Calamares), run after the limine module's Golden
    Master snapshot so the snapshot stays Calamares-identical: dpkg -i the test overlay's
    .debs (qemu-guest-agent and its dependencies) in the target chroot, check qemu-ga is
    present in the target, then the Calamares umount module.
    """
    q = shlex.quote
    tgt = INSTALL_E2E_TARGET
    stage_rel = "/tmp/novos-test-debs"
    lines = [
        "set -euo pipefail",
        "export DEBIAN_FRONTEND=noninteractive",
        "shopt -s nullglob",
        f"debs=({q(TEST_OVERLAY_DEBS_DIR)}/*.deb)",
        f'if [ "${{#debs[@]}}" -eq 0 ]; then echo "test-agent: no .deb in {TEST_OVERLAY_DEBS_DIR}" >&2; exit 1; fi',
        f"rm -rf {q(tgt + stage_rel)}",
        f"mkdir -p {q(tgt + stage_rel)}",
        f'cp -- "${{debs[@]}}" {q(tgt + stage_rel)}/',
        "inner=()",
        f'for d in "${{debs[@]}}"; do inner+=({q(stage_rel)}/"$(basename "$d")"); done',
        'echo "test-agent: dpkg -i ${#inner[@]} packages: ${inner[*]}"',
        f'chroot {tgt} dpkg -i "${{inner[@]}}"',
        f"rm -rf {q(tgt + stage_rel)}",
        f"chroot {tgt} dpkg-query -W qemu-guest-agent",
        f"test -x {q(tgt + '/usr/sbin/qemu-ga')}",
        "sync",
        "# umount module",
        f"umount -R {tgt}",
    ]
    return "\n".join(lines) + "\n"


def _run_guest_install_substep(
    script: str, tag: str, qga_socket: pathlib.Path, timeout: int
) -> tuple[bool, str]:
    """Run one install sub-step script as root in the guest (guest agent); return (passed, log excerpt)."""
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    path = f"/tmp/novos-e2e-{tag}"
    remote = (
        f"printf %s {encoded} | base64 -d > {path}.sh && "
        f"{{ bash {path}.sh > {path}.log 2>&1; rc=$?; tail -n {INSTALL_E2E_LOG_EXCERPT_LINES} {path}.log; exit $rc; }}"
    )
    try:
        res = run_guest_exec(remote, timeout=timeout, qga_socket=qga_socket)
    except subprocess.TimeoutExpired:
        return False, f"{tag}: timed out after {timeout}s"
    except OSError as exc:
        return False, f"{tag}: {exc}"
    excerpt = "\n".join(
        (res.stdout.strip() + "\n" + res.stderr.strip()).strip().splitlines()[-INSTALL_E2E_LOG_EXCERPT_LINES:]
    )
    if res.returncode != 0:
        excerpt = f"[exit {res.returncode}]\n{excerpt}"
    return res.returncode == 0, excerpt


def run_install_e2e_on_guest(
    build_cfg: dict[str, str],
    install_cfg: dict[str, Any],
    groups: list[dict[str, Any]],
    qga_socket: pathlib.Path,
    test_user: str = INSTALL_E2E_TEST_USER,
) -> tuple[bool, list[dict[str, Any]]]:
    """
    Execute Tier 2 (`install-e2e`) against `/dev/vda` inside the live guest, reproducing the
    Calamares exec sequence from `calamares/` (see load_calamares_install_config):
      partition    - GPT ESP (partition.conf) + Btrfs root; the 10 mount.conf subvolumes
                     (`@` ... `@devshare`) created and mounted.
      rootfs       - unpackfs, mount extraMounts, machineid, fstab, users (test user), networkcfg.
      netinstall   - shellprocess@prepackages, then packages.conf try_remove + the `--groups` packages.
      shellprocess - every command of shellprocess-novos.conf in the target chroot,
                     `@@username@@` replaced by the test user.
      bootloader   - the ISO's limine-bootloader main.py (includes the Golden Master snapshot).
      test_agent   - harness-only, not part of Calamares: dpkg -i the test overlay's .debs
                     (qemu-guest-agent) in the target, check qemu-ga, then umount.
    Each sub-step is one milestone (passed, detail, log excerpt); the first failure stops the run.
    Every command runs as root in the live guest through the QEMU guest agent.
    """
    milestones: list[dict[str, Any]] = []

    def record(substep: str, passed: bool, detail: str, excerpt: str, **extra: Any) -> bool:
        milestones.append(
            {
                "id": INSTALL_E2E_SUBSTEP_MILESTONES[substep],
                "substep": substep,
                "passed": passed,
                "detail": detail,
                "log_excerpt": excerpt,
                "harness_only": substep in INSTALL_E2E_HARNESS_ONLY_SUBSTEPS,
                **extra,
            }
        )
        return passed

    ok, excerpt = _run_guest_install_substep(
        build_install_partition_script(install_cfg, build_cfg), "partition", qga_socket, 600
    )
    subvols = ", ".join(sv["subvolume"] for sv in install_cfg["subvolumes"])
    if not record("partition", ok, f"ESP {install_cfg['esp']['size_mib']} MiB + subvolumes {subvols}", excerpt):
        return False, milestones

    ok, excerpt = _run_guest_install_substep(
        build_install_rootfs_script(install_cfg, build_cfg, test_user), "rootfs", qga_socket, 3600
    )
    if not record("rootfs", ok, f"unpacked {install_cfg['unpack_source']}; test user {test_user}", excerpt):
        return False, milestones

    netinstall_timeout = sum(int(e["timeout"]) for e in install_cfg["prepackages"]) + 3600
    ok, excerpt = _run_guest_install_substep(
        build_install_netinstall_script(install_cfg, groups), "netinstall", qga_socket, netinstall_timeout
    )
    if not record(
        "netinstall",
        ok,
        f"{install_cfg['prepackages_conf']} + groups: " + "; ".join(g["name"] for g in groups),
        excerpt,
        groups=[{"token": g["token"], "name": g["name"]} for g in groups],
    ):
        return False, milestones

    command_results: list[dict[str, Any]] = []
    sp_ok, sp_excerpt = True, ""
    for idx, entry in enumerate(install_cfg["shellprocess"], start=1):
        command = entry["command"].replace("@@username@@", test_user)
        script = (
            "set -euo pipefail\nexport DEBIAN_FRONTEND=noninteractive\n"
            f"timeout {int(entry['timeout'])} {_chroot_shell_line(command)}\n"
        )
        cmd_ok, cmd_excerpt = _run_guest_install_substep(
            script, f"shellprocess-{idx}", qga_socket, int(entry["timeout"]) + 60
        )
        command_results.append({"command": command, "passed": cmd_ok})
        sp_excerpt = f"$ {command}\n{cmd_excerpt}"
        if not cmd_ok:
            sp_ok = False
            break
    if not record(
        "shellprocess",
        sp_ok,
        f"{install_cfg['shellprocess_conf']}: {sum(r['passed'] for r in command_results)}/"
        f"{len(install_cfg['shellprocess'])} commands passed",
        sp_excerpt,
        commands=command_results,
    ):
        return False, milestones

    ok, excerpt = _run_guest_install_substep(
        build_install_bootloader_script(install_cfg), "bootloader", qga_socket, 1800
    )
    if not record("bootloader", ok, "limine-bootloader module (Golden Master snapshot taken)", excerpt):
        return False, milestones

    ok, excerpt = _run_guest_install_substep(build_install_test_agent_script(), "test-agent", qga_socket, 900)
    record(
        "test_agent",
        ok,
        f"harness-only (not Calamares): dpkg -i {TEST_OVERLAY_DEBS_DIR}/*.deb in the target, qemu-ga present, umount",
        excerpt,
    )
    return ok, milestones


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
    qga_socket: pathlib.Path,
    installed_mode: bool = False,
) -> tuple[bool, dict[str, bool], str]:
    """
    Poll serial output and the guest agent channel until the agent answers a guest-exec
    probe or timeout expires. While the guest boots the agent is not running yet, so each
    failed sync drops the connection and the next poll reconnects.
    Returns (ready, milestone_matches, detail_msg).
    """
    deadline = time.monotonic() + timeout_sec
    last_matches: dict[str, bool] = {}
    last_agent_err = "no guest agent probe attempted"
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

        # Probe the guest agent (sync + guest-exec); reconnects on every attempt
        try:
            probe_res = run_guest_exec(probe_cmd, timeout=8, qga_socket=qga_socket)
            if probe_res.returncode == 0 and (installed_mode or "ID=" in probe_res.stdout):
                return True, last_matches, "Guest agent readiness confirmed (guest-exec probe passed)"
            last_agent_err = f"probe exit {probe_res.returncode}: {(probe_res.stderr or probe_res.stdout).strip()}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_agent_err = str(exc)

        # Check serial milestone fallback
        if not installed_mode and last_matches.get("uefi_ovmf") and last_matches.get("systemd_or_session_ready"):
            # Give the agent a brief moment if serial already reached session ready
            time.sleep(2)

        time.sleep(3)

    return (
        False,
        last_matches,
        f"Timed out after {timeout_sec}s waiting for guest readiness (last guest agent error: {last_agent_err})",
    )


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

    # install-e2e: read the Calamares configuration and resolve --groups before booting anything.
    install_cfg: dict[str, Any] | None = None
    install_groups: list[dict[str, Any]] = []
    if tier == "install-e2e":
        try:
            install_cfg = load_calamares_install_config()
            install_groups = resolve_install_groups(args.groups, install_cfg["netinstall"])
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            print(f"[ERROR] install-e2e Calamares configuration: {exc}", file=sys.stderr)
            return EXIT_ERROR

    # Pre-flight Guardrails (mandatory before booting QEMU or mutating disk/OVMF state)
    if not args.dry_run:
        preflight_rc = run_preflight_guardrails(
            item_toml=item_toml,
            iso_path=iso_path,
            skip_variance_check=args.skip_variance_check,
        )
        if preflight_rc != EXIT_OK:
            return preflight_rc

        # The harness reaches the guest only through the test overlay's qemu-guest-agent (STEP 15G).
        overlay_ok, overlay_detail = check_test_overlay_iso(iso_path)
        if not overlay_ok:
            print(f"[ERROR] Test overlay pre-flight: {overlay_detail}", file=sys.stderr)
            return EXIT_ERROR
        print(f"[OK] Test overlay pre-flight: {overlay_detail}")

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    qmp_socket = pathlib.Path(tempfile.gettempdir()) / f"novos-qmp-{os.getpid()}.sock"
    qga_socket = pathlib.Path(tempfile.gettempdir()) / f"novos-qga-{os.getpid()}.sock"
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
        qga_socket=qga_socket,
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
                f"xorriso -indev {iso_path} -find {TEST_OVERLAY_LAYER_ISO_PATH} -type f",
            ],
            "qmp_socket": str(qmp_socket),
            "qga_socket": str(qga_socket),
            "serial_log": str(serial_log),
            "qemu_command": qemu_cmd,
            "qemu_command_str": " ".join(qemu_cmd),
            "milestones": milestone_plan,
        }
        if install_cfg is not None:
            dry_payload["install_e2e"] = {
                "subvolumes": install_cfg["subvolumes"],
                "esp": install_cfg["esp"],
                "groups": [{"token": g["token"], "name": g["name"]} for g in install_groups],
                "shellprocess_conf": install_cfg["shellprocess_conf"],
                "test_user": INSTALL_E2E_TEST_USER,
            }
        print(json.dumps(dry_payload, indent=2))
        return EXIT_OK

    websockify_proc: subprocess.Popen[bytes] | None = None
    qemu_proc: subprocess.Popen[Any] | None = None
    completed_milestones: list[dict[str, Any]] = []
    screendumps: list[dict[str, Any]] = []
    test_overlay_record: dict[str, Any] | None = None
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
            qga_socket=qga_socket,
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
        inv_ok, inv_detail = verify_live_guest_invariants(build_cfg, qga_socket)
        completed_milestones.append(
            {
                "id": "M3_LIVE_GUEST_INVARIANTS",
                "passed": inv_ok,
                "detail": inv_detail,
            }
        )
        if not inv_ok:
            raise RuntimeError(inv_detail)

        # Verdict record: test overlay manifest + production filesystem.squashfs sha256 (live guest)
        test_overlay_record = collect_test_overlay_record(qga_socket)

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
        if tier == "install-e2e" and install_cfg is not None:
            inst_ok, inst_milestones = run_install_e2e_on_guest(
                build_cfg, install_cfg, install_groups, qga_socket=qga_socket
            )
            completed_milestones.extend(inst_milestones)
            if not inst_ok:
                failed = next(m for m in inst_milestones if not m["passed"])
                raise RuntimeError(
                    f"install-e2e {failed['substep']} failed ({failed['id']}): {failed['detail']}\n"
                    f"{failed['log_excerpt']}"
                )

            # Shut down live VM and boot installed system from /dev/vda without CD-ROM
            terminate_process(qemu_proc)
            if qmp_socket.exists():
                qmp_socket.unlink()
            if qga_socket.exists():
                qga_socket.unlink()

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
                qga_socket=qga_socket,
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
                qga_socket=qga_socket,
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
        for sock_path in (qmp_socket, qga_socket):
            if sock_path.exists():
                try:
                    sock_path.unlink()
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
        "qga_socket": str(qga_socket),
        "serial_log": str(serial_log),
        "test_overlay": test_overlay_record,
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
        # 4 live-boot + 6 install sub-steps (5 Calamares + harness-only test agent) + reboot
        self.assertEqual(len(plan_e2e), 11)
        self.assertEqual(
            [m["id"] for m in plan_e2e[4:]],
            [
                "M5_INSTALL_PARTITION",
                "M5_INSTALL_ROOTFS",
                "M5_INSTALL_NETINSTALL",
                "M5_INSTALL_SHELLPROCESS",
                "M5_INSTALL_BOOTLOADER",
                "M5_INSTALL_TEST_AGENT",
                "M6_REBOOT_INSTALLED_VDA",
            ],
        )
        self.assertTrue(plan_e2e[9]["harness_only"])
        self.assertEqual(
            [m["id"] for m in plan_e2e if m.get("harness_only")], ["M5_INSTALL_TEST_AGENT"]
        )

    def test_calamares_install_config_groups_and_scripts(self) -> None:
        cfg = load_calamares_install_config()
        self.assertEqual(
            {sv["subvolume"] for sv in cfg["subvolumes"]},
            {"@", "@home", "@snapshots", "@var_log", "@var_cache", "@flatpak",
             "@docker", "@containerd", "@llm", "@devshare"},
        )
        self.assertEqual(cfg["subvolumes"][0], {"subvolume": "@", "mount_point": "/"})
        self.assertEqual(cfg["esp"], {"mount_point": "/boot/efi", "size_mib": 6144, "name": "NOVOS_EFI"})
        self.assertEqual(cfg["shellprocess_conf"], "shellprocess-novos.conf")
        self.assertTrue(any("--post-calamares --user @@username@@" in c["command"] for c in cfg["shellprocess"]))

        groups = resolve_install_groups(DEFAULT_INSTALL_GROUPS, cfg["netinstall"])
        self.assertEqual([g["token"] for g in groups], ["dev", "network"])
        self.assertIn("docker-ce", groups[0]["packages"])
        self.assertIn("tailscale", groups[1]["packages"])
        by_name = resolve_install_groups(groups[0]["name"], cfg["netinstall"])
        self.assertEqual(by_name[0]["name"], groups[0]["name"])
        with self.assertRaises(ValueError):
            resolve_install_groups("no-such-group", cfg["netinstall"])

        part = build_install_partition_script(cfg, {"ROOT_LABEL": "NOVOS_ROOT"})
        self.assertIn("btrfs subvolume create /mnt/novos-install/@devshare", part)
        self.assertIn("subvol=/@devshare", part)
        self.assertIn('size=6144MiB', part)
        net = build_install_netinstall_script(cfg, groups)
        self.assertIn("novos-setup --pre-packages", net)
        self.assertIn("tailscale", net)
        # Harness-only DNS + fetch-error checks run before the packages module installs anything.
        install_at = net.index("apt-get -q -y install")
        self.assertLess(net.index("getent ahosts deb.debian.org"), install_at)
        self.assertLess(net.index("W: Failed to fetch"), install_at)
        self.assertLess(net.index("chroot /mnt/novos-install apt-get -q update"), net.index("W: Failed to fetch"))
        rootfs = build_install_rootfs_script(cfg, {}, INSTALL_E2E_TEST_USER)
        # networkcfg copies the live resolv.conf unconditionally (Calamares networkcfg/main.py:170-185).
        self.assertIn("cp -P /etc/resolv.conf /mnt/novos-install/etc/resolv.conf", rootfs)
        self.assertNotIn("! -s /mnt/novos-install/etc/resolv.conf", rootfs)
        boot = build_install_bootloader_script(cfg)
        self.assertNotIn("umount", boot)
        agent = build_install_test_agent_script()
        self.assertIn("/usr/share/novos-test/debs/*.deb", agent)
        self.assertIn('chroot /mnt/novos-install dpkg -i "${inner[@]}"', agent)
        self.assertIn("/mnt/novos-install/usr/sbin/qemu-ga", agent)
        self.assertTrue(agent.rstrip().endswith("umount -R /mnt/novos-install"))
        self.assertLess(agent.index("dpkg -i"), agent.index("umount -R"))
        for script in (part, build_install_rootfs_script(cfg, {}, INSTALL_E2E_TEST_USER), net, boot, agent):
            res = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)

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
                qga_socket=pathlib.Path("/tmp/novos-qga-1.sock"),
                use_vnc=False,
            )
            self.assertIn("-display", cmd_headless)
            self.assertIn("none", cmd_headless)
            joined = " ".join(cmd_headless)
            self.assertNotIn("hostfwd", joined)
            self.assertIn("socket,id=qga0,path=/tmp/novos-qga-1.sock,server=on,wait=off", cmd_headless)
            self.assertIn("virtio-serial-pci", cmd_headless)
            self.assertIn("virtserialport,chardev=qga0,name=org.qemu.guest_agent.0", cmd_headless)

            cmd_vnc = build_qemu_command(
                iso_path=pathlib.Path("/workspace/build/NovOS-sid-amd64.iso"),
                target_disk=pathlib.Path("/workspace/build/novos-calamares-target.img"),
                disk_format="qcow2",
                ovmf_info=info,
                ovmf_vars_path=vars_f,
                qmp_socket=pathlib.Path("/tmp/qmp.sock"),
                serial_log=pathlib.Path("/tmp/serial.log"),
                qga_socket=pathlib.Path("/tmp/novos-qga-1.sock"),
                use_vnc=True,
                boot_from_disk_only=True,
            )
            self.assertIn("-vnc", cmd_vnc)
            self.assertIn("0.0.0.0:0", cmd_vnc)
            self.assertNotIn("-cdrom", cmd_vnc)
            self.assertIn("virtserialport,chardev=qga0,name=org.qemu.guest_agent.0", cmd_vnc)

    def test_guest_agent_exec_over_fake_socket(self) -> None:
        import threading

        received: list[dict[str, Any]] = []

        def _fake_agent(srv: socket.socket, exit_after_polls: int | None) -> None:
            conn, _ = srv.accept()
            with conn:
                f = conn.makefile("rb")
                polls = 0
                while True:
                    raw = f.readline()
                    if not raw:
                        return
                    line = raw.lstrip(b"\xff").strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    received.append(msg)
                    cmd = msg["execute"]
                    if cmd == "guest-sync-delimited":
                        # A stale reply from an earlier client, then the sentinel-prefixed sync reply.
                        conn.sendall(b'{"return": {"pid": 1}}\n')
                        conn.sendall(b"\xff" + json.dumps({"return": msg["arguments"]["id"]}).encode() + b"\n")
                    elif cmd == "guest-exec":
                        conn.sendall(b'{"return": {"pid": 4242}}\n')
                    elif cmd == "guest-exec-status":
                        polls += 1
                        if exit_after_polls is None or polls < exit_after_polls:
                            conn.sendall(b'{"return": {"exited": false}}\n')
                        else:
                            reply = {
                                "exited": True,
                                "exitcode": 3,
                                "out-data": base64.b64encode("ID=novos\nü\n".encode()).decode(),
                                "err-data": base64.b64encode(b"warn\n").decode(),
                            }
                            conn.sendall(json.dumps({"return": reply}).encode() + b"\n")

        with tempfile.TemporaryDirectory() as tmp:
            for exit_after in (2, None):
                sock_path = pathlib.Path(tmp) / f"qga-{exit_after}.sock"
                received.clear()
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
                    srv.bind(str(sock_path))
                    srv.listen(1)
                    t = threading.Thread(target=_fake_agent, args=(srv, exit_after), daemon=True)
                    t.start()
                    if exit_after is not None:
                        res = run_guest_exec("cat /etc/os-release; exit 3", timeout=10, qga_socket=sock_path)
                        self.assertEqual(res.returncode, 3)
                        self.assertEqual(res.stdout, "ID=novos\nü\n")
                        self.assertEqual(res.stderr, "warn\n")
                    else:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            run_guest_exec("sleep infinity", timeout=1, qga_socket=sock_path)
                    t.join(timeout=3)
                cmds = [m["execute"] for m in received]
                self.assertEqual(cmds[:2], ["guest-sync-delimited", "guest-exec"])
                self.assertTrue(all(c == "guest-exec-status" for c in cmds[2:]))
                self.assertGreaterEqual(len(cmds), 4)
                exec_args = received[1]["arguments"]
                self.assertEqual(exec_args["path"], "/bin/bash")
                self.assertEqual(exec_args["arg"][0], "-c")
                self.assertTrue(exec_args["capture-output"])
                self.assertEqual(received[2]["arguments"], {"pid": 4242})

            # No agent behind the socket path: a channel error (OSError), never a fake result.
            with self.assertRaises(OSError):
                run_guest_exec("true", timeout=1, qga_socket=pathlib.Path(tmp) / "absent.sock")

    def test_test_overlay_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ok, detail = check_test_overlay_iso(pathlib.Path(tmp) / "NovOS-sid-amd64.iso")
            self.assertFalse(ok)
            self.assertIn("scripts/make-test-overlay-iso.py", detail)
            self.assertIn("/live/zz-novos-test.squashfs", detail)

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
        "--groups",
        default=DEFAULT_INSTALL_GROUPS,
        help=(
            "install-e2e: comma-separated netinstall groups to install, as netinstall.yaml group names "
            f"or their config/packages.yaml profile ids (default: {DEFAULT_INSTALL_GROUPS})"
        ),
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
