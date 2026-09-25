from __future__ import annotations

import datetime as dt
import json
import logging
import re
from types import SimpleNamespace
from typing import Any

import pytest

from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.adapters.tools.learned_tool_run import LearnedToolRunTool
from ravn.config import ResidentEvolutionConfig
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.k8s_tool_runner import (
    DEFAULT_TOOL_RUN_IMAGE,
    NETWORK_ALLOWED_LABEL,
    NETWORK_DENIED_LABEL,
    JobRunResult,
    KubernetesJobExecutor,
    KubernetesJobLearnedToolRunner,
    _container_started_at,
    _job_uid,
    _k8s_safe_component,
    _owner_references,
    _pod_infra_reason,
    _policy_grants_egress_to_denied_pods,
    _run_name,
    _selector_matches_denied_pod,
    _verify_run_name,
)
from ravn.valkyrie_evolution.learned_tools import (
    KNOWN_EXECUTION_BACKENDS,
    LearnedToolError,
    LearnedToolInfrastructureError,
    learned_tool_runner_for_backend,
    learned_tool_storage,
    load_learned_tool,
    read_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import ToolReachGrant
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from sleipnir.adapters.in_process import InProcessBus

_DNS1123_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


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


# --------------------------------------------------------------------------
# DNS-1123 job naming — a peer-controlled title must never be able to
# produce an invalid Kubernetes object name (str.isalnum() is Unicode-aware
# and accepts 'é'/'ツ', which a real cluster rejects with a 422).
# --------------------------------------------------------------------------


class TestDns1123SafeNaming:
    def test_run_name_strips_non_ascii_and_stays_dns1123_safe(self) -> None:
        name = _run_name("Inspect Pod é ツ 🚀 Café")

        assert _DNS1123_RE.match(name)
        assert len(name) <= 63

    def test_verify_run_name_strips_non_ascii_and_stays_dns1123_safe(self) -> None:
        name = _verify_run_name("Inspect Pod é ツ 🚀 Café")

        assert _DNS1123_RE.match(name)
        assert len(name) <= 63

    def test_component_of_only_non_ascii_falls_back_to_tool(self) -> None:
        assert _k8s_safe_component("ツツツ", max_len=20) == "tool"

    def test_component_is_truncated_to_max_len(self) -> None:
        safe = _k8s_safe_component("a" * 100, max_len=10)

        assert safe == "a" * 10


class TestPolicyGrantsEgressToDeniedPods:
    def test_ingress_only_policy_never_concerns(self) -> None:
        data = {
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress"],
                "egress": [{}],
            }
        }

        assert (
            _policy_grants_egress_to_denied_pods(data, label_key="niuu.world/tool-network") is False
        )

    def test_egress_type_with_no_rules_grants_nothing(self) -> None:
        data = {
            "spec": {
                "podSelector": {"matchLabels": {"niuu.world/tool-network": "denied"}},
                "policyTypes": ["Egress"],
                "egress": [],
            }
        }

        assert (
            _policy_grants_egress_to_denied_pods(data, label_key="niuu.world/tool-network") is False
        )

    def test_explicit_denied_label_match_concerns(self) -> None:
        data = {
            "spec": {
                "podSelector": {"matchLabels": {"niuu.world/tool-network": "denied"}},
                "policyTypes": ["Egress"],
                "egress": [{}],
            }
        }

        assert (
            _policy_grants_egress_to_denied_pods(data, label_key="niuu.world/tool-network") is True
        )

    def test_match_expression_selecting_denied_label_concerns(self) -> None:
        data = {
            "spec": {
                "podSelector": {
                    "matchExpressions": [
                        {
                            "key": "niuu.world/tool-network",
                            "operator": "In",
                            "values": ["denied", "allowed"],
                        }
                    ]
                },
                "policyTypes": ["Egress"],
                "egress": [{}],
            }
        }

        assert (
            _policy_grants_egress_to_denied_pods(data, label_key="niuu.world/tool-network") is True
        )

    def test_unrelated_label_selector_does_not_concern(self) -> None:
        data = {
            "spec": {
                "podSelector": {"matchLabels": {"some-other-label": "x"}},
                "policyTypes": ["Egress"],
                "egress": [{}],
            }
        }

        assert (
            _policy_grants_egress_to_denied_pods(data, label_key="niuu.world/tool-network") is False
        )

    def test_omitted_policy_types_with_egress_rules_still_applies_to_egress(self) -> None:
        """Kubernetes defaults an omitted policyTypes to ["Egress"] (in
        addition to Ingress) whenever the policy carries egress rules."""
        data = {
            "spec": {
                "podSelector": {"matchLabels": {"niuu.world/tool-network": "denied"}},
                "policyTypes": [],
                "egress": [{}],
            }
        }

        assert (
            _policy_grants_egress_to_denied_pods(data, label_key="niuu.world/tool-network") is True
        )

    def test_omitted_policy_types_with_no_egress_rules_does_not_apply(self) -> None:
        data = {
            "spec": {
                "podSelector": {"matchLabels": {"niuu.world/tool-network": "denied"}},
                "policyTypes": [],
                "egress": [],
            }
        }

        assert (
            _policy_grants_egress_to_denied_pods(data, label_key="niuu.world/tool-network") is False
        )


class TestSelectorMatchesDeniedPod:
    """Table-driven: every matchExpressions operator, evaluated against a
    denied pod's actual, guaranteed label set — our own label (exact value
    'denied'), the Job-controller-injected keys (present, value unknowable
    in advance), and any other key (genuinely absent, since the Job body
    sets no other pod label). An unrecognised operator always fails closed
    (treated as matching)."""

    LABEL_KEY = "niuu.world/tool-network"

    @pytest.mark.parametrize(
        ("selector", "expected"),
        [
            # An empty selector matches every pod in the namespace.
            ({}, True),
            # matchLabels on our own key.
            ({"matchLabels": {LABEL_KEY: "denied"}}, True),
            ({"matchLabels": {LABEL_KEY: "allowed"}}, False),
            # Our own label key, every operator.
            (
                {"matchExpressions": [{"key": LABEL_KEY, "operator": "In", "values": ["denied"]}]},
                True,
            ),
            (
                {"matchExpressions": [{"key": LABEL_KEY, "operator": "In", "values": ["allowed"]}]},
                False,
            ),
            (
                {
                    "matchExpressions": [
                        {"key": LABEL_KEY, "operator": "NotIn", "values": ["allowed"]}
                    ]
                },
                True,
            ),
            (
                {
                    "matchExpressions": [
                        {"key": LABEL_KEY, "operator": "NotIn", "values": ["denied"]}
                    ]
                },
                False,
            ),
            ({"matchExpressions": [{"key": LABEL_KEY, "operator": "Exists"}]}, True),
            ({"matchExpressions": [{"key": LABEL_KEY, "operator": "DoesNotExist"}]}, False),
            ({"matchExpressions": [{"key": LABEL_KEY, "operator": "Bogus"}]}, True),
            # An unrelated key (e.g. "app") our pod template never sets —
            # genuinely absent, standard Kubernetes absent-key semantics.
            ({"matchLabels": {"app": "foo"}}, False),
            (
                {"matchExpressions": [{"key": "app", "operator": "In", "values": ["foo"]}]},
                False,
            ),
            (
                {"matchExpressions": [{"key": "app", "operator": "NotIn", "values": ["foo"]}]},
                True,
            ),
            ({"matchExpressions": [{"key": "app", "operator": "Exists"}]}, False),
            ({"matchExpressions": [{"key": "app", "operator": "DoesNotExist"}]}, True),
            ({"matchExpressions": [{"key": "app", "operator": "Bogus"}]}, True),
            # Job-controller-injected keys: present on every pod, but the
            # exact value (job name/uid) cannot be known in advance.
            ({"matchExpressions": [{"key": "job-name", "operator": "Exists"}]}, True),
            ({"matchExpressions": [{"key": "job-name", "operator": "DoesNotExist"}]}, False),
            (
                {
                    "matchExpressions": [
                        {"key": "job-name", "operator": "In", "values": ["some-job"]}
                    ]
                },
                True,
            ),
            (
                {
                    "matchExpressions": [
                        {"key": "job-name", "operator": "NotIn", "values": ["some-job"]}
                    ]
                },
                True,
            ),
            (
                {
                    "matchExpressions": [
                        {"key": "batch.kubernetes.io/controller-uid", "operator": "Exists"}
                    ]
                },
                True,
            ),
        ],
    )
    def test_operator_against_denied_pod_labels(
        self, selector: dict[str, Any], expected: bool
    ) -> None:
        assert _selector_matches_denied_pod(selector, label_key=self.LABEL_KEY) is expected

    def test_all_terms_must_match_and_semantics(self) -> None:
        """matchLabels and matchExpressions terms are ANDed: our pod, which
        genuinely has no 'app' label, does not satisfy a selector requiring
        both our denied label AND app=foo."""
        selector = {
            "matchLabels": {self.LABEL_KEY: "denied"},
            "matchExpressions": [{"key": "app", "operator": "Exists"}],
        }

        assert _selector_matches_denied_pod(selector, label_key=self.LABEL_KEY) is False


class TestPodInfraReason:
    def test_disruption_target_condition_is_infra(self) -> None:
        pod = {
            "status": {
                "phase": "Running",
                "conditions": [{"type": "DisruptionTarget", "message": "node draining"}],
            }
        }

        assert _pod_infra_reason(pod) == "disruption target: node draining"

    def test_pending_with_no_container_status_is_infra(self) -> None:
        pod = {"status": {"phase": "Pending"}}

        assert (
            _pod_infra_reason(pod) == "pod never started (still Pending with no container status)"
        )

    def test_running_pod_is_not_an_infra_reason(self) -> None:
        pod = {"status": {"phase": "Running"}}

        assert _pod_infra_reason(pod) is None

    def test_non_dict_pod_is_not_an_infra_reason(self) -> None:
        assert _pod_infra_reason(object()) is None

    @pytest.mark.parametrize(
        "message",
        [
            'Usage of EmptyDir volume "tmp" exceeds the limit "64Mi"',
            "Pod ephemeral local storage usage exceeds the total limit of containers",
            "Container tool was using 200Mi, which exceeds its request of 64Mi memory",
            "node pressure",
            "",
        ],
    )
    def test_self_inflicted_eviction_is_not_an_infra_reason(self, message: str) -> None:
        pod = {"status": {"phase": "Failed", "reason": "Evicted", "message": message}}

        assert _pod_infra_reason(pod) is None

    @pytest.mark.parametrize(
        "message",
        [
            "Pod was terminated due to node drain",
            "The pod was preempted by a higher priority pod",
            "deleted due to NoExecute taint",
        ],
    )
    def test_cluster_driven_eviction_is_an_infra_reason(self, message: str) -> None:
        pod = {"status": {"phase": "Failed", "reason": "Evicted", "message": message}}

        reason = _pod_infra_reason(pod)

        assert reason is not None
        assert "evicted" in reason


class TestJobUidAndOwnerReferences:
    def test_uid_from_an_object_style_job(self) -> None:
        job = SimpleNamespace(metadata=SimpleNamespace(uid="abc-123"))

        assert _job_uid(job) == "abc-123"

    def test_uid_from_a_dict_style_job(self) -> None:
        job = {"metadata": {"uid": "abc-123"}}

        assert _job_uid(job) == "abc-123"

    def test_uid_missing_is_empty_string(self) -> None:
        assert _job_uid({"metadata": {}}) == ""
        assert _job_uid({}) == ""
        assert _job_uid(object()) == ""

    def test_owner_references_empty_when_uid_unknown(self) -> None:
        assert _owner_references(name="some-job", uid="") == []

    def test_owner_references_points_at_the_job(self) -> None:
        refs = _owner_references(name="some-job", uid="abc-123")

        assert refs == [
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "name": "some-job",
                "uid": "abc-123",
                "controller": True,
                "blockOwnerDeletion": True,
            }
        ]


class TestContainerStartedAt:
    def test_running_container_is_started(self) -> None:
        pod = {
            "status": {
                "container_statuses": [
                    {"state": {"running": {"started_at": "2020-01-01T00:00:00Z"}}}
                ]
            }
        }

        started_at = _container_started_at(pod)

        assert started_at is not None
        assert started_at.year == 2020

    def test_real_client_returns_a_naive_datetime_object_not_a_string(self) -> None:
        """The real kubernetes_asyncio client deserialises timestamps into
        datetime objects, not strings — a naive one gets UTC attached."""
        naive = dt.datetime(2020, 1, 1)  # noqa: DTZ001 - deliberately naive, matching a raw client value
        pod = {"status": {"container_statuses": [{"state": {"running": {"started_at": naive}}}]}}

        started_at = _container_started_at(pod)

        assert started_at is not None
        assert started_at.tzinfo is dt.UTC

    def test_real_client_returns_an_already_aware_datetime_object(self) -> None:
        aware = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        pod = {"status": {"container_statuses": [{"state": {"running": {"started_at": aware}}}]}}

        assert _container_started_at(pod) == aware

    def test_malformed_timestamp_string_is_skipped(self) -> None:
        pod = {
            "status": {
                "container_statuses": [{"state": {"running": {"started_at": "not-a-timestamp"}}}]
            }
        }

        assert _container_started_at(pod) is None

    def test_terminated_container_is_started(self) -> None:
        pod = {
            "status": {
                "container_statuses": [
                    {"state": {"terminated": {"started_at": "2020-01-01T00:00:00Z"}}}
                ]
            }
        }

        assert _container_started_at(pod) is not None

    def test_waiting_container_is_not_started(self) -> None:
        pod = {
            "status": {
                "container_statuses": [{"state": {"waiting": {"reason": "ContainerCreating"}}}]
            }
        }

        assert _container_started_at(pod) is None

    def test_failed_phase_with_no_container_status_is_not_started(self) -> None:
        """A kubelet admission refusal (OutOfcpu, NodeAffinity) can put a
        pod in phase Failed without its container ever having run — phase
        alone must never be read as 'started'."""
        pod = {"status": {"phase": "Failed"}}

        assert _container_started_at(pod) is None

    def test_non_dict_pod_is_not_started(self) -> None:
        assert _container_started_at(object()) is None


class _FakeBatchV1:
    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def create_namespaced_job(self, namespace: str, body: dict):
        self.created.append(body)
        name = body["metadata"]["name"]
        self.jobs[name] = {"succeeded": 1, "failed": None}
        return SimpleNamespace(metadata=SimpleNamespace(uid=f"uid-{name}"))

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
    def __init__(
        self,
        *,
        valid: bool = True,
        extra_policies: list[dict[str, Any]] | None = None,
    ) -> None:
        self.valid = valid
        self.reads: list[str] = []
        self.list_calls = 0
        self._extra_policies = extra_policies or []

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

    async def list_namespaced_network_policy(self, namespace: str):
        self.list_calls += 1
        return SimpleNamespace(items=list(self._extra_policies))


def _executor(
    *,
    batch: _FakeBatchV1,
    core: _FakeCoreV1,
    network: _FakeNetworkingV1,
    **overrides: Any,
) -> KubernetesJobExecutor:
    return KubernetesJobExecutor(
        namespace="ravn",
        deny_policy_name="tool-deny",
        allow_policy_name="tool-allow",
        batch_v1=batch,
        core_v1=core,
        networking_v1=network,
        in_cluster=False,
        **overrides,
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
    assert pod["enableServiceLinks"] is False
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert "@sha256:" in container["image"]
    assert job["spec"]["ttlSecondsAfterFinished"] == 3600
    # run() has no separate pod-start budget concept: activeDeadlineSeconds
    # is exactly the invocation timeout, unlike verify()'s combined budget.
    assert job["spec"]["activeDeadlineSeconds"] == 30
    assert core.secrets[0]["stringData"]["payload.json"] == '{"q": 1}'
    assert core.secrets[0]["metadata"]["labels"]["app.kubernetes.io/managed-by"]
    # Job created first, so the Secret can carry an ownerReference to its
    # real UID — a GC backstop independent of the explicit delete and TTL.
    owner_refs = core.secrets[0]["metadata"]["ownerReferences"]
    assert owner_refs[0]["kind"] == "Job"
    assert owner_refs[0]["name"] == "ravn-tool-probe-abcd1234"
    assert owner_refs[0]["uid"] == "uid-ravn-tool-probe-abcd1234"
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


@pytest.mark.asyncio
async def test_live_executor_ttl_omitted_when_disabled() -> None:
    batch = _FakeBatchV1()
    core = _FakeCoreV1("{}")
    executor = _executor(
        batch=batch, core=core, network=_FakeNetworkingV1(), job_ttl_seconds_after_finished=0
    )

    await executor.execute(
        run_name="ravn-tool-probe-ttl0",
        image=DEFAULT_TOOL_RUN_IMAGE,
        code="def run(p): return {}",
        payload={},
        entry_point="run",
        requirements=[],
        timeout_seconds=30,
        network_allowed=False,
    )

    assert "ttlSecondsAfterFinished" not in batch.created[0]["spec"]


@pytest.mark.asyncio
async def test_live_executor_skips_additive_check_for_an_allowed_network_run() -> None:
    """The additive NetworkPolicy check only protects the deny guarantee —
    an allowed-network invocation has nothing for it to protect, so it must
    not need the `list` RBAC verb or be able to break on an unrelated
    namespace-wide policy."""
    batch = _FakeBatchV1()
    core = _FakeCoreV1("{}")
    network = _FakeNetworkingV1()
    executor = _executor(batch=batch, core=core, network=network)

    await executor.execute(
        run_name="ravn-tool-probe-allowed",
        image=DEFAULT_TOOL_RUN_IMAGE,
        code="def run(p): return {}",
        payload={},
        entry_point="run",
        requirements=[],
        timeout_seconds=30,
        network_allowed=True,
    )

    assert network.list_calls == 0


@pytest.mark.asyncio
async def test_live_executor_runs_additive_check_for_a_denied_network_run() -> None:
    batch = _FakeBatchV1()
    core = _FakeCoreV1("{}")
    network = _FakeNetworkingV1()
    executor = _executor(batch=batch, core=core, network=network)

    await executor.execute(
        run_name="ravn-tool-probe-denied",
        image=DEFAULT_TOOL_RUN_IMAGE,
        code="def run(p): return {}",
        payload={},
        entry_point="run",
        requirements=[],
        timeout_seconds=30,
        network_allowed=False,
    )

    assert network.list_calls == 1


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


# --------------------------------------------------------------------------
# verify() — a fake Kubernetes API client covering the single denied-network
# test Job, requirements rejection, infra-vs-test-failure classification
# (including a genuine test hang vs. a pod that never started), the
# additive-NetworkPolicy check, and cleanup.
# --------------------------------------------------------------------------


class _FakeVerifyBatchV1:
    def __init__(
        self,
        outcome: dict[str, Any] | None = None,
        *,
        fail_create: bool = False,
    ) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self._outcome = outcome if outcome is not None else {"succeeded": 1, "failed": None}
        self._fail_create = fail_create

    async def create_namespaced_job(self, namespace: str, body: dict) -> SimpleNamespace:
        if self._fail_create:
            raise RuntimeError("simulated Job creation failure")
        name = body["metadata"]["name"]
        self.created.append(body)
        self.jobs[name] = dict(self._outcome)
        return SimpleNamespace(metadata=SimpleNamespace(uid=f"uid-{name}"))

    async def read_namespaced_job(self, name: str, namespace: str):
        return SimpleNamespace(status=self.jobs[name])

    async def delete_namespaced_job(self, name: str, namespace: str, **kwargs: Any) -> None:
        self.deleted.append(name)


class _FakeVerifyCoreV1:
    def __init__(
        self,
        *,
        log: str = "",
        pod_status: dict[str, Any] | None = None,
        fail_delete_secret: bool = False,
        fail_create_secret: bool = False,
    ) -> None:
        self.log = log
        self._pod_status = pod_status or {}
        self.secrets: list[dict[str, Any]] = []
        self.deleted_secrets: list[str] = []
        self._fail_delete_secret = fail_delete_secret
        self._fail_create_secret = fail_create_secret

    async def create_namespaced_secret(self, namespace: str, body: dict) -> None:
        if self._fail_create_secret:
            raise RuntimeError("simulated Secret create failure")
        self.secrets.append(body)

    async def delete_namespaced_secret(self, name: str, namespace: str) -> None:
        if self._fail_delete_secret:
            raise RuntimeError("simulated Secret delete failure")
        self.deleted_secrets.append(name)

    async def list_namespaced_pod(self, namespace: str, label_selector: str = ""):
        job_name = label_selector.split("=", 1)[1] if "=" in label_selector else ""
        pod = {"metadata": {"name": f"{job_name}-pod"}, "status": dict(self._pod_status)}
        return SimpleNamespace(items=[pod])

    async def read_namespaced_pod_log(self, name: str, namespace: str, *, limit_bytes: int) -> str:
        return self.log


def _verify_executor(
    *,
    batch: _FakeVerifyBatchV1,
    core: _FakeVerifyCoreV1,
    network: _FakeNetworkingV1 | None = None,
    **overrides: Any,
) -> KubernetesJobExecutor:
    return KubernetesJobExecutor(
        namespace="ravn",
        deny_policy_name="tool-deny",
        allow_policy_name="tool-allow",
        batch_v1=batch,
        core_v1=core,
        networking_v1=network or _FakeNetworkingV1(),
        in_cluster=False,
        **overrides,
    )


_OK_TEST_CODE = (
    "import _verify_tool\n\ndef test_ok():\n    assert _verify_tool.run({}) == {'ok': True}\n"
)
_OK_TOOL_CODE = "def run(input):\n    return {'ok': True}\n"
#: A pod status whose container has unambiguously been running since well
#: before any test's timeout budget — the test's own hang-timeout clock
#: starts at this timestamp, never at Job-creation/poll time.
_RUNNING_SINCE_2020 = {
    "phase": "Running",
    "container_statuses": [{"state": {"running": {"started_at": "2020-01-01T00:00:00Z"}}}],
}


class TestKubernetesJobVerify:
    async def test_verify_skips_execution_for_static_defects(self) -> None:
        class _UnreachableExecutor:
            enforces_reach = True

            async def execute_verification(self, **kwargs: Any) -> JobRunResult:
                raise AssertionError("must never reach the k8s API: static analysis rejects first")

        runner = KubernetesJobLearnedToolRunner(executor=_UnreachableExecutor())  # type: ignore[arg-type]

        result = await runner.verify(
            tool_name="undeclared_import_tool",
            tool_code="import requests\n\ndef run(input):\n    return {}\n",
            test_code="import _verify_tool\n\ndef test_ok():\n    pass\n",
            requirements=[],
        )

        assert not result.ok
        assert result.missing_module == "requests"

    async def test_verify_rejects_declared_requirements_without_touching_the_api(self) -> None:
        class _UnreachableExecutor:
            enforces_reach = True

            async def execute_verification(self, **kwargs: Any) -> JobRunResult:
                raise AssertionError("k8s_job must decline requirements before reaching the API")

        runner = KubernetesJobLearnedToolRunner(executor=_UnreachableExecutor())  # type: ignore[arg-type]

        result = await runner.verify(
            tool_name="net_tool",
            tool_code=_OK_TOOL_CODE,
            test_code=_OK_TEST_CODE,
            requirements=["requests==2.31.0"],
        )

        assert not result.ok
        assert "baked into the runner image" in result.logs

    async def test_verify_empty_test_code_is_structural_only(self) -> None:
        class _UnreachableExecutor:
            enforces_reach = True

            async def execute_verification(self, **kwargs: Any) -> JobRunResult:
                raise AssertionError("must never reach the k8s API with no test_code")

        runner = KubernetesJobLearnedToolRunner(executor=_UnreachableExecutor())  # type: ignore[arg-type]

        result = await runner.verify(tool_name="t", tool_code=_OK_TOOL_CODE, test_code="   ")

        assert result.ok
        assert "structural validation only" in result.logs

    async def test_verify_without_requirements_runs_one_denied_network_job(self) -> None:
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1(log="verify: ran 1 test callable(s)")
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        result = await runner.verify(
            tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
        )

        assert result.ok
        assert "ran 1 test callable" in result.logs
        assert len(batch.created) == 1
        job = batch.created[0]
        labels = job["spec"]["template"]["metadata"]["labels"]
        assert labels["niuu.world/tool-network"] == NETWORK_DENIED_LABEL
        pod_spec = job["spec"]["template"]["spec"]
        assert pod_spec["automountServiceAccountToken"] is False
        assert pod_spec["enableServiceLinks"] is False
        container = pod_spec["containers"][0]
        assert container["command"] == [
            "python",
            "/verify/_verify_runner.py",
            "/verify/_verify_tool.py",
            "/verify/_verify_test.py",
        ]
        assert "@sha256:" in container["image"]
        assert job["spec"]["ttlSecondsAfterFinished"] == 3600
        # activeDeadlineSeconds covers BOTH budgets (default verify timeout
        # 120s + default pod-start budget 120s) — never just the test
        # timeout, or Kubernetes could kill the Job before the pod even
        # finished starting.
        assert job["spec"]["activeDeadlineSeconds"] == 240
        # Fixed file names in the Secret, always — never derived from the
        # peer-controlled title (see the collision regression test below).
        assert set(core.secrets[0]["stringData"]) == {
            "_verify_tool.py",
            "_verify_test.py",
            "_verify_runner.py",
        }
        assert core.secrets[0]["stringData"]["_verify_tool.py"] == _OK_TOOL_CODE
        assert core.secrets[0]["metadata"]["labels"]["app.kubernetes.io/managed-by"]
        assert batch.deleted == [job["metadata"]["name"]]
        assert core.deleted_secrets == [job["metadata"]["name"]]

    async def test_verify_title_colliding_with_the_test_file_name_never_swaps_them(self) -> None:
        """A peer titling its tool `_verify_test` (the fixed test-file name)
        must never make that title collide with anything — file names in
        the Secret are fixed, never derived from the title."""
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1(log="verify: ran 1 test callable(s)")
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        result = await runner.verify(
            tool_name="_verify_test",
            tool_code="def run(input):\n    return {'from': 'real_tool'}\n",
            test_code=(
                "import _verify_tool\n\n"
                "def test_ok():\n"
                "    assert _verify_tool.run({})['from'] == 'real_tool'\n"
            ),
        )

        assert result.ok
        stored = core.secrets[0]["stringData"]
        assert "real_tool" in stored["_verify_tool.py"]
        assert "_verify_tool.run" in stored["_verify_test.py"]
        assert stored["_verify_tool.py"] != stored["_verify_test.py"]

    async def test_verify_reports_a_failing_test_without_raising(self) -> None:
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": 1})
        core = _FakeVerifyCoreV1(log="AssertionError: expected True, got False")
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        result = await runner.verify(
            tool_name="broken_tool",
            tool_code="def run(input):\n    return {'ok': False}\n",
            test_code=_OK_TEST_CODE,
        )

        assert not result.ok
        assert "AssertionError" in result.logs

    async def test_verify_hung_test_after_container_started_is_a_failed_result_not_infra(
        self,
    ) -> None:
        """The core MUST-FIX: a test that hangs past the deadline, once its
        container has actually started, is a durable failed verification —
        never infrastructure. A peer whose tests hang must be rejected, not
        retried forever because every redelivery attempt 'looks like' an
        outage."""
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": None})
        core = _FakeVerifyCoreV1(
            log="partial output before the hang", pod_status=_RUNNING_SINCE_2020
        )
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        result = await runner.verify(
            tool_name="hung_tool",
            tool_code=_OK_TOOL_CODE,
            test_code=_OK_TEST_CODE,
            timeout_seconds=0.05,
        )

        assert not result.ok
        # Partial pod output, when there is any, beats a generic message.
        assert result.logs == "partial output before the hang"
        # Still cleaned up, same as any other outcome.
        assert batch.deleted == [batch.created[0]["metadata"]["name"]]

    async def test_verify_hung_test_with_no_output_reports_a_generic_timeout_message(
        self,
    ) -> None:
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": None})
        core = _FakeVerifyCoreV1(pod_status=_RUNNING_SINCE_2020)
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        result = await runner.verify(
            tool_name="hung_tool",
            tool_code=_OK_TOOL_CODE,
            test_code=_OK_TEST_CODE,
            timeout_seconds=0.05,
        )

        assert not result.ok
        assert "timed out after 0.05s" in result.logs

    async def test_verify_image_pull_failure_raises_infrastructure_error(self) -> None:
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": 1})
        core = _FakeVerifyCoreV1(
            pod_status={
                "phase": "Pending",
                "container_statuses": [
                    {
                        "state": {
                            "waiting": {
                                "reason": "ImagePullBackOff",
                                "message": "rate limited",
                            }
                        }
                    }
                ],
            }
        )
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with pytest.raises(LearnedToolInfrastructureError, match="ImagePullBackOff"):
            await runner.verify(
                tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
            )

    async def test_cluster_driven_eviction_raises_infrastructure_error(self) -> None:
        """Preemption/drain/taint-manager evictions are genuinely
        infrastructure — nothing the peer's code did caused them."""
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": 1})
        core = _FakeVerifyCoreV1(
            pod_status={
                "phase": "Failed",
                "reason": "Evicted",
                "message": "Pod was terminated due to node drain",
            }
        )
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with pytest.raises(LearnedToolInfrastructureError, match="evicted"):
            await runner.verify(
                tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
            )

    async def test_self_inflicted_eviction_is_a_declined_result_not_infra(self) -> None:
        """MUST-FIX: a test that writes past its ephemeral-storage/emptyDir
        limit is evicted for ITS OWN resource usage. Treating that as
        infrastructure let a peer force retries forever instead of a
        durable rejection."""
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": 1})
        core = _FakeVerifyCoreV1(
            log="test wrote too much",
            pod_status={
                "phase": "Failed",
                "reason": "Evicted",
                "message": 'Usage of EmptyDir volume "tmp" exceeds the limit "64Mi"',
            },
        )
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        result = await runner.verify(
            tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
        )

        assert not result.ok

    async def test_verify_unschedulable_pod_raises_infrastructure_error(self) -> None:
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": None})
        core = _FakeVerifyCoreV1(
            pod_status={
                "phase": "Pending",
                "conditions": [
                    {
                        "type": "PodScheduled",
                        "status": "False",
                        "reason": "Unschedulable",
                        "message": "0/3 nodes available: insufficient cpu",
                    }
                ],
            }
        )
        # The overall test timeout no longer bounds "waiting for the pod to
        # start" — that is job_pod_start_timeout_seconds' own job, on
        # purpose (MUST-FIX: a slow image pull must never eat into the
        # test's own budget) — so it is overridden here to keep the test fast.
        executor = _verify_executor(batch=batch, core=core, job_pod_start_timeout_seconds=0.01)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with pytest.raises(LearnedToolInfrastructureError, match="unschedulable"):
            await runner.verify(
                tool_name="echo_tool",
                tool_code=_OK_TOOL_CODE,
                test_code=_OK_TEST_CODE,
                timeout_seconds=30,
            )

    async def test_verify_pod_start_budget_elapsing_raises_infrastructure_error(self) -> None:
        """Distinct from the overall timeout: the pod never leaves Pending
        (no diagnosable reason) within its own, separately-configured
        pod-start budget."""
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": None})
        core = _FakeVerifyCoreV1()
        executor = _verify_executor(batch=batch, core=core, job_pod_start_timeout_seconds=0.01)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with pytest.raises(LearnedToolInfrastructureError, match="pod did not start within"):
            await runner.verify(
                tool_name="echo_tool",
                tool_code=_OK_TOOL_CODE,
                test_code=_OK_TEST_CODE,
                timeout_seconds=30,
            )

    async def test_verify_100s_pull_then_a_30s_test_gets_its_full_30s(self) -> None:
        """The exact scenario the MUST-FIX names: a slow image pull must
        never eat into the test's own timeout budget. Simulated here by a
        pod that has already been running (started_at far in the past) —
        the test gets its full configured timeout_seconds regardless of how
        long the pod took to start, because the hang clock starts at
        startedAt, not at Job creation."""
        batch = _FakeVerifyBatchV1({"succeeded": None, "failed": None})
        core = _FakeVerifyCoreV1(pod_status=_RUNNING_SINCE_2020)
        executor = _verify_executor(batch=batch, core=core, job_pod_start_timeout_seconds=100)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        result = await runner.verify(
            tool_name="echo_tool",
            tool_code=_OK_TOOL_CODE,
            test_code=_OK_TEST_CODE,
            timeout_seconds=30,
        )

        # A container running since 2020 has certainly run for >= 30s, so
        # this resolves as a hang (declined), never as "pod did not start".
        assert not result.ok
        assert "did not start" not in result.logs

    async def test_verify_missing_network_policy_raises_infrastructure_error(self) -> None:
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1()
        executor = _verify_executor(batch=batch, core=core, network=_FakeNetworkingV1(valid=False))
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with pytest.raises(LearnedToolInfrastructureError, match="does not deny all egress"):
            await runner.verify(
                tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
            )

        assert batch.created == []

    async def test_executor_level_requirements_check_is_defense_in_depth(self) -> None:
        """The runner already declines requirements before ever calling the
        executor; execute_verification() checks again itself, the same
        belt-and-suspenders posture execute() takes for its own checks."""
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1()
        executor = _verify_executor(batch=batch, core=core)

        with pytest.raises(LearnedToolError, match="requirements must be empty"):
            await executor.execute_verification(
                run_name="ravn-verify-direct",
                image=DEFAULT_TOOL_RUN_IMAGE,
                tool_name="t",
                tool_code=_OK_TOOL_CODE,
                test_code=_OK_TEST_CODE,
                requirements=["requests"],
                timeout_seconds=30,
            )

    async def test_executor_level_image_pin_check_is_defense_in_depth(self) -> None:
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1()
        executor = _verify_executor(batch=batch, core=core)

        with pytest.raises(LearnedToolError, match="pinned by sha256"):
            await executor.execute_verification(
                run_name="ravn-verify-direct",
                image="ghcr.io/niuulabs/devrunner:latest",
                tool_name="t",
                tool_code=_OK_TOOL_CODE,
                test_code=_OK_TEST_CODE,
                requirements=[],
                timeout_seconds=30,
            )

    async def test_verify_additive_network_policy_granting_denied_egress_raises(self) -> None:
        """NetworkPolicies are additive: an unrelated namespace-wide policy
        (e.g. 'allow DNS for everyone') can grant learned-tool denied pods
        egress even though the two verified policies look correct alone."""
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1()
        extra = {
            "metadata": {"name": "allow-dns-everyone"},
            "spec": {
                "podSelector": {},
                "policyTypes": ["Egress"],
                "egress": [{"ports": [{"port": 53}]}],
            },
        }
        network = _FakeNetworkingV1(extra_policies=[extra])
        executor = _verify_executor(batch=batch, core=core, network=network)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with pytest.raises(LearnedToolInfrastructureError, match="allow-dns-everyone"):
            await runner.verify(
                tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
            )

        assert batch.created == []

    async def test_verify_unrelated_extra_network_policy_does_not_block(self) -> None:
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1(log="verify: ran 1 test callable(s)")
        extra = {
            "metadata": {"name": "allow-metrics-scrape"},
            "spec": {
                "podSelector": {"matchLabels": {"some-other-label": "x"}},
                "policyTypes": ["Egress"],
                "egress": [{"ports": [{"port": 9090}]}],
            },
        }
        network = _FakeNetworkingV1(extra_policies=[extra])
        executor = _verify_executor(batch=batch, core=core, network=network)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        result = await runner.verify(
            tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
        )

        assert result.ok

    async def test_verify_job_creation_failure_leaves_nothing_to_clean_up(self) -> None:
        """The Job is created before the Secret (so the Secret can carry an
        ownerReference to the Job's real UID); if Job creation itself
        fails, nothing was created at all, and cleanup must delete
        nothing."""
        batch = _FakeVerifyBatchV1(fail_create=True)
        core = _FakeVerifyCoreV1()
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with pytest.raises(LearnedToolInfrastructureError):
            await runner.verify(
                tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
            )

        assert batch.deleted == []
        assert core.deleted_secrets == []

    async def test_verify_secret_creation_failure_still_cleans_up_the_job(self) -> None:
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1(fail_create_secret=True)
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with pytest.raises(RuntimeError, match="simulated Secret create failure"):
            await runner.verify(
                tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
            )

        assert len(batch.deleted) == 1
        assert core.deleted_secrets == []

    async def test_verify_secret_carries_an_owner_reference_to_its_job(self) -> None:
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1(log="verify: ran 1 test callable(s)")
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        await runner.verify(tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE)

        job = batch.created[0]
        secret = core.secrets[0]
        owner_refs = secret["metadata"]["ownerReferences"]
        assert len(owner_refs) == 1
        assert owner_refs[0]["kind"] == "Job"
        assert owner_refs[0]["name"] == job["metadata"]["name"]
        assert owner_refs[0]["uid"] == f"uid-{job['metadata']['name']}"
        assert owner_refs[0]["blockOwnerDeletion"] is True

    async def test_verify_cleanup_failure_is_logged_not_raised(self, caplog) -> None:
        """A teardown failure (Secret delete) must never override a real
        verification result — it is logged loudly instead."""
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1(log="verify: ran 1 test callable(s)", fail_delete_secret=True)
        executor = _verify_executor(batch=batch, core=core)
        runner = KubernetesJobLearnedToolRunner(executor=executor)

        with caplog.at_level(logging.ERROR, logger="ravn.valkyrie_evolution.k8s_tool_runner"):
            result = await runner.verify(
                tool_name="echo_tool", tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE
            )

        assert result.ok
        assert core.deleted_secrets == []
        assert any("Secret" in record.message for record in caplog.records)


# --------------------------------------------------------------------------
# End-to-end: peer receives a proposal, re-verifies it via k8s_job, adopts
# it, and later runs it through the learned_tool_run dispatch tool.
# --------------------------------------------------------------------------


class _FakeE2EExecutor:
    """Backs both verify() (peer re-verification) and execute() (the canary
    run during adoption, and every later learned_tool_run dispatch) — the
    real KubernetesJobExecutor would run both through the same NetworkPolicy-
    verified Job mechanics; this fake just records and always succeeds."""

    enforces_reach = True

    def __init__(self) -> None:
        self.verify_calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []

    async def execute_verification(self, **kwargs: Any) -> JobRunResult:
        self.verify_calls.append(kwargs)
        return JobRunResult(stdout="verify: ran 1 test callable(s)", exit_code=0)

    async def execute(self, **kwargs: Any) -> JobRunResult:
        self.run_calls.append(kwargs)
        return JobRunResult(
            stdout=json.dumps({"ok": True, "echo": kwargs.get("payload")}),
            exit_code=0,
            network_enforced=True,
        )


def _skill_manager(tmp_path, name: str) -> SkillManagementRegistry:
    skill_dir = tmp_path / name / "skills"
    registry = FileSkillRegistry(
        skill_dirs=[str(skill_dir)],
        write_dir=skill_dir,
        include_builtin=False,
    )
    return SkillManagementRegistry(
        registry, metadata_path=tmp_path / name / "skill_management.json"
    )


def _peer_agent_tool_artifact(
    *,
    tool_code: str,
    test_code: str,
    manifest_name: str = "inspect_oomkilled_pod",
    requirements: list[str] | None = None,
) -> ResidentLearningArtifact:
    return ResidentLearningArtifact(
        learning_id=f"learn-{manifest_name}",
        title=manifest_name,
        summary="Inspect an OOMKilled pod signal.",
        content="",
        artifact_type="agent_tool",
        scope="flock",
        confidence=0.74,
        source_environment_id="cluster-a",
        source_valkyrie_id="valkyrie:k8s-a",
        promotion_id=f"learn-{manifest_name}",
        flock_id="k8s-valkyries",
        domain="k8s",
        redaction_status="none",
        tool_code=tool_code,
        tool_entry_point="run",
        learned_tool_manifest={
            "name": manifest_name,
            "description": "Inspect an OOMKilled pod signal.",
            "input_schema": {"type": "object"},
            "required_permission": "k8s:read",
        },
        test_code=test_code,
        canary_sample={"payload": {"reason": "OOMKilled"}},
        requirements=requirements or [],
    )


def _peer_runtime(
    tmp_path,
    *,
    runner: Any,
    name: str = "cluster-k8s-peer",
) -> tuple[ResidentLearningRuntime, Any]:
    bus = InProcessBus()
    peer_skills = _skill_manager(tmp_path, name)
    peer = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id=name,
            valkyrie_id=f"valkyrie:{name}",
            domain="k8s",
            flock_ids=["k8s-valkyries"],
            autonomy_mode="yolo",
        ),
        skills=peer_skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "peer" / "tools",
        learned_tool_runner=runner,
    )
    return peer, bus


class TestPeerAdoptionThroughK8sJob:
    async def test_peer_proposal_verifies_installs_and_then_runs_via_learned_tool_run(
        self,
        tmp_path,
    ) -> None:
        executor = _FakeE2EExecutor()
        runner = KubernetesJobLearnedToolRunner(executor=executor)
        peer, _bus = _peer_runtime(tmp_path, runner=runner)
        artifact = _peer_agent_tool_artifact(tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE)

        decision = await peer.evaluate_and_apply(artifact)

        assert decision.action == "adopted"
        assert decision.installed_skill_name
        # Re-verification happened through k8s_job, never on the host.
        assert len(executor.verify_calls) == 1
        assert executor.verify_calls[0]["tool_code"] == artifact.tool_code
        # The canary run that gates install also went through the same runner.
        assert len(executor.run_calls) == 1

        # The installed artifact now runs through learned_tool_run, using the
        # SAME k8s_job runner/executor — the full peer-adoption loop the k8s_job
        # backend previously could never complete.
        state_dir = (tmp_path / "peer" / "tools").parent
        code_dir, artifacts_dir = learned_tool_storage(state_dir)
        installed_name = decision.installed_skill_name
        installed_artifact = read_learned_tool_artifact(artifacts_dir / f"{installed_name}.json")
        tool = load_learned_tool(
            artifact=installed_artifact,
            tool_path=code_dir / f"{installed_name}.py",
            runner=runner,
        )

        class _FakeResolver:
            def load(self, name: str, *, host_call=None):
                assert name == installed_name
                return tool

        dispatch = LearnedToolRunTool(
            resolver=_FakeResolver(),  # type: ignore[arg-type]
            permission=AllowAllPermission(),
        )
        result = await dispatch.execute({"name": installed_name, "input": {"pod": "api-0"}})

        assert not result.is_error
        assert '"ok": true' in result.content
        assert len(executor.run_calls) == 2

    async def test_peer_proposal_with_requirements_is_declined_before_touching_the_k8s_api(
        self,
        tmp_path,
    ) -> None:
        """Regression for the egress-for-nothing bug: a peer tool with
        requirements must be rejected at verify() time, never given its own
        Job with real egress only to be rejected anyway at canary."""
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1()
        network = _FakeNetworkingV1()
        executor = KubernetesJobExecutor(
            namespace="ravn",
            deny_policy_name="tool-deny",
            allow_policy_name="tool-allow",
            batch_v1=batch,
            core_v1=core,
            networking_v1=network,
            in_cluster=False,
        )
        runner = KubernetesJobLearnedToolRunner(executor=executor)
        peer, _bus = _peer_runtime(tmp_path, runner=runner, name="cluster-k8s-reqs")
        artifact = _peer_agent_tool_artifact(
            tool_code=_OK_TOOL_CODE,
            test_code=_OK_TEST_CODE,
            manifest_name="net_probe",
            requirements=["requests==2.31.0"],
        )

        decision = await peer.evaluate_and_apply(artifact)

        assert decision.action == "rejected"
        # Never touched the k8s API at all — declined before verify() ever
        # reaches the executor.
        assert batch.created == []
        assert core.secrets == []

    async def test_peer_adoption_end_to_end_through_a_real_executor_and_fake_k8s_client(
        self,
        tmp_path,
    ) -> None:
        """Same loop as above, but through the REAL KubernetesJobExecutor
        (fake batch/core/networking clients only), not the coarser
        `_FakeE2EExecutor` — exercises the actual Job/Secret bodies, the
        NetworkPolicy check, and cleanup for both verify() and run()."""
        batch = _FakeVerifyBatchV1()
        core = _FakeVerifyCoreV1(log=json.dumps({"ok": True}))
        network = _FakeNetworkingV1()
        executor = KubernetesJobExecutor(
            namespace="ravn",
            deny_policy_name="tool-deny",
            allow_policy_name="tool-allow",
            batch_v1=batch,
            core_v1=core,
            networking_v1=network,
            in_cluster=False,
        )
        runner = KubernetesJobLearnedToolRunner(executor=executor)
        peer, _bus = _peer_runtime(tmp_path, runner=runner, name="cluster-k8s-real")
        artifact = _peer_agent_tool_artifact(
            tool_code=_OK_TOOL_CODE, test_code=_OK_TEST_CODE, manifest_name="real_probe"
        )

        decision = await peer.evaluate_and_apply(artifact)

        assert decision.action == "adopted"
        # verify() Job + canary run() Job, each created and cleaned up.
        assert len(batch.created) == 2
        assert len(batch.deleted) == 2
        assert len(core.deleted_secrets) == 2
        verify_job, run_job = batch.created
        assert verify_job["spec"]["template"]["spec"]["containers"][0]["command"][0] == "python"
        assert "_verify_runner.py" in "".join(
            verify_job["spec"]["template"]["spec"]["containers"][0]["command"]
        )
