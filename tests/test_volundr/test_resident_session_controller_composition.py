"""Dynamic resident engine adapter composition tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from volundr.config import Settings
from volundr.domain.models import ResidentBackend, ResidentEngine
from volundr.domain.ports import ResidentOpenClawShimHost, ResidentSessionController
from volundr.main import _create_resident_session_controllers


class _SessionController(ResidentSessionController):
    def __init__(
        self,
        *,
        runtime_controller: object,
        credential_store: object,
        marker: str = "",
    ) -> None:
        self.runtime_controller = runtime_controller
        self.credential_store = credential_store
        self.marker = marker

    @property
    def engine(self) -> ResidentEngine:
        return ResidentEngine.HERMES

    async def list_sessions(self, runtime):
        return []

    async def create_session(self, runtime, *, title: str, model: str):
        raise NotImplementedError

    async def delete_session(self, runtime, session_id):
        raise NotImplementedError

    async def connect_chat(self, runtime, session_id):
        raise NotImplementedError


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "resident_runtimes": {
                "session_controllers": [
                    {
                        "adapter": "tests.fake.HermesController",
                        "runtime_backend": "openshell",
                        "kwargs": {"marker": "configured"},
                    }
                ]
            }
        }
    )


def test_resident_session_controller_receives_bound_backend_and_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_controller = SimpleNamespace(backend=ResidentBackend.OPENSHELL)
    credential_store = object()
    monkeypatch.setattr(
        "volundr.composition_builders.import_class",
        lambda _path: _SessionController,
    )

    controllers = _create_resident_session_controllers(
        _settings(),
        [runtime_controller],  # type: ignore[list-item]
        credential_store,
    )

    assert len(controllers) == 1
    controller = controllers[0]
    assert isinstance(controller, _SessionController)
    assert controller.runtime_controller is runtime_controller
    assert controller.credential_store is credential_store
    assert controller.marker == "configured"


def test_resident_session_controller_requires_its_runtime_backend() -> None:
    with pytest.raises(RuntimeError, match="requires unavailable backend openshell"):
        _create_resident_session_controllers(_settings(), [], object())


def test_default_openclaw_adapter_is_optional_without_openshell_backend() -> None:
    settings = Settings()

    assert settings.resident_runtimes.session_controllers[0].optional is True
    assert _create_resident_session_controllers(settings, [], object()) == []


class _ShimHost(ResidentOpenClawShimHost):
    """A resident backend that hosts the Ravn OpenClaw shim (Flux, OpenShell)."""

    def __init__(self, backend: ResidentBackend) -> None:
        self.backend = backend
        self.tokens = None

    def set_resident_openclaw_tokens(self, tokens) -> None:
        self.tokens = tokens

    async def resident_openclaw_token(self, runtime) -> str:
        return await self.tokens.ensure(runtime)

    def resident_openclaw_target(self, runtime, port):
        return None


def _ravn_settings(**resident_runtimes) -> Settings:
    return Settings.model_validate(
        {"resident_runtimes": {"session_controllers": [], **resident_runtimes}}
    )


def test_ravn_sessions_are_wired_on_every_shim_host_by_default() -> None:
    """No secret to configure: each resident's token is its own, in the credential store."""
    from volundr.adapters.outbound.ravn_gateway import (
        CredentialStoreOpenClawTokens,
        RavnResidentSessionController,
    )

    flux = _ShimHost(ResidentBackend.HELMRELEASE)
    openshell = _ShimHost(ResidentBackend.OPENSHELL)
    plain = SimpleNamespace(backend=ResidentBackend.LOCAL)

    controllers = _create_resident_session_controllers(
        _ravn_settings(),
        [flux, openshell, plain],  # type: ignore[list-item]
        MemoryCredentialStore(),
    )

    assert [type(c) for c in controllers] == [RavnResidentSessionController] * 2
    assert [c.runtime_backend for c in controllers] == [
        ResidentBackend.HELMRELEASE,
        ResidentBackend.OPENSHELL,
    ]
    assert all(c.engine is ResidentEngine.RAVN for c in controllers)
    assert isinstance(flux.tokens, CredentialStoreOpenClawTokens)
    assert isinstance(openshell.tokens, CredentialStoreOpenClawTokens)


def test_a_configured_ravn_controller_wins_on_its_backend(monkeypatch, caplog) -> None:
    """Valhalla's old explicit helmrelease entry: still valid, its master ignored."""
    monkeypatch.setenv("RESIDENT_SESSION_CONTROLLER_0_SK_GATEWAY_TOKEN", "valhalla-master")
    flux = _ShimHost(ResidentBackend.HELMRELEASE)
    openshell = _ShimHost(ResidentBackend.OPENSHELL)
    settings = _ravn_settings(
        session_controllers=[
            {
                "adapter": "volundr.adapters.outbound.ravn_gateway.RavnResidentSessionController",
                "runtime_backend": "helmrelease",
                "secret_kwargs_env": {
                    "gateway_token": "RESIDENT_SESSION_CONTROLLER_0_SK_GATEWAY_TOKEN"
                },
            }
        ],
    )

    with caplog.at_level("WARNING"):
        controllers = _create_resident_session_controllers(
            settings, [flux, openshell], MemoryCredentialStore()
        )

    assert [c.runtime_backend for c in controllers] == [
        ResidentBackend.HELMRELEASE,
        ResidentBackend.OPENSHELL,
    ]
    assert "ignores gateway_token" in caplog.text
    assert flux.tokens is not None


def test_default_ravn_sessions_can_be_switched_off() -> None:
    flux = _ShimHost(ResidentBackend.HELMRELEASE)
    settings = _ravn_settings(ravn_sessions_enabled=False)

    assert _create_resident_session_controllers(settings, [flux], MemoryCredentialStore()) == []
    assert flux.tokens is None


def test_no_master_setting_remains() -> None:
    assert (
        "ravn_gateway_token"
        not in Settings.model_fields["resident_runtimes"].annotation.model_fields
    )
