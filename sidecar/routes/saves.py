"""Save-game scanner routes.

Surfaces the save-snapshot trap to the UI: the engine snapshots a merc's
MercProfiles.xml stats into the SOLDIERTYPE struct when the merc is HIRED.
Save files contain those snapshots. So editing a merc's profile after they
were hired in some existing save does NOT retroactively rewrite the save —
only NEW hires get the new stats. Without surfacing this, users edit
Buns's marksmanship 75 → 95, reload, see 75, and report "save corruption."

This module was deleted in commit 261425e ("no frontend caller"). It's
reintroduced here because Edit/Move/Delete now render a yellow banner
warning the user when the slot they're about to touch appears in any
.SAV in the standard save folders.

The actual scan logic lives in `mercwizard_core.saves` and is tested
directly there; this module is just the HTTP surface.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from mercwizard_core import saves as saves_mod
from mercwizard_core.roster import load_roster

from .roster import _resolve_install

router = APIRouter()


@router.get("/saves")
def list_saves(install_id: str | None = Query(default=None)) -> list[dict]:
    """List every .SAV in the standard save folders, newest first.

    `install_id` is accepted for parity with other routes (and to validate
    that there's an active install) even though the save folders are a
    function of the user's home directory, not the install path.
    """
    _resolve_install(install_id)
    saves = saves_mod.list_saves()
    return [
        {"path": str(s.path), "modified": s.modified, "size": s.size}
        for s in saves
    ]


@router.get("/saves/refs")
def saves_refs(
    install_id: str | None = Query(default=None),
    slot: int | None = Query(default=None),
) -> dict:
    """Return the save files referencing a given slot's merc nickname.

    - `slot=N`: returns `{"slot": N, "saves": [<paths>]}` — the targeted
      shape consumed by the SaveSnapshotBanner on Edit/Move/Delete.
    - no slot: returns `{"all": {<slot>: [<paths>], ...}}` — the bulk
      shape for any future Hub-level "where does each merc live?" panel.

    Detection is a UTF-16LE substring scan of the .SAV bytes for the
    merc's `zNickname`. Nicknames shorter than 3 characters are skipped
    to keep the false-positive rate low (a 1-2 char nickname like "Vi"
    would match arbitrary byte pairs). See `saves.find_refs_in_save`.

    This is intentionally a "good enough" heuristic — we're surfacing a
    soft warning, not making a save-edit decision. False positives are
    harmless (banner reads "appears in N saves" instead of N-1); false
    negatives are also harmless (banner doesn't render).
    """
    info = _resolve_install(install_id)
    roster = load_roster(info.path)
    nicknames = {e.slot: e.nickname for e in roster if e.nickname}
    refs = saves_mod.scan_saves_for_mercs(nicknames)
    out: dict[int, list[str]] = {}
    for s, paths in refs.items():
        out[s] = [str(p) for p in paths]
    if slot is not None:
        return {"slot": slot, "saves": out.get(slot, [])}
    return {"all": out}
