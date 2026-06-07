//! Tauri commands exposed to the frontend.

use tauri::State;
use tauri_plugin_dialog::DialogExt;

use crate::sidecar::SidecarState;

#[tauri::command]
pub fn get_server_port(state: State<SidecarState>) -> u16 {
    state.port.load(std::sync::atomic::Ordering::SeqCst)
}

#[tauri::command]
pub fn get_server_token(state: State<SidecarState>) -> String {
    (*state.token).clone()
}

#[tauri::command]
pub async fn pick_directory(app: tauri::AppHandle, title: String) -> Option<String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .set_title(&title)
        .pick_folder(move |path| {
            let _ = tx.send(path.and_then(|p| p.into_path().ok().map(|p| p.to_string_lossy().to_string())));
        });
    tokio::task::spawn_blocking(move || rx.recv().ok().flatten())
        .await
        .ok()
        .flatten()
}

#[derive(Debug, serde::Deserialize)]
pub struct FilterSpec {
    pub name: String,
    pub extensions: Vec<String>,
}

#[tauri::command]
pub async fn pick_file(
    app: tauri::AppHandle,
    title: String,
    filters: Option<Vec<FilterSpec>>,
) -> Option<String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let mut builder = app.dialog().file().set_title(&title);
    if let Some(filters) = filters {
        for f in &filters {
            let exts: Vec<&str> = f.extensions.iter().map(String::as_str).collect();
            builder = builder.add_filter(&f.name, &exts);
        }
    }
    builder.pick_file(move |path| {
        let _ = tx.send(path.and_then(|p| p.into_path().ok().map(|p| p.to_string_lossy().to_string())));
    });
    tokio::task::spawn_blocking(move || rx.recv().ok().flatten())
        .await
        .ok()
        .flatten()
}
