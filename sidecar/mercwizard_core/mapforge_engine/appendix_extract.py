"""Read-only extraction of positioned appendix entities for the MapForge
tactical overlay. Walks the appendix region (never writes), returns entity
lists keyed by tile position. Scope: items + entry points + exit grids;
later sections (lights records, soldiers, doors, edgepoints) are marked
`blocked_at` and deferred. See docs/superpowers/specs/2026-06-20-mapforge-tactical-overlay-design.md.
"""
from __future__ import annotations

import struct
from typing import Any, Dict

from .parse_world_items import parse_world_items
from . import appendix_writer as AW


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
        "items": [], "entry_points": [], "exit_grids": [],
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

    # 2. AMBIENT — fixed 3 bytes, no count.
    if flags & AW.MAP_AMBIENTLIGHTLEVEL_SAVED:
        if pos + 3 > n:
            return blocked("ambient_truncated")
        pos += 3
        out["reached"].append("ambient")

    # 3. LIGHTS — header is parseable; records deferred to a later plan.
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
        if light_count > 0:
            return blocked("lights_records")

    # 4. MAPINFO TAIL (unconditional) — entry points
    tail_size = 32 if major >= 7.0 else 99
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
    pos += tail_size
    out["reached"].append("mapinfo")

    # 5. SOLDIERS — deferred to a later plan.
    if flags & AW.MAP_FULLSOLDIER_SAVED:
        return blocked("soldiers")

    # 6. EXIT GRIDS — uint16 count + 12-byte records (<iiBBBx).
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

    return out
