#!/usr/bin/env python3
"""
NovOS Nebulous Controller (scripts/nebulous-ctl.py)
===================================================
Implements:
  1. Golden OS Snapshot Extractor (`extract-golden`) using unprivileged user-space
     `xorriso` + `unsquashfs` inspection of built Live ISOs.
  2. Asymmetric Forward-Only Variance Lock (`verify-variance`) validating repo
     invariants and live ISO package/binary/module inventories against
     `.nebulous/manifest.toml` and `.nebulous/golden-snapshot.toml`.
  3. Automated Loop Breaker & Guardrail State Machine (`record-attempt`,
     `check-guardrail`) tracking normalized error/diff fingerprints and injecting
     structured negative constraints into tiered `Item.toml` state files.
  4. Host-Only User Approval Commands (`bless-golden`, `approve-variance`,
     `unlock-loop`) guarded against agent invocation.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_VARIANCE_LOCK = 2
EXIT_LOOP_LOCK = 3
EXIT_PERMISSION_DENIED = 4

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_PROJECT_TOML = REPO_ROOT / ".nebulous" / "project.toml"
DEFAULT_MANIFEST_TOML = REPO_ROOT / ".nebulous" / "manifest.toml"
DEFAULT_GOLDEN_TOML = REPO_ROOT / ".nebulous" / "golden-snapshot.toml"
DEFAULT_APPROVALS_TOML = REPO_ROOT / ".nebulous" / "approvals.toml"

COSMIC_EPOCH_COMPONENTS = (
    "cosmic-comp",
    "cosmic-session",
    "cosmic-greeter",
    "cosmic-panel",
    "cosmic-applets",
    "cosmic-files",
    "cosmic-term",
    "cosmic-settings",
)


# ==============================================================================
# 1. Debian Version Comparison (dpkg wrapper + Pure-Python Policy §5.6.12 fallback)
# ==============================================================================

def _char_weight(ch: str) -> int:
    """Return Debian version comparison lexical weight for a non-digit character."""
    if ch == "~":
        return -2
    if ch == "":
        return -1
    if ch.isalpha():
        return ord(ch)
    return ord(ch) + 256


def _compare_deb_part(part_a: str, part_b: str) -> int:
    """Compare upstream_version or debian_revision strings per Debian Policy §5.6.12."""
    i = 0
    j = 0
    len_a = len(part_a)
    len_b = len(part_b)

    while i < len_a or j < len_b:
        # 1. Compare non-digit segments character-by-character
        while (i < len_a and not part_a[i].isdigit()) or (
            j < len_b and not part_b[j].isdigit()
        ):
            ca = part_a[i] if (i < len_a and not part_a[i].isdigit()) else ""
            cb = part_b[j] if (j < len_b and not part_b[j].isdigit()) else ""
            wa = _char_weight(ca)
            wb = _char_weight(cb)
            if wa != wb:
                return -1 if wa < wb else 1
            if ca != "":
                i += 1
            if cb != "":
                j += 1

        # 2. Compare contiguous digit segments numerically
        start_i = i
        while i < len_a and part_a[i].isdigit():
            i += 1
        start_j = j
        while j < len_b and part_b[j].isdigit():
            j += 1

        num_a = int(part_a[start_i:i]) if i > start_i else 0
        num_b = int(part_b[start_j:j]) if j > start_j else 0
        if num_a != num_b:
            return -1 if num_a < num_b else 1

    return 0


def _split_debian_version(ver: str) -> tuple[int, str, str]:
    """Split a Debian version string into (epoch, upstream_version, debian_revision)."""
    v = ver.strip()
    if ":" in v:
        epoch_str, rest = v.split(":", 1)
        epoch = int(epoch_str) if epoch_str.isdigit() else 0
    else:
        epoch = 0
        rest = v

    if "-" in rest:
        upstream, revision = rest.rsplit("-", 1)
    else:
        upstream, revision = rest, "0"

    return epoch, upstream, revision


def compare_debian_versions_pure(v1: str, v2: str) -> int:
    """
    Pure-Python Debian version comparison.
    Returns:
      -1 if v1 < v2
       0 if v1 == v2
      +1 if v1 > v2
    """
    if v1.strip() == v2.strip():
        return 0
    e1, u1, r1 = _split_debian_version(v1)
    e2, u2, r2 = _split_debian_version(v2)
    if e1 != e2:
        return -1 if e1 < e2 else 1
    cmp_u = _compare_deb_part(u1, u2)
    if cmp_u != 0:
        return cmp_u
    return _compare_deb_part(r1, r2)


def compare_debian_versions(v1: str, v2: str, use_dpkg: bool = True) -> int:
    """
    Compare two Debian version strings using `dpkg --compare-versions` when available,
    falling back to `compare_debian_versions_pure`.
    """
    v1_s = str(v1).strip()
    v2_s = str(v2).strip()
    if v1_s == v2_s:
        return 0

    if use_dpkg and shutil.which("dpkg"):
        try:
            res_eq = subprocess.run(
                ["dpkg", "--compare-versions", v1_s, "eq", v2_s],
                check=False,
                capture_output=True,
            )
            if res_eq.returncode == 0:
                return 0
            res_lt = subprocess.run(
                ["dpkg", "--compare-versions", v1_s, "lt", v2_s],
                check=False,
                capture_output=True,
            )
            if res_lt.returncode == 0:
                return -1
            return 1
        except OSError:
            pass

    return compare_debian_versions_pure(v1_s, v2_s)


# ==============================================================================
# 2. TOML Serialization & Configuration Loading Helpers
# ==============================================================================

ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI/OSC escape sequences from log and error strings."""
    return ANSI_ESCAPE_RE.sub("", text)


def _toml_escape(val: str) -> str:
    out: list[str] = []
    for ch in val:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\r":
            out.append("\\r")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _format_toml_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return repr(val)
    if isinstance(val, str):
        return f'"{_toml_escape(val)}"'
    if isinstance(val, (list, tuple)):
        if not val:
            return "[]"
        items = ", ".join(_format_toml_value(x) for x in val)
        return f"[{items}]"
    if isinstance(val, dict):
        parts = []
        for k, v in val.items():
            key_str = k if re.match(r"^[A-Za-z0-9_-]+$", str(k)) else f'"{_toml_escape(str(k))}"'
            parts.append(f"{key_str} = {_format_toml_value(v)}")
        return "{ " + ", ".join(parts) + " }"
    return f'"{_toml_escape(str(val))}"'


def _format_toml_key(key: str) -> str:
    if re.match(r"^[A-Za-z0-9_-]+$", key):
        return key
    return f'"{_toml_escape(key)}"'


def load_toml_file(path: pathlib.Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_in_extract_root(extract_root: pathlib.Path, entry_path: pathlib.Path) -> pathlib.Path:
    """Resolve multi-hop relative and absolute symlinks strictly inside extract_root."""
    root_norm = pathlib.Path(os.path.normpath(extract_root))
    current = pathlib.Path(os.path.normpath(entry_path))
    visited: set[pathlib.Path] = set()
    while os.path.islink(current):
        if current in visited:
            break
        visited.add(current)
        target = os.readlink(current)
        if os.path.isabs(target):
            candidate = pathlib.Path(os.path.normpath(root_norm / target.lstrip("/")))
        else:
            candidate = pathlib.Path(os.path.normpath(current.parent / target))
        try:
            candidate.relative_to(root_norm)
            current = candidate
        except ValueError:
            current = root_norm / candidate.name
    return current


# ==============================================================================
# 3. Golden OS Snapshot Extractor (`extract-golden`)
# ==============================================================================

def parse_os_release(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        result[k] = v
    return result


def parse_dpkg_status(text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for stanza in text.split("\n\n"):
        pkg_name = None
        version = None
        status = None
        for line in stanza.splitlines():
            if line.startswith("Package: "):
                pkg_name = line[len("Package: "):].strip()
            elif line.startswith("Version: "):
                version = line[len("Version: "):].strip()
            elif line.startswith("Status: "):
                status = line[len("Status: "):].strip()
        if pkg_name and version and status and "installed" in status.split():
            packages[pkg_name] = version
    return packages


def extract_iso_inventory(iso_path: pathlib.Path) -> dict[str, Any]:
    """
    Inspect a built Live ISO using unprivileged `xorriso` and `unsquashfs` inside
    a temporary directory and return its complete OS snapshot dictionary.
    """
    if not iso_path.is_file():
        raise FileNotFoundError(f"ISO file not found: {iso_path}")

    iso_size = iso_path.stat().st_size

    with tempfile.TemporaryDirectory(prefix="nebulous-iso-") as tmp_str:
        tmp_dir = pathlib.Path(tmp_str)
        squashfs_path = tmp_dir / "filesystem.squashfs"
        vmlinuz_path = tmp_dir / "vmlinuz"
        initrd_path = tmp_dir / "initrd.img"
        extract_dir = tmp_dir / "extract"

        # Extract /live/filesystem.squashfs, /live/vmlinuz, and /live/initrd.img via xorriso
        subprocess.run(
            [
                "xorriso",
                "-osirrox",
                "on",
                "-indev",
                str(iso_path),
                "-extract",
                "/live/filesystem.squashfs",
                str(squashfs_path),
                "-extract",
                "/live/vmlinuz",
                str(vmlinuz_path),
                "-extract",
                "/live/initrd.img",
                str(initrd_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        vmlinuz_sha256 = sha256_file(vmlinuz_path) if vmlinuz_path.is_file() else ""
        initrd_sha256 = sha256_file(initrd_path) if initrd_path.is_file() else ""

        # Required unsquashfs extraction paths per specification + targeted symlink/metadata paths
        extract_targets = [
            "var/lib/dpkg/status",
            "etc/os-release",
            "usr/lib/os-release",
            "usr/local/bin",
            "usr/local/sbin",
            "etc/calamares",
            "usr/lib/calamares/modules",
            "usr/share/doc/live-boot",
            "usr/bin/live-boot",
            *(f"usr/bin/{comp}" for comp in COSMIC_EPOCH_COMPONENTS),
            "boot",
        ]
        subprocess.run(
            ["unsquashfs", "-d", str(extract_dir), str(squashfs_path), *extract_targets],
            check=False,
            capture_output=True,
            text=True,
        )

        status_file = extract_dir / "var" / "lib" / "dpkg" / "status"
        if not status_file.is_file():
            raise RuntimeError(
                f"Failed to extract var/lib/dpkg/status from {iso_path} via unsquashfs"
            )

        # 1. Parse /etc/os-release (resolving symlink inside extract_dir if needed)
        os_release_entry = _resolve_in_extract_root(
            extract_dir, extract_dir / "etc" / "os-release"
        )
        if not os_release_entry.is_file():
            os_release_entry = extract_dir / "usr" / "lib" / "os-release"
        os_release_data: dict[str, str] = {}
        if os_release_entry.is_file():
            os_release_data = parse_os_release(
                os_release_entry.read_text(encoding="utf-8", errors="replace")
            )

        # 2. Parse var/lib/dpkg/status
        packages = parse_dpkg_status(
            status_file.read_text(encoding="utf-8", errors="replace")
        )

        # Detect live-boot extracted alongside dracut when not registered in dpkg/status
        if "live-boot" not in packages:
            lb_changelog = (
                extract_dir / "usr" / "share" / "doc" / "live-boot" / "changelog.gz"
            )
            lb_bin = extract_dir / "usr" / "bin" / "live-boot"
            if lb_changelog.is_file() and (lb_bin.is_file() or os.path.islink(lb_bin)):
                with gzip.open(lb_changelog, "rt", encoding="utf-8", errors="replace") as gz:
                    first_line = gz.readline().strip()
                m = re.search(r"\(([^)]+)\)", first_line)
                if m:
                    packages["live-boot"] = m.group(1).strip()

        # Map COSMIC component binaries provided by cosmic-epoch-monorepo
        if "cosmic-epoch-monorepo" in packages:
            epoch_ver = packages["cosmic-epoch-monorepo"]
            for comp in COSMIC_EPOCH_COMPONENTS:
                if comp not in packages:
                    comp_bin = extract_dir / "usr" / "bin" / comp
                    if comp_bin.is_file() or os.path.islink(comp_bin):
                        packages[comp] = epoch_ver

        # Build workspace crate version map for custom binaries
        crate_versions: dict[str, str] = {}
        ws_cargo = REPO_ROOT / "Cargo.toml"
        if ws_cargo.is_file():
            try:
                ws_data = load_toml_file(ws_cargo)
                for member in ws_data.get("workspace", {}).get("members", []):
                    mc = REPO_ROOT / member / "Cargo.toml"
                    if mc.is_file():
                        mpkg = load_toml_file(mc).get("package", {})
                        if mpkg.get("name") and mpkg.get("version"):
                            crate_versions[str(mpkg["name"])] = str(mpkg["version"])
            except Exception:
                pass

        # 3. Inspect /usr/local/bin/* and /usr/local/sbin/*
        custom_binaries: dict[str, dict[str, Any]] = {}
        for rel_dir in ("usr/local/bin", "usr/local/sbin"):
            dir_path = extract_dir / rel_dir
            if not dir_path.is_dir():
                continue
            for entry_name in sorted(os.listdir(dir_path)):
                raw_entry = dir_path / entry_name
                abs_in_os = f"/{rel_dir}/{entry_name}"
                is_link = os.path.islink(raw_entry)
                resolved = _resolve_in_extract_root(extract_dir, raw_entry)
                if not resolved.is_file():
                    continue
                digest = sha256_file(resolved)
                size_bytes = resolved.stat().st_size
                flags_found: list[str] = []
                detected_ver = (
                    crate_versions.get(entry_name)
                    or crate_versions.get(resolved.name)
                    or "0.1.0"
                )
                try:
                    raw_bytes = resolved.read_bytes()
                    if b"--pre-packages" in raw_bytes:
                        flags_found.append("--pre-packages")
                    text_probe = raw_bytes.decode("latin1")
                    ver_match = re.search(
                        rf"\b{re.escape(entry_name)}\s+v?(\d+\.\d+\.\d+(?:[-+~][0-9A-Za-z._-]+)?)\b",
                        text_probe,
                    )
                    if ver_match:
                        detected_ver = ver_match.group(1)
                except OSError:
                    pass
                custom_binaries[entry_name] = {
                    "path": abs_in_os,
                    "present": True,
                    "sha256": digest,
                    "size_bytes": size_bytes,
                    "version": detected_ver,
                    "is_symlink": is_link,
                    "flags": flags_found,
                }

        # 4. Inspect /etc/calamares/modules/* and /usr/lib/calamares/modules/*
        calamares_configs: list[str] = []
        calamares_presence: dict[str, bool] = {}
        cal_mod_dir = extract_dir / "etc" / "calamares" / "modules"
        if cal_mod_dir.is_dir():
            for fn in sorted(os.listdir(cal_mod_dir)):
                calamares_configs.append(fn)
                calamares_presence[fn] = True

        custom_calamares_modules: list[str] = []
        usr_cal_dir = extract_dir / "usr" / "lib" / "calamares" / "modules"
        if usr_cal_dir.is_dir():
            for fn in sorted(os.listdir(usr_cal_dir)):
                if (usr_cal_dir / fn).is_dir():
                    custom_calamares_modules.append(fn)
                    calamares_presence[fn] = True

        # 5. Inspect /boot
        boot_files: list[str] = []
        boot_dir = extract_dir / "boot"
        if boot_dir.is_dir():
            for fn in sorted(os.listdir(boot_dir)):
                p = boot_dir / fn
                if p.is_file() or os.path.islink(p):
                    boot_files.append(fn)

    return {
        "schema_version": 1,
        "iso": {
            "path": str(iso_path),
            "size_bytes": iso_size,
            "vmlinuz_sha256": vmlinuz_sha256,
            "initrd_sha256": initrd_sha256,
            "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        "os_release": os_release_data,
        "boot": {
            "files": boot_files,
        },
        "calamares_modules": {
            "configs": calamares_configs,
            "custom_modules": custom_calamares_modules,
            "presence": calamares_presence,
        },
        "custom_binaries": custom_binaries,
        "packages": dict(sorted(packages.items())),
    }


def write_golden_snapshot_toml(snapshot: dict[str, Any], output_path: pathlib.Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# ==============================================================================",
        "# NovOS Golden OS Snapshot Inventory (.nebulous/golden-snapshot.toml)",
        "# Generated by scripts/nebulous-ctl.py extract-golden",
        "# ==============================================================================",
        "",
        f"schema_version = {snapshot.get('schema_version', 1)}",
        "",
        "[iso]",
    ]
    for k, v in snapshot["iso"].items():
        lines.append(f"{_format_toml_key(k)} = {_format_toml_value(v)}")

    lines.append("")
    lines.append("[os_release]")
    for k, v in sorted(snapshot.get("os_release", {}).items()):
        lines.append(f"{_format_toml_key(k)} = {_format_toml_value(v)}")

    lines.append("")
    lines.append("[boot]")
    lines.append(f"files = {_format_toml_value(snapshot.get('boot', {}).get('files', []))}")

    lines.append("")
    lines.append("[calamares_modules]")
    cal = snapshot.get("calamares_modules", {})
    lines.append(f"configs = {_format_toml_value(cal.get('configs', []))}")
    lines.append(f"custom_modules = {_format_toml_value(cal.get('custom_modules', []))}")
    lines.append("")
    lines.append("[calamares_modules.presence]")
    for k, v in sorted(cal.get("presence", {}).items()):
        lines.append(f"{_format_toml_key(k)} = {_format_toml_value(v)}")

    lines.append("")
    lines.append("[custom_binaries]")
    for bin_name, meta in sorted(snapshot.get("custom_binaries", {}).items()):
        lines.append(f"{_format_toml_key(bin_name)} = {_format_toml_value(meta)}")

    lines.append("")
    lines.append("[packages]")
    for pkg_name, pkg_ver in sorted(snapshot.get("packages", {}).items()):
        lines.append(f"{_format_toml_key(pkg_name)} = {_format_toml_value(pkg_ver)}")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


# ==============================================================================
# 4. Asymmetric Forward-Only Variance Lock (`verify-variance`)
# ==============================================================================

def load_approved_exceptions(approvals_path: pathlib.Path) -> set[str]:
    """Return set of item/package/binary names with valid user approvals."""
    if not approvals_path.is_file():
        return set()
    try:
        data = load_toml_file(approvals_path)
    except Exception:
        return set()

    approved: set[str] = set()
    for entry in data.get("approvals", []):
        if isinstance(entry, dict) and entry.get("item_name") and entry.get("reason"):
            if entry.get("approved", True):
                approved.add(str(entry["item_name"]))
    return approved


def parse_custom_tools_manifest_yaml(yaml_path: pathlib.Path) -> list[dict[str, Any]]:
    """Minimal standard-library parser for custom-tools/manifest.yaml tool blocks."""
    if not yaml_path.is_file():
        return []
    tools: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in yaml_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- name:"):
            if current is not None:
                tools.append(current)
            name_val = stripped.split(":", 1)[1].strip().strip("\"'")
            current = {"name": name_val}
        elif current is not None and ":" in stripped and not stripped.startswith("-"):
            k, v = stripped.split(":", 1)
            k = k.strip()
            v = v.strip().strip("\"'")
            if v.lower() in ("true", "false"):
                current[k] = v.lower() == "true"
            else:
                current[k] = v
    if current is not None:
        tools.append(current)
    return tools


def _strip_comments(text: str) -> str:
    """Strip shell/YAML line comments (`# ...`) while preserving code/config tokens."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _has_token(text: str, token: str) -> bool:
    """Check whether `token` appears as a distinct package/binary/flag token in `text`."""
    pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(token)}(?![A-Za-z0-9_.-])"
    return re.search(pattern, text) is not None


def verify_repo_invariants(
    manifest: dict[str, Any],
    approved: set[str],
    repo_root: pathlib.Path = REPO_ROOT,
) -> list[dict[str, str]]:
    """Validate repo-level invariants against .nebulous/manifest.toml."""
    violations: list[dict[str, str]] = []

    # 1. Validate custom-tools/manifest.yaml enabled tools
    ct_yaml = repo_root / "custom-tools" / "manifest.yaml"
    tools = parse_custom_tools_manifest_yaml(ct_yaml)
    tools_by_name = {t.get("name"): t for t in tools if t.get("name")}
    if "novos-slippery-tiles" not in tools_by_name:
        if "novos-slippery-tiles" not in approved:
            violations.append(
                {
                    "type": "repo_custom_tool_missing",
                    "item": "novos-slippery-tiles",
                    "detail": "novos-slippery-tiles missing from custom-tools/manifest.yaml",
                }
            )
    elif not tools_by_name["novos-slippery-tiles"].get("enabled", False):
        if "novos-slippery-tiles" not in approved:
            violations.append(
                {
                    "type": "repo_custom_tool_disabled",
                    "item": "novos-slippery-tiles",
                    "detail": "novos-slippery-tiles is disabled in custom-tools/manifest.yaml",
                }
            )

    # 2. Validate calamares/modules/* presence and netinstall.yaml structure in repo
    cal_spec = manifest.get("calamares_modules", {})
    cal_dir = repo_root / "calamares" / "modules"
    for cfg in cal_spec.get("required_configs", []):
        if not (cal_dir / cfg).is_file() and cfg not in approved:
            violations.append(
                {
                    "type": "repo_calamares_config_missing",
                    "item": cfg,
                    "detail": f"Required Calamares config calamares/modules/{cfg} missing",
                }
            )
    for cmod in cal_spec.get("custom_modules", []):
        if not (cal_dir / cmod).is_dir() and cmod not in approved:
            violations.append(
                {
                    "type": "repo_calamares_module_missing",
                    "item": cmod,
                    "detail": f"Required Calamares custom module calamares/modules/{cmod} missing",
                }
            )

    netinstall_yaml = cal_dir / "netinstall.yaml"
    if netinstall_yaml.is_file() and "netinstall.yaml" not in approved:
        ni_text = _strip_comments(netinstall_yaml.read_text(encoding="utf-8", errors="replace"))
        if "- name:" not in ni_text or "packages:" not in ni_text:
            violations.append(
                {
                    "type": "repo_calamares_netinstall_invalid",
                    "item": "netinstall.yaml",
                    "detail": "calamares/modules/netinstall.yaml is missing profile groups or package lists",
                }
            )

    # 3. Validate Cargo.toml crate versions against manifest custom_binaries min_version
    workspace_cargo = repo_root / "Cargo.toml"
    if workspace_cargo.is_file():
        ws_data = load_toml_file(workspace_cargo)
        members = ws_data.get("workspace", {}).get("members", [])
        for member in members:
            member_cargo = repo_root / member / "Cargo.toml"
            if not member_cargo.is_file():
                violations.append(
                    {
                        "type": "repo_cargo_member_missing",
                        "item": member,
                        "detail": f"Workspace member Cargo.toml missing: {member_cargo}",
                    }
                )
                continue
            m_data = load_toml_file(member_cargo)
            pkg = m_data.get("package", {})
            crate_name = pkg.get("name", "")
            crate_ver = pkg.get("version", "0.0.0")
            bin_key = f"/usr/local/bin/{crate_name}"
            bin_spec = manifest.get("custom_binaries", {}).get(bin_key)
            if bin_spec:
                min_ver = bin_spec.get("min_version", "0.1.0")
                if compare_debian_versions(crate_ver, min_ver) < 0 and crate_name not in approved:
                    violations.append(
                        {
                            "type": "repo_cargo_version_downgrade",
                            "item": crate_name,
                            "detail": f"Crate {crate_name} version {crate_ver} < manifest min_version {min_ver}",
                        }
                    )

    # 4. Validate stage script package lists against .nebulous/manifest.toml (ignoring comments)
    stage_files = sorted(glob.glob(str(repo_root / "scripts" / "stages" / "*.sh")))
    combined_stages = _strip_comments(
        "\n".join(
            pathlib.Path(p).read_text(encoding="utf-8", errors="replace") for p in stage_files
        )
    )
    if ct_yaml.is_file():
        combined_stages += "\n" + _strip_comments(
            ct_yaml.read_text(encoding="utf-8", errors="replace")
        )

    for pkg_name in manifest.get("packages", {}).get("core", {}):
        if not _has_token(combined_stages, pkg_name) and pkg_name not in approved:
            violations.append(
                {
                    "type": "repo_stage_package_missing",
                    "item": pkg_name,
                    "detail": f"Core package '{pkg_name}' missing from scripts/stages/*.sh",
                }
            )

    for pkg_name in manifest.get("packages", {}).get("cosmic", {}):
        if (
            not _has_token(combined_stages, pkg_name)
            and not _has_token(combined_stages, "cosmic-epoch-monorepo")
            and pkg_name not in approved
        ):
            violations.append(
                {
                    "type": "repo_stage_package_missing",
                    "item": pkg_name,
                    "detail": f"COSMIC package '{pkg_name}' missing from scripts/stages/*.sh",
                }
            )

    for bin_path, bin_spec in manifest.get("custom_binaries", {}).items():
        bname = os.path.basename(bin_path)
        if (
            not _has_token(combined_stages, bname)
            and bin_path not in approved
            and bname not in approved
        ):
            violations.append(
                {
                    "type": "repo_stage_binary_missing",
                    "item": bin_path,
                    "detail": f"Custom binary '{bin_path}' missing from scripts/stages/*.sh",
                }
            )
        for req_flag in bin_spec.get("required_flags", []):
            if not _has_token(combined_stages, req_flag) and bin_path not in approved:
                violations.append(
                    {
                        "type": "repo_stage_flag_missing",
                        "item": f"{bin_path}:{req_flag}",
                        "detail": f"Required flag '{req_flag}' for '{bin_path}' missing from stage scripts",
                    }
                )

    return violations


def _index_custom_binaries(raw_bins: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index custom binaries by both basename and full path for uniform lookup."""
    indexed: dict[str, dict[str, Any]] = {}
    for key, val in raw_bins.items():
        if isinstance(val, dict):
            entry = dict(val)
        else:
            entry = {"version": str(val), "present": True}
        bname = os.path.basename(key)
        full_path = entry.get("path") or (key if key.startswith("/") else f"/usr/local/bin/{key}")
        entry["path"] = full_path
        indexed[bname] = entry
        indexed[full_path] = entry
    return indexed


def evaluate_asymmetric_variance(
    live_inventory: dict[str, Any],
    manifest: dict[str, Any],
    golden: dict[str, Any] | None,
    approved: set[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Compare `live_inventory` against `manifest` and `golden` using the
    Asymmetric Forward-Only Rule:
      - Missing/removed package or binary -> violation (unless in `approved`)
      - Older (<) version -> violation (unless in `approved`)
      - Equal (==) or newer (>) version -> PASS
    Returns (violations, upgrades).
    """
    violations: list[dict[str, str]] = []
    upgrades: list[dict[str, str]] = []

    live_pkgs = live_inventory.get("packages", {})
    live_bins = _index_custom_binaries(live_inventory.get("custom_binaries", {}))
    live_cal = live_inventory.get("calamares_modules", {}).get("presence", {})
    live_os = live_inventory.get("os_release", {})

    # 0. Check os-release metadata when present in live_inventory and required in manifest
    os_spec = manifest.get("os", {})
    if os_spec.get("required", False) and live_os and "os_release" not in approved:
        expected_id = os_spec.get("distro_id")
        if expected_id and live_os.get("ID") and live_os.get("ID") != expected_id:
            violations.append(
                {
                    "type": "manifest_os_id_mismatch",
                    "item": "os_release.ID",
                    "detail": f"/etc/os-release ID '{live_os.get('ID')}' != '{expected_id}'",
                }
            )
        min_os_ver = os_spec.get("distro_version")
        live_os_ver = live_os.get("VERSION_ID")
        if min_os_ver and live_os_ver and compare_debian_versions(live_os_ver, min_os_ver) < 0:
            violations.append(
                {
                    "type": "manifest_os_version_downgrade",
                    "item": "os_release.VERSION_ID",
                    "detail": f"/etc/os-release VERSION_ID {live_os_ver} < {min_os_ver}",
                }
            )

    # 1. Check against manifest.toml packages (core + cosmic)
    for group in ("core", "cosmic"):
        for pkg_name, spec in manifest.get("packages", {}).get(group, {}).items():
            if not spec.get("required", True):
                continue
            if pkg_name in approved:
                continue
            min_ver = spec.get("min_version", "0")
            if pkg_name not in live_pkgs:
                violations.append(
                    {
                        "type": "manifest_package_missing",
                        "item": pkg_name,
                        "detail": f"Required manifest package '{pkg_name}' is missing from live OS",
                    }
                )
            else:
                live_ver = live_pkgs[pkg_name]
                cmp_res = compare_debian_versions(live_ver, min_ver)
                if cmp_res < 0:
                    violations.append(
                        {
                            "type": "manifest_package_downgrade",
                            "item": pkg_name,
                            "detail": f"Package '{pkg_name}' version {live_ver} < manifest min_version {min_ver}",
                        }
                    )

    # 2. Check against manifest.toml custom_binaries
    for bin_path, spec in manifest.get("custom_binaries", {}).items():
        if not spec.get("required", True):
            continue
        bname = os.path.basename(bin_path)
        if bin_path in approved or bname in approved:
            continue
        min_ver = spec.get("min_version", "0.1.0")
        live_entry = live_bins.get(bin_path) or live_bins.get(bname)
        if not live_entry or not live_entry.get("present", True):
            violations.append(
                {
                    "type": "manifest_binary_missing",
                    "item": bin_path,
                    "detail": f"Required custom binary '{bin_path}' is missing from live OS",
                }
            )
        else:
            live_ver = live_entry.get("version", "0.1.0")
            if compare_debian_versions(live_ver, min_ver) < 0:
                violations.append(
                    {
                        "type": "manifest_binary_downgrade",
                        "item": bin_path,
                        "detail": f"Custom binary '{bin_path}' version {live_ver} < manifest min_version {min_ver}",
                    }
                )
            for req_flag in spec.get("required_flags", []):
                if "flags" in live_entry and req_flag not in live_entry["flags"]:
                    violations.append(
                        {
                            "type": "manifest_binary_flag_missing",
                            "item": bin_path,
                            "detail": f"Custom binary '{bin_path}' missing required flag '{req_flag}'",
                        }
                    )

    # 3. Check against manifest.toml calamares_modules
    cal_spec = manifest.get("calamares_modules", {})
    if cal_spec.get("required", False):
        for cfg in cal_spec.get("required_configs", []):
            if cfg not in approved and not live_cal.get(cfg, False):
                violations.append(
                    {
                        "type": "manifest_calamares_missing",
                        "item": cfg,
                        "detail": f"Required Calamares module config '{cfg}' missing from live OS",
                    }
                )
        for cmod in cal_spec.get("custom_modules", []):
            if cmod not in approved and not live_cal.get(cmod, False):
                violations.append(
                    {
                        "type": "manifest_calamares_custom_missing",
                        "item": cmod,
                        "detail": f"Required Calamares custom module '{cmod}' missing from live OS",
                    }
                )

    # 4. Check against golden-snapshot.toml (if present)
    if golden:
        golden_pkgs = golden.get("packages", {})
        for pkg_name, golden_ver in golden_pkgs.items():
            if pkg_name in approved:
                continue
            if pkg_name not in live_pkgs:
                violations.append(
                    {
                        "type": "golden_package_removed",
                        "item": pkg_name,
                        "detail": f"Package '{pkg_name}' (golden={golden_ver}) was removed from live OS",
                    }
                )
            else:
                live_ver = live_pkgs[pkg_name]
                cmp_res = compare_debian_versions(live_ver, golden_ver)
                if cmp_res < 0:
                    violations.append(
                        {
                            "type": "golden_package_downgrade",
                            "item": pkg_name,
                            "detail": f"Package '{pkg_name}' downgraded: live={live_ver} < golden={golden_ver}",
                        }
                    )
                elif cmp_res > 0:
                    upgrades.append(
                        {
                            "item": pkg_name,
                            "from_version": golden_ver,
                            "to_version": live_ver,
                        }
                    )

        golden_bins = golden.get("custom_binaries", {})
        for bin_key, golden_meta in golden_bins.items():
            bname = os.path.basename(bin_key)
            full_path = (
                golden_meta.get("path", bin_key)
                if isinstance(golden_meta, dict)
                else bin_key
            )
            if bin_key in approved or bname in approved or full_path in approved:
                continue
            live_entry = live_bins.get(bin_key) or live_bins.get(bname) or live_bins.get(full_path)
            if not live_entry or not live_entry.get("present", True):
                violations.append(
                    {
                        "type": "golden_binary_removed",
                        "item": bin_key,
                        "detail": f"Custom binary '{bin_key}' present in golden snapshot is missing from live OS",
                    }
                )
            else:
                golden_ver = (
                    golden_meta.get("version", "0.1.0")
                    if isinstance(golden_meta, dict)
                    else str(golden_meta)
                )
                live_ver = live_entry.get("version", "0.1.0")
                cmp_res = compare_debian_versions(live_ver, golden_ver)
                if cmp_res < 0:
                    violations.append(
                        {
                            "type": "golden_binary_downgrade",
                            "item": bin_key,
                            "detail": f"Custom binary '{bin_key}' downgraded: live={live_ver} < golden={golden_ver}",
                        }
                    )
                elif cmp_res > 0:
                    upgrades.append(
                        {
                            "item": bin_key,
                            "from_version": golden_ver,
                            "to_version": live_ver,
                        }
                    )

        golden_cal = golden.get("calamares_modules", {}).get("presence", {})
        for mod_name, was_present in golden_cal.items():
            if was_present and mod_name not in approved and not live_cal.get(mod_name, False):
                violations.append(
                    {
                        "type": "golden_calamares_removed",
                        "item": mod_name,
                        "detail": f"Calamares module '{mod_name}' present in golden snapshot is missing",
                    }
                )

    return violations, upgrades


# ==============================================================================
# 5. Loop Breaker & Tiered Item State Machine (`record-attempt`, `check-guardrail`)
# ==============================================================================

def normalize_error_fingerprint(error_summary: str, log_text: str = "") -> str:
    """Compute a deterministic 16-hex-char SHA256 fingerprint from normalized error text."""
    clean_summary = strip_ansi(error_summary).strip()
    clean_log = strip_ansi(log_text).strip()
    combined = (clean_summary or clean_log).lower()
    # Strip volatile timestamps, PIDs, hex addresses, and /tmp paths
    combined = re.sub(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:z|[+-]\d{2}:?\d{2})?\b", "<ts>", combined)
    combined = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", combined)
    combined = re.sub(r"/tmp/[a-z0-9_.-]+", "/tmp/<tmp>", combined)
    combined = re.sub(r"\bpid[=:\s]+\d+\b", "pid=<pid>", combined)
    combined = re.sub(r"\s+", " ", combined).strip()
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def compute_git_diff_fingerprint(repo_root: pathlib.Path = REPO_ROOT) -> tuple[str, list[str]]:
    """Return (diff_fingerprint, modified_files) from current git working tree."""
    try:
        diff_res = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        names_res = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        diff_text = diff_res.stdout.strip()
        modified = [line.strip() for line in names_res.stdout.splitlines() if line.strip()]
        if not diff_text:
            return "clean", modified
        fp = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:16]
        return fp, modified
    except OSError:
        return "clean", []


def write_item_toml(item_data: dict[str, Any], path: pathlib.Path) -> None:
    """Serialize a tiered Item.toml file preserving [item] and [[attempts]]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    item_sec = item_data.get("item", {})
    attempts = item_data.get("attempts")
    if attempts is None:
        attempts = item_sec.get("attempts", [])

    lines = [
        "# ==============================================================================",
        f"# Nebulous Tiered Work Item: {item_sec.get('id', 'item')}",
        "# ==============================================================================",
        "",
    ]

    if not attempts:
        lines.append("attempts = []")
        lines.append("")

    lines.extend(
        [
            "[item]",
            f"id = {_format_toml_value(item_sec.get('id', ''))}",
            f"phase_id = {_format_toml_value(item_sec.get('phase_id', ''))}",
            f"title = {_format_toml_value(item_sec.get('title', ''))}",
            f"status = {_format_toml_value(item_sec.get('status', 'in_progress'))}",
            f"loop_locked = {_format_toml_value(bool(item_sec.get('loop_locked', False)))}",
            f"retry_count = {_format_toml_value(int(item_sec.get('retry_count', 0)))}",
        ]
    )

    neg_list = item_sec.get("negative_constraints", [])
    if not neg_list:
        lines.append("negative_constraints = []")
    else:
        lines.append("negative_constraints = [")
        for nc in neg_list:
            lines.append(f"    {_format_toml_value(nc)},")
        lines.append("]")

    if not attempts:
        lines.append("attempts = []")
    else:
        for att in attempts:
            lines.append("")
            lines.append("[[attempts]]")
            for k in (
                "timestamp",
                "stage",
                "status",
                "error_summary",
                "error_fingerprint",
                "diff_fingerprint",
                "modified_files",
                "log_file",
            ):
                if k in att:
                    lines.append(f"{k} = {_format_toml_value(att[k])}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def record_item_attempt(
    item_path: pathlib.Path,
    stage: str,
    status: str,
    error_summary: str = "",
    log_file: str = "",
    warn_threshold: int = 2,
    hard_stop_threshold: int = 3,
    diff_override: tuple[str, list[str]] | None = None,
) -> int:
    """
    Record a build/test attempt in `item_path`, evaluate loop heuristics, and return
    EXIT_OK (0) or EXIT_LOOP_LOCK (3).
    """
    raw = load_toml_file(item_path)
    item_sec = dict(raw.get("item", {}))
    raw_attempts = raw.get("attempts")
    if raw_attempts is None:
        raw_attempts = item_sec.get("attempts", [])
    attempts: list[dict[str, Any]] = list(raw_attempts)

    log_tail = ""
    if log_file and os.path.isfile(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as lf:
                lines = lf.readlines()
                log_tail = strip_ansi("".join(lines[-40:]))
        except OSError:
            pass

    clean_err_summary = strip_ansi(error_summary).strip()
    if not clean_err_summary and log_tail.strip():
        non_empty = [ln.strip() for ln in log_tail.splitlines() if ln.strip()]
        err_lines = [
            ln
            for ln in non_empty
            if re.search(r"(?i)\b(error|fatal|panic|fail|failed|exception)\b", ln)
        ]
        clean_err_summary = err_lines[-1] if err_lines else non_empty[-1]

    summary_text = clean_err_summary
    err_fp = normalize_error_fingerprint(summary_text, log_tail) if status == "fail" else "none"
    diff_fp, mod_files = diff_override if diff_override is not None else compute_git_diff_fingerprint()

    prior_failed = [a for a in attempts if a.get("status") == "fail"]
    same_diff_prior = any(
        a.get("diff_fingerprint") == diff_fp and diff_fp not in ("", "none")
        for a in prior_failed
    )

    attempt_entry: dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "stage": stage,
        "status": status,
        "error_summary": summary_text,
        "error_fingerprint": err_fp,
        "diff_fingerprint": diff_fp,
        "modified_files": mod_files,
        "log_file": log_file or "",
    }
    attempts.append(attempt_entry)

    neg_constraints: list[str] = list(item_sec.get("negative_constraints", []))

    if status == "pass":
        item_sec["retry_count"] = 0
        if not item_sec.get("loop_locked", False):
            item_sec["status"] = "in_progress"
    else:
        retry_count = int(item_sec.get("retry_count", 0)) + 1
        item_sec["retry_count"] = retry_count

        same_err_count = sum(
            1 for a in attempts if a.get("status") == "fail" and a.get("error_fingerprint") == err_fp
        )

        if same_err_count >= warn_threshold or same_diff_prior:
            mod_str = ", ".join(mod_files) if mod_files else "working-tree"
            neg_prompt = (
                f"LOOP DETECTED [fingerprint={err_fp}]: Approach in [{mod_str}] failed "
                f"repeatedly with '{summary_text}'. DO NOT re-apply the same diff or retry "
                f"without changing root-cause architecture. Dropping packages or downgrading "
                f"versions is strictly forbidden."
            )
            if neg_prompt not in neg_constraints:
                neg_constraints.append(neg_prompt)

        item_sec["negative_constraints"] = neg_constraints

        if retry_count >= hard_stop_threshold:
            item_sec["status"] = "blocked_loop_hard_stop"
            item_sec["loop_locked"] = True
            write_item_toml({"item": item_sec, "attempts": attempts}, item_path)
            return EXIT_LOOP_LOCK

    item_sec["negative_constraints"] = neg_constraints
    write_item_toml({"item": item_sec, "attempts": attempts}, item_path)
    return EXIT_LOOP_LOCK if item_sec.get("loop_locked", False) else EXIT_OK


# ==============================================================================
# 6. Host-Only User Approval Commands (`bless-golden`, `approve-variance`, `unlock-loop`)
# ==============================================================================

def enforce_host_user_confirmation(token: str | None) -> None:
    """Refuse execution if invoked by an agent or without explicit user confirmation token."""
    if os.environ.get("ANTIGRAVITY_AGENT") == "1":
        print(
            "HARD_STOP_PERMISSION_DENIED: Host-only approval command cannot be run when ANTIGRAVITY_AGENT=1.",
            file=sys.stderr,
        )
        sys.exit(EXIT_PERMISSION_DENIED)
    if not token or not token.strip():
        print(
            "HARD_STOP_PERMISSION_DENIED: --user-confirmed-token is required for host-only approval commands.",
            file=sys.stderr,
        )
        sys.exit(EXIT_PERMISSION_DENIED)


# ==============================================================================
# 7. Built-In Unit Test Suite (`--self-test`)
# ==============================================================================

class NebulousCtlSelfTests(unittest.TestCase):
    def test_debian_version_comparison_parity(self) -> None:
        cases = [
            ("1.0", "1.0", 0),
            ("1:20250815", "1:20230131", 1),
            ("1:20230131", "1:20250815", -1),
            ("262.1-1", "255", 1),
            ("0.10.6-1.3+b1", "0.10", 1),
            ("1.11.0-1.build197", "0.1.0", 1),
            ("1.0~alpha1", "1.0", -1),
            ("1.0~beta2", "1.0~beta1", 1),
            ("2:1.0-1", "1:9.9-9", 1),
        ]
        for v1, v2, expected in cases:
            self.assertEqual(
                compare_debian_versions_pure(v1, v2),
                expected,
                f"Pure compare failed for {v1} vs {v2}",
            )
            self.assertEqual(
                compare_debian_versions(v1, v2, use_dpkg=True),
                expected,
                f"dpkg wrapper compare failed for {v1} vs {v2}",
            )

    def test_asymmetric_variance_upgrade_downgrade_and_removal(self) -> None:
        manifest = {
            "packages": {
                "core": {
                    "systemd-sysv": {"required": True, "min_version": "255"},
                    "dracut": {"required": True, "min_version": "060"},
                },
                "cosmic": {
                    "cosmic-comp": {"required": True, "min_version": "0.1.0"},
                },
            },
            "custom_binaries": {
                "/usr/local/bin/novos-slippery-tiles": {
                    "required": True,
                    "min_version": "0.1.0",
                },
                "/usr/local/bin/novos-setup": {
                    "required": True,
                    "min_version": "0.1.0",
                    "required_flags": ["--pre-packages"],
                },
            },
            "calamares_modules": {
                "required": True,
                "required_configs": ["netinstall.yaml"],
                "custom_modules": ["limine-bootloader"],
            },
        }
        golden = {
            "packages": {
                "systemd-sysv": "260-1",
                "dracut": "110-1",
                "cosmic-comp": "1.0.0-1",
                "btrfs-progs": "6.8-1",
            },
            "custom_binaries": {
                "novos-slippery-tiles": {
                    "path": "/usr/local/bin/novos-slippery-tiles",
                    "present": True,
                    "version": "0.1.0",
                },
                "novos-setup": {
                    "path": "/usr/local/bin/novos-setup",
                    "present": True,
                    "version": "0.1.0",
                    "flags": ["--pre-packages"],
                },
            },
            "calamares_modules": {
                "presence": {"netinstall.yaml": True, "limine-bootloader": True}
            },
        }

        # Case A: Forward upgrade (systemd-sysv 262.1-1 > 260-1) -> PASS (0 violations)
        live_upgrade = {
            "packages": {
                "systemd-sysv": "262.1-1",
                "dracut": "110-1",
                "cosmic-comp": "1.11.0-1",
                "btrfs-progs": "6.8-1",
            },
            "custom_binaries": {
                "novos-slippery-tiles": {
                    "path": "/usr/local/bin/novos-slippery-tiles",
                    "present": True,
                    "version": "0.2.0",
                },
                "novos-setup": {
                    "path": "/usr/local/bin/novos-setup",
                    "present": True,
                    "version": "0.1.0",
                    "flags": ["--pre-packages"],
                },
            },
            "calamares_modules": {
                "presence": {"netinstall.yaml": True, "limine-bootloader": True}
            },
        }
        violations, upgrades = evaluate_asymmetric_variance(
            live_upgrade, manifest, golden, set()
        )
        self.assertEqual(len(violations), 0)
        self.assertTrue(len(upgrades) >= 2)

        # Case B: Version downgrade (dracut 105-1 < golden 110-1) -> HARD_STOP_VARIANCE_LOCK
        live_downgrade = {
            "packages": {
                "systemd-sysv": "262.1-1",
                "dracut": "105-1",
                "cosmic-comp": "1.0.0-1",
                "btrfs-progs": "6.8-1",
            },
            "custom_binaries": live_upgrade["custom_binaries"],
            "calamares_modules": live_upgrade["calamares_modules"],
        }
        violations_dg, _ = evaluate_asymmetric_variance(
            live_downgrade, manifest, golden, set()
        )
        self.assertTrue(
            any(v["type"] == "golden_package_downgrade" and v["item"] == "dracut" for v in violations_dg)
        )

        # Case C: Package drop (btrfs-progs removed) -> HARD_STOP_VARIANCE_LOCK
        live_drop = {
            "packages": {
                "systemd-sysv": "262.1-1",
                "dracut": "110-1",
                "cosmic-comp": "1.0.0-1",
            },
            "custom_binaries": live_upgrade["custom_binaries"],
            "calamares_modules": live_upgrade["calamares_modules"],
        }
        violations_drop, _ = evaluate_asymmetric_variance(
            live_drop, manifest, golden, set()
        )
        self.assertTrue(
            any(v["type"] == "golden_package_removed" and v["item"] == "btrfs-progs" for v in violations_drop)
        )

        # Case D: Approved exception permits removal
        violations_approved, _ = evaluate_asymmetric_variance(
            live_drop, manifest, golden, {"btrfs-progs"}
        )
        self.assertEqual(len(violations_approved), 0)

        # Case E: Empty flags (`flags: []`) and empty calamares (`presence: {}`) caught
        live_missing_flags_and_cal = {
            "packages": live_upgrade["packages"],
            "custom_binaries": {
                "novos-slippery-tiles": live_upgrade["custom_binaries"]["novos-slippery-tiles"],
                "novos-setup": {
                    "path": "/usr/local/bin/novos-setup",
                    "present": True,
                    "version": "0.1.0",
                    "flags": [],
                },
            },
            "calamares_modules": {"presence": {}},
        }
        violations_fc, _ = evaluate_asymmetric_variance(
            live_missing_flags_and_cal, manifest, None, set()
        )
        self.assertTrue(
            any(v["type"] == "manifest_binary_flag_missing" for v in violations_fc)
        )
        self.assertTrue(
            any(v["type"] == "manifest_calamares_missing" for v in violations_fc)
        )

    def test_loop_breaker_negative_prompt_and_hard_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            item_path = pathlib.Path(tmp) / "item-test.toml"
            log_path = pathlib.Path(tmp) / "build.log"
            log_path.write_text(
                "\x1b[0;32m[INFO]\x1b[0m Starting stage 3\n"
                "\x1b[0;31m[ERROR] E: Unable to locate package foo\x1b[0m\n",
                encoding="utf-8",
            )
            write_item_toml(
                {
                    "item": {
                        "id": "item-test",
                        "phase_id": "phase-01",
                        "title": "Test Item",
                        "status": "in_progress",
                        "loop_locked": False,
                        "retry_count": 0,
                        "negative_constraints": [],
                    },
                    "attempts": [],
                },
                item_path,
            )

            # Verify empty Item.toml exposes attempts=[] at top-level and under [item]
            d0 = load_toml_file(item_path)
            self.assertEqual(d0.get("attempts"), [])
            self.assertEqual(d0["item"].get("attempts"), [])

            # Attempt 1: fail with ANSI log -> retry_count=1, valid TOML (no raw \x1b)
            rc1 = record_item_attempt(
                item_path,
                stage="build",
                status="fail",
                log_file=str(log_path),
                diff_override=("diff_a", ["scripts/stages/03-core-system.sh"]),
            )
            self.assertEqual(rc1, EXIT_OK)
            d1 = load_toml_file(item_path)
            self.assertEqual(d1["item"]["retry_count"], 1)
            self.assertEqual(len(d1["item"]["negative_constraints"]), 0)
            self.assertNotIn("\x1b", d1["attempts"][0]["error_summary"])

            # Attempt 2: same error -> retry_count=2, negative prompt injected!
            rc2 = record_item_attempt(
                item_path,
                stage="build",
                status="fail",
                error_summary="\x1b[0;31m[ERROR] E: Unable to locate package foo\x1b[0m",
                diff_override=("diff_b", ["scripts/stages/03-core-system.sh"]),
            )
            self.assertEqual(rc2, EXIT_OK)
            d2 = load_toml_file(item_path)
            self.assertEqual(d2["item"]["retry_count"], 2)
            self.assertEqual(len(d2["item"]["negative_constraints"]), 1)
            self.assertIn("LOOP DETECTED [fingerprint=", d2["item"]["negative_constraints"][0])
            self.assertIn(
                "Dropping packages or downgrading versions is strictly forbidden.",
                d2["item"]["negative_constraints"][0],
            )

            # Attempt 3: 3rd consecutive failure -> HARD_STOP_LOOP_LOCK (exit code 3)
            rc3 = record_item_attempt(
                item_path,
                stage="build",
                status="fail",
                error_summary="[ERROR] E: Unable to locate package foo",
                diff_override=("diff_c", ["scripts/stages/03-core-system.sh"]),
            )
            self.assertEqual(rc3, EXIT_LOOP_LOCK)
            d3 = load_toml_file(item_path)
            self.assertTrue(d3["item"]["loop_locked"])
            self.assertEqual(d3["item"]["status"], "blocked_loop_hard_stop")

            # Verify check-guardrail CLI returns EXIT_LOOP_LOCK (3) when loop_locked=true
            rc_chk = main(["check-guardrail", "--item", str(item_path), "--json"])
            self.assertEqual(rc_chk, EXIT_LOOP_LOCK)

    def test_multi_hop_relative_and_absolute_symlink_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "etc" / "alternatives").mkdir(parents=True)
            (root / "usr" / "lib").mkdir(parents=True)
            real_target = root / "usr" / "lib" / "os-release-real"
            real_target.write_text('ID="novos"\n', encoding="utf-8")
            # Hop 1: etc/os-release -> ../etc/alternatives/os-release (relative)
            # Hop 2: etc/alternatives/os-release -> /usr/lib/os-release-real (absolute inside root)
            os.symlink("/usr/lib/os-release-real", root / "etc" / "alternatives" / "os-release")
            os.symlink("../etc/alternatives/os-release", root / "etc" / "os-release")
            resolved = _resolve_in_extract_root(root, root / "etc" / "os-release")
            self.assertEqual(resolved, real_target)
            self.assertTrue(resolved.is_file())

    def test_host_only_approval_commands_blocked_for_agent(self) -> None:
        old_val = os.environ.get("ANTIGRAVITY_AGENT")
        try:
            os.environ["ANTIGRAVITY_AGENT"] = "1"
            with self.assertRaises(SystemExit) as cm:
                enforce_host_user_confirmation("valid-token")
            self.assertEqual(cm.exception.code, EXIT_PERMISSION_DENIED)

            os.environ.pop("ANTIGRAVITY_AGENT", None)
            with self.assertRaises(SystemExit) as cm2:
                enforce_host_user_confirmation("")
            self.assertEqual(cm2.exception.code, EXIT_PERMISSION_DENIED)
        finally:
            if old_val is not None:
                os.environ["ANTIGRAVITY_AGENT"] = old_val
            else:
                os.environ.pop("ANTIGRAVITY_AGENT", None)



# ==============================================================================
# 8. CLI Entrypoint & Subcommand Handlers
# ==============================================================================

def cmd_extract_golden(args: argparse.Namespace) -> int:
    iso_path = pathlib.Path(args.iso).resolve()
    output_path = pathlib.Path(args.output).resolve()
    snapshot = extract_iso_inventory(iso_path)
    write_golden_snapshot_toml(snapshot, output_path)
    print(
        f"GOLDEN_SNAPSHOT_EXTRACTED: packages={len(snapshot['packages'])} "
        f"custom_binaries={len(snapshot['custom_binaries'])} output={output_path}"
    )
    return EXIT_OK


def cmd_verify_variance(args: argparse.Namespace) -> int:
    manifest_path = pathlib.Path(args.manifest).resolve()
    golden_path = pathlib.Path(args.golden).resolve()
    approvals_path = pathlib.Path(args.approvals).resolve()

    manifest = load_toml_file(manifest_path)
    approved = load_approved_exceptions(approvals_path)

    violations = verify_repo_invariants(manifest, approved, REPO_ROOT)
    upgrades: list[dict[str, str]] = []
    checked_pkgs = 0
    checked_bins = 0

    if args.iso and not args.repo_only:
        iso_path = pathlib.Path(args.iso).resolve()
        if not iso_path.is_file():
            violations.append(
                {
                    "type": "iso_missing",
                    "item": str(iso_path),
                    "detail": f"Target ISO does not exist: {iso_path}",
                }
            )
        else:
            live_inv = extract_iso_inventory(iso_path)
            golden = load_toml_file(golden_path) if golden_path.is_file() else None
            iso_violations, upgrades = evaluate_asymmetric_variance(
                live_inv, manifest, golden, approved
            )
            violations.extend(iso_violations)
            checked_pkgs = len(live_inv.get("packages", {}))
            checked_bins = len(live_inv.get("custom_binaries", {}))

    exit_code = EXIT_VARIANCE_LOCK if violations else EXIT_OK
    status_str = "HARD_STOP_VARIANCE_LOCK" if violations else "PASS"

    report = {
        "status": status_str,
        "exit_code": exit_code,
        "repo_only": bool(args.repo_only or not args.iso),
        "checked_packages_count": checked_pkgs,
        "checked_binaries_count": checked_bins,
        "upgraded_count": len(upgrades),
        "upgrades": upgrades,
        "violations": violations,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"VARIANCE_CHECK [{status_str}]: violations={len(violations)} "
            f"upgrades={len(upgrades)} checked_packages={checked_pkgs} checked_binaries={checked_bins}"
        )
        for v in violations:
            print(f"  [VIOLATION] {v['type']} ({v['item']}): {v['detail']}", file=sys.stderr)

    return exit_code


def cmd_record_attempt(args: argparse.Namespace) -> int:
    project_cfg = load_toml_file(DEFAULT_PROJECT_TOML) if DEFAULT_PROJECT_TOML.is_file() else {}
    guardrails = project_cfg.get("guardrails", {})
    warn_thresh = int(guardrails.get("loop_repeat_warn_threshold", 2))
    stop_thresh = int(guardrails.get("loop_max_retries_hard_stop", 3))

    item_path = pathlib.Path(args.item).resolve()
    rc = record_item_attempt(
        item_path=item_path,
        stage=args.stage,
        status=args.status,
        error_summary=args.error_summary or "",
        log_file=args.log_file or "",
        warn_threshold=warn_thresh,
        hard_stop_threshold=stop_thresh,
    )
    if rc == EXIT_LOOP_LOCK:
        print(f"HARD_STOP_LOOP_LOCK: {item_path} exceeded max retries ({stop_thresh})", file=sys.stderr)
    else:
        print(f"ATTEMPT_RECORDED: item={item_path.name} stage={args.stage} status={args.status}")
    return rc


def cmd_check_guardrail(args: argparse.Namespace) -> int:
    item_path = pathlib.Path(args.item).resolve()
    data = load_toml_file(item_path)
    item_sec = data.get("item", {})
    loop_locked = bool(item_sec.get("loop_locked", False))
    retry_count = int(item_sec.get("retry_count", 0))
    neg_constraints = list(item_sec.get("negative_constraints", []))

    manifest = load_toml_file(DEFAULT_MANIFEST_TOML) if DEFAULT_MANIFEST_TOML.is_file() else {}
    approved = load_approved_exceptions(DEFAULT_APPROVALS_TOML)
    repo_violations = verify_repo_invariants(manifest, approved, REPO_ROOT) if manifest else []

    variance_status = "HARD_STOP_VARIANCE_LOCK" if repo_violations else "PASS"
    payload = {
        "item_id": item_sec.get("id", ""),
        "status": item_sec.get("status", ""),
        "loop_locked": loop_locked,
        "retry_count": retry_count,
        "negative_constraints": neg_constraints,
        "variance_status": variance_status,
        "variance_violations": repo_violations,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"GUARDRAIL_STATE: item={payload['item_id']} status={payload['status']} "
            f"loop_locked={loop_locked} retry_count={retry_count} "
            f"negative_constraints={len(neg_constraints)} variance={variance_status}"
        )
        for nc in neg_constraints:
            print(f"  [NEGATIVE_CONSTRAINT] {nc}")

    if loop_locked:
        return EXIT_LOOP_LOCK
    if repo_violations:
        return EXIT_VARIANCE_LOCK
    return EXIT_OK


def cmd_bless_golden(args: argparse.Namespace) -> int:
    enforce_host_user_confirmation(args.user_confirmed_token)
    return cmd_extract_golden(args)


def cmd_approve_variance(args: argparse.Namespace) -> int:
    enforce_host_user_confirmation(args.user_confirmed_token)
    approvals_path = pathlib.Path(args.approvals).resolve()
    existing: list[dict[str, Any]] = []
    if approvals_path.is_file():
        existing = list(load_toml_file(approvals_path).get("approvals", []))
    existing.append(
        {
            "item_name": args.item_name,
            "reason": args.reason,
            "approved": True,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    lines = [
        "# ==============================================================================",
        "# Nebulous User-Approved Variance Exceptions (.nebulous/approvals.toml)",
        "# ==============================================================================",
        "",
    ]
    for entry in existing:
        lines.append("[[approvals]]")
        for k, v in entry.items():
            lines.append(f"{k} = {_format_toml_value(v)}")
        lines.append("")
    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    approvals_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"VARIANCE_APPROVED: item={args.item_name}")
    return EXIT_OK


def cmd_unlock_loop(args: argparse.Namespace) -> int:
    enforce_host_user_confirmation(args.user_confirmed_token)
    item_path = pathlib.Path(args.item).resolve()
    data = load_toml_file(item_path)
    item_sec = dict(data.get("item", {}))
    item_sec["loop_locked"] = False
    item_sec["retry_count"] = 0
    item_sec["status"] = "in_progress"
    write_item_toml({"item": item_sec, "attempts": data.get("attempts", [])}, item_path)
    print(f"LOOP_UNLOCKED: item={item_path}")
    return EXIT_OK


def run_self_tests() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NebulousCtlSelfTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return EXIT_OK if result.wasSuccessful() else EXIT_ERROR


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nebulous-ctl.py",
        description="NovOS Nebulous Golden OS Snapshot, Asymmetric Variance Lock & Loop Breaker CLI",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in unit tests for asymmetric version comparison and loop breaker",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    # extract-golden
    p_ext = subparsers.add_parser("extract-golden", help="Extract golden snapshot from Live ISO")
    p_ext.add_argument("--iso", required=True, help="Path to built Live ISO")
    p_ext.add_argument(
        "--output",
        default=str(DEFAULT_GOLDEN_TOML),
        help="Output path for golden-snapshot.toml",
    )

    # verify-variance
    p_ver = subparsers.add_parser(
        "verify-variance",
        help="Validate repo invariants and optional Live ISO against manifest & golden snapshot",
    )
    p_ver.add_argument("--iso", help="Path to built Live ISO to inspect")
    p_ver.add_argument(
        "--repo-only",
        action="store_true",
        help="Only check repository-level manifest invariants",
    )
    p_ver.add_argument("--json", action="store_true", help="Emit JSON report")
    p_ver.add_argument("--manifest", default=str(DEFAULT_MANIFEST_TOML))
    p_ver.add_argument("--golden", default=str(DEFAULT_GOLDEN_TOML))
    p_ver.add_argument("--approvals", default=str(DEFAULT_APPROVALS_TOML))

    # record-attempt
    p_rec = subparsers.add_parser(
        "record-attempt",
        help="Record a build/test attempt and trigger loop breaker heuristics on repeated failures",
    )
    p_rec.add_argument("--item", required=True, help="Path to tiered Item.toml file")
    p_rec.add_argument(
        "--stage",
        required=True,
        choices=["build", "live-boot", "install-e2e"],
        help="Verification stage",
    )
    p_rec.add_argument(
        "--status",
        required=True,
        choices=["pass", "fail"],
        help="Attempt outcome",
    )
    p_rec.add_argument("--error-summary", default="", help="Brief error summary")
    p_rec.add_argument("--log-file", default="", help="Path to log file")

    # check-guardrail
    p_chk = subparsers.add_parser(
        "check-guardrail",
        help="Check loop lock, retry count, negative constraints, and variance status",
    )
    p_chk.add_argument("--item", required=True, help="Path to tiered Item.toml file")
    p_chk.add_argument("--json", action="store_true", help="Emit JSON output")

    # Host-only user approval commands
    p_bless = subparsers.add_parser(
        "bless-golden",
        help="Host-only user command to bless a new golden snapshot",
    )
    p_bless.add_argument("--iso", required=True, help="Path to built Live ISO")
    p_bless.add_argument("--output", default=str(DEFAULT_GOLDEN_TOML))
    p_bless.add_argument("--user-confirmed-token", default="")

    p_app = subparsers.add_parser(
        "approve-variance",
        help="Host-only user command to approve a specific package/tool removal or downgrade",
    )
    p_app.add_argument("--item-name", required=True, help="Package or tool name")
    p_app.add_argument("--reason", required=True, help="Reason for exception")
    p_app.add_argument("--approvals", default=str(DEFAULT_APPROVALS_TOML))
    p_app.add_argument("--user-confirmed-token", default="")

    p_unl = subparsers.add_parser(
        "unlock-loop",
        help="Host-only user command to reset a loop-locked Item.toml",
    )
    p_unl.add_argument("--item", required=True, help="Path to tiered Item.toml file")
    p_unl.add_argument("--user-confirmed-token", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_tests()

    if args.subcommand == "extract-golden":
        return cmd_extract_golden(args)
    if args.subcommand == "verify-variance":
        return cmd_verify_variance(args)
    if args.subcommand == "record-attempt":
        return cmd_record_attempt(args)
    if args.subcommand == "check-guardrail":
        return cmd_check_guardrail(args)
    if args.subcommand == "bless-golden":
        return cmd_bless_golden(args)
    if args.subcommand == "approve-variance":
        return cmd_approve_variance(args)
    if args.subcommand == "unlock-loop":
        return cmd_unlock_loop(args)

    parser.print_help()
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
