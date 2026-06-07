"""Backup management routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mercwizard_core import backup as backup_mod
from mercwizard_core.cross_lock import cross_process_install_lock

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


class BackupReason(BaseModel):
    reason: str = "manual"


class RestorePayload(BaseModel):
    backup_id: str


@router.get("/backup")
def list_backups(install_id: str | None = Query(default=None)) -> list[dict]:
    info = _resolve_install(install_id)
    return [e.to_dict() for e in backup_mod.list_backups(info.id)]


@router.post("/backup")
def take_snapshot(payload: BackupReason, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    # Snapshot the canonical merc-touch file set for a few key slots — but
    # really, manual backups snapshot the whole TableData directory
    from mercwizard_core.install_context import make_install_context
    ctx = make_install_context(info.path)
    files = [
        ctx.profiles_xml_path(),
        ctx.aim_xml_path(),
        ctx.gear_xml_path(),
        ctx.aim_bios_edt_path(),
        ctx.merc_bios_edt_path(),
    ]
    with cross_process_install_lock(info.id), state.write_lock:
        entry = backup_mod.snapshot(info.path, info.id, files, payload.reason)
    return entry.to_dict()


@router.post("/backup/restore")
def restore(payload: RestorePayload, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    try:
        with cross_process_install_lock(info.id), state.write_lock:
            count = backup_mod.restore(payload.backup_id, info.id, info.path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail={"error": "BACKUP_NOT_FOUND", "message": str(e)})
    return {"ok": True, "files_restored": count}


@router.delete("/backup/{backup_id}")
def delete_backup(backup_id: str, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    removed = backup_mod.delete_backup(backup_id, info.id)
    return {"ok": True, "removed": removed}
