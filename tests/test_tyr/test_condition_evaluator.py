"""Tests for tyr.domain.condition_evaluator."""

from __future__ import annotations

import pytest

from tyr.domain.condition_evaluator import ConditionError, evaluate_condition

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CONTEXT = {
    "stages": {
        "review": {"verdict": "pass", "findings_count": 3, "critical_findings": 0},
        "test": {"verdict": "pass", "critical_count": 0},
        "audit": {"verdict": "fail", "critical_findings": 2},
    }
}


# ---------------------------------------------------------------------------
# Basic comparisons
# ---------------------------------------------------------------------------


class TestBasicComparisons:
    def test_string_equality_pass(self):
        assert evaluate_condition("stages.review.verdict == pass", _CONTEXT) is True

    def test_string_equality_fail(self):
        assert evaluate_condition("stages.audit.verdict == pass", _CONTEXT) is False

    def test_string_inequality(self):
        assert evaluate_condition("stages.audit.verdict != pass", _CONTEXT) is True

    def test_numeric_equality_zero(self):
        assert evaluate_condition("stages.review.critical_findings == 0", _CONTEXT) is True

    def test_numeric_equality_nonzero(self):
        assert evaluate_condition("stages.audit.critical_findings == 0", _CONTEXT) is False

    def test_numeric_greater_than(self):
        assert evaluate_condition("stages.review.findings_count > 0", _CONTEXT) is True

    def test_numeric_less_than(self):
        assert evaluate_condition("stages.review.findings_count < 10", _CONTEXT) is True

    def test_numeric_greater_than_or_equal(self):
        assert evaluate_condition("stages.review.findings_count >= 3", _CONTEXT) is True

    def test_numeric_less_than_or_equal(self):
        assert evaluate_condition("stages.review.findings_count <= 3", _CONTEXT) is True

    def test_none_field_equals_string(self):
        ctx = {"stages": {"review": {"verdict": None}}}
        assert evaluate_condition("stages.review.verdict == pass", ctx) is False

    def test_missing_nested_field_returns_false(self):
        ctx = {"stages": {"review": {}}}
        assert evaluate_condition("stages.review.verdict == pass", ctx) is False


# ---------------------------------------------------------------------------
# AND / OR combinators
# ---------------------------------------------------------------------------


class TestCombinators:
    def test_and_both_true(self):
        assert (
            evaluate_condition(
                "stages.review.verdict == pass AND stages.test.verdict == pass",
                _CONTEXT,
            )
            is True
        )

    def test_and_one_false(self):
        assert (
            evaluate_condition(
                "stages.review.verdict == pass AND stages.audit.verdict == pass",
                _CONTEXT,
            )
            is False
        )

    def test_or_both_false(self):
        ctx = {
            "stages": {
                "a": {"verdict": "fail"},
                "b": {"verdict": "fail"},
            }
        }
        assert (
            evaluate_condition("stages.a.verdict == pass OR stages.b.verdict == pass", ctx) is False
        )

    def test_or_one_true(self):
        assert (
            evaluate_condition(
                "stages.review.verdict == pass OR stages.audit.verdict == pass",
                _CONTEXT,
            )
            is True
        )

    def test_and_takes_precedence_over_or(self):
        # A OR B AND C → A OR (B AND C)
        ctx = {
            "stages": {
                "a": {"verdict": "pass"},
                "b": {"verdict": "fail"},
                "c": {"verdict": "pass"},
            }
        }
        # A=pass OR (B=fail AND C=pass) → True OR False → True
        result = evaluate_condition(
            "stages.a.verdict == pass OR stages.b.verdict == pass AND stages.c.verdict == pass",
            ctx,
        )
        assert result is True

    def test_case_insensitive_and(self):
        assert (
            evaluate_condition(
                "stages.review.verdict == pass and stages.test.verdict == pass",
                _CONTEXT,
            )
            is True
        )

    def test_case_insensitive_or(self):
        assert (
            evaluate_condition(
                "stages.review.verdict == pass or stages.audit.verdict == pass",
                _CONTEXT,
            )
            is True
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_expression_returns_true(self):
        assert evaluate_condition("", _CONTEXT) is True

    def test_whitespace_only_returns_true(self):
        assert evaluate_condition("   ", _CONTEXT) is True

    def test_parentheses(self):
        assert (
            evaluate_condition(
                "(stages.review.verdict == pass AND stages.test.verdict == pass)",
                _CONTEXT,
            )
            is True
        )

    def test_invalid_expression_raises(self):
        with pytest.raises(ConditionError):
            evaluate_condition("stages.review.verdict", _CONTEXT)

    def test_unknown_operator_raises(self):
        with pytest.raises((ConditionError, Exception)):
            evaluate_condition("stages.review.verdict =! pass", _CONTEXT)

    def test_missing_closing_paren_raises(self):
        with pytest.raises(ConditionError):
            evaluate_condition("(stages.review.verdict == pass", _CONTEXT)

    def test_unexpected_token_raises(self):
        with pytest.raises(ConditionError):
            evaluate_condition("== pass", _CONTEXT)

    def test_missing_value_after_operator_raises(self):
        with pytest.raises(ConditionError):
            evaluate_condition("stages.review.verdict ==", _CONTEXT)

    def test_extra_tokens_after_expression_raises(self):
        with pytest.raises(ConditionError):
            evaluate_condition("stages.review.verdict == pass extra_token", _CONTEXT)

    def test_string_less_than(self):
        ctx = {"stages": {"a": {"verdict": "fail"}, "b": {"verdict": "pass"}}}
        # "fail" < "pass" alphabetically
        assert evaluate_condition("stages.a.verdict < stages.b.verdict", ctx) is True

    def test_string_greater_than(self):
        ctx = {"stages": {"a": {"verdict": "pass"}}}
        # "pass" > "fail" alphabetically
        assert evaluate_condition("stages.a.verdict > fail", ctx) is True

    def test_non_dict_path_raises(self):
        ctx = {"stages": "not-a-dict"}
        with pytest.raises(ConditionError):
            evaluate_condition("stages.review.verdict == pass", ctx)
