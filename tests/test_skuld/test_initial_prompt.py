"""The session's first message reaches the harness once, or the owner is told why not.

The claims are served by the real Forge route, so these fail if Skuld and Forge disagree.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import Broker, ConversationTurn
from skuld.config import SkuldSettings
from skuld.delivery_errors import DeliveryNotAcceptedError
from skuld.event_log import ForgeCredentialError
from tests.test_adapters.test_rest_session_log import InMemoryLog, allow_log_access
from volundr.adapters.inbound.rest_message_delivery import create_message_delivery_router
from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.domain.message_delivery import (
    MessageDelivery,
    MessageDeliveryConflictError,
    MessageDeliveryRepository,
)

SID = "468b74c4-a05d-57c5-afd8-f27b1a6e2191"
PROMPT = "Fix the login bug"


class InMemoryDeliveries(MessageDeliveryRepository):
    """The claim table, with the rules ``PostgresMessageDelivery`` enforces."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def claim(self, session_id, request_id, payload_hash, claim_token):
        row = self.rows.setdefault(
            request_id,
            {"hash": payload_hash, "token": claim_token, "status": "pending", "error": None},
        )
        if row["hash"] != payload_hash:
            raise MessageDeliveryConflictError("request_id already belongs to different content")
        claimed = row["token"] == claim_token and row["status"] == "pending"
        return MessageDelivery(request_id, row["status"], claimed=claimed, error=row["error"])

    async def settle(self, session_id, request_id, claim_token, status, error=None):
        row = self.rows[request_id]
        assert row["token"] == claim_token
        row.update(status=status, error=error)
        return MessageDelivery(request_id, status, error=error)

    async def get(self, session_id, request_id):
        row = self.rows.get(request_id)
        if row is None:
            return None
        return MessageDelivery(request_id, row["status"], error=row["error"])

    def statuses(self) -> list[str]:
        return [row["status"] for row in self.rows.values()]


class Forge:
    """A session's Forge across broker restarts: its log, its claims and its workspace."""

    def __init__(self, tmp_path, *, reachable: bool = True) -> None:
        self.tmp_path = tmp_path
        self.url = "http://forge.test" if reachable else ""
        self.log = InMemoryLog()
        self.deliveries = InMemoryDeliveries()
        app = FastAPI()
        app.include_router(
            create_session_log_router(self.log, session_service=allow_log_access(app))
        )
        app.include_router(
            create_message_delivery_router(
                self.deliveries,
                MagicMock(
                    **{
                        "get_session": AsyncMock(),
                        "_check_access": AsyncMock(),
                    }
                ),
            )
        )
        self.asgi = httpx.ASGITransport(app=app)

    async def boot(self, transport, *, prompt: str = PROMPT, keep_files: bool = True, **settings):
        """A new broker process of the session, with its history read back."""
        workspace = self.tmp_path if keep_files else self.tmp_path / "fresh"
        workspace.mkdir(exist_ok=True)
        broker = Broker(
            SkuldSettings(
                session={"id": SID, "workspace_dir": str(workspace), "initial_prompt": prompt},
                volundr_api_url=self.url,
                transport="subprocess",
                chronicle_watcher_enabled=False,
                delivery={"max_attempts": 2, "initial_backoff_seconds": 0.0},
                **settings,
            )
        )
        broker._transport = transport
        broker._http_client = httpx.AsyncClient(base_url="http://forge.test", transport=self.asgi)
        broker._apply_retrieval_reflex = AsyncMock(side_effect=lambda message: message)
        broker.EVENT_LOG_RESUME_INITIAL_DELAY = 0.0
        broker._load_conversation_history()
        await broker._hydrate_conversation_history()
        return broker

    async def run(self, transport, **kwargs) -> Broker:
        broker = await self.boot(transport, **kwargs)
        await broker._auto_start_transport()
        return broker


@pytest.fixture
def forge(tmp_path) -> Forge:
    return Forge(tmp_path)


@pytest.fixture(autouse=True)
def forge_principal(monkeypatch):
    monkeypatch.setattr(
        "volundr.adapters.inbound.rest_message_delivery.extract_principal", AsyncMock()
    )


def harness(*, send=None, start=None, alive=True, **capabilities) -> MagicMock:
    transport = MagicMock()
    transport.is_alive = alive
    transport.session_id = None
    transport.capabilities = TransportCapabilities(**capabilities)
    transport.start = AsyncMock(side_effect=start)
    transport.stop = AsyncMock()
    transport.send_message = AsyncMock(side_effect=send, return_value=True)
    return transport


def sent(transport) -> list[str]:
    return [call.args[0] for call in transport.send_message.await_args_list]


def prompt_turns(broker) -> list[ConversationTurn]:
    return [t for t in broker._conversation_turns if t.role == "user" and t.content == PROMPT]


def frames(broker, kind) -> list[dict]:
    return [e["payload"] for e in broker._event_log_buffer if e["kind"] == kind]


async def test_the_prompt_is_sent_once_the_harness_runs(forge):
    transport = harness(alive=False)

    broker = await forge.run(transport)

    transport.start.assert_awaited_once()
    assert sent(transport) == [PROMPT]
    assert len(prompt_turns(broker)) == 1
    assert len(frames(broker, "user_confirmed")) == 1
    assert len(frames(broker, "user_delivered")) == 1
    assert forge.deliveries.statuses() == ["delivered"]


async def test_the_turn_is_shown_before_the_harness_is_up(forge):
    seen = []
    transport = harness(alive=False, start=lambda: seen.extend(prompt_turns(broker)))
    broker = await forge.boot(transport)

    await broker._auto_start_transport()

    assert [t.metadata["steering_state"] for t in seen] == ["pending"]


async def test_no_prompt_only_warms_the_harness(forge):
    transport = harness(alive=False)

    broker = await forge.run(transport, prompt="")

    transport.start.assert_awaited_once()
    assert sent(transport) == []
    assert broker._conversation_turns == []


@pytest.mark.parametrize("keep_files", [True, False])
async def test_a_delivered_prompt_is_never_sent_again(forge, keep_files):
    await forge.run(harness())
    again = harness(alive=False)

    broker = await forge.run(again, keep_files=keep_files)

    assert sent(again) == []
    again.start.assert_awaited_once()  # the harness is still warmed for the next message
    assert frames(broker, "user_confirmed") == []
    assert frames(broker, "user_delivery_failed") == []


async def test_a_harness_that_does_not_start_is_told_and_the_next_start_sends_it(forge):
    first = await forge.run(harness(alive=False, start=RuntimeError("no credentials")))

    assert prompt_turns(first)[0].metadata["steering_state"] == "failed"
    [failure] = frames(first, "user_delivery_failed")
    assert "The agent did not start" in failure["error"]
    assert "no credentials" in failure["error"]
    assert frames(first, "error")[0]["code"] == "user_delivery_failed"
    assert forge.deliveries.rows == {}

    retry = harness()
    second = await forge.run(retry)

    assert sent(retry) == [PROMPT]
    assert len(prompt_turns(second)) == 1  # the same turn, not a second bubble
    assert frames(second, "user_confirmed")[0]["steering_state"] == "pending"
    assert forge.deliveries.statuses() == ["delivered"]


async def test_a_broker_that_died_before_claiming_left_the_prompt_to_send(forge):
    broker = await forge.boot(harness())
    await broker._open_initial_prompt_turn(PROMPT, None)  # killed while the harness came up

    retry = harness()
    again = await forge.run(retry)

    assert sent(retry) == [PROMPT]
    assert len(prompt_turns(again)) == 1


async def test_a_failed_turn_rebuilt_from_the_durable_log_is_the_one_sent_again(forge):
    first = await forge.boot(harness(alive=False, start=RuntimeError("no credentials")))
    await first._init_event_log()
    await first._auto_start_transport()
    await first._stop_event_log()
    assert "user_delivery_failed" in [entry.kind for entry in await forge.log.read_after(UUID(SID))]

    # The workspace is gone: all that is left of the conversation is Forge's log.
    retry = harness()
    second = await forge.boot(retry, keep_files=False)
    assert prompt_turns(second)[0].metadata["steering_state"] == "failed"
    await second._init_event_log()
    await second._auto_start_transport()
    await second._stop_event_log()

    assert sent(retry) == [PROMPT]
    third = await forge.boot(harness())
    assert [t.metadata["steering_state"] for t in prompt_turns(third)] == ["pending"]


async def test_a_prompt_the_harness_refused_is_sent_by_the_next_start(forge):
    refused = await forge.run(harness(send=DeliveryNotAcceptedError("not ready for input")))
    assert prompt_turns(refused)[0].metadata["steering_state"] == "failed"
    assert forge.deliveries.statuses() == ["failed"]

    for outcome in (DeliveryNotAcceptedError("still not ready"), None):
        retry = harness(send=outcome)
        await forge.run(retry)
        assert set(sent(retry)) == {PROMPT}

    assert forge.deliveries.statuses() == ["failed", "failed", "delivered"]
    settled = harness()
    await forge.run(settled)
    assert sent(settled) == []


async def test_an_unconfirmed_prompt_is_not_repeated_and_the_owner_is_told(forge):
    # The harness died after taking the input: it may have acted on it.
    await forge.run(harness(send=ConnectionResetError("broken pipe")))
    assert forge.deliveries.statuses() == ["pending"]

    again = harness()
    broker = await forge.run(again)

    assert sent(again) == []
    [failure] = frames(broker, "user_delivery_failed")
    assert "nothing confirmed" in failure["error"]
    assert prompt_turns(broker)[0].metadata["steering_state"] == "failed"

    # Told once, not on every restart.
    third = await forge.run(harness())
    assert frames(third, "user_delivery_failed") == []


async def test_an_unconfirmed_prompt_is_told_even_when_the_history_is_gone(forge):
    await forge.run(harness(send=ConnectionResetError("broken pipe")))

    broker = await forge.run(harness(), keep_files=False)

    assert len(prompt_turns(broker)) == 1
    assert len(frames(broker, "user_delivery_failed")) == 1


@pytest.mark.parametrize("claim", ["pending", "failed", None])
async def test_a_conversation_that_went_on_is_never_given_the_prompt_again(forge, claim):
    broker = await forge.boot(harness())
    _, request_id = await broker._initial_prompt_status(PROMPT)
    if claim:
        forge.deliveries.rows[request_id] = {
            "hash": "",
            "token": None,
            "status": claim,
            "error": "",
        }
    turn = await broker._open_initial_prompt_turn(PROMPT, request_id)
    broker._append_turn(ConversationTurn(id="a1", role="assistant", content="Done."))
    turn.metadata["steering_state"] = "pending"
    broker._save_conversation_history()

    again = harness()
    restarted = await forge.run(again)

    assert sent(again) == []
    assert frames(restarted, "user_delivery_failed") == []


async def test_a_prompt_the_agent_took_is_not_reported_as_unconfirmed(forge):
    broker = await forge.boot(harness())
    _, request_id = await broker._initial_prompt_status(PROMPT)
    forge.deliveries.rows[request_id] = {
        "hash": "",
        "token": None,
        "status": "pending",
        "error": "",
    }
    turn = await broker._open_initial_prompt_turn(PROMPT, request_id)
    turn.metadata["steering_state"] = "active"
    broker._save_conversation_history()

    restarted = await forge.run(harness())

    assert frames(restarted, "user_delivery_failed") == []


async def test_what_others_said_in_the_room_does_not_replace_the_first_turn(forge):
    broker = await forge.boot(harness())
    broker._append_turn(
        ConversationTurn(id="p1", role="assistant", content="hello", participant_id="peer")
    )

    again = harness()
    await forge.run(again)

    assert sent(again) == [PROMPT]


async def test_a_turn_recorded_before_turn_ids_were_fixed_is_reused(forge):
    broker = await forge.boot(harness())
    broker._append_turn(ConversationTurn(id="legacy", role="user", content=PROMPT))

    again = harness()
    restarted = await forge.run(again)

    assert sent(again) == [PROMPT]
    assert [t.id for t in prompt_turns(restarted)] == ["legacy"]


async def test_a_session_that_takes_no_messages_says_so(forge):
    transport = harness(alive=False, send_message=False)

    broker = await forge.run(transport)

    transport.start.assert_awaited_once()  # the pairing link is still brought up
    assert sent(transport) == []
    assert "takes no messages" in frames(broker, "user_delivery_failed")[0]["error"]
    assert forge.deliveries.rows == {}


async def test_a_room_with_no_agent_of_its_own_says_so_once(forge):
    broker = await forge.boot(harness(), room={"enabled": True, "routed": True})

    await broker._report_unroutable_initial_prompt()

    assert "no agent of its own" in frames(broker, "user_delivery_failed")[0]["error"]
    again = await forge.boot(harness(), room={"enabled": True, "routed": True})
    await again._report_unroutable_initial_prompt()
    assert frames(again, "user_delivery_failed") == []


@pytest.mark.parametrize("prompt", [PROMPT, ""])
async def test_a_routed_room_reports_at_startup_and_starts_no_harness(forge, monkeypatch, prompt):
    broker = await forge.boot(
        harness(alive=False), prompt=prompt, room={"enabled": True, "routed": True}
    )
    monkeypatch.setattr(broker, "_create_transport", lambda: broker._transport)
    for name in ("_ensure_session_trace_started", "_init_telegram_channel", "_init_event_log"):
        monkeypatch.setattr(broker, name, AsyncMock())
    manager = MagicMock(init=AsyncMock())
    monkeypatch.setattr("skuld.transport_lifecycle.ServiceManager", lambda _workspace: manager)

    await broker.startup()

    broker._transport.start.assert_not_awaited()
    assert len(frames(broker, "user_delivery_failed")) == (1 if prompt else 0)
    await broker.shutdown()


async def test_a_forge_outage_at_boot_is_waited_out(forge, caplog):
    transport = harness()
    broker = await forge.boot(transport)
    read = broker._read_initial_prompt_claim
    broker._read_initial_prompt_claim = AsyncMock(
        side_effect=[httpx.ConnectError("down"), _refused(503), await read("initial-prompt-x")]
    )

    await broker._auto_start_transport()

    assert sent(transport) == [PROMPT]
    assert "trying again" in caplog.text


async def test_a_warm_start_that_fails_is_logged(forge, caplog):
    await forge.run(harness(alive=False, start=RuntimeError("no credentials")), prompt="")

    assert "Transport auto-start failed" in caplog.text


@pytest.mark.parametrize(("transient", "expected"), [(True, [PROMPT]), (False, [])])
async def test_a_missing_forge_credential_is_waited_out_only_while_it_can_come(
    forge, transient, expected
):
    transport = harness()
    broker = await forge.boot(transport)
    broker._read_initial_prompt_claim = AsyncMock(
        side_effect=[ForgeCredentialError("no token", transient=transient), None]
    )

    await broker._auto_start_transport()

    assert sent(transport) == expected


async def test_an_answer_that_names_no_delivery_status_is_told_to_the_owner(forge):
    transport = harness()
    broker = await forge.boot(transport)
    answer = httpx.Response(200, json={}, request=httpx.Request("GET", "http://forge.test"))
    broker._http_client = MagicMock(
        get=AsyncMock(return_value=answer), post=AsyncMock(return_value=answer)
    )

    await broker._auto_start_transport()

    assert sent(transport) == []
    assert "Invalid delivery status" in frames(broker, "user_delivery_failed")[0]["error"]


@pytest.mark.parametrize("failing", ["read", "claim"])
async def test_a_forge_that_refuses_is_told_to_the_owner(forge, monkeypatch, failing):
    transport = harness()
    broker = await forge.boot(transport)
    target = {"read": "read_delivery", "claim": "claim_message"}[failing]
    monkeypatch.setattr(f"skuld.initial_prompt.{target}", AsyncMock(side_effect=_refused(403)))

    await broker._auto_start_transport()

    assert sent(transport) == []
    assert "Forge could not" in frames(broker, "user_delivery_failed")[0]["error"]


async def test_a_claim_another_broker_holds_is_not_sent_twice(forge, monkeypatch):
    transport = harness()
    broker = await forge.boot(transport)
    monkeypatch.setattr(
        "skuld.initial_prompt.claim_message",
        AsyncMock(return_value={"claimed": False, "status": "pending"}),
    )

    await broker._auto_start_transport()

    assert sent(transport) == []
    assert "Another broker" in frames(broker, "user_delivery_failed")[0]["error"]


async def test_an_unexpected_failure_is_told_to_the_owner(forge):
    transport = harness()
    broker = await forge.boot(transport)
    broker._claim_initial_prompt = AsyncMock(side_effect=KeyError("surprise"))

    await broker._auto_start_transport()

    assert sent(transport) == []
    assert "surprise" in frames(broker, "user_delivery_failed")[0]["error"]


@pytest.mark.parametrize(
    ("state", "expected", "told"),
    [("active", [], 0), ("failed", [PROMPT], 0), ("pending", [], 1), (None, [], 0)],
)
async def test_without_a_forge_the_local_history_decides(tmp_path, state, expected, told):
    forge = Forge(tmp_path, reachable=False)
    broker = await forge.boot(harness())
    turn = await broker._open_initial_prompt_turn(PROMPT, None)
    turn.metadata["steering_state"] = state
    broker._save_conversation_history()

    again = harness()
    again_broker = await forge.boot(again)
    again_broker._fail_user_delivery = AsyncMock(wraps=again_broker._fail_user_delivery)
    await again_broker._auto_start_transport()

    assert sent(again) == expected
    assert again_broker._fail_user_delivery.await_count == told


async def test_without_a_forge_a_new_prompt_is_sent(tmp_path):
    forge = Forge(tmp_path, reachable=False)
    transport = harness()

    await forge.run(transport)

    assert sent(transport) == [PROMPT]


def _refused(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://forge.test")
    return httpx.HTTPStatusError(
        str(status), request=request, response=httpx.Response(status, request=request)
    )
