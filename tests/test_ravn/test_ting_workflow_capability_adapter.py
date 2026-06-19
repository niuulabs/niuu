from __future__ import annotations

import json

import pytest

from ravn.adapters.capabilities.ting_workflows import TingWorkflowCapabilityAdapter
from ravn.ports.capability import WorkflowLaunchRequest, WorkflowRunReference


class _FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body


class _FakeAsyncClient:
    calls: list[tuple[str, str, dict]] = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):  # noqa: ANN002
        return None

    async def post(self, url: str, **kwargs):  # noqa: ANN003
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/api/v1/tokens/workload/exchange"):
            assert kwargs["json"]["audiences"] == [
                "volundr-api",
                "forge",
                "ting",
                "mimir",
                "guild",
            ]
            return _FakeResponse(
                201,
                {
                    "token": "exchanged-token",
                    "expiresAt": 9999999999,
                    "principal": {
                        "userId": "owner-1",
                        "tenantId": "tenant-1",
                        "roles": ["volundr:developer"],
                    },
                    "workloadSubject": "system:serviceaccount:nats:valkyrie",
                    "workloadName": "valkyrie-ymir-k8s",
                },
            )
        return _FakeResponse(
            201,
            {
                "workflowId": "wf-1",
                "workflowName": "Incident Investigation",
                "sessionId": "session-1",
                "sessionName": "incident-session",
                "status": "running",
                "slug": "incident-session",
                "clusterName": "ymir",
            },
        )

    async def request(self, method: str, url: str, **kwargs):  # noqa: ANN003
        self.calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/launch"):
            return _FakeResponse(
                201,
                {
                    "workflowId": "wf-1",
                    "workflowName": "Incident Investigation",
                    "sessionId": "session-1",
                    "sessionName": "incident-session",
                    "status": "running",
                    "slug": "incident-session",
                    "clusterName": "ymir",
                },
            )
        if method == "GET" and url.endswith("/api/v1/ting/research/campaigns/proof"):
            return _FakeResponse(
                200,
                {
                    "id": "campaign-1",
                    "slug": "proof",
                    "status": "completed",
                    "workflowId": "wf-1",
                    "workflowName": "Incident Investigation",
                    "sessionId": "session-1",
                    "sessionName": "proof",
                    "activeStageId": "publish",
                    "stageState": [{"stageId": "publish", "status": "complete"}],
                    "canonicalArtifacts": {"summary": "research/campaigns/proof/summary.md"},
                    "updatedAt": "2026-06-18T12:00:00Z",
                },
            )
        if method == "GET" and url.endswith("/api/v1/ting/research/campaigns"):
            return _FakeResponse(
                200,
                [
                    {
                        "id": "campaign-1",
                        "slug": "proof",
                        "status": "completed",
                        "sessionId": "session-1",
                    }
                ],
            )
        if method == "GET" and url.endswith("/api/v1/ting/research/campaigns/proof/artifacts"):
            return _FakeResponse(
                200,
                [
                    {
                        "path": "research/campaigns/proof/summary.md",
                        "title": "Summary",
                        "kind": "summary",
                        "summary": "A concise summary.",
                        "publishState": "published",
                        "sourceIds": ["source-1"],
                    }
                ],
            )
        if method == "GET" and url.endswith("/api/v1/ting/research/campaigns/proof/artifact"):
            return _FakeResponse(
                200,
                {
                    "path": kwargs["params"]["path"],
                    "title": "Summary",
                    "kind": "summary",
                    "summary": "A concise summary.",
                    "publishState": "published",
                    "sourceIds": ["source-1"],
                    "content": "The campaign produced this artifact.",
                },
            )
        if method == "GET" and url.endswith("/api/v1/ting/dispatcher/log"):
            return _FakeResponse(
                200,
                {
                    "events": [
                        {
                            "id": "event-1",
                            "event": "research.completed",
                            "data": {
                                "session_id": "session-1",
                                "slug": "proof",
                                "structured_outcome": {"summary": "done"},
                            },
                            "timestamp": "2026-06-18T12:01:00Z",
                        }
                    ],
                    "total": 1,
                },
            )
        if method == "GET" and url.endswith("/api/v1/ting/sessions/session-1"):
            return _FakeResponse(
                200,
                {
                    "session_id": "session-1",
                    "status": "running",
                    "run_name": "proof",
                    "cluster_name": "ymir",
                },
            )
        return _FakeResponse(
            200,
            [
                {
                    "id": "wf-1",
                    "name": "Incident Investigation",
                    "description": "Investigate signals",
                    "version": "1.0.0",
                    "tags": ["incident", "k8s"],
                    "scope": "system",
                }
            ],
        )


@pytest.mark.asyncio
async def test_ting_workflow_adapter_exchanges_workload_token_and_discovers_workflows(
    tmp_path,
    monkeypatch,
) -> None:
    token_file = tmp_path / "token.jwt"
    token_file.write_text("projected-token", encoding="utf-8")
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(
        "ravn.adapters.capabilities.ting_workflows.httpx.AsyncClient",
        _FakeAsyncClient,
    )

    adapter = TingWorkflowCapabilityAdapter(
        base_url="https://yggdrasil.niuu.world",
        workload_token_file=str(token_file),
    )

    workflows = await adapter.list_workflows()

    assert workflows[0].workflow_id == "wf-1"
    assert workflows[0].tags == ["incident", "k8s"]
    exchange_call, list_call = _FakeAsyncClient.calls
    assert exchange_call[0] == "POST"
    assert exchange_call[2]["json"] == {
        "token": "projected-token",
        "audiences": ["volundr-api", "forge", "ting", "mimir", "guild"],
    }
    assert list_call[0] == "GET"
    assert list_call[2]["headers"]["Authorization"] == "Bearer exchanged-token"


@pytest.mark.asyncio
async def test_ting_workflow_adapter_launches_with_provenance(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "token.jwt"
    token_file.write_text("projected-token", encoding="utf-8")
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(
        "ravn.adapters.capabilities.ting_workflows.httpx.AsyncClient",
        _FakeAsyncClient,
    )

    adapter = TingWorkflowCapabilityAdapter(
        base_url="https://yggdrasil.niuu.world",
        workload_token_file=str(token_file),
    )

    result = await adapter.launch_workflow(
        WorkflowLaunchRequest(
            workflow_id="wf-1",
            prompt="investigate this",
            session_name="incident-session",
            connection_id="valhalla",
            provenance={"signal_id": "sig-1"},
        )
    )

    assert result.session_id == "session-1"
    launch_call = _FakeAsyncClient.calls[-1]
    assert launch_call[0] == "POST"
    assert launch_call[1].endswith("/api/v1/ting/workflows/wf-1/launch")
    assert json.loads(json.dumps(launch_call[2]["json"])) == {
        "prompt": "investigate this",
        "sessionName": "incident-session",
        "connectionId": "valhalla",
        "provenance": {
            "signal_id": "sig-1",
            "workload_identity": {
                "owner_id": "owner-1",
                "tenant_id": "tenant-1",
                "workload_subject": "system:serviceaccount:nats:valkyrie",
                "workload_name": "valkyrie-ymir-k8s",
                "roles": ["volundr:developer"],
            },
        },
    }
    assert result.owner_id == "owner-1"
    assert result.tenant_id == "tenant-1"
    assert result.workload_subject == "system:serviceaccount:nats:valkyrie"


@pytest.mark.asyncio
async def test_ting_workflow_adapter_reads_campaign_status_artifacts_and_events(
    tmp_path,
    monkeypatch,
) -> None:
    token_file = tmp_path / "token.jwt"
    token_file.write_text("projected-token", encoding="utf-8")
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(
        "ravn.adapters.capabilities.ting_workflows.httpx.AsyncClient",
        _FakeAsyncClient,
    )

    adapter = TingWorkflowCapabilityAdapter(
        base_url="https://yggdrasil.niuu.world",
        workload_token_file=str(token_file),
    )

    status = await adapter.get_workflow_status(reference=WorkflowRunReference(slug="proof"))
    artifacts = await adapter.list_workflow_artifacts(reference=WorkflowRunReference(slug="proof"))
    content = await adapter.read_workflow_artifact(
        reference=WorkflowRunReference(slug="proof"),
        path="research/campaigns/proof/summary.md",
    )
    events = await adapter.list_workflow_events(
        reference=WorkflowRunReference(session_id="session-1", slug="proof"),
    )

    assert status.state == "completed"
    assert status.terminal is True
    assert artifacts[0].canonical is True
    assert artifacts[0].publish_state == "published"
    assert content.content == "The campaign produced this artifact."
    assert any(event.event_type == "research.completed" for event in events)
