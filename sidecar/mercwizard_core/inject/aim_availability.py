"""AIMAvailability.xml — controls which mercs appear on the AIM website.

A Type=1 merc only shows up on AIM if AIMAvailability.xml has an <AIM> entry
whose <ProfilId> points at that slot. Without it, the merc is invisible
regardless of Type (the canonical Marcus-at-slot-57 invisibility bug from
merc_integration.md).

Entry schema (vanilla file):

    <AIM_AVAILABLES>
        <AIM>
            <uiIndex>0</uiIndex>
            <description>Chosen One</description>
            <ProfilId>0</ProfilId>     <!-- single L, vanilla typo -->
            <AimBioID>0</AimBioID>     <!-- offset into AIMBIOS.EDT × 1120 -->
        </AIM>
        ...
    </AIM_AVAILABLES>

AimBioID computation:
- 0–39: AimBioID = uiIndex (linear, vanilla AIM)
- 170–177: AimBioID = uiIndex − 130 (40–47)
- 186–187: AimBioID = uiIndex − 117 (69–70)
- Scattered 215+: use the canonical lookup table below
- For brand-new slots: assign the lowest free AimBioID in [0, 199]

The canonical scattered-slot table comes from vanilla AIMAvailability.xml.
It's non-linear, so we hardcode it (see Appendix B of the plan).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from lxml import etree

from mercwizard_core.models import AimBinding


# Canonical mapping for scattered-AIM slots (from vanilla AIMAvailability.xml)
# Source: plan Appendix B; verified against fixture file.
CANONICAL_AIM_BIO_IDS: dict[int, int] = {
    # Vanilla AIM (linear)
    **{i: i for i in range(0, 40)},
    # 1.13 expanded AIM (linear within group)
    170: 40, 171: 41, 172: 42, 173: 43,
    174: 44, 175: 45, 176: 46, 177: 47,
    186: 69, 187: 70,
    # Scattered 215+ (non-linear; from vanilla file)
    215: 17,   # Buns (outlier)
    223: 62,   # Gary
    228: 68,   # Doc
    230: 48,   # Boss
    231: 49,   # Snake
    232: 50,   # Spam
    233: 51,   # Spike
    234: 52,   # Jimmy
    235: 56,   # Leech (out-of-order in vanilla)
    236: 53,   # Bob
    237: 54,   # Kelly
    238: 55,   # Vinny
    239: 57,   # Kaboom
    240: 58,   # Bud
    241: 59,   # Rusty
    242: 60,   # Needle
    243: 61,   # Screw
    245: 64,   # Mouse
    246: 65,   # Hector
    248: 66,   # Stella
    250: 67,   # Moses
    251: 63,   # Smoke
}


def _parse(path: Path) -> Optional[etree._ElementTree]:
    if not path.exists():
        return None
    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return etree.ElementTree(etree.fromstring(data, parser))


def _save(tree: etree._ElementTree, path: Path) -> None:
    # Atomic via tempfile + os.replace — a crash mid-write would otherwise
    # leave a half-written AIMAvailability.xml the engine refuses to load
    # (RUNTIME ERROR LoadExternalGameplayData at boot).
    from ._atomic_xml import save_atomic
    save_atomic(tree, path)
    # External write -> mtime changes -> _PARSE_CACHE would re-parse anyway;
    # explicit eviction is cheaper than waiting on filesystem timestamp
    # resolution to differ. Same pattern as profiles_xml.invalidate_parse_cache.
    invalidate_parse_cache()


import threading

# Parse cache — same mtime/size-keyed strategy as profiles_xml._PARSE_CACHE.
# AIMAvailability is read on every roster cell mount + every audit. The file
# is small but lxml parse + 255 AIM entries x findtext()  is non-trivial when
# hit 16+ times in parallel on the roster grid.
#
# Lock added 2026-05-25: FastAPI's threadpool runs handlers in parallel.
# A 16-cell roster mount fires 16 concurrent audits, all hitting this
# cache. Without the lock, two concurrent threads can both miss + parse +
# insert; if eviction trips between the `len()` check and `next(iter)`
# in another thread, the `del first_key` can KeyError.
_PARSE_CACHE: dict[tuple[str, int, int], dict[int, AimBinding]] = {}
_PARSE_CACHE_MAX = 4
_PARSE_CACHE_LOCK = threading.Lock()


def _cache_key(aim_xml_path: Path) -> Optional[tuple[str, int, int]]:
    try:
        st = aim_xml_path.stat()
    except OSError:
        return None
    return (str(aim_xml_path.resolve()), st.st_mtime_ns, st.st_size)


def invalidate_parse_cache() -> None:
    """Drop all cached parses. Called after a successful write."""
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE.clear()


def read_all(aim_xml_path: Path) -> dict[int, AimBinding]:
    """Parse the file and return {uiIndex: AimBinding} for every <AIM>.

    Cached by (path, mtime_ns, size). External writes bump mtime so the
    cache invalidates naturally; in-process writes call
    `invalidate_parse_cache` for immediate freshness.
    """
    key = _cache_key(aim_xml_path)
    if key is not None:
        with _PARSE_CACHE_LOCK:
            cached = _PARSE_CACHE.get(key)
        if cached is not None:
            return cached
    tree = _parse(aim_xml_path)
    if tree is None:
        return {}
    root = tree.getroot()
    result: dict[int, AimBinding] = {}
    for entry in root.findall("AIM"):
        ui = entry.findtext("uiIndex")
        desc = entry.findtext("description") or ""
        prof = entry.findtext("ProfilId")
        bio = entry.findtext("AimBioID")
        if not (ui and prof and bio):
            continue
        try:
            ui_int = int(ui.strip())
            prof_int = int(prof.strip())
            bio_int = int(bio.strip())
        except ValueError:
            continue
        try:
            result[ui_int] = AimBinding(
                uiIndex=ui_int,
                description=desc,
                ProfilId=prof_int,
                AimBioID=bio_int,
            )
        except Exception:
            # Skip malformed entries silently — they shouldn't block the roster
            continue
    if key is not None:
        with _PARSE_CACHE_LOCK:
            # Re-check inside the lock; another thread may have populated
            # the same key while we were parsing. Doesn't matter which
            # entry wins (they're byte-identical), but we want at most
            # one insert + one eviction per key.
            if key not in _PARSE_CACHE:
                if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
                    try:
                        first_key = next(iter(_PARSE_CACHE))
                        del _PARSE_CACHE[first_key]
                    except StopIteration:
                        pass
                _PARSE_CACHE[key] = result
    return result


def lookup_aim_bio_id(aim_xml_path: Path, ui_index: int) -> Optional[int]:
    """Look up an existing AimBioID for a slot. Returns None if no entry,
    OR if the entry exists but is a placeholder (`<ProfilId>-1</ProfilId>`
    `<AimBioID>-1</AimBioID>`).

    Modded AIMAvailability.xml files often ship with placeholder `<AIM>`
    rows for every slot 0-254, where unbound slots have ProfilId=-1 and
    AimBioID=-1. Those rows aren't real bindings; they're "this slot is
    reserved but empty." Treat them as None so callers allocate a fresh
    AimBioID instead of reusing -1 (which would write the bio at an
    invalid negative offset).

    Prefers the live AIMAvailability.xml (so mod-specific overrides work),
    falls back to the canonical vanilla table.
    """
    live = read_all(aim_xml_path)
    if ui_index in live:
        binding = live[ui_index]
        if binding.AimBioID >= 0 and binding.ProfilId >= 0:
            return binding.AimBioID
        # Placeholder row — treat as unbound so the caller allocates fresh.
    return CANONICAL_AIM_BIO_IDS.get(ui_index)


def compute_aim_bio_id(aim_xml_path: Path, ui_index: int) -> int:
    """Get an AimBioID for `ui_index`. Reuse if one exists; otherwise assign
    the lowest unused ID in [0, 199].

    Used when creating a fresh AIM entry for a slot the player wants to make
    AIM-bound (e.g., assigning slot 220 to AIM, which isn't in the canonical
    table).
    """
    existing = lookup_aim_bio_id(aim_xml_path, ui_index)
    if existing is not None:
        return existing

    # Assign a fresh ID, avoiding all existing real ones. Skip the -1
    # placeholders that modded files use for empty slots — those aren't
    # real allocations.
    live = read_all(aim_xml_path)
    used = {b.AimBioID for b in live.values() if b.AimBioID >= 0}
    # Also avoid the canonical IDs for unrelated slots
    for slot, bid in CANONICAL_AIM_BIO_IDS.items():
        if slot != ui_index:
            used.add(bid)
    for candidate in range(0, 200):
        if candidate not in used:
            return candidate
    raise ValueError(
        f"No free AimBioID available in [0, 199] for slot {ui_index} "
        f"({len(used)} IDs already in use). The 1120-byte AIMBIOS.EDT layout "
        "may have run out of slots."
    )


def upsert(aim_xml_path: Path, binding: AimBinding) -> None:
    """Insert or update an <AIM> entry. Creates the file if missing.

    Post-write the file is re-parsed and verified — see
    `_validate_upsert` for what's checked. Any mismatch raises
    `AIMAvailabilityWriteError`, bubbling up to the route's audit-and-
    rollback handler. Mirrors merc_availability's defensive pattern
    (bug-review finding E7 — the AIM writer's silent-accept of round-
    trip mismatches was the asymmetric gap given that AIM-website
    binding is mission-critical for hireability).
    """
    tree = _parse(aim_xml_path)
    if tree is None:
        aim_xml_path.parent.mkdir(parents=True, exist_ok=True)
        root = etree.Element("AIM_AVAILABLES")
        tree = etree.ElementTree(root)
        pre_existed = False
    else:
        root = tree.getroot()
        pre_existed = True

    target = None
    for entry in root.findall("AIM"):
        ui = entry.findtext("uiIndex")
        if ui and ui.strip() == str(binding.uiIndex):
            target = entry
            break

    inserting = target is None
    if target is None:
        target = etree.SubElement(root, "AIM")

    _set_child(target, "uiIndex", str(binding.uiIndex))
    _set_child(target, "description", binding.description)
    _set_child(target, "ProfilId", str(binding.ProfilId))
    _set_child(target, "AimBioID", str(binding.AimBioID))

    # Snapshot the row count BEFORE save so the validator can verify
    # insert / update arithmetic.
    expected_row_count = len(root.findall("AIM"))

    _save(tree, aim_xml_path)

    _validate_upsert(
        path=aim_xml_path,
        binding=binding,
        expected_row_count=expected_row_count,
        was_insert=inserting,
        pre_existed=pre_existed,
    )


class AIMAvailabilityWriteError(RuntimeError):
    """Post-upsert read-back didn't match what was supposed to be written.

    Raised when:
      - The file disappeared between save and re-read.
      - The expected uiIndex row isn't present after save (atomic-replace
        failed, write hit a different file).
      - One of the bound fields on the persisted row doesn't match the
        binding (encoding round-trip loss, concurrent editor).
      - Total row count drifted from expectation (duplicate row written,
        existing rows clobbered).

    The route's audit-and-rollback handler treats this like any other
    write failure: roll back the snapshot, surface
    AIM_AVAIL_WRITE_FAILED.
    """


def _validate_upsert(
    *,
    path: Path,
    binding: AimBinding,
    expected_row_count: int,
    was_insert: bool,
    pre_existed: bool,
) -> None:
    """Re-parse the file we just wrote and verify our row is there with
    the right values. Mirrors merc_availability._validate_upsert.

    Cheap by construction: typical AIMAvailability.xml has <100 rows and
    parses in <2ms. Worth the overhead for every upsert because the
    failure mode (silent AIM-binding corruption → merc invisible on the
    laptop) is exactly the kind of bug that eats a day of debugging
    when it does fire.
    """
    live = read_all(path)
    if binding.uiIndex not in live:
        raise AIMAvailabilityWriteError(
            f"uiIndex={binding.uiIndex} missing from {path.name} after upsert "
            f"(insert={was_insert}, file_pre_existed={pre_existed})"
        )
    row = live[binding.uiIndex]
    mismatches: list[str] = []
    if row.AimBioID != binding.AimBioID:
        mismatches.append(f"AimBioID written={binding.AimBioID} read={row.AimBioID}")
    if row.ProfilId != binding.ProfilId:
        mismatches.append(f"ProfilId written={binding.ProfilId} read={row.ProfilId}")
    if (row.description or "") != (binding.description or ""):
        mismatches.append(
            f"description written={binding.description!r} read={row.description!r}"
        )
    if mismatches:
        raise AIMAvailabilityWriteError(
            f"Round-trip mismatch on {path.name} uiIndex={binding.uiIndex}: "
            + "; ".join(mismatches)
        )
    # Total row count check — mirrors the MERC validator. Count XML
    # elements on disk directly so pre-existing duplicate uiIndex rows
    # (read_all dedupes) don't false-positive.
    persisted_tree = _parse(path)
    actual_rows = (
        len(persisted_tree.getroot().findall("AIM")) if persisted_tree is not None else 0
    )
    if actual_rows != expected_row_count:
        raise AIMAvailabilityWriteError(
            f"Row count drift on {path.name}: expected {expected_row_count}, "
            f"read {actual_rows} (insert={was_insert})"
        )


def remove(aim_xml_path: Path, ui_index: int) -> bool:
    """Remove the <AIM> entry for `ui_index`. Returns True if removed."""
    tree = _parse(aim_xml_path)
    if tree is None:
        return False
    root = tree.getroot()
    removed = False
    for entry in list(root.findall("AIM")):
        ui = entry.findtext("uiIndex")
        if ui and ui.strip() == str(ui_index):
            root.remove(entry)
            removed = True
            break
    if removed:
        _save(tree, aim_xml_path)
    return removed


def _set_child(parent: etree._Element, tag: str, value: str) -> None:
    child = parent.find(tag)
    if child is None:
        child = etree.SubElement(parent, tag)
    child.text = value
