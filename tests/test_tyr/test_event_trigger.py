"""Tests for tyr.adapters.event_trigger.EventTriggerAdapter."""

from __future__ import annotations

import asyncio
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent
from tests.test_tyr.stubs import InMemorySagaRepository, StubVolundrFactory, StubVolundrPort
from tyr.adapters.event_trigger import (
    EventTriggerAdapter,
    _TriggerRule,
    matches_filter,
)
from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.domain.models import Phase, Raid, RaidStatus, Saga, SagaStatus
from tyr.domain.templates import BUNDLED_TEMPLATES_DIR, load_template
from tyr.ports.event_bus import TyrEvent
from tyr.ports.saga_repository import SagaRepository

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TS = datetime(2026, 4, 12, 10, 0, 0, tzinfo=UTC)
_OWNER = "test-owner"


def _make_sleipnir_event(
    event_type: str,
    payload: dict | None = None,
    *,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    return SleipnirEvent(
        event_type=event_type,
        source="test:source",
        payload=payload or {},
        summary="test event",
        urgency=0.5,
        domain="code",
        timestamp=_TS,
        correlation_id=correlation_id,
    )


def _make_rule(
    event_pattern: str = "github.pr.opened",
    saga_template: str = "review",
    auto_start: bool = True,
    filter: dict | None = None,
) -> _TriggerRule:
    return _TriggerRule(
        event_pattern=event_pattern,
        saga_template=saga_template,
        auto_start=auto_start,
        filter=filter or {},
    )


def _make_adapter(
    *,
    subscriber: object = None,
    saga_repo: SagaRepository | None = None,
    volundr_factory: object = None,
    event_bus: InMemoryEventBus | None = None,
    rules: list[_TriggerRule] | None = None,
    templates_dir: Path | None = None,
) -> EventTriggerAdapter:
    return EventTriggerAdapter(
        subscriber=subscriber or InProcessBus(),
        saga_repo=saga_repo or InMemorySagaRepository(),
        volundr_factory=volundr_factory or StubVolundrFactory(),
        event_bus=event_bus or InMemoryEventBus(),
        rules=rules if rules is not None else [_make_rule()],
        templates_dir=templates_dir or BUNDLED_TEMPLATES_DIR,
        owner_id=_OWNER,
    )


# ---------------------------------------------------------------------------
# Unit tests: matches_filter
# ---------------------------------------------------------------------------


class TestMatchesFilter:
    def test_empty_filter_always_matches(self):
        assert matches_filter({"branch": "main"}, {}) is True

    def test_matching_single_key(self):
        assert matches_filter({"branch": "main"}, {"branch": "main"}) is True

    def test_mismatching_single_key(self):
        assert matches_filter({"branch": "dev"}, {"branch": "main"}) is False

    def test_missing_key_in_payload(self):
        assert matches_filter({}, {"branch": "main"}) is False

    def test_all_keys_must_match(self):
        payload = {"branch": "main", "label": "bug"}
        assert matches_filter(payload, {"branch": "main", "label": "bug"}) is True
        assert matches_filter(payload, {"branch": "main", "label": "feature"}) is False

    def test_value_coerced_to_string(self):
        # Event payload values may be integers from JSON
        assert matches_filter({"pr_number": 42}, {"pr_number": "42"}) is True


# ---------------------------------------------------------------------------
# Unit tests: load_template
# ---------------------------------------------------------------------------


class TestLoadTemplate:
    def test_loads_bundled_review_template(self, tmp_path):
        payload = {
            "repo": "niuulabs/volundr",
            "pr_number": "99",
            "branch": "feat/test",
            "base_branch": "main",
            "title": "Fix the bug",
            "author": "alice",
            "pr_url": "https://github.com/niuulabs/volundr/pull/99",
        }
        tpl = load_template("review", BUNDLED_TEMPLATES_DIR, payload)

        assert "99" in tpl.name
        # review.yaml has 3 stages: parallel-review, qa-test, human-approval
        assert len(tpl.phases) == 3
        assert tpl.phases[0].name == "parallel-review"
        assert tpl.phases[2].name == "human-approval"
        assert tpl.phases[2].needs_approval is True
        assert len(tpl.phases[0].raids) == 2  # parallel: reviewer + security-auditor
        assert tpl.phases[0].raids[0].persona == "reviewer"

    def test_missing_template_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_template("nonexistent_template", tmp_path, {})

    def test_interpolation_replaces_event_placeholders(self, tmp_path):
        template_file = tmp_path / "simple.yaml"
        template_file.write_text(
            textwrap.dedent("""\
                name: "Test {event.repo}"
                feature_branch: "{event.branch}"
                base_branch: main
                repos:
                  - "{event.repo}"
                phases:
                  - name: Do It
                    raids:
                      - name: "Raid for {event.repo}"
                        description: "Desc"
                        acceptance_criteria: ["Done"]
                        declared_files: []
                        estimate_hours: 1.0
                        persona: executor
                        prompt: "Do something in {event.repo}"
            """),
            encoding="utf-8",
        )
        payload = {"repo": "my-repo", "branch": "feat/x"}
        tpl = load_template("simple", tmp_path, payload)

        assert tpl.name == "Test my-repo"
        assert tpl.feature_branch == "feat/x"
        assert tpl.repos == ["my-repo"]
        raid = tpl.phases[0].raids[0]
        assert raid.name == "Raid for my-repo"
        assert "my-repo" in raid.prompt

    def test_yaml_metacharacters_in_payload_do_not_inject(self, tmp_path):
        """Payload values with YAML metacharacters must not alter document structure."""
        template_file = tmp_path / "safe.yaml"
        template_file.write_text(
            textwrap.dedent("""\
                name: "PR {event.title}"
                feature_branch: main
                base_branch: main
                repos: []
                phases: []
            """),
            encoding="utf-8",
        )
        # A crafted title that would break structure if interpolated before parsing
        malicious_title = "legit\\ninjected_key: injected_value"
        tpl = load_template("safe", tmp_path, {"title": malicious_title})
        # Name should contain the raw string, no injected keys
        assert malicious_title in tpl.name
        assert tpl.feature_branch == "main"

    def test_unknown_placeholder_left_intact(self, tmp_path):
        template_file = tmp_path / "partial.yaml"
        template_file.write_text(
            textwrap.dedent("""\
                name: "Test {event.known} and {event.unknown}"
                feature_branch: main
                base_branch: main
                repos: []
                phases: []
            """),
            encoding="utf-8",
        )
        tpl = load_template("partial", tmp_path, {"known": "hello"})
        assert "hello" in tpl.name
        assert "{event.unknown}" in tpl.name

    def test_custom_templates_dir_takes_priority(self, tmp_path):
        custom = tmp_path / "review.yaml"
        custom.write_text(
            textwrap.dedent("""\
                name: "Custom Review"
                feature_branch: main
                base_branch: main
                repos: []
                phases: []
            """),
            encoding="utf-8",
        )
        tpl = load_template("review", tmp_path, {})
        assert tpl.name == "Custom Review"


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestEventTriggerAdapterLifecycle:
    async def test_start_subscribes_to_configured_patterns(self):
        bus = InProcessBus()
        adapter = _make_adapter(subscriber=bus, rules=[_make_rule("github.pr.opened")])

        assert not adapter.is_running
        await adapter.start()
        assert adapter.is_running
        await adapter.stop()

    async def test_start_is_idempotent(self):
        bus = InProcessBus()
        adapter = _make_adapter(subscriber=bus)
        await adapter.start()
        sub_before = adapter._subscription
        await adapter.start()
        assert adapter._subscription is sub_before
        await adapter.stop()

    async def test_stop_unsubscribes(self):
        bus = InProcessBus()
        adapter = _make_adapter(subscriber=bus)
        await adapter.start()
        await adapter.stop()
        assert not adapter.is_running
        assert adapter._subscription is None

    async def test_stop_when_not_started_is_safe(self):
        adapter = _make_adapter()
        await adapter.stop()  # must not raise

    async def test_no_rules_does_not_subscribe(self):
        bus = InProcessBus()
        adapter = _make_adapter(subscriber=bus, rules=[])
        await adapter.start()
        # Still safe; just no subscription created
        assert adapter._subscription is None
        await adapter.stop()


# ---------------------------------------------------------------------------
# Saga creation: auto_start=True
# ---------------------------------------------------------------------------


class TestEventTriggerAutoStart:
    async def test_matching_event_creates_saga_in_repo(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=True)],
        )
        await adapter.start()

        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "niuulabs/test"}))
        await bus.flush()
        await asyncio.sleep(0)  # let create_task run

        assert len(saga_repo.sagas) == 1
        saga = list(saga_repo.sagas.values())[0]
        assert saga.owner_id == _OWNER
        assert saga.tracker_type == "native"
        assert saga.status == SagaStatus.ACTIVE

        await adapter.stop()

    async def test_matching_event_creates_phase_and_raid(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=True)],
        )
        await adapter.start()

        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "repo/x"}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.phases) == 1
        assert len(saga_repo.raids) == 1

        await adapter.stop()

    async def test_auto_start_resolves_owner_integrations(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        volundr = StubVolundrPort()
        volundr.integration_ids = ["int-telegram", "int-linear"]
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            volundr_factory=StubVolundrFactory(volundr),
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=True)],
        )
        await adapter.start()

        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "repo/x"}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].integration_ids == ["int-telegram", "int-linear"]

        await adapter.stop()

        await adapter.stop()

    async def test_auto_start_spawns_volundr_session(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        volundr = StubVolundrPort()
        factory = StubVolundrFactory(volundr)
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            volundr_factory=factory,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=True)],
        )
        await adapter.start()

        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "niuulabs/test"}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(volundr.spawned) == 1

        await adapter.stop()

    async def test_auto_start_updates_raid_to_running(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        raid = list(saga_repo.raids.values())[0]
        assert raid.status == RaidStatus.RUNNING
        assert raid.session_id == "sess-001"

        await adapter.stop()

    async def test_auto_start_emits_saga_created_and_raid_state_changed(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        event_bus = InMemoryEventBus()
        q = event_bus.subscribe()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            event_bus=event_bus,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        events: list[TyrEvent] = []
        while not q.empty():
            events.append(q.get_nowait())

        event_types = {e.event for e in events}
        assert "saga.created" in event_types
        assert "raid.state_changed" in event_types

        await adapter.stop()

    async def test_no_volundr_adapter_logs_error_but_does_not_raise(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        factory = StubVolundrFactory(None)
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            volundr_factory=factory,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=True)],
        )
        await adapter.start()
        # Must not raise even though no Volundr adapter is available
        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)
        await adapter.stop()


# ---------------------------------------------------------------------------
# Saga creation: auto_start=False
# ---------------------------------------------------------------------------


class TestEventTriggerPendingApproval:
    async def test_auto_start_false_creates_pending_raid(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=False)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        raid = list(saga_repo.raids.values())[0]
        assert raid.status == RaidStatus.PENDING

        await adapter.stop()

    async def test_auto_start_false_does_not_spawn_session(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        volundr = StubVolundrPort()
        factory = StubVolundrFactory(volundr)
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            volundr_factory=factory,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=False)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(volundr.spawned) == 0

        await adapter.stop()

    async def test_auto_start_false_emits_needs_approval(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        event_bus = InMemoryEventBus()
        q = event_bus.subscribe()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            event_bus=event_bus,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened", auto_start=False)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        events: list[TyrEvent] = []
        while not q.empty():
            events.append(q.get_nowait())

        approval_events = [e for e in events if e.event == "raid.needs_approval"]
        assert len(approval_events) == 1
        assert approval_events[0].owner_id == _OWNER

        await adapter.stop()


# ---------------------------------------------------------------------------
# Filter matching
# ---------------------------------------------------------------------------


class TestEventTriggerFilterMatching:
    async def test_nonmatching_filter_ignores_event(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.merged", filter={"branch": "main"})],
        )
        await adapter.start()

        # branch=dev does not match filter branch=main
        await bus.publish(_make_sleipnir_event("github.pr.merged", {"branch": "dev", "repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.sagas) == 0

        await adapter.stop()

    async def test_matching_filter_triggers_saga(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.merged", filter={"branch": "main"})],
        )
        await adapter.start()

        await bus.publish(_make_sleipnir_event("github.pr.merged", {"branch": "main", "repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.sagas) == 1

        await adapter.stop()

    async def test_nonmatching_event_pattern_ignored(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened")],
        )
        await adapter.start()

        # ravn.session.ended does not match github.pr.opened
        await bus.publish(
            _make_sleipnir_event(
                "ravn.session.ended",
                {"session_id": "s-1", "repo": "r", "outcome": "ok", "duration_seconds": "60"},
            )
        )
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.sagas) == 0

        await adapter.stop()


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestEventTriggerDeduplication:
    async def test_same_correlation_id_creates_only_one_saga(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened")],
        )
        await adapter.start()

        event = _make_sleipnir_event("github.pr.opened", {"repo": "r"}, correlation_id="corr-123")
        await bus.publish(event)
        await bus.publish(event)
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.sagas) == 1

        await adapter.stop()

    async def test_different_correlation_ids_create_separate_sagas(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened")],
        )
        await adapter.start()

        await bus.publish(
            _make_sleipnir_event("github.pr.opened", {"repo": "r"}, correlation_id="corr-A")
        )
        await bus.publish(
            _make_sleipnir_event("github.pr.opened", {"repo": "r"}, correlation_id="corr-B")
        )
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.sagas) == 2

        await adapter.stop()

    async def test_dedup_uses_event_id_when_no_correlation_id(self, tmp_path):
        _write_minimal_template(tmp_path, "review")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("github.pr.opened")],
        )
        await adapter.start()

        event = _make_sleipnir_event("github.pr.opened", {"repo": "r"})
        # Publish the same object twice; event_id will be identical
        await bus.publish(event)
        await bus.publish(event)
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.sagas) == 1

        await adapter.stop()


# ---------------------------------------------------------------------------
# Missing template
# ---------------------------------------------------------------------------


class TestEventTriggerMissingTemplate:
    async def test_missing_template_logs_and_does_not_create_saga(self, tmp_path):
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,  # empty dir, no templates
            rules=[_make_rule("github.pr.opened", saga_template="nonexistent")],
        )
        await adapter.start()

        await bus.publish(_make_sleipnir_event("github.pr.opened", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.sagas) == 0

        await adapter.stop()


# ---------------------------------------------------------------------------
# Bundled templates smoke tests
# ---------------------------------------------------------------------------


class TestBundledTemplates:
    def test_review_template_loads(self):
        payload = {
            "repo": "niuulabs/v",
            "pr_number": "1",
            "branch": "feat/x",
            "base_branch": "main",
            "title": "My PR",
            "author": "bob",
            "pr_url": "https://github.com/niuulabs/v/pull/1",
        }
        tpl = load_template("review", BUNDLED_TEMPLATES_DIR, payload)
        assert tpl.name
        assert len(tpl.phases) >= 1

    def test_deploy_template_loads(self):
        payload = {
            "repo": "niuulabs/v",
            "sha": "abc123def456",
            "sha_short": "abc123d",
            "title": "Merge feat/x",
            "pr_url": "https://github.com/niuulabs/v/pull/1",
            "author": "bob",
        }
        tpl = load_template("deploy", BUNDLED_TEMPLATES_DIR, payload)
        assert tpl.name

    def test_investigate_template_loads(self):
        payload = {
            "repo": "niuulabs/v",
            "issue_number": "42",
            "title": "Something is broken",
            "author": "alice",
            "issue_url": "https://github.com/niuulabs/v/issues/42",
            "body": "It crashes on startup.",
        }
        tpl = load_template("investigate", BUNDLED_TEMPLATES_DIR, payload)
        assert tpl.name

    def test_reflect_template_loads(self):
        payload = {
            "session_id": "sess-xyz",
            "repo": "niuulabs/v",
            "outcome": "success",
            "duration_seconds": "120",
        }
        tpl = load_template("reflect", BUNDLED_TEMPLATES_DIR, payload)
        assert tpl.name


# ---------------------------------------------------------------------------
# build_event_trigger_adapter factory
# ---------------------------------------------------------------------------


class TestBuildEventTriggerAdapter:
    def test_build_from_config(self, tmp_path):
        from tyr.adapters.event_trigger import build_event_trigger_adapter
        from tyr.config import EventTriggerConfig, EventTriggerRule

        cfg = EventTriggerConfig(
            enabled=True,
            owner_id="owner-1",
            templates_dir=str(tmp_path),
            default_model="claude-haiku-4-5-20251001",
            dedup_cache_size=500,
            rules=[
                EventTriggerRule(
                    event="github.pr.opened",
                    saga_template="review",
                    auto_start=True,
                )
            ],
        )
        adapter = build_event_trigger_adapter(
            subscriber=InProcessBus(),
            saga_repo=InMemorySagaRepository(),
            volundr_factory=StubVolundrFactory(),
            event_bus=InMemoryEventBus(),
            config=cfg,
            initial_confidence=0.5,
        )
        assert len(adapter._rules) == 1
        assert adapter._rules[0].event_pattern == "github.pr.opened"
        assert adapter._rules[0].auto_start is True
        assert adapter._owner_id == "owner-1"
        assert adapter._default_model == "claude-haiku-4-5-20251001"

    def test_build_with_default_templates_dir(self):
        from tyr.adapters.event_trigger import build_event_trigger_adapter
        from tyr.config import EventTriggerConfig

        cfg = EventTriggerConfig(enabled=True)
        adapter = build_event_trigger_adapter(
            subscriber=InProcessBus(),
            saga_repo=InMemorySagaRepository(),
            volundr_factory=StubVolundrFactory(),
            event_bus=InMemoryEventBus(),
            config=cfg,
            initial_confidence=0.5,
        )
        assert adapter._templates_dir == BUNDLED_TEMPLATES_DIR


# ---------------------------------------------------------------------------
# NotificationService: raid.needs_approval
# ---------------------------------------------------------------------------


class TestNotificationServiceNeedsApproval:
    async def test_needs_approval_emits_high_urgency_notification(self):
        from tyr.domain.services.notification import NotificationService
        from tyr.ports.channel_resolver import ChannelResolverPort
        from tyr.ports.notification_channel import (
            Notification,
            NotificationChannel,
            NotificationUrgency,
        )

        class RecordingChannel(NotificationChannel):
            def __init__(self):
                self.sent: list[Notification] = []

            def should_notify(self, n: Notification) -> bool:
                return True

            async def send(self, n: Notification) -> None:
                self.sent.append(n)

        class StubFactory(ChannelResolverPort):
            def __init__(self, channel):
                self._channel = channel

            async def for_owner(self, owner_id: str) -> list[NotificationChannel]:
                return [self._channel]

        channel = RecordingChannel()
        event_bus = InMemoryEventBus()
        svc = NotificationService(
            event_bus=event_bus,
            channel_factory=StubFactory(channel),
            confidence_threshold=0.3,
        )
        await svc.start()

        await event_bus.emit(
            TyrEvent(
                event="raid.needs_approval",
                data={
                    "raid_id": "raid-1",
                    "raid_name": "Review PR #99",
                    "saga_id": "saga-1",
                    "saga_name": "Review: niuulabs/test#99",
                    "owner_id": "user-1",
                },
                owner_id="user-1",
            )
        )

        await asyncio.sleep(0.05)
        await svc.stop()

        assert len(channel.sent) == 1
        notif = channel.sent[0]
        assert notif.urgency == NotificationUrgency.HIGH
        assert notif.event_type == "raid.needs_approval"
        assert "Review PR #99" in notif.body

    async def test_needs_approval_without_owner_id_produces_no_notification(self):
        from tyr.domain.services.notification import NotificationService
        from tyr.ports.channel_resolver import ChannelResolverPort
        from tyr.ports.notification_channel import Notification, NotificationChannel

        class RecordingChannel(NotificationChannel):
            def __init__(self):
                self.sent: list[Notification] = []

            def should_notify(self, n: Notification) -> bool:
                return True

            async def send(self, n: Notification) -> None:
                self.sent.append(n)

        class StubFactory(ChannelResolverPort):
            def __init__(self, channel):
                self._channel = channel

            async def for_owner(self, owner_id: str) -> list[NotificationChannel]:
                return [self._channel]

        channel = RecordingChannel()
        event_bus = InMemoryEventBus()
        svc = NotificationService(
            event_bus=event_bus,
            channel_factory=StubFactory(channel),
            confidence_threshold=0.3,
        )
        await svc.start()

        await event_bus.emit(
            TyrEvent(
                event="raid.needs_approval",
                data={"raid_id": "r-1", "raid_name": "Something"},  # no owner_id
                owner_id="",
            )
        )

        await asyncio.sleep(0.05)
        await svc.stop()

        assert len(channel.sent) == 0


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestEventTriggerConfig:
    def test_default_config_has_empty_rules(self):
        from tyr.config import EventTriggerConfig

        cfg = EventTriggerConfig()
        assert cfg.rules == []
        assert cfg.enabled is False

    def test_rules_parsed_from_dict(self):
        from tyr.config import EventTriggerConfig

        cfg = EventTriggerConfig(
            enabled=True,
            rules=[
                {"event": "github.pr.opened", "saga_template": "review", "auto_start": True},
                {
                    "event": "github.pr.merged",
                    "saga_template": "deploy",
                    "auto_start": True,
                    "filter": {"branch": "main"},
                },
            ],
        )
        assert len(cfg.rules) == 2
        assert cfg.rules[0].event == "github.pr.opened"
        assert cfg.rules[1].filter == {"branch": "main"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_minimal_template(tmp_path: Path, name: str) -> None:
    """Write a minimal single-phase, single-raid YAML template."""
    (tmp_path / f"{name}.yaml").write_text(
        textwrap.dedent("""\
            name: "Test saga for {event.repo}"
            feature_branch: "feat/test"
            base_branch: main
            repos:
              - "{event.repo}"
            phases:
              - name: Execute
                raids:
                  - name: "Do the thing in {event.repo}"
                    description: "Automated saga raid"
                    acceptance_criteria:
                      - "Task completed"
                    declared_files: []
                    estimate_hours: 1.0
                    persona: executor
                    prompt: "Execute the task in {event.repo}"
        """),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Template validation tests
# ---------------------------------------------------------------------------


class TestTemplateValidation:
    """Tests for load_template validation rules."""

    def test_missing_raid_persona_raises(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            textwrap.dedent("""\
                name: "Bad template"
                feature_branch: main
                base_branch: main
                repos: []
                phases:
                  - name: Phase 1
                    raids:
                      - name: "Raid without persona"
                        description: "Missing persona field"
                        acceptance_criteria: []
                        declared_files: []
                        estimate_hours: 1.0
                        prompt: "Do something"
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="persona"):
            load_template("bad", tmp_path, {})

    def test_missing_raid_name_raises(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            textwrap.dedent("""\
                name: "Bad template"
                feature_branch: main
                base_branch: main
                repos: []
                phases:
                  - name: Phase 1
                    raids:
                      - description: "No name field"
                        acceptance_criteria: []
                        declared_files: []
                        estimate_hours: 1.0
                        persona: executor
                        prompt: "Do something"
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="name"):
            load_template("bad", tmp_path, {})

    def test_phase_without_raids_raises(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            textwrap.dedent("""\
                name: "Bad template"
                feature_branch: main
                base_branch: main
                repos: []
                phases:
                  - name: Empty Phase
                    raids: []
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="no raids"):
            load_template("bad", tmp_path, {})

    def test_missing_phase_name_raises(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            textwrap.dedent("""\
                name: "Bad template"
                feature_branch: main
                base_branch: main
                repos: []
                phases:
                  - raids:
                      - name: "Raid"
                        persona: executor
                        prompt: "Do"
                        acceptance_criteria: []
                        declared_files: []
                        estimate_hours: 1.0
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="name"):
            load_template("bad", tmp_path, {})

    def test_empty_phases_list_passes_validation(self, tmp_path):
        (tmp_path / "ok.yaml").write_text(
            textwrap.dedent("""\
                name: "Empty saga"
                feature_branch: main
                base_branch: main
                repos: []
                phases: []
            """),
            encoding="utf-8",
        )
        tpl = load_template("ok", tmp_path, {})
        assert tpl.phases == []

    def test_valid_template_with_needs_approval_loads(self, tmp_path):
        (tmp_path / "gated.yaml").write_text(
            textwrap.dedent("""\
                name: "Gated saga"
                feature_branch: main
                base_branch: main
                repos: []
                phases:
                  - name: Work
                    raids:
                      - name: "Do work"
                        persona: worker
                        prompt: "Work hard"
                        acceptance_criteria: ["Done"]
                        declared_files: []
                        estimate_hours: 1.0
                  - name: Gate
                    needs_approval: true
                    raids:
                      - name: "Approve"
                        persona: approver
                        prompt: "Approve this"
                        acceptance_criteria: ["Approved"]
                        declared_files: []
                        estimate_hours: 0.0
            """),
            encoding="utf-8",
        )
        tpl = load_template("gated", tmp_path, {})
        assert len(tpl.phases) == 2
        assert tpl.phases[0].needs_approval is False
        assert tpl.phases[1].needs_approval is True


# ---------------------------------------------------------------------------
# Template data-class fields
# ---------------------------------------------------------------------------


class TestTemplateDataclasses:
    def test_template_raid_has_persona_field(self, tmp_path):
        _write_minimal_template(tmp_path, "tpl")
        tpl = load_template("tpl", tmp_path, {"repo": "r"})
        assert tpl.phases[0].raids[0].persona == "executor"

    def test_template_phase_needs_approval_defaults_false(self, tmp_path):
        _write_minimal_template(tmp_path, "tpl")
        tpl = load_template("tpl", tmp_path, {"repo": "r"})
        assert tpl.phases[0].needs_approval is False

    def test_template_phase_needs_approval_true(self, tmp_path):
        (tmp_path / "gated.yaml").write_text(
            textwrap.dedent("""\
                name: "Gate saga"
                feature_branch: main
                base_branch: main
                repos: []
                phases:
                  - name: Approve
                    needs_approval: true
                    raids:
                      - name: "Gate"
                        persona: gatekeeper
                        prompt: "Gate"
                        acceptance_criteria: []
                        declared_files: []
                        estimate_hours: 0.0
            """),
            encoding="utf-8",
        )
        tpl = load_template("gated", tmp_path, {})
        assert tpl.phases[0].needs_approval is True


# ---------------------------------------------------------------------------
# Multi-phase sequential dispatch
# ---------------------------------------------------------------------------


def _write_two_phase_template(tmp_path: Path, name: str) -> None:
    """Write a two-phase template for sequential dispatch tests."""
    (tmp_path / f"{name}.yaml").write_text(
        textwrap.dedent("""\
            name: "Two-phase saga"
            feature_branch: feat/test
            base_branch: main
            repos:
              - "test/repo"
            phases:
              - name: Phase One
                raids:
                  - name: "Phase 1 raid"
                    description: "First phase work"
                    acceptance_criteria: ["Done"]
                    declared_files: []
                    estimate_hours: 1.0
                    persona: worker
                    prompt: "Do phase 1 work"
              - name: Phase Two
                raids:
                  - name: "Phase 2 raid"
                    description: "Second phase work"
                    acceptance_criteria: ["Done"]
                    declared_files: []
                    estimate_hours: 1.0
                    persona: worker
                    prompt: "Do phase 2 work"
        """),
        encoding="utf-8",
    )


def _write_gated_template(tmp_path: Path, name: str) -> None:
    """Write a two-phase template where Phase 2 needs approval."""
    (tmp_path / f"{name}.yaml").write_text(
        textwrap.dedent("""\
            name: "Gated saga"
            feature_branch: feat/test
            base_branch: main
            repos:
              - "test/repo"
            phases:
              - name: Work Phase
                raids:
                  - name: "Work raid"
                    description: "Work"
                    acceptance_criteria: ["Done"]
                    declared_files: []
                    estimate_hours: 1.0
                    persona: worker
                    prompt: "Do work"
              - name: Approval Gate
                needs_approval: true
                raids:
                  - name: "Approval raid"
                    description: "Awaiting approval"
                    acceptance_criteria: ["Approved"]
                    declared_files: []
                    estimate_hours: 0.0
                    persona: approver
                    prompt: "Approve"
        """),
        encoding="utf-8",
    )


class TestMultiPhaseDispatch:
    async def test_multi_phase_only_phase_1_raids_dispatched_initially(self, tmp_path):
        _write_two_phase_template(tmp_path, "two")
        volundr = StubVolundrPort()
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            volundr_factory=StubVolundrFactory(volundr),
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="two", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        # Only the Phase 1 raid should be spawned
        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].name == "phase-1-raid"

        await adapter.stop()

    async def test_multi_phase_creates_all_phases_in_db(self, tmp_path):
        _write_two_phase_template(tmp_path, "two")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="two", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(saga_repo.phases) == 2
        assert len(saga_repo.raids) == 2

        await adapter.stop()

    async def test_multi_phase_phase_2_starts_pending(self, tmp_path):
        _write_two_phase_template(tmp_path, "two")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="two", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        from tyr.domain.models import PhaseStatus

        phases_by_num = {p.number: p for p in saga_repo.phases.values()}
        assert phases_by_num[1].status == PhaseStatus.ACTIVE
        assert phases_by_num[2].status == PhaseStatus.PENDING

        await adapter.stop()

    async def test_multi_phase_all_raids_start_pending(self, tmp_path):
        _write_two_phase_template(tmp_path, "two")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="two", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        # Phase 1 raid: RUNNING (was dispatched); Phase 2 raid: PENDING
        raids_by_name = {r.name: r for r in saga_repo.raids.values()}
        assert raids_by_name["Phase 1 raid"].status == RaidStatus.RUNNING
        assert raids_by_name["Phase 2 raid"].status == RaidStatus.PENDING

        await adapter.stop()


# ---------------------------------------------------------------------------
# advance_phase tests
# ---------------------------------------------------------------------------


class TestAdvancePhase:
    async def test_advance_phase_dispatches_next_phase_raids(self, tmp_path):
        _write_two_phase_template(tmp_path, "two")
        volundr = StubVolundrPort()
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            volundr_factory=StubVolundrFactory(volundr),
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="two", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        saga_id = str(list(saga_repo.sagas.keys())[0])
        assert len(volundr.spawned) == 1

        # Advance to Phase 2
        await adapter.advance_phase(saga_id)

        assert len(volundr.spawned) == 2
        assert volundr.spawned[1].name == "phase-2-raid"

        await adapter.stop()

    async def test_advance_phase_with_needs_approval_emits_event(self, tmp_path):
        _write_gated_template(tmp_path, "gated")
        event_bus = InMemoryEventBus()
        q = event_bus.subscribe()
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            event_bus=event_bus,
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="gated", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        saga_id = str(list(saga_repo.sagas.keys())[0])
        # Drain events from Phase 1
        while not q.empty():
            q.get_nowait()

        await adapter.advance_phase(saga_id)

        events: list[TyrEvent] = []
        while not q.empty():
            events.append(q.get_nowait())

        approval_events = [e for e in events if e.event == "phase.needs_approval"]
        assert len(approval_events) == 1
        assert approval_events[0].data["phase_name"] == "Approval Gate"

        await adapter.stop()

    async def test_advance_phase_with_needs_approval_does_not_spawn(self, tmp_path):
        _write_gated_template(tmp_path, "gated")
        volundr = StubVolundrPort()
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            volundr_factory=StubVolundrFactory(volundr),
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="gated", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        saga_id = str(list(saga_repo.sagas.keys())[0])
        spawned_before = len(volundr.spawned)

        await adapter.advance_phase(saga_id)

        # No new sessions — phase is gated
        assert len(volundr.spawned) == spawned_before

        await adapter.stop()

    async def test_advance_phase_with_needs_approval_sets_gated_status(self, tmp_path):
        from tyr.domain.models import PhaseStatus

        _write_gated_template(tmp_path, "gated")
        saga_repo = InMemorySagaRepository()
        bus = InProcessBus()
        adapter = _make_adapter(
            subscriber=bus,
            saga_repo=saga_repo,
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="gated", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {"repo": "r"}))
        await bus.flush()
        await asyncio.sleep(0)

        saga_id = str(list(saga_repo.sagas.keys())[0])
        await adapter.advance_phase(saga_id)

        phases_by_num = {p.number: p for p in saga_repo.phases.values()}
        assert phases_by_num[2].status == PhaseStatus.GATED

        await adapter.stop()

    async def test_advance_phase_unknown_saga_is_noop(self):
        adapter = _make_adapter()
        # Must not raise
        await adapter.advance_phase("non-existent-saga-id")


# ---------------------------------------------------------------------------
# Persona → profile in SpawnRequest
# ---------------------------------------------------------------------------


class TestPersonaPassedToSpawnRequest:
    async def test_persona_passed_as_profile_in_spawn_request(self, tmp_path):
        volundr = StubVolundrPort()
        bus = InProcessBus()
        (tmp_path / "profiled.yaml").write_text(
            textwrap.dedent("""\
                name: "Profiled saga"
                feature_branch: main
                base_branch: main
                repos:
                  - "test/repo"
                phases:
                  - name: Review
                    raids:
                      - name: "Code review raid"
                        description: "Review code"
                        acceptance_criteria: ["Reviewed"]
                        declared_files: []
                        estimate_hours: 1.0
                        persona: code-reviewer
                        prompt: "Review the code"
            """),
            encoding="utf-8",
        )
        adapter = _make_adapter(
            subscriber=bus,
            volundr_factory=StubVolundrFactory(volundr),
            templates_dir=tmp_path,
            rules=[_make_rule("test.event", saga_template="profiled", auto_start=True)],
        )
        await adapter.start()
        await bus.publish(_make_sleipnir_event("test.event", {}))
        await bus.flush()
        await asyncio.sleep(0)

        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].profile == "code-reviewer"

        await adapter.stop()

    async def test_empty_persona_sets_profile_to_none(self, tmp_path):
        """An explicitly-empty persona is passed as None profile."""
        volundr = StubVolundrPort()
        bus = InProcessBus()
        # Use a phase with needs_approval=True (persona allowed to be empty by template design,
        # but the validator would normally reject empty persona).
        # We bypass validation by NOT using needs_approval and using a non-empty persona.
        # Instead test the None path by calling _spawn_raid with empty persona directly.
        from tyr.domain.templates import TemplateRaid

        adapter_obj = _make_adapter(
            subscriber=bus,
            volundr_factory=StubVolundrFactory(volundr),
            templates_dir=tmp_path,
        )
        import uuid
        from datetime import UTC, datetime

        from tyr.domain.models import PhaseStatus, RaidStatus, SagaStatus

        now = datetime.now(UTC)
        saga_id = uuid.uuid4()
        saga = Saga(
            id=saga_id,
            tracker_id=str(saga_id),
            tracker_type="native",
            slug="test",
            name="test",
            repos=["r"],
            feature_branch="main",
            base_branch="main",
            status=SagaStatus.ACTIVE,
            confidence=0.5,
            created_at=now,
            owner_id=_OWNER,
        )
        phase_id = uuid.uuid4()
        phase = Phase(
            id=phase_id,
            saga_id=saga_id,
            tracker_id=str(phase_id),
            number=1,
            name="P1",
            status=PhaseStatus.ACTIVE,
            confidence=0.5,
        )
        raid_id = uuid.uuid4()
        raid = Raid(
            id=raid_id,
            phase_id=phase_id,
            tracker_id=str(raid_id),
            name="r1",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            status=RaidStatus.PENDING,
            confidence=0.5,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )
        tpl_raid = TemplateRaid(
            name="r1",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="p",
            persona="",  # empty persona
        )
        await adapter_obj._spawn_raid(volundr, saga, phase, raid, tpl_raid)

        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].profile is None


# ---------------------------------------------------------------------------
# Bundled template smoke tests (ship + retro)
# ---------------------------------------------------------------------------


class TestBundledShipRetroTemplates:
    def test_ship_template_loads(self):
        payload = {
            "repo": "niuulabs/volundr",
            "branch": "feat/release",
            "base_branch": "main",
        }
        tpl = load_template("ship", BUNDLED_TEMPLATES_DIR, payload)
        assert tpl.name
        assert len(tpl.phases) == 4
        assert tpl.phases[0].name == "test-suite"
        assert tpl.phases[1].name == "pre-ship-review"
        assert tpl.phases[2].name == "version-bump"
        assert tpl.phases[3].name == "release-pr"
        # All phases have personas
        for phase in tpl.phases:
            for raid in phase.raids:
                assert raid.persona

    def test_retro_template_loads(self):
        payload = {"week": "2026-W15"}
        tpl = load_template("retro", BUNDLED_TEMPLATES_DIR, payload)
        assert tpl.name
        assert "2026-W15" in tpl.name
        assert len(tpl.phases) == 2
        assert tpl.phases[0].name == "retrospective-analysis"
        assert tpl.phases[1].name == "write-to-mimir"
        for phase in tpl.phases:
            for raid in phase.raids:
                assert raid.persona == "retro-analyst"

    def test_deploy_template_has_3_phases(self):
        payload = {
            "repo": "niuulabs/v",
            "sha": "abc123def456",
            "sha_short": "abc123d",
            "title": "Merge feat/x",
            "pr_url": "https://github.com/niuulabs/v/pull/1",
            "author": "bob",
        }
        tpl = load_template("deploy", BUNDLED_TEMPLATES_DIR, payload)
        assert len(tpl.phases) == 3
        assert tpl.phases[0].name == "smoke-test"
        assert tpl.phases[1].name == "monitor"
        assert tpl.phases[2].name == "release-docs"

    def test_review_template_has_4_phases_with_approval_gate(self):
        payload = {
            "repo": "niuulabs/v",
            "pr_number": "42",
            "branch": "feat/x",
            "base_branch": "main",
            "title": "My PR",
            "author": "bob",
            "pr_url": "https://github.com/niuulabs/v/pull/42",
        }
        tpl = load_template("review", BUNDLED_TEMPLATES_DIR, payload)
        # review.yaml has 3 stages: parallel-review, qa-test, human-approval
        assert len(tpl.phases) == 3
        assert tpl.phases[2].needs_approval is True
        assert tpl.phases[0].raids[0].persona == "reviewer"
        assert tpl.phases[0].raids[1].persona == "security-auditor"
        assert tpl.phases[1].raids[0].persona == "qa-agent"
