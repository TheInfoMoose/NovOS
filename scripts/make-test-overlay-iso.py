#!/usr/bin/env python3
"""
make-test-overlay-iso.py -- derive a test-only NovOS ISO (STEP 15F).

The production ISO is never modified. The derived ISO is the production ISO
plus one extra live-boot layer, /live/zz-novos-test.squashfs, that carries the
harness-only packages (default: qemu-guest-agent) and the dependencies the
production image does not already satisfy. The production
/live/filesystem.squashfs is carried over byte-identical; the script proves
this by hashing it out of both ISOs.

Runs unprivileged: xorriso -osirrox / unsquashfs for extraction, dpkg-deb -x
for staging, mksquashfs -all-root for the layer, xorriso -boot_image any replay
for the remaster. No root, no loop mounts.

Trust chain for downloaded packages:
  production ISO's /usr/share/keyrings/debian-archive-keyring.gpg
    -> gpgv-verified dists/<suite>/InRelease (Valid-Until honored)
    -> SHA256 of main/binary-amd64/Packages.xz listed in InRelease
    -> SHA256 + size of every .deb listed in Packages.

Usage:
  python3 scripts/make-test-overlay-iso.py --iso build/NovOS-sid-amd64.iso
  python3 scripts/make-test-overlay-iso.py --iso <iso> --out build/x-test.iso \
      --packages qemu-guest-agent
  python3 scripts/make-test-overlay-iso.py --iso <iso> --resolve-only
  python3 scripts/make-test-overlay-iso.py --self-test

Exit codes: 0 success, 1 failure (reason printed as "[FATAL] ..."), 2 usage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import lzma
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD_ENV = REPO_ROOT / "config" / "build.env"

ARCH = "amd64"
COMPONENT = "main"
LAYER_ISO_PATH = "/live/zz-novos-test.squashfs"
PROD_SQUASHFS_ISO_PATH = "/live/filesystem.squashfs"
KEYRING_REL = "usr/share/keyrings/debian-archive-keyring.gpg"
DPKG_STATUS_REL = "var/lib/dpkg/status"
TEST_SHARE_REL = "usr/share/novos-test"
MERGED_USR_TOPS = ("bin", "sbin", "lib", "lib32", "lib64", "libx32")
HTTP_TIMEOUT = 120


class Fail(Exception):
    """Fatal, named failure. Never caught except at top level."""


# ---------------------------------------------------------------------------
# Debian version comparison (Debian Policy 5.6.12)
# ---------------------------------------------------------------------------

def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _cmp_fragment(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            bc = _order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def _split_version(v: str) -> tuple[int, str, str]:
    epoch = 0
    if ":" in v:
        full = v
        e, v = v.split(":", 1)
        if not e.isdigit():
            raise Fail(f"invalid version epoch in {full!r}")
        epoch = int(e)
    if "-" in v:
        upstream, rev = v.rsplit("-", 1)
    else:
        upstream, rev = v, ""
    return epoch, upstream, rev


def version_compare(a: str, b: str) -> int:
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)
    if ea != eb:
        return -1 if ea < eb else 1
    c = _cmp_fragment(ua, ub)
    if c:
        return -1 if c < 0 else 1
    c = _cmp_fragment(ra, rb)
    return 0 if c == 0 else (-1 if c < 0 else 1)


def version_satisfies(have: str, op: str | None, want: str | None) -> bool:
    if op is None:
        return True
    assert want is not None
    c = version_compare(have, want)
    if op == "<<":
        return c < 0
    if op == "<=":
        return c <= 0
    if op == "=":
        return c == 0
    if op == ">=":
        return c >= 0
    if op == ">>":
        return c > 0
    raise Fail(f"unknown version operator {op!r}")


# ---------------------------------------------------------------------------
# Control-file and relationship parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rel:
    name: str
    op: str | None = None
    version: str | None = None
    arch_any: bool = False

    def __str__(self) -> str:
        s = self.name + (":any" if self.arch_any else "")
        return f"{s} ({self.op} {self.version})" if self.op else s


_REL_RE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9+.\-]*)(?::(?P<arch>[a-z0-9\-]+))?"
    r"\s*(?:\(\s*(?P<op><<|<=|=|>=|>>|<|>)\s*(?P<ver>[^)\s]+)\s*\))?$"
)


def parse_relation_field(text: str) -> list[list[Rel]]:
    """Parse a Depends-style field into AND-of-OR groups."""
    groups: list[list[Rel]] = []
    text = " ".join(text.split())
    if not text:
        return groups
    for group_txt in text.split(","):
        group_txt = group_txt.strip()
        if not group_txt:
            continue
        alts: list[Rel] = []
        for alt in group_txt.split("|"):
            alt = re.sub(r"\[[^\]]*\]", "", alt)  # arch restrictions
            alt = re.sub(r"<[^>]*>", "", alt)  # build profiles
            alt = alt.strip()
            m = _REL_RE.match(alt)
            if not m:
                raise Fail(f"unparsable relation {alt!r} in {text!r}")
            op = m.group("op")
            if op == "<":
                op = "<="  # obsolete forms, Policy 7.1
            elif op == ">":
                op = ">="
            arch = m.group("arch")
            alts.append(Rel(m.group("name"), op, m.group("ver"), arch == "any"))
        groups.append(alts)
    return groups


def parse_control_stanzas(text: str) -> list[dict[str, str]]:
    stanzas: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    last: str | None = None
    for line in text.splitlines():
        if not line.strip():
            if cur:
                stanzas.append(cur)
            cur, last = {}, None
            continue
        if line[0] in " \t":
            if last is None:
                raise Fail(f"continuation line without field: {line!r}")
            cur[last] += "\n" + line.strip()
            continue
        if ":" not in line:
            raise Fail(f"malformed control line: {line!r}")
        k, v = line.split(":", 1)
        last = k.strip()
        cur[last] = v.strip()
    if cur:
        stanzas.append(cur)
    return stanzas


@dataclass
class Pkg:
    name: str
    version: str
    arch: str
    depends: list[list[Rel]] = field(default_factory=list)
    provides: list[Rel] = field(default_factory=list)
    breaks: list[list[Rel]] = field(default_factory=list)
    stanza: dict[str, str] = field(default_factory=dict)


def _pkg_from_stanza(st: dict[str, str]) -> Pkg:
    for k in ("Package", "Version", "Architecture"):
        if k not in st:
            raise Fail(f"stanza without {k}: {st.get('Package', '?')}")
    deps = parse_relation_field(st.get("Pre-Depends", "")) + parse_relation_field(st.get("Depends", ""))
    provides = [alt for grp in parse_relation_field(st.get("Provides", "")) for alt in grp]
    breaks = parse_relation_field(st.get("Breaks", "")) + parse_relation_field(st.get("Conflicts", ""))
    return Pkg(st["Package"], st["Version"], st["Architecture"], deps, provides, breaks, st)


class Universe:
    """A set of packages indexed by name and by provided name."""

    def __init__(self, pkgs: list[Pkg]):
        self.by_name: dict[str, list[Pkg]] = {}
        self.providers: dict[str, list[tuple[Pkg, Rel]]] = {}
        for p in pkgs:
            if p.arch not in (ARCH, "all"):
                continue
            self.by_name.setdefault(p.name, []).append(p)
            for pr in p.provides:
                self.providers.setdefault(pr.name, []).append((p, pr))

    def satisfiers(self, rel: Rel) -> list[Pkg]:
        out = [p for p in self.by_name.get(rel.name, []) if version_satisfies(p.version, rel.op, rel.version)]
        for p, pr in self.providers.get(rel.name, []):
            if rel.op is None:
                out.append(p)
            elif pr.op == "=" and pr.version and version_satisfies(pr.version, rel.op, rel.version):
                out.append(p)
        return out


def installed_from_status(text: str) -> list[Pkg]:
    pkgs: list[Pkg] = []
    for st in parse_control_stanzas(text):
        words = st.get("Status", "").split()
        if len(words) == 3 and words[2] == "installed":
            pkgs.append(_pkg_from_stanza(st))
    return pkgs


def candidates_from_packages(text: str) -> list[Pkg]:
    return [_pkg_from_stanza(st) for st in parse_control_stanzas(text)]


def resolve(requested: list[str], installed: Universe, archive: Universe) -> list[Pkg]:
    """
    Return the archive packages to add so that `requested` and all their
    Pre-Depends/Depends are satisfied, skipping what `installed` satisfies.
    Never replaces an installed package: if one is installed but at a version
    that does not satisfy a dependency, that is fatal.
    """
    selected: dict[str, Pkg] = {}
    order: list[str] = []
    queue: list[Pkg] = []

    for name in requested:
        if installed.by_name.get(name):
            v = installed.by_name[name][0].version
            raise Fail(f"requested package {name} {v} is already in the production ISO; "
                       "production must not contain test hooks")
        cands = archive.by_name.get(name, [])
        if not cands:
            raise Fail(f"requested package {name} not found in {COMPONENT}/binary-{ARCH}")
        best = max(cands, key=_VersionKey)
        selected[name] = best
        order.append(name)
        queue.append(best)

    def group_satisfied(grp: list[Rel]) -> bool:
        for rel in grp:
            if installed.satisfiers(rel):
                return True
            for p in selected.values():
                if p.name == rel.name and version_satisfies(p.version, rel.op, rel.version):
                    return True
                for pr in p.provides:
                    if pr.name == rel.name and (
                        rel.op is None
                        or (pr.op == "=" and pr.version and version_satisfies(pr.version, rel.op, rel.version))
                    ):
                        return True
        return False

    while queue:
        pkg = queue.pop(0)
        for grp in pkg.depends:
            if group_satisfied(grp):
                continue
            chosen: Pkg | None = None
            blocked: list[str] = []
            for rel in grp:
                inst = installed.by_name.get(rel.name)
                if inst:
                    blocked.append(f"{rel} (production has {rel.name} {inst[0].version})")
                    continue
                real = [p for p in archive.by_name.get(rel.name, [])
                        if version_satisfies(p.version, rel.op, rel.version)]
                if real:
                    chosen = max(real, key=_VersionKey)
                    break
                provs = {p.name: p for p in archive.satisfiers(rel)}
                if len(provs) == 1:
                    chosen = next(iter(provs.values()))
                    if installed.by_name.get(chosen.name):
                        blocked.append(f"{rel} via {chosen.name} (production has "
                                       f"{chosen.name} {installed.by_name[chosen.name][0].version})")
                        chosen = None
                        continue
                    break
                if len(provs) > 1:
                    blocked.append(f"{rel} (ambiguous providers: {', '.join(sorted(provs))})")
            if chosen is None:
                alts = " | ".join(str(r) for r in grp)
                why = "; ".join(blocked) if blocked else "no candidate in archive"
                raise Fail(f"{pkg.name} {pkg.version}: cannot satisfy '{alts}' without "
                           f"replacing a production package: {why}")
            if chosen.name in selected:
                if selected[chosen.name].version != chosen.version:
                    raise Fail(f"conflicting selections for {chosen.name}")
                continue
            selected[chosen.name] = chosen
            order.append(chosen.name)
            queue.append(chosen)

    # Breaks/Conflicts of the added packages against production.
    for name in order:
        p = selected[name]
        for grp in p.breaks:
            for rel in grp:
                for ip in installed.by_name.get(rel.name, []):
                    if version_satisfies(ip.version, rel.op, rel.version):
                        raise Fail(f"{p.name} {p.version} Breaks/Conflicts {rel}; "
                                   f"production has {ip.name} {ip.version}")
    # Breaks/Conflicts of production packages against the added ones (incl. their Provides).
    added = Universe([selected[n] for n in order])
    for ips in installed.by_name.values():
        for ip in ips:
            for grp in ip.breaks:
                for rel in grp:
                    hit = [p for p in added.satisfiers(rel) if p.name != ip.name]
                    if hit:
                        raise Fail(f"production {ip.name} {ip.version} Breaks/Conflicts {rel}; "
                                   f"overlay adds {hit[0].name} {hit[0].version}")
    return [selected[n] for n in order]


class _VersionKey:
    def __init__(self, p: Pkg):
        self.v = p.version

    def __lt__(self, other: "_VersionKey") -> bool:
        return version_compare(self.v, other.v) < 0


# ---------------------------------------------------------------------------
# Subprocess / IO helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], what: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-15:])
        raise Fail(f"{what} failed (exit {proc.returncode}): {' '.join(cmd)}\n{tail}")
    return proc.stdout + proc.stderr


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_build_env() -> tuple[str, str]:
    if not BUILD_ENV.is_file():
        raise Fail(f"{BUILD_ENV} not found")
    vals: dict[str, str] = {}
    for line in BUILD_ENV.read_text().splitlines():
        m = re.match(r'^\s*(?:export\s+)?(DEBIAN_SUITE|DEBIAN_MIRROR)=(["\']?)([^"\'$`]*)\2\s*(?:#.*)?$', line)
        if m:
            vals[m.group(1)] = m.group(3)
    for k in ("DEBIAN_SUITE", "DEBIAN_MIRROR"):
        if not vals.get(k):
            raise Fail(f"{k} not set as a literal in {BUILD_ENV}")
    mirror = vals["DEBIAN_MIRROR"].rstrip("/")
    host = urllib.parse.urlparse(mirror).hostname or ""
    if host not in ("deb.debian.org", "snapshot.debian.org"):
        raise Fail(f"DEBIAN_MIRROR host {host!r} is not a declared Debian mirror")
    return vals["DEBIAN_SUITE"], mirror


def fetch(url: str, dest: pathlib.Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "novos-make-test-overlay-iso"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r, dest.open("wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
    except OSError as e:
        raise Fail(f"download failed: {url}: {e}") from e


def xorriso_extract(iso: pathlib.Path, pairs: list[tuple[str, pathlib.Path]]) -> None:
    cmd = ["xorriso", "-osirrox", "on", "-indev", str(iso)]
    for src, dst in pairs:
        cmd += ["-extract", src, str(dst)]
    run(cmd, f"xorriso extract from {iso.name}")
    for src, dst in pairs:
        if not dst.is_file():
            raise Fail(f"{src} not present in {iso}")


def unsquashfs_file(squashfs: pathlib.Path, root: pathlib.Path, rel: str, depth: int = 0) -> pathlib.Path:
    """Extract one file from the squashfs, following in-image symlinks."""
    if depth > 8:
        raise Fail(f"symlink loop resolving {rel} in {squashfs.name}")
    run(["unsquashfs", "-f", "-no-xattrs", "-d", str(root), str(squashfs), rel],
        f"unsquashfs {rel}")
    p = root / rel
    if p.is_symlink():
        target = os.readlink(p)
        nxt = os.path.normpath(target.lstrip("/") if target.startswith("/")
                               else os.path.join(os.path.dirname(rel), target))
        if nxt.startswith(".."):
            raise Fail(f"{rel} links outside the image: {target}")
        return unsquashfs_file(squashfs, root, nxt, depth + 1)
    if not p.is_file():
        raise Fail(f"{rel} not found in {squashfs.name}")
    return p


def squashfs_types(squashfs: pathlib.Path) -> dict[str, str]:
    """Map every path in the image to 'd', 'l' or 'f'."""
    out = run(["unsquashfs", "-lls", "-d", "R", str(squashfs)], "unsquashfs listing")
    types: dict[str, str] = {}
    for line in out.splitlines():
        m = re.match(r"^([dlcbps-])\S{9}\s+\S+\s+\d+(?:,\s*\d+)?\s+\S+\s+\S+\s+R(/.*)?$", line)
        if not m:
            continue
        path = (m.group(2) or "/").lstrip("/")
        if m.group(1) == "l":
            path = path.split(" -> ", 1)[0]
        types[path] = {"d": "d", "l": "l"}.get(m.group(1), "f")
    if "usr" not in types:
        raise Fail(f"could not list {squashfs.name} (no usr/ in listing)")
    return types


def el_torito_signature(iso: pathlib.Path) -> tuple[list[str], str]:
    out = run(["xorriso", "-indev", str(iso), "-report_el_torito", "plain",
               "-report_system_area", "plain"], f"xorriso boot report {iso.name}")
    entries: list[str] = []
    summary = ""
    for line in out.splitlines():
        if line.startswith("El Torito boot img :"):
            f = line.split(":", 1)[1].split()
            entries.append(f"{f[0]} {f[1]} bootable={f[2]}")
        elif line.startswith("El Torito img path :"):
            entries.append(line.split(":", 1)[1].strip())
        elif line.startswith("System area summary:"):
            summary = line.split(":", 1)[1].strip()
    if not entries:
        raise Fail(f"{iso} has no El Torito boot records")
    return entries, summary


# ---------------------------------------------------------------------------
# Archive metadata
# ---------------------------------------------------------------------------

def verified_release(mirror: str, suite: str, keyring: pathlib.Path, work: pathlib.Path) -> dict[str, str]:
    inrel = work / "InRelease"
    fetch(f"{mirror}/dists/{suite}/InRelease", inrel)
    gnupg = work / "gnupghome"
    gnupg.mkdir(mode=0o700)
    signed = work / "InRelease.verified"
    env = dict(os.environ, GNUPGHOME=str(gnupg), LC_ALL="C")
    out = run(["gpgv", "--status-fd", "1", "--keyring", str(keyring), "--output", str(signed), str(inrel)],
              "gpgv InRelease verification", env=env)
    if not re.search(r"^\[GNUPG:\] VALIDSIG ", out, re.M):
        raise Fail("gpgv did not report VALIDSIG for InRelease")
    stanzas = parse_control_stanzas(signed.read_text())
    if not stanzas:
        raise Fail("verified InRelease is empty")
    rel = stanzas[0]
    if suite not in (rel.get("Suite"), rel.get("Codename")):
        raise Fail(f"InRelease is for {rel.get('Suite')}/{rel.get('Codename')}, expected {suite}")
    now = dt.datetime.now(dt.timezone.utc)
    if "Valid-Until" in rel:
        valid_until = email.utils.parsedate_to_datetime(rel["Valid-Until"])
        if valid_until.tzinfo is None:
            raise Fail(f"InRelease Valid-Until has no timezone: {rel['Valid-Until']}")
        if valid_until < now:
            raise Fail(f"InRelease expired (Valid-Until {rel['Valid-Until']})")
    if "Date" not in rel:
        raise Fail("InRelease has no Date")
    return rel


def verified_packages(mirror: str, suite: str, release: dict[str, str], work: pathlib.Path) -> str:
    target = f"{COMPONENT}/binary-{ARCH}/Packages.xz"
    want = None
    for line in release.get("SHA256", "").splitlines():
        f = line.split()
        if len(f) == 3 and f[2] == target:
            want = (f[0], int(f[1]))
    if want is None:
        raise Fail(f"{target} not listed in InRelease SHA256")
    dest = work / "Packages.xz"
    fetch(f"{mirror}/dists/{suite}/{target}", dest)
    if dest.stat().st_size != want[1] or sha256_file(dest) != want[0]:
        raise Fail(f"{target} does not match InRelease SHA256/size")
    return lzma.decompress(dest.read_bytes()).decode("utf-8")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def build(args: argparse.Namespace) -> None:
    iso = pathlib.Path(args.iso).resolve()
    if not iso.is_file():
        raise Fail(f"production ISO not found: {iso}")
    out = pathlib.Path(args.out).resolve() if args.out else iso.with_name(f"{iso.stem}-test.iso")
    if out == iso:
        raise Fail("--out must differ from --iso; the production ISO is never written")
    out.parent.mkdir(parents=True, exist_ok=True)
    requested = [p.strip() for p in args.packages.split(",") if p.strip()]
    if not requested:
        raise Fail("--packages is empty")
    suite, mirror = read_build_env()
    for tool in ("xorriso", "unsquashfs", "mksquashfs", "gpgv", "dpkg-deb"):
        if shutil.which(tool) is None:
            raise Fail(f"required tool not found: {tool}")

    with tempfile.TemporaryDirectory(prefix=".make-test-overlay-", dir=out.parent) as tmp_s:
        tmp = pathlib.Path(tmp_s)

        print(f"==> Extracting {PROD_SQUASHFS_ISO_PATH} from {iso}")
        prod_sq = tmp / "prod-filesystem.squashfs"
        xorriso_extract(iso, [(PROD_SQUASHFS_ISO_PATH, prod_sq)])
        prod_sha = sha256_file(prod_sq)
        prod_root = tmp / "prod-root"
        status_path = unsquashfs_file(prod_sq, prod_root, DPKG_STATUS_REL)
        keyring = unsquashfs_file(prod_sq, prod_root, KEYRING_REL)
        prod_types = squashfs_types(prod_sq)
        prod_sq.unlink()
        if TEST_SHARE_REL in prod_types:
            raise Fail(f"production image already contains /{TEST_SHARE_REL}; it must carry no test hooks")
        installed = Universe(installed_from_status(status_path.read_text()))
        print(f"    production filesystem.squashfs sha256 {prod_sha}")
        print(f"    production packages installed: {sum(len(v) for v in installed.by_name.values())}")

        print(f"==> Verifying {mirror}/dists/{suite}/InRelease with the ISO's Debian keyring")
        release = verified_release(mirror, suite, keyring, tmp)
        archive = Universe(candidates_from_packages(verified_packages(mirror, suite, release, tmp)))
        print(f"    InRelease Date: {release['Date']}")

        plan = resolve(requested, installed, archive)
        print(f"==> Packages to add ({len(plan)}):")
        for p in plan:
            print(f"    {p.name} {p.version} {p.arch}")
        if args.resolve_only:
            return

        debs_dir = tmp / "debs"
        debs_dir.mkdir()
        stage = tmp / "stage"
        stage.mkdir(mode=0o755)
        manifest_pkgs = []
        unrun_scripts = []
        for p in plan:
            fn = p.stanza.get("Filename")
            sha = p.stanza.get("SHA256")
            size = p.stanza.get("Size")
            if not fn or not sha or not size:
                raise Fail(f"{p.name}: Packages entry lacks Filename/SHA256/Size")
            deb = debs_dir / pathlib.PurePosixPath(fn).name
            fetch(f"{mirror}/{fn}", deb)
            if deb.stat().st_size != int(size) or sha256_file(deb) != sha:
                raise Fail(f"{deb.name}: SHA256/size does not match the signed Packages index")
            run(["dpkg-deb", "-x", str(deb), str(stage)], f"dpkg-deb -x {deb.name}")
            info = run(["dpkg-deb", "-I", str(deb)], f"dpkg-deb -I {deb.name}")
            scripts = [s for s in ("preinst", "postinst")
                       if re.search(rf"^\s+\d+ bytes,\s+\d+ lines\s+\*?\s*{s}\b", info, re.M)]
            if scripts:
                unrun_scripts.append(f"{p.name} ({', '.join(scripts)})")
            manifest_pkgs.append({"package": p.name, "version": p.version, "arch": p.arch,
                                  "sha256": sha, "filename": fn})

        for top in MERGED_USR_TOPS:
            if os.path.lexists(stage / top):
                raise Fail(f"overlay would create /{top}, shadowing the production merged-/usr symlink")
        collisions = []
        for dirpath, dirnames, filenames in os.walk(stage):
            for n in dirnames + filenames:
                rel = os.path.relpath(os.path.join(dirpath, n), stage)
                pt = prod_types.get(rel)
                if pt is None:
                    continue
                st = "d" if os.path.isdir(os.path.join(dirpath, n)) and not os.path.islink(os.path.join(dirpath, n)) else "x"
                if not (pt == "d" and st == "d"):
                    collisions.append(rel)
        if collisions:
            raise Fail("overlay would shadow production paths: " + ", ".join(sorted(collisions)[:20]))

        share = stage / TEST_SHARE_REL
        (share / "debs").mkdir(parents=True)
        for deb in sorted(debs_dir.iterdir()):
            shutil.copy2(deb, share / "debs" / deb.name)
        manifest = {
            "schema": 1,
            "purpose": "NovOS test-only overlay layer (STEP 15F); never part of the production ISO",
            "mirror": mirror,
            "suite": suite,
            "inrelease_date": release["Date"],
            "requested": requested,
            "production_iso": iso.name,
            "production_filesystem_squashfs_sha256": prod_sha,
            "packages": manifest_pkgs,
        }
        (share / "overlay.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.chmod(stage, 0o755)
        for dp, _dirs, files in os.walk(share):
            os.chmod(dp, 0o755)
            for name in files:
                os.chmod(os.path.join(dp, name), 0o644)

        layer = tmp / "zz-novos-test.squashfs"
        print(f"==> Building {layer.name}")
        run(["mksquashfs", str(stage), str(layer), "-all-root", "-noappend", "-comp", "zstd", "-quiet"],
            "mksquashfs overlay layer")
        layer_sha = sha256_file(layer)

        tmp_iso = tmp / "derived.iso"
        print(f"==> Remastering into {out}")
        run(["xorriso", "-indev", str(iso), "-outdev", str(tmp_iso),
             "-map", str(layer), LAYER_ISO_PATH, "-boot_image", "any", "replay"],
            "xorriso remaster")

        print("==> Verifying derived ISO")
        derived_sq = tmp / "derived-filesystem.squashfs"
        derived_layer = tmp / "derived-layer.squashfs"
        xorriso_extract(tmp_iso, [(PROD_SQUASHFS_ISO_PATH, derived_sq), (LAYER_ISO_PATH, derived_layer)])
        derived_sha = sha256_file(derived_sq)
        derived_sq.unlink()
        print(f"    production {PROD_SQUASHFS_ISO_PATH} sha256 {prod_sha}")
        print(f"    derived    {PROD_SQUASHFS_ISO_PATH} sha256 {derived_sha}")
        if derived_sha != prod_sha:
            raise Fail("filesystem.squashfs differs between production and derived ISO")
        if sha256_file(derived_layer) != layer_sha:
            raise Fail(f"{LAYER_ISO_PATH} in derived ISO does not match the built layer")
        prod_boot = el_torito_signature(iso)
        der_boot = el_torito_signature(tmp_iso)
        if prod_boot != der_boot:
            raise Fail(f"boot records differ: production {prod_boot} vs derived {der_boot}")
        print(f"    boot records identical: {'; '.join(prod_boot[0])}; system area {prod_boot[1]}")
        print(f"    {LAYER_ISO_PATH} sha256 {layer_sha}")
        os.replace(tmp_iso, out)

    print(f"==> Derived test ISO: {out}")
    if unrun_scripts:
        print("    maintainer scripts NOT run (files unpacked only): " + "; ".join(unrun_scripts))
    print(f"==> /{TEST_SHARE_REL}/overlay.json:")
    print(json.dumps(manifest, indent=2))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

_STATUS_FIXTURE = """\
Package: libc6
Status: install ok installed
Architecture: amd64
Version: 2.41-6
Provides: libc6-x (= 2.41-6)

Package: libglib2.0-0t64
Status: install ok installed
Architecture: amd64
Version: 2.84.1-1

Package: oldlib
Status: install ok installed
Architecture: amd64
Version: 1.0-1

Package: removed-pkg
Status: deinstall ok config-files
Architecture: amd64
Version: 9.0-1

Package: i386only
Status: install ok installed
Architecture: i386
Version: 1.0-1
"""

_PACKAGES_FIXTURE = """\
Package: qemu-guest-agent
Version: 1:10.0.2+ds-1
Architecture: amd64
Depends: libc6 (>= 2.38), libglib2.0-0t64 (>= 2.80.0), liburing2 (>= 2.3) | liburing-alt,
 libnuma1:any, default-agent-dep, i386only
Pre-Depends: init-system-helpers (>= 1.54~)

Package: liburing2
Version: 2.9-1
Architecture: amd64
Depends: libc6 (>= 2.34)

Package: liburing-alt
Version: 1.0-1
Architecture: amd64

Package: libnuma1
Version: 2.0.19-1
Architecture: amd64
Depends: libc6 (>= 2.14)

Package: init-system-helpers
Version: 1.68
Architecture: all

Package: real-provider
Version: 3-1
Architecture: amd64
Provides: default-agent-dep

Package: i386only
Version: 1.0-1
Architecture: amd64

Package: needs-new-oldlib
Version: 1-1
Architecture: amd64
Depends: oldlib (>= 2.0)

Package: oldlib
Version: 2.0-1
Architecture: amd64

Package: alt-skips-production
Version: 1-1
Architecture: amd64
Depends: oldlib (>= 2.0) | liburing-alt

Package: breaker
Version: 1-1
Architecture: amd64
Breaks: libglib2.0-0t64 (<< 3)
"""


class SelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self.installed = Universe(installed_from_status(_STATUS_FIXTURE))
        self.archive = Universe(candidates_from_packages(_PACKAGES_FIXTURE))

    def test_version_compare(self) -> None:
        cases = [("1.0", "1.0", 0), ("1.0~rc1", "1.0", -1), ("1:0.1", "2.0", 1), ("1.0-1", "1.0-2", -1),
                 ("2.10", "2.9", 1), ("1.0+b1", "1.0", 1), ("1.54~", "1.54", -1), ("1.0a", "1.0", 1),
                 ("0:1.0", "1.0", 0), ("1.0-1~bpo1", "1.0-1", -1), ("1.001", "1.1", 0)]
        for a, b, want in cases:
            self.assertEqual(version_compare(a, b), want, f"{a} vs {b}")

    def test_operators(self) -> None:
        self.assertTrue(version_satisfies("2.0", ">=", "2.0"))
        self.assertFalse(version_satisfies("2.0", ">>", "2.0"))
        self.assertTrue(version_satisfies("1.9", "<<", "2.0"))
        self.assertTrue(version_satisfies("2.0", "<=", "2.0"))
        self.assertTrue(version_satisfies("2.0", "=", "2.0"))
        self.assertFalse(version_satisfies("2.0", "=", "2.0-1"))
        self.assertTrue(version_satisfies("5", None, None))

    def test_parse_relations(self) -> None:
        g = parse_relation_field("a (>= 1), b:any | c (<< 2) [amd64], d <!nocheck>, e (> 1)")
        self.assertEqual(g[0], [Rel("a", ">=", "1")])
        self.assertEqual(g[1], [Rel("b", None, None, True), Rel("c", "<<", "2")])
        self.assertEqual(g[2], [Rel("d")])
        self.assertEqual(g[3], [Rel("e", ">=", "1")])
        with self.assertRaises(Fail):
            parse_relation_field("Bad Name (>= 1)")

    def test_status_only_installed(self) -> None:
        self.assertIn("libc6", self.installed.by_name)
        self.assertNotIn("removed-pkg", self.installed.by_name)
        self.assertNotIn("i386only", self.installed.by_name)  # foreign arch does not satisfy

    def test_resolver(self) -> None:
        plan = [p.name for p in resolve(["qemu-guest-agent"], self.installed, self.archive)]
        self.assertEqual(plan[0], "qemu-guest-agent")
        self.assertIn("init-system-helpers", plan)  # Pre-Depends; tilde version
        self.assertIn("liburing2", plan)  # first alternative chosen
        self.assertNotIn("liburing-alt", plan)
        self.assertIn("libnuma1", plan)  # :any qualifier
        self.assertIn("real-provider", plan)  # single provider of a virtual package
        self.assertIn("i386only", plan)  # foreign-arch install does not satisfy
        self.assertNotIn("libc6", plan)  # satisfied by production
        self.assertNotIn("libglib2.0-0t64", plan)
        self.assertEqual(len(plan), len(set(plan)))

    def test_alternative_skips_production_upgrade(self) -> None:
        plan = [p.name for p in resolve(["alt-skips-production"], self.installed, self.archive)]
        self.assertEqual(plan, ["alt-skips-production", "liburing-alt"])

    def test_never_upgrades_production(self) -> None:
        with self.assertRaisesRegex(Fail, "production has oldlib 1.0-1"):
            resolve(["needs-new-oldlib"], self.installed, self.archive)

    def test_requested_already_in_production(self) -> None:
        with self.assertRaisesRegex(Fail, "already in the production ISO"):
            resolve(["oldlib"], self.installed, self.archive)

    def test_breaks_production(self) -> None:
        with self.assertRaisesRegex(Fail, "Breaks/Conflicts"):
            resolve(["breaker"], self.installed, self.archive)

    def test_production_breaks_added(self) -> None:
        inst = Universe(installed_from_status(_STATUS_FIXTURE + "\nPackage: guard\nStatus: install ok installed\n"
                                              "Architecture: amd64\nVersion: 1\nConflicts: liburing-alt\n"))
        with self.assertRaisesRegex(Fail, "production guard 1 Breaks/Conflicts"):
            resolve(["alt-skips-production"], inst, self.archive)

    def test_missing_package(self) -> None:
        with self.assertRaisesRegex(Fail, "not found"):
            resolve(["nonexistent"], self.installed, self.archive)

    def test_provides_versioned(self) -> None:
        self.assertTrue(self.installed.satisfiers(Rel("libc6-x", ">=", "2.40")))
        self.assertFalse(self.installed.satisfiers(Rel("libc6-x", ">=", "2.42")))


def self_test() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SelfTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Derive a test-only NovOS ISO with a qemu-guest-agent layer.")
    ap.add_argument("--iso", help="production ISO (read only)")
    ap.add_argument("--out", help="derived ISO path (default: <iso-stem>-test.iso next to --iso)")
    ap.add_argument("--packages", default="qemu-guest-agent", help="comma-separated packages for the layer")
    ap.add_argument("--resolve-only", action="store_true",
                    help="verify the archive and print the packages to add; build nothing")
    ap.add_argument("--self-test", action="store_true", help="run resolver unit tests and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.iso:
        ap.error("--iso is required")
    try:
        build(args)
    except Fail as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
