"""Receipt validation is bound to persisted attempts and an authenticated Forge."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from niuu.domain.delivery import CandidateEvidence, EvidenceValidationReport, evidence_digest
from tests.test_ting.test_delivery_execution import _execution, _proposal
from ting.adapters.child_evidence import ForgeChildEvidenceVerifier
from ting.adapters.volundr_http import VolundrHTTPAdapter
from ting.domain.delivery_execution import ChildExecutionState, make_children


def child_result():
    execution = _execution(current_generation=1)
    child = make_children(execution, generation=1, ordered=(_proposal(execution, "parser"),))[0]
    child = replace(
        child,
        workspace={
            "worker_id": "worker",
            "campaign_id": str(execution.id),
            "workstream_key": child.key,
            "base_sha": child.base_sha,
            "test_contract_ids": ["tests"],
        },
    )
    result = {
        "attemptId": str(child.id),
        "candidateSha": "b" * 40,
        "candidateTree": "c" * 40,
        "verificationReceipts": [
            {
                "receipt_id": "verification",
                "campaign_id": str(execution.id),
                "workstream_key": child.key,
                "attempt_id": str(child.id),
                "repository": execution.repository,
                "base_sha": child.base_sha,
                "candidate_sha": "b" * 40,
                "candidate_tree": "c" * 40,
                "contract_id": "tests",
                "command_digest": "d" * 64,
                "exit_code": 0,
                "stdout_digest": "e" * 64,
                "stderr_digest": "f" * 64,
                "started_at": datetime.now(UTC).isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
            }
        ],
        "reviewReceipts": [],
        "requirementEvidence": [
            {
                "requirement_id": "REQ-parser",
                "implementation_paths": ["parser.py"],
                "verification_contract_ids": ["tests"],
            }
        ],
    }
    return execution, child, result


def report_for(evidence, **kwargs):
    return EvidenceValidationReport(
        accepted=True, blocking_reasons=(), manifest_digest=evidence_digest(evidence)
    )


@pytest.mark.asyncio
async def test_checks_exact_manifest_over_existing_authenticated_connection():
    execution, child, result = child_result()
    factory = AsyncMock()
    adapter = factory.for_connection.return_value
    adapter.validate_delivery_evidence.side_effect = report_for
    verifier = ForgeChildEvidenceVerifier(volundr_factory=factory, policy_id="developer-workstream")
    report = await verifier.validate(execution, child, result)
    assert report.accepted
    factory.for_connection.assert_awaited_once_with(execution.owner_id, execution.connection_id)
    factory.primary_for_owner.assert_not_called()
    call = adapter.validate_delivery_evidence.call_args
    evidence = call.args[0]
    assert evidence.attempt_id == str(child.id)
    assert evidence.worker_id == "worker"
    assert evidence.campaign_id == str(execution.id)
    assert call.kwargs["principal"].tenant_id == execution.tenant_id
    assert call.kwargs["policy_id"] == "developer-workstream"


@pytest.mark.asyncio
async def test_backend_credential_has_configured_admission_role_and_parent_session() -> None:
    execution, child, result = child_result()
    factory = AsyncMock()
    adapter = factory.for_connection.return_value
    adapter.validate_delivery_evidence.side_effect = report_for
    issuer = MagicMock()
    issuer.issue_token.return_value = SimpleNamespace(token="scoped-token")
    verifier = ForgeChildEvidenceVerifier(
        volundr_factory=factory,
        policy_id="developer-workstream",
        token_issuer=issuer,
        admission_roles=("volundr:developer",),
    )

    await verifier.validate(execution, child, result)

    issued = issuer.issue_token.call_args.kwargs
    assert issued["principal"].roles == ["volundr:developer"]
    assert issued["claims"]["forge_session_id"] == execution.parent_session_id
    assert adapter.validate_delivery_evidence.call_args.kwargs["auth_token"] == "scoped-token"


@pytest.mark.asyncio
async def test_backend_credential_requires_durable_parent_session() -> None:
    execution, child, result = child_result()
    execution = replace(execution, parent_session_id=None)
    factory = AsyncMock()
    factory.for_connection.return_value = AsyncMock()
    issuer = MagicMock()

    with pytest.raises(RuntimeError, match="durable parent session"):
        await ForgeChildEvidenceVerifier(
            volundr_factory=factory,
            policy_id="developer-workstream",
            token_issuer=issuer,
        ).validate(execution, child, result)


@pytest.mark.parametrize(
    "mutation, reason",
    [
        ("execution", "another execution"),
        ("generation", "superseded generation"),
        ("superseded", "no longer active"),
        ("workspace", "no durable workspace"),
        ("worker", "another workstream"),
        ("base", "base differs"),
        ("attempt", "another child attempt"),
        ("sha", "typed receipt"),
        ("requirements", "assigned requirements"),
        ("missing_test", "assigned verification contract"),
        ("undeclared_tests", "assigned verification contract"),
    ],
)
@pytest.mark.asyncio
async def test_rejects_stale_or_foreign_evidence_before_remote_check(mutation, reason):
    execution, child, result = child_result()
    if mutation == "execution":
        child = replace(child, execution_id=_execution().id)
    elif mutation == "generation":
        child = replace(child, generation=2)
    elif mutation == "superseded":
        child = replace(child, state=ChildExecutionState.SUPERSEDED)
    elif mutation == "workspace":
        child = replace(child, workspace=None)
    elif mutation == "worker":
        child = replace(child, workspace={**child.workspace, "workstream_key": "foreign"})
    elif mutation == "base":
        child = replace(child, workspace={**child.workspace, "base_sha": "d" * 40})
    elif mutation == "attempt":
        result["attemptId"] = "stale"
    elif mutation == "sha":
        result["candidateSha"] = "not-a-commit"
    elif mutation == "requirements":
        result["requirementEvidence"][0]["requirement_id"] = "foreign"
    elif mutation == "missing_test":
        result["verificationReceipts"] = []
    elif mutation == "undeclared_tests":
        child = replace(child, workspace={**child.workspace, "test_contract_ids": []})
    factory = AsyncMock()
    report = await ForgeChildEvidenceVerifier(volundr_factory=factory, policy_id="policy").validate(
        execution, child, result
    )
    assert not report.accepted
    assert reason in report.blocking_reasons[0]
    factory.for_connection.assert_not_called()


@pytest.mark.asyncio
async def test_missing_configured_connection_never_uses_other_provider():
    execution, child, result = child_result()
    factory = AsyncMock()
    factory.for_connection.return_value = None
    with pytest.raises(RuntimeError, match="configured Forge connection"):
        await ForgeChildEvidenceVerifier(volundr_factory=factory, policy_id="policy").validate(
            execution, child, result
        )
    factory.primary_for_owner.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_unrelated_validation_report_and_preserves_forge_veto():
    execution, child, result = child_result()
    factory = AsyncMock()
    adapter = factory.for_connection.return_value
    adapter.validate_delivery_evidence.return_value = EvidenceValidationReport(
        accepted=True, blocking_reasons=(), manifest_digest="a" * 64
    )
    verifier = ForgeChildEvidenceVerifier(volundr_factory=factory, policy_id="policy")
    assert not (await verifier.validate(execution, child, result)).accepted

    def rejected(evidence, **kwargs):
        return EvidenceValidationReport(
            accepted=False,
            blocking_reasons=("Untrusted signature",),
            manifest_digest=evidence_digest(evidence),
        )

    adapter.validate_delivery_evidence.side_effect = rejected
    assert (await verifier.validate(execution, child, result)).blocking_reasons == (
        "Untrusted signature",
    )


@pytest.mark.asyncio
@respx.mock
async def test_http_adapter_sends_evidence_with_auth_and_propagates_failures():
    execution, child, result = child_result()
    evidence = CandidateEvidence(
        campaign_id=str(execution.id),
        workstream_key=child.key,
        attempt_id=str(child.id),
        worker_id="worker",
        repository=execution.repository,
        base_sha=child.base_sha,
        candidate_sha=result["candidateSha"],
        candidate_tree=result["candidateTree"],
        requirements=result["requirementEvidence"],
    )
    report = report_for(evidence)
    route = respx.post("https://forge.example/api/v1/forge/delivery/evidence/validate").mock(
        return_value=httpx.Response(200, json=report.model_dump())
    )
    adapter = VolundrHTTPAdapter(
        base_url="https://forge.example", api_key="test-service-credential"
    )
    assert await adapter.validate_delivery_evidence(evidence, policy_id="policy") == report
    assert route.calls.last.request.headers["authorization"] == "Bearer test-service-credential"
    route.mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.validate_delivery_evidence(evidence, policy_id="policy")
