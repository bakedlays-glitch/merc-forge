# Merc Forge

A standalone Windows toolkit for the Jagged Alliance 2 community — tested on vanilla, AIMNAS, Wildfire etc.

There are currently 6 modules and they all work, but I built these tools to help me make my mod and some have been tested more than others.

- **Map Forge** *(used regularly — the most proven part of the app)* — open any 1.13 sector `.dat`, paint tiles with an iso-faithful renderer, place multi-tile structures, run generators and validators, and save back to disk with crash-recovery autosave.
- **Merc Wizard** *(partly exercised)* — create, edit, move, duplicate, delete, import, and export mercs (including recruitable RPCs) across installs. Creating and editing a merc has real mileage; cross-install move, RPC authoring, and FaceGear overlay authoring do not.
- **Tileset Editor** *(barely used)* — build a tileset: add STIs from the library, inject sub-frames into existing slots, view and edit JSD collision companions.
- **Voice Lab** *(barely used)* — audit, cut, replace, preview, and deploy a merc's speech lines, with a journalled deploy and one-click Undo.
- **INI Editor** *(barely used)* — edit engine settings across the install's INI files, either as per-campaign override files or in place.
- **Backgrounds / Items** *(barely used)* — manage `Backgrounds.xml` (including a cross-mod library harvested from every mod on disk) and browse/edit `Items.xml` with per-class stats and inventory graphics.

Plus **Tools** (STI viewer, SLF extractor) that work on any asset, inside an install or out — read-only, so the cheapest thing here to trust.

> **Beta, and a work in progress. Read this before pointing it at an install you care about.**
>
> Only **Map Forge** has seen sustained real-world use — it gets used to build maps, regularly, by an actual human. Every other module is **lightly tested at best**: they pass an automated suite, but most have never been exercised end-to-end against a real modding session. Treat Voice Lab, the INI Editor, Items, Backgrounds, the Tileset Editor, and the less-travelled corners of the Merc Wizard (cross-install move, RPC authoring, FaceGear overlays) as unproven. Expect bugs, expect unfinished edges, and expect some of it to be plain wrong on a mod that isn't one of the few listed under [Engine version compatibility](#engine-version-compatibility).
>
> Automated tests are not user testing. A green suite means the code does what it was written to do, not that the feature is right for your install.
>
> Mitigations, not guarantees: every operation backs up the files it touches before writing, and Settings restores any snapshot in a click. **Back up your install yourself anyway** before a bulk edit. Please [report bugs](#reporting-bugs) — on the untested modules, the bug reports are the testing.
>
> The installer isn't code-signed yet, so Windows SmartScreen will warn "unknown publisher" — click **More info → Run anyway**. (Some antivirus may also flag the bundled Python sidecar — a known PyInstaller false-positive, not malware.)
>
> **Disclaimer.** Merc Forge is an unofficial, fan-made tool for the Jagged Alliance 2 v1.13 community and is **not** affiliated with or endorsed by Sir-Tech, Strategy First, THQ Nordic, or the JA2 1.13 development team. It ships no game executable — bring your own legally obtained copy of Jagged Alliance 2 and the v1.13 mod. All game trademarks and assets belong to their respective owners; redistributed third-party and game-derived content is attributed in [NOTICE](NOTICE).

## Quick install

The current build is `1.0.0-beta.4`; the Windows installer filename follows the release tag. The lite installer pulls the ~1.7 MB WebView2 bootstrapper from Microsoft at install time if WebView2 isn't already on the machine. On Windows 10 (1803+) and Windows 11 WebView2 is preinstalled, so this is usually a no-op and the install completes offline.

Steps:

1. Run the setup `.exe`.
2. Launch **Merc Forge** from the Start menu.
3. On first run, point it at your JA2 1.13 install directory. Merc Forge auto-detects Steam, GOG, and common manual paths; if those don't find yours, browse to it yourself.

No Python, no Node, no MSVC runtime download required either way — the sidecar EXE is a PyInstaller `--onefile` bundle that carries its own interpreter and runtime DLLs.

What changed between releases: [CHANGELOG.md](CHANGELOG.md). What's still open: [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## Merc Wizard

### Roster management
- **Create** new mercs through a 9-step wizard: slot → identity → portrait → attributes → traits → biography → gear → voice → review. Pick AIM, M.E.R.C., or RPC on the slot step (Speck's regular service, the budget service for non-human companions, or a recruitable NPC). Optionally pre-fill every field from an existing merc with "Start from existing." The portrait pipeline takes any PNG and writes engine-correct 8-frame SmallFace STIs (the format JA2 needs for blink/talk animation) at all four canonical sizes. Drag rectangles on the zoomed SmallFace preview to set eye/mouth regions (`usEyesX/Y` + per-merc sub-frame size). The Traits step auto-detects whether the install runs OT or NT (Old vs New Trait System) via `Ja2_Options.ini` and surfaces the matching catalog.
- **Edit** any deployed merc across six tabs — **Profile** (name, type, voice index, attributes, personality, gear, biography), **Portrait** (recompile all four STIs; preview every size plus the base/blink/talk animation frames), **Voice** (drop `.wav` files into `Speech/<voice_index>/`), **FaceGear** (capacity warning, overlay authoring, in-game composite preview — see below), and, for RPCs, **Placement** and **Recruitment**.
- **Duplicate** a merc into another slot. Source stays intact; useful for experimenting before committing.
- **Move** a merc to a different slot. Updates `MercProfiles.xml`, `AIMAvailability.xml`, `MercStartingGear.xml`, and the EDT bio at the correct routing.
- **Cross-install move**: pick a destination install from your registered list and Move sends the merc there end-to-end — exports to a temp bundle, imports into the target with its AIM tables, voice files, and STIs, then clears the source. Both sides get backed up before any writes.
- **Delete** removes a merc cleanly across all six game files.
- **Save-game awareness**: before a destructive change, the app scans your `Save Games` folder and warns inline when the merc you're about to move, edit, or delete appears in existing saves.

### Recruitable RPCs
Author a recruitable NPC end to end: map placement, recruit dialogue, and the fact triggers that gate it. RPCs get a separate 90×100 talk-panel face alongside the four standard portrait sizes, and small-face coordinate overrides are written to `RPCFacesSmall.xml`. Dialogue editing is sparse — it compacts what the engine reads without disturbing hand-authored neighbours.

### FaceGear (sunglasses, hats, gas masks, helmets)
When a custom merc with a high `ubFaceIndex` equips face gear in vanilla JA2, the engine bounds-checks the STI frame count and crashes the process if it's short (`vobject.cpp:958` → `exit(0)`). Merc Forge closes this hole several ways:
- **Capacity banner** on the slot step and the Edit > FaceGear tab — green when every `Face_*.sti` in the install covers your merc's face index, red with a one-click "Extend (with backup)" button when any don't (appends transparent frames so the merc renders blank but doesn't crash).
- **Orphan-pair detection** — flags any `Face_X.sti` missing its `Face_X_IMP.sti` partner. The engine crashes at BOOT if either is missing, regardless of which merc is being played. Surfaced as a separate red warning with the offending file list.
- **Per-merc overlay authoring** — two workflows per item: **Auto** copies a stock frame from the install and shifts its `sOffsetX/sOffsetY` by the eye-coord delta between your merc and the source (one-click, no art needed), or **Upload PNG** writes a custom 48×43 overlay verbatim. Both mirror to the `_IMP.sti` partner and back up first.
- **Composite preview** with exact-layer targeting, so gear is judged as the engine will draw it rather than as a flat overlay.

### `.wmerc` bundles
A `.wmerc` is a portable zip containing a merc's profile, gear, AIM/MERC website binding, EDT biography, portrait PNGs, voice clips, signature-item STIs, and mod-specific XML rows.

- **Export** writes a `.wmerc` to anywhere on disk, from the roster grid.
- **Import** reads a `.wmerc` and deploys it into any free slot in the active install. The wizard re-derives `AimBioID` AND `MercBioID` for the target slot (so a bundle built for slot 220 won't collide if you drop it into slot 175), recompiles the portrait STIs at the target's face index, and copies voice clips into `Speech/<usVoiceIndex>/` (or the slot-prefix `Speech/<slot>_X.ogg` layout if the target mod uses Vengeance-style audio).

Full format spec + import/export semantics: [`docs/WMERC_FORMAT.md`](docs/WMERC_FORMAT.md). Pydantic schema authority: [`sidecar/mercwizard_core/bundle/manifest.py`](sidecar/mercwizard_core/bundle/manifest.py).

## Map Forge

A sector editor for 1.13 `.dat` maps, rendered client-side.

- **Iso-faithful rendering** — the engine's own depth model for the struct/roof/onroof tier, engine-added foliage shadows drawn as the game adds them, and a full-resolution detail window on zoomed bigmaps.
- **Mode-less placement** — select a sprite, a ghost arms for placement, and validity is checked as you hover (footprint, occupancy, worst-verdict-wins per tile). A sidecar **placement oracle** refines that with the engine's own category and tier tables, and degrades to local-only checks when unavailable. The older modal tool bar is still available behind a `legacyTools` setting.
- **Editing verbs** — sprite clipboard (slice, paste, delete, move, cycle), marquee selection, grab-and-drag with shadow preview, fence line-drag with topology-correct corners, a placement queue committed as one undo stroke, persisted control groups (`Ctrl+N` save, `N` recall), `WASD` camera pan, and a command card for payload/shape verbs.
- **Generators and validators** — parametric generators (including four engine-LUT socket smoothers for terrain, walls, water, and caves) with clamped parameters, plus a validation pass that reports corner-art problems, shared room ids, and engine-invalid sheet registrations before you save.
- **Read-only tactical overlay** — NPCs drawn as their actual body sprites, world items as their BIGITEMS graphics, door/exit-grid/edgepoint markers, light records, and NPC schedule waypoints, parsed straight out of the map appendix.
- **Tileset browsing** — 203 registered sheets with human-readable prop names rather than STI filenames, 34 aliases, and 36 custom prefixes covering the New Vegas and Fallout 2 families.
- **Crash-recovery autosave** — a background autosaver (30s default) writes each dirty session to a recovery `.dat` in its per-map backup folder, outside the install. Reopening a map with a differing snapshot offers Restore / Discard; the on-disk file is untouched until you save.

## Auto-backup before every write

Every destructive operation snapshots the affected files to `%APPDATA%\MercWizard\backups\<install_id>\<timestamp>__<reason>\` before touching them. Settings lists every snapshot and restores one in a click; the store prunes to the 50 most recent. Map Forge keeps its own per-map backup folder, also outside the install.

## Launch JA2

A button on the Hub starts `ja2.exe` against the active install so you can verify changes in-game without alt-tabbing to Explorer. After a run, a health chip reports whether the engine logged INI errors and deep-links to the INI Editor.

## Engine-correctness rules baked in

The library encodes several engine truths that hand-edited XML and older tools routinely violate:

| Concern | What the app does |
|---|---|
| Expanded-AIM EDT bug | Routes bios via `AimBioID × 1120` (not `uiIndex × 1120`) for slots 170+. Most forum guides get this wrong; Merc Forge reads `AimBioID` from `AIMAvailability.xml` and writes at the correct offset. |
| MERC EDT routing | Type=2 (M.E.R.C.) bios go to `MERCBIOS.EDT` at `MercBioID × 1120` (not `MercEdt/<n>.EDT` — the engine doesn't read that for Type=2). Symmetric fix with the AIM bug above. |
| SmallFace STI layout | Always 8 frames: 1 base 48×43 + 4 eye + 3 mouth. Sub-frame sizes are **mod-defined** (engine reads `usEyesWidth/Height` from the per-frame ETRLE header) — vanilla uses 17×6/14×6, Vengeance uses 31×13/32×21. All eye frames must share a size and all mouth frames must share a size. Palette index 0 is reserved for transparency **by construction** (quantize to 255 colors + shift indices +1 + write (0,0,0) at palette[0]). rawmode='RGB' on the palette object. The engine has no fallback for fewer frames — it crashes on render. |
| FaceGear capacity | Detects every `Face_*.sti` frame count and warns when your merc's `ubFaceIndex` exceeds it. The engine's `SGP_THROW_IFFALSE` at `vobject.cpp:958` calls `exit(0)` on out-of-range — not a render glitch, a real process termination. One-click "Extend (with backup)" appends transparent placeholder frames so the merc renders blank instead of crashing. |
| FaceGear positioning | Engine adds per-frame signed `sOffsetX/sOffsetY` to the bottom-anchored blit position (`vobject_blitters.cpp:319-320`). Auto-position writes the right offset computed from the merc's `usEyesX/usEyesY`. Handles the ja2py UINT16-vs-engine-INT16 signed-encoding gotcha (`v % 65536` on write). |
| Body type | Validates `ubBodyType` against the closed `SoldierBodyTypes` enum (humans 0-3, monsters 42-48, animals 59-61). Out-of-enum values index past `gAnimControl[]` and crash on render — blocked with an audit error. Cross-checks `bSex` against the body's implicit sex (REGMALE+female → warning). |
| Trait system | Detects OT vs NT per-install via `Ja2_Options.ini`'s `ENABLE_NEW_TRAIT_SYSTEM` and surfaces the right catalog. The same integer ID means different traits between systems (NT 13 = Night Ops, OT 13 = Knifing) — the picker prevents picking from the wrong system. Same Major trait twice in NT = Expert tier; surfaced as a hint. |
| Schema-aware writer | Reads the install's existing `MercProfiles.xml` fields and only writes the intersection — Arulco Revisited (pre-STOMP) profiles don't get `bRace`/`usBackground` stuffed in; AIMNAS profiles don't get both `bEvolution` and `fRegresses` written. New blank files get the full set. |
| XML encoding | The engine's expat parser accepts UTF-8 in practice, so every table is written UTF-8 with a declaration, and edits to hand-authored files are byte-spliced rather than reflowed — a Windows-1252 sibling row can't be mojibaked into a file the engine then refuses to load. |
| `mAbsolutePrice` | Always `-1` so the engine auto-calculates. `0` greys the gear out in the AIM hiring UI. |
| `bEvolution` | Defaults to `0`. Missing it reads uninitialized memory. |
| Profile slot occupancy | "Empty" means both `<zName>` and `<zNickname>` are blank, not absence of the `<PROFILE>` block. Matches what the engine actually checks. |
| Map engine asserts | `new-sector` stamps `ubMapVersion`; `inject-sub` refuses a JSD/STI frame-count mismatch; `set_room_id` is capped at `MAX_ROOMS`; legacy (<v7.0) exit-grid records read as the engine's 7 bytes; door records branch on the map's major version. |

## Where things live

| File / folder | What's there |
|---|---|
| `%APPDATA%\MercWizard\backups\<install_id>\` | Auto-backups (timestamp + reason directory per snapshot, pruned to 50) |
| `%APPDATA%\MercWizard\logs\sidecar.log` | Sidecar Python log — useful if something hangs |
| `<your install>\Data-1.13\TableData\MercProfiles.xml` | Where mercs live |
| `<your install>\Data-1.13\BinaryData\AIMBIOS.EDT` | AIM bios (1120 bytes per record) |
| `<your install>\Data-1.13\BinaryData\MERCBIOS.EDT` | MERC bios |
| `<your install>\Data-1.13\faces\<face_index>.sti` | Portrait files (SmallFace + 65/33/BigFaces subdirs) |
| `<your install>\Data-1.13\Speech\<voice_index>\` | Voice clips |
| `<your install>\Data-1.13\Maps\` | Sector `.dat` maps |

On a VFS install (Vengeance, AIMNAS+Bigmaps, UC113+UC113NewMaps, …) these paths resolve through the mod's content layer rather than the vanilla stub — see [Engine version compatibility](#engine-version-compatibility).

## System requirements

- Windows 10 or 11, x64
- ~250 MB free disk for the install + backups (offline variant; lite is smaller)
- Microsoft WebView2 runtime
  - With the **offline installer**: bundled inside the .exe; works without internet
  - With the **lite installer**: pulled from Microsoft at install time if not already on the machine (~1.7 MB one-time download). WebView2 ships preinstalled with Edge on Windows 10 1803+ and Windows 11, so on almost any modern Windows install this is a no-op.
- Optional: FFmpeg on `PATH` for Voice Lab's render and transcription features. The app starts and runs without it.

The Python sidecar is bundled as a single PyInstaller `--onefile` EXE — no Python install needed, no separate VC++ Redistributable required. Pillow, lxml, pydantic, and all transitive native deps are inside the EXE. It listens on loopback only, requires a per-launch token, and exits when the shell that spawned it goes away.

## Known limitations

- **AIM description** field in `AIMAvailability.xml` is preserved verbatim from the bundle on import. If you export Sulik from slot 1 (whose AIM description happens to be "Blood" in your install) and import to slot 37, the AIM entry there will read "Blood" until you Edit it.
- **STI files outside the standard sizes** (e.g., custom mod portraits at non-canonical resolutions) aren't compiled. The app writes the canonical 48×43 (SmallFace), 31×27 (65Face), 15×14 (33Face), and 106×122 (BigFace) STIs — plus the 90×100 talk-panel face for RPCs — and nothing else.
- **`force=true` overwrites** clear the previous occupant's EDT bio before writing the new one, but they don't touch the target's STI files. If the displaced merc had a custom portrait at the same face index, it gets replaced. The backup captures everything, so restore is one click away.
- **Cross-install move** preserves `usVoiceIndex` from the source merc, which means voice clips land in the target install's `Speech/<source_voice_index>/` folder. If that voice index collides with an existing voice donor in the target install, the imported clips overwrite. Use a fresh voice index per merc if you care about isolation.
- **FaceGear auto-positioning** lands close enough for roughly four out of five vanilla-style portraits. Vanilla art was hand-positioned rather than eye-coord-aligned, so outliers need the Upload PNG path.
- **Generated maps are candidates, not finished work.** The generator corpus doesn't record which tileset gave a subindex its visual meaning, so a generated sector wants a visual and in-engine review before you ship it.
- **Graphics stack** and **Game Setup** are not exposed in this release; the code remains in the tree but is unreachable from the UI.

## Engine version compatibility

Tested against Jagged Alliance 2 v1.13 stable release (build 8915+) on the following mods:
- Vanilla 1.13
- AIMNAS
- Wildfire
- The Wasteland (Fallout 2 total conversion)
- Urban Chaos
- Vengeance Reloaded (VFS-aware: reads from `Data-Vengeance/`, writes to the mod content layer)

The app parses each install's `vfs_config.<Mod>.ini` and routes every read/write to the mod's actual content layer rather than the empty vanilla `Data-1.13/` stub — works correctly on chained-layer installs (Vengeance, AIMNAS+Bigmaps, UC113+UC113NewMaps, etc.).

Older v1.12 and pre-1.13 builds aren't supported — the app expects expanded AIM slots, expanded MERC slots, and the per-file EDT format introduced in 1.13.

## Reporting bugs

This is a beta, so please file what you hit: <https://github.com/bakedlays-glitch/merc-forge/issues>. Include your JA2 mod/install, the steps that triggered it, and attach the sidecar log at `%APPDATA%\MercWizard\logs\sidecar.log` — it records errors with full tracebacks and never contains secrets. If something looks wrong in-game after an edit, Settings > Backups restores the previous state in one click.

## License

Merc Forge's own source is licensed under the MIT License — see [LICENSE](LICENSE). Bundled third-party software and game-derived content are attributed in [NOTICE](NOTICE); notably, the vendored `ja2py` library is LGPL-3.0.

## For developers

Source layout, build commands, and architecture notes live in [DEVELOPER.md](DEVELOPER.md). The Python sidecar at `sidecar/` has 1,500+ pytest tests covering audit rules, STI generation, EDT routing, bundle round-trip, cross-install move, security (path traversal), backup discipline, Voice Lab deploy and recovery, Map Forge, and the explicit-frames animation pipeline; the frontend carries its own Vitest suite and Playwright agendas. That coverage is broad but it is fixture-based — see DEVELOPER.md's "What the suite does not cover" for which modules have real-install mileage and which don't. The Tauri shell at `shell/` is a thin wrapper that picks the sidecar port, proves the lifeline token, manages the watchdog, and ships the WebView2 bootstrapper.
