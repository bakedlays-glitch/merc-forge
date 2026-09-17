"""Friendly names for The Wasteland's custom prop sheets, for the MapForge palette.

The palette otherwise labels every tile by its raw STI filename (``gecko_props_v101``).
This module maps a sheet's filename to a human name, which map it belongs to, and
per-subframe names for the sub-picker. Data lives in ``prop_labels.json`` beside
this file (hand-maintained); ``aliases`` fold version / shadow / cross-tileset
copies onto one canonical entry so a future ``_vNNN`` copy needs one alias line,
not a re-name. ``tools/prop_labels_lint.py`` flags any registered custom sheet or
sub that has no name here, so new props never ship unlabelled.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

_JSON = Path(__file__).with_name("prop_labels.json")
_LOCK = threading.Lock()
_CACHE: Optional[dict] = None
_CACHE_TAG: Optional[tuple[int, int]] = None


def _load() -> dict:
    global _CACHE, _CACHE_TAG
    with _LOCK:
        try:
            stat = _JSON.stat()
            tag = (stat.st_mtime_ns, stat.st_size)
            if _CACHE is None or _CACHE_TAG != tag:
                _CACHE = json.loads(_JSON.read_text(encoding="utf-8"))
                _CACHE_TAG = tag
        except Exception:
            _CACHE = {"sheets": {}, "aliases": {}, "custom_prefixes": []}
            _CACHE_TAG = None
    return _CACHE


def reload() -> None:
    """Drop the cache (for the lint tool / tests after editing the JSON)."""
    global _CACHE, _CACHE_TAG
    with _LOCK:
        _CACHE = None
        _CACHE_TAG = None


def custom_prefixes() -> tuple[str, ...]:
    return tuple(_load().get("custom_prefixes", []))


def is_custom(sti_filename: str) -> bool:
    n = (sti_filename or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return any(n.startswith(k) for k in custom_prefixes())


def _canonical_key(sti_filename: str, data: Optional[dict] = None) -> Optional[str]:
    """basename.lower() resolved through the alias table to a sheet key, or None."""
    if not sti_filename:
        return None
    name = Path(sti_filename).name.lower()
    if data is None:
        data = _load()
    name = data.get("aliases", {}).get(name, name)
    return name if name in data.get("sheets", {}) else None


def label_for(sti_filename: str) -> Optional[dict]:
    """Full label record ({name, origin, category, note?, per_sub?}) or None."""
    data = _load()
    key = _canonical_key(sti_filename, data)
    return data.get("sheets", {}).get(key) if key else None


def display_name(sti_filename: str) -> Optional[str]:
    rec = label_for(sti_filename)
    return rec.get("name") if rec else None


def origin(sti_filename: str) -> Optional[str]:
    rec = label_for(sti_filename)
    return rec.get("origin") if rec else None


def sub_name(sti_filename: str, sub: int) -> Optional[str]:
    """Per-sub prop name (1-based sub index) or None."""
    rec = label_for(sti_filename)
    if not rec:
        return None
    return (rec.get("per_sub") or {}).get(str(int(sub)))


def sub_names(sti_filename: str) -> dict[int, str]:
    """{sub_index -> name} for the whole sheet ({} when none)."""
    rec = label_for(sti_filename)
    if not rec:
        return {}
    return {int(k): v for k, v in (rec.get("per_sub") or {}).items()}


def named_sheet_keys() -> set[str]:
    return set(_load().get("sheets", {}).keys())


def alias_keys() -> set[str]:
    return set(_load().get("aliases", {}).keys())
