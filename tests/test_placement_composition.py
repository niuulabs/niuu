"""The composition root builds nested pod managers from their adapter configs."""

from __future__ import annotations

from uuid import uuid4

import pytest

from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.adapters.outbound.placement_pod_manager import PlacementPodManager
from volundr.adapters.outbound.vm_pod_manager import VmPodManager
from volundr.composition_builders import _create_pod_manager
from volundr.config import PodManagerConfig, Settings
from volundr.domain.models import Session

PLACEMENT = "volundr.adapters.outbound.placement_pod_manager.PlacementPodManager"
LOCAL = "volundr.adapters.outbound.local_process.LocalProcessPodManager"
VM = "volundr.adapters.outbound.vm_pod_manager.VmPodManager"
POOL = {"adapter": VM, "kwargs": {"profile": "host", "pool_id": "hosts", "max_machines": 2}}


def _settings(kwargs: dict, **settings) -> Settings:
    return Settings(
        pod_manager=PodManagerConfig(adapter=PLACEMENT, kwargs=kwargs),
        server_public_host="192.168.1.10",
        server_port=8181,
        **settings,
    )


def test_nested_pod_managers_get_the_forge_defaults_and_their_own_kwargs(tmp_path):
    preview_auth = {"adapter": "skuld.preview_adapters.NoRouterAuth", "kwargs": {}}
    pod_manager = _create_pod_manager(
        _settings(
            {
                "local": {
                    "adapter": LOCAL,
                    "kwargs": {
                        "workspaces_dir": str(tmp_path),
                        "max_concurrent": 3,
                        "preview_router_auth": preview_auth,
                    },
                },
                "pool": POOL,
            }
        )
    )

    assert isinstance(pod_manager, PlacementPodManager)
    local, pool = pod_manager._local, pod_manager._pool
    assert isinstance(local, LocalProcessPodManager) and isinstance(pool, VmPodManager)
    assert local._max_concurrent == 3
    assert local._server_public_host == "192.168.1.10" and local._server_port == 8181
    # An adapter-shaped kwarg the constructor does not type as a PodManager is data.
    assert local._preview_router_auth == preview_auth
    assert pool.initial_chat_endpoint(_any_session()).startswith("ws://192.168.1.10:8181/s/")


def test_mini_mode_placement_builds_with_mesh_signing_off(tmp_path):
    """The off switch must not stop the placement pod manager mini mode runs on.

    The wrapper has no mesh setting of its own; each manager it wraps was given the
    decision, so the wrapper is covered. Its local sessions then run unauthenticated.
    """
    settings = _settings(
        {"local": {"adapter": LOCAL, "kwargs": {"workspaces_dir": str(tmp_path)}}, "pool": POOL}
    )
    settings.mesh.attestation.required = False

    pod_manager = _create_pod_manager(settings)

    assert isinstance(pod_manager, PlacementPodManager)
    assert pod_manager._local._mesh == {"attestation": {"required": False}}


def test_a_nested_pod_manager_resolves_its_own_secret_kwargs(monkeypatch, tmp_path):
    monkeypatch.setenv("HOSTS_POOL_ID", "from-env")
    pod_manager = _create_pod_manager(
        _settings(
            {
                "local": {"adapter": LOCAL, "kwargs": {"workspaces_dir": str(tmp_path)}},
                "pool": {**POOL, "secret_kwargs_env": {"pool_id": "HOSTS_POOL_ID"}},
            }
        )
    )

    assert pod_manager._pool._pool_id == "from-env"


def test_a_nested_config_with_stray_keys_is_refused(tmp_path):
    with pytest.raises(ValueError, match=r"pod_manager.kwargs.pool takes adapter"):
        _create_pod_manager(
            _settings(
                {
                    "local": {"adapter": LOCAL, "kwargs": {"workspaces_dir": str(tmp_path)}},
                    "pool": {**POOL, "profile": "host"},
                }
            )
        )


def _any_session() -> Session:
    return Session(id=uuid4(), name="s", owner_id="o", tenant_id="t")
