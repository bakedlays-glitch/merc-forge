"""Read-only extraction of positioned appendix entities for the MapForge
tactical overlay. Walks the appendix region (never writes), returns entity
lists keyed by tile position. Scope: items, entry points, exit grids, world
lights, soldiers (incl. legacy 1040-byte detailed-placement skip), doors, and
edgepoints. Only schedules and the modern (major>=7.0) variable-inventory
detailed-soldier path remain deferred/bailed. See
docs/superpowers/specs/2026-06-20-mapforge-tactical-overlay-design.md.
"""
from __future__ import annotations

import struct
from typing import Any, Dict

from .parse_world_items import parse_world_items
from . import appendix_writer as AW

TEAM_LABELS = {0: "player", 1: "enemy", 2: "creature", 3: "militia", 4: "civilian"}

# Basic soldier record (BASIC_SOLDIERCREATE_STRUCT). Vanilla/major<7.0 = 52 bytes;
# modern/major>=7.0 = 64 bytes. Offsets per soldier-layout-research.md.
_SOLDIER_OLD = {"size": 52, "grid_fmt": "<h", "grid_off": 2, "team_off": 4,
                "dir_off": 7, "class_off": 34}
_SOLDIER_NEW = {"size": 64, "grid_fmt": "<i", "grid_off": 4, "team_off": 8,
                "dir_off": 11, "class_off": 58}

# LIGHT_SPRITE (lighting.h:90-97), MSVC x86-32 4-byte align: 5*INT16 (10B) +
# 2B pad + INT32 iTemplate + 2*UINT32 = 24 bytes. Written verbatim per light by
# SaveMapLights (worlddef.cpp:4168, sizeof(LIGHT_SPRITE)).
_LIGHT_SPRITE_SIZE = 24

# Legacy/vanilla detailed SOLDIERCREATE block = fixed 1040-byte POD, no trailing
# inventory (SaveLoadGame.cpp:1064-1071). Modern (major>=6.0 & minor>26) appends a
# variable inventory and can't be fixed-skipped. See detailed-placement-research.md.
_DETAILED_POD_OLD = 1040


def _xy(gridno: int, cols: int) -> tuple[int, int]:
    return gridno % cols, gridno // cols


def extract_appendix_entities(data: bytes, parsed: Dict[str, Any]) -> Dict[str, Any]:
    flags = parsed["flags"]
    major = parsed["major"]
    minor = parsed["minor"]
    cols = parsed["cols"]
    rows = parsed["rows"]
    pos = parsed["appendix_offset"]
    n = len(data)

    out: Dict[str, Any] = {
        "items": [], "entry_points": [], "exit_grids": [], "soldiers": [],
        "lights": [], "doors": [], "edgepoints": [], "schedules": [],
        "reached": [], "blocked_at": None, "rows": rows, "cols": cols,
    }

    def blocked(reason: str) -> Dict[str, Any]:
        out["blocked_at"] = reason
        return out

    # 1. ITEMS
    if flags & AW.MAP_WORLDITEMS_SAVED:
        if pos + 4 > n:
            return blocked("items_count_truncated")
        count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        items, pos, bail = parse_world_items(data, pos, count, major, minor,
                                             capture="summary")
        for it in items:
            g = it["gridno"]
            if not it.get("exists", True) or g < 0:
                continue
            x, y = _xy(g, cols)
            out["items"].append({"gridno": g, "x": x, "y": y,
                                 "usItem": it["usItem"], "level": it["level"]})
        out["reached"].append("items")
        if bail:
            return blocked(bail)

    # 2. AMBIENT â€” fixed 3 bytes, no count.
    if flags & AW.MAP_AMBIENTLIGHTLEVEL_SAVED:
        if pos + 3 > n:
            return blocked("ambient_truncated")
        pos += 3
        out["reached"].append("ambient")

    # 3. LIGHTS â€” header + per-light records. See lights-layout-research.md.
    #   header: u8 ubNumColors + ubNumColors*SGPPaletteEntry(4B) + u16 usNumLights.
    #   per-light (SaveMapLights, worlddef.cpp:4153-4176, repeated usNumLights):
    #     LIGHT_SPRITE (24B, lighting.h:90-97) + u8 ubStrLen + ubStrLen string
    #     bytes (template filename incl. trailing NUL; ubStrLen = strlen+1).
    #   LIGHT_SPRITE fields (MSVC x86-32, 4B align): iX,iY,iOldX,iOldY,iAnimSpeed
    #     (5*INT16 @0..9), 2B pad @10, iTemplate(INT32 @12), uiFlags(@16),
    #     uiLightType(@20). Position = iX/iY (tile col/row), uiFlags @ +16.
    if flags & AW.MAP_WORLDLIGHTS_SAVED:
        if pos + 1 > n:
            return blocked("lights_header_truncated")
        num_colors = data[pos]
        pos += 1
        if pos + 4 * num_colors + 2 > n:
            return blocked("lights_palette_truncated")
        pos += 4 * num_colors
        light_count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        out["reached"].append("lights_header")
        for _ in range(light_count):
            if pos + _LIGHT_SPRITE_SIZE + 1 > n:
                return blocked("light_records_overrun")
            iX, iY = struct.unpack_from("<hh", data, pos)
            pos += _LIGHT_SPRITE_SIZE
            str_len = data[pos]
            pos += 1
            if pos + str_len > n:
                return blocked("light_string_overrun")
            tmpl = data[pos:pos + str_len].split(b"\x00", 1)[0].decode("ascii", "replace")
            pos += str_len
            # iX/iY are tile col/row (not a gridno); plot if in-bounds.
            if 0 <= iX < cols and 0 <= iY < rows:
                out["lights"].append({"x": iX, "y": iY,
                                      "gridno": iY * cols + iX, "template": tmpl})
        out["reached"].append("lights")

    # 4. MAPINFO TAIL (unconditional) â€” entry points
    tail_size = 32 if major >= 7.0 else 100  # _OLD_MAPCREATE_STRUCT sizeof = 100 (99 raw, align-2)
    if pos + tail_size > n:
        return blocked("mapinfo_truncated")
    if major >= 7.0:
        north, east, south, west, center, isolated = struct.unpack_from("<6i", data, pos)
    else:
        north, east, south, west = struct.unpack_from("<4h", data, pos)
        center, isolated = struct.unpack_from("<2h", data, pos + 12)
    for kind, g in (("north", north), ("east", east), ("south", south),
                    ("west", west), ("center", center), ("isolated", isolated)):
        if g < 0:
            continue
        x, y = _xy(g, cols)
        out["entry_points"].append({"kind": kind, "gridno": g, "x": x, "y": y})
    num_individuals = data[pos + 8] if major < 7.0 else struct.unpack_from("<H", data, pos + 24)[0]
    pos += tail_size
    out["reached"].append("mapinfo")

    # 5. SOLDIERS â€” fixed-stride basic placements (BASIC_SOLDIERCREATE_STRUCT).
    # Count is the MapInfo tail's ubNumIndividuals; no marker bytes around records.
    # On the legacy path (major<6.0 or minor<=26) a detailed block is a fixed
    # 1040-byte POD with no trailing inventory â€” we read the basic fields, emit
    # the soldier, then skip the POD.  On the modern path the detailed block
    # carries a variable inventory and cannot be fixed-skipped, so we still bail.
    if flags & AW.MAP_FULLSOLDIER_SAVED:
        spec = _SOLDIER_NEW if major >= 7.0 else _SOLDIER_OLD
        is_legacy = major < 6.0 or minor <= 26
        for _ in range(num_individuals):
            if pos + spec["size"] > n:
                return blocked("soldier_records_overrun")
            f_detailed = data[pos]
            g = struct.unpack_from(spec["grid_fmt"], data, pos + spec["grid_off"])[0]
            team = struct.unpack_from("<b", data, pos + spec["team_off"])[0]
            facing = data[pos + spec["dir_off"]]
            body = data[pos + (10 if major < 7.0 else 14)]
            sclass = data[pos + spec["class_off"]]
            pos += spec["size"]
            if g >= 0:
                x, y = _xy(g, cols)
                out["soldiers"].append({"gridno": g, "x": x, "y": y, "team": team,
                                        "team_label": TEAM_LABELS.get(team, "other"),
                                        "facing": facing, "soldier_class": sclass,
                                        "body_type": body})
            if f_detailed == 1:
                if is_legacy:
                    if pos + _DETAILED_POD_OLD > n:
                        return blocked("soldier_detailed_overrun")
                    pos += _DETAILED_POD_OLD
                else:
                    return blocked("soldier_detailed")
        out["reached"].append("soldiers")

    # 6. EXIT GRIDS â€” uint16 count + 12-byte records (<iiBBBx).
    if flags & AW.MAP_EXITGRIDS_SAVED:
        if pos + 2 > n:
            return blocked("exitgrid_count_truncated")
        eg_count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        for _ in range(eg_count):
            if pos + 12 > n:
                return blocked("exitgrid_records_overrun")
            map_index, grid_no, sx, sy, sz = struct.unpack_from("<iiBBB", data, pos)
            pos += 12  # 12B record: <iiBBBx>, trailing pad byte not unpacked
            x, y = _xy(map_index, cols)
            out["exit_grids"].append({"gridno": map_index, "x": x, "y": y,
                                      "dest_gridno": grid_no, "sx": sx, "sy": sy, "sz": sz})
        out["reached"].append("exitgrids")

    # 7. DOOR TABLE â€” uint8 count + 14-byte _OLD_DOOR records.
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

    # 8. EDGEPOINTS â€” 8 sub-sections (primary N/E/S/W, secondary N/E/S/W).
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

    # 9. SCHEDULES â€” uint8 count + 36-byte _OLD_SCHEDULENODE (major<7.0).
    # Plot usData1[j] waypoint gridnos (valid in-map only). v7 records (52/56B)
    # are advanced-only (field offsets derivation-only â€” no stock v7 map).
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
                    if 0 <= g < world_max:
                        x, y = _xy(g, cols)
                        out["schedules"].append({"gridno": g, "x": x, "y": y,
                                                 "schedule_id": sid,
                                                 "action": data[pos + 28 + j]})
            pos += rec_size
        out["reached"].append("schedules")

    return out
