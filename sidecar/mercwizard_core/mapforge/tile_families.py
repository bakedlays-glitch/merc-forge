"""TileDat slot → palette-category table for the MapForge Brush Box.

Source of truth: ``TileEngine/TileDat.h`` — the SECOND enum, ``enum
TileTypeDefines`` at TileDat.h:3230 (NOT the per-file sub-slot enums near
the top of the header), plus its ``FIRST*/LAST*`` ``#define`` anchors at
TileDat.h:3453-3477.

The palette "slot" number IS the position in that enum (== the .dat
tile-type index == ``load_tileset_xml`` keys == ``gTileTypeStartIndex``
index). The engine enum is *implicitly* valued (no ``= N``), so the
positions below were derived by counting members in order from
``FIRSTTEXTURE = 0`` and then CROSS-CHECKED against the partial enum
mirrors already living in the sidecar/frontend:

  * ``mapforge_engine.building_library`` — WALL/DOOR/WINDOW/DECAL slots
  * ``mapforge.generators``             — water/road/tree mask ranges
  * ``mapforge.shadow_pairs``           — STRUCT_TO_SHADOW (shadow slots)
  * ``frontend/src/lib/jaSlotPairs.ts``  — the frontend mirror

Pinned anchors all agree: FIRSTOSTRUCT=12, FIRSTWALL=36, FIRSTDOOR=40,
FIRSTFLOOR=60, FIRSTROAD=78, FENCESTRUCT=86, FIRSTVEHICLE=88, FOURTHWINDOW=51.
A single miscount would shift every later slot, so ``test_tile_families.py``
asserts those anchors + full 0..MAX_TILE_SLOT coverage and re-derives the
shadow set from ``STRUCT_TO_SHADOW``.

NOTE: ``engine.db`` does NOT index this enum (verified — zero hits for the
FIRST*/LAST* members), so the family table is read from the header here
rather than queried from the graph.

Family keys are the EXISTING palette categories (``PALETTE_CATEGORY_ORDER``
in ``routes/mapforge.py`` + ``CATEGORY_TO_LAYER`` in ``MapForgePalette.tsx``)
plus one new ``"shadow"`` key, so the frontend needs no new label/layer
wiring beyond adding ``shadow``. The struct split follows Will's call
(2026-06-13): outdoor O/FULL-structs → vegetation, interior I-structs →
furniture, loose debris/decals/blood → scatter.
"""
from __future__ import annotations

# Inclusive [first, last] enum-position ranges → palette category key.
# The trailing comment names the TileTypeDefines members the range spans.
# (Listed in enum order; lookups use the prebuilt dict below.)
_FAMILY_RANGES: list[tuple[int, int, str]] = [
    (0,   8,   "floor"),     # FIRSTTEXTURE..DEEPWATERTEXTURE (ground + water)
    (9,   10,  "floor"),     # FIRSTCLIFFHANG, FIRSTCLIFF (terrain banks, land Z)
    (11,  11,  "shadow"),    # FIRSTCLIFFSHADOW
    (12,  23,  "veg"),       # FIRST..EIGHT OSTRUCT + FIRST..FOURTH FULLSTRUCT (trees/bushes)
    (24,  35,  "shadow"),    # FIRSTSHADOW..FOURTHFULLSHADOW
    (36,  39,  "wall"),      # FIRST..FOURTH WALL
    (40,  43,  "door"),      # FIRST..FOURTH DOOR
    (44,  47,  "shadow"),    # FIRST..FOURTH DOORSHADOW
    (48,  48,  "roof"),      # SLANTROOFCEILING
    (49,  49,  "scatter"),   # ANOTHERDEBRIS
    (50,  50,  "floor"),     # ROADPIECES
    (51,  51,  "window"),    # FOURTHWINDOW
    (52,  55,  "scatter"),   # FIRST..FOURTH DECORATIONS
    (56,  59,  "scatter"),   # FIRST..FOURTH WALLDECAL
    (60,  63,  "floor"),     # FIRST..FOURTH FLOOR
    (64,  67,  "roof"),      # FIRST..FOURTH ROOF
    (68,  69,  "roof"),      # FIRST/SECOND SLANTROOF
    (70,  71,  "roof"),      # FIRST/SECOND ONROOF
    (72,  72,  "floor"),     # MOCKFLOOR
    (73,  77,  "furniture"), # FIRST..FOURTH ISTRUCT + FIRSTCISTRUCT (interior)
    (78,  78,  "floor"),     # FIRSTROAD
    (79,  84,  "scatter"),   # DEBRISROCKS..DEBRISMISC
    (85,  85,  "scatter"),   # ANIOSTRUCT (animated misc object)
    (86,  86,  "wall"),      # FENCESTRUCT
    (87,  87,  "shadow"),    # FENCESHADOW
    (88,  89,  "vehicle"),   # FIRST/SECOND VEHICLE
    (90,  91,  "shadow"),    # FIRST/SECOND VEHICLESHADOW
    (92,  94,  "scatter"),   # DEBRIS2MISC + FIRST/SECOND DEBRISSTRUCT
    (95,  96,  "shadow"),    # FIRST/SECOND DEBRISSTRUCTSHADOW
    (97,  98,  "veg"),       # NINTH/TENTH OSTRUCT (trees/obstacles)
    (99,  100, "shadow"),    # NINTH/TENTH OSTRUCTSHADOW
    (101, 104, "scatter"),   # FIRST/SECOND EXPLDEBRIS + FIRST/SECOND LARGEEXPDEBRIS
    (105, 106, "shadow"),    # FIRST/SECOND LARGEEXPDEBRISSHADOW
    (107, 110, "furniture"), # FIFTH..EIGHT ISTRUCT (interior)
    (111, 112, "roof"),      # FIRST/SECOND HIGHROOF
    (113, 116, "scatter"),   # FIFTH..EIGTH WALLDECAL
    (117, 118, "scatter"),   # HUMANBLOOD, CREATUREBLOOD
    (119, 119, "furniture"), # FIRSTSWITCHES (interactive fixture)
    (120, 122, "roof"),      # REVEALEDSLANTROOFS + FIRST/SECOND REVEALEDHIGHROOFS
]

#: Highest enum position that is real, paintable TILE content
#: (SECONDREVEALEDHIGHROOFS). Past here the enum is GUNS / P*ITEMS / UI
#: elements (cursors, rings, miss-tiles, wireframes, item slots) — never
#: map tiles. Those fall through to the filename fallback and are filtered
#: out of the palette by ``engineMaxTileSlot`` on the frontend anyway.
MAX_TILE_SLOT = 122

#: Every category key this table can emit. Keep in sync with
#: ``PALETTE_CATEGORY_ORDER`` (routes/mapforge.py) and the frontend
#: ``CATEGORY_LABELS`` / ``CATEGORY_TO_LAYER`` (MapForgePalette.tsx).
FAMILY_KEYS = frozenset({
    "floor", "wall", "door", "window", "roof",
    "furniture", "veg", "scatter", "vehicle", "shadow",
})

_SLOT_FAMILY: dict[int, str] = {}
for _first, _last, _cat in _FAMILY_RANGES:
    for _s in range(_first, _last + 1):
        if _s in _SLOT_FAMILY:  # pragma: no cover - defends against an overlapping edit
            raise ValueError(f"tile_families: slot {_s} mapped twice")
        _SLOT_FAMILY[_s] = _cat
del _first, _last, _cat, _s


def slot_family(slot: int) -> str | None:
    """Palette category for a ``TileTypeDefines`` slot position.

    Returns one of :data:`FAMILY_KEYS`, or ``None`` when ``slot`` isn't a
    known tile-content family (caller should fall back to the legacy
    filename heuristic — in practice only item/UI slots > ``MAX_TILE_SLOT``,
    which the palette filters out regardless).
    """
    return _SLOT_FAMILY.get(slot)
