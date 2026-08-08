"""Inline fact capture is composition's choice, not the agent's.

The agent used to read `if self._mimir is not None: ... elif self._memory ...`
— picking a persistence backend by asking which infrastructure it had been
handed. MemoryPort.process_inline_facts already documented the opposite
contract ("called unconditionally ... no isinstance check at the call site").
"""

from __future__ import annotations

import pytest

from ravn.adapters.memory.mimir_facts import MimirFactCapturingMemory
from ravn.adapters.memory.sqlite import SqliteMemoryAdapter
from ravn.cli.runtime_builders import _with_mimir_fact_capture


class FakeMimir:
    def __init__(self) -> None:
        self.pages: list[tuple[str, str]] = []

    async def upsert_page(self, path: str, content: str) -> None:
        self.pages.append((path, content))


@pytest.mark.asyncio
async def test_facts_reach_mimir_through_the_memory_port() -> None:
    mimir = FakeMimir()
    memory = MimirFactCapturingMemory(mimir)

    await memory.process_inline_facts("session-1", "Remember that valhalla drains on Fridays.")

    assert len(mimir.pages) == 1
    path, content = mimir.pages[0]
    assert path.startswith("memory/directives/")
    assert "valhalla" in content


@pytest.mark.asyncio
async def test_non_fact_input_writes_nothing() -> None:
    mimir = FakeMimir()
    memory = MimirFactCapturingMemory(mimir)

    await memory.process_inline_facts("session-1", "Pod api-7f4 is in CrashLoopBackOff.")

    assert mimir.pages == []


@pytest.mark.asyncio
async def test_episodes_still_reach_the_inner_backend(tmp_path) -> None:
    # The case a replace-instead-of-wrap design would break: sqlite AND Mimir
    # configured together means episodes for prefetch *and* facts as compiled
    # truth, not one at the cost of the other.
    from datetime import UTC, datetime

    from ravn.domain.models import Episode, Outcome

    mimir = FakeMimir()
    inner = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))
    memory = MimirFactCapturingMemory(mimir, inner=inner)

    await memory.record_episode(
        Episode(
            episode_id="ep-1",
            session_id="s-1",
            timestamp=datetime.now(UTC),
            summary="Investigated a crashlooping pod.",
            task_description="pod triage",
            tools_used=["kubernetes_inspect"],
            outcome=Outcome.SUCCESS,
            tags=["k8s"],
        )
    )
    await memory.process_inline_facts("s-1", "Remember that noatun has no GPU nodes.")

    assert await memory.count_episodes() == 1
    assert len(mimir.pages) == 1


@pytest.mark.asyncio
async def test_retractions_are_captured_too() -> None:
    mimir = FakeMimir()
    memory = MimirFactCapturingMemory(mimir)

    await memory.process_inline_facts("s-1", "Actually no, ignore what I said about the drain.")

    assert mimir.pages[0][0].startswith("memory/retractions/")


@pytest.mark.asyncio
async def test_capture_survives_a_failing_mimir() -> None:
    # Fact capture must never interrupt a turn — the guarantee the agent-side
    # try/except used to provide, now owned by the adapter.
    class BrokenMimir:
        async def upsert_page(self, path: str, content: str) -> None:
            raise RuntimeError("mimir down")

    memory = MimirFactCapturingMemory(BrokenMimir())

    assert await memory.process_inline_facts("s-1", "Remember that this must not raise.") == []


def test_composition_passes_memory_through_when_mimir_is_absent() -> None:
    sentinel = object()

    assert _with_mimir_fact_capture(sentinel, None) is sentinel


def test_composition_wraps_when_mimir_is_present() -> None:
    inner = object()

    wrapped = _with_mimir_fact_capture(inner, FakeMimir())

    assert isinstance(wrapped, MimirFactCapturingMemory)
    assert wrapped._inner is inner


def test_mimir_without_a_memory_backend_still_captures_facts() -> None:
    # backend: none + Mimir wired. Previously handled by the agent's `if`
    # branch; a wrapper that required an inner adapter would drop it.
    wrapped = _with_mimir_fact_capture(None, FakeMimir())

    assert isinstance(wrapped, MimirFactCapturingMemory)
    assert wrapped._inner is None


@pytest.mark.asyncio
async def test_wrapper_is_inert_for_episodes_when_there_is_no_inner() -> None:
    memory = MimirFactCapturingMemory(FakeMimir())

    assert await memory.prefetch("anything") == ""
    assert await memory.query_episodes("anything") == []
    assert await memory.search_sessions("anything") == []
    assert await memory.count_episodes() == 0
    assert memory.get_shared_context() is None
    assert memory.extra_tools("s-1") == []
    await memory.close()
