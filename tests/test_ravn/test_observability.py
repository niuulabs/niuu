"""Tests for Ravn's optional OpenTelemetry facade."""

from __future__ import annotations

import pytest

from ravn.adapters.tools.build_tool import _build_result_outcome
from ravn.domain.models import ToolResult


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (ToolResult(tool_call_id="", content="failed", is_error=True), "error"),
        (
            ToolResult(tool_call_id="", content='{"registered": true}'),
            "registered",
        ),
        (
            ToolResult(
                tool_call_id="",
                content='{"review_required": true, "review_filed": true}',
            ),
            "review_pending",
        ),
        (
            ToolResult(tool_call_id="", content='{"status": "input_required"}'),
            "input_required",
        ),
    ],
)
def test_tool_build_lifecycle_outcome_is_not_inferred_from_success_alone(
    result: ToolResult,
    expected: str,
) -> None:
    assert _build_result_outcome(result) == expected


def test_span_context_and_metrics_are_real_when_sdk_is_installed() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from niuu.observability import Observability

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        capture_content=True,
        content_max_chars=256,
    )

    with telemetry.span("signal", attributes={"ravn.environment.id": "workshop"}):
        carrier = telemetry.inject()
    with telemetry.span("agent", carrier=carrier):
        telemetry.event(
            "model.response",
            content={"answer": "inspect capabilities", "token": "must-not-escape"},
        )
        assert len(telemetry.trace_id()) == 32
        telemetry.count("ravn.agent.tasks", attributes={"ravn.task.outcome": "complete"})
        telemetry.duration("ravn.agent.task.duration", 0.25)
        telemetry.gauge("ravn.queue.depth", 3)
    with telemetry.span("failed") as failed_span:
        telemetry.mark_error(
            failed_span,
            "a2a_rpc_failed",
            "A2A SendMessage failed with Bearer secret-value",
        )

    spans = span_exporter.get_finished_spans()
    assert [span.name for span in spans] == ["signal", "agent", "failed"]
    assert spans[0].context.trace_id == spans[1].context.trace_id
    assert spans[1].events[0].name == "model.response"
    assert "must-not-escape" not in spans[1].events[0].attributes["ravn.content"]
    assert "[REDACTED]" in spans[1].events[0].attributes["ravn.content"]
    assert carrier["traceparent"].startswith("00-")
    assert spans[2].attributes["error.type"] == "a2a_rpc_failed"
    assert spans[2].attributes["error.message"] == ("A2A SendMessage failed with Bearer [REDACTED]")
    assert spans[2].status.description == "A2A SendMessage failed with Bearer [REDACTED]"
    metric_names = {
        metric.name
        for resource in metric_reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert metric_names == {
        "ravn.agent.tasks",
        "ravn.agent.task.duration",
        "ravn.queue.depth",
    }

    telemetry.shutdown()


@pytest.mark.asyncio
async def test_learned_tool_lifecycle_has_explicit_trace_spans(
    tmp_path,
    monkeypatch,
) -> None:
    from contextlib import contextmanager

    import ravn.adapters.tools.build_tool as build_tool_module
    import ravn.adapters.tools.learned_tool_run as learned_tool_module
    import ravn.adapters.tools.skill_tools as skill_tools_module
    from ravn.adapters.permission.allow_deny import AllowAllPermission
    from ravn.adapters.skill.file_registry import FileSkillRegistry
    from ravn.adapters.tools.build_tool import BuildTool
    from ravn.adapters.tools.learned_tool_run import LearnedToolRunTool
    from ravn.adapters.tools.skill_tools import SkillManageTool
    from ravn.skills.management import SkillManagementRegistry

    class Span:
        def set_attribute(self, _name, _value) -> None:
            return None

    class RecordingTelemetry:
        def __init__(self) -> None:
            self.spans = []
            self.events = []
            self._active = []

        @contextmanager
        def span(self, name, **_kwargs):
            self.spans.append(name)
            self._active.append(name)
            try:
                yield Span()
            finally:
                self._active.pop()

        def event(self, name, **_kwargs) -> None:
            self.events.append((self._active[-1] if self._active else "", name))

        def set_attributes(self, _attributes) -> None:
            return None

        def mark_error(self, *_args, **_kwargs) -> None:
            return None

        def count(self, *_args, **_kwargs) -> None:
            return None

        def gauge(self, *_args, **_kwargs) -> None:
            return None

    telemetry = RecordingTelemetry()
    monkeypatch.setattr(build_tool_module, "get_observability", lambda: telemetry)
    monkeypatch.setattr(learned_tool_module, "get_observability", lambda: telemetry)
    monkeypatch.setattr(skill_tools_module, "get_observability", lambda: telemetry)

    skills_dir = tmp_path / "skills"
    manager = SkillManagementRegistry(
        FileSkillRegistry(
            skill_dirs=[str(skills_dir)],
            write_dir=skills_dir,
            include_builtin=False,
            cwd=tmp_path,
        ),
        metadata_path=tmp_path / "skill_management.json",
    )
    await manager.create(name="status_probe", content="capability: tool.status_probe")

    class LearnedTool:
        required_permission = "tool:run"

        async def execute(self, payload):
            return ToolResult(tool_call_id="", content="ok")

    class Resolver:
        def load(self, name):
            return LearnedTool()

    await LearnedToolRunTool(
        resolver=Resolver(),  # type: ignore[arg-type]
        permission=AllowAllPermission(),
        skill_manager=manager,
    ).execute({"name": "status_probe", "input": {}})
    await SkillManageTool(manager.skill_port, manager=manager).execute(
        {"action": "archive", "name": "status_probe"}
    )

    async def record_installed(_artifact) -> None:
        return None

    built = BuildTool(
        tools_dir=tmp_path / "tools",
        artifacts_dir=tmp_path / "artifacts",
        register_tool=lambda *_args, **_kwargs: None,
        autonomy_mode="autonomous",
        installed_artifact_recorder=record_installed,
    )
    await built.execute(
        {
            "manifest": {
                "name": "new_probe",
                "description": "Inspect status",
                "input_schema": {"type": "object"},
                "required_permission": "tool:run",
            },
            "tool_code": "def run(payload):\n    return {'ok': True}\n",
            "test_code": "",
        }
    )

    assert {
        "ravn.learned_tool.lifecycle.run",
        "ravn.skill.lifecycle",
        "ravn.learned_tool.lifecycle.register",
    }.issubset(telemetry.spans)
    assert (
        "ravn.learned_tool.lifecycle.run",
        "ravn.learned_tool.lifecycle.usage_recorded",
    ) in telemetry.events
    assert ("ravn.skill.lifecycle", "ravn.skill.lifecycle.finished") in telemetry.events
    assert (
        "ravn.learned_tool.lifecycle.register",
        "ravn.learned_tool.lifecycle.registered",
    ) in telemetry.events


def test_linked_span_starts_a_bounded_trace_with_causal_link() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from niuu.observability import Observability

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=MeterProvider(metric_readers=[InMemoryMetricReader()]),
    )

    with telemetry.span("before-restart"):
        carrier = telemetry.inject()
    with telemetry.span("after-restart", link_carrier=carrier):
        pass

    before, after = exporter.get_finished_spans()
    assert before.context.trace_id != after.context.trace_id
    assert len(after.links) == 1
    assert after.links[0].context.trace_id == before.context.trace_id
    telemetry.shutdown()


class TestMetricToolNameBounding:
    """A garbled tool call must not mint a new metric time series.

    Observed in production: tool-name labels containing a full JSON tool
    definition, a stray ``</parameter=1``, and a bare first name — one series
    each, permanently, in the tool-usage panels.
    """

    def test_real_tool_names_pass_through(self) -> None:
        from ravn.tool_observability import metric_tool_name

        for name in (
            "kubernetes_inspect",
            "mimir_search",
            "learned_tool_run",
            "web.fetch",
            "a2a:send",
            "build-tool",
        ):
            assert metric_tool_name(name) == name

    def test_malformed_names_collapse_to_one_label(self) -> None:
        from ravn.tool_observability import MALFORMED_TOOL_LABEL, metric_tool_name

        malformed = [
            '{\n  "name": "valkyrie-inspect-kubernetes-node",\n  "description": "..."\n}',
            "</parameter=1",
            "Elliott Smith wrote it",
            "kube-apiserver-jarnvidr-controlplane-xwdpt-jnk22\n</parameter=1",
            "x" * 200,
            "",
            None,
        ]
        labels = {metric_tool_name(n) for n in malformed}
        assert labels == {MALFORMED_TOOL_LABEL}

    def test_whitespace_is_not_a_new_series(self) -> None:
        from ravn.tool_observability import metric_tool_name

        assert metric_tool_name("  mimir_search  ") == "mimir_search"
