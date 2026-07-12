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


def test_create_contributors_auto_wires_persona_provider_once() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)

    contributors = _create_contributors(Settings(), persona_provider=provider)

    assert [contributor.name for contributor in contributors].count("persona") == 1
    names = [contributor.name for contributor in contributors]
    assert names.index("persona") < max(
        index for index, name in enumerate(names) if name == "prompt"
    )
