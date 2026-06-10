"""Phase 2 (A5): heights backend — `set_height` op."""
import pytest

from mercwizard_core.mapforge_engine.dat_edit_ops import set_height, EditOpError


def _mk():
    return {"rows": 2, "cols": 2,
            "heights": [0, 10, 20, 30],
            "heights_high": [5, 6, 7, 8]}


def test_set_height_sets_low_byte_returns_old():
    p = _mk()
    assert set_height(p, 1, 80) == 10
    assert p["heights"][1] == 80
    # The high byte (ubAdjacentSoldierCnt) lives in a separate array and is
    # never touched by a height edit.
    assert p["heights_high"][1] == 6


def test_set_height_rejects_out_of_range():
    p = _mk()
    with pytest.raises(EditOpError):
        set_height(p, 1, 256)
    with pytest.raises(EditOpError):
        set_height(p, 1, -1)


def test_set_height_rejects_bad_gridno():
    p = _mk()
    with pytest.raises(EditOpError):
        set_height(p, 99, 10)


def test_set_height_needs_heights_array():
    with pytest.raises(EditOpError):
        set_height({"rows": 2, "cols": 2}, 0, 10)
