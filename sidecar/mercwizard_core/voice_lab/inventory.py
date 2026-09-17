"""VFS-aware discovery of Voice Lab assets without mutating an install."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Callable, Iterable, Iterator, Literal, Protocol

from ja2py.fileformats.SlfFS import SlfEntry, SlfFS, SlfHeader

from mercwizard_core.inject import profiles_xml
from mercwizard_core.install_context import make_install_context

from .models import (
    InventorySnapshot,
    TriggerMeaning,
    VoiceAsset,
    VoiceBank,
    VoiceLine,
    VoiceProfile,
)
from .triggers import TriggerCatalog


_AUDIO_EXTENSIONS = (".mp3", ".ogg", ".wav")
_AUDIO_EXTENSION_PRIORITY = {extension: priority for priority, extension in enumerate(_AUDIO_EXTENSIONS)}
_FLAT_CLIP = re.compile(r"^(?P<voice>\d+)_(?P<line>.+)$")


@dataclass(frozen=True)
class _Candidate:
    """A scanned source plus the bytes already read to calculate its hash."""

    asset: VoiceAsset
    container_key: str
    origin_family: Literal["speech", "battle", "dialogue_edt"]


class FingerprintCache(Protocol):
    """The narrow cache boundary needed by a read-only inventory scan."""

    def cached_sha256(self, source_locator: str, size_bytes: int, mtime_ns: int) -> str | None:
        """Return a hash only when the full file/member fingerprint matches."""

    def remember_fingerprint(
        self, source_locator: str, size_bytes: int, mtime_ns: int, sha256_hex: str
    ) -> None:
        """Remember the hash for one observed loose file or archive member."""


def scan_inventory(
    install_id: str,
    install_root: Path,
    catalog: TriggerCatalog,
    fingerprint_cache: FingerprintCache | None = None,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    voice_indexes: set[int] | None = None,
) -> InventorySnapshot | None:
    """Scan loose and SLF voice sources in the install's engine VFS order.

    The scan is deliberately read-only.  Archive members are catalogued as
    immutable inputs; a later deployment writes a loose override rather than
    modifying an SLF in place.
    """
    report_progress = progress or (lambda completed, total, message: None)
    is_cancelled = cancelled or (lambda: False)
    if is_cancelled():
        return None
    context = make_install_context(Path(install_root))
    profiles = _profiles_for_context(context)
    if voice_indexes is not None:
        profiles = [profile for profile in profiles if profile.voice_index in voice_indexes]
    candidates: list[_Candidate] = []
    scanned_archives: set[Path] = set()
    completed = 0

    def checkpoint(message: str) -> bool:
        nonlocal completed
        if is_cancelled():
            return False
        completed += 1
        report_progress(completed, 0, message)
        return not is_cancelled()

    for layer_rank, mount_kind, mount_path in _vfs_mounts(context):
        if is_cancelled():
            return None
        if mount_kind == "directory":
            loose = _scan_loose_layer(
                install_id, mount_path, layer_rank, fingerprint_cache, checkpoint,
                voice_indexes,
            )
            if loose is None:
                return None
            candidates.extend(loose)
            archives = _scan_slf_layer(
                install_id, mount_path, layer_rank, scanned_archives, fingerprint_cache, checkpoint,
                voice_indexes,
            )
            if archives is None:
                return None
            candidates.extend(archives)
        else:
            if mount_path not in scanned_archives:
                scanned_archives.add(mount_path)
                archive = _scan_slf_archive(
                    install_id, mount_path, layer_rank, fingerprint_cache, checkpoint,
                    voice_indexes,
                )
                if archive is None:
                    return None
                candidates.extend(archive)
    if is_cancelled():
        return None
    return _build_snapshot(install_id, profiles, candidates, catalog)


def scan_profile_catalog(
    install_id: str,
    install_root: Path,
    catalog: TriggerCatalog,
) -> InventorySnapshot:
    """Read the merc/voice ownership catalog without touching any audio files."""
    context = make_install_context(Path(install_root))
    return _build_snapshot(
        install_id,
        _profiles_for_context(context),
        (),
        catalog,
    )


def read_asset_bytes(asset: VoiceAsset) -> bytes:
    """Read a previously-inventoried loose file or immutable SLF member."""
    if asset.source_kind == "loose":
        return Path(asset.source_locator).read_bytes()
    locator = json.loads(asset.source_locator)
    archive = Path(locator["archive"])
    member = locator["member"]
    filesystem = SlfFS(str(archive))
    try:
        with filesystem.openbin(member, "rb") as stream:
            return stream.read()
    finally:
        filesystem.close()


def _profiles_for_context(context) -> list[VoiceProfile]:
    raw_slots = profiles_xml.read_all_slots(context.profiles_xml_path())
    profiles: list[VoiceProfile] = []
    for profile_id, raw in raw_slots.items():
        name = (raw.get("zName") or "").strip()
        nickname = (raw.get("zNickname") or "").strip()
        if not name and not nickname:
            continue
        voice_index = _int_or(raw.get("usVoiceIndex"), profile_id)
        profiles.append(
            VoiceProfile(
                profile_id=profile_id,
                profile_type=_int_or(raw.get("Type"), 0),
                name=name,
                nickname=nickname,
                face_index=_optional_int(raw.get("ubFaceIndex")),
                voice_index=voice_index,
            )
        )
    return sorted(profiles, key=lambda profile: profile.profile_id)


def _vfs_mounts(context) -> Iterator[tuple[int, Literal["directory", "slf"], Path]]:
    """Yield readable directory and direct-SLF mounts in engine priority order."""
    layer_rank = 0
    seen: set[tuple[str, Path]] = set()
    for profile in reversed(context.layout.profiles):
        for location in profile.locations:
            try:
                path = location.path.resolve()
            except OSError:
                continue
            mount_kind: Literal["directory", "slf"] | None = None
            if location.is_directory and path.is_dir():
                mount_kind = "directory"
            elif location.type.upper() == "SLF" and path.is_file():
                mount_kind = "slf"
            if mount_kind is None or (mount_kind, path) in seen:
                continue
            seen.add((mount_kind, path))
            yield layer_rank, mount_kind, path
            layer_rank += 1


def _scan_loose_layer(
    install_id: str,
    root: Path,
    layer_rank: int,
    fingerprint_cache: FingerprintCache | None,
    checkpoint: Callable[[str], bool],
    voice_indexes: set[int] | None,
) -> list[_Candidate] | None:
    candidates: list[_Candidate] = []
    speech = _child_ci(root, "Speech")
    if speech is not None:
        speech_files = (
            _files_under(speech)
            if voice_indexes is None
            else _targeted_speech_files(speech, voice_indexes)
        )
        for path in speech_files:
            identity = _speech_identity(path.relative_to(speech))
            if identity is not None and (
                voice_indexes is None or identity[0] in voice_indexes
            ):
                message = "scanning loose voice files" if voice_indexes is None else "scanning selected merc voice files"
                if not checkpoint(message):
                    return None
                candidates.append(
                    _loose_candidate(
                        install_id, "speech", *identity, path, layer_rank, fingerprint_cache
                    )
                )
    battle = _child_ci(root, "Battlesnds")
    if battle is not None:
        battle_files = (
            _files_under(battle)
            if voice_indexes is None
            else _targeted_flat_files(battle, voice_indexes)
        )
        for path in battle_files:
            identity = _flat_identity(path.name)
            if identity is not None and (
                voice_indexes is None or identity[0] in voice_indexes
            ):
                message = "scanning loose voice files" if voice_indexes is None else "scanning selected merc voice files"
                if not checkpoint(message):
                    return None
                candidates.append(
                    _loose_candidate(
                        install_id, "battle", *identity, path, layer_rank, fingerprint_cache
                    )
                )
    for relative in (Path("MercEdt"), Path("BinaryData") / "MercEdt"):
        dialogue = _path_ci(root, relative)
        if dialogue is None:
            continue
        dialogue_files = (
            _files_under(dialogue)
            if voice_indexes is None
            else _targeted_dialogue_files(dialogue, voice_indexes)
        )
        for path in dialogue_files:
            if path.suffix.lower() != ".edt" or not path.stem.isdigit():
                continue
            voice_index = int(path.stem)
            if voice_indexes is not None and voice_index not in voice_indexes:
                continue
            message = "scanning loose voice files" if voice_indexes is None else "scanning selected merc voice files"
            if not checkpoint(message):
                return None
            candidates.append(
                _loose_candidate(
                    install_id,
                    "dialogue_edt",
                    voice_index,
                    "document",
                    path,
                    layer_rank,
                    fingerprint_cache,
                )
            )
    return candidates


def _scan_slf_layer(
    install_id: str,
    root: Path,
    layer_rank: int,
    scanned_archives: set[Path],
    fingerprint_cache: FingerprintCache | None,
    checkpoint: Callable[[str], bool],
    voice_indexes: set[int] | None,
) -> list[_Candidate] | None:
    try:
        archives = sorted(
            (path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".slf"),
            key=lambda path: path.name.lower(),
        )
    except OSError:
        return []
    candidates: list[_Candidate] = []
    for archive in archives:
        try:
            resolved_archive = archive.resolve()
        except OSError:
            continue
        if resolved_archive in scanned_archives:
            continue
        scanned_archives.add(resolved_archive)
        scanned = _scan_slf_archive(
            install_id, archive, layer_rank, fingerprint_cache, checkpoint, voice_indexes,
        )
        if scanned is None:
            return None
        candidates.extend(scanned)
    return candidates


def _scan_slf_archive(
    install_id: str,
    archive: Path,
    layer_rank: int,
    fingerprint_cache: FingerprintCache | None,
    checkpoint: Callable[[str], bool],
    voice_indexes: set[int] | None,
) -> list[_Candidate] | None:
    """Read applicable members directly from the SLF table without building a MemoryFS."""
    candidates: list[_Candidate] = []
    try:
        archive_stat = archive.stat()
        stream = archive.open("rb")
        header = SlfHeader.from_bytes(stream.read(SlfHeader.get_size()))
        entry_size = SlfEntry.get_size()
        stream.seek(-entry_size * header["number_of_entries"], 2)
        entries = [
            SlfEntry.from_bytes(stream.read(entry_size))
            for _ in range(header["number_of_entries"])
        ]
    except Exception:
        return candidates
    try:
        resolved_archive = str(archive.resolve())
        for entry in entries:
            member = entry["file_name"].replace("\\", "/")
            identity = _slf_identity(archive.name, member)
            if identity is None:
                continue
            family, voice_index, line_id = identity
            if voice_indexes is not None and voice_index not in voice_indexes:
                continue
            message = "scanning archived voice files" if voice_indexes is None else "scanning selected merc voice files"
            if not checkpoint(message):
                return None
            asset_family: Literal["speech", "battle", "gap", "dialogue_edt"] = (
                "gap" if Path(member).suffix.lower() == ".gap" else family
            )
            locator = json.dumps(
                {"archive": resolved_archive, "member": member},
                separators=(",", ":"),
                sort_keys=True,
            )
            size_bytes = int(entry["length"])
            sha256_hex = (
                fingerprint_cache.cached_sha256(locator, size_bytes, archive_stat.st_mtime_ns)
                if fingerprint_cache is not None and size_bytes >= 0
                else None
            )
            if sha256_hex is None:
                try:
                    stream.seek(int(entry["offset"]))
                    data = stream.read(size_bytes)
                except Exception:
                    continue
                size_bytes = len(data)
                sha256_hex = _content_sha256(data)
                if fingerprint_cache is not None:
                    fingerprint_cache.remember_fingerprint(
                        locator, size_bytes, archive_stat.st_mtime_ns, sha256_hex
                    )
            asset = _asset_from_bytes(
                install_id=install_id,
                family=asset_family,
                voice_index=voice_index,
                line_id=line_id,
                extension=Path(member).suffix.lower(),
                source_kind="slf",
                source_locator=locator,
                layer_rank=layer_rank,
                size_bytes=size_bytes,
                mtime_ns=archive_stat.st_mtime_ns,
                sha256_hex=sha256_hex,
                writable=False,
            )
            candidates.append(_Candidate(asset, resolved_archive, family))
    except Exception:
        return candidates
    finally:
        try:
            stream.close()
        except Exception:
            pass
    return candidates


def _speech_identity(relative: Path) -> tuple[int, str] | None:
    """Return logical voice and quote IDs for flat or directory speech clips."""
    suffix = relative.suffix.lower()
    if suffix not in {*_AUDIO_EXTENSIONS, ".gap"}:
        return None
    parts = relative.parts
    if len(parts) > 1 and parts[0].isdigit():
        line_id = _filename_line_id(Path(parts[-1]).stem)
        return (int(parts[0]), line_id) if line_id else None
    return _flat_identity(relative.name)


def _flat_identity(filename: str) -> tuple[int, str] | None:
    path = Path(filename)
    if path.suffix.lower() not in {*_AUDIO_EXTENSIONS, ".gap"}:
        return None
    match = _FLAT_CLIP.match(path.stem)
    if match is None:
        return None
    return int(match.group("voice")), match.group("line")


def _filename_line_id(stem: str) -> str | None:
    if "_" not in stem:
        return None
    return stem.rsplit("_", 1)[1] or None


def _slf_identity(archive_name: str, member: str) -> tuple[str, int, str] | None:
    normalized = member.strip("/").replace("\\", "/")
    path = Path(normalized)
    parts = tuple(part.lower() for part in path.parts)
    archive_lower = archive_name.lower()
    if (
        "npc_speech" in archive_lower
        or "npcdata" in archive_lower
        or "npc_speech" in parts
        or "npcdata" in parts
    ):
        return None
    if path.suffix.lower() == ".edt" and path.stem.isdigit() and (
        "mercedt" in parts or "mercedt" in archive_lower
    ):
        return "dialogue_edt", int(path.stem), "document"
    if "battlesnds" in parts or "battle" in archive_lower:
        identity = _flat_identity(path.name)
        return ("battle", *identity) if identity is not None else None
    if "speech" in parts:
        speech_index = parts.index("speech")
        relative = Path(*path.parts[speech_index + 1:])
        identity = _speech_identity(relative)
    elif "speech" in archive_lower:
        identity = _speech_identity(path)
    else:
        identity = None
    return ("speech", *identity) if identity is not None else None


def _loose_candidate(
    install_id: str,
    family: Literal["speech", "battle", "dialogue_edt"],
    voice_index: int,
    line_id: str,
    path: Path,
    layer_rank: int,
    fingerprint_cache: FingerprintCache | None,
) -> _Candidate:
    stat = path.stat()
    source_locator = str(path.resolve())
    sha256_hex = (
        fingerprint_cache.cached_sha256(source_locator, stat.st_size, stat.st_mtime_ns)
        if fingerprint_cache is not None
        else None
    )
    if sha256_hex is None:
        data = path.read_bytes()
        stat = path.stat()
        sha256_hex = _content_sha256(data)
        if fingerprint_cache is not None:
            fingerprint_cache.remember_fingerprint(
                source_locator, stat.st_size, stat.st_mtime_ns, sha256_hex
            )
    asset_family: Literal["speech", "battle", "gap", "dialogue_edt"] = (
        "gap" if path.suffix.lower() == ".gap" else family
    )
    asset = _asset_from_bytes(
        install_id=install_id,
        family=asset_family,
        voice_index=voice_index,
        line_id=line_id,
        extension=path.suffix.lower(),
        source_kind="loose",
        source_locator=source_locator,
        layer_rank=layer_rank,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256_hex=sha256_hex,
        writable=True,
    )
    return _Candidate(asset, str(path.resolve().parent), family)


def _content_sha256(data: bytes) -> str:
    """Hash file bytes only when the cache cannot prove the input is unchanged."""
    return sha256(data).hexdigest()


def _asset_from_bytes(
    *,
    install_id: str,
    family: Literal["speech", "battle", "gap", "dialogue_edt"],
    voice_index: int,
    line_id: str,
    extension: str,
    source_kind: Literal["loose", "slf"],
    source_locator: str,
    layer_rank: int,
    size_bytes: int,
    mtime_ns: int,
    sha256_hex: str,
    writable: bool,
) -> VoiceAsset:
    asset_id_material = "\0".join(
        (install_id, family, str(voice_index), line_id, extension, source_kind, source_locator)
    )
    return VoiceAsset(
        asset_id=sha256(asset_id_material.encode("utf-8")).hexdigest(),
        family=family,
        voice_index=voice_index,
        line_id=line_id,
        extension=extension,
        source_kind=source_kind,
        source_locator=source_locator,
        layer_rank=layer_rank,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        sha256=sha256_hex,
        writable=writable,
        winner=False,
    )


def _build_snapshot(
    install_id: str,
    profiles: list[VoiceProfile],
    candidates: Iterable[_Candidate],
    catalog: TriggerCatalog,
) -> InventorySnapshot:
    profiles_by_voice: dict[int, list[VoiceProfile]] = defaultdict(list)
    for profile in profiles:
        profiles_by_voice[profile.voice_index].append(profile)
    grouped: dict[tuple[str, int, str], list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        asset = candidate.asset
        grouped[(candidate.origin_family, asset.voice_index, asset.line_id)].append(candidate)
    voice_indices = set(profiles_by_voice)
    voice_indices.update(voice_index for _family, voice_index, _line_id in grouped)
    banks: list[VoiceBank] = []
    for voice_index in sorted(voice_indices):
        attached_profiles = sorted(profiles_by_voice.get(voice_index, []), key=lambda profile: profile.profile_id)
        lines = _lines_for_bank(voice_index, attached_profiles, grouped, catalog)
        banks.append(VoiceBank(voice_index=voice_index, profiles=attached_profiles, lines=lines))
    return InventorySnapshot(install_id=install_id, banks=banks)


def _lines_for_bank(
    voice_index: int,
    profiles: list[VoiceProfile],
    grouped: dict[tuple[str, int, str], list[_Candidate]],
    catalog: TriggerCatalog,
) -> list[VoiceLine]:
    line_keys = {key for key in grouped if key[1] == voice_index}
    lines: list[VoiceLine] = []
    for family, _voice_index, line_id in sorted(line_keys, key=lambda key: (key[0], key[2])):
        candidates = grouped[(family, voice_index, line_id)]
        if family == "dialogue_edt":
            variants = _with_winner(candidates, _pick_layer_winner(candidates))
            winner = next(asset for asset in variants if asset.winner)
            lines.append(
                VoiceLine(
                    family="dialogue_edt",
                    voice_index=voice_index,
                    line_id=line_id,
                    dialogue_variants=variants,
                    dialogue_edt=winner,
                )
            )
            continue
        audio_candidates = [candidate for candidate in candidates if candidate.asset.family != "gap"]
        gap_candidates = [candidate for candidate in candidates if candidate.asset.family == "gap"]
        audio_winner_candidate = _pick_audio_winner(audio_candidates) if audio_candidates else None
        audio_variants = _with_winner(audio_candidates, audio_winner_candidate)
        audio_winner = next((asset for asset in audio_variants if asset.winner), None)
        matching_gaps = (
            [gap for gap in gap_candidates if gap.container_key == _winner_container(audio_candidates, audio_winner)]
            if audio_winner is not None
            else gap_candidates
        )
        gap_winner_candidate = _pick_layer_winner(matching_gaps)
        gap_variants = _with_winner(gap_candidates, gap_winner_candidate)
        trigger_by_profile_id = _trigger_meanings(line_id, profiles, catalog) if family == "speech" else {}
        lines.append(
            VoiceLine(
                family=family,
                voice_index=voice_index,
                line_id=line_id,
                audio_variants=audio_variants,
                audio_winner=audio_winner,
                gap_variants=gap_variants,
                gap_winner=(next((asset for asset in gap_variants if asset.winner), None)),
                trigger_by_profile_id=trigger_by_profile_id,
            )
        )
    return lines


def _pick_audio_winner(candidates: list[_Candidate]) -> _Candidate:
    return min(
        candidates,
        key=lambda candidate: (
            _AUDIO_EXTENSION_PRIORITY[candidate.asset.extension],
            candidate.asset.layer_rank,
            0 if candidate.asset.source_kind == "loose" else 1,
            candidate.asset.source_locator,
        ),
    )


def _pick_layer_winner(candidates: list[_Candidate]) -> _Candidate | None:
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: (
            candidate.asset.layer_rank,
            0 if candidate.asset.source_kind == "loose" else 1,
            candidate.asset.source_locator,
        ),
    )


def _with_winner(candidates: list[_Candidate], winner: _Candidate | None) -> list[VoiceAsset]:
    assets = [
        candidate.asset.model_copy(update={"winner": candidate == winner})
        for candidate in candidates
    ]
    return sorted(
        assets,
        key=lambda asset: (asset.layer_rank, asset.extension, asset.source_kind, asset.source_locator),
    )


def _winner_container(candidates: list[_Candidate], winner: VoiceAsset) -> str:
    for candidate in candidates:
        if candidate.asset.asset_id == winner.asset_id:
            return candidate.container_key
    raise RuntimeError("winner candidate was not found")


def _trigger_meanings(
    line_id: str,
    profiles: list[VoiceProfile],
    catalog: TriggerCatalog,
) -> dict[int, TriggerMeaning]:
    if not line_id.isdigit():
        return {}
    slot = int(line_id)
    meanings: dict[int, TriggerMeaning] = {}
    for profile in profiles:
        try:
            trigger = catalog.for_profile(slot, profile.profile_type)
        except ValueError:
            continue
        meanings[profile.profile_id] = TriggerMeaning(
            slot=trigger.slot,
            name=trigger.name,
            meaning=trigger.meaning,
        )
    return meanings


def _child_ci(parent: Path, name: str) -> Path | None:
    return _path_ci(parent, Path(name))


def _path_ci(root: Path, relative: Path) -> Path | None:
    current = root
    for part in relative.parts:
        try:
            current = next(child for child in current.iterdir() if child.name.lower() == part.lower())
        except (OSError, StopIteration):
            return None
    return current if current.is_dir() else None


def _files_under(root: Path) -> Iterator[Path]:
    try:
        yield from (path for path in root.rglob("*") if path.is_file())
    except OSError:
        return


def _targeted_speech_files(root: Path, voice_indexes: set[int]) -> Iterator[Path]:
    """Enumerate only flat clips or numeric subdirectories for selected banks."""
    try:
        for path in root.iterdir():
            identity = _flat_identity(path.name)
            if identity is not None:
                if identity[0] in voice_indexes:
                    yield path
                continue
            if path.name.isdigit() and int(path.name) in voice_indexes and path.is_dir():
                yield from _files_under(path)
    except OSError:
        return


def _targeted_flat_files(root: Path, voice_indexes: set[int]) -> Iterator[Path]:
    """Enumerate selected flat bank prefixes without statting unrelated files."""
    try:
        for path in root.iterdir():
            identity = _flat_identity(path.name)
            if identity is not None and identity[0] in voice_indexes:
                yield path
    except OSError:
        return


def _targeted_dialogue_files(root: Path, voice_indexes: set[int]) -> Iterator[Path]:
    """Resolve selected MercEdt documents from one case-insensitive directory pass."""
    try:
        targets = {f"{voice_index}.edt" for voice_index in voice_indexes}
        for path in root.iterdir():
            if path.name.lower() in targets:
                yield path
    except OSError:
        return


def _int_or(value: str | None, default: int) -> int:
    try:
        return int((value or "").strip())
    except ValueError:
        return default


def _optional_int(value: str | None) -> int | None:
    try:
        return int((value or "").strip())
    except ValueError:
        return None
