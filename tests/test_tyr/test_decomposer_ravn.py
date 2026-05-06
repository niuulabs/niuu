"""Tests for the decomposer ravn persona and BifrostAdapter decomposer integration.

Test harness from NIU-619 spec:
  6. Unit: decomposer persona — load YAML, verify schema matches SagaStructure
  7. Integration: decompose round-trip — mock dispatcher returning canned SagaStructure;
     BifrostAdapter dispatches to decomposer → validated → returned correctly
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from tyr.adapters.bifrost import BifrostAdapter
from tyr.domain.models import SagaStructure

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SAGA = {
    "name": "Auth Rework Saga",
    "phases": [
        {
            "name": "Phase 1 — Core",
            "raids": [
                {
                    "name": "Add JWT validation",
                    "description": "Implement JWT token validation middleware",
                    "acceptance_criteria": ["tokens validated", "tests pass"],
                    "declared_files": ["src/auth/jwt.py", "tests/test_jwt.py"],
                    "estimate_hours": 3.0,
                    "confidence": 0.85,
                }
            ],
        }
    ],
}

VALID_SAGA_JSON = json.dumps(VALID_SAGA)

OUTCOME_BLOCK = f"---outcome---\nphases: '{VALID_SAGA_JSON}'\n---end---"


def _make_adapter(
    *,
    ravn_decomposer_enabled: bool = False,
    api_url: str = "http://bifrost.test",
    ravn_decomposer_timeout: float = 5.0,
) -> BifrostAdapter:
    return BifrostAdapter(
        base_url=api_url,
        api_key="test-key",
        ravn_decomposer_enabled=ravn_decomposer_enabled,
        ravn_decomposer_timeout=ravn_decomposer_timeout,
    )


def _api_response(text: str) -> dict:
    """Wrap text as Anthropic-style response."""
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 100, "output_tokens": 200},
    }


# ---------------------------------------------------------------------------
# 6. Unit: decomposer persona YAML
# ---------------------------------------------------------------------------


class TestDecomposerPersona:
    def test_persona_loads(self) -> None:
        """decomposer.yaml must be loadable by FilesystemPersonaAdapter."""
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        loader = FilesystemPersonaAdapter()
        persona = loader.load("decomposer")
        assert persona is not None, "decomposer persona not found"

    def test_persona_name(self) -> None:
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        persona = FilesystemPersonaAdapter().load("decomposer")
        assert persona is not None
        assert persona.name == "decomposer"

    def test_persona_has_system_prompt(self) -> None:
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        persona = FilesystemPersonaAdapter().load("decomposer")
        assert persona is not None
        assert len(persona.system_prompt_template) > 50

    def test_persona_schema_has_phases_field(self) -> None:
        """decomposer must declare a 'phases' field in its produces schema."""
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        persona = FilesystemPersonaAdapter().load("decomposer")
        assert persona is not None
        assert "phases" in persona.produces.schema

    def test_persona_phases_field_is_string(self) -> None:
        """'phases' field must be type 'string' (JSON-encoded SagaStructure)."""
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        persona = FilesystemPersonaAdapter().load("decomposer")
        assert persona is not None
        phases_field = persona.produces.schema["phases"]
        assert phases_field.type == "string"

    def test_persona_iteration_budget(self) -> None:
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        persona = FilesystemPersonaAdapter().load("decomposer")
        assert persona is not None
        assert persona.iteration_budget == 20

    def test_persona_stop_on_outcome(self) -> None:
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        persona = FilesystemPersonaAdapter().load("decomposer")
        assert persona is not None
        assert persona.stop_on_outcome is True

    def test_persona_allowed_tools_include_file_tools(self) -> None:
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        persona = FilesystemPersonaAdapter().load("decomposer")
        assert persona is not None
        assert "file_read" in persona.allowed_tools or "glob" in persona.allowed_tools

    def test_persona_allowed_tools_include_mimir(self) -> None:
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        persona = FilesystemPersonaAdapter().load("decomposer")
        assert persona is not None
        assert any("mimir" in t for t in persona.allowed_tools)


# ---------------------------------------------------------------------------
# 7. Integration: decompose round-trip
# ---------------------------------------------------------------------------


class TestDecomposerRoundTrip:
    @pytest.mark.asyncio
    @respx.mock
    async def test_decomposer_ravn_success(self) -> None:
        """When decomposer ravn returns valid JSON, BifrostAdapter returns SagaStructure."""
        # Mock the ravn dispatcher LLM call (same endpoint, same api)
        respx.post("http://bifrost.test/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json=_api_response(OUTCOME_BLOCK),
            )
        )

        adapter = _make_adapter(ravn_decomposer_enabled=True, api_url="http://bifrost.test")
        try:
            result = await adapter.decompose_spec(
                spec="Add JWT auth to the platform",
                repo="org/repo",
                model="claude-sonnet-4-6",
            )
        finally:
            await adapter.close()

        assert isinstance(result, SagaStructure)
        assert result.name == "Auth Rework Saga"
        assert len(result.phases) == 1
        assert result.phases[0].name == "Phase 1 — Core"
        assert len(result.phases[0].raids) == 1
        assert result.phases[0].raids[0].name == "Add JWT validation"

    @pytest.mark.asyncio
    @respx.mock
    async def test_decomposer_ravn_fallback_on_no_outcome(self) -> None:
        """When decomposer ravn returns no outcome block, fall back to direct API."""
        call_count = 0

        def response_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (ravn dispatcher): no outcome block
                return httpx.Response(200, json=_api_response("I am thinking..."))
            # Second call (direct API fallback): valid JSON
            return httpx.Response(200, json=_api_response(VALID_SAGA_JSON))

        respx.post("http://bifrost.test/v1/messages").mock(side_effect=response_handler)

        adapter = _make_adapter(ravn_decomposer_enabled=True, api_url="http://bifrost.test")
        try:
            result = await adapter.decompose_spec(
                spec="Add JWT auth",
                repo="org/repo",
                model="claude-sonnet-4-6",
            )
        finally:
            await adapter.close()

        assert isinstance(result, SagaStructure)
        # Two API calls: one for ravn, one for fallback
        assert call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_decomposer_disabled_goes_direct(self) -> None:
        """When ravn_decomposer_enabled=False, only direct API is called."""
        call_count = 0

        def response_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_api_response(VALID_SAGA_JSON))

        respx.post("http://bifrost.test/v1/messages").mock(side_effect=response_handler)

        adapter = _make_adapter(ravn_decomposer_enabled=False, api_url="http://bifrost.test")
        try:
            result = await adapter.decompose_spec(
                spec="Add JWT auth",
                repo="org/repo",
                model="claude-sonnet-4-6",
            )
        finally:
            await adapter.close()

        assert isinstance(result, SagaStructure)
        assert call_count == 1  # Only direct call

    @pytest.mark.asyncio
    @respx.mock
    async def test_decomposer_ravn_invalid_json_fallback(self) -> None:
        """When decomposer ravn outcome has invalid JSON in 'phases', fall back."""
        bad_outcome = "---outcome---\nphases: 'not valid json {{{'\n---end---"
        call_count = 0

        def response_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json=_api_response(bad_outcome))
            return httpx.Response(200, json=_api_response(VALID_SAGA_JSON))

        respx.post("http://bifrost.test/v1/messages").mock(side_effect=response_handler)

        adapter = _make_adapter(ravn_decomposer_enabled=True, api_url="http://bifrost.test")
        try:
            result = await adapter.decompose_spec(
                spec="Add JWT auth",
                repo="org/repo",
                model="claude-sonnet-4-6",
            )
        finally:
            await adapter.close()

        assert isinstance(result, SagaStructure)
        assert call_count == 2  # ravn failed → fallback

    @pytest.mark.asyncio
    @respx.mock
    async def test_decomposer_ravn_context_includes_spec_and_repo(self) -> None:
        """The context passed to the decomposer ravn must include spec and repo."""
        captured_bodies: list[dict] = []

        def capture_handler(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            # Return valid saga JSON directly so we get a result
            return httpx.Response(200, json=_api_response(OUTCOME_BLOCK))

        respx.post("http://bifrost.test/v1/messages").mock(side_effect=capture_handler)

        adapter = _make_adapter(ravn_decomposer_enabled=True, api_url="http://bifrost.test")
        try:
            await adapter.decompose_spec(
                spec="Implement OAuth2 login flow",
                repo="org/platform",
                model="claude-sonnet-4-6",
            )
        finally:
            await adapter.close()

        assert len(captured_bodies) >= 1
        user_content = captured_bodies[0]["messages"][0]["content"]
        assert "Implement OAuth2 login flow" in user_content
        assert "org/platform" in user_content


# ---------------------------------------------------------------------------
# Unit: RavnDispatcher
# ---------------------------------------------------------------------------


class TestRavnDispatcher:
    @pytest.mark.asyncio
    @respx.mock
    async def test_dispatch_returns_parsed_outcome(self) -> None:
        """RavnDispatcher.dispatch() returns parsed outcome fields."""
        from tyr.adapters.ravn_dispatcher import RavnDispatcher

        response_text = (
            "Some reasoning...\n\n---outcome---\nverdict: approve\nreason: all good\n---end---"
        )
        respx.post("http://dispatch.test/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={"content": [{"type": "text", "text": response_text}]},
            )
        )

        dispatcher = RavnDispatcher(
            base_url="http://dispatch.test",
            api_key="test",
            model="claude-sonnet-4-6",
        )
        try:
            result = await dispatcher.dispatch("review-arbiter", "context here")
        finally:
            await dispatcher.close()

        assert result is not None
        assert result["verdict"] == "approve"
        assert result["reason"] == "all good"

    @pytest.mark.asyncio
    @respx.mock
    async def test_dispatch_unknown_persona_returns_none(self) -> None:
        """Dispatching an unknown persona returns None without calling the API."""
        from tyr.adapters.ravn_dispatcher import RavnDispatcher

        dispatcher = RavnDispatcher(
            base_url="http://dispatch.test",
            api_key="test",
        )
        try:
            result = await dispatcher.dispatch("nonexistent-persona-xyz", "context")
        finally:
            await dispatcher.close()

        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_dispatch_no_outcome_block_returns_none(self) -> None:
        """When LLM response has no ---outcome--- block, dispatch returns None."""
        from tyr.adapters.ravn_dispatcher import RavnDispatcher

        respx.post("http://dispatch.test/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={"content": [{"type": "text", "text": "Just some text, no outcome."}]},
            )
        )

        dispatcher = RavnDispatcher(
            base_url="http://dispatch.test",
            api_key="test",
        )
        try:
            result = await dispatcher.dispatch("review-arbiter", "context")
        finally:
            await dispatcher.close()

        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_dispatch_http_error_returns_none(self) -> None:
        """When the API returns an error, dispatch returns None."""
        from tyr.adapters.ravn_dispatcher import RavnDispatcher

        respx.post("http://dispatch.test/v1/messages").mock(
            return_value=httpx.Response(500, json={"error": "server error"})
        )

        dispatcher = RavnDispatcher(
            base_url="http://dispatch.test",
            api_key="test",
        )
        try:
            result = await dispatcher.dispatch("review-arbiter", "context")
        finally:
            await dispatcher.close()

        assert result is None

    def test_load_persona_review_arbiter(self) -> None:
        """RavnDispatcher.load_persona() can resolve 'review-arbiter'."""
        from tyr.adapters.ravn_dispatcher import RavnDispatcher

        dispatcher = RavnDispatcher()
        persona = dispatcher.load_persona("review-arbiter")
        assert persona is not None
        assert persona.name == "review-arbiter"

    def test_load_persona_decomposer(self) -> None:
        """RavnDispatcher.load_persona() can resolve 'decomposer'."""
        from tyr.adapters.ravn_dispatcher import RavnDispatcher

        dispatcher = RavnDispatcher()
        persona = dispatcher.load_persona("decomposer")
        assert persona is not None
        assert persona.name == "decomposer"
