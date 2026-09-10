"""A workflow memory well survives registry resolution and session materialization."""

import json

import httpx
import pytest
import respx
import yaml

from mimir.connections import resolve_mimir_registry_refs
from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from ravn.adapters.tools.mimir_tools import MimirReadTool, MimirSearchTool, MimirWriteTool
from ravn.cli.runtime_builders import _build_mimir
from ravn.config import Settings
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
    payload = resolve_mimir_registry_refs(
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
        resolve_mimir_registry_refs(
            {"registry_refs": [{"registry_entry_id": entry.id}]}, registry_path=str(path)
        )


@pytest.mark.parametrize("backend", ["mimir", "gbrain"])
@respx.mock
async def test_research_reads_the_same_gateway_mount_as_the_runtime(backend, tmp_path, monkeypatch):
    """Write through the runtime, read through research, resolve only in Mímir."""

    from fastapi import FastAPI

    from mimir.adapters.markdown import MarkdownMimirAdapter
    from mimir.router import MimirRouter
    from ravn.adapters.mimir.composite import CompositeMimirAdapter
    from ravn.adapters.mimir.gbrain import GBrainMimirAdapter
    from ravn.adapters.mimir.http import HttpMimirAdapter
    from ravn.domain.mimir import MimirMount
    from tests.test_ting_research_mimir_auth import _campaign, _settings
    from ting.api.research import _campaign_artifact_summaries, _load_campaign_artifacts

    records = {}

    def brain_reply(request):
        params = json.loads(request.content)["params"]
        args = params["arguments"]
        if params["name"] == "put_page":
            records[args["slug"]] = {"slug": args["slug"], "content": args["content"]}
            result = {}
        elif params["name"] == "list_pages":
            # Native listings contain slugs, not Mímir filenames or page bodies.
            result = {"structuredContent": [{"slug": slug} for slug in records]}
        else:
            assert params["name"] == "get_page"
            result = {
                "structuredContent": [records[args["slug"]]] if args["slug"] in records else []
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    respx.post("https://brain.test/mcp").mock(side_effect=brain_reply)
    well = (
        GBrainMimirAdapter("https://brain.test/mcp", "test-token")
        if backend == "gbrain"
        else MarkdownMimirAdapter(root=tmp_path / "well")
    )
    gateway = FastAPI()
    gateway.include_router(
        MimirRouter(
            CompositeMimirAdapter(
                mounts=[
                    MimirMount(
                        name="shared",
                        role="shared",
                        port=MarkdownMimirAdapter(root=tmp_path / "empty"),
                    ),
                    MimirMount(name="research-well", role="shared", port=well),
                ]
            )
        ).router,
        prefix="/api/v1/mimir",
    )
    requests = []

    async def gateway_client(adapter):
        if adapter._client is None:

            async def observe(request):
                requests.append(request)
                assert request.headers["authorization"] == "Bearer caller-token"
                assert request.url.params["mount"] == "research-well"

            adapter._client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=gateway),
                base_url=adapter._base_url,
                event_hooks={"request": [observe]},
                headers={"x-auth-user-id": "owner", "x-auth-tenant": "tenant"},
            )
        return adapter._client

    monkeypatch.setattr(HttpMimirAdapter, "_get_client", gateway_client)
    connection = {
        "adapter": "ravn.adapters.mimir.http.HttpMimirAdapter",
        "kwargs": {"base_url": "https://knowledge.test/api/v1", "mount": "research-well"},
    }
    campaign = _campaign()
    campaign.workflow_snapshot["mimir"] = {
        "default_mounts": ["research-well"],
        "registry_refs": [{"mount_name": "research-well", **connection}],
    }
    runtime = _build_mimir(
        Settings.model_validate(
            {
                "mimir": {
                    "enabled": True,
                    "instances": [
                        {
                            "name": "research-well",
                            **connection,
                            "auth": {"type": "bearer", "token": "caller-token"},
                        }
                    ],
                    "write_routing": {"default": ["research-well"]},
                }
            }
        )
    )
    prefix = f"research/campaigns/{campaign.slug}/"
    bodies = {
        prefix + "final.md": "# Verified runbook\nEvidence exists.",
        prefix + "manifest.md": f"Published: {prefix}final.md",
        f"learnings/research/{campaign.slug}.md": "# Learning",
        f"followups/research/{campaign.slug}.md": "# Follow-up",
    }
    for path, body in bodies.items():
        await runtime.upsert_page(path, body)
    settings = _settings()
    # A different default store must never replace the selected mount.
    settings.dispatch.flock.mimir_hosted_url = "https://wrong-store.test/api/v1"
    artifacts, canonical = await _load_campaign_artifacts(
        campaign, settings=settings, bearer_token="caller-token"
    )
    assert {artifact.path for artifact in artifacts} == set(bodies)
    assert canonical["final"] == prefix + "final.md"
    assert next(a for a in artifacts if a.kind == "final").publish_state == "published"
    summaries = await _campaign_artifact_summaries(
        [campaign], settings=settings, bearer_token="caller-token"
    )
    assert summaries[0].artifact_count == 4
    assert summaries[0].published
    assert summaries[0].learning_count == summaries[0].follow_up_count == 1
    page = await runtime.get_page(canonical["final"])
    assert page.content == bodies[canonical["final"]]
    assert page.meta.path == canonical["final"]
    assert requests
    await runtime._mounts[0].port.aclose()
    if backend == "gbrain":
        await well.close()
