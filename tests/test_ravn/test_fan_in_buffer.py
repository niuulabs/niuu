"""Unit tests for the FanInBuffer in drive_loop.py."""

from ravn.drive_loop import FanInBuffer


class TestMergeStrategy:
    """merge strategy returns immediately — no accumulation."""

    def test_single_event_returns_result(self):
        buf = FanInBuffer()
        result = buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="reviewer",
            consumes_event_types=["code.changed"],
            strategy="merge",
        )
        assert result is not None
        assert result.persona_name == "reviewer"
        assert "code.changed" in result.merged_context

    def test_multi_event_merge_returns_immediately(self):
        buf = FanInBuffer()
        result = buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="merge",
        )
        assert result is not None
        assert buf.pending_count == 0


class TestAllMustPassStrategy:
    """all_must_pass waits for all event types, checks all verdicts != fail."""

    def _make_buffer(self) -> FanInBuffer:
        """Create a FanInBuffer with contributors registered so fan-in accumulates."""
        buf = FanInBuffer()
        # Register contributors so the buffer does NOT short-circuit to immediate return
        buf.set_contributors("some.target", ["coder", "reviewer"])
        return buf

    def test_first_event_returns_none(self):
        buf = self._make_buffer()
        result = buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        assert result is None
        assert buf.pending_count == 1

    def test_second_event_completes(self):
        buf = self._make_buffer()
        buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        result = buf.try_accept_consumer(
            event_type="review.completed",
            event_payload={"persona": "reviewer", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        assert result is not None
        assert buf.pending_count == 0
        assert "Fan-in complete" in result.merged_context
        assert "PASS" in result.merged_context

    def test_different_root_correlation_creates_separate_slots(self):
        buf = self._make_buffer()
        buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "pass"}},
            root_correlation_id="root2",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        assert buf.pending_count == 2

    def test_fail_verdict_shows_in_context(self):
        buf = self._make_buffer()
        buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        result = buf.try_accept_consumer(
            event_type="review.completed",
            event_payload={"persona": "reviewer", "outcome": {"verdict": "fail"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        assert result is not None
        assert "FAIL" in result.merged_context


class TestAnyPassStrategy:
    def test_any_pass_with_one_passing(self):
        buf = FanInBuffer()
        # Register contributors so the buffer accumulates instead of returning immediately
        buf.set_contributors("some.target", ["coder", "reviewer"])
        buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "fail"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="any_pass",
        )
        result = buf.try_accept_consumer(
            event_type="review.completed",
            event_payload={"persona": "reviewer", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="any_pass",
        )
        assert result is not None
        assert "PASS" in result.merged_context


class TestProducerAggregation:
    def test_single_contributor_returns_none(self):
        buf = FanInBuffer()
        buf.set_contributors("review.verdict", ["reviewer", "security-auditor"])
        result = buf.try_accept_producer(
            contributes_to="review.verdict",
            producer_persona="reviewer",
            event_type="review.completed",
            event_payload={"persona": "reviewer", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
        )
        assert result is None
        assert buf.pending_count == 1

    def test_all_contributors_completes(self):
        buf = FanInBuffer()
        buf.set_contributors("review.verdict", ["reviewer", "security-auditor"])
        buf.try_accept_producer(
            contributes_to="review.verdict",
            producer_persona="reviewer",
            event_type="review.completed",
            event_payload={"persona": "reviewer", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
        )
        result = buf.try_accept_producer(
            contributes_to="review.verdict",
            producer_persona="security-auditor",
            event_type="security.completed",
            event_payload={"persona": "security-auditor", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
        )
        assert result is not None
        assert buf.pending_count == 0
        assert "PASS" in result.merged_context

    def test_no_contributors_registered_returns_none(self):
        buf = FanInBuffer()
        result = buf.try_accept_producer(
            contributes_to="unknown.target",
            producer_persona="reviewer",
            event_type="review.completed",
            event_payload={},
            root_correlation_id="root1",
        )
        assert result is None
        assert buf.pending_count == 0


class TestFormatSingleEvent:
    """Tests for _format_single_event enriched payload formatting."""

    def test_basic_format(self):
        ctx = FanInBuffer._format_single_event(
            "code.changed",
            {"persona": "coder", "task_id": "t1"},
        )
        assert "Event type: code.changed" in ctx
        assert "From persona: coder" in ctx
        assert "Source task: t1" in ctx

    def test_includes_task_description(self):
        ctx = FanInBuffer._format_single_event(
            "code.changed",
            {"persona": "coder", "task_description": "Fix the login bug"},
        )
        assert "Task description: Fix the login bug" in ctx

    def test_includes_workspace_path(self):
        ctx = FanInBuffer._format_single_event(
            "code.changed",
            {"persona": "coder", "workspace_path": "/tmp/workspace"},
        )
        assert "Workspace path: /tmp/workspace" in ctx

    def test_includes_files_changed(self):
        ctx = FanInBuffer._format_single_event(
            "code.changed",
            {
                "persona": "coder",
                "files_changed": ["/src/auth.py", "/src/login.py"],
            },
        )
        assert "Files changed (2):" in ctx
        assert "  - /src/auth.py" in ctx
        assert "  - /src/login.py" in ctx

    def test_includes_diff_summary(self):
        ctx = FanInBuffer._format_single_event(
            "code.changed",
            {
                "persona": "coder",
                "diff_summary": "diff --git a/main.py\n+new line",
            },
        )
        assert "Git diff:" in ctx
        assert "diff --git a/main.py" in ctx

    def test_includes_outcome(self):
        ctx = FanInBuffer._format_single_event(
            "code.changed",
            {"persona": "coder", "outcome": {"verdict": "pass"}},
        )
        assert "Outcome:" in ctx
        assert "pass" in ctx

    def test_enriched_payload_all_fields(self):
        """Full enriched payload includes all fields in correct order."""
        ctx = FanInBuffer._format_single_event(
            "code.changed",
            {
                "persona": "coder",
                "task_description": "Fix auth",
                "workspace_path": "/ws",
                "files_changed": ["/src/a.py"],
                "diff_summary": "diff text",
                "outcome": {"verdict": "pass"},
            },
        )
        lines = ctx.split("\n")
        # Task description should come before diff
        task_idx = next(i for i, line in enumerate(lines) if "Task description" in line)
        diff_idx = next(i for i, line in enumerate(lines) if "Git diff" in line)
        assert task_idx < diff_idx

    def test_omits_empty_enriched_fields(self):
        """Empty enriched fields should not appear in context."""
        ctx = FanInBuffer._format_single_event(
            "code.changed",
            {"persona": "coder"},
        )
        assert "Task description" not in ctx
        assert "Workspace path" not in ctx
        assert "Files changed" not in ctx
        assert "Git diff" not in ctx
        assert "Outcome" not in ctx


class TestExpiry:
    def test_sweep_removes_expired_slots(self):
        buf = FanInBuffer(ttl_seconds=0.001)
        # Register contributors so the buffer accumulates
        buf.set_contributors("some.target", ["coder", "reviewer"])
        buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        assert buf.pending_count == 1

        import time

        time.sleep(0.01)

        expired = buf.sweep_expired()
        assert len(expired) == 1
        assert buf.pending_count == 0

    def test_sweep_keeps_non_expired(self):
        buf = FanInBuffer(ttl_seconds=300)
        # Register contributors so the buffer accumulates
        buf.set_contributors("some.target", ["coder", "reviewer"])
        buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        expired = buf.sweep_expired()
        assert len(expired) == 0
        assert buf.pending_count == 1


class TestPersistence:
    def test_round_trip(self):
        buf = FanInBuffer()
        # Register contributors so the buffer accumulates
        buf.set_contributors("some.target", ["coder", "reviewer"])
        buf.try_accept_consumer(
            event_type="code.changed",
            event_payload={"persona": "coder", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        data = buf.to_dict()
        assert len(data) == 1

        buf2 = FanInBuffer()
        # Register contributors on the restored buffer too
        buf2.set_contributors("some.target", ["coder", "reviewer"])
        buf2.load_dict(data)
        assert buf2.pending_count == 1

        # Complete the fan-in in the restored buffer
        result = buf2.try_accept_consumer(
            event_type="review.completed",
            event_payload={"persona": "reviewer", "outcome": {"verdict": "pass"}},
            root_correlation_id="root1",
            persona_name="qa-agent",
            consumes_event_types=["code.changed", "review.completed"],
            strategy="all_must_pass",
        )
        assert result is not None
        assert buf2.pending_count == 0
