"""What a VM guest's metadata request is answered with.

A guest agent asks Forge for its session's credentials. The configured
``GuestIdentity`` proves which allocation is asking; the request names the slot, and
Forge answers only for sessions of the unit currently bound to that slot of the
allocation, from the launch payload the unit was started with. That is the bound session
and, when it is a workflow run's unit, the members of the run its payload lists: they run
in the same slot as the slot's one Unix user and so share it (docs/design/
workflow-mesh-handoff.md, decision 2), and a request names the member it fetches for.
A stopping, unbound or released allocation gets nothing, and a restart revokes what an
earlier launch handed out.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from uuid import UUID

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from volundr.domain.compute import (
    ComputeLease,
    ComputeLeaseRepository,
    LeaseState,
    SlotLease,
    SlotState,
    platform_bootstrap,
)
from volundr.domain.guest_credentials import (
    GuestAuthenticationError,
    GuestCredentialPlan,
    GuestCredentials,
    GuestCredentialUnavailableError,
    GuestIdentity,
    GuestRequest,
    GuestUnavailableReason,
    IssuedGuestForgeSession,
    IssuedGuestToken,
)
from volundr.domain.service_account_tokens import ServiceAccountTokenPort
from volundr.domain.services.compute_leases import ComputeLeaseService

#: Session bootstrap file holding the launch payload (``ssh_vm_runtime`` writes it).
LAUNCH_PAYLOAD_PATH = "/etc/niuu/session-launch.json"

#: Machine states that can run a session (one in service is idle); every other state has
#: nothing to hand out.
_RUNNING_STATES = frozenset({LeaseState.READY, LeaseState.IDLE})

#: Slot states that run a session.
_RUNNING_SLOT_STATES = frozenset({SlotState.STARTING, SlotState.BUSY})

IssueForgeSession = Callable[[UUID], Awaitable[IssuedForgeSessionToken | None]]


#: The body a guest must send; ``slot`` may be omitted only for a single-slot allocation.
BAD_BODY = (
    'The request body must be {"allocation_id": "<uuid>", "slot": <index>}, with an optional '
    '"session_id": "<uuid>" naming a member of the unit bound to the slot'
)


class GuestRequestError(ValueError):
    """An authenticated metadata request has a body the endpoint cannot read."""


class GuestMetadataService:
    def __init__(
        self,
        repository: ComputeLeaseRepository,
        compute_service: ComputeLeaseService,
        pool_id: str,
        identity: GuestIdentity,
        service_account_tokens: ServiceAccountTokenPort,
        issue_forge_session: IssueForgeSession,
    ) -> None:
        if not pool_id:
            raise ValueError("GuestMetadataService requires the compute pool_id it answers for")
        self._repository = repository
        self._compute = compute_service
        self._pool_id = pool_id
        self._identity = identity
        self._tokens = service_account_tokens
        self._issue_forge_session = issue_forge_session

    async def credentials(self, request: GuestRequest) -> GuestCredentials:
        """Authenticate the guest and issue the credentials its bound session needs.

        The body may name a member of the unit with ``session_id``; the answer is then that
        member's Forge session credential alone, and only when the unit's launch payload
        lists it (``GuestCredentialUnavailableError`` otherwise).

        Raises ``GuestAuthenticationError`` when the request does not prove an allocation
        of this pool (a bad proof and an unknown allocation are the same error), and
        ``GuestCredentialUnavailableError`` when the named slot has no running session.
        The body's ``allocation_id`` must be the authenticated one, else it is the same
        authentication error. A body that cannot be read, or a ``slot`` that is not an
        index of the allocation, raises ``GuestRequestError``; ``slot`` may be omitted
        only for a single-slot allocation.
        """
        allocation_id = await self._identity.authenticate(request)
        requested_allocation, requested_slot, requested_session = self._requested(request.body)
        if requested_allocation != allocation_id:
            raise GuestAuthenticationError
        machine = await self._repository.get(allocation_id)
        if machine is None or machine.pool_id != self._pool_id:
            raise GuestAuthenticationError
        slot = self._slot_index(requested_slot, machine)
        lease = await self._bound_slot(machine, slot)
        launch = await self._launch(lease)
        if requested_session is not None and requested_session != lease.session_id:
            return await self._member_credentials(lease, launch, requested_session)
        session_id = lease.session_id
        plan = GuestCredentialPlan.from_launch(launch)

        issued_tokens: list[IssuedGuestToken] = []
        if plan.token_volumes and not plan.service_account:
            raise GuestCredentialUnavailableError(
                GuestUnavailableReason.NO_SERVICE_ACCOUNT,
                "The session's launch names token volumes but no ServiceAccount",
            )
        for volume in plan.token_volumes:
            for token in volume.tokens:
                issued = await self._tokens.issue(
                    plan.service_account,
                    audience=token.audience,
                    expiration_seconds=token.expiration_seconds,
                )
                issued_tokens.append(
                    IssuedGuestToken(
                        volume=volume.volume,
                        path=token.path,
                        token=issued.token,
                        expires_at=issued.expires_at,
                    )
                )

        forge_session = await self._forge_session(plan, session_id)
        return GuestCredentials(
            service_account_tokens=tuple(issued_tokens), forge_session=forge_session
        )

    async def _forge_session(
        self, plan: GuestCredentialPlan, session_id: UUID
    ) -> IssuedGuestForgeSession | None:
        if not plan.forge_session:
            return None
        credential = await self._issue_forge_session(session_id)
        if credential is None:
            raise GuestCredentialUnavailableError(
                GuestUnavailableReason.NO_FORGE_SESSION,
                "The session needs a Forge session credential but none can be issued "
                "(the session has no owner or launch, or forge_mcp.session_tokens is off)",
            )
        return IssuedGuestForgeSession(
            token=credential.token,
            grants=tuple(sorted(grant.value for grant in credential.grants)),
        )

    async def _member_credentials(
        self, lease: SlotLease, launch: dict, session_id: UUID
    ) -> GuestCredentials:
        """The Forge session credential of one member of the unit bound to the slot.

        Only a session the unit's own launch payload lists answers. The payload is what Forge
        wrote when it started the unit, so a session that asks for another run's member, or
        for a session that is no member at all, gets the same refusal as an unbound slot. A
        member holds no ServiceAccount tokens of its own: those belong to the unit.
        """
        member = next(
            (m for m in launch.get("members") or [] if m.get("id") == str(session_id)), None
        )
        if member is None:
            raise GuestCredentialUnavailableError(
                GuestUnavailableReason.UNBOUND,
                f"Session {session_id} is not a member of the unit bound to slot {lease.slot}",
            )
        plan = GuestCredentialPlan.from_launch(member)
        forge_session = await self._forge_session(plan, session_id)
        return GuestCredentials(forge_session=forge_session)

    @staticmethod
    def _requested(body: bytes) -> tuple[UUID, object, UUID | None]:
        try:
            document = json.loads(body)
            session = document.get("session_id")
            return (
                UUID(document["allocation_id"]),
                document.get("slot"),
                UUID(session) if session is not None else None,
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise GuestRequestError(BAD_BODY) from exc

    @staticmethod
    def _slot_index(requested: object, machine: ComputeLease) -> int:
        if requested is None:
            if machine.slots == 1:
                return 0
            raise GuestRequestError(f"The allocation has {machine.slots} slots; name one")
        # bool is an int subclass: reject it explicitly.
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise GuestRequestError("slot must be an integer")
        if not 0 <= requested < machine.slots:
            raise GuestRequestError(f"slot must be below {machine.slots}")
        return requested

    async def _bound_slot(self, machine: ComputeLease, slot: int) -> SlotLease:
        if machine.state not in _RUNNING_STATES:
            raise GuestCredentialUnavailableError(
                GuestUnavailableReason.NOT_RUNNING, f"The allocation is {machine.state.value}"
            )
        binding = await self._repository.slot(machine.id, slot)
        if binding is None:
            raise GuestCredentialUnavailableError(
                GuestUnavailableReason.UNBOUND, f"The allocation's slot {slot} has no bound session"
            )
        if binding.state not in _RUNNING_SLOT_STATES:
            raise GuestCredentialUnavailableError(
                GuestUnavailableReason.NOT_RUNNING, f"The slot's session is {binding.state.value}"
            )
        if binding.stop_requested:
            raise GuestCredentialUnavailableError(
                GuestUnavailableReason.STOPPING, "The slot's session is stopping"
            )
        return SlotLease(allocation=machine, binding=binding)

    async def _launch(self, lease: SlotLease) -> dict:
        bootstrap = await self._compute.bootstrap_for(lease)
        # A host execution for several platforms keeps one launch per platform.
        machine = lease.machine
        bootstrap = platform_bootstrap(bootstrap, machine.platform if machine else None)
        launch = next(
            (item.content for item in bootstrap.files if item.path == LAUNCH_PAYLOAD_PATH), None
        )
        if launch is None:
            raise GuestCredentialUnavailableError(
                GuestUnavailableReason.NOT_LAUNCHED,
                "The allocation's session has not been launched",
            )
        return json.loads(launch)
