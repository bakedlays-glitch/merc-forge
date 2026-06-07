// Tauri-specific wrappers around the backend `invoke` calls.
//
// This file is the SINGLE SEAM that needs to be swapped when these components
// get lifted into MercForge. MercForge has its own backend (Python sidecar),
// so it would replace these functions with calls into the existing
// installs.py routes. All the React components (CampaignCard, CampaignList,
// SettingsTab, DiagnosticTab) import from here, so a one-file rewrite is
// enough to repoint everything.

import { invoke } from "@tauri-apps/api/core";
import type {
  ModpackManifest,
  DiagnosticReport,
  UserOptions,
  SchemaAvailability,
  SchemaDoc,
  EffectiveValue,
  PresetChange,
} from "../types/modpack";

// ---- v1: campaign + launch ----

export async function detectModpackFolder(): Promise<string | null> {
  return await invoke<string | null>("detect_modpack_folder");
}

export async function loadModpack(folder: string): Promise<ModpackManifest> {
  return await invoke<ModpackManifest>("load_modpack", { folder });
}

export async function getActiveCampaign(folder: string): Promise<string> {
  return await invoke<string>("get_active_campaign", { folder });
}

export async function setActiveCampaign(
  folder: string,
  vfsConfig: string
): Promise<void> {
  return await invoke<void>("set_active_campaign", {
    folder,
    vfsConfig: vfsConfig,
  });
}

export async function setScreenResolution(
  folder: string,
  code: number
): Promise<void> {
  return await invoke<void>("set_screen_resolution", { folder, code });
}

export async function launchGame(
  folder: string,
  exeName: string
): Promise<number> {
  return await invoke<number>("launch_game", { folder, exeName });
}

// ---- v1.5: generic Ja2.ini edit ----

export async function getJa2iniKey(
  folder: string,
  key: string
): Promise<string | null> {
  return await invoke<string | null>("get_ja2ini_key", { folder, key });
}

export async function setJa2iniKey(
  folder: string,
  key: string,
  value: string
): Promise<void> {
  return await invoke<void>("set_ja2ini_key", { folder, key, value });
}

// ---- v1.5: Data-User/Ja2_Options.ini editing ----

export async function readUserOptions(
  folder: string,
  iniFile: string
): Promise<UserOptions> {
  return await invoke<UserOptions>("read_user_options", { folder, iniFile });
}

export async function writeUserOption(
  folder: string,
  iniFile: string,
  section: string,
  key: string,
  value: string
): Promise<void> {
  return await invoke<void>("write_user_option", {
    folder,
    iniFile,
    section,
    key,
    value,
  });
}

export async function deleteUserOption(
  folder: string,
  iniFile: string,
  section: string,
  key: string
): Promise<void> {
  return await invoke<void>("delete_user_option", {
    folder,
    iniFile,
    section,
    key,
  });
}

// ---- v1.5: diagnostic ----

export async function buildDiagnosticReport(
  folder: string,
  saveDir: string
): Promise<DiagnosticReport> {
  return await invoke<DiagnosticReport>("build_diagnostic_report", {
    folder,
    saveDir,
  });
}

export async function openLogFolder(
  folder: string,
  saveDir: string
): Promise<void> {
  return await invoke<void>("open_log_folder", { folder, saveDir });
}

// ---- v1.6: schema-driven settings browser ----

export async function listSchemas(folder: string): Promise<SchemaAvailability[]> {
  return await invoke<SchemaAvailability[]>("list_schemas", { folder });
}

export async function loadSchema(
  folder: string,
  iniFile: string
): Promise<SchemaDoc> {
  return await invoke<SchemaDoc>("load_schema", { folder, iniFile });
}

export async function readEffectiveSetting(
  folder: string,
  iniFile: string,
  section: string,
  key: string
): Promise<EffectiveValue> {
  return await invoke<EffectiveValue>("read_effective_setting", {
    folder,
    iniFile,
    section,
    key,
  });
}

// ---- v1.8: Presets (Phase B) ----

export async function applyPresetChanges(
  folder: string,
  changes: PresetChange[]
): Promise<number> {
  return await invoke<number>("apply_preset_changes", { folder, changes });
}

export async function clearAllOverrides(folder: string): Promise<number> {
  return await invoke<number>("clear_all_overrides", { folder });
}
