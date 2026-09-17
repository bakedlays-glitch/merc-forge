# Door-table appendix fixtures (Lane A11, F13/F14)

Trimmed real-map fixtures for `tests/test_appendix_door_records.py`. Both
source `.dat` files exceed 300 KB, so only the **appendix tail** (from the
map's `appendix_offset` to EOF) is stored — `extract_appendix_entities()`
only ever indexes into `data` starting at `parsed["appendix_offset"]`, so a
trimmed tail blob with `appendix_offset=0` in the test's hand-built `parsed`
dict is byte-for-byte equivalent to reading the real file for this function.

| Fixture | Source (Copy install) | Offset in source | Tail size | Major | Doors |
|---|---|---|---|---|---|
| `a9_v7_34doors_tail.bin` | `Data-1.13\Maps\a9.dat` | 283779 | 941 B | 7.0 | 34 |
| `A2_v5_3doors_tail.bin` | `Data-1.13\Maps\A2.DAT` | 309874 | 14472 B | 5.0 | 3 |

`*_meta.json` alongside each tail records `source_path`, `source_sha256`
(hash of the FULL original file, for provenance), `source_size`,
`appendix_offset_in_source`, `tail_sha256`, `tail_size`, and the `major`/
`minor`/`flags`/`cols`/`rows` values the test needs to build its `parsed`
dict (all read via `parse_dat_full` against the untouched original).

## Why these two maps

- **a9.dat** (major 7.0) is one of only two hero sectors on the live 7.0
  side of the mixed fleet (F14) and has 34 doors (F13's own example).
- **A2.DAT** was the smallest of the 17 loose major-5.0 maps whose door
  count is actually > 0 — the other small candidates (SOCKTEST, GENSECTOR,
  A15, SOCKETGEN, GENSEED_TS1, A1, A12, A6, C7, GENSEED) all have
  `MAP_DOORTABLE_SAVED` set but a door table COUNT byte of 0, and A7/A8
  (also small) show a `MAP_EXITGRIDS_SAVED`-less flag combo with 84 door
  records that are mostly zero-gridno filler — a different, unrelated
  question, not chased here (see the lane report / F14 follow-up notes).
  `C6.DAT` (14 doors) and `L11.DAT` (6 doors) were also witness-vote-tested
  as cross-checks; see `Headless_Compiler/scratch/catchup_20260905/a11/probe_doorsize.py`
  for the exact values (not persisted in the test module, which only carries
  the two shipped fixtures above).

## How they were regenerated (if ever needed again)

`Headless_Compiler/scratch/catchup_20260905/a11/make_fixtures.py` — reads
the two source files from the Copy read-only, slices `data[appendix_offset:]`,
and writes the tail + metadata sidecar here. Re-run only if a source map is
intentionally re-saved by the live engine (which would change every hash in
this table).
