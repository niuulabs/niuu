"""Focused tests for Tyr application wiring helpers."""

from __future__ import annotations

import pytest

from ravn.ports.persona import PersonaPort
from tyr.api.persona_names import build_persona_names_dependency


class _StubPersonaSource(PersonaPort):
    def __init__(self, names: list[str], *, should_raise: bool = False) -> None:
        self._names = names
        self._should_raise = should_raise

    def load(self, name: str):
        return None

    def list_names(self) -> list[str]:
        if self._should_raise:
            raise RuntimeError("boom")
        return self._names


class TestBuildPersonaNamesDependency:
    @pytest.mark.asyncio
    async def test_returns_names_from_persona_source(self) -> None:
        dependency = build_persona_names_dependency(
            _StubPersonaSource(["reviewer", "coordinator"])
        )

        assert await dependency() == {"reviewer", "coordinator"}

    @pytest.mark.asyncio
    async def test_none_source_returns_empty_set(self) -> None:
        dependency = build_persona_names_dependency(None)

        assert await dependency() == set()

    @pytest.mark.asyncio
    async def test_source_error_returns_empty_set(self) -> None:
        dependency = build_persona_names_dependency(
            _StubPersonaSource([], should_raise=True)
        )

        assert await dependency() == set()
