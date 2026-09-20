"""Ports for durable, condition-agnostic workflow waits."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID

from ting.domain.workflow_execution import WorkflowExecution
from ting.domain.workflow_wait import WaitObservation, WorkflowWait


class WorkflowWaitRepository(ABC):
    """Durable ledger for waits, addressed by their exact wait node."""

    @abstractmethod
    async def reserve(
        self,
        execution: WorkflowExecution,
        *,
        wait_id: UUID,
        node_id: str,
        condition_type: str,
        request: dict[str, Any],
        request_digest: str,
        next_poll_at: datetime,
    ) -> WorkflowWait:
        """Reserve one exact idempotent wait, rejecting a different active wait on the node."""

    @abstractmethod
    async def list_for_execution(self, execution_id: UUID) -> list[WorkflowWait]:
        """List the immutable wait history for one execution."""

    @abstractmethod
    async def claim_due(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[WorkflowWait]:
        """Lease due polls and terminal notifications with fencing tokens."""

    @abstractmethod
    async def record_pending(
        self,
        wait: WorkflowWait,
        observation: WaitObservation | None,
        *,
        next_poll_at: datetime,
        error: str = "",
    ) -> None:
        """Persist a nonterminal observation and release the current lease."""

    @abstractmethod
    async def record_terminal(
        self,
        wait: WorkflowWait,
        observation: WaitObservation,
        *,
        expected_execution_revision: int,
        project_execution: bool,
    ) -> WorkflowWait:
        """Persist terminal evidence before any continuation is delivered."""

    @abstractmethod
    async def mark_notified(self, wait: WorkflowWait) -> None:
        """Mark a terminal observation delivered under the current lease."""


class WaitConditionObserver(ABC):
    """Reads and classifies one condition type without acting on anything.

    Registered by ``condition_type`` through configuration (see
    ``.claude/rules/dynamic-adapters.md``); the generic wait service dispatches
    to the observer whose ``condition_type`` matches a wait's own, and never
    branches on the string itself.
    """

    @property
    @abstractmethod
    def condition_type(self) -> str:
        """The opaque condition identity this observer is registered for."""

    @abstractmethod
    def validate(self, request: dict[str, Any], execution: WorkflowExecution) -> None:
        """Reject a request before it is ever persisted.

        A request is rejected when it is malformed, and when it does not belong
        to this execution: an observer that reports on something the execution
        produced checks here that the request names exactly that thing.
        """

    @abstractmethod
    async def observe(
        self,
        wait: WorkflowWait,
        execution: WorkflowExecution,
    ) -> WaitObservation:
        """Read and classify the current state of one exact wait's condition."""


class WaitContinuation[ExecutionT: WorkflowExecution](ABC):
    """Deliver one persisted wait observation to the exact parent session."""

    @abstractmethod
    async def notify_wait_observation(
        self,
        execution: ExecutionT,
        wait: WorkflowWait,
        observation: WaitObservation,
    ) -> None:
        """Deliver one persisted terminal wait observation to the exact parent session."""
