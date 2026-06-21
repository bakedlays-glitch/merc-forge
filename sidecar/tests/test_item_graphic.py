# sidecar/tests/test_item_graphic.py
import os, pytest
from mercwizard_core.mapforge_engine.item_graphic import (
    render_item_graphic, _bigitems_stem,
)
import mercwizard_core.mapforge_engine.item_graphic as igph

_INSTALL = r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"

def test_bigitems_stem_padding():
    assert _bigitems_stem(0, 24) == "gun24"
    assert _bigitems_stem(0, 9) == "gun09"
    assert _bigitems_stem(1, 96) == "p1item96"
    assert _bigitems_stem(1, 5) == "p1item05"
    assert _bigitems_stem(2, 13) == "p2item13"

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_renders_famas_png():
    png = render_item_graphic(_INSTALL, 24)   # FAMAS -> gun24.sti
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 100

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_renders_leather_jacket_png():
    png = render_item_graphic(_INSTALL, 188)  # Leather Jacket -> p1item96.sti
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_unknown_item_returns_none():
    assert render_item_graphic(_INSTALL, 65000) is None   # not in Items.xml


# ── Task 5 additions: render_bigitem_by_ref + list_bigitem_graphics ──────────

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_render_by_ref_matches_item_render():
    # FAMAS = item 24 -> graphic (type=0, num=24) = gun24.sti
    by_item = igph.render_item_graphic(_INSTALL, 24)
    by_ref = igph.render_bigitem_by_ref(_INSTALL, 0, 24)
    assert by_item is not None and by_ref is not None
    assert by_item == by_ref


@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_list_bigitem_graphics_nonempty():
    cat = igph.list_bigitem_graphics(_INSTALL)
    assert isinstance(cat, list) and len(cat) > 0
    assert all({"type", "num", "stem"} <= set(e) for e in cat)
    # Verify sorting by (type, num)
    keys = [(e["type"], e["num"]) for e in cat]
    assert keys == sorted(keys)
