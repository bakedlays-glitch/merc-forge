# MercWizard RPC Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete MercWizard's recruitable-RPC workflow with correct talk-face assets, safe coordinate wiring, simple recruitment defaults, and an end-to-end readiness rail.

**Architecture:** Extend the shared portrait compiler with an optional RPC talk-face output and return both coordinate spaces. Keep game-data mutation in the existing backed-up API routes, then compose those APIs from Create/Edit. Add a read-only readiness endpoint and a small frontend status rail rather than embedding new soldier records in map files.

**Tech Stack:** Python 3.12, Pillow, ja2py/STCI, FastAPI/Pydantic, React 19, TypeScript, TanStack Query, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-rpc-workflow-design.md`

## Global Constraints

- The canonical game install remains the Copy install selected through MercWizard's install context.
- RPC profile coordinates use 90x100 space; `RPCFacesSmall.xml` keeps 48x43 coordinates.
- Every game-file mutation is backed up, install-locked, and atomic.
- Hand-authored Lua and unrepresentable `.NPC` logic remain protected.
- Direct map `.dat` soldier serialization is out of scope.

---

### Task 1: RPC talk-face compiler

**Files:**
- Create: `sidecar/mercwizard_core/portrait/talkface.py`
- Modify: `sidecar/mercwizard_core/portrait/sti.py`
- Test: `sidecar/tests/test_rpc_talkface.py`

**Interfaces:**
- Produces: `build_talkface(source_png_bytes, explicit_eye_pngs, explicit_mouth_pngs, small_eye_box, small_mouth_box) -> TalkFaceBuild`.
- Produces: `TalkFaceBuild(base, frames, eyes_xy, mouth_xy, animated_eyes, animated_mouth)`.
- Produces: `write_animated_face_sti(path, base, frames)` for any valid eight-frame face base.
- Produces: a route-ready talk-face build that shares the existing atomic STI writer.

- [x] **Step 1: Write failing tests** for 90x100 base size, eight frames, changed-region coordinate detection, no-animation fallback, and read-back STI structure.
- [x] **Step 2: Run** `python -m pytest tests/test_rpc_talkface.py -q` and confirm the imports/functions are missing.
- [x] **Step 3: Implement** center-cropped 90x100 variant comparison, padded bounding boxes, transparent changed-pixel frames, fallback coordinate mapping, and the generalized atomic animated-STI writer.
- [x] **Step 4: Expose** the talk-face builder/writer so the backed-up portrait route can add `faces/B<face>.sti` without changing the normal four-face compiler.
- [x] **Step 5: Run** the new tests plus existing portrait tests and confirm they pass.

### Task 2: Portrait API and backup boundary

**Files:**
- Modify: `sidecar/routes/portrait.py`
- Modify: `sidecar/mercwizard_core/install_context.py`
- Test: `sidecar/tests/test_rpc_talkface.py`

**Interfaces:**
- Accepts multipart field: `rpc_talkface: bool = false`.
- Returns: `talkface: {written, eyes_x, eyes_y, mouth_x, mouth_y, animated_eyes, animated_mouth} | null`.
- Produces: `InstallContext.rpc_talkface_path(face_index, for_write=...)`.

- [x] **Step 1: Add a failing route test** proving an RPC compile includes `B<face>.sti` in the pre-write snapshot and returns talk-face coordinate data.
- [x] **Step 2: Run** the focused route test and confirm it fails because the multipart field/result do not exist.
- [x] **Step 3: Implement** the optional request field, path helper, backup coverage, rollback coverage, and JSON result.
- [x] **Step 4: Run** focused and existing portrait/backup-discipline tests.

### Task 3: RPC readiness model

**Files:**
- Create: `sidecar/mercwizard_core/rpc_readiness.py`
- Modify: `sidecar/routes/rpc_dialogue.py`
- Modify: `frontend/src/lib/api.ts`
- Test: `sidecar/tests/test_rpc_readiness.py`

**Interfaces:**
- Produces: `inspect_rpc_readiness(ctx, profile) -> dict`.
- Adds: `GET /rpc/readiness/{profile}`.

- [x] **Step 1: Write failing tests** for missing/valid talkface, missing/satisfied recruit quote, managed/hand placement, duplicate placement, and optional post-recruit content.
- [x] **Step 2: Run** the tests and confirm they fail on the missing readiness module.
- [x] **Step 3: Implement** a read-only readiness inspector using existing placement/dialogue/small-face codecs and STI read-back metadata.
- [x] **Step 4: Expose** the endpoint and typed frontend API.
- [x] **Step 5: Run** readiness, RPC, and backup-discipline tests.

### Task 4: Guided Create/Edit wiring

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/routes/Create.tsx`
- Modify: `frontend/src/routes/Edit.tsx`
- Modify: `frontend/src/components/RpcDialogueTab.tsx`
- Create: `frontend/src/components/RpcReadinessRail.tsx`
- Test: `frontend/src/lib/__tests__/rpcWorkflow.test.ts`

**Interfaces:**
- `compilePortrait` sends `rpc_talkface=true` for Type 3 and returns talk-face coordinates.
- Create writes profile talk-face coordinates, then `RPCFacesSmall.xml` tactical coordinates.
- Edit portrait replacement performs the same coordinate synchronization after compilation.
- `RpcReadinessRail` consumes `getRpcReadiness` and navigates to `profile`, `portrait`, `placement`, or `recruitment` tabs.

- [x] **Step 1: Write failing frontend tests** for the always-available branch default, blank-quote detection, and readiness label mapping.
- [x] **Step 2: Run** the focused Vitest file and confirm the new behavior is absent.
- [x] **Step 3: Change** the default recruit branch to approach 4 with opinion 0 and add a blank-accept-quote warning.
- [x] **Step 4: Wire** Create and Edit portrait operations to build the talk face and synchronize both coordinate stores.
- [x] **Step 5: Add** the dossier-style readiness rail above RPC editor tabs, with required/advisory states and direct tab links.
- [x] **Step 6: Run** the focused frontend tests and TypeScript compile.

### Task 5: Integrated verification and packaging

**Files:**
- Modify only if verification exposes an RPC regression.

**Interfaces:**
- Consumes all earlier task outputs.

- [x] **Step 1: Run** all RPC and portrait sidecar tests.
- [x] **Step 2: Run** frontend Vitest; separate pre-existing unrelated failures from RPC failures.
- [x] **Step 3: Run** `npm run build` and produce a fresh Vite bundle.
- [x] **Step 4: Start** the source sidecar and Vite frontend, register/activate the canonical Copy install, and smoke-test the RPC tabs/readiness endpoint without mutating a live profile.
- [x] **Step 5: Build** the packaged sidecar and an isolated Tauri executable, verify its packaged sidecar, and retain the artifacts until the active prior build can be closed safely.
- [ ] **Step 6: Promote** the isolated executable into the normal release path, launch it, and record the final verification after the active prior build is closed.
- [x] **Step 7: Commit** only RPC-owned files, preserving unrelated working-tree changes.
