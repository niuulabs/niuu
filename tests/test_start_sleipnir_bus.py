"""Forge's Sleipnir bus: off is off, on must start."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from volundr.composition_builders import _start_sleipnir_bus


def _settings(**fields):
    base = {"enabled": True, "adapter": "sleipnir.adapters.in_process.InProcessBus"}
    base.update(fields)
    return SimpleNamespace(sleipnir=SimpleNamespace(kwargs={}, secret_kwargs_env={}, **base))


async def test_disabled_runs_without_a_bus() -> None:
    assert await _start_sleipnir_bus(_settings(enabled=False)) is None


async def test_enabled_starts_the_configured_bus() -> None:
    bus = await _start_sleipnir_bus(_settings())
    assert type(bus).__name__ == "InProcessBus"


@pytest.mark.parametrize("adapter", ["no.such.module.Bus", "sleipnir.adapters.in_process.Nope"])
async def test_enabled_but_unable_to_start_is_fatal_with_the_remedy(adapter: str) -> None:
    with pytest.raises(RuntimeError, match="sleipnir.enabled: false"):
        await _start_sleipnir_bus(_settings(adapter=adapter))
