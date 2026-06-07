"""Game launcher route."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from mercwizard_core.game import launch_ja2
from mercwizard_core.ini_editor import game_running

from .roster import _resolve_install

router = APIRouter()


@router.post("/game/launch")
def launch(install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    try:
        pid = launch_ja2(info.path, exe_name=info.exe_path.name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail={"error": "EXE_NOT_FOUND", "message": str(e)})
    return {"ok": True, "pid": pid, "exe": str(info.exe_path)}


@router.get("/game/status")
def status(install_id: str | None = Query(default=None)) -> dict:
    """Is the game running? Detection is by process IMAGE NAME (tasklist),
    so with multiple installs sharing `ja2.exe` this cannot pin the
    specific install — callers should treat `running=true` as 'some JA2
    with this exe name is up'. Deliberately conservative: that's the
    right bias for the INI editor's write guard."""
    info = _resolve_install(install_id)
    return {
        "running": game_running(info.exe_path.name),
        "exe_name": info.exe_path.name,
        "by": "image_name",
    }
