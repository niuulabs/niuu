"""Drive-loop integration tests for the Retrieval Reflex (NIU-1059)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx

from ravn.config import InitiativeConfig, MimirConfig, MimirReflexConfig
from ravn.domain.models import AgentTask, OutputMode
from ravn.drive_loop import DriveLoop
from ravn.reflex import POINTER_BLOCK_HEADER, ReflexEntity, ReflexInjector


def _make_task(task_id: str = "t1", *, context: str = "do it") -> AgentTask:
    return AgentTask(
        task_id=task_id,
        title="test",
        initiative_context=context,
        triggered_by="test",
        output_mode=OutputMode.SILENT,
    )


def _make_drive_loop() -> tuple[DriveLoop, list[str]]:
    """Build a DriveLoop whose agent factory records the prompt it receives."""
    prompts: list[str] = []

    def _agent_factory(channel, task_id, persona, triggered_by):
        agent = AsyncMock()
        fake_usage = MagicMock(input_tokens=0, output_tokens=0)

        async def _run_turn(prompt):
            prompts.append(prompt)
            return MagicMock(usage=fake_usage, cost_usd=0.0, response="ok")

        agent.run_turn = AsyncMock(side_effect=_run_turn)
        return agent

    cfg = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=1,
        task_queue_max=10,
        queue_journal_path="/proc/no_such_dir/queue.json",
    )
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = False
    settings.mesh.enabled = False
    settings.budget.daily_cap_usd = 100.0
    settings.budget.warn_at_percent = 80
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)
    return dl, prompts


def _injector(feed: list[ReflexEntity]) -> ReflexInjector:
    async def fetch():
        return feed

    return ReflexInjector(fetch_entities=fetch, max_pointers=5, cache_ttl_seconds=300.0)


_FEED = [
    ReflexEntity(
        slug="volundr-auth",
        title="Volundr Auth",
        type="decision",
        confidence="high",
        updated_at="2026-05-20T10:00:00Z",
        path="wiki/technical/volundr/auth.md",
    )
]


class TestDriveLoopReflex:
    async def test_default_settings_leave_prompt_unchanged(self):
        dl, prompts = _make_drive_loop()
        await dl._run_task(_make_task(context="Check Volundr Auth"))
        assert len(prompts) == 1
        assert POINTER_BLOCK_HEADER not in prompts[0]
        # MagicMock settings never expose a literal enabled=True, so the
        # injector is not built.
        assert dl._reflex_injector is None
        assert dl._reflex_initialised is True

    async def test_enabled_reflex_prefixes_pointer_block(self):
        dl, prompts = _make_drive_loop()
        dl._reflex_injector = _injector(_FEED)
        dl._reflex_initialised = True

        await dl._run_task(_make_task(context="Check Volundr Auth before changing it"))

        assert len(prompts) == 1
        assert prompts[0].startswith(POINTER_BLOCK_HEADER)
        assert "[[volundr-auth]]" in prompts[0]
        assert "mimir_read wiki/technical/volundr/auth.md" in prompts[0]

    async def test_same_session_injects_slug_once(self):
        dl, prompts = _make_drive_loop()
        dl._reflex_injector = _injector(_FEED)
        dl._reflex_initialised = True

        first = _make_task("t1", context="Check Volundr Auth")
        second = _make_task("t2", context="More about Volundr Auth")
        first.session_id = "session-a"
        second.session_id = "session-a"
        await dl._run_task(first)
        await dl._run_task(second)

        assert POINTER_BLOCK_HEADER in prompts[0]
        assert POINTER_BLOCK_HEADER not in prompts[1]

    async def test_feed_error_fails_open_and_logs(self, caplog):
        async def fetch():
            raise httpx.ConnectError("mimir down")

        dl, prompts = _make_drive_loop()
        dl._reflex_injector = ReflexInjector(
            fetch_entities=fetch, max_pointers=5, cache_ttl_seconds=300.0
        )
        dl._reflex_initialised = True

        with caplog.at_level(logging.ERROR, logger="ravn.reflex"):
            await dl._run_task(_make_task(context="Check Volundr Auth"))

        assert len(prompts) == 1
        assert POINTER_BLOCK_HEADER not in prompts[0]
        assert any("entity feed unreachable" in r.getMessage() for r in caplog.records)

    async def test_injector_built_from_real_config(self):
        dl, _ = _make_drive_loop()
        dl._settings.mimir = MimirConfig(
            reflex=MimirReflexConfig(enabled=True, base_url="http://mimir.test")
        )
        injector = dl._retrieval_reflex_injector()
        assert isinstance(injector, ReflexInjector)
        # Lazily built once, then cached.
        assert dl._retrieval_reflex_injector() is injector

    async def test_injector_none_when_config_disabled(self):
        dl, _ = _make_drive_loop()
        dl._settings.mimir = MimirConfig(reflex=MimirReflexConfig(enabled=False))
        assert dl._retrieval_reflex_injector() is None

    async def test_apply_retrieval_reflex_never_raises(self, caplog):
        dl, _ = _make_drive_loop()
        broken = MagicMock()
        broken.apply = AsyncMock(side_effect=RuntimeError("boom"))
        dl._reflex_injector = broken
        dl._reflex_initialised = True

        with caplog.at_level(logging.ERROR, logger="ravn.drive_loop"):
            result = await dl._apply_retrieval_reflex("prompt text", _make_task())

        assert result == "prompt text"
        assert any("retrieval reflex failed" in r.getMessage() for r in caplog.records)
