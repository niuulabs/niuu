"""A session pod's broker reads its Forge session credential from a mounted Secret.

Flux and Kubernetes session pods never carry the token in values or the environment:
Forge mounts it from a per-session Secret and the broker reads the file at start.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from skuld.config import ForgeMcpConfig


def test_the_token_is_read_from_the_mounted_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("session-token\n")
    assert ForgeMcpConfig(token_file=str(token_file)).token == "session-token"


def test_an_explicit_token_wins_over_the_file(tmp_path):
    config = ForgeMcpConfig(token="env-token", token_file=str(tmp_path / "missing"))
    assert config.token == "env-token"


def test_no_file_means_no_session_credential():
    assert ForgeMcpConfig().token == ""


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_a_missing_or_empty_file_is_fatal(tmp_path, content):
    token_file = tmp_path / "token"
    if content is not None:
        token_file.write_text(content)
    with pytest.raises(ValidationError, match="forge_mcp.token_file"):
        ForgeMcpConfig(token_file=str(token_file))
