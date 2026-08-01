"""build_tool verify+repair loop (P2): verify on handback, repair, never
install a hard-failed tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ravn.adapters.tools.build_tool as build_tool_mod
from ravn.adapters.tools.build_tool import DEFAULT_MAX_REPAIR_ATTEMPTS, BuildTool
from ravn.ports.tool_build_backend import (
    ToolBuildInputRequiredError,
    ToolBuildPendingError,
    ToolBuildResult,
)
from ravn.valkyrie_evolution.tool_verification import VerificationResult

_ECHO_TOOL = "def run(payload):\n    return {'echo': payload}\n"
_ECHO_TEST = (
    "import _verify_tool\n\n"
    "def test_echo():\n"
    "    assert _verify_tool.run({'a': 1}) == {'echo': {'a': 1}}\n"
)


def _manifest(name: str = "echo_tool") -> dict[str, Any]:
    return {
        "name": name,
        "description": "echo the payload",
        "input_schema": {"type": "object"},
        "required_permission": "tool:run",
    }


def _tool(
    tmp_path: Path,
    *,
    build_backend: Any | None = None,
    **kwargs: Any,
) -> tuple[BuildTool, list]:
    registered: list = []

    def register(tool: Any, replace: bool = False) -> None:
        registered.append((tool, replace))

    tool = BuildTool(
        tools_dir=tmp_path / "tools",
        artifacts_dir=tmp_path / "arts",
        register_tool=register,
        autonomy_mode="autonomous",
        build_backend=build_backend,
        **kwargs,
    )
    return tool, registered


def _persisted_provenance(tmp_path: Path) -> dict[str, Any]:
    arts = list((tmp_path / "arts").glob("*.json"))
    assert arts, "artifact was not persisted"
    return dict(json.loads(arts[0].read_text())["provenance"])


def test_default_max_repair_attempts_is_three() -> None:
    assert DEFAULT_MAX_REPAIR_ATTEMPTS == 3


def test_manifest_schema_describes_declared_reach_as_an_array(tmp_path) -> None:
    tool, _ = _tool(tmp_path)
    reach_schema = tool.input_schema["properties"]["manifest"]["properties"]["declared_reach"]

    assert reach_schema["type"] == "array"


async def test_verify_passes_then_tool_installs_real_venv(tmp_path) -> None:
    """End-to-end: a trivial stdlib tool verifies in a real venv and installs."""
    tool, registered = _tool(tmp_path)
    result = await tool.execute(
        {
            "manifest": _manifest(),
            "tool_code": _ECHO_TOOL,
            "test_code": _ECHO_TEST,
            "canary_input": {"a": 1},
        }
    )
    assert result.is_error is False
    assert len(registered) == 1
    verification = _persisted_provenance(tmp_path)["verification"]
    assert verification["ok"] is True
    assert verification["attempts"] == 0


async def test_successful_install_records_resident_lifecycle_artifact(tmp_path) -> None:
    recorded = []

    async def record(artifact) -> None:
        recorded.append(artifact)

    tool, _ = _tool(tmp_path, installed_artifact_recorder=record)
    result = await tool.execute(
        {
            "manifest": _manifest("managed_echo"),
            "tool_code": _ECHO_TOOL,
            "test_code": "",
        }
    )

    assert not result.is_error
    assert [artifact.title for artifact in recorded] == ["managed_echo"]


async def test_verify_skipped_for_empty_test_code_still_installs(tmp_path) -> None:
    tool, registered = _tool(tmp_path)
    result = await tool.execute(
        {
            "manifest": _manifest(),
            "tool_code": _ECHO_TOOL,
            "test_code": "",
        }
    )
    assert result.is_error is False
    assert len(registered) == 1
    verification = _persisted_provenance(tmp_path)["verification"]
    assert verification["ok"] is True
    assert "structural validation only" in verification["logs"]


async def test_dependency_heal_appends_missing_module_then_installs(tmp_path, monkeypatch) -> None:
    """First verify fails on a missing module; deps-heal appends it and the
    re-verify passes, so the tool installs — no LLM, no backend."""
    calls: list[list[str]] = []

    def fake_verify(*, requirements: list[str], **_: Any) -> VerificationResult:
        calls.append(list(requirements))
        if "requests" in requirements:
            return VerificationResult(ok=True, logs="ok after heal")
        return VerificationResult(
            ok=False,
            logs="No module named 'requests'",
            missing_module="requests",
        )

    monkeypatch.setattr(build_tool_mod, "verify_learned_tool_in_ephemeral_venv", fake_verify)

    tool, registered = _tool(tmp_path)
    result = await tool.execute(
        {
            "manifest": _manifest(),
            "tool_code": "import requests\n\ndef run(payload):\n    return {}\n",
            "test_code": _ECHO_TEST,
        }
    )
    assert result.is_error is False
    assert len(registered) == 1
    # Verified twice: initial failure, then success after appending "requests".
    assert calls == [[], ["requests"]]
    prov = _persisted_provenance(tmp_path)
    assert prov["verification"]["ok"] is True
    assert prov["verification"]["attempts"] == 1


async def test_verify_fails_with_no_backend_returns_error_and_does_not_install(
    tmp_path, monkeypatch
) -> None:
    """An inline tool whose verification hard-fails (not a missing module) has
    no repair path: fail loudly, do not install."""

    def fake_verify(**_: Any) -> VerificationResult:
        return VerificationResult(ok=False, logs="AssertionError: boom", missing_module=None)

    monkeypatch.setattr(build_tool_mod, "verify_learned_tool_in_ephemeral_venv", fake_verify)

    tool, registered = _tool(tmp_path)
    result = await tool.execute(
        {
            "manifest": _manifest(),
            "tool_code": _ECHO_TOOL,
            "test_code": _ECHO_TEST,
        }
    )
    assert result.is_error is True
    assert "verification failed" in result.content
    assert "AssertionError: boom" in result.content
    assert registered == []
    # Verification outcome still recorded in provenance for the failed build.
    prov = _persisted_provenance(tmp_path)
    assert prov["verification"]["ok"] is False


async def test_backend_recommission_repairs_on_verify_failure_then_installs(
    tmp_path, monkeypatch
) -> None:
    """A commissioned build that fails verification is re-commissioned with the
    failing logs; the second build passes and installs."""
    builds: list[str] = []

    class _Backend:
        name = "fake"

        async def build(self, request: Any) -> ToolBuildResult:
            builds.append(request.signal_context)
            return ToolBuildResult(
                manifest=_manifest(),
                tool_code=_ECHO_TOOL,
                test_code=_ECHO_TEST,
                requirements=[],
                provenance={"builder": "fake"},
            )

    verify_calls = {"n": 0}

    def fake_verify(**_: Any) -> VerificationResult:
        verify_calls["n"] += 1
        if verify_calls["n"] == 1:
            return VerificationResult(ok=False, logs="ValueError: bad", missing_module=None)
        return VerificationResult(ok=True, logs="ok on retry")

    monkeypatch.setattr(build_tool_mod, "verify_learned_tool_in_ephemeral_venv", fake_verify)

    tool, registered = _tool(tmp_path, build_backend=_Backend())
    result = await tool.execute(
        {
            "manifest": _manifest(),
            "build_request": "build an echo tool",
        }
    )
    assert result.is_error is False
    assert len(registered) == 1
    # Two commissions: the initial build and one repair re-commission.
    assert len(builds) == 2
    # The repair commission carries the failing verification logs.
    assert "ValueError: bad" in builds[1]
    prov = _persisted_provenance(tmp_path)
    assert prov["verification"]["ok"] is True
    assert prov["verification"]["attempts"] == 1


async def test_verify_failure_exhausts_repair_budget_and_aborts(tmp_path, monkeypatch) -> None:
    """A backend that never fixes the tool is retried up to the bounded limit,
    then the build aborts without installing."""

    class _Backend:
        name = "fake"

        async def build(self, request: Any) -> ToolBuildResult:
            return ToolBuildResult(
                manifest=_manifest(),
                tool_code=_ECHO_TOOL,
                test_code=_ECHO_TEST,
                requirements=[],
            )

    def always_fail(**_: Any) -> VerificationResult:
        return VerificationResult(ok=False, logs="still broken", missing_module=None)

    monkeypatch.setattr(build_tool_mod, "verify_learned_tool_in_ephemeral_venv", always_fail)

    tool, registered = _tool(tmp_path, build_backend=_Backend(), max_repair_attempts=2)
    result = await tool.execute(
        {
            "manifest": _manifest(),
            "build_request": "build an echo tool",
        }
    )
    assert result.is_error is True
    assert "2 repair attempt" in result.content
    assert registered == []
    prov = _persisted_provenance(tmp_path)
    assert prov["verification"]["ok"] is False
    assert prov["verification"]["attempts"] == 2


async def test_commissioned_build_question_is_persisted_and_resumed(tmp_path) -> None:
    requests: list[Any] = []

    class _Backend:
        name = "a2a"

        async def build(self, request: Any) -> ToolBuildResult:
            requests.append(request)
            if not request.continuation:
                raise ToolBuildInputRequiredError(
                    task_id="task-1",
                    input_kind="question",
                    prompt="Which namespaces are in scope?",
                    continuation={
                        "task_id": "task-1",
                        "input_kind": "question",
                        "input_payload": {
                            "requestId": "help-1",
                            "question": "Which namespaces are in scope?",
                        },
                        "reply_metadata": {"requestId": "help-1"},
                        "exchanges": [],
                        "round": 1,
                    },
                )
            assert request.continuation["answer"] == "All namespaces, read-only."
            assert request.continuation["reply_metadata"] == {"requestId": "help-1"}
            return ToolBuildResult(
                manifest=_manifest(),
                tool_code=_ECHO_TOOL,
                test_code=_ECHO_TEST,
                requirements=[],
                provenance={"builder": "remote"},
            )

    tool, registered = _tool(tmp_path, build_backend=_Backend())
    first = await tool.execute(
        {
            "manifest": _manifest(),
            "build_request": "build an echo tool",
        }
    )

    assert first.is_error is False
    suspended = json.loads(first.content)
    assert suspended["status"] == "input_required"
    assert suspended["task_id"] == "task-1"
    assert suspended["question"] == "Which namespaces are in scope?"
    pending = list((tmp_path / "arts" / "pending-builds").glob("*.json"))
    assert len(pending) == 1
    assert registered == []

    resumed = await tool.execute(
        {
            "continuation_task_id": "task-1",
            "continuation_answer": "All namespaces, read-only.",
        }
    )

    assert resumed.is_error is False
    assert len(requests) == 2
    assert len(registered) == 1
    assert list((tmp_path / "arts" / "pending-builds").glob("*.json")) == []


async def test_commissioned_build_push_pending_resumes_without_answer(tmp_path) -> None:
    requests: list[Any] = []

    class _Backend:
        name = "a2a"

        async def build(self, request: Any) -> ToolBuildResult:
            requests.append(request)
            if not request.continuation:
                raise ToolBuildPendingError(
                    task_id="task-push-1",
                    push_registered=True,
                    continuation={
                        "task_id": "task-push-1",
                        "input_kind": "pending",
                        "push_registered": True,
                    },
                )
            assert request.continuation["input_kind"] == "pending"
            assert "answer" not in request.continuation
            return ToolBuildResult(
                manifest=_manifest(),
                tool_code=_ECHO_TOOL,
                test_code=_ECHO_TEST,
                requirements=[],
                provenance={"builder": "remote"},
            )

    tool, registered = _tool(tmp_path, build_backend=_Backend())
    first = await tool.execute({"manifest": _manifest(), "build_request": "build an echo tool"})

    assert first.is_error is False
    pending = json.loads(first.content)
    assert pending["status"] == "pending"
    assert pending["push_registered"] is True
    assert pending["resume_with"] == {"continuation_task_id": "task-push-1"}
    assert "Do not poll" in pending["next_step"]
    assert registered == []

    resumed = await tool.execute({"continuation_task_id": "task-push-1"})

    assert resumed.is_error is False
    assert len(requests) == 2
    assert len(registered) == 1
    assert list((tmp_path / "arts" / "pending-builds").glob("*.json")) == []


async def test_commissioned_build_rejects_scalar_declared_reach(tmp_path) -> None:
    class _Backend:
        name = "a2a"

        async def build(self, request: Any) -> ToolBuildResult:
            raise AssertionError("invalid manifest must not reach the backend")

    tool, registered = _tool(tmp_path, build_backend=_Backend())
    result = await tool.execute(
        {
            "manifest": {
                **_manifest(),
                "declared_reach": "Pure local computation",
            },
            "build_request": "build an echo tool",
        }
    )

    assert result.is_error is True
    assert "manifest.declared_reach must be an array" in result.content
    assert registered == []


async def test_commissioned_build_rejects_unknown_continuation_task(tmp_path) -> None:
    class _Backend:
        name = "a2a"

        async def build(self, request: Any) -> ToolBuildResult:
            raise AssertionError("backend must not be called without durable state")

    tool, registered = _tool(tmp_path, build_backend=_Backend())
    result = await tool.execute(
        {
            "continuation_task_id": "unknown-task",
            "continuation_answer": "answer",
        }
    )

    assert result.is_error is True
    assert "no pending commissioned build exists" in result.content
    assert registered == []
