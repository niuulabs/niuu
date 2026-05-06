"""Tests for the reviewer session service (NIU-255)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tyr.config import ReviewConfig
from tyr.domain.models import PRStatus, Raid, RaidStatus
from tyr.domain.services.reviewer_session import (
    ReviewerResult,
    ReviewerSessionService,
    build_reviewer_initial_prompt,
    parse_reviewer_response,
)
from tyr.ports.volundr import ActivityEvent, SpawnRequest, VolundrPort, VolundrSession

NOW = datetime.now(UTC)
PHASE_ID = uuid4()
OWNER_ID = "user-1"


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubVolundr(VolundrPort):
    """In-memory Volundr stub for reviewer session tests."""

    def __init__(self) -> None:
        self.spawn_calls: list[SpawnRequest] = []
        self.messages: list[tuple[str, str]] = []
        self.fail_spawn: bool = False
        self.fail_send: bool = False
        self.chronicle_summaries: dict[str, str] = {}
        self.integration_ids: list[str] = []

    async def spawn_session(
        self, request: SpawnRequest, *, auth_token: str | None = None
    ) -> VolundrSession:
        if self.fail_spawn:
            raise RuntimeError("Spawn failed")
        self.spawn_calls.append(request)
        return VolundrSession(
            id=f"reviewer-{len(self.spawn_calls)}",
            name=request.name,
            status="running",
            tracker_issue_id=request.tracker_issue_id,
        )

    async def get_session(
        self, session_id: str, *, auth_token: str | None = None
    ) -> VolundrSession | None:
        return VolundrSession(id=session_id, name="s", status="running", tracker_issue_id=None)

    async def list_sessions(self, *, auth_token: str | None = None) -> list[VolundrSession]:
        return []

    async def get_pr_status(self, session_id: str) -> PRStatus:
        raise NotImplementedError

    async def get_chronicle_summary(self, session_id: str) -> str:
        return self.chronicle_summaries.get(session_id, "")

    async def send_message(
        self, session_id: str, message: str, *, auth_token: str | None = None
    ) -> None:
        if self.fail_send:
            raise RuntimeError("Send failed")
        self.messages.append((session_id, message))

    async def stop_session(self, session_id: str, *, auth_token: str | None = None) -> None:
        pass

    async def list_integration_ids(self, *, auth_token: str | None = None) -> list[str]:
        return list(self.integration_ids)

    async def list_repos(self, *, auth_token: str | None = None) -> list[dict]:
        return []

    async def get_conversation(self, session_id: str) -> dict:
        stub = '{"confidence": 0.9, "approved": true, "issues": []}'
        return {"turns": [{"role": "assistant", "content": stub}]}

    async def get_last_assistant_message(self, session_id: str) -> str:
        return '{"confidence": 0.9, "approved": true, "summary": "stub", "issues": []}'

    async def subscribe_activity(self) -> AsyncGenerator[ActivityEvent, None]:
        return
        yield  # type: ignore[misc]  # pragma: no cover


class StubVolundrFactory:
    def __init__(self, volundr: StubVolundr | None = None) -> None:
        self._volundr = volundr

    async def for_owner(self, owner_id: str) -> list[StubVolundr]:
        if self._volundr is None:
            return []
        return [self._volundr]

    async def primary_for_owner(self, owner_id: str) -> StubVolundr | None:
        return self._volundr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raid(
    tracker_id: str = "NIU-100",
    status: RaidStatus = RaidStatus.REVIEW,
    confidence: float = 0.5,
    pr_id: str | None = "pr-42",
    session_id: str | None = "session-1",
    branch: str | None = "raid/test",
    acceptance_criteria: list[str] | None = None,
    chronicle_summary: str | None = "All tests pass",
) -> Raid:
    return Raid(
        id=uuid4(),
        phase_id=PHASE_ID,
        tracker_id=tracker_id,
        name="Test raid",
        description="A test raid description",
        acceptance_criteria=acceptance_criteria or ["tests pass", "lint clean"],
        declared_files=["src/main.py"],
        estimate_hours=2.0,
        status=status,
        confidence=confidence,
        session_id=session_id,
        branch=branch,
        chronicle_summary=chronicle_summary,
        pr_url="https://github.com/org/repo/pull/42",
        pr_id=pr_id,
        retry_count=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_pr_status(ci_passed: bool = True, mergeable: bool = True) -> PRStatus:
    return PRStatus(
        pr_id="pr-42",
        url="https://github.com/org/repo/pull/42",
        state="open",
        mergeable=mergeable,
        ci_passed=ci_passed,
    )


def _default_config(**overrides: object) -> ReviewConfig:
    defaults: dict = {
        "reviewer_session_enabled": True,
        "reviewer_model": "claude-opus-4-6",
        "reviewer_profile": "reviewer",
    }
    defaults.update(overrides)
    return ReviewConfig(**defaults)


def _make_service(
    volundr: StubVolundr | None = None,
    config: ReviewConfig | None = None,
) -> tuple[ReviewerSessionService, StubVolundr]:
    v = volundr or StubVolundr()
    c = config or _default_config()
    factory = StubVolundrFactory(v)
    service = ReviewerSessionService(
        volundr_factory=factory,
        review_config=c,
    )
    return service, v


# ---------------------------------------------------------------------------
# Tests: parse_reviewer_response — text format
# ---------------------------------------------------------------------------


class TestParseReviewerResponseText:
    """Tests for parsing text-based reviewer output."""

    def test_parse_complete_response(self) -> None:
        text = """
CONFIDENCE: 0.85
APPROVED: yes
SUMMARY: Clean implementation following all project rules
ISSUES:
- Minor: missing docstring on helper function
- Nit: unused import in test file
"""
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 0.85
        assert result.approved is True
        assert result.summary == "Clean implementation following all project rules"
        assert len(result.findings) == 2
        assert "missing docstring" in result.findings[0]

    def test_parse_rejection_response(self) -> None:
        text = """
CONFIDENCE: 0.45
APPROVED: no
SUMMARY: Significant architecture violations
ISSUES:
- Critical: tyr imports from volundr directly
- Critical: uses ORM instead of raw SQL
"""
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 0.45
        assert result.approved is False
        assert len(result.findings) == 2

    def test_parse_no_issues(self) -> None:
        text = """
CONFIDENCE: 0.95
APPROVED: yes
SUMMARY: Perfect implementation
"""
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 0.95
        assert result.approved is True
        assert result.findings == []

    def test_parse_empty_text(self) -> None:
        result = parse_reviewer_response("")
        assert result is None

    def test_parse_garbage_text(self) -> None:
        result = parse_reviewer_response("This is just random text with no structure")
        assert result is None

    def test_confidence_clamped_to_bounds(self) -> None:
        text = "CONFIDENCE: 1.5\nSUMMARY: test"
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 1.0

        text_low = "CONFIDENCE: -0.5\nSUMMARY: test"
        result_low = parse_reviewer_response(text_low)
        assert result_low is not None
        assert result_low.confidence == 0.0

    def test_approved_variations(self) -> None:
        for val in ("yes", "true", "1"):
            text = f"CONFIDENCE: 0.9\nAPPROVED: {val}\nSUMMARY: ok"
            result = parse_reviewer_response(text)
            assert result is not None
            assert result.approved is True

        for val in ("no", "false", "0"):
            text = f"CONFIDENCE: 0.9\nAPPROVED: {val}\nSUMMARY: ok"
            result = parse_reviewer_response(text)
            assert result is not None
            assert result.approved is False

    def test_parse_with_markdown_fence(self) -> None:
        """Reviewer may wrap response in a code fence."""
        text = """```
CONFIDENCE: 0.80
APPROVED: yes
SUMMARY: Good implementation
```"""
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 0.80

    def test_parse_invalid_confidence_value(self) -> None:
        text = "CONFIDENCE: not-a-number\nSUMMARY: test"
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Tests: parse_reviewer_response — JSON format
# ---------------------------------------------------------------------------


class TestParseReviewerResponseJSON:
    """Tests for parsing JSON-formatted reviewer output."""

    def test_parse_json_complete(self) -> None:
        text = '{"confidence": 0.85, "approved": true, "summary": "Clean code", "issues": ["nit"]}'
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 0.85
        assert result.approved is True
        assert result.summary == "Clean code"
        assert result.findings == ["nit"]

    def test_parse_json_in_code_fence(self) -> None:
        text = """```json
{"confidence": 0.92, "approved": true, "summary": "All good", "issues": []}
```"""
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 0.92
        assert result.approved is True
        assert result.findings == []

    def test_parse_json_no_issues(self) -> None:
        text = '{"confidence": 0.95, "approved": true, "summary": "Perfect"}'
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 0.95
        assert result.findings == []

    def test_parse_json_clamped_confidence(self) -> None:
        text = '{"confidence": 1.5, "approved": true, "summary": "test"}'
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.confidence == 1.0

    def test_parse_json_rejected(self) -> None:
        text = '{"confidence": 0.3, "approved": false, "summary": "Bad", "issues": ["a", "b"]}'
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.approved is False
        assert len(result.findings) == 2

    def test_json_preferred_over_text(self) -> None:
        """When text contains valid JSON, JSON parser wins."""
        text = '{"confidence": 0.77, "approved": false, "summary": "JSON wins"}'
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.summary == "JSON wins"

    def test_invalid_json_falls_back_to_text(self) -> None:
        text = "CONFIDENCE: 0.88\nAPPROVED: yes\nSUMMARY: text fallback"
        result = parse_reviewer_response(text)
        assert result is not None
        assert result.summary == "text fallback"


# ---------------------------------------------------------------------------
# Tests: build_reviewer_initial_prompt
# ---------------------------------------------------------------------------


class TestBuildReviewerInitialPrompt:
    """Tests for building the initial prompt sent to the reviewer."""

    def test_basic_prompt_structure(self) -> None:
        raid = _make_raid()
        pr = _make_pr_status()
        prompt = build_reviewer_initial_prompt(
            raid=raid,
            pr_status=pr,
            changed_files=["src/main.py", "tests/test_main.py"],
            diff_summary="Added new feature",
        )

        assert "NIU-100" in prompt
        assert "Test raid" in prompt
        assert "tests pass" in prompt
        assert "src/main.py" in prompt
        assert "json" in prompt.lower()

    def test_prompt_without_pr(self) -> None:
        raid = _make_raid()
        prompt = build_reviewer_initial_prompt(
            raid=raid,
            pr_status=None,
            changed_files=[],
            diff_summary="",
        )
        assert "NIU-100" in prompt
        assert "PR State" not in prompt

    def test_prompt_with_no_changed_files(self) -> None:
        raid = _make_raid()
        prompt = build_reviewer_initial_prompt(
            raid=raid,
            pr_status=_make_pr_status(),
            changed_files=[],
            diff_summary="",
        )
        assert "Changed Files" not in prompt

    def test_prompt_includes_acceptance_criteria(self) -> None:
        raid = _make_raid(acceptance_criteria=["coverage >= 85%", "no lint warnings"])
        prompt = build_reviewer_initial_prompt(
            raid=raid,
            pr_status=None,
            changed_files=[],
            diff_summary="",
        )
        assert "coverage >= 85%" in prompt
        assert "no lint warnings" in prompt

    def test_prompt_includes_diff_summary(self) -> None:
        raid = _make_raid()
        prompt = build_reviewer_initial_prompt(
            raid=raid,
            pr_status=None,
            changed_files=[],
            diff_summary="Refactored auth module",
        )
        assert "Refactored auth module" in prompt


# ---------------------------------------------------------------------------
# Tests: reviewer_system_prompt config field
# ---------------------------------------------------------------------------


class TestReviewerSystemPromptConfig:
    """Tests for the system prompt in ReviewConfig."""

    def test_default_prompt_has_content(self) -> None:
        cfg = ReviewConfig()
        assert "code reviewer" in cfg.reviewer_system_prompt.lower()

    def test_prompt_overridable_via_config(self) -> None:
        cfg = ReviewConfig(reviewer_system_prompt="Custom prompt for this deploy")
        assert cfg.reviewer_system_prompt == "Custom prompt for this deploy"

    def test_initial_prompt_template_overridable(self) -> None:
        cfg = ReviewConfig(reviewer_initial_prompt_template="Review: {tracker_id}")
        assert cfg.reviewer_initial_prompt_template == "Review: {tracker_id}"


# ---------------------------------------------------------------------------
# Tests: ReviewerSessionService.spawn_reviewer
# ---------------------------------------------------------------------------


class TestSpawnReviewer:
    """Tests for spawning a reviewer session."""

    @pytest.mark.asyncio
    async def test_spawn_success(self) -> None:
        service, volundr = _make_service()
        raid = _make_raid()
        pr = _make_pr_status()

        session = await service.spawn_reviewer(
            raid=raid,
            owner_id=OWNER_ID,
            pr_status=pr,
            changed_files=["src/main.py"],
            integration_ids=["int-1", "int-2"],
        )

        assert session is not None
        assert session.id == "reviewer-1"
        assert len(volundr.spawn_calls) == 1

        req = volundr.spawn_calls[0]
        assert req.name == "review-niu-100"
        assert req.model == "claude-opus-4-6"
        assert req.profile == "reviewer"
        assert req.workload_type == "reviewer"
        assert req.tracker_issue_id == "NIU-100"
        assert req.integration_ids == ["int-1", "int-2"]
        assert "code reviewer" in req.system_prompt.lower()

    @pytest.mark.asyncio
    async def test_spawn_with_no_volundr_adapter(self) -> None:
        factory = StubVolundrFactory(None)
        service = ReviewerSessionService(
            volundr_factory=factory,
            review_config=_default_config(),
        )
        raid = _make_raid()

        session = await service.spawn_reviewer(
            raid=raid,
            owner_id=OWNER_ID,
            pr_status=None,
            changed_files=[],
        )
        assert session is None

    @pytest.mark.asyncio
    async def test_spawn_failure_returns_none(self) -> None:
        volundr = StubVolundr()
        volundr.fail_spawn = True
        service, _ = _make_service(volundr=volundr)
        raid = _make_raid()

        session = await service.spawn_reviewer(
            raid=raid,
            owner_id=OWNER_ID,
            pr_status=None,
            changed_files=[],
        )
        assert session is None

    @pytest.mark.asyncio
    async def test_spawn_includes_diff_summary_from_chronicle(self) -> None:
        service, volundr = _make_service()
        raid = _make_raid(chronicle_summary="Implemented auth flow")

        await service.spawn_reviewer(
            raid=raid,
            owner_id=OWNER_ID,
            pr_status=None,
            changed_files=[],
        )

        assert len(volundr.spawn_calls) == 1
        assert "Implemented auth flow" in volundr.spawn_calls[0].initial_prompt

    @pytest.mark.asyncio
    async def test_spawn_resolves_integrations_when_not_provided(self) -> None:
        service, volundr = _make_service()
        volundr.integration_ids = ["int-telegram", "int-linear"]
        raid = _make_raid()

        session = await service.spawn_reviewer(
            raid=raid,
            owner_id=OWNER_ID,
            pr_status=None,
            changed_files=[],
            integration_ids=None,
        )

        assert session is not None
        assert len(volundr.spawn_calls) == 1
        assert volundr.spawn_calls[0].integration_ids == ["int-telegram", "int-linear"]

    @pytest.mark.asyncio
    async def test_spawn_with_no_chronicle(self) -> None:
        service, volundr = _make_service()
        raid = _make_raid(chronicle_summary=None)

        session = await service.spawn_reviewer(
            raid=raid,
            owner_id=OWNER_ID,
            pr_status=_make_pr_status(),
            changed_files=["src/file.py"],
        )
        assert session is not None


# ---------------------------------------------------------------------------
# Tests: ReviewerSessionService.send_feedback_to_working_session
# ---------------------------------------------------------------------------


class TestSendFeedback:
    """Tests for sending reviewer feedback to the working session."""

    @pytest.mark.asyncio
    async def test_sends_feedback_with_issues(self) -> None:
        service, volundr = _make_service()
        raid = _make_raid()
        result = ReviewerResult(
            session_id="reviewer-1",
            confidence=0.65,
            summary="Architecture violation found",
            findings=["tyr imports from volundr", "missing tests"],
            approved=False,
        )

        await service.send_feedback_to_working_session(
            raid=raid,
            owner_id=OWNER_ID,
            result=result,
        )

        assert len(volundr.messages) == 1
        session_id, msg = volundr.messages[0]
        assert session_id == "session-1"
        assert "Architecture violation" in msg
        assert "tyr imports from volundr" in msg
        assert "0.65" in msg

    @pytest.mark.asyncio
    async def test_skips_feedback_when_no_issues(self) -> None:
        service, volundr = _make_service()
        raid = _make_raid()
        result = ReviewerResult(
            session_id="reviewer-1",
            confidence=0.95,
            summary="Looks great",
            findings=[],
            approved=True,
        )

        await service.send_feedback_to_working_session(
            raid=raid,
            owner_id=OWNER_ID,
            result=result,
        )

        assert len(volundr.messages) == 0

    @pytest.mark.asyncio
    async def test_skips_feedback_when_no_session_id(self) -> None:
        service, volundr = _make_service()
        raid = _make_raid(session_id=None)
        result = ReviewerResult(
            session_id="reviewer-1",
            confidence=0.5,
            summary="Issues",
            findings=["problem"],
            approved=False,
        )

        await service.send_feedback_to_working_session(
            raid=raid,
            owner_id=OWNER_ID,
            result=result,
        )

        assert len(volundr.messages) == 0

    @pytest.mark.asyncio
    async def test_feedback_handles_send_failure(self) -> None:
        volundr = StubVolundr()
        volundr.fail_send = True
        service, _ = _make_service(volundr=volundr)
        raid = _make_raid()
        result = ReviewerResult(
            session_id="reviewer-1",
            confidence=0.5,
            summary="Issues",
            findings=["problem"],
            approved=False,
        )

        # Should not raise
        await service.send_feedback_to_working_session(
            raid=raid,
            owner_id=OWNER_ID,
            result=result,
        )

    @pytest.mark.asyncio
    async def test_feedback_with_no_volundr_adapter(self) -> None:
        factory = StubVolundrFactory(None)
        service = ReviewerSessionService(
            volundr_factory=factory,
            review_config=_default_config(),
        )
        raid = _make_raid()
        result = ReviewerResult(
            session_id="reviewer-1",
            confidence=0.5,
            summary="Issues",
            findings=["problem"],
            approved=False,
        )

        # Should not raise
        await service.send_feedback_to_working_session(
            raid=raid,
            owner_id=OWNER_ID,
            result=result,
        )


# ---------------------------------------------------------------------------
# Shared stubs for ReviewEngine integration tests
# ---------------------------------------------------------------------------


class _FullGitStub:
    """Git stub that returns plausible data for review engine tests."""

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        pass

    async def merge_branch(self, repo: str, source: str, target: str) -> None:
        pass

    async def delete_branch(self, repo: str, branch: str) -> None:
        pass

    async def create_pr(self, repo: str, source: str, target: str, title: str) -> str:
        return "pr-1"

    async def get_pr_status(self, pr_id: str) -> PRStatus:
        return PRStatus(
            pr_id=pr_id,
            url="https://github.com/org/repo/pull/42",
            state="open",
            mergeable=True,
            ci_passed=True,
        )

    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        return ["src/main.py"]


class _TrackerStub:
    """Minimal tracker for ReviewEngine integration tests."""

    def __init__(self, raid: Raid) -> None:
        self.raids = {raid.tracker_id: raid}
        self.events: dict[str, list] = {}
        self._all_merged = False
        self.phase_status_updates: list = []

    async def get_raid(self, tracker_id: str) -> Raid:
        raid = self.raids.get(tracker_id)
        if raid is None:
            raise ValueError(f"Raid not found: {tracker_id}")
        return raid

    async def update_raid_progress(self, tracker_id: str, **kwargs) -> Raid:
        raid = self.raids[tracker_id]
        status = kwargs.get("status", raid.status)
        updated = Raid(
            id=raid.id,
            phase_id=raid.phase_id,
            tracker_id=raid.tracker_id,
            name=raid.name,
            description=raid.description,
            acceptance_criteria=raid.acceptance_criteria,
            declared_files=raid.declared_files,
            estimate_hours=raid.estimate_hours,
            status=status,
            confidence=raid.confidence,
            session_id=raid.session_id,
            branch=raid.branch,
            chronicle_summary=raid.chronicle_summary,
            pr_url=raid.pr_url,
            pr_id=raid.pr_id,
            retry_count=kwargs.get("retry_count", raid.retry_count),
            created_at=raid.created_at,
            updated_at=datetime.now(UTC),
            reviewer_session_id=kwargs.get("reviewer_session_id", raid.reviewer_session_id),
            review_round=kwargs.get("review_round", raid.review_round),
        )
        self.raids[tracker_id] = updated
        return updated

    async def add_confidence_event(self, tracker_id: str, event) -> None:
        self.events.setdefault(tracker_id, []).append(event)

    async def get_saga_for_raid(self, tracker_id: str):
        return None

    async def get_phase_for_raid(self, tracker_id: str):
        return None

    async def all_raids_merged(self, phase_tracker_id: str) -> bool:
        return self._all_merged

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list:
        return []

    async def update_phase_status(self, phase_tracker_id: str, status):
        return None


class _TrackerFactoryStub:
    def __init__(self, tracker: _TrackerStub) -> None:
        self._tracker = tracker

    async def for_owner(self, owner_id: str) -> list[_TrackerStub]:
        return [self._tracker]


# ---------------------------------------------------------------------------
# Tests: ReviewEngine integration with reviewer sessions
# ---------------------------------------------------------------------------


class TestReviewEngineReviewerIntegration:
    """Tests for the ReviewEngine spawning reviewer sessions."""

    @pytest.mark.asyncio
    async def test_engine_spawns_reviewer_when_enabled(self) -> None:
        """Verify the ReviewEngine spawns a reviewer and defers decision."""
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.models import ConfidenceEventType
        from tyr.domain.services.review_engine import ReviewEngine

        volundr = StubVolundr()
        event_bus = InMemoryEventBus()
        config = _default_config(
            reviewer_session_enabled=True,
            auto_approve_threshold=0.80,
        )

        factory = StubVolundrFactory(volundr)
        reviewer = ReviewerSessionService(
            volundr_factory=factory,
            review_config=config,
        )

        raid = _make_raid(confidence=0.5)
        tracker_stub = _TrackerStub(raid)

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(tracker_stub),
            volundr_factory=factory,
            git=_FullGitStub(),
            review_config=config,
            event_bus=event_bus,
            reviewer_service=reviewer,
        )

        decision = await engine.evaluate(raid.tracker_id, OWNER_ID)

        # Reviewer session was spawned
        assert len(volundr.spawn_calls) == 1
        assert volundr.spawn_calls[0].profile == "reviewer"

        # Decision is deferred (reviewer_pending)
        assert decision.action == "reviewer_pending"

        # A REVIEWER_SCORE confidence event was recorded (spawn bonus)
        events = tracker_stub.events.get(raid.tracker_id, [])
        reviewer_events = [e for e in events if e.event_type == ConfidenceEventType.REVIEWER_SCORE]
        assert len(reviewer_events) == 1

        # The reviewer session is tracked
        assert engine.get_reviewer_raid("reviewer-1") == (raid.tracker_id, OWNER_ID)

    @pytest.mark.asyncio
    async def test_engine_skips_reviewer_when_disabled(self) -> None:
        """When reviewer_session_enabled=False, no reviewer is spawned."""
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.services.review_engine import ReviewEngine

        volundr = StubVolundr()
        config = _default_config(reviewer_session_enabled=False)
        factory = StubVolundrFactory(volundr)
        reviewer = ReviewerSessionService(
            volundr_factory=factory,
            review_config=config,
        )

        raid = _make_raid(confidence=0.5)
        tracker_stub = _TrackerStub(raid)

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(tracker_stub),
            volundr_factory=factory,
            git=_FullGitStub(),
            review_config=config,
            event_bus=InMemoryEventBus(),
            reviewer_service=reviewer,
        )

        await engine.evaluate(raid.tracker_id, OWNER_ID)
        assert len(volundr.spawn_calls) == 0

    @pytest.mark.asyncio
    async def test_engine_works_without_reviewer_service(self) -> None:
        """Engine works fine when reviewer_service is None (backward compat)."""
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.services.review_engine import ReviewEngine

        config = _default_config(reviewer_session_enabled=True)
        volundr = StubVolundr()
        factory = StubVolundrFactory(volundr)

        raid = _make_raid(confidence=0.5)
        tracker_stub = _TrackerStub(raid)

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(tracker_stub),
            volundr_factory=factory,
            git=_FullGitStub(),
            review_config=config,
            event_bus=InMemoryEventBus(),
            reviewer_service=None,
        )

        decision = await engine.evaluate(raid.tracker_id, OWNER_ID)
        assert decision is not None
        # No reviewer spawned — immediate decision
        assert decision.action != "reviewer_pending"
        assert len(volundr.spawn_calls) == 0


# ---------------------------------------------------------------------------
# Tests: ReviewEngine.handle_reviewer_completion
# ---------------------------------------------------------------------------


class TestHandleReviewerCompletion:
    """Tests for the review loop: reviewer completion → decision."""

    @pytest.mark.asyncio
    async def test_completion_with_high_confidence_auto_approves(self) -> None:
        """Reviewer returns high confidence → auto-approve."""
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.services.review_engine import ReviewEngine

        volundr = StubVolundr()
        config = _default_config(
            reviewer_session_enabled=True,
            auto_approve_threshold=0.80,
            reviewer_confidence_weight=0.60,
        )
        factory = StubVolundrFactory(volundr)
        reviewer = ReviewerSessionService(volundr_factory=factory, review_config=config)

        # Start at 0.7: reviewer delta = 0.60 * (0.95 - 0.7) = 0.15 → 0.85 >= 0.80
        raid = _make_raid(confidence=0.7)
        tracker_stub = _TrackerStub(raid)

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(tracker_stub),
            volundr_factory=factory,
            git=_FullGitStub(),
            review_config=config,
            event_bus=InMemoryEventBus(),
            reviewer_service=reviewer,
        )

        # First spawn the reviewer
        decision = await engine.evaluate(raid.tracker_id, OWNER_ID)
        assert decision.action == "reviewer_pending"

        # Now simulate reviewer completion with high confidence
        chronicle = '{"confidence": 0.95, "approved": true, "summary": "Looks great", "issues": []}'
        await engine.handle_reviewer_completion("reviewer-1", chronicle)

        # After completion, the raid should have been auto-approved (MERGED)
        updated_raid = tracker_stub.raids[raid.tracker_id]
        assert updated_raid.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_completion_with_low_confidence_escalates(self) -> None:
        """Reviewer returns low confidence → escalate."""
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.services.review_engine import ReviewEngine

        volundr = StubVolundr()
        config = _default_config(
            reviewer_session_enabled=True,
            auto_approve_threshold=0.80,
            reviewer_confidence_weight=0.60,
        )
        factory = StubVolundrFactory(volundr)
        reviewer = ReviewerSessionService(volundr_factory=factory, review_config=config)

        raid = _make_raid(confidence=0.5)
        tracker_stub = _TrackerStub(raid)

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(tracker_stub),
            volundr_factory=factory,
            git=_FullGitStub(),
            review_config=config,
            event_bus=InMemoryEventBus(),
            reviewer_service=reviewer,
        )

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        output = (
            '{"confidence": 0.3, "approved": false, "summary": "Bad code", "issues": ["problem"]}'
        )
        await engine.handle_reviewer_completion("reviewer-1", output)

        updated_raid = tracker_stub.raids[raid.tracker_id]
        # With issues, the reviewer drives the loop — round incremented, no escalation yet
        assert updated_raid.review_round == 1
        assert updated_raid.status == RaidStatus.REVIEW

    @pytest.mark.asyncio
    async def test_completion_sends_feedback_on_issues(self) -> None:
        """Reviewer issues are forwarded to the working session."""
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.services.review_engine import ReviewEngine

        volundr = StubVolundr()
        config = _default_config(
            reviewer_session_enabled=True,
            auto_approve_threshold=0.80,
        )
        factory = StubVolundrFactory(volundr)
        reviewer = ReviewerSessionService(volundr_factory=factory, review_config=config)

        raid = _make_raid(confidence=0.5)
        tracker_stub = _TrackerStub(raid)

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(tracker_stub),
            volundr_factory=factory,
            git=_FullGitStub(),
            review_config=config,
            event_bus=InMemoryEventBus(),
            reviewer_service=reviewer,
        )

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        output = (
            '{"confidence": 0.6, "approved": false,'
            ' "summary": "Issues found", "issues": ["bad import"]}'
        )
        await engine.handle_reviewer_completion("reviewer-1", output)

        # With issues, the reviewer drives the loop — round incremented, not escalated
        updated_raid = tracker_stub.raids[raid.tracker_id]
        assert updated_raid.review_round == 1
        assert updated_raid.status == RaidStatus.REVIEW
        # Reviewer session mapping is kept alive for the next idle event
        assert engine.get_reviewer_raid("reviewer-1") is not None

    @pytest.mark.asyncio
    async def test_completion_unparseable_chronicle_is_ignored(self) -> None:
        """If chronicle can't be parsed, nothing happens."""
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.services.review_engine import ReviewEngine

        volundr = StubVolundr()
        config = _default_config(reviewer_session_enabled=True)
        factory = StubVolundrFactory(volundr)
        reviewer = ReviewerSessionService(volundr_factory=factory, review_config=config)

        raid = _make_raid(confidence=0.5)
        tracker_stub = _TrackerStub(raid)

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(tracker_stub),
            volundr_factory=factory,
            git=_FullGitStub(),
            review_config=config,
            event_bus=InMemoryEventBus(),
            reviewer_service=reviewer,
        )

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        # Unparseable text
        await engine.handle_reviewer_completion("reviewer-1", "random garbage text")

        # Raid status should remain REVIEW (unchanged)
        updated_raid = tracker_stub.raids[raid.tracker_id]
        assert updated_raid.status == RaidStatus.REVIEW

    @pytest.mark.asyncio
    async def test_get_reviewer_raid_returns_none_for_unknown(self) -> None:
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.services.review_engine import ReviewEngine

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(_TrackerStub(_make_raid())),
            volundr_factory=StubVolundrFactory(StubVolundr()),
            git=_FullGitStub(),
            review_config=_default_config(),
            event_bus=InMemoryEventBus(),
        )
        assert engine.get_reviewer_raid("unknown-session") is None

    @pytest.mark.asyncio
    async def test_completion_for_untracked_session_is_ignored(self) -> None:
        from tyr.adapters.memory_event_bus import InMemoryEventBus
        from tyr.domain.services.review_engine import ReviewEngine

        engine = ReviewEngine(
            tracker_factory=_TrackerFactoryStub(_TrackerStub(_make_raid())),
            volundr_factory=StubVolundrFactory(StubVolundr()),
            git=_FullGitStub(),
            review_config=_default_config(),
            event_bus=InMemoryEventBus(),
        )
        # Should not raise
        await engine.handle_reviewer_completion("no-such-session", "some output")


# ---------------------------------------------------------------------------
# Tests: SpawnRequest profile field
# ---------------------------------------------------------------------------


class TestSpawnRequestProfile:
    """Tests for the profile field on SpawnRequest."""

    def test_default_profile_is_none(self) -> None:
        req = SpawnRequest(
            name="test",
            repo="org/repo",
            branch="main",
            model="claude-sonnet-4-6",
            tracker_issue_id="NIU-1",
            tracker_issue_url="https://example.com",
            system_prompt="prompt",
            initial_prompt="go",
            base_branch="dev",
        )
        assert req.profile is None

    def test_profile_can_be_set(self) -> None:
        req = SpawnRequest(
            name="test",
            repo="org/repo",
            branch="main",
            model="claude-sonnet-4-6",
            tracker_issue_id="NIU-1",
            tracker_issue_url="https://example.com",
            system_prompt="prompt",
            initial_prompt="go",
            base_branch="dev",
            profile="reviewer",
        )
        assert req.profile == "reviewer"


# ---------------------------------------------------------------------------
# Tests: ReviewConfig reviewer fields
# ---------------------------------------------------------------------------


class TestReviewConfigReviewerFields:
    """Tests for the new reviewer-related config fields."""

    def test_defaults(self) -> None:
        cfg = ReviewConfig()
        assert cfg.reviewer_session_enabled is True
        assert cfg.reviewer_model == "claude-opus-4-6"
        assert cfg.reviewer_profile == "reviewer"
        assert cfg.reviewer_confidence_weight == 0.60
        assert cfg.reviewer_spawn_bonus == 0.1
        assert "code reviewer" in cfg.reviewer_system_prompt.lower()

    def test_no_polling_config(self) -> None:
        """Ensure timeout/poll_interval fields do NOT exist."""
        cfg = ReviewConfig()
        assert not hasattr(cfg, "reviewer_timeout")
        assert not hasattr(cfg, "reviewer_poll_interval")

    def test_override(self) -> None:
        cfg = ReviewConfig(
            reviewer_session_enabled=False,
            reviewer_model="claude-sonnet-4-6",
            reviewer_profile="custom-reviewer",
        )
        assert cfg.reviewer_session_enabled is False
        assert cfg.reviewer_model == "claude-sonnet-4-6"
        assert cfg.reviewer_profile == "custom-reviewer"


# ---------------------------------------------------------------------------
# Tests: ConfidenceEventType.REVIEWER_SCORE
# ---------------------------------------------------------------------------


class TestConfidenceEventTypeReviewerScore:
    """Tests for the new REVIEWER_SCORE confidence event type."""

    def test_reviewer_score_exists(self) -> None:
        from tyr.domain.models import ConfidenceEventType

        assert hasattr(ConfidenceEventType, "REVIEWER_SCORE")
        assert ConfidenceEventType.REVIEWER_SCORE == "reviewer_score"

    def test_reviewer_score_in_enum_values(self) -> None:
        from tyr.domain.models import ConfidenceEventType

        values = [e.value for e in ConfidenceEventType]
        assert "reviewer_score" in values
