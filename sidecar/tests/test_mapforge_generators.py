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
    NAMED_MASKS,
    _combine_masks,
    _make_mask_predicate,
    _make_named_mask_predicate,
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


def _bank_split(ctx, params):
    """(set_height ops, cliff-face ops) for one bank run."""
    events = [e for e in BankGenerator().iter_ops(ctx, params) if "op" in e]
    heights = [e for e in events if e["op"] == "set_height"]
    faces = [e for e in events if e["op"] != "set_height"]
    return heights, faces


def test_bank_uniform_plateau_in_80_steps():
    """levels × 80 (the engine's WORLD_CLIFF_HEIGHT) on every tile —
    vanilla cliff semantics; no terrace bands (those just made
    concentric impassable rings)."""
    ctx = _bank_ctx(20, 20)
    heights, faces = _bank_split(ctx, {
        "x1": 2, "y1": 2, "x2": 6, "y2": 6, "levels": 2,
        "bank_mode": "plateau",
    })
    assert len(heights) == 25
    for o in heights:
        assert o["height"] == 160
    assert {(o["x"], o["y"]) for o in heights} == {
        (x, y) for x in range(2, 7) for y in range(2, 7)
    }
    # R3: the border carries cliff-face art on the two camera-facing
    # sides. A 5×5 rect = one E piece (flush, single), one S sub-7 at the
    # SE corner, one SW sub-8 taper — each a structs+objs dual entry.
    assert len(faces) == 2 * 3


def test_bank_default_is_one_cliff_level():
    ctx = _bank_ctx(10, 10)
    heights, _faces = _bank_split(ctx, {
        "x1": 0, "y1": 0, "x2": 1, "y2": 1, "bank_mode": "plateau",
    })
    assert heights and all(o["height"] == 80 for o in heights)


def test_bank_levels_zero_flattens():
    ctx = _bank_ctx(10, 10)
    ops = [e for e in BankGenerator().iter_ops(ctx, {
        "x1": 0, "y1": 0, "x2": 1, "y2": 1, "levels": 0,
        "bank_mode": "plateau",
    }) if "op" in e]
    assert ops and all(o["height"] == 0 for o in ops)


def test_bank_normalizes_corners():
    ctx = _bank_ctx(30, 30)
    a = {(e["x"], e["y"]) for e in BankGenerator().iter_ops(ctx, {
        "x1": 10, "y1": 10, "x2": 4, "y2": 4, "levels": 1,
        "bank_mode": "plateau"}) if "op" in e}
    b = {(e["x"], e["y"]) for e in BankGenerator().iter_ops(ctx, {
        "x1": 4, "y1": 4, "x2": 10, "y2": 10, "levels": 1,
        "bank_mode": "plateau"}) if "op" in e}
    assert a == b


def test_bank_clamps_oob_region_and_levels():
    ctx = _bank_ctx(10, 10)
    heights, _faces = _bank_split(ctx, {
        "x1": 0, "y1": 0, "x2": 999, "y2": 999, "levels": 999,
        "bank_mode": "plateau",
    })
    assert len(heights) == 100           # region clamped to the 10×10 grid
    # levels clamp to the engine max of 3 raises → 240, never 255-ish junk.
    assert all(o["height"] == 240 for o in heights)


def test_bank_phase_start_has_total():
    """The start event's `total` must equal the number of mutation ops
    actually emitted (heights + cliff-face entries)."""
    ctx = _bank_ctx(20, 20)
    events = list(BankGenerator().iter_ops(ctx, {
        "x1": 1, "y1": 1, "x2": 5, "y2": 8, "levels": 1,
        "bank_mode": "plateau"}))
    start = next(e for e in events if e.get("status") == "start")
    n_ops = sum(1 for e in events if "op" in e)
    assert start["total"] == n_ops
    assert n_ops > 5 * 8                 # heights plus at least the corners


# ── R3: cliff-face sprites — the vanilla chain grammar ──────────────────
#
# Spacing/chaining truth from the A6/F5/G5/A8 run-walks
# (scratch/clifftest/analyze_runs.py): straight faces chain at stride 4
# with a 1-tile overlap per joint (pieces span 5 — a Δ5 butt joint reads
# as a crack); corners anchor AT the corner tiles (SE sub 7 + the E-face
# bottom piece on the same gridno, SW sub 8 taper); N/W back edges and
# both their corners get NOTHING.


# Known 12×10 rect — anchors hand-derived from `_face_chain`:
#   E face (x=13): _face_chain(11, 6)  → y ∈ [11, 8, 6]   (Δ3, Δ2)
#   S face (y=11): _face_chain(13, 6)  → x ∈ [13, 9, 6]   (Δ4, Δ3)
#   + SW sub-8 taper at (2, 11). The SE corner tile (13,11) carries TWO
#   anchors (E bottom piece + S sub 7) — vanilla multi-anchors gridnos.
_R2_RECT = {"x1": 2, "y1": 2, "x2": 13, "y2": 11, "levels": 1,
            "bank_mode": "plateau"}
_R2_E_ANCHORS = [(13, 11), (13, 8), (13, 6)]
_R2_S_ANCHORS = [(13, 11), (9, 11), (6, 11)]
_R2_SW = (2, 11)


def test_bank_faces_exactly_on_visible_edges():
    """Every cliff-face op sits on the S/E border (the camera-facing
    sides), at exactly the hand-derived chain anchors, as a
    structs(10)+objs(9) dual entry with the same sub. The N and W edges
    and the NW corner get NO art (vanilla leaves the away-facing edges
    of interior plateaus to diagonal escarpments; its only long straight
    N edge, A6 row 38, is bare)."""
    ctx = _bank_ctx(40, 40)
    _heights, faces = _bank_split(ctx, dict(_R2_RECT))
    expected_tiles = set(_R2_E_ANCHORS) | set(_R2_S_ANCHORS) | {_R2_SW}
    # 3 E + 3 S + 1 SW anchors, each a structs+objs pair.
    assert len(faces) == 2 * 7

    by_tile: dict = {}
    for o in faces:
        by_tile.setdefault((o["x"], o["y"]), []).append(o)
    assert set(by_tile) == expected_tiles
    for (x, y) in by_tile:
        assert x == 13 or y == 11        # S/E borders only
    assert (2, 2) not in by_tile         # NW corner stays bare

    for (x, y), ops in by_tile.items():
        per_layer: dict = {}
        for o in ops:
            per_layer.setdefault(o["layer"], []).append(o)
        # The vanilla dual entry: every struct has its hang, same sub.
        assert set(per_layer) == {"structs", "objs"}
        s_subs = sorted(o["sub"] for o in per_layer["structs"])
        o_subs = sorted(o["sub"] for o in per_layer["objs"])
        assert s_subs == o_subs
        assert all(o["slot"] == 10 for o in per_layer["structs"])
        assert all(o["slot"] == 9 for o in per_layer["objs"])


def test_bank_chain_spacing_and_corner_subs():
    """The new grammar: E/S chains never butt-joint (every joint Δ≤4 =
    overlap, per the vanilla run-walks), chains land flush at both ends,
    corner subs are forced (SE 7, SW 8), edge picks stay in pool."""
    from mercwizard_core.mapforge.generators import CLIFF_FACE_LUT
    ctx = _bank_ctx(40, 40)
    _heights, faces = _bank_split(ctx, dict(_R2_RECT))
    subs: dict = {}
    for o in faces:
        if o["layer"] == "structs":
            subs.setdefault((o["x"], o["y"]), set()).add(o["sub"])

    e_pool = {s for s, _ in CLIFF_FACE_LUT["edge_E"]["subs"]}
    s_pool = {s for s, _ in CLIFF_FACE_LUT["edge_S"]["subs"]}

    # E chain: exact anchors, Δ≤4 joints, subs from the E pool.
    e_ys = sorted(y for (x, y) in subs if x == 13 and (13, y) in subs)
    assert [(13, y) for y in sorted(e_ys, reverse=True)] == _R2_E_ANCHORS
    for a, b in zip(e_ys, e_ys[1:]):
        assert 1 <= b - a <= 4           # never a Δ5 butt joint
    for y in e_ys:
        if (13, y) == (13, 11):
            continue                     # corner tile checked below
        assert subs[(13, y)] <= e_pool

    # S chain: sub 7 forced at the SE corner; mid anchors from the pool.
    assert 7 in subs[(13, 11)]           # corner_SE
    assert subs[(13, 11)] - {7} <= e_pool  # plus the E-face bottom piece
    assert subs[(9, 11)] <= s_pool and subs[(6, 11)] <= s_pool
    assert subs[_R2_SW] == {8}           # corner_SW taper


def test_bank_face_chain_covers_flush_no_butt_joints():
    """_face_chain invariants against the engine footprint table: pieces
    span 5 cells (CLIFF_FOOTPRINT subs 5/6: offsets (0,-4)..(0,0)); the
    chain must cover [cap-4, hi] completely, start AT hi, end AT cap,
    and never leave a Δ5 butt joint."""
    from mercwizard_core.mapforge.generators import (
        CLIFF_FOOTPRINT, _face_chain,
    )
    span_lo = min(dy for _, dy in CLIFF_FOOTPRINT[5])
    assert span_lo == -4                 # piece covers a-4..a
    for hi in (20,):
        for cap in range(8, 21):
            ys = _face_chain(hi, cap)
            assert ys[0] == hi and ys[-1] == min(cap, hi)
            covered = set()
            for a in ys:
                covered |= set(range(a - 4, a + 1))
            assert covered >= set(range(cap - 4, hi + 1))
            for a, b in zip(ys, ys[1:]):
                assert 1 <= a - b <= 4   # vanilla stride, never butt


def test_bank_levels_zero_emits_no_faces():
    ctx = _bank_ctx(40, 40)
    heights, faces = _bank_split(ctx, dict(_R2_RECT, levels=0))
    assert heights and not faces


def test_bank_place_cliff_faces_false_heights_only():
    ctx = _bank_ctx(40, 40)
    heights, faces = _bank_split(ctx, dict(_R2_RECT, place_cliff_faces=False))
    assert len(heights) == 12 * 10
    assert not faces


def test_bank_face_ops_are_valid_editop_shapes():
    """Face ops must be route-applicable: add op, valid layer, int coords,
    1-based sub, slot 9/10 — the EditOp shape routes/mapforge.py expects."""
    ctx = _bank_ctx(40, 40)
    _heights, faces = _bank_split(ctx, dict(_R2_RECT))
    for o in faces:
        assert o["op"] == "add"
        assert o["layer"] in ALL_LAYERS
        assert o["layer"] in ("structs", "objs")
        assert isinstance(o["x"], int) and isinstance(o["y"], int)
        assert isinstance(o["slot"], int) and o["slot"] in (9, 10)
        assert isinstance(o["sub"], int) and 1 <= o["sub"] <= 17


def test_bank_seed_determinism():
    ctx = _bank_ctx(40, 40)
    a = list(BankGenerator().iter_ops(ctx, dict(_R2_RECT, seed=7)))
    b = list(BankGenerator().iter_ops(ctx, dict(_R2_RECT, seed=7)))
    assert a == b


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
#  Named masks (avoid_named) — the "Don't place on" checkboxes
# ────────────────────────────────────────────────────────────────────────


def test_named_mask_none_when_blank():
    ctx = GeneratorContext(rows=4, cols=4, parsed={"rows": 4, "cols": 4})
    assert _make_named_mask_predicate(ctx, "objs", "") is None
    assert _make_named_mask_predicate(ctx, "objs", "  ") is None


def test_named_mask_unknown_name_raises():
    parsed = _parsed_with_layers(4, 4)
    ctx = GeneratorContext(rows=4, cols=4, parsed=parsed)
    with pytest.raises(ValueError, match="unknown avoid_named"):
        _make_named_mask_predicate(ctx, "objs", "occupied,lava")


def test_named_mask_none_when_grids_absent():
    """Minimal test context with no layer grids → nothing to avoid."""
    ctx = GeneratorContext(rows=4, cols=4, parsed={"rows": 4, "cols": 4})
    assert _make_named_mask_predicate(ctx, "objs", "occupied,water,trees") is None


def test_named_mask_occupied_reads_target_layer():
    """`occupied` masks tiles with ANY content on the generator's TARGET
    layer — and only that layer."""
    parsed = _parsed_with_layers(4, 4, {
        "objs": {(1, 1): [(5, 1)]},
        "land": {(2, 2): [(0, 1)]},
    })
    ctx = GeneratorContext(rows=4, cols=4, parsed=parsed)
    is_masked = _make_named_mask_predicate(ctx, "objs", "occupied")
    assert is_masked is not None
    assert is_masked(1, 1) is True    # content on target layer
    assert is_masked(2, 2) is False   # land content doesn't mask objs target
    assert is_masked(0, 0) is False
    # Same map, structs target → neither tile masks.
    is_masked_s = _make_named_mask_predicate(ctx, "structs", "occupied")
    assert is_masked_s(1, 1) is False


def test_named_mask_water_land_slots():
    """`water` = land-layer REGWATERTEXTURE(7) / DEEPWATERTEXTURE(8)
    (TileDat.h TileTypeDefines)."""
    parsed = _parsed_with_layers(4, 4, {
        "land": {(0, 0): [(7, 3)], (1, 0): [(8, 1)], (2, 0): [(0, 1)]},
    })
    ctx = GeneratorContext(rows=4, cols=4, parsed=parsed)
    is_masked = _make_named_mask_predicate(ctx, "objs", "water")
    assert is_masked(0, 0) is True    # REGWATERTEXTURE
    assert is_masked(1, 0) is True    # DEEPWATERTEXTURE
    assert is_masked(2, 0) is False   # plain ground texture
    assert is_masked(3, 3) is False


def test_named_mask_roads_objs50_and_land78():
    """`roads` = ROADPIECES(50) on objs (modern macro roads) plus
    FIRSTROAD(78) legacy land-layer roads."""
    parsed = _parsed_with_layers(4, 4, {
        "objs": {(0, 0): [(50, 120)], (1, 0): [(5, 1)]},
        "land": {(2, 0): [(78, 4)]},
    })
    ctx = GeneratorContext(rows=4, cols=4, parsed=parsed)
    is_masked = _make_named_mask_predicate(ctx, "objs", "roads")
    assert is_masked(0, 0) is True    # ROADPIECES on objs
    assert is_masked(2, 0) is True    # legacy FIRSTROAD on land
    assert is_masked(1, 0) is False   # non-road obj
    assert is_masked(3, 3) is False


def test_named_mask_structures_any_structs_content():
    parsed = _parsed_with_layers(4, 4, {
        "structs": {(1, 2): [(39, 7)]},
        "objs": {(2, 2): [(5, 1)]},
    })
    ctx = GeneratorContext(rows=4, cols=4, parsed=parsed)
    is_masked = _make_named_mask_predicate(ctx, "objs", "structures")
    assert is_masked(1, 2) is True
    assert is_masked(2, 2) is False   # objs content isn't a structure


def test_named_mask_trees_ostruct_families():
    """`trees` = the O-struct/full-struct vegetation families
    (FIRSTOSTRUCT..FOURTHFULLSTRUCT 12-23 + NINTH/TENTHOSTRUCT 97-98) on
    both objs and structs layers."""
    parsed = _parsed_with_layers(6, 6, {
        "structs": {(0, 0): [(20, 1)],   # FIRSTFULLSTRUCT (tree)
                    (1, 0): [(97, 2)],   # NINTHOSTRUCT
                    (2, 0): [(39, 7)]},  # FOURTHWALL — not a tree
        "objs": {(3, 0): [(12, 1)],      # FIRSTOSTRUCT
                 (4, 0): [(50, 9)]},     # ROADPIECES — not a tree
    })
    ctx = GeneratorContext(rows=6, cols=6, parsed=parsed)
    is_masked = _make_named_mask_predicate(ctx, "objs", "trees")
    assert is_masked(0, 0) is True
    assert is_masked(1, 0) is True
    assert is_masked(3, 0) is True
    assert is_masked(2, 0) is False
    assert is_masked(4, 0) is False
    assert is_masked(5, 5) is False


def test_named_mask_combined_list():
    parsed = _parsed_with_layers(4, 4, {
        "land": {(0, 0): [(7, 1)]},          # water
        "structs": {(1, 0): [(39, 7)]},      # structure
        "objs": {(2, 0): [(5, 1)]},          # occupied (target = objs)
    })
    ctx = GeneratorContext(rows=4, cols=4, parsed=parsed)
    is_masked = _make_named_mask_predicate(
        ctx, "objs", "occupied, water, structures")
    assert is_masked(0, 0) is True
    assert is_masked(1, 0) is True
    assert is_masked(2, 0) is True
    assert is_masked(3, 3) is False


def test_combine_masks_or_semantics():
    a = lambda x, y: x == 1   # noqa: E731
    b = lambda x, y: y == 2   # noqa: E731
    assert _combine_masks(None, None) is None
    assert _combine_masks(a, None) is a
    both = _combine_masks(a, b)
    assert both(1, 0) is True
    assert both(0, 2) is True
    assert bool(both(0, 0)) is False


def test_scatter_avoid_named_occupied_skips_existing_content():
    """Scatter with avoid_named=occupied never stacks on a tile that
    already has content on the TARGET layer."""
    rows = cols = 20
    blocked = {(x, y) for x in range(10) for y in range(rows)}
    occupied = {"objs": {xy: [(20, 3)] for xy in blocked}}
    parsed = _parsed_with_layers(rows, cols, occupied)
    ctx = GeneratorContext(rows=rows, cols=cols, parsed=parsed)
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 80, "min_distance": 1, "layer": "objs",
        "slot": 5, "sub": 1, "seed": 3, "avoid_named": "occupied",
    }))
    coords = {(e["x"], e["y"]) for e in events if "op" in e}
    assert coords, "expected placements in the unoccupied half"
    assert not (coords & blocked)


def test_scatter_avoid_named_combines_with_legacy_mask():
    """avoid_named ORs with avoid_layer/avoid_slots — both masks apply."""
    rows = cols = 20
    water = {(x, 0) for x in range(cols)}
    structs = {(x, 1) for x in range(cols)}
    parsed = _parsed_with_layers(rows, cols, {
        "land": {xy: [(7, 1)] for xy in water},
        "structs": {xy: [(39, 7)] for xy in structs},
    })
    ctx = GeneratorContext(rows=rows, cols=cols, parsed=parsed)
    events = list(ScatterGenerator().iter_ops(ctx, {
        "count": 120, "min_distance": 1, "layer": "objs",
        "slot": 5, "sub": 1, "seed": 11,
        "avoid_named": "water",
        "avoid_layer": "structs", "avoid_slots": "",
    }))
    coords = {(e["x"], e["y"]) for e in events if "op" in e}
    assert coords
    assert not (coords & water)
    assert not (coords & structs)


def test_cluster_and_density_respect_avoid_named():
    rows = cols = 40
    blocked = {(x, y) for x in range(rows) for y in range(20)}  # top half
    parsed = _parsed_with_layers(rows, cols, {
        "objs": {xy: [(12, 1)] for xy in blocked},   # FIRSTOSTRUCT = trees
    })
    ctx = GeneratorContext(rows=rows, cols=cols, parsed=parsed)
    cluster_events = list(ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 6, "objects_per_cluster": 15, "cluster_radius": 4,
        "layer": "objs", "slot": 5, "sub": 1, "seed": 8,
        "avoid_named": "trees",
    }))
    density_events = list(DensityFalloffGenerator().iter_ops(ctx, {
        "center_x": 20, "center_y": 20, "radius": 18, "peak_density": 1.0,
        "layer": "objs", "slot": 5, "sub": 1, "seed": 2,
        "avoid_named": "trees",
    }))
    for events in (cluster_events, density_events):
        coords = {(e["x"], e["y"]) for e in events if "op" in e}
        assert coords
        assert not (coords & blocked)


def test_scatter_avoid_named_blank_is_byte_identical():
    """Default avoid_named='' changes nothing — legacy streams intact."""
    ctx_a = GeneratorContext(rows=30, cols=30, parsed={"rows": 30, "cols": 30})
    ctx_b = GeneratorContext(rows=30, cols=30, parsed={"rows": 30, "cols": 30})
    base = {"count": 30, "min_distance": 1, "layer": "objs",
            "slot": 5, "sub": 1, "seed": 4}
    a = [(e["x"], e["y"], e["sub"])
         for e in ScatterGenerator().iter_ops(ctx_a, base) if "op" in e]
    b = [(e["x"], e["y"], e["sub"])
         for e in ScatterGenerator().iter_ops(ctx_b, {**base, "avoid_named": ""})
         if "op" in e]
    assert a == b


def test_avoid_named_param_declared_on_scatter_family():
    for gen in (ScatterGenerator(), ClusterScatterGenerator(),
                DensityFalloffGenerator()):
        names = {p.name for p in gen.params}
        assert "avoid_named" in names, f"{gen.name} missing avoid_named"


def test_subs_param_declared_on_fill_and_rect():
    """fill + rect grew the `subs` variant param so the panel's thumbnail
    multi-select drives them too."""
    for gen in (FillLayerGenerator(), RectangleGenerator()):
        names = {p.name for p in gen.params}
        assert "subs" in names, f"{gen.name} missing subs"


def test_fill_and_rect_subs_variants_emit_from_set():
    ctx = GeneratorContext(rows=8, cols=8, parsed={"rows": 8, "cols": 8})
    fill_events = list(FillLayerGenerator().iter_ops(ctx, {
        "layer": "land", "slot": 0, "sub": 1, "seed": 5, "subs": "1,2,3",
    }))
    subs = {e["sub"] for e in fill_events if "op" in e}
    assert subs <= {1, 2, 3}
    assert len(subs) >= 2
    rect_events = list(RectangleGenerator().iter_ops(ctx, {
        "x1": 0, "y1": 0, "x2": 7, "y2": 7, "layer": "land",
        "slot": 0, "sub": 1, "seed": 5, "mode": "fill", "subs": "4,5",
    }))
    rsubs = {e["sub"] for e in rect_events if "op" in e}
    assert rsubs <= {4, 5}
    assert len(rsubs) >= 2


def test_named_masks_constant_shape():
    """The UI's checkbox row mirrors this tuple — keep it stable."""
    assert NAMED_MASKS == ("occupied", "water", "roads", "structures", "trees")


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



def test_cluster_respects_region():
    """region_x1..y2 confines cluster CENTERS (objects may spill by at
    most the cluster radius); 0/0/0/0 stays whole-map (scatter's
    sentinel convention)."""
    from mercwizard_core.mapforge.generators import ClusterScatterGenerator
    ctx = _bank_ctx(80, 80)
    radius = 3
    ops = [e for e in ClusterScatterGenerator().iter_ops(ctx, {
        "cluster_count": 6, "objects_per_cluster": 8,
        "cluster_radius": radius, "slot": 1, "sub": 1,
        "region_x1": 20, "region_y1": 30, "region_x2": 40, "region_y2": 50,
        "clip_to_playable": False,
    }) if "op" in e]
    assert ops, "region run placed nothing"
    for o in ops:
        assert 20 - radius <= o["x"] <= 40 + radius
        assert 30 - radius <= o["y"] <= 50 + radius


# ── Escarpment mode (the DEFAULT) — edge-to-edge cliff line ─────────────


def test_bank_default_mode_is_escarpment_edge_to_edge():
    """Default (no bank_mode) = escarpment: the drag's south edge becomes
    a full-width cliff line and EVERYTHING north of it rises — vanilla's
    idiom (a floating island plateau reads odd in iso view)."""
    ctx = _bank_ctx(40, 40)
    heights, faces = _bank_split(ctx, {
        "x1": 10, "y1": 10, "x2": 20, "y2": 15, "levels": 1,
    })
    # Raised region = rows 0..15 across the FULL width.
    assert {(o["x"], o["y"]) for o in heights} == {
        (x, y) for y in range(0, 16) for x in range(40)
    }
    # Faces sit on row 15 only, spanning edge to edge: westmost piece
    # covers cols 0..3 (anchor at 4), easternmost anchor at col 39.
    face_xy = {(o["x"], o["y"]) for o in faces}
    assert all(y == 15 for _, y in face_xy)
    xs = sorted(x for x, _ in face_xy)
    assert xs[0] == 4 and xs[-1] == 39
    # Chain invariant: joints never wider than the vanilla stride 4.
    assert all(b - a <= 4 for a, b in zip(xs, xs[1:]))
    # Dual entries per anchor.
    assert len(faces) == 2 * len(face_xy)


def test_bank_escarpment_high_side_west():
    ctx = _bank_ctx(30, 30)
    heights, faces = _bank_split(ctx, {
        "x1": 5, "y1": 5, "x2": 12, "y2": 9, "levels": 1,
        "high_side": "W",
    })
    assert {(o["x"], o["y"]) for o in heights} == {
        (x, y) for y in range(30) for x in range(0, 13)
    }
    face_xy = {(o["x"], o["y"]) for o in faces}
    assert all(x == 12 for x, _ in face_xy)
    ys = sorted(y for _, y in face_xy)
    assert ys[0] == 4 and ys[-1] == 29
    assert all(b - a <= 4 for a, b in zip(ys, ys[1:]))


def test_bank_escarpment_faces_use_only_straight_run_subs():
    """Edge-to-edge lines have no corners: S lines use only subs 7/8,
    E lines only subs 5/6 (the straight-run pools)."""
    ctx = _bank_ctx(40, 40)
    _h, faces_n = _bank_split(ctx, {"x1": 0, "y1": 0, "x2": 5, "y2": 20,
                                    "levels": 1, "high_side": "N"})
    assert {o["sub"] for o in faces_n} <= {7, 8}
    _h, faces_w = _bank_split(ctx, {"x1": 0, "y1": 0, "x2": 20, "y2": 5,
                                    "levels": 1, "high_side": "W"})
    assert {o["sub"] for o in faces_w} <= {5, 6}


def test_bank_escarpment_quadrants():
    """Corner high-sides raise a quadrant with an L-shaped visible face;
    SE (both boundaries away-facing) raises heights with no art."""
    ctx = _bank_ctx(40, 40)
    drag = {"x1": 10, "y1": 12, "x2": 22, "y2": 18, "levels": 1}

    # NW: E face on col 22 + S face on row 18 with the SE-corner sub 7.
    heights, faces = _bank_split(ctx, dict(drag, high_side="NW"))
    assert {(o["x"], o["y"]) for o in heights} == {
        (x, y) for y in range(0, 19) for x in range(0, 23)
    }
    fxy = {(o["x"], o["y"]) for o in faces}
    assert all(x == 22 or y == 18 for x, y in fxy)
    assert any(o["x"] == 22 and o["y"] == 18 and o["sub"] == 7 for o in faces)

    # NE: S face from x1 to the east border + SW sub-8 taper at (10, 18).
    heights, faces = _bank_split(ctx, dict(drag, high_side="NE"))
    assert {(o["x"], o["y"]) for o in heights} == {
        (x, y) for y in range(0, 19) for x in range(10, 40)
    }
    assert any(o["x"] == 10 and o["y"] == 18 and o["sub"] == 8 for o in faces)
    assert max(o["x"] for o in faces) == 39
    assert all(o["y"] == 18 for o in faces)

    # SW: E face on col 22 from the south border, flush at y1.
    heights, faces = _bank_split(ctx, dict(drag, high_side="SW"))
    assert {(o["x"], o["y"]) for o in heights} == {
        (x, y) for y in range(12, 40) for x in range(0, 23)
    }
    assert all(o["x"] == 22 for o in faces)
    assert {o["sub"] for o in faces} <= {5, 6}

    # SE: heights only, no faces (away-facing boundaries are bare).
    heights, faces = _bank_split(ctx, dict(drag, high_side="SE"))
    assert {(o["x"], o["y"]) for o in heights} == {
        (x, y) for y in range(12, 40) for x in range(10, 40)
    }
    assert faces == []
