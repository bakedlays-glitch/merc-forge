"""Author an RPC's small (talking) face coord override in RPCFacesSmall.xml.

By default the engine takes an RPC's small-face eye/mouth coords from its
MercProfiles row. RPCFacesSmall.xml lets one override them for the small face
specifically (the Dogmeat mouth-tweak case). The engine activates an entry when
its ``FaceIndex`` field == the merc's PROFILE id AND ``uiIndex`` != 65535
(Faces.cpp:610). The field named ``FaceIndex`` therefore holds the profile id;
``uiIndex`` is just a non-65535 activation flag.

This module manages ONLY active entries it owns (FaceIndex == profile,
uiIndex != 65535); inactive vanilla rows (uiIndex 65535) are never touched.
Parses with ElementTree (robust) and re-serializes by hand to preserve the
file's BOM + tab formatting.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

INACTIVE = 65535
_FIELD_ORDER = ("uiIndex", "Name", "FaceIndex", "EyesX", "EyesY", "MouthX", "MouthY")


def _parse(text: str) -> list[dict]:
    """Return each <FACE> as an ordered dict {tag: text}. Empty list if no/blank file."""
    if not text.strip():
        return []
    root = ET.fromstring(text.lstrip("﻿"))
    faces: list[dict] = []
    for face in root.findall("FACE"):
        entry: dict = {}
        for child in face:
            entry[child.tag] = (child.text or "").strip()
        faces.append(entry)
    return faces


def _serialize(entries: list[dict]) -> str:
    lines = ["﻿<SMALLFACE>"]
    for e in entries:
        lines.append("\t<FACE>")
        # Known fields first in canonical order, then any extras (future-proof).
        keys = list(_FIELD_ORDER) + [k for k in e if k not in _FIELD_ORDER]
        for k in keys:
            if k in e:
                lines.append(f"\t\t<{k}>{escape(str(e[k]))}</{k}>")
        lines.append("\t</FACE>")
    lines.append("</SMALLFACE>")
    lines.append("")
    return "\n".join(lines)


def _is_active(e: dict) -> bool:
    try:
        return int(e.get("uiIndex", "65535")) != INACTIVE
    except ValueError:
        return False


def _matches_profile(e: dict, profile: int) -> bool:
    try:
        return _is_active(e) and int(e.get("FaceIndex", "-1")) == profile
    except ValueError:
        return False


def read_override(text: str, profile: int) -> dict | None:
    for e in _parse(text):
        if _matches_profile(e, profile):
            return {
                "eyesX": int(e["EyesX"]), "eyesY": int(e["EyesY"]),
                "mouthX": int(e["MouthX"]), "mouthY": int(e["MouthY"]),
                "name": e.get("Name", ""),
            }
    return None


def upsert(text: str, profile: int, name: str, eyesX: int, eyesY: int,
           mouthX: int, mouthY: int) -> str:
    """Insert or replace this profile's active small-face override."""
    entries = _parse(text)
    entry = {
        "uiIndex": str(profile),        # any non-0, non-65535 value activates
        "Name": name or f"RPC{profile}",
        "FaceIndex": str(profile),      # matched against the profile id
        "EyesX": str(eyesX), "EyesY": str(eyesY),
        "MouthX": str(mouthX), "MouthY": str(mouthY),
    }
    for i, e in enumerate(entries):
        if _matches_profile(e, profile):
            entries[i] = entry
            return _serialize(entries)
    entries.append(entry)
    return _serialize(entries)


def remove(text: str, profile: int) -> str:
    entries = [e for e in _parse(text) if not _matches_profile(e, profile)]
    return _serialize(entries)
