"""Items editor routes — browse Items.xml, edit common + per-class stats,
re-point BIGITEMS graphics. Mirrors routes/backgrounds.py: every write takes the
cross-process install lock + snapshots each touched file to the Backups page,
validates + clamps to engine ranges, and refuses the uiIndex-0 template row.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from mercwizard_core import item_enums
from mercwizard_core import items_schema as schema
from mercwizard_core.backup import snapshot
from mercwizard_core.cross_lock import cross_process_install_lock
from mercwizard_core.inject import item_class_xml as cx
from mercwizard_core.inject import items_xml as ix
from mercwizard_core.install_context import make_install_context
from mercwizard_core.mapforge_engine import item_graphic as igph

from .roster import _resolve_install
from .state import get_state

router = APIRouter()


# ── Request model ────────────────────────────────────────────────────────────

class ItemUpdateBody(BaseModel):
    strings: dict[str, str] = Field(default_factory=dict)
    ints: dict[str, int] = Field(default_factory=dict)
    class_fields: dict[str, int] = Field(default_factory=dict)


# ── Shared helpers ───────────────────────────────────────────────────────────

def _items_path(info, filename: str, *, for_write: bool = False):
    """Resolve TableData/Items/<filename> through the VFS for this install."""
    ctx = make_install_context(info.path)
    return ctx.items_table_path(filename, for_write=for_write)


def _validate_common(
    strings: dict[str, str], ints: dict[str, int]
) -> tuple[dict[str, str], dict[str, int], list[dict]]:
    """Validate + clamp common (Items.xml) fields.

    Returns (clean_strings, clean_ints, clamps). Raises HTTPException(400)
    on unknown fields or string cap violations.
    """
    errors: list[str] = []
    clean_str: dict[str, str] = {}
    clean_int: dict[str, int] = {}
    clamps: list[dict] = []

    for key, val in strings.items():
        spec = schema.get_common_spec(key)
        if spec is None or spec.kind != "str":
            errors.append(f"Unknown string field '{key}'.")
            continue
        if schema.utf16_len(val) > spec.cap:
            errors.append(f"{spec.label} exceeds {spec.cap} characters.")
        clean_str[key] = val

    for key, val in ints.items():
        spec = schema.get_common_spec(key)
        if spec is None or spec.kind != "int":
            errors.append(f"Unknown numeric field '{key}'.")
            continue
        v, changed = schema.clamp_int(spec, val)
        if changed:
            clamps.append({"key": key, "requested": val, "stored": v})
        clean_int[key] = v

    if errors:
        raise HTTPException(status_code=400, detail={
            "error": "ITEM_INVALID",
            "message": " ".join(errors),
            "issues": errors,
        })
    return clean_str, clean_int, clamps


def _validate_class(
    family: Optional[schema.ClassFamily], fields: dict[str, int]
) -> tuple[dict[str, int], list[dict]]:
    """Validate + clamp per-class (sister-file) fields.

    Returns (clean_fields, clamps). Raises HTTPException(400) for
    unknown fields or if class_fields are supplied for a classless item.
    """
    if not fields:
        return {}, []
    if family is None:
        raise HTTPException(status_code=400, detail={
            "error": "NO_CLASS",
            "message": "This item has no per-class stats.",
        })
    by_key = {f.key: f for f in family.fields}
    clean: dict[str, int] = {}
    clamps: list[dict] = []
    for key, val in fields.items():
        spec = by_key.get(key)
        if spec is None:
            raise HTTPException(status_code=400, detail={
                "error": "UNKNOWN_FIELD",
                "message": f"'{key}' is not a {family.name} field.",
            })
        v, changed = schema.clamp_int(spec, val)
        if changed:
            clamps.append({"key": key, "requested": val, "stored": v})
        clean[key] = v
    return clean, clamps


# ── Read endpoints ───────────────────────────────────────────────────────────

@router.get("/items")
def list_items(install_id: Optional[str] = Query(default=None)) -> dict:
    """Return the full items index + the common-field schema the editor renders."""
    info = _resolve_install(install_id)
    read_path = _items_path(info, "Items.xml")
    file_present = bool(read_path and read_path.exists())
    write_path = _items_path(info, "Items.xml", for_write=True)
    writable = bool(write_path and write_path.exists())
    rows = ix.read_index(read_path) if file_present else []

    # Attach category key to each item summary and tally counts.
    counts: dict[str, int] = {cat.key: 0 for cat in schema.CATEGORIES}
    items_out = []
    for r in rows:
        cat_key = schema.resolve_category(r.item_class)
        counts[cat_key] = counts.get(cat_key, 0) + 1
        d = r.__dict__.copy()
        d["category"] = cat_key
        items_out.append(d)

    categories = [
        {"key": cat.key, "label": cat.label, "count": counts.get(cat.key, 0)}
        for cat in schema.CATEGORIES
    ]

    return {
        "items": items_out,
        "categories": categories,
        "common_schema": schema.common_schema_payload(),
        "install_id": info.id,
        "file_present": file_present,
        "writable": writable,
    }


@router.get("/items/{ui_index}")
def get_item(
    ui_index: int,
    install_id: Optional[str] = Query(default=None),
) -> dict:
    """Return common + per-class fields for one item, plus the class schema."""
    info = _resolve_install(install_id)
    # Build the install context ONCE — make_install_context is a ~50-100ms call
    # (VFS/flavor probes); reuse it for the Items path, sister path, and enums.
    ctx = make_install_context(info.path)
    read_path = ctx.items_table_path("Items.xml")
    if not read_path or not read_path.exists():
        raise HTTPException(status_code=400, detail={
            "error": "ITEMS_NOT_PRESENT",
            "message": "This install has no Items.xml.",
        })
    try:
        detail = ix.read_item(read_path, ui_index)
    except ix.ItemError as e:
        raise HTTPException(status_code=404, detail={
            "error": e.code, "message": e.message,
        })

    item_class = detail["ints"].get("usItemClass", 0)
    family = schema.resolve_family(item_class)
    class_fields = None
    class_schema_out = None
    family_name = None

    if family is not None:
        family_name = family.name
        class_schema_out = schema.class_schema_payload(family)
        sister_path = ctx.items_table_path(family.filename)
        row = cx.read_row(sister_path, family.record_tag, detail["class_index"]) \
            if sister_path else None
        if row is not None:
            wanted = {f.key for f in family.fields}
            class_fields = {k: v for k, v in row.items() if k in wanted}

    # Decoded class label (human bit-names).
    class_label = schema.decode_class(item_class)

    # Per-field enum options for all coded fields in common + class schema
    # (reuses the ctx built above).
    enum_options: dict[str, list] = {}
    all_field_keys = list(schema.COMMON_INT_KEYS)
    if family is not None:
        all_field_keys += [f.key for f in family.fields]
    for fk in all_field_keys:
        opts = item_enums.enum_options_for(fk, ctx)
        if opts is not None:
            enum_options[fk] = opts

    return {
        **detail,
        "family": family_name,
        "class_fields": class_fields,
        "class_schema": class_schema_out,
        "class_label": class_label,
        "enum_options": enum_options,
    }


# ── Write endpoint ───────────────────────────────────────────────────────────

@router.put("/items/{ui_index}")
def update_item(
    ui_index: int,
    body: ItemUpdateBody,
    install_id: Optional[str] = Query(default=None),
) -> dict:
    """Validate + clamp all fields, snapshot both files, write atomically."""
    info = _resolve_install(install_id)
    state = get_state()

    items_path = _items_path(info, "Items.xml", for_write=True)
    if not items_path or not items_path.exists():
        raise HTTPException(status_code=400, detail={
            "error": "ITEMS_NOT_PRESENT",
            "message": "This install has no Items.xml.",
        })
    if ui_index == schema.TEMPLATE_INDEX:
        raise HTTPException(status_code=400, detail={
            "error": "TEMPLATE_PROTECTED",
            "message": "uiIndex 0 is the template row and can't be edited.",
        })

    clean_str, clean_int, clamps = _validate_common(body.strings, body.ints)

    # Resolve family from the effective class (post-edit if usItemClass is changed).
    try:
        detail = ix.read_item(items_path, ui_index)
    except ix.ItemError as e:
        raise HTTPException(status_code=404, detail={"error": e.code, "message": e.message})

    # CLASS_IMMUTABLE guard — must run before acquiring the lock.
    stored_class = detail["ints"].get("usItemClass", 0)
    if "usItemClass" in clean_int and clean_int["usItemClass"] != stored_class:
        raise HTTPException(status_code=400, detail={
            "error": "CLASS_IMMUTABLE",
            "message": "Item class can't be changed here.",
        })

    eff_class = clean_int.get("usItemClass", stored_class)
    family = schema.resolve_family(eff_class)
    clean_class, class_clamps = _validate_class(family, body.class_fields)
    clamps += class_clamps

    # Gather files to snapshot.
    files = [items_path]
    sister_path = None
    if clean_class and family is not None:
        sister_path = _items_path(info, family.filename, for_write=True)
        if not sister_path or not sister_path.exists():
            raise HTTPException(status_code=400, detail={
                "error": "SISTER_NOT_PRESENT",
                "message": f"This install has no {family.filename}.",
            })
        files.append(sister_path)

    with cross_process_install_lock(info.id), state.write_lock:
        snap = snapshot(
            install_root=info.path,
            install_id=info.id,
            files_to_back_up=files,
            reason=f"item_edit_{ui_index}",
        )
        try:
            ix.edit_item(items_path, ui_index=ui_index,
                         strings=clean_str, ints=clean_int)
            if clean_class and family is not None and sister_path is not None:
                cx.edit_row(sister_path, record_tag=family.record_tag,
                            class_index=detail["class_index"], fields=clean_class)
        except (ix.ItemError, cx.ClassRowError) as e:
            raise HTTPException(status_code=400,
                                detail={"error": e.code, "message": e.message})

    return {"ok": True, "backup_id": snap.id, "clamps": clamps}


# ── Graphics endpoints ───────────────────────────────────────────────────────

@router.get("/bigitems-catalog")
def bigitems_catalog(install_id: Optional[str] = Query(default=None)) -> dict:
    """List every resolvable BIGITEMS graphic in the install."""
    info = _resolve_install(install_id)
    return {"graphics": igph.list_bigitem_graphics(str(info.path))}


@router.get("/bigitem-graphic")
def bigitem_graphic(
    type: int = Query(...),
    num: int = Query(...),
) -> Response:
    """Render one BIGITEM graphic as PNG from the active install."""
    info = get_state().active()
    if info is None:
        raise HTTPException(status_code=400, detail="no active install")
    png = igph.render_bigitem_by_ref(str(info.path), type, num)
    if png is None:
        raise HTTPException(status_code=404, detail="graphic unavailable")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
