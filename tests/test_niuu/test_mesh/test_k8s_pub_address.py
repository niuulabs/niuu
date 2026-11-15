"""Kubernetes discovery carries each pod's nng PUB address, from an annotation or the pod IP."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from niuu.adapters.discovery.k8s import K8sDiscoveryAdapter
from niuu.mesh.identity import MeshCandidate, MeshIdentity


def _identity() -> MeshIdentity:
    return MeshIdentity(
        peer_id="me",
        realm_id="proof",
        persona="coder",
        capabilities=[],
        permission_mode="full_access",
        version="1",
    )


def _pod(peer_id: str, ip: str, **annotations: str) -> MagicMock:
    pod = MagicMock()
    pod.metadata.annotations = {"ravn.niuu.world/peer-id": peer_id, **annotations}
    pod.status.pod_ip = ip
    return pod


def _candidates(adapter: K8sDiscoveryAdapter, *pods: MagicMock) -> list[MeshCandidate]:
    api = MagicMock()
    api.list_pod_for_all_namespaces.return_value.items = list(pods)
    with patch("niuu.adapters.discovery.k8s.k8s_client") as client:
        client.CoreV1Api.return_value = api
        return adapter._list_candidates()


def test_a_pod_is_dialed_at_its_ip_and_the_configured_nng_port():
    adapter = K8sDiscoveryAdapter(_identity(), nng_port=7480)
    [candidate] = _candidates(adapter, _pod("peer-1", "10.0.3.4"))
    assert candidate.pub_address == "tcp://10.0.3.4:7480"


def test_an_annotation_names_the_address_when_the_deployer_knows_better():
    adapter = K8sDiscoveryAdapter(_identity(), nng_port=7480)
    [candidate] = _candidates(
        adapter,
        _pod("peer-1", "10.0.3.4", **{"ravn.niuu.world/pub-address": "tcp://gw.example:9000"}),
    )
    assert candidate.pub_address == "tcp://gw.example:9000"


def test_a_pod_with_no_ip_yet_or_no_port_has_no_address():
    assert (
        _candidates(K8sDiscoveryAdapter(_identity(), nng_port=7480), _pod("p", ""))[0].pub_address
        is None
    )
    assert (
        _candidates(K8sDiscoveryAdapter(_identity()), _pod("p", "10.0.3.4"))[0].pub_address is None
    )


async def test_a_restarted_pod_keeps_its_peer_id_and_gets_its_new_address():
    adapter = K8sDiscoveryAdapter(_identity(), nng_port=7480)
    first = _candidates(adapter, _pod("peer-1", "10.0.3.4"))
    with patch.object(adapter, "_list_candidates", return_value=first):
        await adapter._do_scan()
    assert adapter.peers()["peer-1"].pub_address == "tcp://10.0.3.4:7480"

    second = _candidates(adapter, _pod("peer-1", "10.0.9.9"))
    with patch.object(adapter, "_list_candidates", return_value=second):
        await adapter._do_scan()
    assert adapter.peers()["peer-1"].pub_address == "tcp://10.0.9.9:7480"
