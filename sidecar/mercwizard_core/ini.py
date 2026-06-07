"""Minimal Ja2_Options.ini reader.

JA2 1.13 INIs are loosely formatted: sections in [brackets], `KEY = VALUE`
lines, comments with `;`, `#`, or `//`. We only read the values we care
about; we don't write to Ja2_Options.ini (the wizard never modifies it).

Robust to:
- UTF-8 BOM
- Multiple comment styles
- Whitespace around `=`
- Unquoted values
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Ja2OptionsConfig:
    """The subset of Ja2_Options.ini values the wizard reads."""
    enable_new_trait_system: bool = True   # default for The Wasteland; vanilla varies
    data_dir_override: Optional[str] = None
    save_game_folder_override: Optional[str] = None


_COMMENT_RE = re.compile(r"^\s*(;|#|//)")


def _parse_ini(path: Path) -> dict[str, dict[str, str]]:
    """Generic INI parser: returns {section: {key: value}}."""
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")

    sections: dict[str, dict[str, str]] = {}
    current_section: Optional[str] = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _COMMENT_RE.match(line):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            sections.setdefault(current_section, {})
            continue
        if "=" in line and current_section is not None:
            key, _, value = line.partition("=")
            # Strip inline comments
            for marker in (";", "#", "//"):
                if marker in value:
                    value = value.split(marker, 1)[0]
            sections[current_section][key.strip()] = value.strip().strip('"').strip("'")
    return sections


def _coerce_bool(value: str, default: bool = False) -> bool:
    v = value.strip().lower()
    if v in ("true", "yes", "on", "1"):
        return True
    if v in ("false", "no", "off", "0"):
        return False
    return default


def read_options(install_root: Path) -> Ja2OptionsConfig:
    """Read Ja2_Options.ini and return the subset the wizard cares about."""
    install_root = Path(install_root)
    candidates = [
        install_root / "Ja2_Options.ini",
        install_root / "Data-1.13" / "Ja2_Options.ini",
        install_root / "Data" / "Ja2_Options.ini",
    ]
    sections: dict[str, dict[str, str]] = {}
    for path in candidates:
        if path.is_file():
            sections = _parse_ini(path)
            break

    cfg = Ja2OptionsConfig()
    # ENABLE_NEW_TRAIT_SYSTEM lives in [Strategic Gameplay Settings] historically
    for section_name, kvs in sections.items():
        for key, value in kvs.items():
            if key.upper() == "ENABLE_NEW_TRAIT_SYSTEM":
                cfg.enable_new_trait_system = _coerce_bool(value, True)
            elif key.upper() == "DATA_DIR":
                cfg.data_dir_override = value or None
            elif key.upper() == "SAVEGAMEFOLDER":
                cfg.save_game_folder_override = value or None
    return cfg
