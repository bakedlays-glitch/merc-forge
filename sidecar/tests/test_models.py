"""Tests for the pydantic data model."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mercwizard_core.models import AimBinding, Gear, GearKit, Merc


def test_merc_minimal_valid(sample_merc: Merc) -> None:
    assert sample_merc.uiIndex == 220
    assert sample_merc.zName == "Tycho"


def test_merc_nickname_max_9_chars() -> None:
    with pytest.raises(ValidationError):
        Merc(uiIndex=10, ubFaceIndex=10, zName="Test", zNickname="A" * 10)


def test_merc_name_max_50_chars() -> None:
    with pytest.raises(ValidationError):
        Merc(uiIndex=10, ubFaceIndex=10, zName="A" * 51, zNickname="Test")


def test_merc_bio_max_400_chars() -> None:
    with pytest.raises(ValidationError):
        Merc(
            uiIndex=10, ubFaceIndex=10, zName="Test", zNickname="Test",
            biographyText="x" * 401,
        )


def test_merc_addl_info_max_160_chars() -> None:
    with pytest.raises(ValidationError):
        Merc(
            uiIndex=10, ubFaceIndex=10, zName="Test", zNickname="Test",
            additionalInfoText="x" * 161,
        )


def test_is_aim_bound_slot_is_deprecated() -> None:
    """is_aim_bound_slot is deprecated — static slot ranges are wrong because
    AIM membership is XML-driven. Callers should query slot_picker instead."""
    m = Merc(uiIndex=10, ubFaceIndex=160, zName="X", zNickname="X")
    with pytest.raises(NotImplementedError, match="slot_picker"):
        _ = m.is_aim_bound_slot


def test_is_merc_bound_slot_is_deprecated() -> None:
    """is_merc_bound_slot is deprecated — see slot_picker."""
    m = Merc(uiIndex=45, ubFaceIndex=160, zName="X", zNickname="X")
    with pytest.raises(NotImplementedError, match="slot_picker"):
        _ = m.is_merc_bound_slot


def test_gear_kit_must_have_absolute_price_minus_one() -> None:
    """The canonical gear rule: mAbsolutePrice MUST be -1."""
    with pytest.raises(ValidationError):
        GearKit(mAbsolutePrice=0)
    with pytest.raises(ValidationError):
        GearKit(mAbsolutePrice=100)
    # -1 must work
    k = GearKit(mAbsolutePrice=-1)
    assert k.mAbsolutePrice == -1


def test_gear_must_have_at_least_one_kit() -> None:
    with pytest.raises(ValidationError):
        Gear(mIndex=10, kits=[])


def test_aim_binding_valid(sample_aim_binding: AimBinding) -> None:
    assert sample_aim_binding.ProfilId == 220
    assert sample_aim_binding.AimBioID == 52


def test_aim_binding_aim_bio_id_bounds() -> None:
    """AimBioID is bounded [-1, 199] — -1 is the documented placeholder
    marker for unbound rows in modded AIMAvailability.xml files, and 199
    is the upper bound the 1120-byte AIMBIOS.EDT layout supports.
    """
    with pytest.raises(ValidationError):
        AimBinding(uiIndex=0, description="X", ProfilId=0, AimBioID=200)
    with pytest.raises(ValidationError):
        AimBinding(uiIndex=0, description="X", ProfilId=0, AimBioID=-2)
    # -1 must succeed — modded XMLs use it for placeholder rows; the
    # lookup function treats it as "unbound" rather than as a real binding.
    placeholder = AimBinding(uiIndex=0, description="X", ProfilId=-1, AimBioID=-1)
    assert placeholder.AimBioID == -1
    assert placeholder.ProfilId == -1
