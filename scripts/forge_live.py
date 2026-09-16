"""Run live Forge acceptance sessions: python -m scripts.forge_live --help.

This intentionally creates billable provider sessions. It only stops sessions
created by this invocation and always preserves their IDs and evidence on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect

from scripts.forge_trace import (
    SCHEMA_VERSION,
    check_scenario,
    compare_streams,
    completion_results,
    digest,
    native_thread_continuity,
    scan_sensitive,
    stream_frames,
    validate_rows,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "tests/fixtures/forge-live/scenarios.json"
API = "/api/v1/forge/sessions"
POLL_SECONDS = 0.5
HTTP_TIMEOUT = 30
QUIET_SECONDS = 2
REPLAY_TIMEOUT = 90
PAGE_SIZE = 500
MAX_WS_BYTES = 16 * 1024 * 1024


@dataclass
class Options:
    base_url: str
    output: Path
    workspace_root: Path
    timeout: float = 240
    startup_timeout: float = 120
    token_env: str = "FORGE_LIVE_TOKEN"


class Platform:
    def __init__(self, options: Options):
        self.options = options
        token = os.environ.get(options.token_env, "")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.AsyncClient(
            base_url=options.base_url, headers=self.headers, timeout=HTTP_TIMEOUT
        )

    async def request(self, method: str, path: str, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def socket_url(self, path: str) -> str:
        parsed = urlsplit(self.options.base_url)
        return urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                parsed.path.rstrip("/") + path,
                "",
                "",
            )
        )

    async def head(self, sid: str) -> int:
        data = await self.request("GET", f"{API}/{sid}/log/head")
        value = data["latest_seq"]
        if type(value) is not int or value < 0:
            raise ValueError("invalid durable log head")
        return value

    async def rows(self, sid: str, *, head: int, after: int = 0) -> list[dict]:
        """Drain a frozen seq horizon, including completely filtered pages.

        REST filters AFTER paging raw rows and exposes no raw next cursor. On an
        empty page, advancing by the raw page size is safe: every row in that
        page was excluded, and positive strictly increasing seqs bound its end.
        Never treat an empty visible page or a visible short page as EOF.
        """
        rows: list[dict] = []
        cursor = after
        while cursor < head:
            page = await self.request(
                "GET",
                f"{API}/{sid}/log",
                params={"after": cursor, "limit": PAGE_SIZE, "show_internal": "true"},
            )
            if not isinstance(page, list):
                raise ValueError("log page must be an array")
            if not page:
                cursor += PAGE_SIZE
                continue
            if page[0]["seq"] <= cursor or any(
                a["seq"] >= b["seq"] for a, b in zip(page, page[1:], strict=False)
            ):
                raise ValueError("log cursor failed to advance monotonically")
            rows.extend(r for r in page if r["seq"] <= head)
            cursor = page[-1]["seq"]
        return rows

    async def replay(self, sid: str, *, after: int = 0) -> list[dict]:
        query = urlencode(
            {
                "speed": 1000,
                "max_gap": 0,
                "show_internal": "true",
                "preamble": "false",
                "after": after,
            }
        )
        frames = []
        async with asyncio.timeout(REPLAY_TIMEOUT):
            async with connect(
                self.socket_url(f"{API}/{sid}/replay") + "?" + query,
                additional_headers=self.headers,
                max_size=MAX_WS_BYTES,
            ) as ws:
                async for raw in ws:
                    frames.append(json.loads(raw))
        return frames


def question_autoanswer_block_reason(frame: dict, answer: str) -> str | None:
    """A question fixture never authorizes permission choices or unrelated options."""
    metadata = frame.get("metadata") or {}
    if metadata.get("control_kind") == "permission":
        return "permission_card"
    questions = frame.get("questions")
    if not isinstance(questions, list) or not questions:
        return "invalid_question"
    for question in questions:
        if not isinstance(question, dict):
            return "invalid_question"
        options = question.get("options") or []
        if not isinstance(options, list):
            return "invalid_options"
        labels = {
            option["label"].strip().casefold()
            for option in options
            if isinstance(option, dict) and isinstance(option.get("label"), str)
        }
        # Older TTY bridges carry permission gates in ask_user_question frames
        # without an explicit kind. Their stable Allow/Deny shape is sufficient
        # to withhold automation, even when the fixed fixture answer is Allow.
        if "allow" in labels and "deny" in labels:
            return "permission_card"
        if options and answer.strip().casefold() not in labels:
            return "answer_not_declared_option"
    return None


class Observer:
    def __init__(self, platform: Platform, sid: str):
        self.platform = platform
        self.sid = sid
        self.frames: list[dict] = []
        self.records: list[dict] = []
        self.task: asyncio.Task | None = None
        self.ws = None
        self.answer: str | None = None
        self.answers: set[str] = set()
        self.blocked_control_answers: dict[str, dict] = {}

    async def start(self):
        self.ws = await connect(
            self.platform.socket_url(f"/s/{self.sid}/session"),
            additional_headers=self.platform.headers,
            max_size=MAX_WS_BYTES,
        )
        await self.ws.send(json.dumps({"type": "set_internal_visibility", "visible": True}))
        self.task = asyncio.create_task(self._receive())
        await asyncio.sleep(QUIET_SECONDS)

    async def _receive(self):
        async for raw in self.ws:
            frame = json.loads(raw)
            if not isinstance(frame, dict):
                raise ValueError("live WebSocket frame is not an object")
            self.frames.append(frame)
            self.records.append({"received_at": time.time(), "payload": frame})
            if frame.get("type") == "ask_user_question" and self.answer is not None:
                rid = frame.get("request_id")
                if rid and rid not in self.answers:
                    reason = question_autoanswer_block_reason(frame, self.answer)
                    if reason is not None:
                        self.blocked_control_answers.setdefault(
                            rid,
                            {
                                "request_id": rid,
                                "reason": reason,
                                "automatically_answered": False,
                            },
                        )
                        continue
                    self.answers.add(rid)
                    await self.ws.send(
                        json.dumps(
                            {
                                "type": "ask_user_answer",
                                "request_id": rid,
                                "answers": [
                                    {
                                        "question_id": q.get("id"),
                                        "question": q.get("question", ""),
                                        "answer": self.answer,
                                    }
                                    for q in frame.get("questions", [])
                                ],
                            }
                        )
                    )
            # Permission requests deliberately remain visible. The runner never
            # approves arbitrary operations or changes a session's permission policy.

    async def stop(self):
        if self.ws is not None:
            await self.ws.close()
        if self.task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.ws = None
        self.task = None

    async def prepare_workspace(self, workspace: Path, *, timeout: float):
        """Acknowledge only the trust screen for this invocation's synthetic folder."""
        answered = False
        async with asyncio.timeout(timeout):
            while True:
                panes = [
                    f
                    for f in self.frames
                    if f.get("type") in {"terminal_frame", "terminal_snapshot"}
                ]
                text = panes[-1].get("text", "") if panes else ""
                if "Yes, I trust this folder" in text and str(workspace) in text:
                    if not answered:
                        selected = next(
                            (
                                line.strip()
                                for line in text.splitlines()
                                if line.strip().startswith("❯")
                            ),
                            "",
                        )
                        keys = ["Enter"] if "Yes, I trust" in selected else ["Down", "Enter"]
                        await self.ws.send(json.dumps({"type": "terminal_key", "keys": keys}))
                        answered = True
                elif "for shortcuts" in text or "bypass permissions on" in text:
                    return
                if self.task.done():
                    self.task.result()
                    raise RuntimeError("CLI disconnected during workspace setup")
                await asyncio.sleep(POLL_SECONDS)

    async def turn(self, prompt: str, *, timeout: float, marker: str, disconnect: bool = False):
        offset = len(self.frames)
        await self.ws.send(json.dumps({"type": "user", "content": prompt}))
        if disconnect:
            # The caller observes completion through the durable log in this case.
            await self.stop()
            return
        async with asyncio.timeout(timeout):
            while not completion_results(self.frames[offset:], marker):
                if any(f.get("type") == "terminal_pane_closed" for f in self.frames[offset:]):
                    raise RuntimeError("CLI pane closed before terminal result")
                if self.task.done():
                    self.task.result()
                    raise RuntimeError("observer disconnected before terminal result")
                await asyncio.sleep(POLL_SECONDS)
        await asyncio.sleep(QUIET_SECONDS)


async def wait_ready(platform: Platform, sid: str) -> dict:
    async with asyncio.timeout(platform.options.startup_timeout):
        while True:
            session = await platform.request("GET", f"{API}/{sid}")
            if session["status"] in {"failed", "stopped", "archived"}:
                raise RuntimeError(f"session launch reached {session['status']}")
            if session["status"] == "running" and session.get("chat_endpoint"):
                response = await platform.http.get(f"/s/{sid}/api/capabilities")
                if response.status_code == 200 and response.json().get("send_message"):
                    return response.json()
            await asyncio.sleep(POLL_SECONDS)


def make_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)
    (path / "numbers.json").write_text("[8, 13, 21]\n")
    (path / "TASK.md").write_text(
        "# Forge acceptance workspace\n\nOnly synthetic data lives here. "
        "Use this directory for all task files. Do not inspect parent directories, "
        "credentials, environment variables, or unrelated repositories. "
        "Do not publish, commit, push, or contact people. Native worker agents "
        "may read these task files. Public documentation search is allowed.\n"
    )
    # Both CLIs discover a local task instruction file, independent of whether
    # their transport honors the appended system prompt.
    for filename in ("CLAUDE.md", "AGENTS.md"):
        (path / filename).write_text((path / "TASK.md").read_text())
    subprocess.run(["git", "init", "-q", str(path)], check=True)


async def run_provider(
    options: Options, provider: str, config: dict, scenarios: list[dict]
) -> dict:
    run_id = f"{provider}-{uuid4().hex[:10]}"
    output = options.output / run_id
    output.mkdir(parents=True, exist_ok=False)
    workspace = options.workspace_root / run_id
    make_workspace(workspace)
    platform = Platform(options)
    sid = None
    observer = None
    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "provider": provider,
        "definition": config["definition"],
        "model": config["model"],
        "source": "live-provider",
        "workspace_dir": str(workspace),
        "controller_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip(),
        "source_hashes": {
            str(path.relative_to(ROOT)): digest(path.read_text())
            for path in (
                ROOT / "src/skuld/broker.py",
                ROOT / "src/skuld/transports/tmux_interactive.py",
                ROOT / "src/skuld/transports/codex_ws.py",
                Path(__file__),
                CATALOG,
            )
        },
        "started_at": time.time(),
        "scenarios": [],
        "errors": [],
        "review": {"status": "pending"},
        "control_answer_policy": {"question_fixtures_only": True, "permission_autoanswer": False},
    }
    write_json(output / "manifest.json", report)
    try:
        session = await platform.request(
            "POST",
            API,
            json={
                "name": f"forge-test-{run_id}",
                "definition": config["definition"],
                "model": config["model"],
                "source": {"type": "local_mount", "local_path": str(workspace)},
                "system_prompt": (workspace / "TASK.md").read_text(),
            },
        )
        sid = session["id"]
        report["session_id"] = sid
        write_json(output / "manifest.json", report)
        print(f"{provider}: created {sid}", flush=True)
        report["capabilities"] = await wait_ready(platform, sid)
        observer = Observer(platform, sid)
        await observer.start()
        if provider == "claude-tmux":
            await observer.prepare_workspace(workspace, timeout=options.startup_timeout)
        for template in scenarios:
            marker = f"[FORGE_DONE:{uuid4().hex}:{template['id']}]"
            scenario = {**template, "completion_marker": marker}
            if scenario.get("reconnect_before"):
                await observer.stop()
                await observer.start()
            observer.answer = scenario.get("answer")
            before = await platform.head(sid)
            prompt = (
                scenario["prompt"] + "\n\nAcceptance controller: after all requested work "
                "and all workers have finished (or you have explained an unavailable "
                "capability), append this exact completion marker to your final response: "
                + marker
                + ". Never put this marker in worker instructions or interim "
                "updates. It identifies completion of this request only."
            )
            start = time.monotonic()
            scenario_error = None
            try:
                await observer.turn(
                    prompt,
                    timeout=options.timeout,
                    marker=marker,
                    disconnect=scenario.get("disconnect_during", False),
                )
                if scenario.get("disconnect_during"):
                    async with asyncio.timeout(options.timeout):
                        while True:
                            tail = await platform.rows(
                                sid, head=await platform.head(sid), after=before
                            )
                            if completion_results(stream_frames(tail), marker):
                                break
                            await asyncio.sleep(POLL_SECONDS)
                    await observer.start()
            except (TimeoutError, RuntimeError) as exc:
                scenario_error = f"{type(exc).__name__}: {exc}"
            after = await platform.head(sid)
            rows = await platform.rows(sid, head=after, after=before)
            result = check_scenario(scenario, stream_frames(rows), workspace)
            result.update(
                {
                    "prompt": prompt,
                    "after_seq": before,
                    "through_seq": after,
                    "duration_seconds": round(time.monotonic() - start, 3),
                }
            )
            if scenario_error:
                result["error"] = scenario_error
                result["passed"] = False
            report["scenarios"].append(result)
            report["blocked_control_answers"] = list(observer.blocked_control_answers.values())
            write_json(output / "manifest.json", report)
            write_json(output / "live.json", observer.records)
            failed = [c["name"] for c in result["checks"] if not c["passed"]]
            print(
                f"{provider}/{scenario['id']}: {'PASS' if result['passed'] else 'FAIL'} {failed}",
                flush=True,
            )
            if scenario_error:
                # Do not queue later scenarios into a still-busy provider.
                break
        report["live_conversation"] = await platform.request("GET", f"{API}/{sid}/conversation")
        for surface in ("plan", "agents"):
            response = await platform.http.get(f"/s/{sid}/api/{surface}")
            report[surface] = (
                response.json() if response.is_success else {"http_status": response.status_code}
            )
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        print(f"{provider}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        if observer:
            try:
                await observer.stop()
            except Exception as exc:
                report["errors"].append(f"observer cleanup: {type(exc).__name__}")
            write_json(output / "live.json", observer.records)
            report["blocked_control_answers"] = list(observer.blocked_control_answers.values())
        if sid:
            try:
                await platform.request("POST", f"{API}/{sid}/stop")
                async with asyncio.timeout(options.startup_timeout):
                    while True:
                        final = await platform.request("GET", f"{API}/{sid}")
                        if final["status"] in {"stopped", "failed"}:
                            break
                        await asyncio.sleep(POLL_SECONDS)
                report["final_status"] = final["status"]
                head = await platform.head(sid)
                rows = await platform.rows(sid, head=head)
                write_json(output / "session.frames.json", rows)
                report["durable_head"] = head
                report["frame_count"] = len(rows)
                report["frames_sha256"] = digest(rows)
                report["row_errors"] = validate_rows(rows, sid)
                if provider == "codex":
                    report["native_thread_continuity"] = native_thread_continuity(
                        stream_frames(rows)
                    )
                report["stopped_conversation"] = await platform.request(
                    "GET", f"{API}/{sid}/conversation"
                )
                write_json(output / "conversation.json", report["stopped_conversation"])
                report["sensitive_scan"] = scan_sensitive(rows)
                replay = await platform.replay(sid)
                write_json(output / "replay.json", replay)
                report["replay_parity"] = compare_streams(stream_frames(rows), replay)
                cursor = rows[len(rows) // 2]["seq"] if rows else 0
                tail = await platform.replay(sid, after=cursor)
                report["cursor_replay_parity"] = compare_streams(
                    stream_frames([r for r in rows if r["seq"] > cursor]), tail
                )
            except Exception as exc:
                report["errors"].append(f"harvest: {type(exc).__name__}: {exc}")
        await platform.http.aclose()
        report["finished_at"] = time.time()
        report["passed"] = (
            not report["errors"]
            and not report.get("row_errors")
            and len(report["scenarios"]) == len(scenarios)
            and all(s["passed"] for s in report["scenarios"])
            and report.get("replay_parity", {}).get("passed", False)
            and report.get("cursor_replay_parity", {}).get("passed", False)
            and (
                provider != "codex"
                or report.get("native_thread_continuity", {}).get("passed", False)
            )
        )
        write_json(output / "manifest.json", report)
        print(f"{provider}: evidence {output} passed={report['passed']}", flush=True)
    return report


async def main_async(args) -> int:
    catalog = json.loads(CATALOG.read_text())
    providers = args.providers or list(catalog["providers"])
    scenarios = [
        s
        for s in catalog["scenarios"]
        if (s["id"] in args.scenarios if args.scenarios else s["tier"] == "core" or args.extended)
    ]
    if not scenarios:
        raise ValueError("no scenarios selected")
    unknown = set(args.scenarios or []) - {s["id"] for s in scenarios}
    if unknown:
        raise ValueError(f"unknown scenarios: {sorted(unknown)}")
    options = Options(
        args.base_url.rstrip("/"),
        args.output.resolve(),
        args.workspace_root.resolve(),
        args.timeout,
        args.startup_timeout,
        args.token_env,
    )
    options.output.mkdir(parents=True, exist_ok=True)
    options.workspace_root.mkdir(parents=True, exist_ok=True)
    reports = []
    for _ in range(args.repeat):
        for provider in providers:
            config = dict(catalog["providers"][provider])
            override = args.claude_model if provider == "claude-tmux" else args.codex_model
            if override:
                config["model"] = override
            reports.append(await run_provider(options, provider, config, scenarios))
    write_json(
        options.output / "summary.json",
        {
            "schema_version": SCHEMA_VERSION,
            "passed": all(r["passed"] for r in reports),
            "runs": [
                {k: r.get(k) for k in ("run_id", "provider", "session_id", "passed", "errors")}
                for r in reports
            ],
        },
    )
    return 0 if all(r["passed"] for r in reports) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--output", type=Path, default=Path(".forge-results/live"))
    parser.add_argument("--workspace-root", type=Path, default=Path("/tmp/forge-live-workspaces"))
    parser.add_argument("--providers", nargs="+", choices=("claude-tmux", "codex"))
    parser.add_argument("--scenarios", nargs="+")
    parser.add_argument("--extended", action="store_true")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--token-env", default="FORGE_LIVE_TOKEN")
    parser.add_argument("--claude-model")
    parser.add_argument("--codex-model")
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout <= 0 or args.startup_timeout <= 0:
        parser.error("repeat and timeouts must be positive")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
