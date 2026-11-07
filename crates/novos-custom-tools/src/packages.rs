//! NovOS unified package manifest (`config/packages.yaml`)
//! ======================================================
//! Typed model, loader, validation and renderers behind the
//! `novos-custom-tools packages` subcommands:
//!   - `validate`: structural + semantic checks of the manifest
//!   - `list`: filtered, de-duplicated package names (optionally `name:i386`)
//!   - `render-netinstall`: deterministic Calamares `netinstall.yaml`
//!   - `check-apt`: `apt-cache policy` resolution of every apt package
//!
//! The manifest uses a YAML subset (block mappings, block sequences, flow
//! mappings/lists, `#` comments). Leaf values are parsed with the crate's
//! existing `parse_scalar` into `YamlScalar`; no external YAML crate is used.

use std::collections::HashSet;
use std::fs;
use std::path::Path;
use std::process::Command;

use crate::{parse_scalar, strip_yaml_comment, YamlScalar};

pub const KNOWN_PROFILES: [&str; 12] = [
    "wifi",
    "bluetooth",
    "gaming",
    "dev",
    "devsecops",
    "llm",
    "network",
    "comms",
    "browsers",
    "media",
    "utilities",
    "custom_tools",
];

const NETINSTALL_HEADER: &str = "\
# ==============================================================================
# NovOS Calamares Modular Profile & Granular Package Selection (netinstall.yaml)
# Rendered from config/packages.yaml (edit the manifest, not this file) with:
#   novos-custom-tools packages render-netinstall config/packages.yaml
# ==============================================================================
";

// ---------------------------------------------------------------------------
// YAML subset tree
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum YamlNode {
    Scalar(YamlScalar),
    List(Vec<YamlNode>),
    Map(Vec<(String, YamlNode)>),
}

#[derive(Debug, Clone)]
struct Line {
    no: usize,
    indent: usize,
    text: String,
}

fn is_seq_item(text: &str) -> bool {
    text == "-" || text.starts_with("- ")
}

/// Split `key: value` at the first `:` outside quotes that is followed by a
/// space or end of line. Returns `None` for plain scalars such as `wine32:i386`.
fn split_key(text: &str) -> Option<(String, String)> {
    if text.starts_with(['"', '\'', '{', '[']) {
        return None;
    }
    let mut in_single = false;
    let mut in_double = false;
    let mut escape = false;
    let bytes = text.as_bytes();
    for (idx, ch) in text.char_indices() {
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
            ':' if idx + 1 == bytes.len() || bytes[idx + 1] == b' ' => {
                let key = text[..idx].trim();
                if key.is_empty() {
                    return None;
                }
                return Some((key.to_string(), text[idx + 1..].trim().to_string()));
            }
            _ => {}
        }
    }
    None
}

fn tokenize(text: &str) -> Result<Vec<Line>, String> {
    let mut lines = Vec::new();
    for (idx, raw) in text.lines().enumerate() {
        let no = idx + 1;
        let uncommented = strip_yaml_comment(raw).trim_end();
        let content = uncommented.trim_start_matches(' ');
        if content.starts_with('\t') {
            return Err(format!("line {no}: tab indentation is not allowed"));
        }
        if content.is_empty() || content == "---" || content == "..." {
            continue;
        }
        lines.push(Line {
            no,
            indent: uncommented.len() - content.len(),
            text: content.to_string(),
        });
    }
    Ok(lines)
}

struct BlockParser {
    lines: Vec<Line>,
    pos: usize,
}

impl BlockParser {
    fn parse_block(&mut self, indent: usize) -> Result<YamlNode, String> {
        if is_seq_item(&self.lines[self.pos].text) {
            self.parse_seq(indent)
        } else {
            self.parse_map(indent)
        }
    }

    fn parse_seq(&mut self, indent: usize) -> Result<YamlNode, String> {
        let mut items = Vec::new();
        while self.pos < self.lines.len() {
            let line = self.lines[self.pos].clone();
            if line.indent < indent || !is_seq_item(&line.text) {
                break;
            }
            if line.indent > indent {
                return Err(format!("line {}: unexpected indentation", line.no));
            }
            let raw_body = line.text.strip_prefix('-').unwrap_or("");
            let body = raw_body.trim_start();
            let lead = raw_body.len() - body.len();
            if body.is_empty() {
                self.pos += 1;
                if self.pos < self.lines.len() && self.lines[self.pos].indent > indent {
                    let child = self.lines[self.pos].indent;
                    items.push(self.parse_block(child)?);
                } else {
                    items.push(YamlNode::Scalar(YamlScalar::Null));
                }
            } else if body.starts_with(['{', '[']) {
                items.push(parse_flow(body, line.no)?);
                self.pos += 1;
            } else if split_key(body).is_some() {
                // `- key: value` opens a mapping whose column is the item body.
                let col = indent + 1 + lead;
                self.lines[self.pos].indent = col;
                self.lines[self.pos].text = body.to_string();
                items.push(self.parse_map(col)?);
            } else {
                items.push(YamlNode::Scalar(parse_scalar(body)));
                self.pos += 1;
            }
        }
        Ok(YamlNode::List(items))
    }

    fn parse_map(&mut self, indent: usize) -> Result<YamlNode, String> {
        let mut entries: Vec<(String, YamlNode)> = Vec::new();
        while self.pos < self.lines.len() {
            let line = self.lines[self.pos].clone();
            if line.indent < indent || is_seq_item(&line.text) {
                break;
            }
            if line.indent > indent {
                return Err(format!("line {}: unexpected indentation", line.no));
            }
            let (key, value) = split_key(&line.text)
                .ok_or_else(|| format!("line {}: expected 'key: value'", line.no))?;
            if entries.iter().any(|(k, _)| *k == key) {
                return Err(format!("line {}: duplicate key {key:?}", line.no));
            }
            self.pos += 1;
            let node = if value.is_empty() {
                match self.lines.get(self.pos) {
                    Some(next) if next.indent > indent => {
                        let child = next.indent;
                        self.parse_block(child)?
                    }
                    Some(next) if next.indent == indent && is_seq_item(&next.text) => {
                        self.parse_seq(indent)?
                    }
                    _ => YamlNode::Scalar(YamlScalar::Null),
                }
            } else if value.starts_with(['{', '[']) {
                parse_flow(&value, line.no)?
            } else {
                YamlNode::Scalar(parse_scalar(&value))
            };
            entries.push((key, node));
        }
        Ok(YamlNode::Map(entries))
    }
}

fn skip_ws(chars: &[char], pos: &mut usize) {
    while *pos < chars.len() && chars[*pos].is_whitespace() {
        *pos += 1;
    }
}

/// Read a flow token up to one of `stops` at quote depth zero.
fn read_flow_token(chars: &[char], pos: &mut usize, stops: &[char]) -> String {
    let mut out = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut escape = false;
    while *pos < chars.len() {
        let ch = chars[*pos];
        if in_double {
            if escape {
                escape = false;
            } else if ch == '\\' {
                escape = true;
            } else if ch == '"' {
                in_double = false;
            }
        } else if in_single {
            if ch == '\'' {
                in_single = false;
            }
        } else if ch == '"' {
            in_double = true;
        } else if ch == '\'' {
            in_single = true;
        } else if stops.contains(&ch) {
            break;
        }
        out.push(ch);
        *pos += 1;
    }
    out
}

fn flow_value(chars: &[char], pos: &mut usize, no: usize) -> Result<YamlNode, String> {
    skip_ws(chars, pos);
    match chars.get(*pos) {
        Some('{') => {
            *pos += 1;
            let mut entries: Vec<(String, YamlNode)> = Vec::new();
            loop {
                skip_ws(chars, pos);
                if chars.get(*pos) == Some(&'}') {
                    *pos += 1;
                    break;
                }
                let key = read_flow_token(chars, pos, &[':', ',', '}']);
                let key = key.trim().to_string();
                if chars.get(*pos) != Some(&':') || key.is_empty() {
                    return Err(format!("line {no}: expected 'key: value' in flow mapping"));
                }
                *pos += 1;
                if entries.iter().any(|(k, _)| *k == key) {
                    return Err(format!("line {no}: duplicate key {key:?}"));
                }
                let value = flow_value(chars, pos, no)?;
                entries.push((key, value));
                skip_ws(chars, pos);
                match chars.get(*pos) {
                    Some(',') => *pos += 1,
                    Some('}') => {
                        *pos += 1;
                        break;
                    }
                    _ => return Err(format!("line {no}: unterminated flow mapping")),
                }
            }
            Ok(YamlNode::Map(entries))
        }
        Some('[') => {
            *pos += 1;
            let mut items = Vec::new();
            loop {
                skip_ws(chars, pos);
                if chars.get(*pos) == Some(&']') {
                    *pos += 1;
                    break;
                }
                items.push(flow_value(chars, pos, no)?);
                skip_ws(chars, pos);
                match chars.get(*pos) {
                    Some(',') => *pos += 1,
                    Some(']') => {
                        *pos += 1;
                        break;
                    }
                    _ => return Err(format!("line {no}: unterminated flow list")),
                }
            }
            Ok(YamlNode::List(items))
        }
        Some(_) => {
            let token = read_flow_token(chars, pos, &[',', '}', ']']);
            Ok(YamlNode::Scalar(parse_scalar(&token)))
        }
        None => Err(format!("line {no}: missing flow value")),
    }
}

fn parse_flow(text: &str, no: usize) -> Result<YamlNode, String> {
    let chars: Vec<char> = text.trim().chars().collect();
    let mut pos = 0usize;
    let node = flow_value(&chars, &mut pos, no)?;
    skip_ws(&chars, &mut pos);
    if pos != chars.len() {
        return Err(format!(
            "line {no}: trailing characters after flow collection"
        ));
    }
    Ok(node)
}

/// Parse a YAML-subset document into a `YamlNode` tree.
pub fn parse_yaml_document(text: &str) -> Result<YamlNode, String> {
    let lines = tokenize(text)?;
    if lines.is_empty() {
        return Ok(YamlNode::Scalar(YamlScalar::Null));
    }
    let indent = lines[0].indent;
    let mut parser = BlockParser { lines, pos: 0 };
    let node = parser.parse_block(indent)?;
    if let Some(extra) = parser.lines.get(parser.pos) {
        return Err(format!("line {}: unexpected content", extra.no));
    }
    Ok(node)
}

// ---------------------------------------------------------------------------
// Typed model
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Apt,
    Flatpak,
    NovosMeta,
    /// Installed by `novos-custom-tools --tool <name>` from
    /// `custom-tools/manifest.yaml`; never resolved through apt.
    CustomTool,
}

pub const SOURCE_CHOICES: &str = "apt|flatpak|novos-meta|custom-tool";

impl Source {
    pub fn parse(s: &str) -> Option<Source> {
        match s {
            "apt" => Some(Source::Apt),
            "flatpak" => Some(Source::Flatpak),
            "novos-meta" => Some(Source::NovosMeta),
            "custom-tool" => Some(Source::CustomTool),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Source::Apt => "apt",
            Source::Flatpak => "flatpak",
            Source::NovosMeta => "novos-meta",
            Source::CustomTool => "custom-tool",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Arch {
    Amd64,
    I386,
    All,
}

impl Arch {
    pub fn parse(s: &str) -> Option<Arch> {
        match s {
            "amd64" => Some(Arch::Amd64),
            "i386" => Some(Arch::I386),
            "all" => Some(Arch::All),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Arch::Amd64 => "amd64",
            Arch::I386 => "i386",
            Arch::All => "all",
        }
    }

    /// Multiarch qualifier for apt: only the foreign i386 architecture is
    /// qualified. `amd64` is native and `all` packages install natively.
    pub fn foreign_qualifier(self) -> Option<&'static str> {
        match self {
            Arch::I386 => Some("i386"),
            Arch::Amd64 | Arch::All => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layer {
    Base,
    Desktop,
    Profile,
}

impl Layer {
    pub fn parse(s: &str) -> Option<Layer> {
        match s {
            "base" => Some(Layer::Base),
            "desktop" => Some(Layer::Desktop),
            "profile" => Some(Layer::Profile),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageEntry {
    pub name: String,
    pub source: Source,
    pub arch: Arch,
    pub required: bool,
    pub alternative: Option<String>,
    pub smoke: Option<String>,
}

impl PackageEntry {
    /// Package spec as passed to apt: `name:i386` for i386 entries when
    /// `with_arch` is set, the bare name otherwise.
    pub fn spec(&self, with_arch: bool) -> String {
        match (with_arch, self.arch.foreign_qualifier()) {
            (true, Some(q)) => format!("{}:{q}", self.name),
            _ => self.name.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Subgroup {
    pub id: String,
    pub name: String,
    pub description: String,
    pub selected: bool,
    pub critical: Option<bool>,
    pub expanded: Option<bool>,
    pub packages: Vec<PackageEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Group {
    pub id: String,
    pub name: String,
    pub description: String,
    pub layer: Layer,
    pub profile: Option<String>,
    pub selected: bool,
    pub critical: bool,
    pub expanded: Option<bool>,
    pub packages: Vec<PackageEntry>,
    pub subgroups: Vec<Subgroup>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageManifest {
    pub groups: Vec<Group>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ListFilter {
    pub layer: Option<Layer>,
    pub profile: Option<String>,
    pub source: Option<Source>,
    pub with_arch: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManifestSummary {
    pub groups: usize,
    pub subgroups: usize,
    pub entries: usize,
    pub unique_packages: usize,
    pub required: usize,
    pub smoke_checks: usize,
}

// ---------------------------------------------------------------------------
// Loader: YamlNode tree -> typed model
// ---------------------------------------------------------------------------

type YamlMap = [(String, YamlNode)];

fn get<'a>(map: &'a YamlMap, key: &str) -> Option<&'a YamlNode> {
    map.iter().find(|(k, _)| k == key).map(|(_, v)| v)
}

fn describe(node: &YamlNode) -> String {
    match node {
        YamlNode::Scalar(YamlScalar::Str(s)) => format!("{s:?}"),
        YamlNode::Scalar(YamlScalar::Bool(b)) => b.to_string(),
        YamlNode::Scalar(YamlScalar::Int(i)) => i.to_string(),
        YamlNode::Scalar(YamlScalar::Null) => "null".to_string(),
        YamlNode::List(_) => "a list".to_string(),
        YamlNode::Map(_) => "a mapping".to_string(),
    }
}

fn check_keys(map: &YamlMap, allowed: &[&str], ctx: &str, errors: &mut Vec<String>) {
    for (k, _) in map {
        if !allowed.contains(&k.as_str()) {
            errors.push(format!("{ctx}: unknown key {k:?}"));
        }
    }
}

fn field_str(map: &YamlMap, key: &str, ctx: &str, errors: &mut Vec<String>) -> Option<String> {
    match get(map, key) {
        Some(YamlNode::Scalar(YamlScalar::Str(s))) if !s.trim().is_empty() => Some(s.clone()),
        Some(other) => {
            errors.push(format!(
                "{ctx}: '{key}' must be a non-empty string, got {}",
                describe(other)
            ));
            None
        }
        None => {
            errors.push(format!("{ctx}: missing '{key}'"));
            None
        }
    }
}

fn field_bool(map: &YamlMap, key: &str, ctx: &str, errors: &mut Vec<String>) -> Option<bool> {
    match get(map, key) {
        Some(YamlNode::Scalar(YamlScalar::Bool(b))) => Some(*b),
        Some(other) => {
            errors.push(format!(
                "{ctx}: '{key}' must be a boolean, got {}",
                describe(other)
            ));
            None
        }
        None => {
            errors.push(format!("{ctx}: missing '{key}'"));
            None
        }
    }
}

/// Optional boolean: `Ok(None)` when absent, `Err(())` (error recorded) when invalid.
fn field_opt_bool(
    map: &YamlMap,
    key: &str,
    ctx: &str,
    errors: &mut Vec<String>,
) -> Result<Option<bool>, ()> {
    match get(map, key) {
        None => Ok(None),
        Some(YamlNode::Scalar(YamlScalar::Bool(b))) => Ok(Some(*b)),
        Some(other) => {
            errors.push(format!(
                "{ctx}: '{key}' must be a boolean, got {}",
                describe(other)
            ));
            Err(())
        }
    }
}

/// Optional string: `Ok(None)` when absent or null, `Err(())` (error recorded) when invalid.
fn field_opt_str(
    map: &YamlMap,
    key: &str,
    ctx: &str,
    errors: &mut Vec<String>,
) -> Result<Option<String>, ()> {
    match get(map, key) {
        None | Some(YamlNode::Scalar(YamlScalar::Null)) => Ok(None),
        Some(YamlNode::Scalar(YamlScalar::Str(s))) if !s.trim().is_empty() => Ok(Some(s.clone())),
        Some(other) => {
            errors.push(format!(
                "{ctx}: '{key}' must be a non-empty string or null, got {}",
                describe(other)
            ));
            Err(())
        }
    }
}

fn field_list<'a>(
    map: &'a YamlMap,
    key: &str,
    ctx: &str,
    errors: &mut Vec<String>,
) -> Result<&'a [YamlNode], ()> {
    match get(map, key) {
        None => Ok(&[]),
        Some(YamlNode::List(items)) => Ok(items.as_slice()),
        Some(other) => {
            errors.push(format!(
                "{ctx}: '{key}' must be a list, got {}",
                describe(other)
            ));
            Err(())
        }
    }
}

fn load_entry(node: &YamlNode, ctx: &str, errors: &mut Vec<String>) -> Option<PackageEntry> {
    let YamlNode::Map(map) = node else {
        errors.push(format!(
            "{ctx}: package entry must be a mapping, got {}",
            describe(node)
        ));
        return None;
    };
    check_keys(
        map,
        &["name", "source", "arch", "required", "alternative", "smoke"],
        ctx,
        errors,
    );
    let name = field_str(map, "name", ctx, errors);
    let ctx = match &name {
        Some(n) => format!("{ctx} ({n})"),
        None => ctx.to_string(),
    };
    let source = field_str(map, "source", &ctx, errors).and_then(|s| {
        let parsed = Source::parse(&s);
        if parsed.is_none() {
            errors.push(format!(
                "{ctx}: invalid source {s:?}; expected {SOURCE_CHOICES}"
            ));
        }
        parsed
    });
    let arch = match get(map, "arch") {
        None => Some(Arch::Amd64),
        Some(_) => field_str(map, "arch", &ctx, errors).and_then(|s| {
            let parsed = Arch::parse(&s);
            if parsed.is_none() {
                errors.push(format!(
                    "{ctx}: invalid arch {s:?}; expected amd64|i386|all"
                ));
            }
            parsed
        }),
    };
    let required = match get(map, "required") {
        None => Some(true),
        Some(_) => field_bool(map, "required", &ctx, errors),
    };
    let alternative = field_opt_str(map, "alternative", &ctx, errors);
    let smoke = field_opt_str(map, "smoke", &ctx, errors);
    Some(PackageEntry {
        name: name?,
        source: source?,
        arch: arch?,
        required: required?,
        alternative: alternative.ok()?,
        smoke: smoke.ok()?,
    })
}

fn load_entries(items: &[YamlNode], owner: &str, errors: &mut Vec<String>) -> Vec<PackageEntry> {
    items
        .iter()
        .enumerate()
        .filter_map(|(i, item)| load_entry(item, &format!("{owner} package #{i}"), errors))
        .collect()
}

fn load_subgroup(node: &YamlNode, ctx: &str, errors: &mut Vec<String>) -> Option<Subgroup> {
    let YamlNode::Map(map) = node else {
        errors.push(format!(
            "{ctx}: subgroup must be a mapping, got {}",
            describe(node)
        ));
        return None;
    };
    check_keys(
        map,
        &[
            "id",
            "name",
            "description",
            "selected",
            "critical",
            "expanded",
            "packages",
        ],
        ctx,
        errors,
    );
    let id = field_str(map, "id", ctx, errors);
    let ctx = match &id {
        Some(i) => format!("subgroup {i:?}"),
        None => ctx.to_string(),
    };
    let name = field_str(map, "name", &ctx, errors);
    let description = field_str(map, "description", &ctx, errors);
    let selected = field_bool(map, "selected", &ctx, errors);
    let critical = field_opt_bool(map, "critical", &ctx, errors);
    let expanded = field_opt_bool(map, "expanded", &ctx, errors);
    let packages =
        field_list(map, "packages", &ctx, errors).map(|items| load_entries(items, &ctx, errors));
    Some(Subgroup {
        id: id?,
        name: name?,
        description: description?,
        selected: selected?,
        critical: critical.ok()?,
        expanded: expanded.ok()?,
        packages: packages.ok()?,
    })
}

fn load_group(node: &YamlNode, ctx: &str, errors: &mut Vec<String>) -> Option<Group> {
    let YamlNode::Map(map) = node else {
        errors.push(format!(
            "{ctx}: group must be a mapping, got {}",
            describe(node)
        ));
        return None;
    };
    check_keys(
        map,
        &[
            "id",
            "name",
            "description",
            "layer",
            "profile",
            "selected",
            "critical",
            "expanded",
            "packages",
            "subgroups",
        ],
        ctx,
        errors,
    );
    let id = field_str(map, "id", ctx, errors);
    let ctx = match &id {
        Some(i) => format!("group {i:?}"),
        None => ctx.to_string(),
    };
    let name = field_str(map, "name", &ctx, errors);
    let description = field_str(map, "description", &ctx, errors);
    let layer = field_str(map, "layer", &ctx, errors).and_then(|s| {
        let parsed = Layer::parse(&s);
        if parsed.is_none() {
            errors.push(format!(
                "{ctx}: invalid layer {s:?}; expected base|desktop|profile"
            ));
        }
        parsed
    });
    let profile = field_opt_str(map, "profile", &ctx, errors);
    let selected = field_bool(map, "selected", &ctx, errors);
    let critical = field_bool(map, "critical", &ctx, errors);
    let expanded = field_opt_bool(map, "expanded", &ctx, errors);
    let packages =
        field_list(map, "packages", &ctx, errors).map(|items| load_entries(items, &ctx, errors));
    let subgroups = field_list(map, "subgroups", &ctx, errors).map(|items| {
        items
            .iter()
            .enumerate()
            .filter_map(|(i, s)| load_subgroup(s, &format!("{ctx} subgroup #{i}"), errors))
            .collect::<Vec<_>>()
    });
    Some(Group {
        id: id?,
        name: name?,
        description: description?,
        layer: layer?,
        profile: profile.ok()?,
        selected: selected?,
        critical: critical?,
        expanded: expanded.ok()?,
        packages: packages.ok()?,
        subgroups: subgroups.ok()?,
    })
}

/// Load the typed manifest from YAML text. Returns every structural error found.
pub fn load_packages_text(text: &str) -> Result<PackageManifest, Vec<String>> {
    let root = parse_yaml_document(text).map_err(|e| vec![e])?;
    let YamlNode::Map(map) = &root else {
        return Err(vec!["top level must be a mapping".to_string()]);
    };
    let mut errors = Vec::new();
    check_keys(map, &["schema_version", "groups"], "top level", &mut errors);
    match get(map, "schema_version") {
        Some(YamlNode::Scalar(YamlScalar::Int(1))) => {}
        Some(other) => errors.push(format!(
            "unsupported schema_version {} (expected 1)",
            describe(other)
        )),
        None => errors.push("missing schema_version (expected 1)".to_string()),
    }
    let mut groups = Vec::new();
    match get(map, "groups") {
        Some(YamlNode::List(items)) => {
            for (i, item) in items.iter().enumerate() {
                if let Some(g) = load_group(item, &format!("group #{i}"), &mut errors) {
                    groups.push(g);
                }
            }
        }
        Some(other) => errors.push(format!("'groups' must be a list, got {}", describe(other))),
        None => errors.push("missing 'groups' list".to_string()),
    }
    if errors.is_empty() {
        Ok(PackageManifest { groups })
    } else {
        Err(errors)
    }
}

pub fn load_packages(path: &Path) -> Result<PackageManifest, Vec<String>> {
    let text = fs::read_to_string(path)
        .map_err(|e| vec![format!("could not read {}: {e}", path.display())])?;
    load_packages_text(&text)
}

// ---------------------------------------------------------------------------
// Validation, listing, rendering
// ---------------------------------------------------------------------------

fn check_entries(
    owner: &str,
    entries: &[PackageEntry],
    declared: &HashSet<&str>,
    errors: &mut Vec<String>,
) {
    for e in entries {
        if e.arch == Arch::I386 && e.source != Source::Apt {
            errors.push(format!(
                "{owner}: {} has arch i386 but source {}; i386 is only valid for apt",
                e.name,
                e.source.as_str()
            ));
        }
        if e.source == Source::CustomTool {
            if e.arch != Arch::Amd64 {
                errors.push(format!(
                    "{owner}: {} has source custom-tool but arch {}; custom-tool entries must be amd64",
                    e.name,
                    e.arch.as_str()
                ));
            }
            if e.alternative.is_some() {
                errors.push(format!(
                    "{owner}: {} has source custom-tool; custom-tool entries must not set 'alternative'",
                    e.name
                ));
            }
        }
        if let Some(alt) = &e.alternative {
            if *alt == e.name {
                errors.push(format!("{owner}: {} names itself as alternative", e.name));
            } else if !declared.contains(alt.as_str()) {
                errors.push(format!(
                    "{owner}: {} alternative {alt:?} is not declared in the manifest",
                    e.name
                ));
            }
        }
    }
}

impl PackageManifest {
    /// Every package entry with its owning group, in manifest order
    /// (group-level packages first, then each subgroup's).
    pub fn all_entries(&self) -> Vec<(&Group, &PackageEntry)> {
        let mut out = Vec::new();
        for g in &self.groups {
            out.extend(g.packages.iter().map(|e| (g, e)));
            for s in &g.subgroups {
                out.extend(s.packages.iter().map(|e| (g, e)));
            }
        }
        out
    }

    fn find_entry(&self, name: &str, arch: Arch) -> Option<&PackageEntry> {
        let entries = self.all_entries();
        entries
            .iter()
            .find(|(_, e)| e.name == name && e.arch == arch)
            .or_else(|| entries.iter().find(|(_, e)| e.name == name))
            .map(|(_, e)| *e)
    }

    /// Semantic validation: unique group/subgroup ids, known profiles,
    /// i386 only for apt, custom-tool entries amd64 without alternative,
    /// declared alternatives, no empty groups.
    pub fn validate(&self) -> Vec<String> {
        let mut errors = Vec::new();
        if self.groups.is_empty() {
            errors.push("manifest must define a non-empty 'groups' list".to_string());
        }
        let declared: HashSet<&str> = self
            .all_entries()
            .into_iter()
            .map(|(_, e)| e.name.as_str())
            .collect();
        let mut ids = HashSet::new();
        for g in &self.groups {
            if !ids.insert(g.id.as_str()) {
                errors.push(format!("duplicate group id {:?}", g.id));
            }
            match (g.layer, &g.profile) {
                (Layer::Profile, None) => errors.push(format!(
                    "group {:?}: profile-layer groups must declare 'profile'",
                    g.id
                )),
                (Layer::Profile, Some(p)) if !KNOWN_PROFILES.contains(&p.as_str()) => {
                    errors.push(format!(
                        "group {:?}: unknown profile {p:?}; expected one of {KNOWN_PROFILES:?}",
                        g.id
                    ))
                }
                (Layer::Base | Layer::Desktop, Some(p)) => errors.push(format!(
                    "group {:?}: profile {p:?} is only valid on layer profile",
                    g.id
                )),
                _ => {}
            }
            if g.packages.is_empty() && g.subgroups.is_empty() {
                errors.push(format!("group {:?} has no packages or subgroups", g.id));
            }
            check_entries(
                &format!("group {:?}", g.id),
                &g.packages,
                &declared,
                &mut errors,
            );
            for s in &g.subgroups {
                if !ids.insert(s.id.as_str()) {
                    errors.push(format!("duplicate group id {:?}", s.id));
                }
                if s.packages.is_empty() {
                    errors.push(format!("subgroup {:?} has no packages", s.id));
                }
                check_entries(
                    &format!("subgroup {:?}", s.id),
                    &s.packages,
                    &declared,
                    &mut errors,
                );
            }
        }
        errors
    }

    pub fn summary(&self) -> ManifestSummary {
        let entries = self.all_entries();
        let unique: HashSet<String> = entries.iter().map(|(_, e)| e.spec(true)).collect();
        ManifestSummary {
            groups: self.groups.len(),
            subgroups: self.groups.iter().map(|g| g.subgroups.len()).sum(),
            entries: entries.len(),
            unique_packages: unique.len(),
            required: entries.iter().filter(|(_, e)| e.required).count(),
            smoke_checks: entries.iter().filter(|(_, e)| e.smoke.is_some()).count(),
        }
    }

    /// Filtered, de-duplicated package specs in manifest order. Entries named
    /// in `broken` are replaced by their declared alternative.
    pub fn list(
        &self,
        filter: &ListFilter,
        broken: &HashSet<String>,
    ) -> Result<Vec<String>, String> {
        let mut out = Vec::new();
        let mut seen = HashSet::new();
        for (g, e) in self.all_entries() {
            if filter.layer.is_some_and(|l| g.layer != l) {
                continue;
            }
            if let Some(p) = &filter.profile {
                if g.profile.as_deref() != Some(p.as_str()) {
                    continue;
                }
            }
            if filter.source.is_some_and(|s| e.source != s) {
                continue;
            }
            let chosen = if broken.contains(&e.name) {
                let alt = e.alternative.as_deref().ok_or_else(|| {
                    format!("{} is listed broken but declares no alternative", e.name)
                })?;
                if broken.contains(alt) {
                    return Err(format!(
                        "{} is listed broken and its alternative {alt} is also broken",
                        e.name
                    ));
                }
                let alt_entry = self.find_entry(alt, e.arch).ok_or_else(|| {
                    format!(
                        "{} alternative {alt} is not declared in the manifest",
                        e.name
                    )
                })?;
                if alt_entry.source != e.source {
                    return Err(format!(
                        "{} ({}) alternative {alt} has source {}; a substitute must keep the same source",
                        e.name,
                        e.source.as_str(),
                        alt_entry.source.as_str()
                    ));
                }
                alt_entry
            } else {
                e
            };
            let spec = chosen.spec(filter.with_arch);
            if seen.insert(spec.clone()) {
                out.push(spec);
            }
        }
        Ok(out)
    }

    /// Deterministic Calamares netinstall.yaml: profile-layer groups only,
    /// apt and novos-meta packages (flatpak refs are provisioned by novos-setup;
    /// custom-tool entries are installed by novos-custom-tools).
    pub fn render_netinstall(&self) -> String {
        let mut blocks = Vec::new();
        for g in self.groups.iter().filter(|g| g.layer == Layer::Profile) {
            let group_pkgs = netinstall_packages(&g.packages);
            let mut subs = String::new();
            for s in &g.subgroups {
                let pkgs = netinstall_packages(&s.packages);
                if pkgs.is_empty() {
                    continue;
                }
                subs.push_str(&format!("    - name: {}\n", yaml_quote(&s.name)));
                subs.push_str(&format!(
                    "      description: {}\n",
                    yaml_quote(&s.description)
                ));
                subs.push_str(&format!("      selected: {}\n", s.selected));
                if let Some(c) = s.critical {
                    subs.push_str(&format!("      critical: {c}\n"));
                }
                if let Some(x) = s.expanded {
                    subs.push_str(&format!("      expanded: {x}\n"));
                }
                subs.push_str("      packages:\n");
                for p in pkgs {
                    subs.push_str(&format!("        - {p}\n"));
                }
            }
            if group_pkgs.is_empty() && subs.is_empty() {
                continue;
            }
            let mut b = String::new();
            b.push_str(&format!("- name: {}\n", yaml_quote(&g.name)));
            b.push_str(&format!("  description: {}\n", yaml_quote(&g.description)));
            b.push_str(&format!("  selected: {}\n", g.selected));
            b.push_str(&format!("  critical: {}\n", g.critical));
            if let Some(x) = g.expanded {
                b.push_str(&format!("  expanded: {x}\n"));
            }
            if !group_pkgs.is_empty() {
                b.push_str("  packages:\n");
                for p in group_pkgs {
                    b.push_str(&format!("    - {p}\n"));
                }
            }
            if !subs.is_empty() {
                b.push_str("  subgroups:\n");
                b.push_str(&subs);
            }
            blocks.push(b);
        }
        format!("{NETINSTALL_HEADER}---\n{}", blocks.join("\n"))
    }
}

fn netinstall_packages(entries: &[PackageEntry]) -> Vec<String> {
    let mut seen = HashSet::new();
    entries
        .iter()
        .filter(|e| matches!(e.source, Source::Apt | Source::NovosMeta))
        .map(|e| e.spec(true))
        .filter(|s| seen.insert(s.clone()))
        .collect()
}

fn yaml_quote(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Package names marked broken in an app-health TOML file
/// (`[[broken]]` tables with `package = "<name>"`).
pub fn parse_health_toml(text: &str) -> Result<HashSet<String>, String> {
    let mut broken = HashSet::new();
    let mut in_broken = false;
    let mut current: Option<String> = None;
    let mut table_start = 0usize;
    for (idx, raw) in text.lines().enumerate() {
        let line = strip_yaml_comment(raw).trim();
        if line.is_empty() {
            continue;
        }
        if line.starts_with('[') {
            if in_broken {
                broken.insert(current.take().ok_or_else(|| {
                    format!("line {table_start}: [[broken]] table without 'package'")
                })?);
            }
            in_broken = line.replace([' ', '\t'], "") == "[[broken]]";
            table_start = idx + 1;
            continue;
        }
        if in_broken {
            if let Some((k, v)) = line.split_once('=') {
                if k.trim() == "package" {
                    match parse_scalar(v.trim()) {
                        YamlScalar::Str(s) if !s.is_empty() => current = Some(s),
                        _ => {
                            return Err(format!(
                                "line {}: 'package' must be a non-empty string",
                                idx + 1
                            ))
                        }
                    }
                }
            }
        }
    }
    if in_broken {
        broken
            .insert(current.ok_or_else(|| {
                format!("line {table_start}: [[broken]] table without 'package'")
            })?);
    }
    Ok(broken)
}

// ---------------------------------------------------------------------------
// apt-cache policy resolution
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AptResolution {
    Candidate(String),
    Unresolvable,
}

/// Interpret `apt-cache policy <spec>` stdout. Empty output (unknown
/// package) and `Candidate: (none)` are both unresolvable.
pub fn parse_apt_cache_policy(output: &str) -> AptResolution {
    for line in output.lines() {
        if let Some(rest) = line.trim().strip_prefix("Candidate:") {
            let cand = rest.trim();
            if cand.is_empty() || cand == "(none)" {
                return AptResolution::Unresolvable;
            }
            return AptResolution::Candidate(cand.to_string());
        }
    }
    AptResolution::Unresolvable
}

pub fn apt_cache_policy(spec: &str) -> Result<String, String> {
    let out = Command::new("apt-cache")
        .args(["policy", spec])
        .output()
        .map_err(|e| format!("failed to run apt-cache policy {spec}: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "apt-cache policy {spec} exited with {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Resolve every spec with `policy`; returns the unresolvable specs in order.
pub fn check_apt<F>(specs: &[String], mut policy: F) -> Result<Vec<String>, String>
where
    F: FnMut(&str) -> Result<String, String>,
{
    let mut missing = Vec::new();
    for spec in specs {
        if parse_apt_cache_policy(&policy(spec)?) == AptResolution::Unresolvable {
            missing.push(spec.clone());
        }
    }
    Ok(missing)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    const FIXTURE: &str = r#"# fixture manifest
schema_version: 1
groups:
  - id: base-core
    name: "Core"
    description: "Base tools."
    layer: base
    selected: true
    critical: true
    packages:
      - {name: curl, source: apt, arch: amd64, required: true, alternative: null, smoke: null}
      - {name: libvulkan1, source: apt, arch: i386, required: true, alternative: null, smoke: null}
      - {name: libvulkan1, source: apt}  # arch defaults to amd64

  - id: gaming
    name: "Gaming \"Suite\""
    description: "Games: launchers & runtimes."
    layer: profile
    profile: gaming
    selected: true
    critical: false
    expanded: false
    subgroups:
      - id: gaming-wine
        name: "Wine"
        description: "Wine layer."
        selected: true
        packages:
          - {name: wine, source: apt, arch: amd64, required: true, alternative: null, smoke: null}
          - {name: wine32, source: apt, arch: i386, required: true, alternative: null, smoke: null}
          - {name: lutris, source: apt, arch: amd64, required: false, alternative: wine, smoke: "lutris --version"}
      - id: gaming-flathub
        name: "Flathub"
        description: "Flatpak launchers."
        selected: false
        packages:
          - name: flatpak
            source: apt
          - {name: com.valvesoftware.Steam, source: flatpak, arch: amd64, required: true, alternative: null, smoke: null}

  - id: wifi
    name: "WiFi"
    description: "Wireless."
    layer: profile
    profile: wifi
    selected: true
    critical: false
    subgroups:
      - id: wifi-daemons
        name: "Daemons"
        description: "iwd."
        selected: true
        packages:
          - {name: novos-profile-wifi, source: novos-meta, arch: all, required: true, alternative: null, smoke: null}
          - {name: iwd, source: apt}
          - {name: zig, source: custom-tool, arch: amd64, required: true, alternative: null, smoke: null}
"#;

    const FIXTURE_NETINSTALL: &str = r#"# ==============================================================================
# NovOS Calamares Modular Profile & Granular Package Selection (netinstall.yaml)
# Rendered from config/packages.yaml (edit the manifest, not this file) with:
#   novos-custom-tools packages render-netinstall config/packages.yaml
# ==============================================================================
---
- name: "Gaming \"Suite\""
  description: "Games: launchers & runtimes."
  selected: true
  critical: false
  expanded: false
  subgroups:
    - name: "Wine"
      description: "Wine layer."
      selected: true
      packages:
        - wine
        - wine32:i386
        - lutris
    - name: "Flathub"
      description: "Flatpak launchers."
      selected: false
      packages:
        - flatpak

- name: "WiFi"
  description: "Wireless."
  selected: true
  critical: false
  subgroups:
    - name: "Daemons"
      description: "iwd."
      selected: true
      packages:
        - novos-profile-wifi
        - iwd
"#;

    fn repo_file(rel: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(rel)
    }

    fn fixture() -> PackageManifest {
        load_packages_text(FIXTURE).expect("fixture must load")
    }

    fn strs(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    fn has_error(errors: &[String], needle: &str) -> bool {
        errors.iter().any(|e| e.contains(needle))
    }

    #[test]
    fn parse_fixture_model() {
        let m = fixture();
        assert!(m.validate().is_empty(), "{:?}", m.validate());
        assert_eq!(m.groups.len(), 3);
        let base = &m.groups[0];
        assert_eq!(base.layer, Layer::Base);
        assert_eq!(base.profile, None);
        assert_eq!(base.packages[1].arch, Arch::I386);
        assert_eq!(base.packages[2].arch, Arch::Amd64);
        assert!(base.packages[2].required);
        let gaming = &m.groups[1];
        assert_eq!(gaming.name, "Gaming \"Suite\"");
        assert_eq!(gaming.description, "Games: launchers & runtimes.");
        assert_eq!(gaming.profile.as_deref(), Some("gaming"));
        assert_eq!(gaming.expanded, Some(false));
        let lutris = &gaming.subgroups[0].packages[2];
        assert!(!lutris.required);
        assert_eq!(lutris.alternative.as_deref(), Some("wine"));
        assert_eq!(lutris.smoke.as_deref(), Some("lutris --version"));
        let flatpak = &gaming.subgroups[1].packages[0];
        assert_eq!(flatpak.name, "flatpak");
        assert_eq!(flatpak.source, Source::Apt);
        assert_eq!(m.groups[2].expanded, None);
        assert_eq!(
            m.groups[2].subgroups[0].packages[0].source,
            Source::NovosMeta
        );
        assert_eq!(
            m.summary(),
            ManifestSummary {
                groups: 3,
                subgroups: 3,
                entries: 11,
                unique_packages: 11,
                required: 10,
                smoke_checks: 1,
            }
        );
    }

    #[test]
    fn real_manifest_loads_and_validates() {
        let m = load_packages(&repo_file("config/packages.yaml")).expect("manifest loads");
        assert!(m.validate().is_empty(), "{:?}", m.validate());
        let s = m.summary();
        assert_eq!(s.groups, 17);
        assert_eq!(s.subgroups, 38);
        assert_eq!(s.entries, 286);
        assert_eq!(s.unique_packages, 256);
        let base = m
            .list(
                &ListFilter {
                    layer: Some(Layer::Base),
                    with_arch: true,
                    ..Default::default()
                },
                &HashSet::new(),
            )
            .unwrap();
        for want in [
            "mesa-vulkan-drivers",
            "mesa-vulkan-drivers:i386",
            "libvulkan1:i386",
        ] {
            assert!(base.contains(&want.to_string()), "missing {want}");
        }
        let gaming = m
            .list(
                &ListFilter {
                    profile: Some("gaming".to_string()),
                    source: Some(Source::Apt),
                    with_arch: true,
                    ..Default::default()
                },
                &HashSet::new(),
            )
            .unwrap();
        assert!(gaming.contains(&"wine32:i386".to_string()));
        assert!(gaming.contains(&"libwine:i386".to_string()));
        assert!(!gaming.contains(&"wine32".to_string()));
        assert!(!gaming.iter().any(|p| p.starts_with("com.")));
    }

    #[test]
    fn loader_rejects_bad_structure() {
        let bad = FIXTURE
            .replace("schema_version: 1", "schema_version: 2")
            .replace(
                "{name: curl, source: apt, arch: amd64,",
                "{name: curl, arch: arm64,",
            )
            .replace(
                "    critical: true\n",
                "    critical: yes\n    colour: red\n",
            );
        let errors = load_packages_text(&bad).unwrap_err();
        assert!(
            has_error(&errors, "unsupported schema_version 2"),
            "{errors:?}"
        );
        assert!(has_error(&errors, "(curl): missing 'source'"), "{errors:?}");
        assert!(has_error(&errors, "invalid arch \"arm64\""), "{errors:?}");
        assert!(
            has_error(&errors, "'critical' must be a boolean"),
            "{errors:?}"
        );
        assert!(has_error(&errors, "unknown key \"colour\""), "{errors:?}");

        let errors = load_packages_text("schema_version: 1\ngroups:\n  - id: x\n   bad: indent\n")
            .unwrap_err();
        assert!(has_error(&errors, "line 4"), "{errors:?}");
    }

    #[test]
    fn validation_failures() {
        let mut m = fixture();
        m.groups[2].id = "gaming".to_string();
        let errors = m.validate();
        assert!(
            has_error(&errors, "duplicate group id \"gaming\""),
            "{errors:?}"
        );

        let mut m = fixture();
        m.groups[1].profile = Some("bogus".to_string());
        assert!(has_error(&m.validate(), "unknown profile \"bogus\""));

        let mut m = fixture();
        m.groups[1].profile = None;
        assert!(has_error(&m.validate(), "must declare 'profile'"));

        let mut m = fixture();
        m.groups[0].profile = Some("gaming".to_string());
        assert!(has_error(&m.validate(), "only valid on layer profile"));

        let mut m = fixture();
        m.groups[1].subgroups[1].packages[1].arch = Arch::I386;
        assert!(has_error(
            &m.validate(),
            "com.valvesoftware.Steam has arch i386 but source flatpak"
        ));

        let mut m = fixture();
        m.groups[1].subgroups[0].packages[2].alternative = Some("not-declared".to_string());
        assert!(has_error(
            &m.validate(),
            "alternative \"not-declared\" is not declared"
        ));

        let mut m = fixture();
        m.groups[0].packages.clear();
        assert!(has_error(
            &m.validate(),
            "group \"base-core\" has no packages"
        ));

        let mut m = fixture();
        m.groups[2].subgroups[0].packages.clear();
        assert!(has_error(
            &m.validate(),
            "subgroup \"wifi-daemons\" has no packages"
        ));
    }

    #[test]
    fn list_filters() {
        let m = fixture();
        let none = HashSet::new();
        let all = m.list(&ListFilter::default(), &none).unwrap();
        assert_eq!(
            all,
            strs(&[
                "curl",
                "libvulkan1",
                "wine",
                "wine32",
                "lutris",
                "flatpak",
                "com.valvesoftware.Steam",
                "novos-profile-wifi",
                "iwd",
                "zig",
            ])
        );
        let apt = ListFilter {
            source: Some(Source::Apt),
            with_arch: true,
            ..Default::default()
        };
        assert_eq!(
            m.list(&apt, &none).unwrap(),
            strs(&[
                "curl",
                "libvulkan1:i386",
                "libvulkan1",
                "wine",
                "wine32:i386",
                "lutris",
                "flatpak",
                "iwd",
            ])
        );
        let base = ListFilter {
            layer: Some(Layer::Base),
            with_arch: true,
            ..Default::default()
        };
        assert_eq!(
            m.list(&base, &none).unwrap(),
            strs(&["curl", "libvulkan1:i386", "libvulkan1"])
        );
        let flat = ListFilter {
            profile: Some("gaming".to_string()),
            source: Some(Source::Flatpak),
            ..Default::default()
        };
        assert_eq!(
            m.list(&flat, &none).unwrap(),
            strs(&["com.valvesoftware.Steam"])
        );
        let meta = ListFilter {
            source: Some(Source::NovosMeta),
            with_arch: true,
            ..Default::default()
        };
        assert_eq!(m.list(&meta, &none).unwrap(), strs(&["novos-profile-wifi"]));
        let custom = ListFilter {
            source: Some(Source::CustomTool),
            with_arch: true,
            ..Default::default()
        };
        assert_eq!(m.list(&custom, &none).unwrap(), strs(&["zig"]));
    }

    #[test]
    fn custom_tool_source_parse_and_validation() {
        assert_eq!(Source::parse("custom-tool"), Some(Source::CustomTool));
        assert_eq!(Source::CustomTool.as_str(), "custom-tool");
        assert_eq!(Source::parse("custom_tool"), None);
        let errors = load_packages_text(
            &FIXTURE.replace("{name: iwd, source: apt}", "{name: iwd, source: tarball}"),
        )
        .unwrap_err();
        assert!(
            has_error(
                &errors,
                "invalid source \"tarball\"; expected apt|flatpak|novos-meta|custom-tool"
            ),
            "{errors:?}"
        );
        let zig = fixture().groups[2].subgroups[0].packages[2].clone();
        assert_eq!(zig.source, Source::CustomTool);

        let mut m = fixture();
        m.groups[2].subgroups[0].packages[2].arch = Arch::All;
        assert!(has_error(
            &m.validate(),
            "zig has source custom-tool but arch all; custom-tool entries must be amd64"
        ));

        let mut m = fixture();
        m.groups[2].subgroups[0].packages[2].arch = Arch::I386;
        let errors = m.validate();
        assert!(has_error(
            &errors,
            "zig has source custom-tool but arch i386"
        ));
        assert!(has_error(&errors, "i386 is only valid for apt"));

        let mut m = fixture();
        m.groups[2].subgroups[0].packages[2].alternative = Some("iwd".to_string());
        assert!(has_error(
            &m.validate(),
            "zig has source custom-tool; custom-tool entries must not set 'alternative'"
        ));
    }

    #[test]
    fn custom_tool_excluded_from_apt_and_netinstall() {
        let m = fixture();
        let apt = m
            .list(
                &ListFilter {
                    source: Some(Source::Apt),
                    with_arch: true,
                    ..Default::default()
                },
                &HashSet::new(),
            )
            .unwrap();
        assert!(!apt.contains(&"zig".to_string()));
        let rendered = m.render_netinstall();
        assert!(!rendered.contains("zig"), "{rendered}");
        assert_eq!(rendered, FIXTURE_NETINSTALL);

        let real = load_packages(&repo_file("config/packages.yaml")).unwrap();
        let custom = real
            .list(
                &ListFilter {
                    source: Some(Source::CustomTool),
                    ..Default::default()
                },
                &HashSet::new(),
            )
            .unwrap();
        assert_eq!(custom, strs(&["zig"]));
        assert!(!real.render_netinstall().contains("- zig\n"));
    }

    #[test]
    fn list_health_substitution() {
        let m = fixture();
        let wine_apt = ListFilter {
            profile: Some("gaming".to_string()),
            source: Some(Source::Apt),
            with_arch: true,
            ..Default::default()
        };
        let broken = parse_health_toml(
            "# health\n[[broken]]\npackage = \"lutris\" # comment\nreason = \"crash\"\nrecheck = true\n",
        )
        .unwrap();
        assert_eq!(
            m.list(&wine_apt, &broken).unwrap(),
            strs(&["wine", "wine32:i386", "flatpak"])
        );
        let broken: HashSet<String> = ["wine".to_string()].into_iter().collect();
        let err = m.list(&wine_apt, &broken).unwrap_err();
        assert!(err.contains("wine is listed broken but declares no alternative"));
        assert!(parse_health_toml("[[broken]]\nreason = \"x\"\n").is_err());
        assert!(parse_health_toml("").unwrap().is_empty());
        let spaced = parse_health_toml("[[ broken ]]\npackage = \"lutris\"\n").unwrap();
        assert!(spaced.contains("lutris"));

        let mut m = fixture();
        m.groups[1].subgroups[0].packages[2].alternative =
            Some("com.valvesoftware.Steam".to_string());
        let broken: HashSet<String> = ["lutris".to_string()].into_iter().collect();
        let err = m.list(&wine_apt, &broken).unwrap_err();
        assert!(
            err.contains("a substitute must keep the same source"),
            "{err}"
        );
    }

    #[test]
    fn render_header_has_no_generated_marker() {
        // STEP 9C prefixes the committed file with a "# GENERATED" line and
        // strips it with `grep -v '^# GENERATED'` before diffing the render.
        let rendered = fixture().render_netinstall();
        assert!(!rendered.lines().any(|l| l.starts_with("# GENERATED")));
    }

    #[test]
    fn render_netinstall_golden_and_deterministic() {
        let m = fixture();
        let first = m.render_netinstall();
        assert_eq!(first, FIXTURE_NETINSTALL);
        assert_eq!(first, fixture().render_netinstall());
    }

    fn netinstall_index(text: &str) -> Vec<(String, String, Vec<String>)> {
        let YamlNode::List(groups) = parse_yaml_document(text).expect("netinstall parses") else {
            panic!("netinstall root must be a list");
        };
        let mut out = Vec::new();
        for g in &groups {
            let YamlNode::Map(gm) = g else {
                panic!("group map")
            };
            let gname = match get(gm, "name") {
                Some(YamlNode::Scalar(YamlScalar::Str(s))) => s.clone(),
                other => panic!("group name {other:?}"),
            };
            let Some(YamlNode::List(subs)) = get(gm, "subgroups") else {
                panic!("subgroups")
            };
            for s in subs {
                let YamlNode::Map(sm) = s else {
                    panic!("subgroup map")
                };
                let sname = match get(sm, "name") {
                    Some(YamlNode::Scalar(YamlScalar::Str(s))) => s.clone(),
                    other => panic!("subgroup name {other:?}"),
                };
                let Some(YamlNode::List(pkgs)) = get(sm, "packages") else {
                    panic!("packages")
                };
                let names = pkgs
                    .iter()
                    .map(|p| match p {
                        YamlNode::Scalar(YamlScalar::Str(s)) => s.clone(),
                        other => panic!("package {other:?}"),
                    })
                    .collect();
                out.push((gname.clone(), sname, names));
            }
        }
        out
    }

    #[test]
    fn real_render_covers_current_netinstall() {
        let m = load_packages(&repo_file("config/packages.yaml")).unwrap();
        let rendered = m.render_netinstall();
        assert_eq!(rendered, m.render_netinstall());
        let current = fs::read_to_string(repo_file("calamares/modules/netinstall.yaml")).unwrap();
        let old = netinstall_index(&current);
        let new = netinstall_index(&rendered);
        assert_eq!(old.len(), new.len());
        for ((og, os, op), (ng, ns, np)) in old.iter().zip(new.iter()) {
            assert_eq!((og, os), (ng, ns));
            for p in op {
                assert!(np.contains(p), "{og}/{os}: rendered output drops {p}");
            }
        }
        assert!(rendered.contains("        - wine32:i386\n"));
        assert!(rendered.contains("        - novos-profile-wifi\n"));
        assert!(!rendered.contains("com.valvesoftware.Steam"));
    }

    #[test]
    fn apt_cache_policy_parser() {
        let found = "wine32:i386:\n  Installed: (none)\n  Candidate: 10.0~repack-6\n  Version table:\n     10.0~repack-6 500\n        500 http://deb.debian.org/debian sid/main i386 Packages\n";
        assert_eq!(
            parse_apt_cache_policy(found),
            AptResolution::Candidate("10.0~repack-6".to_string())
        );
        let none = "rocm-smi:\n  Installed: (none)\n  Candidate: (none)\n  Version table:\n";
        assert_eq!(parse_apt_cache_policy(none), AptResolution::Unresolvable);
        assert_eq!(parse_apt_cache_policy(""), AptResolution::Unresolvable);

        let specs = strs(&["wine32:i386", "rocm-smi", "ghost"]);
        let missing = check_apt(&specs, |spec| {
            Ok(match spec {
                "wine32:i386" => found.to_string(),
                "rocm-smi" => none.to_string(),
                _ => String::new(),
            })
        })
        .unwrap();
        assert_eq!(missing, strs(&["rocm-smi", "ghost"]));
        assert!(check_apt(&specs, |_| Err("apt-cache missing".to_string())).is_err());
    }
}
