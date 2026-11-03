"""Native-shape counterexamples for LEX-1421's numeric projection."""

from datetime import UTC, datetime, timedelta

import pytest

from volundr.config import SessionUsageConfig
from volundr.domain.session_usage import fold_usage, observation, projection_key

AT = datetime(2026, 10, 23, tzinfo=UTC)
WINDOWS = SessionUsageConfig().model_windows


def claude(identity="msg_one", *, input=12, read=7234, write=300, output=10, **extra):
    return {
        "type": "assistant",
        "session_id": "native-claude",
        "message": {
            "id": identity,
            "model": "claude-opus-5-5",
            "content": [],
            "usage": {
                "input_tokens": input,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": write,
                "output_tokens": output,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 100,
                    "ephemeral_1h_input_tokens": 200,
                },
            },
        },
        **extra,
    }


class Projection:
    def __init__(self):
        self.state = None
        self.identities = {}
        self.seq = 0

    def feed(self, payload, *, at=None, seq=None):
        self.seq += 1
        event = observation(payload, seq=seq or self.seq, at=at or AT + timedelta(seconds=self.seq))
        if event is None:
            return self.state
        key = projection_key(event, self.state)
        self.state, checkpoint = fold_usage(
            self.state, event, self.identities.get(key), model_windows=WINDOWS
        )
        self.identities[key] = checkpoint
        return self.state


def codex(input=4203, read=7808, output=102, total_input=4203, total_read=7808, total_output=102):
    return {
        "type": "session_usage",
        "engine": "codex",
        "thread_id": "native-codex",
        "turn_id": "one-agent-turn",
        "model": "gpt-6-astra",
        "window": 258400,
        "last": {"input": input, "cache_read": read, "cache_write": 0, "output": output},
        "total": {
            "input": total_input,
            "cache_read": total_read,
            "cache_write": 0,
            "output": total_output,
        },
    }


def test_repeated_and_noncontiguous_claude_blocks_count_response_once():
    p = Projection()
    p.feed(claude(output=1))
    p.feed(claude(output=236))
    p.feed(claude("msg_two", input=30, output=100))
    state = p.feed(claude(output=236))
    assert state["totals"] == {"input": 42, "cache_read": 14468, "cache_write": 600, "output": 336}
    assert state["last_turn"]["input"] == 30
    assert state["context"] == {"tokens": 7564, "window": 1_000_000, "share": 0.007564}


def test_last_context_excludes_output_and_cache_creation_children():
    state = Projection().feed(claude(output=999999))
    assert state["context"]["tokens"] == 7546
    assert state["totals"]["cache_write"] == 300


def test_checkpoint_and_summary_survive_process_restart():
    original = Projection()
    original.feed(claude())
    restarted = Projection()
    restarted.state, restarted.identities = original.state, original.identities
    before = dict(original.state["totals"])
    assert restarted.feed(claude(), seq=100)["totals"] == before


def test_codex_multiple_responses_in_same_turn_use_cumulative_totals():
    p = Projection()
    p.feed(codex())
    p.feed(codex(input=4341, output=71, total_input=8544, total_read=15616, total_output=173))
    state = p.feed(
        codex(input=4483, output=140, total_input=13027, total_read=23424, total_output=313)
    )
    assert state["context"]["tokens"] == 12291
    assert state["context"]["share"] == 12291 / 258400
    assert state["totals"] == {"input": 13027, "cache_read": 23424, "cache_write": 0, "output": 313}
    repeated = p.feed(
        codex(input=4483, output=140, total_input=13027, total_read=23424, total_output=313)
    )
    assert repeated["totals"] == state["totals"]


def test_old_cumulative_snapshot_does_not_rewind_context_or_decrement_totals():
    p = Projection()
    p.feed(codex(total_input=9000, total_output=200))
    before = p.state["totals"].copy()
    after = p.feed(codex(input=1))
    assert after["totals"] == before
    assert after["context"]["tokens"] is None
    assert after["usage_reason"] == "native_counter_regression"
    assert after["totals_complete"] is False


def test_two_native_codex_threads_sum_totals_without_losing_last_context():
    p = Projection()
    p.feed(codex())
    second = codex(input=12, read=0, total_input=12, total_read=0)
    second["thread_id"] = "new-native-thread"
    state = p.feed(second)
    assert state["context"]["tokens"] == 12
    assert state["totals"]["input"] == 4215


def test_legacy_turn_aggregate_never_pretends_to_be_last_response():
    state = Projection().feed(
        {
            "type": "session_usage",
            "engine": "codex",
            "thread_id": "t",
            "response_id": "turn-1",
            "delta": {"input": 100, "cache_read": 0, "cache_write": 0, "output": 10},
            "last": None,
        }
    )
    assert state["totals"]["input"] == 100
    assert state["last_turn"] is None and state["context"]["tokens"] is None


def test_native_window_wins_and_unknown_model_window_is_null():
    p = Projection()
    event = claude()
    event["message"]["model"] = "unknown-model"
    assert p.feed(event)["context"]["share"] is None
    result = {
        "type": "result",
        "session_id": "native-claude",
        "modelUsage": {"unknown-model": {"contextWindow": 200000, "costUSD": 0.75}},
    }
    state = p.feed(result)
    assert state["context"]["window"] == 200000
    assert state["cost_estimate"] == 0.75
    assert p.feed(result)["cost_estimate"] == 0.75


def test_result_counters_are_not_added_to_response_totals():
    p = Projection()
    p.feed(claude())
    before = p.state["totals"].copy()
    p.feed(
        {
            "type": "result",
            "session_id": "native-claude",
            "modelUsage": {
                "claude-opus-5-5": {"inputTokens": 900000, "outputTokens": 6000, "costUSD": 5}
            },
        }
    )
    assert p.state["totals"] == before


def test_tmux_backlog_counts_history_but_exposes_context_only_when_caught_up():
    p = Projection()
    event = claude()
    event.update(type="system", subtype="claude_usage", caught_up=False)
    assert p.feed(event)["context"]["tokens"] is None
    event["caught_up"] = True
    state = p.feed(event)
    assert state["context"]["tokens"] == 7546
    assert state["totals"]["input"] == 12


def test_compaction_replay_counts_once_and_waits_for_new_response():
    p = Projection()
    p.feed(claude())
    boundary = {
        "type": "system",
        "subtype": "compact_boundary",
        "uuid": "boundary-one",
        "session_id": "native-claude",
    }
    state = p.feed(boundary)
    assert state["compactions"] == 1 and state["context"]["tokens"] is None
    assert p.feed(boundary)["compactions"] == 1
    assert p.feed(claude())["context"]["tokens"] is None
    assert p.feed(claude("after-compact", input=3, read=0, write=10))["context"]["tokens"] == 13


def test_reset_clears_context_but_preserves_lifetime_usage_and_native_cost():
    p = Projection()
    p.feed(claude())
    result = {
        "type": "result",
        "session_id": "native-claude",
        "modelUsage": {"claude-opus-5-5": {"costUSD": 5}},
    }
    p.feed(result)
    p.feed({"type": "conversation_reset", "new_conversation_id": "clear-one"})
    assert p.state["context"]["tokens"] is None
    assert p.feed(result)["cost_estimate"] == 5
    result["session_id"] = "native-claude-after-clear"
    result["modelUsage"]["claude-opus-5-5"]["costUSD"] = 2
    assert p.feed(result)["cost_estimate"] == 7
    assert p.state["totals"]["input"] == 12


@pytest.mark.parametrize(
    "payload",
    [
        claude(parent_tool_use_id="worker"),
        claude(isSidechain=True),
        {"type": "assistant", "message": {"usage": {"input_tokens": 3}}},
        {"type": "result", "modelUsage": {"estimated": {"inputTokens": 10}}},
        {"type": "system", "subtype": "compact_boundary"},
        {"type": "session_usage", "engine": "codex", "thread_id": "x", "delta": {"input": 4}},
    ],
)
def test_unidentifiable_estimated_or_child_usage_is_not_measured(payload):
    assert observation(payload, seq=1, at=AT) is None


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10", float("inf")])
def test_malformed_native_counters_are_ignored(bad):
    event = claude()
    event["message"]["usage"]["input_tokens"] = bad
    assert observation(event, seq=1, at=AT) is None


def test_unknown_or_partial_transcript_is_explicit():
    p = Projection()
    p.feed(claude())
    state = p.feed(
        {"type": "system", "subtype": "claude_usage_unavailable", "reason": "record_too_large"}
    )
    assert state["context"]["tokens"] is None
    assert state["usage_available"] is False and state["totals_complete"] is False


def test_per_connection_init_is_not_a_session_start():
    p = Projection()
    assert (
        p.feed(
            {"type": "system", "subtype": "init", "session_id": "x", "_per_connect_handshake": True}
        )
        is None
    )
    state = p.feed({"type": "system", "subtype": "init", "session_id": "x", "model": "m"})
    assert state["started_at"] == (AT + timedelta(seconds=2)).isoformat()
    assert (
        p.feed({"type": "system", "subtype": "init", "session_id": "x"})["started_at"]
        == state["started_at"]
    )


def test_cumulative_replay_after_boundary_does_not_restore_pre_compaction_context():
    p = Projection()
    before = p.feed(codex())["totals"].copy()
    p.feed(
        {
            "type": "system",
            "subtype": "compact_boundary",
            "thread_id": "native-codex",
            "compaction_id": "native-compact-item",
        }
    )
    replay = p.feed(codex())
    assert replay["context"]["tokens"] is None
    assert replay["totals"] == before
    fresh = p.feed(codex(input=200, total_input=4403))
    assert fresh["context"]["tokens"] == 8008


def test_cumulative_replay_after_clear_does_not_restore_context():
    p = Projection()
    p.feed(codex())
    p.feed({"type": "conversation_reset", "new_conversation_id": "new-thread"})
    state = p.feed(codex())
    assert state["context"]["tokens"] is None
    assert state["usage_available"] is False
    assert state["usage_reason"] == "reset_pending_usage"


def test_late_historical_boundary_counts_without_clearing_current_context():
    p = Projection()
    p.feed(claude(), at=AT + timedelta(seconds=20))
    state = p.feed(
        {
            "type": "system",
            "subtype": "compact_boundary",
            "uuid": "old-boundary",
            "timestamp": (AT + timedelta(seconds=10)).isoformat(),
        }
    )
    assert state["compactions"] == 1
    assert state["context"]["tokens"] == 7546


def test_native_sdk_scalar_cost_is_cumulative_and_not_summed_with_model_breakdown():
    p = Projection()
    state = p.feed({"type": "result", "session_id": "sdk-native", "total_cost_usd": 1.25})
    assert state["cost_estimate"] == 1.25
    state = p.feed(
        {
            "type": "result",
            "session_id": "sdk-native",
            "total_cost_usd": 2.5,
            "modelUsage": {"claude-opus-5-5": {"costUSD": 2.5}},
        }
    )
    assert state["cost_estimate"] == 2.5


def test_old_native_result_cannot_overwrite_new_native_model_window():
    p = Projection()
    p.feed(claude())
    current = claude("new-native-response", session_id="new-native")
    p.feed(current)
    p.feed(
        {
            "type": "result",
            "session_id": "new-native",
            "modelUsage": {"claude-opus-5-5": {"contextWindow": 123456}},
        }
    )
    state = p.feed(
        {
            "type": "result",
            "session_id": "native-claude",
            "modelUsage": {"claude-opus-5-5": {"contextWindow": 200000}},
        }
    )
    assert state["context"]["window"] == 123456


def test_codex_context_only_and_window_only_snapshots_do_not_fabricate_totals():
    p = Projection()
    payload = codex()
    payload.pop("total")
    state = p.feed(payload)
    assert state["context"]["tokens"] == 12011
    assert "totals" not in state
    payload.pop("last")
    payload["window"] = 300000
    state = p.feed(payload)
    assert state["context"] == {"tokens": 12011, "window": 300000, "share": 12011 / 300000}


def test_context_only_historical_snapshot_cannot_rewind_even_with_same_native_key():
    p = Projection()
    current = codex(input=5000)
    current.pop("total")
    p.feed(current, at=AT + timedelta(seconds=20))
    old = codex(input=1)
    old.pop("total")
    old["timestamp"] = (AT + timedelta(seconds=10)).isoformat()
    assert p.feed(old)["context"]["tokens"] == 12808


def test_context_only_replay_does_not_restore_across_compaction():
    p = Projection()
    payload = codex()
    payload.pop("total")
    p.feed(payload)
    p.feed({"type": "system", "subtype": "compact_boundary", "uuid": "compact"})
    assert p.feed(payload)["context"]["tokens"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"type": []},
        {"type": {}},
        {"type": "result", "session_id": "s", "modelUsage": []},
        {"type": "result", "session_id": "s", "modelUsage": "bad"},
        {"type": "result", "session_id": "s", "total_cost_usd": 10**400},
        {"type": "result", "session_id": "s", "modelUsage": {"m": {"contextWindow": 10**400}}},
    ],
)
def test_malformed_numeric_telemetry_cannot_break_opaque_log_ingest(payload):
    assert observation(payload, seq=1, at=AT) is None


@pytest.mark.parametrize(
    "usage",
    [
        {"output_tokens": 3},
        {"input_tokens": 12},
        {},
        {
            "input_tokens": 10**400,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": 1,
        },
    ],
)
def test_partial_or_oversized_claude_usage_is_unknown_not_zero(usage):
    payload = claude()
    payload["message"]["usage"] = usage
    assert observation(payload, seq=1, at=AT) is None


def test_resumed_sdk_usage_does_not_claim_complete_history_or_zero_compactions():
    state = Projection().feed(claude())
    assert state["totals_complete"] is False
    assert state.get("compactions") is None


def test_torn_record_can_recover_current_context_but_not_invent_history_coverage():
    p = Projection()
    p.feed(claude())
    p.feed({"type": "system", "subtype": "claude_usage_unavailable", "reason": "incomplete_record"})
    state = p.feed(claude("completed-line"))
    assert state["usage_available"] is True
    assert "usage_reason" not in state
    assert state["totals_complete"] is False


def test_noncontiguous_context_only_replays_do_not_restore_pre_compaction_samples():
    p = Projection()
    first, second = codex(input=50), codex(input=100)
    for payload in (first, second):
        payload.pop("total")
        p.feed(payload)
    p.feed({"type": "system", "subtype": "compact_boundary", "uuid": "compact"})
    assert p.feed(first)["context"]["tokens"] is None
    assert p.feed(second)["context"]["tokens"] is None
    # A new native turn is distinct even when its response has the same counts.
    first["turn_id"] = "new-native-turn"
    assert p.feed(first)["context"]["tokens"] == 7858


def test_old_native_regression_cannot_clear_current_native_context():
    p = Projection()
    p.feed(codex(total_input=9000))
    newer = codex(input=12, read=0, total_input=12, total_read=0)
    newer["thread_id"] = "new-native"
    p.feed(newer)
    assert p.feed(codex())["context"]["tokens"] == 12


def test_cost_replay_is_exactly_idempotent_across_native_sessions():
    p = Projection()
    first = {"type": "result", "session_id": "first", "total_cost_usd": 0.1}
    p.feed(first)
    p.feed({**first, "session_id": "second"})
    assert p.state["cost_estimate"] == 0.2
    assert p.feed(first)["cost_estimate"] == 0.2
