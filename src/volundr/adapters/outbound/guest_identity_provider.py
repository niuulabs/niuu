"""Guest identity vouched for by the machine provider (the hypervisor-side answer).

The provider puts a token into every guest at boot and keeps it fresh. On Harvester
that is the VM's virt-launcher pod ServiceAccount token, shared into the guest over
virtiofs; Forge checks it with a TokenReview and confirms the pod runs that
allocation's VM. Nothing is added to the VM image and Forge generates no key: a guest
is trusted because the provider that runs it says so.

The guest agent sends the token as ``Authorization: Bearer <token>``. Every rejection
is the same ``GuestAuthenticationError``.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from uuid import UUID

from volundr.domain.guest_credentials import (
    GuestAttestingProvider,
    GuestAuthenticationError,
    GuestIdentity,
    GuestRequest,
)


class ProviderAttestedGuestIdentity(GuestIdentity):
    """Authenticates a guest by a token its machine provider issued and verifies."""

    def __init__(self, *, machine_provider: object) -> None:
        if not isinstance(machine_provider, GuestAttestingProvider):
            raise ValueError(
                f"ProviderAttestedGuestIdentity needs a machine provider that attests guests; "
                f"{type(machine_provider).__name__} does not. Use HostKeyGuestIdentity or "
                "SpiffeGuestIdentity for this provider"
            )
        if not machine_provider.attests_guests:
            raise ValueError(
                "ProviderAttestedGuestIdentity needs the provider to put a token into guests; "
                "set guest_service_account in compute.provider.kwargs"
            )
        self._provider = machine_provider

    def agent_auth(self, allocation_id: UUID) -> dict:
        return {"kind": "bearer_file", "token_path": self._provider.guest_token_path}

    def agent_mounts(self) -> tuple[dict, ...]:
        directory = str(PurePosixPath(self._provider.guest_token_path).parent)
        return ({"source": directory, "target": directory, "readonly": True},)

    async def authenticate(self, request: GuestRequest) -> UUID:
        headers = {name.lower(): value for name, value in request.headers.items()}
        scheme, _, token = headers.get("authorization", "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise GuestAuthenticationError
        return await self._provider.attested_allocation(token)
