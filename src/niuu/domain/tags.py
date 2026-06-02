"""Shared tag-selector matching for label-based targeting.

Used by the instance registry (which backends are visible), the guild Volundr
aggregator (which backend a session targets), and ting dispatch (which backend a
flock/workflow runs on) — one source of truth for the match semantics.
"""

from __future__ import annotations

from collections.abc import Iterable


def matches_tags(
    have: Iterable[str],
    required: Iterable[str] | None,
    match: str = "all",
) -> bool:
    """Return True if ``have`` satisfies the ``required`` tag selector.

    ``match="all"`` (default) requires every required tag to be present —
    Kubernetes-style equality selector semantics; ``match="any"`` requires at
    least one. An empty or ``None`` selector matches everything.
    """
    required_set = set(required or ())
    if not required_set:
        return True
    have_set = set(have)
    if match == "any":
        return bool(have_set & required_set)
    return required_set <= have_set
