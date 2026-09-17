"""Two real sidecar services contend on the same Voice Lab deployment."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys
import time

from mercwizard_core.voice_lab.dialogue_edt import FILE_SIZE
from mercwizard_core.voice_lab.models import EditRecipe
from mercwizard_core.voice_lab.service import VoiceLabService


class _Toolchain:
    def generate_gap(self, audio: Path):
        from mercwizard_core.voice_lab.audio import GapResult
        return GapResult(b"", 1, ())


def test_two_competing_sidecars_preflight_then_serialize_real_deploy(tmp_path: Path) -> None:
    """One writer commits; the other revalidates under the production lock and goes stale."""
    install = tmp_path / "install"
    data = install / "Data-1.13"
    profiles = data / "TableData" / "MercProfiles.xml"
    profiles.parent.mkdir(parents=True)
    profiles.write_text("<MERCPROFILES><PROFILE><uiIndex>108</uiIndex><Type>1</Type><zName>King</zName><zNickname>King</zNickname><usVoiceIndex>108</usVoiceIndex></PROFILE></MERCPROFILES>", encoding="utf-8")
    speech = data / "Speech"
    speech.mkdir()
    (speech / "108_111.wav").write_bytes(b"source")
    edt = data / "MercEdt" / "108.EDT"
    edt.parent.mkdir()
    edt.write_bytes(bytes(FILE_SIZE))
    preview = tmp_path / "preview.ogg"
    preview.write_bytes(b"rendered")
    # Seed the shared immutable recipe repository before the two sidecars open.
    seed = VoiceLabService.for_install("compete", install, tmp_path / "store.sqlite3", workspace=tmp_path / "workspace", audio_toolchain=_Toolchain())
    try:
        source = seed.scan().line("speech", 108, "111").audio_winner
        assert source is not None
        base = EditRecipe(recipe_id="compete", install_id="compete", voice_index=108, family="speech", line_id="111", input_asset_id=source.asset_id, input_sha256=source.sha256, output_extension=".ogg", subtitle="changed")
        seed.save_recipe(EditRecipe.model_validate({**base.model_dump(), "preview_asset_path": str(preview), "preview_sha256": sha256(preview.read_bytes()).hexdigest(), "preview_pcm_sha256": "a" * 64, "preview_gap_sha256": sha256(b"").hexdigest(), "preview_ffmpeg_version": "fixture", "preview_source_sha256": source.sha256, "preview_recipe_sha256": base.serialized_recipe_hash(), "preview_gap_present": True}))
    finally:
        seed.close()
    script = Path(__file__).with_name("voice_lab_competing_child.py")
    log = tmp_path / "competing.log"
    environment = dict(os.environ, APPDATA=str(tmp_path / "appdata"), PYTHONPATH=str(Path(__file__).parents[1]))
    first = subprocess.Popen([sys.executable, str(script), str(tmp_path), str(log), "one"], env=environment)
    second = subprocess.Popen([sys.executable, str(script), str(tmp_path), str(log), "two"], env=environment)
    deadline = time.monotonic() + 15
    while (not log.exists() or log.read_text(encoding="utf-8").count("preflight") < 2) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert log.exists() and log.read_text(encoding="utf-8").count("preflight") == 2
    (tmp_path / "go").write_text("go", encoding="utf-8")
    assert first.wait(timeout=15) == 0
    assert second.wait(timeout=15) == 0
    events = log.read_text(encoding="utf-8").splitlines()
    assert sum("result deployed" in line for line in events) == 1
    assert sum("result STALE_PLAN" in line for line in events) == 1
    assert (data / "Speech" / "108_111.ogg").read_bytes() == b"rendered"
