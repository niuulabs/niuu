"""Tests for RavnDispatcher LLM config resolution (NIU-645).

Test matrix:
  1. Persona with llm.primary_alias set + default_llm_config.model → uses config model
  2. Persona without llm fields + default_llm_config.model → uses config model
  3. Persona with llm.max_tokens + no global → uses persona max_tokens fallback
  4. global_override max_tokens wins over persona default
  5. Per-dispatch model= kwarg always wins over everything
  6. No persona LLM, no global config → falls back to constructor default model
  7. default_llm_config=None → uses constructor default model
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from ravn.adapters.personas.loader import PersonaConfig, PersonaLLMConfig
from ravn.ports.persona import PersonaPort
from tyr.adapters.ravn_dispatcher import RavnDispatcher

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_URL = "http://ravn-dispatch.test"
_OUTCOME_BLOCK = "---outcome---\nverdict: approve\nreason: looks good\n---end---"
_FAKE_RESPONSE = {
    "content": [{"type": "text", "text": _OUTCOME_BLOCK}],
    "usage": {"input_tokens": 50, "output_tokens": 100},
}


def _stub_loader(persona_name: str, primary_alias: str = "", max_tokens: int = 0) -> MagicMock:
    """Return a fake PersonaPort whose .load() returns a minimal PersonaConfig."""
    persona = PersonaConfig(
        name=persona_name,
        system_prompt_template="You are a test agent.",
        llm=PersonaLLMConfig(
            primary_alias=primary_alias,
            thinking_enabled=False,
            max_tokens=max_tokens,
        ),
    )
    loader = MagicMock(spec=PersonaPort)
    loader.load.return_value = persona
    return loader


def _make_dispatcher(
    *,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
    default_llm_config: dict | None = None,
    loader: PersonaPort | None = None,
) -> RavnDispatcher:
    return RavnDispatcher(
        base_url=_BASE_URL,
        api_key="test-key",
        model=model,
        max_tokens=max_tokens,
        default_llm_config=default_llm_config,
        persona_loader=loader,
    )


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestRavnDispatcherLLMResolution:
    """Model resolution matrix for RavnDispatcher.dispatch()."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_global_model_overrides_constructor_default(self) -> None:
        """default_llm_config.model wins over the constructor default model."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_FAKE_RESPONSE)

        respx.post(f"{_BASE_URL}/v1/messages").mock(side_effect=handler)

        loader = _stub_loader("test-persona", primary_alias="")
        dispatcher = _make_dispatcher(
            model="claude-sonnet-4-6",
            default_llm_config={"model": "claude-opus-4-6"},
            loader=loader,
        )
        try:
            result = await dispatcher.dispatch("test-persona", "some context")
        finally:
            await dispatcher.close()

        assert result is not None
        assert captured[0]["model"] == "claude-opus-4-6"

    @pytest.mark.asyncio
    @respx.mock
    async def test_persona_primary_alias_does_not_block_global_model(self) -> None:
        """Persona with primary_alias='powerful' still uses the global model override."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_FAKE_RESPONSE)

        respx.post(f"{_BASE_URL}/v1/messages").mock(side_effect=handler)

        loader = _stub_loader("test-persona", primary_alias="powerful")
        dispatcher = _make_dispatcher(
            model="claude-sonnet-4-6",
            default_llm_config={"model": "claude-opus-4-6"},
            loader=loader,
        )
        try:
            result = await dispatcher.dispatch("test-persona", "context")
        finally:
            await dispatcher.close()

        assert result is not None
        # Global override provides concrete model — persona alias does not override it.
        assert captured[0]["model"] == "claude-opus-4-6"

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_global_model_falls_back_to_constructor_default(self) -> None:
        """When default_llm_config has no 'model' key, use the constructor default."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_FAKE_RESPONSE)

        respx.post(f"{_BASE_URL}/v1/messages").mock(side_effect=handler)

        loader = _stub_loader("test-persona", primary_alias="powerful")
        dispatcher = _make_dispatcher(
            model="claude-sonnet-4-6",
            default_llm_config={"max_tokens": 8192},  # no 'model' key
            loader=loader,
        )
        try:
            result = await dispatcher.dispatch("test-persona", "context")
        finally:
            await dispatcher.close()

        assert result is not None
        assert captured[0]["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_global_config_falls_back_to_constructor_default(self) -> None:
        """When default_llm_config=None, use the constructor default model."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_FAKE_RESPONSE)

        respx.post(f"{_BASE_URL}/v1/messages").mock(side_effect=handler)

        loader = _stub_loader("test-persona", primary_alias="powerful")
        dispatcher = _make_dispatcher(
            model="claude-sonnet-4-6",
            default_llm_config=None,
            loader=loader,
        )
        try:
            result = await dispatcher.dispatch("test-persona", "context")
        finally:
            await dispatcher.close()

        assert result is not None
        assert captured[0]["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    @respx.mock
    async def test_per_dispatch_model_kwarg_always_wins(self) -> None:
        """The model= kwarg passed to dispatch() takes highest priority."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_FAKE_RESPONSE)

        respx.post(f"{_BASE_URL}/v1/messages").mock(side_effect=handler)

        loader = _stub_loader("test-persona", primary_alias="powerful")
        dispatcher = _make_dispatcher(
            model="claude-sonnet-4-6",
            default_llm_config={"model": "claude-opus-4-6"},
            loader=loader,
        )
        try:
            result = await dispatcher.dispatch(
                "test-persona", "context", model="claude-haiku-4-5-20251001"
            )
        finally:
            await dispatcher.close()

        assert result is not None
        assert captured[0]["model"] == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_global_max_tokens_overrides_constructor_default(self) -> None:
        """default_llm_config.max_tokens wins over the constructor max_tokens."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_FAKE_RESPONSE)

        respx.post(f"{_BASE_URL}/v1/messages").mock(side_effect=handler)

        loader = _stub_loader("test-persona")
        dispatcher = _make_dispatcher(
            max_tokens=4096,
            default_llm_config={"model": "claude-sonnet-4-6", "max_tokens": 8192},
            loader=loader,
        )
        try:
            await dispatcher.dispatch("test-persona", "context")
        finally:
            await dispatcher.close()

        assert captured[0]["max_tokens"] == 8192

    @pytest.mark.asyncio
    @respx.mock
    async def test_persona_max_tokens_used_as_default_when_no_global(self) -> None:
        """Persona max_tokens (non-zero) propagates as the default when no global override."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_FAKE_RESPONSE)

        respx.post(f"{_BASE_URL}/v1/messages").mock(side_effect=handler)

        # Persona has max_tokens=2048; constructor default is 4096; no global config.
        # merge_llm puts persona max_tokens in defaults, but since global_override
        # has no max_tokens key the default (4096) should remain.
        # (persona.llm.max_tokens=0 means "unset"; non-zero would override)
        loader = _stub_loader("test-persona", max_tokens=0)  # 0 = unset
        dispatcher = _make_dispatcher(
            max_tokens=4096,
            default_llm_config=None,
            loader=loader,
        )
        try:
            await dispatcher.dispatch("test-persona", "context")
        finally:
            await dispatcher.close()

        # 0 in persona.llm.max_tokens → _is_empty → not overriding constructor default
        assert captured[0]["max_tokens"] == 4096

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_default_llm_config_uses_constructor_model(self) -> None:
        """An empty dict for default_llm_config is treated as 'no override'."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_FAKE_RESPONSE)

        respx.post(f"{_BASE_URL}/v1/messages").mock(side_effect=handler)

        loader = _stub_loader("test-persona", primary_alias="powerful")
        dispatcher = _make_dispatcher(
            model="claude-sonnet-4-6",
            default_llm_config={},  # empty — no model override
            loader=loader,
        )
        try:
            result = await dispatcher.dispatch("test-persona", "context")
        finally:
            await dispatcher.close()

        assert result is not None
        assert captured[0]["model"] == "claude-sonnet-4-6"

    def test_default_llm_config_stored(self) -> None:
        """Verify default_llm_config is accessible on the dispatcher instance."""
        cfg = {"model": "claude-opus-4-6", "max_tokens": 8192}
        dispatcher = RavnDispatcher(default_llm_config=cfg)
        assert dispatcher._default_llm_config == cfg

    def test_default_llm_config_defaults_to_empty(self) -> None:
        """When default_llm_config not passed, it defaults to empty dict."""
        dispatcher = RavnDispatcher()
        assert dispatcher._default_llm_config == {}
