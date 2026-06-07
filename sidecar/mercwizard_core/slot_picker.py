"""Engine-faithful slot picker.

This module replaces the old static slot-range heuristics in ``slotClass.ts``,
``slot_locks.py``, and ``models.py``. The truth, sourced from the JA2 1.13
engine at ``Tactical/soldier profile type.h`` and the laptop screens in
``Laptop/aim*.cpp`` / ``Laptop/mercs*.cpp``:

  * NUM_PROFILES = 255 (slots 0-254).
  * FIRST_RPC = 57 (non-UB) / 60 (UB).
  * FIRST_NPC = 75.
  * AIM membership = a row exists in AIMAvailability.xml with ProfilId != -1.
  * MERC membership = a row exists in MercAvailability.xml with ProfilId != 0.
    Note the empty-sentinel asymmetry: AIM uses -1, MERC uses 0.
  * MAX_NUMBER_MERCS is a UINT8 computed at boot from gAimAvailability rows
    (``Tactical/Soldier Profile.cpp:986-1006``); there is no compile-time cap.
  * The Members/Sorted/Archives laptop pages render an 8×5 mugshot grid with
    a 3-page limit, so the laptop only ever SHOWS the first 120 AIM rows.
  * Speck's M.E.R.C. page renders 12 rows at a time
    (``Laptop/mercs Account.cpp:66``).
  * Only two slots in the vanilla AIM range are named in the engine enum
    (VICKI=4, BUNS=17). The other 38 are unnamed in source — their identity
    lives entirely in MercProfiles.xml.
  * Vehicle slots 160-164 are written to by the engine on player purchase.
  * Main-story characters in 75-159 are referenced by name in quest scripts.

Tier derivation order (highest priority wins):

  1. vehicle / main-story locked → ``locked``
  2. named in 57-159 RPC/NPC range → ``quest_bound``
  3. has MercProfiles data AND (named in engine OR vanilla 1.13 ships data
     here) → ``vanilla_overwrite``
  4. otherwise → ``safe``

Category derivation:

  1. AIMAvailability row present (ProfilId != -1) → ``aim``
  2. MercAvailability row present (ProfilId != 0) → ``merc``
  3. FIRST_RPC ≤ slot < FIRST_NPC → ``rpc``
  4. FIRST_NPC ≤ slot < 160 → ``npc``
  5. vehicle / main-story named → ``locked``
  6. otherwise → ``unassigned``

Frontend uses category for filter chips; tier for color coding + warning
modals; row presence for the "A"/"M" badges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .engine_flags import EngineFlags, detect_engine_flags
from .inject import aim_availability, merc_availability
from .inject.profiles_xml import read_all_slots
from .install_context import InstallContext, make_install_context


NUM_PROFILES = 255
FIRST_RPC_NON_UB = 57
FIRST_RPC_UB = 60
FIRST_NPC = 75
FIRST_VEHICLE = 160
LAST_NAMED_VEHICLE = 164
FIRST_EXPANSION_MERC_NAMED = 165
LAST_EXPANSION_MERC_NAMED = 168
# Members/Sorted/Archives laptop pages: 8 cols × 5 rows × 3 pages cap.
MAX_LAPTOP_AIM_DISPLAY = 8 * 5 * 3  # 120


@dataclass(frozen=True)
class EngineNamedSlot:
    """One slot named in the JA2 1.13 engine source.

    ``is_main_story_locked`` flags slots whose loss would break the campaign
    (Queen, Skyrider, Speck, etc.). ``is_vehicle_slot`` flags 160-164, which
    the engine writes to on vehicle purchase. ``is_quest_bound`` flags named
    RPC/NPCs in the 57-159 range — overwriting works but redirects scripted
    dialogue to the replacement.
    """
    name: str
    role: str
    is_main_story_locked: bool = False
    is_vehicle_slot: bool = False
    is_quest_bound: bool = False


# ──────────────────────────────────────────────────────────────────────────
#  Vanilla 1.13 data conventions (used only for tier derivation, not the
#  authoritative engine truth)
# ──────────────────────────────────────────────────────────────────────────

# Slots that vanilla 1.13 MercProfiles.xml ships with real data. Used for
# "if data exists here AND it's a named slot OR vanilla shipped it, that's a
# vanilla_overwrite tier". This is a vanilla DATA convention, not an engine
# rule — mods can put new mercs anywhere.
_VANILLA_AIM_SLOT_RANGE = range(0, 40)
_VANILLA_MERC_SLOT_RANGE = range(40, 51)


def _build_engine_named_slots_non_ub() -> dict[int, EngineNamedSlot]:
    """The named-slot table for non-UB 1.13 builds.

    Encoded verbatim from ``Tactical/soldier profile type.h:81-228`` and
    ``Tactical/Vehicles.h:225-235``. Every constant the engine references by
    name appears here.
    """
    named: dict[int, EngineNamedSlot] = {}

    # Vanilla AIM (0-39): only VICKI and BUNS are named in the enum. The other
    # 38 vanilla AIM mercs exist in MercProfiles.xml but their identity is
    # data-driven, not engine-driven.
    named[4] = EngineNamedSlot("VICKI", "Vanilla AIM (referenced in Speck hire logic)")
    named[17] = EngineNamedSlot("BUNS", "Vanilla AIM")

    # Vanilla MERC (40-50) — every slot named in the engine enum (Speck's
    # dialog calls these by name).
    merc_names = [
        ("BIFF",         40), ("HAYWIRE",      41), ("GASKET",       42),
        ("RAZOR",        43), ("FLO",          44), ("GUMPY",        45),
        ("LARRY_NORMAL", 46), ("LARRY_DRUNK",  47), ("COUGAR",       48),
        ("NUMB",         49), ("BUBBA",        50),
    ]
    for name, slot in merc_names:
        named[slot] = EngineNamedSlot(name, "Vanilla M.E.R.C. contractor")

    # 51-56: GAP. Unnamed in soldier profile type.h. Vanilla data ships
    # nothing here; modern engine reads <Type> from XML so these slots are
    # safe to repurpose when fReadProfileDataFromXML=TRUE.

    # RPC range (57-74): named, quest-recruitable.
    rpc_names = [
        ("MIGUEL",   57), ("CARLOS",   58), ("IRA",      59),
        ("DIMITRI",  60), ("DEVIN",    61), ("ROBOT",    62),
        ("HAMOUS",   63), ("SLAY",     64), ("RPC65",    65),
        ("DYNAMO",   66), ("SHANK",    67), ("IGGY",     68),
        ("VINCE",    69), ("CONRAD",   70), ("RPC71",    71),
        ("MADDOG",   72), ("DARREL",   73), ("PERKO",    74),
    ]
    for name, slot in rpc_names:
        named[slot] = EngineNamedSlot(
            name,
            "Quest-recruitable RPC — scripts call this slot by name",
            is_quest_bound=True,
        )

    # NPC range (75-159): named, mostly scripted dialogue / quest references.
    # Main-story slots are flagged is_main_story_locked.
    npc_table: dict[int, tuple[str, str, bool]] = {
        75:  ("QUEEN",     "Deidranna — main antagonist (main quest)",                       True),
        76:  ("AUNTIE",    "Cambria quest giver",                                            False),
        77:  ("ENRICO",    "Player's employer — main quest checks",                          True),
        78:  ("CARMEN",    "Bounty hunter quest line",                                       False),
        79:  ("JOE",       "Scripted NPC",                                                   False),
        80:  ("STEVE",     "Scripted NPC",                                                   False),
        81:  ("RAT",       "San Mona quest line",                                            False),
        82:  ("ANNIE",     "Scripted NPC (Cambria)",                                         False),
        83:  ("CHRIS",     "Scripted NPC",                                                   False),
        84:  ("BOB",       "Scripted NPC",                                                   False),
        85:  ("BRENDA",    "Scripted NPC",                                                   False),
        86:  ("KINGPIN",   "San Mona mafia boss (major quest line)",                         True),
        87:  ("DARREN",    "San Mona boxing arena",                                          False),
        88:  ("MARIA",     "Scripted NPC",                                                   False),
        89:  ("ANGEL",     "Pawn shop owner",                                                False),
        90:  ("JOEY",      "Scripted NPC",                                                   False),
        91:  ("TONY",      "Black-market gun dealer",                                        False),
        92:  ("FRANK",     "Bartender (San Mona)",                                           False),
        93:  ("SPIKE",     "Scripted NPC",                                                   False),
        94:  ("DAMON",     "Scripted NPC",                                                   False),
        95:  ("KYLE",      "Scripted NPC",                                                   False),
        96:  ("MICKY",     "Hicks's buddy",                                                  False),
        97:  ("SKYRIDER",  "Helicopter pilot — strategic transport",                         True),
        98:  ("PABLO",     "Drassen baggage handler",                                        False),
        99:  ("SAL",       "Bartender",                                                      False),
        100: ("FATHER",    "Father Walker (Cambria) — major quest",                          True),
        101: ("FATIMA",    "Scripted NPC",                                                   False),
        102: ("WARDEN",    "Tixa prison warden",                                              False),
        103: ("GORDON",    "Scripted NPC",                                                   False),
        104: ("GABBY",     "Witch doctor (Tixa)",                                             False),
        105: ("ERNEST",    "Scripted NPC",                                                   False),
        106: ("FRED",      "Scripted NPC",                                                   False),
        107: ("MADAME",    "Brothel owner (San Mona)",                                       False),
        108: ("YANNI",     "Scripted NPC",                                                   False),
        109: ("MARTHA",    "Scripted NPC",                                                   False),
        110: ("TIFFANY",   "Scripted NPC",                                                   False),
        111: ("T_REX",     "Mine boss",                                                       True),
        112: ("DRUGGIST",  "Drug merchant (Elgin)",                                           False),
        113: ("JAKE",      "Scripted NPC",                                                   False),
        114: ("PACOS",     "Scripted NPC",                                                   False),
        115: ("GERARD",    "Scripted NPC",                                                   False),
        116: ("SKIPPER",   "Scripted NPC",                                                   False),
        117: ("HANS",      "Scripted NPC",                                                   False),
        118: ("JOHN",      "Scripted NPC (John Kulba)",                                      False),
        119: ("MARY",      "Scripted NPC (Mary Kulba)",                                      False),
        120: ("GENERAL",   "Meduna general — main quest endgame",                            True),
        121: ("SERGEANT",  "Scripted NPC",                                                   False),
        122: ("ARMAND",    "Scripted NPC",                                                   False),
        123: ("LORA",      "Scripted NPC",                                                   False),
        124: ("FRANZ",     "Scripted NPC",                                                   False),
        125: ("HOWARD",    "Scripted NPC",                                                   False),
        126: ("SAM",       "Scripted NPC",                                                   False),
        127: ("ELDIN",     "Scripted NPC",                                                   False),
        128: ("ARNIE",     "Repairman (Grumm)",                                              False),
        129: ("TINA",      "Scripted NPC",                                                   False),
        130: ("FREDO",     "Scripted NPC",                                                   False),
        131: ("WALTER",    "Bartender (San Mona)",                                            False),
        132: ("JENNY",     "Scripted NPC",                                                   False),
        133: ("BILLY",     "Scripted NPC",                                                   False),
        134: ("BREWSTER",  "Scripted NPC",                                                   True),
        135: ("ELLIOT",    "Deidranna's radio deputy — main quest dialogue",                 True),
        136: ("DEREK",     "Scripted NPC",                                                   False),
        137: ("OLIVER",    "Scripted NPC",                                                   False),
        138: ("WALDO",     "Scripted NPC",                                                   False),
        139: ("DOREEN",    "Sweat shop owner (Cambria)",                                     False),
        140: ("JIM",       "Scripted NPC",                                                   False),
        141: ("JACK",      "Scripted NPC",                                                   False),
        142: ("OLAF",      "Scripted NPC",                                                   False),
        143: ("RAY",       "Scripted NPC",                                                   False),
        144: ("OLGA",      "Scripted NPC",                                                   False),
        145: ("TYRONE",    "Scripted NPC",                                                   False),
        146: ("MADLAB",    "Robot lab researcher",                                            True),
        147: ("KEITH",     "Scripted NPC",                                                   False),
        148: ("MATT",      "Scripted NPC",                                                   False),
        149: ("MIKE",      "Scripted NPC",                                                   False),
        150: ("DARYL",     "Scripted NPC",                                                   False),
        151: ("HERVE",     "Scripted NPC",                                                   False),
        152: ("PETER",     "Scripted NPC",                                                   False),
        153: ("ALBERTO",   "Scripted NPC",                                                   False),
        154: ("CARLO",     "Scripted NPC",                                                   False),
        155: ("MANNY",     "Scripted NPC",                                                   False),
        156: ("OSWALD",    "Scripted NPC",                                                   False),
        157: ("CALVIN",    "Scripted NPC",                                                   False),
        158: ("CARL",      "Scripted NPC",                                                   False),
        159: ("SPECK",     "M.E.R.C. website owner — laptop UI",                              True),
    }
    for slot, (name, role, is_locked) in npc_table.items():
        named[slot] = EngineNamedSlot(
            name,
            role,
            is_main_story_locked=is_locked,
            is_quest_bound=not is_locked,
        )

    # Vehicle range 160-164: actively written to by engine on player purchase
    # / spawn.
    vehicle_table = [
        (160, "PROF_HUMMER",     "Hummer vehicle — engine writes state on purchase"),
        (161, "PROF_ELDERODO",   "El Dorado vehicle — engine writes state on purchase"),
        (162, "PROF_ICECREAM",   "Ice cream truck (Hamous's) — engine writes state"),
        (163, "PROF_HELICOPTER", "Skyrider's helicopter — engine writes state"),
        (164, "TANK_CAR",        "Tank vehicle (Vehicles.h:230) — engine reads in Queen Command + Soldier Create"),
    ]
    for slot, name, role in vehicle_table:
        named[slot] = EngineNamedSlot(name, role, is_vehicle_slot=True)

    # 1.13 expansion MERC named slots (165-168). These overlap dormant
    # NEW_VEHICLE reservations in Vehicles.h:231-234; treating them as
    # vanilla_overwrite (named expansion mercs) is right because the vehicle
    # use is currently inactive in non-UB and overwriting destroys a named
    # expansion merc.
    expansion_merc_165_168 = [
        (165, "GASTON",   "1.13 expansion M.E.R.C. contractor (vehicle reservation at Vehicles.h:231 dormant)"),
        (166, "STOGIE",   "1.13 expansion M.E.R.C. contractor (vehicle reservation at Vehicles.h:232 dormant)"),
        (167, "TEX",      "1.13 expansion M.E.R.C. contractor (vehicle reservation at Vehicles.h:233 dormant)"),
        (168, "BIGGENS",  "1.13 expansion M.E.R.C. contractor (vehicle reservation at Vehicles.h:234 dormant)"),
    ]
    for slot, name, role in expansion_merc_165_168:
        named[slot] = EngineNamedSlot(name, role)

    # NPC169 — Vehicles.h:235 reservation. Unused in vanilla data, but the
    # engine names it. Treat as locked since the vehicle slot is reserved.
    named[169] = EngineNamedSlot(
        "NPC169",
        "Vehicle-reserved per Vehicles.h:235 for future expansion. Unused as merc slot in vanilla.",
        is_vehicle_slot=True,
    )

    # 1.13 expansion MERC named (188-199): see soldier profile type.h:222-228.
    expansion_188_199 = [
        (191, "SPECK_PLAYABLE", "Playable Speck variant (1.13 expansion)"),
        (195, "JOHN_MERC",      "1.13 expansion M.E.R.C. contractor"),
        (196, "ELIO",           "1.13 expansion M.E.R.C. contractor"),
        (197, "JUAN",           "1.13 expansion M.E.R.C. contractor"),
        (198, "WAHAN",          "1.13 expansion M.E.R.C. contractor"),
    ]
    for slot, name, role in expansion_188_199:
        named[slot] = EngineNamedSlot(name, role)

    # 1.13 expansion AIM named: 215 (BUNS_CHAOTIC — collides with vanilla
    # Buns via AimBioID=17). Other expansion AIM slots like Gary/Doc/etc.
    # have data in MercProfiles.xml but aren't named in the engine enum.
    named[215] = EngineNamedSlot(
        "BUNS_CHAOTIC",
        "1.13 expansion AIM — AimBioID=17 collides with vanilla Buns (slot 17)",
    )

    # NPC170 = soldier profile type.h sentinel (NPC169 + 84). Vanilla doesn't
    # name 253 but mods deploy real MERCs there via MercAvailability.xml.
    # Leave 253 unnamed in the table — its tier comes from XML presence.

    return named


def engine_named_slots(*, is_ub: bool = False) -> dict[int, EngineNamedSlot]:
    """Return the engine-named-slot table.

    UB builds shift FIRST_RPC from 57 to 60, so MIGUEL lands at slot 60 and
    the rest cascade by +3. UB's RPC range is shorter (60-74 = 15 slots vs.
    non-UB 57-74 = 18 slots); any names that would shift past FIRST_NPC=75
    are dropped because they don't exist in the UB enum.
    """
    table = _build_engine_named_slots_non_ub()
    if not is_ub:
        return table
    shift = FIRST_RPC_UB - FIRST_RPC_NON_UB
    rpc_entries = [
        (slot, table.pop(slot))
        for slot in list(table.keys())
        if FIRST_RPC_NON_UB <= slot < FIRST_NPC
    ]
    for slot, info in rpc_entries:
        new_slot = slot + shift
        if new_slot >= FIRST_NPC:
            # Name doesn't exist in UB — its slot landed in NPC territory
            # where a different enum constant takes precedence.
            continue
        table[new_slot] = info
    return table


# ──────────────────────────────────────────────────────────────────────────
#  Pydantic response models
# ──────────────────────────────────────────────────────────────────────────


class AimRowInfo(BaseModel):
    """Live AIMAvailability.xml data for one slot."""
    present: bool
    ProfilId: Optional[int] = None
    AimBioID: Optional[int] = None
    description: Optional[str] = None


class MercRowInfo(BaseModel):
    """Live MercAvailability.xml data for one slot."""
    present: bool
    ProfilId: Optional[int] = None
    MercBioID: Optional[int] = None
    Name: Optional[str] = None
    uiIndex: Optional[int] = None


SlotTier = str  # "safe" | "vanilla_overwrite" | "quest_bound" | "locked"
SlotCategory = str  # "aim" | "merc" | "rpc" | "npc" | "locked" | "unassigned"


class SlotInfo(BaseModel):
    """Everything the frontend needs to render one cell in the picker."""
    slot: int
    tier: SlotTier
    category: SlotCategory
    is_empty: bool
    engine_name: Optional[str] = None
    engine_role: Optional[str] = None
    profile_name: Optional[str] = None
    profile_nickname: Optional[str] = None
    profile_type: Optional[int] = None
    aim_row: AimRowInfo
    merc_row: MercRowInfo


class EngineFlagsResponse(BaseModel):
    is_ub: bool
    reads_profiles_from_xml: bool


class SlotPickerResponse(BaseModel):
    slots: list[SlotInfo]
    engine_flags: EngineFlagsResponse
    aim_row_count: int
    merc_row_count: int
    laptop_aim_display_cap: int = MAX_LAPTOP_AIM_DISPLAY


# ──────────────────────────────────────────────────────────────────────────
#  Derivation
# ──────────────────────────────────────────────────────────────────────────


def _derive_tier(
    slot: int,
    named: Optional[EngineNamedSlot],
    has_profile_data: bool,
    *,
    flags: EngineFlags,
) -> SlotTier:
    if named is not None:
        if named.is_vehicle_slot or named.is_main_story_locked:
            return "locked"
        if named.is_quest_bound:
            return "quest_bound"
        # Named but not flagged → expansion-named (e.g. GASTON, BUNS_CHAOTIC).
        # Overwriting destroys a named expansion merc; bump to vanilla_overwrite.
        return "vanilla_overwrite"
    # Unnamed slot. Vanilla data conventions:
    if slot in _VANILLA_AIM_SLOT_RANGE or slot in _VANILLA_MERC_SLOT_RANGE:
        if has_profile_data:
            return "vanilla_overwrite"
        # Vanilla range but empty (rare on stock installs; common on stripped
        # mod installs) → still treat as vanilla_overwrite because writing
        # here re-occupies a slot the laptop's site indexing knows about.
        return "vanilla_overwrite"
    # 51-56: gap. When fReadProfileDataFromXML is FALSE (legacy code path)
    # the engine mis-tags these as IMP fallback. Keep them LOCKED in that
    # case. Modern installs (XML read enabled) treat them as safe.
    if 51 <= slot <= 56 and not flags.reads_profiles_from_xml:
        return "locked"
    return "safe"


def _derive_category(
    slot: int,
    named: Optional[EngineNamedSlot],
    aim_present: bool,
    merc_present: bool,
    *,
    flags: EngineFlags,
) -> SlotCategory:
    if aim_present:
        return "aim"
    if merc_present:
        return "merc"
    first_rpc = FIRST_RPC_UB if flags.is_ub else FIRST_RPC_NON_UB
    if first_rpc <= slot < FIRST_NPC:
        return "rpc"
    if FIRST_NPC <= slot < FIRST_VEHICLE:
        return "npc"
    if named is not None and (named.is_vehicle_slot or named.is_main_story_locked):
        return "locked"
    return "unassigned"


def _aim_row_present(binding) -> bool:
    """AIM rows use -1 as the empty sentinel (Init.cpp:1147)."""
    if binding is None:
        return False
    return binding.ProfilId is not None and binding.ProfilId >= 0


def _merc_row_present(binding) -> bool:
    """MERC rows use 0 as the empty sentinel (mercs.cpp:402-410, 490-498)."""
    if binding is None:
        return False
    return binding.ProfilId is not None and binding.ProfilId != 0 and binding.ProfilId != -1


def build_slot_picker(
    install_root: Path,
    *,
    vfs_config_path: Optional[Path] = None,
    flags: Optional[EngineFlags] = None,
    ctx: Optional[InstallContext] = None,
) -> SlotPickerResponse:
    """Read MercProfiles + AIM/MercAvailability and derive per-slot info.

    ``flags`` and ``ctx`` can be passed in for tests; production callers pass
    just ``install_root`` and the function infers the rest.
    """
    install_root = Path(install_root)
    if ctx is None:
        ctx = make_install_context(install_root)
    if flags is None:
        flags = detect_engine_flags(install_root, vfs_config_path)

    profiles_path = ctx.profiles_xml_path()
    aim_path = ctx.aim_xml_path()
    merc_path = ctx.merc_xml_path()

    profiles = read_all_slots(profiles_path) if profiles_path.is_file() else {}
    aim_map = aim_availability.read_all(aim_path) if aim_path.is_file() else {}
    merc_map = merc_availability.read_all(merc_path) if merc_path is not None else {}

    named_table = engine_named_slots(is_ub=flags.is_ub)

    slots: list[SlotInfo] = []
    aim_row_count = 0
    merc_row_count = 0
    for slot in range(NUM_PROFILES):
        named = named_table.get(slot)
        prof = profiles.get(slot)
        zname = (prof.get("zName") if prof else "") or ""
        znick = (prof.get("zNickname") if prof else "") or ""
        has_profile_data = bool(zname.strip() or znick.strip())
        try:
            ptype = int((prof.get("Type") or "").strip()) if prof else None
        except (ValueError, AttributeError):
            ptype = None

        aim_binding = aim_map.get(slot)
        aim_present = _aim_row_present(aim_binding)
        if aim_present:
            aim_row_count += 1
        merc_binding = merc_map.get(slot)
        merc_present = _merc_row_present(merc_binding)
        if merc_present:
            merc_row_count += 1

        tier = _derive_tier(slot, named, has_profile_data, flags=flags)
        category = _derive_category(slot, named, aim_present, merc_present, flags=flags)

        slots.append(SlotInfo(
            slot=slot,
            tier=tier,
            category=category,
            is_empty=not has_profile_data,
            engine_name=named.name if named else None,
            engine_role=named.role if named else None,
            profile_name=zname or None,
            profile_nickname=znick or None,
            profile_type=ptype,
            aim_row=AimRowInfo(
                present=aim_present,
                ProfilId=aim_binding.ProfilId if aim_binding else None,
                AimBioID=aim_binding.AimBioID if aim_binding else None,
                description=aim_binding.description if aim_binding else None,
            ),
            merc_row=MercRowInfo(
                present=merc_present,
                ProfilId=merc_binding.ProfilId if merc_binding else None,
                MercBioID=merc_binding.MercBioID if merc_binding else None,
                Name=merc_binding.Name if merc_binding else None,
                uiIndex=merc_binding.uiIndex if merc_binding else None,
            ),
        ))

    return SlotPickerResponse(
        slots=slots,
        engine_flags=EngineFlagsResponse(
            is_ub=flags.is_ub,
            reads_profiles_from_xml=flags.reads_profiles_from_xml,
        ),
        aim_row_count=aim_row_count,
        merc_row_count=merc_row_count,
    )
