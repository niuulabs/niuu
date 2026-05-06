"""Memory port — interface for episodic memory backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ravn.domain.models import (
    Episode,
    EpisodeMatch,
    SessionSummary,
    SharedContext,
)

if TYPE_CHECKING:
    from ravn.ports.tool import ToolPort

# Default maximum characters for the in-memory rolling session summary.
# Override per-instance by setting ``adapter._rolling_summary_max_chars``
# after construction (done by ``_build_memory`` from the loaded config).
_ROLLING_SUMMARY_MAX_CHARS = 2_000


class MemoryPort(ABC):
    """Abstract interface for episodic memory storage and retrieval.

    Implementations provide persistence for agent episodes across sessions,
    enabling continuity of context and the ability to search past work.
    """

    @abstractmethod
    async def record_episode(self, episode: Episode) -> None:
        """Persist a completed episode to the memory store."""
        ...

    @abstractmethod
    async def query_episodes(
        self,
        query: str,
        *,
        limit: int = 5,
        min_relevance: float = 0.3,
    ) -> list[EpisodeMatch]:
        """Search for episodes relevant to *query*.

        Returns at most *limit* results with relevance >= *min_relevance*,
        ordered by descending combined score (relevance × recency × outcome).
        """
        ...

    @abstractmethod
    async def prefetch(self, context: str) -> str:
        """Return a formatted "Relevant Past Context" block for the system prompt.

        Searches memory using *context* (typically the current user input),
        applies recency and outcome weighting, respects the token budget, and
        formats the results as a Markdown block ready for injection.
        Returns an empty string if no relevant episodes are found.
        """
        ...

    @abstractmethod
    async def search_sessions(
        self,
        query: str,
        *,
        limit: int = 3,
    ) -> list[SessionSummary]:
        """Return per-session summaries for sessions matching *query*.

        Two-stage search: FTS5 keyword search across all episodes, then
        grouping and summarisation per session.
        """
        ...

    @abstractmethod
    def inject_shared_context(self, context: SharedContext) -> None:
        """Store a shared blackboard context for this adapter instance."""
        ...

    @abstractmethod
    def get_shared_context(self) -> SharedContext | None:
        """Return the most recently injected shared context, or None."""
        ...

    def extra_tools(self, session_id: str) -> list[ToolPort]:
        """Return any additional agent tools this memory adapter provides.

        Default implementation returns an empty list.  Adapters that offer
        agent-facing tools override this method.  Called once by
        ``_build_tools()`` alongside all other tool registration.
        No isinstance checks needed at the call site.
        """
        return []

    async def process_inline_facts(self, session_id: str, user_input: str) -> list:
        """Detect and persist inline fact patterns from *user_input*.

        Default implementation is a no-op returning an empty list.
        Custom memory adapters may override this for backend-specific handling.
        Called unconditionally at the start of run_turn() — no isinstance
        check at the call site.

        Note: when a Mímir adapter is wired, the agent loop calls
        ``inline_facts.detect_and_write()`` directly, independently of this
        hook.
        """
        return []

    async def count_episodes(self) -> int:
        """Return the total number of stored episodes.

        Returns 0 when the backend does not support counting.  Override in
        concrete adapters to provide an accurate count.
        """
        return 0

    async def on_turn_complete(
        self,
        session_id: str,
        user_input: str,
        response_summary: str,
    ) -> None:
        """Update the rolling session summary after each turn.

        Default implementation maintains an in-memory rolling summary using
        simple truncation — no separate database table required.  Subclasses
        may override to persist the summary across process restarts.
        """
        summaries: dict[str, str] = self.__dict__.setdefault("_rolling_summaries", {})
        max_chars: int = self.__dict__.get("_rolling_summary_max_chars", _ROLLING_SUMMARY_MAX_CHARS)
        existing = summaries.get(session_id, "")
        entry = f"U: {user_input[:200]}\nA: {response_summary[:400]}"
        updated = f"{existing}\n\n{entry}".strip() if existing else entry
        if len(updated) > max_chars:
            updated = updated[-max_chars:]
        summaries[session_id] = updated

    def get_rolling_summary(self, session_id: str) -> str:
        """Return the in-memory rolling summary for *session_id*, or empty string."""
        summaries: dict[str, str] = self.__dict__.get("_rolling_summaries", {})
        return summaries.get(session_id, "")
