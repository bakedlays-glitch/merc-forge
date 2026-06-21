# Item Editor (S1 + S2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-app Items editor to Merc Forge that browses every JA2 1.13 item with its inventory graphic, edits common fields + per-class stats, and re-points an item's graphic to any existing BIGITEMS art.

**Architecture:** Mirror the existing Backgrounds editor. A Python sidecar layer (engine-derived schema module + byte-splice XML writers + FastAPI routes wrapped in cross-process lock + snapshot) plus a React route (virtualized grid + edit panel + art picker). Items.xml holds identity + common fields; per-class stats live in sister XMLs (Weapons/Armours/Magazines/Explosives) keyed by the item's `ubClassIndex`.

**Tech Stack:** Python 3 / FastAPI / lxml + regex byte-splice (sidecar); React + TypeScript + Vite + @tanstack/react-query + react-router (frontend). Tests: pytest (backend), `tsc --noEmit` + Playwright browser-dev recipe (frontend).

**Spec:** `docs/superpowers/specs/2026-06-21-item-editor-design.md`. This plan implements **S1 (browse + edit common + re-point image)** and **S2 (per-class stats)** only. S3 (create/duplicate) and S4 (PNG import) are separate later plans.

## Global Constraints

- **Byte-splice, never reflow.** Edits touch only the target record's bytes; every other byte round-trips verbatim. No `ET.tostring`/`pretty_print` of the whole file. (Mirror `mercwizard_core/inject/backgrounds_xml.py`.)
- **Encoding round-trip:** read via `latin-1` (1:1 byte map), write via `latin-1` + `errors="xmlcharrefreplace"`, preserve BOM. Authored text escapes non-ASCII to numeric XML entities. Strip XML-1.0-illegal C0 controls (`< 0x20` except `\t \n \r`).
- **Lock + snapshot every write.** Wrap mutations in `cross_process_install_lock(install_id)` + `state.write_lock`, and `snapshot()` every file touched (both Items.xml and the sister file in a multi-file save) BEFORE mutating. Restorable from the Backups page.
- **Refuse `uiIndex 0`** (the "Nada/Nothing" template row) for edits.
- **Clamp** every numeric field to its engine range; reject illegal XML control chars; enforce string caps in **UTF-16 code units**.
- **String caps (CHAR16 arrays, cap = N-1):** `szItemName` 79, `szLongItemName` 79, `szBRName` 79, `szItemDesc` 399, `szBRDesc` 399. (`Tactical/Item Types.h:1096-1100`.)
- **Item-class bitfield** (`Tactical/Item Types.h:655-682`): `IC_GUN=0x2, IC_BLADE=0x4, IC_THROWING_KNIFE=0x8, IC_LAUNCHER=0x10, IC_TENTACLES=0x20, IC_PUNCH=0x80, IC_GRENADE=0x100, IC_BOMB=0x200, IC_AMMO=0x400, IC_ARMOUR=0x800, IC_FACE=0x8000`.
- **Sister-file linkage is by `ubClassIndex`, NOT global `uiIndex`.** A sister file's own `<uiIndex>` element IS the class index. (Verified: Items.xml Glock 17 `uiIndex=1, ubClassIndex=1` → Weapons.xml `<WEAPON><uiIndex>1</uiIndex>` = Glock 17.)
- **Active install** resolution via existing `_resolve_install` / `get_state().active()`, same as Backgrounds/roster. No new install-picker.
- **Reuse existing graphics stack:** `mercwizard_core/mapforge_engine/item_graphic.py` + `sti_decode.py`. Never write a new STI decoder.
- **Run pytest with the main checkout's venv** (the worktree has no `.venv`); `mercwizard_core` resolves via `sys.path`. Run from the sidecar dir.
- **Frontend verification** = `npm run typecheck` (no unit-test runner exists) + the browser-dev Playwright recipe (`reference_mercforge_browserdev_verify`).

---

## File Structure

**Create (backend):**
- `sidecar/mercwizard_core/inject/_xml_splice.py` — shared byte-splice text helpers (read/write/escape/find-record/set-child), generalized over a record tag. Used by both new writers; backgrounds is left untouched.
- `sidecar/mercwizard_core/items_schema.py` — engine-derived field specs: common Items.xml fields + per-class field specs + class→file map + clamp/utf16/`schema_payload`.
- `sidecar/mercwizard_core/inject/items_xml.py` — Items.xml read (index summary + full item) + `edit_item` byte-splice.
- `sidecar/mercwizard_core/inject/item_class_xml.py` — generic sister-file read_row/edit_row byte-splice, keyed by class index.
- `sidecar/routes/items.py` — `GET /items`, `GET /items/{id}`, `PUT /items/{id}`, `GET /bigitems-catalog`, `GET /bigitem-graphic`.
- `sidecar/tests/test_xml_splice.py`, `test_items_schema.py`, `test_items_xml.py`, `test_item_class_xml.py`, `test_items_route.py`.

**Modify (backend):**
- `sidecar/mercwizard_core/install_context.py` — add `items_table_path(filename, *, for_write)` resolving `TableData/Items/<filename>`, and `has_items` flavor detection.
- `sidecar/mercwizard_core/mapforge_engine/item_graphic.py` — factor `render_bigitem_by_ref(root, gtype, gnum)`; add `list_bigitem_graphics(root)`.
- `sidecar/main.py` — import + register `items.router`.

**Create (frontend):**
- `frontend/src/routes/Items.tsx` — virtualized grid + edit panel.
- `frontend/src/components/BigItemPicker.tsx` — browse/preview existing art, re-point.
- `frontend/src/components/forms/ItemCommonForm.tsx` — common fields.
- `frontend/src/components/forms/ItemClassStatsForm.tsx` — per-class stats (schema-driven, S2).

**Modify (frontend):**
- `frontend/src/lib/api.ts` — item types + client funcs.
- `frontend/src/App.tsx` — lazy route + `<Route path="/items">`.
- `frontend/src/routes/Hub.tsx` — add an "Items" tile to `secondary`.

---

## Task 1: Shared byte-splice helpers (`_xml_splice.py`)

**Files:**
- Create: `sidecar/mercwizard_core/inject/_xml_splice.py`
- Test: `sidecar/tests/test_xml_splice.py`

**Interfaces:**
- Consumes: `mercwizard_core/inject/_atomic_xml.py :: write_bytes_atomic(path, bytes)` (existing).
- Produces:
  - `read_text(path: Path) -> tuple[str, bool, str]` → `(text, had_bom, eol)`
  - `write_text(path: Path, text: str, had_bom: bool) -> None`
  - `esc(s: str) -> str` (escape `& < >` + non-ASCII→`&#NNNN;`, strip illegal C0)
  - `RecordBlock` dataclass: `.ui_index: Optional[int]`, `.start: int`, `.end: int`, `.text: str`
  - `find_blocks(text: str, record_tag: str) -> list[RecordBlock]`
  - `set_child(block: str, tag: str, inner: str, eol: str) -> str`
  - `block_int(block: str, tag: str) -> Optional[int]`, `block_text_child(block: str, tag: str) -> Optional[str]`

- [ ] **Step 1: Write the failing test**

```python
# sidecar/tests/test_xml_splice.py
from __future__ import annotations
from pathlib import Path
from mercwizard_core.inject import _xml_splice as sp

SAMPLE = (
    "﻿<ITEMLIST>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szItemName>Nada</szItemName>\r\n\t\t<usPrice>0</usPrice>\r\n\t</ITEM>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szItemName>Glock 17</szItemName>\r\n\t\t<usPrice>225</usPrice>\r\n\t</ITEM>\r\n"
    "</ITEMLIST>"
)

def test_read_text_strips_bom_keeps_eol(tmp_path: Path) -> None:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    text, had_bom, eol = sp.read_text(p)
    assert had_bom is True
    assert eol == "\r\n"
    assert not text.startswith("﻿")

def test_find_blocks_and_index(tmp_path: Path) -> None:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    text, _b, _e = sp.read_text(p)
    blocks = sp.find_blocks(text, "ITEM")
    assert [b.ui_index for b in blocks] == [0, 1]
    assert sp.block_int(blocks[1].text, "usPrice") == 225
    assert sp.block_text_child(blocks[1].text, "szItemName") == "Glock 17"

def test_set_child_replaces_in_place(tmp_path: Path) -> None:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    text, had_bom, eol = sp.read_text(p)
    blocks = sp.find_blocks(text, "ITEM")
    b1 = blocks[1]
    new_block = sp.set_child(b1.text, "usPrice", "999", eol)
    new_text = text[: b1.start] + new_block + text[b1.end :]
    sp.write_text(p, new_text, had_bom)
    # Block 0 untouched byte-for-byte; only block 1 price changed; BOM preserved.
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    out = raw[3:].decode("utf-8")
    assert "<usPrice>999</usPrice>" in out
    assert "<szItemName>Nada</szItemName>" in out
    assert out.count("<usPrice>0</usPrice>") == 1  # block 0's price intact

def test_esc_entitizes_non_ascii_and_strips_c0() -> None:
    assert sp.esc("a&b<c>") == "a&amp;b&lt;c&gt;"
    assert sp.esc("café") == "caf&#233;"
    assert sp.esc("x\x07y") == "xy"  # bell (C0) stripped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && "C:/AI Projects/The Wasteland/MercWizard2/sidecar/.venv/Scripts/python.exe" -m pytest tests/test_xml_splice.py -v`
(If the worktree has no `.venv`, use the main checkout's venv python — see Global Constraints.)
Expected: FAIL — `ModuleNotFoundError: No module named 'mercwizard_core.inject._xml_splice'`.

- [ ] **Step 3: Write minimal implementation**

```python
# sidecar/mercwizard_core/inject/_xml_splice.py
"""Shared XML byte-splice helpers — edit one record's bytes, never reflow.

Generalizes the proven private helpers from `inject/backgrounds_xml.py` over an
arbitrary record tag (ITEM / WEAPON / ARMOUR / …) so the Items editor's writers
share one implementation. Read via latin-1 (total 1:1 byte map); write via
latin-1 + xmlcharrefreplace so every untouched byte round-trips verbatim and any
authored high codepoint becomes a numeric XML entity (valid under the engine's
UTF-8-default expat). See `reference_ja2_xml_encoding`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._atomic_xml import write_bytes_atomic

_XML_ILLEGAL_C0 = frozenset(set(range(0x20)) - {0x09, 0x0A, 0x0D})


def read_text(path: Path) -> tuple[str, bool, str]:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("latin-1")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text, had_bom, eol


def write_text(path: Path, text: str, had_bom: bool) -> None:
    body = text.encode("latin-1", errors="xmlcharrefreplace")
    if had_bom:
        body = b"\xef\xbb\xbf" + body
    write_bytes_atomic(path, body)


def esc(s: str) -> str:
    s = "".join(c for c in s if ord(c) not in _XML_ILLEGAL_C0)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(c if ord(c) < 0x80 else f"&#{ord(c)};" for c in s)


@dataclass
class RecordBlock:
    ui_index: Optional[int]
    start: int
    end: int
    text: str


_UIINDEX_RE = re.compile(r"<uiIndex>\s*(-?\d+)")


def find_blocks(text: str, record_tag: str) -> list[RecordBlock]:
    pat = re.compile(rf"<{re.escape(record_tag)}>.*?</{re.escape(record_tag)}>", re.S)
    out: list[RecordBlock] = []
    for m in pat.finditer(text):
        block = m.group(0)
        idm = _UIINDEX_RE.search(block)
        ui = int(idm.group(1)) if idm else None
        out.append(RecordBlock(ui_index=ui, start=m.start(), end=m.end(), text=block))
    return out


def _tag_pat(tag: str) -> re.Pattern[str]:
    return re.compile(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", re.S)


def block_text_child(block: str, tag: str) -> Optional[str]:
    m = _tag_pat(tag).search(block)
    return m.group(1) if m else None


def block_int(block: str, tag: str) -> Optional[int]:
    raw = block_text_child(block, tag)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def set_child(block: str, tag: str, inner: str, eol: str) -> str:
    """Replace <tag>…</tag> in place; if absent, insert before the record close."""
    pat = re.compile(rf"<{re.escape(tag)}>.*?</{re.escape(tag)}>", re.S)
    if pat.search(block):
        return pat.sub(lambda _m: f"<{tag}>{inner}</{tag}>", block, count=1)
    m = re.search(r"(?:\r\n|\r|\n)[ \t]*</[A-Za-z_]+>\s*$", block)
    if m is None:
        idx = block.rfind("</")
        return block[:idx] + f"<{tag}>{inner}</{tag}>" + block[idx:]
    return block[: m.start()] + eol + "\t\t" + f"<{tag}>{inner}</{tag}>" + block[m.start() :]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && <venv-python> -m pytest tests/test_xml_splice.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/inject/_xml_splice.py sidecar/tests/test_xml_splice.py
git commit -m "feat(items): shared XML byte-splice helpers"
```

---

## Task 2: Item schema module (`items_schema.py`)

**Files:**
- Create: `sidecar/mercwizard_core/items_schema.py`
- Test: `sidecar/tests/test_items_schema.py`

**Interfaces:**
- Produces:
  - Constants: `TEMPLATE_INDEX=0`, string caps `NAME_MAX=79, LONG_NAME_MAX=79, BR_NAME_MAX=79, DESC_MAX=399, BR_DESC_MAX=399`.
  - `FieldSpec` dataclass: `key, label, group, kind` (`"str"|"int"`), `min, max` (ints; ignored for str), `cap` (int; for str), `advanced: bool`, `note: Optional[str]`.
  - `COMMON_FIELDS: tuple[FieldSpec, ...]`, `COMMON_STR_KEYS: frozenset[str]`, `COMMON_INT_KEYS: frozenset[str]`.
  - Per-class: `CLASS_FAMILIES: tuple[ClassFamily, ...]` where `ClassFamily(name, mask, filename, record_tag, fields: tuple[FieldSpec,...])`.
  - `resolve_family(us_item_class: int) -> Optional[ClassFamily]` (first family whose `mask & class != 0`).
  - `get_common_spec(key) -> Optional[FieldSpec]`, `clamp_int(spec, value) -> tuple[int,bool]`, `utf16_len(text) -> int`.
  - `common_schema_payload() -> list[dict]`, `class_schema_payload(family) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# sidecar/tests/test_items_schema.py
from __future__ import annotations
from mercwizard_core import items_schema as s

def test_common_fields_present_and_typed() -> None:
    keys = {f.key for f in s.COMMON_FIELDS}
    assert {"szItemName", "szItemDesc", "usPrice", "ubCoolness",
            "ubGraphicType", "ubGraphicNum"} <= keys
    assert s.get_common_spec("szItemName").kind == "str"
    assert s.get_common_spec("usPrice").kind == "int"

def test_string_caps() -> None:
    assert s.get_common_spec("szItemName").cap == 79
    assert s.get_common_spec("szItemDesc").cap == 399

def test_clamp_int() -> None:
    spec = s.get_common_spec("ubCoolness")  # 0..10
    assert s.clamp_int(spec, 99) == (10, True)
    assert s.clamp_int(spec, 5) == (5, False)

def test_resolve_family_by_class_bit() -> None:
    assert s.resolve_family(0x2).record_tag == "WEAPON"     # IC_GUN
    assert s.resolve_family(0x800).record_tag == "ARMOUR"   # IC_ARMOUR
    assert s.resolve_family(0x400).record_tag == "MAGAZINE" # IC_AMMO
    assert s.resolve_family(0x100).record_tag == "EXPLOSIVE"# IC_GRENADE
    assert s.resolve_family(0x10000) is None                # IC_KEY → no sister stats

def test_weapon_family_has_key_stats() -> None:
    fam = s.resolve_family(0x2)
    wkeys = {f.key for f in fam.fields}
    assert {"ubImpact", "usRange", "ubMagSize"} <= wkeys

def test_utf16_len_counts_code_units() -> None:
    assert s.utf16_len("abc") == 3
    assert s.utf16_len("\U0001F600") == 2  # emoji = 2 UTF-16 units
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && <venv-python> -m pytest tests/test_items_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mercwizard_core.items_schema'`.

- [ ] **Step 3: Write minimal implementation**

```python
# sidecar/mercwizard_core/items_schema.py
"""Items.xml + sister-file field schema — engine-derived single source of truth.

Identity = Items.xml `uiIndex` (0..1853). Per-class stats live in sister files
keyed by the item's `ubClassIndex` (the sister file's own `<uiIndex>` element IS
the class index). Class bits from `Tactical/Item Types.h:655-682`; string caps
from `Item Types.h:1096-1100` (CHAR16[N] → cap N-1).

S1 = COMMON_FIELDS (Items.xml). S2 = CLASS_FAMILIES (sister-file stats). The
per-class numeric ranges below are TYPE-DERIVED (ub*=0..255, b*=-128..127,
us*=0..65535, s*=-32768..32767); exact engine clamps are refined in the
pre-S2 research note but the validation path is identical regardless.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

TEMPLATE_INDEX = 0
NAME_MAX = 79
LONG_NAME_MAX = 79
BR_NAME_MAX = 79
DESC_MAX = 399
BR_DESC_MAX = 399

UB_MIN, UB_MAX = 0, 255
B_MIN, B_MAX = -128, 127
US_MIN, US_MAX = 0, 65535
S_MIN, S_MAX = -32768, 32767


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    group: str
    kind: str            # "str" | "int"
    min: int = 0
    max: int = 0
    cap: int = 0         # str cap (UTF-16 code units)
    advanced: bool = False
    note: Optional[str] = None


G_NAMES = "Names & description"
G_CORE = "Core"
G_GRAPHIC = "Graphic"

COMMON_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("szItemName", "Name", G_NAMES, "str", cap=NAME_MAX),
    FieldSpec("szLongItemName", "Long name", G_NAMES, "str", cap=LONG_NAME_MAX),
    FieldSpec("szItemDesc", "Description", G_NAMES, "str", cap=DESC_MAX),
    FieldSpec("szBRName", "BR name", G_NAMES, "str", cap=BR_NAME_MAX),
    FieldSpec("szBRDesc", "BR description", G_NAMES, "str", cap=BR_DESC_MAX),
    FieldSpec("usItemClass", "Item class (bitfield)", G_CORE, "int", US_MIN, US_MAX,
              advanced=True, note="Bitfield (IC_*). Changing this changes which "
              "sister-file stats apply; edit with care."),
    FieldSpec("usPrice", "Price", G_CORE, "int", US_MIN, US_MAX),
    FieldSpec("ubCoolness", "Coolness", G_CORE, "int", 0, 10),
    FieldSpec("ubWeight", "Weight", G_CORE, "int", UB_MIN, UB_MAX),
    FieldSpec("ItemSize", "Item size", G_CORE, "int", UB_MIN, UB_MAX),
    FieldSpec("ubPerPocket", "Per pocket", G_CORE, "int", UB_MIN, UB_MAX),
    FieldSpec("bReliability", "Reliability", G_CORE, "int", B_MIN, B_MAX),
    FieldSpec("bRepairEase", "Repair ease", G_CORE, "int", B_MIN, B_MAX),
    FieldSpec("ubGraphicType", "Graphic type", G_GRAPHIC, "int", UB_MIN, UB_MAX,
              advanced=True),
    FieldSpec("ubGraphicNum", "Graphic number", G_GRAPHIC, "int", UB_MIN, UB_MAX,
              advanced=True),
)

_COMMON_BY_KEY = {f.key: f for f in COMMON_FIELDS}
COMMON_STR_KEYS = frozenset(f.key for f in COMMON_FIELDS if f.kind == "str")
COMMON_INT_KEYS = frozenset(f.key for f in COMMON_FIELDS if f.kind == "int")


@dataclass(frozen=True)
class ClassFamily:
    name: str
    mask: int
    filename: str
    record_tag: str
    fields: tuple[FieldSpec, ...]


_WEAPON_FIELDS = (
    FieldSpec("ubWeaponType", "Weapon type", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubCalibre", "Calibre", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubReadyTime", "Ready time (AP)", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubShotsPer4Turns", "Shots / 4 turns", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubShotsPerBurst", "Shots per burst", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubBulletSpeed", "Bullet speed", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubImpact", "Impact (damage)", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("ubDeadliness", "Deadliness", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("bAccuracy", "Accuracy (OCTH)", "Weapon", "int", B_MIN, B_MAX),
    FieldSpec("ubMagSize", "Magazine size", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("usRange", "Range", "Weapon", "int", US_MIN, US_MAX),
    FieldSpec("APsToReload", "APs to reload", "Weapon", "int", UB_MIN, UB_MAX),
    FieldSpec("nAccuracy", "Accuracy (NCTH)", "Weapon", "int", S_MIN, S_MAX),
)
_ARMOUR_FIELDS = (
    FieldSpec("ubArmourClass", "Armour class", "Armour", "int", UB_MIN, UB_MAX),
    FieldSpec("ubProtection", "Protection", "Armour", "int", UB_MIN, UB_MAX),
    FieldSpec("ubCoverage", "Coverage", "Armour", "int", UB_MIN, UB_MAX),
    FieldSpec("ubDegradePercent", "Degrade %", "Armour", "int", UB_MIN, UB_MAX),
)
_MAGAZINE_FIELDS = (
    FieldSpec("ubCalibre", "Calibre", "Magazine", "int", UB_MIN, UB_MAX),
    FieldSpec("ubMagSize", "Magazine size", "Magazine", "int", UB_MIN, UB_MAX),
    FieldSpec("ubAmmoType", "Ammo type", "Magazine", "int", UB_MIN, UB_MAX),
    FieldSpec("ubMagType", "Mag type", "Magazine", "int", UB_MIN, UB_MAX),
)
_EXPLOSIVE_FIELDS = (
    FieldSpec("ubType", "Type", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubDamage", "Damage", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubStunDamage", "Stun damage", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubRadius", "Radius", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubVolume", "Volume", "Explosive", "int", UB_MIN, UB_MAX),
    FieldSpec("ubVolatility", "Volatility", "Explosive", "int", UB_MIN, UB_MAX),
)

# IC_* masks. Order matters: first match wins (weapon families share no bits
# with the others, so simple priority is safe).
_IC_WEAPON = 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x80  # GUN|BLADE|THROWKNIFE|LAUNCHER|TENTACLES|PUNCH
_IC_AMMO = 0x400
_IC_ARMOUR = 0x800 | 0x8000  # ARMOUR|FACE
_IC_EXPLOSV = 0x100 | 0x200  # GRENADE|BOMB

CLASS_FAMILIES: tuple[ClassFamily, ...] = (
    ClassFamily("Weapon", _IC_WEAPON, "Weapons.xml", "WEAPON", _WEAPON_FIELDS),
    ClassFamily("Ammo", _IC_AMMO, "Magazines.xml", "MAGAZINE", _MAGAZINE_FIELDS),
    ClassFamily("Armour", _IC_ARMOUR, "Armours.xml", "ARMOUR", _ARMOUR_FIELDS),
    ClassFamily("Explosive", _IC_EXPLOSV, "Explosives.xml", "EXPLOSIVE", _EXPLOSIVE_FIELDS),
)


def resolve_family(us_item_class: int) -> Optional[ClassFamily]:
    for fam in CLASS_FAMILIES:
        if us_item_class & fam.mask:
            return fam
    return None


def get_common_spec(key: str) -> Optional[FieldSpec]:
    return _COMMON_BY_KEY.get(key)


def clamp_int(spec: FieldSpec, value: int) -> tuple[int, bool]:
    if value < spec.min:
        return spec.min, True
    if value > spec.max:
        return spec.max, True
    return value, False


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _payload(specs) -> list[dict]:
    out = []
    for s in specs:
        e: dict = {"key": s.key, "label": s.label, "group": s.group, "kind": s.kind}
        if s.kind == "str":
            e["cap"] = s.cap
        else:
            e["min"], e["max"] = s.min, s.max
        if s.advanced:
            e["advanced"] = True
        if s.note:
            e["note"] = s.note
        out.append(e)
    return out


def common_schema_payload() -> list[dict]:
    return _payload(COMMON_FIELDS)


def class_schema_payload(family: ClassFamily) -> list[dict]:
    return _payload(family.fields)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && <venv-python> -m pytest tests/test_items_schema.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/items_schema.py sidecar/tests/test_items_schema.py
git commit -m "feat(items): engine-derived item + class field schema"
```

---

## Task 3: Items.xml reader/writer (`inject/items_xml.py`)

**Files:**
- Create: `sidecar/mercwizard_core/inject/items_xml.py`
- Test: `sidecar/tests/test_items_xml.py`

**Interfaces:**
- Consumes: `_xml_splice` (Task 1), `items_schema` (Task 2).
- Produces:
  - `ItemError(Exception)` with `.code`, `.message`.
  - `ItemSummary` dataclass: `ui_index, name, item_class, price, coolness, graphic_type, graphic_num, class_index`.
  - `read_index(path: Path) -> list[ItemSummary]` (physical order).
  - `read_item(path: Path, ui_index: int) -> dict` → `{"ui_index", "strings": {key:str}, "ints": {key:int}}` for COMMON keys (missing tags omitted).
  - `edit_item(path, *, ui_index, strings: dict[str,str], ints: dict[str,int]) -> dict` → byte-splice; replaces only the listed common children; returns `{"ui_index": ui_index}`. Refuses `TEMPLATE_INDEX`.

- [ ] **Step 1: Write the failing test**

```python
# sidecar/tests/test_items_xml.py
from __future__ import annotations
from pathlib import Path
import pytest
from mercwizard_core.inject import items_xml as ix

SAMPLE = (
    "<ITEMLIST>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szItemName>Nada</szItemName>\r\n"
    "\t\t<usItemClass>128</usItemClass>\r\n\t\t<ubClassIndex>0</ubClassIndex>\r\n"
    "\t\t<usPrice>0</usPrice>\r\n\t\t<ubCoolness>0</ubCoolness>\r\n"
    "\t\t<ubGraphicType>0</ubGraphicType>\r\n\t\t<ubGraphicNum>0</ubGraphicNum>\r\n\t</ITEM>\r\n"
    "\t<ITEM>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szItemName>Glock 17</szItemName>\r\n"
    "\t\t<szItemDesc>A pistol.</szItemDesc>\r\n\t\t<usItemClass>2</usItemClass>\r\n"
    "\t\t<ubClassIndex>1</ubClassIndex>\r\n\t\t<usPrice>225</usPrice>\r\n\t\t<ubCoolness>3</ubCoolness>\r\n"
    "\t\t<ubGraphicType>0</ubGraphicType>\r\n\t\t<ubGraphicNum>5</ubGraphicNum>\r\n\t</ITEM>\r\n"
    "</ITEMLIST>"
)

@pytest.fixture
def items_file(tmp_path: Path) -> Path:
    p = tmp_path / "Items.xml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    return p

def test_read_index(items_file: Path) -> None:
    rows = ix.read_index(items_file)
    assert [r.ui_index for r in rows] == [0, 1]
    g = rows[1]
    assert g.name == "Glock 17" and g.item_class == 2 and g.class_index == 1
    assert g.price == 225 and g.graphic_type == 0 and g.graphic_num == 5

def test_read_item(items_file: Path) -> None:
    d = ix.read_item(items_file, 1)
    assert d["strings"]["szItemName"] == "Glock 17"
    assert d["ints"]["usPrice"] == 225
    assert d["ints"]["ubGraphicNum"] == 5

def test_edit_item_changes_only_target(items_file: Path) -> None:
    ix.edit_item(items_file, ui_index=1, strings={"szItemName": "Glock 18"},
                 ints={"usPrice": 300, "ubGraphicNum": 9})
    out = items_file.read_bytes().decode("utf-8")
    assert "<szItemName>Glock 18</szItemName>" in out
    assert "<usPrice>300</usPrice>" in out
    assert "<ubGraphicNum>9</ubGraphicNum>" in out
    # Item 0 (Nada) untouched.
    assert "<szItemName>Nada</szItemName>" in out
    # Item 1's class index never rewritten by a common-field edit.
    assert out.count("<ubClassIndex>1</ubClassIndex>") == 1

def test_edit_item_escapes_and_refuses_template(items_file: Path) -> None:
    ix.edit_item(items_file, ui_index=1, strings={"szItemName": "A & B"}, ints={})
    out = items_file.read_bytes().decode("utf-8")
    assert "<szItemName>A &amp; B</szItemName>" in out
    with pytest.raises(ix.ItemError):
        ix.edit_item(items_file, ui_index=0, strings={"szItemName": "x"}, ints={})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && <venv-python> -m pytest tests/test_items_xml.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mercwizard_core.inject.items_xml'`.

- [ ] **Step 3: Write minimal implementation**

```python
# sidecar/mercwizard_core/inject/items_xml.py
"""Items.xml surgical reader/writer — byte-splice, never reflow.

Items.xml is a 1854-record master table (~150 children each). The editor only
ever touches a curated set of common children (name/desc/price/graphic/…); every
other byte — including the ~135 untouched columns — round-trips verbatim. See
`reference_ja2_xml_encoding` and the Backgrounds writer this mirrors.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import items_schema as schema
from . import _xml_splice as sp


class ItemError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ItemSummary:
    ui_index: int
    name: str
    item_class: int
    price: int
    coolness: int
    graphic_type: int
    graphic_num: int
    class_index: int


def _require_single(blocks: list[sp.RecordBlock], ui_index: int) -> sp.RecordBlock:
    matches = [b for b in blocks if b.ui_index == ui_index]
    if not matches:
        raise ItemError("ITEM_NOT_FOUND", f"No item with uiIndex {ui_index}.")
    if len(matches) > 1:
        raise ItemError("DUPLICATE_INDEX",
                        f"Items.xml has {len(matches)} entries with uiIndex {ui_index}.")
    return matches[0]


def read_index(path: Path) -> list[ItemSummary]:
    text, _bom, _eol = sp.read_text(path)
    out: list[ItemSummary] = []
    for b in sp.find_blocks(text, "ITEM"):
        if b.ui_index is None:
            continue
        out.append(ItemSummary(
            ui_index=b.ui_index,
            name=(sp.block_text_child(b.text, "szItemName") or "").strip(),
            item_class=sp.block_int(b.text, "usItemClass") or 0,
            price=sp.block_int(b.text, "usPrice") or 0,
            coolness=sp.block_int(b.text, "ubCoolness") or 0,
            graphic_type=sp.block_int(b.text, "ubGraphicType") or 0,
            graphic_num=sp.block_int(b.text, "ubGraphicNum") or 0,
            class_index=sp.block_int(b.text, "ubClassIndex") or 0,
        ))
    return out


def read_item(path: Path, ui_index: int) -> dict:
    text, _bom, _eol = sp.read_text(path)
    target = _require_single(sp.find_blocks(text, "ITEM"), ui_index)
    strings: dict[str, str] = {}
    ints: dict[str, int] = {}
    for key in schema.COMMON_STR_KEYS:
        raw = sp.block_text_child(target.text, key)
        if raw is not None:
            # unescape the few entities the writer emits
            strings[key] = (raw.replace("&amp;", "&").replace("&lt;", "<")
                            .replace("&gt;", ">"))
    for key in schema.COMMON_INT_KEYS:
        v = sp.block_int(target.text, key)
        if v is not None:
            ints[key] = v
    # Always surface the class index so the route can resolve sister stats.
    ci = sp.block_int(target.text, "ubClassIndex")
    return {"ui_index": ui_index, "strings": strings, "ints": ints,
            "class_index": ci if ci is not None else 0}


def edit_item(path: Path, *, ui_index: int, strings: dict[str, str],
              ints: dict[str, int]) -> dict:
    if ui_index == schema.TEMPLATE_INDEX:
        raise ItemError("TEMPLATE_PROTECTED",
                        "uiIndex 0 is the template row and can't be edited.")
    text, had_bom, eol = sp.read_text(path)
    blocks = sp.find_blocks(text, "ITEM")
    target = _require_single(blocks, ui_index)

    block = target.text
    for key, value in strings.items():
        if key not in schema.COMMON_STR_KEYS:
            raise ItemError("UNKNOWN_FIELD", f"Unknown string field '{key}'.")
        block = sp.set_child(block, key, sp.esc(value), eol)
    for key, value in ints.items():
        if key not in schema.COMMON_INT_KEYS:
            raise ItemError("UNKNOWN_FIELD", f"Unknown int field '{key}'.")
        block = sp.set_child(block, key, str(value), eol)

    new_text = text[: target.start] + block + text[target.end :]
    sp.write_text(path, new_text, had_bom)
    return {"ui_index": ui_index}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && <venv-python> -m pytest tests/test_items_xml.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/inject/items_xml.py sidecar/tests/test_items_xml.py
git commit -m "feat(items): Items.xml byte-splice reader/writer"
```

---

## Task 4: Sister-file reader/writer (`inject/item_class_xml.py`)

**Files:**
- Create: `sidecar/mercwizard_core/inject/item_class_xml.py`
- Test: `sidecar/tests/test_item_class_xml.py`

**Interfaces:**
- Consumes: `_xml_splice` (Task 1).
- Produces:
  - `ClassRowError(Exception)` with `.code`, `.message`.
  - `read_row(path: Path, record_tag: str, class_index: int) -> Optional[dict[str,int]]` — all int children of the record whose `<uiIndex>` == class_index; `None` if absent.
  - `edit_row(path, *, record_tag: str, class_index: int, fields: dict[str,int]) -> dict` — byte-splice, replace listed children in place (all sister fields already exist in every record). Returns `{"class_index": class_index, "record_tag": record_tag}`. Raises `ClassRowError("ROW_NOT_FOUND")` if the class index isn't present.

- [ ] **Step 1: Write the failing test**

```python
# sidecar/tests/test_item_class_xml.py
from __future__ import annotations
from pathlib import Path
import pytest
from mercwizard_core.inject import item_class_xml as cx

WEAPONS = (
    "﻿<WEAPONLIST>\r\n"
    "\t<WEAPON>\r\n\t\t<uiIndex>0</uiIndex>\r\n\t\t<szWeaponName>Nothing</szWeaponName>\r\n"
    "\t\t<ubImpact>0</ubImpact>\r\n\t\t<usRange>0</usRange>\r\n\t</WEAPON>\r\n"
    "\t<WEAPON>\r\n\t\t<uiIndex>1</uiIndex>\r\n\t\t<szWeaponName>Glock 17</szWeaponName>\r\n"
    "\t\t<ubImpact>25</ubImpact>\r\n\t\t<usRange>115</usRange>\r\n\t</WEAPON>\r\n"
    "</WEAPONLIST>"
)

@pytest.fixture
def weapons(tmp_path: Path) -> Path:
    p = tmp_path / "Weapons.xml"
    p.write_bytes(WEAPONS.encode("utf-8"))
    return p

def test_read_row(weapons: Path) -> None:
    row = cx.read_row(weapons, "WEAPON", 1)
    assert row["ubImpact"] == 25 and row["usRange"] == 115
    assert cx.read_row(weapons, "WEAPON", 99) is None

def test_edit_row_in_place(weapons: Path) -> None:
    cx.edit_row(weapons, record_tag="WEAPON", class_index=1,
                fields={"ubImpact": 30, "usRange": 120})
    out = weapons.read_bytes()
    assert out.startswith(b"\xef\xbb\xbf")  # BOM preserved
    body = out[3:].decode("utf-8")
    assert "<ubImpact>30</ubImpact>" in body and "<usRange>120</usRange>" in body
    # Row 0 untouched.
    assert "<ubImpact>0</ubImpact>" in body
    assert body.count("<szWeaponName>Glock 17</szWeaponName>") == 1

def test_edit_row_missing_raises(weapons: Path) -> None:
    with pytest.raises(cx.ClassRowError):
        cx.edit_row(weapons, record_tag="WEAPON", class_index=99, fields={"ubImpact": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && <venv-python> -m pytest tests/test_item_class_xml.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mercwizard_core.inject.item_class_xml'`.

- [ ] **Step 3: Write minimal implementation**

```python
# sidecar/mercwizard_core/inject/item_class_xml.py
"""Generic sister-file (Weapons/Armours/Magazines/Explosives) byte-splice editor.

Each record is a flat list of int children keyed by `<uiIndex>` == the item's
`ubClassIndex`. Every editable field already exists in every record (full
templates), so an edit only ever REPLACES a child value in place — no insert /
remove. Mirrors the Items.xml writer's discipline; one generic module instead of
four near-identical ones.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import _xml_splice as sp


class ClassRowError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _find_row(text: str, record_tag: str, class_index: int) -> Optional[sp.RecordBlock]:
    for b in sp.find_blocks(text, record_tag):
        if b.ui_index == class_index:
            return b
    return None


def read_row(path: Path, record_tag: str, class_index: int) -> Optional[dict[str, int]]:
    if not path or not path.exists():
        return None
    text, _bom, _eol = sp.read_text(path)
    block = _find_row(text, record_tag, class_index)
    if block is None:
        return None
    out: dict[str, int] = {}
    for m in re.finditer(r"<([A-Za-z_][\w]*)>\s*(-?\d+)\s*</\1>", block.text):
        out[m.group(1)] = int(m.group(2))
    return out


def edit_row(path: Path, *, record_tag: str, class_index: int,
             fields: dict[str, int]) -> dict:
    text, had_bom, eol = sp.read_text(path)
    block = _find_row(text, record_tag, class_index)
    if block is None:
        raise ClassRowError("ROW_NOT_FOUND",
                            f"No {record_tag} row with class index {class_index}.")
    new_block = block.text
    for key, value in fields.items():
        new_block = sp.set_child(new_block, key, str(value), eol)
    new_text = text[: block.start] + new_block + text[block.end :]
    sp.write_text(path, new_text, had_bom)
    return {"class_index": class_index, "record_tag": record_tag}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sidecar && <venv-python> -m pytest tests/test_item_class_xml.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/inject/item_class_xml.py sidecar/tests/test_item_class_xml.py
git commit -m "feat(items): generic sister-file class-row byte-splice editor"
```

---

## Task 5: Install path resolution + BIGITEMS catalog/render helpers

**Files:**
- Modify: `sidecar/mercwizard_core/install_context.py` (add `items_table_path` + `has_items`)
- Modify: `sidecar/mercwizard_core/mapforge_engine/item_graphic.py` (factor render-by-ref + list catalog)
- Test: `sidecar/tests/test_item_graphic.py` (extend — file exists)

**Interfaces:**
- Consumes: existing `VfsLayout.resolve_read/resolve_write`, `item_graphic` internals (`_bigitems_stem`, `_resolve_bigitem_bytes`, `decode_sti_frame_to_png`).
- Produces:
  - `InstallContext.items_table_path(filename: str, *, for_write: bool = False) -> Path` — resolves `TableData/Items/<filename>` through the VFS (read layer for reads, write layer for writes).
  - `item_graphic.render_bigitem_by_ref(install_root: str, gtype: int, gnum: int) -> Optional[bytes]` — PNG for an arbitrary (type, num), independent of any item.
  - `item_graphic.list_bigitem_graphics(install_root: str) -> list[dict]` — `[{"type": int, "num": int, "stem": str}]` for every BIGITEMS STI found (loose dir + SLF), sorted by (type, num).

- [ ] **Step 1: Write the failing test**

```python
# append to sidecar/tests/test_item_graphic.py
from mercwizard_core.mapforge_engine import item_graphic as igph

def test_render_by_ref_matches_item_render(tmp_install_with_bigitem):
    # tmp_install_with_bigitem: existing fixture/helper that yields (root, us_item,
    # gtype, gnum) for a known item whose STI exists. If no such fixture exists,
    # build one from the active install path used by the other tests in this file.
    root, us_item, gtype, gnum = tmp_install_with_bigitem
    by_item = igph.render_item_graphic(root, us_item)
    by_ref = igph.render_bigitem_by_ref(root, gtype, gnum)
    assert by_item is not None and by_ref is not None
    assert by_item == by_ref

def test_list_bigitem_graphics_nonempty(tmp_install_with_bigitem):
    root, *_ = tmp_install_with_bigitem
    cat = igph.list_bigitem_graphics(root)
    assert isinstance(cat, list) and len(cat) > 0
    assert all({"type", "num", "stem"} <= set(e) for e in cat)
```

> If `test_item_graphic.py` has no reusable install fixture, mirror the file's existing setup (it already resolves a real BIGITEMS STI). Keep the new tests in the same style; the goal is "render-by-ref == render-by-item for the same graphic" and "catalog is non-empty".

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && <venv-python> -m pytest tests/test_item_graphic.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'render_bigitem_by_ref'`.

- [ ] **Step 3: Write minimal implementation**

In `item_graphic.py`, refactor `render_item_graphic` to delegate, and add the catalog lister:

```python
def render_bigitem_by_ref(install_root: str, gtype: int, gnum: int) -> Optional[bytes]:
    """PNG of the BIGITEMS graphic at (gtype, gnum), or None if its STI is missing."""
    stem = _bigitems_stem(gtype, gnum)
    data = _resolve_bigitem_bytes(install_root, stem)
    if data is None:
        return None
    try:
        from mercwizard_core.sti_decode import decode_sti_frame_to_png
        return decode_sti_frame_to_png(data, 0)
    except Exception:
        return None


def render_item_graphic(install_root: str, us_item: int) -> Optional[bytes]:
    """PNG of `us_item`'s BIGITEMS graphic, or None (unknown item / missing STI)."""
    gfx = _load_item_graphics(install_root).get(us_item)
    if gfx is None:
        return None
    return render_bigitem_by_ref(install_root, gfx[0], gfx[1])


_STEM_RE = re.compile(r"^(?:gun(\d+)|p(\d+)item(\d+))\.sti$", re.I)


def list_bigitem_graphics(install_root: str) -> list[dict]:
    """Enumerate every BIGITEMS graphic (loose dir + SLF) as {type, num, stem}."""
    import re  # noqa: F401  (module-level re already imported; keep local safe)
    found: dict[tuple[int, int], str] = {}
    root = Path(install_root)
    for base in (root / "Data-1.13" / "BigItems", root / "Data" / "BigItems"):
        if not base.is_dir():
            continue
        try:
            for child in base.iterdir():
                m = _STEM_RE.match(child.name)
                if not m or not child.is_file():
                    continue
                if m.group(1) is not None:
                    key = (0, int(m.group(1)))
                else:
                    key = (int(m.group(2)), int(m.group(3)))
                found.setdefault(key, child.stem)
        except OSError:
            pass
    slf_path = root / "Data" / "Bigitems.slf"
    if slf_path.is_file():
        try:
            from mercwizard_core.install_context import _open_slf_cached
            slf = _open_slf_cached(slf_path)
            if slf is not None:
                for entry in slf.listdir("/BIGITEMS"):
                    m = _STEM_RE.match(entry)
                    if not m:
                        continue
                    if m.group(1) is not None:
                        key = (0, int(m.group(1)))
                    else:
                        key = (int(m.group(2)), int(m.group(3)))
                    found.setdefault(key, entry.rsplit(".", 1)[0])
        except Exception:
            pass
    return [{"type": t, "num": n, "stem": found[(t, n)]}
            for (t, n) in sorted(found)]
```

> Ensure `import re` exists at the top of `item_graphic.py` (add if missing) and remove the redundant local `import re` if it triggers a lint error. The `slf.listdir` member name must match the `ja2py.SlfFS` API used elsewhere in `install_context.py`; if it differs, mirror that call.

In `install_context.py`, add to `InstallContext`:

```python
    def items_table_path(self, filename: str, *, for_write: bool = False) -> Path:
        """Resolve TableData/Items/<filename> (Items.xml + sister files)."""
        rel = f"TableData/Items/{filename}"
        return self.layout.resolve_write(rel) if for_write else (
            self.layout.resolve_read(rel) or self.layout.resolve_write(rel)
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sidecar && <venv-python> -m pytest tests/test_item_graphic.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add sidecar/mercwizard_core/install_context.py sidecar/mercwizard_core/mapforge_engine/item_graphic.py sidecar/tests/test_item_graphic.py
git commit -m "feat(items): items table path resolver + bigitem catalog/render-by-ref"
```

---

## Task 6: Items routes (`routes/items.py`) + registration

**Files:**
- Create: `sidecar/routes/items.py`
- Modify: `sidecar/main.py` (import + register `items.router`)
- Test: `sidecar/tests/test_items_route.py`

**Interfaces:**
- Consumes: `items_schema`, `inject/items_xml`, `inject/item_class_xml`, `mapforge_engine.item_graphic`, `install_context.make_install_context`, `routes.roster._resolve_install`, `routes.state.get_state`, `mercwizard_core.backup.snapshot`, `mercwizard_core.cross_lock.cross_process_install_lock`.
- Produces these endpoints (all under `/api/v1`):
  - `GET /items?install_id=` → `{items: [ItemSummary…], common_schema: [...], install_id, file_present, writable}`.
  - `GET /items/{ui_index}?install_id=` → `{ui_index, strings, ints, class_index, family, class_fields, class_schema}` (family/class_* null when classless or sister file absent).
  - `PUT /items/{ui_index}` body `{strings, ints, class_fields}` → validates+clamps, multi-file locked snapshot write → `{ok, backup_id, clamps}`.
  - `GET /bigitems-catalog?install_id=` → `{graphics: [{type,num,stem}]}`.
  - `GET /bigitem-graphic?type=&num=` → PNG (active install), 404 if missing.

- [ ] **Step 1: Write the failing test**

```python
# sidecar/tests/test_items_route.py
from __future__ import annotations
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Reuse the app + an active-install fixture the same way test_backgrounds_xml.py /
# the other route tests do. If those tests use a shared conftest fixture
# (e.g. `client` + `active_install` writing a tmp install), reuse it here.
from main import app

@pytest.fixture
def client():
    return TestClient(app)

def test_list_items_returns_schema_and_rows(client, active_items_install):
    r = client.get("/api/v1/items")
    assert r.status_code == 200
    body = r.json()
    assert any(it["name"] == "Glock 17" for it in body["items"])
    assert any(f["key"] == "usPrice" for f in body["common_schema"])

def test_get_item_resolves_weapon_family(client, active_items_install):
    # Glock 17 = uiIndex 1, class IC_GUN, classindex 1 → Weapons row present.
    r = client.get("/api/v1/items/1")
    assert r.status_code == 200
    body = r.json()
    assert body["family"] == "Weapon"
    assert body["class_fields"]["ubImpact"] == 25
    assert any(f["key"] == "usRange" for f in body["class_schema"])

def test_put_item_clamps_and_writes_both_files(client, active_items_install):
    r = client.put("/api/v1/items/1", json={
        "strings": {"szItemName": "Glock 18"},
        "ints": {"ubCoolness": 999},          # clamps to 10
        "class_fields": {"ubImpact": 30},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["backup_id"]
    assert any(c["key"] == "ubCoolness" and c["stored"] == 10 for c in body["clamps"])
    # Re-read reflects both files.
    g = client.get("/api/v1/items/1").json()
    assert g["strings"]["szItemName"] == "Glock 18"
    assert g["class_fields"]["ubImpact"] == 30

def test_put_item_refuses_template(client, active_items_install):
    r = client.put("/api/v1/items/0", json={"strings": {"szItemName": "x"},
                                            "ints": {}, "class_fields": {}})
    assert r.status_code == 400
```

> `active_items_install` fixture: write a tmp install dir with
> `Data-1.13/TableData/Items/Items.xml` (the SAMPLE from Task 3 test) and
> `Data-1.13/TableData/Items/Weapons.xml` (the WEAPONS sample from Task 4), mark
> it active in `get_state()`, mirroring how the existing route tests register an
> active install (see `tests/conftest.py`). If conftest already exposes an
> install-builder helper, extend it with the two item files.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sidecar && <venv-python> -m pytest tests/test_items_route.py -v`
Expected: FAIL — route not registered (404) / import error.

- [ ] **Step 3: Write minimal implementation**

```python
# sidecar/routes/items.py
"""Items editor routes — browse Items.xml, edit common + per-class stats,
re-point BIGITEMS graphics. Mirrors routes/backgrounds.py: every write takes the
cross-process install lock + snapshots each touched file to the Backups page,
validates + clamps to engine ranges, and refuses the uiIndex-0 template row.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from mercwizard_core import items_schema as schema
from mercwizard_core.backup import snapshot
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.inject import item_class_xml as cx
from mercwizard_core.inject import items_xml as ix
from mercwizard_core.install_context import make_install_context
from mercwizard_core.mapforge_engine import item_graphic as igph

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


class ItemUpdateBody(BaseModel):
    strings: dict[str, str] = Field(default_factory=dict)
    ints: dict[str, int] = Field(default_factory=dict)
    class_fields: dict[str, int] = Field(default_factory=dict)


def _items_path(info, filename: str, *, for_write: bool = False):
    ctx = make_install_context(info.path)
    return ctx.items_table_path(filename, for_write=for_write)


def _validate_common(strings: dict[str, str], ints: dict[str, int]):
    errors: list[str] = []
    clean_str: dict[str, str] = {}
    clean_int: dict[str, int] = {}
    clamps: list[dict] = []
    for key, val in strings.items():
        spec = schema.get_common_spec(key)
        if spec is None or spec.kind != "str":
            errors.append(f"Unknown string field '{key}'.")
            continue
        if schema.utf16_len(val) > spec.cap:
            errors.append(f"{spec.label} exceeds {spec.cap} characters.")
        clean_str[key] = val
    for key, val in ints.items():
        spec = schema.get_common_spec(key)
        if spec is None or spec.kind != "int":
            errors.append(f"Unknown numeric field '{key}'.")
            continue
        v, changed = schema.clamp_int(spec, val)
        if changed:
            clamps.append({"key": key, "requested": val, "stored": v})
        clean_int[key] = v
    if errors:
        raise HTTPException(status_code=400, detail={
            "error": "ITEM_INVALID", "message": " ".join(errors), "issues": errors})
    return clean_str, clean_int, clamps


def _validate_class(family: Optional[schema.ClassFamily], fields: dict[str, int]):
    if not fields:
        return {}, []
    if family is None:
        raise HTTPException(status_code=400, detail={
            "error": "NO_CLASS", "message": "This item has no per-class stats."})
    by_key = {f.key: f for f in family.fields}
    clean: dict[str, int] = {}
    clamps: list[dict] = []
    for key, val in fields.items():
        spec = by_key.get(key)
        if spec is None:
            raise HTTPException(status_code=400, detail={
                "error": "UNKNOWN_FIELD",
                "message": f"'{key}' is not a {family.name} field."})
        v, changed = schema.clamp_int(spec, val)
        if changed:
            clamps.append({"key": key, "requested": val, "stored": v})
        clean[key] = v
    return clean, clamps


@router.get("/items")
def list_items(install_id: Optional[str] = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    read_path = _items_path(info, "Items.xml")
    file_present = bool(read_path and read_path.exists())
    write_path = _items_path(info, "Items.xml", for_write=True)
    writable = bool(write_path and write_path.exists())
    rows = ix.read_index(read_path) if file_present else []
    return {
        "items": [r.__dict__ for r in rows],
        "common_schema": schema.common_schema_payload(),
        "install_id": info.id,
        "file_present": file_present,
        "writable": writable,
    }


@router.get("/items/{ui_index}")
def get_item(ui_index: int, install_id: Optional[str] = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    read_path = _items_path(info, "Items.xml")
    if not read_path or not read_path.exists():
        raise HTTPException(status_code=400, detail={
            "error": "ITEMS_NOT_PRESENT", "message": "This install has no Items.xml."})
    try:
        detail = ix.read_item(read_path, ui_index)
    except ix.ItemError as e:
        raise HTTPException(status_code=404, detail={"error": e.code, "message": e.message})

    item_class = detail["ints"].get("usItemClass", 0)
    family = schema.resolve_family(item_class)
    class_fields = None
    class_schema = None
    family_name = None
    if family is not None:
        family_name = family.name
        class_schema = schema.class_schema_payload(family)
        sister_path = _items_path(info, family.filename)
        row = cx.read_row(sister_path, family.record_tag, detail["class_index"]) \
            if sister_path else None
        if row is not None:
            wanted = {f.key for f in family.fields}
            class_fields = {k: v for k, v in row.items() if k in wanted}
    return {**detail, "family": family_name,
            "class_fields": class_fields, "class_schema": class_schema}


@router.put("/items/{ui_index}")
def update_item(ui_index: int, body: ItemUpdateBody,
                install_id: Optional[str] = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    items_path = _items_path(info, "Items.xml", for_write=True)
    if not items_path or not items_path.exists():
        raise HTTPException(status_code=400, detail={
            "error": "ITEMS_NOT_PRESENT", "message": "This install has no Items.xml."})
    if ui_index == schema.TEMPLATE_INDEX:
        raise HTTPException(status_code=400, detail={
            "error": "TEMPLATE_PROTECTED",
            "message": "uiIndex 0 is the template row and can't be edited."})

    clean_str, clean_int, clamps = _validate_common(body.strings, body.ints)

    # Resolve family from the POST-edit class if the edit changes usItemClass,
    # else from the on-disk class.
    detail = ix.read_item(items_path, ui_index)
    eff_class = clean_int.get("usItemClass", detail["ints"].get("usItemClass", 0))
    family = schema.resolve_family(eff_class)
    clean_class, class_clamps = _validate_class(family, body.class_fields)
    clamps += class_clamps

    files = [items_path]
    sister_path = None
    if clean_class and family is not None:
        sister_path = _items_path(info, family.filename, for_write=True)
        if not sister_path or not sister_path.exists():
            raise HTTPException(status_code=400, detail={
                "error": "SISTER_NOT_PRESENT",
                "message": f"This install has no {family.filename}."})
        files.append(sister_path)

    with cross_process_install_lock(info.id), state.write_lock:
        snap = snapshot(install_root=info.path, install_id=info.id,
                        files_to_back_up=files, reason=f"item_edit_{ui_index}")
        try:
            ix.edit_item(items_path, ui_index=ui_index,
                         strings=clean_str, ints=clean_int)
            if clean_class and family is not None and sister_path is not None:
                cx.edit_row(sister_path, record_tag=family.record_tag,
                            class_index=detail["class_index"], fields=clean_class)
        except (ix.ItemError, cx.ClassRowError) as e:
            raise HTTPException(status_code=400,
                                detail={"error": e.code, "message": e.message})
    return {"ok": True, "backup_id": snap.id, "clamps": clamps}


@router.get("/bigitems-catalog")
def bigitems_catalog(install_id: Optional[str] = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    return {"graphics": igph.list_bigitem_graphics(str(info.path))}


@router.get("/bigitem-graphic")
def bigitem_graphic(type: int = Query(...), num: int = Query(...)) -> Response:
    info = get_state().active()
    if info is None:
        raise HTTPException(status_code=400, detail="no active install")
    png = igph.render_bigitem_by_ref(str(info.path), type, num)
    if png is None:
        raise HTTPException(status_code=404, detail="graphic unavailable")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})
```

In `sidecar/main.py`, add `items` to the `from routes import (...)` block and register it next to `backgrounds`:

```python
    app.include_router(items.router, prefix=api_prefix, tags=["items"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sidecar && <venv-python> -m pytest tests/test_items_route.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full sidecar suite (regression gate)**

Run: `cd sidecar && <venv-python> -m pytest -q`
Expected: PASS (existing suite + new tests; no regressions).

- [ ] **Step 6: Commit**

```bash
git add sidecar/routes/items.py sidecar/main.py sidecar/tests/test_items_route.py
git commit -m "feat(items): items editor routes (list/detail/edit/graphics)"
```

---

## Task 7: Frontend API client (`api.ts`)

**Files:**
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: existing `request<T>(path, init?)`, `mediaUrl(pathWithQs)` helpers.
- Produces (exported):
  - Types `ItemSummary`, `ItemFieldSpec`, `ItemsResponse`, `ItemDetail`, `ItemUpdatePayload`, `ItemWriteResult`, `BigItemGraphic`.
  - `listItems(install_id?) => Promise<ItemsResponse>`
  - `getItem(id, install_id?) => Promise<ItemDetail>`
  - `updateItem(id, payload, install_id?) => Promise<ItemWriteResult>`
  - `listBigItems(install_id?) => Promise<{graphics: BigItemGraphic[]}>`
  - `itemGraphicUrl(id) => Promise<string>` (via `mediaUrl('/item-graphic?item=' + id)`)
  - `bigItemGraphicUrl(type, num) => Promise<string>` (via `mediaUrl('/bigitem-graphic?type=&num=')`)

- [ ] **Step 1: Add the types + client functions**

Append to `frontend/src/lib/api.ts` (after the Backgrounds block, mirroring its style):

```typescript
// ────────────────────────────────────────────────────────────────
//   Items
// ────────────────────────────────────────────────────────────────
export interface ItemSummary {
  ui_index: number;
  name: string;
  item_class: number;
  price: number;
  coolness: number;
  graphic_type: number;
  graphic_num: number;
  class_index: number;
}

export interface ItemFieldSpec {
  key: string;
  label: string;
  group: string;
  kind: "str" | "int";
  min?: number;
  max?: number;
  cap?: number;
  advanced?: boolean;
  note?: string;
}

export interface ItemsResponse {
  items: ItemSummary[];
  common_schema: ItemFieldSpec[];
  install_id: string;
  file_present: boolean;
  writable: boolean;
}

export interface ItemDetail {
  ui_index: number;
  strings: Record<string, string>;
  ints: Record<string, number>;
  class_index: number;
  family: string | null;
  class_fields: Record<string, number> | null;
  class_schema: ItemFieldSpec[] | null;
}

export interface ItemUpdatePayload {
  strings: Record<string, string>;
  ints: Record<string, number>;
  class_fields: Record<string, number>;
}

export interface ItemClamp { key: string; requested: number; stored: number; }
export interface ItemWriteResult { ok: boolean; backup_id?: string; clamps?: ItemClamp[]; }
export interface BigItemGraphic { type: number; num: number; stem: string; }

export function listItems(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<ItemsResponse>(`/items${qs}`);
}

export function getItem(id: number, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<ItemDetail>(`/items/${id}${qs}`);
}

export function updateItem(id: number, payload: ItemUpdatePayload, install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<ItemWriteResult>(`/items/${id}${qs}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function listBigItems(install_id?: string) {
  const qs = install_id ? `?install_id=${encodeURIComponent(install_id)}` : "";
  return request<{ graphics: BigItemGraphic[] }>(`/bigitems-catalog${qs}`);
}

export function itemGraphicUrl(id: number): Promise<string> {
  return mediaUrl(`/item-graphic?item=${id}`);
}

export function bigItemGraphicUrl(type: number, num: number): Promise<string> {
  return mediaUrl(`/bigitem-graphic?type=${type}&num=${num}`);
}
```

> Confirm `request` is exported/used the same way the Backgrounds functions use it (some codebases name it `request`, others wrap `fetchWithTimeout`). Match the existing `listBackgrounds` exactly — copy its call shape. If `request` requires a method/JSON-parse contract, the Backgrounds functions are the template.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS (no type errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(items): frontend API client for items editor"
```

---

## Task 8: BigItem picker component (`BigItemPicker.tsx`)

**Files:**
- Create: `frontend/src/components/BigItemPicker.tsx`

**Interfaces:**
- Consumes: `listBigItems`, `bigItemGraphicUrl` (Task 7).
- Produces: default-exported `<BigItemPicker value={{type,num}} onPick={(g)=>void} onClose={()=>void} />`. A modal grid of thumbnails (lazy `<img>` via `bigItemGraphicUrl`), search by stem, highlights the current `value`, calls `onPick({type,num})` on click.

- [ ] **Step 1: Implement the component**

```tsx
// frontend/src/components/BigItemPicker.tsx
import { useEffect, useMemo, useState } from "react";
import { listBigItems, bigItemGraphicUrl, type BigItemGraphic } from "../lib/api";

interface Props {
  value: { type: number; num: number };
  onPick: (g: { type: number; num: number }) => void;
  onClose: () => void;
}

function Thumb({ g, selected, onClick }: {
  g: BigItemGraphic; selected: boolean; onClick: () => void;
}) {
  const [src, setSrc] = useState<string>("");
  useEffect(() => {
    let alive = true;
    bigItemGraphicUrl(g.type, g.num).then((u) => { if (alive) setSrc(u); });
    return () => { alive = false; };
  }, [g.type, g.num]);
  return (
    <button
      onClick={onClick}
      title={g.stem}
      className={`flex flex-col items-center p-1 border rounded ${
        selected ? "border-rust-400 bg-wasteland-800" : "border-wasteland-700"
      }`}
    >
      {src ? <img src={src} alt={g.stem} className="h-10 object-contain" /> : <div className="h-10" />}
      <span className="text-[10px] text-wasteland-400 truncate w-16">{g.stem}</span>
    </button>
  );
}

export default function BigItemPicker({ value, onPick, onClose }: Props) {
  const [graphics, setGraphics] = useState<BigItemGraphic[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { listBigItems().then((r) => setGraphics(r.graphics)); }, []);
  const filtered = useMemo(
    () => graphics.filter((g) => g.stem.toLowerCase().includes(q.toLowerCase())),
    [graphics, q],
  );
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
         onClick={onClose}>
      <div className="card max-w-2xl w-full max-h-[80vh] flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-bold text-wasteland-100">Pick a graphic</h2>
          <button className="btn-secondary text-xs" onClick={onClose}>Close</button>
        </div>
        <input
          className="input mb-2" placeholder="Search (e.g. gun, p1item)…"
          value={q} onChange={(e) => setQ(e.target.value)}
        />
        <div className="grid grid-cols-8 gap-1 overflow-y-auto">
          {filtered.map((g) => (
            <Thumb
              key={g.stem}
              g={g}
              selected={g.type === value.type && g.num === value.num}
              onClick={() => onPick({ type: g.type, num: g.num })}
            />
          ))}
        </div>
        <p className="text-[11px] text-wasteland-500 mt-2">{filtered.length} graphics</p>
      </div>
    </div>
  );
}
```

> CSS class names (`card`, `btn-secondary`, `input`, `rust-400`, `wasteland-*`) follow the project's Tailwind theme used across existing components; verify against `Backgrounds.tsx`/`BackgroundForm.tsx` and adjust if a class name differs.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/BigItemPicker.tsx
git commit -m "feat(items): BigItem graphic picker component"
```

---

## Task 9: Item edit forms (`ItemCommonForm.tsx`, `ItemClassStatsForm.tsx`)

**Files:**
- Create: `frontend/src/components/forms/ItemCommonForm.tsx`
- Create: `frontend/src/components/forms/ItemClassStatsForm.tsx`

**Interfaces:**
- Consumes: `ItemFieldSpec` (Task 7), `BigItemPicker` (Task 8).
- Produces:
  - `ItemCommonForm` props: `{ schema: ItemFieldSpec[]; strings: Record<string,string>; ints: Record<string,number>; onStr(key,val); onInt(key,val); onPickGraphic(g) }`. Renders a labelled input per common field grouped by `spec.group`; str→`<input>`/`<textarea>` (cap-enforced `maxLength`), int→numeric `<input>` clamped to `[min,max]`. Includes a "Change graphic…" button that opens `BigItemPicker`.
  - `ItemClassStatsForm` props: `{ family: string; schema: ItemFieldSpec[]; fields: Record<string,number>; onChange(key,val) }`. One numeric input per class field; nothing rendered if `schema` empty.

- [ ] **Step 1: Implement `ItemCommonForm.tsx`**

```tsx
// frontend/src/components/forms/ItemCommonForm.tsx
import { useState } from "react";
import type { ItemFieldSpec } from "../../lib/api";
import BigItemPicker from "../BigItemPicker";

interface Props {
  schema: ItemFieldSpec[];
  strings: Record<string, string>;
  ints: Record<string, number>;
  onStr: (key: string, val: string) => void;
  onInt: (key: string, val: number) => void;
  onPickGraphic: (g: { type: number; num: number }) => void;
}

export default function ItemCommonForm(props: Props) {
  const { schema, strings, ints, onStr, onInt, onPickGraphic } = props;
  const [picking, setPicking] = useState(false);
  const groups = [...new Set(schema.map((f) => f.group))];
  const gType = ints["ubGraphicType"] ?? 0;
  const gNum = ints["ubGraphicNum"] ?? 0;
  return (
    <div className="space-y-4">
      {groups.map((group) => (
        <fieldset key={group} className="border border-wasteland-700 rounded p-2">
          <legend className="text-xs text-wasteland-400 px-1">{group}</legend>
          <div className="grid grid-cols-2 gap-2">
            {schema.filter((f) => f.group === group).map((f) => (
              <label key={f.key} className="flex flex-col text-xs gap-0.5"
                     title={f.note ?? f.key}>
                <span className={f.advanced ? "text-wasteland-500" : "text-wasteland-300"}>
                  {f.label}{f.advanced ? " (advanced)" : ""}
                </span>
                {f.kind === "str" ? (
                  f.key === "szItemDesc" || f.key === "szBRDesc" ? (
                    <textarea className="input" rows={2} maxLength={f.cap}
                      value={strings[f.key] ?? ""}
                      onChange={(e) => onStr(f.key, e.target.value)} />
                  ) : (
                    <input className="input" maxLength={f.cap}
                      value={strings[f.key] ?? ""}
                      onChange={(e) => onStr(f.key, e.target.value)} />
                  )
                ) : (
                  <input className="input" type="number" min={f.min} max={f.max}
                    value={ints[f.key] ?? 0}
                    onChange={(e) => onInt(f.key, Number(e.target.value))} />
                )}
              </label>
            ))}
          </div>
          {group === "Graphic" && (
            <button className="btn-secondary text-xs mt-2"
                    onClick={() => setPicking(true)}>
              Change graphic…
            </button>
          )}
        </fieldset>
      ))}
      {picking && (
        <BigItemPicker
          value={{ type: gType, num: gNum }}
          onPick={(g) => { onPickGraphic(g); setPicking(false); }}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Implement `ItemClassStatsForm.tsx`**

```tsx
// frontend/src/components/forms/ItemClassStatsForm.tsx
import type { ItemFieldSpec } from "../../lib/api";

interface Props {
  family: string;
  schema: ItemFieldSpec[];
  fields: Record<string, number>;
  onChange: (key: string, val: number) => void;
}

export default function ItemClassStatsForm({ family, schema, fields, onChange }: Props) {
  if (!schema.length) return null;
  return (
    <fieldset className="border border-wasteland-700 rounded p-2">
      <legend className="text-xs text-rust-400 px-1">{family} stats</legend>
      <div className="grid grid-cols-2 gap-2">
        {schema.map((f) => (
          <label key={f.key} className="flex flex-col text-xs gap-0.5" title={f.key}>
            <span className="text-wasteland-300">{f.label}</span>
            <input className="input" type="number" min={f.min} max={f.max}
              value={fields[f.key] ?? 0}
              onChange={(e) => onChange(f.key, Number(e.target.value))} />
          </label>
        ))}
      </div>
    </fieldset>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/forms/ItemCommonForm.tsx frontend/src/components/forms/ItemClassStatsForm.tsx
git commit -m "feat(items): item common + per-class edit forms"
```

---

## Task 10: Items route page (`Items.tsx`) + nav wiring

**Files:**
- Create: `frontend/src/routes/Items.tsx`
- Modify: `frontend/src/App.tsx` (lazy import + `<Route path="/items">`)
- Modify: `frontend/src/routes/Hub.tsx` (add "Items" tile to `secondary`)

**Interfaces:**
- Consumes: `listItems`, `getItem`, `updateItem`, `itemGraphicUrl` (Task 7); `ItemCommonForm`, `ItemClassStatsForm` (Task 9); `useQuery`/`useQueryClient` (react-query, already used app-wide).
- Produces: default-exported `Items` route. Left: searchable + class-filtered list of `ItemSummary` (thumbnail via `itemGraphicUrl`, name, id, price). Right: edit panel that loads `getItem(selectedId)` into local form state and saves via `updateItem`, invalidating both the list and the detail query on success (so thumbnails refresh after a re-point).

- [ ] **Step 1: Implement `Items.tsx`**

```tsx
// frontend/src/routes/Items.tsx
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listItems, getItem, updateItem, itemGraphicUrl,
  type ItemSummary, type ItemDetail,
} from "../lib/api";
import ItemCommonForm from "../components/forms/ItemCommonForm";
import ItemClassStatsForm from "../components/forms/ItemClassStatsForm";

function Thumb({ id }: { id: number }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let alive = true;
    itemGraphicUrl(id).then((u) => { if (alive) setSrc(u); });
    return () => { alive = false; };
  }, [id]);
  return src
    ? <img src={src} alt="" className="h-6 w-6 object-contain"
           onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")} />
    : <div className="h-6 w-6" />;
}

export default function Items() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["items"], queryFn: () => listItems() });
  const [selected, setSelected] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [draft, setDraft] = useState<ItemDetail | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string>("");

  const detail = useQuery({
    queryKey: ["item", selected],
    queryFn: () => getItem(selected as number),
    enabled: selected !== null,
  });
  useEffect(() => { if (detail.data) setDraft(detail.data); }, [detail.data]);

  const rows = useMemo(() => {
    const items = list.data?.items ?? [];
    const needle = q.toLowerCase();
    return items.filter(
      (it) => it.name.toLowerCase().includes(needle) || String(it.ui_index) === q,
    );
  }, [list.data, q]);

  async function save() {
    if (!draft || selected === null) return;
    setSaving(true);
    setMsg("");
    try {
      const res = await updateItem(selected, {
        strings: draft.strings,
        ints: draft.ints,
        class_fields: draft.class_fields ?? {},
      });
      const clampNote = res.clamps?.length
        ? ` (${res.clamps.length} value(s) clamped)` : "";
      setMsg(`Saved${clampNote}. Backup ${res.backup_id ?? ""}.`);
      await qc.invalidateQueries({ queryKey: ["items"] });
      await qc.invalidateQueries({ queryKey: ["item", selected] });
    } catch (e) {
      setMsg(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full gap-3 p-3">
      <div className="w-80 flex flex-col">
        <h1 className="font-bold text-wasteland-100 mb-2">Items</h1>
        <input className="input mb-2" placeholder="Search name or id…"
               value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="flex-1 overflow-y-auto border border-wasteland-700 rounded">
          {rows.slice(0, 400).map((it: ItemSummary) => (
            <button key={it.ui_index}
              onClick={() => setSelected(it.ui_index)}
              className={`flex items-center gap-2 w-full px-2 py-1 text-left text-xs ${
                selected === it.ui_index ? "bg-wasteland-800" : ""}`}>
              <Thumb id={it.ui_index} />
              <span className="text-wasteland-500 w-10">{it.ui_index}</span>
              <span className="flex-1 truncate text-wasteland-200">{it.name}</span>
              <span className="text-wasteland-500">${it.price}</span>
            </button>
          ))}
          {rows.length > 400 && (
            <p className="text-[11px] text-wasteland-500 p-2">
              Showing first 400 of {rows.length}. Refine your search.
            </p>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {selected === null && <p className="text-wasteland-500 text-sm">Select an item to edit.</p>}
        {selected !== null && draft && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-bold text-wasteland-100">
                #{draft.ui_index} {draft.strings["szItemName"] ?? ""}
              </h2>
              <button className="btn-primary text-xs" disabled={saving} onClick={save}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
            {msg && <p className="text-xs text-rust-300">{msg}</p>}
            <ItemCommonForm
              schema={list.data?.common_schema ?? []}
              strings={draft.strings}
              ints={draft.ints}
              onStr={(k, v) => setDraft({ ...draft, strings: { ...draft.strings, [k]: v } })}
              onInt={(k, v) => setDraft({ ...draft, ints: { ...draft.ints, [k]: v } })}
              onPickGraphic={(g) => setDraft({
                ...draft,
                ints: { ...draft.ints, ubGraphicType: g.type, ubGraphicNum: g.num },
              })}
            />
            {draft.family && draft.class_schema && (
              <ItemClassStatsForm
                family={draft.family}
                schema={draft.class_schema}
                fields={draft.class_fields ?? {}}
                onChange={(k, v) => setDraft({
                  ...draft,
                  class_fields: { ...(draft.class_fields ?? {}), [k]: v },
                })}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire the route into `App.tsx`**

Add the lazy import alongside the others (near line 32):

```tsx
const Items = lazy(() => import("./routes/Items"));
```

Add the route alongside `/backgrounds` (near line 192):

```tsx
          <Route path="/items" element={<Items />} />
```

- [ ] **Step 3: Add the Hub tile in `Hub.tsx`**

Insert into the `secondary` array (after the `backgrounds` tile, near line 72):

```tsx
  {
    id: "items",
    label: "Items",
    href: "/items",
    icon: "🔫",
    description: "Browse every item, see its inventory graphic, edit name/price/coolness and per-class stats, and re-point its graphic to existing art.",
  },
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/Items.tsx frontend/src/App.tsx frontend/src/routes/Hub.tsx
git commit -m "feat(items): Items editor route + Hub tile"
```

---

## Task 11: End-to-end browser verification

**Files:** none (verification only).

This task confirms the feature works in the real app, since there is no frontend unit-test runner. Follow the browser-dev recipe (`reference_mercforge_browserdev_verify`).

- [ ] **Step 1: Start the sidecar against the canonical Copy install**

Run (background): `cd sidecar && <venv-python> main.py --port 8000`
Then POST the active install so routes resolve:
`curl -X POST "http://127.0.0.1:8000/api/v1/installs/active" -H "Content-Type: application/json" -d '{"install_id":"<copy-install-id>"}'`
(Discover the id via `GET /api/v1/installs`.)

- [ ] **Step 2: Start vite dev**

Run (background): `cd frontend && npm run dev` (serves on `http://localhost:1420`, the sidecar's only allowed CORS origin).

- [ ] **Step 3: Drive the page with Playwright**

Navigate to `http://localhost:1420/items`. Verify:
- The list renders with thumbnails + names (e.g. search "Glock").
- Selecting Glock 17 shows common fields populated AND a "Weapon stats" section with `ubImpact` = 25, `usRange` = 115.
- Open "Change graphic…", pick a different graphic, confirm the preview/thumbnail changes.
- Edit the name + price, click Save, confirm the "Saved … Backup …" message, and that the list thumbnail/name refresh.
Take a screenshot (narrow the viewport first, ≤5s cap) for the record.

- [ ] **Step 4: Confirm the write + backup on disk**

Verify `Data-1.13/TableData/Items/Items.xml` shows the edited name/price and that a backup entry exists (GET `/api/v1/backup` list or the Backups page). Confirm a per-class edit also wrote `Weapons.xml`.

- [ ] **Step 5: Final regression gate**

Run: `cd sidecar && <venv-python> -m pytest -q` → PASS.
Run: `cd frontend && npm run typecheck` → PASS.

- [ ] **Step 6: Commit any verification fixups** (only if Step 3/4 surfaced bugs).

---

## Self-Review

**Spec coverage (S1 + S2):**
- Browse all items + thumbnails + search/filter → Task 10 (+ Task 7 graphic URL). ✓
- Edit common fields → Tasks 2, 3, 6, 9, 10. ✓
- Re-point image to existing art → Tasks 5 (catalog/render-by-ref), 6 (routes), 8 (picker), 9 (button). ✓
- Per-class stats (S2) → Tasks 2 (class schema), 4 (sister writer), 6 (resolve + coordinated write), 9 (form), 10 (panel). ✓
- Byte-splice + encoding discipline → Tasks 1, 3, 4. ✓
- Lock + snapshot every write, multi-file consistent → Task 6. ✓
- Clamp + caps + template protection → Tasks 2, 3, 6. ✓
- `ubClassIndex` linkage → Tasks 3 (`class_index` surfaced), 6 (resolve + edit_row keyed by class index). ✓
- Active-install target → Task 6 (`_resolve_install` / `get_state().active()`). ✓
- S3/S4 explicitly out of scope. ✓

**Placeholder scan:** No "TBD"/"implement later"; every code step has full code. The two soft notes (BIGITEMS SLF `listdir` member name; `request`/CSS class-name confirmation) are concrete "verify against existing file X" instructions, not missing logic.

**Type consistency:** `ItemSummary` fields match `read_index`/`r.__dict__`. `ItemDetail` (`strings/ints/class_index/family/class_fields/class_schema`) matches the `GET /items/{id}` payload. `ItemUpdatePayload` (`strings/ints/class_fields`) matches `ItemUpdateBody`. `clamp_int`/`utf16_len`/`resolve_family` names consistent across Tasks 2/3/6. Frontend `updateItem`/`getItem`/`listItems`/`itemGraphicUrl` names consistent across Tasks 7/8/9/10.

**Known approximation:** per-class numeric clamps in Task 2 are type-derived (ub/b/us/s ranges), not exact engine clamps. This is safe (the cast width is correct) and called out; a pre-S2 research note refines exact ranges if needed without changing the validation path.
