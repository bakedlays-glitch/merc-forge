"""In-process state shared across route modules.

The wizard is single-user and single-writer. We hold:
- The currently-active install (set via POST /installs/active)
- A re-entrant write lock to enforce sequential mutations
- The set of installs the user has manually registered

State persists to `%APPDATA%/MercWizard/state.json` across sidecar
restarts. The Tauri shell's watchdog respawns the sidecar on health-ping
failures; without disk persistence the user's install registration and
active selection get wiped every respawn (bug-sweep #39).

Per bug #12 (May 2026): auto-detection probes (Steam / GOG / common
paths) have been removed. The install set is purely user-driven: only
folders the user picks via the Tauri file dialog + the VFS Selector
Wizard end up here.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from mercwizard_core.install_detect import (
    InstallInfo,
    build_install_info_for_vfs_config,
    validate_install,
)


def _state_file() -> Path:
    """Where the persisted state lives: `%APPDATA%/MercWizard/state.json`."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "MercWizard" if appdata else Path.home() / ".config" / "MercWizard"
    return base / "state.json"


def _persistence_enabled() -> bool:
    """Disable disk persistence during pytest runs.

    Otherwise the module-level singleton (`_state = SidecarState()`) loads
    %APPDATA%/MercWizard/state.json at import time and writes back on
    every `set_active`/`register_manual_install`. Tests using tmp_path
    fixtures end up persisting fake install paths into the user's REAL
    appdata — exactly what happened on 2026-05-13 when test_move_merc0's
    `fake_install` ID survived into a production launch and stuck the
    Tauri shell in a respawn loop.

    Detection: pytest sets PYTEST_CURRENT_TEST while running, and the
    `pytest` module is imported into sys.modules.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if os.environ.get("MERCWIZARD_NO_PERSIST"):
        return False
    import sys
    if "pytest" in sys.modules:
        return False
    return True


class SidecarState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._installs: dict[str, InstallInfo] = {}
        self._active_install_id: Optional[str] = None
        self._scan_done = False
        # Entries waiting to be re-validated after a sidecar restart.
        # Populated by `_load_from_disk`; consumed by `refresh_installs`.
        # Each tuple is (install_root, vfs_config_path_or_None).
        self._persisted_installs: list[tuple[Path, Optional[Path]]] = []
        # Active install id from last session — applied after the
        # next refresh validates a matching install.
        self._pending_active_install_id: Optional[str] = None
        # Load persisted state at startup so a watchdog respawn doesn't
        # wipe the user's registered installs + active selection. Cheap —
        # just deserializes paths, no validation.
        self._load_from_disk()

    @property
    def write_lock(self) -> threading.RLock:
        return self._lock

    # ── Persistence ─────────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        """Read the previous session's installs + active selection from
        `%APPDATA%/MercWizard/state.json` (if it exists).

        Cheap on purpose: no validation, no I/O against the JA2 installs
        themselves -- those happen in the next `refresh_installs` call.

        Disabled under pytest via `_persistence_enabled()` so test runs
        can't bleed into a user's real state.json.

        Format v1 (legacy): {install_paths: [str, ...], active_install_id}
        Format v2 (current): {installs: [{path, vfs_config_path?}, ...], active_install_id}
        """
        if not _persistence_enabled():
            return
        state_file = _state_file()
        if not state_file.is_file():
            return
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Corrupt or unreadable -- treat as no prior state. The next
            # save will overwrite it with a fresh snapshot.
            return
        if not isinstance(data, dict):
            return
        # v2 format
        installs = data.get("installs")
        if isinstance(installs, list):
            for raw in installs:
                if not isinstance(raw, dict):
                    continue
                p = raw.get("path")
                if not isinstance(p, str) or not p.strip():
                    continue
                vc = raw.get("vfs_config_path")
                vc_path = Path(vc) if isinstance(vc, str) and vc else None
                self._persisted_installs.append((Path(p), vc_path))
        # v1 fallback — paths only, no vfs_config binding.
        paths = data.get("install_paths")
        if isinstance(paths, list):
            for raw in paths:
                if isinstance(raw, str) and raw.strip():
                    self._persisted_installs.append((Path(raw), None))
        active = data.get("active_install_id")
        if isinstance(active, str) and active:
            self._pending_active_install_id = active

    def _save_to_disk_impl(self) -> None:
        """Write the current install paths + active id to state.json.

        v2 format: persist each install's (path, vfs_config_path) tuple
        so the user's mod-profile choice survives a sidecar restart.

        Caller is expected to hold `self._lock`.

        Failure modes now LOG instead of silently passing — bug #83
        traced the "no active install mid-session" CTD to silent write
        failures (file locked by another sidecar instance, parent dir
        missing, permissions, etc.) that left state.json frozen at
        whatever it had on disk before the session.
        """
        import logging
        log = logging.getLogger(__name__)
        state_file = _state_file()
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.error("state.json parent mkdir failed: %s (%s)", state_file.parent, e)
            return
        # Dedupe by (path, vfs_config_path) so multiple registrations of
        # the same path with different configs become distinct entries
        # (intentional — user picked AIMNAS AND Wildfire) but identical
        # tuples collapse.
        seen: set[tuple[str, Optional[str]]] = set()
        installs: list[dict] = []
        for info in self._installs.values():
            p = str(info.path)
            v = str(info.vfs_config_path) if info.vfs_config_path else None
            key = (p, v)
            if key in seen:
                continue
            seen.add(key)
            installs.append({"path": p, "vfs_config_path": v})
        data = {
            "version": 2,
            "installs": installs,
            "active_install_id": self._active_install_id,
        }
        try:
            state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            log.error(
                "state.json write FAILED: %s (%s). Active install id = %r will "
                "be lost on next sidecar restart. Possible cause: file locked "
                "by another sidecar instance, drive read-only, or AV scanner.",
                state_file, e, self._active_install_id,
            )

    def _save_to_disk(self) -> None:
        """Persist current state to disk if persistence is enabled.

        Caller is expected to hold `self._lock` (we don't re-acquire to
        avoid deadlocks since many callers already do).
        """
        if not _persistence_enabled():
            return
        self._save_to_disk_impl()

    # ── Install validation ─────────────────────────────────────────────

    def refresh_installs(self) -> list[InstallInfo]:
        """Re-validate registered + persisted install paths.

        Per bug #12, no auto-detection runs — this only validates paths
        the user has registered. Persisted-from-disk entries (loaded at
        startup) get validated on the first call so the sidecar can bind
        its port quickly. Manually-registered installs already in memory
        get re-validated to catch paths that have moved or broken.

        (Pre-fix this accepted an `extra_paths` kwarg that was no longer
        wired to any caller — the Settings rescan-extra-paths surface it
        advertised was never implemented. Dropped to match what callers
        actually do. Bug-review finding C7.)
        """
        with self._lock:
            persisted_snapshot = list(self._persisted_installs)
            existing_snapshot = dict(self._installs)

        new_map: dict[str, InstallInfo] = {}
        seen_keys: set[tuple[Path, Optional[Path]]] = set()

        def _add_validated(path: Path, vfs_cfg: Optional[Path]) -> None:
            try:
                resolved = Path(path).resolve()
            except OSError:
                return
            key = (resolved, vfs_cfg)
            if key in seen_keys:
                return
            seen_keys.add(key)
            info = validate_install(Path(path))
            if not info.valid:
                return
            if vfs_cfg is not None and vfs_cfg.is_file():
                info = build_install_info_for_vfs_config(info, vfs_cfg)
            new_map[info.id] = info

        for path, vfs_cfg in persisted_snapshot:
            _add_validated(path, vfs_cfg)

        # Preserve currently-registered entries that aren't in the new
        # validated set — auto-detected entries no longer exist (bug
        # #12), so every existing entry is a manual registration the
        # user actively chose. Re-validate to catch broken paths.
        for iid, existing in existing_snapshot.items():
            if iid in new_map:
                continue
            try:
                revalidated = validate_install(existing.path)
            except Exception:
                continue
            if not revalidated.valid:
                continue
            if existing.vfs_config_path is not None and existing.vfs_config_path.is_file():
                preserved = build_install_info_for_vfs_config(
                    revalidated, existing.vfs_config_path,
                )
                new_map[preserved.id] = preserved
            else:
                new_map[revalidated.id] = revalidated

        with self._lock:
            # `self._installs` may have grown during the unlocked
            # validate_install() loop — concurrent register_manual_install
            # calls write directly into self._installs. Drop only the
            # entries that existed at snapshot time AND aren't in new_map
            # (paths that no longer validate), then overlay new_map.
            drops = set(existing_snapshot) - set(new_map)
            for k in drops:
                self._installs.pop(k, None)
            for k, v in new_map.items():
                self._installs[k] = v
            self._scan_done = True
            # Apply the persisted active selection now that we've validated
            # the install pool. If the previously-active id no longer
            # validates (path deleted between sessions), drop it silently.
            if (
                self._active_install_id is None
                and self._pending_active_install_id is not None
                and self._pending_active_install_id in self._installs
            ):
                self._active_install_id = self._pending_active_install_id
            self._pending_active_install_id = None
            self._persisted_installs = []  # consumed
            self._save_to_disk()
            return list(self._installs.values())

    # ── Read paths ─────────────────────────────────────────────────────

    def list_installs(self) -> list[InstallInfo]:
        """Return cached installs. Triggers a one-time re-validate of
        persisted entries on the first call so the user's previous
        session's installs show up again after a sidecar restart.
        """
        with self._lock:
            needs_refresh = (
                not self._scan_done and bool(self._persisted_installs)
            )
        if needs_refresh:
            self.refresh_installs()
        with self._lock:
            if not self._scan_done:
                self._scan_done = True
            return list(self._installs.values())

    def cached_install_count(self) -> int:
        """Count of currently-known installs WITHOUT triggering validation.
        Used by /health which must not block."""
        with self._lock:
            return len(self._installs)

    def installs(self) -> dict[str, InstallInfo]:
        """Snapshot of the current registered installs by id.

        Used by routes that need to look up a specific install (e.g.
        `apply_vfs_config`) without triggering a re-validate.
        """
        with self._lock:
            return dict(self._installs)

    def get_install(self, install_id: str) -> Optional[InstallInfo]:
        with self._lock:
            if not self._scan_done and self._persisted_installs:
                pass  # fall through to refresh below (drops lock)
            else:
                return self._installs.get(install_id)
        self.refresh_installs()
        with self._lock:
            return self._installs.get(install_id)

    # ── Mutating paths (all lock-guarded) ──────────────────────────────

    def register_manual_install(
        self,
        path: Path,
        preferred_vfs_config: Optional[Path] = None,
    ) -> InstallInfo:
        """Validate `path` and register it as an install.

        When `preferred_vfs_config` is supplied (from the VFS Selector
        Wizard in FirstRun), the returned InstallInfo is bound to that
        config: it gets a fresh id derived from (path, vfs_config) so
        registering the same folder with different configs produces
        distinct entries the user can switch between.
        """
        info = validate_install(path)
        if info.valid and preferred_vfs_config is not None:
            info = build_install_info_for_vfs_config(info, preferred_vfs_config)
        if info.valid:
            resolved = info.path.resolve()
            with self._lock:
                # Dedup by (resolved_path, vfs_config_path): re-registering
                # the same folder + config returns the existing entry, but
                # the same folder with a DIFFERENT config is a new entry.
                for existing in self._installs.values():
                    try:
                        if (
                            existing.path.resolve() == resolved
                            and existing.vfs_config_path == info.vfs_config_path
                        ):
                            return existing
                    except OSError:
                        continue
                self._installs[info.id] = info
                self._scan_done = True
                self._save_to_disk()
        return info

    def remove_install(self, install_id: str) -> bool:
        with self._lock:
            if install_id in self._installs:
                del self._installs[install_id]
                if self._active_install_id == install_id:
                    self._active_install_id = None
                self._save_to_disk()
                return True
            return False

    def set_active(self, install_id: str) -> bool:
        with self._lock:
            if install_id not in self._installs:
                return False
            info = self._installs[install_id]
            self._active_install_id = install_id
            self._save_to_disk()
        # The previous behavior automatically rewrote the install's
        # `Ja2.ini` VFS_CONFIG_INI line on every activation. This was a
        # silent destructive mutation: it switched the game engine's
        # save-game directory + mod content stack to whatever VFS
        # config MercForge thought the install "should" use, hiding
        # the user's existing saves and reverting any manual VFS edits
        # the user had made. Bug #11 in MERC_FORGE_BUG_LIST.md
        # documents the user-visible symptom (Jakub Vito's "saves
        # disappeared after activating an install" report).
        #
        # New behavior: activation is purely a local-state change.
        # JA2.ini is NEVER touched unless the user explicitly clicks
        # the Hub's "Apply VFS to JA2.ini" action (which calls the
        # `apply_vfs_config` endpoint) or picks a different config in
        # the VFS Selector Wizard during install registration.
        if info.vfs_config_path is not None:
            import logging
            logging.getLogger(__name__).info(
                "Install %s has vfs_config_path=%s — NOT writing to JA2.ini "
                "automatically (silent VFS mutation disabled per bug #11). "
                "The user can apply this manually if needed.",
                install_id, info.vfs_config_path,
            )
        return True

    # Note: a `_apply_vfs_config_to_ja2_ini` helper previously lived here
    # using backslash-separated paths. Dead code — the only live caller
    # is now the explicit `apply_vfs_config` endpoint in routes/installs.py,
    # which builds the path string itself. The duplicate helper used
    # the OPPOSITE slash direction from the live endpoint, which the
    # bug-review (finding B7) flagged as an inconsistency. Removed
    # rather than reconciled because nothing called it. If a future
    # consumer needs in-process VFS application, call
    # mercwizard_core.vfs.write_vfs_config_to_ja2_ini directly with
    # the slash convention the engine actually accepts (TBD via
    # parent CLAUDE.md — Wasteland docs note backslashes are the
    # Ja2.ini Windows-native convention; the live endpoint currently
    # writes forward slashes pending engine-side verification).

    def active(self) -> Optional[InstallInfo]:
        with self._lock:
            if self._active_install_id is None:
                return None
            return self._installs.get(self._active_install_id)


# Singleton accessor
_state = SidecarState()


def get_state() -> SidecarState:
    return _state
