"""Tests for the discovered resident backend (residents the Forge never deploys)."""

from __future__ import annotations

import json

import httpx
import pytest

from volundr.adapters.outbound.discovered_resident_runtime import (
    DiscoveredResidentRuntimeController,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentDeploymentProfile,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
)

_HILDR = {
    "key": "valkyrie-glitnir-k8s",
    "name": "Hildr",
    "owner_id": "user-a",
    "tenant_id": "tenant-a",
    "shim_url": "https://hildr.glitnir.example/openclaw",
    "chat_endpoint": "wss://ygg.example/rooms/hildr/session",
    "persona_name": "k8s-valkyrie",
}


def _controller(handler=None, **overrides) -> DiscoveredResidentRuntimeController:
    kwargs = {
        "residents": [_HILDR],
        "shim_tokens": json.dumps({"valkyrie-glitnir-k8s": "tok-hildr"}),
        "transport": httpx.MockTransport(handler or (lambda _: httpx.Response(200))),
    }
    kwargs.update(overrides)
    return DiscoveredResidentRuntimeController(**kwargs)


def _runtime(key: str = "valkyrie-glitnir-k8s") -> ResidentRuntime:
    return ResidentRuntime(
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Hildr",
        backend=ResidentBackend.DISCOVERED,
        engine=ResidentEngine.RAVN,
        profile_id="discovered-ravn",
        backend_ref={"key": key},
    )


def _profile(backend: ResidentBackend = ResidentBackend.DISCOVERED) -> ResidentDeploymentProfile:
    return ResidentDeploymentProfile(
        id="discovered-ravn",
        display_name="Discovered Ravn",
        backend=backend,
        engine=ResidentEngine.RAVN,
    )


async def test_lists_each_configured_resident_for_the_forge():
    controller = _controller()

    (hildr,) = await controller.discovered_residents()
    await controller.close()

    assert (hildr.key, hildr.name, hildr.owner_id, hildr.tenant_id) == (
        "valkyrie-glitnir-k8s",
        "Hildr",
        "user-a",
        "tenant-a",
    )
    assert hildr.profile_id == "discovered-ravn"
    assert hildr.persona_name == "k8s-valkyrie"
    assert controller.backend is ResidentBackend.DISCOVERED
    assert controller.supports(_profile())
    assert not controller.supports(_profile(ResidentBackend.OPENSHELL))


async def test_a_resident_whose_shim_answers_is_active_with_its_room():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "status": "live"})

    controller = _controller(handler)
    observation = await controller.reconcile(_runtime(), _profile())
    await controller.close()

    assert seen == ["https://hildr.glitnir.example/openclaw/health"]
    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert observation.backend_ref == {
        "key": "valkyrie-glitnir-k8s",
        "shim_url": "https://hildr.glitnir.example/openclaw",
    }
    assert [(e.kind, e.url) for e in observation.endpoints] == [
        ("chat", "wss://ygg.example/rooms/hildr/session")
    ]


async def test_a_resident_whose_shim_does_not_answer_is_failed_and_says_where():
    controller = _controller(lambda _: httpx.Response(502))
    observation = await controller.reconcile(_runtime(), _profile())
    await controller.close()

    assert observation.observed_state is ResidentObservedState.FAILED
    assert observation.conditions[0].reason == "ShimUnreachable"
    assert "hildr.glitnir.example/openclaw/health" in observation.conditions[0].message


async def test_a_record_no_longer_discovered_is_failed_until_it_is_removed():
    controller = _controller()
    observation = await controller.reconcile(_runtime("gone"), _profile())
    await controller.close()

    assert observation.observed_state is ResidentObservedState.FAILED
    assert observation.conditions[0].reason == "NotDiscovered"


async def test_routes_the_forge_to_the_shim_with_the_residents_own_token():
    controller = _controller()

    target = controller.resident_openclaw_target(_runtime(), 18790)
    token = await controller.resident_openclaw_token(_runtime())
    await controller.close()

    assert target is not None
    assert target.service_url == "https://hildr.glitnir.example/openclaw"
    assert (target.connect_host, target.connect_port, target.connect_secure) == (
        "hildr.glitnir.example",
        443,
        True,
    )
    assert token == "tok-hildr"
    assert controller.resident_openclaw_target(_runtime("gone"), 18790) is None


async def test_a_record_without_a_configured_token_fails_loudly():
    controller = _controller()

    with pytest.raises(RuntimeError, match="No OpenClaw shim token"):
        await controller.resident_openclaw_token(_runtime("gone"))
    await controller.close()


@pytest.mark.parametrize("verb", ["deploy", "restart", "resume"])
async def test_the_forge_never_manages_a_discovered_residents_lifecycle(verb):
    controller = _controller()

    with pytest.raises(RuntimeError, match="runs outside the Forge"):
        await getattr(controller, verb)(_runtime(), _profile())
    with pytest.raises(RuntimeError, match="runs outside the Forge"):
        await controller.suspend(_runtime())
    with pytest.raises(RuntimeError, match="runs outside the Forge"):
        await controller.delete(_runtime())
    await controller.close()


@pytest.mark.parametrize(
    ("residents", "tokens", "message"),
    [
        ([{**_HILDR, "shim_url": "http://hildr/openclaw"}], {"valkyrie-glitnir-k8s": "t"}, "https"),
        ([{**_HILDR, "owner_id": ""}], {"valkyrie-glitnir-k8s": "t"}, "needs owner_id"),
        ([_HILDR, _HILDR], {"valkyrie-glitnir-k8s": "t"}, "more than once"),
        (["not-an-object"], {}, "must be an object"),
        ([_HILDR], "", "No OpenClaw shim token"),
        ([_HILDR], {"valkyrie-glitnir-k8s": "t", "stray": "t"}, "not discovered"),
        ([_HILDR], json.dumps(["not", "a", "map"]), "JSON object"),
    ],
)
def test_a_misconfigured_resident_list_fails_at_startup(residents, tokens, message):
    with pytest.raises(ValueError, match=message):
        DiscoveredResidentRuntimeController(residents=residents, shim_tokens=tokens)


async def test_the_forge_token_store_never_replaces_the_configured_token():
    controller = _controller()
    store = object()

    controller.set_resident_openclaw_tokens(store)  # type: ignore[arg-type]
    await controller.close()

    assert await controller.resident_openclaw_token(_runtime()) == "tok-hildr"


def _loki(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/query_range"):
        return httpx.Response(
            200,
            json={
                "data": {
                    "result": [
                        {
                            "stream": {
                                "k8s_container_name": "ravn",
                                "k8s_pod_name": "valkyrie-glitnir-k8s-5bfc6d69cf-zlq7l",
                                "detected_level": "info",
                            },
                            "values": [["2000000000", "2026-10-28 ravn.x WARNING slow"]],
                        }
                    ]
                }
            },
        )
    return httpx.Response(200)


async def test_reads_a_placed_residents_logs_from_its_clusters_loki_tenant():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _loki(request)

    placed = {
        **_HILDR,
        "placement": {
            "cluster": "glitnir",
            "namespace": "nats",
            "workload": "valkyrie-glitnir-k8s",
        },
    }
    controller = _controller(handler, residents=[placed], loki_base_url="https://loki.example")

    page = await controller.logs(_runtime(), lines=5, sources=(), min_level="")
    await controller.close()

    assert seen == ["/glitnir/loki/api/v1/query_range"]
    assert page.buffer_total == 1
    assert (page.entries[0].level, page.entries[0].source) == ("warning", "ravn")
    assert page.entries[0].timestamp_ms == 2000


async def test_logs_say_what_is_missing_instead_of_answering_empty():
    from volundr.domain.ports import ResidentLogsUnavailableError

    no_loki = _controller()
    with pytest.raises(ResidentLogsUnavailableError, match="loki_base_url"):
        await no_loki.logs(_runtime(), lines=5, sources=(), min_level="")
    with pytest.raises(ResidentLogsUnavailableError, match="no longer discovered"):
        await no_loki.logs(_runtime("gone"), lines=5, sources=(), min_level="")
    await no_loki.close()

    unplaced = _controller(loki_base_url="https://loki.example")
    with pytest.raises(ResidentLogsUnavailableError, match="no placement"):
        await unplaced.logs(_runtime(), lines=5, sources=(), min_level="")
    await unplaced.close()


async def test_a_loki_failure_is_a_backend_error():
    placed = {
        **_HILDR,
        "placement": {
            "cluster": "glitnir",
            "namespace": "nats",
            "workload": "valkyrie-glitnir-k8s",
        },
    }
    controller = _controller(
        lambda _: httpx.Response(500), residents=[placed], loki_base_url="https://loki.example"
    )

    with pytest.raises(
        RuntimeError, match="could not return the logs of nats/valkyrie-glitnir-k8s"
    ):
        await controller.logs(_runtime(), lines=5, sources=(), min_level="")
    await controller.close()


def test_a_partial_placement_fails_at_startup():
    with pytest.raises(ValueError, match="placement needs cluster"):
        DiscoveredResidentRuntimeController(
            residents=[{**_HILDR, "placement": {"cluster": "glitnir"}}],
            shim_tokens={"valkyrie-glitnir-k8s": "t"},
        )
