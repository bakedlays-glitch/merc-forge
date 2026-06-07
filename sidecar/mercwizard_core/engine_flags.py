"""Per-install engine-build flags that change slot semantics.

Two flags matter for the slot picker:

* ``is_ub`` — Urban Brawl build. In UB, ``FIRST_RPC`` is 60 instead of 57
  (see ``Tactical/soldier profile type.h:14``), shifting the named RPC range
  by three slots. Detected by presence of a ``Data-UB/`` directory in the
  install (the canonical UB content layer) or a ``UB113`` / ``UB`` mod tag in
  the active vfs_config.

* ``reads_profiles_from_xml`` — Ja2_Options.ini's ``READ_PROFILE_DATA_FROM_XML``
  key. When ``TRUE`` (the modern default in any post-2018 1.13), the engine
  reads ``<Type>`` per-slot from MercProfiles.xml at boot. The legacy code
  path that tagged slots 51-56 as IMP-fallback is dead. When ``FALSE`` (rare),
  slots 51-56 still go through the legacy mis-tag and must stay LOCKED.

These flags are install-local — never global — so the slot picker takes them
as inputs rather than reading them itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class EngineFlags:
    is_ub: bool
    reads_profiles_from_xml: bool

    @classmethod
    def default(cls) -> "EngineFlags":
        return cls(is_ub=False, reads_profiles_from_xml=True)


def detect_is_ub(install_root: Path, vfs_config_path: Optional[Path] = None) -> bool:
    """Heuristic: an install is a UB build if any of these signals fire.

    * ``Data-UB/`` directory exists under ``install_root`` (UB's content layer)
    * Active vfs_config filename contains ``UB`` (case-insensitive) as a token
    * ``Ja2.ini`` `VFS_CONFIG_INI` line points at a `vfs_config.UB*.ini` file

    A miss means non-UB (default 1.13). We don't probe the .exe — the PE
    resource doesn't distinguish UB cleanly.
    """
    try:
        if (install_root / "Data-UB").is_dir():
            return True
    except OSError:
        pass
    if vfs_config_path is not None:
        # The vfs_config filename for UB is conventionally
        # `vfs_config.UB.ini` or `vfs_config.UB-<variant>.ini`. We need
        # to detect "ub" as a TOKEN, not a substring — naive `"ub" in
        # stem` matches `vfs_config.club`, `pub`, `submod`, `dublin`,
        # etc. and misclassifies the install as UB, which shifts
        # FIRST_RPC from 57 to 60 in slot_picker.engine_named_slots and
        # silently misroutes named-RPC slots 57-59 (MIGUEL, IRA,
        # DIMITRI). Sweep bug-review finding. Split on dot-separated
        # components and look for an exact "ub" or "ub-..." token.
        stem = vfs_config_path.stem.lower()
        # Strip "ubuntu" first — the only legitimate non-UB substring
        # match we know about — then split on common token boundaries.
        sanitized = stem.replace("ubuntu", "")
        tokens = [t for component in sanitized.split(".") for t in component.split("-")]
        if any(t == "ub" or t.startswith("ub_") for t in tokens):
            return True
    try:
        ja2_ini = install_root / "Ja2.ini"
        if ja2_ini.is_file():
            head = ja2_ini.read_text(encoding="utf-8", errors="replace")[:8192]
            for line in head.splitlines():
                if line.strip().lower().startswith("vfs_config_ini"):
                    # Same anchoring rule as above — tokenize the value
                    # side of the `=` rather than checking for substring
                    # "ub" anywhere on the line.
                    value = line.lower().split("=", 1)[-1].strip()
                    value_sanitized = value.replace("ubuntu", "")
                    # Drop the .ini suffix and split on the same
                    # boundaries as the vfs_config_path branch above.
                    if value_sanitized.endswith(".ini"):
                        value_sanitized = value_sanitized[:-4]
                    ini_tokens = [
                        t
                        for component in value_sanitized.split(".")
                        for t in component.split("-")
                    ]
                    if any(t == "ub" or t.startswith("ub_") for t in ini_tokens):
                        return True
    except OSError:
        pass
    return False


def detect_reads_profiles_from_xml(install_root: Path) -> bool:
    """Return True when ``Ja2_Options.ini`` has READ_PROFILE_DATA_FROM_XML=TRUE.

    Default if the key isn't set or the file is missing is TRUE — that matches
    the engine source default at ``Tactical/Soldier Profile.cpp:842`` for any
    1.13 build past 2018. Only an explicit ``=FALSE`` line returns False.

    Searches both ``Ja2_Options.ini`` (canonical) and the rare
    ``Data-1.13/Ja2_Options.ini`` variant for completeness.
    """
    candidates = (
        install_root / "Ja2_Options.ini",
        install_root / "Data-1.13" / "Ja2_Options.ini",
    )
    for path in candidates:
        try:
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip().upper() in {
                "READ_PROFILE_DATA_FROM_XML",
                "READ_PROFILES_FROM_XML",
            }:
                return value.strip().split(";", 1)[0].strip().upper() in {
                    "TRUE", "1", "YES", "ON",
                }
    return True


def detect_engine_flags(
    install_root: Path,
    vfs_config_path: Optional[Path] = None,
) -> EngineFlags:
    """Read both flags for a given install. Safe to call on any path."""
    install_root = Path(install_root)
    return EngineFlags(
        is_ub=detect_is_ub(install_root, vfs_config_path),
        reads_profiles_from_xml=detect_reads_profiles_from_xml(install_root),
    )
