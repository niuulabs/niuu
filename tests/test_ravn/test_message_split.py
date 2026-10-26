"""Outbound chat text is split at the platform limit, never cut."""

from __future__ import annotations

import pytest

from ravn.adapters.channels._message_split import split_message


def test_short_text_is_one_chunk() -> None:
    assert split_message("hello", 10) == ["hello"]


def test_splits_on_the_last_line_break() -> None:
    assert split_message("first line\nsecond line", 15) == ["first line", "second line"]


def test_splits_on_the_last_space_without_a_line_break() -> None:
    assert split_message("alpha beta gamma", 11) == ["alpha beta", "gamma"]


def test_hard_cut_keeps_every_character() -> None:
    text = "x" * 25
    chunks = split_message(text, 10)
    assert chunks == ["x" * 10, "x" * 10, "x" * 5]
    assert "".join(chunks) == text


def test_every_chunk_fits() -> None:
    text = ("word " * 400 + "\n") * 3
    assert all(len(chunk) <= 100 for chunk in split_message(text, 100))


def test_a_non_positive_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        split_message("x", 0)
