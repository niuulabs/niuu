"""Canonical, content-safe telemetry for a single runtime harness step."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

from ravn.domain.models import TokenUsage

HarnessType = Literal["native", "cli-backed"]

_MAX_CONTEXT_SECTION_ESTIMATES = 32
_MAX_CONTEXT_SECTION_NAME_LENGTH = 128


@dataclass(frozen=True)
class HarnessStepTelemetryRecord:
    """Normalized observability record for one model/runtime step.

    ``unavailable`` names every detail the harness could not observe.  Values
    are never fabricated as zero merely because a runtime did not expose them.
    The record intentionally contains composition summaries only, never prompt
    text, tool arguments, tool results, or model output.
    """

    step_id: str
    session_id: str
    task_id: str
    turn: int
    attempt: int
    phase: str
    harness_type: HarnessType
    model: str
    provider: str
    provider_call_id: str | None
    context_section_estimates: dict[str, int] | None
    context_message_count: int
    context_tool_schema_count: int
    compaction_applied: bool | None
    compaction_passes: int | None
    compaction_messages_removed: int | None
    provider_duration_ms: float | None
    ttft_ms: float | None
    tool_wall_time_ms: float | None
    tool_call_count: int
    usage: TokenUsage | None
    stop_reason: str | None
    llm_error_category: str | None
    retry_index: int
    recovery_state: str
    unavailable: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe record for the established event port."""
        data = asdict(self)
        usage = data["usage"]
        if usage is not None:
            data["usage"] = {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cache_read_tokens": usage["cache_read_tokens"],
                "cache_write_tokens": usage["cache_write_tokens"],
                "thinking_tokens": usage["thinking_tokens"],
            }
        return data

    def attributes(self) -> dict[str, str | int | float | bool]:
        """Return bounded attributes suitable for the observability event."""
        attributes: dict[str, str | int | float | bool] = {
            "ravn.harness.step.schema_version": "1",
            "ravn.harness.step.id": self.step_id,
            "ravn.harness.session.id": self.session_id,
            "ravn.harness.task.id": self.task_id,
            "ravn.harness.turn": self.turn,
            "ravn.harness.attempt": self.attempt,
            "ravn.harness.phase": self.phase,
            "ravn.harness.type": self.harness_type,
            "gen_ai.request.model": self.model,
            "gen_ai.provider.name": self.provider,
            "ravn.harness.context.message_count": self.context_message_count,
            "ravn.harness.context.tool_schema_count": self.context_tool_schema_count,
            "ravn.harness.tool.call_count": self.tool_call_count,
            "ravn.harness.recovery.retry_index": self.retry_index,
            "ravn.harness.recovery.state": self.recovery_state,
        }
        _set_available(attributes, "ravn.harness.provider_call.id", self.provider_call_id)
        _set_available(
            attributes, "ravn.harness.timing.provider_duration_ms", self.provider_duration_ms
        )
        _set_available(attributes, "ravn.harness.timing.ttft_ms", self.ttft_ms)
        _set_available(attributes, "ravn.harness.timing.tool_wall_time_ms", self.tool_wall_time_ms)
        _set_available(attributes, "ravn.harness.stop_reason", self.stop_reason)
        _set_available(attributes, "ravn.harness.llm_error_category", self.llm_error_category)
        _set_available(attributes, "ravn.harness.compaction.applied", self.compaction_applied)
        _set_available(attributes, "ravn.harness.compaction.passes", self.compaction_passes)
        _set_available(
            attributes,
            "ravn.harness.compaction.messages_removed",
            self.compaction_messages_removed,
        )
        _set_context_section_estimates(attributes, self.context_section_estimates)
        attributes["ravn.harness.usage.available"] = self.usage is not None
        if self.usage is not None:
            attributes["gen_ai.usage.input_tokens"] = self.usage.input_tokens
            attributes["gen_ai.usage.output_tokens"] = self.usage.output_tokens
            attributes["gen_ai.usage.cache_read_tokens"] = self.usage.cache_read_tokens
            attributes["gen_ai.usage.cache_write_tokens"] = self.usage.cache_write_tokens
            attributes["gen_ai.usage.thinking_tokens"] = self.usage.thinking_tokens
        for field_name, reason in self.unavailable.items():
            attributes[f"ravn.harness.{field_name}.available"] = False
            attributes[f"ravn.harness.{field_name}.unavailable_reason"] = reason
        return attributes


def _set_available(
    attributes: dict[str, str | int | float | bool],
    name: str,
    value: str | int | float | bool | None,
) -> None:
    attributes[f"{name}.available"] = value is not None
    if value is not None:
        attributes[name] = value


def _set_context_section_estimates(
    attributes: dict[str, str | int | float | bool],
    estimates: dict[str, int] | None,
) -> None:
    """Export prompt composition without relying on optional content capture."""
    name = "ravn.harness.context.section_estimates"
    attributes[f"{name}.available"] = estimates is not None
    if estimates is None:
        return

    bounded_estimates = {
        section[:_MAX_CONTEXT_SECTION_NAME_LENGTH]: tokens
        for section, tokens in sorted(estimates.items())[:_MAX_CONTEXT_SECTION_ESTIMATES]
    }
    attributes[name] = json.dumps(bounded_estimates, separators=(",", ":"), sort_keys=True)
    attributes["ravn.harness.context.section_estimate_count"] = len(estimates)
    attributes["ravn.harness.context.section_estimates.truncated"] = (
        len(estimates) > _MAX_CONTEXT_SECTION_ESTIMATES
    )
