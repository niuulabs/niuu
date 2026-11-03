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
    def __init__(self, stdout):
        self.stdout = stdout


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
