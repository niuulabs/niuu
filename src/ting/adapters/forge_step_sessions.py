"""The harness sessions that run a workflow run's steps, on Forge.

``ForgeStepSessions`` implements the step engine's ``StepSessionPort`` over the
owner's Forge connection. A run has one session per stage member, kept alive for the
whole run and reused for the member's later activations, rework included.

Finding a session again: Forge is the record. A session is named
``wf-{run slug}-{member}`` (shortened with a digest of the run and member when the
name would pass Forge's 63 character limit), is tagged ``workflow:{run slug}`` and
carries ``workflow_run_id`` / ``workflow_member`` metadata. ``ensure_session`` and
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
from typing import Any
from uuid import UUID

from ting.domain.models import WorkflowCampaign
from ting.domain.step_instructions import render_step_instructions
from ting.ports.volundr import StepSessionRequest, VolundrFactory, VolundrPort, VolundrSession
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_steps import PersonaRef, StepActivation, StepSessionPort

logger = logging.getLogger(__name__)

#: Forge names sessions with an RFC 1123 label, which DNS limits to 63 characters.
FORGE_SESSION_NAME_MAX = 63
#: Short digest appended to a name that had to be shortened, so two long names stay distinct.
_NAME_DIGEST_CHARS = 8
#: Of a shortened name, how much the member keeps; the run slug gets what is left of the limit.
_NAME_MEMBER_CHARS = 20

_FAILED_STATUSES = frozenset({"failed"})
_STOPPED_STATUSES = frozenset({"stopped"})


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


def _label(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


class ForgeStepSessions(StepSessionPort):
    """Step-host sessions on the owner's Forge, found again by name and tag."""

    def __init__(
        self,
        *,
        volundr_factory: VolundrFactory,
        campaigns: WorkflowCampaignRepository,
        default_model: str = "",
        name_prefix: str = "wf",
    ) -> None:
        self._factory = volundr_factory
        self._campaigns = campaigns
        self._default_model = default_model
        self._prefix = name_prefix
        self._locks: dict[tuple[UUID, str], asyncio.Lock] = {}

    async def ensure_session(self, run_id: UUID, member: str, persona_ref: PersonaRef) -> str:
        campaign, forge = await self._run(run_id)
        async with self._locks.setdefault((run_id, member), asyncio.Lock()):
            name = step_session_name(campaign.slug, member, run_id, prefix=self._prefix)
            tag = step_session_tag(campaign.slug)
            found = await self._find(forge, name, tag, campaign, member)
            if found is not None:
                return await self._reuse(forge, found)
            document = self._persona_document(campaign, member, persona_ref)
            created = await forge.create_step_session(
                StepSessionRequest(
                    name=name,
                    model=self._model_of(campaign, member),
                    persona_definition=document,
                    tracker_issue_id=tag,
                    run_id=str(run_id),
                    member=member,
                    repo=str(campaign.metadata.get("repo") or ""),
                    branch=str(campaign.metadata.get("branch") or ""),
                    base_branch=str(campaign.metadata.get("base_branch") or ""),
                )
            )
            logger.info(
                "Created step session %s (%s) for member %s of run %s",
                created.id,
                name,
                member,
                campaign.slug,
            )
            return created.id

    async def deliver(self, activation: StepActivation) -> None:
        _, forge = await self._run(activation.run_id)
        await forge.deliver_workflow_step(activation.session_id, activation_body(activation))

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

    async def _find(
        self,
        forge: VolundrPort,
        name: str,
        tag: str,
        campaign: WorkflowCampaign,
        member: str,
    ) -> VolundrSession | None:
        named = [s for s in await forge.list_sessions() if s.name == name]
        if not named:
            return None
        session = named[0]
        if session.tracker_issue_id != tag:
            raise StepSessionError(
                f"Forge already has a session named {name!r} that is not run "
                f"{campaign.slug}'s session for {member!r} (it is tagged "
                f"{session.tracker_issue_id!r}, not {tag!r}); rename or stop it"
            )
        return session

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
    graph = snapshot.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    models: set[str] = set()
    for node in nodes or []:
        if not isinstance(node, Mapping) or node.get("kind") != "stage":
            continue
        for raw in node.get("stageMembers") or []:
            if isinstance(raw, Mapping) and str(raw.get("personaId") or "") == member:
                model = str(raw.get("model") or "").strip()
                if model:
                    models.add(model)
    return models
