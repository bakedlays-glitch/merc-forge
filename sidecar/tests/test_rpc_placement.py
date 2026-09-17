"""Tests for the InitNPCs() RPC-placement Lua manager (rpc_placement.py).

Load-bearing behaviors: the column/row transposition guard, that hand-authored
InitialProfile lines outside the managed block are never touched, idempotent /
replace-in-place upsert, remove, block/InitNPCs creation, and no-BOM output.
"""

import pytest

from mercwizard_core import rpc_placement as rp

# A GameInit.lua with a hand-authored InitNPCs() that already places two RPCs
# OUTSIDE any managed block — mirrors the live project file.
FIXTURE = """\
-- GameInit.lua
function InitNPCs()

\t-- hand-authored placements, must never be touched by the tool
\tInitialProfile( 166, 9, 1, 0, 5672 )   -- Dogmeat
\tInitialProfile( 59, 9, 1, 0, 12553 )   -- Ringo
end

function SomethingElse()
\treturn 1
end
"""


# ── sector <-> col/row (the transposition guard) ────────────────────────────

def test_sector_to_colrow_A9_is_col9_row1():
    assert rp.sector_to_colrow("A9") == (9, 1)  # NOT (1, 9) == sector I1


def test_sector_roundtrip():
    for code in ("A1", "A9", "P16", "H8"):
        col, row = rp.sector_to_colrow(code)
        assert rp.colrow_to_sector(col, row) == code


def test_bad_sector_raises():
    for bad in ("", "9A", "Q1", "A0", "A17", "AA1"):
        with pytest.raises(ValueError):
            rp.sector_to_colrow(bad)


def test_gridno_range():
    rp.validate_gridno(0)
    rp.validate_gridno(25599)
    for bad in (-1, 25600, 99999):
        with pytest.raises(ValueError):
            rp.validate_gridno(bad)


# ── upsert / managed block isolation ────────────────────────────────────────

def test_upsert_creates_block_and_leaves_hand_lines_untouched():
    out = rp.apply_upsert(FIXTURE, profile=63, col=9, row=1, z=0, gridno=13979, label="Jay")
    parsed = rp.read_placements(out)
    managed = {p.profile for p in parsed["managed"]}
    hand = {p.profile for p in parsed["handAuthored"]}
    assert managed == {63}
    assert hand == {166, 59}  # hand-authored lines still present, still outside
    assert "-- Dogmeat" in out and "-- Ringo" in out


def test_upsert_is_idempotent():
    once = rp.apply_upsert(FIXTURE, 63, 9, 1, 0, 13979, "Jay")
    twice = rp.apply_upsert(once, 63, 9, 1, 0, 13979, "Jay")
    assert once == twice  # no duplicate line on re-write


def test_upsert_replaces_in_place():
    a = rp.apply_upsert(FIXTURE, 63, 9, 1, 0, 13979, "Jay")
    b = rp.apply_upsert(a, 63, 5, 2, 0, 100, "Jay moved")
    managed = rp.read_placements(b)["managed"]
    assert len(managed) == 1
    assert managed[0].gridno == 100 and managed[0].col == 5 and managed[0].row == 2


def test_remove():
    a = rp.apply_upsert(FIXTURE, 63, 9, 1, 0, 13979, "Jay")
    a = rp.apply_upsert(a, 64, 9, 1, 0, 10012, "Rex")
    b = rp.apply_remove(a, 63)
    managed = {p.profile for p in rp.read_placements(b)["managed"]}
    assert managed == {64}
    # removing a profile that isn't managed is a no-op
    assert rp.apply_remove(b, 999) == b


# ── file-shape edge cases ───────────────────────────────────────────────────

def test_creates_initnpcs_when_absent():
    text = "-- empty script\nfunction Foo()\n\treturn 0\nend\n"
    out = rp.apply_upsert(text, 166, 9, 1, 0, 5672, "Dogmeat")
    assert "function InitNPCs()" in out
    assert rp.read_placements(out)["managed"][0].profile == 166
    assert out.count(rp.BEGIN_MARKER.split("(")[0].strip()) == 1


def test_no_bom_ever():
    withbom = "﻿" + FIXTURE
    out = rp.apply_upsert(withbom, 63, 9, 1, 0, 13979, "Jay")
    assert not out.startswith("﻿")


def test_label_sanitized_cannot_break_block():
    out = rp.apply_upsert(FIXTURE, 63, 9, 1, 0, 100, "evil\nline -- oops")
    assert "evil line - oops" in out           # newline + comment-dash flattened
    assert len(rp.read_placements(out)["managed"]) == 1  # still one clean line


def test_adopt_moves_hand_line_into_block():
    out = rp.apply_adopt(FIXTURE, 166)          # 166 is hand-authored in FIXTURE
    parsed = rp.read_placements(out)
    assert 166 in {p.profile for p in parsed["managed"]}
    assert 166 not in {p.profile for p in parsed["handAuthored"]}
    assert 59 in {p.profile for p in parsed["handAuthored"]}  # other hand line untouched
    m = [p for p in parsed["managed"] if p.profile == 166][0]
    assert (m.col, m.row, m.gridno) == (9, 1, 5672)          # coords preserved


def test_adopt_noop_when_no_hand_line():
    assert rp.apply_adopt(FIXTURE, 999) == FIXTURE


def test_managed_line_uses_sector_derived_colrow():
    # Route-level: sector "A9" -> (9,1). Verify the pair the manager stores.
    col, row = rp.sector_to_colrow("A9")
    out = rp.apply_upsert(FIXTURE, 63, col, row, 0, 13979, "Jay")
    p = [q for q in rp.read_placements(out)["managed"] if q.profile == 63][0]
    assert (p.col, p.row) == (9, 1)
