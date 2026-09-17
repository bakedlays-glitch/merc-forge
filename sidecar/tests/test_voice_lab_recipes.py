"""Durable, non-destructive Voice Lab recipe contracts."""
from __future__ import annotations

import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from mercwizard_core.voice_lab.models import EditRecipe
from mercwizard_core.voice_lab.recipes import RecipeRepository
from mercwizard_core.voice_lab.service import VoiceLabService


def _recipe(**changes: object) -> EditRecipe:
    """Build one hand-auditable recipe with a real input hash binding."""
    values: dict[str, object] = {
        "recipe_id": "recipe-king-111",
        "install_id": "fixture-install",
        "voice_index": 108,
        "family": "speech",
        "line_id": "111",
        "input_asset_id": "asset-king-111",
        "input_sha256": "a" * 64,
        "input_duration_ms": 1_000,
        "output_extension": ".ogg",
        "operations": [{"kind": "cut", "start_ms": 100, "end_ms": 200}],
    }
    values.update(changes)
    return EditRecipe.model_validate(values)


def test_recipe_rejects_overlapping_cut_ranges() -> None:
    """Allowing overlap would make cut order change the audible output."""
    with pytest.raises(ValueError, match="overlap"):
        _recipe(operations=[
            {"kind": "cut", "start_ms": 100, "end_ms": 300},
            {"kind": "cut", "start_ms": 250, "end_ms": 400},
        ])


def test_recipe_pins_the_canonical_ogg_contract() -> None:
    """Permitting a different deploy format would break the shared preview contract."""
    recipe = _recipe()
    assert recipe.output_format_contract == {
        "container": "ogg", "codec": "libvorbis", "quality": 4,
        "sample_rate_hz": 22050, "channels": 1,
    }
    with pytest.raises(ValueError):
        _recipe(output_extension=".wav")


@pytest.mark.parametrize("claimed_contract", [
    {"container": "ogg", "codec": "libvorbis", "quality": 3, "sample_rate_hz": 22050, "channels": 1},
    {"container": "ogg", "codec": "libvorbis", "quality": 4, "sample_rate_hz": 44100, "channels": 1},
    {"container": "ogg", "codec": "libvorbis", "quality": 4, "sample_rate_hz": 22050, "channels": 1, "extra": "not-a-contract"},
])
def test_recipe_rejects_any_noncanonical_output_contract(claimed_contract: dict[str, object]) -> None:
    """A recipe must not claim a format that the fixed renderer did not make."""
    with pytest.raises(ValueError, match="output format contract"):
        _recipe(output_format_contract=claimed_contract)


@pytest.mark.parametrize("operation", [
    {"kind": "cut", "start_ms": 100, "end_ms": 100},
    {"kind": "trim", "start_ms": 1_000, "end_ms": 1_001},
])
def test_recipe_rejects_zero_length_or_out_of_duration_ranges(operation: dict[str, object]) -> None:
    """Invalid milliseconds must be rejected before any render is attempted."""
    with pytest.raises(ValueError, match="zero-length|duration"):
        _recipe(operations=[operation])


def test_repository_round_trip_preserves_recipe_and_schema_validates_import(tmp_path: Path) -> None:
    """A corrupt exported recipe must not become durable authoring history."""
    repository = RecipeRepository(workspace=tmp_path / "authoring")
    recipe = _recipe()

    saved = repository.save(recipe)
    assert saved.exists()
    assert repository.load(recipe.recipe_id) == recipe

    exported = repository.export_recipe(recipe.recipe_id)
    decoded = json.loads(exported.decode("utf-8"))
    assert decoded["input_sha256"] == "a" * 64

    decoded["input_sha256"] = "not-a-sha256"
    with pytest.raises(ValueError, match="sha256"):
        repository.import_recipe(json.dumps(decoded).encode("utf-8"))


def test_service_refuses_an_authoring_workspace_inside_its_install(tmp_path: Path) -> None:
    """Construction must defend against stale settings before creating recipes."""
    install = tmp_path / "fake-install"
    install.mkdir()
    workspace = install / "authoring"

    with pytest.raises(ValueError, match="VOICE_WORKSPACE_IN_INSTALL"):
        VoiceLabService.for_install(
            "fixture", install, tmp_path / "voice.sqlite3", workspace=workspace,
        )

    assert not workspace.exists()


def test_repository_versions_rewrites_without_losing_prior_recipe(tmp_path: Path) -> None:
    """Replacing a draft in place would destroy an auditable prior version."""
    repository = RecipeRepository(workspace=tmp_path / "authoring")
    recipe = _recipe()
    first = repository.save(recipe)
    second = repository.save(recipe.model_copy(update={"subtitle": "Now, what can I do for you?"}))

    assert first != second
    assert first.exists()
    assert second.exists()
    assert repository.load(recipe.recipe_id).subtitle == "Now, what can I do for you?"


def test_repository_revalidates_model_copy_and_rejects_incomplete_preview_evidence(tmp_path: Path) -> None:
    """Trusting a copied Pydantic instance could persist a preview no one can audit."""
    repository = RecipeRepository(workspace=tmp_path / "authoring")
    incomplete = _recipe().model_copy(update={
        "preview_asset_path": "C:/authoring/previews/king_111.ogg",
        "preview_sha256": "b" * 64,
    })

    with pytest.raises(ValueError, match="preview evidence"):
        repository.save(incomplete)


def test_recipe_rejects_gap_marker_without_gap_hash() -> None:
    """A present-but-unhashed gap cannot distinguish an empty gap from missing evidence."""
    recipe = _recipe()
    evidence = {
        "preview_asset_path": "C:/authoring/previews/king_111.ogg",
        "preview_sha256": "b" * 64,
        "preview_pcm_sha256": "c" * 64,
        "preview_ffmpeg_version": "ffmpeg version test",
        "preview_recipe_sha256": recipe.serialized_recipe_hash(),
        "preview_source_sha256": recipe.input_sha256,
        "preview_gap_present": True,
    }

    with pytest.raises(ValueError, match="preview_gap_sha256"):
        EditRecipe.model_validate({**recipe.model_dump(mode="python"), **evidence})


def _complete_preview_values(recipe: EditRecipe, **changes: object) -> dict[str, object]:
    """Return complete non-gap preview provenance with stable source bindings."""
    values: dict[str, object] = {
        **recipe.model_dump(mode="python"),
        "preview_asset_path": "C:/authoring/previews/king_111.ogg",
        "preview_sha256": "b" * 64,
        "preview_pcm_sha256": "c" * 64,
        "preview_ffmpeg_version": "ffmpeg version test",
        "preview_recipe_sha256": recipe.serialized_recipe_hash(),
        "preview_source_sha256": recipe.input_sha256,
    }
    values.update(changes)
    return values


def test_recipe_requires_explicit_gap_marker_for_complete_preview() -> None:
    """An omitted marker makes a zero-byte generated gap indistinguishable from no gap."""
    with pytest.raises(ValueError, match="preview_gap_present"):
        EditRecipe.model_validate(_complete_preview_values(_recipe()))


def test_recipe_accepts_complete_preview_with_explicit_no_gap() -> None:
    """A no-gap preview remains auditable when the marker explicitly says false."""
    recipe = EditRecipe.model_validate(_complete_preview_values(_recipe(), preview_gap_present=False))
    assert recipe.preview_gap_present is False
    assert recipe.preview_gap_sha256 is None


def test_recipe_rejects_gap_hash_when_marker_is_false() -> None:
    """A gap digest with a false marker would make the preview evidence contradictory."""
    with pytest.raises(ValueError, match="preview_gap_present"):
        EditRecipe.model_validate(_complete_preview_values(
            _recipe(), preview_gap_present=False, preview_gap_sha256="d" * 64,
        ))


def test_repository_concurrent_saves_reserve_distinct_immutable_versions(tmp_path: Path) -> None:
    """Concurrent draft saves must not overwrite one recipe version with another."""
    workspace = tmp_path / "authoring"
    barrier = Barrier(12)

    def save(number: int) -> Path:
        barrier.wait()
        repository = RecipeRepository(workspace=workspace)
        return repository.save(_recipe(subtitle=f"revision {number}"))

    with ThreadPoolExecutor(max_workers=12) as executor:
        paths = list(executor.map(save, range(12)))

    assert len(set(paths)) == 12
    assert {RecipeRepository(workspace=workspace).import_recipe(path.read_bytes()).subtitle for path in paths} == {
        f"revision {number}" for number in range(12)
    }
