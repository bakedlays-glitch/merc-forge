"""
biome_map.py
============

Canonical tileset-ID -> biome mapping for the MapForge generator corpus.

JA2's engine has NO "urban/desert/forest" terrain enum — terrain type
(FLAT_GROUND, PAVED_ROAD, LOW_WATER ...) is per-tile, and the *look* of a
sector (concrete vs sand vs swamp) is a property of which STIs a TILESET
registers. So "biome" here is a tileset-level label we assign by hand from
the tileset's name (authoritative source: tileset_corpus/tilesets.csv).

The distiller (distill_generator_corpus.py) uses this to stratify each
source install's maps by biome before rolling up subframe distributions.
The shipped corpus JSON carries the resolved biome as a key, so the sidecar
never imports this module — it's dev-side only.

Fine taxonomy (locked with user 2026-05-31):
    urban, desert, tropical, temperate, farm, swamp, cave, cliff, arctic,
    wasteland   (+ "unknown" fallback for unused / unrecognized tilesets)
"""
from __future__ import annotations

# Ordered canonical biome list (drives picker order + coverage reporting).
BIOMES: tuple[str, ...] = (
    "urban",
    "desert",
    "tropical",
    "temperate",
    "farm",
    "swamp",
    "cave",
    "cliff",
    "arctic",
    "wasteland",
)

UNKNOWN = "unknown"

# Explicit per-tileset assignment. Trailing comment is the tilesets.csv name.
# Kept explicit (not keyword-matched) so it's auditable and easy to retune.
TILESET_BIOME: dict[int, str] = {
    0:  "temperate",   # GENERIC 1
    1:  "cave",        # CAVES 1
    2:  "desert",      # DESERT 1
    3:  "temperate",   # LUSH 1 (dirt roads)  — green Arulco grassland
    4:  "tropical",    # TROPICAL 1
    5:  "cliff",       # MOUNTAINS 1
    6:  "temperate",   # COASTAL 1
    7:  "swamp",       # SWAMP 1
    8:  "farm",        # FARM 1
    9:  "temperate",   # OMERTA (green village)
    10: "temperate",   # GENERIC 2 (Dirtroads)
    11: "farm",        # FARM 2 (ruined walls)
    12: "urban",       # PRISON
    13: "urban",       # HOSPITAL (Cambria)
    14: "cave",        # DEMO BASEMENT
    15: "wasteland",   # BURNT TREES
    16: "urban",       # LAWLESS 1 (San Mona-d5)
    17: "urban",       # AIRSTRIP (Drassen-b13)
    18: "urban",       # LAWLESS 2 (burnt-c5)
    19: "wasteland",   # DEAD AIRSTRIP (Drassen-c13)
    20: "cave",        # BASEMENT
    21: "urban",       # LAWLESS 3 (burnt-c6)
    22: "cave",        # PRISON DUNGEON
    23: "urban",       # ACTIVE DRASSEN (d13)
    24: "urban",       # SAM SITES (military installation)
    25: "temperate",   # LUSH2 (different trees)
    26: "urban",       # MILITARY BASE
    27: "urban",       # MILITARY JAIL
    28: "urban",       # MILITARY WAREHOUSE
    29: "urban",       # MILITARY TOWN
    30: "urban",       # OLD SCHOOL
    31: "urban",       # CAMBRIA STRIP
    32: "urban",       # CAMBRIA HOMES
    33: "urban",       # PALACE!
    34: "tropical",    # TROPICAL SAM
    35: "urban",       # GRUMM g2,h2
    36: "urban",       # GRUMM g1,h1
    37: "urban",       # BALIME
    38: "urban",       # BALIME MUSEUM
    39: "desert",      # DESERT SAM
    40: "urban",       # ORTA
    41: "urban",       # ORTA WEAPONS
    42: "swamp",       # SWAMP BARETREES
    43: "urban",       # ESTONI (settlement)
    44: "urban",       # QUEEN'S PRISON
    45: "tropical",    # QUEEN'S TROPICAL
    46: "urban",       # MEDUNA INNER TOWN
    47: "urban",       # QUEEN'S SAM
    48: "urban",       # QUEEN'S AIRPORT
    49: "temperate",   # DEMO TILESET (generic)
    50: "arctic",      # HEAVY SNOW
    51: "arctic",      # MIXED SNOW
    52: "arctic",      # GRASS & SNOW
    53: "urban",       # FALL TOWN
    54: "urban",       # MINING TOWN
    55: "urban",       # POWER PLANT
    56: "cave",        # SEWERS
    57: "cave",        # UNDERGROUND COMPLEX
    58: "cave",        # UPPER COMPLEX
    59: "cave",        # LOWEST LEVEL COMPLEX
    # 60-69 JA25 — defined_slot_count 0, effectively unused
    70: "wasteland",   # The Wasteland custom tileset (not in tilesets.csv)
}


def biome_for_tileset(tileset_id: int) -> str:
    """Return the fine-biome label for a tileset, or 'unknown'."""
    return TILESET_BIOME.get(int(tileset_id), UNKNOWN)
