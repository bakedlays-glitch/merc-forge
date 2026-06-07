"""Batch runner for the perf_*.py scripts. Runs each against a known
fixture install + sector and prints a single summary table.

Used by the testing matrix's pre-demo readiness check (§14) to confirm
all perf budgets are within range before recording.

Usage:
    cd <repo>\\sidecar
    .venv\\Scripts\\python.exe tools\\perf_run_all.py --install "<your install>"
    .venv\\Scripts\\python.exe tools\\perf_run_all.py --install "<your install>" --cold

`--cold` clears every relevant cache before measuring — gives the
worst-case numbers a real user would hit on first launch.

Pass `--install "C:\\path\\to\\install"` to point at a JA2 1.13 install to
measure against (or set the MW2_TEST_INSTALL env var). The runner picks a
sensible default tileset + sector inside the install.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


# Default test install (override via --install / MW2_TEST_INSTALL) + sample
# sectors (override via --sectors).
_DEFAULT_INSTALL = os.environ.get("MW2_TEST_INSTALL", "")
# Sectors to time. Mix of small + large to surface "this map is bigger"
# perf cliffs.
_DEFAULT_SECTORS = ["C5.dat", "C6.dat", "G8.dat"]


def _find_xml(install_root: Path) -> Path | None:
    for sub in ("Data-1.13", "Data", "Data-UB"):
        cand = install_root / sub / "Ja2Set.dat.xml"
        if cand.is_file():
            return cand
    return None


def _find_maps_dir(install_root: Path) -> Path | None:
    for sub in ("Data-1.13", "Data"):
        cand = install_root / sub / "Maps"
        if cand.is_dir():
            return cand
    return None


def _tileset_for_dat(dat_path: Path) -> int | None:
    """Read the tileset index from the .dat header via the same
    parse_dat_file the sidecar uses."""
    try:
        # Sidecar's import paths are wired by perf_session_open.py et al.
        # For the run_all wrapper, just call the script and parse JSON
        # — avoids duplicating sys.path setup here.
        # Depends on the separate Headless_Compiler project (not in this repo).
        # Set WASTELAND_ROOT to the dir containing your Headless_Compiler checkout.
        import sys as _sys
        _wr = os.environ.get("WASTELAND_ROOT")
        if _wr:
            _sys.path.insert(0, str(Path(_wr) / "Headless_Compiler"))
            _sys.path.insert(0, str(Path(_wr) / "Headless_Compiler" / "map_corpus"))
        from parse_dat_ext import parse_dat_file  # noqa: E402
    except ImportError:
        return None
    try:
        parsed = parse_dat_file(dat_path)
    except Exception:  # noqa: BLE001
        return None
    return parsed.get("tileset")


def _run(script: str, args: list[str]) -> dict:
    """Run a perf_*.py with --json and return the parsed result, or an
    error dict if it fails."""
    here = Path(__file__).resolve().parent
    cmd = [sys.executable, str(here / script), "--json", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        return {"error": f"subprocess launch failed: {e}"}
    if proc.returncode not in (0, 1):  # 1 = over budget, still parse
        return {
            "error": f"exit {proc.returncode}",
            "stderr": proc.stderr.strip()[:500],
        }
    try:
        return json.loads(proc.stdout.strip())
    except (json.JSONDecodeError, ValueError) as e:
        return {"error": f"non-json output: {e}", "stdout": proc.stdout[:500]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install",
        default=_DEFAULT_INSTALL,
        help="Install root (or set the MW2_TEST_INSTALL env var)",
    )
    parser.add_argument(
        "--sectors",
        nargs="+",
        default=_DEFAULT_SECTORS,
        help="Sector .dat filenames to time (default: C5/C6/G8)",
    )
    parser.add_argument(
        "--cold",
        action="store_true",
        help="Force cold runs (clear caches first)",
    )
    args = parser.parse_args()

    if not args.install:
        print(
            "ERROR: no install given. Pass --install \"<path>\" or set MW2_TEST_INSTALL.",
            file=sys.stderr,
        )
        return 2
    install_root = Path(args.install)
    xml_path = _find_xml(install_root)
    maps_dir = _find_maps_dir(install_root)
    if xml_path is None:
        print(f"ERROR: no Ja2Set.dat.xml under {install_root}", file=sys.stderr)
        return 2
    if maps_dir is None:
        print(f"ERROR: no Maps/ dir under {install_root}", file=sys.stderr)
        return 2

    print(f"install: {install_root.name}")
    print(f"xml    : {xml_path}")
    print(f"sectors: {args.sectors}")
    print(f"mode   : {'COLD' if args.cold else 'warm'}")
    print()

    # Resolve sectors → tileset indices. Avoids re-doing this per
    # perf script.
    sector_paths = []
    for fn in args.sectors:
        p = maps_dir / fn
        if not p.is_file():
            print(f"  [skip] {fn} not in {maps_dir}", file=sys.stderr)
            continue
        ts = _tileset_for_dat(p)
        sector_paths.append((p, ts))

    # Build a unique tileset list — bake once per tileset.
    tilesets = sorted({ts for _p, ts in sector_paths if ts is not None})

    results: list[tuple[str, str, dict]] = []

    # Roster + portrait-sheet perf (install-based; not per-tileset/sector).
    # Runs perf_roster.py once against the whole install.
    results.append((
        "roster", install_root.name,
        _run("perf_roster.py", ["--install", str(install_root)]),
    ))

    # Atlas bake per unique tileset.
    for ts in tilesets:
        extra = ["--cold"] if args.cold else []
        res = _run("perf_atlas_bake.py", [
            "--xml", str(xml_path),
            "--tileset", str(ts),
            *extra,
        ])
        results.append(("atlas_bake", f"tileset {ts}", res))

    # Palette sheet per unique tileset.
    for ts in tilesets:
        extra = ["--cold"] if args.cold else []
        res = _run("perf_palette_sheet.py", [
            "--xml", str(xml_path),
            "--tileset", str(ts),
            *extra,
        ])
        results.append(("palette_sheet", f"tileset {ts}", res))

    # Session open per sector. cold_atlas only on first call per
    # tileset to avoid re-baking 3x for one tileset.
    seen_tilesets: set[int] = set()
    for p, ts in sector_paths:
        cold_atlas_arg = []
        if args.cold and ts is not None and ts not in seen_tilesets:
            cold_atlas_arg = ["--cold-atlas"]
            seen_tilesets.add(ts)
        res = _run("perf_session_open.py", [
            "--dat", str(p),
            *cold_atlas_arg,
        ])
        results.append(("session_open", p.name, res))

    # Print summary table.
    print(f"{'script':<16} {'target':<14} {'total':>10}  {'detail'}")
    print(f"{'-'*16} {'-'*14} {'-'*10}  {'-'*40}")
    over_budget_count = 0
    for script, target, res in results:
        if "error" in res:
            print(f"{script:<16} {target:<14} {'ERR':>10}  {res['error'][:50]}")
            continue
        if script == "roster":
            rm = res.get("results_ms", {})
            total = f"{rm.get('load_roster_cold', '?')} ms"
            detail = (
                f"bake {rm.get('portrait_sheet_bake_cold','?')}ms  "
                f"disk-hit {rm.get('portrait_sheet_disk_hit','?')}ms"
            )
        else:
            total = f"{res.get('total_ms', '?')} ms"
            detail = ""
            if script == "atlas_bake":
                detail = f"{res.get('atlas_size','')}  {res.get('slots_baked','?')} slots"
            elif script == "palette_sheet":
                detail = f"from_cache={res.get('from_cache','?')}  {res.get('png_kb','?')} KB"
            elif script == "session_open":
                detail = f"parse={res.get('parse_ms','?')}ms  atlas={res.get('atlas_ms','?')}ms"
        print(f"{script:<16} {target:<14} {total:>10}  {detail}")
        if res.get("over_budget"):
            over_budget_count += 1

    print()
    if over_budget_count:
        print(f"!! {over_budget_count} runs OVER BUDGET. See per-script output for details.")
        return 1
    print("All measured runs within informational range (no --budget-ms set in batch).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
