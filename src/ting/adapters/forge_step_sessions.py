"""The harness sessions that run a workflow run's steps, on Forge.

``ForgeStepSessions`` implements the step engine's ``StepSessionPort`` over the
owner's Forge connection. A run is **one unit**: Forge runs the run's root and every
stage member's session in one pod, VM or VM slot, all started together. ``launch_run``
creates the root (the run's room, a Skuld with no agent) held as the unit root, then each
member session hosted by it, then starts the root, which starts the unit with them all. A
run's members are fixed when its unit starts; Forge refuses a member after that (409), and
this adapter raises that refusal rather than creating a session outside the unit.

Finding a session again: Forge is the record. The root is named ``wf-{run slug}`` and a
member ``wf-{run slug}-{member}`` (shortened with a digest of the run and member when the
name would pass Forge's 63 character limit); each is tagged ``workflow:{run slug}`` and
carries ``workflow_run_id`` / ``workflow_member`` metadata. ``launch_run``,
``ensure_session`` and ``stop_run_sessions`` look the run's sessions up from Forge by that
name and tag, so a Ting restart, or a crash between creating a session and starting the
unit, can neither lose a session nor create a second one. Ting keeps no mapping of its own
that could disagree with Forge. (The ledger also records each step's session id, which is
what fences a close; it is not consulted to find sessions.)

A member keeps its session for the whole run. Reassigning a step restarts the member's
harness *in place* (``replace_session``): Forge stops the member's Skuld and harness and
starts them again under a new launch of the same session, because a running unit cannot
take a new member.

``publish_offer`` hands a signed offer to the root's Skuld, which publishes it on the run's
mesh (``POST /sessions/{root}/room/offer``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from ting.domain.models import WorkflowCampaign
from ting.domain.services.run_thread import (
    RunProject,
    run_label,
    run_objective,
    run_project,
)
from ting.domain.step_instructions import render_step_instructions
from ting.ports.saga_repository import SagaRepository
from ting.ports.volundr import (
    ForgeProjectRef,
    RunRoomRequest,
    SessionMembership,
    StepSessionRequest,
    VolundrFactory,
    VolundrPort,
    VolundrSession,
)
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_steps import (
    PersonaRef,
    SessionLiveness,
    SessionNotReadyError,
    StepActivation,
    StepObservation,
    StepSessionPort,
)

logger = logging.getLogger(__name__)

#: Forge names sessions with an RFC 1123 label, which DNS limits to 63 characters.
FORGE_SESSION_NAME_MAX = 63
#: Short digest appended to a name that had to be shortened, so two long names stay distinct.
_NAME_DIGEST_CHARS = 8
#: Of a shortened name, how much the member keeps; the run slug gets what is left of the limit.
_NAME_MEMBER_CHARS = 20

_FAILED_STATUSES = frozenset({"failed"})
#: What Forge answers for a session whose runtime is up but whose broker does not serve yet:
#: its proxy could not reach the endpoint (bad gateway, unavailable, gateway timeout).
_NOT_READY_STATUSES = frozenset({502, 503, 504})
_STOPPED_STATUSES = frozenset({"stopped"})
#: Statuses of a session that is not running and will not come up by itself.
_ENDED_STATUSES = frozenset({"stopped", "stopping", "archived"})
#: Statuses of a unit root that Forge holds, not started: its members can still be attached.
#: Once it is anything else the unit has started and its members are fixed.
_HELD_STATUSES = frozenset({"created"})
#: Forge refuses a member the unit cannot take (it started, another owner's or another run's).
_CONFLICT = 409
#: Statuses of a session that is on its way up.
_STARTING_STATUSES = frozenset({"created", "starting", "provisioning"})


class StepSessionError(RuntimeError):
    """A run's session could not be found, created, started or stopped; the message says why."""


def step_session_tag(run_slug: str) -> str:
    """The tracker tag every session of a run carries."""
    return f"workflow:{run_slug}"


def step_session_name(run_slug: str, member: str, run_id: UUID, *, prefix: str = "wf") -> str:
    """The deterministic name of a run's session for one stage member."""
    name = _label(f"{prefix}-{run_slug}-{member}")
    if len(name) <= FORGE_SESSION_NAME_MAX:
        return name
    digest = hashlib.sha256(f"{run_id}:{member}".encode()).hexdigest()[:_NAME_DIGEST_CHARS]
    member_part = _label(member)[:_NAME_MEMBER_CHARS].rstrip("-")
    slug_room = FORGE_SESSION_NAME_MAX - len(prefix) - len(member_part) - len(digest) - 3
    slug_part = _label(run_slug)[:slug_room].rstrip("-")
    return f"{prefix}-{slug_part}-{member_part}-{digest}"


#: The run's room session's role in the run's project: it coordinates the run's thread.
RUN_ROOM_ROLE = "coordinator"


def run_room_name(run_slug: str, run_id: UUID, *, prefix: str = "wf") -> str:
    """The name of a run's room session: the run's own, ``wf-{run slug}``."""
    name = _label(f"{prefix}-{run_slug}")
    if len(name) <= FORGE_SESSION_NAME_MAX:
        return name
    digest = hashlib.sha256(f"{run_id}:room".encode()).hexdigest()[:_NAME_DIGEST_CHARS]
    slug_room = FORGE_SESSION_NAME_MAX - len(prefix) - len(digest) - 2
    return f"{prefix}-{_label(run_slug)[:slug_room].rstrip('-')}-{digest}"


def _label(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


@dataclass(frozen=True)
class RunUnit:
    """A launched run's unit: the root session and each stage member's session by member."""

    root_id: str
    members: Mapping[str, str]


@dataclass(frozen=True)
class _MemberPlan:
    """What one member's session is created with, checked before anything is created."""

    member: str
    name: str
    model: str
    document: dict[str, Any]
    extra_prompt: str


class ForgeStepSessions(StepSessionPort):
    """Step-host sessions on the owner's Forge: one unit per run, found again by name and tag."""

    def __init__(
        self,
        *,
        volundr_factory: VolundrFactory,
        campaigns: WorkflowCampaignRepository,
        sagas: SagaRepository,
        default_model: str = "",
        name_prefix: str = "wf",
    ) -> None:
        self._factory = volundr_factory
        self._campaigns = campaigns
        self._sagas = sagas
        self._default_model = default_model
        self._prefix = name_prefix
        self._locks: dict[tuple[UUID, str], asyncio.Lock] = {}
        #: One launch at a time per run, so two launches of a run cannot each create a root.
        self._run_locks: dict[UUID, asyncio.Lock] = {}

    async def launch_run(self, run_id: UUID, members: Mapping[str, PersonaRef]) -> RunUnit:
        """Create the run's root and every member hosted by it, then start the unit.

        The root is the run's room: a Skuld with no agent, created held as the unit root.
        Each member is created hosted by it, in the order given (Forge gives each member
        its port by that order). Starting the root starts the unit with all of them at once.
        Calling it again finds what Forge already has by name and tag, so a launch that
        stopped half way continues, and a launched run is returned as it is. A member that
        is missing from a unit that already started cannot be added; that raises.
        """
        campaign, forge = await self._run(run_id)
        if not members:
            raise StepSessionError(f"run {campaign.slug} has no stage members to launch")
        # Everything a member's session needs is checked before the first create, so a run
        # with a bad model or persona pin creates nothing.
        plans = [self._plan(campaign, member, ref) for member, ref in members.items()]
        async with self._run_locks.setdefault(run_id, asyncio.Lock()):
            listed = {session.name: session for session in await forge.list_sessions()}
            root = self._existing_root(listed, campaign)
            existing = {plan.member: listed[plan.name] for plan in plans if plan.name in listed}
            for member, session in existing.items():
                self._require_tag(session, campaign, member)
            missing = [plan for plan in plans if plan.member not in existing]
            started = root is not None and not _is_held(root)
            if root is not None and started and missing:
                names = ", ".join(repr(plan.member) for plan in missing)
                raise StepSessionError(
                    f"run {campaign.slug}'s unit {root.id} already started without member(s) "
                    f"{names}; a run's members are fixed when its unit starts, so the run "
                    "cannot take them now"
                )
            project = await self._project_ref(forge, campaign)
            if root is None:
                root = await self._create_root(forge, campaign, plans[0].model, project)
            session_ids = {member: session.id for member, session in existing.items()}
            for plan in missing:
                session_ids[plan.member] = await self._create_member(
                    forge, campaign, plan, members[plan.member], root, project
                )
            if not started:
                await self._start_unit(forge, campaign, root)
        logger.info(
            "Launched run %s as one unit: root %s with members %s",
            campaign.slug,
            root.id,
            ", ".join(sorted(session_ids)),
        )
        return RunUnit(
            root_id=root.id, members={plan.member: session_ids[plan.member] for plan in plans}
        )

    async def ensure_session(self, run_id: UUID, member: str, persona_ref: PersonaRef) -> str:
        campaign, forge = await self._run(run_id)
        async with self._locks.setdefault((run_id, member), asyncio.Lock()):
            session = await self._member_session(forge, campaign, member)
            return await self._reuse(forge, session)

    async def replace_session(
        self,
        run_id: UUID,
        member: str,
        persona_ref: PersonaRef,
        replaces: str | None,
    ) -> str:
        """Restart the member's harness in place; its session id does not change.

        A running unit cannot take a new member, so a reassigned or failed step is run by
        the same session under a new launch: Forge stops the member's Skuld and harness
        (not the unit) and starts them again. Returns the member's session id.
        """
        campaign, forge = await self._run(run_id)
        async with self._locks.setdefault((run_id, member), asyncio.Lock()):
            session = await self._member_session(forge, campaign, member)
            if replaces is not None and session.id != replaces:
                raise StepSessionError(
                    f"run {campaign.slug}: asked to restart session {replaces} for member "
                    f"{member!r}, but the member's session in the unit is {session.id}"
                )
            await self._stop_member(forge, session, campaign)
            await self._start_member(forge, session, campaign)
            return session.id

    async def publish_offer(self, run_id: UUID, offer: Mapping[str, Any]) -> None:
        """Hand a signed offer to the run's root, whose Skuld publishes it on the run's mesh.

        One call per offer. Forge's answer is raised as it is: a root whose broker is not
        up yet, or an offer its Skuld refuses, is the caller's to hold back and retry.
        """
        campaign, forge = await self._run(run_id)
        listed = {session.name: session for session in await forge.list_sessions()}
        root = self._existing_root(listed, campaign)
        if root is None:
            raise StepSessionError(
                f"run {campaign.slug} has no root session to publish an offer on; "
                "launch the run first"
            )
        await forge.post_room_offer(root.id, dict(offer))

    async def session_liveness(self, run_id: UUID, session_id: str) -> SessionLiveness:
        _, forge = await self._run(run_id)
        return _liveness_of(await forge.get_session(session_id))

    async def observe(self, run_id: UUID, session_id: str, activation_id: UUID) -> StepObservation:
        _, forge = await self._run(run_id)
        liveness = _liveness_of(await forge.get_session(session_id))
        if liveness is not SessionLiveness.RUNNING:
            return StepObservation(liveness)
        return StepObservation(
            liveness, await forge.get_workflow_step(session_id, str(activation_id))
        )

    async def deliver(self, activation: StepActivation) -> None:
        _, forge = await self._run(activation.run_id)
        try:
            await forge.deliver_workflow_step(activation.session_id, activation_body(activation))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _NOT_READY_STATUSES:
                raise SessionNotReadyError(str(exc)) from exc
            raise
        except httpx.TransportError as exc:
            raise SessionNotReadyError(f"{type(exc).__name__}: {exc}") from exc

    async def stop_run_sessions(self, run_id: UUID) -> None:
        campaign, forge = await self._run(run_id)
        tag = step_session_tag(campaign.slug)
        root_name = run_room_name(campaign.slug, campaign.id, prefix=self._prefix)
        sessions = [s for s in await forge.list_sessions() if s.tracker_issue_id == tag]
        # Members first, the root last: stopping the root stops the unit, and a member
        # stopped after that is a refusal for nothing.
        ordered = sorted(sessions, key=lambda session: session.name == root_name)
        failures: list[str] = []
        for session in ordered:
            if _is_held(session) or session.status.strip().lower() in _ENDED_STATUSES:
                # Never started, or already stopped: there is no runtime to stop.
                continue
            try:
                await forge.stop_session(session.id)
            except Exception as exc:
                logger.error(
                    "Could not stop step session %s of run %s",
                    session.id,
                    campaign.slug,
                    exc_info=True,
                )
                failures.append(f"{session.id}: {exc}")
        if failures:
            raise StepSessionError(
                f"run {campaign.slug} ended but its session(s) could not be stopped: "
                + "; ".join(failures)
            )

    async def _run(self, run_id: UUID) -> tuple[WorkflowCampaign, VolundrPort]:
        campaign = await self._campaigns.get_campaign(run_id)
        if campaign is None:
            raise StepSessionError(f"workflow run {run_id} has no campaign")
        return campaign, await self._forge(campaign)

    async def _forge(self, campaign: WorkflowCampaign) -> VolundrPort:
        if campaign.connection_id:
            forge = await self._factory.for_connection(campaign.owner_id, campaign.connection_id)
            if forge is None:
                raise StepSessionError(
                    f"run {campaign.slug} runs on Forge connection {campaign.connection_id!r}, "
                    "which no longer resolves; check the connection's credential in Guild"
                )
            return forge
        forge = await self._factory.primary_for_owner(campaign.owner_id)
        if forge is None:
            raise StepSessionError(
                f"run {campaign.slug} names no Forge connection and its owner has none to use"
            )
        return forge

    @staticmethod
    def _require_tag(session: VolundrSession, campaign: WorkflowCampaign, member: str) -> None:
        tag = step_session_tag(campaign.slug)
        if session.tracker_issue_id != tag:
            raise StepSessionError(
                f"Forge already has a session named {session.name!r} that is not run "
                f"{campaign.slug}'s session for {member!r} (it is tagged "
                f"{session.tracker_issue_id!r}, not {tag!r}); rename or stop it"
            )

    def _existing_root(
        self, listed: Mapping[str, VolundrSession], campaign: WorkflowCampaign
    ) -> VolundrSession | None:
        """The run's root as Forge has it, or ``None`` when the run was not launched."""
        name = run_room_name(campaign.slug, campaign.id, prefix=self._prefix)
        root = listed.get(name)
        if root is None:
            return None
        if root.tracker_issue_id != step_session_tag(campaign.slug):
            raise StepSessionError(
                f"Forge already has a session named {name!r} that is not run "
                f"{campaign.slug}'s root (it is tagged {root.tracker_issue_id!r}); "
                "rename or stop it"
            )
        return root

    async def _member_session(
        self, forge: VolundrPort, campaign: WorkflowCampaign, member: str
    ) -> VolundrSession:
        """The member's session in the run's launched unit; it is never created here."""
        listed = {session.name: session for session in await forge.list_sessions()}
        if self._existing_root(listed, campaign) is None:
            raise StepSessionError(
                f"run {campaign.slug} has no unit on Forge: launch the run (launch_run) so "
                "its root and every member are created and started together"
            )
        name = step_session_name(campaign.slug, member, campaign.id, prefix=self._prefix)
        session = listed.get(name)
        if session is None:
            raise StepSessionError(
                f"run {campaign.slug}'s unit has no session for member {member!r}: a run's "
                "members are fixed when its unit starts, so the member must be named at launch"
            )
        self._require_tag(session, campaign, member)
        return session

    def _plan(
        self, campaign: WorkflowCampaign, member: str, persona_ref: PersonaRef
    ) -> _MemberPlan:
        document = self._persona_document(campaign, member, persona_ref)
        return _MemberPlan(
            member=member,
            name=step_session_name(campaign.slug, member, campaign.id, prefix=self._prefix),
            model=self._model_of(campaign, member),
            document=document,
            extra_prompt=self._extra_prompt_of(campaign, member),
        )

    async def _project_ref(
        self, forge: VolundrPort, campaign: WorkflowCampaign
    ) -> tuple[RunProject, ForgeProjectRef] | None:
        """The run's project on Forge, created under its id for a ticket's run; ``None`` if none."""
        project = await run_project(campaign, self._sagas)
        if project is None:
            return None
        if project.name is not None:
            # A ticket's project is created on Forge under the id Work uses for it.
            ref = await forge.ensure_project(project.id, project.name)
        else:
            ref = await forge.get_project(project.id)
        if ref is None:
            raise StepSessionError(
                f"run {campaign.slug} belongs to project {project.id}, which Forge "
                f"{forge.name} does not have; create it there or launch the run elsewhere"
            )
        return project, ref

    async def _create_root(
        self,
        forge: VolundrPort,
        campaign: WorkflowCampaign,
        model: str,
        project: tuple[RunProject, ForgeProjectRef] | None,
    ) -> VolundrSession:
        """The run's root: its room, a Skuld with no agent, held as the unit root."""
        name = run_room_name(campaign.slug, campaign.id, prefix=self._prefix)
        root = await forge.create_run_room(
            RunRoomRequest(
                name=name,
                model=model,
                tracker_issue_id=step_session_tag(campaign.slug),
                run_id=str(campaign.id),
                membership=(
                    SessionMembership(
                        project_id=project[0].id,
                        role=RUN_ROOM_ROLE,
                        objective=run_objective(campaign),
                        labels=(run_label(campaign),),
                    )
                    if project is not None
                    else None
                ),
                repo=str(campaign.metadata.get("repo") or ""),
                branch=str(campaign.metadata.get("branch") or ""),
                base_branch=str(campaign.metadata.get("base_branch") or ""),
                hosts_members=True,
            )
        )
        logger.info("Created the root %s (%s) of run %s", root.id, name, campaign.slug)
        return root

    async def _create_member(
        self,
        forge: VolundrPort,
        campaign: WorkflowCampaign,
        plan: _MemberPlan,
        persona_ref: PersonaRef,
        root: VolundrSession,
        project: tuple[RunProject, ForgeProjectRef] | None,
    ) -> str:
        """A member's session, hosted by the root and held until the unit starts."""
        membership = (
            SessionMembership(
                project_id=project[0].id,
                role=plan.member,
                objective=run_objective(campaign),
                labels=(run_label(campaign),),
                parent_instance_id=project[1].instance_id,
                parent_session_id=root.id,
            )
            if project is not None
            else None
        )
        try:
            created = await forge.create_step_session(
                StepSessionRequest(
                    name=plan.name,
                    model=plan.model,
                    persona_definition=plan.document,
                    tracker_issue_id=step_session_tag(campaign.slug),
                    run_id=str(campaign.id),
                    member=plan.member,
                    repo=str(campaign.metadata.get("repo") or ""),
                    branch=str(campaign.metadata.get("branch") or ""),
                    base_branch=str(campaign.metadata.get("base_branch") or ""),
                    system_prompt=plan.extra_prompt,
                    membership=membership,
                    hosted_by=root.id,
                )
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != _CONFLICT:
                raise
            raise StepSessionError(
                f"Forge refused to host member {plan.member!r} in run {campaign.slug}'s unit "
                f"{root.id}: {exc}"
            ) from exc
        logger.info(
            "Created step session %s (%s) for member %s, hosted by %s, of run %s",
            created.id,
            plan.name,
            plan.member,
            root.id,
            campaign.slug,
        )
        return created.id

    async def _start_unit(
        self, forge: VolundrPort, campaign: WorkflowCampaign, root: VolundrSession
    ) -> None:
        """Start the root: Forge starts the unit with every member at once."""
        try:
            await forge.start_session(root.id)
        except Exception as exc:
            raise StepSessionError(
                f"Forge could not start run {campaign.slug}'s unit (root {root.id}): {exc}"
            ) from exc

    async def _stop_member(
        self, forge: VolundrPort, session: VolundrSession, campaign: WorkflowCampaign
    ) -> None:
        """Stop a member's Skuld and harness, not the unit; nothing to stop when it is down."""
        if _is_down(session):
            return
        try:
            await forge.stop_session(session.id)
        except Exception as exc:
            raise StepSessionError(
                f"session {session.id} ({session.name}) of run {campaign.slug} is being "
                f"restarted but could not be stopped: {exc}"
            ) from exc
        logger.warning(
            "Stopped step session %s (%s) to restart it in place", session.id, session.name
        )

    async def _start_member(
        self, forge: VolundrPort, session: VolundrSession, campaign: WorkflowCampaign
    ) -> None:
        """Start a member again under a new launch; the unit restarts its harness in place."""
        try:
            await forge.start_session(session.id)
        except Exception as exc:
            raise StepSessionError(
                f"session {session.id} ({session.name}) of run {campaign.slug} could not be "
                f"restarted in place: {exc}"
            ) from exc

    async def _reuse(self, forge: VolundrPort, session: VolundrSession) -> str:
        status = session.status.strip().lower()
        if status in _FAILED_STATUSES:
            raise StepSessionError(
                f"step session {session.id} ({session.name}) has failed; the run cannot "
                "continue on it"
            )
        if status in _STOPPED_STATUSES:
            logger.info("Starting stopped step session %s", session.id)
            await forge.start_session(session.id)
        return session.id

    def _model_of(self, campaign: WorkflowCampaign, member: str) -> str:
        models = _member_models(campaign.workflow_snapshot, member)
        if len(models) > 1:
            raise StepSessionError(
                f"member {member!r} is given different models by different stages "
                f"({', '.join(sorted(models))}) but a run has one session per member; "
                "give the member one model"
            )
        model = (
            next(iter(models), "")
            or str(campaign.metadata.get("model") or "")
            or self._default_model
        )
        if not model:
            raise StepSessionError(
                f"no model for member {member!r}: set its model in the workflow, launch "
                "with a model, or set dispatch.default_model"
            )
        return model

    @staticmethod
    def _extra_prompt_of(campaign: WorkflowCampaign, member: str) -> str:
        """The member's ``systemPromptExtra``; one per member, as its session is one."""
        snapshot = campaign.workflow_snapshot
        # Both spellings, as the workflow snapshot reads them.
        extras = _member_values(snapshot, member, "systemPromptExtra") | _member_values(
            snapshot, member, "system_prompt_extra"
        )
        if len(extras) > 1:
            raise StepSessionError(
                f"member {member!r} is given different systemPromptExtra by different stages "
                "but a run has one session per member; give the member one extra prompt"
            )
        return next(iter(extras), "")

    @staticmethod
    def _persona_document(
        campaign: WorkflowCampaign, member: str, persona_ref: PersonaRef
    ) -> dict[str, Any]:
        documents = campaign.workflow_snapshot.get("persona_definitions")
        document = documents.get(member) if isinstance(documents, Mapping) else None
        if not isinstance(document, Mapping):
            raise StepSessionError(
                f"run {campaign.slug} pins no persona document for member {member!r}"
            )
        if str(document.get("id") or "") != persona_ref.id:
            raise StepSessionError(
                f"run {campaign.slug}: the pinned document for member {member!r} is persona "
                f"{document.get('id')!r}, not the pinned {persona_ref.id!r}"
            )
        return dict(document)


def _is_held(session: VolundrSession) -> bool:
    return session.status.strip().lower() in _HELD_STATUSES


def _is_down(session: VolundrSession) -> bool:
    return session.status.strip().lower() in (_FAILED_STATUSES | _ENDED_STATUSES)


def _liveness_of(session: VolundrSession | None) -> SessionLiveness:
    if session is None:
        return SessionLiveness.GONE
    status = session.status.strip().lower()
    if status in _FAILED_STATUSES:
        return SessionLiveness.FAILED
    if status in _ENDED_STATUSES:
        return SessionLiveness.STOPPED
    if status in _STARTING_STATUSES:
        return SessionLiveness.STARTING
    return SessionLiveness.RUNNING


def activation_body(activation: StepActivation) -> dict[str, Any]:
    """The JSON body of a step activation, exactly as Forge's step route takes it."""
    return {
        "activation_id": str(activation.activation_id),
        "run_id": str(activation.run_id),
        "attempt": activation.attempt,
        "generation": activation.generation,
        "node_id": activation.node_id,
        "node_label": activation.node_label,
        "member": activation.member,
        "valid_endings": list(activation.valid_endings),
        "valid_verdicts": list(activation.valid_verdicts),
        "outcome_schema": dict(activation.outcome_schema) if activation.outcome_schema else None,
        "deadline_at": activation.deadline_at.isoformat() if activation.deadline_at else None,
        "instructions": render_step_instructions(activation),
    }


def _member_models(snapshot: Mapping[str, Any], member: str) -> set[str]:
    return _member_values(snapshot, member, "model")


def _member_values(snapshot: Mapping[str, Any], member: str, key: str) -> set[str]:
    """The distinct non-empty values of ``key`` the member's stages give it."""
    graph = snapshot.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    values: set[str] = set()
    for node in nodes or []:
        if not isinstance(node, Mapping) or node.get("kind") != "stage":
            continue
        for raw in node.get("stageMembers") or []:
            if isinstance(raw, Mapping) and str(raw.get("personaId") or "") == member:
                value = str(raw.get(key) or "").strip()
                if value:
                    values.add(value)
    return values
