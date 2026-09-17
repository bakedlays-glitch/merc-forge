"""RPC recruitment + dialogue authoring — reads/writes the .NPC + .EDT files.

Authors three files that make a Type=3 profile a talkable, recruitable RPC:
  NpcData/<profile:03d>.NPC   quote records (recruit branches)
  NpcData/<voice:03d>.EDT     pre-recruit dialogue text
  MercEdt/<voice:03d>.EDT     post-recruit barks

File naming verified against the engine (see install_context.rpc_* helpers).
Recruit branches are first-match-wins in order; three condition kinds
(leadership / give-item / fact-gated). The Lua fact-setter that flips a
fact-gated recruit's fact is a separate layer.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mercwizard_core import (
    backup,
    rpc_data as R,
    rpc_dialogue as D,
    rpc_fact_setter as FS,
    rpc_facessmall as SF,
    rpc_readiness as RR,
)
from mercwizard_core.cross_lock import cross_process_install_root_lock
from mercwizard_core.install_context import make_install_context

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


@router.get("/rpc/readiness/{profile}")
def get_rpc_readiness(
    profile: int,
    install_id: Optional[str] = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    return RR.inspect_rpc_readiness(make_install_context(info.path), profile)


class Branch(BaseModel):
    approach: int                       # 4 = recruit (leadership), 6 = give-item
    opinion_required: int = 0
    required_item: int = 0
    fact_must_be_true: Optional[int] = None
    accept_quote: int = 0


class DialogueSpec(BaseModel):
    voice_index: int
    branches: list[Branch] = Field(min_length=1)
    pre_quotes: list[str]
    post_quotes: list[str] = []


class DialogueResponse(BaseModel):
    profile: int
    voice_index: int
    standard_quote_labels: list[str]
    branches: list[Branch]
    pre_quotes: list[str]
    post_quotes: list[str]
    npc_exists: bool
    lossy: bool = False           # existing .NPC has logic this editor can't represent
    warnings: list[str] = []
    backup_id: Optional[str] = None


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes() if path.exists() else b""


def _npc_is_lossy(npc_bytes: bytes) -> bool:
    """True if this editor cannot faithfully re-author the file: a save would
    drop records/fields it doesn't model (quote-only records, quest gates,
    usSetFactTrue, multi-quote, gridno triggers) or it's a new-format .NPC."""
    if not npc_bytes:
        return False
    try:
        decoded = D.decode_npc_records(npc_bytes)
        return R.pack_npc_file(D.build_npc_records(decoded)) != npc_bytes
    except ValueError:
        return True


def _voice_warnings(profile: int, voice: int) -> list[str]:
    w: list[str] = []
    if voice == 0:
        w.append("Voice index is 0 — dialogue would write 000.EDT, the engine's default "
                 "namespace. Give this merc a real voice index (usually = its profile id).")
    elif voice != profile:
        w.append(f"Voice index {voice} differs from profile {profile}; the dialogue EDT lands "
                 f"in voice {voice}'s file, which other mercs may share. RPC convention is "
                 f"voice index = profile id.")
    return w


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".mwtmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


@router.get("/rpc/dialogue/{profile}", response_model=DialogueResponse)
def get_dialogue(
    profile: int,
    voice_index: int = Query(...),
    install_id: Optional[str] = Query(default=None),
) -> DialogueResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)

    npc_path = ctx.rpc_npc_records_path(profile, for_write=False)
    pre_path = ctx.rpc_pre_edt_path(voice_index, for_write=False)
    post_path = ctx.rpc_post_edt_path(voice_index, for_write=False)

    branches: list[Branch] = []
    pre_quotes: list[str] = []
    post_quotes: list[str] = []
    npc_bytes = _read_bytes(npc_path)
    if npc_bytes:
        try:
            branches = [Branch(**b) for b in D.decode_npc_records(npc_bytes)]
        except ValueError:
            branches = []  # not an old-format .NPC; present as empty
    pre_bytes = _read_bytes(pre_path)
    if pre_bytes:
        pre_quotes = D.decode_edt(pre_bytes)
    post_bytes = _read_bytes(post_path)
    if post_bytes:
        post_quotes = D.decode_edt(post_bytes)

    return DialogueResponse(
        profile=profile,
        voice_index=voice_index,
        standard_quote_labels=D.STANDARD_QUOTE_LABELS,
        branches=branches,
        pre_quotes=pre_quotes,
        post_quotes=post_quotes,
        npc_exists=bool(npc_bytes),
        lossy=_npc_is_lossy(npc_bytes),
        warnings=_voice_warnings(profile, voice_index),
    )


@router.put("/rpc/dialogue/{profile}", response_model=DialogueResponse)
def put_dialogue(
    profile: int,
    spec: DialogueSpec,
    install_id: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
) -> DialogueResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    state = get_state()

    branch_dicts = [b.model_dump() for b in spec.branches]
    for b in branch_dicts:
        if b["approach"] not in (D.APPROACH_RECRUIT, D.APPROACH_GIVINGITEM):
            raise HTTPException(status_code=422, detail=f"Bad approach {b['approach']} (expect 4 or 6).")

    try:
        npc_bytes = R.pack_npc_file(D.build_npc_records(branch_dicts))
        pre_bytes = D.build_pre_edt(spec.pre_quotes, branch_dicts)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    npc_path = ctx.rpc_npc_records_path(profile, for_write=True)
    pre_path = ctx.rpc_pre_edt_path(spec.voice_index, for_write=True)
    post_path = ctx.rpc_post_edt_path(spec.voice_index, for_write=True)

    # Only write post-recruit EDT when there's actual bark text — never emit a
    # 0-byte or all-empty MercEdt file that would shadow a shared voice's barks.
    has_post = any(q.strip() for q in spec.post_quotes)

    with cross_process_install_root_lock(info.path), state.write_lock:
        # Refuse to clobber an existing .NPC this editor can't faithfully
        # represent (vanilla quote logic, new-format, quest gates) unless forced.
        if not force and _npc_is_lossy(_read_bytes(ctx.rpc_npc_records_path(profile, for_write=False))):
            raise HTTPException(
                status_code=409,
                detail="The existing .NPC contains dialogue logic this editor can't represent; "
                       "saving would discard it. Pass force=true to overwrite.",
            )

        writes = [(npc_path, npc_bytes), (pre_path, pre_bytes)]
        if has_post:
            writes.append((post_path, D.build_post_edt(spec.post_quotes)))
        paths = [p for p, _ in writes]
        existed = [p for p in paths if p.exists()]
        entry = backup.snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=paths, reason="rpc-dialogue",
        )
        for p, b in writes:
            _atomic_write_bytes(p, b)
        created = [p for p in paths if p not in existed]
        if created:
            backup.record_files_created(entry.id, info.id, created)

    return DialogueResponse(
        profile=profile,
        voice_index=spec.voice_index,
        standard_quote_labels=D.STANDARD_QUOTE_LABELS,
        branches=spec.branches,
        pre_quotes=spec.pre_quotes,
        post_quotes=spec.post_quotes,
        npc_exists=True,
        warnings=_voice_warnings(profile, spec.voice_index),
        backup_id=entry.id,
    )


# ── Carried-item -> fact triggers (strategicmap.lua) ─────────────────────────

class FactSetterModel(BaseModel):
    profile: int
    sector: str
    col: int
    row: int
    z: int
    item: int
    fact: int

    @classmethod
    def of(cls, fs: FS.FactSetter) -> "FactSetterModel":
        return cls(profile=fs.profile, sector=fs.sector, col=fs.col, row=fs.row,
                   z=fs.z, item=fs.item, fact=fs.fact)


class SetFactSetterRequest(BaseModel):
    profile: int
    sector: str            # e.g. "A9"
    item: int
    fact: int
    z: int = 0


class FactSettersResponse(BaseModel):
    install_id: str
    lua_path: str
    setters: list[FactSetterModel]
    backup_id: Optional[str] = None


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".mwtmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def _strat_text(ctx) -> str:
    r = ctx.strategicmap_lua_path(for_write=False)
    w = ctx.strategicmap_lua_path(for_write=True)
    return _read_text(w if w.exists() else r)


@router.get("/rpc/fact-setters", response_model=FactSettersResponse)
def list_fact_setters(install_id: Optional[str] = Query(default=None)) -> FactSettersResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    setters = FS.read_fact_setters(_strat_text(ctx))
    return FactSettersResponse(
        install_id=info.id,
        lua_path=str(ctx.strategicmap_lua_path(for_write=True)),
        setters=[FactSetterModel.of(s) for s in setters],
    )


@router.put("/rpc/fact-setters", response_model=FactSetterModel)
def set_fact_setter(
    req: SetFactSetterRequest,
    install_id: Optional[str] = Query(default=None),
) -> FactSetterModel:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    state = get_state()
    try:
        col, row = FS.sector_to_colrow(req.sector)
        FS.validate_fact(req.fact)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    fs = FS.FactSetter(profile=req.profile, col=col, row=row, z=req.z, item=req.item, fact=req.fact)
    write_path = ctx.strategicmap_lua_path(for_write=True)
    with cross_process_install_root_lock(info.path), state.write_lock:
        text = _strat_text(ctx)
        try:
            new_text = FS.apply_upsert(text, fs)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))  # function not found
        backup.snapshot(install_root=info.path, install_id=info.id,
                        files_to_back_up=[write_path], reason="rpc-fact-setter")
        _atomic_write_text(write_path, new_text)
    return FactSetterModel.of(fs)


@router.delete("/rpc/fact-setters/{profile}", response_model=FactSettersResponse)
def remove_fact_setter(
    profile: int,
    install_id: Optional[str] = Query(default=None),
) -> FactSettersResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    state = get_state()
    write_path = ctx.strategicmap_lua_path(for_write=True)
    with cross_process_install_root_lock(info.path), state.write_lock:
        text = _strat_text(ctx)
        new_text = FS.apply_remove(text, profile)
        backup.snapshot(install_root=info.path, install_id=info.id,
                        files_to_back_up=[write_path], reason="rpc-fact-setter")
        _atomic_write_text(write_path, new_text)
        setters = FS.read_fact_setters(new_text)
    return FactSettersResponse(
        install_id=info.id,
        lua_path=str(write_path),
        setters=[FactSetterModel.of(s) for s in setters],
    )


# ── RPC small-face coord override (RPCFacesSmall.xml) ─────────────────────────

class SmallFaceOverride(BaseModel):
    name: str = ""
    eyesX: int
    eyesY: int
    mouthX: int
    mouthY: int


class SmallFaceResponse(BaseModel):
    profile: int
    override: Optional[SmallFaceOverride] = None
    backup_id: Optional[str] = None


@router.get("/rpc/small-face/{profile}", response_model=SmallFaceResponse)
def get_small_face(
    profile: int,
    install_id: Optional[str] = Query(default=None),
) -> SmallFaceResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    ov = SF.read_override(_read_text(ctx.rpc_faces_small_path(for_write=False)), profile)
    return SmallFaceResponse(profile=profile, override=SmallFaceOverride(**ov) if ov else None)


@router.put("/rpc/small-face/{profile}", response_model=SmallFaceResponse)
def set_small_face(
    profile: int,
    req: SmallFaceOverride,
    install_id: Optional[str] = Query(default=None),
) -> SmallFaceResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    state = get_state()
    write_path = ctx.rpc_faces_small_path(for_write=True)
    read_path = ctx.rpc_faces_small_path(for_write=False)
    with cross_process_install_root_lock(info.path), state.write_lock:
        text = _read_text(write_path if write_path.exists() else read_path)
        new_text = SF.upsert(text, profile, req.name, req.eyesX, req.eyesY, req.mouthX, req.mouthY)
        existed = write_path.exists()
        entry = backup.snapshot(install_root=info.path, install_id=info.id,
                                files_to_back_up=[write_path], reason="rpc-small-face")
        _atomic_write_text(write_path, new_text)
        if not existed:
            backup.record_files_created(entry.id, info.id, [write_path])
    return SmallFaceResponse(profile=profile, override=req, backup_id=entry.id)


@router.delete("/rpc/small-face/{profile}", response_model=SmallFaceResponse)
def remove_small_face(
    profile: int,
    install_id: Optional[str] = Query(default=None),
) -> SmallFaceResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    state = get_state()
    write_path = ctx.rpc_faces_small_path(for_write=True)
    read_path = ctx.rpc_faces_small_path(for_write=False)
    with cross_process_install_root_lock(info.path), state.write_lock:
        text = _read_text(write_path if write_path.exists() else read_path)
        new_text = SF.remove(text, profile)
        entry = backup.snapshot(install_root=info.path, install_id=info.id,
                                files_to_back_up=[write_path], reason="rpc-small-face")
        _atomic_write_text(write_path, new_text)
    return SmallFaceResponse(profile=profile, override=None, backup_id=entry.id)
