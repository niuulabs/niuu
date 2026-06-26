"""Backend and executor adapters for resident portfolio management."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravn.domain.operator_contact import OperatorContactResult
from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_portfolio import (
    CapabilityDiscoveryPort,
    ResidentCapabilityDiscoveryResult,
    ResidentCapabilityGap,
    ResidentCapabilityOption,
    ResidentDelegationRecord,
    ResidentDelegationReview,
    ResidentDelegationStatus,
    ResidentExecutionPort,
    ResidentExecutionResult,
    ResidentExecutionSession,
    ResidentObjective,
    ResidentPortfolio,
    ResidentWorkerBrief,
    ResidentWorkItemBackend,
)
from ravn.ports.capability import (
    WorkflowCapabilityPort,
    WorkflowLaunchRequest,
    WorkflowRunReference,
)
from ravn.resident_continuation import _compact_line, _slug
from ravn.resident_text import timestamp_slug

from .constants import (
    _CAPABILITY_DISCOVERY_PREFIX,
    _DECISION_PREFIX,
    _DELEGATION_PREFIX,
    _DELEGATION_RESULT_PREFIX,
    _DELEGATION_REVIEW_PREFIX,
    _LOCAL_WORKER_SCRIPT,
    _OBJECTIVE_PREFIX,
    _OPERATOR_CONTACT_PREFIX,
    _PORTFOLIO_PATH,
)
from .helpers import (
    _artifact_assimilation_sort_key,
    _assimilate_workflow_artifact_excerpts,
    _delegation_status_from_cancel,
    _delegation_status_from_external,
    _merge_text,
    _parse_delegation,
    _parse_objective,
    _parse_portfolio,
    _render_delegation,
    _render_objective,
    _render_operator_contact,
    _render_portfolio,
    _subprocess_payload,
    _workflow_reference_from_key,
    _workflow_reference_key,
    _workflow_result_status,
    _workflow_status_summary,
    render_worker_brief,
)


class LocalResidentWorkItemBackend(ResidentWorkItemBackend):
    """Filesystem-backed resident work item backend."""

    # TODO: Add a Ting-backed ResidentWorkItemBackend adapter once Ting can use
    # Mimir as a lightweight ticket backend.

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def read_portfolio(self, mandate: str) -> ResidentPortfolio | None:
        path = self._root / _PORTFOLIO_PATH
        if not path.exists():
            return None
        portfolio = _parse_portfolio(path.read_text(encoding="utf-8"), mandate=mandate)
        objectives = tuple(await self.list_objectives(mandate))
        return portfolio.with_objectives(objectives) if objectives else portfolio

    async def write_portfolio(self, portfolio: ResidentPortfolio) -> str:
        return self._write(Path(_PORTFOLIO_PATH), _render_portfolio(portfolio))

    async def list_objectives(self, mandate: str) -> list[ResidentObjective]:
        base = self._root / _OBJECTIVE_PREFIX
        if not base.exists():
            return []
        objectives: list[ResidentObjective] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_objective(path.read_text(encoding="utf-8"))
            if parsed is not None:
                objectives.append(parsed)
        return objectives

    async def write_objective(self, objective: ResidentObjective) -> str:
        rel = Path(_OBJECTIVE_PREFIX) / f"{objective.id}.md"
        return self._write(rel, _render_objective(objective))

    async def append_decision(self, mandate: str, entry: str) -> str:
        stamp = timestamp_slug(datetime.now(UTC))
        rel = Path(_DECISION_PREFIX) / f"{stamp}.md"
        return self._write(rel, f"# Resident Portfolio Decision\n\n{entry}\n")

    async def list_refs(self, prefix: str) -> list[str]:
        base = self._root / prefix
        if not base.exists():
            return []
        return sorted(str(path.relative_to(self._root)) for path in base.glob("*.md"))

    async def write_capability_discovery(self, discovery_id: str, content: str) -> str:
        rel = Path(_CAPABILITY_DISCOVERY_PREFIX) / f"{_slug(discovery_id)}.md"
        return self._write(rel, content)

    async def list_delegations(self, mandate: str) -> list[ResidentDelegationRecord]:
        base = self._root / _DELEGATION_PREFIX
        if not base.exists():
            return []
        delegations: list[ResidentDelegationRecord] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_delegation(path.read_text(encoding="utf-8"))
            if parsed is not None:
                delegations.append(parsed)
        return delegations

    async def write_delegation(self, delegation: ResidentDelegationRecord) -> str:
        rel = Path(_DELEGATION_PREFIX) / f"{delegation.id}.md"
        return self._write(rel, _render_delegation(delegation))

    async def write_delegation_result(
        self,
        delegation_id: str,
        result: ResidentExecutionResult,
        content: str,
    ) -> str:
        filename = f"{_slug(delegation_id)}-{_slug(result.session_id)}.md"
        rel = Path(_DELEGATION_RESULT_PREFIX) / filename
        return self._write(rel, content)

    async def write_delegation_review(
        self,
        review: ResidentDelegationReview,
        content: str,
    ) -> str:
        rel = Path(_DELEGATION_REVIEW_PREFIX) / f"{review.id}.md"
        return self._write(rel, content)

    async def write_operator_contact(self, result: OperatorContactResult) -> str:
        rel = Path(_OPERATOR_CONTACT_PREFIX) / f"{result.request.id}.md"
        return self._write(rel, _render_operator_contact(result))

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


class LocalCapabilityDiscoveryBackend(CapabilityDiscoveryPort):
    """Deterministic bounded capability discovery backend for local proof/tests."""

    async def discover(
        self,
        mandate: str,
        gap: ResidentCapabilityGap,
    ) -> ResidentCapabilityDiscoveryResult:
        capability = _compact_line(gap.capability or gap.summary, limit=120)
        safe_option = ResidentCapabilityOption(
            id=f"evaluate-{_slug(capability)}",
            title=f"Evaluate available capability path for {capability}",
            summary=(
                "Inspect existing tools, workflows, and documentation before building anything new."
            ),
            required_tools=("read_only_catalog_inspection",),
            required_workflows=("local_dry_run",),
            safe_next_experiment=(
                "Run a read-only catalog/workspace inspection and record whether a path exists."
            ),
            evidence=gap.source_evidence,
        )
        adapter_option = ResidentCapabilityOption(
            id=f"build-adapter-{_slug(capability)}",
            title=f"Build or wrap a capability adapter for {capability}",
            summary="Create the smallest adapter/workflow after existing paths are ruled out.",
            required_tools=("code", "tests"),
            required_workflows=("adapter_prototype",),
            required_adapters=("resident_capability_adapter",),
            risks=gap.risk_boundaries,
            approval_required=bool(gap.risk_boundaries),
            safe_next_experiment="Draft an adapter contract and dry-run it without side effects.",
            evidence=gap.source_evidence,
        )
        options = [safe_option, adapter_option]
        if gap.risk_boundaries:
            options.append(
                ResidentCapabilityOption(
                    id=f"approve-{_slug(capability)}",
                    title=f"Ask operator about risk boundaries for {capability}",
                    summary="Clarify human approval before touching bounded or external effects.",
                    risks=gap.risk_boundaries,
                    approval_required=True,
                    safe_next_experiment="Ask the operator for explicit approval boundaries.",
                    evidence=gap.source_evidence,
                )
            )
        return ResidentCapabilityDiscoveryResult(
            gap=gap,
            capability_summary=f"Capability gap: {capability}",
            why_it_matters=(
                "The resident cannot safely advance related objectives until this capability "
                "has a known path, adapter, or approval boundary."
            ),
            known_constraints=tuple(
                item
                for item in (
                    *gap.risk_boundaries,
                    *gap.blocked_dependencies,
                    *gap.required_capabilities,
                )
                if item
            ),
            candidate_options=tuple(options),
            recommended_option_id=safe_option.id,
            recommended_safe_next_experiment=safe_option.safe_next_experiment,
            unresolved_questions=(
                ("What approval boundary applies?",) if gap.risk_boundaries else ()
            ),
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
