"""OAuth device grant runner; the provider is an explicitly mocked HTTP boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import respx

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from volundr.adapters.outbound.oauth_device_runner import (
    DEVICE_GRANT_TYPE,
    CompositeCredentialEnrollmentRunner,
    OAuthDeviceFlowRunner,
)
from volundr.config import _default_integration_definitions
from volundr.domain.models import (
    CredentialEnrollment,
    CredentialEnrollmentPoll,
    CredentialEnrollmentState,
)
from volundr.domain.ports import CredentialEnrollmentRunnerPort
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.oauth_clients import (
    SOURCE_CONFIGURED,
    OAuthClient,
    OAuthClientRegistry,
)

DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"


def registry() -> IntegrationRegistry:
    return IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in _default_integration_definitions()])
    )


def enrollment(slug: str = "github", method: str = "oauth_device") -> CredentialEnrollment:
    now = datetime.now(UTC)
    return CredentialEnrollment(
        id=uuid4(),
        connection_id="c",
        owner_id="user",
        tenant_id="t",
        provider_slug=slug,
        credential_name=f"{slug}-signin",
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


def clients(**ids: str) -> OAuthClientRegistry:
    integrations = registry()
    return OAuthClientRegistry(
        credential_store=MemoryCredentialStore(),
        integration_registry=integrations,
        configured={
            slug: OAuthClient(slug=slug, client_id=client_id, source=SOURCE_CONFIGURED)
            for slug, client_id in ids.items()
        },
    )


@pytest.fixture
def runner() -> OAuthDeviceFlowRunner:
    return OAuthDeviceFlowRunner(
        registry=registry(), clients=clients(github="Iv1.public", gitlab="")
    )


def test_availability_needs_a_client_id_and_a_device_url(runner: OAuthDeviceFlowRunner) -> None:
    assert runner.supports_enrollment("oauth_device")
    assert not runner.supports_enrollment("codex_device")
    assert runner.available_for("github", "oauth_device")
    assert not runner.available_for("gitlab", "oauth_device")  # no client id
    assert not runner.available_for("anthropic", "oauth_device")  # no oauth spec
    assert not runner.available_for("github", "codex_device")


@pytest.mark.asyncio
async def test_start_requires_configuration(runner: OAuthDeviceFlowRunner) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        await runner.start_enrollment(enrollment(method="codex_device"))
    with pytest.raises(
        ValueError, match="No OAuth application 'default' is registered for 'gitlab'"
    ):
        await runner.start_enrollment(enrollment("gitlab"))
    with pytest.raises(ValueError, match="no OAuth specification"):
        await runner.start_enrollment(enrollment("anthropic"))


@pytest.mark.asyncio
@respx.mock
async def test_an_account_signs_in_through_its_own_application() -> None:
    clients_by_app = clients(github="Iv1.personal")
    await clients_by_app.register("github", "Iv1.org", app="niuu-org")
    runner = OAuthDeviceFlowRunner(registry=registry(), clients=clients_by_app)
    start = respx.post(DEVICE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"device_code": "d", "user_code": "U", "verification_uri": "v", "interval": 0},
        )
    )

    started = await runner.start_enrollment(
        replace(enrollment(), runner_ref={"oauth_app": "niuu-org"})
    )

    sent = dict(httpx.QueryParams(start.calls.last.request.content.decode()))
    assert sent["client_id"] == "Iv1.org"
    assert started.runner_ref == {"runner": "oauth_device", "oauth_app": "niuu-org"}
    with pytest.raises(ValueError, match="No OAuth application 'other'"):
        await runner.start_enrollment(replace(enrollment(), runner_ref={"oauth_app": "other"}))


@pytest.mark.asyncio
@respx.mock
async def test_device_flow_end_to_end(runner: OAuthDeviceFlowRunner) -> None:
    start = respx.post(DEVICE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dev-1",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 0,
            },
        )
    )
    item = enrollment()
    started = await runner.start_enrollment(item)
    assert started.state == CredentialEnrollmentState.AWAITING_USER
    assert started.verification_uri == "https://github.com/login/device"
    assert started.user_code == "ABCD-1234"
    assert start.calls[0].request.content == b"client_id=Iv1.public"

    token = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"error": "authorization_pending"}),
            httpx.Response(200, json={"error": "slow_down"}),
            httpx.Response(
                200,
                json={
                    "access_token": "ghu_secret",
                    "refresh_token": "ghr_refresh",
                    "expires_in": 28800,
                    "token_type": "bearer",
                },
            ),
        ]
    )
    pending = await runner.poll_enrollment(item)
    assert pending.state == CredentialEnrollmentState.AWAITING_USER
    assert pending.user_code == "ABCD-1234"
    body = token.calls[0].request.content.decode()
    assert "device_code=dev-1" in body and DEVICE_GRANT_TYPE.replace(":", "%3A") in body
    # slow_down pushes the next poll out; until then no request is made
    slowed = await runner.poll_enrollment(item)
    assert slowed.state == CredentialEnrollmentState.AWAITING_USER
    assert token.call_count == 2
    assert (await runner.poll_enrollment(item)).state == CredentialEnrollmentState.AWAITING_USER
    assert token.call_count == 2
    runner._sessions[item.id].next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
    done = await runner.poll_enrollment(item)
    assert done.state == CredentialEnrollmentState.COMPLETE
    assert done.credential_data["token"] == "ghu_secret"
    assert done.credential_data["refresh_token"] == "ghr_refresh"
    assert done.credential_data["expires_at"]
    # the device code is forgotten once the token is out
    assert (await runner.poll_enrollment(item)).error_code == "login_worker_missing"


@pytest.mark.asyncio
@respx.mock
async def test_denied_expired_and_provider_errors(runner: OAuthDeviceFlowRunner) -> None:
    respx.post(DEVICE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"device_code": "d", "user_code": "U", "verification_uri": "v", "interval": 0},
        )
    )
    for error, expected_state, expected_code in (
        ("access_denied", CredentialEnrollmentState.FAILED, "provider_login_rejected"),
        ("expired_token", CredentialEnrollmentState.EXPIRED, ""),
        (
            "incorrect_client_credentials",
            CredentialEnrollmentState.FAILED,
            "oauth_incorrect_client_credentials",
        ),
    ):
        item = enrollment()
        await runner.start_enrollment(item)
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"error": error}))
        runner._sessions[item.id].next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
        result = await runner.poll_enrollment(item)
        assert result.state == expected_state
        assert result.error_code == expected_code
        assert item.id not in runner._sessions

    # provider refuses the device request
    respx.post(DEVICE_URL).mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(ValueError, match="HTTP 404"):
        await runner.start_enrollment(enrollment())
    respx.post(DEVICE_URL).mock(
        return_value=httpx.Response(200, json={"error": "device_flow_disabled"})
    )
    with pytest.raises(ValueError, match="device_flow_disabled"):
        await runner.start_enrollment(enrollment())

    # the session's own expiry wins over the enrollment's
    respx.post(DEVICE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"device_code": "d", "user_code": "U", "verification_uri": "v", "expires_in": 1},
        )
    )
    item = enrollment()
    await runner.start_enrollment(item)
    runner._sessions[item.id].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await runner.poll_enrollment(item)).state == CredentialEnrollmentState.EXPIRED
    await runner.cancel_enrollment(item)  # idempotent


@pytest.mark.asyncio
@respx.mock
async def test_github_form_encoded_token_response(runner: OAuthDeviceFlowRunner) -> None:
    respx.post(DEVICE_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/x-www-form-urlencoded"},
            text="device_code=d&user_code=U&verification_uri=https%3A%2F%2Fgithub.com%2Flogin%2Fdevice&interval=0",
        )
    )
    item = enrollment()
    started = await runner.start_enrollment(item)
    assert started.verification_uri == "https://github.com/login/device"
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/x-www-form-urlencoded"},
            text="access_token=ghu_x&token_type=bearer&scope=",
        )
    )
    runner._sessions[item.id].next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
    done = await runner.poll_enrollment(item)
    assert done.credential_data == {"token": "ghu_x"}


class _Cli(CredentialEnrollmentRunnerPort):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def supports_enrollment(self, method: str) -> bool:
        return method == "codex_device"

    async def start_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        self.calls.append("start")
        return enrollment

    async def poll_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollmentPoll:
        self.calls.append("poll")
        return CredentialEnrollmentPoll(state=CredentialEnrollmentState.PENDING)

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        self.calls.append("cancel")

    async def submit_code(self, enrollment: CredentialEnrollment, code: str) -> None:
        self.calls.append(f"code:{code}")


@pytest.mark.asyncio
async def test_composite_dispatches_by_method(runner: OAuthDeviceFlowRunner) -> None:
    with pytest.raises(ValueError, match="At least one"):
        CompositeCredentialEnrollmentRunner([])
    cli = _Cli()
    composite = CompositeCredentialEnrollmentRunner([cli, runner])
    assert composite.supports_enrollment("codex_device")
    assert composite.supports_enrollment("oauth_device")
    assert not composite.supports_enrollment("nope")
    assert composite.available_for("github", "oauth_device")
    assert composite.available_for("codex", "codex_device")
    assert not composite.available_for("gitlab", "oauth_device")
    item = enrollment("codex", "codex_device")
    await composite.start_enrollment(item)
    await composite.poll_enrollment(item)
    await composite.cancel_enrollment(item)
    await composite.submit_code(item, "c")
    assert cli.calls == ["start", "poll", "cancel", "code:c"]
    with pytest.raises(ValueError, match="No enrollment runner"):
        await composite.poll_enrollment(enrollment("x", "nope"))
