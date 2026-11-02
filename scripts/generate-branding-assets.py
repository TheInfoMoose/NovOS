#!/usr/bin/env python3
"""
Deterministic Nova Branding Asset Generator (Logo & Wallpaper) for NovOS.

Generates:
  - branding/novos-logo.svg
  - branding/novos-logo.png (256x256 RGBA PNG)
  - branding/nova-wallpaper.svg
  - branding/nova-wallpaper.png (1920x1080 RGBA PNG)

Uses pure Python standard library (struct, zlib, math) so it succeeds
deterministically with zero external Python packages required.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from pathlib import Path


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
    compressed = zlib.compress(bytes(raw_scanlines), level=6)

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
      <stop offset="0%" stop-color="#121521"/>
      <stop offset="75%" stop-color="#0B0C10"/>
      <stop offset="100%" stop-color="#07080B"/>
    </radialGradient>
    <linearGradient id="accretionRingOuter" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FF6B1A"/>
      <stop offset="50%" stop-color="#FF9E44"/>
      <stop offset="100%" stop-color="#FF6B1A"/>
    </linearGradient>
    <linearGradient id="accretionRingInner" x1="100%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#FF9E44"/>
      <stop offset="50%" stop-color="#FFB347"/>
      <stop offset="100%" stop-color="#FF6B1A"/>
    </linearGradient>
    <radialGradient id="coreGlow" cx="50%" cy="50%" r="45%">
      <stop offset="0%" stop-color="#FFF3E0" stop-opacity="1.0"/>
      <stop offset="35%" stop-color="#FF9E44" stop-opacity="0.85"/>
      <stop offset="70%" stop-color="#FF6B1A" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#FF6B1A" stop-opacity="0.0"/>
    </radialGradient>
  </defs>

  <!-- Deep Obsidian Circular Badge (#0B0C10) -->
  <circle cx="128" cy="128" r="120" fill="url(#obsidianBadge)" stroke="#FF6B1A" stroke-width="3" stroke-opacity="0.55"/>
  <circle cx="128" cy="128" r="112" fill="none" stroke="#1E2233" stroke-width="1.5"/>

  <!-- Glowing Nova Orange Accretion Rings (#FF6B1A -> #FF9E44) -->
  <ellipse cx="128" cy="128" rx="92" ry="38" transform="rotate(-28 128 128)"
           fill="none" stroke="url(#accretionRingOuter)" stroke-width="7" stroke-opacity="0.9"/>
  <ellipse cx="128" cy="128" rx="76" ry="30" transform="rotate(-28 128 128)"
           fill="none" stroke="url(#accretionRingInner)" stroke-width="3.5" stroke-opacity="0.85"/>
  <circle cx="128" cy="128" r="58" fill="none" stroke="url(#accretionRingOuter)" stroke-width="2.5" stroke-opacity="0.65" stroke-dasharray="18 8"/>

  <!-- Supernova Core Halo -->
  <circle cx="128" cy="128" r="52" fill="url(#coreGlow)"/>

  <!-- Secondary Diagonal Star Rays -->
  <polygon points="128,84 134,122 172,128 134,134 128,172 122,134 84,128 122,122"
           fill="#FF9E44" opacity="0.85" transform="rotate(45 128 128)"/>

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
    Evaluate the geometric Supernova emblem at normalized offset (dx, dy) around center
    for a given badge radius. Returns (r, g, b, a) in 0..255.
    """
    dist = math.hypot(dx, dy)
    if dist > radius + 1.5:
        return (0.0, 0.0, 0.0, 0.0)

    # Anti-aliased circular badge mask
    badge_alpha = max(0.0, min(1.0, (radius + 0.75 - dist) / 1.5))
    norm_r = dist / radius

    # Base deep obsidian circular badge (#0B0C10 edge -> #121521 inner)
    if norm_r >= 0.75:
        r, g, b = 11.0, 12.0, 16.0
    else:
        t_bg = max(0.0, 1.0 - norm_r / 0.75)
        r = 11.0 + 7.0 * t_bg
        g = 12.0 + 9.0 * t_bg
        b = 16.0 + 17.0 * t_bg

    # Outer rim glow (#FF6B1A)
    rim_dist = abs(norm_r - 0.96)
    if rim_dist < 0.04:
        rim_w = (1.0 - rim_dist / 0.04) * 0.65
        r = r * (1.0 - rim_w) + 255.0 * rim_w
        g = g * (1.0 - rim_w) + 107.0 * rim_w
        b = b * (1.0 - rim_w) + 26.0 * rim_w

    # Tilted accretion rings (-28 degrees)
    cos_a = 0.882947592858927
    sin_a = 0.4694715627858908
    ux = (dx * cos_a - dy * sin_a) / radius
    uy = (dx * sin_a + dy * cos_a) / radius

    ell1 = math.hypot(ux / 0.72, uy / 0.30)
    ell2 = math.hypot(ux / 0.58, uy / 0.23)

    # Radial core glow (#FF6B1A -> #FF9E44) beneath rings and star flare
    if norm_r < 0.45:
        glow = ((1.0 - norm_r / 0.45) ** 2.0) * 0.75
        r = r * (1.0 - glow) + 255.0 * glow
        g = g * (1.0 - glow) + 140.0 * glow
        b = b * (1.0 - glow) + 50.0 * glow

    ring1_d = abs(ell1 - 1.0)
    if ring1_d < 0.08:
        w = 1.0 if ring1_d <= 0.012 else ((1.0 - (ring1_d - 0.012) / 0.068) ** 1.5)
        # Gradient #FF6B1A (255, 107, 26) -> #FF9E44 (255, 158, 68)
        if ux <= -0.55:
            grad_t = 0.0
        elif ux >= 0.55:
            grad_t = 1.0
        else:
            grad_t = (ux + 0.55) / 1.10
        rr = 255.0
        rg = 107.0 + (158.0 - 107.0) * grad_t
        rb = 26.0 + (68.0 - 26.0) * grad_t
        r = r * (1.0 - w) + rr * w
        g = g * (1.0 - w) + rg * w
        b = b * (1.0 - w) + rb * w

    ring2_d = abs(ell2 - 1.0)
    if ring2_d < 0.06:
        w = 1.0 if ring2_d <= 0.010 else ((1.0 - (ring2_d - 0.010) / 0.050) ** 1.5)
        # Inner ring gradient #FF9E44 (255, 158, 68) -> #FFB347 (255, 179, 71)
        t2 = 1.0 if ux >= 0.25 else (0.0 if ux <= -0.25 else (ux + 0.25) / 0.50)
        rr = 255.0
        rg = 158.0 + (179.0 - 158.0) * t2
        rb = 68.0 + (71.0 - 68.0) * t2
        r = r * (1.0 - w) + rr * w
        g = g * (1.0 - w) + rg * w
        b = b * (1.0 - w) + rb * w

    # Circular inner accretion ring
    c_ring_d = abs(norm_r - 0.45)
    if c_ring_d < 0.03:
        w = (1.0 - c_ring_d / 0.03) * 0.55
        r = r * (1.0 - w) + 255.0 * w
        g = g * (1.0 - w) + 125.0 * w
        b = b * (1.0 - w) + 40.0 * w

    # Secondary 45-degree diagonal star rays (#FF9E44 = 255, 158, 68)
    inv_sqrt2 = 0.7071067811865475
    rx = abs(dx + dy) * inv_sqrt2 / radius
    ry = abs(dx - dy) * inv_sqrt2 / radius
    diag_metric = min(rx + ry * 7.5, ry + rx * 7.5)
    if diag_metric < 0.38 and norm_r < 0.38:
        d_flare = (max(0.0, 1.0 - diag_metric / 0.38) ** 2.0) * 0.85
        r = r * (1.0 - d_flare) + 255.0 * d_flare
        g = g * (1.0 - d_flare) + 158.0 * d_flare
        b = b * (1.0 - d_flare) + 68.0 * d_flare

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
        buf[row_off] = 0  # Filter type 0 (None)
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
    <!-- Deep Cosmic Space Background (#0B0C10 -> #121521) -->
    <linearGradient id="cosmicSpace" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0B0C10"/>
      <stop offset="65%" stop-color="#0F121C"/>
      <stop offset="100%" stop-color="#121521"/>
    </linearGradient>

    <!-- Warm Nova Orange (#FF6B1A) and Amber (#FFB347) Horizon Nebula Arc -->
    <radialGradient id="nebulaGlow" cx="62%" cy="88%" r="65%">
      <stop offset="0%" stop-color="#FFB347" stop-opacity="0.55"/>
      <stop offset="30%" stop-color="#FF6B1A" stop-opacity="0.38"/>
      <stop offset="65%" stop-color="#FF6B1A" stop-opacity="0.10"/>
      <stop offset="100%" stop-color="#0B0C10" stop-opacity="0.0"/>
    </radialGradient>

    <linearGradient id="horizonArc" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#FF6B1A" stop-opacity="0.0"/>
      <stop offset="30%" stop-color="#FF6B1A" stop-opacity="0.75"/>
      <stop offset="65%" stop-color="#FFB347" stop-opacity="0.90"/>
      <stop offset="100%" stop-color="#FF6B1A" stop-opacity="0.15"/>
    </linearGradient>

    <radialGradient id="emblemHalo" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#FFF3E0" stop-opacity="0.95"/>
      <stop offset="28%" stop-color="#FFB347" stop-opacity="0.65"/>
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
  <path d="M -100,1065 Q 980,715 2020,995" fill="none" stroke="#FF6B1A" stroke-width="2" opacity="0.45"/>

  <!-- Center-Right Supernova Emblem (cx=1280, cy=460) -->
  <g transform="translate(1280, 460)">
    <circle cx="0" cy="0" r="220" fill="url(#emblemHalo)"/>
    <circle cx="0" cy="0" r="145" fill="#0B0C10" fill-opacity="0.88" stroke="#FF6B1A" stroke-width="3" stroke-opacity="0.65"/>
    <ellipse cx="0" cy="0" rx="115" ry="46" transform="rotate(-28)" fill="none" stroke="#FF6B1A" stroke-width="8" stroke-opacity="0.90"/>
    <ellipse cx="0" cy="0" rx="94" ry="36" transform="rotate(-28)" fill="none" stroke="#FF9E44" stroke-width="4" stroke-opacity="0.85"/>
    <circle cx="0" cy="0" r="68" fill="url(#emblemHalo)"/>
    <polygon points="0,-58 8,-8 58,0 8,8 0,58 -8,8 -58,0 -8,-8" fill="#FFB347" opacity="0.85" transform="rotate(45)"/>
    <polygon points="0,-125 14,-14 125,0 14,14 0,125 -14,14 -125,0 -14,-14" fill="#FFF3E0"/>
    <circle cx="0" cy="0" r="16" fill="#FFF3E0"/>
  </g>
</svg>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def generate_wallpaper_png(path: Path) -> None:
    """
    Generate a 1920x1080 RGBA PNG wallpaper efficiently using scanline synthesis:
      1. Deep cosmic space vertical/horizontal gradient (#0B0C10 -> #121521)
      2. Warm Nova Orange (#FF6B1A) and amber (#FFB347) horizon nebula arc
      3. Deterministic subtle starfield
      4. Center-right Supernova emblem at (1280, 460)
    """
    width, height = 1920, 1080
    row_stride = 1 + width * 4
    buf = bytearray(height * row_stride)

    # Precompute X-dependent terms for the horizon arc parabola & horizontal gradient
    x_norm = [x / (width - 1) for x in range(width)]
    arc_y = [830.0 + 210.0 * (((x - 1120.0) / 1120.0) ** 2) for x in range(width)]
    arc_horiz_env = [min(1.0, 0.35 + 0.68 * (math.sin(math.pi * xn) ** 0.85)) for xn in x_norm]
    neb_dx2 = [((x - 1200.0) / 920.0) ** 2 for x in range(width)]

    for y in range(height):
        row_off = y * row_stride
        buf[row_off] = 0  # Filter type 0 (None)
        yn = y / (height - 1)

        # Base cosmic background interpolation (#0B0C10 -> #121521)
        base_r = 11.0 + 7.0 * yn
        base_g = 12.0 + 9.0 * yn
        base_b = 16.0 + 17.0 * yn

        neb_dy2 = ((y - 920.0) / 420.0) ** 2

        for x0 in range(0, width, 4):
            ad0 = abs(y - arc_y[x0])
            if ad0 >= 54.0:
                # Smooth background/nebula region away from sharp horizon arc: 4-pixel block fill
                d2 = neb_dx2[x0] + neb_dy2
                if d2 < 1.0:
                    neb_w = ((1.0 - d2) ** 2.0) * 0.42
                    r = base_r * (1.0 - neb_w) + 255.0 * neb_w
                    g = base_g * (1.0 - neb_w) + 107.0 * neb_w
                    b = base_b * (1.0 - neb_w) + 26.0 * neb_w
                else:
                    r, g, b = base_r, base_g, base_b
                pixel = bytes((_clamp_byte(r), _clamp_byte(g), _clamp_byte(b), 255))
                start = row_off + 1 + x0 * 4
                buf[start : start + 16] = pixel * 4
            else:
                # Per-pixel evaluation across the curved horizon arc to prevent 4-pixel stair-stepping
                for x in range(x0, x0 + 4):
                    d2 = neb_dx2[x] + neb_dy2
                    if d2 < 1.0:
                        neb_w = ((1.0 - d2) ** 2.0) * 0.42
                        r = base_r * (1.0 - neb_w) + 255.0 * neb_w
                        g = base_g * (1.0 - neb_w) + 107.0 * neb_w
                        b = base_b * (1.0 - neb_w) + 26.0 * neb_w
                    else:
                        r, g, b = base_r, base_g, base_b

                    ad = abs(y - arc_y[x])
                    if ad < 48.0:
                        vert_w = 1.0 if ad <= 1.0 else ((1.0 - (ad - 1.0) / 47.0) ** 2.2)
                        arc_w = vert_w * arc_horiz_env[x]
                        # Smoothly blend along arc from Nova Orange (#FF6B1A) to Amber (#FFB347)
                        t_amber = 0.0 if x_norm[x] <= 0.42 else (1.0 if x_norm[x] >= 0.68 else (x_norm[x] - 0.42) / 0.26)
                        ar = 255.0
                        ag = 107.0 + (179.0 - 107.0) * t_amber
                        ab = 26.0 + (71.0 - 26.0) * t_amber
                        r = r * (1.0 - arc_w) + ar * arc_w
                        g = g * (1.0 - arc_w) + ag * arc_w
                        b = b * (1.0 - arc_w) + ab * arc_w

                    px = row_off + 1 + x * 4
                    buf[px] = _clamp_byte(r)
                    buf[px + 1] = _clamp_byte(g)
                    buf[px + 2] = _clamp_byte(b)
                    buf[px + 3] = 255

    # Overlay subtle starfield
    stars = _deterministic_stars(width, height, count=140)
    for sx, sy, srad, sbright in stars:
        sr, sg, sb = (255, 243, 224) if srad > 1 else (216, 226, 255)
        for oy in range(-srad, srad + 1):
            py = sy + oy
            if py < 0 or py >= height:
                continue
            for ox in range(-srad, srad + 1):
                px = sx + ox
                if px < 0 or px >= width:
                    continue
                dist = math.hypot(ox, oy)
                if dist <= srad + 0.5:
                    w = sbright * max(0.0, 1.0 - dist / (srad + 0.8))
                    idx = py * row_stride + 1 + px * 4
                    buf[idx] = _clamp_byte(buf[idx] * (1.0 - w) + sr * w)
                    buf[idx + 1] = _clamp_byte(buf[idx + 1] * (1.0 - w) + sg * w)
                    buf[idx + 2] = _clamp_byte(buf[idx + 2] * (1.0 - w) + sb * w)

    # Overlay center-right Supernova emblem at (cx=1280, cy=460, radius=145)
    ecx, ecy = 1280, 460
    eradius = 145.0
    halo_r = 210
    for y in range(max(0, ecy - halo_r), min(height, ecy + halo_r + 1)):
        row_off = y * row_stride
        dy = float(y - ecy)
        for x in range(max(0, ecx - halo_r), min(width, ecx + halo_r + 1)):
            dx = float(x - ecx)
            dist = math.hypot(dx, dy)
            idx = row_off + 1 + x * 4

            # Soft outer halo around emblem
            if dist < halo_r and dist > eradius - 2.0:
                hw = ((1.0 - dist / halo_r) ** 2.2) * 0.55
                buf[idx] = _clamp_byte(buf[idx] * (1.0 - hw) + 255.0 * hw)
                buf[idx + 1] = _clamp_byte(buf[idx + 1] * (1.0 - hw) + 120.0 * hw)
                buf[idx + 2] = _clamp_byte(buf[idx + 2] * (1.0 - hw) + 35.0 * hw)

            if dist <= eradius + 1.5:
                er, eg, eb, ea = _sample_supernova_emblem(dx, dy, eradius)
                alpha = ea / 255.0
                if alpha > 0.0:
                    buf[idx] = _clamp_byte(buf[idx] * (1.0 - alpha) + er * alpha)
                    buf[idx + 1] = _clamp_byte(buf[idx + 1] * (1.0 - alpha) + eg * alpha)
                    buf[idx + 2] = _clamp_byte(buf[idx + 2] * (1.0 - alpha) + eb * alpha)

    write_rgba_png(path, width, height, buf)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    branding_dir = repo_root / "branding"
    branding_dir.mkdir(parents=True, exist_ok=True)

    logo_svg = branding_dir / "novos-logo.svg"
    logo_png = branding_dir / "novos-logo.png"
    wall_svg = branding_dir / "nova-wallpaper.svg"
    wall_png = branding_dir / "nova-wallpaper.png"

    generate_logo_svg(logo_svg)
    generate_logo_png(logo_png)
    generate_wallpaper_svg(wall_svg)
    generate_wallpaper_png(wall_png)


if __name__ == "__main__":
    main()
