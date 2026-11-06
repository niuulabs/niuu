"""GuestMetadataService: credentials only for the session an allocation currently runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.forge_mcp.models import ForgeMcpGrant
from volundr.domain.compute import BootstrapFile, ComputeLease, LeaseState, MachineBootstrap
from volundr.domain.guest_credentials import (
    GuestAuthenticationError,
    GuestCredentialUnavailableError,
    GuestIdentity,
    GuestRequest,
)
from volundr.domain.service_account_tokens import (
    ServiceAccountToken,
    ServiceAccountTokenError,
    ServiceAccountTokenPort,
)
from volundr.domain.services.guest_metadata import (
    LAUNCH_PAYLOAD_PATH,
    GuestMetadataService,
    GuestRequestError,
)

POOL = "pool"
EXPIRY = datetime(2030, 1, 1, tzinfo=UTC)


class FixedIdentity(GuestIdentity):
    def __init__(self, allocation_id: UUID | None) -> None:
        self.allocation_id = allocation_id

    def agent_auth(self, allocation_id: UUID) -> dict:
        return {}

    async def authenticate(self, request: GuestRequest) -> UUID:
        if self.allocation_id is None:
            raise GuestAuthenticationError
        return self.allocation_id


class RecordingTokens(ServiceAccountTokenPort):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def issue(self, service_account, *, audience, expiration_seconds):
        self.calls.append((service_account, audience, expiration_seconds))
        return ServiceAccountToken(token=f"sa:{audience}", expires_at=EXPIRY)


def make_lease(**update) -> ComputeLease:
    fields = {
        "id": uuid4(),
        "pool_id": POOL,
        "session_id": uuid4(),
        "tenant_id": "tenant",
        "owner_id": "owner",
        "profile": "small",
        "request_fingerprint": "digest",
        "state": LeaseState.BUSY,
    }
    return ComputeLease(**{**fields, **update})


LAUNCH = {
    "service_account": "session-sa",
    "token_volumes": [
        {
            "volume": "vault",
            "directory": "/run/niuu/session/tokens/vault",
            "tokens": [
                {"path": "token", "audience": "openbao", "expiration_seconds": 600},
                {"path": "exchange", "audience": "forge", "expiration_seconds": 3600},
            ],
        }
    ],
    "forge_session": True,
}


def bootstrap(launch: dict | None) -> MachineBootstrap:
    if launch is None:
        return MachineBootstrap()
    return MachineBootstrap(
        files=(BootstrapFile(path=LAUNCH_PAYLOAD_PATH, content=json.dumps(launch)),)
    )


def forge_credential(session_id: UUID) -> IssuedForgeSessionToken:
    return IssuedForgeSessionToken(
        token="forge-secret",
        expires_at=1,
        session_id=str(session_id),
        launch_id="launch",
        scopes=(),
        grants=(ForgeMcpGrant.MESSAGE, ForgeMcpGrant.LIFECYCLE),
    )


class Harness:
    def __init__(self, lease: ComputeLease | None, launch: dict | None = LAUNCH, **identity):
        self.lease = lease
        self.repository = AsyncMock()
        self.repository.get.return_value = lease
        self.compute = AsyncMock()
        self.compute.bootstrap_for.return_value = bootstrap(launch)
        self.tokens = RecordingTokens()
        self.issue_forge_session = AsyncMock()
        if lease is not None:
            self.issue_forge_session.return_value = forge_credential(lease.session_id)
        allocation = lease.id if lease is not None else uuid4()
        self.identity = FixedIdentity(identity.get("allocation_id", allocation))
        self.service = GuestMetadataService(
            self.repository,
            self.compute,
            POOL,
            self.identity,
            self.tokens,
            self.issue_forge_session,
        )

    def request(self, allocation_id: UUID | None = None) -> GuestRequest:
        allocation = allocation_id or (self.lease.id if self.lease else uuid4())
        return GuestRequest(
            method="POST",
            path="/api/v1/internal/compute/metadata/credentials",
            headers={},
            body=json.dumps({"allocation_id": str(allocation)}).encode(),
        )


async def test_issues_every_planned_token_and_the_forge_session_for_the_bound_session():
    harness = Harness(make_lease())

    issued = await harness.service.credentials(harness.request())

    assert [(t.volume, t.path, t.token, t.expires_at) for t in issued.service_account_tokens] == [
        ("vault", "token", "sa:openbao", EXPIRY),
        ("vault", "exchange", "sa:forge", EXPIRY),
    ]
    assert harness.tokens.calls == [("session-sa", "openbao", 600), ("session-sa", "forge", 3600)]
    assert issued.forge_session.token == "forge-secret"
    assert issued.forge_session.grants == ("lifecycle", "message")
    harness.issue_forge_session.assert_awaited_once_with(harness.lease.session_id)


async def test_no_forge_session_is_issued_when_the_plan_does_not_ask_for_one():
    harness = Harness(make_lease(), launch={**LAUNCH, "forge_session": False})

    issued = await harness.service.credentials(harness.request())

    assert issued.forge_session is None
    harness.issue_forge_session.assert_not_awaited()


@pytest.mark.parametrize("state", [LeaseState.READY, LeaseState.BUSY])
async def test_answers_for_ready_and_busy_allocations(state):
    harness = Harness(make_lease(state=state))
    assert (await harness.service.credentials(harness.request())).forge_session is not None


async def test_a_failed_authentication_is_the_authentication_error_and_touches_nothing():
    harness = Harness(make_lease(), allocation_id=None)

    with pytest.raises(GuestAuthenticationError):
        await harness.service.credentials(harness.request())

    harness.repository.get.assert_not_awaited()
    assert harness.tokens.calls == []


async def test_an_unknown_allocation_is_the_same_authentication_error():
    harness = Harness(None)

    with pytest.raises(GuestAuthenticationError):
        await harness.service.credentials(harness.request())


async def test_an_allocation_of_another_pool_is_the_same_authentication_error():
    harness = Harness(make_lease(pool_id="other"))

    with pytest.raises(GuestAuthenticationError):
        await harness.service.credentials(harness.request())

    harness.compute.bootstrap_for.assert_not_awaited()


async def test_a_body_naming_another_allocation_is_the_authentication_error():
    harness = Harness(make_lease())

    with pytest.raises(GuestAuthenticationError):
        await harness.service.credentials(harness.request(allocation_id=uuid4()))

    assert harness.tokens.calls == []


@pytest.mark.parametrize("body", [b"", b"[]", b"{}", b'{"allocation_id": 5}', b"not json"])
async def test_an_unreadable_body_is_refused_after_authentication(body):
    harness = Harness(make_lease())
    request = GuestRequest(method="POST", path="/p", headers={}, body=body)

    with pytest.raises(GuestRequestError):
        await harness.service.credentials(request)


@pytest.mark.parametrize(
    "state",
    [
        LeaseState.PROVISIONING,
        LeaseState.IDLE,
        LeaseState.QUARANTINED,
        LeaseState.DRAINING,
        LeaseState.RELEASED,
        LeaseState.FAILED,
    ],
)
async def test_an_allocation_that_runs_no_session_gets_nothing(state):
    harness = Harness(make_lease(state=state))

    with pytest.raises(GuestCredentialUnavailableError, match=state.value):
        await harness.service.credentials(harness.request())

    assert harness.tokens.calls == []


async def test_an_unbound_allocation_gets_nothing():
    harness = Harness(make_lease(session_id=None))

    with pytest.raises(GuestCredentialUnavailableError, match="no bound session"):
        await harness.service.credentials(harness.request())


async def test_a_stopping_session_gets_nothing():
    harness = Harness(make_lease(stop_requested=True))

    with pytest.raises(GuestCredentialUnavailableError, match="stopping"):
        await harness.service.credentials(harness.request())

    assert harness.tokens.calls == []


async def test_a_session_that_was_never_launched_gets_nothing():
    harness = Harness(make_lease(), launch=None)

    with pytest.raises(GuestCredentialUnavailableError, match="not been launched"):
        await harness.service.credentials(harness.request())


async def test_a_forge_session_that_cannot_be_issued_is_unavailable_not_omitted():
    harness = Harness(make_lease())
    harness.issue_forge_session.return_value = None

    with pytest.raises(GuestCredentialUnavailableError, match="Forge session credential"):
        await harness.service.credentials(harness.request())


async def test_token_volumes_without_a_service_account_are_unavailable():
    harness = Harness(make_lease(), launch={**LAUNCH, "service_account": ""})

    with pytest.raises(GuestCredentialUnavailableError, match="ServiceAccount"):
        await harness.service.credentials(harness.request())

    assert harness.tokens.calls == []


async def test_a_refused_token_request_is_not_swallowed():
    harness = Harness(make_lease())
    harness.tokens.issue = AsyncMock(side_effect=ServiceAccountTokenError("refused"))

    with pytest.raises(ServiceAccountTokenError):
        await harness.service.credentials(harness.request())

    harness.issue_forge_session.assert_not_awaited()


async def test_a_plan_with_nothing_to_deliver_is_an_empty_answer():
    harness = Harness(make_lease(), launch={"service_account": "", "forge_session": False})

    issued = await harness.service.credentials(harness.request())

    assert issued.service_account_tokens == ()
    assert issued.forge_session is None


def test_a_pool_id_is_required():
    with pytest.raises(ValueError, match="pool_id"):
        GuestMetadataService(
            AsyncMock(), AsyncMock(), "", FixedIdentity(None), AsyncMock(), AsyncMock()
        )
