"""Measure the full sector-open path: parse .dat + bake atlas + create
session. Approximates what the user pays when clicking a sector in
MapForge — same code paths the HTTP `POST /mapforge/sessions` endpoint
runs, minus the HTTP overhead.

Times each phase separately so we can attribute a slow open to .dat
parsing vs atlas bake vs session bookkeeping.

Usage:
    cd <repo>\\sidecar
    .venv\\Scripts\\python.exe tools\\perf_session_open.py ^
        --dat "<your install>\\Data-1.13\\Maps\\C5.dat"

The XML path + tileset index are auto-derived from the .dat (each .dat
declares its tileset; the XML lives at <install>/Data-1.13/Tilesets/Ja2Set.dat.xml
or the equivalent Data/ fallback).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# NOTE: dev-only perf tool. Depends on the separate Headless_Compiler project
# (not part of this repo). Set WASTELAND_ROOT to the directory containing your
# local `Headless_Compiler/` and `ja2-open-toolset/` checkouts.
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))
_WASTELAND_ROOT = os.environ.get("WASTELAND_ROOT")
if _WASTELAND_ROOT:
    _wr = Path(_WASTELAND_ROOT)
    sys.path.insert(0, str(_wr / "Headless_Compiler"))
    sys.path.insert(0, str(_wr / "Headless_Compiler" / "map_corpus"))
    sys.path.insert(0, str(_wr / "ja2-open-toolset"))


def _resolve_xml_for_dat(dat_path: Path) -> Path | None:
    """Walk up from the .dat to find the install root, then look for
    Ja2Set.dat.xml directly under Data-1.13/, Data/, or Data-UB/.
    (The XML lives at the data dir's root, NOT in a Tilesets/ subdir —
    the Tilesets/ folder holds STIs and per-tileset overrides.)"""
    cur = dat_path.parent  # .../Data-1.13/Maps
    for _ in range(5):
        cur = cur.parent
        for sub in ("Data-1.13", "Data", "Data-UB"):
            cand = cur / sub / "Ja2Set.dat.xml"
            if cand.is_file():
                return cand
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dat", required=True, help="Path to <sector>.dat")
    parser.add_argument("--xml", default=None, help="Override Ja2Set.dat.xml path")
    parser.add_argument(
        "--cold-atlas",
        action="store_true",
        help="Delete the atlas cache for this tileset before timing",
    )
    parser.add_argument("--budget-ms", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    dat_path = Path(args.dat)
    if not dat_path.is_file():
        print(f"ERROR: dat not found: {dat_path}", file=sys.stderr)
        return 2

    xml_path = Path(args.xml) if args.xml else _resolve_xml_for_dat(dat_path)
    if xml_path is None or not xml_path.is_file():
        print(f"ERROR: couldn't locate Ja2Set.dat.xml relative to {dat_path}", file=sys.stderr)
        return 2

    from routes.mapforge import (  # noqa: E402
        _build_atlas, _ATLAS_CACHE, _atlas_fingerprint,
    )
    from iso_renderer import load_tileset_xml  # noqa: E402
    from parse_dat_ext import parse_dat_file  # noqa: E402

    t_parse_start = time.perf_counter()
    parsed = parse_dat_file(dat_path)
    t_parse_ms = (time.perf_counter() - t_parse_start) * 1000
    tileset = parsed.get("tileset", 0)

    if args.cold_atlas:
        slot_map = load_tileset_xml(xml_path, tileset)
        fp = _atlas_fingerprint(xml_path, tileset, slot_map)
        cache_dir = _ATLAS_CACHE / f"{tileset}_{fp}"
        if cache_dir.is_dir():
            # NO ignore_errors — silent delete failure would make a warm-
            # cache result get falsely reported as cold. Let the
            # exception surface. (Pre-fix the comment continuation
            # broke the `if` block's suite — `shutil.rmtree` ran
            # unconditionally and raised FileNotFoundError on first-
            # run, sweep bug-review.)
            shutil.rmtree(cache_dir)
            if cache_dir.exists():
                print(f"ERROR: failed to delete cache dir {cache_dir}", file=sys.stderr)
                return 4

    t_atlas_start = time.perf_counter()
    try:
        png_bytes, manifest, _ = _build_atlas(xml_path, tileset)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: atlas bake failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    t_atlas_ms = (time.perf_counter() - t_atlas_start) * 1000

    total_ms = t_parse_ms + t_atlas_ms

    summary = {
        "dat": str(dat_path),
        "xml": str(xml_path),
        "tileset": tileset,
        "cold_atlas": args.cold_atlas,
        "parse_ms": round(t_parse_ms, 1),
        "atlas_ms": round(t_atlas_ms, 1),
        "total_ms": round(total_ms, 1),
        "atlas_size": f"{manifest.atlas_w}x{manifest.atlas_h}",
        "slots_baked": len(manifest.slot_filenames),
        "png_kb": round(len(png_bytes) / 1024, 1),
    }

    if args.budget_ms is not None:
        summary["budget_ms"] = args.budget_ms
        summary["over_budget"] = total_ms > args.budget_ms

    if args.json:
        print(json.dumps(summary, separators=(",", ":")))
    else:
        mode = "COLD-atlas" if args.cold_atlas else "warm-atlas"
        print(f"session_open [{mode}] dat={dat_path.name} tileset={tileset}")
        print(f"  parse  : {summary['parse_ms']:>7.1f} ms")
        print(f"  atlas  : {summary['atlas_ms']:>7.1f} ms ({summary['atlas_size']}, "
              f"{summary['slots_baked']} slots)")
        print(f"  total  : {summary['total_ms']:>7.1f} ms")
        if args.budget_ms is not None:
            verdict = "OVER BUDGET" if summary["over_budget"] else "ok"
            print(f"  budget: {args.budget_ms} ms  [{verdict}]")

    if args.budget_ms is not None and summary["over_budget"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
