"""Tests for the thin Skuld composition around the shared mesh bridge."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.room_mesh_bridge import RoomMeshBridge


@pytest.mark.asyncio
async def test_usage_adapter_preserves_runtime_accounting_shape_and_deduplicates() -> None:
    room = MagicMock()
    room.handle_collaboration_frame = AsyncMock()
    room.register_mesh_peer = AsyncMock()
    room.has_participant.return_value = True
    report = AsyncMock()
    bridge = RoomMeshBridge(
        AsyncMock(),
        room,
        report_usage=report,
    )

    usage = {
        "usage_id": "usage-1",
        "model": "codex",
        "inputTokens": 10,
        "outputTokens": 4,
        "costUSD": 0.25,
    }
    await bridge.report_usage(usage)
    await bridge.report_usage(usage)

    report.assert_awaited_once_with(
        {
            "modelUsage": {
                "codex": {
                    "inputTokens": 10,
                    "outputTokens": 4,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                    "costUSD": 0.25,
                }
            }
        }
    )
