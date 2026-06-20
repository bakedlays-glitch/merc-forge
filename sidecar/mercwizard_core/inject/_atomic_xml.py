"""Shared atomic XML write helper used by every XML injector.

Pre-2026-05-15 the four XML writers (profiles_xml, aim_availability,
merc_availability, starting_gear) each had their own `_save(tree, path)`
that did `etree.tostring(...) -> path.write_bytes(xml_bytes)` — non-atomic.
A crash mid-write (power loss, OOM, AV truncation) left a half-written
XML file that the engine refuses to load (LoadExternalGameplayData
RUNTIME ERROR at boot). The fix consolidates all four to this helper,
which writes to a sibling tempfile and `os.replace`s into position so
the on-disk file is either fully pre-edit or fully post-edit.

Mirrors the pattern already used by `edt.py::EDT._atomic_write_bytes`
for the binary EDTs.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from lxml import etree

# Leading XML declaration, for the cp1252 rescue: lxml refuses to parse a
# `str` that still carries an encoding declaration, so we strip it first.
# Require whitespace (or the closing `?>`) right after `xml` so this matches
# only a real `<?xml ...?>` declaration, NOT a `<?xml-stylesheet ...?>` PI.
_XML_DECL_RE = re.compile(r"^\s*<\?xml(?:\s[^>]*)?\?>\s*")


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Replace `path` with `data` atomically (same-dir tempfile + os.replace).

    The bytes-level core of `save_atomic`, exposed for callers that have
    already serialized their content and must NOT round-trip it through an
    XML serializer. Two such callers:

      - The `.wmerc` FaceGear.xml row append, which builds the new `<ITEM>`
        block by string insertion. Round-tripping FaceGear.xml through
        ElementTree silently corrupts its dual-entry "last wins" structure
        (documented KGoggles boot-CTD — MercWizard2/CLAUDE.md "FaceGear is
        overlay, not portrait paint").
      - The generic extra-table upsert, which serializes its ElementTree via
        `ET.tostring` and writes the bytes here.

    Algorithm:
      1. Create a tempfile in the same directory (so `os.replace` is within
         the same filesystem and is atomic on POSIX + NTFS).
      2. Write the bytes, fsync, close.
      3. `os.replace(tmp, path)` — atomic rename.
      4. On any error, clean up the tempfile so we don't leave litter.

    Same-directory tempfiles are important: `tempfile.gettempdir()` may be
    on a different volume (especially with mod installs on D:/E:), and
    `os.replace` across volumes is NOT atomic on Windows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as tf:
            tf.write(data)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_atomic(
    tree: etree._ElementTree,
    path: Path,
    *,
    pretty_print: bool = True,
    encoding: str = "utf-8",
    xml_declaration: bool = False,
) -> None:
    """Serialize `tree` to bytes and replace `path` atomically.

    Serializes via `etree.tostring(...)` to in-memory bytes, then hands them
    to `write_bytes_atomic` for the tempfile + `os.replace` dance. The
    on-disk file is therefore either fully pre-edit or fully post-edit,
    never a truncated half-write.
    """
    xml_bytes = etree.tostring(
        tree,
        pretty_print=pretty_print,
        xml_declaration=xml_declaration,
        encoding=encoding,
    )
    write_bytes_atomic(path, xml_bytes)


def parse_tolerant(path: Path) -> etree._ElementTree:
    """Parse an XML file, tolerant of a UTF-8 BOM AND of legacy cp1252 /
    mislabeled bytes.

    The default lxml parse raises `XMLSyntaxError: Input is not proper
    UTF-8` on a file that carries raw cp1252 high bytes under a utf-8 or
    absent encoding declaration — the packaging of many localized 1.13
    rebundles (RU/DE/FR). Inside a writer's read-modify-write that error
    rolls the save back and HARD-BLOCKS every merc save on that install.

    Here we instead rescue such a file: decode the bytes 1:1 via cp1252
    (a superset of latin-1 that also covers the 0x80–0x9F smart-quote
    range), strip the encoding declaration lxml refuses on a `str`, and
    re-parse as Unicode. The caller (`save_atomic_preserving`) then
    re-serializes the tree as valid UTF-8 — the ONLY byte encoding the
    engine round-trips: its expat is created with `XML_ParserCreate(NULL)`
    and registers no Windows-1252 handler, and it re-decodes char data as
    `CP_UTF8` (XML_Profiles.cpp:396/404).

    Assumes `path` exists — callers keep their own not-found handling.
    """
    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return etree.ElementTree(etree.fromstring(data, parser))
    except etree.XMLSyntaxError:
        text = _XML_DECL_RE.sub("", data.decode("cp1252"), count=1)
        return etree.ElementTree(etree.fromstring(text, parser))


def save_atomic_preserving(tree: etree._ElementTree, path: Path) -> None:
    """Atomic save that ALWAYS emits UTF-8 + an `<?xml?>` declaration.

    The four core merc XML writers (profiles / aim_availability /
    merc_availability / starting_gear) reflow the whole tree on save.
    Normalizing to self-describing UTF-8 makes the output decodable by the
    engine — its expat understands only UTF-8/UTF-16/ISO-8859-1/US-ASCII
    and re-decodes char data as `CP_UTF8`, so UTF-8 is the only byte
    encoding that round-trips accented names. It also restores the
    `<?xml ... encoding='utf-8'?>` declaration the previous
    `encoding='utf-8', xml_declaration=False` save silently dropped.

    NB: echoing a source `Windows-1252` declaration here would be engine
    FATAL (`XML_ERROR_UNKNOWN_ENCODING` — no handler is registered → the
    whole table fails to load), which is why we normalize rather than
    preserve the source codepage. Paired with `parse_tolerant`, which
    rescues legacy cp1252 content INTO the tree so this re-emits it as
    valid UTF-8.
    """
    save_atomic(
        tree, path,
        pretty_print=True, encoding="utf-8", xml_declaration=True,
    )
