"""A workflow memory well survives registry resolution and session materialization."""

import json

import httpx
import pytest
import respx

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from mimir.builders import build_mimir
from mimir.client_settings import MimirClientSettings
from mimir.connections import resolve_mimir_registry_refs
from mimir.registry import MimirRegistryEntry, MimirRegistryStore


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

    from mimir.adapters.composite import CompositeMimirAdapter
    from mimir.adapters.gbrain import GBrainMimirAdapter
    from mimir.adapters.http import HttpMimirAdapter
    from mimir.adapters.markdown import MarkdownMimirAdapter
    from mimir.domain import MimirMount
    from mimir.router import MimirRouter
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
            ),
            auth=EnvoyHeaderAuthenticationAdapter(),
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
                headers={
                    "x-auth-user-id": "owner",
                    "x-auth-tenant": "default",
                    "x-auth-roles": "volundr:developer",
                },
            )
        return adapter._client

    monkeypatch.setattr(HttpMimirAdapter, "_get_client", gateway_client)
    connection = {
        "adapter": "mimir.adapters.http.HttpMimirAdapter",
        "kwargs": {"base_url": "https://knowledge.test/api/v1", "mount": "research-well"},
    }
    campaign = _campaign()
    campaign.workflow_snapshot["mimir"] = {
        "default_mounts": ["research-well"],
        "registry_refs": [{"mount_name": "research-well", **connection}],
    }
    runtime = build_mimir(
        MimirClientSettings.model_validate(
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
    settings.dispatch.mimir.hosted_url = "https://wrong-store.test/api/v1"
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
