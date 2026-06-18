from __future__ import annotations

from ravn.domain.capability_resolution import (
    BuildMissingCapabilityPolicy,
    CapabilityPolicy,
    CapabilityResolver,
    WorkflowCapability,
    WorkflowSelector,
)
from ravn.valkyrie_evolution.models import OperationalSignal


def _signal() -> OperationalSignal:
    return OperationalSignal(
        signal_id="sig-1",
        event_type="signal.kubernetes.event",
        environment_id="ymir",
        domain="k8s",
        severity="warning",
        summary="pod restarted",
        payload={"source_id": "k8s-events", "kind": "pod", "reason": "crashloop"},
    )


def test_resolver_prefers_configured_local_skill_when_available() -> None:
    resolver = CapabilityResolver(
        [
            CapabilityPolicy(
                name="k8s-policy",
                signal_types=["signal.kubernetes.event"],
                local_skills=["inspect-k8s-crashloop"],
                remote_workflows=WorkflowSelector(tags=["incident"]),
            )
        ]
    )

    result = resolver.resolve(
        _signal(),
        local_skill_names=["inspect-k8s-crashloop"],
        workflows=[WorkflowCapability("wf-1", "Incident Investigation", tags=["incident"])],
    )

    assert result.decision == "handle_locally"
    assert result.local_skill == "inspect-k8s-crashloop"
    assert result.policy_name == "k8s-policy"


def test_resolver_selects_remote_workflow_from_existing_catalog() -> None:
    resolver = CapabilityResolver(
        [
            CapabilityPolicy(
                name="k8s-policy",
                severities=["warning", "critical"],
                remote_trigger_decisions=["needs_remote_research"],
                remote_workflows=WorkflowSelector(tags=["incident"]),
            )
        ]
    )

    result = resolver.resolve(
        _signal(),
        resident_decision="needs_remote_research",
        workflows=[WorkflowCapability("wf-1", "Incident Investigation", tags=["incident"])],
    )

    assert result.decision == "invoke_workflow"
    assert result.workflow is not None
    assert result.workflow.workflow_id == "wf-1"
    assert result.capability_name == "kubernetes.pod.crashloop"


def test_resolver_reports_when_remote_discovery_is_needed() -> None:
    resolver = CapabilityResolver(
        [
            CapabilityPolicy(
                name="k8s-policy",
                severities=["warning", "critical"],
                remote_trigger_decisions=["needs_remote_research"],
                remote_workflows=WorkflowSelector(tags=["incident"]),
            )
        ]
    )

    assert (
        resolver.needs_remote_workflows(
            _signal(),
            resident_decision="needs_remote_research",
        )
        is True
    )
    assert (
        resolver.needs_remote_workflows(
            _signal(),
            resident_decision="ignore",
        )
        is False
    )


def test_resolver_can_fall_back_to_builder_workflow() -> None:
    resolver = CapabilityResolver(
        [
            CapabilityPolicy(
                name="k8s-policy",
                remote_workflows=WorkflowSelector(tags=["missing-direct-workflow"]),
                remote_trigger_decisions=["defer_to_builder"],
                build_missing_capability=BuildMissingCapabilityPolicy(
                    enabled=True,
                    workflow=WorkflowSelector(tags=["tool-builder"]),
                    requires_approval=True,
                ),
            )
        ]
    )

    result = resolver.resolve(
        _signal(),
        resident_decision="defer_to_builder",
        workflows=[WorkflowCapability("wf-build", "Tool Builder", tags=["tool-builder"])],
    )

    assert result.decision == "build_missing_capability"
    assert result.requires_approval is True
    assert result.workflow is not None
    assert result.workflow.name == "Tool Builder"


def test_resolver_declines_when_policy_matches_without_capability() -> None:
    resolver = CapabilityResolver(
        [CapabilityPolicy(name="k8s-policy", signal_types=["signal.kubernetes.event"])]
    )

    result = resolver.resolve(_signal())

    assert result.decision == "decline"
    assert result.policy_name == "k8s-policy"
    assert "no allowed capability" in result.reason
