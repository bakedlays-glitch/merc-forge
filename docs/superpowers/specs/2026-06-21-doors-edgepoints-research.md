# Door Table & Edgepoints — on-disk parse spec (READ-ONLY research)

Reverse-engineering of the JA2 1.13 map appendix **DOOR TABLE** and **EDGEPOINTS**
sections, for the MapForge read-only tactical overlay. Both sections are
**fully tractable** and validated byte-for-byte on 4 real maps. This document is
research only; no code was written or modified.

Appendix section order (the overlay already parses through *exit grids*):
```
items → ambient → lights → MapInfo tail(100B v5/32B v7) → soldiers →
exit grids → DOOR TABLE → EDGEPOINTS → schedules → (trailing data) → EOF
```
Load dispatch: `worlddef.cpp:3238-3263` (`LoadDoorTableFromMap` → `LoadMapEdgepoints` → `LoadSchedules`).
Save dispatch (last-written = schedules): `worlddef.cpp:2283-2296`.

---

## Decisive version fact (drives every width choice)

Our shipped maps are **major = 5.00, minor = 25 (or 24)**, which **equals**
`VANILLA_MAJOR_MAP_VERSION (5.00)` / `VANILLA_MINOR_MAP_VERSION (25)`
(`worlddef.h:49-50`). Two independent consequences, both confirmed empirically:

1. **`major < 7.0`** → every "vanilla/old" *load* branch is taken
   (`DOOR::Load` `:841`, edgepoint INT16 reads `Map Edgepoints.cpp:945`).
2. The MapInfo struct's stored **`ubMapVersion` field = the minor version = 25**
   — set on save at `Map Information.cpp:154`
   (`OldMapCreateStruct.ubMapVersion = ubMinorMapVersion;`). 25 is **≥ 17 and ≥ 22**,
   so `LoadMapEdgepoints` takes the **full 8-section path** and does **NOT**
   regenerate (the `<17` and `<22` trash branches are skipped). Edgepoints are
   genuinely read from disk and kept — they are *not* regenerated for these maps.

> Note on C7.DAT (minor=24): its door count is 0, so the save-time vanilla-branch
> mismatch (`minor==25` required) is moot. For *loading*, the format is selected by
> `major<7.0` only (door + edgepoint element width), so C7 parses identically. All 4
> maps validated under one code path.

---

## 1. DOOR TABLE — confidence: **HIGH**

### Source
- Count: `gubNumDoors` is **`UINT8`** (`Keys.h:181`). Save writes `sizeof(gubNumDoors)`
  = **1 byte** (`Keys.cpp:905`); load reads `sizeof(gubNumDoors)` = 1 byte (`Keys.cpp:881`).
- Record: for our maps the **`_OLD_DOOR`** struct is on disk. Save selects it when
  `major==5.00 && minor==25` (`Keys.cpp:857-870`, writes `sizeof(_OLD_DOOR)`); load
  selects it when `major<7.0` (`Keys.cpp:841-846`, reads `sizeof(_OLD_DOOR)`).
- `_OLD_DOOR` definition: `Keys.h:66-79`.

### `_OLD_DOOR` on-disk layout (MSVC x86-32, struct align = 2, largest member = INT16)
| off | field             | type    | bytes | notes |
|-----|-------------------|---------|-------|-------|
| 0   | `sGridNo`         | INT16   | 2     | **the gridno** (signed; -1 / NOWHERE possible in theory) |
| 2   | `fLocked`         | BOOLEAN | 1     | **lock status** (1 = locked) |
| 3   | `ubTrapLevel`     | UINT8   | 1     | trap find-difficulty 0-10 |
| 4   | `ubTrapID`        | UINT8   | 1     | trap type (0 = none) |
| 5   | `ubLockID`        | UINT8   | 1     | lock id (0 = none) — index into LockTable; *no per-door "has key" bool* |
| 6   | `bPerceivedLocked`| INT8    | 1     | reset to 0 on load; not meaningful on disk |
| 7   | `bPerceivedTrapped`| INT8   | 1     | reset to 0 on load |
| 8   | `bLockDamage`     | INT8    | 1     | damage to lock |
| 9   | `bPadding[4]`     | INT8×4  | 4     | uninitialized padding |
|     | **total**         |         | **14**| 13 raw bytes, padded up to even (align 2) = **14** |

**Record size = 14 bytes.** (The in-repo `parse_dat_ext.py:71` comment claiming
`uint16 count + count*10 bytes` is **wrong on both fields** — count is u8 not u16,
record is 14 not 10.)

"Has-key" is not a per-door field — lock/key association lives in `ubLockID` →
global LockTable. For overlay purposes the load-bearing fields are
**`sGridNo` (off 0) + `fLocked` (off 2)**; `ubTrapID`/`ubLockID` (off 4/5) are
optional extras.

### Pseudocode
```
count = u8(at pos); pos += 1
for i in range(count):
    sGridNo  = int16(pos + 0)     # gridno, plot if 0 <= sGridNo < rows*cols
    fLocked  = u8   (pos + 2)
    ubTrapID = u8   (pos + 4)     # optional
    ubLockID = u8   (pos + 5)     # optional
    pos += 14
```

### Empirical result (4 maps, world_max = 25600)
| map  | count | sample (sGridNo, fLocked)               | all gridnos valid | cursor lands on edge hdr (size, middle) |
|------|-------|-----------------------------------------|-------------------|------------------------------------------|
| A6   | 0     | —                                       | n/a               | (108, 49) ✓ |
| A1   | 0     | —                                       | n/a               | (0, 0) ✓ (empty 1N section) |
| A2   | **3** | (18953,1) (15621,1) (18475,1)           | ✓ 0 OOB           | (277, 134) ✓ middle<size |
| C7   | 0     | —                                       | n/a               | (298, 149) ✓ |

A2 is the discriminator: only **u8-count + 14B record** yields 3 valid locked-door
gridnos AND a sane edgepoints header (size 277, middle 134 < 277). Record sizes 12
and 13 produce garbage `fLocked`/edge headers; u16-count overruns. A6 landing
(@315781 → 315782, count 0) matches the prior research's known landing.

---

## 2. EDGEPOINTS — confidence: **HIGH**

### Source
- Writer `WriteMapEdgepoints` (`Map Edgepoints.cpp:855-872`): always writes
  `INT16 size` + `INT16 middleIndex` (`:858-859`); if size>0, writes the array as
  **`INT16` elements** when `major==5.00 && minor==25` (`:862-868`), else INT32.
  → **On our maps the array is INT16 on disk.**
- `SaveMapEdgepoints` (`:874-886`) emits **8 sub-sections in fixed order**:
  `1st N, 1st E, 1st S, 1st W, 2nd N, 2nd E, 2nd S, 2nd W`.
- Reader `LoadMapEdgepoints` (`:926-1096`): since `ubMapVersion(25) ≥ 17` it skips
  `OldLoadMapEdgepoints`, reads all 8 sections, and because `major<7.0` reads each
  array as **INT16** then widens to INT32 in memory (`:945-956` etc.). Final
  `ubMapVersion(25) < 22` is false → **no regenerate / no trash** (`:1089-1093`).

> The "INT16-written / INT32-read" inconsistency flagged in prior research lives
> only in `OldLoadMapEdgepoints` (`:889-923`, the `ubMapVersion<17` legacy path),
> which our maps never hit. For our files writer and reader **agree on INT16**.

### On-disk layout (per sub-section, ×8 in the order above)
| off | field          | type   | bytes |
|-----|----------------|--------|-------|
| 0   | `size`         | UINT16 | 2     | element count (0 allowed) |
| 2   | `middleIndex`  | UINT16 | 2     | index into the array (< size when size>0) |
| 4   | `array[size]`  | INT16  | 2×size| each element = a gridno |

If `size == 0`, only the 4-byte header is present (writer returns early, `:860-861`).
Empty 2nd-priority sections are normal.

### Pseudocode
```
for s in range(8):              # 1N,1E,1S,1W,2N,2E,2S,2W
    size   = u16(pos + 0)
    middle = u16(pos + 2)
    pos += 4
    for i in range(size):
        gridno = int16(pos + i*2)   # plot if 0 <= gridno < rows*cols
    pos += size * 2
# pos now == start of schedules section (u8 count + ...)
```

### Empirical result (element width INT16 vs INT32)
| map  | edge end | sections (size,middle,OOB)                              | grids_ok | next byte = sched count (sane?) |
|------|----------|---------------------------------------------------------|----------|----------------------------------|
| A6   | 316126   | 1N(108,49,0) 1W(48,23,0) rest 0                         | ✓ 0 OOB  | 0 ✓ |
| A1   | 276763   | 1E(285,141,0) 1S(308,153,0) rest 0                      | ✓ 0 OOB  | 1, sample (t=1290,grid=10178) ✓ |
| A2   | 321946   | 1N(277,134) 1E(265,135) 1S(296,146) 1W(284,142)         | ✓ 0 OOB  | 7, samples grids 17078/18336 ✓ |
| C7   | 325238   | 1N(298,149) 1E(274,137) 1S(299,147) 1W(274,134)         | ✓ 0 OOB  | 0 ✓ |

- **INT16 closes cleanly on all 4 maps**: every section parses, `middle < size`
  always, **zero out-of-range gridnos**, and the very next byte is a plausible
  schedule count (0/1/7/0) with valid time/gridno samples where present.
- **INT32 overruns immediately** on every map (it reads section-0 size, then the
  next "size" comes out as 14000–24000 and runs past EOF). Unambiguous.

The walk lands exactly on the schedules header (`UINT8 count` + `_OLD_SCHEDULENODE`
records, 36 B each for major<7.0; `Scheduling.h:45-55`, `Scheduling.cpp:551-589`),
confirming the edgepoint end is the true schedules start.

### Note on trailing data (not a defect)
A full walk through schedules does **not** reach EOF — each file has a trailing
region (A6 ~192 B, A2 ~2.1 KB, C7 ~2.2 KB, A1 ~37 KB) after the last schedule.
Schedules is the last section the engine writes (`worlddef.cpp:2293-2298`,
`FileClose` immediately after), so this tail is data `LoadWorld` never reads
(map-editor trailing/minimap-style INT16 runs — irrelevant to the overlay). It does
**not** affect door/edgepoint closure: those two sections parse exactly and hand off
to a valid schedules header. **Schedules remain out of scope; the overlay walk can
stop after edgepoints.**

---

## 3. Per-section confidence & recommendation

| section    | confidence | shippable now |
|------------|------------|----------------|
| Door table | **HIGH**   | **YES** — u8 count + 14B `_OLD_DOOR`; plot `sGridNo`@0 + `fLocked`@2 (+ optional `ubTrapID`@4 / `ubLockID`@5). Validated on 4 maps incl. A2's 3 locked doors. |
| Edgepoints | **HIGH**   | **YES** — 8 sub-sections, each `u16 size + u16 middle + size×INT16`. INT16 element width is the only one that closes; validated on 4 maps, 0 OOB gridnos, lands on a sane schedules header. |

**Recommendation: ship BOTH.** Doors and edgepoints are independently and jointly
validated byte-for-byte across A6/A1/A2/C7. After parsing edgepoints the cursor sits
exactly at the schedules header, so the overlay can either stop there (recommended —
schedules are variable-size and out of scope) or, if ever needed, doors-only is a
safe earlier stop. No regeneration caveat applies to these maps
(`ubMapVersion = 25 ≥ 22`).

### Guardrails for the implementer (read-only parse)
- Count widths: doors = **UINT8**, edgepoint `size`/`middle` = **UINT16**.
- Door record = **14 B** (not 10/12/13); edgepoint element = **INT16** (not INT32).
- Bounds-check every gridno against `rows*cols` and bail (don't crash) on overrun —
  the existing `appendix_extract` `blocked_at` pattern is the right model.
- These widths are correct for `major < 7.0` maps. A `major ≥ 7.0` map would use a
  4-byte `DOOR.sGridNo`/larger record and INT32 edgepoints — gate on `major` if such
  maps ever appear (current Wasteland maps are all 5.0).
```
