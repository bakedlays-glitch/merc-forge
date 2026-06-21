# sidecar/tests/test_items_xml.py
from __future__ import annotations
from pathlib import Path
import pytest
from mercwizard_core.inject import items_xml as ix

SAMPLE = (
    "<ITEMLIST>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szItemName>Nada</szItemName>\r\n"
    "\t\t<usItemClass>128</usItemClass>\r\n\t\t<ubClassIndex>0</ubClassIndex>\r\n"
    "\t\t<usPrice>0</usPrice>\r\n\t\t<ubCoolness>0</ubCoolness>\r\n"
    "\t\t<ubGraphicType>0</ubGraphicType>\r\n\t\t<ubGraphicNum>0</ubGraphicNum>\r\n\t</ITEM>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szItemName>Glock 17</szItemName>\r\n"
    "\t\t<szItemDesc>A pistol.</szItemDesc>\r\n\t\t<usItemClass>2</usItemClass>\r\n"
    "\t\t<ubClassIndex>1</ubClassIndex>\r\n\t\t<usPrice>225</usPrice>\r\n\t\t<ubCoolness>3</ubCoolness>\r\n"
    "\t\t<ubGraphicType>0</ubGraphicType>\r\n\t\t<ubGraphicNum>5</ubGraphicNum>\r\n\t</ITEM>\r\n"
    "</ITEMLIST>"
)

@pytest.fixture
def items_file(tmp_path: Path) -> Path:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    return p

def test_read_index(items_file: Path) -> None:
    rows = ix.read_index(items_file)
    assert [r.ui_index for r in rows] == [0, 1]
    g = rows[1]
    assert g.name == "Glock 17" and g.item_class == 2 and g.class_index == 1
    assert g.price == 225 and g.graphic_type == 0 and g.graphic_num == 5

def test_read_item(items_file: Path) -> None:
    d = ix.read_item(items_file, 1)
    assert d["strings"]["szItemName"] == "Glock 17"
    assert d["ints"]["usPrice"] == 225
    assert d["ints"]["ubGraphicNum"] == 5

def test_edit_item_changes_only_target(items_file: Path) -> None:
    pre = items_file.read_bytes()
    i0_start = pre.index(b"<ITEM>")
    i0_end = pre.index(b"</ITEM>") + len(b"</ITEM>")
    item0_before = pre[i0_start:i0_end]

    ix.edit_item(items_file, ui_index=1, strings={"szItemName": "Glock 18"},
                 ints={"usPrice": 300, "ubGraphicNum": 9})
    out = items_file.read_bytes().decode("utf-8")
    assert "<szItemName>Glock 18</szItemName>" in out
    assert "<usPrice>300</usPrice>" in out
    assert "<ubGraphicNum>9</ubGraphicNum>" in out
    # Item 0 (Nada) untouched.
    assert "<szItemName>Nada</szItemName>" in out
    # Item 1's class index never rewritten by a common-field edit.
    assert out.count("<ubClassIndex>1</ubClassIndex>") == 1
    # Byte-splice contract: Item 0's raw bytes must be verbatim after the edit.
    post = items_file.read_bytes()
    item0_after = post[post.index(b"<ITEM>"):post.index(b"</ITEM>") + len(b"</ITEM>")]
    assert item0_before == item0_after

def test_edit_item_escapes_and_refuses_template(items_file: Path) -> None:
    ix.edit_item(items_file, ui_index=1, strings={"szItemName": "A & B"}, ints={})
    out = items_file.read_bytes().decode("utf-8")
    assert "<szItemName>A &amp; B</szItemName>" in out
    # read_item must decode &amp; back to "&".
    d = ix.read_item(items_file, 1)
    assert d["strings"]["szItemName"] == "A & B"
    with pytest.raises(ix.ItemError):
        ix.edit_item(items_file, ui_index=0, strings={"szItemName": "x"}, ints={})


# ── Numeric-entity round-trip (Fix 1) ────────────────────────────────────────

SAMPLE_NUMERIC_ENTITY = (
    "<ITEMLIST>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szItemName>Nada</szItemName>\r\n"
    "\t\t<usItemClass>128</usItemClass>\r\n\t\t<ubClassIndex>0</ubClassIndex>\r\n"
    "\t\t<usPrice>0</usPrice>\r\n\t\t<ubCoolness>0</ubCoolness>\r\n"
    "\t\t<ubGraphicType>0</ubGraphicType>\r\n\t\t<ubGraphicNum>0</ubGraphicNum>\r\n\t</ITEM>\r\n"
    # Item 1: name stored as numeric XML entity for é (U+00E9 = decimal 233).
    "\t<ITEM>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szItemName>Caf&#233;</szItemName>\r\n"
    "\t\t<usItemClass>2</usItemClass>\r\n\t\t<ubClassIndex>1</ubClassIndex>\r\n"
    "\t\t<usPrice>100</usPrice>\r\n\t\t<ubCoolness>5</ubCoolness>\r\n"
    "\t\t<ubGraphicType>0</ubGraphicType>\r\n\t\t<ubGraphicNum>1</ubGraphicNum>\r\n\t</ITEM>\r\n"
    "</ITEMLIST>"
)


@pytest.fixture
def numeric_entity_file(tmp_path: Path) -> Path:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE_NUMERIC_ENTITY.encode("utf-8"))
    return p


def test_read_item_decodes_numeric_entity(numeric_entity_file: Path) -> None:
    """read_item must return real Unicode, not the raw &#233; entity text."""
    d = ix.read_item(numeric_entity_file, 1)
    assert d["strings"]["szItemName"] == "Café", (
        f"Expected 'Café' but got {d['strings']['szItemName']!r} — "
        "numeric entity not decoded"
    )


def test_roundtrip_no_double_escape(numeric_entity_file: Path) -> None:
    """Read → write the name unchanged; file must still contain &#233; not &amp;#233;."""
    # Read the decoded name.
    d = ix.read_item(numeric_entity_file, 1)
    name = d["strings"]["szItemName"]
    assert name == "Café"

    # Write it back unchanged.
    ix.edit_item(numeric_entity_file, ui_index=1, strings={"szItemName": name}, ints={})

    raw = numeric_entity_file.read_bytes().decode("utf-8")
    # The writer must have re-escaped é as a numeric entity, not double-escaped the &.
    assert "Caf&#233;" in raw or "Caf&#xE9;" in raw or "Café" in raw, (
        "Expected é to be round-tripped as a numeric/literal entity"
    )
    assert "Caf&amp;#" not in raw, (
        f"Double-escaping detected: the & in &#233; was re-escaped. Excerpt: "
        f"{raw[max(0, raw.find('Caf')-5):raw.find('Caf')+20]!r}"
    )
