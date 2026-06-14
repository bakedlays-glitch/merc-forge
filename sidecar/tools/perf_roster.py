#!/usr/bin/env python3
"""Perf benchmark: roster load + portrait-sheet bake / on-disk cache.

Locks in the 2026-05-31 perf fixes so a regression shows up as a budget
failure instead of a silent slowdown (per docs/TESTING_MATRIX.md rule
"no perf change ships without before/after numbers"):

  * detect_flavor() os.scandir + early-break fix
    (commit e5a3650) -- load_roster ~2.1 s -> ~0.07 s on a 250-merc install.
  * on-disk portrait-sheet cache (commit 0410b4c)
    -- first roster view after each launch ~1.2 s -> ~0.03 s.

Metrics (all milliseconds, min of N reps):
  make_install_context_cold  one InstallContext build (was the ~2.2 s pig)
  load_roster_cold           load_roster() with a fresh MercProfiles parse
  load_roster_warm           load_roster() with the parse cache warm
  portrait_sheet_bake_cold   full sheet bake (decode every face + composite)
  portrait_sheet_disk_hit    sheet served from the on-disk cache after a
                             simulated fresh sidecar launch (in-memory empty)

Usage (run from the sidecar dir so `mercwizard_core` / `routes` import):
    python tools/perf_roster.py                 # human-readable table
    python tools/perf_roster.py --json          # machine-parseable
    python tools/perf_roster.py --budget-ms     # exit 1 if any metric over budget
    python tools/perf_roster.py --install "C:/path/to/JA2 install"

With no --install, if the JA2_INSTALL_ROOT env var is set the most-modded JA2
install under it (most filled merc slots) is auto-selected, so the benchmark
runs against a realistic worst case.

The benchmark redirects %APPDATA% to a temp dir for the run, so it never
reads or writes the user's real portrait-sheet cache.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# This script lives in <sidecar>/tools/. Put <sidecar> on sys.path so the
# imports work no matter the caller's cwd.
_SIDECAR = Path(__file__).resolve().parent.parent
if str(_SIDECAR) not in sys.path:
    sys.path.insert(0, str(_SIDECAR))


# Budgets in milliseconds. Generous headroom (~5-10x) over the measured
# baseline numbers (a heavily-modded 252-merc install: ~14 / ~72 / ~43 /
# ~1470 / ~34) so normal disk/CPU variance never trips them, but a return of
# the ~2 s detect_flavor pig or the ~1.2 s per-launch bake does.
BUDGETS_MS: dict[str, float] = {
    "make_install_context_cold": 800.0,
    "load_roster_cold": 800.0,
    "load_roster_warm": 500.0,
    "portrait_sheet_bake_cold": 8000.0,
    "portrait_sheet_disk_hit": 400.0,
}


def _time_ms(fn, reps: int = 3) -> float:
    """Run fn reps times, return the min wall-clock in ms (GC disabled)."""
    best = None
    gc.disable()
    try:
        for _ in range(reps):
            start = time.perf_counter()
            fn()
            elapsed = (time.perf_counter() - start) * 1000.0
            best = elapsed if best is None else min(best, elapsed)
    finally:
        gc.enable()
    return round(best or 0.0, 2)


def _count_filled(install_path: Path) -> int:
    """Number of filled merc slots (non-empty zName/zNickname), or -1."""
    try:
        from mercwizard_core import install_context as IC
        from mercwizard_core.inject import profiles_xml
        ctx = IC.make_install_context(install_path)
        slots = profiles_xml.read_all_slots(ctx.profiles_xml_path())
        return sum(
            1 for v in slots.values()
            if (v.get("zName") or "").strip() or (v.get("zNickname") or "").strip()
        )
    except Exception:
        return -1


def _auto_detect_install() -> Path | None:
    """Pick the JA2 install with the most filled merc slots (worst case)."""
    env = os.environ.get("JA2_INSTALL_ROOT")
    if not env:
        return None
    base = Path(env)
    if not base.is_dir():
        return None
    best: Path | None = None
    best_n = 0
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        n = _count_filled(d)
        if n > best_n:
            best, best_n = d, n
    return best


def _resolve_install(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if not p.is_dir():
            sys.exit(f"perf_roster: --install path is not a directory: {p}")
        return p
    p = _auto_detect_install()
    if p is None:
        sys.exit(
            "perf_roster: no --install given and no JA2 install auto-detected. "
            "Set JA2_INSTALL_ROOT to a JA2 parent dir, or pass "
            "--install \"<path to a JA2 1.13 install>\"."
        )
    return p


def run(install_path: Path) -> dict[str, float]:
    """Measure every metric against `install_path`. Returns {name: ms}."""
    from mercwizard_core import install_context as IC
    from mercwizard_core.inject import profiles_xml
    from mercwizard_core.roster import load_roster
    from routes import roster as R

    results: dict[str, float] = {}

    # make_install_context: the build that used to cost ~2.2 s (detect_flavor).
    results["make_install_context_cold"] = _time_ms(
        lambda: IC.make_install_context(install_path)
    )

    # load_roster: cold = fresh MercProfiles parse; warm = parse cache hit.
    profiles_xml.invalidate_parse_cache()
    results["load_roster_cold"] = _time_ms(lambda: load_roster(install_path), reps=1)
    results["load_roster_warm"] = _time_ms(lambda: load_roster(install_path))

    # Portrait sheet. Isolate the on-disk cache to a throwaway dir so the
    # benchmark never touches the user's real %APPDATA%/MercWizard/cache.
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="mw2_perf_")
    R.invalidate_portrait_sheet_cache(None)
    # _bake_portrait_sheet now takes a pre-built InstallContext (the caller
    # builds it once and shares it with the mtime sample that keys the
    # cache). Build one here to mirror that contract.
    _bake_ctx = IC.make_install_context(install_path)
    results["portrait_sheet_bake_cold"] = _time_ms(
        lambda: R._bake_portrait_sheet(_bake_ctx, "smallface"), reps=1
    )

    # Disk-hit: prime the on-disk cache, drop the in-memory tier to mimic a
    # fresh sidecar launch, then time the (disk-served) fetch.
    R.invalidate_portrait_sheet_cache(None)
    R._portrait_sheet_bytes_and_meta("perfbench", install_path, "smallface")
    with R._SHEET_CACHE_LOCK:
        R._SHEET_CACHE.clear()
    results["portrait_sheet_disk_hit"] = _time_ms(
        lambda: R._portrait_sheet_bytes_and_meta("perfbench", install_path, "smallface")
    )
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Roster + portrait-sheet perf benchmark")
    ap.add_argument("--install", default=None, help="Path to a JA2 1.13 install")
    ap.add_argument("--json", action="store_true", help="machine-parseable output")
    ap.add_argument(
        "--budget-ms", action="store_true",
        help="exit 1 if any metric exceeds its built-in budget",
    )
    args = ap.parse_args()

    install_path = _resolve_install(args.install)
    filled = _count_filled(install_path)
    results = run(install_path)

    over = {
        k: {"ms": results[k], "budget_ms": BUDGETS_MS[k]}
        for k in results
        if k in BUDGETS_MS and results[k] > BUDGETS_MS[k]
    }
    passed = not over

    if args.json:
        print(json.dumps({
            "install": str(install_path),
            "filled_slots": filled,
            "results_ms": results,
            "budgets_ms": BUDGETS_MS,
            "over_budget": over,
            "passed": passed,
        }, indent=2))
    else:
        print(f"install      : {install_path}")
        print(f"filled slots : {filled}")
        print("-" * 56)
        for k, ms in results.items():
            budget = BUDGETS_MS.get(k)
            flag = ""
            if budget is not None:
                flag = "  OK" if ms <= budget else f"  OVER (budget {budget:.0f})"
            print(f"  {k:30s} {ms:10.2f} ms{flag}")
        print("-" * 56)
        print("RESULT: PASS" if passed else f"RESULT: FAIL ({len(over)} over budget)")

    if args.budget_ms and not passed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
