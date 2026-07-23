"""Tests for NIU-594: outcome block parsing, episode enrichment, and Sleipnir emission.

Covers:
- _parse_outcome_block_for_persona helper
- Episode.structured_outcome / Episode.outcome_valid fields
- TurnResult.episode field populated after run_turn()
- ravn.session.ended emitted with structured_outcome in payload
- ravn.session.started emitted on first turn
- Agent without persona_config produces no outcome parsing
- emit_session_ended() is a no-op when already emitted by outcome capture
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from niuu.domain.mimir import MimirPage, MimirPageMeta, MimirSource, compute_content_hash
from niuu.domain.outcome import OutcomeField
from ravn.adapters.personas.loader import (
    PersonaConfig,
    PersonaProduces,
)
from ravn.agent import (
    RavnAgent,
    _parse_outcome_block_for_persona,
    _validate_mimir_outcome_for_persona,
)
from ravn.domain.models import (
    Episode,
    LLMResponse,
    Outcome,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    TurnResult,
)
from ravn.ports.llm import LLMPort
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent
from tests.ravn.fixtures.fakes import InMemoryChannel
from tests.test_ravn.conftest import AllowAllPermission

# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


def _make_llm_with_response(response_text: str) -> LLMPort:
    """Build a mock LLM that streams *response_text* as a single text delta."""

    async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=response_text)
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )

    llm = AsyncMock(spec=LLMPort)
    llm.stream = _stream
    llm.generate = AsyncMock(
        return_value=LLMResponse(
            content="reflection",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=2, output_tokens=2),
        )
    )
    return llm


def _reviewer_persona() -> PersonaConfig:
    """Return the built-in reviewer persona config (with schema)."""
    from ravn.adapters.personas.loader import FilesystemPersonaAdapter

    persona = FilesystemPersonaAdapter().load("reviewer")
    assert persona is not None, "reviewer persona must exist in built-ins"
    return persona


def _research_persona() -> PersonaConfig:
    return PersonaConfig(
        name="research-persona",
        produces=PersonaProduces(
            event_type="research.completed",
            schema={
                "summary": OutcomeField(type="string", description="summary"),
                "page_path": OutcomeField(type="string", description="page path"),
            },
        ),
    )


def _research_alias_persona() -> PersonaConfig:
    return PersonaConfig(
        name="council-chair",
        produces=PersonaProduces(
            event_type="council.chair.turn.completed",
            event_type_map={"research_published": "research.completed"},
            schema={
                "summary": OutcomeField(type="string", description="summary"),
                "page_path": OutcomeField(type="string", description="page path"),
            },
        ),
    )


def _artifact_persona() -> PersonaConfig:
    return PersonaConfig(
        name="artifact-publisher",
        produces=PersonaProduces(
            event_type="artifact.publish.completed",
            schema={
                "verdict": OutcomeField(
                    type="enum",
                    enum_values=["published", "blocked"],
                    description="publication verdict",
                ),
                "artifact_path": OutcomeField(type="string", description="artifact path"),
            },
        ),
    )


class _FakeMimir:
    def __init__(
        self,
        *,
        page: MimirPage | None = None,
        sources: dict[str, MimirSource] | None = None,
    ):
        self._page = page
        self._sources = sources or {}

    async def get_page(self, path: str) -> MimirPage:
        if self._page is None or self._page.meta.path != path:
            raise FileNotFoundError(path)
        return self._page

    async def read_source(self, source_id: str) -> MimirSource | None:
        return self._sources.get(source_id)


def _make_agent(
    llm: LLMPort,
    *,
    persona_config: object | None = None,
    sleipnir_publisher: object | None = None,
    persona_name: str = "",
) -> tuple[RavnAgent, InMemoryChannel]:
    ch = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=ch,
        permission=AllowAllPermission(),
        system_prompt="You are a test assistant.",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=10,
        persona_config=persona_config,
        sleipnir_publisher=sleipnir_publisher,
        persona=persona_name,
    )
    return agent, ch


# ---------------------------------------------------------------------------
# Unit tests: _parse_outcome_block_for_persona
# ---------------------------------------------------------------------------


class TestParseOutcomeBlockForPersona:
    def test_returns_none_when_no_persona(self) -> None:
        result = _parse_outcome_block_for_persona("some text", None)
        assert result is None

    def test_returns_none_when_persona_has_no_schema(self) -> None:
        persona = PersonaConfig(name="minimal")
        result = _parse_outcome_block_for_persona("some text", persona)
        assert result is None

    def test_returns_none_when_response_has_no_block(self) -> None:
        persona = _reviewer_persona()
        result = _parse_outcome_block_for_persona("No outcome block here.", persona)
        assert result is None

    def test_parses_valid_outcome_block(self) -> None:
        persona = _reviewer_persona()
        text = (
            "I reviewed the code.\n"
            "---outcome---\n"
            "verdict: pass\n"
            "findings_count: 0\n"
            "critical_count: 0\n"
            "summary: All good\n"
            "---end---\n"
        )
        result = _parse_outcome_block_for_persona(text, persona)
        assert result is not None
        assert result.valid is True  # type: ignore[union-attr]
        assert result.fields["verdict"] == "pass"  # type: ignore[union-attr]

    def test_parses_invalid_outcome_block_marks_valid_false(self) -> None:
        persona = _reviewer_persona()
        text = (
            "---outcome---\n"
            "verdict: maybe\n"  # not in enum
            "---end---\n"
        )
        result = _parse_outcome_block_for_persona(text, persona)
        assert result is not None
        assert result.valid is False  # type: ignore[union-attr]

    def test_returns_none_when_produces_attr_is_none(self) -> None:
        """Cover the produces-is-None branch in the helper."""

        class _FakePersona:
            produces = None

        result = _parse_outcome_block_for_persona("some text", _FakePersona())
        assert result is None

    def test_returns_none_on_import_or_parse_exception(self) -> None:
        """Cover the exception handler in _parse_outcome_block_for_persona."""
        from unittest.mock import patch

        from niuu.domain.outcome import OutcomeField

        class _FakeProduces:
            schema = {"bad_field": OutcomeField(type="string", description="x")}

        class _FakePersona:
            produces = _FakeProduces()

        text = "---outcome---\nbad_field: hello\n---end---\n"
        # Force parse_outcome_block to raise to cover the except branch
        with patch("niuu.domain.outcome.parse_outcome_block", side_effect=RuntimeError("forced")):
            result = _parse_outcome_block_for_persona(text, _FakePersona())
        assert result is None


class TestValidateMimirOutcomeForPersona:
    @pytest.mark.asyncio
    async def test_published_artifact_must_exist_in_mimir(self) -> None:
        persona = _artifact_persona()
        parsed = _parse_outcome_block_for_persona(
            (
                "---outcome---\n"
                "verdict: published\n"
                "artifact_path: capabilities/demo/learned_tool.json\n"
                "---end---\n"
            ),
            persona,
        )
        assert parsed is not None

        validated = await _validate_mimir_outcome_for_persona(
            parsed,
            persona_config=persona,
            mimir=_FakeMimir(),
        )

        assert validated.valid is False
        assert any("artifact not found in Mimir" in error for error in validated.errors)

    @pytest.mark.asyncio
    async def test_published_artifact_requires_configured_mimir(self) -> None:
        persona = _artifact_persona()
        parsed = _parse_outcome_block_for_persona(
            (
                "---outcome---\n"
                "verdict: published\n"
                "artifact_path: capabilities/demo/learned_tool.json\n"
                "---end---\n"
            ),
            persona,
        )
        assert parsed is not None

        validated = await _validate_mimir_outcome_for_persona(
            parsed,
            persona_config=persona,
            mimir=None,
        )

        assert validated.valid is False
        assert any("Mimir is not configured" in error for error in validated.errors)

    @pytest.mark.asyncio
    async def test_published_artifact_is_valid_when_mimir_read_succeeds(self) -> None:
        persona = _artifact_persona()
        path = "capabilities/demo/learned_tool.json"
        parsed = _parse_outcome_block_for_persona(
            f"---outcome---\nverdict: published\nartifact_path: {path}\n---end---\n",
            persona,
        )
        assert parsed is not None
        page = MimirPage(
            meta=MimirPageMeta(
                path=path,
                title="learned_tool.json",
                summary="",
                category="capabilities",
                updated_at=datetime.now(UTC),
            ),
            content='{"manifest": {"name": "demo"}}',
        )

        validated = await _validate_mimir_outcome_for_persona(
            parsed,
            persona_config=persona,
            mimir=_FakeMimir(page=page),
        )

        assert validated.valid is True

    @pytest.mark.asyncio
    async def test_research_outcome_invalid_when_page_has_no_source_ids(self) -> None:
        persona = _research_persona()
        parsed = _parse_outcome_block_for_persona(
            ("---outcome---\nsummary: done\npage_path: research/example.md\n---end---\n"),
            persona,
        )
        assert parsed is not None

        page = MimirPage(
            meta=MimirPageMeta(
                path="research/example.md",
                title="Example",
                summary="summary",
                category="research",
                updated_at=datetime.now(UTC),
                source_ids=[],
                produced_by_thread=True,
            ),
            content="# Example\n\nBody.",
        )
        validated = await _validate_mimir_outcome_for_persona(
            parsed,
            persona_config=persona,
            mimir=_FakeMimir(page=page),
        )

        assert validated.valid is False
        assert any("no source_ids provenance" in error for error in validated.errors)

    @pytest.mark.asyncio
    async def test_research_outcome_invalid_when_sources_are_self_ingested_page(self) -> None:
        persona = _research_persona()
        parsed = _parse_outcome_block_for_persona(
            ("---outcome---\nsummary: done\npage_path: research/example.md\n---end---\n"),
            persona,
        )
        assert parsed is not None

        content = (
            "---\n"
            "type: research\n"
            "confidence: high\n"
            "produced_by_thread: true\n"
            "source_ids: [src_self]\n"
            "---\n\n"
            "# Example\n\n"
            "## Compiled Truth\n\n"
            "Body."
        )
        page = MimirPage(
            meta=MimirPageMeta(
                path="research/example.md",
                title="Example",
                summary="summary",
                category="research",
                updated_at=datetime.now(UTC),
                source_ids=["src_self"],
                produced_by_thread=True,
            ),
            content=content,
        )
        source = MimirSource(
            source_id="src_self",
            title="Example",
            content=content,
            source_type="document",
            content_hash=compute_content_hash(content),
            ingested_at=datetime.now(UTC),
        )

        validated = await _validate_mimir_outcome_for_persona(
            parsed,
            persona_config=persona,
            mimir=_FakeMimir(page=page, sources={"src_self": source}),
        )

        assert validated.valid is False
        assert any("self-ingested page content" in error for error in validated.errors)

    @pytest.mark.asyncio
    async def test_research_alias_outcome_still_validates_permanent_page(self) -> None:
        persona = _research_alias_persona()
        parsed = _parse_outcome_block_for_persona(
            (
                "---outcome---\n"
                "verdict: research_published\n"
                "summary: done\n"
                "page_path: research/example.md\n"
                "---end---\n"
            ),
            persona,
        )
        assert parsed is not None

        page = MimirPage(
            meta=MimirPageMeta(
                path="research/example.md",
                title="Example",
                summary="summary",
                category="research",
                updated_at=datetime.now(UTC),
                source_ids=[],
                produced_by_thread=True,
            ),
            content="# Example\n\nBody.",
        )
        validated = await _validate_mimir_outcome_for_persona(
            parsed,
            persona_config=persona,
            mimir=_FakeMimir(page=page),
        )

        assert validated.valid is False
        assert any("no source_ids provenance" in error for error in validated.errors)


# ---------------------------------------------------------------------------
# Unit tests: Episode model fields
# ---------------------------------------------------------------------------


class TestEpisodeStructuredOutcomeFields:
    def test_defaults_to_none_and_false(self) -> None:
        from datetime import UTC, datetime

        ep = Episode(
            episode_id="e1",
            session_id="s1",
            timestamp=datetime.now(UTC),
            summary="test",
            task_description="task",
            tools_used=[],
            outcome=Outcome.SUCCESS,
            tags=[],
        )
        assert ep.structured_outcome is None
        assert ep.outcome_valid is False

    def test_can_set_structured_outcome(self) -> None:
        from datetime import UTC, datetime

        ep = Episode(
            episode_id="e2",
            session_id="s2",
            timestamp=datetime.now(UTC),
            summary="test",
            task_description="task",
            tools_used=[],
            outcome=Outcome.SUCCESS,
            tags=[],
        )
        ep.structured_outcome = {"verdict": "pass", "summary": "ok"}
        ep.outcome_valid = True
        assert ep.structured_outcome["verdict"] == "pass"
        assert ep.outcome_valid is True


# ---------------------------------------------------------------------------
# Unit tests: TurnResult.episode field
# ---------------------------------------------------------------------------


class TestTurnResultEpisodeField:
    def test_episode_defaults_to_none(self) -> None:
        result = TurnResult(
            response="hello",
            tool_calls=[],
            tool_results=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
        assert result.episode is None


# ---------------------------------------------------------------------------
# Integration tests: agent with reviewer persona
# ---------------------------------------------------------------------------


_REVIEWER_RESPONSE_WITH_OUTCOME = (
    "I reviewed the diff thoroughly.\n\n"
    "The code looks clean with no blocking issues.\n\n"
    "---outcome---\n"
    "verdict: pass\n"
    "findings_count: 1\n"
    "critical_count: 0\n"
    "summary: Minor style issue only\n"
    "---end---\n"
)

_REVIEWER_RESPONSE_NO_OUTCOME = "I reviewed the diff thoroughly. The code looks clean."


class TestAgentOutcomeCapture:
    @pytest.mark.asyncio
    async def test_outcome_stored_on_episode_when_block_found(self) -> None:
        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(llm, persona_config=persona, persona_name="reviewer")

        result = await agent.run_turn("Review the diff")

        assert result.episode is not None
        assert result.episode.structured_outcome is not None
        assert result.episode.structured_outcome["verdict"] == "pass"
        assert result.episode.outcome_valid is True

    @pytest.mark.asyncio
    async def test_no_outcome_when_persona_has_no_schema(self) -> None:
        persona = PersonaConfig(name="minimal")
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(llm, persona_config=persona)

        result = await agent.run_turn("Do something")

        assert result.episode is not None
        assert result.episode.structured_outcome is None
        assert result.episode.outcome_valid is False

    @pytest.mark.asyncio
    async def test_no_outcome_when_no_persona_config(self) -> None:
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(llm)

        result = await agent.run_turn("Do something")

        assert result.episode is not None
        assert result.episode.structured_outcome is None

    @pytest.mark.asyncio
    async def test_episode_is_set_even_without_memory(self) -> None:
        """Episode is always set on TurnResult so callers can inspect outcome."""
        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(llm, persona_config=persona)

        result = await agent.run_turn("Review")

        assert result.episode is not None

    @pytest.mark.asyncio
    async def test_no_outcome_when_response_lacks_block(self) -> None:
        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_NO_OUTCOME)
        agent, _ = _make_agent(llm, persona_config=persona)

        result = await agent.run_turn("Review")

        assert result.episode is not None
        assert result.episode.structured_outcome is None


# ---------------------------------------------------------------------------
# Integration tests: Sleipnir event emission
# ---------------------------------------------------------------------------


class TestAgentSleipnirOutcomeEvent:
    @pytest.mark.asyncio
    async def test_session_ended_emitted_with_structured_outcome(self) -> None:
        events: list[SleipnirEvent] = []
        bus = InProcessBus()
        await bus.subscribe(["ravn.session.*"], lambda e: events.append(e))

        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(
            llm,
            persona_config=persona,
            sleipnir_publisher=bus,
            persona_name="reviewer",
        )

        await agent.run_turn("Review the diff in this repo")
        await bus.flush()

        ended = [e for e in events if e.event_type == "ravn.session.ended"]
        assert len(ended) == 1
        payload = ended[0].payload
        assert "structured_outcome" in payload
        assert payload["structured_outcome"]["verdict"] == "pass"
        assert payload["outcome_valid"] is True
        assert payload["outcome_event_type"] == "review.completed"

    @pytest.mark.asyncio
    async def test_session_started_emitted_on_first_turn(self) -> None:
        events: list[SleipnirEvent] = []
        bus = InProcessBus()
        await bus.subscribe(["ravn.session.*"], lambda e: events.append(e))

        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(
            llm,
            persona_config=persona,
            sleipnir_publisher=bus,
            persona_name="reviewer",
        )

        await agent.run_turn("Review the diff in this repo")
        await bus.flush()

        started = [e for e in events if e.event_type == "ravn.session.started"]
        assert len(started) == 1
        assert started[0].payload["persona"] == "reviewer"

    @pytest.mark.asyncio
    async def test_session_ended_not_emitted_when_no_outcome_block(self) -> None:
        events: list[SleipnirEvent] = []
        bus = InProcessBus()
        await bus.subscribe(["ravn.session.*"], lambda e: events.append(e))

        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_NO_OUTCOME)
        agent, _ = _make_agent(
            llm,
            persona_config=persona,
            sleipnir_publisher=bus,
            persona_name="reviewer",
        )

        await agent.run_turn("Review")
        await bus.flush()

        ended = [e for e in events if e.event_type == "ravn.session.ended"]
        assert len(ended) == 0

    @pytest.mark.asyncio
    async def test_emit_session_ended_is_noop_after_outcome_emitted(self) -> None:
        """emit_session_ended() must not double-emit when outcome block already triggered it."""
        events: list[SleipnirEvent] = []
        bus = InProcessBus()
        await bus.subscribe(["ravn.session.*"], lambda e: events.append(e))

        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(
            llm,
            persona_config=persona,
            sleipnir_publisher=bus,
            persona_name="reviewer",
        )

        await agent.run_turn("Review")
        await agent.emit_session_ended("success")  # should be ignored
        await bus.flush()

        ended = [e for e in events if e.event_type == "ravn.session.ended"]
        assert len(ended) == 1  # only one, from outcome capture

    @pytest.mark.asyncio
    async def test_no_event_emitted_when_no_publisher(self) -> None:
        """Agent works fine without a Sleipnir publisher — no errors."""
        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(llm, persona_config=persona)

        # Should not raise
        result = await agent.run_turn("Review")
        assert result.episode is not None
        assert result.episode.structured_outcome is not None


# ---------------------------------------------------------------------------
# E2E test matching the delivery spec
# ---------------------------------------------------------------------------


class TestRavnOutcomeE2E:
    @pytest.mark.asyncio
    async def test_ravn_outcome_emitted(self) -> None:
        """Run agent with reviewer persona → verify outcome extracted and event emitted."""
        events: list[SleipnirEvent] = []
        bus = InProcessBus()
        await bus.subscribe(["ravn.session.*"], lambda e: events.append(e))

        persona = _reviewer_persona()
        llm = _make_llm_with_response(_REVIEWER_RESPONSE_WITH_OUTCOME)
        agent, _ = _make_agent(
            llm,
            persona_config=persona,
            sleipnir_publisher=bus,
            persona_name="reviewer",
        )

        result = await agent.run_turn("Review the diff in this repo")

        # Verify outcome parsed
        assert result.episode is not None
        assert result.episode.structured_outcome is not None
        assert result.episode.structured_outcome["verdict"] in ("pass", "fail", "needs_changes")

        # Verify event emitted
        await bus.flush()
        ended_events = [e for e in events if e.event_type == "ravn.session.ended"]
        assert len(ended_events) == 1
        assert "structured_outcome" in ended_events[0].payload
        assert ended_events[0].payload["outcome_event_type"] == "review.completed"
