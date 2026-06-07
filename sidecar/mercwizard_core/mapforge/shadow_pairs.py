"""JA2 struct→shadow tile-type pairing — Python mirror of the frontend's
`frontend/src/lib/jaSlotPairs.ts`.

Source of truth: `TileEngine/TileDat.h` `TileTypeDefines` enum. Slot
numbers in `Ja2Set.dat.xml` are positions in that enum — each a
categorical TILE TYPE (FIRSTOSTRUCT, FIRSTSHADOW, …). Most shadow-casting
struct types have a paired shadow type, and JA2 ships the struct + shadow
STIs FRAME-ALIGNED: sub N of the struct corresponds to sub N of the
shadow (verified empirically for tileset 9 — l_bush(6)↔l_bush_s(6),
tree1_t(12)↔trshdwt1(12), tree2_t(9)↔trshdwt2(9)).

KEEP IN SYNC with jaSlotPairs.ts. The pairing is structural (TileType
enum positions, tileset-independent); the STI at each slot varies per
tileset, so a consumer must still check the active tileset actually has
the shadow STI before placing it.

NOTE: buildings (build_*.sti) are NOT in this table — they carry their
shadow as a dedicated FRAME inside the building STI (sub 30/31), placed
on the shadow layer at the building's own slot. That mechanism is
separate and handled by the map's own building placement, not by
struct→shadow pairing.
"""
from __future__ import annotations

# struct slot → shadow slot. Mirror of STRUCT_TO_SHADOW in jaSlotPairs.ts.
STRUCT_TO_SHADOW: dict[int, int] = {
    # FIRSTOSTRUCT..EIGHTOSTRUCT (12-19) → FIRSTSHADOW..EIGHTSHADOW (24-31)
    12: 24, 13: 25, 14: 26, 15: 27, 16: 28, 17: 29, 18: 30, 19: 31,
    # FIRSTFULLSTRUCT..FOURTHFULLSTRUCT (20-23) → FIRSTFULLSHADOW..FOURTHFULLSHADOW (32-35)
    20: 32, 21: 33, 22: 34, 23: 35,
    # FIRSTDOOR..FOURTHDOOR (40-43) → FIRSTDOORSHADOW..FOURTHDOORSHADOW (44-47)
    40: 44, 41: 45, 42: 46, 43: 47,
    # FENCESTRUCT (86) → FENCESHADOW (87)
    86: 87,
    # FIRSTVEHICLE / SECONDVEHICLE (88-89) → ..SHADOW (90-91)
    88: 90, 89: 91,
    # FIRSTDEBRISSTRUCT / SECONDDEBRISSTRUCT (93-94) → ..SHADOW (95-96)
    93: 95, 94: 96,
    # NINTHOSTRUCT / TENTHOSTRUCT (97-98) → ..SHADOW (99-100)
    97: 99, 98: 100,
    # FIRSTLARGEEXPDEBRIS / SECONDLARGEEXPDEBRIS (103-104) → ..SHADOW (105-106)
    103: 105, 104: 106,
    # FIRSTCLIFF (10) → FIRSTCLIFFSHADOW (11)
    10: 11,
}

# User-facing category buckets for the AutoShadow generator's toggles.
# Every key of STRUCT_TO_SHADOW belongs to exactly one bucket.
OBSTACLE_STRUCTS = frozenset({
    10, 12, 13, 14, 15, 16, 17, 18, 19,   # cliff + O-structs (trees, bushes, rocks)
    20, 21, 22, 23,                        # full-structs
    93, 94, 97, 98, 103, 104,              # debris + extra O-structs
})
DOOR_STRUCTS = frozenset({40, 41, 42, 43})
VEHICLE_FENCE_STRUCTS = frozenset({86, 88, 89})

# Sanity: the three buckets partition the table's keys exactly.
assert (OBSTACLE_STRUCTS | DOOR_STRUCTS | VEHICLE_FENCE_STRUCTS) == set(STRUCT_TO_SHADOW), (
    "shadow_pairs buckets must cover exactly the STRUCT_TO_SHADOW keys"
)


def shadow_slot_for(struct_slot: int) -> int | None:
    """Return the paired shadow slot for a struct slot, or None."""
    return STRUCT_TO_SHADOW.get(struct_slot)
