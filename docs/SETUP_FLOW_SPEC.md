# Setup Flow — Spec

Binding spec for the `/setup` route + `routes/setup.py` (MercForge UI
Phase 3, 2026-06-07). Owner decisions: build the full flow (MercForge may
become the player-facing launcher); resolution step detects cnc-ddraw and
edits `ddraw.ini` (the file that actually owns the window under the
golden config), falling back to `Ja2.ini` keys otherwise. Companion:
`INI_PRESETS_SPEC.md`.

## Anti-treadmill affordances (binding — the old launcher's wizard was
condemned for lacking all of these)

1. **Clickable step rail** showing all six steps; any step reachable in
   any order; current step marked.
2. **Skip = keep current value.** Every step's pre-selected option is
   "Keep current (<value>)" — skipping writes NOTHING for that step.
   Skip ≠ Next: a persistent "Close" exits to the Hub at any point.
3. **Nothing is written until Review.** Steps stage choices client-side;
   Review shows the full staged batch (changed vs kept per step), runs a
   dry_run, then ONE apply.
4. Neutral copy throughout; each step names the file it writes.

## Steps

| # | Step | Writes | Mechanism |
|---|---|---|---|
| 1 | Display | `ddraw.ini [ddraw] windowed / fullscreen / inject_resolution` when cnc-ddraw is detected (ddraw.dll + ddraw.ini present); else `Ja2.ini SCREEN_RESOLUTION` (hardcoded engine-verified codes 4,5,11,19,20,22,23,24 — the schema's list_values is empty, NOT derivable) + `SCREEN_MODE_WINDOWED` | direct INI write (ddraw.ini is outside the editor whitelist — setup module uses `surgical_upsert` explicitly, documented exemption) |
| 2 | Intro & UI | `Ja2.ini PLAY_INTRO`, `TOOLTIP_SCALE_FACTOR` | canon (Ja2.ini rule) |
| 3 | Difficulty | builtin preset `easier_combat` / `harder_combat` / keep | preset apply (override) |
| 4 | Quality of life | builtin preset `quality_of_life` / keep | preset apply |
| 5 | Graphics | golden-config deploy — OPTIONAL, never blocks: runtimes missing → status + "set up later in Settings → Graphics" note, default Skip | existing `/graphics/deploy` (own backup — see Undo note) |
| 6 | Review | summary per step: `changed (old → new)` or `kept` ; dry_run gate; Apply → Play button (`/game/launch`) | one batch via `/setup/apply` |

**Undo note (honest):** the INI batch (steps 1–4) = ONE backup snapshot.
Graphics deploy (step 5) = its own backup (separate route). Review's
completion text lists both backup ids. There is no single cross-route
undo; the UI must not claim one.

**Effect-timing note:** Review labels each staged change with when it
takes effect (`new game` / `relaunch`) from the preset/effect-timing
metadata. Display/ddraw changes apply on next launch.

## Endpoints

```
GET  /setup/state?install_id=
     → {display: {renderer: "cnc-ddraw"|"engine", windowed, resolution,
                  available_resolutions[. ddraw: free text 1280x720 style /
                  engine: the 8 verified codes]},
        intro: {play_intro, tooltip_scale},
        graphics: <existing /graphics/status payload>,
        offered: bool}
POST /setup/apply?install_id=&dry_run=
     body: {display?: {...}, intro?: {...}, preset_ids: ["builtin:..."]}
     → merges everything into one IniChange batch + ddraw surgical writes;
       one lock, one backup; dry_run returns the staged plan with current
       values (gates the Review screen's Apply).
POST /setup/offered?install_id=   → marks offered (app settings
     `setup_offered_installs: [install_id]`; SettingsPatch gains the field —
     the closed model silently drops unknown keys today)
```

## First-run offer

- Hub banner, LAST in the existing banner block (below VfsMismatch +
  FaceGearOrphan — those are higher severity). Shown when the active
  install's id is not in `setup_offered_installs`.
- Wording (neutral): **"New install registered."** "Optional setup can set
  display, difficulty, and graphics in one pass." [Run setup] [Dismiss]
- Dismiss and Run both mark offered (persisted; survives restarts).
  Accepted limitation: re-registering the same folder with a different
  vfs_config yields a new install id → one re-offer (documented, fine).
- Never auto-launches.
