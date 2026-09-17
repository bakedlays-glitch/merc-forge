# MercForge Full-Review Repair Design

**Date:** 2026-08-29

**Approval:** Complete repair sequence approved on 2026-08-29.

## Goal

Make MercForge safe against the confirmed live-engine, map-write, sidecar-lifecycle, and destructive-UI defects; restore a green test baseline; and produce a freshly verified package without modifying game content or interrupting the currently running MercForge instance.

## Global constraints

- Work only in the nested `MercWizard2` repository.
- Preserve all unrelated tracked and untracked work already present on `main`.
- Do not write to either JA2 installation during implementation or automated verification.
- Do not stop the running MercForge process. Package into an isolated Cargo target.
- Every behavior change starts with a focused failing test and ends with its focused suite plus the full relevant suite.
- Game-file writes remain atomic and recoverable.
- Windows process cleanup is PID/path scoped; image-name-wide termination is forbidden.

## 1. Target-aware body types

Create one backend registry for `SoldierBodyTypes` instead of keeping a stock-looking table inside `audit.py` and a second four-option table in React.

- Vanilla 1.13 uses enum values 0 through 28 from `Animation Data.h`.
- The Wasteland uses the same values 0 through 28 plus the append-only custom values 29 through 43: `DOG`, `GORISCLAW`, `GRUTHARCLAW`, `MOMCLAW`, `MUTANT`, `ALPHACLAW`, `NIGHTKIN`, `GHOUL`, `FERALGHOUL`, `GLOWGHOUL`, `RADSCORPION`, `HULK`, `MARCUS`, `JAY`, and `SILENTBOB`.
- `TOTALBODYTYPES` is an exclusive sentinel and is never exposed or accepted.
- Known mods use their exact registry. Unknown/custom mods use the vanilla registry plus body IDs already observed in that target install’s `MercProfiles.xml`; observed extensions are labeled as such instead of being invented.
- Audit and bundle import validate against the selected target install’s registry. A Wasteland import must reject 44 and accept 29 through 43.
- `GET /merc/body-types` returns the target-derived options and provenance. `DemographicsForm` consumes that endpoint, preserves the current value during loading/error, and no longer claims only four humanoid body types are valid.
- Sex consistency warnings apply only when the registry entry has an explicit sex.

## 2. MapForge write safety

Each editing session records the active install ID at open time. Normal save holds, in this order, the cross-process install lock, the process-wide state write lock, and the session lock across the external-change recheck, backups, serialization, atomic replacement, and baseline update. This prevents two MercForge sessions from both passing the guard and then last-writer-winning.

- Replace hand-rolled fixed `.mwtmp` writes with the shared `write_bytes_atomic` helper.
- `save-copy-as` uses the same install-scoped serialization and atomic writer for its destination.
- Appendix `map_version` is constrained to 15 through 255 at the API boundary and again in `pack_map_tail`; values below 15 never enter session state.
- SLF loose extraction uses an atomic writer. `slf://` archive paths must resolve inside the active install’s recognized VFS roots.
- Session close marks the session closed while holding its lock, removes it from the store, and deletes recovery state. Autosave refuses to write a closed session, eliminating recovery resurrection after discard.

## 3. Sidecar and shell lifecycle

The Python sidecar owns its socket before emitting `SIDECAR_PORT`. The Rust shell then waits for an authenticated `/api/v1/health` success before returning the sidecar to Tauri setup. A marker is port discovery, not readiness.

- A non-loopback `--host` is rejected unless `MERCWIZARD_TOKEN` is set.
- Remove the startup image-name-wide `taskkill /IM mercwizard_core.exe` sweep. Normal shutdown and panic cleanup retain captured-PID process-tree termination.
- `launch_current.ps1` may stop only MercForge processes whose executable path equals this checkout’s release executable or sidecar path.
- Remove unused `shell:allow-execute` and `shell:allow-spawn` webview permissions.
- Add focused Rust/Python tests for readiness and host/token policy.

## 4. Destructive UI and API invariants

- Recruitment dialogue always retains at least one branch. The frontend cannot remove the final branch, Save is disabled for an empty branch list, and the API rejects an empty list.
- Deleting a persisted fact setter or talking-face override uses the shared destructive confirmation service.
- MapForge Tile Inspector replaces native `confirm()` with `useDialog().confirm()`.
- A busy `ConfirmModal` ignores Escape and backdrop dismissal.
- Shared dialogs have an accessible name, trap focus inside the modal, and restore focus to the previously focused element when they close.
- Tests exercise the observable invariants rather than checking source text.

## 5. Green baseline and package verification

- Fix the Backgrounds glossary regression so lower-case “percent” is linked and the existing failing test passes.
- Upgrade the React Router dependency family to a non-vulnerable supported release and adapt the app only as required by the official migration contract.
- Required verification: backend full pytest suite, frontend tests/typecheck/build, `npm audit --omit=dev`, Rust tests/check, and a full Tauri bundle in an isolated target directory.
- Verify the bundled executable is newer than its frontend bundle and the installer contains the rebuilt sidecar. Do not install or launch the resulting package automatically.

## Success criteria

1. Wasteland body IDs 29–43 validate and appear in the editor; 44 is rejected.
2. A deterministic concurrent-save test proves one same-target writer receives the external-modification conflict instead of overwriting silently.
3. Appendix version 14 is rejected before mutation; 15 is accepted.
4. Sidecar readiness cannot succeed before authenticated health responds.
5. Destructive UI/API paths enforce the stated confirmation and non-empty invariants.
6. All required test/build/audit gates pass and a fresh isolated installer is produced.
