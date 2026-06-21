r"""
parse_dat_ext.py
================

Standalone .dat parser that accepts raw bytes (so we can feed maps directly
from SLF archives) and extracts everything the corpus analyzer needs:

  - header (version, rows, cols, flags, tileset)
  - per-tile layer counts (land/obj/struct/shadow/roof/onroof) + world_flags
  - all 6 layer passes
  - room IDs per tile
  - the MapInformation tail (entry points, center, isolated, smoothing,
    map version, num individuals) — 100 bytes for major<7.0, 32 bytes for major>=7.0

Format reference: the JA2 1.13 map/tile format (worlddef.h / SaveLoadMap.cpp).

The vanilla parse_dat.py reads from a file path and prints diagnostics; this
version is silent, byte-oriented, and returns a dict.
"""

from __future__ import annotations
import struct
from typing import Any, Dict, List, Tuple

# Layer 3-byte object misalignment: object entries are UINT8 type + UINT16 sub.
# All other layers are UINT8 type + UINT8 sub.
LAYER_FMT = {
    "land": ("<BB", 2),
    "obj":  ("<BH", 3),   # 3-byte misalignment
    "struct": ("<BB", 2),
    "shadow": ("<BB", 2),
    "roof":   ("<BB", 2),
    "onroof": ("<BB", 2),
}


class DatParseError(Exception):
    pass


# Phase E1.5 Phase 2C: appendix flag bit values per worlddef.cpp:60-68.
MAP_FULLSOLDIER_SAVED       = 0x00000001
MAP_WORLDONLY_SAVED         = 0x00000002  # legacy / no-op in 1.13
MAP_WORLDLIGHTS_SAVED       = 0x00000004  # gates point-light array (deprecated bit name but still active)
MAP_WORLDITEMS_SAVED        = 0x00000008
MAP_EXITGRIDS_SAVED         = 0x00000010
MAP_DOORTABLE_SAVED         = 0x00000020
MAP_EDGEPOINTS_SAVED        = 0x00000040
MAP_AMBIENTLIGHTLEVEL_SAVED = 0x00000080
MAP_NPCSCHEDULES_SAVED      = 0x00000100


def parse_appendix_minimal(
    data: bytes,
    appendix_offset: int,
    flags: int,
    major: float,
    minor: int = 0,
    items_table: Dict[int, int] | None = None,
) -> Dict[str, Any]:
    """Best-effort linear parse of the appendix region.

    The appendix region holds optional sections gated by `flags` bits.
    Engine read order per worlddef.cpp LoadWorld() lines 3171-3264:
      1. items       (MAP_WORLDITEMS_SAVED)        - VARIABLE-SIZE per record (BLOCKS further parse)
      2. ambient     (MAP_AMBIENTLIGHTLEVEL_SAVED) - 3 bytes (basement, caves, level)
      3. lights      (MAP_WORLDLIGHTS_SAVED)       - uint16 count + count*16 bytes
      4. mapinfo     (mandatory)                   - 32 bytes (major>=7.0) or 100 bytes (older)
      5. soldiers    (MAP_FULLSOLDIER_SAVED)       - VARIABLE-SIZE (BLOCKS)
      6. exit grids  (MAP_EXITGRIDS_SAVED)         - uint16 count + count*12 bytes
      7. door table  (MAP_DOORTABLE_SAVED)         - uint8 count + count*14 bytes
      8. edge points (MAP_EDGEPOINTS_SAVED)        - 8 sections (north/east/south/west x primary/2nd),
                                                     each (uint16 size + uint16 middle + size*record),
                                                     record_size = 2 (INT16) if major<7.0 else 4 (INT32)
      9. schedules   (MAP_NPCSCHEDULES_SAVED)      - VARIABLE-SIZE (BLOCKS)

    Variable-size sections (items, soldiers, schedules) block further parse
    when encountered, because the engine's per-record Load* methods are
    version-dependent and not easily mirrored in Python. For maps with no
    items/soldiers/schedules in their flags, we get the full appendix breakdown.

    Returns a dict with `appendix_sections_present`, the parsed counts for
    every section reached, and `appendix_parse_stopped_at` naming the first
    blocker (or None if all reachable sections parsed successfully).
    """
    out: Dict[str, Any] = {
        "appendix_sections_present": {
            "items":       bool(flags & MAP_WORLDITEMS_SAVED),
            "ambient":     bool(flags & MAP_AMBIENTLIGHTLEVEL_SAVED),
            "lights":      bool(flags & MAP_WORLDLIGHTS_SAVED),
            "soldiers":    bool(flags & MAP_FULLSOLDIER_SAVED),
            "exitgrids":   bool(flags & MAP_EXITGRIDS_SAVED),
            "doortable":   bool(flags & MAP_DOORTABLE_SAVED),
            "edgepoints":  bool(flags & MAP_EDGEPOINTS_SAVED),
            "schedules":   bool(flags & MAP_NPCSCHEDULES_SAVED),
        },
        "appendix_item_count":       None,
        "appendix_items":            None,   # Phase WA: per-item summaries (Path B only)
        "appendix_basement":         None,
        "appendix_caves":            None,
        "appendix_ambient_level":    None,
        "appendix_light_count":      None,
        "appendix_exitgrid_count":   None,
        "appendix_doortable_count":  None,
        "appendix_edgepoint_count":  None,
        "appendix_parse_stopped_at": None,
    }

    pos = appendix_offset
    n = len(data)
    end = n  # appendix runs to end of file
    safe_uint16 = lambda p: struct.unpack_from("<H", data, p)[0]
    safe_uint32 = lambda p: struct.unpack_from("<I", data, p)[0]

    def bail(reason: str) -> Dict[str, Any]:
        # Phase 2C originally nullified every appendix field on any bail because
        # we couldn't trust intermediate values. Phase WA validated the items
        # (52-byte Path B), lights-header (uint8 colors + 4*N palette + uint16
        # count), and MapInfo (32B major>=7.0 / 100B legacy) byte layouts via
        # hex-dump on Arulco Revisited p1.dat. Fields successfully assigned
        # before the bail point stay at their parsed value; fields after the
        # bail stay None (they were never assigned).
        out["appendix_parse_stopped_at"] = reason
        return out

    try:
        # 1. ITEMS — variable-size. Path B (legacy v5.0.x) is fixed-size and
        # parsed here; Path A (modern v6+) returns a "WB not implemented" bail.
        if flags & MAP_WORLDITEMS_SAVED:
            if pos + 4 > end:
                return bail("items_count_truncated")
            out["appendix_item_count"] = safe_uint32(pos)
            pos += 4
            # Phase WA: dispatch to parse_world_items (lazy import — module
            # only loaded when items flag is set, keeps build_corpus startup
            # cost down and avoids circularity if anything else imports here).
            from .parse_world_items import parse_world_items
            items, pos, item_bail = parse_world_items(
                data, pos, out["appendix_item_count"], major, minor,
                items_table=items_table, capture="summary",
            )
            out["appendix_items"] = items
            if item_bail:
                return bail(item_bail)
            # Path B success — continue past items into ambient/lights/mapinfo.

        # 2. AMBIENT — fixed 3 bytes if flag set.
        if flags & MAP_AMBIENTLIGHTLEVEL_SAVED:
            if pos + 3 > end:
                return bail("ambient_truncated")
            out["appendix_basement"]      = data[pos]
            out["appendix_caves"]         = data[pos + 1]
            out["appendix_ambient_level"] = data[pos + 2]
            pos += 3

        # 3. LIGHTS — LoadMapLights (worlddef.cpp:4179) actually reads:
        #   uint8 ubNumColors
        #   ubNumColors * SGPPaletteEntry(4 bytes: peRed, peGreen, peBlue, peFlags)
        #   uint16 usNumLights
        #   For each light: LIGHT_SPRITE + uint8 ubStrLen + str[ubStrLen]
        # We can't size LIGHT_SPRITE (and the trailing string is variable),
        # so we report the count but stop parsing here. Maps with non-zero
        # light count get appendix_parse_stopped_at = "lights_records_variable".
        if flags & MAP_WORLDLIGHTS_SAVED:
            if pos + 1 > end:
                return bail("lights_header_truncated")
            num_colors = data[pos]
            pos += 1
            if pos + 4 * num_colors + 2 > end:
                return bail("lights_palette_truncated")
            pos += 4 * num_colors
            light_count = safe_uint16(pos)
            out["appendix_light_count"] = light_count
            pos += 2
            if light_count > 0:
                # Variable per-light layout; bail here, downstream counts null.
                return bail("lights_records_variable")

        # 4. MAPINFO tail. Engine LoadMapInformation reads version-dependent
        # data: 100 bytes for major<7.0 (_OLD_MAPCREATE_STRUCT, MSVC align-2
        # rounds 99 raw fields up to 100), 32 bytes for major>=7.0
        # (MAPCREATE_STRUCT with MSVC 4-byte alignment).
        # Phase WA: re-enabled the legacy path now that items + lights
        # parsing advances the cursor correctly to the tail. Validated on
        # Arulco Revisited p1.dat (major=5.0, flags=0x17d): 4 plausible edge
        # gridnos parse at the expected offset.
        tail_size = 32 if major >= 7.0 else 100
        if pos + tail_size > end:
            return bail("mapinfo_truncated")
        pos += tail_size

        # 5. SOLDIERS — variable-size blocker.
        if flags & MAP_FULLSOLDIER_SAVED:
            return bail("soldiers_records")

        # 6. EXITGRIDS — uint16 count + count * 12 bytes. Modern (major>=7.0)
        # EXITGRID is a class: iMapIndex(i32) usGridNo(i32) ubGotoSectorX/Y/Z(3xu8)
        # +1 pad = sizeof 12 (Exit Grids.h:16-31, source-verified 2026-06-14).
        # (The old "8" was the classic INT16-gridno layout — wrong for v7.0.)
        if flags & MAP_EXITGRIDS_SAVED:
            if pos + 2 > end:
                return bail("exitgrid_count_truncated")
            eg_count = safe_uint16(pos)
            out["appendix_exitgrid_count"] = eg_count
            pos += 2 + 12 * eg_count
            if pos > end:
                return bail("exitgrid_records_overrun")

        # 7. DOORTABLE — uint8 count + count * 14 bytes (_OLD_DOOR is 14 bytes;
        # the count is a single byte, not uint16).
        if flags & MAP_DOORTABLE_SAVED:
            if pos + 1 > end:
                return bail("doortable_count_truncated")
            dt_count = data[pos]
            out["appendix_doortable_count"] = dt_count
            pos += 1 + 14 * dt_count
            if pos > end:
                return bail("doortable_records_overrun")

        # 8. EDGEPOINTS — 8 sections. Each: uint16 size + uint16 middle +
        # size * element, where element is INT16 (2 bytes) for major<7.0 and
        # INT32 (4 bytes) for major>=7.0.
        if flags & MAP_EDGEPOINTS_SAVED:
            record_size = 2 if major < 7.0 else 4
            total_edges = 0
            for _ in range(8):  # N/E/S/W x primary/secondary
                if pos + 4 > end:
                    return bail("edgepoint_section_header_truncated")
                ep_size = safe_uint16(pos)
                # uint16 middle ignored — just advance
                pos += 4
                if ep_size:
                    pos += ep_size * record_size
                    if pos > end:
                        return bail("edgepoint_records_overrun")
                total_edges += ep_size
            out["appendix_edgepoint_count"] = total_edges

        # 9. SCHEDULES — variable-size blocker.
        if flags & MAP_NPCSCHEDULES_SAVED:
            return bail("schedules_records")

    except struct.error as e:
        return bail(f"struct_error:{e}")

    return out


def parse_dat_full(
    data: bytes,
    source_name: str = "<bytes>",
    items_table: Dict[int, int] | None = None,
) -> Dict[str, Any]:
    """Parse a JA2 .dat map from raw bytes. Returns a dict with everything the
    corpus scanner needs. Raises DatParseError on malformed data.

    Phase WB: `items_table` is the install-resolved {usItem: usItemClass} map
    used by Path A's IsActiveLBE check. None disables LBE detection (Path A
    bails with `items_lbe_no_table` on first LBE-eligible item; Path B is
    unaffected since it has no nested LBE recursion).
    """
    n = len(data)
    if n < 99 + 17:
        raise DatParseError(f"file too small ({n} bytes)")

    # --- Header ----------------------------------------------------------
    try:
        major = struct.unpack_from("<f", data, 0)[0]
    except struct.error as e:
        raise DatParseError(f"unreadable header: {e}")
    minor = data[4]

    if not (4.0 <= major <= 9.0):
        raise DatParseError(f"implausible major version {major!r}")

    if major >= 7.0:
        rows  = struct.unpack_from("<i", data, 5)[0]
        cols  = struct.unpack_from("<i", data, 9)[0]
        flags = struct.unpack_from("<I", data, 13)[0]
        tileset = struct.unpack_from("<I", data, 17)[0]
        header_len = 25  # 4 + 1 + 4 + 4 + 4 + 4 + 4
    else:
        rows, cols = 160, 160
        flags   = struct.unpack_from("<I", data, 5)[0]
        tileset = struct.unpack_from("<I", data, 9)[0]
        header_len = 17  # 4 + 1 + 4 + 4 + 4

    if rows <= 0 or cols <= 0 or rows > 1024 or cols > 1024:
        raise DatParseError(f"implausible dimensions {rows}x{cols}")

    world_max = rows * cols
    heights_size = 2 * world_max
    lc_off = header_len + heights_size

    if lc_off + 4 * world_max > n:
        raise DatParseError(
            f"layer-count region runs past EOF: lc_off+4*WORLD_MAX = "
            f"{lc_off + 4 * world_max} but file is {n}")

    # --- Per-tile heights (1 byte each, stored in a 2-byte slot) --------
    # Phase E1.5 Phase 2B: previously skipped via the `lc_off` jump; now
    # parsed and exposed for height-statistics analysis in scan_map.
    #
    # Engine source has a known bug: worlddef.h:272 declares MAPELEMENT.sHeight
    # as UINT8 (1 byte), but worlddef.cpp:2976-2980 reads `sizeof(INT16)` (2
    # bytes) per tile, and worlddef.cpp:1877 writes `sizeof(INT16)`. The save
    # writes [sHeight_byte, next_struct_field_byte] per tile, and the load
    # over-copies 2 bytes into a 1-byte field (with the high byte landing in
    # the adjacent struct field). Net effect: the file reserves 2 bytes per
    # tile for height, but only the LOW byte is the real height — the HIGH
    # byte is junk from whatever MAPELEMENT field happened to sit next to
    # sHeight at save time.
    #
    # We extract the meaningful low byte via [::2] slicing. Values are
    # typically WORLD_BASE_HEIGHT (0) for flat ground or WORLD_CLIFF_HEIGHT
    # (80) for cliff tops.
    heights_region = data[header_len:header_len + heights_size]
    heights = list(heights_region[::2])
    # The high byte of each 2-byte slot is the engine's UINT8-read-as-INT16
    # over-read (it lands in the adjacent MAPELEMENT field `ubAdjacentSoldierCnt`,
    # runtime combat state — not load-critical, not engine-meaningful). We KEEP
    # it as `heights_high` so the writer can re-emit the heights region
    # byte-identically — only an intentionally-edited low byte differs —
    # instead of a verbatim passthrough that can't support height edits.
    heights_high = list(heights_region[1::2])

    # --- Per-tile layer counts + world_flags ----------------------------
    n_land    = [0] * world_max
    n_obj     = [0] * world_max
    n_struct  = [0] * world_max
    n_shadow  = [0] * world_max
    n_roof    = [0] * world_max
    n_onroof  = [0] * world_max
    world_flags = [0] * world_max  # high nibble of byte 0
    unused_nib  = [0] * world_max  # high nibble of byte 3

    for i in range(world_max):
        b0 = data[lc_off + i*4]
        b1 = data[lc_off + i*4 + 1]
        b2 = data[lc_off + i*4 + 2]
        b3 = data[lc_off + i*4 + 3]
        n_land[i]    = b0 & 0xF
        world_flags[i] = b0 >> 4
        n_obj[i]     = b1 & 0xF
        n_struct[i]  = b1 >> 4
        n_shadow[i]  = b2 & 0xF
        n_roof[i]    = b2 >> 4
        n_onroof[i]  = b3 & 0xF
        unused_nib[i] = b3 >> 4

    # --- 6 layer passes -------------------------------------------------
    pos = lc_off + 4 * world_max

    def read_pass_2byte(counts: List[int]) -> List[List[Tuple[int, int]]]:
        nonlocal pos
        out = [[] for _ in range(world_max)]
        for i in range(world_max):
            c = counts[i]
            if c == 0:
                continue
            if pos + 2*c > n:
                raise DatParseError(
                    f"2-byte pass ran past EOF at tile {i} (pos={pos}, n={n})")
            for _ in range(c):
                t = data[pos]
                s = data[pos+1]
                pos += 2
                out[i].append((t, s))
        return out

    def read_pass_object() -> List[List[Tuple[int, int]]]:
        nonlocal pos
        out = [[] for _ in range(world_max)]
        for i in range(world_max):
            c = n_obj[i]
            if c == 0:
                continue
            if pos + 3*c > n:
                raise DatParseError(
                    f"object pass ran past EOF at tile {i} (pos={pos}, n={n})")
            for _ in range(c):
                t = data[pos]
                s = struct.unpack_from("<H", data, pos+1)[0]
                pos += 3
                out[i].append((t, s))
        return out

    try:
        land    = read_pass_2byte(n_land)
        objs    = read_pass_object()
        structs = read_pass_2byte(n_struct)
        shadows = read_pass_2byte(n_shadow)
        roofs   = read_pass_2byte(n_roof)
        onroofs = read_pass_2byte(n_onroof)
    except DatParseError:
        raise

    # --- Room info ------------------------------------------------------
    # 1 byte per tile when minor < 29, 2 bytes per tile when minor >= 29
    room_bytes_per_tile = 2 if minor >= 29 else 1
    room_size = world_max * room_bytes_per_tile
    if pos + room_size > n:
        raise DatParseError(
            f"not enough bytes for room info: pos={pos}, need {room_size}, "
            f"have {n - pos}")

    rooms = [0] * world_max
    if room_bytes_per_tile == 1:
        for i in range(world_max):
            rooms[i] = data[pos + i]
    else:
        for i in range(world_max):
            rooms[i] = struct.unpack_from("<H", data, pos + i*2)[0]
    pos += room_size

    # --- MapInformation tail --------------------------------------------
    # The engine calls LoadMapInformation() between LoadMapLights() and
    # LoadExitGrids() per worlddef.cpp:3204-3236. For maps with appendix
    # sections (flags != 0) the tail sits in the middle of variable-sized
    # blocks (items, lights) we don't parse, so we can't locate it without
    # parsing those.
    #
    # Strategy: extract the tail ONLY when there's no appendix to navigate.
    # That covers:
    #   * flags == 0: tail starts immediately after room info
    #     (size 100 bytes for major < 7.0, 32 bytes for major >= 7.0)
    #   * everything else: tail is unrecoverable, return None
    tail = None
    if flags == 0:
        if major < 7.0:
            tail_size = 100  # _OLD_MAPCREATE_STRUCT: MSVC align-2 rounds 99 raw fields to 100
        else:
            tail_size = 32  # sizeof(MAPCREATE_STRUCT) with MSVC 4-byte alignment
        if pos + tail_size <= n:
            tb = data[pos:pos + tail_size]
            if major < 7.0:
                tail = {
                    "sNorthGridNo": struct.unpack_from("<h", tb, 0)[0],
                    "sEastGridNo":  struct.unpack_from("<h", tb, 2)[0],
                    "sSouthGridNo": struct.unpack_from("<h", tb, 4)[0],
                    "sWestGridNo":  struct.unpack_from("<h", tb, 6)[0],
                    "ubNumIndividuals":      tb[8],
                    "ubMapVersion":          tb[9],
                    "ubRestrictedScrollID":  tb[10],
                    "ubEditorSmoothingType": tb[11],
                    "sCenterGridNo":   struct.unpack_from("<h", tb, 12)[0],
                    "sIsolatedGridNo": struct.unpack_from("<h", tb, 14)[0],
                    "_struct": "_OLD_MAPCREATE_STRUCT",
                }
            else:
                tail = {
                    "sNorthGridNo":   struct.unpack_from("<i", tb, 0)[0],
                    "sEastGridNo":    struct.unpack_from("<i", tb, 4)[0],
                    "sSouthGridNo":   struct.unpack_from("<i", tb, 8)[0],
                    "sWestGridNo":    struct.unpack_from("<i", tb, 12)[0],
                    "sCenterGridNo":  struct.unpack_from("<i", tb, 16)[0],
                    "sIsolatedGridNo":struct.unpack_from("<i", tb, 20)[0],
                    "ubNumIndividuals":      struct.unpack_from("<H", tb, 24)[0],
                    "ubMapVersion":          tb[26],
                    "ubRestrictedScrollID":  tb[27],
                    "ubEditorSmoothingType": tb[28],
                    "_struct": "MAPCREATE_STRUCT",
                }
        # If the tail doesn't fit, leave it None.

    # Phase E1.5 Phase 2C: best-effort appendix parse.
    appendix_offset = pos
    appendix_info = parse_appendix_minimal(
        data, appendix_offset, flags, major, minor, items_table=items_table
    )

    return {
        "source": source_name,
        "size_bytes": n,
        "major": major,
        "minor": minor,
        "rows": rows,
        "cols": cols,
        "flags": flags,
        "tileset": tileset,
        "header_len": header_len,
        "appendix_offset": appendix_offset,
        **appendix_info,
        "counts": {
            "land":   sum(n_land),
            "obj":    sum(n_obj),
            "struct": sum(n_struct),
            "shadow": sum(n_shadow),
            "roof":   sum(n_roof),
            "onroof": sum(n_onroof),
        },
        "n_per_tile": {
            "land":   n_land,
            "obj":    n_obj,
            "struct": n_struct,
            "shadow": n_shadow,
            "roof":   n_roof,
            "onroof": n_onroof,
        },
        "world_flags": world_flags,
        "unused_nib":  unused_nib,
        "heights":     heights,
        "heights_high": heights_high,
        "land":    land,
        "objs":    objs,
        "structs": structs,
        "shadows": shadows,
        "roofs":   roofs,
        "onroofs": onroofs,
        "rooms":   rooms,
        "room_bytes_per_tile": room_bytes_per_tile,
        "tail": tail,
        "bytes_consumed_before_room_info": pos - room_size,
        "appendix_bytes": n - pos,
    }


def parse_dat_file(path) -> Dict[str, Any]:
    from pathlib import Path
    p = Path(path)
    return parse_dat_full(p.read_bytes(), str(p))


if __name__ == "__main__":
    # Smoke test: parse a .dat sector passed on the command line.
    import sys, json
    from pathlib import Path
    if len(sys.argv) <= 1:
        print("usage: python parse_dat_ext.py <path-to-sector.dat>")
        sys.exit(2)
    p = Path(sys.argv[1])
    if not p.is_file():
        print(f"ERROR: {p} not found"); sys.exit(1)
    parsed = parse_dat_full(p.read_bytes(), str(p))
    # Drop big arrays from the smoke-test print
    for k in ("land", "objs", "structs", "shadows", "roofs", "onroofs",
              "rooms", "n_per_tile", "world_flags", "unused_nib", "heights"):
        v = parsed[k]
        if isinstance(v, list):
            parsed[k] = f"<list len={len(v)}>"
        else:
            parsed[k] = {kk: f"<list len={len(vv)}>" for kk, vv in v.items()}
    print(json.dumps(parsed, indent=2, default=str))
