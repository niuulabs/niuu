"""Tests for the startup auth-mode guard in niuu.service_runtime.

``auth.mode: oidc`` is a claim that every inbound identity path on the host
is signature-verified; ``auth.mode: none`` is the mirror claim ("no
authentication at all"). These tests cover both directions: the adapter
class actually configured must match what the declared mode promises, not
just avoid the specific "Envoy-trust" mistake.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.adapters.identity import (
    AllowAllHeaderAuthenticationAdapter,
    AllowAllIdentityAdapter,
    EnvoyHeaderAuthenticationAdapter,
    EnvoyHeaderIdentityAdapter,
)
from identity.adapters.jwks import JwksBearerAuthenticationAdapter, JwksIdentityAdapter
from niuu.service_runtime import (
    _get_auth_mode,
    _validate_authorization_adapter_class,
    _validate_identity_adapter_class,
    create_authorization_adapter,
    create_identity_adapter,
)

_OIDC_KWARGS = {
    "issuers": [
        {
            "issuer": "https://kc.example/realms/volundr",
            "audiences": ["volundr-api"],
            "jwks_uri": "https://kc.example/realms/volundr/certs",
        }
    ]
}


def _identity_settings(
    *, adapter: str, auth_mode: str, kwargs: dict | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        identity=SimpleNamespace(
            adapter=adapter, kwargs=kwargs or {}, secret_kwargs_env={}, role_mapping={}
        ),
        pat=SimpleNamespace(
            token_issuer_adapter="niuu.adapters.keycloak_token_issuer.KeycloakTokenIssuer"
        ),
        auth_mode=auth_mode,
    )


def _authorization_settings(*, adapter: str, auth_mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        authorization=SimpleNamespace(adapter=adapter, kwargs={}, secret_kwargs_env={}),
        auth_mode=auth_mode,
    )


class TestGetAuthMode:
    def test_missing_attribute_defaults_to_envoy_for_unmigrated_packages(self) -> None:
        """Packages this round did not touch (guild, credentials, ...) have no
        auth_mode field yet; they must keep working unchanged rather than
        crash every co-hosted service. Völundr and Ting (the packages this
        round DOES touch) always declare a real auth_mode field, so this
        fallback is never actually exercised for them — see _get_auth_mode's
        docstring for the full reasoning."""
        assert _get_auth_mode(SimpleNamespace()) == "envoy"

    def test_unknown_value_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown auth_mode"):
            _get_auth_mode(SimpleNamespace(auth_mode="ldap"))

    @pytest.mark.parametrize("mode", ["envoy", "none", "oidc"])
    def test_known_values_pass_through(self, mode: str) -> None:
        assert _get_auth_mode(SimpleNamespace(auth_mode=mode)) == mode


class TestValidateIdentityAdapterClass:
    def test_envoy_mode_allows_anything_unchanged(self) -> None:
        _validate_identity_adapter_class(EnvoyHeaderIdentityAdapter, "envoy")
        _validate_identity_adapter_class(AllowAllIdentityAdapter, "envoy")
        _validate_identity_adapter_class(JwksIdentityAdapter, "envoy")

    def test_envoy_adapter_without_envoy_present_raises(self) -> None:
        with pytest.raises(ValueError, match="trusts x-auth-\\* headers"):
            _validate_identity_adapter_class(EnvoyHeaderIdentityAdapter, "none")
        with pytest.raises(ValueError, match="trusts x-auth-\\* headers"):
            _validate_identity_adapter_class(EnvoyHeaderAuthenticationAdapter, "oidc")

    def test_oidc_with_jwks_identity_adapter_passes(self) -> None:
        """JwksIdentityAdapter subclasses EnvoyHeaderIdentityAdapter to reuse its
        JIT-provisioning pipeline; it must NOT be flagged as Envoy-trusting."""
        _validate_identity_adapter_class(JwksIdentityAdapter, "oidc")

    def test_oidc_with_jwks_bearer_adapter_passes(self) -> None:
        _validate_identity_adapter_class(JwksBearerAuthenticationAdapter, "oidc")

    def test_oidc_with_allow_all_raises(self) -> None:
        with pytest.raises(ValueError, match="requires an identity adapter that verifies"):
            _validate_identity_adapter_class(AllowAllIdentityAdapter, "oidc")

    def test_none_with_jwks_raises(self) -> None:
        with pytest.raises(ValueError, match="requires the explicit allow-all"):
            _validate_identity_adapter_class(JwksIdentityAdapter, "none")

    def test_none_with_allow_all_passes(self) -> None:
        _validate_identity_adapter_class(AllowAllIdentityAdapter, "none")
        _validate_identity_adapter_class(AllowAllHeaderAuthenticationAdapter, "none")


class TestValidateAuthorizationAdapterClass:
    def test_envoy_mode_allows_anything_unchanged(self) -> None:
        _validate_authorization_adapter_class(AllowAllAuthorizationAdapter, "envoy")
        _validate_authorization_adapter_class(CedarAuthorizationAdapter, "envoy")

    def test_oidc_requires_cedar(self) -> None:
        with pytest.raises(ValueError, match="requires Cedar authorization"):
            _validate_authorization_adapter_class(AllowAllAuthorizationAdapter, "oidc")
        _validate_authorization_adapter_class(CedarAuthorizationAdapter, "oidc")

    def test_none_requires_allow_all(self) -> None:
        with pytest.raises(ValueError, match="requires the explicit allow-all authorizer"):
            _validate_authorization_adapter_class(CedarAuthorizationAdapter, "none")
        _validate_authorization_adapter_class(AllowAllAuthorizationAdapter, "none")


class TestCreateIdentityAdapterEndToEnd:
    def test_misconfigured_envoy_adapter_fails_before_constructing(self) -> None:
        settings = _identity_settings(
            adapter="identity.adapters.identity.EnvoyHeaderIdentityAdapter",
            auth_mode="none",
        )
        with pytest.raises(ValueError, match="trusts x-auth-\\* headers"):
            create_identity_adapter(settings, user_repository=AsyncMock())

    def test_none_mode_with_allow_all_constructs_and_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        settings = _identity_settings(
            adapter="identity.adapters.identity.AllowAllIdentityAdapter",
            auth_mode="none",
        )
        with caplog.at_level("WARNING"):
            adapter = create_identity_adapter(settings, user_repository=AsyncMock())
        assert isinstance(adapter, AllowAllIdentityAdapter)
        assert any(
            "authentication disabled (auth.mode: none): every caller is treated as admin"
            in r.message
            for r in caplog.records
        )

    def test_oidc_mode_with_jwks_identity_adapter_constructs(self) -> None:
        settings = _identity_settings(
            adapter="identity.adapters.jwks.JwksIdentityAdapter",
            auth_mode="oidc",
            kwargs=_OIDC_KWARGS,
        )
        adapter = create_identity_adapter(settings, user_repository=AsyncMock())
        assert isinstance(adapter, JwksIdentityAdapter)

    def test_oidc_mode_with_allow_all_raises(self) -> None:
        settings = _identity_settings(
            adapter="identity.adapters.identity.AllowAllIdentityAdapter",
            auth_mode="oidc",
        )
        with pytest.raises(ValueError, match="requires an identity adapter that verifies"):
            create_identity_adapter(settings, user_repository=AsyncMock())

    def test_oidc_with_memory_token_issuer_raises(self) -> None:
        settings = _identity_settings(
            adapter="identity.adapters.jwks.JwksIdentityAdapter",
            auth_mode="oidc",
            kwargs=_OIDC_KWARGS,
        )
        settings.pat.token_issuer_adapter = "niuu.adapters.memory_token_issuer.MemoryTokenIssuer"
        with pytest.raises(ValueError, match="cannot verify PATs"):
            create_identity_adapter(settings, user_repository=AsyncMock())


class TestCreateAuthorizationAdapterEndToEnd:
    def test_oidc_mode_with_allow_all_raises(self) -> None:
        settings = _authorization_settings(
            adapter="identity.adapters.authorization.AllowAllAuthorizationAdapter",
            auth_mode="oidc",
        )
        with pytest.raises(ValueError, match="requires Cedar authorization"):
            create_authorization_adapter(settings)

    def test_oidc_mode_with_cedar_constructs(self) -> None:
        settings = _authorization_settings(
            adapter="identity.adapters.cedar.CedarAuthorizationAdapter",
            auth_mode="oidc",
        )
        adapter = create_authorization_adapter(settings)
        assert isinstance(adapter, CedarAuthorizationAdapter)

    def test_none_mode_with_cedar_raises(self) -> None:
        settings = _authorization_settings(
            adapter="identity.adapters.cedar.CedarAuthorizationAdapter",
            auth_mode="none",
        )
        with pytest.raises(ValueError, match="requires the explicit allow-all authorizer"):
            create_authorization_adapter(settings)
