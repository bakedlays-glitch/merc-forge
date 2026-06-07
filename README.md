# Merc Forge

A standalone Windows tool for the Jagged Alliance 2 v1.13 community — vanilla, AIMNAS, Wildfire, The Wasteland, or anything else built on the 1.13 codebase. Two halves:

- **Merc management** — create, edit, move, duplicate, delete, and share mercs across installs without hand-editing XML, hex-patching EDT files, or memorizing the documented AIMBIOS off-by-one bug.
- **Map Forge** — open any 1.13 sector `.dat`, paint tiles with an iso-faithful client-side renderer, stamp multi-tile structs (helis, vehicles) in one click, and save back to disk with `.bak`. *(A built-in browser for picking stock-tileset art is on the way in a post-beta update.)*

> **Beta.** This is an early public beta — expect rough edges, and please [report bugs](#reporting-bugs). Every operation backs up the files it touches before writing, so mistakes are one click away from being undone. The installer isn't code-signed yet, so Windows SmartScreen will warn "unknown publisher" — click **More info → Run anyway**. (Some antivirus may also flag the bundled Python sidecar — a known PyInstaller false-positive, not malware.)
>
> **Disclaimer.** Merc Forge is an unofficial, fan-made tool for the Jagged Alliance 2 v1.13 community and is **not** affiliated with or endorsed by Sir-Tech, Strategy First, THQ Nordic, or the JA2 1.13 development team. It ships no game executable — bring your own legally obtained copy of Jagged Alliance 2 and the v1.13 mod. All game trademarks and assets belong to their respective owners; redistributed third-party and game-derived content is attributed in [NOTICE](NOTICE).

## Quick install

The download is a single Windows installer (`Merc Forge_1.0.0_x64-setup.exe`) that pulls the ~1.7 MB WebView2 bootstrapper from Microsoft at install time if WebView2 isn't already on the machine. On Windows 10 (1803+) and Windows 11 WebView2 is preinstalled, so this is usually a no-op and the install completes offline.

Steps:

1. Run the setup `.exe`.
2. Launch **Merc Forge** from the Start menu.
3. On first run, point it at your JA2 1.13 install directory. Merc Forge auto-detects Steam, GOG, and common manual paths; if those don't find yours, browse to it yourself.

No Python, no Node, no MSVC runtime download required either way — the sidecar EXE is a PyInstaller `--onefile` bundle that carries its own interpreter and runtime DLLs.

## What it does

### Roster management
- **Create** new mercs through a 9-step wizard: slot → identity → portrait → attributes → traits → biography → gear → voice → review. Pick AIM or M.E.R.C. on the slot step (Speck's regular service or the budget service for non-human companions). Optionally pre-fill every field from an existing merc with "Start from existing." Portrait pipeline takes any PNG and writes engine-correct 8-frame SmallFace STIs (the format JA2 needs for blink/talk animation) at all four canonical sizes. Drag rectangles on the zoomed SmallFace preview to set eye/mouth regions (`usEyesX/Y` + per-merc sub-frame size). The Traits step auto-detects whether the install runs OT or NT (Old vs New Trait System) via `Ja2_Options.ini` and surfaces the matching catalog.
- **Edit** any deployed merc — name, type, voice index, attributes, personality, biography on the Profile tab, plus dedicated tabs for **Portrait** (recompile the four STIs), **Voice** (drop .wav files into `Speech/<voice_index>/`), and **FaceGear** (capacity warning + custom overlay authoring — see below).
- **Duplicate** a merc into another slot. Source stays intact; useful for experimenting before committing.
- **Move** a merc to a different slot. Updates `MercProfiles.xml`, `AIMAvailability.xml`, `MercStartingGear.xml`, and the EDT bio at the correct routing.
- **Cross-install move**: pick a destination install from your registered list and Move sends the merc there end-to-end — exports to a temp bundle, imports into the target with its AIM tables, voice files, and STIs, then clears the source. Both sides get backed up before any writes.
- **Delete** removes a merc cleanly across all six game files.

### FaceGear (sunglasses, hats, gas masks, helmets)
When a custom merc with a high `ubFaceIndex` equips face gear in vanilla JA2, the engine bounds-checks the STI frame count and crashes the process if it's short (`vobject.cpp:958` → `exit(0)`). MercWizard 2 closes this hole three ways:
- **Capacity banner** on the slot step and the Edit > FaceGear tab — green when every `Face_*.sti` in the install covers your merc's face index, red with a one-click "Extend (with backup)" button when any don't (appends transparent frames so the merc renders blank but doesn't crash).
- **Orphan-pair detection** — flags any `Face_X.sti` missing its `Face_X_IMP.sti` partner. The engine crashes at BOOT if either is missing, regardless of which merc is being played. Surfaced as a separate red warning with the offending file list.
- **Per-merc overlay authoring** on the Edit > FaceGear tab — two workflows per item: **Auto** copies a stock frame from the install and shifts its `sOffsetX/sOffsetY` by the eye-coord delta between your merc and the source (one-click, no art needed), or **Upload PNG** writes a custom 48×43 overlay verbatim. Both mirror to the `_IMP.sti` partner and back up first.

### Backgrounds
The Biography step's background dropdown reads the active install's `Backgrounds.xml` and lets you pick one — `usBackground` defaults to 255 (none) but real backgrounds grant the merc sector-AP modifiers, skill bonuses, and other engine effects. Empty/missing `Backgrounds.xml` (some pre-STOMP mods) gracefully falls back to "None."

### `.wmerc` bundles
A `.wmerc` is a portable zip containing a merc's profile, gear, AIM/MERC website binding, EDT biography, portrait PNGs, voice clips, signature-item STIs, and mod-specific XML rows.

- **Export** writes a `.wmerc` to anywhere on disk. One click on the Hub or hover-export from the Roster.
- **Import** reads a `.wmerc` and deploys it into any free slot in the active install. The wizard re-derives `AimBioID` AND `MercBioID` for the target slot (so a bundle built for slot 220 won't collide if you drop it into slot 175), recompiles the portrait STIs at the target's face index, and copies voice clips into `Speech/<usVoiceIndex>/` (or the slot-prefix `Speech/<slot>_X.ogg` layout if the target mod uses Vengeance-style audio).

Full format spec + import/export semantics: [`docs/WMERC_FORMAT.md`](docs/WMERC_FORMAT.md). Pydantic schema authority: [`sidecar/mercwizard_core/bundle/manifest.py`](sidecar/mercwizard_core/bundle/manifest.py).

### Auto-backup before every write
Every destructive operation snapshots the affected files to `%APPDATA%\MercWizard\backups\<install_id>\<timestamp>__<reason>\` before touching them. The Backups page lists everything; one click restores the snapshot.

### Save-game scanner
The Saves page scans your `Save Games` folder, extracts merc nicknames from save metadata, and flags any save that depends on a merc you've edited or moved.

### Voice clip manager
Drop `.wav` files into a merc's `Speech/<usVoiceIndex>/` folder through the UI. No need to dig through Data-1.13 yourself.

### Launch JA2
A button on the Hub starts `ja2.exe` against the active install so you can verify changes in-game without alt-tabbing to Explorer.

## Engine-correctness rules baked in

The library encodes several engine truths that hand-edited XML and older tools routinely violate:

| Concern | What the wizard does |
|---|---|
| Expanded-AIM EDT bug | Routes bios via `AimBioID × 1120` (not `uiIndex × 1120`) for slots 170+. compile_merc.py and most forum guides get this wrong; the wizard reads `AimBioID` from `AIMAvailability.xml` and writes at the correct offset. |
| MERC EDT routing | Type=2 (M.E.R.C.) bios go to `MERCBIOS.EDT` at `MercBioID × 1120` (not `MercEdt/<n>.EDT` — the engine doesn't read that for Type=2). Symmetric fix with the AIM bug above. |
| SmallFace STI layout | Always 8 frames: 1 base 48×43 + 4 eye + 3 mouth. Sub-frame sizes are **mod-defined** (engine reads `usEyesWidth/Height` from the per-frame ETRLE header) — vanilla uses 17×6/14×6, Vengeance uses 31×13/32×21. All eye frames must share a size and all mouth frames must share a size. Palette index 0 is reserved for transparency **by construction** (quantize to 255 colors + shift indices +1 + write (0,0,0) at palette[0]). rawmode='RGB' on the palette object. The engine has no fallback for fewer frames — it crashes on render. |
| FaceGear capacity | Detects every `Face_*.sti` frame count and warns when your merc's `ubFaceIndex` exceeds it. The engine's `SGP_THROW_IFFALSE` at `vobject.cpp:958` calls `exit(0)` on out-of-range — not a render glitch, a real process termination. One-click "Extend (with backup)" appends transparent placeholder frames so the merc renders blank instead of crashing. |
| FaceGear positioning | Engine adds per-frame signed `sOffsetX/sOffsetY` to the bottom-anchored blit position (`vobject_blitters.cpp:319-320`). Auto-position writes the right offset computed from the merc's `usEyesX/usEyesY`. Handles the ja2py UINT16-vs-engine-INT16 signed-encoding gotcha (`v % 65536` on write). |
| Body type | Validates `ubBodyType` against the closed `SoldierBodyTypes` enum (humans 0-3, monsters 42-48, animals 59-61). Out-of-enum values index past `gAnimControl[]` and crash on render — wizard blocks them with an audit error. Cross-checks `bSex` against the body's implicit sex (REGMALE+female → warning). |
| Trait system | Detects OT vs NT per-install via `Ja2_Options.ini`'s `ENABLE_NEW_TRAIT_SYSTEM` and surfaces the right catalog. Same integer ID means different traits between systems (NT 13 = Night Ops, OT 13 = Knifing) — the picker prevents picking from the wrong system. Same Major trait twice in NT = Expert tier; surfaced as a hint. |
| Schema-aware writer | Reads the install's existing `MercProfiles.xml` fields and only writes the intersection — Arulco Revisited (pre-STOMP) profiles don't get `bRace`/`usBackground` stuffed in; AIMNAS profiles don't get both `bEvolution` and `fRegresses` written. New blank files get the full set. |
| `mAbsolutePrice` | Always `-1` so the engine auto-calculates. `0` greys the gear out in the AIM hiring UI. |
| `bEvolution` | Defaults to `0`. Missing it reads uninitialized memory. |
| Profile slot occupancy | "Empty" means both `<zName>` and `<zNickname>` are blank, not absence of the `<PROFILE>` block. Matches what the engine actually checks. |

## Where things live

| File / folder | What's there |
|---|---|
| `%APPDATA%\MercWizard\backups\<install_id>\` | Auto-backups (timestamp + reason directory per snapshot) |
| `%APPDATA%\MercWizard\logs\sidecar.log` | Sidecar Python log — useful if something hangs |
| `<your install>\Data-1.13\TableData\MercProfiles.xml` | Where mercs live |
| `<your install>\Data-1.13\BinaryData\AIMBIOS.EDT` | AIM bios (1120 bytes per record) |
| `<your install>\Data-1.13\BinaryData\MERCBIOS.EDT` | MERC bios |
| `<your install>\Data-1.13\faces\<face_index>.sti` | Portrait files (SmallFace + 65/33/BigFaces subdirs) |
| `<your install>\Data-1.13\Speech\<voice_index>\` | Voice clips |

## System requirements

- Windows 10 or 11, x64
- ~250 MB free disk for the install + backups (offline variant; lite is smaller)
- Microsoft WebView2 runtime
  - With the **offline installer**: bundled inside the .exe; works without internet
  - With the **lite installer**: pulled from Microsoft at install time if not already on the machine (~1.7 MB one-time download). WebView2 ships preinstalled with Edge on Windows 10 1803+ and Windows 11, so on almost any modern Windows install this is a no-op.

The Python sidecar is bundled as a single PyInstaller `--onefile` EXE — no Python install needed, no separate VC++ Redistributable required. Pillow, lxml, pydantic, and all transitive native deps are inside the EXE.

## Known limitations

- **AIM description** field in `AIMAvailability.xml` is preserved verbatim from the bundle on import. If you export Sulik from slot 1 (whose AIM description happens to be "Blood" in your install) and import to slot 37, the AIM entry there will read "Blood" until you Edit it.
- **STI files outside the standard four sizes** (e.g., custom mod portraits at non-canonical resolutions) aren't compiled. The wizard writes the canonical 48×43 (SmallFace), 31×27 (65Face), 15×14 (33Face), and 106×122 (BigFace) STIs and nothing else.
- **Edit form** doesn't include portrait/gear/voice editors — those have dedicated tools, but you can't change them on the Edit page directly. Re-Create the merc if you need a full overhaul.
- **`force=true` overwrites** clear the previous occupant's EDT bio before writing the new one, but they don't touch the target's STI files. If the displaced merc had a custom portrait at the same face index, it gets replaced. The backup captures everything, so restore is one click away.
- **Cross-install move** preserves `usVoiceIndex` from the source merc, which means voice clips land in the target install's `Speech/<source_voice_index>/` folder. If that voice index collides with an existing voice donor in the target install, the imported clips overwrite. Use a fresh voice index per merc if you care about isolation.

## Engine version compatibility

Tested against Jagged Alliance 2 v1.13 stable release (build 8915+) on the following mods:
- Vanilla 1.13
- AIMNAS
- Wildfire
- The Wasteland (Fallout 2 total conversion)
- Urban Chaos
- Vengeance Reloaded (VFS-aware: reads from `Data-Vengeance/`, writes to the mod content layer)

The wizard parses each install's `vfs_config.<Mod>.ini` and routes every read/write to the mod's actual content layer rather than the empty vanilla `Data-1.13/` stub — works correctly on chained-layer installs (Vengeance, AIMNAS+Bigmaps, UC113+UC113NewMaps, etc.).

Older v1.12 and pre-1.13 builds aren't supported — the wizard expects expanded AIM slots, expanded MERC slots, and the per-file EDT format introduced in 1.13.

## Reporting bugs

This is a beta, so please file what you hit: <https://github.com/bakedlays-glitch/merc-forge/issues>. Include your JA2 mod/install, the steps that triggered it, and attach the sidecar log at `%APPDATA%\MercWizard\logs\sidecar.log` — it records errors with full tracebacks and never contains secrets. If something looks wrong in-game after an edit, the **Backups** page restores the previous state in one click.

## License

Merc Forge's own source is licensed under the MIT License — see [LICENSE](LICENSE). Bundled third-party software and game-derived content are attributed in [NOTICE](NOTICE); notably, the vendored `ja2py` library is LGPL-3.0.

## For developers

Source layout, build commands, and architecture notes live in [DEVELOPER.md](DEVELOPER.md). The Python sidecar at `sidecar/` has 500+ pytest tests covering audit rules, STI generation, EDT routing, bundle round-trip, cross-install move, security (path traversal), and the explicit-frames animation pipeline. The Tauri shell at `shell/` is a thin wrapper that picks the sidecar port, manages the watchdog, and ships the WebView2 bootstrapper.
