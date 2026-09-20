"""Proof-stack preparation validates real, isolated runtime inputs."""

import json
from pathlib import Path

import pytest
import yaml

from cli.commands.platform import _resolve_local_pod_manager_env
from cli.config import CLISettings
from cli.services.compose_bundle import platform_environment
from scripts.prepare_delivery_proof import build_config
from ting.config import WorkflowExecutionConfig
from volundr.config import DeliveryConfig


def test_builds_isolated_docker_clone_mode_config(tmp_path: Path) -> None:
    root = tmp_path / "proof"
    repository = root / "repositories" / "target"
    (repository / ".git").mkdir(parents=True)
    config = build_config(
        root=root,
        repository_root=repository,
        repository_url="https://git.example/approved/project",
        runner_image=f"proof-runner@sha256:{'a' * 64}",
        pinned_inputs={".gitlab-ci.yml": "b" * 40},
        platform_image="niuu-proof:local",
        skuld_image="skuld-proof:local",
        port=8180,
    )
    delivery = DeliveryConfig.model_validate(config["delivery"])
    developer = WorkflowExecutionConfig.model_validate(config["workflow_execution"])
    assert delivery.enabled
    assert delivery.workstream_producer_id == "workstream-runner"
    assert delivery.workstreams.kwargs["isolation_mode"] == "clone"
    assert delivery.workstreams.kwargs["repository_paths"] == {
        "https://git.example/approved/project": str(repository)
    }
    assert developer.delivery.integration_policy_id == "developer-integration"
    assert developer.enabled
    assert developer.delivery.enabled
    assert config["docker"]["project_name"] == "niuu-developer-delivery-proof"
    assert config["pod_manager"]["sandbox_sessions_dir"] == str(root / "workspaces")


def test_rendered_platform_loads_delivery_config_and_mount_grants(tmp_path, monkeypatch):
    """Exercise both settings consumers used by the actual Docker startup."""
    root = tmp_path / "proof"
    repository = root / "repositories" / "target"
    (repository / ".git").mkdir(parents=True)
    config = build_config(
        root=root,
        repository_root=repository,
        runner_image=f"python@sha256:{'a' * 64}",
        pinned_inputs={"tests/test_delivery.py": "b" * 40},
        platform_image="niuu-proof:local",
        skuld_image="skuld-proof:local",
        port=8180,
    )
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("NIUU_CONFIG", str(config_path))
    cli = CLISettings()
    environment = platform_environment(cli, root)
    assert environment["NIUU_CONFIG"] == str(config_path)
    # Service settings independently read the same YAML; CLISettings intentionally
    # ignores service-owned sections rather than forwarding arbitrary settings.
    from volundr.config import Settings as ForgeSettings

    forge = ForgeSettings()
    assert forge.delivery.enabled
    assert forge.local_mounts.allowed_prefixes == [str(root / "delivery" / "workspaces")]
    assert forge.delivery.policies["developer-integration"].required_review_roles == (
        "integration",
    )
    assert forge.delivery.policies["developer-workstream"].required_review_roles == (
        "code",
        "security",
        "adversarial",
    )
    local_env = _resolve_local_pod_manager_env(cli)
    assert local_env["POD_MANAGER__KWARGS__ALLOWED_MOUNT_PREFIXES"] == str(
        root / "delivery" / "workspaces"
    )
    assert json.loads(environment["SESSION_CONTRIBUTORS"])


def test_refuses_unpinned_runner_or_repository_outside_proof_root(tmp_path: Path) -> None:
    root = tmp_path / "proof"
    repository = root / "repositories" / "target"
    (repository / ".git").mkdir(parents=True)
    kwargs = {
        "root": root,
        "repository_root": repository,
        "runner_image": f"proof-runner@sha256:{'a' * 64}",
        "pinned_inputs": {"test.lock": "b" * 40},
        "platform_image": "niuu-proof:local",
        "skuld_image": "skuld-proof:local",
        "port": 8180,
    }
    with pytest.raises(ValueError, match="pinned"):
        build_config(**{**kwargs, "runner_image": "proof-runner:latest"})
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="below the proof root"):
        build_config(**{**kwargs, "repository_root": outside})
    for contracts in (
        {},
        [],
        {"commands": {"test": []}},
        {
            "commands": {"test": ["python", "-V"]},
            "workstream_required": ["missing"],
            "integration_required": ["test"],
        },
    ):
        with pytest.raises(ValueError, match="contracts|commands"):
            build_config(**kwargs, contracts=contracts)

    # A contracts file as an operator passes it with --contracts-file: workstreams
    # require less than integration does.
    contracts_file = tmp_path / "contracts.yaml"
    contracts_file.write_text(
        yaml.safe_dump(
            {
                "commands": {
                    "proof-syntax": ["python", "-m", "compileall", "-q", "src"],
                    "proof-slug": ["python", "-m", "unittest", "tests.test_slug", "-v"],
                    "proof-limits": ["python", "-m", "unittest", "tests.test_limits", "-v"],
                },
                "workstream_required": ["proof-syntax"],
                "integration_required": ["proof-syntax", "proof-slug", "proof-limits"],
            }
        )
    )
    definitions = yaml.safe_load(contracts_file.read_text())
    configured = build_config(**kwargs, contracts=definitions)
    policy = DeliveryConfig.model_validate(configured["delivery"])
    assert policy.policies["developer-workstream"].required_test_contract_ids == ("proof-syntax",)
    assert policy.policies["developer-integration"].required_test_contract_ids == (
        "proof-syntax",
        "proof-slug",
        "proof-limits",
    )
