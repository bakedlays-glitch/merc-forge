import json
from hashlib import sha256
from pathlib import Path
import subprocess
import sys

from mercwizard_core.voice_lab.models import VoiceAsset


def asset(*, digest: str = "a" * 64) -> VoiceAsset:
    return VoiceAsset(
        asset_id="asset-a", family="speech", voice_index=108, line_id="111",
        extension=".ogg", source_kind="loose", source_locator="C:/stage/a.ogg",
        layer_rank=0, size_bytes=12, mtime_ns=1, sha256=digest,
        writable=True, winner=True,
    )


def write_fake_worker(path: Path, capture_path: Path | None = None) -> None:
    capture = ""
    if capture_path is not None:
        capture = f"        open({str(capture_path)!r}, 'wb').write(open(request['audio_path'], 'rb').read())\n"
    source = (
        "import json, sys\n"
        "for raw in sys.stdin:\n"
        "    request = json.loads(raw)\n"
        "    if request.get('operation') == 'capability':\n"
        "        print(json.dumps({'request_id': request['request_id'], 'available': True, 'model_id': request['model'], 'model_version': 'fake-v1'}))\n"
        "    else:\n"
        + capture
        + "        print(json.dumps({'request_id': request['request_id'], 'text': 'Now, what can I do for you?', 'confidence': 0.83, 'words': [{'word': 'Now', 'start': 0.0, 'end': 0.2}], 'language': 'en', 'model_version': 'fake-v1'}))\n"
    )
    path.write_text(source, encoding="utf-8")


def test_missing_transcriber_is_capability_not_scan_failure(tmp_path):
    from mercwizard_core.voice_lab.transcriber import Transcriber

    capability = Transcriber("Z:/missing/python.exe", tmp_path / "worker.py").capability()
    assert capability.available is False
    assert capability.reason == "interpreter_not_found"


def test_default_worker_is_packaged_as_a_runtime_resource():
    from mercwizard_core.voice_lab.transcriber import Transcriber

    assert Transcriber(sys.executable).worker_path.is_file()


def test_worker_protocol_returns_model_provenanced_transcription(tmp_path):
    from mercwizard_core.voice_lab.transcriber import Transcriber

    worker = tmp_path / "worker.py"
    captured = tmp_path / "captured.ogg"
    write_fake_worker(worker, captured)
    audio = tmp_path / "a.ogg"
    audio_bytes = b"not decoded by fake worker"
    audio.write_bytes(audio_bytes)
    transcriber = Transcriber(Path(sys.executable), worker, model_id="fake/whisper")

    assert transcriber.capability().available is True
    transcript = transcriber.transcribe(asset(digest=sha256(audio_bytes).hexdigest()), audio)

    assert transcript.asset_id == "asset-a"
    assert transcript.source_sha256 == sha256(audio_bytes).hexdigest()
    assert transcript.text == "Now, what can I do for you?"
    assert transcript.confidence == 0.83
    assert transcript.model_id == "fake/whisper"
    assert transcript.model_version == "fake-v1"
    assert transcript.word_timestamps == [{"word": "Now", "start": 0.0, "end": 0.2}]
    assert captured.read_bytes() == audio_bytes


def test_capability_probe_is_cached_per_transcriber(tmp_path, monkeypatch):
    from mercwizard_core.voice_lab.transcriber import Transcriber

    worker = tmp_path / "worker.py"
    worker.write_text("# test worker\n", encoding="utf-8")
    transcriber = Transcriber(Path(sys.executable), worker, model_id="fake/whisper")
    calls = []

    def fake_request(request):
        calls.append(request)
        return {
            "available": True,
            "model_id": "fake/whisper",
            "model_version": "fake-v1",
        }

    monkeypatch.setattr(transcriber, "_request", fake_request)

    assert transcriber.capability().available is True
    assert transcriber.capability().available is True
    assert len(calls) == 1


def test_worker_launch_uses_windows_no_console_flag(tmp_path, monkeypatch):
    import mercwizard_core.voice_lab.transcriber as transcriber_module

    worker = tmp_path / "worker.py"
    worker.write_text("# test worker\n", encoding="utf-8")
    no_window = 0x08000000
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        request = json.loads(kwargs["input"])
        response = {
            "request_id": request["request_id"],
            "available": True,
            "model_id": request["model"],
            "model_version": "fake-v1",
        }
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(response), stderr="",
        )

    monkeypatch.setattr(transcriber_module.subprocess, "CREATE_NO_WINDOW", no_window, raising=False)
    monkeypatch.setattr(transcriber_module.subprocess, "run", fake_run)

    capability = transcriber_module.Transcriber(
        Path(sys.executable), worker, model_id="fake/whisper",
    ).capability()

    assert capability.available is True
    assert observed["creationflags"] == no_window


def test_transcriber_rejects_audio_path_when_bytes_do_not_match_asset(tmp_path):
    from mercwizard_core.voice_lab.transcriber import Transcriber

    worker = tmp_path / "worker.py"
    write_fake_worker(worker)
    audio = tmp_path / "unrelated.ogg"
    audio.write_bytes(b"unrelated bytes")

    transcriber = Transcriber(Path(sys.executable), worker, model_id="fake/whisper")
    import pytest
    with pytest.raises(ValueError, match="immutable source bytes"):
        transcriber.transcribe(asset(), audio)


def test_worker_reads_and_writes_exactly_one_json_object_per_line(tmp_path):
    worker = Path(__file__).parents[1] / "mercwizard_core" / "data" / "voice_transcribe_worker.py"
    requests = "\n".join([
        json.dumps({"request_id": "one", "operation": "capability", "model": "does/not-exist"}),
        json.dumps({"request_id": "two", "audio_path": "Z:/missing.wav", "model": "does/not-exist"}),
    ]) + "\n"
    import subprocess
    completed = subprocess.run(
        [sys.executable, str(worker)], input=requests, text=True,
        capture_output=True, check=False,
    )
    assert completed.returncode == 0
    lines = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [line["request_id"] for line in lines] == ["one", "two"]
    assert all("error" in line or "available" in line for line in lines)
