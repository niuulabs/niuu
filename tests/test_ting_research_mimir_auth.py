from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from ting.api import research
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        dispatch=SimpleNamespace(
            flock=SimpleNamespace(mimir_hosted_url="", mimir_registry_path=""),
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
                        "path": "/data/mimir-yggdrasil",
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


def test_campaign_connection_uses_caller_auth() -> None:
    adapter = research._campaign_knowledge(_campaign(), _settings(), bearer_token="caller-token")
    assert adapter is not None
    assert getattr(adapter, "_base_url") == "https://mimir.yggdrasil.niuu.world/api/v1"
    assert getattr(adapter, "_auth").token == "caller-token"


def test_campaign_without_caller_does_not_borrow_volundr_credentials() -> None:
    settings = _settings()
    settings.volundr = SimpleNamespace(auth=object())
    adapter = research._campaign_knowledge(_campaign(), settings)
    assert getattr(adapter, "_auth") is None


def test_gateway_mounts_are_not_batched_together() -> None:
    from ravn.adapters.mimir.http import HttpMimirAdapter

    first = HttpMimirAdapter(base_url="https://knowledge.test/api/v1", mount="first")
    second = HttpMimirAdapter(base_url="https://knowledge.test/api/v1", mount="second")
    same = HttpMimirAdapter(base_url="https://knowledge.test/api/v1", mount="first")
    assert not research._same_mimir_mount(first, second)
    assert research._same_mimir_mount(first, same)


def test_unresolved_well_does_not_read_the_hosted_default() -> None:
    import pytest

    settings = _settings()
    settings.dispatch.flock.mimir_hosted_url = "https://wrong-store.test/api/v1"
    campaign = _campaign()
    campaign.workflow_snapshot["mimir"]["registry_refs"] = [{"mount_name": "mimir-yggdrasil"}]
    with pytest.raises(ValueError, match="no adapter, path or url"):
        research._campaign_knowledge(campaign, settings)
