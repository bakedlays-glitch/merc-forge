"""Author RPC on-map placements into a mod's ``Scripts/GameInit.lua``.

An RPC (profile ``Type=3``) is dropped onto a sector at new-game init by a
single Lua call inside ``InitNPCs()``::

    InitialProfile( <profile>, <col>, <row>, <z>, <gridno> )

``InitialProfile`` sets ``fUseProfileInsertionInfo=TRUE`` + ``INSERTION_CODE_GRIDNO``
so ``AddProfilesUsingProfileInsertionData`` spawns the profile on the civilian
team the first time that sector loads. This is exactly how every live RPC in the
project ships (Dogmeat 166, Ringo 59, Trudy 72, ...).

Merc Wizard owns a MARKED block inside ``InitNPCs()`` and only ever edits text
between the markers — hand-authored ``InitialProfile`` lines outside the block
are never touched. The block is inserted at the TOP of the function body, so we
never have to match Lua's nested ``end`` keywords.

Two footguns this module removes structurally:

* **Column/row transposition** — ``InitialProfile`` is ``(profile, X=column,
  Y=row, ...)``. Sector "A9" = row A(1), column 9 => ``(9, 1)``. Passing ``(1, 9)``
  silently places the RPC in sector I1 (opposite corner) and it never spawns
  where the player looks. Callers pass a sector CODE ("A9"); we do the mapping.
* **BOM** — a UTF-8 BOM makes the Lua loader crash with a lying "Cannot open
  file". We never write one and strip a pre-existing one on rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BEGIN_MARKER = "-- MERCWIZARD RPC PLACEMENTS BEGIN (managed by Merc Wizard - edit in the tool)"
END_MARKER = "-- MERCWIZARD RPC PLACEMENTS END"

# Engine world size: gridno is 0 .. WORLD_MAX-1 (WORLD_MAX = 160*160 = 25600).
GRIDNO_MAX = 25599
SECTOR_ROWS = 16  # A..P
SECTOR_COLS = 16  # 1..16

_INITIAL_PROFILE_RE = re.compile(
    r"InitialProfile\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)"
)
_BEGIN_RE = re.compile(r"--\s*MERCWIZARD RPC PLACEMENTS BEGIN", re.IGNORECASE)
_END_RE = re.compile(r"--\s*MERCWIZARD RPC PLACEMENTS END", re.IGNORECASE)
_INITNPCS_RE = re.compile(r"^\s*function\s+InitNPCs\s*\(\s*\)", re.MULTILINE)
_LABEL_RE = re.compile(r"--\s*(.+?)\s*$")


@dataclass(frozen=True)
class Placement:
    profile: int
    col: int
    row: int
    z: int
    gridno: int
    label: str = ""

    @property
    def sector(self) -> str:
        return colrow_to_sector(self.col, self.row)


# ── sector <-> (col,row) ────────────────────────────────────────────────────

def sector_to_colrow(sector_code: str) -> tuple[int, int]:
    """"A9" -> (col=9, row=1). Letter = row (A..P => 1..16), number = column."""
    s = (sector_code or "").strip().upper()
    m = re.fullmatch(r"([A-P])\s*(\d{1,2})", s)
    if not m:
        raise ValueError(
            f"Invalid sector code {sector_code!r} - expected a letter A-P then a "
            f"number 1-16, e.g. 'A9'."
        )
    row = ord(m.group(1)) - ord("A") + 1
    col = int(m.group(2))
    if not (1 <= col <= SECTOR_COLS):
        raise ValueError(f"Sector column {col} out of range 1-{SECTOR_COLS}.")
    return col, row


def colrow_to_sector(col: int, row: int) -> str:
    if 1 <= row <= SECTOR_ROWS and 1 <= col <= SECTOR_COLS:
        return f"{chr(ord('A') + row - 1)}{col}"
    return f"?(col={col},row={row})"


def validate_gridno(gridno: int) -> int:
    if not (0 <= gridno <= GRIDNO_MAX):
        raise ValueError(f"gridno {gridno} out of range 0-{GRIDNO_MAX}.")
    return gridno


# ── parsing ─────────────────────────────────────────────────────────────────

def _split_lines_keepends(text: str) -> tuple[list[str], str]:
    """Return (lines_without_endings, dominant_eol)."""
    eol = "\r\n" if text.count("\r\n") >= text.count("\n") - text.count("\r\n") else "\n"
    if "\r\n" not in text and "\n" in text:
        eol = "\n"
    return text.replace("\r\n", "\n").split("\n"), eol


def _parse_line(line: str) -> Placement | None:
    m = _INITIAL_PROFILE_RE.search(line)
    if not m:
        return None
    profile, col, row, z, gridno = (int(g) for g in m.groups())
    label = ""
    after = line[m.end():]
    lm = _LABEL_RE.search(after)
    if lm:
        label = lm.group(1).strip()
    return Placement(profile, col, row, z, gridno, label)


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Index of the BEGIN line and END line (inclusive), or None."""
    begin = end = None
    for i, ln in enumerate(lines):
        if begin is None and _BEGIN_RE.search(ln):
            begin = i
        elif begin is not None and _END_RE.search(ln):
            end = i
            break
    if begin is not None and end is not None:
        return begin, end
    return None


def read_placements(text: str) -> dict:
    """Return {'managed': [Placement...], 'handAuthored': [Placement...]}.

    Managed = the InitialProfile lines inside our marker block.
    Hand-authored = every other InitialProfile line in the file.
    """
    lines, _ = _split_lines_keepends(text)
    bounds = _block_bounds(lines)
    managed: list[Placement] = []
    hand: list[Placement] = []
    block_range = range(bounds[0], bounds[1] + 1) if bounds else range(0)
    for i, ln in enumerate(lines):
        p = _parse_line(ln)
        if p is None:
            continue
        (managed if i in block_range else hand).append(p)
    return {"managed": managed, "handAuthored": hand}


# ── rewriting ───────────────────────────────────────────────────────────────

def _sanitize_label(label: str) -> str:
    # A newline or comment-dash in the label would break the block structure
    # (comment runs to EOL; a stray newline splits the line). Flatten both.
    return label.replace("\r", " ").replace("\n", " ").replace("--", "-").strip()


def _format_line(p: Placement) -> str:
    base = f"\tInitialProfile( {p.profile}, {p.col}, {p.row}, {p.z}, {p.gridno} )"
    label = _sanitize_label(p.label)
    return f"{base}   -- {label}" if label else base


def _render(lines: list[str], eol: str) -> str:
    # Strip any BOM off the first line; never emit one.
    if lines and lines[0].startswith("﻿"):
        lines[0] = lines[0][1:]
    return eol.join(lines)


def _ensure_block(lines: list[str]) -> tuple[int, int]:
    """Ensure the marker block exists; return (begin_idx, end_idx) inclusive.

    Creates the block at the top of InitNPCs()'s body. Creates InitNPCs()
    itself at EOF if the file has none.
    """
    bounds = _block_bounds(lines)
    if bounds:
        return bounds

    empty_block = [f"\t{BEGIN_MARKER}", f"\t{END_MARKER}"]
    m = _INITNPCS_RE.search("\n".join(lines))
    if m:
        # Line index of the `function InitNPCs()` header.
        header_idx = "\n".join(lines)[: m.start()].count("\n")
        insert_at = header_idx + 1
        lines[insert_at:insert_at] = empty_block
        return insert_at, insert_at + 1

    # No InitNPCs() at all: append a fresh one at EOF.
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.extend(["function InitNPCs()", *empty_block, "end", ""])
    begin = lines.index(f"\t{BEGIN_MARKER}")
    return begin, begin + 1


def apply_upsert(
    text: str,
    profile: int,
    col: int,
    row: int,
    z: int,
    gridno: int,
    label: str = "",
) -> str:
    """Insert or replace the managed InitialProfile line for `profile`."""
    validate_gridno(gridno)
    lines, eol = _split_lines_keepends(text)
    begin, end = _ensure_block(lines)
    new_line = _format_line(Placement(profile, col, row, z, gridno, label))

    # Replace an existing managed line for this profile, else insert before END.
    for i in range(begin + 1, end):
        p = _parse_line(lines[i])
        if p is not None and p.profile == profile:
            lines[i] = new_line
            return _render(lines, eol)
    lines.insert(end, new_line)
    return _render(lines, eol)


def apply_remove(text: str, profile: int) -> str:
    """Remove the managed InitialProfile line for `profile` (no-op if absent)."""
    lines, eol = _split_lines_keepends(text)
    bounds = _block_bounds(lines)
    if not bounds:
        return text
    begin, end = bounds
    for i in range(begin + 1, end):
        p = _parse_line(lines[i])
        if p is not None and p.profile == profile:
            del lines[i]
            break
    return _render(lines, eol)


def apply_adopt(text: str, profile: int) -> str:
    """Move a hand-authored placement for `profile` into the managed block.

    Deletes exactly the one hand-authored ``InitialProfile`` line (outside the
    block) for this profile — a deliberate, narrow exception to the never-touch-
    outside-markers rule — then re-adds it as a managed line with the same
    sector/gridno/label. No-op if there's no hand-authored line to adopt.
    """
    lines, eol = _split_lines_keepends(text)
    bounds = _block_bounds(lines)
    block_range = range(bounds[0], bounds[1] + 1) if bounds else range(0)
    for i, ln in enumerate(lines):
        if i in block_range:
            continue
        p = _parse_line(ln)
        if p is not None and p.profile == profile:
            del lines[i]
            return apply_upsert(
                _render(lines, eol), p.profile, p.col, p.row, p.z, p.gridno, p.label,
            )
    return text
