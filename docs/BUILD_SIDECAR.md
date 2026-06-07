# Rebuilding the sidecar binary

Merc Forge is three parts: the **Tauri shell** (Rust), the **React frontend**,
and a **Python sidecar** that does all game-file I/O (XML / EDT / STI, roster,
portraits, voice, backgrounds, MapForge). The sidecar ships as a single
**PyInstaller `--onefile` EXE** that the shell launches.

## The gotcha (read this)

Rebuilding the **frontend** (`vite` / `tauri build`) does **NOT** regenerate the
sidecar EXE — they're separate build steps. So any change under `sidecar/`
(Python) won't show up in the running app until you rebuild the sidecar binary.

This is exactly why a frontend-only rebuild shows new **UI** but not new
**backend behaviour** (e.g. roster face resolution, voice auto-naming, or the
background fixes all live in the sidecar).

## Rebuild it — easy way

From the repo root:

```powershell
.\build_sidecar.ps1
```

It stops any running instance, runs PyInstaller, and copies the fresh EXE to the
two places the shell looks for it:

- `shell\binaries\mercwizard_core-x86_64-pc-windows-msvc.exe` — the Tauri
  `externalBin` source (bundled by `tauri build` / the NSIS installer).
- `shell\target\release\mercwizard_core.exe` — the dev-run copy next to the app
  `.exe` (only refreshed if a shell release build already exists).

Then build/run the app so the shell picks it up:

```powershell
.\launch_current.ps1      # rebuilds stale frontend + shell and launches
```

(`launch_current.ps1` already rebuilds the sidecar **incrementally** by mtime —
`build_sidecar.ps1` is the "force-rebuild just the sidecar" shortcut for when you
want to be certain it's fresh.)

## Rebuild it — manual, for reference

```powershell
cd sidecar
.venv\Scripts\python.exe -m PyInstaller mercwizard_core.spec --clean --noconfirm
# -> sidecar\dist\mercwizard_core.exe
Copy-Item dist\mercwizard_core.exe ..\shell\binaries\mercwizard_core-x86_64-pc-windows-msvc.exe -Force
Copy-Item dist\mercwizard_core.exe ..\shell\target\release\mercwizard_core.exe -Force
```

Notes:

- **Don't** pipe PyInstaller through `2>&1` in Windows PowerShell 5.1 — it writes
  progress to stderr, which gets wrapped in error records that abort the script
  under `$ErrorActionPreference = "Stop"`. Let it stream; check `$LASTEXITCODE`.
- Build spec: `sidecar/mercwizard_core.spec`. Venv: `sidecar/.venv` (create with
  `python -m venv .venv` + `pip install -r requirements.txt` if missing).
- Cold start of the `--onefile` EXE unpacks to `%TEMP%\_MEIxxxxx\` (~3–5 s the
  first launch); that's expected.
