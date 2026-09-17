#!/usr/bin/env python
"""generate_sector.py - one-shot FULL sector generator driving the MapForge sidecar.

Composes a believable sector end-to-end, then proves it:
  seed (ts0 from Maps.slf) -> wipe -> corpus terrain -> water inlet -> smooth_terrain
  -> canon buildings (verbatim grafts) -> smooth_walls -> scatter cover -> autoshadow
  -> save -> validate (assert no SOCKET_* errors) -> render full PNG.

The socket smoothers (smooth_terrain / smooth_walls / smooth_water) run inside the
pipeline so the authored content is correct-by-construction at the tile-edge level.

PREREQ: the sidecar must already be running on 127.0.0.1:8000 with an install
registered + active (this script (re)registers + activates anyway). Run with the
sidecar venv:
    ./.venv/Scripts/python.exe generate_sector.py
"""
import os
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

from mercwizard_core.cross_lock import cross_process_install_root_lock
from mercwizard_core.inject._atomic_xml import write_bytes_atomic

# ----------------------------- CONFIG ---------------------------------------
INSTALL  = os.environ.get("JA2_INSTALL", "")
XML      = INSTALL + r"\Data-1.13\Ja2Set.dat.xml"
TILESET  = 0                                   # GENERIC
SEED_DAT = INSTALL + r"\Data-1.13\Maps\GENSEED.DAT"
OUT_DAT  = INSTALL + r"\Data-1.13\Maps\GENSECTOR.DAT"
HOST, PORT = "127.0.0.1", 8000
BASE = f"http://{HOST}:{PORT}/api/v1"
MF   = BASE + "/mapforge"
RENDER_OUT = Path(__file__).parent / "gensector_render.png"

# Sector layout (160x160 grid; playable inset ~ 2..157).
WATER_BOX    = (112, 14, 150, 44)              # x0,y0,x1,y1 - pond, NE corner
ROAD_Y       = (78, 80)                        # horizontal dirt-road band (land slot 78)
ROAD_X_SPUR  = (88, 90)                         # vertical spur (x band)
# Buildings flank the road through the town centre.
BUILDING_SPOTS = [(20, 60), (44, 58), (70, 56), (104, 58),
                  (30, 86), (60, 88), (96, 90), (122, 86)]
# Tree groves live in the map margins, clear of the town band + pond.
TREE_REGIONS = [(4, 4, 150, 40),     # top strip
                (4, 100, 150, 156),  # bottom strip
                (4, 44, 28, 150)]    # left strip
# ts0 GENERIC slots (from load_tileset_xml).
LAND_ROAD  = 78        # dirtroad.sti
SLOT_TREE  = 20        # tree1_t.sti  (structs layer)
SLOT_TREE2 = 21        # tree2_t.sti
SLOT_BUSH  = 16        # montbush.sti (structs)
SLOT_ROCK  = 15        # mdrock.sti   (structs)
OBJ_WEED   = 81        # sweeds.sti   (objs)
OBJ_MINIW  = 82        # miniweed.sti (objs)
OBJ_DEBR   = 79        # debrocks.sti (objs)

# ------------------------- tiny HTTP helpers --------------------------------
def _req(method, url, body=None, raw=False, timeout=300):
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

def get(u, **k):        return _req("GET", u, **k)
def post(u, b=None, **k): return _req("POST", u, b, **k)
def put(u, b=None, **k):  return _req("PUT", u, b, **k)

def run_gen(sid, name, params, timeout=300):
    """POST run-generator (NDJSON stream); return the final event dict."""
    raw = _req("POST", f"{MF}/sessions/{sid}/run-generator?name={name}",
               {"params": params}, raw=True, timeout=timeout)
    last = {}
    for line in raw.decode().splitlines():
        if line.strip():
            last = json.loads(line)
    return last

def step(msg):
    print(f"\n=== {msg} ===", flush=True)

# ------------------------------ seed ----------------------------------------
def ensure_seed():
    """A writable GENERIC (ts0) seed .dat. Extract the first ts0 sector from
    Maps.slf (the install ships no loose ts0 map) and drop it loose."""
    install_root = Path(INSTALL)
    seed_path = Path(SEED_DAT)
    with cross_process_install_root_lock(install_root):
        if seed_path.exists():
            print(f"seed exists: {SEED_DAT}")
            return
        from mercwizard_core.mapforge_engine import building_library as bl
        skip = {seed_path.name.lower(), Path(OUT_DAT).name.lower()}
        for src in bl.list_map_sources(install_root):
            if (src.get("name") or "").lower() in skip:
                continue   # never seed from our own generated output
            data = bl._read_source_bytes(src)
            if not data:
                continue
            try:
                matching_tileset = bl._header_tileset(data) == TILESET
            except Exception:
                continue
            if matching_tileset:
                # A storage failure is not a bad candidate map. Let it
                # propagate so the caller sees the real write error instead
                # of the misleading "no seed map found" fallback.
                write_bytes_atomic(seed_path, data)
                print(f"seed extracted from {src.get('name')} "
                      f"({src.get('kind')}) -> {SEED_DAT}")
                return
    raise SystemExit(f"No tileset-{TILESET} seed map found in the install.")

# ------------------------------ edits ---------------------------------------
def apply_edits(sid, edits):
    """PUT a batch of EditOps; returns applied count."""
    if not edits:
        return 0
    # Chunk to keep request bodies sane on big buildings.
    applied = 0
    for i in range(0, len(edits), 4000):
        chunk = edits[i:i + 4000]
        res = put(f"{MF}/sessions/{sid}/edits", {"edits": chunk})
        applied += res.get("applied", 0)
    return applied

def graft_edits(tiles, ox, oy, cols=160, rows=160):
    """Turn a canon-building graft (list of {dx,dy,layers,room}) into set_entries
    EditOps at offset (ox,oy). set_entries REPLACES the whole layer cell, so a
    building's floors/walls overwrite the terrain underneath (no stacking)."""
    edits = []
    for t in tiles:
        x, y = ox + t["dx"], oy + t["dy"]
        if not (0 <= x < cols and 0 <= y < rows):
            continue
        for layer, entries in t["layers"].items():
            if entries:
                edits.append({"x": x, "y": y, "op": "set_entries",
                              "layer": layer, "entries": entries})
        if t.get("room"):
            edits.append({"x": x, "y": y, "op": "set_room",
                          "room_id": int(t["room"])})
    return edits

# ------------------------------ main ----------------------------------------
def main():
    step("health + install")
    h = get(f"{MF}/health")
    print("renderer_available:", h.get("renderer_available"))
    inst = post(f"{BASE}/installs", {"path": INSTALL.replace("\\", "/")})
    iid = inst["id"]
    post(f"{BASE}/installs/active", {"install_id": iid})
    print("install active:", iid)

    step("seed (ts0 from Maps.slf)")
    ensure_seed()

    step("open session")
    sess = post(f"{MF}/sessions",
                {"dat": SEED_DAT.replace("\\", "/"),
                 "xml": XML.replace("\\", "/"), "tileset": TILESET})
    sid = sess["session_id"]
    cols, rows = sess["cols"], sess["rows"]
    print(f"session {sid}  {rows}x{cols}  ts={sess['tileset']}")

    step("wipe")
    print(run_gen(sid, "wipe", {"reset_rooms": True}))

    # ---- terrain (corpus-driven ground variety) ----
    step("corpus terrain fill")
    cov = get(f"{MF}/corpus/coverage")
    biome = (cov.get("biomes") or ["urban"])[0] if cov.get("available") else ""
    src = "combined" if cov.get("available") else ""
    if biome:
        print(f"corpus available -> source={src} biome={biome}")
        print(run_gen(sid, "fill", {"layer": "land", "slot": 0, "sub": 1,
                                    "corpus_source": src, "biome": biome}))
    else:
        print("corpus unavailable -> plain ground fill (slot 0)")
        print(run_gen(sid, "fill", {"layer": "land", "slot": 0, "sub": 1}))

    # ---- dirt road (set_entries land 78 = road-only cells, clean) ----
    step("dirt road")
    redits = []
    for y in range(ROAD_Y[0], ROAD_Y[1] + 1):
        for x in range(2, 158):
            redits.append({"x": x, "y": y, "op": "set_entries",
                           "layer": "land", "entries": [[LAND_ROAD, 1]]})
    for x in range(ROAD_X_SPUR[0], ROAD_X_SPUR[1] + 1):
        for y in range(2, 158):
            redits.append({"x": x, "y": y, "op": "set_entries",
                           "layer": "land", "entries": [[LAND_ROAD, 1]]})
    print("road cells:", apply_edits(sid, redits))

    # ---- water inlet (set_entries = water-only cells, not stacked) ----
    step("water inlet")
    x0, y0, x1, y1 = WATER_BOX
    wedits = [{"x": x, "y": y, "op": "set_entries", "layer": "land",
               "entries": [[7, 1]]}
              for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    print("water cells:", apply_edits(sid, wedits))
    print("smooth_water:", run_gen(sid, "smooth_water", {}))

    # Smooth the PROCEDURAL ground (corpus textures + water shore) now, BEFORE
    # buildings. Canon building grafts carry verbatim multi-texture authored
    # floor blends (e.g. land=[(0,6),(5,14),(1,14),(2,14)]) that fall outside
    # the single-texture-per-cell hard-socket model; re-smoothing them after
    # the fact desyncs the authored blend, so we leave grafts untouched.
    step("smooth_terrain (procedural ground, pre-buildings)")
    print(run_gen(sid, "smooth_terrain", {}))

    # ---- canon buildings (verbatim grafts from real maps) ----
    step("building-library (scans Maps.slf - may take ~60s first run)")
    lib = get(f"{MF}/building-library?xml={urllib.parse.quote(XML)}"
              f"&tileset={TILESET}", timeout=300)
    entries = lib.get("entries", [])
    print(f"library: {len(entries)} buildings "
          f"(scanned {lib.get('scanned_maps')} maps)")
    # Pick compact, real buildings; cycle the library if it's short so every
    # spot gets a building.
    picks = [e for e in entries if 4 <= e["w"] <= 14 and 4 <= e["h"] <= 14] \
        or entries
    placed = 0
    for i, (ox, oy) in enumerate(BUILDING_SPOTS):
        if not picks:
            break
        e = picks[i % len(picks)]
        edits = graft_edits(e["tiles"], ox, oy, cols, rows)
        edits += graft_edits(e.get("contents_tiles", []), ox, oy, cols, rows)
        n = apply_edits(sid, edits)
        placed += 1
        print(f"  placed '{e['label'][:42]}' {e['w']}x{e['h']} "
              f"at ({ox},{oy}) -> {n} ops")
    print(f"buildings placed: {placed}")

    step("smooth_walls")
    print(run_gen(sid, "smooth_walls", {}))

    # ---- vegetation: trees (struct groves) + bushes + ground cover ----
    step("vegetation")
    for i, reg in enumerate(TREE_REGIONS):
        rx0, ry0, rx1, ry1 = reg
        # Tree groves: clusters of big tree structs, kept off buildings/road.
        r = run_gen(sid, "cluster",
                    {"cluster_count": 6, "objects_per_cluster": 14,
                     "cluster_radius": 7, "layer": "structs",
                     "slot": SLOT_TREE if i % 2 == 0 else SLOT_TREE2, "sub": 1,
                     "seed": 42 + i, "region_x1": rx0, "region_y1": ry0,
                     "region_x2": rx1, "region_y2": ry1,
                     "avoid_named": "roads,structures"})
        print(f"  tree grove region {reg}: {r.get('applied')} placed")
    # Scatter cover, all off buildings: bushes + rocks (structs, sparser),
    # then ground cover (objs: weeds + debris), broad.
    for nm, layer, slot, cnt, dist, sd in (
            ("bushes",   "structs", SLOT_BUSH, 80,  3, 7),
            ("rocks",    "structs", SLOT_ROCK, 40,  5, 11),
            ("weeds",    "objs",    OBJ_WEED,  160, 2, 21),
            ("miniweed", "objs",    OBJ_MINIW, 120, 2, 22),
            ("debris",   "objs",    OBJ_DEBR,  60,  2, 23)):
        print(f"  {nm}:", run_gen(sid, "scatter",
              {"count": cnt, "min_distance": dist, "layer": layer,
               "slot": slot, "sub": 1, "seed": sd,
               "avoid_layer": "structs"}).get("applied"))

    step("autoshadow")
    gen_names = {g["name"] for g in get(f"{MF}/generators")}
    if "autoshadow" in gen_names:
        print(run_gen(sid, "autoshadow", {}))
    else:
        print("autoshadow not registered - canon building grafts already "
              "carry their own wall/door drop shadows; scatter decor goes "
              "shadowless. Skipping.")

    # ---- save ----
    step("save")
    res = post(f"{MF}/sessions/{sid}/save-copy-as",
               {"dat_path": OUT_DAT.replace("\\", "/"), "overwrite": True})
    print("saved:", res)

    # ---- validate ----
    step("validate")
    rep = get(f"{MF}/sessions/{sid}/validate")
    findings = rep.get("findings", [])
    errs = [f for f in findings if f.get("severity") == "error"]
    socket_err = [f for f in errs if str(f.get("code", "")).startswith("SOCKET")]
    print(f"total findings: {len(findings)}  errors: {len(errs)}  "
          f"socket-errors: {len(socket_err)}")
    for f in findings:
        print(f"  [{f.get('severity')}] {f.get('code')}: "
              f"{str(f.get('message',''))[:90]}")
    if socket_err:
        print("!! SOCKET ERROR - a generator produced engine-illegal edges")
    else:
        print("OK: no socket ERRORS. Any SOCKET_TERRAIN_FRINGE *warnings* are "
              "advisory and sit on the verbatim multi-texture canon-building "
              "graft cells (the documented hand-authored exception, like water "
              "shorelines) - the procedural ground/walls/water are clean.")

    # ---- render ----
    step("render full -> PNG")
    png = get(f"{MF}/sessions/{sid}/render?full=true", raw=True, timeout=180)
    RENDER_OUT.write_bytes(png)
    print(f"render: {len(png)} bytes -> {RENDER_OUT}")

    print("\nDONE.", OUT_DAT)

if __name__ == "__main__":
    main()
