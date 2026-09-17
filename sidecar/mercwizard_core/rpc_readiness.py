"""Read-only closure audit for a profile-backed recruitable RPC."""
from __future__ import annotations

import re
from typing import Any

from . import (
    rpc_data as R,
    rpc_dialogue as D,
    rpc_facessmall as SF,
    rpc_fact_setter as FS,
    rpc_placement as P,
)
from .inject import profiles_xml
from .portrait.sti import verify_animated_face_sti


def _stage(status: str, detail: str, **data: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail, **data}


def _read_text(path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _as_int(raw: dict[str, str], key: str, default: int = 0) -> int:
    try:
        return int((raw.get(key) or str(default)).strip())
    except (ValueError, AttributeError):
        return default


def _coords_fit(x: int, y: int, region: tuple[int, int], canvas: tuple[int, int]) -> bool:
    width, height = region
    canvas_w, canvas_h = canvas
    return x >= 0 and y >= 0 and x + width <= canvas_w and y + height <= canvas_h


def inspect_rpc_readiness(ctx, profile: int) -> dict[str, Any]:
    """Return required/advisory stages without modifying the install."""
    raw = profiles_xml.read_slot(ctx.profiles_xml_path(), profile)
    if raw is None:
        stages = {
            "profile": _stage("blocked", f"Profile slot {profile} does not exist."),
            "portraits": _stage("blocked", "Create the profile before compiling RPC faces."),
            "placement": _stage("blocked", "No profile placement can be checked."),
            "recruitment": _stage("blocked", "No recruit record can be checked."),
            "dialogue": _stage("blocked", "No dialogue can be checked."),
            "post_recruit": _stage("advisory", "Post-recruit barks and voice are optional."),
        }
        return {"profile": profile, "ready": False, "stages": stages}

    profile_type = _as_int(raw, "Type", -1)
    face_index = _as_int(raw, "ubFaceIndex", profile)
    voice_index = _as_int(raw, "usVoiceIndex", 0)
    if profile_type != 3:
        profile_stage = _stage("blocked", f"Profile Type is {profile_type}; recruitable RPCs use Type 3.")
    elif voice_index <= 0:
        profile_stage = _stage("blocked", "Voice index is 0; assign a profile-specific voice index.")
    else:
        profile_stage = _stage("ready", f"Type 3 profile in slot {profile}; voice index {voice_index}.")

    normal_missing = [
        name for name in ("smallface", "face_65", "face_33", "bigface")
        if ctx.face_sti_bytes(face_index, size=name) is None
    ]
    talk_source = ctx.rpc_talkface_bytes(face_index)
    talk_valid = False
    talk_info: dict[str, Any] = {}
    if talk_source is not None:
        try:
            talk_info = verify_animated_face_sti(
                talk_source[0], expected_base_size=(90, 100),
            )
            talk_valid = bool(talk_info["valid"])
        except Exception:
            talk_valid = False
    small_override = SF.read_override(_read_text(ctx.rpc_faces_small_path()), profile)
    portrait_problems: list[str] = []
    if normal_missing:
        portrait_problems.append("missing " + ", ".join(normal_missing))
    if not talk_valid:
        portrait_problems.append("missing or invalid 90x100 eight-frame talk face")
    if small_override is None:
        portrait_problems.append("missing 48x43 RPCFacesSmall coordinate override")
    else:
        if not _coords_fit(
            int(small_override["eyesX"]), int(small_override["eyesY"]), (17, 6), (48, 43),
        ):
            portrait_problems.append("48x43 eye coordinates place the animation outside the small face")
        if not _coords_fit(
            int(small_override["mouthX"]), int(small_override["mouthY"]), (14, 6), (48, 43),
        ):
            portrait_problems.append("48x43 mouth coordinates place the animation outside the small face")
    if talk_valid:
        eye_size = talk_info.get("eye_subframe_size") or (0, 0)
        mouth_size = talk_info.get("mouth_subframe_size") or (0, 0)
        if not _coords_fit(
            _as_int(raw, "usEyesX"), _as_int(raw, "usEyesY"), eye_size, (90, 100),
        ):
            portrait_problems.append("profile eye coordinates place the animation outside the 90x100 talk face")
        if not _coords_fit(
            _as_int(raw, "usMouthX"), _as_int(raw, "usMouthY"), mouth_size, (90, 100),
        ):
            portrait_problems.append("profile mouth coordinates place the animation outside the 90x100 talk face")
    portraits_stage = (
        _stage("blocked", "; ".join(portrait_problems), face_index=face_index)
        if portrait_problems else
        _stage("ready", "Normal portraits, 90x100 talk face, and 48x43 override are present.", face_index=face_index)
    )

    placement_text = _read_text(ctx.game_init_lua_path())
    placements = P.read_placements(placement_text)
    managed = [p for p in placements["managed"] if p.profile == profile]
    hand = [p for p in placements["handAuthored"] if p.profile == profile]
    if len(managed) + len(hand) == 0:
        placement_stage = _stage("blocked", "No InitialProfile placement exists for this RPC.")
    elif len(managed) + len(hand) > 1:
        placement_stage = _stage("blocked", "More than one placement exists; the RPC would be inserted twice.")
    else:
        placed = (managed or hand)[0]
        owner = "managed" if managed else "hand-authored"
        placement_stage = _stage(
            "ready", f"{owner.capitalize()} placement at {placed.sector}, grid {placed.gridno}.",
            sector=placed.sector, gridno=placed.gridno, managed=bool(managed),
        )

    npc_path = ctx.rpc_npc_records_path(profile)
    recruit_records: list[dict] = []
    npc_error = ""
    if npc_path.exists():
        try:
            recruit_records = [
                rec for rec in R.unpack_npc_file(npc_path.read_bytes())
                if rec.get("ubApproachRequired") and rec.get("sActionData") == D.ACTION_RECRUIT
            ]
        except ValueError as exc:
            npc_error = str(exc)
    if npc_error:
        recruitment_stage = _stage("blocked", f"Recruit file cannot be read: {npc_error}")
    elif not recruit_records:
        recruitment_stage = _stage("blocked", "No branch dispatches the Recruit action.")
    else:
        invalid_items = [
            rec for rec in recruit_records
            if rec["ubApproachRequired"] == D.APPROACH_GIVINGITEM
            and int(rec["sRequiredItem"]) <= 0
        ]
        required_facts = {
            int(rec["usFactMustBeTrue"])
            for rec in recruit_records
            if int(rec["usFactMustBeTrue"]) not in (0, R.NO_FACT)
        }
        strategic_text = _read_text(ctx.strategicmap_lua_path())
        managed_facts = {
            setter.fact for setter in FS.read_fact_setters(strategic_text)
            if setter.profile == profile
        }
        # Preserve hand-authored strategicmap.lua gates too.  They are not
        # editable by the managed trigger UI, but a literal SetFactTrue call
        # is enough evidence that the gate has an author-provided setter.
        authored_facts = {
            fact for fact in required_facts
            if re.search(rf"\bSetFactTrue\s*\(\s*{fact}\s*\)", strategic_text)
        }
        unresolved_facts = sorted(required_facts - managed_facts - authored_facts)
        always = any(
            rec["ubApproachRequired"] == D.APPROACH_RECRUIT
            and rec["ubOpinionRequired"] == 0
            and rec["usFactMustBeTrue"] in (0, R.NO_FACT)
            for rec in recruit_records
        )
        detail = f"{len(recruit_records)} recruit branch(es)"
        detail += "; includes an always-available Recruit approach." if always else "; all branches are conditional."
        if invalid_items:
            recruitment_stage = _stage(
                "blocked", detail + " Give-item branch requires item 0 (NONE), so it can never fire.",
                branch_count=len(recruit_records), always_available=always,
            )
        elif unresolved_facts:
            names = ", ".join(f"fact {fact}" for fact in unresolved_facts)
            recruitment_stage = _stage(
                "blocked", detail + f" No strategicmap.lua setter was found for {names}.",
                branch_count=len(recruit_records), always_available=always,
                unresolved_facts=unresolved_facts,
            )
        else:
            recruitment_stage = _stage(
                "ready", detail, branch_count=len(recruit_records), always_available=always,
            )

    pre_path = ctx.rpc_pre_edt_path(voice_index)
    quotes: list[str] = []
    if pre_path.exists():
        quotes = R.unpack_edt(pre_path.read_bytes())
    referenced = sorted({int(rec["ubQuoteNum"]) for rec in recruit_records})
    blank = [n for n in referenced if n >= len(quotes) or not quotes[n].strip()]
    if not quotes:
        dialogue_stage = _stage("blocked", f"NpcData/{voice_index:03d}.EDT is missing or empty.")
    elif blank:
        dialogue_stage = _stage("blocked", "Recruit accept quote(s) are blank: " + ", ".join(map(str, blank)), blank_quotes=blank)
    else:
        dialogue_stage = _stage("ready", "Pre-recruit dialogue and every referenced accept quote are present.")

    post_path = ctx.rpc_post_edt_path(voice_index)
    post_quotes = R.unpack_edt(post_path.read_bytes()) if post_path.exists() else []
    post_count = sum(1 for q in post_quotes if q.strip())
    post_stage = (
        _stage("ready", f"{post_count} post-recruit bark(s) present.", count=post_count)
        if post_count else
        _stage("advisory", "Post-recruit barks and voice are optional; add them for a finished character.", count=0)
    )

    stages = {
        "profile": profile_stage,
        "portraits": portraits_stage,
        "placement": placement_stage,
        "recruitment": recruitment_stage,
        "dialogue": dialogue_stage,
        "post_recruit": post_stage,
    }
    required = ("profile", "portraits", "placement", "recruitment", "dialogue")
    return {
        "profile": profile,
        "face_index": face_index,
        "voice_index": voice_index,
        "ready": all(stages[name]["status"] == "ready" for name in required),
        "stages": stages,
    }
