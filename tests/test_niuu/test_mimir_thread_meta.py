"""Unit tests for niuu.domain.mimir thread fields (NIU-564).

Covers:
- MimirPageMeta with thread fields constructs correctly
- MimirPageMeta without thread fields: all thread fields are None / defaults
- ThreadState enum values match expected strings
- ThreadContextRef constructs and serialises correctly
"""

from __future__ import annotations

from datetime import UTC, datetime

from niuu.domain.mimir import (
    MimirPageMeta,
    ThreadContextRef,
    ThreadState,
)

# ---------------------------------------------------------------------------
# ThreadState
# ---------------------------------------------------------------------------


class TestThreadState:
    def test_open_value(self) -> None:
        assert ThreadState.open == "open"

    def test_pulling_value(self) -> None:
        assert ThreadState.pulling == "pulling"

    def test_blocked_value(self) -> None:
        assert ThreadState.blocked == "blocked"

    def test_closed_value(self) -> None:
        assert ThreadState.closed == "closed"

    def test_dissolved_value(self) -> None:
        assert ThreadState.dissolved == "dissolved"

    def test_waiting_for_peer_value(self) -> None:
        assert ThreadState.waiting_for_peer == "waiting_for_peer"

    def test_waiting_for_operator_value(self) -> None:
        assert ThreadState.waiting_for_operator == "waiting_for_operator"

    def test_all_states_exist(self) -> None:
        values = {s.value for s in ThreadState}
        assert values == {
            "open",
            "pulling",
            "blocked",
            "waiting_for_peer",
            "waiting_for_operator",
            "closed",
            "dissolved",
        }

    def test_is_str_enum(self) -> None:
        # StrEnum members compare equal to their string values
        assert ThreadState.open == "open"
        assert str(ThreadState.open) == "open"

    def test_round_trip_from_string(self) -> None:
        for state in ThreadState:
            assert ThreadState(state.value) is state


# ---------------------------------------------------------------------------
# ThreadContextRef
# ---------------------------------------------------------------------------


class TestThreadContextRef:
    def test_constructs_with_all_fields(self) -> None:
        ref = ThreadContextRef(
            ref_type="conversation", ref_id="sess-abc", ref_summary="User asked about auth"
        )
        assert ref.ref_type == "conversation"
        assert ref.ref_id == "sess-abc"
        assert ref.ref_summary == "User asked about auth"

    def test_constructs_ingest_type(self) -> None:
        ref = ThreadContextRef(ref_type="ingest", ref_id="src-001", ref_summary="")
        assert ref.ref_type == "ingest"
        assert ref.ref_id == "src-001"
        assert ref.ref_summary == ""

    def test_constructs_observation_type(self) -> None:
        ref = ThreadContextRef(ref_type="observation", ref_id="obs-42", ref_summary="Note")
        assert ref.ref_type == "observation"
        assert ref.ref_id == "obs-42"

    def test_fields_are_plain_strings(self) -> None:
        ref = ThreadContextRef(ref_type="search", ref_id="q-1", ref_summary="s")
        assert isinstance(ref.ref_type, str)
        assert isinstance(ref.ref_id, str)
        assert isinstance(ref.ref_summary, str)


# ---------------------------------------------------------------------------
# MimirPageMeta — thread fields present
# ---------------------------------------------------------------------------


class TestMimirPageMetaWithThreadFields:
    def _make_meta(self, **overrides) -> MimirPageMeta:
        defaults = dict(
            path="technical/auth.md",
            title="Auth Deep-Dive",
            summary="Open question about session tokens",
            category="technical",
            updated_at=datetime(2024, 6, 1, tzinfo=UTC),
            source_ids=["src-1"],
            thread_state=ThreadState.open,
            thread_weight=1.2,
            is_thread=True,
            thread_weight_signals={"age_days": 0.0, "mention_count": 2},
            thread_next_action_hint="Review token storage approach",
            thread_context_refs=[
                ThreadContextRef(ref_type="ingest", ref_id="src-1", ref_summary="")
            ],
            produced_by_thread=False,
        )
        defaults.update(overrides)
        return MimirPageMeta(**defaults)

    def test_thread_state_set(self) -> None:
        meta = self._make_meta()
        assert meta.thread_state == ThreadState.open

    def test_thread_weight_set(self) -> None:
        meta = self._make_meta()
        assert meta.thread_weight == 1.2

    def test_is_thread_true(self) -> None:
        meta = self._make_meta()
        assert meta.is_thread is True

    def test_thread_weight_signals_preserved(self) -> None:
        meta = self._make_meta()
        assert meta.thread_weight_signals["age_days"] == 0.0
        assert meta.thread_weight_signals["mention_count"] == 2

    def test_thread_next_action_hint_set(self) -> None:
        meta = self._make_meta()
        assert meta.thread_next_action_hint == "Review token storage approach"

    def test_thread_context_refs_set(self) -> None:
        meta = self._make_meta()
        assert len(meta.thread_context_refs) == 1
        assert meta.thread_context_refs[0].ref_type == "ingest"
        assert meta.thread_context_refs[0].ref_id == "src-1"

    def test_produced_by_thread_false(self) -> None:
        meta = self._make_meta()
        assert meta.produced_by_thread is False

    def test_produced_by_thread_can_be_true(self) -> None:
        meta = self._make_meta(produced_by_thread=True)
        assert meta.produced_by_thread is True

    def test_non_thread_fields_unchanged(self) -> None:
        meta = self._make_meta()
        assert meta.path == "technical/auth.md"
        assert meta.title == "Auth Deep-Dive"
        assert meta.category == "technical"
        assert meta.source_ids == ["src-1"]

    def test_pulling_thread_state(self) -> None:
        meta = self._make_meta(thread_state=ThreadState.pulling)
        assert meta.thread_state == ThreadState.pulling

    def test_blocked_thread_state(self) -> None:
        meta = self._make_meta(thread_state=ThreadState.blocked)
        assert meta.thread_state == ThreadState.blocked

    def test_multiple_context_refs(self) -> None:
        refs = [
            ThreadContextRef(ref_type="ingest", ref_id="src-1", ref_summary=""),
            ThreadContextRef(
                ref_type="conversation", ref_id="sess-99", ref_summary="live discussion"
            ),
        ]
        meta = self._make_meta(thread_context_refs=refs)
        assert len(meta.thread_context_refs) == 2
        assert meta.thread_context_refs[1].ref_id == "sess-99"


# ---------------------------------------------------------------------------
# MimirPageMeta — no thread fields (plain wiki page)
# ---------------------------------------------------------------------------


class TestMimirPageMetaWithoutThreadFields:
    def _make_plain_meta(self) -> MimirPageMeta:
        return MimirPageMeta(
            path="projects/overview.md",
            title="Project Overview",
            summary="High-level summary of active projects",
            category="projects",
            updated_at=datetime(2024, 5, 10, tzinfo=UTC),
        )

    def test_thread_state_is_none(self) -> None:
        assert self._make_plain_meta().thread_state is None

    def test_thread_weight_is_none(self) -> None:
        assert self._make_plain_meta().thread_weight is None

    def test_is_thread_is_false(self) -> None:
        assert self._make_plain_meta().is_thread is False

    def test_thread_weight_signals_is_none(self) -> None:
        assert self._make_plain_meta().thread_weight_signals is None

    def test_thread_next_action_hint_is_none(self) -> None:
        assert self._make_plain_meta().thread_next_action_hint is None

    def test_thread_context_refs_is_empty_list(self) -> None:
        assert self._make_plain_meta().thread_context_refs == []

    def test_produced_by_thread_is_false(self) -> None:
        assert self._make_plain_meta().produced_by_thread is False

    def test_source_ids_defaults_to_empty_list(self) -> None:
        assert self._make_plain_meta().source_ids == []
