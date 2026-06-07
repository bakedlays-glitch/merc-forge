"""Canonical Backgrounds.xml field schema — engine-derived single source of truth.

Every numeric/flag column the engine recognizes for a `<BACKGROUND>`, baked
verbatim from the loader `Visual Studio Root/Tactical/XML_Background.cpp`
(per-field `min(MAX, max(MIN, ...))` clamps) + `Tactical/Interface.h`
(struct BACKGROUND_VALUES, NUM_BACKGROUND). This module is the authority the
write path validates against and the GET endpoint exposes so the frontend
renders the editor form without hard-coding the field list twice.

Engine truth this encodes (see memory `reference_ja2_backgrounds_engine`):

- `zBackground[NUM_BACKGROUND]`, NUM_BACKGROUND = 500. `uiIndex` is a direct
  array index; the loader does `if (uiIndex < 500)` so any id >= 500 is
  SILENTLY DROPPED. Valid editable ids are 1..499 (0 is the
  "Background name (128 letters)" template; usBackground=0 = no background).
- ~68 numeric `value[]` fields. The AP/stat/travel/resistance block casts
  through `(INT8)atol` then clamps; the rest cast through `(INT16)atol` then
  clamp. We always WRITE a value already clamped to [min, max], so the engine's
  INT8 cast never sees an out-of-range value (no wrap) and its re-clamp is a
  no-op. `cast` is recorded for documentation only.
- ~12 boolean FLAG fields stored in a `uiFlags` bitmask via
  `uiFlags |= atol(x) ? FLAG : 0` — any non-zero sets the bit, so they are 0/1.
- `dislikebackground` is the ONLY unclamped numeric field: a signed pairing
  token (A dislikes B iff A.dislikebg != 0 and A.dislikebg == -B.dislikebg).
- `<drugtypes>`/`<drugitems>` are nested INT16 lists (valueVectors). They are
  NOT in this flat schema; the writer preserves them verbatim.

String caps (CHAR16 arrays, so the limit is UTF-16 code units, not Python len):
  szName <= 127, szShortName <= 19, szDescription <= 255.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── Engine constants ────────────────────────────────────────────────────────
NUM_BACKGROUND = 500          # zBackground[] size — Interface.h
MAX_INDEX = NUM_BACKGROUND - 1  # 499; ids >= 500 are silently dropped on load
TEMPLATE_INDEX = 0            # the "Background name (128 letters)" template row

# CHAR16[N] string caps (usable code units = N - 1, last cell forced to NUL)
NAME_MAX = 127        # szName     CHAR16[128]
SHORT_NAME_MAX = 19   # szShortName CHAR16[20]
DESCRIPTION_MAX = 255  # szDescription CHAR16[256]

# INT16 storage bound (for `dislikebackground`, the one unclamped field)
INT16_MIN = -32768
INT16_MAX = 32767


@dataclass(frozen=True)
class FieldSpec:
    key: str                # the XML tag, e.g. "ap_forest"
    label: str              # human label for the editor
    group: str              # form section
    kind: str               # "int" | "flag" | "enum"
    min: int                # inclusive engine clamp floor
    max: int                # inclusive engine clamp ceiling
    cast: str = "INT16"     # "INT8" | "INT16" — documentation of the load cast
    options: Optional[tuple[tuple[int, str], ...]] = None  # for kind == "enum"
    note: Optional[str] = None  # special semantics surfaced in the UI


# Group labels (display order follows first appearance below)
G_AP_TERRAIN = "Action points — terrain"
G_AP_OTHER = "Action points — activities"
G_STATS = "Stat modifiers"
G_TRAVEL = "Travel time"
G_RESIST = "Resistances"
G_COMBAT = "Combat & perception"
G_APPROACH = "Recruitment approach"
G_ECON = "Economy & survival"
G_MED = "Medical & disease"
G_ASSIGN = "Assignment effectiveness"
G_SOCIAL = "Social"
G_FLAGS = "Flags (on/off)"


# Field table — order mirrors the engine/template column order. INT8-cast block
# first (AP / stats / travel / resistances), then the INT16 "various" block,
# then the flag block. Clamps copied 1:1 from XML_Background.cpp.
FIELD_SPECS: tuple[FieldSpec, ...] = (
    # ── AP: terrain (INT8, ±XML_BACKGROUND_AP_MAX=8) ────────────────────────
    FieldSpec("ap_polar", "Polar", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_desert", "Desert", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_swamp", "Swamp", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_urban", "Urban", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_forest", "Forest", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_plains", "Plains", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_river", "River", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_coastal", "Coastal", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_tropical", "Tropical", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_mountain", "Mountain", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    FieldSpec("ap_height", "Height (climbing)", G_AP_TERRAIN, "int", -8, 8, "INT8"),
    # ── AP: activities ──────────────────────────────────────────────────────
    FieldSpec("ap_swimming", "Swimming", G_AP_OTHER, "int", -40, 40, "INT8"),
    FieldSpec("ap_fortify", "Fortify", G_AP_OTHER, "int", -40, 40, "INT8"),
    FieldSpec("ap_artillery", "Artillery", G_AP_OTHER, "int", -40, 40, "INT8"),
    FieldSpec("ap_inventory", "Inventory", G_AP_OTHER, "int", -40, 40, "INT8"),
    FieldSpec("ap_airdrop", "Airdrop", G_AP_OTHER, "int", -40, 40, "INT8"),
    FieldSpec("ap_assault", "Assault", G_AP_OTHER, "int", -10, 10, "INT8"),
    # ── Stats (INT8, ±XML_BACKGROUND_STAT_MAX=10) ───────────────────────────
    FieldSpec("agility", "Agility", G_STATS, "int", -10, 10, "INT8"),
    FieldSpec("strength", "Strength", G_STATS, "int", -10, 10, "INT8"),
    FieldSpec("dexterity", "Dexterity", G_STATS, "int", -10, 10, "INT8"),
    FieldSpec("mechanical", "Mechanical", G_STATS, "int", -10, 10, "INT8"),
    FieldSpec("medical", "Medical", G_STATS, "int", -10, 10, "INT8"),
    FieldSpec("wisdom", "Wisdom", G_STATS, "int", -10, 10, "INT8"),
    FieldSpec("explosives", "Explosives", G_STATS, "int", -10, 10, "INT8"),
    FieldSpec("leadership", "Leadership", G_STATS, "int", -10, 10, "INT8"),
    FieldSpec("marksmanship", "Marksmanship", G_STATS, "int", -10, 10, "INT8"),
    # ── Travel (INT8, ±XML_BACKGROUND_TRAVEL_MAX=20) ────────────────────────
    FieldSpec("travel_foot", "On foot", G_TRAVEL, "int", -20, 20, "INT8"),
    FieldSpec("travel_car", "By car", G_TRAVEL, "int", -20, 20, "INT8"),
    FieldSpec("travel_air", "By air", G_TRAVEL, "int", -20, 20, "INT8"),
    FieldSpec("travel_boat", "By boat", G_TRAVEL, "int", -20, 20, "INT8"),
    # ── Resistances (INT8, ±XML_BACKGROUND_RESI_MAX=20; physical ±10) ───────
    FieldSpec("resistance_fear", "Fear", G_RESIST, "int", -20, 20, "INT8"),
    FieldSpec("resistance_suppression", "Suppression", G_RESIST, "int", -20, 20, "INT8"),
    FieldSpec("resistance_physical", "Physical", G_RESIST, "int", -10, 10, "INT8"),
    FieldSpec("resistance_alcohol", "Alcohol", G_RESIST, "int", -20, 20, "INT8"),
    FieldSpec("resistance_disease", "Disease", G_RESIST, "int", -20, 20, "INT8"),
    # ── Various / perc (INT16) ──────────────────────────────────────────────
    FieldSpec("meleedamage", "Melee damage %", G_COMBAT, "int", -10, 10),
    FieldSpec("cth_blades", "CtH with blades %", G_COMBAT, "int", -10, 10),
    FieldSpec("cth_vs_creatures", "CtH vs creatures %", G_COMBAT, "int", -10, 10),
    FieldSpec("increased_maxcth", "Max CtH %", G_COMBAT, "int", -5, 5),
    FieldSpec("camo", "Camouflage %", G_COMBAT, "int", -20, 10),
    FieldSpec("stealth", "Stealth %", G_COMBAT, "int", -20, 10),
    FieldSpec("hearing_night", "Hearing (night)", G_COMBAT, "int", -5, 2),
    FieldSpec("hearing_day", "Hearing (day)", G_COMBAT, "int", -5, 2),
    FieldSpec("spotter", "Spotter %", G_COMBAT, "int", -30, 30),
    FieldSpec("croucheddefense", "Crouched defense %", G_COMBAT, "int", -30, 30),
    FieldSpec("snake_defense", "Snake/creature defense %", G_COMBAT, "int", -100, 100),
    FieldSpec("breachingcharge", "Breaching charge %", G_COMBAT, "int", -100, 100),
    FieldSpec("SAM_cth", "SAM site CtH %", G_COMBAT, "int", -50, 100),
    FieldSpec("disarm_trap", "Disarm trap %", G_COMBAT, "int", -50, 50),
    FieldSpec("ambush_radius", "Ambush radius", G_COMBAT, "int", 0, 50),
    FieldSpec("tracker_ability", "Tracker ability", G_COMBAT, "int", 0, 40),
    # ── Approach (recruitment) ──────────────────────────────────────────────
    FieldSpec("approach_friendly", "Friendly", G_APPROACH, "int", -50, 20),
    FieldSpec("approach_direct", "Direct", G_APPROACH, "int", -50, 20),
    FieldSpec("approach_threaten", "Threaten", G_APPROACH, "int", -50, 20),
    FieldSpec("approach_recruit", "Recruit", G_APPROACH, "int", -50, 20),
    # ── Economy & survival ──────────────────────────────────────────────────
    FieldSpec("betterprices_guns", "Better prices: guns", G_ECON, "int", -10, 10),
    FieldSpec("betterprices", "Better prices: all", G_ECON, "int", -10, 10),
    FieldSpec("capitulation", "Capitulation resist %", G_ECON, "int", -50, 100),
    FieldSpec("food", "Food need %", G_ECON, "int", -50, 100),
    FieldSpec("water", "Water need %", G_ECON, "int", -50, 100),
    FieldSpec(
        "sleep", "Sleep need", G_ECON, "enum", -1, 1,
        options=((-1, "Needs less sleep (-1)"), (0, "Normal (0)"), (1, "Needs more sleep (+1)")),
    ),
    FieldSpec("drink_energyregen", "Drink energy regen %", G_ECON, "int", -80, 300),
    FieldSpec("carrystrength", "Carry strength %", G_ECON, "int", -20, 20),
    FieldSpec("speed_run", "Running speed %", G_ECON, "int", -50, 50),
    FieldSpec("speed_bandaging", "Bandaging speed %", G_ECON, "int", -50, 50),
    FieldSpec("insurance", "Insurance cost %", G_ECON, "int", -50, 200),
    FieldSpec("interrogation", "Interrogation %", G_ECON, "int", -50, 300),
    FieldSpec("prisonguard", "Prison guard %", G_ECON, "int", -50, 300),
    # ── Medical & disease ───────────────────────────────────────────────────
    FieldSpec("disease_diagnose", "Disease diagnose %", G_MED, "int", -50, 50),
    FieldSpec("disease_treatment", "Disease treatment %", G_MED, "int", -50, 50),
    # ── Assignment effectiveness ────────────────────────────────────────────
    FieldSpec("fortify_assignment", "Fortification", G_ASSIGN, "int", -50, 200),
    FieldSpec("hackerskill", "Hacker skill (0 = can't hack)", G_ASSIGN, "int", 0, 100),
    FieldSpec("burial_assignment", "Burial", G_ASSIGN, "int", -50, 1000),
    FieldSpec("administration_assignment", "Administration", G_ASSIGN, "int", -50, 1000),
    FieldSpec("exploration_assignment", "Exploration", G_ASSIGN, "int", -100, 1000),
    # ── Social ──────────────────────────────────────────────────────────────
    FieldSpec(
        "dislikebackground", "Dislike pairing token", G_SOCIAL, "int",
        INT16_MIN, INT16_MAX,
        note="Signed pairing key (not a magnitude). A background dislikes another"
             " only if this value is the exact negative of the other's"
             " (e.g. +5 dislikes -5). 0 = no pairing. Not clamped by the engine.",
    ),
    # ── Smoker is a 0/1/2 enum, not a flag (Interface.h BG_SMOKERTYPE) ───────
    FieldSpec(
        "smoker", "Smoking", G_SOCIAL, "enum", 0, 2,
        options=((0, "Doesn't care (0)"), (1, "Smoker — dislikes non-smokers (1)"),
                 (2, "Anti-smoker — dislikes smokers (2)")),
    ),
    # ── Flags (uiFlags bitmask; any non-zero = on, written as 0/1) ──────────
    FieldSpec("druguse", "Uses drugs", G_FLAGS, "flag", 0, 1),
    FieldSpec("xenophobic", "Xenophobic", G_FLAGS, "flag", 0, 1),
    FieldSpec("corruptionspread", "Spreads corruption", G_FLAGS, "flag", 0, 1),
    FieldSpec("level_underground", "Levels faster underground", G_FLAGS, "flag", 0, 1),
    FieldSpec("scrounging", "Scrounging", G_FLAGS, "flag", 0, 1),
    FieldSpec("traplevel", "Trap detection level", G_FLAGS, "flag", 0, 1),
    FieldSpec("no_male", "Not available to males", G_FLAGS, "flag", 0, 1),
    FieldSpec("no_female", "Not available to females", G_FLAGS, "flag", 0, 1),
    FieldSpec("loyalitylossondeath", "Global loyalty loss on death", G_FLAGS, "flag", 0, 1),
    FieldSpec("animal_friend", "Animal friend", G_FLAGS, "flag", 0, 1),
    FieldSpec("civgroup_loyal", "Civ-group loyal", G_FLAGS, "flag", 0, 1),
    FieldSpec("alt_impcreation", "Alternate IMP creation", G_FLAGS, "flag", 0, 1),
)

# Fast lookups
_SPEC_BY_KEY: dict[str, FieldSpec] = {s.key: s for s in FIELD_SPECS}
FLAG_FIELDS: frozenset[str] = frozenset(s.key for s in FIELD_SPECS if s.kind == "flag")
# Every flat field the editor owns (writer treats these as authoritative; anything
# else in a <BACKGROUND> — unknown mod columns, nested drug lists — is preserved).
OWNED_FIELDS: frozenset[str] = frozenset(s.key for s in FIELD_SPECS)
# Meta tags that are not modifier fields.
META_TAGS: frozenset[str] = frozenset({"uiIndex", "szName", "szShortName", "szDescription"})
# Nested container tags the writer must never treat as flat ints.
NESTED_TAGS: frozenset[str] = frozenset({"drugtypes", "drugitems"})

# Stable group display order.
GROUP_ORDER: tuple[str, ...] = (
    G_AP_TERRAIN, G_AP_OTHER, G_STATS, G_TRAVEL, G_RESIST, G_COMBAT,
    G_APPROACH, G_ECON, G_MED, G_ASSIGN, G_SOCIAL, G_FLAGS,
)


def get_spec(key: str) -> Optional[FieldSpec]:
    return _SPEC_BY_KEY.get(key)


def is_owned(key: str) -> bool:
    return key in _SPEC_BY_KEY


def clamp_value(key: str, value: int) -> tuple[int, bool]:
    """Clamp `value` to the field's engine [min, max]. Returns (clamped, changed).

    Unknown keys pass through unchanged (the caller validates membership first).
    """
    spec = _SPEC_BY_KEY.get(key)
    if spec is None:
        return value, False
    if value < spec.min:
        return spec.min, True
    if value > spec.max:
        return spec.max, True
    return value, False


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units — the unit the engine's CHAR16[] caps measure.

    A non-BMP character (e.g. an emoji) is one Python char but two UTF-16 code
    units, so `len(text)` undercounts against the engine's cap. The engine does
    `MultiByteToWideChar` then forces `[N-1] = '\\0'`, truncating in code units.
    """
    return len(text.encode("utf-16-le")) // 2


def schema_payload() -> list[dict]:
    """Serializable schema for the GET endpoint / frontend form."""
    out: list[dict] = []
    for s in FIELD_SPECS:
        entry: dict = {
            "key": s.key, "label": s.label, "group": s.group,
            "kind": s.kind, "min": s.min, "max": s.max,
        }
        if s.options is not None:
            entry["options"] = [{"value": v, "label": l} for v, l in s.options]
        if s.note is not None:
            entry["note"] = s.note
        out.append(entry)
    return out
