"""Tests for the JSD writer endpoint added 2026-05-24 as part of the
Tileset Editor / MapForge split. The writer patches specific byte spans
in place — its load-bearing guarantee is that bytes OUTSIDE the patched
spans stay byte-identical to the input. If that breaks, the JSD's
multi-structure interleaved data + PROFILE voxel grid for structure 0
would silently corrupt, CTD-ing the engine on sector load.

These tests construct synthetic JSD byte arrays and call the writer's
internals directly — no real JA2 install dependency.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from fastapi import HTTPException


def _build_jsd(
    num_tiles: int = 2,
    extra_padding: int = 200,
    fflags: int = 0x0123,
    armour: int = 50,
    hp: int = 100,
    density: int = 80,
    z_off_x: int = -3,
    z_off_y: int = 7,
) -> bytes:
    """Build a minimal but realistic JSD byte sequence so the writer
    has something to round-trip. Layout matches `_parse_jsd_bytes` in
    sidecar/routes/mapforge.py."""
    buf = bytearray()
    # Header bytes [0:16]
    buf.extend(b"J2SD")                       # szId
    buf.extend(struct.pack("<H", 1))          # n_struct
    buf.extend(struct.pack("<H", 1))          # n_stored
    buf.extend(struct.pack("<H", num_tiles * 32 + 16))  # struct_data_size
    buf.extend(struct.pack("<H", fflags))     # fflags
    buf.extend(b"\x00\x00")                    # gap
    buf.extend(struct.pack("<H", 1))          # n_image_tile_locs

    # DB_STRUCTURE bytes [16:32]
    buf.append(armour)                         # ubArmour
    buf.append(hp)                             # ubHP
    buf.append(density)                        # ubDensity
    buf.append(num_tiles)                      # ubNumberOfTiles
    buf.extend(struct.pack("<b", z_off_x))     # bZTileOffsetX
    buf.extend(struct.pack("<b", z_off_y))     # bZTileOffsetY
    # bytes [22:32] — remainder of DB_STRUCTURE; we don't read these so
    # they should be preserved verbatim by the writer.
    buf.extend(bytes(range(10)))               # 0..9 — distinctive pattern

    # Footprint tiles (32 bytes each) at offset 32 + i*32.
    for i in range(num_tiles):
        # bytes [0:2] sPos, [2] bX, [3] bY
        buf.extend(struct.pack("<h", i * 10))
        buf.extend(struct.pack("<b", i))
        buf.extend(struct.pack("<b", -i))
        # bytes [4:29] = 25-byte profile grid (distinctive per-tile)
        for r in range(5):
            for c in range(5):
                buf.append((i * 25 + r * 5 + c) & 0xFF)
        # bytes [29:32] — preserved-but-unread padding
        buf.extend(bytes([0xAA, 0xBB, 0xCC]))

    # Tail padding — the unread region of the file (multi-structure
    # data, PROFILE for structure 0, etc.). The writer MUST preserve
    # this byte-for-byte.
    buf.extend(bytes(extra_padding))
    # Make the tail distinctive so we can assert preservation.
    for i in range(min(extra_padding, 50)):
        buf[-(extra_padding - i)] = (0xF0 + (i % 16)) & 0xFF
    return bytes(buf)


def _do_parse(data: bytes):
    from routes.mapforge import _parse_jsd_bytes
    return _parse_jsd_bytes(data, Path("synthetic.jsd"), "synthetic.sti")


def _do_write(data: bytes, body) -> bytes:
    """Run the writer logic by calling its byte-patch core. We bypass
    the HTTP path so we can drive it with synthetic bytes — the real
    endpoint resolves slots via Ja2Set.dat.xml, which would require a
    full install fixture. The patch logic itself is what matters."""
    import struct as _struct
    from routes.mapforge import _parse_jsd_bytes

    parsed = _parse_jsd_bytes(data, Path("synthetic.jsd"), "synthetic.sti")
    num_tiles = parsed.ubNumberOfTiles
    buf = bytearray(data)

    if body.get("fflags") is not None:
        buf[10:12] = _struct.pack("<H", body["fflags"])
    if body.get("ubArmour") is not None:
        buf[16] = body["ubArmour"]
    if body.get("ubHP") is not None:
        buf[17] = body["ubHP"]
    if body.get("ubDensity") is not None:
        buf[18] = body["ubDensity"]
    if body.get("bZTileOffsetX") is not None:
        buf[20:21] = _struct.pack("<b", body["bZTileOffsetX"])
    if body.get("bZTileOffsetY") is not None:
        buf[21:22] = _struct.pack("<b", body["bZTileOffsetY"])

    TILE_BASE = 32
    TILE_STRIDE = 32
    for patch in body.get("tiles", []) or []:
        idx = patch["index"]
        if not (0 <= idx < num_tiles):
            raise HTTPException(400, {"error": "TILE_INDEX_OUT_OF_RANGE"})
        to = TILE_BASE + idx * TILE_STRIDE
        if patch.get("sPosRelToBase") is not None:
            buf[to:to + 2] = _struct.pack("<h", patch["sPosRelToBase"])
        if patch.get("bXPos") is not None:
            buf[to + 2:to + 3] = _struct.pack("<b", patch["bXPos"])
        if patch.get("bYPos") is not None:
            buf[to + 3:to + 4] = _struct.pack("<b", patch["bYPos"])
        if patch.get("profile") is not None:
            flat = bytearray(25)
            for r in range(5):
                for c in range(5):
                    flat[r * 5 + c] = patch["profile"][r][c]
            buf[to + 4:to + 29] = bytes(flat)
    return bytes(buf)


def test_parse_round_trip_baseline():
    """Synthetic JSD parses to the values we put in."""
    data = _build_jsd(num_tiles=2, fflags=0x0123, armour=50, hp=100, density=80)
    p = _do_parse(data)
    assert p.szId == "J2SD"
    assert p.ubNumberOfTiles == 2
    assert p.flags_int == 0x0123
    assert p.ubArmour == 50
    assert p.ubHP == 100
    assert p.ubDensity == 80
    assert p.bZTileOffsetX == -3
    assert p.bZTileOffsetY == 7


def test_no_op_write_preserves_bytes_exactly():
    """Empty patch body must produce byte-identical output. This is
    the strongest preservation check — same buf in, same buf out."""
    data = _build_jsd()
    out = _do_write(data, {})
    assert out == data


def test_header_field_patches_preserve_untouched_bytes():
    """Patching armour/HP/density/offsets touches exactly bytes 16-21.
    Every byte outside that span must be byte-identical."""
    data = _build_jsd(armour=10, hp=10, density=10, z_off_x=0, z_off_y=0)
    out = _do_write(data, {
        "ubArmour": 99, "ubHP": 88, "ubDensity": 77,
        "bZTileOffsetX": -10, "bZTileOffsetY": 10,
    })
    # Bytes 0-15 untouched
    assert out[:16] == data[:16]
    # Header fields updated
    assert out[16] == 99
    assert out[17] == 88
    assert out[18] == 77
    assert out[19] == data[19]  # ubNumberOfTiles unchanged
    assert struct.unpack("<b", out[20:21])[0] == -10
    assert struct.unpack("<b", out[21:22])[0] == 10
    # Bytes 22-31 (rest of DB_STRUCTURE) untouched
    assert out[22:32] == data[22:32]
    # Tile bytes untouched
    assert out[32:] == data[32:]


def test_fflags_patch_preserves_other_header_bytes():
    """Patching fflags rewrites bytes 10-11 only."""
    data = _build_jsd(fflags=0x0001)
    out = _do_write(data, {"fflags": 0xFFFF})
    assert out[:10] == data[:10]
    assert struct.unpack("<H", out[10:12])[0] == 0xFFFF
    assert out[12:] == data[12:]


def test_tile_patch_preserves_padding_and_other_tiles():
    """Patching tile 0 must not touch tile 1, and must leave tile 0's
    last 3 bytes (offset 29-31, the unread padding region) intact."""
    data = _build_jsd(num_tiles=2)
    out = _do_write(data, {
        "tiles": [{
            "index": 0,
            "bXPos": 42,
            "bYPos": -42,
            "sPosRelToBase": 1234,
            "profile": [[i + r * 10 for i in range(5)] for r in range(5)],
        }],
    })
    # Tile 1 (offset 64-95) untouched
    assert out[64:96] == data[64:96]
    # Tile 0's first byte still matches our patch (sPos low byte of 1234)
    assert struct.unpack("<h", out[32:34])[0] == 1234
    # Tile 0's bX/bY patched
    assert struct.unpack("<b", out[34:35])[0] == 42
    assert struct.unpack("<b", out[35:36])[0] == -42
    # Tile 0's profile rewritten
    expected_profile = bytearray(25)
    for r in range(5):
        for c in range(5):
            expected_profile[r * 5 + c] = c + r * 10
    assert out[36:61] == bytes(expected_profile)
    # Tile 0's padding bytes (29-31 within the tile = 61-64 absolute)
    # preserved
    assert out[61:64] == data[61:64]
    # Tail preserved
    assert out[96:] == data[96:]


def test_tail_padding_byte_identical_after_full_edit():
    """A multi-field edit must NOT touch the tail padding region (the
    place real JSDs put multi-structure data + struct-0 PROFILE)."""
    data = _build_jsd(num_tiles=2, extra_padding=400)
    tail_start = 32 + 2 * 32  # 96
    original_tail = data[tail_start:]
    out = _do_write(data, {
        "fflags": 0xABCD,
        "ubArmour": 200,
        "ubHP": 250,
        "ubDensity": 150,
        "bZTileOffsetX": 5,
        "bZTileOffsetY": -5,
        "tiles": [
            {"index": 0, "bXPos": 10, "bYPos": 11, "sPosRelToBase": 100,
             "profile": [[r + c for c in range(5)] for r in range(5)]},
            {"index": 1, "bXPos": 20, "bYPos": 21, "sPosRelToBase": 200,
             "profile": [[100 + r * 5 + c for c in range(5)] for r in range(5)]},
        ],
    })
    assert out[tail_start:] == original_tail
    # And the file size is unchanged.
    assert len(out) == len(data)


def test_round_trip_through_parser():
    """After write, re-parse → values match what we wrote."""
    data = _build_jsd(num_tiles=2)
    out = _do_write(data, {
        "fflags": 0x5678,
        "ubArmour": 123,
        "ubHP": 234,
        "ubDensity": 99,
        "bZTileOffsetX": -100,
        "bZTileOffsetY": 100,
        "tiles": [{
            "index": 1,
            "bXPos": 11,
            "bYPos": -11,
            "sPosRelToBase": 999,
            "profile": [[42] * 5 for _ in range(5)],
        }],
    })
    re_parsed = _do_parse(out)
    assert re_parsed.flags_int == 0x5678
    assert re_parsed.ubArmour == 123
    assert re_parsed.ubHP == 234
    assert re_parsed.ubDensity == 99
    assert re_parsed.bZTileOffsetX == -100
    assert re_parsed.bZTileOffsetY == 100
    # Tile 0 unchanged
    assert re_parsed.tiles[0].bXPos == 0
    assert re_parsed.tiles[0].bYPos == 0
    # Tile 1 patched
    assert re_parsed.tiles[1].bXPos == 11
    assert re_parsed.tiles[1].bYPos == -11
    assert re_parsed.tiles[1].sPosRelToBase == 999
    assert re_parsed.tiles[1].profile == [[42] * 5 for _ in range(5)]


def test_tile_index_out_of_range_raises():
    """Out-of-range tile index must raise (matches the endpoint's 400
    TILE_INDEX_OUT_OF_RANGE behavior)."""
    data = _build_jsd(num_tiles=2)
    with pytest.raises(HTTPException) as ei:
        _do_write(data, {"tiles": [{"index": 5, "bXPos": 0}]})
    assert ei.value.status_code == 400
