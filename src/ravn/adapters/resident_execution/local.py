"""Local resident execution adapters — simulated, subprocess, and workflow-backed."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_portfolio import (
    ResidentDelegationStatus,
    ResidentExecutionPort,
    ResidentExecutionResult,
    ResidentExecutionSession,
    ResidentWorkerBrief,
)
from ravn.ports.capability import (
    WorkflowCapabilityPort,
    WorkflowLaunchRequest,
    WorkflowRunReference,
)
from ravn.resident_continuation import _compact_line, _slug
from ravn.resident_portfolio.constants import _LOCAL_WORKER_SCRIPT
from ravn.resident_portfolio.helpers import (
    _artifact_assimilation_sort_key,
    _assimilate_workflow_artifact_excerpts,
    _delegation_status_from_cancel,
    _delegation_status_from_external,
    _merge_text,
    _subprocess_payload,
    _workflow_reference_from_key,
    _workflow_reference_key,
    _workflow_result_status,
    _workflow_status_summary,
    render_worker_brief,
)


class LocalSimulatedResidentExecutor(ResidentExecutionPort):
    """Deterministic resident execution backend for local proofs and tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[ResidentWorkerBrief, ResidentExecutionSession]] = {}
        self.launched: list[ResidentWorkerBrief] = []

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        session_id = f"local-{brief.id}"
        session = ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.COMPLETED.value,
            backend_name="local-simulated",
            summary=f"Simulated bounded execution for {brief.objective_title}.",
        )
        self._sessions[session_id] = (brief, session)
        self.launched.append(brief)
        return session

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        stored = self._sessions.get(session_id)
        if stored is None:
            return ResidentExecutionSession(
                session_id=session_id,
                status=ResidentDelegationStatus.UNAVAILABLE.value,
                backend_name="local-simulated",
                summary="session is not available in local simulator",
            )
        return stored[1]

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        stored = self._sessions.get(session_id)
        if stored is None:
            return None
        brief, session = stored
        if session.status != ResidentDelegationStatus.COMPLETED.value:
            return None
        return ResidentExecutionResult(
            session_id=session_id,
            status=ResidentDelegationStatus.COMPLETED.value,
            summary=f"Completed delegated work for {brief.objective_title}.",
            output_refs=(f"local-simulated/{brief.id}.md",),
            findings=(f"Delegated worker produced bounded evidence for {brief.objective_id}.",),
            follow_up_suggestions=(f"Review delegated output for {brief.objective_title}",),
            usage=ResidentBudgetSnapshot(turns_used=1),
        )

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        stored = self._sessions.get(session_id)
        brief = stored[0] if stored is not None else None
        session = ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.CANCELLED.value,
            backend_name="local-simulated",
            summary=reason,
        )
        if brief is not None:
            self._sessions[session_id] = (brief, session)
        return session


class WorkflowResidentExecutionAdapter(ResidentExecutionPort):
    """Resident execution adapter backed by an existing workflow capability port."""

    def __init__(
        self,
        *,
        workflows: WorkflowCapabilityPort,
        workflow_id: str = "",
        connection_id: str = "",
        repo: str = "",
        branch: str = "",
        model: str = "",
        definition: str = "",
    ) -> None:
        self._workflows = workflows
        self._workflow_id = workflow_id
        self._connection_id = connection_id
        self._repo = repo
        self._branch = branch
        self._model = model
        self._definition = definition
        self._references: dict[str, WorkflowRunReference] = {}

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        workflow_id = self._workflow_id or await self._select_workflow_id(brief)
        launch = await self._workflows.launch_workflow(
            WorkflowLaunchRequest(
                workflow_id=workflow_id,
                prompt=render_worker_brief(brief),
                session_name=_compact_line(f"resident-{brief.objective_id}", limit=63),
                repo=self._repo,
                branch=self._branch,
                connection_id=self._connection_id,
                model=self._model,
                definition=self._definition,
                provenance={
                    "resident_objective_id": brief.objective_id,
                    "resident_brief_id": brief.id,
                },
            )
        )
        reference = WorkflowRunReference(
            session_id=launch.session_id,
            slug=launch.slug,
            workflow_id=launch.workflow_id,
        )
        session_id = _workflow_reference_key(reference)
        self._references[session_id] = reference
        return ResidentExecutionSession(
            session_id=session_id,
            status=_delegation_status_from_external(launch.status),
            backend_name="workflow",
            summary=launch.workflow_name or launch.session_name or "workflow launched",
        )

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        reference = self._reference(session_id)
        status = await self._workflows.get_workflow_status(reference)
        return ResidentExecutionSession(
            session_id=status.session_id or session_id,
            status=_delegation_status_from_external(status.state),
            backend_name="workflow",
            summary=_workflow_status_summary(status),
        )

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        reference = self._reference(session_id)
        status = await self._workflows.get_workflow_status(reference)
        mapped = _delegation_status_from_external(status.state)
        events = await self._workflows.list_workflow_events(reference, limit=20)
        artifacts = await self._workflows.list_workflow_artifacts(reference)
        if mapped not in {
            ResidentDelegationStatus.COMPLETED.value,
            ResidentDelegationStatus.BLOCKED.value,
            ResidentDelegationStatus.FAILED.value,
        } and not artifacts:
            return None
        output_refs = tuple(item.path for item in artifacts if item.path)
        artifact_excerpts = await self._read_workflow_artifact_excerpts(
            reference,
            tuple(artifacts),
        )
        findings = tuple(
            _compact_line(
                f"{event.event_type}: {json.dumps(event.data, sort_keys=True)}",
                limit=240,
            )
            for event in events[:5]
        )
        result_status = _workflow_result_status(mapped, output_refs, events)
        if result_status == ResidentDelegationStatus.RUNNING.value and artifacts:
            findings = _merge_text(
                findings,
                (
                    "workflow artifact snapshot observed while session remained running; "
                    "terminal worker result still pending",
                ),
            )
        summary_parts = [
            _compact_line(item.summary or item.title or item.path, limit=160)
            for item in artifacts[:3]
            if item.summary or item.title or item.path
        ]
        summary = "; ".join(summary_parts) or _workflow_status_summary(status)
        assimilated = _assimilate_workflow_artifact_excerpts(artifact_excerpts)
        return ResidentExecutionResult(
            session_id=status.session_id or session_id,
            status=result_status,
            summary=summary,
            output_refs=output_refs,
            artifact_excerpts=artifact_excerpts,
            findings=_merge_text(findings, assimilated["findings"]),
            follow_up_suggestions=_merge_text(
                assimilated["follow_up_suggestions"],
                (
                    (f"Assimilate workflow output for session {status.session_id or session_id}",)
                    if (output_refs or findings) and not artifact_excerpts
                    else ()
                ),
            ),
            known_facts=assimilated["known_facts"],
            hypotheses=assimilated["hypotheses"],
            open_questions=assimilated["open_questions"],
            operator_questions=assimilated["operator_questions"],
            risk_notes=assimilated["risk_notes"],
            recommended_next_action=assimilated["recommended_next_action"][0]
            if assimilated["recommended_next_action"]
            else "",
            blocked_reason=(
                summary
                if result_status
                in {
                    ResidentDelegationStatus.BLOCKED.value,
                    ResidentDelegationStatus.FAILED.value,
                }
                else ""
            ),
        )

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        reference = self._reference(session_id)
        status = await self._workflows.cancel_workflow(reference, reason=reason)
        return ResidentExecutionSession(
            session_id=status.session_id or session_id,
            status=_delegation_status_from_cancel(status.state),
            backend_name="workflow",
            summary=_workflow_status_summary(status),
        )

    async def _select_workflow_id(self, brief: ResidentWorkerBrief) -> str:
        workflows = await self._workflows.list_workflows()
        if not workflows:
            raise RuntimeError("No workflow capability is available for resident delegation")
        capability_words = {
            word
            for item in brief.constraints + brief.proof_criteria + (brief.objective_title,)
            for word in _slug(item).split("-")
            if len(word) > 3
        }
        scored = sorted(
            workflows,
            key=lambda item: len(
                capability_words
                & set(
                    _slug(
                        " ".join(
                            (
                                item.workflow_id,
                                item.name,
                                item.description,
                                " ".join(item.tags),
                            )
                        )
                    ).split("-")
                )
            ),
            reverse=True,
        )
        return scored[0].workflow_id

    def _reference(self, session_id: str) -> WorkflowRunReference:
        return (
            self._references.get(session_id)
            or _workflow_reference_from_key(session_id)
            or WorkflowRunReference(session_id=session_id)
        )

    async def _read_workflow_artifact_excerpts(
        self,
        reference: WorkflowRunReference,
        artifacts: tuple[Any, ...],
    ) -> tuple[str, ...]:
        excerpts: list[str] = []
        remaining = 18000
        for artifact in sorted(artifacts, key=_artifact_assimilation_sort_key):
            path = getattr(artifact, "path", "")
            if not path or remaining <= 0:
                continue
            try:
                content = await self._workflows.read_workflow_artifact(reference, path=path)
            except Exception:
                continue
            text = str(getattr(content, "content", "") or "").strip()
            if not text:
                continue
            excerpt = text[: min(remaining, 9000)]
            excerpts.append(f"# Artifact: {path}\n\n{excerpt}")
            remaining -= len(excerpt)
            if len(excerpts) >= 6:
                break
        return tuple(excerpts)


class LocalSubprocessResidentExecutor(ResidentExecutionPort):
    """Resident execution adapter that runs a local worker process."""

    def __init__(self, command: tuple[str, ...] | None = None) -> None:
        self._command = command or (sys.executable, "-c", _LOCAL_WORKER_SCRIPT)
        self._sessions: dict[str, ResidentExecutionSession] = {}
        self._results: dict[str, ResidentExecutionResult] = {}

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        session_id = f"subprocess-{brief.id}"
        proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(render_worker_brief(brief).encode("utf-8"))
        status = (
            ResidentDelegationStatus.COMPLETED.value
            if proc.returncode == 0
            else ResidentDelegationStatus.FAILED.value
        )
        summary, findings, follow_ups = _subprocess_payload(stdout, stderr)
        output_refs = (f"local-worker/{brief.id}.json",) if proc.returncode == 0 else ()
        result = ResidentExecutionResult(
            session_id=session_id,
            status=status,
            summary=summary,
            output_refs=output_refs,
            findings=findings,
            follow_up_suggestions=follow_ups,
            blocked_reason="" if proc.returncode == 0 else summary,
        )
        if output_refs:
            artifact_path = Path.cwd() / output_refs[0]
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "status": status,
                        "backend_name": "local-subprocess",
                        "summary": summary,
                        "findings": list(findings),
                        "follow_up_suggestions": list(follow_ups),
                        "brief": {
                            "id": brief.id,
                            "objective_id": brief.objective_id,
                            "objective_title": brief.objective_title,
                            "desired_outcome": brief.desired_outcome,
                            "proof_criteria": list(brief.proof_criteria),
                            "evidence": list(brief.evidence),
                            "artifact_links": list(brief.artifact_links),
                            "constraints": list(brief.constraints),
                            "risk_boundaries": list(brief.risk_boundaries),
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        session = ResidentExecutionSession(
            session_id=session_id,
            status=status,
            backend_name="local-subprocess",
            summary=summary,
        )
        self._sessions[session_id] = session
        self._results[session_id] = result
        return session

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        return self._sessions.get(
            session_id,
            ResidentExecutionSession(
                session_id=session_id,
                status=ResidentDelegationStatus.UNAVAILABLE.value,
                backend_name="local-subprocess",
                summary="local worker session is not available",
            ),
        )

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        return self._results.get(session_id)

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        session = ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.CANCELLED.value,
            backend_name="local-subprocess",
            summary=reason,
        )
        self._sessions[session_id] = session
        return session
