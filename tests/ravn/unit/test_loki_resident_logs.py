"""Tests for reading resident logs from Loki by placement."""

from __future__ import annotations

import httpx
import pytest

from ravn.adapters.resident_logs.loki import LokiResidentLogAdapter
from ravn.ports.resident_discovery import ResidentPlacement
from ravn.ports.resident_logs import ResidentLogSourceError

_PLACEMENT = ResidentPlacement(cluster="glitnir", namespace="nats", workload="valkyrie-glitnir-k8s")


def _stream(container: str, detected: str, values: list[tuple[int, str]]) -> dict:
    return {
        "stream": {
            "k8s_container_name": container,
            "k8s_pod_name": "valkyrie-glitnir-k8s-5bfc6d69cf-zlq7l",
            "detected_level": detected,
        },
        "values": [[str(ts), line] for ts, line in values],
    }


def _adapter(handler, **kwargs) -> tuple[LokiResidentLogAdapter, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    adapter = LokiResidentLogAdapter(
        base_url="https://loki.test/", transport=httpx.MockTransport(record), **kwargs
    )
    return adapter, requests


def _result(*streams: dict) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": {"result": list(streams)}})


async def test_reads_the_workloads_pods_from_the_clusters_tenant_newest_lines_last():
    adapter, requests = _adapter(
        lambda _: _result(
            _stream(
                "ravn",
                "info",
                [
                    (3_000_000_000, "2026-10-28 18:15:03,000 ravn.x WARNING slow"),
                    (1_000_000_000, "2026-10-28 18:15:01,000 ravn.x INFO started"),
                ],
            ),
            _stream("tbot", "info", [(2_000_000_000, "Task succeeded")]),
        )
    )

    page = await adapter.read_logs(_PLACEMENT, lines=2, sources=(), min_level="")
    await adapter.aclose()

    request = requests[0]
    assert request.url.path == "/glitnir/loki/api/v1/query_range"
    assert request.url.params["query"] == (
        '{k8s_namespace_name="nats", k8s_pod_name=~"valkyrie-glitnir-k8s-[a-z0-9]+-[a-z0-9]+"}'
    )
    assert request.url.params["limit"] == "2"
    assert request.url.params["direction"] == "backward"
    assert page["bufferTotal"] == 2
    assert [entry["message"] for entry in page["entries"]] == [
        "Task succeeded",
        "2026-10-28 18:15:03,000 ravn.x WARNING slow",
    ]
    assert page["entries"][0] == {
        "timestampMs": 2000,
        "level": "info",
        "source": "tbot",
        "target": "tbot",
        "message": "Task succeeded",
        "fields": {"pod": "valkyrie-glitnir-k8s-5bfc6d69cf-zlq7l"},
    }
    assert page["entries"][1]["level"] == "warning"


async def test_sources_and_minimum_level_narrow_the_query():
    adapter, requests = _adapter(lambda _: _result())

    await adapter.read_logs(_PLACEMENT, lines=10, sources=("ravn", "tbot"), min_level="WARN")
    await adapter.aclose()

    query = requests[0].url.params["query"]
    assert 'k8s_container_name=~"ravn|tbot"' in query
    assert query.endswith('| detected_level=~"(?i)warning|error|critical|warn|fatal"')


async def test_a_line_without_a_level_word_takes_lokis_detected_level():
    adapter, _ = _adapter(lambda _: _result(_stream("ravn", "error", [(1, "boom")])))

    page = await adapter.read_logs(_PLACEMENT, lines=1, sources=(), min_level="")
    await adapter.aclose()

    assert page["entries"][0]["level"] == "error"


async def test_an_unknown_level_reads_as_info():
    adapter, _ = _adapter(lambda _: _result(_stream("ravn", "unknown", [(1, "plain line")])))

    page = await adapter.read_logs(_PLACEMENT, lines=1, sources=(), min_level="")
    await adapter.aclose()

    assert page["entries"][0]["level"] == "info"


async def test_an_unknown_minimum_level_is_refused():
    adapter, requests = _adapter(lambda _: _result())

    with pytest.raises(ValueError, match="min_level 'loud'"):
        await adapter.read_logs(_PLACEMENT, lines=1, sources=(), min_level="loud")
    await adapter.aclose()
    assert requests == []


async def test_a_source_that_is_not_a_container_name_is_refused():
    adapter, requests = _adapter(lambda _: _result())

    with pytest.raises(ValueError, match="not a Kubernetes name"):
        await adapter.read_logs(_PLACEMENT, lines=1, sources=('ravn"}',), min_level="")
    await adapter.aclose()
    assert requests == []


async def test_a_loki_failure_names_loki_and_the_resident():
    adapter, _ = _adapter(lambda _: httpx.Response(500, text="ingester down"))

    with pytest.raises(ResidentLogSourceError, match="nats/valkyrie-glitnir-k8s"):
        await adapter.read_logs(_PLACEMENT, lines=1, sources=(), min_level="")
    await adapter.aclose()


@pytest.mark.parametrize(
    "body",
    [{"status": "error"}, {"data": {"result": ["not-a-stream"]}}, ["not", "an", "object"]],
)
async def test_an_answer_without_streams_is_an_error_not_an_empty_log(body):
    adapter, _ = _adapter(lambda _: httpx.Response(200, json=body))

    with pytest.raises(ResidentLogSourceError, match="without a stream result"):
        await adapter.read_logs(_PLACEMENT, lines=1, sources=(), min_level="")
    await adapter.aclose()


def test_base_url_is_required():
    with pytest.raises(ValueError, match="needs base_url"):
        LokiResidentLogAdapter(base_url=" ")


def test_the_query_path_must_name_the_cluster():
    with pytest.raises(ValueError, match="must contain"):
        LokiResidentLogAdapter(base_url="https://loki.test", query_path="/loki/api/v1/query_range")


def test_a_placement_must_use_kubernetes_names():
    with pytest.raises(ValueError):
        ResidentPlacement(cluster="Glitnir", namespace="nats", workload="valkyrie")
