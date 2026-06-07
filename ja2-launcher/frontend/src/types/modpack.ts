// TypeScript mirror of modpack.json schema + Rust diagnostic types.
// Keep in sync if either side changes.

export interface ModpackManifest {
  schema_version: number;
  modpack: ModpackInfo;
  campaigns: Campaign[];
  mercforge_compat?: Record<string, unknown>;
}

export interface ModpackInfo {
  name: string;
  version: string;
  description: string;
  author?: string;
  source_url?: string;
  engine_tier: string;
  engine_binary: string;
  graphics_preset?: string;
}

export interface Campaign {
  id: string;
  display_name: string;
  vfs_config: string;
  description: string;
  difficulty_hint?: string;
  save_dir?: string;
  screenshot?: string;
  source?: {
    mod_name?: string;
    mod_id?: number | null;
    version?: string;
    url?: string | null;
  };
  requires?: string[];
  estimated_playtime_hours?: number;
}

// ---- Diagnostic types (v1.5) ----

export interface VfsLayer {
  name: string;             // e.g. "Arulco Revisited"
  kind: "library" | "directory";
  path: string;             // e.g. "Mods\\Data-AR.7z" or "Data-1.13"
}

export interface IniError {
  section: string;
  key: string;
  message: string;
  kind: "out_of_range" | "empty_toption" | "file_not_found" | "other";
  is_first_boot_noise: boolean;
}

export interface DiagnosticReport {
  campaign_id: string;
  log_dir: string;
  vfs_layers: VfsLayer[];
  errors: IniError[];
  first_boot_noise_count: number;
  last_launch_iso: string | null;
}

// ---- Settings types (v1.5) ----

/// Section → key → value, mirroring Rust's BTreeMap<String, BTreeMap<...>>.
export type UserOptions = Record<string, Record<string, string>>;

// ---- Schema types (v1.6 — INIEditor*.xml-driven settings browser) ----

export interface SchemaAvailability {
  ini_file: string;            // "Ja2_Options.ini"
  xml_filename: string;        // "INIEditorJA2Options.xml"
  in_modpack: boolean;         // schema XML present in modpack root
  embedded_available: boolean; // launcher binary has a fallback copy
}

export interface SchemaProperty {
  name: string;                // "MAX_NUMBER_PLAYER_MERCS"
  datatype: string;            // "numeric" | "boolean" | "string" | "list" | ...
  default_value: string | null;
  min_value: string | null;
  max_value: string | null;
  interval: string | null;
  vanilla_value: string | null; // "Vanilla JA2 - X" from INI comments (auto-extracted)
  description: string;         // multi-paragraph, may contain newlines
  list_values: string[];       // populated when datatype = "list"
}

export interface SchemaSection {
  name: string;                // "System Limit Settings"
  description: string;
  properties: SchemaProperty[];
}

export interface SchemaDoc {
  ini_file: string;
  source: "modpack" | "embedded";
  description: string;
  sections: SchemaSection[];
}

export interface EffectiveValue {
  value: string | null;
  source: "data_user" | "data_113" | "ja2_ini" | "none";
}

// ---- Presets (v1.8 — Phase B) ----

export interface PresetChange {
  ini_file: string;
  section: string;
  key: string;
  value: string;
  /// "user" (writes to Data-User/<ini_file>) or "ja2_ini" (writes Ja2.ini root).
  target?: "user" | "ja2_ini";
}

export interface Preset {
  id: string;
  name: string;
  description: string;
  tags: string[];           // e.g. ["beginner", "vanilla", "challenge"]
  is_reset?: boolean;       // if true, click clears all Data-User overrides (ignores `changes`)
  changes: PresetChange[];
}
