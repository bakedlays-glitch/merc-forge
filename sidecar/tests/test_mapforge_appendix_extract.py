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
