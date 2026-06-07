# INI Editor — Engine Facts

Ground-truth reference for the MercForge INI editor (Phase 1, 2026-06-07).
Every claim cites `file:line` into `Visual Studio Root\` (The Wasteland's
custom engine source) or the live install
(`C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy\`).
**This document gates write strategy in `mercwizard_core/ini_editor.py` —
do not change write behavior without updating this doc, and vice versa.**

Provenance: engine-navigator research pass + manual verification of the
Override-file semantics (the one detail the research pass got wrong —
see §3). Supersedes all assumptions inherited from the frozen
`ja2-launcher/` (whose `Data-User/` model is engine-fiction; the engine
never mounts such a layer).

---

## 1. Core mechanism

- **Merge is opt-in per file.** `CIniReader::RegisterFileForMerging`
  populates a static `m_merge_files` set (`Utils/INIReader.cpp:33-36`).
  Registered files are loaded from **every** VFS profile that contains
  them, lowest-priority first, later layers overwriting earlier keys —
  per-key merge, top profile wins (`INIReader.cpp:46-61`). Unregistered
  files load as a single VFS-resolved file (`INIReader.cpp:42-45`) —
  the topmost layer containing the file shadows it **whole-file**.
- **Registration comes from root `JA2.ini`:** `[Ja2 Settings]
  MERGE_INI_FILES` (and `MERGE_INI_FILES_UB` for UB campaigns) read at
  `sgp/sgp.cpp:987-1002`. Live install: `MERGE_INI_FILES =
  Ja2_Options.ini` (`JA2.ini:60`), no `_UB` line. **Ja2_Options.ini is
  the only merge-registered file.**
- **The engine's write layer** is the vfs_config profile with
  `WRITE = true` — `[PROFILE_UserProf]`, `PROFILE_ROOT =
  Profiles\UserProfile_JA2113` in `vfs_config.JA2113.ini:19-23`. The
  profile root dir is per-campaign (3 distinct `UserProfile_*` dirs in
  the live install). There is **no `Data-User` layer** in any config or
  anywhere in engine source.
- **Out-of-range numeric = clamp + log, non-fatal.**
  `ReadInteger(..., min, max)` clamps and pushes a line into
  `iniErrorReport.log` (`INIReader.cpp:114-130`; same for
  UINT/Float/Double `:145-183`, `:307-357`). `ReadBoolean` falls back to
  the default and logs on non-TRUE/FALSE (`:259-281`). The 3-arg
  `ReadInteger` overload has **no clamp** (`:108-111`). This is the
  basis for the acceptance canary: a deliberately out-of-range value
  must produce a deterministic log line naming `[section][key]`.
- **Names are case-insensitive** (sections, keys, filenames) — uppercase
  compare in `vfs_string` / `vfs::StrCmp::Equal`. `Ja2_Options.INI` ==
  `Ja2_Options.ini` to the engine; the editor must locate and preserve
  the existing on-disk casing rather than create same-name-different-case
  siblings.

## 2. Per-file matrix

Loaders in `Ja2/GameSettings.cpp` run at startup (`gameloop.cpp:120-188`)
**and again on every new-game start** via `InitDependingGameStyleOptions`
(`MainMenuScreen.cpp:348,356-381` — called from New Game, Features
screen, Options screen, MP host/connect). See §5 for re-read rules.

| File | Loader | CIniReader ctor | Read when | **Write strategy (the gate)** |
|---|---|---|---|---|
| `Ja2.ini` (root) | raw PropertyContainer `sgp/sgp.cpp:943` | — (not CIniReader) | boot, very early | **ROOT-DIRECT** — section-aware in-place edit of the root file. No merge, no Override. |
| `Ja2_Options.ini` | `LoadGameExternalOptions` GameSettings.cpp:950 | 1-arg, **merge-registered** | startup + every new game | **`.Override` PER-KEY** (uniform with the rest — see note below the table). A partial merge-copy in the profile root would also work; not used. |
| `APBPConstants.ini` | `LoadGameAPBPConstants` :3429 | 1-arg | startup + new game | **`.Override` PER-KEY** (§3) |
| `AI.ini` | `PlanFactoryLibrary` ctor `ModularizedTacticalAI/src/PlanFactoryLibrary.cpp:44` | **2-arg** (no Override) | lazy, first AI plan, once/process | **AUTHOR-MODE ONLY** — no per-key override exists; partial edits dangerous (`NumFactories` must match a consecutive `Factory_0..N-1` block or `create_plan` throws `logic_error`, :80). |
| `CTHConstants.ini` | `LoadCTHConstants` :3671 | 1-arg | startup + new game | **`.Override` PER-KEY** |
| `Creatures_Settings.INI` | `LoadCreaturesSettings` :4072 | 1-arg | startup + new game | **`.Override` PER-KEY** |
| `Helicopter_Settings.INI` | `LoadHelicopterRepairRefuelSettings` :3879 | 1-arg | startup + new game | **`.Override` PER-KEY** |
| `IntroVideos.ini` | `Ja2/Intro.cpp:283` | 1-arg | lazy, intro playback | **`.Override` PER-KEY** |
| `Item_Settings.ini` | `LoadItemSettings` :3175 | 1-arg | **startup only** (not in the new-game re-read block) | **`.Override` PER-KEY** (requires relaunch) |
| `Mod_Settings.ini` | `LoadModSettings` :2924 | 1-arg | **startup only** | **`.Override` PER-KEY** (requires relaunch) |
| `Morale_Settings.INI` | `LoadMoraleSettings` :3914 | 1-arg | startup + new game | **`.Override` PER-KEY** |
| `RebelCommand_Settings.ini` | `LoadRebelCommandSettings` :4081 | 1-arg | startup + new game | **`.Override` PER-KEY** |
| `Reputation_Settings.INI` | `LoadReputationSettings` :4048 | 1-arg | startup + new game | **`.Override` PER-KEY** |
| `Skills_Settings.INI` | `LoadSkillTraitsExternalSettings` :2597 | 1-arg | startup + new game | **`.Override` PER-KEY** |
| `Taunts_Settings.INI` | `LoadTauntsSettings` :3818 | 1-arg | startup + new game | **`.Override` PER-KEY** |

**Author mode** (editing The Wasteland's shipped canon) targets the
layer the engine actually resolves for the base file — practically
`Data-1.13\` (the v113 profile) — via `VfsLayout.resolve_write`
(in-place edit semantics). Author mode applies to all 15 files.

**Play mode** (per-campaign, never touches canon) uses column 5:
**one uniform mechanism — `<stem>.Override` in the engine write
profile's root — for every 1-arg CIniReader file including
Ja2_Options.ini** (the Override hook runs unconditionally after both
the merge and non-merge load paths, so it works identically for both;
implemented in `ini_editor.IniEditor.write_target`). **No Play mode for
AI.ini**; root-direct for Ja2.ini (install-global, not per-campaign —
surface that in UI). The uniform choice trades the battle-tested
MERGE_INI_FILES path for one mechanism + one test surface; the Step-6
canary proves `.Override` on BOTH a merge-registered file
(Ja2_Options) and a plain file (Skills_Settings) before anything
ships.

## 3. The `.Override` mechanism (manually verified — corrects the research pass)

`INIReader.cpp:62-69`, executed unconditionally at the end of the
**1-arg** ctor (registered or not):

```cpp
_splitpath(szFileName, Drive, Dir, Name, Ext);
_makepath(OvrFileName, Drive, Dir, Name, "Override");
if(getVFS()->fileExists(OvrFileName))
    m_oProps.initFromIniFile(vfs::Path(OvrFileName));
```

- `_makepath` **replaces the extension**: `Ja2_Options.ini` →
  **`Ja2_Options.Override`** (NOT `Ja2_OptionsOverride.ini` — the
  research pass misread this; MSVC `_makepath` inserts the dot and
  swaps the ext).
- `initFromIniFile` **accumulates** into the already-loaded container →
  per-key overlay; keys absent from the Override file survive from the
  base. Applied AFTER the merge step, so it also tops Ja2_Options.ini's
  merged result.
- The Override file is resolved **through the normal VFS read chain**
  (any layer; topmost wins). Writing it into the active campaign's
  profile root keeps all Play-mode artifacts in one per-campaign dir.
- The **2-arg ctor has no Override block** (`INIReader.cpp:72-99`) →
  AI.ini, `Ja2_Settings.INI`, `Ja2_Features.ini` never see Overrides.
- The live install has **zero `*.Override` files** — mechanism dormant,
  no collision risk. Untested in the wild: the Step-6 acceptance canary
  must prove a `.Override` file loads (out-of-range canary in e.g.
  `Skills_Settings.Override`) before Play mode ships for non-merge
  files.

## 4. Engine-authored files — HANDS-OFF list

The engine rewrites these **wholesale**; an external editor must never
present them as editable (changes are lost on the next engine write):

| File (in profile root) | Writer | Trigger |
|---|---|---|
| `Ja2_Settings.INI` | `SaveGameSettings` GameSettings.cpp:513 | options apply, help screen, save-game, game exit (10 callsites) |
| `Ja2_Features.ini` | `SaveFeatureFlags` GameSettings.cpp:662 | features screen, save-game, exit (8 callsites) |
| `ja2_sp.ini` | GameInitOptionsScreen.cpp (:535, :2902) | SP game-setup screen |
| `ja2_mp.ini` | MPHostScreen/MPJoinScreen/server/client | MP screens |
| `vfs.log`, `iniErrorReport.log`, `Temp/`, `ShadeTables/`, `SavedGames/` | engine runtime | every launch |

The engine's own file header says "this file is automatically generated
by the game" (GameSettings.cpp:528,672).

Editor-owned files in the profile root are exactly: the partial
`Ja2_Options.ini` and any `*.Override` files we create. Nothing else.

## 5. When edits take effect

- Most GameSettings INIs re-read on **every new-game start from the
  main menu** (`InitDependingGameStyleOptions`), not just process
  launch. Exceptions: `Item_Settings.ini` + `Mod_Settings.ini`
  (startup only → relaunch required), `AI.ini` (first-AI-use, once per
  process), `IntroVideos.ini` (intro playback).
- **Loading a savegame does NOT re-read these INIs** — the save-load
  path only *writes* Ja2_Settings/Ja2_Features
  (`SaveLoadGame.cpp:4452-4453,6461-6462`). Mid-campaign edits take
  effect on next new game / relaunch, not next load.
- Therefore: a **game-running guard is mandatory** for writes (the
  engine flushes its HANDS-OFF files on options-apply/save/exit and
  could interleave with ours), and the UI must label when each file's
  changes land ("next launch" vs "next new game").
- Save-compat: `MAX_NUMBER_PLAYER_MERCS` / vehicle limits carry the
  engine's own "can render savegames unloadable" warning
  (GameSettings.cpp:954) — flag System Limit keys as
  save-compat-sensitive.

## 6. Engine-true schema metadata (mining verdict)

`engine.db`'s `callsites` table stores no argument literals → **not
DB-minable**. But the loader calls are uniform
(`iniReader.ReadInteger("SECTION","KEY", default, min, max)`,
GameSettings.cpp ≈ 1384 ReadInteger + 475 ReadBoolean + 339 ReadFloat
callsites) → **FEASIBLE-VIA-REGEX** over `GameSettings.cpp` (+
`Intro.cpp`, `PlanFactoryLibrary.cpp`), whitespace/multiline-tolerant,
with named min/max constants (e.g.
`CODE_MAXIMUM_NUMBER_OF_PLAYER_MERCS`) resolved through engine.db's
`constants` table (~90-95% hit rate; unresolved residue stays
`scraped`-confidence). This powers `confidence: engine` metadata in the
schema pipeline — authoritative defaults/ranges straight from the
loader, beating comment-scraping.

Verified samples (GameSettings.cpp:955-967):
`MAX_NUMBER_PLAYER_MERCS` default 24, min 1, max 254;
`MAX_NUMBER_ENEMIES_IN_TACTICAL` 32/16/256;
`MAX_STRATEGIC_ENEMY_GROUP_SIZE` 20/10/100.

## 7. Implications baked into `ini_editor.py`

1. Play-mode write targets come **only** from this doc's matrix —
   refuse anything not listed (no partial profile copies of
   unregistered files: whole-file shadowing would silently reset every
   unlisted key).
2. Effective-value resolution must model: full profile stack (per-key
   for Ja2_Options, whole-file-resolve for the rest) → `.Override`
   overlay → engine clamp range (when engine-mined metadata exists) →
   schema default. Provenance names the actual winning layer.
3. All writes: game-running guard → cross-process install lock →
   backup snapshot → comment-preserving surgery → re-parse self-check
   → auto-rollback on mismatch.
4. Per-campaign awareness: the profile root is a function of the
   active `VFS_CONFIG_INI`; surface `writable_profile` + campaign in
   every API response; `compute_vfs_mismatch` check before writes.
5. Ja2.ini edits are install-global (affect all campaigns) — UI labels
   them as such.
