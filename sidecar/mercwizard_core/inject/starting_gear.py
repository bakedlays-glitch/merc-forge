"""MercStartingGear.xml read/write.

Without a <MERCGEAR> block whose <mIndex> matches the merc's uiIndex, the
merc joins the game UNARMED — confirmed via Headless_Compiler's behavior and
merc_integration.md. So this writer is mandatory for every create flow.

Schema (vanilla 1.13):

    <MERCGEARLIST>
        <MERCGEAR>
            <mIndex>0</mIndex>
            <mName>Narg</mName>
            <GEARKIT>
                <mGearKitName>Standard</mGearKitName>
                <mHelmet>176</mHelmet>
                <mVest>161</mVest>
                <mLeg>0</mLeg>
                <mWeapon>2</mWeapon>
                <mBig0>71</mBig0>
                <mBig0Status>100</mBig0Status>
                <mBig0Quantity>3</mBig0Quantity>
                ...
                <mSmall0>53</mSmall0>
                ...
                <mPriceMod>0</mPriceMod>
                <mAbsolutePrice>-1</mAbsolutePrice>
            </GEARKIT>
            <!-- repeatable: more GEARKITs allowed (Combat, Stealth, ...) -->
        </MERCGEAR>
        ...
    </MERCGEARLIST>

Critical: mAbsolutePrice MUST be -1 (engine auto-calculates). 0 greys out the
gear in the AIM hiring UI. The Gear pydantic model enforces this via
validator; we double-check here as defense in depth.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from lxml import etree

from mercwizard_core.models import Gear, GearKit


# GearKit field order — preserved when writing, matches vanilla file
_GEARKIT_FIELDS = [
    "mGearKitName", "mHelmet", "mVest", "mLeg",
    "mWeapon",
    "mBig0", "mBig0Status", "mBig0Quantity",
    "mBig1", "mBig1Status", "mBig1Quantity",
    "mBig2", "mBig2Status", "mBig2Quantity",
    "mBig3", "mBig3Status", "mBig3Quantity",
    "mSmall0", "mSmall1", "mSmall2", "mSmall3",
    "mSmall4", "mSmall5", "mSmall6", "mSmall7",
    "mPriceMod", "mAbsolutePrice",
]


def _parse(path: Path) -> Optional[etree._ElementTree]:
    if not path.exists():
        return None
    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return etree.ElementTree(etree.fromstring(data, parser))


def _save(tree: etree._ElementTree, path: Path) -> None:
    # Atomic write — see aim_availability._save for the rationale.
    from ._atomic_xml import save_atomic
    save_atomic(tree, path)
    invalidate_parse_cache()


import threading

# Parse cache for the WHOLE file — keyed on (path, mtime_ns, size). Same
# strategy as profiles_xml / aim_availability / merc_availability. Stocked by
# read_slot's first hit; subsequent reads (one per roster cell, one per
# audit) bypass the lxml parse.
#
# The cache stores the full {ui_index: Gear} map so read_slot is O(1)
# after the first call. This is the file the audit walks per-merc — without
# the cache, a 16-cell roster mount re-parses MercStartingGear.xml 16 times.
#
# Lock added 2026-05-25 — see aim_availability for the rationale (FastAPI
# threadpool fan-out + race on FIFO eviction).
_PARSE_CACHE: dict[tuple[str, int, int], dict[int, Gear]] = {}
_PARSE_CACHE_MAX = 4
_PARSE_CACHE_LOCK = threading.Lock()


def _cache_key(gear_xml_path: Path) -> Optional[tuple[str, int, int]]:
    try:
        st = gear_xml_path.stat()
    except OSError:
        return None
    return (str(gear_xml_path.resolve()), st.st_mtime_ns, st.st_size)


def invalidate_parse_cache() -> None:
    """Drop all cached parses. Called after a successful write."""
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE.clear()


def _read_all_gear(gear_xml_path: Path) -> dict[int, Gear]:
    """Parse the whole file into {ui_index: Gear}. Cached. Internal — most
    callers use `read_slot` instead."""
    key = _cache_key(gear_xml_path)
    if key is not None:
        with _PARSE_CACHE_LOCK:
            cached = _PARSE_CACHE.get(key)
        if cached is not None:
            return cached
    tree = _parse(gear_xml_path)
    if tree is None:
        return {}
    root = tree.getroot()
    out: dict[int, Gear] = {}
    for entry in root.findall("MERCGEAR"):
        idx_text = entry.findtext("mIndex")
        if idx_text is None:
            continue
        try:
            ui = int(idx_text.strip())
        except ValueError:
            continue
        try:
            out[ui] = _parse_mercgear(entry, ui)
        except Exception:
            continue
    if key is not None:
        with _PARSE_CACHE_LOCK:
            if key not in _PARSE_CACHE:
                if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
                    try:
                        first_key = next(iter(_PARSE_CACHE))
                        del _PARSE_CACHE[first_key]
                    except StopIteration:
                        pass
                _PARSE_CACHE[key] = out
    return out


def read_slot(gear_xml_path: Path, ui_index: int) -> Optional[Gear]:
    """Read one merc's gear block. Returns None if not present.

    Backed by the per-file parse cache (`_PARSE_CACHE`) so repeated
    lookups during a roster grid mount don't re-parse the file.
    """
    return _read_all_gear(gear_xml_path).get(ui_index)


def _parse_mercgear(entry: etree._Element, ui_index: int) -> Gear:
    name = entry.findtext("mName") or ""
    kits: list[GearKit] = []
    for kit_elem in entry.findall("GEARKIT"):
        fields: dict[str, str] = {}
        for f in _GEARKIT_FIELDS:
            text = kit_elem.findtext(f)
            if text is not None:
                fields[f] = text.strip()
        # Coerce numerics; leave the kit name as string
        kit_kwargs: dict[str, object] = {}
        for k, v in fields.items():
            if k == "mGearKitName":
                kit_kwargs[k] = v
            else:
                try:
                    kit_kwargs[k] = int(v)
                except ValueError:
                    pass
        try:
            kits.append(GearKit(**kit_kwargs))
        except Exception:
            # Tolerate malformed kits — surface a warning later via audit
            continue
    if not kits:
        # Every kit in the source XML failed to parse. Surface a marker
        # kit so reads succeed (roster can still display the merc) but
        # writes refuse — see `upsert` below. Pre-fix the silent
        # substitution committed an empty default kit back on Edit →
        # save, permanently overwriting the malformed-but-recoverable
        # original. Sweep bug-review finding.
        kits = [GearKit(mGearKitName=PARSE_FAILED_MARKER)]
    return Gear(mIndex=ui_index, mName=name, kits=kits)


# Marker used by parse to flag "all kits in the source block failed to
# parse" — the writer refuses to commit any kit carrying this name back
# to disk, so the original (malformed-but-recoverable) bytes stay
# readable for diagnostic.
PARSE_FAILED_MARKER = "__MERCWIZARD_PARSE_FAILED__"


def upsert(gear_xml_path: Path, gear: Gear) -> None:
    """Insert or update the <MERCGEAR> block for `gear.mIndex`.

    Pydantic's GearKit.mAbsolutePrice validator already enforces -1, but we
    re-check here for belt-and-braces.

    Refuses to write any kit flagged with `PARSE_FAILED_MARKER` (the
    `_parse_kits` substitute used when the on-disk XML failed to parse).
    Without this guard, a roster read → Edit page → Save round-trip
    would silently overwrite the malformed-but-recoverable source bytes
    with the wizard's empty default kit. Surface ValueError instead so
    the user knows the gear file needs manual repair. Sweep bug-review
    finding.
    """
    for kit in gear.kits:
        if kit.mGearKitName == PARSE_FAILED_MARKER:
            raise ValueError(
                f"Refusing to write gear for slot {gear.mIndex}: the "
                "on-disk MercStartingGear.xml block failed to parse, "
                "and writing the wizard's empty default would clobber "
                "the malformed-but-recoverable original. Repair the XML "
                "manually and reload."
            )
        if kit.mAbsolutePrice != -1:
            raise ValueError(
                f"mAbsolutePrice must be -1 (got {kit.mAbsolutePrice}). "
                "0 greys out gear in the AIM hiring UI."
            )

    tree = _parse(gear_xml_path)
    if tree is None:
        gear_xml_path.parent.mkdir(parents=True, exist_ok=True)
        root = etree.Element("MERCGEARLIST")
        tree = etree.ElementTree(root)
    else:
        root = tree.getroot()

    target = None
    for entry in root.findall("MERCGEAR"):
        idx_text = entry.findtext("mIndex")
        if idx_text and idx_text.strip() == str(gear.mIndex):
            target = entry
            break

    if target is None:
        target = etree.SubElement(root, "MERCGEAR")

    # Preserve any mod-specific children we don't know about — earlier code
    # wiped ALL children and re-emitted just the wizard's known set, which
    # dropped fields like AIMNAS's custom-kit metadata on Edit/Move. We
    # only strip the children that the upsert is about to re-emit (the
    # mIndex/mName scalars + every GEARKIT block), leaving anything else
    # in place.
    known_tags = {"mIndex", "mName", "GEARKIT"}
    for child in list(target):
        if child.tag in known_tags:
            target.remove(child)

    _set_child(target, "mIndex", str(gear.mIndex))
    _set_child(target, "mName", gear.mName or "")
    for kit in gear.kits:
        kit_elem = etree.SubElement(target, "GEARKIT")
        kit_dict = kit.model_dump()
        for field_name in _GEARKIT_FIELDS:
            _set_child(kit_elem, field_name, str(kit_dict[field_name]))

    _save(tree, gear_xml_path)


def clear_slot(gear_xml_path: Path, ui_index: int) -> bool:
    """Remove the <MERCGEAR> block for `ui_index`. Returns True if removed."""
    tree = _parse(gear_xml_path)
    if tree is None:
        return False
    root = tree.getroot()
    removed = False
    for entry in list(root.findall("MERCGEAR")):
        idx_text = entry.findtext("mIndex")
        if idx_text and idx_text.strip() == str(ui_index):
            root.remove(entry)
            removed = True
            break
    if removed:
        _save(tree, gear_xml_path)
    return removed


def _set_child(parent: etree._Element, tag: str, value: str) -> None:
    child = parent.find(tag)
    if child is None:
        child = etree.SubElement(parent, tag)
    child.text = value
