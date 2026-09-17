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

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional, Union

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
    # `help` is not stored on the spec; see `_FIELD_DOCS` (same split as items_schema).


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

# Glossary ids the frontend FieldHelp can open. Keep in sync with
# frontend/src/lib/glossary.ts. Shared nouns only — one-off field names
# (Scrounging, Fortify, …) stay as their field tip, not a second card.
HELP_TERMS: frozenset[str] = frozenset({
    "AP", "CtH", "IMP", "percent", "flat", "clamp",
    "Strength", "Agility", "Dexterity", "Wisdom", "Leadership",
    "Marksmanship", "Mechanical", "Medical", "Explosives",
    "Breath", "Stealth", "Camo", "Survivalist", "SAM", "NPC",
    "Experience",
})


def _link_term(term_id: str) -> tuple[str, Callable[[re.Match[str]], str]]:
    """Case-insensitive word → [[id]] or [[id|originalCase]]."""

    def repl(m: re.Match[str]) -> str:
        word = m.group(0)
        return f"[[{term_id}]]" if word == term_id else f"[[{term_id}|{word}]]"

    return (rf"(?i)\b{re.escape(term_id)}\b", repl)


WikiRepl = Union[str, Callable[[re.Match[str]], str]]

# Longer phrases first; re-split after each sub so [[AP|APs]] is not
# then eaten by \\bAP\\b. Stat names before Percent/AP.
_WIKI_PHRASES: tuple[tuple[str, WikiRepl], ...] = (
    (r"not a flat add", "not a [[flat|flat add]]"),
    (r"not a percent", "not a [[percent]]"),
    (r"saves as", "[[clamp|saves as]]"),
    (r"chance-to-hit", "[[CtH|chance-to-hit]]"),
    (r"(?i)\bcamouflage\b", lambda m: f"[[Camo|{m.group(0)}]]"),
    (r"(?i)\bexperience level\b", lambda m: f"[[Experience|{m.group(0)}]]"),
    _link_term("Marksmanship"),
    _link_term("Mechanical"),
    _link_term("Explosives"),
    _link_term("Leadership"),
    _link_term("Survivalist"),
    _link_term("Dexterity"),
    _link_term("Agility"),
    _link_term("Strength"),
    _link_term("Wisdom"),
    _link_term("Medical"),
    _link_term("Stealth"),
    _link_term("Breath"),
    _link_term("Experience"),
    _link_term("Camo"),
    _link_term("NPC"),
    _link_term("SAM"),
    (r"(?i)\bpercent\b", lambda m: f"[[percent|{m.group(0)}]]"),
    (r"\bFlat\b", "[[flat|Flat]]"),
    (r"\bAPs\b", "[[AP|APs]]"),
    (r"\bAP\b", "[[AP]]"),
    (r"\bCtH\b", "[[CtH]]"),
    (r"\bIMPs\b", "[[IMP|IMPs]]"),
    (r"\bIMP\b", "[[IMP]]"),
)


def wikify_help(text: str) -> str:
    """Wrap glossary terms as [[id]] / [[id|label]] for FieldHelp cross-links.

    Re-splits after each substitution so a newly-inserted [[AP|APs]] is not
    then matched by the later ``\\bAP\\b`` rule.
    """
    for pattern, repl in _WIKI_PHRASES:
        chunks = re.split(r"(\[\[[^\]]+\]\])", text)
        out: list[str] = []
        for chunk in chunks:
            if chunk.startswith("[["):
                out.append(chunk)
            else:
                out.append(re.sub(pattern, repl, chunk))
        text = "".join(out)
    return text


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

# Editor tooltips. Sourced from GetAPBonus / GetBackgroundValue call sites.
# Each numeric tip states: percent vs flat, the formula, a worked example,
# and the clamp when a round number like 10 is out of range.
_FIELD_DOCS: dict[str, str] = {
    # ── AP: terrain — GetAPBonus() is APs * (100+N)/100, NOT a flat add ──
    "ap_polar": "Would be % of this turn's APs in polar terrain, but BG_POLAR is not used in-game yet. Range −8..8.",
    "ap_desert": "Percent of this turn's APs in desert/sand (not a flat add). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_swamp": "Percent of this turn's APs in swamp (not a flat add). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_urban": "Percent of this turn's APs in towns/airports/hospital sites (not a flat add). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_forest": "Percent of this turn's APs in dense/forest (not a flat add). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_plains": "Percent of this turn's APs in plains/farmland (not a flat add). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_river": "Percent of this turn's APs in river sectors (not a flat add). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_coastal": "Percent of this turn's APs in coastal sectors (also stacks with Tropical on tropics SAM sites). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_tropical": "Percent of this turn's APs in tropics (not a flat add). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_mountain": "Percent of this turn's APs in hills/mountains (not a flat add). APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    "ap_height": "Percent of this turn's APs while on a roof/upper level; stacks with the sector-terrain value. APs × (100+N)÷100. Range −8..8; a 10 saves as 8. Example: 100 AP at +8 → 108 AP.",
    # ── AP: activities ──
    "ap_swimming": "Percent of water-move AP *cost* (positive = more expensive). Cost × (100+N)÷100. Range −40..40. Example: a 20 AP swim at +10 costs 22 AP.",
    "ap_fortify": "Percent of the AP *cost* to build fortifications (positive = more expensive). Cost × (100+N)÷100. Range −40..40. Example: a 20 AP fortify at +10 costs 22 AP.",
    "ap_artillery": "Percent of mortar/artillery AP *cost* (positive = more expensive). Cost × (100+N)÷100. Range −40..40. Example: a 20 AP shot at +10 costs 22 AP.",
    "ap_inventory": "Percent of inventory-move AP *cost* (positive = more expensive). Cost × (100+N)÷100. Range −40..40. Example: a 10 AP rearrange at +10 costs 11 AP.",
    "ap_airdrop": "Percent of this turn's APs on the airdrop turn only (not a flat add). APs × (100+N)÷100. Range −40..40. Example: 100 AP at +10 → 110 AP that turn.",
    "ap_assault": "Percent of this turn's APs while the assault-bonus flag is set (not a flat add). APs × (100+N)÷100. Range −10..10. Example: 100 AP at +10 → 110 AP.",
    # ── Stats — Effective*() * (100+N)/100 in SkillCheck.cpp ──
    "agility": "Speed and footwork: more APs each turn (biggest AP stat after experience level), cheaper knife-ready times, better melee. Overweight cuts Agility first. This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Agility 80 at +10 → 88.",
    "strength": "Muscle: carry more before you slow down, hit harder in melee, throw farther, smash doors, climb, resist sleep darts. Wounds cut it. This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Strength 80 at +10 → 88.",
    "dexterity": "Dexterity is hands — coordination. More APs, better throws/knives/launchers/melee, less to-hit loss when tired, faster first aid / doctor / repair / lockpick / bombs. Click Dexterity for the full list. This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Dexterity 80 at +10 → 88.",
    "mechanical": "Tools: lockpicking, electronic locks, unjamming guns, attaching kits, faster repair and gun-cleaning. 0 Mechanical fails those checks. This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Mechanical 80 at +10 → 88.",
    "medical": "Medicine: faster first aid in combat and faster doctoring on the map (also uses Dexterity, Wisdom, and level). This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Medical 80 at +10 → 88.",
    "wisdom": "Judgment: better skill checks (lockpick etc.), faster doctoring, better interrogation/recruiting, steadier aim. Drunk cuts it. This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Wisdom 80 at +10 → 88.",
    "explosives": "Bombs and traps: planting charges, attaching detonators, related explosive checks (Dexterity also helps those rolls). This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Explosives 80 at +10 → 88.",
    "leadership": "Command: recruiting, interrogation, militia/admin work. Feeling-good drunk raises it 20%. This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Leadership 80 at +10 → 88.",
    "marksmanship": "Shooting: gun chance-to-hit and cheaper aiming AP. Launchers and throws mix in Dexterity. This field is a percent of that stat: Stat × (100+N)÷100. Range −10..10. Example: Marksmanship 80 at +10 → 88.",
    # ── Travel — traverseTime * (100-N)/100; positive = faster ──
    "travel_foot": "Percent faster strategic travel on foot. Time × (100−N)÷100. Squad uses the SLOWEST merc. Range −20..20. Example: a 100-min hike at +10 → 90 min.",
    "travel_car": "Percent faster strategic travel by car/truck. Time × (100−N)÷100. Squad uses the BEST merc. Range −20..20. Example: a 100-min drive at +10 → 90 min.",
    "travel_air": "Percent faster helicopter travel. Time × (100−N)÷100. Squad uses the BEST merc. Range −20..20. Example: a 100-min flight at +10 → 90 min.",
    "travel_boat": "Loaded into BG_TRAVEL_BOAT but not applied to travel time in-game. Range −20..20.",
    # ── Resistances ──
    "resistance_fear": "Flat points added to fear resistance (−100..100 clamp). Range −20..20. Example: +10 = 10 more points of panic resistance, not 10% of your current resist.",
    "resistance_suppression": "Flat points added to suppression resistance (−100..100 clamp). Range −20..20. Example: +10 = 10 more points, not 10% of your current resist.",
    "resistance_physical": "Flat percentage-points of physical damage resistance (added to other resists, then capped at 95%). Range −10..10. Example: +10 → take 10% less damage from that layer, not 10% of your current resist.",
    "resistance_alcohol": "Percent less alcohol effect. Effect × (100−N)÷100. Range −20..20. Example: a drink that would apply 50 at +10 → 45.",
    "resistance_disease": "Flat percentage-points of disease resistance (−100..100 clamp). Range −20..20. Example: +10 = 10 more points, not 10% of your current resist.",
    # ── Combat & perception ──
    "meleedamage": "Percent of melee impact. Damage × (100+N)÷100. Range −10..10. Example: 50 melee damage at +10 → 55.",
    "cth_blades": "Percent of blade chance-to-hit. CtH × (100+N)÷100. Range −10..10. Example: 70 CtH at +10 → 77.",
    "cth_vs_creatures": "Flat chance-to-hit points vs creatures (added to the roll, not a percent). Range −10..10. Example: 70 CtH at +10 → 80.",
    "increased_maxcth": "Flat points added to the engine's maximum CtH cap. Range −5..5. Example: a 90 cap at +5 → 95. A 10 saves as 5.",
    "camo": "Flat percentage-points added to camouflage effectiveness. Range −20..10. Example: +10 = 10 more camo points (harder to spot).",
    "stealth": "Flat percentage-points added to stealth (then capped at 100). Range −20..10. Example: 40 stealth at +10 → 50.",
    "hearing_night": "Flat hearing-range points at night (same units as hearing-aid bonuses). Range −5..2. Example: +2 = hear 2 steps farther after dark. A 10 saves as 2.",
    "hearing_day": "Flat hearing-range points during the day. Range −5..2. Example: +2 = hear 2 steps farther in daylight. A 10 saves as 2.",
    "spotter": "Flat points added to spotter effectiveness when calling shots. Range −30..30. Example: +10 = 10 more spotter points.",
    "croucheddefense": "Flat points *added to the enemy's* chance-to-hit when this merc is crouched in cover facing the shot. Negative = harder to hit. Range −30..30. Stock backgrounds use about −4 to −9. Example: enemy 70 CtH vs −10 → 60 CtH.",
    "snake_defense": "Flat points added to snake/creature defense. Range −100..100. Example: +10 = 10 more defense points.",
    "breachingcharge": "Flat points added to the planting-bomb skill check. Range −100..100. Example: +10 = 10 more points on the roll.",
    "SAM_cth": "Percent of SAM-site chance-to-hit. CtH × (100+N)÷100. Range −50..100. Example: 50 SAM CtH at +10 → 55.",
    "disarm_trap": "Flat points added to the disarm-trap skill check. Range −50..50. Example: +10 = 10 more points on the roll.",
    "ambush_radius": "Flat tiles added to ambush detection (on top of 10× experience). Range 0..50. Example: exp 4 with +10 → radius 50.",
    "tracker_ability": "Flat points added to tracker skill, then ÷100 with Survivalist. Range 0..40. Example: +20 Survivalist + 20 here → 0.40 tracking. 0 = no bonus.",
    # ── Approach — value * (100+N)/100 ──
    "approach_friendly": "Percent of the Friendly NPC/recruit approach score. Score × (100+N)÷100. Range −50..20. Example: 100 at +10 → 110. A 10 is in range.",
    "approach_direct": "Percent of the Direct NPC approach score. Score × (100+N)÷100. Range −50..20. Example: 100 at +10 → 110.",
    "approach_threaten": "Percent of the Threaten approach score (also used in interrogation). Score × (100+N)÷100. Range −50..20. Example: 100 at +10 → 110.",
    "approach_recruit": "Percent of recruiting effectiveness. Score × (100+N)÷100. Range −50..20. Example: 100 at +10 → 110.",
    # ── Economy & survival ──
    "betterprices_guns": "Percent better gun prices (buy cheaper / sell higher). Price% = 100±N. Range −10..10. Example: +10 → guns cost 90% to buy and sell for 110%.",
    "betterprices": "Percent better prices on all items. Price% = 100±N. Range −10..10. Example: +10 → pay 90% / sell at 110%.",
    "capitulation": "Percent of player-side team power when enemies consider surrender (not the Strength stat). Score × (100+N)÷100. Range −50..100. Example: 100 at +10 → 110 (harder for them to force a capitulation).",
    "food": "Percent of food *consumption* (positive = hungrier). Drain × (100+N)÷100. Range −50..100. Example: a 10 food drain at +10 → 11. +10 means they eat 10% more, not +10 food.",
    "water": "Percent of water *consumption* (positive = thirstier). Drain × (100+N)÷100. Range −50..100. Example: a 10 water drain at +10 → 11.",
    "sleep": "Flat hours added to sleep needed per day. Range −1..1. −1 = one hour less, 0 = normal, +1 = one hour more. A 10 saves as 1.",
    "drink_energyregen": "Percent of breath recovered when gaining energy (only on breath *gain*, not spend). Gain × (100+N)÷100. Range −80..300. Example: recover 20 breath at +10 → 22.",
    "carrystrength": "Percent of carry capacity. Starts from effective Strength, then this extra percent. Capacity × (100+N)÷100. Range −20..20. Example: 80 carry-str at +10 → 88. Separate field from the Strength stat modifier.",
    "speed_run": "Percent faster tactical running (reduces run delay). Delay × (100−N)÷100. Range −50..50. Example: a 100 delay at +10 → 90 (runs faster).",
    "speed_bandaging": "Flat points added to bandaging speed. Range −50..50. Example: +10 = 10 more bandaging points (wraps faster).",
    "insurance": "Percent of insurance risk/cost (positive = more expensive). Cost × (100+N)÷100. Range −50..200. Example: a $1000 policy at +10 → $1100.",
    "interrogation": "Percent of interrogation assignment points. Points × (100+N)÷100. Range −50..300. Example: 100 at +10 → 110.",
    "prisonguard": "Percent of prison-guard assignment (negatives are ignored). Points × (100+max(0,N))÷100. Range −50..300. Example: 100 at +10 → 110.",
    # ── Medical ──
    "disease_diagnose": "Percent of disease-diagnosis skill. Skill × (100+N)÷100. Range −50..50. Example: 40 diagnose at +10 → 44.",
    "disease_treatment": "Percent of disease-treatment points. Points × (100+N)÷100. Range −50..50. Example: 40 treatment at +10 → 44.",
    # ── Assignments ──
    "fortify_assignment": "Percent of Fortification-assignment effect. Effect × (100+N)÷100. Range −50..200. Example: 100 at +10 → 110 (more cover per session).",
    "hackerskill": "Flat hacking skill 0–100. 0 = cannot hack; any value above 0 enables hacking. Example: 10 = weak hacker; 100 = max.",
    "burial_assignment": "Percent of Burial-assignment effect. Effect × (100+N)÷100. Range −50..1000. Example: 100 at +10 → 110.",
    "administration_assignment": "Percent of Administration-assignment effect. Effect × (100+N)÷100. Range −50..1000. Example: 100 at +10 → 110.",
    "exploration_assignment": "Percent of Exploration-assignment effect. Effect × (100+N)÷100. Range −100..1000. Example: 100 at +10 → 110.",
    # ── Social ──
    "dislikebackground": "Signed pairing token, not a magnitude and not a percent. A background dislikes another only if this value is the exact negative of the other's (e.g. +5 dislikes −5). 0 = no pairing.",
    "smoker": "Enum, not a percent. 0 = doesn't care. 1 = smoker (will smoke; dislikes non-smokers). 2 = anti-smoker (refuses cigarettes; dislikes smokers).",
    # ── Flags ──
    "druguse": "On/off flag. On: this merc may take drugs on their own (the 'Larry' effect).",
    "xenophobic": "On/off flag. On: arrogant toward mercs who do not share this background (morale hit).",
    "corruptionspread": "On/off flag. On: intended to spread corruption to others. Not used in the current engine trunk.",
    "level_underground": "On/off flag. On: counts as +1 experience level while underground (flat +1 level, not a percent).",
    "scrounging": "On/off flag. On: may pick up valuable items on their own during exploration.",
    "traplevel": "On/off flag. On: trap detection/handling level +1 (flat +1, not a percent).",
    "no_male": "On/off flag. On: this background cannot be selected by male IMPs.",
    "no_female": "On/off flag. On: this background cannot be selected by female IMPs.",
    "loyalitylossondeath": "On/off flag. On: if this character dies, the whole country takes a huge loyalty loss.",
    "animal_friend": "On/off flag. On: refuses to attack animals.",
    "civgroup_loyal": "On/off flag. On: refuses to attack members of the same civilian group.",
    "alt_impcreation": "On/off flag. On: only offered during IMP creation when ALT_IMP_CREATION is TRUE in ja2options.ini.",
}

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
        help_text = _FIELD_DOCS.get(s.key)
        if help_text:
            entry["help"] = wikify_help(help_text)
        out.append(entry)
    return out
