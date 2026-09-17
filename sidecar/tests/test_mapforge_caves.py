"""Cave socket LUT + CaveSmoothGenerator tests.

Pure-function checks on socket_lut's cave autotiler (verbatim from
newsmooth.cpp) plus an integration check that the generator only runs on cave
tilesets and re-tiles wrong cave walls to a legal perimeter piece.
"""
import random

from mercwizard_core.mapforge import socket_lut as S
from mercwizard_core.mapforge.generators import (
    REGISTRY, CaveSmoothGenerator, GeneratorContext, ALL_LAYERS,
)


def _cave_grid(rows, cols, cave_cells, sub=0):
    """parsed dict (tileset 1) with cave walls (type 36, given sub) at cave_cells."""
    n = rows * cols
    parsed = {"rows": rows, "cols": cols, "tileset": 1}
    for layer in ALL_LAYERS:
        parsed[layer] = [[] for _ in range(n)]
    for (x, y) in cave_cells:
        parsed["structs"][y * cols + x] = [(S.FIRSTWALL, sub)]
    return parsed


def _ops(gen, ctx, params):
    return [e for e in gen.iter_ops(ctx, params) if "op" in e]


# ── pure LUT ──────────────────────────────────────────────────────────────
def test_cave_lut_covers_all_256_perimeters():
    assert all(v in S.CAVE_PERIM_LUT for v in range(256))


def test_cave_perimeter_bitmask_matches_engine_weights():
    # 3x3 with only the centre's 4 cardinal neighbours as caves -> N|E|S|W = 0x0f.
    cols = rows = 3
    cells = [(1, 0), (2, 1), (1, 2), (0, 1)]      # N, E, S, W of centre (1,1)
    p = _cave_grid(rows, cols, cells)
    assert S.cave_perimeter(p["structs"], 1 * cols + 1, cols, rows) == 0x0f


def test_fully_surrounded_is_interior_floor():
    wtype, sub = S.pick_cave_sub(0xff, random.Random(1))
    assert wtype == S.FIRSTWALL and 60 <= sub <= 65


def test_no_cardinal_neighbour_is_stalagmite():
    wtype, sub = S.pick_cave_sub(0x00, random.Random(1))
    assert wtype is None and sub is None
    # a pure-diagonal perimeter is also stalagmite (low nibble 0)
    assert S.pick_cave_sub(0xf0, random.Random(1)) == (None, None)


def test_single_cardinal_uses_secondwall():
    wtype, sub = S.pick_cave_sub(0x01, random.Random(1))     # only N
    assert wtype == S.SECONDWALL and 1 <= sub <= 4


def test_legal_check_round_trips_for_every_perimeter():
    rng = random.Random(0)
    for perim in range(256):
        wtype, sub = S.pick_cave_sub(perim, rng)
        if wtype is None:
            continue
        assert S.cave_sub_is_legal(sub, perim)


# ── generator ─────────────────────────────────────────────────────────────
def test_registry_has_smooth_caves():
    assert "smooth_caves" in REGISTRY
    assert isinstance(REGISTRY["smooth_caves"], CaveSmoothGenerator)


def test_cave_generator_skips_non_cave_tileset():
    p = _cave_grid(3, 3, [(1, 1)])
    p["tileset"] = 5                                   # not a cave tileset
    ctx = GeneratorContext(rows=3, cols=3, parsed=p)
    assert _ops(CaveSmoothGenerator(), ctx, {}) == []


def test_cave_generator_retiles_wrong_centre_to_interior_floor():
    # 3x3 solid cave block: centre (1,1) is fully surrounded -> must become 60-65.
    cells = [(x, y) for y in range(3) for x in range(3)]
    p = _cave_grid(3, 3, cells, sub=0)                 # sub 0 is illegal everywhere
    ctx = GeneratorContext(rows=3, cols=3, parsed=p)
    ops = _ops(CaveSmoothGenerator(), ctx, {})
    centre = next(o for o in ops if o["x"] == 1 and o["y"] == 1)
    assert centre["layer"] == "structs" and centre["slot"] == S.FIRSTWALL
    assert 60 <= centre["sub"] <= 65


def test_cave_generator_leaves_legal_walls_untouched():
    # place the engine-correct piece at the centre; generator should not touch it.
    cells = [(x, y) for y in range(3) for x in range(3)]
    p = _cave_grid(3, 3, cells, sub=0)
    correct_sub = S.pick_cave_sub(0xff, random.Random(0))[1]
    p["structs"][1 * 3 + 1] = [(S.FIRSTWALL, correct_sub)]
    ctx = GeneratorContext(rows=3, cols=3, parsed=p)
    ops = _ops(CaveSmoothGenerator(), ctx, {})
    assert not any(o["x"] == 1 and o["y"] == 1 for o in ops)
