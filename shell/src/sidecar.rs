//! Sidecar lifecycle: spawn, watchdog, lifeline release.
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
//!   5. Hold an authenticated loopback lifeline which makes the long-lived
//!      PyInstaller runtime exit when the shell closes or is killed.

use std::future::Future;
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{oneshot, watch};
use tokio::task::JoinSet;

struct RunningSidecar {
    child: CommandChild,
    lifeline: LifelineLease,
    terminated: oneshot::Receiver<()>,
}

struct LifelineLease(Option<TcpStream>);

impl LifelineLease {
    fn new(stream: TcpStream) -> Self {
        Self(Some(stream))
    }

    fn close(&mut self) {
        drop(self.0.take());
    }
}

pub struct SidecarState {
    pub port: Arc<AtomicU16>,
    /// Shared secret the sidecar requires on the `X-MercWizard-Token` header
    /// for every request. Generated once per shell launch (stable across
    /// watchdog respawns) so the cached value in the frontend stays valid.
    /// Defends the loopback HTTP API against drive-by webpages that scan
    /// localhost ports — without the token the sidecar 401s every request.
    pub token: Arc<String>,
    process: Arc<Mutex<Option<RunningSidecar>>>,
}

const SPAWN_TIMEOUT: Duration = Duration::from_secs(20);
const HEALTH_POLL_INTERVAL: Duration = Duration::from_millis(100);
const PORT_PREFIX: &str = "SIDECAR_PORT=";
const LIFELINE_PORT_ENV: &str = "MERCWIZARD_LIFELINE_PORT";
const LIFELINE_TOKEN_ENV: &str = "MERCWIZARD_LIFELINE_TOKEN";
const LIFELINE_ACK: &[u8; 4] = b"MWL1";
const LIFELINE_TOKEN_BYTES: usize = 64;
const LIFELINE_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_PENDING_LIFELINE_HANDSHAKES: usize = 8;
const LIFELINE_EXIT_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, PartialEq, Eq)]
enum SidecarStdout {
    Port(Result<u16, String>),
    Log(String),
}

fn drain_sidecar_stdout(buffer: &mut Vec<u8>, chunk: &[u8]) -> Vec<SidecarStdout> {
    buffer.extend_from_slice(chunk);
    let mut messages = Vec::new();
    while let Some(newline) = buffer.iter().position(|byte| *byte == b'\n') {
        let line: Vec<u8> = buffer.drain(..=newline).collect();
        let text = String::from_utf8_lossy(&line[..line.len() - 1]);
        let trimmed = text.trim();
        if let Some(rest) = trimmed.strip_prefix(PORT_PREFIX) {
            let marker = match rest.trim().parse::<u16>() {
                Ok(port) if port != 0 => Ok(port),
                _ => Err("Sidecar reported an invalid port marker".to_string()),
            };
            messages.push(SidecarStdout::Port(marker));
        } else if !trimmed.is_empty() {
            messages.push(SidecarStdout::Log(trimmed.to_string()));
        }
    }
    messages
}

fn abort_spawn<T>(child: CommandChild, message: String) -> Result<T, String> {
    // This path runs before a lifeline handshake could prove a long-lived
    // runtime exists.  Killing the captured handle is safe: it is a handle to
    // this exact child, never a later-reused numeric PID.
    let _ = child.kill();
    Err(message)
}

/// 32 bytes of OS entropy, hex-encoded — 256 bits of unguessable session token.
fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("OS entropy unavailable");
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

async fn bind_lifeline_listener() -> Result<TcpListener, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .await
        .map_err(|e| format!("Failed to bind parent lifeline: {}", e))
}

#[cfg(test)]
async fn accept_lifeline(listener: TcpListener, token: &str) -> Result<TcpStream, String> {
    let (_termination_tx, termination_rx) = watch::channel(false);
    accept_lifeline_until(
        listener,
        token,
        tokio::time::Instant::now() + SPAWN_TIMEOUT,
        termination_rx,
    )
    .await
}

async fn accept_lifeline_until(
    listener: TcpListener,
    token: &str,
    deadline: tokio::time::Instant,
    mut termination: watch::Receiver<bool>,
) -> Result<TcpStream, String> {
    let expected: [u8; LIFELINE_TOKEN_BYTES] = token
        .as_bytes()
        .try_into()
        .map_err(|_| "Generated lifeline token has an invalid length".to_string())?;
    let mut pending = JoinSet::new();

    loop {
        if *termination.borrow() {
            return Err("Sidecar terminated before completing parent lifeline".to_string());
        }
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => {
                return Err("Sidecar did not connect its parent lifeline".to_string());
            }
            changed = termination.changed() => {
                if changed.is_ok() && *termination.borrow() {
                    return Err("Sidecar terminated before completing parent lifeline".to_string());
                }
                return Err("Sidecar termination monitor stopped during parent lifeline".to_string());
            }
            completed = pending.join_next(), if !pending.is_empty() => {
                if let Some(result) = completed {
                    match result {
                        Ok((_peer, Ok(stream))) => return Ok(stream),
                        Ok((peer, Err(reason))) => log::warn!(
                            "Rejected unauthenticated parent lifeline connection from {}: {}",
                            peer,
                            reason
                        ),
                        Err(error) => log::warn!("Parent lifeline handshake task failed: {}", error),
                    }
                }
            }
            accepted = listener.accept(), if pending.len() < MAX_PENDING_LIFELINE_HANDSHAKES => {
                let (stream, peer) = accepted
                    .map_err(|e| format!("Failed to accept parent lifeline: {}", e))?;
                let timeout = LIFELINE_HANDSHAKE_TIMEOUT.min(
                    deadline.saturating_duration_since(tokio::time::Instant::now()),
                );
                pending.spawn(async move {
                    let result = authenticate_lifeline(stream, expected, timeout).await;
                    (peer, result)
                });
            }
        }
    }
}

async fn authenticate_lifeline(
    mut stream: TcpStream,
    expected: [u8; LIFELINE_TOKEN_BYTES],
    timeout: Duration,
) -> Result<TcpStream, String> {
    let mut proof = [0_u8; LIFELINE_TOKEN_BYTES];
    match tokio::time::timeout(timeout, stream.read_exact(&mut proof)).await {
        Ok(Ok(_)) if proof == expected => {}
        Ok(Ok(_)) => return Err("token mismatch".to_string()),
        Ok(Err(error)) => return Err(format!("proof read failed: {}", error)),
        Err(_) => return Err("proof timed out".to_string()),
    }
    stream
        .write_all(LIFELINE_ACK)
        .await
        .map_err(|e| format!("acknowledgement failed: {}", e))?;
    Ok(stream)
}

async fn wait_for_port_until(
    port_rx: oneshot::Receiver<Result<u16, String>>,
    mut termination: watch::Receiver<bool>,
    deadline: tokio::time::Instant,
) -> Result<u16, String> {
    if *termination.borrow() {
        return Err("Sidecar terminated before reporting port".to_string());
    }
    tokio::select! {
        _ = tokio::time::sleep_until(deadline) =>
            Err("Sidecar didn't report port before startup deadline".to_string()),
        changed = termination.changed() => {
            if changed.is_ok() && *termination.borrow() {
                Err("Sidecar terminated before reporting port".to_string())
            } else {
                Err("Sidecar termination monitor stopped before reporting port".to_string())
            }
        }
        marker = port_rx => match marker {
            Ok(Ok(port)) => Ok(port),
            Ok(Err(message)) => Err(message),
            Err(_) => Err("Sidecar exited before reporting port".to_string()),
        },
    }
}

async fn wait_for_lifeline_then_port(
    listener: TcpListener,
    token: &str,
    port_rx: oneshot::Receiver<Result<u16, String>>,
    termination: watch::Receiver<bool>,
    deadline: tokio::time::Instant,
) -> Result<(TcpStream, u16), String> {
    // Python intentionally waits for this acknowledgement before it binds and
    // emits SIDECAR_PORT.  Accept it first so neither side waits on the other.
    let lifeline = accept_lifeline_until(listener, token, deadline, termination.clone()).await?;
    let port = wait_for_port_until(port_rx, termination, deadline).await?;
    Ok((lifeline, port))
}

fn release_running_sidecar(running: RunningSidecar) {
    let RunningSidecar {
        child,
        mut lifeline,
        terminated,
    } = running;
    // Dropping this socket is the authoritative shutdown request.  The Python
    // runtime owns the other end and exits itself on EOF, including when its
    // PyInstaller bootloader is no longer a live child of this shell.
    lifeline.close();
    tauri::async_runtime::spawn(async move {
        if matches!(
            tokio::time::timeout(LIFELINE_EXIT_TIMEOUT, terminated).await,
            Ok(Ok(()))
        ) {
            return;
        }
        log::warn!("Sidecar ignored lifeline closure; terminating captured child handle");
        let _ = child.kill();
    });
}

/// Spawn one sidecar process and block until it reports its bound port on
/// stdout, completes an authenticated parent-lifeline handshake, and passes
/// authenticated health readiness. Returns the port and captured lifecycle.
/// Used by both initial setup and the watchdog respawn path. The token is
/// injected via env var (not argv) so it doesn't appear in process listings.
async fn spawn_one(app: &AppHandle, token: &str) -> Result<(u16, RunningSidecar), String> {
    log::info!("Spawning sidecar");
    let startup_deadline = tokio::time::Instant::now() + SPAWN_TIMEOUT;

    let lifeline_listener = bind_lifeline_listener().await?;
    let lifeline_port = lifeline_listener
        .local_addr()
        .map_err(|e| format!("Failed to inspect parent lifeline: {}", e))?
        .port();
    let lifeline_token = generate_token();

    let (mut rx, child) = app
        .shell()
        .sidecar("mercwizard_core")
        .map_err(|e| format!("Failed to locate sidecar binary: {}", e))?
        .args(["--port", "0"])
        .env("MERCWIZARD_TOKEN", token)
        .env(LIFELINE_PORT_ENV, lifeline_port.to_string())
        .env(LIFELINE_TOKEN_ENV, &lifeline_token)
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    let (port_tx, port_rx) = oneshot::channel::<Result<u16, String>>();
    let mut port_tx_opt = Some(port_tx);
    let (terminated_tx, terminated_rx) = oneshot::channel::<()>();
    let mut terminated_tx_opt = Some(terminated_tx);
    let (termination_notice_tx, termination_notice_rx) = watch::channel(false);

    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        let mut stdout_buffer = Vec::new();
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    for message in drain_sidecar_stdout(&mut stdout_buffer, &bytes) {
                        match message {
                            SidecarStdout::Port(marker) => {
                                if let Some(tx) = port_tx_opt.take() {
                                    let _ = tx.send(marker);
                                }
                            }
                            SidecarStdout::Log(line) => log::info!("sidecar: {}", line),
                        }
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    if let Ok(text) = String::from_utf8(bytes) {
                        log::warn!("sidecar: {}", text.trim_end());
                    }
                }
                CommandEvent::Terminated(payload) => {
                    if let Some(tx) = terminated_tx_opt.take() {
                        let _ = tx.send(());
                    }
                    let _ = termination_notice_tx.send(true);
                    log::info!("sidecar terminated: {:?}", payload);
                }
                _ => {}
            }
        }
    });

    let (lifeline, port) = match wait_for_lifeline_then_port(
        lifeline_listener,
        &lifeline_token,
        port_rx,
        termination_notice_rx,
        startup_deadline,
    )
    .await
    {
        Ok(startup) => startup,
        Err(error) => return abort_spawn(child, error),
    };

    let running = RunningSidecar {
        child,
        lifeline: LifelineLease::new(lifeline),
        terminated: terminated_rx,
    };

    if let Err(error) = wait_for_health_until(port, token, startup_deadline).await {
        release_running_sidecar(running);
        return Err(format!(
            "Sidecar failed authenticated health readiness on port {} before startup deadline: {}",
            port, error,
        ));
    }

    log::info!("Sidecar ready on port {}", port);
    Ok((port, running))
}

/// Initial sidecar spawn — called once from `setup()`. Returns the
/// `SidecarState` to be registered via `app.manage()`. Do NOT call this
/// from the watchdog — use `spawn_one()` + in-place mutation of the
/// existing state instead.
pub async fn spawn_sidecar(app: &AppHandle) -> Result<SidecarState, String> {
    let token = generate_token();
    let (port, running) = spawn_one(app, &token).await?;
    Ok(SidecarState {
        port: Arc::new(AtomicU16::new(port)),
        token: Arc::new(token),
        process: Arc::new(Mutex::new(Some(running))),
    })
}

pub fn kill_sidecar(state: &SidecarState) {
    if let Ok(mut guard) = state.process.lock() {
        if let Some(running) = guard.take() {
            log::info!("Releasing sidecar parent lifeline");
            release_running_sidecar(running);
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
                        let backoff_secs = (2_u64.saturating_pow(consecutive_respawn_failures + 1))
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
    authenticated_health(&client, url, token)
        .await
        .unwrap_or(false)
}

#[cfg(test)]
async fn wait_for_health(port: u16, token: &str) -> Result<(), String> {
    wait_for_health_until(port, token, tokio::time::Instant::now() + SPAWN_TIMEOUT).await
}

async fn wait_for_health_until(
    port: u16,
    token: &str,
    deadline: tokio::time::Instant,
) -> Result<(), String> {
    let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
    if remaining.is_zero() {
        return Err("startup deadline elapsed before health readiness".to_string());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_millis(3000))
        .build()
        .map_err(|e| format!("Failed to create health client: {}", e))?;
    let url = format!("http://127.0.0.1:{}/api/v1/health", port);
    wait_for_health_with(remaining, HEALTH_POLL_INTERVAL, || {
        authenticated_health(&client, &url, token)
    })
    .await
}

async fn wait_for_health_with<F, Fut, E>(
    timeout: Duration,
    retry_interval: Duration,
    mut health_check: F,
) -> Result<(), String>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<bool, E>>,
{
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        if matches!(health_check().await, Ok(true)) {
            return Ok(());
        }
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            return Err("health endpoint never returned authenticated 200".to_string());
        }
        tokio::time::sleep(retry_interval.min(remaining)).await;
    }
}

async fn authenticated_health(
    client: &reqwest::Client,
    url: &str,
    token: &str,
) -> Result<bool, reqwest::Error> {
    client
        .get(url)
        .header("X-MercWizard-Token", token)
        .send()
        .await
        .map(|resp| resp.status() == reqwest::StatusCode::OK)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;
    use std::sync::Arc;
    use std::time::Instant;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::sync::watch;

    #[tokio::test]
    async fn startup_acknowledges_lifeline_before_waiting_for_the_backend_port() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let token = "d".repeat(LIFELINE_TOKEN_BYTES);
        let (port_tx, port_rx) = oneshot::channel();
        let (_termination_tx, termination_rx) = watch::channel(false);

        let backend_token = token.clone();
        let backend = tokio::spawn(async move {
            let mut connection = TcpStream::connect(address).await.unwrap();
            connection
                .write_all(backend_token.as_bytes())
                .await
                .unwrap();
            let mut acknowledgement = [0_u8; 4];
            connection.read_exact(&mut acknowledgement).await.unwrap();
            assert_eq!(acknowledgement, *LIFELINE_ACK);
            port_tx.send(Ok(45231)).unwrap();
        });

        let (_lifeline, port) = wait_for_lifeline_then_port(
            listener,
            &token,
            port_rx,
            termination_rx,
            tokio::time::Instant::now() + Duration::from_secs(1),
        )
        .await
        .unwrap();

        assert_eq!(port, 45231);
        backend.await.unwrap();
    }

    #[tokio::test]
    async fn partial_lifeline_proof_does_not_delay_a_valid_backend() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let token = "e".repeat(LIFELINE_TOKEN_BYTES);
        let (_termination_tx, termination_rx) = watch::channel(false);

        let mut slow_peer = TcpStream::connect(address).await.unwrap();
        slow_peer.write_all(b"partial").await.unwrap();

        let valid_token = token.clone();
        let valid_backend = tokio::spawn(async move {
            let mut connection = TcpStream::connect(address).await.unwrap();
            connection.write_all(valid_token.as_bytes()).await.unwrap();
            let mut acknowledgement = [0_u8; 4];
            connection.read_exact(&mut acknowledgement).await.unwrap();
            (acknowledgement, connection.local_addr().unwrap())
        });

        let accepted = tokio::time::timeout(
            Duration::from_millis(300),
            accept_lifeline_until(
                listener,
                &token,
                tokio::time::Instant::now() + Duration::from_secs(1),
                termination_rx,
            ),
        )
        .await
        .expect("a partial proof must not serialize valid lifeline acceptance")
        .unwrap();

        let (acknowledgement, valid_address) = valid_backend.await.unwrap();
        assert_eq!(acknowledgement, *LIFELINE_ACK);
        assert_eq!(accepted.peer_addr().unwrap(), valid_address);
        drop(slow_peer);
    }

    #[test]
    fn split_port_marker_chunks_are_reassembled_before_startup_consumes_them() {
        let mut buffer = Vec::new();

        assert!(drain_sidecar_stdout(&mut buffer, b"SIDECAR_PO").is_empty());
        assert_eq!(
            drain_sidecar_stdout(&mut buffer, b"RT=45231\n"),
            vec![SidecarStdout::Port(Ok(45231))]
        );
    }

    #[tokio::test]
    async fn lifeline_rejects_an_invalid_proof_before_accepting_the_shell_token() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let token = "a".repeat(LIFELINE_TOKEN_BYTES);
        let expected_token = token.clone();
        let accept = tokio::spawn(async move { accept_lifeline(listener, &expected_token).await });

        let mut impostor = TcpStream::connect(address).await.unwrap();
        impostor
            .write_all(&[b'b'; LIFELINE_TOKEN_BYTES])
            .await
            .unwrap();
        drop(impostor);

        let mut shell = TcpStream::connect(address).await.unwrap();
        shell.write_all(token.as_bytes()).await.unwrap();
        let mut acknowledgement = [0_u8; 4];
        shell.read_exact(&mut acknowledgement).await.unwrap();

        let accepted = accept.await.unwrap().unwrap();
        assert_eq!(acknowledgement, *LIFELINE_ACK);
        assert_eq!(accepted.peer_addr().unwrap(), shell.local_addr().unwrap());
    }

    #[tokio::test]
    async fn lifeline_cleanup_is_idempotent_without_a_numeric_pid() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let mut backend = TcpStream::connect(address).await.unwrap();
        let (shell, _) = listener.accept().await.unwrap();
        let mut lifeline = LifelineLease::new(shell);

        lifeline.close();
        lifeline.close();

        let mut eof = [0_u8; 1];
        assert_eq!(backend.read(&mut eof).await.unwrap(), 0);
    }

    #[tokio::test]
    async fn wait_for_health_requires_authenticated_success() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let expected_token = "test-readiness-token";

        let server = tokio::spawn(async move {
            for status in ["401 Unauthorized", "200 OK"] {
                let (mut stream, _) = listener.accept().await.unwrap();
                let mut request = vec![0_u8; 4096];
                let read = stream.read(&mut request).await.unwrap();
                let request = String::from_utf8_lossy(&request[..read]).to_ascii_lowercase();
                assert!(request.contains("x-mercwizard-token: test-readiness-token"));
                stream
                    .write_all(
                        format!(
                            "HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        )
                        .as_bytes(),
                    )
                    .await
                    .unwrap();
            }
        });

        wait_for_health(port, expected_token).await.unwrap();
        server.await.unwrap();
    }

    #[tokio::test]
    async fn wait_for_health_retries_transient_connection_errors() {
        let attempts = Arc::new(AtomicUsize::new(0));
        let check_attempts = attempts.clone();

        wait_for_health_with(
            Duration::from_millis(100),
            Duration::from_millis(1),
            move || {
                let attempts = check_attempts.clone();
                async move {
                    if attempts.fetch_add(1, Ordering::SeqCst) == 0 {
                        Err::<bool, ()>(())
                    } else {
                        Ok::<bool, ()>(true)
                    }
                }
            },
        )
        .await
        .unwrap();

        assert_eq!(attempts.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn wait_for_health_times_out_after_permanent_non_200() {
        let started = Instant::now();
        let error = wait_for_health_with(
            Duration::from_millis(25),
            Duration::from_millis(2),
            || async { Ok::<bool, ()>(false) },
        )
        .await
        .expect_err("permanent non-200 must not mark the sidecar ready");

        assert!(started.elapsed() >= Duration::from_millis(20));
        assert_eq!(error, "health endpoint never returned authenticated 200");
    }
}
