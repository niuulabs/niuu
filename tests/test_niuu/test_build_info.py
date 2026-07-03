"""Tests for the shared runtime build-identification helper."""

import re

from niuu import build_info as build_info_mod
from niuu.build_info import build_info


def _fresh():
    build_info.cache_clear()
    return build_info()


def test_build_info_has_expected_keys_and_never_raises():
    info = _fresh()
    assert set(info) == {"version", "git_sha", "git_sha_full", "git_branch", "git_dirty"}
    assert info["version"] == build_info_mod.VERSION
    assert isinstance(info["git_dirty"], bool)


def test_git_sha_is_a_hex_sha_or_unknown():
    info = _fresh()
    sha = info["git_sha"]
    assert sha == "unknown" or re.fullmatch(r"[0-9a-f]{7,12}", sha)
    full = info["git_sha_full"]
    assert full == "unknown" or full.startswith(sha)


def test_is_cached_same_object_on_repeat_calls():
    build_info.cache_clear()
    first = build_info()
    assert build_info() is first  # lru_cache returns the same dict instance


def test_env_vars_take_precedence_over_git(monkeypatch):
    monkeypatch.setenv("NIUU_BUILD_SHA", "deadbeefcafe1234")
    monkeypatch.setenv("NIUU_BUILD_REF", "release/v9")
    info = _fresh()
    assert info["git_sha"] == "deadbeefcafe"
    assert info["git_sha_full"] == "deadbeefcafe1234"
    assert info["git_branch"] == "release/v9"
    build_info.cache_clear()  # don't leak the override into other tests
