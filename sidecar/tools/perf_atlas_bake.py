"""Measure _build_atlas cold + warm bake time.

Times the atlas bake for a given (Ja2Set.dat.xml, tileset) pair and prints
per-phase elapsed ms + total. Used by the testing-matrix performance budget
checks (`docs/TESTING_MATRIX.md` § 13).

The script bypasses Tauri + HTTP — it imports `_build_atlas` from the
sidecar and calls it directly with a timing emit callback. No token, no
port, no shell needed.

Exit code: 0 if within budget (`--budget-ms`), 1 if over.

Usage:
    cd <repo>\\sidecar
    .venv\\Scripts\\python.exe tools\\perf_atlas_bake.py ^
        --xml "<your install>\\Data-1.13\\Tilesets\\Ja2Set.dat.xml" ^
        --tileset 65 ^
        --cold

Pass `--cold` to delete the disk cache for this tileset before measuring
(forces a true cold bake). Without it, the second-and-onward run hits the
on-disk cache and reports near-zero time — useful for warm-cache budget
verification.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# NOTE: this is a dev-only perf tool. It depends on the separate
# Headless_Compiler project (and the vendored ja2-open-toolset), which is NOT
# part of this repo. Set the WASTELAND_ROOT env var to the directory that
# contains your local `Headless_Compiler/` and `ja2-open-toolset/` checkouts.
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))
_WASTELAND_ROOT = os.environ.get("WASTELAND_ROOT")
if _WASTELAND_ROOT:
    _wr = Path(_WASTELAND_ROOT)
    sys.path.insert(0, str(_wr / "Headless_Compiler"))
    sys.path.insert(0, str(_wr / "Headless_Compiler" / "map_corpus"))
    sys.path.insert(0, str(_wr / "ja2-open-toolset"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, help="Path to Ja2Set.dat.xml")
    parser.add_argument("--tileset", type=int, required=True, help="Tileset index")
    parser.add_argument(
        "--cold",
        action="store_true",
        help="Delete the disk cache for this tileset before timing (forces a cold bake)",
    )
    parser.add_argument(
        "--budget-ms",
        type=int,
        default=None,
        help="Optional pass/fail budget in ms. Exit 1 if total > budget.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output single-line JSON instead of human-readable text",
    )
    args = parser.parse_args()

    xml_path = Path(args.xml)
    if not xml_path.is_file():
        print(f"ERROR: xml not found: {xml_path}", file=sys.stderr)
        return 2

    # Import after path setup. Done late so a missing dependency surfaces
    # as a normal ImportError instead of a cryptic argparse failure.
    from routes.mapforge import _build_atlas, _ATLAS_CACHE, _atlas_fingerprint  # noqa: E402
    from iso_renderer import load_tileset_xml  # noqa: E402

    # Find + (optionally) remove the cache dir for this tileset. The
    # fingerprint depends on the slot map, so we have to load it first.
    slot_map = load_tileset_xml(xml_path, args.tileset)
    fp = _atlas_fingerprint(xml_path, args.tileset, slot_map)
    cache_dir = _ATLAS_CACHE / f"{args.tileset}_{fp}"
    cache_existed = cache_dir.is_dir()
    if args.cold and cache_existed:
        # NO ignore_errors — silent delete failure = warm-cache result
        # falsely reported as cold.
        shutil.rmtree(cache_dir)
        if cache_dir.exists():
            print(f"ERROR: failed to delete cache dir {cache_dir}", file=sys.stderr)
            return 4
        cache_existed = False

    # Phase timing: capture (phase_name, start_ms_since_t0, label) tuples
    # via the emit callback. Compute durations by diffing consecutive
    # timestamps in the final pass.
    t0 = time.perf_counter()
    phase_events: list[dict] = []
    progress_count = 0

    def emit(evt: dict) -> None:
        nonlocal progress_count
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if evt.get("event") == "phase":
            phase_events.append({
                "phase": evt.get("phase"),
                "label": evt.get("label"),
                "t_ms": elapsed_ms,
            })
        elif evt.get("event") == "progress":
            progress_count += 1

    try:
        png_bytes, manifest, _cache = _build_atlas(xml_path, args.tileset, emit=emit)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: bake failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    total_ms = (time.perf_counter() - t0) * 1000

    # Compute per-phase durations: phase N's duration is t(N+1) - t(N),
    # or total_ms - t(N) for the last one.
    phases_with_duration: list[dict] = []
    for i, evt in enumerate(phase_events):
        end_ms = phase_events[i + 1]["t_ms"] if i + 1 < len(phase_events) else total_ms
        phases_with_duration.append({
            "phase": evt["phase"],
            "label": evt["label"],
            "ms": round(end_ms - evt["t_ms"], 1),
        })

    summary = {
        "xml": str(xml_path),
        "tileset": args.tileset,
        "cold": args.cold,
        "cache_dir_existed_before": bool(args.cold and not cache_existed),
        "atlas_w": manifest.atlas_w,
        "atlas_h": manifest.atlas_h,
        "slots_baked": len(manifest.slot_filenames),
        "cells": len(manifest.cells),
        "png_kb": round(len(png_bytes) / 1024, 1),
        "total_ms": round(total_ms, 1),
        "progress_events": progress_count,
        "phases": phases_with_duration,
    }

    if args.budget_ms is not None:
        summary["budget_ms"] = args.budget_ms
        summary["over_budget"] = total_ms > args.budget_ms

    if args.json:
        print(json.dumps(summary, separators=(",", ":")))
    else:
        mode = "COLD" if args.cold else "warm"
        print(f"_build_atlas [{mode}] xml={xml_path.name} tileset={args.tileset}")
        print(f"  total: {summary['total_ms']} ms")
        print(f"  atlas: {summary['atlas_w']}x{summary['atlas_h']}, "
              f"{summary['slots_baked']} slots, {summary['cells']} cells, "
              f"{summary['png_kb']} KB png")
        print("  phases:")
        for p in phases_with_duration:
            print(f"    {p['phase']:>14} {p['ms']:>7.1f} ms  — {p['label']}")
        if args.budget_ms is not None:
            verdict = "OVER BUDGET" if summary["over_budget"] else "ok"
            print(f"  budget: {args.budget_ms} ms  [{verdict}]")

    if args.budget_ms is not None and summary["over_budget"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
