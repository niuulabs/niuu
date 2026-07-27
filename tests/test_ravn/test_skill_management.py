"""Tests for Environment-aware skill management and telemetry."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ravn.adapters.skill.sqlite import SqliteSkillAdapter
from ravn.adapters.tools.skill_tools import SkillListTool, SkillManageTool, SkillRunTool
from ravn.domain.models import Episode, Outcome
from ravn.skills.management import SkillManagementRegistry


async def _adapter(tmp_path: Path, *, threshold: int = 3) -> SqliteSkillAdapter:
    adapter = SqliteSkillAdapter(
        path=str(tmp_path / "skills.db"),
        suggestion_threshold=threshold,
    )
    await adapter.initialize()
    return adapter


def _episode(index: int, *, tool: str = "kubectl") -> Episode:
    return Episode(
        episode_id=f"ep-{index}",
        session_id="sess-1",
        timestamp=datetime.now(UTC),
        summary="Cluster probe succeeded",
        task_description="Investigate pod restarts",
        tools_used=[tool],
        outcome=Outcome.SUCCESS,
        tags=["environment:cluster-a", "domain:k8s"],
        structured_outcome={
            "environment_id": "cluster-a",
            "domain_scope": "k8s",
        },
        outcome_valid=True,
    )


async def test_create_environment_skill_is_immediately_discoverable(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")

    skill = await manager.create(
        name="k8s restart probe",
        content="Use `kubectl` to inspect restart counts before taking action.",
        scope="environment",
        environment_id="cluster-a",
        domain="k8s",
        action_safety_class="read_only",
        source="flock-learning:learning-1",
        source_environment_id="cluster-source",
        source_valkyrie_id="valkyrie:k8s-source",
    )

    listed = await manager.list_skills()
    runnable = await manager.get_runnable_skill("k8s restart probe")
    assert skill.name == "k8s restart probe"
    assert runnable is not None
    assert runnable.name == "k8s restart probe"
    assert listed[0]["metadata"]["scope"] == "environment"
    assert listed[0]["metadata"]["environment_id"] == "cluster-a"
    assert listed[0]["metadata"]["source_environment_id"] == "cluster-source"
    assert listed[0]["metadata"]["source_valkyrie_id"] == "valkyrie:k8s-source"

    await manager.update(
        name="k8s restart probe",
        source="flock-learning:learning-2",
        source_environment_id="cluster-new-source",
        source_valkyrie_id="valkyrie:k8s-new-source",
    )
    shown = await manager.show("k8s restart probe")
    assert shown["metadata"]["source"] == "flock-learning:learning-2"
    assert shown["metadata"]["source_environment_id"] == "cluster-new-source"
    assert shown["metadata"]["source_valkyrie_id"] == "valkyrie:k8s-new-source"


async def test_duplicate_active_skill_is_rejected(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")
    await manager.create(name="triage inbox", content="Draft with `mailgrep`.")

    with pytest.raises(ValueError):
        await manager.create(name="triage inbox", content="Different body.")


async def test_lifecycle_archive_restore_pin_promote_and_usage(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")
    await manager.create(name="printer resin check", content="Check `printerctl` resin state.")

    await manager.archive("printer resin check")
    assert await manager.get_runnable_skill("printer resin check") is None
    archived = await manager.list_skills(include_archived=True)
    assert archived[0]["metadata"]["status"] == "archived"

    await manager.restore("printer resin check")
    await manager.pin("printer resin check", pinned=True)
    promoted = await manager.promote(
        "printer resin check",
        scope="flock",
        environment_id="printer-cell-a",
        domain="printer",
    )
    telemetry = await manager.record_usage(
        "printer resin check",
        success=False,
        environment_id="printer-cell-a",
        domain="printer",
        action_safety_class="diagnostic",
    )

    assert promoted.scope == "flock"
    assert telemetry.run_count == 1
    assert telemetry.failure_count == 1
    assert telemetry.pinned is True
    assert telemetry.action_safety_class == "diagnostic"


async def test_skill_tools_honor_shared_archive_state(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")
    await manager.create(name="obsolete probe", content="Inspect with `probe`.")
    await manager.archive("obsolete probe")

    listed = await SkillListTool(skill_port, manager=manager).execute({})
    loaded = await SkillRunTool(skill_port, manager=manager).execute(
        {"skill_name": "obsolete probe"}
    )

    assert "obsolete probe" not in listed.content
    assert loaded.is_error


async def test_successful_environment_episodes_feed_skill_extraction(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path, threshold=2)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")

    assert await manager.record_episode(_episode(1)) is None
    skill = await manager.record_episode(_episode(2))

    assert skill is not None
    shown = await manager.show(skill.name)
    assert shown["metadata"]["source"] == "episode"
    assert shown["metadata"]["scope"] == "environment"
    assert shown["metadata"]["environment_id"] == "cluster-a"
    assert shown["metadata"]["domain"] == "k8s"


async def test_skill_manage_tool_permissions_and_actions(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")
    tool = SkillManageTool(skill_port, manager=manager)

    assert SkillListTool(skill_port).required_permission == "skill:read"
    assert SkillRunTool(skill_port).required_permission == "skill:read"
    assert tool.required_permission == "skill:manage"

    created = await tool.execute(
        {
            "action": "create",
            "name": "k8s event classifier",
            "content": "Classify pod warnings with `kubectl` and record evidence.",
            "scope": "environment",
            "environment_id": "cluster-a",
        }
    )
    assert not created.is_error
    assert json.loads(created.content)["status"] == "created"

    telemetry = await tool.execute(
        {
            "action": "telemetry",
            "name": "k8s event classifier",
            "success": True,
            "environment_id": "cluster-a",
            "domain": "k8s",
        }
    )
    assert json.loads(telemetry.content)["metadata"]["run_count"] == 1

    archived = await tool.execute({"action": "archive", "name": "k8s event classifier"})
    assert json.loads(archived.content)["metadata"]["status"] == "archived"
    restored = await tool.execute({"action": "restore", "name": "k8s event classifier"})
    assert json.loads(restored.content)["metadata"]["status"] == "active"


async def test_validate_action_rejects_empty_content(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path)
    tool = SkillManageTool(
        skill_port,
        manager=SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json"),
    )

    result = await tool.execute({"action": "validate", "name": "empty", "content": ""})

    assert result.is_error
    assert "content is required" in result.content


async def test_skill_manage_guarded_mode_defers_skill_changes(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")
    tool = SkillManageTool(skill_port, manager=manager)

    result = await tool.execute(
        {
            "action": "create",
            "name": "guarded k8s probe",
            "content": "Use `kubectl` to inspect events.",
            "scope": "environment",
            "environment_id": "cluster-a",
            "autonomy_mode": "guarded",
            "proposal_store_path": str(tmp_path / "proposals.json"),
        }
    )

    payload = json.loads(result.content)
    assert payload["status"] == "needs_review"
    assert payload["proposal"]["policy_decision"] == "needs_approval"
    assert await manager.get_runnable_skill("guarded k8s probe") is None


async def test_skill_manage_yolo_applies_environment_skill_and_rolls_back(
    tmp_path: Path,
) -> None:
    skill_port = await _adapter(tmp_path)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")
    tool = SkillManageTool(skill_port, manager=manager)
    proposal_store_path = tmp_path / "proposals.json"

    result = await tool.execute(
        {
            "action": "create",
            "name": "yolo k8s probe",
            "content": "Use `kubectl` to inspect warnings before paging a human.",
            "scope": "environment",
            "environment_id": "cluster-a",
            "autonomy_mode": "yolo",
            "proposal_store_path": str(proposal_store_path),
        }
    )
    payload = json.loads(result.content)
    proposal_id = payload["proposal"]["proposal_id"]

    assert payload["status"] == "applied"
    assert payload["proposal"]["status"] == "applied"
    assert await manager.get_runnable_skill("yolo k8s probe") is not None

    rolled_back = await tool.execute(
        {
            "action": "rollback",
            "proposal_id": proposal_id,
            "proposal_store_path": str(proposal_store_path),
        }
    )
    rollback_payload = json.loads(rolled_back.content)

    assert rollback_payload["status"] == "rolled_back"
    assert await manager.get_runnable_skill("yolo k8s probe") is None


async def test_skill_manage_yolo_gates_external_sends(tmp_path: Path) -> None:
    skill_port = await _adapter(tmp_path)
    manager = SkillManagementRegistry(skill_port, metadata_path=tmp_path / "meta.json")
    tool = SkillManageTool(skill_port, manager=manager)

    result = await tool.execute(
        {
            "action": "create",
            "name": "send invoice mail",
            "content": "Automatically send external email replies.",
            "scope": "environment",
            "environment_id": "inbox-host",
            "autonomy_mode": "yolo",
            "risk_boundaries": ["external_send"],
            "proposal_store_path": str(tmp_path / "proposals.json"),
        }
    )

    payload = json.loads(result.content)
    assert payload["status"] == "needs_review"
    assert "external_send" in payload["proposal"]["policy_reason"]


async def test_successful_use_restores_stale_skill(tmp_path) -> None:
    """A stale-marked skill that gets used successfully becomes active again."""
    from ravn.adapters.skill.file_registry import FileSkillRegistry
    from ravn.skills.management import SkillManagementRegistry

    skill_dir = tmp_path / "skills"
    skills = SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "skill_management.json",
    )
    await skills.create(name="probe", content="# skill: probe\n\nBody.\n")
    await skills.mark_stale("probe")
    assert (await skills.show("probe"))["metadata"]["status"] == "stale"

    await skills.record_usage("probe", success=True)
    assert (await skills.show("probe"))["metadata"]["status"] == "active"

    await skills.mark_stale("probe")
    await skills.record_usage("probe", success=False)
    assert (await skills.show("probe"))["metadata"]["status"] == "stale"
