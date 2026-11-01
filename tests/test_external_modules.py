"""Tests for provider-neutral external module manifests."""

from pathlib import Path

import pytest

from volundr.config import IntegrationsConfig
from volundr.external_modules import (
    ExternalModuleLoadError,
    load_external_module_manifest,
    load_external_module_manifests,
)
from volundr.integration_definitions import load_integration_definition_configs

HARVESTER = "volundr.adapters.outbound.harvester.HarvesterMachineProvider"
SSH_RUNTIME = "volundr.adapters.outbound.ssh_vm_runtime.SshContainerVmRuntime"


def _manifest(*, adapter: str = HARVESTER, extra: str = "") -> str:
    return (
        "schema_version: 1\n"
        "id: private-compute\n"
        "requires:\n"
        "  niuu_machine_provider: 1\n"
        "  niuu_vm_runtime: 1\n"
        "components:\n"
        "  - kind: machine_provider\n"
        "    name: private\n"
        f"    adapter: {adapter}\n"
        f"{extra}"
    )


def test_loads_compute_only_manifest_and_validates_both_component_ports(tmp_path: Path) -> None:
    path = tmp_path / "niuu-module.yaml"
    path.write_text(
        _manifest(
            extra=(f"  - kind: vm_runtime\n    name: private-ssh\n    adapter: {SSH_RUNTIME}\n")
        ),
        encoding="utf-8",
    )

    loaded = load_external_module_manifest(path)

    assert loaded.manifest.id == "private-compute"
    assert [component.kind for component in loaded.manifest.components] == [
        "machine_provider",
        "vm_runtime",
    ]
    assert loaded.integration_definition_files == ()


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (_manifest().replace("schema_version: 1", "schema_version: 2"), "schema_version"),
        (_manifest().replace("kind: machine_provider", "kind: database"), "kind"),
        (
            _manifest().replace("niuu_machine_provider: 1", "niuu_machine_provider: 2"),
            "niuu_machine_provider",
        ),
        (
            _manifest(
                extra=f"  - kind: vm_runtime\n    name: ssh\n    adapter: {SSH_RUNTIME}\n"
            ).replace("niuu_vm_runtime: 1", "niuu_vm_runtime: 2"),
            "niuu_vm_runtime",
        ),
        (
            _manifest(
                extra=f"  - kind: vm_runtime\n    name: ssh\n    adapter: {SSH_RUNTIME}\n"
            ).replace("  niuu_vm_runtime: 1\n", ""),
            "niuu_vm_runtime",
        ),
        (
            _manifest(
                extra=(f"  - kind: vm_runtime\n    name: private\n    adapter: {SSH_RUNTIME}\n")
            ),
            "component names",
        ),
    ],
)
def test_rejects_incompatible_manifest_shapes(tmp_path: Path, contents: str, message: str) -> None:
    path = tmp_path / "niuu-module.yaml"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ExternalModuleLoadError, match=message):
        load_external_module_manifest(path)


def test_rejects_component_that_does_not_implement_declared_port(tmp_path: Path) -> None:
    path = tmp_path / "niuu-module.yaml"
    path.write_text(
        _manifest(adapter="volundr.domain.services.integration_registry.IntegrationRegistry"),
        encoding="utf-8",
    )

    with pytest.raises(ExternalModuleLoadError, match="must implement.*MachineProvider"):
        load_external_module_manifest(path)


def test_rejects_abstract_component_class(tmp_path: Path) -> None:
    path = tmp_path / "niuu-module.yaml"
    path.write_text(_manifest(adapter="volundr.domain.compute.MachineProvider"), encoding="utf-8")

    with pytest.raises(ExternalModuleLoadError, match="is abstract"):
        load_external_module_manifest(path)


def test_rejects_duplicate_package_ids(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(_manifest(), encoding="utf-8")
    second.write_text(_manifest(), encoding="utf-8")

    with pytest.raises(ExternalModuleLoadError, match="Duplicate external module id"):
        load_external_module_manifests([str(first), str(second)])


def test_rejects_duplicate_component_names_across_packages(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(_manifest(), encoding="utf-8")
    second.write_text(_manifest().replace("id: private-compute", "id: another"), encoding="utf-8")

    with pytest.raises(ExternalModuleLoadError, match="Duplicate external component"):
        load_external_module_manifests([str(first), str(second)])


def test_manifest_catalog_files_extend_legacy_definition_files(tmp_path: Path) -> None:
    definitions = tmp_path / "catalog.yaml"
    definitions.write_text(
        "slug: private-tracker\nname: Private tracker\nintegration_type: issue_tracker\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "niuu-module.yaml"
    manifest.write_text(
        _manifest(extra="integration_definition_files:\n  - catalog.yaml\n"),
        encoding="utf-8",
    )

    loaded = load_integration_definition_configs(
        IntegrationsConfig(definitions=[], module_manifest_files=[str(manifest)])
    )

    assert [definition.slug for definition in loaded] == ["private-tracker"]


def test_manifest_definition_must_exist_inside_package(tmp_path: Path) -> None:
    manifest = tmp_path / "niuu-module.yaml"
    manifest.write_text(
        _manifest(extra="integration_definition_files:\n  - missing.yaml\n"),
        encoding="utf-8",
    )

    with pytest.raises(ExternalModuleLoadError, match="does not exist"):
        load_external_module_manifest(manifest)
