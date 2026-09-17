"""socket_lut.py — engine smoothing LUTs as tile-connection ("LEGO socket") rules.

Transcribed VERBATIM from the JA2 1.13 editor source (the same logic the engine used
to PLACE every authored tile), so placement is correct-by-construction and validation
is structural — not statistical. Validated on 297 Urban Chaos maps:
  walls 0.38% FP, terrain 0.23% FP (1.67M tiles), water 4.9% (hand-authored shorelines).
See Headless_Compiler/map_corpus/SOCKET_RULES_RESULTS.md.

This module is PURE (no parse/IO deps) so generators + validators can import it freely.
It exposes both directions:
  - FORWARD  (generation): neighbour pattern -> correct sub  (mirrors SmoothTerrain etc.)
  - INVERSE  (validation): a placed sub -> the boundary it implies -> check neighbours

Layer 1 here: TERRAIN fringes (smooth.cpp gbSmoothStruct + SmoothTerrain).
Walls + water tables live in Headless_Compiler/map_corpus/engine_socket_rules.py and
move here as their generators land.
"""
from __future__ import annotations
from typing import Optional
import random

# Smoothable land textures: FIRSTTEXTURE..SEVENTHTEXTURE = slots 0..6.
# Water (7,8) uses the separate gbSmoothWaterStruct. 60-63 = floor/room markers.
TEXTURE_TYPES = frozenset(range(0, 7))

# gbSmoothStruct rows (smooth.cpp:20-38), verbatim: [code, nvar, v1, v2, v3].
# code = prime sum over CARDINAL neighbours that LACK this texture: +3 N, +5 E,
# +7 S, +11 W. The engine picks one of the first `nvar` variants for that code.
_GB_SMOOTH_STRUCT = [
    (3, 2, [12, 27, 12]), (5, 2, [15, 30, 39]), (7, 2, [17, 32, 41]),
    (11, 2, [14, 29, 14]), (8, 2, [13, 28, 38]), (15, 1, [19, 0, 43]),
    (26, 1, [20, 0, 44]), (12, 2, [18, 33, 42]), (23, 1, [21, 0, 45]),
    (18, 2, [16, 31, 40]), (21, 1, [22, 0, 46]), (14, 2, [11, 26, 11]),
    (19, 1, [23, 0, 47]), (16, 2, [24, 34, 48]), (10, 2, [25, 35, 49]),
]
# FORWARD pick set: exactly the `nvar` variants the engine randomises among.
TERR_CODE_VARIANTS = {code: vs[:nvar] for (code, nvar, vs) in _GB_SMOOTH_STRUCT}
# INVERSE/validation set: permissive union of all listed subs (incl. the trailing
# variant slot), so authored fringes aren't false-flagged. sub -> code.
TERR_CODE_SUBS = {code: set(v for v in vs if v) for (code, nvar, vs) in _GB_SMOOTH_STRUCT}
TERR_SUB2CODE = {s: c for c, subs in TERR_CODE_SUBS.items() for s in subs}
TERR_FRINGE_SUBS = frozenset(TERR_SUB2CODE)
FULL_TILE_SUBS = frozenset(range(1, 11))   # subs 1-10 = interior/full tiles


def land_has_type(land_grid, gn: int, t: int) -> bool:
    """True if the land cell at gridno gn holds an entry of texture slot t."""
    if not (0 <= gn < len(land_grid)):
        return False
    for e in land_grid[gn]:
        if int(e[0]) == t:
            return True
    return False


def boundary_code(land_grid, gn: int, cols: int, rows: int, t: int) -> int:
    """SmoothTerrain's prime boundary code for texture t at gn, from real neighbours.
    A side adds its prime only if IN-BOUNDS and the neighbour LACKS texture t."""
    y, x = divmod(gn, cols)
    code = 0
    if y - 1 >= 0 and not land_has_type(land_grid, gn - cols, t):
        code += 3                                   # N
    if x + 1 < cols and not land_has_type(land_grid, gn + 1, t):
        code += 5                                   # E
    if y + 1 < rows and not land_has_type(land_grid, gn + cols, t):
        code += 7                                   # S
    if x - 1 >= 0 and not land_has_type(land_grid, gn - 1, t):
        code += 11                                  # W
    return code


def pick_terrain_sub(code: int, rng: random.Random) -> Optional[int]:
    """FORWARD: given a boundary code, return the correct fringe sub (engine-faithful
    random pick among the variants), or None when code==0 (interior -> a full tile)."""
    variants = TERR_CODE_VARIANTS.get(code)
    if not variants:
        return None                                 # no boundary (or unknown) -> full tile
    return variants[rng.randrange(len(variants))]


def terrain_sub_is_legal(sub: int, actual_code: int) -> bool:
    """INVERSE/VALIDATION (lenient): is this placed sub legal for the boundary?
    A full-tile sub (1-10) is always legal (the engine leaves un-smoothed boundary
    full tiles in non-force mode); a fringe must match its boundary code."""
    if sub in FULL_TILE_SUBS:
        return True
    return sub in TERR_CODE_SUBS.get(actual_code, ())


def terrain_sub_matches(sub: int, actual_code: int) -> bool:
    """FORWARD/GENERATION (strict, force-smooth): does this sub EXACTLY fit the
    boundary? At a boundary (code>0) it must be the matching fringe (a full tile
    there needs smoothing); in the interior (code==0) it must be a full tile."""
    if actual_code == 0:
        return sub in FULL_TILE_SUBS
    return sub in TERR_CODE_SUBS.get(actual_code, ())


# =====================================================================================
# WALL sockets  (source: Editor/newsmooth.cpp gbWallTileLUT + BuildWallPiece)
# =====================================================================================
# Wall TYPE families in the .dat: FIRSTWALL..FOURTHWALL = 36..39 (doors 40-43 reuse
# the same orientation classes; handled later). gbWallTileLUT class -> subframe set:
WALL_TYPES = frozenset((36, 37, 38, 39))
WALL_CLASS_SUBS = {
    "INTERIOR_L": {10, 11, 12, 27, 28, 29}, "INTERIOR_R": {7, 8, 9, 24, 25, 26},
    "EXTERIOR_L": {4, 5, 6, 21, 22, 23},    "EXTERIOR_R": {1, 2, 3, 18, 19, 20},
    "INTERIOR_CORNER": {14}, "INTERIOR_BOTTOMEND": {15}, "EXTERIOR_BOTTOMEND": {13},
    "INTERIOR_EXTENDED": {16}, "EXTERIOR_EXTENDED": {57},
    "INTERIOR_EXTENDED_BOTTOMEND": {56}, "EXTERIOR_EXTENDED_BOTTOMEND": {17},
}
WALL_SUB2CLASS = {s: c for c, subs in WALL_CLASS_SUBS.items() for s in subs}
# L-family straight walls run EW (gridno +-1); R-family run NS (+-cols).
CLASS_AXIS = {"INTERIOR_L": "EW", "EXTERIOR_L": "EW", "INTERIOR_R": "NS", "EXTERIOR_R": "NS"}
CLASS_FAMILY = {"INTERIOR_L": "L", "EXTERIOR_L": "L", "INTERIOR_R": "R", "EXTERIOR_R": "R"}
CLASS_FACE = {"INTERIOR_L": "INT", "INTERIOR_R": "INT", "EXTERIOR_L": "EXT", "EXTERIOR_R": "EXT"}
STRAIGHT_WALL_CLASSES = frozenset(CLASS_AXIS)
# (face, run-axis) -> the straight class for that combo (the forward fix target).
_WALL_TARGET = {("EXT", "EW"): "EXTERIOR_L", ("EXT", "NS"): "EXTERIOR_R",
                ("INT", "EW"): "INTERIOR_L", ("INT", "NS"): "INTERIOR_R"}
# Cave tilesets use the 256-entry bitmask, NOT this LUT -- skip wall rules there.
CAVE_TILESETS = frozenset((1,))


def wall_class(sub: int) -> Optional[str]:
    return WALL_SUB2CLASS.get(sub)


def wall_family_continuation(struct_grid, gn, cols, rows, walltype, family):
    """Count SAME-family same-walltype straight-wall neighbours on EW (+-1) and NS
    (+-cols). Only same-family neighbours continue a run; the other family crossing
    the tile is a junction, not a continuation. Returns (ew_count, ns_count)."""
    y, x = divmod(gn, cols)
    ew = ns = 0
    for dx in (1, -1):
        nx = x + dx
        if 0 <= nx < cols:
            for e in struct_grid[y * cols + nx]:
                if int(e[0]) == walltype and CLASS_FAMILY.get(WALL_SUB2CLASS.get(int(e[1]))) == family:
                    ew += 1
    for dy in (1, -1):
        ny = y + dy
        if 0 <= ny < rows:
            for e in struct_grid[ny * cols + x]:
                if int(e[0]) == walltype and CLASS_FAMILY.get(WALL_SUB2CLASS.get(int(e[1]))) == family:
                    ns += 1
    return ew, ns


def wall_run_axis_mismatch(cur_class, ew, ns):
    """Given a straight wall's class and its same-family neighbour counts, return the
    axis it ACTUALLY runs ('EW'/'NS') if that contradicts the class's expected axis,
    else None (correct, or no continuation = endpoint -> leave it)."""
    exp = CLASS_AXIS[cur_class]
    if exp == "EW":
        return "NS" if (ns > 0 and ew == 0) else None
    return "EW" if (ew > 0 and ns == 0) else None


def wall_target_sub(cur_class: str, run_axis: str, rng: random.Random) -> int:
    """FORWARD fix: a straight wall of cur_class actually running `run_axis` should be
    re-tiled to the same FACE on the correct axis. Returns a chosen sub of that class."""
    target = _WALL_TARGET[(CLASS_FACE[cur_class], run_axis)]
    variants = sorted(WALL_CLASS_SUBS[target])
    return variants[rng.randrange(len(variants))]


# =====================================================================================
# WATER / SHORELINE sockets  (source: Editor/smooth.cpp gbSmoothWaterStruct + SmoothWaterTerrain)
# =====================================================================================
# REGWATERTEXTURE = 7. 8-neighbour + self bitmask; a bit is SET when that cell HAS the
# water texture. bit weights (SmoothWaterTerrain:579-665):
WATER_TYPE = 7
WATER_BITS = {(-1, 0): 4, (0, 1): 64, (1, 0): 256, (0, -1): 16,
              (-1, 1): 8, (-1, -1): 2, (1, 1): 512, (1, -1): 128}
WATER_SELF_BIT = 32
# gbSmoothWaterStruct (smooth.cpp:48-90): [bitvalue, nvar, v1, v2]. Source warns
# bitvalues recur -> entries UNIONed. (subs are the shoreline fringe indices.)
_GB_SMOOTH_WATER = [
    (1020, [11]), (1000, [12]), (510, [13, 43]), (190, [14]), (894, [15]),
    (622, [16]), (1014, [17, 41]), (944, [18, 24]), (872, [19]), (992, [20]),
    (62, [21]), (190, [22, 14]), (620, [23]), (944, [24, 18]), (878, [25, 32]),
    (434, [28]), (110, [29]), (1010, [30]), (876, [31, 32]), (878, [32, 31]),
    (1004, [32, 31]), (1006, [33, 34]), (1008, [34, 33]), (1016, [33, 34]),
    (126, [35, 36]), (254, [35, 26]), (638, [36, 26]), (438, [38, 27]),
    (446, [37, 38]), (950, [37, 27]), (864, [39]), (1040, [40]), (1014, [41, 17]),
    (432, [42]), (510, [43, 13]), (54, [44]), (108, [45]),
]
WATER_CODE_SUBS = {}
for _c, _subs in _GB_SMOOTH_WATER:
    WATER_CODE_SUBS.setdefault(_c, set()).update(_subs)
WATER_CODE_VARIANTS = {c: sorted(s) for c, s in WATER_CODE_SUBS.items()}
WATER_FRINGE_SUBS = frozenset().union(*WATER_CODE_SUBS.values())


def water_bitvalue(land_grid, gn, cols, rows) -> int:
    """SmoothWaterTerrain's 8-neighbour+self bitmask for REGWATERTEXTURE at gn."""
    y, x = divmod(gn, cols)
    code = WATER_SELF_BIT if land_has_type(land_grid, gn, WATER_TYPE) else 0
    for (dy, dx), bit in WATER_BITS.items():
        ny, nx = y + dy, x + dx
        if 0 <= ny < rows and 0 <= nx < cols and land_has_type(land_grid, ny * cols + nx, WATER_TYPE):
            code |= bit
    return code


def pick_water_sub(bitvalue: int, rng: random.Random) -> Optional[int]:
    """FORWARD: shoreline fringe sub for a water bitvalue, or None (no table entry =
    interior/open water -> a full water tile)."""
    variants = WATER_CODE_VARIANTS.get(bitvalue)
    if not variants:
        return None
    return variants[rng.randrange(len(variants))]


def water_sub_matches(sub: int, bitvalue: int) -> bool:
    """FORWARD/GENERATION (strict): does this water sub fit the shoreline bitvalue?
    No table entry -> only a full tile fits (open water)."""
    allowed = WATER_CODE_SUBS.get(bitvalue)
    if allowed is None:
        return sub in FULL_TILE_SUBS
    return sub in allowed


def water_sub_is_legal(sub: int, bitvalue: int) -> bool:
    """INVERSE/VALIDATION (lenient): full tiles always legal; fringe must match."""
    if sub in FULL_TILE_SUBS:
        return True
    return sub in WATER_CODE_SUBS.get(bitvalue, ())


# =====================================================================================
# CAVE sockets  (source: Editor/newsmooth.cpp CalcNewCavePerimeterValue + GetCaveTileIndexFromPerimeterValue)
# =====================================================================================
# Cave tilesets (ts1) place every wall via an 8-neighbour cave-presence bitmask, NOT
# the gbWallTileLUT used above. Perimeter bits (verbatim): N 0x01 E 0x02 S 0x04 W 0x08
# NW 0x10 NE 0x20 SE 0x40 SW 0x80. A 256-case switch maps perimeter -> (wall type,
# subindex). FIRSTWALL=36 default; SECONDWALL=37 for single-cardinal dead-ends.
# Low-nibble-0 perimeters (no cardinal neighbour) -> stalagmite (no wall).
# Validated on 36 ts1 maps from vanilla Maps.slf: 2.80% deviation (interior-fill,
# hand-authored — like water, so cave is LUT-GENERATABLE but ADVISORY-validatable).
FIRSTWALL, SECONDWALL = 36, 37
CAVE_WALL_TYPES = frozenset((FIRSTWALL, SECONDWALL))
_CAVE_BITS = {(-1, 0): 0x01, (0, 1): 0x02, (1, 0): 0x04, (0, -1): 0x08,
              (-1, -1): 0x10, (-1, 1): 0x20, (1, 1): 0x40, (1, -1): 0x80}
# Case groups from GetCaveTileIndexFromPerimeterValue: (perimeters, type, base, nvar).
_CAVE_CASES = [
    ([0x00,0x10,0x20,0x30,0x40,0x50,0x60,0x70,0x80,0x90,0xa0,0xb0,0xc0,0xd0,0xe0,0xf0], None, 0, 0),
    ([0x01,0x11,0x21,0x31,0x41,0x51,0x61,0x71,0x81,0x91,0xa1,0xb1,0xc1,0xd1,0xe1,0xf1], SECONDWALL, 1, 4),
    ([0x02,0x12,0x22,0x32,0x42,0x52,0x62,0x72,0x82,0x92,0xa2,0xb2,0xc2,0xd2,0xe2,0xf2], SECONDWALL, 5, 4),
    ([0x03,0x13,0x43,0x53,0x83,0x93,0xc3,0xd3], FIRSTWALL, 1, 1),
    ([0x04,0x14,0x24,0x34,0x44,0x54,0x64,0x74,0x84,0x94,0xa4,0xb4,0xc4,0xd4,0xe4,0xf4], SECONDWALL, 9, 4),
    ([0x05,0x15,0x25,0x35,0x45,0x55,0x65,0x75,0x85,0x95,0xa5,0xb5,0xc5,0xd5,0xe5,0xf5], FIRSTWALL, 2, 2),
    ([0x06,0x16,0x26,0x36,0x86,0x96,0xa6,0xb6], FIRSTWALL, 4, 1),
    ([0x07,0x17,0x87,0x97], FIRSTWALL, 5, 1),
    ([0x08,0x18,0x28,0x38,0x48,0x58,0x68,0x78,0x88,0x98,0xa8,0xb8,0xc8,0xd8,0xe8,0xf8], SECONDWALL, 13, 4),
    ([0x09,0x29,0x49,0x69,0x89,0xa9,0xc9,0xe9], FIRSTWALL, 6, 1),
    ([0x0a,0x1a,0x2a,0x3a,0x4a,0x5a,0x6a,0x7a,0x8a,0x9a,0xaa,0xba,0xca,0xda,0xea,0xfa], FIRSTWALL, 7, 2),
    ([0x0b,0x4b,0x8b,0xcb], FIRSTWALL, 9, 1),
    ([0x0c,0x1c,0x2c,0x3c,0x4c,0x5c,0x6c,0x7c], FIRSTWALL, 10, 1),
    ([0x0d,0x2d,0x4d,0x6d], FIRSTWALL, 11, 1),
    ([0x0e,0x1e,0x2e,0x3e], FIRSTWALL, 12, 1),
    ([0x0f], FIRSTWALL, 13, 1),
    ([0x19,0x39,0x59,0x79,0x99,0xb9,0xd9,0xf9], FIRSTWALL, 14, 2),
    ([0x1b,0x5b,0x9b,0xdb], FIRSTWALL, 16, 1),
    ([0x1d,0x3d,0x5d,0x7d], FIRSTWALL, 17, 1),
    ([0x1f], FIRSTWALL, 18, 1),
    ([0x23,0x33,0x63,0x73,0xa3,0xb3,0xe3,0xf3], FIRSTWALL, 19, 2),
    ([0x27,0x37,0xa7,0xb7], FIRSTWALL, 21, 1),
    ([0x2b,0x6b,0xab,0xeb], FIRSTWALL, 22, 1),
    ([0x2f], FIRSTWALL, 23, 1),
    ([0x3b,0x7b,0xbb,0xfb], FIRSTWALL, 24, 3),
    ([0x3f], FIRSTWALL, 27, 1),
    ([0x46,0x56,0x66,0x76,0xc6,0xd6,0xe6,0xf6], FIRSTWALL, 28, 2),
    ([0x47,0x57,0xc7,0xd7], FIRSTWALL, 30, 1),
    ([0x4e,0x5e,0x6e,0x7e], FIRSTWALL, 31, 1),
    ([0x4f], FIRSTWALL, 32, 1),
    ([0x5f], FIRSTWALL, 33, 1),
    ([0x67,0x77,0xe7,0xf7], FIRSTWALL, 34, 3),
    ([0x6f], FIRSTWALL, 37, 1),
    ([0x7f], FIRSTWALL, 38, 2),
    ([0x8c,0x9c,0xac,0xbc,0xcc,0xdc,0xec,0xfc], FIRSTWALL, 40, 2),
    ([0x8d,0xad,0xcd,0xed], FIRSTWALL, 42, 1),
    ([0x8e,0x9e,0xae,0xbe], FIRSTWALL, 43, 1),
    ([0x8f], FIRSTWALL, 44, 1),
    ([0x9d,0xbd,0xdd,0xfd], FIRSTWALL, 45, 3),
    ([0x9f], FIRSTWALL, 48, 1),
    ([0xaf], FIRSTWALL, 49, 1),
    ([0xbf], FIRSTWALL, 50, 2),
    ([0xce,0xde,0xee,0xfe], FIRSTWALL, 52, 3),
    ([0xcf], FIRSTWALL, 55, 1),
    ([0xdf], FIRSTWALL, 56, 2),
    ([0xef], FIRSTWALL, 58, 2),
    ([0xff], FIRSTWALL, 60, 6),
]
# perimeter -> (type | None, frozenset(subs)).  None type = stalagmite (no wall).
CAVE_PERIM_LUT = {}
for _vals, _wt, _base, _cnt in _CAVE_CASES:
    for _v in _vals:
        CAVE_PERIM_LUT[_v] = (_wt, frozenset(range(_base, _base + _cnt)) if _wt is not None else frozenset())
assert all(v in CAVE_PERIM_LUT for v in range(256)), "cave LUT does not cover all 256 perimeters"


def cave_at(structs, gn: int, cols: int, rows: int) -> bool:
    """CaveAtGridNo: a cave wall (type 36/37) sits at gn. OOB -> False."""
    if not (0 <= gn < cols * rows):
        return False
    return any(int(e[0]) in CAVE_WALL_TYPES for e in structs[gn])


def cave_perimeter(structs, gn: int, cols: int, rows: int) -> int:
    """CalcNewCavePerimeterValue: 8-neighbour cave-presence bitmask at gn."""
    y, x = divmod(gn, cols)
    ub = 0
    for (dy, dx), bit in _CAVE_BITS.items():
        ny, nx = y + dy, x + dx
        if 0 <= ny < rows and 0 <= nx < cols and cave_at(structs, ny * cols + nx, cols, rows):
            ub |= bit
    return ub


def pick_cave_sub(perimeter: int, rng: random.Random):
    """FORWARD (generate): (type, sub) for a perimeter, or (None, None) for a
    stalagmite cell (no cardinal cave neighbour)."""
    wtype, subs = CAVE_PERIM_LUT[perimeter]
    if wtype is None or not subs:
        return None, None
    subs = sorted(subs)
    return wtype, subs[rng.randrange(len(subs))]


def cave_sub_is_legal(sub: int, perimeter: int) -> bool:
    """INVERSE (validate, lenient): does the placed cave sub fit the perimeter?"""
    return sub in CAVE_PERIM_LUT[perimeter][1]
