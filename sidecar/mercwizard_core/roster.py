"""Parse MercProfiles.xml × AIMAvailability.xml to produce a unified roster.

The roster is the data structure the UI's Browse screen uses. It enumerates
all 256 slots and reports which are filled, what name/type/etc the merc has,
and whether the slot is AIM-bound.

Slot occupancy is determined by the presence of a non-empty `<zName>` in the
PROFILES XML — the engine treats empty zName as "no merc here".
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .inject.aim_availability import read_all as read_aim_all
from .inject.profiles_xml import read_all_slots
from .models import AimBinding


@dataclass
class RosterEntry:
    """One slot's high-level summary."""
    slot: int
    is_empty: bool
    name: Optional[str] = None
    nickname: Optional[str] = None
    profile_type: Optional[int] = None  # MercProfiles <Type>
    face_index: Optional[int] = None    # MercProfiles <ubFaceIndex>
    aim_binding: Optional[AimBinding] = None
    has_sti_base: bool = False  # Data-1.13/faces/{faceIndex}.sti exists

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "is_empty": self.is_empty,
            "name": self.name,
            "nickname": self.nickname,
            "profile_type": self.profile_type,
            "face_index": self.face_index,
            "aim_bound": self.aim_binding is not None,
            "has_sti": self.has_sti_base,
        }


def load_roster(install_root: Path) -> list[RosterEntry]:
    """Read MercProfiles.xml + AIMAvailability.xml and return 256 entries.

    VFS-aware: routes through the install's mod content layer.
    """
    from .install_context import make_install_context
    ctx = make_install_context(Path(install_root))
    profiles_xml = ctx.profiles_xml_path()
    aim_xml = ctx.aim_xml_path()

    profiles = read_all_slots(profiles_xml)
    aim_bindings = read_aim_all(aim_xml)

    faces_dir = ctx.faces_dir()

    roster: list[RosterEntry] = []
    for slot in range(256):
        prof = profiles.get(slot)
        if prof is None:
            roster.append(RosterEntry(slot=slot, is_empty=True))
            continue

        zname = (prof.get("zName") or "").strip()
        znick = (prof.get("zNickname") or "").strip()
        if not zname and not znick:
            roster.append(RosterEntry(slot=slot, is_empty=True))
            continue

        face_index_str = prof.get("ubFaceIndex") or ""
        face_index = None
        try:
            face_index = int(face_index_str.strip())
        except ValueError:
            pass

        profile_type = None
        type_str = prof.get("Type") or ""
        try:
            profile_type = int(type_str.strip())
        except ValueError:
            pass

        has_sti = False
        if face_index is not None:
            sti_path = faces_dir / f"{face_index}.sti"
            sti_path_padded = faces_dir / f"{face_index:02}.sti"
            has_sti = sti_path.is_file() or sti_path_padded.is_file()

        roster.append(RosterEntry(
            slot=slot,
            is_empty=False,
            name=zname or None,
            nickname=znick or None,
            profile_type=profile_type,
            face_index=face_index,
            aim_binding=aim_bindings.get(slot),
            has_sti_base=has_sti,
        ))

    return roster


def find_empty_slots(roster: list[RosterEntry], in_range: Optional[range] = None) -> list[int]:
    """Return list of empty slot indices, optionally restricted to a range."""
    out: list[int] = []
    for entry in roster:
        if not entry.is_empty:
            continue
        if in_range is not None and entry.slot not in in_range:
            continue
        out.append(entry.slot)
    return out


def find_unused_face_index(roster: list[RosterEntry], min_value: int = 160) -> int:
    """Find the lowest unused ubFaceIndex >= min_value.

    Custom mercs must use ubFaceIndex >= 160 to avoid colliding with vanilla
    Faces.slf (indices 0-159 are reserved).
    """
    used = {e.face_index for e in roster if e.face_index is not None}
    for candidate in range(min_value, 1000):
        if candidate not in used:
            return candidate
    raise ValueError(f"No unused ubFaceIndex >= {min_value} found (1000 limit reached)")
