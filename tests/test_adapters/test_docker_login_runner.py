"""Docker login runner tests; the Docker SDK is an explicitly faked boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from docker.errors import NotFound

from volundr.adapters.outbound import docker_login_runner as dlr
from volundr.adapters.outbound.docker_login_runner import DockerLoginRunner
from volundr.domain.models import CredentialEnrollment, CredentialEnrollmentState


def enrollment(method: str = "codex_device") -> CredentialEnrollment:
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


class _Container:
    def __init__(self, name: str, status: str = "running") -> None:
        self.name = name
        self.status = status
        self.exec_calls: list[dict[str, Any]] = []
        self.exec_results: list[tuple[int, bytes]] = []
        self.removed: list[dict[str, Any]] = []

    def reload(self) -> None:
        return None

    def exec_run(self, cmd: list[str], **kwargs: Any) -> tuple[int, bytes]:
        self.exec_calls.append({"cmd": cmd, **kwargs})
        return self.exec_results.pop(0)

    def remove(self, **kwargs: Any) -> None:
        self.removed.append(kwargs)


class _Containers:
    def __init__(self) -> None:
        self.by_name: dict[str, _Container] = {}
        self.run_kwargs: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> _Container:
        self.run_kwargs.append(kwargs)
        container = _Container(kwargs["name"])
        self.by_name[kwargs["name"]] = container
        return container

    def get(self, name: str) -> _Container:
        if name not in self.by_name:
            raise NotFound(name)
        return self.by_name[name]


class _Client:
    def __init__(self) -> None:
        self.containers = _Containers()


@pytest.fixture
def client() -> _Client:
    fake = _Client()
    with patch.object(dlr.docker, "from_env", return_value=fake):
        yield fake


@pytest.fixture
def runner(client: _Client) -> DockerLoginRunner:
    del client
    return DockerLoginRunner(image="ghcr.io/niuulabs/skuld:test", network="niuu_default")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["codex_device", "claude_setup"])
async def test_start_runs_sealed_worker_container(
    runner: DockerLoginRunner, client: _Client, method: str
) -> None:
    started = await runner.start_enrollment(enrollment(method))
    kwargs = client.containers.run_kwargs[0]
    assert kwargs["image"] == "ghcr.io/niuulabs/skuld:test"
    assert kwargs["name"] == f"niuu-login-{started.id.hex}"
    assert started.runner_ref == {"container_name": kwargs["name"]}
    assert kwargs["read_only"] is True
    assert kwargs["tmpfs"] == {"/tmp": "size=32m,mode=1777"}
    assert kwargs["environment"] == {}
    assert kwargs["network"] == "niuu_default"
    assert kwargs["labels"][dlr.LOGIN_LABEL] == str(started.id)
    assert kwargs["labels"]["niuu.managed-by"] == "docker_container"
    command = kwargs["command"]
    assert command[:2] == ["/opt/venv/bin/python", "-c"]
    assert "login_worker" in command[2] or "def main" in command[2]
    expected_exe = "/usr/local/bin/codex" if method == "codex_device" else "/usr/local/bin/claude"
    assert command[command.index("--executable") + 1] == expected_exe
    assert command[command.index("--method") + 1] == method
    assert int(command[command.index("--ttl") + 1]) > 0


@pytest.mark.asyncio
async def test_unsupported_method_and_base_url_client(client: _Client) -> None:
    with patch.object(dlr.docker, "DockerClient", return_value=client) as ctor:
        runner = DockerLoginRunner(image="img", docker_base_url="unix:///var/run/docker.sock")
    ctor.assert_called_once_with(base_url="unix:///var/run/docker.sock")
    assert runner.supports_enrollment("codex_device")
    assert not runner.supports_enrollment("password")
    with pytest.raises(ValueError, match="Unsupported login method"):
        await runner.start_enrollment(enrollment("password"))


@pytest.mark.asyncio
async def test_poll_reports_missing_pending_failed_and_worker_status(
    runner: DockerLoginRunner, client: _Client
) -> None:
    item = enrollment()
    missing = await runner.poll_enrollment(item)
    assert missing.state == CredentialEnrollmentState.FAILED
    assert missing.error_code == "login_worker_missing"

    await runner.start_enrollment(item)
    container = client.containers.by_name[runner.container_name(item)]
    container.status = "created"
    assert (await runner.poll_enrollment(item)).state == CredentialEnrollmentState.PENDING

    container.status = "exited"
    failed = await runner.poll_enrollment(item)
    assert failed.state == CredentialEnrollmentState.FAILED
    assert failed.error_code == "login_worker_failed"

    container.status = "running"
    container.exec_results.append(
        (
            0,
            json.dumps(
                {
                    "state": "awaiting_user",
                    "verification_uri": "https://auth.openai.com/codex/device",
                    "user_code": "ABCD-1234",
                }
            ).encode(),
        )
    )
    waiting = await runner.poll_enrollment(item)
    assert waiting.state == CredentialEnrollmentState.AWAITING_USER
    assert waiting.user_code == "ABCD-1234"
    assert container.exec_calls[0]["stderr"] is False
    assert container.exec_calls[0]["environment"] is None

    container.exec_results.append(
        (0, json.dumps({"state": "complete", "credential_data": {"auth.json": "s"}}).encode())
    )
    done = await runner.poll_enrollment(item)
    assert done.state == CredentialEnrollmentState.COMPLETE
    assert done.credential_data == {"auth.json": "s"}

    container.exec_results.append((1, b""))
    with pytest.raises(RuntimeError, match="login_worker_read_failed"):
        await runner.poll_enrollment(item)


@pytest.mark.asyncio
async def test_cancel_removes_container_and_is_idempotent(
    runner: DockerLoginRunner, client: _Client
) -> None:
    item = enrollment()
    await runner.cancel_enrollment(item)  # nothing to remove
    await runner.start_enrollment(item)
    container = client.containers.by_name[runner.container_name(item)]
    await runner.cancel_enrollment(item)
    assert container.removed == [{"force": True}]


@pytest.mark.asyncio
async def test_submit_code_uses_exec_environment(
    runner: DockerLoginRunner, client: _Client
) -> None:
    with pytest.raises(ValueError, match="does not accept"):
        await runner.submit_code(enrollment("codex_device"), "x")
    item = enrollment("claude_setup")
    with pytest.raises(ValueError, match="not running"):
        await runner.submit_code(item, "x")
    await runner.start_enrollment(item)
    container = client.containers.by_name[runner.container_name(item)]
    container.status = "exited"
    with pytest.raises(ValueError, match="not running"):
        await runner.submit_code(item, "x")
    container.status = "running"
    container.exec_results.append((0, b"accepted\n"))
    await runner.submit_code(item, "the-code")
    call = container.exec_calls[0]
    assert call["environment"] == {"NIUU_LOGIN_CODE": json.dumps({"code": "the-code"})}
    assert "the-code" not in " ".join(call["cmd"])
    container.exec_results.append((0, b"nope"))
    with pytest.raises(ValueError, match="could not accept"):
        await runner.submit_code(item, "the-code")
    container.exec_results.append((1, b""))
    with pytest.raises(ValueError, match="could not accept"):
        await runner.submit_code(item, "the-code")
