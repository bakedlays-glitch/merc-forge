# Soldier Body-Sprite Rendering — Feasibility Research

**Goal:** a sidecar endpoint that, given `(ubBodyType, bDirection)`, returns a PNG of that
body type's STANDING sprite facing that direction, for the MapForge NPC-placement overlay.

**Verdict: FEASIBLE and PROVEN.** Five real standing sprites (REGMALE, BIGMALE, REGFEMALE,
ADULTFEMALEMONSTER, and the Wasteland DOG creature) were decoded from the live Copy install
using the *existing* sidecar SLF + STI helpers — no new decoder needed. They render as
recognizable, correctly-coloured JA2 soldier silhouettes from the STIs' own embedded palette.

Source citations are from the **custom recompiled engine** tree at
`C:\AI Projects\The Wasteland\Visual Studio Root\` (the only tree that defines the Wasteland
creatures; stock `Source Files\1.13 Source\source-master\` stops at `COMBAT_JEEP`; the Copy
*install* has no source tree).

---

## 1. Bodytype → STI-filename table (state-constant intermediary, source-cited)

Two engine lookups chain together:

1. `gubAnimSurfaceIndex[ubBodyType][STANDING]` → a **surface constant**
   (`Tactical\Animation Control.cpp`, table built ~line 1228+; `STANDING` = enum value 1 in
   `AnimationStates`, `Tactical\Animation Control.h:142`).
2. `gAnimSurfaceDatabase[surfaceConstant].Filename` → the **STI path**
   (`Tactical\Animation Data.cpp:34+`, struct `AnimationSurfaceType` at `Animation Data.h:701-714`).

| ubBodyType (enum int) | STANDING surface constant | STI filename (internal path) | Source (`Visual Studio Root\`) |
|---|---|---|---|
| REGMALE (0) | `RGMSTANDING` | `ANIMS\S_MERC\S_R_STD.STI` | AnimCtrl.cpp:1270 / AnimData.cpp:37 |
| BIGMALE (1) | `BGMSTANDING` | `ANIMS\M_MERC\M_R_STD.STI` | AnimCtrl.cpp:1727 / AnimData.cpp:130 |
| STOCKYMALE (2) | `RGMSTANDING` (shares regular male) | `ANIMS\S_MERC\S_R_STD.STI` | AnimCtrl.cpp:2202 |
| REGFEMALE (3) | `RGFSTANDING` | `ANIMS\F_MERC\F_BRETH2.STI` | AnimCtrl.cpp:2657 / AnimData.cpp:225 |
| ADULTFEMALEMONSTER (4) | `AFMONSTERSTANDING` | `ANIMS\MONSTERS\MN_BREAT.STI` | AnimCtrl.cpp:3103 / AnimData.cpp:316 |
| FATCIV (11) | `FATMANSTANDING` | `ANIMS\CIVS\FT_BRTH.STI` | AnimCtrl.cpp:3257 / AnimData.cpp:348 |
| MANCIV (12) | `MANCIVSTANDING` | `ANIMS\CIVS\M_BREATH.STI` | AnimData.cpp:358 |
| BLOODCAT (20) | `CATBREATH` | `ANIMS\ANIMALS\CT_BREATH.STI` | AnimCtrl.cpp:3600 / AnimData.cpp:430 |
| DOG (29, Wasteland) | `DOGBREATH` | `ANIMS\ANIMALS\DOG_BREATH.STI` | AnimCtrl.cpp:3614 / AnimData.cpp:603 |
| GORISCLAW (30, Wasteland) | `GORISBREATH` | `ANIMS\ANIMALS\GORIS_BREATH.STI` | AnimCtrl.cpp:3628 / AnimData.cpp:613 |
| GRUTHARCLAW (31, Wasteland) | `GRUTHARBREATH` | `ANIMS\ANIMALS\GRUTHAR_BREATH.STI` | AnimCtrl.cpp:3642 / AnimData.cpp:623 |
| MOMCLAW (32, Wasteland) | `MOMBREATH` | `ANIMS\ANIMALS\MOM_BREATH.STI` | AnimCtrl.cpp:3655 / AnimData.cpp:632 |

**Full `ubBodyType` enum** (`Tactical\Animation Data.h:36-78`, positional — only `REGMALE=0`
is explicit, each subsequent member +1): `REGMALE 0, BIGMALE 1, STOCKYMALE 2, REGFEMALE 3,
ADULTFEMALEMONSTER 4, AM_MONSTER 5, YAF_MONSTER 6, YAM_MONSTER 7, LARVAE_MONSTER 8,
INFANT_MONSTER 9, QUEENMONSTER 10, FATCIV 11, MANCIV 12, MINICIV 13, DRESSCIV 14, HATKIDCIV 15,
KIDCIV 16, CRIPPLECIV 17, COW 18, CROW 19, BLOODCAT 20, ROBOTNOWEAPON 21, HUMVEE 22, TANK_NW 23,
TANK_NE 24, ELDORADO 25, ICECREAMTRUCK 26, JEEP 27, COMBAT_JEEP 28, DOG 29, GORISCLAW 30,
GRUTHARCLAW 31, MOMCLAW 32, TOTALBODYTYPES 33`.

**Wasteland creatures CONFIRMED** in the recompiled engine (DOG=29 / GORISCLAW=30 /
GRUTHARCLAW=31 / MOMCLAW=32) — `Animation Data.h:71-74`, matching CLAUDE.md. They are absent
from the stock tree.

---

## 2. Direction → frame (sub-image) rule  ⚠️ NOT `frame == direction`

The standing STIs are **not** 8 sub-images = 8 directions. They are an idle/breathing loop:
**N directions × M frames-per-direction, grouped contiguously by direction.** Measured:
`S_R_STD.STI` = **64 sub-images** = 8 dir × 8 frames/dir; `MN_BREAT.STI` = 96 = 8 × 12;
`DOG_BREATH.STI` = 64 = 8 × 8. (The trailing `8` in the `gAnimSurfaceDatabase` row is
`uiNumDirections`, *not* a frame count; `uiNumFramesPerDir` is `TO_INIT`=0 in source and
filled at load from the STI aux data — `Animation Data.cpp:1080`.)

Engine sub-image formula (`Tactical\Soldier Control.cpp:8090-8162`, `ConvertAniCodeToAniFrame`):

```
subImageIndex = animPhaseFrame + (uiNumFramesPerDir * spriteDir)         // line 8145
spriteDir     = gOneCDirection[ ubDirection ]                            // line 8102
```

`gOneCDirection` (`TileEngine\Isometric Utils.cpp:70-80`) is a **one-step-clockwise remap**,
NOT identity: world dir d → sprite dir `(d + 1) mod 8`.

**Practical rule for a static standing frame** (use `animPhaseFrame = 0`):

```
framesPerDir = totalSubImages // 8          # 8 world directions, always
spriteDir    = (bDirection + 1) % 8         # gOneCDirection remap
subImageIndex = framesPerDir * spriteDir    # first/rest frame of that direction
```

So for `S_R_STD` (8 frames/dir): world-NORTH(0)→sub-image 8, world-EAST(2)→24,
world-SOUTH(4)→40, world-NORTHWEST(7)→0. Picking `framesPerDir * spriteDir` gives a clean
upright pose for any direction. (Empirically the per-direction blocks are visually
distinguishable — frame sizes step every 8 sub-images, confirming the contiguous grouping.)

If you'd rather ignore the clockwise remap and treat the placement's stored `bDirection`
as already the sprite direction, the sprite will face one octant off — match the engine and
apply `(d+1) % 8`.

---

## 3. Asset-resolution recipe (which SLF, path format, which existing helpers)

**SLF:** `…\Mod Prototype - Copy\Data\Anims.slf` (59 MB, 645 entries). It is the *only*
`Anims*.slf` in the install. **But it does NOT contain the Wasteland creature breath STIs.**

**VFS shadowing — must check loose files first.** Loose files under `Data\Anims\…` exist and
the engine VFS reads them ahead of / alongside the SLF. The creature STIs ship **only loose**:
`Data\Anims\Animals\{DOG,GORIS,GRUTHAR,MOM}_BREATH.STI` (verified present; also `_bak_*`
history dirs — ignore those). The merc/monster STIs live in the SLF. So resolution order is:

1. Loose: `<install_root>\Data\Anims\<rest of path>` (case-insensitive; dirs are `Anims\Animals`,
   `Anims\S_MERC`, etc.). Also probe `<install_root>\Data-1.13\Anims\…` for completeness
   (empty in this install, but it's the higher-priority VFS layer when populated).
2. SLF: open `Data\Anims.slf`, internal path `/<SUBDIR>/<NAME>.STI` (forward-slash
   normalized; e.g. `/S_MERC/S_R_STD.STI`). `SlfFS` normalizes the source's backslashes.

**Existing helpers to call (module + function):**

- **SLF read** — `ja2py.fileformats.SlfFS.SlfFS`:
  `slf = SlfFS(str(anims_slf_path))`; `slf.isfile("/S_MERC/S_R_STD.STI")`;
  `data = slf.openbin("/S_MERC/S_R_STD.STI", "r").read()` → raw STI bytes.
  Reuse the cached opener pattern in `mercwizard_core/install_context.py:_open_slf_cached`
  (keyed by `(path, mtime_ns)`, FIFO-evicting, lock-guarded) to avoid re-parsing the 59 MB
  header on every overlay cell.
- **STI decode** — `mercwizard_core.sti_decode.decode_sti_frame_to_png(source, frame_index)`:
  accepts **raw bytes OR a filesystem Path** (so loose files and SLF bytes share one path),
  returns PNG bytes. It palette-resolves frame-by-frame against the container palette and emits
  index-0 as fully transparent. For a loose creature file just pass the `Path`.
  (`decode_subimage_to_rgba` is the lower-level PIL-image variant if you need to composite.)

No new decoder, no new SLF reader. The whole endpoint is: resolve path → get bytes/Path →
`decode_sti_frame_to_png(src, framesPerDir * ((dir+1)%8))`.

---

## 4. Palette finding

**Embedded palette is sufficient — no `.col` needed.** Every standing STI is 8-bit with a
768-byte (256×RGB) embedded palette (REGMALE: 297 non-zero channel bytes; BIGMALE: 190).
`decode_sti_frame_to_png` resolves through `images.palette.palette` and produces correctly
coloured, recognizable sprites (purple shirt / green trousers merc, brown bug monster, brown
dog). Index 0 → transparent gives a clean cut-out silhouette suitable for overlay compositing.

Caveat (cosmetic only): these are the *generic* base-palette sprites. In-game the engine
re-tints skin/vest/pants per-merc via palette replacement (`.col` files exist under
`Data\Anims\…`), but the overlay does **not** need that — a recognizable body silhouette is
the goal, and the embedded palette delivers it. The green blob at the feet is the engine's
isometric drop-shadow baked into the STI (low palette indices); harmless for an overlay, and
could be masked later if undesirable.

---

## 5. PROOF — what was decoded

Venv: `…\MercWizard2\sidecar\.venv\Scripts\python.exe`. Helpers imported as in §3.
PNGs written to `…\MercWizard2\.superpowers\sdd\sprite-probe\` (gitignored scratch):

| Body type | STI source | sub-images | frame0 size | opaque cov. | PNG | Visual |
|---|---|---|---|---|---|---|
| REGMALE | SLF `/S_MERC/S_R_STD.STI` | 64 (8×8) | 24×48 | 49% | `REGMALE_S_R_STD_frame{0,2}_v101.png`, `REGMALE_dir0..7_v101.png` | standing merc, purple top / green legs, weapon, iso shadow |
| BIGMALE | SLF `/M_MERC/M_R_STD.STI` | 64 (8×8) | 39×62 | 37% | `BIGMALE_M_R_STD_frame{0,2}_v101.png` | larger standing humanoid, same style |
| REGFEMALE | SLF `/F_MERC/F_BRETH2.STI` | 64 (8×8) | 26×46 | 42% | `REGFEMALE_F_BRETH2_frame0_v101.png` | smaller standing figure, blue top |
| ADULTFEMALEMONSTER | SLF `/MONSTERS/MN_BREAT.STI` | 96 (8×12) | 41×75 | 65% | `MONSTER_MN_BREAT_frame0_v101.png` | brown bug-creature silhouette |
| DOG (Wasteland) | loose `Data\Anims\Animals\DOG_BREATH.STI` | 64 (8×8) | 17×57 | 63% | `DOG_BREATH_dir0_v101.png` | brown quadruped silhouette |

All five are non-empty, plausibly-shaped, correctly-coloured silhouettes (verified by eye).
The 8 `REGMALE_dir0..7` PNGs were rendered at `subImage = 8 * dir` to confirm per-direction
poses decode cleanly across all directions. Embedded palette only — no `.col` applied.

---

## 6. Confidence + recommended endpoint design

**Confidence: HIGH** for the 4 merc body types + monsters + the 4 Wasteland creatures
(every STI in the table above was either decoded or its source row verified verbatim).

Recommended endpoint:

```
GET /mapforge/soldier-sprite?bodytype=<int>&dir=<0-7>[&team=<int>]  →  image/png
```

- **Mapping table** — hardcode a `BODYTYPE_STANDING_STI: dict[int, str]` in the sidecar mirroring
  §1 (it's a tiny, stable, source-derived table; no need to parse `Animation Data.cpp` at runtime).
  Civilian/vehicle rows can be filled in from the same `gAnimSurfaceDatabase` table as needed.
- **Resolution** — loose-first then SLF per §3; reuse `_open_slf_cached` and
  `decode_sti_frame_to_png`.
- **Frame pick** — `framesPerDir = totalSubImages // 8; sub = framesPerDir * ((dir+1) % 8)`.
  Decode once to learn `totalSubImages` (or cache it per STI).
- **Cache key** — `(install_id, bodytype, dir)`; underlying STI invalidation by SLF/loose-file
  `mtime_ns` (same discipline as `_open_slf_cached` and the roster portrait cache). The decoded
  PNG per `(bodytype, dir)` is tiny (<3 KB) and there are only ~33 bodytypes × 8 dirs = 264
  possible images — the whole set can be warm-baked at install-select time if desired.
- **`team`/`bDirection` are already parsed** by the overlay; `team` is optional here (would only
  matter for a future palette-tint, which is out of scope).
- **Fallback when a bodytype has no mapping** — return the REGMALE standing sprite (or a 1×1
  transparent PNG / HTTP 204) so the overlay degrades to "generic humanoid" rather than 500.
  Vehicles/COW/CROW/robot and the rarer civ/monster sub-types can map to a placeholder until
  their `gAnimSurfaceDatabase` rows are transcribed.

---

## 7. Honest gaps

- **Direction remap is the one trap.** It is `(d+1) % 8` (`gOneCDirection`), not identity. If
  the overlay's stored `bDirection` already encodes a sprite-facing convention, double-check
  against one in-game screenshot before trusting the remap — verified from source, not against
  a live render.
- **Civilian / vehicle / extra-monster rows** (MINICIV, DRESSCIV, kids, COW, CROW,
  ROBOTNOWEAPON, HUMVEE/TANK/JEEP/ELDORADO/ICECREAMTRUCK, AM/YAF/YAM/LARVAE/INFANT/QUEEN
  monsters) were **not individually decoded** — only FATCIV/MANCIV/BLOODCAT rows were read from
  source and one monster (MN_BREAT) decoded. Their STI paths are in the same
  `gAnimSurfaceDatabase` table; transcribe and spot-check before relying on them. Vehicles
  render via a different (vehicle-sprite) path in-engine and may not have a clean standing STI —
  treat as placeholder-only initially.
- **GORISCLAW / GRUTHARCLAW / MOMCLAW** creature STIs exist as loose files
  (`Data\Anims\Animals\{GORIS,GRUTHAR,MOM}_BREATH.STI`, confirmed present) but were **not
  decoded in this probe** (only DOG was, to prove the loose-creature path). Very likely fine —
  same format/source as DOG — but verify on first use.
- **Per-merc palette tinting** is intentionally out of scope; the generic embedded palette is
  used. Acceptable per the brief.
