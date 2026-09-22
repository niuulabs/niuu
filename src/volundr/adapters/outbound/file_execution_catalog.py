"""Strict file-backed compute execution catalog adapter."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from volundr.domain.execution_catalog import (
    AccessConfiguration,
    CatalogRef,
    ExecutionCatalogError,
    ExecutionCatalogIntegrityError,
    ExecutionCatalogSnapshot,
    ExecutionProfile,
    HostRecipe,
    MachineProfile,
    RuntimeProfile,
)
from volundr.domain.services.execution_catalog import canonical_digest
from volundr.ports.execution_catalog import ExecutionCatalogPort

_EntryT = TypeVar("_EntryT", bound=BaseModel)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class _CatalogManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    default_execution: CatalogRef
    legacy_compute_profiles: dict[str, CatalogRef] = Field(default_factory=dict)


class _RawArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = "application/x-executable"


class _RawHostRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    id: str
    revision: str
    preparation: dict[str, Any]
    artifacts: tuple[_RawArtifact, ...]
    stages: tuple[dict[str, Any], ...]


class FileExecutionCatalog(ExecutionCatalogPort):
    """Load an immutable catalog from separate operator-owned YAML directories."""

    _ENTRY_DIRECTORIES = (
        "machines",
        "host-recipes",
        "access",
        "runtimes",
        "executions",
    )

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    def load(self) -> ExecutionCatalogSnapshot:
        if not self._root.is_dir():
            raise ExecutionCatalogError(f"Execution catalog directory does not exist: {self._root}")
        manifest = self._read_model(self._root / "catalog.yaml", _CatalogManifest)
        machines = self._load_entries("machines", MachineProfile)
        recipes = self._load_recipes()
        access = self._load_entries("access", AccessConfiguration)
        runtimes = self._load_entries("runtimes", RuntimeProfile)
        executions = self._load_entries("executions", ExecutionProfile)
        snapshot = ExecutionCatalogSnapshot(
            default_execution=manifest.default_execution,
            legacy_compute_profiles=manifest.legacy_compute_profiles,
            machines=machines,
            host_recipes=recipes,
            access_configurations=access,
            runtimes=runtimes,
            executions=executions,
        )
        self._validate_references(snapshot)
        return snapshot

    def _load_entries(self, directory: str, model: type[_EntryT]) -> tuple[_EntryT, ...]:
        entries: list[_EntryT] = []
        for path in self._entry_files(directory):
            raw = self._read_mapping(path)
            if "digest" in raw:
                raise ExecutionCatalogError(
                    f"Catalog digest is derived and must not be set: {path}"
                )
            try:
                entry = model.model_validate({**raw, "digest": canonical_digest(raw)})
            except ValidationError as exc:
                raise ExecutionCatalogError(
                    f"Invalid execution catalog entry {path}: {exc}"
                ) from exc
            entries.append(entry)
        self._reject_duplicates(entries, directory)
        return tuple(sorted(entries, key=lambda item: (item.id, item.revision)))

    def _load_recipes(self) -> tuple[HostRecipe, ...]:
        entries: list[HostRecipe] = []
        for path in self._entry_files("host-recipes"):
            raw = self._read_model(path, _RawHostRecipe)
            artifacts = []
            portable_artifacts = []
            for artifact in raw.artifacts:
                resolved = self._verified_artifact(path, artifact.path, artifact.sha256)
                artifact_data = {
                    "id": artifact.id,
                    "package_path": Path(artifact.path).as_posix(),
                    "resolved_path": resolved,
                    "sha256": artifact.sha256,
                    "media_type": artifact.media_type,
                }
                artifacts.append(artifact_data)
                portable_artifacts.append(
                    {key: value for key, value in artifact_data.items() if key != "resolved_path"}
                )
            portable = {
                "id": raw.id,
                "revision": raw.revision,
                "preparation": raw.preparation,
                "artifacts": portable_artifacts,
                "stages": list(raw.stages),
            }
            try:
                entry = HostRecipe.model_validate(
                    {**portable, "artifacts": artifacts, "digest": canonical_digest(portable)}
                )
            except ValidationError as exc:
                raise ExecutionCatalogError(f"Invalid host recipe {path}: {exc}") from exc
            entries.append(entry)
        self._reject_duplicates(entries, "host-recipes")
        return tuple(sorted(entries, key=lambda item: (item.id, item.revision)))

    def _entry_files(self, directory: str) -> tuple[Path, ...]:
        location = self._root / directory
        if not location.is_dir():
            raise ExecutionCatalogError(f"Execution catalog directory is missing: {location}")
        paths = sorted((*location.glob("*.yaml"), *location.glob("*.yml")))
        if not paths:
            raise ExecutionCatalogError(f"Execution catalog directory is empty: {location}")
        return tuple(paths)

    def _verified_artifact(self, source: Path, package_path: str, expected: str) -> Path:
        relative = Path(package_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ExecutionCatalogIntegrityError(
                f"Artifact path must be relative and contained ({source}): {package_path}"
            )
        try:
            resolved = (self._root / relative).resolve(strict=True)
        except OSError as exc:
            raise ExecutionCatalogIntegrityError(
                f"Artifact does not exist ({source}): {package_path}"
            ) from exc
        if not resolved.is_relative_to(self._root) or not resolved.is_file():
            raise ExecutionCatalogIntegrityError(
                f"Artifact is not a contained regular file ({source}): {package_path}"
            )
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if not hmac.compare_digest(actual, expected):
            raise ExecutionCatalogIntegrityError(
                f"Artifact digest mismatch ({source}): {package_path}; "
                f"expected {expected}, got {actual}"
            )
        return resolved

    @staticmethod
    def _reject_duplicates(entries: list[BaseModel], kind: str) -> None:
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            key = (entry.id, entry.revision)
            if key in seen:
                raise ExecutionCatalogError(
                    f"Duplicate {kind} catalog entry: {entry.id}@{entry.revision}"
                )
            seen.add(key)

    @classmethod
    def _validate_references(cls, snapshot: ExecutionCatalogSnapshot) -> None:
        executions = {(entry.id, entry.revision): entry for entry in snapshot.executions}
        default_key = (snapshot.default_execution.id, snapshot.default_execution.revision)
        if default_key not in executions:
            raise ExecutionCatalogError(
                f"Default execution does not exist: {snapshot.default_execution}"
            )
        for profile, reference in snapshot.legacy_compute_profiles.items():
            if (reference.id, reference.revision) not in executions:
                raise ExecutionCatalogError(
                    f"Legacy compute profile {profile!r} references unknown execution: {reference}"
                )
        catalogs = {
            "machine": {(entry.id, entry.revision) for entry in snapshot.machines},
            "host recipe": {(entry.id, entry.revision) for entry in snapshot.host_recipes},
            "access": {(entry.id, entry.revision) for entry in snapshot.access_configurations},
            "runtime": {(entry.id, entry.revision) for entry in snapshot.runtimes},
        }
        for execution in snapshot.executions:
            references = {
                "machine": execution.machine,
                "host recipe": execution.host_recipe,
                "access": execution.access,
                "runtime": execution.runtime,
            }
            for kind, reference in references.items():
                if (reference.id, reference.revision) not in catalogs[kind]:
                    raise ExecutionCatalogError(
                        f"Execution {execution.ref} references unknown {kind}: {reference}"
                    )

    @staticmethod
    def _read_mapping(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ExecutionCatalogError(f"Execution catalog file does not exist: {path}")
        try:
            value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
        except (OSError, yaml.YAMLError) as exc:
            raise ExecutionCatalogError(
                f"Cannot read execution catalog file {path}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise ExecutionCatalogError(f"Execution catalog file must contain a mapping: {path}")
        return value

    @classmethod
    def _read_model(cls, path: Path, model: type[_EntryT]) -> _EntryT:
        try:
            return model.model_validate(cls._read_mapping(path))
        except ValidationError as exc:
            raise ExecutionCatalogError(f"Invalid execution catalog file {path}: {exc}") from exc
