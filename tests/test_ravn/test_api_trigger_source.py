"""Tests for ApiTriggerSource's repo filter and failure handling, and for
DriveLoop._trigger_watcher not swallowing a trigger's fatal error.

Covers two MUST-FIX findings from the round-3 verification review:

1. ``_repo_filter`` read ``repo`` from the top level of the decoded AMQP
   message, but real Sleipnir events (``SleipnirEvent.to_dict()`` /
   ``sleipnir.adapters.serialization.serialize``) nest event-specific fields
   under ``payload`` — so every event-kind trigger's repo filter silently
   matched nothing, ever. Tested here against a REAL catalog event, not a
   flattened fixture dict, so a regression back to the flat shape fails loud.
2. ``_trigger_watcher`` caught-and-logged any exception from a trigger's
   ``run()``, so a permanently failed ``ApiTriggerSource`` (auth broken, the
   store unreachable past ``max_consecutive_poll_failures``) left the
   resident running forever on a frozen, no-longer-updating trigger cache.
   ``ApiTriggerSource.run()`` must also drop that cache in ``finally`` so any
   window before the process actually exits does not keep firing stale or
   deleted triggers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from ravn.adapters.triggers.api_source import ApiTriggerSource, _repo_filter
from ravn.config import InitiativeConfig
from ravn.drive_loop import DriveLoop
from sleipnir.domain import catalog


def _real_pr_opened_envelope(repo: str) -> dict:
    """The exact wire shape a resident's SleipnirEventTrigger decodes:
    ``json.loads(message.body)`` where the body was
    ``sleipnir.adapters.serialization.serialize(event, fmt="json")`` —
    i.e. ``event.to_dict()``, not the bare inner payload."""
    event = catalog.github_pr_opened(
        repo=repo,
        branch="main",
        author="octocat",
        pr_url="https://github.com/org/repo/pull/1",
        title="Fix the thing",
        source="volundr-webhook",
    )
    return event.to_dict()


class TestRepoFilter:
    def test_matches_a_real_catalog_event_for_the_same_repo(self) -> None:
        envelope = _real_pr_opened_envelope("org/repo")
        assert _repo_filter("org/repo")(envelope) is True

    def test_rejects_a_real_catalog_event_for_a_different_repo(self) -> None:
        envelope = _real_pr_opened_envelope("org/other-repo")
        assert _repo_filter("org/repo")(envelope) is False

    def test_rejects_an_envelope_with_no_payload_key(self) -> None:
        assert _repo_filter("org/repo")({"event_type": "x"}) is False

    def test_rejects_a_flat_dict_that_used_to_pass(self) -> None:
        """The pre-fix shape (repo at the top level) must NOT match — that
        was the bug, and a fixture built the wrong way could hide it again."""
        assert _repo_filter("org/repo")({"repo": "org/repo"}) is False


@dataclass(frozen=True)
class _Resp:
    status_code: int
    body: Any


class _FlakyClient:
    """Always returns a client error, forcing ApiTriggerSource.run() to give
    up after max_consecutive_poll_failures (a transient-shaped failure that
    is never actually a PermanentPollError, to exercise the *other* raise
    path — the max-failures RuntimeError)."""

    async def get(self, url: str, *, headers: dict | None = None) -> _Resp:
        del url, headers
        raise ConnectionError("platform unreachable")


class TestApiTriggerSourceFailureHandling:
    async def test_run_raises_after_max_consecutive_poll_failures(self) -> None:
        source = ApiTriggerSource(
            client=_FlakyClient(),
            base_url="https://volundr.internal",
            persona_name="product-resident",
            poll_interval_seconds=0.0,
            amqp_url="amqp://localhost",
            sleipnir_exchange="sleipnir",
            max_consecutive_poll_failures=2,
        )

        async def _enqueue(task):
            return True

        with pytest.raises(RuntimeError, match="consecutive poll"):
            await source.run(_enqueue)

    async def test_run_clears_cron_records_and_event_tasks_on_exit(self) -> None:
        source = ApiTriggerSource(
            client=_FlakyClient(),
            base_url="https://volundr.internal",
            persona_name="product-resident",
            poll_interval_seconds=0.0,
            amqp_url="amqp://localhost",
            sleipnir_exchange="sleipnir",
            max_consecutive_poll_failures=1,
        )
        # Seed state as if a prior successful poll had populated it.
        source._cron_records = [MagicMock()]  # noqa: SLF001
        source._event_tasks = {"stale-trigger": MagicMock()}  # noqa: SLF001

        async def _enqueue(task):
            return True

        with pytest.raises(RuntimeError):
            await source.run(_enqueue)

        assert source.cron_records == []
        assert source._event_tasks == {}  # noqa: SLF001


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = False
    settings.budget.daily_cap_usd = 1.0
    settings.budget.warn_at_percent = 80
    settings.budget.pricing_source = "flat"
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    settings.llm.model = "a-model-bifrost-does-not-price"
    return settings


_NO_JOURNAL_PATH = "/dev/null/does-not-exist"


def _drive_loop() -> DriveLoop:
    cfg = InitiativeConfig(enabled=True, queue_journal_path=_NO_JOURNAL_PATH)
    return DriveLoop(
        agent_factory=MagicMock(),
        config=cfg,
        settings=_settings(),
        resident_budget=None,
    )


class _RaisingTrigger:
    name = "raising-trigger"

    async def run(self, enqueue):
        del enqueue
        raise RuntimeError("consecutive poll failures against volundr")


class TestTriggerWatcherPropagatesFailures:
    async def test_trigger_watcher_does_not_swallow_the_error(self) -> None:
        """The bug: _trigger_watcher used to catch Exception and log it, so
        a dead ApiTriggerSource never surfaced past this point and the
        resident kept running on a frozen trigger cache forever."""
        dl = _drive_loop()

        with pytest.raises(RuntimeError, match="consecutive poll failures"):
            await dl._trigger_watcher(_RaisingTrigger())  # noqa: SLF001
