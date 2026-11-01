"""The morning Guild summary's pure parts: counter moves, reasons, rollouts, text."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "guild_daily_summary", ROOT / "scripts/guild_daily_summary.py"
)
daily = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = daily  # dataclasses look their module up by name
_spec.loader.exec_module(daily)

WHEN = datetime(2026, 11, 2, 6, 30, tzinfo=UTC)
SINCE = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def stats(**values):
    return {key: values.get(key, 0) for key in daily.PUSH_COUNTERS}


def test_counters_move_since_the_previous_summary():
    moves, restarted = daily.counter_moves(stats(alerts_sent=9), stats(alerts_sent=4))
    assert (moves["alerts_sent"], restarted) == (5, False)


def test_a_counter_that_went_down_means_a_restart():
    moves, restarted = daily.counter_moves(stats(alerts_sent=2), stats(alerts_sent=40))
    assert (moves["alerts_sent"], restarted) == (2, True)


def test_environment_answers_move_like_the_counters():
    now = {"production": {"accepted": 10, "rejected": 3}, "sandbox": {"accepted": 1}}
    before = {"production": {"accepted": 4, "rejected": 3}}
    assert daily.environment_moves(now, before, restarted=False) == {
        "production": {"accepted": 6, "rejected": 0},
        "sandbox": {"accepted": 1},
    }
    assert daily.environment_moves(now, before, restarted=True) == now


def test_rejections_are_counted_by_reason_from_both_log_lines():
    lines = [
        "Deleted a dead widget token e735acfff315 (production) of owner dev-user "
        "(APNs: HTTP 400 BadDeviceToken)",
        "Deleted a dead live_activity token 0123456789ab (production) of owner dev-user "
        "(APNs: HTTP 410 ExpiredToken)",
        "APNs refused a alert push for owner dev-user: HTTP 429 TooManyRequests",
        "unrelated line",
    ]
    assert daily.rejection_reasons(lines) == Counter(
        {
            "HTTP 400 BadDeviceToken (token pruned)": 1,
            "HTTP 410 ExpiredToken (token pruned)": 1,
            "HTTP 429 TooManyRequests": 1,
        }
    )


def test_server_errors_count_only_5xx_answers():
    lines = [
        'INFO:     1.2.3.4:5 - "GET /api/v1/forge/sessions/x HTTP/1.1" 502 Bad Gateway',
        'INFO:     1.2.3.4:5 - "GET /health HTTP/1.1" 200 OK',
        'INFO:     1.2.3.4:5 - "POST /x HTTP/1.1" 500 Internal Server Error',
    ]
    assert daily.server_errors(lines) == 2


def test_rollouts_are_named_by_their_folder_oldest_first_within_the_window():
    lines = [
        ("forge-old", "2026-10-30T10:00:00Z Thor run: rc=1 ROLLED_BACK build=forge-prev"),
        ("forge-b", "2026-11-01T17:42:00Z Thor run: rc=1 ROLLED_BACK build=forge-a"),
        ("forge-b", "2026-11-01T19:02:00Z Thor run: rc=0 CUTOVER_OK build=forge-b"),
        ("forge-b", "2026-11-01T18:57:00Z mini: CUTOVER_OK build=forge-m6-b"),
        ("forge-b", "2026-11-01T18:58:00Z told coordinator: CUTOVER_OK"),
    ]
    rolled, completed = daily.rollout_outcomes(lines, SINCE)
    assert rolled == ["11-01 17:42 Thor forge-b"]
    assert completed == ["11-01 18:57 mini forge-b", "11-01 19:02 Thor forge-b"]


def test_a_growing_log_is_read_from_its_offset_and_a_shrunk_one_from_the_start():
    assert daily.new_log_bytes(500, 200) == (200, 500)
    assert daily.new_log_bytes(100, 200) == (0, 100)  # rotated
    assert daily.new_log_bytes(500, None) == (500, 500)  # first time: nothing yet


def test_the_text_names_hosts_push_and_promotions_without_tokens():
    host = daily.HostSnapshot("Thor", "ok", "forge-x", "4cd719bad0", False, [], "118", 70, 14, 3)
    push = {
        "running": True,
        **stats(alerts_sent=5, tokens_pruned=1),
        "apns_by_environment": {"production": {"accepted": 5}},
        "tokens_by_environment": {"production": {"alert": 1}},
        "feedless_nodes": {"spark": "no feed"},
        "unreachable_nodes": {},
    }
    previous = {"stats": stats(alerts_sent=1), "apns_by_environment": {}}
    text = daily.render(
        WHEN,
        SINCE,
        [host],
        push,
        previous,
        Counter({"HTTP 410 ExpiredToken (token pruned)": 1}),
        [],
        ["11-01 19:02 Thor forge-b"],
    )
    assert "## Guild summary 2026-11-02" in text
    assert "**Thor**: ok, forge-x (4cd719bad, not dirty); ledger 118; 70 sessions" in text
    assert "since the previous summary: alerts_sent 4, tokens_pruned 1" in text
    assert "HTTP 410 ExpiredToken (token pruned) ×1" in text
    assert "Feedless nodes: spark; unreachable: none" in text
    assert "rolled back 0; completed 1 (11-01 19:02 Thor forge-b)" in text
