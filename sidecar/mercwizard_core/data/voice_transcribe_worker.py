"""JSON-lines Whisper worker run in the separately provisioned voice environment.

This file deliberately has no project imports: PyInstaller does not bundle Torch or
Transformers, and the configured external interpreter owns those dependencies.
"""
from __future__ import annotations

import json
import sys
from typing import Any


_PIPES: dict[str, Any] = {}


def _error(request_id: Any, reason: str) -> dict[str, Any]:
    return {"request_id": request_id, "error": reason}


def _load_pipeline(model_id: str):
    if model_id in _PIPES:
        return _PIPES[model_id]
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline  # type: ignore[import-not-found]

    model = AutoModelForSpeechSeq2Seq.from_pretrained(model_id, local_files_only=True)
    processor = AutoProcessor.from_pretrained(model_id, local_files_only=True)
    asr = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
    )
    _PIPES[model_id] = asr
    return asr


def _capability(request_id: Any, model_id: str) -> dict[str, Any]:
    try:
        loaded = _load_pipeline(model_id)
        version = getattr(__import__("transformers"), "__version__", "unknown")
        del loaded
        return {"request_id": request_id, "available": True, "model_id": model_id, "model_version": version}
    except ImportError:
        return {"request_id": request_id, "available": False, "reason": "dependency_unavailable"}
    except OSError:
        return {"request_id": request_id, "available": False, "reason": "model_unavailable"}
    except Exception as exc:
        # Do not expose a machine path or stack trace through the API protocol.
        return {"request_id": request_id, "available": False, "reason": f"model_load_failed:{type(exc).__name__}"}


def _transcribe(request_id: Any, model_id: str, audio_path: Any) -> dict[str, Any]:
    if not isinstance(audio_path, str) or not audio_path:
        return _error(request_id, "audio_path_required")
    try:
        result = _load_pipeline(model_id)(audio_path, return_timestamps="word")
    except FileNotFoundError:
        return _error(request_id, "audio_not_found")
    except ImportError:
        return _error(request_id, "dependency_unavailable")
    except OSError:
        return _error(request_id, "model_unavailable")
    except Exception as exc:
        return _error(request_id, f"transcription_failed:{type(exc).__name__}")
    chunks = result.get("chunks") if isinstance(result, dict) else None
    words: list[dict[str, Any]] = []
    scores: list[float] = []
    if isinstance(chunks, list):
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            timestamp = chunk.get("timestamp")
            word: dict[str, Any] = {"word": str(chunk.get("text") or "").strip()}
            if isinstance(timestamp, (list, tuple)) and len(timestamp) == 2:
                word["start"], word["end"] = timestamp
            if isinstance(chunk.get("score"), (int, float)):
                scores.append(float(chunk["score"]))
            words.append(word)
    confidence = sum(scores) / len(scores) if scores else 0.0
    text = result.get("text") if isinstance(result, dict) else ""
    return {
        "request_id": request_id,
        "text": str(text or "").strip(),
        "confidence": confidence,
        "words": words,
        "language": "en",
        "model_version": getattr(__import__("transformers"), "__version__", "unknown"),
    }


def main() -> int:
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            request_id = request.get("request_id")
            model_id = request.get("model")
            if not isinstance(model_id, str) or not model_id:
                response = _error(request_id, "model_required")
            elif request.get("operation") == "capability":
                response = _capability(request_id, model_id)
            else:
                response = _transcribe(request_id, model_id, request.get("audio_path"))
        except (ValueError, json.JSONDecodeError):
            response = _error(None, "invalid_json_request")
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
