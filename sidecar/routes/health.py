"""Health check + sidecar info."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from mercwizard_core import __version__ as core_version
from mercwizard_core.vfs import compute_vfs_mismatch

from .state import get_state

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Cheap liveness check.

    Must answer within the shell watchdog's 3 s per-ping timeout; 3
    consecutive misses (pinging every 2 s, so ~6 s) trigger a respawn.

    Per bug #12 the sidecar no longer runs a background install scan —
    detection is purely user-driven via the FirstRun VFS Selector
    Wizard — so this endpoint dropped the `scan_in_progress` /
    `last_scan_error` / `scan_progress` fields it used to expose.

    `vfs_mismatch` (bug-review B5) reports whether the active install's
    bound `vfs_config_path` disagrees with what Ja2.ini's VFS_CONFIG_INI
    line currently says. After bug #11 removed the auto-apply on
    activation, the user can switch between sibling registrations
    (same path, different config) without Ja2.ini following along —
    silently routing wizard writes into a mod content layer the engine
    isn't reading. The Hub banner watches this flag and offers an
    explicit Apply-VFS button when it's true.
    """
    state = get_state()
    # Call state.active() ONCE — calling it twice as a truthiness check
    # then dereference races against concurrent set_active(None) /
    # remove_install(active_id), which could make the second call
    # return None and 500 the endpoint on .id. The Tauri shell pings
    # /health every 2s and respawns on 3 consecutive failures, so a
    # 500 burst here triggers a sidecar respawn for a benign race.
    # Sweep bug-review finding.
    active = state.active()
    vfs_mismatch: Optional[bool] = None
    if active is not None:
        try:
            vfs_mismatch = compute_vfs_mismatch(active.path, active.vfs_config_path)
        except Exception:
            # Belt-and-suspenders: the watchdog must not fall over on a
            # diagnostic-flag exception. Treat any failure as "can't tell"
            # rather than 500ing /health.
            vfs_mismatch = None
    return {
        "ok": True,
        "version": core_version,
        "install_count": state.cached_install_count(),
        "active_install_id": active.id if active is not None else None,
        "vfs_mismatch": vfs_mismatch,
    }


@router.get("/version")
def version() -> dict:
    return {"core": core_version, "tool": "MercWizard", "tool_version": "2.0.0"}
