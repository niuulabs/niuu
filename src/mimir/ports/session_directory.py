"""SessionDirectory — who a session is, as Forge says (contract C1).

The gateway asks on every call, with the session's own credential, so Forge's
live-session check holds: a stopped session's memory access ends with it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from mimir.memory.models import SessionMemoryContext


class SessionNotConfirmedError(Exception):
    """Forge did not confirm the credential for this session (refused, gone, or unreachable).

    ``reason`` says which, for the 401 the gateway answers with.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SessionDirectory(ABC):
    @abstractmethod
    async def memory_context(self, session_id: str, credential: str) -> SessionMemoryContext:
        """The session's owner, tenant, project and write prefixes.

        Raises:
            SessionNotConfirmedError: on any answer but a well-formed 200 for *session_id*.
        """

    async def close(self) -> None:  # noqa: B027 — optional hook
        """Release connections."""
