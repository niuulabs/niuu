"""Rotation of exact-scope credentials for developer coordinator sessions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from niuu.ports.workload_identity import WorkloadTokenIssuer
from volundr.domain.models import Session, SessionStatus
from volundr.domain.ports import SessionRepository
from volundr.ports.workflow_execution_credentials import (
    ExecutionCredentialProjection,
    ExecutionCredentialProjectionPort,
)

logger = logging.getLogger(__name__)

WORKFLOW_COORDINATE_SCOPE = "ting:workflow:coordinate"
_ACTIVE_STATUSES = frozenset(
    {
        SessionStatus.CREATED,
        SessionStatus.STARTING,
        SessionStatus.PROVISIONING,
        SessionStatus.RUNNING,
    }
)


class WorkflowExecutionCredentialError(ValueError):
    """The configured credential projection cannot safely serve a session."""


@dataclass(frozen=True)
class WorkflowExecutionCredentialBinding:
    """Non-secret persisted lineage needed to mint one coordinator credential."""

    execution_id: UUID
    parent_node_id: str
    parent_session_key: str
    coordinator_id: str
    child_attempt_id: UUID | None = None
    child_intent_id: UUID | None = None
    child_task_id: str = ""

    @property
    def is_child(self) -> bool:
        return self.child_attempt_id is not None

    @classmethod
    def from_session(cls, session: Session) -> WorkflowExecutionCredentialBinding | None:
        provenance = session.workload_config.get("provenance")
        if not isinstance(provenance, dict):
            return None
        raw = provenance.get("workflow_execution")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise WorkflowExecutionCredentialError(
                "workflow_execution provenance must be an object"
            )

        def required(name: str) -> str:
            value = str(raw.get(name) or "").strip()
            if not value:
                raise WorkflowExecutionCredentialError(
                    f"workflow_execution provenance requires {name}"
                )
            return value

        try:
            execution_id = UUID(required("execution_id"))
        except ValueError as exc:
            raise WorkflowExecutionCredentialError(
                "workflow_execution execution_id must be a UUID"
            ) from exc

        attempt = str(raw.get("child_attempt_id") or "").strip()
        intent = str(raw.get("child_intent_id") or "").strip()
        task = str(raw.get("child_task_id") or "").strip()
        if bool(attempt) != bool(intent) or bool(attempt) != bool(task):
            raise WorkflowExecutionCredentialError(
                "child workflow_execution provenance requires attempt, intent, and task lineage"
            )
        try:
            child_attempt_id = UUID(attempt) if attempt else None
            child_intent_id = UUID(intent) if intent else None
        except ValueError as exc:
            raise WorkflowExecutionCredentialError(
                "workflow_execution child attempt and intent ids must be UUIDs"
            ) from exc

        parent_session_key = required("parent_session_key")
        expected_prefix = "workflow:a2a-" if child_attempt_id else "workflow:developer-"
        if not parent_session_key.startswith(expected_prefix):
            raise WorkflowExecutionCredentialError(
                f"workflow_execution parent_session_key must start with {expected_prefix}"
            )
        return cls(
            execution_id=execution_id,
            parent_node_id=required("parent_node_id"),
            parent_session_key=parent_session_key,
            coordinator_id=required("coordinator_id"),
            child_attempt_id=child_attempt_id,
            child_intent_id=child_intent_id,
            child_task_id=task,
        )


class WorkflowExecutionCredentialService:
    """Mint and rotate existing scoped workload JWTs into runtime projections."""

    def __init__(
        self,
        *,
        repository: SessionRepository,
        token_issuer: WorkloadTokenIssuer,
        projection: ExecutionCredentialProjectionPort,
        runtime_backend: str,
        refresh_interval_seconds: float,
        trusted_signing_configured: bool,
        admission_roles: tuple[str, ...] = ("volundr:developer",),
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise WorkflowExecutionCredentialError(
                "developer credential refresh_interval_seconds must be positive"
            )
        if not token_issuer.enabled:
            raise WorkflowExecutionCredentialError(
                "developer credential rotation requires workload_identity.enabled=true"
            )
        if not trusted_signing_configured:
            raise WorkflowExecutionCredentialError(
                "developer credential rotation requires a configured workload identity signing "
                "key trusted by Ting; generated process-local keys are not supported"
            )
        if not projection.supports(runtime_backend):
            raise WorkflowExecutionCredentialError(
                "developer credential projection does not support runtime backend "
                f"{runtime_backend!r}; configure a projection adapter for that backend"
            )
        if not admission_roles or any(not role.strip() for role in admission_roles):
            raise WorkflowExecutionCredentialError(
                "developer credential rotation requires configured gateway admission roles"
            )
        self._repository = repository
        self._token_issuer = token_issuer
        self._projection = projection
        self._runtime_backend = runtime_backend
        self._refresh_interval_seconds = refresh_interval_seconds
        self._admission_roles = list(admission_roles)
        self._task: asyncio.Task[None] | None = None
        self._locations: dict[UUID, ExecutionCredentialProjection] = {}

    async def project(self, session: Session) -> ExecutionCredentialProjection | None:
        binding = WorkflowExecutionCredentialBinding.from_session(session)
        if binding is None:
            return None
        if not session.owner_id or not session.tenant_id:
            raise WorkflowExecutionCredentialError(
                "developer coordinator session requires durable owner_id and tenant_id"
            )
        issued = self._token_issuer.issue_token(
            principal=Principal(
                user_id=session.owner_id,
                email="",
                tenant_id=session.tenant_id,
                # Gateway admission still requires a platform role. Route scope
                # enforcement bounds this scoped JWT to the coordinate operation.
                # Roles come from operator config, never caller provenance.
                roles=self._admission_roles,
            ),
            workload_subject=binding.parent_session_key,
            workload_name=binding.coordinator_id,
            audiences=[],
            token_use=VALKYRIE_BUILD_TOKEN_USE,
            claims=self._claims(binding, session.id),
        )
        projected = await self._projection.project(
            session_id=session.id,
            token=issued.token,
            runtime_backend=self._runtime_backend,
        )
        self._locations[session.id] = projected
        return projected

    def token_file(self, session_id: UUID) -> str:
        """Return the path produced by the initial pre-start projection."""
        projected = self._locations.get(session_id)
        if projected is None:
            raise WorkflowExecutionCredentialError(
                f"developer credential was not projected for session {session_id}"
            )
        return projected.token_file

    def projection(self, session_id: UUID) -> ExecutionCredentialProjection:
        """Return the initial projection so sidecars can mount the same directory."""
        projected = self._locations.get(session_id)
        if projected is None:
            raise WorkflowExecutionCredentialError(
                f"developer credential was not projected for session {session_id}"
            )
        return projected

    async def remove(self, session_id: UUID) -> None:
        """Remove one session's projected credential material."""
        self._locations.pop(session_id, None)
        await self._projection.remove(session_id)

    @staticmethod
    def _claims(
        binding: WorkflowExecutionCredentialBinding,
        forge_session_id: UUID,
    ) -> dict[str, Any]:
        claims: dict[str, Any] = {
            "scopes": [WORKFLOW_COORDINATE_SCOPE],
            "workflow_execution_id": str(binding.execution_id),
            "parent_node_id": binding.parent_node_id,
            "parent_session_key": binding.parent_session_key,
            "coordinator_id": binding.coordinator_id,
            "forge_session_id": str(forge_session_id),
        }
        if binding.child_attempt_id is None:
            return claims
        claims.update(
            {
                "child_attempt_id": str(binding.child_attempt_id),
                "child_intent_id": str(binding.child_intent_id),
                "child_task_id": binding.child_task_id,
            }
        )
        return claims

    async def _reconcile_session(
        self, session: Session, failures: list[tuple[UUID, Exception]]
    ) -> None:
        """Rotate, fail, or clean up the credential binding for one session row."""
        try:
            binding = WorkflowExecutionCredentialBinding.from_session(session)
        except WorkflowExecutionCredentialError as exc:
            # Historical descriptors need cleanup, but their terminal
            # status must remain unchanged.
            if session.status in _ACTIVE_STATUSES:
                failed = session.with_status(SessionStatus.FAILED).with_error(str(exc))
                await self._repository.update(failed)
                logger.error(
                    "Rejected malformed developer credential binding for session %s: %s",
                    session.id,
                    exc,
                )
            try:
                await self.remove(session.id)
            except Exception as cleanup_error:
                failures.append((session.id, cleanup_error))
                logger.exception("Developer credential cleanup failed for %s", session.id)
            return
        try:
            if binding is None:
                return
            if session.status in _ACTIVE_STATUSES:
                await self.project(session)
                return
            await self.remove(session.id)
        except Exception as exc:
            failures.append((session.id, exc))
            logger.exception(
                "Developer credential reconciliation failed for session %s", session.id
            )

    @staticmethod
    def _raise_aggregated_failures(failures: list[tuple[UUID, Exception]]) -> None:
        if not failures:
            return
        ids = ", ".join(str(session_id) for session_id, _ in failures)
        raise WorkflowExecutionCredentialError(
            f"developer credential reconciliation failed for sessions: {ids}"
        ) from failures[0][1]

    async def reconcile_once(self) -> None:
        """Exhaustive sweep over every session row.

        Used once at startup (via `start()`) to pick up bindings from before
        this process existed — a prior instance's projected credential can
        outlive it if the process died mid-cleanup. This does list() with no
        status filter deliberately: it is the one place that is allowed to be
        unbounded, precisely because it only ever runs once per process
        lifetime. The periodic loop must not repeat this shape (see
        `_reconcile_active_cycle`).
        """
        sessions = await self._repository.list()
        failures: list[tuple[UUID, Exception]] = []
        for session in sessions:
            await self._reconcile_session(session, failures)
        self._raise_aggregated_failures(failures)

    async def _reconcile_active_cycle(self) -> None:
        """Bounded per-interval reconciliation: active sessions, plus drift cleanup.

        Rotation only needs currently-active sessions, so this lists by status
        instead of every session ever created — the table only grows, so an
        unfiltered list() here would make each tick more expensive than the
        last forever. Terminal-session cleanup is bounded the same way: rather
        than re-scanning full history, it diffs `_locations` (the sessions this
        process actually holds a live projection for, populated by `project()`)
        against the freshly-fetched active set and removes whatever fell out —
        i.e. "clean up on the transition", using the marker that already
        exists. A session that went terminal before this process ever
        projected for it is caught by the startup `reconcile_once()` sweep
        instead, not by this cycle.
        """
        sessions: dict[UUID, Session] = {}
        for status in _ACTIVE_STATUSES:
            for item in await self._repository.list(status=status):
                sessions[item.id] = item
        failures: list[tuple[UUID, Exception]] = []
        for item in sessions.values():
            await self._reconcile_session(item, failures)
        for session_id in [sid for sid in self._locations if sid not in sessions]:
            try:
                await self.remove(session_id)
            except Exception as cleanup_error:
                failures.append((session_id, cleanup_error))
                logger.exception("Developer credential cleanup failed for %s", session_id)
        self._raise_aggregated_failures(failures)

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.reconcile_once()
        self._task = asyncio.create_task(self._run(), name="developer-credential-rotation")
        self._task.add_done_callback(self._log_if_rotation_loop_died)

    @staticmethod
    def _log_if_rotation_loop_died(task: asyncio.Task[None]) -> None:
        """Backstop so a dead rotation loop can never be silent.

        `_run()` already catches every non-cancellation exception at the cycle
        boundary and keeps going, so this should never fire in practice — but
        if it ever does (a bug in the catch-all itself, for instance), tokens
        silently stop rotating and expire with no other signal. A `critical`
        log with the full traceback is the loud failure no-fallbacks requires.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.critical(
            "Developer credential rotation loop exited unexpectedly; developer "
            "coordinator tokens will stop rotating and expire until the process "
            "is restarted",
            exc_info=exc,
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval_seconds)
            try:
                await self._reconcile_active_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Broad on purpose: reconcile_once()/​_reconcile_active_cycle()
                # already isolate per-session failures, so anything still
                # reaching here is a cycle-wide fault — e.g. the repository
                # connection itself (list()/update() are not wrapped
                # per-session). A transient asyncpg error must not kill this
                # loop forever, or coordinator tokens quietly expire; retry on
                # the next interval instead.
                logger.exception("Developer credential rotation cycle failed; retrying")
