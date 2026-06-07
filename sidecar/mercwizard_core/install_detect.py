"""Validate user-picked JA2 1.13 installation folders.

The wizard is purely manual-add (per bug #12): the user picks an install
folder via the Tauri file picker and we validate it here. No more
auto-detection — the previous Steam / GOG / common-paths probes were
fragile (false positives on non-1.13 folders, false negatives on the
custom dirs where 1.13 players actually keep their installs) and the
silent background scan caused the confusing "card expansion" UX where
secondary mod variants popped up seconds after the user added a path.

Validation per candidate:
- Has a JA2 executable at the root (JA2.exe / ja2_1.13.exe / ja2_v1.13.exe / JA2_113.exe)
- Has MercProfiles.xml SOMEWHERE in the VFS chain (mod content layer,
  vanilla 1.13 layer, or bare Data/ for pre-VFS installs)
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


JA2_EXE_NAMES = (
    "JA2.exe",
    "ja2.exe",
    "ja2_1.13.exe",
    "ja2_v1.13.exe",
    "JA2_113.exe",
    "ja2_113.exe",
    "Jagged Alliance 2.exe",
)


@dataclass
class InstallInfo:
    """A validated JA2 1.13 installation.

    A single physical install folder can be REGISTERED multiple times
    with different `vfs_config_path` bindings (e.g. the user wants to
    work on both AIMNAS and Wildfire profiles of the same Russian
    modpack). Each registration produces its own InstallInfo with a
    distinct id derived from (path, vfs_config_path).
    """
    id: str                       # Stable ID for cross-session reference
    path: Path                    # Install root (folder containing JA2.exe)
    exe_path: Path                # The JA2 executable found
    data_root: Path               # <path>/Data-1.13
    valid: bool                   # Did all validation checks pass?
    errors: list[str] = field(default_factory=list)
    last_played: Optional[float] = None  # mtime of JA2.exe; sortable
    # Specific vfs_config file this entry represents. None = use whatever
    # Ja2.ini's VFS_CONFIG_INI says (legacy / single-mod installs).
    vfs_config_path: Optional[Path] = None
    # Kept for frontend compatibility. With manual-only registration
    # every registered entry is its own primary (no auto-expansion to
    # secondary variants).
    is_primary: bool = True
    # Short revision label (`r7605` / `1.13.0.8748`) derived from the
    # JA2.exe PE resource with a Changelog_*.txt fallback. None when no
    # source agrees on a version.
    engine_version: Optional[str] = None
    engine_version_source: Optional[str] = None  # "exe" | "changelog_source" | "changelog_data"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": str(self.path),
            "exe_path": str(self.exe_path),
            "data_root": str(self.data_root),
            "valid": self.valid,
            "errors": self.errors,
            "last_played": self.last_played,
            "vfs_config_path": str(self.vfs_config_path) if self.vfs_config_path else None,
            "is_primary": self.is_primary,
            "engine_version": self.engine_version,
            "engine_version_source": self.engine_version_source,
        }


def _make_install_id(path: Path, vfs_config_path: Optional[Path] = None) -> str:
    """Stable, readable ID for an install.

    When `vfs_config_path` is set the mod name from the config filename
    gets folded into the id so different mods registered at the same
    physical install get distinct ids (e.g.
    `ja2_gold_113_rusmix__aimnas_<hash>` vs `..._wildfire_<hash>`).
    """
    import hashlib
    parts = [p for p in path.parts if p][-2:]
    raw = "_".join(parts)
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", raw).lower()
    base = sanitized[:48] or "install"
    # Hash the FULL path + vfs_config so similarly-named installs (and
    # different vfs_configs within one install) get distinct ids even
    # when truncation collapses their visible name.
    key = str(path)
    if vfs_config_path is not None:
        key += "|" + str(vfs_config_path)
        from .vfs import mod_name_from_vfs_config
        mod_name = mod_name_from_vfs_config(vfs_config_path)
        mod_sanitized = re.sub(r"[^A-Za-z0-9_]", "_", mod_name).lower()
        if mod_sanitized:
            base = f"{base[:36]}__{mod_sanitized[:16]}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]
    return f"{base}_{digest}"


def find_exe(install_root: Path) -> Optional[Path]:
    """Find the JA2 executable in the given install root."""
    if not install_root.is_dir():
        return None
    for name in JA2_EXE_NAMES:
        candidate = install_root / name
        if candidate.is_file():
            return candidate
    # Case-insensitive search as fallback
    try:
        for entry in install_root.iterdir():
            if entry.is_file() and entry.name.lower() in {n.lower() for n in JA2_EXE_NAMES}:
                return entry
    except (OSError, PermissionError):
        pass
    return None


def validate_install(install_root: Path) -> InstallInfo:
    """Check a candidate path against the install requirements.

    VFS-aware: validates that `MercProfiles.xml` exists SOMEWHERE in the
    VFS chain (mod content layer, vanilla 1.13 layer, or bare Data/ for
    pre-VFS installs). The old "must exist under Data-1.13/" check passed
    accidentally for modded installs (where the vanilla copy is empty) and
    failed wrongly for mod-only installs that don't ship a Data-1.13 layer.

    Returns an InstallInfo with `valid=True` if everything checks out,
    `valid=False` with descriptive `errors` otherwise.
    """
    install_root = Path(install_root).resolve()
    info = InstallInfo(
        id=_make_install_id(install_root),
        path=install_root,
        exe_path=install_root,
        data_root=install_root / "Data-1.13",
        valid=False,
    )

    if not install_root.is_dir():
        info.errors.append(f"Path does not exist or is not a directory: {install_root}")
        return info

    # Reject anything under the user's temp directory in production.
    # Real installs never live there; a path under TEMP is a
    # test-fixture leak (an orphaned `tempfile.mkdtemp()` directory).
    # 2026-05-25: a user hit this when a stray `tmpl4eaz18_` dir with a
    # placeholder JA2.exe survived a test run and kept re-appearing on
    # the FirstRun install picker. See feedback_tests_must_not_persist_to_user_appdata.md.
    #
    # Skip the filter during pytest runs — pytest's `tmp_path` fixture
    # IS under TEMP, and dozens of tests legitimately call this with
    # synthesized installs there. Same mechanism that state.py's
    # `_persistence_enabled()` uses.
    if not os.environ.get("PYTEST_CURRENT_TEST") and "pytest" not in sys.modules:
        import tempfile as _tempfile
        try:
            temp_root = Path(_tempfile.gettempdir()).resolve()
            if install_root.is_relative_to(temp_root):
                info.errors.append(
                    f"Path is inside the user's temp directory ({temp_root}). "
                    "This is almost certainly a leftover test fixture, not a real install."
                )
                return info
        except (OSError, ValueError):
            # `is_relative_to` raises ValueError on cross-drive paths
            # in older pathlib versions; let those fall through.
            pass

    exe = find_exe(install_root)
    if exe is None:
        info.errors.append(
            f"No JA2 executable found at {install_root}. "
            f"Expected one of: {', '.join(JA2_EXE_NAMES)}"
        )
        return info
    info.exe_path = exe

    try:
        info.last_played = exe.stat().st_mtime
    except OSError:
        pass

    # Best-effort engine/data revision detection. Never fails the install
    # -- if no signal is available, the UI just omits the revision chip.
    try:
        from .engine_version import read_engine_version
        ev = read_engine_version(install_root, exe)
        if ev is not None:
            info.engine_version = ev.display
            info.engine_version_source = ev.source
    except Exception:
        # Defensive: a buggy version reader must not break install detection.
        pass

    # Use the VFS layout to find MercProfiles.xml regardless of which layer
    # holds it. data_root stays at Data-1.13 for backward compatibility with
    # callers that read it directly; new code should use install_context.
    from .install_context import make_install_context
    ctx = make_install_context(install_root)

    # Surface VFS-config errors (SDO case: broken VFS_CONFIG_INI path) as
    # warnings, but don't fail validation — the legacy fallback may still
    # be usable.
    for vfs_err in ctx.layout.errors:
        info.errors.append(f"VFS config issue: {vfs_err}")

    profiles_path = ctx.profiles_xml_path()
    if not profiles_path.is_file():
        info.errors.append(
            f"Couldn't find TableData/MercProfiles.xml anywhere in the install. "
            f"Tried: {profiles_path}"
        )
        return info

    # Set data_root to a sensible value for legacy callers. Prefer Data-1.13
    # if it exists; else use the dir that actually contains MercProfiles.xml.
    data_113 = install_root / "Data-1.13"
    if data_113.is_dir():
        info.data_root = data_113
    else:
        # Walk up from the MercProfiles.xml location to find TableData's parent
        td_parent = profiles_path.parent.parent
        info.data_root = td_parent

    aim_path = ctx.aim_xml_path()
    if not aim_path.is_file():
        info.errors.append(
            "Missing AIMAvailability.xml anywhere in the install "
            "(warning: AIM features will be limited)"
        )
        # Don't fail — some mods may omit it

    info.valid = True
    return info


# Stock vfs_config names that ship in every 1.13 install -- they're the
# base engine config templates a user falls back to, NOT separate mods
# the user added.
_STOCK_VFS_CONFIG_NAMES = frozenset({
    "ja2113",
    "ja2vanilla",
    "ub113",
    "ubvanilla",
})


def is_stock_vfs_config(cfg_path: Path) -> bool:
    """True for the bundled 1.13 fallback configs (JA2113, UB113, etc.).

    Surfaced via the scan-vfs-configs endpoint so the frontend can render
    a "stock fallback" hint next to these entries — they're rarely what
    a modder wants to select.
    """
    from .vfs import mod_name_from_vfs_config
    return mod_name_from_vfs_config(cfg_path).lower() in _STOCK_VFS_CONFIG_NAMES


def build_install_info_for_vfs_config(
    base: InstallInfo, vfs_config_path: Path,
) -> InstallInfo:
    """Clone `base` but bind a specific vfs_config to the entry.

    Used when the user picks a mod profile in the VFS Selector Wizard
    (see `installs.py:scan_vfs_configs`). The returned InstallInfo has
    a fresh id derived from (path, vfs_config_path) so re-registering
    the same folder with a different config produces a distinct entry.
    """
    new_id = _make_install_id(base.path, vfs_config_path=vfs_config_path)
    return InstallInfo(
        id=new_id,
        path=base.path,
        exe_path=base.exe_path,
        data_root=base.data_root,
        valid=base.valid,
        errors=list(base.errors),
        last_played=base.last_played,
        vfs_config_path=vfs_config_path,
        is_primary=True,
        engine_version=base.engine_version,
        engine_version_source=base.engine_version_source,
    )
