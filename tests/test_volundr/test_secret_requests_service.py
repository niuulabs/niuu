"""Secret request service, and what a session ending does to its secret requests."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.support.secret_requests import InMemorySecretRequestRepository
from volundr.domain.models import Session, SessionStatus
from volundr.domain.secret_request_ports import SessionSecretCleanup
from volundr.domain.secret_requests import (
    SecretKeep,
    SecretRequestNotFoundError,
    SecretRequestStateError,
    SecretRequestStatus,
    SecretRequestValidationError,
    SecretValueMissingError,
    saved_credential_name,
    session_credential_name,
    validate_secret_name,
)
from volundr.domain.services.secret_requests import CREDENTIAL_OWNER_TYPE, SecretRequestService
from volundr.domain.services.session import SessionService

OWNER = "owner-1"
VALUE = "s3cret-value"


@pytest.fixture
def repository() -> InMemorySecretRequestRepository:
    return InMemorySecretRequestRepository()


@pytest.fixture
def credentials() -> MemoryCredentialStore:
    return MemoryCredentialStore()


@pytest.fixture
def service(repository, credentials) -> SecretRequestService:
    return SecretRequestService(repository, credentials)


def make_session(status: SessionStatus = SessionStatus.RUNNING) -> Session:
    return Session(name="s", owner_id=OWNER, tenant_id="t", status=status)


async def ask(service, session, name="GITLAB_TOKEN"):
    request, _ = await service.create(session, name=name, purpose="push", target=None)
    return request


class TestNames:
    @pytest.mark.parametrize("name", ["GITLAB_TOKEN", "_X", "A1_B2", "A" * 255])
    def test_legal(self, name):
        assert validate_secret_name(name) == name

    @pytest.mark.parametrize("name", ["", "a", "1A", "A-B", "A B", "A/B", "A\n", "A" * 256])
    def test_illegal(self, name):
        with pytest.raises(SecretRequestValidationError):
            validate_secret_name(name)

    def test_saved_credential_name_is_kebab_case(self):
        assert saved_credential_name("GITLAB_TOKEN") == "gitlab-token"
        assert saved_credential_name("API") == "api"


class TestService:
    async def test_a_blank_purpose_is_refused(self, service):
        with pytest.raises(SecretRequestValidationError):
            await service.create(make_session(), name="A_KEY", purpose="  ", target=None)

    async def test_an_empty_value_is_refused_and_nothing_is_stored(self, service, credentials):
        request = await ask(service, make_session())
        with pytest.raises(SecretRequestValidationError):
            await service.fulfill(request.session_id, request.id, value="", keep=SecretKeep.SESSION)
        assert credentials._values == {}

    async def test_a_request_is_only_visible_through_its_own_session(self, service):
        request = await ask(service, make_session())
        with pytest.raises(SecretRequestNotFoundError):
            await service.get(uuid4(), request.id)

    async def test_a_value_stored_for_a_request_that_closed_meanwhile_is_removed(
        self, service, repository, credentials, monkeypatch
    ):
        request = await ask(service, make_session())
        real = repository.mark_fulfilled

        async def cancelled_first(request_id, **kwargs):
            await repository.mark_cancelled(request_id, at=kwargs["at"])
            return await real(request_id, **kwargs)

        monkeypatch.setattr(repository, "mark_fulfilled", cancelled_first)

        with pytest.raises(SecretRequestStateError) as raised:
            await service.fulfill(
                request.session_id, request.id, value=VALUE, keep=SecretKeep.SESSION
            )

        assert raised.value.status is SecretRequestStatus.CANCELLED
        assert credentials._values == {}
        assert VALUE not in str(raised.value)

    async def test_only_one_of_two_concurrent_claims_gets_the_value(
        self, service, repository, monkeypatch
    ):
        request = await ask(service, make_session())
        await service.fulfill(request.session_id, request.id, value=VALUE, keep=SecretKeep.SAVED)
        real = repository.mark_delivered

        async def lost_race(request_id, **kwargs):
            await real(request_id, **kwargs)  # the other claim got there first
            return None

        monkeypatch.setattr(repository, "mark_delivered", lost_race)

        with pytest.raises(SecretRequestStateError) as raised:
            await service.claim(request.session_id, request.id)
        assert raised.value.status is SecretRequestStatus.DELIVERED

    async def test_claim_without_a_stored_value_is_an_error(self, service, credentials):
        request = await ask(service, make_session())
        await service.fulfill(request.session_id, request.id, value=VALUE, keep=SecretKeep.SESSION)
        await credentials.delete(CREDENTIAL_OWNER_TYPE, OWNER, session_credential_name(request.id))

        with pytest.raises(SecretValueMissingError):
            await service.claim(request.session_id, request.id)

    async def test_a_value_that_cannot_be_deleted_is_not_handed_over(
        self, service, repository, credentials, monkeypatch
    ):
        request = await ask(service, make_session())
        await service.fulfill(request.session_id, request.id, value=VALUE, keep=SecretKeep.SESSION)

        async def broken(*args, **kwargs):
            raise ConnectionError("openbao down")

        monkeypatch.setattr(credentials, "delete", broken)

        with pytest.raises(ConnectionError):
            await service.claim(request.session_id, request.id)
        # still fulfilled, so the claim can be retried once the store is back
        assert repository.rows[request.id].status is SecretRequestStatus.FULFILLED


class TestSessionEnd:
    async def test_cancels_open_requests_and_deletes_session_only_values(
        self, service, repository, credentials
    ):
        session = make_session()
        pending = await ask(service, session, "A_KEY")
        kept_session = await ask(service, session, "B_KEY")
        kept_saved = await ask(service, session, "C_KEY")
        done = await ask(service, session, "D_KEY")
        other = await ask(service, make_session(), "A_KEY")
        for request, keep in ((kept_session, SecretKeep.SESSION), (kept_saved, SecretKeep.SAVED)):
            await service.fulfill(session.id, request.id, value=VALUE, keep=keep)
        await service.cancel(session.id, done.id)

        assert await service.cancel_for_session(session.id) == 3

        status = {r.id: r.status for r in repository.rows.values()}
        assert status[pending.id] is SecretRequestStatus.CANCELLED
        assert status[kept_session.id] is SecretRequestStatus.CANCELLED
        assert status[kept_saved.id] is SecretRequestStatus.CANCELLED
        assert status[other.id] is SecretRequestStatus.PENDING
        # the session-only value is gone; what the owner chose to save stays theirs
        assert (
            await credentials.get_value(
                CREDENTIAL_OWNER_TYPE, OWNER, session_credential_name(kept_session.id)
            )
            is None
        )
        assert await credentials.get_value(CREDENTIAL_OWNER_TYPE, OWNER, "c-key") == {
            "value": VALUE
        }

    async def test_purge_also_drops_the_records(self, service, repository):
        session = make_session()
        gone = await ask(service, session)
        kept = await ask(service, make_session())

        await service.purge_session(session.id)

        assert gone.id not in repository.rows
        assert kept.id in repository.rows


class RecordingCleanup(SessionSecretCleanup):
    def __init__(self, fail: bool = False) -> None:
        self.cancelled: list[UUID] = []
        self.purged: list[UUID] = []
        self.fail = fail

    async def cancel_for_session(self, session_id: UUID) -> int:
        if self.fail:
            raise ConnectionError("openbao down")
        self.cancelled.append(session_id)
        return 0

    async def purge_session(self, session_id: UUID) -> None:
        if self.fail:
            raise ConnectionError("openbao down")
        self.purged.append(session_id)


class EndedPodManager(MockPodManager):
    """A pod manager that reports every runtime already stopped."""

    async def status(self, session):
        return SessionStatus.STOPPED


async def running(repo: InMemorySessionRepository, status=SessionStatus.RUNNING) -> Session:
    return await repo.create(make_session(status))


def session_service(repo, cleanup, pod_manager=None) -> SessionService:
    return SessionService(
        repo, pod_manager or MockPodManager(), secret_cleanup=cleanup, provisioning_initial_delay=0
    )


class TestSessionServiceHooks:
    async def test_stopping_a_session_cancels_its_secret_requests(self):
        repo, cleanup = InMemorySessionRepository(), RecordingCleanup()
        session = await running(repo)

        await session_service(repo, cleanup).stop_session(session.id)

        assert cleanup.cancelled == [session.id]

    async def test_a_failed_cleanup_fails_the_stop_loudly_and_keeps_it_retryable(self):
        repo, cleanup = InMemorySessionRepository(), RecordingCleanup(fail=True)
        session = await running(repo)

        with pytest.raises(ConnectionError):
            await session_service(repo, cleanup).stop_session(session.id)

        stored = await repo.get(session.id)
        assert stored.status is SessionStatus.FAILED  # a failed session can be stopped again
        assert "openbao down" in (stored.error or "")

    async def test_archiving_cancels_even_a_session_that_was_already_stopped(self):
        repo, cleanup = InMemorySessionRepository(), RecordingCleanup()
        session = await running(repo, SessionStatus.STOPPED)

        await session_service(repo, cleanup).archive_session(session.id)

        assert cleanup.cancelled == [session.id]

    async def test_deleting_purges_before_the_record_goes(self):
        repo, cleanup = InMemorySessionRepository(), RecordingCleanup()
        session = await running(repo, SessionStatus.STOPPED)

        assert await session_service(repo, cleanup).delete_session(session.id) is True

        assert cleanup.purged == [session.id]

    async def test_a_failed_purge_keeps_the_session_so_the_delete_can_retry(self):
        repo, cleanup = InMemorySessionRepository(), RecordingCleanup(fail=True)
        session = await running(repo, SessionStatus.STOPPED)

        with pytest.raises(ConnectionError):
            await session_service(repo, cleanup).delete_session(session.id)

        assert await repo.get(session.id) is not None

    async def test_a_runtime_found_dead_cancels_too(self):
        repo, cleanup = InMemorySessionRepository(), RecordingCleanup()
        session = await running(repo)

        await session_service(repo, cleanup, EndedPodManager()).mark_session_dead(session.id)

        assert cleanup.cancelled == [session.id]

    async def test_without_the_hook_nothing_changes(self):
        repo = InMemorySessionRepository()
        session = await running(repo)

        stopped = await session_service(repo, None).stop_session(session.id)

        assert stopped.status is SessionStatus.STOPPED
