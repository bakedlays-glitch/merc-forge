"""Roster: list all 256 slots, get details on one."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from PIL import Image

from mercwizard_core.inject import (
    aim_availability,
    profiles_xml,
    starting_gear,
)
from mercwizard_core.roster import load_roster

from .state import get_state

router = APIRouter()

_log = logging.getLogger(__name__)


# ─── Portrait sprite-sheet cache + bake helpers ─────────────────────
# Replaces the N+1 per-slot portrait fetches with ONE bundled PNG +
# metadata response. Same trick MapForge palette uses for tile
# thumbnails (task #22). User feedback: "i want it to be fast".
#
# Cache key: (install_id, profiles_xml_mtime_ns, size). Any merc
# edit bumps profiles_xml mtime → key changes → bake re-runs on
# the next request. The bake itself returns (png_bytes, cells_json)
# where cells_json maps slot → (x, y, w, h) so the frontend can use
# CSS background-position to crop each cell.
#
# Filled-slot-only: empty slots are skipped at bake time. Frontend
# falls back to the "slot number" placeholder for cells that don't
# appear in the cells_json.
import threading

_SHEET_CACHE: dict[tuple[str, int, str], tuple[bytes, dict]] = {}
_SHEET_CACHE_MAX = 4  # 4 entries = 4 size variants OR 4 install switches
# Lock added 2026-05-25: FastAPI runs each handler in a threadpool
# worker. Concurrent roster mounts (e.g. user switches installs while
# the previous install's bake is still running) can race here. Lock
# spans the eviction + insert window so dict can't mutate mid-iter.
_SHEET_CACHE_LOCK = threading.Lock()


def _portrait_sheet_cache_get(
    key: tuple[str, int, str],
) -> Optional[tuple[bytes, dict]]:
    with _SHEET_CACHE_LOCK:
        return _SHEET_CACHE.get(key)


def _portrait_sheet_cache_put(
    key: tuple[str, int, str], value: tuple[bytes, dict],
) -> None:
    with _SHEET_CACHE_LOCK:
        if len(_SHEET_CACHE) >= _SHEET_CACHE_MAX:
            try:
                del _SHEET_CACHE[next(iter(_SHEET_CACHE))]
            except StopIteration:
                pass
        _SHEET_CACHE[key] = value


# ─── On-disk portrait-sheet cache (survives sidecar restarts) ────────
# The in-memory _SHEET_CACHE above is empty on every sidecar launch, so
# the FIRST roster view after each launch always paid the full bake
# (~1.2 s on a 250-merc install, measured 2026-05-31). Persist the baked
# (png, manifest) under %APPDATA%/MercWizard/cache/portrait_sheets/ keyed
# on the SAME (install_id, MercProfiles.xml mtime_ns, size) tuple, so the
# first view after launch loads the cached PNG instantly and only re-bakes
# when the merc data actually changes.
#
# Staleness: the key tracks MercProfiles.xml's mtime, which every
# create/edit/delete bumps. A portrait recompile that rewrites only the
# face STI (not profiles) is caught by an explicit
# invalidate_portrait_sheet_cache() call from the compile route.
def _sheet_cache_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) / "MercWizard" if base else Path.home() / ".config" / "MercWizard"
    return root / "cache" / "portrait_sheets"


# Bump whenever the bake's face-resolution / compositing logic changes, so old
# on-disk sheets (keyed by install+size+MercProfiles-mtime, which do NOT change
# when the sidecar CODE does) get ignored instead of served stale.
_PORTRAIT_CACHE_VERSION = 3  # v3: + BigFace/65/33 fallback for small-face-less NPCs (2026-06-04)


def _disk_key_prefix(install_id: str, size: str) -> str:
    # Stable per (cache-version, install, size); the mtime is the filename
    # SUFFIX so stale versions are glob-prunable.
    return hashlib.md5(
        f"v{_PORTRAIT_CACHE_VERSION}|{install_id}|{size}".encode("utf-8")
    ).hexdigest()


def _disk_sheet_get(
    install_id: str, mtime_ns: int, size: str,
) -> Optional[tuple[bytes, dict]]:
    prefix = _disk_key_prefix(install_id, size)
    d = _sheet_cache_dir()
    png_path = d / f"{prefix}__{mtime_ns}.png"
    json_path = d / f"{prefix}__{mtime_ns}.json"
    try:
        if not (png_path.is_file() and json_path.is_file()):
            return None
        png = png_path.read_bytes()
        manifest = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return None
        return (png, manifest)
    except (OSError, ValueError):
        # Missing, half-written, or corrupt — treat as a miss and let the
        # caller re-bake. A bad cache file must never break the response.
        return None


def _disk_sheet_put(
    install_id: str, mtime_ns: int, size: str, value: tuple[bytes, dict],
) -> None:
    png, manifest = value
    prefix = _disk_key_prefix(install_id, size)
    try:
        d = _sheet_cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        png_path = d / f"{prefix}__{mtime_ns}.png"
        json_path = d / f"{prefix}__{mtime_ns}.json"
        # Temp + atomic replace so a crash mid-write can't leave a half
        # PNG that a later read would serve as if complete.
        tmp_png = d / f"{prefix}__{mtime_ns}.png.tmp"
        tmp_json = d / f"{prefix}__{mtime_ns}.json.tmp"
        tmp_png.write_bytes(png)
        tmp_json.write_text(json.dumps(manifest), encoding="utf-8")
        os.replace(tmp_png, png_path)
        os.replace(tmp_json, json_path)
        # Prune older mtimes for this install+size — keep at most one pair.
        for p in list(d.glob(f"{prefix}__*.png")) + list(d.glob(f"{prefix}__*.json")):
            if p.name not in (png_path.name, json_path.name):
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        # Disk cache is best-effort; a write failure (read-only dir, AV
        # lock, full disk) must not break the portrait response.
        pass


def invalidate_portrait_sheet_cache(install_id: Optional[str] = None) -> None:
    """Drop cached portrait sheets (memory + disk) for an install.

    Call after a write that changes a merc's FACE art without bumping
    MercProfiles.xml's mtime — notably a standalone portrait recompile.
    Create/edit/delete already rewrite MercProfiles.xml (rotating the
    cache key on their own), but calling this is always safe.
    `install_id=None` clears everything.
    """
    with _SHEET_CACHE_LOCK:
        if install_id is None:
            _SHEET_CACHE.clear()
        else:
            for k in [k for k in _SHEET_CACHE if k[0] == install_id]:
                del _SHEET_CACHE[k]
    d = _sheet_cache_dir()
    if not d.is_dir():
        return
    try:
        if install_id is None:
            targets = (
                list(d.glob("*.png")) + list(d.glob("*.json")) + list(d.glob("*.tmp"))
            )
        else:
            targets = []
            for size in _CELL_SIZES:
                prefix = _disk_key_prefix(install_id, size)
                targets += list(d.glob(f"{prefix}__*"))
        for p in targets:
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


# Cell pixel dimensions per size variant. Source STIs are stable
# (vanilla pipeline) but mods occasionally ship oddly-sized faces;
# the bake uses these as the canonical cell size and scales each
# decoded face to fit while preserving aspect.
_CELL_SIZES = {
    "smallface": (48, 43),
    "face_65":   (65, 65),
    "face_33":   (33, 33),
    "bigface":   (106, 122),
}
_GRID_COLS = 16  # 16 cells per row — matches the roster grid layout


def _bake_portrait_sheet(install_path, size: str) -> tuple[bytes, dict]:
    """Compose every filled slot's portrait into one PNG grid.

    Returns (png_bytes, manifest_dict). Manifest shape:
        {
          "size": "smallface",
          "cell_w": 48, "cell_h": 43,
          "sheet_w": 768, "sheet_h": 301,
          "cells": [{"slot": 0, "x": 0, "y": 0, "face_index": 1}, ...],
          "errors": [{"slot": 26, "face_index": 26, "reason": "..."}, ...],
        }

    Decode failures (16-bit STI, missing palette, etc.) are logged and
    EXCLUDED from `cells` — the frontend falls back to the slot-number
    placeholder for those, instead of the garbled multicolor pixels
    a user hit on slots 26/200/201/etc. 2026-05-24.
    """
    from mercwizard_core.install_context import make_install_context
    from mercwizard_core.sti_decode import decode_sti_frame_to_png

    if size not in _CELL_SIZES:
        raise ValueError(f"Unknown size {size}")
    cell_w, cell_h = _CELL_SIZES[size]

    ctx = make_install_context(install_path)
    profiles_path = ctx.profiles_xml_path()
    all_slots = profiles_xml.read_all_slots(profiles_path)

    # Collect (slot, face_index, decoded_RGBA_image) for every filled
    # non-zero-face slot. Decode errors get appended to `errors` and
    # the slot is left off the sheet entirely.
    decoded: list[tuple[int, int, Image.Image]] = []
    errors: list[dict] = []
    for slot in sorted(all_slots.keys()):
        raw = all_slots[slot]
        try:
            face_index = int(raw.get("ubFaceIndex", "0").strip())
        except (ValueError, AttributeError):
            face_index = 0
        # Face index 0 is a real face ONLY for Barry (profile 0); for every
        # other slot it's the "no portrait assigned" default, so skip those.
        if face_index == 0 and slot != 0:
            continue
        result = ctx.face_sti_bytes(face_index, size=size)
        if result is None and size == "smallface":
            # Some NPCs ship only a larger face variant (BigFace / 65 / 33)
            # and no small face. Fall back to those — the bake scales any
            # decoded face down to the cell size below.
            for _fb in ("bigface", "face_65", "face_33"):
                result = ctx.face_sti_bytes(face_index, size=_fb)
                if result is not None:
                    break
        if result is None:
            errors.append({
                "slot": slot, "face_index": face_index,
                "reason": "STI not found (loose or in any SLF)",
            })
            continue
        sti_bytes, _source_id = result
        # decode_sti_frame_to_png returns PNG-encoded bytes; we want a
        # PIL image to composite. Re-decode the PNG into PIL. The
        # round-trip costs ~1ms per slot — small vs the alternative of
        # refactoring decode_sti_frame_to_png to optionally return PIL.
        png_bytes = decode_sti_frame_to_png(sti_bytes, frame_index=0)
        if png_bytes is None:
            errors.append({
                "slot": slot, "face_index": face_index,
                "reason": "decoder returned None (likely 16-bit STI or malformed palette)",
            })
            _log.warning(
                "portrait sheet bake: slot %d face %d (%s) — decode failed",
                slot, face_index, size,
            )
            continue
        try:
            img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        except Exception as e:  # noqa: BLE001
            errors.append({
                "slot": slot, "face_index": face_index,
                "reason": f"PIL re-decode failed: {type(e).__name__}",
            })
            continue
        decoded.append((slot, face_index, img))

    # Pack into a grid. Fixed columns so the layout is stable across
    # roster invalidations (an added merc just appends a cell; nothing
    # else shifts).
    n = len(decoded)
    rows = (n + _GRID_COLS - 1) // _GRID_COLS if n > 0 else 1
    sheet_w = _GRID_COLS * cell_w
    sheet_h = rows * cell_h
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    cells_meta: list[dict] = []
    for idx, (slot, face_index, img) in enumerate(decoded):
        col = idx % _GRID_COLS
        row = idx // _GRID_COLS
        x = col * cell_w
        y = row * cell_h
        # Resize to fit while preserving aspect — most STIs are
        # exactly cell-sized already so this is a no-op for them.
        if img.size != (cell_w, cell_h):
            img = img.resize((cell_w, cell_h), Image.Resampling.NEAREST)
        sheet.paste(img, (x, y), img)
        cells_meta.append({
            "slot": slot, "face_index": face_index,
            "x": x, "y": y,
        })

    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    png_bytes = buf.getvalue()
    manifest = {
        "size": size,
        "cell_w": cell_w, "cell_h": cell_h,
        "sheet_w": sheet_w, "sheet_h": sheet_h,
        "cells": cells_meta,
        "errors": errors,
    }
    return png_bytes, manifest


def _portrait_sheet_bytes_and_meta(
    install_id: str, install_path, size: str,
) -> tuple[bytes, dict]:
    """Get the (png, manifest) pair via a three-tier lookup.

    in-memory cache -> on-disk cache -> bake. Key is
    (install_id, MercProfiles.xml mtime_ns, size), so any create/edit/
    delete (which rewrites MercProfiles.xml) rotates the key on its own;
    a portrait-only recompile is handled by invalidate_portrait_sheet_cache.
    """
    from mercwizard_core.install_context import make_install_context
    ctx = make_install_context(install_path)
    profiles_path = ctx.profiles_xml_path()
    try:
        mtime_ns = profiles_path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    key = (install_id, mtime_ns, size)
    cached = _portrait_sheet_cache_get(key)
    if cached is not None:
        return cached
    # On-disk tier: survives sidecar restarts, so the first roster view
    # after launch skips the ~1 s bake. Warm the in-memory tier from it.
    on_disk = _disk_sheet_get(install_id, mtime_ns, size)
    if on_disk is not None:
        _portrait_sheet_cache_put(key, on_disk)
        return on_disk
    result = _bake_portrait_sheet(install_path, size)
    _portrait_sheet_cache_put(key, result)
    _disk_sheet_put(install_id, mtime_ns, size, result)
    return result


def _portrait_sheet_etag(png_bytes: bytes) -> str:
    h = hashlib.md5(png_bytes[:4096]).hexdigest()[:16]
    return f'"{h}-{len(png_bytes)}"'


def _resolve_install(install_id: str | None):
    state = get_state()
    if install_id:
        info = state.get_install(install_id)
    else:
        info = state.active()
    if info is None:
        raise HTTPException(status_code=400, detail={
            "error": "NO_ACTIVE_INSTALL",
            "message": "Pass ?install_id=... or POST /installs/active first",
        })
    return info


@router.get("/roster")
def get_roster(install_id: str | None = Query(default=None)) -> list[dict]:
    info = _resolve_install(install_id)
    roster = load_roster(info.path)
    return [e.to_dict() for e in roster]


@router.get("/roster/portrait-sheet.png")
def get_roster_portrait_sheet(
    install_id: str | None = Query(default=None),
    size: str = Query(
        default="smallface",
        description="smallface | face_65 | face_33 | bigface",
    ),
) -> Response:
    """One PNG containing every filled slot's portrait, packed into a
    16-column grid. Replaces the N+1 per-slot fetches.

    Fetch this once on roster mount and pair it with
    `/roster/portrait-sheet.json` for the cell offsets. The frontend
    then renders each cell with `background-image` + `background-
    position` — zero per-cell HTTP.
    """
    info = _resolve_install(install_id)
    png_bytes, _manifest = _portrait_sheet_bytes_and_meta(
        info.id, info.path, size,
    )
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            # 5 min — the sheet's fingerprint is keyed on every face STI's
            # mtime via _portrait_sheet_bytes_and_meta, so any merc edit
            # invalidates the on-disk cache anyway. 60 s was overly cautious;
            # the only thing 60-300 s adds is a revalidate roundtrip after a
            # minute of scroll.
            "Cache-Control": "private, max-age=300, must-revalidate",
            "ETag": _portrait_sheet_etag(png_bytes),
        },
    )


@router.get("/roster/portrait-sheet.json")
def get_roster_portrait_sheet_meta(
    install_id: str | None = Query(default=None),
    size: str = Query(default="smallface"),
) -> JSONResponse:
    """Companion JSON for `/roster/portrait-sheet.png` — maps each slot
    to its (x, y) origin inside the sheet, plus the cell dimensions and
    an `errors` list naming slots whose STIs failed to decode (so the
    frontend can render a clean placeholder instead of garbled bytes)."""
    info = _resolve_install(install_id)
    _png, manifest = _portrait_sheet_bytes_and_meta(info.id, info.path, size)
    return JSONResponse(content=manifest, headers={
        "Cache-Control": "private, max-age=300, must-revalidate",
    })


@router.get("/roster/{slot}")
def get_slot(slot: int, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    from mercwizard_core.install_context import make_install_context
    ctx = make_install_context(info.path)
    profiles_path = ctx.profiles_xml_path()
    aim_path = ctx.aim_xml_path()
    gear_path = ctx.gear_xml_path()

    profile = profiles_xml.read_slot(profiles_path, slot)
    if profile is None:
        raise HTTPException(status_code=404, detail={
            "error": "SLOT_EMPTY",
            "slot": slot,
        })
    aim_map = aim_availability.read_all(aim_path)
    gear = starting_gear.read_slot(gear_path, slot)
    return {
        "slot": slot,
        # Translate the engine's b-prefixed growth-modifier tags to the
        # model/frontend field names so the editor shows real values (the
        # frontend's parseProfileToMerc copies keys verbatim and would
        # otherwise see undefined for every growth modifier on a b-tag install).
        "profile": profiles_xml.normalize_profile_tags(profile),
        "aim_binding": aim_map.get(slot).model_dump() if slot in aim_map else None,
        "gear": gear.model_dump() if gear else None,
    }
