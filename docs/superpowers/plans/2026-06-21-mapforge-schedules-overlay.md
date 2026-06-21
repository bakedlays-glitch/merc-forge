# MapForge Schedules Overlay (minimal) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Parse the NPC schedules appendix section (the last section) and render schedule waypoint gridnos as toggleable markers — completing the read-only tactical overlay.

**Architecture:** Append a schedules block to `extract_appendix_entities` (after edgepoints), surface via the endpoint model, render markers (toggle default OFF, since the data is sparse). Read-only — no `.dat` write path.

**Tech Stack:** Python 3 (`struct`), FastAPI, pytest; TS + React.

## Global Constraints

- Layout (validated byte-exact on A2/C6/A10/C5): `docs/superpowers/specs/2026-06-21-schedules-research.md`.
- Schedules sit LAST: `… → edgepoints → SCHEDULES → (editor trailing data, ignored) → EOF`. Gated by `MAP_NPCSCHEDULES_SAVED = 0x100`.
- **Section = `uint8 count` + `count × 36-byte` records (major<7.0 `_OLD_SCHEDULENODE`).** v7 record = 52B (7.0–8.0) / 56B (≥8.0) — DERIVATION-ONLY (no stock v7 map); for v7, ADVANCE by the record size but do NOT extract waypoints (offsets unvalidated).
- 36B record field offsets (v5): `usData1[4]` @12 (4× UINT16 — the waypoint gridnos), `ubAction[4]` @28 (4× UINT8), `ubScheduleID` @32 (UINT8 — the schedule id / soldier link). (Do NOT use the on-disk `ubSoldierID` @33 — it's stale junk = 156.)
- **Plot rule:** for each of the 4 slots, emit `usData1[j]` as a waypoint **iff `0 < usData1[j] < rows*cols`** (a valid in-map gridno; this naturally excludes `0` and `0xFFFF`=NOWHERE). Tag each with `schedule_id` + `action` (= `ubAction[j]`). This is the validated-clean filter on A2 (yields exactly the real sleep/gridno waypoints).
- gridno → tile: `x = g % cols`, `y = g // cols`.
- Read-only, never throw: on truncation set `blocked_at` and return reached.
- Validated on the v5 path. Sparse data is expected (most maps = 0 schedules); the toggle default is OFF.
- Reuse `AW.MAP_NPCSCHEDULES_SAVED`. Out of scope: soldier attribution (needs the skipped 1040B detailed block), the v7 field offsets.
- Venv: `./.venv/Scripts/python.exe` from `sidecar/`. Frontend gate: `tsc --noEmit` exit 0.
- Commits end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Branch `mapforge-schedules`; USER pushes.

---

### Task 1: Extractor + endpoint model

**Files:**
- Modify: `sidecar/mercwizard_core/mapforge_engine/appendix_extract.py`
- Modify: `sidecar/routes/mapforge.py`
- Test: `sidecar/tests/test_mapforge_appendix_extract.py`

**Interfaces:**
- Produces: `extract_appendix_entities` emits `out["schedules"]` (`{gridno,x,y,schedule_id,action}`), adds `"schedules"` to `reached`. `AppendixSchedule` model + `schedules: list[AppendixSchedule]` on `AppendixEntities`.

- [ ] **Step 1: Write the failing tests**

```python
def _schedule_record(usdata1, actions, schedule_id=1):
    """36-byte _OLD_SCHEDULENODE (v5). next@0(4), usTime[4]@4(8), usData1[4]@12(8),
    usData2[4]@20(8), ubAction[4]@28(4), ubScheduleID@32, ubSoldierID@33, usFlags@34(2).
    usdata1/actions are length-4 lists."""
    b = bytearray(36)
    for j in range(4):
        struct.pack_into("<H", b, 12 + 2 * j, usdata1[j] & 0xFFFF)
        b[28 + j] = actions[j] & 0xFF
    b[32] = schedule_id & 0xFF
    return bytes(b)

def test_extracts_schedule_waypoints():
    # flags=SCHED only: tail(100) -> (no soldiers/exit/door/edge) -> schedules.
    data = _old_tail_100()
    data += bytes([2])   # uint8 schedule count
    # schedule 1: two real waypoints (gridno 100 action 5, gridno 260 action 9), two empty.
    data += _schedule_record([100, 260, 0xFFFF, 0], [5, 9, 8, 0], schedule_id=1)
    # schedule 2: one waypoint (gridno 320 action 5).
    data += _schedule_record([320, 0, 0xFFFF, 0xFFFF], [5, 0, 8, 8], schedule_id=2)
    out = extract_appendix_entities(data, _parsed(AW.MAP_NPCSCHEDULES_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert "schedules" in out["reached"]
    assert [(s["gridno"], s["x"], s["y"], s["schedule_id"], s["action"]) for s in out["schedules"]] == [
        (100, 100, 0, 1, 5), (260, 100, 1, 1, 9), (320, 0, 2, 2, 5)]

def test_schedules_overrun_degrades():
    data = _old_tail_100()
    data += bytes([2]) + _schedule_record([100, 0, 0, 0], [5, 0, 0, 0])  # count 2, only 1 record
    out = extract_appendix_entities(data, _parsed(AW.MAP_NPCSCHEDULES_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] == "schedules_records_overrun"
    assert [s["gridno"] for s in out["schedules"]] == [100]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: the 2 new tests FAIL (no `schedules` key).

- [ ] **Step 3: Implement the schedules block**

Add `"schedules": [],` to the `out` dict initializer.

Insert AFTER the edgepoint block (after `out["reached"].append("edgepoints")`), before `return out`:

```python
    # 9. SCHEDULES — uint8 count + 36-byte _OLD_SCHEDULENODE (major<7.0).
    # Plot usData1[j] waypoint gridnos (valid in-map only). v7 records (52/56B)
    # are advanced-only (field offsets derivation-only — no stock v7 map).
    if flags & AW.MAP_NPCSCHEDULES_SAVED:
        rec_size = 36 if major < 7.0 else (52 if major < 8.0 else 56)
        if pos + 1 > n:
            return blocked("schedules_count_truncated")
        sc_count = data[pos]
        pos += 1
        world_max = rows * cols
        for _ in range(sc_count):
            if pos + rec_size > n:
                return blocked("schedules_records_overrun")
            if major < 7.0:
                sid = data[pos + 32]
                for j in range(4):
                    g = struct.unpack_from("<H", data, pos + 12 + 2 * j)[0]
                    if 0 < g < world_max:
                        x, y = _xy(g, cols)
                        out["schedules"].append({"gridno": g, "x": x, "y": y,
                                                 "schedule_id": sid,
                                                 "action": data[pos + 28 + j]})
            pos += rec_size
        out["reached"].append("schedules")
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Expected: PASS.

- [ ] **Step 5: Add the endpoint model**

In `routes/mapforge.py`, add next to `AppendixEdgepoint`:

```python
class AppendixSchedule(BaseModel):
    gridno: int
    x: int
    y: int
    schedule_id: int
    action: int
```
Add `schedules: list[AppendixSchedule]` to `AppendixEntities` (after `edgepoints`).

- [ ] **Step 6: Add endpoint + real-map tests**

```python
def test_appendix_endpoint_returns_schedules():
    data = _old_tail_100()
    data += bytes([1]) + _schedule_record([200, 0xFFFF, 0xFFFF, 0xFFFF], [5, 8, 8, 8], schedule_id=3)
    parsed = {"flags": AW.MAP_NPCSCHEDULES_SAVED, "major": 5.0, "minor": 25,
              "cols": 160, "rows": 160, "appendix_offset": 0}
    sess = _fake_session(data, parsed)
    _session_store._sessions[sess.id] = sess
    try:
        res = session_appendix(sess.id)
    finally:
        del _session_store._sessions[sess.id]
    assert len(res.schedules) == 1
    assert res.schedules[0].gridno == 200 and res.schedules[0].schedule_id == 3

@pytest.mark.skipif(not os.path.exists(_A2), reason="canonical install not present")
def test_real_a2_schedules():
    with open(_A2, "rb") as f:
        data = f.read()
    out = extract_appendix_entities(data, parse_dat_full(data))
    assert out["blocked_at"] is None
    assert "schedules" in out["reached"]
    # A2 has 7 schedule nodes; several carry valid waypoint gridnos.
    assert len(out["schedules"]) >= 1
    assert all(0 < s["gridno"] < 25600 for s in out["schedules"])
```

(`_A2` is the module constant added in the doors/edges slice; reuse it.)

- [ ] **Step 7: Run focused + mapforge suite**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_mapforge_appendix_extract.py -q`
Then: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/ -q -k mapforge`
Expected: PASS, no regression.

- [ ] **Step 8: Commit**

```bash
git add sidecar/mercwizard_core/mapforge_engine/appendix_extract.py sidecar/routes/mapforge.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: NPC schedule waypoint extraction + endpoint model\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Frontend — schedule markers + toggle

**Files:**
- Modify: `frontend/src/lib/mapforge.ts`
- Modify: `frontend/src/routes/MapForgeSector.tsx`

**Interfaces:**
- Produces: `AppendixSchedule` TS interface + `schedules` field; `showSchedules` toggle (default OFF) + markers.

- [ ] **Step 1: TS type**

In `mapforge.ts`:

```typescript
export interface AppendixSchedule {
  gridno: number; x: number; y: number; schedule_id: number; action: number;
}
```
Add `schedules: AppendixSchedule[];` to `AppendixEntities` (after `edgepoints`).

- [ ] **Step 2: Toggle state + thread**

In `MapForgeSector.tsx`, add `const [showSchedules, setShowSchedules] = useState(false);` beside the others, and thread it through the overlay component the SAME way `showEdges` is threaded (state → call site → destructure → prop-type → render).

- [ ] **Step 3: Markers**

In the appendix marker block, using the `c(x,y)` helper:

```tsx
{showSchedules && appendix.schedules.map((s, i) => {
  const { cx, cy } = c(s.x, s.y);
  return <rect key={`sc-${i}`} x={cx - 3} y={cy - 3} width={6} height={6}
    transform={`rotate(45 ${cx} ${cy})`}
    fill="rgba(255,160,60,0.85)" stroke="rgba(160,90,0,0.9)" strokeWidth={1}
    vectorEffect="non-scaling-stroke">
    <title>{`schedule #${s.schedule_id} (action ${s.action})`}</title>
  </rect>;
})}
```

- [ ] **Step 4: Toggle checkbox**

Beside the others (include `text-gray-300` to match):

```tsx
<label className="flex items-center gap-1 text-xs text-gray-300">
  <input type="checkbox" checked={showSchedules} onChange={(e) => setShowSchedules(e.target.checked)} />
  Schedules{appendix ? ` (${appendix.schedules.length})` : ""}
</label>
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && node node_modules/typescript/bin/tsc --noEmit`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/mapforge.ts frontend/src/routes/MapForgeSector.tsx
git commit -m "$(printf 'MapForge overlay: schedule waypoint markers + toggle\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Self-Review

**Spec coverage:** schedules (u8 count + 36B v5, usData1 waypoints filtered to valid gridno) → Task 1; v7 advance-only → Task 1; model → Task 1; frontend markers + toggle (default off) → Task 2; real-A2 regression → Task 1 Step 6; never-throw overrun → Task 1. ✓
**Placeholder scan:** none. ✓
**Type consistency:** extractor `schedules`{gridno,x,y,schedule_id,action} == pydantic == TS; `**ents` carries it; `showSchedules` threaded like `showEdges`. ✓
