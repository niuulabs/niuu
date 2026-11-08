"""Composition and configuration of Forge's mesh identity."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from niuu.domain.services.forge_session_token import ForgeSessionTokenService
from niuu.mesh.attestation import KIND_AUTHORITY, KIND_SESSION, MeshAttestationVerifier
from niuu.service_runtime import create_workload_identity_service
from sleipnir.adapters.in_process import InProcessBus
from tests.support.mesh_identity import InMemoryMeshPeerClaims
from volundr.composition_builders import (
    MeshIdentityUnavailableError,
    _create_forge_session_tokens,
    _create_mesh_certificate_issuer,
    _create_mesh_revocation_publisher,
    _forge_authority_peer_id,
)
from volundr.config import ForgeMcpConfig, ForgeMcpMeshIdentityConfig, Settings
from volundr.domain.mesh_peer_claims import PeerIdClaimedError


def _settings(tmp_path, **forge_mcp) -> Settings:
    session_tokens = {"signing_key_file": str(tmp_path / "forge.pem")}
    session_tokens.update(forge_mcp.pop("session_tokens", {}))
    return Settings(forge_mcp={"session_tokens": session_tokens, **forge_mcp})


def _tokens(settings: Settings) -> ForgeSessionTokenService | None:
    workload = create_workload_identity_service(settings.workload_identity)
    return _create_forge_session_tokens(settings, workload)


class TestConfig:
    def test_defaults(self) -> None:
        config = ForgeMcpConfig().mesh_identity
        assert config.enabled is True and config.ttl_seconds == 900

    @pytest.mark.parametrize("ttl", [0, 1, -5])
    def test_a_ttl_the_issuer_cannot_use_is_refused(self, ttl: int) -> None:
        with pytest.raises(ValidationError):
            ForgeMcpMeshIdentityConfig(ttl_seconds=ttl)

    def test_it_is_set_through_forge_mcp(self, tmp_path) -> None:
        settings = _settings(tmp_path, mesh_identity={"ttl_seconds": 120, "enabled": False})
        assert settings.forge_mcp.mesh_identity.ttl_seconds == 120
        assert settings.forge_mcp.mesh_identity.enabled is False


class TestIssuerComposition:
    def test_signs_with_the_session_credential_key(self, tmp_path) -> None:
        settings = _settings(tmp_path, mesh_identity={"ttl_seconds": 120})
        tokens = _tokens(settings)

        issuer = _create_mesh_certificate_issuer(settings, tokens)

        assert issuer is not None and issuer.ttl_seconds == 120
        assert tokens.signing_issuer is not None
        assert issuer.issuer == tokens.signing_issuer.issuer
        certificate = issuer.issue(
            peer_id="p",
            kind=KIND_SESSION,
            public_key=b"\x01" * 32,
            memberships=["tenant:t"],
            session_id=str(uuid4()),
            launch_id="l",
        )
        # The same key that signs the session credentials signs (and verifies) the cert.
        verifier = MeshAttestationVerifier(
            lambda: certificate.jwks, issuer=certificate.issuer, own_memberships=lambda: ()
        )
        assert verifier.read_certificate(certificate.token).peer_id == "p"
        assert tokens.signing_issuer.jwks() == certificate.jwks

    def test_a_restarted_forge_keeps_certificates_verifiable(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        first = _create_mesh_certificate_issuer(settings, _tokens(settings))
        second = _create_mesh_certificate_issuer(settings, _tokens(settings))
        assert first.jwks() == second.jwks()

    def test_disabled_by_config_means_no_mesh_identity(self, tmp_path) -> None:
        settings = _settings(tmp_path, mesh_identity={"enabled": False})
        assert _create_mesh_certificate_issuer(settings, _tokens(settings)) is None

    def test_no_session_credentials_means_no_mesh_identity(self, tmp_path) -> None:
        settings = _settings(tmp_path, session_tokens={"enabled": False})
        assert _tokens(settings) is None
        assert _create_mesh_certificate_issuer(settings, None) is None

    def test_a_signer_that_cannot_sign_certificates_is_fatal(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        tokens = ForgeSessionTokenService(
            SimpleNamespace(issue_token=None, verify_token=None),
            audiences=["volundr-api"],
            ttl_seconds=600,
            key_source="key_file",
        )
        with pytest.raises(
            MeshIdentityUnavailableError,
            match="cannot sign mesh certificates.*forge_mcp.mesh_identity.enabled: false",
        ):
            _create_mesh_certificate_issuer(settings, tokens)


class TestAuthorityComposition:
    def test_peer_id_is_the_instance_id(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        settings.projects.instance_id = "thor"
        assert _forge_authority_peer_id(settings) == "forge:thor"

    def test_peer_id_falls_back_to_the_host_name(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        settings.projects.instance_id = ""
        assert _forge_authority_peer_id(settings) == f"forge:{socket.gethostname()}"

    async def test_the_publisher_starts_certified_as_the_authority(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        settings.projects.instance_id = "thor"
        issuer = _create_mesh_certificate_issuer(settings, _tokens(settings))
        claims = InMemoryMeshPeerClaims()

        publisher = await _create_mesh_revocation_publisher(
            settings, issuer, claims, InProcessBus()
        )

        assert publisher.peer_id == "forge:thor"
        assert publisher._signer.certificate.kind == KIND_AUTHORITY
        assert claims.rows["forge:thor"]["holder"] == "authority:forge:thor"
        await publisher.stop()

    async def test_a_second_forge_with_the_same_name_does_not_start(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        settings.projects.instance_id = "thor"
        issuer = _create_mesh_certificate_issuer(settings, _tokens(settings))
        claims = InMemoryMeshPeerClaims()
        first = await _create_mesh_revocation_publisher(settings, issuer, claims, InProcessBus())
        # A different process: same name, but it holds the peer id under another launch.
        claims.rows["forge:thor"]["holder"] = "authority:somebody-else"

        with pytest.raises(PeerIdClaimedError, match="forge:thor"):
            await _create_mesh_revocation_publisher(settings, issuer, claims, InProcessBus())
        await first.stop()
