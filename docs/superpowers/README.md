# Merc Forge implementation-plan index

The files in `plans/` are historical implementation plans, not the live backlog. Their unchecked task boxes record the intended build sequence; they were not maintained as completion ledgers after implementation landed. Do not interpret the 211 unchecked boxes as 211 open Merc Forge tasks.

Implementation evidence:

| Plan | Primary implementation commit |
| --- | --- |
| `2026-06-20-mapforge-soldier-overlay.md` | `e8df8cf` — detailed soldiers |
| `2026-06-20-mapforge-tactical-overlay.md` | `e8df8cf` — detailed tactical soldier work |
| `2026-06-21-item-editor-qol.md` | `21731f8` — item-editor quality-of-life work |
| `2026-06-21-item-editor-s1-s2.md` | `67a6c25` — item editor implementation |
| `2026-06-21-mapforge-doors-edges-overlay.md` | `efb9bf6` — doors and edges |
| `2026-06-21-mapforge-item-graphics.md` | `ddd47c3` — item graphics |
| `2026-06-21-mapforge-schedules-overlay.md` | `9eddba2` — schedules |
| `2026-06-21-mapforge-soldier-sprites.md` | `903a07a` — soldier sprites |
| Related world-item work | `5e19fa3` — world items |

Live defects and deferred features belong in [`../../KNOWN_ISSUES.md`](../../KNOWN_ISSUES.md). Project-level state belongs in the maintainer's own working notes, not here. Verify individual acceptance criteria against current code and tests when revisiting a plan; the commit mapping proves implementation landed, not that every historical checkbox still describes current behavior.
