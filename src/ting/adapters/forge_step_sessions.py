"""The harness sessions that run a workflow run's steps, on Forge.

``ForgeStepSessions`` implements the step engine's ``StepSessionPort`` over the
owner's Forge connection. A run has one session per stage member, kept alive for the
whole run and reused for the member's later activations, rework included.

Finding a session again: Forge is the record. A session is named
``wf-{run slug}-{member}`` (shortened with a digest of the run and member when the
name would pass Forge's 63 character limit), is tagged ``workflow:{run slug}`` and
carries ``workflow_run_id`` / ``workflow_member`` metadata. A member whose session had to
be replaced (it failed, or a person reassigned the step) gets the next *epoch* of the
name, ``...-r2``, ``...-r3``: the highest epoch that exists is the member's session, and
the replaced ones stay behind, stopped. ``ensure_session`` and
``stop_run_sessions`` look the run's sessions up from Forge by that name and tag, so
a Ting restart, or a crash between creating a session and recording it, can neither
lose a session nor create a second one. Ting keeps no mapping of its own that could
disagree with Forge. (The ledger also records each step's session id, which is what
fences a close; it is not consulted to find sessions.)
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
from ting.domain.run_members import RunMemberError, member_model, member_values
from ting.domain.services.run_thread import (
    run_label,
    run_objective,
    run_project,
)
from ting.domain.step_instructions import render_step_instructions
from ting.ports.saga_repository import SagaRepository
from ting.ports.volundr import (
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
#: Statuses of a session that is on its way up.
_STARTING_STATUSES = frozenset({"created", "starting", "provisioning"})


class StepSessionError(RuntimeError):
    """A run's session could not be found, created, started or stopped; the message says why."""


def step_session_tag(run_slug: str) -> str:
    """The tracker tag every session of a run carries."""
    return f"workflow:{run_slug}"


def step_session_name(
    run_slug: str, member: str, run_id: UUID, *, prefix: str = "wf", epoch: int = 1
) -> str:
    """The deterministic name of a run's session for one stage member.

    ``epoch`` 1 is the member's first session; a replacement is epoch 2, 3, ... and
    carries ``-r{epoch}`` so the name stays unique within the project.
    """
    suffix = "" if epoch == 1 else f"-r{epoch}"
    name = _label(f"{prefix}-{run_slug}-{member}") + suffix
    if len(name) <= FORGE_SESSION_NAME_MAX:
        return name
    seed = f"{run_id}:{member}" if epoch == 1 else f"{run_id}:{member}:{epoch}"
    digest = hashlib.sha256(seed.encode()).hexdigest()[:_NAME_DIGEST_CHARS]
    member_part = _label(member)[:_NAME_MEMBER_CHARS].rstrip("-")
    slug_room = (
        FORGE_SESSION_NAME_MAX - len(prefix) - len(member_part) - len(digest) - len(suffix) - 3
    )
    slug_part = _label(run_slug)[:slug_room].rstrip("-")
    return f"{prefix}-{slug_part}-{member_part}-{digest}{suffix}"


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
class _Current:
    """A member's session and which epoch of the name it carries."""

    session: VolundrSession
    epoch: int


class ForgeStepSessions(StepSessionPort):
    """Step-host sessions on the owner's Forge, found again by name and tag."""

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
        #: One create at a time per run, so the run's first session is the root every
        #: later one nests under, even when members start in parallel.
        self._run_locks: dict[UUID, asyncio.Lock] = {}

    async def ensure_session(self, run_id: UUID, member: str, persona_ref: PersonaRef) -> str:
        campaign, forge = await self._run(run_id)
        async with self._locks.setdefault((run_id, member), asyncio.Lock()):
            current = await self._current(forge, campaign, member)
            if current is not None:
                self._require_tag(current.session, campaign, member)
                return await self._reuse(forge, current.session)
            return await self._create(forge, campaign, member, persona_ref, epoch=1)

    async def replace_session(
        self,
        run_id: UUID,
        member: str,
        persona_ref: PersonaRef,
        replaces: str | None,
    ) -> str:
        campaign, forge = await self._run(run_id)
        async with self._locks.setdefault((run_id, member), asyncio.Lock()):
            current = await self._current(forge, campaign, member)
            if current is None:
                return await self._create(forge, campaign, member, persona_ref, epoch=1)
            self._require_tag(current.session, campaign, member)
            if current.session.id != replaces and not _is_down(current.session):
                # Another reassignment already put a live session in its place.
                return current.session.id
            await self._stop_replaced(forge, current.session, campaign)
            return await self._create(forge, campaign, member, persona_ref, epoch=current.epoch + 1)

    async def session_failure(self, run_id: UUID, session_id: str) -> str:
        _, forge = await self._run(run_id)
        session = await forge.get_session(session_id)
        return session.error if session is not None else ""

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
        sessions = [s for s in await forge.list_sessions() if s.tracker_issue_id == tag]
        failures: list[str] = []
        for session in sessions:
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

    async def _current(
        self, forge: VolundrPort, campaign: WorkflowCampaign, member: str
    ) -> _Current | None:
        """The member's session: the one of the highest epoch that exists on Forge.

        Epochs are tried up to one more than the number of sessions Forge lists, which is
        the most a chain of replacements can reach.
        """
        sessions = await forge.list_sessions()
        by_name = {session.name: session for session in sessions}
        current: _Current | None = None
        for epoch in range(1, len(sessions) + 2):
            name = step_session_name(
                campaign.slug, member, campaign.id, prefix=self._prefix, epoch=epoch
            )
            if name in by_name:
                current = _Current(by_name[name], epoch)
        return current

    @staticmethod
    def _require_tag(session: VolundrSession, campaign: WorkflowCampaign, member: str) -> None:
        tag = step_session_tag(campaign.slug)
        if session.tracker_issue_id != tag:
            raise StepSessionError(
                f"Forge already has a session named {session.name!r} that is not run "
                f"{campaign.slug}'s session for {member!r} (it is tagged "
                f"{session.tracker_issue_id!r}, not {tag!r}); rename or stop it"
            )

    async def _create(
        self,
        forge: VolundrPort,
        campaign: WorkflowCampaign,
        member: str,
        persona_ref: PersonaRef,
        *,
        epoch: int,
    ) -> str:
        name = step_session_name(
            campaign.slug, member, campaign.id, prefix=self._prefix, epoch=epoch
        )
        document = self._persona_document(campaign, member, persona_ref)
        async with self._run_locks.setdefault(campaign.id, asyncio.Lock()):
            created = await self._create_in_run(
                forge, campaign, member, name=name, document=document
            )
        logger.info(
            "Created step session %s (%s) for member %s of run %s",
            created.id,
            name,
            member,
            campaign.slug,
        )
        return created.id

    async def _create_in_run(
        self,
        forge: VolundrPort,
        campaign: WorkflowCampaign,
        member: str,
        *,
        name: str,
        document: dict[str, Any],
    ) -> VolundrSession:
        return await forge.create_step_session(
            StepSessionRequest(
                name=name,
                model=self._model_of(campaign, member),
                persona_definition=document,
                tracker_issue_id=step_session_tag(campaign.slug),
                run_id=str(campaign.id),
                member=member,
                repo=str(campaign.metadata.get("repo") or ""),
                branch=str(campaign.metadata.get("branch") or ""),
                base_branch=str(campaign.metadata.get("base_branch") or ""),
                system_prompt=self._extra_prompt_of(campaign, member),
                membership=await self._membership(forge, campaign, member),
            )
        )

    async def _membership(
        self, forge: VolundrPort, campaign: WorkflowCampaign, member: str
    ) -> SessionMembership | None:
        """The run's project, with the member as its role, nested under the run's room.

        The run's room is a session of its own (no agent), the root of the run's thread:
        every member is nested under it, so Forge shows the run as one session with its
        workers. The caller holds the run's lock, so the run has one room.
        """
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
        room = await self._run_room(forge, campaign, member, project.id)
        return SessionMembership(
            project_id=project.id,
            role=member,
            objective=run_objective(campaign),
            labels=(run_label(campaign),),
            parent_instance_id=ref.instance_id,
            parent_session_id=room.id,
        )

    async def _run_room(
        self, forge: VolundrPort, campaign: WorkflowCampaign, member: str, project_id: str
    ) -> VolundrSession:
        """The run's room session, created on the run's first member session."""
        name = run_room_name(campaign.slug, campaign.id, prefix=self._prefix)
        tag = step_session_tag(campaign.slug)
        for session in await forge.list_sessions():
            if session.name != name:
                continue
            if session.tracker_issue_id != tag:
                raise StepSessionError(
                    f"Forge already has a session named {name!r} that is not run "
                    f"{campaign.slug}'s room (it is tagged {session.tracker_issue_id!r}); "
                    "rename or stop it"
                )
            return session
        room = await forge.create_run_room(
            RunRoomRequest(
                name=name,
                model=self._model_of(campaign, member),
                tracker_issue_id=tag,
                run_id=str(campaign.id),
                membership=SessionMembership(
                    project_id=project_id,
                    role=RUN_ROOM_ROLE,
                    objective=run_objective(campaign),
                    labels=(run_label(campaign),),
                ),
                repo=str(campaign.metadata.get("repo") or ""),
                branch=str(campaign.metadata.get("branch") or ""),
                base_branch=str(campaign.metadata.get("base_branch") or ""),
            )
        )
        logger.info("Created the room %s (%s) of run %s", room.id, name, campaign.slug)
        return room

    async def _stop_replaced(
        self, forge: VolundrPort, session: VolundrSession, campaign: WorkflowCampaign
    ) -> None:
        if _is_down(session):
            return
        try:
            await forge.stop_session(session.id)
        except Exception as exc:
            raise StepSessionError(
                f"session {session.id} ({session.name}) of run {campaign.slug} is being "
                f"replaced but could not be stopped: {exc}"
            ) from exc
        logger.warning(
            "Stopped step session %s (%s), which is being replaced", session.id, session.name
        )

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
        try:
            return member_model(
                campaign.workflow_snapshot,
                member,
                launch_model=str(campaign.metadata.get("model") or ""),
                default_model=self._default_model,
            )
        except RunMemberError as exc:
            raise StepSessionError(str(exc)) from exc

    @staticmethod
    def _extra_prompt_of(campaign: WorkflowCampaign, member: str) -> str:
        """The member's ``systemPromptExtra``; one per member, as its session is one."""
        snapshot = campaign.workflow_snapshot
        # Both spellings, as the workflow snapshot reads them.
        extras = member_values(snapshot, member, "systemPromptExtra") | member_values(
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
