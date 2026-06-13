"""R6 trust fix — the MapForge session store must NEVER evict a dirty
session (its unsaved edits live only in memory, so eviction would silently
lose the user's work). Idle/LRU eviction may only drop CLEAN sessions.

Eviction reads just `id` / `dirty` / `last_used_at`, so the tests use
lightweight duck-typed stand-ins instead of parsing a real .dat.
"""
from __future__ import annotations

import time
import types

from routes.mapforge import _SessionStore, _SESSION_IDLE_TIMEOUT


def _fake(sid: str, dirty: bool, age_s: float):
    s = types.SimpleNamespace()
    s.id = sid
    s.dirty = dirty
    s.last_used_at = time.time() - age_s
    return s


def test_idle_evict_keeps_dirty_drops_clean_idle():
    store = _SessionStore()
    old = _SESSION_IDLE_TIMEOUT + 100
    store._sessions = {
        "clean_old": _fake("clean_old", False, old),   # evict (clean + idle)
        "dirty_old": _fake("dirty_old", True, old),     # KEEP (dirty, never evict)
        "clean_new": _fake("clean_new", False, 1.0),    # KEEP (fresh)
    }
    store._evict_idle_locked()
    assert set(store._sessions) == {"dirty_old", "clean_new"}


def test_idle_evict_keeps_dirty_even_when_all_idle():
    store = _SessionStore()
    old = _SESSION_IDLE_TIMEOUT + 100
    store._sessions = {
        "d1": _fake("d1", True, old),
        "d2": _fake("d2", True, old),
    }
    store._evict_idle_locked()
    assert set(store._sessions) == {"d1", "d2"}


def test_clean_idle_session_is_dropped():
    store = _SessionStore()
    store._sessions = {"a": _fake("a", False, _SESSION_IDLE_TIMEOUT + 1)}
    store._evict_idle_locked()
    assert store._sessions == {}
