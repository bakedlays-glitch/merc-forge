"""Optional, externally hosted local transcription for Voice Lab."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from importlib import resources
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel

from .models import Transcription, VoiceAsset


DEFAULT_MODEL_ID = "openai/whisper-tiny.en"


class TranscriberCapability(BaseModel):
    """Truthful availability result for the configured external worker."""

    available: bool
    reason: str | None = None
    model_id: str = DEFAULT_MODEL_ID
    model_version: str | None = None


class Transcriber:
    """Run a JSON-lines transcription worker in an explicitly configured Python."""

    def __init__(self, interpreter: Path | str | None, worker_path: Path | str | None = None, *, model_id: str = DEFAULT_MODEL_ID) -> None:
        self.interpreter = Path(interpreter) if interpreter else None
        self.worker_path = Path(worker_path) if worker_path else _default_worker_path()
        self.model_id = model_id
        self._capability_cache: TranscriberCapability | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "Transcriber":
        configured = _setting(settings, "voice_transcriber_python")
        return cls(configured)

    def capability(self) -> TranscriberCapability:
        """Probe interpreter, worker, dependencies, and the cached local model only."""
        if self._capability_cache is not None:
            return self._capability_cache
        if self.interpreter is None or not self.interpreter.is_file():
            return TranscriberCapability(available=False, reason="interpreter_not_found", model_id=self.model_id)
        if not self.worker_path.is_file():
            return TranscriberCapability(available=False, reason="worker_not_found", model_id=self.model_id)
        response = self._request({"operation": "capability"})
        if response is None:
            capability = TranscriberCapability(available=False, reason="worker_unavailable", model_id=self.model_id)
        elif response.get("available") is True:
            capability = TranscriberCapability(
                available=True,
                model_id=str(response.get("model_id") or self.model_id),
                model_version=_optional_string(response.get("model_version")),
            )
        else:
            capability = TranscriberCapability(
                available=False,
                reason=_optional_string(response.get("reason")) or "worker_unavailable",
                model_id=self.model_id,
                model_version=_optional_string(response.get("model_version")),
            )
        self._capability_cache = capability
        return capability

    def transcribe(self, asset: VoiceAsset, audio_path: Path | str) -> Transcription:
        """Transcribe exact asset bytes; failure remains an optional-capability error."""
        capability = self.capability()
        if not capability.available:
            raise RuntimeError(f"Voice Lab transcription unavailable: {capability.reason}")
        source = Path(audio_path)
        source_bytes = source.read_bytes()
        source_sha256 = sha256(source_bytes).hexdigest()
        if source_sha256 != asset.sha256:
            raise ValueError("audio_path bytes do not match immutable source bytes")
        suffix = source.suffix if source.suffix else ".audio"
        with tempfile.NamedTemporaryFile(prefix="mercforge-voice-", suffix=suffix, delete=False) as staged:
            staged.write(source_bytes)
            staged_path = Path(staged.name)
        try:
            # The handle is closed before the worker starts so Windows can reopen it.
            response = self._request({"audio_path": str(staged_path)})
        finally:
            try:
                staged_path.unlink()
            except FileNotFoundError:
                pass
        if response is None:
            raise RuntimeError("Voice Lab transcription worker did not return JSON")
        if response.get("error"):
            raise RuntimeError(f"Voice Lab transcription failed: {response['error']}")
        text = response.get("text")
        if not isinstance(text, str):
            raise RuntimeError("Voice Lab transcription worker did not return text")
        confidence = _confidence(response.get("confidence"))
        words = response.get("words")
        if not isinstance(words, list):
            words = []
        return Transcription(
            asset_id=asset.asset_id,
            source_sha256=source_sha256,
            text=text,
            confidence=confidence,
            language=_optional_string(response.get("language")) or "en",
            model_id=self.model_id,
            model_version=_optional_string(response.get("model_version")) or capability.model_version or "unknown",
            word_timestamps=[word for word in words if isinstance(word, dict)],
            created_utc=datetime.now(timezone.utc).isoformat(),
        )

    def _request(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        if self.interpreter is None:
            return None
        request_id = uuid4().hex
        payload = {"request_id": request_id, "model": self.model_id, **request}
        try:
            completed = subprocess.run(
                [str(self.interpreter), str(self.worker_path)],
                input=json.dumps(payload, separators=(",", ":")) + "\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        for raw in completed.stdout.splitlines():
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if response.get("request_id") == request_id and isinstance(response, dict):
                return response
        return None


def _default_worker_path() -> Path:
    return Path(resources.files("mercwizard_core.data").joinpath("voice_transcribe_worker.py"))


def _setting(settings: Any, key: str) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(key)
    return getattr(settings, key, None)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _confidence(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
