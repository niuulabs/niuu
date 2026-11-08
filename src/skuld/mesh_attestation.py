"""This session's proof of who it is on the mesh.

Skuld holds a Forge session credential (``forge_mcp.token``) in every pod manager that
delivers one. With it, Skuld asks its own Forge for a mesh certificate for a key it
generates in process memory (``POST /sessions/{id}/mesh-identity``), signs what it sends
with that key, and verifies what it receives against the keys in the same answer. See
:mod:`niuu.mesh.attestation` for the scheme and ``docs/operator/agent-messaging.md`` for
the operator's view.

A session that cannot get a certificate does not start its mesh: configured-but-impossible
is fatal, and the message names the fix. ``mesh.attestation.required: false`` is the
operator's explicit choice to run without proof.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from niuu.adapters.mesh.attested import AttestedMesh, RefusalListener
from niuu.mesh.attestation import (
    IssuedMeshCertificate,
    MeshAttestationVerifier,
    MeshSigner,
    b64url,
)
from niuu.mesh.config import MeshAttestationConfig

logger = logging.getLogger(__name__)

FORGE_SESSIONS_PATH = "/api/v1/forge/sessions"


class MeshAttestationUnavailableError(RuntimeError):
    """This session cannot prove its identity on the mesh; the message says how to fix it."""


class ForgeCertificateSource:
    """Gets this session's mesh certificate from the Forge that launched it."""

    def __init__(
        self,
        *,
        http_client: Callable[[], Awaitable[httpx.AsyncClient]],
        token: str,
        session_id: str,
        mesh_realm: str,
        timeout_s: float,
    ) -> None:
        self._http_client = http_client
        self._token = token
        self._session_id = session_id
        self._mesh_realm = mesh_realm
        self._timeout_s = timeout_s

    async def issue(self, public_key: bytes, peer_id: str) -> IssuedMeshCertificate:
        client = await self._http_client()
        try:
            response = await client.post(
                f"{FORGE_SESSIONS_PATH}/{self._session_id}/mesh-identity",
                json={
                    "public_key": b64url(public_key),
                    "peer_id": peer_id,
                    "mesh_realm": self._mesh_realm,
                },
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout_s,
            )
        except httpx.HTTPError as exc:
            raise MeshAttestationUnavailableError(
                f"could not reach Forge for this session's mesh certificate: {exc!r}. Check "
                "volundr_api_url and that Forge is up; the mesh does not start without it "
                "(or set mesh.attestation.required: false to run it unauthenticated)"
            ) from exc
        if response.status_code != httpx.codes.OK:
            raise MeshAttestationUnavailableError(
                f"Forge refused this session's mesh certificate ({response.status_code}): "
                f"{_detail(response)}"
            )
        body = response.json()
        try:
            return IssuedMeshCertificate(
                token=str(body["certificate"]),
                jwks=dict(body["jwks"]),
                issuer=str(body["issuer"]),
                ttl_s=int(body["ttl_seconds"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MeshAttestationUnavailableError(
                f"Forge's mesh certificate answer is malformed ({exc!r}); Forge and Skuld "
                "are different versions?"
            ) from exc


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and body.get("detail") is not None:
        return str(body["detail"])
    return response.text


async def attest_mesh(
    mesh: Any,
    *,
    config: MeshAttestationConfig,
    peer_id: str,
    session_id: str,
    mesh_realm: str,
    forge_token: str,
    http_client: Callable[[], Awaitable[httpx.AsyncClient]],
    request_timeout_s: float,
    on_refusal: RefusalListener,
) -> AttestedMesh:
    """Wrap *mesh* so it signs and verifies. Raises when this session cannot sign."""
    if not forge_token:
        raise MeshAttestationUnavailableError(
            "the mesh needs this session's Forge credential to get its mesh certificate, "
            "and none is configured (forge_mcp.token / forge_mcp.token_file). Enable "
            "forge_mcp.session_tokens on the Forge that launches this session (Kubernetes "
            "and Flux sessions also need forge_mcp.session_tokens.projection_adapter), or "
            "set mesh.attestation.required: false to run the mesh unauthenticated"
        )
    signer = MeshSigner(
        ForgeCertificateSource(
            http_client=http_client,
            token=forge_token,
            session_id=session_id,
            mesh_realm=mesh_realm,
            timeout_s=request_timeout_s,
        ),
        peer_id,
        renew_fraction=config.renew_fraction,
        retry_fraction=config.retry_fraction,
    )
    await signer.start()
    verifier = MeshAttestationVerifier(
        lambda: signer.jwks,
        issuer=signer.issuer,
        own_memberships=signer.memberships,
        clock_skew_s=config.clock_skew_s,
        event_max_age_s=config.event_max_age_s,
        # A revoked launch can still be believed for as long as its certificate could be
        # valid, plus how far back an event may be signed.
        revocation_retain_s=lambda: signer.ttl_s + config.event_max_age_s + config.clock_skew_s,
    )
    logger.info(
        "mesh attestation: peer %s certified (memberships=%s)",
        peer_id,
        list(signer.memberships()),
    )
    return AttestedMesh(
        mesh,
        signer,
        verifier,
        own_peer_id=peer_id,
        event_max_age_s=config.event_max_age_s,
        on_refusal=on_refusal,
    )
