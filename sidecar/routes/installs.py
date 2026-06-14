"""Install registration, listing, and VFS-config selection."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mercwizard_core.install_detect import is_stock_vfs_config
from mercwizard_core.mod_detect import detect_mod
from mercwizard_core.vfs import (
    _read_vfs_config_path_from_ja2_ini,
    find_vfs_configs,
    mod_name_from_vfs_config,
)

from .state import get_state


def _enrich_install_dict(info_dict: dict, info_path: Path, vfs_config_path) -> dict:
    """Attach `mod_id` / `mod_display` / confidence / evidence to a
    serialized InstallInfo, derived from either the folder's name or
    (when this entry represents a specific vfs_config the user picked
    in the VFS Selector Wizard) the mod name in the config filename."""
    mod = detect_mod(info_path)
    info_dict["mod_id"] = mod.id.value
    info_dict["mod_display"] = mod.display_name
    info_dict["mod_confidence"] = mod.confidence
    info_dict["mod_evidence"] = list(mod.evidence)
    # vfs_config override: the config filename is a stronger signal than
    # the folder name when we have it.
    if vfs_config_path is not None:
        mod_name = mod_name_from_vfs_config(Path(vfs_config_path))
        if mod_name:
            info_dict["mod_display"] = mod_name
            info_dict["mod_evidence"] = [
                f"VFS config file: {Path(vfs_config_path).name}",
                *info_dict["mod_evidence"],
            ]
    # Engine-version fallback: when folder-name heuristics drew an
    # "unknown" but the engine_version reader DID find a `r7605` /
    # `1.13.0.<rev>` signal, the install is clearly a legitimate 1.13
    # build — don't flag it as Unknown.
    if (
        info_dict["mod_id"] == "unknown"
        and info_dict.get("engine_version")
    ):
        info_dict["mod_id"] = "vanilla"
        info_dict["mod_display"] = "Vanilla 1.13"
        info_dict["mod_confidence"] = 0.55
        info_dict["mod_evidence"] = [
            f"Engine version: {info_dict['engine_version']} ({info_dict.get('engine_version_source')})",
            *info_dict["mod_evidence"],
        ]
    return info_dict

router = APIRouter()


class InstallPayload(BaseModel):
    path: str
    # Optional: bind a specific vfs_config.*.ini to the registration.
    # Set by the FirstRun VFS Selector Wizard after the user picks a
    # mod profile from the install's detected configs.
    preferred_vfs_config_path: Optional[str] = None


class ActivePayload(BaseModel):
    install_id: str


class ScanVfsConfigsPayload(BaseModel):
    path: str


@router.get("/installs")
def list_installs() -> list[dict]:
    state = get_state()
    out = []
    for info in state.list_installs():
        d = info.to_dict()
        out.append(_enrich_install_dict(d, info.path, info.vfs_config_path))
    return out


@router.post("/installs/scan-vfs-configs")
def scan_vfs_configs(payload: ScanVfsConfigsPayload) -> dict:
    """Enumerate the vfs_config.*.ini files in a candidate install folder.

    Called by FirstRun's VFS Selector Wizard between folder pick and
    `addInstall`. Surfaces each detected config with a display-friendly
    mod name and flags which one (if any) is currently active in
    `JA2.ini`. The frontend uses this list to render the picker — same
    pick = silent register, different pick = save-game warning modal +
    apply-vfs-config.
    """
    path = Path(payload.path)
    if not path.is_dir():
        raise HTTPException(400, {
            "error": "PATH_NOT_DIR",
            "message": f"Not a directory: {path}",
        })
    install_root = path.resolve()
    configs = find_vfs_configs(install_root)

    # Find Ja2.ini and read its active VFS_CONFIG_INI line (if any).
    active_rel: Optional[Path] = None
    for name in ("Ja2.ini", "ja2.ini", "JA2.INI"):
        candidate = install_root / name
        if candidate.is_file():
            active_rel = _read_vfs_config_path_from_ja2_ini(candidate)
            break

    # Resolve the active relative path against the detected config set
    # so the picker knows which entry to flag as the current choice.
    active_resolved: Optional[Path] = None
    if active_rel is not None:
        target = (install_root / str(active_rel).replace("\\", "/")).resolve(strict=False)
        for cfg in configs:
            try:
                if cfg.resolve() == target:
                    active_resolved = cfg
                    break
            except OSError:
                continue

    config_entries: list[dict] = []
    for cfg in configs:
        try:
            rel = cfg.resolve().relative_to(install_root)
            rel_str = str(rel).replace("\\", "/")
        except (OSError, ValueError):
            rel_str = str(cfg)
        config_entries.append({
            "path": str(cfg),
            "relative_path": rel_str,
            "mod_name": mod_name_from_vfs_config(cfg) or cfg.stem,
            "is_active": cfg == active_resolved,
            "is_stock": is_stock_vfs_config(cfg),
        })

    return {
        "install_path": str(install_root),
        "configs": config_entries,
        "active_relative_path": (
            str(active_rel).replace("\\", "/") if active_rel else None
        ),
    }


@router.post("/installs")
def add_install(payload: InstallPayload) -> dict:
    state = get_state()
    preferred: Optional[Path] = None
    if payload.preferred_vfs_config_path:
        candidate = Path(payload.preferred_vfs_config_path)
        if not candidate.is_file():
            raise HTTPException(status_code=400, detail={
                "error": "VFS_CONFIG_NOT_FOUND",
                "message": f"preferred_vfs_config_path does not exist: {candidate}",
            })
        preferred = candidate
    info = state.register_manual_install(
        Path(payload.path),
        preferred_vfs_config=preferred,
    )
    if not info.valid:
        # Mirror info.errors into `message` so formatApiError surfaces the
        # specific reason(s) — e.g. "No JA2 executable found...; Couldn't
        # find TableData/MercProfiles.xml..." — rather than just showing the
        # generic friendly text. The `errors` array stays for clients that
        # want to render each reason individually.
        raise HTTPException(status_code=400, detail={
            "error": "INVALID_INSTALL",
            "errors": info.errors,
            "message": "; ".join(info.errors) if info.errors else "validation failed",
        })
    d = info.to_dict()
    return _enrich_install_dict(d, info.path, info.vfs_config_path)


@router.delete("/installs/{install_id}")
def remove_install(install_id: str) -> dict:
    state = get_state()
    removed = state.remove_install(install_id)
    return {"removed": removed}


@router.post("/installs/active")
def set_active(payload: ActivePayload) -> dict:
    state = get_state()
    ok = state.set_active(payload.install_id)
    if not ok:
        raise HTTPException(status_code=404, detail={
            "error": "INSTALL_NOT_FOUND",
            "install_id": payload.install_id,
        })
    active = state.active()
    if active is not None:
        # Fire-and-forget background warm: pre-bake the roster portrait
        # sheet + prime the roster/parse caches so the user's first roster
        # view after switching installs is a cache hit, not a ~1 s bake.
        # Non-blocking daemon thread — does not delay this response and
        # does not reintroduce the watchdog-endangering startup crawl that
        # bug #12 removed.
        from .roster import warm_install
        warm_install(active.id, active.path)
    return {"active_install_id": active.id if active else None}


@router.post("/installs/refresh")
def refresh() -> list[dict]:
    state = get_state()
    state.refresh_installs()
    return list_installs()


class ApplyVfsResult(BaseModel):
    install_id: str
    ja2_ini_path: str
    vfs_config_written: str
    backup_path: str | None
    already_active: bool


@router.post("/installs/{install_id}/apply-vfs-config",
             response_model=ApplyVfsResult)
def apply_vfs_config(install_id: str) -> ApplyVfsResult:
    """Write this install's `vfs_config_path` into its `Ja2.ini`.

    Used to be called automatically from `set_active` (bug #11), which
    silently mutated the user's `Ja2.ini` and redirected their saved
    games. Now it's an EXPLICIT user action: either the Hub's Apply-VFS
    button or the FirstRun VFS Selector Wizard's "Confirm & Update VFS"
    confirmation calls this endpoint after a clear save-game warning.

    Returns information about what was written + the backup path
    (a `.mwbak` is taken on the first mutation per file). Idempotent
    — calling twice with the same install just rewrites the same line.
    """
    from mercwizard_core.vfs import write_vfs_config_to_ja2_ini
    state = get_state()
    info = state.installs().get(install_id)
    if info is None:
        raise HTTPException(404, {"error": "INSTALL_NOT_FOUND",
                                  "install_id": install_id})
    if info.vfs_config_path is None:
        raise HTTPException(400, {
            "error": "NO_VFS_CONFIG",
            "message": "This install has no specific vfs_config; nothing to apply.",
        })
    ja2_ini = None
    for name in ("Ja2.ini", "ja2.ini", "JA2.INI"):
        candidate = info.path / name
        if candidate.is_file():
            ja2_ini = candidate
            break
    if ja2_ini is None:
        raise HTTPException(500, {
            "error": "JA2_INI_NOT_FOUND",
            "message": f"No Ja2.ini under {info.path}",
        })
    try:
        rel = info.vfs_config_path.resolve().relative_to(info.path.resolve())
    except (OSError, ValueError):
        rel = info.vfs_config_path
    rel_str = str(rel).replace("\\", "/")
    # Detect whether THIS specific vfs_config line is already active in
    # Ja2.ini — not whether a .mwbak has ever been taken (pre-fix used
    # `backup_existed` as a proxy, which conflated "we've ever applied
    # a VFS to this install" with "the current request would be a
    # no-op"). Read the file, scan for the first non-comment
    # VFS_CONFIG_INI line, and compare the value (whitespace +
    # slash-direction tolerant). TODO #13 fix.
    backup_path = ja2_ini.with_suffix(ja2_ini.suffix + ".mwbak")
    already_active = False
    try:
        for line in ja2_ini.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip().lower() != "vfs_config_ini":
                continue
            # Normalize both sides: trim whitespace, collapse slashes.
            current = value.strip().replace("\\", "/")
            target = rel_str.replace("\\", "/")
            already_active = current == target
            break
    except OSError:
        pass
    write_vfs_config_to_ja2_ini(ja2_ini, rel_str)
    return ApplyVfsResult(
        install_id=install_id,
        ja2_ini_path=str(ja2_ini),
        vfs_config_written=rel_str,
        backup_path=str(backup_path) if backup_path.exists() else None,
        already_active=already_active,
    )
