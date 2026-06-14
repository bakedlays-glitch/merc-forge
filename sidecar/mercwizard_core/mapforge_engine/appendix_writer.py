"""JA2 1.13 .dat APPENDIX serializer (modern major>=7.0 map format).

The tile/room/header writer (dat_writer.py) passes the appendix through
verbatim. This module SYNTHESIZES the appendix so MapForge can AUTHOR
exit grids, ambient lighting, and edge entry points on a map that had
none (e.g. a flags=0 sector like A9/Junktown).

Byte layout source-verified against the engine (engine-navigator crawl,
2026-06-14) — NOT paraphrased; a wrong byte crashes the map on load:

  Flag bits (worlddef.cpp:60-68):
    FULLSOLDIER=0x01 WORLDLIGHTS=0x04 WORLDITEMS=0x08 EXITGRIDS=0x10
    DOORTABLE=0x20 EDGEPOINTS=0x40 AMBIENTLIGHTLEVEL=0x80 NPCSCHEDULES=0x100

  Appendix write order (worlddef.cpp SaveWorld 2255-2296), each gated by
  its flag EXCEPT the MapInfo tail which is UNCONDITIONAL:
    1 items(0x08)  2 ambient(0x80,3B)  3 lights(0x04)
    4 MAPINFO TAIL (always, 32B)       5 soldiers(0x01)
    6 exitgrids(0x10)  7 doortable(0x20)  8 edgepoints(0x40)  9 schedules(0x100)
  => the tail sits in the MIDDLE, before exit grids. Reorder = corruption.

  Ambient (worlddef.cpp:2261-2266): 3 bytes gfBasement,gfCaves,ubAmbientLightLevel.

  MAPCREATE_STRUCT tail (Map Information.h:40-57), sizeof=32, INT32 gridnos:
    sNorth,sEast,sSouth,sWest,sCenter,sIsolated (6x i32),
    ubNumIndividuals (UINT16!), ubMapVersion,ubRestrictedScrollID,
    ubEditorSmoothingType (3x u8), +3 pad  ->  '<iiiiiiHBBBxxx'
    Unused entry points = NOWHERE (-1), not 0.

  EXITGRID (Exit Grids.h:16-31), sizeof=12, written whole incl. pad:
    iMapIndex(i32 source gridno), usGridNo(i32 dest gridno),
    ubGotoSectorX,Y,Z (3x u8), +1 pad  ->  '<iiBBBx'
    Section = UINT16 count + 12*n.

We DO NOT emit edgepoints (0x40): the engine regenerates them at load from
the tail's entry points (worlddef.cpp:3256-3276) — exactly how a flags=0
map already behaves. We set a flag bit ONLY when we actually write its
section (required_flags discipline) so LoadWorld reads at the right offsets.
"""
from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional

NOWHERE = -1

MAP_FULLSOLDIER_SAVED       = 0x00000001
MAP_WORLDLIGHTS_SAVED       = 0x00000004
MAP_WORLDITEMS_SAVED        = 0x00000008
MAP_EXITGRIDS_SAVED         = 0x00000010
MAP_DOORTABLE_SAVED         = 0x00000020
MAP_EDGEPOINTS_SAVED        = 0x00000040
MAP_AMBIENTLIGHTLEVEL_SAVED = 0x00000080
MAP_NPCSCHEDULES_SAVED      = 0x00000100

_TAIL_FMT = "<iiiiiiHBBBxxx"   # 32 bytes
_EXITGRID_FMT = "<iiBBBx"      # 12 bytes
assert struct.calcsize(_TAIL_FMT) == 32
assert struct.calcsize(_EXITGRID_FMT) == 12


def pack_ambient(basement: int, caves: int, level: int) -> bytes:
    """3-byte ambient section (worlddef.cpp:2261-2266)."""
    return struct.pack("<BBB", basement & 0xFF, caves & 0xFF, level & 0xFF)


def pack_map_tail(
    north: int = NOWHERE, east: int = NOWHERE, south: int = NOWHERE,
    west: int = NOWHERE, center: int = NOWHERE, isolated: int = NOWHERE,
    num_individuals: int = 0, map_version: int = 31,
    restricted_scroll_id: int = 0, smoothing_type: int = 0,
) -> bytes:
    """32-byte MAPCREATE_STRUCT (modern major>=7.0). map_version must be the
    file's minor version (>=15, and >=17 to avoid the legacy edgepoint path)."""
    return struct.pack(
        _TAIL_FMT, north, east, south, west, center, isolated,
        num_individuals & 0xFFFF, map_version & 0xFF,
        restricted_scroll_id & 0xFF, smoothing_type & 0xFF,
    )


def pack_exit_grids(grids: List[Dict[str, int]]) -> bytes:
    """UINT16 count + 12 bytes per exit grid.
    Each grid dict: map_index, grid_no, sx, sy, sz."""
    out = bytearray(struct.pack("<H", len(grids) & 0xFFFF))
    for g in grids:
        out += struct.pack(
            _EXITGRID_FMT,
            int(g["map_index"]), int(g["grid_no"]),
            int(g["sx"]) & 0xFF, int(g["sy"]) & 0xFF, int(g["sz"]) & 0xFF,
        )
    return bytes(out)


def build_appendix(
    model: Dict[str, Any],
    existing_tail: Optional[Dict[str, Any]] = None,
    major: float = 7.0,
) -> tuple[bytes, int]:
    """Assemble the full appendix bytes + the flags word to set.

    model keys (all optional):
      ambient: {"basement":0,"caves":0,"level":int} | None
      exit_grids: [ {map_index,grid_no,sx,sy,sz}, ... ]
      tail: {north,east,south,west,center,isolated,num_individuals,
             map_version,restricted_scroll_id,smoothing_type}  (overrides;
             missing keys fall back to existing_tail, then to defaults)

    existing_tail: the parsed tail dict (to preserve fields not overridden).
    Returns (appendix_bytes, flags). Only the sections we write get flagged.
    """
    if major < 7.0:
        raise ValueError("build_appendix emits the modern (major>=7.0) format only")
    flags = 0
    out = bytearray()

    # 2. ambient (before the tail)
    amb = model.get("ambient")
    if amb is not None:
        out += pack_ambient(amb.get("basement", 0), amb.get("caves", 0),
                            amb.get("level", 0))
        flags |= MAP_AMBIENTLIGHTLEVEL_SAVED

    # 4. MapInfo tail (UNCONDITIONAL)
    et = existing_tail or {}
    t = model.get("tail", {})
    def pick(k, default):
        if k in t:
            return t[k]
        # parsed tail uses the engine field names
        engine_key = {
            "north": "sNorthGridNo", "east": "sEastGridNo",
            "south": "sSouthGridNo", "west": "sWestGridNo",
            "center": "sCenterGridNo", "isolated": "sIsolatedGridNo",
            "num_individuals": "ubNumIndividuals", "map_version": "ubMapVersion",
            "restricted_scroll_id": "ubRestrictedScrollID",
            "smoothing_type": "ubEditorSmoothingType",
        }[k]
        return et.get(engine_key, default)
    out += pack_map_tail(
        north=pick("north", NOWHERE), east=pick("east", NOWHERE),
        south=pick("south", NOWHERE), west=pick("west", NOWHERE),
        center=pick("center", NOWHERE), isolated=pick("isolated", NOWHERE),
        num_individuals=pick("num_individuals", 0),
        map_version=pick("map_version", 31),
        restricted_scroll_id=pick("restricted_scroll_id", 0),
        smoothing_type=pick("smoothing_type", 0),
    )

    # 6. exit grids (after the tail)
    grids = model.get("exit_grids") or []
    if grids:
        out += pack_exit_grids(grids)
        flags |= MAP_EXITGRIDS_SAVED

    return bytes(out), flags
