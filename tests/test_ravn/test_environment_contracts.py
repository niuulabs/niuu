"""Tests for resident Valkyrie Environment contracts."""

from __future__ import annotations

from observatory.data import REGISTRY
from ravn.config import EnvironmentConfig
from ravn.domain.environment import (
    Environment,
    apply_environment_metadata,
    example_environments,
    inbox_environment_fixture,
    k8s_environment_fixture,
    printer_environment_fixture,
)
from sleipnir.domain.catalog import learning_promoted, signal_received
from sleipnir.domain.events import SleipnirEvent


def test_environment_examples_share_one_serializable_model() -> None:
    environments = example_environments()

    assert {environment.type for environment in environments} == {
        "k8s",
        "host.inbox",
        "printer.pi",
    }
    for environment in environments:
        dumped = environment.model_dump_json()
        loaded = Environment.model_validate_json(dumped)

        assert loaded.id == environment.id
        assert loaded.tenant_id
        assert loaded.realm_id
        assert loaded.signal_sources
        assert loaded.operational_state.health in {
            "nominal",
            "watching",
            "degraded",
            "incident",
            "recovering",
            "maintenance",
            "unknown",
        }
        assert loaded.action_capabilities
        assert loaded.wakefulness.wakefulness_enabled is True
        assert loaded.flock_memberships
        assert loaded.learning_scopes


def test_environment_projects_to_existing_ravn_config_and_derived_nats_subjects() -> None:
    environment = k8s_environment_fixture()
    config = EnvironmentConfig(**environment.to_ravn_environment_config())

    assert config.id == "cluster-prod-a"
    assert config.type == "k8s"
    assert config.flocks == ["k8s-valkyries"]
    assert [source.id for source in config.signal_sources] == [
        "kubernetes-events",
        "prometheus-alerts",
    ]
    assert config.signal_sources[0].adapter == "ravn.adapters.environment.kubernetes"
    assert config.signal_sources[0].kwargs == {
        "metadata": {"cluster": "prod-a"},
        "replay_cursor": "",
    }
    assert config.signal_subjects == []
    assert environment.signal_subjects() == [
        "ravn.environment.signal.kubernetes.*",
        "ravn.environment.environment.state",
        "ravn.environment.signal.metrics.*",
    ]
    assert environment.subject_for_event_type("valkyrie.judgment.proposed") == (
        "ravn.environment.valkyrie.judgment.proposed"
    )


def test_environment_projects_to_existing_skuld_participant_metadata() -> None:
    environment = printer_environment_fixture()
    participant = environment.to_participant_meta(persona="printer-pi-valkyrie")

    assert participant.peer_id == "valkyrie:printer-cell-basement"
    assert participant.participant_type == "ravn"
    assert participant.participant_kind == "valkyrie"
    assert participant.environment_id == "printer-cell-basement"
    assert participant.wakefulness == "watching"
    assert participant.authority_role == "autonomous"
    assert participant.room_ids == ("environment:printer-cell-basement",)
    assert "printer.pause_print" in participant.capabilities
    assert "ravn.environment.signal.printer.*" in participant.subscribes_to


def test_environment_projects_to_existing_ting_flock_and_mimir_scopes() -> None:
    environment = k8s_environment_fixture()
    refs = environment.to_ting_flock_refs()

    assert refs == [
        {
            "flock_id": "k8s-valkyries",
            "role": "cluster-resident",
            "ting_flow": "k8s-environment-flock",
            "learning_exchange_subject": "flock.k8s-valkyries.learning.*",
            "adoption_policy": "canary",
            "peer_environment_ids": ["cluster-prod-b"],
        }
    ]
    assert environment.mimir_mount_names == [
        "valkyrie-private-cluster-prod-a",
        "environment-cluster-prod-a",
        "flock-k8s-valkyries",
    ]


def test_environment_metadata_flows_through_sleipnir_event_roundtrip() -> None:
    environment = inbox_environment_fixture()
    event = signal_received(
        environment_id=environment.id,
        environment_type=environment.type,
        signal_source="gmail.inbox",
        signal_kind="inbox",
        severity="info",
        data={"from": "customer@example.com", "subject": "Renewal"},
        source="adapter:gmail",
        correlation_id="corr-inbox-renewal",
    )

    enriched = apply_environment_metadata(
        event,
        environment,
        root_correlation_id="corr-inbox-renewal",
    )
    round_tripped = SleipnirEvent.from_dict(enriched.to_dict())

    assert round_tripped.tenant_id == "niuu"
    assert round_tripped.payload["environment_id"] == "host-jozef-mail"
    assert round_tripped.payload["environment_type"] == "host.inbox"
    assert round_tripped.payload["realm_id"] == "personal"
    assert round_tripped.payload["topology_ref"]["type_id"] == "host"
    assert round_tripped.payload["nats_subject"] == "ravn.environment.signal.inbox.message"
    assert round_tripped.payload["root_correlation_id"] == "corr-inbox-renewal"


def test_environment_metadata_covers_learning_provenance() -> None:
    environment = k8s_environment_fixture()
    event = learning_promoted(
        environment_id=environment.id,
        learning_id="learn-oom-normal",
        from_scope="environment",
        to_scope="flock",
        summary="Recovered OOM restarts can stay in watch before escalation.",
        confidence=0.82,
        source="valkyrie:k8s-prod-a",
        correlation_id="corr-oom",
    )

    enriched = apply_environment_metadata(event, environment)

    assert enriched.payload["environment_id"] == "cluster-prod-a"
    assert enriched.payload["flock_ids"] == ["k8s-valkyries"]
    assert enriched.payload["learning_scopes"] == [
        "private:cluster-prod-a",
        "environment:cluster-prod-a",
        "flock:k8s-valkyries",
    ]
    assert enriched.payload["nats_subject"] == "ravn.environment.learning.promoted"


def test_environment_projects_into_existing_observatory_topology_types() -> None:
    known_types = {item["id"] for item in REGISTRY["types"]}  # type: ignore[index]

    for environment in example_environments():
        fragment = environment.to_observatory_fragment()
        nodes = fragment["nodes"]
        edges = fragment["edges"]
        meta = fragment["meta"]
        valkyrie_node_id = f"valkyrie:{environment.resident_valkyrie_ids[0]}"

        assert meta["sourceKind"] == "environment"
        assert meta["realmId"] == environment.realm_id
        assert nodes[0]["sourceId"] == environment.id
        assert nodes[0]["typeId"] in known_types
        assert nodes[1]["typeId"] == "valkyrie"
        assert nodes[1]["typeId"] in known_types
        assert edges == [
            {
                "id": f"{environment.topology.node_id}->{valkyrie_node_id}",
                "sourceId": environment.topology.node_id,
                "targetId": valkyrie_node_id,
                "kind": "soft",
                "relationType": "manages",
                "label": "manages",
                "confidence": "declared",
                "evidence": {
                    "adapter": "environment",
                    "field": "Environment.resident_valkyrie_ids",
                },
            }
        ]


def test_environment_vocabulary_rejects_unknown_values_with_known_list() -> None:
    import pytest

    from ravn.domain.environment import environment_vocabulary

    vocabulary = environment_vocabulary()
    try:
        with pytest.raises(ValueError, match="unknown environment type 'submarine'"):
            Environment(
                id="env-x",
                name="X",
                type="submarine",
                topology=k8s_environment_fixture().topology,
            )
        with pytest.raises(ValueError, match="must not be empty"):
            vocabulary.validate_signal_source_kind("")
    finally:
        vocabulary.reset()


def test_environment_vocabulary_extends_from_config() -> None:
    from ravn.domain.environment import SignalSource, environment_vocabulary

    vocabulary = environment_vocabulary()
    try:
        EnvironmentConfig(
            id="cnc-cell-1",
            name="CNC Cell",
            type="local",
            vocabulary={
                "environment_types": ["cnc.cell"],
                "signal_source_kinds": ["cnc_telemetry"],
                "operational_health_states": ["calibrating"],
            },
        )
        environment = Environment(
            id="cnc-cell-1",
            name="CNC Cell",
            type="CNC.Cell",
            topology=k8s_environment_fixture().topology,
            signal_sources=[SignalSource(id="cnc", name="CNC Telemetry", kind="cnc_telemetry")],
        )
        assert environment.type == "cnc.cell"
        assert environment.signal_sources[0].kind == "cnc_telemetry"
        assert "calibrating" in vocabulary.operational_health_states
    finally:
        vocabulary.reset()
