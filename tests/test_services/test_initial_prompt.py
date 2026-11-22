"""A session's first message survives every start until the session has run a turn.

On 2026-11-22 a session crash-looped before its first turn, was stopped, and was started
again after the fix: it came up with an empty chat, because the prompt was only an argument
of the first launch and stored nowhere.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.adapters.outbound.contributors.prompt import PromptContributor
from volundr.domain.models import Session, SessionStatus
from volundr.domain.ports import SessionContribution
from volundr.domain.services.session import SessionService
from volundr.domain.session_initial_prompt import INITIAL_PROMPT_KEY, initial_prompt_of

PROMPT = "Fix the login bug"


class Recorder:
    """A contributor that keeps the contexts it was shown."""

    name = "recorder"

    def __init__(self) -> None:
        self.contexts = []

    async def contribute(self, session, context):
        self.contexts.append(context)
        return SessionContribution()


class Forge:
    def __init__(self, *, start_success: bool = True) -> None:
        self.repository = InMemorySessionRepository()
        self.pods = MockPodManager(start_success=start_success)
        self.recorder = Recorder()
        self.service = SessionService(
            self.repository, self.pods, contributors=[PromptContributor(), self.recorder]
        )
        self.service._poll_readiness = AsyncMock()

    async def create(self) -> Session:
        return await self.service.create_session(name="s", model="m")

    async def start(self, session: Session, **kwargs) -> dict:
        """Start the session; the ``session`` values its pod manager was given."""
        await self.service.start_session(session.id, **kwargs)
        await asyncio.gather(*list(self.service._provisioning_tasks.values()))
        _, spec = self.pods.start_calls[-1]
        return spec.values.get("session", {})

    async def end(self, session: Session, status: SessionStatus, **fields) -> None:
        stored = (await self.repository.get(session.id)).with_status(status)
        await self.repository.update(stored.model_copy(update=fields))


@pytest.mark.parametrize("ended", [SessionStatus.STOPPED, SessionStatus.FAILED])
async def test_a_start_before_the_first_turn_carries_the_prompt_again(ended):
    forge = Forge()
    session = await forge.create()
    assert (await forge.start(session, initial_prompt=PROMPT))["initialPrompt"] == PROMPT

    for _ in range(2):
        await forge.end(session, ended)
        # Exactly what POST /sessions/{id}/start sends: no prompt of its own.
        assert (await forge.start(session))["initialPrompt"] == PROMPT


async def test_a_launch_that_never_reached_a_runtime_keeps_the_prompt():
    forge = Forge(start_success=False)
    session = await forge.create()
    await forge.start(session, initial_prompt=PROMPT)
    assert (await forge.repository.get(session.id)).status is SessionStatus.FAILED

    forge.pods.start_success = True
    assert (await forge.start(session))["initialPrompt"] == PROMPT


async def test_a_start_that_names_its_own_workload_config_keeps_the_prompt():
    forge = Forge()
    session = await forge.create()
    await forge.start(session, initial_prompt=PROMPT, workload_config={"a": 1})
    await forge.end(session, SessionStatus.STOPPED)

    assert (await forge.start(session, workload_config={"b": 2}))["initialPrompt"] == PROMPT


async def test_a_session_that_ran_a_turn_is_never_given_the_prompt_again():
    forge = Forge()
    session = await forge.create()
    await forge.start(session, initial_prompt=PROMPT)
    # ``last_work_at`` is folded from the durable log: the harness produced turn work.
    await forge.end(session, SessionStatus.STOPPED, last_work_at=datetime.now(UTC))

    assert "initialPrompt" not in await forge.start(session)
    # Nor when the caller (a hosted unit's restart) sends the kept prompt itself.
    await forge.end(session, SessionStatus.STOPPED)
    assert "initialPrompt" not in await forge.start(session, initial_prompt=PROMPT)


async def test_a_session_without_a_prompt_stores_none():
    forge = Forge()
    session = await forge.create()

    assert "initialPrompt" not in await forge.start(session)
    assert INITIAL_PROMPT_KEY not in (await forge.repository.get(session.id)).workload_config


async def test_a_new_prompt_replaces_the_stored_one():
    forge = Forge()
    session = await forge.create()
    await forge.start(session, initial_prompt=PROMPT)
    await forge.end(session, SessionStatus.STOPPED)

    await forge.start(session, initial_prompt="Do this instead")
    await forge.end(session, SessionStatus.STOPPED)

    assert (await forge.start(session))["initialPrompt"] == "Do this instead"


async def test_contributors_never_see_the_stored_prompt():
    forge = Forge()
    session = await forge.create()
    await forge.start(session, initial_prompt=PROMPT)
    stored = await forge.repository.get(session.id)
    assert initial_prompt_of(stored.workload_config) == PROMPT
    await forge.end(session, SessionStatus.STOPPED)
    await forge.start(session)

    assert len(forge.recorder.contexts) == 2
    assert all(INITIAL_PROMPT_KEY not in c.workload_config for c in forge.recorder.contexts)


async def test_a_request_cannot_set_the_stored_prompt():
    forge = Forge()
    session = await forge.create()

    values = await forge.start(session, workload_config={INITIAL_PROMPT_KEY: "forged"})

    assert "initialPrompt" not in values
    assert initial_prompt_of((await forge.repository.get(session.id)).workload_config) == ""
