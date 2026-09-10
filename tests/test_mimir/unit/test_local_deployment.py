import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.local import LocalKnowledgeDeploymentAdapter
from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.ports.deployment import DeploymentRequest
from mimir.router import MimirRouter


@pytest.mark.asyncio
async def test_local_deploy_mount_resume_and_inspection(tmp_path, monkeypatch):
    adapter = LocalKnowledgeDeploymentAdapter(
        root=str(tmp_path), warden_defaults={"features": {"recap_enabled": False}}
    )
    adapter._install = AsyncMock()
    adapter._view = AsyncMock(return_value={"ready": True})
    specs = []
    adapter.wardens.create = lambda spec: specs.append(spec) or spec
    adapter.wardens.install = lambda *_: None
    request = DeploymentRequest(name="research", backend="mimir", warden=True)
    await adapter.deploy(request)
    await adapter.deploy(request)
    assert adapter._install.await_count == 1
    assert not specs[0].features.recap_enabled
    record = json.loads((tmp_path / "research/deployment.json").read_text())
    assert record["warden_id"] == "research-warden"
    assert (tmp_path / "research/deployment.json").stat().st_mode & 0o777 == 0o600
    app = FastAPI()
    app.include_router(
        MimirRouter(MarkdownMimirAdapter(root=tmp_path / "base"), deployment=adapter).router
    )
    with TestClient(app) as client:
        assert client.get("/instances/inspect?mount=research").status_code == 200
        assert client.get("/graph?mount=research").json() == {"nodes": [], "edges": []}
        assert client.get("/deployments/research").status_code == 403
        assert client.post("/deployments/research/delete").status_code == 403
    logs = await adapter.inspect_deployment("local/research")
    assert logs["logs"] == {"stdout": "", "stderr": "", "dream": ""}
    with pytest.raises(ValueError):
        await adapter.control("../other", "stop")


@pytest.mark.asyncio
async def test_explicit_maintenance_capabilities_and_failure(tmp_path):
    adapter = LocalKnowledgeDeploymentAdapter(root=str(tmp_path))
    with pytest.raises(ValueError, match="warden"):
        await adapter.deploy(DeploymentRequest(name="denied", backend="mimir", warden=True))
    with pytest.raises(ValueError, match="gbrain executable"):
        await adapter.deploy(DeploymentRequest(name="missing", backend="gbrain"))
    adapter._install = AsyncMock(side_effect=RuntimeError("launchd unavailable"))
    with pytest.raises(RuntimeError, match="launchd"):
        await adapter.deploy(DeploymentRequest(name="failed", backend="mimir"))
    assert (
        json.loads((tmp_path / "failed/deployment.json").read_text())["error"]
        == "launchd unavailable"
    )


@pytest.mark.asyncio
async def test_delete_local_instance_stops_maintenance_and_removes_owned_data(
    tmp_path, monkeypatch
):
    from unittest.mock import Mock

    adapter = LocalKnowledgeDeploymentAdapter(root=str(tmp_path), warden_defaults={})
    adapter._install = AsyncMock()
    adapter._view = AsyncMock(return_value={"ready": True})
    adapter.wardens.create = lambda spec: spec
    adapter.wardens.install = lambda *_: None
    adapter.wardens.uninstall = Mock()
    adapter.wardens.delete = Mock()
    await adapter.deploy(DeploymentRequest(name="delete-me", backend="mimir", warden=True))
    adapter.mounted_ports()
    service = tmp_path / "test.plist"
    service.write_text("service")
    adapter._service_file = lambda _: service
    adapter._run = AsyncMock(return_value="- 0 dev.niuu.knowledge.delete-me")
    monkeypatch.setattr("mimir.adapters.local.sys.platform", "darwin")
    other = tmp_path / "keep.txt"
    other.write_text("keep")
    assert await adapter.control("delete-me", "delete") == {"name": "delete-me", "deleted": True}
    adapter.wardens.uninstall.assert_called_once_with("delete-me-warden")
    adapter.wardens.delete.assert_called_once_with("delete-me-warden")
    adapter._run.assert_any_await("launchctl", "unload", "-w", str(service))
    assert not service.exists()
    assert not (tmp_path / "delete-me").exists()
    assert other.read_text() == "keep"
    assert adapter.mounted_ports() == []
    with pytest.raises(ValueError, match="Unknown local deployment"):
        await adapter.control("../keep.txt", "delete")


@pytest.mark.asyncio
async def test_delete_preserves_data_when_service_cannot_be_stopped(tmp_path):
    adapter = LocalKnowledgeDeploymentAdapter(root=str(tmp_path))
    adapter._install = AsyncMock()
    adapter._view = AsyncMock(return_value={"ready": True})
    await adapter.deploy(DeploymentRequest(name="keep", backend="mimir"))
    adapter._run = AsyncMock(side_effect=RuntimeError("service manager unavailable"))
    adapter._service_file = lambda _: tmp_path / "service"
    (tmp_path / "service").touch()
    with pytest.raises(RuntimeError, match="service manager"):
        await adapter.control("keep", "delete")
    assert (tmp_path / "keep/deployment.json").exists()


@pytest.mark.asyncio
async def test_new_local_warden_receives_only_explicit_overrides(tmp_path):
    adapter = LocalKnowledgeDeploymentAdapter(
        root=str(tmp_path),
        warden_defaults={"model": "old", "schedules": {"staleness_trigger_schedule_hours": 12}},
    )
    adapter._install = AsyncMock()
    adapter._view = AsyncMock(return_value={"ready": True})
    specs = []
    adapter.wardens.create = lambda spec: specs.append(spec) or spec
    adapter.wardens.install = lambda *_: None
    await adapter.deploy(
        DeploymentRequest(
            name="custom",
            backend="mimir",
            warden=True,
            warden_overrides={
                "model": "new",
                "persona": "research",
                "dream_cycle_cron_expression": "0 4 * * *",
            },
        )
    )
    assert specs[0].model == "new"
    assert specs[0].persona == "research"
    assert specs[0].schedules.dream_cycle_cron_expression == "0 4 * * *"
    assert specs[0].schedules.staleness_trigger_schedule_hours == 12
    assert adapter.warden_defaults["model"] == "old"


def test_managed_gbrain_registry_descriptor_contains_reference_not_token(tmp_path):
    directory = tmp_path / "brain"
    directory.mkdir()
    (directory / "deployment.json").write_text(
        json.dumps(
            {
                "name": "brain",
                "initialized": True,
                "mount": {
                    "adapter": "ravn.adapters.mimir.gbrain.GBrainMimirAdapter",
                    "kwargs": {
                        "mcp_url": "http://127.0.0.1:3000/mcp",
                        "api_token": "private-token",
                    },
                },
            }
        )
    )
    adapter = LocalKnowledgeDeploymentAdapter(root=str(tmp_path))
    app = FastAPI()
    app.include_router(
        MimirRouter(MarkdownMimirAdapter(root=tmp_path / "base"), deployment=adapter).router
    )
    with TestClient(app) as client:
        response = client.get("/registry/mounts")
        assert response.status_code == 200
        assert "private-token" not in response.text
        entry = next(item for item in response.json() if item["name"] == "brain")
        assert entry["adapter"] == "ravn.adapters.mimir.gbrain.GBrainMimirAdapter"
        assert entry["kwargs"]["api_token_file"] == str(directory / "session-token")
    assert (directory / "session-token").stat().st_mode & 0o777 == 0o600
