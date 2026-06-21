# MapForge item-graphic rendering — feasibility research

**Question:** can the MapForge overlay render a placed world item's graphic (the way the
JA2 editor shows ground items) given we already parse each world item's `usItem`?

**Answer: YES — proven.** Decoded 2 real item BIGITEMS graphics from the live install to
recognizable PNGs (a FAMAS rifle and a leather jacket) using the *exact* existing sidecar
STI/SLF helpers. Recipe below is source-cited and matches the running soldier-sprite path.

All source line refs are
`C:\AI Projects\The Wasteland\Source Files\1.13 Source\source-master\…`.
Install probed = the canonical Copy:
`C:\Jagged Alliance 2\Jagged Alliance 2 Gold 1.13 Mod Prototype - Copy`.

---

## 1. usItem → (ubGraphicType, ubGraphicNum)

`Items.xml` (`<install>\Data-1.13\TableData\Items\Items.xml`) keys each `<ITEM>` by
`<uiIndex>` (== `usItem` == array index into the engine `Item[]` / `INVTYPE`). The two
graphic fields are children of `<ITEM>`:

| XML element | INVTYPE field | type | meaning |
|---|---|---|---|
| `<ubGraphicType>` | `INVTYPE::ubGraphicType` (`Tactical/Item Types.h:1205`, `UINT8`) | sheet selector | which item-sheet family |
| `<ubGraphicNum>`  | `INVTYPE::ubGraphicNum`  (`Tactical/Item Types.h:1151`, `UINT16`) | sub-image / file index within that family |

`ubGraphicType` is frequently **absent** in the XML (e.g. most guns) → it defaults to **0**.
`ubGraphicNum` absent → 0. Both observed in the live file (FAMAS uiIndex 24 has no
`<ubGraphicType>`; YF Hide uiIndex 192 has no `<ubGraphicNum>`).

**Existing loader to reuse / extend:** `parse_world_items.load_items_xml(path)` already
parses Items.xml — but only `{uiIndex: usItemClass}`. It does **not** capture the two
graphic fields. A new tiny loader (or a 2-line extension returning
`{uiIndex: (ubGraphicType, ubGraphicNum)}`) is needed; same `ET.parse` + `findtext`
pattern, same graceful-empty-dict-on-failure contract. `mapforge.py` already knows the
Items.xml path resolution for the appendix parser.

---

## 2. ubGraphicType → STI sheet + the frame rule

There are **two** parallel item-graphic systems in the engine. Both key off the same
`(ubGraphicType, ubGraphicNum)` pair; they differ in packaging.

### (A) BIGITEMS — one STI **file per item** (RECOMMENDED for the overlay)

`LoadTileGraphicForItem` (`Tactical/Interface Items.cpp:10361-10414`) builds a filename:

```c
if (ubGraphicType == 0)  sprintf(zName, ubGraphic<10 ? "gun0%d" : "gun%d", ubGraphic);
else                     sprintf(zName, ubGraphic<10 ? "p%ditem0%d":"p%ditem%d", ubGraphicType, ubGraphic);
sprintf(ImageFile, "BIGITEMS\\%s.sti", zName);   // ubGraphic == ubGraphicNum
```

So the path is **`BIGITEMS\<stem>.sti`**, one image per file:

| ubGraphicType | filename stem | example |
|---|---|---|
| 0 | `gun<ubGraphicNum>` (zero-pad to 2 only when <10) | `gun24.sti`, `gun09.sti` |
| N≥1 | `p<N>item<ubGraphicNum>` (same pad rule) | `p1item96.sti`, `p2item13.sti`, `p1item05.sti` |

**Frame rule: each BIGITEMS STI is a single-image STI → always decode frame 0.** The item
identity is encoded in the *filename*, not a sub-image index. (Confirmed: both probe files
report `subimages=1`.) Padding is 2-digit only for values <10; `ubGraphicNum=24` → `gun24`,
NOT `gun024`. Verified against the loose `BigItems/` dir (`P1ITEM96.STI` exists, not
`P1ITEM096.STI`).

This same function is what the engine uses for **ground items** — the in-game tile graphic
goes through `GetTileGraphicForItem` → tile-data slot whose art was loaded by
`LoadTileGraphicForItem` from BIGITEMS (`Handle Items.cpp:2870`, `physics.cpp:1371`). So the
BIGITEMS picture **is** the editor's ground-item graphic, and it's the most recognizable.

### (B) INTERFACE sheets — multi-frame icon sheets (alternative, more complex)

`GetInterfaceGraphicForItem` (`Interface Items.cpp:10303-10321`) +
`RegisterItemImages` (`Tactical/InterfaceItemImages.cpp:98-133`):

| ubGraphicType | loose sheet (Data-1.13\INTERFACE) | frame = ? |
|---|---|---|
| 0 | `mdguns.sti` (`guiGUNSM`) | sub-image index == `ubGraphicNum` |
| N≥1 | `mdp<N>items.sti` (`guiPITEMS[N-1]`) | sub-image index == `ubGraphicNum` |

Here `ubGraphicNum` **is** the sub-image/frame index into one big sheet (`mdguns.sti` =
430 subimages, `mdp1items.sti` = 935, `mdp2items.sti` = 257 — verified). Functional, but you
must load+decode a large multi-frame STI per render and index a frame, vs. one tiny
single-image file with BIGITEMS. The BIGITEMS small-pic and the INTERFACE icon are the same
artwork at different sizes; BIGITEMS is the cleaner resolve.

---

## 3. Asset resolution (which SLF/loose, order, names)

Resolution mirrors the soldier-sprite VFS order (loose mod-override first, SLF fallback):

1. `…\Data-1.13\BigItems\<stem>.STI`  ← mod-override layer, case-insensitive. **Present and
   populated** (1696 loose files).
2. `…\Data\BigItems\<stem>.STI`        ← stock loose (1211 files). Present.
3. `…\Data\Bigitems.slf` member `BIGITEMS\<stem>.STI` ← SLF fallback, via
   `install_context._open_slf_cached` + basename match (same code path as
   `vehicle_icon_bytes` / face SLF probe). Present.

Interface sheets (path B), if ever used, resolve the same way: loose
`Data-1.13\INTERFACE\mdguns.sti` / `mdp<N>items.sti` (present: `MDGUNS.sti`, `MDP1ITEMS.sti`,
`MDP2ITEMS.STI`, `MDP3ITEMS.STI`) then `Data\Interface.slf`.

**Helpers to reuse (all already used by the soldier-sprite endpoint):**
- `ja2py.fileformats.Sti.is_8bit_sti` / `load_8bit_sti` — sniff + load.
- `mercwizard_core.sti_decode.decode_sti_frame_to_png(source, frame_index)` — accepts raw
  bytes OR a path, handles 8-bit (palette-resolved) and 16-bit; returns PNG bytes or None.
  For BIGITEMS just call with `frame_index=0`.
- `mercwizard_core.install_context._open_slf_cached(slf_path)` — cached SlfFS for the SLF
  fallback.
- A new resolver should copy the loose-first / case-insensitive-walk / SLF-fallback shape of
  `soldier_sprite._resolve_anim_sti_bytes` (just point it at `BigItems` / `Bigitems.slf`).

---

## 4. PROOF — 2 items decoded from the live install

Probe script: `.superpowers/sdd/item-probe/probe.py` (throwaway, gitignored). Run with the
sidecar venv (`sidecar\.venv\Scripts\python.exe`). Output:

| usItem | name | ubGraphicType | ubGraphicNum | resolved file | PNG | dims | opaque | looks like |
|---|---|---|---|---|---|---|---|---|
| 24 | FAMAS | 0 (absent→0) | 24 | `Data-1.13\BigItems\GUN24.STI` | `famas_gun24_v101.png` | 116×46 | 42% | **a bullpup assault rifle** ✓ |
| 188 | Leather Jacket | 1 | 96 | `Data-1.13\BigItems\P1ITEM96.STI` | `leatherjacket_p1item96_v101.png` | 56×48 | 69% | **a leather jacket** ✓ |

Both are 8-bit single-image STIs, decoded at frame 0, palette-resolved with the same
`decode_sti_frame_to_png` the roster/FaceGear/soldier-sprite use. Visually confirmed
recognizable. The `(ubGraphicType, ubGraphicNum) → filename` rule landed the correct item on
the **first** try for both — no frame-index hunting needed (BIGITEMS = 1 image/file).

---

## 5. Recommended endpoint design

Mirror the soldier-sprite endpoint exactly (`routes/mapforge.py:5927`):

```
GET /mapforge/item-graphic?item=<usItem>   →  image/png
```

- Handler: `root = _active_install_root()` (400 if none) → new
  `render_item_graphic(str(root), usItem)` in a new
  `mercwizard_core/mapforge_engine/item_graphic.py` → `Response(png, media_type="image/png",
  headers={"Cache-Control": "public, max-age=86400"})`, 404 when unavailable.
- `render_item_graphic`: (1) parse Items.xml once → `(ubGraphicType, ubGraphicNum)` for
  `usItem` (cache the dict by Items.xml mtime, like the SLF cache); (2) build the BIGITEMS
  stem via the pad rule; (3) loose-first→SLF resolve to bytes; (4)
  `decode_sti_frame_to_png(bytes, 0)`.
- **Cache key:** `(install_root, usItem)` plus the resolved file's mtime/size (the existing
  endpoints already fingerprint by mtime). PNGs are tiny; an in-process LRU on
  `(root, usItem)` is plenty, or just rely on the HTTP `max-age` like soldier-sprite does.
- **Fallbacks:** unmapped `usItem` (not in Items.xml) or missing STI → 404, and the overlay
  keeps the current dot. Optionally a generic "crate/box" placeholder STI; not required.

---

## 6. Honest gaps + SHIP / SKIP

**Gaps / caveats (all minor):**
- New code needed: a graphic-field Items.xml loader (existing `load_items_xml` only reads
  `usItemClass`) and the BIGITEMS resolver. Both are ~30 lines copied from existing patterns;
  no new deps, no engine changes.
- Padding edge case: zero-pad to 2 digits **only** when value <10 (`gun09`, `gun24`,
  `p1item05`, `p1item96`). Easy to get wrong — encoded in the proof script and table above.
- A handful of items have no `<ubGraphicType>`/`<ubGraphicNum>` (default 0/0 = `gun00`) or
  point at a stub graphic — fine, just resolves to whatever `gun00.sti` is, or 404s.
- Items are **sparse** on stock maps (typically 1–3 per sector) and the world-item Path A
  (modern recursive) parser is not yet implemented — only legacy Path-B maps currently expose
  `usItem` reliably. So the feature only lights up where items are already parsed.
- BIGITEMS pics are inventory-scale (≈40–120 px wide), larger than a tile; the overlay would
  render them centered on the gridno (down-scaled or as a hover/click popover), not as a
  pixel-perfect ground sprite.

**Recommendation: SHIP — small, but real and cheap.** Feasibility is proven and the cost is
low (two ~30-line helpers + one endpoint cloned from soldier-sprite, reusing 100% of the STI
pipeline). The payoff is concrete: instead of an anonymous dot, the user sees *that's a rifle,
that's a jacket* — genuinely useful for an editor. Because items are sparse, the render budget
is trivial (a few small PNGs per sector). **Render the BIGITEMS graphic** (`gun*/p*item*`,
frame 0) — it's the same art the engine puts on the ground and the most recognizable. Suggested
UX: keep the dot as the placement marker, show the decoded item PNG on hover or as a small
icon when zoomed in, to avoid clutter when items overlap. Skip the multi-frame INTERFACE-sheet
path — it's strictly more work for the same picture.
