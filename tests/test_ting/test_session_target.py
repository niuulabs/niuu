"""Existing-session operations must follow the owner, not placement order."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ting.domain.services.session_target import find_session_target


async def test_missing_session_does_not_select_unrelated_target():
    targets = [SimpleNamespace(get_session=AsyncMock(return_value=None)) for _ in range(2)]
    with pytest.raises(LookupError, match="session-1"):
        await find_session_target(targets, "session-1")


async def test_failed_lookup_does_not_redirect_operation():
    failed = SimpleNamespace(get_session=AsyncMock(side_effect=RuntimeError("unavailable")))
    other = SimpleNamespace(get_session=AsyncMock(return_value=object()))
    with pytest.raises(RuntimeError, match="unavailable"):
        await find_session_target([failed, other], "session-1")
    other.get_session.assert_not_awaited()
