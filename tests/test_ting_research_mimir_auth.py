from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from ting.api import research
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus


class _StaticAuthAdapter:
    def __init__(self, *, token: str) -> None:
        self._token = token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        volundr=SimpleNamespace(
            auth=SimpleNamespace(
                adapter="tests.test_ting_research_mimir_auth._StaticAuthAdapter",
                kwargs={"token": "service-token"},
                secret_kwargs_env={},
            )
        ),
        dispatch=SimpleNamespace(
            flock=SimpleNamespace(
                mimir_hosted_url="",
                mimir_registry_path="",
            )
        ),
    )


def _campaign() -> WorkflowCampaign:
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug="openviking-investigation",
        name="OpenViking investigation",
        owner_id="owner",
        workflow_id=uuid4(),
        workflow_version="1",
        workflow_name="Research Campaign",
        workflow_snapshot={
            "mimir": {
                "default_mounts": ["mimir-yggdrasil"],
                "registry_refs": [
                    {
                        "mount_name": "mimir-yggdrasil",
                        "url": "https://mimir.yggdrasil.niuu.world/api/v1",
                    }
                ],
                "ephemeral_locals": [],
                "bindings": [],
            }
        },
        session_id="session",
        session_name="session",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id=None,
        stage_state=[],
        metadata={},
        created_at=now,
        updated_at=now,
    )


def test_mimir_http_auth_uses_configured_outbound_bearer(monkeypatch) -> None:
    monkeypatch.setattr(research, "import_class", lambda _path: _StaticAuthAdapter)

    auth = research._mimir_http_auth(_settings())

    assert auth is not None
    assert auth.type == "bearer"
    assert auth.token == "service-token"


def test_campaign_mimir_http_adapter_receives_outbound_auth(monkeypatch) -> None:
    monkeypatch.setattr(research, "import_class", lambda _path: _StaticAuthAdapter)

    adapter = research._resolve_campaign_mimir_port(_campaign(), _settings())

    assert adapter is not None
    assert getattr(adapter, "_base_url") == "https://mimir.yggdrasil.niuu.world/api/v1"
    auth = getattr(adapter, "_auth")
    assert auth.type == "bearer"
    assert auth.token == "service-token"
