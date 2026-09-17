"""RPC on-map placement — read/write InitialProfile lines in GameInit.lua.

Surfaces the ``rpc_placement`` Lua manager over HTTP so the merc editor can
drop a Type=3 RPC onto a sector without hand-editing Scripts/GameInit.lua.

The tool owns a marked block inside ``InitNPCs()`` and never touches
hand-authored lines outside it (except ``adopt``, a deliberate move of one
hand line into the block). Callers pass a sector CODE ("A9") — the column/row
mapping (the transposition footgun) is done server-side.

Placement takes effect at NEW-GAME init only (that's when InitNPCs runs);
the frontend surfaces that caveat.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mercwizard_core import backup, rpc_placement as rp
from mercwizard_core.cross_lock import cross_process_install_root_lock
from mercwizard_core.install_context import make_install_context

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


class PlacementModel(BaseModel):
    profile: int
    sector: str
    col: int
    row: int
    z: int
    gridno: int
    label: str = ""
    managed: bool = True

    @classmethod
    def of(cls, p: rp.Placement, *, managed: bool) -> "PlacementModel":
        return cls(
            profile=p.profile, sector=p.sector, col=p.col, row=p.row,
            z=p.z, gridno=p.gridno, label=p.label, managed=managed,
        )


class PlacementsResponse(BaseModel):
    install_id: str
    lua_path: str
    lua_exists: bool
    managed: list[PlacementModel]
    handAuthored: list[PlacementModel]


class SetPlacementRequest(BaseModel):
    profile: int
    sector: str            # e.g. "A9"
    gridno: int
    z: int = 0
    label: str = ""


class SetPlacementResponse(BaseModel):
    placement: PlacementModel
    warnings: list[str]
    backup_id: Optional[str] = None


def _read_lua(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # A general user's GameInit.lua may be cp1252/latin-1; round-trip it.
        return path.read_text(encoding="latin-1")


def _lua_paths(ctx) -> tuple[Path, Path]:
    """(canonical read path, write target). Equal on non-VFS installs."""
    return ctx.game_init_lua_path(for_write=False), ctx.game_init_lua_path(for_write=True)


def _current_text(read_path: Path, write_path: Path) -> str:
    """Read what the engine effectively sees: the write layer once we've
    written it, else the canonical read layer (to seed a first edit)."""
    seed = write_path if write_path.exists() else read_path
    return _read_lua(seed)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".mwtmp")
    # newline="" so the eol chars the manager emitted survive verbatim.
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def _write_with_backup(info, write_path: Path, new_text: str) -> str:
    """Snapshot -> atomic write -> record creation for rollback. Returns backup id."""
    existed = write_path.exists()
    entry = backup.snapshot(
        install_root=info.path,
        install_id=info.id,
        files_to_back_up=[write_path],
        reason="rpc-placement",
    )
    _atomic_write(write_path, new_text)
    if not existed:
        # snapshot skipped the (absent) file; record it so restore can delete it.
        backup.record_files_created(entry.id, info.id, [write_path])
    return entry.id


@router.get("/rpc/placements", response_model=PlacementsResponse)
def list_placements(install_id: Optional[str] = Query(default=None)) -> PlacementsResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    read_path, write_path = _lua_paths(ctx)
    parsed = rp.read_placements(_current_text(read_path, write_path))
    return PlacementsResponse(
        install_id=info.id,
        lua_path=str(write_path),
        lua_exists=write_path.exists() or read_path.exists(),
        managed=[PlacementModel.of(p, managed=True) for p in parsed["managed"]],
        handAuthored=[PlacementModel.of(p, managed=False) for p in parsed["handAuthored"]],
    )


@router.put("/rpc/placements", response_model=SetPlacementResponse)
def set_placement(
    req: SetPlacementRequest,
    install_id: Optional[str] = Query(default=None),
) -> SetPlacementResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    state = get_state()

    try:
        col, row = rp.sector_to_colrow(req.sector)
        rp.validate_gridno(req.gridno)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    read_path, write_path = _lua_paths(ctx)
    with cross_process_install_root_lock(info.path), state.write_lock:
        text = _current_text(read_path, write_path)

        warnings: list[str] = []
        dup = [p for p in rp.read_placements(text)["handAuthored"] if p.profile == req.profile]
        if dup:
            warnings.append(
                f"Profile {req.profile} is already placed by a hand-authored line "
                f"(sector {dup[0].sector}, gridno {dup[0].gridno}) outside the managed "
                f"block. The engine will use both; remove the hand line or adopt it."
            )

        new_text = rp.apply_upsert(text, req.profile, col, row, req.z, req.gridno, req.label)
        backup_id = _write_with_backup(info, write_path, new_text)

    placed = rp.Placement(req.profile, col, row, req.z, req.gridno, req.label)
    return SetPlacementResponse(
        placement=PlacementModel.of(placed, managed=True),
        warnings=warnings,
        backup_id=backup_id,
    )


@router.post("/rpc/placements/{profile}/adopt", response_model=SetPlacementResponse)
def adopt_placement(
    profile: int,
    install_id: Optional[str] = Query(default=None),
) -> SetPlacementResponse:
    """Move a hand-authored placement for `profile` into the managed block."""
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    state = get_state()

    read_path, write_path = _lua_paths(ctx)
    with cross_process_install_root_lock(info.path), state.write_lock:
        text = _current_text(read_path, write_path)
        hand = [p for p in rp.read_placements(text)["handAuthored"] if p.profile == profile]
        if not hand:
            raise HTTPException(
                status_code=404,
                detail=f"No hand-authored placement for profile {profile} to adopt.",
            )
        new_text = rp.apply_adopt(text, profile)
        backup_id = _write_with_backup(info, write_path, new_text)

    return SetPlacementResponse(
        placement=PlacementModel.of(hand[0], managed=True),
        warnings=[],
        backup_id=backup_id,
    )


@router.delete("/rpc/placements/{profile}", response_model=SetPlacementResponse)
def remove_placement(
    profile: int,
    install_id: Optional[str] = Query(default=None),
) -> SetPlacementResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    state = get_state()

    read_path, write_path = _lua_paths(ctx)
    with cross_process_install_root_lock(info.path), state.write_lock:
        text = _current_text(read_path, write_path)
        existing = [p for p in rp.read_placements(text)["managed"] if p.profile == profile]
        if not existing:
            raise HTTPException(status_code=404, detail=f"No managed placement for profile {profile}.")
        new_text = rp.apply_remove(text, profile)
        backup_id = _write_with_backup(info, write_path, new_text)

    return SetPlacementResponse(
        placement=PlacementModel.of(existing[0], managed=True),
        warnings=[],
        backup_id=backup_id,
    )
