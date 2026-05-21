"""Tests for the Sleipnir event type constant registry."""

from __future__ import annotations

import sleipnir.domain.registry as registry
from sleipnir.domain.events import EVENT_NAMESPACES, validate_event_type


def _all_constants() -> list[tuple[str, str]]:
    """Return all (name, value) pairs that look like event type constants."""
    return [
        (name, value)
        for name, value in vars(registry).items()
        if not name.startswith("_") and isinstance(value, str) and "." in value
    ]


# ---------------------------------------------------------------------------
# Constant presence by namespace
# ---------------------------------------------------------------------------


def test_ravn_constants_present():
    assert registry.RAVN_TOOL_CALL == "ravn.tool.call"
    assert registry.RAVN_TOOL_COMPLETE == "ravn.tool.complete"
    assert registry.RAVN_TOOL_ERROR == "ravn.tool.error"
    assert registry.RAVN_STEP_START == "ravn.step.start"
    assert registry.RAVN_STEP_COMPLETE == "ravn.step.complete"
    assert registry.RAVN_SESSION_START == "ravn.session.start"
    assert registry.RAVN_SESSION_END == "ravn.session.end"
    assert registry.RAVN_RESPONSE_COMPLETE == "ravn.response.complete"
    assert registry.RAVN_INTERRUPT == "ravn.interrupt"


def test_ting_constants_present():
    assert registry.TING_TASK_QUEUED == "ting.task.queued"
    assert registry.TING_TASK_STARTED == "ting.task.started"
    assert registry.TING_TASK_COMPLETE == "ting.task.complete"
    assert registry.TING_TASK_FAILED == "ting.task.failed"
    assert registry.TING_TASK_CANCELLED == "ting.task.cancelled"
    assert registry.TING_SAGA_CREATED == "ting.saga.created"
    assert registry.TING_SAGA_STEP == "ting.saga.step"
    assert registry.TING_SAGA_COMPLETE == "ting.saga.complete"
    assert registry.TING_SAGA_FAILED == "ting.saga.failed"
    assert registry.TING_SESSION_START == "ting.session.start"
    assert registry.TING_SESSION_END == "ting.session.end"


def test_volundr_constants_present():
    assert registry.VOLUNDR_PR_OPENED == "volundr.pr.opened"
    assert registry.VOLUNDR_PR_CLOSED == "volundr.pr.closed"
    assert registry.VOLUNDR_PR_REVIEWED == "volundr.pr.reviewed"
    assert registry.VOLUNDR_REPO_REGISTERED == "volundr.repo.registered"
    assert registry.VOLUNDR_REPO_REMOVED == "volundr.repo.removed"
    assert registry.VOLUNDR_PIPELINE_STARTED == "volundr.pipeline.started"
    assert registry.VOLUNDR_PIPELINE_COMPLETE == "volundr.pipeline.complete"
    assert registry.VOLUNDR_PIPELINE_FAILED == "volundr.pipeline.failed"


def test_volundr_session_constants_present():
    """New session/token/chronicle event type constants added in NIU-466."""
    assert registry.VOLUNDR_SESSION_CREATED == "volundr.session.created"
    assert registry.VOLUNDR_SESSION_STARTED == "volundr.session.started"
    assert registry.VOLUNDR_SESSION_STOPPED == "volundr.session.stopped"
    assert registry.VOLUNDR_SESSION_FAILED == "volundr.session.failed"
    assert registry.VOLUNDR_TOKEN_USAGE == "volundr.token.usage"
    assert registry.VOLUNDR_CHRONICLE_CREATED == "volundr.chronicle.created"
    assert registry.VOLUNDR_CHRONICLE_UPDATED == "volundr.chronicle.updated"


def test_bifrost_constants_present():
    assert registry.BIFROST_CONNECTION_OPEN == "bifrost.connection.open"
    assert registry.BIFROST_CONNECTION_CLOSE == "bifrost.connection.close"
    assert registry.BIFROST_ROUTE_SELECTED == "bifrost.route.selected"
    assert registry.BIFROST_AUTH_SUCCESS == "bifrost.auth.success"
    assert registry.BIFROST_AUTH_FAILURE == "bifrost.auth.failure"
    assert registry.BIFROST_RATE_LIMITED == "bifrost.rate.limited"


def test_bifrost_cost_quota_provider_constants_present():
    """New cost/quota/provider event type constants added in NIU-526."""
    assert registry.BIFROST_REQUEST_COMPLETE == "bifrost.request.complete"
    assert registry.BIFROST_QUOTA_WARNING == "bifrost.quota.warning"
    assert registry.BIFROST_QUOTA_EXCEEDED == "bifrost.quota.exceeded"
    assert registry.BIFROST_PROVIDER_DOWN == "bifrost.provider.down"
    assert registry.BIFROST_PROVIDER_RECOVERED == "bifrost.provider.recovered"


def test_system_constants_present():
    assert registry.SYSTEM_HEALTH_PING == "system.health.ping"
    assert registry.SYSTEM_SERVICE_STARTED == "system.service.started"
    assert registry.SYSTEM_SERVICE_STOPPING == "system.service.stopping"
    assert registry.SYSTEM_CONFIG_RELOADED == "system.config.reloaded"
    assert registry.SYSTEM_ERROR == "system.error"
    assert registry.SYSTEM_METRIC == "system.metric"


# ---------------------------------------------------------------------------
# All namespaces represented
# ---------------------------------------------------------------------------


def test_all_namespaces_have_at_least_one_constant():
    values = {v for _, v in _all_constants()}
    for ns in EVENT_NAMESPACES:
        assert any(v.startswith(f"{ns}.") for v in values), (
            f"No constants found for namespace {ns!r}"
        )


# ---------------------------------------------------------------------------
# All constants are valid event type strings
# ---------------------------------------------------------------------------


def test_all_constants_pass_format_validation():
    for name, value in _all_constants():
        try:
            validate_event_type(value)
        except ValueError as exc:
            raise AssertionError(
                f"Constant {name}={value!r} failed format validation: {exc}"
            ) from exc


def test_constants_are_strings_not_magic_numbers():
    for name, value in _all_constants():
        assert isinstance(value, str), f"{name} must be a str, got {type(value)}"
