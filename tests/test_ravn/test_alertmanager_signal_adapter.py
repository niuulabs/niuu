"""Tests for the Alertmanager signal adapter."""

from __future__ import annotations

import json

import httpx
import pytest

from ravn.adapters.environment_signals import AlertmanagerSignalAdapter
from ravn.domain.environment import k8s_environment_fixture


def _alert(
    *,
    alertname: str = "VanaheimVMPausedIOError",
    severity: str = "critical",
    fingerprint: str = "abc123",
    starts_at: str = "2026-09-09T07:09:33.123456789Z",
) -> dict:
    return {
        "labels": {
            "alertname": alertname,
            "severity": severity,
            "namespace": "asgard",
            "name": "saga",
            "node": "njord",
        },
        "annotations": {
            "summary": "VM asgard/saga paused on a disk I/O error",
            "description": "KubeVirt paused asgard/saga on njord.",
        },
        "startsAt": starts_at,
        "endsAt": "0001-01-01T00:00:00.000Z",
        "fingerprint": fingerprint,
        "status": {"state": "active", "silencedBy": [], "inhibitedBy": []},
        "generatorURL": "http://prometheus/graph?g0.expr=up",
    }


async def test_alertmanager_adapter_normalizes_a_firing_alert() -> None:
    environment = k8s_environment_fixture()
    adapter = AlertmanagerSignalAdapter(environment=environment, raw_items=[_alert()])

    signals = await adapter.collect()

    assert len(signals) == 1
    signal = signals[0]
    assert signal.source_id == "alertmanager"
    assert signal.signal_type == "metrics"
    assert signal.severity == "critical"
    assert signal.provider == "alertmanager"
    assert signal.timestamp.isoformat() == "2026-09-09T07:09:33.123456+00:00"
    assert signal.dedupe_key == "alertmanager:abc123@2026-09-09T07:09:33.123456+00:00"
    assert signal.provider_event_id == "abc123@2026-09-09T07:09:33.123456+00:00"
    assert signal.correlation_id == f"{environment.id}:{signal.dedupe_key}"
    assert signal.raw_payload_ref == "alertmanager://alertmanager/abc123"
    assert signal.normalized_payload["alertname"] == "VanaheimVMPausedIOError"
    assert signal.normalized_payload["summary"] == "VM asgard/saga paused on a disk I/O error"
    assert signal.normalized_payload["state"] == "active"
    assert signal.object_ref == {
        "kind": "Alert",
        "name": "VanaheimVMPausedIOError",
        "namespace": "asgard",
        "subject": "saga",
        "fingerprint": "abc123",
    }
    assert signal.provenance == {"adapter": "alertmanager.alerts", "source_id": "alertmanager"}


async def test_alertmanager_adapter_only_trusts_warning_and_critical_labels() -> None:
    environment = k8s_environment_fixture()
    adapter = AlertmanagerSignalAdapter(
        environment=environment,
        raw_items=[
            _alert(severity="warning", fingerprint="w"),
            _alert(severity="page", fingerprint="p"),
            {"labels": {"alertname": "NoSeverity"}, "startsAt": "2026-09-09T07:00:00Z"},
        ],
    )

    signals = await adapter.collect()

    assert [signal.severity for signal in signals] == ["warning", "info", "info"]
    # A missing fingerprint still yields a stable identity from the labels.
    assert signals[2].object_ref["fingerprint"]
    assert signals[2].dedupe_key.startswith("alertmanager:")


async def test_alertmanager_adapter_keys_each_firing_episode_separately() -> None:
    environment = k8s_environment_fixture()
    adapter = AlertmanagerSignalAdapter(
        environment=environment,
        raw_items=[
            _alert(starts_at="2026-09-09T07:09:33Z"),
            _alert(starts_at="2026-09-09T09:54:22Z"),
        ],
    )

    signals = await adapter.collect()

    assert len({signal.dedupe_key for signal in signals}) == 2


async def test_alertmanager_adapter_can_exclude_alertnames() -> None:
    environment = k8s_environment_fixture()
    adapter = AlertmanagerSignalAdapter(
        environment=environment,
        raw_items=[_alert(), _alert(alertname="Watchdog", fingerprint="wd")],
        exclude_alertnames=["watchdog"],
    )

    signals = await adapter.collect()

    assert [signal.normalized_payload["alertname"] for signal in signals] == [
        "VanaheimVMPausedIOError"
    ]


def test_alertmanager_adapter_requires_a_url_without_injected_items() -> None:
    with pytest.raises(ValueError, match="url is required"):
        AlertmanagerSignalAdapter(environment=k8s_environment_fixture())


async def test_alertmanager_adapter_polls_the_v2_api_with_filters() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = request.url.params.multi_items()
        return httpx.Response(200, json=[_alert()])

    adapter = AlertmanagerSignalAdapter(
        environment=k8s_environment_fixture(),
        url="http://alertmanager:9093/",
        label_filters=['cluster="vanaheim"'],
        transport=httpx.MockTransport(handler),
    )

    signals = await adapter.collect()

    assert seen["path"] == "/api/v2/alerts"
    assert seen["params"] == [
        ("active", "true"),
        ("silenced", "false"),
        ("inhibited", "false"),
        ("filter", 'cluster="vanaheim"'),
    ]
    assert [signal.normalized_payload["alertname"] for signal in signals] == [
        "VanaheimVMPausedIOError"
    ]


async def test_alertmanager_adapter_fails_hard_on_http_errors() -> None:
    adapter = AlertmanagerSignalAdapter(
        environment=k8s_environment_fixture(),
        url="http://alertmanager:9093",
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="down")),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await adapter.collect()


async def test_alertmanager_adapter_rejects_non_list_payloads() -> None:
    adapter = AlertmanagerSignalAdapter(
        environment=k8s_environment_fixture(),
        url="http://alertmanager:9093",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps({"alerts": []}))
        ),
    )

    with pytest.raises(ValueError, match="non-list"):
        await adapter.collect()


async def test_alertmanager_adapter_groups_instances_of_the_same_alert() -> None:
    adapter = AlertmanagerSignalAdapter(
        environment=k8s_environment_fixture(),
        raw_items=[
            _alert(
                alertname="VolumeDegraded",
                severity="warning",
                fingerprint="v1",
                starts_at="2026-09-09T10:00:00Z",
            ),
            _alert(
                alertname="VolumeDegraded",
                severity="warning",
                fingerprint="v2",
                starts_at="2026-09-09T09:00:00Z",
            ),
            _alert(alertname="BMCDown", severity="critical", fingerprint="b1"),
        ],
        group_by_alertname=True,
    )

    signals = await adapter.collect()

    by_name = {signal.normalized_payload["alertname"]: signal for signal in signals}
    assert set(by_name) == {"VolumeDegraded", "BMCDown"}
    group = by_name["VolumeDegraded"]
    assert group.normalized_payload["count"] == 2
    assert group.timestamp.isoformat() == "2026-09-09T09:00:00+00:00"
    assert group.dedupe_key == "alertmanager:group:VolumeDegraded@2026-09-09T09:00:00+00:00"
    assert group.object_ref["kind"] == "AlertGroup"
    assert group.object_ref["fingerprints"] == ["v2", "v1"]
    assert "2 instances firing" in group.normalized_payload["summary"]
    # A lone alert keeps its per-instance identity.
    assert by_name["BMCDown"].object_ref["kind"] == "Alert"
    assert by_name["BMCDown"].dedupe_key.startswith("alertmanager:b1@")
