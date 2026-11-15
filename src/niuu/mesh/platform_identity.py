"""A platform service's own identity for signing the events it publishes.

Forge signs platform events with its ``authority`` certificate. Every other service that
publishes them (Ting, Mímir, ...) gives itself a ``service`` certificate from the key it
already has for workload identity, with no scopes and no memberships, and no Forge call:

* where the deployment configures workload identity (``workload_identity`` with its signing
  key, the same key Envoy validates and Forge signs with) the service signs with that;
* otherwise, on one host, with the host's key file that Forge signs session credentials with
  (created once, 0600, ``~/.niuu/forge-session-signing-key.pem``), which every service of the
  host's user can read, so ``niuu platform up`` needs no setup.

Receivers trust the certificate because it verifies against the JWKS of that key, and accept
it as a platform service because its ``kind`` is ``service`` or ``authority``. A mesh peer's
certificate (a session, a resident) has another kind and is refused.

With ``mesh.attestation.required`` false the service signs nothing and says so once.
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace
from typing import Any

from niuu.adapters.workload_identity.key_file import SigningKeyFileError, load_or_create_rsa_key_pem
from niuu.domain.services.mesh_certificate import MeshCertificateIssuer
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.mesh.attestation import (
    KIND_SERVICE,
    IssuedMeshCertificate,
    MeshAttestationVerifier,
    MeshSigner,
)
from niuu.mesh.config import MeshAttestationConfig
from niuu.mesh.platform_events import PlatformEventVerifier

logger = logging.getLogger(__name__)

#: The host key file Forge signs session credentials with, and its issuer and key id.
HOST_KEY_FILE = "~/.niuu/forge-session-signing-key.pem"
HOST_KEY_ISSUER = "niuu-forge-session"
HOST_KEY_ID = "niuu-forge-session"
_HOST_KEY_BITS = 2048


class PlatformIdentityUnavailableError(RuntimeError):
    """A platform service cannot sign its events; the message names the fix."""


class _SelfIssuedCertificates:
    """Certificates a service issues itself, with the key it already holds."""

    def __init__(self, issuer: MeshCertificateIssuer) -> None:
        self._issuer = issuer

    async def issue(self, public_key: bytes, peer_id: str) -> IssuedMeshCertificate:
        return self._issuer.issue(
            peer_id=peer_id, kind=KIND_SERVICE, public_key=public_key, memberships=()
        )


def _signing_service(workload_identity: Any, key_file: str) -> WorkloadIdentityService:
    """The issuer of this deployment's workload identity, else the host's key file."""
    if workload_identity is not None and getattr(workload_identity, "enabled", False):
        pem = str(getattr(workload_identity, "signing_key_pem", "") or "")
        env_name = str(getattr(workload_identity, "signing_key_env", "") or "")
        if not pem and env_name:
            pem = os.environ.get(env_name, "")
        if pem:
            return WorkloadIdentityService(workload_identity, signing_key_pem=pem)
    try:
        pem = load_or_create_rsa_key_pem(key_file, key_size=_HOST_KEY_BITS)
    except SigningKeyFileError as exc:
        raise PlatformIdentityUnavailableError(
            f"cannot sign platform events: no workload-identity signing key is configured and the "
            f"host key file is unusable ({exc}). Configure workload_identity with its signing key "
            "(the key Forge signs with), or set mesh.attestation.required: false"
        ) from exc
    return WorkloadIdentityService(
        SimpleNamespace(
            enabled=True, issuer=HOST_KEY_ISSUER, audiences=["volundr-api"], key_id=HOST_KEY_ID
        ),
        signing_key_pem=pem,
    )


async def start_platform_signer(
    name: str,
    mesh: MeshAttestationConfig,
    *,
    workload_identity: Any | None = None,
    key_file: str = HOST_KEY_FILE,
) -> MeshSigner | None:
    """The signer for *name*'s platform events, or ``None`` when signing is off.

    Raises :class:`PlatformIdentityUnavailableError` when signing is required and the
    service has no key to sign with.
    """
    if not mesh.required:
        logger.warning(
            "mesh.attestation.required is false: %s publishes platform events with no proof "
            "of who sent them, and accepts platform events without checking",
            name,
        )
        return None
    issuer = MeshCertificateIssuer(
        _signing_service(workload_identity, key_file), ttl_seconds=mesh.service_certificate_ttl_s
    )
    signer = MeshSigner(
        _SelfIssuedCertificates(issuer),
        f"service:{name}",
        renew_fraction=mesh.renew_fraction,
        retry_fraction=mesh.retry_fraction,
    )
    await signer.start()
    logger.info("Platform events of %s are signed as service:%s", name, name)
    return signer


def platform_event_verifier(
    verifier: MeshAttestationVerifier, mesh: MeshAttestationConfig
) -> PlatformEventVerifier | None:
    """What a subscriber that acts on platform events checks them with; ``None`` when off."""
    if not mesh.required:
        return None
    return PlatformEventVerifier(verifier, event_max_age_s=mesh.event_max_age_s)


def verifier_for(signer: MeshSigner, mesh: MeshAttestationConfig) -> MeshAttestationVerifier:
    """A verifier that trusts the keys *signer* was issued with (the deployment's JWKS)."""
    return MeshAttestationVerifier(
        lambda: signer.jwks,
        issuer=signer.issuer,
        own_memberships=signer.memberships,
        clock_skew_s=mesh.clock_skew_s,
        event_max_age_s=mesh.event_max_age_s,
    )
