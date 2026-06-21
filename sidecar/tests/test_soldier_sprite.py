import os
import pytest
from mercwizard_core.mapforge_engine.soldier_sprite import (
    render_standing_sprite, BODYTYPE_STANDING_STI,
)

_INSTALL = r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"


def test_bodytype_table_has_core_bodies():
    # All 12 verified body types must be present in the table.
    assert set(BODYTYPE_STANDING_STI.keys()) >= {0, 1, 2, 3, 4, 11, 12, 20, 29, 30, 31, 32}


@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_renders_regmale_standing_png():
    png = render_standing_sprite(_INSTALL, bodytype=0, direction=2)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"   # PNG signature
    assert len(png) > 100


@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_renders_dog_creature_loose_png():
    png = render_standing_sprite(_INSTALL, bodytype=29, direction=0)  # DOG = loose-only
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_unmapped_bodytype_falls_back_to_regmale():
    png = render_standing_sprite(_INSTALL, bodytype=99, direction=0)  # no mapping
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

def test_pick_subimage_direction_remap():
    from mercwizard_core.mapforge_engine.soldier_sprite import _pick_subimage
    # total=64 (8 dir x 8 frames): dir d -> 8 * ((d+1)%8)
    assert _pick_subimage(64, 0) == 8      # world-N -> sprite-dir 1
    assert _pick_subimage(64, 2) == 24
    assert _pick_subimage(64, 4) == 40
    assert _pick_subimage(64, 7) == 0      # (7+1)%8 == 0
    # total=96 (8x12): frames_per_dir=12
    assert _pick_subimage(96, 0) == 12
    assert _pick_subimage(96, 7) == 0
