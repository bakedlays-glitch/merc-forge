// Tauri 2.0's `tauri-build` should emit `cargo:rerun-if-changed` for the
// frontend dist, but in practice cargo's incremental compilation often
// skips re-linking the shell binary when only the frontend dist
// changed. Without a fresh re-link, the embedded frontend stays the
// old version — meaning frontend edits show up in `frontend/dist/`
// but NEVER in the running `mercwizard.exe`. Symptom is "I edited
// frontend code, ran the launcher, and the change isn't there."
//
// Discovered 2026-05-26 while debugging the WebGL Z-buffer wall-clip
// fix: shell binary was from 18:20:50 while frontend dist was from
// 19:30:09 — the launcher rebuilt the frontend bundle, but the
// embedded frontend in the shell binary stayed stale.
//
// Workaround: explicit `cargo:rerun-if-changed` directives for the
// frontend dist's index.html (sentinel for any change to the bundle)
// AND the assets directory. Bumping either forces cargo to re-link
// the shell binary on the next build.
fn main() {
    println!("cargo:rerun-if-changed=../frontend/dist/index.html");
    println!("cargo:rerun-if-changed=../frontend/dist/assets");
    tauri_build::build()
}
