"""End-to-end readiness classification for a profile-backed recruitable RPC."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from mercwizard_core import (
    rpc_data as R,
    rpc_dialogue as D,
    rpc_facessmall as SF,
    rpc_placement as P,
)
from mercwizard_core.install_context import make_install_context
from mercwizard_core.portrait.animate_skip import BoundingBox
from mercwizard_core.portrait.compile import compile_and_write_all
from mercwizard_core.portrait.talkface import build_talkface, write_talkface
from mercwizard_core.rpc_readiness import inspect_rpc_readiness


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (200, 200), (120, 90, 65, 255)).save(out, format="PNG")
    return out.getvalue()


def _seed_ready_rpc(root: Path) -> None:
    table = root / "Data-1.13" / "TableData"
    table.mkdir(parents=True)
    (table / "MercProfiles.xml").write_text(
        "<PROFILES><PROFILE><uiIndex>63</uiIndex><ubFaceIndex>63</ubFaceIndex>"
        "<Type>3</Type><usVoiceIndex>63</usVoiceIndex><usEyesX>20</usEyesX>"
        "<usEyesY>25</usEyesY><usMouthX>18</usMouthX><usMouthY>60</usMouthY>"
        "</PROFILE></PROFILES>", encoding="utf-8",
    )
    (table / "RPCFacesSmall.xml").write_text(
        SF.upsert("", 63, "Jay", 10, 8, 7, 28), encoding="utf-8",
    )
    scripts = root / "Data-1.13" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "GameInit.lua").write_text(
        P.apply_upsert("function InitNPCs()\nend\n", 63, 9, 1, 0, 13979, "Jay"),
        encoding="utf-8",
    )
    npc = root / "Data-1.13" / "NpcData"
    npc.mkdir(parents=True)
    branch = [{"approach": 4, "opinion_required": 0, "accept_quote": 10}]
    (npc / "063.NPC").write_bytes(R.pack_npc_file(D.build_npc_records(branch)))
    quotes = [""] * 11
    quotes[0] = "Need another gun?"
    quotes[10] = "All right. I'm in."
    (npc / "063.EDT").write_bytes(D.build_pre_edt(quotes, branch))

    compile_and_write_all(root, 63, _png())
    built = build_talkface(
        _png(), explicit_eye_pngs=None, explicit_mouth_pngs=None,
        small_eye_box=BoundingBox(10, 8, 17, 6),
        small_mouth_box=BoundingBox(7, 28, 14, 6),
    )
    write_talkface(root / "Data-1.13" / "faces" / "B63.sti", built)


def test_ready_rpc_passes_all_required_stages(tmp_path: Path):
    _seed_ready_rpc(tmp_path)
    result = inspect_rpc_readiness(make_install_context(tmp_path), 63)

    assert result["ready"] is True
    assert result["stages"]["profile"]["status"] == "ready"
    assert result["stages"]["portraits"]["status"] == "ready"
    assert result["stages"]["placement"]["status"] == "ready"
    assert result["stages"]["recruitment"]["status"] == "ready"
    assert result["stages"]["dialogue"]["status"] == "ready"


def test_duplicate_placement_and_blank_accept_quote_block_readiness(tmp_path: Path):
    _seed_ready_rpc(tmp_path)
    game_init = tmp_path / "Data-1.13" / "Scripts" / "GameInit.lua"
    game_init.write_text(
        game_init.read_text(encoding="utf-8")
        + "\nInitialProfile( 63, 9, 1, 0, 100 ) -- duplicate\n",
        encoding="utf-8",
    )
    npc_edt = tmp_path / "Data-1.13" / "NpcData" / "063.EDT"
    quotes = R.unpack_edt(npc_edt.read_bytes())
    quotes[10] = ""
    npc_edt.write_bytes(R.pack_edt(quotes))

    result = inspect_rpc_readiness(make_install_context(tmp_path), 63)
    assert result["ready"] is False
    assert result["stages"]["placement"]["status"] == "blocked"
    assert result["stages"]["dialogue"]["status"] == "blocked"


def test_missing_talkface_is_reported_without_crashing(tmp_path: Path):
    _seed_ready_rpc(tmp_path)
    (tmp_path / "Data-1.13" / "faces" / "B63.sti").unlink()

    result = inspect_rpc_readiness(make_install_context(tmp_path), 63)
    assert result["ready"] is False
    assert result["stages"]["portraits"]["status"] == "blocked"
    assert "90x100" in result["stages"]["portraits"]["detail"]


def test_zero_item_recruit_branch_blocks_readiness(tmp_path: Path):
    _seed_ready_rpc(tmp_path)
    npc = tmp_path / "Data-1.13" / "NpcData" / "063.NPC"
    rec = R.active_record(
        ubApproachRequired=D.APPROACH_GIVINGITEM,
        sRequiredItem=0,
        sActionData=D.ACTION_RECRUIT,
        ubQuoteNum=10,
    )
    npc.write_bytes(R.pack_npc_file([rec]))

    result = inspect_rpc_readiness(make_install_context(tmp_path), 63)

    assert result["ready"] is False
    assert result["stages"]["recruitment"]["status"] == "blocked"
    assert "item 0" in result["stages"]["recruitment"]["detail"]


def test_fact_gate_without_matching_setter_blocks_readiness(tmp_path: Path):
    _seed_ready_rpc(tmp_path)
    npc = tmp_path / "Data-1.13" / "NpcData" / "063.NPC"
    rec = R.active_record(
        ubApproachRequired=D.APPROACH_RECRUIT,
        usFactMustBeTrue=450,
        sActionData=D.ACTION_RECRUIT,
        ubQuoteNum=10,
    )
    npc.write_bytes(R.pack_npc_file([rec]))

    result = inspect_rpc_readiness(make_install_context(tmp_path), 63)

    assert result["ready"] is False
    assert result["stages"]["recruitment"]["status"] == "blocked"
    assert "fact 450" in result["stages"]["recruitment"]["detail"]


def test_hand_authored_fact_setter_with_flexible_spacing_satisfies_readiness(tmp_path: Path):
    _seed_ready_rpc(tmp_path)
    npc = tmp_path / "Data-1.13" / "NpcData" / "063.NPC"
    npc.write_bytes(R.pack_npc_file([R.active_record(
        ubApproachRequired=D.APPROACH_RECRUIT,
        usFactMustBeTrue=450,
        sActionData=D.ACTION_RECRUIT,
        ubQuoteNum=10,
    )]))
    strategic = tmp_path / "Data-1.13" / "Scripts" / "strategicmap.lua"
    strategic.parent.mkdir(parents=True, exist_ok=True)
    strategic.write_text("function unlock_jay() SetFactTrue ( 450 ) end\n", encoding="utf-8")

    result = inspect_rpc_readiness(make_install_context(tmp_path), 63)

    assert result["ready"] is True
    assert result["stages"]["recruitment"]["status"] == "ready"


def test_out_of_bounds_face_coordinates_block_readiness(tmp_path: Path):
    _seed_ready_rpc(tmp_path)
    small = tmp_path / "Data-1.13" / "TableData" / "RPCFacesSmall.xml"
    small.write_text(SF.upsert("", 63, "Jay", 40, 40, 40, 40), encoding="utf-8")

    result = inspect_rpc_readiness(make_install_context(tmp_path), 63)

    assert result["ready"] is False
    assert result["stages"]["portraits"]["status"] == "blocked"
    assert "outside" in result["stages"]["portraits"]["detail"]
