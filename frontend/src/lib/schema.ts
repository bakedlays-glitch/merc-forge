/**
 * TypeScript mirrors of the sidecar's pydantic models.
 *
 * Source of truth lives in:
 *   sidecar/mercwizard_core/models.py
 *
 * Keep these in sync manually — there's no auto-codegen in v1. The mismatch
 * cost is small because every field is loosely-typed JSON in the response
 * anyway; this file is mainly for editor autocomplete + structural review.
 */

// ───────────────────────────────────────────────────────────────────────
//  Core merc model — matches Merc in models.py
// ───────────────────────────────────────────────────────────────────────

export type ProfileType = 0 | 1 | 2 | 3 | 4 | 5 | 6;

// Palette codes — vanilla 1.13 has a fixed set but mods (The Wasteland uses
// BLACKSHIRT, etc.) add custom codes. Typed as plain string so any mod-defined
// code parses; UI dropdowns curate the vanilla list as suggestions.
export type PantsCode = string;
export type VestCode = string;
export type SkinCode = string;
export type HairCode = string;

export interface Merc {
  // Identity & face routing
  uiIndex: number;
  ubFaceIndex: number;
  Type: ProfileType;
  zName: string;
  zNickname: string;
  bSex: 0 | 1;
  ubBodyType: number;
  uiBodyTypeSubFlags: number;
  usVoiceIndex: number;
  bRace: number;
  bNationality: number;

  // Portrait coordinates (48×43 space)
  usEyesX: number;
  usEyesY: number;
  usMouthX: number;
  usMouthY: number;
  uiEyeDelay: number;
  uiMouthDelay: number;
  uiBlinkFrequency: number;
  uiExpressionFrequency: number;

  // Appearance
  PANTS: PantsCode;
  VEST: VestCode;
  SKIN: SkinCode;
  HAIR: HairCode;

  // Attributes
  bLifeMax: number; bLife: number;
  bStrength: number; bAgility: number; bDexterity: number; bWisdom: number;
  bExpLevel: number;
  bEvolution: number;
  bMarksmanship: number; bExplosive: number; bLeadership: number;
  bMedical: number; bMechanical: number;
  fRegresses: 0 | 1;

  // Growth modifiers
  GrowthModifierLife: number;
  GrowthModifierStrength: number; GrowthModifierAgility: number;
  GrowthModifierDexterity: number; GrowthModifierWisdom: number;
  GrowthModifierMarksmanship: number; GrowthModifierExplosive: number;
  GrowthModifierLeadership: number; GrowthModifierMedical: number;
  GrowthModifierMechanical: number; GrowthModifierExpLevel: number;

  // Traits
  bOldSkillTrait: number; bOldSkillTrait2: number;
  // bNewSkillTrait5-30: forward-compat slots, default 0
  bNewSkillTrait5: number; bNewSkillTrait6: number; bNewSkillTrait7: number; bNewSkillTrait8: number;
  bNewSkillTrait9: number; bNewSkillTrait10: number; bNewSkillTrait11: number; bNewSkillTrait12: number;
  bNewSkillTrait13: number; bNewSkillTrait14: number; bNewSkillTrait15: number; bNewSkillTrait16: number;
  bNewSkillTrait17: number; bNewSkillTrait18: number; bNewSkillTrait19: number; bNewSkillTrait20: number;
  bNewSkillTrait21: number; bNewSkillTrait22: number; bNewSkillTrait23: number; bNewSkillTrait24: number;
  bNewSkillTrait25: number; bNewSkillTrait26: number; bNewSkillTrait27: number; bNewSkillTrait28: number;
  bNewSkillTrait29: number; bNewSkillTrait30: number;
  bNewSkillTrait1: number; bNewSkillTrait2: number;
  bNewSkillTrait3: number; bNewSkillTrait4: number;

  // Background
  usBackground: number;

  // Personality
  bAttitude: number; bCharacterTrait: number; bDisability: number;
  ubNeedForSleep: number;
  bReputationTolerance: number; bDeathRate: number;
  bAppearance: number; bAppearanceCareLevel: number;
  bRefinement: number; bRefinementCareLevel: number;
  bHatedNationality: number; bHatedNationalityCareLevel: number;
  bRacist: number; bSexist: number;
  fGoodGuy: 0 | 1;

  // Relationships
  bBuddy1: number; bBuddy2: number; bBuddy3: number; bBuddy4: number; bBuddy5: number;
  bHated1: number; bHatedTime1: number;
  bHated2: number; bHatedTime2: number;
  bHated3: number; bHatedTime3: number;
  bHated4: number; bHatedTime4: number;
  bHated5: number; bHatedTime5: number;
  bLearnToLike: number; bLearnToLikeTime: number;
  bLearnToHate: number; bLearnToHateTime: number;

  // Economy
  sSalary: number; uiWeeklySalary: number; uiBiWeeklySalary: number;
  bMedicalDeposit: 0 | 1; sMedicalDepositAmount: number;
  usOptionalGearCost: number;
  bArmourAttractiveness: number; bMainGunAttractiveness: number;

  // Dialogue
  usApproachFactorFriendly: number; usApproachFactorDirect: number;
  usApproachFactorThreaten: number; usApproachFactorRecruit: number;

  // Location
  sSectorX: number; sSectorY: number; sSectorZ: number;
  ubCivilianGroup: number; bTown: number; bTownAttachment: number;

  // Free text (EDT-bound)
  biographyText: string;
  additionalInfoText: string;
}

// ───────────────────────────────────────────────────────────────────────
//  Gear
// ───────────────────────────────────────────────────────────────────────

export interface GearKit {
  mGearKitName: string;
  mHelmet: number;
  mVest: number;
  mLeg: number;
  mWeapon: number;
  mBig0: number; mBig0Status: number; mBig0Quantity: number;
  mBig1: number; mBig1Status: number; mBig1Quantity: number;
  mBig2: number; mBig2Status: number; mBig2Quantity: number;
  mBig3: number; mBig3Status: number; mBig3Quantity: number;
  mSmall0: number; mSmall1: number; mSmall2: number; mSmall3: number;
  mSmall4: number; mSmall5: number; mSmall6: number; mSmall7: number;
  mPriceMod: number;
  mAbsolutePrice: -1;  // ALWAYS -1 — engine auto-calculates
}

export interface Gear {
  mIndex: number;
  mName: string;
  kits: GearKit[];
}

// ───────────────────────────────────────────────────────────────────────
//  AIM binding
// ───────────────────────────────────────────────────────────────────────

export interface AimBinding {
  uiIndex: number;
  description: string;
  ProfilId: number;    // single-L typo IS canonical
  AimBioID: number;    // ★ the bug-fix field — used for AIMBIOS.EDT offset
}

// ───────────────────────────────────────────────────────────────────────
//  M.E.R.C. binding (Speck's mercenary service)
// ───────────────────────────────────────────────────────────────────────
// Mirror of sidecar's MercBinding Pydantic model. Used by the bundle
// manifest preview + future M.E.R.C.-write paths. Added 2026-05-25 as
// the cross-cutting review's contract-drift fix — the bundle preview
// was rendering AIM-only metadata for Type=2 (M.E.R.C.) bundles
// because the TS WmercManifestSummary lacked this field.

export interface MercBinding {
  uiIndex: number;             // M.E.R.C. site display position
  Name: string;
  ProfilId: number;            // slot pointer into MercProfiles.xml
  MercBioID: number;           // ★ offset into MERCBIOS.EDT × 1120
  usMoneyPaid: number;
  usDay: number;
  // Optional fields the engine reads but many mod files omit
  Drunk?: number;
  uiAlternateIndex?: number;
  StartMercsAvailable?: number;
  NewMercsAvailable?: number;
}

// ───────────────────────────────────────────────────────────────────────
//  Roster (response from /roster)
// ───────────────────────────────────────────────────────────────────────

export interface RosterEntry {
  slot: number;
  is_empty: boolean;
  name: string | null;
  nickname: string | null;
  profile_type: number | null;
  face_index: number | null;
  aim_bound: boolean;
  has_sti: boolean;
}

// ───────────────────────────────────────────────────────────────────────
//  Install detection
// ───────────────────────────────────────────────────────────────────────

export type ModId = "vanilla" | "wasteland" | "aimnas" | "wildfire" | "unknown";

export interface InstallInfo {
  id: string;
  path: string;
  exe_path: string;
  data_root: string;
  valid: boolean;
  errors: string[];
  last_played: number | null;
  mod_id: ModId;
  mod_display: string;
  mod_confidence: number;
  mod_evidence: string[];
  /** Set when this install entry represents a specific vfs_config from a
   * multi-VFS install (e.g. a Russian modpack with AIMNAS + Wildfire + UB).
   * The same physical install path can appear multiple times in the list,
   * each entry pointing at a different mod's vfs_config file. Null for
   * legacy / single-mod installs. */
  vfs_config_path: string | null;
  /** True for the "default" entry of an install -- the mod its Ja2.ini
   * currently points at. False for the secondary mod variants in a
   * multi-VFS install. FirstRun filters to primary=true by default so
   * the list shows one card per physical install; toggling "Show all
   * mod variants" reveals the rest. */
  is_primary: boolean;
  /** Short revision label (`r7605` / `1.13.0.8748`). Derived from the
   * JA2.exe PE version resource when available, else from the first
   * `rNNNN` line in `Changelog_Source.txt` / `Changelog_Data.txt`.
   * Null when no source agrees on a version. */
  engine_version: string | null;
  engine_version_source: "exe" | "changelog_source" | "changelog_data" | null;
}

/** Response from `POST /installs/{install_id}/apply-vfs`.
 *
 * Mirrors `ApplyVfsResult` Pydantic model in `sidecar/routes/installs.py`.
 * Added per TODO #17 — pre-fix callers ad-hoc-parsed the response shape. */
export interface ApplyVfsResult {
  install_id: string;
  /** Absolute path to the Ja2.ini file we modified. */
  ja2_ini_path: string;
  /** The `VFS_CONFIG_INI` value we wrote (relative path with forward
   *  slashes). */
  vfs_config_written: string;
  /** Path to the `.mwbak` backup of the original Ja2.ini. Null on the
   *  rare case where the backup couldn't be located after write. */
  backup_path: string | null;
  /** True when the install's chosen vfs_config was already the active
   *  value in Ja2.ini — the write was a no-op. */
  already_active: boolean;
}

// ───────────────────────────────────────────────────────────────────────
//  Audit
// ───────────────────────────────────────────────────────────────────────

export type IssueSeverity = "info" | "warn" | "error";

export interface AuditIssue {
  severity: IssueSeverity;
  field: string | null;
  code: string;
  message: string;
  suggested_fix: string | null;
}

// ───────────────────────────────────────────────────────────────────────
//  Backup
// ───────────────────────────────────────────────────────────────────────

export interface BackupEntry {
  id: string;
  timestamp: string;
  install_id: string;
  reason: string;
  root_dir: string;
  files: string[];
  total_size_bytes: number;
}

// ───────────────────────────────────────────────────────────────────────
//  INI editor (MercForge UI Phase 2 — backend: routes/ini_editor.py)
// ───────────────────────────────────────────────────────────────────────

export interface IniSchemaIndexEntry {
  ini_file: string;
  json: string;
  sections: number;
  properties: number;
}

export interface IniSchemasResponse {
  schemas: IniSchemaIndexEntry[];
  editable: string[];
  writable_profile: string | null;
  profile_root: string | null;
  vfs_mismatch: boolean;
}

export type IniConfidence = "official" | "engine" | "scraped" | "curated";

export interface IniProperty {
  name: string;
  datatype: string; // "numeric" | "boolean" | "string" | "list" | "array" | ""
  default: string | null;
  min: string | null;
  max: string | null;
  interval?: string | null;
  vanilla?: string | null;
  description: string;
  list_values: string[];
  confidence: IniConfidence;
  shipped?: string | null; // value in the INI at schema-generation time
  engine?: { loader?: string; default?: string; min?: string; max?: string };
  curated_note?: string;
}

export interface IniSchemaSection {
  name: string;
  description: string;
  properties: IniProperty[];
}

export interface IniSchemaDoc {
  ini_file: string;
  provenance: string;
  sections: IniSchemaSection[];
}

export interface IniEffectiveEntry {
  value: string | null;
  // profile name ("v113", "Vanilla", ...) | "override" | "ja2_ini" | "default" | "unset"
  source: string;
  override_active: boolean;
  stock_value?: string | null; // present when a reference install is configured
}

export interface IniEffectiveResponse {
  ini_file: string;
  merge_registered: boolean;
  override_file: string | null;
  override_present: boolean;
  writable_profile: string | null;
  profile_root: string | null;
  vfs_mismatch: boolean;
  sections: Record<string, Record<string, IniEffectiveEntry>>;
}

export interface IniOverrideEntry {
  ini_file: string;
  section: string;
  key: string;
  value: string;
  file: string;
}

export interface IniOverridesResponse {
  writable_profile: string | null;
  profile_root: string | null;
  overrides: IniOverrideEntry[];
}

export interface IniChangeItem {
  ini_file: string;
  section: string;
  key: string;
  value?: string | null;
  delete?: boolean;
}

export interface IniChangeResult {
  ini_file: string;
  section: string;
  key: string;
  status: "applied" | "planned";
  warning: string | null;
}

export interface IniApplyResult {
  ok: boolean;
  dry_run: boolean;
  target: "canon" | "override";
  applied: number;
  backup_id: string | null;
  files: Array<Record<string, unknown>>;
  results: IniChangeResult[];
}

export interface GameStatus {
  running: boolean;
  exe_name: string;
  by: "image_name";
}

export interface IniDiagnosticError {
  section: string;
  key: string;
  message: string;
  kind: "out_of_range" | "empty_toption" | "file_not_found" | "other";
  is_first_boot_noise: boolean;
}

export interface IniDiagnostic {
  profile_root: string | null;
  writable_profile?: string;
  vfs_layers: Array<{ name: string; kind: string; path: string }>;
  errors: IniDiagnosticError[];
  first_boot_noise_count: number;
  last_launch_raw: string | null;
  log_mtime: number | null;
}

export interface IniSummaryFile {
  ini_file: string;
  override_changed: number;
  play_sections: Record<string, number>;
  author_changed: number | null;
  author_sections: Record<string, number> | null;
}

export interface IniSummaryResponse {
  files: IniSummaryFile[];
  baseline: string | null;
}

export interface AppSettings {
  baseline_install_path?: string;
  backup_mode?: string;
}

export interface GraphicsComponent {
  component: string;
  kind: "runtime" | "managed_file" | "config_overlay";
  check_kind: "presence" | "strict_hash" | "key_subset";
  present: boolean;
  matches: boolean;
  note?: string;
  download_url?: string | null;
  source_available?: boolean;
  mismatched_keys?: string[];
}

export interface GraphicsStatusResponse {
  components: GraphicsComponent[];
}

export interface GraphicsDeployResult {
  ok: boolean;
  actions: string[];
  backup_id: string;
}

// ---- INI presets + setup flow (MercForge UI Phase 3) ----

export interface IniPresetChange {
  ini_file: string;
  section: string;
  key: string;
  value: string | null;
  delete: boolean;
  target: "override" | "canon" | null;
}

export interface IniPreset {
  id: string; // wire id: "builtin:x" | "install:y"
  name: string;
  description: string;
  default_target: "override" | "canon";
  source: "builtin" | "install";
  effect_timing: "new_game" | "relaunch";
  savegame_risk: boolean;
  apply_disabled: string | null;
  warnings: string[];
  changes: IniPresetChange[];
}

export interface IniPresetsResponse {
  presets: IniPreset[];
  file_warnings: string[];
}

export interface PresetDryRunFile {
  ini_file: string;
  path: string;
  preset?: string;
  changes: Array<{ section: string; key: string; value: string | null; current: string | null }>;
}

export interface PresetApplyResult {
  ok: boolean;
  dry_run: boolean;
  preset: string;
  batches?: Array<{ target: string; files: PresetDryRunFile[] }>;
  applied?: number;
  backup_id?: string;
  effect_timing: string;
  savegame_risk?: boolean;
}

export interface SetupDisplayState {
  renderer: "cnc-ddraw" | "engine";
  windowed: boolean;
  resolution: string | null;
  available_resolutions: Array<string | { code: number; label: string }>;
}

export interface SetupState {
  display: SetupDisplayState;
  intro: Record<string, string | undefined>;
  graphics: GraphicsStatusResponse;
  offered: boolean;
}

export interface SetupApplyResult {
  ok: boolean;
  dry_run: boolean;
  plan?: PresetDryRunFile[];
  applied?: number;
  backup_id?: string;
}
