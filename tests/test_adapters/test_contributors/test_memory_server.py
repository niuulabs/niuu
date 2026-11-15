"""MemoryServerContributor and memory.gateway_url: every session gets Skuld's memory server."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from volundr.adapters.outbound.contributors.memory_server import (
    MEMORY_ENABLED_ENV,
    MEMORY_GATEWAY_URL_ENV,
    MemoryServerContributor,
)
from volundr.adapters.outbound.contributors.session_mcp import MimirMountConfigError
from volundr.adapters.outbound.flux import FluxPodManager
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.composition_builders import require_memory_credentials
from volundr.config import MemoryConfig, Settings
from volundr.domain.models import GitSource, Session, SessionSpec
from volundr.domain.ports import SessionContext
from volundr.main import _create_contributors

URL = "http://127.0.0.1:8080/api/v1/mimir"
ENV = [
    {"name": MEMORY_ENABLED_ENV, "value": "true"},
    {"name": MEMORY_GATEWAY_URL_ENV, "value": URL},
]


async def test_every_session_gets_the_memory_server_settings() -> None:
    contributor = MemoryServerContributor(gateway_url=f" {URL} ")

    contribution = await contributor.contribute(Session(name="s"), SessionContext())

    assert list(contribution.pod_spec.env) == ENV
    assert contribution.values == {}


def test_the_skuld_settings_are_named_as_skuld_reads_them() -> None:
    assert MEMORY_ENABLED_ENV == "SKULD__MEMORY__ENABLED"
    assert MEMORY_GATEWAY_URL_ENV == "SKULD__MEMORY__GATEWAY_URL"


@pytest.mark.parametrize("url", ["", "   "])
def test_a_contributor_without_a_gateway_names_the_fix(url: str) -> None:
    with pytest.raises(ValueError, match="memory.gateway_url"):
        MemoryServerContributor(gateway_url=url)


class TestConfig:
    def test_memory_is_off_by_default(self) -> None:
        assert Settings().memory.gateway_url == ""
        assert Settings().memory.gateway_service_subject == ""

    def test_the_service_subject_is_trimmed(self) -> None:
        subject = "system:serviceaccount:niuu:mimir"
        assert (
            MemoryConfig(gateway_service_subject=f" {subject} ").gateway_service_subject == subject
        )

    def test_a_trailing_slash_is_dropped_and_whitespace_trimmed(self) -> None:
        assert MemoryConfig(gateway_url=f"  {URL}/ ").gateway_url == URL

    @pytest.mark.parametrize("url", ["mimir:8080/api", "ftp://x", "/api/v1/mimir"])
    def test_a_gateway_that_is_not_an_http_url_is_refused(self, url: str) -> None:
        with pytest.raises(ValidationError, match="memory.gateway_url must be an http"):
            MemoryConfig(gateway_url=url)


class TestWiring:
    async def test_a_configured_gateway_reaches_the_session_mcp_contributor(self) -> None:
        mimir = {
            "registry_refs": [{"mount_name": "a", "registry_entry_id": "e", "url": "https://a"}],
            "bindings": [{"mount_name": "a", "access": "read", "enforce": True}],
        }
        context = SessionContext(workload_config={"mimir": mimir})

        def session_mcp(settings: Settings):
            return next(c for c in _create_contributors(settings) if c.name == "session_mcp")

        with_gateway = await session_mcp(Settings(memory={"gateway_url": URL})).contribute(
            Session(name="s"), context
        )
        assert with_gateway.values == {}
        with pytest.raises(MimirMountConfigError, match="limits writes"):
            await session_mcp(Settings()).contribute(Session(name="s"), context)

    def test_a_configured_gateway_wires_the_contributor_once(self) -> None:
        contributors = _create_contributors(Settings(memory={"gateway_url": URL}))

        assert [c.name for c in contributors].count("memory_server") == 1

    def test_without_a_gateway_no_session_gets_a_memory_server(self) -> None:
        assert "memory_server" not in [c.name for c in _create_contributors(Settings())]


class TestStartupChecks:
    def test_no_gateway_needs_no_credentials(self) -> None:
        settings = Settings(forge_mcp={"session_tokens": {"enabled": False}})
        require_memory_credentials(settings, delivers_session_credential=False)

    def test_a_gateway_with_delivered_credentials_starts(self) -> None:
        require_memory_credentials(
            Settings(memory={"gateway_url": URL}), delivers_session_credential=True
        )

    def test_a_gateway_with_session_credentials_off_is_fatal_and_names_the_fix(self) -> None:
        settings = Settings(
            memory={"gateway_url": URL}, forge_mcp={"session_tokens": {"enabled": False}}
        )
        with pytest.raises(RuntimeError, match="forge_mcp.session_tokens"):
            require_memory_credentials(settings, delivers_session_credential=True)

    def test_a_gateway_with_a_verifying_identity_needs_the_service_subject(self) -> None:
        settings = Settings(
            memory={"gateway_url": URL},
            identity={"adapter": "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"},
        )
        with pytest.raises(RuntimeError, match="memory.gateway_service_subject"):
            require_memory_credentials(settings, delivers_session_credential=True)

    def test_a_verifying_identity_with_the_service_subject_starts(self) -> None:
        settings = Settings(
            memory={"gateway_url": URL, "gateway_service_subject": "system:serviceaccount:n:m"},
            identity={"adapter": "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"},
        )
        require_memory_credentials(settings, delivers_session_credential=True)

    def test_no_gateway_needs_no_service_subject_whatever_the_identity(self) -> None:
        settings = Settings(
            identity={"adapter": "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"}
        )
        require_memory_credentials(settings, delivers_session_credential=False)

    def test_a_gateway_no_broker_can_authenticate_to_is_fatal_and_names_the_fix(self) -> None:
        with pytest.raises(RuntimeError, match="projection_adapter"):
            require_memory_credentials(
                Settings(memory={"gateway_url": URL}), delivers_session_credential=False
            )


class TestDelivery:
    """The pod-spec env reaches Skuld through each runtime."""

    async def _spec(self) -> SessionSpec:
        contribution = await MemoryServerContributor(gateway_url=URL).contribute(
            Session(name="s"), SessionContext()
        )
        return SessionSpec.merge([contribution])

    async def test_a_flux_session_gets_it_as_chart_env(self) -> None:
        manager = FluxPodManager(namespace="ns", base_domain="volundr.example.com")

        values = manager._release_values(await self._spec())

        assert values["envVars"] == ENV

    async def test_a_local_process_session_gets_it_in_its_broker_env(self, tmp_path: Path) -> None:
        manager = LocalProcessPodManager(
            workspaces_dir=str(tmp_path / "workspaces"),
            claude_binary="/usr/bin/fake-claude",
            state_file=str(tmp_path / "state.json"),
        )
        session = Session(id=uuid4(), name="s", source=GitSource(repo="https://x/y", branch="b"))
        workspace = tmp_path / "workspaces" / str(session.id)
        workspace.mkdir(parents=True)
        process = MagicMock()
        process.pid = 42

        with (
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process
            ) as spawned,
        ):
            await manager._spawn_skuld(session, await self._spec(), workspace, 9100)

        env = spawned.call_args.kwargs["env"]
        assert env[MEMORY_ENABLED_ENV] == "true"
        assert env[MEMORY_GATEWAY_URL_ENV] == URL
