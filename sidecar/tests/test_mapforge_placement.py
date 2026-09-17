"""Placement oracle route: tables, per-candidate verdicts, cache mirroring."""
from __future__ import annotations
import json
import threading
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import routes.mapforge as mf
from routes.mapforge import MapForgeSession, _session_store
from mercwizard_core.mapforge_engine.parse_dat_ext import parse_dat_full
from tests.test_mapforge_library import _build_minimal_dat

import routes.mapforge_placement as mp

TRUCK = 79     # t72 vehicle (2-tile JSD in the real tileset)
FENCE = 86     # t72 wirefenc


def _session(tmp_path: Path, structs: dict[int, list[tuple[int, int]]], sid="plc"):
    data = _build_minimal_dat(rows=16, cols=16, tileset=72, structs=structs)
    dat = tmp_path / "L1.dat"; dat.write_bytes(data)
    sess = MapForgeSession.__new__(MapForgeSession)
    sess.id = sid; sess.dat_path = dat; sess.xml_path = tmp_path / "x.xml"; sess.tileset = 72
    sess.parsed = parse_dat_full(data, str(dat)); sess.original_bytes = data; sess.disk_baseline = data
    sess.dirty = False; sess.mutation_seq = 0; sess.autosaved_seq = 0; sess.edit_count = 0
    sess.created_at = 0.0; sess.last_used_at = 0.0; sess.read_only = False; sess.source_uri = ""
    sess.install_id = "t"; sess.closed = False; sess._lock = threading.Lock()
    _session_store._sessions[sid] = sess
    return sess


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setattr(mf, "_require_renderer", lambda: None)
    from fastapi import FastAPI
    app = FastAPI(); app.include_router(mp.router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    _session_store._sessions.pop("plc", None)
    mp._oracle.drop("plc")


def _hc_present() -> bool:
    try:
        mp._headless_compiler_root(); return True
    except mp.SitekitUnavailable:
        return False


# Every test that touches a real sitekit World needs Headless_Compiler on
# disk; skip (not fail) when it's absent so CI without that tree is green.
# test_missing_sitekit_gives_503 is exempt — it monkeypatches `_sitekit`
# itself and never needs the real package.
_needs_sitekit = pytest.mark.skipif(
    not _hc_present(),
    reason="Headless_Compiler/sitekit not found (set MERCWIZARD_HEADLESS_COMPILER)",
)


def _occ_map(w) -> dict[int, list[tuple]]:
    return {g: sorted(i.id for i in lst) for g, lst in w.occ.items() if lst}


@_needs_sitekit
def test_tables_shape(app_client):
    r = app_client.get("/api/v1/mapforge/placement/tables", params={"tileset": 72})
    assert r.status_code == 200
    body = r.json()
    assert body["road_slot"] == 50
    assert body["categories"][str(FENCE)] == "fence"
    assert "TILE" in body["tiers"]
    assert isinstance(body["fences"], dict)
    # The agreed role table: every family carries the six roles.
    for slot in ("15", "77", "86", "89", "104"):
        assert {"ns", "ew", "nw", "ne", "sw", "se"} <= set(body["fences"][slot]), slot
    assert body["fences"]["86"]["corners"] is True


@_needs_sitekit
def test_placement_data_dir_env_override(app_client, tmp_path, monkeypatch):
    """`MERCWIZARD_PLACEMENT_DATA` must win over the source-relative default —
    the frozen-exe ladder (env → source → frozen bundle → fixed path)."""
    (tmp_path / "t72_fences.json").write_text(
        json.dumps({"fences": {"1": {"ns": 1}}}), encoding="utf-8")
    monkeypatch.setenv("MERCWIZARD_PLACEMENT_DATA", str(tmp_path))
    r = app_client.get("/api/v1/mapforge/placement/tables", params={"tileset": 72})
    assert r.status_code == 200
    assert r.json()["fences"] == {"1": {"ns": 1}}


def test_tier_big_blocking_resolves_by_party():
    cats = {"tiers": {"CONTACT": "big-blocking"}}
    assert mp._tier(cats, "CONTACT", "fence", "building") == "advisory"
    assert mp._tier(cats, "CONTACT", "vehicle", "building") == "blocking"
    assert mp._tier(cats, "CONTACT", "fence", "vehicle") == "blocking"
    assert mp._tier({"tiers": {"X": "advisory"}}, "X", None, None) == "advisory"


@_needs_sitekit
def test_check_reports_tile_conflict_and_ok(app_client, tmp_path):
    g = 5 * 16 + 5
    _session(tmp_path, {g: [(TRUCK, 1)]})
    r = app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": [
        {"x": 5, "y": 5, "layer": "structs", "slot": FENCE, "sub": 1},
        {"x": 12, "y": 12, "layer": "structs", "slot": FENCE, "sub": 1},
    ]})
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[0]["ok"] is False and res[0]["test"] == "TILE" and res[0]["tier"] == "blocking"
    assert res[0]["detail"] and "vehicle" in res[0]["detail"]
    assert res[1]["ok"] is True and res[1]["test"] is None


@_needs_sitekit
def test_check_ring_for_big_candidate(app_client, tmp_path):
    """Real geometry (t72_footprints.json, slot 79 sub 1): a truck anchored at
    (10,10) occupies x=5..10, y=9..10. A fence one column west of the
    footprint's west edge at (4,9) is an 8-neighbour but never sits ON the
    footprint, so this must resolve to RING exactly — not TILE (same-tile
    overlap) or CONTACT (only reachable if RING were absent)."""
    fence_g = 9 * 16 + 4
    _session(tmp_path, {fence_g: [(FENCE, 1)]})
    r = app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": [
        {"x": 10, "y": 10, "layer": "structs", "slot": TRUCK, "sub": 1},
    ]})
    body = r.json()["results"][0]
    assert body["ok"] is False and body["test"] == "RING" and body["tier"] == "blocking"


@_needs_sitekit
def test_check_ring_symmetric_for_fence_next_to_truck(app_client, tmp_path):
    """sitekit's own `World.ring_violators(inst)` only fires when the
    CANDIDATE is vehicle/landmark, so a FENCE dropped next to an existing
    TRUCK used to read clean even though sitekit's global `conflicts()` pass
    flags that exact pair either way (Playwright verification finding, fix
    round 2). The truck (79,1)'s JSD footprint runs west of the anchor across
    two rows (for anchor (10,10): x=5..10, y=9..10 — see t72_footprints.json);
    one row further north (y=8) 8-neighbours the footprint's north edge
    without sitting on it."""
    A = 10 * 16 + 10
    _session(tmp_path, {A: [(TRUCK, 1)]})
    r = app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": [
        {"x": 10, "y": 8, "layer": "structs", "slot": FENCE, "sub": 1},   # 8-neighbours the footprint → RING
        {"x": 10, "y": 5, "layer": "structs", "slot": FENCE, "sub": 1},   # 3 tiles further out → clean
    ]})
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[0]["ok"] is False and res[0]["test"] == "RING" and res[0]["tier"] == "blocking"
    assert res[0]["detail"] and "vehicle" in res[0]["detail"]
    assert res[1]["ok"] is True


@_needs_sitekit
def test_ring_overrides_advisory_why_not(app_client, tmp_path, monkeypatch):
    """A candidate whose OWN why_not is advisory (not None) must still fall
    through to the symmetric RING check (_fence_ring_violation) — a blocking
    RING wins over an advisory finding underneath it, it isn't masked by it.
    Real advisory why_not findings (CONTACT/INVERSION) need atlas masks on
    the 16x16 fixture, so this monkeypatches `why_not` on the cached World
    to return "INVERSION" unconditionally; `_explain`'s INVERSION branch
    then finds no real inversion and falls through to `other=None`, which
    `_tier` resolves to advisory (big-blocking, neither party is BIG) —
    exactly the "own test is advisory" case this guards."""
    A = 10 * 16 + 10
    sess = _session(tmp_path, {A: [(TRUCK, 1)]})
    w = mp._oracle.check(sess)
    monkeypatch.setattr(w, "why_not", lambda inst: "INVERSION")
    r = app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": [
        {"x": 10, "y": 8, "layer": "structs", "slot": FENCE, "sub": 1},   # 8-neighbours the truck footprint
    ]})
    body = r.json()["results"][0]
    assert body["ok"] is False and body["test"] == "RING" and body["tier"] == "blocking"


@_needs_sitekit
def test_cache_mirrors_edits_like_a_fresh_world(app_client, tmp_path):
    sess = _session(tmp_path, {5 * 16 + 5: [(TRUCK, 1)]})
    app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": []})
    cached = mp._oracle.check(sess)
    # mutate the session the way PUT /edits does, bump edit_count
    sess.parsed["structs"][10 * 16 + 10].append([FENCE, 4]); sess.edit_count += 1
    w2 = mp._oracle.check(sess)
    assert w2 is cached                                    # same object, mirrored not rebuilt
    fresh = mp._oracle.build(sess)
    cand = w2.make(10 * 16 + 11, FENCE, 1)
    assert w2.why_not(cand) == fresh.why_not(fresh.make(10 * 16 + 11, FENCE, 1))
    assert sorted(w2.insts) == sorted(fresh.insts)


@_needs_sitekit
def test_cache_mirrors_a_replaced_tile_not_just_appended(app_client, tmp_path):
    """A tile whose whole entry list is SWAPPED (routes.mapforge's
    place_layer_entry / set_layer_entries semantics — remove-then-add, not
    append), not just grown by one. Regression for the mirror bug where a
    changed tile's old struct list was only cleared via remove()-by-instance,
    leaving anything not tracked as a sitekit Instance behind."""
    g = 5 * 16 + 5
    sess = _session(tmp_path, {g: [(FENCE, 1)]})
    app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": []})
    cached = mp._oracle.check(sess)
    sess.parsed["structs"][g] = [[TRUCK, 1]]        # swap, not append
    sess.edit_count += 1
    w = mp._oracle.check(sess)
    assert w is cached                                     # same object, mirrored not rebuilt
    fresh = mp._oracle.build(sess)
    assert sorted(w.insts) == sorted(fresh.insts)
    assert _occ_map(w) == _occ_map(fresh)
    probe, fresh_probe = w.make(g, FENCE, 1), fresh.make(g, FENCE, 1)
    assert w.why_not(probe) == fresh.why_not(fresh_probe) == "TILE"


@_needs_sitekit
def test_cache_mirrors_a_moved_struct(app_client, tmp_path):
    """A struct removed from one anchor and re-added at another anchor in the
    same edit batch (a drag/move), not a same-tile replace."""
    A, B = 5 * 16 + 5, 10 * 16 + 10
    sess = _session(tmp_path, {A: [(TRUCK, 1)]})
    app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": []})
    cached = mp._oracle.check(sess)
    sess.parsed["structs"][A] = []
    sess.parsed["structs"][B].append([TRUCK, 1])
    sess.edit_count += 1
    w = mp._oracle.check(sess)
    assert w is cached                                     # same object, mirrored not rebuilt
    fresh = mp._oracle.build(sess)
    assert sorted(w.insts) == sorted(fresh.insts)
    assert _occ_map(w) == _occ_map(fresh)
    assert not any(gridno == A for gridno, _t, _s in w.insts)


@_needs_sitekit
def test_cache_mirror_clears_uncategorised_leftover(app_client, tmp_path):
    """Ledger 17 regression: a tile seeded with only an UNCATEGORISED entry
    (slot 1 is absent from t72_categories.json slots/subs, so sitekit never
    tracks it as an Instance) must not survive a same-tile REPLACE. The
    mirror's force-clear (`w.structs[g] = []` before re-placing fresh) is
    what removes it — a remove()-by-instance pass alone never saw it."""
    g = 5 * 16 + 5
    sess = _session(tmp_path, {g: [(1, 1)]})
    app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": []})
    cached = mp._oracle.check(sess)
    sess.parsed["structs"][g] = [[TRUCK, 1]]        # swap the uncategorised entry for a tracked one
    sess.edit_count += 1
    w = mp._oracle.check(sess)
    assert w is cached                                     # same object, mirrored not rebuilt
    assert w.structs[g] == [(TRUCK, 1)]                    # no leftover [1, 1]
    fresh = mp._oracle.build(sess)
    assert sorted(w.insts) == sorted(fresh.insts)
    assert _occ_map(w) == _occ_map(fresh)


@_needs_sitekit
def test_oracle_prunes_worlds_of_closed_sessions(app_client, tmp_path):
    """A session that closes (idle eviction, explicit close) never tells the
    oracle — the next check() on ANY session must drop cached Worlds whose
    session id is no longer in the store, so the dict doesn't grow forever."""
    sess_a = _session(tmp_path, {5 * 16 + 5: [(TRUCK, 1)]}, sid="plcA")
    sess_b = _session(tmp_path, {5 * 16 + 5: [(TRUCK, 1)]}, sid="plcB")
    try:
        mp._oracle.check(sess_a)
        mp._oracle.check(sess_b)
        assert "plcA" in mp._oracle._worlds and "plcB" in mp._oracle._worlds
        _session_store._sessions.pop("plcA", None)
        mp._oracle.check(sess_b)
        assert "plcA" not in mp._oracle._worlds
        assert "plcB" in mp._oracle._worlds
    finally:
        _session_store._sessions.pop("plcA", None)
        _session_store._sessions.pop("plcB", None)
        mp._oracle.drop("plcA")
        mp._oracle.drop("plcB")


def test_missing_sitekit_gives_503(app_client, tmp_path, monkeypatch):
    _session(tmp_path, {})
    monkeypatch.setattr(mp, "_sitekit", lambda: (_ for _ in ()).throw(mp.SitekitUnavailable("no HC")))
    r = app_client.post("/api/v1/mapforge/sessions/plc/placement/check", json={"candidates": []})
    assert r.status_code == 503 and "sitekit" in r.json()["detail"].lower()
