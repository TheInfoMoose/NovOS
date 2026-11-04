//! NovOS Slippery Tile Engine & Interactive gTile Grid Overlay Manager
//! ===================================================================
//! Adaptive window grid manager and deterministic 2D collision-slip solver for
//! COSMIC Desktop on ultrawide (32:9 -> 12x4 grid) and standard (16:9 -> 8x4 grid)
//! displays.
//!
//! Features:
//! - Auto-detects active display resolution and aspect ratio:
//!   * 32:9 ultrawide (e.g., 5120x1440): 12x4 grid (1 col = 426px, default block =
//!     3x4 = 1280x1440 or 3x2 = 1280x720).
//!   * 16:9 standard (e.g., 3840x2160 / 1920x1080): 8x4 grid (default block =
//!     4x4 or 2x4).
//! - Prevents single-window auto-fullscreening by spawning/managing windows as
//!   scaled building blocks anchored to Left, Right, or Center (`--anchor
//!   left|right|center`, default `left`).
//! - Deterministic 2D Slippery Reflow Solver (`resolve_slippery_layout`):
//!   * `move <left|right|up|down>`   (Super+Shift+Arrows)
//!   * `grow <left|right|up|down>`   (Super+Ctrl+Arrows)
//!   * `shrink <left|right|up|down>` (Super+Ctrl+Alt+Arrows)
//!   * `pack <left|right>`           (Super+BracketLeft / Super+BracketRight)
//!   * `preset <cols>x<rows> <x>,<y>,<w>,<h>`
//!   * `overlay`                     (Super+G)

use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

pub const THEME_BG: &str = "#1B1B1B";
pub const THEME_SURFACE: &str = "#262626";
pub const THEME_CELL_INACTIVE: &str = "#2E2E2E";
pub const THEME_CELL_BORDER: &str = "#444444";
pub const THEME_ACCENT: &str = "#FFAD00";
pub const THEME_TEXT: &str = "#F2F2F2";
pub const THEME_SUBTEXT: &str = "#B0B0B0";

pub const DEFAULT_MIN_SPAN: i32 = 2;
pub const VALID_DIRECTIONS: [&str; 4] = ["left", "right", "up", "down"];
pub const VALID_PACK_DIRECTIONS: [&str; 2] = ["left", "right"];
pub const VALID_ANCHORS: [&str; 3] = ["left", "right", "center"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GridProfile {
    pub width_px: i32,
    pub height_px: i32,
    pub cols: i32,
    pub rows: i32,
    pub aspect_label: String,
    pub default_block_w: i32,
    pub default_block_h: i32,
    pub alt_block_w: i32,
    pub alt_block_h: i32,
}

impl GridProfile {
    pub fn col_width_px(&self) -> i32 {
        self.width_px / self.cols
    }

    pub fn row_height_px(&self) -> i32 {
        self.height_px / self.rows
    }

    /// Convert grid cell coordinates (x, y, w, h) to exact pixel geometry.
    pub fn cell_to_pixel_rect(&self, x: i32, y: i32, w: i32, h: i32) -> (i32, i32, i32, i32) {
        let px_x = round_ratio(x * self.width_px, self.cols);
        let px_y = round_ratio(y * self.height_px, self.rows);
        let px_right = round_ratio((x + w) * self.width_px, self.cols);
        let px_bottom = round_ratio((y + h) * self.height_px, self.rows);
        (px_x, px_y, px_right - px_x, px_bottom - px_y)
    }
}

fn round_ratio(num: i32, den: i32) -> i32 {
    if den == 0 {
        return 0;
    }
    ((num as f64) / (den as f64)).round() as i32
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tile {
    pub id: String,
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
    pub anchor: String,
    pub orig_x: Option<i32>,
    pub orig_y: Option<i32>,
    pub orig_w: Option<i32>,
    pub orig_h: Option<i32>,
}

impl Tile {
    pub fn new(id: &str, x: i32, y: i32, w: i32, h: i32) -> Self {
        Self::with_anchor(id, x, y, w, h, "left")
    }

    pub fn with_anchor(id: &str, x: i32, y: i32, w: i32, h: i32, anchor: &str) -> Self {
        Self {
            id: id.to_string(),
            x,
            y,
            w,
            h,
            anchor: anchor.to_string(),
            orig_x: Some(x),
            orig_y: Some(y),
            orig_w: Some(w),
            orig_h: Some(h),
        }
    }

    pub fn right(&self) -> i32 {
        self.x + self.w
    }

    pub fn bottom(&self) -> i32 {
        self.y + self.h
    }

    pub fn overlaps(&self, other: &Tile) -> bool {
        if self.id == other.id {
            return false;
        }
        self.x < other.right()
            && self.right() > other.x
            && self.y < other.bottom()
            && self.bottom() > other.y
    }

    pub fn clamp(&mut self, cols: i32, rows: i32, min_w: i32, min_h: i32) {
        self.w = self.w.clamp(min_w, cols);
        self.h = self.h.clamp(min_h, rows);
        self.x = self.x.clamp(0, (cols - self.w).max(0));
        self.y = self.y.clamp(0, (rows - self.h).max(0));
    }

    pub fn snapshot_as_original(&mut self) {
        self.orig_x = Some(self.x);
        self.orig_y = Some(self.y);
        self.orig_w = Some(self.w);
        self.orig_h = Some(self.h);
    }

    pub fn to_json(&self) -> String {
        let ox = self.orig_x.map_or("null".to_string(), |v| v.to_string());
        let oy = self.orig_y.map_or("null".to_string(), |v| v.to_string());
        let ow = self.orig_w.map_or("null".to_string(), |v| v.to_string());
        let oh = self.orig_h.map_or("null".to_string(), |v| v.to_string());
        format!(
            "{{\"id\": \"{}\", \"x\": {}, \"y\": {}, \"w\": {}, \"h\": {}, \"anchor\": \"{}\", \"orig_x\": {}, \"orig_y\": {}, \"orig_w\": {}, \"orig_h\": {}}}",
            escape_json(&self.id),
            self.x,
            self.y,
            self.w,
            self.h,
            escape_json(&self.anchor),
            ox,
            oy,
            ow,
            oh
        )
    }
}

fn escape_json(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c => out.push(c),
        }
    }
    out
}

/// Build the adaptive grid profile for a given display resolution.
/// - 32:9 ultrawide (aspect >= 2.2, e.g. 5120x1440): 12x4 grid, default 3x4 (or 3x2).
/// - 16:9 standard (e.g. 3840x2160, 1920x1080): 8x4 grid, default 4x4 (or 2x4).
pub fn build_grid_profile(mut width_px: i32, mut height_px: i32) -> GridProfile {
    if width_px <= 0 || height_px <= 0 {
        width_px = 1920;
        height_px = 1080;
    }

    if (width_px as i64) * 10 >= (height_px as i64) * 22 {
        return GridProfile {
            width_px,
            height_px,
            cols: 12,
            rows: 4,
            aspect_label: "32:9".to_string(),
            default_block_w: 3,
            default_block_h: 4,
            alt_block_w: 3,
            alt_block_h: 2,
        };
    }

    GridProfile {
        width_px,
        height_px,
        cols: 8,
        rows: 4,
        aspect_label: "16:9".to_string(),
        default_block_w: 4,
        default_block_h: 4,
        alt_block_w: 2,
        alt_block_h: 4,
    }
}

pub fn parse_resolution_string(res: &str) -> Option<(i32, i32)> {
    let trimmed = res.trim();
    let (w_str, h_str) = trimmed.split_once('x')?;
    if !(3..=5).contains(&w_str.len()) || !(3..=5).contains(&h_str.len()) {
        return None;
    }
    if !w_str.chars().all(|c| c.is_ascii_digit()) || !h_str.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let w: i32 = w_str.parse().ok()?;
    let h: i32 = h_str.parse().ok()?;
    if w > 0 && h > 0 {
        Some((w, h))
    } else {
        None
    }
}

fn strip_ansi(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let mut chars = input.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            if chars.peek() == Some(&'[') {
                chars.next();
                while let Some(&nc) = chars.peek() {
                    chars.next();
                    if nc.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
        } else {
            out.push(c);
        }
    }
    out
}

fn extract_resolution_from_line(line: &str) -> Option<(i32, i32)> {
    let bytes = line.as_bytes();
    let n = bytes.len();
    let mut i = 0;
    while i < n {
        if bytes[i].is_ascii_digit() {
            let start_w = i;
            while i < n && bytes[i].is_ascii_digit() {
                i += 1;
            }
            let w_len = i - start_w;
            if (3..=5).contains(&w_len) && i < n && bytes[i] == b'x' {
                i += 1;
                let start_h = i;
                while i < n && bytes[i].is_ascii_digit() {
                    i += 1;
                }
                let h_len = i - start_h;
                if (3..=5).contains(&h_len) {
                    if let (Ok(w), Ok(h)) = (
                        line[start_w..start_w + w_len].parse::<i32>(),
                        line[start_h..start_h + h_len].parse::<i32>(),
                    ) {
                        if w > 0 && h > 0 {
                            return Some((w, h));
                        }
                    }
                }
            }
        } else {
            i += 1;
        }
    }
    None
}

fn command_exists(bin: &str) -> bool {
    if let Ok(path_var) = env::var("PATH") {
        for dir in env::split_paths(&path_var) {
            let full = dir.join(bin);
            if full.is_file() {
                return true;
            }
        }
    }
    false
}

/// Auto-detect active display resolution from CLI/env, cosmic-randr, wlr-randr, xrandr, or DRM.
pub fn detect_active_resolution(override_res: Option<&str>) -> (i32, i32) {
    if let Some(ov) = override_res {
        if let Some(parsed) = parse_resolution_string(ov) {
            return parsed;
        }
    }
    if let Ok(env_res) = env::var("NOVOS_DISPLAY_RESOLUTION") {
        if let Some(parsed) = parse_resolution_string(&env_res) {
            return parsed;
        }
    }

    let commands: [&[&str]; 3] = [
        &["cosmic-randr", "list"],
        &["wlr-randr"],
        &["xrandr", "--current"],
    ];

    for cmd in commands {
        if !command_exists(cmd[0]) {
            continue;
        }
        if let Ok(output) = Command::new(cmd[0])
            .args(&cmd[1..])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .output()
        {
            let text = String::from_utf8_lossy(&output.stdout);
            let clean = strip_ansi(&text);
            for line in clean.lines() {
                let lower = line.to_ascii_lowercase();
                if lower.contains("current") || line.contains('*') {
                    if let Some(res) = extract_resolution_from_line(line) {
                        return res;
                    }
                }
            }
        }
    }

    let drm_root = Path::new("/sys/class/drm");
    if drm_root.is_dir() {
        if let Ok(entries) = fs::read_dir(drm_root) {
            let mut dirs: Vec<PathBuf> = entries
                .filter_map(|e| e.ok().map(|d| d.path()))
                .filter(|p| {
                    p.file_name()
                        .and_then(|n| n.to_str())
                        .is_some_and(|s| s.starts_with("card") && s.contains('-'))
                })
                .collect();
            dirs.sort();
            for card_dir in dirs {
                let status_file = card_dir.join("status");
                let modes_file = card_dir.join("modes");
                if status_file.is_file() {
                    if let Ok(st) = fs::read_to_string(&status_file) {
                        let st = st.trim();
                        if !st.is_empty() && st != "connected" {
                            continue;
                        }
                    }
                }
                if modes_file.is_file() {
                    if let Ok(modes) = fs::read_to_string(&modes_file) {
                        if let Some(first_line) = modes.lines().next() {
                            if let Some(parsed) = parse_resolution_string(first_line.trim()) {
                                return parsed;
                            }
                        }
                    }
                }
            }
        }
    }

    (1920, 1080)
}

fn find_free_position(
    tiles: &[Tile],
    w: i32,
    h: i32,
    cols: i32,
    rows: i32,
    anchor: &str,
    ignore_id: Option<&str>,
) -> Option<(i32, i32)> {
    let w = w.clamp(1, cols);
    let h = h.clamp(1, rows);

    let x_candidates: Vec<i32> = if anchor == "right" {
        (0..=cols - w).rev().collect()
    } else if anchor == "center" {
        let center_x = ((cols - w) / 2).max(0);
        let mut xs: Vec<i32> = (0..=cols - w).collect();
        xs.sort_by_key(|&cx| ((cx - center_x).abs(), cx));
        xs
    } else {
        (0..=cols - w).collect()
    };

    let y_candidates: Vec<i32> = (0..=rows - h).collect();
    let probe_id = ignore_id.unwrap_or("__probe__");

    for cy in y_candidates {
        for &cx in &x_candidates {
            let probe = Tile::new(probe_id, cx, cy, w, h);
            let any_overlap = tiles
                .iter()
                .filter(|t| Some(t.id.as_str()) != ignore_id)
                .any(|t| probe.overlaps(t));
            if !any_overlap {
                return Some((cx, cy));
            }
        }
    }
    None
}

/// Spawn a new window building block without single-window auto-fullscreening.
/// Anchors to left, right, or center (`--anchor left|right|center`, default `left`).
pub fn spawn_building_block(
    tiles: &[Tile],
    tile_id: &str,
    profile: &GridProfile,
    anchor: &str,
    variant: &str,
) -> Result<(Vec<Tile>, Tile), String> {
    if !VALID_ANCHORS.contains(&anchor) {
        return Err(format!(
            "Invalid anchor {anchor:?}; expected one of {VALID_ANCHORS:?}"
        ));
    }

    if variant != "default" && variant != "alt" {
        return Err(format!(
            "Invalid variant {variant:?}; expected \"default\" or \"alt\""
        ));
    }

    let mut result: Vec<Tile> = tiles
        .iter()
        .filter(|t| t.id != tile_id)
        .cloned()
        .collect();

    let (bw, bh) = if variant == "alt" {
        (profile.alt_block_w, profile.alt_block_h)
    } else {
        (profile.default_block_w, profile.default_block_h)
    };

    let slot = find_free_position(&result, bw, bh, profile.cols, profile.rows, anchor, None);
    let (sx, sy) = match slot {
        Some(pos) => pos,
        None => {
            if anchor == "right" {
                ((profile.cols - bw).max(0), 0)
            } else if anchor == "center" {
                (((profile.cols - bw) / 2).max(0), 0)
            } else {
                (0, 0)
            }
        }
    };

    let new_tile = Tile::with_anchor(tile_id, sx, sy, bw, bh, anchor);
    result.push(new_tile);

    if slot.is_none() {
        let pref_dir = if anchor == "right" { "left" } else { "right" };
        reflow_neighbors_from_active(
            &mut result,
            tile_id,
            pref_dir,
            profile.cols,
            profile.rows,
            DEFAULT_MIN_SPAN,
        );
    }

    let spawned = result
        .iter()
        .find(|t| t.id == tile_id)
        .cloned()
        .ok_or_else(|| "Spawned tile missing".to_string())?;
    Ok((result, spawned))
}

fn reflow_neighbors_from_active(
    tiles: &mut [Tile],
    active_id: &str,
    preferred_direction: &str,
    cols: i32,
    rows: i32,
    min_span: i32,
) {
    fn resolve_against(
        tiles: &mut [Tile],
        source_id: &str,
        direction: &str,
        visited: &HashSet<String>,
        cols: i32,
        rows: i32,
        min_span: i32,
    ) -> bool {
        let mut next_visited = visited.clone();
        next_visited.insert(source_id.to_string());

        let source = match tiles.iter().find(|t| t.id == source_id).cloned() {
            Some(s) => s,
            None => return true,
        };

        let overlapping_ids: Vec<String> = tiles
            .iter()
            .filter(|t| !next_visited.contains(&t.id) && source.overlaps(t))
            .map(|t| t.id.clone())
            .collect();

        for nb_id in overlapping_ids {
            let idx = match tiles.iter().position(|t| t.id == nb_id) {
                Some(i) => i,
                None => continue,
            };
            if direction == "right" {
                let needed_x = source.right();
                let delta = needed_x - tiles[idx].x;
                if delta <= 0 {
                    continue;
                }
                if tiles[idx].right() + delta <= cols {
                    tiles[idx].x += delta;
                } else {
                    let available_w = cols - needed_x;
                    let effective_min = min_span.min(tiles[idx].w);
                    if available_w >= effective_min {
                        tiles[idx].x = needed_x;
                        tiles[idx].w = effective_min.max(tiles[idx].w.min(available_w));
                    } else {
                        return false;
                    }
                }
                if !resolve_against(
                    tiles,
                    &nb_id,
                    direction,
                    &next_visited,
                    cols,
                    rows,
                    min_span,
                ) {
                    return false;
                }
            } else if direction == "left" {
                let needed_right = source.x;
                let delta = tiles[idx].right() - needed_right;
                if delta <= 0 {
                    continue;
                }
                if tiles[idx].x - delta >= 0 {
                    tiles[idx].x -= delta;
                } else {
                    let available_w = needed_right;
                    let effective_min = min_span.min(tiles[idx].w);
                    if available_w >= effective_min {
                        tiles[idx].x = (needed_right - tiles[idx].w).max(0);
                        tiles[idx].w = tiles[idx].w.min(available_w);
                    } else {
                        return false;
                    }
                }
                if !resolve_against(
                    tiles,
                    &nb_id,
                    direction,
                    &next_visited,
                    cols,
                    rows,
                    min_span,
                ) {
                    return false;
                }
            } else if direction == "down" {
                let needed_y = source.bottom();
                let delta = needed_y - tiles[idx].y;
                if delta <= 0 {
                    continue;
                }
                if tiles[idx].bottom() + delta <= rows {
                    tiles[idx].y += delta;
                } else {
                    let available_h = rows - needed_y;
                    let effective_min = min_span.min(tiles[idx].h);
                    if available_h >= effective_min {
                        tiles[idx].y = needed_y;
                        tiles[idx].h = effective_min.max(tiles[idx].h.min(available_h));
                    } else {
                        return false;
                    }
                }
                if !resolve_against(
                    tiles,
                    &nb_id,
                    direction,
                    &next_visited,
                    cols,
                    rows,
                    min_span,
                ) {
                    return false;
                }
            } else if direction == "up" {
                let needed_bottom = source.y;
                let delta = tiles[idx].bottom() - needed_bottom;
                if delta <= 0 {
                    continue;
                }
                if tiles[idx].y - delta >= 0 {
                    tiles[idx].y -= delta;
                } else {
                    let available_h = needed_bottom;
                    let effective_min = min_span.min(tiles[idx].h);
                    if available_h >= effective_min {
                        tiles[idx].y = (needed_bottom - tiles[idx].h).max(0);
                        tiles[idx].h = tiles[idx].h.min(available_h);
                    } else {
                        return false;
                    }
                }
                if !resolve_against(
                    tiles,
                    &nb_id,
                    direction,
                    &next_visited,
                    cols,
                    rows,
                    min_span,
                ) {
                    return false;
                }
            }
        }
        true
    }

    let visited = HashSet::new();
    let _ = resolve_against(
        tiles,
        active_id,
        preferred_direction,
        &visited,
        cols,
        rows,
        min_span,
    );
}

/// Pure deterministic 2D grid collision-slip solver.
pub fn resolve_slippery_layout(
    tiles: &[Tile],
    active_id: &str,
    action: &str,
    direction: Option<&str>,
    cols: i32,
    rows: i32,
    min_span: i32,
    preset_rect: Option<(i32, i32, i32, i32)>,
) -> Result<Vec<Tile>, String> {
    let mut state: Vec<Tile> = tiles.to_vec();
    let active_idx = state
        .iter()
        .position(|t| t.id == active_id)
        .ok_or_else(|| format!("Active tile {active_id:?} not found in tile list"))?;

    let act = action.trim().to_ascii_lowercase();

    match act.as_str() {
        "grow" => {
            let dir = direction.unwrap_or("");
            if !VALID_DIRECTIONS.contains(&dir) {
                return Err(format!(
                    "grow requires direction in {VALID_DIRECTIONS:?}, got {direction:?}"
                ));
            }
            let saved = state.clone();

            match dir {
                "right" => {
                    if state[active_idx].right() >= cols {
                        return Ok(saved);
                    }
                    state[active_idx].w += 1;
                }
                "left" => {
                    if state[active_idx].x <= 0 {
                        return Ok(saved);
                    }
                    state[active_idx].x -= 1;
                    state[active_idx].w += 1;
                }
                "down" => {
                    if state[active_idx].bottom() >= rows {
                        return Ok(saved);
                    }
                    state[active_idx].h += 1;
                }
                "up" => {
                    if state[active_idx].y <= 0 {
                        return Ok(saved);
                    }
                    state[active_idx].y -= 1;
                    state[active_idx].h += 1;
                }
                _ => unreachable!(),
            }

            if !try_slip_for_grow(&mut state, active_id, dir, cols, rows, min_span) {
                return Ok(saved);
            }
            if let Some(a) = state.iter_mut().find(|t| t.id == active_id) {
                a.snapshot_as_original();
            }
            Ok(state)
        }
        "shrink" => {
            let dir = direction.unwrap_or("");
            if !VALID_DIRECTIONS.contains(&dir) {
                return Err(format!(
                    "shrink requires direction in {VALID_DIRECTIONS:?}, got {direction:?}"
                ));
            }
            apply_shrink(&mut state, active_id, dir, cols, rows, min_span);
            Ok(state)
        }
        "move" => {
            let dir = direction.unwrap_or("");
            if !VALID_DIRECTIONS.contains(&dir) {
                return Err(format!(
                    "move requires direction in {VALID_DIRECTIONS:?}, got {direction:?}"
                ));
            }
            apply_move(&mut state, active_id, dir, cols, rows);
            Ok(state)
        }
        "pack" => {
            let dir = direction.unwrap_or("");
            if !VALID_PACK_DIRECTIONS.contains(&dir) {
                return Err(format!(
                    "pack requires direction in {VALID_PACK_DIRECTIONS:?}, got {direction:?}"
                ));
            }
            apply_pack(&mut state, active_id, dir, cols, rows);
            Ok(state)
        }
        "preset" => {
            let (px, py, pw, ph) =
                preset_rect.ok_or_else(|| "preset action requires preset_rect=(x, y, w, h)".to_string())?;
            let x = px.clamp(0, (cols - 1).max(0));
            let y = py.clamp(0, (rows - 1).max(0));
            let w = pw.clamp(1, (cols - x).max(1));
            let h = ph.clamp(1, (rows - y).max(1));
            state[active_idx].x = x;
            state[active_idx].y = y;
            state[active_idx].w = w;
            state[active_idx].h = h;
            state[active_idx].snapshot_as_original();
            relocate_colliding_neighbors(&mut state, active_id, cols, rows, min_span);
            Ok(state)
        }
        _ => Err(format!("Unsupported action {act:?}")),
    }
}

type TileSnapshot = Vec<(
    i32,
    i32,
    i32,
    i32,
    Option<i32>,
    Option<i32>,
    Option<i32>,
    Option<i32>,
)>;

fn snapshot_tiles(tiles: &[Tile]) -> TileSnapshot {
    tiles
        .iter()
        .map(|t| (t.x, t.y, t.w, t.h, t.orig_x, t.orig_y, t.orig_w, t.orig_h))
        .collect()
}

fn restore_tiles(tiles: &mut [Tile], snap: &TileSnapshot) {
    for (t, &(x, y, w, h, ox, oy, ow, oh)) in tiles.iter_mut().zip(snap.iter()) {
        t.x = x;
        t.y = y;
        t.w = w;
        t.h = h;
        t.orig_x = ox;
        t.orig_y = oy;
        t.orig_w = ow;
        t.orig_h = oh;
    }
}

fn try_slip_for_grow(
    tiles: &mut [Tile],
    active_id: &str,
    direction: &str,
    cols: i32,
    rows: i32,
    min_span: i32,
) -> bool {
    fn push_tile(
        tiles: &mut [Tile],
        source_id: &str,
        direction: &str,
        visited: &HashSet<String>,
        allow_relocate: bool,
        cols: i32,
        rows: i32,
        min_span: i32,
    ) -> bool {
        let mut next_visited = visited.clone();
        next_visited.insert(source_id.to_string());

        let source = match tiles.iter().find(|t| t.id == source_id).cloned() {
            Some(s) => s,
            None => return true,
        };

        let mut colliding: Vec<Tile> = tiles
            .iter()
            .filter(|t| !next_visited.contains(&t.id) && source.overlaps(t))
            .cloned()
            .collect();

        match direction {
            "right" => colliding.sort_by_key(|t| (t.x, t.y)),
            "left" => colliding.sort_by_key(|t| (-t.right(), t.y)),
            "down" => colliding.sort_by_key(|t| (t.y, t.x)),
            "up" => colliding.sort_by_key(|t| (-t.bottom(), t.x)),
            _ => {}
        }

        for nb_initial in colliding {
            let nb_id = nb_initial.id.clone();
            let nb_idx = match tiles.iter().position(|t| t.id == nb_id) {
                Some(i) => i,
                None => continue,
            };
            let current_source = match tiles.iter().find(|t| t.id == source_id).cloned() {
                Some(s) => s,
                None => continue,
            };
            if !current_source.overlaps(&tiles[nb_idx]) {
                continue;
            }

            let old_x = tiles[nb_idx].x;
            let old_y = tiles[nb_idx].y;
            let old_w = tiles[nb_idx].w;
            let old_h = tiles[nb_idx].h;
            let branch_snap = snapshot_tiles(tiles);
            let mut resolved_nb = false;

            if direction == "right" {
                let delta = current_source.right() - old_x;
                if delta <= 0 {
                    continue;
                }
                let eff_min = min_span.min(old_w);
                for comp in 0..=(old_w - eff_min) {
                    restore_tiles(tiles, &branch_snap);
                    let cand_x = current_source.right();
                    let cand_w = old_w - comp;
                    if cand_w >= eff_min && cand_x + cand_w <= cols {
                        tiles[nb_idx].x = cand_x;
                        tiles[nb_idx].w = cand_w;
                        if push_tile(
                            tiles,
                            &nb_id,
                            direction,
                            &next_visited,
                            allow_relocate,
                            cols,
                            rows,
                            min_span,
                        ) {
                            resolved_nb = true;
                            break;
                        }
                    }
                }
            } else if direction == "left" {
                let delta = old_x + old_w - current_source.x;
                if delta <= 0 {
                    continue;
                }
                let eff_min = min_span.min(old_w);
                for comp in 0..=(old_w - eff_min) {
                    restore_tiles(tiles, &branch_snap);
                    let cand_w = old_w - comp;
                    let cand_x = current_source.x - cand_w;
                    if cand_w >= eff_min && cand_x >= 0 {
                        tiles[nb_idx].x = cand_x;
                        tiles[nb_idx].w = cand_w;
                        if push_tile(
                            tiles,
                            &nb_id,
                            direction,
                            &next_visited,
                            allow_relocate,
                            cols,
                            rows,
                            min_span,
                        ) {
                            resolved_nb = true;
                            break;
                        }
                    }
                }
            } else if direction == "down" {
                let delta = current_source.bottom() - old_y;
                if delta <= 0 {
                    continue;
                }
                let eff_min = min_span.min(old_h);
                for comp in 0..=(old_h - eff_min) {
                    restore_tiles(tiles, &branch_snap);
                    let cand_y = current_source.bottom();
                    let cand_h = old_h - comp;
                    if cand_h >= eff_min && cand_y + cand_h <= rows {
                        tiles[nb_idx].y = cand_y;
                        tiles[nb_idx].h = cand_h;
                        if push_tile(
                            tiles,
                            &nb_id,
                            direction,
                            &next_visited,
                            allow_relocate,
                            cols,
                            rows,
                            min_span,
                        ) {
                            resolved_nb = true;
                            break;
                        }
                    }
                }
            } else if direction == "up" {
                let delta = old_y + old_h - current_source.y;
                if delta <= 0 {
                    continue;
                }
                let eff_min = min_span.min(old_h);
                for comp in 0..=(old_h - eff_min) {
                    restore_tiles(tiles, &branch_snap);
                    let cand_h = old_h - comp;
                    let cand_y = current_source.y - cand_h;
                    if cand_h >= eff_min && cand_y >= 0 {
                        tiles[nb_idx].y = cand_y;
                        tiles[nb_idx].h = cand_h;
                        if push_tile(
                            tiles,
                            &nb_id,
                            direction,
                            &next_visited,
                            allow_relocate,
                            cols,
                            rows,
                            min_span,
                        ) {
                            resolved_nb = true;
                            break;
                        }
                    }
                }
            }

            if resolved_nb {
                continue;
            }

            restore_tiles(tiles, &branch_snap);
            if allow_relocate {
                if let Some((sx, sy)) =
                    find_free_position(tiles, old_w, old_h, cols, rows, "left", Some(&nb_id))
                {
                    tiles[nb_idx].x = sx;
                    tiles[nb_idx].y = sy;
                    continue;
                }
            }
            return false;
        }

        true
    }

    let initial_snap = snapshot_tiles(tiles);
    let visited = HashSet::new();
    if push_tile(
        tiles,
        active_id,
        direction,
        &visited,
        false,
        cols,
        rows,
        min_span,
    ) {
        return true;
    }
    restore_tiles(tiles, &initial_snap);
    if push_tile(
        tiles,
        active_id,
        direction,
        &visited,
        true,
        cols,
        rows,
        min_span,
    ) {
        return true;
    }
    restore_tiles(tiles, &initial_snap);
    false
}

fn would_overlap(tiles: &[Tile], tile_id: &str, x: i32, y: i32, w: i32, h: i32) -> bool {
    let probe = Tile::new(tile_id, x, y, w, h);
    tiles.iter().any(|t| t.id != tile_id && probe.overlaps(t))
}

fn apply_shrink(
    tiles: &mut [Tile],
    active_id: &str,
    direction: &str,
    cols: i32,
    rows: i32,
    min_span: i32,
) {
    let active_idx = match tiles.iter().position(|t| t.id == active_id) {
        Some(i) => i,
        None => return,
    };
    let active = tiles[active_idx].clone();
    let eff_min_w = if active.w < min_span {
        min_span.min(active.w)
    } else {
        min_span
    };
    let eff_min_h = if active.h < min_span {
        min_span.min(active.h)
    } else {
        min_span
    };

    fn v_overlap_range(top: i32, bottom: i32, b: &Tile) -> bool {
        top < b.bottom() && bottom > b.y
    }

    fn h_overlap_range(left: i32, right: i32, b: &Tile) -> bool {
        left < b.right() && right > b.x
    }

    fn reclaim_from_right(
        tiles: &mut [Tile],
        vacated_x: i32,
        top: i32,
        bottom: i32,
        visited: &HashSet<String>,
    ) {
        let neighbor_ids: Vec<String> = tiles
            .iter()
            .filter(|t| {
                !visited.contains(&t.id) && t.x == vacated_x && v_overlap_range(top, bottom, t)
            })
            .map(|t| t.id.clone())
            .collect();

        for nb_id in neighbor_ids {
            let idx = match tiles.iter().position(|t| t.id == nb_id) {
                Some(i) => i,
                None => continue,
            };
            let old_nb_right = tiles[idx].right();
            let old_nb_y = tiles[idx].y;
            let old_nb_bottom = tiles[idx].bottom();
            let nb_x = tiles[idx].x;
            let nb_y = tiles[idx].y;
            let nb_w = tiles[idx].w;
            let nb_h = tiles[idx].h;
            let orig_w = tiles[idx].orig_w;

            if orig_w.is_some_and(|ow| nb_w < ow) {
                if !would_overlap(tiles, &nb_id, nb_x - 1, nb_y, nb_w + 1, nb_h) {
                    tiles[idx].x -= 1;
                    tiles[idx].w += 1;
                }
            } else if !would_overlap(tiles, &nb_id, nb_x - 1, nb_y, nb_w, nb_h) {
                tiles[idx].x -= 1;
                let mut next_visited = visited.clone();
                next_visited.insert(nb_id);
                reclaim_from_right(
                    tiles,
                    old_nb_right,
                    old_nb_y,
                    old_nb_bottom,
                    &next_visited,
                );
            }
        }
    }

    fn reclaim_from_left(
        tiles: &mut [Tile],
        vacated_right: i32,
        top: i32,
        bottom: i32,
        visited: &HashSet<String>,
    ) {
        let neighbor_ids: Vec<String> = tiles
            .iter()
            .filter(|t| {
                !visited.contains(&t.id)
                    && t.right() == vacated_right
                    && v_overlap_range(top, bottom, t)
            })
            .map(|t| t.id.clone())
            .collect();

        for nb_id in neighbor_ids {
            let idx = match tiles.iter().position(|t| t.id == nb_id) {
                Some(i) => i,
                None => continue,
            };
            let old_nb_left = tiles[idx].x;
            let old_nb_y = tiles[idx].y;
            let old_nb_bottom = tiles[idx].bottom();
            let nb_x = tiles[idx].x;
            let nb_y = tiles[idx].y;
            let nb_w = tiles[idx].w;
            let nb_h = tiles[idx].h;
            let orig_w = tiles[idx].orig_w;

            if orig_w.is_some_and(|ow| nb_w < ow) {
                if !would_overlap(tiles, &nb_id, nb_x, nb_y, nb_w + 1, nb_h) {
                    tiles[idx].w += 1;
                }
            } else if !would_overlap(tiles, &nb_id, nb_x + 1, nb_y, nb_w, nb_h) {
                tiles[idx].x += 1;
                let mut next_visited = visited.clone();
                next_visited.insert(nb_id);
                reclaim_from_left(
                    tiles,
                    old_nb_left,
                    old_nb_y,
                    old_nb_bottom,
                    &next_visited,
                );
            }
        }
    }

    fn reclaim_from_bottom(
        tiles: &mut [Tile],
        vacated_y: i32,
        left: i32,
        right: i32,
        visited: &HashSet<String>,
    ) {
        let neighbor_ids: Vec<String> = tiles
            .iter()
            .filter(|t| {
                !visited.contains(&t.id) && t.y == vacated_y && h_overlap_range(left, right, t)
            })
            .map(|t| t.id.clone())
            .collect();

        for nb_id in neighbor_ids {
            let idx = match tiles.iter().position(|t| t.id == nb_id) {
                Some(i) => i,
                None => continue,
            };
            let old_nb_bottom = tiles[idx].bottom();
            let old_nb_x = tiles[idx].x;
            let old_nb_right = tiles[idx].right();
            let nb_x = tiles[idx].x;
            let nb_y = tiles[idx].y;
            let nb_w = tiles[idx].w;
            let nb_h = tiles[idx].h;
            let orig_h = tiles[idx].orig_h;

            if orig_h.is_some_and(|oh| nb_h < oh) {
                if !would_overlap(tiles, &nb_id, nb_x, nb_y - 1, nb_w, nb_h + 1) {
                    tiles[idx].y -= 1;
                    tiles[idx].h += 1;
                }
            } else if !would_overlap(tiles, &nb_id, nb_x, nb_y - 1, nb_w, nb_h) {
                tiles[idx].y -= 1;
                let mut next_visited = visited.clone();
                next_visited.insert(nb_id);
                reclaim_from_bottom(
                    tiles,
                    old_nb_bottom,
                    old_nb_x,
                    old_nb_right,
                    &next_visited,
                );
            }
        }
    }

    fn reclaim_from_top(
        tiles: &mut [Tile],
        vacated_bottom: i32,
        left: i32,
        right: i32,
        visited: &HashSet<String>,
    ) {
        let neighbor_ids: Vec<String> = tiles
            .iter()
            .filter(|t| {
                !visited.contains(&t.id)
                    && t.bottom() == vacated_bottom
                    && h_overlap_range(left, right, t)
            })
            .map(|t| t.id.clone())
            .collect();

        for nb_id in neighbor_ids {
            let idx = match tiles.iter().position(|t| t.id == nb_id) {
                Some(i) => i,
                None => continue,
            };
            let old_nb_top = tiles[idx].y;
            let old_nb_x = tiles[idx].x;
            let old_nb_right = tiles[idx].right();
            let nb_x = tiles[idx].x;
            let nb_y = tiles[idx].y;
            let nb_w = tiles[idx].w;
            let nb_h = tiles[idx].h;
            let orig_h = tiles[idx].orig_h;

            if orig_h.is_some_and(|oh| nb_h < oh) {
                if !would_overlap(tiles, &nb_id, nb_x, nb_y, nb_w, nb_h + 1) {
                    tiles[idx].h += 1;
                }
            } else if !would_overlap(tiles, &nb_id, nb_x, nb_y + 1, nb_w, nb_h) {
                tiles[idx].y += 1;
                let mut next_visited = visited.clone();
                next_visited.insert(nb_id);
                reclaim_from_top(tiles, old_nb_top, old_nb_x, old_nb_right, &next_visited);
            }
        }
    }

    let mut init_visited = HashSet::new();
    init_visited.insert(active.id.clone());

    if direction == "left" || direction == "right" {
        if active.w <= eff_min_w {
            return;
        }
        let has_right_neighbors = tiles.iter().any(|t| {
            t.id != active.id
                && t.x == active.right()
                && v_overlap_range(active.y, active.bottom(), t)
        });
        let has_left_neighbors = tiles.iter().any(|t| {
            t.id != active.id
                && t.right() == active.x
                && v_overlap_range(active.y, active.bottom(), t)
        });

        let contract_right_edge = if direction == "right" {
            !(active.right() == cols && has_left_neighbors && !has_right_neighbors)
        } else {
            (active.x == 0) || (has_right_neighbors && !has_left_neighbors)
        };

        if contract_right_edge {
            let old_right = active.right();
            let ay = active.y;
            let ab = active.bottom();
            tiles[active_idx].w -= 1;
            tiles[active_idx].snapshot_as_original();
            reclaim_from_right(tiles, old_right, ay, ab, &init_visited);
        } else {
            let old_left = active.x;
            let ay = active.y;
            let ab = active.bottom();
            tiles[active_idx].x += 1;
            tiles[active_idx].w -= 1;
            tiles[active_idx].snapshot_as_original();
            reclaim_from_left(tiles, old_left, ay, ab, &init_visited);
        }
    } else if direction == "up" || direction == "down" {
        if active.h <= eff_min_h {
            return;
        }
        let has_bottom_neighbors = tiles.iter().any(|t| {
            t.id != active.id
                && t.y == active.bottom()
                && h_overlap_range(active.x, active.right(), t)
        });
        let has_top_neighbors = tiles.iter().any(|t| {
            t.id != active.id
                && t.bottom() == active.y
                && h_overlap_range(active.x, active.right(), t)
        });

        let contract_bottom_edge = if direction == "down" {
            !(active.bottom() == rows && has_top_neighbors && !has_bottom_neighbors)
        } else {
            (active.y == 0) || (has_bottom_neighbors && !has_top_neighbors)
        };

        if contract_bottom_edge {
            let old_bottom = active.bottom();
            let ax = active.x;
            let ar = active.right();
            tiles[active_idx].h -= 1;
            tiles[active_idx].snapshot_as_original();
            reclaim_from_bottom(tiles, old_bottom, ax, ar, &init_visited);
        } else {
            let old_top = active.y;
            let ax = active.x;
            let ar = active.right();
            tiles[active_idx].y += 1;
            tiles[active_idx].h -= 1;
            tiles[active_idx].snapshot_as_original();
            reclaim_from_top(tiles, old_top, ax, ar, &init_visited);
        }
    }
}

fn has_any_overlap(tiles: &[Tile]) -> bool {
    for i in 0..tiles.len() {
        for j in (i + 1)..tiles.len() {
            if tiles[i].overlaps(&tiles[j]) {
                return true;
            }
        }
    }
    false
}

fn apply_move(tiles: &mut [Tile], active_id: &str, direction: &str, cols: i32, rows: i32) {
    let active_idx = match tiles.iter().position(|t| t.id == active_id) {
        Some(i) => i,
        None => return,
    };
    let initial_snap = snapshot_tiles(tiles);
    let old_x = tiles[active_idx].x;
    let old_y = tiles[active_idx].y;

    match direction {
        "right" => {
            if tiles[active_idx].right() >= cols {
                return;
            }
            tiles[active_idx].x += 1;
        }
        "left" => {
            if tiles[active_idx].x <= 0 {
                return;
            }
            tiles[active_idx].x -= 1;
        }
        "down" => {
            if tiles[active_idx].bottom() >= rows {
                return;
            }
            tiles[active_idx].y += 1;
        }
        "up" => {
            if tiles[active_idx].y <= 0 {
                return;
            }
            tiles[active_idx].y -= 1;
        }
        _ => return,
    }

    let active_snap = tiles[active_idx].clone();
    let colliding_ids: Vec<String> = tiles
        .iter()
        .filter(|t| t.id != active_id && active_snap.overlaps(t))
        .map(|t| t.id.clone())
        .collect();

    for nb_id in colliding_ids {
        let nb_idx = match tiles.iter().position(|t| t.id == nb_id) {
            Some(i) => i,
            None => continue,
        };
        let nb_x = tiles[nb_idx].x;
        let nb_y = tiles[nb_idx].y;
        let nb_w = tiles[nb_idx].w;
        let nb_h = tiles[nb_idx].h;
        let nb_right = tiles[nb_idx].right();
        let nb_bottom = tiles[nb_idx].bottom();

        let act_x = tiles[active_idx].x;
        let act_y = tiles[active_idx].y;
        let act_w = tiles[active_idx].w;
        let act_h = tiles[active_idx].h;
        let act_right = tiles[active_idx].right();
        let act_bottom = tiles[active_idx].bottom();

        if direction == "right" {
            let opp_x = act_x - nb_w;
            if opp_x >= 0 && !would_overlap(tiles, &nb_id, opp_x, nb_y, nb_w, nb_h) {
                tiles[nb_idx].x = opp_x;
            } else if nb_right + 1 <= cols
                && !would_overlap(tiles, &nb_id, act_right, nb_y, nb_w, nb_h)
            {
                tiles[nb_idx].x = act_right;
            } else if old_x + nb_w <= cols {
                let nb_old_x = nb_x;
                tiles[active_idx].x = (cols - act_w).min(nb_right - act_w);
                tiles[nb_idx].x = old_x;
                if tiles[active_idx].overlaps(&tiles[nb_idx]) {
                    tiles[active_idx].x = (cols - act_w).min(tiles[nb_idx].right());
                }
                if would_overlap(tiles, &nb_id, tiles[nb_idx].x, nb_y, nb_w, nb_h) {
                    tiles[nb_idx].x = nb_old_x;
                    if let Some((sx, sy)) =
                        find_free_position(tiles, nb_w, nb_h, cols, rows, "left", Some(&nb_id))
                    {
                        tiles[nb_idx].x = sx;
                        tiles[nb_idx].y = sy;
                    }
                }
            } else if let Some((sx, sy)) =
                find_free_position(tiles, nb_w, nb_h, cols, rows, "left", Some(&nb_id))
            {
                tiles[nb_idx].x = sx;
                tiles[nb_idx].y = sy;
            }
        } else if direction == "left" {
            let opp_x = act_right;
            if opp_x + nb_w <= cols && !would_overlap(tiles, &nb_id, opp_x, nb_y, nb_w, nb_h) {
                tiles[nb_idx].x = opp_x;
            } else if act_x - nb_w >= 0
                && !would_overlap(tiles, &nb_id, act_x - nb_w, nb_y, nb_w, nb_h)
            {
                tiles[nb_idx].x = act_x - nb_w;
            } else {
                let nb_old_x = nb_x;
                tiles[nb_idx].x = old_x + act_w - nb_w;
                tiles[active_idx].x = nb_old_x;
                if would_overlap(tiles, &nb_id, tiles[nb_idx].x, nb_y, nb_w, nb_h) {
                    tiles[nb_idx].x = nb_old_x;
                    if let Some((sx, sy)) =
                        find_free_position(tiles, nb_w, nb_h, cols, rows, "left", Some(&nb_id))
                    {
                        tiles[nb_idx].x = sx;
                        tiles[nb_idx].y = sy;
                    }
                }
            }
        } else if direction == "down" {
            let opp_y = act_y - nb_h;
            if opp_y >= 0 && !would_overlap(tiles, &nb_id, nb_x, opp_y, nb_w, nb_h) {
                tiles[nb_idx].y = opp_y;
            } else if act_bottom + nb_h <= rows
                && !would_overlap(tiles, &nb_id, nb_x, act_bottom, nb_w, nb_h)
            {
                tiles[nb_idx].y = act_bottom;
            } else {
                let nb_old_y = nb_y;
                tiles[active_idx].y = (rows - act_h).min(nb_bottom - act_h);
                tiles[nb_idx].y = old_y;
                if would_overlap(tiles, &nb_id, nb_x, tiles[nb_idx].y, nb_w, nb_h) {
                    tiles[nb_idx].y = nb_old_y;
                    if let Some((sx, sy)) =
                        find_free_position(tiles, nb_w, nb_h, cols, rows, "left", Some(&nb_id))
                    {
                        tiles[nb_idx].x = sx;
                        tiles[nb_idx].y = sy;
                    }
                }
            }
        } else if direction == "up" {
            let opp_y = act_bottom;
            if opp_y + nb_h <= rows && !would_overlap(tiles, &nb_id, nb_x, opp_y, nb_w, nb_h) {
                tiles[nb_idx].y = opp_y;
            } else if act_y - nb_h >= 0
                && !would_overlap(tiles, &nb_id, nb_x, act_y - nb_h, nb_w, nb_h)
            {
                tiles[nb_idx].y = act_y - nb_h;
            } else {
                let nb_old_y = nb_y;
                tiles[nb_idx].y = old_y + act_h - nb_h;
                tiles[active_idx].y = nb_old_y;
                if would_overlap(tiles, &nb_id, nb_x, tiles[nb_idx].y, nb_w, nb_h) {
                    tiles[nb_idx].y = nb_old_y;
                    if let Some((sx, sy)) =
                        find_free_position(tiles, nb_w, nb_h, cols, rows, "left", Some(&nb_id))
                    {
                        tiles[nb_idx].x = sx;
                        tiles[nb_idx].y = sy;
                    }
                }
            }
        }
    }

    tiles[active_idx].snapshot_as_original();
    relocate_colliding_neighbors(tiles, active_id, cols, rows, DEFAULT_MIN_SPAN);
    if has_any_overlap(tiles) {
        restore_tiles(tiles, &initial_snap);
    }
}

fn apply_pack(tiles: &mut [Tile], active_id: &str, direction: &str, cols: i32, rows: i32) {
    let active_idx = match tiles.iter().position(|t| t.id == active_id) {
        Some(i) => i,
        None => return,
    };
    let initial_snap = snapshot_tiles(tiles);

    if direction == "left" {
        tiles[active_idx].x = 0;
        tiles[active_idx].snapshot_as_original();

        let mut other_ids: Vec<(i32, i32, String)> = tiles
            .iter()
            .filter(|t| t.id != active_id)
            .map(|t| (t.x, t.y, t.id.clone()))
            .collect();
        other_ids.sort_by_key(|(x, y, _)| (*x, *y));

        for (_, _, nb_id) in other_ids {
            let nb_idx = match tiles.iter().position(|t| t.id == nb_id) {
                Some(i) => i,
                None => continue,
            };
            if tiles[active_idx].overlaps(&tiles[nb_idx]) {
                let nw = tiles[nb_idx].w;
                let nh = tiles[nb_idx].h;
                if let Some((sx, sy)) =
                    find_free_position(tiles, nw, nh, cols, rows, "left", Some(&nb_id))
                {
                    tiles[nb_idx].x = sx;
                    tiles[nb_idx].y = sy;
                    tiles[nb_idx].snapshot_as_original();
                }
            }
        }
    } else if direction == "right" {
        let aw = tiles[active_idx].w;
        tiles[active_idx].x = (cols - aw).max(0);
        tiles[active_idx].snapshot_as_original();

        let mut other_ids: Vec<(i32, i32, String)> = tiles
            .iter()
            .filter(|t| t.id != active_id)
            .map(|t| (-t.right(), t.y, t.id.clone()))
            .collect();
        other_ids.sort_by_key(|(neg_r, y, _)| (*neg_r, *y));

        for (_, _, nb_id) in other_ids {
            let nb_idx = match tiles.iter().position(|t| t.id == nb_id) {
                Some(i) => i,
                None => continue,
            };
            if tiles[active_idx].overlaps(&tiles[nb_idx]) {
                let nw = tiles[nb_idx].w;
                let nh = tiles[nb_idx].h;
                if let Some((sx, sy)) =
                    find_free_position(tiles, nw, nh, cols, rows, "right", Some(&nb_id))
                {
                    tiles[nb_idx].x = sx;
                    tiles[nb_idx].y = sy;
                    tiles[nb_idx].snapshot_as_original();
                }
            }
        }
    }

    relocate_colliding_neighbors(tiles, active_id, cols, rows, DEFAULT_MIN_SPAN);
    if has_any_overlap(tiles) {
        restore_tiles(tiles, &initial_snap);
    }
}

fn relocate_colliding_neighbors(
    tiles: &mut [Tile],
    active_id: &str,
    cols: i32,
    rows: i32,
    min_span: i32,
) {
    let nb_ids: Vec<String> = tiles
        .iter()
        .filter(|t| t.id != active_id)
        .map(|t| t.id.clone())
        .collect();

    for nb_id in nb_ids {
        let idx = match tiles.iter().position(|t| t.id == nb_id) {
            Some(i) => i,
            None => continue,
        };
        let nb = tiles[idx].clone();
        if !would_overlap(tiles, &nb_id, nb.x, nb.y, nb.w, nb.h) {
            continue;
        }
        let nw = nb.w;
        let nh = nb.h;
        let anchor = nb.anchor.clone();
        if let Some((sx, sy)) =
            find_free_position(tiles, nw, nh, cols, rows, &anchor, Some(&nb_id))
        {
            tiles[idx].x = sx;
            tiles[idx].y = sy;
            continue;
        }
        let eff_min_w = min_span.min(nw).max(1);
        let eff_min_h = min_span.min(nh).max(1);
        let mut placed = false;
        let mut cand_w = nw;
        while cand_w >= eff_min_w && !placed {
            let mut cand_h = nh;
            while cand_h >= eff_min_h {
                if let Some((sx, sy)) =
                    find_free_position(tiles, cand_w, cand_h, cols, rows, &anchor, Some(&nb_id))
                {
                    tiles[idx].x = sx;
                    tiles[idx].y = sy;
                    tiles[idx].w = cand_w;
                    tiles[idx].h = cand_h;
                    placed = true;
                    break;
                }
                cand_h -= 1;
            }
            cand_w -= 1;
        }
    }
}

pub struct StateStore {
    pub path: PathBuf,
}

impl StateStore {
    pub fn new(custom_path: Option<PathBuf>) -> Self {
        if let Some(p) = custom_path {
            return Self { path: p };
        }
        let runtime_dir = env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| {
            let uid = unsafe_or_env_uid();
            format!("/tmp/novos-runtime-{uid}")
        });
        Self {
            path: PathBuf::from(runtime_dir)
                .join("novos-slippery-tiles")
                .join("state.json"),
        }
    }

    pub fn load(
        &self,
        profile: &GridProfile,
        anchor: &str,
        init_if_empty: bool,
    ) -> (Vec<Tile>, String) {
        if self.path.is_file() {
            if let Ok(content) = fs::read_to_string(&self.path) {
                if let Some((cols, rows, active_id, tiles)) = parse_state_json(&content) {
                    if cols == profile.cols && rows == profile.rows && !tiles.is_empty() {
                        let act = if active_id.is_empty() {
                            tiles[0].id.clone()
                        } else {
                            active_id
                        };
                        if tiles.iter().any(|t| t.id == act) {
                            return (tiles, act);
                        }
                    }
                }
            }
        }
        if !init_if_empty {
            return (Vec::new(), String::new());
        }
        let (tiles, active) =
            spawn_building_block(&[], "win-1", profile, anchor, "default").unwrap();
        (tiles, active.id)
    }

    pub fn save(&self, profile: &GridProfile, tiles: &[Tile], active_id: &str) {
        if let Some(parent) = self.path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let tiles_json: Vec<String> = tiles.iter().map(|t| format!("    {}", t.to_json())).collect();
        let payload = format!(
            "{{\n  \"width_px\": {},\n  \"height_px\": {},\n  \"cols\": {},\n  \"rows\": {},\n  \"aspect\": \"{}\",\n  \"active_id\": \"{}\",\n  \"tiles\": [\n{}\n  ]\n}}\n",
            profile.width_px,
            profile.height_px,
            profile.cols,
            profile.rows,
            escape_json(&profile.aspect_label),
            escape_json(active_id),
            tiles_json.join(",\n")
        );
        let _ = fs::write(&self.path, payload);
    }
}

fn unsafe_or_env_uid() -> String {
    if let Ok(u) = env::var("UID") {
        if !u.is_empty() {
            return u;
        }
    }
    if let Ok(out) = Command::new("id").arg("-u").output() {
        let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if !s.is_empty() {
            return s;
        }
    }
    "1000".to_string()
}

fn unescape_json_string(raw: &str) -> Option<String> {
    let mut out = String::with_capacity(raw.len());
    let mut chars = raw.chars();
    while let Some(c) = chars.next() {
        if c == '\\' {
            match chars.next()? {
                'n' => out.push('\n'),
                'r' => out.push('\r'),
                't' => out.push('\t'),
                '\\' => out.push('\\'),
                '"' => out.push('"'),
                '/' => out.push('/'),
                other => out.push(other),
            }
        } else {
            out.push(c);
        }
    }
    Some(out)
}

/// Parse top-level `"key": value` entries of a JSON object `{ ... }` in a quote/brace-aware pass.
fn parse_json_object_fields(src: &str) -> Option<HashMap<String, String>> {
    let trimmed = src.trim();
    let body = trimmed.strip_prefix('{')?.strip_suffix('}')?;
    let mut map = HashMap::new();
    let bytes = body.as_bytes();
    let n = bytes.len();
    let mut i = 0usize;

    while i < n {
        while i < n && (bytes[i].is_ascii_whitespace() || bytes[i] == b',') {
            i += 1;
        }
        if i >= n {
            break;
        }
        if bytes[i] != b'"' {
            return None;
        }
        i += 1;
        let key_start = i;
        while i < n {
            if bytes[i] == b'\\' {
                i += 2;
                continue;
            }
            if bytes[i] == b'"' {
                break;
            }
            i += 1;
        }
        if i >= n {
            return None;
        }
        let key = unescape_json_string(&body[key_start..i])?;
        i += 1;

        while i < n && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i >= n || bytes[i] != b':' {
            return None;
        }
        i += 1;
        while i < n && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i >= n {
            return None;
        }

        let val_start = i;
        if bytes[i] == b'"' {
            i += 1;
            while i < n {
                if bytes[i] == b'\\' {
                    i += 2;
                    continue;
                }
                if bytes[i] == b'"' {
                    i += 1;
                    break;
                }
                i += 1;
            }
        } else if bytes[i] == b'[' || bytes[i] == b'{' {
            let mut depth = 0usize;
            let mut in_str = false;
            while i < n {
                let b = bytes[i];
                if in_str {
                    if b == b'\\' {
                        i += 2;
                        continue;
                    }
                    if b == b'"' {
                        in_str = false;
                    }
                } else {
                    match b {
                        b'"' => in_str = true,
                        b'[' | b'{' => depth += 1,
                        b']' | b'}' => {
                            depth = depth.saturating_sub(1);
                            if depth == 0 {
                                i += 1;
                                break;
                            }
                        }
                        _ => {}
                    }
                }
                i += 1;
            }
        } else {
            while i < n && bytes[i] != b',' && !bytes[i].is_ascii_whitespace() {
                i += 1;
            }
        }
        let val_raw = body[val_start..i].trim().to_string();
        map.insert(key, val_raw);
    }

    Some(map)
}

fn field_as_int(fields: &HashMap<String, String>, key: &str) -> Option<i32> {
    let raw = fields.get(key)?;
    if raw == "null" {
        return None;
    }
    raw.parse().ok()
}

fn field_as_str(fields: &HashMap<String, String>, key: &str) -> Option<String> {
    let raw = fields.get(key)?;
    let inner = raw.strip_prefix('"')?.strip_suffix('"')?;
    unescape_json_string(inner)
}

fn parse_state_json(content: &str) -> Option<(i32, i32, String, Vec<Tile>)> {
    let root_fields = parse_json_object_fields(content)?;
    let cols = field_as_int(&root_fields, "cols")?;
    let rows = field_as_int(&root_fields, "rows")?;
    let active_id = field_as_str(&root_fields, "active_id").unwrap_or_default();
    let tiles_raw = root_fields.get("tiles")?;
    let array_body = tiles_raw.trim().strip_prefix('[')?.strip_suffix(']')?;

    let mut tiles = Vec::new();
    let mut depth = 0usize;
    let mut in_str = false;
    let mut escape = false;
    let mut obj_start = None;

    for (idx, ch) in array_body.char_indices() {
        if in_str {
            if escape {
                escape = false;
            } else if ch == '\\' {
                escape = true;
            } else if ch == '"' {
                in_str = false;
            }
            continue;
        }
        match ch {
            '"' => in_str = true,
            '{' => {
                if depth == 0 {
                    obj_start = Some(idx);
                }
                depth += 1;
            }
            '}' => {
                depth = depth.saturating_sub(1);
                if depth == 0 {
                    if let Some(st) = obj_start {
                        let obj_str = &array_body[st..=idx];
                        if let Some(tfields) = parse_json_object_fields(obj_str) {
                            if let (Some(id), Some(x), Some(y), Some(w), Some(h)) = (
                                field_as_str(&tfields, "id"),
                                field_as_int(&tfields, "x"),
                                field_as_int(&tfields, "y"),
                                field_as_int(&tfields, "w"),
                                field_as_int(&tfields, "h"),
                            ) {
                                let anchor = field_as_str(&tfields, "anchor")
                                    .unwrap_or_else(|| "left".to_string());
                                let orig_x = field_as_int(&tfields, "orig_x").or(Some(x));
                                let orig_y = field_as_int(&tfields, "orig_y").or(Some(y));
                                let orig_w = field_as_int(&tfields, "orig_w").or(Some(w));
                                let orig_h = field_as_int(&tfields, "orig_h").or(Some(h));
                                tiles.push(Tile {
                                    id,
                                    x,
                                    y,
                                    w,
                                    h,
                                    anchor,
                                    orig_x,
                                    orig_y,
                                    orig_w,
                                    orig_h,
                                });
                            }
                        }
                        obj_start = None;
                    }
                }
            }
            _ => {}
        }
    }

    Some((cols, rows, active_id, tiles))
}

pub fn apply_window_geometry(profile: &GridProfile, tile: &Tile) -> (i32, i32, i32, i32) {
    let (px_x, px_y, px_w, px_h) = profile.cell_to_pixel_rect(tile.x, tile.y, tile.w, tile.h);
    let has_display = env::var("DISPLAY").is_ok_and(|v| !v.is_empty())
        || env::var("WAYLAND_DISPLAY").is_ok_and(|v| !v.is_empty());

    if has_display {
        if command_exists("cosmic-toplevel") {
            let _ = Command::new("cosmic-toplevel")
                .args([
                    "set-geometry",
                    "--active",
                    &px_x.to_string(),
                    &px_y.to_string(),
                    &px_w.to_string(),
                    &px_h.to_string(),
                ])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        if command_exists("wlrctl") {
            let _ = Command::new("wlrctl")
                .args(["toplevel", "focus"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        if command_exists("wmctrl") {
            let _ = Command::new("wmctrl")
                .args(["-r", ":ACTIVE:", "-b", "remove,maximized_vert,maximized_horz"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let _ = Command::new("wmctrl")
                .args([
                    "-r",
                    ":ACTIVE:",
                    "-e",
                    &format!("0,{px_x},{px_y},{px_w},{px_h}"),
                ])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        } else if command_exists("xdotool") {
            let _ = Command::new("xdotool")
                .args([
                    "getactivewindow",
                    "windowmove",
                    &px_x.to_string(),
                    &px_y.to_string(),
                    "windowsize",
                    &px_w.to_string(),
                    &px_h.to_string(),
                ])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
    }

    (px_x, px_y, px_w, px_h)
}

pub fn compute_named_preset(name: &str, cols: i32, rows: i32) -> Result<(i32, i32, i32, i32), String> {
    let key = name.trim().to_ascii_lowercase();
    match key.as_str() {
        "1/4 col" | "1/4-col" | "quarter" => Ok((0, 0, (cols / 4).max(2), rows)),
        "1/3 col" | "1/3-col" | "third" => Ok((0, 0, (cols / 3).max(2), rows)),
        "center 1/2" | "center-1/2" | "center" => {
            let w = (cols / 2).max(2);
            Ok(((cols - w) / 2, 0, w, rows))
        }
        "2x2 stack" | "2x2-stack" | "stack" => Ok((0, 0, (cols / 4).max(2), (rows / 2).max(2))),
        "pack left" | "pack-left" => Ok((0, 0, (cols / 4).max(2), rows)),
        "pack right" | "pack-right" => {
            let w = (cols / 4).max(2);
            Ok((cols - w, 0, w, rows))
        }
        _ => Err(format!("Unknown preset name: {name:?}")),
    }
}

pub fn parse_preset_args(
    grid_spec: &str,
    rect_spec: &str,
    profile: &GridProfile,
) -> Result<(i32, i32, i32, i32), String> {
    let (c_str, r_str) = grid_spec
        .trim()
        .split_once('x')
        .ok_or_else(|| format!("Invalid grid specification {grid_spec:?}; expected <cols>x<rows>"))?;
    let spec_cols: i32 = c_str
        .parse()
        .map_err(|_| format!("Invalid grid specification {grid_spec:?}; expected <cols>x<rows>"))?;
    let spec_rows: i32 = r_str
        .parse()
        .map_err(|_| format!("Invalid grid specification {grid_spec:?}; expected <cols>x<rows>"))?;
    if spec_cols <= 0 || spec_rows <= 0 {
        return Err("Grid cols and rows must be > 0".to_string());
    }

    let parts: Vec<&str> = rect_spec.split(',').map(|s| s.trim()).collect();
    if parts.len() != 4 {
        return Err(format!(
            "Invalid rect specification {rect_spec:?}; expected <x>,<y>,<w>,<h>"
        ));
    }
    let mut x: i32 = parts[0]
        .parse()
        .map_err(|_| format!("Invalid rect specification {rect_spec:?}"))?;
    let mut y: i32 = parts[1]
        .parse()
        .map_err(|_| format!("Invalid rect specification {rect_spec:?}"))?;
    let mut w: i32 = parts[2]
        .parse()
        .map_err(|_| format!("Invalid rect specification {rect_spec:?}"))?;
    let mut h: i32 = parts[3]
        .parse()
        .map_err(|_| format!("Invalid rect specification {rect_spec:?}"))?;

    if w <= 0 || h <= 0 || x < 0 || y < 0 || x + w > spec_cols || y + h > spec_rows {
        return Err(format!(
            "Rect ({x},{y},{w},{h}) out of bounds for grid {spec_cols}x{spec_rows}"
        ));
    }

    if (spec_cols, spec_rows) != (profile.cols, profile.rows) {
        x = round_ratio(x * profile.cols, spec_cols);
        y = round_ratio(y * profile.rows, spec_rows);
        w = round_ratio(w * profile.cols, spec_cols).max(1);
        h = round_ratio(h * profile.rows, spec_rows).max(1);
        x = x.min(profile.cols - w);
        y = y.min(profile.rows - h);
    }

    Ok((x, y, w, h))
}

const TK_OVERLAY_HELPER: &str = r##"
import sys, tkinter as tk

cols = int(sys.argv[1])
rows = int(sys.argv[2])
width_px = int(sys.argv[3])
height_px = int(sys.argv[4])
aspect = sys.argv[5]
ax = int(sys.argv[6])
ay = int(sys.argv[7])
aw = int(sys.argv[8])
ah = int(sys.argv[9])

THEME_BG = "#1B1B1B"
THEME_SURFACE = "#262626"
THEME_CELL_INACTIVE = "#2E2E2E"
THEME_CELL_BORDER = "#444444"
THEME_ACCENT = "#FFAD00"
THEME_TEXT = "#F2F2F2"
THEME_SUBTEXT = "#B0B0B0"

root = tk.Tk()
root.title(f"NovOS gTile Overlay ({cols}x{rows} - {aspect})")
root.configure(bg=THEME_BG)
root.resizable(False, False)
try:
    root.attributes("-topmost", True)
except Exception:
    pass

sel_anchor = [ax, ay]
sel_cursor = [ax + aw - 1, ay + ah - 1]
drag_active = [False]

def current_rect():
    x0 = min(sel_anchor[0], sel_cursor[0])
    y0 = min(sel_anchor[1], sel_cursor[1])
    x1 = max(sel_anchor[0], sel_cursor[0])
    y1 = max(sel_anchor[1], sel_cursor[1])
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)

header = tk.Frame(root, bg=THEME_BG, padx=14, pady=10)
header.pack(fill=tk.X)
tk.Label(
    header,
    text=f"NovOS Slippery Tile / gTile ({cols}×{rows} Grid • {width_px}×{height_px})",
    bg=THEME_BG,
    fg=THEME_ACCENT,
    font=("DejaVu Sans", 11, "bold"),
).pack(side=tk.LEFT)

coords_var = tk.StringVar()
tk.Label(
    header,
    textvariable=coords_var,
    bg=THEME_BG,
    fg=THEME_TEXT,
    font=("DejaVu Sans Mono", 10),
).pack(side=tk.RIGHT)

grid_frame = tk.Frame(root, bg=THEME_BG, padx=14, pady=6)
grid_frame.pack()

cell_widgets = {}
widget_to_cell = {}
cell_w = 46 if cols <= 8 else 36
cell_h = 44

def refresh_cells():
    rx, ry, rw, rh = current_rect()
    px_x = round(rx * width_px / cols)
    px_y = round(ry * height_px / rows)
    px_w = round((rx + rw) * width_px / cols) - px_x
    px_h = round((ry + rh) * height_px / rows) - px_y
    coords_var.set(f"[{rx},{ry}  {rw}×{rh}]  ({px_w}×{px_h}px)")
    for (cx, cy), frame in cell_widgets.items():
        in_sel = rx <= cx < rx + rw and ry <= cy < ry + rh
        frame.configure(
            bg=THEME_ACCENT if in_sel else THEME_CELL_INACTIVE,
            highlightbackground=THEME_ACCENT if in_sel else THEME_CELL_BORDER,
        )

def commit_rect(rect=None):
    rx, ry, rw, rh = rect if rect is not None else current_rect()
    print(f"RECT:{rx},{ry},{rw},{rh}", flush=True)
    root.destroy()

def on_cell_press(cx, cy):
    drag_active[0] = True
    sel_anchor[0], sel_anchor[1] = cx, cy
    sel_cursor[0], sel_cursor[1] = cx, cy
    refresh_cells()

def on_cell_enter(cx, cy):
    if drag_active[0]:
        sel_cursor[0], sel_cursor[1] = cx, cy
        refresh_cells()

def on_drag_motion(event):
    if not drag_active[0]:
        return
    hovered = root.winfo_containing(event.x_root, event.y_root)
    if hovered in widget_to_cell:
        cx, cy = widget_to_cell[hovered]
        if (sel_cursor[0], sel_cursor[1]) != (cx, cy):
            sel_cursor[0], sel_cursor[1] = cx, cy
            refresh_cells()

def on_cell_release(_cx, _cy):
    if drag_active[0]:
        drag_active[0] = False
        commit_rect()

for r in range(rows):
    for c in range(cols):
        cell = tk.Frame(
            grid_frame,
            width=cell_w,
            height=cell_h,
            bg=THEME_CELL_INACTIVE,
            highlightthickness=1,
            highlightbackground=THEME_CELL_BORDER,
        )
        cell.grid(row=r, column=c, padx=2, pady=2)
        cell.pack_propagate(False)
        cell.bind("<ButtonPress-1>", lambda _e, cx=c, cy=r: on_cell_press(cx, cy))
        cell.bind("<Enter>", lambda _e, cx=c, cy=r: on_cell_enter(cx, cy))
        cell.bind("<B1-Motion>", on_drag_motion)
        cell.bind("<ButtonRelease-1>", lambda _e, cx=c, cy=r: on_cell_release(cx, cy))
        cell_widgets[(c, r)] = cell
        widget_to_cell[cell] = (c, r)

preset_frame = tk.Frame(root, bg=THEME_BG, padx=14, pady=10)
preset_frame.pack(fill=tk.X)

for label in ["1/4 Col", "1/3 Col", "Center 1/2", "2x2 Stack", "Pack Left", "Pack Right"]:
    btn = tk.Button(
        preset_frame,
        text=label,
        bg=THEME_SURFACE,
        fg=THEME_TEXT,
        activebackground=THEME_ACCENT,
        activeforeground=THEME_BG,
        relief=tk.FLAT,
        padx=8,
        pady=4,
        command=lambda l=label: (print(f"PRESET:{l}", flush=True), root.destroy()),
    )
    btn.pack(side=tk.LEFT, padx=3)

tk.Label(
    root,
    text="Arrows: Move Cell  •  Shift+Arrows: Rubber-Band Span  •  Enter: Apply  •  Esc: Cancel",
    bg=THEME_BG,
    fg=THEME_SUBTEXT,
    font=("DejaVu Sans", 9),
    pady=6,
).pack()

def on_key(event):
    key = event.keysym
    shift_held = bool(event.state & 0x0001)
    dx, dy = 0, 0
    if key == "Left":
        dx = -1
    elif key == "Right":
        dx = 1
    elif key == "Up":
        dy = -1
    elif key == "Down":
        dy = 1
    elif key in ("Return", "KP_Enter", "space"):
        commit_rect()
        return
    elif key == "Escape":
        root.destroy()
        return
    else:
        return
    if shift_held:
        sel_cursor[0] = max(0, min(cols - 1, sel_cursor[0] + dx))
        sel_cursor[1] = max(0, min(rows - 1, sel_cursor[1] + dy))
    else:
        nx = max(0, min(cols - 1, sel_anchor[0] + dx))
        ny = max(0, min(rows - 1, sel_anchor[1] + dy))
        sel_anchor[0], sel_anchor[1] = nx, ny
        sel_cursor[0], sel_cursor[1] = nx, ny
    refresh_cells()

root.bind("<Key>", on_key)
root.bind("<B1-Motion>", on_drag_motion)
refresh_cells()
root.mainloop()
"##;

pub fn launch_gtile_overlay(
    profile: &GridProfile,
    store: &StateStore,
    anchor: &str,
    dry_run: bool,
) -> i32 {
    let (mut tiles, active_id) = store.load(profile, anchor, true);
    let active = match tiles.iter().find(|t| t.id == active_id).cloned() {
        Some(a) => a,
        None => return 1,
    };

    let has_display = env::var("DISPLAY").is_ok_and(|v| !v.is_empty())
        || env::var("WAYLAND_DISPLAY").is_ok_and(|v| !v.is_empty());

    if dry_run || !has_display {
        println!(
            "{{\"mode\": \"overlay\", \"theme\": {{\"background\": \"{}\", \"highlight\": \"{}\"}}, \"grid\": \"{}x{}\", \"resolution\": \"{}x{}\", \"presets\": [\"1/4 Col\", \"1/3 Col\", \"Center 1/2\", \"2x2 Stack\", \"Pack Left\", \"Pack Right\"], \"active_tile\": {}}}",
            THEME_BG,
            THEME_ACCENT,
            profile.cols,
            profile.rows,
            profile.width_px,
            profile.height_px,
            active.to_json()
        );
        return 0;
    }

    if command_exists("python3") {
        if let Ok(out) = Command::new("python3")
            .args([
                "-c",
                TK_OVERLAY_HELPER,
                &profile.cols.to_string(),
                &profile.rows.to_string(),
                &profile.width_px.to_string(),
                &profile.height_px.to_string(),
                &profile.aspect_label,
                &active.x.to_string(),
                &active.y.to_string(),
                &active.w.to_string(),
                &active.h.to_string(),
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .output()
        {
            if out.status.success() {
                let text = String::from_utf8_lossy(&out.stdout);
                for line in text.lines() {
                    if let Some(rest) = line.strip_prefix("RECT:") {
                        if let Ok(rect) = parse_preset_args(
                            &format!("{}x{}", profile.cols, profile.rows),
                            rest,
                            profile,
                        ) {
                            if let Ok(updated) = resolve_slippery_layout(
                                &tiles,
                                &active_id,
                                "preset",
                                None,
                                profile.cols,
                                profile.rows,
                                DEFAULT_MIN_SPAN,
                                Some(rect),
                            ) {
                                tiles = updated;
                                store.save(profile, &tiles, &active_id);
                                if let Some(act) = tiles.iter().find(|t| t.id == active_id) {
                                    apply_window_geometry(profile, act);
                                }
                            }
                        }
                        return 0;
                    } else if let Some(label) = line.strip_prefix("PRESET:") {
                        if label == "Pack Left" || label == "Pack Right" {
                            let pack_dir = if label == "Pack Left" {
                                "left"
                            } else {
                                "right"
                            };
                            if let Ok(updated) = resolve_slippery_layout(
                                &tiles,
                                &active_id,
                                "pack",
                                Some(pack_dir),
                                profile.cols,
                                profile.rows,
                                DEFAULT_MIN_SPAN,
                                None,
                            ) {
                                tiles = updated;
                                store.save(profile, &tiles, &active_id);
                                if let Some(act) = tiles.iter().find(|t| t.id == active_id) {
                                    apply_window_geometry(profile, act);
                                }
                            }
                        } else if let Ok(rect) =
                            compute_named_preset(label, profile.cols, profile.rows)
                        {
                            if let Ok(updated) = resolve_slippery_layout(
                                &tiles,
                                &active_id,
                                "preset",
                                None,
                                profile.cols,
                                profile.rows,
                                DEFAULT_MIN_SPAN,
                                Some(rect),
                            ) {
                                tiles = updated;
                                store.save(profile, &tiles, &active_id);
                                if let Some(act) = tiles.iter().find(|t| t.id == active_id) {
                                    apply_window_geometry(profile, act);
                                }
                            }
                        }
                        return 0;
                    }
                }
                return 0;
            }
        }
    }

    println!(
        "{{\"mode\": \"overlay-fallback\", \"grid\": \"{}x{}\", \"active_tile\": {}}}",
        profile.cols,
        profile.rows,
        active.to_json()
    );
    0
}

pub fn test_ultrawide_12x4_profile_and_spawning() -> Result<(), String> {
    let profile = build_grid_profile(5120, 1440);
    if (profile.cols, profile.rows) != (12, 4) {
        return Err(format!("Expected 12x4, got {}x{}", profile.cols, profile.rows));
    }
    if profile.col_width_px() != 426 {
        return Err(format!("Expected col_width_px=426, got {}", profile.col_width_px()));
    }
    if (profile.default_block_w, profile.default_block_h) != (3, 4) {
        return Err("Expected default block 3x4".to_string());
    }
    if (profile.alt_block_w, profile.alt_block_h) != (3, 2) {
        return Err("Expected alt block 3x2".to_string());
    }
    if profile.cell_to_pixel_rect(0, 0, 3, 4) != (0, 0, 1280, 1440) {
        return Err("Expected 3x4 rect to be (0,0,1280,1440)".to_string());
    }
    if profile.cell_to_pixel_rect(0, 0, 3, 2) != (0, 0, 1280, 720) {
        return Err("Expected 3x2 rect to be (0,0,1280,720)".to_string());
    }

    let (tiles, t1) = spawn_building_block(&[], "w1", &profile, "left", "default")?;
    if (t1.x, t1.y, t1.w, t1.h) != (0, 0, 3, 4) {
        return Err(format!("Expected w1 at (0,0,3,4), got {:?}", t1));
    }

    let (tiles, t2) = spawn_building_block(&tiles, "w2", &profile, "center", "default")?;
    if (t2.x, t2.y, t2.w, t2.h) != (4, 0, 3, 4) {
        return Err(format!("Expected w2 at (4,0,3,4), got {:?}", t2));
    }

    let (_tiles, t3) = spawn_building_block(&tiles, "w3", &profile, "right", "default")?;
    if (t3.x, t3.y, t3.w, t3.h) != (9, 0, 3, 4) {
        return Err(format!("Expected w3 at (9,0,3,4), got {:?}", t3));
    }
    Ok(())
}

pub fn test_standard_8x4_profile_and_spawning() -> Result<(), String> {
    let profile = build_grid_profile(3840, 2160);
    if (profile.cols, profile.rows) != (8, 4) {
        return Err(format!("Expected 8x4, got {}x{}", profile.cols, profile.rows));
    }
    if (profile.default_block_w, profile.default_block_h) != (4, 4) {
        return Err("Expected default block 4x4".to_string());
    }
    if (profile.alt_block_w, profile.alt_block_h) != (2, 4) {
        return Err("Expected alt block 2x4".to_string());
    }

    let (tiles, t1) = spawn_building_block(&[], "w1", &profile, "left", "default")?;
    if (t1.x, t1.y, t1.w, t1.h) != (0, 0, 4, 4) {
        return Err(format!("Expected w1 at (0,0,4,4), got {:?}", t1));
    }

    let (_tiles, t2) = spawn_building_block(&tiles, "w2", &profile, "right", "alt")?;
    if (t2.x, t2.y, t2.w, t2.h) != (6, 0, 2, 4) {
        return Err(format!("Expected w2 at (6,0,2,4), got {:?}", t2));
    }
    Ok(())
}

pub fn test_grow_collision_slip_and_edge_compression() -> Result<(), String> {
    let tiles = vec![
        Tile::new("a", 0, 0, 3, 4),
        Tile::new("b", 3, 0, 3, 4),
    ];
    let tiles = resolve_slippery_layout(&tiles, "a", "grow", Some("right"), 12, 4, 2, None)?;
    let by_id: HashMap<String, Tile> = tiles.into_iter().map(|t| (t.id.clone(), t)).collect();
    if (by_id["a"].x, by_id["a"].w) != (0, 4) {
        return Err("Expected a to grow to (0,4)".to_string());
    }
    if (by_id["b"].x, by_id["b"].w) != (4, 3) {
        return Err("Expected b to slip to (4,3)".to_string());
    }
    if by_id["a"].overlaps(&by_id["b"]) {
        return Err("a and b should not overlap".to_string());
    }

    let edge_tiles = vec![
        Tile::new("a", 6, 0, 3, 4),
        Tile::new("b", 9, 0, 3, 4),
    ];
    let edge_tiles =
        resolve_slippery_layout(&edge_tiles, "a", "grow", Some("right"), 12, 4, 2, None)?;
    let eb: HashMap<String, Tile> = edge_tiles.into_iter().map(|t| (t.id.clone(), t)).collect();
    if (eb["a"].x, eb["a"].w) != (6, 4) {
        return Err("Expected edge a to be (6,4)".to_string());
    }
    if (eb["b"].x, eb["b"].w) != (10, 2) {
        return Err("Expected edge b to compress to (10,2)".to_string());
    }
    if eb["a"].overlaps(&eb["b"]) {
        return Err("edge a and b should not overlap".to_string());
    }

    let chain_tiles = vec![
        Tile::new("a", 4, 0, 3, 4),
        Tile::new("b", 7, 0, 3, 4),
        Tile::new("c", 10, 0, 2, 4),
    ];
    let chain_tiles =
        resolve_slippery_layout(&chain_tiles, "a", "grow", Some("right"), 12, 4, 2, None)?;
    let cb: HashMap<String, Tile> = chain_tiles.into_iter().map(|t| (t.id.clone(), t)).collect();
    if (cb["a"].x, cb["a"].w) != (4, 4) {
        return Err("Expected chain a to be (4,4)".to_string());
    }
    if (cb["b"].x, cb["b"].w) != (8, 2) {
        return Err("Expected chain b to compress to (8,2)".to_string());
    }
    if (cb["c"].x, cb["c"].w) != (10, 2) {
        return Err("Expected chain c to remain at (10,2)".to_string());
    }
    Ok(())
}

pub fn test_shrink_and_neighbor_reclaim() -> Result<(), String> {
    let tiles = vec![
        Tile::new("a", 3, 0, 3, 4),
        Tile::new("b", 6, 0, 3, 4),
        Tile::new("c", 9, 0, 3, 4),
    ];
    let tiles = resolve_slippery_layout(&tiles, "a", "grow", Some("right"), 12, 4, 2, None)?;
    let by_id: HashMap<String, Tile> = tiles.iter().map(|t| (t.id.clone(), t.clone())).collect();
    if (by_id["a"].x, by_id["a"].w) != (3, 4)
        || (by_id["b"].x, by_id["b"].w) != (7, 3)
        || (by_id["c"].x, by_id["c"].w) != (10, 2)
    {
        return Err("Unexpected grow state before shrink".to_string());
    }

    let tiles = resolve_slippery_layout(&tiles, "a", "shrink", Some("right"), 12, 4, 2, None)?;
    let by_id: HashMap<String, Tile> = tiles.into_iter().map(|t| (t.id.clone(), t)).collect();
    if (by_id["a"].x, by_id["a"].w) != (3, 3) {
        return Err(format!("Expected a=(3,3), got ({},{})", by_id["a"].x, by_id["a"].w));
    }
    if (by_id["b"].x, by_id["b"].w) != (6, 3) {
        return Err(format!("Expected b=(6,3), got ({},{})", by_id["b"].x, by_id["b"].w));
    }
    if (by_id["c"].x, by_id["c"].w) != (9, 3) {
        return Err(format!("Expected c=(9,3), got ({},{})", by_id["c"].x, by_id["c"].w));
    }
    Ok(())
}

pub fn test_multi_row_cascading_collision_and_rollback() -> Result<(), String> {
    let tiles = vec![
        Tile::new("left_top", 0, 0, 4, 2),
        Tile::new("left_bot", 0, 2, 4, 2),
        Tile::new("active", 4, 0, 3, 4),
        Tile::new("mid_top", 7, 0, 3, 2),
        Tile::new("mid_bot", 7, 2, 3, 2),
        Tile::new("edge_top", 10, 0, 2, 2),
        Tile::new("edge_bot", 10, 2, 2, 2),
    ];
    let tiles =
        resolve_slippery_layout(&tiles, "active", "grow", Some("right"), 12, 4, 2, None)?;
    let by_id: HashMap<String, Tile> = tiles.iter().map(|t| (t.id.clone(), t.clone())).collect();
    if (by_id["active"].x, by_id["active"].w) != (4, 4)
        || (by_id["mid_top"].x, by_id["mid_top"].w) != (8, 2)
        || (by_id["mid_bot"].x, by_id["mid_bot"].w) != (8, 2)
        || (by_id["edge_top"].x, by_id["edge_top"].w) != (10, 2)
        || (by_id["edge_bot"].x, by_id["edge_bot"].w) != (10, 2)
    {
        return Err("Multi-row grow failed expected positions".to_string());
    }

    let snapshot_before: Vec<(String, i32, i32, i32, i32)> = tiles
        .iter()
        .map(|t| (t.id.clone(), t.x, t.y, t.w, t.h))
        .collect();
    let tiles_blocked =
        resolve_slippery_layout(&tiles, "active", "grow", Some("right"), 12, 4, 2, None)?;
    let snapshot_after: Vec<(String, i32, i32, i32, i32)> = tiles_blocked
        .iter()
        .map(|t| (t.id.clone(), t.x, t.y, t.w, t.h))
        .collect();
    if snapshot_after != snapshot_before {
        return Err("Expected blocked multi-row grow to roll back cleanly".to_string());
    }
    Ok(())
}

pub fn test_move_and_pack_invariants() -> Result<(), String> {
    let tiles = vec![
        Tile::new("a", 0, 0, 3, 4),
        Tile::new("b", 3, 0, 3, 4),
    ];
    let tiles = resolve_slippery_layout(&tiles, "a", "move", Some("right"), 12, 4, 2, None)?;
    let by_id: HashMap<String, Tile> = tiles.iter().map(|t| (t.id.clone(), t.clone())).collect();
    if by_id["a"].x <= 0 || by_id["a"].overlaps(&by_id["b"]) {
        return Err("Move right invariant failed".to_string());
    }

    let tiles = resolve_slippery_layout(&tiles, "a", "pack", Some("right"), 12, 4, 2, None)?;
    let by_id: HashMap<String, Tile> = tiles.iter().map(|t| (t.id.clone(), t.clone())).collect();
    if by_id["a"].x != 9 || by_id["a"].overlaps(&by_id["b"]) {
        return Err("Pack right invariant failed".to_string());
    }

    let tiles = resolve_slippery_layout(&tiles, "a", "pack", Some("left"), 12, 4, 2, None)?;
    let by_id: HashMap<String, Tile> = tiles.iter().map(|t| (t.id.clone(), t.clone())).collect();
    if by_id["a"].x != 0 || by_id["a"].overlaps(&by_id["b"]) {
        return Err("Pack left invariant failed".to_string());
    }

    // Regression check: 3-tile constrained 8x4 grid move & pack must never leave overlapping tiles
    let constrained = vec![
        Tile::new("a", 0, 0, 4, 2),
        Tile::new("d", 0, 2, 4, 2),
        Tile::new("b", 4, 0, 4, 4),
    ];
    let after_move =
        resolve_slippery_layout(&constrained, "a", "move", Some("right"), 8, 4, 2, None)?;
    if has_any_overlap(&after_move) {
        return Err(format!(
            "Constrained move right left overlapping tiles: {after_move:?}"
        ));
    }

    let after_pack =
        resolve_slippery_layout(&constrained, "b", "pack", Some("left"), 8, 4, 2, None)?;
    if has_any_overlap(&after_pack) {
        return Err(format!(
            "Constrained pack left left overlapping tiles: {after_pack:?}"
        ));
    }

    // Regression check: JSON state round-trip with key-colliding ("w", "cols") and special-character IDs
    let profile = build_grid_profile(5120, 1440);
    let tricky_tiles = vec![
        Tile::with_anchor("w", 0, 0, 3, 4, "left"),
        Tile::with_anchor("cols", 3, 0, 3, 4, "center"),
        Tile::with_anchor("win-{1}]\t\"escaped\"", 6, 0, 3, 4, "right"),
    ];
    let tiles_json: Vec<String> = tricky_tiles
        .iter()
        .map(|t| format!("    {}", t.to_json()))
        .collect();
    let payload = format!(
        "{{\n  \"width_px\": 5120,\n  \"height_px\": 1440,\n  \"cols\": 12,\n  \"rows\": 4,\n  \"aspect\": \"32:9\",\n  \"active_id\": \"w\",\n  \"tiles\": [\n{}\n  ]\n}}\n",
        tiles_json.join(",\n")
    );
    let (parsed_cols, parsed_rows, parsed_active, parsed_tiles) =
        parse_state_json(&payload).ok_or_else(|| "Failed parsing tricky state JSON".to_string())?;
    if (parsed_cols, parsed_rows) != (12, 4) || parsed_active != "w" {
        return Err("State JSON header mismatch".to_string());
    }
    if parsed_tiles != tricky_tiles {
        return Err(format!(
            "State JSON tile round-trip mismatch: expected {tricky_tiles:?}, got {parsed_tiles:?}"
        ));
    }
    let _ = profile;

    Ok(())
}

pub fn run_self_tests() -> i32 {
    let tests: [(&str, fn() -> Result<(), String>); 6] = [
        (
            "test_ultrawide_12x4_profile_and_spawning",
            test_ultrawide_12x4_profile_and_spawning,
        ),
        (
            "test_standard_8x4_profile_and_spawning",
            test_standard_8x4_profile_and_spawning,
        ),
        (
            "test_grow_collision_slip_and_edge_compression",
            test_grow_collision_slip_and_edge_compression,
        ),
        (
            "test_shrink_and_neighbor_reclaim",
            test_shrink_and_neighbor_reclaim,
        ),
        (
            "test_multi_row_cascading_collision_and_rollback",
            test_multi_row_cascading_collision_and_rollback,
        ),
        (
            "test_move_and_pack_invariants",
            test_move_and_pack_invariants,
        ),
    ];

    let mut failed = 0usize;
    for (name, func) in tests {
        match func() {
            Ok(()) => println!("{name} ... ok"),
            Err(e) => {
                println!("{name} ... FAIL: {e}");
                failed += 1;
            }
        }
    }

    if failed == 0 {
        println!("\nRan {} tests\n\nOK", tests.len());
        0
    } else {
        eprintln!("\nFAILED (failures={failed})");
        1
    }
}

fn print_help() {
    println!(
        "usage: novos-slippery-tiles [--self-test] [--resolution RES] [--anchor {{left,right,center}}] [--state-file PATH] [--dry-run] [COMMAND ...]\n\n\
         NovOS Slippery Tile Engine & gTile Grid Overlay Manager (12x4 / 8x4)\n\n\
         commands:\n  \
           move <left|right|up|down>\n  \
           grow <left|right|up|down>\n  \
           shrink <left|right|up|down>\n  \
           pack <left|right>\n  \
           preset <cols>x<rows> <x>,<y>,<w>,<h>\n  \
           spawn [--id ID] [--variant default|alt]\n  \
           overlay\n  \
           daemon\n  \
           status"
    );
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let invoked_name = Path::new(args.first().map(|s| s.as_str()).unwrap_or("novos-slippery-tiles"))
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("novos-slippery-tiles")
        .to_string();

    let mut self_test = false;
    let mut resolution: Option<String> = None;
    let mut anchor = "left".to_string();
    let mut state_file: Option<PathBuf> = None;
    let mut dry_run = false;

    let mut cmd: Option<String> = None;
    let mut cmd_args: Vec<String> = Vec::new();

    let mut i = 1usize;
    while i < args.len() {
        let arg = &args[i];
        match arg.as_str() {
            "-h" | "--help" => {
                print_help();
                std::process::exit(0);
            }
            "--self-test" => {
                self_test = true;
            }
            "--dry-run" => {
                dry_run = true;
            }
            "--resolution" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("error: --resolution requires a value");
                    std::process::exit(2);
                }
                resolution = Some(args[i].clone());
            }
            "--anchor" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("error: --anchor requires a value");
                    std::process::exit(2);
                }
                anchor = args[i].clone();
            }
            "--state-file" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("error: --state-file requires a path");
                    std::process::exit(2);
                }
                state_file = Some(PathBuf::from(&args[i]));
            }
            s if s.starts_with("--resolution=") => {
                resolution = Some(s["--resolution=".len()..].to_string());
            }
            s if s.starts_with("--anchor=") => {
                anchor = s["--anchor=".len()..].to_string();
            }
            s if s.starts_with("--state-file=") => {
                state_file = Some(PathBuf::from(&s["--state-file=".len()..]));
            }
            _ => {
                if cmd.is_none() {
                    cmd = Some(arg.clone());
                } else {
                    cmd_args.push(arg.clone());
                }
            }
        }
        i += 1;
    }

    if self_test {
        std::process::exit(run_self_tests());
    }

    if !VALID_ANCHORS.contains(&anchor.as_str()) {
        eprintln!("error: invalid anchor {anchor:?}; expected left, right, or center");
        std::process::exit(2);
    }

    let command_str = cmd.unwrap_or_else(|| {
        if invoked_name.contains("gtile-overlay") {
            "overlay".to_string()
        } else {
            "status".to_string()
        }
    });

    let (res_w, res_h) = detect_active_resolution(resolution.as_deref());
    let profile = build_grid_profile(res_w, res_h);
    let store = StateStore::new(state_file);

    if command_str == "overlay" {
        std::process::exit(launch_gtile_overlay(&profile, &store, &anchor, dry_run));
    }

    let (mut tiles, mut active_id) = store.load(&profile, &anchor, command_str != "spawn");

    if command_str == "status" || command_str == "daemon" {
        store.save(&profile, &tiles, &active_id);
        let active = tiles
            .iter()
            .find(|t| t.id == active_id)
            .unwrap_or(&tiles[0]);
        let (px_x, px_y, px_w, px_h) =
            profile.cell_to_pixel_rect(active.x, active.y, active.w, active.h);
        let tiles_json: Vec<String> = tiles.iter().map(|t| t.to_json()).collect();
        println!(
            "{{\"grid\": \"{}x{}\", \"aspect\": \"{}\", \"resolution\": \"{}x{}\", \"col_width_px\": {}, \"active_id\": \"{}\", \"active_pixel_rect\": [{}, {}, {}, {}], \"tiles\": [{}]}}",
            profile.cols,
            profile.rows,
            escape_json(&profile.aspect_label),
            profile.width_px,
            profile.height_px,
            profile.col_width_px(),
            escape_json(&active_id),
            px_x,
            px_y,
            px_w,
            px_h,
            tiles_json.join(", ")
        );
        std::process::exit(0);
    }

    if command_str == "spawn" {
        let mut custom_id: Option<String> = None;
        let mut variant = "default".to_string();
        let mut j = 0usize;
        while j < cmd_args.len() {
            match cmd_args[j].as_str() {
                "--id" => {
                    j += 1;
                    if j >= cmd_args.len() {
                        eprintln!("error: --id requires a value");
                        std::process::exit(2);
                    }
                    custom_id = Some(cmd_args[j].clone());
                }
                "--variant" => {
                    j += 1;
                    if j >= cmd_args.len() {
                        eprintln!("error: --variant requires a value");
                        std::process::exit(2);
                    }
                    variant = cmd_args[j].clone();
                }
                s if s.starts_with("--id=") => {
                    custom_id = Some(s["--id=".len()..].to_string());
                }
                s if s.starts_with("--variant=") => {
                    variant = s["--variant=".len()..].to_string();
                }
                other => {
                    eprintln!("error: unrecognized spawn argument {other:?}");
                    std::process::exit(2);
                }
            }
            j += 1;
        }
        let existing_ids: HashSet<String> = tiles.iter().map(|t| t.id.clone()).collect();
        let new_id = custom_id.unwrap_or_else(|| {
            let mut idx = tiles.len() + 1;
            while existing_ids.contains(&format!("win-{idx}")) {
                idx += 1;
            }
            format!("win-{idx}")
        });
        match spawn_building_block(&tiles, &new_id, &profile, &anchor, &variant) {
            Ok((updated, spawned)) => {
                tiles = updated;
                active_id = spawned.id;
            }
            Err(e) => {
                eprintln!("error: {e}");
                std::process::exit(1);
            }
        }
    } else if matches!(command_str.as_str(), "move" | "grow" | "shrink" | "pack") {
        let dir = cmd_args.first().map(|s| s.as_str()).unwrap_or("");
        match resolve_slippery_layout(
            &tiles,
            &active_id,
            &command_str,
            Some(dir),
            profile.cols,
            profile.rows,
            DEFAULT_MIN_SPAN,
            None,
        ) {
            Ok(updated) => tiles = updated,
            Err(e) => {
                eprintln!("error: {e}");
                std::process::exit(2);
            }
        }
    } else if command_str == "preset" {
        if cmd_args.len() < 2 {
            eprintln!("error: preset requires <cols>x<rows> <x>,<y>,<w>,<h>");
            std::process::exit(2);
        }
        let rect = match parse_preset_args(&cmd_args[0], &cmd_args[1], &profile) {
            Ok(r) => r,
            Err(e) => {
                eprintln!("error: {e}");
                std::process::exit(2);
            }
        };
        match resolve_slippery_layout(
            &tiles,
            &active_id,
            "preset",
            None,
            profile.cols,
            profile.rows,
            DEFAULT_MIN_SPAN,
            Some(rect),
        ) {
            Ok(updated) => tiles = updated,
            Err(e) => {
                eprintln!("error: {e}");
                std::process::exit(2);
            }
        }
    } else {
        eprintln!("error: unknown command {command_str:?}");
        std::process::exit(2);
    }

    store.save(&profile, &tiles, &active_id);
    let active = tiles
        .iter()
        .find(|t| t.id == active_id)
        .unwrap_or(&tiles[0]);
    let (px_x, px_y, px_w, px_h) = if dry_run {
        profile.cell_to_pixel_rect(active.x, active.y, active.w, active.h)
    } else {
        apply_window_geometry(&profile, active)
    };

    println!(
        "{{\"command\": \"{}\", \"grid\": \"{}x{}\", \"active_tile\": {}, \"pixel_rect\": [{}, {}, {}, {}]}}",
        escape_json(&command_str),
        profile.cols,
        profile.rows,
        active.to_json(),
        px_x,
        px_y,
        px_w,
        px_h
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unit_ultrawide_12x4_profile_and_spawning() {
        test_ultrawide_12x4_profile_and_spawning().unwrap();
    }

    #[test]
    fn unit_standard_8x4_profile_and_spawning() {
        test_standard_8x4_profile_and_spawning().unwrap();
    }

    #[test]
    fn unit_grow_collision_slip_and_edge_compression() {
        test_grow_collision_slip_and_edge_compression().unwrap();
    }

    #[test]
    fn unit_shrink_and_neighbor_reclaim() {
        test_shrink_and_neighbor_reclaim().unwrap();
    }

    #[test]
    fn unit_multi_row_cascading_collision_and_rollback() {
        test_multi_row_cascading_collision_and_rollback().unwrap();
    }

    #[test]
    fn unit_move_and_pack_invariants() {
        test_move_and_pack_invariants().unwrap();
    }
}
