"""Which turns become episodes.

Sized against the real corpus: 100 of 139 documents in Travis's live memory were health checks
("Hello!", "pong"), and they made recall degenerate — two unrelated queries returned the same
three rows. The rule is narrow on purpose: dropping a real episode loses it forever, keeping a
trivial one merely dilutes, so only the unambiguous case is discarded.
"""

from __future__ import annotations

from ravn.agent import _is_worth_remembering
from ravn.domain.models import Episode


def _episode(
    task: str, summary: str = "", *, tools: list[str] | None = None, errors: list[str] | None = None
) -> Episode:
    return Episode(
        episode_id="e1",
        session_id="s1",
        timestamp="2026-08-15T00:00:00Z",
        summary=summary,
        task_description=task,
        tools_used=tools or [],
        outcome="success",
        tags=[],
        errors=errors or [],
    )


class TestDiscarded:
    def test_the_hello_ping_that_filled_the_corpus(self) -> None:
        assert not _is_worth_remembering(_episode("Hello!", "ok"))

    def test_the_pong_ping(self) -> None:
        assert not _is_worth_remembering(
            _episode("Reply with only the word pong. Do not use any tools.", "pong")
        )

    def test_an_empty_turn(self) -> None:
        assert not _is_worth_remembering(_episode("", ""))


class TestKept:
    def test_a_real_exchange(self) -> None:
        assert _is_worth_remembering(
            _episode(
                "Which commit is the dev-integration branch rebased on?",
                "`6ffcb551` — upstream/dev as of 2026-08-09, per the merge-base.",
            )
        )

    def test_anything_that_used_a_tool_however_short(self) -> None:
        """It did something. That is a fact about the world, and length says nothing about it."""
        assert _is_worth_remembering(_episode("ls", "ok", tools=["bash"]))

    def test_a_failure_however_terse(self) -> None:
        """A failure is a fact about the system — often the most useful kind."""
        assert _is_worth_remembering(_episode("go", "", errors=["LLMError: 401"]))

    def test_a_turn_just_over_the_line(self) -> None:
        """The shortest genuinely useful episode in the real corpus was 88 chars."""
        assert _is_worth_remembering(
            _episode(
                "Reply in one short sentence: are you Travis?",
                "Yes — Travis, Thor's resident Ravn.",
            )
        )
