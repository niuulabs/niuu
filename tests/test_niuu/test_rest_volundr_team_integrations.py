"""The gateway carries a project's team-integration routes and team installs to Forge."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from niuu.adapters.inbound.rest_volundr import _team_integration_operation
from tests.test_niuu.test_rest_volundr import _client, _headers, _instance

PREFIX = "/api/v1/forge"
LOCAL = _instance(
    "local", base_url="embedded://local", is_default=True, config={"transport": "embedded"}
)


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "integrations", True),
        ("POST", "integrations", True),
        ("PUT", "integrations", False),
        ("GET", "integrations/catalog", True),
        ("POST", "integrations/catalog", False),
        ("GET", "integrations/routes", True),
        ("GET", "integrations/usage", True),
        ("PATCH", "integrations/c1", True),
        ("DELETE", "integrations/c1", True),
        ("GET", "integrations/c1", False),
        ("PUT", "integrations/routes/errors", True),
        ("DELETE", "integrations/routes/errors", True),
        ("POST", "integrations/routes/errors", False),
        ("PUT", "integrations/routes/errors/x", False),
        ("GET", "members", False),
    ],
)
def test_team_integration_operations(method, path, allowed):
    assert _team_integration_operation(method, path.split("/")) is allowed


def _forge(calls: list) -> FastAPI:
    app = FastAPI()

    @app.api_route(
        f"{PREFIX}/projects/{{project_id}}/integrations/routes/{{event}}", methods=["PUT"]
    )
    async def route(project_id: str, event: str, body: dict):
        calls.append(("route", event, body))
        return {"event": event, **body}

    @app.post(f"{PREFIX}/integrations/oauth/authorize-team")
    async def authorize(request: Request, body: dict):
        calls.append(
            (
                "authorize",
                body["slug"],
                request.headers.get("x-auth-user-id"),
                str(request.base_url),
            )
        )
        return {"url": f"https://provider/authorize?redirect={request.base_url}"}

    @app.get(f"{PREFIX}/integrations/oauth/callback")
    async def callback(request: Request):
        calls.append(("callback", dict(request.query_params)))
        return HTMLResponse("<h2>Connected to Slack</h2>")

    return app


def test_a_route_is_set_through_put():
    calls: list = []
    client = _client([LOCAL], embedded_forge_app=_forge(calls))
    pid = uuid4()
    response = client.put(
        f"{PREFIX}/projects/{pid}/integrations/routes/errors",
        json={"connection_id": "c1", "deliver_to": "channel"},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert calls == [("route", "errors", {"connection_id": "c1", "deliver_to": "channel"})]


def test_team_install_runs_on_this_nodes_forge_with_the_callers_origin():
    calls: list = []
    client = _client([LOCAL], embedded_forge_app=_forge(calls))
    started = client.post(
        f"{PREFIX}/integrations/oauth/authorize-team",
        json={"slug": "slack", "project_id": str(uuid4())},
        headers=_headers(),
    )
    assert started.status_code == 200
    assert calls[0][:3] == ("authorize", "slack", "user-a")
    assert calls[0][3] == "http://testserver/"

    back = client.get(f"{PREFIX}/integrations/oauth/callback?code=c&state=s")
    assert back.status_code == 200 and "Connected to Slack" in back.text
    assert back.headers["content-type"].startswith("text/html")
    assert calls[1] == ("callback", {"code": "c", "state": "s"})


def test_a_node_without_its_own_forge_refuses_team_installs():
    client = _client([LOCAL])
    response = client.get(f"{PREFIX}/integrations/oauth/callback?code=c&state=s")
    assert response.status_code == 502
    assert "home node" in response.json()["detail"]
