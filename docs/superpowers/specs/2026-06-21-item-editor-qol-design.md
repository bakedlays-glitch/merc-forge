# Item Editor QoL (v2) — Design

**Date:** 2026-06-21
**Status:** Approved (brainstorming) — ready for implementation plan
**Target:** Merc Forge (MercWizard2) — Tauri + React/Vite frontend over a Python FastAPI sidecar
**Builds on:** `2026-06-21-item-editor-design.md` (the shipped S1+S2 editor)

## Goal

Turn the shipped Items editor into a tool a modder can live in: correct
navigation, comprehensible fields (definitions + enum dropdowns), a trustworthy
graphic picker, and no silent data loss. Hardened by an antagonistic design
review + an independent context-gapped gap scan + empirical verification against
the canonical install.

## What shipped (the starting point)

`frontend/src/routes/Items.tsx` (2-column: search-only list capped at 400 /
scroll-everything edit panel), `components/forms/ItemCommonForm.tsx` +
`ItemClassStatsForm.tsx` (flat grouped fieldsets, `title={f.note ?? f.key}`
tooltips — but only `usItemClass` has a note), `components/BigItemPicker.tsx`
(modal grid, every sprite forced to `h-10`, one authenticated fetch per sprite),
`items_schema.py` (FieldSpec with label/group/kind/min/max/cap/advanced/note),
`routes/items.py` (`/items`, `/items/{id}`, `PUT`, `/bigitems-catalog`,
`/bigitem-graphic`). Thumbnails use `/mapforge/item-graphic?item=N`.

## Engine truth (verified for this spec)

- **Item class is a bitfield** (`Tactical/Item Types.h:655-682`). Empirically,
  **no item in the canonical install has more than one non-MISC class bit**
  (checked all 1878 items) — so a single-tab *partition* and honest counts are
  safe; first-match resolution is correct.
- **Navigation taxonomy = the engine's own `IC_MAPFILTER_*`**
  (`Item Types.h:692-700`), **8 groups**: Guns (`GUN|LAUNCHER`), Ammo (`AMMO`),
  Explosives (`GRENADE|BOMB`), Melee (`BLADE|PUNCH|THROWN|THROWING_KNIFE`),
  Kits (`KIT|MEDKIT|APPLIABLE`), LBE (`LBEGEAR|BELTCLIP`), Armor (`ARMOUR|FACE`),
  Misc (`TENTACLES|KEY|MISC|MONEY|NONE`). Canonical counts:
  Guns 415, Ammo 619, Explosives 130, Melee 43, Kits 30, LBE 108, Armor 246,
  Misc 266 (sum = 1857; remaining ids are the template/zero rows).
- **Sister-file family ≠ navigation tab.** Family decides which stats form/file
  applies; tab decides the list grouping. They are different partitions
  (LAUNCHER is in the *Guns* tab but the *Weapon* family; Melee items are in the
  *Melee* tab but also the *Weapon* family). Both are engine-derived.
- **Family mask (sister-file resolution), empirically verified** — every item
  with any of `GUN|BLADE|THROWING_KNIFE|LAUNCHER|TENTACLES|THROWN|PUNCH` has a
  row in Weapons.xml at its `ubClassIndex` (388/388 guns, 24/24 blades, 12/12
  punch, 2/2 thrown, …). The **shipped `_IC_WEAPON` mask omits `THROWN` (0x40)**,
  so 2 thrown weapons currently get no stats form — **fix: add 0x40**. Ammo→
  Magazines.xml (`AMMO`), Armour→Armours.xml (`ARMOUR|FACE`), Explosive→
  Explosives.xml (`GRENADE|BOMB`). Kits/Medkits/LBE/Keys/Misc have no S2 sister
  family (common-fields-only).
- **Enum sources:** `ubCalibre` → AmmoStrings.xml `<AmmoCaliber>` keyed by
  `uiIndex` (".38 Spc", "9x19mm", …); `ubAmmoType` → AmmoTypes.xml keyed by
  `uiIndex`; `ubWeaponType` / `ubArmourClass` / `ubType` (explosive) → engine
  `#define`/enum tables in the source headers. Exact values are extracted +
  cited in the plan's first task, not hand-typed.

## The design — 10 areas

### A. Correctness + navigation (slice A)

**A1. Eight engine-derived category tabs** (partition, priority-ordered:
Guns→Ammo→Explosives→Melee→Kits→LBE→Armor→Misc), each with a live count, above
the search box. Selecting a tab filters the list; tab + search compose. The
server tags each item with its `category` (one of the 8) and returns per-category
counts, so the frontend never re-derives masks. Counts sum to the item total by
construction.

**A2. Fix the sister-file family mask** — add `THROWN` (0x40) to the Weapon
family in `items_schema.py`. Keep `resolve_family` (sister file) and the new
`resolve_category` (tab) as two separate engine-derived mappings; both have a
single source of truth in the schema module. A test asserts the 8 categories
partition a representative class-bit set and that the Weapon family includes
thrown/punch/blade.

**A3. `usItemClass` becomes read-only** — render the decoded bit-names (e.g.
"GUN", "ARMOUR | FACE") instead of an editable integer. Removes the cross-file
corruption footgun (changing class re-points sister-file writes with no row
migration). Class re-assignment, if ever needed, is a separate explicit action
(out of scope here). The PUT endpoint **rejects** an attempt to change
`usItemClass` (defense in depth, since the field is no longer sent).

**A4. Enum dropdowns** for coded integer fields — `ubCalibre`, `ubWeaponType`,
`ubAmmoType`, `ubMagType`, `ubArmourClass`, `ubType` (explosive) render as
`<select>` of `{value,label}` from engine-verified enum tables (new shared
modules mirroring the merc-demographics enum pattern). A value outside the known
set still renders (shown as "Unknown (N)") so unusual data never blocks editing.

### B. Editor UX (slice B)

**B1. Collapsible sections** — Identity / Economy / Class-stats / Graphic open;
**Advanced collapsed** (read-only decoded class, reliability/repair, flags). The
**Graphic section solely owns** `ubGraphicType`/`ubGraphicNum` (preview + picker);
the raw ints do NOT also appear in Advanced (no duplicate/desync). Sticky Save
header.

**B2. Verified tooltips + definitions** — each FieldSpec gains `help` (one-line
definition) and `unit` (e.g. "AP", "%", "tiles", "lbs"). Labels show the unit +
range inline; a focusable, accessible `?` icon reveals the `help`. **Every
`help`/`unit` string is verified against engine source or XML with a file:line
citation recorded in review** — no definition ships from memory. Uncertain units
state the storage truth (e.g. Range = "internal range units" unless proven
tiles). Consolidate onto the single `help` channel (retire the raw-key `note`
tooltip). Start with the high-traffic fields verified; remaining fields may ship
without a tooltip rather than with a guessed one.

**B3. Unsaved-edit guard + dirty state** — a confirm-on-dirty when changing the
selected item, switching category tab, or leaving the route; a dirty indicator
(asterisk/badge) in the header; Save disabled when the draft is clean.

**B4. Post-save correctness** — on save success, set the draft to the server's
post-clamp values (return the stored record, or apply `clamps` to the draft) so
the form is the source of truth immediately; mark clamped fields inline at their
input, not just a summary line.

**B5. List + keyboard QoL** — header shows "showing N of <total>"; sort by
name / price / coolness; ↑/↓ move the list selection; **Ctrl+S** saves;
**Revert** re-loads the on-disk values. When `/items` reports `writable: false`,
Save is disabled with a visible reason.

### C. Picker rework (slice C)

**C1. Uniform scan grid** — sprites render in a fixed, normalized cell
(`object-contain`, `image-rendering: pixelated` so small sprites stay crisp),
giving consistent click targets and a clean scan; **native size + 2–3× zoom live
only in a preview pane** for the focused/hovered sprite (with stem + (type,num)).
This is the "native size for picking" ask placed where it helps (inspection)
without wrecking the scan grid.

**C2. No fetch storm** — the picker must not fire ~1649 independent authenticated
image requests. Virtualize the grid (render only on-screen cells) and/or add a
catalog sprite-sheet endpoint mirroring the roster's N+1→1 solution
(`api.ts` roster sheet). The chosen approach is decided in slice C's plan; the
hard requirement is "opening the picker does not stall."

**C3. Picker context** — the item's current graphic is highlighted; the editor's
Graphic section shows an inline thumbnail of the current assignment before the
picker is opened; search retained.

### Cross-cutting

Accessibility: tabs are a `role="tablist"` with arrow-key nav; the `?` icon is
focusable with `aria-describedby` (hover-only `title=` is insufficient). Empty/
error states are explicit: empty category, no-search-results, item with no
graphic, sister-file-missing-on-load (warn that class stats are uneditable),
non-writable install.

## Deferred (out of scope for this spec — noted for later)

Comparison / "typical value" stat bars; calibre↔magazine and "referenced-by"
cross-references; attachment-compatibility display; bulk edit; favorites /
recently-viewed; duplicate-to-new-id (that is editor S3); undo-across-saves UI;
per-item deep-link URLs; modified-this-session list markers.

## Architecture / files

- **Backend:**
  - `items_schema.py` — add `help`/`unit` to FieldSpec + verified definitions;
    add `resolve_category` (8-tab partition) alongside `resolve_family`; fix the
    Weapon family mask (+THROWN); expose `help`/`unit` in `schema_payload`.
  - New `mercwizard_core/item_enums.py` (or per-enum modules) — engine/XML-verified
    enum tables for calibre/ammo-type/weapon-type/armour-class/explosive-type,
    each with a source citation; served via the item detail/schema payload.
  - `routes/items.py` — `/items` returns per-item `category` + category counts;
    `/items/{id}` includes the enum option lists for the item's editable coded
    fields; `PUT` rejects `usItemClass` changes.
  - Tests mirror the existing item tests (category partition, family mask,
    enum lookups, `usItemClass`-reject).
- **Frontend:**
  - `routes/Items.tsx` — category tabs, filtering, counts, sort, count display,
    keyboard nav, dirty-guard, Ctrl+S, Revert, writable-awareness.
  - New `components/items/CategoryTabs.tsx`, `CollapsibleSection.tsx`,
    `FieldHelp.tsx` (accessible tooltip), `EnumSelect.tsx`,
    `ClassBadge.tsx` (decoded read-only class).
  - Restructure `ItemCommonForm.tsx` / `ItemClassStatsForm.tsx` into the
    collapsible sections with help icons + enum selects.
  - `BigItemPicker.tsx` — uniform grid + preview pane + virtualization/sheet.

## Implementation slices

The plan is sliced; each is independently shippable.
- **Slice A — correctness + navigation:** category tabs + counts, family-mask
  fix, read-only class badge, enum dropdowns (+ the verified enum tables). The
  highest-value, correctness-bearing work.
- **Slice B — editor UX:** collapsible sections, verified tooltips/definitions,
  dirty-guard + indicator, post-save clamp sync, list sort/count/keyboard/Ctrl+S/
  Revert/writable.
- **Slice C — picker rework:** uniform scan grid + native preview pane +
  no-fetch-storm + current-graphic context.

## Testing

- Backend (pytest): category partition sums to total + each class bit maps to its
  expected tab; Weapon family includes thrown/punch/blade/launcher; enum tables
  resolve known ids to expected labels + cite sources; `PUT` with a changed
  `usItemClass` is rejected; `/items` payload includes `category` + counts.
- Frontend (tsc + browser-dev recipe): tabs filter + counts; dirty-guard blocks
  silent loss on item/tab switch; enum selects render labels; picker opens
  without a stall and the preview pane zooms; non-writable disables Save. The
  field definitions get a verification pass (file:line citations) before merge.

## Definition-verification gate (non-negotiable)

No `help`/`unit`/enum-label string merges without a source citation (engine
file:line or XML). This mirrors the prior tooltip audit. Wrong definitions are
worse than none: an unverifiable field ships with no tooltip rather than a guess.
