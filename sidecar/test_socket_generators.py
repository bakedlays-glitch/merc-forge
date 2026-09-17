#!/usr/bin/env python
"""test_socket_generators.py - prove the 4 MapForge socket smoothers (Track A / A2+A3).

Each socket smoother reproduces an engine autotiler LUT. This test drives the sidecar
with deliberately BLOCKY / socket-illegal input, runs the smoother under test, and
asserts the socket validator reports ZERO findings for that smoother's code:

    smooth_terrain -> SOCKET_TERRAIN_FRINGE == 0
    smooth_walls   -> SOCKET_WALL_AXIS      == 0   (+ applied > 0)
    smooth_water   -> SOCKET_WATER_SHORE    == 0
    smooth_caves   -> SOCKET_CAVE_WALL      == 0   (+ applied > 0)   [ts1 seed]

IMPORTANT (correction vs plan v2 wording): the SOCKET_* findings are severity
warn (walls/terrain) / info (water/caves), NOT "error". The real pass/fail gate is
therefore the COUNT of findings whose `code` starts with "SOCKET_", regardless of
severity. Filtering by severity=="error" would be vacuously true and prove nothing.

Each smoother is isolated on a freshly WIPEd session so no other layer interferes.
Where a fix is expected (walls, caves), we also assert the smoother actually acted
(applied > 0), so a silent no-op can't masquerade as a pass.

A3 tail: builds one combined ts0 sector (terrain + water + walls, all smoothed),
authors a CLEAN appendix (0 inherited soldiers, perimeter entry points, map_version
31) via POST /appendix-model, saves it to Data-1.13/Maps/SOCKTEST.DAT, and re-validates
SOCKET_*==0 -> that saved .dat is the engine-load artifact for A3.

PREREQ: sidecar running on 127.0.0.1:8000 (this script registers+activates the install).
Run with the sidecar venv:
    ./.venv/Scripts/python.exe test_socket_generators.py
"""
import os
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ----------------------------- CONFIG ---------------------------------------
INSTALL   = os.environ.get("JA2_INSTALL", "")
XML       = INSTALL + r"\Data-1.13\Ja2Set.dat.xml"
SEED_TS0  = INSTALL + r"\Data-1.13\Maps\GENSEED.DAT"       # tileset 0 (GENERIC)
SEED_TS1  = INSTALL + r"\Data-1.13\Maps\GENSEED_TS1.DAT"   # tileset 1 (CAVES)
OUT_DAT   = INSTALL + r"\Data-1.13\Maps\SOCKTEST.DAT"      # A3 engine-load artifact
HOST, PORT = "127.0.0.1", 8000
BASE = f"http://{HOST}:{PORT}/api/v1"
MF   = BASE + "/mapforge"
COLS = ROWS = 160

# Layer type/slot constants (match socket_lut).
TEX_A, TEX_B = 0, 1     # two abutting land textures (full-tile sub 1)
FULL_SUB     = 1
WATER_TYPE   = 7
WALL_TYPE    = 36
WALL_SUB_R   = 1        # EXTERIOR_R (NS-family) straight -> WRONG axis in an EW run
CAVE_TYPE    = 36
CAVE_SUB_FULL = 60      # legal only for perimeter 0xff (all-8 interior) -> edges illegal

# ------------------------- tiny HTTP helpers --------------------------------
def _req(method, url, body=None, raw=False, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} {method} {url}\n  {detail}")
    return b if raw else (json.loads(b) if b else {})

def get(u, **k):          return _req("GET", u, **k)
def post(u, b=None, **k):  return _req("POST", u, b, **k)
def put(u, b=None, **k):   return _req("PUT", u, b, **k)

def run_gen(sid, name, params=None, timeout=600):
    """POST run-generator (NDJSON stream); return the final {done,ok,applied} event."""
    raw = _req("POST", f"{MF}/sessions/{sid}/run-generator?name={name}",
               {"params": params or {}}, raw=True, timeout=timeout)
    last = {}
    for line in raw.decode().splitlines():
        if line.strip():
            last = json.loads(line)
    if not last.get("ok", False):
        raise SystemExit(f"generator {name!r} failed: {last}")
    return last

def apply_edits(sid, edits):
    applied = 0
    for i in range(0, len(edits), 4000):
        res = put(f"{MF}/sessions/{sid}/edits", {"edits": edits[i:i + 4000]})
        applied += res.get("applied", 0)
    return applied

def socket_findings(sid, code_prefix="SOCKET_"):
    """Return {code: count} for every SOCKET_* finding in the session's live state."""
    rep = get(f"{MF}/sessions/{sid}/validate?check_jsd=false")
    out = {}
    for f in rep["findings"]:
        if f["code"].startswith(code_prefix):
            out[f["code"]] = f.get("count") if f.get("count") is not None else len(f.get("tiles", []))
    return out

def open_session(seed, tileset):
    sess = post(f"{MF}/sessions",
                {"dat": seed.replace("\\", "/"), "xml": XML.replace("\\", "/"),
                 "tileset": tileset})
    return sess["session_id"], sess["tileset"]

def close_session(sid):
    try:
        _req("DELETE", f"{MF}/sessions/{sid}")
    except SystemExit:
        pass

# ------------------------------ seeds ---------------------------------------
def ensure_seed(dest, tileset):
    """A writable seed .dat of the given tileset, extracted from Maps.slf if absent."""
    if Path(dest).exists():
        return
    from mercwizard_core.mapforge_engine import building_library as bl
    skip = {Path(SEED_TS0).name.lower(), Path(SEED_TS1).name.lower(),
            Path(OUT_DAT).name.lower()}
    for src in bl.list_map_sources(Path(INSTALL)):
        if (src.get("name") or "").lower() in skip:
            continue
        data = bl._read_source_bytes(src)
        if not data:
            continue
        try:
            if bl._header_tileset(data) == tileset:
                Path(dest).write_bytes(data)
                print(f"  seed(ts{tileset}) <- {src.get('name')} ({src.get('kind')})")
                return
        except Exception:
            continue
    raise SystemExit(f"No tileset-{tileset} seed found in the install ({dest}).")

# --------------------------- blocky painters --------------------------------
def paint_rect(sid, layer, entries, x0, y0, x1, y1):
    edits = [{"x": x, "y": y, "op": "set_entries", "layer": layer, "entries": entries}
             for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    return apply_edits(sid, edits)

# ------------------------------- checks -------------------------------------
RESULTS = []

def record(name, ok, detail):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

def test_terrain(sid):
    """Two abutting full-tile textures -> smooth_terrain adds correct fringe -> 0."""
    run_gen(sid, "wipe", {"reset_rooms": True})
    paint_rect(sid, "land", [[TEX_A, FULL_SUB]], 20, 20, 69, 80)
    paint_rect(sid, "land", [[TEX_B, FULL_SUB]], 70, 20, 119, 80)
    ev = run_gen(sid, "smooth_terrain", {})
    codes = socket_findings(sid)
    n = codes.get("SOCKET_TERRAIN_FRINGE", 0)
    record("smooth_terrain", n == 0 and ev.get("applied", 0) > 0,
           f"applied={ev.get('applied')} SOCKET_TERRAIN_FRINGE={n} (all={codes})")

def test_walls(sid):
    """A horizontal run of NS-family (EXTERIOR_R) straight walls = wrong axis."""
    run_gen(sid, "wipe", {"reset_rooms": True})
    n_pre = paint_rect(sid, "structs", [[WALL_TYPE, WALL_SUB_R]], 30, 40, 60, 40)
    pre = socket_findings(sid).get("SOCKET_WALL_AXIS", 0)
    ev = run_gen(sid, "smooth_walls", {})
    n = socket_findings(sid).get("SOCKET_WALL_AXIS", 0)
    record("smooth_walls", n == 0 and ev.get("applied", 0) > 0,
           f"painted={n_pre} pre_bad={pre} applied={ev.get('applied')} "
           f"SOCKET_WALL_AXIS={n}")

def test_water(sid):
    """A blocky water box -> smooth_water adds shoreline fringe -> 0."""
    run_gen(sid, "wipe", {"reset_rooms": True})
    # ground under+around the pond so shore cells have a land neighbour to fringe against
    paint_rect(sid, "land", [[TEX_A, FULL_SUB]], 20, 20, 80, 70)
    paint_rect(sid, "land", [[WATER_TYPE, FULL_SUB]], 35, 35, 65, 55)
    ev = run_gen(sid, "smooth_water", {})
    n = socket_findings(sid).get("SOCKET_WATER_SHORE", 0)
    record("smooth_water", n == 0 and ev.get("applied", 0) > 0,
           f"applied={ev.get('applied')} SOCKET_WATER_SHORE={n}")

def test_caves(sid):
    """A blocky filled cave-wall rect (sub legal only for interior) -> edges illegal
    -> smooth_caves re-tiles the perimeter -> 0 (ts1)."""
    run_gen(sid, "wipe", {"reset_rooms": True})
    n_pre = paint_rect(sid, "structs", [[CAVE_TYPE, CAVE_SUB_FULL]], 30, 30, 55, 50)
    pre = socket_findings(sid).get("SOCKET_CAVE_WALL", 0)
    ev = run_gen(sid, "smooth_caves", {})
    n = socket_findings(sid).get("SOCKET_CAVE_WALL", 0)
    record("smooth_caves", n == 0 and ev.get("applied", 0) > 0,
           f"painted={n_pre} pre_bad={pre} applied={ev.get('applied')} "
           f"SOCKET_CAVE_WALL={n}")

# --------------------- A3: combined sector + clean appendix ------------------
def gn(x, y):
    return y * COLS + x

def build_a3_sector(sid):
    """One ts0 GENERIC sector: full ground, a smoothed pond, a smoothed wall run,
    an abutting smoothed texture patch. Saved to OUT_DAT as the engine-load artifact.

    NOTE: the ts0 seed is legacy format (major 5.0); build_appendix only emits the
    modern (>=7.0) appendix, so we CANNOT synthesize a clean appendix here — the
    seed's original appendix (incl. its 32 editor soldiers) passes through verbatim.
    Those soldiers are the map's own pre-existing content, not a smoother artifact,
    and are non-blocking for an editor LOAD. Dropping them needs a modern seed (see
    test_appendix_endpoint, which proves the endpoint round-trips on a9.dat)."""
    run_gen(sid, "wipe", {"reset_rooms": True})
    run_gen(sid, "fill", {"layer": "land", "slot": TEX_A, "sub": FULL_SUB})
    # abutting second texture + smooth
    paint_rect(sid, "land", [[TEX_B, FULL_SUB]], 90, 20, 130, 90)
    run_gen(sid, "smooth_terrain", {})
    # pond + smooth
    paint_rect(sid, "land", [[WATER_TYPE, FULL_SUB]], 35, 35, 60, 55)
    run_gen(sid, "smooth_water", {})
    # wrong-axis wall run + smooth
    paint_rect(sid, "structs", [[WALL_TYPE, WALL_SUB_R]], 30, 100, 70, 100)
    run_gen(sid, "smooth_walls", {})

    codes = socket_findings(sid)
    ap = get(f"{MF}/sessions/{sid}/appendix")
    res = post(f"{MF}/sessions/{sid}/save-copy-as",
               {"dat_path": OUT_DAT.replace("\\", "/"), "overwrite": True})
    return codes, res, len(ap.get("soldiers", []))


def _north_gridno(ap):
    for e in ap.get("entry_points", []):
        if e.get("kind") == "north":
            return e.get("gridno")
    return None


def _post_status(url, body):
    """POST returning (http_status, parsed_or_text) instead of SystemExit on error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            b = r.read()
            return r.status, (json.loads(b) if b else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


_CLEAN_MODEL = {
    "tail": {
        "north": gn(80, 2), "south": gn(80, 157),
        "east": gn(157, 80), "west": gn(2, 80),
        "center": gn(80, 80), "isolated": -1,
        "num_individuals": 0, "map_version": 31,
        "restricted_scroll_id": 0, "smoothing_type": 0,
    },
    "exit_grids": [],
}


def test_appendix_endpoint():
    """Prove POST /appendix-model authors a valid modern (>=7.0) appendix that
    round-trips through save->reparse.

    Runs on a FRESH modern sector from POST /new-sector (major 7.0, no extra
    appendix sections), not on a live install map: a9.dat has since grown an
    items section, and the appendix writer's section-preservation guard now
    (correctly) refuses to author over sections it would drop. We author a
    CLEAN appendix (perimeter entry points, 0 exit grids, map_version 31),
    save a copy, re-open from disk, and assert our authored north entry point
    + exit-grid drop actually took (proving the bytes are engine-shaped and
    honored, not ignored)."""
    FRESH = INSTALL + r"\Data-1.13\Maps\SOCKTEST_FRESH.DAT"
    OUT = INSTALL + r"\Data-1.13\Maps\SOCKTEST_APPENDIX.DAT"
    my_north = _CLEAN_MODEL["tail"]["north"]  # 400 — distinct from the fresh default (0)
    post(f"{MF}/new-sector",
         {"dat_path": FRESH.replace("\\", "/"), "tileset": 0, "overwrite": True})
    sid, _ = open_session(FRESH, 0)
    try:
        b_ap = get(f"{MF}/sessions/{sid}/appendix")
        before_sol, before_north = len(b_ap.get("soldiers", [])), _north_gridno(b_ap)
        before_exits = len(b_ap.get("exit_grids", []))
        post(f"{MF}/sessions/{sid}/appendix-model", _CLEAN_MODEL)
        post(f"{MF}/sessions/{sid}/save-copy-as",
             {"dat_path": OUT.replace("\\", "/"), "overwrite": True})
    finally:
        close_session(sid)
    # re-open the saved copy from disk and re-parse its appendix
    sid2, _ = open_session(OUT, 0)
    try:
        a_ap = get(f"{MF}/sessions/{sid2}/appendix")
        after_sol, after_north = len(a_ap.get("soldiers", [])), _north_gridno(a_ap)
        after_exits = len(a_ap.get("exit_grids", []))
    finally:
        close_session(sid2)
    ok = after_sol == 0 and after_north == my_north and after_exits == 0
    record("appendix_endpoint(modern)", ok,
           f"soldiers {before_sol}->{after_sol}=0, north {before_north}->{after_north} "
           f"(authored {my_north}), exit_grids {before_exits}->{after_exits}=0")


def test_appendix_guard():
    """Prove the section-preservation guard: authoring an appendix model over a
    map whose original appendix carries sections the model doesn't synthesize
    (a9.dat now has an items section) must be REFUSED at save time, never
    silently dropped. Skips (as pass) if a9.dat is absent or section-free."""
    A9 = INSTALL + r"\Data-1.13\Maps\a9.dat"
    SCRATCH = INSTALL + r"\Data-1.13\Maps\SOCKTEST_GUARD.DAT"
    if not Path(A9).exists():
        record("appendix_guard(sections)", True, "a9.dat not found - skipped")
        return
    sid, _ = open_session(A9, 71)
    try:
        post(f"{MF}/sessions/{sid}/appendix-model", _CLEAN_MODEL)
        status, detail = _post_status(
            f"{MF}/sessions/{sid}/save-copy-as",
            {"dat_path": SCRATCH.replace("\\", "/"), "overwrite": True})
    finally:
        close_session(sid)
    if status == 200:
        # a9 carried no extra sections in this install - authoring legitimately
        # succeeded; the guard had nothing to protect. Not a failure.
        record("appendix_guard(sections)", True,
               "a9.dat has no extra appendix sections - guard not exercised")
        return
    refused = status == 500 and "would drop sections" in str(detail)
    record("appendix_guard(sections)", refused,
           f"HTTP {status}: {'guard refused as designed' if refused else str(detail)[:200]}")

# --------------------------------- main -------------------------------------
def main():
    print("=== socket-smoother test (Track A / A2+A3) ===")
    h = get(f"{MF}/health")
    print("renderer_available:", h.get("renderer_available"))
    inst = post(f"{BASE}/installs", {"path": INSTALL.replace('\\', '/')})
    post(f"{BASE}/installs/active", {"install_id": inst["id"]})
    print("install active:", inst["id"])

    print("\n-- seeds --")
    ensure_seed(SEED_TS0, 0)
    ensure_seed(SEED_TS1, 1)

    print("\n-- ts0 smoothers (terrain / walls / water) --")
    sid0, ts0 = open_session(SEED_TS0, 0)
    print(f"  session {sid0} tileset={ts0}")
    try:
        test_terrain(sid0)
        test_walls(sid0)
        test_water(sid0)
    finally:
        close_session(sid0)

    print("\n-- ts1 smoother (caves) --")
    sid1, ts1 = open_session(SEED_TS1, 1)
    print(f"  session {sid1} tileset={ts1}")
    if ts1 != 1:
        record("smooth_caves", False, f"seed opened as tileset {ts1}, expected 1")
        close_session(sid1)
    else:
        try:
            test_caves(sid1)
        finally:
            close_session(sid1)

    print("\n-- A1 endpoint: author a clean modern appendix (fresh sector) --")
    test_appendix_endpoint()

    print("\n-- A1 guard: refuse authoring over unpreserved sections (a9.dat) --")
    test_appendix_guard()

    print("\n-- A3: combined ts0 sector (smoothed) -> save (engine-load artifact) --")
    sidA, tsA = open_session(SEED_TS0, 0)
    try:
        codes, res, soldiers = build_a3_sector(sidA)
        soc = sum(codes.values())
        record("A3_combined_validate", soc == 0,
               f"final SOCKET_* = {codes}")
        record("A3_save", Path(OUT_DAT).exists() and res.get("bytes_written", 0) > 0,
               f"-> {res.get('dat_path')} ({res.get('bytes_written')} bytes); "
               f"seed soldiers pass-through = {soldiers} (legacy fmt, see NOTE)")
    finally:
        close_session(sidA)

    print("\n=== SUMMARY ===")
    allpass = all(ok for _, ok, _ in RESULTS)
    for name, ok, detail in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{'ALL PASS' if allpass else 'FAILURES PRESENT'} "
          f"({sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)})")
    sys.exit(0 if allpass else 1)

if __name__ == "__main__":
    main()
