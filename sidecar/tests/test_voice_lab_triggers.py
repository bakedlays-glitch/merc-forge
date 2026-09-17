import os
import hashlib
import json
import re
from importlib import resources
from pathlib import Path

from mercwizard_core.voice_lab.triggers import load_trigger_catalog


def test_profile_type_selects_alias_meaning() -> None:
    """Slot 80 has distinct AIM and non-AIM engine meanings."""
    catalog = load_trigger_catalog()

    assert "reputation" in catalog.for_profile(80, 1).meaning.lower()
    assert "buddy 3" in catalog.for_profile(80, 2).meaning.lower()


def test_catalog_keeps_all_aliases_for_an_overloaded_slot() -> None:
    """Consumers need the original aliases to explain profile-specific meanings."""
    catalog = load_trigger_catalog()

    names = [alias.name for alias in catalog.for_profile(80, 1).aliases]

    assert names == ["QUOTE_REPUTATION_REFUSAL", "QUOTE_NON_AIM_BUDDY_3_KILLED"]


def test_catalog_exposes_clean_meaning_and_verbatim_source_comment() -> None:
    """Header line-number markers must not leak into the player-facing meaning."""
    catalog = load_trigger_catalog()
    aim_trigger = catalog.for_profile(80, 1)
    aim_alias = aim_trigger.aliases[0]

    assert aim_trigger.meaning == "AIM: refuse to be hired due to bad player reputation"
    assert aim_alias.source_comment == "80\t\t\t\t\t\t\t\t// AIM: refuse to be hired due to bad player reputation"


def test_catalog_records_engine_sources_and_is_numeric_by_slot() -> None:
    """Generated catalog provenance and ordering catch stale or reordered inputs."""
    catalog_path = Path(__file__).parents[1] / "mercwizard_core" / "data" / "voice_triggers.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    source_root = Path(os.environ.get("JA2_SOURCE", ""))
    dialogue_header = source_root / "Tactical" / "Dialogue Control.h"
    profile_header = source_root / "Tactical" / "Soldier Profile.h"

    # Provenance is only checkable where the engine source is available; the
    # ordering assertion below is machine-independent and always runs.
    if dialogue_header.is_file() and profile_header.is_file():
        assert payload["source_sha256"]["dialogue_header"] == hashlib.sha256(dialogue_header.read_bytes()).hexdigest()
        assert payload["source_sha256"]["profile_header"] == hashlib.sha256(profile_header.read_bytes()).hexdigest()
    assert [entry["slot"] for entry in payload["triggers"]] == sorted(entry["slot"] for entry in payload["triggers"])


def test_catalog_uses_lf_bytes_for_cross_platform_reproducibility() -> None:
    """The checked-in generator output must not vary with the host newline convention."""
    catalog_path = Path(__file__).parents[1] / "mercwizard_core" / "data" / "voice_triggers.json"

    assert b"\r\n" not in catalog_path.read_bytes()


def test_catalog_is_declared_and_read_as_package_data() -> None:
    """Installed sidecars must carry the generated catalog alongside the package."""
    pyproject_text = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    catalog_resource = resources.files("mercwizard_core").joinpath("data", "voice_triggers.json")

    assert re.search(
        r'^\[tool\.setuptools\.package-data\]\nmercwizard_core = \["data/\*\.json"\]$',
        pyproject_text,
        flags=re.MULTILINE,
    )
    assert catalog_resource.is_file()
    assert json.loads(catalog_resource.read_text(encoding="utf-8"))["triggers"][80]["slot"] == 80
