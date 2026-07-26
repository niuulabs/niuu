"""Tests for IterationBudget and TokenEstimator (ravn.budget)."""

from __future__ import annotations

from ravn.budget import _CHARS_PER_TOKEN, IterationBudget, TokenEstimator
from ravn.domain.models import Message, TokenUsage

# ---------------------------------------------------------------------------
# TokenEstimator
# ---------------------------------------------------------------------------


class TestTokenEstimatorRough:
    def test_empty_string(self):
        assert TokenEstimator.rough("") == 0

    def test_basic(self):
        # 40 chars → 10 tokens
        assert TokenEstimator.rough("a" * 40) == 10

    def test_fractional_truncates(self):
        # 5 chars → 1 token (floor division)
        assert TokenEstimator.rough("hello") == 1

    def test_longer_text(self):
        text = "Hello world, this is a test." * 10
        expected = len(text) // _CHARS_PER_TOKEN
        assert TokenEstimator.rough(text) == expected


class TestTokenEstimatorRoughMessages:
    def test_empty_list(self):
        assert TokenEstimator.rough_messages([]) == 0

    def test_simple_string_messages(self):
        msgs = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="World"),
        ]
        total_chars = len("Hello") + len("World")
        assert TokenEstimator.rough_messages(msgs) == total_chars // _CHARS_PER_TOKEN

    def test_list_content_message(self):
        # Structured content is serialized in full, including JSON syntax.
        content = [{"type": "text", "text": "abcd"}, {"type": "other", "value": "efgh"}]
        msgs = [Message(role="user", content=content)]
        assert TokenEstimator.rough_messages(msgs) == 15

    def test_nested_non_string_is_counted(self):
        content = [{"type": "number", "val": 42}]
        msgs = [Message(role="user", content=content)]
        assert TokenEstimator.rough_messages(msgs) == 7

    def test_nested_tool_input_is_counted(self):
        small = Message(
            role="assistant",
            content=[{"type": "tool_use", "name": "bash", "input": {"command": "pwd"}}],
        )
        large = Message(
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "name": "bash",
                    "input": {"command": "x" * 400},
                }
            ],
        )
        assert TokenEstimator.rough_messages([large]) > TokenEstimator.rough_messages([small]) + 90


class TestTokenEstimatorRoughApiMessages:
    def test_empty(self):
        assert TokenEstimator.rough_api_messages([]) == 0

    def test_string_content(self):
        msgs = [{"role": "user", "content": "abcdefgh"}]  # 8 chars → 2 tokens
        assert TokenEstimator.rough_api_messages(msgs) == 2

    def test_list_content(self):
        # {"type": "text", "text": "abcd"} → "text"(4) + "abcd"(4) = 8 chars / 4 = 2
        msgs = [{"role": "user", "content": [{"type": "text", "text": "abcd"}]}]
        assert TokenEstimator.rough_api_messages(msgs) == 2

    def test_missing_content(self):
        msgs = [{"role": "user"}]
        assert TokenEstimator.rough_api_messages(msgs) == 0


class TestTokenEstimatorRoughBlocks:
    def test_empty(self):
        assert TokenEstimator.rough_blocks([]) == 0

    def test_single_block(self):
        blocks = [{"type": "text", "text": "abcdefgh"}]  # 8 / 4 = 2
        assert TokenEstimator.rough_blocks(blocks) == 2

    def test_multiple_blocks(self):
        blocks = [
            {"type": "text", "text": "abcd"},
            {"type": "text", "text": "efgh"},
        ]
        assert TokenEstimator.rough_blocks(blocks) == 2

    def test_non_dict_entries_skipped(self):
        assert TokenEstimator.rough_blocks(["not_a_dict"]) == 0


class TestTokenEstimatorFromUsage:
    def test_total(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert TokenEstimator.from_usage(usage) == 150

    def test_with_cache(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50, cache_read_tokens=20)
        assert TokenEstimator.from_usage(usage) == 150  # total_tokens ignores cache


# ---------------------------------------------------------------------------
# IterationBudget
# ---------------------------------------------------------------------------


class TestIterationBudgetBasic:
    def test_defaults(self):
        b = IterationBudget()
        assert b.total == 90
        assert b.consumed == 0
        assert b.remaining == 90
        assert not b.near_limit
        assert not b.exhausted

    def test_consume_increments(self):
        b = IterationBudget(total=10)
        b.consume()
        assert b.consumed == 1
        assert b.remaining == 9

    def test_consume_multiple(self):
        b = IterationBudget(total=10)
        b.consume(3)
        assert b.consumed == 3
        assert b.remaining == 7

    def test_exhausted_when_consumed_equals_total(self):
        b = IterationBudget(total=5)
        for _ in range(5):
            b.consume()
        assert b.exhausted
        assert b.remaining == 0

    def test_remaining_never_negative(self):
        b = IterationBudget(total=2)
        b.consume(5)
        assert b.remaining == 0


class TestIterationBudgetNearLimit:
    def test_not_near_limit_below_threshold(self):
        b = IterationBudget(total=10, near_limit_threshold=0.8)
        b.consume(7)  # 70% — below 80%
        assert not b.near_limit

    def test_near_limit_at_threshold(self):
        b = IterationBudget(total=10, near_limit_threshold=0.8)
        b.consume(8)  # 80% — at threshold
        assert b.near_limit

    def test_near_limit_above_threshold(self):
        b = IterationBudget(total=10, near_limit_threshold=0.8)
        b.consume(9)
        assert b.near_limit

    def test_near_limit_when_total_zero(self):
        b = IterationBudget(total=0)
        assert b.near_limit


class TestIterationBudgetTaskCeiling:
    def test_ceiling_limits_remaining(self):
        b = IterationBudget(total=90, task_ceiling=30)
        assert b.remaining == 30

    def test_ceiling_respected_when_below_global(self):
        b = IterationBudget(total=90, task_ceiling=20)
        b.consume(15)
        # task_consumed=15, ceiling=20 → task_remaining=5
        # global_remaining=75
        assert b.remaining == 5

    def test_global_limits_when_lower_than_ceiling(self):
        b = IterationBudget(total=10, task_ceiling=50)
        b.consume(8)
        # global_remaining=2, task_remaining=42
        assert b.remaining == 2

    def test_ceiling_near_limit_based_on_task(self):
        b = IterationBudget(total=90, task_ceiling=10, near_limit_threshold=0.8)
        b.consume(8)  # task_consumed=8/10 = 80% → near limit
        assert b.near_limit

    def test_ceiling_exhausted_from_task(self):
        b = IterationBudget(total=90, task_ceiling=5)
        b.consume(5)
        assert b.exhausted

    def test_task_consumed_tracks_locally(self):
        b = IterationBudget(total=90, task_ceiling=10)
        b.consume(3)
        assert b._task_consumed == 3


class TestIterationBudgetWarningSuffix:
    def test_no_warning_when_healthy(self):
        b = IterationBudget(total=100)
        b.consume(10)  # 10% used
        assert b.warning_suffix() is None

    def test_warning_near_limit(self):
        b = IterationBudget(total=10, near_limit_threshold=0.8)
        b.consume(8)
        suffix = b.warning_suffix()
        assert suffix is not None
        assert "Budget warning" in suffix
        assert "8/10" in suffix

    def test_warning_exhausted(self):
        b = IterationBudget(total=5)
        b.consume(5)
        suffix = b.warning_suffix()
        assert suffix is not None
        assert "exhausted" in suffix.lower()

    def test_warning_contains_remaining(self):
        b = IterationBudget(total=10, near_limit_threshold=0.8)
        b.consume(8)
        suffix = b.warning_suffix()
        assert "2 remaining" in suffix


class TestIterationBudgetSharedReference:
    """Verify that sharing the same object simulates cascade behaviour."""

    def test_two_agents_share_consumed_counter(self):
        shared = IterationBudget(total=10)
        # Simulate parent agent consuming 3
        shared.consume(3)
        # Sub-agent receives same object and consumes 2 more
        shared.consume(2)
        assert shared.consumed == 5
        assert shared.remaining == 5

    def test_consumed_visible_across_references(self):
        budget = IterationBudget(total=20)
        alias = budget  # same object
        budget.consume(5)
        assert alias.consumed == 5
