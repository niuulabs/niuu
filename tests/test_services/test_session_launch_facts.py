"""A launch records what its contributors established, and each launch replaces the record."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.domain.models import SessionStatus
from volundr.domain.ports import SessionContribution
from volundr.domain.services.session import SessionService
from volundr.domain.session_launch_facts import (
    LAUNCH_FACTS_KEY,
    VOICE_CHANNEL_FACT,
    launch_facts_of,
    voice_channel_launched,
    without_launch_facts,
)


class Reporter:
    """A contributor that reports the facts it is given and records what it was shown."""

    def __init__(self, name: str, facts: dict | None = None) -> None:
        self.name = name
        self._facts = facts or {}
        self.contexts = []

    async def contribute(self, session, context):
        self.contexts.append(context)
        return SessionContribution(launch_facts=dict(self._facts))


def _service(*contributors, repository=None) -> tuple[SessionService, InMemorySessionRepository]:
    repository = repository or InMemorySessionRepository()
    service = SessionService(repository, MockPodManager(), contributors=list(contributors))
    service._poll_readiness = AsyncMock()
    return service, repository


async def _launch(service: SessionService, session, **kwargs):
    started = await service.start_session(session.id, **kwargs)
    await asyncio.gather(*list(service._provisioning_tasks.values()))
    return started


async def test_a_fact_a_contributor_reports_is_recorded_on_the_session():
    service, repository = _service(Reporter("voice", {VOICE_CHANNEL_FACT: True}))
    session = await service.create_session(name="s", model="m")

    await _launch(service, session)

    stored = await repository.get(session.id)
    assert stored.workload_config[LAUNCH_FACTS_KEY] == {VOICE_CHANNEL_FACT: True}
    assert voice_channel_launched(stored.workload_config)


async def test_a_launch_that_establishes_nothing_records_nothing():
    service, repository = _service(Reporter("quiet"))
    session = await service.create_session(name="s", model="m")

    await _launch(service, session)

    stored = await repository.get(session.id)
    assert LAUNCH_FACTS_KEY not in stored.workload_config
    assert not voice_channel_launched(stored.workload_config)


async def test_contributors_never_see_the_record():
    reporter = Reporter("voice", {VOICE_CHANNEL_FACT: True})
    service, repository = _service(reporter)
    session = await service.create_session(name="s", model="m")
    await _launch(service, session)
    assert LAUNCH_FACTS_KEY in (await repository.get(session.id)).workload_config
    await repository.update((await repository.get(session.id)).with_status(SessionStatus.STOPPED))

    await _launch(service, session)

    assert len(reporter.contexts) == 2
    assert all(LAUNCH_FACTS_KEY not in c.workload_config for c in reporter.contexts)


async def test_a_restart_replaces_the_previous_launchs_facts():
    reporter = Reporter("voice", {VOICE_CHANNEL_FACT: True})
    service, repository = _service(reporter)
    session = await service.create_session(name="s", model="m")
    await _launch(service, session)
    stopped = (await repository.get(session.id)).with_status(SessionStatus.STOPPED)
    await repository.update(stopped)

    # Voice is switched off before the restart: the new launch establishes nothing.
    reporter._facts = {}
    started = await service.start_session(session.id)

    # The old record is gone as soon as the new launch begins, not only once it finishes.
    assert LAUNCH_FACTS_KEY not in started.workload_config
    assert LAUNCH_FACTS_KEY not in (await repository.get(session.id)).workload_config
    await asyncio.gather(*list(service._provisioning_tasks.values()))
    assert LAUNCH_FACTS_KEY not in (await repository.get(session.id)).workload_config


async def test_a_request_cannot_set_the_record():
    service, repository = _service(Reporter("quiet"))
    session = await service.create_session(name="s", model="m")
    forged = {LAUNCH_FACTS_KEY: {VOICE_CHANNEL_FACT: True}, "other": "kept"}

    started = await _launch(service, session, workload_config=forged)

    assert LAUNCH_FACTS_KEY not in started.workload_config
    stored = await repository.get(session.id)
    assert LAUNCH_FACTS_KEY not in stored.workload_config
    assert stored.workload_config["other"] == "kept"
    assert not voice_channel_launched(stored.workload_config)


async def test_the_launch_overwrites_a_forged_record_with_its_own_facts():
    service, repository = _service(Reporter("voice", {VOICE_CHANNEL_FACT: True}))
    session = await service.create_session(name="s", model="m")
    forged = {LAUNCH_FACTS_KEY: {"forged": True, VOICE_CHANNEL_FACT: False}}

    await _launch(service, session, workload_config=forged)

    stored = await repository.get(session.id)
    assert stored.workload_config[LAUNCH_FACTS_KEY] == {VOICE_CHANNEL_FACT: True}


async def test_two_contributors_may_report_the_same_fact_alike():
    service, repository = _service(
        Reporter("a", {VOICE_CHANNEL_FACT: True, "x": 1}),
        Reporter("b", {VOICE_CHANNEL_FACT: True}),
    )
    session = await service.create_session(name="s", model="m")

    await _launch(service, session)

    stored = await repository.get(session.id)
    assert stored.workload_config[LAUNCH_FACTS_KEY] == {VOICE_CHANNEL_FACT: True, "x": 1}


async def test_two_contributors_that_disagree_fail_the_launch_naming_both():
    service, repository = _service(
        Reporter("first", {VOICE_CHANNEL_FACT: True}),
        Reporter("second", {VOICE_CHANNEL_FACT: False}),
    )
    session = await service.create_session(name="s", model="m")

    await _launch(service, session)

    failed = await repository.get(session.id)
    assert failed.status == SessionStatus.FAILED
    assert VOICE_CHANNEL_FACT in failed.error
    assert "'first'" in failed.error
    assert "'second'" in failed.error
    assert LAUNCH_FACTS_KEY not in failed.workload_config


async def test_the_merge_names_the_disagreement_when_built_directly():
    service, _ = _service(
        Reporter("first", {VOICE_CHANNEL_FACT: True}),
        Reporter("second", {VOICE_CHANNEL_FACT: False}),
    )
    session = await service.create_session(name="s", model="m")

    with pytest.raises(ValueError, match="two contributors must not disagree"):
        await service._build_spec(session, None, None, None, False)


class TestSessionLaunchFactsHelpers:
    def test_facts_of_a_session_never_launched_with_any_are_empty(self):
        assert launch_facts_of(None) == {}
        assert launch_facts_of({}) == {}
        assert launch_facts_of({"other": 1}) == {}

    def test_a_record_that_is_not_a_mapping_reads_as_no_facts(self):
        assert launch_facts_of({LAUNCH_FACTS_KEY: "voice_channel"}) == {}

    def test_facts_are_returned_as_a_copy(self):
        config = {LAUNCH_FACTS_KEY: {VOICE_CHANNEL_FACT: True}}
        launch_facts_of(config)["x"] = 1
        assert config[LAUNCH_FACTS_KEY] == {VOICE_CHANNEL_FACT: True}

    def test_without_the_record_the_rest_of_the_config_remains(self):
        config = {LAUNCH_FACTS_KEY: {VOICE_CHANNEL_FACT: True}, "a": 1}
        assert without_launch_facts(config) == {"a": 1}
        assert LAUNCH_FACTS_KEY in config

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            (None, False),
            ({}, False),
            ({LAUNCH_FACTS_KEY: {VOICE_CHANNEL_FACT: True}}, True),
            ({LAUNCH_FACTS_KEY: {VOICE_CHANNEL_FACT: False}}, False),
            ({LAUNCH_FACTS_KEY: {VOICE_CHANNEL_FACT: "true"}}, False),
            ({LAUNCH_FACTS_KEY: {}}, False),
        ],
    )
    def test_the_voice_channel_reads_true_only_when_recorded_true(self, config, expected):
        assert voice_channel_launched(config) is expected
