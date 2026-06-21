"""Decode a world item's BIGITEMS graphic to PNG for the MapForge overlay.
usItem -> Items.xml (ubGraphicType, ubGraphicNum) -> BIGITEMS\\<stem>.sti -> frame 0.
Reuses the existing STI/SLF helpers; never writes. Recipe validated in
docs/superpowers/specs/2026-06-21-item-graphic-research.md.
"""
from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# (install_root) -> {uiIndex: (ubGraphicType, ubGraphicNum)}, fingerprinted by mtime_ns.
_ITEMS_CACHE: dict[str, tuple[int, dict[int, tuple[int, int]]]] = {}
_ITEMS_LOCK = threading.Lock()


def _items_xml_path(install_root: str) -> Optional[Path]:
    for rel in ("Data-1.13/TableData/Items/Items.xml", "Data/TableData/Items/Items.xml"):
        p = Path(install_root) / rel
        if p.is_file():
            return p
    return None


def _load_item_graphics(install_root: str) -> dict[int, tuple[int, int]]:
    path = _items_xml_path(install_root)
    if path is None:
        return {}
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _ITEMS_LOCK:
        hit = _ITEMS_CACHE.get(install_root)
        if hit and hit[0] == mtime:
            return hit[1]
    out: dict[int, tuple[int, int]] = {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}
    for item in root.findall("ITEM"):
        idx = item.findtext("uiIndex")
        if idx is None:
            continue
        try:
            ui = int(idx)
        except ValueError:
            continue
        def _int(tag: str) -> int:
            t = item.findtext(tag)
            try:
                return int(t) if t is not None else 0
            except ValueError:
                return 0
        out[ui] = (_int("ubGraphicType"), _int("ubGraphicNum"))
    with _ITEMS_LOCK:
        _ITEMS_CACHE[install_root] = (mtime, out)
    return out


def _bigitems_stem(gtype: int, gnum: int) -> str:
    """BIGITEMS filename stem. type 0 -> gun<NN>; else p<type>item<NN>.
    <NN> = 2-digit only when <10 (gun09, gun24, p1item05, p1item96)."""
    nn = f"{gnum:02d}"
    return f"gun{nn}" if gtype == 0 else f"p{gtype}item{nn}"


def _resolve_bigitem_bytes(install_root: str, stem: str) -> Optional[bytes]:
    root = Path(install_root)
    fname = f"{stem}.STI"
    # loose-first, case-insensitive.
    for base in (root / "Data-1.13" / "BigItems", root / "Data" / "BigItems"):
        if not base.is_dir():
            continue
        try:
            for child in base.iterdir():
                if child.name.lower() == fname.lower() and child.is_file():
                    return child.read_bytes()
        except OSError:
            pass
    # SLF fallback: Data/Bigitems.slf member BIGITEMS/<stem>.STI.
    slf_path = root / "Data" / "Bigitems.slf"
    if slf_path.is_file():
        try:
            from mercwizard_core.install_context import _open_slf_cached
            slf = _open_slf_cached(slf_path)
            internal = f"/BIGITEMS/{stem}.STI"
            if slf is not None and slf.isfile(internal):
                return slf.openbin(internal, "r").read()
        except Exception:
            pass
    return None


def render_item_graphic(install_root: str, us_item: int) -> Optional[bytes]:
    """PNG of `us_item`'s BIGITEMS graphic, or None (unknown item / missing STI)."""
    graphics = _load_item_graphics(install_root)
    gfx = graphics.get(us_item)
    if gfx is None:
        return None
    stem = _bigitems_stem(gfx[0], gfx[1])
    data = _resolve_bigitem_bytes(install_root, stem)
    if data is None:
        return None
    try:
        from mercwizard_core.sti_decode import decode_sti_frame_to_png
        return decode_sti_frame_to_png(data, 0)
    except Exception:
        return None
