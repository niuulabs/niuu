"""Operator contact port — boundary for asking the operator for judgment."""

from __future__ import annotations

from typing import Protocol

from ravn.domain.operator_contact import OperatorContactRequest, OperatorContactResult


class OperatorContactPort(Protocol):
    """Boundary for asking the operator and optionally waiting for an answer."""

    async def ask(self, request: OperatorContactRequest) -> OperatorContactResult:
        """Emit an operator request and return an answer when available."""
