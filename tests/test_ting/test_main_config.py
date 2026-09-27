"""Tests for Ting app startup config decisions."""

from types import SimpleNamespace

import pytest

from bifrost.config import BifrostConfig, ManagedModelConfig
from niuu.service_runtime import _get_auth_mode, _validate_identity_adapter_class
from ting.config import AuthConfig, DispatchConfig, Settings, VolundrConfig
from ting.main import (
    _dispatch_service_config,
    _use_local_volundr_factory,
    _workflow_execution_token_issuer,
)


class TestTingAuthModeGuard:
    """Ting composes its identity adapter directly (not via
    niuu.service_runtime.create_identity_adapter), so ting.main wires the
    same guard in by hand — these tests cover that wiring stays correct."""

    def test_auth_mode_defaults_to_envoy(self) -> None:
        assert Settings().auth_mode == "envoy"

    def test_default_adapter_under_none_mode_is_rejected(self) -> None:
        """Ting's own default (EnvoyHeaderAuthenticationAdapter +
        allow_anonymous_dev) trusts caller headers unconditionally — exactly
        the bug this fix round closes. It must be rejected once a host
        explicitly declares it has no Envoy."""
        settings = Settings(auth_mode="none")
        cls = _resolve(settings.auth.adapter)
        with pytest.raises(ValueError, match="trusts x-auth-\\* headers"):
            _validate_identity_adapter_class(cls, _get_auth_mode(settings))

    def test_allow_all_adapter_under_none_mode_passes(self) -> None:
        settings = Settings(
            auth_mode="none",
            auth=AuthConfig(
                adapter="identity.adapters.identity.AllowAllHeaderAuthenticationAdapter"
            ),
        )
        cls = _resolve(settings.auth.adapter)
        _validate_identity_adapter_class(cls, _get_auth_mode(settings))

    def test_jwks_bearer_adapter_under_oidc_mode_passes(self) -> None:
        settings = Settings(
            auth_mode="oidc",
            auth=AuthConfig(adapter="identity.adapters.jwks.JwksBearerAuthenticationAdapter"),
        )
        cls = _resolve(settings.auth.adapter)
        _validate_identity_adapter_class(cls, _get_auth_mode(settings))

    def test_default_adapter_under_oidc_mode_is_rejected(self) -> None:
        settings = Settings(auth_mode="oidc")
        cls = _resolve(settings.auth.adapter)
        with pytest.raises(ValueError, match="trusts x-auth-\\* headers"):
            _validate_identity_adapter_class(cls, _get_auth_mode(settings))


def _resolve(dotted_path: str) -> type:
    from niuu.utils import import_class

    return import_class(dotted_path)


def test_uses_local_volundr_factory_for_classic_anonymous_dev() -> None:
    settings = Settings(
        auth=AuthConfig(allow_anonymous_dev=True),
        volundr=VolundrConfig(use_connection_factory_in_dev=False),
    )

    assert _use_local_volundr_factory(settings) is True


def test_allows_connection_factory_in_anonymous_dev_when_enabled() -> None:
    settings = Settings(
        auth=AuthConfig(allow_anonymous_dev=True),
        volundr=VolundrConfig(use_connection_factory_in_dev=True),
    )

    assert _use_local_volundr_factory(settings) is False


def test_non_anonymous_mode_uses_connection_factory() -> None:
    settings = Settings(
        auth=AuthConfig(allow_anonymous_dev=False),
        volundr=VolundrConfig(use_connection_factory_in_dev=False),
    )

    assert _use_local_volundr_factory(settings) is False


def test_anonymous_workflow_execution_omits_disabled_token_issuer() -> None:
    settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
    disabled = SimpleNamespace(enabled=False)

    assert _workflow_execution_token_issuer(settings, disabled) is None


def test_authenticated_workflow_execution_requires_enabled_token_issuer() -> None:
    settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))

    with pytest.raises(RuntimeError, match="requires workload identity"):
        _workflow_execution_token_issuer(settings, SimpleNamespace(enabled=False))


def test_workflow_execution_uses_enabled_token_issuer_in_any_auth_mode() -> None:
    settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
    enabled = SimpleNamespace(enabled=True)

    assert _workflow_execution_token_issuer(settings, enabled) is enabled


@pytest.mark.parametrize("timeout_seconds", [900.0, 0.0])
def test_dispatch_service_gets_the_configured_workflow_cli_turn_timeout(
    timeout_seconds: float,
) -> None:
    settings = Settings(dispatch=DispatchConfig(workflow_cli_turn_timeout_seconds=timeout_seconds))

    assert _dispatch_service_config(settings).workflow_cli_turn_timeout_seconds == timeout_seconds


def test_dispatch_service_keeps_the_120_second_default() -> None:
    assert _dispatch_service_config(Settings()).workflow_cli_turn_timeout_seconds == 120.0


def test_dispatch_service_config_carries_the_dispatch_settings() -> None:
    model = ManagedModelConfig(id="gpt-5.5", name="GPT-5.5", vendor="openai")
    settings = Settings(
        dispatch=DispatchConfig(
            default_system_prompt="Be terse.",
            default_model="gpt-5.5",
            default_session_definition="skuldCodex",
            dispatch_prompt_template="Do {identifier}",
        ),
        bifrost=BifrostConfig(models=[model]),
    )

    config = _dispatch_service_config(settings)

    assert config.default_system_prompt == "Be terse."
    assert config.default_model == "gpt-5.5"
    assert config.default_session_definition == "skuldCodex"
    assert config.dispatch_prompt_template == "Do {identifier}"
    assert config.session_definitions is settings.session_definitions
    assert config.configured_models == [model]
    assert config.live_flock is settings.dispatch.flock
