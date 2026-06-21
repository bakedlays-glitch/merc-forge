# MapForge Read-only Tactical Overlay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a read-only overlay of a sector's appendix entities (world items, map entry points, exit grids) on top of the MapForge canvas, so the user can SEE tactical data that today is invisible.

**Architecture:** A new sidecar module walks the `.dat` appendix region from raw bytes and returns positioned entity lists (`gridno → x,y`). A new `GET /mapforge/sessions/{id}/appendix` endpoint serves them. The frontend fetches them once per session and draws toggleable markers in the EXISTING SVG overlay layer. Nothing is written — no `.dat` serialization path is touched, so the B-phase data-safety gates do not apply.

**Tech Stack:** Python 3 (sidecar, FastAPI, `struct`); pytest; TypeScript + React (frontend); existing IsoRenderer iso-projection.

## Global Constraints

- **Scope = the parseable-today layers only:** world items (Path B / v5.0), map-info entry points, exit grids. Lights records, soldiers/NPCs, doors, edgepoints, schedules are DEFERRED to follow-on plans (they need engine-struct derivation). The extractor must reach them, mark `blocked_at`, and return cleanly — never throw.
- **Read-only.** No writes, no edits, no `.dat` serialization. The overlay reflects the ON-DISK appendix.
- **Appendix file order** (`worlddef.cpp` LoadWorld; already encoded in `parse_appendix_minimal` + `appendix_writer.py`): `items → ambient → lights → mapinfo(tail) → soldiers → exitgrids → doortable → edgepoints → schedules`.
- **gridno → tile:** `x = gridno % cols`, `y = gridno // cols` (cols = 160 for stock sectors). `gridno < 0` (NOWHERE = -1) means "absent" — skip it.
- **Reuse, don't re-derive bytes:** flag-bit constants and the 32-byte tail / 12-byte exit-grid layouts already live, source-verified, in `sidecar/mercwizard_core/mapforge_engine/appendix_writer.py`. Item record parsing already lives in `parse_world_items.py` (Path B returns `{gridno, level, usItem, count, fFlags, exists}`).
- **Test targets are deterministic crafted bytes** (no install dependency). Real-map smoke is manual: A1/A2 carry items; A6 reaches entry points.
- **Out of scope / known latent bug:** real modern (v7) maps with appendices may mis-locate `appendix_offset` (the `minor>=29 → 2-byte room` heuristic). v5.0 real maps and synthetic v7 maps are unaffected. Do NOT fix that here.
- **Venv:** run sidecar python as `./.venv/Scripts/python.exe` from `sidecar/` (the `.venv` lives only in the main checkout).
- **Commits:** end each message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Branch is `mapforge-tactical-overlay` (already created). Agent does not push.

---

### Task 1: Appendix extractor — world items

**Files:**
- Create: `sidecar/mercwizard_core/mapforge_engine/appendix_extract.py`
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Consumes: `parse_world_items(data, pos, count, major, minor, capture="summary") -> (items, new_pos, bail)` from `parse_world_items.py`; flag constants `MAP_*_SAVED` from `appendix_writer.py`.
- Produces: `extract_appendix_entities(data: bytes, parsed: dict) -> dict` returning keys `items`, `entry_points`, `exit_grids` (lists), `reached` (list[str]), `blocked_at` (str|None), `rows`, `cols`. `items[i] = {gridno, x, y, usItem, level}`.

- [ ] **Step 1: Write the failing test**

```python
# sidecar/tests/test_mapforge_appendix_extract.py
import struct
from mercwizard_core.mapforge_engine.appendix_extract import extract_appendix_entities
from mercwizard_core.mapforge_engine import appendix_writer as AW

COLS = 160

def _old_worlditem(gridno, usItem=264, level=0, exists=True):
    """One 52-byte OLD_WORLDITEM_101 (Path B). Offsets per parse_world_items:
    fExists@0, sGridNo@2 (int16), ubLevel@4, oldObject@8 (usItem@8), usFlags@44."""
    b = bytearray(52)
    b[0] = 1 if exists else 0
    struct.pack_into("<h", b, 2, gridno)
    b[4] = level
    struct.pack_into("<H", b, 8, usItem)
    return bytes(b)

def _old_tail_99(north=-1, east=-1, south=-1, west=-1, center=-1, isolated=-1):
    """99-byte _OLD_MAPCREATE_STRUCT (v<7). N/E/S/W int16 @0/2/4/6,
    center@12, isolated@14, padded to 99."""
    b = bytearray(b"\x00" * 99)
    struct.pack_into("<hhhh", b, 0, north, east, south, west)
    struct.pack_into("<hh", b, 12, center, isolated)
    return bytes(b)

def _parsed(flags, major=5.0, minor=25, cols=COLS, rows=160, off=0):
    return {"flags": flags, "major": major, "minor": minor,
            "cols": cols, "rows": rows, "appendix_offset": off}

def test_extracts_world_items_with_positions():
    data = struct.pack("<I", 2)                       # u32 item count
    data += _old_worlditem(gridno=12880, usItem=264)  # tile (80,80)
    data += _old_worlditem(gridno=1, usItem=99)       # tile (1,0)
    data += _old_tail_99()                            # unconditional tail (no entries)
    out = extract_appendix_entities(data, _parsed(AW.MAP_WORLDITEMS_SAVED))
    assert out["blocked_at"] is None
    assert "items" in out["reached"] and "mapinfo" in out["reached"]
    assert [(i["gridno"], i["x"], i["y"], i["usItem"]) for i in out["items"]] == [
        (12880, 80, 80, 264), (1, 1, 0, 99)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: FAIL — `ModuleNotFoundError: ...appendix_extract`.

- [ ] **Step 3: Write minimal implementation**

```python
# sidecar/mercwizard_core/mapforge_engine/appendix_extract.py
"""Read-only extraction of positioned appendix entities for the MapForge
tactical overlay. Walks the appendix region (never writes), returns entity
lists keyed by tile position. Scope: items + entry points + exit grids;
later sections (lights records, soldiers, doors, edgepoints) are marked
`blocked_at` and deferred. See docs/superpowers/specs/2026-06-20-mapforge-tactical-overlay-design.md.
"""
from __future__ import annotations

import struct
from typing import Any, Dict

from .parse_world_items import parse_world_items
from . import appendix_writer as AW


def _xy(gridno: int, cols: int) -> tuple[int, int]:
    return gridno % cols, gridno // cols


def extract_appendix_entities(data: bytes, parsed: Dict[str, Any]) -> Dict[str, Any]:
    flags = parsed["flags"]
    major = parsed["major"]
    minor = parsed["minor"]
    cols = parsed["cols"]
    rows = parsed["rows"]
    pos = parsed["appendix_offset"]
    n = len(data)

    out: Dict[str, Any] = {
        "items": [], "entry_points": [], "exit_grids": [],
        "reached": [], "blocked_at": None, "rows": rows, "cols": cols,
    }

    def blocked(reason: str) -> Dict[str, Any]:
        out["blocked_at"] = reason
        return out

    # 1. ITEMS
    if flags & AW.MAP_WORLDITEMS_SAVED:
        if pos + 4 > n:
            return blocked("items_count_truncated")
        count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        items, pos, bail = parse_world_items(data, pos, count, major, minor,
                                             capture="summary")
        for it in items:
            g = it["gridno"]
            if not it.get("exists", True) or g < 0:
                continue
            x, y = _xy(g, cols)
            out["items"].append({"gridno": g, "x": x, "y": y,
                                 "usItem": it["usItem"], "level": it["level"]})
        out["reached"].append("items")
        if bail:
            return blocked(bail)

    # 4. MAPINFO TAIL (unconditional) — entry points
    tail_size = 32 if major >= 7.0 else 99
    if pos + tail_size > n:
        return blocked("mapinfo_truncated")
    if major >= 7.0:
        north, east, south, west, center, isolated = struct.unpack_from("<6i", data, pos)
    else:
        north, east, south, west = struct.unpack_from("<4h", data, pos)
        center, isolated = struct.unpack_from("<2h", data, pos + 12)
    for kind, g in (("north", north), ("east", east), ("south", south),
                    ("west", west), ("center", center), ("isolated", isolated)):
        if g < 0:
            continue
        x, y = _xy(g, cols)
        out["entry_points"].append({"kind": kind, "gridno": g, "x": x, "y": y})
    pos += tail_size
    out["reached"].append("mapinfo")

    return out
```

> Note: ambient (step 2) / lights (step 3) / soldiers (step 5) / exit grids (step 6) are added in Tasks 2-3. This task intentionally jumps items → tail; the item test has no ambient/lights/soldier flags so the cursor reaches the tail directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/mapforge_engine/appendix_extract.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: appendix item extractor\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Extractor — ambient skip, lights header, entry points, soldier block

**Files:**
- Modify: `sidecar/mercwizard_core/mapforge_engine/appendix_extract.py`
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Produces: same `extract_appendix_entities` now traverses ambient (3B) and the lights HEADER (numColors + palette + count), and sets `blocked_at="lights_records"` when `light_count > 0`, `blocked_at="soldiers"` when the soldier flag is set after the tail. `reached` gains `"ambient"`, `"lights_header"`.

- [ ] **Step 1: Write the failing tests**

```python
# append to sidecar/tests/test_mapforge_appendix_extract.py

def test_modern_tail_entry_points():
    # flags=0: no items/lights/soldiers; 32-byte v7 tail only.
    data = AW.pack_map_tail(north=1000, east=2000, south=3000, west=4000,
                            center=5000, isolated=-1, map_version=31)
    out = extract_appendix_entities(data, _parsed(0, major=7.0, minor=31))
    assert out["blocked_at"] is None
    eps = {e["kind"]: (e["gridno"], e["x"], e["y"]) for e in out["entry_points"]}
    assert eps == {"north": (1000, 1000 % 160, 1000 // 160),
                   "east": (2000, 2000 % 160, 2000 // 160),
                   "south": (3000, 3000 % 160, 3000 // 160),
                   "west": (4000, 4000 % 160, 4000 // 160),
                   "center": (5000, 5000 % 160, 5000 // 160)}  # isolated=-1 dropped

def test_blocks_on_light_records():
    # flags=LIGHTS, header says 3 lights -> deferred.
    data = bytes([1]) + bytes(4) + struct.pack("<H", 3)   # numColors=1, 1 palette, count=3
    out = extract_appendix_entities(data, _parsed(AW.MAP_WORLDLIGHTS_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] == "lights_records"
    assert "lights_header" in out["reached"]

def test_blocks_on_soldiers_after_tail():
    # flags=SOLDIER: tail parses, then soldier block defers.
    data = AW.pack_map_tail(north=10, map_version=31)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] == "soldiers"
    assert "mapinfo" in out["reached"]
    assert out["entry_points"][0]["kind"] == "north"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: FAIL — `test_blocks_on_light_records` (no lights handling), `test_blocks_on_soldiers_after_tail` (no soldier block). `test_modern_tail_entry_points` PASSES already.

- [ ] **Step 3: Add the ambient/lights blocks before the tail, and the soldier block after it**

Insert between the ITEMS block and the MAPINFO TAIL block:

```python
    # 2. AMBIENT — fixed 3 bytes, no count.
    if flags & AW.MAP_AMBIENTLIGHTLEVEL_SAVED:
        if pos + 3 > n:
            return blocked("ambient_truncated")
        pos += 3
        out["reached"].append("ambient")

    # 3. LIGHTS — header is parseable; records deferred to a later plan.
    if flags & AW.MAP_WORLDLIGHTS_SAVED:
        if pos + 1 > n:
            return blocked("lights_header_truncated")
        num_colors = data[pos]
        pos += 1
        if pos + 4 * num_colors + 2 > n:
            return blocked("lights_palette_truncated")
        pos += 4 * num_colors
        light_count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        out["reached"].append("lights_header")
        if light_count > 0:
            return blocked("lights_records")
```

Insert immediately AFTER the `out["reached"].append("mapinfo")` line (before `return out`):

```python
    # 5. SOLDIERS — deferred to a later plan.
    if flags & AW.MAP_FULLSOLDIER_SAVED:
        return blocked("soldiers")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/mapforge_engine/appendix_extract.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: ambient/lights-header traversal + entry points + soldier block\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: Extractor — exit grids (post-soldier, no-soldier maps)

**Files:**
- Modify: `sidecar/mercwizard_core/mapforge_engine/appendix_extract.py`
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Produces: `extract_appendix_entities` now emits `exit_grids[i] = {gridno, x, y, dest_gridno, sx, sy, sz}` when the EXITGRID flag is set and no earlier section blocked. `reached` gains `"exitgrids"`. Exit-grid record = 12 bytes `<iiBBBx` (iMapIndex source gridno, usGridNo dest gridno, sx, sy, sz, +pad) per `appendix_writer.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to sidecar/tests/test_mapforge_appendix_extract.py

def test_exit_grids_after_tail_when_no_soldiers():
    # flags=EXITGRIDS only: tail (no entries) then 1 exit grid.
    data = AW.pack_map_tail(map_version=31)
    data += AW.pack_exit_grids([{"map_index": 12880, "grid_no": 13000,
                                 "sx": 9, "sy": 1, "sz": 0}])
    out = extract_appendix_entities(data, _parsed(AW.MAP_EXITGRIDS_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] is None
    assert "exitgrids" in out["reached"]
    assert out["exit_grids"] == [{"gridno": 12880, "x": 80, "y": 80,
                                  "dest_gridno": 13000, "sx": 9, "sy": 1, "sz": 0}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py::test_exit_grids_after_tail_when_no_soldiers -v`
Expected: FAIL — `exit_grids` empty (`exitgrids` not reached).

- [ ] **Step 3: Add the exit-grid block**

Insert immediately AFTER the soldier block (after `return blocked("soldiers")`), before `return out`:

```python
    # 6. EXIT GRIDS — uint16 count + 12-byte records (<iiBBBx).
    if flags & AW.MAP_EXITGRIDS_SAVED:
        if pos + 2 > n:
            return blocked("exitgrid_count_truncated")
        eg_count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        for _ in range(eg_count):
            if pos + 12 > n:
                return blocked("exitgrid_records_overrun")
            map_index, grid_no, sx, sy, sz = struct.unpack_from("<iiBBB", data, pos)
            pos += 12
            x, y = _xy(map_index, cols)
            out["exit_grids"].append({"gridno": map_index, "x": x, "y": y,
                                      "dest_gridno": grid_no, "sx": sx, "sy": sy, "sz": sz})
        out["reached"].append("exitgrids")
```

- [ ] **Step 4: Run the FULL extractor test file**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/mapforge_engine/appendix_extract.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: exit-grid extraction\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 4: API endpoint + response model

**Files:**
- Modify: `sidecar/routes/mapforge.py` (add model near `ParsedSector`; add route near `session_parsed`, ~line 5841)
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Consumes: `extract_appendix_entities` (Task 1-3); `MapForgeSession` (slots `id`, `parsed`, `original_bytes`); `_session_store`.
- Produces: `GET /mapforge/sessions/{session_id}/appendix` → `AppendixEntities` JSON. Fields: `session_id, rows, cols, items, entry_points, exit_grids, reached, blocked_at`.

- [ ] **Step 1: Write the failing test**

```python
# append to sidecar/tests/test_mapforge_appendix_extract.py
import struct as _struct
from routes.mapforge import MapForgeSession, _session_store, session_appendix
from mercwizard_core.mapforge_engine import appendix_writer as _AW

def _fake_session(data, parsed):
    """Build a MapForgeSession without touching disk (bypass __init__)."""
    s = object.__new__(MapForgeSession)
    s.id = "testsess123456"
    s.original_bytes = data
    s.parsed = parsed
    return s

def test_appendix_endpoint_returns_entities():
    data = _AW.pack_map_tail(north=1000, map_version=31)
    data += _AW.pack_exit_grids([{"map_index": 320, "grid_no": 9, "sx": 1, "sy": 2, "sz": 0}])
    parsed = {"flags": _AW.MAP_EXITGRIDS_SAVED, "major": 7.0, "minor": 31,
              "cols": 160, "rows": 160, "appendix_offset": 0}
    sess = _fake_session(data, parsed)
    _session_store._sessions[sess.id] = sess
    try:
        res = session_appendix(sess.id)
    finally:
        del _session_store._sessions[sess.id]
    assert res.session_id == sess.id
    assert res.blocked_at is None
    assert res.entry_points[0].kind == "north"
    assert res.exit_grids[0].gridno == 320 and res.exit_grids[0].y == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py::test_appendix_endpoint_returns_entities -v`
Expected: FAIL — `ImportError: cannot import name 'session_appendix'`.

- [ ] **Step 3: Add the pydantic models + route**

Add near the other response models (e.g. just above `def _serialize_layer`, ~line 5832). The import for the extractor goes at the top of the file with the other `mapforge_engine` imports:

```python
# top of file, with the existing mapforge_engine imports:
from mercwizard_core.mapforge_engine.appendix_extract import extract_appendix_entities
```

```python
class AppendixItem(BaseModel):
    gridno: int
    x: int
    y: int
    usItem: int
    level: int

class AppendixEntryPoint(BaseModel):
    kind: str
    gridno: int
    x: int
    y: int

class AppendixExitGrid(BaseModel):
    gridno: int
    x: int
    y: int
    dest_gridno: int
    sx: int
    sy: int
    sz: int

class AppendixEntities(BaseModel):
    session_id: str
    rows: int
    cols: int
    items: list[AppendixItem]
    entry_points: list[AppendixEntryPoint]
    exit_grids: list[AppendixExitGrid]
    reached: list[str]
    blocked_at: str | None
```

Add the route next to `session_parsed` (~line 5841). It does NOT call `_require_renderer()` — it returns pure appendix data, no atlas needed.

```python
@router.get("/sessions/{session_id}/appendix", response_model=AppendixEntities)
def session_appendix(session_id: str):
    """Read-only positioned appendix entities (items / entry points / exit
    grids) for the tactical overlay. Extracted from the on-disk bytes; never
    written. Later sections (lights records, soldiers, doors, edgepoints)
    report via `blocked_at` until their parsers land."""
    sess = _session_store.get(session_id)
    ents = extract_appendix_entities(sess.original_bytes, sess.parsed)
    return AppendixEntities(session_id=sess.id, **ents)
```

> `extract_appendix_entities` returns exactly the keys `AppendixEntities` needs minus `session_id` (`rows, cols, items, entry_points, exit_grids, reached, blocked_at`), so `**ents` maps cleanly. Pydantic coerces the inner dicts into the item/entry/exit models.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS (all 6).

- [ ] **Step 5: Run the full mapforge suite to confirm no regression**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/ -q -k mapforge`
Expected: PASS (existing appendix/roundtrip tests still green — the writer is untouched).

- [ ] **Step 6: Commit**

```bash
git add sidecar/routes/mapforge.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: appendix entities endpoint + models\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 5: Frontend API client

**Files:**
- Modify: `frontend/src/lib/mapforge.ts` (add types + fetch near `getSessionParsed`, ~line 1777)

**Interfaces:**
- Consumes: `jsonGet<T>(path)` (mapforge.ts:96).
- Produces: `getSessionAppendix(sessionId: string): Promise<AppendixEntities>` and exported interfaces `AppendixItem`, `AppendixEntryPoint`, `AppendixExitGrid`, `AppendixEntities`.

- [ ] **Step 1: Add the types + fetch function**

Add immediately after `getSessionParsed` (~line 1787):

```typescript
export interface AppendixItem {
  gridno: number; x: number; y: number; usItem: number; level: number;
}
export interface AppendixEntryPoint {
  kind: string; gridno: number; x: number; y: number;
}
export interface AppendixExitGrid {
  gridno: number; x: number; y: number;
  dest_gridno: number; sx: number; sy: number; sz: number;
}
export interface AppendixEntities {
  session_id: string;
  rows: number;
  cols: number;
  items: AppendixItem[];
  entry_points: AppendixEntryPoint[];
  exit_grids: AppendixExitGrid[];
  reached: string[];
  blocked_at: string | null;
}

/** Read-only appendix entities (items / entry points / exit grids) for the
 * tactical overlay. Fetched once per session; the appendix is the on-disk
 * tactical layer (MapForge cannot edit it). */
export function getSessionAppendix(sessionId: string): Promise<AppendixEntities> {
  return jsonGet<AppendixEntities>(
    `/mapforge/sessions/${encodeURIComponent(sessionId)}/appendix`,
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && node node_modules/typescript/bin/tsc --noEmit`
Expected: exit 0 (no errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/mapforge.ts
git commit -m "$(printf 'MapForge overlay: frontend appendix API client\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 6: Frontend overlay markers + toggles

**Files:**
- Modify: `frontend/src/routes/MapForgeSector.tsx` (state + fetch effect near the parsed-load effect ~line 1581; toggle UI; markers inside the SVG overlay ~line 5334)

**Interfaces:**
- Consumes: `getSessionAppendix`, `AppendixEntities` (Task 5); `tileToCanvasPixel(x, y, meta)` from `../lib/IsoRenderer`; `renderMeta` state; `session.id`.
- Produces: a marker layer (items / entry points / exit grids) drawn in the existing SVG overlay, gated by three boolean toggles.

- [ ] **Step 1: Add appendix state + a fetch effect**

Near the other session state (e.g. beside `const [renderMeta, setRenderMeta] = useState<RenderMeta | null>(null);`, ~line 909) add:

```typescript
const [appendix, setAppendix] = useState<AppendixEntities | null>(null);
const [showItems, setShowItems] = useState(false);
const [showEntries, setShowEntries] = useState(true);
const [showExits, setShowExits] = useState(true);
```

Add the import at the top with the other `../lib/mapforge` imports:

```typescript
import { getSessionAppendix, type AppendixEntities } from "../lib/mapforge";
```

Add a fetch effect (place after the parsed-load effect):

```typescript
// Read-only tactical appendix (items / entry points / exit grids).
// Fetched once per session; the overlay is purely visual.
useEffect(() => {
  setAppendix(null);
  if (!session) return;
  let cancelled = false;
  getSessionAppendix(session.id)
    .then((a) => { if (!cancelled) setAppendix(a); })
    .catch(() => { if (!cancelled) setAppendix(null); });
  return () => { cancelled = true; };
}, [session]);
```

- [ ] **Step 2: Add the marker `<g>` to the SVG overlay**

Inside the `<svg className="pointer-events-none absolute inset-0 z-10" ...>` block (~line 5334), after the existing `previewPath`/`heightOverlay` children, add a self-invoking marker block. `tileToCanvasPixel` returns the tile's top-left in canvas space; add half-tile for the center.

```tsx
{appendix && (() => {
  const c = (x: number, y: number) => {
    const p = tileToCanvasPixel(x, y, meta);
    return { cx: p.x + meta.tileW / 2, cy: p.y + meta.tileH / 2 };
  };
  return (
    <g>
      {showExits && appendix.exit_grids.map((e, i) => {
        const { cx, cy } = c(e.x, e.y);
        return <rect key={`xg-${i}`} x={cx - 5} y={cy - 5} width={10} height={10}
          fill="rgba(120,200,255,0.35)" stroke="rgba(150,220,255,0.95)"
          strokeWidth={1} vectorEffect="non-scaling-stroke" />;
      })}
      {showEntries && appendix.entry_points.map((e, i) => {
        const { cx, cy } = c(e.x, e.y);
        return <circle key={`ep-${i}`} cx={cx} cy={cy} r={5}
          fill="rgba(255,205,80,0.45)" stroke="rgba(255,225,120,0.95)"
          strokeWidth={1.5} vectorEffect="non-scaling-stroke" />;
      })}
      {showItems && appendix.items.map((it, i) => {
        const { cx, cy } = c(it.x, it.y);
        return <circle key={`it-${i}`} cx={cx} cy={cy} r={3}
          fill="rgba(120,255,160,0.9)" stroke="rgba(40,160,80,0.9)"
          strokeWidth={1} vectorEffect="non-scaling-stroke" />;
      })}
    </g>
  );
})()}
```

Confirm `tileToCanvasPixel` is imported in this file (it is used elsewhere; if not, add it to the `../lib/IsoRenderer` import).

- [ ] **Step 3: Add the toggle controls**

Find an existing on-canvas control cluster (the same row as the grid/debug toggles) and add three checkboxes. Use the existing control styling; minimal version:

```tsx
<label className="flex items-center gap-1 text-xs">
  <input type="checkbox" checked={showItems} onChange={(e) => setShowItems(e.target.checked)} />
  Items{appendix ? ` (${appendix.items.length})` : ""}
</label>
<label className="flex items-center gap-1 text-xs">
  <input type="checkbox" checked={showEntries} onChange={(e) => setShowEntries(e.target.checked)} />
  Entries{appendix ? ` (${appendix.entry_points.length})` : ""}
</label>
<label className="flex items-center gap-1 text-xs">
  <input type="checkbox" checked={showExits} onChange={(e) => setShowExits(e.target.checked)} />
  Exits{appendix ? ` (${appendix.exit_grids.length})` : ""}
</label>
```

If `appendix?.blocked_at` is set, show a one-line muted note so the user knows a layer is unparsed (e.g. soldiers): render `appendix.blocked_at && <span className="text-xs text-amber-400">layer “{appendix.blocked_at}” not yet shown</span>`.

- [ ] **Step 4: Typecheck**

Run: `cd frontend && node node_modules/typescript/bin/tsc --noEmit`
Expected: exit 0.

- [ ] **Step 5: Manual verify (browser-dev recipe)**

Per `reference_mercforge_browserdev_verify`: start the sidecar (`./.venv/Scripts/python.exe -m mercwizard_core.main --port 8000` from `sidecar/`), POST `/installs/active`, run `npm run dev` (frontend, port 1420), open a sector with Playwright. Verify:
- Open **A1** (or A2): toggle **Items** → at least one green item dot appears at the correct tile.
- Open **A6**: **Entries** shows gold entry-point circles; the `blocked_at: soldiers` note appears (A6 has the SOLDIER flag, so post-tail layers are deferred — expected).
- Markers track pan/zoom (they live inside the transformed canvas wrapper).

Capture a screenshot for the PR.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/MapForgeSector.tsx
git commit -m "$(printf 'MapForge overlay: tactical marker layer + toggles\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Self-Review

**Spec coverage:**
- Read-only parse-extract (sidecar) → Tasks 1-4. ✓
- Frontend marker overlay + toggles → Tasks 5-6. ✓
- `GET /…/appendix` endpoint → Task 4. ✓
- gridno→x,y (`%cols` / `//cols`) → Task 1 `_xy`. ✓
- Graceful degradation / `blocked_at`, never throw → Tasks 1-3 (`blocked()` returns, no raise). ✓
- §2 byte sizes reused from `appendix_writer.py` (tail 32/99, exit grid 12) + `parse_world_items` (52) → Tasks 1-3. ✓
- Build order (items+entries+exits now; lights/soldiers/doors/edges deferred) → Global Constraints + `blocked_at`. ✓
- Test on real `0x17D` sectors → Task 6 manual (A1 items, A6 entries). ✓
- B0 round-trip stays green (writer untouched) → Task 4 Step 5. ✓
- DEFERRED (own follow-on plans, called out in Global Constraints): lights-record positions, soldiers/NPCs, doors, edgepoints, schedules, A9 data recovery, the v7 room-size `appendix_offset` latent bug.

**Placeholder scan:** no TBD/TODO; every code step is complete. ✓

**Type consistency:** extractor dict keys (`items/entry_points/exit_grids/reached/blocked_at/rows/cols`) match `AppendixEntities` (`**ents` + `session_id`); per-entity dict keys match the pydantic inner models and the TS interfaces (`gridno/x/y/usItem/level`, `kind/gridno/x/y`, `gridno/x/y/dest_gridno/sx/sy/sz`); `getSessionAppendix` return type matches Task 4's `response_model`. ✓
