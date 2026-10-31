"""Composition of the Forge session credential issuer and its configuration."""

from __future__ import annotations

import logging
import stat

import pytest
from pydantic import ValidationError

from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.service_runtime import create_workload_identity_service
from volundr.composition_builders import (
    KEY_SOURCE_KEY_FILE,
    KEY_SOURCE_WORKLOAD_IDENTITY,
    ForgeSessionCredentialsUnavailableError,
    _create_forge_session_tokens,
)
from volundr.config import ForgeMcpConfig, ForgeMcpHttpConfig, Settings


def _settings(tmp_path, **forge_mcp) -> Settings:
    session_tokens = {"signing_key_file": str(tmp_path / "keys" / "forge.pem")}
    session_tokens.update(forge_mcp.pop("session_tokens", {}))
    return Settings(forge_mcp={"session_tokens": session_tokens, **forge_mcp})


def _mint(service):
    return service.mint(
        session_id="00000000-0000-4000-8000-000000000001",
        session_name="s",
        owner_id="owner",
        tenant_id=None,
        grants=(),
        launch_id="l",
    )


class TestIssuerSelection:
    def test_local_key_file_in_mini_mode(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        workload = create_workload_identity_service(settings.workload_identity)
        assert workload.enabled is False  # mini mode: no workload identity
        service = _create_forge_session_tokens(settings, workload)
        assert service.key_source == KEY_SOURCE_KEY_FILE
        key = tmp_path / "keys" / "forge.pem"
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
        issued = _mint(service)
        # a restarted Forge reads the same key, so running brokers keep working
        again = _create_forge_session_tokens(settings, workload)
        assert again.verify(issued.token).owner_id == "owner"
        assert service.ttl_seconds == settings.forge_mcp.session_tokens.ttl_seconds

    def test_workload_identity_with_a_configured_key(self, tmp_path) -> None:
        pem = WorkloadIdentityService(
            type("C", (), {"enabled": True})()
        ).private_key_pem_for_tests()
        settings = Settings(
            workload_identity={"enabled": True, "issuer": "niuu", "signing_key_pem": pem},
            forge_mcp={"session_tokens": {"signing_key_file": str(tmp_path / "unused.pem")}},
        )
        workload = create_workload_identity_service(settings.workload_identity)
        service = _create_forge_session_tokens(settings, workload)
        assert service.key_source == KEY_SOURCE_WORKLOAD_IDENTITY
        assert not (tmp_path / "unused.pem").exists()
        issued = _mint(service)
        assert workload.verify_token(issued.token)["iss"] == "niuu"

    def test_workload_identity_without_a_key_uses_the_file(self, tmp_path) -> None:
        settings = _settings(tmp_path, session_tokens={})
        settings.workload_identity.enabled = True
        workload = create_workload_identity_service(settings.workload_identity)
        assert workload.has_configured_key is False
        service = _create_forge_session_tokens(settings, workload)
        assert service.key_source == KEY_SOURCE_KEY_FILE

    def test_disabled_by_config(self, tmp_path, caplog) -> None:
        caplog.set_level(logging.INFO)
        settings = _settings(tmp_path, session_tokens={"enabled": False})
        workload = create_workload_identity_service(settings.workload_identity)
        assert _create_forge_session_tokens(settings, workload) is None
        assert "disabled" in caplog.text
        assert not (tmp_path / "keys").exists()

    def test_unusable_key_refuses_to_start(self, tmp_path) -> None:
        key = tmp_path / "keys" / "forge.pem"
        key.parent.mkdir()
        key.write_text("not a key")
        key.chmod(0o600)
        settings = _settings(tmp_path)
        workload = create_workload_identity_service(settings.workload_identity)
        with pytest.raises(
            ForgeSessionCredentialsUnavailableError,
            match="no usable signing key.*forge_mcp.session_tokens.enabled: false",
        ):
            _create_forge_session_tokens(settings, workload)
        assert key.read_text() == "not a key"  # never overwritten


class TestConfig:
    def test_defaults(self) -> None:
        config = ForgeMcpConfig()
        assert config.default_grants == []
        assert config.session_tokens.enabled is True
        assert config.session_tokens.ttl_seconds == 30 * 24 * 3600
        assert config.session_tokens.signing_key_file.endswith("forge-session-signing-key.pem")
        assert config.http.enabled is True and config.http.allowed_origins == []

    def test_grants_are_validated(self) -> None:
        assert ForgeMcpConfig(default_grants=["message"]).default_grants[0].value == "message"
        with pytest.raises(ValidationError):
            ForgeMcpConfig(default_grants=["root"])

    @pytest.mark.parametrize(
        "limits",
        [
            {"list_default_limit": 60, "list_max_limit": 50},
            {"transcript_default_turns": 40, "transcript_max_turns": 30},
        ],
    )
    def test_http_limits_are_ordered(self, limits) -> None:
        with pytest.raises(ValidationError):
            ForgeMcpHttpConfig(**limits)

    def test_key_size_floor(self) -> None:
        with pytest.raises(ValidationError):
            ForgeMcpConfig(session_tokens={"signing_key_bits": 1024})
