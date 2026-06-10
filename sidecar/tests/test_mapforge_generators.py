"""Tests for the MapForge generator subsystem (bug-review task #114)."""
from __future__ import annotations

import random

import pytest

from mercwizard_core.mapforge import shadow_pairs
from mercwizard_core.mapforge.generators import (
    ALL_LAYERS,
    AutoShadowGenerator,
    BankGenerator,
    ClusterScatterGenerator,
    DensityFalloffGenerator,
    FillLayerGenerator,
    Generator,
    GeneratorContext,
    Param,
    RectangleGenerator,
    REGISTRY,
    ScatterGenerator,
    WipeGenerator,
    _make_mask_predicate,
    _make_sub_picker,
    _normalize_region,
    _parse_int_csv,
    _parse_weighted_subs,
    _validate_layer,
    get,
    list_all,
)


def _ops(gen, ctx, params):
    """Collect just the mutation ops (drop phase/progress events)."""
    return [e for e in gen.iter_ops(ctx, params) if "op" in e]


# Tileset-9-like metadata: the tree/bush shadow STIs exist and are
# frame-aligned with their structs (verified empirically).
_TS9_SLOT_MAP = {
    18: "l_bush.sti", 20: "tree1_t.sti", 21: "tree2_t.sti",
    30: "l_bush_s.sti", 32: "trshdwt1.sti", 33: "trshdwt2.sti",
    40: "door1.sti", 44: "door1s.sti",
    39: "build_06.sti",
}
_TS9_FRAMES = {18: 6, 20: 12, 21: 9, 30: 6, 32: 12, 33: 9, 40: 8, 44: 8, 39: 65}


def _ts9_ctx(parsed, with_meta=True):
    if with_meta:
        return GeneratorContext(
            rows=parsed["rows"], cols=parsed["cols"], parsed=parsed,
            slot_map=_TS9_SLOT_MAP,
            frame_count=lambda s: _TS9_FRAMES.get(s, 0),
        )
    return GeneratorContext(rows=parsed["rows"], cols=parsed["cols"], parsed=parsed)


def _parsed_with_layers(rows, cols, occupied=None):
    """Build a parsed dict with empty per-tile layer grids, mirroring the
    real session shape (`parsed[layer][y*cols + x]` → list of (slot, sub)).

    `occupied` pre-fills tiles: {layer: {(x, y): [(slot, sub), ...]}}.
    Used by the masking tests, which need a real layer grid to read."""
    n = rows * cols
    parsed = {"rows": rows, "cols": cols}
    for layer in ALL_LAYERS:
        parsed[layer] = [[] for _ in range(n)]
    for layer, tiles in (occupied or {}).items():
        for (x, y), entries in tiles.items():
            parsed[layer][y * cols + x] = list(entries)
    return parsed


def test_registry_has_wipe_generator():
    """WipeGenerator is the seed entry — must be registered."""
    assert "wipe" in REGISTRY
    assert isinstance(REGISTRY["wipe"], WipeGenerator)


def test_list_all_returns_sorted():
    """list_all sorts by name for stable UI display order."""
    names = [g.name for g in list_all()]
    assert names == sorted(names)


def test_get_returns_none_for_unknown():
    assert get("definitely-not-a-real-generator") is None


def test_get_returns_instance_for_known():
    g = get("wipe")
    assert g is not None
    assert g.name == "wipe"


def test_wipe_metadata_serializes():
    """to_dict produces a JSON-friendly schema the UI can render."""
    d = WipeGenerator().to_dict()
    assert d["name"] == "wipe"
    assert d["label"]
    assert d["description"]
    assert d["params"]  # has at least one param
    assert all("name" in p and "type" in p for p in d["params"])


def test_wipe_emits_phase_events_and_ops():
    """WipeGenerator's stream must include at least one phase=start
    + a phase=done bookending the op stream. Each non-phase event must
    be a valid edit op."""
    ctx = GeneratorContext(rows=2, cols=2, parsed={"rows": 2, "cols": 2})
    events = list(WipeGenerator().iter_ops(ctx, {}))

    phase_starts = [e for e in events if e.get("phase") and e.get("status") == "start"]
    phase_dones = [e for e in events if e.get("phase") and e.get("status") == "done"]
    assert len(phase_starts) == 1
    assert len(phase_dones) == 1

    op_events = [e for e in events if "op" in e]
    # 2×2 grid × 6 layers = 24 set_entries ops; no set_room since
    # reset_rooms defaults to False.
    assert len(op_events) == 24
    for ev in op_events:
        assert ev["op"] == "set_entries"
        assert ev["layer"] in ("land", "objs", "shadows", "structs", "roofs", "onroofs")
        assert ev["entries"] == []
        assert 0 <= ev["x"] < 2
        assert 0 <= ev["y"] < 2


def test_wipe_reset_rooms_emits_set_room_ops():
    """reset_rooms=True adds one set_room op per tile (room_id=0)."""
    ctx = GeneratorContext(rows=2, cols=2, parsed={"rows": 2, "cols": 2})
    events = list(WipeGenerator().iter_ops(ctx, {"reset_rooms": True}))
    set_room_ops = [e for e in events if e.get("op") == "set_room"]
    assert len(set_room_ops) == 4  # 2×2 tiles
    assert all(e["room_id"] == 0 for e in set_room_ops)


def test_wipe_op_order_is_row_major():
    """The op stream walks tiles y-outer, x-inner so the canvas fills
    line-by-line during incremental render. This is a UX contract, not
    just an implementation detail — locking it down so a future
    refactor doesn't silently reorder."""
    ctx = GeneratorContext(rows=3, cols=3, parsed={"rows": 3, "cols": 3})
    coords_seen: list[tuple[int, int]] = []
    for ev in WipeGenerator().iter_ops(ctx, {}):
        if "op" in ev and ev["op"] == "set_entries" and ev["layer"] == "land":
            coords_seen.append((ev["x"], ev["y"]))
    expected = [(x, y) for y in range(3) for x in range(3)]
    assert coords_seen == expected


# ────────────────────────────────────────────────────────────────────────
#  Layer validation (shared helper)
# ────────────────────────────────────────────────────────────────────────


def test_all_layers_constant_matches_route_dispatcher():
    """The ALL_LAYERS allow-list MUST mirror the layer set
    `_apply_single_edit` accepts in routes/mapforge.py. Locked in
    here so a future maintainer adding a 7th layer to one side
    doesn't drift the other."""
    assert ALL_LAYERS == ("land", "objs", "shadows", "structs", "roofs", "onroofs")


def test_validate_layer_accepts_canonical_names():
    for layer in ALL_LAYERS:
        assert _validate_layer(layer) == layer


def test_validate_layer_rejects_misspellings():
    with pytest.raises(ValueError, match="unknown layer"):
        _validate_layer("ground")  # typo for "land"
    with pytest.raises(ValueError):
        _validate_layer("LAND")  # case-sensitive — engine is too


# ────────────────────────────────────────────────────────────────────────
#  FillLayerGenerator
# ────────────────────────────────────────────────────────────────────────


def test_fill_emits_one_place_op_per_tile():
    """FillLayer emits a `place` op for every (x, y) on the layer."""
    ctx = GeneratorContext(rows=4, cols=4, parsed={"rows": 4, "cols": 4})
    events = list(FillLayerGenerator().iter_ops(ctx, {
        "layer": "land", "slot": 12, "sub": 3,
    }))
    op_events = [e for e in events if "op" in e]
    # 4×4 grid, exactly one op per tile.
    assert len(op_events) == 16
    for ev in op_events:
        assert ev["op"] == "place"
        assert ev["layer"] == "land"
        assert ev["slot"] == 12
        assert ev["sub"] == 3
    # Every (x, y) coordinate exactly once.
    coords = {(ev["x"], ev["y"]) for ev in op_events}
    assert coords == {(x, y) for x in range(4) for y in range(4)}


def test_fill_default_layer_is_land():
    """Defaults: layer=land, slot=0, sub=1. Matches what the param
    schema declares; mismatched defaults would surface here.

    sub=1 (not 0) because JA2 .dat sub-indices are 1-BASED — the
    renderer's cellMap is built from manifests that use sub-1 → frame
    index. sub=0 produces frame[-1] (blank canvas).
    """
    ctx = GeneratorContext(rows=1, cols=1, parsed={"rows": 1, "cols": 1})
    events = list(FillLayerGenerator().iter_ops(ctx, {}))
    op = next(e for e in events if "op" in e)
    assert op["layer"] == "land"
    assert op["slot"] == 0
    assert op["sub"] == 1


def test_fill_phase_markers_bookend():
    """phase=start before any op, phase=done after."""
    ctx = GeneratorContext(rows=2, cols=2, parsed={"rows": 2, "cols": 2})
    events = list(FillLayerGenerator().iter_ops(ctx, {"layer": "objs", "slot": 5, "sub": 1}))
    assert events[0].get("phase") == "fill"
    assert events[0].get("status") == "start"
    assert events[-1].get("phase") == "fill"
    assert events[-1].get("status") == "done"


def test_fill_invalid_layer_raises():
    ctx = GeneratorContext(rows=1, cols=1, parsed={"rows": 1, "cols": 1})
    with pytest.raises(ValueError, match="unknown layer"):
        list(FillLayerGenerator().iter_ops(ctx, {"layer": "ground"}))


def test_fill_registered_in_registry():
    assert "fill" in REGISTRY
    assert isinstance(REGISTRY["fill"], FillLayerGenerator)


# ────────────────────────────────────────────────────────────────────────
#  RectangleGenerator — outline mode
# ────────────────────────────────────────────────────────────────────────


def test_rect_outline_emits_perimeter_only():
    """outline mode: only the border tiles, no interior."""
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 2, "y1": 2, "x2": 5, "y2": 5,
        "layer": "structs", "slot": 86, "sub": 1, "mode": "outline",
    }))
    op_events = [e for e in events if "op" in e]
    coords = {(ev["x"], ev["y"]) for ev in op_events}
    expected = set()
    for x in range(2, 6):
        expected.add((x, 2))  # top edge
        expected.add((x, 5))  # bottom edge
    for y in range(2, 6):
        expected.add((2, y))  # left edge
        expected.add((5, y))  # right edge
    assert coords == expected
    # Interior tile (3, 3) MUST NOT be in the output
    assert (3, 3) not in coords
    # 4×4 box outline = 12 perimeter tiles
    assert len(op_events) == 12


def test_rect_outline_collapses_to_line_for_1xN():
    """A 1×N rect IS a line — outline mode should still paint it
    instead of producing zero tiles (the interior-skip logic must
    special-case degenerate dimensions)."""
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 5, "y1": 2, "x2": 5, "y2": 7,
        "layer": "land", "slot": 1, "sub": 1, "mode": "outline",
    }))
    op_events = [e for e in events if "op" in e]
    assert len(op_events) == 6  # y=2..7 at x=5
    coords = {(ev["x"], ev["y"]) for ev in op_events}
    assert coords == {(5, y) for y in range(2, 8)}


def test_rect_outline_normalizes_corners():
    """(x1, y1) and (x2, y2) can be passed in any order — the
    generator normalizes to a canonical orientation. Same input
    swapped should produce the same tile set."""
    ctx = GeneratorContext(rows=20, cols=20, parsed={"rows": 20, "cols": 20})
    canonical = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 3, "y1": 3, "x2": 8, "y2": 8,
        "layer": "land", "slot": 1, "sub": 1, "mode": "outline",
    }))
    flipped = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 8, "y1": 8, "x2": 3, "y2": 3,
        "layer": "land", "slot": 1, "sub": 1, "mode": "outline",
    }))
    canon_coords = {(e["x"], e["y"]) for e in canonical if "op" in e}
    flip_coords = {(e["x"], e["y"]) for e in flipped if "op" in e}
    assert canon_coords == flip_coords


# ────────────────────────────────────────────────────────────────────────
#  RectangleGenerator — fill mode
# ────────────────────────────────────────────────────────────────────────


def test_rect_fill_emits_every_interior_tile():
    """fill mode: every tile inside the rect, including corners +
    interior. 3×3 rect = 9 tiles total."""
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 1, "y1": 1, "x2": 3, "y2": 3,
        "layer": "land", "slot": 5, "sub": 1, "mode": "fill",
    }))
    op_events = [e for e in events if "op" in e]
    coords = {(e["x"], e["y"]) for e in op_events}
    assert coords == {(x, y) for x in range(1, 4) for y in range(1, 4)}
    assert len(op_events) == 9


# ────────────────────────────────────────────────────────────────────────
#  RectangleGenerator — edge cases
# ────────────────────────────────────────────────────────────────────────


def test_rect_clamps_oob_coords_to_grid():
    """Out-of-bounds inputs clamp to the grid edge rather than raising.
    A user typing `x2=999` against a 160-wide sector gets a rect that
    runs to the actual edge."""
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 0, "y1": 0, "x2": 999, "y2": 999,
        "layer": "land", "slot": 1, "sub": 1, "mode": "fill",
    }))
    op_events = [e for e in events if "op" in e]
    # Fill of the full 10×10 grid after clamping
    assert len(op_events) == 100


def test_rect_invalid_mode_raises():
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    with pytest.raises(ValueError, match="mode must be"):
        list(RectangleGenerator().iter_ops(ctx, {
            "x1": 0, "y1": 0, "x2": 5, "y2": 5, "mode": "stipple",
        }))


def test_rect_invalid_layer_raises():
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    with pytest.raises(ValueError, match="unknown layer"):
        list(RectangleGenerator().iter_ops(ctx, {
            "x1": 0, "y1": 0, "x2": 5, "y2": 5, "layer": "floor",
        }))


def test_rect_registered_in_registry():
    assert "rect" in REGISTRY
    assert isinstance(REGISTRY["rect"], RectangleGenerator)


def test_registry_lists_all_phase_d_generators():
    """Registry sanity — wipe + fill + rect + the three scatter-family
    generators + the corpus-driven building stamp (autoshadow retired
    2026-05-31). Catches accidental double-registration or a forgotten
    REGISTRY entry."""
    names = {g.name for g in list_all()}
    assert names == {
        "wipe", "fill", "rect",
        "scatter", "cluster", "density-falloff",
        "building", "bank",
    }


# ────────────────────────────────────────────────────────────────────────
#  BankGenerator (A7 — heights escarpment)
# ────────────────────────────────────────────────────────────────────────


def _bank_ctx(rows, cols):
    # iter_ops never reads ctx.parsed (heights flow out as ops), so a
    # dims-only context is enough to enumerate the stream.
    return GeneratorContext(rows=rows, cols=cols, parsed={"rows": rows, "cols": cols})


def test_bank_registered():
    assert "bank" in REGISTRY
    assert isinstance(REGISTRY["bank"], BankGenerator)


def test_bank_uniform_plateau_in_80_steps():
    """levels × 80 (the engine's WORLD_CLIFF_HEIGHT) on every tile —
    vanilla cliff semantics; no terrace bands (those just made
    concentric impassable rings)."""
    ctx = _bank_ctx(20, 20)
    events = list(BankGenerator().iter_ops(ctx, {
        "x1": 2, "y1": 2, "x2": 6, "y2": 6, "levels": 2,
    }))
    ops = [e for e in events if "op" in e]
    assert len(ops) == 25
    for o in ops:
        assert o["op"] == "set_height"
        assert o["height"] == 160
    assert {(o["x"], o["y"]) for o in ops} == {
        (x, y) for x in range(2, 7) for y in range(2, 7)
    }


def test_bank_default_is_one_cliff_level():
    ctx = _bank_ctx(10, 10)
    ops = [e for e in BankGenerator().iter_ops(ctx, {
        "x1": 0, "y1": 0, "x2": 1, "y2": 1,
    }) if "op" in e]
    assert all(o["height"] == 80 for o in ops)


def test_bank_levels_zero_flattens():
    ctx = _bank_ctx(10, 10)
    ops = [e for e in BankGenerator().iter_ops(ctx, {
        "x1": 0, "y1": 0, "x2": 1, "y2": 1, "levels": 0,
    }) if "op" in e]
    assert all(o["height"] == 0 for o in ops)


def test_bank_normalizes_corners():
    ctx = _bank_ctx(30, 30)
    a = {(e["x"], e["y"]) for e in BankGenerator().iter_ops(ctx, {
        "x1": 10, "y1": 10, "x2": 4, "y2": 4, "levels": 1}) if "op" in e}
    b = {(e["x"], e["y"]) for e in BankGenerator().iter_ops(ctx, {
        "x1": 4, "y1": 4, "x2": 10, "y2": 10, "levels": 1}) if "op" in e}
    assert a == b


def test_bank_clamps_oob_region_and_levels():
    ctx = _bank_ctx(10, 10)
    ops = [e for e in BankGenerator().iter_ops(ctx, {
        "x1": 0, "y1": 0, "x2": 999, "y2": 999, "levels": 999,
    }) if "op" in e]
    assert len(ops) == 100               # region clamped to the 10×10 grid
    # levels clamp to the engine max of 3 raises → 240, never 255-ish junk.
    assert all(o["height"] == 240 for o in ops)


def test_bank_phase_start_has_total():
    ctx = _bank_ctx(20, 20)
    start = next(
        e for e in BankGenerator().iter_ops(ctx, {
            "x1": 1, "y1": 1, "x2": 5, "y2": 8, "levels": 1})
        if e.get("status") == "start"
    )
    assert start["total"] == 5 * 8  # width 5 × height 8


# ────────────────────────────────────────────────────────────────────────
#  _normalize_region helper
# ────────────────────────────────────────────────────────────────────────


def test_normalize_region_full_map_on_all_none():
    """All None → whole grid as inclusive bounds."""
    ctx = GeneratorContext(rows=160, cols=160, parsed={"rows": 160, "cols": 160})
    assert _normalize_region(ctx, None, None, None, None) == (0, 0, 159, 159)


def test_normalize_region_swaps_reversed_corners():
    ctx = GeneratorContext(rows=160, cols=160, parsed={"rows": 160, "cols": 160})
    assert _normalize_region(ctx, 80, 80, 10, 10) == (10, 10, 80, 80)


def test_normalize_region_clamps_oob():
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    # Negative + over-max both clamp to grid edges.
    assert _normalize_region(ctx, -5, -5, 999, 999) == (0, 0, 9, 9)


# ────────────────────────────────────────────────────────────────────────
#  ScatterGenerator
# ────────────────────────────────────────────────────────────────────────


def test_scatter_emits_count_ops_when_density_fits():
    """A sparse scatter (small count, large region, small min_dist)
    should place every requested tile."""
    ctx = GeneratorContext(rows=80, cols=80, parsed={"rows": 80, "cols": 80})
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 50, "min_distance": 2,
        "layer": "objs", "slot": 81, "sub": 3, "seed": 7,
    }))
    op_events = [e for e in events if "op" in e]
    assert len(op_events) == 50
    for ev in op_events:
        assert ev["op"] == "add"
        assert ev["layer"] == "objs"
        assert ev["slot"] == 81
        assert ev["sub"] == 3


def test_scatter_respects_min_distance():
    """All pairs of placed scatter coords must satisfy Chebyshev
    distance ≥ min_distance. With min_distance=5 on a 40×40 grid
    with count=20 there's plenty of room."""
    ctx = GeneratorContext(rows=40, cols=40, parsed={"rows": 40, "cols": 40})
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 20, "min_distance": 5,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 12,
    }))
    coords = [(e["x"], e["y"]) for e in events if "op" in e]
    for i, (ax, ay) in enumerate(coords):
        for (bx, by) in coords[i + 1:]:
            assert abs(ax - bx) >= 5 or abs(ay - by) >= 5, (
                f"({ax},{ay}) and ({bx},{by}) violate min_distance=5"
            )


def test_scatter_reproducible_with_same_seed():
    """Same seed + same params + same context → bit-identical op stream."""
    ctx = GeneratorContext(rows=60, cols=60, parsed={"rows": 60, "cols": 60})
    params = {
        "count": 30, "min_distance": 2,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 99,
    }
    run1 = [(e["x"], e["y"]) for e in ScatterGenerator().iter_ops(ctx, params) if "op" in e]
    run2 = [(e["x"], e["y"]) for e in ScatterGenerator().iter_ops(ctx, params) if "op" in e]
    assert run1 == run2


def test_scatter_different_seeds_produce_different_output():
    """Different seeds → different coords (with overwhelming probability
    at count ≥ 20)."""
    ctx = GeneratorContext(rows=60, cols=60, parsed={"rows": 60, "cols": 60})
    base = {"count": 20, "min_distance": 2, "layer": "objs", "slot": 1, "sub": 1}
    a = [(e["x"], e["y"]) for e in ScatterGenerator().iter_ops(ctx, {**base, "seed": 1}) if "op" in e]
    b = [(e["x"], e["y"]) for e in ScatterGenerator().iter_ops(ctx, {**base, "seed": 999}) if "op" in e]
    assert a != b


def test_scatter_too_dense_gracefully_stops():
    """If the region is too dense to fit count, we emit what we can
    and report it in the done phase. No crash, no infinite loop."""
    # 4×4 region with min_distance=3 can fit at most ~4 tiles
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 100, "min_distance": 3,
        "region_x1": 0, "region_y1": 0, "region_x2": 3, "region_y2": 3,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 1,
    }))
    op_count = sum(1 for e in events if "op" in e)
    assert op_count < 100  # never fits 100 requested
    final = events[-1]
    assert final.get("phase") == "scatter"
    assert "too dense" in final.get("label", "").lower() or "placed" in final.get("label", "").lower()


def test_scatter_respects_region_bounds():
    """All scatter points must fall inside the user-specified region."""
    ctx = GeneratorContext(rows=100, cols=100, parsed={"rows": 100, "cols": 100})
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 30, "min_distance": 2,
        "region_x1": 20, "region_y1": 30, "region_x2": 50, "region_y2": 60,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 5,
    }))
    for ev in events:
        if "op" not in ev:
            continue
        assert 20 <= ev["x"] <= 50, f"x={ev['x']} outside region"
        assert 30 <= ev["y"] <= 60, f"y={ev['y']} outside region"


# ────────────────────────────────────────────────────────────────────────
#  ClusterScatterGenerator
# ────────────────────────────────────────────────────────────────────────


def test_cluster_emits_objects_grouped_around_centers():
    """Every emitted op must land within `cluster_radius` of SOME
    cluster center. Picks centers from the emitted ops by checking
    they form ≤ cluster_count distinct local groups."""
    ctx = GeneratorContext(rows=60, cols=60, parsed={"rows": 60, "cols": 60})
    events = list(ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 3, "objects_per_cluster": 10,
        "cluster_radius": 4,
        "layer": "objs", "slot": 81, "sub": 1, "seed": 11,
    }))
    coords = [(e["x"], e["y"]) for e in events if "op" in e]
    assert len(coords) > 0

    # Simple connected-component grouping: each tile is "near" another
    # if Chebyshev distance ≤ cluster_radius * 2 (clusters can almost
    # touch each other so use 2x as the connectivity threshold). Group
    # count should be ≤ cluster_count.
    groups: list[list[tuple[int, int]]] = []
    for c in coords:
        attached = False
        for g in groups:
            if any(abs(c[0] - p[0]) <= 8 and abs(c[1] - p[1]) <= 8 for p in g):
                g.append(c)
                attached = True
                break
        if not attached:
            groups.append([c])
    assert len(groups) <= 3


def test_cluster_reproducible_with_seed():
    ctx = GeneratorContext(rows=80, cols=80, parsed={"rows": 80, "cols": 80})
    params = {
        "cluster_count": 4, "objects_per_cluster": 8,
        "cluster_radius": 5, "layer": "objs", "slot": 1, "sub": 1, "seed": 22,
    }
    a = [(e["x"], e["y"]) for e in ClusterScatterGenerator().iter_ops(ctx, params) if "op" in e]
    b = [(e["x"], e["y"]) for e in ClusterScatterGenerator().iter_ops(ctx, params) if "op" in e]
    assert a == b


def test_cluster_radius_too_large_bails_gracefully():
    """If cluster_radius is bigger than half the grid, there's no
    room for the center inset. Emit a sensible done phase + no ops."""
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    events = list(ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 2, "objects_per_cluster": 5,
        "cluster_radius": 50, "layer": "objs", "slot": 1, "sub": 1,
    }))
    ops = [e for e in events if "op" in e]
    assert ops == []
    # done phase should explain
    final = events[-1]
    assert final.get("phase") == "cluster"
    assert "too large" in final.get("label", "").lower()


def test_cluster_objects_inside_grid():
    """Per-cluster offsets that overflow the grid get clamped/skipped
    — no out-of-bounds ops."""
    ctx = GeneratorContext(rows=40, cols=40, parsed={"rows": 40, "cols": 40})
    events = list(ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 5, "objects_per_cluster": 20,
        "cluster_radius": 6, "layer": "objs", "slot": 1, "sub": 1, "seed": 7,
    }))
    for ev in events:
        if "op" not in ev:
            continue
        assert 0 <= ev["x"] < 40
        assert 0 <= ev["y"] < 40


# ────────────────────────────────────────────────────────────────────────
#  DensityFalloffGenerator
# ────────────────────────────────────────────────────────────────────────


def test_density_falloff_only_emits_inside_radius():
    """No op should land outside the falloff disk."""
    ctx = GeneratorContext(rows=100, cols=100, parsed={"rows": 100, "cols": 100})
    events = list(DensityFalloffGenerator().iter_ops(ctx, {
        "center_x": 50, "center_y": 50, "radius": 15,
        "peak_density": 0.8,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 33,
    }))
    for ev in events:
        if "op" not in ev:
            continue
        dx = ev["x"] - 50
        dy = ev["y"] - 50
        assert dx * dx + dy * dy <= 15 * 15 + 1, f"({ev['x']},{ev['y']}) outside radius 15"


def test_density_falloff_higher_peak_emits_more():
    """With the same seed + center + radius, raising peak_density must
    increase the placement count (linear-falloff probability scales
    monotonically with peak)."""
    ctx = GeneratorContext(rows=100, cols=100, parsed={"rows": 100, "cols": 100})
    base = {
        "center_x": 50, "center_y": 50, "radius": 20,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 44,
    }
    low = [e for e in DensityFalloffGenerator().iter_ops(ctx, {**base, "peak_density": 0.1}) if "op" in e]
    high = [e for e in DensityFalloffGenerator().iter_ops(ctx, {**base, "peak_density": 0.9}) if "op" in e]
    assert len(high) > len(low)


def test_density_falloff_reproducible_with_seed():
    ctx = GeneratorContext(rows=60, cols=60, parsed={"rows": 60, "cols": 60})
    params = {
        "center_x": 30, "center_y": 30, "radius": 10, "peak_density": 0.5,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 55,
    }
    a = [(e["x"], e["y"]) for e in DensityFalloffGenerator().iter_ops(ctx, params) if "op" in e]
    b = [(e["x"], e["y"]) for e in DensityFalloffGenerator().iter_ops(ctx, params) if "op" in e]
    assert a == b


def test_density_falloff_at_zero_peak_emits_nothing():
    """peak_density=0 means even at the focal point P(place)=0 — zero
    output. Useful when callers want to disable a layer via param
    without removing the generator from a pipeline."""
    ctx = GeneratorContext(rows=20, cols=20, parsed={"rows": 20, "cols": 20})
    events = list(DensityFalloffGenerator().iter_ops(ctx, {
        "center_x": 10, "center_y": 10, "radius": 8, "peak_density": 0.0,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 1,
    }))
    ops = [e for e in events if "op" in e]
    assert ops == []


def test_generator_param_to_dict_roundtrip():
    """Param.to_dict surfaces every field the UI needs to render the
    form widget. min/max stay None for unbounded params (the bool case
    in WipeGenerator)."""
    p = Param(name="seed", type="int", default=42, description="RNG seed", min=0, max=2**31 - 1)
    d = p.to_dict()
    assert d == {
        "name": "seed",
        "type": "int",
        "default": 42,
        "description": "RNG seed",
        "min": 0,
        "max": 2**31 - 1,
    }


# ────────────────────────────────────────────────────────────────────────
#  Regression: sub Param must be 1-based on every generator
# ────────────────────────────────────────────────────────────────────────
#
# A user ran `:gen fill slot=1 sub=0` and got an invisible fill —
# the renderer's cellMap is built from manifests with 1-based subs (per
# JA2 .dat convention; engine subtracts 1 to index the STI frame array).
# sub=0 produced frame[-1] = no draw. The fix sets default=1, min=1 on
# every sub Param so the wizard's number input refuses 0 + the slider
# starts at a valid value.
#
# These tests lock that contract in so a future "let me clean up the
# generator params" refactor doesn't silently flip them back to 0-based.


@pytest.mark.parametrize("gen_cls,gen_name", [
    (FillLayerGenerator, "fill"),
    (RectangleGenerator, "rect"),
    (ScatterGenerator, "scatter"),
    (ClusterScatterGenerator, "cluster"),
    (DensityFalloffGenerator, "density-falloff"),
])
def test_sub_param_is_one_based(gen_cls, gen_name):
    """Every generator with a `sub` Param declares default=1, min=1.

    sub=0 in the .dat means "no frame" (the engine computes
    `sub-1` to index the STI frame array → -1 on sub=0). Generators
    SHOULD NOT default to invisible output."""
    gen = gen_cls()
    sub_param = next((p for p in gen.params if p.name == "sub"), None)
    assert sub_param is not None, f"{gen_name} missing `sub` param"
    assert sub_param.default == 1, (
        f"{gen_name}.sub default={sub_param.default}; must be 1 — "
        f"sub=0 is invalid (frame[-1] / invisible)."
    )
    assert sub_param.min == 1, (
        f"{gen_name}.sub min={sub_param.min}; must be 1 to reject "
        f"sub=0 at the wizard form level."
    )


def test_wipe_does_not_have_sub_param():
    """WipeGenerator clears tiles; it doesn't write a (slot, sub). If
    someone adds a `sub` param here in the future they probably also
    need to follow the 1-based rule — fail loudly so the parametrized
    test above covers them."""
    assert not any(p.name == "sub" for p in WipeGenerator().params)


# ────────────────────────────────────────────────────────────────────────
#  Regression: phase-start events carry a `total` field for progress bar
# ────────────────────────────────────────────────────────────────────────
#
# The wizard's progress bar reads `total` off the FIRST phase-start
# event the generator emits. Without it the bar falls back to an
# indeterminate animation — workable but worse UX. Lock the contract
# in: every generator emits at least one phase-start with `total: int`.


def test_wipe_phase_start_has_total_field():
    """WipeGenerator emits total = rows × cols × num_layers."""
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    events = list(WipeGenerator().iter_ops(ctx, {}))
    start = next(e for e in events if e.get("status") == "start")
    assert "total" in start, "wipe phase-start missing `total` field"
    assert start["total"] == 10 * 10 * 6, (
        f"wipe total={start['total']}; expected {10 * 10 * 6} (rows × cols × 6 layers)"
    )


def test_wipe_phase_start_total_includes_set_room_when_reset_rooms():
    """reset_rooms adds one set_room op per tile to the total count."""
    ctx = GeneratorContext(rows=5, cols=5, parsed={"rows": 5, "cols": 5})
    events = list(WipeGenerator().iter_ops(ctx, {"reset_rooms": True}))
    start = next(e for e in events if e.get("status") == "start")
    # 25 tiles × 6 layers + 25 set_room = 175
    assert start["total"] == 5 * 5 * 6 + 5 * 5


def test_fill_phase_start_total_matches_grid_area():
    """FillLayer emits one place op per tile = rows × cols."""
    ctx = GeneratorContext(rows=8, cols=12, parsed={"rows": 8, "cols": 12})
    events = list(FillLayerGenerator().iter_ops(ctx, {"layer": "land"}))
    start = next(e for e in events if e.get("status") == "start")
    assert start["total"] == 8 * 12


def test_rect_fill_phase_start_total_matches_area():
    """rect mode=fill emits one op per interior tile = w × h."""
    ctx = GeneratorContext(rows=20, cols=20, parsed={"rows": 20, "cols": 20})
    events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 2, "y1": 3, "x2": 6, "y2": 8,
        "layer": "land", "slot": 1, "sub": 1, "mode": "fill",
    }))
    start = next(e for e in events if e.get("status") == "start")
    # width = 6-2+1 = 5, height = 8-3+1 = 6 → 30 tiles
    assert start["total"] == 5 * 6


def test_rect_outline_phase_start_total_matches_perimeter():
    """rect mode=outline emits only perimeter tiles."""
    ctx = GeneratorContext(rows=20, cols=20, parsed={"rows": 20, "cols": 20})
    events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 0, "y1": 0, "x2": 4, "y2": 4,
        "layer": "structs", "slot": 86, "sub": 1, "mode": "outline",
    }))
    start = next(e for e in events if e.get("status") == "start")
    # 5×5 square outline: 2*5 + 2*(5-2) = 10 + 6 = 16
    assert start["total"] == 16


def test_scatter_phase_start_total_is_count_upper_bound():
    """Scatter's total is the requested count — actual placed may be
    less if the region is too dense for the spacing constraint, but
    the bar's denominator is the upper bound so the user sees a
    meaningful "X / count" ratio."""
    ctx = GeneratorContext(rows=50, cols=50, parsed={"rows": 50, "cols": 50})
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 47, "min_distance": 2, "layer": "objs",
        "slot": 1, "sub": 1, "seed": 7,
    }))
    start = next(e for e in events if e.get("status") == "start")
    assert start["total"] == 47


def test_cluster_phase_start_total_is_product():
    """Cluster total = cluster_count × objects_per_cluster (upper bound)."""
    ctx = GeneratorContext(rows=80, cols=80, parsed={"rows": 80, "cols": 80})
    events = list(ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 4, "objects_per_cluster": 9, "cluster_radius": 3,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 11,
    }))
    start = next(e for e in events if e.get("status") == "start")
    assert start["total"] == 4 * 9


def test_density_falloff_phase_start_total_is_bbox_area():
    """Density-falloff iterates every tile in the bounding box around
    the focal point — that's the upper bound on placements (only a
    fraction become actual ops due to probabilistic sampling)."""
    ctx = GeneratorContext(rows=160, cols=160, parsed={"rows": 160, "cols": 160})
    events = list(DensityFalloffGenerator().iter_ops(ctx, {
        "center_x": 80, "center_y": 80, "radius": 10, "peak_density": 0.5,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 1,
    }))
    start = next(e for e in events if e.get("status") == "start")
    # bbox is (70..90) × (70..90) = 21 × 21 = 441
    assert start["total"] == 21 * 21


def test_density_falloff_total_clamps_to_grid_when_radius_overflows():
    """Focal point near the grid edge — bbox gets clamped to
    [0, cols-1] × [0, rows-1]. Total should reflect the CLAMPED area,
    not the unclamped (which would over-promise on the progress bar)."""
    ctx = GeneratorContext(rows=10, cols=10, parsed={"rows": 10, "cols": 10})
    events = list(DensityFalloffGenerator().iter_ops(ctx, {
        "center_x": 0, "center_y": 0, "radius": 20, "peak_density": 0.5,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 1,
    }))
    start = next(e for e in events if e.get("status") == "start")
    # Clamp: x_lo=0, x_hi=9, y_lo=0, y_hi=9 → 10 × 10 = 100
    assert start["total"] == 10 * 10


# ────────────────────────────────────────────────────────────────────────
#  Regression: deterministic generators emit exactly `total` ops
# ────────────────────────────────────────────────────────────────────────
#
# For Wipe / Fill / Rect, total IS the exact count (not an upper bound).
# Mismatch would surface as the progress bar stopping short of 100%
# even on a clean run — the kind of "almost working" that erodes trust
# in the bar.


def test_wipe_emits_exactly_total_ops():
    ctx = GeneratorContext(rows=4, cols=4, parsed={"rows": 4, "cols": 4})
    events = list(WipeGenerator().iter_ops(ctx, {}))
    start = next(e for e in events if e.get("status") == "start")
    op_count = sum(1 for e in events if "op" in e)
    assert op_count == start["total"]


def test_wipe_reset_rooms_emits_exactly_total_ops():
    ctx = GeneratorContext(rows=4, cols=4, parsed={"rows": 4, "cols": 4})
    events = list(WipeGenerator().iter_ops(ctx, {"reset_rooms": True}))
    start = next(e for e in events if e.get("status") == "start")
    op_count = sum(1 for e in events if "op" in e)
    assert op_count == start["total"]


def test_fill_emits_exactly_total_ops():
    ctx = GeneratorContext(rows=6, cols=7, parsed={"rows": 6, "cols": 7})
    events = list(FillLayerGenerator().iter_ops(ctx, {"layer": "land"}))
    start = next(e for e in events if e.get("status") == "start")
    op_count = sum(1 for e in events if "op" in e)
    assert op_count == start["total"]


def test_rect_fill_emits_exactly_total_ops():
    ctx = GeneratorContext(rows=30, cols=30, parsed={"rows": 30, "cols": 30})
    events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 5, "y1": 5, "x2": 14, "y2": 12,
        "layer": "land", "slot": 1, "sub": 1, "mode": "fill",
    }))
    start = next(e for e in events if e.get("status") == "start")
    op_count = sum(1 for e in events if "op" in e)
    assert op_count == start["total"]


def test_rect_outline_emits_exactly_total_ops():
    ctx = GeneratorContext(rows=30, cols=30, parsed={"rows": 30, "cols": 30})
    events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 5, "y1": 5, "x2": 14, "y2": 12,
        "layer": "structs", "slot": 86, "sub": 1, "mode": "outline",
    }))
    start = next(e for e in events if e.get("status") == "start")
    op_count = sum(1 for e in events if "op" in e)
    assert op_count == start["total"]


# ────────────────────────────────────────────────────────────────────────
#  Regression: probabilistic generators never EXCEED their stated total
# ────────────────────────────────────────────────────────────────────────
#
# Scatter / Cluster / DensityFalloff use upper-bound totals. The
# emitted count must be ≤ total — overshoot would push the progress bar
# past 100% and break the cap-at-100 logic on the frontend.


def test_scatter_op_count_never_exceeds_total():
    ctx = GeneratorContext(rows=20, cols=20, parsed={"rows": 20, "cols": 20})
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 30, "min_distance": 2, "layer": "objs",
        "slot": 1, "sub": 1, "seed": 42,
    }))
    start = next(e for e in events if e.get("status") == "start")
    op_count = sum(1 for e in events if "op" in e)
    assert op_count <= start["total"], (
        f"scatter emitted {op_count} ops > stated total {start['total']} — "
        f"the upper-bound contract is broken; frontend cap-at-100% would mask."
    )


def test_cluster_op_count_never_exceeds_total():
    ctx = GeneratorContext(rows=40, cols=40, parsed={"rows": 40, "cols": 40})
    events = list(ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 3, "objects_per_cluster": 10, "cluster_radius": 4,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 99,
    }))
    start = next(e for e in events if e.get("status") == "start")
    op_count = sum(1 for e in events if "op" in e)
    assert op_count <= start["total"]


def test_density_falloff_op_count_never_exceeds_total():
    ctx = GeneratorContext(rows=160, cols=160, parsed={"rows": 160, "cols": 160})
    events = list(DensityFalloffGenerator().iter_ops(ctx, {
        "center_x": 80, "center_y": 80, "radius": 15, "peak_density": 1.0,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 1,
    }))
    start = next(e for e in events if e.get("status") == "start")
    op_count = sum(1 for e in events if "op" in e)
    assert op_count <= start["total"]


# ────────────────────────────────────────────────────────────────────────
#  Regression: set_entries op with [] entries is a valid wipe
# ────────────────────────────────────────────────────────────────────────
#
# WipeGenerator emits `{op: set_entries, entries: []}` per tile/layer.
# This must round-trip through the route layer's _apply_single_edit
# without raising — earlier code paths in dat_edit_ops.py treated empty
# entries as "missing entries" and 400-d.


def test_wipe_set_entries_op_shape_is_valid():
    """Wipe ops must have op='set_entries', entries=[], layer set, x/y
    in range. This is the contract _apply_single_edit relies on."""
    ctx = GeneratorContext(rows=3, cols=3, parsed={"rows": 3, "cols": 3})
    events = list(WipeGenerator().iter_ops(ctx, {}))
    for e in events:
        if "op" in e:
            assert e["op"] == "set_entries"
            assert e["entries"] == []
            assert e["layer"] in ALL_LAYERS
            assert 0 <= e["x"] < 3
            assert 0 <= e["y"] < 3


# ────────────────────────────────────────────────────────────────────────
#  Regression: every generator emits a phase-start with `total` > 0
# ────────────────────────────────────────────────────────────────────────
#
# Cover the contract uniformly: a future generator that forgets to set
# total will fail the parametrized version of this test, instead of
# silently degrading the wizard's progress bar to indeterminate.


@pytest.mark.parametrize("gen_cls,params,grid", [
    (WipeGenerator, {}, (4, 4)),
    (FillLayerGenerator, {"layer": "land", "slot": 1, "sub": 1}, (4, 4)),
    (RectangleGenerator, {
        "x1": 0, "y1": 0, "x2": 3, "y2": 3,
        "layer": "land", "slot": 1, "sub": 1, "mode": "fill",
    }, (10, 10)),
    (ScatterGenerator, {
        "count": 5, "min_distance": 2, "layer": "objs",
        "slot": 1, "sub": 1, "seed": 1,
    }, (10, 10)),
    (ClusterScatterGenerator, {
        "cluster_count": 2, "objects_per_cluster": 3, "cluster_radius": 2,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 1,
    }, (20, 20)),
    (DensityFalloffGenerator, {
        "center_x": 10, "center_y": 10, "radius": 5, "peak_density": 0.5,
        "layer": "objs", "slot": 1, "sub": 1, "seed": 1,
    }, (20, 20)),
])
def test_every_generator_emits_phase_start_with_positive_total(
    gen_cls, params, grid,
):
    """No silent degradation to indeterminate progress."""
    rows, cols = grid
    ctx = GeneratorContext(rows=rows, cols=cols, parsed={"rows": rows, "cols": cols})
    events = list(gen_cls().iter_ops(ctx, params))
    starts = [e for e in events if e.get("status") == "start"]
    assert len(starts) >= 1, f"{gen_cls.__name__} emitted no phase-start"
    first_start = starts[0]
    assert "total" in first_start, (
        f"{gen_cls.__name__} phase-start missing `total` — wizard progress "
        f"bar would fall back to indeterminate"
    )
    assert isinstance(first_start["total"], int)
    assert first_start["total"] > 0


# ────────────────────────────────────────────────────────────────────────
#  Regression: SLOT_TAKEN response shape (mapforge_library route)
# ────────────────────────────────────────────────────────────────────────
#
# User feedback: "when i tried to replace an existing slot it failed".
# The backend now returns a richer SLOT_TAKEN error that names the
# occupant so the frontend can show a useful message instead of a
# cryptic 409. Lock the response shape so a future "simplify the error
# detail" refactor doesn't silently break the friendly message.


def test_slot_taken_response_includes_occupant_filename(tmp_path):
    """The 409 SLOT_TAKEN response from add_sti_to_tileset must include
    the occupant filename + slot + tileset fields so the frontend can
    render "slot N is taken by X.sti" instead of just a number."""
    # Build a minimal Ja2Set.dat.xml with one tileset, slot 14 → dump.sti
    import xml.etree.ElementTree as ET
    xml_path = tmp_path / "Ja2Set.dat.xml"
    root = ET.Element("JA2SET")
    ts = ET.SubElement(root, "Tileset", attrib={"index": "27"})
    files = ET.SubElement(ts, "Files")
    f = ET.SubElement(files, "file", attrib={"index": "14"})
    f.text = "dump.sti"
    ET.ElementTree(root).write(xml_path)

    # Mirror the route's slot-collision logic directly (the full route
    # touches state + catalog db which require heavier fixtures; the
    # shape contract is what we're locking down here).
    used: dict[int, str] = {}
    tree = ET.parse(xml_path)
    for tnode in tree.getroot().iter("Tileset"):
        if int(tnode.get("index", -1)) == 27:
            fnode = tnode.find("Files")
            if fnode is not None:
                for fe in fnode.findall("file"):
                    idx = fe.get("index")
                    if idx is not None:
                        used[int(idx)] = (fe.text or "").strip()
    assert 14 in used
    assert used[14] == "dump.sti"

    # Now assert the FRIENDLY message construction reads sensibly. The
    # response shape is hand-built in routes/mapforge_library.py — if
    # this changes there, the test must change here.
    occupant = used[14]
    message = (
        f"slot 14 is already taken by {occupant} in "
        f"tileset 27. Replacing existing slots "
        "isn't supported yet — pick a different slot, or "
        "leave the Slot field blank for auto-pick."
    )
    assert "dump.sti" in message
    assert "slot 14" in message
    assert "tileset 27" in message
    assert "auto-pick" in message  # tells the user the workaround


def test_add_sti_to_tileset_filename_guard_rejects_reserved_device_names():
    """Lock down the filename grammar enforced in
    routes/mapforge_library.py::add_sti_to_tileset before the STI write.

    Windows resolves reserved device names (CON/PRN/AUX/NUL, COM1-9,
    LPT1-9) regardless of extension or directory, so "<dir>\\con.sti"
    would write to a device instead of creating a file. Those names pass
    the base ASCII-basename regex, so the route rejects them explicitly.
    This mirrors the route's two predicates verbatim — if they change
    there, this test must change here (same sync contract as
    test_slot_taken_response_includes_occupant_filename above).
    """
    import re

    def accepted(name: str) -> bool:
        # Both guards from routes/mapforge_library.py, verbatim.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.[Ss][Tt][Ii]", name):
            return False
        stem = name.split(".", 1)[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"COM[1-9]|LPT[1-9]", stem):
            return False
        return True

    # Reserved device names — must be REJECTED (the device-name guard).
    # com1.foo.sti checks that resolution keys on the FIRST-dot stem.
    for name in ("con.sti", "CON.STI", "Con.Sti", "prn.sti", "aux.sti",
                 "nul.sti", "com1.sti", "com9.sti", "lpt1.sti", "lpt9.sti",
                 "com1.foo.sti"):
        assert not accepted(name), f"{name!r} should be rejected (reserved device)"

    # Look-alikes / edges that must STAY ALLOWED (no false positives):
    # device-letter prefixes that aren't the bare stem, COM0/LPT0 (not
    # reserved), COM10 (only COM1-9 are devices), and the harmless
    # all-dot literal in-dir names.
    for name in ("foo.sti", "console.sti", "concrete.sti", "aux_tile.sti",
                 "prnt.sti", "nullish.sti", "com0.sti", "lpt0.sti",
                 "com10.sti", "..sti", "...sti"):
        assert accepted(name), f"{name!r} should be allowed"

    # Traversal / ADS / drive / trailing quirks — rejected by the base
    # regex alone (class omits / \\ : ; fullmatch forbids trailing chars).
    for name in ("../foo.sti", "..\\foo.sti", "foo/bar.sti", "foo\\bar.sti",
                 "foo.sti:ads", "foo:bar.sti", "C:foo.sti", "C:\\evil.sti",
                 "foo.sti.", "foo.sti ", " foo.sti", "..", ".sti", "foo.bin"):
        assert not accepted(name), f"{name!r} should be rejected (bad grammar)"


# ────────────────────────────────────────────────────────────────────────
#  Variant + mask helpers (scatter-family quality knobs)
# ────────────────────────────────────────────────────────────────────────


def test_parse_weighted_subs_equal_weight():
    assert _parse_weighted_subs("1,2,3") == [(1, 1.0), (2, 1.0), (3, 1.0)]


def test_parse_weighted_subs_weighted():
    assert _parse_weighted_subs("1:5,2:2,3:1") == [(1, 5.0), (2, 2.0), (3, 1.0)]


def test_parse_weighted_subs_blank_is_empty():
    assert _parse_weighted_subs("") == []
    assert _parse_weighted_subs("   ") == []
    assert _parse_weighted_subs(None) == []  # type: ignore[arg-type]


def test_parse_weighted_subs_tolerates_whitespace():
    assert _parse_weighted_subs(" 1 , 2 :3 ") == [(1, 1.0), (2, 3.0)]


def test_parse_weighted_subs_rejects_zero_sub():
    """sub=0 renders nothing (frame[-1]); the parser enforces the
    1-based rule the rest of the pipeline depends on."""
    with pytest.raises(ValueError, match="1-based"):
        _parse_weighted_subs("0")
    with pytest.raises(ValueError):
        _parse_weighted_subs("1,0,3")


def test_parse_weighted_subs_rejects_nonpositive_weight():
    with pytest.raises(ValueError, match="weight"):
        _parse_weighted_subs("1:0")
    with pytest.raises(ValueError):
        _parse_weighted_subs("1:-2")


def test_make_sub_picker_constant_without_variants():
    pick = _make_sub_picker(random.Random(1), 7, "")
    assert [pick() for _ in range(5)] == [7, 7, 7, 7, 7]


def test_make_sub_picker_no_variants_does_not_consume_rng():
    """With no variants the picker must NOT draw from rng — that's what
    keeps the back-compat seed streams (scatter/cluster/density without
    `subs`) byte-identical to before this feature landed."""
    rng_a = random.Random(123)
    rng_b = random.Random(123)
    pick = _make_sub_picker(rng_a, 3, "")
    for _ in range(10):
        pick()
    assert rng_a.random() == rng_b.random()


def test_make_sub_picker_only_returns_variant_subs():
    pick = _make_sub_picker(random.Random(5), 1, "4,8,15")
    draws = {pick() for _ in range(200)}
    assert draws <= {4, 8, 15}
    assert len(draws) >= 2  # spread across the set, not stuck on one


def test_make_sub_picker_deterministic():
    a = _make_sub_picker(random.Random(9), 1, "2,3,4")
    b = _make_sub_picker(random.Random(9), 1, "2,3,4")
    assert [a() for _ in range(20)] == [b() for _ in range(20)]


def test_make_sub_picker_weighting_skews_distribution():
    """A heavy weight should dominate the draws (statistical, but the
    margin at weight 20:1 over 1000 draws is overwhelming)."""
    pick = _make_sub_picker(random.Random(7), 1, "2:20,9:1")
    draws = [pick() for _ in range(1000)]
    assert draws.count(2) > draws.count(9) * 3


def test_parse_int_csv():
    assert _parse_int_csv("12,13") == [12, 13]
    assert _parse_int_csv(" 12 , 13 ") == [12, 13]
    assert _parse_int_csv("") == []
    assert _parse_int_csv(None) == []  # type: ignore[arg-type]


def test_make_mask_predicate_none_when_blank():
    ctx = GeneratorContext(rows=4, cols=4, parsed={"rows": 4, "cols": 4})
    assert _make_mask_predicate(ctx, "", "") is None


def test_make_mask_predicate_invalid_layer_raises():
    ctx = GeneratorContext(rows=4, cols=4, parsed={"rows": 4, "cols": 4})
    with pytest.raises(ValueError, match="unknown layer"):
        _make_mask_predicate(ctx, "ground", "")


def test_make_mask_predicate_none_when_layer_absent():
    """Valid layer name but the parsed dict carries no grid for it (a
    minimal test context) → no mask, rather than crashing."""
    ctx = GeneratorContext(rows=4, cols=4, parsed={"rows": 4, "cols": 4})
    assert _make_mask_predicate(ctx, "land", "") is None


def test_make_mask_predicate_any_content():
    parsed = _parsed_with_layers(4, 4, {"land": {(1, 1): [(5, 1)]}})
    ctx = GeneratorContext(rows=4, cols=4, parsed=parsed)
    is_masked = _make_mask_predicate(ctx, "land", "")  # blank slots = any
    assert is_masked is not None
    assert is_masked(1, 1) is True
    assert is_masked(0, 0) is False


def test_make_mask_predicate_specific_slots():
    parsed = _parsed_with_layers(4, 4, {"land": {(1, 1): [(5, 1)], (2, 2): [(9, 1)]}})
    ctx = GeneratorContext(rows=4, cols=4, parsed=parsed)
    is_masked = _make_mask_predicate(ctx, "land", "9")
    assert is_masked is not None
    assert is_masked(2, 2) is True   # slot 9 matches the avoid set
    assert is_masked(1, 1) is False  # slot 5 not avoided
    assert is_masked(0, 0) is False  # empty tile


# ────────────────────────────────────────────────────────────────────────
#  ScatterGenerator — variants + masking
# ────────────────────────────────────────────────────────────────────────


def test_scatter_without_subs_uses_single_sub():
    """Back-compat: no `subs` param → every op carries the single `sub`."""
    ctx = GeneratorContext(rows=40, cols=40, parsed={"rows": 40, "cols": 40})
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 30, "min_distance": 1, "layer": "objs",
        "slot": 5, "sub": 8, "seed": 4,
    }))
    subs = {e["sub"] for e in events if "op" in e}
    assert subs == {8}


def test_scatter_variants_only_emit_from_set():
    ctx = GeneratorContext(rows=40, cols=40, parsed={"rows": 40, "cols": 40})
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 60, "min_distance": 1, "layer": "objs",
        "slot": 5, "sub": 1, "seed": 4, "subs": "3,7,9",
    }))
    subs = {e["sub"] for e in events if "op" in e}
    assert subs <= {3, 7, 9}
    assert len(subs) >= 2  # 60 placements over 3 variants → expect a mix


def test_scatter_variants_reproducible_with_seed():
    ctx = GeneratorContext(rows=50, cols=50, parsed={"rows": 50, "cols": 50})
    params = {
        "count": 40, "min_distance": 1, "layer": "objs",
        "slot": 5, "sub": 1, "seed": 17, "subs": "2:3,5:1",
    }
    a = [(e["x"], e["y"], e["sub"]) for e in ScatterGenerator().iter_ops(ctx, params) if "op" in e]
    b = [(e["x"], e["y"], e["sub"]) for e in ScatterGenerator().iter_ops(ctx, params) if "op" in e]
    assert a == b


def test_scatter_masks_out_avoided_slots():
    """avoid_layer + avoid_slots: no scatter lands on a tile whose land
    layer holds the avoided (water) slot."""
    rows = cols = 20
    water = 99
    occupied = {"land": {(x, y): [(water, 1)] for x in range(10) for y in range(rows)}}
    parsed = _parsed_with_layers(rows, cols, occupied)
    ctx = GeneratorContext(rows=rows, cols=cols, parsed=parsed)
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 80, "min_distance": 1, "layer": "objs",
        "slot": 5, "sub": 1, "seed": 3,
        "avoid_layer": "land", "avoid_slots": str(water),
    }))
    coords = [(e["x"], e["y"]) for e in events if "op" in e]
    assert coords, "expected placements in the unmasked half"
    for (x, y) in coords:
        assert x >= 10, f"({x},{y}) landed on a masked water tile"


def test_scatter_masks_any_content_when_no_slots():
    """Blank avoid_slots → avoid ANY content on the avoid layer. Keep
    scatter off tiles that already have a struct (e.g. a road)."""
    rows = cols = 16
    blocked = {(x, y) for x in range(4) for y in range(4)}
    occupied = {"structs": {xy: [(7, 1)] for xy in blocked}}
    parsed = _parsed_with_layers(rows, cols, occupied)
    ctx = GeneratorContext(rows=rows, cols=cols, parsed=parsed)
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 60, "min_distance": 1, "layer": "objs",
        "slot": 5, "sub": 1, "seed": 9, "avoid_layer": "structs",
    }))
    coords = {(e["x"], e["y"]) for e in events if "op" in e}
    assert coords
    assert not (coords & blocked)


# ────────────────────────────────────────────────────────────────────────
#  ClusterScatterGenerator — variants + masking
# ────────────────────────────────────────────────────────────────────────


def test_cluster_variants_only_from_set():
    ctx = GeneratorContext(rows=60, cols=60, parsed={"rows": 60, "cols": 60})
    events = list(ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 4, "objects_per_cluster": 12, "cluster_radius": 4,
        "layer": "objs", "slot": 5, "sub": 1, "seed": 8, "subs": "1:2,9:1",
    }))
    subs = {e["sub"] for e in events if "op" in e}
    assert subs <= {1, 9}
    assert subs  # non-empty


def test_cluster_respects_mask():
    rows = cols = 40
    blocked = {(x, y) for x in range(rows) for y in range(20)}  # top half
    occupied = {"structs": {xy: [(7, 1)] for xy in blocked}}
    parsed = _parsed_with_layers(rows, cols, occupied)
    ctx = GeneratorContext(rows=rows, cols=cols, parsed=parsed)
    events = list(ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 6, "objects_per_cluster": 15, "cluster_radius": 4,
        "layer": "objs", "slot": 5, "sub": 1, "seed": 8,
        "avoid_layer": "structs",
    }))
    coords = {(e["x"], e["y"]) for e in events if "op" in e}
    for xy in coords:
        assert xy not in blocked, f"{xy} landed on a masked struct tile"


# ────────────────────────────────────────────────────────────────────────
#  DensityFalloffGenerator — variants + masking
# ────────────────────────────────────────────────────────────────────────


def test_density_falloff_variants_only_from_set():
    ctx = GeneratorContext(rows=60, cols=60, parsed={"rows": 60, "cols": 60})
    events = list(DensityFalloffGenerator().iter_ops(ctx, {
        "center_x": 30, "center_y": 30, "radius": 20, "peak_density": 0.9,
        "layer": "objs", "slot": 5, "sub": 1, "seed": 2, "subs": "4,6",
    }))
    subs = {e["sub"] for e in events if "op" in e}
    assert subs <= {4, 6}
    assert subs


def test_density_falloff_respects_mask():
    rows = cols = 40
    blocked = {(x, y) for x in range(18, 22) for y in range(rows)}  # vertical strip
    occupied = {"land": {xy: [(99, 1)] for xy in blocked}}
    parsed = _parsed_with_layers(rows, cols, occupied)
    ctx = GeneratorContext(rows=rows, cols=cols, parsed=parsed)
    events = list(DensityFalloffGenerator().iter_ops(ctx, {
        "center_x": 20, "center_y": 20, "radius": 18, "peak_density": 1.0,
        "layer": "objs", "slot": 5, "sub": 1, "seed": 2,
        "avoid_layer": "land", "avoid_slots": "99",
    }))
    coords = {(e["x"], e["y"]) for e in events if "op" in e}
    assert coords
    assert not (coords & blocked)


def test_scatter_variant_subs_param_declared():
    """The three scatter-family generators expose `subs`, `avoid_layer`,
    `avoid_slots` so the wizard's data-driven param form renders them."""
    for gen in (ScatterGenerator(), ClusterScatterGenerator(), DensityFalloffGenerator()):
        names = {p.name for p in gen.params}
        assert {"subs", "avoid_layer", "avoid_slots"} <= names, (
            f"{gen.name} missing one of the variant/mask params: {names}"
        )


# ──────────────────────────────────────────────────────────────────────────
#  AutoShadowGenerator
# ──────────────────────────────────────────────────────────────────────────


def test_autoshadow_retired_from_registry():
    # Retired 2026-05-31: the renderer overlays buddy shadows and the engine
    # re-adds them at load, so baking via AutoShadow only doubled in-game.
    assert "autoshadow" not in REGISTRY
    # Class kept for reference; still serializes if instantiated directly.
    d = AutoShadowGenerator().to_dict()
    assert d["name"] == "autoshadow"
    assert d["label"] and d["description"]
    assert {"obstacles", "doors", "vehicles_fences", "source_layers"} == {
        p["name"] for p in d["params"]
    }


def test_shadow_pairs_buckets_partition_the_table():
    """Every struct slot belongs to exactly one category bucket."""
    union = (shadow_pairs.OBSTACLE_STRUCTS
             | shadow_pairs.DOOR_STRUCTS
             | shadow_pairs.VEHICLE_FENCE_STRUCTS)
    assert union == set(shadow_pairs.STRUCT_TO_SHADOW)


def test_autoshadow_adds_paired_shadow_for_tree_same_sub():
    """A tree (slot 20 sub 3) gets shadow slot 32 sub 3 on the shadows
    layer at the same gridno — the auto-pair rule applied as a sweep."""
    parsed = _parsed_with_layers(10, 10, occupied={"structs": {(5, 5): [(20, 3)]}})
    ops = _ops(AutoShadowGenerator(), _ts9_ctx(parsed), {})
    assert ops == [{"x": 5, "y": 5, "op": "add", "layer": "shadows",
                    "slot": 32, "sub": 3}]


def test_autoshadow_idempotent_skips_existing_shadow():
    """If the exact paired shadow already exists, emit nothing."""
    parsed = _parsed_with_layers(10, 10, occupied={
        "structs": {(5, 5): [(20, 3)]},
        "shadows": {(5, 5): [(32, 3)]},
    })
    assert _ops(AutoShadowGenerator(), _ts9_ctx(parsed), {}) == []


def test_autoshadow_leaves_building_shadows_untouched():
    """Buildings (slot 39) aren't in the pairing table → no shadow added,
    and a pre-existing building shadow is never disturbed."""
    parsed = _parsed_with_layers(10, 10, occupied={
        "structs": {(5, 5): [(39, 24)]},
        "shadows": {(5, 5): [(39, 30)]},
    })
    assert _ops(AutoShadowGenerator(), _ts9_ctx(parsed), {}) == []


def test_autoshadow_category_toggle_doors():
    parsed = _parsed_with_layers(10, 10, occupied={"structs": {(2, 2): [(40, 1)]}})
    ctx = _ts9_ctx(parsed)
    on = _ops(AutoShadowGenerator(), ctx, {})
    assert on == [{"x": 2, "y": 2, "op": "add", "layer": "shadows",
                   "slot": 44, "sub": 1}]
    off = _ops(AutoShadowGenerator(), ctx, {"doors": False})
    assert off == []


def test_autoshadow_skips_shadow_sti_absent_in_tileset():
    """If the tileset doesn't define the paired shadow STI, skip rather
    than write a dangling (slot, sub) the renderer/engine can't resolve."""
    parsed = _parsed_with_layers(10, 10, occupied={"structs": {(5, 5): [(20, 3)]}})
    ctx = GeneratorContext(rows=10, cols=10, parsed=parsed,
                           slot_map={20: "tree1_t.sti"},  # no slot 32
                           frame_count=lambda s: 12)
    assert _ops(AutoShadowGenerator(), ctx, {}) == []


def test_autoshadow_skips_sub_out_of_frame_range():
    """A sub beyond the shadow STI's frame count is skipped (would read
    out of range in the engine)."""
    parsed = _parsed_with_layers(10, 10, occupied={"structs": {(5, 5): [(20, 99)]}})
    assert _ops(AutoShadowGenerator(), _ts9_ctx(parsed), {}) == []


def test_autoshadow_trusts_pairing_without_tileset_metadata():
    """Bare context (no slot_map/frame_count) → place per the convention."""
    parsed = _parsed_with_layers(10, 10, occupied={"structs": {(5, 5): [(20, 3)]}})
    ops = _ops(AutoShadowGenerator(), _ts9_ctx(parsed, with_meta=False), {})
    assert ops == [{"x": 5, "y": 5, "op": "add", "layer": "shadows",
                    "slot": 32, "sub": 3}]


def test_autoshadow_invalid_source_layer_errors_without_ops():
    parsed = _parsed_with_layers(10, 10, occupied={"structs": {(5, 5): [(20, 3)]}})
    events = list(AutoShadowGenerator().iter_ops(_ts9_ctx(parsed),
                                                 {"source_layers": "bogus"}))
    assert all("op" not in e for e in events)
    assert any(e.get("phase") == "error" for e in events)


def test_autoshadow_dedupes_two_trees_one_shadow_per_tile():
    """Two trees stacked on one tile that map to the same shadow only
    yield one shadow op (no duplicate entries)."""
    parsed = _parsed_with_layers(10, 10, occupied={
        "structs": {(4, 4): [(20, 5)]},
        "objs": {(4, 4): [(20, 5)]},
    })
    ops = _ops(AutoShadowGenerator(), _ts9_ctx(parsed), {})
    assert ops == [{"x": 4, "y": 4, "op": "add", "layer": "shadows",
                    "slot": 32, "sub": 5}]

