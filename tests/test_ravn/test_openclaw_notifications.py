"""The OpenClaw shim serves the resident's notification outbox to Forge.

Wire tests on a real socket, like the rest of the shim's: ``notifications.subscribe``
/ ``.pull`` / ``.ack``, the ``notifications.changed`` event, the advertised method
list, and that a notification the agent sends from a turn names that turn's channel.
"""

from __future__ import annotations

import asyncio

from niuu.domain.notifications import NotificationDraft, NotificationKind
from niuu.domain.resident_notifications import AttentionInputKind, ResidentNotificationEntry
from ravn.adapters.tools.notify import ResidentNotifyTool
from tests.test_ravn.fake_lexichat_client import FakeLexiChatClient
from tests.test_ravn.test_openclaw_gateway import (
    TOKEN,
    StubRavnGateway,
    _Event,
    running_shim,
)


class TestShimServesTheOutbox:
    async def test_methods_are_advertised_only_with_an_outbox(self, tmp_path, open_outbox) -> None:
        async with running_shim(tmp_path / "a") as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                assert "notifications.pull" not in client.hello["features"]["methods"]
                refused = await client.request("notifications.pull", {})
                assert refused["ok"] is False
                assert refused["error"]["code"] == "UNSUPPORTED"
        (tmp_path / "b").mkdir()
        async with running_shim(
            tmp_path / "b", notifications=open_outbox(tmp_path / "b" / "state.db")
        ) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                methods = set(client.hello["features"]["methods"])
                assert {
                    "notifications.subscribe",
                    "notifications.pull",
                    "notifications.ack",
                } <= methods
                assert "chat.send" in methods

    async def test_pull_then_ack_drains_it(self, tmp_path, open_outbox) -> None:
        outbox = open_outbox(tmp_path / "state.db")
        await outbox.attention(
            request_id="q1", group="case", input_kind=AttentionInputKind.QUESTION, prompt="A?"
        )
        await outbox.notify(
            draft=NotificationDraft(kind=NotificationKind.INFO, title="FYI"), idempotency_key="k"
        )
        async with running_shim(tmp_path, notifications=outbox) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                pulled = await client.request("notifications.pull", {"limit": 1})
                [first] = [
                    ResidentNotificationEntry.model_validate(row)
                    for row in pulled["payload"]["entries"]
                ]
                assert first.request_id == "q1"

                acked = await client.request("notifications.ack", {"throughSeq": first.seq})
                assert acked["payload"] == {"ok": True, "acknowledged": 1}
                rest = (await client.request("notifications.pull", {}))["payload"]["entries"]
                assert [row["idempotency_key"] for row in rest] == ["k"]

    async def test_bad_arguments_are_refused_without_dropping_the_socket(
        self, tmp_path, open_outbox
    ) -> None:
        async with running_shim(tmp_path, notifications=open_outbox(tmp_path / "state.db")) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                for method, params in (
                    ("notifications.pull", {"limit": 0}),
                    ("notifications.pull", {"limit": True}),
                    ("notifications.ack", {}),
                ):
                    refused = await client.request(method, params)
                    assert refused["ok"] is False
                    assert refused["error"]["code"] == "INVALID_REQUEST"
                assert (await client.request("health"))["ok"] is True

    async def test_subscribers_hear_when_an_entry_is_added(self, tmp_path, open_outbox) -> None:
        outbox = open_outbox(tmp_path / "state.db")
        async with running_shim(tmp_path, notifications=outbox) as shim:
            async with (
                FakeLexiChatClient(url=shim.url, token=TOKEN) as forge,
                FakeLexiChatClient(url=shim.url, token=TOKEN) as phone,
            ):
                await forge.request("notifications.subscribe", {})
                await outbox.attention(
                    request_id="q1", group="g", input_kind=AttentionInputKind.QUESTION, prompt=""
                )
                events = await forge.collect_events(until_event="notifications.changed", timeout=5)
                assert events[-1]["event"] == "notifications.changed"
                await asyncio.sleep(0.05)
                assert all(
                    frame.get("event") != "notifications.changed" for frame in _drain(phone._events)
                ), "only subscribers are told"

    async def test_a_turn_notifies_from_its_own_channel(self, tmp_path, open_outbox) -> None:
        outbox = open_outbox(tmp_path / "state.db")
        tool = ResidentNotifyTool(outbox)

        class NotifyingRavn(StubRavnGateway):
            async def handle_message_stream(self, session_id, text):
                self.seen.append((session_id, text))
                # The agent's turn runs in its own task, as RavnGateway runs it.
                await asyncio.create_task(tool.execute({"kind": "milestone", "title": "Done"}))
                yield _Event("response", {"text": "done"})

        key = "agent:main:niuu-6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b"
        async with running_shim(
            tmp_path, stub=NotifyingRavn(), agent_id="main", notifications=outbox
        ) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                await client.request("sessions.subscribe", {"sessionKey": key})
                await client.request(
                    "chat.send", {"sessionKey": key, "message": "go", "idempotencyKey": "r1"}
                )
                await client.collect_events(until_event="sessions.changed", timeout=10)

        [entry] = await outbox.pending(limit=10)
        assert entry.channel == key
        assert entry.draft is not None and entry.draft.title == "Done"


def _drain(queue: asyncio.Queue) -> list[dict]:
    frames = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames
