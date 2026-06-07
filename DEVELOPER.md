# Merc Wizard 2 — Developer Guide

## Architecture

Three layers:

1. **Tauri shell** (`shell/`) — Rust binary that opens a desktop window, spawns and manages the Python sidecar, picks a free localhost port, runs a watchdog. ~10 MB.
2. **Frontend** (`frontend/`) — React + TypeScript SPA built with Vite. All UI, calls the sidecar via HTTP.
3. **Sidecar** (`sidecar/`) — Python FastAPI service that does all game-file I/O (STI, EDT, XML), portrait processing, and roster management. ~20 MB PyInstaller `--onefile` bundle.

Plus `mercwizard_core/` inside the sidecar — the importable Python library with no FastAPI deps. Can be used as a CLI too.

## Setup

### Sidecar (no Rust/Node required)

```bash
cd sidecar
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
pytest tests/ -v
```

### Frontend (requires Node.js)

```bash
cd frontend
npm install
npm run dev    # Vite dev server, but only useful when sidecar is running
```

### Shell (requires Rust + MSVC Build Tools)

```bash
cd shell
cargo build --release
# Or full Tauri build (also requires Node):
npm run tauri build
```

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
│   └── tests/                     — pytest suite (220+ tests)
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

```bash
cd sidecar && pytest tests/ -v --cov=mercwizard_core
```

Coverage target: ≥70% on `inject/`, `portrait/`, `audit/`, `bundle/`.

## Contributing

Keep the test suite green and match the surrounding code style.
