"""Hermetic transactional Voice Lab deployment contracts."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from ja2py.fileformats.SlfFS import BufferedSlfFS

from mercwizard_core.voice_lab.models import EditRecipe
from mercwizard_core.voice_lab.service import VoiceLabService


def _profiles(path: Path, *, voice: int = 108) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<MERCPROFILES><PROFILE><uiIndex>108</uiIndex><Type>1</Type>"
        "<zName>King</zName><zNickname>King</zNickname><ubFaceIndex>108</ubFaceIndex>"
        f"<usVoiceIndex>{voice}</usVoiceIndex></PROFILE></MERCPROFILES>",
        encoding="utf-8",
    )


class _Toolchain:
    def probe(self, audio: Path):
        from mercwizard_core.voice_lab.audio import AudioProbe

        return AudioProbe(1000)

    def generate_gap(self, audio: Path):
        from mercwizard_core.voice_lab.audio import GapResult

        return GapResult(gap_bytes=b"\x00\x00\x00\x00\x01\x00\x00\x00", duration_ms=1, pairs=((0, 1),))

    def decoded_metrics(self, audio: Path):
        from mercwizard_core.voice_lab.audio import DecodedMetrics
        return DecodedMetrics(1000, "p" * 64, 22050, 1, 0, 0, -12.0, -20.0)


@pytest.fixture
def deploy_fixture(tmp_path: Path):
    install = tmp_path / "fake-install"
    data = install / "Data-1.13"
    _profiles(data / "TableData" / "MercProfiles.xml")
    speech = data / "Speech"
    speech.mkdir(parents=True)
    source = speech / "108_111.wav"
    source.write_bytes(b"original-wav")
    edt = data / "MercEdt" / "108.EDT"
    edt.parent.mkdir()
    from mercwizard_core.voice_lab.dialogue_edt import FILE_SIZE
    edt.write_bytes(bytes(FILE_SIZE))
    preview = tmp_path / "preview.ogg"
    preview.write_bytes(b"rendered-ogg")
    service = VoiceLabService.for_install(
        "fixture", install, tmp_path / "voice.sqlite3", workspace=tmp_path / "authoring", audio_toolchain=_Toolchain(),
    )
    snapshot = service.scan()
    asset = snapshot.line("speech", 108, "111").audio_winner
    assert asset is not None
    recipe = EditRecipe(
        recipe_id="recipe-line-111", install_id="fixture", voice_index=108, family="speech", line_id="111",
        input_asset_id=asset.asset_id, input_sha256=asset.sha256, output_extension=".ogg", subtitle="Replacement subtitle.",
    )
    recipe = EditRecipe.model_validate({**recipe.model_dump(),
        "preview_asset_path": str(preview), "preview_sha256": sha256(preview.read_bytes()).hexdigest(),
        "preview_pcm_sha256": "a" * 64, "preview_gap_sha256": sha256(b"\x00\x00\x00\x00\x01\x00\x00\x00").hexdigest(),
        "preview_ffmpeg_version": "fixture", "preview_source_sha256": asset.sha256,
        "preview_recipe_sha256": recipe.serialized_recipe_hash(), "preview_gap_present": True,
    })
    service.save_recipe(recipe)
    yield service, install, source, edt
    service.close()


def test_preflight_stages_complete_targets_and_deploys_loose_ogg(deploy_fixture) -> None:
    service, install, source, edt = deploy_fixture

    plan = service.preflight("recipe-line-111")

    assert {action.kind for action in plan.targets} == {"replace", "create"}
    assert all(Path(action.staged_path).is_file() for action in plan.targets if action.staged_path)
    deployed = service.deploy(plan.plan_id)
    assert (install / "Data-1.13" / "Speech" / "108_111.ogg").read_bytes() == b"rendered-ogg"
    assert source.read_bytes() == b"original-wav"
    assert deployed.before_subtitle == ""
    assert edt.is_file()
    winner = service.scan().line("speech", 108, "111").audio_winner
    assert winner is not None and winner.extension == ".ogg"


def test_preflight_refuses_an_active_archived_mp3_shadow(deploy_fixture) -> None:
    """A loose OGG cannot be declared deployed while an immutable MP3 still wins."""
    service, install, _source, _edt = deploy_fixture
    _pack_slf(
        install / "Data-1.13" / "Speech.slf",
        [("108_111.mp3", b"immutable-winning-mp3")],
    )
    source = service.scan().line("speech", 108, "111").audio_winner
    assert source is not None and source.source_kind == "slf" and source.extension == ".mp3"
    preview = install.parent / "archived-shadow-preview.ogg"
    preview.write_bytes(b"rendered-ogg")
    recipe = EditRecipe(
        recipe_id="archived-mp3-shadow", install_id="fixture", voice_index=108,
        family="speech", line_id="111", input_asset_id=source.asset_id,
        input_sha256=source.sha256, output_extension=".ogg",
    )
    recipe = EditRecipe.model_validate({
        **recipe.model_dump(), "preview_asset_path": str(preview),
        "preview_sha256": sha256(preview.read_bytes()).hexdigest(),
        "preview_pcm_sha256": "a" * 64,
        "preview_gap_sha256": sha256(b"\x00\x00\x00\x00\x01\x00\x00\x00").hexdigest(),
        "preview_ffmpeg_version": "fixture", "preview_source_sha256": source.sha256,
        "preview_recipe_sha256": recipe.serialized_recipe_hash(), "preview_gap_present": True,
    })
    service.save_recipe(recipe)

    with pytest.raises(ValueError, match="ACTIVE_ARCHIVED_MP3_SHADOW"):
        service.preflight(recipe.recipe_id)

    assert not (install / "Data-1.13" / "Speech" / "108_111.ogg").exists()
    assert service.deployment_history() == []


def test_preflight_reports_a_future_backup_without_claiming_an_existing_pin(deploy_fixture, monkeypatch) -> None:
    """The public preflight shape must not represent the future snapshot as present."""
    service, _install, _source, _edt = deploy_fixture
    monkeypatch.setattr(service, "_game_running", lambda: False)

    plan = service.preflight("recipe-line-111")

    # This is the actual service plan serialized by the preflight route, not a
    # hand-written frontend-only approximation of the sidecar response.
    assert plan.snapshot["backup_pinned"] is None
    assert plan.snapshot["backup_will_be_pinned"] is True
    assert plan.snapshot["capabilities"] == {
        "game_running": False,
        "toolchain_available": True,
        "recovery_required": False,
    }


def test_deploy_rejects_stale_preflight_before_backup_or_journal(deploy_fixture) -> None:
    service, _install, source, _edt = deploy_fixture
    plan = service.preflight("recipe-line-111")
    source.write_bytes(b"changed-after-review")

    with pytest.raises(ValueError, match="STALE_PLAN"):
        service.deploy(plan.plan_id)

    assert service.pending_journals() == []
    assert service.deployment_history() == []


def test_imported_recipe_keeps_its_original_live_destination_binding(deploy_fixture) -> None:
    """An imported render source must not let a later live winner redirect deployment."""
    service, install, source, _edt = deploy_fixture
    original_winner = service.scan().line("speech", 108, "111").audio_winner
    assert original_winner is not None
    imported_path = install.parent / "durable-import.ogg"
    imported_bytes = b"imported-replacement"
    imported_path.write_bytes(imported_bytes)
    imported = service.register_imported_asset(
        asset_id="import-durable", extension=".ogg", source_locator=imported_path,
        size_bytes=len(imported_bytes), sha256=sha256(imported_bytes).hexdigest(), duration_ms=1000,
    )

    recipe = service.create_recipe_from_draft({
        "input_asset_id": imported.asset_id,
        "input_sha256": imported.sha256,
        "voice_index": 108,
        "family": "speech",
        "line_id": "111",
        "output_extension": ".ogg",
        "operations": [],
        # Client-supplied target claims must be overwritten from the scan.
        "replacement_source": {
            "asset_id": imported.asset_id,
            "sha256": imported.sha256,
            "target_asset_id": "forged-target",
            "target_sha256": "0" * 64,
        },
    })
    assert recipe.replacement_source == {
        "asset_id": imported.asset_id,
        "sha256": imported.sha256,
        "target_asset_id": original_winner.asset_id,
        "target_sha256": original_winner.sha256,
    }
    assert recipe.replacement_source["target_asset_id"] != "forged-target"

    preview = install.parent / "import-preview.ogg"
    preview.write_bytes(b"rendered-import")
    complete = EditRecipe.model_validate({
        **recipe.model_dump(),
        "preview_asset_path": str(preview),
        "preview_sha256": sha256(preview.read_bytes()).hexdigest(),
        "preview_pcm_sha256": "a" * 64,
        "preview_gap_sha256": sha256(b"\x00\x00\x00\x00\x01\x00\x00\x00").hexdigest(),
        "preview_ffmpeg_version": "fixture",
        "preview_source_sha256": imported.sha256,
        "preview_recipe_sha256": recipe.serialized_recipe_hash(),
        "preview_gap_present": True,
    })
    service.save_recipe(complete)
    plan = service.preflight(complete.recipe_id)
    assert any(action.relative_path.endswith("Speech/108_111.ogg") for action in plan.targets)

    source.write_bytes(b"changed-live-winner")
    with pytest.raises(ValueError, match="STALE_PLAN: destination"):
        service.preflight(complete.recipe_id)


def test_public_recipe_listing_and_plan_handoff_keep_stale_plan_revalidation(deploy_fixture) -> None:
    """Routes must not query store internals or bypass deploy's stale-plan lock checks."""
    service, _install, source, _edt = deploy_fixture

    assert [recipe.recipe_id for recipe in service.list_recipes()] == ["recipe-line-111"]
    plan = service.preflight("recipe-line-111")
    service.handoff_preflight_plan(plan)
    source.write_bytes(b"changed-after-public-handoff")

    with pytest.raises(ValueError, match="STALE_PLAN"):
        service.deploy(plan.plan_id)


def test_locked_deploy_rejects_changed_attached_profiles(deploy_fixture) -> None:
    service, install, _source, _edt = deploy_fixture
    plan = service.preflight("recipe-line-111")
    profiles = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    _profiles(profiles, voice=197)

    with pytest.raises(ValueError, match="STALE_PLAN"):
        service.deploy(plan.plan_id)


def test_game_running_refuses_before_any_staging_or_backup(deploy_fixture, monkeypatch) -> None:
    service, _install, _source, _edt = deploy_fixture
    monkeypatch.setattr(service, "_game_running", lambda: True)

    with pytest.raises(RuntimeError, match="GAME_RUNNING"):
        service.preflight("recipe-line-111")

    assert service.pending_journals() == []


def test_game_running_defers_recovery_without_advancing_journal(deploy_fixture, monkeypatch) -> None:
    service, _install, _source, _edt = deploy_fixture
    monkeypatch.setattr(service, "_game_running", lambda: True)
    called: list[bool] = []
    monkeypatch.setattr(service._deployer, "recover_pending", lambda: called.append(True))

    with pytest.raises(RuntimeError, match="GAME_RUNNING"):
        service.recover_pending()

    assert called == []


def test_scan_and_audit_decodes_real_edt_gap_metrics_and_cached_transcript(deploy_fixture) -> None:
    service, install, _source, edt = deploy_fixture
    # Two physical clips with the same decoded PCM plus a malformed gap exercise
    # service integration rather than calling audit helpers directly.
    (install / "Data-1.13" / "Speech" / "108_112.wav").write_bytes(b"second")
    (install / "Data-1.13" / "Speech" / "108_112.gap").write_bytes(b"bad")
    from mercwizard_core.voice_lab.dialogue_edt import DialogueDocument
    edt.write_bytes(DialogueDocument.from_bytes(edt.read_bytes()).replace_text(111, "Completely unrelated displayed text."))
    snapshot = service.scan()
    audio = snapshot.line("speech", 108, "111").audio_winner
    assert audio is not None
    from mercwizard_core.voice_lab.models import Transcription
    service.store.save_transcription(Transcription(asset_id=audio.asset_id, source_sha256=audio.sha256, text="hello there friend", confidence=1.0, language="en", model_id="fixture", model_version="1", created_utc="2026-01-01T00:00:00+00:00"))

    report = service.scan_and_audit()

    codes = {finding.code for finding in report.findings}
    assert {"INVALID_GAP", "EXACT_AUDIO_DUPLICATE", "SUBTITLE_TRANSCRIPT_MISMATCH"}.issubset(codes)


def test_scan_and_audit_reports_malformed_dialogue_edt(deploy_fixture) -> None:
    service, _install, _source, edt = deploy_fixture
    edt.write_bytes(b"not-a-dialogue-document")

    report = service.scan_and_audit()

    assert "MALFORMED_EDT" in {finding.code for finding in report.findings}


def test_scan_and_audit_cancels_between_audio_lines_without_publishing_partial_findings(deploy_fixture) -> None:
    """Checking cancellation only after auditing the full snapshot wastes work and publishes partial state."""
    service, install, _source, _edt = deploy_fixture
    (install / "Data-1.13" / "Speech" / "108_112.wav").write_bytes(b"second-audio")
    progress: list[tuple[int, int, str]] = []

    report = service.scan_and_audit(
        progress=lambda completed, total, message: progress.append((completed, total, message)),
        cancelled=lambda: len(progress) >= 1,
    )

    assert report is None
    assert progress == [(1, 2, "auditing voice audio")]
    assert service.store.findings() == []


def test_inventory_scan_never_decodes_audio_and_targeted_audit_is_bounded(deploy_fixture) -> None:
    """Inventory must remain quick; explicit audit work is bounded to selection."""
    service, install, _source, _edt = deploy_fixture
    (install / "Data-1.13" / "Speech" / "108_112.wav").write_bytes(b"second-audio")

    class CountingToolchain(_Toolchain):
        def __init__(self) -> None:
            self.decoded: list[str] = []

        def decoded_metrics(self, audio: Path):
            self.decoded.append(audio.name)
            return super().decoded_metrics(audio)

    tools = CountingToolchain()
    service._deployer.audio_toolchain = tools

    snapshot = service.scan()
    assert tools.decoded == []

    findings = service.audit_snapshot(snapshot, 108, line_ids=["111"])

    assert findings is not None
    assert tools.decoded == ["108_111.wav"]


def test_targeted_audit_preserves_requested_line_sequence(deploy_fixture) -> None:
    """A selected service audit keeps the supplied branch order for real content evidence."""
    service, install, _source, _edt = deploy_fixture
    (install / "Data-1.13" / "Speech" / "108_112.wav").write_bytes(b"second-audio")
    snapshot = service.scan()
    from mercwizard_core.voice_lab.models import Transcription
    for line_id in ("111", "112"):
        audio = snapshot.line("speech", 108, line_id).audio_winner
        assert audio is not None
        service.store.save_transcription(Transcription(
            asset_id=audio.asset_id, source_sha256=audio.sha256,
            text="same contextual phrase", confidence=1.0, language="en",
            model_id="fixture", model_version="1", created_utc="2026-01-01T00:00:00+00:00",
        ))

    findings = service.audit_snapshot(snapshot, 108, line_ids=["112", "111"])

    assert findings is not None
    duplicate = next(item for item in findings if item.code == "SEQUENCE_NEAR_DUPLICATE")
    assert duplicate.evidence["lines"] == ["112", "111"]


def test_targeted_audit_cancellation_publishes_no_partial_findings(deploy_fixture) -> None:
    service, install, _source, _edt = deploy_fixture
    (install / "Data-1.13" / "Speech" / "108_112.wav").write_bytes(b"second-audio")
    snapshot = service.scan()
    progress: list[tuple[int, int, str]] = []

    findings = service.audit_snapshot(
        snapshot, 108,
        progress=lambda completed, total, message: progress.append((completed, total, message)),
        cancelled=lambda: len(progress) >= 1,
    )

    assert findings is None
    assert progress == [(1, 2, "auditing selected voice audio")]
    assert service.store.findings() == []


def test_undo_preserves_later_unrelated_edt_record(deploy_fixture) -> None:
    service, _install, _source, edt = deploy_fixture
    deployment = service.deploy(service.preflight("recipe-line-111").plan_id)
    from mercwizard_core.voice_lab.dialogue_edt import DialogueDocument
    current = DialogueDocument.from_bytes(edt.read_bytes())
    edt.write_bytes(current.replace_text(112, "A later independent edit."))

    undone = service.undo(deployment.deployment_id)
    after = DialogueDocument.from_bytes(edt.read_bytes())

    assert undone.status == "undone"
    assert after.text(111) == deployment.before_subtitle
    assert after.text(112) == "A later independent edit."


def test_undo_refuses_same_record_conflict_without_unpinning_backup(deploy_fixture) -> None:
    service, _install, _source, edt = deploy_fixture
    deployment = service.deploy(service.preflight("recipe-line-111").plan_id)
    from mercwizard_core.voice_lab.dialogue_edt import DialogueDocument
    edt.write_bytes(DialogueDocument.from_bytes(edt.read_bytes()).replace_text(111, "Other edit."))

    result = service.undo(deployment.deployment_id)

    assert result.status == "UNDO_CONFLICT"
    assert DialogueDocument.from_bytes(edt.read_bytes()).text(111) == "Other edit."
    assert service.backup_is_pinned(deployment.backup_id)


def test_undo_verify_failure_recovers_deployed_postimage(deploy_fixture, monkeypatch) -> None:
    service, install, _source, _edt = deploy_fixture
    deployment = service.deploy(service.preflight("recipe-line-111").plan_id)
    import mercwizard_core.voice_lab.deploy as deploy_module
    real_write = deploy_module.write_bytes_atomic
    corrupted = {"done": False}

    def corrupt_once(path: Path, data: bytes) -> None:
        if not corrupted["done"] and path.suffix.lower() == ".edt":
            corrupted["done"] = True
            real_write(path, b"corrupt")
            return
        real_write(path, data)
    monkeypatch.setattr(deploy_module, "write_bytes_atomic", corrupt_once)

    with pytest.raises(ValueError):
        service.undo(deployment.deployment_id)

    assert (install / "Data-1.13" / "Speech" / "108_111.ogg").read_bytes() == b"rendered-ogg"
    assert service.deployment_history()[0]["status"] == "deployed"


def _pack_slf(path: Path, entries: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slf = BufferedSlfFS()
    slf.library_name = "VOICE_TEST"
    slf.library_path = path.name
    for name, data in entries:
        member = "/" + name.lstrip("/")
        parent = "/".join(member.strip("/").split("/")[:-1])
        if parent:
            slf.makedirs("/" + parent, recreate=True)
        with slf.open(member, "wb") as stream:
            stream.write(data)
    with path.open("wb") as stream:
        slf.save(stream)


def test_slf_source_writes_and_then_removes_a_loose_override(tmp_path: Path) -> None:
    install = tmp_path / "slf-install"
    data = install / "Data-1.13"
    _profiles(data / "TableData" / "MercProfiles.xml")
    _pack_slf(data / "Speech.slf", [("108_111.wav", b"immutable-slf-source")])
    edt = data / "MercEdt" / "108.EDT"
    edt.parent.mkdir()
    from mercwizard_core.voice_lab.dialogue_edt import FILE_SIZE
    edt.write_bytes(bytes(FILE_SIZE))
    preview = tmp_path / "preview.ogg"
    preview.write_bytes(b"rendered-ogg")
    service = VoiceLabService.for_install("slf", install, tmp_path / "slf.sqlite3", workspace=tmp_path / "authoring", audio_toolchain=_Toolchain())
    try:
        source = service.scan().line("speech", 108, "111").audio_winner
        assert source is not None and source.source_kind == "slf"
        base = EditRecipe(recipe_id="slf-recipe", install_id="slf", voice_index=108, family="speech", line_id="111", input_asset_id=source.asset_id, input_sha256=source.sha256, output_extension=".ogg", subtitle="SLF override")
        recipe = EditRecipe.model_validate({**base.model_dump(), "preview_asset_path": str(preview), "preview_sha256": sha256(preview.read_bytes()).hexdigest(), "preview_pcm_sha256": "a" * 64, "preview_gap_sha256": sha256(b"\x00\x00\x00\x00\x01\x00\x00\x00").hexdigest(), "preview_ffmpeg_version": "fixture", "preview_source_sha256": source.sha256, "preview_recipe_sha256": base.serialized_recipe_hash(), "preview_gap_present": True})
        service.save_recipe(recipe)
        deployment = service.deploy(service.preflight("slf-recipe").plan_id)
        override = data / "Speech" / "108_111.ogg"
        assert override.read_bytes() == b"rendered-ogg"
        assert data.joinpath("Speech.slf").read_bytes()  # archive bytes were not changed
        assert service.undo(deployment.deployment_id).status == "undone"
        assert not override.exists()
    finally:
        service.close()
