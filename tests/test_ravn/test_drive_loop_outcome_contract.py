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
    async def test_soft_wrapped_outcome_block_still_routes_alias_when_workflow_only_allows_alias(
        self,
    ) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-member-b",
            produces=SimpleNamespace(
                event_type="council.member.turn.completed",
                event_type_map={"opinion_submitted": "council.b.opinion.submitted"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"council.b.opinion.submitted"}
        )

        task = _make_agent_task(task_id="task-wrapped-verdict")
        task.session_id = "sess-wrapped-verdict"
        task.workflow_node_id = "member-b-opinion"
        response_text = """\
---outcome---
verdict: opinion_submitted
summary
: Wrote an
 evidence-driven memo recommending
 SQLite by default.
page_path: council
/example
/opinion-b
.md
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_topic == "council.member.turn.completed"
        assert canonical_event.payload["verdict"] == "opinion_submitted"
        assert (
            canonical_event.payload["summary"]
            == "Wrote an evidence-driven memo recommending SQLite by default."
        )
        assert canonical_event.payload["fields"]["page_path"] == "council/example/opinion-b.md"
        assert alias_topic == "council.b.opinion.submitted"
        assert alias_event.payload["event_type"] == "council.b.opinion.submitted"

    @pytest.mark.asyncio
    async def test_page_write_fallback_routes_alias_when_outcome_block_is_missing(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-member-b",
            produces=SimpleNamespace(
                event_type="council.member.turn.completed",
                event_type_map={"opinion_submitted": "council.b.opinion.submitted"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"council.b.opinion.submitted"}
        )

        task = _make_agent_task(task_id="task-page-write-fallback")
        task.session_id = "sess-page-write-fallback"
        task.workflow_node_id = "member-b-opinion"
        task.tool_outcomes["mimir.page.written"] = {
            "page_path": "council/example/opinions/opinion-b.md",
            "mount_name": "council-scratch-board",
        }

        await dl._emit_mesh_outcome_event(
            task,
            "SQLite offers a simpler local-first default for this setup.",
            success=True,
        )

        assert mesh.publish.await_count == 2

        canonical_event = mesh.publish.await_args_list[0].args[0]
        canonical_topic = mesh.publish.await_args_list[0].kwargs["topic"]
        alias_event = mesh.publish.await_args_list[1].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_topic == "council.member.turn.completed"
        assert canonical_event.payload["verdict"] == "opinion_submitted"
        assert canonical_event.payload["summary"] == "Wrote council/example/opinions/opinion-b.md"
        assert (
            canonical_event.payload["fields"]["page_path"]
            == "council/example/opinions/opinion-b.md"
        )
        assert canonical_event.payload["valid"] is True
        assert alias_topic == "council.b.opinion.submitted"
        assert alias_event.payload["event_type"] == "council.b.opinion.submitted"

    @pytest.mark.asyncio
    async def test_split_outcome_markers_still_route_alias_for_wrapped_codex_output(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-member-b",
            produces=SimpleNamespace(
                event_type="council.member.turn.completed",
                event_type_map={"opinion_submitted": "council.b.opinion.submitted"},
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"council.b.opinion.submitted"}
        )

        task = _make_agent_task(task_id="task-split-outcome-markers")
        task.session_id = "sess-split-outcome-markers"
        task.workflow_node_id = "member-b-opinion"
        response_text = """\
---
out
come
---

ver
dict
:
 opinion
_sub
mitted

summary
:
 W
rote
 an
 evidence
-driven
 opinion
 recommending
 SQLite
.

page
_path
:
 council
/example
/opinion-b
.md

---
end
---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2
        canonical_event = mesh.publish.await_args_list[0].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_event.payload["verdict"] == "opinion_submitted"
        assert canonical_event.payload["valid"] is True
        assert canonical_event.payload["fields"]["page_path"] == "council/example/opinion-b.md"
        assert alias_topic == "council.b.opinion.submitted"

    @pytest.mark.asyncio
    async def test_soft_wrapped_scalar_before_next_key_still_routes_alias(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-member-b",
            produces=SimpleNamespace(
                event_type="council.member.turn.completed",
                event_type_map={"opinion_submitted": "council.b.opinion.submitted"},
                schema={
                    "verdict": OutcomeField(
                        type="enum",
                        description="workflow verdict",
                        enum_values=["opinion_submitted"],
                    ),
                    "summary": OutcomeField(type="string", description="summary"),
                    "page_path": OutcomeField(type="string", description="page path"),
                },
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(
            lambda _task, _persona: {"council.b.opinion.submitted"}
        )

        task = _make_agent_task(task_id="task-soft-wrapped-scalar-before-key")
        task.session_id = "sess-soft-wrapped-scalar-before-key"
        task.workflow_node_id = "member-b-opinion"
        response_text = """\
---outcome---
ver
dict: opinion_sub
mitted
summary:
 Recommended lightweight human approval by
 default for final council publication
, with autonomous scratch
 work and risk-based graduation
 conditions.
page_path: council
/niu-906
-human-approval-gate
/opinions/opinion
-b.md
---
end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 2
        canonical_event = mesh.publish.await_args_list[0].args[0]
        alias_topic = mesh.publish.await_args_list[1].kwargs["topic"]

        assert canonical_event.payload["verdict"] == "opinion_submitted"
        assert canonical_event.payload["valid"] is True
        assert (
            canonical_event.payload["fields"]["page_path"]
            == "council/niu-906-human-approval-gate/opinions/opinion-b.md"
        )
        assert alias_topic == "council.b.opinion.submitted"

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

    @pytest.mark.asyncio
    async def test_help_needed_verdict_emits_help_notification_with_workflow_context(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        skuld_channel = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = skuld_channel
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-chair",
            produces=SimpleNamespace(
                event_type="council.chair.turn.completed",
                event_type_map={"help_needed": "council.human_input.requested"},
            ),
        )

        task = _make_agent_task(task_id="task-help")
        task.session_id = "sess-help"
        task.root_correlation_id = "root-help"
        task.workflow_parent_event_id = "parent-help"
        task.workflow_node_id = "chair-synthesis"
        response_text = """\
---outcome---
verdict: help_needed
summary: need the user's preference between the top two options
reason: uncertain
attempted:
  - compared the strongest evidence
  - reviewed prior Mimir notes
recommendation: choose whether to optimize for latency or quality
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        assert mesh.publish.await_count == 3
        help_event = mesh.publish.await_args_list[1].args[0]
        help_topic = mesh.publish.await_args_list[1].kwargs["topic"]
        alias_event = mesh.publish.await_args_list[2].args[0]

        assert help_topic == "help_needed"
        assert (
            help_event.payload["summary"]
            == "need the user's preference between the top two options"
        )
        assert help_event.payload["context"]["root_correlation_id"] == "root-help"
        assert help_event.payload["context"]["workflow_parent_event_id"] == "parent-help"
        assert help_event.payload["context"]["workflow_node_id"] == "chair-synthesis"
        assert alias_event.payload["event_type"] == "council.human_input.requested"

        emitted_types = [call.args[0].type for call in skuld_channel.emit.await_args_list]
        assert emitted_types == ["outcome", "help_needed"]

    @pytest.mark.asyncio
    async def test_help_needed_bypasses_node_outcome_filter_for_human_intervention(self) -> None:
        dl = _make_drive_loop()
        mesh = AsyncMock()
        dl._mesh = mesh
        dl._skuld_channel = None
        dl._source_id = "drive_loop"
        dl._persona_config = SimpleNamespace(
            name="council-chair",
            produces=SimpleNamespace(
                event_type="council.chair.turn.completed",
                event_type_map={
                    "research_published": "research.completed",
                    "help_needed": "council.human_input.requested",
                },
            ),
        )
        dl.set_workflow_allowed_outcomes_resolver(lambda _task, _persona: {"research.completed"})

        task = _make_agent_task(task_id="task-help-filter")
        task.session_id = "sess-help-filter"
        task.workflow_node_id = "chair-synthesis"
        response_text = """\
---outcome---
verdict: help_needed
summary: need an operator tie-break
reason: the final two opinions remain split
attempted:
  - compared the submitted reviews
recommendation: reply with the preferred tradeoff
---end---
"""

        await dl._emit_mesh_outcome_event(task, response_text, success=True)

        published_topics = [call.kwargs["topic"] for call in mesh.publish.await_args_list]
        assert "council.chair.turn.completed" in published_topics
        assert "council.human_input.requested" in published_topics
        assert "help_needed" in published_topics
