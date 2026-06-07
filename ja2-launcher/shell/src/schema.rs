//! INIEditor*.xml schema parser.
//!
//! The JA2 1.13 INI Editor ships these XML files with rich per-key metadata
//! (datatype, min/max/default, multi-language descriptions, enum values).
//! We parse them to drive the launcher's settings browser, so every UI
//! widget is schema-derived rather than hand-coded.
//!
//! Files (all in modpack root if shipped, embedded copies as fallback):
//!   - INIEditorJA2.xml             (Ja2.ini, ~20 keys)
//!   - INIEditorJA2Options.xml      (Data-1.13/Ja2_Options.ini, 813 keys, 53 sections, 1.7 MB)
//!   - INIEditorAPBPConstants.xml   (Data-1.13/APBPConstants.ini, ~50 keys)
//!
//! Encoding: UTF-16 LE with BOM. We decode to UTF-8 before parsing.

use serde::Serialize;
use std::path::Path;

#[derive(Debug, Serialize)]
pub struct Schema {
    /// Logical name: "Ja2.ini" | "Ja2_Options.ini" | "APBPConstants.ini"
    pub ini_file: String,
    /// Where this schema was loaded from: "modpack" | "embedded"
    pub source: String,
    pub description: String,
    pub sections: Vec<Section>,
}

#[derive(Debug, Serialize)]
pub struct Section {
    pub name: String,
    pub description: String,
    pub properties: Vec<Property>,
}

#[derive(Debug, Serialize)]
pub struct Property {
    pub name: String,
    /// "numeric" | "boolean" | "string" | "list" | "array" | other
    pub datatype: String,
    pub default_value: Option<String>,
    pub min_value: Option<String>,
    pub max_value: Option<String>,
    pub interval: Option<String>,
    /// Pre-1.13 "Vanilla JA2 - X" value if the INI comment mentioned one.
    /// Lets the UI show "Modpack default: 30 · Vanilla JA2: 25" alongside.
    pub vanilla_value: Option<String>,
    pub description: String,
    /// For datatype="list" — the legal enum values.
    pub list_values: Vec<String>,
}

/// Read a file, handling UTF-16 LE BOM if present.
pub fn read_xml_to_utf8(bytes: &[u8]) -> Result<String, String> {
    if bytes.starts_with(&[0xFF, 0xFE]) {
        // UTF-16 LE
        let body = &bytes[2..];
        if body.len() % 2 != 0 {
            return Err("UTF-16 byte length is odd".into());
        }
        let u16s: Vec<u16> = body
            .chunks_exact(2)
            .map(|c| u16::from_le_bytes([c[0], c[1]]))
            .collect();
        String::from_utf16(&u16s).map_err(|e| format!("UTF-16 decode: {}", e))
    } else if bytes.starts_with(&[0xFE, 0xFF]) {
        // UTF-16 BE — rare for JA2 files but handle it
        let body = &bytes[2..];
        if body.len() % 2 != 0 {
            return Err("UTF-16 BE byte length is odd".into());
        }
        let u16s: Vec<u16> = body
            .chunks_exact(2)
            .map(|c| u16::from_be_bytes([c[0], c[1]]))
            .collect();
        String::from_utf16(&u16s).map_err(|e| format!("UTF-16 BE decode: {}", e))
    } else {
        String::from_utf8(bytes.to_vec()).map_err(|e| format!("UTF-8 decode: {}", e))
    }
}

/// Parse the schema XML text into structured form.
/// Picks Description_ENG when available, falls back to Description_GER then any other.
pub fn parse_schema(text: &str, ini_file: &str, source: &str) -> Result<Schema, String> {
    let doc = roxmltree::Document::parse(text).map_err(|e| format!("XML parse: {}", e))?;
    let root = doc.root_element();
    let description = pick_description(&root);

    let mut sections = Vec::new();
    if let Some(sections_node) = root.children().find(|n| n.has_tag_name("Sections")) {
        for sect_node in sections_node.children().filter(|n| n.has_tag_name("Section")) {
            let name = sect_node.attribute("name").unwrap_or("").to_string();
            let sect_desc = pick_description(&sect_node);
            let mut props = Vec::new();
            if let Some(props_node) = sect_node.children().find(|n| n.has_tag_name("Properties")) {
                for prop_node in props_node.children().filter(|n| n.has_tag_name("Property")) {
                    props.push(parse_property(&prop_node));
                }
            }
            sections.push(Section {
                name,
                description: sect_desc,
                properties: props,
            });
        }
    }

    Ok(Schema {
        ini_file: ini_file.to_string(),
        source: source.to_string(),
        description,
        sections,
    })
}

fn parse_property(node: &roxmltree::Node) -> Property {
    // Common attrs across all datatypes
    let name = node.attribute("name").unwrap_or("").to_string();
    let datatype = node.attribute("datatype").unwrap_or("").to_string();
    let default_value = node.attribute("defaultvalue").map(|s| s.to_string());
    let min_value = node.attribute("minvalue").map(|s| s.to_string());
    let max_value = node.attribute("maxvalue").map(|s| s.to_string());
    let interval = node.attribute("interval").map(|s| s.to_string());
    let vanilla_value = node.attribute("vanillavalue").map(|s| s.to_string());
    let description = pick_description(node);

    // For datatype=list, the legal values live in child <ListValue>/<Value> nodes.
    // Schema varies per file; collect from a few likely tag names.
    let mut list_values = Vec::new();
    for child in node.children().filter(|n| n.is_element()) {
        let tag = child.tag_name().name();
        if tag.eq_ignore_ascii_case("ListValues")
            || tag.eq_ignore_ascii_case("Values")
            || tag.eq_ignore_ascii_case("EnumValues")
        {
            for v in child.children().filter(|n| n.is_element()) {
                if let Some(name_attr) = v.attribute("name") {
                    list_values.push(name_attr.to_string());
                } else if let Some(text) = v.text() {
                    let t = text.trim();
                    if !t.is_empty() {
                        list_values.push(t.to_string());
                    }
                }
            }
        }
    }

    Property {
        name,
        datatype,
        default_value,
        min_value,
        max_value,
        interval,
        vanilla_value,
        description,
        list_values,
    }
}

/// Pick the best available description. Priority: ENG → GER → first non-empty.
fn pick_description(node: &roxmltree::Node) -> String {
    let mut candidates: Vec<(&str, String)> = Vec::new();
    for child in node.children().filter(|n| n.is_element()) {
        let tag = child.tag_name().name();
        if tag.starts_with("Description_") {
            let text = child.text().unwrap_or("").trim().to_string();
            if !text.is_empty() {
                candidates.push((tag, text));
            }
        }
    }
    if let Some((_, t)) = candidates.iter().find(|(tag, _)| *tag == "Description_ENG") {
        return t.clone();
    }
    if let Some((_, t)) = candidates.iter().find(|(tag, _)| *tag == "Description_GER") {
        return t.clone();
    }
    candidates
        .into_iter()
        .next()
        .map(|(_, t)| t)
        .unwrap_or_default()
}

// ---- Embedded schemas (compiled into the binary as fallback) ----

// Official JA2 1.13 schemas (UTF-16 LE BOM)
const EMBEDDED_JA2_INI: &[u8] = include_bytes!("../embedded_schemas/INIEditorJA2.xml");
const EMBEDDED_OPTIONS: &[u8] = include_bytes!("../embedded_schemas/INIEditorJA2Options.xml");
const EMBEDDED_APBP: &[u8] = include_bytes!("../embedded_schemas/INIEditorAPBPConstants.xml");

// Auto-extracted schemas for the other 12 INIs (UTF-8, generated by tools/build_ini_schemas.py)
const EMBEDDED_AI: &[u8] = include_bytes!("../embedded_schemas/INIEditorAI.xml");
const EMBEDDED_CTH: &[u8] = include_bytes!("../embedded_schemas/INIEditorCTHConstants.xml");
const EMBEDDED_CREATURES: &[u8] = include_bytes!("../embedded_schemas/INIEditorCreatures_Settings.xml");
const EMBEDDED_HELICOPTER: &[u8] = include_bytes!("../embedded_schemas/INIEditorHelicopter_Settings.xml");
const EMBEDDED_INTROVIDEOS: &[u8] = include_bytes!("../embedded_schemas/INIEditorIntroVideos.xml");
const EMBEDDED_ITEM: &[u8] = include_bytes!("../embedded_schemas/INIEditorItem_Settings.xml");
const EMBEDDED_MOD: &[u8] = include_bytes!("../embedded_schemas/INIEditorMod_Settings.xml");
const EMBEDDED_MORALE: &[u8] = include_bytes!("../embedded_schemas/INIEditorMorale_Settings.xml");
const EMBEDDED_REBELCMD: &[u8] = include_bytes!("../embedded_schemas/INIEditorRebelCommand_Settings.xml");
const EMBEDDED_REPUTATION: &[u8] = include_bytes!("../embedded_schemas/INIEditorReputation_Settings.xml");
const EMBEDDED_SKILLS: &[u8] = include_bytes!("../embedded_schemas/INIEditorSkills_Settings.xml");
const EMBEDDED_TAUNTS: &[u8] = include_bytes!("../embedded_schemas/INIEditorTaunts_Settings.xml");

/// Return the embedded schema bytes for a given INI file name.
/// Returns None for unknown files.
pub fn embedded_schema_bytes(ini_file: &str) -> Option<&'static [u8]> {
    match ini_file {
        "Ja2.ini" => Some(EMBEDDED_JA2_INI),
        "Ja2_Options.ini" => Some(EMBEDDED_OPTIONS),
        "APBPConstants.ini" => Some(EMBEDDED_APBP),
        "AI.ini" => Some(EMBEDDED_AI),
        "CTHConstants.ini" => Some(EMBEDDED_CTH),
        "Creatures_Settings.INI" => Some(EMBEDDED_CREATURES),
        "Helicopter_Settings.INI" => Some(EMBEDDED_HELICOPTER),
        "IntroVideos.ini" => Some(EMBEDDED_INTROVIDEOS),
        "Item_Settings.ini" => Some(EMBEDDED_ITEM),
        "Mod_Settings.ini" => Some(EMBEDDED_MOD),
        "Morale_Settings.INI" => Some(EMBEDDED_MORALE),
        "RebelCommand_Settings.ini" => Some(EMBEDDED_REBELCMD),
        "Reputation_Settings.INI" => Some(EMBEDDED_REPUTATION),
        "Skills_Settings.INI" => Some(EMBEDDED_SKILLS),
        "Taunts_Settings.INI" => Some(EMBEDDED_TAUNTS),
        _ => None,
    }
}

/// Map an INI file name to its schema XML filename in the modpack folder.
pub fn schema_filename_for(ini_file: &str) -> Option<&'static str> {
    match ini_file {
        "Ja2.ini" => Some("INIEditorJA2.xml"),
        "Ja2_Options.ini" => Some("INIEditorJA2Options.xml"),
        "APBPConstants.ini" => Some("INIEditorAPBPConstants.xml"),
        "AI.ini" => Some("INIEditorAI.xml"),
        "CTHConstants.ini" => Some("INIEditorCTHConstants.xml"),
        "Creatures_Settings.INI" => Some("INIEditorCreatures_Settings.xml"),
        "Helicopter_Settings.INI" => Some("INIEditorHelicopter_Settings.xml"),
        "IntroVideos.ini" => Some("INIEditorIntroVideos.xml"),
        "Item_Settings.ini" => Some("INIEditorItem_Settings.xml"),
        "Mod_Settings.ini" => Some("INIEditorMod_Settings.xml"),
        "Morale_Settings.INI" => Some("INIEditorMorale_Settings.xml"),
        "RebelCommand_Settings.ini" => Some("INIEditorRebelCommand_Settings.xml"),
        "Reputation_Settings.INI" => Some("INIEditorReputation_Settings.xml"),
        "Skills_Settings.INI" => Some("INIEditorSkills_Settings.xml"),
        "Taunts_Settings.INI" => Some("INIEditorTaunts_Settings.xml"),
        _ => None,
    }
}

/// Load a schema for one INI file: prefer modpack copy, fall back to embedded.
pub fn load_schema_for(
    modpack_folder: &Path,
    ini_file: &str,
) -> Result<Schema, String> {
    let xml_filename = schema_filename_for(ini_file)
        .ok_or_else(|| format!("No schema mapping for INI file: {}", ini_file))?;

    // Try modpack folder first
    let modpack_path = modpack_folder.join(xml_filename);
    if modpack_path.is_file() {
        let bytes = std::fs::read(&modpack_path).map_err(|e| format!("Reading modpack schema: {}", e))?;
        let utf8 = read_xml_to_utf8(&bytes)?;
        return parse_schema(&utf8, ini_file, "modpack");
    }

    // Fall back to embedded
    let bytes = embedded_schema_bytes(ini_file)
        .ok_or_else(|| format!("No embedded schema for: {}", ini_file))?;
    let utf8 = read_xml_to_utf8(bytes)?;
    parse_schema(&utf8, ini_file, "embedded")
}
