"""Tests for Ting app startup config decisions."""

from types import SimpleNamespace

import pytest

from ting.config import AuthConfig, Settings, VolundrConfig
from ting.main import _developer_execution_token_issuer, _use_local_volundr_factory


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


def test_anonymous_developer_execution_omits_disabled_token_issuer() -> None:
    settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
    disabled = SimpleNamespace(enabled=False)

    assert _developer_execution_token_issuer(settings, disabled) is None


def test_authenticated_developer_execution_requires_enabled_token_issuer() -> None:
    settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))

    with pytest.raises(RuntimeError, match="requires workload identity"):
        _developer_execution_token_issuer(settings, SimpleNamespace(enabled=False))


def test_developer_execution_uses_enabled_token_issuer_in_any_auth_mode() -> None:
    settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
    enabled = SimpleNamespace(enabled=True)

    assert _developer_execution_token_issuer(settings, enabled) is enabled
