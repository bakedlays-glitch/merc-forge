// Prevent the extra console window on Windows release builds
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod schema;

use commands::*;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_app, _argv, _cwd| {}))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            // v1: campaigns
            detect_modpack_folder,
            pick_modpack_folder,
            load_modpack,
            get_active_campaign,
            set_active_campaign,
            launch_game,
            // v1: resolution (kept for compat; new Settings tab uses set_ja2ini_key)
            set_screen_resolution,
            // v1.5: generic Ja2.ini edit + Data-User overrides (Settings tab)
            get_ja2ini_key,
            set_ja2ini_key,
            read_user_options,
            write_user_option,
            delete_user_option,
            // v1.5: diagnostic readers (Diagnostic tab)
            build_diagnostic_report,
            open_log_folder,
            // v1.6: settings schema browser (INIEditor*.xml-driven)
            list_schemas,
            load_schema,
            read_effective_setting,
            // v1.8: presets (Phase B)
            apply_preset_changes,
            clear_all_overrides
        ])
        .run(tauri::generate_context!())
        .expect("error while running JA2 Launcher");
}
