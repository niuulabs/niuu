"""Tests for shared Kubernetes pod-manager credential configuration."""

from __future__ import annotations

from volundr.adapters.outbound.brokered_credentials import BrokeredCredentialPodManager
from volundr.adapters.outbound.direct_k8s_pod_manager import DirectK8sPodManager
from volundr.adapters.outbound.flux import FluxPodManager
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.domain.models import PodSpecAdditions, SessionSpec


class _Manager(BrokeredCredentialPodManager):
    pass


def test_shared_mixin_projects_one_skuld_auth_contract() -> None:
    manager = _Manager()
    manager._configure_brokered_credentials(codex_auth_kwargs={"credential_name": "codex-default"})

    projected = manager._with_brokered_credentials(
        SessionSpec(
            values={"broker": {"codexAuth": {"kwargs": {"credential_field": "auth.json"}}}},
            pod_spec=PodSpecAdditions(),
        )
    )

    assert projected.values["broker"]["codexAuth"] == {
        "adapter": "skuld.codex_auth.VolundrCodexAuthProvider",
        "kwargs": {
            "credential_name": "codex-default",
            "credential_field": "auth.json",
        },
    }
    assert manager._brokered_credential_environment(projected) == {
        "SKULD__CODEX_AUTH__ADAPTER": "skuld.codex_auth.VolundrCodexAuthProvider",
        "SKULD__CODEX_AUTH__KWARGS": (
            '{"credential_name": "codex-default", "credential_field": "auth.json"}'
        ),
    }


def test_kubernetes_managers_share_mixin_while_local_process_keeps_host_auth() -> None:
    assert issubclass(FluxPodManager, BrokeredCredentialPodManager)
    assert issubclass(DirectK8sPodManager, BrokeredCredentialPodManager)
    assert not issubclass(LocalProcessPodManager, BrokeredCredentialPodManager)
