"""Unit tests for the MapForge pre-flight validator (A4).

`validate_parsed` is a pure function over a parsed-sector dict, so these
tests build synthetic dicts and assert the findings — no install, no
renderer, no game launch. The dicts don't have to be physically
self-consistent; each test exercises one branch.
"""
from mercwizard_core.mapforge_engine.validate import (
    validate_parsed, SEVERITY_ERROR, SEVERITY_WARN, SEVERITY_INFO,
)

_LAYERS = ("land", "obj", "struct", "shadow", "roof", "onroof")
_PLURAL = {"land": "land", "obj": "objs", "struct": "structs",
           "shadow": "shadows", "roof": "roofs", "onroof": "onroofs"}


def _mk(world_max=4, cols=2, rows=2, *, playable=False):
    """Minimal parsed dict. `playable=True` sets the appendix presence +
    counts + tail so a clean map produces zero errors and zero warnings."""
    d = {
        "flags": 0,
        "rows": rows,
        "cols": cols,
        "appendix_sections_present": {
            "items": False, "ambient": False, "lights": False,
            "soldiers": False, "exitgrids": False, "doortable": False,
            "edgepoints": False, "schedules": False,
        },
        "n_per_tile": {k: [0] * world_max for k in _LAYERS},
        "rooms": [0] * world_max,
        "tail": None,
        "appendix_exitgrid_count": None,
        "appendix_edgepoint_count": None,
        "appendix_light_count": None,
        "appendix_parse_stopped_at": None,
    }
    for k in _LAYERS:
        d[_PLURAL[k]] = [[] for _ in range(world_max)]
    if playable:
        p = d["appendix_sections_present"]
        p["exitgrids"] = p["edgepoints"] = p["soldiers"] = p["lights"] = True
        d["appendix_exitgrid_count"] = 2
        d["appendix_edgepoint_count"] = 8
        d["appendix_light_count"] = 3
        d["tail"] = {
            "sNorthGridNo": 100, "sEastGridNo": 200,
            "sSouthGridNo": 300, "sWestGridNo": 400,
            "ubMapVersion": 21,
        }
    return d


def _codes(findings):
    return {f.code for f in findings}


def _by_code(findings, code):
    return next(f for f in findings if f.code == code)


def test_clean_playable_map_has_no_errors_or_warnings():
    findings = validate_parsed(_mk(playable=True))
    assert [f for f in findings if f.severity == SEVERITY_ERROR] == []
    assert [f for f in findings if f.severity == SEVERITY_WARN] == []


def test_room_id_gap_is_warn():
    # WARN not ERROR: real install maps (e.g. A10.DAT) ship with room gaps
    # and load fine, so a gap is a heads-up, not a certain crash.
    d = _mk()
    d["rooms"] = [1, 1, 3, 3]  # missing room 2
    f = _by_code(validate_parsed(d), "ROOM_ID_GAP")
    assert f.severity == SEVERITY_WARN
    assert f.count == 1


def test_contiguous_rooms_no_gap():
    d = _mk()
    d["rooms"] = [1, 1, 2, 2]
    assert "ROOM_ID_GAP" not in _codes(validate_parsed(d))


def test_layer_count_desync_is_error():
    d = _mk()
    d["n_per_tile"]["struct"][0] = 1  # claims 1 struct, array has 0
    f = _by_code(validate_parsed(d), "LAYER_COUNT_DESYNC")
    assert f.severity == SEVERITY_ERROR
    assert 0 in f.tiles


def test_layer_over_cap_is_error():
    d = _mk()
    d["structs"][0] = [(1, 1)] * 16   # 16 > 15 nibble cap
    d["n_per_tile"]["struct"][0] = 16  # kept in sync so it's over-cap, not desync
    codes = _codes(validate_parsed(d))
    assert "LAYER_OVER_CAP" in codes
    assert "LAYER_COUNT_DESYNC" not in codes


def test_layer_array_len_mismatch_is_error():
    d = _mk()
    d["structs"] = [[], []]  # length 2, counts length 4
    assert "LAYER_ARRAY_LEN_MISMATCH" in _codes(validate_parsed(d))


def test_no_exit_grids_warns():
    d = _mk()  # flags=0, no appendix
    f = _by_code(validate_parsed(d), "NO_EXIT_GRIDS")
    assert f.severity == SEVERITY_WARN


def test_no_edgepoints_warns():
    assert "NO_EDGEPOINTS" in _codes(validate_parsed(_mk()))


def test_exit_grid_count_zero_warns_even_when_flag_present():
    d = _mk(playable=True)
    d["appendix_exitgrid_count"] = 0
    assert "NO_EXIT_GRIDS" in _codes(validate_parsed(d))


def test_unreached_exit_grid_count_does_not_warn():
    # flag set but count None (parser stopped earlier) -> no false warning
    d = _mk(playable=True)
    d["appendix_exitgrid_count"] = None
    assert "NO_EXIT_GRIDS" not in _codes(validate_parsed(d))


def test_mapversion_too_low_is_error():
    d = _mk(playable=True)
    d["tail"]["ubMapVersion"] = 10
    f = _by_code(validate_parsed(d), "MAPVERSION_TOO_LOW")
    assert f.severity == SEVERITY_ERROR


def test_missing_edge_entry_warns():
    d = _mk(playable=True)
    d["tail"]["sNorthGridNo"] = 0
    f = _by_code(validate_parsed(d), "MISSING_EDGE_ENTRY")
    assert f.severity == SEVERITY_WARN
    assert f.count == 1


def test_high_object_count_warns():
    d = _mk()
    d["objs"][0] = [(1, 1)] * 4
    d["n_per_tile"]["obj"][0] = 4
    f = _by_code(validate_parsed(d), "HIGH_OBJECT_COUNT")
    assert f.severity == SEVERITY_WARN
    assert 0 in f.tiles


def test_parse_incomplete_is_info():
    d = _mk()
    d["appendix_parse_stopped_at"] = "soldiers_records"
    f = _by_code(validate_parsed(d), "PARSE_INCOMPLETE")
    assert f.severity == SEVERITY_INFO


def test_no_enemies_and_no_lights_are_info():
    codes = _codes(validate_parsed(_mk()))
    assert "NO_ENEMIES" in codes
    assert "NO_LIGHTS" in codes


def test_findings_ordered_error_then_warn_then_info():
    d = _mk()
    d["n_per_tile"]["struct"][0] = 1      # error: layer count desync
    d["appendix_parse_stopped_at"] = "x"  # info
    # (no exit grids / no edgepoints -> warns come for free)
    sevs = [f.severity for f in validate_parsed(d)]
    order = {SEVERITY_ERROR: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}
    ranks = [order[s] for s in sevs]
    assert ranks == sorted(ranks)
