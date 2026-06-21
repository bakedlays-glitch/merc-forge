# Item Editor — Design

**Date:** 2026-06-21
**Status:** Approved (brainstorming) — ready for implementation plan
**Target:** Merc Forge (MercWizard2) desktop app — Tauri shell + FastAPI Python sidecar

## Goal

Add an in-app editor that lets the user browse every JA2 1.13 item, see each
item's inventory graphic, edit its properties (common fields + per-class stats),
change which graphic an item uses, create new items, and (later) import new art
from a PNG. Mirrors the existing **Backgrounds editor** in structure and safety.

## Background / current state

- **Items.xml** is the master item table: **1854 items** (`uiIndex` 0–1853),
  ~150 fields each. Lives at `Data-1.13/TableData/Items/Items.xml` (with a
  `Data/TableData/Items/Items.xml` fallback — same resolution as
  `item_graphic.py`).
- **Per-class stats live in sister XMLs**, keyed by the item's **`ubClassIndex`**
  field (NOT its global `uiIndex`). Verified: Items.xml `Glock 17` has
  `uiIndex=1, ubClassIndex=1` → Weapons.xml row `uiIndex=1` = `Glock 17`
  (impact 25, range 115). The sister files' `<uiIndex>` element is the
  *class index*, not the global item id.
- **Item class** is the bitfield `usItemClass` (`Tactical/Item Types.h`):
  `IC_NONE=0x1, IC_GUN=0x2, IC_BLADE=0x4, IC_THROWING_KNIFE=0x8,
  IC_LAUNCHER=0x10, IC_TENTACLES=0x20, IC_THROWN=0x40, IC_PUNCH=0x80,
  IC_GRENADE=0x100, IC_BOMB=0x200, IC_AMMO=0x400, IC_ARMOUR=0x800,
  IC_MEDKIT=0x1000, IC_KIT=0x2000, IC_FACE=0x8000, IC_KEY=0x10000, …`.
- **Image rendering already exists**:
  `mercwizard_core/mapforge_engine/item_graphic.py :: render_item_graphic(
  install_root, us_item)` decodes an item's BIGITEMS graphic to PNG via
  `ubGraphicType`/`ubGraphicNum` → BIGITEMS stem (`gun<NN>` for type 0, else
  `p<type>item<NN>`) → STI frame 0. Exposed at `GET /item-graphic?item=N`
  (`routes/mapforge.py`). Recipe documented in
  `2026-06-21-item-graphic-research.md`.
- **The template to mirror** is the Backgrounds editor:
  - `mercwizard_core/backgrounds_schema.py` — engine-derived field specs
    (caps, clamps, flag-vs-int), `schema_payload()` for the UI.
  - `mercwizard_core/inject/backgrounds_xml.py` — byte-splice read/edit/create/
    delete that preserves multi-line descriptions, nested lists, and unknown
    columns; never reflows the whole file.
  - `routes/backgrounds.py` — `GET` (list + schema) and `POST/PUT/DELETE`, each
    wrapped in `cross_process_install_lock` + `snapshot()` (Backups-restorable),
    validating + clamping, refusing the `uiIndex 0` template row.
  - Frontend: `routes/Backgrounds.tsx`, `components/forms/BackgroundForm.tsx`,
    `components/BackgroundPicker.tsx`.

## Class → sister-file map

| Item class bits | Sister file | Root / record | Key stats (S2) |
|---|---|---|---|
| `IC_GUN|BLADE|THROWING_KNIFE|LAUNCHER|TENTACLES|PUNCH` | Weapons.xml | `<WEAPONLIST>/<WEAPON>` | impact, range, accuracy, AP/burst, mag size, calibre |
| `IC_AMMO` | Magazines.xml | `<MAGAZINELIST>/<MAGAZINE>` | calibre, mag size, ammo type |
| `IC_ARMOUR|FACE` | Armours.xml | `<ARMOURLIST>/<ARMOUR>` | protection, coverage, degrade % |
| `IC_GRENADE|BOMB` | Explosives.xml | `<EXPLOSIVELIST>/<EXPLOSIVE>` | damage, stun, radius, volatility |
| (later) drugs / food / clothes | Drugs/Food/Clothes.xml | — | deferred past S2 |

Resolution is by `ubClassIndex` into the matching file's `<uiIndex>` row. An
item with no class-specific bit (MISC, KEY, MONEY, NONE) has common fields only.

## Slices (build order)

The spec covers all four; the first implementation plan targets **S1 + S2**.

### S1 — Browse + edit common fields + re-point image
- Virtualized grid of all 1854 items: thumbnail (`GET /item-graphic`), name,
  `uiIndex`, class label, price, coolness. Search by name/id + filter by class.
- Edit panel for the curated common Items.xml fields:
  `szItemName`, `szLongItemName`, `szItemDesc`, `szBRName`, `szBRDesc`,
  `usItemClass` (read-mostly / advanced), `usPrice`, `ubCoolness`, `ubWeight`,
  `ItemSize`, `bReliability`, `bRepairEase`, `ubPerPocket`,
  `ubGraphicType`, `ubGraphicNum`.
- **Re-point image**: a BigItem picker browses every existing BIGITEMS graphic
  (`gun*`, `p*item*`), previews them, and on pick sets `ubGraphicType`/
  `ubGraphicNum`. Live preview before save.
- Writes Items.xml only.

### S2 — Per-class stats (front-loaded per user request)
- When the selected item is gun/melee/launcher, armour, ammo, or explosive,
  resolve `ubClassIndex` → sister row and show its stats in the same edit panel.
- Saving an item that touches both files writes Items.xml **and** the sister
  file inside one `cross_process_install_lock` + a single snapshot batch
  (both files backed up together so a restore is consistent).

### S3 — Create new / duplicate
- Allocate the next free `uiIndex` (and a free `ubClassIndex` within the class
  family), insert coordinated rows into Items.xml + the right sister file.
- "Duplicate" seeds every field from an existing item (known-good template),
  then assigns fresh ids. Engine item-count caps (`MAXITEMS`) enforced.

### S4 — PNG import
- Bake a new BIGITEMS STI (+ the small INVENTORY graphic) from a supplied PNG
  (palette quantize + ETRLE encode via the existing STI stack), write it to the
  writable VFS layer, then re-point the item. Real art pipeline; specced in
  detail when S1–S3 land.

## Architecture

### Backend (Python sidecar)
- `mercwizard_core/items_schema.py` — engine-derived specs for the common
  Items.xml fields and each sister file's editable fields: type, min/max clamp,
  flag-vs-int, field caps; the class→file map; `schema_payload()` for the UI.
- `mercwizard_core/inject/items_xml.py` — byte-splice read/edit/create/delete on
  Items.xml. Preserves the ~135 untouched columns, unknown mod columns,
  multi-line descriptions, declaration/whitespace, and encoding. Never reflows.
- `mercwizard_core/inject/weapons_xml.py`, `armours_xml.py`, `magazines_xml.py`,
  `explosives_xml.py` — same byte-splice discipline per sister file, keyed by
  class index.
- `routes/items.py`:
  - `GET /items` — list (id, name, class, price, coolness, graphic ref) + schema.
  - `GET /items/{id}` — full common fields + resolved sister-row fields.
  - `PUT /items/{id}` — edit common (and sister) fields; coordinated multi-file
    write under one lock + snapshot.
  - `POST /items` — create / duplicate (S3).
  - `DELETE /items/{id}` — delete (S3); refuse `uiIndex 0`.
  - `GET /bigitems-catalog` — enumerate existing BIGITEMS stems for the picker.
  - `GET /item-graphic?item=N` — already exists; reused for thumbnails.
- Install resolution: **active install** via the existing `_resolve_install` /
  `_active_install_root` helpers (same as Backgrounds/roster).

### Frontend (React/Vite)
- `routes/Items.tsx` — windowed/virtualized grid (1854 rows needs windowing),
  search + class filter, selection → edit panel.
- `components/forms/ItemCommonForm.tsx` — common fields.
- `components/forms/WeaponStatsForm.tsx` / `ArmourStatsForm.tsx` /
  `MagazineStatsForm.tsx` / `ExplosiveStatsForm.tsx` — per-class (S2).
- `components/BigItemPicker.tsx` — browse + preview existing art, re-point.
- Wire a new "Items" entry into Hub + App nav, mirroring Backgrounds.

## Safety & correctness

- **Byte-splice, never reflow.** Edits touch only the target record's bytes.
  Honors the project's XML-encoding discipline (write UTF-8 + declaration; never
  echo Windows-1252) so the engine's expat loader can still parse the file.
- **Lock + snapshot every write.** `cross_process_install_lock(install_id)` +
  `snapshot()` of every file touched (both Items.xml and the sister file in a
  multi-file save) before mutation, restorable from the Backups page.
- **Validate + clamp** to engine ranges (audit-style), refuse illegal XML
  control chars, refuse the `uiIndex 0` template.
- **`ubClassIndex` linkage** is the one load-bearing detail; capture it in a
  short research doc (extend `2026-06-21-item-graphic-research.md` or a new
  `item-class-linkage-research.md`) before implementing S2, including how a
  newly-created item allocates a free class index (S3).
- **Game-running guard** for writes where the rest of the app uses it.

## Testing

Mirror `tests/test_backgrounds_xml.py`:
- Round-trip: edit one field → every untouched column byte-identical; unknown
  columns preserved; encoding preserved.
- Clamp/validation: out-of-range values clamped; illegal chars rejected;
  template row protected.
- Class resolution: item → correct sister file + row; multi-class bitfields
  resolve to the right family; classless items show common-only.
- Multi-file save (S2): both files snapshotted; partial-failure leaves both
  files unchanged (or restores).
- Create/duplicate (S3): fresh ids allocated; coordinated rows consistent.

## Out of scope (for now)

- Drugs/Food/Clothes/LBE/attachment-system editing (past S2).
- Attachment compatibility, merges, transformations, ammo-string editing.
- Bulk/CSV import-export.
- The generic config-driven table-editor refactor (approach C) — revisit only
  if a third XML-table editor is requested.
