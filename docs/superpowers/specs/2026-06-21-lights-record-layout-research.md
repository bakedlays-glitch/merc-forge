# JA2 1.13 binary map — "world lights" appendix section parse spec (READ-ONLY)

**Goal:** advance a byte cursor exactly past the per-light record section so the
downstream MapInfo-tail / soldier parse is reachable on LIT sectors (town maps
like A1/A2). Plotting light positions is a secondary bonus.

**Confidence: HIGH** — derived from source (`SaveMapLights`/`LoadMapLights` +
`LIGHT_SPRITE` struct), with MSVC padding reasoned explicitly, and empirically
validated against two real maps (A1 = 1 light, A2 = 8 lights). Both walks land
on a valid 100-byte MapInfo tail.

---

## 1. Per-light parse algorithm

The lights section is written by `SaveMapLights` and read by `LoadMapLights`
(`TileEngine/worlddef.cpp`). Section layout (after the items + ambient sections):

```
HEADER (already handled correctly by the existing extractor):
  u8   ubNumColors
  SGPPaletteEntry[ubNumColors]      # 4 bytes each (himage.h:40-47: 4*UINT8)
  u16  usNumLights

PER-LIGHT RECORD, repeated usNumLights times:
  LIGHT_SPRITE  (24 bytes, fixed — see §2)        # worlddef.cpp:4168
  u8   ubStrLen                                    # worlddef.cpp:4170-4171
  u8[ubStrLen]  template-filename bytes            # worlddef.cpp:4172
                #   ubStrLen = strlen(name) + 1, i.e. INCLUDES the trailing NUL
```

Pseudocode (read-only cursor advance):

```
pos = lights_records_offset            # = after header; A1 272651, A2 309937
for _ in range(usNumLights):
    sprite = data[pos : pos + 24]      # LIGHT_SPRITE, fixed 24 bytes
    iX, iY = int16(sprite[0:2]), int16(sprite[2:4])   # tile col/row (position)
    pos += 24
    ubStrLen = u8(data[pos]); pos += 1                 # length INCLUDES NUL
    template = data[pos : pos + ubStrLen]; pos += ubStrLen   # e.g. "L-R05.LHT\0"
# pos now points at the MapInfo tail (100 bytes for major < 7.0).
```

**Per-light stride = 24 + 1 + ubStrLen** (variable, because the filename string
length varies). In the canonical maps every light template is `"L-R0N.LHT"`
(9 chars + NUL → ubStrLen = 10 → stride = 35), but the string is genuinely
variable-length and MUST be read from `ubStrLen`, not assumed.

**Source citations:**
- Header read: `worlddef.cpp:4196-4199` (`LOADDATA &ubNumColors,1` /
  `LOADDATA LColors, 4*ubNumColors` / `LOADDATA &usNumLights,2`).
- Header write: `worlddef.cpp:4126-4150`.
- Per-light read loop: `worlddef.cpp:4225-4263`
  (`LOADDATA &TmpLight, sizeof(LIGHT_SPRITE)` @4227, then
  `LOADDATA &ubStrLen, 1` @4228, then `LOADDATA str, ubStrLen` @4236).
- Per-light write loop: `worlddef.cpp:4153-4176`
  (`FileWrite &LightSprites[cnt], sizeof(LIGHT_SPRITE)` @4168;
  `ubStrLen = strlen(pLightNames[...]) + 1` @4170;
  `FileWrite pLightNames[...], ubStrLen` @4172).
- `ubStrLen` includes the NUL: `+ 1` at `worlddef.cpp:4170`; on load the buffer
  read is `str[ubStrLen] = 0` @4239 (so `str` already contains the saved NUL).

**Version branches:** none in the lights section. `LoadMapLights` /
`SaveMapLights` take no version parameter and have no major/minor branch — the
record layout is identical across map versions in this 1.13 source. (The only
modern divergence in the appendix is the MapInfo tail and soldier records, which
already version-branch in the extractor.) The save struct is the runtime
`LIGHT_SPRITE` itself (no separate on-disk struct), and `LIGHT_NODE` /
`LightTemplate` are NOT written per light — only `LIGHT_SPRITE` + the length-
prefixed template name.

---

## 2. The fixed part — `LIGHT_SPRITE` (24 bytes) + padding derivation

Definition, `TileEngine/lighting.h:90-97`:

```c
typedef struct {
    INT16   iX, iY;
    INT16   iOldX, iOldY;
    INT16   iAnimSpeed;
    INT32   iTemplate;
    UINT32  uiFlags;
    UINT32  uiLightType;
} LIGHT_SPRITE;
```

MSVC default packing, x86-32 (4-byte max align; INT16/UINT16 align 2,
INT32/UINT32 align 4):

| offset | size | field        | note                                            |
|-------:|-----:|--------------|-------------------------------------------------|
| 0      | 2    | iX           | **position X (tile column)**                    |
| 2      | 2    | iY           | **position Y (tile row)**                       |
| 4      | 2    | iOldX        |                                                 |
| 6      | 2    | iOldY        |                                                 |
| 8      | 2    | iAnimSpeed   |                                                 |
| 10     | 2    | (padding)    | inserted to align iTemplate (INT32) to offset 12 |
| 12     | 4    | iTemplate    | index into `pLightNames[]`                      |
| 16     | 4    | uiFlags      | LIGHT_PRIMETIME 0x80 / LIGHT_NIGHTTIME 0x100 etc |
| 20     | 4    | uiLightType  |                                                 |

`sizeof(LIGHT_SPRITE) = 24` bytes (struct align = 4; total already a multiple of
4, so no trailing pad). Matches the in-repo comment ("LIGHT_SPRITE = 24 bytes").

**Position field:** `iX` @ offset 0, `iY` @ offset 2, both `INT16`. These are
TILE coordinates (column, row) — NOT a packed gridno. Derive gridno as
`iY * cols + iX`. Confirmed: A1's single light is iX=100, iY=65; A2's eight
lights all read as plausible interior tile coords within the 160×160 map. The
2-byte slot at offset 10 reads as `0x0000` in every observed record, consistent
with it being pad (the engine never initialises it as a field).

---

## 3. Empirical validation result

Targets: `…\Mod Prototype - Copy\Data-1.13\Maps\A1.DAT` (1 light) and
`A2.DAT` (8 lights). Both: major=5.0, minor=25, flags=0x17D, 160×160
(WORLD_MAX = 25600). Validation walked the lights at the §1 stride, then checked
the MapInfo tail and the first soldiers.

### A1 (1 light)
- Items end → ambient → lights header @ 272651: `ubNumColors=1`, `usNumLights=1`.
- Light[0] @ 272651: iX=100 iY=65 iOldX=100 iOldY=65 animSpeed=0 pad=0
  iTemplate=1 uiFlags=0x9B uiLightType=0; ubStrLen=10; name=`"L-R05.LHT"`.
  Stride = 24+1+10 = 35.
- **Lights end → cursor = 272686** (exactly the MapInfo tail start).
- **MapInfo tail @ 272686** (100-byte `_OLD_MAPCREATE_STRUCT`):
  N=-1, E=7802, S=14064, W=-1, C=10529, I=-1 — all in range (-1 or 0..25599).
  **ubNumIndividuals = 33** (UINT8 @ tail+8) — sane.
- Soldiers (52-byte basic, count=33) begin @ 272786: soldier[0] gridno=10180
  team=4 (civilian); soldier[2] gridno=0 team=0 (player); all gridnos in-range,
  teams ∈ 0..4.

### A2 (8 lights — the stronger repeated-stride test)
- Lights header @ 309937: `ubNumColors=1`, `usNumLights=8`.
- Eight light records @ 309937, 309972, 310007, 310042, 310077, 310112,
  310147, 310182 — each iX/iY a plausible interior tile, ubStrLen=10,
  name=`"L-R08.LHT"` (uniform stride 35).
- **Lights end → cursor = 310217** (exactly the MapInfo tail start).
- **MapInfo tail @ 310217:** N=12179, E=12310, S=19153, W=20694, C=16552, I=-1 —
  all in range. **ubNumIndividuals = 39** — sane.
- Soldiers (count=39) begin @ 310317: soldier[0] gridno=13661 team=4 (civilian);
  soldier[1] team=1 (enemy); all gridnos in-range, teams ∈ 0..4.

**Both A1 and A2: lights → tail landing is exact and the tail is structurally
valid (entry points in-range, ubNumIndividuals sane), and the immediately
following soldiers decode with valid gridnos + plausible teams.** This closes the
lights→tail→soldiers chain for the part that matters: the lights stride is
correct.

### Code change landed (read-only walk)
`mercwizard_core/mapforge_engine/appendix_extract.py` now walks the records
(replacing the old `return blocked("lights_records")`). Via the real
`extract_appendix_entities` entry point:

- A1: `reached = [items, lights_header, lights, mapinfo]`, 1 light plotted
  (x=100, y=65, "L-R05.LHT").
- A2: `reached = [items, lights_header, lights, mapinfo]`, 8 lights plotted.

Tests: `tests/test_mapforge_appendix_extract.py` — replaced the obsolete
`test_blocks_on_light_records` with `test_walks_light_records_and_continues_to_tail`
+ `test_light_string_overrun_degrades_gracefully` (synthetic) and added
install-gated real-map regressions `test_real_town_lights_walk_to_tail[A1/A2]`.
Full sidecar suite: **891 passed, 1 skipped** (no regressions).

---

## 4. Confidence + unresolved / version-specific notes

**Confidence: HIGH** for the lights stride and the position field, on
major=5.0/minor=25 vanilla maps. The stride is the PRIMARY deliverable and it is
proven both by source and by exact tail landing on two maps (one with a single
light, one with eight).

Unresolved / not validated here:

- **Detailed soldier placements (separate, pre-existing limit).** A1/A2 contain
  `fDetailedPlacement=1` soldiers, after which a variable-size
  `SOLDIERCREATE_STRUCT priority` block follows the 52-byte basic record
  (`worlddef.cpp:2658-2661`). The extractor (correctly) bails at
  `soldier_detailed`. This is NOT a lights issue — the lights cursor already
  landed on a valid tail before soldiers begin. The downstream full soldier-
  section closure on LIT maps with detailed placements is a separate task.
- **Modern maps (major ≥ 7.0).** The lights section itself has no version branch
  in this source, so the 24-byte LIGHT_SPRITE + u8-len + string stride should
  hold for major ≥ 7.0 too — but I had no LIT major-7.0 map to confirm, so treat
  modern lights as MEDIUM until a real major-7.0 LIT sector is walked. (The
  MapInfo tail / soldier records DO version-branch and are handled separately.)
- **Multi-color palettes.** All observed maps wrote `ubNumColors=1`. The header
  handles `ubNumColors > 1` generically (4 bytes each), but this path is
  untested against real data with >1 color.
- **The offset-10 2-byte slot.** Reasoned as struct padding (and reads 0x0000 in
  every observed record); not used as a field by the engine. No risk to the
  stride either way since it is inside the fixed 24-byte block.
