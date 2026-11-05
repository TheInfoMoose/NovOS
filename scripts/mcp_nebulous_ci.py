#!/usr/bin/env python3
"""
NovOS Nebulous CI/CD Stdio MCP Server (scripts/mcp_nebulous_ci.py)
==================================================================
Implements a standard Model Context Protocol (MCP) JSON-RPC 2.0 server over
stdio (`initialize`, `notifications/initialized`, `ping`, `tools/list`,
`tools/call`) plus a built-in `--self-test` suite.

Exposes 6 typed MCP tools for autonomous, guardrailed CI/CD orchestration:
  1. `nebulous_get_status`
  2. `nebulous_verify_variance`
  3. `nebulous_run_docker_build`
  4. `nebulous_run_qemu_test`
  5. `nebulous_record_diagnostic`
  6. `nebulous_consult_local_llm`
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
import urllib.error
import urllib.request
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_VARIANCE_LOCK = 2
EXIT_LOOP_LOCK = 3

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "nebulous_ci"
SERVER_VERSION = "1.0.0"

HARD_STOP_VARIANCE_INSTRUCTION = (
    "HARD STOP: Package/component removal or version downgrade detected. "
    "You are strictly prohibited from dropping items or reverting versions "
    "without explicit user approval. Halt immediately and ask the user."
)

HARD_STOP_LOOP_INSTRUCTION = (
    "HARD STOP: Item is loop-locked after repeated failures (retry limit reached). "
    "Automated retries are blocked. Halt immediately and ask the user to review "
    "or unlock the item via `nebulous-ctl.py unlock-loop`."
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
NEBULOUS_CTL = REPO_ROOT / "scripts" / "nebulous-ctl.py"
TEST_DOCKER_QEMU = REPO_ROOT / "scripts" / "test-docker-qemu.py"
DOCKER_COMPOSE_CI = REPO_ROOT / "docker" / "docker-compose.ci.yml"
DEFAULT_PROJECT_TOML = REPO_ROOT / ".nebulous" / "project.toml"
DEFAULT_MANIFEST_TOML = REPO_ROOT / ".nebulous" / "manifest.toml"
DEFAULT_GOLDEN_TOML = REPO_ROOT / ".nebulous" / "golden-snapshot.toml"
DEFAULT_APPROVALS_TOML = REPO_ROOT / ".nebulous" / "approvals.toml"
DEFAULT_ISO_PATH = REPO_ROOT / "build" / "NovOS-sid-amd64.iso"
DEFAULT_ITEM_TOML = REPO_ROOT / ".nebulous" / "items" / "item-001-docker-qemu-ci.toml"
DEFAULT_LLM_ENDPOINT = os.environ.get("NOVOS_LLM_ENDPOINT", "http://127.0.0.1:8081/v1")
DEFAULT_LLM_MODEL = os.environ.get("NOVOS_LLM_MODEL", "Qwen3-14B-Q4_K_M.gguf")
LOGS_DIR = REPO_ROOT / "logs"

ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


# ==============================================================================
# 1. TOML & Helper Utilities
# ==============================================================================

def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from log and diagnostic strings."""
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
        return "[" + ", ".join(_format_toml_value(x) for x in val) + "]"
    if isinstance(val, dict):
        parts = []
        for k, v in val.items():
            key_str = (
                k
                if re.match(r"^[A-Za-z0-9_-]+$", str(k))
                else f'"{_toml_escape(str(k))}"'
            )
            parts.append(f"{key_str} = {_format_toml_value(v)}")
        return "{ " + ", ".join(parts) + " }"
    return f'"{_toml_escape(str(val))}"'


def load_toml_file(path: pathlib.Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def write_item_toml_with_diagnostics(item_data: dict[str, Any], path: pathlib.Path) -> None:
    """Serialize a tiered Item.toml file preserving [item], [[attempts]], and [[diagnostics]]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    item_sec = item_data.get("item", {})
    attempts = item_data.get("attempts")
    if attempts is None:
        attempts = item_sec.get("attempts", [])
    diagnostics = item_data.get("diagnostics")
    if diagnostics is None:
        diagnostics = item_sec.get("diagnostics", [])

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

    for diag in diagnostics:
        lines.append("")
        lines.append("[[diagnostics]]")
        for k in (
            "timestamp",
            "hypothesis",
            "root_cause",
            "subsystem",
            "recommended_fix",
            "violates_negative_constraints",
            "llm_model",
            "diff_fingerprint",
            "proposed_files",
            "prior_failed_overlap",
        ):
            if k in diag:
                lines.append(f"{k} = {_format_toml_value(diag[k])}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def compute_diff_fingerprint(
    repo_root: pathlib.Path,
    proposed_diff: str | None = None,
    proposed_files: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Compute a 16-hex-char SHA256 fingerprint for a proposed diff or current git working tree."""
    if proposed_diff is not None and proposed_diff.strip():
        clean_diff = proposed_diff.strip()
        fp = hashlib.sha256(clean_diff.encode("utf-8")).hexdigest()[:16]
        files = list(proposed_files) if proposed_files else []
        if not files:
            for line in clean_diff.splitlines():
                if line.startswith("+++ b/"):
                    files.append(line[len("+++ b/") :].strip())
        return fp, files

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
        mod_files = [
            line.strip() for line in names_res.stdout.splitlines() if line.strip()
        ]
        if proposed_files:
            for pf in proposed_files:
                if pf not in mod_files:
                    mod_files.append(pf)
        if not diff_text:
            return "clean", mod_files
        fp = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:16]
        return fp, mod_files
    except OSError:
        return "clean", list(proposed_files or [])


# ==============================================================================
# 2. Nebulous CI/CD MCP Server Implementation
# ==============================================================================

class NebulousCiMcpServer:
    """Model Context Protocol (MCP) JSON-RPC 2.0 Server for Nebulous CI/CD Guardrails."""

    def __init__(
        self,
        repo_root: pathlib.Path = REPO_ROOT,
        project_toml: pathlib.Path | None = None,
        nebulous_ctl: pathlib.Path | None = None,
        test_docker_qemu: pathlib.Path | None = None,
        docker_compose_ci: pathlib.Path | None = None,
        runner: Any = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.project_toml = (
            project_toml.resolve()
            if project_toml is not None
            else (self.repo_root / ".nebulous" / "project.toml")
        )
        self.nebulous_ctl = (
            nebulous_ctl.resolve()
            if nebulous_ctl is not None
            else (self.repo_root / "scripts" / "nebulous-ctl.py")
        )
        self.test_docker_qemu = (
            test_docker_qemu.resolve()
            if test_docker_qemu is not None
            else (self.repo_root / "scripts" / "test-docker-qemu.py")
        )
        self.docker_compose_ci = (
            docker_compose_ci.resolve()
            if docker_compose_ci is not None
            else (self.repo_root / "docker" / "docker-compose.ci.yml")
        )
        self.runner = runner or subprocess.run
        self.initialized = False

    # --------------------------------------------------------------------------
    # Path & Item Discovery Helpers
    # --------------------------------------------------------------------------

    def _resolve_repo_path(self, rel_or_abs: str | pathlib.Path) -> pathlib.Path:
        p = pathlib.Path(rel_or_abs)
        if p.is_absolute():
            return p.resolve()
        return (self.repo_root / p).resolve()

    def _discover_hierarchy(self) -> dict[str, Any]:
        """Load project.toml, all phase TOMLs, and all item TOMLs."""
        project_data = (
            load_toml_file(self.project_toml) if self.project_toml.is_file() else {}
        )
        hierarchy = project_data.get("hierarchy", {})
        phase_rel_paths = list(hierarchy.get("phases", []))
        if not phase_rel_paths:
            phases_dir = self.project_toml.parent / "phases"
            if phases_dir.is_dir():
                phase_rel_paths = [
                    str(p) for p in sorted(phases_dir.glob("*.toml"))
                ]

        phases: list[dict[str, Any]] = []
        items_by_id: dict[str, dict[str, Any]] = {}
        items_by_path: dict[str, dict[str, Any]] = {}

        for phase_ref in phase_rel_paths:
            phase_path = self._resolve_repo_path(phase_ref)
            if not phase_path.is_file():
                continue
            p_raw = load_toml_file(phase_path)
            p_sec = dict(p_raw.get("phase", {}))
            p_items = p_raw.get("items") or p_sec.get("items", [])
            phases.append(
                {
                    "path": str(phase_path),
                    "phase": p_sec,
                    "gates": p_raw.get("gates", {}),
                    "items": p_items,
                }
            )
            for item_ref in p_items:
                item_path = self._resolve_repo_path(item_ref)
                if not item_path.is_file():
                    continue
                self._register_item(item_path, items_by_id, items_by_path)

        # Also scan .nebulous/items/*.toml so standalone items are discoverable
        items_dir = self.project_toml.parent / "items"
        if items_dir.is_dir():
            for item_path in sorted(items_dir.glob("*.toml")):
                self._register_item(item_path.resolve(), items_by_id, items_by_path)

        return {
            "project": project_data.get("project", {}),
            "guardrails": project_data.get("guardrails", {}),
            "hierarchy": hierarchy,
            "phases": phases,
            "items_by_id": items_by_id,
            "items_by_path": items_by_path,
        }

    def _register_item(
        self,
        item_path: pathlib.Path,
        items_by_id: dict[str, dict[str, Any]],
        items_by_path: dict[str, dict[str, Any]],
    ) -> None:
        raw = load_toml_file(item_path)
        item_sec = dict(raw.get("item", {}))
        attempts = raw.get("attempts")
        if attempts is None:
            attempts = item_sec.get("attempts", [])
        diagnostics = raw.get("diagnostics")
        if diagnostics is None:
            diagnostics = item_sec.get("diagnostics", [])
        item_id = str(item_sec.get("id") or item_path.stem)
        entry = {
            "id": item_id,
            "path": str(item_path),
            "item": item_sec,
            "attempts": list(attempts),
            "diagnostics": list(diagnostics),
        }
        items_by_id[item_id] = entry
        items_by_path[str(item_path)] = entry
        items_by_path[item_path.name] = entry

    def resolve_item_path(self, item_id_or_path: str | None = None) -> pathlib.Path:
        """Resolve an item ID (e.g. 'item-001-docker-qemu-ci') or file path to its Item.toml."""
        if not item_id_or_path:
            default_cand = self.project_toml.parent / "items" / "item-001-docker-qemu-ci.toml"
            if default_cand.is_file():
                return default_cand.resolve()
            info = self._discover_hierarchy()
            if info["items_by_id"]:
                first_entry = next(iter(info["items_by_id"].values()))
                return pathlib.Path(first_entry["path"])
            return default_cand.resolve()

        cand_path = self._resolve_repo_path(item_id_or_path)
        if cand_path.is_file():
            return cand_path

        info = self._discover_hierarchy()
        if item_id_or_path in info["items_by_id"]:
            return pathlib.Path(info["items_by_id"][item_id_or_path]["path"])
        if item_id_or_path in info["items_by_path"]:
            return pathlib.Path(info["items_by_path"][item_id_or_path]["path"])

        cand_in_items = self.project_toml.parent / "items" / f"{item_id_or_path}.toml"
        if cand_in_items.is_file():
            return cand_in_items.resolve()

        raise FileNotFoundError(f"Nebulous Item.toml not found for '{item_id_or_path}'")

    # --------------------------------------------------------------------------
    # MCP Tool Schema Definitions
    # --------------------------------------------------------------------------

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return [
            {
                "name": "nebulous_get_status",
                "description": (
                    "Read .nebulous/project.toml, active phase TOMLs, and item TOMLs. "
                    "Returns current item state, retry counts, loop_locked flag, and any "
                    "active [NEGATIVE_CONSTRAINTS] prompts that the agent MUST obey."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "Optional item ID or path to filter a specific Item.toml.",
                        }
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "nebulous_verify_variance",
                "description": (
                    "Run scripts/nebulous-ctl.py verify-variance against .nebulous/manifest.toml "
                    "and .nebulous/golden-snapshot.toml. Emits HARD_STOP_VARIANCE_LOCK if any "
                    "package, binary, or module was dropped or downgraded."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "iso_path": {
                            "type": "string",
                            "description": "Optional path to built Live ISO (default: ./build/NovOS-sid-amd64.iso).",
                        },
                        "repo_only": {
                            "type": "boolean",
                            "description": "If true, validate repository manifest invariants only without inspecting the ISO.",
                            "default": False,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "nebulous_run_docker_build",
                "description": (
                    "Enforce pre-build guardrail + manifest check, execute a fresh Stage 1-6 "
                    "container build via `docker compose -f docker/docker-compose.ci.yml run --rm novos-builder`, "
                    "run post-build golden variance check, and record the attempt in Item.toml."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "Target item ID or path (e.g. 'item-001-docker-qemu-ci').",
                        },
                        "profiles": {
                            "type": "string",
                            "description": "Optional comma-separated NovOS profiles (default: 'none').",
                            "default": "none",
                        },
                        "iso_path": {
                            "type": "string",
                            "description": "Target ISO path (default: ./build/NovOS-sid-amd64.iso).",
                        },
                    },
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "nebulous_run_qemu_test",
                "description": (
                    "Enforce guardrail + variance check, execute scripts/test-docker-qemu.py "
                    "('live-boot' or 'install-e2e'), capture serial/QMP screendump diagnostics, "
                    "update Item.toml, and return any triggered [NEGATIVE_CONSTRAINTS]."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "Target item ID or path (e.g. 'item-001-docker-qemu-ci').",
                        },
                        "tier": {
                            "type": "string",
                            "enum": ["live-boot", "install-e2e"],
                            "default": "live-boot",
                            "description": "Verification tier ('live-boot' or 'install-e2e').",
                        },
                        "use_docker": {
                            "type": "boolean",
                            "default": True,
                            "description": "If true, run inside novos-qemu-tester container; otherwise run headless on host.",
                        },
                        "enable_vnc": {
                            "type": "boolean",
                            "default": False,
                            "description": "Enable VNC (5900) and noVNC (6080) web display.",
                        },
                        "iso_path": {
                            "type": "string",
                            "description": "Optional path to built Live ISO (default: ./build/NovOS-sid-amd64.iso).",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": "If true, validate guardrails and emit planned QEMU invocation without booting.",
                        },
                    },
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "nebulous_record_diagnostic",
                "description": (
                    "Record a root-cause hypothesis in Item.toml and check whether proposed "
                    "diff fingerprints or modified files overlap with prior failed attempts "
                    "before mutating code."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "Target item ID or path (e.g. 'item-001-docker-qemu-ci').",
                        },
                        "hypothesis": {
                            "type": "string",
                            "description": "Root-cause diagnostic hypothesis before mutating code.",
                        },
                        "root_cause": {
                            "type": "string",
                            "description": "Optional identified subsystem or defect location.",
                        },
                        "proposed_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of file paths proposed for modification.",
                        },
                        "proposed_diff": {
                            "type": "string",
                            "description": "Optional unified diff string to fingerprint against prior failed attempts.",
                        },
                    },
                    "required": ["item_id", "hypothesis"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "nebulous_consult_local_llm",
                "description": (
                    "Query the local llama.cpp Qwen3-14B server (/v1/chat/completions) with "
                    "bounded failure logs and active negative_constraints from Item.toml, "
                    "record the resulting diagnostic hypothesis into Item.toml ([[diagnostics]]), "
                    "and return the structured diagnosis alongside active NEGATIVE_CONSTRAINTS."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "Target item ID or path (e.g. 'item-001-docker-qemu-ci').",
                        },
                        "error_summary": {
                            "type": "string",
                            "description": "Concise failure summary from build or QEMU test run.",
                        },
                        "log_file": {
                            "type": "string",
                            "description": "Optional path to build or serial log file to extract tail context.",
                        },
                        "proposed_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of file paths proposed for modification.",
                        },
                        "llm_endpoint": {
                            "type": "string",
                            "default": DEFAULT_LLM_ENDPOINT,
                            "description": "Optional OpenAI-compatible llama.cpp endpoint (default: NOVOS_LLM_ENDPOINT or http://127.0.0.1:8081/v1).",
                        },
                    },
                    "required": ["item_id", "error_summary"],
                    "additionalProperties": False,
                },
            },
        ]

    # --------------------------------------------------------------------------
    # Subprocess Helpers for nebulous-ctl.py
    # --------------------------------------------------------------------------

    def _run_check_guardrail(self, item_path: pathlib.Path) -> tuple[int, dict[str, Any]]:
        res = self.runner(
            [
                sys.executable,
                str(self.nebulous_ctl),
                "check-guardrail",
                "--item",
                str(item_path),
                "--json",
            ],
            cwd=str(self.repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        payload: dict[str, Any] = {}
        if res.stdout and res.stdout.strip():
            try:
                payload = json.loads(res.stdout)
            except json.JSONDecodeError:
                payload = {"raw_stdout": res.stdout.strip()}
        return res.returncode, payload

    def _run_verify_variance(
        self,
        iso_path: pathlib.Path | None = None,
        repo_only: bool = False,
        manifest_path: pathlib.Path | None = None,
        golden_path: pathlib.Path | None = None,
        approvals_path: pathlib.Path | None = None,
    ) -> tuple[int, dict[str, Any]]:
        cmd = [sys.executable, str(self.nebulous_ctl), "verify-variance", "--json"]
        if repo_only:
            cmd.append("--repo-only")
        elif iso_path is not None:
            cmd.extend(["--iso", str(iso_path)])

        if manifest_path is not None:
            cmd.extend(["--manifest", str(manifest_path)])
        if golden_path is not None:
            cmd.extend(["--golden", str(golden_path)])
        if approvals_path is not None:
            cmd.extend(["--approvals", str(approvals_path)])

        res = self.runner(
            cmd,
            cwd=str(self.repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        report: dict[str, Any] = {}
        if res.stdout and res.stdout.strip():
            try:
                report = json.loads(res.stdout)
            except json.JSONDecodeError:
                report = {"raw_stdout": res.stdout.strip()}
        if not report:
            report = {
                "status": "HARD_STOP_VARIANCE_LOCK" if res.returncode != EXIT_OK else "PASS",
                "exit_code": res.returncode,
                "violations": [],
                "stderr": (res.stderr or "").strip(),
            }
        return res.returncode, report

    def _run_record_attempt(
        self,
        item_path: pathlib.Path,
        stage: str,
        status: str,
        error_summary: str = "",
        log_file: str = "",
    ) -> int:
        prior_diagnostics: list[dict[str, Any]] = []
        if item_path.is_file():
            raw_before = load_toml_file(item_path)
            prior_diagnostics = list(
                raw_before.get("diagnostics")
                or raw_before.get("item", {}).get("diagnostics", [])
            )

        cmd = [
            sys.executable,
            str(self.nebulous_ctl),
            "record-attempt",
            "--item",
            str(item_path),
            "--stage",
            stage,
            "--status",
            status,
        ]
        if error_summary:
            cmd.extend(["--error-summary", error_summary])
        if log_file:
            cmd.extend(["--log-file", log_file])
        res = self.runner(
            cmd,
            cwd=str(self.repo_root),
            check=False,
            capture_output=True,
            text=True,
        )

        if prior_diagnostics and item_path.is_file():
            raw_after = load_toml_file(item_path)
            after_diags = raw_after.get("diagnostics") or raw_after.get("item", {}).get(
                "diagnostics", []
            )
            if not after_diags:
                write_item_toml_with_diagnostics(
                    {
                        "item": raw_after.get("item", {}),
                        "attempts": raw_after.get("attempts")
                        or raw_after.get("item", {}).get("attempts", []),
                        "diagnostics": prior_diagnostics,
                    },
                    item_path,
                )
        return res.returncode

    # --------------------------------------------------------------------------
    # Tool 1: `nebulous_get_status`
    # --------------------------------------------------------------------------

    def tool_get_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        hierarchy_info = self._discover_hierarchy()
        requested_item = arguments.get("item_id")

        items_summary: list[dict[str, Any]] = []
        active_negative_constraints: list[str] = []
        any_loop_locked = False

        if requested_item:
            item_path = self.resolve_item_path(str(requested_item))
            raw = load_toml_file(item_path)
            item_sec = dict(raw.get("item", {}))
            attempts = raw.get("attempts") or item_sec.get("attempts", [])
            diagnostics = raw.get("diagnostics") or item_sec.get("diagnostics", [])
            neg = list(item_sec.get("negative_constraints", []))
            locked = bool(item_sec.get("loop_locked", False))
            any_loop_locked = locked
            active_negative_constraints.extend(neg)
            items_summary.append(
                {
                    "id": item_sec.get("id", item_path.stem),
                    "phase_id": item_sec.get("phase_id", ""),
                    "title": item_sec.get("title", ""),
                    "path": str(item_path),
                    "status": item_sec.get("status", "in_progress"),
                    "loop_locked": locked,
                    "retry_count": int(item_sec.get("retry_count", 0)),
                    "negative_constraints": neg,
                    "attempts_count": len(attempts),
                    "last_attempt": attempts[-1] if attempts else None,
                    "diagnostics_count": len(diagnostics),
                    "last_diagnostic": diagnostics[-1] if diagnostics else None,
                }
            )
        else:
            for item_id, entry in sorted(hierarchy_info["items_by_id"].items()):
                item_sec = entry["item"]
                attempts = entry["attempts"]
                diagnostics = entry["diagnostics"]
                neg = list(item_sec.get("negative_constraints", []))
                locked = bool(item_sec.get("loop_locked", False))
                if locked:
                    any_loop_locked = True
                for nc in neg:
                    if nc not in active_negative_constraints:
                        active_negative_constraints.append(nc)
                items_summary.append(
                    {
                        "id": item_id,
                        "phase_id": item_sec.get("phase_id", ""),
                        "title": item_sec.get("title", ""),
                        "path": entry["path"],
                        "status": item_sec.get("status", "in_progress"),
                        "loop_locked": locked,
                        "retry_count": int(item_sec.get("retry_count", 0)),
                        "negative_constraints": neg,
                        "attempts_count": len(attempts),
                        "last_attempt": attempts[-1] if attempts else None,
                        "diagnostics_count": len(diagnostics),
                        "last_diagnostic": diagnostics[-1] if diagnostics else None,
                    }
                )

        current_item = items_summary[0] if items_summary else None
        status_code = "HARD_STOP_LOOP_LOCK" if any_loop_locked else "OK"

        result: dict[str, Any] = {
            "status": status_code,
            "project": hierarchy_info["project"],
            "guardrails": hierarchy_info["guardrails"],
            "phases": hierarchy_info["phases"],
            "current_item": current_item,
            "items": items_summary,
            "loop_locked": any_loop_locked,
            "retry_count": current_item["retry_count"] if current_item else 0,
            "NEGATIVE_CONSTRAINTS": active_negative_constraints,
        }
        if any_loop_locked:
            result["mandatory_instruction"] = HARD_STOP_LOOP_INSTRUCTION
        return result

    # --------------------------------------------------------------------------
    # Tool 2: `nebulous_verify_variance`
    # --------------------------------------------------------------------------

    def tool_verify_variance(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repo_only = bool(arguments.get("repo_only", False))
        raw_iso = arguments.get("iso_path")

        hierarchy = self._discover_hierarchy().get("hierarchy", {})
        manifest_path = self._resolve_repo_path(
            hierarchy.get("manifest_path", ".nebulous/manifest.toml")
        )
        golden_path = self._resolve_repo_path(
            hierarchy.get("golden_snapshot_path", ".nebulous/golden-snapshot.toml")
        )
        approvals_path = self._resolve_repo_path(
            hierarchy.get("approvals_path", ".nebulous/approvals.toml")
        )

        iso_path: pathlib.Path | None = None
        if not repo_only:
            if raw_iso:
                iso_path = self._resolve_repo_path(str(raw_iso))
            else:
                default_iso = self.repo_root / "build" / "NovOS-sid-amd64.iso"
                if default_iso.is_file():
                    iso_path = default_iso
                else:
                    repo_only = True

        rc, report = self._run_verify_variance(
            iso_path=iso_path,
            repo_only=repo_only,
            manifest_path=manifest_path,
            golden_path=golden_path,
            approvals_path=approvals_path,
        )

        violations = report.get("violations", [])
        if rc != EXIT_OK or violations:
            return {
                "status": "HARD_STOP_VARIANCE_LOCK",
                "exit_code": EXIT_VARIANCE_LOCK,
                "mandatory_instruction": HARD_STOP_VARIANCE_INSTRUCTION,
                "repo_only": bool(report.get("repo_only", repo_only)),
                "checked_packages_count": report.get("checked_packages_count", 0),
                "checked_binaries_count": report.get("checked_binaries_count", 0),
                "upgraded_count": report.get("upgraded_count", 0),
                "upgrades": report.get("upgrades", []),
                "violations": violations,
            }

        return {
            "status": "PASS",
            "exit_code": EXIT_OK,
            "repo_only": bool(report.get("repo_only", repo_only)),
            "checked_packages_count": report.get("checked_packages_count", 0),
            "checked_binaries_count": report.get("checked_binaries_count", 0),
            "upgraded_count": report.get("upgraded_count", 0),
            "upgrades": report.get("upgrades", []),
            "violations": [],
        }

    # --------------------------------------------------------------------------
    # Tool 3: `nebulous_run_docker_build`
    # --------------------------------------------------------------------------

    def tool_run_docker_build(self, arguments: dict[str, Any]) -> dict[str, Any]:
        item_id = arguments.get("item_id")
        profiles = str(arguments.get("profiles", "none")).strip() or "none"
        if any(
            forbidden in profiles
            for forbidden in ("--from-stage", "from-stage", "FROM_STAGE")
        ):
            return {
                "status": "HARD_STOP_FRESH_BUILD_INVARIANT",
                "exit_code": EXIT_ERROR,
                "mandatory_instruction": (
                    "HARD STOP: Partial or resumed stage builds (--from-stage) are strictly "
                    "prohibited. All NovOS builds must execute Stages 1-6 fresh."
                ),
            }

        item_path = self.resolve_item_path(item_id)
        iso_path = (
            self._resolve_repo_path(str(arguments["iso_path"]))
            if arguments.get("iso_path")
            else (self.repo_root / "build" / "NovOS-sid-amd64.iso")
        )

        # 1. Pre-check guardrail (loop lock + repo variance)
        guard_rc, guard_payload = self._run_check_guardrail(item_path)
        if guard_rc == EXIT_LOOP_LOCK or guard_payload.get("loop_locked"):
            return {
                "status": "HARD_STOP_LOOP_LOCK",
                "exit_code": EXIT_LOOP_LOCK,
                "item_id": guard_payload.get("item_id", item_path.stem),
                "loop_locked": True,
                "retry_count": guard_payload.get("retry_count", 0),
                "NEGATIVE_CONSTRAINTS": guard_payload.get("negative_constraints", []),
                "mandatory_instruction": HARD_STOP_LOOP_INSTRUCTION,
            }

        # 2. Pre-check repository manifest variance explicitly
        pre_var = self.tool_verify_variance({"repo_only": True})
        if pre_var["status"] == "HARD_STOP_VARIANCE_LOCK":
            return {
                "status": "HARD_STOP_VARIANCE_LOCK",
                "stage": "pre_build_variance_check",
                "exit_code": EXIT_VARIANCE_LOCK,
                "mandatory_instruction": HARD_STOP_VARIANCE_INSTRUCTION,
                "violations": pre_var.get("violations", []),
                "NEGATIVE_CONSTRAINTS": guard_payload.get("negative_constraints", []),
            }

        # 3. Execute fresh Stage 1-6 container build via docker compose
        logs_dir = self.repo_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        build_log_path = logs_dir / f"docker-build-{ts}.log"

        docker_cmd = [
            "docker",
            "compose",
            "-f",
            str(self.docker_compose_ci),
            "run",
            "--rm",
        ]
        if profiles and profiles != "none":
            docker_cmd.extend(["-e", f"NOVOS_PROFILES={profiles}"])
        docker_cmd.append("novos-builder")

        build_res = self.runner(
            docker_cmd,
            cwd=str(self.repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        combined_output = (build_res.stdout or "") + "\n" + (build_res.stderr or "")
        build_log_path.write_text(combined_output, encoding="utf-8")

        if build_res.returncode != EXIT_OK:
            err_lines = [
                ln.strip()
                for ln in strip_ansi(combined_output).splitlines()
                if ln.strip()
            ]
            err_summary = (
                err_lines[-1]
                if err_lines
                else f"Docker build failed with exit code {build_res.returncode}"
            )
            rec_rc = self._run_record_attempt(
                item_path=item_path,
                stage="build",
                status="fail",
                error_summary=err_summary,
                log_file=str(build_log_path),
            )
            updated_item = load_toml_file(item_path).get("item", {})
            neg_constraints = list(updated_item.get("negative_constraints", []))
            locked = bool(updated_item.get("loop_locked", False)) or (rec_rc == EXIT_LOOP_LOCK)
            result: dict[str, Any] = {
                "status": "HARD_STOP_LOOP_LOCK" if locked else "BUILD_FAILED",
                "exit_code": EXIT_LOOP_LOCK if locked else build_res.returncode,
                "command": docker_cmd,
                "log_file": str(build_log_path),
                "error_summary": err_summary,
                "loop_locked": locked,
                "retry_count": int(updated_item.get("retry_count", 0)),
                "NEGATIVE_CONSTRAINTS": neg_constraints,
            }
            if locked:
                result["mandatory_instruction"] = HARD_STOP_LOOP_INSTRUCTION
            return result

        # 4. Run post-build golden variance check against built ISO
        post_var = self.tool_verify_variance({"iso_path": str(iso_path), "repo_only": False})
        if post_var["status"] == "HARD_STOP_VARIANCE_LOCK":
            self._run_record_attempt(
                item_path=item_path,
                stage="build",
                status="fail",
                error_summary="Post-build Golden OS Snapshot variance lock triggered",
                log_file=str(build_log_path),
            )
            updated_item = load_toml_file(item_path).get("item", {})
            return {
                "status": "HARD_STOP_VARIANCE_LOCK",
                "stage": "post_build_variance_check",
                "exit_code": EXIT_VARIANCE_LOCK,
                "mandatory_instruction": HARD_STOP_VARIANCE_INSTRUCTION,
                "command": docker_cmd,
                "log_file": str(build_log_path),
                "violations": post_var.get("violations", []),
                "loop_locked": bool(updated_item.get("loop_locked", False)),
                "retry_count": int(updated_item.get("retry_count", 0)),
                "NEGATIVE_CONSTRAINTS": list(updated_item.get("negative_constraints", [])),
            }

        # 5. Record successful build attempt
        self._run_record_attempt(
            item_path=item_path,
            stage="build",
            status="pass",
            log_file=str(build_log_path),
        )
        updated_item = load_toml_file(item_path).get("item", {})
        return {
            "status": "PASS",
            "exit_code": EXIT_OK,
            "command": docker_cmd,
            "iso_path": str(iso_path),
            "log_file": str(build_log_path),
            "variance_report": post_var,
            "loop_locked": bool(updated_item.get("loop_locked", False)),
            "retry_count": int(updated_item.get("retry_count", 0)),
            "NEGATIVE_CONSTRAINTS": list(updated_item.get("negative_constraints", [])),
        }

    # --------------------------------------------------------------------------
    # Tool 4: `nebulous_run_qemu_test`
    # --------------------------------------------------------------------------

    def tool_run_qemu_test(self, arguments: dict[str, Any]) -> dict[str, Any]:
        item_id = arguments.get("item_id")
        tier = str(arguments.get("tier", "live-boot")).strip()
        if tier not in ("live-boot", "install-e2e"):
            raise ValueError(f"Unsupported tier '{tier}'; must be 'live-boot' or 'install-e2e'")

        use_docker = bool(arguments.get("use_docker", True))
        enable_vnc = bool(arguments.get("enable_vnc", False))
        dry_run = bool(arguments.get("dry_run", False))

        item_path = self.resolve_item_path(item_id)
        iso_path = (
            self._resolve_repo_path(str(arguments["iso_path"]))
            if arguments.get("iso_path")
            else (self.repo_root / "build" / "NovOS-sid-amd64.iso")
        )

        # 1. Pre-check guardrail
        guard_rc, guard_payload = self._run_check_guardrail(item_path)
        if guard_rc == EXIT_LOOP_LOCK or guard_payload.get("loop_locked"):
            return {
                "status": "HARD_STOP_LOOP_LOCK",
                "exit_code": EXIT_LOOP_LOCK,
                "item_id": guard_payload.get("item_id", item_path.stem),
                "tier": tier,
                "loop_locked": True,
                "retry_count": guard_payload.get("retry_count", 0),
                "NEGATIVE_CONSTRAINTS": guard_payload.get("negative_constraints", []),
                "mandatory_instruction": HARD_STOP_LOOP_INSTRUCTION,
            }

        # 2. Pre-check variance against ISO (or repo-only if dry_run and ISO not present)
        var_check = self.tool_verify_variance(
            {
                "iso_path": str(iso_path),
                "repo_only": bool(dry_run and not iso_path.is_file()),
            }
        )
        if var_check["status"] == "HARD_STOP_VARIANCE_LOCK":
            return {
                "status": "HARD_STOP_VARIANCE_LOCK",
                "exit_code": EXIT_VARIANCE_LOCK,
                "tier": tier,
                "mandatory_instruction": HARD_STOP_VARIANCE_INSTRUCTION,
                "violations": var_check.get("violations", []),
                "NEGATIVE_CONSTRAINTS": guard_payload.get("negative_constraints", []),
            }

        # 3. Build command for containerized or host headless test-docker-qemu.py
        try:
            rel_iso = str(iso_path.relative_to(self.repo_root))
        except ValueError:
            rel_iso = str(iso_path)
        try:
            rel_item = str(item_path.relative_to(self.repo_root))
        except ValueError:
            rel_item = str(item_path)

        harness_args = [
            "--iso",
            rel_iso if use_docker else str(iso_path),
            "--tier",
            tier,
            "--item",
            rel_item if use_docker else str(item_path),
            "--vnc" if enable_vnc else "--no-vnc",
        ]
        if dry_run:
            harness_args.append("--dry-run")

        if use_docker and not dry_run:
            cmd = [
                "docker",
                "compose",
                "-f",
                str(self.docker_compose_ci),
                "run",
                "--rm",
                *(["--service-ports"] if enable_vnc else []),
                "novos-qemu-tester",
                *harness_args,
            ]
        else:
            cmd = [sys.executable, str(self.test_docker_qemu), *harness_args]

        raw_before = load_toml_file(item_path) if item_path.is_file() else {}
        attempts_before_count = len(
            raw_before.get("attempts") or raw_before.get("item", {}).get("attempts", [])
        )
        diagnostics_before = list(
            raw_before.get("diagnostics")
            or raw_before.get("item", {}).get("diagnostics", [])
        )

        res = self.runner(
            cmd,
            cwd=str(self.repo_root),
            check=False,
            capture_output=True,
            text=True,
        )

        verdict_file = self.repo_root / "logs" / "qemu-artifacts" / "last-test-verdict.json"
        verdict_data: dict[str, Any] = {}
        if verdict_file.is_file():
            try:
                verdict_data = json.loads(verdict_file.read_text(encoding="utf-8"))
            except Exception:
                verdict_data = {}

        # Extract serial tail if available
        serial_tail = ""
        serial_log_str = verdict_data.get("serial_log")
        if serial_log_str and os.path.isfile(serial_log_str):
            try:
                with open(serial_log_str, "r", encoding="utf-8", errors="replace") as sf:
                    serial_tail = strip_ansi("".join(sf.readlines()[-30:])).strip()
            except OSError:
                pass

        # If containerized runner failed before test-docker-qemu.py recorded telemetry, record it
        raw_after = load_toml_file(item_path)
        attempts_after = raw_after.get("attempts") or raw_after.get("item", {}).get("attempts", [])
        if not dry_run and res.returncode != EXIT_OK and len(attempts_after) <= attempts_before_count:
            err_msg = (res.stderr or res.stdout or f"QEMU test ({tier}) failed").strip()
            self._run_record_attempt(
                item_path=item_path,
                stage=tier,
                status="fail",
                error_summary=err_msg.splitlines()[-1] if err_msg else "QEMU test failed",
                log_file=serial_log_str or "",
            )
            raw_after = load_toml_file(item_path)

        if diagnostics_before:
            after_diags = raw_after.get("diagnostics") or raw_after.get("item", {}).get(
                "diagnostics", []
            )
            if not after_diags:
                write_item_toml_with_diagnostics(
                    {
                        "item": raw_after.get("item", {}),
                        "attempts": raw_after.get("attempts")
                        or raw_after.get("item", {}).get("attempts", []),
                        "diagnostics": diagnostics_before,
                    },
                    item_path,
                )
                raw_after = load_toml_file(item_path)

        updated_item = raw_after.get("item", {})
        locked = bool(updated_item.get("loop_locked", False)) or (res.returncode == EXIT_LOOP_LOCK)
        neg_constraints = list(updated_item.get("negative_constraints", []))

        if res.returncode == EXIT_VARIANCE_LOCK:
            status_str = "HARD_STOP_VARIANCE_LOCK"
        elif locked:
            status_str = "HARD_STOP_LOOP_LOCK"
        elif res.returncode == EXIT_OK:
            status_str = "PASS"
        else:
            status_str = "TEST_FAILED"

        result: dict[str, Any] = {
            "status": status_str,
            "exit_code": res.returncode,
            "tier": tier,
            "use_docker": use_docker,
            "enable_vnc": enable_vnc,
            "command": cmd,
            "verdict": verdict_data,
            "screendumps": verdict_data.get("screendumps", []),
            "serial_log": serial_log_str,
            "serial_tail": serial_tail,
            "stdout": (res.stdout or "").strip(),
            "stderr": (res.stderr or "").strip(),
            "loop_locked": locked,
            "retry_count": int(updated_item.get("retry_count", 0)),
            "NEGATIVE_CONSTRAINTS": neg_constraints,
        }
        if status_str == "HARD_STOP_VARIANCE_LOCK":
            result["mandatory_instruction"] = HARD_STOP_VARIANCE_INSTRUCTION
        elif status_str == "HARD_STOP_LOOP_LOCK":
            result["mandatory_instruction"] = HARD_STOP_LOOP_INSTRUCTION
        return result

    # --------------------------------------------------------------------------
    # Tool 5: `nebulous_record_diagnostic`
    # --------------------------------------------------------------------------

    def tool_record_diagnostic(self, arguments: dict[str, Any]) -> dict[str, Any]:
        item_id = arguments.get("item_id")
        hypothesis = strip_ansi(str(arguments.get("hypothesis", ""))).strip()
        if not hypothesis:
            raise ValueError("'hypothesis' is required and cannot be empty")

        root_cause = strip_ansi(str(arguments.get("root_cause", ""))).strip()
        proposed_files = [
            str(f).strip() for f in arguments.get("proposed_files", []) if str(f).strip()
        ]
        proposed_diff = arguments.get("proposed_diff")

        item_path = self.resolve_item_path(item_id)
        raw = load_toml_file(item_path)
        item_sec = dict(raw.get("item", {}))
        attempts = list(raw.get("attempts") or item_sec.get("attempts", []))
        diagnostics = list(raw.get("diagnostics") or item_sec.get("diagnostics", []))

        diff_fp, detected_files = compute_diff_fingerprint(
            self.repo_root,
            proposed_diff=str(proposed_diff) if proposed_diff is not None else None,
            proposed_files=proposed_files,
        )

        failed_attempts = [a for a in attempts if a.get("status") == "fail"]
        matching_diff_attempts = [
            a
            for a in failed_attempts
            if a.get("diff_fingerprint") == diff_fp
            and diff_fp not in ("", "clean", "none")
        ]
        overlapping_file_attempts = [
            a
            for a in failed_attempts
            if detected_files
            and set(detected_files).intersection(set(a.get("modified_files", [])))
        ]

        prior_failed_overlap = bool(matching_diff_attempts)
        neg_constraints = list(item_sec.get("negative_constraints", []))

        if prior_failed_overlap:
            prior_err = matching_diff_attempts[-1].get("error_summary", "previous failure")
            mod_str = ", ".join(detected_files) if detected_files else "working-tree"
            warn_msg = (
                f"LOOP DETECTED [diff_fingerprint={diff_fp}]: Proposed diff in [{mod_str}] "
                f"matches a prior failed attempt ('{prior_err}'). DO NOT re-apply the same "
                f"diff without changing root-cause architecture. Dropping packages or "
                f"downgrading versions is strictly forbidden."
            )
            if warn_msg not in neg_constraints:
                neg_constraints.append(warn_msg)
            item_sec["negative_constraints"] = neg_constraints

        diag_entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "hypothesis": hypothesis,
            "root_cause": root_cause or "unspecified",
            "diff_fingerprint": diff_fp,
            "proposed_files": detected_files,
            "prior_failed_overlap": prior_failed_overlap,
        }
        diagnostics.append(diag_entry)

        write_item_toml_with_diagnostics(
            {
                "item": item_sec,
                "attempts": attempts,
                "diagnostics": diagnostics,
            },
            item_path,
        )

        locked = bool(item_sec.get("loop_locked", False))
        status_str = (
            "HARD_STOP_LOOP_LOCK"
            if locked
            else ("REJECTED_DUPLICATE_DIFF" if prior_failed_overlap else "RECORDED")
        )
        return {
            "status": status_str,
            "item_id": item_sec.get("id", item_path.stem),
            "diagnostic": diag_entry,
            "diff_fingerprint": diff_fp,
            "prior_failed_diff_match": prior_failed_overlap,
            "overlapping_failed_file_attempts_count": len(overlapping_file_attempts),
            "loop_locked": locked,
            "retry_count": int(item_sec.get("retry_count", 0)),
            "NEGATIVE_CONSTRAINTS": neg_constraints,
        }

    # --------------------------------------------------------------------------
    # Tool 6: `nebulous_consult_local_llm`
    # --------------------------------------------------------------------------

    @staticmethod
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

    def tool_consult_local_llm(self, arguments: dict[str, Any]) -> dict[str, Any]:
        item_id = arguments.get("item_id")
        error_summary = strip_ansi(str(arguments.get("error_summary", ""))).strip()
        if not error_summary:
            raise ValueError("'error_summary' is required and cannot be empty")

        raw_log_file = arguments.get("log_file")
        proposed_files = [
            str(f).strip() for f in arguments.get("proposed_files", []) if str(f).strip()
        ]
        raw_endpoint = arguments.get("llm_endpoint")
        base_endpoint = (
            str(raw_endpoint).strip()
            if raw_endpoint is not None and str(raw_endpoint).strip()
            else os.environ.get("NOVOS_LLM_ENDPOINT", DEFAULT_LLM_ENDPOINT)
        ).rstrip("/")
        chat_url = (
            base_endpoint
            if base_endpoint.endswith("/chat/completions")
            else f"{base_endpoint}/chat/completions"
        )
        model_name = os.environ.get("NOVOS_LLM_MODEL", DEFAULT_LLM_MODEL)

        item_path = self.resolve_item_path(item_id)
        raw = load_toml_file(item_path)
        item_sec = dict(raw.get("item", {}))
        attempts = list(raw.get("attempts") or item_sec.get("attempts", []))
        diagnostics = list(raw.get("diagnostics") or item_sec.get("diagnostics", []))
        neg_constraints = list(item_sec.get("negative_constraints", []))

        log_tail = ""
        resolved_log_path_str = ""
        if raw_log_file:
            log_path = self._resolve_repo_path(str(raw_log_file))
            resolved_log_path_str = str(log_path)
            if log_path.is_file():
                try:
                    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines(True)
                    log_tail = strip_ansi("".join(lines[-40:]))[-4000:].strip()
                except OSError:
                    log_tail = ""

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
                            "error_summary": error_summary[:1000],
                            "log_tail": log_tail,
                            "proposed_files": proposed_files,
                            "negative_constraints": neg_constraints,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }

        llm_available = False
        llm_error = ""
        parsed_diag: dict[str, Any] = {}

        try:
            req = urllib.request.Request(
                chat_url,
                data=json.dumps(req_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
            resp_json = json.loads(raw_body)
            choices = resp_json.get("choices")
            if isinstance(choices, list) and choices:
                msg_content = choices[0].get("message", {}).get("content", "")
                parsed_diag = self._parse_llm_json_content(msg_content)
            elif isinstance(resp_json, dict) and "root_cause" in resp_json:
                parsed_diag = resp_json
            llm_available = True
        except Exception as exc:
            llm_available = False
            llm_error = str(exc)

        root_cause = strip_ansi(str(parsed_diag.get("root_cause", ""))).strip() or (
            f"Offline fallback diagnosis for: {error_summary}"
        )
        subsystem = strip_ansi(str(parsed_diag.get("subsystem", ""))).strip() or "unspecified"
        recommended_fix = strip_ansi(str(parsed_diag.get("recommended_fix", ""))).strip() or (
            "Inspect log tail and stage scripts while obeying active NEGATIVE_CONSTRAINTS."
        )
        violates_neg = bool(parsed_diag.get("violates_negative_constraints", False))

        diff_fp, detected_files = compute_diff_fingerprint(
            self.repo_root,
            proposed_diff=None,
            proposed_files=proposed_files,
        )
        failed_attempts = [a for a in attempts if a.get("status") == "fail"]
        prior_failed_overlap = any(
            a.get("diff_fingerprint") == diff_fp and diff_fp not in ("", "clean", "none")
            for a in failed_attempts
        )

        hypothesis_text = (
            f"[Qwen3-14B:{subsystem}] {recommended_fix}"
            if llm_available
            else f"[LLM-Offline:{subsystem}] {error_summary}"
        )

        diag_entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "hypothesis": hypothesis_text,
            "root_cause": root_cause,
            "subsystem": subsystem,
            "recommended_fix": recommended_fix,
            "violates_negative_constraints": violates_neg,
            "llm_model": model_name,
            "diff_fingerprint": diff_fp,
            "proposed_files": detected_files,
            "prior_failed_overlap": prior_failed_overlap,
        }
        diagnostics.append(diag_entry)

        write_item_toml_with_diagnostics(
            {
                "item": item_sec,
                "attempts": attempts,
                "diagnostics": diagnostics,
            },
            item_path,
        )

        locked = bool(item_sec.get("loop_locked", False))
        if locked:
            status_str = "HARD_STOP_LOOP_LOCK"
        elif violates_neg:
            status_str = "REJECTED_NEGATIVE_CONSTRAINT_VIOLATION"
        elif llm_available:
            status_str = "DIAGNOSED"
        else:
            status_str = "LLM_UNAVAILABLE"

        return {
            "status": status_str,
            "item_id": item_sec.get("id", item_path.stem),
            "llm_available": llm_available,
            "llm_endpoint": chat_url,
            "llm_model": model_name,
            "llm_error": llm_error,
            "log_file": resolved_log_path_str,
            "log_tail": log_tail,
            "diagnosis": {
                "root_cause": root_cause,
                "subsystem": subsystem,
                "recommended_fix": recommended_fix,
                "violates_negative_constraints": violates_neg,
            },
            "diagnostic": diag_entry,
            "loop_locked": locked,
            "retry_count": int(item_sec.get("retry_count", 0)),
            "NEGATIVE_CONSTRAINTS": neg_constraints,
        }

    # --------------------------------------------------------------------------
    # JSON-RPC 2.0 Request Dispatcher & Stdio Framing Support
    # --------------------------------------------------------------------------

    @classmethod
    def _validate_tool_arguments(cls, name: str, arguments: Any) -> dict[str, Any]:
        if arguments is None:
            args: dict[str, Any] = {}
        elif isinstance(arguments, dict):
            args = dict(arguments)
        else:
            raise ValueError(f"Tool arguments for '{name}' must be a JSON object")

        tools_by_name = {t["name"]: t for t in cls.list_tools()}
        if name not in tools_by_name:
            raise KeyError(f"Unknown MCP tool: {name}")

        schema = tools_by_name[name].get("inputSchema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        for req_key in required:
            if req_key not in args or args[req_key] is None:
                raise ValueError(f"Missing required parameter '{req_key}' for tool '{name}'")
            if isinstance(args[req_key], str) and not args[req_key].strip():
                raise ValueError(f"Required parameter '{req_key}' for tool '{name}' cannot be empty")

        if schema.get("additionalProperties") is False:
            unknown = [k for k in args if k not in props]
            if unknown:
                raise ValueError(
                    f"Unknown parameter(s) {unknown} for tool '{name}' (additionalProperties=false)"
                )

        for k, v in args.items():
            if v is None or k not in props:
                continue
            p_schema = props[k]
            expected_type = p_schema.get("type")
            if expected_type == "string" and not isinstance(v, str):
                raise ValueError(f"Parameter '{k}' must be a string")
            if expected_type == "boolean" and not isinstance(v, bool):
                raise ValueError(f"Parameter '{k}' must be a boolean")
            if expected_type == "array":
                if not isinstance(v, list):
                    raise ValueError(f"Parameter '{k}' must be an array")
                item_type = p_schema.get("items", {}).get("type")
                if item_type == "string" and not all(isinstance(x, str) for x in v):
                    raise ValueError(f"Parameter '{k}' items must be strings")
            if "enum" in p_schema and v not in p_schema["enum"]:
                raise ValueError(
                    f"Parameter '{k}' value '{v}' must be one of {p_schema['enum']}"
                )

        return args

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = self._validate_tool_arguments(name, arguments)
        if name == "nebulous_get_status":
            payload = self.tool_get_status(args)
        elif name == "nebulous_verify_variance":
            payload = self.tool_verify_variance(args)
        elif name == "nebulous_run_docker_build":
            payload = self.tool_run_docker_build(args)
        elif name == "nebulous_run_qemu_test":
            payload = self.tool_run_qemu_test(args)
        elif name == "nebulous_record_diagnostic":
            payload = self.tool_record_diagnostic(args)
        elif name == "nebulous_consult_local_llm":
            payload = self.tool_consult_local_llm(args)
        else:
            raise KeyError(f"Unknown MCP tool: {name}")

        is_error = payload.get("status", "").startswith("HARD_STOP_") or payload.get(
            "status"
        ) in (
            "BUILD_FAILED",
            "TEST_FAILED",
            "REJECTED_DUPLICATE_DIFF",
            "REJECTED_NEGATIVE_CONSTRAINT_VIOLATION",
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, indent=2),
                }
            ],
            "structuredContent": payload,
            "isError": bool(is_error),
        }

    def handle_jsonrpc_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single JSON-RPC 2.0 message and return response dict (or None for notifications)."""
        method = msg.get("method", "")
        msg_id = msg.get("id")
        is_notification = "id" not in msg

        # Notifications do not produce a JSON-RPC response
        if is_notification or method.startswith("notifications/"):
            if method == "notifications/initialized":
                self.initialized = True
            return None

        try:
            raw_params = msg.get("params")
            if raw_params is not None and not isinstance(raw_params, dict):
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params: expected JSON object",
                    },
                }
            params = raw_params or {}

            if method == "initialize":
                client_proto = params.get("protocolVersion", MCP_PROTOCOL_VERSION)
                self.initialized = True
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": client_proto or MCP_PROTOCOL_VERSION,
                        "capabilities": {
                            "tools": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": SERVER_NAME,
                            "version": SERVER_VERSION,
                        },
                    },
                }

            if method == "ping":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {},
                }

            if method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": self.list_tools(),
                    },
                }

            if method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments")
                try:
                    tool_result = self.call_tool(tool_name, tool_args)
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": tool_result,
                    }
                except KeyError as exc:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {
                            "code": -32602,
                            "message": f"Unknown tool: {exc}",
                        },
                    }
                except ValueError as exc:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {
                            "code": -32602,
                            "message": f"Invalid tool parameters: {exc}",
                        },
                    }
                except FileNotFoundError as exc:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {
                            "code": -32001,
                            "message": str(exc),
                        },
                    }

            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {exc}",
                },
            }

    def serve_streams(self, in_stream: io.BufferedReader, out_stream: io.BufferedWriter) -> int:
        """
        Serve MCP JSON-RPC 2.0 over stdio supporting both:
          - Newline-delimited JSON-RPC (MCP 2024-11-05 stdio specification)
          - Content-Length header-framed JSON-RPC (LSP/legacy MCP clients)
        """
        while True:
            first_line = in_stream.readline()
            if not first_line:
                break

            stripped = first_line.strip()
            if not stripped:
                continue

            use_content_length = False
            if stripped.lower().startswith(b"content-length:"):
                use_content_length = True
                length = int(stripped.split(b":", 1)[1].strip())
                # Consume remaining headers until blank line
                while True:
                    hdr_line = in_stream.readline()
                    if not hdr_line or hdr_line.strip() == b"":
                        break
                body_bytes = in_stream.read(length)
            else:
                body_bytes = stripped

            try:
                msg = json.loads(body_bytes.decode("utf-8"))
            except json.JSONDecodeError as exc:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {exc}",
                    },
                }
                self._write_response(out_stream, err_resp, use_content_length)
                continue

            if isinstance(msg, list):
                responses = []
                for sub_msg in msg:
                    resp = self.handle_jsonrpc_message(sub_msg)
                    if resp is not None:
                        responses.append(resp)
                if responses:
                    self._write_response(out_stream, responses, use_content_length)
            elif isinstance(msg, dict):
                resp = self.handle_jsonrpc_message(msg)
                if resp is not None:
                    self._write_response(out_stream, resp, use_content_length)

        return EXIT_OK

    @staticmethod
    def _write_response(
        out_stream: io.BufferedWriter,
        payload: Any,
        use_content_length: bool = False,
    ) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if use_content_length:
            header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
            out_stream.write(header + encoded)
        else:
            out_stream.write(encoded + b"\n")
        out_stream.flush()


# ==============================================================================
# 3. Built-In Unit Test Suite (`--self-test`)
# ==============================================================================

class McpNebulousCiSelfTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mcp-nebulous-test-")
        self.tmp_root = pathlib.Path(self._tmp.name)
        (self.tmp_root / ".nebulous" / "phases").mkdir(parents=True)
        (self.tmp_root / ".nebulous" / "items").mkdir(parents=True)
        (self.tmp_root / "logs" / "qemu-artifacts").mkdir(parents=True)
        (self.tmp_root / "docker").mkdir(parents=True)
        (self.tmp_root / "docker" / "docker-compose.ci.yml").write_text(
            "services:\n  novos-builder: {}\n  novos-qemu-tester: {}\n",
            encoding="utf-8",
        )

        self.project_toml = self.tmp_root / ".nebulous" / "project.toml"
        self.project_toml.write_text(
            '[project]\n'
            'id = "novos"\n'
            'name = "NovOS"\n'
            'version = "2026.10"\n'
            'codename = "nova"\n'
            'schema_version = 1\n\n'
            '[hierarchy]\n'
            'manifest_path = ".nebulous/manifest.toml"\n'
            'golden_snapshot_path = ".nebulous/golden-snapshot.toml"\n'
            'approvals_path = ".nebulous/approvals.toml"\n'
            'phases = [".nebulous/phases/phase-01-ci-foundation.toml"]\n\n'
            '[guardrails]\n'
            'variance_policy = "asymmetric_forward_only"\n'
            'allow_package_removal = false\n'
            'allow_version_downgrade = false\n'
            'allow_custom_tool_disable = false\n'
            'loop_repeat_warn_threshold = 2\n'
            'loop_max_retries_hard_stop = 3\n'
            'require_fresh_stage_1_to_6_build = true\n',
            encoding="utf-8",
        )

        self.phase_toml = (
            self.tmp_root / ".nebulous" / "phases" / "phase-01-ci-foundation.toml"
        )
        self.phase_toml.write_text(
            'items = [".nebulous/items/item-001-docker-qemu-ci.toml"]\n\n'
            '[phase]\n'
            'id = "phase-01-ci-foundation"\n'
            'project_id = "novos"\n'
            'title = "Test Phase"\n'
            'status = "in_progress"\n'
            'items = [".nebulous/items/item-001-docker-qemu-ci.toml"]\n',
            encoding="utf-8",
        )

        self.item_toml = (
            self.tmp_root / ".nebulous" / "items" / "item-001-docker-qemu-ci.toml"
        )
        write_item_toml_with_diagnostics(
            {
                "item": {
                    "id": "item-001-docker-qemu-ci",
                    "phase_id": "phase-01-ci-foundation",
                    "title": "Test Item",
                    "status": "in_progress",
                    "loop_locked": False,
                    "retry_count": 0,
                    "negative_constraints": [],
                },
                "attempts": [],
            },
            self.item_toml,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_mcp_stdio_initialize_and_tools_list(self) -> None:
        server = NebulousCiMcpServer(repo_root=self.tmp_root)
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            },
        ]
        in_buf = io.BytesIO(
            "\n".join(json.dumps(r) for r in requests).encode("utf-8") + b"\n"
        )
        out_buf = io.BytesIO()
        rc = server.serve_streams(in_buf, out_buf)
        self.assertEqual(rc, EXIT_OK)

        lines = [
            json.loads(line)
            for line in out_buf.getvalue().decode("utf-8").splitlines()
            if line.strip()
        ]
        # Notifications produce no response, so 2 responses expected (id=1 and id=2)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "nebulous_ci")

        tools = lines[1]["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertEqual(
            tool_names,
            [
                "nebulous_get_status",
                "nebulous_verify_variance",
                "nebulous_run_docker_build",
                "nebulous_run_qemu_test",
                "nebulous_record_diagnostic",
                "nebulous_consult_local_llm",
            ],
        )

    def test_real_repo_status_and_variance_check(self) -> None:
        real_server = NebulousCiMcpServer(repo_root=REPO_ROOT)
        status_res = real_server.call_tool("nebulous_get_status", {})
        self.assertFalse(status_res["isError"])
        structured = status_res["structuredContent"]
        self.assertEqual(structured["project"]["id"], "novos")
        self.assertGreaterEqual(len(structured["items"]), 1)

        var_res = real_server.call_tool("nebulous_verify_variance", {"repo_only": True})
        self.assertFalse(var_res["isError"])
        self.assertEqual(var_res["structuredContent"]["status"], "PASS")

    def test_variance_hard_stop_when_package_dropped(self) -> None:
        # Create a manifest that requires a dropped/missing package not present in stage scripts
        (self.tmp_root / ".nebulous" / "manifest.toml").write_text(
            '[packages.core]\n'
            'nonexistent-dropped-pkg-xyz = { required = true, min_version = "1.0" }\n',
            encoding="utf-8",
        )
        server = NebulousCiMcpServer(
            repo_root=self.tmp_root,
            nebulous_ctl=NEBULOUS_CTL,
        )
        res = server.call_tool("nebulous_verify_variance", {"repo_only": True})
        self.assertTrue(res["isError"])
        sc = res["structuredContent"]
        self.assertEqual(sc["status"], "HARD_STOP_VARIANCE_LOCK")
        self.assertIn("strictly prohibited from dropping items", sc["mandatory_instruction"])
        self.assertTrue(len(sc["violations"]) > 0)

    def test_content_length_framed_stdio_and_error_handling(self) -> None:
        server = NebulousCiMcpServer(repo_root=self.tmp_root)
        req_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {"name": "nonexistent_tool", "arguments": {}},
            }
        ).encode("utf-8")
        framed = f"Content-Length: {len(req_body)}\r\n\r\n".encode("ascii") + req_body
        in_buf = io.BytesIO(framed)
        out_buf = io.BytesIO()
        rc = server.serve_streams(in_buf, out_buf)
        self.assertEqual(rc, EXIT_OK)
        raw_out = out_buf.getvalue()
        self.assertTrue(raw_out.startswith(b"Content-Length:"))
        body_part = raw_out.split(b"\r\n\r\n", 1)[1]
        resp = json.loads(body_part.decode("utf-8"))
        self.assertEqual(resp["id"], 99)
        self.assertEqual(resp["error"]["code"], -32602)

    def test_docker_build_and_qemu_test_orchestration_and_loop_lock(self) -> None:
        def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            cmd_str = " ".join(str(x) for x in cmd)
            if "check-guardrail" in cmd_str or "record-attempt" in cmd_str:
                return subprocess.run(cmd, **kwargs)
            if "verify-variance" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        {
                            "status": "PASS",
                            "exit_code": 0,
                            "repo_only": True,
                            "checked_packages_count": 100,
                            "checked_binaries_count": 10,
                            "upgraded_count": 0,
                            "upgrades": [],
                            "violations": [],
                        }
                    ),
                    stderr="",
                )
            if "novos-builder" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    stdout="[INFO] Stage 3\n",
                    stderr="[ERROR] E: Unable to locate package missing-pkg\n",
                )
            if "novos-qemu-tester" in cmd_str or "test-docker-qemu.py" in cmd_str:
                verdict_file = (
                    self.tmp_root / "logs" / "qemu-artifacts" / "last-test-verdict.json"
                )
                verdict_file.write_text(
                    json.dumps(
                        {
                            "tier": "live-boot",
                            "status": "pass",
                            "screendumps": [
                                {"ppm_path": "logs/qemu-artifacts/live-boot-final.ppm"}
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(cmd, 0, stdout="QEMU PASS\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        server = NebulousCiMcpServer(
            repo_root=self.tmp_root,
            nebulous_ctl=NEBULOUS_CTL,
            runner=mock_runner,
        )

        # Reject --from-stage attempts immediately
        bad_build = server.call_tool(
            "nebulous_run_docker_build",
            {"item_id": "item-001-docker-qemu-ci", "profiles": "--from-stage 3"},
        )
        self.assertTrue(bad_build["isError"])
        self.assertEqual(
            bad_build["structuredContent"]["status"], "HARD_STOP_FRESH_BUILD_INVARIANT"
        )

        # Test successful QEMU run first
        qemu_ok = server.call_tool(
            "nebulous_run_qemu_test",
            {"item_id": "item-001-docker-qemu-ci", "tier": "live-boot", "use_docker": True},
        )
        self.assertFalse(qemu_ok["isError"])
        self.assertEqual(qemu_ok["structuredContent"]["status"], "PASS")
        self.assertEqual(len(qemu_ok["structuredContent"]["screendumps"]), 1)

        # Now test 3 consecutive build failures -> negative constraint at 2, HARD_STOP_LOOP_LOCK at 3
        b1 = server.call_tool(
            "nebulous_run_docker_build",
            {"item_id": "item-001-docker-qemu-ci"},
        )
        self.assertEqual(b1["structuredContent"]["status"], "BUILD_FAILED")
        self.assertEqual(b1["structuredContent"]["retry_count"], 1)

        b2 = server.call_tool(
            "nebulous_run_docker_build",
            {"item_id": "item-001-docker-qemu-ci"},
        )
        self.assertEqual(b2["structuredContent"]["status"], "BUILD_FAILED")
        self.assertEqual(b2["structuredContent"]["retry_count"], 2)
        self.assertGreaterEqual(len(b2["structuredContent"]["NEGATIVE_CONSTRAINTS"]), 1)

        b3 = server.call_tool(
            "nebulous_run_docker_build",
            {"item_id": "item-001-docker-qemu-ci"},
        )
        self.assertEqual(b3["structuredContent"]["status"], "HARD_STOP_LOOP_LOCK")
        self.assertTrue(b3["structuredContent"]["loop_locked"])

        # Subsequent QEMU test must be blocked pre-flight by HARD_STOP_LOOP_LOCK
        qemu_blocked = server.call_tool(
            "nebulous_run_qemu_test",
            {"item_id": "item-001-docker-qemu-ci", "tier": "live-boot"},
        )
        self.assertTrue(qemu_blocked["isError"])
        self.assertEqual(
            qemu_blocked["structuredContent"]["status"], "HARD_STOP_LOOP_LOCK"
        )

    def test_record_diagnostic_detects_duplicate_failed_diff(self) -> None:
        server = NebulousCiMcpServer(
            repo_root=self.tmp_root,
            nebulous_ctl=NEBULOUS_CTL,
        )
        sample_diff = "--- a/scripts/stages/03-core-system.sh\n+++ b/scripts/stages/03-core-system.sh\n@@ -1 +1 @@\n-foo\n+bar\n"
        fp, _ = compute_diff_fingerprint(self.tmp_root, proposed_diff=sample_diff)

        # Seed a failed attempt with that diff_fingerprint
        write_item_toml_with_diagnostics(
            {
                "item": {
                    "id": "item-001-docker-qemu-ci",
                    "phase_id": "phase-01-ci-foundation",
                    "title": "Test Item",
                    "status": "in_progress",
                    "loop_locked": False,
                    "retry_count": 1,
                    "negative_constraints": [],
                },
                "attempts": [
                    {
                        "timestamp": "2026-10-01T00:00:00+00:00",
                        "stage": "build",
                        "status": "fail",
                        "error_summary": "Stage 3 failed",
                        "error_fingerprint": "deadbeef12345678",
                        "diff_fingerprint": fp,
                        "modified_files": ["scripts/stages/03-core-system.sh"],
                        "log_file": "",
                    }
                ],
            },
            self.item_toml,
        )

        diag_res = server.call_tool(
            "nebulous_record_diagnostic",
            {
                "item_id": "item-001-docker-qemu-ci",
                "hypothesis": "Re-trying identical patch in 03-core-system.sh",
                "root_cause": "Stage 3 package list",
                "proposed_files": ["scripts/stages/03-core-system.sh"],
                "proposed_diff": sample_diff,
            },
        )
        self.assertTrue(diag_res["isError"])
        sc = diag_res["structuredContent"]
        self.assertEqual(sc["status"], "REJECTED_DUPLICATE_DIFF")
        self.assertTrue(sc["prior_failed_diff_match"])
        self.assertTrue(
            any("LOOP DETECTED" in nc for nc in sc["NEGATIVE_CONSTRAINTS"])
        )

    def test_qemu_failure_no_double_record_and_diagnostics_preserved(self) -> None:
        def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            cmd_str = " ".join(str(x) for x in cmd)
            if "verify-variance" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({"status": "PASS", "exit_code": 0, "violations": []}),
                    stderr="",
                )
            if "novos-qemu-tester" in cmd_str:
                # Simulate test-docker-qemu.py recording 1 failed attempt inside the container
                subprocess.run(
                    [
                        sys.executable,
                        str(NEBULOUS_CTL),
                        "record-attempt",
                        "--item",
                        str(self.item_toml),
                        "--stage",
                        "live-boot",
                        "--status",
                        "fail",
                        "--error-summary",
                        "Guest SSH timeout",
                    ],
                    check=True,
                )
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="QEMU failed")
            return subprocess.run(cmd, **kwargs)

        server = NebulousCiMcpServer(
            repo_root=self.tmp_root,
            nebulous_ctl=NEBULOUS_CTL,
            runner=mock_runner,
        )
        server.call_tool(
            "nebulous_record_diagnostic",
            {
                "item_id": "item-001-docker-qemu-ci",
                "hypothesis": "Verify greetd service unit ordering",
                "root_cause": "cosmic-greeter.service",
            },
        )
        res = server.call_tool(
            "nebulous_run_qemu_test",
            {"item_id": "item-001-docker-qemu-ci", "tier": "live-boot"},
        )
        self.assertTrue(res["isError"])
        self.assertEqual(res["structuredContent"]["status"], "TEST_FAILED")
        self.assertEqual(res["structuredContent"]["retry_count"], 1)

        after = load_toml_file(self.item_toml)
        self.assertEqual(after["item"]["retry_count"], 1)
        self.assertEqual(len(after.get("attempts", [])), 1)
        self.assertEqual(len(after.get("diagnostics", [])), 1)
        self.assertEqual(
            after["diagnostics"][0]["hypothesis"],
            "Verify greetd service unit ordering",
        )

    def test_consult_local_llm_with_mock_http_server(self) -> None:
        import http.server
        import threading

        neg_constraint = "LOOP DETECTED [fingerprint=abc123]: DO NOT drop limine package."
        write_item_toml_with_diagnostics(
            {
                "item": {
                    "id": "item-001-docker-qemu-ci",
                    "phase_id": "phase-01-ci-foundation",
                    "title": "Test Item",
                    "status": "in_progress",
                    "loop_locked": False,
                    "retry_count": 2,
                    "negative_constraints": [neg_constraint],
                },
                "attempts": [],
                "diagnostics": [],
            },
            self.item_toml,
        )

        sample_log = self.tmp_root / "logs" / "serial-failure.log"
        sample_log.write_text(
            "BdsDxe: starting Boot0001\n[ERROR] Kernel panic - not syncing: VFS: Unable to mount root fs\n",
            encoding="utf-8",
        )

        captured_reqs: list[dict[str, Any]] = []

        class _MockLlamaServer(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                captured_reqs.append({"path": self.path, "body": body})
                resp_payload = {
                    "id": "chatcmpl-qwen3-14b",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "root_cause": "Missing live-boot initramfs hook in Stage 6",
                                        "subsystem": "scripts/stages/06-snapshot-finalize.sh",
                                        "recommended_fix": "Run update-initramfs -c -k all after installing live-boot",
                                        "violates_negative_constraints": False,
                                    }
                                ),
                            },
                        }
                    ],
                }
                encoded = json.dumps(resp_payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: Any) -> None:
                return

        httpd = http.server.HTTPServer(("127.0.0.1", 0), _MockLlamaServer)
        port = httpd.server_port
        t = threading.Thread(target=httpd.handle_request, daemon=True)
        t.start()

        server = NebulousCiMcpServer(
            repo_root=self.tmp_root,
            nebulous_ctl=NEBULOUS_CTL,
        )
        res = server.call_tool(
            "nebulous_consult_local_llm",
            {
                "item_id": "item-001-docker-qemu-ci",
                "error_summary": "Kernel panic: VFS: Unable to mount root fs",
                "log_file": "logs/serial-failure.log",
                "proposed_files": ["scripts/stages/06-snapshot-finalize.sh"],
                "llm_endpoint": f"http://127.0.0.1:{port}/v1",
            },
        )
        t.join(timeout=3)
        httpd.server_close()

        self.assertFalse(res["isError"])
        sc = res["structuredContent"]
        self.assertEqual(sc["status"], "DIAGNOSED")
        self.assertTrue(sc["llm_available"])
        self.assertEqual(
            sc["diagnosis"]["root_cause"],
            "Missing live-boot initramfs hook in Stage 6",
        )
        self.assertEqual(
            sc["diagnosis"]["subsystem"],
            "scripts/stages/06-snapshot-finalize.sh",
        )
        self.assertEqual(sc["NEGATIVE_CONSTRAINTS"], [neg_constraint])

        # Verify request sent to mock llama.cpp server
        self.assertEqual(len(captured_reqs), 1)
        self.assertEqual(captured_reqs[0]["path"], "/v1/chat/completions")
        user_msg = json.loads(captured_reqs[0]["body"]["messages"][1]["content"])
        self.assertIn("Unable to mount root fs", user_msg["log_tail"])
        self.assertEqual(user_msg["negative_constraints"], [neg_constraint])

        # Verify diagnosis was persisted into Item.toml [[diagnostics]]
        reloaded = load_toml_file(self.item_toml)
        diags = reloaded.get("diagnostics", [])
        self.assertEqual(len(diags), 1)
        self.assertEqual(
            diags[0]["root_cause"],
            "Missing live-boot initramfs hook in Stage 6",
        )
        self.assertIn("Qwen3-14B", diags[0]["hypothesis"])

        # Verify Qwen3 <think> blocks and markdown code fences are parsed cleanly
        parsed_fenced = server._parse_llm_json_content(
            "<think>Checking initramfs hooks...</think>\n```json\n"
            '{"root_cause": "live-boot missing", "subsystem": "stage-06", '
            '"recommended_fix": "update-initramfs", "violates_negative_constraints": false}\n```'
        )
        self.assertEqual(parsed_fenced.get("root_cause"), "live-boot missing")
        self.assertEqual(parsed_fenced.get("subsystem"), "stage-06")

    def test_strict_input_schema_validation(self) -> None:
        server = NebulousCiMcpServer(repo_root=self.tmp_root)
        # Missing required item_id
        resp_missing = server.handle_jsonrpc_message(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "nebulous_run_docker_build", "arguments": {}},
            }
        )
        self.assertIsNotNone(resp_missing)
        assert resp_missing is not None
        self.assertEqual(resp_missing["error"]["code"], -32602)

        # Unknown property rejected by additionalProperties: False
        resp_unknown = server.handle_jsonrpc_message(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "nebulous_run_docker_build",
                    "arguments": {
                        "item_id": "item-001-docker-qemu-ci",
                        "from_stage": 3,
                    },
                },
            }
        )
        self.assertIsNotNone(resp_unknown)
        assert resp_unknown is not None
        self.assertEqual(resp_unknown["error"]["code"], -32602)


def run_self_tests() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(McpNebulousCiSelfTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return EXIT_OK if result.wasSuccessful() else EXIT_ERROR


# ==============================================================================
# 4. CLI Entrypoint
# ==============================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp_nebulous_ci.py",
        description="NovOS Nebulous CI/CD Model Context Protocol (MCP) JSON-RPC 2.0 Stdio Server",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in unit tests for MCP JSON-RPC 2.0 protocol and all 6 Nebulous CI tools",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_tests()

    server = NebulousCiMcpServer(repo_root=REPO_ROOT)
    return server.serve_streams(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    sys.exit(main())
