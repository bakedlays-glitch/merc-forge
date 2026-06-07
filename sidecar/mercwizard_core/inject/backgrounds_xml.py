"""Backgrounds.xml surgical reader/writer — byte-level splice, never full reflow.

WHY BYTE-SPLICE (not lxml/ET reserialize):
    Backgrounds.xml is CRLF + tab-indented, has no XML declaration, and ~70% of
    its entries carry multi-line `<szDescription>` text. Round-tripping the whole
    file through lxml `pretty_print` or `ET.indent`+`tostring` normalizes CRLF→LF
    and reflows every entry — a ~240 KB no-semantic-change diff that also risks
    mangling the multi-line descriptions. So writes edit ONLY the target
    `<BACKGROUND>` block's bytes and leave every other byte untouched, mirroring
    the FaceGear row-append discipline (`inject/_atomic_xml.write_bytes_atomic`).
    lxml is used for READING/validation only.

ENGINE TRUTH (see memory `reference_ja2_backgrounds_engine`, `XML_Background.cpp`):
    - `zBackground[NUM_BACKGROUND=500]`; `uiIndex` is the direct array index; the
      loader does `if (uiIndex < 500)` so ids >= 500 are SILENTLY DROPPED. Valid
      editable ids are 1..499 (0 = the template row; usBackground=0 = none).
    - `num_found_background = <the LAST PHYSICAL entry's uiIndex>` (assigned on
      every `</BACKGROUND>`), and the IMP creation picker enumerates
      `0..num_found_background`. So the physical position of the LAST entry is
      load-bearing: we never reorder existing entries except via the explicit
      `set_imp_threshold` (which moves one entry to physical-last on purpose).
      The live canonical file's tail is uiIndex 198 while its max id is 356, so
      ids 199..356 already aren't IMP-pickable — don't "fix" that silently.
    - ~12 flag fields (0/1) + ~68 clamped numeric fields + nested
      `<drugtypes>`/`<drugitems>` lists. Anything not in `backgrounds_schema`
      (unknown mod columns, the nested drug lists) is preserved verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree

from .. import backgrounds_schema as schema
from ._atomic_xml import write_bytes_atomic


class BackgroundError(Exception):
    """Raised for writer-level validation failures (mapped to HTTP by the route)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── Low-level text helpers ──────────────────────────────────────────────────

_BLOCK_RE = re.compile(r"<BACKGROUND>.*?</BACKGROUND>", re.S)
_UIINDEX_RE = re.compile(r"<uiIndex>\s*(-?\d+)")  # forgiving, mirrors engine atol


def _read_text(path: Path) -> tuple[str, bool, str]:
    """Return (text, had_bom, eol). `text` is BOM-stripped; line endings kept."""
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    # Decode as latin-1: a total, 1:1 byte<->codepoint map that never raises and
    # re-encodes byte-for-byte. Backgrounds.xml has no XML declaration and
    # localized mods routinely ship Windows-1252 high bytes (é, ñ, …); a utf-8
    # decode would crash the editor on those. The byte-splice writer only ever
    # rewrites the target block, so every other byte (incl. utf-8 multi-byte
    # runs, preserved as their raw bytes) round-trips verbatim via _write_text.
    text = raw.decode("latin-1")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text, had_bom, eol


def _write_text(path: Path, text: str, had_bom: bool) -> None:
    # Encode latin-1 to mirror _read_text so every untouched byte round-trips
    # exactly. `xmlcharrefreplace` turns any codepoint the user typed that's
    # outside the 1-byte range (e.g. a smart quote) into a numeric XML entity
    # the engine's parser decodes — never a UnicodeEncodeError. The BOM is
    # written as raw bytes (U+FEFF can't be latin-1 encoded).
    body = text.encode("latin-1", errors="xmlcharrefreplace")
    if had_bom:
        body = b"\xef\xbb\xbf" + body
    write_bytes_atomic(path, body)


@dataclass
class _Block:
    ui_index: Optional[int]   # None if the block's uiIndex is missing/unparseable
    start: int                # offset of "<BACKGROUND>"
    end: int                  # offset just past "</BACKGROUND>"
    text: str                 # the block substring [start:end] (no leading indent)


def _find_blocks(text: str) -> list[_Block]:
    out: list[_Block] = []
    for m in _BLOCK_RE.finditer(text):
        block = m.group(0)
        idm = _UIINDEX_RE.search(block)
        ui = int(idm.group(1)) if idm else None
        out.append(_Block(ui_index=ui, start=m.start(), end=m.end(), text=block))
    return out


def _leading_indent_start(text: str, start: int) -> int:
    """Walk back over the run of spaces/tabs immediately before `start`."""
    i = start
    while i > 0 and text[i - 1] in " \t":
        i -= 1
    return i


def _esc(s: str) -> str:
    """Escape XML text content (element text, not attributes).

    Beyond the structural & < >, every non-ASCII codepoint (>= 0x80) is emitted
    as a numeric character reference (&#NNNN;). Backgrounds.xml ships with no
    <?xml?> declaration, so the engine's expat parser defaults to UTF-8; a raw
    Latin-1 high byte (é=0xE9, ñ, …) is invalid UTF-8 and would fail the WHOLE
    file's load at boot. A numeric entity is pure ASCII and decodes to the right
    glyph under any encoding. Only values the writer AUTHORS pass through here —
    untouched entries keep their byte-faithful latin-1 round-trip (raw high bytes
    included). Mirrors slot_table_xml's _ascii_safe.
    """
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(c if ord(c) < 0x80 else f"&#{ord(c)};" for c in s)


def _esc_multiline(s: str, eol: str) -> str:
    """Escape + normalize newlines to the file's EOL (for szDescription)."""
    s = _esc(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", eol)
    return s


# XML 1.0 allows only tab/LF/CR below 0x20; the other C0 controls are forbidden
# even as numeric character references, so they must be STRIPPED, not entitized.
_XML_ILLEGAL_C0 = frozenset(set(range(0x20)) - {0x09, 0x0A, 0x0D})


def _ascii_safe(block: str) -> str:
    """Make an ALREADY-well-formed fragment safe to splice into the target.

    Entitizes every non-ASCII codepoint (>= 0x80) to a numeric XML character
    reference (markup </>/& stays untouched), AND drops XML-1.0-illegal C0
    control chars (everything < 0x20 except tab/LF/CR).

    For the .wmerc import splice (`upsert_background_block`): the incoming
    <BACKGROUND> fragment is already valid XML, but can carry a RAW high codepoint
    (é=U+00E9, from `ET.tostring(encoding="unicode")` over a UTF-8 source). Because
    that codepoint IS latin-1-encodable, `_write_text`'s xmlcharrefreplace won't
    catch it — leaving a lone 0xE9 byte in the no-<?xml?> target, which makes the
    engine's UTF-8-default expat fail the WHOLE file's load at boot. A numeric
    entity is pure ASCII and valid under any encoding.

    C0 controls get STRIPPED, not entitized: XML 1.0 forbids them even as &#NNN;
    references, so a raw one (smuggled in a hand-crafted bundle, past the editor's
    input validator) would brick the target the same way. Distinct from `_esc`,
    which ALSO escapes structural &/</> (right for AUTHORED text, wrong for an
    already-escaped fragment). Mirrors slot_table_xml's `_ascii_safe`.
    """
    return "".join(
        "" if ord(c) in _XML_ILLEGAL_C0
        else (c if ord(c) < 0x80 else f"&#{ord(c)};")
        for c in block
    )


# ── Block field mutation (operates on a single block's string) ──────────────

def _tag_pat(tag: str) -> re.Pattern[str]:
    return re.compile(rf"<{re.escape(tag)}>.*?</{re.escape(tag)}>", re.S)


def _set_child(block: str, tag: str, inner: str, eol: str) -> str:
    """Set <tag>inner</tag>: replace existing in place, else insert before close."""
    pat = _tag_pat(tag)
    if pat.search(block):
        return pat.sub(lambda _m: f"<{tag}>{inner}</{tag}>", block, count=1)
    # Insert a new child line just before the closing "</BACKGROUND>".
    m = re.search(r"(?:\r\n|\r|\n)[ \t]*</BACKGROUND>\s*$", block)
    if m is None:
        idx = block.rfind("</BACKGROUND>")
        return block[:idx] + f"<{tag}>{inner}</{tag}>" + block[idx:]
    return block[: m.start()] + eol + "\t\t" + f"<{tag}>{inner}</{tag}>" + block[m.start():]


def _remove_child(block: str, tag: str) -> str:
    """Remove a child element AND its own line (indent + trailing newline)."""
    # Common case: the child sits on its own line with a trailing newline.
    pat = re.compile(rf"[ \t]*<{re.escape(tag)}>.*?</{re.escape(tag)}>(?:\r\n|\r|\n)", re.S)
    new = pat.sub("", block, count=1)
    if new != block:
        return new
    # Fallback: child is the last line before the close (no trailing newline of
    # its own) — strip the preceding newline + indent too.
    pat2 = re.compile(rf"(?:\r\n|\r|\n)[ \t]*<{re.escape(tag)}>.*?</{re.escape(tag)}>", re.S)
    return pat2.sub("", block, count=1)


def _apply_fields(block: str, fields: dict[str, int], eol: str) -> str:
    """Apply owned-field edits to a block.

    Only keys present in `fields` are touched (subset = partial update). For each:
    non-zero → set/replace; zero → remove if present. Unknown/nested children and
    any owned field NOT in `fields` are left untouched. New fields are inserted in
    canonical schema order for tidiness.
    """
    # Removals + replacements first (don't depend on order).
    for key, value in fields.items():
        if value == 0:
            block = _remove_child(block, key)
    # Sets in schema order so freshly-inserted fields land in a stable sequence.
    for spec in schema.FIELD_SPECS:
        if spec.key in fields and fields[spec.key] != 0:
            block = _set_child(block, spec.key, str(fields[spec.key]), eol)
    return block


def _build_block(
    *, ui_index: int, name: str, short_name: str, description: str,
    fields: dict[str, int], eol: str,
) -> str:
    """Construct a fresh <BACKGROUND> block (no leading indent; splicer adds it)."""
    children = [
        f"<uiIndex>{ui_index}</uiIndex>",
        f"<szName>{_esc(name)}</szName>",
        f"<szShortName>{_esc(short_name)}</szShortName>",
        f"<szDescription>{_esc_multiline(description, eol)}</szDescription>",
    ]
    for spec in schema.FIELD_SPECS:
        v = fields.get(spec.key, 0)
        if v != 0:
            children.append(f"<{spec.key}>{v}</{spec.key}>")
    body = "".join(f"\t\t{c}{eol}" for c in children)
    return f"<BACKGROUND>{eol}{body}\t</BACKGROUND>"


# ── Public read API ─────────────────────────────────────────────────────────

@dataclass
class ParsedEntry:
    ui_index: int
    name: str
    short_name: str
    description: str
    # Non-zero flat integer modifiers in file order (known + unknown columns),
    # as (tag, value). Mirrors the legacy read route's surface.
    modifiers: list[tuple[str, int]] = field(default_factory=list)
    has_nested: bool = False      # carries <drugtypes>/<drugitems>
    has_unknown: bool = False     # carries non-zero columns outside the schema


@dataclass
class Catalog:
    entries: list[ParsedEntry]    # physical (document) order
    num_found_background: int     # = last physical entry's uiIndex (engine bound)
    duplicate_ids: list[int]


def read_catalog(path: Optional[Path]) -> Catalog:
    """Parse Backgrounds.xml (read-only, lxml) preserving physical order."""
    if not path or not path.exists():
        return Catalog(entries=[], num_found_background=0, duplicate_ids=[])
    try:
        data = path.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError:
            # cp1252/latin-1 file with no UTF-8-valid declaration (common in
            # localized mods): re-encode the bytes as UTF-8 so libxml2 accepts
            # them. Read-only (for listing entries); write-back stays
            # byte-faithful via _read_text/_write_text.
            root = etree.fromstring(data.decode("latin-1").encode("utf-8"))
    except (etree.XMLSyntaxError, OSError):
        return Catalog(entries=[], num_found_background=0, duplicate_ids=[])

    entries: list[ParsedEntry] = []
    seen: set[int] = set()
    dups: set[int] = set()
    last_id = 0
    for bg in root.findall("BACKGROUND"):
        id_elem = bg.find("uiIndex")
        if id_elem is None or id_elem.text is None:
            continue
        try:
            bg_id = int(id_elem.text.strip())
        except ValueError:
            continue
        if bg_id in seen:
            dups.add(bg_id)
        seen.add(bg_id)
        last_id = bg_id  # engine assigns num_found_background on every </BACKGROUND>

        modifiers: list[tuple[str, int]] = []
        has_nested = False
        has_unknown = False
        for child in bg:
            tag = child.tag
            if not isinstance(tag, str):
                continue  # comment / PI
            if tag in schema.META_TAGS:
                continue
            if tag in schema.NESTED_TAGS:
                has_nested = True
                continue
            if child.text is None:
                continue
            try:
                v = int(child.text.strip())
            except (ValueError, AttributeError):
                continue
            if v != 0:
                modifiers.append((tag, v))
                if not schema.is_owned(tag):
                    has_unknown = True
        entries.append(ParsedEntry(
            ui_index=bg_id,
            name=(bg.findtext("szName") or "").strip(),
            short_name=(bg.findtext("szShortName") or "").strip(),
            description=(bg.findtext("szDescription") or "").strip(),
            modifiers=modifiers,
            has_nested=has_nested,
            has_unknown=has_unknown,
        ))
    return Catalog(
        entries=entries,
        num_found_background=last_id,
        duplicate_ids=sorted(dups),
    )


# ── Index helpers ───────────────────────────────────────────────────────────

def _existing_ids(text: str) -> list[int]:
    return [b.ui_index for b in _find_blocks(text) if b.ui_index is not None]


def next_free_index(path: Path) -> int:
    """Smallest safe id for a new background: max+1, else fill the lowest gap.

    Raises BackgroundError(TABLE_FULL) when all of 1..499 are taken (id 500+ is
    silently dropped by the engine, so it's never a valid target).
    """
    text, _bom, _eol = _read_text(path)
    ids = set(_existing_ids(text))
    candidate = (max(ids) if ids else 0) + 1
    if candidate <= schema.MAX_INDEX:
        return candidate
    for i in range(1, schema.MAX_INDEX + 1):
        if i not in ids:
            return i
    raise BackgroundError("TABLE_FULL", f"Background table is full ({schema.NUM_BACKGROUND} max).")


def _require_single(blocks: list[_Block], ui_index: int) -> _Block:
    matches = [b for b in blocks if b.ui_index == ui_index]
    if not matches:
        raise BackgroundError("BACKGROUND_NOT_FOUND", f"No background with uiIndex {ui_index}.")
    if len(matches) > 1:
        raise BackgroundError(
            "DUPLICATE_INDEX",
            f"Backgrounds.xml has {len(matches)} entries with uiIndex {ui_index} "
            f"(the engine uses the last one). Fix the file before editing.",
        )
    return matches[0]


# ── Public write API (byte-splice + atomic) ─────────────────────────────────

def create_background(
    path: Path, *, ui_index: int, name: str, short_name: str, description: str,
    fields: dict[str, int], make_imp_selectable: bool = False,
) -> dict:
    """Insert a new <BACKGROUND>. By default placed just before the current
    physical-last entry so `num_found_background` (the IMP picker bound) is
    unchanged. With `make_imp_selectable`, appended last so it (and any
    currently-hidden higher ids) become IMP-selectable.
    """
    if ui_index <= schema.TEMPLATE_INDEX or ui_index > schema.MAX_INDEX:
        raise BackgroundError(
            "INVALID_INDEX",
            f"uiIndex must be {schema.TEMPLATE_INDEX + 1}..{schema.MAX_INDEX} "
            f"(0 is the template; {schema.NUM_BACKGROUND}+ is silently dropped).",
        )
    text, had_bom, eol = _read_text(path)
    blocks = _find_blocks(text)
    if any(b.ui_index == ui_index for b in blocks):
        raise BackgroundError("INDEX_TAKEN", f"uiIndex {ui_index} already exists.")

    block = _build_block(
        ui_index=ui_index, name=name, short_name=short_name,
        description=description, fields=fields, eol=eol,
    )

    if not blocks:
        # Degenerate file with no entries — insert before </BACKGROUNDS>.
        new_text = _insert_before_root_close(text, block, eol)
    elif make_imp_selectable:
        last = blocks[-1]
        new_text = text[: last.end] + eol + "\t" + block + text[last.end:]
    else:
        last = blocks[-1]
        new_text = text[: last.start] + block + eol + "\t" + text[last.start:]

    _write_text(path, new_text, had_bom)
    return {
        "ui_index": ui_index,
        "num_found_background": _num_found(new_text),
        "imp_selectable": make_imp_selectable or ui_index <= _num_found(new_text),
    }


def edit_background(
    path: Path, *, ui_index: int, name: str, short_name: str, description: str,
    fields: dict[str, int],
) -> dict:
    """Update an existing <BACKGROUND> in place (never reorders, never changes id).

    Preserves unknown children + nested drug lists + every other entry byte-for-
    byte. `fields` is authoritative only for the keys it contains.
    """
    if ui_index == schema.TEMPLATE_INDEX:
        raise BackgroundError("TEMPLATE_PROTECTED", "uiIndex 0 is the template row and can't be edited.")
    text, had_bom, eol = _read_text(path)
    blocks = _find_blocks(text)
    target = _require_single(blocks, ui_index)

    block = target.text
    block = _set_child(block, "szName", _esc(name), eol)
    block = _set_child(block, "szShortName", _esc(short_name), eol)
    block = _set_child(block, "szDescription", _esc_multiline(description, eol), eol)
    block = _apply_fields(block, fields, eol)

    new_text = text[: target.start] + block + text[target.end:]
    _write_text(path, new_text, had_bom)
    return {"ui_index": ui_index, "num_found_background": _num_found(new_text)}


def delete_background(path: Path, *, ui_index: int) -> dict:
    """Remove a <BACKGROUND> block (and its line). Refuses the template row."""
    if ui_index == schema.TEMPLATE_INDEX:
        raise BackgroundError("TEMPLATE_PROTECTED", "uiIndex 0 is the template row and can't be deleted.")
    text, had_bom, eol = _read_text(path)
    blocks = _find_blocks(text)
    target = _require_single(blocks, ui_index)
    was_last = target is blocks[-1]

    istart = _leading_indent_start(text, target.start)
    iend = target.end
    # consume one trailing line ending if present
    if text[iend: iend + 2] == "\r\n":
        iend += 2
    elif text[iend: iend + 1] in ("\n", "\r"):
        iend += 1
    new_text = text[:istart] + text[iend:]

    _write_text(path, new_text, had_bom)
    return {
        "ui_index": ui_index,
        "was_physical_last": was_last,
        "num_found_background": _num_found(new_text),
    }


def set_imp_threshold(path: Path, *, ui_index: int) -> dict:
    """Move the entry with `ui_index` to physically-last so the engine's
    `num_found_background` becomes its id — i.e. it (and every background with a
    lower id) becomes selectable in IMP character creation.
    """
    text, had_bom, eol = _read_text(path)
    blocks = _find_blocks(text)
    target = _require_single(blocks, ui_index)
    if target is blocks[-1]:
        return {"num_found_background": _num_found(text), "moved": False}

    block_str = target.text
    istart = _leading_indent_start(text, target.start)
    iend = target.end
    if text[iend: iend + 2] == "\r\n":
        iend += 2
    elif text[iend: iend + 1] in ("\n", "\r"):
        iend += 1
    removed = text[:istart] + text[iend:]

    last = _find_blocks(removed)[-1]
    new_text = removed[: last.end] + eol + "\t" + block_str + removed[last.end:]
    _write_text(path, new_text, had_bom)
    return {"num_found_background": _num_found(new_text), "moved": True}


def make_all_imp_selectable(path: Path) -> dict:
    """Move the highest-id entry to physically-last → num_found_background = max id
    → every background 0..max becomes IMP-selectable (gaps are harmless)."""
    text, _bom, _eol = _read_text(path)
    ids = _existing_ids(text)
    if not ids:
        raise BackgroundError("BACKGROUND_NOT_FOUND", "No backgrounds to expose.")
    return set_imp_threshold(path, ui_index=max(ids))


def upsert_background_block(
    path: Path, *, block_text: str, make_imp_selectable: bool = False,
) -> dict:
    r"""Ensure a verbatim ``<BACKGROUND>`` block exists, keyed by its OWN uiIndex.

    Create-if-missing ONLY: if the id already exists the file is left untouched
    (we never clobber a shared-catalog entry on behalf of one merc import). When
    created, the block is spliced in just before the physical-last entry so
    ``num_found_background`` (the IMP picker bound) is unchanged, and it is
    preserved VERBATIM — nested ``<drugtypes>``/``<drugitems>`` and unknown mod
    columns included. Atomic write.

    ``block_text`` is a serialized ``<BACKGROUND>`` fragment as the .wmerc
    exporter emits it (LF endings, ``<BACKGROUND>`` at column 0, children at one
    tab). Returns ``{ui_index, created, num_found_background}``.
    """
    idm = _UIINDEX_RE.search(block_text)
    if idm is None:
        raise BackgroundError("INVALID_BLOCK", "Background fragment has no <uiIndex>.")
    ui_index = int(idm.group(1))
    if ui_index <= schema.TEMPLATE_INDEX or ui_index > schema.MAX_INDEX:
        raise BackgroundError(
            "INVALID_INDEX",
            f"uiIndex {ui_index} is out of range "
            f"({schema.TEMPLATE_INDEX + 1}..{schema.MAX_INDEX}).",
        )

    text, had_bom, eol = _read_text(path)
    blocks = _find_blocks(text)
    if any(b.ui_index == ui_index for b in blocks):
        # Already present — never overwrite a shared entry for one import.
        return {"ui_index": ui_index, "created": False,
                "num_found_background": _num_found(text)}

    # Normalize the spliced fragment to pure ASCII (non-ASCII codepoints → numeric
    # XML entities) so any high byte it carries is valid under the target's
    # declared encoding; _write_text's latin-1+xmlcharrefreplace does NOT catch a
    # 0x80..0xFF codepoint, which would brick a no-<?xml?> target's boot load.
    # Sibling entries keep their byte-faithful latin-1 round-trip. Mirrors
    # slot_table_xml's `_ascii_safe(_reindent_foreign_block(...))`.
    block = _ascii_safe(_reindent_foreign_block(block_text, eol))
    if not blocks:
        new_text = _insert_before_root_close(text, block, eol)
    elif make_imp_selectable:
        last = blocks[-1]
        new_text = text[: last.end] + eol + "\t" + block + text[last.end:]
    else:
        last = blocks[-1]
        new_text = text[: last.start] + block + eol + "\t" + text[last.start:]

    _write_text(path, new_text, had_bom)
    return {"ui_index": ui_index, "created": True,
            "num_found_background": _num_found(new_text)}


# ── internals ───────────────────────────────────────────────────────────────

def _num_found(text: str) -> int:
    blocks = _find_blocks(text)
    for b in reversed(blocks):
        if b.ui_index is not None:
            return b.ui_index
    return 0


def _insert_before_root_close(text: str, block: str, eol: str) -> str:
    m = re.search(r"(?:\r\n|\r|\n)?[ \t]*</BACKGROUNDS>", text)
    if m is None:
        # No root close — append (degenerate). Keep it valid-ish.
        return text + eol + "\t" + block
    return text[: m.start()] + eol + "\t" + block + text[m.start():]


def _reindent_foreign_block(block_text: str, eol: str) -> str:
    r"""Re-indent a serialized ``<BACKGROUND>`` fragment to nest under the root.

    The .wmerc exporter emits the block via ``ET.tostring`` after
    ``ET.indent(space="\t")``: LF endings, ``<BACKGROUND>`` at column 0, its
    children at one tab. To match the file's existing entries (entry at one tab,
    children at two) we add one leading tab to every STRUCTURAL line — i.e. a
    line whose first non-space char is ``<`` — except the opening ``<BACKGROUND>``
    line, and convert line endings to the file's ``eol``.

    We key on a leading ``<`` rather than "every non-first line" so that
    continuation lines of a multi-line ``<szDescription>`` are left untouched
    (well-formed XML text can never contain a raw ``<``, so a leading ``<``
    unambiguously marks an element line, never description text). The returned
    block starts at column 0 — exactly like ``_build_block`` output, so the
    splicer supplies the entry's own leading tab.
    """
    lines = block_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n").split("\n")
    out = [
        ("\t" + ln) if (i != 0 and ln.lstrip().startswith("<")) else ln
        for i, ln in enumerate(lines)
    ]
    return eol.join(out)
