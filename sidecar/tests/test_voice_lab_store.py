"""Persistence contracts for Voice Lab inventory and review state."""
from __future__ import annotations

import os
from pathlib import Path
from threading import Barrier, Thread
from typing import Any

import pytest
from ja2py.fileformats.SlfFS import BufferedSlfFS

import mercwizard_core.voice_lab.inventory as inventory_module
import mercwizard_core.voice_lab.store as store_module
from mercwizard_core.voice_lab.triggers import load_trigger_catalog
from mercwizard_core.voice_lab.models import (
    EditRecipe,
    Finding,
    InventorySnapshot,
    Transcription,
    VoiceAsset,
    VoiceBank,
    VoiceLine,
    VoiceProfile,
)
from mercwizard_core.voice_lab.store import VoiceLabStore


def _asset(*, sha256: str = "audio-a", mtime_ns: int = 34) -> VoiceAsset:
    return VoiceAsset(
        asset_id="loose:fixture:108:111.wav",
        family="speech",
        voice_index=108,
        line_id="111",
        extension=".wav",
        source_kind="loose",
        source_locator="C:/fixture/Speech/108_111.wav",
        layer_rank=0,
        size_bytes=12,
        mtime_ns=mtime_ns,
        sha256=sha256,
        writable=True,
        winner=True,
    )


def _snapshot(*, sha256: str = "audio-a", mtime_ns: int = 34) -> InventorySnapshot:
    asset = _asset(sha256=sha256, mtime_ns=mtime_ns)
    return InventorySnapshot(
        install_id="fixture-install",
        banks=[
            VoiceBank(
                voice_index=108,
                lines=[
                    VoiceLine(
                        family="speech",
                        voice_index=108,
                        line_id="111",
                        audio_variants=[asset],
                        audio_winner=asset,
                    )
                ],
            )
        ],
    )


def _profile(profile_id: int, voice_index: int) -> VoiceProfile:
    return VoiceProfile(
        profile_id=profile_id,
        profile_type=1,
        name=f"Merc {profile_id}",
        nickname=f"M{profile_id}",
        face_index=profile_id,
        voice_index=voice_index,
    )


def _bank_with_profiles(voice_index: int, *profile_ids: int) -> VoiceBank:
    asset = _asset().model_copy(update={
        "asset_id": f"loose:fixture:{voice_index}:111.wav",
        "voice_index": voice_index,
        "source_locator": f"C:/fixture/Speech/{voice_index}_111.wav",
    })
    return VoiceBank(
        voice_index=voice_index,
        profiles=[_profile(profile_id, voice_index) for profile_id in profile_ids],
        lines=[VoiceLine(
            family="speech",
            voice_index=voice_index,
            line_id="111",
            audio_variants=[asset],
            audio_winner=asset,
        )],
    )


def _finding(*, evidence_hash: str = "evidence-a", state: str = "needs_review") -> Finding:
    return Finding(
        stable_key="subtitle:108:111",
        evidence_hash=evidence_hash,
        code="SUBTITLE_TRANSCRIPT_MISMATCH",
        severity="high",
        confidence=0.9,
        state=state,
        asset_ids=["loose:fixture:108:111.wav"],
        evidence={"subtitle": "old", "transcript": "new"},
        suggested_action="Review the subtitle.",
    )


def _voice_install_for_scan(tmp_path: Path) -> Path:
    install = tmp_path / "voice-install"
    profiles = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles.parent.mkdir(parents=True)
    profiles.write_text(
        "<MERCPROFILES><PROFILE><uiIndex>108</uiIndex><Type>1</Type>"
        "<zName>King</zName><zNickname>King</zNickname>"
        "<usVoiceIndex>108</usVoiceIndex></PROFILE></MERCPROFILES>",
        encoding="utf-8",
    )
    speech = install / "Data-1.13" / "Speech"
    speech.mkdir()
    (speech / "108_111.wav").write_bytes(b"fixture-wav")
    return install


def _pack_slf(path: Path, member: str, data: bytes) -> None:
    archive = BufferedSlfFS()
    archive.library_name = "VOICE_TEST"
    archive.library_path = path.name
    archive.makedirs("/9", recreate=True)
    with archive.open("/" + member.lstrip("/"), "wb") as stream:
        stream.write(data)
    with path.open("wb") as stream:
        archive.save(stream)


def _slf_voice_install_for_scan(tmp_path: Path) -> Path:
    install = tmp_path / "slf-voice-install"
    profiles = install / "Data-1.13" / "TableData" / "MercProfiles.xml"
    profiles.parent.mkdir(parents=True)
    profiles.write_text(
        "<MERCPROFILES><PROFILE><uiIndex>9</uiIndex><Type>1</Type>"
        "<zName>Mike</zName><zNickname>Mike</zNickname>"
        "<usVoiceIndex>9</usVoiceIndex></PROFILE></MERCPROFILES>",
        encoding="utf-8",
    )
    _pack_slf(install / "Data-1.13" / "Speech.slf", "9/MERC009_112.wav", b"slf-a")
    return install


class _HeaderSwitchConnection:
    """Inject one writer commit immediately after deployment header retrieval."""

    def __init__(self, connection: Any, after_header: Any) -> None:
        self._connection = connection
        self._after_header = after_header
        self._armed = True

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        cursor = self._connection.execute(sql, parameters)
        if self._armed and "FROM deployments" in sql:
            self._armed = False
            return _HeaderSwitchCursor(cursor, self._after_header)
        return cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class _HeaderSwitchCursor:
    def __init__(self, cursor: Any, after_header: Any) -> None:
        self._cursor = cursor
        self._after_header = after_header
        self._fired = False

    def fetchone(self) -> Any:
        row = self._cursor.fetchone()
        if row is not None and not self._fired:
            self._fired = True
            self._after_header()
        return row

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


def test_store_creates_versioned_wal_schema(tmp_path: Path) -> None:
    """Dropping WAL or a required table would lose concurrent review state."""
    database = tmp_path / "voice_lab.sqlite3"
    store = VoiceLabStore(database)
    try:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "assets", "banks", "profiles", "lines", "fingerprints", "transcriptions",
            "findings", "recipes", "deployments", "deployment_targets", "imported_assets",
        } <= tables
    finally:
        store.close()


def test_concurrent_first_open_serializes_schema_migration(tmp_path: Path) -> None:
    """Two first-open callers must not both execute the version-zero DDL plan."""
    database = tmp_path / "concurrent.sqlite3"
    barrier = Barrier(2)
    errors: list[Exception] = []

    def open_store() -> None:
        try:
            barrier.wait(timeout=2)
            store = VoiceLabStore(database)
            store.close()
        except Exception as exc:  # Assertion is made by the parent thread.
            errors.append(exc)

    first = Thread(target=open_store); second = Thread(target=open_store)
    first.start(); second.start(); first.join(timeout=5); second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    checked = VoiceLabStore(database)
    try:
        assert checked.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        checked.close()


def test_intentional_finding_reopens_when_evidence_changes(tmp_path: Path) -> None:
    """Keeping an old intentional disposition after evidence changes hides regressions."""
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    try:
        first = _finding()
        store.upsert_findings([first])
        store.set_finding_state(first.stable_key, "intentional")

        store.upsert_findings([first.model_copy(update={"evidence_hash": "evidence-b"})])

        assert store.finding(first.stable_key).state == "needs_review"
    finally:
        store.close()


def test_unchanged_finding_keeps_review_disposition(tmp_path: Path) -> None:
    """Resetting a reviewed finding on an identical rescan wastes reviewer decisions."""
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    try:
        first = _finding()
        store.upsert_findings([first])
        store.set_finding_state(first.stable_key, "intentional")
        store.upsert_findings([first])

        assert store.finding(first.stable_key).state == "intentional"
    finally:
        store.close()


def test_unchanged_fingerprint_reuses_hash(tmp_path: Path) -> None:
    """Ignoring a matching fingerprint needlessly rehashes unchanged source audio."""
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    try:
        store.remember_fingerprint("loose:a", 12, 34, "deadbeef")
        assert store.cached_sha256("loose:a", 12, 34) == "deadbeef"
    finally:
        store.close()


def test_changed_fingerprint_never_returns_stale_hash(tmp_path: Path) -> None:
    """Returning a cached hash after a size or mtime change conceals changed audio."""
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    try:
        store.remember_fingerprint("loose:a", 12, 34, "deadbeef")
        assert store.cached_sha256("loose:a", 13, 34) is None
        assert store.cached_sha256("loose:a", 12, 35) is None
    finally:
        store.close()


def test_second_scan_reuses_unchanged_hash_and_rehashes_changed_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing every scan defeats incremental review and misses cache invalidation coverage."""
    fake_voice_install = _voice_install_for_scan(tmp_path)
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    hash_calls = 0
    original_hash = inventory_module._content_sha256

    def count_hash(data: bytes) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return original_hash(data)

    monkeypatch.setattr(inventory_module, "_content_sha256", count_hash)
    clip = fake_voice_install / "Data-1.13" / "Speech" / "108_111.wav"
    try:
        first = inventory_module.scan_inventory(
            "fixture", fake_voice_install, load_trigger_catalog(), store
        )
        assert hash_calls == 1
        second = inventory_module.scan_inventory(
            "fixture", fake_voice_install, load_trigger_catalog(), store
        )
        assert hash_calls == 1
        assert second.line("speech", 108, "111").audio_winner is not None
        assert second.line("speech", 108, "111").audio_winner.sha256 == (
            first.line("speech", 108, "111").audio_winner.sha256  # type: ignore[union-attr]
        )

        before = clip.stat()
        clip.write_bytes(b"changed-wav")
        os.utime(clip, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        changed = inventory_module.scan_inventory(
            "fixture", fake_voice_install, load_trigger_catalog(), store
        )
        assert hash_calls == 2
        assert changed.line("speech", 108, "111").audio_winner is not None
        assert changed.line("speech", 108, "111").audio_winner.sha256 != (
            first.line("speech", 108, "111").audio_winner.sha256  # type: ignore[union-attr]
        )
    finally:
        store.close()


def test_second_scan_reuses_unchanged_slf_member_and_rehashes_changed_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignoring archive-member fingerprints makes SLF-backed banks expensive every scan."""
    install = _slf_voice_install_for_scan(tmp_path)
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    hash_calls = 0
    original_hash = inventory_module._content_sha256

    def count_hash(data: bytes) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return original_hash(data)

    monkeypatch.setattr(inventory_module, "_content_sha256", count_hash)
    archive = install / "Data-1.13" / "Speech.slf"
    try:
        first = inventory_module.scan_inventory("fixture", install, load_trigger_catalog(), store)
        assert hash_calls == 1
        inventory_module.scan_inventory("fixture", install, load_trigger_catalog(), store)
        assert hash_calls == 1

        before = archive.stat()
        _pack_slf(archive, "9/MERC009_112.wav", b"slf-b")
        os.utime(archive, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        changed = inventory_module.scan_inventory("fixture", install, load_trigger_catalog(), store)
        assert hash_calls == 2
        assert changed.line("speech", 9, "112").audio_winner is not None
        assert changed.line("speech", 9, "112").audio_winner.sha256 != (
            first.line("speech", 9, "112").audio_winner.sha256  # type: ignore[union-attr]
        )
    finally:
        store.close()


def test_failed_first_migration_leaves_no_partial_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A migration error must roll back every table so a later open can recover cleanly."""
    database = tmp_path / "voice_lab.sqlite3"
    schema = (
        "CREATE TABLE migration_probe (id INTEGER PRIMARY KEY)",
        "THIS IS NOT VALID SQL",
    )
    monkeypatch.setattr(store_module, "_SCHEMA_STATEMENTS", schema, raising=False)

    with pytest.raises(Exception):
        VoiceLabStore(database)

    probe = store_module.sqlite3.connect(database)
    try:
        assert probe.execute("PRAGMA user_version").fetchone()[0] == 0
        assert probe.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'migration_probe'"
        ).fetchone() is None
    finally:
        probe.close()

    monkeypatch.undo()
    recovered = VoiceLabStore(database)
    try:
        assert recovered.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        recovered.close()


def test_replace_inventory_persists_current_asset_and_reopens_when_input_changes(tmp_path: Path) -> None:
    """Retaining stale asset evidence after a rescan can apply review decisions to wrong audio."""
    database = tmp_path / "voice_lab.sqlite3"
    store = VoiceLabStore(database)
    try:
        store.replace_inventory(_snapshot())
        assert store.asset("loose:fixture:108:111.wav").sha256 == "audio-a"

        store.replace_inventory(_snapshot(sha256="audio-b", mtime_ns=35))
        assert store.asset("loose:fixture:108:111.wav").sha256 == "audio-b"
    finally:
        store.close()

    reopened = VoiceLabStore(database)
    try:
        assert reopened.asset("loose:fixture:108:111.wav").sha256 == "audio-b"
    finally:
        reopened.close()


def test_replace_banks_updates_one_bank_without_erasing_other_cached_banks(tmp_path: Path) -> None:
    """A selected-merc refresh must preserve banks already indexed this session."""
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    other_asset = _asset(sha256="other-a").model_copy(update={
        "asset_id": "loose:fixture:42:111.wav",
        "voice_index": 42,
    })
    other = VoiceBank(
        voice_index=42,
        lines=[VoiceLine(
            family="speech", voice_index=42, line_id="111",
            audio_variants=[other_asset], audio_winner=other_asset,
        )],
    )
    try:
        store.replace_inventory(InventorySnapshot(
            install_id="fixture-install",
            banks=[_snapshot().bank(108), other],
        ))

        store.replace_banks(_snapshot(sha256="audio-b", mtime_ns=35))

        assert store.asset("loose:fixture:108:111.wav").sha256 == "audio-b"
        assert store.asset("loose:fixture:42:111.wav").sha256 == "other-a"
    finally:
        store.close()


def test_replace_banks_purges_a_former_bank_when_its_only_profile_moves(tmp_path: Path) -> None:
    """A selected refresh must not leave the former unowned bank in the catalog."""
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    try:
        store.replace_inventory(InventorySnapshot(
            install_id="fixture-install",
            banks=[_bank_with_profiles(108, 7)],
        ))

        store.replace_banks(InventorySnapshot(
            install_id="fixture-install",
            banks=[_bank_with_profiles(42, 7)],
        ))

        assert [tuple(row) for row in store.connection.execute(
            "SELECT voice_index FROM banks WHERE install_id = ? ORDER BY voice_index",
            ("fixture-install",),
        ).fetchall()] == [(42,)]
        assert [tuple(row) for row in store.connection.execute(
            "SELECT voice_index FROM profiles WHERE install_id = ?",
            ("fixture-install",),
        ).fetchall()] == [(42,)]
        assert [tuple(row) for row in store.connection.execute(
            "SELECT voice_index FROM lines WHERE install_id = ?",
            ("fixture-install",),
        ).fetchall()] == [(42,)]
        assert [tuple(row) for row in store.connection.execute(
            "SELECT voice_index FROM assets WHERE install_id = ?",
            ("fixture-install",),
        ).fetchall()] == [(42,)]
    finally:
        store.close()


def test_replace_banks_keeps_a_former_bank_still_owned_by_another_profile(tmp_path: Path) -> None:
    """Reclaiming a moved profile's former bank must retain its remaining shared owner."""
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    try:
        store.replace_inventory(InventorySnapshot(
            install_id="fixture-install",
            banks=[_bank_with_profiles(108, 7, 8)],
        ))

        store.replace_banks(InventorySnapshot(
            install_id="fixture-install",
            banks=[_bank_with_profiles(42, 7)],
        ))

        assert [tuple(row) for row in store.connection.execute(
            "SELECT voice_index FROM banks WHERE install_id = ? ORDER BY voice_index",
            ("fixture-install",),
        ).fetchall()] == [(42,), (108,)]
        assert [tuple(row) for row in store.connection.execute(
            "SELECT profile_id, voice_index FROM profiles WHERE install_id = ? ORDER BY profile_id",
            ("fixture-install",),
        ).fetchall()] == [(7, 42), (8, 108)]
        assert [tuple(row) for row in store.connection.execute(
            "SELECT voice_index FROM lines WHERE install_id = ? ORDER BY voice_index",
            ("fixture-install",),
        ).fetchall()] == [(42,), (108,)]
        assert [tuple(row) for row in store.connection.execute(
            "SELECT voice_index FROM assets WHERE install_id = ? ORDER BY voice_index",
            ("fixture-install",),
        ).fetchall()] == [(42,), (108,)]
    finally:
        store.close()


def test_imported_asset_migrates_from_v1_and_survives_inventory_replacement(tmp_path: Path) -> None:
    """Treating an authoring import as scanned inventory would erase it on the next scan."""
    database = tmp_path / "voice_lab.sqlite3"
    legacy = VoiceLabStore(database)
    with legacy.transaction():
        legacy.connection.execute("DROP TABLE imported_assets")
        legacy.connection.execute("PRAGMA user_version = 1")
    legacy.close()

    store = VoiceLabStore(database)
    try:
        imported = store.register_imported_asset(
            install_id="fixture-install",
            asset_id="import-deadbeef",
            extension=".ogg",
            source_locator=str(tmp_path / "imports" / "deadbeef.ogg"),
            size_bytes=12,
            sha256="deadbeef",
            duration_ms=100,
        )
        store.replace_inventory(_snapshot())
        assert store.asset(imported.asset_id).sha256 == "deadbeef"
    finally:
        store.close()

    reopened = VoiceLabStore(database)
    try:
        assert reopened.asset("import-deadbeef").asset_id == "import-deadbeef"
        assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        reopened.close()


def test_replace_inventory_keeps_gap_variant_on_its_audio_line(tmp_path: Path) -> None:
    """Using the gap file's family as a line key rejects valid lip-sync variants."""
    audio = _asset()
    gap = audio.model_copy(
        update={
            "asset_id": "loose:fixture:108:111.gap",
            "family": "gap",
            "extension": ".gap",
            "sha256": "gap-a",
            "winner": True,
        }
    )
    snapshot = InventorySnapshot(
        install_id="fixture-install",
        banks=[
            VoiceBank(
                voice_index=108,
                lines=[
                    VoiceLine(
                        family="speech",
                        voice_index=108,
                        line_id="111",
                        audio_variants=[audio],
                        audio_winner=audio,
                        gap_variants=[gap],
                        gap_winner=gap,
                    )
                ],
            )
        ],
    )
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    try:
        store.replace_inventory(snapshot)
        assert store.asset(gap.asset_id).family == "gap"
    finally:
        store.close()


def test_transcription_is_reused_only_for_matching_source_hash(tmp_path: Path) -> None:
    """Serving a transcript for altered audio makes subtitle audits use false evidence."""
    store = VoiceLabStore(tmp_path / "voice_lab.sqlite3")
    transcription = Transcription(
        asset_id="loose:fixture:108:111.wav",
        source_sha256="audio-a",
        text="Now, what can I do for you?",
        confidence=0.98,
        language="en",
        model_id="whisper-tiny.en",
        model_version="test",
        word_timestamps=[],
        created_utc="2026-08-15T00:00:00+00:00",
    )
    try:
        store.save_transcription(transcription)
        assert store.transcription(transcription.asset_id, "audio-a") == transcription
        assert store.transcription(transcription.asset_id, "audio-b") is None
    finally:
        store.close()


def test_wal_reader_sees_committed_inventory_while_another_connection_writes(tmp_path: Path) -> None:
    """A long writer transaction must not make the most recent committed scan unreadable."""
    database = tmp_path / "voice_lab.sqlite3"
    writer = VoiceLabStore(database)
    reader = VoiceLabStore(database)
    try:
        writer.replace_inventory(_snapshot())
        with writer.transaction():
            writer.connection.execute(
                "UPDATE assets SET sha256 = ? WHERE asset_id = ?", ("uncommitted", "loose:fixture:108:111.wav")
            )
            assert reader.asset("loose:fixture:108:111.wav").sha256 == "audio-a"
    finally:
        reader.close()
        writer.close()


def test_recipe_and_deployment_indexes_survive_reopen(tmp_path: Path) -> None:
    """Losing searchable recipe history after a restart hides deployment provenance."""
    database = tmp_path / "voice_lab.sqlite3"
    recipe = EditRecipe(
        recipe_id="recipe-1",
        install_id="fixture-install",
        voice_index=108,
        family="speech",
        line_id="111",
        input_asset_id="loose:fixture:108:111.wav",
        input_sha256="audio-a",
        operations=[{"kind": "cut", "start_ms": 100, "end_ms": 250}],
        output_extension=".ogg",
        created_utc="2026-08-15T00:00:00+00:00",
        updated_utc="2026-08-15T00:00:00+00:00",
    )
    store = VoiceLabStore(database)
    try:
        store.save_recipe(recipe)
        store.save_deployment(
            deployment_id="deployment-1",
            install_id="fixture-install",
            recipe_id=recipe.recipe_id,
            status="verified",
            manifest={"recipe_id": recipe.recipe_id},
            targets=[{"asset_id": recipe.input_asset_id, "output_sha256": "output-a"}],
        )
    finally:
        store.close()

    reopened = VoiceLabStore(database)
    try:
        assert reopened.recipe(recipe.recipe_id) == recipe
        assert reopened.deployment("deployment-1")["status"] == "verified"
        assert reopened.deployment("deployment-1")["targets"] == [
            {"asset_id": recipe.input_asset_id, "output_sha256": "output-a"}
        ]
    finally:
        reopened.close()


def test_deployment_read_keeps_header_and_targets_from_one_snapshot(tmp_path: Path) -> None:
    """A commit between two deployment queries must not return a mixed manifest and target set."""
    database = tmp_path / "voice_lab.sqlite3"
    reader = VoiceLabStore(database)
    writer = VoiceLabStore(database)
    try:
        writer.save_deployment(
            deployment_id="deployment-1",
            install_id="fixture-install",
            recipe_id="recipe-1",
            status="old",
            manifest={"revision": "old"},
            targets=[{"revision": "old"}],
        )

        def replace_with_new_revision() -> None:
            writer.save_deployment(
                deployment_id="deployment-1",
                install_id="fixture-install",
                recipe_id="recipe-1",
                status="new",
                manifest={"revision": "new"},
                targets=[{"revision": "new"}],
            )

        reader.connection = _HeaderSwitchConnection(reader.connection, replace_with_new_revision)
        deployment = reader.deployment("deployment-1")

        assert deployment["status"] == "old"
        assert deployment["manifest"] == {"revision": "old"}
        assert deployment["targets"] == [{"revision": "old"}]
    finally:
        reader.close()
        writer.close()


def test_open_uses_install_scoped_database_under_given_base(tmp_path: Path) -> None:
    """Sharing a database across installs would mix unrelated review dispositions."""
    store = VoiceLabStore.open("fixture-install", base=tmp_path)
    try:
        assert store.database_path == tmp_path / "voice_lab" / "fixture-install" / "voice_lab.sqlite3"
    finally:
        store.close()
