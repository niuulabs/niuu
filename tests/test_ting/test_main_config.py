"""Tests for Ting app startup config decisions."""

from ting.config import AuthConfig, Settings, VolundrConfig
from ting.main import _use_local_volundr_factory


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
