"""Measure _build_palette_sheet cold + warm bake time.

Sibling to perf_atlas_bake.py — same pattern but for the palette sprite
sheet (the 64x64 thumbnail grid used by MapForge's left palette).

Usage:
    cd <repo>\\sidecar
    .venv\\Scripts\\python.exe tools\\perf_palette_sheet.py ^
        --xml "<your install>\\...\\Ja2Set.dat.xml" --tileset 65 --cold
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True)
    parser.add_argument("--tileset", type=int, required=True)
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--budget-ms", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    xml_path = Path(args.xml)
    if not xml_path.is_file():
        print(f"ERROR: xml not found: {xml_path}", file=sys.stderr)
        return 2

    from routes.mapforge import (  # noqa: E402
        _build_palette_sheet,
        _PALETTE_SHEET_CACHE,
        _palette_sheet_fingerprint,
    )
    from iso_renderer import load_tileset_xml  # noqa: E402

    slot_map = load_tileset_xml(xml_path, args.tileset)
    fp = _palette_sheet_fingerprint(xml_path, args.tileset, slot_map)
    cache_dir = _PALETTE_SHEET_CACHE / f"{args.tileset}_{fp}"
    if args.cold and cache_dir.is_dir():
        shutil.rmtree(cache_dir)
        if cache_dir.exists():
            print(f"ERROR: failed to delete cache dir {cache_dir}", file=sys.stderr)
            return 4

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
        png_bytes, from_cache = _build_palette_sheet(xml_path, args.tileset, emit=emit)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: bake failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    total_ms = (time.perf_counter() - t0) * 1000

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
        "from_cache": from_cache,
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
        print(f"_build_palette_sheet [{mode}] xml={xml_path.name} tileset={args.tileset}")
        print(f"  total: {summary['total_ms']} ms")
        print(f"  png: {summary['png_kb']} KB  from_cache={from_cache}")
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
