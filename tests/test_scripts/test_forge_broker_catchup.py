"""LEX-1957: a promotion brings every running session's broker up to the broker contract."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "forge_broker_catchup", ROOT / "scripts/forge_broker_catchup.py"
)
catchup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catchup)

BASE = "http://forge"
API = f"{BASE}/api/v1/forge"
UNTIL = "2099-01-01T00:00:00Z"


class FakeForge:
    """Running sessions whose brokers report a contract state; stop/start relaunches current."""

    def __init__(self, *sessions: tuple[str, str, str]):
        self.rows = {
            sid: {
                "id": sid,
                "name": name,
                "status": "running",
                "activity_state": "idle",
                "cli_session_id": f"conv-{sid}",
            }
            for sid, name, _ in sessions
        }
        self.contract = {sid: state for sid, _, state in sessions}
        self.calls: list[str] = []
        self.start_new_conversation: set[str] = set()
        self.start_fails: set[str] = set()
        self.no_transcript: set[str] = set()
        self.judged: list[list[str]] = []

    def __call__(self, method: str, url: str):
        path = url.removeprefix(API)
        self.calls.append(f"{method} {path}")
        if path == "/sessions?scope=local":
            return 200, list(self.rows.values())
        sid, _, action = path.removeprefix("/sessions/").partition("/")
        row = self.rows[sid]
        if action == "":
            return 200, row
        if action == "runtime-version":
            if row["status"] != "running":
                return 200, {"contract": {"state": "not_running"}}
            return 200, {
                "current": {"build": "old" if self.contract[sid] != "met" else "new"},
                "contract": {"required": 1, "state": self.contract[sid]},
            }
        if action == "stop":
            row["status"] = "stopped"
            return 200, row
        if action == "start":
            if sid in self.start_fails:
                return 503, "no capacity"
            row["status"] = "running"
            self.contract[sid] = "met"
            if sid in self.start_new_conversation:
                row["cli_session_id"] = "fresh"
            return 200, row
        raise AssertionError(url)


@pytest.fixture
def forge(monkeypatch):
    def install(*sessions, busy=None):
        fake = FakeForge(*sessions)
        monkeypatch.setattr(catchup, "_call", fake)
        monkeypatch.setattr(catchup.time, "sleep", lambda _s: None)
        monkeypatch.setattr(catchup, "transcript_exists", lambda cid: cid not in fake.no_transcript)
        monkeypatch.setattr(
            catchup,
            "live_resume",
            lambda tmux, sock, sid: (
                (f"pid-{sid}", fake.rows[sid]["cli_session_id"])
                if fake.rows[sid]["status"] == "running" and sid not in fake.start_new_conversation
                else (f"pid-{sid}", "fresh")
                if fake.rows[sid]["status"] == "running"
                else None
            ),
        )
        fake.busy = busy if busy is not None else {}

        def judge(base, tmux, only, background=False, socket_dir=None):
            assert base == BASE and background
            fake.judged.append(list(only))
            running = [sid for sid in only if fake.rows[sid]["status"] == "running"]
            return {
                "lanes": len(running),
                "judged": running,
                "busy": [
                    {"id": sid, "reasons": fake.busy[sid]} for sid in running if sid in fake.busy
                ],
                "host_trains": [],
            }

        monkeypatch.setattr(catchup.forge_lane_busy, "judge", judge)
        return fake

    return install


def _run(capsys, *extra: str) -> tuple[int, dict]:
    code = catchup.main(["restart", BASE, "tmux", "--until", UNTIL, *extra])
    out = capsys.readouterr().out
    return code, json.loads(out[out.index("\n{") + 1 :] if "\n{" in out else out)


def _soon() -> str:
    return (
        catchup.datetime.now(catchup.UTC) + __import__("datetime").timedelta(seconds=1)
    ).isoformat()


def test_audit_lists_each_running_broker_and_fails_while_one_is_below(forge, capsys):
    forge(("a", "lexi", "below"), ("b", "nepal", "met"))
    assert catchup.main(["audit", BASE]) == 1
    rows = json.loads(capsys.readouterr().out)
    assert [(r["name"], r["contract"], r["build"]) for r in rows] == [
        ("lexi", "below", "old"),
        ("nepal", "met", "new"),
    ]


def test_sessions_below_the_contract_restart_and_resume_their_conversation(forge, capsys):
    fake = forge(("a", "lexi", "below"), ("b", "nepal", "met"))
    code, outcome = _run(capsys)
    assert code == 0
    assert outcome == {"a": {**outcome["a"], "result": "restarted"}}
    assert "POST /sessions/a/stop" in fake.calls and "POST /sessions/a/start" in fake.calls
    assert not any(c.startswith("POST /sessions/b") for c in fake.calls)


def test_a_held_session_is_listed_and_never_restarted(forge, capsys):
    fake = forge(("a", "ios-coordinator", "below"), ("b", "lexi", "below"))
    code, outcome = _run(capsys, "--hold", "ios-coordinator")
    assert code == 0
    assert outcome["a"]["result"] == "held: restart only on its owner's GO"
    assert outcome["b"]["result"] == "restarted"
    assert not any(c.startswith("POST /sessions/a") for c in fake.calls)


def test_only_restarts_a_held_session_its_owner_gave_a_go_for(forge, capsys):
    fake = forge(("a", "ios-coordinator", "below"), ("b", "lexi", "below"))
    code, outcome = _run(capsys, "--hold", "b", "--only", "a")
    assert code == 0
    assert list(outcome) == ["a"] and outcome["a"]["result"] == "restarted"
    assert not any(c.startswith("POST /sessions/b") for c in fake.calls)


def test_a_busy_session_is_reported_at_the_deadline_not_restarted(forge, capsys):
    fake = forge(("a", "lexi", "below"), busy={"a": ["train: pid 9, 01:00: xcodebuild"]})
    code = catchup.main(["restart", BASE, "tmux", "--until", _soon(), "--retry", "0"])
    out = capsys.readouterr().out
    assert code == 1
    assert json.loads(out)["a"]["result"] == (
        "busy until the deadline: train: pid 9, 01:00: xcodebuild"
    )
    assert not any(c.startswith("POST") for c in fake.calls)


def test_a_restart_that_loses_the_conversation_does_not_count(forge, capsys):
    fake = forge(("a", "lexi", "below"))
    fake.start_new_conversation.add("a")
    code, outcome = _run(capsys)
    assert code == 1
    assert outcome["a"]["result"] == (
        "started, but its Claude is not resuming conv-a (live: ('pid-a', 'fresh')): context lost?"
    )


def test_a_session_with_no_conversation_id_is_never_stopped(forge, capsys):
    fake = forge(("a", "lexi", "below"))
    fake.rows["a"]["cli_session_id"] = None
    code, outcome = _run(capsys)
    assert code == 1
    assert outcome["a"]["result"] == "not restarted: no recorded conversation id to resume"
    assert not any(c.startswith("POST") for c in fake.calls)


def test_a_failed_start_says_the_session_is_stopped(forge, capsys):
    fake = forge(("a", "lexi", "below"))
    fake.start_fails.add("a")
    code, outcome = _run(capsys)
    assert code == 1
    assert outcome["a"]["result"] == (
        "STOPPED, start failed: HTTP 503 no capacity; start it by hand"
    )
    assert fake.calls.count("POST /sessions/a/start") == 1


def test_an_unprobed_broker_is_reported_not_restarted(forge, capsys):
    fake = forge(("a", "lexi", "unknown"))
    code, outcome = _run(capsys)
    assert code == 1
    assert outcome["a"]["result"] == "broker not probed (unknown)"
    assert not any(c.startswith("POST") for c in fake.calls)


def test_evidence_records_each_outcome(forge, capsys, tmp_path):
    forge(("a", "lexi", "below"))
    evidence = tmp_path / "catchup.json"
    _run(capsys, "--evidence", str(evidence))
    assert json.loads(evidence.read_text())["a"]["result"] == "restarted"


def test_the_busy_gate_runs_again_right_before_each_stop(forge, capsys):
    fake = forge(("a", "lexi-todo", "below"), ("b", "trickle", "below"))
    _run(capsys)
    assert fake.judged == [["a"], ["b"]]


def test_a_session_that_stopped_meanwhile_is_not_started_again(forge, capsys, monkeypatch):
    fake = forge(("a", "lexi-todo", "below"))
    real_audit = catchup.audit

    audits = []

    def audit_then_owner_stops(base):
        rows = real_audit(base)
        audits.append(base)
        if len(audits) == 2:  # the owner stops it between the loop's audit and the gate
            fake.rows["a"]["status"] = "stopped"
        return rows

    monkeypatch.setattr(catchup, "audit", audit_then_owner_stops)
    code = catchup.main(["restart", BASE, "tmux", "--until", UNTIL])
    capsys.readouterr()
    assert code == 1
    assert "POST /sessions/a/start" not in fake.calls


def test_the_canary_fails_unless_it_really_restarted(forge, capsys):
    """Review: --only exited 0 when its session was busy until the deadline or already met."""
    forge(("a", "trickle", "met"))
    code, outcome = _run(capsys, "--only", "a")
    assert code == 1
    assert outcome["a"]["result"] == "not restarted: already meets the contract"


def test_a_busy_canary_fails_at_the_deadline(forge, capsys):
    forge(("a", "trickle", "below"), busy={"a": ["background agent x1 running"]})
    code = catchup.main(["restart", BASE, "tmux", "--until", "2000-01-01T00:00:00Z", "--only", "a"])
    out = capsys.readouterr().out
    assert code == 1
    assert json.loads(out)["a"]["result"] == "deadline passed before it was tried"


def test_only_refuses_a_session_that_is_not_running_here(forge):
    forge(("a", "trickle", "below"))
    with pytest.raises(SystemExit, match="not running here"):
        catchup.main(["restart", BASE, "tmux", "--until", UNTIL, "--only", "zz"])


def test_hold_must_name_exactly_one_running_session(forge):
    forge(("a", "lexi", "below"), ("b", "lexi", "below"))
    with pytest.raises(SystemExit, match="names 2 running sessions"):
        catchup.main(["restart", BASE, "tmux", "--until", UNTIL, "--hold", "lexi"])


def test_a_deadline_without_a_timezone_is_refused(forge):
    forge(("a", "lexi", "below"))
    with pytest.raises(SystemExit, match="timezone"):
        catchup.main(["restart", BASE, "tmux", "--until", "2026-11-06T23:00:00"])


def test_no_transcript_means_no_stop(forge, capsys):
    fake = forge(("a", "lexi", "below"))
    fake.no_transcript.add("conv-a")
    code, outcome = _run(capsys)
    assert code == 1
    assert outcome["a"]["result"].startswith("not restarted: no transcript")
    assert not any(c.startswith("POST") for c in fake.calls)


def test_an_error_after_the_stop_is_reported_as_possibly_stopped(forge, capsys):
    fake = forge(("a", "lexi", "below"), ("b", "trickle", "below"))
    real = fake.__call__

    def flaky(method, url):
        if url.endswith("/sessions/a") and fake.rows["a"]["status"] == "stopped":
            return 502, "bad gateway"
        return real(method, url)

    catchup._call = flaky
    try:
        code, outcome = _run(capsys)
    finally:
        catchup._call = fake
    assert code == 1
    assert outcome["a"]["result"].startswith("STOPPED? RuntimeError after the stop")
    assert outcome["b"]["result"] == "restarted"


# -- Codex sessions (LEX-1967) ------------------------------------------------------------


def _rollout_line(ordinal: int, kind: str = "item_completed") -> str:
    return json.dumps({"ordinal": ordinal, "type": "event_msg", "payload": {"type": kind}})


@pytest.fixture
def codex(forge, monkeypatch, tmp_path):
    """One Codex session: an app-server per broker start, holding the thread's rollout."""

    def install(state="below", *, reopens=True, replace=False, turns_after=None, twins=False):
        fake = forge(("cx", "agent-service", state))
        thread = fake.rows["cx"]["cli_session_id"]
        home = tmp_path / "codex"
        rollout = home / f"sessions/2026/10/24/rollout-2026-10-24T05-00-13-{thread}.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text("\n".join(_rollout_line(n) for n in range(1, 4)) + "\n")
        fake.app = {"n": 0}
        fake.turns = [7, 7 if turns_after is None else turns_after]

        def app_servers(sid, procs, proc):
            if fake.rows[sid]["status"] != "running":
                return []
            if fake.calls.count("POST /sessions/cx/start") and fake.app["n"] == 0:
                fake.app["n"] = 1
                if replace:
                    rollout.unlink()
                    rollout.write_text("\n".join(_rollout_line(n) for n in (9, 8, 7)) + "\n")
            if twins and fake.app["n"]:
                return ["app-0", "app-1"]  # the old one never exited
            return [f"app-{fake.app['n']}"]

        def app_server(sid, procs, proc):
            found = app_servers(sid, procs, proc)
            return found[0] if len(found) == 1 else None

        monkeypatch.setattr(catchup.forge_lane_busy, "codex_app_servers", app_servers)
        monkeypatch.setattr(catchup.forge_lane_busy, "codex_app_server", app_server)
        monkeypatch.setattr(catchup.forge_lane_busy, "codex_home", lambda app: home)
        monkeypatch.setattr(
            catchup.forge_lane_busy,
            "open_rollouts",
            lambda app: [rollout] if (app == "app-0" or reopens) else [],
        )
        monkeypatch.setattr(catchup, "_procs", lambda: {})
        monkeypatch.setattr(
            catchup, "turn_count", lambda api, sid: fake.turns[min(fake.app["n"], 1)]
        )
        monkeypatch.setattr(
            catchup, "transcript_exists", lambda cid: pytest.fail("a Codex thread is not Claude's")
        )
        fake.rollout = rollout
        return fake

    return install


def test_a_codex_session_resumes_its_thread_with_proof(codex, capsys, tmp_path):
    fake = codex()
    evidence = tmp_path / "ev.json"
    code, outcome = _run(capsys, "--only", "cx", "--evidence", str(evidence))
    assert code == 0 and outcome["cx"]["result"] == "restarted"
    proof = outcome["cx"]["proof"]
    assert proof["before"]["rollout"] == str(fake.rollout)
    assert proof["before"]["last_ordinal"] == 3 and proof["before"]["turns"] == 7
    assert proof["after"]["app_server_pid"] == "app-1" and proof["after"]["turns"] == 7
    assert json.loads(evidence.read_text())["cx"]["proof"] == proof


def test_a_codex_session_whose_thread_is_not_reopened_fails(codex, capsys):
    codex(reopens=False)
    code, outcome = _run(capsys, "--only", "cx", "--start-timeout", "0")
    assert code == 1
    assert "no new Codex app-server reopened" in outcome["cx"]["result"]


def test_a_codex_rollout_replaced_on_resume_fails(codex, capsys):
    codex(replace=True)
    code, outcome = _run(capsys, "--only", "cx")
    assert code == 1 and "was replaced or shrank" in outcome["cx"]["result"]


def test_a_codex_session_whose_turns_drop_fails(codex, capsys):
    codex(turns_after=6)
    code, outcome = _run(capsys, "--only", "cx")
    assert code == 1 and outcome["cx"]["result"] == "started, but its turns dropped 7 -> 6"


def test_even_if_met_restarts_a_named_session_on_the_contract(codex, capsys):
    codex(state="met")
    code, outcome = _run(capsys, "--only", "cx")
    assert outcome["cx"]["result"] == "not restarted: already meets the contract"
    code, outcome = _run(capsys, "--only", "cx", "--even-if-met")
    assert code == 0 and outcome["cx"]["result"] == "restarted"


def _appended(capsys, evidence) -> tuple[int, dict]:
    code = catchup.main(["codex-appended", str(evidence), "cx"])
    return code, json.loads(capsys.readouterr().out)


def test_codex_appended_proves_the_next_turn_continues_the_same_file(codex, capsys, tmp_path):
    fake = codex()
    evidence = tmp_path / "ev.json"
    _run(capsys, "--only", "cx", "--evidence", str(evidence))
    with open(fake.rollout, "a") as stream:
        stream.write(
            _rollout_line(4, "task_started") + "\n" + _rollout_line(5, "task_complete") + "\n"
        )
    code, verdict = _appended(capsys, evidence)
    assert code == 0 and verdict["problems"] == []
    assert verdict["first_ordinal"] == 4 and verdict["completed_turns"] == 1


def test_codex_appended_fails_on_a_gap_or_nothing_new(codex, capsys, tmp_path):
    fake = codex()
    evidence = tmp_path / "ev.json"
    _run(capsys, "--only", "cx", "--evidence", str(evidence))
    code, verdict = _appended(capsys, evidence)
    assert code == 1 and "nothing was appended" in verdict["problems"][0]
    with open(fake.rollout, "a") as stream:
        stream.write(_rollout_line(9, "task_complete") + "\n")
    code, verdict = _appended(capsys, evidence)
    assert code == 1 and verdict["problems"] == ["ordinals do not continue from 3: [9]..."]


def test_even_if_met_needs_only(codex, capsys):
    codex(state="met")
    with pytest.raises(SystemExit, match="--even-if-met restarts only sessions named"):
        catchup.main(["restart", BASE, "tmux", "--until", UNTIL, "--even-if-met"])


def test_a_codex_restart_with_the_old_app_server_still_up_fails(codex, capsys):
    codex(twins=True)
    code, outcome = _run(capsys, "--only", "cx", "--start-timeout", "0")
    assert code == 1 and "no new Codex app-server reopened" in outcome["cx"]["result"]


def test_a_thread_id_that_changes_while_settling_fails(codex, capsys, monkeypatch):
    fake = codex()
    real_sleep = []

    def settle(seconds):
        real_sleep.append(seconds)
        if seconds == 15.0:  # the settle: Forge now reports another thread
            fake.rows["cx"]["cli_session_id"] = "another"

    monkeypatch.setattr(catchup.time, "sleep", settle)
    code, outcome = _run(capsys, "--only", "cx")
    assert code == 1
    assert outcome["cx"]["result"].startswith("started, then within 15s its thread is another")


def test_codex_appended_needs_a_completed_restart(codex, capsys, tmp_path):
    codex(reopens=False)
    evidence = tmp_path / "ev.json"
    _run(capsys, "--only", "cx", "--start-timeout", "0", "--evidence", str(evidence))
    with pytest.raises(SystemExit, match="no completed Codex restart"):
        catchup.main(["codex-appended", str(evidence), "cx"])


def test_last_ordinal_reads_within_the_recorded_size_and_past_huge_records(tmp_path):
    path = tmp_path / "rollout-x.jsonl"
    big = json.dumps({"type": "response_item", "payload": {"output": "x" * 3000}})
    path.write_text(_rollout_line(5) + "\n" + big + "\n")
    assert catchup.last_ordinal(path, chunk=512) == 5
    size = path.stat().st_size
    with open(path, "a") as stream:
        stream.write(_rollout_line(6) + "\n")
    assert catchup.last_ordinal(path, size) == 5 and catchup.last_ordinal(path) == 6


def test_a_recorded_size_never_lands_inside_a_record(tmp_path):
    path = tmp_path / "rollout-x.jsonl"
    path.write_text(_rollout_line(1) + "\n" + '{"ordinal": 2, "pay')
    whole = len(_rollout_line(1)) + 1
    assert catchup.record_boundary(path, path.stat().st_size) == whole
    assert catchup.record_boundary(path, whole) == whole
    assert catchup.record_boundary(path, 3) == 0


def test_codex_appended_checks_the_records_written_during_the_restart(codex, capsys, tmp_path):
    fake = codex()
    evidence = tmp_path / "ev.json"
    _run(capsys, "--only", "cx", "--evidence", str(evidence))
    record = json.loads(evidence.read_text())
    # A record written while restarting that resets the numbering, then a turn after it.
    before = record["cx"]["proof"]["before"]
    with open(fake.rollout, "a") as stream:
        stream.write(_rollout_line(0) + "\n")
    size = fake.rollout.stat().st_size
    record["cx"]["proof"]["after"].update(
        size=size,
        last_ordinal=0,
        prefix_digest=catchup.prefix_digest(fake.rollout, size),
    )
    evidence.write_text(json.dumps(record))
    with open(fake.rollout, "a") as stream:
        stream.write(_rollout_line(1, "task_complete") + "\n")
    code, verdict = _appended(capsys, evidence)
    assert code == 1
    assert verdict["problems"] == [
        f"ordinals do not continue from {before['last_ordinal']}: [0, 1]..."
    ]
