"""How k8s_job reads resident-authored reach into enforceable egress."""

from __future__ import annotations

import pytest

from ravn.valkyrie_evolution.learned_tools import LearnedToolError
from ravn.valkyrie_evolution.models import ToolReachGrant
from ravn.valkyrie_evolution.reach_plan import (
    EgressPlan,
    HostTarget,
    ServiceTarget,
    parse_service_ref,
    plan_egress,
)

PROMETHEUS = "monitoring/kube-prom-kube-prometheus-prometheus:9090"


def _plan(*grants: ToolReachGrant, **kwargs) -> EgressPlan:
    return plan_egress(list(grants), **kwargs)


@pytest.mark.parametrize(
    ("kind", "target"),
    [
        # Declarations taken from the residents' installed tools.
        ("kubernetes", "pods,pods/log,events"),
        ("kubernetes", "pods/log"),
        ("k8s", "in-cluster Kubernetes API pod log endpoint (read-only)"),
        ("kubernetes-api", "in-cluster API"),
        ("kubernetes:read", ""),
        ("kubernetes api in-cluster read via service account (stdlib urllib)", ""),
        ("http", "kubernetes.default.svc:443"),
        ("http", "kubernetes-apiserver"),
        ("network", "in-cluster Kubernetes API kubernetes.default.svc:443, pods/log subresource"),
        ("https", "kubernetes api server (in-cluster)"),
        ("network", "https://kubernetes.default.svc"),
        (
            "http",
            "kubernetes API server (https://kubernetes.default.svc:443, "
            "/apis/longhorn.io/v1beta2 GET endpoints)",
        ),
        ("file", "/var/run/secrets/kubernetes.io/serviceaccount/token"),
        ("fs", "/var/run/secrets/kubernetes.io/serviceaccount"),
    ],
)
def test_kubernetes_api_reach_in_residents_own_words(kind: str, target: str) -> None:
    assert _plan(ToolReachGrant(kind=kind, target=target, access="read")) == EgressPlan(
        kubernetes_api=True
    )


def test_service_references_become_service_targets() -> None:
    plan = _plan(
        ToolReachGrant(kind="network", target=PROMETHEUS, access="read"),
        ToolReachGrant(
            kind="http", target="http://kube-prom-kube-prometheus-prometheus.monitoring.svc:9090"
        ),
        ToolReachGrant(kind="http", target="http://api.ns.svc.cluster.local"),
    )

    assert plan.services == (
        ServiceTarget("monitoring", "kube-prom-kube-prometheus-prometheus", 9090),
        ServiceTarget("ns", "api", 80),
    )
    assert plan.scoped and plan.network and not plan.unrestricted


def test_addresses_become_host_targets() -> None:
    plan = _plan(
        ToolReachGrant(kind="http", target="https://yggdrasil.niuu.world"),
        ToolReachGrant(kind="network", target="10.40.161.58:3030,10.40.161.2:3030"),
    )

    assert plan.hosts == (
        HostTarget("yggdrasil.niuu.world", 443),
        HostTarget("10.40.161.58", 3030),
        HostTarget("10.40.161.2", 3030),
    )


def test_operator_named_targets_resolve_prose() -> None:
    grant = ToolReachGrant(
        kind="http", target="in-cluster Prometheus/Thanos service /api/v1/query_range"
    )

    with pytest.raises(LearnedToolError, match="named_targets"):
        _plan(grant)
    assert _plan(grant, named_targets={"prometheus": PROMETHEUS}).services == (
        parse_service_ref(PROMETHEUS),
    )
    # Whole words only: "prometheuses" names nothing.
    with pytest.raises(LearnedToolError, match="cannot derive"):
        _plan(
            ToolReachGrant(kind="http", target="all prometheuses"),
            named_targets={"prometheus": PROMETHEUS},
        )


def test_local_and_declined_grants_need_no_egress() -> None:
    plan = _plan(
        ToolReachGrant(kind="pure_compute"),
        ToolReachGrant(kind="process", target="kubectl (optional)"),
        ToolReachGrant(kind="network", target="anything", access="none"),
    )

    assert plan == EgressPlan()
    assert not plan.network


def test_untargeted_read_write_network_is_unrestricted() -> None:
    plan = _plan(ToolReachGrant(kind="network", access="read_write"))

    assert plan == EgressPlan(unrestricted=True)
    assert plan.network and not plan.scoped


@pytest.mark.parametrize(
    ("grant", "message"),
    [
        (ToolReachGrant(kind="http", access="read"), "names no target"),
        (ToolReachGrant(kind="http", target="*:10255"), "wildcard"),
        (ToolReachGrant(kind="http", target="https://*.example.com"), "wildcard"),
        (ToolReachGrant(kind="filesystem", target="~/.kube/config"), "service-account token"),
        (
            ToolReachGrant(
                kind="file", target="/var/run/secrets/kubernetes.io/serviceaccount", access="write"
            ),
            "read-only",
        ),
        (ToolReachGrant(kind="credential", target="TOKEN"), "passes no credentials"),
        (ToolReachGrant(kind="native_tool", target="kubernetes_inspect"), "no channel back"),
        (ToolReachGrant(kind="kubernetes", access="read_write"), "read-only Kubernetes identity"),
        (ToolReachGrant(kind="http", target="ftp://example.com"), "names no port"),
        (ToolReachGrant(kind="http", target="intranet:8080"), "neither an IP"),
        (ToolReachGrant(kind="http", target="https://example.com:99999"), "invalid port"),
    ],
)
def test_unenforceable_reach_is_refused_with_the_remedy(
    grant: ToolReachGrant, message: str
) -> None:
    with pytest.raises(LearnedToolError, match=message):
        _plan(grant)


def test_service_ref_parsing() -> None:
    assert parse_service_ref("ns/svc") == ServiceTarget("ns", "svc", None)
    with pytest.raises(ValueError, match="namespace/service"):
        parse_service_ref("not a service")
