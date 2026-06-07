"""
distill_generator_corpus.py
===========================

Distill the big empirical map corpus down to the compact, biome-stratified,
subframe-level catalog that MapForge's generators consume. Strips the 16-mod
research corpus down to THREE selectable sources:

    stock    = Jagged Alliance 2 Gold 1.13_2025   (clean modern stock 1.13)
    redux    = Jagged Alliance 2 Redux
    combined = sha1-deduped union of the two

For each source we stratify by BIOME (tileset -> biome via biome_map.py) and
record, per generator LAYER and STI slot, the empirical {sub: count}
distribution — i.e. exactly which subframe vanilla/Redux mappers placed per
tile. The generators turn that into weighted variant picks.

Data sources:
  - Maps: fresh-scanned from disk (reuses build_corpus's iter_*_dats +
    parse_dat_ext + scan_map) so we pick up the land/object subframe catalogs
    that the canonical maps.jsonl predates. Deduped by map sha1 PER SOURCE
    (the canonical maps.jsonl global dedup is order-dependent across all 16
    installs and can't be used for per-source attribution).
  - Buildings: read from building_positional.jsonl (the connected-component
    wall detector's output), filtered to the two installs, deduped within a
    source by (dat_name, source_kind, component_id, bbox). No sha1 in that
    file, so combined-dedup is a byte-identity heuristic, not exact.

Output (committed + shipped with MapForge):
  <out-dir>/generator_corpus.json   the catalog
  <out-dir>/coverage.json           per (source,biome) map/building counts

Run:
    python distill_generator_corpus.py [--out-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

THIS_DIR = Path(__file__).parent
WASTELAND_ROOT = THIS_DIR.parent.parent
sys.path.insert(0, str(THIS_DIR))
sys.path.insert(0, str(WASTELAND_ROOT / "ja2-open-toolset"))

from parse_dat_ext import parse_dat_full, DatParseError       # noqa: E402
from scan_map import scan_map                                  # noqa: E402
from build_corpus import iter_loose_dats, iter_slf_dats        # noqa: E402
from biome_map import biome_for_tileset, BIOMES, TILESET_BIOME # noqa: E402

INSTALLS_PATH = THIS_DIR / "installs.json"
BUILDINGS_PATH = THIS_DIR / "building_positional.jsonl"
DEFAULT_OUT = WASTELAND_ROOT / "MercWizard2" / "sidecar" / "mercwizard_core" / "mapforge" / "corpus"

# source key -> exact install name in installs.json
SOURCE_INSTALLS = {
    "stock": "Jagged Alliance 2 Gold 1.13_2025",
    "redux": "Jagged Alliance 2 Redux",
}

# generator layer name -> scan_map record field holding its {slot:{sub:count}}
LAYER_CATALOGS = {
    "land":    "land_subindex_catalog",
    "objs":    "obj_subindex_catalog",
    "structs": "subindex_catalog",
    "shadows": "shadow_subindex_catalog",
    "roofs":   "roof_subindex_catalog",
    "onroofs": "onroof_subindex_catalog",
}

POS_CLASSES = ("NW", "N", "NE", "W", "Interior", "E", "SW", "S", "SE")
TOP_N_SUBS = 24  # cap distinct subs per (source,biome,layer,slot) in the shipped file


# ── small dict-merge helpers ────────────────────────────────────────────────

def _bump(catalog: dict, slot, sub, n: int = 1) -> None:
    catalog.setdefault(str(slot), {})
    inner = catalog[str(slot)]
    inner[str(sub)] = inner.get(str(sub), 0) + n


def _merge_catalog(dst: dict, src: dict) -> None:
    """Merge a {slot: {sub: count}} catalog into dst (counts add)."""
    for slot, subs in src.items():
        inner = dst.setdefault(str(slot), {})
        for sub, cnt in subs.items():
            inner[str(sub)] = inner.get(str(sub), 0) + cnt


def _cap_subs(scatter: dict, top_n: int = TOP_N_SUBS) -> None:
    """Keep only the top-N subs per (biome,layer,slot) to bound file size."""
    for layers in scatter.values():
        for slots in layers.values():
            for slot, subs in list(slots.items()):
                if len(subs) > top_n:
                    slots[slot] = dict(
                        sorted(subs.items(), key=lambda kv: -kv[1])[:top_n]
                    )


# ── map scan ────────────────────────────────────────────────────────────────

def scan_source_maps(install: dict) -> dict[str, tuple[str, dict]]:
    """Fresh-scan one install's maps. Return {sha1: (biome, {layer: catalog})},
    deduped by sha1 within this source."""
    out: dict[str, tuple[str, dict]] = {}
    name = install["name"]
    for source in install["map_sources"]:
        kind = source["kind"]
        path = Path(source["path"])
        if kind == "loose":
            src_iter = iter_loose_dats(path)
        else:
            try:
                src_iter = iter_slf_dats(path)
            except Exception as e:
                print(f"   [{kind}] SLF open failed {path}: {e}", file=sys.stderr)
                continue
        for dat_name, dat_or_err in src_iter:
            if isinstance(dat_or_err, Exception):
                continue
            data = dat_or_err
            try:
                parsed = parse_dat_full(data, f"{name}/{dat_name}", items_table=None)
                rec = scan_map(parsed, {
                    "install": name, "source_kind": kind,
                    "source_path": str(path), "dat_name": dat_name,
                }, data)
            except (DatParseError, Exception):
                continue
            sha = rec["sha1"]
            if sha in out:
                continue
            biome = biome_for_tileset(rec["tileset"])
            layers = {
                layer: rec.get(field) or {}
                for layer, field in LAYER_CATALOGS.items()
            }
            out[sha] = (biome, layers)
    return out


def aggregate_scatter(maps: list[tuple[str, dict]]) -> dict:
    """maps: list of (biome, {layer: catalog}) -> {biome:{layer:{slot:{sub:count}}}}"""
    out: dict = {}
    for biome, layers in maps:
        bdst = out.setdefault(biome, {})
        for layer, catalog in layers.items():
            if catalog:
                _merge_catalog(bdst.setdefault(layer, {}), catalog)
    _cap_subs(out)
    return out


# ── building aggregation ─────────────────────────────────────────────────────

def _building_key(b: dict):
    bb = b.get("bbox", {})
    return (b.get("dat_name"), b.get("source_kind"), b.get("component_id"),
            bb.get("x0"), bb.get("y0"), bb.get("x1"), bb.get("y1"), b.get("tileset"))


def load_source_buildings() -> dict[str, dict]:
    """Return {source: {building_key: building_record}} for the 2 installs,
    deduped within each source."""
    name_to_source = {v: k for k, v in SOURCE_INSTALLS.items()}
    out: dict[str, dict] = {k: {} for k in SOURCE_INSTALLS}
    if not BUILDINGS_PATH.is_file():
        print(f"   WARN: {BUILDINGS_PATH} missing — building corpus will be empty")
        return out
    with open(BUILDINGS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)
            src = name_to_source.get(b.get("install"))
            if src is None:
                continue
            out[src][_building_key(b)] = b
    return out


def aggregate_buildings(buildings: list[dict]) -> dict:
    """-> {biome: {positions:{POS:{structs,roofs}}, doors:{by_slot,by_edge},
    size_w, size_h, n_buildings}}"""
    out: dict = {}
    for b in buildings:
        biome = biome_for_tileset(b["tileset"])
        bdst = out.setdefault(biome, {
            "positions": {}, "doors": {"by_slot": {}, "by_edge": {}},
            "size_w": {}, "size_h": {}, "n_buildings": 0,
        })
        bdst["n_buildings"] += 1
        bb = b.get("bbox", {})
        if bb.get("w") is not None:
            wk = str(bb["w"]); hk = str(bb.get("h"))
            bdst["size_w"][wk] = bdst["size_w"].get(wk, 0) + 1
            bdst["size_h"][hk] = bdst["size_h"].get(hk, 0) + 1
        for pos, tiles in (b.get("tiles_by_pos") or {}).items():
            pdst = bdst["positions"].setdefault(pos, {"structs": {}, "roofs": {}})
            for tile in tiles:
                for entry in tile.get("structs", []):
                    _bump(pdst["structs"], entry[0], entry[1])
                for entry in tile.get("roofs", []):
                    _bump(pdst["roofs"], entry[0], entry[1])
        for d in (b.get("doors") or []):
            _bump(bdst["doors"]["by_slot"], d.get("type"), d.get("sub"))
            for edge in d.get("edges", []):
                bdst["doors"]["by_edge"][edge] = bdst["doors"]["by_edge"].get(edge, 0) + 1
    return out


# ── coverage ─────────────────────────────────────────────────────────────────

def build_coverage(per_source_maps, per_source_buildings, scatter, buildings) -> dict:
    cov: dict = {}
    for source in ("stock", "redux", "combined"):
        biome_maps = Counter(biome for biome, _ in per_source_maps[source])
        biome_blds = Counter(biome_for_tileset(b["tileset"]) for b in per_source_buildings[source])
        cov[source] = {}
        for biome in BIOMES:
            layers_present = sorted(
                L for L in LAYER_CATALOGS
                if scatter.get(source, {}).get(biome, {}).get(L)
            )
            cov[source][biome] = {
                "n_maps": biome_maps.get(biome, 0),
                "n_buildings": biome_blds.get(biome, 0),
                "layers": layers_present,
                "has_buildings": bool(buildings.get(source, {}).get(biome, {}).get("positions")),
            }
    return cov


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT),
                    help="where to write generator_corpus.json + coverage.json")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    installs = json.loads(INSTALLS_PATH.read_text())["installs"]
    by_name = {i["name"]: i for i in installs}

    t0 = time.time()
    per_source_maps: dict[str, list[tuple[str, dict]]] = {}
    sha_to_map: dict[str, dict[str, tuple[str, dict]]] = {}
    for source, install_name in SOURCE_INSTALLS.items():
        install = by_name.get(install_name)
        if install is None:
            print(f"ERROR: install '{install_name}' not in installs.json", file=sys.stderr)
            sys.exit(1)
        print(f"== scanning {source}: {install_name} ==", flush=True)
        scanned = scan_source_maps(install)
        sha_to_map[source] = scanned
        per_source_maps[source] = list(scanned.values())
        print(f"   {len(scanned)} unique maps", flush=True)

    # combined = sha1-deduped union
    combined_map_dict = {**sha_to_map["stock"], **sha_to_map["redux"]}
    per_source_maps["combined"] = list(combined_map_dict.values())
    print(f"== combined: {len(combined_map_dict)} unique maps ==")

    # buildings
    per_source_buildings_d = load_source_buildings()
    combined_blds = {**per_source_buildings_d["stock"], **per_source_buildings_d["redux"]}
    per_source_buildings = {
        "stock": list(per_source_buildings_d["stock"].values()),
        "redux": list(per_source_buildings_d["redux"].values()),
        "combined": list(combined_blds.values()),
    }
    for s in ("stock", "redux", "combined"):
        print(f"   buildings[{s}]: {len(per_source_buildings[s])}")

    scatter = {s: aggregate_scatter(per_source_maps[s]) for s in ("stock", "redux", "combined")}
    buildings = {s: aggregate_buildings(per_source_buildings[s]) for s in ("stock", "redux", "combined")}

    corpus = {
        "schema_version": 1,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_installs": SOURCE_INSTALLS,
        "sources": ["stock", "redux", "combined"],
        "biomes": list(BIOMES),
        "layers": list(LAYER_CATALOGS.keys()),
        "position_classes": list(POS_CLASSES),
        "tileset_biome": {str(k): v for k, v in sorted(TILESET_BIOME.items())},
        "scatter": scatter,
        "buildings": buildings,
    }
    coverage = build_coverage(per_source_maps, per_source_buildings, scatter, buildings)

    corpus_path = out_dir / "generator_corpus.json"
    coverage_path = out_dir / "coverage.json"
    corpus_path.write_text(json.dumps(corpus, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    coverage_path.write_text(json.dumps(coverage, indent=1, sort_keys=True), encoding="utf-8")

    size_kb = corpus_path.stat().st_size / 1024
    print()
    print(f"Wrote {corpus_path}  ({size_kb:.0f} KB)")
    print(f"Wrote {coverage_path}")
    print(f"Elapsed: {time.time() - t0:.1f}s")
    print()
    print("Coverage (n_maps per biome):")
    for source in ("stock", "redux", "combined"):
        cells = ", ".join(
            f"{b}:{coverage[source][b]['n_maps']}"
            for b in BIOMES if coverage[source][b]["n_maps"]
        )
        print(f"  {source:9s} {cells}")


if __name__ == "__main__":
    main()
