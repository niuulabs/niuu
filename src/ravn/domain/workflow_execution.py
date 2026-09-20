"""Typed Ravn boundary for the domain-neutral durable workflow execution lifecycle.

Fan-out and join, durable waits, and session continuation serve any workflow
that expands into a bounded child DAG. This port is deliberately silent about
what a child produces, what a coordinator's children look like, or how a
result is verified — that is exactly what a specialization (for example code
delivery, see ``ravn.domain.delivery``) layers on top of the same durable
execution instead of teaching this port its vocabulary.
"""

from __future__ import annotations

from typing import Any, Protocol


class WorkflowExecutionToolPort(Protocol):
    """Coordinator-safe facade over Ting's durable, domain-neutral execution lifecycle."""

    async def expand(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def reconcile(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def retry(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def message(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def cancel(self) -> dict[str, Any]: ...

    async def wait(self, payload: dict[str, Any]) -> dict[str, Any]: ...
