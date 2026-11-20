"""LEX-1706: a lane is restartable only when idle at its prompt with no tool command running."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "forge_lane_busy", ROOT / "scripts/forge_lane_busy.py"
)
lane_busy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lane_busy)

PS = """\
  100     1 10:00 tmux -S /tmp/skuld-tmux-501/skuld-a.sock new-session
  200   100 10:00 claude --resume
  201   200 10:00 node /Users/x/.local/bin/rea mcp
  202   200 10:00 /venv/bin/python -P -m skuld.forge_mcp --broker
  300   100 05:00 claude --resume
  301   300 05:00 /bin/zsh -c source /Users/x/.claude/shell-snapshots/s.sh && eval 'release.py'
  302   301 05:00 /usr/bin/python3 tools/release.py --build 2442
  303   302 03:00 /usr/bin/xcodebuild -scheme LexiChat archive
  400   100 01:00 claude --resume
  401   400 00:10 /bin/bash -c source /home/x/.claude/shell-snapshots/s.sh && eval ls
  500     1 02:00 /usr/bin/python3 tools/release.py --resume 2442
"""


def test_an_idle_lane_with_only_its_mcp_servers_may_restart():
    procs = lane_busy.parse_ps(PS)
    reasons, pids = lane_busy.lane_reasons("idle", "200", procs)
    assert reasons == []
    assert pids == {"201", "202"}


def test_a_lane_forge_calls_idle_is_busy_while_its_train_runs():
    reasons, _ = lane_busy.lane_reasons("idle", "300", lane_busy.parse_ps(PS))
    assert reasons and reasons[0].startswith("train: ")


def test_a_running_tool_command_makes_a_lane_busy():
    reasons, _ = lane_busy.lane_reasons("idle", "400", lane_busy.parse_ps(PS))
    assert reasons and reasons[0].startswith("pid 401, 00:10, tool command:")


def test_a_lane_not_idle_in_forge_is_busy():
    reasons, _ = lane_busy.lane_reasons("awaiting_input", "200", lane_busy.parse_ps(PS))
    assert reasons == ["forge activity awaiting_input"]


def test_a_detached_train_is_a_host_train():
    procs = lane_busy.parse_ps(PS)
    _, lane_pids = lane_busy.lane_reasons("idle", "300", procs)
    trains = lane_busy.host_trains(procs, lane_pids)
    assert [t.split()[0] for t in trains] == ["500"]


def test_a_launchd_job_does_not_make_the_host_busy(monkeypatch, capsys):
    rows = [{"id": "s1", "name": "ios-release", "status": "running", "activity_state": "idle"}]
    monkeypatch.setattr(lane_busy.urllib.request, "urlopen", lambda *a, **k: _Json(rows))

    def run(cmd, **kwargs):
        if cmd[0] == "ps":
            return _Done(PS)
        return _Done("200\n")  # the lane's pane: only its MCP servers

    monkeypatch.setattr(lane_busy.subprocess, "run", run)
    monkeypatch.setattr(lane_busy.os, "getuid", lambda: 501)

    assert lane_busy.main(["http://forge", "tmux"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["busy"] == []
    # pane 300 is not this lane's, so its train is not in the judged pane tree either
    assert [t.split()[0] for t in report["host_trains"]] == ["301", "302", "303", "500"]


class _Json(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


class _Done:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode


WRAP = (
    "/bin/zsh -c source /Users/x/.claude/shell-snapshots/s.sh 2>/dev/null || true"
    " && eval '{}' < /dev/null && pwd -P >| /tmp/claude-1-cwd"
)
POLLING = """\
  600     1 05:00 claude --resume
  601   600 04:56:24 {wait}
  602   601 00:06 sleep 15
  610   600 02:35:33 {then}
  611   610 00:04 sleep 30
""".format(
    wait=WRAP.format('until ! pgrep -f "lane LEX-1715-device" >/dev/null; do sleep 15; done'),
    then=WRAP.format("until grep -q RESULT: /tmp/qa.log; do sleep 30; done; ./tag-release.sh"),
)


def test_blocking_shells_are_named_with_pid_age_command_and_what_they_wait_on():
    reasons, _ = lane_busy.lane_reasons("idle", "600", lane_busy.parse_ps(POLLING))

    assert reasons == [
        'pid 601, 04:56:24, polling loop, only waiting: until ! pgrep -f "lane LEX-1715-device"'
        " >/dev/null; do sleep 15; done (now: sleep 15)",
        "pid 610, 02:35:33, polling loop, then runs: ./tag-release.sh: until grep -q RESULT:"
        " /tmp/qa.log; do sleep 30; done; ./tag-release.sh (now: sleep 30)",
    ]


def test_a_shell_that_only_names_a_train_is_not_a_train():
    ps = "  700     1 01:00 claude\n  701   700 00:30 {}\n  702   701 00:01 sleep 15\n".format(
        WRAP.format("until ! pgrep -f qa.sh; do sleep 15; done")
    )
    reasons, _ = lane_busy.lane_reasons("idle", "700", lane_busy.parse_ps(ps))

    assert len(reasons) == 1 and reasons[0].startswith("pid 701, 00:30, polling loop")


def test_quotes_inside_the_agent_command_are_restored():
    cmd = WRAP.format("grep -E '\"'\"'a|b'\"'\"' log")
    assert lane_busy.agent_command(cmd) == "grep -E 'a|b' log"


def _judge(monkeypatch, panes: str, returncode: int = 0, ps: str = PS):
    rows = [{"id": "s1", "name": "lexi", "status": "running", "activity_state": "idle"}]
    monkeypatch.setattr(lane_busy.urllib.request, "urlopen", lambda *a, **k: _Json(rows))

    def run(cmd, **kwargs):
        if cmd[0] == "ps":
            return _Done(ps)
        return _Done(panes, returncode)

    monkeypatch.setattr(lane_busy.subprocess, "run", run)
    return lane_busy.judge("http://forge/", "tmux", ["s1"], socket_dir="/sock")


def test_a_lane_whose_panes_cannot_be_listed_is_busy(monkeypatch):
    """LEX-1957 review: a wrong uid or socket dir must not read as 'nothing running'."""
    verdict = _judge(monkeypatch, "", returncode=1)
    assert verdict["judged"] == ["s1"]
    assert verdict["busy"][0]["reasons"] == [
        "its panes cannot be listed (/sock/skuld-s1.sock): nothing in them can be seen"
    ]


def test_a_second_pane_is_inspected_too(monkeypatch):
    """lexi's pane 0.1 ran a teammate agent the first-pane check never saw."""
    verdict = _judge(monkeypatch, "200\n300\n")
    reasons = verdict["busy"][0]["reasons"]
    assert any("train: pid 303" in r for r in reasons)


def test_a_vanished_pane_process_is_busy(monkeypatch):
    verdict = _judge(monkeypatch, "999\n")
    assert "pane pid 999 is not running" in verdict["busy"][0]["reasons"]


def test_etime_seconds():
    assert lane_busy.etime_seconds("05") == 5
    assert lane_busy.etime_seconds("02:05") == 125
    assert lane_busy.etime_seconds("1-01:00:01") == 86400 + 3601


def _line(record: dict) -> str:
    return json.dumps(record) + "\n"


def _use(at: str, tool_id: str, name: str, **inputs) -> str:
    block = {"type": "tool_use", "id": tool_id, "name": name, "input": inputs}
    return _line({"type": "assistant", "timestamp": at, "message": {"content": [block]}})


def _result(at: str, tool_id: str, text: str) -> str:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": text}
    return _line({"type": "user", "timestamp": at, "message": {"content": [block]}})


def _epoch(stamp: str) -> float:
    return lane_busy.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def test_a_pending_wakeup_and_an_unfired_cron_keep_the_lane_busy(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        _use("2026-11-06T20:00:00Z", "w1", "ScheduleWakeup", delaySeconds=900)
        + _result("2026-11-06T20:00:01Z", "w1", "Next wakeup scheduled for 20:15:01 (in 900s).")
        + _use("2026-11-06T20:01:00Z", "c1", "CronCreate", cron="0 21 6 11 *")
        + _result(
            "2026-11-06T20:01:01Z",
            "c1",
            "Scheduled one-shot task ab12cd34 (0 21 6 11 *). Next run: Nov 6 9:00pm.",
        )
    )
    now = _epoch("2026-11-06T20:10:00Z")
    reasons = lane_busy.pending_schedules(transcript, _epoch("2026-11-06T19:00:00Z"), now)
    assert reasons[0] == "ScheduleWakeup pending, due in 301s"
    assert reasons[1].startswith("session cron ab12cd34 (0 21 6 11 *) due in ")


def test_stopped_fired_deleted_and_older_schedules_do_not(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        # armed by an earlier Claude process: died with it
        _use("2026-11-06T10:00:00Z", "c0", "CronCreate")
        + _result("2026-11-06T10:00:01Z", "c0", "Scheduled recurring task old00000 (*/5 * * * *).")
        + _use("2026-11-06T20:00:00Z", "w1", "ScheduleWakeup", delaySeconds=900)
        + _result("2026-11-06T20:00:01Z", "w1", "Next wakeup scheduled for 20:15:01 (in 900s).")
        + _use("2026-11-06T20:02:00Z", "w2", "ScheduleWakeup", stop=True)
        + _result("2026-11-06T20:02:01Z", "w2", "Loop stopped - cancelled 1 pending wakeup(s).")
        + _use("2026-11-06T20:03:00Z", "c1", "CronCreate")
        + _result("2026-11-06T20:03:01Z", "c1", "Scheduled recurring task rec11111 (*/5 * * * *).")
        + _use("2026-11-06T20:04:00Z", "d1", "CronDelete", id="rec11111")
        + _use("2026-11-06T20:05:00Z", "c2", "CronCreate")
        + _result("2026-11-06T20:05:01Z", "c2", "Scheduled one-shot task one22222 (6 20 6 11 *).")
    )
    now = _epoch("2026-11-06T20:10:00Z")
    assert lane_busy.pending_schedules(transcript, _epoch("2026-11-06T19:00:00Z"), now) == []


def test_a_recurring_cron_stays_busy_until_deleted(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        _use("2026-11-06T20:03:00Z", "c1", "CronCreate")
        + _result("2026-11-06T20:03:01Z", "c1", "Scheduled recurring task rec11111 (*/5 * * * *).")
    )
    now = _epoch("2026-11-06T22:00:00Z")
    assert lane_busy.pending_schedules(transcript, _epoch("2026-11-06T19:00:00Z"), now) == [
        "session cron rec11111 (*/5 * * * *) recurring"
    ]


def test_a_background_agent_is_busy_until_its_subagent_stop(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    running, done = tmp_path / "agent-a.jsonl", tmp_path / "agent-b.jsonl"
    running.write_text(_line({"type": "assistant", "message": {"content": []}}))
    done.write_text(_line({"type": "attachment", "attachment": {"hookEvent": "SubagentStop"}}))
    (tasks / "a1.output").symlink_to(running)
    (tasks / "b2.output").symlink_to(done)
    (tasks / "shell.output").write_text("a background shell's log")
    reasons = lane_busy.running_agents(tasks, started=0)
    assert len(reasons) == 1 and reasons[0].startswith("background agent a1 running")
    # an agent last written before this Claude process started cannot be running in it
    assert lane_busy.running_agents(tasks, started=lane_busy.time.time() + 60) == []


def test_background_reasons_without_a_pane_or_a_claude_pane_are_busy():
    assert lane_busy.background_reasons([], {}, "conv") == [
        "no tmux pane: its background work cannot be seen"
    ]
    procs = lane_busy.parse_ps("  700     1 01:00 codex --yolo\n")
    assert lane_busy.background_reasons(["700"], procs, "conv") == [
        "pane runs codex --yolo, not Claude: its background work cannot be seen"
    ]


def test_a_worker_exec_ed_after_claude_started_is_busy():
    procs = lane_busy.parse_ps(
        "  800     1 01:00:00 claude --resume c\n"
        "  801   800 59:58 node /x/mcp-server\n"  # started with Claude: a helper
        "  802   800 05:00 python worker.py\n"  # a background shell exec'ed into it
    )
    assert lane_busy.late_children("800", procs) == [
        "pid 802 started 3300s after Claude: python worker.py"
    ]


def test_a_detached_process_carrying_the_session_is_busy(tmp_path):
    def proc(pid, cmd, sid="s1"):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "environ").write_bytes(f"HOME=/h\0SKULD__SESSION__ID={sid}\0".encode())
        (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")

    proc(10, "/rel/.venv/bin/python -m skuld")
    proc(11, "tmux -S /tmp/skuld-tmux-1000/skuld-s1.sock new-session")
    proc(12, "sh -c cat >> /w/.skuld/tmux-interactive/skuld-s1/panes/0.log")
    proc(13, "cat")
    proc(14, "claude --resume c")  # in the panes
    proc(15, "Xorg :7 -seat seat-trickle")
    proc(16, "vite preview", sid="other")
    reasons = lane_busy.stray_session_processes("s1", {"14"}, proc=tmp_path)
    assert reasons == ["pid 15 outside the panes carries the session: Xorg :7 -seat seat-trickle "]


# -- Codex lanes (LEX-1967) ---------------------------------------------------------------

SID = "03f94fdb-468d-424d-9c28-127f443e7832"
THREAD = "01a16c47-e530-77d1-b461-aa851eb59245"
SUB = "01a1ba7d-461d-78b2-bbf7-45fc93bbe5cd"
CODEX_PS = """\
 2925740  2625 8-07:03:49 /rel/.venv/bin/python -m skuld
 2926124 2925740 8-07:03:44 /x/bin/codex app-server -c mcp_servers.forge.command="/rel/py"
 2926460 2926124 8-07:03:40 /rel/.venv/bin/python -P -m skuld.forge_mcp --broker-url http://127.0.0.1:9100
 2926457 2926124 8-07:03:40 node /home/x/.local/bin/lexi-google
  326336 2926124 8-01:16:38 /home/x/.codex/packages/bin/codex-code-mode-host
"""


def _record(ordinal: int, kind: str | None = None, turn: str = "t1") -> str:
    payload = {"type": kind, "turn_id": turn} if kind else {"type": "item_completed"}
    return json.dumps(
        {
            "timestamp": "2026-11-08T13:22:36Z",
            "ordinal": ordinal,
            "type": "event_msg",
            "payload": payload,
        }
    )


def _codex_host(
    tmp_path, ps: str = CODEX_PS, *, main_last="task_complete", sub_last="task_complete"
):
    """A fake /proc and CODEX_HOME: the app-server holds its main and one sub-thread rollout."""
    home = tmp_path / "codex"
    sessions = home / "sessions/2026/11/08"
    sessions.mkdir(parents=True)
    (home / "config.toml").write_text(
        '[mcp_servers.google]\ncommand = "/home/x/.local/bin/lexi-google"\n'
        '[other]\ncommand = "/bin/no"\n'
    )
    rollouts = {}
    for thread, last in ((THREAD, main_last), (SUB, sub_last)):
        path = sessions / f"rollout-2026-11-08T09-28-54-{thread}.jsonl"
        lines = [_record(1, "task_started"), _record(2), _record(3, "task_complete")]
        if last == "task_started":
            lines += [_record(4, "task_started", "t2"), _record(5)]
        path.write_text("\n".join(lines) + "\n")
        rollouts[thread] = path
    proc = tmp_path / "proc"
    procs = lane_busy.parse_ps(ps)
    for pid, (_, _, command) in procs.items():
        (proc / pid / "fd").mkdir(parents=True)
        env = [b"HOME=/home/x"]
        if pid in ("2925740", "2926124", "2344517", "777"):
            env.append(f"SKULD__SESSION__ID={SID}".encode())
        if pid == "2926124":
            env.append(f"CODEX_HOME={home}".encode())
            for n, path in enumerate(rollouts.values()):
                (proc / pid / "fd" / str(30 + n)).symlink_to(path)
        (proc / pid / "environ").write_bytes(b"\0".join(env))
        (proc / pid / "cmdline").write_bytes(command.replace(" ", "\0").encode())
    return procs, proc


def test_an_idle_codex_lane_with_only_its_helpers_may_restart(tmp_path):
    procs, proc = _codex_host(tmp_path)
    reasons, pids = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == []
    assert {"2926124", "2926460", "2926457", "326336"} <= pids


def test_a_worker_the_codex_agent_detached_makes_it_busy(tmp_path):
    ps = CODEX_PS + " 2344517 2926124 5-15:12:00 python3 -u /tmp/lex1836-watch-proof.py\n"
    procs, proc = _codex_host(tmp_path, ps)
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == [
        "pid 2344517, 5-15:12:00, under the Codex app-server: "
        "python3 -u /tmp/lex1836-watch-proof.py"
    ]


def test_a_running_exec_tool_command_makes_it_busy_once(tmp_path):
    ps = (
        CODEX_PS
        + " 500 2926124 00:12 /bin/bash -lc pytest -q\n 501 500 00:11 python -m pytest -q\n"
    )
    procs, proc = _codex_host(tmp_path, ps)
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert len(reasons) == 1 and reasons[0].startswith("pid 500, 00:12, under the Codex app-server")


def test_a_thread_mid_turn_makes_it_busy_even_a_sub_thread(tmp_path):
    procs, proc = _codex_host(tmp_path, sub_last="task_started")
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == [f"thread {SUB} turn t2 running since 2026-11-08T13:22:36Z"]


def test_a_codex_lane_whose_main_thread_is_not_open_is_busy(tmp_path):
    procs, proc = _codex_host(tmp_path)
    reasons, _ = lane_busy.codex_reasons(SID, "another-thread", procs, proc)
    assert reasons == [
        "the app-server has no rollout open for thread another-thread: its turns cannot be seen"
    ]


def test_a_lane_without_an_app_server_is_not_judged_as_codex(tmp_path):
    procs, proc = _codex_host(tmp_path)
    assert lane_busy.codex_reasons("someone-else", THREAD, procs, proc) is None


def test_the_last_turn_marker_is_found_across_read_chunks(tmp_path):
    path = tmp_path / "rollout-x.jsonl"
    filler = [_record(n) for n in range(2, 400)]
    path.write_text("\n".join([_record(1, "task_started", "early"), *filler]) + "\n")
    assert lane_busy.last_turn_marker(path, chunk=97) == (
        "task_started",
        "early",
        "2026-11-08T13:22:36Z",
    )
    assert lane_busy.last_turn_marker(tmp_path / "rollout-x.jsonl", chunk=1 << 20)[1] == "early"


def test_mcp_commands_come_from_the_overrides_and_config_sections(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[mcp_servers.google]\ncommand = "/bin/google"\n'
        '[mcp_servers.hgx.oauth]\ncommand = "/bin/no"\n'
    )
    app = 'codex app-server -c mcp_servers.forge.command="/venv/python" -c x=1'
    assert sorted(lane_busy.mcp_commands(app, tmp_path)) == [["/bin/google"], ["/venv/python"]]


def test_a_rollout_mid_write_or_without_turns_is_busy(tmp_path):
    procs, proc = _codex_host(tmp_path)
    main = next((tmp_path / "codex/sessions/2026/11/08").glob(f"*{THREAD}.jsonl"))
    with open(main, "a") as stream:
        stream.write('{"ordinal": 9, "payload": {"type": "task_sta')
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == [f"thread {THREAD}'s rollout is mid-write: a record is being appended"]
    main.write_text(_record(1) + "\n")
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == [f"thread {THREAD} has no turn record: its state cannot be seen"]


def test_code_the_code_mode_host_runs_is_work(tmp_path):
    ps = CODEX_PS + " 900 326336 00:30 /usr/bin/node -e work()\n"
    procs, proc = _codex_host(tmp_path, ps)
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == ["pid 900, 00:30, under the Codex app-server: /usr/bin/node -e work()"]


def test_a_tool_run_of_an_mcp_servers_interpreter_is_work(tmp_path):
    ps = CODEX_PS + " 901 2926124 00:20 /rel/py worker.py\n"
    procs, proc = _codex_host(tmp_path, ps)
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == ["pid 901, 00:20, under the Codex app-server: /rel/py worker.py"]


def test_two_app_servers_for_one_session_are_busy(tmp_path):
    ps = CODEX_PS + " 2926999 2925740 00:05 /x/bin/codex app-server --listen unix:///tmp/t.sock\n"
    procs, proc = _codex_host(tmp_path, ps)
    (proc / "2926999" / "environ").write_bytes(f"SKULD__SESSION__ID={SID}".encode())
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == ["2 Codex app-servers carry the session: 2926124, 2926999"]


def test_mcp_servers_match_on_their_whole_argv(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[mcp_servers.google]\ncommand = "/bin/google"\nargs = [\n  "--port",\n  "1"\n]\n'
    )
    app = 'codex app-server -c mcp_servers.forge.command="/venv/python" -c x=1'
    mcp = lane_busy.mcp_commands(app, tmp_path)
    assert sorted(mcp) == [["/bin/google", "--port", "1"], ["/venv/python"]]
    assert lane_busy._is_helper("node /bin/google --port 1", mcp)
    assert not lane_busy._is_helper("/venv/python worker.py", mcp)


def test_mcp_config_is_read_as_toml(tmp_path):
    (tmp_path / "config.toml").write_text(
        "# servers\n[mcp_servers.google]\ncommand = '/bin/google'  # literal string\n"
        'args = [\n  "--label",\n  "two words",\n]\n'
    )
    assert lane_busy.mcp_commands("codex app-server", tmp_path) == [
        ["/bin/google", "--label", "two words"]
    ]


def test_an_mcp_server_with_a_spaced_argument_is_a_helper(tmp_path):
    (tmp_path / "codex").mkdir()
    ps = CODEX_PS.replace("node /home/x/.local/bin/lexi-google", "/bin/google --label two words")
    procs, proc = _codex_host(tmp_path, ps)
    (tmp_path / "codex/config.toml").write_text(
        '[mcp_servers.google]\ncommand = "/bin/google"\nargs = ["--label", "two words"]\n'
    )
    (proc / "2926457" / "cmdline").write_bytes(b"/bin/google\0--label\0two words\0")
    reasons, _ = lane_busy.codex_reasons(SID, THREAD, procs, proc)
    assert reasons == []
