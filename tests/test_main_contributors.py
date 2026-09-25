from __future__ import annotations

from unittest.mock import AsyncMock

from volundr.config import SessionContributorConfig, Settings
from volundr.domain.ports import SessionPersonaProvider
from volundr.main import _create_contributors


def test_create_contributors_does_not_duplicate_ravn_flock() -> None:
    settings = Settings(
        session_contributors=[
            SessionContributorConfig(
                adapter="volundr.adapters.outbound.contributors.ravn_flock.RavnFlockContributor"
            )
        ]
    )

    contributors = _create_contributors(settings)
    ravn_flock_contributors = [
        contributor for contributor in contributors if contributor.name == "ravn_flock"
    ]

    assert len(ravn_flock_contributors) == 1


def test_create_contributors_does_not_duplicate_notification_channels() -> None:
    settings = Settings(
        session_contributors=[
            SessionContributorConfig(
                adapter=(
                    "volundr.adapters.outbound.contributors.notification_channels."
                    "NotificationChannelContributor"
                )
            )
        ]
    )

    contributors = _create_contributors(settings)
    notification_contributors = [
        contributor for contributor in contributors if contributor.name == "notification_channels"
    ]

    assert len(notification_contributors) == 1


def test_create_contributors_auto_wires_workload_config_once() -> None:
    settings = Settings(
        session_contributors=[
            SessionContributorConfig(
                adapter=(
                    "volundr.adapters.outbound.contributors.workload_config."
                    "WorkloadConfigContributor"
                )
            )
        ]
    )

    contributors = _create_contributors(settings)
    workload_contributors = [
        contributor for contributor in contributors if contributor.name == "workload_config"
    ]

    assert len(workload_contributors) == 1


def test_create_contributors_passes_ravn_flock_image_to_auto_wired_contributor() -> None:
    settings = Settings(ravn_flock_image="ghcr.io/niuulabs/skuld:dev-test")

    contributors = _create_contributors(settings)
    ravn_flock = next(
        contributor for contributor in contributors if contributor.name == "ravn_flock"
    )

    assert ravn_flock._ravn_image == "ghcr.io/niuulabs/skuld:dev-test"


def test_create_contributors_passes_ravn_flock_init_writer_image() -> None:
    settings = Settings(ravn_flock_init_writer_image="ghcr.io/niuulabs/skuld:writer")

    contributors = _create_contributors(settings)
    ravn_flock = next(
        contributor for contributor in contributors if contributor.name == "ravn_flock"
    )

    assert ravn_flock._init_writer_image == "ghcr.io/niuulabs/skuld:writer"


def test_create_contributors_passes_ravn_flock_llm_default() -> None:
    llm = {"model": "Qwen/Qwen3.8-27B", "max_tokens": 8192}
    settings = Settings(ravn_flock_llm_config=llm)

    contributors = _create_contributors(settings)
    ravn_flock = next(
        contributor for contributor in contributors if contributor.name == "ravn_flock"
    )

    assert ravn_flock._default_llm_config == llm


def test_create_contributors_leaves_ravn_flock_without_llm_default_by_default() -> None:
    contributors = _create_contributors(Settings())
    ravn_flock = next(
        contributor for contributor in contributors if contributor.name == "ravn_flock"
    )

    assert ravn_flock._default_llm_config == {}


def test_create_contributors_auto_wires_persona_provider_once() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)

    contributors = _create_contributors(Settings(), persona_provider=provider)

    assert [contributor.name for contributor in contributors].count("persona") == 1
    names = [contributor.name for contributor in contributors]
    assert names.index("persona") < max(
        index for index, name in enumerate(names) if name == "prompt"
    )


def test_create_contributors_auto_wires_integrations_with_a_registry() -> None:
    """Docker and host installs get the integrations contributor (Claude auth
    mode, MCP servers, the model gateway URL) without listing it in config."""
    from volundr.domain.services.integration_registry import IntegrationRegistry

    contributors = _create_contributors(Settings(), integration_registry=IntegrationRegistry([]))
    assert [c.name for c in contributors if c.name == "integrations"] == ["integrations"]
    assert not [c for c in _create_contributors(Settings()) if c.name == "integrations"]


def test_create_contributors_keeps_a_configured_integrations_contributor_single() -> None:
    from volundr.domain.services.integration_registry import IntegrationRegistry

    settings = Settings(
        session_contributors=[
            SessionContributorConfig(
                adapter="volundr.adapters.outbound.contributors.integrations.IntegrationContributor"
            )
        ]
    )
    contributors = _create_contributors(settings, integration_registry=IntegrationRegistry([]))
    assert len([c for c in contributors if c.name == "integrations"]) == 1
