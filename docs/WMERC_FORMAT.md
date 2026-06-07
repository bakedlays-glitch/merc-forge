# `.wmerc` bundle format

A `.wmerc` file is a **ZIP archive** carrying everything needed to recreate one Jagged Alliance 2 1.13 mercenary in a target install — profile, gear, AIM/MERC website binding, EDT biography, portrait STIs, voice clips, signature-item STIs, mod-specific XML rows, and optional source PNGs. The format is the canonical interchange unit between MercWizard exports and imports, and between cross-install moves.

> File extension: `.wmerc`
> Container: standard ZIP (`PK\x03\x04` magic)
> Encoding: filenames are ASCII or UTF-8; payload files are bytes per their native format
> Schema authority: [`sidecar/mercwizard_core/bundle/manifest.py`](../sidecar/mercwizard_core/bundle/manifest.py) — Pydantic models

## Why a bundle (and not a recipe + paths)

Mercs in JA2 1.13 live in **at least six** loosely-coupled files (MercProfiles.xml, AIMAvailability.xml, MercAvailability.xml, MercStartingGear.xml, AIMBIOS.EDT or MERCBIOS.EDT, four to seven STIs at face_index, voice clips at voice_index, plus a long list of mod-only XML tables). A bundle captures the full closure so a recipient install with a different mod, different MercBioID layout, different audio convention can re-assemble the merc correctly. The importer rederives every install-coupled ID — `AimBioID`, `MercBioID`, voice-clip rename — against the target install, not against the source's values.

## Top-level layout

```text
<merc>.wmerc/
├── manifest.json          ← required; the source of truth
├── README.md              ← optional; human-readable description
├── portrait_source.png    ← optional; high-res RGBA, importer recompiles STIs
├── extreme_master.png     ← optional; alt portrait source
├── bigface_source.png     ← optional; alt portrait source
├── preview.png            ← optional; thumbnail for browsers
├── anim_eye_1.png         ← optional; explicit eye frames (skip-animation default)
├── anim_eye_2.png         ←   "
├── anim_mouth_1.png       ← optional; explicit mouth frames
├── anim_mouth_2.png       ←   "
├── anim_mouth_3.png       ←   "
├── voice/                 ← Speech/<voice_index>/<file>.wav|ogg clips
│   ├── MERC<slot>_001.wav
│   └── ...
├── audio/                 ← mod-specific audio buckets
│   ├── battlesnds/<slot>_<TAG>.ogg          ← combat shouts
│   ├── npc_speech/<slot>_<NNN>.ogg          ← Vengeance NPC-style dialogue
│   ├── snitch_names/<OTHER>_<slot>.ogg      ← other mercs naming THIS merc
│   └── snitch_names_alt/<OTHER>_<slot>.ogg  ← alt variants
├── raw_stis/              ← verbatim face STIs (fallback if no portrait_source.png)
│   ├── Faces/<face_index>.STI               ← SmallFace 48x43
│   ├── Faces/65Face/<face_index>.STI
│   ├── Faces/33Face/<face_index>.STI
│   ├── Faces/BigFaces/<face_index>.STI
│   ├── Faces/DESERTCAMO/<face_index>.STI    ← optional, mod-specific
│   ├── Faces/URBANCAMO/<face_index>.STI
│   ├── Faces/WOODCAMO/<face_index>.STI
│   └── IMPFaces/<face_index>.STI            ← optional
├── big_items/             ← signature item STIs (gun<slot>.sti, p1item<slot>.sti, etc.)
├── facegear/              ← per-merc FaceGear overlay frames (one PNG per Face_*.sti)
│   ├── Face_SunGoggles.png        ← bundle's view of source[face_index] from this STI
│   ├── Face_NVGoggles.png
│   └── Face_GasMask.png
├── npc_script/<slot>.EDT  ← per-slot NPC dialogue script
└── table_rows/            ← per-slot XML rows from mod-only tables
    ├── Backgrounds.xml
    ├── CivGroupNames.xml
    ├── FaceGear.xml
    ├── MercOpinions.xml
    └── MercQuote.xml
```

### Sample sizes (Vengeance Eskimo bundle, slot 218)

| Section | Files | Bytes |
|---|---:|---:|
| `manifest.json` | 1 | ~6 KB |
| `audio/` (Speech, Battlesnds, NPC_Speech, snitch) | 129 | ~1.9 MB |
| `raw_stis/` | 15 | ~170 KB |
| `big_items/` | 6 | ~14 KB |
| `npc_script/<slot>.EDT` | 1 | ~8 KB |
| `table_rows/` | 5 | ~8 KB |
| `portrait_source.png` | 1 | ~25 KB |
| **Total** | **159** | **~1.8 MB compressed** |

A minimal bundle is just `manifest.json` (a few KB). A complete Vengeance-flavor bundle is ~1.8 MB.

## `manifest.json` — the source of truth

The Pydantic model is `WmercManifest` in [`sidecar/mercwizard_core/bundle/manifest.py`](../sidecar/mercwizard_core/bundle/manifest.py). The root uses `extra="ignore"` — newer bundles with fields older sidecars don't recognize parse cleanly, with the unknown fields silently dropped. Nested models (`Merc`, `AimBinding`, `MercBinding`, `GearKit`, `WmercCompat`) keep `extra="forbid"` so typos in well-defined sub-schemas surface immediately. Optional top-level fields default to `None`/empty.

```jsonc
{
  "wmerc_version": 1,
  "tool": "MercWizard",
  "tool_version": "2.0.0",
  "exported_at": "2026-05-14T19:31:00+00:00",

  "author":   { "name": "modder", "contact": null },
  "license":  "CC-BY-SA",
  "notes":    "Vengeance Eskimo, 1:1 reproduction",

  // The merc itself — every MercProfiles.xml column the wizard supports
  "merc": {
    "uiIndex":      218,
    "ubFaceIndex":  218,
    "Type":         2,
    "zName":        "Amaqjuag 'Eskimo' Kuliktana",
    "zNickname":    "Eskimo",
    "usVoiceIndex": 218,
    "bStrength":    92, "bAgility": 51, "bDexterity": 86, "bWisdom": 66,
    "bMarksmanship": 55, "bMechanical": 15, "bExplosive": 7, "bMedical": 5,
    "biographyText":      "Amaqjuag (or 'Strong One' in his native tongue) is an expert tracker and hunter...",
    "additionalInfoText": "Due to a technical glitch Eskimo's appearance on our roster system was delayed...",
    // ...50+ more fields documented in models.py::Merc
  },

  // MercStartingGear kits — one entry per <KIT>
  "gear": [
    { "mGearKitName": "Standard", "mWeapon": 218, "mBig0": 71, "mAbsolutePrice": -1, ... }
  ],

  // AIM website binding (Type=1 only) — see "Binding remap on import" below
  "aim_binding": null,

  // M.E.R.C. website binding (Type=2 only) — see "Binding remap on import" below
  "merc_binding": {
    "uiIndex":      12,     // display position in M.E.R.C. roster (rederived on import)
    "Name":         "Eskimo",
    "Drunk":        0,
    "uiAlternateIndex": -1,
    "StartMercsAvailable": 1,
    "NewMercsAvailable":   0,
    "MercBioID":    47,     // offset into MERCBIOS.EDT (rederived on import)
    "ProfilId":     218,    // MercProfiles slot pointer (rewritten to target_slot)
    "usMoneyPaid":  0,
    "usDay":        0
  },

  // Portrait crop metadata — used by the compile pipeline if portrait_source.png
  // is bundled. Falls back to the merc's usEyesX/Y, usMouthX/Y if absent.
  "portrait": {
    "crop_box":   null,   // {x, y, w, h} in source-PNG coordinates
    "eye_box":    null,   // {x, y} in 48x43 SmallFace coordinates
    "mouth_box":  null    // {x, y} in 48x43 SmallFace coordinates
  },

  // Voice metadata — match against bundled voice/<name> clips
  "voice": {
    "voice_index": 218,
    "count":       60,
    "filenames":   ["218_001.ogg", "218_002.ogg", ...]
  },

  // Cross-mod compatibility hints
  "compat": {
    "intended_mod":        "any",       // "vanilla" | "wasteland" | "aimnas" | "wildfire" | "any"
    "intended_slot_range": "either",    // "aim" | "merc" | "either"
    "trait_system":        "NT",        // "NT" | "OT" | "either"
    "min_game_version":    "1.13"
  },

  // Source install's schema fingerprint — informs cross-mod diff warnings on import
  "schema_fingerprint": {
    "source_mod":            "Vengeance Reloaded",
    "source_vfs_config":     "vfs_config.Vengeance.ini",
    "source_install_path":   "C:\\Games\\JA2 1.13 Vengeance",
    "profile_fields":        ["bAgility", "bAttitude", "bBuddy1", ...],
    "has_bEvolution":        true,
    "has_fRegresses":        false,
    "has_usVoiceIndex":      true,
    "has_growth_modifiers":  false,
    "has_stomp_block":       true,
    "merc_opinions_format":  "dense",   // "sparse" (vanilla/AIMNAS) | "dense" (Vengeance/UC/AR)
    "extra_tables":          ["merc_opinions", "merc_quote", "merc_availability", "face_gear"]
  }
}
```

## Binding remap on import — the **load-bearing** rule

`aim_binding` and `merc_binding` are how the engine routes the merc's bio inside the shared EDT files. Their numeric fields are **install-specific** and the importer **must** rederive them against the target install.

### `aim_binding` → `AIMBIOS.EDT` at offset `AimBioID * 1120`

- Vanilla AIM slots 0-39: `AimBioID == uiIndex`.
- 1.13 expansion AIM (170-177, 186, 187, scattered 215+): `AimBioID` is **non-linear** and mod-allocated. Read it from the install's `AIMAvailability.xml`, not from the source's value.
- The compiler bug `compile_merc.py:670` writes at `uiIndex * 1120` instead of `AimBioID * 1120`. MercWizard fixes this — bundles capture the source-install value but the importer rederives.

### `merc_binding` → `MERCBIOS.EDT` at offset `MercBioID * 1120`

- Vanilla MERC slots 40-50: canonical `MercBioID = uiIndex - 40` (0-10).
- 1.13 expansion MERC (178-185, 188-199, 244, 247, 249, 252-253) and modded extensions: `MercBioID` is mod-allocated and `MERCBIOS.EDT` grows past vanilla's 11 records (Vengeance: 52 records / 58,240 bytes).
- **Bug fixed 2026-05-14**: MercWizard 1.x and early MercWizard 2 routed expansion MERC bios to `MercEdt/<uiIndex>.EDT`. **The engine never reads those files for Type=2 mercs.** It reads `MERCBIOS.EDT` for every MERC bio. Symptom: Eskimo's Speck-laptop bio rendered as "Herman Regents, probation officer in Chicago" — Turtle's text at the MERCBIOS record the engine actually consulted.

The fix is symmetric with the AIM fix: bundle the source-install row in `merc_binding`, then on import `compute_merc_bio_id(target_install, target_slot)` allocates the lowest free ID in [0, 199] (or reuses the existing row if one is already bound at that slot). The fresh `MercBioID` lands inside the target install's MERCBIOS.EDT (which may be shorter than the source's), avoiding past-EOF reads.

### What gets rewritten at import time

| Field | Source value | Import behavior |
|---|---|---|
| `merc.uiIndex` | source slot | rewritten to `target_slot` |
| `merc.ubFaceIndex` | source value | **preserved** — keeps the bundle's portrait at the same face index |
| `gear[*].mIndex` | source slot | rewritten to `target_slot` |
| `aim_binding.uiIndex` / `.ProfilId` | source slot | rewritten to `target_slot` |
| `aim_binding.AimBioID` | source-install allocation | **rederived** via `compute_aim_bio_id(target_aim_xml, target_slot)` |
| `merc_binding.ProfilId` | source slot | rewritten to `target_slot` |
| `merc_binding.MercBioID` | source-install allocation | **rederived** via `compute_merc_bio_id(target_merc_xml, target_slot)` |
| `merc_binding.uiIndex` | source display order | reuses existing row's value if `target_slot` already has a `<MERC>` row; else `compute_ui_index(target)` allocates a fresh one at the end |

## `table_rows/<filename>` — mod-specific XML extras

Per-slot rows from mod-only `TableData/*.xml` files (e.g. AIMNAS's `MercOpinions.xml`, Vengeance's `FaceGear.xml`). Each entry is a **single XML element fragment** (the `<NAME>...</NAME>` block) whose slot-key child has been left at the source slot's value. The importer:

1. Parses the fragment with `ElementTree`.
2. Rewrites the slot-key child (e.g. `<uiIndex>`, `<ProfilId>`) to `target_slot`.
3. Upserts into the target install's same-named XML file — if that file exists. Removes any pre-existing row for the same slot before appending.

If the target install lacks the table (vanilla 1.13 doesn't ship `MercOpinions.xml` or `FaceGear.xml`), the row is silently skipped with a `partial_failures` warning in the import report.

### Tables intentionally **not** bundled here

| File | Why excluded |
|---|---|
| `MercProfiles.xml` | Carried canonically by `manifest.merc` |
| `MercStartingGear.xml` | Carried canonically by `manifest.gear` |
| `AIMAvailability.xml` | Carried canonically by `manifest.aim_binding`; bundling clobbers the importer's `AimBioID` rederivation |
| `MercAvailability.xml` | Carried canonically by `manifest.merc_binding`; **bundling caused the Eskimo→Narg regression** (see "MERCBIOS routing bug" below) |
| `Vehicles.xml` | Not merc data — vehicles are slots 160-169 with engine-side state |

The importer's `_install_table_rows` has an explicit skip-list (`INTENTIONAL_SKIPS = {"AIMAvailability.xml", "MercAvailability.xml", "Vehicles.xml"}`) so older bundles that include these files don't trigger warnings or clobber canonical bindings.

## `audio/` — mod-flavored layouts

Vanilla 1.13 puts voice clips at `Speech/<voice_index>/<file>.wav`. Vengeance and other mods use a flatter `Speech/<slot>_<idx>.ogg` layout at the Speech root, plus extra dirs (`Battlesnds/`, `NPC_Speech/`, `Speech/snitch/names/`).

The bundle captures all of them as-is under `audio/<bucket>/<filename>`. The importer detects the target install's `flavor.voice_layout`:

- **vanilla "subdir"**: `voice/` clips land at `Speech/<voice_index>/`, slot tokens preserved.
- **"slot_prefix" (Vengeance)**: `audio/battlesnds/<source_slot>_X.ogg` is renamed to `<target_slot>_X.ogg` and dropped at `Speech/` root or the matching mod-specific dir.

The rename uses a regex that respects token boundaries (`(?<![A-Za-z0-9])<source_slot>(?![A-Za-z0-9])`) so embedded digits like `gun218.sti` aren't accidentally split.

## `portrait_source.png` and the STI pipeline

If `portrait_source.png` is present, the importer **recompiles** all four canonical STI sizes (SmallFace 48x43, 65Face 31x27, 33Face 15x14, BigFace 106x122) plus the 7 sub-frames required by SmallFace's 8-frame layout. The recompile path is `mercwizard_core/portrait/compile.py::compile_and_write_all`.

If `portrait_source.png` is absent, the importer falls back to `raw_stis/` — copying the source install's verbatim STIs into the target. Those preserve the original artist's exact pixel work but cannot be reformatted (the recompile pipeline applies the index-0 reservation and ja2py rawmode fix; raw STIs assume those rules already hold).

The recompile uses the merc's `usEyesX/Y` and `usMouthX/Y` from `manifest.merc` to position the eye and mouth sub-frames (the engine reads the same coords from `MercProfiles.xml` at render time — they must match).

### Optional richer authoring

A bundle may carry additional PNGs that override parts of the default recompile:

| PNG slot | What it overrides | Notes |
|---|---|---|
| `bigface_source.png` | The 106x122 BigFace source | When the artist authored the AIM/M.E.R.C. hero portrait with different framing/composition than the tight 48x43 face. Without it, BigFace is center-cropped from `portrait_source.png`. |
| `anim_eye_1.png` .. `anim_eye_4.png` | The 4 eye sub-frames in the SmallFace animation strip | Each may be the sub-frame at its native size (e.g. 17x6 vanilla / 31x13 Vengeance) OR a full-face 48x43 variant the wizard auto-crops at `eye_box`. Auto-pads if fewer than 4 are supplied: 1 source → all 4 slots; 2 sources → alternate; 3 sources → slot 3 duplicates slot 1 per the engine's hardware-dup convention. |
| `anim_mouth_1.png` .. `anim_mouth_3.png` | The 3 mouth sub-frames | Same coercion + auto-pad rules. |

The exporter auto-extracts these from the source install's existing SmallFace/BigFace STIs when present, so a round-trip preserves blink/talk animation losslessly. (Pre-fix the round-trip stripped frames 1-7 of SmallFace and re-cropped from the base → static merc.)

**Sub-frame sizes are mod-defined, not hardcoded.** The engine reads `usEyesWidth`/`Height` from the STI's per-frame ETRLE header (`Faces.cpp:480-481`). Vanilla 1.13 uses 17x6 eyes and 14x6 mouths; Vengeance Reloaded uses 31x13 and 32x21; future mods may use other sizes. The wizard accepts any consistent size: all 4 eye frames in a single SmallFace STI must share a size, and all 3 mouth frames must share a size, but the size itself is free.

### Union-palette quantize

SmallFace's 8 sub-frames share a single 255-color palette (the ETRLE-strip format requires it). Earlier compile passes built the shared palette from the base portrait only, which silently lost any color in the animation frames that didn't appear in the base. The current pipeline (`portrait/sti.py::_build_palette_source`) computes the shared palette from the **union** of base + all 7 anim frames, so hand-painted blink colors survive intact.

## `facegear/<sti_stem>.png` — per-merc FaceGear overlays

When the source merc has custom overlays in FaceGear STIs (e.g. a uniquely-painted
hat for their `ubFaceIndex` slot in `Face_SunGoggles.sti`), the exporter extracts
`frame[face_index]` from every non-IMP `Face_*.sti` in the source install and
bundles each as `facegear/<stem>.png`. The filename matches the source STI's
stem (without extension and without the `_IMP` suffix) so the importer can
locate the matching target STI by name.

**On import**, for each `facegear/<stem>.png` in the bundle:

1. The importer scans the target install's `Data*/faces/FACESGEAR/` for a
   non-IMP STI whose stem matches case-insensitively.
2. If found, the overlay is injected at the merc's `ubFaceIndex` via
   `inject_overlay` (which extends the STI with transparent placeholders
   first if `face_index >= frame_count`, then replaces frame[face_index]
   with the bundled PNG quantized against the STI's existing palette).
3. The matching `_IMP.sti` partner gets the same overlay so IMP-Type mercs
   render consistently.
4. If the target install lacks a matching STI (vanilla doesn't ship every
   mod's gear), the bundled overlay is silently skipped — `report.partial_failures`
   records "facegear overlay 'X.png' has no matching STI in target install".

**Color fidelity caveat**: the overlay is quantized against the target STI's
existing palette to preserve every OTHER merc's frame. New colors not in the
palette map to nearest-neighbors. FaceGear art is typically simple enough
(skin tones + accessory colors) that this looks fine, but a heavily-recolored
overlay imported into a vanilla-palette target can look "close to" but not
exactly like the source.

**Sub-frame offset preservation via PNG metadata**: the engine reads each
sub-frame's signed `sOffsetX/sOffsetY` and adds them to the bottom-anchored
blit position (vobject_blitters.cpp:319-320), so per-merc positioning lives
in the STI sub-frame header. To preserve those offsets across a `.wmerc`
round-trip without an extra sidecar file, `extract_overlay` embeds the
signed offsets as `mw2_offset_x` and `mw2_offset_y` PNG `tEXt` chunks.
`inject_overlay` reads them as the fallback when no explicit `offset_xy`
parameter is supplied. So a merc auto-positioned via the eye-coord delta
shortcut keeps the same on-screen position when their `.wmerc` is imported
into a different install.

**IMP variants aren't bundled separately.** They normally mirror the base
variant; the importer mirrors automatically on write. If you actually want
a separately-authored IMP overlay, edit it in-place on the target after
import via MercWizard's FaceGear authoring tab.

## `npc_script/<slot>.EDT`

Per-slot NPC dialogue script (`NPCData/<slot>.EDT`). The importer copies it verbatim to `NPCData/<target_slot>.EDT` in the target install. Only meaningful for slots that act as RPCs/NPCs in addition to or instead of AIM/MERC mercs.

## Versioning and forward compatibility

`wmerc_version` is currently `1`. The forward-compat policy is **tolerate-unknown-at-root, reject-unknown-in-nested**:

- The root `WmercManifest` uses `extra="ignore"`. A new optional top-level field added in a future release (e.g. a `voice_binding` analogue) parses cleanly on older sidecars — the unknown field is silently dropped. The known fields still bind the merc.
- Nested models (`Merc`, `AimBinding`, `MercBinding`, `GearKit`, `WmercCompat`, `WmercPortraitMeta`, `WmercVoiceMeta`, `WmercSchemaFingerprint`) use `extra="forbid"` so typos like `usEyeX` (vs `usEyesX`) surface as a validation error rather than getting silently ignored.

This policy was settled on 2026-05-14 after the Eskimo regression: every existing installer rejected the new `merc_binding` field with `extra_forbidden` because the root used `extra="forbid"`. The fix moved root validation to `ignore` so adding optional fields is no longer a breaking change.

If you want to omit `None`-valued optional fields entirely from the on-disk JSON (smaller diffs, no rendered `null`s), export with `model_dump(exclude_none=True)`. The official exporter currently does NOT do this — every field is serialized, including nulls.

## Security

The importer's `_is_safe_arcname` rejects any zip entry whose name:
- starts with `/` or `\` (absolute path)
- contains a `..` traversal segment
- has a Windows drive letter (`C:`)
- contains a NUL byte
- is empty

The importer extracts a fixed set of well-known names (`portrait_source.png`, `voice/<file>.wav`, `audio/*`, `raw_stis/*`, `table_rows/*`, `big_items/*`, `npc_script/<slot>.EDT`). It does **not** use the arcname directly as a disk path — files are routed by category prefix into install-context-resolved paths. The arcname check is defense-in-depth.

## Engine-truth references

- [`models.py`](../sidecar/mercwizard_core/models.py) — every Pydantic schema the bundle uses (Merc, Gear, GearKit, AimBinding, MercBinding)
