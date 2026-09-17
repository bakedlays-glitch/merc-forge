"""MapForge's half of the wall/fence corner checker.

The geometry is the same rule set as `Headless_Compiler/sector_gen/
validators.py::check_corner_art` (duplicated there/here for the same reason
`socket_lut.py` duplicates `engine_socket_rules.py`), so these tests exist to
prove three MapForge-specific properties, not to re-derive the geometry:

  1. `validate_parsed` is unchanged unless a caller opts in with `slot_art`;
  2. the finding shape matches the rest of the module (gridnos in `tiles`,
     (x,y) in the message, severity `error` only for the verified art);
  3. `validate_generated_edits_before_commit` blocks ONLY defects the
     generated edits introduced — a user map's pre-existing broken corners
     never gate a generator run, and no save restriction is added.

The regression fixtures are the excised Junktown v112 regions that live with
the Headless_Compiler suite; this module reads them rather than keeping a
second copy.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mercwizard_core.mapforge_engine.validate import (
    validate_parsed, corner_findings, validate_generated_edits_before_commit,
    SEVERITY_ERROR, SEVERITY_WARN, SEVERITY_INFO,
)

FIX = (Path(__file__).resolve().parents[3]
       / "Headless_Compiler" / "sector_gen" / "tests" / "fixtures" / "corners")
pytestmark = pytest.mark.skipif(
    not FIX.is_dir(), reason=f"corner fixtures not present at {FIX}")


def region(name):
    d = json.loads((FIX / name).read_text())
    structs = [[] for _ in range(d["cols"] * d["rows"])]
    for gn, entries in d["structs"].items():
        structs[int(gn)] = [tuple(e) for e in entries]
    return {"cols": d["cols"], "rows": d["rows"], "tileset": d["tileset"],
            "structs": structs}


def slot_art(name):
    return json.loads((FIX / name).read_text())["slot_art"]


def codes(findings):
    return {f.code for f in findings}


def gridnos(findings, code):
    out = set()
    for f in findings:
        if f.code == code:
            out.update(f.tiles)
    return out


# ---------------------------------------------------------------------------
# 1. opt-in only
# ---------------------------------------------------------------------------

def test_validate_parsed_is_unchanged_without_slot_art():
    """No caller that does not ask for it sees a single new finding."""
    r = region("wirefenc_enclosure_broken.json")
    assert not any(c.startswith("CORNER_") for c in codes(validate_parsed(r)))


def test_validate_parsed_reports_corners_when_slot_art_is_supplied():
    r = region("wirefenc_enclosure_broken.json")
    f = validate_parsed(r, slot_art("slot_art_t71.json"))
    assert "CORNER_DANGLING" in codes(f)
    assert "CORNER_ORIENTATION" in codes(f)


def test_validate_parsed_never_calls_a_corner_defect_an_error():
    """`error` in this module means "won't load / will crash". A fence that
    stops does neither, and authored maps stop fences deliberately — so
    reporting is advisory while the commit gate still blocks."""
    art = slot_art("slot_art_t71.json")
    broken = region("wirefenc_enclosure_broken.json")
    corners = [f for f in validate_parsed(broken, art) if f.code.startswith("CORNER_")]
    assert corners
    assert all(f.severity != SEVERITY_ERROR for f in corners)
    # the same defect still gates a generated edit that introduces it
    assert validate_generated_edits_before_commit(
        region("wirefenc_enclosure_fixed.json"), broken, art)


def test_an_explicit_none_reports_the_coverage_gap_rather_than_passing():
    r = region("wirefenc_enclosure_broken.json")
    f = [x for x in validate_parsed(r, None) if x.code.startswith("CORNER_")]
    assert [x.code for x in f] == ["CORNER_UNSUPPORTED"]
    assert f[0].severity == SEVERITY_INFO


# ---------------------------------------------------------------------------
# 2. the Junktown regression pair + finding shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stem,slot", [("wirefenc_enclosure", 86),
                                       ("oldfence_enclosure", 104),
                                       ("rustwall_rect", 88)])
def test_broken_fails_and_repaired_passes(stem, slot):
    art = slot_art("slot_art_t71.json")
    broken = corner_findings(region(f"{stem}_broken.json"), art)
    fixed = corner_findings(region(f"{stem}_fixed.json"), art)
    assert [f for f in broken if f.severity == SEVERITY_ERROR and f.slot == slot]
    assert [f for f in fixed if f.severity == SEVERITY_ERROR and f.slot == slot] == []


def test_finding_shape_matches_the_module_convention():
    r = region("wirefenc_enclosure_broken.json")
    f = next(x for x in corner_findings(r, slot_art("slot_art_t71.json"))
             if x.code == "CORNER_DANGLING")
    assert f.slot == 86 and f.count == len(f.tiles)
    assert all(isinstance(g, int) and 0 <= g < r["cols"] * r["rows"] for g in f.tiles)
    x, y = f.tiles[0] % r["cols"], f.tiles[0] // r["cols"]
    assert f"({x},{y})" in f.message


def test_only_the_verified_art_reaches_error_severity():
    r = region("wirefenc_enclosure_broken.json")
    art = dict(slot_art("slot_art_t71.json"), **{"86": "some_other_fence.sti"})
    f = corner_findings(r, art)
    assert [x for x in f if x.slot == 86 and x.severity == SEVERITY_ERROR] == []
    # unknown art on a perimeter slot is a reported coverage gap, not silence
    assert [x.code for x in f if x.slot == 86] == ["CORNER_UNSUPPORTED"]
    # the same geometry at advisory severity when the art is merely unverified
    loose = corner_findings(r, slot_art("slot_art_t71.json"),
                            hard_art=frozenset())
    assert [x for x in loose if x.severity == SEVERITY_WARN
            and x.code == "CORNER_DANGLING"]


def test_the_same_numeric_slot_with_different_art_infers_no_family():
    """Slot 88 is `build_24a_rust.sti` on tileset 71 and `truck.sti` on
    tileset 0 — the identical grid must not be validated as a wall sheet."""
    r = region("rustwall_rect_broken.json")
    assert [f for f in corner_findings(r, slot_art("slot_art_t71.json"))
            if f.slot == 88 and f.severity == SEVERITY_ERROR]
    assert [f for f in corner_findings(r, slot_art("slot_art_t0.json"))
            if f.slot == 88] == []


# ---------------------------------------------------------------------------
# 3. the generation boundary: block what the edits introduced, nothing else
# ---------------------------------------------------------------------------

def test_a_pre_existing_defect_never_blocks_a_generated_edit():
    """The user's own broken corners are the baseline. Running a generator over
    a map that already has them must not be refused."""
    broken = region("wirefenc_enclosure_broken.json")
    assert validate_generated_edits_before_commit(
        broken, copy.deepcopy(broken), slot_art("slot_art_t71.json")) == []


def test_a_newly_broken_corner_blocks_with_exact_gridnos():
    art = slot_art("slot_art_t71.json")
    before = region("wirefenc_enclosure_fixed.json")
    after = copy.deepcopy(before)
    gn = next(g for g, cell in enumerate(after["structs"])
              if any(e[0] == 86 and e[1] in (1, 2, 3, 4) for e in cell))
    after["structs"][gn] = [(86, 11)]        # a turn flattened to a straight run
    blocking = validate_generated_edits_before_commit(before, after, art)
    assert blocking, "a generation-introduced broken turn must gate the commit"
    assert all(f.severity == SEVERITY_ERROR for f in blocking)
    assert gn in gridnos(blocking, "CORNER_DANGLING") | gridnos(blocking, "CORNER_ORIENTATION")


def test_repairing_a_defect_does_not_block():
    art = slot_art("slot_art_t71.json")
    assert validate_generated_edits_before_commit(
        region("wirefenc_enclosure_broken.json"),
        region("wirefenc_enclosure_fixed.json"), art) == []


def test_the_boundary_gate_is_silent_without_art_identity():
    """`slot_art=None` must never block a commit on a guess — the coverage gap
    is advisory and reported by `validate_parsed`, not used to gate."""
    before = region("wirefenc_enclosure_fixed.json")
    after = copy.deepcopy(before)
    after["structs"][after["structs"].index(
        next(c for c in after["structs"] if any(e[0] == 86 for e in c)))] = []
    assert validate_generated_edits_before_commit(before, after, None) == []


def test_scope_limits_the_gate_to_the_tiles_the_generator_touched():
    art = slot_art("slot_art_t71.json")
    before = region("wirefenc_enclosure_fixed.json")
    after = copy.deepcopy(before)
    gn = next(g for g, cell in enumerate(after["structs"])
              if any(e[0] == 86 and e[1] in (1, 2, 3, 4) for e in cell))
    after["structs"][gn] = [(86, 11)]
    assert validate_generated_edits_before_commit(before, after, art)
    far = [g for g in range(len(after["structs"])) if abs(g - gn) > 500]
    assert validate_generated_edits_before_commit(before, after, art, scope=far) == []


def test_allow_openings_clears_a_deliberate_gate():
    art = slot_art("slot_art_t71.json")
    before = region("wirefenc_enclosure_fixed.json")
    after = copy.deepcopy(before)
    gn = next(g for g, cell in enumerate(after["structs"])
              if any(e[0] == 86 and e[1] == 11 for e in cell))
    after["structs"][gn] = []                # punch a one-tile hole in a run
    assert validate_generated_edits_before_commit(before, after, art,
                                                  allow_openings=[gn]) == []
