import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from mimir.adapters.local import LocalKnowledgeDeploymentAdapter
from mimir.ports.deployment import DeploymentRequest
from mimir.router import MimirRouter
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter

_TOKEN_OUTPUT = "created niuu-research\n  gbrain_testtoken123\n"


def _gbrain_adapter(tmp_path) -> LocalKnowledgeDeploymentAdapter:
    """A local target whose service manager and gbrain executable are stubbed."""
    adapter = LocalKnowledgeDeploymentAdapter(root=str(tmp_path), gbrain_command=["gbrain"])
    adapter._install = AsyncMock()
    adapter._view = AsyncMock(return_value={"ready": True})

    async def run(*args, env=None):
        return _TOKEN_OUTPUT if "auth" in args else ""

    adapter._run = AsyncMock(side_effect=run)
    return adapter


@pytest.mark.asyncio
async def test_local_deploy_mount_resume_and_inspection(tmp_path, monkeypatch):
    adapter = _gbrain_adapter(tmp_path)
    request = DeploymentRequest(name="research", backend="gbrain")
    await adapter.deploy(request)
    await adapter.deploy(request)
    assert adapter._install.await_count == 1
    record = json.loads((tmp_path / "research/deployment.json").read_text())
    assert "warden_id" not in record
    assert "dream" not in record
    assert record["backend"] == "gbrain"
    assert record["health_url"].endswith("/health")
    assert not record["health_url"].endswith("/mimir/health")
    assert record["mount"]["adapter"] == "mimir.adapters.gbrain.GBrainMimirAdapter"
    assert (tmp_path / "research/deployment.json").stat().st_mode & 0o777 == 0o600
    # The mounted store is the deployed gbrain; stand a store in for it so the
    # router can be exercised without a live gbrain.
    adapter._ports["research"] = InMemoryMimirAdapter()
    app = FastAPI()
    app.include_router(
        MimirRouter(
            InMemoryMimirAdapter(),
            deployment=adapter,
            auth=EnvoyHeaderAuthenticationAdapter(),
        ).router
    )
    with TestClient(app) as client:
        assert client.get("/instances/inspect?mount=research").status_code == 200
        assert client.get("/graph?mount=research").json() == {"nodes": [], "edges": []}
        assert client.get("/deployments/research").status_code == 403
        assert client.post("/deployments/research/delete").status_code == 403
    logs = await adapter.inspect_deployment("local/research")
    assert logs["logs"] == {"stdout": "", "stderr": ""}
    with pytest.raises(ValueError):
        await adapter.control("../other", "stop")


@pytest.mark.asyncio
async def test_missing_executable_and_install_failure(tmp_path):
    adapter = LocalKnowledgeDeploymentAdapter(root=str(tmp_path))
    with pytest.raises(ValueError, match="gbrain executable"):
        await adapter.deploy(DeploymentRequest(name="missing", backend="gbrain"))
    adapter = _gbrain_adapter(tmp_path)
    adapter._install = AsyncMock(side_effect=RuntimeError("launchd unavailable"))
    with pytest.raises(RuntimeError, match="launchd"):
        await adapter.deploy(DeploymentRequest(name="failed", backend="gbrain"))
    assert (
        json.loads((tmp_path / "failed/deployment.json").read_text())["error"]
        == "launchd unavailable"
    )


@pytest.mark.asyncio
async def test_delete_local_instance_removes_owned_data(tmp_path, monkeypatch):
    adapter = _gbrain_adapter(tmp_path)
    await adapter.deploy(DeploymentRequest(name="delete-me", backend="gbrain"))
    adapter.mounted_ports()
    service = tmp_path / "test.plist"
    service.write_text("service")
    adapter._service_file = lambda _: service
    adapter._run = AsyncMock(return_value="- 0 dev.niuu.knowledge.delete-me")
    monkeypatch.setattr("mimir.adapters.local.sys.platform", "darwin")
    other = tmp_path / "keep.txt"
    other.write_text("keep")
    assert await adapter.control("delete-me", "delete") == {"name": "delete-me", "deleted": True}
    adapter._run.assert_any_await("launchctl", "unload", "-w", str(service))
    assert not service.exists()
    assert not (tmp_path / "delete-me").exists()
    assert other.read_text() == "keep"
    assert adapter.mounted_ports() == []
    with pytest.raises(ValueError, match="Unknown local deployment"):
        await adapter.control("../keep.txt", "delete")


@pytest.mark.asyncio
async def test_delete_preserves_data_when_service_cannot_be_stopped(tmp_path):
    adapter = _gbrain_adapter(tmp_path)
    await adapter.deploy(DeploymentRequest(name="keep", backend="gbrain"))
    adapter._run = AsyncMock(side_effect=RuntimeError("service manager unavailable"))
    adapter._service_file = lambda _: tmp_path / "service"
    (tmp_path / "service").touch()
    with pytest.raises(RuntimeError, match="service manager"):
        await adapter.control("keep", "delete")
    assert (tmp_path / "keep/deployment.json").exists()


def test_managed_gbrain_registry_descriptor_contains_reference_not_token(tmp_path):
    directory = tmp_path / "brain"
    directory.mkdir()
    (directory / "deployment.json").write_text(
        json.dumps(
            {
                "name": "brain",
                "initialized": True,
                "mount": {
                    "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
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
        MimirRouter(
            InMemoryMimirAdapter(),
            deployment=adapter,
            auth=EnvoyHeaderAuthenticationAdapter(),
        ).router
    )
    with TestClient(app) as client:
        response = client.get("/registry/mounts")
        assert response.status_code == 200
        assert "private-token" not in response.text
        entry = next(item for item in response.json() if item["name"] == "brain")
        assert entry["adapter"] == "mimir.adapters.gbrain.GBrainMimirAdapter"
        assert entry["kwargs"]["api_token_file"] == str(directory / "session-token")
    assert (directory / "session-token").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_only_gbrain_is_offered_and_only_when_an_executable_is_configured(tmp_path):
    without = LocalKnowledgeDeploymentAdapter(root=str(tmp_path))
    assert (await without.list_deployments())["backends"] == []

    with_gbrain = LocalKnowledgeDeploymentAdapter(root=str(tmp_path), gbrain_command=["gbrain"])
    assert (await with_gbrain.list_deployments())["backends"] == ["gbrain"]


def test_the_markdown_mimir_backend_is_no_longer_deployable():
    with pytest.raises(ValueError, match="gbrain"):
        DeploymentRequest(name="research", backend="mimir")
