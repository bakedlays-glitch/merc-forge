"""
Auto-extract INIEditor*.xml schemas from JA2 1.13 INI files.

JA2 1.13 INIs are heavily self-documenting: each KEY = VALUE pair is preceded by
one or more `; ...` comment lines explaining purpose, valid range, and often
vanilla-vs-1.13 comparisons. This script walks the INIs, attaches each
comment block to the following key, infers datatype + range from the comment
text + the default value, and emits an XML schema compatible with the
launcher's roxmltree-based parser (mirroring the official INIEditor*.xml
format that ships for Ja2.ini, Ja2_Options.ini, and APBPConstants.ini).

Output XMLs use UTF-8 (no BOM), Description_ENG only (the launcher parser
falls back gracefully for missing language descriptions).

Usage:
  python build_ini_schemas.py <data-1.13-dir> <output-dir>

Example:
  python build_ini_schemas.py "C:\\Games\\JA2 1.13\\Data-1.13" "./generated_schemas"
"""

import re
import sys
import os
from pathlib import Path
from xml.dom.minidom import Document

# Files to extract. Skips the 3 that already have official schemas
# (Ja2_Options.INI, APBPConstants.ini) and the .bak / "- Copy" leftovers.
EXTRACT_FILES = [
    "AI.ini",
    "CTHConstants.ini",
    "Creatures_Settings.INI",
    "Helicopter_Settings.INI",
    "IntroVideos.ini",
    "Item_Settings.ini",
    "Mod_Settings.ini",
    "Morale_Settings.INI",
    "RebelCommand_Settings.ini",
    "Reputation_Settings.INI",
    "Skills_Settings.INI",
    "Taunts_Settings.INI",
]


def detect_datatype(value: str, description: str) -> str:
    """Infer the datatype from the default value + description hints."""
    v = value.strip().rstrip(",")  # trim trailing comma artifacts
    if v.upper() in ("TRUE", "FALSE"):
        return "boolean"
    try:
        int(v)
        return "numeric"
    except ValueError:
        pass
    try:
        float(v)
        return "numeric"
    except ValueError:
        pass
    # Comma-separated values → array
    if "," in v:
        return "array"
    # Quoted strings or paths → string
    return "string"


# Pre-compiled regexes for range extraction.
# Order matters: most specific first.
RANGE_PATTERNS = [
    # "(0 - 100000)" / "(1 - 255)" / "(in hours, 1 - 255)" — anywhere inside parens
    re.compile(r"\([^)]*?(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)[^)]*\)"),
    # "valid values 16 through 254"
    re.compile(r"valid values?\s+(-?\d+(?:\.\d+)?)\s+through\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    # "range 0-100" / "range: 1 - 5"
    re.compile(r"range[:\s]+(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    # "between 1 and 100"
    re.compile(r"between\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    # "from 1 to 100"
    re.compile(r"from\s+(-?\d+(?:\.\d+)?)\s+to\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    # "1 to 100" — only after a clear lead-in word
    re.compile(r"(?:value|values|range|allowed|accepts|amount)[^a-z\d-]*?(-?\d+(?:\.\d+)?)\s+to\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    # "0 - 100000" with explicit lead-in (catches unparenthesized variants)
    re.compile(r"(?:range|value|values|allowed)[^a-z\d-]*(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    # "minimum X, maximum Y" pair
    re.compile(r"min(?:imum)?[:\s]+(-?\d+(?:\.\d+)?).*?max(?:imum)?[:\s]+(-?\d+(?:\.\d+)?)", re.IGNORECASE | re.DOTALL),
]


def extract_range(description: str) -> tuple[str | None, str | None]:
    """Try to find a min-max range hint in the description. Returns (min, max) or (None, None)."""
    for pat in RANGE_PATTERNS:
        m = pat.search(description)
        if m:
            lo, hi = m.group(1), m.group(2)
            # Sanity: skip nonsense pairs (same number, or descending where it shouldn't be)
            try:
                if float(lo) > float(hi):
                    continue
            except ValueError:
                pass
            return lo, hi
    # "in percents" / "(in percents)" → 0-100
    if re.search(r"\bin\s+percent", description, re.IGNORECASE):
        return "0", "100"
    # "chance ... %" patterns
    if "%" in description and re.search(r"\bchance\b", description, re.IGNORECASE):
        return "0", "100"
    return None, None


# Match "Vanilla JA2 - 25" or "Vanilla JA2 = TRUE" or "Vanilla JA2: 30" anywhere
# in the description. Extracts the value the original 1999 Sir-Tech build used,
# so the UI can show "Vanilla default: 25" alongside the modpack's default.
VANILLA_PATTERN = re.compile(
    r"vanilla(?:[-\s]+(?:ja2|JA2))?\s*[-=:]\s*([A-Za-z0-9_.\-]+)",
    re.IGNORECASE,
)


def extract_vanilla_default(description: str) -> str | None:
    """Return the 'Vanilla JA2 - X' value if mentioned in the description, else None."""
    m = VANILLA_PATTERN.search(description)
    if not m:
        return None
    val = m.group(1).rstrip(".,;")
    # Filter obvious false positives (e.g. version strings, common stop words)
    if val.lower() in ("the", "is", "was", "version"):
        return None
    return val


def parse_ini(text: str) -> list[dict]:
    """Parse INI text into [{name, description, properties: [...]}, ...]."""
    sections = []
    current: dict | None = None
    pending_comments: list[str] = []

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            # Blank line: flush pending comments only if no key has used them yet
            # (we keep pending until a key consumes them OR a new section starts)
            continue

        if stripped.startswith(";") or stripped.startswith("#"):
            # Comment line — strip the comment char + leading whitespace
            comment = stripped.lstrip(";#").strip()
            pending_comments.append(comment)
            continue

        if stripped.startswith("[") and stripped.endswith("]"):
            # New section. Any pending comments become its description.
            name = stripped[1:-1].strip()
            current = {
                "name": name,
                "description": "\n".join(pending_comments).strip(),
                "properties": [],
            }
            sections.append(current)
            pending_comments = []
            continue

        # KEY = VALUE
        if "=" in stripped and current is not None:
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            description = "\n".join(pending_comments).strip()
            datatype = detect_datatype(value, description)
            min_v, max_v = extract_range(description) if datatype in ("numeric",) else (None, None)
            vanilla = extract_vanilla_default(description)
            current["properties"].append({
                "name": key,
                "datatype": datatype,
                "default": value,
                "min": min_v,
                "max": max_v,
                "vanilla": vanilla,
                "description": description,
            })
            pending_comments = []

    return sections


def build_xml(sections: list[dict], ini_filename: str) -> bytes:
    """Build an INIEditor-compatible XML schema. Returns UTF-8 bytes."""
    doc = Document()
    root = doc.createElement("Settings")
    root.setAttribute("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.setAttribute("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")
    doc.appendChild(root)

    desc = doc.createElement("Description_ENG")
    desc.appendChild(doc.createTextNode(
        f"Auto-extracted schema for {ini_filename}. Descriptions come from the INI "
        f"file's own ; comments. Generated by ja2-launcher/tools/build_ini_schemas.py."
    ))
    root.appendChild(desc)

    sections_el = doc.createElement("Sections")
    root.appendChild(sections_el)

    for sect in sections:
        sect_el = doc.createElement("Section")
        sect_el.setAttribute("name", sect["name"])
        if sect["description"]:
            d = doc.createElement("Description_ENG")
            d.appendChild(doc.createTextNode(sect["description"]))
            sect_el.appendChild(d)

        props_el = doc.createElement("Properties")
        sect_el.appendChild(props_el)

        for p in sect["properties"]:
            prop_el = doc.createElement("Property")
            prop_el.setAttribute("name", p["name"])
            prop_el.setAttribute("datatype", p["datatype"])
            prop_el.setAttribute("defaultvalue", p["default"])
            if p["min"] is not None:
                prop_el.setAttribute("minvalue", p["min"])
            if p["max"] is not None:
                prop_el.setAttribute("maxvalue", p["max"])
            # Custom attribute carrying the "Vanilla JA2 - X" value from the
            # INI comment. Our Rust parser reads it; the official 1.13 INIEditor
            # schemas don't carry it but our parser ignores unknown attrs there.
            if p.get("vanilla") is not None:
                prop_el.setAttribute("vanillavalue", p["vanilla"])
            if p["description"]:
                d = doc.createElement("Description_ENG")
                d.appendChild(doc.createTextNode(p["description"]))
                prop_el.appendChild(d)
            props_el.appendChild(prop_el)

        sections_el.appendChild(sect_el)

    return doc.toprettyxml(indent="    ", encoding="utf-8")


def schema_xml_name_for(ini_filename: str) -> str:
    """Map an INI filename to its schema XML filename.

    Convention: strip the .ini/.INI extension, prepend 'INIEditor', append '.xml'.
    Example: Helicopter_Settings.INI -> INIEditorHelicopter_Settings.xml
    """
    stem = Path(ini_filename).stem
    return f"INIEditor{stem}.xml"


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <data-1.13-dir> <output-dir>")
        sys.exit(1)

    data_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for filename in EXTRACT_FILES:
        path = data_dir / filename
        if not path.is_file():
            print(f"  SKIP (not found): {filename}")
            summary.append((filename, "not found", 0, 0, 0))
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        sections = parse_ini(text)

        n_props = sum(len(s["properties"]) for s in sections)
        n_with_ranges = sum(
            1 for s in sections for p in s["properties"]
            if p["min"] is not None or p["max"] is not None
        )
        n_with_descs = sum(
            1 for s in sections for p in s["properties"] if p["description"]
        )
        n_with_vanilla = sum(
            1 for s in sections for p in s["properties"] if p.get("vanilla") is not None
        )

        xml_bytes = build_xml(sections, filename)
        out_filename = schema_xml_name_for(filename)
        out_path = out_dir / out_filename
        out_path.write_bytes(xml_bytes)

        print(
            f"  OK: {filename:35} {len(sections):3} sect, {n_props:4} props "
            f"({n_with_descs:>3} desc, {n_with_ranges:>3} range, {n_with_vanilla:>3} vanilla)"
        )
        summary.append((filename, out_filename, len(sections), n_props, n_with_ranges, n_with_vanilla))

    print()
    print(f"Generated {len(summary)} schema XMLs in {out_dir}")
    total_props = sum(s[3] for s in summary if s[1] != "not found")
    total_ranges = sum(s[4] for s in summary if s[1] != "not found")
    total_vanilla = sum(s[5] for s in summary if s[1] != "not found")
    print(f"Total properties: {total_props}")
    print(f"Total with extracted range: {total_ranges} ({100*total_ranges/max(total_props,1):.0f}%)")
    print(f"Total with vanilla default: {total_vanilla} ({100*total_vanilla/max(total_props,1):.0f}%)")


if __name__ == "__main__":
    main()
