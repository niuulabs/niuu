"""Cross-host routing must preserve project/session identity and partial failures."""

from uuid import uuid4

import httpx
import respx

from tests.test_niuu.test_rest_volundr import _client, _headers, _instance


def client():
    return _client(
        [
            _instance("thor", base_url="http://thor.test", is_default=True),
            _instance("spark", base_url="http://spark.test"),
        ]
    )


@respx.mock
def test_project_replicas_retain_host_identity_and_partial_failure_is_visible():
    project_id = str(uuid4())
    respx.get("http://thor.test/api/v1/forge/projects").mock(
        return_value=httpx.Response(200, json=[{"id": project_id}])
    )
    spark = respx.get("http://spark.test/api/v1/forge/projects")
    spark.mock(return_value=httpx.Response(200, json=[{"id": project_id}]))
    api = client()
    response = api.get("/api/v1/forge/projects", headers=_headers())
    assert {p["instance_id"] for p in response.json()} == {"thor", "spark"}
    spark.mock(return_value=httpx.Response(503))
    response = api.get("/api/v1/forge/projects", headers=_headers())
    assert response.status_code == 200 and len(response.json()) == 1
    assert response.headers["X-Forge-Unavailable-Instances"] == "spark"


@respx.mock
def test_explicit_host_routes_project_receipts_and_registration():
    project_id = str(uuid4())
    receipt_id = str(uuid4())
    route = respx.post(
        f"http://spark.test/api/v1/forge/projects/{project_id}/receipts/{receipt_id}/ack"
    )
    route.mock(return_value=httpx.Response(200, json={"acknowledged": True}))
    response = client().post(
        f"/api/v1/forge/projects/{project_id}/receipts/{receipt_id}/ack?instance_id=spark",
        headers=_headers(),
    )
    assert response.status_code == 200 and route.called
    register = respx.post("http://spark.test/api/v1/forge/projects").mock(
        return_value=httpx.Response(201, json={"id": project_id})
    )
    response = client().post(
        "/api/v1/forge/projects",
        headers=_headers(),
        json={"id": project_id, "instance_id": "spark"},
    )
    assert response.json()["instance_id"] == "spark" and register.called


@respx.mock
def test_session_collision_and_explicit_target_never_fall_back_to_other_host():
    session_id = str(uuid4())
    for host in ("thor", "spark"):
        respx.get(f"http://{host}.test/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[{"id": session_id}])
        )
    response = client().get("/api/v1/forge/sessions", headers=_headers())
    assert len(response.json()) == 2
    response = client().get("/api/v1/forge/sessions?instance_id=spark", headers=_headers())
    assert [s["instance_id"] for s in response.json()] == ["spark"]
    route = respx.get(f"http://spark.test/api/v1/forge/sessions/{session_id}").mock(
        return_value=httpx.Response(404)
    )
    response = client().get(
        f"/api/v1/forge/sessions/{session_id}?instance_id=spark", headers=_headers()
    )
    assert response.status_code == 404 and route.called


@respx.mock
def test_checkout_discovery_and_creation_route_only_to_selected_host():
    project_id = str(uuid4())
    for operation in ["discover", "connect"]:
        route = respx.post(f"http://spark.test/api/v1/forge/projects/{operation}").mock(
            return_value=httpx.Response(
                201 if operation == "connect" else 200, json={"id": project_id}
            )
        )
        response = client().post(
            f"/api/v1/forge/projects/{operation}?instance_id=spark",
            headers=_headers(),
            json={"workspace_path": "/home/horde/projects/kit"},
        )
        assert response.status_code == (201 if operation == "connect" else 200)
        assert response.json()["instance_id"] == "spark" and route.called
        assert route.calls[0].request.content == b'{"workspace_path":"/home/horde/projects/kit"}'
    route = respx.post("http://spark.test/api/v1/forge/projects/connect").mock(
        return_value=httpx.Response(404, json={"detail": "Not Found"})
    )
    response = client().post(
        "/api/v1/forge/projects/connect",
        headers=_headers(),
        json={"instance_id": "spark", "workspace_path": "/absent"},
    )
    assert response.status_code == 404 and route.called
