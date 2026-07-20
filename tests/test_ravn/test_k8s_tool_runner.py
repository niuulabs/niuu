from __future__ import annotations

import json
from typing import Any

import pytest

from ravn.config import ResidentEvolutionConfig
from ravn.valkyrie_evolution.k8s_tool_runner import (
    DEFAULT_TOOL_RUN_IMAGE,
    NETWORK_ALLOWED_LABEL,
    NETWORK_DENIED_LABEL,
    JobRunResult,
    KubernetesJobExecutor,
    KubernetesJobLearnedToolRunner,
)
from ravn.valkyrie_evolution.learned_tools import (
    KNOWN_EXECUTION_BACKENDS,
    LearnedToolError,
    learned_tool_runner_for_backend,
)
from ravn.valkyrie_evolution.models import ToolReachGrant


class _FakeExecutor:
    enforces_reach = True

    def __init__(self, result: JobRunResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> JobRunResult:
        self.calls.append(kwargs)
        return self.result


def _write_tool(tmp_path):
    path = tmp_path / "probe.py"
    path.write_text("def run(payload):\n    return {'ok': True}\n")
    return path


@pytest.mark.asyncio
async def test_runner_requests_denied_network_and_preserves_enforcement(tmp_path) -> None:
    executor = _FakeExecutor(JobRunResult(stdout=json.dumps({"answer": 42}), network_enforced=True))
    runner = KubernetesJobLearnedToolRunner(executor=executor)

    result = await runner.run(
        _write_tool(tmp_path), {"q": "x"}, entry_point="run", timeout_seconds=30
    )

    assert result.ok
    assert result.result == {"answer": 42}
    assert result.enforcement == "enforced"
    assert executor.calls[0]["network_allowed"] is False


@pytest.mark.asyncio
async def test_runner_allows_only_broad_read_write_network_reach(tmp_path) -> None:
    executor = _FakeExecutor(JobRunResult(stdout='{"ok": true}', network_enforced=True))
    runner = KubernetesJobLearnedToolRunner(executor=executor)
    tool = _write_tool(tmp_path)

    allowed = await runner.run(
        tool,
        {},
        entry_point="run",
        timeout_seconds=30,
        declared_reach=[ToolReachGrant(kind="network", access="read_write")],
    )
    targeted = await runner.run(
        tool,
        {},
        entry_point="run",
        timeout_seconds=30,
        declared_reach=[ToolReachGrant(kind="network", target="api.example", access="read_write")],
    )
    filesystem = await runner.run(
        tool,
        {},
        entry_point="run",
        timeout_seconds=30,
        declared_reach=[ToolReachGrant(kind="filesystem", target="/tmp", access="read")],
    )

    assert allowed.ok
    assert executor.calls[0]["network_allowed"] is True
    assert not targeted.ok and "target-specific" in targeted.error
    assert not filesystem.ok and "cannot enforce declared reach" in filesystem.error
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_runner_refuses_runtime_dependency_installation(tmp_path) -> None:
    executor = _FakeExecutor(JobRunResult(stdout='{"ok": true}', network_enforced=True))
    runner = KubernetesJobLearnedToolRunner(executor=executor)

    result = await runner.run(
        _write_tool(tmp_path),
        {},
        entry_point="run",
        timeout_seconds=30,
        requirements=["httpx==1.0"],
    )

    assert not result.ok
    assert "does not install packages at invocation time" in result.error
    assert executor.calls == []


class _FakeBatchV1:
    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def create_namespaced_job(self, namespace: str, body: dict) -> None:
        self.created.append(body)
        self.jobs[body["metadata"]["name"]] = {"succeeded": 1, "failed": None}

    async def read_namespaced_job(self, name: str, namespace: str):
        return type("Job", (), {"status": self.jobs[name]})()

    async def delete_namespaced_job(self, name, namespace, **kwargs) -> None:
        self.deleted.append(name)


class _FakeCoreV1:
    def __init__(self, log: str) -> None:
        self.log = log
        self.secrets: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def create_namespaced_secret(self, namespace: str, body: dict) -> None:
        self.secrets.append(body)

    async def list_namespaced_pod(self, namespace: str, label_selector: str = ""):
        pod = type("Pod", (), {"metadata": type("M", (), {"name": "pod-1"})()})()
        return type("List", (), {"items": [pod]})()

    async def read_namespaced_pod_log(self, name: str, namespace: str, *, limit_bytes: int) -> str:
        return self.log

    async def delete_namespaced_secret(self, name, namespace) -> None:
        self.deleted.append(name)


class _FakeNetworkingV1:
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid
        self.reads: list[str] = []

    async def read_namespaced_network_policy(self, name: str, namespace: str) -> dict:
        self.reads.append(name)
        denied = name.endswith("deny")
        value = NETWORK_DENIED_LABEL if denied else NETWORK_ALLOWED_LABEL
        if not self.valid and denied:
            value = "wrong"
        return {
            "spec": {
                "podSelector": {"matchLabels": {"niuu.world/tool-network": value}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [] if denied else [{}],
            }
        }


def _executor(
    *,
    batch: _FakeBatchV1,
    core: _FakeCoreV1,
    network: _FakeNetworkingV1,
) -> KubernetesJobExecutor:
    return KubernetesJobExecutor(
        namespace="ravn",
        deny_policy_name="tool-deny",
        allow_policy_name="tool-allow",
        batch_v1=batch,
        core_v1=core,
        networking_v1=network,
        in_cluster=False,
    )


@pytest.mark.asyncio
async def test_live_executor_verifies_policies_and_builds_locked_down_job() -> None:
    batch = _FakeBatchV1()
    core = _FakeCoreV1('{"result": "ok"}')
    network = _FakeNetworkingV1()
    executor = _executor(batch=batch, core=core, network=network)
    assert executor.enforces_reach is False

    result = await executor.execute(
        run_name="ravn-tool-probe-abcd1234",
        image=DEFAULT_TOOL_RUN_IMAGE,
        code="def run(p): return {'result': 'ok'}",
        payload={"q": 1},
        entry_point="run",
        requirements=[],
        timeout_seconds=30,
        network_allowed=False,
    )

    assert result.exit_code == 0
    assert result.network_enforced is True
    assert executor.enforces_reach is True
    assert network.reads == ["tool-deny", "tool-allow"]
    job = batch.created[0]
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels["niuu.world/tool-network"] == NETWORK_DENIED_LABEL
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert "@sha256:" in container["image"]
    assert core.secrets[0]["stringData"]["payload.json"] == '{"q": 1}'
    assert batch.deleted == ["ravn-tool-probe-abcd1234"]
    assert core.deleted == ["ravn-tool-probe-abcd1234"]


@pytest.mark.asyncio
async def test_live_executor_fails_before_creating_workload_when_policy_is_wrong() -> None:
    batch = _FakeBatchV1()
    core = _FakeCoreV1("{}")
    executor = _executor(batch=batch, core=core, network=_FakeNetworkingV1(valid=False))

    with pytest.raises(LearnedToolError, match="does not deny all egress"):
        await executor.execute(
            run_name="ravn-tool-probe-abcd1234",
            image=DEFAULT_TOOL_RUN_IMAGE,
            code="def run(p): return {}",
            payload={},
            entry_point="run",
            requirements=[],
            timeout_seconds=30,
            network_allowed=False,
        )

    assert executor.enforces_reach is False
    assert batch.created == []
    assert core.secrets == []


def test_k8s_backend_requires_explicit_verified_deployment_coordinates(tmp_path) -> None:
    with pytest.raises(ValueError, match="requires learned_tool_k8s"):
        ResidentEvolutionConfig(learned_tool_execution_backend="k8s_job")

    config = ResidentEvolutionConfig(
        learned_tool_execution_backend="k8s_job",
        learned_tool_k8s={
            "namespace": "ravn",
            "deny_policy_name": "tool-deny",
            "allow_policy_name": "tool-allow",
        },
    )
    runner = learned_tool_runner_for_backend(
        config.learned_tool_execution_backend,
        workspace_root=tmp_path,
        venvs_dir=tmp_path / "venvs",
        backend_kwargs=config.learned_tool_k8s.model_dump(),
    )

    assert "k8s_job" in KNOWN_EXECUTION_BACKENDS
    assert isinstance(runner, KubernetesJobLearnedToolRunner)
    assert runner.enforces_reach is False


def test_k8s_executor_rejects_mutable_runner_image() -> None:
    with pytest.raises(LearnedToolError, match="pinned by sha256"):
        KubernetesJobExecutor(
            namespace="ravn",
            deny_policy_name="deny",
            allow_policy_name="allow",
            image="ghcr.io/niuulabs/devrunner:latest",
        )
