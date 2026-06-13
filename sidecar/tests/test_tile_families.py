"""Cross-checks for the TileDat slot → palette-category table.

The table in ``mapforge.tile_families`` is hand-transcribed from an
implicitly-valued C++ enum (``TileTypeDefines``, TileDat.h:3230), so a
single miscount would silently shift every later slot. These tests turn the
*other* partial enum mirrors already in the codebase into an independent
guard: anchors, full coverage, and a shadow set re-derived from
``STRUCT_TO_SHADOW``.
"""
from __future__ import annotations

from mercwizard_core.mapforge import tile_families as tf
from mercwizard_core.mapforge.shadow_pairs import STRUCT_TO_SHADOW
from mercwizard_core.mapforge.generators import (
    _WATER_LAND_SLOTS,
    _ROAD_OBJ_SLOTS,
    _ROAD_LAND_SLOTS,
    _TREE_SLOTS,
)
from mercwizard_core.mapforge_engine.building_library import (
    WALL_SLOTS,
    DOOR_SLOTS,
    WINDOW_SLOTS,
    WALL_DECAL_SLOTS,
    SLANT_ROOF_CEILING_SLOTS,
)


def test_pinned_anchors():
    """The FIRST* members whose positions are independently pinned across
    the partial mirrors must land on the right family."""
    assert tf.slot_family(12) == "veg"      # FIRSTOSTRUCT (trees)
    assert tf.slot_family(36) == "wall"     # FIRSTWALL
    assert tf.slot_family(40) == "door"     # FIRSTDOOR
    assert tf.slot_family(51) == "window"   # FOURTHWINDOW
    assert tf.slot_family(60) == "floor"    # FIRSTFLOOR
    assert tf.slot_family(78) == "floor"    # FIRSTROAD
    assert tf.slot_family(86) == "wall"     # FENCESTRUCT
    assert tf.slot_family(88) == "vehicle"  # FIRSTVEHICLE


def test_full_tile_range_covered():
    """Every paintable tile slot resolves to a known family — no silent
    'None' holes that would re-pollute the 'Other' bucket."""
    holes = [s for s in range(0, tf.MAX_TILE_SLOT + 1) if tf.slot_family(s) is None]
    assert holes == [], f"unmapped tile slots: {holes}"


def test_only_known_family_keys_emitted():
    emitted = {tf.slot_family(s) for s in range(0, tf.MAX_TILE_SLOT + 1)}
    assert emitted <= tf.FAMILY_KEYS, f"unexpected keys: {emitted - tf.FAMILY_KEYS}"


def test_item_and_ui_slots_are_not_classified():
    """Past MAX_TILE_SLOT the enum is GUNS/P*ITEMS/UI — must return None so
    the caller's filename fallback handles them (and the palette filters)."""
    assert tf.slot_family(tf.MAX_TILE_SLOT + 1) is None  # GUNS
    assert tf.slot_family(200) is None


def test_building_library_mirror_agrees():
    for s in WALL_SLOTS:
        assert tf.slot_family(s) == "wall", s
    for s in DOOR_SLOTS:
        assert tf.slot_family(s) == "door", s
    for s in WINDOW_SLOTS:
        assert tf.slot_family(s) == "window", s
    for s in WALL_DECAL_SLOTS:
        assert tf.slot_family(s) == "scatter", s
    for s in SLANT_ROOF_CEILING_SLOTS:
        assert tf.slot_family(s) == "roof", s


def test_generators_mirror_agrees():
    for s in _WATER_LAND_SLOTS:           # 7, 8 → ground/water
        assert tf.slot_family(s) == "floor", s
    for s in _ROAD_OBJ_SLOTS | _ROAD_LAND_SLOTS:  # 50, 78 → roads
        assert tf.slot_family(s) == "floor", s
    for s in _TREE_SLOTS:                 # 12-23, 97, 98 → trees
        assert tf.slot_family(s) == "veg", s


def test_shadow_slots_all_classified_shadow():
    """Every shadow slot the engine pairs with a struct (the VALUES of
    STRUCT_TO_SHADOW) must be the 'shadow' family."""
    for struct_slot, shadow_slot in STRUCT_TO_SHADOW.items():
        assert tf.slot_family(shadow_slot) == "shadow", (struct_slot, shadow_slot)


def test_struct_slots_are_not_shadow():
    """The struct KEYS (the visible pieces) must NOT be classified shadow —
    catches an off-by-one that swapped struct/shadow halves."""
    for struct_slot in STRUCT_TO_SHADOW:
        fam = tf.slot_family(struct_slot)
        assert fam is not None and fam != "shadow", (struct_slot, fam)
