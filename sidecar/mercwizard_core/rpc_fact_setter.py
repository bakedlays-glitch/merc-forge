"""Author carried-item -> fact triggers in strategicmap.lua.

A fact-gated recruit branch (in <profile>.NPC) requires a fact to be true. This
module authors the Lua that SETS that fact: on entering the RPC's sector, if any
squad member carries a chosen item, ``SetFactTrue(fact)``. Mirrors the live
Dogmeat leather-jacket pattern (fact 450).

Managed block inside ``HandleSectorTacticalEntry`` (inserted at the TOP of the
body, so we never match Lua's nested ``end``s). Each RPC's trigger is a
per-profile delimited entry — hand-authored triggers outside the block are never
touched.

Traps encoded here (memory project_rpc_authoring_dogmeat_a9):
  * OUR_TEAM = literal 0 (this script defines no Team table)
  * SetFactTrue writes gubFact[] — the array usFactMustBeTrue reads via CheckFact
    (NOT SetModderLUAFact)
  * HasItemInInventory(soldierID, item) scans worn + carried
  * free facts 431-499
  * BOM-free write
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .rpc_placement import (
    _render,
    _split_lines_keepends,
    colrow_to_sector,
    sector_to_colrow,  # noqa: F401  (re-exported for the route)
    validate_gridno,   # noqa: F401
)

BEGIN_MARKER = "-- MERCWIZARD RPC FACTS BEGIN (managed by Merc Wizard - edit in the tool)"
END_MARKER = "-- MERCWIZARD RPC FACTS END"

FACT_MIN, FACT_MAX = 431, 499  # engine free-fact range

_FUNC_RE = re.compile(r"^\s*function\s+HandleSectorTacticalEntry\s*\(", re.MULTILINE)
_BEGIN_RE = re.compile(r"--\s*MERCWIZARD RPC FACTS BEGIN", re.IGNORECASE)
_END_RE = re.compile(r"--\s*MERCWIZARD RPC FACTS END", re.IGNORECASE)
_ENTRY_BEGIN_RE = re.compile(r"--\s*MW-RPC-FACT\s+(\d+)\s+BEGIN")
_ENTRY_END_RE = re.compile(r"--\s*MW-RPC-FACT\s+(\d+)\s+END")
_IF_RE = re.compile(
    r"sSectorX\s*==\s*(\d+)\s+and\s+sSectorY\s*==\s*(\d+)\s+and\s+bSectorZ\s*==\s*(\d+)"
)
_ITEM_RE = re.compile(r"HasItemInInventory\(\s*iLoop\s*,\s*(\d+)\s*\)")
_FACT_RE = re.compile(r"SetFactTrue\(\s*(\d+)\s*\)")


@dataclass(frozen=True)
class FactSetter:
    profile: int
    col: int
    row: int
    z: int
    item: int
    fact: int

    @property
    def sector(self) -> str:
        return colrow_to_sector(self.col, self.row)


def validate_fact(fact: int) -> int:
    if not (FACT_MIN <= fact <= FACT_MAX):
        raise ValueError(f"fact {fact} out of the free range {FACT_MIN}-{FACT_MAX}.")
    return fact


def _entry_lines(fs: FactSetter) -> list[str]:
    return [
        f"\t-- MW-RPC-FACT {fs.profile} BEGIN",
        f"\tif ( sSectorX == {fs.col} and sSectorY == {fs.row} and bSectorZ == {fs.z} ) then",
        "\t\tfor iLoop = GetTacticalStatusFirstID(0), GetTacticalStatusLastID(0) do",
        f"\t\t\tif ( HasItemInInventory( iLoop, {fs.item} ) ) then SetFactTrue( {fs.fact} ) end",
        "\t\tend",
        "\tend",
        f"\t-- MW-RPC-FACT {fs.profile} END",
    ]


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    begin = end = None
    for i, ln in enumerate(lines):
        if begin is None and _BEGIN_RE.search(ln):
            begin = i
        elif begin is not None and _END_RE.search(ln):
            end = i
            break
    return (begin, end) if begin is not None and end is not None else None


def _entry_span(lines: list[str], begin: int, end: int, profile: int) -> tuple[int, int] | None:
    """(start, stop) inclusive line indices of `profile`'s entry within the block."""
    start = None
    for i in range(begin + 1, end):
        m = _ENTRY_BEGIN_RE.search(lines[i])
        if m and int(m.group(1)) == profile:
            start = i
        elif start is not None:
            me = _ENTRY_END_RE.search(lines[i])
            if me and int(me.group(1)) == profile:
                return start, i
    return None


def read_fact_setters(text: str) -> list[FactSetter]:
    lines, _ = _split_lines_keepends(text)
    bounds = _block_bounds(lines)
    if not bounds:
        return []
    begin, end = bounds
    out: list[FactSetter] = []
    i = begin + 1
    while i < end:
        m = _ENTRY_BEGIN_RE.search(lines[i])
        if not m:
            i += 1
            continue
        profile = int(m.group(1))
        span = _entry_span(lines, begin, end, profile)
        if not span:
            i += 1
            continue
        chunk = "\n".join(lines[span[0]:span[1] + 1])
        mif, mit, mf = _IF_RE.search(chunk), _ITEM_RE.search(chunk), _FACT_RE.search(chunk)
        if mif and mit and mf:
            out.append(FactSetter(
                profile=profile,
                col=int(mif.group(1)), row=int(mif.group(2)), z=int(mif.group(3)),
                item=int(mit.group(1)), fact=int(mf.group(1)),
            ))
        i = span[1] + 1
    return out


def _ensure_block(lines: list[str]) -> tuple[int, int]:
    bounds = _block_bounds(lines)
    if bounds:
        return bounds
    empty = [f"\t{BEGIN_MARKER}", f"\t{END_MARKER}"]
    m = _FUNC_RE.search("\n".join(lines))
    if not m:
        raise ValueError("HandleSectorTacticalEntry( ) not found in strategicmap.lua.")
    header_idx = "\n".join(lines)[: m.start()].count("\n")
    insert_at = header_idx + 1
    lines[insert_at:insert_at] = empty
    return insert_at, insert_at + 1


def apply_upsert(text: str, fs: FactSetter) -> str:
    validate_fact(fs.fact)
    lines, eol = _split_lines_keepends(text)
    begin, end = _ensure_block(lines)
    span = _entry_span(lines, begin, end, fs.profile)
    entry = _entry_lines(fs)
    if span:
        lines[span[0]:span[1] + 1] = entry
    else:
        lines[end:end] = entry
    return _render(lines, eol)


def apply_remove(text: str, profile: int) -> str:
    lines, eol = _split_lines_keepends(text)
    bounds = _block_bounds(lines)
    if not bounds:
        return text
    begin, end = bounds
    span = _entry_span(lines, begin, end, profile)
    if span:
        del lines[span[0]:span[1] + 1]
    return _render(lines, eol)
