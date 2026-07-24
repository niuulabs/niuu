"""Shared telemetry for native and MCP-dispatched Ravn tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from time import monotonic
from typing import Any

from niuu.observability import get_observability


async def execute_observed_tool[T](
    *,
    name: str,
    arguments: dict[str, Any],
    execute: Callable[[], Awaitable[T]],
    call_id: str = "",
    agent_name: str = "ravn",
    conversation_id: str = "",
    task_id: str = "",
    iteration: int | None = None,
    carrier: dict[str, str] | None = None,
    runtime_component: str = "resident",
) -> T:
    """Execute one tool and emit the same spans and metrics on every runtime."""
    telemetry = get_observability()
    attributes = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": name,
        "gen_ai.tool.type": "function",
        "gen_ai.agent.name": agent_name or "ravn",
        "ravn.runtime.component": runtime_component,
        **tool_argument_attributes(name, arguments),
    }
    if conversation_id:
        attributes["gen_ai.conversation.id"] = conversation_id
    if task_id:
        attributes["ravn.task.id"] = task_id
    if iteration is not None:
        attributes["ravn.agent.iteration"] = iteration
    if carrier:
        attributes["ravn.trace.relationship"] = "remote_parent"

    metric_attributes = {
        key: attributes[key]
        for key in (
            "gen_ai.operation.name",
            "gen_ai.tool.name",
            "gen_ai.agent.name",
            "ravn.learned_tool.name",
            "ravn.runtime.component",
        )
        if key in attributes
    }
    if carrier:
        telemetry.count(
            "ravn.trace.boundaries",
            attributes={
                "ravn.trace.relationship": "remote_parent",
                "ravn.trace.component": "tool_execution",
                "ravn.runtime.component": runtime_component,
            },
            description="Explicit cross-process and restart trace boundaries.",
        )
    started = monotonic()
    with telemetry.span(
        f"execute_tool {name}",
        attributes=attributes,
        carrier=carrier,
    ) as span:
        telemetry.event(
            "gen_ai.tool.request",
            attributes={
                "gen_ai.tool.name": name,
                "gen_ai.tool.call.id": call_id,
            },
            content=arguments,
        )
        try:
            result = await execute()
        except Exception as exc:
            outcome_attributes = {
                **metric_attributes,
                "ravn.tool.outcome": "exception",
                "error.type": type(exc).__name__,
            }
            telemetry.mark_error(span, type(exc).__name__, str(exc))
            telemetry.count("ravn.agent.tool.calls", attributes=outcome_attributes)
            telemetry.duration(
                "ravn.agent.tool.duration",
                monotonic() - started,
                attributes=outcome_attributes,
                description="Duration of a resident tool execution.",
            )
            raise

        is_error = bool(getattr(result, "is_error", False))
        outcome = "error" if is_error else "success"
        outcome_attributes = {
            **metric_attributes,
            "ravn.tool.outcome": outcome,
        }
        span.set_attribute("ravn.tool.outcome", outcome)
        if is_error:
            outcome_attributes["error.type"] = "tool_error"
            telemetry.mark_error(span, "tool_error")
        telemetry.event(
            "gen_ai.tool.response",
            attributes={
                "gen_ai.tool.name": name,
                "gen_ai.tool.call.id": call_id,
                "ravn.tool.outcome": outcome,
            },
            content=getattr(result, "content", ""),
        )
        telemetry.count("ravn.agent.tool.calls", attributes=outcome_attributes)
        telemetry.duration(
            "ravn.agent.tool.duration",
            monotonic() - started,
            attributes=outcome_attributes,
            description="Duration of a resident tool execution.",
        )
        return result


def publish_learned_tool_inventory(artifacts: Iterable[Any]) -> int:
    """Publish the resident's bounded installed-tool catalog to the HUD."""
    telemetry = get_observability()
    count = 0
    for artifact in artifacts:
        manifest = artifact.manifest
        verification = artifact.provenance.get("verification")
        verified = (
            "passed"
            if isinstance(verification, dict) and verification.get("ok") is True
            else "failed"
            if isinstance(verification, dict) and verification.get("ok") is False
            else "unknown"
        )
        telemetry.gauge(
            "ravn.learned_tool.installed",
            1,
            attributes={
                "ravn.learned_tool.name": manifest.name,
                "ravn.learned_tool.artifact_id": artifact.artifact_id,
                "ravn.learned_tool.artifact_type": artifact.artifact_type,
                "ravn.learned_tool.verification": verified,
                "ravn.learned_tool.required_permission": manifest.required_permission,
            },
            description="Installed learned tools visible to this resident.",
        )
        count += 1
    telemetry.gauge(
        "ravn.learned_tool.count",
        count,
        description="Number of installed learned tools visible to this resident.",
    )
    return count


def tool_argument_attributes(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return bounded identity attributes derived from one tool invocation."""
    attributes: dict[str, Any] = {}
    for input_name, attribute_name in (
        ("action", "ravn.tool.action"),
        ("skill_id", "a2a.skill.id"),
        ("capability_name", "ravn.capability.name"),
        ("task_id", "a2a.task.id"),
        ("operation_id", "ravn.tool_build.operation.id"),
    ):
        value = arguments.get(input_name)
        if isinstance(value, str | int | float | bool):
            attributes[attribute_name] = value

    if name == "learned_tool_run":
        learned_name = arguments.get("name")
        if isinstance(learned_name, str) and learned_name:
            attributes["ravn.learned_tool.name"] = learned_name

    manifest = arguments.get("manifest")
    if name == "build_tool" and isinstance(manifest, dict):
        tool_name = manifest.get("name")
        if isinstance(tool_name, str) and tool_name:
            attributes["ravn.tool_build.name"] = tool_name
    return attributes


__all__ = [
    "execute_observed_tool",
    "publish_learned_tool_inventory",
    "tool_argument_attributes",
]
