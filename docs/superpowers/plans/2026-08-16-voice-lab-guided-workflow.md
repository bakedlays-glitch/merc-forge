# Voice Lab Guided Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a guided, plain-language Voice Lab with automatic transcription-assisted analysis, complete line navigation, persistent editing drafts, and synchronized waveform playback.

**Architecture:** A serialized backend analysis job composes the existing transcript cache and audit engine. Focused frontend view-model modules own selection, labels, line filtering, draft persistence, and waveform math; React components render those tested behaviors while the existing deploy service remains unchanged.

**Tech Stack:** FastAPI/Pydantic/Python, React/TypeScript, TanStack Query, Vitest, pytest, FFmpeg, Tauri.

**Spec:** `docs/superpowers/specs/2026-08-16-voice-lab-guided-workflow-design.md`

## Global Constraints

- The canonical game install remains read-only during development and validation.
- Existing guarded recipe, preflight, backup, deploy, recovery, and Undo code remains authoritative.
- No Python, FFmpeg, or transcription environment packages are installed or changed.
- Existing unrelated main-checkout edits are preserved outside this branch.

---

### Task 1: Serialized analysis job

**Files:**
- Modify: `sidecar/mercwizard_core/voice_lab/jobs.py`
- Modify: `sidecar/routes/voice_lab.py`
- Test: `sidecar/tests/test_voice_lab_routes.py`
- Test: `sidecar/tests/test_voice_lab_jobs.py`

**Interfaces:**
- Consumes: inventory snapshots, `Transcriber`, transcript store, `VoiceLabService.audit_snapshot`.
- Produces: `POST /voice-lab/analyses` returning a `VoiceLabJob` with kind `analysis` and two-phase progress.

- [ ] Write tests proving missing transcripts run before audit, cached transcripts are skipped, cancellation publishes no partial findings, and analysis jobs serialize with transcription work.
- [ ] Run the focused tests and confirm they fail because the analysis contract is absent.
- [ ] Add the analysis request/work function and a named serialized job submission method.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Line navigation and plain-language view models

**Files:**
- Modify: `frontend/src/lib/voiceLabViewModel.ts`
- Modify: `frontend/src/lib/voiceLabSchema.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/__tests__/voiceLabViewModel.test.ts`
- Modify: `frontend/src/lib/__tests__/voiceLabSchema.test.ts`

**Interfaces:**
- Consumes: `VoiceBank`, `VoiceLine`, `VoiceFinding`, and analysis-job wire data.
- Produces: no-arbitrary-line selection, searchable line rows, confirmed human labels, risk targeting, dirty-draft comparison, and `startVoiceAnalysis`.

- [ ] Write tests for null initial line selection, highest-risk target selection, line search, trigger fallback language, and dirty-state comparison.
- [ ] Run the tests and confirm the new expectations fail against current behavior.
- [ ] Implement the pure view-model functions and typed API wrapper.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Durable per-line drafts

**Files:**
- Create: `frontend/src/lib/voiceLabDrafts.ts`
- Create: `frontend/src/lib/__tests__/voiceLabDrafts.test.ts`
- Modify: `frontend/src/components/voice-lab/LineWorkbench.tsx`

**Interfaces:**
- Produces: `loadVoiceLineDraft`, `saveVoiceLineDraft`, and `removeVoiceLineDraft`, keyed by install/line/source hash.

- [ ] Write tests proving save/reload, line isolation, malformed-data rejection, and source-hash invalidation.
- [ ] Run the test and confirm the draft module is missing.
- [ ] Implement the versioned storage boundary and wire autosave/reset into the workbench.
- [ ] Run the focused tests and confirm they pass.

### Task 4: Persistent waveform editing and playback

**Files:**
- Create: `frontend/src/lib/voiceWaveform.ts`
- Create: `frontend/src/lib/__tests__/voiceWaveform.test.ts`
- Modify: `frontend/src/components/voice-lab/WaveformEditor.tsx`
- Modify: `frontend/src/components/voice-lab/LineWorkbench.tsx`

**Interfaces:**
- Produces: coordinate/time conversion, drag classification, cut adjustment, seek/playhead state, selected-region audition, and preview playback.

- [ ] Write tests for drag release acceptance, reverse drags, click-to-seek, playhead position, and bounded cut adjustment.
- [ ] Run the tests and confirm the helpers/behavior are absent.
- [ ] Implement tested waveform math, persistent selection controls, audio event synchronization, and accessible playback controls.
- [ ] Run the focused tests and confirm they pass.

### Task 5: Guided Voice Lab page

**Files:**
- Modify: `frontend/src/routes/VoiceLab.tsx`
- Modify: `frontend/src/components/voice-lab/BankBrowser.tsx`
- Create: `frontend/src/components/voice-lab/LineBrowser.tsx`
- Modify: `frontend/src/components/voice-lab/LineWorkbench.tsx`
- Modify: `frontend/src/components/voice-lab/RiskQueue.tsx`

**Interfaces:**
- Consumes: analysis job, line view models, drafts, findings, existing deploy flow.
- Produces: Choose → Analyze → Review → Edit → Preview → Deploy UI and automatic highest-risk opening.

- [ ] Add component-facing tests for ordered workflow state, analysis completion targeting, every-line navigation, and preview disabled until dirty.
- [ ] Run the focused tests and confirm the old page violates the new contracts.
- [ ] Implement the guided layout, analysis progress/cancel controls, line browser, help text, transcript/subtitle comparison, and advanced technical disclosure.
- [ ] Run frontend tests, typecheck, and production build.

### Task 6: Packaged acceptance and integration

**Files:**
- Review only: all changed source and test files.

**Interfaces:**
- Produces: fresh sidecar and desktop binaries plus verification evidence; no game-file writes.

- [ ] Run the complete sidecar suite and complete frontend suite.
- [ ] Build PyInstaller sidecar, copy both required binary locations, and verify hashes/resources.
- [ ] Build the Tauri shell and verify its timestamp is newer than the frontend bundle.
- [ ] Smoke-test the packaged app with Sulik, King, and Tycho, checking line navigation, analysis, transcripts, cuts, playhead, draft restore, and preview without deployment.
- [ ] Recheck scoped canonical game hashes, review the diff, commit the branch, merge into main while preserving unrelated edits, and rerun merge-level checks.
