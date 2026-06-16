"""Tests for the shared tag-selector matcher."""

from __future__ import annotations

from niuu.domain.tags import matches_tags


def test_empty_selector_matches_everything() -> None:
    assert matches_tags(["gpu"], None) is True
    assert matches_tags([], []) is True
    assert matches_tags(["anything"], ()) is True


def test_match_all_requires_every_tag() -> None:
    assert matches_tags(["gpu", "us-west", "prod"], ["gpu", "us-west"]) is True
    assert matches_tags(["gpu"], ["gpu", "us-west"]) is False


def test_match_any_requires_one_tag() -> None:
    assert matches_tags(["us-west"], ["us-east", "us-west"], match="any") is True
    assert matches_tags(["eu"], ["us-east", "us-west"], match="any") is False


def test_accepts_any_iterable() -> None:
    assert matches_tags({"gpu", "prod"}, ("gpu",)) is True
