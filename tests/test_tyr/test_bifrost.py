"""Tests for BifröstAdapter — LLM spec decomposition."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.registry import (
    BIFROST_PROVIDER_DOWN,
    BIFROST_PROVIDER_RECOVERED,
    BIFROST_QUOTA_EXCEEDED,
    BIFROST_QUOTA_WARNING,
    BIFROST_REQUEST_COMPLETE,
)
from sleipnir.testing import EventCapture
from tyr.adapters.anthropic_client import ANTHROPIC_API_VERSION
from tyr.adapters.bifrost import (
    DECOMPOSITION_PROMPT,
    BifrostAdapter,
    DecompositionError,
)
from tyr.adapters.bifrost_publisher import BifrostPublisher
from tyr.domain.models import RaidSpec, SagaStructure
from tyr.domain.validation import ValidationError, parse_and_validate, validate_raid

# ---------------------------------------------------------------------------
# Valid fixture data
# ---------------------------------------------------------------------------

# Default bounds (matching LLMConfig defaults)
DEFAULT_MIN = 2.0
DEFAULT_MAX = 8.0

VALID_RAID = {
    "name": "Add user model",
    "description": "Create the User dataclass and persistence layer",
    "acceptance_criteria": ["Unit tests pass", "Coverage > 85%"],
    "declared_files": ["src/models/user.py", "tests/test_user.py"],
    "estimate_hours": 3.0,
    "confidence": 0.85,
}

VALID_RESPONSE = {
    "name": "User Management Saga",
    "phases": [
        {
            "name": "Phase 1 — Models",
            "raids": [VALID_RAID],
        }
    ],
}

API_URL = "http://bifrost.test/v1/messages"


def _api_response(payload: dict) -> dict:
    """Wrap payload as an Anthropic-style response."""
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }


# ---------------------------------------------------------------------------
# parse_and_validate
# ---------------------------------------------------------------------------


class TestParseAndValidate:
    def test_valid_response(self) -> None:
        raw = json.dumps(VALID_RESPONSE)
        result = parse_and_validate(raw)
        assert isinstance(result, SagaStructure)
        assert result.name == "User Management Saga"
        assert len(result.phases) == 1
        assert result.phases[0].name == "Phase 1 — Models"
        assert len(result.phases[0].raids) == 1
        raid = result.phases[0].raids[0]
        assert raid.name == "Add user model"
        assert raid.confidence == 0.85
        assert raid.estimate_hours == 3.0

    def test_strips_markdown_fences(self) -> None:
        raw = "```json\n" + json.dumps(VALID_RESPONSE) + "\n```"
        result = parse_and_validate(raw)
        assert result.name == "User Management Saga"

    def test_strips_plain_fences(self) -> None:
        raw = "```\n" + json.dumps(VALID_RESPONSE) + "\n```"
        result = parse_and_validate(raw)
        assert result.name == "User Management Saga"

    def test_invalid_json(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            parse_and_validate("not json at all")

    def test_not_a_dict(self) -> None:
        with pytest.raises(ValidationError, match="JSON object"):
            parse_and_validate(json.dumps([1, 2, 3]))

    def test_missing_name(self) -> None:
        data = {**VALID_RESPONSE, "name": ""}
        with pytest.raises(ValidationError, match="'name'"):
            parse_and_validate(json.dumps(data))

    def test_missing_phases(self) -> None:
        data = {**VALID_RESPONSE, "phases": []}
        with pytest.raises(ValidationError, match="non-empty list"):
            parse_and_validate(json.dumps(data))

    def test_phases_not_list(self) -> None:
        data = {**VALID_RESPONSE, "phases": "nope"}
        with pytest.raises(ValidationError, match="non-empty list"):
            parse_and_validate(json.dumps(data))

    def test_phase_not_dict(self) -> None:
        data = {"name": "S", "phases": ["not a dict"]}
        with pytest.raises(ValidationError, match="Phase 0 must be an object"):
            parse_and_validate(json.dumps(data))

    def test_phase_missing_name(self) -> None:
        data = {"name": "S", "phases": [{"raids": [VALID_RAID]}]}
        with pytest.raises(ValidationError, match="Phase 0.*'name'"):
            parse_and_validate(json.dumps(data))

    def test_phase_empty_raids(self) -> None:
        data = {"name": "S", "phases": [{"name": "P1", "raids": []}]}
        with pytest.raises(ValidationError, match="non-empty list"):
            parse_and_validate(json.dumps(data))

    def test_multiple_phases_and_raids(self) -> None:
        raid2 = {**VALID_RAID, "name": "Second raid"}
        data = {
            "name": "Multi",
            "phases": [
                {"name": "P1", "raids": [VALID_RAID]},
                {"name": "P2", "raids": [raid2]},
            ],
        }
        result = parse_and_validate(json.dumps(data))
        assert len(result.phases) == 2
        assert result.phases[1].raids[0].name == "Second raid"

    def test_integer_estimate_accepted(self) -> None:
        raid = {**VALID_RAID, "estimate_hours": 4}
        data = {"name": "S", "phases": [{"name": "P", "raids": [raid]}]}
        result = parse_and_validate(json.dumps(data))
        assert result.phases[0].raids[0].estimate_hours == 4.0

    def test_integer_confidence_accepted(self) -> None:
        raid = {**VALID_RAID, "confidence": 1}
        data = {"name": "S", "phases": [{"name": "P", "raids": [raid]}]}
        result = parse_and_validate(json.dumps(data))
        assert result.phases[0].raids[0].confidence == 1.0

    def test_custom_estimate_bounds(self) -> None:
        raid = {**VALID_RAID, "estimate_hours": 1.0}
        data = {"name": "S", "phases": [{"name": "P", "raids": [raid]}]}
        # Lenient validation clamps instead of rejecting
        result = parse_and_validate(json.dumps(data))
        assert result.phases[0].raids[0].estimate_hours == 1.0
        # Custom bounds also accept 1.0
        result = parse_and_validate(
            json.dumps(data), min_estimate_hours=0.5, max_estimate_hours=10.0
        )
        assert result.phases[0].raids[0].estimate_hours == 1.0


# ---------------------------------------------------------------------------
# validate_raid
# ---------------------------------------------------------------------------


class TestValidateRaid:
    def test_valid_raid(self) -> None:
        result = validate_raid(VALID_RAID, "P1", 0)
        assert isinstance(result, RaidSpec)
        assert result.name == "Add user model"

    def test_raid_not_dict(self) -> None:
        with pytest.raises(ValidationError, match="must be an object"):
            validate_raid("not a dict", "P1", 0)

    def test_missing_name(self) -> None:
        data = {**VALID_RAID, "name": ""}
        with pytest.raises(ValidationError, match="'name'"):
            validate_raid(data, "P1", 0)

    def test_missing_description(self) -> None:
        data = {**VALID_RAID, "description": ""}
        with pytest.raises(ValidationError, match="'description'"):
            validate_raid(data, "P1", 0)

    def test_empty_acceptance_criteria(self) -> None:
        data = {**VALID_RAID, "acceptance_criteria": []}
        with pytest.raises(ValidationError, match="acceptance_criteria"):
            validate_raid(data, "P1", 0)

    def test_blank_criterion(self) -> None:
        data = {**VALID_RAID, "acceptance_criteria": ["valid", "  "]}
        with pytest.raises(ValidationError, match="acceptance_criteria"):
            validate_raid(data, "P1", 0)

    def test_empty_declared_files(self) -> None:
        data = {**VALID_RAID, "declared_files": []}
        result = validate_raid(data, "P1", 0)
        assert result.declared_files == []  # lenient: empty list accepted

    def test_blank_declared_file(self) -> None:
        data = {**VALID_RAID, "declared_files": ["valid.py", ""]}
        result = validate_raid(data, "P1", 0)
        assert result.declared_files == ["valid.py"]  # blanks filtered out

    def test_estimate_not_number(self) -> None:
        data = {**VALID_RAID, "estimate_hours": "three"}
        result = validate_raid(data, "P1", 0)
        assert result.estimate_hours == DEFAULT_MIN  # defaults to min

    def test_estimate_too_low(self) -> None:
        data = {**VALID_RAID, "estimate_hours": 0.3}
        result = validate_raid(data, "P1", 0)
        assert result.estimate_hours == 0.5  # clamped to floor

    def test_estimate_too_high(self) -> None:
        data = {**VALID_RAID, "estimate_hours": 50.0}
        result = validate_raid(data, "P1", 0)
        assert result.estimate_hours == 40.0  # clamped to ceiling

    def test_estimate_at_min_boundary(self) -> None:
        data = {**VALID_RAID, "estimate_hours": DEFAULT_MIN}
        result = validate_raid(data, "P1", 0)
        assert result.estimate_hours == DEFAULT_MIN

    def test_estimate_at_max_boundary(self) -> None:
        data = {**VALID_RAID, "estimate_hours": DEFAULT_MAX}
        result = validate_raid(data, "P1", 0)
        assert result.estimate_hours == DEFAULT_MAX

    def test_custom_estimate_bounds_accepts_value(self) -> None:
        data = {**VALID_RAID, "estimate_hours": 1.0}
        result = validate_raid(data, "P1", 0, min_estimate_hours=0.5, max_estimate_hours=10.0)
        assert result.estimate_hours == 1.0

    def test_confidence_not_number(self) -> None:
        data = {**VALID_RAID, "confidence": "high"}
        result = validate_raid(data, "P1", 0)
        assert result.confidence == 0.5  # defaults to 0.5

    def test_confidence_out_of_range_low(self) -> None:
        data = {**VALID_RAID, "confidence": -0.1}
        result = validate_raid(data, "P1", 0)
        assert result.confidence == 0.0  # clamped to 0.0

    def test_confidence_out_of_range_high(self) -> None:
        data = {**VALID_RAID, "confidence": 1.1}
        result = validate_raid(data, "P1", 0)
        assert result.confidence == 1.0  # clamped to 1.0

    def test_confidence_at_boundaries(self) -> None:
        for val in [0.0, 1.0]:
            data = {**VALID_RAID, "confidence": val}
            result = validate_raid(data, "P1", 0)
            assert result.confidence == val

    def test_criteria_not_list(self) -> None:
        data = {**VALID_RAID, "acceptance_criteria": "just a string"}
        with pytest.raises(ValidationError, match="acceptance_criteria"):
            validate_raid(data, "P1", 0)

    def test_files_not_list(self) -> None:
        data = {**VALID_RAID, "declared_files": "single.py"}
        result = validate_raid(data, "P1", 0)
        assert result.declared_files == []  # lenient: non-list becomes empty


# ---------------------------------------------------------------------------
# BifrostAdapter
# ---------------------------------------------------------------------------


class TestBifrostAdapter:
    @pytest.fixture
    def adapter(self) -> BifrostAdapter:
        return BifrostAdapter(base_url="http://bifrost.test")

    @pytest.mark.asyncio
    @respx.mock
    async def test_decompose_spec_success(self, adapter: BifrostAdapter) -> None:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_api_response(VALID_RESPONSE))
        )
        result = await adapter.decompose_spec(
            "Build user management", "org/repo", model="claude-sonnet-4-6"
        )
        assert isinstance(result, SagaStructure)
        assert result.name == "User Management Saga"
        assert len(result.phases) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_decompose_spec_retries_on_bad_json(self, adapter: BifrostAdapter) -> None:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200, json={"content": [{"type": "text", "text": "not valid json"}]}
            )
        )
        with pytest.raises(DecompositionError, match="Failed to decompose"):
            await adapter.decompose_spec("spec", "repo", model="claude-sonnet-4-6")

    @pytest.mark.asyncio
    @respx.mock
    async def test_decompose_spec_retries_on_validation_error(
        self, adapter: BifrostAdapter
    ) -> None:
        invalid_data = {"name": "S", "phases": []}
        respx.post(API_URL).mock(return_value=httpx.Response(200, json=_api_response(invalid_data)))
        with pytest.raises(DecompositionError):
            await adapter.decompose_spec("spec", "repo", model="claude-sonnet-4-6")

    @pytest.mark.asyncio
    @respx.mock
    async def test_decompose_retries_then_succeeds(self, adapter: BifrostAdapter) -> None:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(200, json={"content": [{"type": "text", "text": "bad"}]}),
            httpx.Response(200, json=_api_response(VALID_RESPONSE)),
        ]
        result = await adapter.decompose_spec("spec", "repo", model="claude-sonnet-4-6")
        assert result.name == "User Management Saga"
        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_retryable_http_error_propagates(self, adapter: BifrostAdapter) -> None:
        respx.post(API_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.decompose_spec("spec", "repo", model="claude-sonnet-4-6")

    @pytest.mark.asyncio
    @respx.mock
    async def test_retryable_http_500_retries(self, adapter: BifrostAdapter) -> None:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(500),
            httpx.Response(200, json=_api_response(VALID_RESPONSE)),
        ]
        result = await adapter.decompose_spec("spec", "repo", model="m")
        assert result.name == "User Management Saga"
        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_retryable_http_429_retries(self) -> None:
        adapter = BifrostAdapter(base_url="http://bifrost.test", max_retries=2)
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(429),
            httpx.Response(200, json=_api_response(VALID_RESPONSE)),
        ]
        result = await adapter.decompose_spec("spec", "repo", model="m")
        assert result.name == "User Management Saga"
        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_retryable_http_503_exhausts_retries(self) -> None:
        adapter = BifrostAdapter(base_url="http://bifrost.test", max_retries=2)
        respx.post(API_URL).mock(return_value=httpx.Response(503))
        with pytest.raises(DecompositionError, match="2 attempts"):
            await adapter.decompose_spec("spec", "repo", model="m")

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_correct_payload(self, adapter: BifrostAdapter) -> None:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_api_response(VALID_RESPONSE))
        )
        await adapter.decompose_spec("my spec", "org/repo", model="claude-opus-4-6")
        assert route.called
        req = route.calls.last.request
        body = json.loads(req.content)
        assert body["model"] == "claude-opus-4-6"
        assert body["max_tokens"] == 8192
        assert len(body["messages"]) == 1
        assert "my spec" in body["messages"][0]["content"]
        assert "org/repo" in body["messages"][0]["content"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_anthropic_headers(self, adapter: BifrostAdapter) -> None:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_api_response(VALID_RESPONSE))
        )
        await adapter.decompose_spec("spec", "repo", model="m")
        req = route.calls.last.request
        assert req.headers["anthropic-version"] == ANTHROPIC_API_VERSION
        assert req.headers["content-type"] == "application/json"

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_api_key_header_when_set(self) -> None:
        adapter = BifrostAdapter(base_url="http://bifrost.test", api_key="sk-test-123")
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_api_response(VALID_RESPONSE))
        )
        await adapter.decompose_spec("spec", "repo", model="m")
        req = route.calls.last.request
        assert req.headers["x-api-key"] == "sk-test-123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_api_key_header_when_empty(self, adapter: BifrostAdapter) -> None:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_api_response(VALID_RESPONSE))
        )
        await adapter.decompose_spec("spec", "repo", model="m")
        req = route.calls.last.request
        assert "x-api-key" not in req.headers

    @pytest.mark.asyncio
    @respx.mock
    async def test_extracts_text_blocks(self, adapter: BifrostAdapter) -> None:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [
                        {"type": "text", "text": '{"name": "S",'},
                        {"type": "thinking", "text": "ignore this"},
                        {
                            "type": "text",
                            "text": ' "phases": [{"name": "P", "raids": [',
                        },
                        {
                            "type": "text",
                            "text": json.dumps(VALID_RAID) + "]}]}",
                        },
                    ]
                },
            )
        )
        result = await adapter.decompose_spec("spec", "repo", model="claude-sonnet-4-6")
        assert result.name == "S"

    def test_adapter_strips_trailing_slash(self) -> None:
        adapter = BifrostAdapter(base_url="http://bifrost.test/")
        assert adapter._base_url == "http://bifrost.test"

    def test_default_base_url(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._base_url == "https://api.anthropic.com"

    def test_default_estimate_bounds(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._min_estimate_hours == DEFAULT_MIN
        assert adapter._max_estimate_hours == DEFAULT_MAX

    def test_custom_estimate_bounds(self) -> None:
        adapter = BifrostAdapter(min_estimate_hours=1.0, max_estimate_hours=12.0)
        assert adapter._min_estimate_hours == 1.0
        assert adapter._max_estimate_hours == 12.0

    @pytest.mark.asyncio
    @respx.mock
    async def test_custom_max_tokens(self) -> None:
        adapter = BifrostAdapter(base_url="http://bifrost.test", max_tokens=4096)
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_api_response(VALID_RESPONSE))
        )
        await adapter.decompose_spec("spec", "repo", model="m")
        body = json.loads(route.calls.last.request.content)
        assert body["max_tokens"] == 4096

    @pytest.mark.asyncio
    @respx.mock
    async def test_custom_max_retries(self) -> None:
        adapter = BifrostAdapter(base_url="http://bifrost.test", max_retries=1)
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "bad"}]})
        )
        with pytest.raises(DecompositionError, match="1 attempts"):
            await adapter.decompose_spec("spec", "repo", model="m")

    @pytest.mark.asyncio
    async def test_close_shuts_down_client(self) -> None:
        adapter = BifrostAdapter(base_url="http://bifrost.test")
        assert not adapter._client.is_closed
        await adapter.close()
        assert adapter._client.is_closed


# ---------------------------------------------------------------------------
# Decomposition prompt
# ---------------------------------------------------------------------------


class TestDecompositionPrompt:
    def test_prompt_contains_placeholders(self) -> None:
        prompt = DECOMPOSITION_PROMPT.format(spec="test spec", repo="org/repo")
        assert "test spec" in prompt
        assert "org/repo" in prompt

    def test_prompt_mentions_constraints(self) -> None:
        assert "2–6 hours" in DECOMPOSITION_PROMPT
        assert "8 hours" in DECOMPOSITION_PROMPT
        assert "declared_files" in DECOMPOSITION_PROMPT
        assert "acceptance_criteria" in DECOMPOSITION_PROMPT
        assert "confidence" in DECOMPOSITION_PROMPT
        lower_prompt = DECOMPOSITION_PROMPT.lower()
        assert "no markdown fences" in lower_prompt or "no markdown" in lower_prompt

    def test_default_max_retries(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._max_retries == 2

    def test_default_max_tokens(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._max_tokens == 8192


# ---------------------------------------------------------------------------
# Helpers shared by Sleipnir emission tests
# ---------------------------------------------------------------------------

_API_URL = "http://bifrost.test/v1/messages"


def _ok_response_with_usage(
    payload: dict, input_tokens: int = 50, output_tokens: int = 150
) -> dict:
    """Wrap payload as an Anthropic-style response with usage data."""
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _make_adapter_with_bus(bus: InProcessBus, **kwargs) -> BifrostAdapter:
    adapter = BifrostAdapter(base_url="http://bifrost.test", **kwargs)
    adapter.set_publisher(BifrostPublisher(bus, agent_id=kwargs.get("agent_id", "")))
    return adapter


# ---------------------------------------------------------------------------
# Sleipnir event emission — bifrost.request.complete
# ---------------------------------------------------------------------------


class TestBifrostAdapterRequestCompleteEvent:
    @pytest.mark.asyncio
    @respx.mock
    async def test_emits_request_complete_on_success(self) -> None:
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus)
        respx.post(_API_URL).mock(
            return_value=httpx.Response(200, json=_ok_response_with_usage(VALID_RESPONSE))
        )

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 1
        assert capture.events[0].event_type == BIFROST_REQUEST_COMPLETE

    @pytest.mark.asyncio
    @respx.mock
    async def test_request_complete_urgency_is_zero(self) -> None:
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus)
        respx.post(_API_URL).mock(
            return_value=httpx.Response(200, json=_ok_response_with_usage(VALID_RESPONSE))
        )

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert capture.events[0].urgency == 0.0

    @pytest.mark.asyncio
    @respx.mock
    async def test_request_complete_token_counts_in_payload(self) -> None:
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus)
        respx.post(_API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_ok_response_with_usage(VALID_RESPONSE, input_tokens=100, output_tokens=200),
            )
        )

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await adapter.decompose_spec("spec", "repo", model="claude-opus-4-6")
            await bus.flush()

        p = capture.events[0].payload
        assert p["model"] == "claude-opus-4-6"
        assert p["input_tokens"] == 100
        assert p["output_tokens"] == 200
        assert p["total_tokens"] == 300

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_event_when_no_publisher(self) -> None:
        """BifrostAdapter without a publisher emits nothing."""
        bus = InProcessBus()
        adapter = BifrostAdapter(base_url="http://bifrost.test")
        respx.post(_API_URL).mock(
            return_value=httpx.Response(200, json=_ok_response_with_usage(VALID_RESPONSE))
        )

        async with EventCapture(bus, ["bifrost.*"]) as capture:
            await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 0


# ---------------------------------------------------------------------------
# Sleipnir event emission — bifrost.provider.down / recovered
# ---------------------------------------------------------------------------


class TestBifrostAdapterProviderEvents:
    @pytest.mark.asyncio
    @respx.mock
    async def test_emits_provider_down_on_5xx(self) -> None:
        """Sköll receives bifrost.provider.down when the provider returns 5xx."""
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus, max_retries=1)
        respx.post(_API_URL).mock(return_value=httpx.Response(503))

        async with EventCapture(bus, [BIFROST_PROVIDER_DOWN]) as capture:
            with pytest.raises(DecompositionError):
                await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 1
        evt = capture.events[0]
        assert evt.urgency == 0.8
        assert evt.payload["status_code"] == 503

    @pytest.mark.asyncio
    @respx.mock
    async def test_provider_down_emitted_only_once_per_outage(self) -> None:
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus, max_retries=2)
        respx.post(_API_URL).mock(return_value=httpx.Response(500))

        async with EventCapture(bus, [BIFROST_PROVIDER_DOWN]) as capture:
            with pytest.raises(DecompositionError):
                await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        # Both retries hit 500, but provider_down emitted only once.
        assert len(capture.events) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_provider_recovered_emitted_after_5xx_then_success(self) -> None:
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus, max_retries=2)
        route = respx.post(_API_URL)
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(200, json=_ok_response_with_usage(VALID_RESPONSE)),
        ]

        async with EventCapture(
            bus, [BIFROST_PROVIDER_DOWN, BIFROST_PROVIDER_RECOVERED]
        ) as capture:
            await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        types = {e.event_type for e in capture.events}
        assert BIFROST_PROVIDER_DOWN in types
        assert BIFROST_PROVIDER_RECOVERED in types

    @pytest.mark.asyncio
    @respx.mock
    async def test_provider_down_not_emitted_for_non_5xx(self) -> None:
        """A 429 (rate limit) should NOT trigger provider.down."""
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus, max_retries=1)
        respx.post(_API_URL).mock(return_value=httpx.Response(429))

        async with EventCapture(bus, [BIFROST_PROVIDER_DOWN]) as capture:
            with pytest.raises(DecompositionError):
                await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 0


# ---------------------------------------------------------------------------
# Sleipnir event emission — quota
# ---------------------------------------------------------------------------


class TestBifrostAdapterQuotaEvents:
    @pytest.mark.asyncio
    @respx.mock
    async def test_quota_warning_emitted_at_threshold(self) -> None:
        """Valkyries receive bifrost.quota.warning at the correct urgency (0.5)."""
        bus = InProcessBus()
        # Budget: 1000 tokens, threshold: 0.8 → warning at 800 tokens used.
        adapter = _make_adapter_with_bus(bus, budget_tokens=1000, quota_warning_threshold=0.8)
        # Each call uses 500 tokens (input=200, output=300).
        respx.post(_API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_ok_response_with_usage(VALID_RESPONSE, input_tokens=200, output_tokens=300),
            )
        )

        async with EventCapture(bus, [BIFROST_QUOTA_WARNING]) as capture:
            # First call: 500 tokens (50% — no warning)
            await adapter.decompose_spec("spec", "repo", model="m")
            # Second call: 1000 total (100% — warning triggers at 80%)
            await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 1
        evt = capture.events[0]
        assert evt.urgency == 0.5
        assert evt.payload["budget_tokens"] == 1000

    @pytest.mark.asyncio
    @respx.mock
    async def test_quota_warning_emitted_only_once(self) -> None:
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus, budget_tokens=100, quota_warning_threshold=0.1)
        respx.post(_API_URL).mock(
            return_value=httpx.Response(
                200, json=_ok_response_with_usage(VALID_RESPONSE, input_tokens=20, output_tokens=5)
            )
        )

        async with EventCapture(bus, [BIFROST_QUOTA_WARNING]) as capture:
            for _ in range(3):
                await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_quota_exceeded_emitted_at_budget(self) -> None:
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus, budget_tokens=100, quota_warning_threshold=0.8)
        respx.post(_API_URL).mock(
            return_value=httpx.Response(
                200, json=_ok_response_with_usage(VALID_RESPONSE, input_tokens=60, output_tokens=60)
            )
        )

        async with EventCapture(bus, [BIFROST_QUOTA_EXCEEDED]) as capture:
            await adapter.decompose_spec("spec", "repo", model="m")
            await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 1
        assert capture.events[0].urgency == 0.7

    @pytest.mark.asyncio
    @respx.mock
    async def test_quota_exceeded_emitted_only_once(self) -> None:
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus, budget_tokens=50, quota_warning_threshold=0.1)
        respx.post(_API_URL).mock(
            return_value=httpx.Response(
                200, json=_ok_response_with_usage(VALID_RESPONSE, input_tokens=30, output_tokens=30)
            )
        )

        async with EventCapture(bus, [BIFROST_QUOTA_EXCEEDED]) as capture:
            for _ in range(3):
                await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_quota_events_when_budget_is_zero(self) -> None:
        """budget_tokens=0 means unlimited — no quota events."""
        bus = InProcessBus()
        adapter = _make_adapter_with_bus(bus, budget_tokens=0)
        respx.post(_API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_ok_response_with_usage(VALID_RESPONSE, input_tokens=9999, output_tokens=9999),
            )
        )

        async with EventCapture(bus, [BIFROST_QUOTA_WARNING, BIFROST_QUOTA_EXCEEDED]) as capture:
            await adapter.decompose_spec("spec", "repo", model="m")
            await bus.flush()

        assert len(capture.events) == 0


# ---------------------------------------------------------------------------
# New config fields
# ---------------------------------------------------------------------------


class TestBifrostAdapterNewConfig:
    def test_default_budget_tokens_is_zero(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._budget_tokens == 0

    def test_default_quota_warning_threshold(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._quota_warning_threshold == 0.8

    def test_custom_budget_and_threshold(self) -> None:
        adapter = BifrostAdapter(budget_tokens=5000, quota_warning_threshold=0.9)
        assert adapter._budget_tokens == 5000
        assert adapter._quota_warning_threshold == 0.9

    def test_set_publisher_assigns_publisher(self) -> None:
        adapter = BifrostAdapter()
        bus = InProcessBus()
        pub = BifrostPublisher(bus)
        adapter.set_publisher(pub)
        assert adapter._publisher is pub

    def test_provider_starts_healthy(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._provider_healthy is True

    def test_total_tokens_starts_at_zero(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._total_tokens == 0
