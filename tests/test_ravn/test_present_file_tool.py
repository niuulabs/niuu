"""Ravn's present_file tool: a flock member presents a document through its broker."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ravn import cap_telemetry
from ravn.adapters.tools.present_file import PresentFileTool
from ravn.cli.trigger_wiring import _build_present_file_tool
from ravn.config import PresentFileConfig


class FakeMesh:
    def __init__(self, reply: dict | None = None, exc: Exception | None = None) -> None:
        self.reply = reply or {"status": "complete", "file_id": "pf_1", "name": "plan.md"}
        self.exc = exc
        self.sent: list[tuple[str, dict, float]] = []

    async def send(self, target_peer_id: str, message: dict, *, timeout_s: float = 10.0) -> dict:
        self.sent.append((target_peer_id, message, timeout_s))
        if self.exc is not None:
            raise self.exc
        return dict(self.reply)


def _tool(mesh: FakeMesh, workspace) -> PresentFileTool:
    return PresentFileTool(
        mesh,
        broker_peer_id="skuld-1234",
        own_peer_id="flock-developer-analyst",
        workspace_root=workspace,
        timeout_s=12.0,
    )


async def test_presents_a_workspace_file_by_its_relative_path(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "plan.md").write_text("# Plan")
    mesh = FakeMesh(
        {
            "status": "complete",
            "file_id": "pf_1",
            "provider_key": "s:plan",
            "revision_id": "pf_1",
            "version_label": "v1",
        }
    )
    result = await _tool(mesh, tmp_path).execute(
        {"path": str(tmp_path / "docs" / "plan.md"), "artifact_key": "plan", "title": "Plan"}
    )
    assert not result.is_error
    assert json.loads(result.content) == {
        "file_id": "pf_1",
        "provider_key": "s:plan",
        "revision_id": "pf_1",
        "version_label": "v1",
    }
    [(peer, message, timeout)] = mesh.sent
    assert (peer, timeout) == ("skuld-1234", 12.0)
    assert message == {
        "type": "present_file",
        "path": "docs/plan.md",
        "peer_id": "flock-developer-analyst",
        "title": "Plan",
        "artifact_key": "plan",
    }


async def test_content_is_written_under_ravn_state_then_presented(tmp_path):
    mesh = FakeMesh()
    result = await _tool(mesh, tmp_path).execute(
        {"content": "# Plan\n", "name": "plan.md", "artifact_key": "plan"}
    )
    assert not result.is_error
    relative = mesh.sent[0][1]["path"]
    assert relative.startswith(".ravn/presented/") and relative.endswith("/plan.md")
    assert (tmp_path / relative).read_text() == "# Plan\n"


@pytest.mark.parametrize(
    ("given", "says"),
    [
        ({}, "Give path"),
        ({"path": "a.md", "content": "x"}, "not both"),
        ({"content": "x", "name": "../evil.md"}, "name is the file name"),
        ({"path": "/etc/passwd"}, "must be in the session workspace"),
        ({"path": "missing.md"}, "No such file"),
        ({"content": "x", "name": "a.md", "artifact_key": "../plan"}, "artifact_key"),
    ],
)
async def test_bad_requests_are_refused_before_the_broker_is_asked(tmp_path, given, says):
    mesh = FakeMesh()
    result = await _tool(mesh, tmp_path).execute(given)
    assert result.is_error and says in result.content
    assert mesh.sent == []


async def test_a_broker_refusal_fails_the_tool(tmp_path):
    (tmp_path / "plan.md").write_text("x")
    mesh = FakeMesh({"status": "error", "error": "file exceeds max_presented_file_bytes"})
    result = await _tool(mesh, tmp_path).execute({"path": "plan.md"})
    assert result.is_error and "max_presented_file_bytes" in result.content


async def test_a_timeout_names_the_knob_and_is_counted(tmp_path):
    (tmp_path / "plan.md").write_text("x")
    before = cap_telemetry.cap_hit_counts().get("present_file.timeout_s", 0)
    result = await _tool(FakeMesh(exc=TimeoutError()), tmp_path).execute({"path": "plan.md"})
    assert result.is_error and "present_file.timeout_s" in result.content
    assert cap_telemetry.cap_hit_counts()["present_file.timeout_s"] == before + 1


def test_wiring_builds_the_tool_only_when_configured(tmp_path):
    def settings(enabled: bool) -> SimpleNamespace:
        return SimpleNamespace(
            present_file=PresentFileConfig(enabled=enabled, broker_peer_id="skuld-1"),
            mesh=SimpleNamespace(own_peer_id="flock-coder"),
            permission=SimpleNamespace(workspace_root=str(tmp_path)),
        )

    assert _build_present_file_tool(settings(False), FakeMesh()) is None
    assert _build_present_file_tool(settings(True), FakeMesh()).name == "present_file"
    with pytest.raises(RuntimeError, match="mesh.enabled"):
        _build_present_file_tool(settings(True), None)


def test_enabled_without_a_broker_is_a_config_error():
    with pytest.raises(ValueError, match="broker_peer_id"):
        PresentFileConfig(enabled=True)
