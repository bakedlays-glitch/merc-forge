"""Graphics-stack routes — verify/deploy the golden cnc-ddraw + ReShade
config (see mercwizard_core/graphics.py for the model)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from mercwizard_core import backup as backup_mod
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.graphics import (
    GraphicsDeployError,
    deploy_graphics,
    graphics_status,
)
from mercwizard_core.ini_editor import game_running

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


@router.get("/graphics/status")
def status(install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    return {"components": graphics_status(info.path)}


@router.post("/graphics/deploy")
def deploy(install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    if game_running(info.exe_path.name):
        raise HTTPException(status_code=409, detail={
            "error": "GAME_RUNNING",
            "message": "Close JA2 before deploying graphics config."})
    state = get_state()
    with cross_process_install_lock(info.id), state.write_lock:
        targets = [info.path / n for n in
                   ("ddraw.ini", "ReShade.ini", "ja2_remastered.ini")]
        entry = backup_mod.snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=[p for p in targets if p.is_file()],
            reason="graphics_deploy")
        created = [p for p in targets if not p.is_file()]
        try:
            result = deploy_graphics(info.path)
        except GraphicsDeployError as e:
            raise HTTPException(status_code=422, detail={
                "error": e.code, "message": str(e)})
        if created:
            backup_mod.record_files_created(
                entry.id, info.id, [p for p in created if p.is_file()])
    result["backup_id"] = entry.id
    return result
