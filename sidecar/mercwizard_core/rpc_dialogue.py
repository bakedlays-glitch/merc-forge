"""Author an RPC's recruit logic + dialogue from a high-level spec.

Turns a spec (recruit branches + quote texts) into the two binary files:

  * ``<profile>.NPC``  — one quote record per recruit branch (rpc_data)
  * pre-recruit ``.EDT`` — the dialogue text, quote N at slot N

and decodes existing files back into the spec so the editor round-trips.

Recruit branches are FIRST-MATCH-WINS in order. Three condition kinds:
  * leadership  — approach RECRUIT + ``opinion_required`` (talk-desire threshold)
  * give-item   — approach GIVINGITEM + ``required_item``
  * fact-gated  — any branch may also require ``fact_must_be_true`` (e.g. a fact a
    Lua hook sets when the squad wears/carries an item). The record references the
    fact; SETTING it is the Lua-fact-setter layer, not this module.
"""
from __future__ import annotations

from . import rpc_data as R

APPROACH_RECRUIT = 4
APPROACH_GIVINGITEM = 6
ACTION_RECRUIT = 4  # NPC_ACTION_RECRUIT (free join)

# Engine StandardQuoteIDs — quote slots 0-9 are auto-played by Converse; custom
# accept lines start at 10. Labels drive the editor.
STANDARD_QUOTE_LABELS = [
    "Intro", "Subsequent intro", "Friendly 1", "Friendly 2", "Give-item refused",
    "Direct", "Threaten", "Recruit refused", "Bye", "Get lost",
]
FIRST_CUSTOM_QUOTE = len(STANDARD_QUOTE_LABELS)  # 10


def _is_active(rec: dict) -> bool:
    return bool(rec.get("ubApproachRequired"))


def build_npc_records(branches: list[dict]) -> list[dict]:
    """One active record per branch. usGiftItem stays 0 (gift-item assert trap)."""
    recs: list[dict] = []
    for b in branches:
        approach = int(b["approach"])
        kw: dict = dict(
            ubApproachRequired=approach,
            sActionData=ACTION_RECRUIT,
            ubQuoteNum=int(b.get("accept_quote", 0)),
            usGiftItem=0,
        )
        if approach == APPROACH_GIVINGITEM:
            required_item = int(b.get("required_item") or 0)
            if required_item <= 0:
                raise ValueError("Give-item recruit branches require a positive required_item; item 0 is NONE.")
            kw["sRequiredItem"] = required_item
            kw["ubOpinionRequired"] = 0
        else:
            kw["ubOpinionRequired"] = int(b.get("opinion_required") or 0)
        fact = b.get("fact_must_be_true")
        if fact is not None:
            kw["usFactMustBeTrue"] = int(fact)
        recs.append(R.active_record(**kw))
    return recs


def decode_npc_records(npc_bytes: bytes) -> list[dict]:
    """Reverse of build_npc_records: active records -> branch spec dicts."""
    branches: list[dict] = []
    for rec in R.unpack_npc_file(npc_bytes):
        if not _is_active(rec):
            continue
        approach = rec["ubApproachRequired"]
        fact = rec["usFactMustBeTrue"]
        branches.append({
            "approach": approach,
            "opinion_required": rec["ubOpinionRequired"],
            "required_item": rec["sRequiredItem"] if approach == APPROACH_GIVINGITEM else 0,
            "fact_must_be_true": None if fact in (0, R.NO_FACT) else fact,
            "accept_quote": rec["ubQuoteNum"],
        })
    return branches


def _quote_capacity(branches: list[dict]) -> int:
    """Number of EDT slots needed so every branch's accept_quote resolves."""
    hi = FIRST_CUSTOM_QUOTE
    for b in branches:
        hi = max(hi, int(b.get("accept_quote", 0)) + 1)
    return hi


def build_pre_edt(pre_quotes: list[str], branches: list[dict]) -> bytes:
    """Pack pre-recruit quotes, padding so every referenced quote index exists."""
    quotes = list(pre_quotes)
    need = _quote_capacity(branches)
    if len(quotes) < need:
        quotes += [""] * (need - len(quotes))
    return R.pack_edt(quotes)


def build_post_edt(post_quotes: list[str]) -> bytes:
    return R.pack_edt(list(post_quotes))


def decode_edt(edt_bytes: bytes) -> list[str]:
    quotes = R.unpack_edt(edt_bytes)
    # Trim trailing empties (padding) but keep interior gaps.
    while quotes and not quotes[-1].strip():
        quotes.pop()
    return quotes
