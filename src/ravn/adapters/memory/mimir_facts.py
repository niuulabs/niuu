"""Memory adapter that captures inline facts into Mímir.

``MemoryPort.process_inline_facts`` documents its own contract as "called
unconditionally at the start of run_turn() — no isinstance check at the call
site", and then notes that the agent bypasses it and calls
``inline_facts.detect_and_write()`` directly whenever a Mímir adapter happens
to be wired. The agent was choosing a persistence backend by asking which
piece of infrastructure it had been handed, which is the decision the port
exists to take away from it.

This adapter puts that choice back in composition. It wraps an optional inner
memory adapter, delegates everything to it, and overrides one hook to write
facts to Mímir. Composition wires it when Mímir is present; the agent makes a
single unconditional call and knows nothing about either backend.

Wrapping rather than replacing matters: with sqlite *and* Mímir configured a
resident needs both behaviours — episodes to sqlite for prefetch, facts to
Mímir as compiled truth — and only a decorator gives both without the agent
holding two references and picking between them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ravn.adapters.memory.inline_facts import detect_and_write
from ravn.domain.models import Episode, EpisodeMatch, SessionSummary, SharedContext
from ravn.ports.memory import MemoryPort

if TYPE_CHECKING:
    from niuu.ports.mimir import MimirPort
    from ravn.ports.tool import ToolPort


class MimirFactCapturingMemory(MemoryPort):
    """Delegating memory adapter that writes inline facts to Mímir.

    *inner* is the configured episodic backend, or ``None`` when memory is
    disabled but Mímir is still wired — the configuration in which the agent
    previously captured facts with no memory adapter at all, and which a
    non-wrapping design would silently drop.
    """

    def __init__(self, mimir: MimirPort, *, inner: MemoryPort | None = None) -> None:
        self._mimir = mimir
        self._inner = inner

    # -- the one behaviour this adapter adds ---------------------------------

    async def process_inline_facts(self, session_id: str, user_input: str) -> list:
        # Errors are swallowed inside detect_and_write: fact capture must never
        # interrupt a turn. Kept identical to the agent-side call it replaces.
        await detect_and_write(user_input, self._mimir)
        if self._inner is None:
            return []
        return await self._inner.process_inline_facts(session_id, user_input)

    # -- everything else is the inner adapter's job --------------------------

    async def record_episode(self, episode: Episode) -> None:
        if self._inner is not None:
            await self._inner.record_episode(episode)

    async def query_episodes(
        self,
        query: str,
        *,
        limit: int = 5,
        min_relevance: float = 0.3,
    ) -> list[EpisodeMatch]:
        if self._inner is None:
            return []
        return await self._inner.query_episodes(query, limit=limit, min_relevance=min_relevance)

    async def prefetch(self, context: str) -> str:
        if self._inner is None:
            return ""
        return await self._inner.prefetch(context)

    async def search_sessions(self, query: str, *, limit: int = 3) -> list[SessionSummary]:
        if self._inner is None:
            return []
        return await self._inner.search_sessions(query, limit=limit)

    def inject_shared_context(self, context: SharedContext) -> None:
        if self._inner is not None:
            self._inner.inject_shared_context(context)

    def get_shared_context(self) -> SharedContext | None:
        if self._inner is None:
            return None
        return self._inner.get_shared_context()

    def extra_tools(self, session_id: str) -> list[ToolPort]:
        if self._inner is None:
            return []
        return self._inner.extra_tools(session_id)

    async def count_episodes(self) -> int:
        if self._inner is None:
            return 0
        return await self._inner.count_episodes()

    async def on_turn_complete(
        self,
        session_id: str,
        user_input: str,
        response_summary: str,
    ) -> None:
        if self._inner is None:
            return
        await self._inner.on_turn_complete(session_id, user_input, response_summary)

    def get_rolling_summary(self, session_id: str) -> str:
        if self._inner is None:
            return ""
        return self._inner.get_rolling_summary(session_id)

    async def close(self) -> None:
        if self._inner is not None:
            await self._inner.close()

    def __getattr__(self, name: str) -> Any:
        # Backend-specific attributes (_rolling_summary_max_chars is set on the
        # adapter after construction by _build_memory, and tests reach for
        # internals) must still resolve through the wrapper.
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)
