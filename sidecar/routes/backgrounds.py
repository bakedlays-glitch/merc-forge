"""Backgrounds.xml reader + editor — exposes and mutates the install's catalog.

Backgrounds in JA2 1.13 are stat/AP/perk modifier bundles a merc can carry
(e.g. "Soldier" = +AP, "Doctor" = +healing, "Hunter" = +tracking). A merc's
`usBackground` field indexes into this catalog.

READ (`GET /backgrounds`): the picklist + the engine-derived field schema the
editor renders. WRITE (`POST`/`PUT`/`DELETE`/`imp-threshold`): create, edit,
delete a `<BACKGROUND>`, and control IMP-creation visibility.

Backgrounds.xml is a SHARED, install-wide file (every merc + IMP creation reads
it), so every write: takes the cross-process install lock, snapshots the file
first (restore via the Backups page), validates + clamps to the engine's
per-field ranges, refuses to touch the uiIndex-0 template, and edits only the
target block's bytes (preserving multi-line descriptions, nested drug lists,
and unknown mod columns). See `inject/backgrounds_xml.py` +
`backgrounds_schema.py` for the engine details.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mercwizard_core import backgrounds_schema as schema
from mercwizard_core.backup import snapshot
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.inject import backgrounds_xml as bg_xml
from mercwizard_core.install_context import make_install_context

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


# ── Response models ─────────────────────────────────────────────────────────

class BackgroundModifier(BaseModel):
    key: str
    value: int


class BackgroundEntry(BaseModel):
    id: int
    name: str
    short_name: str
    description: str
    modifiers: list[BackgroundModifier] = []
    # True when this id is enumerated by the IMP creation picker
    # (id <= num_found_background, i.e. at or before the last physical entry).
    imp_selectable: bool = False
    # True when the entry carries nested <drugtypes>/<drugitems> or non-zero
    # columns outside the editor's schema — these are preserved verbatim on save
    # but not shown in the form.
    has_advanced_data: bool = False


class BackgroundsResponse(BaseModel):
    backgrounds: list[BackgroundEntry]
    schema_fields: list[dict] = Field(default_factory=list)
    install_id: str
    file_present: bool
    writable: bool
    write_path: Optional[str] = None
    # The engine's IMP picker bound = the LAST physical entry's uiIndex.
    num_found_background: int = 0
    max_index: int = schema.MAX_INDEX
    name_max: int = schema.NAME_MAX
    short_name_max: int = schema.SHORT_NAME_MAX
    description_max: int = schema.DESCRIPTION_MAX
    duplicate_ids: list[int] = []


# ── Request models ──────────────────────────────────────────────────────────

class BackgroundCreateBody(BaseModel):
    name: str
    short_name: str = ""
    description: str = ""
    fields: dict[str, int] = Field(default_factory=dict)
    # Omit to auto-pick the next free id; provide to claim a specific id (1..499).
    ui_index: Optional[int] = None
    # When true, the new entry is placed physically last so it (and any
    # currently-hidden higher ids) appear in IMP character creation.
    make_imp_selectable: bool = False


class BackgroundUpdateBody(BaseModel):
    name: str
    short_name: str = ""
    description: str = ""
    # Only the keys present are touched (non-zero set, zero removed). The editor
    # submits every owned field, so a PUT fully syncs the owned columns.
    fields: dict[str, int] = Field(default_factory=dict)


class ImpThresholdBody(BaseModel):
    # Make this id (and everything below it) IMP-selectable by moving it last.
    ui_index: Optional[int] = None
    # Or expose every background by moving the highest id last.
    all: bool = False


# ── Shared helpers ──────────────────────────────────────────────────────────

def _resolve_write_path(info):
    """Return (ctx, write_path) or raise 400 when the install has no Backgrounds.xml."""
    ctx = make_install_context(info.path)
    write_path = ctx.extra_table_path("backgrounds", for_write=True)
    if write_path is None or not write_path.exists():
        raise HTTPException(status_code=400, detail={
            "error": "BACKGROUNDS_NOT_PRESENT",
            "message": (
                "This install has no Backgrounds.xml (TableData/Backgrounds.xml). "
                "The background editor can only edit an existing background table."
            ),
        })
    return ctx, write_path


# XML 1.0 allows only tab/LF/CR below 0x20; the other C0 controls are forbidden
# even as numeric character references. A raw one written into the no-<?xml?>
# Backgrounds.xml fails the engine's expat parse for the WHOLE file at boot, and
# entitizing can't rescue it — so reject at input.
_XML_ILLEGAL_ORDS = set(range(0x20)) - {0x09, 0x0A, 0x0D}


def _has_illegal_xml_char(s: str) -> bool:
    return any(ord(c) in _XML_ILLEGAL_ORDS for c in s)


def _validate(name: str, short_name: str, description: str,
              fields: dict[str, int]) -> tuple[dict[str, int], list[dict]]:
    """Validate caps + field membership; coerce flags; clamp ints to engine ranges.

    Returns (clean_fields, clamps). Raises HTTPException(400) on hard errors.
    """
    errors: list[str] = []
    if not name.strip():
        errors.append("Name is required.")
    if schema.utf16_len(name) > schema.NAME_MAX:
        errors.append(f"Name exceeds {schema.NAME_MAX} characters.")
    if schema.utf16_len(short_name) > schema.SHORT_NAME_MAX:
        errors.append(f"Short name exceeds {schema.SHORT_NAME_MAX} characters.")
    if schema.utf16_len(description) > schema.DESCRIPTION_MAX:
        errors.append(f"Description exceeds {schema.DESCRIPTION_MAX} characters.")
    for label, value in (("Name", name), ("Short name", short_name),
                         ("Description", description)):
        if _has_illegal_xml_char(value):
            errors.append(f"{label} contains a control character that can't be saved.")

    clean: dict[str, int] = {}
    clamps: list[dict] = []
    for key, raw in fields.items():
        spec = schema.get_spec(key)
        if spec is None:
            errors.append(f"Unknown field '{key}'.")
            continue
        if spec.kind == "flag":
            # Engine treats any non-zero as on; store the canonical 0/1.
            v = 1 if raw != 0 else 0
            if raw not in (0, 1):
                clamps.append({"key": key, "requested": raw, "stored": v})
        else:
            v, changed = schema.clamp_value(key, raw)
            if changed:
                clamps.append({"key": key, "requested": raw, "stored": v})
        clean[key] = v

    if errors:
        raise HTTPException(status_code=400, detail={
            "error": "BACKGROUND_INVALID", "message": " ".join(errors), "issues": errors,
        })
    return clean, clamps


def _bg_error_to_http(e: bg_xml.BackgroundError) -> HTTPException:
    status = {
        "BACKGROUND_NOT_FOUND": 404,
        "DUPLICATE_INDEX": 409,
        "INDEX_TAKEN": 409,
        "TABLE_FULL": 409,
        "TEMPLATE_PROTECTED": 400,
        "INVALID_INDEX": 400,
    }.get(e.code, 400)
    return HTTPException(status_code=status, detail={"error": e.code, "message": e.message})


# ── Read ────────────────────────────────────────────────────────────────────

@router.get("/backgrounds")
def list_backgrounds(install_id: Optional[str] = Query(default=None)) -> BackgroundsResponse:
    info = _resolve_install(install_id)
    ctx = make_install_context(info.path)
    read_path = ctx.extra_table_path("backgrounds")
    write_path = ctx.extra_table_path("backgrounds", for_write=True)
    file_present = bool(read_path and read_path.exists())
    writable = bool(write_path and write_path.exists())

    catalog = bg_xml.read_catalog(read_path) if read_path else bg_xml.Catalog([], 0, [])
    nfb = catalog.num_found_background
    entries = [
        BackgroundEntry(
            id=e.ui_index,
            name=e.name,
            short_name=e.short_name,
            description=e.description,
            modifiers=[BackgroundModifier(key=k, value=v) for k, v in e.modifiers],
            imp_selectable=e.ui_index <= nfb,
            has_advanced_data=e.has_nested or e.has_unknown,
        )
        for e in sorted(catalog.entries, key=lambda x: x.ui_index)
    ]
    return BackgroundsResponse(
        backgrounds=entries,
        schema_fields=schema.schema_payload(),
        install_id=info.id,
        file_present=file_present,
        writable=writable,
        write_path=str(write_path) if write_path else None,
        num_found_background=nfb,
        duplicate_ids=catalog.duplicate_ids,
    )


# ── Write ───────────────────────────────────────────────────────────────────

@router.post("/backgrounds")
def create_background(
    body: BackgroundCreateBody,
    install_id: Optional[str] = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    _ctx, write_path = _resolve_write_path(info)
    clean_fields, clamps = _validate(body.name, body.short_name, body.description, body.fields)

    if body.ui_index is not None and (
        body.ui_index <= schema.TEMPLATE_INDEX or body.ui_index > schema.MAX_INDEX
    ):
        raise HTTPException(status_code=400, detail={
            "error": "INVALID_INDEX",
            "message": f"uiIndex must be {schema.TEMPLATE_INDEX + 1}..{schema.MAX_INDEX}.",
        })

    with cross_process_install_lock(info.id), state.write_lock:
        snap = snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=[write_path], reason="background_create",
        )
        try:
            ui_index = body.ui_index
            if ui_index is None:
                ui_index = bg_xml.next_free_index(write_path)
            result = bg_xml.create_background(
                write_path, ui_index=ui_index, name=body.name,
                short_name=body.short_name, description=body.description,
                fields=clean_fields, make_imp_selectable=body.make_imp_selectable,
            )
        except bg_xml.BackgroundError as e:
            raise _bg_error_to_http(e)

    return {"ok": True, "backup_id": snap.id, "clamps": clamps, **result}


@router.put("/backgrounds/{ui_index}")
def update_background(
    ui_index: int,
    body: BackgroundUpdateBody,
    install_id: Optional[str] = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    _ctx, write_path = _resolve_write_path(info)
    if ui_index == schema.TEMPLATE_INDEX:
        raise HTTPException(status_code=400, detail={
            "error": "TEMPLATE_PROTECTED",
            "message": "uiIndex 0 is the template row and can't be edited.",
        })
    clean_fields, clamps = _validate(body.name, body.short_name, body.description, body.fields)

    with cross_process_install_lock(info.id), state.write_lock:
        snap = snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=[write_path], reason=f"background_edit_{ui_index}",
        )
        try:
            result = bg_xml.edit_background(
                write_path, ui_index=ui_index, name=body.name,
                short_name=body.short_name, description=body.description,
                fields=clean_fields,
            )
        except bg_xml.BackgroundError as e:
            raise _bg_error_to_http(e)

    return {"ok": True, "backup_id": snap.id, "clamps": clamps, **result}


@router.delete("/backgrounds/{ui_index}")
def delete_background(
    ui_index: int,
    install_id: Optional[str] = Query(default=None),
) -> dict:
    info = _resolve_install(install_id)
    state = get_state()
    _ctx, write_path = _resolve_write_path(info)
    if ui_index == schema.TEMPLATE_INDEX:
        raise HTTPException(status_code=400, detail={
            "error": "TEMPLATE_PROTECTED",
            "message": "uiIndex 0 is the template row and can't be deleted.",
        })

    with cross_process_install_lock(info.id), state.write_lock:
        snap = snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=[write_path], reason=f"background_delete_{ui_index}",
        )
        try:
            result = bg_xml.delete_background(write_path, ui_index=ui_index)
        except bg_xml.BackgroundError as e:
            raise _bg_error_to_http(e)

    return {"ok": True, "backup_id": snap.id, **result}


@router.post("/backgrounds/imp-threshold")
def set_imp_threshold(
    body: ImpThresholdBody,
    install_id: Optional[str] = Query(default=None),
) -> dict:
    """Control which backgrounds appear in IMP character creation.

    The engine shows ids 0..num_found_background, where num_found_background is
    the LAST physical entry's id. Passing `all=true` moves the highest id last
    (every background becomes selectable); passing `ui_index` moves that entry
    last (it + everything below it become selectable).
    """
    info = _resolve_install(install_id)
    state = get_state()
    _ctx, write_path = _resolve_write_path(info)
    if not body.all and body.ui_index is None:
        raise HTTPException(status_code=400, detail={
            "error": "BACKGROUND_INVALID",
            "message": "Provide ui_index, or all=true.",
        })

    with cross_process_install_lock(info.id), state.write_lock:
        snap = snapshot(
            install_root=info.path, install_id=info.id,
            files_to_back_up=[write_path], reason="background_imp_threshold",
        )
        try:
            if body.all:
                result = bg_xml.make_all_imp_selectable(write_path)
            else:
                result = bg_xml.set_imp_threshold(write_path, ui_index=body.ui_index)  # type: ignore[arg-type]
        except bg_xml.BackgroundError as e:
            raise _bg_error_to_http(e)

    return {"ok": True, "backup_id": snap.id, **result}
