"""Tests for the RPCFacesSmall.xml small-face override manager (rpc_facessmall.py).

Load-bearing: an authored entry activates (FaceIndex == profile, uiIndex != 65535)
so the engine actually reads it; inactive vanilla rows are preserved untouched;
upsert replaces in place; remove clears only the managed entry.
"""

from mercwizard_core import rpc_facessmall as S

# A vanilla inactive row (uiIndex 65535) + an active Dogmeat-style override.
FIXTURE = """﻿<SMALLFACE>
\t<FACE>
\t\t<uiIndex>65535</uiIndex>
\t\t<Name>Ira</Name>
\t\t<FaceIndex>59</FaceIndex>
\t\t<EyesX>10</EyesX>
\t\t<EyesY>8</EyesY>
\t\t<MouthX>8</MouthX>
\t\t<MouthY>26</MouthY>
\t</FACE>
\t<FACE>
\t\t<uiIndex>31</uiIndex>
\t\t<Name>DOGMEAT</Name>
\t\t<FaceIndex>166</FaceIndex>
\t\t<EyesX>10</EyesX>
\t\t<EyesY>12</EyesY>
\t\t<MouthX>0</MouthX>
\t\t<MouthY>22</MouthY>
\t</FACE>
</SMALLFACE>
"""


def test_read_existing_active_override():
    got = S.read_override(FIXTURE, 166)
    assert got == {"eyesX": 10, "eyesY": 12, "mouthX": 0, "mouthY": 22, "name": "DOGMEAT"}


def test_inactive_row_is_not_read_as_override():
    # Ira's row has FaceIndex==59 but uiIndex==65535 (inactive) -> not an override.
    assert S.read_override(FIXTURE, 59) is None


def test_upsert_creates_active_entry():
    out = S.upsert(FIXTURE, 63, "Jay", 9, 8, 7, 24)
    got = S.read_override(out, 63)
    assert got is not None and got["mouthY"] == 24
    # engine-activation invariants: FaceIndex == profile, uiIndex != 65535
    assert "<FaceIndex>63</FaceIndex>" in out
    assert "<uiIndex>63</uiIndex>" in out


def test_upsert_preserves_other_rows():
    out = S.upsert(FIXTURE, 63, "Jay", 9, 8, 7, 24)
    assert "DOGMEAT" in out and "Ira" in out
    assert S.read_override(out, 166) is not None  # Dogmeat untouched


def test_upsert_replaces_in_place():
    a = S.upsert(FIXTURE, 63, "Jay", 9, 8, 7, 24)
    b = S.upsert(a, 63, "Jay", 5, 6, 3, 20)
    assert a != b
    assert S.read_override(b, 63) == {"eyesX": 5, "eyesY": 6, "mouthX": 3, "mouthY": 20, "name": "Jay"}
    # still exactly one active entry for 63
    assert b.count("<FaceIndex>63</FaceIndex>") == 1


def test_upsert_idempotent():
    a = S.upsert(FIXTURE, 63, "Jay", 9, 8, 7, 24)
    assert a == S.upsert(a, 63, "Jay", 9, 8, 7, 24)


def test_remove_clears_only_managed_entry():
    a = S.upsert(FIXTURE, 63, "Jay", 9, 8, 7, 24)
    b = S.remove(a, 63)
    assert S.read_override(b, 63) is None
    assert S.read_override(b, 166) is not None      # Dogmeat kept
    assert "<Name>Ira</Name>" in b                  # vanilla inactive kept


def test_upsert_into_empty_file():
    out = S.upsert("", 63, "Jay", 9, 8, 7, 24)
    assert out.startswith("﻿<SMALLFACE>")
    assert S.read_override(out, 63) is not None
