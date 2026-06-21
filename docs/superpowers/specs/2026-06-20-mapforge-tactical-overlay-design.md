# MapForge — Read-only Tactical Overlay (design)

**Date:** 2026-06-20
**Branch:** `mapforge-tactical-overlay` (off `main`; USER pushes/merges — agent cannot)
**Status:** approved design → implementation plan next

---

## 1. Problem

Opening sector A9 in MapForge shows no NPCs, unlike the in-game JA2 map
editor. MapForge today renders only the **A-half** of a `.dat` map — the tile
data (land / object / struct / shadow / roof / onroof layers + heights + room
info). The **B-half appendix** — the tactical layer — is verbatim-copied through
on save and **never parsed for display**. Everything in the appendix is
invisible in the editor: NPCs/enemies/civilians, world items, exit grids, door
table, lights, edgepoints, schedules, map-info tail.

## 2. Grounding findings (2026-06-20)

Investigated the real install
(`C:\Jagged Alliance 2\…Mod Prototype - Copy\Data-1.13\Maps`) with the sidecar
parser before designing.

- **Appendix flag bits** (`parse_dat_ext.py`, per `worlddef.cpp:60-68`):
  `SOLDIER 0x01 · WORLDONLY 0x02 · LIGHTS 0x04 · ITEMS 0x08 · EXITGRID 0x10 ·
  DOOR 0x20 · EDGE 0x40 · AMBIENT 0x80 · SCHED 0x100`.
- **Every stock sector (A1/A2/A6/A10/A12/A15/C6/C7) = v5.0, flags `0x17D`** =
  `SOLDIER|LIGHTS|ITEMS|EXITGRID|DOOR|EDGE|SCHED` — full appendix, NPCs present.
- **A9 alone = v7.0.31, flags `0x10` (EXITGRID only)** — no soldier/NPC data at
  all. Flag-chronology of the A9 backups shows the appendix was **stripped
  2026-05-02** when A9 was rebuilt from scratch as the Vault-16/Junktown sector,
  and never re-added; only exit grids returned (today). The original NPCs survive
  in `a9.dat.bak` (2026-05-02, v5.0, `0x179`). **A9 is therefore the worst test
  map** — a perfect overlay still shows nothing there because the data is absent.
  (Whether the in-game editor shows auto-generated/strategic enemies for A9 — not
  stored in any `.dat` — is unconfirmed and out of scope here.)
- **Test target = the v5.0 `0x17D` sectors** (A1/A2…), which carry real
  placements. User-chosen.

### 2a. The hard ordering constraint

Engine `LoadWorld` appendix read order (`worlddef.cpp` ~3171-3264), mirrored in
`parse_appendix_minimal`:

```
items → ambient → lights → mapinfo(tail) → SOLDIERS → exitgrids → doortable → edgepoints → schedules
```

Soldiers (= NPCs) sit in the *middle*. Consequences:

- **Items + lights precede soldiers** → reachable without decoding soldiers.
- **Exit grids / doors / edgepoints follow soldiers** → on any map that has
  soldiers, you cannot reach them until the soldier block is *traversed* (cursor
  advanced past every soldier record). There is **no cheap NPC path** and no way
  to show post-soldier layers on a full map without first cracking the soldier
  record size.

The current parser **bails** (stops advancing) at the variable-size sections:
modern (Path A, v6+) items, lights-with-records, soldiers, schedules. So today it
reaches nothing past the first variable section on a full map.

## 3. Approach

**Read-only parse-extract (sidecar) + frontend marker overlay.** Chosen over (a)
the full parse+edit+**write** appendix engine — that is the data-safety-gated
B-phase, far larger, and not requested — and (b) doing nothing. The parser is
shaped so its record structs *could* feed a future writer, but **this work writes
nothing**: no `.dat` serialization path is touched, so it sidesteps the entire §3
safety regime (per-save round-trip assert, backups, soak) by construction. Read
fidelity need only be enough to **locate + label** each entity, not to round-trip
it — a far lower bar than the writer that B-phase was scoped against.

### 3.1 Sidecar

New deep-extract module in `sidecar/mercwizard_core/mapforge_engine/`
(leaves `parse_dat_full`'s existing return shape stable; deep extract is opt-in).

- Advances the cursor through each section, fixing the latent §2 size bugs **as
  reads only** (sizes derived from the C++ structs via `engine_graph` per
  discipline #5, MSVC-padded — not the reference docs, which §2 proved wrong):
  - exit grid record **12 (v≥7) / 7 (v<7)**; count `uint16`.
  - door table count **uint8** (not uint16); record **12**.
  - light record **24 bytes** (`LIGHT_SPRITE`) + `uint8 strlen` + string;
    preceded by `uint8 numColors` + `numColors×4` palette + `uint16` count.
  - edgepoint **3 encodings** (pre-17 UINT8 / v<7 INT16 / v≥7 INT32), switched on
    the tail's `ubMapVersion`, not the major version; 8 sections.
  - mapinfo tail **32 (v≥7) / 99 (v<7)**.
  - items: Path B (v5.0 fixed-size) parsed; Path A (modern) advances only if
    solvable, else the extractor reports `items` as a blocker and degrades
    gracefully (still returns what it reached).
  - **soldiers**: minimal read — decode each `BASIC_SOLDIERCREATE_STRUCT` enough
    to (1) advance the cursor exactly and (2) extract `{gridno, team/type,
    orientation, optional detailed-flag}`. Exact layout from `engine_graph`
    (`Soldier Create.cpp` / soldier-init load) during implementation.
- Per reachable section, returns a list of entities:
  `{ kind, gridno, x: gridno%160, y: gridno//160, label, … }`.
- Degrades gracefully: if a section can't be sized, stop there, mark it a
  blocker, and return every entity reached before it (never throw).

### 3.2 API

`GET /mapforge/sector/{id}/appendix` → JSON: per-layer entity arrays + a
`reached`/`blocked_at` diagnostic so the UI can show "items shown; soldiers
unparsed (modern items block)".

### 3.3 Frontend

New overlay render pass in `IsoRenderer.ts` + toggle panel in
`MapForgeSector.tsx` (settings entry in `mapforgeSettings.ts`):

- Markers at iso positions per entity kind: NPC (enemy red / civ blue / creature
  green), item (dot), exit (arrow + dest sector), door, light, edgepoint.
- Per-layer show/hide checkboxes; hover tooltip with entity details.
- **Read-only.** No placing, no editing, no save interaction.

## 4. Build order

1. **Infra + items + lights + exit-grids (simple maps)** — the parse pieces that
   need no soldier decode. Establishes the extractor → API → overlay → toggle
   pipeline end-to-end and immediately lets the user *see* a map's contents.
2. **Soldiers / NPCs** — the headline and the hard record. Minimal read
   (position + type + facing). Unblocks NPCs *and* every post-soldier layer.
3. **Post-soldier fixed layers on full maps** — exitgrids, doors, edgepoints
   (cheap once the soldier block is traversable).

NPCs land a step behind the easier layers; that is inherent to the file order,
not a scope cut.

## 5. Testing

- Sidecar: unit tests on the extractor against real v5.0 `0x17D` sectors (A1/A2)
  — assert non-zero soldier/item/exit counts and plausible gridno→x,y. Read-only,
  so no round-trip/data-safety gate applies. Keep within the existing
  `sidecar/tests/` pytest suite.
- Frontend: `tsc --noEmit` clean; manual browser-dev verify
  (`reference_mercforge_browserdev_verify`) showing markers on A1.
- Guard: the existing B0 round-trip audit must stay green (this work doesn't touch
  the writer, so it should be untouched — verify, don't assume).

## 6. Non-goals

- No appendix **writing** / editing / save path (that's the gated B-phase).
- No A9 data recovery (separate; original NPCs are in the 2026-05-02 backup).
- No modern (Path A) item decode beyond best-effort cursor advance.
- No schedules decode in v1 (last in file order, variable; behind soldiers).

## 7. Risks

- **Soldier record size wrong → cursor desync** → everything after it misreads.
  Mitigated: read-only (no corruption risk to the file), validated against real
  maps by asserting the cursor lands exactly on the next known section / EOF.
- **Modern (v7) full-appendix maps** may block at Path A items before soldiers.
  Accepted: v1 targets v5.0 sectors; modern maps degrade gracefully.
