"""Voice file management for mercs.

JA2 plays a merc's voice clips from one of two locations depending on the
install's VFS-aware layout flavor:

  Vanilla / subdir:     `<mod content>/Speech/<usVoiceIndex>/<file>.<ext>`
  Vengeance / slot_prefix: `<mod content>/Speech/<usVoiceIndex>_<idx>.<ext>` at the Speech root

The wizard detects the layout per-install via `install_context.LayoutFlavor`
and writes to the matching convention. List/upload/delete are all VFS-aware.

Naming conventions vary by JA2 version and mod (MERCNNN_NNN.wav vs NNN.wav).
We don't enforce any pattern — accept whatever the player gives us with the
filename intact.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import gap


WAV_EXTENSIONS = (".wav", ".WAV", ".ogg", ".mp3")  # JA2 mostly uses .wav, but be permissive on read


@dataclass
class VoiceClip:
    name: str
    size_bytes: int
    path: str  # absolute, for the UI


def speech_dir(install_root: Path, voice_index: int) -> Path:
    """The on-disk folder for a given voice index.

    VFS-aware: walks the install's VFS chain to find the Speech/ root
    in the mod content layer (e.g. Data-Vengeance/Speech/) rather than
    assuming Data-1.13/Speech/.
    """
    from .install_context import make_install_context
    ctx = make_install_context(Path(install_root))
    return ctx.speech_root() / str(voice_index)


def list_clips(install_root: Path, voice_index: int) -> list[VoiceClip]:
    """Enumerate every audio file for this voice/slot index.

    Handles two layout conventions:
    - **Vanilla / subdir**: `Speech/<voice_index>/<file>.wav` — every file
      in the per-index subdirectory.
    - **Vengeance / slot_prefix**: `Speech/<voice_index>_<idx>.ogg` — every
      file at the Speech/ root whose name starts with `<voice_index>_`.

    Detection is flavor-aware via `InstallContext`. Returns clips from
    whichever layout matches the install. If both somehow exist, returns
    the union (de-duped by filename).
    """
    from .install_context import make_install_context
    ctx = make_install_context(Path(install_root))
    clips_by_name: dict[str, VoiceClip] = {}

    # Subdir layout
    subdir = ctx.speech_root() / str(voice_index)
    if subdir.is_dir():
        try:
            for entry in subdir.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix not in WAV_EXTENSIONS:
                    continue
                try:
                    clips_by_name[entry.name] = VoiceClip(
                        name=entry.name,
                        size_bytes=entry.stat().st_size,
                        path=str(entry.resolve()),
                    )
                except OSError:
                    continue
        except (OSError, PermissionError):
            pass

    # Slot-prefix layout (Vengeance: Speech/<n>_NNN.ogg at the Speech/ root)
    if ctx.flavor.voice_layout == "slot_prefix":
        speech_root = ctx.speech_root()
        prefix = f"{voice_index}_"
        if speech_root.is_dir():
            try:
                for entry in speech_root.iterdir():
                    if not entry.is_file():
                        continue
                    if entry.suffix not in WAV_EXTENSIONS:
                        continue
                    if not entry.name.startswith(prefix):
                        continue
                    try:
                        clips_by_name[entry.name] = VoiceClip(
                            name=entry.name,
                            size_bytes=entry.stat().st_size,
                            path=str(entry.resolve()),
                        )
                    except OSError:
                        continue
            except (OSError, PermissionError):
                pass

    clips = list(clips_by_name.values())
    clips.sort(key=lambda c: c.name.lower())
    return clips


def _resolve_write_path(install_root: Path, voice_index: int, filename: str) -> Path:
    """Compute where a clip with this filename should live in the target install.

    For vanilla `subdir` layout: `Speech/<voice_index>/<filename>`.
    For Vengeance `slot_prefix` layout: `Speech/<filename>` at the root —
    the filename is expected to already be `<slot>_<event>.ogg` shaped.
    Caller's responsibility to ensure the filename's prefix matches the
    intended slot before passing it in.
    """
    from .install_context import make_install_context
    ctx = make_install_context(install_root)
    if ctx.flavor.voice_layout == "slot_prefix":
        return ctx.speech_root(for_write=True) / filename
    return ctx.speech_root(for_write=True) / str(voice_index) / filename


def add_clips(install_root: Path, voice_index: int, source_files: Iterable[Path]) -> list[VoiceClip]:
    """Copy `source_files` into the voice folder. Creates the folder if needed.

    VFS-aware: writes to the layout the active install uses (vanilla subdir
    or Vengeance slot_prefix).
    """
    for src in source_files:
        src = Path(src)
        if not src.is_file():
            continue
        if src.suffix.lower() not in {s.lower() for s in WAV_EXTENSIONS}:
            continue
        dst = _resolve_write_path(install_root, voice_index, src.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # If a clip with the same name already exists, overwrite it (player's
        # call — they uploaded the same name).
        shutil.copy2(src, dst)
        # Generate the lip-sync sidecar alongside the copied clip.
        try:
            gap.write_gap_beside(dst, src.read_bytes())
        except OSError:
            pass
    return list_clips(install_root, voice_index)


def add_clip_bytes(
    install_root: Path,
    voice_index: int,
    filename: str,
    data: bytes,
) -> VoiceClip:
    """Write a single uploaded clip (from multipart upload) into the voice folder.

    `filename` is the original name from the upload; we sanitize path
    separators so multipart uploads can't escape the voice folder.

    For slot_prefix layouts (Vengeance) the filename must start with
    `<voice_index>_` — otherwise the engine reads it under the WRONG slot
    or doesn't find it at all (no nested folder, flat at Speech/ root).
    We auto-rename if the prefix is missing or wrong, picking up the
    user-supplied event suffix (e.g. `_001.ogg`) when present. This
    prevents the silent-fail mode where a user drops `999_alert.ogg`
    onto slot 200 and the file lives at `Speech/999_alert.ogg`.

    VFS-aware: routes to the layout the active install uses.
    """
    safe_name = Path(filename).name  # strip any directory components
    if not safe_name:
        raise ValueError("Empty filename")
    if Path(safe_name).suffix.lower() not in {s.lower() for s in WAV_EXTENSIONS}:
        raise ValueError(f"Unsupported audio extension: {safe_name}")

    # Slot-prefix layout filename normalization.
    from .install_context import make_install_context
    ctx = make_install_context(install_root)
    if ctx.flavor.voice_layout == "slot_prefix":
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        expected_prefix = f"{voice_index}_"
        if not stem.startswith(expected_prefix):
            # Preserve the "_<event>" tail if the user-supplied filename
            # already followed the slot-prefix pattern under a different
            # slot — e.g. `999_alert.ogg` becomes `200_alert.ogg`.
            if "_" in stem:
                tail = stem.split("_", 1)[1]
                safe_name = f"{expected_prefix}{tail}{suffix}"
            else:
                # No `_` in the source — treat the whole stem as the event tag.
                safe_name = f"{expected_prefix}{stem}{suffix}"

    dst = _resolve_write_path(install_root, voice_index, safe_name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    # Generate the lip-sync sidecar so JA2 animates the mouth for this clip.
    # Best-effort: a failure here never blocks the clip write.
    gap.write_gap_beside(dst, data)
    return VoiceClip(name=safe_name, size_bytes=len(data), path=str(dst.resolve()))


def delete_clip(install_root: Path, voice_index: int, filename: str) -> bool:
    """Delete one clip by name. VFS-aware. Returns True if removed."""
    safe_name = Path(filename).name
    target = _resolve_write_path(install_root, voice_index, safe_name)
    if not target.is_file():
        return False
    try:
        target.unlink()
    except OSError:
        return False
    # Remove the lip-sync sidecar alongside the clip (best-effort).
    gap_sibling = target.with_suffix(".gap")
    try:
        if gap_sibling.is_file():
            gap_sibling.unlink()
    except OSError:
        pass
    return True


def delete_all_clips(install_root: Path, voice_index: int) -> int:
    """Delete every clip for this voice/slot index. VFS-aware.

    For vanilla subdir layout: empties `Speech/<voice_index>/`.
    For slot_prefix layout: deletes every `Speech/<voice_index>_*.ogg`
    at the Speech root.
    """
    from .install_context import make_install_context
    ctx = make_install_context(install_root)
    count = 0
    if ctx.flavor.voice_layout == "slot_prefix":
        prefix = f"{voice_index}_"
        root = ctx.speech_root()
        if not root.is_dir():
            return 0
        audio_exts = {s.lower() for s in WAV_EXTENSIONS}
        for entry in list(root.iterdir()):
            if not (entry.is_file() and entry.name.startswith(prefix)):
                continue
            sfx = entry.suffix.lower()
            if sfx in audio_exts:
                try:
                    entry.unlink()
                    count += 1
                except OSError:
                    continue
            elif sfx == ".gap":
                # Lip-sync sidecar — remove alongside its clips, don't count.
                try:
                    entry.unlink()
                except OSError:
                    pass
        return count
    # vanilla subdir layout
    sd = speech_dir(install_root, voice_index)
    if not sd.is_dir():
        return 0
    audio_exts = {s.lower() for s in WAV_EXTENSIONS}
    for entry in list(sd.iterdir()):
        if not entry.is_file():
            continue
        sfx = entry.suffix.lower()
        if sfx in audio_exts:
            try:
                entry.unlink()
                count += 1
            except OSError:
                continue
        elif sfx == ".gap":
            # Lip-sync sidecar — remove alongside its clips, don't count.
            try:
                entry.unlink()
            except OSError:
                pass
    return count
