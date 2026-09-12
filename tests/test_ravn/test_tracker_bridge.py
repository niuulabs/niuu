"""The CLI bridge uses session workload identity and the platform API."""

import argparse
import json

import httpx
import pytest
import respx

from ravn.cli import tracker_bridge
from ravn.config import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["search", "get", "update_status"])
async def test_tracker_bridge_exchanges_projected_identity(
    tmp_path, monkeypatch, capsys, operation
):
    proof = tmp_path / "token"
    proof.write_text("projected-proof")
    monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_TOKEN_FILE", str(proof))
    monkeypatch.setenv(
        "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL",
        "https://platform.test/api/v1/tokens/workload/exchange",
    )
    monkeypatch.delenv("SKULD__VOLUNDR_API_URL", raising=False)
    monkeypatch.setattr(tracker_bridge, "_settings", lambda: Settings())
    path = "/api/v1/tracker/issues" + ("" if operation == "search" else "/issue-1")
    method = "PATCH" if operation == "update_status" else "GET"
    with respx.mock as router:
        exchange = router.post("https://platform.test/api/v1/tokens/workload/exchange").mock(
            return_value=httpx.Response(201, json={"token": "workload-bearer"})
        )
        request = router.route(method=method, url="https://platform.test" + path).mock(
            return_value=httpx.Response(200, json={"id": "issue-1"})
        )
        run = getattr(tracker_bridge, "_run_" + operation)
        assert (
            await run(argparse.Namespace(query="NIU-1086", issue_id="issue-1", status="Done")) == 0
        )
        assert json.loads(exchange.calls[0].request.content)["token"] == "projected-proof"
        assert request.calls[0].request.headers["Authorization"] == "Bearer workload-bearer"
    assert json.loads(capsys.readouterr().out) == {"id": "issue-1"}


@pytest.mark.asyncio
async def test_tracker_bridge_uses_openshell_platform_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("SKULD__VOLUNDR_API_URL", "https://proxy.test")
    monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_TOKEN_FILE", str(tmp_path / "absent"))
    monkeypatch.delenv("NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", raising=False)
    monkeypatch.setattr(tracker_bridge, "_settings", lambda: Settings())
    with respx.mock as router:
        route = router.get("https://proxy.test/api/v1/tracker/issues").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert await tracker_bridge._run_search(argparse.Namespace(query="NIU-1086")) == 0
        # OpenShell injects the session's short-lived credential at the proxy.
        assert "Authorization" not in route.calls[0].request.headers
