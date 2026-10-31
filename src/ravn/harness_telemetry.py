"""Emission helper for canonical harness-step telemetry."""

from __future__ import annotations

from niuu.observability import get_observability
from ravn.domain.harness_telemetry import HarnessStepTelemetryRecord

HARNESS_STEP_EVENT = "ravn.harness.step"


def emit_harness_step(record: HarnessStepTelemetryRecord) -> None:
    """Emit a canonical step record through Ravn's existing observability port."""
    get_observability().event(
        HARNESS_STEP_EVENT,
        attributes=record.attributes(),
        content=record.to_dict(),
    )
