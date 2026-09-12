"""Tests for multi-tenant session scoping and ownership validation."""

import pytest

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter
from volundr.domain.models import GitSource, Principal, SessionStatus, TenantRole
from volundr.domain.services import SessionService
from volundr.domain.services.session import SessionAccessDeniedError

Repo = InMemorySessionRepository
Pods = MockPodManager
_authz = SimpleRoleAuthorizationAdapter()


def _principal(
    user_id: str = "user-1",
    tenant_id: str = "tenant-a",
    roles: list[str] | None = None,
) -> Principal:
    """Build a Principal for tests."""
    return Principal(
        user_id=user_id,
        email=f"{user_id}@example.com",
        tenant_id=tenant_id,
        roles=roles or [TenantRole.DEVELOPER],
    )


class TestCreateSessionScoping:
    """create_session sets owner_id and tenant_id from principal."""

    async def test_create_sets_owner_and_tenant(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        principal = _principal(user_id="alice", tenant_id="acme")

        session = await service.create_session(
            name="s1",
            model="m",
            source=GitSource(repo="r", branch="main"),
            principal=principal,
        )

        assert session.owner_id == "alice"
        assert session.tenant_id == "acme"

    async def test_create_without_principal_is_denied(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)

        with pytest.raises(SessionAccessDeniedError):
            await service.create_session(
                name="s1", model="m", source=GitSource(repo="r", branch="main")
            )
        assert await repository.list() == []


class TestListSessionsScoping:
    """list_sessions filters by tenant and owner based on principal."""

    async def _seed(self, service, repo):
        """Seed sessions for two users in two tenants."""
        p_alice = _principal(user_id="alice", tenant_id="acme")
        p_bob = _principal(user_id="bob", tenant_id="acme")
        p_carol = _principal(user_id="carol", tenant_id="globex")

        s_alice = await service.create_session(
            name="alice-1",
            model="m",
            source=GitSource(repo="r", branch="main"),
            principal=p_alice,
        )
        s_bob = await service.create_session(
            name="bob-1",
            model="m",
            source=GitSource(repo="r", branch="main"),
            principal=p_bob,
        )
        s_carol = await service.create_session(
            name="carol-1",
            model="m",
            source=GitSource(repo="r", branch="main"),
            principal=p_carol,
        )
        return s_alice, s_bob, s_carol, p_alice, p_bob, p_carol

    async def test_developer_sees_only_own_sessions(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        s_alice, _s_bob, _s_carol, p_alice, _p_bob, _p_carol = await self._seed(
            service,
            repository,
        )

        alice_sessions = await service.list_sessions(principal=p_alice)

        assert len(alice_sessions) == 1
        assert alice_sessions[0].id == s_alice.id

    async def test_admin_sees_all_in_tenant(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        s_alice, s_bob, s_carol, *_ = await self._seed(service, repository)

        admin = _principal(
            user_id="admin-user",
            tenant_id="acme",
            roles=[TenantRole.ADMIN],
        )
        sessions = await service.list_sessions(principal=admin)

        session_ids = {s.id for s in sessions}
        assert s_alice.id in session_ids
        assert s_bob.id in session_ids
        assert s_carol.id not in session_ids  # different tenant

    async def test_cross_tenant_isolation(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        _s_alice, _s_bob, s_carol, _, _, p_carol = await self._seed(
            service,
            repository,
        )

        carol_sessions = await service.list_sessions(principal=p_carol)

        assert len(carol_sessions) == 1
        assert carol_sessions[0].id == s_carol.id

    async def test_no_principal_is_denied(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        await self._seed(service, repository)

        with pytest.raises(PermissionError):
            await service.list_sessions(principal=None)


class TestOwnershipValidation:
    """Mutation operations enforce ownership for non-admin users."""

    async def _create_session_as(
        self,
        service,
        principal,
        name="test",
    ):
        return await service.create_session(
            name=name,
            model="m",
            source=GitSource(repo="r", branch="main"),
            principal=principal,
        )

    async def test_owner_can_update(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        updated = await service.update_session(
            session.id,
            name="renamed",
            principal=alice,
        )

        assert updated.name == "renamed"

    async def test_other_user_cannot_update(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        bob = _principal(user_id="bob", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        with pytest.raises(SessionAccessDeniedError):
            await service.update_session(
                session.id,
                name="hacked",
                principal=bob,
            )

    async def test_admin_can_update_others(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        admin = _principal(
            user_id="admin",
            tenant_id="acme",
            roles=[TenantRole.ADMIN],
        )
        session = await self._create_session_as(service, alice)

        updated = await service.update_session(
            session.id,
            name="admin-rename",
            principal=admin,
        )

        assert updated.name == "admin-rename"

    async def test_cross_tenant_admin_cannot_access(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        other_admin = _principal(
            user_id="evil-admin",
            tenant_id="globex",
            roles=[TenantRole.ADMIN],
        )
        session = await self._create_session_as(service, alice)

        with pytest.raises(SessionAccessDeniedError):
            await service.update_session(
                session.id,
                name="hacked",
                principal=other_admin,
            )

    async def test_owner_can_delete(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        result = await service.delete_session(session.id, principal=alice)

        assert result is True

    async def test_other_user_cannot_delete(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        bob = _principal(user_id="bob", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        with pytest.raises(SessionAccessDeniedError):
            await service.delete_session(session.id, principal=bob)

    async def test_owner_can_start(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        result = await service.start_session(
            session.id,
            principal=alice,
        )

        assert result.status == SessionStatus.STARTING

    async def test_other_user_cannot_start(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        bob = _principal(user_id="bob", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        with pytest.raises(SessionAccessDeniedError):
            await service.start_session(session.id, principal=bob)

    async def test_owner_can_stop(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        # Start first, then stop
        await service.start_session(session.id, principal=alice)
        result = await service.stop_session(session.id, principal=alice)

        assert result.status == SessionStatus.STOPPED

    async def test_other_user_cannot_stop(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        bob = _principal(user_id="bob", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        await service.start_session(session.id, principal=alice)

        with pytest.raises(SessionAccessDeniedError):
            await service.stop_session(session.id, principal=bob)

    async def test_no_principal_is_denied(self, repository: Repo, pod_manager: Pods):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)
        for method in [
            service.update_session,
            service.start_session,
            service.stop_session,
            service.delete_session,
        ]:
            with pytest.raises(SessionAccessDeniedError):
                await method(session.id, principal=None)
        assert await repository.get(session.id) == session

    async def test_owner_can_archive(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        result = await service.archive_session(session.id, principal=alice)
        assert result.status == SessionStatus.ARCHIVED

    async def test_owner_can_archive_running_session(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)
        await service.start_session(session.id, principal=alice)

        result = await service.archive_session(session.id, principal=alice)
        assert result.status == SessionStatus.ARCHIVED

    async def test_other_user_cannot_archive(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        bob = _principal(user_id="bob", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        with pytest.raises(SessionAccessDeniedError):
            await service.archive_session(session.id, principal=bob)

    async def test_owner_can_restore(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)
        await service.archive_session(session.id, principal=alice)

        result = await service.restore_session(session.id, principal=alice)
        assert result.status == SessionStatus.STOPPED

    async def test_other_user_cannot_restore(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        bob = _principal(user_id="bob", tenant_id="acme")
        session = await self._create_session_as(service, alice)
        await service.archive_session(session.id, principal=alice)

        with pytest.raises(SessionAccessDeniedError):
            await service.restore_session(session.id, principal=bob)

    async def test_owner_can_update_tracker_issue(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        service = SessionService(repository, pod_manager, authorization=_authz)
        alice = _principal(user_id="alice", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        updated = await service.update_session(
            session.id,
            tracker_issue_id="NIU-123",
            principal=alice,
        )
        assert updated.tracker_issue_id == "NIU-123"

    async def test_no_authorization_adapter_allows_all(
        self,
        repository: Repo,
        pod_manager: Pods,
    ):
        """Without authorization adapter, all operations are allowed."""
        service = SessionService(repository, pod_manager)
        alice = _principal(user_id="alice", tenant_id="acme")
        bob = _principal(user_id="bob", tenant_id="acme")
        session = await self._create_session_as(service, alice)

        # Bob can update Alice's session when no authz adapter
        updated = await service.update_session(
            session.id,
            name="no-authz",
            principal=bob,
        )
        assert updated.name == "no-authz"
