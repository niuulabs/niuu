"""Session credentials at launch: grants persistence, re-mint and escalation."""

from __future__ import annotations

import pytest

from niuu.domain.services.token_scope import (
    FORGE_MESH_IDENTITY_SCOPE,
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
    FORGE_SESSION_SECRET_SCOPE,
)
from niuu.forge_mcp.models import ForgeMcpGrant
from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.support.forge_session import OWNER, TENANT, build_forge_app, token_service
from volundr.domain.models import LaunchSpec, Principal, Session, SessionStatus
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor
from volundr.domain.services.forge_session_launch import (
    ForgeMcpGrantEscalationError,
    grants_in,
    launch_id_of,
    launch_spec_grants,
    with_forge_mcp,
    without_forge_mcp,
)
from volundr.domain.services.session import SessionService


class StaticLaunchSpecs:
    def __init__(self, *specs: LaunchSpec, default: LaunchSpec | None = None) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._default = default

    def get(self, name: str) -> LaunchSpec | None:
        return self._specs.get(name)

    def list(self, workload_type: str | None = None) -> list[LaunchSpec]:
        return list(self._specs.values())

    def get_default(self, workload_type: str) -> LaunchSpec | None:
        return self._default


def _spec(name: str, *grants: str) -> LaunchSpec:
    return LaunchSpec(name=name, workload_config={"forge_mcp": {"grants": list(grants)}})


def _session_principal(*scopes: str) -> Principal:
    return Principal(
        user_id=OWNER,
        email="",
        tenant_id=TENANT,
        roles=["volundr:developer"],
        token_use="forge_session",
        scopes=(FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE, *scopes),
        bound_session_id="caller",
    )


class TestMintAtStart:
    async def test_defaults_without_grants(self) -> None:
        forge = build_forge_app()
        session = await forge.session()
        credential = await forge.start(session)
        assert credential.scopes == (
            FORGE_NOTIFY_SCOPE,
            FORGE_SESSION_READ_SCOPE,
            FORGE_SESSION_SECRET_SCOPE,
            FORGE_MESH_IDENTITY_SCOPE,
        )
        stored = await forge.sessions.get(session.id)
        assert stored.workload_config["forge_mcp"] == {
            "grants": [],
            "launch_id": credential.launch_id,
        }
        claims = forge.tokens.verify(credential.token)
        assert claims.session_id == str(session.id) and claims.owner_id == OWNER

    async def test_grants_survive_restart_and_are_reminted(self) -> None:
        forge = build_forge_app()
        session = await forge.session()
        stored = await forge.sessions.get(session.id)
        await forge.sessions.update(
            stored.model_copy(update={"workload_config": {"forge_mcp": {"grants": ["message"]}}})
        )
        first = await forge.start(session)
        await forge.set_status(session.id, SessionStatus.STOPPED)
        second = await forge.start(session)
        assert FORGE_SESSION_MESSAGE_SCOPE in first.scopes
        assert FORGE_SESSION_MESSAGE_SCOPE in second.scopes
        assert first.launch_id != second.launch_id and first.token != second.token
        stored = await forge.sessions.get(session.id)
        assert stored.workload_config["forge_mcp"]["launch_id"] == second.launch_id

    async def test_restart_with_explicit_workload_config_keeps_grants(self) -> None:
        forge = build_forge_app()
        session = await forge.session()
        stored = await forge.sessions.get(session.id)
        await forge.sessions.update(
            stored.model_copy(update={"workload_config": {"forge_mcp": {"grants": ["lifecycle"]}}})
        )
        credential = await forge.start(
            session, workload_config={"forge_mcp": {"grants": ["message"]}}
        )
        assert set(credential.grants) == {ForgeMcpGrant.LIFECYCLE, ForgeMcpGrant.MESSAGE}

    async def test_launch_spec_and_default_grants(self) -> None:
        specs = StaticLaunchSpecs(_spec("supervisor", "lifecycle"), default=_spec("default"))
        forge = build_forge_app(launch_spec_provider=specs, forge_mcp_default_grants=["message"])
        session = await forge.session()
        credential = await forge.start(session, launch_spec="supervisor")
        assert credential.scopes == (
            FORGE_NOTIFY_SCOPE,
            FORGE_SESSION_READ_SCOPE,
            FORGE_SESSION_SECRET_SCOPE,
            FORGE_MESH_IDENTITY_SCOPE,
            FORGE_SESSION_LIFECYCLE_SCOPE,
            FORGE_SESSION_MESSAGE_SCOPE,
        )
        stored = await forge.sessions.get(session.id)
        # the launch spec's grant is persisted; the node default is applied at each mint
        assert stored.workload_config["forge_mcp"]["grants"] == ["lifecycle"]
        await forge.set_status(session.id, SessionStatus.STOPPED)
        restarted = await forge.start(session)  # default spec, no grants of its own
        assert set(restarted.grants) == {ForgeMcpGrant.LIFECYCLE, ForgeMcpGrant.MESSAGE}

    async def test_unknown_launch_spec_falls_to_the_default_spec(self) -> None:
        specs = StaticLaunchSpecs(default=_spec("default", "message"))
        forge = build_forge_app(launch_spec_provider=specs)
        credential = await forge.start(await forge.session(), launch_spec="missing")
        assert credential.grants == (ForgeMcpGrant.MESSAGE,)

    async def test_bad_launch_spec_grant_fails_the_start(self) -> None:
        forge = build_forge_app(launch_spec_provider=StaticLaunchSpecs(_spec("bad", "root")))
        session = await forge.session()
        with pytest.raises(ValueError, match="launch spec 'bad'"):
            await forge.session_service.start_session(session.id, launch_spec="bad")

    async def test_no_mint_when_the_pod_manager_cannot_deliver(self) -> None:
        sessions = InMemorySessionRepository()
        pods = MockPodManager()
        service = SessionService(
            sessions, pods, provisioning_initial_delay=0, forge_session_tokens=token_service()
        )
        session = await sessions.create(Session(name="s", owner_id=OWNER))
        await service.start_session(session.id)
        await service._provisioning_tasks[session.id]
        assert pods.start_calls[-1][1].forge_session is None
        stored = await sessions.get(session.id)
        assert launch_id_of(stored.workload_config)  # the launch is still recorded

    async def test_the_manager_that_runs_the_session_decides(self) -> None:
        """A placement manager whose pool delivers nothing still serves local sessions."""

        class PerSessionDelivery(MockPodManager):
            delivers_forge_session_token = False  # the whole-Forge answer: not every session

            def __init__(self, delivers_to: set[str]) -> None:
                super().__init__()
                self._delivers_to = delivers_to

            def delivers_forge_session_token_for(self, session: Session) -> bool:
                return session.name in self._delivers_to

        sessions = InMemorySessionRepository()
        pods = PerSessionDelivery({"local"})
        service = SessionService(
            sessions, pods, provisioning_initial_delay=0, forge_session_tokens=token_service()
        )
        for name in ("local", "pooled"):
            session = await sessions.create(Session(name=name, owner_id=OWNER))
            await service.start_session(session.id)
            await service._provisioning_tasks[session.id]
        local_spec, pooled_spec = (spec for _, spec in pods.start_calls[-2:])
        assert local_spec.forge_session is not None
        assert pooled_spec.forge_session is None

    async def test_no_mint_without_a_token_service(self) -> None:
        forge = build_forge_app(tokens=False)
        session = await forge.session()
        await forge.session_service.start_session(session.id)
        await forge.session_service._provisioning_tasks[session.id]
        assert forge.pod_manager.start_calls[-1][1].forge_session is None

    async def test_unowned_session_gets_no_credential(self) -> None:
        forge = build_forge_app()
        session = await forge.sessions.create(Session(name="legacy"))
        await forge.session_service.start_session(session.id)
        await forge.session_service._provisioning_tasks[session.id]
        assert forge.pod_manager.start_calls[-1][1].forge_session is None

    async def test_missing_launch_id_is_an_error(self) -> None:
        forge = build_forge_app()
        session = await forge.session()
        with pytest.raises(RuntimeError, match="launch id"):
            forge.session_service._mint_forge_session(session)


class RecordingContributor(SessionContributor):
    def __init__(self) -> None:
        self.contexts: list[SessionContext] = []

    @property
    def name(self) -> str:
        return "recorder"

    async def contribute(self, session: Session, context: SessionContext) -> SessionContribution:
        self.contexts.append(context)
        return SessionContribution()


class TestContributorsDoNotSeeTheBlock:
    async def test_empty_workload_config_stays_empty_for_contributors(self) -> None:
        # SessionMCPContributor falls back to the launch spec's workload config only
        # when the session's is empty; credential bookkeeping must not defeat that.
        recorder = RecordingContributor()
        forge = build_forge_app(contributors=[recorder])
        session = await forge.session()
        await forge.start(session)
        assert recorder.contexts[-1].workload_config == {}
        await forge.set_status(session.id, SessionStatus.STOPPED)
        await forge.start(session)  # the restart reuses the stored config
        assert recorder.contexts[-1].workload_config == {}
        stored = await forge.sessions.get(session.id)
        assert "forge_mcp" in stored.workload_config

    async def test_other_keys_still_reach_contributors(self) -> None:
        recorder = RecordingContributor()
        forge = build_forge_app(contributors=[recorder])
        session = await forge.session()
        await forge.start(session, workload_config={"persona": "reviewer"})
        assert recorder.contexts[-1].workload_config == {"persona": "reviewer"}


class TestEscalation:
    async def test_session_credential_cannot_add_grants_on_start(self) -> None:
        forge = build_forge_app(launch_spec_provider=StaticLaunchSpecs(_spec("big", "message")))
        peer = await forge.session("peer")
        caller = _session_principal(FORGE_SESSION_LIFECYCLE_SCOPE)
        with pytest.raises(ForgeMcpGrantEscalationError, match="message"):
            await forge.session_service.start_session(peer.id, launch_spec="big", principal=caller)
        stored = await forge.sessions.get(peer.id)
        assert stored.status is SessionStatus.CREATED  # nothing changed

    async def test_restarting_a_peer_keeps_its_existing_grants(self) -> None:
        forge = build_forge_app()
        peer = await forge.session("peer")
        stored = await forge.sessions.get(peer.id)
        await forge.sessions.update(
            stored.model_copy(update={"workload_config": {"forge_mcp": {"grants": ["message"]}}})
        )
        caller = _session_principal(FORGE_SESSION_LIFECYCLE_SCOPE)
        await forge.session_service.start_session(peer.id, principal=caller)
        await forge.session_service._provisioning_tasks[peer.id]
        assert forge.pod_manager.start_calls[-1][1].forge_session.grants == (ForgeMcpGrant.MESSAGE,)

    def test_precheck(self) -> None:
        forge = build_forge_app(launch_spec_provider=StaticLaunchSpecs(_spec("big", "message")))
        service = forge.session_service
        caller = _session_principal(FORGE_SESSION_LIFECYCLE_SCOPE)
        service.check_forge_mcp_grants(caller, ["lifecycle"], launch_spec=None)
        service.check_forge_mcp_grants(None, ["message"], launch_spec="big")
        user = Principal(user_id="u", email="", tenant_id="t", roles=[])
        service.check_forge_mcp_grants(user, ["message", "lifecycle"], launch_spec="big")
        with pytest.raises(ForgeMcpGrantEscalationError) as refused:
            service.check_forge_mcp_grants(caller, [], launch_spec="big")
        assert refused.value.session_id is None and refused.value.grants == ("message",)


class TestLaunchHelpers:
    def test_grants_in(self) -> None:
        assert grants_in(None, source="x") == frozenset()
        assert grants_in({"forge_mcp": "junk"}, source="x") == frozenset()
        assert grants_in({"forge_mcp": {"grants": ["message"]}}, source="x") == {
            ForgeMcpGrant.MESSAGE
        }
        with pytest.raises(ValueError, match="must be a list"):
            grants_in({"forge_mcp": {"grants": "message"}}, source="x")
        with pytest.raises(ValueError, match="unknown forge_mcp grant"):
            grants_in({"forge_mcp": {"grants": ["root"]}}, source="x")

    def test_launch_spec_grants(self) -> None:
        assert launch_spec_grants(None) == frozenset()
        assert launch_spec_grants(LaunchSpec(name="plain")) == frozenset()

    def test_with_forge_mcp_replaces_only_its_block(self) -> None:
        updated = with_forge_mcp(
            {"persona": "p", "forge_mcp": {"launch_id": "old"}},
            grants=[ForgeMcpGrant.MESSAGE],
            launch_id="new",
        )
        assert updated == {"persona": "p", "forge_mcp": {"grants": ["message"], "launch_id": "new"}}
        assert launch_id_of(updated) == "new"
        assert launch_id_of({"forge_mcp": {"launch_id": ""}}) is None
        assert without_forge_mcp(updated) == {"persona": "p"}
        assert without_forge_mcp(None) == {}


class RecordingProjection:
    """An ExecutionCredentialProjectionPort that keeps what it would have published."""

    def __init__(self, *, backend: str = "kubernetes") -> None:
        self.backend = backend
        self.tokens: dict = {}
        self.removed: list = []

    def supports(self, runtime_backend: str) -> bool:
        return runtime_backend == self.backend

    async def project(self, *, session_id, token, runtime_backend):
        from volundr.domain.models import PodSpecAdditions
        from volundr.ports.workflow_execution_credentials import ExecutionCredentialProjection

        self.tokens[session_id] = token
        return ExecutionCredentialProjection(
            token_file="/var/run/secrets/niuu-forge-session/token",
            pod_spec=PodSpecAdditions(
                volumes=({"name": "forge-session-x", "secret": {"secretName": "forge-session-x"}},),
                volume_mounts=(
                    {"name": "forge-session-x", "mountPath": "/var/run/secrets/niuu-forge-session"},
                ),
            ),
        )

    async def remove(self, session_id) -> None:
        self.removed.append(session_id)


class TestProjectedCredential:
    """Flux and Kubernetes session pods get their credential through a mounted Secret."""

    async def _started(self, projection: RecordingProjection):
        sessions = InMemorySessionRepository()
        pods = MockPodManager()
        tokens = token_service()
        service = SessionService(
            sessions,
            pods,
            provisioning_initial_delay=0,
            forge_session_tokens=tokens,
            forge_session_projection=projection,
        )
        session = await sessions.create(Session(name="s", owner_id=OWNER, tenant_id=TENANT))
        await service.start_session(session.id)
        await service._provisioning_tasks[session.id]
        return service, sessions, pods, tokens, session

    async def test_the_credential_is_mounted_and_never_in_values(self) -> None:
        import json

        projection = RecordingProjection()
        service, _, pods, tokens, session = await self._started(projection)
        assert service.delivers_forge_session is True
        _, spec = pods.start_calls[-1]
        assert spec.forge_session is None  # nothing for the pod manager to deliver itself
        token = projection.tokens[session.id]
        claims = tokens.verify(token)
        assert claims.session_id == str(session.id) and claims.owner_id == OWNER
        assert spec.values["forgeMcp"] == {
            "tokenFile": "/var/run/secrets/niuu-forge-session/token",
            "grants": [],
        }
        assert token not in json.dumps(spec.values)
        assert {"name": "forge-session-x", "secret": {"secretName": "forge-session-x"}} in (
            spec.pod_spec.volumes
        )
        assert spec.pod_spec.volume_mounts[-1]["mountPath"] == (
            "/var/run/secrets/niuu-forge-session"
        )

    async def test_a_restart_projects_the_new_launchs_credential(self) -> None:
        projection = RecordingProjection()
        service, sessions, pods, tokens, session = await self._started(projection)
        first = projection.tokens[session.id]
        stored = await sessions.get(session.id)
        await sessions.update(stored.model_copy(update={"status": SessionStatus.STOPPED}))
        await service.start_session(session.id)
        await service._provisioning_tasks[session.id]
        second = projection.tokens[session.id]
        assert first != second
        assert tokens.verify(second).launch_id != tokens.verify(first).launch_id

    async def test_cleanup_removes_the_projected_credential(self) -> None:
        projection = RecordingProjection()
        service, _, _, _, session = await self._started(projection)
        await service._run_cleanup(session, None)
        assert projection.removed == [session.id]

    def test_a_projection_for_another_backend_is_refused(self) -> None:
        with pytest.raises(ValueError, match="does not support runtime backend 'kubernetes'"):
            SessionService(
                InMemorySessionRepository(),
                MockPodManager(),
                forge_session_tokens=token_service(),
                forge_session_projection=RecordingProjection(backend="vm"),
            )

    def test_no_delivery_without_tokens_or_a_way_to_deliver(self) -> None:
        assert not SessionService(
            InMemorySessionRepository(),
            MockPodManager(),
            forge_session_projection=RecordingProjection(),
        ).delivers_forge_session
        assert not SessionService(
            InMemorySessionRepository(), MockPodManager(), forge_session_tokens=token_service()
        ).delivers_forge_session


class TestPresentedFileUploadsNeedTheCredential:
    def _settings(self, identity: str):
        from volundr.config import Settings

        return Settings(
            identity={"adapter": identity},
            presented_files={"enabled": True, "max_total_bytes": 10},
        )

    def test_an_authenticating_forge_refuses_brokers_without_it(self) -> None:
        from volundr.composition_builders import require_presented_file_uploads

        settings = self._settings("volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter")
        with pytest.raises(RuntimeError, match="projection_adapter"):
            require_presented_file_uploads(settings, delivers_session_credential=False)
        require_presented_file_uploads(settings, delivers_session_credential=True)

    def test_an_allow_all_forge_admits_the_brokers_own_call(self) -> None:
        from volundr.composition_builders import require_presented_file_uploads

        settings = self._settings("volundr.adapters.outbound.identity.AllowAllIdentityAdapter")
        require_presented_file_uploads(settings, delivers_session_credential=False)
