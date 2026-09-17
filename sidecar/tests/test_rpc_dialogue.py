"""Tests for the RPC dialogue codecs (rpc_data) + authoring layer (rpc_dialogue).

Load-bearing: the .NPC record + .EDT slot round-trip byte-exactly, active_record
bakes in the recruit-safe sentinels (usGoToGridno=65535, triggers=255), and the
branch spec survives build -> decode for leadership / give-item / fact-gated.
"""

import pytest
from pydantic import ValidationError

from mercwizard_core import rpc_data as R
from mercwizard_core import rpc_dialogue as D
from routes.rpc_dialogue import DialogueSpec


# ── codecs ──────────────────────────────────────────────────────────────────

def test_npc_file_is_1600_bytes():
    assert len(R.pack_npc_file([R.active_record(ubApproachRequired=4)])) == 1600


def test_record_roundtrip():
    rec = R.active_record(ubApproachRequired=4, ubOpinionRequired=30, ubQuoteNum=11,
                          sActionData=4)
    back = R.unpack_record(R.pack_record(rec))
    for k in ("ubApproachRequired", "ubOpinionRequired", "ubQuoteNum", "sActionData"):
        assert back[k] == rec[k]


def test_active_record_recruit_safe_sentinels():
    rec = R.active_record(ubApproachRequired=4)
    assert rec["usGoToGridno"] == 65535        # else recruit action silently never fires
    assert rec["ubTriggerNPC"] == 255          # else a squadmate blurts "I see an enemy!"
    assert rec["ubTriggerNPCRec"] == 255
    assert rec["ubLastDay"] == 255             # valid all game


def test_edt_slot_roundtrip_and_obfuscation():
    text = "A friend at last. Lead on."
    slot = R.encode_slot(text)
    assert len(slot) == 480
    assert R.decode_slot(slot) == text


def test_edt_quote_too_long_raises():
    import pytest
    with pytest.raises(ValueError):
        R.encode_slot("x" * 240)


def test_edt_pack_unpack_roundtrip():
    quotes = ["intro", "", "friendly", "you're hired"]
    assert R.unpack_edt(R.pack_edt(quotes))[:4] == quotes


# ── authoring layer ─────────────────────────────────────────────────────────

def test_dialogue_spec_rejects_empty_recruit_branches():
    with pytest.raises(ValidationError):
        DialogueSpec(voice_index=63, branches=[], pre_quotes=[], post_quotes=[])


def test_build_and_decode_leadership_branch():
    branches = [{"approach": D.APPROACH_RECRUIT, "opinion_required": 30, "accept_quote": 11}]
    npc = R.pack_npc_file(D.build_npc_records(branches))
    back = D.decode_npc_records(npc)
    assert len(back) == 1
    assert back[0]["approach"] == D.APPROACH_RECRUIT
    assert back[0]["opinion_required"] == 30
    assert back[0]["accept_quote"] == 11
    assert back[0]["fact_must_be_true"] is None


def test_build_and_decode_give_item_branch():
    branches = [{"approach": D.APPROACH_GIVINGITEM, "required_item": 1565, "accept_quote": 12}]
    recs = D.build_npc_records(branches)
    assert recs[0]["usGiftItem"] == 0          # gift-item assert trap
    back = D.decode_npc_records(R.pack_npc_file(recs))
    assert back[0]["required_item"] == 1565


def test_give_item_branch_rejects_item_zero():
    """Item 0 is NONE, so a give-item recruit gate using it can never fire."""
    import pytest

    with pytest.raises(ValueError, match="positive required_item"):
        D.build_npc_records([
            {"approach": D.APPROACH_GIVINGITEM, "required_item": 0, "accept_quote": 10},
        ])


def test_build_and_decode_fact_gated_branch():
    branches = [{"approach": D.APPROACH_RECRUIT, "opinion_required": 0,
                 "fact_must_be_true": 450, "accept_quote": 10}]
    back = D.decode_npc_records(R.pack_npc_file(D.build_npc_records(branches)))
    assert back[0]["fact_must_be_true"] == 450


def test_multiple_branches_first_match_order_preserved():
    branches = [
        {"approach": D.APPROACH_RECRUIT, "fact_must_be_true": 450, "accept_quote": 10},
        {"approach": D.APPROACH_RECRUIT, "opinion_required": 30, "accept_quote": 11},
        {"approach": D.APPROACH_GIVINGITEM, "required_item": 1565, "accept_quote": 12},
    ]
    back = D.decode_npc_records(R.pack_npc_file(D.build_npc_records(branches)))
    assert [b["accept_quote"] for b in back] == [10, 11, 12]


def test_pre_edt_padded_to_cover_referenced_quotes():
    branches = [{"approach": D.APPROACH_RECRUIT, "accept_quote": 12}]
    pre = ["intro"]                             # only 1 quote, but branch references #12
    edt = D.build_pre_edt(pre, branches)
    assert len(edt) // R.DIALOGUE_SLOT_BYTES >= 13


# ── lossy-overwrite guard (routes) ───────────────────────────────────────────

def test_lossy_false_for_tool_authored_and_empty():
    from routes.rpc_dialogue import _npc_is_lossy
    branches = [{"approach": 4, "opinion_required": 30, "accept_quote": 11}]
    assert _npc_is_lossy(R.pack_npc_file(D.build_npc_records(branches))) is False
    assert _npc_is_lossy(b"") is False


def test_lossy_true_when_record_has_unmodeled_fields():
    from routes.rpc_dialogue import _npc_is_lossy
    # a recruit record that ALSO sets a fact — the branch spec doesn't model
    # usSetFactTrue, so a save would drop it.
    rec = R.active_record(ubApproachRequired=4, ubOpinionRequired=30, ubQuoteNum=11,
                          sActionData=4, usSetFactTrue=99)
    assert _npc_is_lossy(R.pack_npc_file([rec])) is True


def test_lossy_true_for_quote_only_record_and_bad_format():
    from routes.rpc_dialogue import _npc_is_lossy
    quote_only = R.new_record(ubQuoteNum=5, usSetFactTrue=42)   # approach==0 -> decode drops it
    assert _npc_is_lossy(R.pack_npc_file([quote_only])) is True
    assert _npc_is_lossy(b"not an old-format npc file") is True
