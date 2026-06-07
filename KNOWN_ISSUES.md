# MercWizard 2 — Known Issues

Last reviewed: 2026-05-23.

This file tracks **currently open issues** — bugs the team has acknowledged but not yet fixed — and a separate "Deferred features" section for forward-looking work we've decided to skip for now. For the full history of the pre-distribution bug sweep (2026-05-13) and the audit + fix arcs that followed, see the git log and `DEVELOPER.md`'s "Version notes" section.

> If you're hitting a problem that isn't listed here, file it. If you're hitting one that IS listed here, the workaround (if any) is documented inline.

## Severity

- **High** — wrong behaviour in normal use; user-visible. Workaround documented.
- **Medium** — surfaces under uncommon conditions or specific mods.
- **Low** — diagnostic / polish; no correctness impact in the deployed threat model (single user, Windows-only, localhost-only).

---

## Open — Medium


## Open — Low

These don't affect correctness in the deployed threat model (single user, Windows-only, localhost-only). Documented for future work.

- **`_force_transparent_to_index_0` perf** — list zip; could be `numpy.where`. Runs in ~20 ms per call; not in any hot path.
- **FaceGear auto-positioning quality varies per merc** — the algorithm assumes the eye XML coord tracks the goggle bbox row across all mercs in the source install, but vanilla art was hand-positioned and not coord-aligned (Christine's goggles sit at row 14 vs Narg's at row 11 despite both having `usEyesY=10`). Auto-positioning lands the gear close-enough for ~80% of vanilla-style portraits; outliers may need the Upload PNG path. No automatic fine-tune offered today.
- ~~**Auto-position source-frame selection is naive**~~ — RESOLVED 2026-05-27 (see Resolved section). The source-picker abstraction was removed entirely; fine-tuning is now direct sOffsetX/sOffsetY editing via `POST /facegear/set-offset`. The auto_position route's source-merc override parameters were dropped — sidecar always uses first-non-empty as the starting point.
- ~~**Auto-position offset isn't editable in the UI after writing**~~ — RESOLVED 2026-05-27 (see Resolved section). The ±1px nudge arrow widget was added earlier; the remaining gap was that it only surfaced after a session-local Auto / nudge mutation. Fixed by extending `GET /facegear/overlay` to also return the frame's signed `offset_xy` and using it as the live-offset fallback in `FaceGearOverlayAuthor.tsx`.
- **Settings "Animation default" select is still disabled** — left over from the procedural-animation arc. The actual default behavior (skip mode unless explicit frames supplied) is hardcoded in Create.tsx. Could either be removed or repurposed to switch between skip + future template-overlay modes.

---

## Deferred features

Tracked here so they don't get forgotten between sessions. Not bugs — work the team has consciously decided to defer.

### Animation template-overlay library (low priority)

**Idea:** ship a small library of canonical eye/mouth overlay PNGs (closed eyelid, half-open mouth, etc.) in the sidecar. For each new merc, composite the templates at the merc's eye/mouth coords over the base portrait → those become the 7 animation sub-frames. Users get a real-looking blink/talk cycle without uploading any frames.

**Why deferred:** the current `animate_procedural` is placeholder-grade (vertical squash + skin-tone fill), not real animation. Real animation today only comes from `animate_explicit` (user-supplied frames). Template overlays would close that gap with a one-time art investment — author 7 generic eyelid/lip templates, sidecar composites them per merc.

**Caveats noted at decision time (2026-05-16):**
- Generic overlays look identical across mercs (no per-face character)
- Doesn't work for non-human portraits (super mutants, deathclaws, robots) — templates assume human-face geometry
- Mod-defined sub-frame sizes (Vengeance 31×13 / 32×21 vs vanilla 17×6 / 14×6) mean the template either ships in multiple sizes or scales — both have tradeoffs

**Where it would live:** new `sidecar/mercwizard_core/portrait/animate_template.py` next to the three existing modes. Templates as PNGs in `sidecar/mercwizard_core/portrait/templates/`. New Settings option "Animation default = Template / Skip" wired to the Create flow.

### MediaPipe-backed auto-detect for eye/mouth coords (low priority)

**Idea:** vision-detect the eye and mouth landmarks on the uploaded portrait so the user doesn't have to drag rectangles. Same library Headless_Compiler and legacy MercWizard use.

**Why deferred (2026-05-16):** MediaPipe adds ~50 MB to the sidecar EXE (26 MB → ~76 MB), tripling the installer size, for one feature (eye/mouth auto-placement) that has a free alternative (the drag-rect picker shipped in this session). Reconsider when multi-feature vision needs surface (auto-crop, frontal validation, FaceGear preview overlay).

### Painting overlays inside the wizard (low priority — upload is sufficient today)

**Idea:** HTML5 canvas inside the FaceGear authoring tab where the user can draw
or paint a hat/goggle directly instead of uploading a pre-made PNG. Brush tools,
eraser, palette picker.

**Why deferred:** the upload path covers the common case (users author overlays
in their preferred tool and drag the result in). A built-in painter is a real
UI undertaking — brushes, layers, undo, color picker — and not load-bearing
for the feature.

**~~Per-merc FaceGear overlay authoring~~** (resolved 2026-05-16. Plus
the engine-supported auto-positioning shortcut shipped in 2.0f — "Auto"
button copies a stock frame and shifts via the eye-coord delta).

**~~Bundle FaceGear preservation~~** (resolved 2026-05-16 — bundles now carry
`facegear/<stem>.png` per non-IMP STI; importer auto-mirrors to `_IMP.sti`
partners; sub-frame offsets ride in PNG `tEXt` metadata for full
round-trip; see [docs/WMERC_FORMAT.md](docs/WMERC_FORMAT.md)).

---

## Resolved (recent — keep for blame searches)

The following items from the 2026-05-13 bug sweep + 2026-05-15 audit have been fixed. Removing them entirely would lose the git-blame signal pointing at the fix commits.

- **Install + active install persistence to AppData** — `routes/state.py` has `_load_from_disk()` / `_save_to_disk()` / `_persistence_enabled()` (test-aware).
- **`backup.snapshot` tracking created files** — `BackupEntry.files_created` exists; `record_files_created` is called by `deploy_import` for the Step 7 rollback path.
- **Cross-mod schema warning surfacing** — `partial_failures` is emitted in the report and rendered in the Import page.
- **Voice upload uses slot_prefix layout when active** — `voice.add_clip_bytes` detects the flavor and rewrites the slot prefix to match the target.
- **Create / Edit hardcoded AimBioID=71** — server-side `aim_availability.compute_aim_bio_id` is canonical; frontend no longer sends a stub.
- **MERCBIOS routing for Type=2 expansion slots** — `inject/edt.py::route_bio` writes to MERCBIOS.EDT at `MercBioID × 1120` for every MERC bio. `MercEdt/<n>.EDT` is dead (engine doesn't read it for Type=2).
- **Eskimo blink preservation across .wmerc round-trip** — `bundle/export.py::_extract_sti_subframes_as_pngs` auto-extracts the 7 animation sub-frames + BigFace source from the source install's STIs; `import_.py` passes them to `compile_and_write_all`.
- **SmallFace sub-frame sizes mod-defined** — `verify_smallface_sti` accepts any consistent eye/mouth sizes; `animate_explicit.make_explicit_frames` picks target sizes from the first source per region.
- **Quantize index-0 reservation by construction** — `quantize_with_anchor` shifts indices +1; `quantize_against_palette` remaps opaque pixels at index 0 to nearest non-zero.
- **Union-palette quantize for SmallFace** — `_build_palette_source` composes base + all 7 anim frames so explicit-mode hand-painted colors survive.
- **Zip-slip in `_is_safe_arcname`** — boolean precedence bug fixed; `safe_write` also resolves the target and refuses paths outside the install root.
- **MercProfiles.xml scaffold uses `<MERCPROFILES>` + `<PROFILE>`** — was `<PROFILES>` + `<MERCPROFILE>`.
- **Scattered AIM slots tier bumped to VANILLA_OVERWRITE** — all 21 slots in `CANONICAL_AIM_BIO_IDS` (215, 223, 228, 230–243, 245–246, 248, 250–251) plus 165–168, 191, 195–198, 253. Each shares an AimBioID with a 1.13-expansion AIM merc; slot 215 specifically shares with vanilla Buns.
- **Atomic XML writes** — `inject/_atomic_xml.py::save_atomic` shared by all four XML injectors. Tempfile + os.replace; no more half-written XML if a write is interrupted.
- **API timeouts on `request<T>()`** — 30 s default; opt-in 5 min for move/import/export/backup/restore.
- **Blob URL leak in Create.tsx** — `useEffect` cleanup revokes `portraitUrl` on unmount.
- **Watchdog exponential backoff** — 4 s → 8 s → 16 s → 32 s → 60 s cap on consecutive `spawn_one` failures. Healthy spawn resets to base interval.
- **Slot 200–254 with `aim_bio_id` routes to AIMBIOS** — was silently falling through to NPCDATA.
- **Bio-id allocation moved inside `state.write_lock`** — was outside, TOCTOU race.
- **Audit-on-Edit (PUT /merc/{slot})** — was bypassing audit entirely.
- **`WmercManifest` root uses `extra="ignore"`** — was `extra="forbid"` which broke older binaries on every new optional field.
- **Backup coverage expanded** — `files_for_merc` now includes voice clips (vanilla + slot-prefix layouts), Battlesnds, NPC_Speech, snitch names, IMPFaces variants, camo face variants, BigItems for slot, and all six mod-specific XML tables.
- **`starting_gear.upsert` preserves unknown children** — was wiping all children of `<MERCGEAR>`, dropping mod-specific extras.
- **Double-submit guards** — Create/Move/Duplicate/Import buttons disable on `isSuccess` as well as `isPending`.
- **`SlotLockWarningModal` accessibility** — role/aria-modal/Esc handler/focus-on-Cancel.
- **Frontend `classifySlot` deduped** — single canonical implementation in `lib/slotClass.ts`.
- **Settings page dead-UI** — "Backup behavior" and "Animation default" selects now `disabled` with "coming soon" chips and accurate copy.
- **Sidecar respawn-orphan cascade** — `Arc<AtomicU16>` interior mutation + watchdog exponential backoff. Manual taskkill test no longer accumulates orphan processes.
- **`list_installs` register-during-scan race** — Bug #12 (commit `a7a0479`) removed the background install-detection probes entirely. The dict-swap race no longer exists because there's no concurrent scan to race against; registrations are only triggered by the user's manual folder-pick action and run in the request thread.
- **FaceGear orphan banner false positives + manual cleanup** — Bug #79 cross-references orphans against FaceGear.xml's `<szFile>` registry so unregistered leftovers (KGoggles-style) don't fire the banner. Bug #89 added a "Repair all" button that copies present STIs to their missing partners in one click.
- **Slot picker static range heuristics** — `slot_picker.py` reads AIMAvailability.xml + MercAvailability.xml live so categorization tracks the install's actual state instead of the legacy hardcoded 200–254 / scattered-AIM ranges. The classifySlot / SCATTERED_AIM_LIST UI strings have been replaced with engine-faithful copy.
- **Roster portrait thumbnails + BigFace sidebar** — Bug #86–#88 wired `GET /merc/{slot}/portrait` so the 256-slot grid renders SmallFace thumbnails per cell and the selection sidebar shows the BigFace. `?v=roster.dataUpdatedAt` cache-busts on roster invalidation so recompiles + replaces aren't masked by a stale browser cache.
- **`WmercManifest.exported_at` re-fires on parse** — `manifest.py:89` uses `Optional[str] = None`; export-side constructor (`export.py:530`) sets the timestamp explicitly. Bundle parsing no longer overwrites the source's timestamp.
- **Import partial_failures UX too quiet** — Bug-review #93. When `partial_failures.length > 0` the success banner flips green→yellow with "Some files were skipped" headline and inline likely-cause hints.
- **Portrait decoder duplicated between facegear + routes/merc** — Bug-review #95 extracted `decode_sti_frame_to_png` / `decode_subimage_to_rgba` into `mercwizard_core/sti_decode.py`; both call sites go through it.
- **MercAvailability writer corruption** — Bug-review #96. `upsert` re-parses + verifies the written row (ProfilId present, fields round-trip clean, total row count matches expected). Mismatches raise `MercAvailabilityWriteError`.
- **Dead routes deleted** — `routes/saves.py` (no frontend caller) and `GET /game/exe` (duplicated info already in `/installs/{id}` response) removed in bug-review #91.
- **Stalled "coming soon" Settings sections deleted** — Backup-behavior and Animation-default panels removed in bug-review #92; both had been disabled since 2026-05-16 with no roadmap.
- **Frontend build timestamp visible in Settings** — Bug-review #94. The About card now shows "Frontend built: <date> (N min ago)" + sidecar version, catching stale-exe / fresh-source mismatches at a glance.
- **VFS broken-config silent fallback** — Bug-review #98. `VfsLayout.resolve_write` now raises `VfsConfigError` when the install fell back to a legacy layout because the user's Ja2.ini VFS line couldn't be honored. Writes are refused rather than landing in `Data-1.13/` where the modded engine wouldn't see them.
- **encode_field silently mangles emoji / supplementary-plane chars** — Bug-review #99. New `find_unencodable_chars()` helper + `audit_merc` CONTAINS_UNENCODABLE warning surfaces every codepoint that would clamp to U+FFFE at save time. The clamp itself stays as a last-resort safety net, but the audit fires BEFORE the user commits so they can remove the offending characters first.
- **Defensive guards in palette quantize + profile XML format** — Bug-review #100. `quantize_with_anchor` now raises an `AssertionError` with a concrete message if PIL returns fewer than 255 palette entries (corner case for empty / single-color frames); `_format_value` keeps its bool branch with a documented-intent comment so future maintainers don't delete it as dead code.
- **Cross-process flock on shared EDTs** — `cross_lock.cross_process_install_lock` is already taken before every mutating route enters `state.write_lock` (see `routes/merc.py:114, 275, 561, 679, 774, 951`). Two MercForge instances on the same install serialize cleanly through a portalocker exclusive lock at `%APPDATA%\MercWizard\<install-id>\.write.lock`. Bug-review #101 confirmed coverage; doc entry was stale.
- **Nudge widget hidden for prior-session FaceGear edits** — fixed 2026-05-27. `GET /facegear/overlay` now returns the frame's signed `sOffsetX/sOffsetY` as `offset_xy`. `FaceGearOverlayAuthor.tsx` extends the `liveOffset` fallback chain to include `current.data?.offset_xy` and the widget visibility gate is simplified to `{liveOffset && (...)}`. Result: opening a merc whose FaceGear was authored last week now shows the ±1px nudge arrows immediately, without requiring an Auto / nudge action first. Out-of-range frames still hide the widget (preview returns `offset_xy=null`, nudge would fail with "face_index >= frame count"). Test coverage: `test_facegear_overlay_preview_returns_offset_xy_for_existing_frame` + `test_facegear_overlay_preview_returns_null_offset_for_out_of_range_frame` in `tests/test_routes.py`.
- **FaceGear source-picker UI replaced by direct coord editing** — 2026-05-27. The source-face number input next to each gear row's Auto button was removed entirely. In its place, the row's lower widget grew two editable X/Y inputs alongside the existing ±1 nudge arrows; typing a value commits via the new `POST /facegear/set-offset` route. The underlying primitive `set_overlay_offset` (header-only, pixel-preserving) mirrors `nudge_overlay_offset`'s pattern. `POST /facegear/auto-position` body simplified — `source_face_index`, `source_eye_x`, `source_eye_y` removed; sidecar always uses first-non-empty as the auto-pick starting point. Test coverage: `test_set_overlay_offset_writes_absolute_value` + `_does_not_modify_pixels` + `_rejects_int16_overflow` + `_rejects_out_of_range_frame` in `test_facegear.py`, and `test_facegear_set_offset_route_applies_and_backs_up` + `_mirrors_to_imp_partner` + `_rejects_int16_overflow` in `test_routes.py`.
