"""LEX-2045: the allowlist and value rules for a session's launch env."""

import pytest

from volundr.config import Settings
from volundr.domain.launch_env import LaunchEnvRule, validate_launch_env

RULES = Settings.model_fields["session_launch_env"].default_factory()


def test_default_allowlist_accepts_the_lane_values():
    checked = validate_launch_env(
        {"CLAUDE_CODE_SUBAGENT_MODEL": "opus", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": 300000},
        RULES,
    )
    assert checked == {
        "CLAUDE_CODE_SUBAGENT_MODEL": "opus",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "300000",
    }
    assert validate_launch_env({"CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-5-5[1m]"}, RULES)


def test_an_unlisted_name_is_refused_with_the_allowlist():
    with pytest.raises(ValueError, match="'PATH' is not allowed.*CLAUDE_CODE_SUBAGENT_MODEL"):
        validate_launch_env({"PATH": "/tmp"}, RULES)
    with pytest.raises(ValueError, match="none configured"):
        validate_launch_env({"CLAUDE_CODE_SUBAGENT_MODEL": "opus"}, {})


@pytest.mark.parametrize("window", [99_999, 1_000_001, "lots", True])
def test_the_compact_window_must_be_an_integer_in_range(window):
    with pytest.raises(ValueError, match="CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
        validate_launch_env({"CLAUDE_CODE_AUTO_COMPACT_WINDOW": window}, RULES)


@pytest.mark.parametrize("model", ["", "opus; rm -rf /", "-opus", "a" * 101])
def test_the_subagent_model_must_match_its_pattern(model):
    with pytest.raises(ValueError, match="CLAUDE_CODE_SUBAGENT_MODEL"):
        validate_launch_env({"CLAUDE_CODE_SUBAGENT_MODEL": model}, RULES)


def test_the_launch_env_must_be_a_mapping_of_scalars():
    with pytest.raises(ValueError, match="must be an object"):
        validate_launch_env(["CLAUDE_CODE_SUBAGENT_MODEL"], RULES)
    with pytest.raises(ValueError, match="string or an integer"):
        validate_launch_env({"CLAUDE_CODE_SUBAGENT_MODEL": {"x": 1}}, RULES)


def test_a_rule_is_exactly_one_kind_and_consistent():
    with pytest.raises(ValueError, match="not both"):
        LaunchEnvRule(pattern="x", min_int=1)
    with pytest.raises(ValueError, match="needs a pattern"):
        LaunchEnvRule()
    with pytest.raises(ValueError, match="greater than"):
        LaunchEnvRule(min_int=2, max_int=1)
    with pytest.raises(ValueError):
        LaunchEnvRule(pattern="(")
