"""Room-routed sessions must leave ``provisioning`` without a CLI transport.

Resident sessions never start Skuld's own CLI, so the ``system/init`` frame that
normally moves a session out of ``provisioning`` never arrives. These tests pin the
transitions that replace it.
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from niuu.ports.cli import TransportCapabilities
from skuld.broker import Broker
from skuld.config import SkuldSettings


class TestRoomRoutedStartupLeavesProvisioning:
    async def _startup(self, broker: Broker, transport=None) -> AsyncMock:
        report = AsyncMock(return_value=True)

        async def start_mesh() -> None:
            broker._mesh_adapter = MagicMock(peer_id="skuld-wf")

        if transport is None:
            transport = AsyncMock()
        transport.on_event = MagicMock()
        with (
            patch.object(broker, "_report_activity_state", new=report),
            patch.object(broker, "_hydrate_conversation_history", new=AsyncMock()),
            patch.object(broker, "_create_transport", return_value=transport),
            patch("skuld.broker.ServiceManager") as service_manager_cls,
            patch.object(broker, "_start_mesh_adapter", new=AsyncMock(side_effect=start_mesh)),
        ):
            service_manager_cls.return_value = AsyncMock()
            await broker.startup()
            await asyncio.sleep(0)
        if broker._room_bridge is not None:
            await broker._room_bridge.stop_presence_sweep()
        return report

    @pytest.mark.asyncio
    async def test_resident_session_reports_idle_once_started(self, tmp_path):
        broker = Broker(
            settings=SkuldSettings(
                session={"id": "resident", "workspace_dir": str(tmp_path)},
                room={"enabled": True, "default_target_peer_id": "regin"},
                chronicle_watcher_enabled=False,
            )
        )

        report = await self._startup(broker)

        assert broker.startup_status()["startup_state"] == "ready"
        report.assert_awaited_once_with("idle")

    @staticmethod
    def _cli_broker(tmp_path) -> Broker:
        return Broker(
            settings=SkuldSettings(
                session={"id": "cli", "workspace_dir": str(tmp_path)},
                chronicle_watcher_enabled=False,
            )
        )

    @staticmethod
    def _transport(*, announces_ready: bool) -> AsyncMock:
        transport = AsyncMock()
        transport.capabilities = TransportCapabilities(announces_ready=announces_ready)
        return transport

    @pytest.mark.asyncio
    async def test_cli_session_waits_for_system_init(self, tmp_path):
        broker = self._cli_broker(tmp_path)

        report = await self._startup(broker, self._transport(announces_ready=True))

        report.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cli_that_announces_itself_on_its_first_turn_is_idle_once_started(self, tmp_path):
        """Seen on Valhalla: Claude in stream-json mode sends system/init only after the
        first message, so sessions launched without a prompt read as starting forever."""
        broker = self._cli_broker(tmp_path)
        broker._activity_state = "provisioning"

        report = await self._startup(broker, self._transport(announces_ready=False))

        assert broker.startup_status()["startup_state"] == "ready"
        report.assert_awaited_with("idle")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "adapter",
        [
            "skuld.transports.persistent_subprocess.PersistentSubprocessTransport",
            "skuld.transports.subprocess.SubprocessTransport",
        ],
    )
    async def test_claude_print_mode_transports_say_they_announce_on_first_turn(
        self, tmp_path, adapter
    ):
        module, _, name = adapter.rpartition(".")
        cls = getattr(importlib.import_module(module), name)
        transport = cls(str(tmp_path))

        assert transport.capabilities.announces_ready is False
