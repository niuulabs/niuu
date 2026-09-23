"""Chronicles are scoped to the principals who may see the session they record.

Reads are bounded like ``GET /sessions`` (``SessionService.visibility_scope``
plus the policy's ``read``/``list``); writes also need the policy to allow the
action on the chronicle's own owner and tenant. These tests run the bundled
Cedar policy, so the resource kind and action names are proven against the
real schema rather than an allow-all double.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from identity.adapters.cedar import CedarAuthorizationAdapter
from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    InMemoryTimelineRepository,
    MockPodManager,
)
from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter
from volundr.domain.models import (
    Chronicle,
    GitSource,
    Principal,
    Session,
    TimelineEventType,
)
from volundr.domain.services import (
    ChronicleAccessDeniedError,
    ChronicleNotFoundError,
    ChronicleService,
    SessionAccessDeniedError,
    SessionNotFoundError,
    SessionService,
)

ALICE = Principal(user_id="alice", email="", tenant_id="t1", roles=["volundr:developer"])
BOB = Principal(user_id="bob", email="", tenant_id="t1", roles=["volundr:developer"])
VERA = Principal(user_id="vera", email="", tenant_id="t1", roles=["volundr:viewer"])
T1_ADMIN = Principal(user_id="root", email="", tenant_id="t1", roles=["volundr:admin"])
T2_ADMIN = Principal(user_id="other-root", email="", tenant_id="t2", roles=["volundr:admin"])
ROLELESS_ALICE = Principal(user_id="alice", email="", tenant_id="t1", roles=[])


class _Fixture:
    def __init__(self, authorization=None) -> None:
        self.sessions = InMemorySessionRepository()
        self.chronicles = InMemoryChronicleRepository()
        self.timeline = InMemoryTimelineRepository()
        self.session_service = SessionService(
            self.sessions, MockPodManager(), authorization=authorization
        )
        self.service = ChronicleService(
            self.chronicles, self.session_service, timeline_repository=self.timeline
        )

    def session(self, owner_id: str | None, tenant_id: str | None) -> Session:
        session = Session(
            name=f"{owner_id}-session",
            model="sonnet",
            owner_id=owner_id,
            tenant_id=tenant_id,
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        self.sessions._sessions[session.id] = session
        return session

    def chronicle(
        self,
        owner_id: str | None,
        tenant_id: str | None,
        *,
        session_id: UUID | None = None,
        parent: Chronicle | None = None,
    ) -> Chronicle:
        chronicle = Chronicle(
            session_id=session_id if session_id is not None else uuid4(),
            project=f"{owner_id}-project",
            repo="https://github.com/org/repo",
            branch="main",
            model="sonnet",
            config_snapshot={"name": f"{owner_id}-session", "model": "sonnet"},
            owner_id=owner_id,
            tenant_id=tenant_id,
            parent_chronicle_id=parent.id if parent is not None else None,
        )
        self.chronicles._chronicles[chronicle.id] = chronicle
        return chronicle


@pytest.fixture
def world() -> _Fixture:
    return _Fixture(CedarAuthorizationAdapter())


@pytest.fixture
def seeded(world: _Fixture) -> dict[str, Chronicle]:
    return {
        "alice": world.chronicle("alice", "t1"),
        "bob": world.chronicle("bob", "t1"),
        "vera": world.chronicle("vera", "t1"),
        "other_tenant": world.chronicle("carol", "t2"),
        "unowned": world.chronicle(None, "t1"),
        "untenanted": world.chronicle("alice", None),
        "blank_tenant": world.chronicle("alice", ""),
    }


def _names(chronicles: list[Chronicle], seeded: dict[str, Chronicle]) -> set[str]:
    by_id = {c.id: name for name, c in seeded.items()}
    return {by_id[c.id] for c in chronicles}


class TestListIsScoped:
    async def test_a_developer_lists_only_its_own_history(self, world, seeded):
        result = await world.service.list_chronicles(principal=ALICE)
        assert _names(result, seeded) == {"alice"}

    async def test_a_tenant_admin_lists_its_tenant_including_unowned(self, world, seeded):
        result = await world.service.list_chronicles(principal=T1_ADMIN)
        assert _names(result, seeded) == {"alice", "bob", "vera", "unowned"}

    async def test_another_tenants_admin_sees_none_of_it(self, world, seeded):
        result = await world.service.list_chronicles(principal=T2_ADMIN)
        assert _names(result, seeded) == {"other_tenant"}

    async def test_the_bound_is_pushed_into_the_repository(self, world, seeded):
        calls: list[tuple[str | None, str | None]] = []
        original = world.chronicles.list

        async def spy(**kwargs):
            calls.append((kwargs["tenant_id"], kwargs["owner_id"]))
            return await original(**kwargs)

        world.chronicles.list = spy
        await world.service.list_chronicles(principal=ALICE)
        await world.service.list_chronicles(principal=T1_ADMIN)
        assert calls == [("t1", "alice"), ("t1", None)]

    async def test_the_policy_still_applies_inside_the_bound(self, world, seeded):
        """A principal the policy lets list nothing sees nothing, even its own."""
        assert await world.service.list_chronicles(principal=ROLELESS_ALICE) == []

    async def test_an_absent_principal_is_refused_with_authorization(self, world, seeded):
        with pytest.raises(PermissionError):
            await world.service.list_chronicles(principal=None)

    async def test_an_absent_principal_is_unbounded_without_authorization(self):
        dev = _Fixture()
        seeded = {"a": dev.chronicle("alice", "t1"), "n": dev.chronicle(None, None)}
        result = await dev.service.list_chronicles(principal=None)
        assert _names(result, seeded) == {"a", "n"}


class TestReadsAreScoped:
    async def test_owner_reads_its_chronicle(self, world, seeded):
        found = await world.service.get_chronicle(seeded["alice"].id, principal=ALICE)
        assert found == seeded["alice"]

    @pytest.mark.parametrize(
        ("principal", "name"),
        [
            (BOB, "alice"),
            (T2_ADMIN, "alice"),
            (ALICE, "other_tenant"),
            (T1_ADMIN, "untenanted"),
            (ALICE, "untenanted"),
            (ALICE, "blank_tenant"),
            (ALICE, "unowned"),
        ],
    )
    async def test_history_outside_the_scope_is_not_found(self, world, seeded, principal, name):
        chronicle = seeded[name]
        assert await world.service.get_chronicle(chronicle.id, principal=principal) is None
        assert (
            await world.service.get_session_chronicle(chronicle.session_id, principal=principal)
            is None
        )

    async def test_a_tenant_admin_reads_another_owners_chronicle(self, world, seeded):
        found = await world.service.get_chronicle(seeded["bob"].id, principal=T1_ADMIN)
        assert found == seeded["bob"]

    async def test_an_absent_principal_cannot_probe_for_existence(self, world, seeded):
        for chronicle_id in (seeded["alice"].id, uuid4()):
            with pytest.raises(PermissionError):
                await world.service.get_chronicle(chronicle_id, principal=None)

    async def test_the_chain_leaves_out_what_the_caller_cannot_read(self, world, seeded):
        root = seeded["bob"]
        mine = world.chronicle("alice", "t1", parent=root)
        bobs_leaf = world.chronicle("bob", "t1", parent=mine)

        assert await world.service.get_chain(mine.id, principal=ALICE) == [mine]
        # A chain is only disclosed from a chronicle the caller may read.
        assert await world.service.get_chain(bobs_leaf.id, principal=ALICE) == []
        admin_chain = await world.service.get_chain(bobs_leaf.id, principal=T1_ADMIN)
        assert [c.id for c in admin_chain] == [root.id, mine.id, bobs_leaf.id]

    async def test_the_timeline_follows_its_chronicles_scope(self, world, seeded):
        alice = seeded["alice"]
        assert await world.service.get_timeline(alice.session_id, principal=BOB) is None
        timeline = await world.service.get_timeline(alice.session_id, principal=ALICE)
        assert timeline is not None and timeline.events == []


class TestWritesAreAuthorized:
    async def test_a_developer_updates_its_own_chronicle(self, world, seeded):
        updated = await world.service.update_chronicle(
            seeded["alice"].id, principal=ALICE, summary="done"
        )
        assert updated.summary == "done"

    async def test_a_tenant_admin_updates_another_owners_chronicle(self, world, seeded):
        updated = await world.service.update_chronicle(
            seeded["bob"].id, principal=T1_ADMIN, summary="reviewed"
        )
        assert updated.summary == "reviewed"

    @pytest.mark.parametrize(
        ("principal", "name"),
        [(BOB, "alice"), (T2_ADMIN, "alice"), (T1_ADMIN, "untenanted"), (ALICE, "unowned")],
    )
    async def test_history_outside_the_scope_cannot_be_changed(
        self, world, seeded, principal, name
    ):
        chronicle = seeded[name]
        with pytest.raises(ChronicleNotFoundError):
            await world.service.update_chronicle(chronicle.id, principal=principal, summary="x")
        with pytest.raises(ChronicleNotFoundError):
            await world.service.delete_chronicle(chronicle.id, principal=principal)
        with pytest.raises(ChronicleNotFoundError):
            await world.service.reforge(chronicle.id, principal=principal)
        assert world.chronicles._chronicles[chronicle.id] == chronicle
        assert world.sessions._sessions == {}

    async def test_a_viewer_sees_its_history_but_cannot_change_it(self, world, seeded):
        vera = seeded["vera"]
        assert await world.service.get_chronicle(vera.id, principal=VERA) == vera
        for action, call in (
            ("update", world.service.update_chronicle(vera.id, principal=VERA, summary="x")),
            ("delete", world.service.delete_chronicle(vera.id, principal=VERA)),
            ("start", world.service.reforge(vera.id, principal=VERA)),
        ):
            with pytest.raises(ChronicleAccessDeniedError) as denied:
                await call
            assert denied.value.action == action
        assert world.chronicles._chronicles[vera.id] == vera
        assert world.sessions._sessions == {}

    async def test_an_absent_principal_is_refused_before_lookup(self, world, seeded):
        for chronicle_id in (seeded["alice"].id, uuid4()):
            with pytest.raises(PermissionError):
                await world.service.delete_chronicle(chronicle_id, principal=None)
        assert seeded["alice"].id in world.chronicles._chronicles

    async def test_the_owner_deletes_its_chronicle(self, world, seeded):
        await world.service.delete_chronicle(seeded["alice"].id, principal=ALICE)
        assert seeded["alice"].id not in world.chronicles._chronicles

    async def test_a_reforged_session_belongs_to_the_caller(self, world, seeded):
        session = await world.service.reforge(seeded["bob"].id, principal=T1_ADMIN)
        assert (session.owner_id, session.tenant_id) == ("root", "t1")
        assert session.name == "bob-session (reforged)"


class TestSessionKeyedHistoryWrites:
    async def test_creating_a_chronicle_needs_the_session_in_scope(self, world):
        alices = world.session("alice", "t1")
        with pytest.raises(SessionNotFoundError):
            await world.service.create_chronicle(alices.id, principal=BOB)
        assert await world.chronicles.get_by_session(alices.id) is None

        created = await world.service.create_chronicle(alices.id, principal=ALICE)
        assert (created.owner_id, created.tenant_id) == ("alice", "t1")

    async def test_a_viewer_cannot_create_history_for_its_session(self, world):
        veras = world.session("vera", "t1")
        with pytest.raises(SessionAccessDeniedError):
            await world.service.create_chronicle(veras.id, principal=VERA)
        assert await world.chronicles.get_by_session(veras.id) is None

    async def test_a_timeline_append_outside_the_scope_writes_nothing(self, world):
        alices = world.session("alice", "t1")
        with pytest.raises(SessionNotFoundError):
            await world.service.add_timeline_event(
                alices.id, principal=BOB, t=1, type=TimelineEventType.MESSAGE, label="x"
            )
        assert await world.chronicles.get_by_session(alices.id) is None

    async def test_a_denied_timeline_append_writes_nothing(self, world):
        veras = world.session("vera", "t1")
        with pytest.raises(SessionAccessDeniedError):
            await world.service.add_timeline_event(
                veras.id, principal=VERA, t=1, type=TimelineEventType.MESSAGE, label="x"
            )
        assert await world.chronicles.get_by_session(veras.id) is None

    async def test_an_unauthenticated_append_writes_nothing(self, world):
        alices = world.session("alice", "t1")
        with pytest.raises(PermissionError):
            await world.service.add_timeline_event(
                alices.id, principal=None, t=1, type=TimelineEventType.MESSAGE, label="x"
            )
        assert await world.chronicles.get_by_session(alices.id) is None

    async def test_history_keeps_its_scope_after_the_session_is_deleted(self, world):
        alices = world.session("alice", "t1")
        chronicle = await world.service.create_chronicle(alices.id, principal=ALICE)
        del world.sessions._sessions[alices.id]

        stored = await world.service.add_timeline_event(
            alices.id, principal=ALICE, t=2, type=TimelineEventType.GIT, label="c", hash="abc"
        )
        assert stored.chronicle_id == chronicle.id
        reported = await world.service.create_or_update_from_broker(
            alices.id, principal=ALICE, summary="wrapped up"
        )
        assert (reported.id, reported.summary) == (chronicle.id, "wrapped up")

        with pytest.raises(SessionNotFoundError):
            await world.service.add_timeline_event(
                alices.id, principal=BOB, t=3, type=TimelineEventType.MESSAGE, label="x"
            )
        with pytest.raises(SessionNotFoundError):
            await world.service.create_or_update_from_broker(
                alices.id, principal=BOB, summary="hijacked"
            )
        assert (await world.chronicles.get(chronicle.id)).summary == "wrapped up"
        assert len(await world.timeline.get_events(chronicle.id)) == 1

    async def test_an_unattributed_chronicle_is_never_writable_by_a_bounded_caller(self, world):
        legacy = world.chronicle(None, None)
        for principal in (ALICE, T1_ADMIN):
            with pytest.raises(SessionNotFoundError):
                await world.service.add_timeline_event(
                    legacy.session_id,
                    principal=principal,
                    t=0,
                    type=TimelineEventType.MESSAGE,
                    label="x",
                )
        assert await world.timeline.get_events(legacy.id) == []


class TestSimpleRolePolicy:
    """The ownership bound holds even where the policy itself is permissive."""

    async def test_a_same_tenant_developer_cannot_touch_another_owners_history(self):
        simple = _Fixture(SimpleRoleAuthorizationAdapter())
        alices = simple.chronicle("alice", "t1")
        with pytest.raises(ChronicleNotFoundError):
            await simple.service.update_chronicle(alices.id, principal=BOB, summary="x")
        assert await simple.service.list_chronicles(principal=BOB) == []

    async def test_an_unattributed_chronicle_is_not_widened_by_the_policy(self):
        simple = _Fixture(SimpleRoleAuthorizationAdapter())
        legacy = simple.chronicle(None, None)
        with pytest.raises(ChronicleNotFoundError):
            await simple.service.delete_chronicle(legacy.id, principal=T1_ADMIN)
        assert legacy.id in simple.chronicles._chronicles


class TestSessionScopeHelpers:
    @pytest.mark.parametrize(
        ("scope", "owner_id", "tenant_id", "inside"),
        [
            (("t1", "alice"), "alice", "t1", True),
            (("t1", "alice"), "bob", "t1", False),
            (("t1", "alice"), "alice", "t2", False),
            (("t1", None), None, "t1", True),
            (("t1", None), "bob", "t1", True),
            (("t1", None), "bob", None, False),
            (("t1", None), "bob", "", False),
            (("", None), "bob", "", False),
            (("t1", ""), "", "t1", False),
            ((None, None), None, None, True),
        ],
    )
    def test_within_scope_never_widens_for_missing_attribution(
        self, scope, owner_id, tenant_id, inside
    ):
        assert SessionService.within_scope(scope, owner_id=owner_id, tenant_id=tenant_id) is inside

    async def test_authorizes_everything_without_authorization(self):
        sessions = _Fixture().session_service
        resource = SessionService.attributed_resource("s", owner_id=None, tenant_id=None)
        assert await sessions.authorizes(None, "delete", resource) is True
        assert await sessions.filter_authorized(None, "list", [resource]) == [resource]

    async def test_authorization_refuses_an_absent_principal(self, world):
        resource = SessionService.attributed_resource("s", owner_id="alice", tenant_id="t1")
        with pytest.raises(PermissionError):
            await world.session_service.authorizes(None, "read", resource)
        with pytest.raises(PermissionError):
            await world.session_service.filter_authorized(None, "list", [resource])

    async def test_get_authorized_session(self, world):
        alices = world.session("alice", "t1")
        found = await world.session_service.get_authorized_session(alices.id, ALICE, "read")
        assert found == alices
        with pytest.raises(SessionNotFoundError):
            await world.session_service.get_authorized_session(alices.id, BOB, "read")
        with pytest.raises(SessionNotFoundError):
            await world.session_service.get_authorized_session(uuid4(), ALICE, "read")
        veras = world.session("vera", "t1")
        with pytest.raises(SessionAccessDeniedError):
            await world.session_service.get_authorized_session(veras.id, VERA, "update")


class TestChronicleContent:
    """What the scoped operations write, once authorized."""

    async def test_update_applies_every_mutable_field(self, world, seeded):
        updated = await world.service.update_chronicle(
            seeded["alice"].id,
            principal=ALICE,
            key_changes=["a.py: fixed"],
            unfinished_work="docs",
        )
        assert (updated.key_changes, updated.unfinished_work) == (["a.py: fixed"], "docs")

    async def test_a_delete_that_loses_a_race_is_not_found(self, world, seeded):
        async def already_gone(_chronicle_id):
            return False

        world.chronicles.delete = already_gone
        with pytest.raises(ChronicleNotFoundError):
            await world.service.delete_chronicle(seeded["alice"].id, principal=ALICE)

    async def test_a_broker_report_without_history_creates_a_complete_chronicle(self, world):
        alices = world.session("alice", "t1")
        chronicle = await world.service.create_or_update_from_broker(
            alices.id,
            principal=ALICE,
            key_changes=["a.py"],
            unfinished_work="tests",
            duration_seconds=42,
        )
        assert chronicle.status.value == "complete"
        assert (chronicle.key_changes, chronicle.unfinished_work) == (["a.py"], "tests")
        assert (chronicle.duration_seconds, chronicle.owner_id) == (42, "alice")

    async def test_a_local_mount_session_is_chronicled_by_its_directory(self, world, tmp_path):
        from volundr.domain.models import LocalMountSource

        workspace = tmp_path / "my-workspace"
        workspace.mkdir()
        session = Session(
            name="local",
            model="sonnet",
            owner_id="alice",
            tenant_id="t1",
            source=LocalMountSource(local_path=str(workspace)),
        )
        world.sessions._sessions[session.id] = session

        chronicle = await world.service.create_chronicle(session.id, principal=ALICE)

        assert (chronicle.project, chronicle.repo, chronicle.branch) == (
            "my-workspace",
            str(workspace),
            "local",
        )

    async def test_session_timeline_is_the_unscoped_in_process_read(self, world):
        alices = world.session("alice", "t1")
        assert await world.service.session_timeline(alices.id) is None
        await world.service.create_chronicle(alices.id, principal=ALICE)
        timeline = await world.service.session_timeline(alices.id)
        assert timeline is not None and timeline.events == []

    async def test_session_timeline_needs_a_timeline_repository(self):
        dev = _Fixture()
        dev.service = ChronicleService(dev.chronicles, dev.session_service)
        assert await dev.service.session_timeline(uuid4()) is None
        assert await dev.service.get_timeline(uuid4(), principal=None) is None
