from mercwizard_core.voice_lab.models import Transcription, VoiceAsset, VoiceProfile


def voice_asset(asset_id: str, family: str, line_id: str, sha: str, *, winner: bool = True) -> VoiceAsset:
    return VoiceAsset(
        asset_id=asset_id, family=family, voice_index=108, line_id=line_id,
        extension=".ogg" if family != "dialogue_edt" else ".edt", source_kind="loose",
        source_locator=f"C:/fixture/{asset_id}", layer_rank=0, size_bytes=12,
        mtime_ns=1, sha256=sha, writable=True, winner=winner,
    )


def audit_line(line_id: str, subtitle: str | None, transcript: str | None, duration_ms: int, **changes):
    from mercwizard_core.voice_lab.audit import AuditLine, AudioMetrics

    audio = voice_asset(f"audio-{line_id}", "speech", line_id, f"{int(line_id):064x}")
    transcription = None if transcript is None else Transcription(
        asset_id=audio.asset_id, source_sha256=audio.sha256, text=transcript,
        confidence=0.9, language="en", model_id="fake", model_version="1",
        word_timestamps=[], created_utc="2026-08-15T00:00:00+00:00",
    )
    values = dict(
        voice_index=108, family="speech", line_id=line_id, audio=audio,
        subtitle=subtitle, transcription=transcription,
        metrics=AudioMetrics(duration_ms=duration_ms, pcm_sha256=f"pcm-{line_id}"),
    )
    values.update(changes)
    return AuditLine(**values)


def test_king_shaped_sequence_flags_without_character_rule():
    from mercwizard_core.voice_lab.audit import run_content_audit

    lines = [
        audit_line("108", "What can the King do for you?", "What can the King do for you?", 1800),
        audit_line("111", "Cash up front. No exceptions. Business is business.", "Now, what can I do for you?", 1000),
    ]
    findings = run_content_audit(lines)
    codes = {finding.code for finding in findings}
    assert {"SUBTITLE_TRANSCRIPT_MISMATCH", "SEQUENCE_NEAR_DUPLICATE", "DURATION_TEXT_MISMATCH"} <= codes
    assert all(finding.asset_ids and finding.evidence_hash and finding.evidence.get("plain_language") for finding in findings)


def test_content_audit_can_preserve_explicit_context_order():
    """A selected branch sequence must not be renumbered before comparison."""
    from mercwizard_core.voice_lab.audit import run_content_audit

    later = audit_line("112", "later", "same contextual phrase", 1000)
    earlier = audit_line("111", "earlier", "same contextual phrase", 1000)

    findings = run_content_audit([later, earlier], preserve_context_order=True)

    duplicate = next(item for item in findings if item.code == "SEQUENCE_NEAR_DUPLICATE")
    assert duplicate.evidence["lines"] == ["112", "111"]


def test_missing_transcription_suppresses_only_transcription_dependent_findings():
    from mercwizard_core.voice_lab.audit import run_content_audit

    findings = run_content_audit([audit_line("1", "word " * 30, None, 100)])
    assert {finding.code for finding in findings} == set()


def test_deterministic_audit_covers_release_one_families():
    from mercwizard_core.voice_lab.audit import AuditBank, AudioMetrics, run_deterministic_audit

    shared = [VoiceProfile(profile_id=108, profile_type=0, name="One", nickname="", face_index=None, voice_index=108),
              VoiceProfile(profile_id=109, profile_type=0, name="Two", nickname="", face_index=None, voice_index=108)]
    duplicate = audit_line("2", "same subtitle", None, 1000, metrics=AudioMetrics(duration_ms=1000, pcm_sha256="same"))
    main = audit_line(
        "1", "same subtitle", None, 999999,
        audio_variants=[voice_asset("shadow", "speech", "1", "b" * 64, winner=False)],
        dialogue=voice_asset("edt", "dialogue_edt", "document", "c" * 64),
        gap=voice_asset("gap", "gap", "1", "d" * 64),
        metrics=AudioMetrics(duration_ms=999999, pcm_sha256="same", sample_rate=8000, channels=2,
                             leading_silence_ms=600, trailing_silence_ms=600, peak_dbfs=0.0,
                             rms_dbfs=-95.0, lufs=-50.0),
        expected_gap_duration_ms=1000, gap_valid=False,
        reviewed_component_hashes={"audio": "different", "edt": "c" * 64, "gap": "d" * 64},
        reviewed_candidate_asset_id="shadow",
    )
    missing = audit_line("3", None, None, 1000, audio=None, dialogue=None, gap=None,
                         expected_components=("audio", "subtitle", "gap"))
    bank = AuditBank(
        voice_index=108, profiles=shared, lines=[main, duplicate, missing], edt_valid=False,
        edt_asset=voice_asset("bank-edt", "dialogue_edt", "document", "e" * 64),
    )
    findings = run_deterministic_audit(bank)
    codes = {finding.code for finding in findings}
    assert {
        "MISSING_COMPONENT", "MALFORMED_EDT", "INVALID_AUDIO_FORMAT",
        "EXACT_AUDIO_DUPLICATE", "DUPLICATE_SUBTITLE", "EXCESS_SILENCE",
        "CLIPPING", "NEAR_SILENCE", "EXTREME_DURATION", "LEVEL_OUTLIER",
        "INVALID_GAP", "STALE_GAP", "COMPONENT_HASH_DRIFT",
        "SHARED_BANK_IMPACT", "SHADOWED_AUDIO",
    } <= codes
    assert all(finding.asset_ids for finding in findings)


def test_audit_evidence_reopens_when_content_changes_under_stable_asset_id(tmp_path):
    from mercwizard_core.voice_lab.audit import AudioMetrics, run_deterministic_audit
    from mercwizard_core.voice_lab.store import VoiceLabStore

    original = audit_line("1", "hello", None, 1000, metrics=AudioMetrics(duration_ms=1000, pcm_sha256="pcm-a", sample_rate=8000))
    changed_audio = original.audio.model_copy(update={"sha256": "f" * 64})
    changed = audit_line("1", "hello", None, 1000, audio=changed_audio, metrics=AudioMetrics(duration_ms=1000, pcm_sha256="pcm-b", sample_rate=8000))
    first = run_deterministic_audit([original])[0]
    later = run_deterministic_audit([changed])[0]
    assert first.stable_key == later.stable_key
    assert first.evidence_hash != later.evidence_hash
    with VoiceLabStore.open("task5-evidence", base=tmp_path) as store:
        store.upsert_findings([first])
        store.set_finding_state(first.stable_key, "intentional")
        store.upsert_findings([later])
        assert store.finding(first.stable_key).state == "needs_review"


def test_component_hash_drift_compares_reviewed_expected_hashes_not_unique_content():
    from mercwizard_core.voice_lab.audit import run_deterministic_audit

    line = audit_line("1", "hello", None, 1000,
                      dialogue=voice_asset("edt", "dialogue_edt", "document", "b" * 64),
                      gap=voice_asset("gap", "gap", "1", "c" * 64))
    reviewed = {"audio": line.audio.sha256, "edt": line.dialogue.sha256, "gap": line.gap.sha256}
    healthy = line.__class__(**{**line.__dict__, "reviewed_component_hashes": reviewed})
    assert "COMPONENT_HASH_DRIFT" not in {finding.code for finding in run_deterministic_audit([healthy])}
    drifted = line.__class__(**{**line.__dict__, "reviewed_component_hashes": {**reviewed, "gap": "d" * 64}})
    finding = next(finding for finding in run_deterministic_audit([drifted]) if finding.code == "COMPONENT_HASH_DRIFT")
    assert finding.evidence["mismatches"]["gap"]["current"] == "c" * 64


def test_shadowed_audio_requires_explicit_reviewed_candidate_identity():
    from mercwizard_core.voice_lab.audit import run_deterministic_audit

    winner = voice_asset("winner", "speech", "1", "a" * 64, winner=True)
    loser = voice_asset("loser", "speech", "1", "b" * 64, winner=False)
    ordinary = audit_line("1", "hello", None, 1000, audio=winner, audio_variants=[winner, loser])
    assert "SHADOWED_AUDIO" not in {finding.code for finding in run_deterministic_audit([ordinary])}
    reviewed_loser = ordinary.__class__(**{**ordinary.__dict__, "reviewed_candidate_asset_id": "loser"})
    assert "SHADOWED_AUDIO" in {finding.code for finding in run_deterministic_audit([reviewed_loser])}
    reviewed_winner = ordinary.__class__(**{**ordinary.__dict__, "reviewed_candidate_asset_id": "winner"})
    assert "SHADOWED_AUDIO" not in {finding.code for finding in run_deterministic_audit([reviewed_winner])}
