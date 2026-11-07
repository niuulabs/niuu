"""The bring-your-own-machine execution Niuu ships, built in code."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from volundr.adapters.outbound import builtin_host_catalog
from volundr.adapters.outbound.builtin_host_catalog import (
    EXECUTION_ID,
    NIX_VERSION,
    RECIPES,
    BuiltinHostCatalog,
)
from volundr.domain.execution_catalog import (
    CatalogRef,
    ExecutionCatalogError,
    ExecutionCatalogIntegrityError,
)
from volundr.domain.services.execution_catalog import ExecutionPlanResolver

REPO = Path(__file__).resolve().parents[2]
PLATFORM_HOST_RUNTIME = "volundr.adapters.outbound.platform_host_runtime.PlatformHostRuntime"
LINUX_RUNTIME = "volundr.adapters.outbound.ssh_host_vm_runtime.SshHostVmRuntime"
DARWIN_RUNTIME = "volundr.adapters.outbound.ssh_darwin_host_runtime.SshDarwinHostRuntime"


def _catalog(tmp_path: Path, **overrides) -> BuiltinHostCatalog:
    settings = {
        "pool_id": "hosts",
        "provider_binding": "registered-hosts",
        "data_dir": str(tmp_path / "forge"),
        "platform_port": 8080,
    }
    return BuiltinHostCatalog(**{**settings, **overrides})


def _plan(catalog: BuiltinHostCatalog):
    default, plans = ExecutionPlanResolver(catalog).resolve_all()
    assert plans == (default,)
    return default


def test_resolves_one_complete_execution(tmp_path):
    plan = _plan(_catalog(tmp_path, profile="byo"))

    assert plan.execution.id == EXECUTION_ID
    assert plan.execution.revision.startswith("builtin-")
    assert plan.pool_id == "hosts"
    assert plan.provider_binding == "registered-hosts"
    assert plan.provider_profile == "byo"
    assert plan.contributor_backend == "vm"
    assert plan.storage_mode == "vm"
    assert plan.capabilities == ("workspace_archive",)
    assert plan.host_requirements == {"nix": NIX_VERSION}
    assert plan.preparation_binding_id == "ssh-recipe"
    assert plan.runtime_binding_id == "platform-host"
    assert [stage.id for stage in plan.host_recipe.stages] == ["nix", "tools"]


def test_access_pins_the_registered_host_key_and_delivers_nothing_at_boot(tmp_path):
    plan = _plan(_catalog(tmp_path, ssh_user="forge", command_timeout_seconds=60))

    assert plan.access.trust.mode == "registered_host_key"
    assert plan.access.principal == "forge"
    kwargs = plan.access.transport.kwargs
    assert kwargs["bootstrap_delivery"] == "registered"
    assert kwargs["ssh_user"] == "forge"
    assert kwargs["command_timeout_seconds"] == 60
    assert kwargs["data_dir"] == str(tmp_path / "forge")


def test_runtime_is_a_platform_runtime_with_a_child_per_platform(tmp_path):
    plan = _plan(
        _catalog(
            tmp_path,
            slot_cpu=4,
            slot_memory_mib=2048,
            slot_pids=None,
            slot_uid_base=70000,
            native_images=["claude"],
            guest_platform_port=19000,
            default_service_account="session-sa",
            preview_router_auth={"mode": "token"},
        )
    )

    binding = plan.runtime.runtime
    assert binding.adapter == PLATFORM_HOST_RUNTIME
    children = binding.kwargs["platforms"]
    assert set(children) == {"linux", "darwin"}

    linux, darwin = children["linux"], children["darwin"]
    assert linux["adapter"] == LINUX_RUNTIME
    assert darwin["adapter"] == DARWIN_RUNTIME
    for child in (linux, darwin):
        assert child["kwargs"]["platform_port"] == 8080
        assert child["kwargs"]["nix"] is True
        assert child["kwargs"]["default_service_account"] == "session-sa"
        assert child["kwargs"]["preview_router_auth"] == {"mode": "token"}
    assert linux["kwargs"]["slot_isolation"] == {
        "uid_base": 70000,
        "cpu": 4,
        "memory_mib": 2048,
        "pids": None,
    }
    assert linux["kwargs"]["containers"] == {}
    assert darwin["kwargs"]["guest_platform_port"] == 19000
    assert darwin["kwargs"]["uid_base"] == 70000
    assert darwin["kwargs"]["native_images"] == ["claude"]


def test_optional_runtime_settings_are_left_out_when_unset(tmp_path):
    plan = _plan(_catalog(tmp_path, rootless_containers=False, nix=False))

    children = plan.runtime.runtime.kwargs["platforms"]
    assert "default_service_account" not in children["linux"]["kwargs"]
    assert "preview_router_auth" not in children["linux"]["kwargs"]
    assert children["linux"]["kwargs"]["containers"] is None
    assert children["linux"]["kwargs"]["nix"] is False
    assert plan.host_requirements == {}


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_one_platform_alone(tmp_path, platform):
    plan = _plan(_catalog(tmp_path, platforms=[platform]))

    assert set(plan.runtime.runtime.kwargs["platforms"]) == {platform}


@pytest.mark.parametrize(
    "platforms",
    [["windows"], ["linux", "windows"], [], ["linux", "linux"]],
)
def test_invalid_platforms_are_refused(tmp_path, platforms):
    with pytest.raises(ExecutionCatalogError, match="platforms must list some of linux, darwin"):
        _catalog(tmp_path, platforms=platforms)


@pytest.mark.parametrize("name", ["platform_port", "guest_platform_port"])
@pytest.mark.parametrize("port", [0, 65536, -1])
def test_invalid_ports_are_refused(tmp_path, name, port):
    with pytest.raises(ExecutionCatalogError, match="must be TCP ports"):
        _catalog(tmp_path, **{name: port})


def test_invalid_settings_are_an_execution_catalog_error(tmp_path):
    with pytest.raises(ExecutionCatalogError, match="Invalid built-in host catalog settings"):
        _catalog(tmp_path, pool_id="").load()


# ── revision ────────────────────────────────────────────────────────────────


def test_revision_is_stable_for_the_same_settings(tmp_path):
    first = _catalog(tmp_path).load().default_execution
    second = _catalog(tmp_path).load().default_execution

    assert first == second
    assert isinstance(first, CatalogRef)
    assert re.fullmatch(r"builtin-[0-9a-f]{16}", first.revision)


@pytest.mark.parametrize(
    "change",
    [
        {"platform_port": 9090},
        {"platforms": ["linux"]},
        {"slot_cpu": 8},
        {"ssh_user": "forge"},
        {"pool_id": "other"},
        {"nix": False},
    ],
)
def test_revision_changes_when_a_setting_changes(tmp_path, change):
    base = _catalog(tmp_path).load().default_execution
    changed = _catalog(tmp_path, **change).load().default_execution

    assert changed.revision != base.revision


def test_a_changed_recipe_script_makes_a_new_revision(tmp_path, monkeypatch):
    base = _catalog(tmp_path).load().default_execution
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    for script in RECIPES.iterdir():
        (recipes / script.name).write_bytes(script.read_bytes())
    (recipes / "tools-apply.sh").write_bytes(b"#!/bin/sh\necho changed\n")
    monkeypatch.setattr(builtin_host_catalog, "RECIPES", recipes)

    changed = _catalog(tmp_path).load().default_execution

    assert changed.revision != base.revision


# ── recipe artifacts ────────────────────────────────────────────────────────


def test_artifacts_are_the_shipped_scripts_with_their_hashes(tmp_path):
    plan = _plan(_catalog(tmp_path))

    artifacts = {artifact.id: artifact for artifact in plan.host_recipe.artifacts}
    assert set(artifacts) == {
        "nix_check",
        "nix_apply",
        "nix_verify",
        "tools_check",
        "tools_apply",
        "tools_verify",
    }
    for artifact in artifacts.values():
        shipped = RECIPES / Path(artifact.package_path).name
        assert artifact.package_path == f"host_recipes/{shipped.name}"
        assert artifact.resolved_path == shipped
        assert artifact.sha256 == hashlib.sha256(shipped.read_bytes()).hexdigest()
    assert artifacts["nix_apply"].package_path == "host_recipes/install-nix.sh"


def test_a_missing_script_is_an_integrity_error(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(builtin_host_catalog, "RECIPES", empty)

    with pytest.raises(ExecutionCatalogIntegrityError, match="missing from this install"):
        _catalog(tmp_path).load()


# ── single-source guards ────────────────────────────────────────────────────


def test_shipped_installer_is_byte_identical_to_the_deploy_one():
    shipped = RECIPES / "install-nix.sh"
    deployed = REPO / "deploy" / "nix" / "install-nix.sh"

    assert shipped.read_bytes() == deployed.read_bytes(), (
        "src/volundr/adapters/outbound/host_recipes/install-nix.sh must be a copy of "
        "deploy/nix/install-nix.sh; edit one and copy it over the other"
    )


def test_nix_version_is_the_installers_default_everywhere():
    installer = (RECIPES / "install-nix.sh").read_text(encoding="utf-8")
    check = (RECIPES / "nix-check.sh").read_text(encoding="utf-8")

    default = re.search(r'^NIX_VERSION="\$\{NIX_VERSION:-([^}]+)\}"', installer, re.MULTILINE)
    pinned = re.search(r"^NIX_VERSION=(\S+)$", check, re.MULTILINE)

    assert default is not None
    assert pinned is not None
    assert NIX_VERSION == default.group(1)
    assert pinned.group(1) == default.group(1)
