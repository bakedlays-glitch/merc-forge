"""Save-game scanner: detect which `.SAV` files reference a given merc.

JA2's save format is binary and version-dependent. For v1 we use a
lightweight UTF-16LE string-match approach: scan each save file's raw bytes
for the merc's nickname as a UTF-16LE encoded substring. This is fast,
robust across mod variants, and good enough to surface a save-compatibility
warning in the UI.

Save folder paths (in order of probing):
    %USERPROFILE%/Documents/JA2_113_SavedGames/
    %USERPROFILE%/Documents/JA2 Saved Games/
    %APPDATA%/JA2/Saves/
    %LOCALAPPDATA%/JA2/Saves/
    plus an optional override from Ja2_Options.ini [Data File Settings] SaveGameFolder

Important UX caveat (and the reason the scanner exists at all):
    The engine snapshots a merc's stats into the SOLDIERTYPE struct when the
    merc is hired. Save files contain those snapshots. Editing the merc's
    MercProfiles.xml entry post-hire does NOT retroactively update those
    snapshots — only NEW hires get the new stats. The wizard surfaces this
    as a banner when the player edits/moves/deletes a merc found in any save.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Saves directory candidates (resolved at call time so env vars can change)
def _candidate_save_folders(extra: Optional[Path] = None) -> list[Path]:
    candidates: list[Path] = []
    home = Path.home()
    docs = home / "Documents"
    candidates.append(docs / "JA2_113_SavedGames")
    candidates.append(docs / "JA2 Saved Games")
    if "APPDATA" in os.environ:
        candidates.append(Path(os.environ["APPDATA"]) / "JA2" / "Saves")
    if "LOCALAPPDATA" in os.environ:
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "JA2" / "Saves")
    if extra is not None:
        candidates.append(Path(extra))
    return [c for c in candidates if c.is_dir()]


@dataclass
class SaveFile:
    path: Path
    modified: float    # mtime
    size: int


def list_saves(extra_save_folder: Optional[Path] = None) -> list[SaveFile]:
    """List every `.SAV` file in the candidate save folders, newest first."""
    out: list[SaveFile] = []
    for folder in _candidate_save_folders(extra_save_folder):
        for entry in folder.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".sav":
                continue
            try:
                stat = entry.stat()
                out.append(SaveFile(path=entry, modified=stat.st_mtime, size=stat.st_size))
            except OSError:
                continue
    out.sort(key=lambda s: s.modified, reverse=True)
    return out


def find_refs_in_save(save_path: Path, nicknames: list[str], min_nick_len: int = 3) -> list[str]:
    """Scan one `.SAV` for UTF-16LE encoded matches of any given nickname.

    Returns the list of nicknames found. Skips nicknames shorter than
    `min_nick_len` to reduce false positives (a 1-2 letter nickname like
    "Vi" would match many byte sequences).
    """
    try:
        data = save_path.read_bytes()
    except OSError:
        return []
    hits: list[str] = []
    for nick in nicknames:
        if len(nick) < min_nick_len:
            continue
        needle = nick.encode("utf-16-le")
        if needle in data:
            hits.append(nick)
    return hits


def scan_saves_for_mercs(
    nicknames_by_slot: dict[int, str],
    extra_save_folder: Optional[Path] = None,
    min_nick_len: int = 3,
) -> dict[int, list[Path]]:
    """For each slot, find the save files where that slot's nickname appears.

    Args:
        nicknames_by_slot: {uiIndex: zNickname} from the live roster.
        extra_save_folder: optional override (from Ja2_Options.ini).
        min_nick_len: ignore nicknames shorter than this (default 3) to
            reduce false positives.

    Returns:
        {uiIndex: [save_paths]} for every slot that appears in at least one
        save. Slots with no matches are omitted.
    """
    saves = list_saves(extra_save_folder)
    if not saves:
        return {}

    valid_nicknames = {
        slot: nick for slot, nick in nicknames_by_slot.items()
        if nick and len(nick) >= min_nick_len
    }
    if not valid_nicknames:
        return {}

    # Precompute UTF-16LE needles
    needles = [(slot, nick.encode("utf-16-le")) for slot, nick in valid_nicknames.items()]

    results: dict[int, list[Path]] = {}
    for save in saves:
        try:
            data = save.path.read_bytes()
        except OSError:
            continue
        for slot, needle in needles:
            if needle in data:
                results.setdefault(slot, []).append(save.path)
    return results
