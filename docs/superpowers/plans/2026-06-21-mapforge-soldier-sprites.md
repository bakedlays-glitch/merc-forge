# MapForge Soldier Body-Sprite Rendering (Phase A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Render NPC placements as their actual JA2 body-type **standing sprites** (8-direction, facing the stored direction) instead of colored dots — the way the in-game editor renders them.

**Architecture:** A sidecar endpoint decodes the body type's standing-STI frame for a direction (reusing existing SLF + STI helpers) and returns a PNG; the overlay surfaces each soldier's `body_type`; the frontend blits the sprite at the iso tile, falling back to the team-colored circle when a sprite is unavailable.

**Tech Stack:** Python 3 (FastAPI, ja2py STI/SLF), pytest; TS + React (SVG `<image>`).

## Global Constraints

- Layout/recipe source-of-truth (PROVEN — 5 sprites decoded from the live install): `docs/superpowers/specs/2026-06-21-soldier-sprite-research.md`.
- **bodytype → standing STI** (internal path under `Anims`), hardcode this table (from the research §1):
  `0 REGMALE → S_MERC/S_R_STD.STI · 1 BIGMALE → M_MERC/M_R_STD.STI · 2 STOCKYMALE → S_MERC/S_R_STD.STI · 3 REGFEMALE → F_MERC/F_BRETH2.STI · 4 ADULTFEMALEMONSTER → MONSTERS/MN_BREAT.STI · 11 FATCIV → CIVS/FT_BRTH.STI · 12 MANCIV → CIVS/M_BREATH.STI · 20 BLOODCAT → ANIMALS/CT_BREATH.STI · 29 DOG → ANIMALS/DOG_BREATH.STI · 30 GORISCLAW → ANIMALS/GORIS_BREATH.STI · 31 GRUTHARCLAW → ANIMALS/GRUTHAR_BREATH.STI · 32 MOMCLAW → ANIMALS/MOM_BREATH.STI`. Unmapped bodytype → fall back to REGMALE's STI.
- **Direction→frame (the trap):** standing STIs are `8 dir × M frames/dir` contiguous. `framesPerDir = total_subimages // 8; sub = framesPerDir * ((direction + 1) % 8)`. The `(d+1)%8` is the engine's `gOneCDirection` clockwise remap — do NOT use `frame == direction`.
- **Asset resolution: loose-first, then SLF.** (1) loose `<install_root>/Data/Anims/<path>` (case-insensitive; creatures ship loose-only). (2) SLF `<install_root>/Data/Anims.slf`, internal path `/<path>` (forward slashes). Reuse `mercwizard_core/install_context.py:_open_slf_cached` for the SLF.
- **Decode helpers (reuse, don't reinvent):** `from ja2py.fileformats.Sti import is_8bit_sti, load_8bit_sti`; `from mercwizard_core.sti_decode import decode_subimage_to_rgba`. Load once: `images = load_8bit_sti(io.BytesIO(data))`; `total = len(images.images)`; decode the computed sub via `decode_subimage_to_rgba(images, sub)` → save PNG.
- Embedded palette is sufficient (no `.col`). `team` is parsed but NOT used here (palette-tint is out of scope).
- Read-only: this decodes/serves assets; no `.dat` or asset writes.
- Surfacing: the overlay's soldier records gain `body_type` (already parsed at record offset 10 but not currently output).
- Venv: `./.venv/Scripts/python.exe` from `sidecar/`. Frontend gate: `tsc --noEmit` exit 0.
- Commits end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Branch `mapforge-soldier-sprites`; USER pushes.

---

### Task 1: Sidecar — body_type surfacing + sprite endpoint

**Files:**
- Modify: `sidecar/mercwizard_core/mapforge_engine/appendix_extract.py` (add `body_type` to soldier output)
- Modify: `sidecar/routes/mapforge.py` (AppendixSoldier model + the new endpoint)
- Create: `sidecar/mercwizard_core/mapforge_engine/soldier_sprite.py`
- Test: `sidecar/tests/test_mapforge_appendix_extract.py` (body_type) + `sidecar/tests/test_soldier_sprite.py` (sprite)

**Interfaces:**
- Produces: soldier dict gains `body_type: int`; `AppendixSoldier` gains `body_type`. `render_standing_sprite(install_root, bodytype, direction) -> bytes | None` (PNG). `GET /mapforge/soldier-sprite?bodytype=&dir=` → `image/png`.

- [ ] **Step 1: Surface `body_type` (TDD)**

In `test_mapforge_appendix_extract.py`, extend `test_extracts_soldiers_with_positions_and_team`: the `_old_soldier` helper already writes a record; add a `body_type=` param to it (write at offset 10) and assert the extracted soldier has the right `body_type`. (Read the existing `_old_soldier` helper; it currently sets gridno/team/facing/class — add `body_type` at byte 10, default e.g. 0.)

In `appendix_extract.py`, in the soldier loop, read `body = data[pos + (10 if major < 7.0 else 14)]` (ubBodyType: offset 10 in the 52B v5 record, 14 in the 64B v7 record), and add `"body_type": body` to the appended soldier dict. Run the test → GREEN.

- [ ] **Step 2: Write the failing sprite test**

```python
# sidecar/tests/test_soldier_sprite.py
import os, struct, pytest
from mercwizard_core.mapforge_engine.soldier_sprite import (
    render_standing_sprite, BODYTYPE_STANDING_STI,
)

_INSTALL = r"C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy"

def test_bodytype_table_has_core_bodies():
    for bt in (0, 1, 3, 4, 29):   # REGMALE, BIGMALE, REGFEMALE, monster, DOG
        assert bt in BODYTYPE_STANDING_STI

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_renders_regmale_standing_png():
    png = render_standing_sprite(_INSTALL, bodytype=0, direction=2)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"   # PNG signature
    assert len(png) > 100

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_renders_dog_creature_loose_png():
    png = render_standing_sprite(_INSTALL, bodytype=29, direction=0)  # DOG = loose-only
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

@pytest.mark.skipif(not os.path.exists(_INSTALL), reason="canonical install not present")
def test_unmapped_bodytype_falls_back_to_regmale():
    png = render_standing_sprite(_INSTALL, bodytype=99, direction=0)  # no mapping
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_soldier_sprite.py -q`
Expected: FAIL — `ModuleNotFoundError: ...soldier_sprite`.

- [ ] **Step 4: Implement `soldier_sprite.py`**

```python
# sidecar/mercwizard_core/mapforge_engine/soldier_sprite.py
"""Decode a body type's STANDING sprite (one direction) to PNG, for the
MapForge NPC overlay. Reuses the existing SLF + STI helpers; never writes.
Recipe + bodytype->STI table validated in
docs/superpowers/specs/2026-06-21-soldier-sprite-research.md.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

# bodytype int -> standing STI internal path under Anims/ (forward slashes).
BODYTYPE_STANDING_STI: dict[int, str] = {
    0:  "S_MERC/S_R_STD.STI",      # REGMALE
    1:  "M_MERC/M_R_STD.STI",      # BIGMALE
    2:  "S_MERC/S_R_STD.STI",      # STOCKYMALE (shares regular male)
    3:  "F_MERC/F_BRETH2.STI",     # REGFEMALE
    4:  "MONSTERS/MN_BREAT.STI",   # ADULTFEMALEMONSTER
    11: "CIVS/FT_BRTH.STI",        # FATCIV
    12: "CIVS/M_BREATH.STI",       # MANCIV
    20: "ANIMALS/CT_BREATH.STI",   # BLOODCAT
    29: "ANIMALS/DOG_BREATH.STI",  # DOG (Wasteland)
    30: "ANIMALS/GORIS_BREATH.STI",
    31: "ANIMALS/GRUTHAR_BREATH.STI",
    32: "ANIMALS/MOM_BREATH.STI",
}
_FALLBACK = "S_MERC/S_R_STD.STI"


def _resolve_anim_sti_bytes(install_root: str, internal_path: str) -> Optional[bytes]:
    """Loose-first (<root>/Data/Anims/<path>, case-insensitive), then
    <root>/Data/Anims.slf at /<path>. Returns raw STI bytes or None."""
    root = Path(install_root)
    rel = internal_path.replace("\\", "/").strip("/")
    # 1. loose — case-insensitive walk of each path segment.
    for base in (root / "Data" / "Anims", root / "Data-1.13" / "Anims"):
        cur = base
        ok = base.is_dir()
        for seg in rel.split("/"):
            if not ok:
                break
            match = None
            try:
                for child in cur.iterdir():
                    if child.name.lower() == seg.lower():
                        match = child
                        break
            except OSError:
                ok = False
                break
            if match is None:
                ok = False
                break
            cur = match
        if ok and cur.is_file():
            try:
                return cur.read_bytes()
            except OSError:
                pass
    # 2. SLF.
    slf_path = root / "Data" / "Anims.slf"
    if slf_path.is_file():
        try:
            from mercwizard_core.install_context import _open_slf_cached
            slf = _open_slf_cached(slf_path)
            internal = "/" + rel
            if slf.isfile(internal):
                return slf.openbin(internal, "r").read()
        except Exception:
            pass
    return None


def render_standing_sprite(install_root: str, bodytype: int,
                           direction: int) -> Optional[bytes]:
    """PNG of `bodytype`'s standing sprite facing `direction` (0-7), or None."""
    path = BODYTYPE_STANDING_STI.get(bodytype, _FALLBACK)
    data = _resolve_anim_sti_bytes(install_root, path)
    if data is None and path != _FALLBACK:
        data = _resolve_anim_sti_bytes(install_root, _FALLBACK)
    if data is None:
        return None
    try:
        from ja2py.fileformats.Sti import is_8bit_sti, load_8bit_sti
        from mercwizard_core.sti_decode import decode_subimage_to_rgba
        buf = io.BytesIO(data)
        if not is_8bit_sti(buf):
            return None
        buf.seek(0)
        images = load_8bit_sti(buf)
        total = len(images.images)
        if total < 8:
            return None
        frames_per_dir = total // 8
        sub = frames_per_dir * ((direction + 1) % 8)
        if sub >= total:
            sub = 0
        rgba = decode_subimage_to_rgba(images, sub)
        out = io.BytesIO()
        rgba.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/test_soldier_sprite.py -q`
Expected: PASS (the install-gated ones run if the install is present, else skip).

- [ ] **Step 6: Add the endpoint + model field**

In `routes/mapforge.py`: add `body_type: int` to the `AppendixSoldier` model. Add the route (resolve the active install root via the existing helper — look at how other routes get the active install, e.g. `_active_install_root()` / `_active_install_or_400()`):

```python
@router.get("/soldier-sprite")
def soldier_sprite(bodytype: int = Query(...), dir: int = Query(0, ge=0, le=7)):
    """PNG of a body type's standing sprite facing `dir` (read-only asset decode).
    Falls back to REGMALE for unmapped body types; 404 if no install/asset."""
    root = _active_install_root()
    if root is None:
        raise HTTPException(status_code=400, detail="no active install")
    from mercwizard_core.mapforge_engine.soldier_sprite import render_standing_sprite
    png = render_standing_sprite(str(root), bodytype, dir)
    if png is None:
        raise HTTPException(status_code=404, detail="sprite unavailable")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})
```
(Confirm `_active_install_root` / `Response` / `HTTPException` / `Query` are already imported in the file; reuse the existing active-install helper whichever it is.)

- [ ] **Step 7: Run the mapforge suite**

Run: `cd sidecar && ./.venv/Scripts/python.exe -m pytest tests/ -q -k "mapforge or soldier_sprite"`
Expected: PASS, no regression.

- [ ] **Step 8: Commit**

```bash
git add sidecar/mercwizard_core/mapforge_engine/soldier_sprite.py sidecar/mercwizard_core/mapforge_engine/appendix_extract.py sidecar/routes/mapforge.py sidecar/tests/test_soldier_sprite.py sidecar/tests/test_mapforge_appendix_extract.py
git commit -m "$(printf 'MapForge overlay: soldier body-sprite endpoint + body_type surfacing\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Frontend — render soldier sprites

**Files:**
- Modify: `frontend/src/lib/mapforge.ts` (add `body_type` to `AppendixSoldier`)
- Modify: `frontend/src/routes/MapForgeSector.tsx` (sprite cache + render)

**Interfaces:**
- Consumes: `AppendixSoldier` (now with `body_type`); the soldier markers in the overlay; `mediaUrl` (from `../lib/api`) to build the sprite URL.
- Produces: soldiers render as `<image>` sprites (anchored bottom-center at the tile), falling back to the existing team-colored circle while a sprite loads or is missing.

- [ ] **Step 1: TS type**

In `mapforge.ts`, add `body_type: number;` to the `AppendixSoldier` interface.

- [ ] **Step 2: Sprite cache (load + measure)**

In `MapForgeSector.tsx`, add a sprite-cache state keyed by `${body_type}-${facing}` holding `{ url: string; w: number; h: number }`. Build it with an effect that runs when `appendix?.soldiers` changes: dedupe the `(body_type, facing)` pairs, and for each not-yet-cached pair, resolve the URL via `mediaUrl(\`/mapforge/soldier-sprite?bodytype=${bt}&dir=${dir}\`)`, then load an `Image` to measure natural size; on load, store `{url, w: img.naturalWidth, h: img.naturalHeight}` in the cache (use a functional `setState` merge). On error, store a sentinel so it isn't retried and the circle fallback is used. Cancel-guard the effect (a `cancelled` flag) like the existing appendix fetch effect.

```tsx
const [spriteCache, setSpriteCache] = useState<Map<string, { url: string; w: number; h: number } | null>>(new Map());
useEffect(() => {
  if (!appendix || !showSoldiers) return;
  let cancelled = false;
  const pairs = new Map<string, { bt: number; dir: number }>();
  for (const s of appendix.soldiers) {
    const key = `${s.body_type}-${s.facing}`;
    if (!spriteCache.has(key)) pairs.set(key, { bt: s.body_type, dir: s.facing });
  }
  (async () => {
    for (const [key, { bt, dir }] of pairs) {
      try {
        const url = await mediaUrl(`/mapforge/soldier-sprite?bodytype=${bt}&dir=${dir}`);
        const img = new Image();
        await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(); img.src = url; });
        if (cancelled) return;
        setSpriteCache((m) => new Map(m).set(key, { url, w: img.naturalWidth, h: img.naturalHeight }));
      } catch {
        if (cancelled) return;
        setSpriteCache((m) => new Map(m).set(key, null));  // sentinel: fall back to circle
      }
    }
  })();
  return () => { cancelled = true; };
}, [appendix, showSoldiers]);
```
(Import `mediaUrl` from `../lib/api`. Thread `spriteCache` into the overlay component the SAME way `appendix`/`showSoldiers` are threaded, OR build the soldier markers in the parent where the cache lives — match the existing structure; if the markers live in a child overlay component, pass `spriteCache` as a prop.)

- [ ] **Step 3: Render the sprite (fallback to circle)**

Replace the existing soldier circle marker with: if a loaded sprite exists for `${s.body_type}-${s.facing}`, draw an `<image>` anchored so the sprite's bottom-center sits at the tile center (`x = cx - w/2`, `y = cy - h + meta.tileH/2` — the iso foot anchor; this offset may need a small eyeball tweak, note it); else draw the existing team-colored `<circle>`.

```tsx
{showSoldiers && appendix.soldiers.map((s, i) => {
  const { cx, cy } = c(s.x, s.y);
  const sprite = spriteCache.get(`${s.body_type}-${s.facing}`);
  if (sprite) {
    return <image key={`sol-${i}`} href={sprite.url} width={sprite.w} height={sprite.h}
      x={cx - sprite.w / 2} y={cy - sprite.h + meta.tileH / 2}
      style={{ imageRendering: "pixelated" }} />;
  }
  const color = s.team === 1 ? "rgba(255,80,80,0.95)" : s.team === 2 ? "rgba(120,255,120,0.95)"
    : s.team === 3 ? "rgba(80,220,255,0.95)" : s.team === 4 ? "rgba(120,160,255,0.95)" : "rgba(240,240,240,0.95)";
  return <circle key={`sol-${i}`} cx={cx} cy={cy} r={4} fill={color}
    stroke="rgba(0,0,0,0.7)" strokeWidth={1} vectorEffect="non-scaling-stroke">
    <title>{`${s.team_label} (body ${s.body_type}, dir ${s.facing})`}</title>
  </image>;
})}
```
(Keep the `<title>` on the circle path; an `<image>` can carry a `<title>` child too if desired.)

- [ ] **Step 4: Typecheck**

Run: `cd frontend && node node_modules/typescript/bin/tsc --noEmit`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/mapforge.ts frontend/src/routes/MapForgeSector.tsx
git commit -m "$(printf 'MapForge overlay: render NPC body sprites (fallback to team dot)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

- [ ] **Step 6: Manual verify (controller)**

Browser-dev recipe: open A6/A12 (basic soldiers) → NPCs render as standing merc sprites facing their direction; A2 (civilians) → civilian sprites. Confirm the foot anchor looks right (tweak `meta.tileH/2` offset if the sprites float/sink). The DOG/creature bodies (if any) render as creatures.

---

## Self-Review

**Spec coverage:** bodytype→STI table + fallback → Task 1 `soldier_sprite.py`; dir→frame `(dir+1)%8` → Task 1; loose-first→SLF resolution → Task 1; reuse `decode_subimage_to_rgba`/`load_8bit_sti`/`_open_slf_cached` → Task 1; endpoint → Task 1; body_type surfaced (extract + model + TS) → Task 1/2; frontend sprite render + circle fallback → Task 2; install-gated real-sprite tests → Task 1. ✓
**Placeholder scan:** none (the foot-anchor offset is an explicit "tune by eye" note, not a gap). ✓
**Type consistency:** `body_type` added to extractor dict + `AppendixSoldier` (py + ts); sprite endpoint params (`bodytype`,`dir`) match the frontend URL builder. ✓
