"""Tests for PostgresRealmRepository — asyncpg-backed realm governance storage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from niuu.adapters.postgres_realms import PostgresRealmRepository
from niuu.domain.models import Capability, Realm, TrustGrant

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_REALM_ID = UUID("00000000-0000-0000-0000-000000000001")
_GRANT_ID = UUID("00000000-0000-0000-0000-000000000002")
_CAP_ID = UUID("00000000-0000-0000-0000-000000000003")


def _make_row(mapping: dict) -> MagicMock:
    """Build a fake asyncpg Record-like object from a mapping."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: mapping[key]
    return row


def _realm_row(**kwargs) -> MagicMock:
    defaults = {
        "id": _REALM_ID,
        "slug": "forge",
        "name": "Forge",
        "sleipnir_domain": "forge.mesh",
        "owner_id": "user-1",
        "instance_id": "inst-1",
        "autonomy_profile": "balanced",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    return _make_row(defaults)


def _grant_row(**kwargs) -> MagicMock:
    defaults = {
        "id": _GRANT_ID,
        "realm_id": _REALM_ID,
        "action_class": "build",
        "target": "*",
        "level": 2,
        "limits": {"workflow": "tool-builder"},
        "granted_by": "admin",
        "granted_at": _NOW,
    }
    defaults.update(kwargs)
    return _make_row(defaults)


def _cap_row(**kwargs) -> MagicMock:
    defaults = {
        "id": _CAP_ID,
        "realm_id": _REALM_ID,
        "name": "grep-tool",
        "kind": "tool",
        "status": "gap",
        "trust_level": 0,
        "mimir_page_path": None,
        "notes": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    return _make_row(defaults)


def _make_repo() -> tuple[PostgresRealmRepository, AsyncMock]:
    pool = AsyncMock()
    repo = PostgresRealmRepository(pool=pool)
    return repo, pool


def _realm() -> Realm:
    return Realm(
        id=_REALM_ID,
        slug="forge",
        name="Forge",
        sleipnir_domain="forge.mesh",
        owner_id="user-1",
        instance_id="inst-1",
        autonomy_profile="balanced",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _grant() -> TrustGrant:
    return TrustGrant(
        id=_GRANT_ID,
        realm_id=_REALM_ID,
        action_class="build",
        target="*",
        level=2,
        limits={"workflow": "tool-builder"},
        granted_by="admin",
        granted_at=_NOW,
    )


def _capability() -> Capability:
    return Capability(
        id=_CAP_ID,
        realm_id=_REALM_ID,
        name="grep-tool",
        kind="tool",
        status="gap",
        trust_level=0,
        mimir_page_path=None,
        notes=None,
        created_at=_NOW,
        updated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Realms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_realms():
    repo, pool = _make_repo()
    pool.fetch.return_value = [_realm_row(), _realm_row(slug="other")]

    realms = await repo.list_realms()

    assert len(realms) == 2
    assert realms[0].slug == "forge"
    pool.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_realm_by_uuid():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = _realm_row()

    realm = await repo.get_realm(_REALM_ID)

    assert realm is not None
    assert realm.id == _REALM_ID
    # UUID branch queries by id
    assert "WHERE id = $1" in pool.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_get_realm_by_uuid_not_found():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = None

    assert await repo.get_realm(_REALM_ID) is None


@pytest.mark.asyncio
async def test_get_realm_by_slug():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = _realm_row()

    realm = await repo.get_realm("forge")

    assert realm is not None
    assert realm.slug == "forge"
    assert "WHERE slug = $1" in pool.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_get_realm_by_slug_not_found():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = None

    assert await repo.get_realm("ghost") is None


@pytest.mark.asyncio
async def test_save_realm_returns_realm():
    repo, pool = _make_repo()

    result = await repo.save_realm(_realm())

    assert result.slug == "forge"
    pool.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# Trust grants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trust_grants():
    repo, pool = _make_repo()
    pool.fetch.return_value = [_grant_row()]

    grants = await repo.list_trust_grants(_REALM_ID)

    assert len(grants) == 1
    assert grants[0].action_class == "build"
    assert grants[0].limits == {"workflow": "tool-builder"}


@pytest.mark.asyncio
async def test_list_trust_grants_decodes_json_string_limits():
    repo, pool = _make_repo()
    pool.fetch.return_value = [_grant_row(limits='{"workflow": "tool-builder"}')]

    grants = await repo.list_trust_grants(_REALM_ID)

    assert grants[0].limits == {"workflow": "tool-builder"}


@pytest.mark.asyncio
async def test_list_trust_grants_empty_limits():
    repo, pool = _make_repo()
    pool.fetch.return_value = [_grant_row(limits=None)]

    grants = await repo.list_trust_grants(_REALM_ID)

    assert grants[0].limits == {}


@pytest.mark.asyncio
async def test_save_trust_grant_returns_grant():
    repo, pool = _make_repo()

    result = await repo.save_trust_grant(_grant())

    assert result.action_class == "build"
    pool.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_capabilities():
    repo, pool = _make_repo()
    pool.fetch.return_value = [_cap_row(), _cap_row(name="other")]

    caps = await repo.list_capabilities(_REALM_ID)

    assert len(caps) == 2
    assert caps[0].name == "grep-tool"


@pytest.mark.asyncio
async def test_save_capability_returns_capability():
    repo, pool = _make_repo()

    result = await repo.save_capability(_capability())

    assert result.name == "grep-tool"
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_capability_returns_mapped_row():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = _cap_row(status="building")

    result = await repo.upsert_capability(_capability())

    assert result.status == "building"
    assert result.name == "grep-tool"
    pool.fetchrow.assert_awaited_once()
