"""Evidence checks and portable, content-addressed Forge replay bundles.

Provider prose is never evidence that a tool ran. Checks consume structured
frames and pair call/result IDs. Fixtures retain the existing SessionLogEntry[]
wire shape so iOS and browser clients can replay the same captured session.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from niuu.domain.transcript_reducer import is_read_path_excluded

SCHEMA_VERSION = 1
TOOL_FAMILIES = {
    "command": {"bash", "shell", "shell_command", "exec_command", "commandexecution"},
    "search": {"websearch", "web_search", "web.search", "web.run", "web_search_preview"},
    "agent": {"task", "agent", "spawn_agent", "collabagenttoolcall"},
    "plan": {"todowrite", "taskcreate", "taskupdate", "update_plan", "plan"},
}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def blocks(frame: dict) -> list[dict]:
    """Only protocol content blocks, never arbitrary JSON quoted in model prose."""
    content = frame.get("message", {}).get("content", frame.get("content", []))
    found = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    if frame.get("type") == "content_block_start":
        found.append(frame.get("content_block", {}))
    if frame.get("type") in {"tool_use", "tool_result"}:
        found.append(frame)
    return found


def evidence(frames: list[dict]) -> dict:
    calls: dict[str, dict] = {}
    results: dict[str, dict] = {}
    text_parts: list[str] = []
    agents: set[str] = set()
    for frame in frames:
        if frame.get("type") == "assistant":
            text_parts.extend(b.get("text", "") for b in blocks(frame) if b.get("type") == "text")
            if isinstance(frame.get("content"), str):
                text_parts.append(frame["content"])
        if frame.get("type") == "content_block_delta":
            delta = frame.get("delta", {})
            if delta.get("type") == "text_delta":
                text_parts.append(delta.get("text", ""))
        if frame.get("type") == "agent_update":
            agent = frame.get("agent", {})
            if frame.get("action") == "started" and agent.get("id"):
                agents.add(agent["id"])
        for block in blocks(frame):
            if block.get("type") == "tool_use" and block.get("id"):
                previous = calls.get(block["id"], {})
                calls[block["id"]] = {**previous, **block}
            if block.get("type") == "tool_result" and block.get("tool_use_id"):
                results[block["tool_use_id"]] = block
    families = Counter()
    if any(f.get("type") == "plan" and f.get("tasks") for f in frames):
        families["plan"] += 1
    for call in calls.values():
        name = call.get("name", "").lower().removeprefix("functions.")
        for family, names in TOOL_FAMILIES.items():
            if name in names:
                families[family] += 1
    return {
        "calls": calls,
        "results": results,
        "tool_families": dict(families),
        "agent_ids": sorted(agents),
        "assistant_text": "".join(text_parts),
        "unpaired_calls": sorted(calls.keys() - results.keys()),
        "orphan_results": sorted(results.keys() - calls.keys()),
        "event_counts": dict(Counter(f.get("type", "unknown") for f in frames)),
    }


def _has_search_arguments(value: Any) -> bool:
    """A tool name alone cannot prove that its actual query/open target survived."""
    if not isinstance(value, dict):
        return False
    if any(
        isinstance(value.get(key), str) and value[key].strip()
        for key in ("query", "q", "url", "ref_id", "pattern")
    ):
        return True
    if isinstance(value.get("queries"), list) and any(
        isinstance(query, str) and query.strip() for query in value["queries"]
    ):
        return True
    if _has_search_arguments(value.get("action")):
        return True
    return any(
        isinstance(value.get(key), list)
        and any(_has_search_arguments(entry) for entry in value[key])
        for key in ("search_query", "open", "find")
    )


def _has_search_results(value: Any) -> bool:
    if isinstance(value, str):
        value = value.strip()
        try:
            value = json.loads(value)
        except ValueError:
            return bool(value)
    if isinstance(value, dict) and "results" in value:
        return bool(value["results"])
    return bool(value)


def check_scenario(scenario: dict, frames: list[dict], workspace: Path) -> dict:
    observed = evidence(frames)
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    terminal = [f for f in frames if f.get("type") == "result"]
    marker = scenario.get("completion_marker")
    check("terminal-result", bool(terminal) if marker else len(terminal) == 1, len(terminal))
    if marker:
        completed = completion_results(frames, marker)
        check("correlated-completion", len(completed) == 1, len(completed))
    check(
        "successful-result",
        bool(terminal)
        and not terminal[-1].get("is_error")
        and terminal[-1].get("subtype", "success") in {"success", "completed"},
    )
    check(
        "tool-pairs",
        not observed["unpaired_calls"] and not observed["orphan_results"],
        {k: observed[k] for k in ("unpaired_calls", "orphan_results")},
    )
    for family in scenario.get("required_tools", []):
        count = observed["tool_families"].get(family, 0)
        check(f"tool:{family}", count > 0, count)
    if "search" in scenario.get("required_tools", []):
        search_calls = [
            call
            for call in observed["calls"].values()
            if call.get("name", "").lower().removeprefix("functions.") in TOOL_FAMILIES["search"]
        ]
        check(
            "search-inputs-captured",
            bool(search_calls)
            and all(_has_search_arguments(call.get("input")) for call in search_calls),
        )
        check(
            "search-results-captured",
            bool(search_calls)
            and all(
                _has_search_results(observed["results"].get(call["id"], {}).get("content"))
                for call in search_calls
            ),
        )
    outputs = canonical(list(observed["results"].values()))
    for marker in scenario.get("tool_output_contains", []):
        check(f"tool-output:{marker}", marker in outputs)
    for marker in scenario.get("assistant_contains", []):
        check(f"assistant:{marker}", marker in observed["assistant_text"])
    for kind in scenario.get("required_events", []):
        check(f"event:{kind}", observed["event_counts"].get(kind, 0) > 0)
    if scenario.get("require_freeform_question"):
        questions = [
            question
            for frame in frames
            if frame.get("type") == "ask_user_question"
            for question in frame.get("questions", [])
        ]
        check(
            "freeform-question",
            bool(questions) and all(not question.get("options") for question in questions),
        )
    if scenario.get("require_tool_error"):
        check(
            "intentional-tool-failure", any(r.get("is_error") for r in observed["results"].values())
        )
    if scenario.get("min_agents"):
        # Native spawn invocations also count when a transport lacks the UI surface.
        count = max(len(observed["agent_ids"]), observed["tool_families"].get("agent", 0))
        check("agent-count", count >= scenario["min_agents"], count)
    for relative, expected in scenario.get("files", {}).items():
        path = workspace / relative
        try:
            actual = json.loads(path.read_text())
            check(f"file:{relative}", actual == expected, actual)
        except (OSError, ValueError) as exc:
            check(f"file:{relative}", False, type(exc).__name__)
    return {
        "scenario": scenario["id"],
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "observed": observed,
    }


def native_thread_continuity(frames: list[dict]) -> dict:
    """A reconnect must preserve the native conversation behind one Forge session."""
    identities = list(
        dict.fromkeys(
            frame["session_id"]
            for frame in frames
            if frame.get("type") == "system"
            and frame.get("subtype") == "init"
            and isinstance(frame.get("session_id"), str)
            and frame["session_id"]
        )
    )
    return {"passed": len(identities) == 1, "native_session_ids": identities}


def completion_results(frames: list[dict], marker: str) -> list[dict]:
    """Codex ends with an empty result; its final text arrives before that frame."""
    segment: list[dict] = []
    complete = []
    for frame in frames:
        segment.append(frame)
        if frame.get("type") != "result":
            continue
        if marker in str(frame.get("result", "")) or marker in evidence(segment)["assistant_text"]:
            complete.append(frame)
        segment = []
    return complete


def stream_frames(rows: list[dict]) -> list[dict]:
    return [r["payload"] for r in rows if not is_read_path_excluded(r["kind"], r["payload"])]


def compare_streams(expected: list[dict], actual: list[dict]) -> dict:
    """Ordered equality, including multiplicity; a Counter alone hides reorder bugs."""
    for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
        if left != right:
            return {
                "passed": False,
                "index": index,
                "expected_type": left.get("type"),
                "actual_type": right.get("type"),
                "expected_hash": digest(left),
                "actual_hash": digest(right),
            }
    return {
        "passed": len(expected) == len(actual),
        "expected_count": len(expected),
        "actual_count": len(actual),
    }


def validate_rows(rows: list[dict], session_id: str) -> list[str]:
    errors = []
    previous = 0
    for row in rows:
        seq = row.get("seq")
        if type(seq) is not int or seq <= previous:
            errors.append(f"non-increasing sequence at {seq}")
        if type(seq) is int:
            previous = seq
        if row.get("session_id") != session_id:
            errors.append(f"foreign session at {seq}")
        if row.get("kind") in {"log_gap", "log_conflict"}:
            errors.append(f"{row['kind']} at {seq}")
        if not isinstance(row.get("payload"), dict):
            errors.append(f"invalid payload at {seq}")
    return errors


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def scan_sensitive(value: Any) -> list[str]:
    """Promotion guard, not a claim of automatic anonymization."""
    raw = canonical(value)
    patterns = {
        "credential-like": (
            r"(?i)(?:Bearer\s+[A-Za-z0-9._-]{12,}|sk-[A-Za-z0-9_-]{16,}|"
            r"gh[pousr]_[A-Za-z0-9]{20,})"
        ),
        "private-key": r"-----BEGIN .*PRIVATE KEY-----",
        "secret-field": r'(?i)"(?:api_key|password|access_token|authorization)"\s*:\s*"[^"<>]+"',
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, raw)]
