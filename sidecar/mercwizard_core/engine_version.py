"""Identify what revision an install's engine + data is on.

Two independent signals -- both useful, neither sufficient alone:

  1. The `VERSIONINFO` PE resource of the JA2 executable. Stock 1.13
     builds populate this with `1.13.0.<rev>` or `1.0.0.<rev>` where
     `<rev>` is the SVN revision. Reliable when present, but many
     mod-bundled .exes strip it (SOG69 reads `1.0.0.1` -- useless).

  2. The first `rNNNN` line in `Changelog_Source.txt` /
     `Changelog_Data.txt` at the install root. These are SVN log
     dumps the build packager regenerates each release; the top
     entry's revision number is the build's source/data version.
     Universal across 1.13 mods that follow the convention.

We try PE first (it's the canonical signal); if it returns useless
1.0.0.1-style noise we fall back to the changelog. Source revision
gets priority over data when both are available since "what code is
running" is usually what the user cares about.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class EngineVersion:
    """Identifier for the engine + data revision of an install.

    `display` is the short label for the UI chip (e.g. `r7605` or
    `1.13.0.8748`). `source` is which detector produced it for
    debugging / tooltip ("exe", "changelog_source", "changelog_data").
    """
    display: str
    source: str
    version_string: Optional[str] = None  # full PE version when from exe
    revision: Optional[int] = None        # SVN rev when parseable


# A "useful" PE version is anything more specific than the default
# 1.0.0.1 stub. The build-packager dance: if VERSIONINFO is left at the
# defaults, ignore it and fall through to the changelog.
def _is_useful_exe_version(parts: tuple[int, int, int, int]) -> bool:
    if parts == (0, 0, 0, 0):
        return False
    if parts == (1, 0, 0, 1):
        return False  # PE resource default, used by mod-bundled .exes
    if parts == (1, 0, 0, 0):
        return False
    return True


def _read_pe_version(exe_path: Path) -> Optional[EngineVersion]:
    """PE VERSIONINFO resource, when present and informative."""
    try:
        import win32api  # type: ignore[import]
    except ImportError:
        return None
    try:
        info = win32api.GetFileVersionInfo(str(exe_path), "\\")
    except Exception:
        return None
    ms = int(info.get("FileVersionMS", 0))
    ls = int(info.get("FileVersionLS", 0))
    parts = (
        (ms >> 16) & 0xFFFF,
        ms & 0xFFFF,
        (ls >> 16) & 0xFFFF,
        ls & 0xFFFF,
    )
    if not _is_useful_exe_version(parts):
        return None
    version_string = ".".join(str(p) for p in parts)
    revision = parts[3] if parts[3] > 0 else None
    if revision is not None and (parts[0], parts[1]) in ((1, 13), (1, 0)):
        display = f"r{revision}"
    else:
        display = version_string
    return EngineVersion(
        display=display,
        source="exe",
        version_string=version_string,
        revision=revision,
    )


# 1.13 changelog top-line shape: `r<digits> | <author> | <date> | <n> lines`
_CHANGELOG_REV_RE = re.compile(r"^\s*r(\d{1,7})\s*\|", re.MULTILINE)

# JA2_113-Version.txt's `Game Version:` line. Modern 1.13 builds ship
# this file at the install root; the value is a git-flavored version
# like `v4-462-g2d4f62a` or `v5`. Capture everything after the colon
# until the end of the line, then strip whitespace.
_VERSION_TXT_RE = re.compile(r"^Game Version:\s*(\S.*?)\s*$", re.MULTILINE | re.IGNORECASE)


def _read_ja2_113_version_txt(install_root: Path) -> Optional[str]:
    """Pull the `Game Version: ...` value from JA2_113-Version.txt.

    Modern 1.13 distributions (post-2023ish) ship this file at the
    install root with git-style version stamps (`v5`, `v4-462-g2d4f62a`,
    etc.). Much more informative than the PE resource (which is often
    `1.0.0.1`) and survives mod-bundled re-packaging.
    """
    p = install_root / "JA2_113-Version.txt"
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(2048)
    except OSError:
        return None
    m = _VERSION_TXT_RE.search(head)
    if not m:
        return None
    value = m.group(1).strip()
    return value or None


def _read_changelog_revision(install_root: Path, filename: str) -> Optional[int]:
    """Pull the first `rNNNN` line out of a 1.13 changelog file."""
    p = install_root / filename
    if not p.is_file():
        return None
    try:
        # Read a chunk -- the first revision marker is in the first few
        # hundred bytes. Avoid reading the entire (often-multi-MB) file.
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return None
    m = _CHANGELOG_REV_RE.search(head)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def read_engine_version(install_root: Path, exe_path: Path) -> Optional[EngineVersion]:
    """Resolve an install's engine + data revision via best-available signal.

    Order (best signal first):
      1. JA2_113-Version.txt -- modern distributions ship this with
         git-flavored versions like `v4-462-g2d4f62a` or `v5`. Universal
         on post-2023ish builds.
      2. PE VERSIONINFO -- canonical when the .exe was built with a real
         resource, but mod re-packagers often leave it at `1.0.0.1`.
      3. Changelog_Source.txt -- top SVN entry, SVN-era 1.13 fallback.
      4. Changelog_Data.txt -- last resort.

    Returns None when all sources draw a blank.
    """
    vtxt = _read_ja2_113_version_txt(install_root)
    if vtxt:
        return EngineVersion(display=vtxt, source="version_txt", version_string=vtxt)
    pe = _read_pe_version(exe_path)
    if pe is not None:
        return pe
    src_rev = _read_changelog_revision(install_root, "Changelog_Source.txt")
    if src_rev is not None:
        return EngineVersion(display=f"r{src_rev}", source="changelog_source", revision=src_rev)
    data_rev = _read_changelog_revision(install_root, "Changelog_Data.txt")
    if data_rev is not None:
        return EngineVersion(display=f"r{data_rev} (data)", source="changelog_data", revision=data_rev)
    return None
