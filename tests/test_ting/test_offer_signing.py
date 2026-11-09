"""Ting signs the offers it hands to a run's root as the platform service it is."""

from __future__ import annotations

from typing import Any

import pytest

from niuu.mesh.attestation import PURPOSE_PLATFORM_EVENT
from niuu.mesh.platform_events import offer_subject
from ting.adapters.offer_signing import MeshOfferSigner, OfferSigningError

OFFER = {"kind": "step.offered", "offer_id": "o-1", "node_id": "code"}


class _Signer:
    def __init__(self) -> None:
        self.attested: list[tuple[str, dict[str, Any]]] = []

    def attest(self, purpose: str, subject: dict[str, Any]) -> dict[str, Any]:
        self.attested.append((purpose, subject))
        return {"peer_id": "service:ting", "signature": "sig"}


def test_an_offer_is_signed_over_the_whole_payload_as_a_platform_event() -> None:
    signer = _Signer()

    attestation = MeshOfferSigner(signer=signer, required=True).sign(OFFER)  # type: ignore[arg-type]

    assert attestation == {"peer_id": "service:ting", "signature": "sig"}
    assert signer.attested == [(PURPOSE_PLATFORM_EVENT, offer_subject(OFFER))]


def test_a_deployment_that_does_not_require_attestation_runs_its_offers_unsigned() -> None:
    assert MeshOfferSigner(signer=None, required=False).sign(OFFER) is None


def test_required_attestation_without_a_signer_is_an_error_with_the_remedy() -> None:
    with pytest.raises(OfferSigningError, match="mesh.attestation.required to false"):
        MeshOfferSigner(signer=None, required=True).sign(OFFER)
