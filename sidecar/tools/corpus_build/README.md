# Generator corpus — build tooling (version-controlled backup)

These scripts build the shipped MapForge generator corpus at
`sidecar/mercwizard_core/mapforge/corpus/generator_corpus.json` (+ `coverage.json`),
which the generators read via `mapforge/corpus/__init__.py`.

**Why these live here:** the dev-side map-corpus pipeline lives in a separate,
unversioned `Headless_Compiler/map_corpus/` project (not part of this repo). The
shipped JSON is committed, but the scripts that regenerate it were on-disk only.
These are committed copies so the build logic can't be lost.

> Snapshot / mirror. The canonical copies that actually run are in
> `Headless_Compiler\map_corpus\`. If you change one, sync the other.

## Files here
- `distill_generator_corpus.py` — the distiller. Fresh-scans the two source
  installs (`Jagged Alliance 2 Gold 1.13_2025` = stock, `Jagged Alliance 2 Redux`),
  per-source sha1-dedupes, rolls up per `(source, biome, layer, slot) → {sub: weight}`
  for scatter and per `(source, biome, position-class)` for buildings, and writes
  `generator_corpus.json` + `coverage.json`.
- `biome_map.py` — the tileset-id → fine-biome mapping (urban/desert/.../wasteland),
  hand-assigned from `tileset_corpus/tilesets.csv` names. Single source of truth.

## Dev-side dependencies (NOT in this repo — in `Headless_Compiler\map_corpus\`)
- `maps.jsonl` — 8,038 scanned maps across 16 installs (built by `build_corpus.py`).
- `building_positional.jsonl` — 6,467 detected buildings (built by `scan_buildings.py`).
- `installs.json` — install → map-source paths (built by `enumerate_installs.py`).
- `scan_map.py`, `parse_dat_ext.py`, `build_corpus.py` — the scanner the distiller imports.
- The JA2 installs on the dev machine (the distiller re-parses the two
  source installs' `.dat` files for land/object subframe catalogs).

## Required `scan_map.py` edit
The distiller needs land + object subframe catalogs that the original `scan_map.py`
didn't emit (it read `(t, s)` for the land layer but discarded `s`). If regenerating
from a fresh Headless checkout, re-apply these:

In the land-histogram loop:
```python
    land_type_counter: Counter = Counter()
    land_subindex_by_type: Dict[int, Counter] = {}
    for i in range(world_max):
        for (t, s) in parsed["land"][i]:
            land_type_counter[t] += 1
            land_subindex_by_type.setdefault(t, Counter())[s] += 1
```
A new object-layer pass (after the onroof pass):
```python
    obj_type_counter: Counter = Counter()
    obj_subindex_by_type: Dict[int, Counter] = {}
    objs_layer = parsed.get("objs")
    if objs_layer is not None:
        for i in range(world_max):
            for (t, s) in objs_layer[i]:
                obj_type_counter[t] += 1
                obj_subindex_by_type.setdefault(t, Counter())[s] += 1
```
And in the emitted record dict:
```python
        "land_subindex_catalog": {
            str(t): dict(land_subindex_by_type[t].most_common(20))
            for t in sorted(land_subindex_by_type)
        },
        "obj_types_top": dict(obj_type_counter.most_common(30)),
        "obj_subindex_catalog": {
            str(t): dict(obj_subindex_by_type[t].most_common(20))
            for t in sorted(obj_subindex_by_type)
        },
```

## Regenerate
From `Headless_Compiler\map_corpus\` (with the canonical copies + the scan_map edit):
```
python distill_generator_corpus.py --out-dir "<repo>\sidecar\mercwizard_core\mapforge\corpus"
```
~2 min (fresh-scans ~850 maps). Commit the updated `generator_corpus.json` +
`coverage.json`. Add a biome by editing `biome_map.py` and re-running.
