"""B0 — corpus round-trip audit (MapForge data-safety gate).

Sweeps the real `.dat` map corpus — loose ``*/Maps/*.dat`` plus
``Maps.slf``-bundled maps — across one or more JA2 installs and asserts the
sidecar's round-trip is byte-perfect::

    write_dat_bytes(parse_dat_full(data), data) == data

This is the safety gate the expansion plan calls B0. TODAY the writer
verbatim-copies the appendix, so a GREEN run proves the *tile-data* writer
(header + heights + the 6 layers + room-info) re-serializes byte-identically
corpus-wide — i.e. MapForge's edits never corrupt the bytes it doesn't
touch. When the B-phase replaces the verbatim appendix copy with real
serialization, the SAME harness validates those bytes too, and the latent
appendix-parser size bugs the plan predicts (exitgrid/door/edgepoint) will
surface here as `appendix` divergences instead of staying masked.

READ-ONLY: never writes to any install. Run on demand, e.g.::

    sidecar/.venv/Scripts/python.exe tools/roundtrip_audit.py \
        "C:/Jagged Alliance 2/Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"

    sidecar/.venv/Scripts/python.exe tools/roundtrip_audit.py \
        --installs-dir "C:/Jagged Alliance 2"

Exit code 0 iff every map round-trips byte-identically (the gate).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Iterator, Optional

# Run from anywhere — put the sidecar package root on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mercwizard_core.mapforge_engine.parse_dat_ext import (  # noqa: E402
    DatParseError,
    parse_dat_full,
)
from mercwizard_core.mapforge_engine.dat_writer import write_dat_bytes  # noqa: E402


# ── Round-trip a single map ───────────────────────────────────────────────


def _regions(parsed: dict) -> list[tuple[str, int, Optional[int]]]:
    """(name, start, end) byte regions, for classifying a divergence. The
    final region's end is None (runs to EOF)."""
    hl = parsed["header_len"]
    wm = parsed["rows"] * parsed["cols"]
    lc = hl + 2 * wm                 # heights region is 2 bytes/tile
    layers = lc + 4 * wm             # layer-count nibbles are 4 bytes/tile
    appx = parsed["appendix_offset"]
    return [
        ("header", 0, hl),
        ("heights", hl, lc),
        ("layer-counts", lc, layers),
        ("layer+room", layers, appx),
        ("appendix", appx, None),    # verbatim-copied today → must never differ
    ]


def _first_diff(a: bytes, b: bytes) -> int:
    """First differing byte offset, or -1 if equal. If one is a prefix of
    the other, the offset is the length of the shorter."""
    m = min(len(a), len(b))
    for i in range(m):
        if a[i] != b[i]:
            return i
    return -1 if len(a) == len(b) else m


def _classify(parsed: dict, off: int) -> str:
    for name, start, end in _regions(parsed):
        if end is None:
            if off >= start:
                return name
        elif start <= off < end:
            return name
    return "length"


def roundtrip_one(label: str, data: bytes) -> dict:
    """Round-trip one map's bytes. Returns a result dict with a `status`
    of ok / diverged / parse_error / parse_crash / write_crash."""
    try:
        parsed = parse_dat_full(data, label)
    except DatParseError as e:
        return {"status": "parse_error", "map": label, "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — a crash is itself a finding
        return {"status": "parse_crash", "map": label,
                "detail": f"{type(e).__name__}: {e}"}

    ver = parsed.get("major")
    try:
        out = write_dat_bytes(parsed, data)
    except Exception as e:  # noqa: BLE001
        return {"status": "write_crash", "map": label, "version": ver,
                "detail": f"{type(e).__name__}: {e}"}

    if out == data:
        return {"status": "ok", "map": label, "version": ver}
    off = _first_diff(out, data)
    return {
        "status": "diverged", "map": label, "version": ver,
        "first_diff": off, "region": _classify(parsed, off),
        "len_out": len(out), "len_orig": len(data),
    }


# ── Corpus discovery ──────────────────────────────────────────────────────


def _is_map_path(p: Path) -> bool:
    if p.suffix.lower() != ".dat":
        return False
    return any(part.lower() == "maps" for part in p.parts)


def iter_corpus(roots: list[Path], include_slf: bool = True) -> Iterator[tuple[str, bytes]]:
    """Yield (label, bytes) for every `.dat` map under each install root —
    loose ``*/Maps/*.dat`` plus, if available, ``Maps.slf``-bundled maps."""
    slf_cls = None
    if include_slf:
        try:
            from ja2py.fileformats.SlfFS import SlfFS  # noqa: E402
            slf_cls = SlfFS
        except Exception as e:  # noqa: BLE001
            print(f"  (SLF support unavailable: {e} — loose maps only)", file=sys.stderr)

    for root in roots:
        tag = root.name
        # Loose maps.
        for p in root.rglob("*"):
            if _is_map_path(p) and p.is_file():
                try:
                    yield (f"{tag}:{p.relative_to(root)}", p.read_bytes())
                except OSError as e:
                    print(f"  (skip {p}: {e})", file=sys.stderr)
        # SLF-bundled maps.
        if slf_cls is None:
            continue
        for slf in root.rglob("*.slf"):
            if slf.name.lower() != "maps.slf":
                continue
            try:
                fs = slf_cls(str(slf))
            except Exception:  # noqa: BLE001
                continue
            try:
                for internal in fs.walk.files():
                    if not internal.lower().endswith(".dat"):
                        continue
                    try:
                        yield (f"{tag}:slf:{internal}", fs.readbytes(internal))
                    except Exception as e:  # noqa: BLE001
                        print(f"  (skip {slf.name}!{internal}: {e})", file=sys.stderr)
            except Exception:  # noqa: BLE001
                pass


# ── Driver ────────────────────────────────────────────────────────────────


def run_audit(roots: list[Path], include_slf: bool = True,
              max_fail_lines: int = 60) -> int:
    """Sweep + report. Returns a process exit code (0 = all green)."""
    results: list[dict] = []
    for label, data in iter_corpus(roots, include_slf=include_slf):
        results.append(roundtrip_one(label, data))

    total = len(results)
    by_status = Counter(r["status"] for r in results)
    ok = by_status.get("ok", 0)
    bad = [r for r in results if r["status"] != "ok"]

    # Per-version pass/fail (only for maps that parsed far enough to know).
    ver_total: Counter = Counter()
    ver_ok: Counter = Counter()
    for r in results:
        v = r.get("version")
        if v is None:
            continue
        ver_total[v] += 1
        if r["status"] == "ok":
            ver_ok[v] += 1

    print("\n" + "=" * 64)
    print(f"B0 round-trip audit — {total} maps swept")
    print("=" * 64)
    print(f"  OK         : {ok}")
    for st in ("diverged", "parse_error", "parse_crash", "write_crash"):
        if by_status.get(st):
            print(f"  {st:<11}: {by_status[st]}")
    if ver_total:
        print("\n  By format version (ok / total):")
        for v in sorted(ver_total):
            print(f"    v{v:<5}: {ver_ok[v]} / {ver_total[v]}")

    # Divergences grouped by region — the actionable diagnostic.
    div = [r for r in bad if r["status"] == "diverged"]
    if div:
        region_ct = Counter(r["region"] for r in div)
        print("\n  Divergences by region:")
        for region, c in region_ct.most_common():
            print(f"    {region:<14}: {c}")

    if bad:
        print(f"\n  First {min(len(bad), max_fail_lines)} non-OK maps:")
        for r in bad[:max_fail_lines]:
            if r["status"] == "diverged":
                print(f"    DIVERGED {r['map']}  v{r.get('version')}  "
                      f"@{r['first_diff']} in {r['region']}  "
                      f"(len {r['len_out']} vs {r['len_orig']})")
            else:
                print(f"    {r['status'].upper()} {r['map']}  {r.get('detail', '')}")
        if len(bad) > max_fail_lines:
            print(f"    ... and {len(bad) - max_fail_lines} more")

    green = total > 0 and not bad
    print("\n" + ("  RESULT: 100% GREEN (gate passed)" if green
                  else f"  RESULT: {len(bad)} non-OK of {total} - gate NOT passed"))
    print("=" * 64)
    return 0 if green else 1


def _resolve_roots(args: argparse.Namespace) -> list[Path]:
    roots: list[Path] = [Path(r) for r in args.roots]
    if args.installs_dir:
        base = Path(args.installs_dir)
        roots += [p for p in base.iterdir() if p.is_dir()]
    return [r for r in roots if r.exists()]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", help="install root dir(s) to sweep")
    ap.add_argument("--installs-dir", help="sweep every immediate subdir as an install root")
    ap.add_argument("--no-slf", action="store_true", help="loose maps only (skip Maps.slf)")
    args = ap.parse_args(argv)

    roots = _resolve_roots(args)
    if not roots:
        ap.error("no existing install roots given (pass paths or --installs-dir)")
    print(f"Sweeping {len(roots)} install root(s)…", file=sys.stderr)
    return run_audit(roots, include_slf=not args.no_slf)


if __name__ == "__main__":
    raise SystemExit(main())
