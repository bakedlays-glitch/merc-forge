"""Hermetic release acceptance for the Voice Lab public service boundary."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import wave

import pytest
from ja2py.fileformats.SlfFS import BufferedSlfFS

from mercwizard_core.voice_lab.audio import AudioProbe, DecodedMetrics, GapResult
from mercwizard_core.voice_lab.dialogue_edt import DialogueDocument, FILE_SIZE
from mercwizard_core.voice_lab.models import EditRecipe, Transcription
from mercwizard_core.voice_lab.service import VoiceLabService


sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from voice_lab_acceptance import _install_evidence, _named_profile_bank, _profile_bank, collect_read_only_report


class _AcceptanceTools:
    """Hermetic audio facts that exercise audit/deploy without external tools."""

    def probe(self, _audio: Path) -> AudioProbe:
        return AudioProbe(1000, 22050, 1)

    def decoded_metrics(self, audio: Path) -> DecodedMetrics:
        duration = 1000 if audio.stem.endswith("111") else 250
        return DecodedMetrics(duration, sha256(audio.read_bytes()).hexdigest(), 22050, 1, 0, 0, -12.0, -20.0)

    def generate_gap(self, _audio: Path) -> GapResult:
        return GapResult(struct.pack("<II", 0, 1), 1000, ((0, 1),))


class _FakeTranscriber:
    def capability(self):
        return type("Capability", (), {"available": True, "model_id": "fixture-whisper", "model_version": "1"})()

    def transcribe(self, asset, _audio: Path) -> Transcription:
        text = {
            "111": "Please stay on the line while I transfer your call to the commander right away now.",
            "112": "Please stay on the line while I transfer your call to the commander right away.",
        }[asset.line_id]
        return Transcription(
            asset_id=asset.asset_id, source_sha256=asset.sha256, text=text,
            confidence=0.95, language="en", model_id="fixture-whisper", model_version="1",
            word_timestamps=[], created_utc="2026-08-15T00:00:00+00:00",
        )


def _write_tone(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(b"\x00\x00" * 2205)


def _profiles(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<MERCPROFILES>"
        "<PROFILE><uiIndex>108</uiIndex><Type>1</Type><zName>King</zName>"
        "<zNickname>King</zNickname><ubFaceIndex>108</ubFaceIndex><usVoiceIndex>108</usVoiceIndex></PROFILE>"
        "<PROFILE><uiIndex>15</uiIndex><Type>1</Type><zName>Tycho</zName>"
        "<zNickname>Tycho</zNickname><ubFaceIndex>15</ubFaceIndex><usVoiceIndex>15</usVoiceIndex></PROFILE>"
        "</MERCPROFILES>", encoding="utf-8",
    )


@pytest.fixture
def king_shaped_install(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    data = install / "Data-1.13"
    _profiles(data / "TableData" / "MercProfiles.xml")
    _write_tone(data / "Speech" / "108_111.wav")
    _write_tone(data / "Speech" / "108_112.wav")
    (data / "Speech" / "108_111.gap").write_bytes(struct.pack("<II", 0, 1))
    (data / "Speech" / "108_112.gap").write_bytes(struct.pack("<II", 0, 1))
    _write_tone(data / "Speech" / "15_1.wav")
    edt = data / "MercEdt" / "108.EDT"
    edt.parent.mkdir(parents=True)
    document = DialogueDocument.from_bytes(bytes(FILE_SIZE))
    edt.write_bytes(document.replace_text(111, "A short displayed subtitle."))
    tycho_edt = data / "MercEdt" / "15.EDT"
    tycho_edt.write_bytes(bytes(FILE_SIZE))
    return install


def _service(tmp_path: Path, install: Path) -> VoiceLabService:
    return VoiceLabService.for_install(
        "king-fixture", install, tmp_path / "voice.sqlite3", workspace=tmp_path / "authoring",
        audio_toolchain=_AcceptanceTools(),
    )


def _complete_recipe(service: VoiceLabService, install: Path) -> EditRecipe:
    source = service.scan().line("speech", 108, "111").audio_winner
    assert source is not None
    preview = install.parent / "preview.ogg"
    preview.write_bytes(b"acceptance-preview")
    base = EditRecipe(
        recipe_id="king-line-111", install_id="king-fixture", voice_index=108, family="speech", line_id="111",
        input_asset_id=source.asset_id, input_sha256=source.sha256, output_extension=".ogg",
        operations=[{"kind": "cut", "start_ms": 100, "end_ms": 200}], subtitle="Edited subtitle.",
    )
    return EditRecipe.model_validate({
        **base.model_dump(), "preview_asset_path": str(preview),
        "preview_sha256": sha256(preview.read_bytes()).hexdigest(), "preview_pcm_sha256": "a" * 64,
        "preview_gap_sha256": sha256(struct.pack("<II", 0, 1)).hexdigest(), "preview_ffmpeg_version": "fixture",
        "preview_source_sha256": source.sha256, "preview_recipe_sha256": base.serialized_recipe_hash(),
        "preview_gap_present": True,
    })


def test_acceptance_fixture_detects_phone_failures_by_evidence(king_shaped_install: Path, tmp_path: Path) -> None:
    """Removing content evidence or the audit checks must make this acceptance fail."""
    report = collect_read_only_report(
        king_shaped_install, king_profile=108, tycho_name="Tycho", transcriber=_FakeTranscriber(),
        audio_toolchain=_AcceptanceTools(), state_root=tmp_path / "outside-install",
    )

    codes = {item["code"] for item in report["king"]["findings"]}
    assert {"SUBTITLE_TRANSCRIPT_MISMATCH", "SEQUENCE_NEAR_DUPLICATE", "DURATION_TEXT_MISMATCH"} <= codes
    assert report["king"]["profile"]["profile_id"] == 108
    assert report["tycho"]["profile"]["profile_id"] == 15
    assert report["transcription"]["available"] is True
    assert report["writes_performed"] == 0
    assert report["install_evidence"]["before"] == report["install_evidence"]["after"]


def test_acceptance_evidence_rehashes_assets_after_scan(king_shaped_install: Path, tmp_path: Path) -> None:
    """The before/after proof must detect a changed loose voice asset."""
    service = _service(tmp_path, king_shaped_install)
    try:
        snapshot = service.scan()
        king_bank, _king = _profile_bank(snapshot.banks, 108)
        tycho_bank, _tycho = _named_profile_bank(snapshot.banks, "Tycho")
        before = _install_evidence(king_shaped_install, service, (king_bank, tycho_bank))

        (king_shaped_install / "Data-1.13" / "Speech" / "108_111.wav").write_bytes(b"changed-after-scan")

        after = _install_evidence(king_shaped_install, service, (king_bank, tycho_bank))
    finally:
        service.close()

    assert before != after


def test_acceptance_evidence_rehashes_slf_archive_beyond_selected_member(tmp_path: Path) -> None:
    """An unrelated SLF member change must invalidate the physical archive proof."""
    install = tmp_path / "slf-evidence-install"
    data = install / "Data-1.13"
    _profiles(data / "TableData" / "MercProfiles.xml")
    archive = data / "Speech.slf"
    _write_slf(archive, unrelated=b"original-unrelated-member")
    service = _service(tmp_path, install)
    try:
        snapshot = service.scan()
        king_bank, _king = _profile_bank(snapshot.banks, 108)
        tycho_bank, _tycho = _named_profile_bank(snapshot.banks, "Tycho")
        before = _install_evidence(install, service, (king_bank, tycho_bank))

        _write_slf(archive, unrelated=b"changed-unrelated-member")

        after = _install_evidence(install, service, (king_bank, tycho_bank))
    finally:
        service.close()

    assert before["slf_archives"] != after["slf_archives"]
    assert before != after


def test_acceptance_evidence_hashes_each_slf_archive_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bank with many selected members must not rehash one backing archive per member."""
    install = tmp_path / "slf-dedup-install"
    data = install / "Data-1.13"
    _profiles(data / "TableData" / "MercProfiles.xml")
    archive = data / "Speech.slf"
    _write_slf(archive, unrelated=b"also-selected-by-bank-evidence")
    service = _service(tmp_path, install)
    try:
        snapshot = service.scan()
        king_bank, _king = _profile_bank(snapshot.banks, 108)
        tycho_bank, _tycho = _named_profile_bank(snapshot.banks, "Tycho")
        from voice_lab_acceptance import _hash_file
        calls: list[Path] = []

        def count_hash(path: Path) -> str:
            calls.append(path.resolve())
            return _hash_file(path)

        monkeypatch.setattr("voice_lab_acceptance._hash_file", count_hash)
        _install_evidence(install, service, (king_bank, tycho_bank))
    finally:
        service.close()

    assert calls.count(archive.resolve()) == 1


@pytest.mark.parametrize("locator_kind", ["absolute", "traversal"])
def test_acceptance_evidence_rejects_slf_archives_outside_install_before_reads_or_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locator_kind: str,
) -> None:
    """Read-only acceptance must not follow a source locator beyond its install root."""
    install = tmp_path / "install"
    data = install / "Data-1.13"
    _profiles(data / "TableData" / "MercProfiles.xml")
    archive = data / "Speech.slf"
    _write_slf(archive)
    outside = tmp_path / "outside.slf"
    _write_slf(outside)
    service = _service(tmp_path, install)
    try:
        snapshot = service.scan()
        bank, _king = _profile_bank(snapshot.banks, 108)
        line = snapshot.line("speech", 108, "111")
        asset = line.audio_winner
        assert asset is not None
        locator = str(outside) if locator_kind == "absolute" else str(data / ".." / ".." / "outside.slf")
        unsafe = asset.model_copy(update={"source_locator": json.dumps({"archive": locator, "member": "Speech/108_111.wav"})})
        line.audio_variants = [unsafe]
        line.audio_winner = unsafe
        reads: list[str] = []
        hashes: list[Path] = []
        monkeypatch.setattr(service, "read_asset_bytes", lambda _asset: reads.append("read") or b"")
        monkeypatch.setattr("voice_lab_acceptance._hash_file", lambda path: hashes.append(path) or "hash")

        with pytest.raises(ValueError, match="outside the supplied install"):
            _install_evidence(install, service, (bank,))
    finally:
        service.close()

    assert reads == []
    assert hashes == []


def test_acceptance_fixture_deploys_then_undoes_without_overwriting_later_edt_record(
    king_shaped_install: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Undo must splice only the deployed record rather than restoring a whole stale EDT."""
    service = _service(tmp_path, king_shaped_install)
    monkeypatch.setattr(service, "_game_running", lambda: False)
    try:
        recipe = _complete_recipe(service, king_shaped_install)
        service.save_recipe(recipe)
        plan = service.preflight(recipe.recipe_id)
        assert {action.relative_path.replace("\\", "/") for action in plan.targets} >= {
            "Data-1.13/Speech/108_111.ogg", "Data-1.13/Speech/108_111.gap", "Data-1.13/MercEdt/108.EDT",
        }
        deployed = service.deploy(plan.plan_id)
        edt = king_shaped_install / "Data-1.13" / "MercEdt" / "108.EDT"
        edt.write_bytes(DialogueDocument.from_bytes(edt.read_bytes()).replace_text(112, "Later independent text."))
        assert service.undo(deployed.deployment_id).status == "undone"
        restored = DialogueDocument.from_bytes(edt.read_bytes())
        assert restored.text(111) == "A short displayed subtitle."
        assert restored.text(112) == "Later independent text."
    finally:
        service.close()


def _write_slf(path: Path, *, unrelated: bytes | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    archive = BufferedSlfFS()
    archive.library_name = "VOICE_FIXTURE"
    archive.library_path = path.name
    archive.makedirs("/Speech", recreate=True)
    with archive.open("/Speech/108_111.wav", "wb") as stream:
        stream.write(b"immutable-slf-source")
    if unrelated is not None:
        with archive.open("/Speech/108_999.wav", "wb") as stream:
            stream.write(unrelated)
    with path.open("wb") as stream:
        archive.save(stream)


def test_acceptance_fixture_undo_removes_only_loose_override_for_slf_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo must never rewrite an immutable source archive when a loose override was deployed."""
    install = tmp_path / "slf-install"
    data = install / "Data-1.13"
    _profiles(data / "TableData" / "MercProfiles.xml")
    archive = data / "Speech.slf"
    _write_slf(archive)
    edt = data / "MercEdt" / "108.EDT"
    edt.parent.mkdir(parents=True)
    edt.write_bytes(DialogueDocument.from_bytes(bytes(FILE_SIZE)).replace_text(111, "Original."))
    before_archive = sha256(archive.read_bytes()).hexdigest()
    service = _service(tmp_path, install)
    monkeypatch.setattr(service, "_game_running", lambda: False)
    try:
        recipe = _complete_recipe(service, install)
        service.save_recipe(recipe)
        deployed = service.deploy(service.preflight(recipe.recipe_id).plan_id)
        override = data / "Speech" / "108_111.ogg"
        assert override.is_file()
        assert service.undo(deployed.deployment_id).status == "undone"
        assert not override.exists()
        assert sha256(archive.read_bytes()).hexdigest() == before_archive
    finally:
        service.close()
