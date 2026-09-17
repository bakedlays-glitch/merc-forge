# Changelog

All notable changes to Merc Forge. This project follows a `v1.0.0-beta.N`
pre-release line; dates are ISO (YYYY-MM-DD).

## v1.0.0-beta.4 — 2026-09-16

202 commits since `v1.0.0-beta.3`, spanning 2026-06-28 → 2026-09-16. Three
subsystems arrive whole — Voice Lab, recruitable-RPC authoring, and MapForge's
mode-less placement model — alongside a codebase-wide review-hardening arc and
the crash-recovery work that closes the last data-loss hole in map editing.

### Added — Voice Lab

A full voicing workflow for a merc's speech lines, from inventory to a
reversible deploy. The bank inventory is VFS-aware, reading through the
install's mounted archives (deduplicated, SLF acceptance restricted to known
roots) rather than assuming loose files. A dialogue codec and a generated
voice-trigger catalog — derived from the engine headers and shipped as package
data — name what each line is for.

Editing runs through a line workbench: cut ranges, an imported replacement, a
rendered preview whose evidence is validated before it can be reviewed, and a
recipe whose rendering is reproducible from its inputs. Review state persists
across sessions, and audits are transcript-assisted, bounded, contextually
ordered and carry their own provenance.

Deployment is journalled and recoverable. Every deploy pins a journal with a
durability guarantee, Undo restores the previous audio, and a startup pass
recovers registered installs before any mutation route runs — so a crash
mid-deploy resolves coherently rather than leaving a half-written bank. Backup
and journal concurrency were hardened alongside it: backup identity locking,
restore serialized against backup mutations, and restore-cleanup targets
validated. Legacy voice mutation paths were retired and their refusal is
audited. FFmpeg is optional at startup; transcription workers run hidden.

### Added — recruitable RPC authoring

Author a recruitable NPC end to end: map placement, recruit dialogue, and the
fact triggers that gate it. Small-face coordinate overrides are written to
`RPCFacesSmall.xml`. Sparse dialogue editing compacts what the engine reads
without disturbing hand-authored neighbours.

### Added — MapForge mode-less placement

The sector editor loses its modal tool split. Sprites are selected directly, a
ghost arms for placement, and validity is checked locally as you hover —
footprint, occupancy, and a per-tile verdict where the worst verdict on any
covered tile wins. A sidecar **placement oracle** refines that judgement with
the engine's own category and tier tables, debounced, with an advisory tier and
a stated reason; it degrades to local-only when unavailable, logging the
transition once.

Built on top of it: a sprite clipboard (slice, paste, delete, move, cycle),
marquee enumeration, fence line-drag with topology-correct corner subs, a
StarCraft-style placement queue committed as one undo stroke, persisted control
groups with `Ctrl+N` save and `N` recall, and a command card for payload and
shape verbs. The previous tool bar remains available behind a `legacyTools`
setting. Playwright agendas cover the core, the oracle, and the queue.

### Added — MapForge crash-recovery autosave

Dirty session edits previously lived only in sidecar memory: the session journal
could reconnect to a still-running process, but a sidecar crash or power loss
lost everything since the last save. A background autosaver (30s default,
`MERCWIZARD_MAPFORGE_AUTOSAVE_SECS`) now serializes each changed dirty session
to a recovery `.dat` in its per-map backup folder (outside the install, atomic
write, consistency-checked). Opening a map with a differing snapshot offers
Restore / Discard (`POST /sessions/{id}/recovery`); restore swaps the in-memory
state and leaves the on-disk file untouched until save. Save and explicit close
clear the snapshot. The session's external-modification guard baseline
(`disk_baseline`) is tracked separately from the writer baseline
(`original_bytes`) so a restore can't corrupt the appendix or blind the guard.
Proven end-to-end against a hard-killed sidecar; pinned by
`tests/test_mapforge_autosave.py`.

Session ownership was tightened at the same time: a page reload reconnects to
its own dirty session instead of orphaning it, and a clean same-map session
yields to a new open.

### Added — cross-mod Background Library

A sidecar endpoint over a harvested union of every mod's `<BACKGROUND>` entries,
browsable and assignable into the active install, with the editor and the
library combined into one manager. Page tag queries are batched behind a single
XML parse.

### Added — engine-LUT socket smoothers

Four socket smoothers (`smooth_terrain` / `smooth_walls` / `smooth_water` /
`smooth_caves`), the `SOCKET_*` validator findings, and a `POST /appendix-model`
authoring endpoint. Proving the smoothers passes 8/8 via
`sidecar/test_socket_generators.py` against a live sidecar; the saved
`SOCKTEST.DAT` engine-load check remains a manual step. `set_appendix_model`
carries a reasoned backup-discipline allowlist entry — it mutates the in-memory
session model only, and disk writes still go through the guarded save paths.

### Added — FaceGear composite preview

An in-game composite preview with exact-layer targeting, so gear is judged as
the engine will draw it rather than as a flat overlay.

### Changed — MapForge editor

- Props are labelled by what they are rather than by STI filename, across a
  catalog grown to 203 registered sheets, 34 aliases and 36 custom prefixes
  covering the New Vegas and Fallout 2 families. The tile inspector names the
  painted frame, not the sheet, with slot/sub kept as secondary detail.
- Picking is sprite-aware, so a structure that merely looks multi-tile is
  selectable at a glance.
- Zoomed bigmaps get a full-resolution detail window; selecting a room now
  resets the previous detail window, zoom and pan.
- Grab-and-move: click an object and drag it, with the buddy shadow previewed
  during the move.
- Copy, move and delete carry a structure's shadow even when it is not the next
  slot, so a pasted object keeps it.
- `WASD` pans the camera; `Delete` / `Backspace` removes the pinned entry.
- Roster gains a sort control and keyboard grid navigation; native
  `confirm`/`prompt` are replaced by a themed dialog service; Edit reaches
  parity with Create; Delete and Export fold into the roster grid.

### Fixed — MapForge rendering

- **Bigmaps drew a stretched wedge** under the browser's silent
  drawing-buffer clamp. The viewport is now taken from the real drawing-buffer
  size with the backing store scaled to match.
- **Structures sorted against roofs by layer rather than position.** Depth for
  the struct/roof/onroof tier is now derived from the engine's own world-Y and
  wall-height terms, so a roof sits over its own wall and rows stay ordered.
- **Oversized atlases failed to upload** on large tilesets, exceeding the device
  texture limit. The GPU copy is repacked — whole sprites, no scaling, no
  splitting — leaving source pixels and editor metadata untouched.
- **One-pixel art vanished when zoomed out**: a full-map render rasterizes below
  1:1, where nearest sampling and a fixed alpha cut dropped chainlink fence
  wire. Mipmaps and a lower cut apply only while the buffer is downscaled;
  1:1 and zoomed-in stay crisp.
- **Sprites overhanging a region crop disappeared** at its edge — awnings,
  cooling towers, overhanging roofs. Entries whose projected rectangle reaches
  the crop are now kept without widening the tile ring or moving the camera.
- The atlas cache key includes the tileset fingerprint, plus loose sheet size
  and mtime, so a re-baked sheet can't be served stale.

### Fixed — correctness and data loss

- **MapForge second-save corruption:** `save_session` re-baselined
  `original_bytes` but not `parsed["appendix_offset"]`, so a second save after
  any size-changing edit sliced the appendix at a stale offset and corrupted the
  `.dat`.
- **Edit-form clobber:** window-focus refetch silently replaced in-progress merc
  edits with server state.
- **Backup holes:** partial-payload `PUT /merc`, standalone gear routes and
  voice-clip deletes wrote or deleted with no snapshot; voice deletes were
  unrecoverable. All now snapshot, enforced structurally by
  `tests/test_backup_discipline.py`, which matches snapshot calls in the AST
  rather than by source substring.
- **Crash-atomicity:** portrait STIs, `state.json`, the INI surgical writer and
  the Ja2.ini VFS apply were bare writes — now tmp+replace, with the VFS apply
  byte-faithful across cp1252 and EOL, locked, and snapshotted. A no-op VFS
  apply no longer rewrites Ja2.ini at all.
- **Engine-assert prevention:** `new-sector` stamps `ubMapVersion`; `inject-sub`
  refuses a JSD/STI frame-count mismatch; `set_room_id` is capped at
  `MAX_ROOMS`; legacy (<v7.0) exit-grid records read as 7 bytes (engine `2+5`),
  not 12, with a single owner for that record size; door records branch on the
  map's major version; the render bbox is clamped, which was an OOM.
- Local undo/redo rolls back when the backend sync fails; PNG ETags hash the
  full body rather than a 4 KB prefix and answer `If-None-Match`; the multi-tile
  structure identity and tileset art identity survive a corpus rebuild; merc
  biography text is normalized for the JA2 EDT font.
- **Generator parameters are clamped** to their declared bounds before a
  generator runs. A browser enforces an input's `min`/`max` on the stepper
  arrows only, so a typed value passed through unchanged — and since several
  generators turn a count into an attempt budget with a quadratic inner scan,
  an out-of-range value wedged the sidecar rather than producing a poor map.
  The edit-apply and placement-check bodies also carry a length cap, both
  being walked while the session lock is held.
- **Room ids no longer silently merge.** The "New room (N)" suggestion came
  from the file as it was on disk when the editor opened, so marking a second
  new room in one sitting reused N and folded both regions into one room.
- The palette's shadow-slot checkbox toggled a value the editor overrode
  whenever auto-pairing was off; it now reads as the forced-on setting it
  always was. Stored numeric editor settings are re-clamped on load.
- Validation gained corner-art findings, a generated-edits commit gate, a
  `ROOM_ID_SHARED` warning when one room id spans detached buildings, and a lint
  that refuses engine-invalid custom sheet registrations.

### Changed — Settings

The page is a composition of per-section components rather than one file,
with one line of explanation per control instead of a paragraph. Diagnostics
folds into About; Backups becomes a pointer that also names the 50-snapshot
prune. An install can now be removed, not just added — the confirm says that
nothing inside the game folder is touched. Warnings dismissed with "Don't
show again" can be restored. A folder under Program Files registers with a
warning rather than being refused, which had locked out the default Steam
location.

### Fixed — Settings correctness

Changing or adding an install reset only two queries, leaving the graphics
table below it, Backups, Voice Lab and the INI editor all serving the previous
install's data; both paths now reset the cache. A failed "Set active" reported
nothing. The Voice Lab save confirmation was global and never cleared. The "Re-scan" button, its empty-state text and the Hub tile all
described behaviour the app no longer has.

### Fixed — UI safety and feedback

The slot-lock guard fails **closed** while loading, and Delete and roster
Replace both consult it; `DELETE /merc/{slot}` gained a matching server-side
gate. A back-button dirty guard uses a popstate sentinel and pops it on disarm.
Create defers voice clips until the merc exists and re-checks slot occupancy
before the portrait compile writes STIs. Recompile refreshes previews. Hub,
Settings and FirstRun surface their errors. Rename Escape-cancel beats
blur-commit; a hotkey rebind steals its combo with an explicit notice instead of
silently shadowing the other action. The STI Viewer and SLF Extractor use
`formatApiError`.

### Removed

- The **Graphics stack** section is not exposed in this release. The component
  and the `/graphics` endpoints behind it remain in the tree, unreachable from
  the UI.
- The **Game Setup** flow is not exposed in this release. Its Hub tile, its
  once-per-install offer banner and the `/setup` route are gone; the flow was
  not ready to ship. The route component, its sidecar endpoints and the flow
  spec remain in the tree, unreachable, so the work is not lost.

### Security

- The packaged sidecar lifecycle is authenticated: the shell proves a
  CSPRNG-generated lifeline token over loopback, and the Python runtime exits
  when that connection closes rather than surviving as an orphan.
- The HTTP API **fails closed**. Started without `MERCWIZARD_TOKEN` it refuses
  to serve — 503 from the middleware and an outright exit from `main()` — rather
  than waving every request through. The sidecar executable ships inside the
  install directory, so a copy started outside the shell previously came up
  unauthenticated with write access to the user's game data. Deliberate
  unauthenticated runs set `MERCWIZARD_ALLOW_NO_AUTH=1`.
- The session token no longer travels in a URL. `<img>` / `<audio>` loads can't
  attach a header, so they carry a second per-launch secret that authorizes GET
  only, issued at `GET /auth/media-token`. A copy that reaches the WebView's
  disk cache or a development access log can re-read a portrait and nothing
  else, and it dies with the process.

### Changed — repository hygiene

- No absolute path to a game install, engine source tree or sibling checkout is
  committed anywhere. Tests and developer tools read them from the environment
  (`JA2_INSTALL`, `JA2_INSTALLS_DIR`, `JA2_SOURCE`, `JA2_OPEN_TOOLSET`,
  `MERCWIZARD_HEADLESS_COMPILER`, `MERCWIZARD_BG_LIBRARY_JSON`,
  `MERCWIZARD_PLACEMENT_DATA`, all documented in `DEVELOPER.md`) and skip
  cleanly when unset, so a fresh clone runs green with no local setup.
- Comments no longer carry authoring-process provenance — internal phase and
  task numbering, review identifiers, references to design documents absent from
  the tree, and dated session notes — across 125 files. Engine-truth
  comments, numbered steps that describe a function's own sequence, and
  references to documents that do ship were kept.
- Dead code removed (`MapForgeGeneratorPanel`, unused api exports, `BackupMode`,
  the legacy `/sector/edit-tile` endpoint, never-used server-side library
  filters). NDJSON stream readers deduped on both sides; the install and
  `Ja2Set.dat.xml` resolution loop deduped; Move and Duplicate share extracted
  helpers; the tile-inspector cluster was extracted from the 7.9k-line
  `MapForgeSector.tsx`.
- The frontend has tests (vitest, previously none), one release version is
  established across the shell, sidecar and manifest, a reproducible release
  gate was added, and pytest temporary files are isolated from the user's
  profile.

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
