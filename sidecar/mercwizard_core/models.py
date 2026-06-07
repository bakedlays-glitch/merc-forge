"""Pydantic models for the merc data domain.

Schema reflects MercProfiles.xml + MercStartingGear.xml + AIMAvailability.xml.
Sources:
- MercWizard 1.x HTML schema (extracted to enum tables in `data/enums.json`)
- Headless_Compiler config JSONs (e.g. slot15_config.json)
- merc_integration.md (slot map, EDT routing, field caps)

Field-cap rules:
- zNickname: 9 chars max (AIM UI truncates beyond)
- zName: 50 chars max
- biographyText: 400 chars max (EDT silently truncates at [:400])
- additionalInfoText: 160 chars max (EDT silently truncates at [:160])

The library does NOT auto-truncate. The audit module flags violations and the
inject layer refuses to write over-limit fields. That way the player sees the
problem before the game does.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileType(IntEnum):
    """Profile type enum from Visual Studio Root/Tactical/Soldier Profile.h."""
    NONE = 0
    AIM = 1
    MERC = 2
    RPC = 3
    NPC = 4
    VEHICLE = 5
    IMP = 6


# Palette enum values written as strings into MercProfiles.xml.
# Vanilla 1.13 ships a fixed set, but mods routinely add codes (e.g. The
# Wasteland uses BLACKSHIRT). We type these as `str` so any mod-defined code
# parses; the UI dropdowns still curate the vanilla list as suggestions.
PantsCode = str
VestCode = str
SkinCode = str
HairCode = str

# For UI dropdowns / autocomplete (informational, not enforced)
VANILLA_PANTS_CODES = (
    "TANPANTS", "BLUEPANTS", "JEANPANTS", "BEIGEPANTS",
    "BLACKPANTS", "GREENPANTS", "BROWNPANTS",
)
VANILLA_VEST_CODES = (
    "BROWNVEST", "BLUEVEST", "WHITEVEST", "GREENVEST",
    "YELLOWVEST", "REDVEST", "BLACKVEST", "PURPLESHIRT",
)
VANILLA_SKIN_CODES = ("PINKSKIN", "TANSKIN", "DARKSKIN", "BLACKSKIN")
VANILLA_HAIR_CODES = ("BROWNHEAD", "BLACKHEAD", "WHITEHEAD", "BLONDHEAD", "REDHEAD")


class Merc(BaseModel):
    """One mercenary's complete MercProfiles.xml + EDT data.

    Field names match the canonical JA2 XML tags so serialization is direct.

    `extra="ignore"` (not "forbid"): tolerates unknown XML fields from
    mod-extended schemas (AIMNAS GrowthModifier*, etc.) AND custom
    annotations other tools may add to MercProfiles.xml rows (e.g., some
    external editors write `bigFaceImagePath`, `alphaThreshold`, `alphaThresholdEye`).
    Pre-fix, reading Slot 0 on an install that had been touched by another
    tool returned a profile dict with extra keys, the frontend round-tripped
    them through PUT /merc/{slot}, and Pydantic 422'd the request.
    Unknown fields are dropped from the Merc instance on validation; the
    XML upsert preserves any fields it doesn't know about by leaving them
    untouched in the existing row (surgical update, not delete-then-write).

    Matches the WmercManifest root's `extra="ignore"` decision per
    MercWizard2/CLAUDE.md § ".wmerc bundle format".
    """
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    # ── Identity & face routing ────────────────────────────────────────────
    uiIndex: int = Field(..., ge=0, le=255, description="Profile slot in MercProfiles.xml")
    ubFaceIndex: int = Field(..., ge=0, le=999, description="STI asset ID; ≥160 for custom mercs")
    Type: int = Field(default=1, ge=0, le=6, description="0 None / 1 AIM / 2 MERC / 3 RPC / 4 NPC / 5 Vehicle / 6 IMP")
    zName: str = Field(..., max_length=50)
    zNickname: str = Field(..., max_length=9)
    bSex: int = Field(default=0, ge=0, le=1)
    # Engine uses UINT8; only specific values map to valid soldier sprites
    # (Tactical/Animation Data.h SoldierBodyTypes). Audit warns on unknown.
    ubBodyType: int = Field(default=0, ge=0, le=255)
    uiBodyTypeSubFlags: int = Field(default=0, ge=0, le=1)
    usVoiceIndex: int = Field(default=15, ge=0, le=255)
    # Vanilla bRace is 0-4 (American/Hispanic/European/African/Asian); mods
    # routinely extend. Accept full UINT8 range, audit can warn on out-of-vanilla.
    bRace: int = Field(default=0, ge=0, le=255)
    bNationality: int = Field(default=0, ge=-1, le=112)

    # ── Portrait coordinates (48×43 SmallFace space) ───────────────────────
    usEyesX: int = Field(default=10, ge=0, le=255)
    usEyesY: int = Field(default=8, ge=0, le=255)
    usMouthX: int = Field(default=7, ge=0, le=255)
    usMouthY: int = Field(default=28, ge=0, le=255)
    uiEyeDelay: int = Field(default=0, ge=0, le=65535)
    uiMouthDelay: int = Field(default=0, ge=0, le=65535)
    uiBlinkFrequency: int = Field(default=3000, ge=0, le=65535)
    uiExpressionFrequency: int = Field(default=2000, ge=0, le=65535)

    # ── Appearance palette codes ───────────────────────────────────────────
    PANTS: PantsCode = "BROWNPANTS"
    VEST: VestCode = "BROWNVEST"
    SKIN: SkinCode = "PINKSKIN"
    HAIR: HairCode = "BROWNHEAD"

    # ── Attributes ─────────────────────────────────────────────────────────
    # Engine uses INT8 for stats; mods can exceed the gameplay-sane 0-100
    # range (AIMNAS commonly has 110+ stats). We accept the full signed-byte
    # range here so reads from any install succeed; audit.py separately
    # enforces the gameplay-sane bounds when CREATING a new merc.
    bLifeMax: int = Field(default=80, ge=-128, le=255)
    bLife: int = Field(default=80, ge=-128, le=255)
    bStrength: int = Field(default=70, ge=-128, le=255)
    bAgility: int = Field(default=70, ge=-128, le=255)
    bDexterity: int = Field(default=70, ge=-128, le=255)
    bWisdom: int = Field(default=70, ge=-128, le=255)
    bExpLevel: int = Field(default=3, ge=0, le=255)
    # bEvolution is a vanilla MERCPROFILESTRUCT field tracking veteran progression
    # (0=fresh, increments on milestones). Required in every <PROFILE> block;
    # missing it leaves the engine reading uninitialized memory.
    bEvolution: int = Field(default=0, ge=0, le=255)
    bMarksmanship: int = Field(default=70, ge=-128, le=255)
    bExplosive: int = Field(default=20, ge=-128, le=255)
    bLeadership: int = Field(default=30, ge=-128, le=255)
    bMedical: int = Field(default=15, ge=-128, le=255)
    bMechanical: int = Field(default=20, ge=-128, le=255)
    fRegresses: int = Field(default=0, ge=0, le=1)

    # ── Growth modifiers (signed) ──────────────────────────────────────────
    GrowthModifierLife: int = Field(default=0, ge=-100, le=100)
    GrowthModifierStrength: int = Field(default=0, ge=-100, le=255)
    GrowthModifierAgility: int = Field(default=0, ge=-100, le=255)
    GrowthModifierDexterity: int = Field(default=0, ge=-100, le=255)
    GrowthModifierWisdom: int = Field(default=0, ge=-100, le=255)
    GrowthModifierMarksmanship: int = Field(default=0, ge=-100, le=255)
    GrowthModifierExplosive: int = Field(default=0, ge=-100, le=255)
    GrowthModifierLeadership: int = Field(default=0, ge=-100, le=255)
    GrowthModifierMedical: int = Field(default=0, ge=-100, le=255)
    GrowthModifierMechanical: int = Field(default=0, ge=-100, le=255)
    GrowthModifierExpLevel: int = Field(default=0, ge=-100, le=100)

    # ── Traits (old + new systems coexist; install determines which is active) ──
    bOldSkillTrait: int = Field(default=0, ge=0, le=16)
    bOldSkillTrait2: int = Field(default=0, ge=0, le=16)
    # Engine schema supports up to bNewSkillTrait30. v1 UI exposes slots 1-4;
    # 5-30 default to 0 (None) for forward compatibility with mods that allow
    # multi-trait specialization. Writing them as zero is harmless.
    bNewSkillTrait1: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait2: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait3: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait4: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait5: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait6: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait7: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait8: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait9: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait10: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait11: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait12: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait13: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait14: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait15: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait16: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait17: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait18: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait19: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait20: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait21: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait22: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait23: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait24: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait25: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait26: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait27: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait28: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait29: int = Field(default=0, ge=0, le=255)
    bNewSkillTrait30: int = Field(default=0, ge=0, le=255)

    # ── Background ─────────────────────────────────────────────────────────
    # Engine field is USHORT; mods routinely add backgrounds well beyond
    # vanilla's 356 entries. Accept any USHORT value. Default 0 = no background
    # (the engine inits usBackground to 0); 255 is NOT "none" — it's a real
    # background ("Explosives Technician") in current 1.13 data.
    usBackground: int = Field(default=0, ge=0, le=65535)

    # ── Personality ────────────────────────────────────────────────────────
    # Same logic as stats — engine fields are INT8/UINT8, so we widen the
    # model to the byte range. audit.py keeps the gameplay-sane enum bounds.
    bAttitude: int = Field(default=0, ge=-128, le=127)
    bCharacterTrait: int = Field(default=0, ge=-128, le=127)
    bDisability: int = Field(default=0, ge=-128, le=127)
    ubNeedForSleep: int = Field(default=8, ge=0, le=255)
    bReputationTolerance: int = Field(default=50, ge=-128, le=127)
    bDeathRate: int = Field(default=50, ge=-128, le=127)
    bAppearance: int = Field(default=0, ge=-128, le=127)
    bAppearanceCareLevel: int = Field(default=0, ge=-128, le=127)
    bRefinement: int = Field(default=0, ge=-128, le=127)
    bRefinementCareLevel: int = Field(default=0, ge=-128, le=127)
    bHatedNationality: int = Field(default=-1, ge=-128, le=127)
    bHatedNationalityCareLevel: int = Field(default=0, ge=-128, le=127)
    bRacist: int = Field(default=0, ge=-128, le=127)
    bSexist: int = Field(default=0, ge=-128, le=127)
    fGoodGuy: int = Field(default=0, ge=0, le=1)

    # ── Relationships ──────────────────────────────────────────────────────
    bBuddy1: int = Field(default=255, ge=0, le=255)
    bBuddy2: int = Field(default=255, ge=0, le=255)
    bBuddy3: int = Field(default=255, ge=0, le=255)
    bBuddy4: int = Field(default=255, ge=0, le=255)
    bBuddy5: int = Field(default=255, ge=0, le=255)
    bHated1: int = Field(default=255, ge=0, le=255)
    bHatedTime1: int = Field(default=0, ge=0, le=255)
    bHated2: int = Field(default=255, ge=0, le=255)
    bHatedTime2: int = Field(default=0, ge=0, le=255)
    bHated3: int = Field(default=255, ge=0, le=255)
    bHatedTime3: int = Field(default=0, ge=0, le=255)
    bHated4: int = Field(default=255, ge=0, le=255)
    bHatedTime4: int = Field(default=0, ge=0, le=255)
    bHated5: int = Field(default=255, ge=0, le=255)
    bHatedTime5: int = Field(default=0, ge=0, le=255)
    bLearnToLike: int = Field(default=255, ge=0, le=255)
    bLearnToLikeTime: int = Field(default=0, ge=0, le=255)
    bLearnToHate: int = Field(default=255, ge=0, le=255)
    bLearnToHateTime: int = Field(default=0, ge=0, le=255)

    # ── Economy ────────────────────────────────────────────────────────────
    sSalary: int = Field(default=1000, ge=0, le=100000)
    uiWeeklySalary: int = Field(default=6000, ge=0, le=500000)
    uiBiWeeklySalary: int = Field(default=11000, ge=0, le=1000000)
    bMedicalDeposit: int = Field(default=0, ge=0, le=1)
    sMedicalDepositAmount: int = Field(default=0, ge=0, le=100000)
    usOptionalGearCost: int = Field(default=1000, ge=0, le=100000)
    # Attractiveness fields: 1.13 source `MERCPROFILESTRUCT` declares these
    # as INT8 (signed -128 to 127). Real-world data (AIMNAS, Arulco
    # Vacations) ships values up to ~250 + occasionally negative — engine
    # is permissive about the range. Widened from le=255 to capture the
    # INT16 envelope the engine actually tolerates without rejecting real
    # mod data.
    bArmourAttractiveness: int = Field(default=20, ge=-32768, le=32767)
    bMainGunAttractiveness: int = Field(default=20, ge=-32768, le=32767)

    # ── Dialogue triggers ──────────────────────────────────────────────────
    # usApproachFactor* are UINT16 in the engine (`usApproachFactor[4]` in
    # MERCPROFILESTRUCT). Several mods (Vengeance, Arulco Vacations,
    # AIMNAS) ship values in the 256-65535 range. Widened from le=255 to
    # the actual UINT16 ceiling so manifest parsing doesn't reject real
    # source data.
    usApproachFactorFriendly: int = Field(default=100, ge=0, le=65535)
    usApproachFactorDirect: int = Field(default=100, ge=0, le=65535)
    usApproachFactorThreaten: int = Field(default=100, ge=0, le=65535)
    usApproachFactorRecruit: int = Field(default=100, ge=0, le=65535)

    # ── Location (mostly NPCs) ─────────────────────────────────────────────
    sSectorX: int = Field(default=0, ge=0, le=16)
    sSectorY: int = Field(default=0, ge=0, le=16)
    sSectorZ: int = Field(default=0, ge=0, le=3)
    ubCivilianGroup: int = Field(default=0, ge=0, le=255)
    bTown: int = Field(default=0, ge=0, le=255)
    bTownAttachment: int = Field(default=0, ge=0, le=255)

    # ── EDT-bound free text ────────────────────────────────────────────────
    biographyText: str = Field(default="", max_length=400)
    additionalInfoText: str = Field(default="", max_length=160)

    @field_validator("zNickname")
    @classmethod
    def _nickname_min_length(cls, v: str) -> str:
        if len(v) < 1:
            raise ValueError("zNickname must be at least 1 character")
        return v

    @field_validator("zName")
    @classmethod
    def _name_min_length(cls, v: str) -> str:
        if len(v) < 1:
            raise ValueError("zName must be at least 1 character")
        return v

    @property
    def is_aim_bound_slot(self) -> bool:
        raise NotImplementedError(
            "Static slot ranges are wrong — AIM membership is XML-driven. "
            "Use mercwizard_core.slot_picker.build_slot_picker(ctx).slots[slot]"
            ".aim_row.present (live AIMAvailability.xml row) or .category == 'aim'."
        )

    @property
    def is_merc_bound_slot(self) -> bool:
        raise NotImplementedError(
            "Static slot ranges are wrong — MERC membership is XML-driven. "
            "Use mercwizard_core.slot_picker.build_slot_picker(ctx).slots[slot]"
            ".merc_row.present (live MercAvailability.xml row) or .category == 'merc'."
        )


class GearKit(BaseModel):
    """One starting-gear kit. Mercs may have multiple kits (Standard, Combat, ...).
    The v1 wizard writes only one kit ("Standard") but reads multiple.
    """
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    mGearKitName: str = "Standard"
    mHelmet: int = Field(default=0, ge=0, le=9999)
    mVest: int = Field(default=0, ge=0, le=9999)
    mLeg: int = Field(default=0, ge=0, le=9999)
    mWeapon: int = Field(default=0, ge=0, le=9999)
    mBig0: int = Field(default=0, ge=0, le=9999)
    mBig0Status: int = Field(default=100, ge=0, le=100)
    mBig0Quantity: int = Field(default=0, ge=0, le=255)
    mBig1: int = Field(default=0, ge=0, le=9999)
    mBig1Status: int = Field(default=0, ge=0, le=100)
    mBig1Quantity: int = Field(default=0, ge=0, le=255)
    mBig2: int = Field(default=0, ge=0, le=9999)
    mBig2Status: int = Field(default=0, ge=0, le=100)
    mBig2Quantity: int = Field(default=0, ge=0, le=255)
    mBig3: int = Field(default=0, ge=0, le=9999)
    mBig3Status: int = Field(default=0, ge=0, le=100)
    mBig3Quantity: int = Field(default=0, ge=0, le=255)
    mSmall0: int = Field(default=0, ge=0, le=9999)
    mSmall1: int = Field(default=0, ge=0, le=9999)
    mSmall2: int = Field(default=0, ge=0, le=9999)
    mSmall3: int = Field(default=0, ge=0, le=9999)
    mSmall4: int = Field(default=0, ge=0, le=9999)
    mSmall5: int = Field(default=0, ge=0, le=9999)
    mSmall6: int = Field(default=0, ge=0, le=9999)
    mSmall7: int = Field(default=0, ge=0, le=9999)
    mPriceMod: int = Field(default=0, ge=-100, le=100)
    mAbsolutePrice: int = Field(default=-1, description="MUST be -1; 0 greys out gear in AIM UI")

    @field_validator("mAbsolutePrice")
    @classmethod
    def _absolute_price_must_be_minus_one(cls, v: int) -> int:
        if v != -1:
            raise ValueError(
                f"mAbsolutePrice must be -1 (engine auto-calculates), got {v}. "
                "Setting to 0 greys out the gear in the AIM hiring UI."
            )
        return v


class Gear(BaseModel):
    """A merc's complete MercStartingGear.xml <MERCGEAR> block."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    mIndex: int = Field(..., ge=0, le=255, description="Must match Merc.uiIndex")
    mName: str = ""
    kits: list[GearKit] = Field(default_factory=lambda: [GearKit()])

    @field_validator("kits")
    @classmethod
    def _at_least_one_kit(cls, v: list[GearKit]) -> list[GearKit]:
        if len(v) == 0:
            raise ValueError("Gear must have at least one GearKit; default is 'Standard'")
        return v


class AimBinding(BaseModel):
    """One <AIM> entry in AIMAvailability.xml.

    Critical: <ProfilId> is the canonical XML field name (single L — vanilla typo).
    <AimBioID> determines the AIMBIOS.EDT offset: offset = AimBioID × 1120.

    The existing compile_merc.py uses uiIndex × 1120 (wrong for slots 170+). The
    wizard explicitly uses AimBioID × 1120 to avoid the documented bug.

    Placeholder rows: modded AIMAvailability.xml ships rows for every slot
    0-254 with `<ProfilId>-1</ProfilId>` and `<AimBioID>-1</AimBioID>` on
    unbound slots. We accept -1 (instead of raising at Pydantic validation
    and dropping the row) so `lookup_aim_bio_id` can see those rows and
    correctly treat them as unbound rather than letting `compute_aim_bio_id`
    re-allocate an ID a placeholder reserved.
    """
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    uiIndex: int = Field(..., ge=0, le=255)
    description: str = Field(..., max_length=64)
    ProfilId: int = Field(..., ge=-1, le=255)
    AimBioID: int = Field(..., ge=-1, le=199)


class MercBinding(BaseModel):
    """One <MERC> entry in MercAvailability.xml.

    Same logical role as AimBinding but for Speck's M.E.R.C. website. Engine
    reads the merc's biography from MERCBIOS.EDT at `MercBioID × 1120` for
    every Type=2 (MERC) merc — vanilla 40-50 AND the expansion ranges
    (178-185, 188-199, 244, 247, 249, 252-253). The MercWizard 1.x routing
    that puts expansion bios in `MercEdt/<n>.EDT` matches no engine path —
    those files exist but are ignored. The fix is the M.E.R.C. equivalent
    of the AimBioID × 1120 bug: route every Type=2 bio to MERCBIOS.EDT at
    MercBioID × 1120, reading MercBioID from this row.

    Vanilla 40-50 uses `MercBioID = uiIndex - 40` (0-10) by long-standing
    convention. Mods extend MERCBIOS.EDT and allocate MercBioIDs ad-hoc for
    expansion slots; the wizard reads them from MercAvailability.xml and
    allocates a fresh one when adding a new MERC-bound slot.

    Schema (Vengeance MercAvailability.xml):
        <MERC>
            <uiIndex>12</uiIndex>            <!-- display position in M.E.R.C. UI -->
            <Name>Wahan</Name>
            <Drunk>0</Drunk>
            <uiAlternateIndex>-1</uiAlternateIndex>
            <StartMercsAvailable>0</StartMercsAvailable>
            <NewMercsAvailable>0</NewMercsAvailable>
            <MercBioID>42</MercBioID>        <!-- offset into MERCBIOS.EDT × 1120 -->
            <ProfilId>198</ProfilId>         <!-- pointer to MercProfiles.xml slot -->
            <usMoneyPaid>100</usMoneyPaid>
            <usDay>2</usDay>
        </MERC>
    """
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    uiIndex: int = Field(..., ge=-1, le=255, description="Display order in M.E.R.C. roster, not a profile slot")
    Name: str = Field(..., max_length=64)
    Drunk: int = Field(default=0, ge=0, le=1)
    uiAlternateIndex: int = Field(default=-1, ge=-1, le=255)
    StartMercsAvailable: int = Field(default=1, ge=0, le=1)
    NewMercsAvailable: int = Field(default=0, ge=0, le=1)
    # MercBioID and ProfilId accept -1 to round-trip placeholder rows in
    # modded XMLs that pre-populate every slot 0-254 with disabled entries.
    # `lookup_merc_bio_id` treats -1 as unbound so callers know to allocate
    # a fresh ID; without the relaxation, Pydantic dropped the rows and
    # `compute_merc_bio_id` could re-allocate an ID a placeholder reserved.
    MercBioID: int = Field(..., ge=-1, le=199, description="Offset into MERCBIOS.EDT, in records of 1120 bytes")
    ProfilId: int = Field(..., ge=-1, le=255, description="MercProfiles.xml slot this row points at")
    usMoneyPaid: int = Field(default=0, ge=0, le=65535)
    usDay: int = Field(default=0, ge=0, le=65535)
