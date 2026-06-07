"""Voice file management routes — list/upload/delete .wav clips per merc."""
from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from mercwizard_core import voice
from mercwizard_core.cross_lock import cross_process_install_lock

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


def _voice_index_for_slot(install, slot: int) -> int:
    """The usVoiceIndex for the merc at `slot`.

    Falls back to `slot` if the merc has no <usVoiceIndex> field (vanilla
    convention is index==slot anyway).
    """
    from mercwizard_core.inject import profiles_xml
    from mercwizard_core.install_context import make_install_context
    ctx = make_install_context(install.path)
    profile = profiles_xml.read_slot(ctx.profiles_xml_path(), slot)
    if profile is None:
        return slot
    raw = profile.get("usVoiceIndex")
    if raw is None:
        return slot
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return slot


@router.get("/voice/probe/{voice_index}")
def probe_voice_index(voice_index: int, install_id: str | None = Query(default=None)) -> dict:
    """Lightweight existence check: does this voice index have ANY clips?

    Used by Create's Identity step to warn the user when their chosen voice
    donor maps to an empty Speech/<n>/ directory — the merc would be silent
    in combat. Returns count + folder path without enumerating every file.

    Also probes `Data/Speech.slf` for SLF-archived clips at this index.
    Classic vanilla donor slots (0–50ish) ship their voice barks INSIDE
    Speech.slf, not as loose files. Without this probe the loose-only
    scan returns 0 and we'd warn the user that a vanilla character is
    "silent in combat" — false alarm. (Bug #4 in MERC_FORGE_BUG_LIST.md.)
    """
    info = _resolve_install(install_id)
    clips = voice.list_clips(info.path, voice_index)
    sd = voice.speech_dir(info.path, voice_index)
    # SLF probe — count Speech.slf members whose path matches the
    # convention `<n>/...` or `<n>_NNN.<ext>`. If the SLF has any, the
    # vanilla voice WILL play in-game regardless of the loose count.
    slf_clip_count = _probe_speech_slf(info.path, voice_index)
    return {
        "voice_index": voice_index,
        "folder": str(sd),
        "folder_exists": sd.is_dir(),
        "clip_count": len(clips),
        # New fields. Frontend uses these to differentiate
        # "actually silent" (both zero) from "vanilla archive" (loose
        # zero + SLF nonzero). Pre-existing clients ignoring these
        # fields keep their current behavior.
        "slf_clip_count": slf_clip_count,
        "is_vanilla_archive": slf_clip_count > 0 and len(clips) == 0,
    }


def _probe_speech_slf(install_root, voice_index: int) -> int:
    """Count voice clips for `voice_index` inside Data/Speech.slf.

    Returns 0 when Speech.slf is missing, unreadable, or has no entries
    matching `voice_index/` or `voice_index_NNN`. Errors are swallowed
    because this is an advisory count — a missing or corrupt SLF should
    fall back to the loose count, not crash the endpoint.
    """
    from pathlib import Path as _Path
    speech_slf = _Path(install_root) / "Data" / "Speech.slf"
    if not speech_slf.is_file():
        return 0
    try:
        from ja2py.fileformats.SlfFS import SlfFS  # noqa: E402
    except ImportError:
        return 0
    try:
        fs = SlfFS(str(speech_slf))
    except Exception:  # noqa: BLE001
        return 0
    # Wrap the whole walk in try/finally so the SlfFS handle ALWAYS
    # closes. Without this, every /voice/probe call leaked one Speech.slf
    # handle — on Windows that keeps the SLF exclusively-open so mod
    # managers / file replacers couldn't touch it until the sidecar
    # restarted. Sweep bug-review finding.
    import os
    prefix_dir = f"/{voice_index}/"
    prefix_flat = f"{voice_index}_"
    count = 0
    try:
        try:
            for p in fs.walk.files():
                # SLF paths normalize with leading "/". Two layouts:
                #  - subdir: /<voice_index>/MERCNNN_NNN.wav
                #  - flat:   /<voice_index>_NNN.wav (Vengeance-style)
                lower = p.lower()
                if prefix_dir in lower:
                    count += 1
                    continue
                # Flat layout: the BASENAME starts with `<voice_index>_`.
                # Use basename to avoid matching e.g. "/30_something/" as a
                # directory component.
                base = os.path.basename(lower)
                if base.startswith(prefix_flat):
                    count += 1
        except Exception:  # noqa: BLE001
            return count
    finally:
        try:
            fs.close()
        except Exception:  # noqa: BLE001
            pass
    return count


@router.get("/voice/{slot}")
def list_voice_clips(slot: int, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    idx = _voice_index_for_slot(info, slot)
    clips = voice.list_clips(info.path, idx)
    sd = voice.speech_dir(info.path, idx)
    return {
        "slot": slot,
        "voice_index": idx,
        "folder": str(sd),
        "folder_exists": sd.is_dir(),
        "clips": [
            {"name": c.name, "size_bytes": c.size_bytes, "path": c.path}
            for c in clips
        ],
    }


@router.post("/voice/{slot}/upload")
async def upload_voice_clips(
    slot: int,
    files: List[UploadFile] = File(...),
    barks: str | None = Form(default=None),
    install_id: str | None = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    idx = _voice_index_for_slot(info, slot)
    state = get_state()

    # Optional auto-naming. `barks` is a JSON list parallel to `files`; each
    # entry is the JA2 quote/bark number for that clip (or null to keep the
    # uploaded filename). When set, the clip is written engine-correctly as
    # `<voiceIndex:03d>_<bark:03d>.<ext>` so the user never has to name it.
    bark_list: list = []
    if barks:
        import json
        try:
            parsed = json.loads(barks)
            if isinstance(parsed, list):
                bark_list = parsed
        except (ValueError, TypeError):
            bark_list = []

    added: list[dict] = []
    skipped: list[dict] = []
    with cross_process_install_lock(info.id), state.write_lock:
        for i, f in enumerate(files):
            original = f.filename or "clip.wav"
            try:
                data = await f.read()
                bark = bark_list[i] if i < len(bark_list) else None
                if bark is not None:
                    ext = Path(original).suffix
                    if ext.lower() not in {".wav", ".ogg", ".mp3"}:
                        ext = ".wav"
                    name = f"{idx:03d}_{int(bark):03d}{ext}"
                else:
                    name = original
                clip = voice.add_clip_bytes(info.path, idx, name, data)
                added.append({"name": clip.name, "size_bytes": clip.size_bytes})
            except (ValueError, TypeError) as e:
                skipped.append({"name": original, "reason": str(e)})

    return {
        "ok": True,
        "slot": slot,
        "voice_index": idx,
        "added": added,
        "skipped": skipped,
    }


@router.delete("/voice/{slot}/{filename}")
def delete_voice_clip(
    slot: int, filename: str, install_id: str | None = Query(default=None)
) -> dict:
    info = _resolve_install(install_id)
    idx = _voice_index_for_slot(info, slot)
    state = get_state()
    with cross_process_install_lock(info.id), state.write_lock:
        removed = voice.delete_clip(info.path, idx, filename)
    if not removed:
        raise HTTPException(status_code=404, detail={"error": "CLIP_NOT_FOUND"})
    return {"ok": True, "removed": filename}


@router.delete("/voice/{slot}")
def delete_all_voice_clips(slot: int, install_id: str | None = Query(default=None)) -> dict:
    info = _resolve_install(install_id)
    idx = _voice_index_for_slot(info, slot)
    state = get_state()
    with cross_process_install_lock(info.id), state.write_lock:
        count = voice.delete_all_clips(info.path, idx)
    return {"ok": True, "removed_count": count}
