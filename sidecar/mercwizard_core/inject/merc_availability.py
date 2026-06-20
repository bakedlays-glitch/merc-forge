"""MercAvailability.xml — controls which mercs appear on Speck's M.E.R.C. website.

The M.E.R.C. equivalent of `aim_availability.py`. A Type=2 (MERC) merc only
shows up on Speck's site if MercAvailability.xml has a <MERC> entry whose
<ProfilId> points at the merc's slot. Without it, the merc is invisible
regardless of Type — same trap as the Marcus-at-slot-57 bug for AIM.

Entry schema (Vengeance MercAvailability.xml):

    <MERC_AVAILABLES>
        <MERC>
            <uiIndex>12</uiIndex>               <!-- display position in the M.E.R.C. roster -->
            <Name>Wahan</Name>
            <Drunk>0</Drunk>
            <uiAlternateIndex>-1</uiAlternateIndex>
            <StartMercsAvailable>0</StartMercsAvailable>
            <NewMercsAvailable>0</NewMercsAvailable>
            <MercBioID>42</MercBioID>           <!-- offset into MERCBIOS.EDT × 1120 -->
            <ProfilId>198</ProfilId>            <!-- MercProfiles.xml slot pointer -->
            <usMoneyPaid>100</usMoneyPaid>
            <usDay>2</usDay>
        </MERC>
        ...
    </MERC_AVAILABLES>

MercBioID computation:
- 40–50 (vanilla MERC): MercBioID = uiIndex − 40 (0–10), per long-standing convention.
- 178–199, 244, 247, 249, 252–253 (expansion): no canonical table — each mod
  allocates fresh IDs ad-hoc. We read existing rows when present, else assign
  the lowest unused MercBioID.

NOTE on the routing fix: MercWizard 1.x routed expansion MERC bios to
`MercEdt/<n>.EDT`. The engine doesn't read those files for Type=2 slots; it
reads MERCBIOS.EDT at `MercBioID × 1120` for every MERC bio. This module
gives us the same `lookup/compute/upsert` surface the AIM path uses so the
EDT writer can route correctly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from lxml import etree

from mercwizard_core.models import MercBinding


# Canonical MercBioID for vanilla MERC slots (Biff..Larry, 40..50).
# Mods extend MERCBIOS.EDT past record 10 to make room for expansion rows;
# vanilla's MERCBIOS.EDT is exactly 11 × 1120 = 12,320 bytes.
CANONICAL_MERC_BIO_IDS: dict[int, int] = {
    slot: slot - 40 for slot in range(40, 51)
}


def _parse(path: Path) -> Optional[etree._ElementTree]:
    if not path.exists():
        return None
    from ._atomic_xml import parse_tolerant
    return parse_tolerant(path)


def _save(tree: etree._ElementTree, path: Path) -> None:
    # Atomic write — see aim_availability._save for the rationale.
    from ._atomic_xml import save_atomic_preserving
    save_atomic_preserving(tree, path)
    invalidate_parse_cache()


import threading

# Parse cache — same mtime/size-keyed strategy as profiles_xml + aim_availability.
# Hit by every roster cell and every audit; per-call lxml parse is ~50-150 ms
# on Vengeance's 52-row file when the user mounts the 16-cell roster grid.
#
# Lock added 2026-05-25 — see aim_availability for the rationale (FastAPI
# threadpool fan-out + race on FIFO eviction).
_PARSE_CACHE: dict[tuple[str, int, int], dict[int, MercBinding]] = {}
_PARSE_CACHE_MAX = 4
_PARSE_CACHE_LOCK = threading.Lock()


def _cache_key(merc_xml_path: Path) -> Optional[tuple[str, int, int]]:
    try:
        st = merc_xml_path.stat()
    except OSError:
        return None
    return (str(merc_xml_path.resolve()), st.st_mtime_ns, st.st_size)


def invalidate_parse_cache() -> None:
    """Drop all cached parses. Called after a successful write."""
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE.clear()


def read_all(merc_xml_path: Optional[Path]) -> dict[int, MercBinding]:
    """Parse the file and return {ProfilId: MercBinding} for every <MERC>.

    Unlike AIM (which is keyed by uiIndex), MERC rows are keyed by ProfilId
    in our return map — that's the field every caller cares about ("what's
    the MercBioID for slot N?"). uiIndex here is just the M.E.R.C. UI's
    display order, not a profile slot.

    Cached by (path, mtime_ns, size). See _PARSE_CACHE.
    """
    if merc_xml_path is None:
        return {}
    key = _cache_key(merc_xml_path)
    if key is not None:
        with _PARSE_CACHE_LOCK:
            cached = _PARSE_CACHE.get(key)
        if cached is not None:
            return cached
    tree = _parse(merc_xml_path)
    if tree is None:
        return {}
    root = tree.getroot()
    result: dict[int, MercBinding] = {}
    for entry in root.findall("MERC"):
        prof = entry.findtext("ProfilId")
        if prof is None:
            continue
        try:
            prof_int = int(prof.strip())
        except ValueError:
            continue
        kwargs: dict[str, object] = {"ProfilId": prof_int}
        for tag, default in (
            ("uiIndex", None),
            ("Name", ""),
            ("Drunk", 0),
            ("uiAlternateIndex", -1),
            ("StartMercsAvailable", 1),
            ("NewMercsAvailable", 0),
            ("MercBioID", None),
            ("usMoneyPaid", 0),
            ("usDay", 0),
        ):
            text = entry.findtext(tag)
            if text is None:
                if default is None:
                    kwargs = {}  # missing required field — skip the row
                    break
                kwargs[tag] = default
                continue
            if tag == "Name":
                kwargs[tag] = text
            else:
                try:
                    kwargs[tag] = int(text.strip())
                except ValueError:
                    kwargs = {}
                    break
        if not kwargs:
            continue
        try:
            result[prof_int] = MercBinding(**kwargs)  # type: ignore[arg-type]
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
                _PARSE_CACHE[key] = result
    return result


def lookup_merc_bio_id(merc_xml_path: Optional[Path], profil_id: int) -> Optional[int]:
    """Look up an existing MercBioID for `profil_id`. Returns None if no
    real row exists.

    Placeholder handling: a row with `<MercBioID>-1</MercBioID>`,
    `<ProfilId>-1</ProfilId>`, or `<ProfilId>0</ProfilId>` is treated as a
    reserved-but-unbound slot, not a real binding. Caller should
    allocate a fresh ID instead of reusing -1 (which would write the bio
    at a negative offset and corrupt MERCBIOS.EDT). ProfilId=0 is a
    placeholder convention some mods use; slot_picker._merc_row_present
    treats it the same way, and the two presence checks must agree or
    the wizard's slot picker and EDT writer disagree on whether a slot
    is empty (bug-review finding E6).

    Prefers live MercAvailability.xml; falls back to the canonical 40–50
    table for vanilla MERC slots.
    """
    live = read_all(merc_xml_path)
    if profil_id in live:
        binding = live[profil_id]
        if binding.MercBioID >= 0 and binding.ProfilId > 0:
            return binding.MercBioID
        # Placeholder row — treat as unbound so caller allocates fresh.
    return CANONICAL_MERC_BIO_IDS.get(profil_id)


def compute_merc_bio_id(merc_xml_path: Optional[Path], profil_id: int) -> int:
    """Get a MercBioID for `profil_id`. Reuse if one exists; otherwise assign
    the lowest unused ID in [0, 199].

    Used when creating a fresh MERC entry for a slot the player wants to make
    M.E.R.C.-bound (e.g., assigning Eskimo to slot 198, which Vengeance ships
    with MercBioID=42; a fresh slot has none).
    """
    existing = lookup_merc_bio_id(merc_xml_path, profil_id)
    if existing is not None:
        return existing

    used: set[int] = set()
    live = read_all(merc_xml_path)
    for binding in live.values():
        if binding.MercBioID >= 0:
            used.add(binding.MercBioID)
    # Also reserve the canonical 0–10 vanilla slots so a fresh expansion
    # row doesn't collide with Biff/Larry/etc., even if the live file is
    # missing those rows on a vanilla install.
    for slot, bid in CANONICAL_MERC_BIO_IDS.items():
        if slot != profil_id:
            used.add(bid)

    for candidate in range(0, 200):
        if candidate not in used:
            return candidate
    raise ValueError(
        f"No free MercBioID available in [0, 199] for slot {profil_id} "
        f"({len(used)} IDs already in use). The 1120-byte MERCBIOS.EDT layout "
        "may have run out of slots."
    )


# Always-write fields the engine requires — these are non-negotiable
# even on schemas that strip everything else. ProfilId binds the row to a
# slot; MercBioID drives MERCBIOS.EDT routing; uiIndex is the M.E.R.C.
# UI display order; Name is what the user sees on the laptop. Without
# any of these the row is unusable.
_ALWAYS_WRITE_FIELDS = {"ProfilId", "MercBioID", "uiIndex", "Name"}


def detect_schema(merc_xml_path: Optional[Path]) -> Optional[set[str]]:
    """Return the union of child-tag names across every existing <MERC> row.

    Used to make the writer schema-aware: never adds fields that the
    install's existing rows don't carry (e.g. some stripped mods omit
    `usMoneyPaid` / `usDay`; pre-STOMP variants of MercAvailability are
    in the wild). Mirrors profiles_xml.detect_schema.

    Returns None when the file is missing or has no rows — caller should
    fall back to the full writable set.
    """
    if merc_xml_path is None or not merc_xml_path.exists():
        return None
    tree = _parse(merc_xml_path)
    if tree is None:
        return None
    root = tree.getroot()
    fields: set[str] = set()
    for entry in root.findall("MERC"):
        for child in entry:
            if isinstance(child.tag, str):
                fields.add(child.tag)
    return fields if fields else None


def upsert(merc_xml_path: Path, binding: MercBinding) -> None:
    """Insert or update a <MERC> entry. Creates the file if missing.

    The row is matched by ProfilId, not uiIndex — uiIndex is just the
    M.E.R.C. UI's display order and can collide across rows on partial mods.

    Post-write the file is re-parsed and verified — see `_validate_upsert`
    for what's checked. Any mismatch raises `MercAvailabilityWriteError`,
    bubbling up to the route's audit-and-rollback handler. Bug-review #96.

    Schema-aware: detects which fields the install's existing rows
    carry and only writes those (plus the always-write core
    `_ALWAYS_WRITE_FIELDS`). On a fresh file with no precedent, writes
    the full canonical set. Mirrors profiles_xml._write_block; without
    this, a stripped mod that ships without `usMoneyPaid` / `usDay`
    columns would silently gain them on every upsert. Bug-review
    finding C8.
    """
    tree = _parse(merc_xml_path)
    if tree is None:
        merc_xml_path.parent.mkdir(parents=True, exist_ok=True)
        root = etree.Element("MERC_AVAILABLES")
        tree = etree.ElementTree(root)
        pre_existed = False
        allowed: Optional[set[str]] = None  # fresh file → full canonical set
    else:
        root = tree.getroot()
        pre_existed = True
        allowed = detect_schema(merc_xml_path)

    target = None
    for entry in root.findall("MERC"):
        prof = entry.findtext("ProfilId")
        if prof and prof.strip() == str(binding.ProfilId):
            target = entry
            break

    inserting = target is None
    if target is None:
        target = etree.SubElement(root, "MERC")

    # _write_pair gates each field on the detected schema (allowed) so a
    # mod that doesn't ship `usMoneyPaid` / `usDay` columns doesn't gain
    # them silently on first upsert.
    def _write_pair(tag: str, value: str) -> None:
        if allowed is not None and tag not in allowed and tag not in _ALWAYS_WRITE_FIELDS:
            return
        _set_child(target, tag, value)

    _write_pair("uiIndex", str(binding.uiIndex))
    _write_pair("Name", binding.Name)
    _write_pair("Drunk", str(binding.Drunk))
    _write_pair("uiAlternateIndex", str(binding.uiAlternateIndex))
    _write_pair("StartMercsAvailable", str(binding.StartMercsAvailable))
    _write_pair("NewMercsAvailable", str(binding.NewMercsAvailable))
    _write_pair("MercBioID", str(binding.MercBioID))
    _write_pair("ProfilId", str(binding.ProfilId))
    _write_pair("usMoneyPaid", str(binding.usMoneyPaid))
    _write_pair("usDay", str(binding.usDay))

    # Snapshot the row count BEFORE save so the validator can verify
    # insert / update arithmetic. `len(root)` reflects the in-memory tree
    # which is what we're about to persist.
    expected_row_count = len(root.findall("MERC"))

    _save(tree, merc_xml_path)

    _validate_upsert(
        path=merc_xml_path,
        binding=binding,
        expected_row_count=expected_row_count,
        was_insert=inserting,
        pre_existed=pre_existed,
    )


class MercAvailabilityWriteError(RuntimeError):
    """Post-upsert read-back didn't match what was supposed to be written.

    Raised when:
      - The file disappeared between save and re-read (disk yanked, AV
        quarantine, etc.).
      - The expected ProfilId row isn't present after save (write hit a
        different file, atomic-replace failed, etc.).
      - One of the bound fields on the persisted row doesn't match the
        binding we tried to write (corruption, concurrent editor, encoding
        round-trip loss).
      - Total row count drifted from expectation (duplicate row written,
        existing rows clobbered).

    The route's audit-and-rollback handler treats this like any other
    write failure: roll back the snapshot, surface MERC_AVAIL_WRITE_FAILED.
    """


def _validate_upsert(
    *,
    path: Path,
    binding: MercBinding,
    expected_row_count: int,
    was_insert: bool,
    pre_existed: bool,
) -> None:
    """Re-parse the file we just wrote and verify our row is there with
    the right values. Catches the Flugente engine-side double-write
    quirk (community-reported) AND our own write-path bugs.

    Cheap by construction: typical MercAvailability.xml has <100 rows and
    parses in <2ms. Worth the overhead for every upsert because the
    failure mode (silent corruption) is exactly the kind of bug that
    eats a day of debugging when it does fire.
    """
    live = read_all(path)
    if binding.ProfilId not in live:
        raise MercAvailabilityWriteError(
            f"ProfilId={binding.ProfilId} missing from {path.name} after upsert "
            f"(insert={was_insert}, file_pre_existed={pre_existed})"
        )
    row = live[binding.ProfilId]
    mismatches: list[str] = []
    if row.MercBioID != binding.MercBioID:
        mismatches.append(f"MercBioID written={binding.MercBioID} read={row.MercBioID}")
    if row.uiIndex != binding.uiIndex:
        mismatches.append(f"uiIndex written={binding.uiIndex} read={row.uiIndex}")
    if (row.Name or "") != (binding.Name or ""):
        mismatches.append(f"Name written={binding.Name!r} read={row.Name!r}")
    if mismatches:
        raise MercAvailabilityWriteError(
            f"Round-trip mismatch on {path.name} ProfilId={binding.ProfilId}: "
            + "; ".join(mismatches)
        )
    # Total row count check — catches the Flugente double-write quirk
    # where a second copy of the row appears under a different uiIndex.
    #
    # NB: `read_all` returns a dict keyed by ProfilId and dedupes (last
    # writer wins), so comparing `len(live)` to `expected_row_count`
    # would mis-fire on files that legitimately ship pre-existing
    # duplicate ProfilIds (some Vengeance-derived MercAvailability.xml
    # snapshots have these in the wild). Count XML <MERC> elements from
    # the on-disk file directly so the comparison is apples-to-apples
    # with `expected_row_count = len(root.findall("MERC"))` taken
    # pre-save. Bug-review finding A2.
    persisted_tree = _parse(path)
    actual_rows = (
        len(persisted_tree.getroot().findall("MERC")) if persisted_tree is not None else 0
    )
    if actual_rows != expected_row_count:
        raise MercAvailabilityWriteError(
            f"Row count drift on {path.name}: expected {expected_row_count}, "
            f"read {actual_rows} (insert={was_insert})"
        )


def remove(merc_xml_path: Optional[Path], profil_id: int) -> bool:
    """Remove the <MERC> entry for `profil_id`. Returns True if removed."""
    if merc_xml_path is None:
        return False
    tree = _parse(merc_xml_path)
    if tree is None:
        return False
    root = tree.getroot()
    removed = False
    for entry in list(root.findall("MERC")):
        prof = entry.findtext("ProfilId")
        if prof and prof.strip() == str(profil_id):
            root.remove(entry)
            removed = True
            break
    if removed:
        _save(tree, merc_xml_path)
    return removed


def compute_ui_index(merc_xml_path: Optional[Path]) -> int:
    """Allocate the next free uiIndex (display order) for a new <MERC> row.

    The engine doesn't care about uiIndex collisions per se — it just sorts
    the M.E.R.C. UI roster by it — but giving each row a unique value keeps
    the website's display sensible. Use max(existing)+1, defaulting to 0
    for an empty file.
    """
    live = read_all(merc_xml_path)
    if not live:
        return 0
    # Filter to assigned uiIndex values BEFORE max() — modded files often
    # seed every slot 0-254 with `<uiIndex>-1</uiIndex>` placeholders, so
    # `live` is non-empty but the filtered iterator is. Plain `max()` of
    # an empty iterator raises ValueError, which the create handler
    # propagates as 500 INTERNAL_ERROR mid-save. Bug-review finding A3.
    assigned = [b.uiIndex for b in live.values() if b.uiIndex >= 0]
    if not assigned:
        return 0
    return 1 + max(assigned)


def _set_child(parent: etree._Element, tag: str, value: str) -> None:
    child = parent.find(tag)
    if child is None:
        child = etree.SubElement(parent, tag)
    child.text = value
