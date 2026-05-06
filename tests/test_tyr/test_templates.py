"""Tests for tyr.domain.templates — NIU-644: flock_flow + persona_overrides parsing."""

from __future__ import annotations

import textwrap

import pytest

from tyr.domain.templates import (
    SagaTemplate,
    load_template_from_string,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_PIPELINE = textwrap.dedent(
    """
    name: "Test Pipeline"
    feature_branch: "feat/test"
    base_branch: "main"
    repos:
      - "test/repo"
    """
)

_STAGES_BLOCK = textwrap.dedent(
    """
    stages:
      - name: review
        parallel:
          - persona: reviewer
            prompt: "Review the code"
          - persona: security-auditor
            prompt: "Audit the code"
        fan_in: all_must_pass
    """
)


def _load(yaml_str: str) -> SagaTemplate:
    return load_template_from_string(yaml_str, payload={})


# ---------------------------------------------------------------------------
# SagaTemplate.flock_flow field
# ---------------------------------------------------------------------------


class TestFlockFlowField:
    def test_flock_flow_absent_defaults_to_none(self):
        template = _load(_BASE_PIPELINE + _STAGES_BLOCK)
        assert template.flock_flow is None

    def test_flock_flow_empty_string_normalised_to_none(self):
        yaml_str = _BASE_PIPELINE + "flock_flow: ''\n" + _STAGES_BLOCK
        template = _load(yaml_str)
        assert template.flock_flow is None

    def test_flock_flow_stored_from_yaml(self):
        yaml_str = _BASE_PIPELINE + "flock_flow: code-review-flow\n" + _STAGES_BLOCK
        template = _load(yaml_str)
        assert template.flock_flow == "code-review-flow"

    def test_flock_flow_propagated_to_all_phases(self):
        yaml_str = _BASE_PIPELINE + "flock_flow: my-flow\n" + _STAGES_BLOCK
        template = _load(yaml_str)
        # The flow name lives on the SagaTemplate, not on individual phases
        assert template.flock_flow == "my-flow"
        assert len(template.phases) == 1


# ---------------------------------------------------------------------------
# TemplateRaid.persona_overrides field
# ---------------------------------------------------------------------------


class TestPersonaOverridesField:
    def test_persona_overrides_absent_defaults_to_none(self):
        yaml_str = _BASE_PIPELINE + _STAGES_BLOCK
        template = _load(yaml_str)
        for phase in template.phases:
            for raid in phase.raids:
                assert raid.persona_overrides is None

    def test_persona_overrides_empty_dict_normalised_to_none(self):
        yaml_str = textwrap.dedent(
            """
            name: "P"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            stages:
              - name: s
                sequential:
                  - persona: reviewer
                    prompt: "Do it"
                    persona_overrides: {}
            """
        )
        template = _load(yaml_str)
        assert template.phases[0].raids[0].persona_overrides is None

    def test_persona_overrides_parsed_correctly(self):
        yaml_str = textwrap.dedent(
            """
            name: "PR Review"
            feature_branch: "feat/x"
            base_branch: "main"
            repos: ["test/repo"]
            flock_flow: code-review-flow
            stages:
              - name: parallel-review
                parallel:
                  - persona: reviewer
                    prompt: "Review the diff"
                    persona_overrides:
                      llm:
                        primary_alias: powerful
                        thinking_enabled: true
                      system_prompt_extra: |
                        Production-critical change; be thorough.
                  - persona: security-auditor
                    prompt: "Security audit"
                fan_in: all_must_pass
            """
        )
        template = _load(yaml_str)
        raids = template.phases[0].raids

        reviewer_raid = next(r for r in raids if r.persona == "reviewer")
        auditor_raid = next(r for r in raids if r.persona == "security-auditor")

        assert reviewer_raid.persona_overrides is not None
        assert reviewer_raid.persona_overrides["llm"]["primary_alias"] == "powerful"
        assert reviewer_raid.persona_overrides["llm"]["thinking_enabled"] is True
        assert "Production-critical" in reviewer_raid.persona_overrides["system_prompt_extra"]

        assert auditor_raid.persona_overrides is None

    def test_persona_overrides_in_legacy_phases_parsed(self):
        yaml_str = textwrap.dedent(
            """
            name: "Legacy"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            phases:
              - name: review
                raids:
                  - name: "Code review"
                    persona: reviewer
                    prompt: "Review it"
                    persona_overrides:
                      system_prompt_extra: "Extra context"
            """
        )
        template = _load(yaml_str)
        raid = template.phases[0].raids[0]
        assert raid.persona_overrides == {"system_prompt_extra": "Extra context"}

    def test_persona_overrides_sequential_participants_parsed(self):
        yaml_str = textwrap.dedent(
            """
            name: "Seq"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            stages:
              - name: test
                sequential:
                  - persona: qa-agent
                    prompt: "Run tests"
                    persona_overrides:
                      iteration_budget: 5
            """
        )
        template = _load(yaml_str)
        raid = template.phases[0].raids[0]
        assert raid.persona_overrides == {"iteration_budget": 5}


# ---------------------------------------------------------------------------
# Security boundary: allowed_tools / forbidden_tools are rejected at parse time
# ---------------------------------------------------------------------------


class TestPersonaOverridesSecurityBoundary:
    def test_allowed_tools_in_parallel_participant_raises(self):
        yaml_str = textwrap.dedent(
            """
            name: "P"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            stages:
              - name: s
                parallel:
                  - persona: reviewer
                    prompt: "Review"
                    persona_overrides:
                      allowed_tools: ["Bash"]
            """
        )
        with pytest.raises(ValueError, match="allowed_tools"):
            _load(yaml_str)

    def test_forbidden_tools_in_parallel_participant_raises(self):
        yaml_str = textwrap.dedent(
            """
            name: "P"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            stages:
              - name: s
                parallel:
                  - persona: reviewer
                    prompt: "Review"
                    persona_overrides:
                      forbidden_tools: ["Edit"]
            """
        )
        with pytest.raises(ValueError, match="forbidden_tools"):
            _load(yaml_str)

    def test_allowed_tools_in_sequential_participant_raises(self):
        yaml_str = textwrap.dedent(
            """
            name: "P"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            stages:
              - name: s
                sequential:
                  - persona: reviewer
                    prompt: "Review"
                    persona_overrides:
                      allowed_tools: ["Read"]
            """
        )
        with pytest.raises(ValueError, match="allowed_tools"):
            _load(yaml_str)

    def test_allowed_tools_in_legacy_raid_raises(self):
        yaml_str = textwrap.dedent(
            """
            name: "L"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            phases:
              - name: review
                raids:
                  - name: "Review"
                    persona: reviewer
                    prompt: "Do it"
                    persona_overrides:
                      allowed_tools: ["Write"]
            """
        )
        with pytest.raises(ValueError, match="allowed_tools"):
            _load(yaml_str)

    def test_error_message_cites_offending_path(self):
        yaml_str = textwrap.dedent(
            """
            name: "P"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            stages:
              - name: my-stage
                parallel:
                  - persona: reviewer
                    prompt: "Review"
                    persona_overrides:
                      allowed_tools: ["Bash"]
            """
        )
        with pytest.raises(ValueError, match="my-stage"):
            _load(yaml_str)

    def test_error_message_mentions_security_boundary(self):
        yaml_str = textwrap.dedent(
            """
            name: "P"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            stages:
              - name: s
                parallel:
                  - persona: reviewer
                    prompt: "Review"
                    persona_overrides:
                      forbidden_tools: ["Edit"]
            """
        )
        with pytest.raises(ValueError, match="security boundary"):
            _load(yaml_str)

    def test_safe_overrides_do_not_raise(self):
        """Non-security keys should parse without error."""
        yaml_str = textwrap.dedent(
            """
            name: "P"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            stages:
              - name: s
                parallel:
                  - persona: reviewer
                    prompt: "Review"
                    persona_overrides:
                      llm:
                        primary_alias: powerful
                      system_prompt_extra: "Extra"
                      iteration_budget: 3
            """
        )
        template = _load(yaml_str)
        assert template.phases[0].raids[0].persona_overrides is not None


# ---------------------------------------------------------------------------
# load_template_from_string: event interpolation doesn't affect new fields
# ---------------------------------------------------------------------------


class TestFlockFlowInterpolation:
    def test_flock_flow_preserved_through_interpolation(self):
        yaml_str = textwrap.dedent(
            """
            name: "Review: {event.repo}"
            feature_branch: "{event.branch}"
            base_branch: "main"
            repos: ["{event.repo}"]
            flock_flow: code-review-flow
            stages:
              - name: review
                sequential:
                  - persona: reviewer
                    prompt: "Review {event.repo}"
            """
        )
        template = load_template_from_string(
            yaml_str, payload={"repo": "acme/app", "branch": "feat/x"}
        )
        assert template.flock_flow == "code-review-flow"
        assert template.name == "Review: acme/app"
