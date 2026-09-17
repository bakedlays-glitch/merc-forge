"""Child process for abrupt Voice Lab deployment recovery tests."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import sys

from mercwizard_core.voice_lab.dialogue_edt import FILE_SIZE
from mercwizard_core.voice_lab.models import EditRecipe
from mercwizard_core.voice_lab.service import VoiceLabService


class _Toolchain:
    def generate_gap(self, audio: Path):
        from mercwizard_core.voice_lab.audio import GapResult
        return GapResult(b"", 1, ())


def main(root: Path, fail_after: str) -> None:
    if fail_after.startswith("recover_"):
        install = root / "install"

        def recovery_boundary(name: str) -> None:
            if name == fail_after:
                os._exit(91)

        service = VoiceLabService.for_install(
            "crash-fixture", install, root / "store.sqlite3", workspace=root / "workspace",
            audio_toolchain=_Toolchain(), transaction_hook=recovery_boundary,
        )
        service.recover_pending()
        return
    install = root / "install"
    data = install / "Data-1.13"
    profiles = data / "TableData" / "MercProfiles.xml"
    profiles.parent.mkdir(parents=True, exist_ok=True)
    profiles.write_text("<MERCPROFILES><PROFILE><uiIndex>108</uiIndex><Type>1</Type><zName>King</zName><zNickname>King</zNickname><usVoiceIndex>108</usVoiceIndex></PROFILE></MERCPROFILES>", encoding="utf-8")
    speech = data / "Speech"
    speech.mkdir()
    (speech / "108_111.wav").write_bytes(b"preimage")
    (install / "preimage.marker").write_bytes(b"preimage")
    edt = data / "MercEdt" / "108.EDT"
    edt.parent.mkdir()
    original_edt = bytes(FILE_SIZE)
    edt.write_bytes(original_edt)
    (root / "original.edt").write_bytes(original_edt)
    preview = root / "preview.ogg"
    preview.write_bytes(b"rendered")

    def crash(index: int) -> None:
        if fail_after.isdigit() and index == int(fail_after):
            os._exit(91)
    def boundary(name: str) -> None:
        if name == fail_after:
            os._exit(91)
    service = VoiceLabService.for_install("crash-fixture", install, root / "store.sqlite3", workspace=root / "workspace", audio_toolchain=_Toolchain(), crash_after_target=crash, transaction_hook=None if fail_after.startswith("undo_") else boundary)
    snapshot = service.scan()
    source = snapshot.line("speech", 108, "111").audio_winner
    assert source is not None
    base = EditRecipe(recipe_id="crash", install_id="crash-fixture", voice_index=108, family="speech", line_id="111", input_asset_id=source.asset_id, input_sha256=source.sha256, output_extension=".ogg", subtitle="Changed")
    recipe = EditRecipe.model_validate({**base.model_dump(), "preview_asset_path": str(preview), "preview_sha256": sha256(preview.read_bytes()).hexdigest(), "preview_pcm_sha256": "a" * 64, "preview_gap_sha256": sha256(b"").hexdigest(), "preview_ffmpeg_version": "fixture", "preview_source_sha256": source.sha256, "preview_recipe_sha256": base.serialized_recipe_hash(), "preview_gap_present": True})
    service.save_recipe(recipe)
    deployment = service.deploy(service.preflight("crash").plan_id)
    if fail_after.startswith("undo_"):
        service.close()
        undo_service = VoiceLabService.for_install("crash-fixture", install, root / "store.sqlite3", workspace=root / "workspace", audio_toolchain=_Toolchain(), transaction_hook=boundary)
        undo_service.undo(deployment.deployment_id)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
