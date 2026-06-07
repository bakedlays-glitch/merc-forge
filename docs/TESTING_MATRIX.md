# MercForge — Testing Matrix

Last updated: 2026-06-07 (added INI / GFX / GS sections — INI Editor, graphics station, game status).

A release-checklist deliverable: every major MercForge feature mapped
to:
1. **App-side sanity tests** — verify the GUI behaves and the
   sidecar's HTTP responses are correct
2. **In-game verification** — launch `ja2.exe` against the
   modified install and confirm the engine accepts + displays the
   change

A test passes only when BOTH sides pass. App-side success without
in-game confirmation is unproven — the engine is the ultimate arbiter.

## How to use this doc

- Run the **Pre-test setup** once per session
- Walk the matrix from top to bottom. Skip rows marked
  **(deferred)** unless you're chasing a specific regression
- Each row has an ID (e.g. `M-Create-1`) so failures can be filed
  without ambiguity
- The **🎮** column flags rows that require launching ja2.exe.
  Batch the 🎮 rows so you only launch the game once per session

## Pre-test setup

1. Stop any running Merc Forge / mercwizard / mercwizard_core process
2. Launch from the fresh distributable:
   `<repo>\shell\target\release\mercwizard.exe`
3. Settings → About — verify "Frontend built" timestamp matches your
   last `launch_current.ps1` / Tauri build. If stale, **stop and
   rebuild** before testing
4. Pick a test install — recommend a **throw-away copy** of one of your
   JA2 1.13 installs (one you don't mind breaking)
5. Confirm it's set active in Settings → Game installs
6. Confirm `mercwizard_core.exe` mtime in `shell/target/release/`
   matches PyInstaller's last run

## Verification environments

| Env | Path | Purpose |
|-----|------|---------|
| Test install | A throw-away copy of a modded 1.13 install | Destructive testing |
| Reference install | A pristine modded 1.13 install | Pristine baseline, don't write to |
| Vanilla baseline | A stock 1.13 install | Cross-mod portability checks |

Before destructive tests on **Test install**, take a manual backup:
`Copy-Item -Recurse "<your test install>" "<your test install>.snapshot-YYYY-MM-DD"`

---

# 1. Application boot + install management

## A-Boot — App launch sanity

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| A-Boot-1 | Cold launch | Kill all instances, run `mercwizard.exe` | Window opens within 5s; no error dialogs | |
| A-Boot-2 | Sidecar spawned | Open Task Manager during launch | `mercwizard_core.exe` running as child of `mercwizard.exe` | |
| A-Boot-3 | Settings → About is fresh | Check the build timestamp shown | Matches your last build (< 30 min old in a dev session) | |
| A-Boot-4 | Sidecar version shown | Settings → About | "Sidecar version: 2.0.0" appears | |
| A-Boot-5 | Watchdog respawns sidecar | Task Manager → kill `mercwizard_core.exe` | Sidecar respawns within ~4s; app stays usable | |
| A-Boot-6 | Repeated kill backs off | Kill sidecar 5x in a row | Respawn delay increases (4→8→16→32→60s cap) | |

## A-Install — Install detection + active switching

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| A-Install-1 | Manual add | Settings → Add install → pick a valid 1.13 path | Install appears in list with `mod_id` populated | |
| A-Install-2 | Reject invalid path | Pick `C:\Windows` | Error: "INVALID_INSTALL" / required files missing | |
| A-Install-3 | Set active | Click "Set active" on a registered install | Health → active_install_id updates; Hub roster reloads | |
| A-Install-4 | Program Files warning | Add a path inside `C:\Program Files` | Warning banner about UAC write-protection appears | |
| A-Install-5 | VFS Mod Selector | Register a path with multiple `vfs_config.*.ini` files | Wizard surfaces the list; selecting a non-active variant prompts confirmation | |
| A-Install-6 | VFS broken-config refusal | Manually corrupt a vfs_config file, try to save a merc | Error message names the broken config (not silent fallback to Data-1.13) | |

---

# 2. Roster + portraits

## M-Roster — Grid + filters

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Roster-1 | All 256 slots render | Open Merc Wizard tile | 256 cells visible in a 16-col grid | |
| M-Roster-2 | Filled cells highlighted | Visual inspection | Cells with mercs are visually distinct from empty | |
| M-Roster-3 | Filter chips work | Click "AIM", "MERC", "RPC", "NPC", "Empty" | Grid greys out non-matching slots; filled set updates per chip | |
| M-Roster-4 | Search by name | Type "Chosen" into search | Only matching slot(s) remain highlighted | |
| M-Roster-5 | Search by slot # | Type "206" | Only slot 206 remains highlighted | |
| M-Roster-6 | Selection sidebar | Click slot 0 | Right sidebar shows name, type, category, BigFace portrait | |
| M-Roster-7 | Right-click context | Right-click filled slot | Context menu with Edit/Copy/Move/Export/Replace/Delete | |
| M-Roster-8 | Right-click empty slot | Right-click empty slot | Context menu with Create/Import/Copy here | |

## M-Portrait — Thumbnails + cache

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Portrait-1 | Filled slot shows face | Look at slot 0 (Chosen) | 48×43 portrait visible inside the cell, slot number in corner | |
| M-Portrait-2 | Empty slot shows just # | Look at any empty slot | Centered slot number, no img element rendered | |
| M-Portrait-3 | Missing STI graceful | Pick a slot whose ubFaceIndex points at a deleted face | Cell falls back to slot number, no broken-img icon | |
| M-Portrait-4 | BigFace in sidebar | Click slot with portrait | 106×122 BigFace appears in sidebar with pixelated rendering | |
| M-Portrait-5 | Cache busts on Edit | Edit slot's face, save, return to roster | New portrait visible without page reload (within 1s) | |
| M-Portrait-6 | Cache busts on Replace | Replace slot's merc with a new one | New portrait visible | |

---

# 3. Create / Edit / Delete

## M-Create — New merc end-to-end

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Create-1 | Create at empty AIM slot | Slot 220, Type=1, name="TestAIM", min audit | Save succeeds; roster shows TestAIM at 220 | |
| M-Create-2 | Create at empty MERC slot | Slot 244, Type=2, name="TestMERC" | Save succeeds; MercAvailability row written with auto-allocated MercBioID | |
| M-Create-3 | NPC in AIM slot blocked | Slot 5, Type=3 | AUDIT_FAILED with NPC_IN_AIM_SLOT code | |
| M-Create-4 | Empty fields rejected | Try to save with no name | Save button disabled / validation message | |
| M-Create-5 | Bio over 400 chars | Paste 500-char bio | FIELD_TOO_LONG audit error | |
| M-Create-6 | Bio with emoji | Bio = "Hot stuff 🔥" | CONTAINS_UNENCODABLE warning surfaces BEFORE save | |
| M-Create-7 | Face index cap | ubFaceIndex=260 | FACE_INDEX_EXCEEDS_ENGINE_CAP error | |
| M-Create-8 | Portrait compile | Upload PNG, click Compile | 4 STI files written; progress shows per-file labels | |
| M-Create-9 | Portrait rollback | Force a compile failure (eg malformed PNG) | Pre-compile state restored; PORTRAIT_COMPILE_FAILED error | |
| M-Create-🎮-1 | AIM merc visible | Create TestAIM at 220, launch game, open laptop → AIM | TestAIM appears in the hire list with their portrait | 🎮 |
| M-Create-🎮-2 | MERC merc visible | Create TestMERC at 244, launch game → Speck's | Appears on M.E.R.C. site with portrait | 🎮 |
| M-Create-🎮-3 | Bio renders | Read TestAIM's bio in the AIM page | Bio text matches what was typed (no garbled chars) | 🎮 |
| M-Create-🎮-4 | Hire and deploy | Hire TestAIM, deploy to a sector | Sprite renders + voice plays + portrait correct on stat bar | 🎮 |

## M-Edit — Update an existing merc

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Edit-1 | Change name | Edit slot 0, change zNickname | Save streams; roster updates | |
| M-Edit-2 | Change face | Upload new PNG | Portrait thumbnail updates in roster after save | |
| M-Edit-3 | Streaming progress | Watch the save bar | Per-step events: backup → profiles → edt → aim_avail | |
| M-Edit-4 | Per-file backup labels | Watch the backup row during save | List of file paths accumulates as backup runs | |
| M-Edit-5 | Audit fires on edit | Set ubFaceIndex > 255 | Audit error blocks the save | |
| M-Edit-6 | Rollback on mid-save error | (Hard to trigger — close the file in another process) | error_step surfaced; install reverted | |
| M-Edit-🎮-1 | Updated name in-game | Edit slot 0 name, launch, open AIM | New name shown | 🎮 |
| M-Edit-🎮-2 | Updated portrait in-game | Edit slot 0 portrait, launch, open AIM | New portrait shown | 🎮 |

## M-Delete — Remove a merc

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Delete-1 | Delete via context menu | Right-click filled slot → Delete | Confirmation modal; on confirm the slot goes empty | |
| M-Delete-2 | Replace flow | Right-click → "Replace with new" | Single confirmation; delete + navigate to Create at that slot | |
| M-Delete-3 | Replace delete-failure | (Hard to trigger — make profile file read-only) | Error shows above buttons; modal stays open; "Retry Delete" label | |
| M-Delete-4 | Rollback on mid-delete failure | Same trigger as above | DELETE_FAILED returned; install reverted; merc still present | |
| M-Delete-5 | All side state cleared | Delete a Type=1 merc, then inspect | MercProfiles, AIM row, gear, EDT bio all cleared | |
| M-Delete-🎮-1 | Deleted merc gone | Delete TestAIM, launch | Not in AIM hire list | 🎮 |

---

# 4. Move / Duplicate / Import / Export

## M-Move — Cut a merc to another slot

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Move-1 | Same-install move | Move slot 200 → slot 210 | Source empty; dest populated; AIM/MERC rows relocated | |
| M-Move-2 | Modal closes immediately | Click "Cut" on confirm dialog | Modal closes before stream finishes; progress bar visible | |
| M-Move-3 | Streaming labels | Watch the bar | backup → move steps each emit start/done events | |
| M-Move-4 | Cross-category notice | Move Type=1 from 200 → empty 244 | Blue info: "MercForge will register slot 244 on AIM…" | |
| M-Move-5 | RPC→AIM warns | Move Type=3 to slot 5 | Yellow warn: "RPC stays scripted, won't appear on AIM" | |
| M-Move-6 | Cross-install move | Move slot 200 in install A → slot 200 in install B | Both installs reflect; bundle pipeline log visible | |
| M-Move-🎮-1 | Moved merc at new slot | Same-install move, launch | Source slot empty in AIM, dest slot has the merc | 🎮 |
| M-Move-🎮-2 | Cross-install move | Cross-install move, launch destination install | Merc present with portrait + bio | 🎮 |

## M-Duplicate — Non-destructive copy

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Dup-1 | Same-install copy | Copy slot 0 → empty 206 | Source intact; dest populated; new AimBioID allocated | |
| M-Dup-2 | Modal closes immediately | Click Copy on confirm | Modal closes; progress bar visible | |
| M-Dup-3 | Same portrait reused | Inspect both slots | ubFaceIndex matches; both use same STIs | |
| M-Dup-4 | Independent gear | Inspect MercStartingGear | Each slot has its own block | |
| M-Dup-🎮-1 | Both appear on AIM | Duplicate, launch, open AIM | Both source + copy in the hire list | 🎮 |
| M-Dup-🎮-2 | Hire either | Hire each in turn | Both deploy correctly with the shared portrait | 🎮 |

## M-Import — .wmerc bundle

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Import-1 | Round-trip | Export slot 0, delete, import the bundle | Slot 0 restored with name + portrait + bio | |
| M-Import-2 | Cross-mod import | Export from Wasteland, import to Vanilla | Cross-mod schema warnings surfaced in report | |
| M-Import-3 | Partial failures visible | Import while a voice file is locked by another app | Banner flips green→yellow; "Some files were skipped"; file list shown | |
| M-Import-4 | Type warning surfaces | Import Type=AIM bundle into a slot with no AIM row | Yellow notice in the preview area | |
| M-Import-5 | Audit blocks bad import | Import bundle with FIELD_TOO_LONG bio | Import refused; audit list shown | |
| M-Import-🎮-1 | Imported merc playable | Round-trip a hireable merc, launch | Hireable on AIM/MERC with intact bio + portrait | 🎮 |

## M-Export — Generate .wmerc

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| M-Export-1 | Save-as dialog | Right-click slot → Export | Native file picker opens at default filename | |
| M-Export-2 | Bundle contents | Open `.wmerc` as a zip | manifest.json + portrait_source.png + voice/*.wav + facegear/* | |
| M-Export-3 | FaceGear preserved | Export a merc with custom goggles | facegear/Face_*.png entries present with mw2_offset_x/y metadata | |
| M-Export-4 | exported_at timestamp | Inspect manifest.json | Timestamp = export moment, not a re-parse time | |

---

# 5. FaceGear

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| FG-1 | Orphan banner appears | Delete one half of a FaceGear pair (e.g. `Face_X_IMP.sti`) | Red banner on Hub: "1 FaceGear STI pair missing — game will crash at boot" | |
| FG-2 | Unregistered orphan silent | Drop a `Face_KGoggles.sti` with no FaceGear.xml row | Banner does NOT fire | |
| FG-3 | Repair-all works | Click "Repair all" | Backup snapshot taken; missing partners get the present-file bytes copied; banner clears | |
| FG-4 | Overlay upload | Author tab → pick PNG → Write | Both base + IMP STIs updated; backup_id returned | |
| FG-5 | Auto-position | Pick a stock STI, click Auto | Eye-delta offset applied; success log shows source frame + offset | |
| FG-6 | Nudge widget | After Auto, click ↑ ←→ ↓ | Offset shifts by ±1 per click; "offset: (x, y)" updates | |
| FG-7 | Source-frame override | Type a slot # in the numeric input, click Auto | Wizard uses that slot's frame as source | |
| FG-8 | Extend STIs | Slot 220, click Extend | All face_*.sti reach >= 221 frames; backup_id returned | |
| FG-9 | Atomic STI write | Force-quit during a write (timing-sensitive) | No `.tmp` files left; existing STI uncorrupted | |
| FG-🎮-1 | Orphan boot-CTD averted | After FG-3, launch game | Boots cleanly (no `BltVideoObjectToBuffer NULL` assert) | 🎮 |
| FG-🎮-2 | Overlay visible | Apply overlay, launch, hire that merc | The face gear shows on their portrait in inventory | 🎮 |

---

# 6. Audit checks

These exercise the audit engine. Each row creates a merc with the
named violation and confirms the audit code surfaces.

| ID | Violation | Setup | Expected audit code |
|----|----------|-------|--------------------|
| AUD-1 | NPC in AIM slot | Slot 5, Type=3 | NPC_IN_AIM_SLOT (error) |
| AUD-2 | Bio too long | bio = 401 chars | FIELD_TOO_LONG (error) |
| AUD-3 | Bio near limit | bio = 390 chars | FIELD_NEAR_LIMIT (warn) |
| AUD-4 | Face index over engine cap | ubFaceIndex = 260 | FACE_INDEX_EXCEEDS_ENGINE_CAP (error) |
| AUD-5 | Face shadows vanilla | uiIndex=220, ubFaceIndex=26 | FACE_INDEX_SHADOWS_VANILLA (warn) |
| AUD-6 | Eye spacing too narrow | usEyesX=10, usMouthX=10 | EYE_SPACING_TOO_NARROW (warn) |
| AUD-7 | Eye spacing too wide | usEyesX=0, usMouthX=40 | EYE_SPACING_TOO_WIDE (warn) |
| AUD-8 | Emoji in nickname | zNickname = "🎯" | CONTAINS_UNENCODABLE (warn) |
| AUD-9 | Supplementary CJK in bio | bio with 𠀀 (U+20000) | CONTAINS_UNENCODABLE (warn) |
| AUD-10 | Clean merc | All fields valid | Zero errors, optional warns only |

---

# 7. Backups

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| B-1 | Snapshot taken on save | Create a merc, check Backups page | New entry appears with merc's name in the reason field | |
| B-2 | Restore from snapshot | Edit a merc, restore the pre-edit backup | Roster reverts; bio/portrait restored | |
| B-3 | Backups page lists entries | Open Backups | Reverse-chrono list; install_id-keyed | |
| B-4 | Auto-prune at 50 | Generate 60 snapshots | Older 10 deleted; backup count caps at 50 | |
| B-5 | Restore failure surfaces | (Hard to trigger — delete snapshot dir mid-restore) | BACKUP_NOT_FOUND error; install untouched | |

---

# 8. MapForge

## MF-Sector — Open + render + paint

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| MF-Sector-1 | Open loose .dat | Browse to Data-1.13/Maps/H4.dat | Canvas renders within 5s | |
| MF-Sector-2 | Open SLF-bundled | Browse to a sector inside Maps.slf | Renders read-only; log entry warns + points at Extract button | |
| MF-Sector-3 | Extract from SLF | Click "Extract to loose" prompt | Sector now editable; loose copy at Data-1.13/Maps/ | |
| MF-Sector-4 | Tile inspector | Click any tile | Sidebar shows X, Y, all layer entries, room ID | |
| MF-Sector-5 | Eyedropper | Right-click a tile | Active brush updates to that tile's primary entry | |
| MF-Sector-6 | Paint a tile | Pencil tool + brush + click | Tile updates; render reflects within ~100ms | |
| MF-Sector-7 | Multi-tile struct | Pick a building from library, paint | All footprint tiles get the slot/sub stamped | |
| MF-Sector-8 | Undo | Paint then Ctrl+Z | Tile reverts; undoDepth decrements | |
| MF-Sector-9 | Save | Click Save | `.bak` written first time; `.dat` rewritten; bytes_written reported | |
| MF-Sector-🎮-1 | Saved sector loads | Open the install in JA2, travel to that sector | Sector loads + renders without CTD | 🎮 |
| MF-Sector-🎮-2 | Painted tiles visible | Paint a distinctive ground in MapForge, save, load in-game | New tiles render correctly | 🎮 |

## MF-Generator — Console + Wizard

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| MF-Gen-1 | Console opens with `:` | Press `:` in MapForge | Bottom bar appears, input focused | |
| MF-Gen-2 | `:help` lists commands | Type `:help`, Enter | All 5 commands listed (save/undo/reload/help/gen) | |
| MF-Gen-3 | `:gen` lists generators | Type `:gen`, Enter | Lists wipe/fill/rect/scatter/cluster/density-falloff | |
| MF-Gen-4 | Tab autocomplete | Type `:ge` then Tab | Completes to `:gen` | |
| MF-Gen-5 | History recall | Run a command, press ↑ | Previous command surfaces | |
| MF-Gen-6 | Wizard launches | Click ✨ Generate… | Modal opens; step 1 shows generator cards | |
| MF-Gen-7 | Pick → configure | Click a card | Step 2 shows the param form | |
| MF-Gen-8 | Slider + number combo | Adjust a bounded int param | Slider + number stay in sync | |
| MF-Gen-9 | Layer dropdown | View the `layer` param | Dropdown of 6 valid layers, not free-text | |
| MF-Gen-10 | Advanced collapsed | View any generator with seed param | Seed appears under collapsed "Advanced" section | |
| MF-Gen-11 | Generate streams ops | Click Generate | Step 3 shows phase labels + live op count | |
| MF-Gen-12 | Result on success | After completion | Green card with "N ops in X ms" | |
| MF-Gen-13 | Run another | Click "Run another" | Returns to step 1 with reset state | |
| MF-Gen-14 | Esc closes mid-config | Step 2 + Esc | Wizard closes | |
| MF-Gen-15 | Esc gated mid-run | Step 3 + Esc | Wizard stays open (no premature close during stream) | |
| MF-Gen-16 | Wipe via wizard | Pick wipe → Generate | All tiles cleared; canvas paints row-by-row | |
| MF-Gen-17 | Fill via wizard | Fill layer=land slot=1 sub=0 | Whole ground layer becomes that tile | |
| MF-Gen-18 | Rect outline | x1=10 y1=10 x2=50 y2=50 mode=outline | Only perimeter painted | |
| MF-Gen-19 | Scatter reproducible | Same params + seed twice | Identical tile set | |
| MF-Gen-20 | Cluster patches | cluster_count=3 objects_per_cluster=20 | 3 distinct visual clumps | |
| MF-Gen-21 | Density falloff | center + radius 30 + peak 0.5 | Dense near focal, sparse at edges | |
| MF-Gen-22 | Undo a generator run | Generate → Ctrl+Z | Whole run reverts as one step | |
| MF-Gen-🎮-1 | Generated sector boots | Wipe + fill + save, launch | Sector loads without CTD; ground all matches the chosen tile | 🎮 |
| MF-Gen-🎮-2 | Scatter vegetation in-game | Scatter shrubs at OBJ layer, save, load | Shrubs appear at expected positions | 🎮 |

## MF-Library — Asset browser

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| MF-Lib-1 | Library opens | Click "Browse assets" | Modal opens with tileset's STIs listed | |
| MF-Lib-2 | Add to tileset | Pick a Library STI → "Add to current tileset" | Slot opens up; atlas re-bakes; new STI selectable as brush | |
| MF-Lib-3 | JSD viewer | Click an STI with JSD | Footprint shown in inspector | |

---

# 9. Voice

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| V-1 | Vanilla donor detected | Slot with usVoiceIndex=30 (Bull) | "Voice provided by base game archives" green message (not "no clips") | |
| V-2 | Upload clip | Voice tab → drag a .wav | Clip lands at Speech/<n>/<name>.wav | |
| V-3 | Slot-prefix layout | Vengeance install (uses slot_prefix) | Clip lands at Speech/<slot>_NNN.wav | |
| V-4 | Clip listing accurate | Run probe_voice_index | clip_count matches files in folder | |
| V-🎮-1 | Hired merc speaks | Hire merc with custom voice, deploy | Hire-line + battle barks play with the custom clips | 🎮 |

---

# 10. Build verification (single-machine release smoke test)

| ID | Test | Steps | Expected |
|----|------|-------|---------|
| BV-1 | All tests pass | `pytest sidecar/tests` | All passing (target: green, no skips beyond known) |
| BV-2 | Frontend tsc clean | `cd frontend && npx tsc --noEmit` | Zero errors |
| BV-3 | Vite build clean | `cd frontend && npx vite build` | Bundle produced, no errors |
| BV-4 | PyInstaller clean | `cd sidecar && .venv\Scripts\python.exe -m PyInstaller ...` | mercwizard_core.exe in sidecar/dist/, exit 0 |
| BV-5 | Tauri build clean | `cd shell && tauri build --no-bundle` | mercwizard.exe in shell/target/release/, exit 0 |
| BV-6 | Both artifacts paired | mtime within 5min of each other | Mismatch means a stale binary is staged |
| BV-7 | Cold launch from artifact | Run `mercwizard.exe` directly | Window opens; sidecar spawns; health endpoint responds |

---

# 11. Regression watch — things that broke before

These specific scenarios broke historically. Run them before any
release to catch a regression-by-revert.

| ID | Past bug | Repro | Don't-regress check |
|----|----------|-------|---------------------|
| R-1 | Item 250 boot-CTD (KGoggles) | Make sure Items.xml entry 250 has non-zero usItemClass | Game launches without arms-dealer assert |
| R-2 | Modal sat over progress bar | Open Duplicate, run | Modal closes IMMEDIATELY on Confirm click |
| R-3 | Backup files flashed by | Save a merc | File-list expands under backup row + persists |
| R-4 | Replace-with-new on delete failure | Force a delete error | Modal stays open with retry option; no nav to Create |
| R-5 | Stale portrait after recompile | Edit portrait, return to roster | Thumbnail updates (no 60s cache delay) |
| R-6 | Cross-category warning paranoid | Duplicate AIM merc to unassigned slot | Blue info notice, NOT yellow "won't appear" warning |
| R-7 | TS narrowing in Move success card | After a same-install move | Success card renders without console errors |
| R-8 | Slot 228 face index CTD | Create at slot 228 with ubFaceIndex=228 | Game loads merc; portrait blits without NULL deref |
| R-9 | Mod roster shows vanilla data | Open roster on a heavily-modded install | Roster reflects mod's MercProfiles.xml, not Profiles.slf |
| R-10 | Atomic STI write | Force-kill during FaceGear write | No `.tmp` files; STI unchanged |
| R-11 | VFS broken-config silent fallback | Corrupt vfs_config and try to save | Save refuses with VfsConfigError |
| R-12 | Item 250 usItemClass | Inspect the test install's Items.xml after KGoggles cleanup | usItemClass != 0 (IC_MISC or original IC_FACE) |
| R-13 | Generator setTimeout leak | Run a duplicate, navigate away within 2.5s | No "setState on unmounted" console warning |
| R-14 | Wall-clip overhang (lawless4 through walls) | Open `<test install>/Maps.slf!/C6.DAT`, navigate to tile (62, 86), inspect | Lawless4 sub 16 (AC unit) overhang is clipped behind walls (only ~1 px visible). Was 21+ px in painter-only renderer. |
| R-15 | Tauri stale embedded frontend | Edit any `frontend/src/**` file, run launcher | New code is in the running `.exe`. If `frontend/dist/*` is newer than `shell/target/release/mercwizard.exe`, `build.rs` should have caught it via `cargo:rerun-if-changed`. |

---

# 12. In-game scenarios (game-launch session)

Bundle 🎮 rows into a single game launch when possible. Recommended
sequence:

1. **Setup**: Pick Test install. Create or import 3 mercs at known slots
   (one AIM-vanilla, one AIM-expansion, one MERC).
2. **Edit one** of them to change name + portrait.
3. **Move one** to a new slot.
4. **Duplicate one** to a free slot.
5. **Apply FaceGear** overlay to one merc.
6. **Generate a sector** — wipe + fill ground + scatter vegetation.
7. **Save everything** (each surface has its own save).
8. **Quit MercForge**.
9. **Launch JA2.exe** for that install.
10. Open the laptop → AIM page → confirm all created mercs appear with
    correct names + portraits + bios.
11. Open the M.E.R.C. page → confirm M.E.R.C. mercs appear.
12. Hire the edited merc → confirm new name + portrait everywhere.
13. Hire one of the duplicates + deploy → confirm sprite renders + voice
    plays.
14. Equip the FaceGear-applied merc → confirm overlay visible in
    inventory portrait.
15. Travel to the generated sector → confirm tiles match what MapForge
    wrote.
16. Save the JA2 game → confirm save succeeds.
17. Reload → confirm state persists.

If steps 10–17 all pass: **release candidate is good for ship**.

---

# 13. Performance budgets + measurement scripts

**Why this exists:** 2026-05-25 — a `_build_atlas` change broke C5/C6 cold-bake because it shipped without before/after timing. New rule (per `feedback_verify_with_data_before_claiming_fix.md` 2026-05-25 corollary): no perf change ships without numbers.

## Budgets

Numbers below are MAX acceptable; lower is better. Anything over budget is a regression and blocks ship.

| Operation | Cold (no cache) | Warm (cached) | Measurement |
|----|---:|---:|---|
| App launch → main window visible | 5,000 ms | 5,000 ms | wall clock from `Start-Process` to window paint |
| Sidecar boot → /health 200 | 3,000 ms | 3,000 ms | shell log line gap |
| Roster route → 256 cells rendered | 2,000 ms | 1,000 ms | Network panel: GET /roster duration |
| `load_roster` (sidecar, ~250-merc install) | 800 ms | 500 ms | `perf_roster.py` → results_ms.load_roster_cold / _warm |
| `make_install_context` (modded install) | 800 ms | n/a | `perf_roster.py` → results_ms.make_install_context_cold |
| Roster portrait sheet (~250 mercs) | 8,000 ms (first bake) | 400 ms (disk cache, post-launch) | `perf_roster.py` → results_ms.portrait_sheet_bake_cold / _disk_hit |
| Create wizard route | 1,500 ms | 800 ms | route mount → form interactive |
| Portrait STI compile (Create) | 8,000 ms | 8,000 ms | NDJSON stream: first event → last event |
| MapForge route load | 2,000 ms | 1,000 ms | route mount → install/sector list rendered |
| Sector open → canvas painted | **15,000 ms** | **3,000 ms** | POST /mapforge/sessions → first render epoch |
| `_build_atlas` cold | **10,000 ms** | n/a | `perf_atlas_bake.py --cold` |
| `_build_atlas` warm | **300 ms** | n/a | `perf_atlas_bake.py` (post-cache) |
| `_build_palette_sheet` cold | 8,000 ms | n/a | `perf_palette_sheet.py --cold` |
| `_build_palette_sheet` warm | 200 ms | n/a | `perf_palette_sheet.py` |
| `compute-zstrips` phase (in `_build_atlas`) | 500 ms | 10 ms | `perf_atlas_bake.py --json` → phases array |
| WebGL render (cold C6 sector full) | **30 ms** | **30 ms** | `IsoRendererGL.getLastRenderMs()` after first paint; check browser console `[IsoRendererGL] render #1: N ms` |
| WebGL render (interactive pan/zoom) | 16 ms (60 FPS) | 16 ms | `getLastRenderMs()` sampled while panning |
| Save sector (typical) | 3,000 ms | n/a | save button → `bytes_written` event |
| Generator run (small, ~100 ops) | 5,000 ms | n/a | Generate click → result card |
| Backup snapshot (typical merc) | 2,000 ms | n/a | save → "backup taken" log |

Budgets are CONSERVATIVE — the goal is "no obvious wait" for the YouTube demo. If we want demo-grade snappy, halve everything that isn't an inherently slow operation (cold bakes, STI compile).

## Debug mode (caches disabled)

For perf testing + cold-path verification, launch with `launch_debug.ps1`:

```powershell
cd "<repo>"
.\launch_debug.ps1
```

Console invocation (from Bash via PowerShell):

```bash
powershell -ExecutionPolicy Bypass -File "<repo>\launch_debug.ps1"
```

Effect: sets `MERCWIZARD_DEBUG=1` in process env, inherited by the sidecar. The sidecar's `_build_atlas` and `_build_palette_sheet` skip the on-disk cache hit and re-bake every time. Every load is COLD — what a user pays on first launch with no cached fingerprints. MapForge's progress stream surfaces a `debug-bypass` phase event so you can confirm it's active.

For normal dev work, prefer `launch_current.ps1` — caches stay enabled, iteration is faster.

## Measurement scripts

All in `sidecar/tools/perf_*.py`. Each script:
- Takes the install/tileset/sector via CLI args
- Prints a single-line summary + per-phase breakdown
- Returns exit code 0 on pass (within budget), 1 on regression
- Runs against the sidecar's Python code directly — no Tauri shell + token round-trip needed

Run from `sidecar/` with the venv active:

```powershell
cd <repo>\sidecar
.venv\Scripts\python.exe tools\perf_atlas_bake.py --xml "<your install>\Data-1.13\Tilesets\Ja2Set.dat.xml" --tileset 65 --cold
.venv\Scripts\python.exe tools\perf_palette_sheet.py --xml "..." --tileset 65 --cold
.venv\Scripts\python.exe tools\perf_session_open.py --dat "<your install>\Data-1.13\Maps\C5.dat"
.venv\Scripts\python.exe tools\perf_roster.py --budget-ms  # roster + portrait-sheet budgets (auto-detects most-modded install)
.venv\Scripts\python.exe tools\perf_run_all.py  # batch every perf script with summary
```

Note: the map scripts take `--budget-ms N` (one overall total); `perf_roster.py`
takes `--budget-ms` as a flag because it reports five metrics, each gated against
its own built-in budget (see the script header). It auto-selects the most-modded
JA2 install when `--install` is omitted, and isolates `%APPDATA%` to a temp dir so
it never touches the real portrait-sheet cache.

## Before/after discipline

Before touching ANY perf-critical path:
1. Run the relevant `perf_*.py` against the affected tileset/sector. Save the output.
2. Make the change.
3. Re-run the same `perf_*.py`. Save the new output.
4. **In the change summary message**, include both: `"before X ms, after Y ms"`. If you can't show the numbers, the change didn't ship.

Bundling two perf changes in one edit is BANNED. Land each separately so regressions can be attributed.

---

# 14. Pre-demo readiness checklist

**Purpose:** The YouTube demo will record a linear flow through MercForge's features. This section is the exact path the recording will follow, with pass criteria + timing budgets per step. Run this checklist end-to-end with a stopwatch before recording.

## Demo setup (do BEFORE recording starts)

| Step | Action | Verify |
|----|----|----|
| D-Setup-1 | Confirm the test install is the active install | Settings → active install matches |
| D-Setup-2 | Atlas caches WARMED for every tileset on the demo path | Run `perf_atlas_bake.py` (no `--cold`) for tilesets 65, 0, demo's roof tileset — each <500ms |
| D-Setup-3 | Palette sheet caches warmed | `perf_palette_sheet.py` <500ms for each tileset |
| D-Setup-4 | At least one merc exists at slot 0 with a portrait | Visible in roster sidebar |
| D-Setup-5 | At least one .wmerc bundle on disk for the Import demo | Confirm path before recording |
| D-Setup-6 | At least one backup exists in the backups list | Backups page shows ≥1 entry |
| D-Setup-7 | Kill all browser, IDE, AV-scan processes that could spike disk I/O | Task Manager shows quiet system |
| D-Setup-8 | Verify build is fresh | `mercwizard.exe` mtime < 1 hour |

## Demo linear flow (the recording script)

Time budget per step is the MAX the user should wait. Steps marked **DEMO PAUSE** are intentional show-and-tell moments where wait time is fine.

| ID | Step | Action | Budget | Pass |
|----|----|----|---:|----|
| D-1 | App launch | Double-click mercwizard.exe | 5 s | Main window visible + focused, no error dialogs |
| D-2 | Hub roster | Click "Merc Wizard" tile | 2 s | 256 slots render |
| D-3 | Filter chips | Click AIM → MERC → ALL | <100ms each | Grid filter applies instantly |
| D-4 | Select slot 0 | Click slot 0 | <500 ms | Sidebar populates with name + BigFace |
| D-5 | DEMO PAUSE | Show portrait, name, type, bio | n/a | Visual demo |
| D-6 | Edit merc | Right-click slot 0 → Edit | 1.5 s | Edit route mounted with current values |
| D-7 | Change nickname | Type a new nickname | <100ms per keystroke | Input responsive |
| D-8 | Save edit | Click Save | 5 s | Streaming progress visible; ends green |
| D-9 | Return to roster | Click back to Merc Wizard | <1 s | Roster shows updated nickname |
| D-10 | Create new merc | Click empty slot → Create | 1.5 s | Create wizard mounts at step 1 |
| D-11 | Fill in basics | Name, type, profession | n/a | Form interactive |
| D-12 | Portrait drop | Drop a PNG on the dropzone | <500 ms | Preview appears; hover shows × clear button |
| D-13 | Palette chips | Pick skin/hair/eye via chip rows | <100 ms each | Selected chip highlighted |
| D-14 | Eye/mouth picker | Drag eye + mouth boxes | <50 ms drag latency | Sizes snap to vanilla presets when close |
| D-15 | Save new merc | Streaming compile → save | 12 s | Per-step events visible; ends green |
| D-16 | Back to roster | Confirm new merc appears | <1 s | New cell with portrait |
| D-17 | Move merc | Right-click → Move → pick dest | 8 s | Streaming move; source empty, dest populated |
| D-18 | Duplicate merc | Right-click → Duplicate → pick dest | 8 s | Both slots populated |
| D-19 | Delete merc | Right-click → Delete → confirm | 5 s | Slot empty |
| D-20 | Switch to MapForge | Click MapForge in nav | 2 s | Install/sector list visible |
| D-21 | Open a sector | Click a known-cached sector | **3 s** (warm) | Canvas painted, palette ready |
| D-22 | Paint a tile | Pick brush, click canvas | <100 ms | Tile updates immediately |
| D-23 | Stamp multi-tile | Pick vehicle, click | <100 ms | Footprint tiles all paint, single variant |
| D-24 | Undo | Ctrl+Z | <100 ms | Stamp reverts |
| D-25 | Run generator | Wizard → scatter → Run | 5 s | Streaming op count, ends green |
| D-26 | Undo generator | Ctrl+Z | <500 ms | Whole run reverts as one step |
| D-27 | Save sector | Click Save | 3 s | Bytes written, .bak created |
| D-28 | Open Tileset Editor | Nav → Tileset Editor | 2 s | Slot grid visible |
| D-29 | View JSD | Click slot with JSD button | <500 ms | JSD viewer modal |
| D-30 | Open Tools → STI Viewer | Nav → Tools → STI Viewer | 1 s | STI loader visible |
| D-31 | Pick an STI | Browse → select | 2 s | Frames render |
| D-32 | Open Settings | Click Settings | <500 ms | Settings page |
| D-33 | Switch install | Use install switcher | 2 s | New install active, Hub reloads |
| D-34 | View backups | Settings → Backups | 1 s | Timeline groups visible |

## Demo failure modes — abort recording if any of these happen

- Window invisible at launch (broker-only state) — fixed 2026-05-25 but watch for regression
- Cold-bake stall mid-recording — that's why D-Setup-2 + D-Setup-3 exist
- `SESSION_NOT_FOUND` during generator — fixed 2026-05-25, watch for regression
- Multi-tile stamp places two vehicles next to each other — fixed 2026-05-25
- Generator wizard hangs at step 3 mid-stream
- Save sector takes >10s — backup pipeline regression
- Any modal that doesn't dismiss on Escape

## How to use this checklist for the demo

1. Run **all of section 13 perf scripts** the day before recording. Numbers must be within budget.
2. The morning of recording: do D-Setup steps 1-8.
3. Walk D-1 through D-34 with a stopwatch. If any step blows its budget, fix or work around BEFORE recording.
4. Record.

---

# 15. Test-result template

When running this matrix, copy this skeleton into a session log:

```
# MercForge release verification: 2026-MM-DD

Environment:
  - Test install: ...
  - Build (mercwizard.exe mtime): ...
  - Build (mercwizard_core.exe mtime): ...
  - Tester: ...

Section 1 (Boot + Install): PASS / FAIL
  Failures: ...

Section 2 (Roster + Portrait): PASS / FAIL
  ...

[etc per section]

In-game session: PASS / FAIL
  AIM verify: ...
  MERC verify: ...
  Edit verify: ...
  Move verify: ...
  Duplicate verify: ...
  FaceGear verify: ...
  Sector verify: ...

Regressions hit: none / [list R-IDs]

Ship decision: YES / NO

Notes:
  ...
```

---

## INI — INI Editor (Author/Play config editing)

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| INI-1 | Route loads | Hub → INI Editor tile | Editor opens on Ja2_Options.ini, Play mode, sections sidebar populated | |
| INI-2 | Mode is session-scoped | Switch to Author, restart the app | Editor reopens in **Play** mode (Author never persists) | |
| INI-3 | Play banner names exact file | Play mode, any file | Banner shows `Profiles\UserProfile_<Mod>\<stem>.Override` path | |
| INI-4 | Cross-file search | Type `suppression` with "this file only" OFF | Results from multiple files, grouped, name-matches highlighted | |
| INI-5 | Play override round-trip | Edit a non-risk key, Apply | Row flashes green, dot turns rust, `<stem>.Override` exists in the profile dir with ONLY that key | |
| INI-6 | Remove override | Expanded row → Remove override | Value returns to canon, dot clears, override key gone from file | |
| INI-7 | Savegame-risk gating | Try editing MAX_NUMBER_PLAYER_MERCS collapsed | Editor shows "expand to edit"; expanded → destructive confirm modal | |
| INI-8 | Author first-write gate | Author mode, first apply of session | "Edit shipped canon?" confirm; second write doesn't re-ask | |
| INI-9 | Author writes canon | Author mode, edit a key | Change lands in `Data-1.13\<file>` in place, comments preserved | |
| INI-10 | AI.ini Play refusal | Select AI.ini in Play mode | Banner explains no override mechanism; editors disabled | |
| INI-11 | Game-running guard | Launch JA2, try an edit | Red banner; write rejected with GAME_RUNNING (409) | 🎮 |
| INI-12 | Engine reads the override | Set MAX_NUMBER_PLAYER_MERCS=9999 (Play), launch to menu | `iniErrorReport.log` logs the clamp line naming the key (the canary) | 🎮 |
| INI-13 | Out-of-range advisory | Type a value above engine max | Inline amber "engine will clamp to N" warning; write still allowed | |
| INI-14 | Reference diff | Set reference install in Settings, Author mode | "My changes" lists keys ≠ reference; "Reset to reference value" works | |
| INI-15 | Health chip | After a launch with INI errors | Hub shows "⚠ N INI errors last launch"; click → INI editor | 🎮 |

## GFX — Graphics stack station

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| GFX-1 | Status honest states | Settings → Graphics stack on a fresh install | Runtimes show "not installed — download"; configs "missing" | |
| GFX-2 | Deploy guarded | Fresh install (no ddraw.dll) → Deploy | Button disabled with runtime-missing tooltip | |
| GFX-3 | Customized warning | Hand-edit a golden key in ddraw.ini | Status "⚠ differs — yours is customized (N keys)"; deploy confirm lists them | |
| GFX-4 | Deploy merges | Deploy on an install with runtimes | Golden keys applied, user keys/comments preserved, backup taken | |
| GFX-5 | Golden verified in-game | Deploy, launch | xBRZ/ReShade look active; `ja2_remastered.ini` preset loaded | 🎮 |

## GS — Game status

| ID | Test | Steps | Expected | 🎮 |
|----|------|-------|---------|----|
| GS-1 | Running detection | Launch JA2 from Hub | Button flips to "🟢 JA2 is running" within ~5s, disabled | 🎮 |
| GS-2 | Recovery | Close JA2 | Button returns to "Launch JA2" within ~5s | 🎮 |

# Maintenance

When a new feature ships:
1. Add a row group to the relevant section (or create a new section)
2. Add specific 🎮 in-game rows if the feature changes anything the
   engine reads
3. Add a Regression Watch (Section 11) row if you fixed a bug that
   could plausibly come back
4. Bump the "Last updated" date at the top
