"""GBrainLayers: how a memory layer becomes a gbrain source and client, and what fails loudly.

The full flows run against FakeGBrain in test_gbrain_memory_backend.py; this file holds the
mapping and the answers a real gbrain could give that the fake never does.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from mimir.adapters.gbrain_layers import (
    Address,
    GBrainLayers,
    parse_namespace,
    source_id_of,
)
from mimir.memory.models import MemoryBackendError, MemoryValidationError
from tests.test_mimir.unit.memory_fakes import ADMIN_TOKEN, BASE, MCP, FakeGBrain


def _layers() -> GBrainLayers:
    return GBrainLayers(
        mcp_url=MCP,
        base_url=BASE,
        admin_token=ADMIN_TOKEN,
        timeout_seconds=5,
        token_refresh_margin_seconds=1,
    )


class TestAddresses:
    @pytest.mark.parametrize(
        ("namespace", "layer", "held"),
        [
            ("org", "org", ""),
            ("project-a", "project-a", ""),
            ("_proposals/project-a", "project-a", "_proposals/"),
            ("_decided/session-5e55", "session-5e55", "_decided/"),
            ("run-1", "run-1", ""),
        ],
    )
    def test_a_namespace_is_a_layer_or_what_a_layer_holds(self, namespace, layer, held) -> None:
        assert parse_namespace(namespace) == Address(layer=layer, held=held)

    @pytest.mark.parametrize(
        "namespace",
        ["", "org-x", "project", "project-", "attached-docs", "Project-A", "_other/org"],
    )
    def test_anything_else_is_not_a_namespace(self, namespace) -> None:
        with pytest.raises(MemoryValidationError):
            parse_namespace(namespace)

    def test_a_source_id_is_the_same_for_a_layer_and_its_proposals(self) -> None:
        assert parse_namespace("_proposals/project-a").source_id == source_id_of("project-a")

    def test_source_ids_fit_gbrains_rules(self) -> None:
        for namespace in (
            "project-" + "9a0e0000-0000-4000-8000-0000000000aa",
            "run-x",
            "session-y",
        ):
            source_id = source_id_of(namespace)
            assert len(source_id) == 32
            assert source_id[1] == "-" and source_id[2:].isalnum()


class TestGbrainAnswers:
    async def test_an_unreachable_server_raises_naming_the_root(self) -> None:
        with respx.mock() as router:
            router.post(f"{BASE}/admin/login").mock(side_effect=httpx.ConnectError("refused"))
            layers = _layers()
            with pytest.raises(MemoryBackendError, match="server root"):
                await layers.layers()
            await layers.close()

    async def test_a_login_without_a_session_raises(self) -> None:
        with respx.mock() as router:
            router.post(f"{BASE}/admin/login").mock(
                return_value=httpx.Response(200, json={"status": "authenticated"})
            )
            layers = _layers()
            with pytest.raises(MemoryBackendError, match="set no admin session"):
                await layers.layers()
            await layers.close()

    async def test_an_answer_that_is_not_json_raises(self) -> None:
        fake = FakeGBrain()
        with respx.mock() as router:
            router.get(f"{BASE}/admin/api/sources").mock(
                return_value=httpx.Response(200, text="<html>proxy</html>")
            )
            router.route(url__startswith=BASE).mock(side_effect=fake.handle)
            layers = _layers()
            with pytest.raises(MemoryBackendError, match="without JSON"):
                await layers.layers()
            await layers.close()

    async def test_a_listing_that_is_not_a_list_raises(self) -> None:
        fake = FakeGBrain()
        with respx.mock() as router:
            router.get(f"{BASE}/admin/api/sources").mock(
                return_value=httpx.Response(200, json={"sources": []})
            )
            router.route(url__startswith=BASE).mock(side_effect=fake.handle)
            layers = _layers()
            with pytest.raises(MemoryBackendError, match="without a list"):
                await layers.layers()
            await layers.close()

    async def test_a_refused_token_names_the_client_and_the_remedy(self) -> None:
        fake = FakeGBrain()
        with respx.mock() as router:
            fake.mount(router)
            layers = _layers()
            await layers.create("project-a")
            for client in fake.live_clients():
                client["revoked"] = True
            mcp = await layers.client("project-a", create=False)
            with pytest.raises(Exception, match="refused a token for client"):
                await mcp.call("query", {"query": "x"})
            await layers.close()

    @pytest.mark.parametrize(
        ("broken", "message"),
        [
            (lambda request: httpx.Response(200, json={"access_token": "x"}), "expires_in"),
            (httpx.ConnectError("refused"), "token request failed"),
        ],
    )
    async def test_a_token_endpoint_that_fails_later_raises_on_the_next_call(
        self, broken, message
    ) -> None:
        fake = FakeGBrain()
        failing = {"on": False}

        def token(request: httpx.Request) -> httpx.Response:
            if not failing["on"]:
                return fake.handle(request)
            if isinstance(broken, Exception):
                raise broken
            return broken(request)

        with respx.mock() as router:
            router.post(f"{BASE}/token").mock(side_effect=token)
            router.route(url__startswith=BASE).mock(side_effect=fake.handle)
            layers = _layers()
            await layers.create("project-a")
            await layers.close()
            failing["on"] = True
            fresh = _layers()  # a restarted replica has no token yet
            mcp = await fresh.client("project-a", create=False)
            with pytest.raises(Exception, match=message):
                await mcp.call("query", {"query": "x"})
            await fresh.close()

    async def test_a_registration_without_a_client_id_raises(self) -> None:
        fake = FakeGBrain()
        with respx.mock() as router:
            router.post(f"{BASE}/admin/api/register-client").mock(
                return_value=httpx.Response(200, json={"status": "registered"})
            )
            router.route(url__startswith=BASE).mock(side_effect=fake.handle)
            layers = _layers()
            with pytest.raises(MemoryBackendError, match="without naming it"):
                await layers.create("project-a")
            await layers.close()

    async def test_an_export_without_a_secret_raises(self) -> None:
        fake = FakeGBrain()
        with respx.mock() as router:
            router.post(f"{BASE}/admin/api/recover-client").mock(
                return_value=httpx.Response(200, json={"clientId": "x"})
            )
            router.route(url__startswith=BASE).mock(side_effect=fake.handle)
            layers = _layers()
            with pytest.raises(MemoryBackendError, match="without its secret"):
                await layers.create("project-a")
            await layers.close()
