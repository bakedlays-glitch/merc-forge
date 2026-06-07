"""
parse_world_items.py - WORLDITEM appendix parser for JA2 1.13 saved maps.

Phase E1.5 follow-on (Option 1 / Phase WA): closes the items-section blocker
that prevents `parse_appendix_minimal` from reading past the items appendix.

Two engine code paths per WORLDITEM::Load (SaveLoadGame.cpp:2839):

  Path B (LEGACY, fixed-size) — major < 6.0 OR minor <= 26
    Reads sizeof(OLD_WORLDITEM_101) = 52 bytes per item. No recursion. No
    Items.xml dependency. Covers 5,950 / 8,038 corpus records (~74%) per the
    Phase E1.5 v2 corpus rebuild on 2026-05-19.

  Path A (MODERN, recursive) — major >= 6.0 AND minor > 26
    Reads variable-size WORLDITEM POD + OBJECTTYPE (with recursive
    StackedObjectData attachments + version-conditional ObjectData blocks +
    IsActiveLBE-gated LBENODE recursion). Covers ~2,088 corpus records
    (~26%). NOT IMPLEMENTED in Phase WA — bails with diagnostic.

Phase WA delivery: Path B is fully parsed; Path A returns a clear bail
reason so `parse_appendix_minimal` can nullify downstream sections.

Phase WB will land the Path A parser in a separate session.

Byte-layout source of truth:
  - WORLDITEM, OLD_WORLDITEM_101, _OLD_WORLDITEM, _WORLDITEM_INT8_ID:
    Source Files/1.13 Source/source-master/Tactical/World Items.h:33-120
  - OLD_OBJECTTYPE_101 + OLD_OBJECTTYPE_101_UNION:
    Source Files/1.13 Source/source-master/Tactical/Item Types.h:296-391
  - WORLDITEM::Load dispatch:
    Source Files/1.13 Source/source-master/Ja2/SaveLoadGame.cpp:2839
  - BOOLEAN typedef:
    Source Files/1.13 Source/source-master/sgp/types.h:58
    (typedef unsigned char BOOLEAN — IMPORTANT: NOT INT32; 1 byte only.)

Verified empirically against Arulco Revisited p1.dat (v5.0.25, 41 items, flags
0x17d): SIZEOF_OLD_WORLDITEM_101 = 52 bytes lands the post-items cursor at
LoadMapLights header (1 color + 0 lights + 7-byte header), then the
MapInformation tail starts cleanly with 4 plausible edge gridnos.
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


# --- Constants (byte-exact, from JA2 source) -------------------------------

# BOOLEAN = unsigned char per sgp/types.h:58 — 1 byte. NOT 4 bytes.
# This drove the SIZEOF_OLD_WORLDITEM_101 derivation below.

# OLD_OBJECTTYPE_101 internal layout (Item Types.h:372-391):
#   UINT16 usItem                      @  0..1   (2)
#   UINT8  ubNumberOfObjects           @  2      (1)
#   [pad to 4-align union]             @  3      (1)
#   OLD_OBJECTTYPE_101_UNION ugYucky   @  4..15  (12 — union sized by Money struct
#                                                    aligned to 4 for its UINT32)
#   UINT16 usAttachItem[4]             @ 16..23  (8 — OLD_MAX_ATTACHMENTS_101 = 4)
#   INT8   bAttachStatus[4]            @ 24..27  (4)
#   INT8   fFlags                      @ 28      (1)
#   UINT8  ubMission                   @ 29      (1)
#   INT8   bTrap                       @ 30      (1)
#   UINT8  ubImprintID                 @ 31      (1)
#   UINT8  ubWeight                    @ 32      (1)
#   UINT8  fUsed                       @ 33      (1)
#   [tail pad to 4-align]              @ 34..35  (2)
# Total: 36 bytes
SIZEOF_OLD_OBJECTTYPE_101 = 36

# OLD_WORLDITEM_101 layout (World Items.h:33-45):
#   BOOLEAN fExists                    @  0      (1 — BOOLEAN=uchar)
#   [pad to 2-align sGridNo]           @  1      (1)
#   INT16   sGridNo                    @  2..3   (2)
#   UINT8   ubLevel                    @  4      (1)
#   [pad to 4-align oldObject]         @  5..7   (3)
#   OLD_OBJECTTYPE_101 oldObject       @  8..43  (36)
#   UINT16  usFlags                    @ 44..45  (2)
#   INT8    bRenderZHeightAboveLevel   @ 46      (1)
#   INT8    bVisible                   @ 47      (1)
#   UINT8   ubNonExistChance           @ 48      (1)
#   [tail pad to 4-align]              @ 49..51  (3)
# Total: 52 bytes
SIZEOF_OLD_WORLDITEM_101 = 52

# Field offsets within OLD_WORLDITEM_101 (for struct.unpack_from)
_OFF_FEXISTS      = 0
_OFF_SGRIDNO      = 2
_OFF_UBLEVEL      = 4
_OFF_OBJECT       = 8         # OLD_OBJECTTYPE_101 starts here
_OFF_USITEM       = 8         # within oldObject @ 0
_OFF_UBNUMOBJECTS = 10        # within oldObject @ 2
_OFF_USFLAGS      = 44
_OFF_BRENDERZH    = 46
_OFF_BVISIBLE     = 47
_OFF_UBNONEXIST   = 48


# --- Phase WB constants (Path A modern recursive parser) -------------------

# HIGH-confidence (source-verified field-by-field):
#   OBJECTTYPE POD (Item Types.h:636-646): usItem(2) + ubNumberOfObjects(1) +
#     ubMission(1) + fFlags(1) = 5 bytes to endOfPOD marker.
SIZEOF_OBJECTTYPE_POD = 5

#   LBENODE POD (Item Types.h:240-259): lbeClass(UINT32,4) + lbeIndex(UINT16,2)
#     + ubID(SoldierID wrapper of UINT16,2) + ZipperFlag(BOOLEAN=uchar,1) +
#     3 pad bytes + uniqueID(int,4) + uiNodeChecksum(UINT32,4) = 20 bytes.
SIZEOF_LBENODE_POD = 20

#   OBJECT_LBE union variant (Item Types.h:437-442):
#     bLBEStatus(INT16,2) + bLBE(INT8,1) + 1 pad + uniqueID(int,4) = 8 bytes.
#   bLBE sits at offset 2 within the union (and within the ObjectData block
#   since the union is at offset 0 inside ObjectData_PRE_ITS).
SIZEOF_OBJECT_LBE         = 8
BLBE_OFFSET_IN_OBJECTDATA = 2

# IC_LBEGEAR bitmask in Items.xml usItemClass (Tactical/Item Types.h:677).
IC_LBEGEAR = 0x00020000

# Map minor-version thresholds (TileEngine/worlddef.h:41-46).
MINOR_MAP_OVERHEATING   = 28
MINOR_MAP_REPAIR_SYSTEM = 30
MINOR_MAP_VERSION       = 31

# Hard cap on recursion: WORLDITEM -> OBJECTTYPE -> StackedObjectData
# (-> attachments) -> LBENODE -> Inventory -> OBJECTTYPE -> ... A real attached
# weapon won't get past depth 4-5; depth 16 catches pathological recursion.
MAX_RECURSION_DEPTH = 16

# WORLDITEM POD per version (World Items.h:33-120; dispatch at
# SaveLoadGame.cpp:2839+). Verified field-by-field with MSVC default alignment.
#   _OLD_WORLDITEM      (major<7):       INT16 sGridNo, INT8 soldierID → 12B
#   _WORLDITEM_INT8_ID  (7<=major<8):    INT32 sGridNo, INT8 soldierID → 16B
#   WORLDITEM           (major>=8):      INT32 sGridNo, SoldierID(2B)  → 18B
SIZEOF_WORLDITEM_POD_OLD     = 12
SIZEOF_WORLDITEM_POD_INT8_ID = 16
SIZEOF_WORLDITEM_POD_MODERN  = 18

# LOW-confidence (StackedObjectData.data block size 5-branch ladder per
# SaveLoadGame.cpp:2968-3008). These are starting hypotheses pending hex-dump
# verification on D5.dat. The actual sizeof(ObjectData) used by the engine
# depends on MSVC packing of UINT64 sObjectFlag — could be 40 (Flugente's
# historical comment at line 2992), 44 (Zp4 with tail pad), or 48 (default
# MSVC x86-32 with 8-byte UINT64 alignment). Step 3 of Phase WB will lock
# the actual value via empirical parsing.
#
# Branch dispatch (engine source verbatim):
#   1. major>=8 && minor>=31:                  sizeof(ObjectData)
#   2. major>=7 && minor>=31:                  sizeof(ObjectData_PRE_ITS)
#   3. major>=7 && minor>=MINOR_MAP_REPAIR_SYS: sizeof(ObjectData_PRE_ITS) - sizeof(sObjectFlag)
#   4. major>=7 && minor>=MINOR_MAP_OVERHEATING: 32  (hardcoded literal in engine)
#   5. else:                                    SIZEOF_OBJECTDATA_POD_PRE_ITS + 1 = 16
SIZEOF_OBJECTDATA_BRANCH_1 = 48  # adjust after Step 3
SIZEOF_OBJECTDATA_BRANCH_2 = 48  # adjust after Step 3 (Branch 1 == Branch 2 in current source)
SIZEOF_OBJECTDATA_BRANCH_3 = 40  # always = Branch 2 - 8 (sizeof UINT64 sObjectFlag)
SIZEOF_OBJECTDATA_BRANCH_4 = 32  # HARDCODED in engine — never changes
SIZEOF_OBJECTDATA_BRANCH_5 = 16  # SIZEOF_OBJECTDATA_POD_PRE_ITS(15) + 1


# --- Items.xml loader -------------------------------------------------------


def load_items_xml(path: Path | str | None) -> dict[int, int]:
    """Parse a JA2 1.13 Items.xml into {uiIndex: usItemClass}.

    Used by Path A to decide IsActiveLBE during recursive parsing:
    OBJECTTYPE is an active LBE-container iff
    `Item[usItem].usItemClass & IC_LBEGEAR` AND its first StackedObject's
    `data.lbe.bLBE == -1`.

    Items.xml schema (Data-1.13/TableData/Items/Items.xml):
      <ITEMLIST>
        <ITEM>
          <uiIndex>265</uiIndex>
          <usItemClass>131072</usItemClass>  <!-- IC_LBEGEAR = 0x00020000 -->
          ...
        </ITEM>
        ...
      </ITEMLIST>

    `usItemClass` is an integer (bitmask), not the IC_GUN-style symbolic name.

    Graceful degradation: if `path` is None, missing, or unparseable, returns
    an empty dict rather than raising. Callers treat an empty table as "no
    active-LBE info available" — Path A's recursion still parses, it just
    can't classify containers, which is the same as having no Items.xml.
    """
    if path is None:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        tree = ET.parse(p)
    except (ET.ParseError, OSError):
        return {}
    root = tree.getroot()
    out: dict[int, int] = {}
    for item in root.findall("ITEM"):
        idx_el = item.find("uiIndex")
        cls_el = item.find("usItemClass")
        if idx_el is None or cls_el is None or idx_el.text is None or cls_el.text is None:
            continue
        try:
            out[int(idx_el.text)] = int(cls_el.text)
        except ValueError:
            # Skip malformed entries rather than failing the whole load.
            continue
    return out


def parse_world_items(
    data: bytes,
    pos: int,
    count: int,
    major: float,
    minor: int,
    items_table: dict[int, int] | None = None,
    *,
    capture: str = "summary",
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Parse `count` WORLDITEM records starting at byte offset `pos`.

    Dispatches between Path B (legacy fixed-size) and Path A (modern recursive)
    based on (major, minor). Path A is not yet implemented — Phase WA only
    delivers Path B.

    Args:
        data:        the .dat file bytes
        pos:         byte offset where the first WORLDITEM begins
        count:       number of WORLDITEM records to read (from the leading
                     uint32 already consumed by parse_appendix_minimal)
        major:       map major version (e.g. 5.0, 7.0)
        minor:       map minor version (e.g. 25, 31)
        items_table: optional {usItem: usItemClass} for IsActiveLBE in Path A.
                     Ignored in Phase WA.
        capture:     "summary" returns per-item dicts; "none" returns []
                     (just advancement, faster for the corpus rebuild)

    Returns:
        (items, new_pos, bail_reason)
          items:       list of per-item dicts (or [] if capture="none")
          new_pos:     byte offset of the first byte AFTER the last item
          bail_reason: None on full success, else a short string for
                       parse_appendix_minimal's appendix_parse_stopped_at.
    """
    # Path B (LEGACY): major < 6.0 OR minor <= 26
    # Per WORLDITEM::Load's outer if/else at SaveLoadGame.cpp:2841/2860.
    if major < 6.0 or minor <= 26:
        return _parse_path_b_legacy(data, pos, count, capture)

    # Path A (MODERN, Phase WB): recursive walk of WORLDITEM POD + OBJECTTYPE
    # (with stack of StackedObjectData, recursive attachments, IsActiveLBE-gated
    # LBENODE recursion into a nested OBJECTTYPE inventory).
    return _parse_path_a_modern(data, pos, count, major, minor, items_table, capture)


def _parse_path_b_legacy(
    data: bytes,
    pos: int,
    count: int,
    capture: str,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Parse Path B (legacy) items. Each record is exactly
    SIZEOF_OLD_WORLDITEM_101 = 52 bytes."""
    n = len(data)
    items: list[dict[str, Any]] = []
    p = pos

    for i in range(count):
        end = p + SIZEOF_OLD_WORLDITEM_101
        if end > n:
            return items, p, "items_legacy_truncated"

        if capture == "none":
            # Fast path: just advance the cursor.
            p = end
            continue

        # Summary capture: unpack the 6 most useful fields.
        # struct.unpack_from format chars per ja2-open-toolset/ja2py/content/Npc.py
        # convention: lowercase = signed, uppercase = unsigned.
        # fExists is BOOLEAN (uchar) = 'B'.
        fExists  = data[p + _OFF_FEXISTS]
        sGridNo, = struct.unpack_from("<h", data, p + _OFF_SGRIDNO)
        ubLevel  = data[p + _OFF_UBLEVEL]
        usItem,  = struct.unpack_from("<H", data, p + _OFF_USITEM)
        ubNum    = data[p + _OFF_UBNUMOBJECTS]
        usFlags, = struct.unpack_from("<H", data, p + _OFF_USFLAGS)

        items.append({
            "gridno": sGridNo,
            "level":  ubLevel,
            "usItem": usItem,
            "count":  ubNum,
            "fFlags": usFlags,
            "exists": bool(fExists),
        })
        p = end

    # All items parsed cleanly. Caller can continue with ambient/lights/etc.
    return items, p, None


# --- Path A (MODERN) — Phase WB ---------------------------------------------

def _select_worlditem_pod_size(major: float) -> int:
    """Pick the WORLDITEM POD size per WORLDITEM::Load dispatch
    (SaveLoadGame.cpp:2839-2868).

      _OLD_WORLDITEM      (major<7.0):    12 bytes
      _WORLDITEM_INT8_ID  (7.0<=major<8): 16 bytes
      WORLDITEM           (major>=8.0):   18 bytes
    """
    if major < 7.0:
        return SIZEOF_WORLDITEM_POD_OLD
    if major < 8.0:
        return SIZEOF_WORLDITEM_POD_INT8_ID
    return SIZEOF_WORLDITEM_POD_MODERN


def _select_object_data_size(major: float, minor: int) -> int:
    """5-branch ObjectData size ladder per StackedObjectData::Load
    (SaveLoadGame.cpp:2968-3008). Order matters — the engine checks branches
    top-down and stops on the first match.

      1. major>=8 + minor>=31:                   sizeof(ObjectData)             = 48
      2. major>=7 + minor>=31:                   sizeof(ObjectData_PRE_ITS)     = 48
      3. major>=7 + minor>=MINOR_MAP_REPAIR_SYS: above - sizeof(UINT64)         = 40
      4. major>=7 + minor>=MINOR_MAP_OVERHEATING: 32 (HARDCODED)                = 32
      5. else:                                    SIZEOF_OBJECTDATA_POD_PRE_ITS+1 = 16
    """
    if major >= 8 and minor >= MINOR_MAP_VERSION:
        return SIZEOF_OBJECTDATA_BRANCH_1
    if major >= 7 and minor >= MINOR_MAP_VERSION:
        return SIZEOF_OBJECTDATA_BRANCH_2
    if major >= 7 and minor >= MINOR_MAP_REPAIR_SYSTEM:
        return SIZEOF_OBJECTDATA_BRANCH_3
    if major >= 7 and minor >= MINOR_MAP_OVERHEATING:
        return SIZEOF_OBJECTDATA_BRANCH_4
    return SIZEOF_OBJECTDATA_BRANCH_5


def _is_active_lbe(
    otype_usItem: int,
    sod_data_bytes: bytes,
    items_table: dict[int, int] | None,
) -> bool:
    """Mirror of OBJECTTYPE::IsActiveLBE (Item Types.cpp:527-533).

      bool OBJECTTYPE::IsActiveLBE(unsigned int index) {
          if (exists() && Item[this->usItem].usItemClass == IC_LBEGEAR)
              return ((*this)[index]->data.lbe.bLBE == -1);
          return false;
      }

    Both conditions are required: the item class must be IC_LBEGEAR (lookup
    via Items.xml) AND the stacked object's bLBE byte must be -1 (0xFF as INT8).
    """
    if items_table is None:
        return False
    item_class = items_table.get(otype_usItem, 0)
    if not (item_class & IC_LBEGEAR):
        return False
    if len(sod_data_bytes) <= BLBE_OFFSET_IN_OBJECTDATA:
        return False
    # bLBE is INT8 -1, stored as 0xFF.
    return sod_data_bytes[BLBE_OFFSET_IN_OBJECTDATA] == 0xFF


def _parse_path_a_modern(
    data: bytes,
    pos: int,
    count: int,
    major: float,
    minor: int,
    items_table: dict[int, int] | None,
    capture: str,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Walk `count` modern WORLDITEMs starting at `pos`. Recursive (each item's
    OBJECTTYPE contains a stack of StackedObjectData with attachments, plus
    IsActiveLBE-gated LBENODE recursion into a nested inventory of OBJECTTYPEs).

    Returns (items, new_pos, bail_reason).
    """
    n = len(data)
    items: list[dict[str, Any]] = []
    p = pos
    pod_size = _select_worlditem_pod_size(major)
    sod_size = _select_object_data_size(major, minor)

    for i in range(count):
        if p + pod_size > n:
            return items, p, "items_worlditem_pod_truncated"

        # --- WORLDITEM POD ----------------------------------------------
        # Layout is the same shape across all 3 variants; size and a few
        # field offsets differ. We need: fExists, sGridNo, ubLevel, usFlags.
        fExists = data[p]
        if pod_size == SIZEOF_WORLDITEM_POD_OLD:
            # _OLD_WORLDITEM: INT16 sGridNo @ 2 (after 1B pad)
            sGridNo, = struct.unpack_from("<h", data, p + 2)
            ubLevel  = data[p + 4]
            usFlags, = struct.unpack_from("<H", data, p + 6)
        else:
            # _WORLDITEM_INT8_ID / WORLDITEM: INT32 sGridNo @ 4 (after 3B pad)
            sGridNo, = struct.unpack_from("<i", data, p + 4)
            ubLevel  = data[p + 8]
            usFlags, = struct.unpack_from("<H", data, p + 10)
        p += pod_size

        # --- OBJECTTYPE (the embedded `object` field of WORLDITEM) ------
        otype, p, bail = _load_objecttype(
            data, p, major, minor, sod_size, items_table, depth=0, capture=capture
        )
        if bail:
            return items, p, bail

        if capture == "none":
            continue

        info: dict[str, Any] = {
            "gridno":  sGridNo,
            "level":   ubLevel,
            "usItem":  otype["usItem"],
            "count":   otype["ubNumberOfObjects"],
            "fFlags":  usFlags,
            "exists":  bool(fExists),
            "attachments_count": otype["attachments_count"],
            "has_lbe": otype["has_lbe"],
        }
        if capture == "full":
            info["stack"] = otype.get("stack", [])
        items.append(info)

    return items, p, None


def _load_objecttype(
    data: bytes,
    pos: int,
    major: float,
    minor: int,
    sod_size: int,
    items_table: dict[int, int] | None,
    depth: int,
    capture: str,
) -> tuple[dict[str, Any], int, str | None]:
    """Load one OBJECTTYPE: 5B POD + INT stack_size + stack_size *
    (StackedObjectData [+ LBENODE if IsActiveLBE]).

    Mirrors OBJECTTYPE::Load at SaveLoadGame.cpp:3167-3199:

        LOADDATA(this, *hBuffer, SIZEOF_OBJECTTYPE_POD );
        LOADDATA(&size, *hBuffer, sizeof(int) );
        objectStack.resize(size);
        for (StackedObjects::iterator iter = ..., ++x) {
            iter->Load(hBuffer, ...);                       // StackedObjectData::Load
            if (this->IsActiveLBE(x) == true) {             // checked PER STACK INDEX
                LBEArray.push_back(LBENODE());
                LBEArray.back().Load(hBuffer, ...);          // LBENODE::Load
            }
        }

    Critically: the IsActiveLBE check belongs at the OBJECTTYPE level (not
    inside StackedObjectData::Load), and recursive attachments DO NOT
    re-trigger it. Inventory items in an LBENODE recurse OBJECTTYPE::Load,
    which re-applies the same OBJECTTYPE-level check.

    Returns (otype_dict, new_pos, bail).
    """
    n = len(data)
    if depth >= MAX_RECURSION_DEPTH:
        return {}, pos, "items_recursion_too_deep"
    if pos + SIZEOF_OBJECTTYPE_POD + 4 > n:
        return {}, pos, "items_objecttype_pod_truncated"

    usItem,    = struct.unpack_from("<H", data, pos)
    ubNum      = data[pos + 2]
    ubMission  = data[pos + 3]
    fFlagsByte = data[pos + 4]
    p = pos + SIZEOF_OBJECTTYPE_POD

    stack_size, = struct.unpack_from("<i", data, p)
    p += 4
    if stack_size < 0 or stack_size > 1000:
        return {}, p, f"items_stack_size_implausible_{stack_size}"

    total_attachments = 0
    has_lbe = False
    stack_list: list[dict[str, Any]] | None = [] if capture == "full" else None

    for s in range(stack_size):
        # 1. StackedObjectData::Load — data block + recursive OBJECTTYPE attachments.
        sod_info, p, bail = _load_stacked_object_data(
            data, p, major, minor, sod_size, items_table,
            depth=depth + 1, capture=capture,
        )
        if bail:
            return {}, p, bail
        total_attachments += sod_info["attachments_count"]

        # 2. IsActiveLBE check at the OBJECTTYPE level. Requires items_table.
        sod_bytes = sod_info["data_bytes"]
        blbe_is_neg_one = (
            len(sod_bytes) > BLBE_OFFSET_IN_OBJECTDATA
            and sod_bytes[BLBE_OFFSET_IN_OBJECTDATA] == 0xFF  # INT8 -1
        )
        # If items_table is missing AND the bLBE byte looks active (-1),
        # we can't replicate the engine's `Item[usItem].usItemClass`
        # lookup — bailing here avoids silent misalignment on maps with
        # real LBE containers (whose LBENODE follows immediately and
        # would otherwise be misread as the next item).
        if items_table is None and blbe_is_neg_one:
            return {}, p, "items_lbe_no_table"
        is_lbe_eligible = (
            items_table is not None
            and (items_table.get(usItem, 0) & IC_LBEGEAR)
            and blbe_is_neg_one
        )
        if is_lbe_eligible:
            lbe_info, p, bail = _load_lbenode(
                data, p, major, minor, sod_size, items_table,
                depth=depth + 1, capture=capture,
            )
            if bail:
                return {}, p, bail
            has_lbe = True
            total_attachments += lbe_info.get("inventory_total_attachments", 0)
            if stack_list is not None:
                sod_info = {**sod_info, "lbe": lbe_info}

        if stack_list is not None:
            # Don't carry the raw data bytes through to the dump.
            sod_info.pop("data_bytes", None)
            stack_list.append(sod_info)

    out = {
        "usItem":             usItem,
        "ubNumberOfObjects":  ubNum,
        "ubMission":          ubMission,
        "fFlags":             fFlagsByte,
        "attachments_count":  total_attachments,
        "has_lbe":            has_lbe,
    }
    if stack_list is not None:
        out["stack"] = stack_list
    return out, p, None


def _load_stacked_object_data(
    data: bytes,
    pos: int,
    major: float,
    minor: int,
    sod_size: int,
    items_table: dict[int, int] | None,
    depth: int,
    capture: str,
) -> tuple[dict[str, Any], int, str | None]:
    """Load one StackedObjectData: `sod_size` bytes of ObjectData + INT
    attachment_count + attachment_count * OBJECTTYPE (recursive).

    Mirrors StackedObjectData::Load at SaveLoadGame.cpp:2958-3022. The
    `attachmentList` typedef at Item Types.h:552 is `std::list<OBJECTTYPE>`
    (NOT std::list<StackedObjectData> as the iter->Load call site might
    suggest — `iter->Load` resolves to OBJECTTYPE::Load via the typedef).
    Each attachment therefore re-enters _load_objecttype, which loads the
    attachment's own POD + stack + nested SODs + possibly its own LBENODE.

    Does NOT check IsActiveLBE for the parent OBJECTTYPE — that's done by
    `_load_objecttype` after this returns.

    Returns ({data_bytes, attachments_count, [attachments]}, new_pos, bail).
    """
    n = len(data)
    if depth >= MAX_RECURSION_DEPTH:
        return {}, pos, "items_recursion_too_deep"

    if pos + sod_size + 4 > n:
        return {}, pos, "items_sod_data_truncated"

    sod_bytes = bytes(data[pos:pos + sod_size])
    p = pos + sod_size

    att_count, = struct.unpack_from("<i", data, p)
    p += 4
    if att_count < 0 or att_count > 100:
        return {}, p, f"items_attachment_count_implausible_{att_count}"

    attachments_list: list[dict[str, Any]] | None = [] if capture == "full" else None
    total_attachments = att_count

    for a in range(att_count):
        sub_otype, p, bail = _load_objecttype(
            data, p, major, minor, sod_size, items_table,
            depth=depth + 1, capture=capture,
        )
        if bail:
            return {}, p, bail
        # Sub-attachment counts cascade up.
        total_attachments += sub_otype["attachments_count"]
        if attachments_list is not None:
            attachments_list.append(sub_otype)

    out: dict[str, Any] = {
        "data_bytes": sod_bytes,
        "attachments_count": total_attachments,
    }
    if attachments_list is not None:
        out["attachments"] = attachments_list
    return out, p, None


def _load_lbenode(
    data: bytes,
    pos: int,
    major: float,
    minor: int,
    sod_size: int,
    items_table: dict[int, int] | None,
    depth: int,
    capture: str,
) -> tuple[dict[str, Any], int, str | None]:
    """Load one LBENODE: 20B POD + INT inv_size + inv_size * OBJECTTYPE.

    Returns ({inventory_size, inventory_total_attachments, [inventory]}, new_pos, bail).
    """
    n = len(data)
    if depth >= MAX_RECURSION_DEPTH:
        return {}, pos, "items_recursion_too_deep"
    if pos + SIZEOF_LBENODE_POD + 4 > n:
        return {}, pos, "items_lbenode_pod_truncated"

    p = pos + SIZEOF_LBENODE_POD
    inv_size, = struct.unpack_from("<i", data, p)
    p += 4
    if inv_size < 0 or inv_size > 100:
        return {}, p, f"items_inv_size_implausible_{inv_size}"

    inv_list: list[dict[str, Any]] | None = [] if capture == "full" else None
    inv_total_attachments = 0

    for i in range(inv_size):
        otype, p, bail = _load_objecttype(
            data, p, major, minor, sod_size, items_table,
            depth=depth + 1, capture=capture,
        )
        if bail:
            return {}, p, bail
        inv_total_attachments += otype["attachments_count"]
        if inv_list is not None:
            inv_list.append(otype)

    out: dict[str, Any] = {
        "inventory_size": inv_size,
        "inventory_total_attachments": inv_total_attachments,
    }
    if inv_list is not None:
        out["inventory"] = inv_list
    return out, p, None
