"""Domain contracts for resident physical-world capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class PhysicalActionKind(StrEnum):
    """Safety class for a physical-world capability request."""

    READ_ONLY = "read_only"
    DRY_RUN = "dry_run"
    PHYSICAL_OPERATION = "physical_operation"


@dataclass(frozen=True)
class PhysicalCapability:
    """A physical-world capability visible to a resident behind a port."""

    id: str
    name: str
    description: str
    action_kinds: tuple[str, ...]
    telemetry_supported: bool = False
    dry_run_supported: bool = False
    risk_boundaries: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhysicalActionRequest:
    """A resident request to inspect, dry-run, or operate a physical capability."""

    capability_id: str
    action: str
    kind: str
    reason: str
    parameters: dict[str, Any] = field(default_factory=dict)
    risk_boundaries: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhysicalActionResult:
    """Result of a physical-world capability action or policy block."""

    capability_id: str
    action: str
    kind: str
    status: str
    summary: str
    telemetry: dict[str, Any] = field(default_factory=dict)
    output_refs: tuple[str, ...] = ()
    risk_boundaries: tuple[str, ...] = ()
    approval_required: bool = False
    blocked_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

