"""JA2 1.13 legacy RPC dialogue file codecs — .NPC quote records + .EDT text.

Byte-for-byte writer/reader for the two files that make a profile a talkable,
recruitable RPC:

  * ``.NPC`` — 50 fixed 32-byte quote records (``OLD_QUEST_DEF`` legacy format,
    no B113 header). Each record is one dialogue/recruit branch.
  * ``.EDT`` — 480-byte UTF-16LE dialogue slots with the engine's +1/-1
    obfuscation; quote N lives at offset N*480.

Ported verbatim (stdlib-only) from
``Headless_Compiler/rpc_compiler/rpc_data.py`` — the writer proven
byte-identical against vanilla files. See memory
``project_rpc_authoring_dogmeat_a9`` for the 6 gotchas ``active_record`` bakes in.
"""
from __future__ import annotations

import struct

NUM_NPC_QUOTE_RECORDS = 50
OLD_RECORD_SIZE = 32
OLD_FILE_SIZE = NUM_NPC_QUOTE_RECORDS * OLD_RECORD_SIZE  # 1600

_REC_FIELDS = [
    ("fFlags", "H"),
    ("sRequiredItem", "h"),       # union w/ sRequiredGridno (neg => gridno)
    ("usFactMustBeTrue", "H"),
    ("usFactMustBeFalse", "H"),
    ("ubQuest", "B"),
    ("ubFirstDay", "B"),
    ("ubLastDay", "B"),
    ("ubApproachRequired", "B"),
    ("ubOpinionRequired", "B"),
    ("ubQuoteNum", "B"),
    ("ubNumQuotes", "B"),
    ("ubStartQuest", "B"),
    ("ubEndQuest", "B"),
    ("ubTriggerNPC", "B"),
    ("ubTriggerNPCRec", "B"),
    ("ubFiller", "B"),
    ("usSetFactTrue", "H"),
    ("usGiftItem", "H"),
    ("usGoToGridno", "H"),
    ("sActionData", "h"),
]
_REC_STRUCT = "<" + "".join(code for _, code in _REC_FIELDS) + "4x"  # 4x = ubUnused[4]
assert struct.calcsize(_REC_STRUCT) == OLD_RECORD_SIZE, struct.calcsize(_REC_STRUCT)
_REC_NAMES = [name for name, _ in _REC_FIELDS]

# Engine sentinels (verified TacticalAI/NPC.cpp:101-106): an UNSET fact = NO_FACT,
# an UNGATED quote = NO_QUEST. Vanilla ACTIVE records use these + ubLastDay=255.
NO_FACT = 65535          # NO_FACT == MAX_FACTS-1
NO_QUEST = 255
IRRELEVANT = 255


def new_record(**overrides) -> dict:
    """A zeroed quote record (matches vanilla PADDING); pass field=value overrides."""
    rec = {name: 0 for name in _REC_NAMES}
    for k, v in overrides.items():
        if k not in rec:
            raise KeyError(f"unknown NPC record field: {k}")
        rec[k] = int(v)
    return rec


def active_record(**overrides) -> dict:
    """A real/authored record with engine-correct sentinels (not quest-0/fact-0).

    Defaults: no quest gate, available all game (day 0..255), one quote, no fact
    requirements. Override what you need (ubApproachRequired, ubOpinionRequired,
    sRequiredItem, usFactMustBeTrue, sActionData, ubQuoteNum, ...).

    CRITICAL: usGoToGridno defaults to 65535. In the old->new record conversion
    (NPC.cpp:309) any value >= OLD_WORLD_MAX maps to NOWHERE(-1) == NO_MOVE, and
    Converse only dispatches sActionData when usGoToGridNo == NO_MOVE (NPC.cpp:2437).
    A 0 here means "go to gridno 0" and the recruit action would SILENTLY never fire.

    ubTriggerNPC/ubTriggerNPCRec MUST be IRRELEVANT(255), not 0: on a successful
    match Converse (NPC.cpp:2498) does `if (ubTriggerNPC != IRRELEVANT)` -> with 0
    it makes a random squadmate blurt quote 0 the instant the recruit succeeds.
    """
    base = dict(ubQuest=NO_QUEST, ubFirstDay=0, ubLastDay=255, ubNumQuotes=1,
                usFactMustBeTrue=NO_FACT, usFactMustBeFalse=NO_FACT, usGoToGridno=65535,
                ubTriggerNPC=IRRELEVANT, ubTriggerNPCRec=IRRELEVANT)
    base.update({k: int(v) for k, v in overrides.items()})
    return new_record(**base)


def pack_record(rec: dict) -> bytes:
    return struct.pack(_REC_STRUCT, *(int(rec.get(name, 0)) for name in _REC_NAMES))


def unpack_record(buf: bytes) -> dict:
    vals = struct.unpack(_REC_STRUCT, buf[:OLD_RECORD_SIZE])
    return dict(zip(_REC_NAMES, vals))


def pack_npc_file(records: list[dict]) -> bytes:
    if len(records) > NUM_NPC_QUOTE_RECORDS:
        raise ValueError(f"too many records: {len(records)} > {NUM_NPC_QUOTE_RECORDS}")
    padded = list(records) + [new_record() for _ in range(NUM_NPC_QUOTE_RECORDS - len(records))]
    out = b"".join(pack_record(r) for r in padded)
    assert len(out) == OLD_FILE_SIZE
    return out


def unpack_npc_file(data: bytes) -> list[dict]:
    if len(data) != OLD_FILE_SIZE:
        raise ValueError(f"not old-format .NPC: {len(data)} bytes (expected {OLD_FILE_SIZE})")
    return [unpack_record(data[i * OLD_RECORD_SIZE:(i + 1) * OLD_RECORD_SIZE])
            for i in range(NUM_NPC_QUOTE_RECORDS)]


# ── .EDT dialogue text (480-byte slots, UTF-16LE, +1/-1 obfuscation) ─────────

DIALOGUE_SLOT_BYTES = 480
DIALOGUE_SLOT_UNITS = DIALOGUE_SLOT_BYTES // 2  # 240 UTF-16 code units


def decode_slot(slot: bytes) -> str:
    """Mirror engine DecodeString: per UTF-16 unit until NUL, unit-1 if unit>33."""
    units = struct.unpack(f"<{DIALOGUE_SLOT_UNITS}H", slot[:DIALOGUE_SLOT_BYTES])
    out = []
    for u in units:
        if u == 0:
            break
        out.append(u - 1 if u > 33 else u)
    return "".join(chr(c) for c in out)


def encode_slot(text: str) -> bytes:
    """Inverse of decode_slot -> one 480-byte slot. Raises if the text won't fit."""
    units = [(ord(ch) + 1 if ord(ch) >= 33 else ord(ch)) for ch in text]
    units.append(0)  # NUL terminator
    if len(units) > DIALOGUE_SLOT_UNITS:
        raise ValueError(f"quote too long: {len(text)} chars > {DIALOGUE_SLOT_UNITS - 1}")
    units += [0] * (DIALOGUE_SLOT_UNITS - len(units))
    return struct.pack(f"<{DIALOGUE_SLOT_UNITS}H", *units)


def pack_edt(quotes: list[str]) -> bytes:
    """quotes[i] becomes quote-number i. Gaps allowed via empty strings."""
    return b"".join(encode_slot(q) for q in quotes)


def unpack_edt(data: bytes) -> list[str]:
    n = len(data) // DIALOGUE_SLOT_BYTES
    return [decode_slot(data[i * DIALOGUE_SLOT_BYTES:(i + 1) * DIALOGUE_SLOT_BYTES]) for i in range(n)]
