//! NovOS Declarative Custom Tools & GitHub Component Builder
//! =========================================================
//! Parses and validates `custom-tools/manifest.yaml` and builds/installs tools from:
//!   - `local`: Local repository path (with optional GitHub fallback repo)
//!   - `github_release`: Prebuilt GitHub Release assets (.deb, .AppImage, .tar.gz, binary)
//!   - `github_source`: Source repositories built via `cargo build --release`,
//!     `uv tool install`, or `make`.
//!   - `tarball`: A pinned upstream tarball (https `url` + `sha256`), verified before it is
//!     extracted into `install_dir`, with `install_target` symlinked to `binary`.
//!
//! `novos-custom-tools packages <validate|list|render-netinstall|check-apt>`
//! operates on the unified package manifest `config/packages.yaml` (see `packages.rs`).

mod packages;

use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;

pub const VALID_TOOL_TYPES: [&str; 4] = ["local", "github_release", "github_source", "tarball"];
pub const VALID_ASSET_FORMATS: [&str; 7] = [
    "deb",
    ".deb",
    "appimage",
    ".appimage",
    "tar.gz",
    ".tar.gz",
    "binary",
];
pub const VALID_BUILD_SYSTEMS: [&str; 3] = ["cargo", "uv", "make"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum YamlScalar {
    Str(String),
    Bool(bool),
    Int(i64),
    Null,
}

impl YamlScalar {
    pub fn as_str(&self) -> Option<&str> {
        match self {
            YamlScalar::Str(s) => Some(s.as_str()),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            YamlScalar::Bool(b) => Some(*b),
            _ => None,
        }
    }

    pub fn as_int(&self) -> Option<i64> {
        match self {
            YamlScalar::Int(i) => Some(*i),
            _ => None,
        }
    }

    pub fn to_string_value(&self) -> String {
        match self {
            YamlScalar::Str(s) => s.clone(),
            YamlScalar::Bool(b) => b.to_string(),
            YamlScalar::Int(i) => i.to_string(),
            YamlScalar::Null => String::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ToolFieldValue {
    Scalar(YamlScalar),
    List(Vec<YamlScalar>),
}

impl ToolFieldValue {
    pub fn as_scalar(&self) -> Option<&YamlScalar> {
        match self {
            ToolFieldValue::Scalar(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        self.as_scalar()?.as_str()
    }

    pub fn as_bool(&self) -> Option<bool> {
        self.as_scalar()?.as_bool()
    }

    pub fn as_list(&self) -> Option<&[YamlScalar]> {
        match self {
            ToolFieldValue::List(l) => Some(l.as_slice()),
            _ => None,
        }
    }
}

pub type ToolEntry = HashMap<String, ToolFieldValue>;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ManifestData {
    pub scalars: HashMap<String, YamlScalar>,
    pub tools: Vec<ToolEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionPlan {
    pub name: String,
    pub tool_type: String,
    pub install_target: String,
    pub source_exists: Option<bool>,
    pub source_path: Option<String>,
    pub api_url: Option<String>,
    pub asset_format: Option<String>,
    pub asset_pattern: Option<String>,
    pub repo: Option<String>,
    pub branch: Option<String>,
    pub build_system: Option<String>,
    pub build_command: Option<String>,
    pub url: Option<String>,
    pub sha256: Option<String>,
    pub steps: Vec<String>,
    pub status: Option<String>,
}

impl ExecutionPlan {
    pub fn to_json(&self, indent_spaces: usize) -> String {
        let pad = " ".repeat(indent_spaces);
        let inner = " ".repeat(indent_spaces + 2);
        let step_pad = " ".repeat(indent_spaces + 4);
        let mut fields = Vec::new();
        fields.push(format!("{inner}\"name\": \"{}\"", escape_json(&self.name)));
        fields.push(format!(
            "{inner}\"type\": \"{}\"",
            escape_json(&self.tool_type)
        ));
        if let Some(se) = self.source_exists {
            fields.push(format!("{inner}\"source_exists\": {se}"));
        }
        if let Some(ref sp) = self.source_path {
            fields.push(format!("{inner}\"source_path\": \"{}\"", escape_json(sp)));
        }
        if let Some(ref url) = self.api_url {
            fields.push(format!("{inner}\"api_url\": \"{}\"", escape_json(url)));
        }
        if let Some(ref fmt) = self.asset_format {
            fields.push(format!("{inner}\"asset_format\": \"{}\"", escape_json(fmt)));
        }
        if let Some(ref pat) = self.asset_pattern {
            fields.push(format!(
                "{inner}\"asset_pattern\": \"{}\"",
                escape_json(pat)
            ));
        }
        if let Some(ref repo) = self.repo {
            fields.push(format!("{inner}\"repo\": \"{}\"", escape_json(repo)));
        }
        if let Some(ref branch) = self.branch {
            fields.push(format!("{inner}\"branch\": \"{}\"", escape_json(branch)));
        }
        if let Some(ref bsys) = self.build_system {
            fields.push(format!(
                "{inner}\"build_system\": \"{}\"",
                escape_json(bsys)
            ));
        }
        if let Some(ref bcmd) = self.build_command {
            fields.push(format!(
                "{inner}\"build_command\": \"{}\"",
                escape_json(bcmd)
            ));
        }
        if let Some(ref url) = self.url {
            fields.push(format!("{inner}\"url\": \"{}\"", escape_json(url)));
        }
        if let Some(ref sha) = self.sha256 {
            fields.push(format!("{inner}\"sha256\": \"{}\"", escape_json(sha)));
        }
        fields.push(format!(
            "{inner}\"install_target\": \"{}\"",
            escape_json(&self.install_target)
        ));
        let steps_formatted: Vec<String> = self
            .steps
            .iter()
            .map(|s| format!("{step_pad}\"{}\"", escape_json(s)))
            .collect();
        fields.push(format!(
            "{inner}\"steps\": [\n{}\n{inner}]",
            steps_formatted.join(",\n")
        ));
        if let Some(ref st) = self.status {
            fields.push(format!("{inner}\"status\": \"{}\"", escape_json(st)));
        }
        format!("{pad}{{\n{}\n{pad}}}", fields.join(",\n"))
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

pub(crate) fn strip_yaml_comment(line: &str) -> &str {
    let mut in_single = false;
    let mut in_double = false;
    let mut escape = false;
    for (idx, ch) in line.char_indices() {
        if in_double {
            if escape {
                escape = false;
            } else if ch == '\\' {
                escape = true;
            } else if ch == '"' {
                in_double = false;
            }
            continue;
        }
        if in_single {
            if ch == '\'' {
                in_single = false;
            }
            continue;
        }
        match ch {
            '"' => in_double = true,
            '\'' => in_single = true,
            '#' if idx == 0 || line.as_bytes()[idx - 1].is_ascii_whitespace() => {
                return &line[..idx];
            }
            _ => {}
        }
    }
    line
}

pub fn parse_scalar(val: &str) -> YamlScalar {
    let val = strip_yaml_comment(val).trim();
    if val.is_empty() {
        return YamlScalar::Str(String::new());
    }
    if (val.starts_with('"') && val.ends_with('"') && val.len() >= 2)
        || (val.starts_with('\'') && val.ends_with('\'') && val.len() >= 2)
    {
        let inner = &val[1..val.len() - 1];
        let unescaped = inner.replace("\\\\", "\\").replace("\\\"", "\"");
        return YamlScalar::Str(unescaped);
    }
    let low = val.to_ascii_lowercase();
    if low == "true" {
        return YamlScalar::Bool(true);
    }
    if low == "false" {
        return YamlScalar::Bool(false);
    }
    if matches!(low.as_str(), "null" | "none" | "~") {
        return YamlScalar::Null;
    }
    if let Ok(i) = val.parse::<i64>() {
        return YamlScalar::Int(i);
    }
    YamlScalar::Str(val.to_string())
}

fn parse_inline_list(val: &str) -> Option<Vec<YamlScalar>> {
    let trimmed = strip_yaml_comment(val).trim();
    let inner = trimmed.strip_prefix('[')?.strip_suffix(']')?.trim();
    if inner.is_empty() {
        return Some(Vec::new());
    }
    let mut items = Vec::new();
    let mut current = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut escape = false;
    for ch in inner.chars() {
        if in_double {
            current.push(ch);
            if escape {
                escape = false;
            } else if ch == '\\' {
                escape = true;
            } else if ch == '"' {
                in_double = false;
            }
            continue;
        }
        if in_single {
            current.push(ch);
            if ch == '\'' {
                in_single = false;
            }
            continue;
        }
        match ch {
            '"' => {
                in_double = true;
                current.push(ch);
            }
            '\'' => {
                in_single = true;
                current.push(ch);
            }
            ',' => {
                items.push(parse_scalar(&current));
                current.clear();
            }
            _ => current.push(ch),
        }
    }
    if !current.trim().is_empty() {
        items.push(parse_scalar(&current));
    }
    Some(items)
}

pub fn fallback_yaml_load(text: &str) -> ManifestData {
    let mut root = ManifestData::default();
    let mut current_list_key: Option<String> = None;
    let mut list_item_indent: Option<usize> = None;
    let mut current_sublist_key: Option<String> = None;

    for raw_line in text.lines() {
        let uncommented = strip_yaml_comment(raw_line);
        let stripped = uncommented.trim();
        if stripped.is_empty() || stripped.starts_with('#') {
            continue;
        }
        let indent = uncommented.len() - uncommented.trim_start_matches(' ').len();

        if indent == 0 {
            current_sublist_key = None;
            list_item_indent = None;
            if let Some((k, v)) = stripped.split_once(':') {
                let k = k.trim().to_string();
                let v = v.trim();
                if v.is_empty() {
                    current_list_key = Some(k);
                } else {
                    root.scalars.insert(k, parse_scalar(v));
                    current_list_key = None;
                }
            }
            continue;
        }

        if current_list_key.as_deref() == Some("tools") && stripped.starts_with("- ") {
            let item_body = stripped[2..].trim();
            let has_current_item = !root.tools.is_empty();
            let is_sublist_scalar = has_current_item
                && current_sublist_key.is_some()
                && (list_item_indent.is_some_and(|li| indent > li)
                    || !item_body.contains(':')
                    || item_body.starts_with('"')
                    || item_body.starts_with('\''));

            if !is_sublist_scalar {
                if list_item_indent.is_none() {
                    list_item_indent = Some(indent);
                }
                current_sublist_key = None;
                let mut new_item = ToolEntry::new();
                if let Some((k, v)) = item_body.split_once(':') {
                    let k = k.trim().to_string();
                    let v = v.trim();
                    if v.is_empty() {
                        new_item.insert(k.clone(), ToolFieldValue::List(Vec::new()));
                        current_sublist_key = Some(k);
                    } else if let Some(inline_list) = parse_inline_list(v) {
                        new_item.insert(k, ToolFieldValue::List(inline_list));
                    } else {
                        new_item.insert(k, ToolFieldValue::Scalar(parse_scalar(v)));
                    }
                }
                root.tools.push(new_item);
                continue;
            }
        }

        if let Some(current_item) = root.tools.last_mut() {
            if let Some(rest) = stripped.strip_prefix("- ") {
                if let Some(ref sub_k) = current_sublist_key {
                    let val = parse_scalar(rest.trim());
                    if let Some(ToolFieldValue::List(list)) = current_item.get_mut(sub_k) {
                        list.push(val);
                    }
                    continue;
                }
            }
            if let Some((k, v)) = stripped.split_once(':') {
                let k = k.trim().to_string();
                let v = v.trim();
                if v.is_empty() {
                    current_item.insert(k.clone(), ToolFieldValue::List(Vec::new()));
                    current_sublist_key = Some(k);
                } else if let Some(inline_list) = parse_inline_list(v) {
                    current_item.insert(k, ToolFieldValue::List(inline_list));
                    current_sublist_key = None;
                } else {
                    current_item.insert(k, ToolFieldValue::Scalar(parse_scalar(v)));
                    current_sublist_key = None;
                }
            }
        }
    }

    root
}

pub fn load_manifest_text(text: &str) -> ManifestData {
    fallback_yaml_load(text)
}

pub fn load_manifest(path: &Path) -> Result<ManifestData, String> {
    if !path.is_file() {
        return Err(format!("Manifest file not found: {}", path.display()));
    }
    let text = fs::read_to_string(path).map_err(|e| e.to_string())?;
    Ok(load_manifest_text(&text))
}

pub fn validate_manifest_data(data: &ManifestData) -> (bool, Vec<String>) {
    let mut errors = Vec::new();

    match data.scalars.get("schema_version").and_then(|v| v.as_int()) {
        Some(1) => {}
        other => errors.push(format!(
            "Unsupported or missing schema_version: {other:?} (expected 1)"
        )),
    }

    if data.tools.is_empty() {
        errors.push("Manifest must define a non-empty 'tools' list".to_string());
        return (false, errors);
    }

    let mut seen_names = HashSet::new();
    for (idx, entry) in data.tools.iter().enumerate() {
        let name = match entry.get("name").and_then(|v| v.as_str()) {
            Some(s) if !s.trim().is_empty() => s.to_string(),
            _ => {
                errors.push(format!("Tool entry #{idx} is missing a valid 'name'"));
                continue;
            }
        };

        if !seen_names.insert(name.clone()) {
            errors.push(format!("Duplicate tool name: {name:?}"));
        }

        if entry.get("enabled").and_then(|v| v.as_bool()).is_none() {
            errors.push(format!("Tool {name:?}: 'enabled' must be a boolean"));
        }

        let ttype = match entry.get("type").and_then(|v| v.as_str()) {
            Some(t) if VALID_TOOL_TYPES.contains(&t) => t,
            other => {
                errors.push(format!(
                    "Tool {name:?}: invalid type {other:?}; expected one of {VALID_TOOL_TYPES:?}"
                ));
                continue;
            }
        };

        match entry.get("install_target").and_then(|v| v.as_str()) {
            Some(target) if target.starts_with('/') => {}
            other => {
                errors.push(format!(
                    "Tool {name:?}: 'install_target' must be an absolute path, got {other:?}"
                ));
            }
        }

        if ttype == "local" {
            match entry.get("source_path").and_then(|v| v.as_str()) {
                Some(src) if !src.trim().is_empty() => {}
                _ => errors.push(format!("Tool {name:?} (local): 'source_path' is required")),
            }
            if let Some(symlinks_val) = entry.get("symlinks") {
                let valid_links = symlinks_val.as_list().is_some_and(|list| {
                    list.iter()
                        .all(|item| item.as_str().is_some_and(|s| s.starts_with('/')))
                });
                if !valid_links {
                    errors.push(format!(
                        "Tool {name:?} (local): 'symlinks' must be a list of absolute paths"
                    ));
                }
            }
        } else if ttype == "github_release" {
            match entry.get("repo").and_then(|v| v.as_str()) {
                Some(r) if r.contains('/') => {}
                _ => errors.push(format!(
                    "Tool {name:?} (github_release): 'repo' must be 'owner/repo' or GitHub URL"
                )),
            }
            let fmt = entry
                .get("asset_format")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if !VALID_ASSET_FORMATS.contains(&fmt.as_str()) {
                errors.push(format!(
                    "Tool {name:?} (github_release): 'asset_format' must be one of {VALID_ASSET_FORMATS:?}"
                ));
            }
        } else if ttype == "github_source" {
            match entry.get("repo").and_then(|v| v.as_str()) {
                Some(r) if r.contains('/') => {}
                _ => errors.push(format!("Tool {name:?} (github_source): 'repo' is required")),
            }
            let bsys = entry
                .get("build_system")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if !VALID_BUILD_SYSTEMS.contains(&bsys) {
                errors.push(format!(
                    "Tool {name:?} (github_source): 'build_system' must be one of {VALID_BUILD_SYSTEMS:?}"
                ));
            }
        } else if ttype == "tarball" {
            errors.extend(validate_tarball_entry(&name, entry));
        }
    }

    (errors.is_empty(), errors)
}

/// True for exactly 64 lowercase hex characters (a sha256 digest as `sha256sum` prints it).
pub fn is_sha256_hex(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// True for a non-empty relative path with no `..`, `.` or empty components.
fn is_clean_relative_path(s: &str) -> bool {
    !s.is_empty()
        && !s.starts_with('/')
        && s.split('/').all(|c| !c.is_empty() && c != "." && c != "..")
}

fn validate_tarball_entry(name: &str, entry: &ToolEntry) -> Vec<String> {
    let mut errors = Vec::new();
    let field = |k: &str| entry.get(k).and_then(|v| v.as_str());

    match field("version") {
        Some(v) if !v.trim().is_empty() => {}
        _ => errors.push(format!("Tool {name:?} (tarball): 'version' is required")),
    }
    match field("url") {
        Some(u) if u.starts_with("https://") && u.len() > "https://".len() => {}
        other => errors.push(format!(
            "Tool {name:?} (tarball): 'url' must be an https:// URL, got {other:?}"
        )),
    }
    match field("sha256") {
        Some(h) if is_sha256_hex(h) => {}
        other => errors.push(format!(
            "Tool {name:?} (tarball): 'sha256' must be exactly 64 lowercase hex characters, got {other:?}"
        )),
    }
    match field("install_dir") {
        Some(d) if d.starts_with('/') && d != "/" && is_clean_relative_path(&d[1..]) => {}
        other => errors.push(format!(
            "Tool {name:?} (tarball): 'install_dir' must be an absolute path below /, got {other:?}"
        )),
    }
    match field("binary") {
        Some(b) if is_clean_relative_path(b) => {}
        other => errors.push(format!(
            "Tool {name:?} (tarball): 'binary' must be a relative path inside the extracted tree, got {other:?}"
        )),
    }
    errors
}

pub fn build_execution_plan(entry: &ToolEntry, repo_root: &Path) -> Result<ExecutionPlan, String> {
    let name = entry
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Missing name".to_string())?
        .to_string();
    let ttype = entry
        .get("type")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Missing type".to_string())?
        .to_string();
    let install_target = entry
        .get("install_target")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Missing install_target".to_string())?
        .to_string();

    if ttype == "local" {
        let src_rel = entry
            .get("source_path")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "Missing source_path".to_string())?;
        let joined = repo_root.join(src_rel);
        let src_abs = fs::canonicalize(&joined).unwrap_or(joined);
        let mut steps = Vec::new();
        if let Some(bcmd) = entry.get("build_command").and_then(|v| v.as_str()) {
            if !bcmd.trim().is_empty() {
                steps.push(bcmd.to_string());
            }
        }
        steps.push(format!(
            "install -m 0755 {} {}",
            src_abs.display(),
            install_target
        ));
        if let Some(links) = entry.get("symlinks").and_then(|v| v.as_list()) {
            for link in links {
                if let Some(ls) = link.as_str() {
                    steps.push(format!("ln -sfn {install_target} {ls}"));
                }
            }
        }
        return Ok(ExecutionPlan {
            name,
            tool_type: ttype,
            install_target,
            source_exists: Some(src_abs.is_file()),
            source_path: Some(src_abs.to_string_lossy().to_string()),
            api_url: None,
            asset_format: None,
            asset_pattern: None,
            repo: None,
            branch: None,
            build_system: None,
            build_command: None,
            url: None,
            sha256: None,
            steps,
            status: None,
        });
    }

    if ttype == "github_release" {
        let raw_repo = entry
            .get("repo")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "Missing repo".to_string())?
            .trim()
            .trim_end_matches('/');
        let without_host = raw_repo
            .strip_prefix("https://github.com/")
            .or_else(|| raw_repo.strip_prefix("http://github.com/"))
            .unwrap_or(raw_repo);
        let repo = without_host
            .strip_suffix(".git")
            .unwrap_or(without_host)
            .trim_end_matches('/');
        let tag = entry
            .get("tag")
            .and_then(|v| v.as_str())
            .unwrap_or("latest");
        let fmt = entry
            .get("asset_format")
            .and_then(|v| v.as_str())
            .unwrap_or("binary")
            .trim_start_matches('.')
            .to_string();
        let pattern = entry
            .get("asset_pattern")
            .and_then(|v| v.as_str())
            .unwrap_or(".*")
            .to_string();
        let api_url = if tag == "latest" {
            format!("https://api.github.com/repos/{repo}/releases/latest")
        } else {
            format!("https://api.github.com/repos/{repo}/releases/tags/{tag}")
        };
        let install_cmd = match fmt.as_str() {
            "deb" => "dpkg -i <downloaded.deb>".to_string(),
            "appimage" => format!("install -m 0755 <downloaded.AppImage> {install_target}"),
            "tar.gz" => {
                format!("tar -xzf <downloaded.tar.gz> && install -m 0755 <binary> {install_target}")
            }
            _ => format!("install -m 0755 <downloaded.bin> {install_target}"),
        };
        return Ok(ExecutionPlan {
            name,
            tool_type: ttype,
            install_target,
            source_exists: None,
            source_path: None,
            api_url: Some(api_url.clone()),
            asset_format: Some(fmt),
            asset_pattern: Some(pattern),
            repo: None,
            branch: None,
            build_system: None,
            build_command: None,
            url: None,
            sha256: None,
            steps: vec![format!("fetch {api_url}"), install_cmd],
            status: None,
        });
    }

    if ttype == "github_source" {
        let repo = entry
            .get("repo")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "Missing repo".to_string())?
            .to_string();
        let branch = entry
            .get("branch")
            .and_then(|v| v.as_str())
            .unwrap_or("main")
            .to_string();
        let build_system = entry
            .get("build_system")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "Missing build_system".to_string())?
            .to_string();
        let default_cmd = match build_system.as_str() {
            "cargo" => "cargo build --release",
            "uv" => "uv tool install .",
            "make" => "make",
            _ => "make",
        };
        let build_cmd = entry
            .get("build_command")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or(default_cmd)
            .to_string();
        let steps = vec![
            format!("git clone --depth 1 -b {branch} {repo} <workdir>"),
            build_cmd.clone(),
            format!("install -m 0755 <artifact> {install_target}"),
        ];
        return Ok(ExecutionPlan {
            name,
            tool_type: ttype,
            install_target,
            source_exists: None,
            source_path: None,
            api_url: None,
            asset_format: None,
            asset_pattern: None,
            repo: Some(repo),
            branch: Some(branch),
            build_system: Some(build_system),
            build_command: Some(build_cmd),
            url: None,
            sha256: None,
            steps,
            status: None,
        });
    }

    if ttype == "tarball" {
        let field = |k: &str| {
            entry
                .get(k)
                .and_then(|v| v.as_str())
                .map(str::to_string)
                .ok_or_else(|| format!("Missing {k}"))
        };
        let url = field("url")?;
        let sha256 = field("sha256")?;
        let install_dir = field("install_dir")?;
        let binary = field("binary")?;
        let steps = vec![
            format!("curl -fsSL -o <download> {url}"),
            format!("sha256sum <download> == {sha256} (abort before extracting on mismatch)"),
            format!("tar -xf <download> --strip-components=1 -C {install_dir}"),
            format!("ln -s {install_dir}/{binary} {install_target} (replace symlink only)"),
        ];
        return Ok(ExecutionPlan {
            name,
            tool_type: ttype,
            install_target,
            source_exists: None,
            source_path: None,
            api_url: None,
            asset_format: None,
            asset_pattern: None,
            repo: None,
            branch: None,
            build_system: None,
            build_command: None,
            url: Some(url),
            sha256: Some(sha256),
            steps,
            status: None,
        });
    }

    Err(format!("Unsupported tool type {ttype:?}"))
}

fn collect_files_recursive(dir: &Path, out: &mut Vec<PathBuf>) {
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_dir() {
                collect_files_recursive(&p, out);
            } else if p.is_file() {
                out.push(p);
            }
        }
    }
}

pub fn select_archive_binary(
    extract_dir: &Path,
    target_name: &str,
    tool_name: &str,
) -> Result<PathBuf, String> {
    let mut files = Vec::new();
    collect_files_recursive(extract_dir, &mut files);
    if files.is_empty() {
        return Err("Archive contained no files".to_string());
    }

    let doc_suffixes = [
        ".md", ".txt", ".rst", ".1", ".json", ".yaml", ".yml", ".toml", ".lock",
    ];
    let doc_names = [
        "license",
        "copying",
        "readme",
        "changelog",
        "authors",
        "notice",
    ];

    for cand in &files {
        if let Some(fname) = cand.file_name().and_then(|n| n.to_str()) {
            if fname == target_name || fname == tool_name {
                return Ok(cand.clone());
            }
        }
    }

    let non_docs: Vec<PathBuf> = files
        .iter()
        .filter(|p| {
            let fname_low = p
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            let stem_low = p
                .file_stem()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            !doc_suffixes.iter().any(|s| fname_low.ends_with(s))
                && !doc_names.contains(&stem_low.as_str())
        })
        .cloned()
        .collect();

    for cand in &non_docs {
        if let Ok(meta) = fs::metadata(cand) {
            if meta.permissions().mode() & 0o111 != 0 {
                return Ok(cand.clone());
            }
        }
    }

    Ok(non_docs.first().unwrap_or(&files[0]).clone())
}

fn make_temp_dir(prefix: &str) -> Result<PathBuf, String> {
    let pid = std::process::id();
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let dir = env::temp_dir().join(format!("{prefix}{pid}-{ts}"));
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir)
}

/// Install `src` at `dst` with mode 0755, replacing `dst` atomically.
///
/// The bytes go to a temp file in `dst`'s directory, which is then renamed
/// over `dst`. rename(2) swaps the directory entry, so a `dst` that is
/// currently executing keeps running its old inode and no ETXTBSY occurs.
fn install_binary_mode_755(src: &Path, dst: &Path) -> Result<(), String> {
    let (s, d) = (src.display(), dst.display());
    let fail = |op: &str, e: std::io::Error| format!("Failed installing {s} to {d}: {op}: {e}");
    let parent = dst.parent().filter(|p| !p.as_os_str().is_empty());
    let parent = parent.unwrap_or(Path::new("."));
    let Some(name) = dst.file_name().map(|n| n.to_string_lossy()) else {
        return Err(format!("Failed installing {s} to {d}: no file name"));
    };
    let tmp = parent.join(format!(".{name}.novos-tmp.{}", std::process::id()));
    let result = (|| {
        fs::create_dir_all(parent).map_err(|e| fail("create parent directory", e))?;
        fs::copy(src, &tmp).map_err(|e| fail("copy to temp file", e))?;
        let mode = fs::Permissions::from_mode(0o755);
        fs::set_permissions(&tmp, mode).map_err(|e| fail("set mode 0755 on temp file", e))?;
        fs::rename(&tmp, dst).map_err(|e| fail("rename temp file over target", e))
    })();
    match result {
        Err(err) if tmp.exists() => match fs::remove_file(&tmp) {
            Ok(()) => Err(err),
            Err(e) => Err(format!(
                "{err}; also failed removing temp file {}: {e}",
                tmp.display()
            )),
        },
        other => other,
    }
}

/// Run `sha256sum` on `path` and fail unless the digest equals `expected`.
pub fn verify_sha256(path: &Path, expected: &str) -> Result<(), String> {
    let out = Command::new("sha256sum")
        .arg(path)
        .output()
        .map_err(|e| format!("Failed invoking sha256sum: {e}"))?;
    if !out.status.success() {
        return Err(format!("sha256sum failed for {}", path.display()));
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    let actual = stdout.split_whitespace().next().unwrap_or("");
    if actual != expected {
        return Err(format!(
            "sha256 mismatch for {}: expected {expected}, got {actual}",
            path.display()
        ));
    }
    Ok(())
}

/// Point `link` at `dest`. An existing symlink is replaced; any other existing
/// file type (regular file, directory) is left alone and reported as an error.
pub fn replace_symlink(dest: &Path, link: &Path) -> Result<(), String> {
    match fs::symlink_metadata(link) {
        Ok(meta) if meta.file_type().is_symlink() => {
            fs::remove_file(link)
                .map_err(|e| format!("Failed removing old symlink {}: {e}", link.display()))?;
        }
        Ok(_) => {
            return Err(format!(
                "Refusing to overwrite {}: it exists and is not a symlink",
                link.display()
            ));
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("Failed inspecting {}: {e}", link.display())),
    }
    if let Some(parent) = link.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    symlink(dest, link).map_err(|e| {
        format!(
            "Failed linking {} -> {}: {e}",
            link.display(),
            dest.display()
        )
    })
}

fn install_tarball(entry: &ToolEntry, plan: &ExecutionPlan) -> Result<(), String> {
    let url = plan.url.as_deref().ok_or("Missing url")?;
    let sha256 = plan.sha256.as_deref().ok_or("Missing sha256")?;
    let install_dir = entry
        .get("install_dir")
        .and_then(|v| v.as_str())
        .ok_or("Missing install_dir")?;
    let binary = entry
        .get("binary")
        .and_then(|v| v.as_str())
        .ok_or("Missing binary")?;

    let tmpd = make_temp_dir("novos-tarball-")?;
    let archive = tmpd.join("archive");
    let fetched = (|| {
        let st = Command::new("curl")
            .args(["-fsSL", "-o", &archive.to_string_lossy(), url])
            .status()
            .map_err(|e| format!("Failed invoking curl for {url}: {e}"))?;
        if !st.success() {
            return Err(format!("Failed downloading {url}"));
        }
        verify_sha256(&archive, sha256)?;
        fs::create_dir_all(install_dir)
            .map_err(|e| format!("Failed creating {install_dir}: {e}"))?;
        let st = Command::new("tar")
            .args([
                "-xf",
                &archive.to_string_lossy(),
                "--strip-components=1",
                "-C",
                install_dir,
            ])
            .status()
            .map_err(|e| format!("Failed invoking tar: {e}"))?;
        if !st.success() {
            return Err(format!("tar extraction of {url} into {install_dir} failed"));
        }
        Ok(())
    })();
    let _ = fs::remove_dir_all(&tmpd);
    fetched?;

    let bin_path = Path::new(install_dir).join(binary);
    if !bin_path.is_file() {
        return Err(format!("Extracted tree has no file {}", bin_path.display()));
    }
    replace_symlink(&bin_path, Path::new(&plan.install_target))
}

pub fn execute_tool_install(
    entry: &ToolEntry,
    repo_root: &Path,
    dry_run: bool,
) -> Result<ExecutionPlan, String> {
    let mut plan = build_execution_plan(entry, repo_root)?;
    if dry_run {
        plan.status = Some("dry-run".to_string());
        return Ok(plan);
    }

    let ttype = plan.tool_type.as_str();
    let target = PathBuf::from(&plan.install_target);

    if ttype == "local" {
        let mut src_abs = PathBuf::from(plan.source_path.as_deref().unwrap_or(""));
        if !src_abs.is_file() {
            if let Some(bcmd) = entry.get("build_command").and_then(|v| v.as_str()) {
                if !bcmd.trim().is_empty() {
                    let status = Command::new("sh")
                        .arg("-c")
                        .arg(bcmd)
                        .current_dir(repo_root)
                        .status()
                        .map_err(|e| e.to_string())?;
                    if !status.success() {
                        return Err(format!("Local build_command {bcmd:?} failed"));
                    }
                    plan.source_exists = Some(src_abs.is_file());
                }
            }
        }
        if !src_abs.is_file() {
            if let Some(fallback_repo) = entry.get("fallback_repo").and_then(|v| v.as_str()) {
                let tmpd = make_temp_dir("novos-custom-local-")?;
                let branch = entry
                    .get("fallback_branch")
                    .and_then(|v| v.as_str())
                    .unwrap_or("main");
                let status = Command::new("git")
                    .args([
                        "clone",
                        "--depth",
                        "1",
                        "-b",
                        branch,
                        fallback_repo,
                        &tmpd.to_string_lossy(),
                    ])
                    .status()
                    .map_err(|e| {
                        let _ = fs::remove_dir_all(&tmpd);
                        e.to_string()
                    })?;
                if !status.success() {
                    let _ = fs::remove_dir_all(&tmpd);
                    return Err(format!("git clone failed for {fallback_repo}"));
                }
                if let Some(bcmd) = entry.get("build_command").and_then(|v| v.as_str()) {
                    if !bcmd.trim().is_empty() {
                        let bstatus = Command::new("sh")
                            .arg("-c")
                            .arg(bcmd)
                            .current_dir(&tmpd)
                            .status()
                            .map_err(|e| {
                                let _ = fs::remove_dir_all(&tmpd);
                                e.to_string()
                            })?;
                        if !bstatus.success() {
                            let _ = fs::remove_dir_all(&tmpd);
                            return Err(format!("Fallback build_command {bcmd:?} failed"));
                        }
                    }
                }
                let rel = entry
                    .get("source_path")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                src_abs = tmpd.join(rel);
                let install_res = install_binary_mode_755(&src_abs, &target);
                let _ = fs::remove_dir_all(&tmpd);
                install_res?;
            } else {
                install_binary_mode_755(&src_abs, &target)?;
            }
        } else {
            install_binary_mode_755(&src_abs, &target)?;
        }

        if let Some(links) = entry.get("symlinks").and_then(|v| v.as_list()) {
            for link in links {
                if let Some(link_str) = link.as_str() {
                    let link_path = Path::new(link_str);
                    if let Some(p) = link_path.parent() {
                        let _ = fs::create_dir_all(p);
                    }
                    if link_path.exists() || link_path.symlink_metadata().is_ok() {
                        let _ = fs::remove_file(link_path);
                    }
                    symlink(&target, link_path).map_err(|e| e.to_string())?;
                }
            }
        }
        plan.status = Some("installed".to_string());
        return Ok(plan);
    }

    if ttype == "github_release" {
        let api_url = plan.api_url.clone().unwrap_or_default();
        let pattern = plan
            .asset_pattern
            .clone()
            .unwrap_or_else(|| ".*".to_string());
        let fmt = plan
            .asset_format
            .clone()
            .unwrap_or_else(|| "binary".to_string());

        let out = Command::new("curl")
            .args([
                "-fsSL",
                "-H",
                "User-Agent: NovOS-Custom-Tools/1.0",
                &api_url,
            ])
            .output()
            .map_err(|e| format!("Failed invoking curl for {api_url}: {e}"))?;
        if !out.status.success() {
            return Err(format!(
                "Failed fetching GitHub release metadata from {api_url}"
            ));
        }
        let body = String::from_utf8_lossy(&out.stdout);
        let (matched_name, matched_url) = match_release_asset(&body, &pattern)
            .ok_or_else(|| format!("No release asset matched {pattern:?}"))?;

        let tmpd = make_temp_dir("novos-gh-rel-")?;
        let dl_path = tmpd.join(&matched_name);
        let dl_status = Command::new("curl")
            .args([
                "-fsSL",
                "-L",
                "-o",
                &dl_path.to_string_lossy(),
                &matched_url,
            ])
            .status()
            .map_err(|e| e.to_string())?;
        if !dl_status.success() {
            let _ = fs::remove_dir_all(&tmpd);
            return Err(format!("Failed downloading release asset {matched_url}"));
        }

        if fmt == "deb" {
            let st = Command::new("dpkg")
                .args(["-i", &dl_path.to_string_lossy()])
                .status()
                .map_err(|e| e.to_string())?;
            if !st.success() {
                let _ = fs::remove_dir_all(&tmpd);
                return Err("dpkg -i failed".to_string());
            }
        } else if fmt == "appimage" || fmt == "binary" {
            install_binary_mode_755(&dl_path, &target)?;
        } else if fmt == "tar.gz" {
            let extract_dir = tmpd.join("extracted");
            fs::create_dir_all(&extract_dir).map_err(|e| e.to_string())?;
            let st = Command::new("tar")
                .args([
                    "-xzf",
                    &dl_path.to_string_lossy(),
                    "-C",
                    &extract_dir.to_string_lossy(),
                ])
                .status()
                .map_err(|e| e.to_string())?;
            if !st.success() {
                let _ = fs::remove_dir_all(&tmpd);
                return Err("tar extraction failed".to_string());
            }
            let target_fname = target
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(&plan.name);
            let chosen = select_archive_binary(&extract_dir, target_fname, &plan.name)?;
            install_binary_mode_755(&chosen, &target)?;
        }
        let _ = fs::remove_dir_all(&tmpd);
        plan.status = Some("installed".to_string());
        return Ok(plan);
    }

    if ttype == "github_source" {
        let tmpd = make_temp_dir("novos-gh-src-")?;
        let branch = plan.branch.as_deref().unwrap_or("main");
        let repo = plan.repo.as_deref().unwrap_or("");
        let bcmd = plan.build_command.as_deref().unwrap_or("make");

        let clone_st = Command::new("git")
            .args([
                "clone",
                "--depth",
                "1",
                "-b",
                branch,
                repo,
                &tmpd.to_string_lossy(),
            ])
            .status()
            .map_err(|e| e.to_string())?;
        if !clone_st.success() {
            let _ = fs::remove_dir_all(&tmpd);
            return Err(format!("git clone failed for {repo}"));
        }

        let build_st = Command::new("sh")
            .arg("-c")
            .arg(bcmd)
            .current_dir(&tmpd)
            .status()
            .map_err(|e| e.to_string())?;
        if !build_st.success() {
            let _ = fs::remove_dir_all(&tmpd);
            return Err(format!("Build command {bcmd:?} failed"));
        }

        if let Some(artifact_rel) = entry.get("artifact_path").and_then(|v| v.as_str()) {
            let art_path = tmpd.join(artifact_rel);
            if art_path.is_file() {
                install_binary_mode_755(&art_path, &target)?;
            }
        }
        let _ = fs::remove_dir_all(&tmpd);
        plan.status = Some("installed".to_string());
        return Ok(plan);
    }

    if ttype == "tarball" {
        install_tarball(entry, &plan)?;
        plan.status = Some("installed".to_string());
        return Ok(plan);
    }

    Err(format!("Unsupported type: {ttype:?}"))
}

#[derive(Debug, Clone)]
enum RegexAtom {
    Any,
    Literal(char),
    Digit,
    Word,
    Space,
    CharSet(Vec<char>, bool),
}

impl RegexAtom {
    fn matches(&self, c: char) -> bool {
        match self {
            RegexAtom::Any => true,
            RegexAtom::Literal(expected) => c == *expected,
            RegexAtom::Digit => c.is_ascii_digit(),
            RegexAtom::Word => c.is_ascii_alphanumeric() || c == '_',
            RegexAtom::Space => c.is_ascii_whitespace(),
            RegexAtom::CharSet(chars, negated) => {
                let found = chars.contains(&c);
                if *negated {
                    !found
                } else {
                    found
                }
            }
        }
    }
}

#[derive(Debug, Clone)]
enum Quantifier {
    Once,
    ZeroOrMore,
    OneOrMore,
    ZeroOrOne,
}

fn compile_simple_regex(pattern: &str) -> (bool, bool, Vec<(RegexAtom, Quantifier)>) {
    let anchored_start = pattern.starts_with('^');
    let anchored_end = pattern.ends_with('$') && !pattern.ends_with("\\$");
    let core = pattern.strip_prefix('^').unwrap_or(pattern);
    let core = if anchored_end {
        &core[..core.len() - 1]
    } else {
        core
    };

    let chars: Vec<char> = core.chars().collect();
    let mut tokens = Vec::new();
    let mut i = 0usize;
    while i < chars.len() {
        let atom = match chars[i] {
            '\\' if i + 1 < chars.len() => {
                i += 1;
                match chars[i] {
                    'd' => RegexAtom::Digit,
                    'w' => RegexAtom::Word,
                    's' => RegexAtom::Space,
                    other => RegexAtom::Literal(other),
                }
            }
            '.' => RegexAtom::Any,
            '[' => {
                i += 1;
                let mut negated = false;
                if i < chars.len() && chars[i] == '^' {
                    negated = true;
                    i += 1;
                }
                let mut set = Vec::new();
                while i < chars.len() && chars[i] != ']' {
                    if chars[i] == '\\' && i + 1 < chars.len() {
                        i += 1;
                        set.push(chars[i]);
                    } else if i + 2 < chars.len() && chars[i + 1] == '-' && chars[i + 2] != ']' {
                        let start_c = chars[i];
                        let end_c = chars[i + 2];
                        for ch in start_c..=end_c {
                            set.push(ch);
                        }
                        i += 2;
                    } else {
                        set.push(chars[i]);
                    }
                    i += 1;
                }
                RegexAtom::CharSet(set, negated)
            }
            c => RegexAtom::Literal(c),
        };
        i += 1;
        let quant = if i < chars.len() {
            match chars[i] {
                '*' => {
                    i += 1;
                    Quantifier::ZeroOrMore
                }
                '+' => {
                    i += 1;
                    Quantifier::OneOrMore
                }
                '?' => {
                    i += 1;
                    Quantifier::ZeroOrOne
                }
                _ => Quantifier::Once,
            }
        } else {
            Quantifier::Once
        };
        tokens.push((atom, quant));
    }
    (anchored_start, anchored_end, tokens)
}

pub fn regex_like_match(pattern: &str, text: &str) -> bool {
    let (anchored_start, anchored_end, tokens) = compile_simple_regex(pattern);
    let tchars: Vec<char> = text.chars().collect();

    fn match_from(
        tokens: &[(RegexAtom, Quantifier)],
        ti: usize,
        tchars: &[char],
        ci: usize,
        anchored_end: bool,
    ) -> bool {
        if ti == tokens.len() {
            return !anchored_end || ci == tchars.len();
        }
        let (ref atom, ref quant) = tokens[ti];
        match quant {
            Quantifier::Once => {
                ci < tchars.len()
                    && atom.matches(tchars[ci])
                    && match_from(tokens, ti + 1, tchars, ci + 1, anchored_end)
            }
            Quantifier::ZeroOrOne => {
                if ci < tchars.len()
                    && atom.matches(tchars[ci])
                    && match_from(tokens, ti + 1, tchars, ci + 1, anchored_end)
                {
                    return true;
                }
                match_from(tokens, ti + 1, tchars, ci, anchored_end)
            }
            Quantifier::ZeroOrMore => {
                let mut k = ci;
                while k < tchars.len() && atom.matches(tchars[k]) {
                    k += 1;
                }
                while k >= ci {
                    if match_from(tokens, ti + 1, tchars, k, anchored_end) {
                        return true;
                    }
                    if k == ci {
                        break;
                    }
                    k -= 1;
                }
                false
            }
            Quantifier::OneOrMore => {
                if ci >= tchars.len() || !atom.matches(tchars[ci]) {
                    return false;
                }
                let mut k = ci + 1;
                while k < tchars.len() && atom.matches(tchars[k]) {
                    k += 1;
                }
                while k > ci {
                    if match_from(tokens, ti + 1, tchars, k, anchored_end) {
                        return true;
                    }
                    k -= 1;
                }
                false
            }
        }
    }

    if anchored_start {
        match_from(&tokens, 0, &tchars, 0, anchored_end)
    } else {
        (0..=tchars.len()).any(|start| match_from(&tokens, 0, &tchars, start, anchored_end))
    }
}

fn match_release_asset(json_body: &str, pattern: &str) -> Option<(String, String)> {
    for chunk in json_body.split("\"browser_download_url\"") {
        if let Some(colon) = chunk.find(':') {
            let after = chunk[colon + 1..].trim_start();
            if let Some(rest) = after.strip_prefix('"') {
                if let Some(end_q) = rest.find('"') {
                    let url = &rest[..end_q];
                    let name = url.rsplit('/').next().unwrap_or("");
                    if !name.is_empty() && regex_like_match(pattern, name) {
                        return Some((name.to_string(), url.to_string()));
                    }
                }
            }
        }
    }
    None
}

pub fn resolve_default_manifest(explicit: Option<&Path>) -> PathBuf {
    if let Some(p) = explicit {
        if p.is_file() {
            return p.to_path_buf();
        }
    }

    let mut candidates = Vec::new();
    if let Ok(cwd) = env::current_dir() {
        candidates.push(cwd.join("custom-tools").join("manifest.yaml"));
    }
    if let Ok(exe) = env::current_exe() {
        let mut anc = exe.as_path();
        while let Some(parent) = anc.parent() {
            candidates.push(parent.join("custom-tools").join("manifest.yaml"));
            anc = parent;
        }
    }
    candidates.push(PathBuf::from(
        "/root/novos-build/custom-tools/manifest.yaml",
    ));
    candidates.push(PathBuf::from("/etc/novos/custom-tools/manifest.yaml"));
    candidates.push(PathBuf::from("/usr/share/novos/custom-tools/manifest.yaml"));

    for cand in &candidates {
        if cand.is_file() {
            return cand.clone();
        }
    }
    explicit
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| candidates[0].clone())
}

pub fn resolve_repo_root(manifest_path: &Path) -> PathBuf {
    if let Some(parent) = manifest_path.parent() {
        if parent.file_name().and_then(|n| n.to_str()) == Some("custom-tools") {
            if let Some(root) = parent.parent() {
                if root.join("Cargo.toml").is_file() || root.join("scripts").is_dir() {
                    return root.to_path_buf();
                }
            }
        }
    }
    if let Ok(cwd) = env::current_dir() {
        if cwd.join("custom-tools").is_dir() {
            return cwd;
        }
    }
    PathBuf::from("/root/novos-build")
}

pub fn test_default_manifest_validates() -> Result<(), String> {
    let manifest_path = resolve_default_manifest(None);
    let data = load_manifest(&manifest_path)?;
    let (ok, errors) = validate_manifest_data(&data);
    if !ok {
        return Err(format!("Validation failed with errors: {errors:?}"));
    }

    let mut by_name: HashMap<String, &ToolEntry> = HashMap::new();
    let mut types_present: HashSet<String> = HashSet::new();
    for t in &data.tools {
        if let Some(n) = t.get("name").and_then(|v| v.as_str()) {
            by_name.insert(n.to_string(), t);
        }
        if let Some(tp) = t.get("type").and_then(|v| v.as_str()) {
            types_present.insert(tp.to_string());
        }
    }

    let st = by_name
        .get("novos-custom-tools")
        .ok_or_else(|| "Missing novos-custom-tools".to_string())?;
    if st.get("enabled").and_then(|v| v.as_bool()) != Some(true) {
        return Err("Expected novos-custom-tools to be enabled".to_string());
    }
    if !by_name.contains_key("cosmic-comp-ultrawide-patch") {
        return Err("Missing cosmic-comp-ultrawide-patch".to_string());
    }
    for req_type in VALID_TOOL_TYPES {
        if !types_present.contains(req_type) {
            return Err(format!("Missing tool type {req_type}"));
        }
    }
    Ok(())
}

pub fn test_fallback_yaml_parser_parity() -> Result<(), String> {
    let manifest_path = resolve_default_manifest(None);
    let text = fs::read_to_string(&manifest_path).map_err(|e| e.to_string())?;
    let fallback_data = fallback_yaml_load(&text);
    let (ok, errors) = validate_manifest_data(&fallback_data);
    if !ok {
        return Err(format!("Fallback parser failed validation: {errors:?}"));
    }
    if fallback_data.tools.len() != 5 {
        return Err(format!(
            "Expected 5 tools, got {}",
            fallback_data.tools.len()
        ));
    }
    let symlinks = fallback_data.tools[0]
        .get("symlinks")
        .and_then(|v| v.as_list())
        .ok_or_else(|| "Missing symlinks on first tool".to_string())?;
    if symlinks.len() != 1 || symlinks[0].as_str() != Some("/usr/local/bin/novos-ct") {
        return Err(format!("Unexpected symlinks: {symlinks:?}"));
    }

    // Verify inline comments and flow-style arrays parse and validate cleanly
    let inline_yaml = r#"
schema_version: 1 # inline comment on version
tools:
  - name: inline-test # inline comment on name
    enabled: true # inline comment on bool
    type: local # inline comment on type
    source_path: "target/release/novos-custom-tools" # comment after quoted string with #hash inside
    install_target: "/usr/local/bin/novos-custom-tools"
    symlinks: ["/usr/local/bin/novos-ct", "/usr/local/bin/novos-custom"] # flow list
"#;
    let inline_data = fallback_yaml_load(inline_yaml);
    let (i_ok, i_errs) = validate_manifest_data(&inline_data);
    if !i_ok {
        return Err(format!(
            "Inline comment / flow list YAML failed validation: {i_errs:?}"
        ));
    }
    let flow_links = inline_data.tools[0]
        .get("symlinks")
        .and_then(|v| v.as_list())
        .ok_or_else(|| "Flow list symlinks failed to parse as list".to_string())?;
    if flow_links.len() != 2 {
        return Err(format!("Expected 2 flow list symlinks, got {flow_links:?}"));
    }
    Ok(())
}

pub fn test_execution_plans_for_all_tool_types() -> Result<(), String> {
    let manifest_path = resolve_default_manifest(None);
    let repo_root = resolve_repo_root(&manifest_path);
    let data = load_manifest(&manifest_path)?;
    for entry in &data.tools {
        let plan = build_execution_plan(entry, &repo_root)?;
        let expected_name = entry.get("name").and_then(|v| v.as_str()).unwrap_or("");
        if plan.name != expected_name {
            return Err(format!(
                "Plan name mismatch: {} != {}",
                plan.name, expected_name
            ));
        }
        if plan.steps.is_empty() {
            return Err(format!("Plan for {} has no steps", plan.name));
        }
    }

    // Verify github_release URL normalization (with and without .git suffix) and regex asset matching
    let mut url_entry = ToolEntry::new();
    url_entry.insert(
        "name".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str("gh-url-test".to_string())),
    );
    url_entry.insert(
        "type".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str("github_release".to_string())),
    );
    url_entry.insert(
        "repo".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str(
            "https://github.com/cosmic-utils/tweaks".to_string(),
        )),
    );
    url_entry.insert(
        "asset_format".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str("tar.gz".to_string())),
    );
    url_entry.insert(
        "asset_pattern".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str(".*x86_64.*\\.tar\\.gz$".to_string())),
    );
    url_entry.insert(
        "install_target".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str(
            "/usr/local/bin/cosmic-ext-tweaks".to_string(),
        )),
    );
    let url_plan = build_execution_plan(&url_entry, &repo_root)?;
    if url_plan.api_url.as_deref()
        != Some("https://api.github.com/repos/cosmic-utils/tweaks/releases/latest")
    {
        return Err(format!(
            "Unexpected api_url for full GitHub URL: {:?}",
            url_plan.api_url
        ));
    }

    let sample_gh_json = r#"{
      "assets": [
        {"browser_download_url": "https://github.com/cosmic-utils/tweaks/releases/download/v0.1.0/cosmic-ext-tweaks-0.1.0-aarch64-unknown-linux-gnu.tar.gz"},
        {"browser_download_url": "https://github.com/cosmic-utils/tweaks/releases/download/v0.1.0/cosmic-ext-tweaks-0.1.0-x86_64-unknown-linux-gnu.tar.gz"}
      ]
    }"#;
    let matched =
        match_release_asset(sample_gh_json, ".*x86_64.*\\.tar\\.gz$").ok_or_else(|| {
            "Expected .*x86_64.*\\.tar\\.gz$ to match x86_64 tar.gz asset".to_string()
        })?;
    if matched.0 != "cosmic-ext-tweaks-0.1.0-x86_64-unknown-linux-gnu.tar.gz" {
        return Err(format!("Unexpected matched asset: {matched:?}"));
    }

    Ok(())
}

pub fn test_invalid_manifest_rejected() -> Result<(), String> {
    let mut bad = ManifestData::default();
    bad.scalars
        .insert("schema_version".to_string(), YamlScalar::Int(1));
    let mut broken = ToolEntry::new();
    broken.insert(
        "name".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str("broken-tool".to_string())),
    );
    broken.insert(
        "enabled".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str("yes".to_string())),
    );
    broken.insert(
        "type".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str("unknown".to_string())),
    );
    broken.insert(
        "install_target".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Str("rel".to_string())),
    );
    bad.tools.push(broken);

    let (ok, errors) = validate_manifest_data(&bad);
    if ok || errors.len() < 2 {
        return Err(format!(
            "Expected invalid manifest to be rejected with >=2 errors, got ok={ok}, errors={errors:?}"
        ));
    }
    Ok(())
}

fn str_entry(pairs: &[(&str, &str)]) -> ToolEntry {
    let mut entry: ToolEntry = pairs
        .iter()
        .map(|(k, v)| {
            (
                k.to_string(),
                ToolFieldValue::Scalar(YamlScalar::Str(v.to_string())),
            )
        })
        .collect();
    entry.insert(
        "enabled".to_string(),
        ToolFieldValue::Scalar(YamlScalar::Bool(false)),
    );
    entry
}

pub fn test_tarball_validation_and_plan() -> Result<(), String> {
    const SHA: &str = "3581863e3cbcfd0fce0efc682ce7e9e93da96668a977791d356a26d2a7e9b17a";
    let good: [(&str, &str); 8] = [
        ("name", "tb"),
        ("type", "tarball"),
        ("version", "0.17.1"),
        (
            "url",
            "https://ziglang.org/download/0.17.1/zig-x86_64-linux-0.17.1.tar.xz",
        ),
        ("sha256", SHA),
        ("install_dir", "/opt/zig"),
        ("binary", "zig"),
        ("install_target", "/usr/local/bin/zig"),
    ];
    let manifest_with = |entry: ToolEntry| ManifestData {
        scalars: HashMap::from([("schema_version".to_string(), YamlScalar::Int(1))]),
        tools: vec![entry],
    };
    let with_override = |key: &str, val: Option<&str>| {
        let mut entry = str_entry(&good);
        match val {
            Some(v) => entry.insert(
                key.to_string(),
                ToolFieldValue::Scalar(YamlScalar::Str(v.to_string())),
            ),
            None => entry.remove(key),
        };
        entry
    };

    let (ok, errors) = validate_manifest_data(&manifest_with(str_entry(&good)));
    if !ok {
        return Err(format!("Well-formed tarball entry rejected: {errors:?}"));
    }

    let bad_cases: [(&str, Option<&str>); 9] = [
        ("url", Some("http://ziglang.org/zig.tar.xz")),
        ("sha256", Some(&SHA[..63])),
        (
            "sha256",
            Some("3581863E3CBCFD0FCE0EFC682CE7E9E93DA96668A977791D356A26D2A7E9B17A"),
        ),
        ("sha256", None),
        ("install_dir", Some("opt/zig")),
        ("install_dir", Some("/")),
        ("binary", None),
        ("binary", Some("../zig")),
        ("version", None),
    ];
    for (key, val) in bad_cases {
        let (ok, errors) = validate_manifest_data(&manifest_with(with_override(key, val)));
        if ok
            || !errors
                .iter()
                .any(|e| e.contains("(tarball)") && e.contains(key))
        {
            return Err(format!(
                "Expected tarball {key}={val:?} to be rejected, got ok={ok}, errors={errors:?}"
            ));
        }
    }

    let plan = execute_tool_install(&str_entry(&good), Path::new("/nonexistent"), true)?;
    if plan.status.as_deref() != Some("dry-run") || !plan.to_json(0).contains(SHA) {
        return Err(format!("Dry-run plan lacks sha256: {}", plan.to_json(0)));
    }
    if !plan
        .steps
        .iter()
        .any(|s| s.contains("--strip-components=1"))
    {
        return Err(format!(
            "Dry-run plan lacks extraction step: {:?}",
            plan.steps
        ));
    }
    Ok(())
}

pub fn test_replace_symlink_refuses_regular_file() -> Result<(), String> {
    let dir = make_temp_dir("novos-symlink-test-")?;
    let result = (|| {
        let dest = dir.join("dest");
        fs::write(&dest, b"bin").map_err(|e| e.to_string())?;
        let link = dir.join("link");
        replace_symlink(&dest, &link)?;
        replace_symlink(&dest, &link)?;
        if fs::read_link(&link).map_err(|e| e.to_string())? != dest {
            return Err("Symlink does not point at dest".to_string());
        }
        let regular = dir.join("regular");
        fs::write(&regular, b"keep").map_err(|e| e.to_string())?;
        if replace_symlink(&dest, &regular).is_ok() {
            return Err("Regular file was overwritten".to_string());
        }
        if fs::read(&regular).map_err(|e| e.to_string())? != b"keep" {
            return Err("Regular file content changed".to_string());
        }
        Ok(())
    })();
    let _ = fs::remove_dir_all(&dir);
    result
}

type SelfTestFn = fn() -> Result<(), String>;

pub fn run_self_tests() -> i32 {
    let tests: [(&str, SelfTestFn); 6] = [
        (
            "test_default_manifest_validates",
            test_default_manifest_validates,
        ),
        (
            "test_fallback_yaml_parser_parity",
            test_fallback_yaml_parser_parity,
        ),
        (
            "test_execution_plans_for_all_tool_types",
            test_execution_plans_for_all_tool_types,
        ),
        (
            "test_invalid_manifest_rejected",
            test_invalid_manifest_rejected,
        ),
        (
            "test_tarball_validation_and_plan",
            test_tarball_validation_and_plan,
        ),
        (
            "test_replace_symlink_refuses_regular_file",
            test_replace_symlink_refuses_regular_file,
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
        "usage: novos-custom-tools [--validate [MANIFEST]] [--self-test] [--manifest MANIFEST] [--list] [--install-enabled] [--tool TOOL] [--dry-run]\n\n\
         NovOS Declarative Custom Tools & GitHub Component Builder\n\n\
         {PACKAGES_USAGE}"
    );
}

const PACKAGES_USAGE: &str = "\
usage: novos-custom-tools packages <command> <file> [options]

commands:
  validate <file>
      Validate the unified package manifest (config/packages.yaml).
  list <file> [--layer base|desktop|profile] [--profile P] [--source apt|flatpak|novos-meta]
              [--with-arch] [--health app-health.toml]
      Print one package per line (de-duplicated, manifest order). --with-arch prints
      i386 entries as name:i386. --health substitutes the declared alternative for
      packages listed in [[broken]] tables.
  render-netinstall <file>
      Print a deterministic Calamares netinstall.yaml (profile layer; apt + novos-meta).
  check-apt <file> [--layer base|desktop|profile | --all]
      Run apt-cache policy for every apt package and print each unresolvable one.

exit codes: 0 ok, 1 invalid manifest or runtime error, 2 usage error, 3 unresolvable apt packages";

fn packages_usage_error(msg: &str) -> i32 {
    eprintln!("[FAIL] {msg}\n\n{PACKAGES_USAGE}");
    2
}

fn load_valid_packages(path: &Path) -> Result<packages::PackageManifest, i32> {
    let report = |errors: Vec<String>| {
        for err in errors {
            eprintln!("[FAIL] {err}");
        }
        1
    };
    let manifest = packages::load_packages(path).map_err(report)?;
    let errors = manifest.validate();
    if !errors.is_empty() {
        return Err(report(errors));
    }
    Ok(manifest)
}

fn parse_layer_arg(value: &str) -> Result<packages::Layer, i32> {
    packages::Layer::parse(value).ok_or_else(|| {
        packages_usage_error(&format!(
            "invalid --layer {value:?}; expected base|desktop|profile"
        ))
    })
}

/// `novos-custom-tools packages ...`; returns the process exit code.
fn run_packages_cli(args: &[String]) -> i32 {
    let Some(command) = args.first() else {
        return packages_usage_error("missing packages command");
    };
    if matches!(command.as_str(), "-h" | "--help") {
        println!("{PACKAGES_USAGE}");
        return 0;
    }
    if !matches!(
        command.as_str(),
        "validate" | "list" | "render-netinstall" | "check-apt"
    ) {
        return packages_usage_error(&format!("unknown packages command {command:?}"));
    }

    let mut file: Option<PathBuf> = None;
    let mut filter = packages::ListFilter::default();
    let mut health: Option<PathBuf> = None;
    let mut all_layers = false;
    let mut i = 1usize;
    while i < args.len() {
        let arg = args[i].as_str();
        let takes_value = matches!(arg, "--layer" | "--profile" | "--source" | "--health");
        let value = if takes_value {
            i += 1;
            match args.get(i) {
                Some(v) => Some(v.as_str()),
                None => return packages_usage_error(&format!("{arg} requires a value")),
            }
        } else {
            None
        };
        let allowed = match command.as_str() {
            "list" => matches!(
                arg,
                "--layer" | "--profile" | "--source" | "--with-arch" | "--health"
            ),
            "check-apt" => matches!(arg, "--layer" | "--all"),
            _ => false,
        };
        match (arg, value) {
            (a, _) if a.starts_with('-') && !allowed => {
                return packages_usage_error(&format!("unrecognized option {a} for {command}"));
            }
            ("--layer", Some(v)) => match parse_layer_arg(v) {
                Ok(l) => filter.layer = Some(l),
                Err(code) => return code,
            },
            ("--profile", Some(v)) => {
                if !packages::KNOWN_PROFILES.contains(&v) {
                    return packages_usage_error(&format!(
                        "unknown --profile {v:?}; expected one of {:?}",
                        packages::KNOWN_PROFILES
                    ));
                }
                filter.profile = Some(v.to_string());
            }
            ("--source", Some(v)) => match packages::Source::parse(v) {
                Some(s) => filter.source = Some(s),
                None => {
                    return packages_usage_error(&format!(
                        "invalid --source {v:?}; expected apt|flatpak|novos-meta"
                    ))
                }
            },
            ("--health", Some(v)) => health = Some(PathBuf::from(v)),
            ("--with-arch", None) => filter.with_arch = true,
            ("--all", None) => all_layers = true,
            (positional, None) if file.is_none() => file = Some(PathBuf::from(positional)),
            (extra, _) => {
                return packages_usage_error(&format!("unexpected argument {extra:?}"));
            }
        }
        i += 1;
    }

    let Some(file) = file else {
        return packages_usage_error(&format!("{command} requires a manifest file"));
    };
    if all_layers && filter.layer.is_some() {
        return packages_usage_error("--layer and --all are mutually exclusive");
    }
    let manifest = match load_valid_packages(&file) {
        Ok(m) => m,
        Err(code) => return code,
    };

    match command.as_str() {
        "validate" => {
            let s = manifest.summary();
            println!(
                "{{\"status\": \"valid\", \"manifest\": \"{}\", \"groups\": {}, \"subgroups\": {}, \"entries\": {}, \"unique_packages\": {}, \"required\": {}, \"smoke_checks\": {}}}",
                escape_json(&file.to_string_lossy()),
                s.groups,
                s.subgroups,
                s.entries,
                s.unique_packages,
                s.required,
                s.smoke_checks
            );
            0
        }
        "list" => {
            let broken = match health {
                Some(ref path) => {
                    let parsed = fs::read_to_string(path)
                        .map_err(|e| format!("could not read {}: {e}", path.display()))
                        .and_then(|text| packages::parse_health_toml(&text));
                    match parsed {
                        Ok(b) => b,
                        Err(e) => {
                            eprintln!("[FAIL] {e}");
                            return 1;
                        }
                    }
                }
                None => HashSet::new(),
            };
            match manifest.list(&filter, &broken) {
                Ok(names) => {
                    for n in names {
                        println!("{n}");
                    }
                    0
                }
                Err(e) => {
                    eprintln!("[FAIL] {e}");
                    1
                }
            }
        }
        "render-netinstall" => {
            print!("{}", manifest.render_netinstall());
            0
        }
        _ => {
            let apt_filter = packages::ListFilter {
                layer: filter.layer,
                source: Some(packages::Source::Apt),
                with_arch: true,
                ..Default::default()
            };
            let specs = match manifest.list(&apt_filter, &HashSet::new()) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("[FAIL] {e}");
                    return 1;
                }
            };
            match packages::check_apt(&specs, packages::apt_cache_policy) {
                Ok(missing) if missing.is_empty() => {
                    eprintln!("[OK] all {} apt packages resolvable", specs.len());
                    0
                }
                Ok(missing) => {
                    for m in &missing {
                        println!("{m}");
                    }
                    eprintln!(
                        "[FAIL] {} of {} apt packages unresolvable",
                        missing.len(),
                        specs.len()
                    );
                    3
                }
                Err(e) => {
                    eprintln!("[FAIL] {e}");
                    1
                }
            }
        }
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();

    if args.get(1).map(String::as_str) == Some("packages") {
        std::process::exit(run_packages_cli(&args[2..]));
    }

    let mut self_test = false;
    let mut validate_flag = false;
    let mut validate_arg: Option<PathBuf> = None;
    let mut manifest_arg: Option<PathBuf> = None;
    let mut list_flag = false;
    let mut install_enabled = false;
    let mut tool_name: Option<String> = None;
    let mut dry_run = false;

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
            "--validate" => {
                validate_flag = true;
                if i + 1 < args.len() && !args[i + 1].starts_with('-') {
                    i += 1;
                    validate_arg = Some(PathBuf::from(&args[i]));
                }
            }
            "--manifest" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("[FAIL] --manifest requires a path");
                    std::process::exit(1);
                }
                manifest_arg = Some(PathBuf::from(&args[i]));
            }
            "--list" => {
                list_flag = true;
            }
            "--install-enabled" => {
                install_enabled = true;
            }
            "--tool" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("[FAIL] --tool requires a tool name");
                    std::process::exit(1);
                }
                tool_name = Some(args[i].clone());
            }
            "--dry-run" => {
                dry_run = true;
            }
            s if s.starts_with("--validate=") => {
                validate_flag = true;
                validate_arg = Some(PathBuf::from(&s["--validate=".len()..]));
            }
            s if s.starts_with("--manifest=") => {
                manifest_arg = Some(PathBuf::from(&s["--manifest=".len()..]));
            }
            s if s.starts_with("--tool=") => {
                tool_name = Some(s["--tool=".len()..].to_string());
            }
            _ => {
                eprintln!("[FAIL] Unrecognized argument: {arg}");
                std::process::exit(2);
            }
        }
        i += 1;
    }

    if self_test {
        std::process::exit(run_self_tests());
    }

    if validate_flag {
        let explicit = validate_arg
            .as_deref()
            .unwrap_or_else(|| Path::new("custom-tools/manifest.yaml"));
        let manifest_path = resolve_default_manifest(Some(explicit));
        let data = match load_manifest(&manifest_path) {
            Ok(d) => d,
            Err(exc) => {
                eprintln!("[FAIL] Could not load {}: {exc}", manifest_path.display());
                std::process::exit(1);
            }
        };
        let (ok, errors) = validate_manifest_data(&data);
        if !ok {
            for err in errors {
                eprintln!("[FAIL] {err}");
            }
            std::process::exit(1);
        }
        let enabled_tools: Vec<String> = data
            .tools
            .iter()
            .filter(|t| t.get("enabled").and_then(|v| v.as_bool()) == Some(true))
            .filter_map(|t| t.get("name").and_then(|v| v.as_str()))
            .map(|n| format!("\"{}\"", escape_json(n)))
            .collect();
        println!(
            "{{\"status\": \"valid\", \"manifest\": \"{}\", \"tool_count\": {}, \"enabled_tools\": [{}]}}",
            escape_json(&manifest_path.to_string_lossy()),
            data.tools.len(),
            enabled_tools.join(", ")
        );
        std::process::exit(0);
    }

    let manifest_path = resolve_default_manifest(manifest_arg.as_deref());
    let repo_root = resolve_repo_root(&manifest_path);
    let data = match load_manifest(&manifest_path) {
        Ok(d) => d,
        Err(exc) => {
            eprintln!("[FAIL] {exc}");
            std::process::exit(1);
        }
    };
    let (ok, errors) = validate_manifest_data(&data);
    if !ok {
        for err in errors {
            eprintln!("[FAIL] {err}");
        }
        std::process::exit(1);
    }

    if list_flag || (!install_enabled && tool_name.is_none()) {
        let items: Vec<String> = data
            .tools
            .iter()
            .map(|t| {
                let n = t.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let en = t.get("enabled").and_then(|v| v.as_bool()).unwrap_or(false);
                let tp = t.get("type").and_then(|v| v.as_str()).unwrap_or("");
                let it = t
                    .get("install_target")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                format!(
                    "    {{\n      \"name\": \"{}\",\n      \"enabled\": {},\n      \"type\": \"{}\",\n      \"install_target\": \"{}\"\n    }}",
                    escape_json(n),
                    en,
                    escape_json(tp),
                    escape_json(it)
                )
            })
            .collect();
        println!(
            "{{\n  \"manifest\": \"{}\",\n  \"tools\": [\n{}\n  ]\n}}",
            escape_json(&manifest_path.to_string_lossy()),
            items.join(",\n")
        );
        std::process::exit(0);
    }

    let selected: Vec<&ToolEntry> = if let Some(ref target_name) = tool_name {
        let found: Vec<&ToolEntry> = data
            .tools
            .iter()
            .filter(|t| t.get("name").and_then(|v| v.as_str()) == Some(target_name.as_str()))
            .collect();
        if found.is_empty() {
            eprintln!("[FAIL] Tool {target_name:?} not found in manifest");
            std::process::exit(1);
        }
        found
    } else {
        data.tools
            .iter()
            .filter(|t| t.get("enabled").and_then(|v| v.as_bool()) == Some(true))
            .collect()
    };

    let mut plan_jsons = Vec::new();
    for entry in selected {
        match execute_tool_install(entry, &repo_root, dry_run) {
            Ok(plan) => plan_jsons.push(plan.to_json(4)),
            Err(e) => {
                eprintln!("[FAIL] {e}");
                std::process::exit(1);
            }
        }
    }

    println!("{{\n  \"results\": [\n{}\n  ]\n}}", plan_jsons.join(",\n"));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unit_default_manifest_validates() {
        test_default_manifest_validates().unwrap();
    }

    #[test]
    fn unit_fallback_yaml_parser_parity() {
        test_fallback_yaml_parser_parity().unwrap();
    }

    #[test]
    fn unit_execution_plans_for_all_tool_types() {
        test_execution_plans_for_all_tool_types().unwrap();
    }

    #[test]
    fn unit_invalid_manifest_rejected() {
        test_invalid_manifest_rejected().unwrap();
    }

    #[test]
    fn unit_tarball_validation_and_plan() {
        test_tarball_validation_and_plan().unwrap();
    }

    #[test]
    fn unit_replace_symlink_refuses_regular_file() {
        test_replace_symlink_refuses_regular_file().unwrap();
    }

    const ETXTBSY: i32 = 26;

    fn tmp_leftovers(dir: &Path) -> Vec<String> {
        fs::read_dir(dir)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .filter(|n| n.contains(".novos-tmp."))
            .collect()
    }

    fn mode_of(path: &Path) -> u32 {
        fs::metadata(path).unwrap().permissions().mode() & 0o7777
    }

    #[test]
    fn unit_install_binary_fresh_path_mode_755() {
        let dir = make_temp_dir("novos-install-fresh-").unwrap();
        let src = dir.join("src");
        fs::write(&src, b"payload").unwrap();
        fs::set_permissions(&src, fs::Permissions::from_mode(0o600)).unwrap();
        let dst = dir.join("sub/dir/tool");
        install_binary_mode_755(&src, &dst).unwrap();
        assert_eq!(fs::read(&dst).unwrap(), b"payload");
        assert_eq!(mode_of(&dst), 0o755);
        assert!(tmp_leftovers(&dir.join("sub/dir")).is_empty());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn unit_install_binary_replaces_existing_file() {
        let dir = make_temp_dir("novos-install-replace-").unwrap();
        let src = dir.join("src");
        fs::write(&src, b"new").unwrap();
        let dst = dir.join("tool");
        fs::write(&dst, b"old contents").unwrap();
        fs::set_permissions(&dst, fs::Permissions::from_mode(0o644)).unwrap();
        install_binary_mode_755(&src, &dst).unwrap();
        assert_eq!(fs::read(&dst).unwrap(), b"new");
        assert_eq!(mode_of(&dst), 0o755);
        assert!(tmp_leftovers(&dir).is_empty());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn unit_install_binary_replaces_running_binary() {
        let sleep_bin = ["/bin/sleep", "/usr/bin/sleep"]
            .iter()
            .map(Path::new)
            .find(|p| p.is_file())
            .expect("no sleep binary on host");
        let dir = make_temp_dir("novos-install-running-").unwrap();
        let dst = dir.join("tool");
        fs::copy(sleep_bin, &dst).unwrap();
        fs::set_permissions(&dst, fs::Permissions::from_mode(0o755)).unwrap();

        // A concurrent fork in another test thread can briefly inherit the
        // write fd of the copy above, making exec fail with ETXTBSY. Retry
        // only that error; spawn() returns Ok only after exec succeeded, so
        // once it does, `dst` is a running executable.
        let mut child = None;
        for _ in 0..50 {
            match Command::new(&dst).arg("30").spawn() {
                Ok(c) => {
                    child = Some(c);
                    break;
                }
                Err(e) if e.raw_os_error() == Some(ETXTBSY) => {
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
                Err(e) => panic!("spawn {}: {e}", dst.display()),
            }
        }
        let mut child = child.expect("spawn kept failing with ETXTBSY");

        // Precondition: writing in place to the running file is refused,
        // which is what broke the old fs::copy(src, dst).
        let in_place = fs::OpenOptions::new().write(true).open(&dst);
        let precondition = match in_place {
            Err(e) => e.raw_os_error() == Some(ETXTBSY),
            Ok(_) => false,
        };

        let src = dir.join("src");
        fs::write(&src, b"replacement").unwrap();
        let result = install_binary_mode_755(&src, &dst);

        child.kill().unwrap();
        child.wait().unwrap();

        assert!(precondition, "running file was writable in place");
        result.unwrap();
        assert_eq!(fs::read(&dst).unwrap(), b"replacement");
        assert_eq!(mode_of(&dst), 0o755);
        assert!(tmp_leftovers(&dir).is_empty());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn unit_install_binary_missing_src_leaves_no_temp() {
        let dir = make_temp_dir("novos-install-missing-").unwrap();
        let src = dir.join("does-not-exist");
        let dst = dir.join("tool");
        fs::write(&dst, b"keep").unwrap();
        let err = install_binary_mode_755(&src, &dst).unwrap_err();
        assert!(err.contains(&src.display().to_string()), "{err}");
        assert!(err.contains(&dst.display().to_string()), "{err}");
        assert!(err.contains("copy to temp file"), "{err}");
        assert_eq!(fs::read(&dst).unwrap(), b"keep");
        assert!(tmp_leftovers(&dir).is_empty());
        fs::remove_dir_all(&dir).unwrap();
    }
}
