"""Tests for the shared tag-selector matcher."""

from __future__ import annotations

import pytest

from niuu.domain.tags import matches_tags, residual_selector, satisfiable, validate_match


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


def test_residual_all_is_required_minus_instance_tags() -> None:
    assert residual_selector(["valhalla"], ["valhalla", "gpu"], "all") == (("gpu",), "all")
    assert residual_selector([], ["gpu", "arm64"], "all") == (("arm64", "gpu"), "all")
    assert residual_selector(["gpu", "x"], ["gpu"], "all") is None


def test_residual_any_is_nothing_when_an_instance_tag_matches() -> None:
    assert residual_selector(["gpu"], ["gpu", "arm64"], "any") is None
    assert residual_selector(["x"], ["gpu", "arm64"], "any") == (("arm64", "gpu"), "any")


def test_residual_of_an_empty_selector_is_nothing() -> None:
    assert residual_selector(["x"], None) is None
    assert residual_selector([], []) is None
    assert residual_selector([], (), "any") is None


def test_residual_defaults_to_match_all() -> None:
    assert residual_selector([], ["gpu"]) == (("gpu",), "all")


@pytest.mark.parametrize("function", [residual_selector, satisfiable])
def test_unknown_match_mode_is_refused_not_read_as_all(function) -> None:
    arguments = ([], ["gpu"], "ANY") if function is residual_selector else ([], [], ["gpu"], "ANY")
    with pytest.raises(ValueError, match="ANY"):
        function(*arguments)


def test_validate_match_returns_a_known_mode() -> None:
    assert validate_match("all") == "all"
    assert validate_match("any") == "any"
    with pytest.raises(ValueError):
        validate_match("some")


def test_satisfiable_by_the_instance_tags_alone() -> None:
    assert satisfiable(["gpu"], [], ["gpu"], "all") is True
    assert satisfiable(["gpu"], [["arm64"]], ["gpu", "arm64"], "any") is True
    assert satisfiable([], [], [], "all") is True


def test_satisfiable_by_the_instance_tags_plus_one_host() -> None:
    assert satisfiable(["valhalla"], [["gpu"]], ["valhalla", "gpu"], "all") is True
    assert satisfiable(["valhalla"], [["site:a"], ["gpu"]], ["valhalla", "gpu"], "all") is True
    assert satisfiable([], [["arm64"]], ["gpu", "arm64"], "any") is True


def test_satisfiable_never_unions_tag_sets_across_hosts() -> None:
    # One gpu host and one arm64 host: no single host is gpu AND arm64.
    assert satisfiable([], [["gpu"], ["arm64"]], ["gpu", "arm64"], "all") is False
    assert satisfiable(["valhalla"], [["gpu"], ["arm64"]], ["valhalla", "gpu", "arm64"]) is False
    # ...but one host that has both is enough.
    assert satisfiable([], [["gpu"], ["gpu", "arm64"]], ["gpu", "arm64"], "all") is True


def test_satisfiable_is_false_without_hosts_when_the_instance_falls_short() -> None:
    assert satisfiable(["valhalla"], [], ["gpu"], "all") is False
    assert satisfiable(["valhalla"], [], ["gpu"], "any") is False


def test_satisfiable_agrees_with_residual_for_a_single_host() -> None:
    cases = [
        (["a"], ["b"], ["a", "b"], "all"),
        (["a"], ["c"], ["a", "b"], "all"),
        ([], ["b"], ["a", "b"], "any"),
        ([], ["c"], ["a", "b"], "any"),
        (["a"], [], ["a", "b"], "any"),
    ]
    for instance, host, required, match in cases:
        residual = residual_selector(instance, required, match)
        expected = residual is None or satisfiable([], [host], residual[0], residual[1])
        assert satisfiable(instance, [host], required, match) is expected
