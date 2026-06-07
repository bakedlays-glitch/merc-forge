"""Cross-process file lock for mutating operations on a specific JA2 install.

Phase 2.7 fix: `state.write_lock` is a `threading.RLock` and only serializes
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

import os
from contextlib import contextmanager
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
