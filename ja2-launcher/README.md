# JA2 Launcher

Standalone Tauri+React app that ships inside JA2 1.13 modpack folders (Arulco Stories, future packs). Reads `modpack.json` from the same directory as the launcher exe, presents a campaign picker, switches the active `vfs_config.*.ini` line in `Ja2.ini`, and launches `ja2.exe`.

Designed to be **eventually folded into MercForge** (`../frontend/`). The React components in `frontend/src/components/` are written framework-pure — they take props + callbacks, no Tauri imports. When MercForge gains a Campaigns tab, those components import as-is. The only file that needs to be swapped is `frontend/src/api/launcher.ts` (the Tauri `invoke` wrappers), which would be replaced with calls into MercWizard2's existing Python sidecar routes.

## Project layout

```
ja2-launcher/
├── shell/                       Tauri 2.0 Rust app (matches MercWizard2/shell/ layout)
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── build.rs
│   ├── capabilities/default.json
│   ├── icons/                   (copied from ../shell/icons at build time)
│   └── src/
│       ├── main.rs              Tauri entry — wires plugins and commands
│       └── commands.rs          Rust commands (load_modpack, set_active_campaign, launch_game, etc.)
├── frontend/                    React + TS + Vite + Tailwind
│   ├── package.json
│   ├── vite.config.ts           dev port 1421 (MercForge owns 1420)
│   ├── tsconfig.json
│   ├── tailwind.config.js       JA2-themed olive+amber palette
│   ├── postcss.config.js
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx              top-level UI (header + campaign list + play button)
│       ├── index.css            Tailwind + ja2-* component classes
│       ├── types/modpack.ts     TS mirror of modpack.json schema
│       ├── api/launcher.ts      Tauri invoke wrappers (THE SEAM for MercForge)
│       └── components/
│           ├── CampaignCard.tsx     pure-React, portable
│           ├── CampaignList.tsx     pure-React, portable
│           └── ResolutionPicker.tsx pure-React, portable
├── build.ps1                    one-shot build + drop into a modpack folder
└── README.md                    this file
```

## How it finds a modpack

On startup, `detect_modpack_folder()` (Rust) tries:
1. `--modpack <path>` or positional CLI arg
2. The directory containing `JA2Launcher.exe` itself (the "drop in the modpack folder" case)
3. Walks up from cwd looking for `modpack.json`

If none yield a `modpack.json`, the UI shows a placement-instructions screen.

## Building

```powershell
cd <repo>\ja2-launcher

# First-time setup
npm --prefix frontend install

# Dev (live-reload, runs both Vite + Tauri)
npm --prefix frontend run tauri dev

# Production build (creates the standalone exe + NSIS installer)
npm --prefix frontend run tauri build
```

Output:
- Dev: opens a window driven by Vite at http://localhost:1421
- Production: `shell/target/release/ja2launcher.exe` + NSIS installer under `shell/target/release/bundle/nsis/`

The `build.ps1` script does the production build and copies `ja2launcher.exe` into the Arulco Stories modpack folder as `JA2Launcher.exe`.

## Adding a campaign to an existing modpack

The launcher reads everything from `modpack.json` — no rebuild needed. Edit the modpack's `modpack.json`, add a `campaigns[]` entry, ship the new `vfs_config.X.ini` + `Mods/Data-X.7z`, and JA2Launcher.exe picks it up on next start.

## Migration to MercForge (planned)

When MercForge integrates this:
1. Copy `frontend/src/components/Campaign*.tsx` and `ResolutionPicker.tsx` into `MercWizard2/frontend/src/components/`
2. Copy `frontend/src/types/modpack.ts` into the MercForge types/
3. Create a new MercForge route (`/campaigns` or as a tab in `Hub.tsx`)
4. Reimplement `api/launcher.ts` calls against MercForge's existing `apply_vfs_config()` endpoint at `installs.py:220`
5. Deprecate this standalone app (or keep it for users who don't have MercForge installed)
