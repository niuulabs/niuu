"""Tests for mimir.backend.build_backend — the dynamic-adapter store spec."""

from __future__ import annotations

import pytest

from mimir.backend import build_backend
from mimir.exceptions import MimirConfigurationError
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter


@pytest.mark.parametrize("spec", [None, {}])
def test_a_missing_spec_names_the_setting(spec) -> None:
    with pytest.raises(MimirConfigurationError, match="MIMIR__BACKEND is not set"):
        build_backend(spec, setting="MIMIR__BACKEND")


def test_a_spec_without_an_adapter_is_refused() -> None:
    with pytest.raises(MimirConfigurationError, match="no 'adapter'"):
        build_backend({"mcp_url": "http://x"}, setting="--backend")


def test_an_unimportable_adapter_is_refused() -> None:
    with pytest.raises(MimirConfigurationError, match="cannot be imported"):
        build_backend({"adapter": "no.such.module.Port"}, setting="--backend")
    with pytest.raises(MimirConfigurationError, match="cannot be imported"):
        build_backend({"adapter": "pathlib.NoSuchClass"}, setting="--backend")


def test_an_adapter_that_is_not_a_mimir_port_is_refused() -> None:
    with pytest.raises(MimirConfigurationError, match="not a MimirPort"):
        build_backend({"adapter": "pathlib.Path"}, setting="--backend")


def test_the_named_adapter_is_built() -> None:
    port = build_backend(
        {"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"}, setting="--backend"
    )
    assert isinstance(port, InMemoryMimirAdapter)


def test_remaining_keys_are_constructor_kwargs() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'mcp_url'"):
        build_backend(
            {
                "adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter",
                "mcp_url": "http://x",
            },
            setting="--backend",
        )
