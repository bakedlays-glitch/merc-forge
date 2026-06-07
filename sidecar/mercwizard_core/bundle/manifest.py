"""Pydantic schema for the manifest.json inside a .wmerc bundle."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models import AimBinding, GearKit, Merc, MercBinding


class WmercAuthor(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None


class WmercCompat(BaseModel):
    intended_mod: Literal["vanilla", "wasteland", "aimnas", "wildfire", "any"] = "any"
    intended_slot_range: Literal["aim", "merc", "either"] = "either"
    trait_system: Literal["NT", "OT", "either"] = "NT"
    min_game_version: str = "1.13"


class WmercPortraitMeta(BaseModel):
    """Portrait crop / bounding-box metadata, used at import time to re-encode STIs."""
    model_config = ConfigDict(extra="allow")
    crop_box: Optional[dict[str, int]] = None      # {x, y, w, h}
    eye_box: Optional[dict[str, int]] = None
    mouth_box: Optional[dict[str, int]] = None
    coordOverride: Optional[dict[str, int]] = None


class WmercVoiceMeta(BaseModel):
    """Voice clip inventory bundled inside the .wmerc ZIP under `voice/<name>`."""
    voice_index: int
    count: int
    filenames: list[str] = Field(default_factory=list)


class WmercSchemaFingerprint(BaseModel):
    """Captures the source install's schema/layout so the importer can detect
    cross-mod incompatibilities (per `docs/research/xml_schema_variation.md`).

    Populated at export. On import, compared against the destination's
    fingerprint — if they differ in load-bearing ways (MercOpinions format,
    extra tables present, expected `bEvolution` vs `fRegresses`), the
    importer surfaces a diff to the user before writing.
    """
    model_config = ConfigDict(extra="allow")
    # Identifying labels
    source_mod: Optional[str] = None              # e.g. "Vengeance Reloaded"
    source_vfs_config: Optional[str] = None       # e.g. "vfs_config.Vengeance.ini"
    source_install_path: Optional[str] = None     # informational only
    # Schema shape signals
    profile_fields: list[str] = Field(default_factory=list)   # field names in MercProfiles row
    has_bEvolution: bool = False                  # vs <fRegresses> (AIMNAS rename)
    has_fRegresses: bool = False
    has_usVoiceIndex: bool = False                # absent in Vengeance
    has_growth_modifiers: bool = False            # 11 fields, AIMNAS-only
    has_stomp_block: bool = False                 # bRace/bNationality/usBackground etc.
    # MercOpinions storage format: "dense" (Vengeance/UC/AR) or "sparse" (vanilla/AIMNAS)
    merc_opinions_format: Optional[str] = None
    # Which mod-specific tables ship rows
    extra_tables: list[str] = Field(default_factory=list)


class WmercManifest(BaseModel):
    """Root schema for a .wmerc/manifest.json file.

    Forward-compat policy: the root uses `extra="ignore"` so newer bundles
    with fields older binaries don't know about still parse (the unknown
    fields are dropped silently, but the known portion still binds the
    merc). The Eskimo `merc_binding` regression on 2026-05-14 was caused
    by `extra="forbid"` here — every existing installer rejected the new
    field with `extra_forbidden`. Schema additions on the root remain
    safe to ship without forcing every installer to rebuild.

    Nested models (Merc, AimBinding, MercBinding, GearKit, ...) keep
    `extra="forbid"` to catch typos in well-defined sub-schemas where
    silent field drops would mask real bugs.
    """
    model_config = ConfigDict(extra="ignore")

    wmerc_version: int = 1
    tool: str = "MercWizard"
    tool_version: str = "2.0.0"
    # Set explicitly by the export-side constructor (see export.py). A
    # default_factory here would re-fire at parse time too, overwriting
    # the source's timestamp when an older binary parses a newer bundle.
    exported_at: Optional[str] = None

    author: WmercAuthor = Field(default_factory=WmercAuthor)
    license: str = "unspecified"
    notes: Optional[str] = None

    merc: Merc
    gear: list[GearKit] = Field(default_factory=list)
    aim_binding: Optional[AimBinding] = None
    merc_binding: Optional[MercBinding] = None

    portrait: WmercPortraitMeta = Field(default_factory=WmercPortraitMeta)
    voice: Optional[WmercVoiceMeta] = None
    compat: WmercCompat = Field(default_factory=WmercCompat)
    schema_fingerprint: Optional[WmercSchemaFingerprint] = None
