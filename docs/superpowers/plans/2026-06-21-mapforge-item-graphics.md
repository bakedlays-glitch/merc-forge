# MapForge Item-Graphic Rendering (Phase B2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Render placed world items as their actual BIGITEMS graphic (the same art the engine puts on the ground) instead of a dot — completing the authentic overlay.

**Architecture:** Mirrors the shipped soldier-sprite slice. A sidecar endpoint resolves `usItem → Items.xml (ubGraphicType, ubGraphicNum) → BIGITEMS\<stem>.sti → PNG` (reusing the existing STI/SLF helpers); the frontend blits the item PNG (scaled to fit) at the item tile, falling back to the existing dot.

**Tech Stack:** Python 3 (FastAPI, ja2py STI/SLF, ElementTree), pytest; TS + React (SVG `<image>`).

## Global Constraints

- Recipe source-of-truth (PROVEN — 2 items decoded from install): `docs/superpowers/specs/2026-06-21-item-graphic-research.md`.
- **usItem → graphic:** parse `<install>/Data-1.13/TableData/Items/Items.xml` (the `<ITEM>` keyed by `<uiIndex>`); read `<ubGraphicType>` (absent → 0) + `<ubGraphicNum>` (absent → 0).
- **BIGITEMS filename stem:** `ubGraphicType == 0` → `gun<NN>`; else → `p<type>item<NN>`. `<NN>` = `f"{ubGraphicNum:02d}"` (2-digit only when <10: `gun09`, `gun24`, `p1item05`, `p1item96`). File = `BIGITEMS\<stem>.STI`, single image → **decode frame 0**.
- **Resolution (loose-first, like soldier sprites):** (1) `<root>/Data-1.13/BigItems/<stem>.STI` (case-insensitive) → (2) `<root>/Data/BigItems/<stem>.STI` → (3) SLF `<root>/Data/Bigitems.slf` member `BIGITEMS/<stem>.STI`. Reuse `install_context._open_slf_cached`.
- **Reuse (no reinvention):** `ja2py.fileformats.Sti.is_8bit_sti/load_8bit_sti` (implicitly, via) `mercwizard_core.sti_decode.decode_sti_frame_to_png(bytes, 0)`; `_open_slf_cached`. Model the resolver on `soldier_sprite._resolve_anim_sti_bytes`.
- Read-only asset decode; no `.dat`/asset writes.
- Unmapped usItem (not in Items.xml) or missing STI → endpoint 404; the overlay keeps its dot.
- BIGITEMS pics are inventory-scale (~40–120 px wide) — the frontend renders them scaled to fit a small box (cap ~1.5 tiles wide, preserve aspect), centered on the tile (NOT bottom-anchored — items are flat icons, not standing figures).
- Items are sparse (1–3/sector) and only legacy Path-B maps expose `usItem` today — acceptable; the feature lights up where items already parse.
- Venv: `./.venv/Scripts/python.exe` from `sidecar/`. Frontend gate: `tsc --noEmit` exit 0. **After any frontend edit, verify `git diff --stat` shows a small diff (NOT a whole-file CRLF flip) and the file stays LF.**
- Commits end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Branch `mapforge-item-graphics`; USER pushes.

---

### Task 1: Sidecar — item-graphic endpoint

**Files:**
- Create: `sidecar/mercwizard_core/mapforge_engine/item_graphic.py`
- Modify: `sidecar/routes/mapforge.py` (endpoint)
- Test: `sidecar/tests/test_item_graphic.py`

**Interfaces:**
- Produces: `render_item_graphic(install_root: str, us_item: int) -> bytes | None` (PNG); `GET /mapforge/item-graphic?item=` → `image/png`.

- [ ] **Step 1: Write the failing test**

```python
# sidecar/tests/test_item_graphic.py
import os, pytest
from mercwizard_core.mapforge_engine.item_graphic import (
    render_item_graphic, _bigitems_stem,
)

_INSTALL = r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"

def test_bigitems_stem_padding():
    assert _bigitems_stem(0, 24) == "gun24"
    assert _bigitems_stem(0, 9) == "gun09"
    assert _bigitems_stem(1, 96) == "p1item96"
    assert _bigitems_stem(1, 5) == "p1item05"
    assert _bigitems_stem(2, 13) == "p2item13"

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_renders_famas_png():
    png = render_item_graphic(_INSTALL, 24)   # FAMAS → gun24.sti
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 100

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_renders_leather_jacket_png():
    png = render_item_graphic(_INSTALL, 188)  # Leather Jacket → p1item96.sti
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_unknown_item_returns_none():
    assert render_item_graphic(_INSTALL, 65000) is None   # not in Items.xml
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_item_graphic.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `item_graphic.py`**

```python
# sidecar/mercwizard_core/mapforge_engine/item_graphic.py
"""Decode a world item's BIGITEMS graphic to PNG for the MapForge overlay.
usItem -> Items.xml (ubGraphicType, ubGraphicNum) -> BIGITEMS\\<stem>.sti -> frame 0.
Reuses the existing STI/SLF helpers; never writes. Recipe validated in
docs/superpowers/specs/2026-06-21-item-graphic-research.md.
"""
from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# (install_root) -> {uiIndex: (ubGraphicType, ubGraphicNum)}, fingerprinted by mtime_ns.
_ITEMS_CACHE: dict[str, tuple[int, dict[int, tuple[int, int]]]] = {}
_ITEMS_LOCK = threading.Lock()


def _items_xml_path(install_root: str) -> Optional[Path]:
    for rel in ("Data-1.13/TableData/Items/Items.xml", "Data/TableData/Items/Items.xml"):
        p = Path(install_root) / rel
        if p.is_file():
            return p
    return None


def _load_item_graphics(install_root: str) -> dict[int, tuple[int, int]]:
    path = _items_xml_path(install_root)
    if path is None:
        return {}
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _ITEMS_LOCK:
        hit = _ITEMS_CACHE.get(install_root)
        if hit and hit[0] == mtime:
            return hit[1]
    out: dict[int, tuple[int, int]] = {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}
    for item in root.findall("ITEM"):
        idx = item.findtext("uiIndex")
        if idx is None:
            continue
        try:
            ui = int(idx)
        except ValueError:
            continue
        def _int(tag: str) -> int:
            t = item.findtext(tag)
            try:
                return int(t) if t is not None else 0
            except ValueError:
                return 0
        out[ui] = (_int("ubGraphicType"), _int("ubGraphicNum"))
    with _ITEMS_LOCK:
        _ITEMS_CACHE[install_root] = (mtime, out)
    return out


def _bigitems_stem(gtype: int, gnum: int) -> str:
    """BIGITEMS filename stem. type 0 -> gun<NN>; else p<type>item<NN>.
    <NN> = 2-digit only when <10 (gun09, gun24, p1item05, p1item96)."""
    nn = f"{gnum:02d}"
    return f"gun{nn}" if gtype == 0 else f"p{gtype}item{nn}"


def _resolve_bigitem_bytes(install_root: str, stem: str) -> Optional[bytes]:
    root = Path(install_root)
    fname = f"{stem}.STI"
    # loose-first, case-insensitive.
    for base in (root / "Data-1.13" / "BigItems", root / "Data" / "BigItems"):
        if not base.is_dir():
            continue
        try:
            for child in base.iterdir():
                if child.name.lower() == fname.lower() and child.is_file():
                    return child.read_bytes()
        except OSError:
            pass
    # SLF fallback: Data/Bigitems.slf member BIGITEMS/<stem>.STI.
    slf_path = root / "Data" / "Bigitems.slf"
    if slf_path.is_file():
        try:
            from mercwizard_core.install_context import _open_slf_cached
            slf = _open_slf_cached(slf_path)
            internal = f"/BIGITEMS/{stem}.STI"
            if slf is not None and slf.isfile(internal):
                return slf.openbin(internal, "r").read()
        except Exception:
            pass
    return None


def render_item_graphic(install_root: str, us_item: int) -> Optional[bytes]:
    """PNG of `us_item`'s BIGITEMS graphic, or None (unknown item / missing STI)."""
    graphics = _load_item_graphics(install_root)
    gfx = graphics.get(us_item)
    if gfx is None:
        return None
    stem = _bigitems_stem(gfx[0], gfx[1])
    data = _resolve_bigitem_bytes(install_root, stem)
    if data is None:
        return None
    try:
        from mercwizard_core.sti_decode import decode_sti_frame_to_png
        return decode_sti_frame_to_png(data, 0)
    except Exception:
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_item_graphic.py -q`
Expected: PASS (install-gated render tests run if install present, else skip).

- [ ] **Step 5: Add the endpoint**

In `routes/mapforge.py`, next to the `/soldier-sprite` endpoint:

```python
# Install-scoped (not session-scoped): usItem is a global asset lookup.
@router.get("/item-graphic")
def item_graphic(item: int = Query(...)):
    """PNG of a world item's BIGITEMS graphic (read-only asset decode).
    404 when the item is unmapped or its STI is missing."""
    root = _active_install_root()
    if root is None:
        raise HTTPException(status_code=400, detail="no active install")
    from mercwizard_core.mapforge_engine.item_graphic import render_item_graphic
    png = render_item_graphic(str(root), item)
    if png is None:
        raise HTTPException(status_code=404, detail="item graphic unavailable")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})
```

- [ ] **Step 6: Run the suite**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/ -q -k "mapforge or item_graphic or soldier_sprite"`
Expected: PASS, no regression.

- [ ] **Step 7: Commit**

```bash
git add sidecar/mercwizard_core/mapforge_engine/item_graphic.py sidecar/routes/mapforge.py sidecar/tests/test_item_graphic.py
git commit -m "$(printf 'MapForge overlay: item-graphic endpoint (BIGITEMS)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Frontend — render item graphics

**Files:**
- Modify: `frontend/src/routes/MapForgeSector.tsx`

**Interfaces:**
- Consumes: `appendix.items` (each has `usItem`, `x`, `y`); `mediaUrl`. (No `mapforge.ts` change — `usItem` is already on the item type.)
- Produces: items render as scaled `<image>` graphics centered at the tile, falling back to the existing item dot while loading / on failure.

- [ ] **Step 1: Item sprite cache (mirror the soldier sprite cache)**

Add an `itemCache` state `Map<string, {url,w,h} | null>` keyed by `String(it.usItem)`, filled by a cancel-guarded effect on `appendix`/`showItems` change (dedupe by usItem; resolve via `mediaUrl(\`/mapforge/item-graphic?item=${usItem}\`)`; load an `Image` to measure; functional setState; null sentinel on error; clear the cache on session change in the same effect that resets `appendix`/`spriteCache`). Mirror the existing soldier `spriteCache` exactly (parallel `Promise.all` load). Thread `itemCache` to the overlay child component the same way `spriteCache` is threaded.

- [ ] **Step 2: Render the item graphic (fallback to dot)**

In the items marker map, if a loaded item graphic exists for `String(it.usItem)`, render a scaled `<image>` centered at the tile (cap the display width at ~1.5 tiles, preserve aspect); else the existing green dot. BIGITEMS art is wide, so scale down:

```tsx
{showItems && appendix.items.map((it, i) => {
  const { cx, cy } = c(it.x, it.y);
  const g = itemCache.get(String(it.usItem));
  if (g) {
    const maxW = meta.tileW * 1.5;
    const scale = Math.min(1, maxW / g.w);
    const w = g.w * scale, h = g.h * scale;
    return <image key={`it-${i}`} href={g.url} width={w} height={h}
      x={cx - w / 2} y={cy - h / 2} style={{ imageRendering: "pixelated" }}>
      <title>{`item ${it.usItem}`}</title>
    </image>;
  }
  return <circle key={`it-${i}`} cx={cx} cy={cy} r={3}
    fill="rgba(120,255,160,0.9)" stroke="rgba(40,160,80,0.9)" strokeWidth={1}
    vectorEffect="non-scaling-stroke"><title>{`item ${it.usItem}`}</title></circle>;
})}
```

- [ ] **Step 3: Typecheck + EOL check**

Run: `cd frontend && node node_modules/typescript/bin/tsc --noEmit` → exit 0.
Then confirm no line-ending flip: `git diff --stat` shows a small change to `MapForgeSector.tsx` (NOT thousands of lines), and the file is still LF.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/MapForgeSector.tsx
git commit -m "$(printf 'MapForge overlay: render item graphics (fallback to dot)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

- [ ] **Step 5: Manual verify (controller)**

Browser-dev: open a Path-B map with items (A1/A2/C7) → items render as their BIGITEMS picture (gun/jacket/etc.) at the tile.

---

## Self-Review

**Spec coverage:** Items.xml graphic loader → Task 1; BIGITEMS stem rule (pad-2 <10) → Task 1 `_bigitems_stem` + test; loose-first→SLF resolution → Task 1; reuse `decode_sti_frame_to_png`/`_open_slf_cached` → Task 1; endpoint (404 on unmapped) → Task 1; frontend scaled `<image>` + dot fallback → Task 2; install-gated real-item tests → Task 1; EOL guard → Global Constraints + Task 2 Step 3. ✓
**Placeholder scan:** none. ✓
**Type consistency:** endpoint param `item` matches the frontend URL; `itemCache` mirrors `spriteCache` shape; `usItem` already on the item type. ✓
