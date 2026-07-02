"""Coverage for the Letheo resident-state adapter (session patched; package optional)."""

from __future__ import annotations

import pytest

from ravn.adapters.resident_state import letheo as letheo_mod
from ravn.adapters.resident_state.letheo import LetheoResidentStateAdapter
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentTurnRecord


class _FakeSession:
    def __init__(self, *, mode: str = "unified") -> None:
        self.mode = mode
        self.perceived: list[tuple[str, str]] = []
        self.breaths = 0

    def perceive(self, subject: str, *, act: str) -> None:
        self.perceived.append((subject, act))

    def breathe(self) -> None:
        self.breaths += 1

    def evoke_unified(self, subject: str, query: str) -> str:
        return f"unified:{query}" if self.mode == "unified" else ""

    def recall(self, subject: str, query: str, k: int = 3):  # noqa: ARG002
        return f"recalled:{query}" if self.mode == "recall" else ""


class _RecallOnlySession:
    """Only recall() (no evoke_unified) — exercises the recall fallback path."""

    def recall(self, subject: str, query: str, k: int = 3):  # noqa: ARG002
        return f"recalled:{query}"


class _BareSession:
    """No perceive/breathe/evoke/recall — exercises the empty-evoke path."""


def _turn() -> ResidentTurnRecord:
    return ResidentTurnRecord(
        turn_index=1,
        prompt="p",
        response="r",
        outcome_fields={},
        tool_names=(),
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )


def _adapter(tmp_path, session, monkeypatch) -> LetheoResidentStateAdapter:
    monkeypatch.setattr(letheo_mod, "_load_letheo_session", lambda: session)
    return LetheoResidentStateAdapter(tmp_path, subject="ravn:test")


@pytest.mark.asyncio
async def test_write_turn_perceives_and_breathes(tmp_path, monkeypatch) -> None:
    session = _FakeSession()
    adapter = _adapter(tmp_path, session, monkeypatch)
    ref = await adapter.write_turn(_turn())
    assert ref  # also persisted locally
    assert session.perceived and ref in session.perceived[0][1]
    assert session.breaths == 1


@pytest.mark.asyncio
async def test_artifact_reads_use_inherited_local_state(tmp_path, monkeypatch) -> None:
    adapter = _adapter(tmp_path, _FakeSession(), monkeypatch)

    ref = await adapter.write_artifact("resident/momentum/demo.md", "# Demo\n\ncontent")
    artifact = await adapter.read_artifact(ref)

    assert artifact.path == ref
    assert artifact.content == "# Demo\n\ncontent"


@pytest.mark.asyncio
async def test_recall_prepends_unified_evocation(tmp_path, monkeypatch) -> None:
    adapter = _adapter(tmp_path, _FakeSession(mode="unified"), monkeypatch)
    await adapter.write_turn(_turn())
    recalled = await adapter.recall("what now", limit=5)
    assert recalled[0].path == "letheo:ravn:test"
    assert recalled[0].content == "unified:what now"


@pytest.mark.asyncio
async def test_recall_uses_recall_fallback(tmp_path, monkeypatch) -> None:
    adapter = _adapter(tmp_path, _RecallOnlySession(), monkeypatch)
    recalled = await adapter.recall("topic")
    assert recalled and recalled[0].content == "recalled:topic"


@pytest.mark.asyncio
async def test_recall_without_evocation_returns_local_only(tmp_path, monkeypatch) -> None:
    adapter = _adapter(tmp_path, _BareSession(), monkeypatch)
    await adapter.write_turn(_turn())
    recalled = await adapter.recall("topic")
    assert all(not e.path.startswith("letheo:") for e in recalled)


def test_load_letheo_session_requires_package(tmp_path) -> None:
    # letheo_orchestration is not installed in the test env -> construction raises.
    with pytest.raises(RuntimeError, match="letheo_orchestration"):
        LetheoResidentStateAdapter(tmp_path)
