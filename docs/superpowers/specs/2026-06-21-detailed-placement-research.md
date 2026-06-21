# Detailed-placement POD size — `SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD`

**Goal:** the exact byte size of the *detailed* soldier-placement POD block written per
individual when `BASIC_SOLDIERCREATE_STRUCT.fDetailedPlacement == 1`, on vanilla
(major 5.0 / minor 25) maps, so a read-only parser can SKIP it and keep walking.

**Answer: `SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD = 1040` bytes. Confidence: HIGH.**
Computed from the struct via `offsetof(OLD_SOLDIERCREATE_STRUCT_101, endOfPOD)` and
confirmed empirically against A1.DAT (33 indiv, 1 detailed) and A2.DAT (39 indiv,
7 detailed) — both land on a sane exit-grid / door section *only* at 1040.

---

## 1. Computed POD size — field-by-field `offsetof(endOfPOD)`

The engine's own macro is the definition (`Tactical/Soldier Create.h:448`):

```cpp
#define SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD offsetof( OLD_SOLDIERCREATE_STRUCT_101, endOfPOD )
```

`OLD_SOLDIERCREATE_STRUCT_101` is `Tactical/Soldier Create.h:137-247`. `endOfPOD` is the
`char` marker at line 244 (preceded by filler `ef1,ef2` at line 242 — comment: *"Extra
filler to get offsetof(endOfPOD) to match SIZEOF(oldstruct)"*). MSVC x86-32 default
packing `/Zp8`; no `#pragma pack` anywhere in `Soldier Create.h` / `Item Types.h` /
`Overhead Types.h` (verified). Scalar align = size; array align = element align;
struct align = max member align.

### Supporting constants (all source-verified)
- `PaletteRepID` = `CHAR8[30]`, size 30, align 1 — `Tactical/Overhead Types.h:359`
- `OLD_MAXPATROLGRIDS` = 10 — `Tactical/Soldier Control.h:6`
- `OldInventory::NUM_INV_SLOTS` = `NUM_ORIGINAL_INV_SLOTS` = **19** — `Item Types.h:43,12`
- `OLD_MAX_ATTACHMENTS_101` = 4 — `Item Types.h:279`
- `OLD_OBJECTTYPE_101_UNION` = **12 bytes / align 4** (money member `bMoneyStatus(1)`,
  pad→4, `uiMoneyAmount(UINT32)@4`, `ubMoneyUnused[3]@8` ⇒ 11 → round to align-4 = 12;
  all other union members ≤ 8B) — `Item Types.h:299-368`
- `OLD_OBJECTTYPE_101` = **36 bytes / align 4** — `Item Types.h:372-391`
  (usItem u16, ubNumberOfObjects u8, union[12]@4, usAttachItem[4] u16 = 8, bAttachStatus[4]
  i8 = 4, then 6× u8 → raw 34, round to align-4 = **36**). Matches the prior items
  research (`SIZEOF_OLD_OBJECTTYPE_101 = 36`, in `parse_world_items.py:70`).

### `offsetof(endOfPOD)` running layout (x86-32, /Zp8)

| Members | Bytes | Offset after |
|---|---|---|
| 5× BOOLEAN/UINT8 (fStatic…fCopyProfileItemsOver) | 5 | 5 |
| sSectorX INT16 (pad→6) | 2 | 8 |
| sSectorY INT16 | 2 | 10 |
| ubDirection UINT8 | 1 | 11 |
| sInsertionGridNo INT16 (pad→12) | 2 | 14 |
| bTeam…bAIMorale: 16× INT8 (bTeam,ubBodyType,bAttitude,bOrders + 12 attrs) | 16 | 30 |
| `DO_NOT_USE_Inv[19]` × 36B (align 4 ⇒ pad 30→32) | 684 | 716 |
| 5× PaletteRepID (CHAR8[30], align 1) = 150 | 150 | 866 |
| sPatrolGrid INT16[10] = 20 (align 2, 866 already even) | 20 | 886 |
| bPatrolCnt INT8 | 1 | 887 |
| fVisible BOOLEAN | 1 | 888 |
| name CHAR16[10] = 20 (align 2, 888 even) | 20 | 908 |
| ubSoldierClass, fOnRoof, bSectorZ: 3× 1B | 3 | 911 |
| pExistingSoldier SOLDIERTYPE* (align 4 ⇒ pad 911→912) | 4 | 916 |
| fUseExistingSoldier, ubCivilianGroup, fKillSlotIfOwnerDies, ubScheduleID, fUseGivenVehicle, bUseGivenVehicleID, fHasKeys: 7× 1B | 7 | 923 |
| bPadding INT8[115] | 115 | 1038 |
| ef1, ef2 (char) | 2 | **1040** |
| endOfPOD (char, align 1 — no pad) | — | offsetof = **1040** |

**`offsetof(endOfPOD) = 1040`.** (The `Inv` member — type `Inventory`, an STL-backed
OO container — sits *after* `endOfPOD` and is **not** part of the POD; on vanilla it is
never serialized. See §2.) The `ef1,ef2` filler is the engine deliberately padding the
POD up to a round 1040 so the on-disk POD length equals `sizeof` of the legacy struct.

> engine_graph note: `OLD_SOLDIERCREATE_STRUCT_101` / `OLD_OBJECTTYPE_101` are not indexed
> in `engine.db` (they're legacy classes, not plain structs) — no size row to cross-check.
> Source is authoritative and the empirical test (§3) confirms.

---

## 2. Vanilla detailed block has NO trailing inventory

`SOLDIERCREATE_STRUCT::Save` — `Ja2/SaveLoadGame.cpp:1055-1076` (this is the map-save path,
`fSavingMap`):

```cpp
UINT32 uiBytesToWrite = SIZEOF_SOLDIERCREATE_STRUCT_POD;          // line 1058
if (dMajorMapVersion == VANILLA_MAJOR_MAP_VERSION &&             // 5.0
    ubMinorMapVersion == VANILLA_MINOR_MAP_VERSION) {            // 25  -> line 1060
    OldSoldierCreateStruct = *this;
    pData = &OldSoldierCreateStruct;
    uiBytesToWrite = SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD;    // line 1064  = 1040
}
FileWrite(hFile, pData, uiBytesToWrite, &uiBytesWritten);        // line 1067
if (uiBytesToWrite == uiBytesWritten) {
    if (... VANILLA ...) return TRUE;                            // line 1070-1071  <- NO Inv.Save
    if (Inv.Save(hFile, fSavingMap)) return TRUE;               // line 1072  (non-vanilla only)
}
```

The vanilla branch writes exactly `SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD` (= 1040) bytes
and `return TRUE` **immediately at line 1071 — `Inv.Save` is never reached.** The read side
mirrors this: `SOLDIERCREATE_STRUCT::Load(hBuffer, …)` line 1095-1098 `LOADDATA(...,
SIZEOF_OLD_SOLDIERCREATE_STRUCT_101_POD)` then `CopyOldInventoryToNew()` (rebuilds the OO
inventory from the embedded `DO_NOT_USE_Inv[19]` POD array) — **no separate buffer read.**
So on vanilla the detailed block is a flat fixed-size 1040-byte POD; the inventory is the
`DO_NOT_USE_Inv[19]` array *inside* it, not a trailing variable section.

---

## 3. Empirical validation — A1 and A2 both land sane only at POD = 1040

Read-only walk of each map from the verified soldier-section start (= end of the 100B
MapInfo tail). Per individual: 52-byte BASIC struct (`fDetailedPlacement`@0,
`sStartingGridNo`@2 int16, `bTeam`@4 int8, `class`@34); if `fDetailed==1`, advance an
extra POD_SIZE. After all N, the cursor must hit a sane exit-grid section (small u16
count + 12B `<iiBBBx>` records with `0 ≤ iMapIndex < 25600`), then a sane u8 door table.
Bracketed POD_SIZE over **1040 ± 16**.

### A1.DAT — start 272786, 33 individuals
- Pattern: `D` then 32× `B` → **1 detailed, 32 basic.**
- All 33 gridnos in range [0,25600); teams {1=enemy, 4=civilian}; first rec
  `(fdet=1, grid=10180, team=4, class=0)`, then enemies grid 7939/8580/8740… class 3.
- @ POD=1040: cursor → **275542**, `exit_count = 0`, door_count = 0. (Weakly discriminating
  — A1 has only 1 detailed soldier and 0 exit grids, so POD≈1024-1041 all give
  exit_count=0; but the BASIC record stride is independently sane.)

### A2.DAT — start 310317, 39 individuals  ← the decisive map
- Pattern: 7× `D` then 32× `B` → **7 detailed, 32 basic.**
- **Only POD = 1040 lands sane in the entire ±16 bracket** — every other candidate
  desyncs (the 7 detailed soldiers multiply any per-record error 7×, so a wrong stride
  fails the exit-grid sanity check). 
- @ POD=1040: cursor → **319625**, `exit_count = 0`, then **door_count = 3** with valid
  door records `(sGridNo,flags) = (18953,1), (0,1), (0,0)` — all gridnos in range,
  decode OK, after_doors=319637, 4709 bytes remaining.
- All 39 gridnos in range; teams {1,4}; first recs `(1,13661,4), (1,9669,4), (1,9666,4),
  (1,17539,4), (1,15453,4), (1,18337,4)` — plausible contiguous detailed placements.

**Cross-map agreement:** the single value that makes BOTH A1 (33) and A2 (39) land on a
sane exit-grid + door section is **1040**, exactly equal to the computed
`offsetof(endOfPOD)`. A2 alone uniquely selects it.

---

## 4. Confidence & open items

- **Confidence: HIGH.** Theory (1040) and empirics (1040, uniquely on A2's 7-detailed
  walk) agree exactly; the SaveLoadGame.cpp vanilla branch confirms a flat 1040-byte POD
  with no trailing inventory; door table beyond decodes cleanly on A2.
- **Parser guidance:** for major 5.0 / minor 25, when `fDetailedPlacement == 1`, advance
  `52 + 1040` per such individual (52 for the BASIC struct already counted, +1040 for the
  detailed POD); `fDetailed == 0` advances 52 only.
- **Out of scope / unresolved (separate sizes — do NOT reuse 1040):**
  - **6.0 ≤ major < 7.0, minor > 26:** detailed block = `_OLD_SIZEOF_SOLDIERCREATE_STRUCT_POD`
    = `offsetof(_OLD_SOLDIERCREATE_STRUCT, endOfPOD)` **+ variable `Inv.Load`** (different
    struct at `Soldier Create.h` line ~443; has no embedded `DO_NOT_USE_Inv`).
  - **major ≥ 7.0:** `SIZEOF_SOLDIERCREATE_STRUCT_POD` **+ variable `Inv`**, and the BASIC
    struct is 64B not 52B. These two modern paths carry a *variable* trailing inventory and
    cannot be skipped by a fixed size — they need the OO-Inventory walk, a separate task.
  - No vanilla map with multiple exit grids was available to positively confirm exit-record
    decoding (both A1/A2 have exit_count=0); A2's door table (3 records) is the strongest
    post-soldier structural confirmation instead.
