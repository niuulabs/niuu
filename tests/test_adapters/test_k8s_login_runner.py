"""Worker lifecycle tests; Kubernetes is an explicitly mocked boundary."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from volundr.adapters.outbound.k8s_login_runner import KubernetesLoginRunner
from volundr.domain.models import CredentialEnrollment, CredentialEnrollmentState

ApiException = pytest.importorskip("kubernetes_asyncio.client.exceptions").ApiException


def enrollment(method="codex_device"):
    now = datetime.now(UTC)
    return CredentialEnrollment(
        id=uuid4(),
        connection_id=str(uuid4()),
        owner_id="user",
        tenant_id="tenant",
        provider_slug="codex",
        credential_name="codex-credentials",
        method=method,
        state=CredentialEnrollmentState.PENDING,
        runner_ref={},
        verification_uri="",
        user_code="",
        expires_at=now + timedelta(minutes=15),
        error_code="",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def runner():
    value = KubernetesLoginRunner(image="test-only/skuld:pinned")
    value._configure = AsyncMock()
    return value


@pytest.mark.parametrize("method", ["codex_device", "claude_setup"])
async def test_job_uses_only_temporary_storage_and_records_identity_immediately(runner, method):
    attempt = enrollment(method)
    batch = SimpleNamespace(create_namespaced_job=AsyncMock())
    with patch("kubernetes_asyncio.client.BatchV1Api", return_value=batch):
        started = await runner.start_enrollment(attempt)
    job = batch.create_namespaced_job.call_args.args[1]
    spec = job["spec"]["template"]["spec"]
    assert started.state == CredentialEnrollmentState.PENDING
    assert started.runner_ref["job_name"] == job["metadata"]["name"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["volumes"] == [
        {"name": "temporary", "emptyDir": {"medium": "Memory", "sizeLimit": "32Mi"}}
    ]
    assert spec["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True
    assert "env" not in spec["containers"][0]
    assert job["spec"]["backoffLimit"] == 0
    assert 0 < job["spec"]["activeDeadlineSeconds"] <= 900
    assert job["spec"]["ttlSecondsAfterFinished"] == 60
    compile(spec["containers"][0]["command"][2], "login-worker", "exec")


@pytest.mark.parametrize(
    "phase,expected", [("Pending", "pending"), ("Failed", "failed"), ("Running", "awaiting_user")]
)
async def test_poll_can_resume_from_a_different_api_replica(runner, phase, expected):
    attempt = enrollment()
    batch = SimpleNamespace(
        read_namespaced_job=AsyncMock(
            return_value=SimpleNamespace(status=SimpleNamespace(failed=0))
        )
    )
    core = SimpleNamespace(
        list_namespaced_pod=AsyncMock(
            return_value=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(name="login-pod"),
                        status=SimpleNamespace(phase=phase),
                    )
                ]
            )
        )
    )
    runner._read = AsyncMock(
        return_value={
            "state": "awaiting_user",
            "verification_uri": "https://auth.openai.com/codex/device",
            "user_code": "TEST-CODE",
        }
    )
    with (
        patch("kubernetes_asyncio.client.BatchV1Api", return_value=batch),
        patch("kubernetes_asyncio.client.CoreV1Api", return_value=core),
    ):
        result = await runner.poll_enrollment(attempt)
    assert result.state.value == expected
    if phase == "Running":
        assert result.user_code == "TEST-CODE"
    else:
        runner._read.assert_not_called()


async def test_deleted_worker_is_a_visible_failure_and_cancel_is_idempotent(runner):
    batch = SimpleNamespace(
        read_namespaced_job=AsyncMock(side_effect=ApiException(status=404)),
        delete_namespaced_job=AsyncMock(side_effect=ApiException(status=404)),
    )
    with patch("kubernetes_asyncio.client.BatchV1Api", return_value=batch):
        result = await runner.poll_enrollment(enrollment())
        await runner.cancel_enrollment(enrollment())
    assert result.error_code == "login_worker_missing"


async def test_completed_poll_transfers_secret_only_to_service(runner):
    batch = SimpleNamespace(
        read_namespaced_job=AsyncMock(
            return_value=SimpleNamespace(status=SimpleNamespace(failed=0))
        )
    )
    core = SimpleNamespace(
        list_namespaced_pod=AsyncMock(
            return_value=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(name="login-pod"),
                        status=SimpleNamespace(phase="Running"),
                    )
                ]
            )
        )
    )
    runner._read = AsyncMock(
        return_value={"state": "complete", "credential_data": {"auth.json": "test-secret"}}
    )
    with (
        patch("kubernetes_asyncio.client.BatchV1Api", return_value=batch),
        patch("kubernetes_asyncio.client.CoreV1Api", return_value=core),
    ):
        result = await runner.poll_enrollment(enrollment())
    assert result.credential_data == {"auth.json": "test-secret"}


async def test_exec_decodes_worker_status_without_stderr(runner):
    core = SimpleNamespace(
        connect_get_namespaced_pod_exec=AsyncMock(return_value='{"state":"pending"}')
    )
    with patch("kubernetes_asyncio.client.CoreV1Api", return_value=core):
        assert await runner._read("pod", ["read-status"]) == {"state": "pending"}
    assert core.connect_get_namespaced_pod_exec.call_args.kwargs["stderr"] is False


async def test_browser_code_uses_stdin_not_audited_command_arguments(runner):
    websocket = AsyncMock()
    websocket.__aenter__.return_value = websocket
    websocket.__aiter__.return_value = [SimpleNamespace(data=b"\x01accepted\n")]
    core = SimpleNamespace(
        list_namespaced_pod=AsyncMock(
            return_value=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(name="pod"),
                        status=SimpleNamespace(phase="Running"),
                    )
                ]
            )
        ),
        connect_get_namespaced_pod_exec=AsyncMock(return_value=websocket),
    )
    with patch("kubernetes_asyncio.client.CoreV1Api", return_value=core):
        await runner.submit_code(enrollment("claude_setup"), "test-private-code")
    kwargs = core.connect_get_namespaced_pod_exec.call_args.kwargs
    assert kwargs["stdin"] is True
    assert "test-private-code" not in repr(kwargs["command"])
    assert b"test-private-code" in websocket.send_bytes.call_args.args[0]


async def test_configuration_and_unsupported_method():
    runner = KubernetesLoginRunner(image="test-only/skuld:pinned")
    with patch("kubernetes_asyncio.config.load_incluster_config") as load:
        await runner._configure()
        load.assert_called_once()
    runner._in_cluster = False
    with patch("kubernetes_asyncio.config.load_kube_config", new_callable=AsyncMock) as load:
        await runner._configure()
        load.assert_awaited_once()
    with pytest.raises(ValueError, match="Unsupported"):
        await runner.start_enrollment(enrollment("unknown"))
    with pytest.raises(ValueError, match="does not accept"):
        await runner.submit_code(enrollment(), "test-code")
