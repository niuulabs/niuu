"""GuestMetadataService: credentials only for the unit an allocation currently runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.forge_mcp.models import ForgeMcpGrant
from volundr.domain.compute import (
    BootstrapFile,
    ComputeLease,
    ComputeSlot,
    HostPlatform,
    LeaseState,
    Machine,
    MachineBootstrap,
    MachineState,
    SlotLease,
    SlotState,
    encode_platform_bootstraps,
)
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


def make_machine(**update) -> ComputeLease:
    fields = {
        "id": uuid4(),
        "pool_id": POOL,
        "profile": "small",
        "request_fingerprint": "digest",
        "state": LeaseState.IDLE,
    }
    return ComputeLease(**{**fields, **update})


def make_binding(machine: ComputeLease, **update) -> ComputeSlot:
    fields = {
        "id": uuid4(),
        "allocation_id": machine.id,
        "slot": 0,
        "pool_id": machine.pool_id,
        "session_id": uuid4(),
        "tenant_id": "tenant",
        "owner_id": "owner",
        "state": SlotState.BUSY,
    }
    return ComputeSlot(**{**fields, **update})


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
    """A machine with one slot; ``binding`` edits its session, ``bound=False`` frees it."""

    def __init__(
        self,
        machine: ComputeLease | None = None,
        *,
        bound: bool = True,
        binding: dict | None = None,
        launch: dict | None = LAUNCH,
        **identity,
    ):
        self.machine = machine or make_machine()
        self.binding = make_binding(self.machine, **(binding or {})) if bound else None
        self.repository = AsyncMock()
        self.repository.get.return_value = self.machine
        self.repository.slot.return_value = self.binding
        self.compute = AsyncMock()
        self.compute.bootstrap_for.return_value = bootstrap(launch)
        self.tokens = RecordingTokens()
        self.issue_forge_session = AsyncMock()
        if self.binding is not None:
            self.issue_forge_session.return_value = forge_credential(self.binding.session_id)
        self.identity = FixedIdentity(identity.get("allocation_id", self.machine.id))
        self.service = GuestMetadataService(
            self.repository,
            self.compute,
            POOL,
            self.identity,
            self.tokens,
            self.issue_forge_session,
        )

    def request(self, allocation_id: UUID | None = None, **body) -> GuestRequest:
        document = {"allocation_id": str(allocation_id or self.machine.id), **body}
        return GuestRequest(
            method="POST",
            path="/api/v1/internal/compute/metadata/credentials",
            headers={},
            body=json.dumps(document).encode(),
        )


def unknown_allocation() -> Harness:
    harness = Harness()
    harness.repository.get.return_value = None
    return harness


async def test_issues_every_planned_token_and_the_forge_session_for_the_bound_session():
    harness = Harness(make_machine())

    issued = await harness.service.credentials(harness.request())

    assert [(t.volume, t.path, t.token, t.expires_at) for t in issued.service_account_tokens] == [
        ("vault", "token", "sa:openbao", EXPIRY),
        ("vault", "exchange", "sa:forge", EXPIRY),
    ]
    assert harness.tokens.calls == [("session-sa", "openbao", 600), ("session-sa", "forge", 3600)]
    assert issued.forge_session.token == "forge-secret"
    assert issued.forge_session.grants == ("lifecycle", "message")
    harness.issue_forge_session.assert_awaited_once_with(harness.binding.session_id)
    harness.repository.slot.assert_awaited_once_with(harness.machine.id, 0)
    # The launch payload comes from the slot's own bootstrap.
    view = harness.compute.bootstrap_for.await_args.args[0]
    assert isinstance(view, SlotLease)
    assert (view.allocation, view.binding) == (harness.machine, harness.binding)


async def test_no_forge_session_is_issued_when_the_plan_does_not_ask_for_one():
    harness = Harness(make_machine(), launch={**LAUNCH, "forge_session": False})

    issued = await harness.service.credentials(harness.request())

    assert issued.forge_session is None
    harness.issue_forge_session.assert_not_awaited()


@pytest.mark.parametrize("state", [LeaseState.READY, LeaseState.IDLE])
@pytest.mark.parametrize("slot_state", [SlotState.STARTING, SlotState.BUSY])
async def test_answers_for_a_running_slot_of_a_ready_or_idle_machine(state, slot_state):
    harness = Harness(make_machine(state=state), binding={"state": slot_state})
    assert (await harness.service.credentials(harness.request())).forge_session is not None


async def test_a_failed_authentication_is_the_authentication_error_and_touches_nothing():
    harness = Harness(make_machine(), allocation_id=None)

    with pytest.raises(GuestAuthenticationError):
        await harness.service.credentials(harness.request())

    harness.repository.get.assert_not_awaited()
    assert harness.tokens.calls == []


async def test_an_unknown_allocation_is_the_same_authentication_error():
    harness = unknown_allocation()

    with pytest.raises(GuestAuthenticationError):
        await harness.service.credentials(harness.request())


async def test_an_allocation_of_another_pool_is_the_same_authentication_error():
    harness = Harness(make_machine(pool_id="other"))

    with pytest.raises(GuestAuthenticationError):
        await harness.service.credentials(harness.request())

    harness.compute.bootstrap_for.assert_not_awaited()


async def test_a_body_naming_another_allocation_is_the_authentication_error():
    harness = Harness(make_machine())

    with pytest.raises(GuestAuthenticationError):
        await harness.service.credentials(harness.request(allocation_id=uuid4()))

    assert harness.tokens.calls == []


@pytest.mark.parametrize("body", [b"", b"[]", b"{}", b'{"allocation_id": 5}', b"not json"])
async def test_an_unreadable_body_is_refused_after_authentication(body):
    harness = Harness(make_machine())
    request = GuestRequest(method="POST", path="/p", headers={}, body=body)

    with pytest.raises(GuestRequestError):
        await harness.service.credentials(request)


async def test_the_slot_may_be_omitted_only_on_a_single_slot_allocation():
    single = Harness(make_machine(slots=1))
    assert (await single.service.credentials(single.request())).forge_session is not None
    single.repository.slot.assert_awaited_once_with(single.machine.id, 0)

    multi = Harness(make_machine(slots=2))
    with pytest.raises(GuestRequestError, match="2 slots"):
        await multi.service.credentials(multi.request())
    multi.repository.slot.assert_not_awaited()


async def test_the_named_slot_is_the_one_answered_for():
    harness = Harness(make_machine(slots=3), binding={"slot": 2})

    await harness.service.credentials(harness.request(slot=2))

    harness.repository.slot.assert_awaited_once_with(harness.machine.id, 2)


@pytest.mark.parametrize("slot", [True, False, "0", 1.0, -1, 2])
async def test_a_slot_that_is_not_an_index_of_the_allocation_is_a_bad_request(slot):
    harness = Harness(make_machine(slots=2))

    with pytest.raises(GuestRequestError):
        await harness.service.credentials(harness.request(slot=slot))

    harness.repository.slot.assert_not_awaited()
    assert harness.tokens.calls == []


async def test_an_explicit_null_slot_is_the_same_as_an_omitted_one():
    single = Harness(make_machine(slots=1))
    assert (await single.service.credentials(single.request(slot=None))).forge_session is not None

    multi = Harness(make_machine(slots=2))
    with pytest.raises(GuestRequestError, match="2 slots"):
        await multi.service.credentials(multi.request(slot=None))


@pytest.mark.parametrize(
    "state",
    [
        LeaseState.PROVISIONING,
        LeaseState.QUARANTINED,
        LeaseState.DRAINING,
        LeaseState.RELEASED,
        LeaseState.FAILED,
    ],
)
async def test_a_machine_that_runs_no_session_gets_nothing(state):
    harness = Harness(make_machine(state=state))

    with pytest.raises(GuestCredentialUnavailableError, match=state.value):
        await harness.service.credentials(harness.request())

    assert harness.tokens.calls == []


@pytest.mark.parametrize(
    "slot_state", [SlotState.FAILED, SlotState.DRAINING, SlotState.RETIRED, SlotState.RELEASED]
)
async def test_a_slot_that_runs_no_session_gets_nothing(slot_state):
    harness = Harness(make_machine(), binding={"state": slot_state})

    with pytest.raises(GuestCredentialUnavailableError, match=slot_state.value):
        await harness.service.credentials(harness.request())

    assert harness.tokens.calls == []


async def test_an_unbound_slot_gets_nothing():
    harness = Harness(make_machine(), bound=False)

    with pytest.raises(GuestCredentialUnavailableError, match="no bound session"):
        await harness.service.credentials(harness.request())


async def test_an_idle_machine_with_no_session_is_unbound_not_not_running():
    # A warm spare runs no guest agent; if it asks anyway it has no bound session.
    harness = Harness(make_machine(state=LeaseState.IDLE), bound=False)

    with pytest.raises(GuestCredentialUnavailableError) as raised:
        await harness.service.credentials(harness.request())

    assert raised.value.reason.value == "no_bound_session"


async def test_a_stopping_session_gets_nothing():
    harness = Harness(make_machine(), binding={"stop_requested": True})

    with pytest.raises(GuestCredentialUnavailableError, match="stopping"):
        await harness.service.credentials(harness.request())

    assert harness.tokens.calls == []


async def test_a_session_that_was_never_launched_gets_nothing():
    harness = Harness(make_machine(), launch=None)

    with pytest.raises(GuestCredentialUnavailableError, match="not been launched"):
        await harness.service.credentials(harness.request())


async def test_a_forge_session_that_cannot_be_issued_is_unavailable_not_omitted():
    harness = Harness(make_machine())
    harness.issue_forge_session.return_value = None

    with pytest.raises(GuestCredentialUnavailableError, match="Forge session credential"):
        await harness.service.credentials(harness.request())


async def test_token_volumes_without_a_service_account_are_unavailable():
    harness = Harness(make_machine(), launch={**LAUNCH, "service_account": ""})

    with pytest.raises(GuestCredentialUnavailableError, match="ServiceAccount"):
        await harness.service.credentials(harness.request())

    assert harness.tokens.calls == []


async def test_a_refused_token_request_is_not_swallowed():
    harness = Harness(make_machine())
    harness.tokens.issue = AsyncMock(side_effect=ServiceAccountTokenError("refused"))

    with pytest.raises(ServiceAccountTokenError):
        await harness.service.credentials(harness.request())

    harness.issue_forge_session.assert_not_awaited()


async def test_a_plan_with_nothing_to_deliver_is_an_empty_answer():
    harness = Harness(make_machine(), launch={"service_account": "", "forge_session": False})

    issued = await harness.service.credentials(harness.request())

    assert issued.service_account_tokens == ()
    assert issued.forge_session is None


def platform_machine(platform: HostPlatform | None) -> ComputeLease:
    allocation_id = uuid4()
    return make_machine(
        id=allocation_id,
        machine=Machine(
            allocation_id=allocation_id,
            resource_id="host",
            state=MachineState.RUNNING,
            platform=platform,
        ),
    )


def per_platform_bootstrap() -> MachineBootstrap:
    """A host execution's bootstrap: each platform keeps its own launch payload."""
    return encode_platform_bootstraps(
        {
            HostPlatform.LINUX: bootstrap({**LAUNCH, "service_account": "linux-sa"}),
            HostPlatform.DARWIN: bootstrap({**LAUNCH, "service_account": "darwin-sa"}),
        },
        {},
    )


@pytest.mark.parametrize(
    ("platform", "service_account"),
    [(HostPlatform.LINUX, "linux-sa"), (HostPlatform.DARWIN, "darwin-sa")],
)
async def test_the_plan_is_read_from_the_launch_of_the_hosts_platform(platform, service_account):
    harness = Harness(platform_machine(platform))
    harness.compute.bootstrap_for.return_value = per_platform_bootstrap()

    await harness.service.credentials(harness.request())

    assert harness.tokens.calls[0][0] == service_account


async def test_a_per_platform_bootstrap_needs_the_hosts_recorded_platform():
    harness = Harness(platform_machine(None))
    harness.compute.bootstrap_for.return_value = per_platform_bootstrap()

    with pytest.raises(ValueError, match="records none"):
        await harness.service.credentials(harness.request())

    assert harness.tokens.calls == []


async def test_a_platform_whose_runtime_refused_the_session_was_never_launched():
    harness = Harness(platform_machine(HostPlatform.DARWIN))
    harness.compute.bootstrap_for.return_value = encode_platform_bootstraps(
        {HostPlatform.LINUX: bootstrap(LAUNCH)}, {HostPlatform.DARWIN: "no image on a Mac"}
    )

    with pytest.raises(GuestCredentialUnavailableError, match="not been launched"):
        await harness.service.credentials(harness.request())


def test_a_pool_id_is_required():
    with pytest.raises(ValueError, match="pool_id"):
        GuestMetadataService(
            AsyncMock(), AsyncMock(), "", FixedIdentity(None), AsyncMock(), AsyncMock()
        )


# -- the members of a run's unit ---------------------------------------------------------------

MEMBER_A, MEMBER_B = uuid4(), uuid4()


def unit_launch(*members: UUID, forge_session: bool = True) -> dict:
    """A launch payload of a run's unit, as Forge writes it when it starts the unit."""
    return {
        **LAUNCH,
        "members": [
            {
                "id": str(member),
                "port": 8082 + index,
                "forge_session": forge_session,
                "forge_session_directory": f"/etc/niuu/forge-sessions/{member}",
                "forge_session_file": "token",
            }
            for index, member in enumerate(members)
        ],
    }


async def test_a_member_of_the_bound_unit_gets_its_own_forge_session_alone():
    harness = Harness(make_machine(), launch=unit_launch(MEMBER_A, MEMBER_B))
    harness.issue_forge_session.return_value = forge_credential(MEMBER_B)

    issued = await harness.service.credentials(harness.request(session_id=str(MEMBER_B)))

    harness.issue_forge_session.assert_awaited_once_with(MEMBER_B)
    assert issued.forge_session.token == "forge-secret"
    # The unit's ServiceAccount tokens stay the bound session's.
    assert issued.service_account_tokens == ()
    assert harness.tokens.calls == []


async def test_the_bound_session_asking_by_its_own_id_is_answered_as_itself():
    harness = Harness(make_machine(), launch=unit_launch(MEMBER_A))
    session_id = harness.binding.session_id

    issued = await harness.service.credentials(harness.request(session_id=str(session_id)))

    assert len(issued.service_account_tokens) == 2
    harness.issue_forge_session.assert_awaited_once_with(session_id)


async def test_a_session_the_units_launch_does_not_list_gets_nothing():
    harness = Harness(make_machine(), launch=unit_launch(MEMBER_A))

    with pytest.raises(GuestCredentialUnavailableError) as refused:
        await harness.service.credentials(harness.request(session_id=str(uuid4())))

    assert refused.value.reason.value == "no_bound_session"
    harness.issue_forge_session.assert_not_awaited()
    assert harness.tokens.calls == []


async def test_a_unit_with_no_members_answers_no_member():
    harness = Harness(make_machine(), launch=LAUNCH)

    with pytest.raises(GuestCredentialUnavailableError, match="not a member of the unit"):
        await harness.service.credentials(harness.request(session_id=str(MEMBER_A)))


async def test_a_member_is_only_answered_while_the_slot_runs_its_unit():
    harness = Harness(
        make_machine(), launch=unit_launch(MEMBER_A), binding={"stop_requested": True}
    )

    with pytest.raises(GuestCredentialUnavailableError, match="stopping"):
        await harness.service.credentials(harness.request(session_id=str(MEMBER_A)))
    unbound = Harness(make_machine(), bound=False, launch=unit_launch(MEMBER_A))
    with pytest.raises(GuestCredentialUnavailableError, match="no bound session"):
        await unbound.service.credentials(unbound.request(session_id=str(MEMBER_A)))


async def test_a_member_that_asks_for_no_forge_session_gets_an_empty_answer():
    harness = Harness(make_machine(), launch=unit_launch(MEMBER_A, forge_session=False))

    issued = await harness.service.credentials(harness.request(session_id=str(MEMBER_A)))

    assert issued.forge_session is None
    harness.issue_forge_session.assert_not_awaited()


async def test_a_member_whose_credential_cannot_be_issued_is_unavailable():
    harness = Harness(make_machine(), launch=unit_launch(MEMBER_A))
    harness.issue_forge_session.return_value = None

    with pytest.raises(GuestCredentialUnavailableError, match="Forge session credential"):
        await harness.service.credentials(harness.request(session_id=str(MEMBER_A)))


@pytest.mark.parametrize("session_id", ["not-a-uuid", 5, ["x"]])
async def test_a_session_id_that_is_not_a_uuid_is_a_bad_request(session_id):
    harness = Harness(make_machine(), launch=unit_launch(MEMBER_A))

    with pytest.raises(GuestRequestError):
        await harness.service.credentials(harness.request(session_id=session_id))
