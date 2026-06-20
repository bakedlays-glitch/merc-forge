//! Merc Wizard 2 — Tauri shell.
//!
//! Responsibilities:
//! - Open the main window
//! - Spawn the bundled Python sidecar (`mercwizard_core.exe`) with `--port 0`;
//!   read the bound port back from its stdout (no shell-side port race)
//! - Run a watchdog that pings `/health` every 2 seconds and restarts on 3
//!   consecutive failures, mutating the existing `SidecarState` in place
//! - Kill the sidecar cleanly when the window closes (no orphan processes)
//! - Expose a small set of Tauri commands the frontend invokes for port discovery + file dialogs

mod sidecar;
mod commands;

use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

/// `%APPDATA%\MercWizard\logs\` on Windows; `~/.config/MercWizard/logs/` elsewhere.
fn log_dir() -> std::path::PathBuf {
    #[cfg(target_os = "windows")]
    {
        let appdata = std::env::var_os("APPDATA")
            .map(std::path::PathBuf::from)
            .or_else(|| std::env::var_os("USERPROFILE").map(std::path::PathBuf::from))
            .unwrap_or_else(|| std::path::PathBuf::from("."));
        appdata.join("MercWizard").join("logs")
    }
    #[cfg(not(target_os = "windows"))]
    {
        let home = std::env::var_os("HOME")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| std::path::PathBuf::from("."));
        home.join(".config").join("MercWizard").join("logs")
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // File logging with rotation. The windowed Tauri build has no console
    // (windows_subsystem = "windows"), so logs going only to stderr are lost.
    let dir = log_dir();
    let _ = std::fs::create_dir_all(&dir);
    let logger_handle = flexi_logger::Logger::try_with_str("info")
        .expect("invalid log filter")
        .log_to_file(
            flexi_logger::FileSpec::default()
                .directory(&dir)
                .basename("shell"),
        )
        .duplicate_to_stderr(flexi_logger::Duplicate::Info)
        .rotate(
            // 10 MB per file × 3 rotated files = 30 MB cap on disk per user.
            // Previously KeepLogFiles(5) allowed up to 50 MB — overkill for
            // a desktop tool where the most useful log is "what happened
            // in the last hour", and chatty watchdog/health pings can
            // saturate at info level.
            flexi_logger::Criterion::Size(10_000_000),
            flexi_logger::Naming::Numbers,
            flexi_logger::Cleanup::KeepLogFiles(3),
        )
        .write_mode(flexi_logger::WriteMode::BufferAndFlush)
        .start()
        .expect("failed to init logger");
    // The handle must outlive the program — drop it and the logger goes idle.
    std::mem::forget(logger_handle);

    // Install panic hook BEFORE building the app. Cargo.toml sets
    // `panic = "abort"` in release, so any panic on any thread terminates
    // the process — without this hook the sidecar would be orphaned.
    let default_panic = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        log::error!("Shell panic — killing sidecar before abort: {}", info);
        sidecar::kill_latest_sidecar_blocking();
        default_panic(info);
    }));

    tauri::Builder::default()
        // Single-instance MUST be first — if a second copy is launched, the
        // plugin terminates this process before any further setup runs, so
        // we don't kill the first instance's sidecar in our orphan sweep.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // We're the unique instance now (single-instance plugin guaranteed
            // it). Any mercwizard_core.exe still running is an orphan from a
            // crashed prior session — kill it before we spawn our own.
            sidecar::kill_orphan_sidecars();

            let app_handle = app.handle().clone();
            // Spawn the sidecar and block until it reports its bound port.
            // On failure DO NOT `.expect()` — in release `panic = "abort"`
            // turns a panic into a silent crash-to-desktop (the window is
            // never shown), so a first-launch AV quarantine of the bundled
            // mercwizard_core.exe looks like the app simply doing nothing.
            // Instead show a clear, actionable native dialog naming the most
            // likely cause + the log path, then exit cleanly (no orphan —
            // the spawn failed, so there is no sidecar to kill).
            let state = match tauri::async_runtime::block_on(async {
                sidecar::spawn_sidecar(&app_handle).await
            }) {
                Ok(state) => state,
                Err(e) => {
                    log::error!("Sidecar spawn failed — aborting startup: {}", e);
                    let logs = log_dir();
                    app_handle
                        .dialog()
                        .message(format!(
                            "Merc Forge couldn't start its background service, \
                             so it can't run.\n\n{e}\n\n\
                             The most likely cause is your antivirus blocking or \
                             quarantining the bundled helper (mercwizard_core.exe) \
                             on first launch. Restore/allow it, then relaunch \
                             Merc Forge.\n\nLog folder:\n{}",
                            logs.display(),
                        ))
                        .title("Merc Forge — startup failed")
                        .kind(MessageDialogKind::Error)
                        .blocking_show();
                    std::process::exit(1);
                }
            };
            app.manage(state);

            // Start the watchdog so dead sidecars get restarted
            let app_handle_for_watchdog = app_handle.clone();
            tauri::async_runtime::spawn(async move {
                sidecar::watchdog_loop(app_handle_for_watchdog).await;
            });

            // Force the main window visible + focused. Tauri normally
            // handles this automatically once setup() returns, but the
            // combination of `maximized: true` in tauri.conf.json, the
            // single-instance plugin's broker window race, and the
            // sidecar-spawn block above has been observed to leave the
            // main window CREATED but never marked visible — only the
            // 16×16 single-instance helper window shows up. A user
            // hit this: the real "Merc Forge" window existed as
            // visible=False while the broker was the only visible
            // top-level. Explicit show/unminimize/focus here is the
            // belt-and-suspenders fix.
            if let Some(window) = app.get_webview_window("main") {
                if let Err(e) = window.show() {
                    log::warn!("main window show() failed: {}", e);
                }
                let _ = window.unminimize();
                if let Err(e) = window.set_focus() {
                    log::warn!("main window set_focus() failed: {}", e);
                }
                log::info!("main window shown + focused");
            } else {
                log::error!("main window 'main' not found at setup — UI will not be visible");
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // Kill the sidecar before allowing the window to close so we don't
                // leave an orphan Python process running in the background.
                let app = window.app_handle();
                if let Some(state) = app.try_state::<sidecar::SidecarState>() {
                    sidecar::kill_sidecar(&state);
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_server_port,
            commands::get_server_token,
            commands::pick_directory,
            commands::pick_file,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // RunEvent::Exit fires for every shutdown path that goes through
            // tao's event loop — OS shutdown, last window closed, app exit.
            // CloseRequested above covers the normal close-button path; this
            // is the safety net. kill_sidecar is idempotent (state.process
            // is taken and replaced with None on first call).
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<sidecar::SidecarState>() {
                    sidecar::kill_sidecar(&state);
                }
            }
        });
}
