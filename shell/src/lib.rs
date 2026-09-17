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

mod commands;
mod sidecar;

use tauri::{Emitter, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

/// In-app route requested via `--route` on the command line, consumed once by
/// the frontend (`take_initial_route`). Lets scripts open e.g. a MapForge
/// sector directly: `mercwizard.exe --route "/mapforge/sector?dat=...&tileset=N"`.
pub struct InitialRoute(pub std::sync::Mutex<Option<String>>);

/// Accepts `--route=/path` and `--route /path`. Only in-app absolute paths —
/// a leading `/` but not `//` (protocol-relative would escape the webview).
fn route_from_argv(argv: &[String]) -> Option<String> {
    let mut route: Option<String> = None;
    let mut index = 0;
    while index < argv.len() {
        let arg = &argv[index];
        if arg == "--" {
            break;
        }
        if let Some(v) = arg.strip_prefix("--route=") {
            if route.is_some() {
                return None;
            }
            route = Some(v.to_string());
        } else if arg == "--route" {
            if route.is_some() {
                return None;
            }
            let value = argv.get(index + 1)?;
            if value.starts_with("--") {
                return None;
            }
            route = Some(value.to_string());
            index += 1;
        }
        index += 1;
    }
    route.filter(|r| is_safe_internal_route(r))
}

/// Validates only the route pathname. Query and hash values remain opaque so
/// callers may use them for Windows paths, while route separators cannot be
/// smuggled in raw, percent-encoded, or double-percent-encoded form.
fn is_safe_internal_route(route: &str) -> bool {
    if !route.starts_with('/') {
        return false;
    }
    let suffix_at = route.find(['?', '#']).unwrap_or(route.len());
    let mut pathname = route[..suffix_at].to_string();

    loop {
        if pathname.starts_with("//") || pathname.contains('\\') {
            return false;
        }
        let decoded = match decode_path_once(&pathname) {
            Some(value) => value,
            None => return false,
        };
        if decoded == pathname {
            return true;
        }
        if decoded.contains('\\')
            || decoded.contains("//")
            || decoded.contains('?')
            || decoded.contains('#')
        {
            return false;
        }
        pathname = decoded;
    }
}

fn decode_path_once(pathname: &str) -> Option<String> {
    let bytes = pathname.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] != b'%' {
            decoded.push(bytes[index]);
            index += 1;
            continue;
        }
        let high = *bytes.get(index + 1)?;
        let low = *bytes.get(index + 2)?;
        let byte = (hex_value(high)? << 4) | hex_value(low)?;
        // Reject encoded pathname separators at every decode level before they
        // can affect routing. Raw separators are checked by the caller.
        if byte == b'/' || byte == b'\\' || byte == b'?' || byte == b'#' {
            return None;
        }
        decoded.push(byte);
        index += 3;
    }
    String::from_utf8(decoded).ok()
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn is_final_exit_event(event: &tauri::RunEvent) -> bool {
    matches!(event, tauri::RunEvent::Exit)
}

#[cfg(test)]
mod route_tests {
    use super::route_from_argv;

    #[test]
    fn route_equals_form_accepts_safe_path_and_preserves_query_hash() {
        assert_eq!(
            route_from_argv(&["mercwizard.exe".into(), "--route=/hub?tab=1#top".into()]),
            Some("/hub?tab=1#top".into())
        );
    }

    #[test]
    fn route_split_form_accepts_safe_path_and_preserves_query_hash() {
        assert_eq!(
            route_from_argv(&[
                "mercwizard.exe".into(),
                "--route".into(),
                "/mapforge/sector?dat=A1.dat#inspect".into(),
            ]),
            Some("/mapforge/sector?dat=A1.dat#inspect".into())
        );
    }

    #[test]
    fn route_parser_rejects_encoded_or_backslash_path_separators() {
        for value in [
            "//outside.example",
            "/mapforge\\sector",
            "/%2foutside",
            "/%5coutside",
            "/%252foutside",
            "/%25252Foutside",
            "/%25%32%66outside",
            "/%25%35%43outside",
            "/mapforge%3F//outside",
            "/mapforge%23//outside",
            "/mapforge%2Fsector",
            "/mapforge%5Csector",
            "/bad%",
        ] {
            assert_eq!(
                route_from_argv(&["mercwizard.exe".into(), format!("--route={value}")]),
                None,
                "{value} should be rejected"
            );
        }
    }

    #[test]
    fn route_parser_rejects_missing_flag_values_and_duplicates() {
        for argv in [
            vec!["mercwizard.exe", "--route"],
            vec!["mercwizard.exe", "--route", "--other"],
            vec!["mercwizard.exe", "--route", "--route=/hub"],
            vec!["mercwizard.exe", "--route="],
            vec!["mercwizard.exe", "--route=", "--route=/hub"],
            vec!["mercwizard.exe", "--route=/hub", "--route", "/tools"],
            vec!["mercwizard.exe", "--route=/hub", "--route=/tools"],
        ] {
            assert_eq!(
                route_from_argv(&argv.into_iter().map(String::from).collect::<Vec<_>>()),
                None
            );
        }
    }

    #[test]
    fn route_parser_does_not_interpret_arguments_after_double_dash() {
        assert_eq!(
            route_from_argv(&["mercwizard.exe".into(), "--".into(), "--route=/hub".into(),]),
            None
        );
    }

    #[test]
    fn route_parser_keeps_query_and_hash_values_opaque() {
        let route = "/mapforge/sector?next=%2Fhub%3Ftab%3D1#return=%23top";
        assert_eq!(
            route_from_argv(&["mercwizard.exe".into(), format!("--route={route}")]),
            Some(route.into())
        );
    }

    #[test]
    fn only_the_final_run_event_releases_the_sidecar_lifeline() {
        assert!(super::is_final_exit_event(&tauri::RunEvent::Exit));
        assert!(!super::is_final_exit_event(&tauri::RunEvent::Ready));
    }
}

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

    // Cargo.toml sets `panic = "abort"` in release.  The sidecar's own
    // authenticated parent lifeline is already connected before startup is
    // considered ready, so an abort closes the shell socket and the long-lived
    // PyInstaller runtime exits without an unsafe later PID cleanup attempt.

    tauri::Builder::default()
        // Single-instance MUST be first so a second launch terminates before
        // any startup work can compete with the live instance.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // A second launch with `--route` is a navigation request from a
            // script — forward it to the live webview before focusing.
            if let Some(route) = route_from_argv(&argv) {
                let _ = app.emit("open-route", route);
            }
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // Cold-start `--route`: stash it; the frontend pulls it via
            // `take_initial_route` once mounted (an emit here would race the
            // listener).
            let args: Vec<String> = std::env::args().collect();
            app.manage(InitialRoute(std::sync::Mutex::new(route_from_argv(&args))));

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
                    // Flush the buffered error line: the LoggerHandle is
                    // mem::forget-ed (no Drop-flush) and process::exit skips
                    // destructors, so without this the diagnostic can be lost.
                    log::logger().flush();
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
        .invoke_handler(tauri::generate_handler![
            commands::get_server_port,
            commands::get_server_token,
            commands::take_initial_route,
            commands::pick_directory,
            commands::pick_file,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // Only this final event releases the sidecar lifeline. A
            // CloseRequested event is cancellable by the webview's unsaved
            // edit guard; releasing it there would leave a still-visible app
            // with a dead backend.
            if is_final_exit_event(&event) {
                if let Some(state) = app_handle.try_state::<sidecar::SidecarState>() {
                    sidecar::kill_sidecar(&state);
                }
            }
        });
}
