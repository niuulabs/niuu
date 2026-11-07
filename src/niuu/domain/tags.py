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


MATCH_ALL = "all"
MATCH_ANY = "any"
MATCH_MODES = (MATCH_ALL, MATCH_ANY)


def validate_match(match: str) -> str:
    """Return ``match`` when it is a known mode; raise ``ValueError`` otherwise.

    ``matches_tags`` reads anything other than ``any`` as ``all``. The host-selector
    functions below hand ``match`` on to a Forge that enforces it, so a typo must not
    become a different selector on the way.
    """
    if match not in MATCH_MODES:
        raise ValueError(f"unknown tag match mode {match!r}; use one of {list(MATCH_MODES)}")
    return match


def residual_selector(
    instance_tags: Iterable[str],
    required: Iterable[str] | None,
    match: str = MATCH_ALL,
) -> tuple[tuple[str, ...], str] | None:
    """The part of a selector the Forge must still satisfy with one of its hosts.

    An instance (a Forge) carries its own ``tags``; the tags of its hosts are not
    visible to the caller. What the instance tags already cover is not asked of the
    hosts again:

    * ``all`` -- the required tags minus the instance tags, or ``None`` when the
      instance tags cover every one of them.
    * ``any`` -- ``None`` when an instance tag already matches, otherwise the whole
      required set (one of the host's tags must match).

    An empty selector leaves nothing to satisfy. The tags come back sorted so the
    selector is deterministic. ``ValueError`` for an unknown ``match``.
    """
    validate_match(match)
    required_set = set(required or ())
    if not required_set:
        return None
    have_set = set(instance_tags)
    if match == MATCH_ANY:
        if have_set & required_set:
            return None
        return tuple(sorted(required_set)), MATCH_ANY
    remaining = required_set - have_set
    if not remaining:
        return None
    return tuple(sorted(remaining)), MATCH_ALL


def satisfiable(
    instance_tags: Iterable[str],
    host_tag_sets: Iterable[Iterable[str]],
    required: Iterable[str] | None,
    match: str = MATCH_ALL,
) -> bool:
    """True when the instance can serve ``required`` through its tags and one host.

    That is: the instance tags alone satisfy the selector, or the instance tags
    together with ONE host's tag set do. The tag sets of different hosts are never
    combined: ``all`` over a union would accept a Forge with one ``gpu`` host and
    another ``arm64`` host for ``[gpu, arm64]``, which no single host can serve.
    """
    validate_match(match)
    required_set = set(required or ())
    instance_set = set(instance_tags)
    if matches_tags(instance_set, required_set, match):
        return True
    return any(
        matches_tags(instance_set | set(host_tags), required_set, match)
        for host_tags in host_tag_sets
    )
