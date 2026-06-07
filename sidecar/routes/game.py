"""Game launcher route."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from mercwizard_core.game import launch_ja2

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
