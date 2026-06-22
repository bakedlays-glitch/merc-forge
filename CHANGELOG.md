# Changelog

All notable changes to Merc Forge. This project follows a `v1.0.0-beta.N`
pre-release line; dates are ISO (YYYY-MM-DD).

## v1.0.0-beta.3 — 2026-06-22

The big one: **MapForge**, a full in-app sector map editor — plus new
**Item** and **INI** editors, an AIM-style roster, and a security pass.

### MapForge — sector map editor (new)
- **Tile editing:** brush box with shapes, flood fill, keep-floor erase,
  selection verbs (Delete / Cut / Move), region copy-paste, symmetric
  undo/redo with a click-to-revert History panel, in-place sub-frame
  picker, persisted Recents/Favorites + number-key brushes, hover ghost.
- **Generators:** vanilla-faithful cliff / bank / escarpment generators
  with editable terrain heights; a **canon building library** that grafts
  real buildings from the game's own maps (StarCraft-style placement, auto
  room IDs); live ghost preview + presets.
- **Strategic layer:** sector-picker grid, minimap navigator, create /
  clone sectors ("new sector" + "save a copy as…"), per-tileset clipboard
  (copy here, paste in another sector), per-sector radar thumbnails.
- **Read-only tactical overlay:** renders a sector the way the game does —
  NPCs as their actual body sprites, world items as their BIGITEMS
  graphics, doors / exit grids / map edges / edgepoints, NPC schedule
  waypoints, lights, and team-colored markers.
- **Radar / minimap:** STI radar-map generator with in-UI thumbnails.
- **Safety:** pre-flight validator (dockable, click-to-highlight), session
  locks, transactional edits with rollback, backups kept out of `Maps/`,
  honest dirty-tracking + unsaved-edits guard and reconnect recovery.
- **Tileset viewer / browser** and a scripted demo runner.

### Item Editor (new)
- Browse and edit `Items.xml` in a card-grid browser that opens a
  dedicated edit screen; per-class (sister) stats by class index; re-point
  an item's BIGITEMS graphic; category tabs, enum dropdowns, and field
  definitions.

### INI Editor + Setup (new)
- Full INI editor backed by engine-derived schemas, with a game-status
  view and a graphics station; reusable INI presets; a guided first-run
  setup flow.

### Roster
- AIM-style BigFace portrait cards with a size slider and instant warm
  loading; decode-aware face fallback (fixes blocky portraits); NPC and
  vehicle big-faces resolved from `Faces.slf`; Type-color highlights;
  voice auto-namer; background / Type fixes.

### Merc editing
- cp1252-safe XML writers (accented merc names round-trip correctly);
  `.gap` lip-sync sidecars written on voice upload; growth-modifier tag
  fixes; engine-verified demographics enums; the Edit screen now shows all
  four face sizes plus the SmallFace animation frames.

### Stability
- Kills an orphaned sidecar on spawn timeout and shows a graceful dialog
  on spawn failure; frontend error-boundary reload + blob-leak fixes;
  faster load (animation STI loaded once, on-disk portrait-sheet cache).

### Security & hardening
- MapForge file endpoints are confined to the active install (no arbitrary
  file read/write); VFS path-join backstop; the merc-XML parser is hardened
  against XXE / entity-expansion; developer machine paths and PII were
  stripped from shipped schemas and demo scripts; unused JA2-derived sample
  tiles were removed. The sidecar binds localhost only, behind a per-launch
  token.

## v1.0.0-beta.2

Initial public beta line. See the GitHub release history for earlier notes.
