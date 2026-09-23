"""Integration tests: chronicles are scoped to who may list their session."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/forge"


async def _tenant(txn_pool, tenant_id: str) -> None:
    await txn_pool.execute(
        "INSERT INTO tenants (id, path, name) VALUES ($1, $1, $1)",
        tenant_id,
    )


async def _chronicle(txn_pool, owner_id: str | None, tenant_id: str | None) -> UUID:
    """History that outlived its session, attributed as it was when written."""
    chronicle_id = uuid4()
    await txn_pool.execute(
        "INSERT INTO chronicles "
        "(id, session_id, project, repo, branch, model, owner_id, tenant_id) "
        "VALUES ($1, $2, 'scoped', 'github.com/acme/demo', 'main', 'claude-sonnet-4-6', $3, $4)",
        chronicle_id,
        uuid4(),
        owner_id,
        tenant_id,
    )
    return chronicle_id


async def _listed(client, headers) -> set[UUID]:
    resp = await client.get(f"{API}/chronicles", params={"project": "scoped"}, headers=headers)
    assert resp.status_code == 200, resp.text
    return {UUID(c["id"]) for c in resp.json()}


async def test_chronicle_list_is_bounded_in_sql(volundr_client, auth_headers, txn_pool):
    """Own history for a developer, the tenant's for an admin, never unattributed rows."""
    await _tenant(txn_pool, "chron-t1")
    await _tenant(txn_pool, "chron-t2")
    alice = auth_headers("chron-alice", "chron-alice@test.com", "chron-t1")
    bob = auth_headers("chron-bob", "chron-bob@test.com", "chron-t1")
    t1_admin = auth_headers("chron-root", "chron-root@test.com", "chron-t1", ["volundr:admin"])
    t2_admin = auth_headers("chron-root2", "chron-root2@test.com", "chron-t2", ["volundr:admin"])

    alices = await _chronicle(txn_pool, "chron-alice", "chron-t1")
    bobs = await _chronicle(txn_pool, "chron-bob", "chron-t1")
    unowned = await _chronicle(txn_pool, None, "chron-t1")
    carols = await _chronicle(txn_pool, "chron-carol", "chron-t2")
    await _chronicle(txn_pool, "chron-alice", None)
    await _chronicle(txn_pool, "chron-alice", "")
    await _chronicle(txn_pool, None, None)

    assert await _listed(volundr_client, alice) == {alices}
    assert await _listed(volundr_client, bob) == {bobs}
    assert await _listed(volundr_client, t1_admin) == {alices, bobs, unowned}
    assert await _listed(volundr_client, t2_admin) == {carols}


async def test_another_owners_chronicle_is_not_found(volundr_client, auth_headers, txn_pool):
    """Reads and writes on history outside the caller's scope are 404 and change nothing."""
    await _tenant(txn_pool, "chron-t3")
    owner = auth_headers("chron-dave", "chron-dave@test.com", "chron-t3")
    other = auth_headers("chron-erin", "chron-erin@test.com", "chron-t3")
    chronicle_id = await _chronicle(txn_pool, "chron-dave", "chron-t3")
    url = f"{API}/chronicles/{chronicle_id}"

    assert (await volundr_client.get(url, headers=other)).status_code == 404
    patched = await volundr_client.patch(url, json={"summary": "hijacked"}, headers=other)
    assert patched.status_code == 404
    assert (await volundr_client.delete(url, headers=other)).status_code == 404

    mine = await volundr_client.get(url, headers=owner)
    assert mine.status_code == 200, mine.text
    assert mine.json()["summary"] is None
