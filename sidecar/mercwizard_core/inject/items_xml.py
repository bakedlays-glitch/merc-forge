"""Items.xml surgical reader/writer — byte-splice, never reflow.

Items.xml is a 1854-record master table (~150 children each). The editor only
ever touches a curated set of common children (name/desc/price/graphic/…); every
other byte — including the ~135 untouched columns — round-trips verbatim. See
`reference_ja2_xml_encoding` and the Backgrounds writer this mirrors.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import items_schema as schema
from . import _xml_splice as sp


class ItemError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ItemSummary:
    ui_index: int
    name: str
    item_class: int
    price: int
    coolness: int
    graphic_type: int
    graphic_num: int
    class_index: int


def _require_single(blocks: list[sp.RecordBlock], ui_index: int) -> sp.RecordBlock:
    matches = [b for b in blocks if b.ui_index == ui_index]
    if not matches:
        raise ItemError("ITEM_NOT_FOUND", f"No item with uiIndex {ui_index}.")
    if len(matches) > 1:
        raise ItemError("DUPLICATE_INDEX",
                        f"Items.xml has {len(matches)} entries with uiIndex {ui_index}.")
    return matches[0]


def read_index(path: Path) -> list[ItemSummary]:
    text, _bom, _eol = sp.read_text(path)
    out: list[ItemSummary] = []
    for b in sp.find_blocks(text, "ITEM"):
        if b.ui_index is None:
            continue
        out.append(ItemSummary(
            ui_index=b.ui_index,
            name=(sp.block_text_child(b.text, "szItemName") or "").strip(),
            item_class=sp.block_int(b.text, "usItemClass") or 0,
            price=sp.block_int(b.text, "usPrice") or 0,
            coolness=sp.block_int(b.text, "ubCoolness") or 0,
            graphic_type=sp.block_int(b.text, "ubGraphicType") or 0,
            graphic_num=sp.block_int(b.text, "ubGraphicNum") or 0,
            class_index=sp.block_int(b.text, "ubClassIndex") or 0,
        ))
    return out


def read_item(path: Path, ui_index: int) -> dict:
    text, _bom, _eol = sp.read_text(path)
    target = _require_single(sp.find_blocks(text, "ITEM"), ui_index)
    strings: dict[str, str] = {}
    ints: dict[str, int] = {}
    for key in schema.COMMON_STR_KEYS:
        raw = sp.block_text_child(target.text, key)
        if raw is not None:
            # unescape all XML entities (named + numeric) so non-ASCII
            # characters stored as &#NNN; are returned as real Unicode,
            # preventing progressive re-escaping on successive saves.
            strings[key] = html.unescape(raw)
    for key in schema.COMMON_INT_KEYS:
        v = sp.block_int(target.text, key)
        if v is not None:
            ints[key] = v
    # Always surface the class index so the route can resolve sister stats.
    ci = sp.block_int(target.text, "ubClassIndex")
    return {"ui_index": ui_index, "strings": strings, "ints": ints,
            "class_index": ci if ci is not None else 0}


def edit_item(path: Path, *, ui_index: int, strings: dict[str, str],
              ints: dict[str, int]) -> dict:
    if ui_index == schema.TEMPLATE_INDEX:
        raise ItemError("TEMPLATE_PROTECTED",
                        "uiIndex 0 is the template row and can't be edited.")
    text, had_bom, eol = sp.read_text(path)
    blocks = sp.find_blocks(text, "ITEM")
    target = _require_single(blocks, ui_index)

    block = target.text
    for key, value in strings.items():
        if key not in schema.COMMON_STR_KEYS:
            raise ItemError("UNKNOWN_FIELD", f"Unknown string field '{key}'.")
        block = sp.set_child(block, key, sp.esc(value), eol)
    for key, value in ints.items():
        if key not in schema.COMMON_INT_KEYS:
            raise ItemError("UNKNOWN_FIELD", f"Unknown int field '{key}'.")
        block = sp.set_child(block, key, str(value), eol)

    new_text = text[: target.start] + block + text[target.end :]
    sp.write_text(path, new_text, had_bom)
    return {"ui_index": ui_index}
