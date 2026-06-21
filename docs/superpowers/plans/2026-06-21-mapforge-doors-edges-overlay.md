# MapForge Doors + Edgepoints Overlay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extend the MapForge read-only appendix overlay to parse + render the **door table** and **edgepoints** sections as toggleable markers.

**Architecture:** Append two sections to the existing `extract_appendix_entities` walker (after the exit-grid block, in appendix file order), surface them through the endpoint model, and draw markers in the existing SVG overlay. Still read-only — no `.dat` write path.

**Tech Stack:** Python 3 (`struct`), FastAPI, pytest; TypeScript + React.

## Global Constraints

- Layout source-of-truth (validated byte-exact on A2/A6/C7): `docs/superpowers/specs/2026-06-21-doors-edgepoints-research.md`.
- Appendix order: `… → soldiers → exit grids → DOOR TABLE → EDGEPOINTS → schedules`. The walker already parses through exit grids; doors come next, then edgepoints. Schedules is OUT OF SCOPE (stop after edgepoints).
- **Door table** = `uint8 count` + `count × 14-byte _OLD_DOOR` records. Per record: `sGridNo` INT16 @0, `fLocked` (BOOLEAN/u8) @2. (The old "u16 count + 10B record" assumption is WRONG — verified: A2 has 3 doors; 14B yields valid gridnos 18953/15621/18475, 12B desyncs to garbage.)
- **Edgepoints** = 8 sub-sections (primary N/E/S/W, then secondary N/E/S/W). Each = `uint16 size` + `uint16 middle` + `size × element`, where element is a gridno: **INT16 for major<7.0**, INT32 for major>=7.0. (On-disk width for stock v5 maps is INT16 — INT32 overruns instantly. The engine's INT16-write/INT32-read inconsistency only affects the regenerated `ubMapVersion<17` path, which our `ubMapVersion=25` maps never take.)
- gridno → tile: `x = gridno % cols`, `y = gridno // cols`; `gridno < 0` skipped.
- Read-only, NEVER throw: on any truncation set `blocked_at` and return what's reached.
- Validated on the vanilla v5 path (major=5.0/minor=25). The major>=7.0 element width (INT32) is derivation-only (no stock v7 map has these sections) — implement it but it's unvalidated, consistent with the soldier/lights modern paths.
- Out of scope: schedules; the pre-v17 UINT8 edgepoint encoding (no such map in the corpus); `parse_dat_ext.py`'s separate copy of these (its own latent bug).
- Reuse flag constants `AW.MAP_DOORTABLE_SAVED` / `AW.MAP_EDGEPOINTS_SAVED` from `appendix_writer.py`.
- Venv: `./.venv/Scripts/python.exe` from `sidecar/`. Frontend gate: `tsc --noEmit` exit 0.
- Commits end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Branch `mapforge-doors-edges`; USER pushes.

---

### Task 1: Extractor — door table + edgepoints

**Files:**
- Modify: `sidecar/mercwizard_core/mapforge_engine/appendix_extract.py`
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Produces: `extract_appendix_entities` now emits `out["doors"]` (`{gridno,x,y,locked}`) and `out["edgepoints"]` (`{gridno,x,y,edge}`), and adds `"doortable"` / `"edgepoints"` to `reached`. The out dict gains `doors` + `edgepoints` keys (default `[]`).

- [ ] **Step 1: Write the failing tests**

Add a door/edge helper + tests (reuse the existing `_old_tail_100`, `_old_soldier`, `_parsed`, `AW`):

```python
def _door_record(gridno, locked=1):
    """14-byte _OLD_DOOR. sGridNo @0 (int16), fLocked @2 (u8)."""
    b = bytearray(14)
    struct.pack_into("<h", b, 0, gridno)
    b[2] = locked
    return bytes(b)

def _edge_section(gridnos, middle=0):
    """One edgepoint sub-section: u16 size + u16 middle + size*int16."""
    b = bytearray(struct.pack("<HH", len(gridnos), middle))
    for g in gridnos:
        b += struct.pack("<h", g)
    return bytes(b)

def test_extracts_doors():
    # flags=DOOR only: tail(100) -> (no soldiers) -> (no exitgrids) -> door table.
    data = _old_tail_100()
    data += bytes([2])                       # uint8 door count
    data += _door_record(12880, locked=1)    # (80,80) locked
    data += _door_record(160, locked=0)      # (0,1) unlocked
    out = extract_appendix_entities(data, _parsed(AW.MAP_DOORTABLE_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert "doortable" in out["reached"]
    assert [(d["gridno"], d["x"], d["y"], d["locked"]) for d in out["doors"]] == [
        (12880, 80, 80, True), (160, 0, 1, False)]

def test_extracts_edgepoints():
    # flags=EDGE only: tail(100) -> 8 edge sub-sections (only 1st north populated).
    data = _old_tail_100()
    data += _edge_section([100, 260])        # primary north: 2 entry tiles
    for _ in range(7):
        data += _edge_section([])            # remaining 7 sections empty
    out = extract_appendix_entities(data, _parsed(AW.MAP_EDGEPOINTS_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert "edgepoints" in out["reached"]
    assert [(e["gridno"], e["x"], e["y"], e["edge"]) for e in out["edgepoints"]] == [
        (100, 100, 0, "north"), (260, 100, 1, "north")]

def test_doortable_overrun_degrades():
    data = _old_tail_100()
    data += bytes([2]) + _door_record(100)   # count says 2, only 1 record
    out = extract_appendix_entities(data, _parsed(AW.MAP_DOORTABLE_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] == "doortable_records_overrun"
    assert [d["gridno"] for d in out["doors"]] == [100]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: the 3 new tests FAIL (no `doors`/`edgepoints` keys).

- [ ] **Step 3: Implement the door + edgepoint blocks**

Add `"doors": [], "edgepoints": [],` to the `out` dict initializer (alongside `exit_grids`/`soldiers`).

Insert AFTER the exit-grid block (after `out["reached"].append("exitgrids")`), before `return out`:

```python
    # 7. DOOR TABLE — uint8 count + 14-byte _OLD_DOOR records.
    if flags & AW.MAP_DOORTABLE_SAVED:
        if pos + 1 > n:
            return blocked("doortable_count_truncated")
        dt_count = data[pos]
        pos += 1
        for _ in range(dt_count):
            if pos + 14 > n:
                return blocked("doortable_records_overrun")
            g = struct.unpack_from("<h", data, pos)[0]
            locked = data[pos + 2]
            pos += 14
            if g < 0:
                continue
            x, y = _xy(g, cols)
            out["doors"].append({"gridno": g, "x": x, "y": y, "locked": bool(locked)})
        out["reached"].append("doortable")

    # 8. EDGEPOINTS — 8 sub-sections (primary N/E/S/W, secondary N/E/S/W).
    # Each: uint16 size + uint16 middle + size * gridno (INT16 v<7 / INT32 v>=7).
    if flags & AW.MAP_EDGEPOINTS_SAVED:
        elem_fmt = "<h" if major < 7.0 else "<i"
        elem_size = 2 if major < 7.0 else 4
        edge_names = ["north", "east", "south", "west",
                      "north2", "east2", "south2", "west2"]
        for si in range(8):
            if pos + 4 > n:
                return blocked("edgepoint_header_truncated")
            size = struct.unpack_from("<H", data, pos)[0]
            pos += 4  # uint16 size + uint16 middle (middle ignored)
            for _ in range(size):
                if pos + elem_size > n:
                    return blocked("edgepoint_records_overrun")
                g = struct.unpack_from(elem_fmt, data, pos)[0]
                pos += elem_size
                if g < 0:
                    continue
                x, y = _xy(g, cols)
                out["edgepoints"].append({"gridno": g, "x": x, "y": y,
                                          "edge": edge_names[si]})
        out["reached"].append("edgepoints")
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS.

- [ ] **Step 5: Add real-map regression (install-gated)**

Append (reuse the install-gate pattern already in the file):

```python
@pytest.mark.skipif(not os.path.exists(_A6), reason="canonical install not present")
def test_real_a2_doors_and_edges():
    a2 = _A6.replace("A6.DAT", "A2.DAT")
    with open(a2, "rb") as f:
        data = f.read()
    out = extract_appendix_entities(data, parse_dat_full(data))
    assert out["blocked_at"] is None
    assert len(out["doors"]) == 3                         # A2 has 3 doors
    assert all(0 <= d["gridno"] < 25600 for d in out["doors"])
    assert all(d["locked"] for d in out["doors"])          # all 3 are locked
    assert len(out["edgepoints"]) > 0
    assert all(0 <= e["gridno"] < 25600 for e in out["edgepoints"])
    assert "doortable" in out["reached"] and "edgepoints" in out["reached"]
```

(`_A6` is the existing module constant for the canonical A6.DAT path; if it's named differently, reuse whatever the existing real-map tests use.)

- [ ] **Step 6: Run focused + full mapforge suite**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Then: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/ -q -k mapforge`
Expected: PASS, no regression.

- [ ] **Step 7: Commit**

```bash
git add sidecar/mercwizard_core/mapforge_engine/appendix_extract.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: door table + edgepoint extraction\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Endpoint models — doors + edgepoints

**Files:**
- Modify: `sidecar/routes/mapforge.py`
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Produces: `AppendixDoor` (`gridno,x,y: int; locked: bool`) + `AppendixEdgepoint` (`gridno,x,y: int; edge: str`) models + `doors: list[AppendixDoor]` / `edgepoints: list[AppendixEdgepoint]` fields on `AppendixEntities`. The route already splats `**ents`, which now carries `doors`/`edgepoints`.

- [ ] **Step 1: Write the failing test**

```python
def test_appendix_endpoint_returns_doors_and_edges():
    data = _old_tail_100()
    data += bytes([1]) + _door_record(320, locked=1)   # 1 door (note: flags include DOOR+EDGE)
    data += _edge_section([200])                        # north
    for _ in range(7):
        data += _edge_section([])
    parsed = {"flags": AW.MAP_DOORTABLE_SAVED | AW.MAP_EDGEPOINTS_SAVED,
              "major": 5.0, "minor": 25, "cols": 160, "rows": 160, "appendix_offset": 0}
    sess = _fake_session(data, parsed)
    _session_store._sessions[sess.id] = sess
    try:
        res = session_appendix(sess.id)
    finally:
        del _session_store._sessions[sess.id]
    assert len(res.doors) == 1 and res.doors[0].gridno == 320 and res.doors[0].locked is True
    assert len(res.edgepoints) == 1 and res.edgepoints[0].edge == "north"
```

> Note the soldier section is absent (flags have no SOLDIER bit), so after the tail the walker goes straight to exit-grids (skipped, no flag) → doors → edges. The 100-byte tail's `ubNumIndividuals` defaults to 0, so even with no SOLDIER flag the soldier loop is not entered.

- [ ] **Step 2: Run to verify it fails**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py::test_appendix_endpoint_returns_doors_and_edges -v`
Expected: FAIL — `AppendixEntities` has no `doors`/`edgepoints`.

- [ ] **Step 3: Add the models + fields**

Next to `AppendixExitGrid`/`AppendixSoldier`/`AppendixLight` in `routes/mapforge.py`:

```python
class AppendixDoor(BaseModel):
    gridno: int
    x: int
    y: int
    locked: bool

class AppendixEdgepoint(BaseModel):
    gridno: int
    x: int
    y: int
    edge: str
```

Add to `AppendixEntities` (after `lights`):

```python
    doors: list[AppendixDoor]
    edgepoints: list[AppendixEdgepoint]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS.

- [ ] **Step 5: Run the mapforge suite**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/ -q -k mapforge`
Expected: PASS, no regression.

- [ ] **Step 6: Commit**

```bash
git add sidecar/routes/mapforge.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: doors + edgepoints in endpoint model\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: Frontend — door + edgepoint markers + toggles

**Files:**
- Modify: `frontend/src/lib/mapforge.ts`
- Modify: `frontend/src/routes/MapForgeSector.tsx`

**Interfaces:**
- Consumes: `AppendixEntities` (now with `doors`/`edgepoints`); `tileToCanvasPixel`; `renderMeta`.
- Produces: `AppendixDoor`/`AppendixEdgepoint` TS interfaces + fields; `showDoors`/`showEdges` toggles; door + edge markers in the SVG overlay.

- [ ] **Step 1: TS types**

In `mapforge.ts`, near the other `Appendix*` interfaces:

```typescript
export interface AppendixDoor {
  gridno: number; x: number; y: number; locked: boolean;
}
export interface AppendixEdgepoint {
  gridno: number; x: number; y: number; edge: string;
}
```
Add `doors: AppendixDoor[];` and `edgepoints: AppendixEdgepoint[];` to `AppendixEntities` (after `lights`).

- [ ] **Step 2: Toggle state**

In `MapForgeSector.tsx`, beside `showLights`/`showSoldiers` etc:

```typescript
const [showDoors, setShowDoors] = useState(false);
const [showEdges, setShowEdges] = useState(false);
```
Thread BOTH through the overlay component the SAME way `showLights`/`showSoldiers` are threaded (find the call site, destructure, and prop-type block — a broken thread = markers silently never render).

- [ ] **Step 3: Markers in the SVG overlay**

In the appendix marker block, using the same `c(x,y)` center helper:

```tsx
{showDoors && appendix.doors.map((d, i) => {
  const { cx, cy } = c(d.x, d.y);
  return <rect key={`dr-${i}`} x={cx - 4} y={cy - 4} width={8} height={8}
    fill={d.locked ? "rgba(200,120,255,0.85)" : "rgba(200,180,255,0.6)"}
    stroke="rgba(120,40,200,0.9)" strokeWidth={1} vectorEffect="non-scaling-stroke">
    <title>{d.locked ? "locked door" : "door"}</title>
  </rect>;
})}
{showEdges && appendix.edgepoints.map((e, i) => {
  const { cx, cy } = c(e.x, e.y);
  return <circle key={`eg-${i}`} cx={cx} cy={cy} r={2}
    fill="rgba(160,160,160,0.7)" stroke="none">
    <title>{`edge: ${e.edge}`}</title>
  </circle>;
})}
```

- [ ] **Step 4: Toggle checkboxes**

Beside the existing toggles:

```tsx
<label className="flex items-center gap-1 text-xs">
  <input type="checkbox" checked={showDoors} onChange={(e) => setShowDoors(e.target.checked)} />
  Doors{appendix ? ` (${appendix.doors.length})` : ""}
</label>
<label className="flex items-center gap-1 text-xs">
  <input type="checkbox" checked={showEdges} onChange={(e) => setShowEdges(e.target.checked)} />
  Edges{appendix ? ` (${appendix.edgepoints.length})` : ""}
</label>
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && node node_modules/typescript/bin/tsc --noEmit`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/mapforge.ts frontend/src/routes/MapForgeSector.tsx
git commit -m "$(printf 'MapForge overlay: door + edgepoint markers + toggles\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

- [ ] **Step 7: Manual verify (controller)**

Browser-dev recipe: open A2 → Doors toggle shows 3 purple door markers (locked); Edges shows the perimeter entry tiles. Confirm pan/zoom tracks.

---

## Self-Review

**Spec coverage:** door table (u8 count + 14B, gridno@0/locked@2) → Task 1; edgepoints (8 sections, INT16 v5/INT32 v7) → Task 1; models → Task 2; frontend markers + toggles → Task 3; real-map A2 regression (3 doors + edges) → Task 1 Step 5; never-throw (overrun guards) → Task 1; v7 element width derivation-only → Global Constraints. ✓
**Placeholder scan:** none. ✓
**Type consistency:** extractor `doors`{gridno,x,y,locked} / `edgepoints`{gridno,x,y,edge} == pydantic == TS; `**ents` carries both; toggles threaded like the prior `showLights`. ✓
