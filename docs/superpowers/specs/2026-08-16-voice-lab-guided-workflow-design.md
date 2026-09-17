# Voice Lab Guided Workflow Design

## Goal

Turn Voice Lab from a risk-dashboard prototype into a guided editor that lets a non-technical user choose a merc, analyze every line, understand a finding, edit the selected audio and subtitle, preview the exact result, and deploy it through the existing guarded transaction flow.

## Workflow

The page follows six real stages: choose merc, analyze lines, review an issue, edit, preview, deploy. Inventory is described as indexing rather than analysis. Selecting a merc never silently opens the first technical line; after analysis, the highest-risk unresolved finding opens automatically. Before analysis, the line browser remains usable and no arbitrary workbench is shown.

## Backend analysis boundary

`POST /api/v1/voice-lab/analyses` accepts one voice index and optional ordered line IDs. One serialized background job finds uncached audio transcripts, transcribes only those clips, then runs the existing deterministic and content audits. Progress distinguishes “Transcribing” from “Checking audio.” Cancellation may retain completed transcript cache entries but never publishes partial findings. Existing scan, transcription, and targeted-audit endpoints remain compatible.

## Line browser and language

The left rail contains searchable mercs and, for the selected merc, every indexed speech and battle line. A line row shows its plain family, line number or confirmed trigger description, and finding severity. Raw bank numbers, hashes, source layers, and trigger codes move behind an Advanced details disclosure. Unknown codes are described honestly as game identifiers with no confirmed meaning.

## Editing and drafts

Each line has a versioned local draft keyed by install, voice index, family, line ID, and original source hash. Cuts, chosen imported source metadata, and subtitle edits save immediately and restore after navigation or app restart. A changed live source invalidates the stale draft. Reset removes the draft. Preview is disabled until the source, cuts, or subtitle differs from the live baseline.

## Waveform interaction

The waveform is the page’s signature tape-splicing surface. Dragging creates a visible persistent selection; releasing the pointer accepts it as a cut. Accepted cuts remain visible with timestamp labels, can be selected, adjusted through start/end controls, removed, and undone. Clicking seeks when the pointer did not drag. A moving playhead follows original playback. Controls audition the selected region and the rendered result.

## Safety and compatibility

The existing recipe, preview, preflight, backup, deployment journal, recovery, and Undo paths remain the only writers. Analysis and packaged validation read the canonical game install but write only Merc Forge authoring/cache state. Deployment stays an explicit reviewed action.

## Verification

Tests cover selection normalization, line search and human labels, analysis sequencing/cancellation, draft invalidation and persistence, waveform drag/seek/playhead math, dirty-preview gating, and existing deploy/Undo compatibility. Final verification includes full frontend and sidecar suites, fresh PyInstaller and Tauri builds, packaged smoke testing, and read-only Sulik, King, and Tycho checks.
