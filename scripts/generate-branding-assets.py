#!/usr/bin/env python3
"""
Deterministic Nova Branding Asset & Multi-Aspect Wallpaper Suite Generator for NovOS.

Generates:
  - branding/novos-logo.svg
  - branding/novos-logo.png (256x256 RGBA PNG)
  - branding/nova-wallpaper.svg (1920x1080 SVG)
  - Photorealistic Cosmic Supernova Wallpapers (derived from branding/nova-realistic-base.png):
      * branding/nova-realistic-16x9.png (3840x2160 16:9 4K UHD)
      * branding/nova-realistic-32x9.png (5120x1440 32:9 Super Ultrawide)
      * branding/nova-wallpaper.png (1920x1080 Full HD default alias for Limine/Calamares/COSMIC)
  - Centered Accretion-Swirl & Monochrome Flat-Logo Wallpapers:
      * branding/nova-minimal-faceted-light-16x9.png (3840x2160 NovOS Indigo + Diffused Orange Accretion Swirl)
      * branding/nova-minimal-faceted-light-32x9.png (5120x1440 NovOS Indigo + Diffused Orange Accretion Swirl)
      * branding/nova-minimal-faceted-dark-16x9.png (3840x2160 Charcoal Grayscale + Diffused Orange Accretion Swirl)
      * branding/nova-minimal-faceted-dark-32x9.png (5120x1440 Charcoal Grayscale + Diffused Orange Accretion Swirl)
  - Offset Motion Paint-Splatter & Stenciled NovOS Logo Wallpapers:
      * branding/nova-minimal-silhouette-light-16x9.png (3840x2160 NovOS Indigo + Orange Motion Splatter & Stencil)
      * branding/nova-minimal-silhouette-light-32x9.png (5120x1440 NovOS Indigo + Orange Motion Splatter & Stencil)
      * branding/nova-minimal-silhouette-dark-16x9.png (3840x2160 Grayscale Splatter + NovOS Orange Stencil Outline)
      * branding/nova-minimal-silhouette-dark-32x9.png (5120x1440 Grayscale Splatter + NovOS Orange Stencil Outline)

Uses Pillow (LANCZOS) + NumPy for high-speed 4K/32:9 synthesis when available on the host,
with a deterministic pure-Python standard library (struct, zlib, math) fallback.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

try:
    from PIL import Image, ImageFilter

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

HAS_PIL_NUMPY = HAS_PIL and HAS_NUMPY


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def write_rgba_png(path: Path, width: int, height: int, raw_scanlines: bytes | bytearray) -> None:
    """Write an 8-bit RGBA (color_type=6) PNG file from filter-prefixed raw scanlines."""
    expected_len = height * (1 + width * 4)
    if len(raw_scanlines) != expected_len:
        raise ValueError(f"Expected {expected_len} scanline bytes, got {len(raw_scanlines)}")

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_scanlines), level=3)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(signature)
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", compressed))
        f.write(_png_chunk(b"IEND", b""))


def _clamp_byte(val: float) -> int:
    if val <= 0.0:
        return 0
    if val >= 255.0:
        return 255
    return int(val + 0.5)


def generate_logo_svg(path: Path) -> None:
    svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <defs>
    <radialGradient id="obsidianBadge" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#1B1B1B"/>
      <stop offset="65%" stop-color="#121521"/>
      <stop offset="100%" stop-color="#0B0C10"/>
    </radialGradient>
    <linearGradient id="accretionRingOuter" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FF6B1A"/>
      <stop offset="50%" stop-color="#FFAD00"/>
      <stop offset="100%" stop-color="#FF6B1A"/>
    </linearGradient>
    <linearGradient id="accretionRingInner" x1="100%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#FFAD00"/>
      <stop offset="50%" stop-color="#FFB347"/>
      <stop offset="100%" stop-color="#FF6B1A"/>
    </linearGradient>
    <radialGradient id="coreGlow" cx="50%" cy="50%" r="45%">
      <stop offset="0%" stop-color="#FFF3E0" stop-opacity="1.0"/>
      <stop offset="35%" stop-color="#FFAD00" stop-opacity="0.85"/>
      <stop offset="70%" stop-color="#FF6B1A" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#FF6B1A" stop-opacity="0.0"/>
    </radialGradient>
  </defs>

  <!-- Deep Obsidian Circular Badge (#1B1B1B -> #0B0C10) -->
  <circle cx="128" cy="128" r="120" fill="url(#obsidianBadge)" stroke="#FFAD00" stroke-width="3" stroke-opacity="0.60"/>
  <circle cx="128" cy="128" r="112" fill="none" stroke="#262626" stroke-width="1.5"/>

  <!-- Glowing Nova Amber-Orange Accretion Rings (#FF6B1A -> #FFAD00) -->
  <ellipse cx="128" cy="128" rx="92" ry="38" transform="rotate(-28 128 128)"
           fill="none" stroke="url(#accretionRingOuter)" stroke-width="7" stroke-opacity="0.92"/>
  <ellipse cx="128" cy="128" rx="76" ry="30" transform="rotate(-28 128 128)"
           fill="none" stroke="url(#accretionRingInner)" stroke-width="3.5" stroke-opacity="0.88"/>
  <circle cx="128" cy="128" r="58" fill="none" stroke="url(#accretionRingOuter)" stroke-width="2.5" stroke-opacity="0.65" stroke-dasharray="18 8"/>

  <!-- Supernova Core Halo -->
  <circle cx="128" cy="128" r="52" fill="url(#coreGlow)"/>

  <!-- Secondary Diagonal Star Rays -->
  <polygon points="128,84 134,122 172,128 134,134 128,172 122,134 84,128 122,122"
           fill="#FFAD00" opacity="0.88" transform="rotate(45 128 128)"/>

  <!-- Radiant 4-Point Star Flare Core (#FFF3E0) -->
  <polygon points="128,28 139,117 228,128 139,139 128,228 117,139 28,128 117,117"
           fill="#FFF3E0"/>
  <circle cx="128" cy="128" r="14" fill="#FFF3E0"/>
</svg>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def _sample_supernova_emblem(dx: float, dy: float, radius: float) -> tuple[float, float, float, float]:
    """
    Evaluate the geometric Supernova emblem at offset (dx, dy) around center
    for a given badge radius. Returns (r, g, b, a) in 0..255.
    """
    dist = math.hypot(dx, dy)
    if dist > radius + 1.5:
        return (0.0, 0.0, 0.0, 0.0)

    badge_alpha = max(0.0, min(1.0, (radius + 0.75 - dist) / 1.5))
    norm_r = dist / radius

    # Base deep obsidian circular badge (#0B0C10 edge -> #1B1B1B inner)
    if norm_r >= 0.75:
        r, g, b = 11.0, 12.0, 16.0
    else:
        t_bg = max(0.0, 1.0 - norm_r / 0.75)
        r = 11.0 + 16.0 * t_bg
        g = 12.0 + 15.0 * t_bg
        b = 16.0 + 11.0 * t_bg

    # Outer rim glow (#FFAD00 / #FF6B1A)
    rim_dist = abs(norm_r - 0.96)
    if rim_dist < 0.04:
        rim_w = (1.0 - rim_dist / 0.04) * 0.65
        r = r * (1.0 - rim_w) + 255.0 * rim_w
        g = g * (1.0 - rim_w) + 173.0 * rim_w
        b = b * (1.0 - rim_w) + 0.0 * rim_w

    # Tilted accretion rings (-28 degrees)
    cos_a = 0.882947592858927
    sin_a = 0.4694715627858908
    ux = (dx * cos_a - dy * sin_a) / radius
    uy = (dx * sin_a + dy * cos_a) / radius

    ell1 = math.hypot(ux / 0.72, uy / 0.30)
    ell2 = math.hypot(ux / 0.58, uy / 0.23)

    # Radial core glow (#FF6B1A -> #FFAD00) beneath rings and star flare
    if norm_r < 0.45:
        glow = ((1.0 - norm_r / 0.45) ** 2.0) * 0.75
        r = r * (1.0 - glow) + 255.0 * glow
        g = g * (1.0 - glow) + 155.0 * glow
        b = b * (1.0 - glow) + 20.0 * glow

    ring1_d = abs(ell1 - 1.0)
    if ring1_d < 0.08:
        w = 1.0 if ring1_d <= 0.012 else ((1.0 - (ring1_d - 0.012) / 0.068) ** 1.5)
        # Gradient #FF6B1A (255, 107, 26) -> #FFAD00 (255, 173, 0)
        if ux <= -0.55:
            grad_t = 0.0
        elif ux >= 0.55:
            grad_t = 1.0
        else:
            grad_t = (ux + 0.55) / 1.10
        rr = 255.0
        rg = 107.0 + (173.0 - 107.0) * grad_t
        rb = 26.0 * (1.0 - grad_t)
        r = r * (1.0 - w) + rr * w
        g = g * (1.0 - w) + rg * w
        b = b * (1.0 - w) + rb * w

    ring2_d = abs(ell2 - 1.0)
    if ring2_d < 0.06:
        w = 1.0 if ring2_d <= 0.010 else ((1.0 - (ring2_d - 0.010) / 0.050) ** 1.5)
        # Inner ring gradient #FFAD00 (255, 173, 0) -> #FFB347 (255, 179, 71)
        t2 = 1.0 if ux >= 0.25 else (0.0 if ux <= -0.25 else (ux + 0.25) / 0.50)
        rr = 255.0
        rg = 173.0 + (179.0 - 173.0) * t2
        rb = 0.0 + 71.0 * t2
        r = r * (1.0 - w) + rr * w
        g = g * (1.0 - w) + rg * w
        b = b * (1.0 - w) + rb * w

    # Circular inner accretion ring
    c_ring_d = abs(norm_r - 0.45)
    if c_ring_d < 0.03:
        w = (1.0 - c_ring_d / 0.03) * 0.55
        r = r * (1.0 - w) + 255.0 * w
        g = g * (1.0 - w) + 150.0 * w
        b = b * (1.0 - w) + 18.0 * w

    # Secondary 45-degree diagonal star rays (#FFAD00 = 255, 173, 0)
    inv_sqrt2 = 0.7071067811865475
    rx = abs(dx + dy) * inv_sqrt2 / radius
    ry = abs(dx - dy) * inv_sqrt2 / radius
    diag_metric = min(rx + ry * 7.5, ry + rx * 7.5)
    if diag_metric < 0.38 and norm_r < 0.38:
        d_flare = (max(0.0, 1.0 - diag_metric / 0.38) ** 2.0) * 0.85
        r = r * (1.0 - d_flare) + 255.0 * d_flare
        g = g * (1.0 - d_flare) + 173.0 * d_flare
        b = b * (1.0 - d_flare) + 10.0 * d_flare

    # 4-point star flare core (#FFF3E0 = 255, 243, 224)
    ax = abs(dx) / radius
    ay = abs(dy) / radius
    star_metric = min(ax + ay * 6.5, ay + ax * 6.5)
    if star_metric < 0.78 and norm_r < 0.78:
        flare = 1.0 if star_metric <= 0.03 else (max(0.0, 1.0 - (star_metric - 0.03) / 0.75) ** 2.2)
        r = r * (1.0 - flare) + 255.0 * flare
        g = g * (1.0 - flare) + 243.0 * flare
        b = b * (1.0 - flare) + 224.0 * flare

    # Central bright nucleus (#FFF3E0)
    if norm_r < 0.15:
        core_w = 1.0 if norm_r <= 0.055 else (max(0.0, 1.0 - (norm_r - 0.055) / 0.095) ** 1.6)
        r = r * (1.0 - core_w) + 255.0 * core_w
        g = g * (1.0 - core_w) + 243.0 * core_w
        b = b * (1.0 - core_w) + 224.0 * core_w

    return (r, g, b, badge_alpha * 255.0)


def generate_logo_png(path: Path) -> None:
    width, height = 256, 256
    cx, cy = 127.5, 127.5
    radius = 120.0
    row_stride = 1 + width * 4
    buf = bytearray(height * row_stride)

    for y in range(height):
        row_off = y * row_stride
        buf[row_off] = 0
        dy = y - cy
        for x in range(width):
            dx = x - cx
            r, g, b, a = _sample_supernova_emblem(dx, dy, radius)
            px = row_off + 1 + x * 4
            buf[px] = _clamp_byte(r)
            buf[px + 1] = _clamp_byte(g)
            buf[px + 2] = _clamp_byte(b)
            buf[px + 3] = _clamp_byte(a)

    write_rgba_png(path, width, height, buf)


def _deterministic_stars(width: int, height: int, count: int = 140) -> list[tuple[int, int, int, float]]:
    """Generate a deterministic list of (x, y, radius_px, brightness) stars."""
    stars: list[tuple[int, int, int, float]] = []
    state = 0x4E4F5641  # "NOVA"
    for i in range(count):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        x = 16 + (state % (width - 32))
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        y = 16 + (state % (height - 32))
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        rad = 2 if (i % 11 == 0) else 1
        brightness = 0.35 + ((state & 0xFF) / 255.0) * 0.60
        stars.append((x, y, rad, brightness))
    return stars


def generate_wallpaper_svg(path: Path) -> None:
    width, height = 1920, 1080
    stars = _deterministic_stars(width, height, count=140)
    star_elements = []
    for sx, sy, srad, sbright in stars:
        color = "#FFF3E0" if srad > 1 else "#D8E2FF"
        star_elements.append(
            f'    <circle cx="{sx}" cy="{sy}" r="{srad}" fill="{color}" opacity="{sbright:.2f}"/>'
        )
    stars_xml = "\n".join(star_elements)

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <defs>
    <!-- Deep Cosmic Space Background (#0B0C10 -> #121521 -> #1B1B1B) -->
    <linearGradient id="cosmicSpace" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0B0C10"/>
      <stop offset="60%" stop-color="#121521"/>
      <stop offset="100%" stop-color="#1B1B1B"/>
    </linearGradient>

    <!-- Warm Cosmic Amber (#FFAD00) and Nova Orange (#FF6B1A) Horizon Nebula Arc -->
    <radialGradient id="nebulaGlow" cx="62%" cy="88%" r="65%">
      <stop offset="0%" stop-color="#FFAD00" stop-opacity="0.58"/>
      <stop offset="32%" stop-color="#FF6B1A" stop-opacity="0.38"/>
      <stop offset="68%" stop-color="#FF6B1A" stop-opacity="0.10"/>
      <stop offset="100%" stop-color="#0B0C10" stop-opacity="0.0"/>
    </radialGradient>

    <linearGradient id="horizonArc" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#FF6B1A" stop-opacity="0.0"/>
      <stop offset="30%" stop-color="#FF6B1A" stop-opacity="0.75"/>
      <stop offset="65%" stop-color="#FFAD00" stop-opacity="0.92"/>
      <stop offset="100%" stop-color="#FF6B1A" stop-opacity="0.15"/>
    </linearGradient>

    <radialGradient id="emblemHalo" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#FFF3E0" stop-opacity="0.95"/>
      <stop offset="28%" stop-color="#FFAD00" stop-opacity="0.65"/>
      <stop offset="60%" stop-color="#FF6B1A" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#FF6B1A" stop-opacity="0.0"/>
    </radialGradient>
  </defs>

  <!-- Deep Space Backdrop -->
  <rect width="{width}" height="{height}" fill="url(#cosmicSpace)"/>

  <!-- Subtle Starfield -->
  <g id="starfield">
{stars_xml}
  </g>

  <!-- Horizon Nebula Glow & Arc -->
  <rect width="{width}" height="{height}" fill="url(#nebulaGlow)"/>
  <path d="M -100,1040 Q 980,680 2020,960" fill="none" stroke="url(#horizonArc)" stroke-width="6" opacity="0.85"/>
  <path d="M -100,1065 Q 980,715 2020,995" fill="none" stroke="#FFAD00" stroke-width="2" opacity="0.45"/>

  <!-- Center-Right Supernova Emblem (cx=1280, cy=460) -->
  <g transform="translate(1280, 460)">
    <circle cx="0" cy="0" r="220" fill="url(#emblemHalo)"/>
    <circle cx="0" cy="0" r="145" fill="#1B1B1B" fill-opacity="0.88" stroke="#FFAD00" stroke-width="3" stroke-opacity="0.65"/>
    <ellipse cx="0" cy="0" rx="115" ry="46" transform="rotate(-28)" fill="none" stroke="#FF6B1A" stroke-width="8" stroke-opacity="0.90"/>
    <ellipse cx="0" cy="0" rx="94" ry="36" transform="rotate(-28)" fill="none" stroke="#FFAD00" stroke-width="4" stroke-opacity="0.85"/>
    <circle cx="0" cy="0" r="68" fill="url(#emblemHalo)"/>
    <polygon points="0,-58 8,-8 58,0 8,8 0,58 -8,8 -58,0 -8,-8" fill="#FFAD00" opacity="0.85" transform="rotate(45)"/>
    <polygon points="0,-125 14,-14 125,0 14,14 0,125 -14,14 -125,0 -14,-14" fill="#FFF3E0"/>
    <circle cx="0" cy="0" r="16" fill="#FFF3E0"/>
  </g>
</svg>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def _crop_and_scale_realistic(
    master_img: "Image.Image",
    target_w: int,
    target_h: int,
    vertical_focus: float = 0.50,
) -> "Image.Image":
    """
    Center-crop `master_img` to match `(target_w / target_h)` aspect ratio around `vertical_focus`,
    then resample with LANCZOS and apply a subtle micro-contrast unsharp pass for 4K/ultrawide crispness.
    """
    src_w, src_h = master_img.size
    target_aspect = target_w / target_h
    src_aspect = src_w / src_h

    if src_aspect > target_aspect:
        # Source is wider than target: crop width
        crop_h = src_h
        crop_w = int(round(src_h * target_aspect))
        left = (src_w - crop_w) // 2
        top = 0
    else:
        # Source is taller than target (e.g., 16:9 cropped to 32:9): crop height
        crop_w = src_w
        crop_h = int(round(src_w / target_aspect))
        left = 0
        center_y = int(round(src_h * vertical_focus))
        top = max(0, min(src_h - crop_h, center_y - crop_h // 2))

    cropped = master_img.crop((left, top, left + crop_w, top + crop_h))
    scaled = cropped.resize((target_w, target_h), resample=Image.Resampling.LANCZOS)
    if target_w >= 2560:
        scaled = scaled.filter(ImageFilter.UnsharpMask(radius=1.3, percent=40, threshold=2))
    return scaled


def generate_realistic_wallpapers(
    base_path: Path,
    out_16x9: Path,
    out_32x9: Path,
    out_default_fhd: Path,
) -> None:
    """
    Generate photorealistic Nova wallpapers from `branding/nova-realistic-base.png`:
      - `nova-realistic-16x9.png` (3840x2160)
      - `nova-realistic-32x9.png` (5120x1440)
      - `nova-wallpaper.png` (1920x1080 default alias)
    """
    if HAS_PIL and base_path.is_file():
        master = Image.open(base_path).convert("RGB")
        img_16x9 = _crop_and_scale_realistic(master, 3840, 2160, vertical_focus=0.50)
        img_16x9.save(out_16x9, format="PNG", compress_level=2)

        img_32x9 = _crop_and_scale_realistic(master, 5120, 1440, vertical_focus=0.47)
        img_32x9.save(out_32x9, format="PNG", compress_level=2)

        img_fhd = _crop_and_scale_realistic(master, 1920, 1080, vertical_focus=0.50)
        img_fhd.save(out_default_fhd, format="PNG", compress_level=3)
    else:
        # Pure-stdlib fallback if Pillow is absent
        _fallback_scanline_wallpaper(out_default_fhd, 1920, 1080)
        _fallback_scanline_wallpaper(out_16x9, 2560, 1440)
        _fallback_scanline_wallpaper(out_32x9, 5120, 1440)


def _compute_novos_stencil_sdf_np(radius: int) -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    """
    Compute the signed distance field (in pixels, < 0 inside emblem, > 0 outside)
    for the stenciled/monochrome NovOS Supernova emblem on a `(2*pad + 1, 2*pad + 1)` grid:
      - Primary 4-point compass/supernova star flare
      - Secondary 45-degree diagonal star rays
      - Dual concentric tilted (-28 deg) elliptical accretion rings
      - Dashed circular inner orbital ring (12 segments)
    Returns `(sdf_px, u, v)`.
    """
    pad = int(math.ceil(radius * 1.08))
    coords = np.arange(-pad, pad + 1, dtype=np.float32)
    dy, dx = np.meshgrid(coords, coords, indexing="ij")
    inv_r = 1.0 / float(max(1, radius))
    u = dx * inv_r
    v = dy * inv_r
    r = np.hypot(u, v)

    # 1. Primary 4-point compass/supernova star (vertical & horizontal arms reaching 0.96 * radius)
    l_card = 0.96
    w_card = 0.088
    au = np.abs(u)
    av = np.abs(v)
    w_h = w_card * (1.0 - au / l_card)
    d_horiz = np.maximum((av - w_h) * 0.996, au - l_card)
    w_v = w_card * (1.0 - av / l_card)
    d_vert = np.maximum((au - w_v) * 0.996, av - l_card)
    d_star_card = np.minimum(d_horiz, d_vert)

    # 2. Secondary 45-degree diagonal star rays (reaching 0.42 * radius)
    inv_sqrt2 = 0.70710678
    rx = np.abs(u + v) * inv_sqrt2
    ry = np.abs(u - v) * inv_sqrt2
    l_diag = 0.42
    w_diag = 0.056
    w_d1 = w_diag * (1.0 - rx / l_diag)
    d_diag1 = np.maximum((ry - w_d1) * 0.992, rx - l_diag)
    w_d2 = w_diag * (1.0 - ry / l_diag)
    d_diag2 = np.maximum((rx - w_d2) * 0.992, ry - l_diag)
    d_star = np.minimum(d_star_card, np.minimum(d_diag1, d_diag2))

    # 3. Tilted concentric elliptical accretion rings (-28 degrees)
    cos_a = 0.88294759
    sin_a = 0.46947156
    ux = u * cos_a - v * sin_a
    uy = u * sin_a + v * cos_a

    # Outer tilted ellipse (a1=0.77, b1=0.30) with gradient-normalized Euclidean distance
    a1, b1 = 0.77, 0.30
    f1 = np.hypot(ux / a1, uy / b1)
    grad1 = np.maximum(np.hypot(ux / (a1 * a1), uy / (b1 * b1)) / np.maximum(f1, 1e-4), 1.0)
    dist_ell1 = np.abs(f1 - 1.0) / grad1
    hw1 = 0.0135 * (0.85 + 0.28 * np.clip((ux / a1) ** 2, 0.0, 1.2))
    d_ell1 = dist_ell1 - hw1

    # Inner tilted ellipse (a2=0.60, b2=0.22)
    a2, b2 = 0.60, 0.22
    f2 = np.hypot(ux / a2, uy / b2)
    grad2 = np.maximum(np.hypot(ux / (a2 * a2), uy / (b2 * b2)) / np.maximum(f2, 1e-4), 1.0)
    dist_ell2 = np.abs(f2 - 1.0) / grad2
    d_ell2 = dist_ell2 - 0.0095

    # 4. Dashed circular inner orbital ring (r = 0.46, 12 crisp segments)
    r_orb = 0.46
    d_rad = np.abs(r - r_orb) - 0.0105
    theta = np.arctan2(v, u)
    period = math.pi / 6.0
    half_p = math.pi / 12.0
    seg_angle = np.abs(((theta + half_p) % period) - half_p)
    dash_half = 0.142
    d_arc = (seg_angle - dash_half) * r_orb
    d_orb = np.maximum(d_rad, d_arc)

    # Stencil bridge clearance where rings approach the primary star flare
    star_bridge_gap = 0.0095
    d_rings = np.minimum(np.minimum(d_ell1, d_ell2), d_orb)
    d_rings_bridged = np.maximum(d_rings, -(d_star - star_bridge_gap))

    sdf_norm = np.minimum(d_star, d_rings_bridged)
    sdf_px = sdf_norm * float(radius)
    return sdf_px, u, v


def _composite_flat_monochrome_logo_np(
    canvas: "np.ndarray",
    cx: int,
    cy: int,
    radius: int,
    flat_rgb: tuple[float, float, float],
) -> None:
    """
    Render the centered monochrome NovOS logo with uniform flat shading (100% flat fill
    lightened from the background without 3D bevels, translucency, or color gradients).
    """
    h, w, _ = canvas.shape
    sdf_px, _, _ = _compute_novos_stencil_sdf_np(radius)
    ph, pw = sdf_px.shape
    pad = ph // 2

    y0 = max(0, cy - pad)
    y1 = min(h, cy + pad + 1)
    x0 = max(0, cx - pad)
    x1 = min(w, cx + pad + 1)

    py0 = y0 - (cy - pad)
    py1 = py0 + (y1 - y0)
    px0 = x0 - (cx - pad)
    px1 = px0 + (x1 - x0)

    sdf_sub = sdf_px[py0:py1, px0:px1]
    alpha = np.clip(0.5 - sdf_sub / 1.35, 0.0, 1.0)[:, :, None]
    roi = canvas[y0:y1, x0:x1]
    logo_color = np.array(flat_rgb, dtype=np.float32)[None, None, :]
    roi[:] = roi * (1.0 - alpha) + logo_color * alpha


def _upsample_float_rgb_to_canvas(
    r: "np.ndarray", g: "np.ndarray", b: "np.ndarray", width: int, height: int
) -> "np.ndarray":
    """Upsample half-resolution float32 R, G, B planes to (height, width, 3) float32 without 8-bit pre-quantization."""
    rf = np.asarray(
        Image.fromarray(r.astype(np.float32), mode="F").resize((width, height), resample=Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    gf = np.asarray(
        Image.fromarray(g.astype(np.float32), mode="F").resize((width, height), resample=Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    bf = np.asarray(
        Image.fromarray(b.astype(np.float32), mode="F").resize((width, height), resample=Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    return np.stack([rf, gf, bf], axis=-1)


def _save_dithered_canvas_np(path: Path, canvas: "np.ndarray") -> None:
    """Apply deterministic 8x8 Bayer sub-LSB dithering to eliminate 8-bit gradient banding before saving."""
    h, w, _ = canvas.shape
    bayer8 = (
        np.array(
            [
                [0, 32, 8, 40, 2, 34, 10, 42],
                [48, 16, 56, 24, 50, 18, 58, 26],
                [12, 44, 4, 36, 14, 46, 6, 38],
                [60, 28, 52, 20, 62, 30, 54, 22],
                [3, 35, 11, 43, 1, 33, 9, 41],
                [51, 19, 59, 27, 49, 17, 57, 25],
                [15, 47, 7, 39, 13, 45, 5, 37],
                [63, 31, 55, 23, 61, 29, 53, 21],
            ],
            dtype=np.float32,
        )
        / 64.0
        - 0.5
    )
    dither = np.tile(bayer8, (h // 8 + 1, w // 8 + 1))[:h, :w, None]
    out_u8 = np.clip(canvas + dither + 0.5, 0.0, 255.0).astype(np.uint8)
    out_img = Image.fromarray(out_u8, mode="RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(path, format="PNG", compress_level=2)


def generate_minimal_faceted_wallpaper(path: Path, width: int, height: int, is_dark: bool) -> None:
    """
    Style 1: Centered Accretion-Swirl & Monochrome Flat-Shaded NovOS Logo Wallpaper.
      - Strict Negative-Space Central Disk & Fuzzy Accretion Border:
          * Circular negative-space background disk (`r_circ = hypot(xn, yn)`) around the centered logo
            (`logo_radius = 0.21 * H`). Inside `r_circ <= 0.235`, `orange_weight` is strictly `0.0` so
            orange never touches or bleeds into the logo area.
          * Immediately outside `r_circ = 0.235`, the swirling NovOS Orange (`#FF6B1A` / `#FFAD00`) accretion
            disk ramps smoothly (`smoothstep` from `r_circ = 0.235` to `0.33`) to form a soft, fuzzy glowing
            border framing the logo's dark negative-space disk, fed by diffused top and bottom edge inflows.
      - `is_dark=False` (`nova-minimal-faceted-light-*`):
          * Darker NovOS Deep Indigo background (`#121521` / `#161B29`) with `#0B0C10` peripheral vignette.
          * Centered flat-shaded monochrome logo rendered in lighter indigo (`#2D3856`) on the untouched
            darker indigo negative-space disk.
      - `is_dark=True` (`nova-minimal-faceted-dark-*`):
          * Charcoal grayscale background (`#1B1B1B` center -> `#0B0C10` peripheral vignette).
          * Centered flat-shaded monochrome logo rendered in lighter neutral gray (`#34343A`) on the untouched
            charcoal negative-space disk, surrounded by the fuzzy NovOS Orange accretion border.
    """
    if not HAS_PIL_NUMPY:
        _fallback_minimal_wallpaper(path, width, height, is_dark, style="faceted")
        return

    hw, hh = width // 2, height // 2
    yn = np.linspace(-0.5, 0.5, hh, dtype=np.float32)[:, None]
    aspect = width / float(height)
    xn = (np.linspace(-0.5, 0.5, hw, dtype=np.float32) * aspect)[None, :]

    # Peripheral vignette (darkening indigo or charcoal around the outer periphery and corners to #0B0C10)
    rad_ell = np.hypot(xn / (0.52 * aspect), yn / 0.53)
    vignette = np.clip((rad_ell - 0.18) / 0.82, 0.0, 1.0) ** 1.60
    center_lift = np.exp(-((rad_ell / 0.78) ** 2))
    # Horizontal-only vignette for the orange gradient so top/bottom edge inflows remain visible at yn = +-0.5
    horiz_vignette = np.clip((np.abs(xn) / (0.46 * aspect) - 0.25) / 0.75, 0.0, 1.0) ** 1.5

    # Circular distance from center (in units of H) for the strict negative-space background disk
    r_circ = np.hypot(xn, yn)
    theta_circ = np.arctan2(yn, xn)

    # Strict negative-space disk mask: strictly 0.0 inside r_circ <= 0.235 (around logo_radius = 0.21 * H),
    # then smoothstep ramp from r_circ = 0.235 to 0.33 for a soft, fuzzy glowing accretion border.
    t_neg = np.clip((r_circ - 0.235) / (0.33 - 0.235), 0.0, 1.0)
    neg_space_mask = (t_neg * t_neg * (3.0 - 2.0 * t_neg)) * (r_circ > 0.235)

    # Tilted coordinates (-28 deg) matching the NovOS logo's accretion disk plane
    cos_a = 0.88294759
    sin_a = 0.46947156
    ux = xn * cos_a - yn * sin_a
    uy = xn * sin_a + yn * cos_a

    # 1. Fuzzy circular + tilted accretion border framing the negative-space disk (`r_circ > 0.235`)
    spiral_circ_1 = 2.0 * theta_circ - 4.2 * np.log(np.maximum(r_circ, 0.20)) + 0.55
    swirl_tex_1 = np.clip(0.5 + 0.5 * np.cos(spiral_circ_1), 0.0, 1.0) ** 1.45
    swirl_tex_2 = np.clip(0.5 + 0.5 * np.cos(spiral_circ_1 - 1.35), 0.0, 1.0) ** 1.75

    # Soft fuzzy glowing halo ring immediately outside r_circ = 0.235 (peaking around r_circ ~ 0.30..0.34)
    fuzzy_border_halo = np.exp(-(((r_circ - 0.305) / 0.088) ** 2)) * (
        0.66 + 0.34 * swirl_tex_1
    )

    # Outer tilted accretion swirl wings extending into the surrounding space
    r_disk = np.hypot(ux / 0.42, uy / 0.26)
    theta_disk = np.arctan2(uy / 0.26, ux / 0.42)
    spiral_phase_1 = 2.0 * theta_disk - 3.4 * np.log(np.maximum(r_disk, 0.22)) + 0.65
    spiral_arm_1 = np.clip(0.5 + 0.5 * np.cos(spiral_phase_1), 0.0, 1.0) ** 1.65
    spiral_arm_2 = np.clip(0.5 + 0.5 * np.cos(spiral_phase_1 - 1.10), 0.0, 1.0) ** 2.00

    ring_envel_main = np.exp(-(((r_disk - 0.92) / 0.36) ** 2))
    ring_envel_outer = np.exp(-(((r_disk - 1.28) / 0.48) ** 2))

    accretion_disk = (
        0.92 * fuzzy_border_halo
        + 0.72 * ring_envel_main * (0.48 + 0.52 * spiral_arm_1)
        + 0.48 * ring_envel_outer * (0.35 + 0.65 * spiral_arm_2 + 0.25 * swirl_tex_2)
    )

    # 2. Continuous S-curve swirling inflow streams from TOP edge (yn = -0.5) and BOTTOM edge (yn = +0.5)
    # feeding tangentially into the fuzzy accretion border around r_circ ~ 0.30..0.35
    s_top = np.clip((yn + 0.50) / 0.54, 0.0, 1.0)
    x_path_top = -0.06 * ((1.0 - s_top) ** 1.4) + 0.29 * np.sin(s_top * math.pi * 0.68)
    w_path_top = 0.24 - 0.06 * s_top
    top_stream = (
        np.exp(-(((xn - x_path_top) / w_path_top) ** 2))
        * np.exp(-((np.maximum(0.0, yn - 0.08) / 0.16) ** 2))
        * (0.96 - 0.12 * s_top)
    )

    x_path_top_sub = -0.08 - 0.22 * np.sin(s_top * math.pi * 0.78)
    top_sub_stream = (
        0.48
        * np.exp(-(((xn - x_path_top_sub) / 0.18) ** 2))
        * np.exp(-((np.maximum(0.0, yn + 0.02) / 0.18) ** 2))
    )

    s_bot = np.clip((0.50 - yn) / 0.54, 0.0, 1.0)
    x_path_bot = 0.06 * ((1.0 - s_bot) ** 1.4) - 0.29 * np.sin(s_bot * math.pi * 0.68)
    w_path_bot = 0.24 - 0.06 * s_bot
    bot_stream = (
        np.exp(-(((xn - x_path_bot) / w_path_bot) ** 2))
        * np.exp(-((np.maximum(0.0, -yn - 0.08) / 0.16) ** 2))
        * (0.96 - 0.12 * s_bot)
    )

    x_path_bot_sub = 0.08 + 0.22 * np.sin(s_bot * math.pi * 0.78)
    bot_sub_stream = (
        0.48
        * np.exp(-(((xn - x_path_bot_sub) / 0.18) ** 2))
        * np.exp(-((np.maximum(0.0, -yn + 0.02) / 0.18) ** 2))
    )

    # Softly diffused top & bottom edge horizon glow bleeding inward from yn = -0.5 and yn = +0.5
    d_top = np.clip(yn + 0.5, 0.0, 1.0)
    d_bot = np.clip(0.5 - yn, 0.0, 1.0)
    top_edge_glow = np.exp(-((d_top / 0.26) ** 1.40)) * np.exp(-(((xn + 0.02) / (0.40 * aspect)) ** 2))
    bot_edge_glow = np.exp(-((d_bot / 0.26) ** 1.40)) * np.exp(-(((xn - 0.02) / (0.40 * aspect)) ** 2))

    swirl_streams = top_stream + bot_stream + top_sub_stream + bot_sub_stream
    orange_raw = (
        0.70 * swirl_streams + 0.55 * (top_edge_glow + bot_edge_glow) + 0.98 * accretion_disk
    ) * neg_space_mask

    # Smoothly diffuse into the indigo/charcoal canvas; enforce strict 0.0 inside r_circ <= 0.235
    orange_weight = np.where(
        r_circ <= 0.235,
        0.0,
        np.tanh(orange_raw * 0.82) * 0.30 * (1.0 - 0.85 * horiz_vignette) * neg_space_mask,
    )

    # Chromatic-preserving NovOS Orange palette (#FF6B1A Nova Orange base with subtle #FFAD00 Cosmic Amber
    # warmth in peak filaments, avoiding sRGB dark-yellow olive shift)
    amber_mix = np.clip((accretion_disk * 0.32 + swirl_streams * 0.18) ** 1.50, 0.0, 0.38) * neg_space_mask
    or_r = 255.0
    or_g = 107.0 + (173.0 - 107.0) * amber_mix
    or_b = 26.0 * (1.0 - amber_mix)

    if is_dark:
        # Dark Grayscale Variant: #1B1B1B (27, 27, 27) center -> #0B0C10 (11, 12, 16) peripheral vignette
        bg_r = 18.0 + 9.0 * center_lift - 7.0 * vignette
        bg_g = 18.0 + 9.0 * center_lift - 6.0 * vignette
        bg_b = 20.0 + 7.0 * center_lift - 4.0 * vignette
        # Lighter neutral gray flat monochrome logo (#34343A = rgb(52, 52, 58)) on untouched charcoal disk
        flat_logo_rgb = (52.0, 52.0, 58.0)
    else:
        # Indigo Variant: Darker NovOS Deep Indigo #121521 (18, 21, 33) / #161B29 (22, 27, 41) -> #0B0C10 (11, 12, 16) vignette
        bg_r = 16.0 + 6.0 * center_lift - 5.0 * vignette
        bg_g = 19.0 + 8.0 * center_lift - 7.0 * vignette
        bg_b = 31.0 + 10.0 * center_lift - 15.0 * vignette
        # Lighter indigo flat monochrome logo (#2D3856 = rgb(45, 56, 86)) on untouched darker indigo disk
        flat_logo_rgb = (45.0, 56.0, 86.0)

    # Attenuate background blue & green slightly faster under the orange glow so indigo + amber stays rich warm orange
    blue_atten = np.clip(orange_weight * 1.65, 0.0, 1.0)
    green_atten = np.clip(orange_weight * 1.15, 0.0, 1.0)
    r = bg_r * (1.0 - orange_weight) + or_r * orange_weight
    g = bg_g * (1.0 - green_atten) + or_g * orange_weight
    b = bg_b * (1.0 - blue_atten) + or_b * orange_weight

    canvas = _upsample_float_rgb_to_canvas(r, g, b, width, height)

    # Enforce strict zero orange bleed at full resolution inside the negative-space disk (r_circ <= 0.235)
    # and composite the centered flat-shaded monochrome NovOS logo (logo_radius = 0.21 * H)
    emblem_radius = int(round(height * 0.21))
    _composite_flat_monochrome_logo_np(
        canvas, width // 2, height // 2, emblem_radius, flat_rgb=flat_logo_rgb
    )

    _save_dithered_canvas_np(path, canvas)


def _stamp_droplet_batch_np(
    mask: "np.ndarray",
    xs: "np.ndarray",
    ys: "np.ndarray",
    rads: "np.ndarray",
    stretches: "np.ndarray",
    angles: "np.ndarray",
    lobes: "np.ndarray",
) -> None:
    """
    Vectorized ROI stamping of smooth circular, organic-lobed, and teardrop-streaked paint droplets
    onto full-resolution float32 `mask` (H, W).
    """
    h, w = mask.shape
    for i in range(len(xs)):
        cx_f = float(xs[i])
        cy_f = float(ys[i])
        rad = float(rads[i])
        stretch = float(stretches[i])
        ang = float(angles[i])
        lobe_amp = float(lobes[i])

        max_ext = int(math.ceil(rad * max(stretch, 1.0) * (1.0 + lobe_amp) + 2.5))
        x0 = max(0, int(cx_f) - max_ext)
        x1 = min(w, int(cx_f) + max_ext + 1)
        y0 = max(0, int(cy_f) - max_ext)
        y1 = min(h, int(cy_f) + max_ext + 1)
        if x0 >= x1 or y0 >= y1:
            continue

        yy = np.arange(y0, y1, dtype=np.float32)[:, None] - cy_f
        xx = np.arange(x0, x1, dtype=np.float32)[None, :] - cx_f

        if stretch > 1.03:
            ca = math.cos(ang)
            sa = math.sin(ang)
            ux = xx * ca + yy * sa
            uy = -xx * sa + yy * ca
            # Elongated teardrop/streak shape: rounded head in motion direction (+ux), long tapered wake (-ux)
            taper = np.where(ux < 0.0, 1.0 / stretch, 1.0 / (1.0 + 0.30 * (stretch - 1.0)))
            width_pinch = np.where(
                ux < 0.0,
                1.0 + 0.56 * np.clip(-ux / max(1.0, rad * stretch), 0.0, 1.0),
                1.0,
            )
            dist = np.hypot(ux * taper, uy * width_pinch)
        else:
            dist = np.hypot(xx, yy)

        if lobe_amp > 0.01 and rad >= 9.0:
            th = np.arctan2(yy, xx)
            mod = 1.0 + lobe_amp * (0.62 * np.cos(2.0 * th + ang) + 0.38 * np.sin(3.0 * th - ang * 1.3))
            eff_rad = rad * mod
        else:
            eff_rad = rad

        drop_alpha = np.clip((eff_rad - dist) / 1.20 + 0.5, 0.0, 1.0)
        roi = mask[y0:y1, x0:x1]
        np.maximum(roi, drop_alpha, out=roi)


def _synthesize_splatter_and_motion_trail_np(
    width: int, height: int, cx: int, cy: int
) -> tuple["np.ndarray", "np.ndarray"]:
    """
    Synthesize the high-resolution asymmetric ovoid/teardrop paint-splatter spot at `(cx, cy)`
    and the turbulent left-to-right dynamic motion droplet trail (`x ~ 0.08*W .. 0.70*W`) with
    elongated teardrops/streaks (`2.0x .. 5.5x`) and steep power-law (Pareto) droplet size diversity.
    Returns:
      - `paint_mask`: (H, W) float32 in [0..1] anti-aliased paint coverage
      - `paint_tone`: (H, W) float32 in [0..1] smooth impasto liquid paint shading
    """
    rng = np.random.default_rng(0x4E4F5653)
    paint_mask = np.zeros((height, width), dtype=np.float32)

    r_base = 0.245 * float(height)
    roi_pad_x = int(math.ceil(0.62 * float(height)))
    roi_pad_y = int(math.ceil(0.49 * float(height)))
    x0 = max(0, cx - roi_pad_x)
    x1 = min(width, cx + roi_pad_x + 1)
    y0 = max(0, cy - roi_pad_y)
    y1 = min(height, cy + roi_pad_y + 1)

    dy = np.arange(y0 - cy, y1 - cy, dtype=np.float32)[:, None]
    dx = np.arange(x0 - cx, x1 - cx, dtype=np.float32)[None, :]

    # 1. Asymmetric tilted ovoid / teardrop main splatter spot (a_x ≈ 1.24 * r_base, b_y ≈ 0.88 * r_base,
    #    with a rounded impact front on the right and a tapered/flaring wake on the left)
    tilt_rad = -0.105  # ~ -6 deg oblique impact tilt
    ct = math.cos(tilt_rad)
    st = math.sin(tilt_rad)
    dx_t = dx * ct - dy * st
    dy_t = dx * st + dy * ct

    a_x = 1.24 * r_base
    b_y = 0.88 * r_base
    # Teardrop modulation: rounded fuller front on the right (dx_t > 0), tapered elongated wake on the left (dx_t < 0)
    u_raw = dx_t / a_x
    x_scale = np.where(dx_t >= 0.0, 0.94 * a_x, 1.12 * a_x)
    y_pinch = np.where(
        dx_t < 0.0,
        1.0 - 0.16 * np.clip(-u_raw, 0.0, 1.25),
        1.0 + 0.05 * np.clip(u_raw, 0.0, 1.0),
    )
    ovoid_rho = np.hypot(dx_t / x_scale, dy_t / (b_y * np.maximum(0.65, y_pinch)))
    ovoid_dist = ovoid_rho * r_base
    theta = np.arctan2(dy_t / b_y, dx_t / a_x)

    # Multi-harmonic organic puddle boundary around `r_base` in ovoid metric space
    r_bound = np.full_like(theta, r_base, dtype=np.float32)
    low_modes = [
        (2, 0.046, 0.25),
        (3, 0.062, -1.15),
        (4, 0.044, 1.85),
        (5, 0.052, 0.65),
        (6, 0.036, -0.80),
        (8, 0.030, 1.40),
        (11, 0.026, -2.05),
        (15, 0.020, 0.50),
        (21, 0.015, 1.10),
        (27, 0.011, -0.70),
    ]
    for m, amp, phase in low_modes:
        r_bound += (r_base * amp) * np.cos(float(m) * theta + phase)

    # 2. Asymmetric high-velocity splash spikes, ragged liquid fingers, and elongated spray filaments
    #    concentrated predominantly backward (left / upper-left / lower-left) and outward at oblique angles
    norm_r = ovoid_rho
    spike_ext = np.zeros_like(theta, dtype=np.float32)

    # 22 ragged liquid fingers biased heavily toward the left/upper-left/lower-left wake and oblique flanks
    finger_angles = np.array(
        [
            -3.08, -2.92, -2.76, -2.58, -2.41, -2.22, -2.02, -1.80, -1.52, -1.18, -0.76,
            0.72, 1.14, 1.48, 1.76, 1.98, 2.18, 2.36, 2.54, 2.72, 2.89, 3.04,
        ],
        dtype=np.float32,
    ) + rng.uniform(-0.035, 0.035, size=22).astype(np.float32)
    finger_lengths = np.array(
        [
            0.84, 0.62, 0.92, 0.56, 0.88, 0.68, 0.78, 0.54, 0.62, 0.44, 0.34,
            0.32, 0.46, 0.58, 0.52, 0.76, 0.64, 0.86, 0.58, 0.90, 0.66, 0.80,
        ],
        dtype=np.float32,
    ) * r_base
    finger_widths = rng.uniform(0.028, 0.062, size=22).astype(np.float32)
    finger_curv = rng.uniform(-0.055, 0.055, size=22).astype(np.float32)
    taper_finger = np.exp(-1.75 * np.clip(norm_r - 0.76, 0.0, 1.7))

    for i in range(len(finger_angles)):
        th_i = float(finger_angles[i])
        w_i = float(finger_widths[i])
        l_i = float(finger_lengths[i])
        c_i = float(finger_curv[i])
        dth = (theta - th_i + c_i * np.clip(norm_r - 0.78, 0.0, 1.4) + math.pi) % (2.0 * math.pi) - math.pi
        w_eff = np.maximum(0.0042, w_i * taper_finger)
        spike_ext = np.maximum(spike_ext, l_i * np.exp(-((dth / w_eff) ** 2)))

    # 34 fine high-velocity spray filaments flaring backward and obliquely outward
    ray_angles = finger_angles[rng.integers(0, len(finger_angles), size=34)] + rng.uniform(-0.12, 0.12, size=34)
    ray_lengths = rng.uniform(0.42, 1.05, size=34) * r_base
    ray_widths = rng.uniform(0.008, 0.018, size=34)
    taper_ray = np.exp(-2.35 * np.clip(norm_r - 0.82, 0.0, 1.7))
    for i in range(34):
        th_i = float(ray_angles[i])
        w_i = float(ray_widths[i])
        l_i = float(ray_lengths[i])
        dth = (theta - th_i + math.pi) % (2.0 * math.pi) - math.pi
        w_eff = np.maximum(0.0016, w_i * taper_ray)
        spike_ext = np.maximum(spike_ext, l_i * np.exp(-((dth / w_eff) ** 2)))

    total_bound = r_bound + spike_ext
    core_alpha = np.clip((total_bound - ovoid_dist) / 1.30 + 0.5, 0.0, 1.0)
    paint_mask[y0:y1, x0:x1] = core_alpha

    # Helper to map ovoid polar coordinates (ang, dist_mult) around (cx, cy) with elongated outward trajectory
    def _ovoid_corona_positions(angs: "np.ndarray", dist_mults: "np.ndarray") -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
        ca = np.cos(angs)
        sa = np.sin(angs)
        xs_loc = np.where(ca >= 0.0, 0.94 * a_x, 1.12 * a_x) * ca * dist_mults
        ys_loc = b_y * sa * dist_mults
        xs_out = float(cx) + (xs_loc * ct + ys_loc * st)
        ys_out = float(cy) + (-xs_loc * st + ys_loc * ct)
        traj_ang = np.arctan2(-xs_loc * st + ys_loc * ct, xs_loc * ct + ys_loc * st)
        return xs_out, ys_out, traj_ang

    # 3. Asymmetric Corona of Elongated Droplets with Steep Power-Law Size Diversity
    #    (~32 large/medium-large drops in corona + ~46 in trail = ~78 total large drops,
    #     surrounded by 1,200+ corona small/micro specks & 1,800+ trail small/micro specks = 3,000+ total)
    # 3a. Corona Large/Medium-Large Elongated Splash Drops (32 drops, stretch 2.1x .. 4.8x)
    n_cor_large = 32
    chosen_fl = finger_angles[rng.integers(0, len(finger_angles), size=n_cor_large)]
    ang_cl = chosen_fl + rng.normal(0.0, 0.14, size=n_cor_large)
    dist_cl = rng.uniform(0.92, 1.52, size=n_cor_large)
    xs_cl, ys_cl, tang_cl = _ovoid_corona_positions(ang_cl, dist_cl)
    rad_cl = rng.uniform(0.0052, 0.0155, size=n_cor_large) * float(height)
    str_cl = rng.uniform(2.10, 4.80, size=n_cor_large)
    dir_cl = tang_cl + rng.normal(0.0, 0.12, size=n_cor_large)
    lob_cl = rng.uniform(0.03, 0.10, size=n_cor_large)
    _stamp_droplet_batch_np(paint_mask, xs_cl, ys_cl, rad_cl, str_cl, dir_cl, lob_cl)

    # 3b. Corona Small & Fine Pareto-Distributed Elongated Spray Droplets (1,250 drops, stretch 2.0x .. 5.2x)
    n_cor_small = 1250
    # 65% clustered around large corona drops & finger wakes, 35% turbulent oblique corona scatter
    cluster_pick = rng.integers(0, n_cor_large, size=n_cor_small)
    use_cluster = rng.uniform(0.0, 1.0, size=n_cor_small) < 0.65
    ang_cs = np.where(
        use_cluster,
        ang_cl[cluster_pick] + rng.normal(0.0, 0.09, size=n_cor_small),
        finger_angles[rng.integers(0, len(finger_angles), size=n_cor_small)] + rng.normal(0.0, 0.18, size=n_cor_small),
    )
    dist_cs = np.where(
        use_cluster,
        dist_cl[cluster_pick] + rng.normal(0.0, 0.16, size=n_cor_small),
        rng.uniform(0.94, 1.95, size=n_cor_small),
    )
    xs_cs, ys_cs, tang_cs = _ovoid_corona_positions(ang_cs, np.clip(dist_cs, 0.86, 2.15))
    # Steep Pareto power-law size distribution (alpha=2.8) bounded to small/micro droplets
    u_pareto_c = rng.uniform(0.0, 1.0, size=n_cor_small)
    pareto_c = (1.0 - 0.92 * u_pareto_c) ** (-1.0 / 2.8) - 1.0  # in [0 .. ~1.46]
    rad_cs = (0.00055 + 0.0022 * np.clip(pareto_c / 1.46, 0.0, 1.0)) * float(height)
    str_cs = rng.uniform(2.00, 5.20, size=n_cor_small)
    dir_cs = tang_cs + rng.normal(0.0, 0.16, size=n_cor_small)
    lob_cs = np.zeros(n_cor_small, dtype=np.float32)
    _stamp_droplet_batch_np(paint_mask, xs_cs, ys_cs, rad_cs, str_cs, dir_cs, lob_cs)

    # 4. Irregular, Turbulent Left-to-Right Motion Splatter Trail (`0.08 * W .. 0.70 * W`)
    #    Replaces regular parallel stream tracks with turbulent burst clusters, erratic lateral whip
    #    trajectories, and natural void pockets, while enforcing steep Pareto size diversity and
    #    progressive left-to-right max droplet growth.
    x_trail_start = 0.08 * float(width)
    x_trail_end = float(cx) - 0.36 * a_x
    trail_span = max(1.0, x_trail_end - x_trail_start)

    # Define 14 irregular turbulent burst nodes & whip arcs across t in [0.04 .. 0.96] (leaving natural void pockets)
    burst_t = np.array(
        [0.05, 0.13, 0.22, 0.31, 0.39, 0.48, 0.56, 0.63, 0.70, 0.76, 0.82, 0.88, 0.93, 0.97],
        dtype=np.float32,
    )
    burst_y_off = np.array(
        [0.012, -0.028, 0.038, -0.052, 0.018, 0.074, -0.086, 0.042, -0.118, 0.105, -0.048, 0.138, -0.145, 0.032],
        dtype=np.float32,
    ) * float(height)
    burst_whip_ang = np.array(
        [0.08, -0.16, 0.22, -0.26, 0.14, 0.31, -0.29, 0.19, -0.35, 0.33, -0.21, 0.38, -0.36, 0.12],
        dtype=np.float32,
    )
    burst_spread_x = (0.025 + 0.045 * burst_t) * trail_span
    burst_spread_y = (0.016 + 0.068 * (burst_t ** 1.25)) * float(height)

    # 4a. Trail Large / Medium-Large Elongated Hero Drops (46 drops, concentrated in middle-to-right t,
    #     progressively larger toward the main ovoid spot on the right, elongated 2.2x .. 5.5x)
    n_tr_large = 46
    u_tl = rng.uniform(0.0, 1.0, size=n_tr_large)
    t_tl = 0.18 + 0.82 * (u_tl ** 0.72)
    b_idx_l = np.argmin(np.abs(t_tl[:, None] - burst_t[None, :]), axis=1)
    xs_tl = x_trail_start + t_tl * trail_span + rng.normal(0.0, 0.45, size=n_tr_large) * burst_spread_x[b_idx_l]
    ys_tl = (
        float(cy)
        + burst_y_off[b_idx_l] * rng.uniform(0.45, 1.25, size=n_tr_large)
        + rng.normal(0.0, 0.75, size=n_tr_large) * burst_spread_y[b_idx_l]
    )
    # Progressive max droplet size from left to right
    rad_tl = (0.0038 + 0.0125 * (t_tl ** 1.65)) * rng.uniform(0.72, 1.18, size=n_tr_large) * float(height)
    str_tl = rng.uniform(2.20, 5.50, size=n_tr_large)
    ang_tl = burst_whip_ang[b_idx_l] * rng.uniform(0.5, 1.25, size=n_tr_large) + rng.normal(0.0, 0.14, size=n_tr_large)
    lob_tl = rng.uniform(0.03, 0.09, size=n_tr_large)
    _stamp_droplet_batch_np(paint_mask, xs_tl, ys_tl, rad_tl, str_tl, ang_tl, lob_tl)

    # 4b. High-Density Small & Micro Elongated Trail Droplets (1,850 drops across t in [0..1],
    #     heavily satellite-clustered around the 46 large trail drops + turbulent burst whips)
    n_tr_small = 1850
    # 55% satellite swarm around the 46 large trail drops; 45% turbulent burst & whip dispersion across full span
    sat_mask = rng.uniform(0.0, 1.0, size=n_tr_small) < 0.55
    parent_idx = rng.integers(0, n_tr_large, size=n_tr_small)

    u_ts = rng.uniform(0.0, 1.0, size=n_tr_small)
    t_free = 0.12 * u_ts + 0.88 * (u_ts ** 1.35)
    b_idx_s = rng.integers(0, len(burst_t), size=n_tr_small)
    t_burst = np.clip(burst_t[b_idx_s] + rng.normal(0.0, 0.048, size=n_tr_small), 0.0, 1.0)
    t_ts = np.where(sat_mask, t_tl[parent_idx], np.where(rng.uniform(0.0, 1.0, size=n_tr_small) < 0.72, t_burst, t_free))

    # Irregular turbulent whip offsets with natural void pockets
    whip_phase = rng.uniform(0.0, 2.0 * math.pi, size=n_tr_small)
    free_x = x_trail_start + t_ts * trail_span + rng.normal(0.0, 1.0, size=n_tr_small) * (0.018 * trail_span)
    free_y = (
        float(cy)
        + burst_y_off[b_idx_s] * (0.35 + 0.75 * np.sin(whip_phase))
        + rng.normal(0.0, 1.0, size=n_tr_small) * (0.018 + 0.095 * (t_ts ** 1.20)) * float(height)
    )

    sat_dist_x = rng.normal(0.0, 1.0, size=n_tr_small) * (0.014 + 0.032 * t_ts) * float(width)
    sat_dist_y = rng.normal(0.0, 1.0, size=n_tr_small) * (0.014 + 0.040 * t_ts) * float(height)
    xs_ts = np.where(sat_mask, xs_tl[parent_idx] + sat_dist_x, free_x)
    ys_ts = np.where(sat_mask, ys_tl[parent_idx] + sat_dist_y, free_y)

    # Steep Pareto power-law droplet radius (alpha = 2.65) scaled by progressive left-to-right envelope
    u_pareto_t = rng.uniform(0.0, 1.0, size=n_tr_small)
    pareto_t = (1.0 - 0.94 * u_pareto_t) ** (-1.0 / 2.65) - 1.0  # in [0 .. ~1.89]
    pareto_norm = np.clip(pareto_t / 1.89, 0.0, 1.0)
    left_to_right_scale = 0.38 + 0.62 * (t_ts ** 1.15)
    rad_ts = (0.00050 + 0.0031 * pareto_norm * left_to_right_scale) * float(height)

    # Elongated teardrop/streak aspect ratio (2.0x .. 5.5x) aligned with local trajectory + angular scatter
    str_ts = rng.uniform(2.00, 5.50, size=n_tr_small)
    base_ang = np.where(sat_mask, ang_tl[parent_idx], burst_whip_ang[b_idx_s])
    ang_ts = base_ang + rng.normal(0.0, 0.22, size=n_tr_small)
    lob_ts = np.zeros(n_tr_small, dtype=np.float32)
    _stamp_droplet_batch_np(paint_mask, xs_ts, ys_ts, rad_ts, str_ts, ang_ts, lob_ts)

    # 5. Realistic impasto liquid paint shading across the ovoid main spot and droplets
    paint_tone = np.full((height, width), 0.52, dtype=np.float32)
    radial_warmth = np.exp(-((ovoid_rho / 0.88) ** 2))
    radial_ridges = (
        0.50 * np.cos(14.0 * theta + 0.4)
        + 0.30 * np.sin(23.0 * theta - 1.1)
        + 0.20 * np.cos(37.0 * theta + 0.8)
    ) * np.exp(-(((norm_r - 0.62) / 0.42) ** 2))
    rim_bevel = np.exp(-(((total_bound - ovoid_dist - 4.5) / 6.0) ** 2)) * (ovoid_dist <= total_bound)
    paint_tone[y0:y1, x0:x1] = np.clip(
        0.48 + 0.34 * radial_warmth + 0.045 * radial_ridges + 0.12 * rim_bevel,
        0.0,
        1.0,
    )

    return paint_mask, paint_tone


def generate_minimal_silhouette_wallpaper(path: Path, width: int, height: int, is_dark: bool) -> None:
    """
    Style 2: Offset Motion Paint-Splatter & Stenciled NovOS Logo Wallpaper.
      - `is_dark=False` (`nova-minimal-silhouette-light-*`):
          * NovOS Deep Indigo background (`#121521` / `#0F121C`) with subtle matte canvas texture & dark vignette (`#0B0C10`).
          * High-detail NovOS Orange (`#FF6B1A` / `#FFAD00`) paint splatter at the right-third focal point (`0.70 * W, 0.50 * H`).
          * Dynamic left-to-right motion paint-splatter trail (`0.08 * W .. 0.70 * W`) with smaller droplets on the left
            increasing progressively in droplet diameter, streak length, and spatial density to the right.
          * Stencil-style cutout of the NovOS Supernova logo in the center of the paint splatter.
      - `is_dark=True` (`nova-minimal-silhouette-dark-*`):
          * Converts both background (`#121214` -> `#0B0C10`) and paint splatter + motion trail (`#2B2B30` / `#3D3D44`)
            to monochrome charcoal/slate grayscale.
          * Outlines the stenciled NovOS logo in the center of the splatter with crisp NovOS Orange (`#FFAD00` / `#FF6B1A`).
    """
    if not HAS_PIL_NUMPY:
        _fallback_minimal_wallpaper(path, width, height, is_dark, style="silhouette")
        return

    hw, hh = width // 2, height // 2
    yn = np.linspace(-0.5, 0.5, hh, dtype=np.float32)[:, None]
    aspect = width / float(height)
    xn = (np.linspace(-0.5, 0.5, hw, dtype=np.float32) * aspect)[None, :]

    # Peripheral vignette & subtle left-top ambient canvas lighting (matching reference lighting gradient)
    rad_vig = np.hypot((xn - 0.04 * aspect) / (0.58 * aspect), yn / 0.56)
    vignette = np.clip((rad_vig - 0.18) / 0.88, 0.0, 1.0) ** 1.60
    left_lift = np.exp(-(((xn + 0.18 * aspect) / (0.62 * aspect)) ** 2)) * np.exp(-((yn / 0.72) ** 2))

    # Deterministic subtle matte canvas texture
    rng_bg = np.random.default_rng(0x4E4F5642)
    grain_half = rng_bg.normal(0.0, 0.75, size=(hh, hw)).astype(np.float32)

    if is_dark:
        # Dark Grayscale Background (#121214 = rgb(18,18,20) -> #0B0C10 = rgb(11,12,16) peripheral vignette)
        bg_r = 16.0 + 4.0 * left_lift - 5.0 * vignette + grain_half
        bg_g = 16.0 + 4.0 * left_lift - 4.0 * vignette + grain_half
        bg_b = 18.0 + 4.0 * left_lift - 2.0 * vignette + grain_half
    else:
        # NovOS Deep Indigo Background (#121521 = rgb(18,21,33) / #0F121C = rgb(15,18,28) -> #0B0C10 = rgb(11,12,16) vignette)
        bg_r = 16.0 + 6.0 * left_lift - 5.0 * vignette + grain_half * 0.75
        bg_g = 19.0 + 8.0 * left_lift - 7.0 * vignette + grain_half * 0.75
        bg_b = 31.0 + 12.0 * left_lift - 15.0 * vignette + grain_half * 0.90

    canvas = _upsample_float_rgb_to_canvas(bg_r, bg_g, bg_b, width, height)

    # Synthesize the main paint splatter at (0.70 * W, 0.50 * H) + left-to-right motion droplet trail
    ecx = int(round(width * 0.70))
    ecy = int(round(height * 0.50))
    paint_mask, paint_tone = _synthesize_splatter_and_motion_trail_np(width, height, ecx, ecy)

    # Compute the Stenciled NovOS Logo in the center of the main paint splatter
    stencil_radius = int(round(height * 0.21))
    sdf_px, u_st, v_st = _compute_novos_stencil_sdf_np(stencil_radius)
    ph, pw = sdf_px.shape
    pad = ph // 2

    sy0 = max(0, ecy - pad)
    sy1 = min(height, ecy + pad + 1)
    sx0 = max(0, ecx - pad)
    sx1 = min(width, ecx + pad + 1)

    py0 = sy0 - (ecy - pad)
    py1 = py0 + (sy1 - sy0)
    px0 = sx0 - (ecx - pad)
    px1 = px0 + (sx1 - sx0)

    sdf_sub = sdf_px[py0:py1, px0:px1]
    u_sub = u_st[py0:py1, px0:px1]
    v_sub = v_st[py0:py1, px0:px1]

    # Carve the stencil cutout from the paint splatter mask
    stencil_cutout = np.clip(0.5 - sdf_sub / 1.25, 0.0, 1.0)

    if not is_dark:
        # Subtle distressed paint-stencil texture inside the broad 4-point star core (like the reference image)
        rng_st = np.random.default_rng(0x53544E43)
        speck_low = rng_st.uniform(0.0, 1.0, size=(max(1, (sy1 - sy0) // 3), max(1, (sx1 - sx0) // 3))).astype(
            np.float32
        )
        speck = np.asarray(
            Image.fromarray(speck_low, mode="F").resize((sx1 - sx0, sy1 - sy0), resample=Image.Resampling.BILINEAR),
            dtype=np.float32,
        )
        axis_hairline = np.exp(-((np.minimum(np.abs(u_sub), np.abs(v_sub)) * float(stencil_radius) / 1.3) ** 2))
        distress_bleed = np.clip((speck - 0.72) * 0.85, 0.0, 0.18) + axis_hairline * 0.16 * (speck > 0.50)
        effective_cutout = stencil_cutout * (1.0 - np.clip(distress_bleed, 0.0, 0.26))
    else:
        effective_cutout = stencil_cutout

    paint_mask[sy0:sy1, sx0:sx1] *= 1.0 - effective_cutout

    # Paint color shading (NovOS Orange #FF6B1A / #FFAD00 in color mode; Charcoal/Slate Grayscale #2B2B30 / #3D3D44 in dark mode)
    if is_dark:
        # Smooth Charcoal/Slate Grayscale paint splatter (#2B2B30 = rgb(43,43,48) -> #3D3D44 = rgb(61,61,68))
        p_r = 36.0 + 27.0 * paint_tone
        p_g = 36.0 + 27.0 * paint_tone
        p_b = 41.0 + 29.0 * paint_tone
    else:
        # Smooth vibrant NovOS Orange paint splatter (#FF6B1A Nova Orange = rgb(255,107,26) -> #FFAD00 Cosmic Amber = rgb(255,173,0))
        p_r = np.full_like(paint_tone, 255.0)
        p_g = np.clip(92.0 + 68.0 * paint_tone, 0.0, 255.0)
        p_b = np.clip(28.0 * (1.0 - 0.75 * paint_tone), 0.0, 255.0)

    pm3 = paint_mask[:, :, None]
    paint_rgb = np.stack([p_r, p_g, p_b], axis=-1)
    canvas[:] = canvas * (1.0 - pm3) + paint_rgb * pm3

    # Dark Mode: Outline the stenciled NovOS logo in crisp, luminous NovOS Orange (#FFAD00 / #FF6B1A)
    if is_dark:
        outline_w = max(1.65, float(height) * 0.00105)
        edge_dist = np.abs(sdf_sub - 0.15)
        core_line = np.clip(1.0 - edge_dist / outline_w, 0.0, 1.0)
        outer_glow = np.exp(-((edge_dist / (outline_w * 1.65)) ** 2)) * 0.28
        outline_alpha = np.clip(core_line + outer_glow, 0.0, 1.0)[:, :, None]

        t_col = core_line[:, :, None]
        or_rgb = np.concatenate(
            [
                np.full_like(t_col, 255.0),
                107.0 + (173.0 - 107.0) * t_col,
                26.0 * (1.0 - t_col),
            ],
            axis=-1,
        )
        roi_canvas = canvas[sy0:sy1, sx0:sx1]
        roi_canvas[:] = roi_canvas * (1.0 - outline_alpha) + or_rgb * outline_alpha

    _save_dithered_canvas_np(path, canvas)


def _overlay_emblem_on_scanlines(
    buf: bytearray, width: int, height: int, cx: int, cy: int, radius: int, alpha_scale: float = 1.0
) -> None:
    """Composite the pure-Python Supernova emblem onto RGBA scanlines using a 128x128 LUT."""
    lut_dim = 128
    lut_c = 63.5
    lut_r = 60.0
    lut: list[list[tuple[float, float, float, float]]] = []
    for ly in range(lut_dim):
        row = []
        for lx in range(lut_dim):
            er, eg, eb, ea = _sample_supernova_emblem(lx - lut_c, ly - lut_c, lut_r)
            row.append((er, eg, eb, (ea / 255.0) * alpha_scale))
        lut.append(row)

    row_stride = 1 + width * 4
    y0 = max(0, cy - radius)
    y1 = min(height - 1, cy + radius)
    x0 = max(0, cx - radius)
    x1 = min(width - 1, cx + radius)
    inv_span = (lut_dim - 1) / float(max(1, 2 * radius))

    for y in range(y0, y1 + 1):
        ly = min(lut_dim - 1, max(0, int((y - (cy - radius)) * inv_span + 0.5)))
        lut_row = lut[ly]
        row_off = y * row_stride
        for x in range(x0, x1 + 1):
            lx = min(lut_dim - 1, max(0, int((x - (cx - radius)) * inv_span + 0.5)))
            er, eg, eb, a = lut_row[lx]
            if a <= 0.005:
                continue
            px = row_off + 1 + x * 4
            inv_a = 1.0 - a
            buf[px] = _clamp_byte(buf[px] * inv_a + er * a)
            buf[px + 1] = _clamp_byte(buf[px + 1] * inv_a + eg * a)
            buf[px + 2] = _clamp_byte(buf[px + 2] * inv_a + eb * a)


def _fallback_scanline_wallpaper(path: Path, width: int, height: int) -> None:
    """Pure-Python scanline fallback with Supernova emblem if Pillow/NumPy are absent."""
    row_stride = 1 + width * 4
    buf = bytearray(height * row_stride)
    for y in range(height):
        row_off = y * row_stride
        buf[row_off] = 0
        yn = y / max(1, height - 1)
        horizon = max(0.0, (yn - 0.58) / 0.42) ** 2.0
        r = _clamp_byte(11.0 + 16.0 * yn + 72.0 * horizon)
        g = _clamp_byte(12.0 + 15.0 * yn + 38.0 * horizon)
        b = _clamp_byte(16.0 + 11.0 * yn + 6.0 * horizon)
        pixel = bytes((r, g, b, 255))
        buf[row_off + 1 : row_off + 1 + width * 4] = pixel * width
    _overlay_emblem_on_scanlines(
        buf, width, height, int(width * 0.66), int(height * 0.44), int(height * 0.16), alpha_scale=0.92
    )
    write_rgba_png(path, width, height, buf)


def _fallback_minimal_wallpaper(path: Path, width: int, height: int, is_dark: bool, style: str) -> None:
    """Pure-Python scanline fallback for minimalist wallpapers if Pillow/NumPy are absent."""
    row_stride = 1 + width * 4
    buf = bytearray(height * row_stride)
    is_faceted = style == "faceted"
    for y in range(height):
        row_off = y * row_stride
        buf[row_off] = 0
        yn = y / max(1, height - 1)
        edge_orange = (abs(yn - 0.5) * 2.0) ** 2.0 * 0.18 if is_faceted else 0.0
        if is_dark:
            r = _clamp_byte(24.0 * (1.0 - edge_orange) + 255.0 * edge_orange)
            g = _clamp_byte(24.0 * (1.0 - edge_orange) + 140.0 * edge_orange)
            b = _clamp_byte(26.0 * (1.0 - edge_orange) + 15.0 * edge_orange)
        else:
            r = _clamp_byte(18.0 * (1.0 - edge_orange) + 255.0 * edge_orange)
            g = _clamp_byte(21.0 * (1.0 - edge_orange) + 140.0 * edge_orange)
            b = _clamp_byte(33.0 * (1.0 - edge_orange) + 15.0 * edge_orange)
        pixel = bytes((r, g, b, 255))
        buf[row_off + 1 : row_off + 1 + width * 4] = pixel * width

    if is_faceted:
        ecx, ecy, erad, escale = width // 2, height // 2, int(height * 0.22), 0.35
    else:
        ecx, ecy, erad, escale = int(width * 0.70), int(height * 0.50), int(height * 0.21), 0.55
    _overlay_emblem_on_scanlines(buf, width, height, ecx, ecy, erad, alpha_scale=escale)
    write_rgba_png(path, width, height, buf)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    branding_dir = repo_root / "branding"
    branding_dir.mkdir(parents=True, exist_ok=True)

    # 1. Core Vector & Emblem Assets
    generate_logo_svg(branding_dir / "novos-logo.svg")
    generate_logo_png(branding_dir / "novos-logo.png")
    generate_wallpaper_svg(branding_dir / "nova-wallpaper.svg")

    # 2. Photorealistic Cosmic Supernova Master Derivatives (16:9 4K, 32:9 Ultrawide, 1080p Default Alias)
    generate_realistic_wallpapers(
        base_path=branding_dir / "nova-realistic-base.png",
        out_16x9=branding_dir / "nova-realistic-16x9.png",
        out_32x9=branding_dir / "nova-realistic-32x9.png",
        out_default_fhd=branding_dir / "nova-wallpaper.png",
    )

    # 3. Style 1: Centered Accretion-Swirl & Monochrome Flat-Logo Wallpapers (Dark Grayscale & Indigo, 16:9 & 32:9)
    generate_minimal_faceted_wallpaper(
        branding_dir / "nova-minimal-faceted-dark-16x9.png", 3840, 2160, is_dark=True
    )
    generate_minimal_faceted_wallpaper(
        branding_dir / "nova-minimal-faceted-dark-32x9.png", 5120, 1440, is_dark=True
    )
    generate_minimal_faceted_wallpaper(
        branding_dir / "nova-minimal-faceted-light-16x9.png", 3840, 2160, is_dark=False
    )
    generate_minimal_faceted_wallpaper(
        branding_dir / "nova-minimal-faceted-light-32x9.png", 5120, 1440, is_dark=False
    )

    # 4. Style 2: Offset Motion Paint-Splatter & Stenciled Logo Wallpapers (Dark Grayscale+Orange Outline & Indigo+Orange Splatter, 16:9 & 32:9)
    generate_minimal_silhouette_wallpaper(
        branding_dir / "nova-minimal-silhouette-dark-16x9.png", 3840, 2160, is_dark=True
    )
    generate_minimal_silhouette_wallpaper(
        branding_dir / "nova-minimal-silhouette-dark-32x9.png", 5120, 1440, is_dark=True
    )
    generate_minimal_silhouette_wallpaper(
        branding_dir / "nova-minimal-silhouette-light-16x9.png", 3840, 2160, is_dark=False
    )
    generate_minimal_silhouette_wallpaper(
        branding_dir / "nova-minimal-silhouette-light-32x9.png", 5120, 1440, is_dark=False
    )


if __name__ == "__main__":
    main()

