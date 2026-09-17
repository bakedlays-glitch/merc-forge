"""Tests for the carried-item -> fact Lua manager (rpc_fact_setter.py).

Load-bearing: the generated block round-trips (read == what we wrote), hand-
authored triggers outside the managed block are never touched, upsert replaces
in place, remove works, and the free-fact range is enforced.
"""

import os
import pytest

from mercwizard_core import rpc_fact_setter as F

# strategicmap.lua with a hand-authored Dogmeat jacket trigger (no MW markers).
FIXTURE = """\
function HandleSectorTacticalEntry( sSectorX, sSectorY, bSectorZ, fHasEverBeenPlayerControlled )
\t-- hand-authored jacket path -- must stay untouched
\tif ( sSectorX == 9 and sSectorY == 1 and bSectorZ == 0 ) then
\t\tfor iLoop = GetTacticalStatusFirstID(0), GetTacticalStatusLastID(0) do
\t\t\tif ( HasItemInInventory( iLoop, 188 ) ) then SetFactTrue( 450 ) end
\t\tend
\tend
\tcurrenthour = GetWorldHour()
end
"""


def _fs(profile=63, col=9, row=1, z=0, item=188, fact=450):
    return F.FactSetter(profile=profile, col=col, row=row, z=z, item=item, fact=fact)


def test_upsert_creates_block_and_reads_back():
    out = F.apply_upsert(FIXTURE, _fs(profile=63, fact=451))
    got = F.read_fact_setters(out)
    assert len(got) == 1
    assert got[0].profile == 63 and got[0].item == 188 and got[0].fact == 451
    assert got[0].sector == "A9"
    assert "GetWorldHour" in out  # hand-authored body preserved


def test_hand_authored_trigger_untouched():
    out = F.apply_upsert(FIXTURE, _fs(profile=63, fact=451))
    # the hand-authored fact 450 line is still present and NOT parsed as managed
    assert "SetFactTrue( 450 )" in out
    assert 450 not in {g.fact for g in F.read_fact_setters(out)}


def test_upsert_idempotent_and_replace():
    once = F.apply_upsert(FIXTURE, _fs(profile=63, item=188, fact=451))
    twice = F.apply_upsert(once, _fs(profile=63, item=188, fact=451))
    assert once == twice
    changed = F.apply_upsert(once, _fs(profile=63, item=999, fact=451))
    got = [g for g in F.read_fact_setters(changed) if g.profile == 63]
    assert len(got) == 1 and got[0].item == 999


def test_multiple_profiles_and_remove():
    out = F.apply_upsert(FIXTURE, _fs(profile=63, fact=451))
    out = F.apply_upsert(out, _fs(profile=64, item=200, fact=452))
    assert {g.profile for g in F.read_fact_setters(out)} == {63, 64}
    out = F.apply_remove(out, 63)
    assert {g.profile for g in F.read_fact_setters(out)} == {64}


def test_fact_range_enforced():
    for bad in (430, 500, 0):
        with pytest.raises(ValueError):
            F.apply_upsert(FIXTURE, _fs(fact=bad))


def test_missing_function_raises():
    with pytest.raises(ValueError):
        F.apply_upsert("-- no HandleSectorTacticalEntry here\n", _fs())


def test_real_strategicmap_has_no_managed_entries():
    from pathlib import Path
    install = os.environ.get("JA2_INSTALL", "")
    p = Path(install) / "Data-1.13" / "Scripts" / "strategicmap.lua"
    if not p.exists():
        pytest.skip("live strategicmap.lua not present")
    text = p.read_text(encoding="utf-8", errors="replace")
    assert F.read_fact_setters(text) == []  # hand-authored jacket is not in our format
    # and we can author into the real file (in-memory) without exploding
    out = F.apply_upsert(text, _fs(profile=63, fact=451))
    assert any(g.profile == 63 for g in F.read_fact_setters(out))
