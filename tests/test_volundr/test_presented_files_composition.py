"""Composing durable presented files and the session credential they upload with."""

from __future__ import annotations

import pytest

from volundr.adapters.outbound.local_presented_files import LocalPresentedFileStore
from volundr.composition_builders import (
    _create_forge_session_projection,
    create_presented_file_store,
)
from volundr.config import Settings
from volundr.domain.presented_files import PresentedFileStoreUnavailableError

LOCAL = "volundr.adapters.outbound.local_presented_files.LocalPresentedFileStore"
POSTGRES = "volundr.adapters.outbound.postgres_presented_files.PostgresPresentedFileStore"
K8S_PROJECTION = (
    "volundr.adapters.outbound.execution_credential_k8s.KubernetesExecutionCredentialProjection"
)


def _presented(store: dict, *, enabled: bool = True, max_total_bytes: int = 10) -> Settings:
    return Settings(
        presented_files={
            "enabled": enabled,
            "max_total_bytes": max_total_bytes,
            "store": store,
        }
    )


async def test_no_store_when_presented_files_are_off():
    assert await create_presented_file_store(_presented({}, enabled=False), pool=None) is None


async def test_the_configured_store_is_built_and_its_capacity_checked(tmp_path):
    settings = _presented({"adapter": LOCAL, "root": str(tmp_path / "copies")})
    store = await create_presented_file_store(settings, pool=object())
    assert isinstance(store, LocalPresentedFileStore)
    assert (tmp_path / "copies").is_dir()


async def test_the_postgres_store_receives_forges_pool(tmp_path):
    class Pool:
        async def fetchval(self, query, *args):
            return 0

    pool = Pool()
    settings = _presented({"adapter": POSTGRES, "root": str(tmp_path / "volume")})
    store = await create_presented_file_store(settings, pool)
    assert store._pool is pool


async def test_a_store_that_cannot_hold_the_quota_stops_forge(tmp_path):
    settings = _presented({"adapter": LOCAL, "root": str(tmp_path)}, max_total_bytes=10**18)
    with pytest.raises(PresentedFileStoreUnavailableError, match="max_total_bytes"):
        await create_presented_file_store(settings, pool=None)


async def test_an_adapter_that_is_not_a_store_is_refused():
    settings = _presented({"adapter": "collections.OrderedDict"})
    with pytest.raises(TypeError, match="must implement PresentedFileStore"):
        await create_presented_file_store(settings, pool=None)


def _tokens(**session_tokens) -> Settings:
    return Settings(forge_mcp={"session_tokens": session_tokens})


def test_no_projection_unless_configured():
    assert _create_forge_session_projection(_tokens()) is None
    assert (
        _create_forge_session_projection(_tokens(enabled=False, projection_adapter=K8S_PROJECTION))
        is None
    )


def test_the_configured_projection_is_built_with_its_kwargs():
    projection = _create_forge_session_projection(
        _tokens(
            projection_adapter=K8S_PROJECTION,
            projection_kwargs={"namespace": "volundr-sessions", "secret_prefix": "forge-session"},
        )
    )
    assert projection.supports("kubernetes")
    assert projection._namespace == "volundr-sessions"
    assert projection._secret_prefix == "forge-session"


def test_a_projection_adapter_of_the_wrong_kind_is_refused():
    with pytest.raises(TypeError, match="ExecutionCredentialProjectionPort"):
        _create_forge_session_projection(_tokens(projection_adapter="collections.OrderedDict"))
