"""Shared additive reporting of fan-out source failures.

Merged list endpoints (Guild's Forge/Ravn aggregate routers, Ravn's own
resident directory) must not silently drop a source's contribution when it
fails — an unreachable instance, or a broken discovery adapter, must not
look identical to one that simply has nothing to report. This module is the
one place that shape lives, in a package neutral to all of its callers
(niuu's Guild routers *and* ravn's own API), so every aggregate reports
failures the same way: additively, via the ``X-Niuu-Source-Failures``
response header, mirroring ``TopologySourceHealth``
(``niuu/domain/services/observatory_topology.py``). See
``.claude/rules/no-fallbacks.md``.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

import httpx
from fastapi import Response

from niuu.domain.models import RegisteredInstance

SOURCE_FAILURES_HEADER = "X-Niuu-Source-Failures"

#: Keeps the header well within typical proxy/server header-size limits even
#: when a large fleet is mostly down.
_MAX_ENTRIES = 20
_MAX_ERROR_LENGTH = 200


class SourceFailure(TypedDict):
    """One failed contributor to a merged list — additive, never the whole story."""

    instanceId: str
    name: str
    status: str
    error: str


def describe_remote_failure(result: Any) -> str:
    """Describe a fanned-out remote call's failure, never an empty string.

    Several httpx transport errors stringify to "", which would otherwise
    turn every unreachable instance's failure into a blank message.
    """
    if isinstance(result, Exception):
        return str(result) or result.__class__.__name__
    return f"HTTP {result.status_code}"


def source_failure(
    *, instance_id: str, name: str, error: str, status: str = "unreachable"
) -> SourceFailure:
    """Build one failure entry, truncating the error to a bounded size."""
    return {
        "instanceId": instance_id,
        "name": name,
        "status": status,
        "error": error[:_MAX_ERROR_LENGTH],
    }


def instance_source_failures(
    instances: list[RegisteredInstance],
    results: list[Any],
) -> list[SourceFailure]:
    """Which registered instances a fan-out call failed to reach, and why."""
    failures: list[SourceFailure] = []
    for instance, result in zip(instances, results, strict=False):
        if isinstance(result, Exception) or (
            isinstance(result, httpx.Response) and result.status_code >= 400
        ):
            failures.append(
                source_failure(
                    instance_id=instance.id,
                    name=instance.name,
                    error=describe_remote_failure(result),
                )
            )
    return failures


def set_source_health_header(response: Response, failures: list[SourceFailure]) -> None:
    """Report fan-out failures on the response without changing its body shape.

    A header keeps this strictly additive: existing consumers of the bare
    list body are unaffected, while a caller that wants to know which
    sources were unreachable can read ``X-Niuu-Source-Failures``. Capped in
    both entry count and per-error length so a mostly-down fleet cannot blow
    past typical proxy/server header-size limits.
    """
    if not failures:
        return
    response.headers[SOURCE_FAILURES_HEADER] = json.dumps(failures[:_MAX_ENTRIES])
