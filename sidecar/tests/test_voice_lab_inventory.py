"""Inventory contracts for the Voice Lab's VFS-aware source scan."""
from __future__ import annotations

from pathlib import Path

import pytest

from ja2py.fileformats.SlfFS import BufferedSlfFS

from mercwizard_core.voice_lab.inventory import (
    read_asset_bytes,
    scan_inventory,
    scan_profile_catalog,
)
from mercwizard_core.voice_lab.triggers import load_trigger_catalog


def _write_profiles(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = (
        (9, 1, "Mike", "Mike", 9, 9),
        (42, 2, "Flatline", "Flat", 42, 42),
        (51, 4, "Deathclaw", "Claw", 51, 197),
        (108, 1, "King", "King", 108, 108),
        (197, 3, "Goris", "Goris", 197, 197),
    )
    rows = "\n".join(
        "<PROFILE>"
        f"<uiIndex>{profile_id}</uiIndex>"
        f"<Type>{profile_type}</Type>"
        f"<zName>{name}</zName>"
        f"<zNickname>{nickname}</zNickname>"
        f"<ubFaceIndex>{face_index}</ubFaceIndex>"
        f"<usVoiceIndex>{voice_index}</usVoiceIndex>"
        "</PROFILE>"
        for profile_id, profile_type, name, nickname, face_index, voice_index in profiles
    )
    path.write_text(f"<MERCPROFILES>{rows}</MERCPROFILES>", encoding="utf-8")


def _pack_slf(path: Path, entries: list[tuple[str, bytes]]) -> None:
    """Write a synthetic archive with the same BufferedSlfFS test helper used elsewhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    slf = BufferedSlfFS()
    slf.library_name = "VOICE_TEST"
    slf.library_path = path.name
    for relpath, data in entries:
        member = "/" + relpath.lstrip("/").replace("\\", "/")
        parent = "/".join(member.strip("/").split("/")[:-1])
        if parent:
            slf.makedirs("/" + parent, recreate=True)
        with slf.open(member, "wb") as stream:
            stream.write(data)
    with path.open("wb") as stream:
        slf.save(stream)


@pytest.fixture
def fake_voice_install(tmp_path: Path) -> Path:
    install = tmp_path / "voice-install"
    data = install / "Data-1.13"
    _write_profiles(data / "TableData" / "MercProfiles.xml")
    speech = data / "Speech"
    speech.mkdir(parents=True)
    (speech / "108_111.wav").write_bytes(b"fixture-wav")
    return install


@pytest.fixture
def fake_slf_voice_install(tmp_path: Path) -> Path:
    install = tmp_path / "slf-voice-install"
    data = install / "Data-1.13"
    _write_profiles(data / "TableData" / "MercProfiles.xml")
    _pack_slf(data / "Speech.slf", [("9/MERC009_112.wav", b"slf-speech")])
    return install


def test_inventory_groups_shared_voice_index(fake_voice_install: Path) -> None:
    """Removing one profile from a shared bank must change the bank's owners."""
    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())

    bank = snapshot.bank(197)

    assert [profile.profile_id for profile in bank.profiles] == [51, 197]


def test_profile_catalog_publishes_names_without_hashing_audio(
    fake_voice_install: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The merc picker must not wait for any voice-file hashing."""
    from mercwizard_core.voice_lab import inventory

    monkeypatch.setattr(
        inventory,
        "_content_sha256",
        lambda _data: (_ for _ in ()).throw(AssertionError("catalog hashed audio")),
    )

    snapshot = scan_profile_catalog(
        "fixture", fake_voice_install, load_trigger_catalog(),
    )

    assert [bank.voice_index for bank in snapshot.banks] == [9, 42, 108, 197]
    assert snapshot.bank(108).profiles[0].name == "King"
    assert all(bank.lines == [] for bank in snapshot.banks)


def test_targeted_inventory_hashes_only_the_selected_bank(
    fake_voice_install: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choosing one merc must not read every other merc's recordings."""
    speech = fake_voice_install / "Data-1.13" / "Speech"
    (speech / "42_111.wav").write_bytes(b"other-bank")
    hashed: list[bytes] = []
    from mercwizard_core.voice_lab import inventory

    real_hash = inventory._content_sha256
    monkeypatch.setattr(
        inventory,
        "_content_sha256",
        lambda data: hashed.append(data) or real_hash(data),
    )

    snapshot = scan_inventory(
        "fixture", fake_voice_install, load_trigger_catalog(), voice_indexes={108},
    )

    assert [bank.voice_index for bank in snapshot.banks] == [108]
    assert hashed == [b"fixture-wav"]


def test_mp3_wins_same_stem(fake_voice_install: Path) -> None:
    """Changing extension priority must change the playable audio winner."""
    speech = fake_voice_install / "Data-1.13" / "Speech"
    (speech / "108_111.wav").write_bytes(b"wav")
    (speech / "108_111.ogg").write_bytes(b"ogg")
    (speech / "108_111.mp3").write_bytes(b"mp3")

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())
    line = snapshot.line("speech", 108, "111")

    assert line.audio_winner is not None
    assert line.audio_winner.extension == ".mp3"
    assert {asset.extension for asset in line.audio_variants} == {".mp3", ".ogg", ".wav"}


def test_subdirectory_speech_uses_directory_voice_and_filename_quote(
    fake_voice_install: Path,
) -> None:
    """Treating a nested clip as a root flat filename loses its voice-bank identity."""
    speech = fake_voice_install / "Data-1.13" / "Speech" / "42"
    speech.mkdir()
    (speech / "MERC042_112.wav").write_bytes(b"nested-speech")

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())
    line = snapshot.line("speech", 42, "112")

    assert line.audio_winner is not None
    assert read_asset_bytes(line.audio_winner) == b"nested-speech"


def test_gap_is_attached_to_the_winning_audio_source(fake_voice_install: Path) -> None:
    """Associating a gap with a shadowed audio variant desynchronizes mouth animation."""
    speech = fake_voice_install / "Data-1.13" / "Speech"
    (speech / "108_111.mp3").write_bytes(b"winning-mp3")
    (speech / "108_111.gap").write_bytes(b"lip-sync")

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())
    line = snapshot.line("speech", 108, "111")

    assert line.audio_winner is not None
    assert line.audio_winner.extension == ".mp3"
    assert line.gap_winner is not None
    assert read_asset_bytes(line.gap_winner) == b"lip-sync"


def test_higher_vfs_layer_wins_same_extension(fake_voice_install: Path) -> None:
    """Ignoring VFS layer order can present a shadowed clip as playable."""
    lower_speech = fake_voice_install / "Data" / "Speech"
    lower_speech.mkdir(parents=True)
    (lower_speech / "108_111.wav").write_bytes(b"lower-layer")

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())
    winner = snapshot.line("speech", 108, "111").audio_winner

    assert winner is not None
    assert read_asset_bytes(winner) == b"fixture-wav"


def test_orphaned_gap_is_retained_as_an_incomplete_speech_line(
    fake_voice_install: Path,
) -> None:
    """Dropping a gap-only stem hides the broken audio/gap set from later audits."""
    speech = fake_voice_install / "Data-1.13" / "Speech"
    (speech / "108_112.gap").write_bytes(b"orphan-gap")

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())
    line = snapshot.line("speech", 108, "112")

    assert line.audio_winner is None
    assert line.gap_winner is not None
    assert read_asset_bytes(line.gap_winner) == b"orphan-gap"


def test_battle_and_dialogue_assets_join_their_voice_bank(fake_voice_install: Path) -> None:
    """Skipping battle or MercEdt sources leaves the bank audit incomplete."""
    data = fake_voice_install / "Data-1.13"
    battle = data / "Battlesnds"
    battle.mkdir()
    (battle / "108_DIE_1.ogg").write_bytes(b"battle")
    merc_edt = data / "MercEdt"
    merc_edt.mkdir()
    (merc_edt / "108.EDT").write_bytes(b"dialogue")

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())

    assert snapshot.line("battle", 108, "DIE_1").audio_winner is not None
    assert snapshot.line("dialogue_edt", 108, "document").dialogue_edt is not None


def test_slf_member_is_read_only_source(fake_slf_voice_install: Path) -> None:
    """Treating archive audio as writable would corrupt the shipped source layer."""
    snapshot = scan_inventory("fixture", fake_slf_voice_install, load_trigger_catalog())
    asset = snapshot.line("speech", 9, "112").audio_winner

    assert asset is not None
    assert asset.source_kind == "slf"
    assert asset.writable is False
    assert read_asset_bytes(asset) == b"slf-speech"


def test_slf_gap_is_not_mistaken_for_playable_audio(fake_slf_voice_install: Path) -> None:
    """Classifying an archive .gap as speech can select it as an audio winner."""
    _pack_slf(
        fake_slf_voice_install / "Data-1.13" / "Speech.slf",
        [
            ("9/MERC009_112.wav", b"slf-speech"),
            ("9/MERC009_112.gap", b"slf-gap"),
        ],
    )

    snapshot = scan_inventory("fixture", fake_slf_voice_install, load_trigger_catalog())
    line = snapshot.line("speech", 9, "112")

    assert line.audio_winner is not None
    assert line.audio_winner.extension == ".wav"
    assert line.gap_winner is not None
    assert read_asset_bytes(line.gap_winner) == b"slf-gap"


def test_inventory_reads_explicit_slf_vfs_mount(fake_voice_install: Path) -> None:
    """Ignoring a TYPE=SLF mount makes configured archive-only banks invisible."""
    _pack_slf(
        fake_voice_install / "Archives" / "VoicePack.slf",
        [("Speech/9/MERC009_113.wav", b"explicit-vfs-slf")],
    )
    (fake_voice_install / "Ja2.ini").write_text(
        "[Ja2 Settings]\nVFS_CONFIG_INI = vfs_config.voice.ini\n",
        encoding="utf-8",
    )
    (fake_voice_install / "vfs_config.voice.ini").write_text(
        "[vfs_config]\nPROFILES = v113, VoiceArchive\n"
        "[PROFILE_v113]\nLOCATIONS = data_dir\n"
        "[PROFILE_VoiceArchive]\nLOCATIONS = voice_slf\n"
        "[LOC_data_dir]\nTYPE = DIRECTORY\nPATH = Data-1.13\n"
        "[LOC_voice_slf]\nTYPE = SLF\nPATH = Archives/VoicePack.slf\n",
        encoding="utf-8",
    )

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())
    asset = snapshot.line("speech", 9, "113").audio_winner

    assert asset is not None
    assert asset.source_kind == "slf"
    assert read_asset_bytes(asset) == b"explicit-vfs-slf"


def test_npc_speech_archive_is_excluded(fake_slf_voice_install: Path) -> None:
    """Classifying NPC_SPEECH archive members as merc speech leaks world-NPC lines."""
    _pack_slf(
        fake_slf_voice_install / "Data-1.13" / "Speech.slf",
        [
            ("9/MERC009_112.wav", b"merc-speech"),
            ("NPC_SPEECH/9_114.wav", b"nested-npc-speech"),
        ],
    )
    _pack_slf(
        fake_slf_voice_install / "Data-1.13" / "NPC_SPEECH.slf",
        [("9_113.wav", b"npc-speech")],
    )

    snapshot = scan_inventory("fixture", fake_slf_voice_install, load_trigger_catalog())

    with pytest.raises(KeyError, match="unknown speech line 9:113"):
        snapshot.line("speech", 9, "113")
    with pytest.raises(KeyError, match="unknown speech line 9:114"):
        snapshot.line("speech", 9, "114")


def test_battle_gap_stays_with_battle_line(fake_voice_install: Path) -> None:
    """Collapsing battle gaps into speech lines disconnects their lip-sync evidence."""
    battle = fake_voice_install / "Data-1.13" / "Battlesnds"
    battle.mkdir()
    (battle / "108_DIE_1.ogg").write_bytes(b"battle-audio")
    (battle / "108_DIE_1.gap").write_bytes(b"battle-gap")

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())
    battle_line = snapshot.line("battle", 108, "DIE_1")

    assert battle_line.gap_winner is not None
    assert read_asset_bytes(battle_line.gap_winner) == b"battle-gap"
    with pytest.raises(KeyError, match="unknown speech line 108:DIE_1"):
        snapshot.line("speech", 108, "DIE_1")


def test_direct_slf_mount_is_not_scanned_again_through_its_parent_directory(
    fake_voice_install: Path,
) -> None:
    """Scanning one archive through two VFS declarations must not duplicate variants."""
    _pack_slf(
        fake_voice_install / "Data-1.13" / "Speech.slf",
        [("9/MERC009_115.wav", b"one-archive")],
    )
    (fake_voice_install / "Ja2.ini").write_text(
        "[Ja2 Settings]\nVFS_CONFIG_INI = vfs_config.voice.ini\n",
        encoding="utf-8",
    )
    (fake_voice_install / "vfs_config.voice.ini").write_text(
        "[vfs_config]\nPROFILES = v113, VoiceArchive\n"
        "[PROFILE_v113]\nLOCATIONS = data_dir\n"
        "[PROFILE_VoiceArchive]\nLOCATIONS = voice_slf\n"
        "[LOC_data_dir]\nTYPE = DIRECTORY\nPATH = Data-1.13\n"
        "[LOC_voice_slf]\nTYPE = SLF\nPATH = Data-1.13/Speech.slf\n",
        encoding="utf-8",
    )

    snapshot = scan_inventory("fixture", fake_voice_install, load_trigger_catalog())
    line = snapshot.line("speech", 9, "115")

    assert len(line.audio_variants) == 1
    assert line.audio_winner is not None
    assert line.audio_winner.layer_rank == 0


def test_inventory_cancellation_stops_during_loose_hashing(fake_voice_install: Path, monkeypatch) -> None:
    """A canceled quick scan must stop before reading every loose input."""
    speech = fake_voice_install / "Data-1.13" / "Speech"
    (speech / "108_112.wav").write_bytes(b"second-loose")
    hashes: list[bytes] = []
    from mercwizard_core.voice_lab import inventory

    real_hash = inventory._content_sha256
    monkeypatch.setattr(inventory, "_content_sha256", lambda data: hashes.append(data) or real_hash(data))
    progress: list[tuple[int, int, str]] = []

    snapshot = scan_inventory(
        "fixture", fake_voice_install, load_trigger_catalog(),
        progress=lambda completed, total, message: progress.append((completed, total, message)),
        cancelled=lambda: len(progress) > 1,
    )

    assert snapshot is None
    assert len(hashes) == 1
    assert progress[0][2] == "scanning loose voice files"


def test_inventory_cancellation_stops_during_slf_hashing(fake_slf_voice_install: Path, monkeypatch) -> None:
    """Archive enumeration must honor the same cooperative cancellation contract."""
    _pack_slf(
        fake_slf_voice_install / "Data-1.13" / "Speech.slf",
        [("9/MERC009_112.wav", b"first-slf"), ("9/MERC009_113.wav", b"second-slf")],
    )
    hashes: list[bytes] = []
    from mercwizard_core.voice_lab import inventory

    real_hash = inventory._content_sha256
    monkeypatch.setattr(inventory, "_content_sha256", lambda data: hashes.append(data) or real_hash(data))
    progress: list[tuple[int, int, str]] = []

    snapshot = scan_inventory(
        "fixture", fake_slf_voice_install, load_trigger_catalog(),
        progress=lambda completed, total, message: progress.append((completed, total, message)),
        cancelled=lambda: len(progress) > 1,
    )

    assert snapshot is None
    assert len(hashes) == 1
    assert progress[0][2] == "scanning archived voice files"
