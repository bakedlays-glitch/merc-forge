"""Tests for the Backgrounds.xml byte-splice writer, schema, and write routes.

The writer's contract is "edit only the target block's bytes": every other
entry — including multi-line descriptions, nested <drugtypes>/<drugitems>, and
unknown mod columns — must survive byte-for-byte. The engine ordering quirk
(`num_found_background` = the LAST PHYSICAL entry's id) is also asserted.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mercwizard_core import backgrounds_schema as schema
from mercwizard_core.inject import backgrounds_xml as bg

# Physical order [0,1,2,5,3]: max id is 5 but the LAST physical entry is 3, so
# num_found_background = 3 (mirrors the real file whose tail is 198 < max 356).
# Entry 1 carries a multi-line description with an escaped ampersand, a nested
# <drugtypes> list, and an unknown mod column <future_field> — all must be
# preserved verbatim across edits to other entries.
SAMPLE = (
    "<BACKGROUNDS>\r\n"
    "\t<BACKGROUND>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szName>Background name (128 letters)</szName>\r\n"
    "\t\t<szShortName>short name (20)</szShortName>\r\n\t\t<szDescription>Enter a description.</szDescription>\r\n"
    "\t\t<strength>0</strength>\r\n\t</BACKGROUND>\r\n"
    "\t<BACKGROUND>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szName>ANC Veteran</szName>\r\n"
    "\t\t<szShortName>ANC Vet</szShortName>\r\n\t\t<szDescription>Line one\r\nLine two &amp; more.</szDescription>\r\n"
    "\t\t<ap_swamp>3</ap_swamp>\r\n\t\t<no_male>1</no_male>\r\n\t\t<future_field>7</future_field>\r\n"
    "\t\t<drugtypes>\r\n\t\t\t<drugtype>0</drugtype>\r\n\t\t\t<drugtype>0</drugtype>\r\n\t\t</drugtypes>\r\n"
    "\t</BACKGROUND>\r\n"
    "\t<BACKGROUND>\r\n\t\t<uiIndex>2</uiIndex>\r\n\t\t<szName>Doctor</szName>\r\n"
    "\t\t<szShortName>Doc</szShortName>\r\n\t\t<szDescription>Heals.</szDescription>\r\n\t\t<medical>5</medical>\r\n\t</BACKGROUND>\r\n"
    "\t<BACKGROUND>\r\n\t\t<uiIndex>5</uiIndex>\r\n\t\t<szName>HighId</szName>\r\n"
    "\t\t<szShortName>Hi</szShortName>\r\n\t\t<szDescription>High id, not last.</szDescription>\r\n\t\t<agility>2</agility>\r\n\t</BACKGROUND>\r\n"
    "\t<BACKGROUND>\r\n\t\t<uiIndex>3</uiIndex>\r\n\t\t<szName>Placeholder</szName>\r\n"
    "\t\t<szShortName>PH</szShortName>\r\n\t\t<szDescription>tail entry</szDescription>\r\n\t\t<no_female>1</no_female>\r\n\t</BACKGROUND>\r\n"
    "</BACKGROUNDS>"
)


@pytest.fixture
def bg_file(tmp_path: Path) -> Path:
    p = tmp_path / "Backgrounds.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    return p


def _block(text: str, ui_index: int) -> str:
    """Return the raw <BACKGROUND> block for a given uiIndex (for byte-stability checks)."""
    for m in re.finditer(r"<BACKGROUND>.*?</BACKGROUND>", text, re.S):
        if re.search(rf"<uiIndex>\s*{ui_index}\s*</uiIndex>", m.group(0)):
            return m.group(0)
    raise AssertionError(f"no block {ui_index}")


def _ids(text: str) -> list[int]:
    return [int(m) for m in re.findall(r"<uiIndex>(\d+)</uiIndex>", text)]


# ── read_catalog ────────────────────────────────────────────────────────────

def test_read_catalog_basic(bg_file: Path) -> None:
    cat = bg.read_catalog(bg_file)
    assert [e.ui_index for e in cat.entries] == [0, 1, 2, 5, 3]
    # num_found_background = LAST physical entry's id, NOT the max.
    assert cat.num_found_background == 3
    assert cat.duplicate_ids == []
    e1 = next(e for e in cat.entries if e.ui_index == 1)
    assert ("ap_swamp", 3) in e1.modifiers
    assert ("no_male", 1) in e1.modifiers
    assert e1.has_nested is True
    assert e1.has_unknown is True  # <future_field> is outside the schema


def test_read_catalog_detects_duplicates(tmp_path: Path) -> None:
    p = tmp_path / "b.xml"
    p.write_bytes((
        "<BACKGROUNDS>\r\n"
        "\t<BACKGROUND>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szName>A</szName>\r\n\t</BACKGROUND>\r\n"
        "\t<BACKGROUND>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szName>B</szName>\r\n\t</BACKGROUND>\r\n"
        "</BACKGROUNDS>"
    ).encode("utf-8"))
    assert bg.read_catalog(p).duplicate_ids == [1]


def test_read_catalog_missing_file(tmp_path: Path) -> None:
    cat = bg.read_catalog(tmp_path / "nope.xml")
    assert cat.entries == [] and cat.num_found_background == 0


# ── next_free_index ─────────────────────────────────────────────────────────

def test_next_free_index_is_max_plus_one(bg_file: Path) -> None:
    assert bg.next_free_index(bg_file) == 6  # max id is 5


def test_next_free_index_fills_gap_when_max_is_ceiling(tmp_path: Path) -> None:
    p = tmp_path / "b.xml"
    rows = "".join(
        f"\t<BACKGROUND>\r\n\t\t<uiIndex>{i}</uiIndex>\r\n\t\t<szName>n{i}</szName>\r\n\t</BACKGROUND>\r\n"
        for i in range(0, schema.MAX_INDEX + 1) if i != 7  # leave gap at 7
    )
    p.write_bytes(f"<BACKGROUNDS>\r\n{rows}</BACKGROUNDS>".encode("utf-8"))
    # max is 499 so max+1=500 is invalid; must fall back to the lowest gap.
    assert bg.next_free_index(p) == 7


def test_next_free_index_table_full(tmp_path: Path) -> None:
    p = tmp_path / "b.xml"
    rows = "".join(
        f"\t<BACKGROUND>\r\n\t\t<uiIndex>{i}</uiIndex>\r\n\t\t<szName>n{i}</szName>\r\n\t</BACKGROUND>\r\n"
        for i in range(0, schema.MAX_INDEX + 1)
    )
    p.write_bytes(f"<BACKGROUNDS>\r\n{rows}</BACKGROUNDS>".encode("utf-8"))
    with pytest.raises(bg.BackgroundError) as ei:
        bg.next_free_index(p)
    assert ei.value.code == "TABLE_FULL"


# ── create ──────────────────────────────────────────────────────────────────

def test_create_default_inserts_before_physical_last(bg_file: Path) -> None:
    before_tail = _block(bg_file.read_bytes().decode("utf-8"), 3)
    r = bg.create_background(
        bg_file, ui_index=10, name="Tester", short_name="T",
        description="desc", fields={"strength": 5, "no_male": 1},
    )
    text = bg_file.read_bytes().decode("utf-8")
    # New entry sits just before the physical-last entry (id 3), so nfb is unchanged.
    assert _ids(text) == [0, 1, 2, 5, 10, 3]
    assert r["num_found_background"] == 3
    assert r["imp_selectable"] is False
    # The previously-last entry is byte-identical and still physically last.
    assert _block(text, 3) == before_tail
    assert "\r\n" in text and "\r\n\r\n" not in text  # CRLF kept, no blank lines
    nb = _block(text, 10)
    assert "<strength>5</strength>" in nb and "<no_male>1</no_male>" in nb
    # Zero-valued owned fields are NOT written (clean, ANC-style).
    assert "<agility>" not in nb


def test_create_imp_selectable_appends_last(bg_file: Path) -> None:
    r = bg.create_background(
        bg_file, ui_index=10, name="T", short_name="T", description="d",
        fields={}, make_imp_selectable=True,
    )
    text = bg_file.read_bytes().decode("utf-8")
    assert _ids(text)[-1] == 10
    assert r["num_found_background"] == 10


def test_create_preserves_other_entries_byte_for_byte(bg_file: Path) -> None:
    original = bg_file.read_bytes().decode("utf-8")
    b1, b2, b5 = _block(original, 1), _block(original, 2), _block(original, 5)
    bg.create_background(bg_file, ui_index=11, name="X", short_name="X",
                         description="x", fields={})
    text = bg_file.read_bytes().decode("utf-8")
    assert _block(text, 1) == b1  # multi-line desc + drugtypes + future_field intact
    assert _block(text, 2) == b2
    assert _block(text, 5) == b5


def test_create_rejects_template_and_out_of_range_and_taken(bg_file: Path) -> None:
    for bad in (0, schema.NUM_BACKGROUND, schema.NUM_BACKGROUND + 50, -1):
        with pytest.raises(bg.BackgroundError) as ei:
            bg.create_background(bg_file, ui_index=bad, name="x", short_name="x",
                                 description="x", fields={})
        assert ei.value.code == "INVALID_INDEX"
    with pytest.raises(bg.BackgroundError) as ei:
        bg.create_background(bg_file, ui_index=2, name="dup", short_name="d",
                             description="d", fields={})
    assert ei.value.code == "INDEX_TAKEN"


# ── edit ────────────────────────────────────────────────────────────────────

def test_edit_in_place_set_remove_rename(bg_file: Path) -> None:
    before_others = {i: _block(bg_file.read_bytes().decode("utf-8"), i) for i in (0, 2, 5, 3)}
    bg.edit_background(
        bg_file, ui_index=1, name="ANC Veteran 2", short_name="ANC2",
        description="new\r\ndesc",
        fields={"ap_swamp": 3, "no_male": 0, "strength": 4},  # keep ap_swamp, drop no_male, add strength
    )
    text = bg_file.read_bytes().decode("utf-8")
    b1 = _block(text, 1)
    assert "<szName>ANC Veteran 2</szName>" in b1
    assert "<ap_swamp>3</ap_swamp>" in b1
    assert "<no_male>" not in b1                  # zeroed → removed
    assert "<strength>4</strength>" in b1         # new → inserted
    assert "<drugtypes>" in b1 and "<future_field>7</future_field>" in b1  # preserved
    assert "num_found_background" and bg.read_catalog(bg_file).num_found_background == 3
    # Edits to entry 1 don't touch any other entry.
    for i, blk in before_others.items():
        assert _block(text, i) == blk


def test_edit_rejects_template_missing_and_duplicate(bg_file: Path, tmp_path: Path) -> None:
    with pytest.raises(bg.BackgroundError) as ei:
        bg.edit_background(bg_file, ui_index=0, name="x", short_name="", description="", fields={})
    assert ei.value.code == "TEMPLATE_PROTECTED"
    with pytest.raises(bg.BackgroundError) as ei:
        bg.edit_background(bg_file, ui_index=99, name="x", short_name="", description="", fields={})
    assert ei.value.code == "BACKGROUND_NOT_FOUND"
    dup = tmp_path / "d.xml"
    dup.write_bytes((
        "<BACKGROUNDS>\r\n"
        "\t<BACKGROUND>\r\n\t\t<uiIndex>4</uiIndex>\r\n\t\t<szName>A</szName>\r\n\t</BACKGROUND>\r\n"
        "\t<BACKGROUND>\r\n\t\t<uiIndex>4</uiIndex>\r\n\t\t<szName>B</szName>\r\n\t</BACKGROUND>\r\n"
        "</BACKGROUNDS>"
    ).encode("utf-8"))
    with pytest.raises(bg.BackgroundError) as ei:
        bg.edit_background(dup, ui_index=4, name="x", short_name="", description="", fields={})
    assert ei.value.code == "DUPLICATE_INDEX"


def test_edit_xml_escapes_special_chars(bg_file: Path) -> None:
    bg.edit_background(bg_file, ui_index=2, name="A & B <Co>", short_name="x",
                       description="desc > with < & symbols", fields={})
    # Round-trips cleanly through lxml on read-back.
    e2 = next(e for e in bg.read_catalog(bg_file).entries if e.ui_index == 2)
    assert e2.name == "A & B <Co>"
    assert e2.description == "desc > with < & symbols"
    raw = bg_file.read_bytes().decode("utf-8")
    assert "&amp;" in raw and "&lt;" in raw and "&gt;" in raw


def test_edit_value_with_backslash_digit_is_literal(bg_file: Path) -> None:
    # Guards against re.sub treating "\1" in the replacement as a backreference.
    bg.edit_background(bg_file, ui_index=2, name=r"path C:\1\2", short_name="x",
                       description="", fields={})
    e2 = next(e for e in bg.read_catalog(bg_file).entries if e.ui_index == 2)
    assert e2.name == r"path C:\1\2"


# ── delete ──────────────────────────────────────────────────────────────────

def test_delete_removes_block_and_keeps_others(bg_file: Path) -> None:
    original = bg_file.read_bytes().decode("utf-8")
    b1, b5 = _block(original, 1), _block(original, 5)
    r = bg.delete_background(bg_file, ui_index=2)
    text = bg_file.read_bytes().decode("utf-8")
    assert _ids(text) == [0, 1, 5, 3]
    assert r["was_physical_last"] is False
    assert _block(text, 1) == b1 and _block(text, 5) == b5
    assert "\r\n\r\n" not in text  # no orphaned blank line


def test_delete_physical_last_changes_nfb(bg_file: Path) -> None:
    r = bg.delete_background(bg_file, ui_index=3)  # 3 is the physical tail
    assert r["was_physical_last"] is True
    # New tail is id 5 → nfb becomes 5.
    assert r["num_found_background"] == 5


def test_delete_template_refused(bg_file: Path) -> None:
    with pytest.raises(bg.BackgroundError) as ei:
        bg.delete_background(bg_file, ui_index=0)
    assert ei.value.code == "TEMPLATE_PROTECTED"


# ── IMP threshold ───────────────────────────────────────────────────────────

def test_set_imp_threshold_moves_entry_last(bg_file: Path) -> None:
    r = bg.set_imp_threshold(bg_file, ui_index=1)
    text = bg_file.read_bytes().decode("utf-8")
    assert _ids(text)[-1] == 1
    assert r["num_found_background"] == 1 and r["moved"] is True


def test_set_imp_threshold_noop_when_already_last(bg_file: Path) -> None:
    r = bg.set_imp_threshold(bg_file, ui_index=3)  # already last
    assert r["moved"] is False and r["num_found_background"] == 3


def test_make_all_imp_selectable_moves_max_last(bg_file: Path) -> None:
    r = bg.make_all_imp_selectable(bg_file)
    assert _ids(bg_file.read_bytes().decode("utf-8"))[-1] == 5
    assert r["num_found_background"] == 5  # max id now physically last


# ── BOM preservation ────────────────────────────────────────────────────────

def test_bom_preserved_across_edit(tmp_path: Path) -> None:
    p = tmp_path / "bom.xml"
    p.write_bytes(b"\xef\xbb\xbf" + SAMPLE.encode("utf-8"))
    bg.edit_background(p, ui_index=2, name="Z", short_name="z", description="z", fields={})
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")


# ── schema ──────────────────────────────────────────────────────────────────

def test_schema_clamp_ranges() -> None:
    assert schema.clamp_value("ap_polar", 99) == (8, True)
    assert schema.clamp_value("ap_polar", -99) == (-8, True)
    assert schema.clamp_value("strength", 5) == (5, False)
    assert schema.clamp_value("burial_assignment", 5000) == (1000, True)
    # dislikebackground is the one unclamped (signed pairing) field.
    assert schema.clamp_value("dislikebackground", -12345) == (-12345, False)


def test_schema_utf16_len_counts_code_units() -> None:
    assert schema.utf16_len("abc") == 3
    assert schema.utf16_len("\U0001F600") == 2  # emoji = 2 UTF-16 code units


def test_schema_flag_set_matches_engine() -> None:
    for k in ("no_male", "no_female", "druguse", "animal_friend", "alt_impcreation"):
        assert k in schema.FLAG_FIELDS
    # smoker is an enum (0/1/2), NOT a flag.
    assert "smoker" not in schema.FLAG_FIELDS


# ════════════════════════════════════════════════════════════════════════════
#  Route integration tests
# ════════════════════════════════════════════════════════════════════════════

from main import create_app  # noqa: E402
from routes.state import get_state  # noqa: E402


@pytest.fixture
def _reset_state():
    st = get_state()
    st._installs = {}
    st._active_install_id = None
    st._scan_done = False
    yield
    st._installs = {}
    st._active_install_id = None
    st._scan_done = False


@pytest.fixture
def client(_reset_state) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def install_with_bg(client: TestClient, tmp_path: Path) -> dict:
    root = tmp_path / "install"
    table = root / "Data-1.13" / "TableData"
    table.mkdir(parents=True)
    (root / "JA2.exe").touch()
    (table / "MercProfiles.xml").write_text("<MERCPROFILES />")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />")
    (table / "Backgrounds.xml").write_bytes(SAMPLE.encode("utf-8"))
    resp = client.post("/api/v1/installs", json={"path": str(root)})
    assert resp.status_code == 200, resp.text
    info = resp.json()
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})
    return info


def test_route_get_includes_schema_and_nfb(client: TestClient, install_with_bg) -> None:
    r = client.get("/api/v1/backgrounds")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_present"] and body["writable"]
    assert body["num_found_background"] == 3
    assert body["max_index"] == schema.MAX_INDEX
    assert len(body["schema_fields"]) == len(schema.FIELD_SPECS)
    by_id = {b["id"]: b for b in body["backgrounds"]}
    assert by_id[5]["imp_selectable"] is False  # id 5 > nfb 3
    assert by_id[3]["imp_selectable"] is True
    assert by_id[1]["has_advanced_data"] is True  # nested + unknown


def test_route_create_auto_id_and_clamp(client: TestClient, install_with_bg) -> None:
    r = client.post("/api/v1/backgrounds", json={
        "name": "Demolitions", "short_name": "Demo", "description": "boom",
        "fields": {"explosives": 8, "ap_polar": 99, "no_male": 5},
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["ui_index"] == 6  # auto = max+1
    assert out["backup_id"]
    # ap_polar clamped 99 -> 8; flag no_male coerced 5 -> 1.
    clamps = {c["key"]: c["stored"] for c in out["clamps"]}
    assert clamps["ap_polar"] == 8 and clamps["no_male"] == 1
    got = {b["id"]: b for b in client.get("/api/v1/backgrounds").json()["backgrounds"]}
    assert got[6]["name"] == "Demolitions"


def test_route_put_and_delete(client: TestClient, install_with_bg) -> None:
    r = client.put("/api/v1/backgrounds/2", json={
        "name": "Surgeon", "short_name": "Surg", "description": "expert",
        "fields": {"medical": 9},
    })
    assert r.status_code == 200, r.text
    got = {b["id"]: b for b in client.get("/api/v1/backgrounds").json()["backgrounds"]}
    assert got[2]["name"] == "Surgeon"
    assert ("medical", 9) in [(m["key"], m["value"]) for m in got[2]["modifiers"]]

    d = client.delete("/api/v1/backgrounds/2")
    assert d.status_code == 200, d.text
    assert 2 not in {b["id"] for b in client.get("/api/v1/backgrounds").json()["backgrounds"]}


def test_route_template_guard(client: TestClient, install_with_bg) -> None:
    assert client.put("/api/v1/backgrounds/0", json={"name": "x"}).status_code == 400
    assert client.delete("/api/v1/backgrounds/0").status_code == 400


def test_route_imp_threshold_all(client: TestClient, install_with_bg) -> None:
    r = client.post("/api/v1/backgrounds/imp-threshold", json={"all": True})
    assert r.status_code == 200, r.text
    assert r.json()["num_found_background"] == 5  # max id now last
    assert client.get("/api/v1/backgrounds").json()["num_found_background"] == 5


def test_route_create_requires_name(client: TestClient, install_with_bg) -> None:
    r = client.post("/api/v1/backgrounds", json={"name": "  ", "fields": {}})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "BACKGROUND_INVALID"


def test_route_rejects_control_chars(client: TestClient, install_with_bg) -> None:
    """A C0 control char (e.g. \\x01) in a background field is rejected with a 400
    rather than written as a raw byte. Backgrounds.xml has no <?xml?> decl, so the
    engine's expat (XML 1.0) would fail the WHOLE file's load at boot — and XML
    forbids these even as numeric entities, so the only fix is to reject input."""
    # create path
    r = client.post("/api/v1/backgrounds", json={
        "name": "Bad\x01Name", "short_name": "x", "description": "ok", "fields": {},
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "BACKGROUND_INVALID"
    # edit path (a form-feed 0x0C in the description is caught too)
    r = client.put("/api/v1/backgrounds/2", json={
        "name": "Doctor", "short_name": "Doc", "description": "He\x0cals", "fields": {},
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "BACKGROUND_INVALID"
    # the table is untouched and still loads (no raw control byte slipped in).
    got = client.get("/api/v1/backgrounds")
    assert got.status_code == 200
    assert {b["id"] for b in got.json()["backgrounds"]} >= {0, 1, 2, 3, 5}


def test_route_no_backgrounds_file_returns_400(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "nostomp"
    table = root / "Data-1.13" / "TableData"
    table.mkdir(parents=True)
    (root / "JA2.exe").touch()
    (table / "MercProfiles.xml").write_text("<MERCPROFILES />")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />")
    info = client.post("/api/v1/installs", json={"path": str(root)}).json()
    client.post("/api/v1/installs/active", json={"install_id": info["id"]})
    r = client.post("/api/v1/backgrounds", json={"name": "X", "fields": {}})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "BACKGROUNDS_NOT_PRESENT"


# ── cp1252 / non-UTF-8 tolerance (review fix 2026-06-06) ─────────────────────

def test_editor_survives_cp1252_backgrounds(tmp_path: Path) -> None:
    """A Windows-1252 Backgrounds.xml (accented high bytes, no <?xml?> decl) must
    not crash the editor: read_catalog lists every entry, and editing one entry
    leaves the others byte-for-byte intact — the high byte included."""
    # \xe9='é', \xef='ï' in Windows-1252/latin-1 — single high bytes, NOT utf-8's
    # two-byte runs. No <?xml?> declaration, so a utf-8 read would raise / yield
    # an empty catalog before the fix.
    cp_bytes = (
        b"<BACKGROUNDS>\r\n"
        b"\t<BACKGROUND>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szName>Template</szName>\r\n"
        b"\t\t<szShortName>T</szShortName>\r\n\t\t<szDescription>tpl</szDescription>\r\n\t</BACKGROUND>\r\n"
        b"\t<BACKGROUND>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szName>Caf\xe9 Vet</szName>\r\n"
        b"\t\t<szShortName>Caf\xe9</szShortName>\r\n\t\t<szDescription>Na\xefve.</szDescription>\r\n\t\t<medical>5</medical>\r\n\t</BACKGROUND>\r\n"
        b"\t<BACKGROUND>\r\n\t\t<uiIndex>2</uiIndex>\r\n\t\t<szName>Plain</szName>\r\n"
        b"\t\t<szShortName>P</szShortName>\r\n\t\t<szDescription>ascii</szDescription>\r\n\t\t<strength>3</strength>\r\n\t</BACKGROUND>\r\n"
        b"</BACKGROUNDS>"
    )
    p = tmp_path / "Backgrounds.xml"
    p.write_bytes(cp_bytes)
    assert b"\xe9" in cp_bytes

    # GET path: must list all entries, not silently return an empty catalog.
    cat = bg.read_catalog(p)
    assert [e.ui_index for e in cat.entries] == [0, 1, 2]

    # WRITE path: edit entry 2; entry 1's bytes (high byte and all) must survive.
    block1_before = _block(cp_bytes.decode("latin-1"), 1)
    bg.edit_background(p, ui_index=2, name="Plain2", short_name="P2",
                       description="changed", fields={"strength": 4})
    raw_after = p.read_bytes()
    assert b"\xe9" in raw_after  # high byte preserved as a single byte
    assert _block(raw_after.decode("latin-1"), 1) == block1_before


def test_editor_encodes_supra_latin1_input_as_xml_entity(tmp_path: Path) -> None:
    """Input the engine's 1-byte charset can't hold (e.g. a U+2019 smart quote)
    is written as a numeric XML entity, never a UnicodeEncodeError/500."""
    p = tmp_path / "Backgrounds.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    bg.edit_background(p, ui_index=2, name="O’Brien", short_name="OB",
                       description="d", fields={})
    raw = p.read_bytes()
    assert b"&#8217;" in raw  # smart quote -> numeric char ref
    cat = bg.read_catalog(p)  # still well-formed XML
    name2 = next(e.name for e in cat.entries if e.ui_index == 2)
    assert name2 == "O’Brien"  # round-trips back to the smart quote


def test_editor_encodes_latin1_accents_as_entities_not_raw_bytes(tmp_path: Path) -> None:
    """A user-typed accented char in the 128..255 range (é=U+00E9, ñ=U+00F1) must
    be written as a numeric XML entity, NOT a raw high byte. Backgrounds.xml has
    no <?xml?> decl, so the engine's expat parser defaults to UTF-8 — a lone 0xE9
    is invalid UTF-8 and would fail the WHOLE file's load at boot. (The editor's
    own latin-1 read fallback masks this in-app; the engine has no such fallback.)
    Complements the >255 smart-quote case above, which xmlcharrefreplace already
    handled — this guards the 128..255 gap.
    """
    p = tmp_path / "Backgrounds.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    # Sibling entry 1 carries a multi-line desc + <drugtypes> + <future_field>.
    sib1_before = _block(p.read_bytes().decode("utf-8"), 1)

    # create: accented name/short_name + a smart quote (>255) in the description.
    bg.create_background(p, ui_index=10, name="Café Owner", short_name="Café",
                         description="Runs a café — “the best”.", fields={})
    # edit an existing entry with more 128..255 chars (ñ, ï, é).
    bg.edit_background(p, ui_index=2, name="Niño", short_name="N",
                       description="Naïve résumé.", fields={})

    raw = p.read_bytes()
    # (a) the file is still valid UTF-8 — a raw high byte would make this raise.
    raw.decode("utf-8")
    assert b"\xe9" not in raw and b"\xf1" not in raw  # no raw é / ñ byte slipped through
    # (b) the values are present as numeric character references.
    assert b"&#233;" in raw    # é  (U+00E9 — the 128..255 case this fix adds)
    assert b"&#241;" in raw    # ñ  (U+00F1)
    assert b"&#8220;" in raw   # “  (U+201C — the >255 case, also via _esc now)
    # (c) the untouched sibling entry is byte-for-byte identical.
    assert _block(raw.decode("utf-8"), 1) == sib1_before
    # round-trips back to the real glyphs through the read path.
    by_id = {e.ui_index: e for e in bg.read_catalog(p).entries}
    assert by_id[10].name == "Café Owner"
    assert by_id[2].name == "Niño" and "résumé" in by_id[2].description


# ── .wmerc import splice (upsert_background_block) ───────────────────────────

def test_import_splice_accent_safe_in_no_decl_target(tmp_path: Path) -> None:
    """A .wmerc bundle's <BACKGROUND> fragment carrying an accented name (a raw é
    codepoint, exactly as `ET.tostring(encoding="unicode")` emits from a UTF-8
    source) must be spliced as ASCII numeric entities, not a lone high byte.
    Backgrounds.xml has no <?xml?> decl, so the engine's expat defaults to UTF-8;
    a raw 0xE9 byte is invalid UTF-8 and fails the WHOLE file's load at boot.
    `_write_text`'s latin-1+xmlcharrefreplace does NOT catch a 0x80..0xFF codepoint
    (it's latin-1-encodable), so the guard lives in the splice path — mirroring
    slot_table_xml's `test_new_row_accent_safe_in_utf8_target`.
    """
    p = tmp_path / "Backgrounds.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))  # no <?xml?> declaration
    sib1_before = _block(p.read_bytes().decode("utf-8"), 1)

    # Exporter-shaped fragment: <BACKGROUND> at column 0, children at one tab, LF
    # endings, a RAW é (U+00E9) codepoint in name / short-name / description.
    frag = (
        "<BACKGROUND>\n"
        "\t<uiIndex>50</uiIndex>\n"
        "\t<szName>Café Owner</szName>\n"
        "\t<szShortName>Café</szShortName>\n"
        "\t<szDescription>Runs a café.</szDescription>\n"
        "\t<medical>5</medical>\n"
        "</BACKGROUND>\n"
    )
    assert "é" in frag  # the fragment really carries a raw é codepoint

    r = bg.upsert_background_block(p, block_text=frag)
    assert r["created"] is True and r["ui_index"] == 50

    raw = p.read_bytes()
    # (a) the file stays valid UTF-8 — a raw high byte would make this raise.
    raw.decode("utf-8")
    assert b"\xe9" not in raw            # no lone cp1252 'é' byte slipped through
    # (b) the accented value is present as a numeric character reference.
    assert b"&#233;" in raw             # é → &#233;
    # (c) the untouched sibling entry (multi-line desc + drugtypes + future_field)
    #     is byte-for-byte identical.
    assert _block(raw.decode("utf-8"), 1) == sib1_before
    # round-trips back to the real glyph through the read path.
    by_id = {e.ui_index: e for e in bg.read_catalog(p).entries}
    assert by_id[50].name == "Café Owner" and by_id[50].short_name == "Café"
    # default splice keeps the IMP picker bound unchanged (inserted before tail id 3).
    assert r["num_found_background"] == 3


def test_import_splice_strips_illegal_control_chars(tmp_path: Path) -> None:
    """A hand-crafted .wmerc <BACKGROUND> fragment carrying an XML-1.0-illegal C0
    control char (e.g. 0x01) must NOT be spliced verbatim. The editor validator
    rejects such input, but the import splice (upsert_background_block) string-
    splices WITHOUT parsing -- so _ascii_safe must STRIP the control char, else
    the raw byte bricks the no-<?xml?> target at boot (expat is XML 1.0, which
    forbids these even as numeric entities). Regression for the pre-push
    meta-review (the editor-only control-char fix left this surface open)."""
    p = tmp_path / "Backgrounds.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    sib1_before = _block(p.read_bytes().decode("utf-8"), 1)

    frag = (
        "<BACKGROUND>\n"
        "\t<uiIndex>50</uiIndex>\n"
        "\t<szName>Bad\x01Name</szName>\n"
        "\t<szShortName>OK</szShortName>\n"
        "\t<szDescription>fine</szDescription>\n"
        "</BACKGROUND>\n"
    )
    r = bg.upsert_background_block(p, block_text=frag)
    assert r["created"] is True

    raw = p.read_bytes()
    assert b"\x01" not in raw          # the control byte was stripped, not written
    raw.decode("utf-8")               # still valid bytes
    # The whole file still parses (a raw 0x01 would make lxml/expat fail it).
    by_id = {e.ui_index: e for e in bg.read_catalog(p).entries}
    assert by_id[50].name == "BadName"  # control char gone, rest of the value intact
    # The untouched sibling entry is byte-for-byte identical.
    assert _block(raw.decode("utf-8"), 1) == sib1_before
