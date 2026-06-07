"""Build the INI-editor schema JSONs for mercwizard_core/data/ini_schemas/.

One compact UTF-8 JSON per INI file, merged from four sources in
confidence order (highest wins for numeric metadata):

  1. curated   — hand-verified entries in curated_overrides.json
  2. engine    — (default, min, max) mined from the engine's own
                 ReadInteger/ReadBoolean/ReadFloat loader calls in
                 GameSettings.cpp (+ Intro.cpp, PlanFactoryLibrary.cpp),
                 named constants resolved via engine.db's `constants`
                 table. The engine clamps to exactly these bounds
                 (Utils/INIReader.cpp:114-130), so they're authoritative.
  3. official  — the three hand-authored INIEditor*.xml schemas that
                 ship with JA2 1.13 (Ja2.ini / Ja2_Options.ini /
                 APBPConstants.ini). Rich descriptions; UTF-16 XML.
  4. scraped   — descriptions/ranges regex-mined from the INI files'
                 own `;` comment blocks. Weakest source: ranges here
                 are ADVISORY ONLY and must never gate writes.

Scraper fixes vs the frozen ja2-launcher version (review 2026-06-07):
  - blank lines flush the pending comment block (kills description
    bleed: section banners no longer become the first key's docs)
  - explicit range phrases ("Values from X to Y", "range X-Y") beat
    bare parentheticals, which previously matched prose like
    "(Enemies and militia have always 2-3 traits max.)"
  - an extracted range that doesn't contain the key's own shipped
    value is dropped (hallucination guard)

Usage (from sidecar/):
  .venv/Scripts/python.exe tools/build_ini_schemas.py \
      --data-dir "C:/.../Data-1.13" \
      --root-ja2-ini "C:/.../Ja2.ini" \
      --official-dir "C:/.../ja2-launcher/shell/embedded_schemas" \
      --engine-src "C:/.../Visual Studio Root" \
      --engine-db "C:/.../Headless_Compiler/engine_graph/engine.db" \
      --out mercwizard_core/data/ini_schemas

See docs/INI_EDITOR_ENGINE_FACTS.md for the engine semantics that make
`engine` confidence authoritative.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# INI files covered, with their loader source spans for engine mining.
# (filename in Data-1.13 unless noted; Ja2.ini lives at install root)
INI_FILES = [
    "Ja2.ini",
    "Ja2_Options.ini",
    "APBPConstants.ini",
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

OFFICIAL_XML = {
    "Ja2.ini": "INIEditorJA2.xml",
    "Ja2_Options.ini": "INIEditorJA2Options.xml",
    "APBPConstants.ini": "INIEditorAPBPConstants.xml",
}

# Engine source files that construct CIniReader instances for our INIs.
ENGINE_SOURCES = [
    "Ja2/GameSettings.cpp",
    "Ja2/Intro.cpp",
    "ModularizedTacticalAI/src/PlanFactoryLibrary.cpp",
]


# ───────────────────────────── scraping (INI comments) ──────────────────────

_COMMENT = re.compile(r"^\s*[;#]")

# Explicit range phrases (checked FIRST — authoritative comment style)
_RANGE_EXPLICIT = [
    re.compile(r"values?\s+from\s+(-?\d+(?:\.\d+)?)\s+to\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"valid values?\s+(-?\d+(?:\.\d+)?)\s+(?:through|to|-)\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"range[:\s]+(-?\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"between\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE),
]
# Parenthetical ranges (checked LAST — prose-prone)
_RANGE_PAREN = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*\)")

_VANILLA = re.compile(r"vanilla(?:[-\s]+ja2)?\s*[-=:]\s*([A-Za-z0-9_.\-]+)", re.IGNORECASE)


def _detect_datatype(value: str) -> str:
    v = value.strip().rstrip(",")
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
    if "," in v:
        return "array"
    return "string"


def _extract_range(description: str, shipped: str) -> tuple[str | None, str | None]:
    """Range from comment prose. Explicit phrases win; parentheticals are
    last resort; any range not containing the shipped value is dropped."""
    candidates: list[tuple[str, str]] = []
    for pat in _RANGE_EXPLICIT:
        m = pat.search(description)
        if m:
            candidates.append((m.group(1), m.group(2)))
    m = _RANGE_PAREN.search(description)
    if m:
        candidates.append((m.group(1), m.group(2)))
    for lo, hi in candidates:
        try:
            flo, fhi = float(lo), float(hi)
        except ValueError:
            continue
        if flo > fhi:
            continue
        try:
            if not (flo <= float(shipped.strip().rstrip(",")) <= fhi):
                continue  # hallucination guard: shipped value must fit
        except ValueError:
            continue
        return lo, hi
    return None, None


def scrape_ini(path: Path) -> list[dict]:
    """Parse an INI into sections with comment-derived metadata.

    Comment blocks attach to the NEXT section/key only when contiguous
    (no blank line between) — blank lines flush the pending block.
    """
    sections: list[dict] = []
    current: dict | None = None
    pending: list[str] = []

    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            pending = []  # blank line: comments above it don't belong to what follows
            continue
        if _COMMENT.match(stripped):
            pending.append(stripped.lstrip(";#").strip())
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = {
                "name": stripped[1:-1].strip(),
                "description": "\n".join(pending).strip(),
                "properties": [],
            }
            sections.append(current)
            pending = []
            continue
        if "=" in stripped and current is not None:
            key, value = stripped.split("=", 1)
            key, value = key.strip(), value.strip()
            desc = "\n".join(pending).strip()
            datatype = _detect_datatype(value)
            lo, hi = _extract_range(desc, value) if datatype == "numeric" else (None, None)
            vm = _VANILLA.search(desc)
            vanilla = vm.group(1).rstrip(".,;") if vm else None
            if vanilla and vanilla.lower() in ("the", "is", "was", "version"):
                vanilla = None
            current["properties"].append({
                "name": key,
                "datatype": datatype,
                "default": value,          # the SHIPPED value at generation time
                "min": lo,
                "max": hi,
                "vanilla": vanilla,
                "description": desc,
                "list_values": [],
                "confidence": "scraped",
            })
            pending = []
    return sections


# ───────────────────────────── official XML schemas ─────────────────────────

def _pick_desc(node: ET.Element) -> str:
    cands = []
    for ch in node:
        if ch.tag.startswith("Description_"):
            t = (ch.text or "").strip()
            if t:
                cands.append((ch.tag, t))
    for want in ("Description_ENG", "Description_GER"):
        for tag, t in cands:
            if tag == want:
                return t
    return cands[0][1] if cands else ""


def parse_official_xml(path: Path) -> list[dict]:
    """Parse a hand-authored INIEditor*.xml.

    These ship with unreliable encodings: UTF-16 LE/BE with BOM, or
    UTF-8 bytes under a lying `encoding="utf-16"` declaration
    (INIEditorAPBPConstants.xml). Decode by BOM-sniff, strip the XML
    declaration entirely, and parse the plain unicode string."""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    text = text.lstrip("﻿")
    if text.startswith("<?xml"):
        text = text[text.index("?>") + 2:]
    root = ET.fromstring(text)
    sections: list[dict] = []
    secs = root.find("Sections")
    if secs is None:
        return sections
    for s in secs.findall("Section"):
        props: list[dict] = []
        pp = s.find("Properties")
        if pp is not None:
            for p in pp.findall("Property"):
                lv: list[str] = []
                for ch in p:
                    if ch.tag.lower() in ("listvalues", "values", "enumvalues"):
                        for v in ch:
                            if v.get("name"):
                                lv.append(v.get("name"))
                            elif (v.text or "").strip():
                                lv.append(v.text.strip())
                props.append({
                    "name": p.get("name", ""),
                    "datatype": p.get("datatype", ""),
                    "default": p.get("defaultvalue"),
                    "min": p.get("minvalue"),
                    "max": p.get("maxvalue"),
                    "interval": p.get("interval"),
                    "vanilla": p.get("vanillavalue"),
                    "description": _pick_desc(p),
                    "list_values": lv,
                    "confidence": "official",
                })
        sections.append({
            "name": s.get("name", ""),
            "description": _pick_desc(s),
            "properties": props,
        })
    return sections


# ───────────────────────────── engine mining ────────────────────────────────

# CIniReader construction — both direct filenames and macros.
_CINIREADER = re.compile(
    r"CIniReader\s+\w+\s*\(\s*(?:\"([^\"]+)\"|([A-Z_][A-Z0-9_]*))", re.MULTILINE)
_DEFINE = re.compile(r"#define\s+([A-Z_][A-Z0-9_]*)\s+\"([^\"]+)\"")

# ReadXxx("SECTION","KEY", default[, min, max]) — whitespace/newline tolerant.
_READCALL = re.compile(
    r"\.\s*Read(Integer|Boolean|Float|Double|UINT8|UINT16|UINT32|String)\s*\(\s*"
    r"\"([^\"]*)\"\s*,\s*\"([^\"]*)\"\s*,\s*([^,()]+?)\s*"
    r"(?:,\s*([^,()]+?)\s*,\s*([^,()]+?)\s*)?\)",
    re.DOTALL)


def _resolve_const(token: str, db: sqlite3.Connection | None) -> str | None:
    """Resolve a mined argument token to a plain value string."""
    t = token.strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?f?", t):
        return t.rstrip("f")
    if t.upper() in ("TRUE", "FALSE"):
        return t.upper()
    if db is not None and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t):
        row = db.execute(
            "SELECT value FROM constants WHERE name = ? LIMIT 1", (t,)
        ).fetchone()
        if row and row[0] is not None:
            v = str(row[0]).strip()
            # constants table may store hex or expressions; accept plain ints
            if re.fullmatch(r"-?\d+", v):
                return v
            if re.fullmatch(r"0[xX][0-9a-fA-F]+", v):
                return str(int(v, 16))
    return None


def mine_engine(engine_src: Path, engine_db: Path | None) -> dict[str, dict[str, dict]]:
    """Extract {ini_file: {SECTION/KEY: {engine metadata}}} from the
    loader call sites. Attribution: each ReadXxx call belongs to the most
    recently constructed CIniReader's filename (the loaders are linear)."""
    db = sqlite3.connect(str(engine_db)) if engine_db and engine_db.is_file() else None
    mined: dict[str, dict[str, dict]] = {}
    known_lower = {f.lower(): f for f in INI_FILES}

    for rel in ENGINE_SOURCES:
        src = engine_src / rel
        if not src.is_file():
            print(f"  WARN: engine source missing: {src}")
            continue
        text = src.read_text(encoding="utf-8", errors="replace")
        macros = dict(_DEFINE.findall(text))

        # Build an ordered list of (pos, ini_filename) reader-construction events
        events: list[tuple[int, str]] = []
        for m in _CINIREADER.finditer(text):
            fname = m.group(1) or macros.get(m.group(2) or "", "")
            if fname:
                events.append((m.start(), fname))
        events.sort()

        def file_at(pos: int) -> str | None:
            cur = None
            for p, fname in events:
                if p > pos:
                    break
                cur = fname
            return cur

        for m in _READCALL.finditer(text):
            ini = file_at(m.start())
            if ini is None:
                continue
            ini_canon = known_lower.get(ini.lower())
            if ini_canon is None:
                continue
            kind, section, key, dflt, lo, hi = m.groups()
            entry: dict = {"loader": f"{rel}:{text.count(chr(10), 0, m.start()) + 1}"}
            d = _resolve_const(dflt, db)
            if d is not None:
                entry["default"] = d
            if kind == "Boolean":
                entry["datatype"] = "boolean"
            elif kind == "String":
                entry["datatype"] = "string"
            else:
                entry["datatype"] = "numeric"
                if lo is not None and hi is not None:
                    lo_v, hi_v = _resolve_const(lo, db), _resolve_const(hi, db)
                    if lo_v is not None:
                        entry["min"] = lo_v
                    if hi_v is not None:
                        entry["max"] = hi_v
            mined.setdefault(ini_canon, {})[f"{section}/{key}"] = entry

    if db is not None:
        db.close()
    return mined


# ───────────────────────────── merge + emit ─────────────────────────────────

def merge_schema(
    ini_file: str,
    scraped: list[dict] | None,
    official: list[dict] | None,
    engine: dict[str, dict],
    curated: dict[str, dict],
    provenance: str,
) -> dict:
    """Merge the four sources. Base structure: official if present
    (richest descriptions), else scraped. Engine metadata upgrades
    numeric bounds/defaults; curated overrides beat everything."""
    base = official if official else (scraped or [])
    # Index scraped values so official-based schemas still carry the
    # shipped value + any comment-mined extras for keys the XML lacks.
    scraped_idx: dict[str, dict] = {}
    for sect in scraped or []:
        for p in sect["properties"]:
            scraped_idx[f"{sect['name']}/{p['name']}"] = p

    out_sections: list[dict] = []
    for sect in base:
        props_out: list[dict] = []
        for p in sect["properties"]:
            sk = f"{sect['name']}/{p['name']}"
            prop = dict(p)
            prop.setdefault("interval", None)
            # shipped value (from the INI itself at generation time)
            sc = scraped_idx.get(sk)
            prop["shipped"] = sc["default"] if sc else None
            eng = engine.get(sk)
            if eng:
                prop["engine"] = {k: v for k, v in eng.items() if k != "datatype"}
                if "min" in eng:
                    prop["min"] = eng["min"]
                if "max" in eng:
                    prop["max"] = eng["max"]
                if eng.get("datatype") and not prop.get("datatype"):
                    prop["datatype"] = eng["datatype"]
                prop["confidence"] = "engine"
            cur = curated.get(sk)
            if cur:
                for fld in ("datatype", "default", "min", "max", "description"):
                    if fld in cur:
                        prop[fld] = cur[fld]
                prop["confidence"] = "curated"
                if "note" in cur:
                    prop["curated_note"] = cur["note"]
            props_out.append(prop)
        out_sections.append({
            "name": sect["name"],
            "description": sect.get("description", ""),
            "properties": props_out,
        })

    return {
        "ini_file": ini_file,
        "provenance": provenance,
        "sections": out_sections,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--root-ja2-ini", required=True, type=Path)
    ap.add_argument("--official-dir", required=True, type=Path)
    ap.add_argument("--engine-src", required=True, type=Path)
    ap.add_argument("--engine-db", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    curated_path = args.out / "curated_overrides.json"
    curated_all: dict[str, dict[str, dict]] = {}
    if curated_path.is_file():
        curated_all = json.loads(curated_path.read_text(encoding="utf-8"))

    from datetime import date
    provenance = (
        f"build_ini_schemas.py {date.today().isoformat()} | "
        f"data={args.data_dir} | engine={args.engine_src}"
    )

    print("Mining engine loader metadata...")
    engine = mine_engine(args.engine_src, args.engine_db)
    for ini, keys in sorted(engine.items()):
        print(f"  engine: {ini:28} {len(keys):4} keys")

    index = []
    for ini_file in INI_FILES:
        # locate the INI (root Ja2.ini vs Data-1.13, case-insensitive)
        if ini_file == "Ja2.ini":
            ini_path = args.root_ja2_ini
        else:
            ini_path = args.data_dir / ini_file
            if not ini_path.is_file():
                hits = [p for p in args.data_dir.iterdir()
                        if p.is_file() and p.name.lower() == ini_file.lower()]
                ini_path = hits[0] if hits else ini_path

        scraped = scrape_ini(ini_path) if ini_path.is_file() else None
        if scraped is None:
            print(f"  WARN: INI not found, schema will be official/engine only: {ini_file}")

        official = None
        if ini_file in OFFICIAL_XML:
            xml_path = args.official_dir / OFFICIAL_XML[ini_file]
            if xml_path.is_file():
                official = parse_official_xml(xml_path)
            else:
                print(f"  WARN: official XML missing: {xml_path}")

        schema = merge_schema(
            ini_file, scraped, official,
            engine.get(ini_file, {}),
            curated_all.get(ini_file, {}),
            provenance,
        )
        n_props = sum(len(s["properties"]) for s in schema["sections"])
        n_eng = sum(1 for s in schema["sections"] for p in s["properties"]
                    if p.get("confidence") == "engine")
        n_cur = sum(1 for s in schema["sections"] for p in s["properties"]
                    if p.get("confidence") == "curated")
        out_path = args.out / (Path(ini_file).stem + ".json")
        out_path.write_text(
            json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
        print(f"  OK {ini_file:28} {len(schema['sections']):3} sect "
              f"{n_props:4} props ({n_eng} engine, {n_cur} curated) "
              f"-> {out_path.name} ({out_path.stat().st_size//1024} KB)")
        index.append({
            "ini_file": ini_file,
            "json": out_path.name,
            "sections": len(schema["sections"]),
            "properties": n_props,
        })

    (args.out / "index.json").write_text(
        json.dumps({"provenance": provenance, "schemas": index}, indent=1),
        encoding="utf-8")
    print(f"Wrote {len(index)} schemas + index.json to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
