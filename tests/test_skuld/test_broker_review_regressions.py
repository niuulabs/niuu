"""Failure-path acceptance for the Fable review's broker contract findings."""

import asyncio
import hashlib
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import WebSocketDisconnect

from niuu.ports.cli import TransportCapabilities
from skuld.broker import Broker
from skuld.channels import WebSocketChannel
from skuld.config import SkuldSettings
from skuld.control_errors import ControlRecoveryError, control_error_frame
from skuld.control_state import load_control_state, pending_controls, save_control_state
from skuld.delivery_claims import DeliveryClaimError, claim_message, settle_message
from skuld.live_frames import prepare_live_frame
from skuld.transports.codex_ws import CodexWebSocketTransport
from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.test_skuld.test_tmux_interactive_transport import _collect_events, _send_keys
from tests.test_skuld.test_tmux_native_question_consumption import NativeQuestionTransport


def _broker(tmp_path, *, api_url="http://forge.test"):
    broker = Broker(
        SkuldSettings(
            session={"id": "review-session", "workspace_dir": str(tmp_path)},
            volundr_api_url=api_url,
            delivery={"max_attempts": 2, "attempt_timeout_seconds": 0.2},
        )
    )
    transport = MagicMock()
    transport.is_alive = True
    transport.capabilities = TransportCapabilities()
    transport.send_message = AsyncMock()
    transport.send_control = AsyncMock()
    broker._transport = transport
    broker._apply_retrieval_reflex = AsyncMock(side_effect=lambda message: message)
    for method in (
        "_report_activity_state",
        "_report_usage",
        "_report_timeline_event",
        "_on_result_publish_mesh",
        "_report_session_start",
        "_complete_trace_span",
        "_emit_pipeline_event",
    ):
        setattr(broker, method, AsyncMock())
    return broker


async def _finish_deliveries():
    tasks = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name().startswith("transport-deliver-")
    ]
    if tasks:
        await asyncio.gather(*tasks)


class _Ledger:
    def __init__(self):
        self.rows = {}
        self.requests = []
        self.fail_claim = False
        self.fail_settle = False

    def handle(self, request):
        payload = json.loads(request.content)
        self.requests.append((request.url.path, payload))
        identity = request.url.path.split("/")[-2]
        if request.url.path.endswith("/claim"):
            if self.fail_claim:
                raise httpx.ReadTimeout("claim response lost", request=request)
            old = self.rows.get(identity)
            if old and old["payload_hash"] != payload["payload_hash"]:
                return httpx.Response(409)
            if old is None:
                old = {**payload, "status": "pending", "request_id": identity}
                self.rows[identity] = old
            return httpx.Response(
                200,
                json={
                    "claimed": old["claim_token"] == payload["claim_token"],
                    "status": old["status"],
                    "request_id": identity,
                },
            )
        if self.fail_settle:
            return httpx.Response(503)
        old = self.rows[identity]
        assert old["claim_token"] == payload["claim_token"]
        old["status"] = payload["status"]
        return httpx.Response(200, json={"status": old["status"]})

    def client(self):
        return httpx.AsyncClient(
            base_url="http://forge.test", transport=httpx.MockTransport(self.handle)
        )


def _events(broker, kind):
    return [row["payload"] for row in broker._event_log_buffer if row["kind"] == kind]


async def test_duplicate_pending_then_delivered_and_restart_never_redispatch(tmp_path):
    ledger = _Ledger()
    broker = _broker(tmp_path)
    release = asyncio.Event()
    broker._transport.send_message = AsyncMock(side_effect=lambda *a, **kw: None)

    async def native_send(*args, **kwargs):
        await release.wait()

    broker._transport.send_message.side_effect = native_send
    message = {"content": "Run the review", "request_id": "same-request"}
    async with ledger.client() as client:
        broker._http_client = client
        await broker._dispatch_browser_message(message)
        await broker._dispatch_browser_message(message)
        assert len(broker._conversation_turns) == 1
        assert _events(broker, "user_delivery_pending")[-1]["status"] == "pending"
        release.set()
        await _finish_deliveries()
        broker._transport.send_message.assert_awaited_once()
        original_id = broker._conversation_turns[0].id
        assert ledger.rows["same-request"]["status"] == "delivered"
        await broker._dispatch_browser_message(message)
        assert _events(broker, "user_delivered")[-1]["id"] == original_id
        restarted = _broker(tmp_path)
        restarted._http_client = client
        await restarted._dispatch_browser_message(message)
        restarted._transport.send_message.assert_not_called()
        assert restarted._conversation_turns == []
        assert _events(restarted, "user_delivered")[-1]["id"] == original_id


async def test_reusing_request_id_with_different_content_is_rejected(tmp_path):
    ledger = _Ledger()
    broker = _broker(tmp_path)
    async with ledger.client() as client:
        broker._http_client = client
        await broker._dispatch_browser_message({"content": "first", "request_id": "request"})
        await _finish_deliveries()
        with pytest.raises(DeliveryClaimError) as caught:
            await broker._dispatch_browser_message({"content": "other", "request_id": "request"})
    assert caught.value.code == "request_id_conflict"
    assert len(broker._conversation_turns) == 1
    broker._transport.send_message.assert_awaited_once()


async def test_claim_outage_fails_closed_and_http_retries_keep_token(tmp_path):
    ledger = _Ledger()
    ledger.fail_claim = True
    broker = _broker(tmp_path)
    async with ledger.client() as client:
        broker._http_client = client
        with pytest.raises(DeliveryClaimError, match="unconfirmed"):
            await broker._dispatch_browser_message({"content": "do work", "request_id": "request"})
    assert len(ledger.requests) == 2
    assert ledger.requests[0][1] == ledger.requests[1][1]
    assert ledger.requests[0][1]["payload_hash"] == hashlib.sha256(b"do work").hexdigest()
    assert broker._conversation_turns == []
    broker._transport.send_message.assert_not_called()


@pytest.mark.parametrize("failure", ["native_timeout", "settlement_outage"])
async def test_ambiguous_delivery_remains_pending_without_native_retry(tmp_path, failure):
    ledger = _Ledger()
    broker = _broker(tmp_path)
    if failure == "native_timeout":
        broker._transport.send_message.side_effect = TimeoutError("accepted but ack lost")
    else:
        ledger.fail_settle = True
    message = {"content": "do work once", "request_id": "request"}
    async with ledger.client() as client:
        broker._http_client = client
        await broker._dispatch_browser_message(message)
        await _finish_deliveries()
        assert ledger.rows["request"]["status"] == "pending"
        assert not _events(broker, "user_delivered")
        assert _events(broker, "user_delivery_pending")[-1]["error"]
        restarted = _broker(tmp_path)
        restarted._http_client = client
        await restarted._dispatch_browser_message(message)
        restarted._transport.send_message.assert_not_called()
    broker._transport.send_message.assert_awaited_once()


@pytest.mark.parametrize(
    "response",
    [
        {},
        [],
        {"claimed": 1},
        {
            "claimed": True,
            "request_id": "foreign",
            "status": "pending",
        },
    ],
)
async def test_malformed_claim_response_never_authorizes_a_send(response):
    async with httpx.AsyncClient(
        base_url="http://forge.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    ) as client:
        with pytest.raises(DeliveryClaimError):
            await claim_message(
                client,
                session_id="s",
                request_id="r",
                content="prompt",
                token="token",
                timeout=1,
                attempts=1,
            )


@pytest.mark.parametrize("response", [[], {}, {"status": "pending"}])
async def test_settlement_must_explicitly_confirm_terminal_state(response):
    async with httpx.AsyncClient(
        base_url="http://forge.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    ) as client:
        with pytest.raises(ValueError):
            await settle_message(
                client,
                session_id="s",
                request_id="r",
                token="token",
                status="delivered",
                error=None,
                timeout=1,
            )


async def _native_tmux_question(tmp_path, request_id="question"):
    """A native-identified question with proof written only after its submit key."""
    transport = NativeQuestionTransport(str(tmp_path))
    transport._alive = True
    transport.capture_stdout = "☐ Choice\nChoose\n❯ 1. A\n  2. B\n  3. Type something.\n"
    native_request_id = await transport.surface(
        [
            {
                "id": "q",
                "header": "Choice",
                "question": "Choose",
                "options": [{"label": "A"}, {"label": "B"}],
                "multiSelect": False,
            }
        ]
    )
    transport._pending_tty_prompts[request_id] = transport._pending_tty_prompts.pop(
        native_request_id
    )
    return transport


@pytest.mark.parametrize("provider", ["claude", "codex"])
async def test_bad_answer_is_correlated_and_does_not_interrupt_valid_next_control(
    tmp_path, provider
):
    broker = _broker(tmp_path)
    transport = (
        await _native_tmux_question(tmp_path, "correct")
        if provider == "claude"
        else CodexWebSocketTransport(workspace_dir=str(tmp_path))
    )
    transport._alive = True
    if provider == "claude":
        transport.steps.append(("2", "❯\n"))
        transport.consumed = {"answers": {"Choose": "B"}}
    else:
        transport._pending_user_inputs["correct"] = (7, [{"id": "q", "question": "Choose"}])
        transport._send_rpc_response = AsyncMock()
    transport.on_event(broker._handle_cli_event)
    broker._transport = transport
    ws = AsyncMock()
    ws.headers = {}
    ws.query_params = {}
    ws.receive_json.side_effect = [
        {
            "type": "ask_user_answer",
            "request_id": "stale",
            "answers": [{"question_id": "q", "answer": "A"}],
        },
        {
            "type": "ask_user_answer",
            "request_id": "correct",
            "answers": [{"question_id": "q", "answer": "B"}],
        },
        WebSocketDisconnect(),
    ]
    await broker.handle_websocket(ws)
    errors = _events(broker, "error")
    assert len(errors) == 1
    assert errors[0]["request_id"] == "stale"
    assert errors[0]["code"] == "question_answer_rejected"
    assert "Unknown" in errors[0]["content"]
    assert _events(broker, "ask_user_resolved")[-1]["request_id"] == "correct"
    assert _events(broker, "ask_user_resolved")[-1]["accepted"] is True
    assert not _events(broker, "result")
    assert broker._conversation_turns == []
    if provider == "claude":
        assert _send_keys(transport) == ["2"]
        assert len(_events(broker, "ask_user_resolved")) == 1


@pytest.mark.parametrize("identity", [None, "", "stale"])
async def test_tmux_unknown_id_never_targets_sole_remaining_prompt(tmp_path, identity):
    transport = TmuxInteractiveTransport(workspace_dir=str(tmp_path))
    transport._pending_tty_prompts["other"] = {"kind": "question"}
    transport._send_key = AsyncMock()
    with pytest.raises(ValueError, match="Unknown"):
        await transport._answer_tty_prompt(identity, "A")
    transport._send_key.assert_not_called()
    assert "other" in transport._pending_tty_prompts


@pytest.mark.parametrize("answer,rows", [("", [(1, "A")]), ("B", []), ("B", [(1, "A")])])
async def test_tmux_unknown_or_unrendered_choice_never_selects_default(tmp_path, answer, rows):
    transport = await _native_tmux_question(tmp_path)
    transport.capture_stdout = "☐ Choice\nChoose\n" + "\n".join(
        f"  {digit}. {label}" for digit, label in rows
    )
    transport._send_key = AsyncMock()
    with pytest.raises(ValueError, match="explicit non-empty answers|live Claude menu"):
        await transport._answer_tty_prompt("question", answer)
    transport._send_key.assert_not_called()
    assert "question" in transport._pending_tty_prompts


async def test_tmux_failed_key_send_preserves_evidence_without_blind_answer_retry(tmp_path):
    transport = await _native_tmux_question(tmp_path)
    events = await _collect_events(transport)
    transport._send_key = AsyncMock(side_effect=RuntimeError("pane gone"))
    with pytest.raises(ControlRecoveryError, match="uncertain") as failure:
        await transport._answer_tty_prompt("question", "A")
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert str(failure.value.__cause__) == "pane gone"
    assert "question" in transport._pending_tty_prompts
    assert events[-1]["type"] == "ask_user_question"
    assert events[-1]["metadata"]["answerable"] is False
    with pytest.raises(ControlRecoveryError):
        await transport._answer_tty_prompt("question", "A")
    transport._send_key.assert_awaited_once()


async def test_tmux_duplicate_answer_serializes(tmp_path):
    transport = await _native_tmux_question(tmp_path)
    events = await _collect_events(transport)
    transport.steps.append(("1", "❯\n"))
    transport.consumed = {"answers": {"Choose": "A"}}
    outcomes = await asyncio.gather(
        transport._answer_tty_prompt("question", "A"),
        transport._answer_tty_prompt("question", "A"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in outcomes) == 1
    assert outcomes.count(None) == 1
    assert _send_keys(transport) == ["1"]  # Native choice commits on its digit, once.
    resolved = [event for event in events if event.get("type") == "ask_user_resolved"]
    assert len(resolved) == 1 and resolved[0]["accepted"] is True
    assert resolved[0]["request_id"] == "question"


async def test_restarted_codex_never_reuses_public_question_or_approval_identity(tmp_path):
    old, new = [CodexWebSocketTransport(workspace_dir=str(tmp_path)) for _ in range(2)]
    for transport in [old, new]:
        transport._send_rpc_response = AsyncMock()
        await transport._handle_server_request(
            {
                "method": "item/tool/requestUserInput",
                "id": 1,
                "params": {"questions": [{"id": "q", "question": "Choose"}]},
            }
        )
        await transport._handle_server_request(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 2,
                "params": {},
            }
        )
    old_question = next(iter(old._pending_user_inputs))
    old_approval = next(iter(old._pending_approvals))
    assert old_question not in new._pending_user_inputs
    assert old_approval not in new._pending_approvals
    with pytest.raises(ValueError):
        await new._answer_user_input(old_question, [{"question_id": "q", "answer": "yes"}])
    with pytest.raises(ValueError):
        await new.send_control_response(old_approval, {"behavior": "allow"})
    new._send_rpc_response.assert_not_called()


def _question(identity="question"):
    return {
        "type": "ask_user_question",
        "request_id": identity,
        "questions": [{"question": "Choose storage", "options": []}],
    }


async def test_restart_recovers_card_but_never_claims_old_native_rpc_is_answerable(tmp_path):
    original = _broker(tmp_path)
    await original._handle_cli_event(_question())
    restarted = _broker(tmp_path)
    restarted._load_control_state()
    assert restarted._pending_ask_user_questions == {}
    recovered = restarted._unrestored_questions["question"]
    assert recovered["answerable"] is False
    assert recovered["recovery_required"] is True
    with pytest.raises(ControlRecoveryError):
        await restarted._dispatch_browser_message(
            {
                "type": "ask_user_answer",
                "request_id": "question",
                "answers": [],
            }
        )
    restarted._transport.send_control.assert_not_called()
    ws = AsyncMock()
    ws.headers = {}
    ws.query_params = {}
    ws.receive_json.side_effect = WebSocketDisconnect()
    await restarted.handle_websocket(ws)
    sent = [call.args[0] for call in ws.send_json.call_args_list]
    assert recovered in sent
    assert any(frame.get("code") == "question_recovery_required" for frame in sent)
    await restarted._handle_cli_event(_question("new-question"))
    assert restarted._unrestored_questions == {}
    assert "new-question" in restarted._pending_ask_user_questions
    await restarted._handle_cli_event({"type": "result", "result": "done"})
    assert load_control_state(restarted._control_state_path()) == ({}, {})


def test_durable_pending_control_fold_excludes_resolved_and_finished_turns(tmp_path):
    def frame(payload):
        return SimpleNamespace(kind=payload["type"], payload=payload)

    frames = [
        frame(payload)
        for payload in [
            _question("old"),
            {"type": "result"},
            _question("answered"),
            {"type": "ask_user_resolved", "request_id": "answered"},
            {"type": "control_request", "request_id": "approved"},
            {"type": "permission_resolved", "request_id": "approved"},
            _question("pending"),
            {"type": "control_request", "request_id": "permission"},
            {"type": "error", "code": "control_message_rejected"},
        ]
    ]
    questions, permissions = pending_controls(frames)
    assert set(questions) == {"pending"}
    assert set(permissions) == {"permission"}
    broker = _broker(tmp_path)
    broker._restore_durable_controls(frames)
    assert broker._unrestored_questions["pending"]["answerable"] is False
    assert broker._pending_ask_user_questions == {}


@pytest.mark.parametrize("contents", ["{", "[]", '{"questions": []}', '{"permissions":{"x":1}}'])
def test_bad_control_cache_is_visible_and_does_not_break_startup(tmp_path, contents, caplog):
    broker = _broker(tmp_path)
    path = broker._control_state_path()
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    broker._load_control_state()
    assert broker._unrestored_questions == {}
    assert "Failed to recover pending control state" in caplog.text


def test_control_cache_write_is_atomic_and_roundtrips(tmp_path):
    path = tmp_path / "controls.json"
    save_control_state(path, {"q": _question("q")}, {})
    assert not path.with_suffix(".tmp").exists()
    assert load_control_state(path)[0]["q"]["answerable"] is False
    assert load_control_state(tmp_path / "missing") == ({}, {})


@pytest.mark.parametrize("kind", ["tool_use", "tool_result"])
async def test_oversized_live_tools_are_lazy_and_full_content_remains_available(
    tmp_path, monkeypatch, kind
):
    broker = _broker(tmp_path)
    module = importlib.import_module("skuld.broker")
    monkeypatch.setattr(module, "broker", broker)
    large = "🛠" * 400_000
    block = (
        {"type": "tool_use", "id": "tool", "name": "Bash", "input": {"command": large}}
        if kind == "tool_use"
        else {"type": "tool_result", "tool_use_id": "tool", "content": large, "is_error": True}
    )
    event = {"type": "assistant" if kind == "tool_use" else "user", "message": {"content": [block]}}
    ws = AsyncMock()
    fetched = []

    async def receive_preview(text):
        assert len(text.encode("utf-8")) < 900 * 1024
        fetched.append(await module.get_tool_result("tool"))

    ws.send_text.side_effect = receive_preview
    broker._channels.add(WebSocketChannel(ws, show_internal=True, max_frame_bytes=900 * 1024))
    await broker._handle_cli_event(event)
    assert fetched
    assert (
        fetched[0].get("input", {}).get("command") == large
        if kind == "tool_use"
        else fetched[0]["content"] == large
    )
    persisted = next(
        row["payload"] for row in broker._event_log_buffer if row["kind"] == event["type"]
    )
    assert persisted["message"]["content"][0] == block
    preview = json.loads(ws.send_text.call_args_list[0].args[0])["message"]["content"][0]
    if kind == "tool_use":
        assert preview["input"]["_elided_input"] is True
    else:
        assert preview["truncated"] is True
        assert preview["is_error"] is True
        assert preview["byte_size"] == len(large.encode())


@pytest.mark.parametrize("shape", ["content", "block", "top", "unstructured"])
def test_live_frame_budget_covers_supported_shapes_and_safe_fallback(shape):
    block = {"type": "tool_result", "tool_use_id": "t", "content": "x" * 5000}
    frames = {
        "content": {"type": "user", "content": [block]},
        "block": {"type": "content_block_start", "content_block": block},
        "top": block,
        "unstructured": {"type": "claude_hook", "request_id": "r", "payload": "x" * 5000},
    }
    projected = prepare_live_frame(frames[shape], max_bytes=1024)
    assert len(json.dumps(projected).encode()) <= 1024
    if shape == "unstructured":
        assert projected["code"] == "live_frame_too_large"
        assert projected["request_id"] == "r"
    else:
        assert projected["type"] == frames[shape]["type"]


@pytest.mark.parametrize(
    "kind,code",
    [
        ("ask_user_answer", "question_answer_rejected"),
        ("permission_response", "permission_response_rejected"),
        ("set_model", "control_message_rejected"),
    ],
)
def test_control_error_contract_preserves_content_identity_and_machine_code(kind, code):
    assert control_error_frame("rejected", {"type": kind, "request_id": "id"}) == {
        "type": "error",
        "content": "rejected",
        "request_id": "id",
        "code": code,
    }
    assert "request_id" not in control_error_frame("bad JSON", None, code="malformed_message")


@pytest.mark.parametrize("provider", ["claude", "codex"])
async def test_native_question_cleanup_explicitly_does_not_acknowledge_an_answer(
    tmp_path, provider
):
    transport = (
        TmuxInteractiveTransport(workspace_dir=str(tmp_path))
        if provider == "claude"
        else CodexWebSocketTransport(workspace_dir=str(tmp_path))
    )
    events = []

    async def record(frame):
        events.append(frame)

    transport.on_event(record)
    if provider == "claude":
        transport._pending_tty_prompts["q"] = {"kind": "question"}
        await transport._clear_pending_tty_prompts("turn_ended")
    else:
        transport._pending_user_inputs["q"] = (1, [])
        await transport._finalize_stranded_turn("error", "native closed")
    resolved = next(frame for frame in events if frame["type"] == "ask_user_resolved")
    assert resolved["accepted"] is False


@pytest.mark.parametrize("bad", [[], None, {"type": []}, {"type": {}}])
async def test_wrong_shape_control_does_not_poison_next_websocket_message(tmp_path, bad):
    broker = _broker(tmp_path)
    broker._transport.capabilities = TransportCapabilities(interrupt=True)
    ws = AsyncMock()
    ws.headers = {}
    ws.query_params = {}
    ws.receive_json.side_effect = [bad, {"type": "interrupt"}, WebSocketDisconnect()]
    await broker.handle_websocket(ws)
    broker._transport.send_control.assert_awaited_once_with("interrupt")
    assert len(_events(broker, "error")) == 1


async def test_codex_native_turn_identity_survives_normalization_and_late_completion(tmp_path):
    transport = CodexWebSocketTransport(workspace_dir=str(tmp_path))
    transport._thread_id = "native-thread"
    events = []

    async def record(frame):
        events.append(frame)

    transport.on_event(record)
    transport._pending_prompt_correlations.append(("message-one", "request-one"))
    await transport._handle_server_message(
        {
            "method": "turn/started",
            "params": {"threadId": "native-thread", "turn": {"id": "one"}},
        }
    )
    await transport._handle_server_message(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "native-thread", "turnId": "one", "delta": "working"},
        }
    )
    await transport._handle_server_message(
        {
            "method": "turn/completed",
            "params": {"threadId": "native-thread", "turn": {"id": "one", "status": "completed"}},
        }
    )
    for frame in events:
        if frame["type"] in {"assistant", "content_block_delta", "result", "user_consumed"}:
            assert frame["thread_id"] == "native-thread"
            assert frame["turn_id"] == "one"
            assert frame["request_id"] == "request-one"
    transport._pending_prompt_correlations.append(("message-two", "request-two"))
    await transport._handle_server_message(
        {
            "method": "turn/started",
            "params": {"threadId": "native-thread", "turn": {"id": "two"}},
        }
    )
    result_count = sum(frame["type"] == "result" for frame in events)
    await transport._handle_server_message(
        {
            "method": "turn/completed",
            "params": {"threadId": "native-thread", "turn": {"id": "one", "status": "failed"}},
        }
    )
    assert transport._current_turn_id == "two"
    assert sum(frame["type"] == "result" for frame in events) == result_count
    assert events[-1]["metadata"]["reason"] == "stale_turn_event"
    await transport._finalize_stranded_turn("error", "connection lost")
    assert events[-1]["type"] == "result"
    assert events[-1]["turn_id"] == "two"
    assert events[-1]["request_id"] == "request-two"
    assert events[-1]["is_error"] is True
