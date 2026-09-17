# MercForge Full-Review Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair every confirmed blocker from the 2026-08-29 MercForge review and produce a green, freshly packaged build without touching live game data.

**Architecture:** Centralize target-derived engine contracts in the Python sidecar, serialize MapForge writes at the install boundary, make sidecar readiness an authenticated condition, and enforce destructive invariants in both API and shared UI primitives. Existing WIP is preserved by path-scoped snapshots and reviews rather than catch-all commits.

**Tech Stack:** Python 3.12/FastAPI/Pydantic/pytest, React 19/TypeScript/Vitest/TanStack Query, Rust/Tauri/Tokio/Reqwest, PowerShell, NSIS.

**Spec:** `docs/superpowers/specs/2026-08-29-mercforge-review-repair-design.md`

## Global Constraints

- Operate in `<your checkout>\MercWizard2`, its nested `main` branch, preserving unrelated dirty work.
- Do not write to either JA2 install and do not stop the running MercForge instance.
- Use failing behavior tests before production changes.
- Use shared atomic writers and cross-process install locks for game-adjacent writes.
- Never terminate Windows processes by image name.
- Review each task from a pre-task file snapshot because commits would capture unrelated edits in already-dirty files.

---

### Task 1: Target-aware body-type registry and editor options

**Files:**
- Create: `sidecar/mercwizard_core/body_types.py`
- Modify: `sidecar/mercwizard_core/audit.py`
- Modify: `sidecar/routes/merc.py`
- Modify: `sidecar/routes/portrait.py`
- Modify: `sidecar/mercwizard_core/bundle/import_.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/forms/DemographicsForm.tsx`
- Test: `sidecar/tests/test_body_types.py`
- Test: `sidecar/tests/test_audit.py`
- Test: `sidecar/tests/test_bundle.py`
- Test: `frontend/src/lib/__tests__/bodyTypes.test.ts`

**Interfaces:**
- Produces: `body_types_for_install(install_root: Path) -> BodyTypeRegistry`.
- Produces: `audit_full` gains keyword-only `body_types: Mapping[int, BodyTypeDef] | None = None`.
- Produces: `GET /api/v1/merc/body-types` returning `{mod_id, source, options:[{id,name,sex,category}]}`.
- Consumes: `detect_mod(install_root)` and the target `MercProfiles.xml` for unknown-mod observed extensions.

- [ ] **Step 1: Write failing backend registry and audit tests**

```python
def test_wasteland_registry_accepts_custom_ids_but_not_sentinel(wasteland_install):
    registry = body_types_for_install(wasteland_install)
    assert registry.options[29].name == "DOG"
    assert registry.options[43].name == "SILENTBOB"
    assert 44 not in registry.options

def test_wasteland_audit_accepts_marcus_and_rejects_totalbodytypes(wasteland_registry):
    assert not _issues_for(Merc(ubBodyType=41), wasteland_registry, "BODY_TYPE_UNKNOWN")
    assert _issues_for(Merc(ubBodyType=44), wasteland_registry, "BODY_TYPE_UNKNOWN")
```

- [ ] **Step 2: Run the focused backend tests and verify RED**

Run: `sidecar/.venv/Scripts/python.exe -m pytest sidecar/tests/test_body_types.py sidecar/tests/test_audit.py -q --basetemp sidecar/.pytest-body-types-red`

Expected: FAIL because `body_types_for_install` and registry-aware audit do not exist.

- [ ] **Step 3: Implement the registry and thread it through all write/import audits**

```python
@dataclass(frozen=True)
class BodyTypeDef:
    id: int
    name: str
    sex: str | None
    category: str

def body_types_for_install(install_root: Path) -> BodyTypeRegistry:
    mod = detect_mod(install_root)
    if mod.id is ModId.WASTELAND:
        return BodyTypeRegistry(mod.id.value, "engine-known", WASTELAND_BODY_TYPES)
    if mod.id is ModId.VANILLA:
        return BodyTypeRegistry(mod.id.value, "engine-known", VANILLA_BODY_TYPES)
    return registry_with_observed_extensions(install_root, mod)
```

- [ ] **Step 4: Run backend tests and verify GREEN**

Run the focused command from Step 2 plus `sidecar/tests/test_bundle.py` and the route tests that cover merc/portrait audit responses.

- [ ] **Step 5: Write failing frontend option-model test**

```typescript
it("keeps the current observed custom option while target options load", () => {
  expect(mergeBodyTypeOptions([], 41)).toEqual([{ id: 41, name: "Custom 41" }]);
});
```

- [ ] **Step 6: Run the frontend test and verify RED**

Run: `npm test -- --run src/lib/__tests__/bodyTypes.test.ts`

- [ ] **Step 7: Implement the API client, query, and target-derived select**

The form must render API options, append only the current value when absent, and show provenance/error copy without disabling unrelated fields.

- [ ] **Step 8: Run focused frontend tests, typecheck, and self-review**

Run: `npm test -- --run src/lib/__tests__/bodyTypes.test.ts && npm run typecheck`.

---

### Task 2: MapForge serialization, validation, atomic extraction, and close safety

**Files:**
- Modify: `sidecar/routes/mapforge.py`
- Modify: `sidecar/mercwizard_core/mapforge_engine/appendix_writer.py`
- Test: `sidecar/tests/test_mapforge_save.py`
- Test: `sidecar/tests/test_mapforge_appendix.py`
- Test: `sidecar/tests/test_mapforge_autosave.py`
- Test: `sidecar/tests/test_mapforge_path_confinement.py`

**Interfaces:**
- `MapForgeSession.install_id: str` is captured at open time.
- Normal save lock order is cross-process install lock, `state.write_lock`, then `sess._lock`.
- The existing `pack_map_tail` `map_version: int` parameter raises `ValueError` unless `15 <= map_version <= 255`.
- `MapForgeSession.closed: bool` prevents recovery writes after explicit close.

- [ ] **Step 1: Write four focused failing tests**

```python
def test_same_target_sessions_serialize_guard_and_second_save_conflicts(tmp_path, monkeypatch):
    first = _real_session(tmp_path, "first-same-target")
    second = MapForgeSession(first.dat_path, first.xml_path, first.tileset)
    second.id = "second-same-target"
    second.install_id = first.install_id
    second.parsed["heights"][0] = 160
    second.dirty = True
    second.mutation_seq = 1
    _session_store._sessions[second.id] = second
    first.parsed["heights"][0] = 80
    first_entered = threading.Event()
    release_first = threading.Event()
    real_write = mapforge.write_bytes_atomic
    call_count = 0

    def pause_first_write(path, data):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_entered.set()
            assert release_first.wait(5)
        real_write(path, data)

    monkeypatch.setattr(mapforge, "write_bytes_atomic", pause_first_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(save_session, first.id)
        assert first_entered.wait(5)
        second_future = pool.submit(save_session, second.id)
        release_first.set()
        first_future.result(timeout=5)
        with pytest.raises(HTTPException) as exc:
            second_future.result(timeout=5)
    assert exc.value.detail["error"] == "EXTERNAL_MODIFICATION"
    assert parse_dat_full(first.dat_path.read_bytes(), str(first.dat_path))["heights"][0] == 80

def test_appendix_model_rejects_map_version_14_without_mutating_session(client, open_session):
    session_id, session = open_session()
    before = session.mutation_seq
    response = client.post(
        f"/api/v1/mapforge/sessions/{session_id}/appendix-model",
        json={"tail": {"map_version": 14}},
    )
    assert response.status_code == 422
    assert session.mutation_seq == before

def test_pack_map_tail_rejects_engine_fatal_version():
    with pytest.raises(ValueError, match="15 through 255"):
        pack_map_tail(map_version=14)

def test_autosave_refuses_closed_session(tmp_path, map_session):
    map_session.closed = True
    map_session.dirty = True
    assert mapforge._write_recovery(map_session) is False
    assert not mapforge._recovery_paths(map_session.dat_path)[0].exists()

def test_slf_archive_outside_active_install_is_rejected(active_install, tmp_path):
    outside = tmp_path / "outside.slf"
    outside.write_bytes(b"not-an-slf")
    with pytest.raises(HTTPException) as exc:
        mapforge._resolve_slf_uri(f"slf://{outside}!/A1.dat")
    assert exc.value.status_code == 403
```

The concurrency test pauses the first atomic replacement, starts the second save, then verifies the second response is `409 EXTERNAL_MODIFICATION` and the first bytes remain on disk.

- [ ] **Step 2: Run all four tests and verify RED for the expected contracts**

Run their exact node IDs with a unique workspace-local `--basetemp`.

- [ ] **Step 3: Implement session install identity and save lock scope**

Move the external guard and backups inside the stated lock order. Replace fixed `.mwtmp` code with `write_bytes_atomic` for normal save, copy-as, and SLF extraction.

- [ ] **Step 4: Implement appendix defense in depth**

Validate `tail.map_version` before assigning `sess.parsed["appendix_model"]`; validate again inside `pack_map_tail`.

- [ ] **Step 5: Implement close/autosave exclusion and SLF confinement**

Mark closed under `sess._lock`, have recovery writing recheck the flag under the same lock, and validate the resolved SLF archive against active-install VFS roots.

- [ ] **Step 6: Run focused MapForge suites and verify GREEN**

Run: `pytest sidecar/tests/test_mapforge_save.py sidecar/tests/test_mapforge_appendix.py sidecar/tests/test_mapforge_autosave.py sidecar/tests/test_mapforge_path_confinement.py -q`.

---

### Task 3: Authenticated sidecar readiness and PID-scoped lifecycle

**Files:**
- Modify: `sidecar/main.py`
- Modify: `sidecar/tests/test_security.py`
- Modify: `shell/src/sidecar.rs`
- Modify: `shell/src/lib.rs`
- Modify: `shell/capabilities/default.json`
- Modify: `launch_current.ps1`

**Interfaces:**
- Python binds/listens before printing `SIDECAR_PORT`.
- Rust `wait_for_health(port, token)` polls the existing authenticated health endpoint until success or `SPAWN_TIMEOUT`.
- `spawn_one` kills the captured child and returns an error if health never succeeds.
- No startup code calls image-name-wide task termination.

- [ ] **Step 1: Write failing Python host/readiness tests**

```python
def test_non_loopback_without_token_is_rejected(monkeypatch):
    monkeypatch.delenv("MERCWIZARD_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        main.validate_bind_security("0.0.0.0")

def test_bound_socket_owns_port_before_marker():
    sock, port = main.bind_server_socket("127.0.0.1", 0)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    finally:
        sock.close()
```

- [ ] **Step 2: Verify Python tests RED, then implement and verify GREEN**

Use an explicitly bound socket passed into `uvicorn.Server.run(sockets=[sock])`; never pick and release a port.

- [ ] **Step 3: Write failing Rust health-wait tests**

Use a local Tokio listener that first returns 401 and then 200 only for the expected `X-MercWizard-Token`; assert readiness returns only after the 200.

- [ ] **Step 4: Verify Rust tests RED, then implement health-gated spawn**

Reuse the existing `reqwest` client and token header. A reported port is not returned from `spawn_one` until `wait_for_health` succeeds.

- [ ] **Step 5: Remove broad process cleanup and unused webview permissions**

Delete the startup orphan sweep call/function. Rewrite launcher process selection to compare each process executable path with this checkout’s exact release paths before `Stop-Process -Id`.

- [ ] **Step 6: Run shell/security verification**

Run the focused Python security tests, `cargo test --locked --offline`, and `cargo check --locked --offline` with an isolated `CARGO_TARGET_DIR`.

---

### Task 4: Destructive-flow invariants, modal accessibility, and Backgrounds regression

**Files:**
- Modify: `sidecar/routes/rpc_dialogue.py`
- Modify: `sidecar/tests/test_rpc_dialogue.py`
- Modify: `frontend/src/components/RpcDialogueTab.tsx`
- Modify: `frontend/src/components/ConfirmModal.tsx`
- Modify: `frontend/src/components/DialogProvider.tsx`
- Modify: `frontend/src/routes/MapForgeTileInspector.tsx`
- Modify: `frontend/src/lib/rpcWorkflow.ts`
- Modify: `frontend/src/lib/__tests__/rpcWorkflow.test.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `sidecar/mercwizard_core/backgrounds_schema.py`
- Test: `sidecar/tests/test_backgrounds_xml.py`

**Interfaces:**
- `DialogueSpec.branches` has `min_length=1`.
- `canRemoveRecruitBranch(branchCount: number): boolean` returns true only above one.
- Shared destructive dialogs remain mounted and non-dismissible while busy.
- `ConfirmModal` labels itself from its title, traps Tab/Shift+Tab, and restores the prior active element.

- [ ] **Step 1: Add/verify RED tests for empty branches and removal invariant**

```python
def test_dialogue_spec_rejects_empty_recruit_branches():
    with pytest.raises(ValidationError):
        DialogueSpec(voice_index=63, branches=[], pre_quotes=[], post_quotes=[])
```

```typescript
expect(canRemoveRecruitBranch(1)).toBe(false);
expect(canRemoveRecruitBranch(2)).toBe(true);
```

- [ ] **Step 2: Implement API and RPC UI invariants; verify focused GREEN**

Disable final-branch removal and Save on impossible empty state. Route persisted fact/talkface removal through `useDialog().confirm({ destructive: true })`.

- [ ] **Step 3: Add failing component tests for busy dismissal and focus behavior**

Add current React-compatible `jsdom`, `@testing-library/react`, and `@testing-library/user-event` as development-only dependencies. Use that real DOM environment to assert Escape/backdrop do not call `onCancel` when busy, Tab remains within the modal, and close returns focus.

- [ ] **Step 4: Implement shared-modal behavior and replace native MapForge confirmation**

Use the shared dialog service; preserve the two documented synchronous navigation-guard exceptions only.

- [ ] **Step 5: Reproduce then fix the existing Backgrounds test**

Run `test_schema_payload_includes_help_for_every_field` to observe the lower-case `percent` failure. Make the `percent` wiki rule case-insensitive and rerun the full Backgrounds test file.

- [ ] **Step 6: Run focused and full frontend tests plus typecheck**

Run `npm test`, `npm run typecheck`, and `npm run build`.

---

### Task 5: Dependency remediation, full verification, and isolated package

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify only as required by official migration: router setup/import consumers
- Produce ignored artifacts under an isolated Cargo target and release-verification workspace

**Interfaces:**
- `npm audit --omit=dev` returns zero known production vulnerabilities.
- Existing deep-link behavior accepts `/path?query=value`, rejects protocol-relative and backslash-based routes, and remains covered by tests.
- Packaging consumes the rebuilt Python sidecar and current Vite bundle without stopping the running app.

- [ ] **Step 1: Capture current audit JSON and determine the minimum fixed React Router release**

Use official npm/GitHub advisory metadata. Do not use `--force` blindly.

- [ ] **Step 2: Write or update route-normalization tests, verify any migration break RED, then upgrade**

Keep command-line routes strictly app-relative after decoding/normalization.

- [ ] **Step 3: Run frontend tests/typecheck/build and production audit**

Expected: 55 or more tests pass, typecheck/build exit zero, production audit reports zero vulnerabilities.

- [ ] **Step 4: Run the complete backend suite**

Use the sidecar venv with a unique workspace-local `--basetemp`. Expected: zero failures; the existing skip is allowed.

- [ ] **Step 5: Rebuild the PyInstaller sidecar without replacing the running release copy**

Build to an isolated dist/work path, then point the isolated Tauri bundle at that verified binary or copy only into the build input that is not locked by the running app.

- [ ] **Step 6: Build full Tauri bundle in an isolated Cargo target**

Set a task-specific `CARGO_TARGET_DIR`; run the full bundle command. Do not launch or install it.

- [ ] **Step 7: Verify artifact freshness and contents**

Confirm the isolated `mercwizard.exe` is newer than `frontend/dist/index.html`, the NSIS installer exists, and the packaged sidecar hash matches the rebuilt build input.

- [ ] **Step 8: Whole-change adversarial review**

Review the path-scoped task diffs together for contract gaps, deadlocks, accidental WIP capture, and security regressions. Address all load-bearing findings before completion.
