# Generator corpus — build tooling (version-controlled backup)

These scripts build the shipped MapForge generator corpus at
`sidecar/mercwizard_core/mapforge/corpus/generator_corpus.json` (+ `coverage.json`),
which the generators read via `mapforge/corpus/__init__.py`.

**Why these live here:** the canonical map-corpus pipeline is tracked by the
parent Wasteland repository under `Headless_Compiler/map_corpus/`, while
MercWizard2 is a separate nested repository. These committed mirrors preserve
the distiller and biome mapping with MercForge's own history; they are not the
operational copies.

> Snapshot / mirror. The canonical copies that actually run are in
> `Headless_Compiler\map_corpus\`. Sync aggregation changes between the
> copies; the canonical distiller also owns the post-build validation and
> atomic publication wrapper because its validator lives beside the scanner.

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

## Required `scan_map.py` contract

The tracked canonical scanner owns the six fields consumed by
`LAYER_CATALOGS`: `land_subindex_catalog`, `obj_subindex_catalog`,
`subindex_catalog`, `shadow_subindex_catalog`, `roof_subindex_catalog`, and
`onroof_subindex_catalog`. Its regression test is
`Headless_Compiler/map_corpus/test_scan_map.py`. If a recovered checkout loses
any field, stop: regenerating will silently erase that layer from the shipped
corpus. Repair and run the scanner test before invoking the distiller.

## Regenerate
From `Headless_Compiler\map_corpus\` (with the canonical copies + the scan_map edit):
```
python distill_generator_corpus.py --out-dir "<repo>\sidecar\mercwizard_core\mapforge\corpus"
```
The canonical distiller emits schema 1 and the identity-safe schema 2 beside
the shipped schema-2 file. Schema-2 candidates use the compact row layout
declared by `candidate_fields`; active compatibility is keyed by the resolved
STI SHA-256 and joined by per-frame SHA-256. The loader never treats matching
sub numbers as proof of matching art.

~2 min (fresh-scans ~850 maps, plus art hashing). The canonical distiller writes both candidates
to temporary files, runs `validate_generator_corpus.py`, and replaces the
shipped JSON only after validation succeeds. A validation failure leaves the
previous shipped pair untouched.

The shipped pair can also be checked without regeneration:
```
python validate_generator_corpus.py "<repo>\MercWizard2\sidecar\mercwizard_core\mapforge\corpus\generator_corpus.json" "<repo>\MercWizard2\sidecar\mercwizard_core\mapforge\corpus\coverage.json"
```
Commit the updated `generator_corpus.json` + `coverage.json`. Add a biome by
editing `biome_map.py` and re-running.
