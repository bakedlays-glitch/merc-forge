"""Trait system detection + catalog exposure.

The active install determines which trait system the engine uses via
Ja2_Options.ini's `ENABLE_NEW_TRAIT_SYSTEM` flag:

- True (default for most modern 1.13 installs) → NT (New Traits / STOMP),
  catalog of 24 traits with Major/Minor tiers, written into bNewSkillTrait1-30.
- False (legacy / some old mods) → OT (Old Traits), catalog of 16 traits,
  written into bOldSkillTrait + bOldSkillTrait2.

Same integer ID means a DIFFERENT trait between systems (NT 13 = Night Ops,
OT 13 = Knifing). The wizard's trait picker MUST disambiguate by surfacing
the active system's catalog.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from mercwizard_core.ini import read_options
from mercwizard_core.traits import NEW_TRAITS, OLD_TRAITS

from .roster import _resolve_install

router = APIRouter()


class TraitCatalogEntry(BaseModel):
    id: int
    name: str
    tier: str  # "Major" | "Minor"


class TraitSystemResponse(BaseModel):
    system: str   # "NT" or "OT"
    catalog: list[TraitCatalogEntry]
    install_id: str


@router.get("/traits/system")
def get_trait_system(install_id: Optional[str] = Query(default=None)) -> TraitSystemResponse:
    """Detect the active trait system and return its catalog."""
    info = _resolve_install(install_id)
    opts = read_options(info.path)
    catalog = NEW_TRAITS if opts.enable_new_trait_system else OLD_TRAITS
    return TraitSystemResponse(
        system="NT" if opts.enable_new_trait_system else "OT",
        catalog=[
            TraitCatalogEntry(id=t.id, name=t.name, tier=t.tier.value)
            for t in catalog.values()
        ],
        install_id=info.id,
    )
