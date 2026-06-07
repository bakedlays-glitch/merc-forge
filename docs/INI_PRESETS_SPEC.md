# INI Presets — Spec

Binding spec for `mercwizard_core/ini_presets.py` + `routes/ini_presets.py`
(MercForge UI Phase 3, 2026-06-07). Incorporates the adversarial-review
amendments (per-change targets, load-time coercion/rejection, dry-run
current values, atomic authoring writes). Companion: `SETUP_FLOW_SPEC.md`.

## Model

```jsonc
// One preset
{
  "id": "easier_combat",          // slug; wire ids are source-namespaced: "builtin:easier_combat" / "install:my_tuning"
  "name": "Easier combat",
  "description": "Lowers enemy accuracy, equipment quality, and damage resistance.",
  "default_target": "override",   // "override" | "canon"; per-change target overrides this
  "changes": [
    {
      "ini_file": "Ja2_Options.ini",
      "section": "Tactical Difficulty Settings",
      "key": "REGULAR_CTH_BONUS_PERCENT",
      "value": "-10",             // string, INI-verbatim; null + "delete": true removes the key
      "target": null              // null = inherit default_target
    }
  ]
}
```

### Load-time rules (enforced in `ini_presets.py`, surfaced as per-preset data)

1. **Ja2.ini keys → target coerced to `canon`** (the engine has no override
   mechanism for it). Coercion is recorded as an advisory note, not an error.
2. **AI.ini keys under effective target `override` → load ERROR**; the preset
   is returned with `apply_disabled: true` + the reason. (2-arg CIniReader,
   no Override hook; partial edits can crash PlanFactoryLibrary.)
3. Every change is validated via `validate_against_schema` (advisory
   warnings only — unknown keys are written verbatim; engine clamps
   out-of-range and logs).
4. **effect_timing** per change, derived from the engine-facts re-read table:
   `new_game` (default for all GameSettings INIs), `relaunch`
   (Item_Settings.ini, Mod_Settings.ini, AI.ini, Ja2.ini), and
   `savegame_risk: true` when the section matches `/system limit/i` or the
   schema description contains UNLOADABLE / NOT RECOMMENDED. The preset's
   rolled-up `effect_timing` = worst of its changes; UI must show it before
   apply ("Affects new games only — the current campaign is unchanged").
5. Corrupt/unparseable preset files: **skipped with a surfaced warning**,
   never fatal. Install file size cap: 512 KB.

## Storage

- **Builtin**: `mercwizard_core/data/ini_presets/*.json` (one preset per
  file), shipped in the package (`data/` rides the PyInstaller spec).
- **Install-local**: `<install root>/MercForgePresets.json` — a JSON array
  of presets. Engine-invisible (verified: no VFS/scanner glob matches it);
  NEVER placed inside Data-1.13/ or any VFS layer dir. Authoring writes go
  through `backup.write_bytes_atomic`, with a `backup.snapshot` of the file
  first when it exists.
- Same-id collisions: install presets **shadow** builtins in listings; the
  wire id's namespace disambiguates; DELETE on a `builtin:` id → 403.

## Endpoints

```
GET    /ini/presets?install_id=
       → {presets: [{...preset, source, wire_id, warnings[], apply_disabled,
                     effect_timing, savegame_risk}]}
POST   /ini/presets/apply?install_id=&dry_run=
       body: {id: "<wire id>"}
       → reuses the batch apply machinery (one lock, one backup, surgical
         writes). dry_run response includes per-change `current` value
         (server-side join against editor.effective) so the preview table
         renders current → new without client fan-out.
POST   /ini/presets?install_id=           (create; install-local only)
       body: {name, description, default_target, changes[]}
DELETE /ini/presets/{wire_id}?install_id= (install-local only; builtin → 403)
```

## Starter builtin presets (values justified against engine-mined ranges)

All keys: `Ja2_Options.ini [Tactical Difficulty Settings]` unless noted —
merge-registered, override-capable, effect_timing `new_game`, NOT
savegame-risk. Engine ranges from `GameSettings.cpp` loader mining
(see each schema entry's `engine.loader`).

**`easier_combat`** (target override):
| key | shipped | preset | engine range |
|---|---|---|---|
| ADMIN_CTH_BONUS_PERCENT | 0 | -15 | -100..500 |
| REGULAR_CTH_BONUS_PERCENT | 0 | -10 | -100..500 |
| ELITE_CTH_BONUS_PERCENT | 10 | 0 | -100..500 |
| ADMIN_EQUIPMENT_QUALITY_MODIFIER | 0 | -1 | -5..10 |
| REGULAR_EQUIPMENT_QUALITY_MODIFIER | 0 | -1 | -5..10 |
| ELITE_DAMAGE_RESISTANCE | 10 | 0 | -50..95 |

**`harder_combat`** (target override):
| key | shipped | preset | engine range |
|---|---|---|---|
| ADMIN_CTH_BONUS_PERCENT | 0 | 10 | -100..500 |
| REGULAR_CTH_BONUS_PERCENT | 0 | 15 | -100..500 |
| ELITE_CTH_BONUS_PERCENT | 10 | 25 | -100..500 |
| ADMIN_EQUIPMENT_QUALITY_MODIFIER | 0 | 1 | -5..10 |
| REGULAR_EQUIPMENT_QUALITY_MODIFIER | 0 | 1 | -5..10 |
| ELITE_EQUIPMENT_QUALITY_MODIFIER | 0 | 2 | -5..10 |
| REGULAR_DAMAGE_RESISTANCE | 0 | 5 | -50..95 |
| ELITE_DAMAGE_RESISTANCE | 10 | 20 | -50..95 |

**`quality_of_life`** (mixed targets — Ja2.ini keys auto-coerced to canon):
| ini | section/key | preset | note |
|---|---|---|---|
| Ja2.ini | Ja2 Settings/PLAY_INTRO | 0 | canon-coerced; relaunch timing |
| Ja2.ini | Ja2 Settings/TOOLTIP_SCALE_FACTOR | 150 | canon-coerced |

## UI (PresetsPanel — body-swap inside /ini-editor)

- Entered via a "Presets" button in the header row; replaces the body the
  same way search results do. Cross-file scope, independent of the
  selected file.
- List grouped Built-in / This install, each row: name, description,
  N changes, effect-timing note, warnings count. Neutral copy.
- Preview modal (dry_run): rows grouped by file; columns
  `section · key | current | new | note`. No-op rows greyed with note
  `no change`. Savegame-risk rows amber `Can invalidate existing saved
  games`. Apply via ConfirmModal (destructive when any risk row).
- **Save as preset**: Override mode only (Edit-INI diffs are
  reference-dependent, not portable). Greyed in Edit INI mode with the
  reason in the title attribute. Dialog: name, description, checklist of
  current overrides (default all checked) → POST create.
