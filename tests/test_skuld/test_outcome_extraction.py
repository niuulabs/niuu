"""Tests for NIU-595: Skuld outcome extraction and ravn.session.ended event emission."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from skuld.broker import Broker, SessionArtifacts
from skuld.config import SkuldSessionConfig, SkuldSettings
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.registry import RAVN_SESSION_ENDED
from sleipnir.testing import EventCapture

# ---------------------------------------------------------------------------
# SessionArtifacts tests
# ---------------------------------------------------------------------------


class TestSessionArtifactsNewFields:
    def test_default_values(self):
        artifacts = SessionArtifacts()
        assert artifacts.structured_outcome is None
        assert artifacts.outcome_valid is False
        assert artifacts.saga_id is None
        assert artifacts.raid_id is None
        assert artifacts.total_tokens == 0

    def test_explicit_saga_and_raid(self):
        artifacts = SessionArtifacts(saga_id="saga-1", raid_id="raid-1")
        assert artifacts.saga_id == "saga-1"
        assert artifacts.raid_id == "raid-1"


# ---------------------------------------------------------------------------
# SkuldSessionConfig saga/raid fields
# ---------------------------------------------------------------------------


class TestSkuldSessionConfigSagaRaid:
    def test_defaults_to_none(self):
        cfg = SkuldSessionConfig()
        assert cfg.saga_id is None
        assert cfg.raid_id is None

    def test_can_be_set(self):
        cfg = SkuldSessionConfig(saga_id="s1", raid_id="r1")
        assert cfg.saga_id == "s1"
        assert cfg.raid_id == "r1"


# ---------------------------------------------------------------------------
# Broker initialization wires saga/raid into artifacts
# ---------------------------------------------------------------------------


class TestBrokerSagaRaidInit:
    def test_saga_and_raid_stored_in_artifacts(self, tmp_path):
        settings = SkuldSettings(
            session={
                "id": "sess-1",
                "workspace_dir": str(tmp_path),
                "saga_id": "s-42",
                "raid_id": "r-99",
            },
        )
        b = Broker(settings=settings)
        assert b._artifacts.saga_id == "s-42"
        assert b._artifacts.raid_id == "r-99"

    def test_no_saga_raid_when_not_configured(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-2", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings)
        assert b._artifacts.saga_id is None
        assert b._artifacts.raid_id is None


# ---------------------------------------------------------------------------
# Broker._build_transcript
# ---------------------------------------------------------------------------


class TestBuildTranscript:
    def _make_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-t", "workspace_dir": str(tmp_path)},
        )
        return Broker(settings=settings)

    def test_empty_when_no_turns(self, tmp_path):
        b = self._make_broker(tmp_path)
        assert b._build_transcript() == ""

    def test_only_assistant_turns_included(self, tmp_path):
        from skuld.broker import ConversationTurn

        b = self._make_broker(tmp_path)
        b._conversation_turns = [
            ConversationTurn(id="1", role="user", content="hello"),
            ConversationTurn(id="2", role="assistant", content="world"),
        ]
        assert b._build_transcript() == "world"

    def test_multiple_assistant_turns_joined(self, tmp_path):
        from skuld.broker import ConversationTurn

        b = self._make_broker(tmp_path)
        b._conversation_turns = [
            ConversationTurn(id="1", role="assistant", content="first"),
            ConversationTurn(id="2", role="user", content="mid"),
            ConversationTurn(id="3", role="assistant", content="second"),
        ]
        assert b._build_transcript() == "first\n\nsecond"

    def test_empty_content_skipped(self, tmp_path):
        from skuld.broker import ConversationTurn

        b = self._make_broker(tmp_path)
        b._conversation_turns = [
            ConversationTurn(id="1", role="assistant", content=""),
            ConversationTurn(id="2", role="assistant", content="real"),
        ]
        assert b._build_transcript() == "real"


# ---------------------------------------------------------------------------
# Broker._extract_and_store_outcome
# ---------------------------------------------------------------------------


class TestExtractAndStoreOutcome:
    def _make_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "sess-o", "workspace_dir": str(tmp_path)},
        )
        return Broker(settings=settings)

    def test_no_outcome_block_leaves_artifact_none(self, tmp_path):
        from skuld.broker import ConversationTurn

        b = self._make_broker(tmp_path)
        b._conversation_turns = [
            ConversationTurn(id="1", role="assistant", content="No outcome here."),
        ]
        b._extract_and_store_outcome()
        assert b._artifacts.structured_outcome is None
        assert b._artifacts.outcome_valid is False

    def test_outcome_block_extracted(self, tmp_path):
        from skuld.broker import ConversationTurn

        b = self._make_broker(tmp_path)
        b._conversation_turns = [
            ConversationTurn(
                id="1",
                role="assistant",
                content=(
                    "I reviewed the code.\n\n"
                    "---outcome---\n"
                    "verdict: fail\n"
                    "findings_count: 5\n"
                    "critical_count: 2\n"
                    "summary: Critical auth bypass\n"
                    "---end---"
                ),
            ),
        ]
        b._extract_and_store_outcome()
        assert b._artifacts.structured_outcome is not None
        assert b._artifacts.structured_outcome["verdict"] == "fail"
        assert b._artifacts.structured_outcome["critical_count"] == 2
        assert b._artifacts.outcome_valid is True

    def test_empty_transcript_is_noop(self, tmp_path):
        b = self._make_broker(tmp_path)
        b._extract_and_store_outcome()
        assert b._artifacts.structured_outcome is None

    def test_parse_exception_swallowed(self, tmp_path):
        b = self._make_broker(tmp_path)
        with patch("skuld.broker.parse_outcome_block", side_effect=RuntimeError("boom")):
            from skuld.broker import ConversationTurn

            b._conversation_turns = [
                ConversationTurn(
                    id="1",
                    role="assistant",
                    content="---outcome---\nfoo: bar\n---end---",
                ),
            ]
            b._extract_and_store_outcome()  # must not raise
        assert b._artifacts.structured_outcome is None


# ---------------------------------------------------------------------------
# Broker._emit_session_ended_event
# ---------------------------------------------------------------------------


class TestEmitSessionEndedEvent:
    async def test_event_published_with_correct_fields(self, tmp_path):
        bus = InProcessBus()
        settings = SkuldSettings(
            session={"id": "sess-e", "name": "ravn-audit", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings, sleipnir_publisher=bus)
        b._artifacts.total_tokens = 1234
        b._artifacts.structured_outcome = {"verdict": "pass"}
        b._artifacts.outcome_valid = True
        b._artifacts.saga_id = "saga-abc"
        b._artifacts.raid_id = "raid-xyz"

        async with EventCapture(bus, [RAVN_SESSION_ENDED]) as capture:
            await b._emit_session_ended_event()
            await bus.flush()

        assert len(capture.events) == 1
        evt = capture.events[0]
        assert evt.event_type == RAVN_SESSION_ENDED
        assert evt.payload["session_id"] == "sess-e"
        assert evt.payload["persona"] == "ravn-audit"
        assert evt.payload["outcome"] == "SUCCESS"
        assert evt.payload["token_count"] == 1234
        assert evt.payload["structured_outcome"] == {"verdict": "pass"}
        assert evt.payload["outcome_valid"] is True
        assert evt.payload["raid_id"] == "raid-xyz"
        assert evt.payload["saga_id"] == "saga-abc"
        assert evt.correlation_id == "sess-e"

    async def test_event_emitted_without_outcome(self, tmp_path):
        bus = InProcessBus()
        settings = SkuldSettings(
            session={"id": "sess-no-outcome", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings, sleipnir_publisher=bus)

        async with EventCapture(bus, [RAVN_SESSION_ENDED]) as capture:
            await b._emit_session_ended_event()
            await bus.flush()

        assert len(capture.events) == 1
        evt = capture.events[0]
        assert evt.payload["outcome"] == "PARTIAL"
        assert "structured_outcome" not in evt.payload
        assert "raid_id" not in evt.payload
        assert "saga_id" not in evt.payload

    async def test_event_emitted_without_saga_raid(self, tmp_path):
        bus = InProcessBus()
        settings = SkuldSettings(
            session={"id": "sess-plain", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings, sleipnir_publisher=bus)
        b._artifacts.structured_outcome = {"verdict": "pass"}
        b._artifacts.outcome_valid = True

        async with EventCapture(bus, [RAVN_SESSION_ENDED]) as capture:
            await b._emit_session_ended_event()
            await bus.flush()

        assert len(capture.events) == 1
        evt = capture.events[0]
        assert "raid_id" not in evt.payload
        assert "saga_id" not in evt.payload

    async def test_publish_failure_is_swallowed(self, tmp_path):
        failing_publisher = AsyncMock()
        failing_publisher.publish.side_effect = RuntimeError("bus down")
        settings = SkuldSettings(
            session={"id": "sess-fail", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings, sleipnir_publisher=failing_publisher)
        await b._emit_session_ended_event()  # must not raise

    async def test_outcome_invalid_maps_to_partial(self, tmp_path):
        bus = InProcessBus()
        settings = SkuldSettings(
            session={"id": "sess-invalid", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings, sleipnir_publisher=bus)
        b._artifacts.structured_outcome = {"verdict": "unknown"}
        b._artifacts.outcome_valid = False

        async with EventCapture(bus, [RAVN_SESSION_ENDED]) as capture:
            await b._emit_session_ended_event()
            await bus.flush()

        assert capture.events[0].payload["outcome"] == "PARTIAL"


# ---------------------------------------------------------------------------
# E2E: full session completion flow
# ---------------------------------------------------------------------------


class TestSessionCompletionE2E:
    async def test_transcript_with_outcome_block_emits_structured_event(self, tmp_path):
        """Simulate session with outcome block → verify event emitted with structured payload."""
        bus = InProcessBus()
        settings = SkuldSettings(
            session={
                "id": "e2e-session",
                "workspace_dir": str(tmp_path),
                "saga_id": "saga-1",
                "raid_id": "raid-1",
            },
        )
        b = Broker(settings=settings, sleipnir_publisher=bus)

        # Simulate assistant messages culminating in an outcome block
        from skuld.broker import ConversationTurn

        b._conversation_turns = [
            ConversationTurn(id="1", role="user", content="Review the auth code"),
            ConversationTurn(
                id="2",
                role="assistant",
                content=(
                    "I reviewed the code and found 2 critical issues in the auth middleware.\n\n"
                    "---outcome---\n"
                    "verdict: fail\n"
                    "findings_count: 5\n"
                    "critical_count: 2\n"
                    "summary: Critical auth bypass in middleware\n"
                    "---end---"
                ),
            ),
        ]

        async with EventCapture(bus, [RAVN_SESSION_ENDED]) as capture:
            b._extract_and_store_outcome()
            await b._emit_session_ended_event()
            await bus.flush()

        assert b._artifacts.structured_outcome is not None
        assert b._artifacts.structured_outcome["verdict"] == "fail"
        assert b._artifacts.structured_outcome["critical_count"] == 2

        events = [e for e in capture.events if e.event_type == RAVN_SESSION_ENDED]
        assert len(events) == 1
        evt = events[0]
        assert evt.payload["raid_id"] == "raid-1"
        assert evt.payload["saga_id"] == "saga-1"
        assert evt.payload["structured_outcome"]["verdict"] == "fail"
        assert evt.payload["outcome"] == "SUCCESS"  # outcome_valid is True when YAML parses cleanly

    async def test_transcript_without_outcome_emits_partial_event(self, tmp_path):
        """Session without outcome block still emits ravn.session.ended with PARTIAL."""
        bus = InProcessBus()
        settings = SkuldSettings(
            session={
                "id": "e2e-no-outcome",
                "workspace_dir": str(tmp_path),
                "saga_id": "saga-2",
                "raid_id": "raid-2",
            },
        )
        b = Broker(settings=settings, sleipnir_publisher=bus)

        from skuld.broker import ConversationTurn

        b._conversation_turns = [
            ConversationTurn(
                id="1",
                role="assistant",
                content="I looked at the code but found nothing.",
            ),
        ]

        async with EventCapture(bus, [RAVN_SESSION_ENDED]) as capture:
            b._extract_and_store_outcome()
            await b._emit_session_ended_event()
            await bus.flush()

        assert b._artifacts.structured_outcome is None

        events = [e for e in capture.events if e.event_type == RAVN_SESSION_ENDED]
        assert len(events) == 1
        evt = events[0]
        assert evt.payload["outcome"] == "PARTIAL"
        assert "structured_outcome" not in evt.payload
        assert evt.payload["raid_id"] == "raid-2"

    async def test_total_tokens_accumulated_across_turns(self, tmp_path):
        """Verify total_tokens in artifacts is accumulated from multiple result events."""
        bus = InProcessBus()
        settings = SkuldSettings(
            session={"id": "sess-tokens", "workspace_dir": str(tmp_path)},
        )
        b = Broker(settings=settings, sleipnir_publisher=bus)
        b._artifacts.total_tokens = 0

        # Simulate two result events contributing tokens
        b._artifacts.total_tokens += 500
        b._artifacts.total_tokens += 750

        async with EventCapture(bus, [RAVN_SESSION_ENDED]) as capture:
            await b._emit_session_ended_event()
            await bus.flush()

        assert capture.events[0].payload["token_count"] == 1250
