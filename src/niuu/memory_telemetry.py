"""Shared telemetry for the agent's calls to Mímir.

Mímir (:class:`niuu.ports.mimir.MimirPort`) is the shared knowledge base
reached over HTTP, and it can fail silently: an empty result was
indistinguishable from a healthy one. Metric names live here so the Grafana
dashboard and the adapters cannot drift apart.

Names follow the OTel dotted convention already used by Ravn's tool
observability; the Prometheus exporter renders ``ravn.mimir.operations`` as
``ravn_mimir_operations_total``.
"""

from __future__ import annotations

from niuu.observability import get_observability

# --- metric names ---------------------------------------------------------

DURATION = "ravn.memory.duration"

MIMIR_OPERATIONS = "ravn.mimir.operations"
MIMIR_RESULTS = "ravn.mimir.results"

# --- attribute keys -------------------------------------------------------

ATTR_OPERATION = "ravn.memory.operation"
ATTR_RESULT = "ravn.memory.result"
ATTR_MIMIR_OPERATION = "ravn.mimir.operation"
ATTR_COMPONENT = "ravn.runtime.component"
ATTR_ENVIRONMENT = "ravn.environment.id"

# --- result values --------------------------------------------------------

RESULT_HIT = "hit"
RESULT_EMPTY = "empty"
RESULT_ERROR = "error"


def result_for(count: int) -> str:
    """Map a returned-item count onto the shared ``hit``/``empty`` vocabulary."""
    return RESULT_HIT if count > 0 else RESULT_EMPTY


def _identity(component: str, environment_id: str) -> dict[str, str]:
    """Attributes identifying which resident produced a sample.

    The dashboard's environment picker filters on ``ravn.environment.id``, the
    same key ``drive_loop`` stamps on its gauges. Without it these metrics
    cannot be separated when a Mimir tenant holds more than one resident.
    Omitted entirely when unset, so a series never carries an empty label.
    """
    attributes = {ATTR_COMPONENT: component}
    if environment_id:
        attributes[ATTR_ENVIRONMENT] = environment_id
    return attributes


def record_mimir_operation(
    *,
    operation: str,
    result: str,
    seconds: float | None = None,
    results_returned: int | None = None,
    component: str = "resident",
    environment_id: str = "",
) -> None:
    """Emit one Mímir call made by the agent."""
    telemetry = get_observability()
    attributes = {
        ATTR_MIMIR_OPERATION: operation,
        ATTR_RESULT: result,
        **_identity(component, environment_id),
    }
    telemetry.count(
        MIMIR_OPERATIONS,
        attributes=attributes,
        description="Mimir operations issued by the agent, by outcome.",
    )
    if results_returned is not None:
        telemetry.record(
            MIMIR_RESULTS,
            results_returned,
            attributes=attributes,
            description="Results returned per Mimir retrieval call.",
        )
    if seconds is not None:
        telemetry.duration(
            DURATION,
            seconds,
            attributes={ATTR_OPERATION: f"mimir.{operation}", ATTR_RESULT: result},
            description="Mimir call latency.",
        )


__all__ = [
    "ATTR_COMPONENT",
    "ATTR_ENVIRONMENT",
    "ATTR_MIMIR_OPERATION",
    "ATTR_OPERATION",
    "ATTR_RESULT",
    "DURATION",
    "MIMIR_OPERATIONS",
    "MIMIR_RESULTS",
    "RESULT_EMPTY",
    "RESULT_ERROR",
    "RESULT_HIT",
    "record_mimir_operation",
    "result_for",
]
