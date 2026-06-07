"""Tests for the slot-keyed extra-table byte-splice writer (MercOpinions/MercQuote).

The writer's contract is "edit only the target slot's row bytes": the <?xml?>
declaration, CRLF line endings, a BOM, and every SIBLING row — whether its high
bytes are Windows-1252 (é == 0xE9) or genuine UTF-8 multi-byte (é == 0xC3 0xA9) —
must survive byte-for-byte. The old whole-file ET reflow mojibaked all of those;
these tests pin the regression.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mercwizard_core.inject import slot_table_xml as st


# Sibling rows carry high bytes on purpose: slot 5's nickname has a Windows-1252
# 'é' (0xE9), slot 6's has a UTF-8 'é' (0xC3 0xA9). Slot 10 is the upsert target.
OPINIONS_CP1252 = (
    b'<?xml version="1.0" encoding="Windows-1252"?>\r\n'
    b"<MERCOPINIONS>\r\n"
    b"\t<OPINION>\r\n\t\t<uiIndex>5</uiIndex>\r\n\t\t<zNickname>Ren\xe9e</zNickname>\r\n"
    b'\t\t<AnOpinion id = "0" modifier = "5" />\r\n\t</OPINION>\r\n'
    b"\t<OPINION>\r\n\t\t<uiIndex>6</uiIndex>\r\n\t\t<zNickname>Andr\xc3\xa9</zNickname>\r\n"
    b'\t\t<AnOpinion id = "1" modifier = "2" />\r\n\t</OPINION>\r\n'
    b"\t<OPINION>\r\n\t\t<uiIndex>10</uiIndex>\r\n\t\t<zNickname>Bob</zNickname>\r\n"
    b'\t\t<AnOpinion id = "0" modifier = "3" />\r\n\t</OPINION>\r\n'
    b"</MERCOPINIONS>\r\n"
)

# Exporter-shaped fragment: LF endings, <OPINION> at column 0, children at one
# tab, carrying the SOURCE slot (220) — the writer must re-key it.
OPINION_FRAGMENT = (
    "<OPINION>\n"
    "\t<uiIndex>220</uiIndex>\n"
    "\t<zNickname>Bob</zNickname>\n"
    '\t<AnOpinion id="0" modifier="9" />\n'
    "</OPINION>\n"
)

QUOTE_CP1252 = (
    b'<?xml version="1.0" encoding="Windows-1252"?>\r\n'
    b"<QARRAY>\r\n"
    b"\t<PROFILE>\r\n\t\t<uiIndex>5</uiIndex>\r\n\t\t<QuoteExpHeadShotOnly>1</QuoteExpHeadShotOnly>\r\n\t</PROFILE>\r\n"
    b"</QARRAY>\r\n"
)

PROFILE_FRAGMENT = (
    "<PROFILE>\n"
    "\t<uiIndex>220</uiIndex>\n"
    "\t<QuoteExpHeadShotOnly>1</QuoteExpHeadShotOnly>\n"
    "\t<QuoteExpTeamSpecific>1</QuoteExpTeamSpecific>\n"
    "</PROFILE>\n"
)


def _row_bytes(data: bytes, row_tag: bytes, ui_index: int) -> bytes:
    pat = re.compile(rb"<" + row_tag + rb">.*?</" + row_tag + rb">", re.S)
    for m in pat.finditer(data):
        if re.search(rb"<uiIndex>\s*%d\s*</uiIndex>" % ui_index, m.group(0)):
            return m.group(0)
    raise AssertionError(f"no <{row_tag.decode()}> row {ui_index}")


# ── Byte stability (the headline regression) ────────────────────────────────

def test_replace_preserves_sibling_rows_and_declaration(tmp_path: Path) -> None:
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(OPINIONS_CP1252)
    before = p.read_bytes()

    result = st.upsert_slot_row(
        p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=10
    )
    assert result["action"] == "replaced"
    after = p.read_bytes()

    # Both sibling rows — cp1252 AND utf-8 high bytes — are byte-for-byte intact.
    assert _row_bytes(after, b"OPINION", 5) == _row_bytes(before, b"OPINION", 5)
    assert _row_bytes(after, b"OPINION", 6) == _row_bytes(before, b"OPINION", 6)
    # The raw high bytes survive (NOT transcoded): cp1252 0xE9 and utf-8 0xC3A9.
    assert b"Ren\xe9e" in after
    assert b"Andr\xc3\xa9" in after
    # The <?xml encoding="Windows-1252"?> declaration is preserved verbatim.
    assert after.startswith(b'<?xml version="1.0" encoding="Windows-1252"?>\r\n')
    # CRLF preserved (no CRLF->LF normalization): every newline is still a CRLF.
    assert b"\n" in after and after.count(b"\n") == after.count(b"\r\n")
    # The target row was updated (modifier 3 -> 9) and re-keyed to slot 10.
    row10 = _row_bytes(after, b"OPINION", 10)
    assert b'modifier="9"' in row10


def test_append_when_slot_absent_keeps_siblings(tmp_path: Path) -> None:
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(OPINIONS_CP1252)
    before = p.read_bytes()

    result = st.upsert_slot_row(
        p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=234
    )
    assert result["action"] == "appended"
    after = p.read_bytes()

    for sib in (5, 6, 10):
        assert _row_bytes(after, b"OPINION", sib) == _row_bytes(before, b"OPINION", sib)
    # New row appended, re-keyed, and still inside the root.
    assert b"<uiIndex>234</uiIndex>" in after
    assert after.rstrip().endswith(b"</MERCOPINIONS>")
    assert after.startswith(b'<?xml version="1.0" encoding="Windows-1252"?>')


def test_rekeys_fragment_to_target_slot(tmp_path: Path) -> None:
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(OPINIONS_CP1252)
    st.upsert_slot_row(p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=234)
    after = p.read_bytes()
    # The fragment's source key (220) must NOT survive — it became 234.
    assert b"<uiIndex>220</uiIndex>" not in after
    assert b"<uiIndex>234</uiIndex>" in after


# ── MercQuote (<QARRAY>/<PROFILE>) row tag derived from the fragment ─────────

def test_mercquote_profile_tag_appends(tmp_path: Path) -> None:
    p = tmp_path / "MercQuote.xml"
    p.write_bytes(QUOTE_CP1252)
    before = p.read_bytes()
    result = st.upsert_slot_row(
        p, row_text=PROFILE_FRAGMENT, id_tag="uiIndex", target_slot=234
    )
    assert result["action"] == "appended"
    assert result["row_tag"] == "PROFILE"
    after = p.read_bytes()
    assert _row_bytes(after, b"PROFILE", 5) == _row_bytes(before, b"PROFILE", 5)
    assert b"<uiIndex>234</uiIndex>" in after
    assert b"<QuoteExpTeamSpecific>1</QuoteExpTeamSpecific>" in after


# ── Duplicate key, empty table, BOM, supra-latin-1, malformed ───────────────

def test_duplicate_key_replaces_last_physical_row(tmp_path: Path) -> None:
    # Engine uses the LAST physical row on a dup key; we must replace that one.
    dup = (
        b"<MERCOPINIONS>\r\n"
        b'\t<OPINION>\r\n\t\t<uiIndex>10</uiIndex>\r\n\t\t<AnOpinion id="0" modifier="1" />\r\n\t</OPINION>\r\n'
        b'\t<OPINION>\r\n\t\t<uiIndex>10</uiIndex>\r\n\t\t<AnOpinion id="0" modifier="2" />\r\n\t</OPINION>\r\n'
        b"</MERCOPINIONS>\r\n"
    )
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(dup)
    st.upsert_slot_row(p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=10)
    after = p.read_bytes().decode("latin-1")
    blocks = re.findall(r"<OPINION>.*?</OPINION>", after, re.S)
    assert len(blocks) == 2
    # First (modifier 1) untouched; last replaced with the imported modifier 9.
    assert 'modifier="1"' in blocks[0]
    assert 'modifier="9"' in blocks[1]


def test_append_into_empty_table(tmp_path: Path) -> None:
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(b"<MERCOPINIONS>\r\n</MERCOPINIONS>\r\n")
    result = st.upsert_slot_row(
        p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=234
    )
    assert result["action"] == "appended"
    after = p.read_bytes().decode("latin-1")
    assert after.count("<OPINION>") == 1
    assert "<uiIndex>234</uiIndex>" in after
    assert after.rstrip().endswith("</MERCOPINIONS>")


def test_bom_preserved(tmp_path: Path) -> None:
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(b"\xef\xbb\xbf" + OPINIONS_CP1252)
    st.upsert_slot_row(p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=10)
    assert p.read_bytes().startswith(b"\xef\xbb\xbf<?xml")


def test_supra_latin1_in_new_row_becomes_xml_entity(tmp_path: Path) -> None:
    # A codepoint the new row carries that's outside 1-byte latin-1 (a smart
    # quote) must become a numeric XML entity, never a UnicodeEncodeError.
    frag = (
        "<OPINION>\n\t<uiIndex>220</uiIndex>\n"
        "\t<zNickname>O’Brien</zNickname>\n</OPINION>\n"
    )
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(OPINIONS_CP1252)
    st.upsert_slot_row(p, row_text=frag, id_tag="uiIndex", target_slot=10)
    after = p.read_bytes()
    assert b"&#8217;" in after
    # And the cp1252 sibling is still intact alongside it.
    assert b"Ren\xe9e" in after


def test_malformed_fragment_raises(tmp_path: Path) -> None:
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(OPINIONS_CP1252)
    with pytest.raises(st.SlotTableError):
        st.upsert_slot_row(p, row_text="   not xml   ", id_tag="uiIndex", target_slot=10)


def test_fragment_missing_id_raises(tmp_path: Path) -> None:
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(OPINIONS_CP1252)
    with pytest.raises(st.SlotTableError):
        st.upsert_slot_row(
            p, row_text="<OPINION>\n\t<Opinion0>1</Opinion0>\n</OPINION>\n",
            id_tag="uiIndex", target_slot=10,
        )


def test_no_root_close_raises(tmp_path: Path) -> None:
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(b"garbage with no closing tag")
    with pytest.raises(st.SlotTableError):
        st.upsert_slot_row(p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=234)


# ── Adversarial-review regressions ──────────────────────────────────────────

def test_new_row_accent_safe_in_utf8_target(tmp_path: Path) -> None:
    """An accented value in the IMPORTED row must be written as an ASCII numeric
    entity, not a lone cp1252 high byte — otherwise a utf-8-declared target file
    becomes invalid utf-8 and the engine's encoding-aware expat fails the whole
    table load at boot."""
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
        b"<MERCOPINIONS>\r\n"
        b"\t<OPINION>\r\n\t\t<uiIndex>5</uiIndex>\r\n\t\t<zNickname>Bob</zNickname>\r\n\t</OPINION>\r\n"
        b"</MERCOPINIONS>\r\n"
    )
    frag = "<OPINION>\n\t<uiIndex>220</uiIndex>\n\t<zNickname>Renée</zNickname>\n</OPINION>\n"
    st.upsert_slot_row(p, row_text=frag, id_tag="uiIndex", target_slot=10)
    after = p.read_bytes()
    after.decode("utf-8")  # must NOT raise — no invalid lone high byte
    assert b"&#233;" in after          # é authored as a numeric entity...
    assert b"\xe9" not in after        # ...never a raw cp1252 byte


def test_new_row_strips_illegal_control_chars(tmp_path: Path) -> None:
    """A hand-crafted .wmerc row fragment with an XML-1.0-illegal C0 control char
    (e.g. 0x01) must be STRIPPED, not spliced verbatim -- a raw control byte in a
    cp1252/no-decl table bricks the engine's whole-table load at boot (XML forbids
    these even as numeric entities). Mirrors the Backgrounds import-splice guard;
    regression for the pre-push meta-review."""
    frag = (
        "<OPINION>\n\t<uiIndex>220</uiIndex>\n"
        "\t<zNickname>Ba\x01d</zNickname>\n</OPINION>\n"
    )
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(OPINIONS_CP1252)
    st.upsert_slot_row(p, row_text=frag, id_tag="uiIndex", target_slot=10)
    after = p.read_bytes()
    assert b"\x01" not in after          # control byte stripped, not written raw
    assert b"Bad" in after               # the rest of the value is intact
    assert b"Ren\xe9e" in after          # cp1252 sibling row preserved byte-for-byte


def test_commented_out_duplicate_is_not_shadowed(tmp_path: Path) -> None:
    """A commented-out override row with the same uiIndex must NOT capture the
    upsert (expat ignores comments; the active row is the engine's winner)."""
    data = (
        b"<MERCOPINIONS>\r\n"
        b'\t<OPINION>\r\n\t\t<uiIndex>10</uiIndex>\r\n\t\t<AnOpinion id="0" modifier="3" />\r\n\t</OPINION>\r\n'
        b'\t<!-- <OPINION><uiIndex>10</uiIndex><AnOpinion id="0" modifier="99" /></OPINION> -->\r\n'
        b"</MERCOPINIONS>\r\n"
    )
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(data)
    result = st.upsert_slot_row(
        p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=10
    )
    assert result["action"] == "replaced"
    after = p.read_bytes()
    # The comment is preserved verbatim...
    assert b'<!-- <OPINION><uiIndex>10</uiIndex><AnOpinion id="0" modifier="99" /></OPINION> -->' in after
    # ...and the ACTIVE row (outside any comment) is the one that got updated.
    no_comments = re.sub(rb"<!--.*?-->", b"", after, flags=re.S)
    assert b'modifier="9"' in no_comments    # imported value landed on the active row
    assert b'modifier="3"' not in no_comments  # old active value replaced
    assert b'modifier="99"' not in no_comments  # 99 lived only in the comment


def test_first_insert_with_trailing_content_after_root_close(tmp_path: Path) -> None:
    """An empty table with content after the root close still gets its first row
    spliced before </MERCOPINIONS> (the close is found via the masked scan)."""
    p = tmp_path / "MercOpinions.xml"
    p.write_bytes(b"<MERCOPINIONS>\r\n</MERCOPINIONS>\r\n<!-- trailing note -->\r\n")
    result = st.upsert_slot_row(
        p, row_text=OPINION_FRAGMENT, id_tag="uiIndex", target_slot=234
    )
    assert result["action"] == "appended"
    after = p.read_bytes().decode("latin-1")
    assert after.index("<uiIndex>234") < after.index("</MERCOPINIONS>")
    assert after.rstrip().endswith("<!-- trailing note -->")
