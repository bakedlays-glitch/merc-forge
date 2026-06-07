"""Virtual File System (VFS) awareness for JA2 1.13 mod installs.

Modern 1.13 mods (Vengeance, AIMNAS, Wildfire, Wasteland, etc.) compose
the engine's file view from a stack of layered directories declared in
a `vfs_config.<Mod>.ini` referenced from the install's `Ja2.ini`. Later
profiles in the chain override earlier ones — Linux-overlayfs style.

A merc's "complete data" can therefore live across multiple layers:
- His MercProfiles row in `Data-Vengeance/TableData/MercProfiles.xml`
- His gear row in `Data-AIMv53/TableData/Inventory/MercStartingGear.xml`
- His bio EDT in `Data-Vengeance/MercEdt/218.EDT`

The wizard's old assumption — "everything lives at Data-1.13/..." — only
holds for stock vanilla 1.13. For every modded install, it's wrong, and
the wizard sees the (empty) vanilla layer instead of the mod's override.

This module models that. `parse_vfs_config()` walks an install and
returns a `VfsLayout` describing every layer, what directories it
mounts, and which one is the writable "mod content" layer.

Pre-VFS installs (no `VFS_CONFIG_INI = ...` in Ja2.ini) get a synthetic
single-layer layout pointing at `Data-1.13/`, matching the legacy
wizard behavior.
"""
from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Profile names that the wizard treats as read-only system layers.
# These hold either vanilla content (Vanilla, v113, SlfLibs) or user
# state (UserProf), or media-only assets (music/sounds) that the wizard
# never writes to.
class VfsConfigError(Exception):
    """Raised when the install declares a VFS config that's missing or
    malformed.

    Refusing to operate is safer than silently degrading to the legacy
    `Data-1.13/` layout: writes would land where the modded engine never
    reads, producing an invisible failure mode (created mercs don't
    appear in-game). The caller should surface this to the user and
    require them to fix the Ja2.ini reference or the vfs_config.<Mod>.ini
    file before proceeding.

    A pre-VFS install (Ja2.ini with no `VFS_CONFIG_INI` line at all) is
    NOT an error — that's the legitimate legacy path and `_legacy_layout`
    still handles it.
    """


SYSTEM_PROFILE_PATTERNS = re.compile(
    r"^(SlfLibs|Vanilla|v113|UserProf|music|pcm|ui|weapsounds?|"
    r"VoiceTaunts|sounds?|Faces|maps?|tiles|cache)$",
    re.IGNORECASE,
)

# Profile names that LOOK like mod content but are actually overlays
# (graphics packs, new-maps packs, customisation packs, …) layered on
# top of the real mod core. Research stream 3 found four installs where
# the highest non-system profile is one of these — picking it would
# point the wizard at the overlay instead of the editable mod content.
OVERLAY_PROFILE_PATTERNS = re.compile(
    r"^(FSGraphics|.*NewMaps|.*BigMaps|.*Bigmaps|CamoInterface|"
    r"Data-CamoInterface|.*Custom|.*Customisation|.*Customization|"
    r"DesertCamo|UrbanCamo|WoodCamo|.*Overlay|.*Patch)$",
    re.IGNORECASE,
)


# Files whose presence is a strong signal that a layer holds mod-editable
# merc data (rather than tiles, graphics overrides, audio packs, etc.).
MOD_CONTENT_SIGNAL_FILES = (
    "TableData/MercProfiles.xml",
    "TableData/AIMAvailability.xml",
)


@dataclass
class VfsLocation:
    """One directory or SLF mount in a VFS profile."""
    name: str                # The `[LOC_<name>]` section key
    type: str                # "DIRECTORY" or "SLF" (we only handle DIRECTORY)
    path: Path               # Absolute path on disk

    @property
    def is_directory(self) -> bool:
        return self.type.upper() == "DIRECTORY"


@dataclass
class VfsProfile:
    """One layer in the VFS stack.

    Position in the parent `VfsLayout.profiles` list = priority (later =
    higher priority).
    """
    name: str
    locations: list[VfsLocation] = field(default_factory=list)
    write_allowed: bool = False

    @property
    def is_system(self) -> bool:
        return bool(SYSTEM_PROFILE_PATTERNS.match(self.name))

    @property
    def is_overlay(self) -> bool:
        """True if the profile name suggests a content-overlay layer (maps,
        graphics, customisation pack) rather than the mod's core content.

        Used to skip these when guessing the writable mod content layer —
        otherwise installs like DL113 + FSGraphics or UC113 + UC113NewMaps
        point the wizard at the overlay instead of the editable mod core.
        """
        return bool(OVERLAY_PROFILE_PATTERNS.match(self.name))


@dataclass
class VfsLayout:
    """The full VFS chain for an install."""
    install_root: Path
    vfs_config_path: Optional[Path]   # None for legacy / pre-VFS installs
    profiles: list[VfsProfile] = field(default_factory=list)
    mod_content_profile: Optional[str] = None  # Name of the profile reads/writes target
    # Non-empty when something went wrong but we recovered with a fallback —
    # surface to the UI so the user can fix it. Examples:
    #   "VFS_CONFIG_INI references X but the file does not exist"
    errors: list[str] = field(default_factory=list)

    @property
    def is_legacy(self) -> bool:
        """True if this is a synthesized layout (no VFS config found)."""
        return self.vfs_config_path is None

    def writable_profile(self) -> Optional[VfsProfile]:
        """The profile the wizard should treat as the mod content layer."""
        if self.mod_content_profile is None:
            return None
        for p in self.profiles:
            if p.name == self.mod_content_profile:
                return p
        return None

    def resolve_read(self, rel_path: str) -> Optional[Path]:
        """Find the highest-priority existing copy of `rel_path` in the chain.

        Walks profiles in reverse order (highest priority first) and checks
        each profile's locations. Returns the first match or None.

        `rel_path` is forward-slash separated and case-insensitive on Windows
        (which is how JA2 itself does lookups).
        """
        rel = rel_path.replace("\\", "/")
        for profile in reversed(self.profiles):
            for loc in profile.locations:
                if not loc.is_directory:
                    continue
                candidate = loc.path / rel
                if candidate.exists():
                    return candidate
        return None

    def resolve_write(self, rel_path: str) -> Path:
        """Decide where to write `rel_path`.

        Rules:
        1. If the file already exists somewhere in the chain, write to THAT
           layer (in-place modification, preserves the mod author's intent).
        2. Otherwise, write to the first location of the mod content profile.
        3. As a last resort fall back to install_root / rel_path.

        Refuses to operate when `self.errors` is non-empty AND we fell
        back to a legacy layout — the user's Ja2.ini declared a
        `VFS_CONFIG_INI` line we couldn't honor (file missing, parse
        error). Writing to the legacy `Data-1.13/` location in that case
        is silent data loss because the engine reads from the directory
        chain the broken config WOULD have set up.

        The caller (route handler) should surface the error to the user
        and prompt them to fix Ja2.ini before any write. Refusing here is
        the last line of defense — the install-scan layer already
        surfaces `layout.errors` in the install validation report.
        """
        if self.errors and self.is_legacy and self.vfs_config_path is None:
            # Errors present AND we're in fallback mode (no VFS config
            # successfully parsed). The user's broken config was meant to
            # produce a different layout. Refuse the write.
            raise VfsConfigError(
                f"Refusing to write {rel_path!r}: this install's VFS config "
                f"couldn't be loaded ({self.errors[0]}). Fix Ja2.ini's "
                "VFS_CONFIG_INI line or remove it (legacy mode) before any "
                "merc-content writes — otherwise the file would land where "
                "the modded engine never reads it."
            )
        existing = self.resolve_read(rel_path)
        if existing is not None:
            return existing
        write_profile = self.writable_profile()
        if write_profile and write_profile.locations:
            first_loc = next((l for l in write_profile.locations if l.is_directory), None)
            if first_loc:
                return first_loc.path / rel_path.replace("\\", "/")
        return self.install_root / rel_path.replace("\\", "/")

    def resolve_in_mod_content(self, rel_path: str) -> Optional[Path]:
        """Resolve `rel_path` looking ONLY in the mod content profile.

        Bypasses the full VFS chain — useful when reading merc-data content
        (audio, faces, table rows) where higher-priority media-only layers
        (PCM, weapsounds, music) might shadow the real mod data with empty
        or unrelated overrides.

        Returns the first existing path in the mod content profile's
        locations, or None.
        """
        rel = rel_path.replace("\\", "/")
        write_profile = self.writable_profile()
        if write_profile is None:
            return None
        for loc in write_profile.locations:
            if not loc.is_directory:
                continue
            candidate = loc.path / rel
            if candidate.exists():
                return candidate
        return None

    def mod_content_path(self, rel_path: str) -> Path:
        """The absolute path of `rel_path` in the mod content profile's
        first directory location, whether it exists yet or not.

        Used for writes that should always land in the mod's editable
        layer (e.g., new audio files, new face STIs) rather than wherever
        the chain happens to resolve.
        """
        rel = rel_path.replace("\\", "/")
        write_profile = self.writable_profile()
        if write_profile and write_profile.locations:
            first_dir = next((l for l in write_profile.locations if l.is_directory), None)
            if first_dir:
                return first_dir.path / rel
        return self.install_root / rel

    def all_existing_copies(self, rel_path: str) -> list[Path]:
        """Every layer that has a copy of this file (highest priority first).

        Useful for backup snapshots — we may want to back up the override
        chain even if the wizard only edits the topmost copy.
        """
        rel = rel_path.replace("\\", "/")
        hits: list[Path] = []
        for profile in reversed(self.profiles):
            for loc in profile.locations:
                if not loc.is_directory:
                    continue
                candidate = loc.path / rel
                if candidate.exists():
                    hits.append(candidate)
        return hits


# ──────────────────────────────────────────────────────────────────────────
#  Parsing
# ──────────────────────────────────────────────────────────────────────────


# Common locations where mod vfs_config files live, relative to install
# root. Modpacks (like Russian-language rebundles) ship multiple of these
# so the user can switch between AIMNAS / Wildfire / UB etc. without
# reinstalling -- each mod gets its own `vfs_config.<modname>.ini`.
#
# Patterns ordered by frequency. Bounded so we don't rglob the entire
# install (which can take seconds on a modpack with thousands of files).
_VFS_CONFIG_GLOBS = (
    "vfs_config*.ini",                         # install root (most common)
    "*/vfs_config*.ini",                       # one level down
    "Data*/Addons/*/vfs_config*.ini",          # modpack Addons convention (SDO case)
    "Addons/*/vfs_config*.ini",                # older Addons convention
)


def find_vfs_configs(install_root: Path) -> list[Path]:
    """Enumerate every `vfs_config*.ini` file under an install root.

    Returns absolute paths, deduplicated TWO ways:
      1. By resolved path (same file matched by multiple globs).
      2. By mod name (the stem after `vfs_config.`). Modpacks often ship
         the same logical mod's config at multiple locations -- e.g.
         `<install>/vfs_config.UB_SOG69-Vietnam.ini` (root launcher copy)
         AND `<install>/Data-UB/AddOns/Data-Mod-SOG69-Vietnam/vfs_config.UB_SOG69-Vietnam.ini`
         (deep mod-author canonical copy). Both name the same mod, so
         we keep only one. Preference: the shallowest path -- the
         install-root copy is the user-facing launcher choice that the
         engine traditionally reads via Ja2.ini's VFS_CONFIG_INI.

    Returns an empty list for legacy / pre-VFS installs or when none
    are present.
    """
    install_root = Path(install_root)
    if not install_root.is_dir():
        return []
    # First: collect ALL matches, dedup by resolved path.
    by_resolved: dict[Path, None] = {}
    for pattern in _VFS_CONFIG_GLOBS:
        try:
            for path in install_root.glob(pattern):
                if not path.is_file():
                    continue
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                by_resolved.setdefault(resolved, None)
        except (OSError, PermissionError):
            continue

    # Second: dedup by mod name, preferring the shallowest path. Same
    # mod name at different depths == duplicate copies of the same logical
    # mod (e.g. UB_SOG69-Vietnam at install root vs. nested in the mod's
    # content dir). Path-depth tiebreak: install-root copy wins over a
    # nested copy because that's what JA2's launcher historically reads.
    install_root_resolved = install_root.resolve()
    by_mod_name: dict[str, Path] = {}
    for resolved in by_resolved.keys():
        mod_name = mod_name_from_vfs_config(resolved)
        # Fall back to the full filename when the stem can't be parsed
        # (anonymous `vfs_config.ini`); two anonymous configs at the same
        # depth would still collapse, which is correct.
        key = mod_name or resolved.name.lower()
        existing = by_mod_name.get(key)
        if existing is None:
            by_mod_name[key] = resolved
            continue
        # Tiebreak: shallower (relative to install root) wins.
        try:
            existing_depth = len(existing.resolve().relative_to(install_root_resolved).parts)
        except (OSError, ValueError):
            existing_depth = 999
        try:
            new_depth = len(resolved.relative_to(install_root_resolved).parts)
        except (OSError, ValueError):
            new_depth = 999
        if new_depth < existing_depth:
            by_mod_name[key] = resolved
    return list(by_mod_name.values())


def mod_name_from_vfs_config(vfs_config_path: Path) -> str:
    """Derive a display-friendly mod name from a vfs_config filename.

    Examples:
        vfs_config.Vengeance.ini  -> "Vengeance"
        vfs_config.UB_SOG69-Vietnam.ini -> "UB_SOG69-Vietnam"
        vfs_config.AIMNAS.ini -> "AIMNAS"
        vfs_config.JA2113AIMNAS.ini -> "AIMNAS"
        vfs_config.ini -> "" (legacy generic name; caller falls back)
    """
    stem = vfs_config_path.stem  # drops `.ini`
    # Strip leading `vfs_config` / `vfs_config.` / `vfs_config_`
    m = re.match(r"^vfs_config[._-]?", stem, re.IGNORECASE)
    if m:
        stem = stem[m.end():]
    # 1.13 builds prefix some bundled mod configs with the engine version
    # (`JA2113AIMNAS`, `JA2113Custom`, etc.). Strip the `JA2113` / `JA2`
    # prefix when there's a real mod name after it so the UI displays
    # the mod, not the engine label. `JA2113` alone stays as-is (the
    # caller's stock-config filter handles that case separately).
    m = re.match(r"^(?:JA2113|JA2)(?=[A-Za-z])(.+)$", stem)
    if m:
        return m.group(1)
    return stem


def write_vfs_config_to_ja2_ini(
    ja2_ini_path: Path, vfs_config_relative: str,
    backup_suffix: str = ".mwbak",
) -> None:
    """Update Ja2.ini's `VFS_CONFIG_INI` line to point at `vfs_config_relative`.

    Creates a one-time backup at `<Ja2.ini path><backup_suffix>` before
    the first modification so the user can recover their original setup.
    Idempotent on the backup (doesn't overwrite an existing one).

    Used when the user picks a specific mod from a multi-VFS install --
    we rewrite Ja2.ini so the next `parse_vfs_config()` call (and any
    in-game launch) uses the chosen mod's config.
    """
    if not ja2_ini_path.is_file():
        raise FileNotFoundError(f"Ja2.ini not found at {ja2_ini_path}")
    # One-time backup of the original Ja2.ini.
    backup_path = ja2_ini_path.with_suffix(ja2_ini_path.suffix + backup_suffix)
    if not backup_path.exists():
        import shutil
        shutil.copy2(ja2_ini_path, backup_path)

    text = ja2_ini_path.read_text(encoding="utf-8", errors="replace")
    new_line = f"VFS_CONFIG_INI = {vfs_config_relative}"
    out_lines: list[str] = []
    replaced = False
    for line in text.splitlines():
        stripped = line.strip()
        # Replace the first non-comment VFS_CONFIG_INI line we see.
        if (
            not replaced
            and stripped
            and not stripped.startswith(";")
            and not stripped.startswith("#")
            and stripped.upper().startswith("VFS_CONFIG_INI")
            and "=" in stripped
        ):
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        # No existing VFS_CONFIG_INI line -- append one. Add a blank line
        # before it if the file didn't end with one for readability.
        if out_lines and out_lines[-1].strip():
            out_lines.append("")
        out_lines.append(new_line)
    ja2_ini_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def _read_vfs_config_path_from_ja2_ini(ja2_ini_path: Path) -> Optional[Path]:
    """Read `VFS_CONFIG_INI = ...` from Ja2.ini.

    Returns the path relative to install_root, or None if the line is
    absent or commented out.
    """
    if not ja2_ini_path.is_file():
        return None
    try:
        text = ja2_ini_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("#"):
            continue
        if not stripped.upper().startswith("VFS_CONFIG_INI"):
            continue
        if "=" not in stripped:
            continue
        _, value = stripped.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            # Normalize backslashes to forward slashes for cross-platform sanity
            return Path(value.replace("\\", "/"))
    return None


def compute_vfs_mismatch(install_path: Path, vfs_config_path: Optional[Path]) -> bool:
    """True when an install's bound `vfs_config_path` disagrees with the
    VFS_CONFIG_INI line in its live `Ja2.ini`.

    Bug-review B5: after bug #11 the activation handshake stopped writing
    to Ja2.ini, so the user can register the same install folder twice
    (one entry bound to AIMNAS, one to Wildfire), activate the Wildfire
    entry, and end up editing the AIMNAS content layer because Ja2.ini
    still names AIMNAS. The engine reads what Ja2.ini says; the wizard
    reads what the activated entry says; saves silently go nowhere
    visible in-game.

    Returns False (no mismatch to surface) when:
    - The install has no specific `vfs_config_path` bound — the user
      hasn't picked a mod profile so there's no expectation to violate.
    - Ja2.ini's VFS_CONFIG_INI value (forward-slash, case-insensitive)
      equals `vfs_config_path` taken relative to `install_path`.
    - Ja2.ini doesn't exist on disk — we can't tell, don't false-positive.
    - `vfs_config_path` isn't under `install_path` — can't form a
      relative comparison; bail out rather than guess.

    Returns True when:
    - The install is bound to a specific config but Ja2.ini has no
      VFS_CONFIG_INI line at all (legacy install with a mod choice
      pending — clicking Apply VFS gives the engine the line).
    - Ja2.ini names a DIFFERENT config than the one the registration
      is bound to.
    """
    if vfs_config_path is None:
        return False

    ja2_ini: Optional[Path] = None
    for name in ("Ja2.ini", "ja2.ini", "JA2.INI"):
        candidate = install_path / name
        if candidate.is_file():
            ja2_ini = candidate
            break
    if ja2_ini is None:
        return False

    try:
        expected_rel = Path(vfs_config_path).resolve().relative_to(
            Path(install_path).resolve()
        )
    except (OSError, ValueError):
        return False

    live_rel = _read_vfs_config_path_from_ja2_ini(ja2_ini)
    if live_rel is None:
        return True

    expected_norm = str(expected_rel).replace("\\", "/").lower()
    live_norm = str(live_rel).replace("\\", "/").lower()
    return expected_norm != live_norm


def _read_custom_data_location_from_ja2_ini(ja2_ini_path: Path) -> Optional[str]:
    """Read the legacy pre-VFS `CUSTOM_DATA_LOCATION = ...` directive.

    Used by older mods (e.g. Renegade Republic) that predate the
    `VFS_CONFIG_INI` scheme. Tells the engine to also read from this
    directory on top of `Data/` and `Data-1.13/`.
    """
    if not ja2_ini_path.is_file():
        return None
    try:
        text = ja2_ini_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("#"):
            continue
        if not stripped.upper().startswith("CUSTOM_DATA_LOCATION"):
            continue
        if "=" not in stripped:
            continue
        _, value = stripped.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            return value
    return None


def _parse_csv(text: str) -> list[str]:
    """Comma-separated list, trimmed, with empties dropped."""
    return [item.strip() for item in text.split(",") if item.strip()]


def parse_vfs_config(install_root: Path) -> VfsLayout:
    """Parse the VFS chain for an install.

    Falls back to a legacy single-layer layout for:
      - installs with no Ja2.ini
      - installs with no `VFS_CONFIG_INI` line (synthesizes a layout from
        whichever of `Data/`, `Data-1.13/`, and `CUSTOM_DATA_LOCATION`
        actually exist)
      - installs whose `VFS_CONFIG_INI` references a missing/unreadable
        file (errors list populated so the UI can surface it)

    The returned `VfsLayout` always has at least one profile.
    """
    install_root = Path(install_root).resolve()

    ja2_ini = None
    for name in ("Ja2.ini", "ja2.ini", "JA2.INI"):
        candidate = install_root / name
        if candidate.is_file():
            ja2_ini = candidate
            break

    vfs_rel = _read_vfs_config_path_from_ja2_ini(ja2_ini) if ja2_ini else None

    if vfs_rel is None:
        # Pre-VFS install — synthesize a chain from whatever data dirs exist
        custom_data = _read_custom_data_location_from_ja2_ini(ja2_ini) if ja2_ini else None
        return _legacy_layout(install_root, custom_data_location=custom_data)

    vfs_config_path = install_root / vfs_rel
    if not vfs_config_path.is_file():
        # VFS_CONFIG_INI was set but the referenced file doesn't exist.
        # Raise rather than silently degrade — writes via the resulting
        # legacy layout would land in Data-1.13/ where the modded engine
        # never reads them. The user must fix Ja2.ini before we operate.
        raise VfsConfigError(
            f"VFS_CONFIG_INI in {ja2_ini.name if ja2_ini else 'Ja2.ini'} "
            f"references '{vfs_rel}' but no file exists at "
            f"'{vfs_config_path}'. Fix the path in Ja2.ini, comment the "
            "line out (legacy mode), or restore the missing file."
        )

    try:
        return _parse_vfs_config_file(install_root, vfs_config_path)
    except (OSError, configparser.Error) as e:
        raise VfsConfigError(
            f"Failed to parse {vfs_config_path.name}: {type(e).__name__}: {e}. "
            "Repair the vfs_config file or comment out VFS_CONFIG_INI in "
            "Ja2.ini to use the legacy single-layer layout."
        ) from e


def _legacy_layout(install_root: Path, custom_data_location: Optional[str] = None) -> VfsLayout:
    """Synthesized layout for pre-VFS installs.

    Detects the data dirs that actually exist on disk and stacks them in
    the order the pre-VFS engine reads them:
      Data/  (lowest priority, vanilla content + SLFs)
      Data-1.13/  (1.13 patches, if present)
      <CUSTOM_DATA_LOCATION>/  (mod content, if set — Renegade Republic style)

    The mod content profile is the highest-priority directory found,
    matching the wizard's "write to the mod's editable layer" intent.
    """
    candidates: list[tuple[str, Path]] = []
    data_dir = install_root / "Data"
    if data_dir.is_dir():
        candidates.append(("data-legacy", data_dir))
    data_113 = install_root / "Data-1.13"
    if data_113.is_dir():
        candidates.append(("v113-legacy", data_113))
    if custom_data_location:
        custom_path = install_root / custom_data_location
        if custom_path.is_dir():
            candidates.append((f"custom-{custom_data_location}", custom_path))

    # If nothing exists, fall back to Data-1.13 as the wizard's historical
    # default — caller will likely fail validation but at least paths resolve.
    if not candidates:
        candidates = [("v113-legacy", data_113)]

    profiles = [
        VfsProfile(
            name=name,
            locations=[VfsLocation(name=name.replace("-", "_") + "_dir",
                                   type="DIRECTORY", path=path)],
            write_allowed=False,  # legacy chain has no engine-write target
        )
        for name, path in candidates
    ]
    return VfsLayout(
        install_root=install_root,
        vfs_config_path=None,
        profiles=profiles,
        mod_content_profile=candidates[-1][0],  # highest-priority is mod content
    )


def _find_section_ci(cp: configparser.ConfigParser, target: str) -> Optional[str]:
    """Case-insensitive section lookup.

    The engine treats section/profile names case-insensitively (verified
    against bfVFS source). Vengeance's config has `PROFILES = ... pcm ...`
    referring to a section spelled `[PROFILE_PCM]` — the uppercase pcm.
    """
    target_lower = target.lower()
    for section in cp.sections():
        if section.lower() == target_lower:
            return section
    return None


def _parse_vfs_config_file(install_root: Path, vfs_config_path: Path) -> VfsLayout:
    """Parse a vfs_config.<Mod>.ini file into a VfsLayout."""
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    # strict=False tolerates duplicate sections (Vengeance has [PROFILE_v113]
    # declared twice). Last write wins.
    text = vfs_config_path.read_text(encoding="utf-8", errors="replace")
    cp.read_string(text)

    # The [vfs_config] section is itself looked up case-insensitively
    config_section = _find_section_ci(cp, "vfs_config")
    if config_section is None:
        return _legacy_layout(install_root)
    profiles_csv = cp.get(config_section, "PROFILES", fallback="")
    profile_names = _parse_csv(profiles_csv)
    if not profile_names:
        return _legacy_layout(install_root)

    # Build location table first (LOC_* sections), keyed by lowercased name
    locations: dict[str, VfsLocation] = {}
    for section in cp.sections():
        if not section.lower().startswith("loc_"):
            continue
        name = section[4:]  # strip "LOC_" / "loc_" / "Loc_"
        loc_type = cp.get(section, "TYPE", fallback="DIRECTORY").strip()
        loc_path = cp.get(section, "PATH", fallback="").strip()
        if not loc_path:
            continue
        abs_path = (install_root / loc_path.replace("\\", "/")).resolve()
        locations[name.lower()] = VfsLocation(name=name, type=loc_type, path=abs_path)

    # Build profiles — match section names case-insensitively
    profiles: list[VfsProfile] = []
    for pname in profile_names:
        section = _find_section_ci(cp, f"PROFILE_{pname}")
        if section is None:
            continue
        location_csv = cp.get(section, "LOCATIONS", fallback="")
        location_names = _parse_csv(location_csv)
        prof_locations = [
            locations[n.lower()] for n in location_names if n.lower() in locations
        ]
        write_str = cp.get(section, "WRITE", fallback="false").strip().lower()
        write_allowed = write_str in ("true", "1", "yes")
        profiles.append(VfsProfile(
            name=pname,
            locations=prof_locations,
            write_allowed=write_allowed,
        ))

    mod_content = _pick_mod_content_profile(profiles)
    return VfsLayout(
        install_root=install_root,
        vfs_config_path=vfs_config_path,
        profiles=profiles,
        mod_content_profile=mod_content,
    )


def _pick_mod_content_profile(profiles: list[VfsProfile]) -> Optional[str]:
    """Choose the profile that holds the mod's editable merc content.

    Strategy (in priority order):
      1. Highest-priority non-system, non-overlay profile that has at
         least one directory location AND contains
         `TableData/MercProfiles.xml` on disk.
      2. Highest-priority non-system, non-overlay profile with at least
         one directory location, even if no MercProfiles.xml was found
         (fresh-install / partial-content case).
      3. Highest-priority non-system profile (relax the overlay filter).
      4. Whichever profile has `Data-1.13/` in its location paths.
      5. The topmost profile.

    Each fallback widens the criteria so we always return SOMETHING the
    caller can write to. Higher steps in this list are the more confident
    picks; surface them to the UI so the user can override if wrong.
    """
    # Step 1: non-system, non-overlay, with MercProfiles.xml present
    for profile in reversed(profiles):
        if profile.is_system or profile.is_overlay:
            continue
        if not any(l.is_directory for l in profile.locations):
            continue
        for loc in profile.locations:
            if not loc.is_directory:
                continue
            for signal in MOD_CONTENT_SIGNAL_FILES:
                if (loc.path / signal).is_file():
                    return profile.name

    # Step 2: non-system, non-overlay, any directory location
    for profile in reversed(profiles):
        if profile.is_system or profile.is_overlay:
            continue
        if any(l.is_directory for l in profile.locations):
            return profile.name

    # Step 3: non-system (allow overlay)
    for profile in reversed(profiles):
        if profile.is_system:
            continue
        if any(l.is_directory for l in profile.locations):
            return profile.name

    # Step 4: explicit Data-1.13 fallback
    for profile in profiles:
        if any("Data-1.13" in str(l.path) for l in profile.locations):
            return profile.name

    # Step 5: topmost
    return profiles[-1].name if profiles else None
