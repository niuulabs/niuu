"""Tests for ting.domain.templates — persona_overrides parsing and the retired flock_flow."""

from __future__ import annotations

import textwrap

import pytest

from ting.domain.templates import (
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
# flock_flow: retired with the flocks it named
# ---------------------------------------------------------------------------


class TestFlockFlowIsRefused:
    def test_a_template_naming_a_flock_flow_is_refused_with_the_remedy(self):
        yaml_str = _BASE_PIPELINE + "flock_flow: code-review-flow\n" + _STAGES_BLOCK
        with pytest.raises(ValueError, match="flocks no longer exist; remove flock_flow"):
            _load(yaml_str)

    def test_the_refusal_names_the_template_and_the_flow(self):
        yaml_str = _BASE_PIPELINE + "flock_flow: my-flow\n" + _STAGES_BLOCK
        with pytest.raises(ValueError, match="Test Pipeline.*my-flow"):
            _load(yaml_str)

    def test_an_empty_flock_flow_is_no_flock_flow(self):
        template = _load(_BASE_PIPELINE + "flock_flow: ''\n" + _STAGES_BLOCK)
        assert len(template.phases) == 1

    def test_a_template_without_one_loads(self):
        template = _load(_BASE_PIPELINE + _STAGES_BLOCK)
        assert len(template.phases) == 1


# ---------------------------------------------------------------------------
# TemplateRun.persona_overrides field
# ---------------------------------------------------------------------------


class TestPersonaOverridesField:
    def test_persona_overrides_absent_defaults_to_none(self):
        yaml_str = _BASE_PIPELINE + _STAGES_BLOCK
        template = _load(yaml_str)
        for phase in template.phases:
            for run in phase.runs:
                assert run.persona_overrides is None

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
        assert template.phases[0].runs[0].persona_overrides is None

    def test_persona_overrides_parsed_correctly(self):
        yaml_str = textwrap.dedent(
            """
            name: "PR Review"
            feature_branch: "feat/x"
            base_branch: "main"
            repos: ["test/repo"]
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
        runs = template.phases[0].runs

        reviewer_run = next(r for r in runs if r.persona == "reviewer")
        auditor_run = next(r for r in runs if r.persona == "security-auditor")

        assert reviewer_run.persona_overrides is not None
        assert reviewer_run.persona_overrides["llm"]["primary_alias"] == "powerful"
        assert reviewer_run.persona_overrides["llm"]["thinking_enabled"] is True
        assert "Production-critical" in reviewer_run.persona_overrides["system_prompt_extra"]

        assert auditor_run.persona_overrides is None

    def test_persona_overrides_in_legacy_phases_parsed(self):
        yaml_str = textwrap.dedent(
            """
            name: "Legacy"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            phases:
              - name: review
                runs:
                  - name: "Code review"
                    persona: reviewer
                    prompt: "Review it"
                    persona_overrides:
                      system_prompt_extra: "Extra context"
            """
        )
        template = _load(yaml_str)
        run = template.phases[0].runs[0]
        assert run.persona_overrides == {"system_prompt_extra": "Extra context"}

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
        run = template.phases[0].runs[0]
        assert run.persona_overrides == {"iteration_budget": 5}


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

    def test_allowed_tools_in_legacy_run_raises(self):
        yaml_str = textwrap.dedent(
            """
            name: "L"
            feature_branch: "f"
            base_branch: "main"
            repos: []
            phases:
              - name: review
                runs:
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
        assert template.phases[0].runs[0].persona_overrides is not None


# ---------------------------------------------------------------------------
# load_template_from_string: event interpolation doesn't affect new fields
# ---------------------------------------------------------------------------


class TestInterpolation:
    def test_event_placeholders_are_filled_in(self):
        yaml_str = textwrap.dedent(
            """
            name: "Review: {event.repo}"
            feature_branch: "{event.branch}"
            base_branch: "main"
            repos: ["{event.repo}"]
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
        assert template.name == "Review: acme/app"
        assert template.feature_branch == "feat/x"
