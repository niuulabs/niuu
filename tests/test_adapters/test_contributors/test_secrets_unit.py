"""A workflow run's unit is one pod, so it has one secret injection: the root's.

A member is a container of its unit root's pod. It cannot bring a ServiceAccount, injector
annotations or init containers of its own, so the root's injection has to render what the
members need and a member's own pipeline adds nothing to the pod.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.contributors.secrets import SecretInjectionContributor
from volundr.domain.models import (
    GitSource,
    IntegrationConnection,
    IntegrationDefinition,
    IntegrationType,
    PodSpecAdditions,
    Session,
)
from volundr.domain.ports import SessionContext, UnitMemberContext
from volundr.domain.services.integration_registry import IntegrationRegistry

OWNER = "user-1"


def _connection(slug: str, credential_name: str, connection_id: str) -> IntegrationConnection:
    return IntegrationConnection(
        id=connection_id,
        owner_id=OWNER,
        integration_type="ai_provider",
        adapter="some.adapter",
        credential_name=credential_name,
        config={},
        enabled=True,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        slug=slug,
    )


def _registry(**env_by_slug: dict[str, str]) -> IntegrationRegistry:
    return IntegrationRegistry(
        [
            IntegrationDefinition(
                slug=slug,
                name=slug,
                description=slug,
                integration_type=IntegrationType.AI_PROVIDER,
                adapter="some.adapter",
                env_from_credentials=env,
            )
            for slug, env in env_by_slug.items()
        ]
    )


def _adapter(*, per_pod: bool = True, guest_agents: bool = False) -> AsyncMock:
    adapter = AsyncMock()
    adapter.injects_per_pod = per_pod
    adapter.supports_guest_agents = guest_agents
    adapter.pod_spec_additions.return_value = PodSpecAdditions(
        service_account="openbao-session-root",
        annotations={"vault.hashicorp.com/agent-inject": "true"},
    )
    return adapter


def _root() -> Session:
    return Session(
        name="root",
        model="claude",
        source=GitSource(repo="", branch="main"),
        owner_id=OWNER,
        workload_config={"hosted_unit": {}},
    )


def _member(root: Session) -> Session:
    return Session(
        name="member",
        model="claude",
        source=GitSource(repo="", branch="main"),
        owner_id=OWNER,
        hosted_by=root.id,
        hosted_port=8082,
    )


def _scope(member_id: str, *connections: IntegrationConnection, owner: str = OWNER):
    return UnitMemberContext(
        session_id=member_id,
        owner_id=owner,
        tenant_id=None,
        integration_ids=tuple(c.id for c in connections),
        integration_connections=connections,
    )


class TestMember:
    async def test_a_member_adds_no_pod_level_parts_and_no_injection_resources(self):
        adapter = _adapter()
        context = SessionContext(integration_connections=(_connection("test", "cred", "c-1"),))

        result = await SecretInjectionContributor(
            secret_injection=adapter, integration_registry=_registry(test={"TOKEN": "token"})
        ).contribute(_member(_root()), context)

        assert result.pod_spec is None
        adapter.ensure_secret_provider_class.assert_not_called()
        adapter.pod_spec_additions.assert_not_called()

    async def test_a_member_keeps_the_values_its_credentials_add(self):
        context = SessionContext(integration_connections=(_connection("test", "cred", "c-1"),))

        result = await SecretInjectionContributor(secret_injection=_adapter()).contribute(
            _member(_root()), context
        )

        assert result.values["openshell"]["credentialMappings"][0]["credentialName"] == "cred"

    async def test_an_injector_that_works_per_session_still_gives_a_member_its_own(self):
        adapter = _adapter(per_pod=False)
        context = SessionContext(integration_connections=(_connection("test", "cred", "c-1"),))

        result = await SecretInjectionContributor(secret_injection=adapter).contribute(
            _member(_root()), context
        )

        assert result.pod_spec is adapter.pod_spec_additions.return_value
        adapter.ensure_secret_provider_class.assert_called_once()

    async def test_a_vm_unit_keeps_each_members_own_guest_agent(self):
        adapter = _adapter(guest_agents=True)
        guest = PodSpecAdditions(service_account="guest")
        adapter.guest_agent_pod_spec_additions.return_value = guest
        context = SessionContext(
            runtime_backend="vm",
            integration_connections=(_connection("test", "cred", "c-1"),),
        )

        result = await SecretInjectionContributor(secret_injection=adapter).contribute(
            _member(_root()), context
        )

        assert result.pod_spec is guest

    async def test_a_member_with_credentials_and_no_injection_configured_is_refused(self):
        context = SessionContext(integration_connections=(_connection("test", "cred", "c-1"),))

        with pytest.raises(ValueError, match="require configured secret injection"):
            await SecretInjectionContributor().contribute(_member(_root()), context)

    async def test_ending_a_member_leaves_the_unit_injection_to_its_root(self):
        root = _root()
        adapter = _adapter()
        contributor = SecretInjectionContributor(secret_injection=adapter)

        await contributor.cleanup(_member(root), SessionContext())
        adapter.cleanup_session.assert_not_called()

        await contributor.cleanup(root, SessionContext())
        adapter.cleanup_session.assert_called_once_with(str(root.id))


class TestRoot:
    async def test_the_roots_injection_renders_what_its_members_need(self):
        root = _root()
        adapter = _adapter()
        registry = _registry(test={"TOKEN": "token"}, other={"OTHER_TOKEN": "token"})
        context = SessionContext(
            integration_connections=(_connection("test", "root-cred", "c-1"),),
            unit_members=(_scope("m-1", _connection("other", "member-cred", "c-2")),),
        )

        result = await SecretInjectionContributor(
            secret_injection=adapter, integration_registry=registry
        ).contribute(root, context)

        adapter.ensure_secret_provider_class.assert_called_once()
        _, mappings = adapter.ensure_secret_provider_class.call_args[0]
        assert adapter.ensure_secret_provider_class.call_args[1]["session_id"] == str(root.id)
        assert [(m.credential_name, m.env_mappings) for m in mappings] == [
            ("root-cred", {"TOKEN": "token"}),
            ("member-cred", {"OTHER_TOKEN": "token"}),
        ]
        adapter.pod_spec_additions.assert_called_once_with(OWNER, str(root.id))
        assert result.pod_spec is adapter.pod_spec_additions.return_value

    async def test_a_root_with_no_credentials_of_its_own_still_injects_its_members(self):
        adapter = _adapter()
        context = SessionContext(
            unit_members=(_scope("m-1", _connection("test", "member-cred", "c-2")),)
        )

        result = await SecretInjectionContributor(
            secret_injection=adapter, integration_registry=_registry(test={"TOKEN": "token"})
        ).contribute(_root(), context)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert [m.credential_name for m in mappings] == ["member-cred"]
        assert result.pod_spec is adapter.pod_spec_additions.return_value

    async def test_a_credential_every_member_needs_is_rendered_once(self):
        adapter = _adapter()
        shared = _connection("test", "shared", "c-1")
        context = SessionContext(
            integration_connections=(shared,),
            unit_members=(_scope("m-1", shared), _scope("m-2", shared)),
        )

        await SecretInjectionContributor(
            secret_injection=adapter, integration_registry=_registry(test={"TOKEN": "token"})
        ).contribute(_root(), context)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert [m.credential_name for m in mappings] == ["shared"]

    async def test_two_members_wanting_one_env_var_from_different_credentials_is_refused(self):
        adapter = _adapter()
        context = SessionContext(
            integration_connections=(_connection("test", "first", "c-1"),),
            unit_members=(_scope("m-1", _connection("test", "second", "c-2")),),
        )

        with pytest.raises(ValueError, match="attach the same integrations to every member"):
            await SecretInjectionContributor(
                secret_injection=adapter, integration_registry=_registry(test={"TOKEN": "token"})
            ).contribute(_root(), context)

        adapter.ensure_secret_provider_class.assert_not_called()

    async def test_a_member_of_another_owner_is_refused(self):
        context = SessionContext(unit_members=(_scope("m-1", owner="someone-else"),))

        with pytest.raises(ValueError, match="belongs to another owner or tenant"):
            await SecretInjectionContributor(secret_injection=_adapter()).contribute(
                _root(), context
            )

    async def test_a_vm_unit_root_does_not_take_on_its_members_credentials(self):
        adapter = _adapter(guest_agents=True)
        adapter.guest_agent_pod_spec_additions.return_value = PodSpecAdditions()
        context = SessionContext(
            runtime_backend="vm",
            integration_connections=(_connection("test", "root-cred", "c-1"),),
            unit_members=(_scope("m-1", _connection("test", "member-cred", "c-2")),),
        )

        await SecretInjectionContributor(
            secret_injection=adapter, integration_registry=_registry(test={"TOKEN": "token"})
        ).contribute(_root(), context)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert [m.credential_name for m in mappings] == ["root-cred"]

    async def test_a_context_without_members_is_unchanged(self):
        adapter = _adapter()
        context = SessionContext(integration_connections=(_connection("test", "cred", "c-1"),))

        await SecretInjectionContributor(
            secret_injection=adapter, integration_registry=_registry(test={"TOKEN": "token"})
        ).contribute(_root(), context)

        mappings = adapter.ensure_secret_provider_class.call_args[0][1]
        assert [m.credential_name for m in mappings] == ["cred"]
