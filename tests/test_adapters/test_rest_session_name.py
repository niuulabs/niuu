"""Tests for RFC 1123 session name validation on SessionCreate."""

import pytest
from pydantic import ValidationError

from volundr.adapters.inbound.rest import SessionCreate


class TestSessionNameValidation:
    """SessionCreate.name must be a valid RFC 1123 DNS label."""

    def _create(self, name: str) -> SessionCreate:
        return SessionCreate(name=name, model="claude-sonnet-4-20250514")

    def test_valid_simple_name(self):
        sc = self._create("my-session")
        assert sc.name == "my-session"

    def test_valid_single_char(self):
        sc = self._create("a")
        assert sc.name == "a"

    def test_valid_digits_only(self):
        sc = self._create("123")
        assert sc.name == "123"

    def test_valid_max_length(self):
        sc = self._create("a" * 63)
        assert len(sc.name) == 63

    def test_rejects_uppercase(self):
        with pytest.raises(ValidationError, match="lowercase"):
            self._create("MySession")

    def test_rejects_spaces(self):
        with pytest.raises(ValidationError, match="spaces"):
            self._create("my session")

    def test_rejects_leading_hyphen(self):
        with pytest.raises(ValidationError, match="start with"):
            self._create("-my-session")

    def test_rejects_trailing_hyphen(self):
        with pytest.raises(ValidationError, match="end with"):
            self._create("my-session-")

    def test_rejects_underscore(self):
        with pytest.raises(ValidationError, match="lowercase letters"):
            self._create("my_session")

    def test_rejects_over_63_chars(self):
        with pytest.raises(ValidationError, match="63"):
            self._create("a" * 64)

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            self._create("")

    def test_rejects_dot(self):
        with pytest.raises(ValidationError, match="lowercase letters"):
            self._create("my.session")

    def test_accepts_session_definition_alias(self):
        sc = SessionCreate(name="my-session", session_definition="skuldCodex")
        assert sc.definition == "skuldCodex"
