"""Phase 1 (A5 safety foundation): transactional edit rollback.

Tests the `_snapshot_tiles` / `_restore_tiles` mechanism that makes
`apply_edits` atomic — a mid-batch failure must leave the session
exactly as it was, with no half-applied paste/paint.

The bottom section drives the real `run_generator` route to prove the
generator-run path has the SAME transactional guarantee: a generator
that fails partway (its own exception OR an EditOpError like the
15-entry nibble cap) must roll the whole run back, leaving `sess.parsed`
untouched — no half-applied map stranded in the session.
"""
import asyncio
import copy
import json
import threading
from pathlib import Path

from mercwizard_core.mapforge import generators as gen_mod
from mercwizard_core.mapforge.generators import Generator
from routes.mapforge import (
    MapForgeSession,
    _restore_tiles,
    _session_store,
    _snapshot_tiles,
    run_generator,
)

_LAYERS = ("land", "obj", "struct", "shadow", "roof", "onroof")
_PLURAL = {"land": "land", "obj": "objs", "struct": "structs",
           "shadow": "shadows", "roof": "roofs", "onroof": "onroofs"}


def _mk(world_max=4):
    d = {
        "n_per_tile": {k: [0] * world_max for k in _LAYERS},
        "rooms": [0] * world_max,
        "heights": [0] * world_max,
    }
    for k in _LAYERS:
        d[_PLURAL[k]] = [[] for _ in range(world_max)]
    return d


def test_snapshot_restore_round_trips_all_fields():
    p = _mk()
    snap = _snapshot_tiles(p, [0, 1])
    # Mutate exactly the captured tiles across every field type.
    p["structs"][0] = [(36, 1)]
    p["n_per_tile"]["struct"][0] = 1
    p["objs"][0] = [(12, 7)]
    p["n_per_tile"]["obj"][0] = 1
    p["rooms"][1] = 5
    p["heights"][1] = 80
    _restore_tiles(p, snap)
    assert p["structs"][0] == []
    assert p["n_per_tile"]["struct"][0] == 0
    assert p["objs"][0] == []
    assert p["n_per_tile"]["obj"][0] == 0
    assert p["rooms"][1] == 0
    assert p["heights"][1] == 0


def test_restore_only_touches_snapshotted_tiles():
    p = _mk()
    snap = _snapshot_tiles(p, [0])      # only tile 0 captured
    p["rooms"][2] = 9                    # mutate an UN-snapshotted tile
    p["structs"][0] = [(1, 1)]
    p["n_per_tile"]["struct"][0] = 1
    _restore_tiles(p, snap)
    assert p["structs"][0] == []         # tile 0 restored
    assert p["rooms"][2] == 9            # tile 2 left alone


def test_snapshot_captures_deep_copies_not_references():
    # Restoring must not alias the live list (a later mutation of the
    # restored list must not retroactively change the snapshot).
    p = _mk()
    p["structs"][0] = [(5, 5)]
    p["n_per_tile"]["struct"][0] = 1
    snap = _snapshot_tiles(p, [0])
    p["structs"][0].append((6, 6))      # mutate the live list in place
    _restore_tiles(p, snap)
    assert p["structs"][0] == [(5, 5)]   # snapshot value, not the appended one


# ────────────────────────────────────────────────────────────────────────
#  Generator-run path: transactional snapshot/rollback (route level)
# ────────────────────────────────────────────────────────────────────────
#
# `run_generator` streams a generator's ops into the session under the
# lock. Unlike apply_edits the touched set isn't known up front, so it
# snapshots each tile lazily on first touch and rolls the whole run back
# on any failure. These tests drive the real route (via a hand-built
# session + a monkeypatched generator) and assert the session is left
# byte-identical to its pre-run state on failure — and correctly
# committed on success (so rollback can't be firing spuriously).


def _mk_parsed(rows=2, cols=2):
    """A parsed dict shaped like a real session: 6 layer grids + their
    singular per-tile count nibbles + map-global `counts` totals + rooms
    + heights. Enough for `_apply_single_edit` and `_snapshot_tiles`."""
    n = rows * cols
    p = {
        "rows": rows,
        "cols": cols,
        "n_per_tile": {k: [0] * n for k in _LAYERS},
        "counts": {k: 0 for k in _LAYERS},
        "rooms": [0] * n,
        "heights": [0] * n,
    }
    for k in _LAYERS:
        p[_PLURAL[k]] = [[] for _ in range(n)]
    return p


def _fake_session(parsed):
    """Build a MapForgeSession without touching disk (its __init__ reads
    + parses a .dat). __slots__ means we set each attribute the route
    reads explicitly."""
    sess = MapForgeSession.__new__(MapForgeSession)
    sess.id = "test-gen-rollback-session"
    sess.dat_path = Path("nonexistent.dat")
    sess.xml_path = Path("nonexistent.xml")   # tileset-meta block degrades to None
    sess.tileset = 0
    sess.parsed = parsed
    sess.original_bytes = b""
    sess.dirty = False
    sess.edit_count = 0
    sess.created_at = 0.0
    sess.last_used_at = 0.0
    sess.read_only = False
    sess.source_uri = ""
    sess._lock = threading.Lock()
    return sess


def _drain(resp):
    """Collect every NDJSON chunk from a StreamingResponse. The route
    hands StreamingResponse a sync generator, which Starlette wraps as an
    async iterator (iterate_in_threadpool); drain it on a fresh loop."""
    async def _collect():
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk if isinstance(chunk, str) else chunk.decode())
        return out
    return asyncio.run(_collect())


def _run(monkeypatch, parsed, gen):
    """Drive `run_generator` with `gen` against a session wrapping
    `parsed`. Returns (session, parsed_events)."""
    sess = _fake_session(parsed)
    _session_store._sessions[sess.id] = sess
    # run_generator does `from ...generators import get` at call time, so
    # patching the module attribute swaps in our generator.
    monkeypatch.setattr(gen_mod, "get", lambda name: gen)
    try:
        resp = run_generator(sess.id, name=gen.name)
        chunks = _drain(resp)
    finally:
        _session_store._sessions.pop(sess.id, None)
    events = [json.loads(c) for c in chunks if c.strip()]
    return sess, events


class _TwoOpGenerator(Generator):
    """Two valid `add` ops on two different tiles, then a clean finish."""
    name = "test-two-op"
    label = "Two op (test)"
    description = "test"
    params = []

    def iter_ops(self, ctx, params):
        yield {"phase": "test", "status": "start", "total": 2, "label": "go"}
        yield {"x": 0, "y": 0, "op": "add", "layer": "structs", "slot": 36, "sub": 1}
        yield {"x": 1, "y": 0, "op": "add", "layer": "objs", "slot": 12, "sub": 7}
        yield {"phase": "test", "status": "done", "label": "done"}


class _ThrowMidRunGenerator(Generator):
    """Two valid ops, then the generator raises — the route must roll
    both ops back."""
    name = "test-throw"
    label = "Throw (test)"
    description = "test"
    params = []

    def iter_ops(self, ctx, params):
        yield {"phase": "test", "status": "start", "total": 2, "label": "go"}
        yield {"x": 0, "y": 0, "op": "add", "layer": "structs", "slot": 36, "sub": 1}
        yield {"x": 1, "y": 0, "op": "add", "layer": "objs", "slot": 12, "sub": 7}
        raise RuntimeError("boom — generator failed partway")


class _OverCapGenerator(Generator):
    """16 `add`s onto ONE tile. The 16th trips the 15-entry nibble cap
    inside _apply_single_edit (EditOpError) — the headline mid-run
    failure mode."""
    name = "test-overcap"
    label = "Over-cap (test)"
    description = "test"
    params = []

    def iter_ops(self, ctx, params):
        yield {"phase": "test", "status": "start", "total": 16, "label": "go"}
        for _ in range(16):
            yield {"x": 0, "y": 0, "op": "add", "layer": "structs", "slot": 36, "sub": 1}


def test_generator_run_commits_on_success(monkeypatch):
    """Sanity guard: a generator that finishes cleanly DOES apply +
    commit. Without this, a rollback that fired spuriously would still
    pass the failure tests below."""
    parsed = _mk_parsed(2, 2)
    sess, events = _run(monkeypatch, parsed, _TwoOpGenerator())

    assert sess.parsed["structs"][0] == [(36, 1)]
    assert sess.parsed["objs"][1] == [(12, 7)]
    assert sess.parsed["n_per_tile"]["struct"][0] == 1
    assert sess.parsed["n_per_tile"]["obj"][1] == 1
    assert sess.parsed["counts"]["struct"] == 1
    assert sess.parsed["counts"]["obj"] == 1
    assert sess.edit_count == 2
    assert sess.dirty is True

    final = events[-1]
    assert final["done"] is True and final["ok"] is True
    assert final["applied"] == 2
    # Op events are streamed on success (the canvas mirrors them).
    assert len([e for e in events if "op" in e]) == 2


def test_generator_throw_midrun_rolls_back_session(monkeypatch):
    """A generator that raises partway leaves `sess.parsed` byte-identical
    to its pre-run state — the two ops it emitted first are rolled back."""
    parsed = _mk_parsed(2, 2)
    before = copy.deepcopy(parsed)
    sess, events = _run(monkeypatch, parsed, _ThrowMidRunGenerator())

    assert sess.parsed == before          # whole map unchanged
    assert sess.edit_count == 0           # not bumped
    assert sess.dirty is False            # never marked dirty

    final = events[-1]
    assert final["done"] is True and final["ok"] is False
    assert final["error"] == "GENERATOR_FAILED"
    assert final["applied"] == 0          # rolled back → 0 live
    # The buffered op events were never streamed, so the frontend's atlas
    # mirror also sees nothing — backend + frontend stay consistent.
    assert not [e for e in events if "op" in e]


def test_generator_editoperror_midrun_rolls_back_session(monkeypatch):
    """The 15-entry nibble cap (EditOpError) raised by the 16th add must
    roll back the 15 that already landed on the tile."""
    parsed = _mk_parsed(2, 2)
    before = copy.deepcopy(parsed)
    sess, events = _run(monkeypatch, parsed, _OverCapGenerator())

    assert sess.parsed == before
    assert sess.parsed["structs"][0] == []          # tile cleared back
    assert sess.parsed["n_per_tile"]["struct"][0] == 0
    assert sess.parsed["counts"]["struct"] == 0     # global total restored
    assert sess.edit_count == 0
    assert sess.dirty is False

    final = events[-1]
    assert final["done"] is True and final["ok"] is False
    assert final["error"] == "EDIT_OP_ERROR"
    assert final["applied"] == 0
