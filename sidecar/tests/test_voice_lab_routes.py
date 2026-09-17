"""HTTP contract tests for the Voice Lab sidecar surface."""
from __future__ import annotations

from pathlib import Path
from hashlib import sha256
from threading import Event, Lock
from time import sleep
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from main import create_app
from routes.state import get_state
from routes import voice_lab as voice_routes
from mercwizard_core.voice_lab.dialogue_edt import DialogueDocument
from mercwizard_core.voice_lab.jobs import VoiceLabJobs
from mercwizard_core.voice_lab.models import DeploymentCapabilities, Transcription, VoiceAsset, VoiceBank, VoiceLine, VoiceProfile
from mercwizard_core.voice_lab.models import InventorySnapshot


@pytest.fixture(autouse=True)
def reset_state():
    state = get_state()
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    state._settings = {}
    yield
    state._installs = {}
    state._active_install_id = None
    state._scan_done = False
    state._settings = {}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_loopback_vite_origin_is_allowed_for_local_ui(client):
    """The IPv4 Vite dev server may not share localhost's IPv6 binding."""
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://127.0.0.1:1420",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:1420"


@pytest.fixture
def registered_install(client: TestClient, tmp_path: Path) -> dict:
    root = tmp_path / "fake_install"
    root.mkdir()
    (root / "JA2.exe").touch()
    table = root / "Data-1.13" / "TableData"
    table.mkdir(parents=True)
    (table / "MercProfiles.xml").write_text("<PROFILES />")
    (table / "AIMAvailability.xml").write_text("<AIM_AVAILABLES />")
    response = client.post("/api/v1/installs", json={"path": str(root)})
    assert response.status_code == 200, response.text
    install = response.json()
    assert client.post("/api/v1/installs/active", json={"install_id": install["id"]}).status_code == 200
    return install


def _seed_live_voice_winner(registered_install: dict, *, payload: bytes = b"OggS-live-winner") -> Path:
    """Create the scanned live destination that imported recipes must bind to."""
    path = Path(registered_install["path"]) / "Data-1.13" / "Speech" / "108_111.ogg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_audio_route_rejects_unknown_asset(client, registered_install):
    response = client.get("/api/v1/voice-lab/assets/C:/secret.txt/audio")
    assert response.status_code == 404


def test_audio_route_serves_byte_ranges_for_browser_seeking(client, registered_install, monkeypatch):
    """Without byte ranges, the browser reports a zero-length seekable timeline."""
    from mercwizard_core.voice_lab.audio import AudioProbe
    monkeypatch.setattr(
        "routes.voice_lab._audio_toolchain",
        lambda: type("Tools", (), {"probe": lambda self, path: AudioProbe(100)})(),
    )
    payload = b"OggS-0123456789"
    imported = client.post(
        "/api/v1/voice-lab/import-source",
        files={"file": ("seekable.ogg", payload, "audio/ogg")},
    ).json()

    response = client.get(
        f"/api/v1/voice-lab/assets/{imported['asset_id']}/audio",
        headers={"Range": "bytes=5-9"},
    )

    assert response.status_code == 206
    assert response.content == payload[5:10]
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == f"bytes 5-9/{len(payload)}"
    assert response.headers["content-length"] == "5"


def test_scan_job_can_be_cancelled(client, registered_install):
    created = client.post("/api/v1/voice-lab/scans").json()
    cancelled = client.post(f"/api/v1/voice-lab/jobs/{created['job_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested"] is True


def test_scan_dedupes_registered_aliases_of_the_same_physical_root(monkeypatch, tmp_path: Path):
    """Two VFS registrations of one install must share selected-bank scan admission."""
    jobs = VoiceLabJobs()
    started = Event()
    release = Event()
    root = tmp_path / "same-install"
    root.mkdir()
    installs = {
        "profile-a": SimpleNamespace(id="profile-a", path=root),
        "profile-b": SimpleNamespace(id="profile-b", path=root),
    }
    try:
        monkeypatch.setattr(voice_routes, "_jobs", jobs)
        monkeypatch.setattr(voice_routes, "_resolve_install", lambda install_id: installs[install_id])

        def scan_work(_install_id, _voice_index):
            def work(_progress, _cancelled):
                started.set()
                release.wait(timeout=2)
            return work

        monkeypatch.setattr(voice_routes, "_scan_work", scan_work)
        first = voice_routes.start_scan(install_id="profile-a", voice_index=108)
        assert started.wait(timeout=2)
        same_bank = voice_routes.start_scan(install_id="profile-b", voice_index=108)
        full_scan = voice_routes.start_scan(install_id="profile-b", voice_index=None)

        assert same_bank["job_id"] == first["job_id"]
        assert full_scan["job_id"] != first["job_id"]
    finally:
        release.set()
        jobs.shutdown()


def test_inventory_scan_worker_uses_selected_bank_scan_without_full_audio_audit(registered_install, monkeypatch):
    """The initial route scan must publish inventory without decoding every clip."""
    sentinel = InventorySnapshot(install_id=registered_install["id"], banks=[])
    calls: list[str] = []

    class Service:
        def scan(self, *, progress, cancelled, voice_indexes=None):
            calls.append("scan")
            assert voice_indexes == {108}
            progress(1, 1, "inventory ready")
            assert cancelled() is False
            return sentinel

        def scan_and_audit(self, **_kwargs):
            raise AssertionError("initial inventory route must not invoke full audit")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(voice_routes, "_service", lambda install_id=None: Service())
    result = voice_routes._scan_work(
        registered_install["id"], 108,
    )(lambda *_args: None, lambda: False)

    assert result is sentinel
    assert calls == ["scan", "close"]
    assert voice_routes._snapshot(registered_install["id"]) is sentinel


def test_bank_catalog_returns_profile_names_without_scanning_audio(
    client, registered_install, monkeypatch,
):
    """Opening the merc picker must use the cheap profile catalog boundary."""
    profile = VoiceProfile(
        profile_id=15, profile_type=1, name="Tycho", nickname="Tycho",
        face_index=15, voice_index=15,
    )
    catalog = InventorySnapshot(
        install_id=registered_install["id"],
        banks=[VoiceBank(voice_index=15, profiles=[profile], lines=[])],
    )

    class Service:
        def profile_catalog(self):
            return catalog

        def scan(self, **_kwargs):
            raise AssertionError("catalog route scanned audio")

        def close(self):
            pass

    monkeypatch.setattr(voice_routes, "_service", lambda install_id=None: Service())

    response = client.get("/api/v1/voice-lab/catalog")

    assert response.status_code == 200
    assert response.json()[0]["profiles"][0]["name"] == "Tycho"
    assert response.json()[0]["lines"] == []


def test_status_exposes_authoritative_deployment_capabilities(client, registered_install, monkeypatch):
    """The status wire must distinguish an unavailable writer from a safe one."""
    class Service:
        def deployment_status(self):
            return DeploymentCapabilities(game_running=False, toolchain_available=False, recovery_required=True)

        def close(self):
            pass

    monkeypatch.setattr("routes.voice_lab._service", lambda install_id=None: Service())
    response = client.get("/api/v1/voice-lab/status")

    assert response.status_code == 200, response.text
    assert response.json()["deployment"] == {
        "game_running": False,
        "toolchain_available": False,
        "recovery_required": True,
    }


def test_preflight_reports_future_backup_and_stable_toolchain_error(client, registered_install, monkeypatch):
    """Preflight cannot claim a pin before deploy creates the backup."""
    class Service:
        def preflight(self, recipe_id):
            raise RuntimeError("VOICE_TOOLCHAIN_UNAVAILABLE: FFmpeg is unavailable")

        def close(self):
            pass

    monkeypatch.setattr("routes.voice_lab._service", lambda install_id=None: Service())
    response = client.post("/api/v1/voice-lab/deploy/preflight", json={"recipe_id": "recipe-1"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "error": "VOICE_TOOLCHAIN_UNAVAILABLE",
        "message": "FFmpeg is unavailable",
    }


def test_preflight_maps_stale_source_to_stable_error(client, registered_install, monkeypatch):
    class Service:
        def preflight(self, recipe_id):
            raise ValueError("STALE_PLAN: destination live winner changed")

        def close(self):
            pass

    monkeypatch.setattr("routes.voice_lab._service", lambda install_id=None: Service())
    response = client.post("/api/v1/voice-lab/deploy/preflight", json={"recipe_id": "recipe-1"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "error": "VOICE_SOURCE_CHANGED",
        "message": "destination live winner changed",
    }


def test_settings_accept_external_voice_workspace(client, registered_install, tmp_path):
    workspace = tmp_path / "external-authoring"
    workspace.mkdir()
    response = client.put("/api/v1/settings", json={"voice_authoring_workspace": str(workspace)})
    assert response.status_code == 200
    assert response.json()["voice_authoring_workspace"] == str(workspace.resolve())


def test_settings_reject_workspace_inside_a_registered_install(client, registered_install, monkeypatch):
    """A read route must never bootstrap authoring state inside a game install."""
    install = Path(registered_install["path"])
    workspace = install / "voice-authoring"
    workspace.mkdir()

    response = client.put(
        "/api/v1/settings", json={"voice_authoring_workspace": str(workspace)},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error"] == "VOICE_WORKSPACE_IN_INSTALL"
    assert workspace.is_dir()

    before = sorted(path.relative_to(install) for path in install.rglob("*") if path.is_file())
    from mercwizard_core.voice_lab.audio import AudioProbe
    monkeypatch.setattr(
        "routes.voice_lab._audio_toolchain",
        lambda: type("Tools", (), {"probe": lambda self, path: AudioProbe(100)})(),
    )
    assert client.get("/api/v1/voice-lab/status").status_code == 200
    assert client.post(
        "/api/v1/voice-lab/import-source",
        files={"file": ("external.ogg", b"OggS\x00outside-install", "audio/ogg")},
    ).status_code == 201
    assert client.post("/api/v1/voice-lab/recipes/missing/preview").status_code == 201

    after = sorted(path.relative_to(install) for path in install.rglob("*") if path.is_file())
    assert after == before


def test_scan_cancelled_during_inventory_keeps_the_last_published_snapshot(
    registered_install, monkeypatch,
):
    """Cancellation during enumeration must not publish a partially scanned inventory."""
    old = InventorySnapshot(install_id=registered_install["id"], banks=[])
    voice_routes._remember_snapshot(old)
    calls: list[object] = []

    class Service:
        def scan(self, *, progress, cancelled, voice_indexes=None):
            assert voice_indexes is None
            calls.extend((progress, cancelled))
            progress(1, 4, "scanning loose voice files")
            return None

        def close(self):
            calls.append("close")

    monkeypatch.setattr(voice_routes, "_service", lambda install_id=None: Service())
    cancellation_checks = iter((False, False, True))
    cancelled = voice_routes._scan_work(registered_install["id"])(
        lambda *_args: None, lambda: next(cancellation_checks),
    )

    assert cancelled is None
    assert voice_routes._snapshot(registered_install["id"]) is old
    assert calls[-1] == "close"


def test_imported_source_is_returned_as_opaque_asset(client, registered_install, tmp_path, monkeypatch):
    # The route must not ever turn an upload name or local source path into a
    # public identifier.  A lightweight fake ffprobe keeps this test hermetic.
    from mercwizard_core.voice_lab.audio import AudioProbe
    monkeypatch.setattr("routes.voice_lab._audio_toolchain", lambda: type("Tools", (), {"probe": lambda self, path: AudioProbe(100)})())
    response = client.post(
        "/api/v1/voice-lab/import-source",
        files={"file": ("candidate.ogg", b"OggS\x00test", "audio/ogg")},
    )
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["asset_id"]
    assert "source_locator" not in asset
    assert str(tmp_path) not in response.text


def test_imported_source_is_registered_for_opaque_media_and_transcription_input(
    client, registered_install, tmp_path, monkeypatch,
):
    """Returning an unregistered upload ID makes the opaque asset workflow unusable."""
    from mercwizard_core.voice_lab.audio import AudioProbe

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(
        "routes.voice_lab._audio_toolchain",
        lambda: type("Tools", (), {"probe": lambda self, path: AudioProbe(100)})(),
    )

    imported = client.post(
        "/api/v1/voice-lab/import-source",
        files={"file": ("candidate.ogg", b"OggS\x00registered", "audio/ogg")},
    )
    assert imported.status_code == 201, imported.text
    asset = imported.json()

    media = client.get(f"/api/v1/voice-lab/assets/{asset['asset_id']}/audio")
    assert media.status_code == 200, media.text
    assert media.content == b"OggS\x00registered"
    assert "source_locator" not in media.text

    accepted = client.post("/api/v1/voice-lab/transcriptions", json={"asset_ids": [asset["asset_id"]]})
    assert accepted.status_code == 201, accepted.text
    unknown = client.post("/api/v1/voice-lab/transcriptions", json={"asset_ids": ["C:/secret.ogg"]})
    assert unknown.status_code == 404


def test_recipe_draft_uses_server_owned_identity_and_rejects_hash_mismatch(client, registered_install, monkeypatch):
    """Draft clients must not fabricate install/recipe identity or stale source hashes."""
    from mercwizard_core.voice_lab.audio import AudioProbe
    monkeypatch.setattr("routes.voice_lab._audio_toolchain", lambda: type("Tools", (), {"probe": lambda self, path: AudioProbe(100)})())
    _seed_live_voice_winner(registered_install)
    data = b"OggS\x00draft"
    imported = client.post("/api/v1/voice-lab/import-source", files={"file": ("draft.ogg", data, "audio/ogg")}).json()
    draft = {"input_asset_id": imported["asset_id"], "input_sha256": sha256(data).hexdigest(), "voice_index": 108, "family": "speech", "line_id": "111", "output_extension": ".ogg", "operations": [], "subtitle": "Hello", "replacement_source": {"asset_id": imported["asset_id"], "sha256": sha256(data).hexdigest()}}
    saved = client.post("/api/v1/voice-lab/recipes", json=draft)
    assert saved.status_code == 201, saved.text
    assert saved.json()["recipe_id"]
    rejected = client.post("/api/v1/voice-lab/recipes", json={**draft, "input_sha256": "0" * 64})
    assert rejected.status_code == 422


def test_preview_job_plans_opaque_media_and_transcription_and_line_detail_are_readable(client, registered_install):
    """Preview and evidence reads must expose opaque DTOs only, never filesystem paths."""
    preview = client.post("/api/v1/voice-lab/recipes/not-a-recipe/preview")
    assert preview.status_code == 201
    assert preview.json()["preview_asset_id"].startswith("preview-")
    assert client.get("/api/v1/voice-lab/transcriptions", params={"asset_id": "missing"}).status_code == 404
    detail = client.get("/api/v1/voice-lab/lines/108/speech/111")
    assert detail.status_code == 404


def test_completed_preview_becomes_opaque_media_and_cannot_be_reused_as_source(client, registered_install, monkeypatch):
    from mercwizard_core.voice_lab.audio import AudioProbe, RenderResult
    render_started, allow_render = Event(), Event()
    class Tools:
        def probe(self, path): return AudioProbe(100)
        def render_recipe(self, source, recipe, target):
            render_started.set()
            assert allow_render.wait(timeout=2)
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b"OggS-preview")
            return RenderResult(target, 100, sha256(b"OggS-preview").hexdigest(), "a" * 64, recipe.input_sha256, recipe.serialized_recipe_hash(), "fixture")
    monkeypatch.setattr("routes.voice_lab._audio_toolchain", lambda: Tools())
    _seed_live_voice_winner(registered_install)
    source_bytes = b"OggS-source"; imported = client.post("/api/v1/voice-lab/import-source", files={"file": ("source.ogg", source_bytes, "audio/ogg")}).json()
    draft = {"input_asset_id": imported["asset_id"], "input_sha256": sha256(source_bytes).hexdigest(), "voice_index": 108, "family": "speech", "line_id": "111", "output_extension": ".ogg", "operations": []}
    recipe = client.post("/api/v1/voice-lab/recipes", json=draft).json()
    created = client.post(f"/api/v1/voice-lab/recipes/{recipe['recipe_id']}/preview").json(); preview_id = created["preview_asset_id"]
    assert render_started.wait(timeout=2)
    assert client.get(f"/api/v1/voice-lab/assets/{preview_id}/audio").status_code == 404
    allow_render.set()
    for _ in range(30):
        job = client.get(f"/api/v1/voice-lab/jobs/{created['job_id']}").json()
        if job["status"] in {"complete", "failed"}: break
        sleep(.02)
    assert job["status"] == "complete", job
    streamed = client.get(f"/api/v1/voice-lab/assets/{preview_id}/audio")
    assert streamed.status_code == 200 and streamed.content == b"OggS-preview" and "path" not in str(created).lower()
    rejected = client.post("/api/v1/voice-lab/recipes", json={**draft, "input_asset_id": preview_id, "input_sha256": sha256(b"OggS-preview").hexdigest()})
    assert rejected.status_code == 422


def test_concurrent_preview_generations_keep_distinct_media_bytes(client, registered_install, monkeypatch):
    """A slow older generation must not overwrite the newer generation's media."""
    from mercwizard_core.voice_lab.audio import AudioProbe, RenderResult

    first_started, second_started, allow_writes, second_finished = Event(), Event(), Event(), Event()
    call_lock = Lock()
    call_count = 0

    class Tools:
        def probe(self, path): return AudioProbe(100)
        def render_recipe(self, source, recipe, target):
            nonlocal call_count
            with call_lock:
                call_count += 1
                call = call_count
            (first_started if call == 1 else second_started).set()
            assert allow_writes.wait(2)
            if call == 1:
                assert second_finished.wait(2)
            payload = f"OggS-preview-{call}".encode()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            if call == 2:
                second_finished.set()
            return RenderResult(target, 100, sha256(payload).hexdigest(), "a" * 64, recipe.input_sha256, recipe.serialized_recipe_hash(), "fixture")

    monkeypatch.setattr("routes.voice_lab._audio_toolchain", lambda: Tools())
    _seed_live_voice_winner(registered_install)
    source_bytes = b"OggS-source"
    imported = client.post("/api/v1/voice-lab/import-source", files={"file": ("source.ogg", source_bytes, "audio/ogg")}).json()
    recipe = client.post("/api/v1/voice-lab/recipes", json={
        "input_asset_id": imported["asset_id"], "input_sha256": sha256(source_bytes).hexdigest(),
        "voice_index": 108, "family": "speech", "line_id": "111", "output_extension": ".ogg", "operations": [],
    }).json()
    first = client.post(f"/api/v1/voice-lab/recipes/{recipe['recipe_id']}/preview").json()
    assert first_started.wait(2)
    second = client.post(f"/api/v1/voice-lab/recipes/{recipe['recipe_id']}/preview").json()
    assert second_started.wait(2)
    assert first["preview_asset_id"] != second["preview_asset_id"]
    assert client.get(f"/api/v1/voice-lab/assets/{first['preview_asset_id']}/audio").status_code == 404
    assert client.get(f"/api/v1/voice-lab/assets/{second['preview_asset_id']}/audio").status_code == 404
    allow_writes.set()

    for _ in range(100):
        first_job = client.get(f"/api/v1/voice-lab/jobs/{first['job_id']}").json()
        second_job = client.get(f"/api/v1/voice-lab/jobs/{second['job_id']}").json()
        if first_job["status"] == second_job["status"] == "complete":
            break
        sleep(.02)
    assert first_job["status"] == second_job["status"] == "complete"
    assert client.get(f"/api/v1/voice-lab/assets/{first['preview_asset_id']}/audio").content == b"OggS-preview-1"
    assert client.get(f"/api/v1/voice-lab/assets/{second['preview_asset_id']}/audio").content == b"OggS-preview-2"


def test_line_detail_decodes_subtitle_and_returns_hash_bound_transcript(client, registered_install):
    root = Path(registered_install["path"]); audio_path = root / "line.ogg"; edt_path = root / "line.edt"
    audio_path.write_bytes(b"audio"); edt_path.write_bytes(DialogueDocument(bytes(146 * 480)).replace_text(111, "Stay sharp."))
    audio = VoiceAsset(asset_id="line-audio", family="speech", voice_index=108, line_id="111", extension=".ogg", source_kind="loose", source_locator=str(audio_path), layer_rank=0, size_bytes=5, mtime_ns=1, sha256=sha256(b"audio").hexdigest(), writable=True, winner=True)
    edt = VoiceAsset(asset_id="line-edt", family="dialogue_edt", voice_index=108, line_id="0", extension=".edt", source_kind="loose", source_locator=str(edt_path), layer_rank=0, size_bytes=146 * 480, mtime_ns=1, sha256=sha256(edt_path.read_bytes()).hexdigest(), writable=True, winner=True)
    snapshot = InventorySnapshot(install_id=registered_install["id"], banks=[VoiceBank(voice_index=108, profiles=[VoiceProfile(profile_id=7, profile_type=1, name="Tycho", nickname="Ranger", face_index=None, voice_index=108)], lines=[VoiceLine(family="speech", voice_index=108, line_id="111", audio_variants=[audio], audio_winner=audio, trigger_by_profile_id={7: {"slot": 111, "name": "QUOTE", "meaning": "Combat warning"}}), VoiceLine(family="dialogue_edt", voice_index=108, line_id="0", dialogue_variants=[edt], dialogue_edt=edt)])])
    service = voice_routes._service(); service.store.replace_inventory(snapshot); service.store.save_transcription(Transcription(asset_id=audio.asset_id, source_sha256=audio.sha256, text="Stay sharp.", confidence=.92, language="en", model_id="tiny", model_version="1", word_timestamps=[], created_utc="2026-08-15T00:00:00+00:00")); service.close(); voice_routes._remember_snapshot(snapshot)
    response = client.get("/api/v1/voice-lab/lines/108/speech/111")
    assert response.status_code == 200 and response.json()["subtitle"] == "Stay sharp."
    assert response.json()["transcription"]["text"] == "Stay sharp." and response.json()["transcription"]["confidence"] == .92
    assert response.json()["line"]["trigger_by_profile_id"]["7"]["meaning"] == "Combat warning"
    transcript = client.get("/api/v1/voice-lab/transcriptions", params={"asset_id": "line-audio"})
    assert transcript.status_code == 200 and transcript.json()["model_id"] == "tiny"


def test_analysis_transcribes_only_missing_lines_before_auditing(monkeypatch, tmp_path):
    """A bank analysis must give the content audit all available transcripts."""
    cached_asset = VoiceAsset(asset_id="cached", family="speech", voice_index=7, line_id="108", extension=".ogg", source_kind="loose", source_locator=str(tmp_path / "cached.ogg"), layer_rank=0, size_bytes=6, mtime_ns=1, sha256="a" * 64, writable=True, winner=True)
    missing_asset = VoiceAsset(asset_id="missing", family="speech", voice_index=7, line_id="111", extension=".ogg", source_kind="loose", source_locator=str(tmp_path / "missing.ogg"), layer_rank=0, size_bytes=7, mtime_ns=1, sha256="b" * 64, writable=True, winner=True)
    snapshot = InventorySnapshot(install_id="copy", banks=[VoiceBank(voice_index=7, lines=[
        VoiceLine(family="speech", voice_index=7, line_id="108", audio_variants=[cached_asset], audio_winner=cached_asset),
        VoiceLine(family="speech", voice_index=7, line_id="111", audio_variants=[missing_asset], audio_winner=missing_asset),
    ])])
    transcripts = {
        "cached": Transcription(asset_id="cached", source_sha256="a" * 64, text="Already here", confidence=.9, language="en", model_id="tiny", model_version="1", word_timestamps=[], created_utc="2026-08-16T00:00:00+00:00"),
    }
    calls: list[str] = []

    class Store:
        def transcription(self, asset_id, source_sha256):
            value = transcripts.get(asset_id)
            return value if value is not None and value.source_sha256 == source_sha256 else None
        def save_transcription(self, value):
            calls.append(f"save:{value.asset_id}")
            transcripts[value.asset_id] = value

    class Service:
        store = Store()
        def _stage_readable_asset(self, asset):
            return Path(asset.source_locator)
        def audit_snapshot(self, _snapshot, voice_index, *, line_ids, progress, cancelled):
            calls.append("audit")
            assert voice_index == 7
            assert line_ids is None
            assert set(transcripts) == {"cached", "missing"}
            progress(2, 2, "auditing selected voice audio")
            return ["finding"]
        def close(self):
            calls.append("close")

    class FakeTranscriber:
        def transcribe(self, asset, _path):
            calls.append(f"transcribe:{asset.asset_id}")
            return Transcription(asset_id=asset.asset_id, source_sha256=asset.sha256, text="New transcript", confidence=.8, language="en", model_id="tiny", model_version="1", word_timestamps=[], created_utc="2026-08-16T00:00:00+00:00")

    monkeypatch.setattr(voice_routes, "_service", lambda install_id=None: Service())
    monkeypatch.setattr(voice_routes.Transcriber, "from_settings", lambda _settings: FakeTranscriber())
    progress: list[tuple[int, int, str]] = []

    result = voice_routes._analysis_work("copy", snapshot, voice_routes.AuditRequest(voice_index=7))(
        lambda completed, total, message: progress.append((completed, total, message)),
        lambda: False,
    )

    assert result == ["finding"]
    assert calls == ["transcribe:missing", "save:missing", "audit", "close"]
    assert progress[0] == (1, 3, "Transcribing missing lines")
    assert progress[-1] == (3, 3, "Checking audio and subtitles")


def test_analysis_cancellation_never_publishes_partial_findings(monkeypatch, tmp_path):
    """Cancelling after a cached transcript write must stop before audit publication."""
    asset = VoiceAsset(asset_id="line", family="speech", voice_index=8, line_id="1", extension=".ogg", source_kind="loose", source_locator=str(tmp_path / "line.ogg"), layer_rank=0, size_bytes=4, mtime_ns=1, sha256="c" * 64, writable=True, winner=True)
    snapshot = InventorySnapshot(install_id="copy", banks=[VoiceBank(voice_index=8, lines=[VoiceLine(family="speech", voice_index=8, line_id="1", audio_variants=[asset], audio_winner=asset)])])
    audited = False

    class Store:
        def transcription(self, *_args): return None
        def save_transcription(self, _value): pass
    class Service:
        store = Store()
        def _stage_readable_asset(self, _asset): return tmp_path / "line.ogg"
        def audit_snapshot(self, *_args, **_kwargs):
            nonlocal audited
            audited = True
        def close(self): pass
    class FakeTranscriber:
        def transcribe(self, item, _path):
            return Transcription(asset_id=item.asset_id, source_sha256=item.sha256, text="line", confidence=.8, language="en", model_id="tiny", model_version="1", word_timestamps=[], created_utc="2026-08-16T00:00:00+00:00")

    monkeypatch.setattr(voice_routes, "_service", lambda install_id=None: Service())
    monkeypatch.setattr(voice_routes.Transcriber, "from_settings", lambda _settings: FakeTranscriber())
    checks = iter((False, False, True))
    result = voice_routes._analysis_work("copy", snapshot, voice_routes.AuditRequest(voice_index=8))(
        lambda *_args: None,
        lambda: next(checks, True),
    )

    assert result is None
    assert audited is False
