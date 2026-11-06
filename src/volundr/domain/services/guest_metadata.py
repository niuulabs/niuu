"""What a VM guest's metadata request is answered with.

A guest agent asks Forge for its session's credentials. The configured
``GuestIdentity`` proves which allocation is asking; Forge then answers only for
the session currently bound to that allocation, from the launch payload the
session was started with. A stopping, unbound or released allocation gets
nothing, and a restart revokes what an earlier launch handed out.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from uuid import UUID

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from volundr.domain.compute import ComputeLease, ComputeLeaseRepository, LeaseState
from volundr.domain.guest_credentials import (
    GuestAuthenticationError,
    GuestCredentialPlan,
    GuestCredentials,
    GuestCredentialUnavailableError,
    GuestIdentity,
    GuestRequest,
    IssuedGuestForgeSession,
    IssuedGuestToken,
)
from volundr.domain.service_account_tokens import ServiceAccountTokenPort
from volundr.domain.services.compute_leases import ComputeLeaseService

#: Session bootstrap file holding the launch payload (``ssh_vm_runtime`` writes it).
LAUNCH_PAYLOAD_PATH = "/etc/niuu/session-launch.json"

#: Allocation states that run a session; every other state has nothing to hand out.
_RUNNING_STATES = frozenset({LeaseState.READY, LeaseState.BUSY})

IssueForgeSession = Callable[[UUID], Awaitable[IssuedForgeSessionToken | None]]


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
        ``GuestCredentialUnavailableError`` when the allocation has no running session.
        The body's ``allocation_id`` must be the authenticated one, else it is the same
        authentication error; a body that cannot be read raises ``GuestRequestError``.
        """
        allocation_id = await self._identity.authenticate(request)
        if self._requested_allocation(request.body) != allocation_id:
            raise GuestAuthenticationError
        lease = await self._repository.get(allocation_id)
        if lease is None or lease.pool_id != self._pool_id:
            raise GuestAuthenticationError
        session_id = self._bound_session(lease)
        plan = await self._plan(lease)

        issued_tokens: list[IssuedGuestToken] = []
        if plan.token_volumes and not plan.service_account:
            raise GuestCredentialUnavailableError(
                "The session's launch names token volumes but no ServiceAccount"
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
                    "The session needs a Forge session credential but none can be issued "
                    "(the session has no owner or launch, or forge_mcp.session_tokens is off)"
                )
            forge_session = IssuedGuestForgeSession(
                token=credential.token,
                grants=tuple(sorted(grant.value for grant in credential.grants)),
            )
        return GuestCredentials(
            service_account_tokens=tuple(issued_tokens), forge_session=forge_session
        )

    @staticmethod
    def _requested_allocation(body: bytes) -> UUID:
        try:
            document = json.loads(body)
            return UUID(document["allocation_id"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise GuestRequestError('The request body must be {"allocation_id": "<uuid>"}') from exc

    @staticmethod
    def _bound_session(lease: ComputeLease) -> UUID:
        if lease.state not in _RUNNING_STATES:
            raise GuestCredentialUnavailableError(f"The allocation is {lease.state.value}")
        if lease.stop_requested:
            raise GuestCredentialUnavailableError("The allocation's session is stopping")
        if lease.session_id is None:
            raise GuestCredentialUnavailableError("The allocation has no bound session")
        return lease.session_id

    async def _plan(self, lease: ComputeLease) -> GuestCredentialPlan:
        bootstrap = await self._compute.bootstrap_for(lease)
        launch = next(
            (item.content for item in bootstrap.files if item.path == LAUNCH_PAYLOAD_PATH), None
        )
        if launch is None:
            raise GuestCredentialUnavailableError("The allocation's session has not been launched")
        return GuestCredentialPlan.from_launch(json.loads(launch))
