"""Empirical cliff-face survey across the real .dat map corpus.

Answers, from DATA rather than folklore, the questions the BankGenerator
cliff-face work needs:

  1. Which LAYER do FIRSTCLIFF-family entries live in?  (slots 9
     FIRSTCLIFFHANG / 10 FIRSTCLIFF / 11 FIRSTCLIFFSHADOW per TileDat.h —
     the engine code says the struct layer, this verifies the bytes.)
  2. Do cliff entries sit on the RAISED tile or on the LOWER neighbor?
  3. Which (slot, sub) combos appear at which border ROLE — N/S/E/W edge
     or corner, derived from the tile's height-delta signature vs its 4
     orthogonal neighbors (which sides step DOWN).
  4. How DENSE are cliff anchors along raised edges (the engine's cliff
     pieces are multi-tile sprites anchored at one gridno — see
     Editor/edit_sys.cpp CliffOffsetData — so anchors should be sparse).

Walks every Data*/Maps/*.dat (loose + Maps.slf) under the installs dir,
reusing roundtrip_audit's corpus iterator and parse_dat_full. Read-only.

Usage::

    sidecar/.venv/Scripts/python.exe tools/scan_cliff_faces.py
    sidecar/.venv/Scripts/python.exe tools/scan_cliff_faces.py \
        --installs-dir "C:/Jagged Alliance 2" --top 8
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

# Run from anywhere — put the sidecar package root on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Tool-to-tool import (same directory) for the corpus iterator.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mercwizard_core.mapforge_engine.parse_dat_ext import (  # noqa: E402
    DatParseError,
    parse_dat_full,
)
from roundtrip_audit import iter_corpus  # noqa: E402


CLIFF_SLOTS = {9: "CLIFFHANG", 10: "CLIFF", 11: "CLIFFSHADOW"}
LAYERS = ("land", "objs", "shadows", "structs", "roofs", "onroofs")
# Side order is canonical N,E,S,W so role strings are stable/comparable.
SIDES = (("N", 0, -1), ("E", 1, 0), ("S", 0, 1), ("W", -1, 0))


def classify(heights: list[int], cols: int, rows: int,
             x: int, y: int) -> tuple[str, str, str]:
    """(placement, down_sides, up_sides) for one tile.

    placement: 'raised' (only lower neighbors differ), 'lower' (only
    higher neighbors differ), 'mixed' (both), 'flat' (no height deltas
    around it). Off-grid neighbors are ignored.
    """
    h = heights[y * cols + x]
    down = ""
    up = ""
    for name, dx, dy in SIDES:
        nx, ny = x + dx, y + dy
        if not (0 <= nx < cols and 0 <= ny < rows):
            continue
        nh = heights[ny * cols + nx]
        if nh < h:
            down += name
        elif nh > h:
            up += name
    if down and up:
        return "mixed", down, up
    if down:
        return "raised", down, up
    if up:
        return "lower", down, up
    return "flat", down, up


def main(argv: Optional[list[str]] = None) -> int:
    # Windows consoles/redirects default to cp1252 — non-latin glyphs in the
    # report (arrows, ellipses) must never kill a 10-minute sweep at the
    # final print. (This exact crash ate the first run of this script.)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", help="install root dir(s) to sweep")
    ap.add_argument("--installs-dir", default="C:/Jagged Alliance 2",
                    help="sweep every immediate subdir as an install root "
                         "(default: C:/Jagged Alliance 2)")
    ap.add_argument("--no-slf", action="store_true",
                    help="loose maps only (skip Maps.slf)")
    ap.add_argument("--top", type=int, default=10,
                    help="top-N combos to print per role (default 10)")
    args = ap.parse_args(argv)

    roots = [Path(r) for r in args.roots]
    if args.installs_dir:
        base = Path(args.installs_dir)
        if base.is_dir():
            roots += [p for p in base.iterdir() if p.is_dir()]
    roots = [r for r in roots if r.exists()]
    if not roots:
        ap.error("no existing install roots (pass paths or --installs-dir)")

    print(f"Sweeping {len(roots)} install root(s)…", file=sys.stderr)

    seen_hashes: set[str] = set()
    n_maps = n_dupe = n_err = 0
    n_with_heights = 0
    n_with_cliffs = 0

    # layer → count of cliff-family entries found in that layer
    layer_hist: Counter = Counter()
    # role string → Counter[(layer, slot, sub)]
    role_hist: dict[str, Counter] = defaultdict(Counter)
    # (slot, sub) → Counter[role] — the inverted view, per cliff piece
    sub_roles: dict[tuple[int, int], Counter] = defaultdict(Counter)
    # placement bucket totals (raised / lower / mixed / flat per slot)
    placement_hist: dict[int, Counter] = defaultdict(Counter)
    # anchor density: raised-edge tiles vs raised-edge tiles carrying a
    # cliff entry (any of 9/10/11) — multi-tile sprites ⇒ expect << 100%
    edge_tiles = 0
    edge_tiles_with_cliff = 0
    # heights observed on cliff-carrying tiles (sanity: multiples of 80?)
    height_vals: Counter = Counter()

    for label, data in iter_corpus(roots, include_slf=not args.no_slf):
        digest = hashlib.md5(data).hexdigest()
        if digest in seen_hashes:
            n_dupe += 1
            continue
        seen_hashes.add(digest)
        n_maps += 1
        try:
            parsed = parse_dat_full(data, label)
        except (DatParseError, Exception):  # noqa: BLE001
            n_err += 1
            continue

        heights = parsed["heights"]
        rows, cols = parsed["rows"], parsed["cols"]
        if not any(heights):
            continue
        n_with_heights += 1

        # Cliff entries per layer + role binning.
        map_has_cliffs = False
        cliff_tiles: set[int] = set()
        for layer in LAYERS:
            grid = parsed[layer]
            for gn, entries in enumerate(grid):
                for (slot, sub) in entries:
                    if slot not in CLIFF_SLOTS:
                        continue
                    map_has_cliffs = True
                    cliff_tiles.add(gn)
                    layer_hist[layer] += 1
                    x, y = gn % cols, gn // cols
                    placement, down, up = classify(heights, cols, rows, x, y)
                    placement_hist[slot][placement] += 1
                    if placement in ("raised", "mixed"):
                        role = f"{placement}:down={down or '-'}"
                    elif placement == "lower":
                        role = f"lower:up={up}"
                    else:
                        role = "flat"
                    role_hist[role][(layer, slot, sub)] += 1
                    sub_roles[(slot, sub)][role] += 1
                    height_vals[heights[gn]] += 1
        if map_has_cliffs:
            n_with_cliffs += 1

        # Anchor density along raised edges.
        for y in range(rows):
            for x in range(cols):
                gn = y * cols + x
                if heights[gn] <= 0:
                    continue
                placement, down, _up = classify(heights, cols, rows, x, y)
                if not down:
                    continue
                edge_tiles += 1
                if gn in cliff_tiles:
                    edge_tiles_with_cliff += 1

    print("\n" + "=" * 72)
    print("Cliff-face corpus survey")
    print("=" * 72)
    print(f"  maps parsed        : {n_maps} unique ({n_dupe} byte-dupes "
          f"skipped, {n_err} parse errors)")
    print(f"  with nonzero height: {n_with_heights}")
    print(f"  with cliff entries : {n_with_cliffs}")

    print("\n  WHICH LAYER do slots 9/10/11 live in?")
    total_entries = sum(layer_hist.values())
    for layer, c in layer_hist.most_common():
        print(f"    {layer:<8}: {c:>6}  ({100.0 * c / total_entries:.1f}%)")

    print("\n  RAISED vs LOWER placement per slot:")
    for slot in sorted(placement_hist):
        c = placement_hist[slot]
        tot = sum(c.values())
        parts = ", ".join(f"{k}={v} ({100.0*v/tot:.0f}%)"
                          for k, v in c.most_common())
        print(f"    slot {slot:>2} {CLIFF_SLOTS[slot]:<12} n={tot:<6} {parts}")

    print("\n  Anchor density on raised-edge tiles (h>0 with a lower 4-neighbor):")
    if edge_tiles:
        print(f"    {edge_tiles_with_cliff} / {edge_tiles} carry a cliff entry "
              f"({100.0 * edge_tiles_with_cliff / edge_tiles:.1f}%) — "
              "<100% expected: engine cliff pieces are multi-tile sprites "
              "anchored at ONE gridno (edit_sys.cpp CliffOffsetData)")

    print("\n  Heights on cliff-carrying tiles:")
    for h, c in sorted(height_vals.items()):
        print(f"    h={h:<4}: {c}")

    print("\n  ROLE histogram — top (layer, slot, sub) per height-delta role:")
    for role in sorted(role_hist, key=lambda r: -sum(role_hist[r].values())):
        ctr = role_hist[role]
        tot = sum(ctr.values())
        print(f"\n    {role}  (n={tot})")
        for (layer, slot, sub), c in ctr.most_common(args.top):
            print(f"      {layer:<8} slot={slot:<3} sub={sub:<3} x{c}")

    print("\n  PER-SUB role profile (slot, sub -> where it appears):")
    for (slot, sub) in sorted(sub_roles):
        ctr = sub_roles[(slot, sub)]
        tot = sum(ctr.values())
        tops = ", ".join(f"{r} x{c}" for r, c in ctr.most_common(3))
        print(f"    slot {slot:>2} sub {sub:>3} (n={tot:<5}): {tops}")

    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
