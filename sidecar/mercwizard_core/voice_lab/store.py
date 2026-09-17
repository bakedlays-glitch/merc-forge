"""Incremental, install-scoped persistence for Voice Lab review state.

The database is deliberately a rebuildable index: audio recipes and deployment
manifests remain durable files in the configured authoring workspace.  This
store keeps scans, review dispositions, and searchable metadata close to the
application without ever writing to a game install.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator, Sequence

from .models import EditRecipe, Finding, FindingState, ImportedVoiceAsset, InventorySnapshot, Transcription, VoiceAsset, VoiceBank


_SCHEMA_VERSION = 2
_STORE_INIT_LOCK = threading.Lock()
_FINDING_STATES = {
    "needs_review",
    "accepted",
    "fixed",
    "intentional",
    "false_positive",
    "deferred",
}

_SCHEMA_STATEMENTS = tuple(
    statement.strip()
    for statement in """
    CREATE TABLE banks (
        install_id TEXT NOT NULL,
        voice_index INTEGER NOT NULL,
        bank_json TEXT NOT NULL,
        PRIMARY KEY (install_id, voice_index)
    );
    CREATE TABLE profiles (
        install_id TEXT NOT NULL,
        profile_id INTEGER NOT NULL,
        voice_index INTEGER NOT NULL,
        profile_json TEXT NOT NULL,
        PRIMARY KEY (install_id, profile_id),
        FOREIGN KEY (install_id, voice_index)
            REFERENCES banks(install_id, voice_index) ON DELETE CASCADE
    );
    CREATE TABLE lines (
        install_id TEXT NOT NULL,
        voice_index INTEGER NOT NULL,
        family TEXT NOT NULL,
        line_id TEXT NOT NULL,
        line_json TEXT NOT NULL,
        PRIMARY KEY (install_id, voice_index, family, line_id),
        FOREIGN KEY (install_id, voice_index)
            REFERENCES banks(install_id, voice_index) ON DELETE CASCADE
    );
    CREATE TABLE assets (
        asset_id TEXT PRIMARY KEY,
        install_id TEXT NOT NULL,
        family TEXT NOT NULL,
        voice_index INTEGER NOT NULL,
        line_id TEXT NOT NULL,
        extension TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_locator TEXT NOT NULL,
        layer_rank INTEGER NOT NULL,
        size_bytes INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        writable INTEGER NOT NULL,
        winner INTEGER NOT NULL,
        asset_json TEXT NOT NULL,
        FOREIGN KEY (install_id, voice_index)
            REFERENCES banks(install_id, voice_index) ON DELETE CASCADE
    );
    CREATE TABLE fingerprints (
        asset_id TEXT PRIMARY KEY,
        size_bytes INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        sha256 TEXT NOT NULL
    );
    CREATE TABLE transcriptions (
        asset_id TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        transcription_json TEXT NOT NULL,
        PRIMARY KEY (asset_id, source_sha256)
    );
    CREATE TABLE findings (
        stable_key TEXT PRIMARY KEY,
        evidence_hash TEXT NOT NULL,
        code TEXT NOT NULL,
        severity TEXT NOT NULL,
        confidence REAL NOT NULL,
        state TEXT NOT NULL,
        asset_ids_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        suggested_action TEXT,
        finding_json TEXT NOT NULL
    );
    CREATE TABLE recipes (
        recipe_id TEXT PRIMARY KEY,
        install_id TEXT NOT NULL,
        voice_index INTEGER NOT NULL,
        family TEXT NOT NULL,
        line_id TEXT NOT NULL,
        input_asset_id TEXT NOT NULL,
        input_sha256 TEXT NOT NULL,
        review_status TEXT NOT NULL,
        deployment_status TEXT NOT NULL,
        deployment_id TEXT,
        recipe_json TEXT NOT NULL
    );
    CREATE TABLE deployments (
        deployment_id TEXT PRIMARY KEY,
        install_id TEXT NOT NULL,
        recipe_id TEXT NOT NULL,
        status TEXT NOT NULL,
        manifest_json TEXT NOT NULL
    );
    CREATE TABLE deployment_targets (
        deployment_id TEXT NOT NULL,
        target_index INTEGER NOT NULL,
        target_json TEXT NOT NULL,
        PRIMARY KEY (deployment_id, target_index),
        FOREIGN KEY (deployment_id)
            REFERENCES deployments(deployment_id) ON DELETE CASCADE
    );
    CREATE TABLE imported_assets (
        asset_id TEXT PRIMARY KEY,
        install_id TEXT NOT NULL,
        extension TEXT NOT NULL,
        source_locator TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        duration_ms INTEGER NOT NULL,
        asset_json TEXT NOT NULL
    );
    """.split(";\n")
    if statement.strip()
)


def _appdata_root() -> Path:
    """Return the MercForge application-data root without probing a repository."""
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "MercWizard" if appdata else Path.home() / ".config" / "MercWizard"


class VoiceLabStore:
    """A small SQLite index for one Voice Lab session or install.

    The caller owns the lifecycle.  Use :meth:`open` for the normal,
    application-data, install-scoped location; direct construction is useful
    for tests and explicitly configured workspace databases.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            str(self.database_path),
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        # SQLite's WAL-mode pragma itself is a writer. Serialize first opens
        # in this process; _migrate's BEGIN IMMEDIATE handles other processes.
        with _STORE_INIT_LOCK, self._lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    @classmethod
    def open(cls, install_id: str, base: Path | None = None) -> "VoiceLabStore":
        """Open the normal per-install cache beneath MercForge app data."""
        if not install_id or install_id in {".", ".."} or any(sep in install_id for sep in ("/", "\\")):
            raise ValueError("install_id must be a non-empty opaque identifier")
        root = Path(base) if base is not None else _appdata_root()
        return cls(root / "voice_lab" / install_id / "voice_lab.sqlite3")

    def close(self) -> None:
        """Close the SQLite connection; repeat calls are harmless."""
        with self._lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None  # type: ignore[assignment]

    def __enter__(self) -> "VoiceLabStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run one explicit write transaction and never leave it half-open."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    @contextmanager
    def read_transaction(self) -> Iterator[None]:
        """Keep multi-query history reads on one SQLite snapshot."""
        with self._lock:
            self.connection.execute("BEGIN")
            try:
                yield
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def replace_inventory(self, snapshot: InventorySnapshot) -> None:
        """Replace one install's derived inventory in one atomic transaction."""
        with self.transaction():
            install_id = snapshot.install_id
            self.connection.execute("DELETE FROM profiles WHERE install_id = ?", (install_id,))
            self.connection.execute("DELETE FROM assets WHERE install_id = ?", (install_id,))
            self.connection.execute("DELETE FROM lines WHERE install_id = ?", (install_id,))
            self.connection.execute("DELETE FROM banks WHERE install_id = ?", (install_id,))
            for bank in snapshot.banks:
                self._insert_bank(install_id, bank)

    def replace_banks(self, snapshot: InventorySnapshot) -> None:
        """Atomically refresh selected banks while preserving other cached banks."""
        if not snapshot.banks:
            return
        install_id = snapshot.install_id
        voice_indexes = [bank.voice_index for bank in snapshot.banks]
        profile_ids = [
            profile.profile_id for bank in snapshot.banks for profile in bank.profiles
        ]
        voice_marks = ",".join("?" for _ in voice_indexes)
        with self.transaction():
            former_voice_indexes: list[int] = []
            if profile_ids:
                profile_marks = ",".join("?" for _ in profile_ids)
                former_voice_indexes = [
                    int(row["voice_index"])
                    for row in self.connection.execute(
                        f"SELECT DISTINCT voice_index FROM profiles "
                        f"WHERE install_id = ? AND profile_id IN ({profile_marks})",
                        (install_id, *profile_ids),
                    ).fetchall()
                ]
            self.connection.execute(
                f"DELETE FROM profiles WHERE install_id = ? AND voice_index IN ({voice_marks})",
                (install_id, *voice_indexes),
            )
            if profile_ids:
                self.connection.execute(
                    f"DELETE FROM profiles WHERE install_id = ? AND profile_id IN ({profile_marks})",
                    (install_id, *profile_ids),
                )
            if former_voice_indexes:
                former_marks = ",".join("?" for _ in former_voice_indexes)
                self.connection.execute(
                    f"""
                    DELETE FROM banks
                    WHERE install_id = ? AND voice_index IN ({former_marks})
                    AND NOT EXISTS (
                        SELECT 1 FROM profiles
                        WHERE profiles.install_id = banks.install_id
                        AND profiles.voice_index = banks.voice_index
                    )
                    """,
                    (install_id, *former_voice_indexes),
                )
            self.connection.execute(
                f"DELETE FROM assets WHERE install_id = ? AND voice_index IN ({voice_marks})",
                (install_id, *voice_indexes),
            )
            self.connection.execute(
                f"DELETE FROM lines WHERE install_id = ? AND voice_index IN ({voice_marks})",
                (install_id, *voice_indexes),
            )
            self.connection.execute(
                f"DELETE FROM banks WHERE install_id = ? AND voice_index IN ({voice_marks})",
                (install_id, *voice_indexes),
            )
            for bank in snapshot.banks:
                self._insert_bank(install_id, bank)

    def _insert_bank(self, install_id: str, bank: VoiceBank) -> None:
        self.connection.execute(
            "INSERT INTO banks(install_id, voice_index, bank_json) VALUES (?, ?, ?)",
            (install_id, bank.voice_index, _json(bank.model_dump(mode="json"))),
        )
        for profile in bank.profiles:
            self.connection.execute(
                """
                INSERT INTO profiles(install_id, profile_id, voice_index, profile_json)
                VALUES (?, ?, ?, ?)
                """,
                (install_id, profile.profile_id, bank.voice_index, _json(profile.model_dump(mode="json"))),
            )
        for line in bank.lines:
            self.connection.execute(
                """
                INSERT INTO lines(install_id, voice_index, family, line_id, line_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    install_id,
                    bank.voice_index,
                    line.family,
                    line.line_id,
                    _json(line.model_dump(mode="json")),
                ),
            )
            for asset in _line_assets(line):
                self._save_asset(install_id, asset)

    def asset(self, asset_id: str) -> VoiceAsset | ImportedVoiceAsset:
        """Return a scanned or durable imported asset by opaque identifier."""
        row = self.connection.execute(
            "SELECT asset_json FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if row is not None:
            return VoiceAsset.model_validate_json(row["asset_json"])
        row = self.connection.execute(
            "SELECT asset_json FROM imported_assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown voice asset {asset_id}")
        return ImportedVoiceAsset.model_validate_json(row["asset_json"])

    def register_imported_asset(
        self,
        *,
        install_id: str,
        asset_id: str,
        extension: str,
        source_locator: str,
        size_bytes: int,
        sha256: str,
        duration_ms: int,
        source_kind: str = "import",
        recipe_id: str | None = None,
    ) -> ImportedVoiceAsset:
        """Persist an authoring upload independently of replaceable scan rows."""
        asset = ImportedVoiceAsset(
            asset_id=asset_id,
            install_id=install_id,
            extension=extension,
            source_locator=source_locator,
            size_bytes=size_bytes,
            sha256=sha256,
            duration_ms=duration_ms,
            source_kind=source_kind,
            recipe_id=recipe_id,
        )
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO imported_assets(
                    asset_id, install_id, extension, source_locator, size_bytes,
                    sha256, duration_ms, asset_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    install_id = excluded.install_id,
                    extension = excluded.extension,
                    source_locator = excluded.source_locator,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    duration_ms = excluded.duration_ms,
                    asset_json = excluded.asset_json
                """,
                (
                    asset.asset_id, asset.install_id, asset.extension, asset.source_locator,
                    asset.size_bytes, asset.sha256, asset.duration_ms, asset.model_dump_json(),
                ),
            )
        return asset

    def remember_fingerprint(self, asset_id: str, size_bytes: int, mtime_ns: int, sha256: str) -> None:
        """Cache a hash only for the exact observed size and modification time."""
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO fingerprints(asset_id, size_bytes, mtime_ns, sha256)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    mtime_ns = excluded.mtime_ns,
                    sha256 = excluded.sha256
                """,
                (asset_id, size_bytes, mtime_ns, sha256),
            )

    def cached_sha256(self, asset_id: str, size_bytes: int, mtime_ns: int) -> str | None:
        """Return a cached hash only when every invalidating fingerprint matches."""
        row = self.connection.execute(
            """
            SELECT sha256 FROM fingerprints
            WHERE asset_id = ? AND size_bytes = ? AND mtime_ns = ?
            """,
            (asset_id, size_bytes, mtime_ns),
        ).fetchone()
        return None if row is None else str(row["sha256"])

    def upsert_findings(self, findings: Sequence[Finding]) -> None:
        """Refresh audit evidence while preserving only still-valid dispositions."""
        with self.transaction():
            for finding in findings:
                existing = self.connection.execute(
                    "SELECT evidence_hash, state FROM findings WHERE stable_key = ?",
                    (finding.stable_key,),
                ).fetchone()
                state = finding.state
                if existing is not None:
                    # A changed input invalidates an old accepted/intentional choice.
                    state = (
                        str(existing["state"])
                        if existing["evidence_hash"] == finding.evidence_hash
                        else "needs_review"
                    )
                persisted = finding.model_copy(update={"state": state})
                self.connection.execute(
                    """
                    INSERT INTO findings(
                        stable_key, evidence_hash, code, severity, confidence, state,
                        asset_ids_json, evidence_json, suggested_action, finding_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stable_key) DO UPDATE SET
                        evidence_hash = excluded.evidence_hash,
                        code = excluded.code,
                        severity = excluded.severity,
                        confidence = excluded.confidence,
                        state = excluded.state,
                        asset_ids_json = excluded.asset_ids_json,
                        evidence_json = excluded.evidence_json,
                        suggested_action = excluded.suggested_action,
                        finding_json = excluded.finding_json
                    """,
                    (
                        persisted.stable_key,
                        persisted.evidence_hash,
                        persisted.code,
                        persisted.severity,
                        persisted.confidence,
                        persisted.state,
                        _json(persisted.asset_ids),
                        _json(persisted.evidence),
                        persisted.suggested_action,
                        persisted.model_dump_json(),
                    ),
                )

    def finding(self, stable_key: str) -> Finding:
        """Return one audit finding or raise ``KeyError`` when it is absent."""
        row = self.connection.execute(
            "SELECT finding_json FROM findings WHERE stable_key = ?", (stable_key,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown Voice Lab finding {stable_key}")
        return Finding.model_validate_json(row["finding_json"])

    def findings(self) -> list[Finding]:
        """Return persisted findings in stable key order for deterministic views."""
        rows = self.connection.execute(
            "SELECT finding_json FROM findings ORDER BY stable_key"
        ).fetchall()
        return [Finding.model_validate_json(row["finding_json"]) for row in rows]

    def set_finding_state(self, stable_key: str, state: FindingState) -> Finding:
        """Apply a reviewer disposition without changing the finding evidence."""
        if state not in _FINDING_STATES:
            raise ValueError(f"unknown finding state: {state}")
        with self.transaction():
            finding = self.finding(stable_key)
            updated = finding.model_copy(update={"state": state})
            cursor = self.connection.execute(
                "UPDATE findings SET state = ?, finding_json = ? WHERE stable_key = ?",
                (updated.state, updated.model_dump_json(), stable_key),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown Voice Lab finding {stable_key}")
        return updated

    def save_transcription(self, transcription: Transcription) -> None:
        """Index a transcript by both source identity and exact audio hash."""
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO transcriptions(asset_id, source_sha256, transcription_json)
                VALUES (?, ?, ?)
                ON CONFLICT(asset_id, source_sha256) DO UPDATE SET
                    transcription_json = excluded.transcription_json
                """,
                (
                    transcription.asset_id,
                    transcription.source_sha256,
                    transcription.model_dump_json(),
                ),
            )

    def transcription(self, asset_id: str, source_sha256: str) -> Transcription | None:
        """Return evidence only when it was generated from the requested bytes."""
        row = self.connection.execute(
            """
            SELECT transcription_json FROM transcriptions
            WHERE asset_id = ? AND source_sha256 = ?
            """,
            (asset_id, source_sha256),
        ).fetchone()
        return None if row is None else Transcription.model_validate_json(row["transcription_json"])

    def save_recipe(self, recipe: EditRecipe) -> None:
        """Store a searchable recipe index; the durable recipe file remains external."""
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO recipes(
                    recipe_id, install_id, voice_index, family, line_id, input_asset_id,
                    input_sha256, review_status, deployment_status, deployment_id, recipe_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recipe_id) DO UPDATE SET
                    install_id = excluded.install_id,
                    voice_index = excluded.voice_index,
                    family = excluded.family,
                    line_id = excluded.line_id,
                    input_asset_id = excluded.input_asset_id,
                    input_sha256 = excluded.input_sha256,
                    review_status = excluded.review_status,
                    deployment_status = excluded.deployment_status,
                    deployment_id = excluded.deployment_id,
                    recipe_json = excluded.recipe_json
                """,
                (
                    recipe.recipe_id,
                    recipe.install_id,
                    recipe.voice_index,
                    recipe.family,
                    recipe.line_id,
                    recipe.input_asset_id,
                    recipe.input_sha256,
                    recipe.review_status,
                    recipe.deployment_status,
                    recipe.deployment_id,
                    recipe.model_dump_json(),
                ),
            )

    def recipe(self, recipe_id: str) -> EditRecipe:
        """Return one recipe index entry or raise ``KeyError`` when absent."""
        row = self.connection.execute(
            "SELECT recipe_json FROM recipes WHERE recipe_id = ?", (recipe_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown Voice Lab recipe {recipe_id}")
        return EditRecipe.model_validate_json(row["recipe_json"])

    def recipes(self) -> list[EditRecipe]:
        """Return the recipe index in stable creation order."""
        rows = self.connection.execute(
            "SELECT recipe_json FROM recipes ORDER BY rowid"
        ).fetchall()
        return [EditRecipe.model_validate_json(row["recipe_json"]) for row in rows]

    def save_deployment(
        self,
        *,
        deployment_id: str,
        install_id: str,
        recipe_id: str,
        status: str,
        manifest: dict[str, Any],
        targets: Sequence[dict[str, Any]],
    ) -> None:
        """Index a deployment manifest and its affected targets for history views."""
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO deployments(deployment_id, install_id, recipe_id, status, manifest_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(deployment_id) DO UPDATE SET
                    install_id = excluded.install_id,
                    recipe_id = excluded.recipe_id,
                    status = excluded.status,
                    manifest_json = excluded.manifest_json
                """,
                (deployment_id, install_id, recipe_id, status, _json(manifest)),
            )
            self.connection.execute(
                "DELETE FROM deployment_targets WHERE deployment_id = ?", (deployment_id,))
            self.connection.executemany(
                """
                INSERT INTO deployment_targets(deployment_id, target_index, target_json)
                VALUES (?, ?, ?)
                """,
                [(deployment_id, index, _json(target)) for index, target in enumerate(targets)],
            )

    def commit_deployment_state(
        self,
        *,
        deployment_id: str,
        install_id: str,
        recipe: EditRecipe,
        status: str,
        manifest: dict[str, Any],
        targets: Sequence[dict[str, Any]],
    ) -> None:
        """Atomically publish deploy/Undo history and the recipe state.

        A journal may become terminal only after this transaction commits.  A
        separate ``save_deployment`` then ``save_recipe`` leaves a kill window
        where a live install has no Undo-capable durable record.
        """
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO deployments(deployment_id, install_id, recipe_id, status, manifest_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(deployment_id) DO UPDATE SET
                    install_id = excluded.install_id,
                    recipe_id = excluded.recipe_id,
                    status = excluded.status,
                    manifest_json = excluded.manifest_json
                """,
                (deployment_id, install_id, recipe.recipe_id, status, _json(manifest)),
            )
            self.connection.execute("DELETE FROM deployment_targets WHERE deployment_id = ?", (deployment_id,))
            self.connection.executemany(
                "INSERT INTO deployment_targets(deployment_id, target_index, target_json) VALUES (?, ?, ?)",
                [(deployment_id, index, _json(target)) for index, target in enumerate(targets)],
            )
            self.connection.execute(
                """
                INSERT INTO recipes(
                    recipe_id, install_id, voice_index, family, line_id, input_asset_id,
                    input_sha256, review_status, deployment_status, deployment_id, recipe_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recipe_id) DO UPDATE SET
                    install_id = excluded.install_id,
                    voice_index = excluded.voice_index,
                    family = excluded.family,
                    line_id = excluded.line_id,
                    input_asset_id = excluded.input_asset_id,
                    input_sha256 = excluded.input_sha256,
                    review_status = excluded.review_status,
                    deployment_status = excluded.deployment_status,
                    deployment_id = excluded.deployment_id,
                    recipe_json = excluded.recipe_json
                """,
                (
                    recipe.recipe_id, recipe.install_id, recipe.voice_index, recipe.family, recipe.line_id,
                    recipe.input_asset_id, recipe.input_sha256, recipe.review_status, recipe.deployment_status,
                    recipe.deployment_id, recipe.model_dump_json(),
                ),
            )

    def reconcile_interrupted_deployment(
        self,
        *,
        deployment_id: str,
        recipe_id: str,
        status: str,
        deployment_status: str,
        deployment_id_for_recipe: str | None,
        install_id: str,
        backup_id: str,
        manifest: dict[str, Any] | None = None,
        targets: Sequence[dict[str, Any]] = (),
    ) -> None:
        """Publish recovered state, indexing a validated immutable manifest when present."""
        with self.transaction():
            deployment = self.connection.execute(
                "SELECT install_id, recipe_id FROM deployments WHERE deployment_id = ?", (deployment_id,),
            ).fetchone()
            if deployment is None and manifest is not None:
                if (
                    manifest.get("deployment_id") != deployment_id
                    or manifest.get("install_id") != install_id
                    or manifest.get("recipe_id") != recipe_id
                    or manifest.get("backup_id") != backup_id
                ):
                    raise ValueError("recovered manifest identity does not match journal")
                self.connection.execute(
                    "INSERT INTO deployments(deployment_id, install_id, recipe_id, status, manifest_json) VALUES (?, ?, ?, ?, ?)",
                    (deployment_id, install_id, recipe_id, status, _json(manifest)),
                )
                self.connection.executemany(
                    "INSERT INTO deployment_targets(deployment_id, target_index, target_json) VALUES (?, ?, ?)",
                    [(deployment_id, index, _json(target)) for index, target in enumerate(targets)],
                )
            elif deployment is not None:
                if deployment["install_id"] != install_id or deployment["recipe_id"] != recipe_id:
                    raise ValueError("recovered deployment identity does not match journal")
                self.connection.execute("UPDATE deployments SET status = ? WHERE deployment_id = ?", (status, deployment_id))
            row = self.connection.execute("SELECT recipe_json FROM recipes WHERE recipe_id = ?", (recipe_id,)).fetchone()
            if row is None:
                return
            recipe = EditRecipe.model_validate_json(row["recipe_json"])
            if recipe.install_id != install_id:
                raise ValueError("recovered recipe install does not match journal")
            updated = recipe.model_copy(update={"deployment_status": deployment_status, "deployment_id": deployment_id_for_recipe})
            self.connection.execute(
                "UPDATE recipes SET deployment_status = ?, deployment_id = ?, recipe_json = ? WHERE recipe_id = ?",
                (updated.deployment_status, updated.deployment_id, updated.model_dump_json(), recipe_id),
            )

    def deployment(self, deployment_id: str) -> dict[str, Any]:
        """Return the indexed deployment record and its ordered target actions."""
        with self.read_transaction():
            row = self.connection.execute(
                """
                SELECT install_id, recipe_id, status, manifest_json
                FROM deployments WHERE deployment_id = ?
                """,
                (deployment_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown Voice Lab deployment {deployment_id}")
            targets = self.connection.execute(
                """
                SELECT target_json FROM deployment_targets
                WHERE deployment_id = ? ORDER BY target_index
                """,
                (deployment_id,),
            ).fetchall()
            return {
                "deployment_id": deployment_id,
                "install_id": row["install_id"],
                "recipe_id": row["recipe_id"],
                "status": row["status"],
                "manifest": json.loads(row["manifest_json"]),
                "targets": [json.loads(target["target_json"]) for target in targets],
            }

    def _save_asset(self, install_id: str, asset: VoiceAsset) -> None:
        self.connection.execute(
            """
            INSERT INTO assets(
                asset_id, install_id, family, voice_index, line_id, extension,
                source_kind, source_locator, layer_rank, size_bytes, mtime_ns,
                sha256, writable, winner, asset_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset.asset_id,
                install_id,
                asset.family,
                asset.voice_index,
                asset.line_id,
                asset.extension,
                asset.source_kind,
                asset.source_locator,
                asset.layer_rank,
                asset.size_bytes,
                asset.mtime_ns,
                asset.sha256,
                int(asset.writable),
                int(asset.winner),
                asset.model_dump_json(),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO fingerprints(asset_id, size_bytes, mtime_ns, sha256)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                size_bytes = excluded.size_bytes,
                mtime_ns = excluded.mtime_ns,
                sha256 = excluded.sha256
            """,
            (asset.source_locator, asset.size_bytes, asset.mtime_ns, asset.sha256),
        )

    def _migrate(self) -> None:
        # Version discovery must share the writer transaction: two fresh
        # connections otherwise both observe 0 then race CREATE TABLE.
        with self.transaction():
            version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if version > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"Voice Lab database version {version} is newer than this build supports"
                )
            if version == 0:
                for statement in _SCHEMA_STATEMENTS:
                    self.connection.execute(statement)
                self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            elif version == 1:
                self.connection.execute(
                    """
                    CREATE TABLE imported_assets (
                        asset_id TEXT PRIMARY KEY,
                        install_id TEXT NOT NULL,
                        extension TEXT NOT NULL,
                        source_locator TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        duration_ms INTEGER NOT NULL,
                        asset_json TEXT NOT NULL
                    )
                    """
                )
                self.connection.execute("PRAGMA user_version = 2")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _line_assets(line: Any) -> Iterator[VoiceAsset]:
    """Yield every physical variant exactly once from an inventory line."""
    seen: set[str] = set()
    for collection in (line.audio_variants, line.gap_variants, line.dialogue_variants):
        for asset in collection:
            if asset.asset_id not in seen:
                seen.add(asset.asset_id)
                yield asset
