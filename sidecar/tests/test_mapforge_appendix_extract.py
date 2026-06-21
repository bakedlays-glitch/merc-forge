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
    """100-byte _OLD_MAPCREATE_STRUCT (v<7) — sizeof is 100 (99 raw fields,
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

def test_blocks_on_light_records():
    # flags=LIGHTS, header says 3 lights -> deferred.
    data = bytes([1]) + bytes(4) + struct.pack("<H", 3)   # numColors=1, 1 palette, count=3
    out = extract_appendix_entities(data, _parsed(AW.MAP_WORLDLIGHTS_SAVED, major=7.0, minor=31))
    assert out["blocked_at"] == "lights_records"
    assert "lights_header" in out["reached"]

def test_lights_zero_count_falls_through_to_tail():
    # flags=LIGHTS, count=0 — no deferral; continues to mapinfo tail.
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
# Soldier tests (Step 2 — new)
# ---------------------------------------------------------------------------

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

def test_soldier_records_overrun_degrades_gracefully():
    # tail says 2 individuals, but only 1 full 52-byte record follows.
    data = _old_tail_100(num_individuals=2)
    data += _old_soldier(gridno=100, team=1)        # one full record
    data += b"\x00" * 10                              # partial second record (< 52B)
    out = extract_appendix_entities(data, _parsed(AW.MAP_FULLSOLDIER_SAVED, major=5.0, minor=25))
    assert out["blocked_at"] == "soldier_records_overrun"
    assert [s["gridno"] for s in out["soldiers"]] == [100]   # first soldier retained


# ---------------------------------------------------------------------------
# Real-map regression test (Step 6 — install-gated)
# ---------------------------------------------------------------------------

from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full

_A6 = (r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"
       r"\Data-1.13\Maps\A6.DAT")

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
