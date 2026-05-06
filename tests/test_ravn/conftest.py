"""Shared fixtures for Ravn tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission
from ravn.config import InitiativeConfig
from ravn.domain.models import (
    AgentTask,
    LLMResponse,
    OutputMode,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from ravn.drive_loop import DriveLoop
from ravn.ports.llm import LLMPort
from ravn.ports.spawn import SpawnConfig
from tests.ravn.fixtures.fakes import EchoTool, FailingTool, InMemoryChannel

# Re-export so existing test imports that used `from tests.test_ravn.conftest import X`
# continue to work without modification.
__all__ = [
    "AllowAllPermission",
    "DenyAllPermission",
    "EchoTool",
    "FailingTool",
    "InMemoryChannel",
    "make_simple_llm",
    "_NO_JOURNAL_PATH",
    "_FakePeer",
    "_FakeDiscovery",
    "_FakeSpawnAdapter",
    "_make_drive_loop",
    "_make_agent_task",
]

# Use a path that DriveLoop will silently fail to write (permission error) so
# journal persistence does not cause duplicate task restoration in tests.
_NO_JOURNAL_PATH = "/proc/no_such_dir/queue.json"


class _FakePeer:
    def __init__(self, peer_id: str, status: str = "idle", capabilities: list | None = None):
        self.peer_id = peer_id
        self.status = status
        self.capabilities = capabilities or []
        self.persona = "default"
        self.host = "localhost"
        self.task_count = 0


class _FakeDiscovery:
    def __init__(self, peers: dict | None = None):
        self._peers = peers or {}

    def peers(self) -> dict:
        return self._peers


class _FakeSpawnAdapter:
    def __init__(self, peer_ids: list[str] | None = None):
        self._peer_ids = peer_ids or ["spawned-peer-1"]
        self.spawned_configs: list[SpawnConfig] = []
        self.terminated: list[str] = []
        self.all_terminated = False

    async def spawn(self, count: int, config: SpawnConfig) -> list[str]:
        self.spawned_configs.append(config)
        return self._peer_ids[:count]

    async def terminate(self, peer_id: str) -> None:
        self.terminated.append(peer_id)

    async def terminate_all(self) -> None:
        self.all_terminated = True


def _make_drive_loop(
    max_concurrent: int = 3,
    queue_max: int = 50,
    event_publisher: object | None = None,
    agent_factory: object | None = None,
    heartbeat_seconds: int = 60,
    journal_path: str = _NO_JOURNAL_PATH,
) -> DriveLoop:
    """Build a minimal DriveLoop with a mock agent_factory."""
    if agent_factory is None:
        agent_factory = MagicMock(return_value=AsyncMock())
    cfg = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=max_concurrent,
        task_queue_max=queue_max,
        queue_journal_path=journal_path,
        heartbeat_interval_seconds=heartbeat_seconds,
    )
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = False
    settings.budget.daily_cap_usd = 1.0
    settings.budget.warn_at_percent = 80
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    kwargs: dict = {"agent_factory": agent_factory, "config": cfg, "settings": settings}
    if event_publisher is not None:
        kwargs["event_publisher"] = event_publisher
    return DriveLoop(**kwargs)


def _make_agent_task(
    task_id: str = "task_001",
    output_mode: OutputMode = OutputMode.SILENT,
    priority: int = 10,
    deadline: datetime | None = None,
) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        title="test task",
        initiative_context="do something",
        triggered_by="test",
        output_mode=output_mode,
        priority=priority,
        deadline=deadline,
    )


def make_simple_llm(response_text: str = "Hello!") -> LLMPort:
    """Build a mock LLM that returns a simple text response."""

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
            content=response_text,
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )
    )
    return llm


@pytest.fixture
def channel() -> InMemoryChannel:
    return InMemoryChannel()


@pytest.fixture
def allow_permission() -> AllowAllPermission:
    return AllowAllPermission()


@pytest.fixture
def deny_permission() -> DenyAllPermission:
    return DenyAllPermission()


@pytest.fixture
def echo_tool() -> EchoTool:
    return EchoTool()
