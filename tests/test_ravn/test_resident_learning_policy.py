"""Peer-adoption relevance gate: ResidentLearningPolicy.evaluate (NIU-1034)."""

from __future__ import annotations

import pytest

from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningPolicy,
)


def _identity() -> ResidentLearningIdentity:
    return ResidentLearningIdentity(
        environment_id="cluster-b",
        valkyrie_id="valkyrie:k8s-b",
        domain="k8s",
        flock_ids=["flock:k8s-valkyries"],
        autonomy_mode="autonomous",
    )


def _artifact(**overrides) -> ResidentLearningArtifact:
    data = {
        "learning_id": "learn-1",
        "title": "valkyrie-probe",
        "summary": "a probe",
        "content": "# skill: valkyrie-probe\n\nmetadata:\n  capability: x\n",
        "artifact_type": "ravn_skill_tool",
        "scope": "flock",
        "confidence": 0.9,
        "source_environment_id": "cluster-a",
        "source_valkyrie_id": "valkyrie:k8s-a",
        "flock_id": "flock:k8s-valkyries",
        "domain": "k8s",
        "redaction_status": "redacted",
    }
    data.update(overrides)
    return ResidentLearningArtifact(**data)


def test_relevant_flock_learning_is_accepted() -> None:
    ok, reason = ResidentLearningPolicy().evaluate(_artifact(), _identity())
    assert ok
    assert "relevant to cluster-b" in reason


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"source_valkyrie_id": "valkyrie:k8s-b"}, "source Valkyrie already owns"),
        ({"artifact_type": "mystery"}, "unsupported artifact type"),
        ({"content": "   "}, "artifact content unavailable"),
        ({"redaction_status": "raw"}, "has not been redacted"),
        ({"scope": "private"}, "private learning does not travel"),
        ({"scope": "environment"}, "belongs to another environment"),
        ({"scope": "domain", "domain": "home"}, "domain does not match"),
        ({"flock_id": "printer-cell"}, "not a member of the target flock"),
        ({"scope": "shared", "domain": "home"}, "shared learning is for a different domain"),
    ],
)
def test_irrelevant_learnings_are_rejected_with_a_reason(overrides, expected) -> None:
    ok, reason = ResidentLearningPolicy().evaluate(_artifact(**overrides), _identity())
    assert not ok
    assert expected in reason


def test_agent_tool_skips_the_skill_content_requirement() -> None:
    # agent tools carry executable code, not skill markdown, so empty content
    # is fine for them.
    ok, _reason = ResidentLearningPolicy().evaluate(
        _artifact(artifact_type="agent_tool", content="", tool_code="def run(x):\n    return {}\n"),
        _identity(),
    )
    assert ok


def _event(event_type: str, payload: dict | None = None):
    from sleipnir.domain.events import SleipnirEvent

    return SleipnirEvent(
        event_type=event_type,
        source="test",
        payload=payload or {},
        summary="",
        urgency=0.5,
        domain="infrastructure",
        timestamp=SleipnirEvent.now(),
    )


def test_pure_relevance_and_event_helpers() -> None:
    from ravn.valkyrie_evolution.resident_learning import (
        _adoption_event_action,
        _domain_matches,
        _flock_matches,
        _is_retraction_event,
        _to_operational_signal,
    )
    from sleipnir.domain import registry

    assert _adoption_event_action("ignored") == "rejected"
    assert _adoption_event_action("rolled_back") == "regressed"
    assert _adoption_event_action("adopted") == "adopted"

    assert _domain_matches("", "k8s") and _domain_matches("k8s", "")
    assert _domain_matches("k8s", "k8s")
    assert not _domain_matches("home", "k8s")
    assert _flock_matches("k8s-valkyries", ["flock:k8s-valkyries"])
    assert not _flock_matches("", ["flock:k8s-valkyries"])
    assert not _flock_matches("printer", ["flock:k8s-valkyries"])

    assert _is_retraction_event(_event(registry.FLOCK_LEARNING_ROLLED_BACK))
    assert _is_retraction_event(
        _event(registry.LEARNING_PROMOTED, {"action_kind": "demote", "to_scope": "private"})
    )
    assert not _is_retraction_event(
        _event(registry.LEARNING_PROMOTED, {"action_kind": "promote", "to_scope": "environment"})
    )

    signal = _to_operational_signal(
        _event("signal.kubernetes.event", {"data": {"signal_id": "sig-9", "severity": "warning"}}),
        _identity(),
    )
    assert signal.signal_id == "sig-9"
