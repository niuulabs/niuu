"""Validate a trigger's ``kind``/``spec`` before it is durably stored.

A trigger the resident can never schedule or fire is worse than an API error:
it looks accepted, then silently never runs. ``POST /api/v1/ravn/triggers``
calls :func:`validate_trigger_spec` and returns 422 on failure instead of
persisting a row nothing can execute.
"""

from __future__ import annotations

from ravn.adapters.triggers.cron import (
    CRON_FIELD_COUNT,
    CronExpressionError,
    parse_schedule,
    validate_cron_fields,
)
from sleipnir.domain.registry import known_event_types

#: Kinds an ``ApiTriggerSource`` (ravn.adapters.triggers.api_source) can
#: actually run. Keep this in lockstep with that module's dispatch.
SUPPORTED_TRIGGER_KINDS = frozenset({"cron", "event"})


class TriggerValidationError(ValueError):
    """Raised when a trigger's kind/spec cannot be scheduled or executed."""


def validate_trigger_spec(kind: str, spec: str, *, repo: str = "") -> None:
    """Raise :class:`TriggerValidationError` when *kind*/*spec* cannot fire.

    ``kind`` must be one ``ApiTriggerSource`` actually executes; ``spec``
    must parse as a real schedule (cron) or name a Sleipnir event type
    (event). An event trigger also requires *repo* — without it, nothing
    scopes which repository's events this trigger should react to, and every
    tenant's events for that event type would match (see
    ``ravn.adapters.triggers.api_source``'s payload filter).
    """
    if kind not in SUPPORTED_TRIGGER_KINDS:
        raise TriggerValidationError(
            f"unsupported trigger kind {kind!r}; supported kinds are "
            f"{sorted(SUPPORTED_TRIGGER_KINDS)}"
        )
    if kind == "cron":
        _validate_cron_spec(spec)
        return
    _validate_event_spec(spec, repo=repo)


def _validate_cron_spec(spec: str) -> None:
    schedule = spec.strip()
    if not schedule:
        raise TriggerValidationError("cron trigger spec must not be empty")

    canonical = parse_schedule(schedule)
    if canonical.startswith(("every:", "once:")):
        return  # parse_schedule only builds these from its own regexes/ISO
        # parsing, so a match there is already well-formed.

    if len(canonical.split()) != CRON_FIELD_COUNT:
        raise TriggerValidationError(
            f"cron trigger spec {spec!r} is not a valid schedule — expected a 5-field cron "
            "expression, 'every <n><s|m|h>', 'daily at HH:MM', or an ISO timestamp"
        )
    # Field-shape/range checks, plus exercising the scheduler's own matcher
    # (ravn.adapters.triggers.cron._cron_matches) rather than trusting those
    # checks to stay in lockstep with it forever — they already drifted
    # once: a range-with-step field like "1-5/2" passed every check above
    # but raised ValueError inside the real matcher (a parsing-order bug,
    # long since fixed), permanently killing that resident's scheduler tick
    # the first time the cron loop evaluated it. Calling the real function
    # here means any future divergence is caught as a 422 at creation time
    # instead of a runtime crash. Shared with cron_tools.CronCreateTool so
    # a job created through either path gets the identical guarantee.
    try:
        validate_cron_fields(canonical, spec=spec)
    except CronExpressionError as exc:
        raise TriggerValidationError(str(exc)) from exc


def _validate_event_spec(spec: str, *, repo: str) -> None:
    event_type = spec.strip()
    if not event_type:
        raise TriggerValidationError("event trigger spec must not be empty")
    if event_type not in known_event_types():
        raise TriggerValidationError(
            f"event trigger spec {event_type!r} is not a known Sleipnir event type "
            "(see sleipnir.domain.registry) — check the exact event name, e.g. "
            "'github.pr.opened', not 'github.pull_request.opened'"
        )
    if not repo.strip():
        raise TriggerValidationError(
            "event triggers require a repo — without one, this trigger would fire on "
            f"every tenant's {event_type!r} events, not just this realm's"
        )


def check_repo_allowlisted(*, tenant_id: str, repo: str, grants: dict[str, list[str]]) -> None:
    """Reject an event trigger whose repo the caller's tenant was never
    granted — see :class:`ravn.config.TriggerRepoAllowlistConfig` for why
    this check exists at all (no live per-tenant repo-ownership signal to
    check the free-text ``repo`` field against). Call this in addition to,
    not instead of, :func:`validate_trigger_spec`.

    A tenant's grant list containing ``"*"`` allows any repo for that
    tenant — an explicit operator opt-out, never a default.
    """
    allowed = grants.get(tenant_id, [])
    if "*" in allowed or repo in allowed:
        return
    raise TriggerValidationError(
        f"tenant {tenant_id!r} is not granted repo {repo!r} for event triggers — "
        "an operator must add it to trigger_repo_allowlist.grants in this Ravn "
        "API's config before triggers can reference it"
    )
