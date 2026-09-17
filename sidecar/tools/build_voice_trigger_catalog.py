"""Generate the Voice Lab dialogue-trigger catalog from authoritative headers."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


_ENUM_RE = re.compile(r"enum\s+(?P<name>\w+)\s*\{(?P<body>.*?)\};", re.DOTALL)
_ENTRY_RE = re.compile(
    r"^\s*(?P<name>[A-Z][A-Z0-9_]*)\s*(?:=\s*(?P<value>[^,]+))?\s*,?\s*(?://\s?(?P<comment>.*))?$"
)


def _meaning_from_comment(source_comment: str) -> str:
    """Drop hand-written slot labels while retaining the explanatory comment."""
    return source_comment.rsplit("//", maxsplit=1)[-1].strip()


def _enum_entries(header: str, enum_name: str) -> list[tuple[str, int, str, str]]:
    """Parse every named enum entry, evaluating numeric and prior-name aliases."""
    match = next((item for item in _ENUM_RE.finditer(header) if item["name"] == enum_name), None)
    if match is None:
        raise ValueError(f"enum {enum_name} not found")

    values: dict[str, int] = {}
    entries: list[tuple[str, int, str, str]] = []
    current = -1
    for line in match["body"].splitlines():
        entry = _ENTRY_RE.match(line)
        if entry is None:
            continue
        name = entry["name"]
        expression = entry["value"]
        if expression is None:
            current += 1
        else:
            expression = expression.strip()
            if expression in values:
                current = values[expression]
            else:
                try:
                    current = int(expression, 0)
                except ValueError as exc:
                    raise ValueError(f"unsupported enum expression {expression!r} for {name}") from exc
        values[name] = current
        source_comment = (entry["comment"] or "").strip()
        entries.append((name, current, _meaning_from_comment(source_comment), source_comment))
    return entries


def build_catalog(dialogue_header: Path, profile_header: Path) -> dict[str, object]:
    """Build JSON-ready catalog data without importing any scratch-time tooling."""
    dialogue_bytes = dialogue_header.read_bytes()
    profile_bytes = profile_header.read_bytes()
    dialogue_entries = _enum_entries(dialogue_bytes.decode("utf-8"), "DialogQuoteIDs")
    profile_entries = _enum_entries(profile_bytes.decode("utf-8"), "ProfileType")

    aliases_by_slot: dict[int, list[dict[str, str]]] = defaultdict(list)
    for name, slot, meaning, source_comment in dialogue_entries:
        aliases_by_slot[slot].append(
            {"name": name, "meaning": meaning, "source_comment": source_comment}
        )

    profile_types = {
        name.removeprefix("PROFILETYPE_"): value
        for name, value, _, _ in profile_entries
        if name != "PROFILETYPE_MAX"
    }
    return {
        "source_sha256": {
            "dialogue_header": hashlib.sha256(dialogue_bytes).hexdigest(),
            "profile_header": hashlib.sha256(profile_bytes).hexdigest(),
        },
        "profile_types": profile_types,
        "triggers": [
            {"slot": slot, "aliases": aliases_by_slot[slot]}
            for slot in sorted(aliases_by_slot)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dialogue-header", required=True, type=Path)
    parser.add_argument("--profile-header", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    payload = build_catalog(args.dialogue_header, args.profile_header)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    args.output.write_bytes(output.encode("utf-8"))


if __name__ == "__main__":
    main()
