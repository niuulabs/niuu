"""Shared telemetry for Ravn's three memory surfaces.

Ravn reads from three stores with very different contracts, and each one can
fail silently in its own way:

* **episodic memory** (:class:`ravn.ports.memory.MemoryPort`) — ranked, lossy;
  a miss costs answer quality;
* **resident state** (:class:`ravn.domain.resident_state.ResidentStatePort`) —
  typed, exact-read; a miss breaks continuation;
* **Mímir** (:class:`ravn.ports.mimir.MimirPort`) — the shared knowledge base
  reached over HTTP.

All three previously emitted nothing, so an empty result was indistinguishable
from a healthy one. Metric names live here so the Grafana dashboard, the
adapters, and ``tests/test_ravn/test_valkyrie_grafana_dashboard.py`` cannot
drift apart.

Names follow the OTel dotted convention already used by
``ravn.tool_observability``; the Prometheus exporter renders
``ravn.memory.operations`` as ``ravn_memory_operations_total``.

The candidate funnel deserves a note. ``CANDIDATES`` counts what the search
port returned *before* domain scoring; ``ADMITTED`` counts what survived the
``min_relevance`` gate. Their ratio is the diagnostic that distinguishes the
three very different causes of an empty prefetch — nothing stored, nothing
matched, or the gate discarding good candidates. ``RELEVANCE_SCORE`` records
every scored candidate including rejected ones, so a corpus scoring just below
the threshold is visible rather than merely absent.
"""

from __future__ import annotations

from niuu.observability import get_observability

# --- metric names ---------------------------------------------------------

OPERATIONS = "ravn.memory.operations"
CANDIDATES = "ravn.memory.candidates"
ADMITTED = "ravn.memory.admitted"
RELEVANCE_SCORE = "ravn.memory.relevance.score"
CANDIDATE_AGE_DAYS = "ravn.memory.candidate.age.days"
INJECTED_CHARS = "ravn.memory.prefetch.injected.chars"
DURATION = "ravn.memory.duration"
CORPUS_EPISODES = "ravn.memory.corpus.episodes"
CORPUS_EMBEDDING_COVERAGE = "ravn.memory.corpus.embedding.coverage"
CORPUS_INDEX_COVERAGE = "ravn.memory.corpus.index.coverage"

RESIDENT_STATE_OPERATIONS = "ravn.resident_state.operations"

MIMIR_OPERATIONS = "ravn.mimir.operations"
MIMIR_RESULTS = "ravn.mimir.results"

# --- attribute keys -------------------------------------------------------

ATTR_BACKEND = "ravn.memory.backend"
ATTR_OPERATION = "ravn.memory.operation"
ATTR_RESULT = "ravn.memory.result"
ATTR_ADAPTER = "ravn.resident_state.adapter"
ATTR_RECORD_TYPE = "ravn.resident_state.record_type"
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


def record_memory_operation(
    *,
    operation: str,
    backend: str,
    result: str,
    seconds: float | None = None,
    component: str = "resident",
    environment_id: str = "",
) -> None:
    """Emit the outcome (and optionally the latency) of one memory operation."""
    telemetry = get_observability()
    attributes = {
        ATTR_OPERATION: operation,
        ATTR_BACKEND: backend,
        ATTR_RESULT: result,
        **_identity(component, environment_id),
    }
    telemetry.count(
        OPERATIONS,
        attributes=attributes,
        description="Episodic memory operations by outcome.",
    )
    if seconds is not None:
        telemetry.duration(
            DURATION,
            seconds,
            attributes=attributes,
            description="Episodic memory operation latency.",
        )


def record_funnel(
    *,
    backend: str,
    candidates: int,
    admitted: int,
    scores: list[float],
    top_candidate_age_days: float | None,
    component: str = "resident",
    environment_id: str = "",
) -> None:
    """Emit the retrieval funnel for one query.

    ``scores`` carries every candidate's combined score, admitted or not, so
    the histogram shows a sub-threshold cluster instead of silence.
    """
    telemetry = get_observability()
    attributes = {ATTR_BACKEND: backend, **_identity(component, environment_id)}
    telemetry.count(
        CANDIDATES,
        candidates,
        attributes=attributes,
        description="Candidates returned by the search port before domain scoring.",
    )
    telemetry.count(
        ADMITTED,
        admitted,
        attributes=attributes,
        description="Candidates that survived the min_relevance gate.",
    )
    for score in scores:
        telemetry.record(
            RELEVANCE_SCORE,
            score,
            attributes=attributes,
            description="Combined relevance x recency x outcome score per candidate.",
        )
    if top_candidate_age_days is not None:
        telemetry.record(
            CANDIDATE_AGE_DAYS,
            top_candidate_age_days,
            unit="d",
            attributes=attributes,
            description="Age of the highest-scoring candidate.",
        )


def record_injected_chars(
    *,
    backend: str,
    chars: int,
    component: str = "resident",
    environment_id: str = "",
) -> None:
    """Emit how much context prefetch actually injected into the prompt."""
    get_observability().record(
        INJECTED_CHARS,
        chars,
        attributes={ATTR_BACKEND: backend, **_identity(component, environment_id)},
        description="Characters of past context injected by prefetch.",
    )


def record_corpus(
    *,
    backend: str,
    episodes: int,
    embedding_coverage: float,
    index_coverage: float,
    component: str = "resident",
    environment_id: str = "",
) -> None:
    """Emit corpus-health gauges.

    Coverage ratios catch the silent failures that no per-call metric can:
    embeddings never generated, or episodes missing from the search index.
    """
    telemetry = get_observability()
    attributes = {ATTR_BACKEND: backend, **_identity(component, environment_id)}
    telemetry.gauge(
        CORPUS_EPISODES,
        episodes,
        attributes=attributes,
        description="Episodes stored.",
    )
    telemetry.gauge(
        CORPUS_EMBEDDING_COVERAGE,
        embedding_coverage,
        attributes=attributes,
        description="Fraction of episodes carrying an embedding.",
    )
    telemetry.gauge(
        CORPUS_INDEX_COVERAGE,
        index_coverage,
        attributes=attributes,
        description="Fraction of episodes present in the search index.",
    )


def record_resident_state_operation(
    *,
    operation: str,
    record_type: str,
    adapter: str,
    result: str,
    component: str = "resident",
    environment_id: str = "",
) -> None:
    """Emit one resident-state read or write."""
    get_observability().count(
        RESIDENT_STATE_OPERATIONS,
        attributes={
            ATTR_OPERATION: operation,
            ATTR_RECORD_TYPE: record_type,
            ATTR_ADAPTER: adapter,
            ATTR_RESULT: result,
            **_identity(component, environment_id),
        },
        description="Resident-state operations by record type and outcome.",
    )


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
    "ADMITTED",
    "ATTR_ADAPTER",
    "ATTR_BACKEND",
    "ATTR_COMPONENT",
    "ATTR_ENVIRONMENT",
    "ATTR_MIMIR_OPERATION",
    "ATTR_OPERATION",
    "ATTR_RECORD_TYPE",
    "ATTR_RESULT",
    "CANDIDATES",
    "CANDIDATE_AGE_DAYS",
    "CORPUS_EMBEDDING_COVERAGE",
    "CORPUS_EPISODES",
    "CORPUS_INDEX_COVERAGE",
    "DURATION",
    "INJECTED_CHARS",
    "MIMIR_OPERATIONS",
    "MIMIR_RESULTS",
    "OPERATIONS",
    "RELEVANCE_SCORE",
    "RESIDENT_STATE_OPERATIONS",
    "RESULT_EMPTY",
    "RESULT_ERROR",
    "RESULT_HIT",
    "record_corpus",
    "record_funnel",
    "record_injected_chars",
    "record_memory_operation",
    "record_mimir_operation",
    "record_resident_state_operation",
    "result_for",
]
