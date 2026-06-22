# Item Editor QoL (v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the shipped Items editor with correct 8-tab navigation, enum dropdowns + verified field definitions, a read-only item-class badge, an unsaved-edit guard, and a non-janky native-preview graphic picker.

**Architecture:** Backend adds an engine-derived category partition + verified enum tables + per-field help/unit to the existing schema/routes; frontend adds category tabs, enum selects, a class badge, collapsible sections, an accessible tooltip, a dirty-guard, and a reworked picker. Mirrors the shipped item-editor + Backgrounds patterns.

**Tech Stack:** Python 3 / FastAPI / lxml + regex (sidecar); React + TypeScript + Vite + @tanstack/react-query (frontend). Tests: pytest (backend), `tsc --noEmit` + Playwright browser-dev recipe (frontend, no unit runner).

**Spec:** `docs/superpowers/specs/2026-06-21-item-editor-qol-design.md`. Three slices — **A correctness+nav**, **B editor UX**, **C picker** — each independently shippable.

## Global Constraints

- **Verification gate (non-negotiable):** every `help`/`unit` string and every enum label ships ONLY with a source citation (engine `file:line` or XML path+field). An unverifiable field ships with NO tooltip rather than a guess. Wrong definitions are worse than none.
- **Engine-derived, single source of truth:** category masks, family masks, and enum tables live in the sidecar schema modules; the frontend never re-derives them — it consumes the served payload.
- **Tabs are a partition:** each item maps to exactly one of the 8 categories by priority order; counts sum to the item total.
- **Item-class bits** (`Tactical/Item Types.h:655-682`): `IC_NONE=0x1, IC_GUN=0x2, IC_BLADE=0x4, IC_THROWING_KNIFE=0x8, IC_LAUNCHER=0x10, IC_TENTACLES=0x20, IC_THROWN=0x40, IC_PUNCH=0x80, IC_GRENADE=0x100, IC_BOMB=0x200, IC_AMMO=0x400, IC_ARMOUR=0x800, IC_MEDKIT=0x1000, IC_KIT=0x2000, IC_APPLIABLE=0x4000, IC_FACE=0x8000, IC_KEY=0x10000, IC_LBEGEAR=0x20000, IC_BELTCLIP=0x40000, IC_MISC=0x10000000, IC_MONEY=0x20000000`.
- **8 nav categories** (`IC_MAPFILTER_*`, `Item Types.h:692-700`), priority order: Guns(`GUN|LAUNCHER`) → Ammo(`AMMO`) → Explosives(`GRENADE|BOMB`) → Melee(`BLADE|PUNCH|THROWN|THROWING_KNIFE`) → Kits(`KIT|MEDKIT|APPLIABLE`) → LBE(`LBEGEAR|BELTCLIP`) → Armor(`ARMOUR|FACE`) → Misc(catch-all).
- **Family mask fix:** Weapon family = `GUN|BLADE|THROWING_KNIFE|LAUNCHER|TENTACLES|THROWN|PUNCH` (add `THROWN`=0x40 to the shipped mask). Empirically all such items have a Weapons.xml row at their `ubClassIndex`.
- **Enum sources:** `ubCalibre`→AmmoStrings.xml `<AmmoCaliber>` (key `uiIndex`); `ubAmmoType`→AmmoTypes.xml `<name>` (key `uiIndex`); `ubWeaponType`/`ubArmourClass`/`ubType`(explosive)/`ubMagType`→engine `#define`/enum tables (locate via the source tree under `C:/AI Projects/The Wasteland/Source Files/1.13 Source/source-master/Tactical/` and/or the engine_graph DB at `C:/AI Projects/The Wasteland/Headless_Compiler/engine_graph/engine.db` `constants` table; cite file:line).
- **`usItemClass` is read-only** in the editor; the PUT endpoint rejects any change to it.
- **No fetch storm:** the picker must not fire ~1649 independent image requests on open (virtualize or sheet).
- **Byte-splice/encoding discipline + lock+snapshot on writes** carry over from the shipped editor (`reference_ja2_xml_encoding`; mirror `routes/items.py`).
- **pytest** via `C:/AI Projects/The Wasteland/MercWizard2/sidecar/.venv/Scripts/python.exe` run from `sidecar/`. **Frontend** verify = `cd frontend && npm run typecheck` (baseline clean) + browser-dev recipe (`reference_mercforge_browserdev_verify`).
- Branch off `main`; do NOT work on main directly.

---

## File Structure

**Backend create:** `sidecar/mercwizard_core/item_enums.py` (verified enum tables + loaders + citations). **Backend modify:** `items_schema.py` (family-mask fix, `resolve_category`+category list, `help`/`unit` on FieldSpec + verified definitions), `routes/items.py` (`category`+counts on `/items`; enum options + decoded class on `/items/{id}`; PUT rejects class change). **Backend tests:** `test_items_schema.py`, new `test_item_enums.py`, `test_items_route.py` (extend).

**Frontend create:** `components/items/CategoryTabs.tsx`, `CollapsibleSection.tsx`, `FieldHelp.tsx`, `EnumSelect.tsx`, `ClassBadge.tsx`. **Frontend modify:** `lib/api.ts` (types), `routes/Items.tsx` (tabs/filter/counts/sort/keyboard/dirty-guard/Ctrl+S/Revert/writable/clamp-sync), `components/forms/ItemCommonForm.tsx` + `ItemClassStatsForm.tsx` (collapsible sections + enum selects + help + read-only class), `components/BigItemPicker.tsx` (uniform grid + native preview + virtualization + current highlight).

---

# SLICE A — Correctness + Navigation

## Task A1: Category partition + family-mask fix (`items_schema.py`)

**Files:**
- Modify: `sidecar/mercwizard_core/items_schema.py`
- Test: `sidecar/tests/test_items_schema.py`

**Interfaces:**
- Produces: `Category` dataclass `(key, label, mask)`; `CATEGORIES: tuple[Category,...]` (8, priority order); `resolve_category(us_item_class: int) -> str` (returns a category `key`, default `"misc"`); updated `_IC_WEAPON` including `THROWN`.

- [ ] **Step 1: Write the failing test**

```python
# append to sidecar/tests/test_items_schema.py
def test_resolve_category_partition() -> None:
    assert s.resolve_category(0x2) == "guns"        # GUN
    assert s.resolve_category(0x10) == "guns"        # LAUNCHER groups with guns
    assert s.resolve_category(0x400) == "ammo"
    assert s.resolve_category(0x100) == "explosives" # GRENADE
    assert s.resolve_category(0x4) == "melee"        # BLADE
    assert s.resolve_category(0x80) == "melee"       # PUNCH
    assert s.resolve_category(0x40) == "melee"       # THROWN
    assert s.resolve_category(0x2000) == "kits"      # KIT
    assert s.resolve_category(0x1000) == "kits"      # MEDKIT
    assert s.resolve_category(0x20000) == "lbe"      # LBEGEAR
    assert s.resolve_category(0x800) == "armor"      # ARMOUR
    assert s.resolve_category(0x8000) == "armor"     # FACE
    assert s.resolve_category(0x10000) == "misc"     # KEY
    assert s.resolve_category(0x1) == "misc"         # NONE
    assert {c.key for c in s.CATEGORIES} == {
        "guns","ammo","explosives","melee","kits","lbe","armor","misc"}

def test_weapon_family_includes_thrown_punch() -> None:
    for cls in (0x40, 0x80, 0x4, 0x8, 0x10, 0x2):  # THROWN/PUNCH/BLADE/THROWKNIFE/LAUNCHER/GUN
        assert s.resolve_family(cls).record_tag == "WEAPON"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "C:/AI Projects/The Wasteland/MercWizard2/sidecar" && .venv/Scripts/python.exe -m pytest tests/test_items_schema.py -k "category or thrown" -v`
Expected: FAIL — `resolve_category` undefined; `resolve_family(0x40)` is None.

- [ ] **Step 3: Write minimal implementation**

In `items_schema.py`, fix the Weapon mask and add the category partition:

```python
# fix: add IC_THROWN (0x40)
_IC_WEAPON = 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x40 | 0x80  # +THROWN
# (CLASS_FAMILIES Weapon entry uses _IC_WEAPON — no other change needed)

@dataclass(frozen=True)
class Category:
    key: str
    label: str
    mask: int

# IC_MAPFILTER_* (Item Types.h:692-700), priority order. Misc is the catch-all.
CATEGORIES: tuple[Category, ...] = (
    Category("guns", "Guns", 0x2 | 0x10),                       # GUN|LAUNCHER
    Category("ammo", "Ammo", 0x400),                            # AMMO
    Category("explosives", "Explosives", 0x100 | 0x200),        # GRENADE|BOMB
    Category("melee", "Melee", 0x4 | 0x80 | 0x40 | 0x8),        # BLADE|PUNCH|THROWN|THROWING_KNIFE
    Category("kits", "Kits", 0x2000 | 0x1000 | 0x4000),         # KIT|MEDKIT|APPLIABLE
    Category("lbe", "LBE", 0x20000 | 0x40000),                  # LBEGEAR|BELTCLIP
    Category("armor", "Armor", 0x800 | 0x8000),                 # ARMOUR|FACE
    Category("misc", "Misc", 0x20 | 0x10000 | 0x10000000 | 0x20000000 | 0x1),  # TENTACLES|KEY|MISC|MONEY|NONE
)

def resolve_category(us_item_class: int) -> str:
    for cat in CATEGORIES:
        if us_item_class & cat.mask:
            return cat.key
    return "misc"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && .venv/Scripts/python.exe -m pytest tests/test_items_schema.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/items_schema.py sidecar/tests/test_items_schema.py
git commit -m "feat(items): engine 8-category partition + Weapon family THROWN fix"
```

## Task A2: Verified enum tables (`item_enums.py`)

**Files:**
- Create: `sidecar/mercwizard_core/item_enums.py`
- Test: `sidecar/tests/test_item_enums.py`

**Interfaces:**
- Consumes: `install_context.InstallContext.items_table_path` (existing).
- Produces:
  - `EnumOption = TypedDict-ish dict {"value": int, "label": str}` (plain dicts).
  - `calibre_options(ctx) -> list[dict]` (from AmmoStrings.xml `<uiIndex>`→`<AmmoCaliber>`).
  - `ammo_type_options(ctx) -> list[dict]` (from AmmoTypes.xml `<uiIndex>`→`<name>`).
  - `WEAPON_TYPE_OPTIONS`, `ARMOUR_CLASS_OPTIONS`, `EXPLOSIVE_TYPE_OPTIONS`, `MAG_TYPE_OPTIONS: list[dict]` — engine-`#define`-derived static tables, each with a module-level comment citing the engine `file:line` it was copied from.
  - `enum_options_for(field_key: str, ctx) -> Optional[list[dict]]` — maps a coded field key (`ubCalibre`/`ubAmmoType`/`ubWeaponType`/`ubArmourClass`/`ubType`/`ubMagType`) to its option list, or None.

**Engine-source research (do FIRST, before coding):** locate the enums for `ubWeaponType`, `ubArmourClass`, `ubType` (explosive), `ubMagType`. Query the engine_graph DB (`C:/AI Projects/The Wasteland/Headless_Compiler/engine_graph/engine.db`, `constants` table: `SELECT name,value FROM constants WHERE name LIKE 'ARMOURCLASS%'` etc.) and/or grep `C:/AI Projects/The Wasteland/Source Files/1.13 Source/source-master/Tactical/`. Record the exact `file:line` for each enum block as a comment above its table. If an enum cannot be located+verified, ship that field WITHOUT a dropdown (leave it a number input) and note it in the report — do NOT invent values.

- [ ] **Step 1: Write the failing test**

```python
# sidecar/tests/test_item_enums.py
from __future__ import annotations
from pathlib import Path
import pytest
from mercwizard_core import item_enums as en
from mercwizard_core.install_context import make_install_context

def _ctx(tmp_path: Path):
    items = tmp_path / "Data-1.13" / "TableData" / "Items"
    items.mkdir(parents=True)
    (tmp_path / "JA2.exe").touch()
    (items.parent / "MercProfiles.xml").write_text("<MERCPROFILES />")
    (items / "AmmoStrings.xml").write_bytes((
        "<AMMOLIST>\r\n"
        "\t<AMMO>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<AmmoCaliber>0</AmmoCaliber>\r\n\t</AMMO>\r\n"
        "\t<AMMO>\r\n\t\t<uiIndex>2</uiIndex>\r\n\t\t<AmmoCaliber>9x19mm</AmmoCaliber>\r\n\t</AMMO>\r\n"
        "</AMMOLIST>").encode("utf-8"))
    (items / "AmmoTypes.xml").write_bytes((
        "<AMMOTYPELIST>\r\n"
        "\t<AMMOTYPE>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<name>Ball</name>\r\n\t</AMMOTYPE>\r\n"
        "</AMMOTYPELIST>").encode("utf-8"))
    return make_install_context(tmp_path)

def test_calibre_options_from_ammostrings(tmp_path: Path):
    opts = en.calibre_options(_ctx(tmp_path))
    assert {"value": 2, "label": "9x19mm"} in opts

def test_ammo_type_options_from_ammotypes(tmp_path: Path):
    opts = en.ammo_type_options(_ctx(tmp_path))
    assert {"value": 0, "label": "Ball"} in opts

def test_static_enum_tables_nonempty_and_shaped():
    for table in (en.WEAPON_TYPE_OPTIONS, en.ARMOUR_CLASS_OPTIONS,
                  en.EXPLOSIVE_TYPE_OPTIONS, en.MAG_TYPE_OPTIONS):
        assert table and all({"value","label"} <= set(o) for o in table)

def test_enum_options_for_dispatch(tmp_path: Path):
    ctx = _ctx(tmp_path)
    assert en.enum_options_for("ubCalibre", ctx)
    assert en.enum_options_for("ubArmourClass", ctx) is en.ARMOUR_CLASS_OPTIONS
    assert en.enum_options_for("usPrice", ctx) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && .venv/Scripts/python.exe -m pytest tests/test_item_enums.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

Build `item_enums.py`: XML loaders parse `<uiIndex>`/`<AmmoCaliber>` and `<uiIndex>`/`<name>` via lxml/ET (read-only, tolerant of missing file → `[]`). The four static tables are copied from the located engine enums with a `# source: <file>:<line>` comment each. `enum_options_for` dispatches by field key. (The implementer writes the real values from the research step; the test only checks shape + the two XML-backed lookups, so it does not hard-code engine values.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && .venv/Scripts/python.exe -m pytest tests/test_item_enums.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/item_enums.py sidecar/tests/test_item_enums.py
git commit -m "feat(items): engine/XML-verified enum tables (calibre/ammo/weapon/armour/explosive)"
```

## Task A3: Route payload — category, counts, enums, decoded class, PUT guard

**Files:**
- Modify: `sidecar/routes/items.py`
- Test: `sidecar/tests/test_items_route.py`

**Interfaces:**
- Consumes: `items_schema.resolve_category`/`CATEGORIES` (A1), `item_enums.enum_options_for` (A2).
- Produces: `/items` response gains `categories: [{key,label,count}]` and each item summary gains `category: str`; `/items/{id}` response gains `enum_options: {field_key: [{value,label}]}` and `class_label: str` (decoded bit-names); `PUT /items/{id}` returns 400 `CLASS_IMMUTABLE` if `ints` contains `usItemClass` differing from the stored value.

- [ ] **Step 1: Write the failing test**

```python
# extend sidecar/tests/test_items_route.py
def test_items_payload_has_category_and_counts(client, active_items_install):
    body = client.get("/api/v1/items").json()
    assert any(c["key"] == "guns" and c["count"] >= 1 for c in body["categories"])
    glock = next(i for i in body["items"] if i["ui_index"] == 1)
    assert glock["category"] == "guns"

def test_item_detail_has_enums_and_class_label(client, active_items_install):
    body = client.get("/api/v1/items/1").json()
    assert "WEAPON" not in body["class_label"]  # decoded human bits, e.g. "GUN"
    assert "GUN" in body["class_label"]
    assert "ubCalibre" in body["enum_options"]   # gun has calibre enum

def test_put_rejects_class_change(client, active_items_install):
    r = client.put("/api/v1/items/1", json={
        "strings": {}, "ints": {"usItemClass": 2048}, "class_fields": {}})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "CLASS_IMMUTABLE"
```

> `active_items_install` is the existing fixture; ensure the tmp install also has `AmmoStrings.xml`/`AmmoTypes.xml` under `Data-1.13/TableData/Items/` so the gun's `ubCalibre` enum resolves (add those two files to the fixture, reusing the A2 test samples).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && .venv/Scripts/python.exe -m pytest tests/test_items_route.py -k "category or enum or class_change" -v`
Expected: FAIL — keys absent / PUT accepts class change.

- [ ] **Step 3: Write minimal implementation**

In `routes/items.py`: in `list_items`, set `category` per `ItemSummary` (call `resolve_category(item.item_class)`) and compute `categories` counts over the list. In `get_item`, build `class_label` from the class bits (a small `decode_class(us_item_class) -> str` helper in `items_schema`, e.g. `"GUN"`, `"ARMOUR | FACE"`, joined human names; add it + a test there or here) and `enum_options` by calling `item_enums.enum_options_for` for each coded field present in the common + class schema. In `update_item`, before the lock: if `usItemClass` in `body.ints` and differs from the stored value, raise `HTTPException(400, {"error":"CLASS_IMMUTABLE","message":"Item class can't be changed here."})`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sidecar && .venv/Scripts/python.exe -m pytest tests/test_items_route.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `cd sidecar && .venv/Scripts/python.exe -m pytest -q` → PASS.
```bash
git add sidecar/routes/items.py sidecar/mercwizard_core/items_schema.py sidecar/tests/test_items_route.py
git commit -m "feat(items): category+counts, per-field enums, decoded class label, PUT class-immutable guard"
```

## Task A4: Frontend nav + enums + class badge

**Files:**
- Modify: `frontend/src/lib/api.ts` (extend `ItemSummary` with `category`; `ItemsResponse` with `categories`; `ItemDetail` with `enum_options`, `class_label`).
- Create: `frontend/src/components/items/CategoryTabs.tsx`, `EnumSelect.tsx`, `ClassBadge.tsx`.
- Modify: `frontend/src/routes/Items.tsx` (category tab state + filter + counts), `components/forms/ItemCommonForm.tsx` + `ItemClassStatsForm.tsx` (render `EnumSelect` for coded fields; `ClassBadge` read-only for `usItemClass`).

**Interfaces:**
- Consumes: A3 payload fields.
- Produces: `CategoryTabs({categories, active, onSelect})`; `EnumSelect({label, value, options, onChange, help?})`; `ClassBadge({classLabel})`.

- [ ] **Step 1: Extend api.ts types** — add `category: string` to `ItemSummary`; `categories: {key:string;label:string;count:number}[]` to `ItemsResponse`; `enum_options: Record<string, {value:number;label:string}[]>` and `class_label: string` to `ItemDetail`. Run `npm run typecheck` (clean).

- [ ] **Step 2: Build `CategoryTabs.tsx`** — a `role="tablist"` of buttons `Label (count)`, arrow-key nav, `aria-selected`; calls `onSelect(key)`. Highlights `active`.

- [ ] **Step 3: Build `EnumSelect.tsx`** — a `<select>` of `options`; if `value` is not in `options`, prepend an `Unknown (value)` option so out-of-range data still renders; calls `onChange(number)`.

- [ ] **Step 4: Build `ClassBadge.tsx`** — renders `classLabel` as a read-only pill (no input).

- [ ] **Step 5: Wire into `Items.tsx`** — add `category` state (default `"all"`; include an "All" pseudo-tab summing counts), render `CategoryTabs` above the search box, filter `rows` by `category` (item.category === active || active === "all"), and show "showing N of <total>". Run `npm run typecheck`.

- [ ] **Step 6: Wire forms** — in `ItemCommonForm`/`ItemClassStatsForm`, when a field key is in `detail.enum_options`, render `EnumSelect` instead of the number input; render `usItemClass` via `ClassBadge` using `detail.class_label` (read-only, never an input). Pass `enum_options` down from `Items.tsx`. Run `npm run typecheck`.

- [ ] **Step 7: Browser verify (slice A)** — per the browser-dev recipe: tabs filter the list + counts are correct; selecting a gun shows a calibre dropdown with names; the class field is a read-only badge; a no-results category shows an empty state. Screenshot.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/items/ frontend/src/routes/Items.tsx frontend/src/components/forms/ItemCommonForm.tsx frontend/src/components/forms/ItemClassStatsForm.tsx
git commit -m "feat(items): category tabs, enum dropdowns, read-only class badge"
```

---

# SLICE B — Editor UX

## Task B1: Verified field definitions (`help`/`unit`)

**Files:**
- Modify: `sidecar/mercwizard_core/items_schema.py` (add `help`/`unit` to FieldSpec + populate verified set; expose in `schema_payload`/`class_schema_payload`)
- Test: `sidecar/tests/test_items_schema.py`

**Interfaces:**
- Produces: `FieldSpec.help: Optional[str]`, `FieldSpec.unit: Optional[str]`; `schema_payload` entries include `help`/`unit` when present.

**Verification step (do FIRST):** for each field you write a `help`/`unit` for, confirm the meaning against the engine or the XML comments (e.g. Weapons.xml has inline comments like `ubDeadliness <!-- relevant for merc affinity -->`, `bAccuracy <!-- used with OCTH -->`, `nAccuracy <!-- used with NCTH -->`). Record the citation in the report. Only fields you can cite get a `help`. Do NOT assert a unit you cannot verify (e.g. leave `usRange` unit blank or "internal units" unless you find the tile conversion in source).

- [ ] **Step 1: Write the failing test**

```python
def test_schema_payload_includes_help_for_verified_fields() -> None:
    payload = {e["key"]: e for e in s.common_schema_payload()}
    assert payload["ubCoolness"].get("help")   # a verified definition exists
    # every field that declares a unit also declares help (no orphan units)
    for e in s.common_schema_payload():
        if e.get("unit"):
            assert e.get("help")
```

- [ ] **Step 2: Run** `pytest tests/test_items_schema.py -k help -v` → FAIL.
- [ ] **Step 3: Implement** — add `help`/`unit` fields to `FieldSpec` (default None), populate the verified definitions (cited in the report), include them in `_payload`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(items): verified field definitions + units in schema`.

## Task B2: Collapsible sections + accessible tooltips + forms restructure

**Files:**
- Create: `frontend/src/components/items/CollapsibleSection.tsx`, `FieldHelp.tsx`
- Modify: `frontend/src/components/forms/ItemCommonForm.tsx`, `ItemClassStatsForm.tsx`

**Interfaces:**
- Consumes: B1 `help`/`unit` (served in schema payload).
- Produces: `CollapsibleSection({title, defaultOpen, children})`; `FieldHelp({help})` (focusable `?` with `aria-describedby`, shows `help` on hover/focus/click).

- [ ] **Step 1:** Build `CollapsibleSection` (disclosure with a header button, `aria-expanded`, remembers open/closed in component state).
- [ ] **Step 2:** Build `FieldHelp` — a focusable `<button>`-styled `?` icon; `help` shown via an accessible popover/tooltip (not bare `title=`); keyboard + screen-reader reachable.
- [ ] **Step 3:** Restructure `ItemCommonForm` into sections: **Identity** (name/long/desc/BRdesc — class shown as `ClassBadge`), **Economy** (price/coolness/weight/size/per-pocket), **Graphic** (owns the type/num + Change picker; shows the inline current thumbnail), **Advanced** (collapsed: reliability/repair + the read-only class badge + flags). Each field label shows `unit`/range and a `FieldHelp` when `help` present. The raw `ubGraphicType/Num` ints do NOT appear in Advanced. `ItemClassStatsForm` becomes a collapsible "Class stats" section with `FieldHelp` on each stat.
- [ ] **Step 4:** `npm run typecheck` clean.
- [ ] **Step 5: Browser verify** — sections collapse/expand; advanced hidden by default; `?` icons keyboard-reachable and show definitions; graphic not duplicated.
- [ ] **Step 6: Commit** `feat(items): collapsible sections + accessible field-help tooltips`.

## Task B3: Dirty-guard, clamp-sync, keyboard, list QoL

**Files:**
- Modify: `frontend/src/routes/Items.tsx`

**Interfaces:**
- Consumes: existing `updateItem` (returns `clamps`), `getItem`, `ConfirmModal` (existing `components/ConfirmModal.tsx`).
- Produces: dirty-state + guard behaviors.

- [ ] **Step 1:** Compute `isDirty` = `draft` differs from the last-loaded `detail.data`. Show a dirty indicator (asterisk/badge) in the header; disable Save when `!isDirty`.
- [ ] **Step 2:** Guard: when changing `selected` (clicking another row), changing the category tab, or leaving the route while `isDirty`, show a confirm ("Discard unsaved changes?") via `ConfirmModal`; proceed only on confirm; otherwise keep the current item.
- [ ] **Step 3:** Post-save: set `draft` to the server's clamped result so the form shows what was actually written; mark clamped fields inline (e.g. a small "clamped to N" note at each clamped input) using the returned `clamps`.
- [ ] **Step 4:** Keyboard + list: `Ctrl+S` saves (when dirty + writable); ↑/↓ move the list selection (respecting the dirty-guard); a **Revert** button re-loads on-disk values (re-set `draft` from `detail.data`); when `ItemsResponse.writable` is false, disable Save with a visible reason; header shows "showing N of <total>"; add a sort control (name / price / coolness) over the filtered rows.
- [ ] **Step 5:** `npm run typecheck` clean.
- [ ] **Step 6: Browser verify** — edit a field, click another item → confirm prompt blocks silent loss; save with an out-of-range value → form shows the clamped value + inline note; Ctrl+S saves; Revert restores; read-only install disables Save.
- [ ] **Step 7: Commit** `feat(items): unsaved-edit guard, clamp sync, keyboard + list QoL`.

---

# SLICE C — Picker rework

## Task C1: Uniform scan grid + native preview, no fetch storm

**Files:**
- Modify: `frontend/src/components/BigItemPicker.tsx`
- Modify: `frontend/src/components/forms/ItemCommonForm.tsx` (inline current-graphic thumbnail — if not already added in B2)

**Interfaces:**
- Consumes: existing `listBigItems`, `bigItemGraphicUrl`.
- Produces: reworked picker UX.

- [ ] **Step 1: Uniform grid** — render each sprite in a fixed-size cell (`object-contain`, `style={{imageRendering:"pixelated"}}`) so small sprites stay crisp and all click targets are equal. Highlight the cell matching the item's current `(type,num)`.
- [ ] **Step 2: Preview pane** — a side pane shows the focused/hovered sprite at native size scaled up 2–3× (pixelated), with its `stem` and `(type,num)`. This is where "native size" lives.
- [ ] **Step 3: No fetch storm** — do not mount ~1649 `<img>` at once. Virtualize the grid: render only the cells in/near the viewport (e.g. an `IntersectionObserver` or a windowed list), so only visible sprites fetch. Verify by opening the picker on the canonical install and confirming it is responsive (no multi-second stall) — note in the report how many requests fire on open.
- [ ] **Step 4: Search retained**; selecting a cell calls `onPick({type,num})` and closes.
- [ ] **Step 5:** `npm run typecheck` clean.
- [ ] **Step 6: Browser verify** — open picker on the real install: opens without stalling; grid cells are uniform + crisp; preview pane shows the native/zoomed focused sprite; current graphic highlighted; pick updates the form's inline thumbnail.
- [ ] **Step 7: Commit** `feat(items): picker uniform grid + native preview pane + virtualized loading`.

---

## Self-Review

**Spec coverage:**
- A1 8-tab partition + counts → Tasks A1, A3, A4 ✓; family-mask fix → A1 ✓.
- A3 read-only class → A3 (class_label, PUT guard) + A4 (ClassBadge) ✓.
- A4 enum dropdowns → A2 (tables) + A3 (served) + A4 (EnumSelect) ✓.
- B2 verified tooltips/definitions → B1 (help/unit + citations) + B2 (FieldHelp) ✓; verification gate enforced in B1/B2 report steps ✓.
- B1 collapsible + graphic-owns-type/num → B2 ✓.
- B3 dirty-guard + B4 clamp-sync + B5 list/keyboard → B3 ✓.
- C1 uniform grid + native preview, C2 no fetch storm, C3 current-graphic context → C1 ✓.
- Accessibility (tablist, focusable ?) → A4 (CategoryTabs) + B2 (FieldHelp) ✓.

**Placeholder scan:** the two research steps (A2 engine enums, B1 definitions) are deliberate, source-cited discovery tasks with explicit "do not invent / ship without if unverifiable" rules — not placeholders. Frontend tasks specify component interfaces + behaviors + the existing components to mirror, consistent with the no-unit-runner reality; each ends in a tsc gate + browser verify.

**Type consistency:** `category`/`categories`/`enum_options`/`class_label` names match across A3 (route) ↔ A4 (api.ts) ↔ forms. `resolve_category` returns a category `key` (string) used as `ItemSummary.category` and `CategoryTabs` selection. `EnumSelect`/`FieldHelp`/`ClassBadge`/`CollapsibleSection` prop names are defined once and consumed consistently.

**Known approximation:** exact engine enum values (A2) and field definitions (B1) are discovered by the implementer against cited sources under the verification gate; the plan names the sources and the gate rather than pre-listing every value (which would itself be unverified).
