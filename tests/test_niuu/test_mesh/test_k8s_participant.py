"""Kubernetes discovery tells a Skuld session from a Ravn and names it, from its annotations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from niuu.adapters.discovery.k8s import K8sDiscoveryAdapter
from niuu.mesh.identity import MeshIdentity


def _identity() -> MeshIdentity:
    return MeshIdentity(
        peer_id="me",
        realm_id="workflow-run",
        persona="reviewer",
        capabilities=[],
        permission_mode="full_access",
        version="1",
    )


def _pod(peer_id: str, **annotations: str) -> MagicMock:
    pod = MagicMock()
    pod.metadata.annotations = {"ravn.niuu.world/peer-id": peer_id, **annotations}
    pod.status.pod_ip = "10.0.3.4"
    return pod


async def _scan(*pods: MagicMock) -> K8sDiscoveryAdapter:
    adapter = K8sDiscoveryAdapter(_identity(), nng_port=7480)
    api = MagicMock()
    api.list_pod_for_all_namespaces.return_value.items = list(pods)
    with patch("niuu.adapters.discovery.k8s.k8s_client") as client:
        client.CoreV1Api.return_value = api
        await adapter._do_scan()
    return adapter


async def test_a_session_pod_is_a_skuld_peer_named_after_its_session():
    adapter = await _scan(
        _pod(
            "coder-1",
            **{
                "ravn.niuu.world/participant-type": "skuld",
                "ravn.niuu.world/display-name": "wf-probe-coder",
            },
        )
    )

    peer = adapter.peers()["coder-1"]
    assert peer.participant_type == "skuld"
    assert peer.display_name == "wf-probe-coder"


async def test_a_pod_that_does_not_say_what_it_is_keeps_the_identity_default():
    adapter = await _scan(_pod("resident-1"))

    peer = adapter.peers()["resident-1"]
    assert peer.participant_type == MeshIdentity.participant_type
    assert peer.display_name == ""
