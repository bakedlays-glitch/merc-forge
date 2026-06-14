"""Appendix authoring (exit grids / ambient / entry points) — byte-level.

The serializer (appendix_writer.py) writes the source-verified modern
(major>=7.0) appendix: ambient(3B) + MAPINFO tail(32B, UNCONDITIONAL) +
exitgrids(u16 count + 12B each), in that order, with flags set only for
sections actually written. A wrong byte crashes the map on load, so these
pin the record sizes, the tail-before-exitgrids order, the flag word, and
the idempotent verbatim passthrough for unedited maps.
"""
import struct

from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full
from mercwizard_core.mapforge_engine.dat_writer import (
    build_empty_dat_bytes, write_dat_bytes,
)
from mercwizard_core.mapforge_engine import appendix_writer as AW


def test_record_sizes():
    assert len(AW.pack_ambient(0, 0, 12)) == 3
    assert len(AW.pack_map_tail()) == 32
    assert len(AW.pack_exit_grids([])) == 2          # just the u16 count
    one = AW.pack_exit_grids([{"map_index": 5, "grid_no": 6, "sx": 9, "sy": 1, "sz": 0}])
    assert len(one) == 2 + 12                          # count + one 12B record


def test_exit_grid_record_is_12_bytes_int32_gridnos():
    b = AW.pack_exit_grids([{"map_index": 0x11223344, "grid_no": 0x55667788,
                             "sx": 9, "sy": 1, "sz": 2}])
    n, = struct.unpack_from("<H", b, 0)
    assert n == 1
    mi, gn, sx, sy, sz = struct.unpack_from("<iiBBB", b, 2)
    assert (mi, gn, sx, sy, sz) == (0x11223344, 0x55667788, 9, 1, 2)
    assert len(b) == 2 + 12          # count + one 12B record
    assert b[13] == 0                # trailing pad byte of the record


def test_tail_entry_points_and_size():
    b = AW.pack_map_tail(north=1000, east=2000, south=3000, west=4000,
                         center=5000, isolated=-1, num_individuals=7,
                         map_version=31, smoothing_type=1)
    vals = struct.unpack("<iiiiiiHBBBxxx", b)
    assert vals[:6] == (1000, 2000, 3000, 4000, 5000, -1)
    assert vals[6] == 7 and vals[7] == 31 and vals[9] == 1


def test_build_appendix_order_and_flags():
    model = {
        "ambient": {"basement": 0, "caves": 0, "level": 20},
        "exit_grids": [{"map_index": 100, "grid_no": 200, "sx": 9, "sy": 1, "sz": 0}],
        "tail": {"north": 1000, "map_version": 31},
    }
    b, flags = AW.build_appendix(model, existing_tail=None, major=7.0)
    # flags: ambient(0x80) | exitgrids(0x10)
    assert flags == (AW.MAP_AMBIENTLIGHTLEVEL_SAVED | AW.MAP_EXITGRIDS_SAVED)
    # order: ambient(3) THEN tail(32) THEN exitgrids(2+12). Tail before exitgrids.
    assert len(b) == 3 + 32 + 2 + 12
    # tail north at offset 3 (after ambient)
    assert struct.unpack_from("<i", b, 3)[0] == 1000
    # exitgrid count at offset 3+32
    assert struct.unpack_from("<H", b, 35)[0] == 1


def test_end_to_end_author_then_reparse():
    data = build_empty_dat_bytes(tileset=71)        # flags=0, 32B tail
    parsed = parse_dat_full(data, "synthetic")
    assert parsed["flags"] == 0
    parsed["appendix_model"] = {
        "ambient": {"basement": 0, "caves": 0, "level": 16},
        "exit_grids": [
            {"map_index": 12880, "grid_no": 13000, "sx": 9, "sy": 1, "sz": 0},
            {"map_index": 12881, "grid_no": 13001, "sx": 9, "sy": 1, "sz": 0},
        ],
        "tail": {"north": 100, "east": 200, "south": 300, "west": 400,
                 "map_version": parsed["minor"], "smoothing_type": 1},
    }
    out = write_dat_bytes(parsed, data)
    q = parse_dat_full(out, "authored")
    assert q["flags"] == (AW.MAP_AMBIENTLIGHTLEVEL_SAVED | AW.MAP_EXITGRIDS_SAVED)
    assert q["appendix_sections_present"]["ambient"] is True
    assert q["appendix_sections_present"]["exitgrids"] is True
    assert q["appendix_exitgrid_count"] == 2
    # full appendix navigated cleanly (proves ambient+tail+exitgrid sizing right)
    assert q["appendix_parse_stopped_at"] is None
    # tail sits after the 3-byte ambient; verify entry points survived
    n, e, s, w = struct.unpack_from("<iiii", out, q["appendix_offset"] + 3)
    assert (n, e, s, w) == (100, 200, 300, 400)


def test_no_model_is_byte_identical_passthrough():
    data = build_empty_dat_bytes(tileset=71)
    parsed = parse_dat_full(data, "synthetic")
    # no appendix_model -> verbatim passthrough, byte-exact round-trip
    assert write_dat_bytes(parsed, data) == data
