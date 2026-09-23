"""Local-only session reads must never fan out or guess a remote guild owner."""

from types import SimpleNamespace

import pytest
import respx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import Response

from tests.test_niuu.test_rest_volundr import _client, _headers, _instance


def local(**kwargs):
    return _instance(
        "local", base_url="embedded://forge", config={"transport": "embedded"}, **kwargs
    )


def remote(**kwargs):
    return _instance("remote", base_url="http://remote.example.test", **kwargs)


@pytest.mark.parametrize("query", ["", "&status=archived", "&include_archived=true&project_id=p"])
@respx.mock(assert_all_called=False)
def test_local_list_contacts_only_embedded_even_when_remote_is_default(query):
    seen = []
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def sessions(request: Request):
        seen.append(dict(request.query_params))
        return [
            {"id": "own", "instance_id": "untrusted-row-owner", "read_state": {"is_unread": True}}
        ]

    unexpected = respx.get("http://remote.example.test/api/v1/forge/sessions").mock(
        return_value=Response(500)
    )
    client = _client([remote(is_default=True), local()], embedded_forge_app=embedded)
    response = client.get("/api/v1/forge/sessions?scope=local" + query, headers=_headers())
    assert response.status_code == 200
    assert response.headers["X-Forge-Session-Scope"] == "local"
    assert [s["id"] for s in response.json()] == ["own"]
    assert response.json()[0]["instance_id"] == "local"
    assert response.json()[0]["read_state"]["is_unread"] is True
    assert seen == [dict(response.request.url.params)]
    assert not unexpected.called


@respx.mock
def test_legacy_guild_list_still_aggregates():
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def sessions():
        return [{"id": "own"}]

    guild = respx.get("http://remote.example.test/api/v1/forge/sessions").mock(
        return_value=Response(200, json=[{"id": "remote-owned"}])
    )
    client = _client([local(), remote()], embedded_forge_app=embedded)
    for suffix in ("", "?scope=guild"):
        response = client.get("/api/v1/forge/sessions" + suffix, headers=_headers())
        assert response.status_code == 200
        assert {s["id"] for s in response.json()} == {"own", "remote-owned"}
        assert response.headers.get("X-Forge-Session-Scope") != "local"
    assert guild.call_count == 2


@pytest.mark.parametrize("code", [401, 403, 500, 503])
def test_failed_local_read_is_not_empty_success(code):
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def sessions():
        return JSONResponse({"detail": "fixture failure"}, status_code=code)

    response = _client([local()], embedded_forge_app=embedded).get(
        "/api/v1/forge/sessions?scope=local", headers=_headers()
    )
    assert response.status_code == code
    assert response.headers.get("X-Forge-Session-Scope") is None


@pytest.mark.parametrize("payload", [{"sessions": []}, ["bad row"], None])
def test_malformed_local_payload_fails(payload):
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def sessions():
        return payload

    response = _client([local()], embedded_forge_app=embedded).get(
        "/api/v1/forge/sessions?scope=local", headers=_headers()
    )
    assert response.status_code == 502


@pytest.mark.parametrize(
    "instances",
    [
        [remote(is_default=True)],
        [local(enabled=False), remote()],
        [local(tenant_id="hidden-tenant"), remote()],
        [
            local(),
            _instance("ambiguous", base_url="embedded://second", config={"transport": "embedded"}),
        ],
    ],
)
@pytest.mark.parametrize("path", ["/sessions", "/sessions/stream", "/sessions/own"])
@respx.mock
def test_missing_hidden_disabled_or_ambiguous_local_never_uses_remote(instances, path):
    response = _client(instances, embedded_forge_app=FastAPI()).get(
        "/api/v1/forge" + path + "?scope=local", headers=_headers()
    )
    assert response.status_code == 503
    assert not respx.calls


@pytest.mark.parametrize("path", ["/sessions", "/sessions/stream", "/sessions/own"])
def test_conflicting_target_or_invalid_scope_rejected(path):
    client = _client([local(), remote()], embedded_forge_app=FastAPI())
    for query in ("scope=local&instance_id=remote", "scope=unknown"):
        response = client.get("/api/v1/forge" + path + "?" + query, headers=_headers())
        assert response.status_code == 422


@respx.mock
def test_matching_explicit_local_id_is_allowed_and_detail_cannot_escape_to_guild():
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def sessions():
        return []

    @embedded.get("/api/v1/forge/sessions/{session_id}")
    async def session(session_id: str):
        return JSONResponse({"detail": "not local"}, status_code=404)

    client = _client([remote(is_default=True), local()], embedded_forge_app=embedded)
    response = client.get(
        "/api/v1/forge/sessions?scope=local&instance_id=local", headers=_headers()
    )
    assert response.status_code == 200 and response.json() == []
    assert response.headers["X-Forge-Session-Scope"] == "local"
    response = client.get("/api/v1/forge/sessions/foreign?scope=local", headers=_headers())
    assert response.status_code == 404
    assert not respx.calls


@respx.mock
def test_local_stream_uses_embedded_scoped_stream_not_remote_default(
    monkeypatch, repository, pod_manager
):
    import json
    from datetime import UTC, datetime

    from niuu.adapters.inbound import rest_volundr
    from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter
    from volundr.domain.models import EventType, RealtimeEvent
    from volundr.domain.services import SessionEventStream, SessionService

    async def finite_merge(sources, **_kwargs):
        assert list(sources) == ["local"]
        for source in sources.values():
            async for name, data in source():
                yield f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()

    monkeypatch.setattr(rest_volundr, "merge_events", finite_merge)

    def updated(session_id: str, owner_id: str) -> RealtimeEvent:
        return RealtimeEvent(
            type=EventType.SESSION_UPDATED,
            data={"id": session_id, "owner_id": owner_id, "tenant_id": "tenant-a"},
            timestamp=datetime.now(UTC),
        )

    class Broadcaster:
        async def subscribe(self):
            yield updated("foreign", "user-b")
            yield updated("own", "user-a")

    sessions = SessionService(
        repository=repository,
        pod_manager=pod_manager,
        authorization=SimpleRoleAuthorizationAdapter(),
    )
    embedded = FastAPI()
    embedded.state.session_event_stream = SessionEventStream(Broadcaster(), sessions)
    client = _client([remote(is_default=True), local()], embedded_forge_app=embedded)
    response = client.get("/api/v1/forge/sessions/stream?scope=local", headers=_headers())
    assert response.status_code == 200
    assert response.headers["X-Forge-Session-Scope"] == "local"
    assert '"id": "own"' in response.text
    # The embedded path is not a way around the Forge's own scoping.
    assert '"id": "foreign"' not in response.text
    assert not respx.calls


def test_local_stream_refuses_a_subscriber_the_forge_cannot_scope():
    def refuse(_principal):
        raise PermissionError("An authenticated principal is required to list sessions")

    async def never(_principal):  # pragma: no cover - authorize refuses first
        yield

    embedded = FastAPI()
    embedded.state.session_event_stream = SimpleNamespace(authorize=refuse, subscribe=never)
    response = _client([local()], embedded_forge_app=embedded).get(
        "/api/v1/forge/sessions/stream?scope=local", headers=_headers()
    )
    assert response.status_code == 401


def test_local_stream_without_broadcaster_fails():
    response = _client([local()], embedded_forge_app=FastAPI()).get(
        "/api/v1/forge/sessions/stream?scope=local", headers=_headers()
    )
    assert response.status_code == 503


@respx.mock
def test_local_feature_probe_and_read_state_do_not_visit_remote_default():
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/feature-flags")
    async def flags():
        return {"mini_mode": True}

    @embedded.get("/api/v1/forge/sessions/own")
    async def session():
        return {"id": "own"}

    @embedded.api_route("/api/v1/forge/sessions/own/read-state", methods=["GET", "PATCH"])
    async def read_state(request: Request):
        assert request.headers["x-auth-user-id"] == "user-a"
        return {"revision": 2}

    client = _client([remote(is_default=True), local()], embedded_forge_app=embedded)
    response = client.get("/api/v1/forge/feature-flags?scope=local", headers=_headers())
    assert response.status_code == 200 and response.json()["mini_mode"] is True
    assert response.json()["capabilities"]["local_session_scope"] is True
    path = "/api/v1/forge/sessions/own/read-state?scope=local"
    assert client.get(path, headers=_headers()).json() == {"revision": 2}
    assert client.patch(path, headers=_headers(), json={"state": "unread"}).json() == {
        "revision": 2
    }
    assert not respx.calls


def test_local_transport_is_required_and_non_json_is_not_silently_empty():
    response = _client([local()]).get("/api/v1/forge/sessions?scope=local", headers=_headers())
    assert response.status_code == 502
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def sessions():
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse("not JSON")

    response = _client([local()], embedded_forge_app=embedded).get(
        "/api/v1/forge/sessions?scope=local", headers=_headers()
    )
    assert response.status_code == 502


@respx.mock
def test_native_direct_hosts_do_not_duplicate_guild_rows_or_collapse_same_names():
    """Each configured node contributes its own rows, even with mutual registries."""
    results = []
    for host in ("thor", "spark"):
        embedded = FastAPI()
        own_rows = [
            {"id": f"{host}-running", "name": "same name", "status": "running"},
            {"id": f"{host}-stopped", "name": "same name", "status": "stopped"},
        ]

        @embedded.get("/api/v1/forge/sessions")
        async def sessions():
            return own_rows

        client = _client([local(), remote(is_default=True)], embedded_forge_app=embedded)
        response = client.get("/api/v1/forge/sessions?scope=local", headers=_headers())
        assert response.status_code == 200
        results.extend((host, row["id"]) for row in response.json())
    assert results == [
        ("thor", "thor-running"),
        ("thor", "thor-stopped"),
        ("spark", "spark-running"),
        ("spark", "spark-stopped"),
    ]
    assert len(results) == len(set(results)) == 4
    assert not respx.calls


@respx.mock
def test_explicit_legacy_registry_owner_is_unchanged():
    selected = respx.get("http://remote.example.test/api/v1/forge/sessions/remote-owned").mock(
        return_value=Response(200, json={"id": "remote-owned"})
    )
    client = _client([local(), remote()], embedded_forge_app=FastAPI())
    response = client.get(
        "/api/v1/forge/sessions/remote-owned?instance_id=remote", headers=_headers()
    )
    assert response.status_code == 200 and response.json()["instance_id"] == "remote"
    assert selected.call_count == 1
