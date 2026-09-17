"""Cross-process file lock for mutating operations on a specific JA2 install.

`state.write_lock` is a `threading.RLock` and only serializes
mutations WITHIN a single sidecar process. Two MercWizard instances running
against the same install (e.g. the user accidentally launched the app
twice) can each pass their in-process lock and race on shared files. The
most dangerous case is `AIMBIOS.EDT` / `MERCBIOS.EDT`: both instances ask
`compute_aim_bio_id` for a free slot, both get the same answer, both write
their merc's bio at the same offset — one silently overwrites the other.

This module provides a `cross_process_install_lock(install_id)` context
manager that takes an exclusive `portalocker.Lock` on a per-install
sentinel file under `%APPDATA%\\MercWizard\\<install-id>\\.write.lock`.

Lock-order convention: callers acquire the cross-process lock BEFORE the
in-process `state.write_lock`. That way every MercWizard instance respects
the same order and avoids cross-process deadlocks.

Usage:
    with cross_process_install_lock(info.id):
        with state.write_lock:
            # mutating writes here

The 2 nested context managers are intentional: outer = cross-process,
inner = in-process. Don't combine into one because the in-process lock is
shared across routes that don't need a cross-process scope (read paths,
state.json mutations, etc.).
"""
from __future__ import annotations

import hashlib
import os
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

import portalocker


def _lock_dir(install_id: str) -> Path:
    """Where the per-install lock sentinel lives.

    Mirrors `backup.py:_appdata_root` so the lock file ends up alongside
    the backups dir — `%APPDATA%\\MercWizard\\<install-id>\\.write.lock`
    on Windows.
    """
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "MercWizard" if appdata else Path.home() / ".config" / "MercWizard"
    return base / install_id


def _lock_path(install_id: str) -> Path:
    return _lock_dir(install_id) / ".write.lock"


def normalized_install_root_lock_scope(install_root: str | Path) -> str:
    """Stable lock scope for one physical install root.

    ``cross_process_install_lock(install_id)`` is retained for legacy route
    callers, but profile IDs are not a safe mutation scope: two VFS profiles
    can point at the same install. New MapForge mutations must use this root
    scope instead.
    """
    root = Path(install_root).resolve()
    normalized = os.path.normcase(os.path.normpath(str(root)))
    return "root-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@contextmanager
def cross_process_install_root_lock(install_root: str | Path) -> Iterator[None]:
    """Lock a physical install root, independent of its VFS profile ID."""
    with cross_process_install_lock(normalized_install_root_lock_scope(install_root)):
        yield


@contextmanager
def cross_process_install_roots_lock(*install_roots: str | Path) -> Iterator[None]:
    """Lock one or more physical roots in deterministic order.

    Roots are resolved and de-duplicated before acquiring any lock.  This is
    important for operations such as a cross-profile move: two profile IDs
    can name the same install, and attempting to acquire that root twice is
    both unnecessary and unsafe on platforms where advisory locks are not
    reliably re-entrant.  Distinct roots are acquired in normalized path
    order so two opposing transfers cannot deadlock by taking A/B vs B/A.
    """
    unique: dict[str, Path] = {}
    for install_root in install_roots:
        resolved = Path(install_root).resolve()
        key = os.path.normcase(os.path.normpath(str(resolved)))
        unique.setdefault(key, resolved)

    with ExitStack() as stack:
        for key in sorted(unique):
            stack.enter_context(cross_process_install_root_lock(unique[key]))
        yield


_MAP_LEASE_GUARD = threading.Lock()
_MAP_LEASES: set[str] = set()


class MapSessionLease:
    """Lifetime lease for one writable canonical map path."""
    def __init__(self, key: str, lock: portalocker.Lock):
        self._key, self._lock, self._released = key, lock, False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._lock.release()
        finally:
            with _MAP_LEASE_GUARD:
                _MAP_LEASES.discard(self._key)


def acquire_writable_map_session_lease(install_root: str | Path,
                                       canonical_map_path: str | Path) -> MapSessionLease:
    """Acquire a non-blocking cross-process lifetime lease for one map."""
    root = Path(install_root).resolve()
    path = Path(canonical_map_path).resolve()
    key_source = os.path.normcase(str(root)) + "\0" + os.path.normcase(str(path))
    key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
    with _MAP_LEASE_GUARD:
        if key in _MAP_LEASES:
            raise RuntimeError("writable map session already open")
        lock_dir = _lock_dir(normalized_install_root_lock_scope(root)) / "map_sessions"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(
            str(lock_dir / f"{key}.lock"), mode="a",
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            timeout=0, fail_when_locked=True,
        )
        try:
            lock.acquire()
        except portalocker.exceptions.LockException as e:
            raise RuntimeError("writable map session already open") from e
        _MAP_LEASES.add(key)
    return MapSessionLease(key, lock)


@contextmanager
def cross_process_install_lock(install_id: str) -> Iterator[None]:
    """Take an exclusive lock on this install's mutation sentinel.

    Blocks while another sidecar process holds the same install's lock.
    Releases on context exit (even on exception).

    No timeout — mutations are short-lived (typically <5s for a save) and
    the user-visible alternative (timeout → confusing error) is worse than
    just waiting for the other instance to finish.

    Idempotent within a single process via portalocker's reentrancy
    behavior: if the same process tries to acquire twice, the second
    acquire succeeds (advisory locks on Windows are not strictly
    reentrant, but portalocker handles the file-descriptor reuse). The
    typical usage pattern (one outer acquire per route handler) doesn't
    nest anyway.
    """
    lock_dir = _lock_dir(install_id)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(install_id)
    # portalocker.Lock provides a context-manager API. EXCLUSIVE flag = the
    # default; specified here for clarity.
    with portalocker.Lock(
        str(lock_path),
        mode="a",
        flags=portalocker.LOCK_EX,
    ):
        yield
