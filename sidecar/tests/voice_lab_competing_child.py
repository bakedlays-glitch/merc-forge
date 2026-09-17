"""Independent real VoiceLabService deployment contender for regression tests."""
from __future__ import annotations

from pathlib import Path
import os
import sys
import time

from mercwizard_core.voice_lab.audio import GapResult
from mercwizard_core.voice_lab.service import VoiceLabService


class _Toolchain:
    def generate_gap(self, audio: Path):
        return GapResult(b"", 1, ())


def event(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{text}\n")
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    root, log, label = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    service = VoiceLabService.for_install("compete", root / "install", root / "store.sqlite3", workspace=root / "workspace", audio_toolchain=_Toolchain())
    try:
        plan = service.preflight("compete")
        event(log, f"{label} preflight")
        while not (root / "go").is_file():
            time.sleep(0.01)
        try:
            service.deploy(plan.plan_id)
        except ValueError as exc:
            event(log, f"{label} result STALE_PLAN" if "STALE_PLAN" in str(exc) else f"{label} result error")
        else:
            event(log, f"{label} result deployed")
    finally:
        service.close()
