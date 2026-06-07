"""Slot-keyed extra-table reader/writer — byte-level splice, never full reflow.

Covers the two genuinely per-merc-slot mod tables that a ``.wmerc`` bundle
carries as ``table_rows/*`` fragments: **MercOpinions.xml** and **MercQuote.xml**.
(FaceGear / Backgrounds / CivGroupNames are intercepted upstream — they are NOT
slot-keyed; see ``bundle/import_.py``.)

WHY BYTE-SPLICE (not ET/lxml reserialize):
    The previous importer round-tripped the WHOLE target file through
    ``ET.tostring(encoding="unicode").encode("utf-8")``. When the target file
    declared ``encoding="Windows-1252"`` (common in localized mods — accented
    merc nicknames), that rewrite silently:
      - dropped the ``<?xml ... encoding="Windows-1252"?>`` declaration,
      - transcoded every cp1252 high byte (é == 0xE9) to UTF-8 (0xC3 0xA9),
      - normalized CRLF -> LF,
    mutating EVERY OTHER merc's row in the shared file. The JA2 engine parses
    these with expat (``XML_Opinions.cpp`` / ``XML_Qarray.cpp``) honoring the
    declared encoding, so pulling the declaration out from under it garbles the
    sibling rows. So we edit ONLY the target slot's ``<row>`` block bytes and
    leave every other byte untouched — mirroring the FaceGear row-append and
    Backgrounds byte-splice discipline (``inject/backgrounds_xml`` /
    ``inject/_atomic_xml.write_bytes_atomic``).

ENGINE TRUTH (``XML_Opinions.cpp``, ``XML_Qarray.cpp``):
    - Both tables store BY ``<uiIndex>`` VALUE, not physical position
      (``tempProfiles[curIndex].bMercOpinion = ...`` / ``curArray[uiIndex] = ...``).
      So row order is irrelevant: replace-in-place is safe, and on a duplicate
      uiIndex the LAST physical row wins (we replace the last match).
    - Row elements differ by table: MercOpinions = ``<OPINION>`` under
      ``<MERCOPINIONS>``; MercQuote = ``<PROFILE>`` under ``<QARRAY>``. We derive
      the row tag from the bundled fragment rather than hardcoding it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._atomic_xml import write_bytes_atomic


class SlotTableError(Exception):
    """Raised for malformed-fragment / unspliceable-target failures.

    Caught by the importer and surfaced as a ``partial_failures`` entry — it
    NEVER aborts (and rolls back) the whole merc import, exactly like the old
    ``ET.ParseError`` path it replaces.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── Low-level text helpers (latin-1, 1:1 byte<->codepoint) ──────────────────

def _read_text(path: Path) -> tuple[str, bool, str]:
    """Return (text, had_bom, eol). `text` is BOM-stripped; line endings kept.

    Decoded as latin-1: a total, 1:1 byte<->codepoint map that never raises and
    re-encodes byte-for-byte. These tables have no guaranteed XML declaration and
    localized mods routinely ship Windows-1252 high bytes (é, ñ, …); a utf-8
    decode would crash on those. The splice writer only ever rewrites the target
    block, so every other byte (incl. utf-8 multi-byte runs, preserved as their
    raw bytes) round-trips verbatim via `_write_text`.
    """
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("latin-1")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text, had_bom, eol


def _write_text(path: Path, text: str, had_bom: bool) -> None:
    """Encode latin-1 (mirror of `_read_text`) + atomic replace.

    `xmlcharrefreplace` turns any codepoint outside the 1-byte range (e.g. a
    smart quote the rewritten row carried) into a numeric XML entity the engine's
    expat parser decodes — never a UnicodeEncodeError. The BOM is written as raw
    bytes (U+FEFF can't be latin-1 encoded).
    """
    body = text.encode("latin-1", errors="xmlcharrefreplace")
    if had_bom:
        body = b"\xef\xbb\xbf" + body
    write_bytes_atomic(path, body)


# XML 1.0 allows only tab/LF/CR below 0x20; the other C0 controls are forbidden
# even as numeric character references, so they must be STRIPPED, not entitized.
_XML_ILLEGAL_C0 = frozenset(set(range(0x20)) - {0x09, 0x0A, 0x0D})


def _ascii_safe(block: str) -> str:
    """Make the spliced row safe to write under the file's declared encoding.

    Escapes every non-ASCII codepoint (>= 0x80) to a numeric XML entity so the
    row we author is pure ASCII — valid whether the file is Windows-1252 OR UTF-8
    (expat decodes the entity to the right codepoint regardless). Without it an
    accented value (e.g. a merc's ``<zNickname>``) would be a lone cp1252 high
    byte: INVALID in a utf-8-declared file, failing the WHOLE table load at boot.

    ALSO drops XML-1.0-illegal C0 control chars (< 0x20 except tab/LF/CR): XML
    forbids them even as &#NNN; references, so a raw one smuggled in a
    hand-crafted .wmerc fragment would brick the target table the same way. We
    touch only >= 0x80 and these controls; XML markup (``<``/``>``/``&``) stays
    intact, so the result is always well-formed.
    """
    return "".join(
        "" if ord(ch) in _XML_ILLEGAL_C0
        else (ch if ord(ch) < 0x80 else f"&#{ord(ch)};")
        for ch in block
    )


# ── Fragment / block parsing ────────────────────────────────────────────────

_ROW_TAG_RE = re.compile(r"\s*<([A-Za-z_][\w.\-]*)\s*>")


@dataclass
class _Block:
    id_value: Optional[int]   # None if the block's id tag is missing/unparseable
    start: int                # offset of "<row_tag>"
    end: int                  # offset just past "</row_tag>"


def _row_tag_from_fragment(row_text: str) -> str:
    """The fragment's outer element name (``OPINION`` / ``PROFILE``)."""
    m = _ROW_TAG_RE.match(row_text.lstrip("﻿"))
    if m is None:
        raise SlotTableError("INVALID_FRAGMENT", "Row fragment has no opening element.")
    return m.group(1)


def _id_re(id_tag: str) -> re.Pattern[str]:
    # Forgiving (mirrors the engine's atol/strtoul): leading sign + digits.
    return re.compile(rf"<{re.escape(id_tag)}>\s*(-?\d+)")


def _block_re(row_tag: str) -> re.Pattern[str]:
    # Rows never nest, so a non-greedy match to the first close is exact.
    return re.compile(rf"<{re.escape(row_tag)}>.*?</{re.escape(row_tag)}>", re.S)


_COMMENT_OR_CDATA = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>", re.S)


def _mask_noncontent(text: str) -> str:
    """Same-length copy with comment / CDATA spans blanked to spaces.

    Block scanning runs on this so a ``<row>`` the engine's expat would ignore
    (sitting inside a comment) can't shadow the real row and silently capture the
    upsert. Lengths are preserved, so offsets still index the REAL text.
    """
    return _COMMENT_OR_CDATA.sub(lambda m: " " * (m.end() - m.start()), text)


def _find_row_blocks(masked: str, row_tag: str, id_tag: str) -> list[_Block]:
    # `masked` has comments/CDATA blanked (see `_mask_noncontent`); the <uiIndex>
    # of a real row is live content, so it survives the masking intact, and the
    # offsets returned index the real text (same length).
    id_pat = _id_re(id_tag)
    out: list[_Block] = []
    for m in _block_re(row_tag).finditer(masked):
        idm = id_pat.search(m.group(0))
        out.append(_Block(
            id_value=int(idm.group(1)) if idm else None,
            start=m.start(),
            end=m.end(),
        ))
    return out


def _rewrite_id(row_text: str, id_tag: str, target_slot: int) -> str:
    """Re-key the fragment's OWN slot tag to `target_slot` (first occurrence).

    The row's only ``<id_tag>`` is its key; MercOpinions' ``<AnOpinion id=...>``
    uses an attribute (not an element), so it never collides.
    """
    pat = re.compile(rf"<{re.escape(id_tag)}>\s*-?\d+\s*</{re.escape(id_tag)}>")
    new, n = pat.subn(f"<{id_tag}>{target_slot}</{id_tag}>", row_text, count=1)
    if n == 0:
        raise SlotTableError(
            "MISSING_ID",
            f"Row fragment has no <{id_tag}> to re-key to slot {target_slot}.",
        )
    return new


def _reindent_foreign_block(row_text: str, eol: str) -> str:
    r"""Re-indent a serialized row fragment to nest one level under the root.

    The exporter emits the block via ``ET.tostring`` after ``ET.indent("\t")``:
    LF endings, ``<row>`` at column 0, children at one tab. To match existing
    file entries (row at one tab, children at two) we add one leading tab to
    every STRUCTURAL line — first non-space char ``<`` — except the opening row
    line, and convert line endings to the file's ``eol``.

    Keying on a leading ``<`` (not "every non-first line") leaves continuation
    lines of any multi-line text child untouched (well-formed XML text can't
    contain a raw ``<``). The returned block starts at column 0; the splicer
    supplies the row's own leading tab.
    """
    lines = row_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n").split("\n")
    out = [
        ("\t" + ln) if (i != 0 and ln.lstrip().startswith("<")) else ln
        for i, ln in enumerate(lines)
    ]
    return eol.join(out)


def _insert_before_root_close(text: str, masked: str, block: str, eol: str) -> str:
    """Insert `block` (one tab in) before the document's final closing tag.

    Scans `masked` (comments/CDATA blanked) so a ``</…>`` inside a comment is
    never mistaken for the root close, and takes the LAST closing tag so trailing
    content after the root close doesn't defeat the insert.
    """
    matches = list(re.finditer(r"(?:\r\n|\r|\n)?[ \t]*</[A-Za-z_][\w.\-]*>", masked))
    if not matches:
        raise SlotTableError(
            "NO_ROOT_CLOSE",
            "Target table has no row of this type and no recognizable root "
            "closing tag — refusing to emit malformed XML.",
        )
    pos = matches[-1].start()
    return text[:pos] + eol + "\t" + block + text[pos:]


# ── Public API ───────────────────────────────────────────────────────────────

def upsert_slot_row(
    path: Path, *, row_text: str, id_tag: str, target_slot: int,
) -> dict:
    """Upsert one slot-keyed row into a slot table, losslessly.

    The bundled ``row_text`` fragment is re-keyed to ``target_slot`` and either
    REPLACES the existing row with that key (byte-for-byte in place; last match
    wins on a duplicate key) or is APPENDED after the last existing row of its
    type (or before the root close if the table is empty). Every other byte of
    the file — the ``<?xml?>`` declaration, sibling rows' cp1252/utf-8 bytes,
    CRLF line endings, the BOM — is preserved exactly. Atomic write.

    Returns ``{"action": "replaced"|"appended", "row_tag": str, "target_slot": int}``.
    Raises ``SlotTableError`` on a malformed fragment or an unspliceable target.
    """
    row_tag = _row_tag_from_fragment(row_text)
    rewritten = _rewrite_id(row_text, id_tag, target_slot)

    text, had_bom, eol = _read_text(path)
    # The spliced row is normalized to pure ASCII (non-ASCII → numeric XML
    # entities) so it is valid under ANY declared encoding. Sibling rows are
    # never re-encoded — they keep their original bytes via the latin-1
    # round-trip.
    block = _ascii_safe(_reindent_foreign_block(rewritten, eol))
    # Scan a comment/CDATA-masked copy so a commented-out <row> the engine
    # ignores can't shadow the real one; offsets still index the real text.
    masked = _mask_noncontent(text)
    blocks = _find_row_blocks(masked, row_tag, id_tag)
    matches = [b for b in blocks if b.id_value == target_slot]

    if matches:
        target = matches[-1]  # engine uses the last physical row on a dup key
        new_text = text[: target.start] + block + text[target.end:]
        action = "replaced"
    elif blocks:
        last = blocks[-1]
        new_text = text[: last.end] + eol + "\t" + block + text[last.end:]
        action = "appended"
    else:
        new_text = _insert_before_root_close(text, masked, block, eol)
        action = "appended"

    _write_text(path, new_text, had_bom)
    return {"action": action, "row_tag": row_tag, "target_slot": target_slot}
