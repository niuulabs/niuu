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
    moves = daily.counter_moves(stats(alerts_sent=9), stats(alerts_sent=4), restarted=False)
    assert moves["alerts_sent"] == 5


def test_after_a_restart_every_counter_counts_even_one_that_grew_past_before():
    moves = daily.counter_moves(stats(alerts_sent=10), stats(alerts_sent=4), restarted=True)
    assert moves["alerts_sent"] == 10


def test_a_restart_is_read_from_the_gateways_own_log():
    assert daily.gateway_restarted(["x", "Push gateway started: alerts=True ..."])
    assert not daily.gateway_restarted(["APNs refused a alert push for owner o: HTTP 429"])


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
    previous = {
        "at": SINCE.isoformat(),
        "stats": stats(alerts_sent=1),
        "apns_by_environment": {},
    }
    log = [
        "Deleted a dead live_activity token 0123456789ab (production) of owner o "
        "(APNs: HTTP 410 ExpiredToken)"
    ]
    text = daily.render(WHEN, SINCE, [host], push, previous, log, [], ["11-01 19:02 Thor forge-b"])
    assert "## Guild summary 2026-11-02" in text
    assert "**Thor**: ok, forge-x (4cd719bad, not dirty); ledger 118; 70 sessions" in text
    assert "since 11-01 06:30: alerts_sent 4, tokens_pruned 1" in text
    assert "HTTP 410 ExpiredToken (token pruned) ×1" in text
    assert "Feedless nodes: spark; unreachable: none" in text
    assert "rolled back 0; completed 1 (11-01 19:02 Thor forge-b)" in text


def test_an_unreadable_gateway_log_is_said_not_read_as_no_rejections():
    push = {"running": True, **stats(), "apns_by_environment": {}}
    text = daily.render(WHEN, SINCE, [], push, None, None, [], [])
    assert "Rejections by reason in this window (gateway log): gateway log unreadable" in text
    assert "since the gateway started" in text


def test_a_failed_commit_is_described_not_raised(monkeypatch):
    def failing(config, text, when):
        raise daily.subprocess.CalledProcessError(
            1, ["git", "-C", "/w", "commit", "-q", "-m", "x"], stderr="https://u:secret@host"
        )

    monkeypatch.setattr(daily, "append_to_doc", failing)
    said = daily.record({}, "text", WHEN)
    assert said.startswith("NOT recorded") and "git commit exit 1" in said
    assert "secret" not in said


def test_the_message_goes_first_and_a_recording_failure_follows_it(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(daily.json.dumps({"state_file": str(tmp_path / "state.json")}))
    sent = []
    monkeypatch.setattr(daily, "gather", lambda c, s, w: ("SUMMARY", {"at": "x"}))
    monkeypatch.setattr(daily, "send", lambda c, text, w: sent.append(text) or "delivered")
    monkeypatch.setattr(daily, "record", lambda c, t, w: "NOT recorded (git commit exit 1)")

    assert daily.main(["run", str(config)]) == 1

    assert sent == [
        "SUMMARY",
        f"Guild summary {daily.datetime.now(daily.UTC):%Y-%m-%d}: NOT recorded (git commit exit 1)",
    ]
    assert daily.json.loads((tmp_path / "state.json").read_text()) == {"at": "x"}


def test_a_failed_message_saves_nothing(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(daily.json.dumps({"state_file": str(tmp_path / "state.json")}))
    monkeypatch.setattr(daily, "gather", lambda c, s, w: ("SUMMARY", {"at": "x"}))

    def down(c, t, w):
        raise OSError("Forge down")

    monkeypatch.setattr(daily, "send", down)
    try:
        daily.main(["run", str(config)])
    except OSError:
        pass
    assert not (tmp_path / "state.json").exists()  # the window is sent next time


def test_a_section_that_never_landed_is_carried():
    landed = "# Guild daily summary\n"
    local = landed + "\n## Guild summary 2026-11-01\n- day one\n"
    assert daily.unlanded_sections(landed, local) == "\n## Guild summary 2026-11-01\n- day one\n"
    assert daily.unlanded_sections(landed, landed) == ""
    assert daily.unlanded_sections("something else\n", local) == ""


def test_5xx_split_into_cutovers_storms_and_routes():
    chains = [
        (
            "r",
            "2026-11-01T01:01:19Z mini: CUTOVER_OK build=x stop=2026-11-01T01:00:45Z "
            "end=2026-11-01T01:00:53Z ledger=1",
        ),
    ]
    cutovers = daily.cutover_windows(chains, daily.timedelta(seconds=60))
    storms = [
        {"name": "loop", "from": "2026-10-31T17:30:00+00:00", "to": "2026-10-31T17:50:00+00:00"}
    ]
    line = '{at} thor-host env[1]: INFO:     1.2.3.4:5 - "GET {path} HTTP/1.1" 502 Bad Gateway'
    lines = [
        line.format(at="2026-11-01T01:00:46+00:00", path="/api/v1/forge/notifications?x=1"),
        line.format(at="2026-10-31T17:41:37+00:00", path="/api/v1/forge/sessions/0000"),
        line.format(
            at="2026-11-01T09:00:00+00:00",
            path="/api/v1/forge/sessions/0b9e1f7a-1c2d-4e5f-8a9b-0c1d2e3f4a5b/status",
        ),
        line.format(
            at="2026-11-01T09:05:00+00:00",
            path="/api/v1/forge/sessions/5d62a3a5-d697-435e-90c9-2dad8b9453fb/status",
        ),
        'no time "GET /x HTTP/1.1" 500 Error',
    ]
    during, stormy, rest, unread = daily.classify_server_errors(lines, cutovers, storms)
    assert (during, stormy, unread) == (1, 1, 1)
    assert rest["GET /api/v1/forge/sessions/{id}/status"] == 2


def test_the_trend_line_compares_this_window_with_the_previous_one():
    assert daily.window_trend(stats(alerts_sent=5), None) is None
    assert daily.window_trend(stats(alerts_sent=5), stats(alerts_sent=2)) == "alerts_sent 2 → 5"
    assert daily.window_trend(stats(), stats()) == "the same"
