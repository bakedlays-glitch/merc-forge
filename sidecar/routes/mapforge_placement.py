"""Placement oracle for the mode-less MapForge editor.

Read-only: `tables` serves sitekit's category/tier tables (+ the sidecar's
fence role table); `check` runs sitekit `World.why_not` on candidate anchors
against the session's in-memory state. Nothing here writes a file or mutates a
session — refusal is client-side (D11), and `PUT /edits` keeps no sitekit gate
(sitekit's own resolve writes through it).

sitekit lives in Headless_Compiler (another lane's package): imported lazily,
located env → source-relative → fixed path (the frozen-exe rule from
wasteland-mercforge), never edited from here.

`_require_renderer` / `_session_store` are looked up as `mf.<name>` at call
time (not imported by name) so a test's `monkeypatch.setattr(mf, ...)`
reaches this route without re-patching a second copy of the reference.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from routes import mapforge as mf

router = APIRouter(prefix="/mapforge", tags=["mapforge"])


class SitekitUnavailable(RuntimeError):
    pass


def _placement_data_dir() -> Path:
    """env → source-relative → frozen bundle; first existing dir wins
    (mirrors `_headless_compiler_root`'s ladder). Missing data is tolerated —
    `_fences()` already treats a missing file as an empty table."""
    src = Path(__file__).resolve().parent.parent / "data" / "placement"
    cands = [os.environ.get("MERCWIZARD_PLACEMENT_DATA"), str(src)]
    if getattr(sys, "frozen", False):
        cands.append(str(Path(getattr(sys, "_MEIPASS", "")) / "data" / "placement"))
    for c in cands:
        if c and Path(c).is_dir():
            return Path(c)
    return src


def _headless_compiler_root() -> Path:
    cands = [
        os.environ.get("MERCWIZARD_HEADLESS_COMPILER"),
        str(Path(__file__).resolve().parents[3] / "Headless_Compiler"),
    ]
    for c in cands:
        if c and (Path(c) / "sitekit" / "world.py").exists():
            return Path(c)
    raise SitekitUnavailable("Headless_Compiler/sitekit not found (set MERCWIZARD_HEADLESS_COMPILER)")


_sk_lock = threading.Lock()
_sk_mods: dict[str, Any] = {}


def _sitekit() -> dict[str, Any]:
    """Import sitekit + friends once; raises SitekitUnavailable with a clear message."""
    with _sk_lock:
        if _sk_mods:
            return _sk_mods
        root = _headless_compiler_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from sitekit import world as sk_world, place as sk_place
            from sector_gen import road_macros
        except Exception as e:                                                   # pragma: no cover
            raise SitekitUnavailable(f"sitekit import failed: {e}") from e
        _sk_mods.update(world=sk_world, place=sk_place, road_slot=road_macros.ROADPIECES_TYPE)
        return _sk_mods


def _fences(tileset: int) -> dict[str, dict[str, int]]:
    p = _placement_data_dir() / f"t{tileset}_fences.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("fences", {})


class PlacementOracle:
    """Per-session sitekit World, kept current by mirroring struct/shadow
    changes through the public place()/remove() path.

    A changed tile's OLD struct entries are removed by instance (categorised
    entries only — sitekit never tracked ground/uncategorised ones as
    Instances) and then the tile's struct list is force-cleared before the
    NEW entries are placed fresh. Without that clear, an uncategorised entry
    that survives the edit unchanged would get appended a second time
    (`place()`'s ground path just appends) the first time ANY sibling entry
    on the same tile changes — a `set_entries`/`place` op that REPLACES a
    tile's whole list, not just appends to it, is the normal case (routes.
    mapforge `place_layer_entry` / `set_layer_entries`).

    ponytail: full tile-diff on every edit_count change (~130k list compares,
    tens of ms); switch to an edit journal if a hover ever stalls on it.
    """
    REBUILD_AT = 2000   # changed tiles above this → rebuild instead of mirror

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tileset_data: dict[int, tuple] = {}
        self._cats: dict[int, dict] = {}
        self._worlds: dict[str, tuple[Any, int, list, list]] = {}   # sid → (World, edit_count, structs_snap, shadows_snap)

    def tileset_data(self, ts: int):
        sk = _sitekit()
        with self._lock:
            if ts not in self._tileset_data:
                self._tileset_data[ts] = sk["world"].load_tileset_data(ts)
                self._cats[ts] = sk["world"].load_categories(ts)
            return self._tileset_data[ts], self._cats[ts]

    @staticmethod
    def _snap(layer: list) -> list:
        return [[tuple(e) for e in ents] for ents in layer]

    def build(self, sess):
        sk = _sitekit()
        (index, cells, masks), cats = self.tileset_data(sess.tileset)
        mc = sess.parsed
        return sk["world"].World(mc, index=index, cells=cells, masks=masks, categories=cats, road_type=sk["road_slot"])

    def check(self, sess):
        """Return the session's World, building or mirroring as needed."""
        sk = _sitekit()
        with self._lock:
            cur = self._worlds.get(sess.id)
            if cur is None:
                w = self.build(sess)
                self._worlds[sess.id] = (w, sess.edit_count, self._snap(sess.parsed["structs"]), self._snap(sess.parsed["shadows"]))
            else:
                w, ec, s_snap, sh_snap = cur
                if ec != sess.edit_count:
                    changed = [g for g in range(len(s_snap))
                               if [tuple(e) for e in sess.parsed["structs"][g]] != s_snap[g]
                               or [tuple(e) for e in sess.parsed["shadows"][g]] != sh_snap[g]]
                    if len(changed) > self.REBUILD_AT:
                        w = self.build(sess)
                    else:
                        for g in changed:
                            for (t, s) in s_snap[g]:
                                inst = w.insts.get((g, t, s))
                                if inst is not None:
                                    sk["place"].remove(w, inst)
                            # Force-clear rather than trust the removes above to have
                            # emptied it: a tile whose entries were REPLACED (not
                            # just appended/removed) can leave an uncategorised
                            # leftover that was never tracked as an Instance.
                            w.structs[g] = []
                            x, y = g % w.cols, g // w.cols
                            for e in sess.parsed["structs"][g]:
                                sk["place"].place(w, x, y, int(e[0]), int(e[1]), with_shadow=False, force=True)
                            w.shadows[g] = [tuple(e) for e in sess.parsed["shadows"][g]]
                        # roads/roofs can change through objs/roofs edits too — recompute the cheap sets
                        w.roads = {gg for gg, e in enumerate(sess.parsed["objs"]) for t, _ in e if t == sk["road_slot"]}
                        w.roofed = {gg for gg, e in enumerate(sess.parsed["roofs"]) if e}
                    self._worlds[sess.id] = (w, sess.edit_count, self._snap(sess.parsed["structs"]), self._snap(sess.parsed["shadows"]))
            # Sessions close (idle eviction, explicit close) without telling the
            # oracle — drop any cached World whose session no longer exists so
            # this dict doesn't grow unbounded over a long-running sidecar.
            live = mf._session_store._sessions
            for sid in [s for s in self._worlds if s not in live]:
                del self._worlds[sid]
            return w

    def drop(self, sid: str) -> None:
        with self._lock:
            self._worlds.pop(sid, None)


_oracle = PlacementOracle()


class Candidate(BaseModel):
    x: int
    y: int
    layer: str = "structs"
    slot: int
    sub: int


class CheckBody(BaseModel):
    # Each candidate runs a fence-ring scan over its footprint's
    # neighbours under the session lock, so the list is capped; a
    # 160x160 map only has 25,600 tiles to ask about.
    candidates: list[Candidate] = Field(max_length=50_000)


class Verdict(BaseModel):
    x: int
    y: int
    layer: str
    slot: int
    sub: int
    ok: bool
    test: Optional[str] = None
    tier: Optional[str] = None
    tile: Optional[list[int]] = None
    detail: Optional[str] = None


class CheckResult(BaseModel):
    results: list[Verdict]
    stale: bool = False
    world_ms: float = 0.0


def _tier(cats: dict, test: str, cand_cat: Optional[str], other_cat: Optional[str]) -> str:
    raw = cats.get("tiers", {}).get(test, "blocking")
    if raw == "big-blocking":
        big = ("vehicle", "landmark")
        return "blocking" if (cand_cat in big or other_cat in big) else "advisory"
    return "advisory" if raw == "advisory" else "blocking"


def _who(i) -> str:
    return f"{i.cat} ({i.t},{i.s})@({i.x},{i.y})"


def _fence_ring_violation(w, inst):
    """Symmetric RING: sitekit's own `World.ring_violators(inst)` only fires
    when `inst` itself is vehicle/landmark (`World.BIG`) — a fence/building
    candidate dropped next to an EXISTING multi-tile vehicle/landmark falls
    through it and reads clean, even though sitekit's own global `conflicts()`
    pass flags that exact pair either way. Mirrors that pass's RING rule from
    the candidate's side instead. O(tiles × 8 × occupants); TILE already
    covers same-tile pairs so the centre tile is skipped.

    Returns (other_instance, detail, tile_gridno) or None.
    """
    if inst.cat not in ("fence", "building"):
        return None
    for tg in inst.tiles:
        x, y = tg % w.cols, tg // w.cols
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w.cols and 0 <= ny < w.rows):
                    continue
                ng = ny * w.cols + nx
                for o in w.occ.get(ng, []):
                    if o.id != inst.id and o.cat in w.BIG and len(o.tiles) >= 2:
                        return o, f"RING: {_who(inst)} touches {_who(o)}", ng
    return None


def _explain(w, inst, test: str):
    """(other_instance | None, detail str, offending tile gridno) for the first offender of `test`."""
    cols = w.cols
    who = _who
    if test == "TILE":
        for o, tg in w.tile_conflicts(inst):
            return o, f"TILE: {who(o)} at ({tg % cols},{tg // cols})", tg
    if test == "RING":
        for o in w.ring_violators(inst):
            return o, f"RING: {who(o)} touches the footprint", inst.g
    if test == "CONTACT":
        for o in w.contacts(inst):
            return o, f"CONTACT: ground band meets {who(o)}", inst.g
    if test == "INVERSION":
        for o in w.inversions(inst):
            return o, f"INVERSION: draws over {who(o)} from behind", inst.g
    if test == "ROAD":
        return None, "ROAD: footprint crosses a road tile", inst.g
    if test == "ROOF":
        return None, "ROOF: footprint is under a roof", inst.g
    return None, f"{test}", inst.g


@router.get("/placement/tables")
def placement_tables(tileset: int = Query(...)):
    mf._require_renderer()
    try:
        (_, _, _), cats = _oracle.tileset_data(tileset)
    except SitekitUnavailable as e:
        raise HTTPException(503, f"placement oracle offline: {e}")
    except FileNotFoundError as e:
        raise HTTPException(404, f"no sitekit tables for tileset {tileset}: {e}")
    return {
        "categories": cats.get("slots", {}),
        "subs": cats.get("subs", {}),
        "tiers": cats.get("tiers", {}),
        "fences": _fences(tileset),
        "road_slot": _sitekit()["road_slot"],
    }


@router.post("/sessions/{session_id}/placement/check", response_model=CheckResult)
def placement_check(session_id: str, body: CheckBody):
    """Read-only oracle: never mutates the session or disk (backup ALLOWLIST)."""
    mf._require_renderer()
    try:
        _sitekit()
    except SitekitUnavailable as e:
        raise HTTPException(503, f"placement oracle offline (sitekit): {e}")
    t0 = time.perf_counter()
    with mf._session_store.borrow(session_id) as sess:
        # `PUT /edits` holds sess._lock while it mutates sess.parsed; the World
        # mirror above reads that same dict, so it runs under the same lock
        # (oracle._lock nests inside it — check() never touches sess._lock).
        with sess._lock:
            w = _oracle.check(sess)
            (_, _, _), cats = _oracle.tileset_data(sess.tileset)
            out: list[Verdict] = []
            for c in body.candidates:
                if c.layer != "structs":
                    out.append(Verdict(**c.model_dump(), ok=True)); continue
                g = c.y * w.cols + c.x
                inst = w.make(g, c.slot, c.sub)
                if inst is None:                       # uncategorised = ground, never solid
                    out.append(Verdict(**c.model_dump(), ok=True)); continue
                test = w.why_not(inst)
                other = detail = tg = None
                tier = None
                if test is not None:
                    other, detail, tg = _explain(w, inst, test)
                    tier = _tier(cats, test, inst.cat, getattr(other, "cat", None))
                # The symmetric RING check (see _fence_ring_violation) must not
                # be masked by an advisory why_not finding underneath it — a
                # blocking RING always wins, so it's also evaluated when the
                # own-instance test came back advisory, not just when it was None.
                if test is None or tier == "advisory":
                    sym = _fence_ring_violation(w, inst)
                    if sym is not None:
                        other, detail, tg = sym
                        test = "RING"
                        tier = _tier(cats, test, inst.cat, getattr(other, "cat", None))
                if test is None:
                    out.append(Verdict(**c.model_dump(), ok=True)); continue
                out.append(Verdict(**c.model_dump(), ok=False, test=test, tier=tier,
                                   tile=[tg % w.cols, tg // w.cols], detail=detail))
    return CheckResult(results=out, world_ms=round((time.perf_counter() - t0) * 1000, 1))
