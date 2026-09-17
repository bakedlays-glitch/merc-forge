"""Target-aware SoldierBodyTypes registries.

The engine indexes its animation tables directly by ``ubBodyType``.  A body
type is therefore valid only for the selected install's engine, not merely
because another 1.13 variant happened to define that numeric value.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from .inject import profiles_xml
from .install_context import make_install_context
from .mod_detect import ModId, detect_mod


@dataclass(frozen=True)
class BodyTypeDef:
    id: int
    name: str
    sex: str | None
    category: str
    # Observed extension IDs are retained only so an existing profile can be
    # edited without silently replacing its current animation value.  They
    # are not evidence that another slot may safely use the ID.
    authorable: bool = True


@dataclass(frozen=True)
class BodyTypeRegistry:
    mod_id: str
    source: str
    options: Mapping[int, BodyTypeDef]


class BodyTypeWriteError(ValueError):
    """Base class for target-registry body-type write rejections."""

    code = "BODY_TYPE_WRITE_REJECTED"

    def __init__(self, body_type: int, message: str) -> None:
        self.body_type = body_type
        super().__init__(message)

    def issue(self) -> dict[str, str]:
        return {
            "severity": "error",
            "field": "ubBodyType",
            "code": self.code,
            "message": str(self),
        }


class PreserveOnlyBodyTypeError(BodyTypeWriteError):
    """An observed body type was requested for anything but its own value."""

    code = "BODY_TYPE_PRESERVE_ONLY"

    def __init__(self, body_type: int) -> None:
        super().__init__(body_type,
            f"ubBodyType={body_type} was observed in this install but is not engine-verified. "
            "It may only be preserved on the merc that already uses it."
        )


class UnknownBodyTypeError(BodyTypeWriteError):
    """A write requested an ID absent from the selected engine registry."""

    code = "BODY_TYPE_UNKNOWN"

    def __init__(self, body_type: int) -> None:
        super().__init__(body_type,
            f"ubBodyType={body_type} is not valid for the selected target "
            "engine's SoldierBodyTypes registry. It may index past the "
            "animation surface array and crash on render."
        )


def _body_type(
    id: int,
    name: str,
    category: str,
    sex: str | None = None,
    *,
    authorable: bool = True,
) -> BodyTypeDef:
    return BodyTypeDef(id=id, name=name, sex=sex, category=category, authorable=authorable)


# Tactical/Animation Data.h's vanilla enum values.  TOTALBODYTYPES is the
# exclusive sentinel at 29 and deliberately has no entry here.
VANILLA_BODY_TYPES: dict[int, BodyTypeDef] = {
    0: _body_type(0, "REGMALE", "humanoid", "male"),
    1: _body_type(1, "BIGMALE", "humanoid", "male"),
    2: _body_type(2, "STOCKYMALE", "humanoid", "male"),
    3: _body_type(3, "REGFEMALE", "humanoid", "female"),
    4: _body_type(4, "ADULTFEMALEMONSTER", "monster", "female"),
    5: _body_type(5, "AM_MONSTER", "monster", "male"),
    6: _body_type(6, "YAF_MONSTER", "monster", "female"),
    7: _body_type(7, "YAM_MONSTER", "monster", "male"),
    8: _body_type(8, "LARVAE_MONSTER", "monster"),
    9: _body_type(9, "INFANT_MONSTER", "monster"),
    10: _body_type(10, "QUEENMONSTER", "monster", "female"),
    11: _body_type(11, "FATCIV", "civilian"),
    12: _body_type(12, "MANCIV", "civilian"),
    13: _body_type(13, "MINICIV", "civilian"),
    14: _body_type(14, "DRESSCIV", "civilian"),
    15: _body_type(15, "HATKIDCIV", "civilian"),
    16: _body_type(16, "KIDCIV", "civilian"),
    17: _body_type(17, "CRIPPLECIV", "civilian"),
    18: _body_type(18, "COW", "animal"),
    19: _body_type(19, "CROW", "animal"),
    20: _body_type(20, "BLOODCAT", "animal"),
    21: _body_type(21, "ROBOTNOWEAPON", "robot"),
    22: _body_type(22, "HUMVEE", "vehicle"),
    23: _body_type(23, "TANK_NW", "vehicle"),
    24: _body_type(24, "TANK_NE", "vehicle"),
    25: _body_type(25, "ELDORADO", "vehicle"),
    26: _body_type(26, "ICECREAMTRUCK", "vehicle"),
    27: _body_type(27, "JEEP", "vehicle"),
    28: _body_type(28, "COMBAT_JEEP", "vehicle"),
}


WASTELAND_BODY_TYPES: dict[int, BodyTypeDef] = {
    **VANILLA_BODY_TYPES,
    29: _body_type(29, "DOG", "animal"),
    30: _body_type(30, "GORISCLAW", "monster"),
    31: _body_type(31, "GRUTHARCLAW", "monster"),
    32: _body_type(32, "MOMCLAW", "monster"),
    33: _body_type(33, "MUTANT", "humanoid"),
    34: _body_type(34, "ALPHACLAW", "monster"),
    35: _body_type(35, "NIGHTKIN", "humanoid"),
    36: _body_type(36, "GHOUL", "humanoid"),
    37: _body_type(37, "FERALGHOUL", "humanoid"),
    38: _body_type(38, "GLOWGHOUL", "humanoid"),
    39: _body_type(39, "RADSCORPION", "monster"),
    40: _body_type(40, "HULK", "humanoid"),
    41: _body_type(41, "MARCUS", "humanoid", "male"),
    42: _body_type(42, "JAY", "humanoid", "male"),
    43: _body_type(43, "SILENTBOB", "humanoid", "male"),
}

CANONICAL_WASTELAND_EXE_SIZE = 10_231_808
CANONICAL_WASTELAND_EXE_SHA256 = "eaeacace4e958aa96f4a5e7bd36dea084632ccf7f2c1932e1217305e8159de77"


def _normal_ja2_executable(install_root: Path) -> Path | None:
    """Find only the normal launcher name, case-insensitively.

    ``install_detect.find_exe`` intentionally supports alternate tools and
    historical launcher names.  Those are valid ways to register an install,
    but they cannot prove which engine will run this registry.  Body-type
    extension IDs therefore accept only the normal ``ja2.exe`` target.
    """
    try:
        for candidate in install_root.iterdir():
            if candidate.is_file() and candidate.name.casefold() == "ja2.exe":
                return candidate
    except OSError:
        pass
    return None


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_canonical_wasteland_engine(install_root: Path) -> bool:
    """Verify the normal launch executable against the canonical Copy build."""
    executable = _normal_ja2_executable(install_root)
    if executable is None:
        return False
    try:
        return (
            executable.stat().st_size == CANONICAL_WASTELAND_EXE_SIZE
            and _sha256_file(executable).casefold() == CANONICAL_WASTELAND_EXE_SHA256
        )
    except OSError:
        return False


def _has_wasteland_body_type_fingerprint(install_root: Path) -> bool:
    """Return whether target content proves the custom Wasteland body table.

    ``detect_mod`` deliberately treats folder names as useful informational
    hints.  They are not safe authority for an engine-indexed registry: a
    copied stock install can easily contain "Fallout" or "Wasteland" in its
    directory name.  Require one of the content markers that accompanies the
    custom Wasteland data before surfacing IDs 29–43 as selectable.
    """
    layout = make_install_context(install_root).layout
    tileset_70 = layout.resolve_read("TileSets/Tileset 70")
    if tileset_70 is not None and tileset_70.is_dir():
        return True
    ja2set = layout.resolve_read("Ja2Set.dat.xml")
    if ja2set is None or not ja2set.is_file():
        return False
    try:
        return "FALLOUT VAULT" in ja2set.read_text(encoding="utf-8", errors="replace").upper()
    except OSError:
        return False


def registry_with_observed_extensions(
    install_root: Path, mod_id: str, *, source: str = "observed-extension",
) -> BodyTypeRegistry:
    """Return vanilla entries plus IDs already present in an unknown target.

    Observed IDs are retained for non-destructive editing/import of that
    install; they are not claims about another engine's animation table.
    """
    options = dict(VANILLA_BODY_TYPES)
    profiles_path = make_install_context(install_root).profiles_xml_path()
    for fields in profiles_xml.read_all_slots(profiles_path).values():
        try:
            body_type = int((fields.get("ubBodyType") or "").strip())
        except ValueError:
            continue
        if body_type not in options:
            options[body_type] = _body_type(
                body_type,
                f"Observed {body_type}",
                "observed",
                authorable=False,
            )
    return BodyTypeRegistry(mod_id=mod_id, source=source, options=options)


def body_types_for_install(install_root: Path) -> BodyTypeRegistry:
    """Return the valid body types for the selected target installation."""
    mod = detect_mod(install_root)
    if _has_wasteland_body_type_fingerprint(install_root):
        if _has_canonical_wasteland_engine(install_root):
            return BodyTypeRegistry(ModId.WASTELAND.value, "engine-known", WASTELAND_BODY_TYPES)
        # The data identifies Wasteland, but an unknown/frozen/rebuilt engine
        # cannot safely promise that its animation table reaches ID 43.
        return registry_with_observed_extensions(
            install_root, ModId.WASTELAND.value, source="engine-unverified",
        )
    if mod.id is ModId.VANILLA:
        return BodyTypeRegistry(mod.id.value, "engine-known", VANILLA_BODY_TYPES)
    return registry_with_observed_extensions(install_root, mod.id.value)


def validate_body_type_write(
    install_root: Path,
    requested_body_type: int,
    *,
    existing_body_type: int | None = None,
) -> BodyTypeRegistry:
    """Return the live registry after enforcing the preserve-only invariant.

    Unknown observed values are display/import preservation data, never proof
    that a different profile can safely use the engine's animation table.
    Call this while holding the physical-root write lock, immediately before
    mutating MercProfiles.xml.  Regular audit still reports IDs missing from
    the registry; this helper distinguishes known-but-unverified observations.
    """
    registry = body_types_for_install(install_root)
    definition = registry.options.get(requested_body_type)
    if definition is None:
        raise UnknownBodyTypeError(requested_body_type)
    if definition is not None and not definition.authorable:
        if requested_body_type != existing_body_type:
            raise PreserveOnlyBodyTypeError(requested_body_type)
    return registry
