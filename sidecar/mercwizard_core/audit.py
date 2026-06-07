"""Pre-write validation: structured Issue objects across all the engine rules.

The wizard's audit pass runs every check below and surfaces them in the UI:
  - Errors block the compile button
  - Warnings show in orange and the player can override
  - Info shows in blue (advisory)

This module is intentionally pure: it takes Merc/Gear/AimBinding models +
an install context and returns a list of issues. It does NOT touch the file
system except to optionally read other slots' state for cross-checks
(handled by passing in the existing roster).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, TYPE_CHECKING

from pydantic import BaseModel

from .models import AimBinding, Gear, GearKit, Merc

# `SlotInfo` is type-annotated only (signatures in audit_merc and
# audit_full). With `from __future__ import annotations` enabled at the
# top of this module, annotations are strings at runtime and don't
# require a real import. Keeping SlotInfo behind TYPE_CHECKING breaks
# the audit → slot_picker import edge so a future maintainer adding
# `from .audit import Severity` to slot_picker (a reasonable refactor
# for shared error codes) won't crash startup with a circular import.
# Mirrors the existing lazy-import pattern used inside audit_merc for
# inject.edt.find_unencodable_chars. Bug-review finding C5.
if TYPE_CHECKING:
    from .slot_picker import SlotInfo


# SoldierBodyTypes from Tactical/Animation Data.h:36-73. Closed enum —
# adding a new ID requires recompile. Sprites for any value not in this
# set don't exist, so the engine renders garbage or crashes.
# (Values 4-41 + 49-58 + 62+ are unused / reserved / vehicle slots.)
_HUMAN_BODY_TYPES = {
    0: ("REGMALE", "male"),
    1: ("BIGMALE", "male"),
    2: ("STOCKYMALE", "male"),
    3: ("REGFEMALE", "female"),
}
_MONSTER_BODY_TYPES = {
    42: ("ADULTFEMALEMONSTER", "female"),
    43: ("AM_MONSTER", "male"),
    44: ("YAF_MONSTER", "female"),
    45: ("YAM_MONSTER", "male"),
    46: ("LARVAE_MONSTER", None),
    47: ("INFANT_MONSTER", None),
    48: ("QUEENMONSTER", "female"),
}
_ANIMAL_BODY_TYPES = {
    59: ("BLOODCAT", None),
    60: ("COW", None),
    61: ("CROW", None),
}
KNOWN_BODY_TYPES: dict[int, tuple[str, Optional[str]]] = {
    **_HUMAN_BODY_TYPES,
    **_MONSTER_BODY_TYPES,
    **_ANIMAL_BODY_TYPES,
}
VANILLA_RACE_MAX = 4  # 0-4 are vanilla; mods extend


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class Issue(BaseModel):
    severity: Severity
    field: Optional[str] = None
    code: str
    message: str
    suggested_fix: Optional[str] = None


# Vanilla eye-spacing bounds at 48×43 SmallFace scale (from portrait_pipeline.md)
EYE_SPACING_MIN = 13.26
EYE_SPACING_MAX = 17.77
EYE_SPACING_AVG = 15.44


# Fallback static vanilla data for the no-install-context audit path
# (test fixtures that don't construct an install). Live XML data via
# `slot_info` is authoritative when available.
_VANILLA_AIM_SCATTERED = frozenset({
    215, 223, 228, 230, 231, 232, 233, 234, 235, 236, 237, 238, 239,
    240, 241, 242, 243, 245, 246, 248, 250, 251,
})
_VANILLA_MERC_SCATTERED = frozenset({244, 247, 249, 252, 253})


def _is_aim_bound_fallback(slot: int) -> bool:
    """Static AIM-row presence fallback when no slot_info is available."""
    if 0 <= slot <= 39:
        return True
    if 170 <= slot <= 177:
        return True
    if slot in (186, 187):
        return True
    return slot in _VANILLA_AIM_SCATTERED


def _is_merc_bound_fallback(slot: int) -> bool:
    """Static MERC-row presence fallback when no slot_info is available."""
    if 40 <= slot <= 50:
        return True
    if 178 <= slot <= 185:
        return True
    if 188 <= slot <= 199:
        return True
    return slot in _VANILLA_MERC_SCATTERED


def audit_merc(merc: Merc, *, slot_info: Optional[SlotInfo] = None) -> list[Issue]:
    """Validate a Merc model against engine field caps and slot consistency.

    ``slot_info`` carries live AIM/MERC row data + engine-named-slot tier.
    Routes that have an install context should pass it for authoritative
    checks. Pure-data tests can omit it; checks fall back to vanilla 1.13
    data conventions.
    """
    issues: list[Issue] = []

    # Field caps (model validators already prevent over-limit, but check again
    # in case raw dict-construction bypassed them)
    if len(merc.zNickname) > 9:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="zNickname",
            code="FIELD_TOO_LONG",
            message=f"Nickname '{merc.zNickname}' is {len(merc.zNickname)} chars; max 9",
            suggested_fix=f"Trim to '{merc.zNickname[:9]}'",
        ))
    if len(merc.zName) > 50:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="zName",
            code="FIELD_TOO_LONG",
            message=f"Name '{merc.zName}' is {len(merc.zName)} chars; max 50",
        ))
    if len(merc.biographyText) > 400:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="biographyText",
            code="FIELD_TOO_LONG",
            message=f"Bio is {len(merc.biographyText)} chars; max 400 (EDT silently truncates)",
        ))
    elif len(merc.biographyText) > 380:
        issues.append(Issue(
            severity=Severity.WARN,
            field="biographyText",
            code="FIELD_NEAR_LIMIT",
            message=f"Bio is {len(merc.biographyText)} chars; max 400. Approaching limit.",
        ))
    if len(merc.additionalInfoText) > 160:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="additionalInfoText",
            code="FIELD_TOO_LONG",
            message=f"Additional info is {len(merc.additionalInfoText)} chars; max 160",
        ))
    elif len(merc.additionalInfoText) > 150:
        issues.append(Issue(
            severity=Severity.WARN,
            field="additionalInfoText",
            code="FIELD_NEAR_LIMIT",
            message=f"Additional info is {len(merc.additionalInfoText)} chars; max 160. Approaching limit.",
        ))

    # Surrogate / supplementary-plane character guard. The EDT bio
    # encoder is a 16-bit UTF-16 writer with no surrogate-pair handling
    # — any codepoint above 0xFFFE gets clamped to 0xFFFE on save and
    # renders as the `□` sentinel glyph in-game. This catches emoji,
    # rare CJK, mathematical symbols, etc. before the user commits.
    from .inject.edt import find_unencodable_chars
    for field_name, text in (
        ("biographyText", merc.biographyText),
        ("additionalInfoText", merc.additionalInfoText),
        ("zName", merc.zName),
        ("zNickname", merc.zNickname),
    ):
        bad = find_unencodable_chars(text)
        if bad:
            # Show up to 3 sample characters so the user can ctrl-F to
            # them in the editor without us flooding the message.
            sample = ", ".join(f"{c!r} at index {i}" for i, c in bad[:3])
            more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
            issues.append(Issue(
                severity=Severity.WARN,
                field=field_name,
                code="CONTAINS_UNENCODABLE",
                message=(
                    f"{field_name} has {len(bad)} character(s) the engine's bio "
                    f"reader can't represent ({sample}{more}). They'll render as "
                    "the `□` placeholder glyph in-game — remove them or use a "
                    "1-byte equivalent before saving."
                ),
            ))

    # ubFaceIndex engine-cap guard. JA2 1.13 face STIs are LAZY-loaded
    # from disk by index — `InternalInitFace` (Tactical/Faces.cpp:179)
    # builds the path via `sprintf("FACES\\b%03d.sti", iFaceFileID)` and
    # opens it with VOBJECT_CREATE_FROMFILE. There is no boot-time face
    # table; the file simply needs to exist on disk for the index. (1.13
    # has no `Faces.xml` — face-by-index pre-declaration doesn't apply.)
    #
    # The real ceiling is `NUM_PROFILES = 255` from
    # `Tactical/soldier profile type.h:8`, the size of `gMercProfiles[]`
    # and `gFacesData[NUM_FACE_SLOTS]`. ubFaceIndex >= 256 would overflow
    # those arrays. Indices 0-255 are all engine-valid.
    #
    # The bug-doc's proposal to ALSO warn at >200 was rejected — high
    # indices (e.g. 220) are normal in community mod setups, enforced by
    # the regression test `test_audit_face_index_high_no_warn`.
    #
    # The CTD trap users hit at high indices (e.g. the slot 228 report in
    # MERC_FORGE_BUG_LIST.md bug #8) is almost always FaceGear STI
    # capacity, not base-portrait absence — vobject.cpp:958's
    # `SGP_THROW_IFFALSE` fires when ubFaceIndex >= a FaceGear STI's
    # frame count. That's covered by `FaceGearCapacityBanner` on both
    # Create.tsx and Edit.tsx, not by this audit check.
    if merc.ubFaceIndex > 255:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="ubFaceIndex",
            code="FACE_INDEX_EXCEEDS_ENGINE_CAP",
            message=(
                f"ubFaceIndex={merc.ubFaceIndex} exceeds the stock JA2 1.13 "
                f"face-table capacity (255). Loading this merc in-game would "
                f"crash with 'BltVideoObjectToBuffer: No Source Object'."
            ),
            suggested_fix="Pick a face index 0-255.",
        ))

    # ubFaceIndex sanity (continued — vanilla-portrait shadow rules):
    #  - Same as uiIndex AND uiIndex < 160 → REPLACING a vanilla portrait at its
    #    own slot (normal: writing a new merc at vanilla slot 26 uses ubFaceIndex=26).
    #    NO warning.
    #  - ubFaceIndex < 160 AND it's different from uiIndex → SHADOWING someone
    #    else's vanilla portrait. Warn.
    #  - uiIndex >= 160 AND ubFaceIndex < 160 → custom merc stealing a vanilla
    #    portrait slot. Warn.
    if merc.ubFaceIndex < 160 and merc.ubFaceIndex != merc.uiIndex:
        whose = "vanilla merc" if merc.ubFaceIndex < 51 else "vanilla NPC/RPC"
        issues.append(Issue(
            severity=Severity.WARN,
            field="ubFaceIndex",
            code="FACE_INDEX_SHADOWS_VANILLA",
            message=(
                f"ubFaceIndex={merc.ubFaceIndex} shadows a {whose}'s portrait. "
                f"In-game, slot {merc.ubFaceIndex}'s face will use your portrait too."
            ),
            suggested_fix=f"Use a value ≥ 160 to keep this merc's face unique",
        ))

    # Type vs slot consistency, engine-faithful version.
    #
    # AIM/MERC membership is XML-driven — the engine reads AIMAvailability.xml
    # and MercAvailability.xml at boot to decide which slots appear on each
    # laptop site. So the right question isn't "is this slot in some
    # hardcoded range?" but "does this slot have an availability row?".
    #
    # MercForge writes the availability row on save (see routes/merc.py's
    # auto-derive logic). So a Type=AIM at a slot with no AIM row isn't a
    # broken save — it's a slot where MercForge will write the row. We
    # surface that as a WARN, not an ERROR.
    #
    # Quest-bound slots (named RPC/NPC in 57-159) WARN: overwriting works
    # but quest scripts still call this slot by its original name.
    #
    # Type=RPC at a slot with an AIM row STILL ERRORs — the engine only
    # displays Type=1 (AIM) on the AIM laptop, regardless of row presence.
    # This is the Marcus-at-slot-57 invisibility trap.
    if slot_info is not None:
        aim_row_present = slot_info.aim_row.present
        merc_row_present = slot_info.merc_row.present
        slot_is_quest_bound = slot_info.tier == "quest_bound"
    else:
        aim_row_present = _is_aim_bound_fallback(merc.uiIndex)
        merc_row_present = _is_merc_bound_fallback(merc.uiIndex)
        slot_is_quest_bound = 57 <= merc.uiIndex <= 159

    if merc.Type == 1 and not aim_row_present:
        issues.append(Issue(
            severity=Severity.WARN,
            field="Type",
            code="TYPE_NO_AIM_ROW",
            message=(
                f"Slot {merc.uiIndex} has no AIMAvailability row yet. "
                "Merc Forge will add one during save so the merc appears on "
                "the AIM laptop."
            ),
            suggested_fix=(
                "No action required — the row is written automatically. If you "
                "DON'T want this merc on AIM, change Type to 3 (RPC) or 4 (NPC)."
            ),
        ))

    if merc.Type == 2 and not merc_row_present:
        issues.append(Issue(
            severity=Severity.WARN,
            field="Type",
            code="TYPE_NO_MERC_ROW",
            message=(
                f"Slot {merc.uiIndex} has no MercAvailability row yet. "
                "Merc Forge will add one during save so the merc appears on "
                "Speck's M.E.R.C. laptop."
            ),
            suggested_fix=(
                "No action required — the row is written automatically. If you "
                "DON'T want this merc on M.E.R.C., change Type to 3 (RPC) or 4 (NPC)."
            ),
        ))

    if slot_is_quest_bound:
        issues.append(Issue(
            severity=Severity.WARN,
            field="uiIndex",
            code="SLOT_QUEST_BOUND_OVERWRITE",
            message=(
                f"Slot {merc.uiIndex} is in the engine's named RPC/NPC range "
                "(57-159 in vanilla 1.13). Quest scripts still reference this "
                "slot by its original name — overwriting redirects the scripted "
                "dialogue and quest hooks to your replacement merc."
            ),
            suggested_fix=(
                "OK if you intend to replace the named character. If not, pick "
                "an unnamed slot (the picker labels the engine-named ones)."
            ),
        ))

    # Type=RPC at a vanilla AIM slot OR a slot that has an AIM row right now:
    # the Marcus-at-slot-57 trap. RPC mercs are recruitable via quest events
    # but never display on the AIM laptop (the engine only renders Type=1
    # there). Even if no row exists yet, picking Type=3 at a slot the AIM
    # site normally indexes is almost certainly a misclick.
    aim_trap_zone = aim_row_present or _is_aim_bound_fallback(merc.uiIndex)
    if merc.Type == 3 and aim_trap_zone:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="Type",
            code="NPC_IN_AIM_SLOT",
            message=(
                f"Type=RPC (3) at slot {merc.uiIndex}, an AIM-indexed slot. "
                "The merc is recruitable via quest events but won't appear on "
                "the AIM laptop website (the Marcus-at-slot-57 bug). The engine "
                "only displays Type=1 (AIM) mercs on the laptop. Change Type to "
                "1 (AIM) for AIM-website visibility."
            ),
            suggested_fix="Set Type=1 (AIM) if you want this merc to appear on the AIM website",
        ))

    # uiIndex bounds
    if not 0 <= merc.uiIndex <= 255:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="uiIndex",
            code="UI_INDEX_OUT_OF_RANGE",
            message=f"uiIndex must be in [0, 255], got {merc.uiIndex}",
        ))

    # ubBodyType must be a known SoldierBodyTypes enum value. Unknown
    # values index past the end of gAnimControl[] and crash on render
    # (engine invariant — see wasteland-engine-systems source notes).
    if merc.ubBodyType not in KNOWN_BODY_TYPES:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="ubBodyType",
            code="BODY_TYPE_UNKNOWN",
            message=(
                f"ubBodyType={merc.ubBodyType} is not a known SoldierBodyTypes "
                "enum value. Valid: 0-3 (humans), 42-48 (monsters), 59-61 "
                "(animals). Other values index past the engine's animation "
                "surface array and crash on render."
            ),
            suggested_fix="Set to 0 (REGMALE) for a default human male merc",
        ))
    else:
        body_name, body_sex = KNOWN_BODY_TYPES[merc.ubBodyType]
        # Cross-check bSex when the body type implies one
        if body_sex is not None:
            merc_sex_label = "female" if merc.bSex == 1 else "male"
            if body_sex != merc_sex_label:
                issues.append(Issue(
                    severity=Severity.WARN,
                    field="bSex",
                    code="SEX_BODY_TYPE_MISMATCH",
                    message=(
                        f"bSex={merc.bSex} ({merc_sex_label}) doesn't match "
                        f"ubBodyType={merc.ubBodyType} ({body_name}, {body_sex}). "
                        "Tactical sprite will render with the body's sex; AIM portrait/"
                        "pronouns will use bSex. Pick one or the other."
                    ),
                    suggested_fix=(
                        f"Set bSex={0 if body_sex == 'male' else 1} to match the body type, "
                        "or pick a body type matching the chosen sex."
                    ),
                ))

    # bRace: warn if outside vanilla 0-4 range (mods extend, so not an error)
    if merc.bRace > VANILLA_RACE_MAX:
        issues.append(Issue(
            severity=Severity.INFO,
            field="bRace",
            code="RACE_NON_VANILLA",
            message=(
                f"bRace={merc.bRace} is outside vanilla's 0-4 range. "
                "This is fine for mods that extend the race table — make sure "
                "the active install actually has that race configured."
            ),
        ))

    return issues


def audit_gear(gear: Gear, merc: Optional[Merc] = None) -> list[Issue]:
    """Validate a Gear block."""
    issues: list[Issue] = []

    if merc is not None and gear.mIndex != merc.uiIndex:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="mIndex",
            code="GEAR_MERC_MISMATCH",
            message=f"Gear mIndex={gear.mIndex} doesn't match Merc.uiIndex={merc.uiIndex}",
            suggested_fix=f"Set mIndex to {merc.uiIndex}",
        ))

    for kit_idx, kit in enumerate(gear.kits):
        if kit.mAbsolutePrice != -1:
            issues.append(Issue(
                severity=Severity.ERROR,
                field=f"kits[{kit_idx}].mAbsolutePrice",
                code="GEAR_PRICE_NOT_MINUS_ONE",
                message=(
                    f"mAbsolutePrice={kit.mAbsolutePrice}. Must be -1 "
                    "(engine auto-calculates). 0 greys out gear in AIM UI."
                ),
                suggested_fix="Set mAbsolutePrice=-1",
            ))
        if kit.mWeapon == 0:
            issues.append(Issue(
                severity=Severity.WARN,
                field=f"kits[{kit_idx}].mWeapon",
                code="GEAR_NO_WEAPON",
                message=f"Kit '{kit.mGearKitName}' has no primary weapon (mWeapon=0). Merc will spawn unarmed.",
            ))

    return issues


def audit_aim_binding(binding: AimBinding, merc: Optional[Merc] = None) -> list[Issue]:
    """Validate an AimBinding."""
    issues: list[Issue] = []

    if merc is not None and binding.uiIndex != merc.uiIndex:
        issues.append(Issue(
            severity=Severity.ERROR,
            field="uiIndex",
            code="AIM_BINDING_MERC_MISMATCH",
            message=f"AimBinding.uiIndex={binding.uiIndex} doesn't match Merc.uiIndex={merc.uiIndex}",
        ))

    if merc is not None and binding.ProfilId != merc.uiIndex:
        issues.append(Issue(
            severity=Severity.WARN,
            field="ProfilId",
            code="PROFILE_ID_MISMATCH",
            message=(
                f"ProfilId={binding.ProfilId} doesn't match uiIndex={binding.uiIndex}. "
                "Usually they should be equal."
            ),
        ))

    return issues


def audit_eye_spacing(eye_spacing_px_at_48x43: float) -> list[Issue]:
    """Check eye spacing against the vanilla 67-portrait canonical range.

    Out-of-bounds spacing means the 17×6 eye sub-frame won't align with the
    actual eye positions in the base portrait, so the blink animation will
    look misregistered.
    """
    issues: list[Issue] = []
    if eye_spacing_px_at_48x43 < EYE_SPACING_MIN:
        adjustment = EYE_SPACING_AVG / eye_spacing_px_at_48x43
        issues.append(Issue(
            severity=Severity.WARN,
            field="eye_spacing",
            code="EYE_SPACING_TOO_NARROW",
            message=(
                f"Detected eye spacing {eye_spacing_px_at_48x43:.2f}px is below "
                f"vanilla minimum {EYE_SPACING_MIN}px (avg {EYE_SPACING_AVG}px). "
                "Blink animation may look pinched."
            ),
            suggested_fix=f"Adjust TARGET_FACE_WIDTH by ×{adjustment:.3f} (= {EYE_SPACING_AVG}/{eye_spacing_px_at_48x43:.2f})",
        ))
    elif eye_spacing_px_at_48x43 > EYE_SPACING_MAX:
        adjustment = EYE_SPACING_AVG / eye_spacing_px_at_48x43
        issues.append(Issue(
            severity=Severity.WARN,
            field="eye_spacing",
            code="EYE_SPACING_TOO_WIDE",
            message=(
                f"Detected eye spacing {eye_spacing_px_at_48x43:.2f}px is above "
                f"vanilla maximum {EYE_SPACING_MAX}px (avg {EYE_SPACING_AVG}px). "
                "Blink animation may look stretched."
            ),
            suggested_fix=f"Adjust TARGET_FACE_WIDTH by ×{adjustment:.3f}",
        ))
    return issues


def audit_full(
    merc: Merc,
    gear: Optional[Gear] = None,
    aim_binding: Optional[AimBinding] = None,
    eye_spacing_px_at_48x43: Optional[float] = None,
    *,
    slot_info: Optional[SlotInfo] = None,
) -> list[Issue]:
    """Run every applicable check and aggregate issues.

    Pass ``slot_info`` (from :func:`slot_picker.build_slot_picker`) to use
    live AIM/MERC row data. Without it, audit falls back to vanilla 1.13
    data conventions.
    """
    issues = audit_merc(merc, slot_info=slot_info)
    if gear is not None:
        issues.extend(audit_gear(gear, merc))
    if aim_binding is not None:
        issues.extend(audit_aim_binding(aim_binding, merc))
    if eye_spacing_px_at_48x43 is not None:
        issues.extend(audit_eye_spacing(eye_spacing_px_at_48x43))
    return issues


def has_errors(issues: list[Issue]) -> bool:
    """True if any issue is severity=ERROR (i.e. compile would be unsafe)."""
    return any(i.severity == Severity.ERROR for i in issues)
