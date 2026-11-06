"""Broker side of ``request_secret``: a card turn, a wait, and a private file; never the value."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from niuu.forge_mcp.models import ForgeApiError, SecretRequestError
from niuu.forge_mcp.ports import ForgeClient
from skuld.config import SkuldSettings
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.forge_mcp.routes import REQUEST_SECRET_TOOL_PATH
from skuld.transports.session_tools import BROKER_TOKEN_FILE_ENV, FORGE_REQUEST_SECRET_URL_ENV
from skuld.transports.tool_shims import ensure_codex_tool_shims
from tests.test_skuld import test_forge_mcp_broker as broker_tests
from tests.test_skuld.test_forge_mcp_broker import (
    SESSION_ID,
    _app,
    _broker,
    _call,
)

forge = broker_tests.forge

VALUE = "glpat-super-secret-value-0123456789"
REQUEST_ID = "9b5b0a86-3b43-4c0e-a1c2-000000000001"
BASE = f"/api/v1/forge/sessions/{SESSION_ID}/secret-requests"


def _request(status: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": REQUEST_ID,
        "session_id": SESSION_ID,
        "name": "GITLAB_TOKEN",
        "purpose": "clone the repo",
        "target": "gitlab.example.com",
        "status": status,
        "keep": None,
        "credential_name": None,
        **extra,
    }


class FakeSecretClient(ForgeClient):
    """A ForgeClient that answers secret requests from a script of statuses."""

    def __init__(self, *statuses: str, name: str = "GITLAB_TOKEN", claim: dict | None = None):
        self.statuses = list(statuses)
        self.name = name
        self.claim_body = claim if claim is not None else {"name": name, "value": VALUE}
        self.calls: list[tuple[str, Any]] = []

    def _status(self) -> str:
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    async def create_secret_request(self, session_id, body):
        self.calls.append(("create", (session_id, body)))
        return _request(self._status(), name=body["name"])

    async def get_secret_request(self, session_id, request_id):
        self.calls.append(("get", (session_id, request_id)))
        return _request(self._status())

    async def claim_secret_request(self, session_id, request_id):
        self.calls.append(("claim", (session_id, request_id)))
        return self.claim_body

    async def cancel_secret_request(self, session_id, request_id):
        self.calls.append(("cancel", (session_id, request_id)))
        return _request("cancelled")

    # The rest of the port is not exercised here.
    async def list_notifications(self, params): ...
    async def list_sessions(self, params): ...
    async def get_session(self, session_id, *, instance_id): ...
    async def get_conversation(self, session_id, *, turns, instance_id): ...
    async def send_message(self, session_id, *, content, request_id, instance_id): ...
    async def get_message_delivery(self, session_id, *, request_id, instance_id): ...
    async def create_session(self, body): ...
    async def start_session(self, session_id, *, instance_id): ...
    async def stop_session(self, session_id, *, instance_id): ...
    async def submit_notification(self, session_id, draft, *, idempotency_key, instance_id): ...
    async def update_description(self, session_id, changes): ...


def _host(tmp_path, forge, client: FakeSecretClient, **secret_requests: Any):
    broker = _broker(tmp_path, forge)
    broker._settings.secret_requests = broker._settings.secret_requests.model_copy(
        update={"poll_interval_s": 0.01, "wait_max_s": 5.0, **secret_requests}
    )
    broadcasts: list[dict] = []

    async def capture(frame: dict) -> None:
        broadcasts.append(frame)

    broker._channels.broadcast = capture  # type: ignore[method-assign]
    broker.broadcasts = broadcasts  # type: ignore[attr-defined]
    return BrokerForgeMcpHost(broker, client), broker


def _secret_turns(broker) -> list:
    return [t for t in broker._conversation_turns if t.id.startswith("sr_")]


class TestReady:
    async def test_writes_a_private_file_and_returns_only_its_path(self, tmp_path, forge) -> None:
        client = FakeSecretClient("pending", "pending", "fulfilled")
        host, broker = _host(tmp_path, forge, client)

        result = await host.request_secret("GITLAB_TOKEN", "clone the repo", "gitlab.example.com")

        path = tmp_path / "rt" / "secrets" / "GITLAB_TOKEN"
        assert result == {"status": "ready", "name": "GITLAB_TOKEN", "path": str(path)}
        assert VALUE not in json.dumps(result)
        assert path.read_text() == VALUE
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert [call[0] for call in client.calls] == ["create", "get", "get", "claim"]
        assert client.calls[0][1] == (
            SESSION_ID,
            {"name": "GITLAB_TOKEN", "purpose": "clone the repo", "target": "gitlab.example.com"},
        )
        # The value is not in the card turn, the history, or the live frame.
        [turn] = _secret_turns(broker)
        assert VALUE not in json.dumps(broker.broadcasts) + repr(turn)

    async def test_the_card_turn_has_the_contract_shape(self, tmp_path, forge) -> None:
        host, broker = _host(tmp_path, forge, FakeSecretClient("fulfilled"))

        await host.request_secret("GITLAB_TOKEN", "clone the repo", None)

        [turn] = _secret_turns(broker)
        assert turn.id == f"sr_{REQUEST_ID}"
        assert turn.role == "assistant" and turn.content == "Secret requested: GITLAB_TOKEN"
        assert turn.parts == [
            {
                "type": "tool_use",
                "id": REQUEST_ID,
                "name": "secret_request",
                "input": {
                    "request_id": REQUEST_ID,
                    "name": "GITLAB_TOKEN",
                    "purpose": "clone the repo",
                    "target": None,
                },
            }
        ]
        [frame] = broker.broadcasts
        assert frame["type"] == "conversation.turn" and frame["turn"]["id"] == turn.id

    async def test_the_turn_is_emitted_once_per_request_id(self, tmp_path, forge) -> None:
        client = FakeSecretClient("fulfilled")
        host, broker = _host(tmp_path, forge, client)

        await host.request_secret("GITLAB_TOKEN", "clone the repo", None)
        await host.request_secret("GITLAB_TOKEN", "clone the repo", None)

        assert len(_secret_turns(broker)) == 1
        assert len(broker.broadcasts) == 1
        assert [call[0] for call in client.calls].count("create") == 2

    async def test_a_second_ask_for_a_new_request_gets_its_own_card(self, tmp_path, forge) -> None:
        host, broker = _host(tmp_path, forge, FakeSecretClient("fulfilled"))
        await host.request_secret("GITLAB_TOKEN", "clone the repo", None)

        class Other(FakeSecretClient):
            async def create_secret_request(self, session_id, body):
                return {**_request("fulfilled"), "id": "another-id"}

        host._client = Other("fulfilled")
        await host.request_secret("GITLAB_TOKEN", "clone the repo", None)

        assert [t.id for t in _secret_turns(broker)] == [f"sr_{REQUEST_ID}", "sr_another-id"]

    async def test_a_claimed_request_with_its_file_is_ready_again(self, tmp_path, forge) -> None:
        host, broker = _host(tmp_path, forge, FakeSecretClient("fulfilled"))
        first = await host.request_secret("GITLAB_TOKEN", "clone the repo", None)

        host._client = FakeSecretClient("delivered")
        again = await host.request_secret("GITLAB_TOKEN", "clone the repo", None)

        assert again == first
        assert [c[0] for c in host._client.calls] == ["create"]  # no second claim

    async def test_a_claimed_request_without_its_file_is_an_error(self, tmp_path, forge) -> None:
        host, _ = _host(tmp_path, forge, FakeSecretClient("delivered"))
        with pytest.raises(SecretRequestError, match="file is gone"):
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)


class TestEndsWithoutASecret:
    async def test_declined(self, tmp_path, forge) -> None:
        client = FakeSecretClient("pending", "declined")
        host, _ = _host(tmp_path, forge, client)
        with pytest.raises(SecretRequestError, match="declined to provide GITLAB_TOKEN"):
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)
        assert "claim" not in [call[0] for call in client.calls]
        assert not (tmp_path / "rt" / "secrets").exists()

    async def test_cancelled(self, tmp_path, forge) -> None:
        host, _ = _host(tmp_path, forge, FakeSecretClient("pending", "cancelled"))
        with pytest.raises(SecretRequestError, match="cancelled before the user answered"):
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)

    async def test_timeout_names_the_knob_and_says_it_stays_open(self, tmp_path, forge) -> None:
        client = FakeSecretClient("pending")
        host, _ = _host(tmp_path, forge, client, wait_max_s=0.05)
        with pytest.raises(SecretRequestError) as raised:
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)
        text = str(raised.value)
        assert "secret_requests.wait_max_s" in text
        assert "still open in the chat" in text
        assert "call request_secret again with the same name" in text
        assert "claim" not in [call[0] for call in client.calls]

    async def test_an_unknown_status_is_loud(self, tmp_path, forge) -> None:
        host, _ = _host(tmp_path, forge, FakeSecretClient("wat"))
        with pytest.raises(ForgeApiError, match="unknown status 'wat'"):
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)

    async def test_a_claim_without_a_value_is_loud_and_leaks_nothing(self, tmp_path, forge) -> None:
        client = FakeSecretClient("fulfilled", claim={"name": "OTHER", "value": VALUE})
        host, _ = _host(tmp_path, forge, client)
        with pytest.raises(ForgeApiError) as raised:
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)
        assert VALUE not in str(raised.value)
        assert not (tmp_path / "rt" / "secrets").exists()

    async def test_a_failed_write_never_puts_the_value_in_the_error(self, tmp_path, forge) -> None:
        host, broker = _host(tmp_path, forge, FakeSecretClient("fulfilled"))
        runtime = broker._session_runtime
        runtime.secrets_dir.write_text("a file where the directory should be")
        with pytest.raises(SecretRequestError, match="call request_secret again") as raised:
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)
        assert VALUE not in str(raised.value)
        assert VALUE not in repr(raised.value.__cause__)

    async def test_no_durable_log_means_no_request_is_made(self, tmp_path) -> None:
        client = FakeSecretClient("fulfilled")
        host, _ = _host(tmp_path, None, client)
        with pytest.raises(ForgeApiError, match="no durable Forge log"):
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)
        assert client.calls == []

    async def test_a_turn_the_log_refuses_cancels_the_request(self, tmp_path, forge) -> None:
        client = FakeSecretClient("pending")
        host, broker = _host(tmp_path, forge, client)
        broker._append_turn = lambda turn: None  # type: ignore[method-assign]
        with pytest.raises(ForgeApiError, match="refused the secret request turn"):
            await host.request_secret("GITLAB_TOKEN", "clone the repo", None)
        assert [call[0] for call in client.calls] == ["create", "cancel"]

    @pytest.mark.parametrize("name", ["../x", "lower", "A/B", "A" * 256, ""])
    async def test_a_name_that_is_not_a_file_name_is_refused(self, tmp_path, forge, name) -> None:
        client = FakeSecretClient("fulfilled")
        host, _ = _host(tmp_path, forge, client)
        with pytest.raises(ForgeApiError, match="must match"):
            await host.request_secret(name, "p", None)
        assert client.calls == []


class TestThroughTheToolRoute:
    """The real toolbox, loopback route and REST client against a fake Forge."""

    def _routes(self, forge, *statuses: str) -> list[str]:
        seen: list[str] = []
        script = list(statuses)

        def create(request: httpx.Request) -> httpx.Response:
            seen.append("create")
            body = json.loads(request.content)
            assert body == {"name": "GITLAB_TOKEN", "purpose": "clone"}
            return httpx.Response(201, json=_request(script.pop(0)))

        def get(request: httpx.Request) -> httpx.Response:
            seen.append("get")
            return httpx.Response(200, json=_request(script.pop(0)))

        def claim(request: httpx.Request) -> httpx.Response:
            seen.append("claim")
            return httpx.Response(
                200,
                json={"name": "GITLAB_TOKEN", "value": VALUE},
                headers={"cache-control": "no-store"},
            )

        forge.routes[("POST", BASE)] = create
        forge.routes[("GET", f"{BASE}/{REQUEST_ID}")] = get
        forge.routes[("POST", f"{BASE}/{REQUEST_ID}/claim")] = claim
        return seen

    async def test_ready_result_shape_and_no_value_anywhere(self, tmp_path, forge) -> None:
        seen = self._routes(forge, "pending", "fulfilled")
        broker = _broker(tmp_path, forge)
        broker._settings.secret_requests = broker._settings.secret_requests.model_copy(
            update={"poll_interval_s": 0.01}
        )
        broker._channels.broadcast = lambda frame: _noop()  # type: ignore[method-assign]

        result = await _call(broker, "request_secret", {"name": "GITLAB_TOKEN", "purpose": "clone"})

        path = str(tmp_path / "rt" / "secrets" / "GITLAB_TOKEN")
        assert result["isError"] is False
        assert result["structuredContent"] == {
            "status": "ready",
            "name": "GITLAB_TOKEN",
            "path": path,
        }
        assert VALUE not in json.dumps(result)
        assert seen == ["create", "get", "claim"]
        authorization = {r.headers["authorization"] for r in forge.requests}
        assert authorization == {f"Bearer {broker_tests.SERVICE_TOKEN}"}

    async def test_declined_is_a_tool_error(self, tmp_path, forge) -> None:
        self._routes(forge, "declined")
        broker = _broker(tmp_path, forge)
        broker._channels.broadcast = lambda frame: _noop()  # type: ignore[method-assign]

        result = await _call(broker, "request_secret", {"name": "GITLAB_TOKEN", "purpose": "clone"})

        assert result["isError"] is True
        assert "request_secret failed" in result["content"][0]["text"]
        assert "declined" in result["content"][0]["text"]

    async def test_a_bad_name_is_a_validation_error(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge)
        result = await _call(broker, "request_secret", {"name": "not valid", "purpose": "x"})
        assert result["isError"] is True and "Invalid arguments" in result["content"][0]["text"]


async def _noop() -> None:
    return None


class TestSecretsAreWipedWithTheRuntime:
    def test_revoke_removes_the_secrets_but_not_a_replacement_brokers(self, tmp_path) -> None:
        broker = _broker(tmp_path, None)
        runtime = broker._session_runtime
        runtime.write_secret("A_KEY", "v")
        assert (runtime.secrets_dir / "A_KEY").exists()

        runtime.revoke()
        assert not runtime.secrets_dir.exists()

        # A replacement broker for the same session wrote its own secret first: keep its files.
        other = type(runtime).create(str(tmp_path / "rt"), SESSION_ID)
        other.write_secret("B_KEY", "v")
        runtime.revoke()
        assert (other.secrets_dir / "B_KEY").exists()

    def test_a_secret_name_cannot_name_a_path(self, tmp_path) -> None:
        runtime = _broker(tmp_path, None)._session_runtime
        with pytest.raises(ValueError, match="invalid secret name"):
            runtime.write_secret("../escape", "v")


class TestConfig:
    def test_defaults_fit_the_engine_timeouts(self) -> None:
        settings = SkuldSettings()
        assert settings.secret_requests.poll_interval_s == 2.0
        assert settings.secret_requests.wait_max_s == 50.0
        assert settings.secret_requests.wait_max_s < settings.forge_mcp.broker_request_timeout_s
        assert settings.secret_requests.wait_max_s < settings.forge_mcp.tool_timeout_s

    @pytest.mark.parametrize(
        ("forge_mcp", "knob"),
        [
            ({"broker_request_timeout_s": 60.0, "tool_timeout_s": 600.0}, "broker_request"),
            ({"broker_request_timeout_s": 600.0, "tool_timeout_s": 120.0}, "tool_timeout"),
        ],
    )
    def test_a_wait_the_engine_would_cut_off_is_a_startup_error(self, forge_mcp, knob) -> None:
        with pytest.raises(ValidationError) as raised:
            SkuldSettings(secret_requests={"wait_max_s": 300.0}, forge_mcp=forge_mcp)
        text = str(raised.value)
        assert "secret_requests.wait_max_s (300.0)" in text
        assert f"forge_mcp.{knob}" in text

    def test_a_longer_wait_is_fine_once_the_timeouts_allow_it(self) -> None:
        settings = SkuldSettings(
            secret_requests={"wait_max_s": 300.0},
            forge_mcp={"broker_request_timeout_s": 400.0, "tool_timeout_s": 500.0},
        )
        assert settings.secret_requests.wait_max_s == 300.0

    def test_the_check_is_skipped_when_the_forge_mcp_is_off(self) -> None:
        SkuldSettings(secret_requests={"wait_max_s": 900.0}, forge_mcp={"enabled": False})

    @pytest.mark.parametrize("field", ["poll_interval_s", "wait_max_s"])
    def test_a_zero_bound_is_rejected(self, field) -> None:
        with pytest.raises(ValidationError):
            SkuldSettings(secret_requests={field: 0})


class TestShim:
    def test_it_is_installed_and_validates_its_arguments(self, tmp_path) -> None:
        bin_dir, _ = ensure_codex_tool_shims(str(tmp_path / "ws"))
        shim = bin_dir / "request-secret"
        assert shim.stat().st_mode & 0o111
        env = {"PATH": "/usr/bin:/bin"}

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(shim), *args], env=env, capture_output=True, text=True, timeout=30
            )

        assert run().returncode == 2
        assert run("--help").returncode == 0
        assert run("NAME").returncode == 2  # --purpose is required
        assert run("NAME", "--purpose").returncode == 2
        assert run("NAME", "OTHER", "--purpose", "x").returncode == 2
        unavailable = run("NAME", "--purpose", "x")
        assert unavailable.returncode == 3 and "not available" in unavailable.stderr

    def test_it_prints_the_path_or_fails_with_the_tools_text(self, tmp_path) -> None:
        import http.server
        import threading

        seen: list[tuple[str | None, dict]] = []
        answer: dict[str, Any] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                seen.append((self.headers.get("authorization"), body))
                data = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                return None

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            token = tmp_path / "token"
            token.write_text("loopback-secret")
            bin_dir, shim_env = ensure_codex_tool_shims(
                str(tmp_path / "ws"),
                session_env={
                    FORGE_REQUEST_SECRET_URL_ENV: (
                        f"http://127.0.0.1:{server.server_port}{REQUEST_SECRET_TOOL_PATH}"
                    ),
                    BROKER_TOKEN_FILE_ENV: str(token),
                },
            )
            env = {**os.environ, **shim_env}

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(bin_dir / "request-secret"), *args],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            answer.update(
                {"isError": False, "content": [], "structuredContent": {"path": "/rt/secrets/K"}}
            )
            ok = run("GITLAB_TOKEN", "--purpose", "clone", "--target=gitlab.example.com")
            assert ok.returncode == 0 and ok.stdout == "/rt/secrets/K\n"
            assert seen == [
                (
                    "Bearer loopback-secret",
                    {
                        "name": "GITLAB_TOKEN",
                        "purpose": "clone",
                        "target": "gitlab.example.com",
                    },
                )
            ]

            answer.clear()
            answer.update(
                {"isError": True, "content": [{"type": "text", "text": "the user declined"}]}
            )
            failed = run("GITLAB_TOKEN", "--purpose=clone")
            assert failed.returncode == 1
            assert "the user declined" in failed.stderr and failed.stdout == ""
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_the_route_constant_names_the_tool() -> None:
    assert REQUEST_SECRET_TOOL_PATH == "/api/forge-mcp/tools/request_secret"
    assert _app is not None
