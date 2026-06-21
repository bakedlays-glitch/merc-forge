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
