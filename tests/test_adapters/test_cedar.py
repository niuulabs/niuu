"""Real Cedar engine tests, including policy changes from the Cerbos rules."""

from dataclasses import replace
from importlib.resources import files

import cedarpy
import pytest

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import Resource
from identity.ports import AuthorizationEvaluationError
from niuu.domain.models import Principal


@pytest.fixture
def cedar():
    return CedarAuthorizationAdapter()


def principal(role="developer", tenant="acme", user="alice"):
    return Principal(user, "", tenant, [f"volundr:{role}"])


def resource(kind="session", owner="alice", tenant="acme", id="one"):
    return Resource(kind, id, {"owner_id": owner, "tenant_id": tenant})


@pytest.mark.parametrize(
    "role,owner,action,allowed",
    [
        ("developer", "alice", "create", True),
        ("viewer", "alice", "create", False),
        ("viewer", "bob", "read", True),
        ("viewer", "alice", "delete", False),
        ("developer", "bob", "read", False),
        ("developer", "alice", "start", True),
        ("developer", "bob", "start", False),
        ("admin", "bob", "delete", True),
        ("developer", "alice", "report_usage", True),
        ("viewer", "alice", "report_usage", False),
        ("unknown", "alice", "read", False),
        ("admin", "alice", "invented", False),
    ],
)
async def test_session_policy(cedar, role, owner, action, allowed):
    assert await cedar.is_allowed(principal(role), action, resource(owner=owner)) is allowed


@pytest.mark.parametrize(
    "kind", ["session", "secret", "tenant", "personal_access_token", "saga", "run"]
)
@pytest.mark.parametrize("role", ["developer", "admin", "viewer"])
@pytest.mark.parametrize("tenant", ["other", "", None])
async def test_tenant_boundary_overrides_every_role(cedar, kind, role, tenant):
    assert not await cedar.is_allowed(principal(role), "read", resource(kind, tenant=tenant))


@pytest.mark.parametrize(
    "kind,action,role,owner,allowed",
    [
        ("personal_access_token", "delete", "developer", "alice", True),
        ("personal_access_token", "read", "admin", "bob", False),
        ("saga", "update", "developer", "alice", True),
        ("saga", "update", "admin", "bob", False),
        ("saga", "read", "viewer", "bob", True),
        ("run", "approve", "developer", "alice", True),
        ("run", "approve", "admin", "bob", False),
        ("run", "retry", "viewer", "alice", False),
        ("secret", "update", "admin", "bob", True),
        ("secret", "update", "developer", "bob", False),
        ("secret", "read", "viewer", "bob", True),
        ("tenant", "update", "admin", "bob", True),
        ("tenant", "update", "developer", "alice", False),
        ("tenant", "read", "viewer", "bob", True),
        ("unknown", "read", "admin", "alice", False),
        ("run", "delete", "admin", "alice", False),
    ],
)
async def test_resource_policies(cedar, kind, action, role, owner, allowed):
    assert await cedar.is_allowed(principal(role), action, resource(kind, owner=owner)) is allowed


async def test_missing_identity_never_authorizes(cedar):
    assert not await cedar.is_allowed(principal(tenant=""), "read", resource())
    assert not await cedar.is_allowed(principal(user=""), "read", resource())
    assert not await cedar.is_allowed(replace(principal(), roles=[]), "read", resource())


async def test_batch_preserves_order_and_distinguishes_types(cedar):
    resources = [resource(owner="bob"), resource("saga", owner="bob"), resource(id="two")]
    assert await cedar.filter_allowed(principal(), "read", resources) == resources[1:]
    assert await cedar.filter_allowed(principal(), "read", []) == []


async def test_conflicting_snapshots_fail(cedar):
    with pytest.raises(AuthorizationEvaluationError, match="Conflicting"):
        await cedar.filter_allowed(principal(), "read", [resource(), resource(owner="bob")])


async def test_entity_id_is_data_not_policy_syntax(cedar):
    user = 'alice"; permit(principal, action, resource); //'
    assert await cedar.is_allowed(principal(user=user), "read", resource(owner=user, id=user))
    assert not await cedar.is_allowed(principal(user=user), "read", resource(owner="bob"))


async def test_malformed_entities_raise_instead_of_partial_allow(cedar):
    with pytest.raises(AuthorizationEvaluationError):
        await cedar.is_allowed(principal(), "read", resource(tenant={"bad": "type"}))


def test_invalid_policy_bundle_rejected_at_startup(tmp_path):
    policies = tmp_path / "bad.cedar"
    policies.write_text('permit(principal, action, resource) when { principal.missing == "x" };')
    schema = tmp_path / "schema.json"
    schema.write_text((files("identity.policies") / "schema.json").read_text())
    with pytest.raises(ValueError, match="validation"):
        CedarAuthorizationAdapter(policies_path=str(policies), schema_path=str(schema))
    with pytest.raises(ValueError, match="both"):
        CedarAuthorizationAdapter(policies_path=str(policies))


async def test_cedar_skip_on_error_cannot_turn_into_allow(cedar, monkeypatch):
    # A real Cedar policy error can coexist with Allow. The adapter must reject
    # that response even if an engine or policy update introduces this condition.
    response = cedarpy.is_authorized(
        {"principal": 'User::"u"', "action": 'Action::"read"', "resource": 'Item::"i"'},
        "permit(principal, action, resource); forbid(principal, action, resource) "
        'when { resource.missing == "x" };',
        [{"uid": {"type": "Item", "id": "i"}, "attrs": {}, "parents": []}],
    )
    assert response.allowed and response.diagnostics.errors
    monkeypatch.setattr(cedarpy, "is_authorized_batch", lambda *a, **kw: [response])
    with pytest.raises(AuthorizationEvaluationError):
        await cedar.is_allowed(principal(), "read", resource())
