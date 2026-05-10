"""Tests for canonical outcome emission from DriveLoop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.domain.outcome import OutcomeField
from tests.test_ravn.conftest import _make_agent_task, _make_drive_loop


class TestDriveLoopOutcomeContract:
    @pytest.mark.asyncio
    async def test_canonical_and_alias_outcomes_are_split(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        skuld_channel = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = skuld_channel
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="reviewer",
            produces=SimpleNamespace(
                event_type="review.completed",
                event_type_map={
                    "pass": "review.passed",
                    "needs_changes": "review.changes_requested",
                },
            ),
        )

        task = _make_agent_task(task_id="task-123")
        task.session_id = "sess-123"
        task.root_correlation_id = "root-123"
        task.workflow_parent_event_id = "code-task-123"
        response_text = """\
---outcome---
verdict: needs_changes
summary: tighten the edge-case handling
comments: fix the null branch
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        assert canonical_topic == "review.completed"
        assert canonical_event.payload["event_type"] == "review.completed"
        assert canonical_event.payload["bubble_up"] is True
        assert canonical_event.payload["room_bridge_skip"] is True
        assert canonical_event.payload["verdict"] == "needs_changes"
        assert canonical_event.payload["workflow_parent_event_id"] == "code-task-123"

        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]
        assert alias_topic == "review.changes_requested"
        assert alias_event.payload["event_type"] == "review.changes_requested"
        assert alias_event.payload["canonical_event_type"] == "review.completed"
        assert alias_event.payload["routing_only"] is True
        assert alias_event.payload["bubble_up"] is False
        assert alias_event.payload["room_bridge_skip"] is True

        skuld_channel.emit.assert_awaited_once()
        emitted = skuld_channel.emit.await_args.args[0]
        assert emitted.payload["event_type"] == "review.completed"
        assert emitted.payload["bubble_up"] is True

    @pytest.mark.asyncio
    async def test_canonical_outcome_uses_mesh_bridge_when_skuld_absent(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="coder",
            produces=SimpleNamespace(
                event_type="code.completed",
                event_type_map={"pass": "code.changed", "blocked": "code.blocked"},
            ),
        )

        task = _make_agent_task(task_id="task-456")
        task.session_id = "sess-456"
        response_text = """\
---outcome---
verdict: pass
summary: implemented the fix
files_changed: 2
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2
        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        assert canonical_topic == "code.completed"
        assert canonical_event.payload["event_type"] == "code.completed"
        assert canonical_event.payload["bubble_up"] is True
        assert canonical_event.payload["room_bridge_skip"] is False

        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]
        assert alias_topic == "code.changed"
        assert alias_event.payload["event_type"] == "code.changed"
        assert alias_event.payload["canonical_event_type"] == "code.completed"
        assert alias_event.payload["routing_only"] is True

    @pytest.mark.asyncio
    async def test_success_without_verdict_still_routes_pass_alias(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="coder",
            produces=SimpleNamespace(
                event_type="code.completed",
                event_type_map={"pass": "code.changed", "blocked": "code.blocked"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"code.changed"})

        task = _make_agent_task(task_id="task-no-verdict")
        task.session_id = "sess-no-verdict"
        task.workflow_node_id = "raid-coder"
        response_text = "Implemented and pushed the requested proof artifact."

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2

        canonical_event = mesh.publish.await_args_list[0].args[0]
        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_event.payload["verdict"] == "pass"
        assert canonical_event.payload["valid"] is True
        assert canonical_event.payload["event_type"] == "code.completed"
        assert alias_topic == "code.changed"
        assert alias_event.payload["event_type"] == "code.changed"
        assert alias_event.payload["canonical_event_type"] == "code.completed"

    @pytest.mark.asyncio
    async def test_success_without_verdict_uses_successful_schema_verdict(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="mimir-memory-curator",
            produces=SimpleNamespace(
                event_type="mimir.curated",
                event_type_map={"blocked": "mimir.curation_blocked"},
                schema={"verdict": {"values": ["complete", "blocked"]}},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"mimir.curated"})

        task = _make_agent_task(task_id="task-curation")
        task.session_id = "sess-curation"
        task.workflow_node_id = "raid-memory-curator"
        response_text = "Curated the ingested memory source into canonical wiki knowledge."

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]

        assert canonical_topic == "mimir.curated"
        assert canonical_event.payload["event_type"] == "mimir.curated"
        assert canonical_event.payload["verdict"] == "complete"
        assert canonical_event.payload["valid"] is True

    @pytest.mark.asyncio
    async def test_success_without_verdict_uses_outcome_field_enum_values(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="mimir-memory-curator",
            produces=SimpleNamespace(
                event_type="mimir.curated",
                event_type_map={"blocked": "mimir.curation_blocked"},
                schema={
                    "verdict": OutcomeField(
                        type="enum",
                        description="whether curation succeeded",
                        enum_values=["complete", "blocked"],
                    )
                },
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"mimir.curated"})

        task = _make_agent_task(task_id="task-curation-field-schema")
        task.session_id = "sess-curation-field-schema"
        task.workflow_node_id = "raid-memory-curator"
        response_text = "Curated the ingested memory source into canonical wiki knowledge."

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]

        assert canonical_topic == "mimir.curated"
        assert canonical_event.payload["event_type"] == "mimir.curated"
        assert canonical_event.payload["verdict"] == "complete"
        assert canonical_event.payload["valid"] is True

    @pytest.mark.asyncio
    async def test_suppresses_outcome_when_workflow_node_disallows_it(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = AsyncMock()
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="coordinator",
            produces=SimpleNamespace(event_type="ravn.task.completed", event_type_map={}),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"code.requested"})

        task = _make_agent_task(task_id="task-789")
        task.session_id = "sess-789"
        task.root_correlation_id = "root-789"
        task.workflow_node_id = "raid-coordinator-start"
        response_text = """\
---outcome---
verdict: approve
summary: task completed
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        mesh.publish.assert_not_awaited()
        dl._skuld_channel.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_merges_tool_metadata_into_canonical_outcome(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = AsyncMock()
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="postmortem-analyst",
            produces=SimpleNamespace(event_type="mimir.source.ingested", event_type_map={}),
        )

        task = _make_agent_task(task_id="task-mimir-123")
        task.session_id = "sess-mimir-123"
        task.root_correlation_id = "root-mimir-123"
        dl.record_tool_outcome_fields(
            task=task,
            event_type="mimir.source.ingested",
            fields={
                "source_id": "src_123",
                "mount_name": "tmp-mimir-test",
                "mount_names": ["tmp-mimir-test"],
            },
        )
        response_text = """\
---outcome---
verdict: complete
source_title: NIU-907 postmortem
summary: post-mortem source captured
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        assert canonical_event.payload["event_type"] == "mimir.source.ingested"
        assert canonical_event.payload["fields"]["source_id"] == "src_123"
        assert canonical_event.payload["fields"]["mount_name"] == "tmp-mimir-test"
        assert canonical_event.payload["fields"]["mount_names"] == ["tmp-mimir-test"]
        assert canonical_event.payload["fields"]["source_title"] == "NIU-907 postmortem"

    @pytest.mark.asyncio
    async def test_infers_mimir_mount_from_runtime_when_tool_metadata_missing(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = AsyncMock()
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="postmortem-analyst",
            produces=SimpleNamespace(event_type="mimir.source.ingested", event_type_map={}),
        )
        dl._settings.mimir.instances = [
            SimpleNamespace(name="tmp-mimir-test"),
        ]
        dl._settings.mimir.write_routing.default = []

        task = _make_agent_task(task_id="task-mimir-default-123")
        task.session_id = "sess-mimir-default-123"
        response_text = """\
---outcome---
verdict: complete
source_id: src_456
source_title: NIU-909 postmortem
summary: post-mortem source captured
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        canonical_event = mesh.publish.await_args_list[0].args[0]
        assert canonical_event.payload["fields"]["mount_name"] == "tmp-mimir-test"
        assert canonical_event.payload["fields"]["mount_names"] == ["tmp-mimir-test"]
