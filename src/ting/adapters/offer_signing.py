"""Signs a workflow run's offers as the platform service Ting is.

Ting hands every offer to the run's root, whose Skuld publishes it verbatim; the members check
the signature and accept an engine run's offers only from Ting (``service:ting``). The signer is
the one Ting already signs its platform events with (``niuu.mesh.platform_identity``): the same
key, so a member that verifies one verifies the other.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from niuu.mesh.attestation import MeshSigner
from niuu.mesh.platform_events import sign_workflow_offer
from ting.ports.workflow_steps import OfferSigner


class OfferSigningError(RuntimeError):
    """Offers must be signed and Ting has no key to sign them with."""


class MeshOfferSigner(OfferSigner):
    """Signs offers with Ting's platform signer; unsigned only when attestation is not required."""

    def __init__(self, *, signer: MeshSigner | None, required: bool) -> None:
        self._signer = signer
        self._required = required

    def sign(self, offer: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if self._signer is not None:
            return sign_workflow_offer(self._signer, offer)
        if not self._required:
            return None
        raise OfferSigningError(
            "mesh.attestation.required is true, but Ting has no platform signer, so it cannot "
            "sign the offers a run's members accept only when signed. Give Ting a Sleipnir bus "
            "and a signing key (workload_identity.signing_key), or set "
            "mesh.attestation.required to false if this deployment runs its mesh unsigned"
        )
