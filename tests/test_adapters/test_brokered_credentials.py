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


def test_flux_delivers_the_codex_broker_to_every_container() -> None:
    """OpenShell merges this env into its single sandbox; the Flux path wrote
    broker.codexAuth into the HelmRelease and stopped there, so no container
    ever saw it. A flock pod is nine containers — six research personas each
    building the same Codex transport — and all of them opened the websocket
    unauthenticated: 401 on every turn, while the session read as healthy.
    """
    from volundr.adapters.outbound.flux import _inject_codex_auth_env

    values = {
        "broker": {
            "codexAuth": {
                "adapter": "skuld.codex_auth.VolundrCodexAuthProvider",
                "kwargs": {},
            }
        },
        "extraContainers": [
            {"name": "ravn-research-framer"},
            {"name": "ravn-research-explorer", "env": [{"name": "RAVN_PERSONA", "value": "x"}]},
        ],
    }

    _inject_codex_auth_env(values)

    main = {entry["name"] for entry in values["envVars"]}
    assert "SKULD__CODEX_AUTH__ADAPTER" in main

    for container in values["extraContainers"]:
        names = {entry["name"] for entry in container["env"]}
        assert "SKULD__CODEX_AUTH__ADAPTER" in names, container["name"]
    # Pre-existing sidecar env survives.
    assert any(e["name"] == "RAVN_PERSONA" for e in values["extraContainers"][1]["env"])


def test_flux_codex_injection_is_idempotent_and_quiet_when_unconfigured() -> None:
    from volundr.adapters.outbound.flux import _inject_codex_auth_env

    unconfigured: dict = {"extraContainers": [{"name": "ravn-x"}]}
    _inject_codex_auth_env(unconfigured)
    assert "envVars" not in unconfigured

    values = {
        "broker": {"codexAuth": {"adapter": "skuld.codex_auth.VolundrCodexAuthProvider"}},
        "extraContainers": [{"name": "ravn-x"}],
    }
    _inject_codex_auth_env(values)
    _inject_codex_auth_env(values)

    adapters = [e for e in values["envVars"] if e["name"] == "SKULD__CODEX_AUTH__ADAPTER"]
    assert len(adapters) == 1
