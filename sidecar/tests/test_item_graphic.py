# sidecar/tests/test_item_graphic.py
import os, pytest
from mercwizard_core.mapforge_engine.item_graphic import (
    render_item_graphic, _bigitems_stem,
)

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
