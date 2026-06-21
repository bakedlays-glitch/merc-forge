# MapForge Soldier/NPC Overlay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the MapForge read-only tactical overlay to parse and render **soldier/NPC/enemy/civilian placements** (the map's soldier-init section), so they appear as team-colored markers.

**Architecture:** Extends the existing `extract_appendix_entities` walker (which already reaches the soldier section and bails). Adds a fixed-stride soldier-record parser, surfaces `soldiers` through the endpoint/model, and draws team-colored NPC markers in the existing SVG overlay. Still **read-only** — no `.dat` write path is touched.

**Tech Stack:** Python 3 (`struct`), FastAPI, pytest; TypeScript + React.

## Global Constraints

- **Source of truth for byte layout:** `.superpowers/sdd/soldier-layout-research.md` (HIGH-confidence, source-cited, validated byte-exact on A6/A12/A15/C7). Use its exact offsets/sizes.
- **MapInfo tail is 100 bytes for major<7.0, NOT 99.** The current `appendix_extract.py` uses 99 — a latent off-by-one (masked because every v5 map bails at soldiers before using the post-tail cursor). This plan MUST change it to 100, or the soldier section starts one byte off and parses as garbage. (Modern v7 tail = 32, unchanged.)
- **Vanilla / major<7.0 basic soldier record = 52 bytes.** Offsets within the record: `fDetailedPlacement` @0 (BOOLEAN), `sStartingGridNo` @2 (INT16), `bTeam` @4 (INT8), `ubDirection` @7 (UINT8), `ubSoldierClass` @34 (UINT8).
- **Modern / major≥7.0 basic soldier record = 64 bytes** (derived, not empirically validated — no stock v7 map carries soldiers). Offsets: gridno @4 (INT32), team @8, direction @11, class @58.
- **Count** = the MapInfo tail's `ubNumIndividuals` (UINT8 @ tail-offset 8 for v5; UINT16 @ tail-offset 24 for v7 — already what `parse_dat_ext` uses). There are NO marker/count bytes around the soldier records themselves.
- **Detailed placements:** if any record's `fDetailedPlacement` (@0) == 1, the record is followed by a variable/unsized detailed block. The walker must **bail** `blocked("soldier_detailed")` (returning the soldiers parsed so far). Stock maps have ZERO detailed placements, so the common path never bails.
- **team → label:** `0=player, 1=enemy, 2=creature, 3=militia, 4=civilian` (else `other`). `bTeam` is signed INT8.
- gridno → tile: `x = gridno % cols`, `y = gridno // cols`; `gridno < 0` → skip.
- **Read-only, never throw** (same contract as the existing extractor): on anything unparseable, set `blocked_at` and return what was reached.
- **Reachability:** soldiers come AFTER lights in file order. Maps with light RECORDS (`light_count>0`, e.g. A1/A2) still block at `lights_records` before reaching soldiers — that is expected and out of scope (a separate lights-record task). Light-less maps (A6/A12/A15/C7 and many wilderness/stock sectors) show NPCs.
- **Out of scope (do NOT touch):** `parse_dat_ext.py`'s own `parse_appendix_minimal` has the same 99→100 latent bug but is a separate consumer (corpus stats) and is masked there too; leave it. Lights-record parsing. Doors/edgepoints/schedules.
- **Venv:** run sidecar python as `./.venv/Scripts/python.exe` from `sidecar/`. Frontend gate: `cd frontend && node node_modules/typescript/bin/tsc --noEmit` exit 0.
- **Commits:** end each message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Branch off `main` (this work is post-merge of the first overlay slice); USER pushes.

---

### Task 1: Soldier-record extraction + tail-100 fix

**Files:**
- Modify: `sidecar/mercwizard_core/mapforge_engine/appendix_extract.py`
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Produces: `extract_appendix_entities` now (a) uses a 100-byte v5 tail, (b) emits `out["soldiers"]` (list of `{gridno, x, y, team, team_label, facing, soldier_class}`) and adds `"soldiers"` to `reached`, (c) bails `blocked("soldier_detailed")` on a detailed placement. The output dict gains the `soldiers` key (always present, default `[]`).

- [ ] **Step 1: Update the v5 tail test helper to 100 bytes and fix its caller**

The existing `_old_tail_99` helper builds a 99-byte tail; the parser will now read 100. Rename + resize it and update its one caller so the items test still reaches the tail.

Replace the `_old_tail_99` helper definition with:

```python
def _old_tail_100(north=-1, east=-1, south=-1, west=-1, center=-1, isolated=-1,
                  num_individuals=0):
    """100-byte _OLD_MAPCREATE_STRUCT (v<7) — sizeof is 100 (99 raw fields,
    MSVC align-2 round-up). N/E/S/W int16 @0/2/4/6, ubNumIndividuals @8,
    center @12, isolated @14, padded to 100."""
    b = bytearray(b"\x00" * 100)
    struct.pack_into("<hhhh", b, 0, north, east, south, west)
    b[8] = num_individuals & 0xFF
    struct.pack_into("<hh", b, 12, center, isolated)
    return bytes(b)
```

In `test_extracts_world_items_with_positions`, change `data += _old_tail_99()` to `data += _old_tail_100()`.

- [ ] **Step 2: Write the failing soldier tests**

Add these helpers + tests:

```python
def _old_soldier(gridno, team=1, facing=2, sclass=3, detailed=0):
    """One 52-byte _OLD_BASIC_SOLDIERCREATE_STRUCT (v<7). fDetailed@0,
    sStartingGridNo@2 (int16), bTeam@4 (int8), ubDirection@7, ubSoldierClass@34."""
    b = bytearray(52)
    b[0] = detailed
    struct.pack_into("<h", b, 2, gridno)
    struct.pack_into("<b", b, 4, team)
    b[7] = facing
    b[34] = sclass
    return bytes(b)

def test_extracts_soldiers_with_positions_and_team():
    # flags=SOLDIER only: no items/ambient/lights -> tail(100) -> 2 soldiers.
    data = _old_tail_100(num_individuals=2)
    data += _old_soldier(gridno=12880, team=1, facing=6, sclass=3)   # enemy at (80,80)
    data += _old_soldier(gridno=160,   team=4, facing=2, sclass=0)   # civilian at (0,1)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert "soldiers" in out["reached"]
    assert [(s["gridno"], s["x"], s["y"], s["team"], s["team_label"], s["facing"], s["soldier_class"])
            for s in out["soldiers"]] == [
        (12880, 80, 80, 1, "enemy", 6, 3),
        (160, 0, 1, 4, "civilian", 2, 0)]

def test_soldier_detailed_placement_bails():
    # one basic, one detailed (fDetailed=1) -> bail after the detailed record's basic part.
    data = _old_tail_100(num_individuals=2)
    data += _old_soldier(gridno=100, team=1)
    data += _old_soldier(gridno=200, team=1, detailed=1)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] == "soldier_detailed"
    # the first (basic) soldier was emitted before the detailed one bailed
    assert [s["gridno"] for s in out["soldiers"]] == [100]

def test_zero_soldiers_section_is_empty():
    # SOLDIER flag set but ubNumIndividuals=0 -> empty soldier section, continue.
    data = _old_tail_100(num_individuals=0)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert out["soldiers"] == []
    assert "soldiers" in out["reached"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: the three new soldier tests FAIL (no `soldiers` key / still bails `"soldiers"`); the items + entry-point tests PASS (now that the tail helper is 100).

- [ ] **Step 4: Implement the tail fix + soldier parser**

In `appendix_extract.py`:

(a) Add module-level constants near the top (after the imports):

```python
TEAM_LABELS = {0: "player", 1: "enemy", 2: "creature", 3: "militia", 4: "civilian"}

# Basic soldier record (BASIC_SOLDIERCREATE_STRUCT). Vanilla/major<7.0 = 52 bytes;
# modern/major>=7.0 = 64 bytes. Offsets per soldier-layout-research.md.
_SOLDIER_OLD = {"size": 52, "grid_fmt": "<h", "grid_off": 2, "team_off": 4,
                "dir_off": 7, "class_off": 34}
_SOLDIER_NEW = {"size": 64, "grid_fmt": "<i", "grid_off": 4, "team_off": 8,
                "dir_off": 11, "class_off": 58}
```

(b) Initialize the `soldiers` list in the `out` dict (add the key alongside `items`/`entry_points`/`exit_grids`):

```python
        "items": [], "entry_points": [], "exit_grids": [], "soldiers": [],
```

(c) Change the tail size from 99 to 100 for the v5 path. Find:

```python
    tail_size = 32 if major >= 7.0 else 99
```
Replace with:
```python
    tail_size = 32 if major >= 7.0 else 100  # _OLD_MAPCREATE_STRUCT sizeof = 100 (99 raw, align-2)
```

(d) Read `ubNumIndividuals` from the tail BEFORE advancing `pos`. Inside the tail block, after the entry-point loop and before `pos += tail_size`, add:

```python
    num_individuals = data[pos + 8] if major < 7.0 else struct.unpack_from("<H", data, pos + 24)[0]
```

(e) Replace the soldier bail block:

```python
    # 5. SOLDIERS — deferred to a later plan.
    if flags & AW.MAP_FULLSOLDIER_SAVED:
        return blocked("soldiers")
```
with the parser:

```python
    # 5. SOLDIERS — fixed-stride basic placements (BASIC_SOLDIERCREATE_STRUCT).
    # Count is the MapInfo tail's ubNumIndividuals; no marker bytes around records.
    if flags & AW.MAP_FULLSOLDIER_SAVED:
        spec = _SOLDIER_NEW if major >= 7.0 else _SOLDIER_OLD
        for _ in range(num_individuals):
            if pos + spec["size"] > n:
                return blocked("soldier_records_overrun")
            if data[pos] == 1:  # fDetailedPlacement -> variable/unsized block follows
                return blocked("soldier_detailed")
            g = struct.unpack_from(spec["grid_fmt"], data, pos + spec["grid_off"])[0]
            team = struct.unpack_from("<b", data, pos + spec["team_off"])[0]
            facing = data[pos + spec["dir_off"]]
            sclass = data[pos + spec["class_off"]]
            pos += spec["size"]
            if g < 0:
                continue
            x, y = _xy(g, cols)
            out["soldiers"].append({"gridno": g, "x": x, "y": y, "team": team,
                                    "team_label": TEAM_LABELS.get(team, "other"),
                                    "facing": facing, "soldier_class": sclass})
        out["reached"].append("soldiers")
```

> The exit-grid block (Task 3 of the prior plan) stays immediately after this — on a soldier map the walker now flows soldiers → exit grids instead of bailing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS (all, including the 3 new soldier tests + the updated items/entry-point tests).

- [ ] **Step 6: Add the real-map regression test (install-gated)**

Append a test that validates against the real A6.DAT when the install is present, and skips cleanly otherwise:

```python
import os
import pytest
from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full

_A6 = (r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"
       r"\Data-1.13\Maps\A6.DAT")

@pytest.mark.skipif(not os.path.exists(_A6), reason="canonical install not present")
def test_real_a6_soldiers():
    data = open(_A6, "rb").read()
    out = extract_appendix_entities(data, parse_dat_full(data))
    assert out["blocked_at"] is None
    assert len(out["soldiers"]) == 32                 # ubNumIndividuals
    assert all(0 <= s["gridno"] < 25600 for s in out["soldiers"])
    assert all(s["team"] == 1 for s in out["soldiers"])   # all ENEMY in A6
    assert all(s["soldier_class"] == 3 for s in out["soldiers"])  # ARMY
```

- [ ] **Step 7: Run the full file + the mapforge suite**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Then: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/ -q -k mapforge`
Expected: PASS (the real-map test runs if the install is present, else skips). No regression.

- [ ] **Step 8: Commit**

```bash
git add sidecar/mercwizard_core/mapforge_engine/appendix_extract.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: soldier/NPC extraction + tail-100 fix\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Endpoint model — surface soldiers

**Files:**
- Modify: `sidecar/routes/mapforge.py` (the `Appendix*` models near `ParsedSector`)
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Produces: `AppendixSoldier` model + a `soldiers: list[AppendixSoldier]` field on `AppendixEntities`. The route already builds `AppendixEntities(session_id=sess.id, **ents)`, and `ents` now contains `soldiers`, so no route change beyond the model.

- [ ] **Step 1: Write the failing test**

Extend the endpoint test to include a soldier in the fake session and assert it round-trips through the model.

```python
def test_appendix_endpoint_returns_soldiers():
    data = _old_tail_100(num_individuals=1)
    data += _old_soldier(gridno=320, team=1, facing=3, sclass=3)
    parsed = {"flags": _AW.MAP_FULLSOLDIER_SAVED, "major": 5.0, "minor": 25,
              "cols": 160, "rows": 160, "appendix_offset": 0}
    sess = _fake_session(data, parsed)
    _session_store._sessions[sess.id] = sess
    try:
        res = session_appendix(sess.id)
    finally:
        del _session_store._sessions[sess.id]
    assert len(res.soldiers) == 1
    assert res.soldiers[0].gridno == 320 and res.soldiers[0].team_label == "enemy"
    assert res.soldiers[0].y == 2
```

> Reuse the existing `_old_tail_100` / `_old_soldier` helpers and the `AW` alias already imported in the file (do not re-import).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py::test_appendix_endpoint_returns_soldiers -v`
Expected: FAIL — `AppendixEntities` has no `soldiers` field (validation error or AttributeError).

- [ ] **Step 3: Add the model + field**

In `routes/mapforge.py`, add the `AppendixSoldier` model next to `AppendixExitGrid`:

```python
class AppendixSoldier(BaseModel):
    gridno: int
    x: int
    y: int
    team: int
    team_label: str
    facing: int
    soldier_class: int
```

Add the field to `AppendixEntities` (after `exit_grids`):

```python
    soldiers: list[AppendixSoldier]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS.

- [ ] **Step 5: Run the mapforge suite**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/ -q -k mapforge`
Expected: PASS, no regression.

- [ ] **Step 6: Commit**

```bash
git add sidecar/routes/mapforge.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: soldiers in appendix endpoint model\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: Frontend — NPC markers + toggle

**Files:**
- Modify: `frontend/src/lib/mapforge.ts` (type)
- Modify: `frontend/src/routes/MapForgeSector.tsx` (state, marker layer, toggle)

**Interfaces:**
- Consumes: `AppendixEntities` (now with `soldiers`); `tileToCanvasPixel`; `renderMeta`.
- Produces: `AppendixSoldier` TS interface + `soldiers` on `AppendixEntities`; a `showSoldiers` toggle and team-colored NPC markers in the existing SVG overlay.

- [ ] **Step 1: Add the TS type**

In `frontend/src/lib/mapforge.ts`, add the interface and the field on `AppendixEntities`:

```typescript
export interface AppendixSoldier {
  gridno: number; x: number; y: number;
  team: number; team_label: string; facing: number; soldier_class: number;
}
```
Add `soldiers: AppendixSoldier[];` to the `AppendixEntities` interface (after `exit_grids`).

- [ ] **Step 2: Add toggle state**

In `MapForgeSector.tsx`, beside the existing `showItems/showEntries/showExits` state, add:

```typescript
const [showSoldiers, setShowSoldiers] = useState(true);
```

- [ ] **Step 3: Add team-colored NPC markers to the SVG overlay**

In the appendix marker `<g>` (the `appendix && (() => { ... })()` block), add a soldiers layer. Use a small team→color map and draw a diamond (rect rotated) or circle per NPC; include a `<title>` for hover.

```tsx
{showSoldiers && appendix.soldiers.map((s, i) => {
  const { cx, cy } = c(s.x, s.y);
  const color = s.team === 1 ? "rgba(255,80,80,0.95)"      // enemy
    : s.team === 2 ? "rgba(120,255,120,0.95)"              // creature
    : s.team === 3 ? "rgba(80,220,255,0.95)"               // militia
    : s.team === 4 ? "rgba(120,160,255,0.95)"              // civilian
    : "rgba(240,240,240,0.95)";                            // player/other
  return (
    <circle key={`sol-${i}`} cx={cx} cy={cy} r={4}
      fill={color} stroke="rgba(0,0,0,0.7)" strokeWidth={1}
      vectorEffect="non-scaling-stroke">
      <title>{`${s.team_label} (dir ${s.facing}, class ${s.soldier_class})`}</title>
    </circle>
  );
})}
```

- [ ] **Step 4: Add the toggle control**

Next to the existing Items/Entries/Exits checkboxes, add:

```tsx
<label className="flex items-center gap-1 text-xs">
  <input type="checkbox" checked={showSoldiers} onChange={(e) => setShowSoldiers(e.target.checked)} />
  NPCs{appendix ? ` (${appendix.soldiers.length})` : ""}
</label>
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && node node_modules/typescript/bin/tsc --noEmit`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/mapforge.ts frontend/src/routes/MapForgeSector.tsx
git commit -m "$(printf 'MapForge overlay: team-colored NPC markers + toggle\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

- [ ] **Step 7: Manual verify (controller, after task review)**

Browser-dev recipe (`reference_mercforge_browserdev_verify`): open a light-less soldier map (A6 / A12 / A15 / C7) → NPCs toggle shows red enemy markers at sane positions; hover shows team. Confirm a lit map (A1/A2) shows the `lights_records` blocked note and no NPCs (expected). Capture a screenshot.

---

## Self-Review

**Spec coverage:**
- Soldier parse (52B v5 / 64B v7, fields per research) → Task 1. ✓
- Tail 99→100 fix → Task 1 Step 4c. ✓
- Detailed-placement bail → Task 1 (`data[pos]==1 → blocked("soldier_detailed")`). ✓
- team→label map → Task 1 `TEAM_LABELS`. ✓
- Count from tail ubNumIndividuals (u8 v5 / u16 v7) → Task 1 Step 4d. ✓
- Endpoint/model surface → Task 2. ✓
- Frontend markers + toggle (team-colored) → Task 3. ✓
- Real-map regression (A6, install-gated/skip) → Task 1 Step 6. ✓
- Never-throw (overrun guard + bail) → Task 1 (`soldier_records_overrun`). ✓
- Reachability limitation (lit maps still block at lights) → Global Constraints + Task 3 Step 7. ✓
- Out of scope (parse_dat_ext tail, lights records) → Global Constraints. ✓

**Placeholder scan:** no TBD/TODO; every code step complete. ✓

**Type consistency:** extractor `soldiers[i]` keys `{gridno,x,y,team,team_label,facing,soldier_class}` == `AppendixSoldier` (Python) == `AppendixSoldier` (TS); `**ents` already carries `soldiers`; endpoint test + real-map test assert these exact keys. The `_old_tail_99→_old_tail_100` rename updates its only caller (items test). ✓
