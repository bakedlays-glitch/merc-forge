"""`.wmerc` bundle format: portable single-zip merc packages.

A `.wmerc` file is a ZIP containing:
    manifest.json          — the merc + gear + aim_binding + metadata
    portrait_source.png    — 1024×1024 original portrait (with environment bg)
    extreme_master.png     — 1024×1024 face-centered re-crop (black bg)
    bigface_source.png     — 1024×1024 BigFace source (may equal portrait_source)
    anim_eye_1.png         — optional (1024×1024)
    anim_eye_2.png         — optional
    anim_mouth_1.png       — optional
    anim_mouth_2.png       — optional
    anim_mouth_3.png       — optional
    preview.png            — optional 256×256 thumbnail
    README.md              — optional author notes

The format is intended for community sharing. Players export their mercs
and post them in forums / Discord channels; other players import them.

Compatibility data in the manifest's `compat` block lets the importer warn
the player if the bundle was built for a different mod / trait system.
"""

from .export import export_merc
from .import_ import (
    ImportAuditError,
    ImportReport,
    SlotOccupiedError,
    deploy_import,
    import_merc,
    read_wmerc,
)
from .manifest import WmercManifest
from .move_cross import CrossMoveReport, move_between_installs

__all__ = [
    "CrossMoveReport",
    "ImportAuditError",
    "ImportReport",
    "SlotOccupiedError",
    "WmercManifest",
    "deploy_import",
    "export_merc",
    "import_merc",
    "move_between_installs",
    "read_wmerc",
]
