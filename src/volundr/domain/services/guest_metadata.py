"""What a VM guest's metadata request is answered with.

A guest agent asks Forge for its session's credentials. The configured
``GuestIdentity`` proves which allocation is asking; the request names the slot, and
Forge answers only for the session currently bound to that slot of the allocation, from
the launch payload the session was started with. A stopping, unbound or released
allocation gets nothing, and a restart revokes what an earlier launch handed out.
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
BAD_BODY = 'The request body must be {"allocation_id": "<uuid>", "slot": <index>}'


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

        Raises ``GuestAuthenticationError`` when the request does not prove an allocation
        of this pool (a bad proof and an unknown allocation are the same error), and
        ``GuestCredentialUnavailableError`` when the named slot has no running session.
        The body's ``allocation_id`` must be the authenticated one, else it is the same
        authentication error. A body that cannot be read, or a ``slot`` that is not an
        index of the allocation, raises ``GuestRequestError``; ``slot`` may be omitted
        only for a single-slot allocation.
        """
        allocation_id = await self._identity.authenticate(request)
        requested_allocation, requested_slot = self._requested(request.body)
        if requested_allocation != allocation_id:
            raise GuestAuthenticationError
        machine = await self._repository.get(allocation_id)
        if machine is None or machine.pool_id != self._pool_id:
            raise GuestAuthenticationError
        slot = self._slot_index(requested_slot, machine)
        lease = await self._bound_slot(machine, slot)
        session_id = lease.session_id
        plan = await self._plan(lease)

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

        forge_session = None
        if plan.forge_session:
            credential = await self._issue_forge_session(session_id)
            if credential is None:
                raise GuestCredentialUnavailableError(
                    GuestUnavailableReason.NO_FORGE_SESSION,
                    "The session needs a Forge session credential but none can be issued "
                    "(the session has no owner or launch, or forge_mcp.session_tokens is off)",
                )
            forge_session = IssuedGuestForgeSession(
                token=credential.token,
                grants=tuple(sorted(grant.value for grant in credential.grants)),
            )
        return GuestCredentials(
            service_account_tokens=tuple(issued_tokens), forge_session=forge_session
        )

    @staticmethod
    def _requested(body: bytes) -> tuple[UUID, object]:
        try:
            document = json.loads(body)
            return UUID(document["allocation_id"]), document.get("slot")
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

    async def _plan(self, lease: SlotLease) -> GuestCredentialPlan:
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
        return GuestCredentialPlan.from_launch(json.loads(launch))
