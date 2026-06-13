"""JA2 .dat sector file WRITER — inverse of map_corpus/parse_dat_ext.py.

Round-trips a parsed .dat (per parse_dat_full) back to bytes that match
what the engine writes. The first design goal is byte-exact identity:
parse(file) → write_back → bytes must equal the original file. That's
verified by tools/roundtrip_audit.py (B0 gate — 15,479/15,479 maps
byte-perfect across 30 installs, 2026-06-10) and pinned at byte level
by tests/test_mapforge_save.py.

Hybrid encoding strategy
========================
Some regions of the .dat file are perfectly understood (header, layer
counts, layer passes, room info) and we re-encode them from parsed
fields. Others have engine-side quirks we don't want to invent for
ourselves on write, so we COPY THE ORIGINAL BYTES through:

  - Heights region: each tile reserves 2 bytes but only the low byte
    is the actual sHeight value (worlddef.h:272 declares UINT8 but
    worlddef.cpp:2976-2980 reads INT16, leaving runtime garbage in the
    high byte). The parser carries low bytes in `heights` and the
    garbage high bytes in `heights_high`; the writer re-interleaves
    both, so unedited maps stay byte-identical while `set_height`
    edits to the low byte propagate. (Byte-level pin:
    tests/test_mapforge_save.py::test_height_edit_roundtrips_byte_exactly.)

  - Appendix: the optional WORLDITEMS / LIGHTS / MAPINFO / SOLDIERS /
    EXITGRIDS / DOORTABLE / EDGEPOINTS / NPCSCHEDULES tail is variable-
    size and version-dependent. The parser bails on several sections
    (items/soldiers/schedules). We don't want to re-implement them all
    just to round-trip — instead, copy bytes [appendix_offset:] from
    the original. Editing tile layers doesn't touch the appendix anyway.

Edit support
============
After parsing, mutate the `parsed` dict in place — e.g. update
parsed["structs"][gridno], parsed["n_per_tile"]["struct"][gridno], or
parsed["rooms"][gridno] — then call write_dat_bytes. The new layer
counts and layer passes propagate to the bytes; everything else stays.

Editable today: tile-layer data + rooms + heights (low byte) +
world_flags. Editing anything in the appendix requires writer
extensions (the B-phase work).
"""
from __future__ import annotations

import struct
from typing import Any, Dict, List, Tuple


def _pack_header(parsed: Dict[str, Any], original_bytes: bytes) -> bytes:
    """Pack the .dat header. Returns header_len bytes.

    Re-encodes the fields the parser actually exposes (major, minor,
    rows, cols, flags, tileset). The header_len includes 4 extra bytes
    AFTER those — `uiSoldierSize` (worlddef.cpp `LOADDATA(&uiSoldierSize,
    pBuffer, sizeof(INT32))`) — which the parser skips. We pass those
    4 bytes through from the original so the round-trip matches.
    """
    major: float = parsed["major"]
    minor: int = parsed["minor"]
    flags: int = parsed["flags"]
    tileset: int = parsed["tileset"]
    if major >= 7.0:
        rows: int = parsed["rows"]
        cols: int = parsed["cols"]
        out = bytearray(
            struct.pack("<fBiiII", major, minor, rows, cols, flags, tileset)
        )
        # Bytes 21-24: uiSoldierSize (UINT32). Pass through.
        out.extend(original_bytes[21:25])
        return bytes(out)
    else:
        # Older format: 4+1+4+4 = 13 known bytes, then uiSoldierSize at 13-16.
        out = bytearray(struct.pack("<fBII", major, minor, flags, tileset))
        out.extend(original_bytes[13:17])
        return bytes(out)


def _pack_layer_counts(parsed: Dict[str, Any]) -> bytes:
    """Pack the 4-byte-per-tile layer-count region. Each tile's 4 bytes:
        b0 = land_count (low nibble) | world_flags (high nibble)
        b1 = obj_count  (low nibble) | struct_count (high nibble)
        b2 = shadow_count (low nibble) | roof_count (high nibble)
        b3 = onroof_count (low nibble) | unused_nib (high nibble)
    """
    n_per_tile = parsed["n_per_tile"]
    world_flags = parsed["world_flags"]
    unused_nib = parsed["unused_nib"]
    world_max = parsed["rows"] * parsed["cols"]
    n_land = n_per_tile["land"]
    n_obj = n_per_tile["obj"]
    n_struct = n_per_tile["struct"]
    n_shadow = n_per_tile["shadow"]
    n_roof = n_per_tile["roof"]
    n_onroof = n_per_tile["onroof"]
    buf = bytearray(4 * world_max)
    for i in range(world_max):
        buf[4 * i + 0] = (n_land[i] & 0xF) | ((world_flags[i] & 0xF) << 4)
        buf[4 * i + 1] = (n_obj[i] & 0xF) | ((n_struct[i] & 0xF) << 4)
        buf[4 * i + 2] = (n_shadow[i] & 0xF) | ((n_roof[i] & 0xF) << 4)
        buf[4 * i + 3] = (n_onroof[i] & 0xF) | ((unused_nib[i] & 0xF) << 4)
    return bytes(buf)


def _pack_pass_2byte(entries: List[List[Tuple[int, int]]]) -> bytes:
    """Pack a 2-byte-per-entry layer pass (land/struct/shadow/roof/onroof).
    Each entry is (type:UINT8, sub:UINT8)."""
    out = bytearray()
    for tile_entries in entries:
        for t, s in tile_entries:
            out.append(t & 0xFF)
            out.append(s & 0xFF)
    return bytes(out)


def _pack_pass_object(entries: List[List[Tuple[int, int]]]) -> bytes:
    """Pack the object layer (3 bytes per entry: type:UINT8, sub:UINT16 LE)."""
    out = bytearray()
    for tile_entries in entries:
        for t, s in tile_entries:
            out.append(t & 0xFF)
            out.extend(struct.pack("<H", s & 0xFFFF))
    return bytes(out)


def _pack_room_info(rooms: List[int], room_bytes_per_tile: int) -> bytes:
    """Pack the room info region. 1 byte/tile for minor<29, 2 bytes/tile
    for minor>=29 (per parse_dat_ext.py:388-389)."""
    if room_bytes_per_tile == 1:
        return bytes(r & 0xFF for r in rooms)
    # 2 bytes per tile, little-endian
    out = bytearray(2 * len(rooms))
    for i, r in enumerate(rooms):
        struct.pack_into("<H", out, 2 * i, r & 0xFFFF)
    return bytes(out)


def write_dat_bytes(parsed: Dict[str, Any], original_bytes: bytes) -> bytes:
    """Encode `parsed` (from parse_dat_full) back to .dat bytes.

    `original_bytes` is the source file's bytes — required because we
    pass-through the heights region (preserves runtime-garbage high
    bytes) and the appendix (variable-size, partially-understood). If
    you don't have the original bytes (e.g. building from scratch),
    you'd need a separate writer that synthesizes those regions.
    """
    header_len = parsed["header_len"]
    rows = parsed["rows"]
    cols = parsed["cols"]
    world_max = rows * cols
    heights_size = 2 * world_max
    appendix_offset = parsed["appendix_offset"]

    out = bytearray()
    out.extend(_pack_header(parsed, original_bytes))
    # Heights region: emit from parsed["heights"] (low byte) + parsed
    # ["heights_high"] (the original UINT8-read-as-INT16 high byte we preserve
    # verbatim). Byte-identical to the original EXCEPT any tile whose height
    # was intentionally edited. Falls back to the original-bytes passthrough
    # when heights_high isn't available (e.g. a parsed dict from older code or
    # built from scratch).
    heights = parsed.get("heights")
    heights_high = parsed.get("heights_high")
    if (heights is not None and heights_high is not None
            and len(heights) == world_max and len(heights_high) == world_max):
        hb = bytearray(heights_size)
        for i in range(world_max):
            hb[2 * i] = heights[i] & 0xFF
            hb[2 * i + 1] = heights_high[i] & 0xFF
        out.extend(hb)
    else:
        out.extend(original_bytes[header_len:header_len + heights_size])
    out.extend(_pack_layer_counts(parsed))
    out.extend(_pack_pass_2byte(parsed["land"]))
    out.extend(_pack_pass_object(parsed["objs"]))
    out.extend(_pack_pass_2byte(parsed["structs"]))
    out.extend(_pack_pass_2byte(parsed["shadows"]))
    out.extend(_pack_pass_2byte(parsed["roofs"]))
    out.extend(_pack_pass_2byte(parsed["onroofs"]))
    out.extend(_pack_room_info(parsed["rooms"], parsed["room_bytes_per_tile"]))
    # Appendix passthrough. parsed["appendix_offset"] points to the byte
    # AFTER room info ends in the original file. If our re-encoding is
    # correct, len(out) right now == appendix_offset.
    out.extend(original_bytes[appendix_offset:])
    return bytes(out)


def write_dat_file(parsed: Dict[str, Any], original_bytes: bytes, out_path) -> int:
    """Convenience: encode + write to disk. Returns bytes written."""
    from pathlib import Path
    data = write_dat_bytes(parsed, original_bytes)
    Path(out_path).write_bytes(data)
    return len(data)


# ── Fresh-map synthesis (R6 "New sector") ──────────────────────────────
# write_dat_bytes() requires `original_bytes` because it passes the heights
# region + appendix through verbatim. A brand-new empty map has neither —
# every height is WORLD_BASE_HEIGHT (0) and there is no appendix (flags==0).
# So we synthesize the bytes directly here, matching the exact region layout
# that parse_dat_full reads back and that the engine's LoadWorld writes for a
# flat sector. This is the production twin of tests/test_mapforge_library.py
# `_build_minimal_dat`, generalized to a full sector with a ground texture on
# every tile. Defaults mirror a real shipped sector (major 7.0 / minor 31 /
# flags 0 / FIRSTTEXTURE sub 1 ground), verified against a9.dat (tileset 71).

GROUND_SLOT = 0   # FIRSTTEXTURE — the base ground tile-type
GROUND_SUB = 1    # dominant ground sub in shipped sectors (a9.dat: 22,492 tiles)


def build_empty_dat_bytes(
    tileset: int,
    rows: int = 160,
    cols: int = 160,
    *,
    major: float = 7.0,
    minor: int = 31,
    ground_slot: int = GROUND_SLOT,
    ground_sub: int = GROUND_SUB,
) -> bytes:
    """Synthesize a minimal but valid empty JA2 1.13 sector .dat.

    Every tile gets a single land-layer ground texture entry
    (ground_slot/ground_sub). All other layers (objs/structs/shadows/
    roofs/onroofs) are empty, room is 0, height is 0, world_flags are 0.
    flags==0 so the appendix is just the 32-byte MAPCREATE_STRUCT tail
    (all zero center/edge gridnos) and no items/lights/soldiers sections.

    The result round-trips byte-for-byte through parse_dat_full +
    write_dat_bytes and loads in the editor. It matches the region layout
    a real shipped flat sector uses (major 7.0 / minor 31, verified against
    a9.dat). Engine-validity note: the tail's edge/center gridnos are 0 —
    fine for a freshly-created map (the in-game editor recomputes them on
    first save); see the endpoint's docstring.

    Raises ValueError on out-of-range arguments.
    """
    if not (4.0 <= major <= 9.0):
        raise ValueError(f"implausible major version {major!r}")
    if rows <= 0 or cols <= 0 or rows > 1024 or cols > 1024:
        raise ValueError(f"implausible dimensions {rows}x{cols}")
    if major < 7.0:
        raise ValueError("build_empty_dat_bytes only emits the modern "
                         "(major>=7.0) header format")
    world_max = rows * cols
    gs = ground_slot & 0xFF
    gsub = ground_sub & 0xFF
    room_bytes_per_tile = 2 if minor >= 29 else 1

    out = bytearray()
    # Header (major>=7.0): f major, B minor, i rows, i cols, I flags, I tileset,
    # then 4 pass-through bytes (uiSoldierSize) → header_len == 25.
    out += struct.pack("<f", float(major))
    out += bytes([minor & 0xFF])
    out += struct.pack("<i", rows)
    out += struct.pack("<i", cols)
    out += struct.pack("<I", 0)            # flags == 0 → tail is parseable
    out += struct.pack("<I", tileset & 0xFFFFFFFF)
    out += bytes(4)                        # uiSoldierSize — zeroed
    # Heights region: 2 bytes/tile, all 0 (flat ground, no runtime garbage).
    out += bytes(2 * world_max)
    # Layer-count nibbles per tile: b0 = land(1) | world_flags(0),
    # b1 = obj(0) | struct(0), b2 = shadow(0) | roof(0), b3 = onroof(0)|unused(0).
    one_land_tile = bytes([0x01, 0x00, 0x00, 0x00])
    out += one_land_tile * world_max
    # Land pass: one (ground_slot, ground_sub) per tile.
    out += bytes([gs, gsub]) * world_max
    # obj / struct / shadow / roof / onroof passes: empty.
    # Room info: 0 for every tile.
    out += bytes(room_bytes_per_tile * world_max)
    # MapInformation tail (major>=7.0): 32-byte MAPCREATE_STRUCT, all zero.
    out += bytes(32)
    return bytes(out)
