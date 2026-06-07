"""Identify which mod is installed at a given install root.

Detection signals (in order of confidence):
  1. Folder-name heuristics (highest signal — folder names usually announce the mod)
  2. Content fingerprinting (specific marker files / dirs inside Data-1.13)
  3. Ja2_Options.ini banner text

Detection is informational only — it informs the slot-range presets the UI
highlights, but doesn't gate any operation. The library treats all 1.13
installs uniformly through their actual XML/EDT content.

To support a new mod: add its folder-name keywords + any content fingerprints
to MOD_HEURISTICS. The first match wins after we sort by confidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class ModId(str, Enum):
    VANILLA = "vanilla"
    WASTELAND = "wasteland"
    AIMNAS = "aimnas"
    WILDFIRE = "wildfire"
    URBAN_CHAOS = "urban_chaos"
    ARULCO_REVISITED = "arulco_revisited"
    ARULCO_VACATIONS = "arulco_vacations"
    AI_MODPACK = "ai_modpack"
    VENGEANCE = "vengeance"
    REDUX = "redux"
    SDO = "sdo"
    UNFINISHED_BUSINESS = "unfinished_business"
    UNKNOWN = "unknown"


@dataclass
class ModInfo:
    id: ModId
    display_name: str
    confidence: float  # 0..1
    evidence: list[str]


# Per-mod heuristics. Folder-name matching is case-insensitive substring match
# against the install root's basename. Higher specificity (longer / more unique
# keywords) ranks higher when multiple match.
@dataclass(frozen=True)
class _Heuristic:
    mod: ModId
    display: str
    folder_keywords: tuple[str, ...]   # any-match (case-insensitive)
    requires_all: tuple[str, ...] = () # all-match (case-insensitive) — for disambiguation
    weight: float = 0.8


_HEURISTICS: tuple[_Heuristic, ...] = (
    _Heuristic(
        ModId.WASTELAND, "The Wasteland",
        folder_keywords=("wasteland", "fallout"),
        weight=0.9,
    ),
    _Heuristic(
        ModId.AIMNAS, "AIMNAS",
        folder_keywords=("aimnas",),
        weight=0.95,
    ),
    _Heuristic(
        ModId.WILDFIRE, "Wildfire",
        folder_keywords=("wildfire",),
        weight=0.9,
    ),
    _Heuristic(
        ModId.URBAN_CHAOS, "Urban Chaos",
        folder_keywords=("urban chaos", "urban_chaos"),
        weight=0.95,
    ),
    _Heuristic(
        ModId.ARULCO_REVISITED, "Arulco Revisited",
        folder_keywords=("arulco revisited", "arulco_revisited"),
        weight=0.95,
    ),
    _Heuristic(
        ModId.ARULCO_VACATIONS, "Arulco Vacations",
        folder_keywords=("arulco vacations", "arulco_vacations"),
        weight=0.95,
    ),
    _Heuristic(
        ModId.AI_MODPACK, "AI Modpack",
        folder_keywords=("ai modpack", "ai_modpack"),
        weight=0.95,
    ),
    _Heuristic(
        ModId.VENGEANCE, "Vengeance",
        folder_keywords=("vengeance",),
        weight=0.9,
    ),
    _Heuristic(
        ModId.REDUX, "Redux",
        folder_keywords=("redux",),
        weight=0.85,
    ),
    _Heuristic(
        ModId.SDO, "SDO",
        folder_keywords=(" sdo", "_sdo", "-sdo"),  # word-boundary-ish; avoids matching e.g. "USDO"
        weight=0.85,
    ),
    _Heuristic(
        ModId.UNFINISHED_BUSINESS, "Unfinished Business",
        folder_keywords=("unfinished business", "unfinished_business"),
        weight=0.95,
    ),
)


def _folder_name_match(install_root: Path) -> tuple[Optional[_Heuristic], list[str]]:
    """Return the best-matching heuristic + evidence strings."""
    name = install_root.name.lower()
    best: Optional[_Heuristic] = None
    best_score = 0.0
    for h in _HEURISTICS:
        if not any(kw.lower() in name for kw in h.folder_keywords):
            continue
        # Require all-of constraints
        if h.requires_all and not all(kw.lower() in name for kw in h.requires_all):
            continue
        if h.weight > best_score:
            best = h
            best_score = h.weight
    if best is None:
        return None, []
    matched = [kw for kw in best.folder_keywords if kw.lower() in name]
    return best, [f"Folder name contains '{kw}'" for kw in matched]


def _content_fingerprint(install_root: Path) -> tuple[Optional[ModId], list[str], float]:
    """Inspect Data-1.13 contents for mod-specific marker files."""
    evidence: list[str] = []
    data_root = install_root / "Data-1.13"

    # The Wasteland: tileset 70 ("FALLOUT VAULT") or Ja2Set.dat.xml content
    tileset_70 = data_root / "TileSets" / "Tileset 70"
    if tileset_70.is_dir():
        evidence.append("Data-1.13/TileSets/Tileset 70/ exists (Wasteland-specific)")
        return ModId.WASTELAND, evidence, 0.85

    ja2set = data_root / "Ja2Set.dat.xml"
    if ja2set.is_file():
        try:
            text = ja2set.read_text(encoding="utf-8", errors="replace")
            if "FALLOUT VAULT" in text.upper():
                evidence.append("Ja2Set.dat.xml mentions FALLOUT VAULT")
                return ModId.WASTELAND, evidence, 0.85
        except OSError:
            pass

    return None, [], 0.0


def detect_mod(install_root: Path) -> ModInfo:
    """Fingerprint the install to identify the active mod.

    Combines folder-name heuristics (high signal) with content fingerprinting
    (definitive for The Wasteland). Returns ModInfo with confidence score and
    a list of evidence strings the UI can show in a tooltip.
    """
    install_root = Path(install_root)
    evidence_all: list[str] = []
    candidates: dict[ModId, tuple[float, str]] = {}

    # 1. Folder-name heuristic
    name_match, name_evidence = _folder_name_match(install_root)
    if name_match is not None:
        candidates[name_match.mod] = (name_match.weight, name_match.display)
        evidence_all.extend(name_evidence)

    # 2. Content fingerprint
    content_mod, content_evidence, content_weight = _content_fingerprint(install_root)
    if content_mod is not None:
        prev = candidates.get(content_mod)
        # If content + folder name agree, boost confidence
        if prev:
            candidates[content_mod] = (min(1.0, prev[0] + 0.1), prev[1])
        else:
            display_map = {h.mod: h.display for h in _HEURISTICS}
            candidates[content_mod] = (content_weight, display_map.get(content_mod, content_mod.value.title()))
        evidence_all.extend(content_evidence)

    # 3. Vanilla 1.13 detection — folder name often contains "1.13"
    if not candidates:
        name_lower = install_root.name.lower()
        # Drop the trailing `\b` from the original pattern: underscores
        # are word characters in regex, so `\b1\.?13\b` failed to match
        # "1.13_2026" or "1.13_anything". The leading `\b` keeps us from
        # matching e.g. "21.13" or "v.1.13foo".
        if re.search(r"\b1\.?13|_1\.13|_113", name_lower) or "vanilla" in name_lower:
            return ModInfo(
                id=ModId.VANILLA,
                display_name="Vanilla 1.13",
                confidence=0.5,
                evidence=["Folder name suggests vanilla 1.13"],
            )
        # No match at all — be honest
        return ModInfo(
            id=ModId.UNKNOWN,
            display_name="Unknown / Custom mod",
            confidence=0.0,
            evidence=["No matching mod fingerprint or folder-name keyword"],
        )

    # Pick best candidate
    best_mod = max(candidates, key=lambda m: candidates[m][0])
    best_weight, best_display = candidates[best_mod]
    return ModInfo(
        id=best_mod,
        display_name=best_display,
        confidence=min(best_weight, 1.0),
        evidence=evidence_all,
    )
