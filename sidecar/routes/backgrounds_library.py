"""Cross-mod Background LIBRARY — browse the harvested union + assign into an install.

`Headless_Compiler/backgrounds_crossmod_corpus/harvest_unique_backgrounds.py`
sweeps every JA2 mod on disk and dedups Backgrounds at the ENTRY level (by
name + scored-field signature), emitting `unique_backgrounds.json` — ~2000+
unique `<BACKGROUND>` entries, each with its full verbatim block XML, provenance
(which mods it appears in), and a BP balance score + heuristic cluster.

This router is a thin READ layer over that JSON plus one WRITE action:

    GET  /backgrounds/library          browse (lightweight rows + meta)
    GET  /backgrounds/library/{uid}    one entry incl. its full <BACKGROUND> block
    POST /backgrounds/library/assign   renumber the chosen block into a free <500
                                       index and splice it into the active
                                       install's Backgrounds.xml (verbatim)

The library JSON is located via MERCWIZARD_BG_LIBRARY_JSON → a source-relative
walk (see _resolve_library_json). A frozen build has no source tree to walk,
so it needs the env var; when the JSON is absent every
endpoint degrades to 503 so a checkout without the corpus
doesn't crash the sidecar.

The assign action reuses the SAME safe write machinery as `routes/backgrounds.py`:
the cross-process install lock, an auto-snapshot (restore via the Backups page),
and `inject/backgrounds_xml.upsert_background_block()` — a verbatim splice that
preserves nested `<drugtypes>`/`<drugitems>` and unknown mod columns. It NEVER
ships a raw library index: ids >= 500 are silently dropped by the engine, so the
block's `<uiIndex>` is renumbered to `next_free_index()` (1..499) first.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mercwizard_core import backgrounds_schema as schema
from mercwizard_core.backup import snapshot
from mercwizard_core.cross_lock import cross_process_install_root_lock
from mercwizard_core.inject import backgrounds_xml as bg_xml

from .backgrounds import _resolve_write_path
from .roster import _resolve_install
from .state import get_state

router = APIRouter()


# ── Library JSON location + cache ───────────────────────────────────────────
# Resolve in priority order: explicit env override -> source-tree relative
# (sidecar/routes/ -> parents[3] == the project root, correct when run from
# source). First existing file wins. The corpus lives outside this repo and is
# not bundled, so a frozen build finds it only via MERCWIZARD_BG_LIBRARY_JSON;
# without it every library endpoint degrades to its 503.
def _resolve_library_json() -> Path:
    env = os.environ.get("MERCWIZARD_BG_LIBRARY_JSON")
    if env:
        return Path(env)
    candidates = [
        Path(__file__).resolve().parents[3] / "Headless_Compiler"
        / "backgrounds_crossmod_corpus" / "unique_backgrounds.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[-1]   # last resort — drives the 503 "not found" message


_LIBRARY_JSON = _resolve_library_json()

# Parse once and hold in-process, keyed by the file's mtime so a re-harvest is
# picked up without a sidecar restart. ~2000 rows is cheap to keep resident.
_cache_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": None, "by_uid": None}

# Full <uiIndex>N</uiIndex> element (open+value+close) — replaced wholesale when
# renumbering a library block onto a free in-range index.
_UIINDEX_FULL = re.compile(r"<uiIndex>\s*-?\d+\s*</uiIndex>")

_BG_ERR_STATUS = {
    "BACKGROUND_NOT_FOUND": 404,
    "DUPLICATE_INDEX": 409,
    "INDEX_TAKEN": 409,
    "TABLE_FULL": 409,
    "TEMPLATE_PROTECTED": 400,
    "INVALID_INDEX": 400,
    "INVALID_BLOCK": 400,
}


def _load_library() -> dict:
    """Return the cached `{mtime, data, by_uid}`; (re)load on mtime change.

    503 when the harvested JSON isn't present (e.g. a checkout without the
    corpus, or harvest_unique_backgrounds.py not yet run)."""
    if not _LIBRARY_JSON.is_file():
        raise HTTPException(status_code=503, detail={
            "error": "BG_LIBRARY_NOT_FOUND",
            "message": (
                f"Background library not found ({_LIBRARY_JSON.name}). Run "
                "harvest_unique_backgrounds.py in backgrounds_crossmod_corpus, "
                "or set MERCWIZARD_BG_LIBRARY_JSON."
            ),
        })
    mtime = _LIBRARY_JSON.stat().st_mtime
    with _cache_lock:
        if _cache["mtime"] == mtime and _cache["data"] is not None:
            return _cache
        try:
            data = json.loads(_LIBRARY_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=503, detail={
                "error": "BG_LIBRARY_UNREADABLE",
                "message": f"Could not read the background library: {e}",
            })
        rows = data.get("backgrounds", [])
        _cache.update(mtime=mtime, data=data, by_uid={r["uid"]: r for r in rows})
        return _cache


# ── Request models ──────────────────────────────────────────────────────────

class LibraryAssignBody(BaseModel):
    uid: str
    # Omit to auto-pick the lowest free id (recommended). Provide to claim a
    # specific id (1..499); a taken id returns 409.
    ui_index: Optional[int] = None
    # When true, the new entry is placed physically last so it (and any
    # currently-hidden higher ids) appear in IMP character creation.
    make_imp_selectable: bool = False


# ── Read ────────────────────────────────────────────────────────────────────

@router.get("/backgrounds/library")
def list_library() -> dict:
    """Browse the library. Returns lightweight rows (no `block_xml` — fetch the
    detail endpoint for that) + the corpus `meta`. The frontend fetches the
    whole ~2000-row union once and filters client-side; the former server-side
    filter params (q/source/cluster/kind/min_net/max_net/limit/offset) were
    never sent by any caller and duplicated the client filtering with
    different semantics — removed rather than maintained as a second,
    drifting implementation."""
    cache = _load_library()
    data = cache["data"]
    rows = data.get("backgrounds", [])
    light = [{k: v for k, v in r.items() if k != "block_xml"} for r in rows]
    return {"meta": data.get("meta", {}), "total": len(light),
            "count": len(light), "backgrounds": light}


@router.get("/backgrounds/library/{uid}")
def get_library_entry(uid: str) -> dict:
    """One library entry, including its full verbatim `<BACKGROUND>` block."""
    cache = _load_library()
    entry = cache["by_uid"].get(uid)
    if entry is None:
        raise HTTPException(status_code=404, detail={
            "error": "BACKGROUND_NOT_FOUND",
            "message": f"No library background with uid {uid}.",
        })
    return entry


# ── Write (assign into the active install) ──────────────────────────────────

@router.post("/backgrounds/library/assign")
def assign_from_library(
    body: LibraryAssignBody,
    install_id: Optional[str] = Query(default=None),
) -> dict:
    """Renumber the chosen library block to a free in-range id and splice it
    (verbatim) into the install's Backgrounds.xml. Returns the assigned id —
    the caller wires it into a merc's `usBackground`."""
    cache = _load_library()
    entry = cache["by_uid"].get(body.uid)
    if entry is None:
        raise HTTPException(status_code=404, detail={
            "error": "BACKGROUND_NOT_FOUND",
            "message": f"No library background with uid {body.uid}.",
        })

    info = _resolve_install(install_id)
    state = get_state()
    _ctx, write_path = _resolve_write_path(info)

    if body.ui_index is not None and not (
        schema.TEMPLATE_INDEX < body.ui_index <= schema.MAX_INDEX
    ):
        raise HTTPException(status_code=400, detail={
            "error": "INVALID_INDEX",
            "message": f"uiIndex must be {schema.TEMPLATE_INDEX + 1}..{schema.MAX_INDEX}.",
        })

    with cross_process_install_root_lock(info.path), state.write_lock:
        snap = snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=[write_path], reason="background_library_assign",
        )
        try:
            target_index = (body.ui_index if body.ui_index is not None
                            else bg_xml.next_free_index(write_path))
            block, n_subs = _UIINDEX_FULL.subn(
                f"<uiIndex>{target_index}</uiIndex>", entry["block_xml"], count=1)
            if n_subs != 1:
                raise HTTPException(status_code=500, detail={
                    "error": "CORPUS_BLOCK_MALFORMED",
                    "message": "Library entry's <uiIndex> didn't match the "
                               "renumber pattern; refusing to assign at an "
                               "unverified index.",
                })
            result = bg_xml.upsert_background_block(
                write_path, block_text=block,
                make_imp_selectable=body.make_imp_selectable,
            )
        except bg_xml.BackgroundError as e:
            raise HTTPException(
                status_code=_BG_ERR_STATUS.get(e.code, 400),
                detail={"error": e.code, "message": e.message},
            )

    # Upsert re-derives the id from the block text (looser regex than the
    # renumber pattern above) — verify it landed at the intended index so a
    # divergence can never return ok:true with a wrong id.
    if result.get("ui_index") != target_index:
        raise HTTPException(status_code=500, detail={
            "error": "INDEX_MISMATCH",
            "message": f"Assigned uiIndex {result.get('ui_index')} != "
                       f"intended {target_index}.",
        })

    # An explicit, already-taken id makes upsert a no-op (it never clobbers a
    # shared entry) — surface that rather than silently doing nothing.
    if body.ui_index is not None and not result.get("created", True):
        raise HTTPException(status_code=409, detail={
            "error": "INDEX_TAKEN",
            "message": f"uiIndex {target_index} already exists in this install's Backgrounds.xml.",
        })

    return {"ok": True, "backup_id": snap.id, "uid": body.uid,
            "name": entry["name"], **result}
