"""MapForge generator corpus — loader resolution + corpus-driven generator
wiring (variant-fill on scatter/cluster/density/fill/rect, BuildingStamp).

The loader tests run against a SYNTHETIC corpus injected via monkeypatch so
they're deterministic and independent of the shipped generator_corpus.json.
One integration test exercises the real shipped artifact.
"""
from __future__ import annotations

import json

import pytest

import mercwizard_core.mapforge.corpus as gc
from mercwizard_core.mapforge import generators as G
from mercwizard_core.mapforge.generators import GeneratorContext, _make_playable_predicate


SYNTH = {
    "schema_version": 1,
    "sources": ["stock", "redux", "combined"],
    "biomes": ["urban", "desert"],
    "layers": ["land", "objs", "structs", "shadows", "roofs", "onroofs"],
    "source_installs": {"stock": "S", "redux": "R"},
    "scatter": {
        # sub 0 present to prove it gets dropped from the spec
        "stock": {"urban": {"structs": {"12": {"1": 5, "2": 3, "0": 9}}}},
        "redux": {"desert": {"land": {"0": {"1": 2, "4": 6}}}},
        "combined": {
            "urban": {"structs": {"12": {"1": 5, "2": 3}}},
            "desert": {"land": {"0": {"1": 2, "4": 6}}},
        },
    },
    "buildings": {
        "stock": {"urban": {
            "positions": {
                "N": {"structs": {"36": {"10": 7}}, "roofs": {"66": {"2": 4}}},
                "SE": {"structs": {"36": {"13": 9}}, "roofs": {"66": {"6": 3}}},
                "Interior": {"structs": {}, "roofs": {"66": {"11": 20}}},
            },
            "doors": {"by_slot": {"40": {"6": 5, "1": 2}}, "by_edge": {"S": 4, "E": 1}},
            "size_w": {"7": 3}, "size_h": {"6": 2}, "n_buildings": 5,
        }},
        "redux": {},
        "combined": {},
    },
}


@pytest.fixture
def synth_corpus(monkeypatch, tmp_path):
    p = tmp_path / "generator_corpus.json"
    p.write_text(json.dumps(SYNTH), encoding="utf-8")
    cov = tmp_path / "coverage.json"
    cov.write_text(json.dumps({
        "stock": {"urban": {"n_maps": 5, "n_buildings": 5,
                            "layers": ["structs"], "has_buildings": True}},
    }), encoding="utf-8")
    monkeypatch.setattr(gc, "_CORPUS_PATH", p)
    monkeypatch.setattr(gc, "_COVERAGE_PATH", cov)
    gc._data.cache_clear()
    gc._coverage.cache_clear()
    yield
    gc._data.cache_clear()
    gc._coverage.cache_clear()


# ── loader ───────────────────────────────────────────────────────────────

def test_resolve_subs_formats_spec_and_drops_sub_zero(synth_corpus):
    spec = gc.resolve_subs("stock", "urban", "structs", 12)
    got = dict(tok.split(":") for tok in spec.split(","))
    assert got == {"1": "5", "2": "3"}  # sub 0 dropped (renders nothing)


def test_resolve_subs_falls_back_to_combined(synth_corpus):
    spec = gc.resolve_subs("stock", "desert", "land", 0)  # stock lacks it
    parsed = dict(tok.split(":") for tok in spec.split(","))
    assert parsed == {"1": "2", "4": "6"}


def test_resolve_subs_empty_when_absent(synth_corpus):
    assert gc.resolve_subs("stock", "urban", "structs", 999) == ""
    assert gc.resolve_subs("stock", "swamp", "structs", 12) == ""


def test_building_dominant_slot_and_position_subs(synth_corpus):
    t = gc.get_building_table("stock", "urban")
    assert t is not None
    assert gc.building_dominant_slot(t, 36, 39) == 36
    assert gc.building_dominant_slot(t, 64, 67, kind="roofs") == 66
    assert gc.building_position_subs(t, "SE", 36) == "13:9"
    assert gc.building_position_subs(t, "Interior", 66, kind="roofs") == "11:20"


def test_building_table_missing_returns_none(synth_corpus):
    # redux building cell empty AND combined empty → None
    assert gc.get_building_table("redux", "urban") is None


def test_available_and_lists(synth_corpus):
    assert gc.available() is True
    assert gc.list_sources() == ["stock", "redux", "combined"]
    assert "desert" in gc.list_biomes()
    assert gc.coverage("stock", "urban")["n_maps"] == 5


# ── generator wiring ───────────────────────────────────────────────────────

def _ctx(n=24):
    return GeneratorContext(rows=n, cols=n, parsed={})


def test_scatter_corpus_fills_subs_from_distribution(synth_corpus):
    ops = list(G.REGISTRY["scatter"].iter_ops(_ctx(), {
        "count": 50, "min_distance": 1, "layer": "structs", "slot": 12,
        "corpus_source": "stock", "biome": "urban", "seed": 1,
    }))
    subs = {o["sub"] for o in ops if "sub" in o}
    assert subs and subs <= {1, 2}  # corpus subs only; sub 0 never emitted


def test_explicit_subs_override_corpus(synth_corpus):
    ops = list(G.REGISTRY["scatter"].iter_ops(_ctx(), {
        "count": 30, "min_distance": 1, "layer": "structs", "slot": 12,
        "subs": "7", "corpus_source": "stock", "biome": "urban", "seed": 1,
    }))
    assert {o["sub"] for o in ops if "sub" in o} == {7}


def test_scatter_without_corpus_is_single_sub(synth_corpus):
    ops = list(G.REGISTRY["scatter"].iter_ops(_ctx(), {
        "count": 20, "min_distance": 1, "layer": "structs", "slot": 12,
        "sub": 9, "seed": 1,
    }))
    assert {o["sub"] for o in ops if "sub" in o} == {9}


def test_fill_corpus_varies_ground_and_overrides_default_sub(synth_corpus):
    ops = list(G.REGISTRY["fill"].iter_ops(GeneratorContext(rows=10, cols=10, parsed={}), {
        "layer": "land", "slot": 0, "sub": 9,
        "corpus_source": "redux", "biome": "desert", "seed": 3,
    }))
    subs = {o["sub"] for o in ops if "sub" in o}
    assert subs and subs <= {1, 4} and 9 not in subs  # corpus used, default ignored


def test_all_five_generators_expose_corpus_params():
    for name in ("scatter", "cluster", "density-falloff", "fill", "rect"):
        names = {p.name for p in G.REGISTRY[name].params}
        assert {"corpus_source", "biome"} <= names, name


def test_building_stamp_places_walls_roofs_door_room(synth_corpus):
    ops = list(G.REGISTRY["building"].iter_ops(GeneratorContext(rows=160, cols=160, parsed={}), {
        "x": 74, "y": 74, "width": 6, "height": 6,
        "corpus_source": "stock", "biome": "urban", "seed": 2,
    }))
    edits = [o for o in ops if "op" in o]
    layers = {o.get("layer") for o in edits}
    assert "structs" in layers and "roofs" in layers
    assert any(o.get("op") == "set_room" for o in edits)
    assert {o["slot"] for o in edits if o.get("layer") == "structs"} <= {36, 40}


def test_building_stamp_frame_count_clamps_out_of_range_subs(synth_corpus):
    ctx = GeneratorContext(rows=160, cols=160, parsed={}, frame_count=lambda slot: 5)
    ops = list(G.REGISTRY["building"].iter_ops(ctx, {
        "x": 74, "y": 74, "width": 6, "height": 6,
        "corpus_source": "stock", "biome": "urban", "seed": 2,
    }))
    subs = [o["sub"] for o in ops if o.get("layer") in ("structs", "roofs")]
    assert subs and max(subs) <= 5  # corpus subs 7/10/11/13 clamped out


def test_building_stamp_missing_biome_errors_cleanly(synth_corpus):
    ops = list(G.REGISTRY["building"].iter_ops(GeneratorContext(rows=30, cols=30, parsed={}), {
        "x": 5, "y": 5, "corpus_source": "redux", "biome": "swamp",
    }))
    assert ops and ops[-1].get("phase") == "error"


# ── auto room id (room_id=0 sentinel) ────────────────────────────────────────

def _room_ids(ops):
    return {o["room_id"] for o in ops if o.get("op") == "set_room"}


def test_building_stamp_auto_room_assigns_max_plus_one(synth_corpus):
    """room_id=0 = AUTO: the backend assigns max(existing rooms) + 1."""
    rooms = [0] * (30 * 30)
    rooms[123] = 7   # highest existing room id in the sector
    rooms[200] = 3
    ctx = GeneratorContext(rows=30, cols=30, parsed={"rooms": rooms})
    ops = list(G.REGISTRY["building"].iter_ops(ctx, {
        "x": 5, "y": 5, "width": 6, "height": 6,
        "corpus_source": "stock", "biome": "urban", "room_id": 0, "seed": 2,
        "clip_to_playable": False,
    }))
    assert _room_ids(ops) == {8}


def test_building_stamp_auto_room_is_the_default(synth_corpus):
    """No room_id param at all → AUTO; empty/missing rooms grid starts at 1."""
    assert next(p for p in G.REGISTRY["building"].params
                if p.name == "room_id").default == 0
    ops = list(G.REGISTRY["building"].iter_ops(
        GeneratorContext(rows=30, cols=30, parsed={}), {
            "x": 5, "y": 5, "width": 6, "height": 6,
            "corpus_source": "stock", "biome": "urban", "seed": 2,
            "clip_to_playable": False,
        }))
    assert _room_ids(ops) == {1}


def test_building_stamp_explicit_room_id_respected(synth_corpus):
    """A non-zero room_id is used verbatim (no auto-assignment)."""
    rooms = [0] * (30 * 30)
    rooms[0] = 9
    ctx = GeneratorContext(rows=30, cols=30, parsed={"rooms": rooms})
    ops = list(G.REGISTRY["building"].iter_ops(ctx, {
        "x": 5, "y": 5, "width": 6, "height": 6,
        "corpus_source": "stock", "biome": "urban", "room_id": 5, "seed": 2,
        "clip_to_playable": False,
    }))
    assert _room_ids(ops) == {5}


def test_building_stamp_auto_room_clamps_at_255(synth_corpus):
    """Engine room ids are a byte — AUTO never overflows past 255."""
    rooms = [0] * (30 * 30)
    rooms[0] = 255
    ctx = GeneratorContext(rows=30, cols=30, parsed={"rooms": rooms})
    ops = list(G.REGISTRY["building"].iter_ops(ctx, {
        "x": 5, "y": 5, "width": 6, "height": 6,
        "corpus_source": "stock", "biome": "urban", "room_id": 0, "seed": 2,
        "clip_to_playable": False,
    }))
    assert _room_ids(ops) == {255}


# ── /buildings catalog endpoint ──────────────────────────────────────────────

def test_buildings_catalog_lists_cells_with_building_data(synth_corpus):
    from routes.mapforge import list_buildings
    entries = list_buildings()
    # Only stock:urban has a non-empty positions table in SYNTH.
    assert [e.id for e in entries] == ["stock:urban"]
    e = entries[0]
    assert e.label == "Urban — Stock corpus"
    assert e.corpus_source == "stock" and e.biome == "urban"
    assert e.wall_slot == 36 and e.roof_slot == 66
    # Dominant subs: wall 13 (SE weight 9 > N's 10@7), roof 11 (Interior 20).
    assert e.wall_sub == 13 and e.roof_sub == 11
    assert e.has_door is True
    assert e.n_buildings == 5
    # size_w={'7':3}, size_h={'6':2} → degenerate min=max ranges.
    assert (e.min_w, e.max_w, e.min_h, e.max_h) == (7, 7, 6, 6)
    # defaults clamp into the empirical range
    assert (e.default_w, e.default_h) == (7, 6)


def test_buildings_catalog_empty_without_corpus(synth_corpus, monkeypatch, tmp_path):
    from routes.mapforge import list_buildings
    monkeypatch.setattr(gc, "_CORPUS_PATH", tmp_path / "nope.json")
    gc._data.cache_clear()
    try:
        assert list_buildings() == []
    finally:
        gc._data.cache_clear()


# ── integration: the real shipped corpus ────────────────────────────────────

def test_shipped_corpus_available_and_resolves():
    gc._data.cache_clear()
    if not gc.available():
        pytest.skip("generator_corpus.json not present in this checkout")
    assert "stock" in gc.list_sources()
    assert "urban" in gc.list_biomes()
    spec = gc.resolve_subs("combined", "urban", "land", 0)
    assert spec == "" or ":" in spec  # well-formed weighted spec or empty


# ── playable iso-diamond clipping ───────────────────────────────────────────

def _in_engine_diamond(x, y):
    """Headless_Compiler's in_engine_diamond predicate for a 160-tile grid."""
    return x + y >= 90 and x + y <= 230 and abs(x - y) <= 70


def test_playable_predicate_matches_engine_diamond():
    ctx = GeneratorContext(rows=160, cols=160, parsed={})
    pred = _make_playable_predicate(ctx, True)
    assert all(
        pred(x, y) == _in_engine_diamond(x, y)
        for x in range(0, 160, 2) for y in range(0, 160, 2)
    )
    assert _make_playable_predicate(ctx, False) is None  # disabled = no clip


def test_all_six_generators_expose_clip_param():
    for name in ("scatter", "cluster", "density-falloff", "fill", "rect", "building"):
        assert "clip_to_playable" in {p.name for p in G.REGISTRY[name].params}, name


def test_scatter_clip_keeps_placement_inside_diamond():
    ctx = GeneratorContext(rows=160, cols=160, parsed={})
    ops = list(G.REGISTRY["scatter"].iter_ops(ctx, {
        "count": 300, "min_distance": 1, "layer": "structs", "slot": 12, "sub": 1, "seed": 7,
    }))
    pts = [(o["x"], o["y"]) for o in ops if "x" in o]
    assert pts and all(_in_engine_diamond(x, y) for x, y in pts)


def test_scatter_clip_off_can_reach_border():
    ctx = GeneratorContext(rows=160, cols=160, parsed={})
    ops = list(G.REGISTRY["scatter"].iter_ops(ctx, {
        "count": 300, "min_distance": 1, "layer": "structs", "slot": 12, "sub": 1,
        "seed": 7, "clip_to_playable": False,
    }))
    pts = [(o["x"], o["y"]) for o in ops if "x" in o]
    assert any(not _in_engine_diamond(x, y) for x, y in pts)


def test_fill_clip_skips_off_map_corners():
    ctx = GeneratorContext(rows=160, cols=160, parsed={})
    ops = list(G.REGISTRY["fill"].iter_ops(ctx, {
        "layer": "land", "slot": 0, "sub": 1, "clip_to_playable": True,
    }))
    filled = {(o["x"], o["y"]) for o in ops if "x" in o}
    assert (80, 80) in filled        # center is playable
    assert (0, 0) not in filled      # off-map grid corner skipped
    assert all(_in_engine_diamond(x, y) for x, y in filled)
