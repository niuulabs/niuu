"""TaskContextChannel — rewrites emitted events with task/session context."""

from __future__ import annotations

from dataclasses import replace

from ravn.domain.events import RavnEvent
from ravn.ports.channel import ChannelPort


class TaskContextChannel(ChannelPort):
    """Attach stable task/session ids to emitted agent activity events.

    Mesh-driven ravn tasks should publish activity with the root Volundr
    session correlation so downstream room bridges can associate thought,
    tool, and response events with the visible raid session rather than the
    agent's ephemeral in-memory conversation UUID.
    """

    def __init__(
        self,
        channel: ChannelPort,
        *,
        correlation_id: str,
        session_id: str,
        task_id: str,
        root_correlation_id: str,
    ) -> None:
        self._channel = channel
        self._correlation_id = correlation_id
        self._session_id = session_id
        self._task_id = task_id
        self._root_correlation_id = root_correlation_id

    async def emit(self, event: RavnEvent) -> None:
        await self._channel.emit(
            replace(
                event,
                correlation_id=self._correlation_id,
                session_id=self._session_id,
                task_id=self._task_id,
                root_correlation_id=self._root_correlation_id,
            )
        )
