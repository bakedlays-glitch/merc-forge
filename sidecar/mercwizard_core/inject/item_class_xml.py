"""Generic sister-file (Weapons/Armours/Magazines/Explosives) byte-splice editor.

Each record is a flat list of int children keyed by `<uiIndex>` == the item's
`ubClassIndex`. Every editable field already exists in every record (full
templates), so an edit only ever REPLACES a child value in place — no insert /
remove. Mirrors the Items.xml writer's discipline; one generic module instead of
four near-identical ones.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import _xml_splice as sp


class ClassRowError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _find_row(text: str, record_tag: str, class_index: int) -> Optional[sp.RecordBlock]:
    for b in sp.find_blocks(text, record_tag):
        if b.ui_index == class_index:
            return b
    return None


def read_row(path: Path, record_tag: str, class_index: int) -> Optional[dict[str, int]]:
    if not path or not path.exists():
        return None
    text, _bom, _eol = sp.read_text(path)
    block = _find_row(text, record_tag, class_index)
    if block is None:
        return None
    out: dict[str, int] = {}
    for m in re.finditer(r"<([A-Za-z_][\w]*)>\s*(-?\d+)\s*</\1>", block.text):
        out[m.group(1)] = int(m.group(2))
    return out


def edit_row(path: Path, *, record_tag: str, class_index: int,
             fields: dict[str, int]) -> dict:
    text, had_bom, eol = sp.read_text(path)
    block = _find_row(text, record_tag, class_index)
    if block is None:
        raise ClassRowError("ROW_NOT_FOUND",
                            f"No {record_tag} row with class index {class_index}.")
    new_block = block.text
    for key, value in fields.items():
        new_block = sp.set_child(new_block, key, str(value), eol)
    new_text = text[: block.start] + new_block + text[block.end :]
    sp.write_text(path, new_text, had_bom)
    return {"class_index": class_index, "record_tag": record_tag}
