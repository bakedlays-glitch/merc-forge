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
import tempfile
from pathlib import Path

from lxml import etree


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
