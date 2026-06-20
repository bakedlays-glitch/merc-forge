//! Sidecar lifecycle: spawn, watchdog, kill.
//!
//! The Python sidecar is bundled as `mercwizard_core.exe` (PyInstaller --onefile)
//! and declared as an `externalBin` in tauri.conf.json. We:
//!   1. Spawn the sidecar with `--port 0` so it binds an OS-picked free port
//!      atomically (no pick-then-bind race).
//!   2. Read `SIDECAR_PORT=<n>` from the sidecar's stdout to learn the port.
//!   3. Continue draining stdout/stderr into env_logger output.
//!   4. Run a watchdog that pings /health every 2s. On failure, mutate
//!      `SidecarState` in place via its `Arc<AtomicU16>` + `Arc<Mutex<>>` —
//!      NEVER call `app.manage()` again. Tauri's `Manager::manage<T>()` is
//!      register-once; the second call is silently a no-op, which would
//!      leave the watchdog tracking a dead port forever (zombie spawn loop).
//!   5. Kill the process tree cleanly on shutdown via `taskkill /F /T`.

use std::sync::atomic::{AtomicU16, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// PID of the most recently spawned sidecar. Zero means "no sidecar yet".
/// Read by the global panic hook (which can't reach `SidecarState`) so it
/// can kill the process tree before the shell aborts.
pub static LATEST_SIDECAR_PID: AtomicU32 = AtomicU32::new(0);

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use tokio::sync::oneshot;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

pub struct SidecarState {
    pub port: Arc<AtomicU16>,
    /// Shared secret the sidecar requires on the `X-MercWizard-Token` header
    /// for every request. Generated once per shell launch (stable across
    /// watchdog respawns) so the cached value in the frontend stays valid.
    /// Defends the loopback HTTP API against drive-by webpages that scan
    /// localhost ports — without the token the sidecar 401s every request.
    pub token: Arc<String>,
    pub process: Arc<Mutex<Option<CommandChild>>>,
}

const SPAWN_TIMEOUT: Duration = Duration::from_secs(20);
const PORT_PREFIX: &str = "SIDECAR_PORT=";

/// 32 bytes of OS entropy, hex-encoded — 256 bits of unguessable session token.
fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("OS entropy unavailable");
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

/// Spawn one sidecar process and block until it reports its bound port on
/// stdout. Returns `(port, child)`. Used by both initial setup and the
/// watchdog respawn path. The token is injected via env var (not argv) so
/// it doesn't appear in any process-listing output.
async fn spawn_one(app: &AppHandle, token: &str) -> Result<(u16, CommandChild), String> {
    log::info!("Spawning sidecar");

    let (mut rx, child) = app
        .shell()
        .sidecar("mercwizard_core")
        .map_err(|e| format!("Failed to locate sidecar binary: {}", e))?
        .args(["--port", "0"])
        .env("MERCWIZARD_TOKEN", token)
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    let (port_tx, port_rx) = oneshot::channel::<u16>();
    let mut port_tx_opt = Some(port_tx);

    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    if let Ok(text) = String::from_utf8(bytes) {
                        for line in text.lines() {
                            if let Some(rest) = line.trim().strip_prefix(PORT_PREFIX) {
                                if let Ok(p) = rest.parse::<u16>() {
                                    if let Some(tx) = port_tx_opt.take() {
                                        let _ = tx.send(p);
                                    }
                                    continue;
                                }
                            }
                            log::info!("sidecar: {}", line);
                        }
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    if let Ok(text) = String::from_utf8(bytes) {
                        log::warn!("sidecar: {}", text.trim_end());
                    }
                }
                CommandEvent::Terminated(payload) => {
                    log::error!("sidecar terminated unexpectedly: {:?}", payload);
                }
                _ => {}
            }
        }
    });

    // On any failure here the spawned process is still alive but `child` is
    // about to drop — and `CommandChild` has NO Drop-kill, so an early return
    // would orphan a headless sidecar. Kill it explicitly on both error arms.
    let port = match tokio::time::timeout(SPAWN_TIMEOUT, port_rx).await {
        Ok(Ok(p)) => p,
        Ok(Err(_)) => {
            // port channel closed → the sidecar exited before reporting.
            let _ = child.kill();
            return Err("Sidecar exited before reporting port".to_string());
        }
        Err(_) => {
            // Timeout: process spawned but never printed its port → kill it so
            // we don't leak a running, unreachable sidecar.
            let pid = child.pid();
            let _ = child.kill();
            return Err(format!(
                "Sidecar didn't report port within {}s (pid {} killed)",
                SPAWN_TIMEOUT.as_secs(), pid,
            ));
        }
    };

    LATEST_SIDECAR_PID.store(child.pid(), Ordering::SeqCst);
    log::info!("Sidecar bound port {} (pid {})", port, child.pid());
    Ok((port, child))
}

/// Best-effort synchronous kill via taskkill on the most recently known
/// sidecar PID. Used by the panic hook, which can't acquire async locks
/// or reach `SidecarState` before the process aborts.
#[cfg(target_os = "windows")]
pub fn kill_latest_sidecar_blocking() {
    let pid = LATEST_SIDECAR_PID.load(Ordering::SeqCst);
    if pid == 0 {
        return;
    }
    let _ = std::process::Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .creation_flags(0x08000000) // CREATE_NO_WINDOW
        .status();
}

#[cfg(not(target_os = "windows"))]
pub fn kill_latest_sidecar_blocking() {}

/// Initial sidecar spawn — called once from `setup()`. Returns the
/// `SidecarState` to be registered via `app.manage()`. Do NOT call this
/// from the watchdog — use `spawn_one()` + in-place mutation of the
/// existing state instead.
pub async fn spawn_sidecar(app: &AppHandle) -> Result<SidecarState, String> {
    let token = generate_token();
    let (port, child) = spawn_one(app, &token).await?;
    Ok(SidecarState {
        port: Arc::new(AtomicU16::new(port)),
        token: Arc::new(token),
        process: Arc::new(Mutex::new(Some(child))),
    })
}

/// Boot-time sweep: kill any `mercwizard_core.exe` left over from a prior
/// crashed session. Safe to call inside `setup()` because the single-instance
/// plugin guarantees we're the only live `mercwizard.exe`, so any sidecar
/// already running is necessarily an orphan.
#[cfg(target_os = "windows")]
pub fn kill_orphan_sidecars() {
    let result = std::process::Command::new("taskkill")
        .args(["/F", "/IM", "mercwizard_core.exe"])
        .creation_flags(0x08000000) // CREATE_NO_WINDOW
        .status();
    match result {
        Ok(s) if s.success() => log::info!("Cleared orphan sidecars from prior session"),
        // taskkill returns 128 when no matching processes exist — normal on fresh start.
        Ok(s) if s.code() == Some(128) => log::debug!("No orphan sidecars to clear"),
        Ok(s) => log::warn!("Orphan sidecar sweep exited with status {}", s),
        Err(e) => log::warn!("Failed to launch taskkill for orphan sweep: {}", e),
    }
}

#[cfg(not(target_os = "windows"))]
pub fn kill_orphan_sidecars() {
    // The orphan model is Windows-specific (PyInstaller --onefile bootloader
    // + Python interpreter pair). On other platforms, the single-instance
    // guarantee is sufficient.
}

pub fn kill_sidecar(state: &SidecarState) {
    if let Ok(mut guard) = state.process.lock() {
        if let Some(child) = guard.take() {
            let pid = child.pid();
            log::info!("Killing sidecar process tree (root pid {})", pid);
            // PyInstaller --onefile spawns a bootloader + child Python
            // interpreter. `taskkill /T` walks the process tree so both die.
            #[cfg(target_os = "windows")]
            {
                let status = std::process::Command::new("taskkill")
                    .args(["/F", "/T", "/PID", &pid.to_string()])
                    .creation_flags(0x08000000) // CREATE_NO_WINDOW
                    .status();
                match status {
                    Ok(s) if s.success() => {}
                    Ok(s) => log::warn!("taskkill exited with status {}", s),
                    Err(e) => log::warn!("taskkill failed to launch: {}", e),
                }
            }
            #[cfg(not(target_os = "windows"))]
            {
                let _ = child.kill();
            }
        }
    }
}

/// Watchdog: poll /health every 2s. After 3 consecutive failures, kill the
/// current sidecar and spawn a replacement, mutating the existing
/// `SidecarState` in place. Emits `sidecar:restarted` so the frontend can
/// invalidate cached state.
///
/// Respawn failure handling: if `spawn_one` returns Err (port bind race,
/// missing binary, AV quarantine), the watchdog applies exponential backoff
/// (2s → 4s → 8s → ... capped at 60s) before the next health-check tick.
/// Without backoff a persistently-failing spawn was a tight loop —
/// effectively a fork-bomb on the user's machine during heavy AV scans.
pub async fn watchdog_loop(app: AppHandle) {
    let base_interval = Duration::from_secs(2);
    let max_interval = Duration::from_secs(60);
    let mut interval = base_interval;
    let mut consecutive_failures = 0;
    let mut consecutive_respawn_failures: u32 = 0;

    loop {
        tokio::time::sleep(interval).await;

        let (port, token) = match app.try_state::<SidecarState>() {
            Some(state) => (state.port.load(Ordering::SeqCst), state.token.clone()),
            None => continue,
        };
        let url = format!("http://127.0.0.1:{}/api/v1/health", port);
        let ok = ping_health(&url, &token).await;

        if ok {
            if consecutive_failures > 0 {
                log::info!("Sidecar health restored");
            }
            consecutive_failures = 0;
            consecutive_respawn_failures = 0;
            interval = base_interval;
        } else {
            consecutive_failures += 1;
            log::warn!("Sidecar health ping failed ({}/3)", consecutive_failures);
            if consecutive_failures >= 3 {
                log::error!("Sidecar dead — respawning");
                let state = match app.try_state::<SidecarState>() {
                    Some(s) => s,
                    None => {
                        log::error!("SidecarState missing during respawn — aborting watchdog");
                        return;
                    }
                };
                kill_sidecar(&state);
                match spawn_one(&app, &state.token).await {
                    Ok((new_port, new_child)) => {
                        state.port.store(new_port, Ordering::SeqCst);
                        if let Ok(mut guard) = state.process.lock() {
                            *guard = Some(new_child);
                        }
                        let _ = app.emit("sidecar:restarted", new_port);
                        log::info!("Sidecar respawned on port {}", new_port);
                        consecutive_respawn_failures = 0;
                        interval = base_interval;
                    }
                    Err(e) => {
                        consecutive_respawn_failures =
                            consecutive_respawn_failures.saturating_add(1);
                        // Exponential backoff capped at 60s: 4s, 8s, 16s, 32s, 60s, 60s...
                        let backoff_secs = (2_u64.saturating_pow(
                            consecutive_respawn_failures + 1,
                        ))
                        .min(max_interval.as_secs());
                        interval = Duration::from_secs(backoff_secs);
                        log::error!(
                            "Failed to respawn sidecar ({}): backing off {}s before next check",
                            e,
                            backoff_secs,
                        );
                    }
                }
                consecutive_failures = 0;
            }
        }
    }
}

async fn ping_health(url: &str, token: &str) -> bool {
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_millis(3000))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    match client
        .get(url)
        .header("X-MercWizard-Token", token)
        .send()
        .await
    {
        Ok(resp) => resp.status().is_success(),
        Err(_) => false,
    }
}
