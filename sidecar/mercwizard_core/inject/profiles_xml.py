"""MercProfiles.xml read/write/upsert/clear using lxml.

The existing MercWizard 1.x uses regex munging to inject profile blocks
(server.py:369-391). That approach is fragile against whitespace, comments,
attribute reorderings, and mod-specific extensions. We use lxml instead:
- Parse the whole file
- Locate or create the <PROFILE> child whose <uiIndex> matches
- Replace the field set we know about; leave unknown fields untouched
  (so mod-specific custom XML fields are preserved on upsert)
- Pretty-print preserving the file's existing indentation style

File format (from source_xml_schemas.md):
    <PROFILE_LIST>
        <PROFILE>
            <uiIndex>0</uiIndex>
            <zName>...</zName>
            ...
        </PROFILE>
        ...
    </PROFILE_LIST>

No XML declaration. No namespace. No BOM in vanilla files (we handle BOM
just in case). Whitespace-indented. Engine ignores unknown fields, so we
preserve any non-standard fields the mod author added.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from lxml import etree

from mercwizard_core.models import Merc


# Fields we own — everything in Merc except portrait/animation paths
# (those are STI-bound, not XML-bound) and the EDT text (those go to EDTs).
# This list is the canonical write-set. Anything not in this list is preserved
# verbatim on upsert.
_MERC_WRITABLE_FIELDS = [
    "uiIndex", "ubFaceIndex", "Type",
    "zName", "zNickname",
    "bSex", "ubBodyType", "uiBodyTypeSubFlags", "usVoiceIndex",
    "bRace", "bNationality",
    "usEyesX", "usEyesY", "usMouthX", "usMouthY",
    "uiEyeDelay", "uiMouthDelay", "uiBlinkFrequency", "uiExpressionFrequency",
    "PANTS", "VEST", "SKIN", "HAIR",
    "bLifeMax", "bLife",
    "bStrength", "bAgility", "bDexterity", "bWisdom", "bExpLevel",
    "bMarksmanship", "bExplosive", "bLeadership", "bMedical", "bMechanical",
    "bEvolution", "fRegresses",
    "GrowthModifierLife", "GrowthModifierStrength", "GrowthModifierAgility",
    "GrowthModifierDexterity", "GrowthModifierWisdom",
    "GrowthModifierMarksmanship", "GrowthModifierExplosive",
    "GrowthModifierLeadership", "GrowthModifierMedical",
    "GrowthModifierMechanical", "GrowthModifierExpLevel",
    "bOldSkillTrait", "bOldSkillTrait2",
    "bNewSkillTrait1", "bNewSkillTrait2", "bNewSkillTrait3", "bNewSkillTrait4",
    "bNewSkillTrait5", "bNewSkillTrait6", "bNewSkillTrait7", "bNewSkillTrait8",
    "bNewSkillTrait9", "bNewSkillTrait10", "bNewSkillTrait11", "bNewSkillTrait12",
    "bNewSkillTrait13", "bNewSkillTrait14", "bNewSkillTrait15", "bNewSkillTrait16",
    "bNewSkillTrait17", "bNewSkillTrait18", "bNewSkillTrait19", "bNewSkillTrait20",
    "bNewSkillTrait21", "bNewSkillTrait22", "bNewSkillTrait23", "bNewSkillTrait24",
    "bNewSkillTrait25", "bNewSkillTrait26", "bNewSkillTrait27", "bNewSkillTrait28",
    "bNewSkillTrait29", "bNewSkillTrait30",
    "usBackground",
    "bAttitude", "bCharacterTrait", "bDisability",
    "ubNeedForSleep", "bReputationTolerance", "bDeathRate",
    "bAppearance", "bAppearanceCareLevel",
    "bRefinement", "bRefinementCareLevel",
    "bHatedNationality", "bHatedNationalityCareLevel",
    "bRacist", "bSexist", "fGoodGuy",
    "bBuddy1", "bBuddy2", "bBuddy3", "bBuddy4", "bBuddy5",
    "bHated1", "bHatedTime1", "bHated2", "bHatedTime2",
    "bHated3", "bHatedTime3", "bHated4", "bHatedTime4",
    "bHated5", "bHatedTime5",
    "bLearnToLike", "bLearnToLikeTime",
    "bLearnToHate", "bLearnToHateTime",
    "sSalary", "uiWeeklySalary", "uiBiWeeklySalary",
    "bMedicalDeposit", "sMedicalDepositAmount", "usOptionalGearCost",
    "bArmourAttractiveness", "bMainGunAttractiveness",
    "usApproachFactorFriendly", "usApproachFactorDirect",
    "usApproachFactorThreaten", "usApproachFactorRecruit",
    "sSectorX", "sSectorY", "sSectorZ",
    "ubCivilianGroup", "bTown", "bTownAttachment",
]


# ── Model-field ⇄ on-disk-XML-tag overrides ─────────────────────────────────
# For the 11 growth-modifier fields the engine's XML tag carries a "b" prefix
# that the Merc model + the TS schema drop for readability. Verified in source:
#   - parser accepts <bGrowthModifier*>  (Visual Studio Root/Tactical/
#     XML_Profiles.cpp:105-115)
#   - struct fields are b-prefixed        (Tactical/soldier profile type.h:1044-1054)
# Every OTHER field uses its model name verbatim as the tag. These two maps are
# the single source of truth for the divergence: the writer maps field→tag, and
# `normalize_profile_tags` maps tag→field on read. Without this, the engine
# never reads the values we write (prefix-less tag is ignored at game load) and
# the schema-aware writer silently SKIPS growth edits on installs that already
# carry the engine's b-prefixed tags (e.g. AIMNAS).
_GROWTH_MODIFIER_FIELDS = (
    "GrowthModifierLife", "GrowthModifierStrength", "GrowthModifierAgility",
    "GrowthModifierDexterity", "GrowthModifierWisdom", "GrowthModifierMarksmanship",
    "GrowthModifierExplosive", "GrowthModifierLeadership", "GrowthModifierMedical",
    "GrowthModifierMechanical", "GrowthModifierExpLevel",
)
_FIELD_TO_XML_TAG: dict[str, str] = {f: "b" + f for f in _GROWTH_MODIFIER_FIELDS}
_XML_TAG_TO_FIELD: dict[str, str] = {tag: field for field, tag in _FIELD_TO_XML_TAG.items()}


def normalize_profile_tags(raw: dict[str, str]) -> dict[str, str]:
    """Translate on-disk XML tags to Merc model field names.

    The engine stores the 11 growth modifiers as <bGrowthModifier*>; the Merc
    model and the TS schema use the prefix-less GrowthModifier* names. Any
    caller that feeds a raw profile dict (from `read_slot` / `read_all_slots`)
    into the Merc model or returns it to the frontend MUST run it through here
    first — otherwise the b-prefixed values silently vanish (the model field
    stays at its 0 default and the editor shows 0 even when the install has
    real values).

    Returns a NEW dict; non-divergent keys pass through untouched. If a profile
    somehow carries BOTH spellings (e.g. a pre-fix MercWizard wrote the
    prefix-less tag into a file that already had the engine's b-tag), the
    engine-authoritative b-prefixed value wins.
    """
    out = dict(raw)
    for xml_tag, field_name in _XML_TAG_TO_FIELD.items():
        if xml_tag in out:
            out[field_name] = out.pop(xml_tag)
    return out


def _parse_with_bom_tolerance(path: Path) -> etree._ElementTree:
    """Parse MercProfiles.xml, tolerant of a UTF-8 BOM and of legacy
    cp1252 / mislabeled high bytes (which would otherwise raise
    XMLSyntaxError and hard-block every save on a localized install).
    Delegates to the shared `parse_tolerant`."""
    from ._atomic_xml import parse_tolerant
    return parse_tolerant(path)


def _format_value(field_name: str, value: object) -> str:
    """Format a Python value as the string the XML expects.

    Note: the `bool` branch is forward-compat only — no current `Merc`
    field is typed `bool` (engine-bool fields use `Literal[0, 1]` so the
    pydantic value is already an int). The branch exists because:
      - Python's `isinstance(True, int)` is True, so `str(True)` returns
        "True" — wrong for JA2's XML which expects "1"/"0".
      - The day someone re-types a field as `bool` thinking it's
        cleaner, this branch keeps the write correct.
    Bug-review #100 keeps it as documented-intent rather than deleting
    as dead code.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _set_child_text(parent: etree._Element, tag: str, value: str) -> None:
    """Set or create a child element with the given tag and text."""
    child = parent.find(tag)
    if child is None:
        child = etree.SubElement(parent, tag)
    child.text = value


# ─── Parse cache ─────────────────────────────────────────────────────
# MercProfiles.xml is 1-2 MB on heavily-modded installs (Wasteland,
# AIMNAS, etc.). Parsing it takes 500ms-2s with lxml; without a cache
# every roster fetch + every slot fetch + every portrait endpoint hit
# does a fresh parse, which compounded to 10-40 s of CPU work when the
# roster grid kicked off 200+ portrait requests in parallel — a user
# reported a "REALLY long time" loading slot 196.
#
# Cache key: (canonical_path, mtime_ns, size_bytes). Any external write
# bumps mtime so the cache invalidates naturally. Cap at 4 distinct
# files in case the user switches installs mid-session.
import threading

_PARSE_CACHE: dict[tuple[str, int, int], dict[int, dict[str, str]]] = {}
_PARSE_CACHE_MAX = 4
# Lock added 2026-05-25: FastAPI runs handlers in a threadpool. The
# 16-cell roster grid mounts 16 parallel reads — without the lock, two
# threads racing on `len() >= MAX → next(iter) → del` can hit a KeyError
# when one thread evicts the entry the other was about to delete.
_PARSE_CACHE_LOCK = threading.Lock()


def _cache_key(profiles_xml_path: Path) -> Optional[tuple[str, int, int]]:
    try:
        st = profiles_xml_path.stat()
    except OSError:
        return None
    return (str(profiles_xml_path.resolve()), st.st_mtime_ns, st.st_size)


def read_all_slots(profiles_xml_path: Path) -> dict[int, dict[str, str]]:
    """Return {uiIndex: {field_name: text_value, ...}} for every <PROFILE>.

    Used by the roster scanner. Returns raw string values; callers coerce.
    Empty / missing files return {}.

    Cached by (path, mtime_ns, size) — see _PARSE_CACHE.
    """
    if not profiles_xml_path.exists():
        return {}
    key = _cache_key(profiles_xml_path)
    if key is not None:
        with _PARSE_CACHE_LOCK:
            cached = _PARSE_CACHE.get(key)
        if cached is not None:
            return cached
    tree = _parse_with_bom_tolerance(profiles_xml_path)
    root = tree.getroot()
    result: dict[int, dict[str, str]] = {}
    for profile in root.findall("MERCPROFILE") + root.findall("PROFILE"):
        ui_index_elem = profile.find("uiIndex")
        if ui_index_elem is None or ui_index_elem.text is None:
            continue
        try:
            ui_index = int(ui_index_elem.text.strip())
        except ValueError:
            continue
        fields: dict[str, str] = {}
        for child in profile:
            if isinstance(child.tag, str) and child.text is not None:
                fields[child.tag] = child.text
        result[ui_index] = fields
    if key is not None:
        with _PARSE_CACHE_LOCK:
            # Re-check inside the lock — another thread may have populated
            # while we were parsing. Doesn't matter which wins (same bytes,
            # same key); we just want at most one insert + one eviction.
            if key not in _PARSE_CACHE:
                if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
                    try:
                        first_key = next(iter(_PARSE_CACHE))
                        del _PARSE_CACHE[first_key]
                    except StopIteration:
                        pass
                _PARSE_CACHE[key] = result
    return result


def invalidate_parse_cache() -> None:
    """Drop all cached parses. Called by writer paths after a successful
    write so the next read sees the new state without waiting for the
    mtime stat to differ (which it would anyway — but explicit eviction
    is cheaper than waiting on filesystem timestamp resolution)."""
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE.clear()


def read_slot(profiles_xml_path: Path, ui_index: int) -> Optional[dict[str, str]]:
    """Read one merc's fields from MercProfiles.xml. None if slot empty.

    Reuses the parsed cache built by `read_all_slots`."""
    all_slots = read_all_slots(profiles_xml_path)
    return all_slots.get(ui_index)


def is_slot_occupied(profiles_xml_path: Path, ui_index: int) -> bool:
    """True iff slot has a profile block AND a non-empty name or nickname.

    A stub <PROFILE> block with empty <zName> and <zNickname> is treated as
    EMPTY — matches the roster's definition of "is_empty". The engine treats
    such records as placeholders / NPCs that don't appear in any hireable list.
    """
    raw = read_slot(profiles_xml_path, ui_index)
    if raw is None:
        return False
    zname = (raw.get("zName") or "").strip()
    znick = (raw.get("zNickname") or "").strip()
    return bool(zname or znick)


def detect_schema(profiles_xml_path: Path) -> Optional[set[str]]:
    """Return the union of field names across every existing profile in this file.

    Used to make the writer schema-aware: never adds fields that the install's
    existing profiles don't carry (e.g. AIMNAS uses `fRegresses` not
    `bEvolution`, Arulco Revisited ships pre-STOMP with no `bRace`).

    Returns None when the file is missing or has no profiles — caller should
    fall back to the full writable set.
    """
    if not profiles_xml_path.exists():
        return None
    try:
        tree = _parse_with_bom_tolerance(profiles_xml_path)
    except etree.XMLSyntaxError:
        return None
    root = tree.getroot()
    entry_tag = _detect_entry_tag(root)
    seen: set[str] = set()
    for entry in root.findall(entry_tag):
        for child in entry:
            if isinstance(child.tag, str):
                seen.add(child.tag)
    return seen if seen else None


def upsert(profiles_xml_path: Path, merc: Merc) -> None:
    """Insert or update a <PROFILE> block for `merc`.

    Preserves unknown fields and existing formatting where possible. Creates
    the file with a minimal scaffold if missing.

    Schema-aware: when the file has existing profiles, the writer only writes
    fields that those profiles already carry. This avoids polluting AIMNAS
    rows with `bEvolution` (it uses `fRegresses`), or stuffing STOMP fields
    into Arulco Revisited (pre-STOMP schema). New blank files write the full
    set since there's no install precedent to follow.
    """
    if not profiles_xml_path.exists():
        # Scaffold a fresh file. Vanilla 1.13's MercProfiles.xml uses
        # <MERCPROFILES> (with S) as the list root and <PROFILE> as each
        # entry tag — verified against the reference install. Earlier
        # code scaffolded <PROFILES>/<MERCPROFILE> which the engine's XML
        # loader rejects (LoadMercProfiles expects the canonical pair).
        profiles_xml_path.parent.mkdir(parents=True, exist_ok=True)
        root = etree.Element("MERCPROFILES")
        tree = etree.ElementTree(root)
        _write_block(tree, root, merc, entry_tag="PROFILE")
        _save_tree(tree, profiles_xml_path)
        return

    tree = _parse_with_bom_tolerance(profiles_xml_path)
    root = tree.getroot()

    # Detect the entry tag used by this file (MERCPROFILE in vanilla; PROFILE
    # in some old mods)
    entry_tag = _detect_entry_tag(root)

    # Locate existing entry by uiIndex AND harvest the schema fingerprint in
    # the same walk. Previously this called `detect_schema(path)` which re-
    # parsed the whole file (1-2 MB lxml parse), doubling save-time. The
    # already-loaded tree has every field we need.
    target = None
    allowed: set[str] = set()
    for entry in root.findall(entry_tag):
        # Schema fingerprint: union of child tag names across every entry.
        for child in entry:
            if isinstance(child.tag, str):
                allowed.add(child.tag)
        if target is not None:
            continue  # keep walking entries to finish the schema scan
        ui_idx_elem = entry.find("uiIndex")
        if ui_idx_elem is not None and ui_idx_elem.text:
            try:
                if int(ui_idx_elem.text.strip()) == merc.uiIndex:
                    target = entry
            except ValueError:
                pass

    # When the file has no existing profiles, fall through with allowed=None
    # so _write_block uses the full writable set (no install precedent to
    # follow). Matches the old detect_schema semantics.
    allowed_arg: Optional[set[str]] = allowed if allowed else None
    _write_block(tree, root, merc, entry_tag, existing=target, allowed_fields=allowed_arg)
    _save_tree(tree, profiles_xml_path)


def clear_slot(profiles_xml_path: Path, ui_index: int) -> bool:
    """Remove the <PROFILE> block for `ui_index`. Returns True if removed."""
    if not profiles_xml_path.exists():
        return False
    tree = _parse_with_bom_tolerance(profiles_xml_path)
    root = tree.getroot()
    entry_tag = _detect_entry_tag(root)

    removed = False
    for entry in list(root.findall(entry_tag)):
        ui_idx_elem = entry.find("uiIndex")
        if ui_idx_elem is not None and ui_idx_elem.text:
            try:
                if int(ui_idx_elem.text.strip()) == ui_index:
                    root.remove(entry)
                    removed = True
                    break
            except ValueError:
                pass

    if removed:
        _save_tree(tree, profiles_xml_path)
    return removed


def _detect_entry_tag(root: etree._Element) -> str:
    """Return the tag name used for individual profile entries.

    Vanilla 1.13 uses MERCPROFILE; some mods use PROFILE. We pick whichever
    is present, defaulting to MERCPROFILE for a fresh file.
    """
    if root.find("MERCPROFILE") is not None:
        return "MERCPROFILE"
    if root.find("PROFILE") is not None:
        return "PROFILE"
    return "MERCPROFILE"


# Fields the wizard always writes regardless of detected install schema —
# the engine needs these on every profile for the load path to succeed.
# Even pre-STOMP / minimal-schema installs accept these (they were in vanilla
# 1.13 from the start).
_ALWAYS_WRITE_FIELDS = frozenset({
    "uiIndex", "ubFaceIndex", "Type", "zName", "zNickname",
    "bSex", "ubBodyType", "usVoiceIndex",
    "usEyesX", "usEyesY", "usMouthX", "usMouthY",
    "bLifeMax", "bLife",
    "bStrength", "bAgility", "bDexterity", "bWisdom", "bExpLevel",
    "bMarksmanship", "bExplosive", "bLeadership", "bMedical", "bMechanical",
})


def _write_block(
    tree: etree._ElementTree,
    root: etree._Element,
    merc: Merc,
    entry_tag: str,
    existing: Optional[etree._Element] = None,
    allowed_fields: Optional[set[str]] = None,
) -> None:
    """Write or update a single <MERCPROFILE> block.

    If `allowed_fields` is set (the install's detected schema), only writes
    fields that either (a) appear in the detected set or (b) are in the
    `_ALWAYS_WRITE_FIELDS` core that every install needs. Unknown extras
    in the existing block are left untouched.
    """
    if existing is None:
        block = etree.SubElement(root, entry_tag)
    else:
        block = existing

    merc_dict = merc.model_dump()
    for field_name in _MERC_WRITABLE_FIELDS:
        # The on-disk tag differs from the model field name only for the growth
        # modifiers (engine wants a "b" prefix). Everything else is identity.
        xml_tag = _FIELD_TO_XML_TAG.get(field_name, field_name)
        if allowed_fields is not None:
            # A field is "in the install's schema" if EITHER its engine tag or
            # its (legacy prefix-less) field name is present. The field-name
            # arm heals installs where a pre-fix MercWizard wrote the
            # prefix-less growth tag — we still emit the correct b-tag below.
            if (
                xml_tag not in allowed_fields
                and field_name not in allowed_fields
                and xml_tag not in _ALWAYS_WRITE_FIELDS
            ):
                continue
        value = merc_dict[field_name]
        _set_child_text(block, xml_tag, _format_value(field_name, value))
        # When the tag diverges from the field name, drop any stale prefix-less
        # sibling a pre-fix MercWizard may have written, so the engine-ignored
        # duplicate doesn't linger or get re-detected as the install's schema on
        # the next save (which would re-skip the real b-tag).
        if xml_tag != field_name:
            stale = block.find(field_name)
            if stale is not None:
                block.remove(stale)


def _save_tree(tree: etree._ElementTree, path: Path) -> None:
    """Serialize the tree back atomically, pretty-printed, UTF-8 without BOM.

    Atomic via tempfile + os.replace (see `_atomic_xml.save_atomic`). A crash
    mid-write would otherwise leave a truncated MercProfiles.xml that the
    engine refuses to load at boot (RUNTIME ERROR LoadExternalGameplayData).

    Evicts the in-process parse cache for this path so the next read
    sees the new state without depending on mtime resolution. The cache
    key tuple is `(path, mtime_ns, size)` — if the OS reports the same
    `mtime_ns` for the write that just landed as for the pre-write parse
    (FAT volumes have 2s mtime granularity; some NTFS configurations
    coarsen it; same-second writes can collide), a follow-up reader
    would return stale data. Mirrors the explicit eviction that
    aim_availability._save, merc_availability._save, and
    starting_gear._save already do. Bug-review finding A1/B6/E1.
    """
    from ._atomic_xml import save_atomic_preserving
    save_atomic_preserving(tree, path)
    invalidate_parse_cache()
