"""Validation for trusted, deployment-owned Python component packages.

External modules are mounted by the deployment and selected through the normal
dynamic-adapter configuration.  A manifest only declares and validates the
classes a package contributes; loading it never activates or instantiates an
adapter.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from niuu.utils import import_class
from volundr.domain.compute import MachineProvider
from volundr.domain.vm_runtime import VmRuntime

MACHINE_PROVIDER_CONTRACT = "niuu_machine_provider"
MACHINE_PROVIDER_CONTRACT_VERSION = 1
VM_RUNTIME_CONTRACT = "niuu_vm_runtime"
VM_RUNTIME_CONTRACT_VERSION = 1


class ExternalModuleLoadError(ValueError):
    """An external module manifest or contributed class is invalid."""


class ExternalComponentKind(StrEnum):
    MACHINE_PROVIDER = "machine_provider"
    VM_RUNTIME = "vm_runtime"


class ExternalModuleComponent(BaseModel):
    """One class contributed by an external module."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ExternalComponentKind
    name: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    adapter: str = Field(min_length=3, pattern=r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")


class ExternalModuleManifest(BaseModel):
    """Version 1 manifest for a trusted external Python package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    requires: dict[str, int] = Field(default_factory=dict)
    components: tuple[ExternalModuleComponent, ...] = Field(min_length=1)
    integration_definition_files: tuple[str, ...] = ()

    @field_validator("integration_definition_files")
    @classmethod
    def _definition_files_are_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            path = PurePosixPath(value.strip())
            if not value.strip() or path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    "integration_definition_files entries must be paths within the package"
                )
            normalized.append(str(path))
        return tuple(normalized)

    @model_validator(mode="after")
    def _validate_contracts_and_names(self) -> ExternalModuleManifest:
        names = [component.name for component in self.components]
        if len(set(names)) != len(names):
            raise ValueError("component names must be unique within a package")

        supported_contracts = {MACHINE_PROVIDER_CONTRACT, VM_RUNTIME_CONTRACT}
        unknown_requirements = sorted(set(self.requires) - supported_contracts)
        if unknown_requirements:
            raise ValueError(
                "unsupported external module contract requirement(s): "
                + ", ".join(unknown_requirements)
            )

        providers = [
            component
            for component in self.components
            if component.kind is ExternalComponentKind.MACHINE_PROVIDER
        ]
        if providers and self.requires.get(MACHINE_PROVIDER_CONTRACT) != (
            MACHINE_PROVIDER_CONTRACT_VERSION
        ):
            raise ValueError(
                f"machine_provider components require {MACHINE_PROVIDER_CONTRACT}: "
                f"{MACHINE_PROVIDER_CONTRACT_VERSION}"
            )
        runtimes = [
            component
            for component in self.components
            if component.kind is ExternalComponentKind.VM_RUNTIME
        ]
        if runtimes and self.requires.get(VM_RUNTIME_CONTRACT) != VM_RUNTIME_CONTRACT_VERSION:
            raise ValueError(
                f"vm_runtime components require {VM_RUNTIME_CONTRACT}: "
                f"{VM_RUNTIME_CONTRACT_VERSION}"
            )
        return self


@dataclass(frozen=True)
class LoadedExternalModule:
    """A validated manifest and its resolved catalog definition files."""

    path: Path
    manifest: ExternalModuleManifest
    integration_definition_files: tuple[Path, ...]


_COMPONENT_PORTS: dict[ExternalComponentKind, type] = {
    ExternalComponentKind.MACHINE_PROVIDER: MachineProvider,
    ExternalComponentKind.VM_RUNTIME: VmRuntime,
}


def load_external_module_manifests(paths: list[str]) -> list[LoadedExternalModule]:
    """Load manifests in order and validate every declared component class."""
    loaded: list[LoadedExternalModule] = []
    package_ids: set[str] = set()
    component_names: set[str] = set()
    for configured_path in paths:
        item = load_external_module_manifest(Path(configured_path).expanduser())
        package_id = item.manifest.id
        if package_id in package_ids:
            raise ExternalModuleLoadError(f"Duplicate external module id {package_id!r}")
        package_ids.add(package_id)
        for component in item.manifest.components:
            if component.name in component_names:
                raise ExternalModuleLoadError(
                    f"Duplicate external component name {component.name!r}"
                )
            component_names.add(component.name)
        loaded.append(item)
    return loaded


def load_external_module_manifest(path: Path) -> LoadedExternalModule:
    """Load one manifest and fail before startup if its contract is not usable."""
    loaded = read_external_module_manifest(path)
    for component in loaded.manifest.components:
        _validate_component(path, component)
    return loaded


def read_external_module_manifest(path: Path) -> LoadedExternalModule:
    """Parse one manifest without importing its contributed classes."""
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExternalModuleLoadError(
            f"Could not read external module manifest {path}: {exc}"
        ) from exc

    try:
        document = yaml.safe_load(contents)
    except yaml.YAMLError as exc:
        raise ExternalModuleLoadError(
            f"Invalid YAML/JSON in external module manifest {path}: {exc}"
        ) from exc

    try:
        manifest = ExternalModuleManifest.model_validate(document)
    except ValidationError as exc:
        raise ExternalModuleLoadError(f"Invalid external module manifest {path}: {exc}") from exc

    definition_files: list[Path] = []
    package_root = path.parent.resolve()
    for relative_name in manifest.integration_definition_files:
        definition_path = (package_root / relative_name).resolve()
        try:
            definition_path.relative_to(package_root)
        except ValueError as exc:
            raise ExternalModuleLoadError(
                f"Integration definition resolves outside external module package: {relative_name}"
            ) from exc
        if not definition_path.is_file():
            raise ExternalModuleLoadError(
                f"External module integration definition does not exist: {definition_path}"
            )
        definition_files.append(definition_path)

    return LoadedExternalModule(
        path=path.resolve(),
        manifest=manifest,
        integration_definition_files=tuple(definition_files),
    )


def _validate_component(path: Path, component: ExternalModuleComponent) -> None:
    expected_port = _COMPONENT_PORTS[component.kind]
    try:
        adapter_class = import_class(component.adapter)
    except (AttributeError, ImportError, ValueError) as exc:
        raise ExternalModuleLoadError(
            f"Could not import {component.kind} component {component.name!r} from {path}: {exc}"
        ) from exc
    if not isinstance(adapter_class, type) or not issubclass(adapter_class, expected_port):
        raise ExternalModuleLoadError(
            f"External module component {component.name!r} ({component.adapter}) must implement "
            f"{expected_port.__module__}.{expected_port.__name__}"
        )
    if inspect.isabstract(adapter_class):
        raise ExternalModuleLoadError(
            f"External module component {component.name!r} ({component.adapter}) is abstract"
        )


def _validate_catalog_adapter(adapter: str) -> None:
    imported = import_class(adapter)
    if not isinstance(imported, type):
        raise ExternalModuleLoadError(
            f"External integration adapter {adapter!r} did not resolve to a class"
        )


def _validation_main(argv: list[str] | None = None) -> int:
    """Validate imports in a short-lived process used by the Settings API.

    Invoked with ``-P`` so the interpreter never prepends the script/cwd
    directory to ``sys.path``. ``--package-path`` is appended to the end of
    ``sys.path`` instead of the front, so the package can supply the module
    named by ``--adapter``/the manifest but can never shadow the stdlib, an
    installed distribution, or the platform's own packages that a normal
    import would resolve first.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-path", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--adapter", action="append", default=[])
    args = parser.parse_args(argv)
    if args.package_path is not None:
        sys.path.append(str(args.package_path))
    try:
        if args.manifest is not None:
            load_external_module_manifest(args.manifest)
        for adapter in args.adapter:
            _validate_catalog_adapter(adapter)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_validation_main())
