from __future__ import annotations
from pathlib import Path
from mercwizard_core.inject import _xml_splice as sp

SAMPLE = (
    "﻿<ITEMLIST>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szItemName>Nada</szItemName>\r\n\t\t<usPrice>0</usPrice>\r\n\t</ITEM>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szItemName>Glock 17</szItemName>\r\n\t\t<usPrice>225</usPrice>\r\n\t</ITEM>\r\n"
    "</ITEMLIST>"
)

def test_read_text_strips_bom_keeps_eol(tmp_path: Path) -> None:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    text, had_bom, eol = sp.read_text(p)
    assert had_bom is True
    assert eol == "\r\n"
    assert not text.startswith("﻿")

def test_find_blocks_and_index(tmp_path: Path) -> None:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    text, _b, _e = sp.read_text(p)
    blocks = sp.find_blocks(text, "ITEM")
    assert [b.ui_index for b in blocks] == [0, 1]
    assert sp.block_int(blocks[1].text, "usPrice") == 225
    assert sp.block_text_child(blocks[1].text, "szItemName") == "Glock 17"

def test_set_child_replaces_in_place(tmp_path: Path) -> None:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    text, had_bom, eol = sp.read_text(p)
    blocks = sp.find_blocks(text, "ITEM")
    b1 = blocks[1]
    new_block = sp.set_child(b1.text, "usPrice", "999", eol)
    new_text = text[: b1.start] + new_block + text[b1.end :]
    sp.write_text(p, new_text, had_bom)
    # Block 0 untouched byte-for-byte; only block 1 price changed; BOM preserved.
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    out = raw[3:].decode("utf-8")
    assert "<usPrice>999</usPrice>" in out
    assert "<szItemName>Nada</szItemName>" in out
    assert out.count("<usPrice>0</usPrice>") == 1  # block 0's price intact

def test_esc_entitizes_non_ascii_and_strips_c0() -> None:
    assert sp.esc("a&b<c>") == "a&amp;b&lt;c&gt;"
    assert sp.esc("café") == "caf&#233;"
    assert sp.esc("x\x07y") == "xy"  # bell (C0) stripped
