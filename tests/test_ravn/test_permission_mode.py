"""Tests for the shared permission-mode parser."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ravn.config import PermissionConfig
from ravn.domain.permission_mode import (
    PermissionMode,
    parse_optional_permission_mode,
    parse_permission_mode,
    valid_permission_mode_spellings,
)


class TestParsePermissionMode:
    @pytest.mark.parametrize("mode", list(PermissionMode))
    def test_underscore_spelling(self, mode: PermissionMode) -> None:
        assert parse_permission_mode(mode.value) is mode

    @pytest.mark.parametrize("mode", list(PermissionMode))
    def test_hyphen_spelling(self, mode: PermissionMode) -> None:
        assert parse_permission_mode(mode.value.replace("_", "-")) is mode

    def test_enum_passes_through(self) -> None:
        assert parse_permission_mode(PermissionMode.READ_ONLY) is PermissionMode.READ_ONLY

    def test_surrounding_whitespace_is_ignored(self) -> None:
        assert parse_permission_mode("  read-only\n") is PermissionMode.READ_ONLY

    @pytest.mark.parametrize("value", ["superuser", "read-onyl", "READ_ONLY", "", "workspace-read"])
    def test_unknown_value_raises_naming_valid_values(self, value: str) -> None:
        with pytest.raises(ValueError, match="Unknown permission_mode") as exc_info:
            parse_permission_mode(value)
        message = str(exc_info.value)
        assert repr(value) in message
        for spelling in ("read-only", "read_only", "workspace-write", "full_access", "prompt"):
            assert spelling in message

    @pytest.mark.parametrize("value", [None, 1, True, ["read-only"]])
    def test_non_string_raises(self, value: object) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            parse_permission_mode(value)


class TestParseOptionalPermissionMode:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_unset_is_none(self, value: object) -> None:
        assert parse_optional_permission_mode(value) is None

    def test_set_value_is_parsed(self) -> None:
        assert parse_optional_permission_mode("full-access") is PermissionMode.FULL_ACCESS

    def test_unknown_value_still_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown permission_mode 'nope'"):
            parse_optional_permission_mode("nope")


def test_valid_spellings_cover_both_forms() -> None:
    spellings = valid_permission_mode_spellings()
    for mode in PermissionMode:
        assert mode.value in spellings
        assert mode.value.replace("_", "-") in spellings


class TestPermissionConfigMode:
    def test_hyphen_spelling_is_parsed(self) -> None:
        assert PermissionConfig(mode="read-only").mode is PermissionMode.READ_ONLY

    def test_unknown_mode_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown permission_mode 'superuser'"):
            PermissionConfig(mode="superuser")

    def test_assignment_is_validated(self) -> None:
        config = PermissionConfig()
        config.mode = "workspace-write"
        assert config.mode is PermissionMode.WORKSPACE_WRITE
        with pytest.raises(ValidationError, match="Unknown permission_mode"):
            config.mode = "superuser"
