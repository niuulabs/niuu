"""A workflow memory well survives registry resolution and session materialization."""

import json

import httpx
import pytest
import respx
import yaml

from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from ravn.adapters.tools.mimir_tools import MimirReadTool, MimirSearchTool, MimirWriteTool
from ravn.cli.runtime_builders import _build_mimir
from ravn.config import Settings
from ting.domain.services.dispatch_service import _resolve_mimir_registry_refs
from ting.domain.workflow_snapshot import workflow_mimir_from_snapshot
from volundr.adapters.outbound.contributors.ravn_flock import _build_ravn_config


@respx.mock
async def test_gbrain_well_reaches_runtime_with_tools_and_guidance(tmp_path):
    registry_path = tmp_path / "registry.json"
    token_path = tmp_path / "token"
    token_path.write_text("test-token")
    store = MimirRegistryStore(registry_path)
    entry = store.save_entry(
        MimirRegistryEntry(
            name="research-brain",
            adapter="ravn.adapters.mimir.gbrain.GBrainMimirAdapter",
            kwargs={"mcp_url": "https://brain.test/mcp", "api_token_file": str(token_path)},
        )
    )
    snapshot = {
        "resource_nodes": [
            {
                "id": "brain",
                "kind": "resource",
                "resourceType": "mimir",
                "label": entry.name,
                "registryEntryId": entry.id,
            }
        ]
    }
    payload = _resolve_mimir_registry_refs(
        workflow_mimir_from_snapshot(snapshot), registry_path=str(registry_path)
    )
    config_text = _build_ravn_config(
        persona="researcher",
        persona_override={},
        global_llm=None,
        index=1,
        peer_id="researcher",
        base_port=7000,
        all_personas=["researcher"],
        skuld_peer_id="skuld",
        static_mesh_peers=[],
        mimir_config=payload,
        sleipnir_publish_urls=[],
    )
    assert "test-token" not in config_text
    config = yaml.safe_load(config_text)
    guidance = config["persona_overrides"]["system_prompt_extra"]
    assert "mimir_search" in guidance and "gbrain" in guidance and "raw-source" in guidance
    runtime = _build_mimir(Settings.model_validate(config))
    calls = []

    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        params = json.loads(request.content)["params"]
        calls.append(params["name"])
        result = {
            "structuredContent": [
                {"slug": "notes/finding", "title": "Finding", "content": "Evidence"}
            ]
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    respx.post("https://brain.test/mcp").mock(side_effect=respond)
    search = await MimirSearchTool(runtime).execute({"query": "finding"})
    assert not search.is_error
    read = await MimirReadTool(runtime).execute({"path": "notes/finding"})
    assert read.content == "Evidence" and not read.is_error
    write = await MimirWriteTool(runtime).execute(
        {
            "path": "notes/finding.md",
            "content": "Updated evidence",
            "mimir": config["mimir"]["instances"][0]["name"],
        }
    )
    assert not write.is_error
    assert calls == ["query", "get_page", "put_page"]
    await runtime._mounts[0].port.close()


def test_gbrain_credential_reference_uses_injected_file():
    from volundr.adapters.outbound.contributors.ravn_flock import _resolve_mimir_runtime

    instances, _ = _resolve_mimir_runtime(
        {
            "registry_refs": [
                {
                    "mount_name": "brain",
                    "adapter": "ravn.adapters.mimir.gbrain.GBrainMimirAdapter",
                    "kwargs": {"mcp_url": "https://brain.test/mcp"},
                    "auth_ref": "brain-token",
                    "secret_kwargs_env": {"api_token": "HOST_ONLY_TOKEN"},
                }
            ]
        }
    )
    assert instances[0]["kwargs"]["api_token_file"] == "/run/secrets/mimir/brain-token/token"
    assert "api_token" not in instances[0]["secret_kwargs_env"]


def test_disabled_registry_well_fails_before_dispatch(tmp_path):
    path = tmp_path / "registry.json"
    entry = MimirRegistryStore(path).save_entry(MimirRegistryEntry(name="brain", enabled=False))
    with pytest.raises(ValueError, match="disabled"):
        _resolve_mimir_registry_refs(
            {"registry_refs": [{"registry_entry_id": entry.id}]}, registry_path=str(path)
        )
