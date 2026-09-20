"""Real signed document evidence through the existing Skuld graph node path."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from niuu.domain.workflow_evidence import validate_evidence_gate_nodes
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.main import create_evidence_artifacts, create_evidence_verifier
from tests.test_niuu.test_evidence import (
    _document_bundle,
    _policy,
    _sign,
    authenticator,
    key_pem,
)

__all__ = ["authenticator", "key_pem"]
DOCUMENT_TEXT = "# Retention policy\n\nKeep completed records for 30 days.\n"


def document_bundle(authenticator):
    bundle = _document_bundle(authenticator, execution_id="session-document")
    identity = bundle.identity.model_copy(
        update={
            "artifact_digest": sha256(DOCUMENT_TEXT.encode()).hexdigest(),
        }
    )

    def bind(receipt):
        return _sign(
            receipt.model_copy(update={"identity": identity}),
            authenticator,
            receipt.provenance.producer_id,
        )

    return bundle.model_copy(
        update={
            "identity": identity,
            "results": tuple(bind(r) for r in bundle.results),
            "reviews": tuple(bind(r) for r in bundle.reviews),
            "checks": bind(bundle.checks),
        }
    )


def graph():
    return {
        "nodes": [
            {"id": "document", "kind": "trigger"},
            {
                "id": "verify",
                "kind": "gate",
                "mode": "evidence",
                "label": "Verify document",
                "artifact": {"kind": "document", "id": "policy/retention-v3"},
                "evidencePolicy": _policy().model_dump(mode="json"),
                "approvalEvent": "document.accepted",
                "changesRequestedEvent": "document.rejected",
            },
            {
                "id": "done",
                "kind": "end",
                "label": "Accepted document",
                "joinMode": "all",
                "passingVerdicts": ["pass"],
                "completionEvent": "document.completed",
            },
            {"id": "repair", "kind": "stage", "label": "Revise document"},
        ],
        "edges": [
            {
                "id": "validate",
                "source": "document",
                "target": "verify",
                "label": "document.ready -> document.ready",
            },
            {
                "id": "accept",
                "source": "verify",
                "target": "done",
                "label": "document.accepted -> document.accepted",
            },
            {
                "id": "reject",
                "source": "verify",
                "target": "repair",
                "label": "document.rejected -> document.rejected",
            },
        ],
    }


@pytest.fixture
def runtime(tmp_path, key_pem):
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy/retention-v3").write_text(DOCUMENT_TEXT)
    settings = SkuldSettings(
        session={"id": "session-document", "workspace_dir": str(tmp_path)},
        workflow={
            "graph": graph(),
            "evidence_artifacts": {
                "adapter": "niuu.adapters.artifact_digest.FilesystemArtifactDigestResolver",
                "kwargs": {"root": str(tmp_path)},
            },
            "evidence_verifier": {
                "adapter": "niuu.adapters.evidence_gate.ConfiguredEvidenceGateVerifier",
                "kwargs": {
                    "authenticator_adapter": (
                        "niuu.adapters.evidence_signing.RsaEvidenceAuthenticator"
                    ),
                    "authenticator_kwargs": {
                        "key_id": "document-key",
                        "private_key_pem": key_pem,
                        "producer_keys": {
                            "document-validator": ["document-key"],
                            "independent-reviewer": ["document-key"],
                        },
                    },
                    "trusted_producers": ["document-validator", "independent-reviewer"],
                },
            },
        },
    )
    broker = Broker(
        settings,
        evidence_verifier=create_evidence_verifier(settings),
        evidence_artifacts=create_evidence_artifacts(settings),
    )
    broker._mesh_adapter = AsyncMock()
    broker._emit_pipeline_event = AsyncMock()
    broker._emit_broker_frame = AsyncMock()
    broker._maybe_report_flock_completion = AsyncMock()
    return broker


async def deliver(runtime, evidence, *, activation="attempt-2", valid=True):
    await runtime._maybe_activate_workflow_gate(
        {"root_correlation_id": activation},
        {"event_type": "document.ready", "valid": valid, "fields": {"evidence": evidence}},
    )
    return runtime._mesh_adapter.publish.call_args.args[0].payload


async def test_document_evidence_gate_routes_acceptance_without_model_or_human(
    runtime, authenticator
):
    result = await deliver(runtime, document_bundle(authenticator).model_dump(mode="json"))
    assert result["event_type"] == "document.accepted"
    assert result["workflow_node_id"] == "verify"
    assert result["fields"]["evidence_report"]["accepted"] is True
    public = runtime._emit_broker_frame.call_args.args[0]
    assert public["type"] == "room_outcome"
    assert public["workflowNodeId"] == "verify"
    assert public["fields"]["evidence_report"]["accepted"] is True
    assert runtime._emit_pipeline_event.call_args_list[0].args == ("outcome", result)
    completion = runtime._emit_pipeline_event.call_args_list[-1].args[1]
    assert completion["event_type"] == "document.completed"
    assert completion["workflow_node_id"] == "done"
    runtime._maybe_report_flock_completion.assert_awaited_once()
    assert runtime.list_workflow_gates() == []


@pytest.mark.parametrize("defect", ["stale", "tamper", "missing", "invalid", "foreign_execution"])
async def test_document_evidence_rejections_follow_failure_edge(runtime, authenticator, defect):
    evidence = document_bundle(authenticator).model_dump(mode="json")
    if defect == "foreign_execution":
        evidence["identity"]["execution_id"] = "another-session"
    if defect == "tamper":
        evidence["reviews"][0]["summary"] = "Tampered after signing"
    if defect == "missing":
        evidence["reviews"] = []
    result = await deliver(
        runtime,
        evidence,
        activation="new-attempt" if defect == "stale" else "attempt-2",
        valid=defect != "invalid",
    )
    assert result["event_type"] == "document.rejected"
    assert result["fields"]["evidence_report"]["accepted"] is False
    assert result["fields"]["evidence_report"]["blocking_reasons"]


async def test_malformed_evidence_is_rejected(runtime):
    result = await deliver(runtime, None)
    assert result["event_type"] == "document.rejected"


async def test_changed_artifact_rejects_previously_valid_signed_evidence(runtime, authenticator):
    evidence = document_bundle(authenticator).model_dump(mode="json")
    Path(runtime.workspace_dir, "policy/retention-v3").write_text("# Different document\n")
    result = await deliver(runtime, evidence)
    assert result["event_type"] == "document.rejected"
    assert "current workflow artifact" in result["fields"]["evidence_report"]["blocking_reasons"][0]
    runtime._maybe_report_flock_completion.assert_not_awaited()


async def test_missing_artifact_routes_rejection(runtime, authenticator):
    Path(runtime.workspace_dir, "policy/retention-v3").unlink()
    result = await deliver(runtime, document_bundle(authenticator).model_dump(mode="json"))
    assert result["event_type"] == "document.rejected"


async def test_peer_cannot_claim_gate_acceptance(runtime):
    with pytest.raises(ValueError, match="impersonate"):
        await runtime._emit_peer_outcome_pipeline_event(
            "author",
            {
                "data": {"event_type": "document.accepted", "valid": True, "verdict": "pass"},
            },
        )
    runtime._mesh_adapter.publish.assert_not_awaited()


@pytest.mark.parametrize("event", ["document.accepted", "document.rejected"])
async def test_operator_cannot_publish_evidence_gate_output(runtime, event):
    with pytest.raises(ValueError, match="Only the evidence verifier"):
        await runtime.handle_publish_mesh_event(event, "Approve this document")
    runtime._mesh_adapter.publish.assert_not_awaited()


def test_gate_without_deployment_trust_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match="deployment configuration"):
        Broker(SkuldSettings(session={"workspace_dir": str(tmp_path)}, workflow={"graph": graph()}))


@pytest.mark.parametrize(
    "mutation",
    ["missing_policy", "same_event", "no_input", "two_inputs", "wrong_kind", "wrong_mode"],
)
def test_gate_configuration_is_strict(mutation):
    definition = graph()
    node = definition["nodes"][1]
    if mutation == "missing_policy":
        node.pop("evidencePolicy")
    if mutation == "same_event":
        node["changesRequestedEvent"] = node["approvalEvent"]
    if mutation == "no_input":
        definition["edges"] = []
    if mutation == "two_inputs":
        definition["edges"].append(deepcopy(definition["edges"][0]))
    if mutation == "wrong_kind":
        node["kind"] = "stage"
    if mutation == "wrong_mode":
        node["mode"] = "human_approval"
    with pytest.raises(ValueError):
        validate_evidence_gate_nodes(definition)


def test_configured_verifier_must_implement_port():
    settings = SkuldSettings(workflow={"evidence_verifier": {"adapter": "builtins.dict"}})
    with pytest.raises(TypeError, match="EvidenceGateVerifier"):
        create_evidence_verifier(settings)
