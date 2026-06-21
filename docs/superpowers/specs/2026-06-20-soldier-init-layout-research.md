# JA2 1.13 Map Soldier-Init Section — Read-Only Parse Spec

**Goal:** Walk the soldier-init (NPC/enemy/civilian placement) section of a `.DAT`
map appendix byte-exactly, extracting each placement's position + a type label,
and advance the cursor exactly past the section.

**Status: HIGH confidence for the basic-placement (modern + vanilla) path —
empirically validated on 4 stock maps (A6, A12, A15, C7) byte-exactly to the
following exit-grid section. MEDIUM on the detailed-placement (`fDetailedPlacement==1`)
sub-case: layout/rule derived from source but not empirically validated (no stock
no-light vanilla map carries a detailed placement to test against).**

---

## 1. Where the section sits in the appendix

Save order (`TileEngine/worlddef.cpp:2255-2296`), each gated by a flag bit:

| Order | Section | Flag |
|---|---|---|
| 1 | World items | `MAP_WORLDITEMS_SAVED` 0x08 |
| 2 | Ambient light (fixed 3 bytes) | `MAP_AMBIENTLIGHTLEVEL_SAVED` 0x80 |
| 3 | World lights | `MAP_WORLDLIGHTS_SAVED` 0x04 |
| 4 | **MapInfo tail** (unconditional) | — |
| 5 | **SOLDIERS** ← this spec | `MAP_FULLSOLDIER_SAVED` 0x01 |
| 6 | Exit grids | `MAP_EXITGRIDS_SAVED` 0x10 |
| 7 | Door table | `MAP_DOORTABLE_SAVED` 0x20 |
| 8 | Edgepoints | `MAP_EDGEPOINTS_SAVED` 0x40 |
| 9 | NPC schedules | `MAP_NPCSCHEDULES_SAVED` 0x100 |

The soldier section is written by `SaveSoldiersToMap` (`worlddef.cpp:2277` →
`Tactical/Soldier Init List.cpp:350`) and read by `LoadSoldiersFromMap`
(`worlddef.cpp:3230` → `Soldier Init List.cpp:282`).

### CRITICAL prerequisite — MapInfo tail size is 100, not 99

`SaveMapInformation` (`Map Information.cpp:184`) writes
`sizeof(_OLD_MAPCREATE_STRUCT)` for the vanilla version (major 5.0 / minor 25),
else `sizeof(MAPCREATE_STRUCT)`.

`_OLD_MAPCREATE_STRUCT` (`Map Information.h:18-38`): 4×INT16 + 4×UINT8 + 2×INT16 +
`INT8 bPadding[83]` = **99 raw field bytes**. The source comment says "//99 bytes",
but with MSVC default packing the largest member is INT16 → struct align = 2 →
`sizeof` **rounds 99 up to 100**. The number of placements is this struct's
`ubNumIndividuals` (UINT8 at field offset 8 in the old struct).

> The existing `appendix_extract.py:80` uses `tail_size = 99 if major < 7.0`. That
> is **off by one** — it must be **100**. Empirically: with 99 the soldier records
> start at byte 314114 on A6 but parse as garbage; with **100** they start at
> **314115** and parse perfectly. (Modern tail `sizeof(MAPCREATE_STRUCT)` = 32 for
> major ≥ 7.0 is unaffected and correct.)

No count or marker byte is written around the soldier records themselves — the
count comes entirely from the MapInfo tail's `ubNumIndividuals`.

---

## 2. Per-record stride (the basic struct)

`SaveSoldiersToMap` loops `ubNumIndividuals` times; each iteration writes
`pBasicPlacement->Save(...)` and, **iff** `pBasicPlacement->fDetailedPlacement`,
also `pDetailedPlacement->Save(...)` (`Soldier Init List.cpp:362-376`).

### 2a. Basic struct — version split

`BASIC_SOLDIERCREATE_STRUCT::Save` (`Soldier Init List.cpp:248-280`):
- **Vanilla** (`dMajorMapVersion == 5.00 && ubMinorMapVersion == 25`,
  `worlddef.h:49-50`) → writes `_OLD_BASIC_SOLDIERCREATE_STRUCT`.
- **Else** → writes the modern `BASIC_SOLDIERCREATE_STRUCT`.

`BASIC_SOLDIERCREATE_STRUCT::Load` (`Soldier Init List.cpp:235-246`):
- `dMajorMapVersion < 7.0` → reads `_OLD_BASIC_SOLDIERCREATE_STRUCT`.
- `>= 7.0` → reads modern `BASIC_SOLDIERCREATE_STRUCT`.

(So maps saved at major 6.0 are read with the old struct; the modern struct is
only major ≥ 7.0.)

### 2b. `_OLD_BASIC_SOLDIERCREATE_STRUCT` = **52 bytes** (vanilla / major < 7.0)

Declaration `Soldier Create.h:91-110` (comment "//50 bytes" is WRONG — real
`sizeof` is 52). MSVC default packing, largest member INT16 → struct align = 2:

| rec off | C field | type | bytes |
|---|---|---|---|
| 0 | `fDetailedPlacement` | BOOLEAN | 1 |
| 1 | *(pad to INT16)* | — | 1 |
| **2** | **`sStartingGridNo`** | **INT16** | **2** |
| 4 | `bTeam` | INT8 | 1 |
| 5 | `bRelativeAttributeLevel` | INT8 | 1 |
| 6 | `bRelativeEquipmentLevel` | INT8 | 1 |
| 7 | `ubDirection` | UINT8 | 1 |
| 8 | `bOrders` | INT8 | 1 |
| 9 | `bAttitude` | INT8 | 1 |
| 10 | `ubBodyType` | INT8 | 1 |
| 11 | *(pad to INT16)* | — | 1 |
| 12 | `sPatrolGrid[10]` | INT16×10 | 20 |
| 32 | `bPatrolCnt` | INT8 | 1 |
| 33 | `fOnRoof` | BOOLEAN | 1 |
| 34 | `ubSoldierClass` | UINT8 | 1 |
| 35 | `ubCivilianGroup` | UINT8 | 1 |
| 36 | `fPriorityExistance` | BOOLEAN | 1 |
| 37 | `fHasKeys` | BOOLEAN | 1 |
| 38 | `PADDINGSLOTS[14]` | INT8×14 | 14 |
| **52** | *(end — already even)* | | |

**Stride (vanilla / major<7.0, no detailed) = 52 bytes.**
`OLD_MAXPATROLGRIDS = MAXPATROLGRIDS = 10` (`Soldier Control.h:6-7`).

### 2c. Modern `BASIC_SOLDIERCREATE_STRUCT` = **64 bytes** (major ≥ 7.0)

Declaration `Soldier Create.h:112-135`. `usStartingGridNo` is **INT32**,
`sPatrolGrid[10]` is **INT32×10** → largest member INT32 → struct align = 4:

| rec off | field | type | bytes |
|---|---|---|---|
| 0 | `fDetailedPlacement` | BOOLEAN | 1 |
| 1-3 | *(pad to INT32)* | — | 3 |
| **4** | **`usStartingGridNo`** | **INT32** | **4** |
| 8 | `bTeam` | INT8 | 1 |
| 9 | `bRelativeAttributeLevel` | INT8 | 1 |
| 10 | `bRelativeEquipmentLevel` | INT8 | 1 |
| 11 | `ubDirection` | UINT8 | 1 |
| 12 | `bOrders` | INT8 | 1 |
| 13 | `bAttitude` | INT8 | 1 |
| 14 | `ubBodyType` | INT8 | 1 |
| 15 | *(pad to INT32)* | — | 1 |
| 16 | `sPatrolGrid[10]` | INT32×10 | 40 |
| 56 | `bPatrolCnt` | INT8 | 1 |
| 57 | `fOnRoof` | BOOLEAN | 1 |
| 58 | `ubSoldierClass` | UINT8 | 1 |
| 59 | `ubCivilianGroup` | UINT8 | 1 |
| 60 | `fPriorityExistance` | BOOLEAN | 1 |
| 61 | `fHasKeys` | BOOLEAN | 1 |
| 62-63 | *(pad struct to align-4)* | — | 2 |
| **64** | *(end)* | | |

**Stride (modern, no detailed) = 64 bytes.** (Derived, not empirically validated —
no major≥7.0 stock map in the Copy carries `MAP_FULLSOLDIER_SAVED`; A9 etc. have
flag 0x10 only = 0 soldiers.)

### 2d. Detailed `SOLDIERCREATE_STRUCT` (only when `fDetailedPlacement == 1`)

`SOLDIERCREATE_STRUCT::Load(INT8**, major, minor)` at `Ja2/SaveLoadGame.cpp:1078`:
- `major >= 6.0 && minor > 26`:
  - `major < 7.0` → read `_OLD_SIZEOF_SOLDIERCREATE_STRUCT_POD` bytes
    (`offsetof(_OLD_SOLDIERCREATE_STRUCT, endOfPOD)`), **then** `Inv.Load(...)`
    (variable-size OO inventory).
  - `>= 7.0` → read `SIZEOF_SOLDIERCREATE_STRUCT_POD` bytes, then `Inv.Load(...)`.
- **else (this is the vanilla 5.0/25 path)** → read
  `SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD` bytes
  (`offsetof(OLD_SOLDIERCREATE_STRUCT_101, endOfPOD)`) and **NO** separate
  inventory read — the old 101 struct embeds a fixed `DO_NOT_USE_Inv[...]` POD
  array (`Soldier Create.h:202`). `CopyOldInventoryToNew` runs on already-read data.

Save mirror (`SaveLoadGame.cpp:1055-1076`): vanilla writes
`SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD` and returns immediately (no inventory
appended); non-vanilla writes the POD then `Inv.Save`.

**Rule for the walker:** read the basic struct; look at its byte-offset-0
`fDetailedPlacement`. If 1, consume an additional detailed block:
- **vanilla 5.0/25:** a fixed `SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD`-byte block,
  no trailing inventory. **This exact size is UNVALIDATED** (it is a large nested
  struct: `OLD_SOLDIERCREATE_STRUCT_101`, `Soldier Create.h:137-247`, containing
  `OLD_OBJECTTYPE_101 DO_NOT_USE_Inv[OldInventory::NUM_INV_SLOTS]` +
  5×`PaletteRepID[30]` + `name[10]` CHAR16 + many INT8s + `INT8 bPadding[115]` +
  2 filler bytes). Computing it by hand is error-prone and no stock no-light
  vanilla map exercises it, so treat detailed-vanilla as a "bail/needs-runtime-
  check" case rather than a hard byte count.
- **6.0 ≤ major < 7.0:** `offsetof(_OLD_SOLDIERCREATE_STRUCT, endOfPOD)` bytes +
  a variable OO-inventory blob (cannot be skipped without parsing the inventory).
- **major ≥ 7.0:** `offsetof(SOLDIERCREATE_STRUCT, endOfPOD)` bytes + variable
  OO-inventory blob.

In all detailed cases the detailed block ends with (for 6.0+) a variable inventory,
so a pure fixed-stride walk is impossible once a detailed placement appears with a
6.0+ map — you must parse the inventory. **For the practical read-only extractor
the safe behavior is: if any record has `fDetailedPlacement==1` on a 6.0+ map,
bail (`blocked("soldier_detailed_inventory")`); on a vanilla 5.0/25 map a detailed
record can in principle be skipped by the fixed 101-POD size once that size is
nailed down empirically.** Stock no-light vanilla campaign maps have **zero**
detailed placements (validated below), so the common path never hits this.

---

## 3. Field offsets for extraction

From the basic struct (the only fields needed for the overlay):

| Datum | vanilla rec-off (52B) | modern rec-off (64B) | type | notes |
|---|---|---|---|---|
| **gridno (position)** | **2** | **4** | INT16 / INT32 | `sStartingGridNo`; `x=g%cols, y=g//cols`, cols=160 |
| **team** (type label) | **4** | **8** | INT8 | 0=PLAYER,1=ENEMY,2=CREATURE,3=MILITIA,4=CIV |
| **facing / direction** | **7** | **11** | UINT8 | `ubDirection`, 0–7 |
| soldier class (type label 2) | **34** | **58** | UINT8 | 3 = SOLDIER_CLASS_ARMY, etc. |
| body type | 10 | 14 | INT8 | -1 (0xFF) = random |
| detailed-flag (branch) | 0 | 0 | BOOLEAN | 1 ⇒ detailed block follows |

Recommended type label = `bTeam` (and optionally `ubSoldierClass`).

---

## 4. Parse algorithm (pseudocode)

```
# preconditions: appendix walked through items, ambient, lights, mapinfo tail.
# CRITICAL: mapinfo tail = 100 bytes for major<7.0 (NOT 99), 32 for major>=7.0.
# num = ubNumIndividuals (UINT8 @ old-tail field-offset 8, or UINT16 in modern tail).

if not (flags & MAP_FULLSOLDIER_SAVED): skip section
if num == 0: section is empty (0 bytes), proceed to exit grids

if major < 7.0:   basic_size = 52; grid_off = 2; team_off = 4; dir_off = 7; class_off = 34; grid_fmt='<h'
else:             basic_size = 64; grid_off = 4; team_off = 8; dir_off = 11; class_off = 58; grid_fmt='<i'

for r in range(num):
    rec = data[pos : pos+basic_size]
    fDetailed = rec[0]
    gridno    = unpack(grid_fmt, rec, grid_off)
    team      = signed8(rec[team_off])
    facing    = rec[dir_off]
    sclass    = rec[class_off]
    emit(gridno, team, facing, sclass)
    pos += basic_size
    if fDetailed == 1:
        if major < 7.0 and (major,minor)==(5.0,25):
            # vanilla: fixed 101-POD block, no inventory  (SIZE NOT NAILED -> prefer bail)
            bail("soldier_detailed_vanilla_unsized")
        else:
            # 6.0+: POD + variable OO inventory -> cannot fixed-skip
            bail("soldier_detailed_inventory")
# pos now points exactly at the exit-grid section (uint16 count).
```

---

## 5. Empirical validation (A6.DAT + cross-checks)

Target: `…\Mod Prototype - Copy\Data-1.13\Maps\A6.DAT`, filesize 316319,
major 5.0, minor 25, flags 0x17D, cols 160. `appendix_offset = 314004`.

Appendix walk (corrected tail=100):
- items: count 0 → pos 314008
- ambient: flag NOT set (0x80 absent) → skipped
- lights: num_colors 1, light_count 0 → pos 314015
- **MapInfo tail = 100** → **pos 314115 = soldier section start**

Soldier walk — `ubNumIndividuals = 32`, stride **52**, all `fDetailedPlacement = 0`:
- 32 records × 52 = 1664 bytes → **end pos 315779**.
- Every record: `fDetailed ∈ {0}`, gridno in [0,25600), team = **1 (ENEMY)** for
  all 32, direction 0–7, `ubBodyType = -1` (random), `ubSoldierClass = 3` (ARMY).
- Sample (fDetailed, gridno, team, dir, orders, attitude, body, class):
  - `(0, 18784, 1, 6, 1, 3, -1, 3)`
  - `(0, 18302, 1, 6, 2, 3, -1, 3)`
  - `(0, 16206, 1, 2, 2, 3, -1, 3)`
  - `(0, 10323, 1, 4, 1, 3, -1, 3)` (last)

Continuation past the soldier section (proves the stride is exact):
- **exit grids:** uint16 count @ 315779 = **0** (sane, small) → pos 315781
- **door table:** uint8 count @ 315781 = **0** (sane) → pos 315782
- edge points start at 315782 with a valid `size=108, mid=49` header.

**The 32-record stride lands EXACTLY on a sane exit-grid count (0) and door-table
count (0) — the validation bar is met.**

Cross-validation on 3 more independent stock vanilla maps (all major 5.0, no light
records, `MAP_FULLSOLDIER_SAVED` set), same algorithm:

| Map | num | records OK | next exit-grid count |
|---|---|---|---|
| A6  | 32 | 32/32 | 0 (SANE) |
| A12 | 32 | 32/32 | 0 (SANE) |
| A15 | 41 | 41/41 | 0 (SANE) |
| C7  | 32 | 32/32 | 0 (SANE) |

All four walk byte-exactly to a sane exit-grid count. Zero detailed placements
across all stock no-light vanilla maps scanned.

### Full-EOF chain note (out of scope for soldiers)
The complete walk to EOF through edgepoints+schedules did **not** close cleanly,
but the failure is entirely in the **edgepoints** section, not soldiers: vanilla
`WriteMapEdgepoints` (`Map Edgepoints.cpp:879`) writes array elements as **INT16**
while `OldLoadMapEdgepoints` reads them as **INT32** (`Map Edgepoints.cpp:901`) — a
known vanilla save/load width inconsistency, plus a non-uniform per-array element
width in this file. This is downstream of the soldier section and does not affect
soldier-stride correctness (soldiers land exactly on exit-grids count = doortable
count = 0, both correctly parsed). Resolving the edgepoint width is a separate task.

---

## 6. Confidence & unresolved items

**Confidence: HIGH** for the basic-placement read path (the deliverable):
- Vanilla (major 5.0 / minor 25, and major < 7.0 generally): stride **52**, field
  offsets as tabled — validated byte-exactly on 4 stock maps.
- MapInfo-tail-must-be-100 correction is the key fix and is empirically proven.

**MEDIUM / unresolved:**
1. **Modern basic stride = 64 (major ≥ 7.0)** — derived from struct + MSVC padding,
   NOT empirically validated (no stock major≥7.0 map in the Copy carries
   `MAP_FULLSOLDIER_SAVED`; they save flag 0x10 only = 0 soldiers). Field offsets
   for the modern path (grid@4, team@8, dir@11, class@58) are derivation-only.
2. **Detailed placement (`fDetailedPlacement==1`) exact byte size is UNVALIDATED.**
   - Vanilla: fixed `offsetof(OLD_SOLDIERCREATE_STRUCT_101, endOfPOD)` bytes, no
     trailing inventory — size not computed (large nested struct, error-prone,
     no test map). Recommend bail-or-runtime-measure rather than a hardcoded number.
   - 6.0+: POD + **variable OO inventory** → cannot be fixed-skipped at all without
     parsing inventory; the walker must bail on these.
   - Mitigation: all stock no-light vanilla campaign maps have **zero** detailed
     placements, so the common extractor path never branches here.
3. The existing in-repo `appendix_extract.py:80` tail_size=99 is a latent bug;
   anyone wiring the soldier walk in must change it to 100 (major<7.0) or the
   section start is off by one.

**Source citations (all under `Source Files/1.13 Source/source-master/`):**
- `TileEngine/worlddef.cpp:2255-2296` (appendix save order), `:2277` SaveSoldiers,
  `:3230` LoadSoldiers.
- `Tactical/Soldier Init List.cpp:235-280` (basic Load/Save + version gate),
  `:350-378` (SaveSoldiersToMap loop), `:282-348` (LoadSoldiersFromMap, count from
  `gMapInformation.ubNumIndividuals`, detailed gated by `fDetailedPlacement`).
- `Tactical/Soldier Create.h:91-110` `_OLD_BASIC_SOLDIERCREATE_STRUCT`,
  `:112-135` modern `BASIC_SOLDIERCREATE_STRUCT`, `:137-247` `OLD_SOLDIERCREATE_STRUCT_101`,
  `:448-450` POD-size offsetof macros.
- `Tactical/Soldier Control.h:6-7` `MAXPATROLGRIDS=10`.
- `Ja2/SaveLoadGame.cpp:1055-1101` detailed `SOLDIERCREATE_STRUCT::Save/Load`.
- `Tactical/Map Information.h:18-38` `_OLD_MAPCREATE_STRUCT` (99 raw → 100 sizeof),
  `Map Information.cpp:141-174,184-187` MapInfo Save.
- `TileEngine/worlddef.h:39,46,49-50` version constants
  (`MAJOR_MAP_VERSION 8.0`, `VANILLA_MAJOR=5.00`, `VANILLA_MINOR=25`).
- `TileEngine/Exit Grids.cpp:200-215` exit-grid save (uint16 count + recs).
