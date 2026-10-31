"""Ravn resident sessions: Volundr's controller against Ravn's real OpenClaw shim.

The shim is booted on a real socket with a stub Ravn behind it, and driven by the
production controller, so these prove the two protocol halves agree: session
lifecycle, one Ravn conversation per session, and chat frames in the same shared
contract the OpenClaw controller produces.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.ports.session_proxy import SessionProxyTarget
from tests.test_ravn.test_openclaw_gateway import (
    StubRavnGateway,
    _Event,
    plain_turn,
    running_shim,
)
from volundr.adapters.outbound.ravn_gateway import (
    RAVN_OPENCLAW_CREDENTIAL_NAME,
    CredentialStoreOpenClawTokens,
    RavnResidentSessionController,
)
from volundr.composition_builders import _create_resident_session_controllers
from volundr.config import Settings
from volundr.domain.models import (
    ResidentBackend,
    ResidentDeploymentProfile,
    ResidentEngine,
    ResidentRuntime,
    ResidentSessionStatus,
)
from volundr.domain.ports import (
    ResidentOpenClawShimHost,
    ResidentOpenClawTokens,
    ResidentSessionController,
)

#: The token the resident's running shim checks (its pod's RAVN_OPENCLAW_TOKEN).
TOKEN = "resident-token-9f8e7d6c5b4a39281706f5e4d3c2b1a0"
_RESIDENT_FRAMES = {"assistant", "content_block_delta", "result", "error"}


class _FluxRoutes(ResidentOpenClawShimHost):
    """The helm-release runtime controller, as FluxPodManager behaves: it keeps each
    resident's own shim token (adopting the one its pod runs with) and routes to the
    shim on the resident's Service, or to a local test shim on ``shim_port``."""

    backend = ResidentBackend.HELMRELEASE

    def __init__(self, *, shim_port: int | None = None, running_token: str | None = None):
        self.tokens: ResidentOpenClawTokens | None = None
        self.shim_port = shim_port
        self.running_token = running_token

    def set_resident_openclaw_tokens(self, tokens: ResidentOpenClawTokens) -> None:
        self.tokens = tokens

    async def resident_openclaw_token(self, runtime: ResidentRuntime) -> str:
        assert self.tokens is not None
        token = await self.tokens.stored(runtime)
        return token or await self.tokens.ensure(runtime, adopt=self.running_token)

    def resident_openclaw_target(self, runtime: ResidentRuntime, port: int):
        if self.shim_port is not None:
            return SessionProxyTarget(
                service_url=f"http://127.0.0.1:{self.shim_port}/",
                connect_host="127.0.0.1",
                connect_port=self.shim_port,
            )
        namespace = str(runtime.backend_ref.get("namespace") or "")
        release = str(runtime.backend_ref.get("name") or "")
        if not namespace or not release:
            return None
        host = f"{release}-skuld.{namespace}.svc.cluster.local"
        return SessionProxyTarget(
            service_url=f"http://{host}:{port}/", connect_host=host, connect_port=port
        )

    async def approve_resident_device(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the Ravn shim trusts devices on first use; no pairing")


def _runtime(**update: Any) -> ResidentRuntime:
    runtime = ResidentRuntime(
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Muninn",
        persona_name="product-steward",
        model="gpt-5.6-sol",
        backend=ResidentBackend.HELMRELEASE,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-helm",
    )
    backend_ref = {"namespace": "skuld", "name": f"resident-{runtime.id}"}
    return runtime.model_copy(update={"backend_ref": backend_ref, **update})


def _controller(
    port: int | None = None,
    *,
    running_token: str | None = TOKEN,
    store: MemoryCredentialStore | None = None,
    **kwargs: Any,
) -> RavnResidentSessionController:
    return RavnResidentSessionController(
        _FluxRoutes(shim_port=port, running_token=running_token),
        store or MemoryCredentialStore(),
        **kwargs,
    )


def _resident_shim(tmp_path: Any, **kwargs: Any) -> Any:
    """The shim as the resident's pod runs it: agent id main, the resident's own token."""
    return running_shim(tmp_path, agent_id="main", token=TOKEN, **kwargs)


def _port(url: str) -> int:
    return int(url.rsplit(":", 1)[1])


async def _frames_until_turn_ends(connection: Any) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while True:
        frame = await asyncio.wait_for(connection.receive(), timeout=10)
        frames.append(frame)
        if frame["type"] in {"result", "error"}:
            return frames


class TestRavnSessionsThroughTheShim:
    async def test_create_list_chat_and_delete(self, tmp_path) -> None:
        runtime = _runtime()
        async with _resident_shim(tmp_path) as shim:
            controller = _controller(_port(shim.url))

            session = await controller.create_session(runtime, title="Research", model="")
            assert session.chat_endpoint == f"/s/{runtime.id}/sessions/{session.id}/session"

            listed = await controller.list_sessions(runtime)
            # The shim's own default session is not a Volundr session; only niuu-<uuid> keys are.
            assert [(s.id, s.title) for s in listed] == [(session.id, "Research")]
            assert listed[0].status is ResidentSessionStatus.IDLE

            connection = await controller.connect_chat(runtime, session.id)
            try:
                assert (await connection.receive())["type"] == "capabilities"
                assert await connection.receive() == {"type": "conversation_history", "turns": []}
                await connection.send({"type": "user", "content": "ping", "request_id": "r-1"})
                frames = await _frames_until_turn_ends(connection)
            finally:
                await connection.close()

            confirmed = next(f for f in frames if f["type"] == "user_confirmed")
            assert confirmed["request_id"] == "r-1"
            resident = [f for f in frames if f["type"] in _RESIDENT_FRAMES]
            assert [f["type"] for f in resident] == [
                "assistant",
                "content_block_delta",
                "content_block_delta",
                "result",
            ]
            assert resident[0]["participant"]["display_name"] == "Muninn"
            assert resident[0]["participant"]["persona"] == "product-steward"
            text = "".join(
                f["delta"]["text"] for f in resident if f["type"] == "content_block_delta"
            )
            assert text == "pong", "cumulative shim deltas must not double the answer"
            assert shim.stub.seen == [(f"agent:main:niuu-{session.id}", "ping")]

            await controller.delete_session(runtime, session.id)
            assert await controller.list_sessions(runtime) == []
            assert shim.stub.dropped == [f"agent:main:niuu-{session.id}"]

    async def test_sessions_are_separate_conversations_with_their_own_history(
        self, tmp_path
    ) -> None:
        runtime = _runtime()
        async with _resident_shim(tmp_path) as shim:
            controller = _controller(_port(shim.url))
            first = await controller.create_session(runtime, title="One", model="")
            second = await controller.create_session(runtime, title="Two", model="")

            for session, message in ((first, "alpha"), (second, "beta")):
                connection = await controller.connect_chat(runtime, session.id)
                try:
                    await connection.receive()
                    await connection.receive()
                    await connection.send({"type": "user", "content": message})
                    await _frames_until_turn_ends(connection)
                finally:
                    await connection.close()

            assert {key for key, _ in shim.stub.seen} == {
                f"agent:main:niuu-{first.id}",
                f"agent:main:niuu-{second.id}",
            }
            connection = await controller.connect_chat(runtime, first.id)
            try:
                await connection.receive()
                history = await connection.receive()
            finally:
                await connection.close()
            assert [(t["role"], t["content"]) for t in history["turns"]] == [
                ("user", "alpha"),
                ("assistant", "pong"),
            ]
            assert history["turns"][1]["participant_meta"]["display_name"] == "Muninn"

    async def test_interrupt_aborts_the_turn(self, tmp_path) -> None:
        stub = StubRavnGateway(script=plain_turn(), delay=0.3)
        runtime = _runtime()
        async with _resident_shim(tmp_path, stub=stub) as shim:
            controller = _controller(_port(shim.url))
            session = await controller.create_session(runtime, title="Slow", model="")
            connection = await controller.connect_chat(runtime, session.id)
            try:
                await connection.receive()
                await connection.receive()
                await connection.send({"type": "user", "content": "slow"})
                while (await asyncio.wait_for(connection.receive(), timeout=10))[
                    "type"
                ] != "assistant":
                    pass
                await connection.send({"type": "interrupt"})
                frames = await _frames_until_turn_ends(connection)
            finally:
                await connection.close()
            assert frames[-1] == {"type": "error", "error": "aborted"}

    async def test_a_wrong_token_is_refused(self, tmp_path) -> None:
        runtime = _runtime()
        async with _resident_shim(tmp_path) as shim:
            controller = _controller(_port(shim.url), running_token="not-the-residents-token")
            with pytest.raises(Exception, match="invalid gateway token"):
                await controller.list_sessions(runtime)

    async def test_one_residents_token_does_not_open_another_residents_shim(self, tmp_path) -> None:
        """Another resident has its own, generated token; this shim refuses it."""
        async with _resident_shim(tmp_path) as shim:
            controller = _controller(_port(shim.url), running_token=None)
            with pytest.raises(Exception, match="invalid gateway token"):
                await controller.list_sessions(_runtime())


class TestRavnRepliesStreamOnce:
    """A reply reaches clients exactly once, whatever Ravn executor produced it."""

    async def test_cli_executor_turn_is_not_doubled(self, tmp_path) -> None:
        """The live Valhalla turn: a CLI executor streams fragments, then re-sends the
        whole message as one more thought, then the response. Before the fix clients
        got the fragments and then the full text again as a last delta."""
        fragments = ["I", "’m", " doing", " well", " and", " ready", " to", " help", "."]
        answer = "".join(fragments)
        script = [_Event("thought", {"text": fragment}) for fragment in fragments]
        script += [_Event("thought", {"text": answer}), _Event("response", {"text": answer})]
        runtime = _runtime()
        async with _resident_shim(tmp_path, stub=StubRavnGateway(script=script)) as shim:
            controller = _controller(_port(shim.url))
            session = await controller.create_session(runtime, title="Hello", model="")
            connection = await controller.connect_chat(runtime, session.id)
            try:
                await connection.receive()
                await connection.receive()
                await connection.send({"type": "user", "content": "How are you?"})
                frames = await _frames_until_turn_ends(connection)
            finally:
                await connection.close()

        resident = [f for f in frames if f["type"] in _RESIDENT_FRAMES]
        assert [f["type"] for f in resident] == [
            "assistant",
            *["content_block_delta"] * len(fragments),
            "result",
        ]
        deltas = [f["delta"]["text"] for f in resident if f["type"] == "content_block_delta"]
        assert deltas == fragments
        assert "".join(deltas) == answer

    async def test_text_after_a_tool_does_not_repeat_the_text_before_it(self, tmp_path) -> None:
        script = [
            _Event("thought", {"text": "Let me look."}),
            _Event("tool_start", {"tool_name": "read_file", "input": {}}),
            _Event("tool_result", {"tool_name": "read_file", "result": "# niuu"}),
            _Event("thought", {"text": "It is "}),
            _Event("thought", {"text": "niuu."}),
            _Event("response", {"text": "It is niuu."}),
        ]
        runtime = _runtime()
        async with _resident_shim(tmp_path, stub=StubRavnGateway(script=script)) as shim:
            controller = _controller(_port(shim.url))
            session = await controller.create_session(runtime, title="Tools", model="")
            connection = await controller.connect_chat(runtime, session.id)
            try:
                await connection.receive()
                await connection.receive()
                await connection.send({"type": "user", "content": "Which heading?"})
                frames = await _frames_until_turn_ends(connection)
            finally:
                await connection.close()

        text = "".join(f["delta"]["text"] for f in frames if f["type"] == "content_block_delta")
        assert text == "Let me look.It is niuu."


class TestRavnControllerWiring:
    def test_engine_is_ravn(self) -> None:
        assert _controller().engine is ResidentEngine.RAVN

    def test_hands_the_residents_token_store_to_the_runtime_controller(self) -> None:
        routes = _FluxRoutes()
        RavnResidentSessionController(routes, MemoryCredentialStore())
        assert isinstance(routes.tokens, CredentialStoreOpenClawTokens)

    def test_a_stale_gateway_token_is_ignored_with_a_warning(self, caplog) -> None:
        """An old config's master (Valhalla's secretKwargs) neither crashes nor counts."""
        routes = _FluxRoutes()
        with caplog.at_level("WARNING"):
            RavnResidentSessionController(
                routes, MemoryCredentialStore(), gateway_token="old-cluster-master"
            )
        assert "ignores gateway_token" in caplog.text
        assert isinstance(routes.tokens, CredentialStoreOpenClawTokens)

    def test_route_is_the_resident_service_shim_port(self) -> None:
        controller = _controller()
        runtime = _runtime()
        target = controller._proxy_target(runtime)
        host = f"resident-{runtime.id}-skuld.skuld.svc.cluster.local"
        assert target is not None
        assert target.service_url == f"http://{host}:18790/"
        assert (target.connect_host, target.connect_port) == (host, 18790)

    def test_a_backend_without_the_shim_is_refused(self) -> None:
        class _NoShim:
            backend = ResidentBackend.LOCAL

        with pytest.raises(ValueError, match="ResidentOpenClawShimHost"):
            RavnResidentSessionController(_NoShim(), MemoryCredentialStore())

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            ({}, True),
            ({"resident": {"openclaw": {"enabled": True}}}, True),
            ({"resident": {"openclaw": {"port": 18800}}}, True),
            ({"resident": {"openclaw": {"enabled": False}}}, False),
        ],
    )
    def test_sessions_unless_the_profile_switches_the_shim_off(self, values, expected) -> None:
        profile = ResidentDeploymentProfile(
            id="ravn-helm",
            display_name="Ravn",
            backend=ResidentBackend.HELMRELEASE,
            engine=ResidentEngine.RAVN,
            deployment={"values": values},
        )
        assert _controller().provides_sessions(profile) is expected

    def test_bound_to_its_runtime_backend(self) -> None:
        assert _controller().runtime_backend is ResidentBackend.HELMRELEASE

    def test_no_route_before_the_backend_reports_the_release(self) -> None:
        assert _controller()._proxy_target(_runtime(backend_ref={})) is None

    async def test_device_identity_is_stable_per_resident(self) -> None:
        store = MemoryCredentialStore()
        controller = _controller(store=store, running_token=None)
        runtime = _runtime()
        first = await controller._machine_credentials(runtime)
        again = await controller._machine_credentials(runtime)
        other = await controller._machine_credentials(_runtime())
        assert first == again
        assert first["device_id"] != other["device_id"]
        assert other["gateway_token"] != first["gateway_token"]
        assert first["device_token"] == ""
        await controller._remember_device_token(runtime, {**first, "device_token": "d"})
        assert await store.get_value("resident", str(runtime.id), "openclaw-gateway") is None


class TestResidentTokens:
    """Each resident's own token, kept per resident like the Hermes API keys."""

    async def test_generated_once_and_kept_across_restarts(self) -> None:
        store = MemoryCredentialStore()
        runtime = _runtime()
        first = await CredentialStoreOpenClawTokens(store).ensure(runtime)
        # A new Volundr (restart) and a redeploy read the same kept token.
        again = await CredentialStoreOpenClawTokens(store).ensure(runtime)
        assert first == again and len(first) >= 32
        assert await store.get_value(
            "resident", str(runtime.id), RAVN_OPENCLAW_CREDENTIAL_NAME
        ) == {"token": first}
        assert await CredentialStoreOpenClawTokens(store).ensure(_runtime()) != first

    async def test_adopts_the_running_token_only_when_none_is_kept(self) -> None:
        store = MemoryCredentialStore()
        tokens = CredentialStoreOpenClawTokens(store)
        runtime = _runtime()
        assert await tokens.stored(runtime) is None
        assert await tokens.ensure(runtime, adopt=TOKEN) == TOKEN
        assert await tokens.ensure(runtime, adopt="a-later-one") == TOKEN

    async def test_forget_removes_it(self) -> None:
        store = MemoryCredentialStore()
        tokens = CredentialStoreOpenClawTokens(store)
        runtime = _runtime()
        await tokens.ensure(runtime)
        await tokens.forget(runtime)
        assert await tokens.stored(runtime) is None

    async def test_the_controller_dials_with_the_adopted_token(self) -> None:
        store = MemoryCredentialStore()
        controller = _controller(store=store)
        runtime = _runtime()
        credentials = await controller._machine_credentials(runtime)
        assert credentials["gateway_token"] == TOKEN
        assert (await store.get_value("resident", str(runtime.id), RAVN_OPENCLAW_CREDENTIAL_NAME))[
            "token"
        ] == TOKEN

    def test_composition_builds_it_against_the_helmrelease_backend(self, monkeypatch) -> None:
        monkeypatch.setenv("RESIDENT_SESSION_CONTROLLER_0_SK_GATEWAY_TOKEN", "old-master")
        settings = Settings.model_validate(
            {
                "resident_runtimes": {
                    "session_controllers": [
                        {
                            "adapter": (
                                "volundr.adapters.outbound.ravn_gateway."
                                "RavnResidentSessionController"
                            ),
                            "runtime_backend": "helmrelease",
                            "secret_kwargs_env": {
                                "gateway_token": "RESIDENT_SESSION_CONTROLLER_0_SK_GATEWAY_TOKEN"
                            },
                        }
                    ]
                }
            }
        )
        controllers = _create_resident_session_controllers(
            settings,
            [_FluxRoutes()],  # type: ignore[list-item]
            MemoryCredentialStore(),
        )
        assert len(controllers) == 1
        assert isinstance(controllers[0], ResidentSessionController)
        assert controllers[0].engine is ResidentEngine.RAVN
