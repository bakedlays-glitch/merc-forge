import os
import struct
import pytest
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

def _old_tail_100(north=-1, east=-1, south=-1, west=-1, center=-1, isolated=-1,
                  num_individuals=0):
    """100-byte _OLD_MAPCREATE_STRUCT (v<7) â€” sizeof is 100 (99 raw fields,
    MSVC align-2 round-up). N/E/S/W int16 @0/2/4/6, ubNumIndividuals @8,
    center @12, isolated @14, padded to 100."""
    b = bytearray(b"\x00" * 100)
    struct.pack_into("<hhhh", b, 0, north, east, south, west)
    b[8] = num_individuals & 0xFF
    struct.pack_into("<hh", b, 12, center, isolated)
    return bytes(b)

def _parsed(flags, major=5.0, minor=25, cols=COLS, rows=160, off=0):
    return {"flags": flags, "major": major, "minor": minor,
            "cols": cols, "rows": rows, "appendix_offset": off}

def test_extracts_world_items_with_positions():
    data = struct.pack("<I", 2)                       # u32 item count
    data += _old_worlditem(gridno=12880, usItem=264)  # tile (80,80)
    data += _old_worlditem(gridno=1, usItem=99)       # tile (1,0)
    data += _old_tail_100()                           # unconditional tail (no entries)
    out = extract_appendix_entities(data, _parsed(AW.MAP_WORLDITEMS_SAVED))
    assert out["blocked_at"] is None
    assert "items" in out["reached"] and "mapinfo" in out["reached"]
    assert [(i["gridno"], i["x"], i["y"], i["usItem"]) for i in out["items"]] == [
        (12880, 80, 80, 264), (1, 1, 0, 99)]

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

def _light_record(iX, iY, template=b"L-R05.LHT", flags=0x9B, template_id=1):
    """One per-light record: 24-byte LIGHT_SPRITE + u8 ubStrLen + string.
    LIGHT_SPRITE: iX,iY,iOldX,iOldY,iAnimSpeed (5*int16) + 2B pad +
    iTemplate(int32) + uiFlags(u32) + uiLightType(u32). ubStrLen counts the
    trailing NUL (= strlen+1), per SaveMapLights (worlddef.cpp:4170)."""
    sprite = struct.pack("<5hHiII", iX, iY, iX, iY, 0, 0, template_id, flags, 0)
    assert len(sprite) == 24
    s = template + b"\x00"
    return sprite + bytes([len(s)]) + s

def test_walks_light_records_and_continues_to_tail():
    # flags=LIGHTS, two light records, then the v7 tail. Cursor must clear the
    # variable-length light section and land on a valid tail.
    data = bytes([1]) + bytes(4) + struct.pack("<H", 2)   # numColors=1, 1 palette, count=2
    data += _light_record(100, 65, b"L-R05.LHT")
    data += _light_record(10, 20, b"L-R12.LHT")
    data += AW.pack_map_tail(north=500, map_version=31)
    out = extract_appendix_entities(data, _parsed(AW.MAP_WORLDLIGHTS_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] is None
    assert "lights" in out["reached"] and "mapinfo" in out["reached"]
    assert [(l["x"], l["y"], l["template"]) for l in out["lights"]] == [
        (100, 65, "L-R05.LHT"), (10, 20, "L-R12.LHT")]
    assert out["entry_points"][0]["gridno"] == 500  # tail reached intact

def test_light_string_overrun_degrades_gracefully():
    # Header promises 1 light but the string runs off the buffer end.
    data = bytes([1]) + bytes(4) + struct.pack("<H", 1)
    data += struct.pack("<5hHiII", 5, 5, 5, 5, 0, 0, 1, 0, 0)  # 24-byte sprite
    data += bytes([20])  # ubStrLen=20 but no string bytes follow
    out = extract_appendix_entities(data, _parsed(AW.MAP_WORLDLIGHTS_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] == "light_string_overrun"

def test_lights_zero_count_falls_through_to_tail():
    # flags=LIGHTS, count=0 â€” no deferral; continues to mapinfo tail.
    data = bytes([1]) + bytes(4) + struct.pack("<H", 0)   # numColors=1, 1 palette, count=0
    data += AW.pack_map_tail(north=500, map_version=31)
    out = extract_appendix_entities(data, _parsed(AW.MAP_WORLDLIGHTS_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] is None
    assert "lights_header" in out["reached"] and "mapinfo" in out["reached"]
    assert out["entry_points"][0] == {"kind": "north", "gridno": 500,
                                      "x": 500 % 160, "y": 500 // 160}

def test_soldiers_zero_individuals_v7_continues():
    # flags=SOLDIER, v7 tail with num_individuals=0 -> soldiers section passes through.
    data = AW.pack_map_tail(north=10, map_version=31, num_individuals=0)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] is None
    assert "mapinfo" in out["reached"]
    assert "soldiers" in out["reached"]
    assert out["soldiers"] == []
    assert out["entry_points"][0]["kind"] == "north"

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


from routes.mapforge import MapForgeSession, _session_store, session_appendix

def _fake_session(data, parsed):
    """Build a MapForgeSession without touching disk (bypass __init__)."""
    s = object.__new__(MapForgeSession)
    s.id = "testsess123456"
    s.original_bytes = data
    s.parsed = parsed
    return s

def test_appendix_endpoint_returns_entities():
    data = AW.pack_map_tail(north=1000, map_version=31)
    data += AW.pack_exit_grids([{"map_index": 320, "grid_no": 9, "sx": 1, "sy": 2, "sz": 0}])
    parsed = {"flags": AW.MAP_EXITGRIDS_SAVED, "major": 7.0, "minor": 31,
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

def test_appendix_endpoint_returns_soldiers():
    data = _old_tail_100(num_individuals=1)
    data += _old_soldier(gridno=320, team=1, facing=3, sclass=3)
    parsed = {"flags": AW.MAP_FULLSOLDIER_SAVED, "major": 5.0, "minor": 25,
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

def test_exit_grid_truncation_degrades_gracefully():
    # flags=EXITGRIDS, valid tail, count says 2 but only partial bytes follow.
    data = AW.pack_map_tail(map_version=31)
    data += struct.pack("<H", 2)        # count=2 ...
    data += b"\x00" * 6                 # ...but far fewer than 2*12 bytes
    out = extract_appendix_entities(data, _parsed(AW.MAP_EXITGRIDS_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] == "exitgrid_records_overrun"
    assert out["exit_grids"] == []      # nothing fully parsed


# ---------------------------------------------------------------------------
# Soldier tests (Step 2 â€” new)
# ---------------------------------------------------------------------------

def _old_soldier(gridno, team=1, facing=2, sclass=3, detailed=0, body_type=0):
    """One 52-byte _OLD_BASIC_SOLDIERCREATE_STRUCT (v<7). fDetailed@0,
    sStartingGridNo@2 (int16), bTeam@4 (int8), ubDirection@7,
    ubBodyType@10 (u8), ubSoldierClass@34."""
    b = bytearray(52)
    b[0] = detailed
    struct.pack_into("<h", b, 2, gridno)
    struct.pack_into("<b", b, 4, team)
    b[7] = facing
    b[10] = body_type
    b[34] = sclass
    return bytes(b)

def test_extracts_soldiers_with_positions_and_team():
    # flags=SOLDIER only: no items/ambient/lights -> tail(100) -> 2 soldiers.
    data = _old_tail_100(num_individuals=2)
    data += _old_soldier(gridno=12880, team=1, facing=6, sclass=3, body_type=1)  # enemy at (80,80)
    data += _old_soldier(gridno=160,   team=4, facing=2, sclass=0, body_type=29) # civilian at (0,1)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert "soldiers" in out["reached"]
    assert [(s["gridno"], s["x"], s["y"], s["team"], s["team_label"], s["facing"], s["soldier_class"],
             s["body_type"])
            for s in out["soldiers"]] == [
        (12880, 80, 80, 1, "enemy", 6, 3, 1),
        (160, 0, 1, 4, "civilian", 2, 0, 29)]

def test_soldier_detailed_placement_legacy_skip():
    # Legacy map (major=5.0, minor=25): one basic soldier, one detailed soldier
    # (fDetailed=1) followed by the fixed 1040-byte POD, then another basic soldier.
    # All THREE soldiers must be emitted (detailed one from its basic fields) and
    # blocked_at must be None.
    data = _old_tail_100(num_individuals=3)
    data += _old_soldier(gridno=100, team=1)
    data += _old_soldier(gridno=200, team=1, detailed=1)
    data += b"\x00" * 1040          # the 1040-byte detailed POD
    data += _old_soldier(gridno=300, team=4)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert [s["gridno"] for s in out["soldiers"]] == [100, 200, 300]

def test_soldier_detailed_legacy_skip_truncated():
    # Legacy map: detailed soldier whose 1040-byte POD is truncated (only partial
    # bytes follow). Must bail with "soldier_detailed_overrun"; soldiers BEFORE it retained.
    data = _old_tail_100(num_individuals=2)
    data += _old_soldier(gridno=100, team=1)
    data += _old_soldier(gridno=200, team=1, detailed=1)
    data += b"\x00" * 500           # truncated POD (< 1040)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] == "soldier_detailed_overrun"
    # both basic records are emitted; the truncated 1040B POD after the detailed one trips soldier_detailed_overrun
    assert [s["gridno"] for s in out["soldiers"]] == [100, 200]


def test_soldier_detailed_modern_bails():
    # Modern map (major=7.0): detailed soldier must still bail with "soldier_detailed"
    # (variable inventory; cannot be fixed-skipped).
    # Build a minimal 64-byte modern basic record with fDetailed=1 at byte 0.
    modern_record = bytearray(64)
    modern_record[0] = 1            # fDetailedPlacement = 1
    struct.pack_into("<i", modern_record, 4, 5000)  # sStartingGridNo @4 (v7 format)
    struct.pack_into("<b", modern_record, 8, 1)     # bTeam @8 (v7 format)
    # Build a v7 32-byte tail with num_individuals=1 (ubNumIndividuals @24 as uint16).
    tail = AW.pack_map_tail(map_version=31, num_individuals=1)
    data = tail + bytes(modern_record)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] == "soldier_detailed"
    assert [s["gridno"] for s in out["soldiers"]] == [5000]


def test_zero_soldiers_section_is_empty():
    # SOLDIER flag set but ubNumIndividuals=0 -> empty soldier section, continue.
    data = _old_tail_100(num_individuals=0)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert out["soldiers"] == []
    assert "soldiers" in out["reached"]

def test_soldier_records_overrun_degrades_gracefully():
    # tail says 2 individuals, but only 1 full 52-byte record follows.
    data = _old_tail_100(num_individuals=2)
    data += _old_soldier(gridno=100, team=1)        # one full record
    data += b"\x00" * 10                              # partial second record (< 52B)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] == "soldier_records_overrun"
    assert [s["gridno"] for s in out["soldiers"]] == [100]   # first soldier retained


# ---------------------------------------------------------------------------
# Real-map regression test (Step 6 â€” install-gated)
# ---------------------------------------------------------------------------

from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full

_A6 = (r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"
       r"\Data-1.13\Maps\A6.DAT")
_A2 = (r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"
       r"\Data-1.13\Maps\A2.DAT")

@pytest.mark.skipif(not os.path.exists(_A6), reason="canonical install not present")
def test_real_a6_soldiers():
    with open(_A6, "rb") as f:
        data = f.read()
    out = extract_appendix_entities(data, parse_dat_full(data))
    assert out["blocked_at"] is None
    assert len(out["soldiers"]) == 32                 # ubNumIndividuals
    assert all(0 <= s["gridno"] < 25600 for s in out["soldiers"])
    assert all(s["team"] == 1 for s in out["soldiers"])   # all ENEMY in A6
    assert all(s["soldier_class"] == 3 for s in out["soldiers"])  # ARMY


_MAPS_DIR = (r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"
             r"\Data-1.13\Maps")

@pytest.mark.skipif(not os.path.exists(_MAPS_DIR), reason="canonical install not present")
@pytest.mark.parametrize("name,count,template", [
    ("A1", 1, "L-R05.LHT"),
    ("A2", 8, "L-R08.LHT"),
])
def test_real_town_lights_walk_to_tail(name, count, template):
    """LIT town sectors: the per-light walk (24B LIGHT_SPRITE + u8 len + str)
    must clear the variable-length lights section and reach the MapInfo tail."""
    with open(os.path.join(_MAPS_DIR, f"{name}.DAT"), "rb") as f:
        data = f.read()
    out = extract_appendix_entities(data, parse_dat_full(data))
    assert "lights" in out["reached"]
    assert "mapinfo" in out["reached"]          # cursor landed on a valid tail
    assert len(out["lights"]) == count
    assert all(l["template"] == template for l in out["lights"])
    assert all(0 <= l["x"] < 160 and 0 <= l["y"] < 160 for l in out["lights"])


@pytest.mark.skipif(not os.path.exists(_MAPS_DIR), reason="canonical install not present")
@pytest.mark.parametrize("name,expected_count", [
    ("A1", 33),
    ("A2", 39),
])
def test_real_legacy_detailed_soldiers_emitted(name, expected_count):
    """Legacy town sectors with detailed placements (major=5.0, minor=25): after the
    1040-byte POD skip, ALL soldiers must be emitted and blocked_at must be None.
    A1=33, A2=39 (ubNumIndividuals from the map tail). Real-map proof of the fix."""
    with open(os.path.join(_MAPS_DIR, f"{name}.DAT"), "rb") as f:
        data = f.read()
    out = extract_appendix_entities(data, parse_dat_full(data))
    assert out["blocked_at"] is None, f"{name}: blocked at {out['blocked_at']!r}"
    assert len(out["soldiers"]) == expected_count, (
        f"{name}: got {len(out['soldiers'])} soldiers, expected {expected_count}"
    )
    assert "soldiers" in out["reached"]


def test_appendix_endpoint_returns_lights():
    # LIGHTS flag: header (1 color, 1 light) + one light record + 100B tail.
    data = bytes([1]) + bytes(4) + struct.pack("<H", 1)      # numColors=1, palette, count=1
    data += _light_record(10, 20, template=b"L-R05.LHT")     # reuse existing helper
    data += _old_tail_100()
    parsed = {"flags": AW.MAP_WORLDLIGHTS_SAVED, "major": 5.0, "minor": 25,
              "cols": 160, "rows": 160, "appendix_offset": 0}
    sess = _fake_session(data, parsed)
    _session_store._sessions[sess.id] = sess
    try:
        res = session_appendix(sess.id)
    finally:
        del _session_store._sessions[sess.id]
    assert len(res.lights) == 1
    assert res.lights[0].x == 10 and res.lights[0].y == 20
    assert res.lights[0].template == "L-R05.LHT"
    assert res.lights[0].gridno == 3210


# ---------------------------------------------------------------------------
# Door table + edgepoint tests (Task 1)
# ---------------------------------------------------------------------------

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


@pytest.mark.skipif(not os.path.exists(_A2), reason="canonical install not present")
def test_real_a2_doors_and_edges():
    with open(_A2, "rb") as f:
        data = f.read()
    out = extract_appendix_entities(data, parse_dat_full(data))
    assert out["blocked_at"] is None
    assert len(out["doors"]) == 3                         # A2 has 3 doors
    assert all(0 <= d["gridno"] < 25600 for d in out["doors"])
    assert all(d["locked"] for d in out["doors"])          # all 3 are locked
    assert len(out["edgepoints"]) > 0
    assert all(0 <= e["gridno"] < 25600 for e in out["edgepoints"])
    assert "doortable" in out["reached"] and "edgepoints" in out["reached"]

def test_edgepoint_overrun_degrades_gracefully():
    # North sub-section claims size=3 but only 2 int16s follow (last 2 bytes dropped).
    data = _old_tail_100()
    full = _edge_section([100, 200, 300])   # u16 size=3 + u16 middle + 3*int16
    data += full[:-2]                        # drop the last gridno's 2 bytes
    out = extract_appendix_entities(data, _parsed(AW.MAP_EDGEPOINTS_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] == "edgepoint_records_overrun"
    # the gridnos read before the truncation are retained
    assert [e["gridno"] for e in out["edgepoints"]] == [100, 200]


# ---------------------------------------------------------------------------
# Schedule tests (Task 1 â€” schedules)
# ---------------------------------------------------------------------------

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
    data += _schedule_record([100, 260, 0xFFFF, 0xFFFF], [5, 9, 8, 8], schedule_id=1)
    # schedule 2: one waypoint (gridno 320 action 5).
    data += _schedule_record([320, 0xFFFF, 0xFFFF, 0xFFFF], [5, 8, 8, 8], schedule_id=2)
    out = extract_appendix_entities(data, _parsed(AW.MAP_NPCSCHEDULES_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] is None
    assert "schedules" in out["reached"]
    assert [(s["gridno"], s["x"], s["y"], s["schedule_id"], s["action"]) for s in out["schedules"]] == [
        (100, 100, 0, 1, 5), (260, 100, 1, 1, 9), (320, 0, 2, 2, 5)]

def test_schedules_overrun_degrades():
    data = _old_tail_100()
    data += bytes([2]) + _schedule_record([100, 0xFFFF, 0xFFFF, 0xFFFF], [5, 8, 8, 8])  # count 2, only 1 record
    out = extract_appendix_entities(data, _parsed(AW.MAP_NPCSCHEDULES_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] == "schedules_records_overrun"
    assert [s["gridno"] for s in out["schedules"]] == [100]

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


def test_schedule_waypoint_at_gridno_zero_is_emitted():
    data = _old_tail_100()
    data += bytes([1]) + _schedule_record([0, 0xFFFF, 0xFFFF, 0xFFFF], [5, 8, 8, 8], schedule_id=1)
    out = extract_appendix_entities(data, _parsed(AW.MAP_NPCSCHEDULES_SAVED, major=5.0, minor=25))
    assert [(s["gridno"], s["x"], s["y"]) for s in out["schedules"]] == [(0, 0, 0)]

@pytest.mark.skipif(not os.path.exists(_A2), reason="canonical install not present")
def test_real_a2_schedules():
    with open(_A2, "rb") as f:
        data = f.read()
    out = extract_appendix_entities(data, parse_dat_full(data))
    assert out["blocked_at"] is None
    assert "schedules" in out["reached"]
    # A2 has 7 schedule nodes; several carry valid waypoint gridnos.
    assert len(out["schedules"]) >= 1
    assert all(0 <= s["gridno"] < 25600 for s in out["schedules"])
