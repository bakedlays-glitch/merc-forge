//! Tauri commands for the JA2 Launcher.
//!
//! All commands are folder-relative — the launcher operates on a "modpack folder"
//! that contains: modpack.json, Ja2.ini, ja2.exe, and the Mods/ + Data/ tree.
//!
//! Two-way migration goal: the React components in frontend/ that call these
//! commands are written to be portable to MercForge later, with this command set
//! being the one piece swapped at integration time.

use crate::schema::{load_schema_for, Schema};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

// ---- Types mirroring modpack.json (forward-compatible: extra fields OK) ----

#[derive(Debug, Serialize, Deserialize)]
pub struct ModpackManifest {
    pub schema_version: u32,
    pub modpack: ModpackInfo,
    pub campaigns: Vec<Campaign>,
    #[serde(default)]
    pub mercforge_compat: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ModpackInfo {
    pub name: String,
    pub version: String,
    pub description: String,
    #[serde(default)]
    pub author: Option<String>,
    #[serde(default)]
    pub source_url: Option<String>,
    pub engine_tier: String,
    pub engine_binary: String,
    #[serde(default)]
    pub graphics_preset: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Campaign {
    pub id: String,
    pub display_name: String,
    pub vfs_config: String,
    pub description: String,
    #[serde(default)]
    pub difficulty_hint: Option<String>,
    #[serde(default)]
    pub save_dir: Option<String>,
    #[serde(default)]
    pub screenshot: Option<String>,
    #[serde(default)]
    pub source: Option<serde_json::Value>,
    #[serde(default)]
    pub requires: Vec<String>,
    #[serde(default)]
    pub estimated_playtime_hours: Option<u32>,
}

// ---- Diagnostic types (v1.5) ----

/// One mounted VFS layer parsed from `Profiles/<active>/vfs.log`.
#[derive(Debug, Serialize, Deserialize)]
pub struct VfsLayer {
    /// e.g. "Arulco Revisited", "v1.13", "Player Profile"
    pub name: String,
    /// e.g. "library", "directory"
    pub kind: String,
    /// e.g. "Mods\\Data-AR.7z", "Data-1.13"
    pub path: String,
}

/// One row of the iniErrorReport.log after classification.
#[derive(Debug, Serialize, Deserialize)]
pub struct IniError {
    /// e.g. "Mini Events Settings", "JA2 Game Settings"
    pub section: String,
    /// e.g. "MINI_EVENTS_MIN_HOURS_BETWEEN_EVENTS"
    pub key: String,
    /// Raw error message from the engine
    pub message: String,
    /// "out_of_range" | "empty_toption" | "file_not_found" | "other"
    pub kind: String,
    /// True if this is harmless first-boot noise (empty TOPTION on fresh profile).
    /// UI filters these by default; user can toggle to see them.
    pub is_first_boot_noise: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DiagnosticReport {
    pub campaign_id: String,
    pub log_dir: String,
    pub vfs_layers: Vec<VfsLayer>,
    pub errors: Vec<IniError>,
    pub first_boot_noise_count: u32,
    pub last_launch_iso: Option<String>,
}

// ---- Modpack folder detection ----

#[tauri::command]
pub fn detect_modpack_folder() -> Option<String> {
    let args: Vec<String> = std::env::args().collect();
    for (i, arg) in args.iter().enumerate() {
        if arg == "--modpack" {
            if let Some(p) = args.get(i + 1) {
                if has_modpack_json(p) {
                    return Some(p.clone());
                }
            }
        }
    }
    if let Some(arg) = args.get(1) {
        if !arg.starts_with("--") && has_modpack_json(arg) {
            return Some(arg.clone());
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if has_modpack_json(dir.to_string_lossy().as_ref()) {
                return Some(dir.to_string_lossy().to_string());
            }
        }
    }
    if let Ok(mut cwd) = std::env::current_dir() {
        for _ in 0..6 {
            if has_modpack_json(cwd.to_string_lossy().as_ref()) {
                return Some(cwd.to_string_lossy().to_string());
            }
            if !cwd.pop() {
                break;
            }
        }
    }
    None
}

fn has_modpack_json(folder: &str) -> bool {
    Path::new(folder).join("modpack.json").is_file()
}

#[tauri::command]
pub fn pick_modpack_folder(folder: String) -> Result<String, String> {
    if has_modpack_json(&folder) {
        Ok(folder)
    } else {
        Err(format!("No modpack.json found in: {}", folder))
    }
}

// ---- Manifest loading ----

#[tauri::command]
pub fn load_modpack(folder: String) -> Result<ModpackManifest, String> {
    let path = Path::new(&folder).join("modpack.json");
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("Reading {}: {}", path.display(), e))?;
    serde_json::from_str::<ModpackManifest>(&text)
        .map_err(|e| format!("Parsing modpack.json: {}", e))
}

// ---- Ja2.ini parsing + editing ----

#[tauri::command]
pub fn get_active_campaign(folder: String) -> Result<String, String> {
    let path = Path::new(&folder).join("Ja2.ini");
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("Reading {}: {}", path.display(), e))?;
    for line in text.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with(';') || trimmed.starts_with('#') {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix("VFS_CONFIG_INI") {
            let val = rest.trim_start().trim_start_matches('=').trim();
            return Ok(val.to_string());
        }
    }
    Err("No active VFS_CONFIG_INI line found in Ja2.ini".into())
}

#[tauri::command]
pub fn set_active_campaign(folder: String, vfs_config: String) -> Result<(), String> {
    let path = Path::new(&folder).join("Ja2.ini");
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("Reading {}: {}", path.display(), e))?;

    let bak = path.with_extension("ini.bak");
    if !bak.exists() {
        fs::copy(&path, &bak)
            .map_err(|e| format!("Writing backup {}: {}", bak.display(), e))?;
    }

    let mut out = String::with_capacity(text.len() + 80);
    let mut matched = false;
    for line in text.lines() {
        let trimmed = line.trim_start();
        let stripped = trimmed.trim_start_matches(';').trim_start_matches('#').trim_start();
        if let Some(rest) = stripped.strip_prefix("VFS_CONFIG_INI") {
            let val = rest.trim_start().trim_start_matches('=').trim();
            if val == vfs_config {
                out.push_str(&format!("VFS_CONFIG_INI = {}\n", vfs_config));
                matched = true;
            } else {
                if trimmed.starts_with(';') || trimmed.starts_with('#') {
                    out.push_str(line);
                    out.push('\n');
                } else {
                    out.push_str("; ");
                    out.push_str(line);
                    out.push('\n');
                }
            }
        } else {
            out.push_str(line);
            out.push('\n');
        }
    }
    if !matched {
        out.push_str(&format!("\nVFS_CONFIG_INI = {}\n", vfs_config));
    }

    fs::write(&path, out)
        .map_err(|e| format!("Writing {}: {}", path.display(), e))?;
    Ok(())
}

/// Generic Ja2.ini key setter — used by Settings tab for SCREEN_RESOLUTION,
/// SCREEN_MODE_WINDOWED, PLAY_INTRO, TOOLTIP_SCALE_FACTOR.
/// Replaces the value in-place if the key exists (active or commented),
/// appends if missing. Backup-on-first-edit.
#[tauri::command]
pub fn set_ja2ini_key(folder: String, key: String, value: String) -> Result<(), String> {
    let path = Path::new(&folder).join("Ja2.ini");
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("Reading {}: {}", path.display(), e))?;

    let bak = path.with_extension("ini.bak");
    if !bak.exists() {
        fs::copy(&path, &bak)
            .map_err(|e| format!("Writing backup {}: {}", bak.display(), e))?;
    }

    let mut out = String::with_capacity(text.len() + 64);
    let mut found = false;
    for line in text.lines() {
        let trimmed = line.trim_start();
        let stripped = trimmed.trim_start_matches(';').trim_start_matches('#').trim_start();
        // Look for "KEY =" or "KEY=" at the start of the (commented-or-not) line.
        // Don't match prefixed keys (e.g. SCREEN_RESOLUTION vs EDITOR_SCREEN_RESOLUTION).
        let mut matched_here = false;
        if let Some(rest) = stripped.strip_prefix(&key) {
            let rest_trimmed = rest.trim_start();
            if rest_trimmed.starts_with('=') {
                out.push_str(&format!("{} = {}\n", key, value));
                found = true;
                matched_here = true;
            }
        }
        if !matched_here {
            out.push_str(line);
            out.push('\n');
        }
    }
    if !found {
        out.push_str(&format!("\n{} = {}\n", key, value));
    }
    fs::write(&path, out)
        .map_err(|e| format!("Writing {}: {}", path.display(), e))?;
    Ok(())
}

/// Backwards-compat wrapper for the existing frontend code paths.
#[tauri::command]
pub fn set_screen_resolution(folder: String, code: u32) -> Result<(), String> {
    set_ja2ini_key(folder, "SCREEN_RESOLUTION".into(), code.to_string())
}

/// Read the current value of a single Ja2.ini key. Returns the first
/// uncommented occurrence, or None if absent.
#[tauri::command]
pub fn get_ja2ini_key(folder: String, key: String) -> Result<Option<String>, String> {
    let path = Path::new(&folder).join("Ja2.ini");
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("Reading {}: {}", path.display(), e))?;
    for line in text.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with(';') || trimmed.starts_with('#') {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix(&key) {
            let rest_trimmed = rest.trim_start();
            if let Some(after_eq) = rest_trimmed.strip_prefix('=') {
                return Ok(Some(after_eq.trim().to_string()));
            }
        }
    }
    Ok(None)
}

// ---- Data-User/Ja2_Options.ini editing (Settings tab "engine override" section) ----

/// Read all key/value pairs from Data-User/<ini_file>, grouped by section.
/// Returns empty map if the file doesn't exist yet (legitimate first-time state).
#[tauri::command]
pub fn read_user_options(folder: String, ini_file: String) -> Result<BTreeMap<String, BTreeMap<String, String>>, String> {
    let path = Path::new(&folder).join("Data-User").join(&ini_file);
    if !path.is_file() {
        return Ok(BTreeMap::new());
    }
    let text = fs::read_to_string(&path)
        .map_err(|e| format!("Reading {}: {}", path.display(), e))?;
    let mut out: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
    let mut current_section = String::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with(';') || trimmed.starts_with('#') {
            continue;
        }
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            current_section = trimmed[1..trimmed.len() - 1].trim().to_string();
            continue;
        }
        if let Some(eq) = trimmed.find('=') {
            let key = trimmed[..eq].trim().to_string();
            let val = trimmed[eq + 1..].trim().to_string();
            out.entry(current_section.clone()).or_default().insert(key, val);
        }
    }
    Ok(out)
}

/// Upsert a single key in Data-User/<ini_file>. Creates the file
/// (with header) if missing, the section if missing, the key if missing.
/// Idempotent. No .bak (Data-User is the user-owned scratch layer).
#[tauri::command]
pub fn write_user_option(
    folder: String,
    ini_file: String,
    section: String,
    key: String,
    value: String,
) -> Result<(), String> {
    let dir = Path::new(&folder).join("Data-User");
    fs::create_dir_all(&dir)
        .map_err(|e| format!("Creating {}: {}", dir.display(), e))?;
    let path = dir.join(&ini_file);

    let existing = if path.is_file() {
        fs::read_to_string(&path).map_err(|e| format!("Reading: {}", e))?
    } else {
        // Header for new files. Keep brief — users see this in the file.
        format!(
            ";; User-layer overrides for {}\n\
             ;; Highest precedence in the VFS merge. Safe to edit by hand.\n\
             ;; Written automatically by JA2Launcher's Settings tab.\n\n",
            ini_file
        )
    };

    // Parse into structured form, mutate, re-serialize. Preserves section order
    // by appending; replaces existing keys in place.
    let mut sections: Vec<(String, Vec<(String, String)>)> = Vec::new();
    let mut header_lines: Vec<String> = Vec::new();
    let mut current_section: Option<String> = None;
    let mut in_body = false;
    for raw_line in existing.lines() {
        let trimmed = raw_line.trim();
        if !in_body && (trimmed.is_empty() || trimmed.starts_with(';') || trimmed.starts_with('#')) {
            header_lines.push(raw_line.to_string());
            continue;
        }
        in_body = true;
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            let name = trimmed[1..trimmed.len() - 1].trim().to_string();
            current_section = Some(name.clone());
            sections.push((name, Vec::new()));
            continue;
        }
        if let Some(eq) = trimmed.find('=') {
            let k = trimmed[..eq].trim().to_string();
            let v = trimmed[eq + 1..].trim().to_string();
            if let Some(s) = &current_section {
                if let Some(last) = sections.iter_mut().rev().find(|(sn, _)| sn == s) {
                    last.1.push((k, v));
                }
            }
        }
    }

    // Find or create the target section, then upsert the key.
    let mut sect = sections.iter_mut().find(|(n, _)| n == &section);
    if sect.is_none() {
        sections.push((section.clone(), Vec::new()));
        sect = sections.last_mut();
    }
    let (_, kv) = sect.unwrap();
    if let Some(entry) = kv.iter_mut().find(|(k, _)| k == &key) {
        entry.1 = value;
    } else {
        kv.push((key, value));
    }

    let mut out = String::new();
    for h in &header_lines {
        out.push_str(h);
        out.push('\n');
    }
    if !header_lines.is_empty() && !out.ends_with("\n\n") {
        out.push('\n');
    }
    for (i, (sname, entries)) in sections.iter().enumerate() {
        if i > 0 {
            out.push('\n');
        }
        out.push_str(&format!("[{}]\n", sname));
        for (k, v) in entries {
            out.push_str(&format!("{} = {}\n", k, v));
        }
    }

    fs::write(&path, out)
        .map_err(|e| format!("Writing {}: {}", path.display(), e))?;
    Ok(())
}

/// Remove a single key from Data-User/<ini_file> (the "reset to default"
/// action). If section becomes empty, leaves it (harmless).
#[tauri::command]
pub fn delete_user_option(folder: String, ini_file: String, section: String, key: String) -> Result<(), String> {
    let path = Path::new(&folder).join("Data-User").join(&ini_file);
    if !path.is_file() {
        return Ok(());
    }
    let text = fs::read_to_string(&path).map_err(|e| format!("Reading: {}", e))?;
    let mut out = String::with_capacity(text.len());
    let mut current_section = String::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            current_section = trimmed[1..trimmed.len() - 1].trim().to_string();
            out.push_str(line);
            out.push('\n');
            continue;
        }
        // Skip the matching key in the matching section
        if current_section == section {
            if let Some(eq) = trimmed.find('=') {
                let k = trimmed[..eq].trim();
                if k == key {
                    continue;
                }
            }
        }
        out.push_str(line);
        out.push('\n');
    }
    fs::write(&path, out).map_err(|e| format!("Writing: {}", e))?;
    Ok(())
}

// ---- Diagnostic readers (Diagnostic tab) ----

/// Build the diagnostic report for a campaign — parses vfs.log and iniErrorReport.log.
/// `save_dir` is the path relative to the modpack folder (e.g. "Profiles/AR").
#[tauri::command]
pub fn build_diagnostic_report(folder: String, save_dir: String) -> Result<DiagnosticReport, String> {
    let log_dir = Path::new(&folder).join(&save_dir);
    let campaign_id = Path::new(&save_dir)
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| save_dir.clone());

    let vfs_layers = parse_vfs_log(&log_dir.join("vfs.log")).unwrap_or_default();
    let (errors, first_boot_noise_count) =
        parse_ini_error_report(&log_dir.join("iniErrorReport.log")).unwrap_or((Vec::new(), 0));
    let last_launch_iso = read_log_timestamp(&log_dir.join("vfs.log"));

    Ok(DiagnosticReport {
        campaign_id,
        log_dir: log_dir.to_string_lossy().to_string(),
        vfs_layers,
        errors,
        first_boot_noise_count,
        last_launch_iso,
    })
}

fn parse_vfs_log(path: &Path) -> Option<Vec<VfsLayer>> {
    if !path.is_file() {
        return None;
    }
    let text = fs::read_to_string(path).ok()?;
    let mut layers = Vec::new();
    let mut current_profile: Option<String> = None;

    for raw in text.lines() {
        // Lines look like:
        //   [0.476243] :   Reading profile : SLF Libs
        //   [0.476248] :     library : "Data\Ambient.slf"
        //   [0.600463] :     directory : "Data"
        let Some(after_bracket) = raw.split("] :").nth(1) else { continue };
        let payload = after_bracket.trim();

        if let Some(rest) = payload.strip_prefix("Reading profile :") {
            current_profile = Some(rest.trim().to_string());
        } else if let Some(rest) = payload.strip_prefix("library :") {
            if let Some(prof) = &current_profile {
                layers.push(VfsLayer {
                    name: prof.clone(),
                    kind: "library".into(),
                    path: rest.trim().trim_matches('"').to_string(),
                });
            }
        } else if let Some(rest) = payload.strip_prefix("directory :") {
            if let Some(prof) = &current_profile {
                layers.push(VfsLayer {
                    name: prof.clone(),
                    kind: "directory".into(),
                    path: rest.trim().trim_matches('"').to_string(),
                });
            }
        }
    }
    Some(layers)
}

fn parse_ini_error_report(path: &Path) -> Option<(Vec<IniError>, u32)> {
    if !path.is_file() {
        return None;
    }
    let text = fs::read_to_string(path).ok()?;
    let mut errors = Vec::new();
    let mut noise_count = 0_u32;

    for raw in text.lines() {
        let trimmed = raw.trim();
        if trimmed.is_empty() || trimmed.starts_with("***") || trimmed.starts_with('[') {
            continue;
        }
        // Look for "The value [section][KEY] = "..." in file [...]. ..."
        let Some(start) = trimmed.find("The value [") else { continue };
        let rest = &trimmed[start + "The value [".len()..];
        let Some(close_section) = rest.find("][") else { continue };
        let section = rest[..close_section].to_string();
        let after_section = &rest[close_section + 2..];
        let Some(close_key) = after_section.find(']') else { continue };
        let key = after_section[..close_key].to_string();
        let msg = trimmed.to_string();

        let (kind, is_noise) = if msg.contains("outside the valid range") {
            ("out_of_range".into(), false)
        } else if msg.contains("neither TRUE nor FALSE") && msg.contains("= \"\"") {
            noise_count += 1;
            ("empty_toption".into(), true)
        } else if msg.contains("Error when opening file") {
            ("file_not_found".into(), false)
        } else {
            ("other".into(), false)
        };

        errors.push(IniError {
            section,
            key,
            message: msg,
            kind,
            is_first_boot_noise: is_noise,
        });
    }
    Some((errors, noise_count))
}

fn read_log_timestamp(path: &Path) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    // First line shape: " *** Mon May 25 15:18:21 2026 *** "
    text.lines()
        .find(|l| l.contains("***"))
        .map(|l| l.trim().trim_matches('*').trim().to_string())
}

/// Open the Profiles/<id>/ folder in Windows Explorer (for power users
/// who want to look at the logs directly).
#[tauri::command]
pub fn open_log_folder(folder: String, save_dir: String) -> Result<(), String> {
    let target = Path::new(&folder).join(&save_dir);
    if !target.is_dir() {
        return Err(format!("Folder does not exist: {}", target.display()));
    }
    Command::new("explorer.exe")
        .arg(target.to_string_lossy().as_ref())
        .spawn()
        .map_err(|e| format!("Failed to open Explorer: {}", e))?;
    Ok(())
}

// ---- Settings schema (INIEditor*.xml-driven, v1.6) ----

/// List which INI files we have schemas for, and which are available
/// in the modpack folder vs embedded fallback.
#[derive(Debug, Serialize)]
pub struct SchemaAvailability {
    pub ini_file: String,
    pub xml_filename: String,
    pub in_modpack: bool,
    pub embedded_available: bool,
}

#[tauri::command]
pub fn list_schemas(folder: String) -> Vec<SchemaAvailability> {
    let known = [
        // Official JA2 1.13 schemas
        ("Ja2.ini", "INIEditorJA2.xml"),
        ("Ja2_Options.ini", "INIEditorJA2Options.xml"),
        ("APBPConstants.ini", "INIEditorAPBPConstants.xml"),
        // Auto-extracted schemas
        ("AI.ini", "INIEditorAI.xml"),
        ("CTHConstants.ini", "INIEditorCTHConstants.xml"),
        ("Creatures_Settings.INI", "INIEditorCreatures_Settings.xml"),
        ("Helicopter_Settings.INI", "INIEditorHelicopter_Settings.xml"),
        ("IntroVideos.ini", "INIEditorIntroVideos.xml"),
        ("Item_Settings.ini", "INIEditorItem_Settings.xml"),
        ("Mod_Settings.ini", "INIEditorMod_Settings.xml"),
        ("Morale_Settings.INI", "INIEditorMorale_Settings.xml"),
        ("RebelCommand_Settings.ini", "INIEditorRebelCommand_Settings.xml"),
        ("Reputation_Settings.INI", "INIEditorReputation_Settings.xml"),
        ("Skills_Settings.INI", "INIEditorSkills_Settings.xml"),
        ("Taunts_Settings.INI", "INIEditorTaunts_Settings.xml"),
    ];
    known
        .iter()
        .map(|(ini, xml)| SchemaAvailability {
            ini_file: ini.to_string(),
            xml_filename: xml.to_string(),
            in_modpack: Path::new(&folder).join(xml).is_file(),
            embedded_available: crate::schema::embedded_schema_bytes(ini).is_some(),
        })
        .collect()
}

/// Parse one schema (modpack copy preferred, embedded fallback).
#[tauri::command]
pub fn load_schema(folder: String, ini_file: String) -> Result<Schema, String> {
    load_schema_for(Path::new(&folder), &ini_file)
}

/// Read the effective value of a setting after the VFS merge.
/// Layering for Ja2_Options.ini and APBPConstants.ini:
///   1. Data-User/<ini_file>     (user override — top precedence)
///   2. Data-1.13/<ini_file>     (modpack default)
///   3. None (caller falls back to schema's default_value)
/// For Ja2.ini, only the install root is checked.
#[derive(Debug, Serialize)]
pub struct EffectiveValue {
    pub value: Option<String>,
    /// "data_user" | "data_113" | "ja2_ini" | "none"
    pub source: String,
}

#[tauri::command]
pub fn read_effective_setting(
    folder: String,
    ini_file: String,
    section: String,
    key: String,
) -> Result<EffectiveValue, String> {
    if ini_file == "Ja2.ini" {
        let val = read_ini_key(&Path::new(&folder).join("Ja2.ini"), &section, &key)?;
        return Ok(EffectiveValue {
            value: val,
            source: "ja2_ini".into(),
        });
    }

    // Data-User first (case-insensitive filename match — Ja2_Options.INI vs .ini varies)
    if let Some(user_path) = find_ini_case_insensitive(&Path::new(&folder).join("Data-User"), &ini_file) {
        if let Some(v) = read_ini_key(&user_path, &section, &key)? {
            return Ok(EffectiveValue {
                value: Some(v),
                source: "data_user".into(),
            });
        }
    }
    // Then Data-1.13
    if let Some(v113_path) = find_ini_case_insensitive(&Path::new(&folder).join("Data-1.13"), &ini_file) {
        if let Some(v) = read_ini_key(&v113_path, &section, &key)? {
            return Ok(EffectiveValue {
                value: Some(v),
                source: "data_113".into(),
            });
        }
    }
    Ok(EffectiveValue {
        value: None,
        source: "none".into(),
    })
}

/// Some INIs ship as .INI uppercase, others as .ini lowercase. Match either
/// when looking up an existing file (writes still use the supplied case).
fn find_ini_case_insensitive(dir: &Path, filename: &str) -> Option<PathBuf> {
    let exact = dir.join(filename);
    if exact.is_file() {
        return Some(exact);
    }
    if let Ok(entries) = fs::read_dir(dir) {
        for e in entries.flatten() {
            if let Some(name) = e.file_name().to_str() {
                if name.eq_ignore_ascii_case(filename) {
                    return Some(e.path());
                }
            }
        }
    }
    None
}

/// Read a single key under a section from an INI file. Returns None if missing.
/// Section + key are case-sensitive (INI convention).
fn read_ini_key(path: &Path, section: &str, key: &str) -> Result<Option<String>, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("Reading {}: {}", path.display(), e))?;
    let mut current = String::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with(';') || trimmed.starts_with('#') {
            continue;
        }
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            current = trimmed[1..trimmed.len() - 1].trim().to_string();
            continue;
        }
        if current == section {
            if let Some(eq) = trimmed.find('=') {
                let k = trimmed[..eq].trim();
                if k == key {
                    return Ok(Some(trimmed[eq + 1..].trim().to_string()));
                }
            }
        }
    }
    Ok(None)
}

// ---- Presets (v1.8 — Phase B) ----

/// One key write that a preset wants to perform.
/// target = "user" → writes to Data-User/<ini_file> (engine merges as top layer)
/// target = "ja2_ini" → writes to Ja2.ini at install root (top-level config)
#[derive(Debug, Deserialize)]
pub struct PresetChange {
    pub ini_file: String,
    pub section: String,
    pub key: String,
    pub value: String,
    /// "user" (Data-User override) or "ja2_ini" (root Ja2.ini). Defaults to "user".
    #[serde(default = "default_target")]
    pub target: String,
}

fn default_target() -> String {
    "user".to_string()
}

/// Apply a batch of preset changes. Each one either upserts a Data-User
/// override or edits Ja2.ini directly. Returns the count applied.
/// Skips invalid changes (logs but doesn't fail the batch).
#[tauri::command]
pub fn apply_preset_changes(folder: String, changes: Vec<PresetChange>) -> Result<u32, String> {
    let mut applied = 0;
    for c in changes {
        let result = match c.target.as_str() {
            "ja2_ini" => set_ja2ini_key(folder.clone(), c.key.clone(), c.value.clone()),
            _ => write_user_option(
                folder.clone(),
                c.ini_file.clone(),
                c.section.clone(),
                c.key.clone(),
                c.value.clone(),
            ),
        };
        if result.is_ok() {
            applied += 1;
        }
    }
    Ok(applied)
}

/// Delete every Data-User/*.ini file (the "Default 1.13" reset). Returns count deleted.
/// Does NOT touch Ja2.ini — that's still the player's chosen install settings.
#[tauri::command]
pub fn clear_all_overrides(folder: String) -> Result<u32, String> {
    let dir = Path::new(&folder).join("Data-User");
    if !dir.is_dir() {
        return Ok(0);
    }
    let mut deleted = 0;
    let entries = fs::read_dir(&dir).map_err(|e| format!("Reading Data-User: {}", e))?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_file() {
            if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                if ext.eq_ignore_ascii_case("ini") {
                    fs::remove_file(&path).map_err(|e| format!("Deleting {}: {}", path.display(), e))?;
                    deleted += 1;
                }
            }
        }
    }
    Ok(deleted)
}

// ---- Launching the game ----

#[tauri::command]
pub fn launch_game(folder: String, exe_name: String) -> Result<u32, String> {
    let exe = PathBuf::from(&folder).join(&exe_name);
    if !exe.is_file() {
        return Err(format!("Engine binary not found: {}", exe.display()));
    }
    let child = Command::new(&exe)
        .current_dir(&folder)
        .spawn()
        .map_err(|e| format!("Failed to spawn {}: {}", exe.display(), e))?;
    Ok(child.id())
}
