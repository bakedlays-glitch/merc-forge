"""
mapforge.corpus
===============

Read-only accessor for the distilled MapForge generator corpus — the
biome-stratified, subframe-level catalog shipped at
``generator_corpus.json`` next to this module (built dev-side by
``Headless_Compiler/map_corpus/distill_generator_corpus.py``).

Three sources: ``stock`` (vanilla 1.13_2025 maps), ``redux``, and
``combined`` (sha1-deduped union). For each source × biome the catalog
records, per generator layer and STI slot, the empirical ``{sub: count}``
distribution mappers actually placed. Generators turn that into weighted
variant picks via the existing ``_make_sub_picker``.

The JSON is loaded once and cached. Everything degrades gracefully to ""
/ ``None`` / empty when the file is absent or a cell has no data, so the
generators stay backward-compatible (blank corpus params = old behavior).
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Optional

_DIR = Path(__file__).parent
_CORPUS_PATH = _DIR / "generator_corpus.json"
_COVERAGE_PATH = _DIR / "coverage.json"


@functools.lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    if not _CORPUS_PATH.is_file():
        return {}
    try:
        return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


@functools.lru_cache(maxsize=1)
def _coverage() -> dict[str, Any]:
    if not _COVERAGE_PATH.is_file():
        return {}
    try:
        return json.loads(_COVERAGE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


# ── catalog metadata ─────────────────────────────────────────────────────────

def available() -> bool:
    """True when the corpus JSON loaded and has scatter data."""
    return bool(_data().get("scatter"))


def list_sources() -> list[str]:
    return list(_data().get("sources", []))


def list_biomes() -> list[str]:
    return list(_data().get("biomes", []))


def list_layers() -> list[str]:
    return list(_data().get("layers", []))


def source_installs() -> dict[str, str]:
    return dict(_data().get("source_installs", {}))


# ── weighted-spec formatting ─────────────────────────────────────────────────

def _fmt_subs(subs: Optional[dict]) -> str:
    """Format a {sub: weight} dict into the '_make_sub_picker' spec
    'sub:weight,sub:weight'. Skips sub 0 (renders nothing) and non-positive
    weights. Returns '' for empty/None."""
    if not subs:
        return ""
    parts = []
    for s, w in subs.items():
        try:
            si = int(s)
            wi = int(w)
        except (TypeError, ValueError):
            continue
        if si >= 1 and wi > 0:
            parts.append(f"{si}:{wi}")
    return ",".join(parts)


def _scatter_cell(source: str, biome: str, layer: str, slot: int) -> Optional[dict]:
    try:
        return _data()["scatter"][source][biome][layer][str(slot)]
    except (KeyError, TypeError):
        return None


def resolve_subs(source: str, biome: str, layer: str, slot: int) -> str:
    """Weighted sub spec for a (source, biome, layer, slot) cell, ready to
    feed _make_sub_picker. Fallback chain: exact cell → combined → ''."""
    subs = _scatter_cell(source, biome, layer, slot)
    if not subs and source != "combined":
        subs = _scatter_cell("combined", biome, layer, slot)
    return _fmt_subs(subs)


def scatter_slots(source: str, biome: str, layer: str) -> list[int]:
    """Every slot the corpus has data for on a (source, biome, layer),
    with combined fallback. Sorted ascending."""
    try:
        cell = _data()["scatter"][source][biome][layer]
    except (KeyError, TypeError):
        cell = None
    if not cell and source != "combined":
        try:
            cell = _data()["scatter"]["combined"][biome][layer]
        except (KeyError, TypeError):
            cell = None
    if not cell:
        return []
    return sorted(int(s) for s in cell)


# ── building tables ──────────────────────────────────────────────────────────

def get_building_table(source: str, biome: str) -> Optional[dict]:
    """The buildings[source][biome] table (positions/doors/sizes), with
    combined fallback. None when neither has clean buildings for the biome."""
    try:
        tbl = _data()["buildings"][source][biome]
    except (KeyError, TypeError):
        tbl = None
    if (not tbl or not tbl.get("positions")) and source != "combined":
        try:
            tbl = _data()["buildings"]["combined"][biome]
        except (KeyError, TypeError):
            tbl = None
    return tbl or None


def building_dominant_slot(table: Optional[dict], lo: int, hi: int,
                           kind: str = "structs") -> Optional[int]:
    """The most-used slot in [lo,hi] across all of a building table's
    positions (kind='structs' or 'roofs'). None when nothing in range."""
    if not table:
        return None
    agg: dict[int, int] = {}
    for pos_data in table.get("positions", {}).values():
        for slot, subs in pos_data.get(kind, {}).items():
            si = int(slot)
            if lo <= si <= hi:
                agg[si] = agg.get(si, 0) + sum(int(v) for v in subs.values())
    if not agg:
        return None
    return max(agg, key=lambda k: agg[k])


def building_position_subs(table: Optional[dict], position: str, slot: int,
                           kind: str = "structs") -> str:
    """Weighted sub spec for one slot at one position class of a building
    table. '' when absent."""
    if not table:
        return ""
    subs = table.get("positions", {}).get(position, {}).get(kind, {}).get(str(slot))
    return _fmt_subs(subs)


def building_doors(table: Optional[dict]) -> dict:
    """The door distributions for a building table: {'by_slot':{slot:{sub:w}},
    'by_edge':{edge:count}}. Empty dict when absent."""
    if not table:
        return {}
    return table.get("doors", {}) or {}


def list_building_cells() -> list[tuple[str, str]]:
    """Every (source, biome) cell that carries its OWN non-empty building
    table — no combined fallback, so each cell appears exactly once.
    Sorted (source, biome). Drives the /buildings catalog endpoint."""
    out: list[tuple[str, str]] = []
    for source, biomes in (_data().get("buildings") or {}).items():
        if not isinstance(biomes, dict):
            continue
        for biome, tbl in biomes.items():
            if isinstance(tbl, dict) and tbl.get("positions"):
                out.append((str(source), str(biome)))
    return sorted(out)


def building_dominant_sub(table: Optional[dict], slot: int,
                          kind: str = "structs") -> Optional[int]:
    """The most-weighted sub for `slot` aggregated across all of a building
    table's positions. Used for catalog thumbnails — the single subframe
    that best represents what this cell's walls/roofs look like. None when
    the slot has no data."""
    if not table:
        return None
    agg: dict[int, int] = {}
    for pos_data in table.get("positions", {}).values():
        for s, w in (pos_data.get(kind, {}).get(str(slot)) or {}).items():
            try:
                si, wi = int(s), int(w)
            except (TypeError, ValueError):
                continue
            if si >= 1 and wi > 0:
                agg[si] = agg.get(si, 0) + wi
    if not agg:
        return None
    return max(agg, key=lambda k: agg[k])


def building_size_range(table: Optional[dict]) -> Optional[dict]:
    """Min/max footprint from a building table's empirical size histograms
    (`size_w`/`size_h` = {size: count}). None when either histogram is
    absent/empty — the caller falls back to its own defaults."""
    if not table:
        return None
    try:
        ws = [int(k) for k in (table.get("size_w") or {})]
        hs = [int(k) for k in (table.get("size_h") or {})]
    except (TypeError, ValueError):
        return None
    if not ws or not hs:
        return None
    return {"min_w": min(ws), "max_w": max(ws),
            "min_h": min(hs), "max_h": max(hs)}


# ── coverage ─────────────────────────────────────────────────────────────────

def coverage(source: Optional[str] = None, biome: Optional[str] = None) -> Any:
    """Full coverage dict, or a source slice, or a single (source,biome) cell."""
    cov = _coverage()
    if source is None:
        return cov
    src = cov.get(source, {})
    if biome is None:
        return src
    return src.get(biome, {})
