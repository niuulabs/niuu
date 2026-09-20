"""File catalog integrity and exact execution resolution tests."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

from volundr.adapters.outbound.file_execution_catalog import FileExecutionCatalog
from volundr.domain.execution_catalog import (
    CatalogRef,
    ExecutionCatalogError,
    ExecutionCatalogIntegrityError,
    ExecutionCatalogNotFoundError,
    ExecutionPlanMismatchError,
    ResolvedExecutionPlan,
)
from volundr.domain.services.execution_catalog import ExecutionPlanResolver
from volundr.ports.execution_catalog import ExecutionCatalogPort


def _yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _catalog(tmp_path: Path) -> tuple[Path, Path]:
    """Create one complete example of the supported directory schema."""
    artifact = tmp_path / "artifacts" / "prepare.sh"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    _yaml(
        tmp_path / "catalog.yaml",
        {
            "default_execution": {"id": "vm", "revision": "2026-09-18"},
            "legacy_compute_profiles": {"small": {"id": "vm", "revision": "2026-09-18"}},
        },
    )
    _yaml(
        tmp_path / "machines" / "ubuntu.yaml",
        {
            "id": "ubuntu",
            "revision": "noble-v1",
            "provider_profile": "gpu-small",
            "host_requirements": {"architecture": "amd64"},
        },
    )
    _yaml(
        tmp_path / "host-recipes" / "podman.yaml",
        {
            "id": "podman",
            "revision": "5.4-v1",
            "preparation": {
                "binding_id": "ssh-script-v1",
                "adapter": "volundr.adapters.outbound.host_preparation.SshHostPreparation",
                "kwargs": {"checkpoint_path": "/var/lib/niuu/preparation.json"},
            },
            "artifacts": [
                {
                    "id": "prepare",
                    "path": "artifacts/prepare.sh",
                    "sha256": artifact_digest,
                    "media_type": "text/x-shellscript",
                }
            ],
            "stages": [
                {
                    "id": "podman-ready",
                    "principal": "root",
                    "timeout_seconds": 300,
                    "check": "prepare",
                    "apply": "prepare",
                    "verify": "prepare",
                }
            ],
        },
    )
    _yaml(
        tmp_path / "access" / "ssh.yaml",
        {
            "id": "ssh",
            "revision": "v1",
            "transport": {
                "binding_id": "ssh-v1",
                "adapter": "volundr.adapters.outbound.ssh_transport.SshGuestTransport",
                "kwargs": {"private_key_path": "/run/secrets/compute/id_ed25519"},
                "secret_kwargs_env": {"passphrase": "COMPUTE_SSH_PASSPHRASE"},
            },
            "principal": "niuu",
            "trust": {"mode": "ssh_ca", "kwargs": {"ca_path": "/etc/niuu/ssh_ca.pub"}},
        },
    )
    _yaml(
        tmp_path / "runtimes" / "openshell.yaml",
        {
            "id": "openshell",
            "revision": "0.3.1-v1",
            "runtime": {
                "binding_id": "openshell-ssh-v1",
                "adapter": (
                    "volundr.adapters.outbound.ssh_openshell_vm_runtime.SshOpenShellVmRuntime"
                ),
                "kwargs": {"gateway_port": 8080},
            },
            "contributor_backend": "openshell",
            "storage_mode": "vm",
            "capabilities": ["native_credentials", "workspace_archive"],
            "host_requirements": {"cgroup": "v2"},
        },
    )
    _yaml(
        tmp_path / "executions" / "vm.yaml",
        {
            "id": "vm",
            "revision": "2026-09-18",
            "pool_id": "default",
            "provider_binding": "compute-provider",
            "machine": {"id": "ubuntu", "revision": "noble-v1"},
            "host_recipe": {"id": "podman", "revision": "5.4-v1"},
            "access": {"id": "ssh", "revision": "v1"},
            "runtime": {"id": "openshell", "revision": "0.3.1-v1"},
        },
    )
    return tmp_path, artifact


def test_resolves_complete_default_plan_and_trusted_artifact(tmp_path: Path) -> None:
    root, artifact = _catalog(tmp_path)

    plan = ExecutionPlanResolver(FileExecutionCatalog(root)).resolve()

    assert plan.execution == CatalogRef(id="vm", revision="2026-09-18")
    assert plan.provider_binding == "compute-provider"
    assert plan.provider_profile == "gpu-small"
    assert plan.host_recipe.artifacts[0].resolved_path == artifact.resolve()
    assert plan.host_recipe.artifacts[0].resolved_path.is_absolute()
    assert plan.preparation_binding_id == "ssh-script-v1"
    assert plan.runtime_binding_id == "openshell-ssh-v1"
    assert plan.contributor_backend == "openshell"
    assert plan.storage_mode == "vm"
    assert plan.capabilities == ("native_credentials", "workspace_archive")
    assert plan.host_requirements == {"architecture": "amd64", "cgroup": "v2"}
    assert plan.access.transport.secret_kwargs_env == {"passphrase": "COMPUTE_SSH_PASSPHRASE"}
    assert len(plan.plan_digest) == 64
    assert len(plan.host_compatibility_digest) == 64
    assert len(plan.session_execution_digest) == 64
    assert isinstance(FileExecutionCatalog(root), ExecutionCatalogPort)


def test_plan_has_stable_json_round_trip(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    plan = ExecutionPlanResolver(FileExecutionCatalog(root)).resolve("vm@2026-09-18")

    restored = ResolvedExecutionPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan


def test_exact_selection_is_required_and_unknown_revision_is_fatal(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    resolver = ExecutionPlanResolver(FileExecutionCatalog(root))

    with pytest.raises(ExecutionCatalogNotFoundError, match="id@revision"):
        resolver.resolve("vm")
    with pytest.raises(ExecutionCatalogNotFoundError, match="Unknown execution"):
        resolver.resolve("vm@missing")
    with pytest.raises(ExecutionPlanMismatchError, match="unavailable or has changed"):
        resolver.resolve_pinned("vm@missing", "0" * 64)


def test_legacy_compute_profile_requires_explicit_exact_mapping(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    resolver = ExecutionPlanResolver(FileExecutionCatalog(root))

    assert resolver.resolve_legacy_compute_profile("small").execution == CatalogRef(
        id="vm", revision="2026-09-18"
    )
    with pytest.raises(ExecutionCatalogNotFoundError, match="no execution catalog mapping"):
        resolver.resolve_legacy_compute_profile("gpu-small")


def test_plans_exposes_all_exact_configured_executions(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    second = yaml.safe_load((root / "executions" / "vm.yaml").read_text(encoding="utf-8"))
    second["id"] = "vm-secondary"
    _yaml(root / "executions" / "secondary.yaml", second)

    plans = ExecutionPlanResolver(FileExecutionCatalog(root)).plans()

    assert [str(plan.execution) for plan in plans] == [
        "vm@2026-09-18",
        "vm-secondary@2026-09-18",
    ]


def test_resolve_all_uses_one_snapshot_for_default_and_plans(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)

    default, plans = ExecutionPlanResolver(FileExecutionCatalog(root)).resolve_all()

    assert default.execution == CatalogRef(id="vm", revision="2026-09-18")
    assert plans == (default,)


def test_duplicate_id_and_revision_is_rejected(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    duplicate = (root / "machines" / "ubuntu.yaml").read_text(encoding="utf-8")
    (root / "machines" / "duplicate.yaml").write_text(duplicate, encoding="utf-8")

    with pytest.raises(ExecutionCatalogError, match="Duplicate machines"):
        FileExecutionCatalog(root).load()


def test_missing_reference_is_rejected_when_catalog_loads(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    execution_path = root / "executions" / "vm.yaml"
    execution = yaml.safe_load(execution_path.read_text(encoding="utf-8"))
    execution["runtime"]["revision"] = "missing"
    _yaml(execution_path, execution)

    with pytest.raises(ExecutionCatalogError, match="unknown runtime"):
        FileExecutionCatalog(root).load()


def test_missing_explicit_default_is_rejected(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    _yaml(
        root / "catalog.yaml",
        {"default_execution": {"id": "other", "revision": "v1"}},
    )

    with pytest.raises(ExecutionCatalogError, match="Default execution does not exist"):
        FileExecutionCatalog(root).load()


def test_artifact_digest_mismatch_is_fatal(tmp_path: Path) -> None:
    root, artifact = _catalog(tmp_path)
    artifact.write_text("changed\n", encoding="utf-8")

    with pytest.raises(ExecutionCatalogIntegrityError, match="digest mismatch"):
        FileExecutionCatalog(root).load()


def test_artifact_path_escape_is_fatal(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    recipe_path = root / "host-recipes" / "podman.yaml"
    recipe = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    recipe["artifacts"][0]["path"] = "../outside.sh"
    _yaml(recipe_path, recipe)

    with pytest.raises(ExecutionCatalogIntegrityError, match="contained"):
        FileExecutionCatalog(root).load()


def test_resolve_pinned_detects_changed_catalog_config(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    resolver = ExecutionPlanResolver(FileExecutionCatalog(root))
    original = resolver.resolve()
    runtime_path = root / "runtimes" / "openshell.yaml"
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    runtime["capabilities"].append("tunnels")
    _yaml(runtime_path, runtime)

    with pytest.raises(ExecutionCatalogIntegrityError, match="changed"):
        resolver.resolve_pinned(original.execution, original.plan_digest)


def test_relocation_changes_trusted_path_but_not_canonical_digests(tmp_path: Path) -> None:
    first, _ = _catalog(tmp_path / "first")
    second = tmp_path / "second"
    shutil.copytree(first, second)

    first_plan = ExecutionPlanResolver(FileExecutionCatalog(first)).resolve()
    second_plan = ExecutionPlanResolver(FileExecutionCatalog(second)).resolve()

    assert first_plan.host_recipe.artifacts[0].resolved_path != (
        second_plan.host_recipe.artifacts[0].resolved_path
    )
    assert first_plan.plan_digest == second_plan.plan_digest
    assert first_plan.host_compatibility_digest == second_plan.host_compatibility_digest


def test_same_revision_artifact_replacement_changes_pin_when_repinned(
    tmp_path: Path,
) -> None:
    root, artifact = _catalog(tmp_path)
    resolver = ExecutionPlanResolver(FileExecutionCatalog(root))
    original = resolver.resolve()
    artifact.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    recipe_path = root / "host-recipes" / "podman.yaml"
    recipe = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    recipe["artifacts"][0]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _yaml(recipe_path, recipe)

    changed = resolver.resolve()

    assert changed.execution == original.execution
    assert changed.plan_digest != original.plan_digest
    assert changed.host_recipe_digest != original.host_recipe_digest
    assert changed.host_compatibility_digest != original.host_compatibility_digest
    with pytest.raises(ExecutionCatalogIntegrityError, match="changed"):
        resolver.resolve_pinned(original.execution, original.plan_digest)


def test_recipe_stage_must_reference_packaged_artifacts(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    recipe_path = root / "host-recipes" / "podman.yaml"
    recipe = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    recipe["stages"][0]["verify"] = "not-packaged"
    _yaml(recipe_path, recipe)

    with pytest.raises(ExecutionCatalogError, match="unknown artifacts"):
        FileExecutionCatalog(root).load()


def test_conflicting_machine_and_runtime_host_requirements_are_fatal(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    runtime_path = root / "runtimes" / "openshell.yaml"
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    runtime["host_requirements"]["architecture"] = "arm64"
    _yaml(runtime_path, runtime)

    with pytest.raises(ExecutionCatalogIntegrityError, match="conflicting host value"):
        ExecutionPlanResolver(FileExecutionCatalog(root)).resolve()


def test_catalog_requires_each_separate_entry_directory(tmp_path: Path) -> None:
    _yaml(
        tmp_path / "catalog.yaml",
        {"default_execution": {"id": "vm", "revision": "v1"}},
    )

    with pytest.raises(ExecutionCatalogError, match="directory is missing"):
        FileExecutionCatalog(tmp_path).load()


def test_duplicate_yaml_mapping_key_is_rejected(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    machine_path = root / "machines" / "ubuntu.yaml"
    machine_path.write_text(
        "id: ubuntu\nrevision: v1\nprovider_profile: first\nprovider_profile: second\n",
        encoding="utf-8",
    )

    with pytest.raises(ExecutionCatalogError, match="duplicate key"):
        FileExecutionCatalog(root).load()


def test_validation_errors_do_not_echo_adapter_kwargs(tmp_path: Path) -> None:
    root, _ = _catalog(tmp_path)
    access_path = root / "access" / "ssh.yaml"
    access = yaml.safe_load(access_path.read_text(encoding="utf-8"))
    access["transport"]["adapter"] = 123
    access["transport"]["kwargs"]["unexpected"] = "do-not-echo-this-value"
    _yaml(access_path, access)

    with pytest.raises(ExecutionCatalogError) as error:
        FileExecutionCatalog(root).load()
    assert "do-not-echo-this-value" not in str(error.value)
