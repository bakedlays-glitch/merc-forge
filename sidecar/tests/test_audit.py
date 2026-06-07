"""Tests for the audit module."""
from __future__ import annotations

from mercwizard_core.audit import (
    EYE_SPACING_MAX,
    EYE_SPACING_MIN,
    Severity,
    audit_eye_spacing,
    audit_full,
    audit_gear,
    audit_merc,
    has_errors,
)
from mercwizard_core.models import AimBinding, Gear, GearKit, Merc


def test_audit_clean_merc_has_no_errors(sample_merc: Merc) -> None:
    issues = audit_merc(sample_merc)
    assert not has_errors(issues)


def test_audit_face_index_shadows_vanilla_warns() -> None:
    """Custom merc at slot 220 using vanilla face 100 → warning."""
    m = Merc(uiIndex=220, ubFaceIndex=100, zName="Test", zNickname="Test", Type=1)
    issues = audit_merc(m)
    face_warnings = [i for i in issues if i.code == "FACE_INDEX_SHADOWS_VANILLA"]
    assert len(face_warnings) == 1
    assert face_warnings[0].severity == Severity.WARN


def test_audit_face_index_matching_uiindex_no_warn() -> None:
    """Replacing vanilla merc at slot 26 with ubFaceIndex=26 is legitimate — no warning."""
    m = Merc(uiIndex=26, ubFaceIndex=26, zName="James Drake", zNickname="Pirate", Type=1)
    issues = audit_merc(m)
    face_warnings = [i for i in issues if i.code.startswith("FACE_INDEX_")]
    assert len(face_warnings) == 0


def test_audit_face_index_high_no_warn() -> None:
    """ubFaceIndex >= 160 is the normal custom-merc case — no warning."""
    m = Merc(uiIndex=220, ubFaceIndex=220, zName="Test", zNickname="Test", Type=1)
    issues = audit_merc(m)
    face_warnings = [i for i in issues if i.code.startswith("FACE_INDEX_")]
    assert len(face_warnings) == 0


def test_audit_npc_in_aim_slot_errors() -> None:
    """The Marcus-at-slot-57 trap: Type=3 in an AIM slot makes the merc invisible."""
    m = Merc(uiIndex=5, ubFaceIndex=160, zName="X", zNickname="X", Type=3)
    issues = audit_merc(m)
    errors = [i for i in issues if i.code == "NPC_IN_AIM_SLOT"]
    assert len(errors) == 1
    assert errors[0].severity == Severity.ERROR
    assert has_errors(issues)


def test_audit_bio_near_limit_warns() -> None:
    bio = "x" * 390  # 380 < len < 400
    m = Merc(
        uiIndex=10, ubFaceIndex=160, zName="T", zNickname="T",
        biographyText=bio,
    )
    issues = audit_merc(m)
    warns = [i for i in issues if i.code == "FIELD_NEAR_LIMIT"]
    assert any(w.field == "biographyText" for w in warns)


def test_audit_gear_mismatch_errors(sample_merc: Merc) -> None:
    """Gear.mIndex must match Merc.uiIndex. Use a valid-but-different slot."""
    bad_gear = Gear(mIndex=50, kits=[GearKit(mWeapon=2)])  # sample_merc is slot 220
    issues = audit_gear(bad_gear, sample_merc)
    errors = [i for i in issues if i.code == "GEAR_MERC_MISMATCH"]
    assert len(errors) == 1
    assert errors[0].severity == Severity.ERROR


def test_audit_gear_no_weapon_warns(sample_merc: Merc) -> None:
    unarmed = Gear(mIndex=220, kits=[GearKit(mWeapon=0)])
    issues = audit_gear(unarmed, sample_merc)
    warns = [i for i in issues if i.code == "GEAR_NO_WEAPON"]
    assert len(warns) == 1


def test_audit_eye_spacing_in_bounds_no_issue() -> None:
    issues = audit_eye_spacing(15.0)
    assert issues == []


def test_audit_eye_spacing_too_narrow_warns() -> None:
    issues = audit_eye_spacing(EYE_SPACING_MIN - 1.0)
    assert len(issues) == 1
    assert issues[0].code == "EYE_SPACING_TOO_NARROW"


def test_audit_eye_spacing_too_wide_warns() -> None:
    issues = audit_eye_spacing(EYE_SPACING_MAX + 1.0)
    assert len(issues) == 1
    assert issues[0].code == "EYE_SPACING_TOO_WIDE"


def test_audit_emoji_in_bio_warns_about_unencodable_chars() -> None:
    """Bug-review #99: emoji + supplementary-plane chars get silently
    clamped to U+FFFE by the EDT bio encoder. Audit must surface them
    BEFORE save so the user can decide whether to remove them."""
    # 🔥 is U+1F525 (supplementary plane). The ROT+1 still puts it above
    # 0xFFFE, so encode_field would clamp it.
    m = Merc(
        uiIndex=10, ubFaceIndex=160, zName="Spark", zNickname="Spark",
        Type=1,
        biographyText="Hot stuff 🔥 with some emoji 🎯",
        additionalInfoText="Plain text only.",
    )
    issues = audit_merc(m)
    unencodable = [i for i in issues if i.code == "CONTAINS_UNENCODABLE"]
    assert len(unencodable) == 1
    assert unencodable[0].field == "biographyText"
    assert unencodable[0].severity == Severity.WARN
    assert "2" in unencodable[0].message  # 2 emoji found
    assert "🔥" in unencodable[0].message  # sample char shown


def test_audit_ascii_only_bio_no_unencodable_warning() -> None:
    """Plain ASCII text shouldn't trigger the surrogate check."""
    m = Merc(
        uiIndex=10, ubFaceIndex=160, zName="Carter", zNickname="Carter",
        Type=1,
        biographyText="A boring biography with no special characters.",
    )
    issues = audit_merc(m)
    assert [i for i in issues if i.code == "CONTAINS_UNENCODABLE"] == []


def test_audit_nickname_with_emoji_flags_field() -> None:
    """The nickname field gets the same encoder; emoji there should also flag."""
    m = Merc(
        uiIndex=10, ubFaceIndex=160, zName="Plain", zNickname="🎯",
        Type=1,
    )
    issues = audit_merc(m)
    unencodable = [i for i in issues if i.code == "CONTAINS_UNENCODABLE"]
    assert len(unencodable) == 1
    assert unencodable[0].field == "zNickname"


def test_audit_full_combines_all_checks(sample_merc, sample_gear, sample_aim_binding) -> None:
    issues = audit_full(
        sample_merc,
        gear=sample_gear,
        aim_binding=sample_aim_binding,
        eye_spacing_px_at_48x43=15.44,
    )
    # Clean inputs → should produce only WARN about TYPE_SLOT_MISMATCH (slot 220
    # isn't in canonical AIM ranges) and FACE_INDEX_LOW (220 is < typical custom face index)
    # No ERRORs
    assert not has_errors(issues)
