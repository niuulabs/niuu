"""One place where a configured limit being hit becomes visible.

A cap that is hit silently is the most expensive kind of fault this runtime
has: a reflection budget a reasoning model spent thinking produced no lesson
for days, a compaction document cut off mid-sentence replaced the history it
summarised, a truncated tool call ran with ``{}`` as its arguments. Each looked
healthy from the outside because nothing counted the hit.

Every cap that is still enforced reports here. :func:`record_cap_hit` logs at
error level with the knob that bounds it and the remedy, increments the
``ravn.cap.hits`` counter (attribute ``ravn.cap.knob``), and keeps a process
count that the resident health snapshot publishes next to its durable counts
(:meth:`ravn.resident_runtime.ResidentRuntime.health_snapshot`), so a
resident that keeps hitting a limit shows it on the scorecard instead of only
in logs. :func:`require_complete` is the check every model call makes on its
response; a ``StopReason.MAX_TOKENS`` answer is a cap hit wherever it happens.

The limits themselves, their defaults, and what bounds them are listed in
``docs/operator/ravn-limits.md``.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter

from niuu.observability import get_observability
from ravn.domain.exceptions import OutputTruncatedError
from ravn.domain.models import LLMResponse, StopReason

logger = logging.getLogger(__name__)

CAP_HITS = "ravn.cap.hits"
ATTR_KNOB = "ravn.cap.knob"
#: Prefix of the per-knob keys merged into the resident health snapshot.
HEALTH_KEY_PREFIX = "cap_hits."
HEALTH_TOTAL_KEY = "cap_hits_total"

DEGRADED = "ravn.degraded"
ATTR_PATH = "ravn.degraded.path"
DEGRADED_KEY_PREFIX = "degraded."
DEGRADED_TOTAL_KEY = "degraded_total"

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_degraded: Counter[str] = Counter()


def record_cap_hit(knob: str, message: str) -> None:
    """Report that the limit configured by *knob* was reached.

    *message* says what was cut or refused and how to fix it; it is logged at
    error level as given, so it must name the remedy.
    """
    with _lock:
        _counts[knob] += 1
    logger.error("cap hit [%s]: %s", knob, message)
    get_observability().count(
        CAP_HITS,
        attributes={ATTR_KNOB: knob},
        description="Configured Ravn limits that were reached (see docs/operator/ravn-limits.md).",
    )


def record_truncation(knob: str, *, what: str, kept: int, total: int, remedy: str = "") -> None:
    """Report that *what* was cut from *total* to *kept* characters by *knob*.

    For content a model or a durable record receives shortened. Deliberate
    excerpts whose prompt says it sees only a head do not call this; a limit
    that silently drops the rest does.
    """
    record_cap_hit(
        knob,
        f"{what} was cut from {total} to {kept} characters; "
        f"{remedy or f'raise {knob} if what was cut matters'}",
    )


def record_degraded(path: str, message: str) -> None:
    """Report that *path* carried on without part of what it should have done.

    For the few places where raising would lose more than continuing — an
    operator's reply that could not be saved still has to reach the resident —
    the degradation is an error log and a count on the health snapshot, never
    a silent continue.
    """
    with _lock:
        _degraded[path] += 1
    logger.error("degraded [%s]: %s", path, message)
    get_observability().count(
        DEGRADED,
        attributes={ATTR_PATH: path},
        description="Ravn paths that continued without completing their durable work.",
    )


def degraded_counts() -> dict[str, int]:
    """Degraded paths taken since process start, keyed by path."""
    with _lock:
        return dict(_degraded)


def cap_hit_counts() -> dict[str, int]:
    """Cap hits since process start, keyed by knob."""
    with _lock:
        return dict(_counts)


def health_counts() -> dict[str, int]:
    """Cap hits shaped for the resident health snapshot."""
    counts = cap_hit_counts()
    snapshot = {f"{HEALTH_KEY_PREFIX}{knob}": count for knob, count in sorted(counts.items())}
    snapshot[HEALTH_TOTAL_KEY] = sum(counts.values())
    degraded = degraded_counts()
    snapshot.update(
        {f"{DEGRADED_KEY_PREFIX}{path}": count for path, count in sorted(degraded.items())}
    )
    snapshot[DEGRADED_TOTAL_KEY] = sum(degraded.values())
    return snapshot


def reset_cap_hits() -> None:
    """Forget every recorded hit and degradation (tests and process re-initialisation only)."""
    with _lock:
        _counts.clear()
        _degraded.clear()


def require_complete(
    response: LLMResponse,
    *,
    knob: str,
    budget: int | None,
    model: str,
    purpose: str,
) -> LLMResponse:
    """Return *response* if the model finished; raise if it ran out of room.

    *knob* is the setting that bounds the call's output and *budget* its value
    (``None`` when no cap was sent, so the provider's own limit — the model's
    maximum output or the rest of the context window — is what ran out).

    Raises:
        OutputTruncatedError: ``response.stop_reason`` is ``MAX_TOKENS``.
    """
    if response.stop_reason != StopReason.MAX_TOKENS:
        return response
    if budget is None:
        cause = (
            f"reached the provider's output limit for {model!r} with no cap sent (the "
            "model's maximum output, or the rest of its context window)"
        )
        remedy = (
            "shorten what this call is given (context_management.max_prompt_tokens bounds "
            "the prompt), or use a model with a larger window"
        )
    else:
        cause = f"used its whole {budget}-token budget"
        remedy = f"raise {knob}, or set it to null to let the provider use the remaining context"
    message = (
        f"{purpose} was cut off [{knob}]: model {model!r} {cause} "
        f"(answer {len(response.content or '')} chars, reasoning "
        f"{len(response.reasoning or '')} chars, {len(response.tool_calls)} tool call(s)); "
        f"{remedy}"
    )
    record_cap_hit(knob, message)
    raise OutputTruncatedError(knob=knob, message=message)
