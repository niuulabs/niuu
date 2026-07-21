"""Skuld composition adapter for shared room/mesh collaboration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from niuu.collaboration.mesh import MeshCollaborationBridge
from sleipnir.ports.events import SleipnirSubscriber

if TYPE_CHECKING:
    from skuld.collaboration_adapter import RoomAdapter


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class RoomMeshBridge(MeshCollaborationBridge):
    """Connect the shared mesh bridge to Skuld's room surface adapter."""

    def __init__(
        self,
        subscriber: SleipnirSubscriber,
        room_bridge: RoomAdapter,
        session_id: str | None = None,
        environment_id: str = "",
        patterns: list[str] | None = None,
        report_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._usage_reporter = report_usage
        self._reported_usage_ids: set[str] = set()
        super().__init__(
            subscriber,
            handle_frame=room_bridge.handle_collaboration_frame,
            register_peer=room_bridge.register_mesh_peer,
            has_participant=room_bridge.has_participant,
            session_id=session_id,
            environment_id=environment_id,
            patterns=patterns,
            report_usage=self.report_usage,
        )

    async def report_usage(self, usage: dict[str, Any]) -> None:
        """Adapt projected Ravn usage to Skuld's runtime-result schema."""
        if self._usage_reporter is None:
            return
        usage_id = str(usage.get("usage_id") or "")
        if usage_id and usage_id in self._reported_usage_ids:
            return
        if usage_id:
            self._reported_usage_ids.add(usage_id)

        model = str(usage.get("model") or "unknown")
        model_usage: dict[str, Any] = {
            "inputTokens": _as_int(usage.get("inputTokens")),
            "outputTokens": _as_int(usage.get("outputTokens")),
            "cacheReadInputTokens": _as_int(usage.get("cacheReadInputTokens")),
            "cacheCreationInputTokens": _as_int(usage.get("cacheCreationInputTokens")),
        }
        if usage.get("costUSD") is not None:
            model_usage["costUSD"] = float(usage["costUSD"])
        await self._usage_reporter({"modelUsage": {model: model_usage}})


__all__ = ["RoomMeshBridge"]
