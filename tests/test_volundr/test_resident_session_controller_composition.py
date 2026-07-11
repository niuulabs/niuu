"""Dynamic resident engine adapter composition tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from volundr.config import Settings
from volundr.domain.models import ResidentBackend, ResidentEngine
from volundr.domain.ports import ResidentSessionController
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
    monkeypatch.setattr("volundr.main.import_class", lambda _path: _SessionController)

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
