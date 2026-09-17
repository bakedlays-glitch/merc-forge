# Merc Forge — Developer Guide

## Architecture

Three layers:

1. **Tauri shell** (`shell/`) — Rust binary that opens a desktop window, spawns and manages the Python sidecar, picks a free localhost port, runs a watchdog. ~10 MB.
2. **Frontend** (`frontend/`) — React + TypeScript SPA built with Vite. All UI, calls the sidecar via HTTP.
3. **Sidecar** (`sidecar/`) — Python FastAPI service that does all game-file I/O (STI, EDT, XML), portrait processing, and roster management. The current local PyInstaller `--onefile` bundle is about 33 MB; size varies with bundled dependencies.

Plus `mercwizard_core/` inside the sidecar — the importable Python library with no FastAPI deps. Can be used as a CLI too.

## Release versioning

`VERSION` is the sole authority for the Merc Forge product version. Run
`powershell -File tools/sync_version.ps1 -Write` after changing it, then run
the same command with `-Check` before building or committing. The sync tool
updates product manifests, runtime responses, UI text, and release docs; it
does not rewrite dependency versions.

The sidecar's package version, FastAPI metadata, `/version` response, and the
`.wmerc` `tool_version` field all report the Merc Forge product version. A
future incompatible HTTP or `.wmerc` contract must use a separately named
integer schema field (for example, `api_schema_version` or the existing
`wmerc_version`) rather than overloading product SemVer.

## Setup

### Sidecar (no Rust/Node required)

```bash
cd sidecar
python -m venv .venv
.venv\Scripts\activate          # Windows
python -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest tests/ -v
```

### Frontend (requires Node.js)

```bash
cd frontend
npm install
npm run dev    # Vite dev server, but only useful when sidecar is running
```

### Shell (requires Rust + MSVC Build Tools)

The Tauri CLI is the npm `@tauri-apps/cli` installed under `frontend/`, and it
must be run **from `shell/`** — the directory holding `tauri.conf.json`. Run it
from anywhere else and its `beforeBuildCommand` (`npm --prefix frontend run
build`) resolves against the wrong directory and the build fails before it
compiles anything.

```bash
cd shell

# The app binary only (no installer). This is what launch_current.ps1 runs.
../frontend/node_modules/.bin/tauri build --no-bundle

# The full build, including the NSIS installer under
# shell/target/release/bundle/nsis/.
../frontend/node_modules/.bin/tauri build
```

Both forms rebuild the frontend on the way through, via `beforeBuildCommand`.
Neither rebuilds the Python sidecar: `tauri build` bundles whatever
`shell/binaries/mercwizard_core-x86_64-pc-windows-msvc.exe` happens to be on
disk, so run `build_sidecar.ps1` first whenever sidecar sources have changed.
Close a running Merc Forge before building — a live app locks its own
executable under `shell/target/release/` and the build fails with a
permission error.

`cargo build --release` on its own compiles the Rust crate without the frontend
or the bundle, which is rarely what you want.

## Toolchain requirements

| Component | Tool | Install on Windows |
|---|---|---|
| Sidecar | Python 3.12+ | `winget install Python.Python.3.12` |
| Frontend | Node.js LTS | `winget install OpenJS.NodeJS.LTS` |
| Shell | Rust | `winget install Rustlang.Rustup` |
| Shell | MSVC Build Tools | `winget install Microsoft.VisualStudio.2022.BuildTools` |

## Repository layout

```
MercWizard2/
├── shell/                         — Tauri shell (Rust)
│   └── src/
│       ├── main.rs                — Tauri app entry, window setup
│       ├── sidecar.rs             — SidecarState, spawn, watchdog, respawn_in_place
│       └── commands.rs            — Tauri command handlers (get_server_port, etc.)
├── frontend/                      — React + TypeScript SPA (Vite)
│   └── src/
│       ├── main.tsx               — React app entry
│       └── lib/
│           ├── tauri.ts           — Tauri bridge, port cache, sidecar:restarted listener
│           └── api.ts             — Base URL derivation, post-respawn rediscovery
├── sidecar/                       — Python FastAPI service
│   ├── main.py                    — FastAPI app entry, startup hooks
│   ├── requirements.txt
│   ├── mercwizard_core/           — Pure library (no FastAPI deps)
│   │   ├── models.py              — Pydantic schemas (Merc, Gear, AimBinding, …)
│   │   ├── audit.py               — Field-cap enforcement before writes
│   │   ├── backup.py              — Pre-write snapshot logic
│   │   ├── install_detect.py      — Auto-discovery of JA2 installs
│   │   ├── install_context.py     — Per-install path resolution
│   │   ├── vfs.py                 — VFS config parsing (mod content layer routing)
│   │   ├── mod_detect.py          — Mod fingerprinting
│   │   ├── roster.py              — Roster read/write
│   │   ├── slot_locks.py          — Engine-named slot safety tiers
│   │   ├── relocator.py           — Cross-install move logic
│   │   ├── saves.py               — Save-game scanner
│   │   ├── traits.py              — NT/OT trait ID tables
│   │   ├── voice.py               — Voice clip manager
│   │   ├── bundle/                — .wmerc export/import
│   │   │   ├── manifest.py        — WmercManifest Pydantic model
│   │   │   ├── export.py
│   │   │   ├── import_.py
│   │   │   └── move_cross.py
│   │   ├── inject/                — Game-file write primitives
│   │   │   ├── edt.py             — EDT encoding + AIMBIOS/MERCBIOS routing
│   │   │   ├── profiles_xml.py    — MercProfiles.xml writer
│   │   │   ├── starting_gear.py   — MercStartingGear.xml writer
│   │   │   ├── aim_availability.py
│   │   │   └── merc_availability.py
│   │   ├── portrait/              — STI compile pipeline
│   │   │   ├── compile.py         — Top-level compile_and_write_all
│   │   │   ├── quantize.py        — 255-color quantize with index-0 reservation
│   │   │   ├── sti.py             — STI writer, union-palette build
│   │   │   ├── sizes.py           — Canonical face-size constants
│   │   │   ├── animate_skip.py    — Static-portrait 7-dummy-frame path
│   │   │   ├── animate_explicit.py — Explicit per-frame authoring path
│   │   │   └── animate_procedural.py
│   │   └── presets/               — Bundled gear preset JSONs
│   ├── routes/                    — FastAPI route modules
│   │   ├── state.py               — Install registry + AppData persistence
│   │   ├── merc.py, roster.py, slots.py, gear.py
│   │   ├── bundle.py, backup.py, saves.py, voice.py
│   │   ├── installs.py, portrait.py, game.py, health.py
│   │   └── slots.py
│   └── tests/                     — pytest suite (1,000+ tests)
│       ├── conftest.py
│       └── test_audit, test_backup, test_bundle, test_edt, test_install_detect,
│           test_models, test_portrait, test_relocator, test_roster, test_routes,
│           test_saves_and_misc, test_security, test_vfs, test_xml_writers
├── docs/
│   └── WMERC_FORMAT.md            — .wmerc bundle format spec
├── README.md                      — User-facing install + feature overview
├── DEVELOPER.md                   — This file
└── LICENSE
```

## Testing

Run the complete automated chain from the repository root:

```powershell
.\run_tests.ps1
```

It runs the sidecar pytest suite, frontend TypeScript typecheck, and frontend Vitest suite. Browser-driven Playwright checks remain a separate live-app verification because they require the app and sidecar to be running.

For sidecar-only coverage:

```powershell
cd sidecar
.\.venv\Scripts\python.exe -m pytest tests/ -v --cov=mercwizard_core
```

Coverage target: ≥70% on `inject/`, `portrait/`, `audit/`, and `bundle/`.

### Tests and tools that need a real game install

No absolute path to a game install, engine source tree or sibling checkout is
committed anywhere in this repository. The tests and developer tools that need
one read it from the environment and skip cleanly when it is unset, so a fresh
clone runs green without any local setup.

| Variable | Points at |
| --- | --- |
| `JA2_INSTALL` | The root of a JA2 1.13 install (the folder holding `JA2.exe` and `Data-1.13`). |
| `JA2_INSTALLS_DIR` | A folder holding several installs, for the tools that sweep across them. |
| `JA2_SOURCE` | A checkout of the JA2 1.13 C++ source, for the tests that verify engine provenance. |
| `JA2_OPEN_TOOLSET` | A `ja2-open-toolset` checkout, when it is not beside this repository. |
| `MERCWIZARD_HEADLESS_COMPILER` | A `Headless_Compiler` checkout, for the placement oracle and its tools. |
| `MERCWIZARD_BG_LIBRARY_JSON` | The harvested `unique_backgrounds.json`; the background library returns 503 without it. |
| `MERCWIZARD_PLACEMENT_DATA` | An override for the bundled placement tables. |

Setting none of them is a supported configuration: the suite skips the install-
dependent tests and everything else runs.

## Contributing

Keep the test suite green and match the surrounding code style.
