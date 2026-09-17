"""Versioned, atomic storage for non-destructive Voice Lab edit recipes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import portalocker

from ..inject._atomic_xml import write_bytes_atomic
from .models import EditRecipe, revalidate_edit_recipe


_RECIPE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_VERSION_RE = re.compile(r"^(?P<id>.+)\.v(?P<version>[0-9]{4})\.json$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HASH_FIELDS = (
    "input_sha256", "preview_sha256", "preview_pcm_sha256", "preview_gap_sha256",
    "preview_recipe_sha256", "preview_source_sha256",
)


def _appdata_root() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "MercWizard" if appdata else Path.home() / ".config" / "MercWizard"


def _setting(settings: Any, key: str) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(key)
    return getattr(settings, key, None)


def _resolved_windows_path(value: Path | str) -> Path:
    """Canonicalize an existing path before comparing Windows aliases/junctions."""
    path = Path(value)
    try:
        return Path(os.path.realpath(os.path.abspath(path))).resolve(strict=False)
    except OSError as exc:
        raise ValueError("Voice Lab authoring workspace is not accessible") from exc


def ensure_workspace_is_external(
    workspace: Path | str,
    install_roots: Iterable[Path | str],
) -> Path:
    """Reject a workspace at or below any registered game install."""
    candidate = _resolved_windows_path(workspace)
    candidate_key = os.path.normcase(str(candidate))
    for install_root in install_roots:
        root = _resolved_windows_path(install_root)
        root_key = os.path.normcase(str(root))
        try:
            if os.path.commonpath((candidate_key, root_key)) == root_key:
                raise ValueError(
                    "VOICE_WORKSPACE_IN_INSTALL: Voice authoring workspace must be outside "
                    "every registered game install"
                )
        except ValueError as exc:
            if str(exc).startswith("VOICE_WORKSPACE_IN_INSTALL:"):
                raise
            continue
    return candidate


class RecipeRepository:
    """Keep authoring recipes outside the rebuildable SQLite review index."""

    def __init__(
        self,
        *,
        workspace: Path | str | None = None,
        settings: Any | None = None,
        install_roots: Iterable[Path | str] = (),
    ) -> None:
        configured = workspace if workspace is not None else _setting(settings, "voice_authoring_workspace")
        root = Path(configured) if configured else _appdata_root() / "voice_lab"
        root = ensure_workspace_is_external(root, install_roots)
        self.root = root / "recipes"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, recipe: EditRecipe) -> Path:
        """Validate then atomically append a new immutable version of a recipe."""
        recipe = self._revalidate(recipe)
        self._validate_id(recipe.recipe_id)
        self._validate_hashes(recipe)
        payload = self._serialize(recipe)
        with portalocker.Lock(
            str(self.root / ".recipes.lock"), mode="a+", timeout=10.0, check_interval=0.05,
        ):
            version = self._latest_version(recipe.recipe_id) + 1
            target = self.root / f"{recipe.recipe_id}.v{version:04d}.json"
            while target.exists():
                version += 1
                target = self.root / f"{recipe.recipe_id}.v{version:04d}.json"
            write_bytes_atomic(target, payload)
            return target

    def load(self, recipe_id: str) -> EditRecipe:
        """Return the latest valid recipe version, refusing corrupt history."""
        self._validate_id(recipe_id)
        candidates = self._versions(recipe_id)
        if not candidates:
            raise KeyError(f"unknown Voice Lab recipe {recipe_id}")
        return self.import_recipe(candidates[-1][1].read_bytes())

    def export_recipe(self, recipe_id: str) -> bytes:
        """Return schema-validated canonical JSON suitable for a user export."""
        return self._serialize(self.load(recipe_id))

    def import_recipe(self, payload: bytes | str) -> EditRecipe:
        """Parse only a schema-valid recipe with cryptographically valid hashes."""
        try:
            decoded = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("recipe import is not valid UTF-8 JSON") from exc
        try:
            recipe = EditRecipe.model_validate(decoded)
        except Exception as exc:
            raise ValueError(f"recipe import failed schema validation: {exc}") from exc
        self._validate_id(recipe.recipe_id)
        self._validate_hashes(recipe)
        return recipe

    def _serialize(self, recipe: EditRecipe) -> bytes:
        validated = self._revalidate(recipe)
        return json.dumps(
            validated.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8") + b"\n"

    def _latest_version(self, recipe_id: str) -> int:
        versions = self._versions(recipe_id)
        return versions[-1][0] if versions else 0

    def _versions(self, recipe_id: str) -> list[tuple[int, Path]]:
        result: list[tuple[int, Path]] = []
        for candidate in self.root.glob(f"{recipe_id}.v*.json"):
            match = _VERSION_RE.fullmatch(candidate.name)
            if match is not None and match.group("id") == recipe_id:
                result.append((int(match.group("version")), candidate))
        return sorted(result)

    @staticmethod
    def _validate_id(recipe_id: str) -> None:
        if _RECIPE_ID_RE.fullmatch(recipe_id) is None:
            raise ValueError("recipe_id must be an opaque filename-safe identifier")

    @staticmethod
    def _validate_hashes(recipe: EditRecipe) -> None:
        for field in _HASH_FIELDS:
            value = getattr(recipe, field)
            if value is not None and _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{field} must be a 64-character lowercase sha256")

    @staticmethod
    def _revalidate(recipe: EditRecipe) -> EditRecipe:
        """Defeat Pydantic's instance fast-path for untrusted model copies."""
        return revalidate_edit_recipe(recipe)
