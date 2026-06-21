"""Shared XML byte-splice helpers — edit one record's bytes, never reflow.

Generalizes the proven private helpers from `inject/backgrounds_xml.py` over an
arbitrary record tag (ITEM / WEAPON / ARMOUR / …) so the Items editor's writers
share one implementation. Read via latin-1 (total 1:1 byte map); write via
latin-1 + xmlcharrefreplace so every untouched byte round-trips verbatim and any
authored high codepoint becomes a numeric XML entity (valid under the engine's
UTF-8-default expat). See `reference_ja2_xml_encoding`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._atomic_xml import write_bytes_atomic

_XML_ILLEGAL_C0 = frozenset(set(range(0x20)) - {0x09, 0x0A, 0x0D})


def read_text(path: Path) -> tuple[str, bool, str]:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("latin-1")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text, had_bom, eol


def write_text(path: Path, text: str, had_bom: bool) -> None:
    body = text.encode("latin-1", errors="xmlcharrefreplace")
    if had_bom:
        body = b"\xef\xbb\xbf" + body
    write_bytes_atomic(path, body)


def esc(s: str) -> str:
    s = "".join(c for c in s if ord(c) not in _XML_ILLEGAL_C0)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(c if ord(c) < 0x80 else f"&#{ord(c)};" for c in s)


@dataclass
class RecordBlock:
    ui_index: Optional[int]
    start: int
    end: int
    text: str


_UIINDEX_RE = re.compile(r"<uiIndex>\s*(-?\d+)")


def find_blocks(text: str, record_tag: str) -> list[RecordBlock]:
    pat = re.compile(rf"<{re.escape(record_tag)}>.*?</{re.escape(record_tag)}>", re.S)
    out: list[RecordBlock] = []
    for m in pat.finditer(text):
        block = m.group(0)
        idm = _UIINDEX_RE.search(block)
        ui = int(idm.group(1)) if idm else None
        out.append(RecordBlock(ui_index=ui, start=m.start(), end=m.end(), text=block))
    return out


def _tag_pat(tag: str) -> re.Pattern[str]:
    return re.compile(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", re.S)


def block_text_child(block: str, tag: str) -> Optional[str]:
    m = _tag_pat(tag).search(block)
    return m.group(1) if m else None


def block_int(block: str, tag: str) -> Optional[int]:
    raw = block_text_child(block, tag)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def set_child(block: str, tag: str, inner: str, eol: str) -> str:
    """Replace <tag>…</tag> in place; if absent, insert before the record close."""
    pat = re.compile(rf"<{re.escape(tag)}>.*?</{re.escape(tag)}>", re.S)
    if pat.search(block):
        return pat.sub(lambda _m: f"<{tag}>{inner}</{tag}>", block, count=1)
    m = re.search(r"(?:\r\n|\r|\n)[ \t]*</[A-Za-z_]+>\s*$", block)
    if m is None:
        idx = block.rfind("</")
        return block[:idx] + f"<{tag}>{inner}</{tag}>" + block[idx:]
    return block[: m.start()] + eol + "\t\t" + f"<{tag}>{inner}</{tag}>" + block[m.start() :]
