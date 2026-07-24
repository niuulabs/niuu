"""Shared OpenTelemetry configuration for Niuu runtime processes."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ObservabilityConfig(BaseModel):
    """OpenTelemetry trace and metric export settings."""

    enabled: bool = Field(default=False)
    service_name: str = Field(default="ravn")
    trace_endpoint: str = Field(
        default="",
        description="OTLP/gRPC endpoint for traces, including scheme and port.",
    )
    metric_endpoint: str = Field(
        default="",
        description="OTLP/HTTP metrics endpoint, including the /v1/metrics path.",
    )
    insecure: bool = Field(
        default=False,
        description="Disable TLS for the OTLP/gRPC trace exporter.",
    )
    metric_export_interval_milliseconds: int = Field(default=10_000, ge=1_000)
    capture_content: bool = Field(
        default=False,
        description=(
            "Include redacted, size-bounded event content in trace events. "
            "Disabled by default because payloads may be sensitive."
        ),
    )
    content_max_chars: int = Field(
        default=8_192,
        ge=256,
        le=100_000,
        description="Maximum serialized characters attached to one trace event.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Optional OTLP headers for both exporters.",
    )

    @model_validator(mode="after")
    def _validate_enabled_endpoints(self) -> ObservabilityConfig:
        if self.enabled and (not self.trace_endpoint or not self.metric_endpoint):
            raise ValueError(
                "observability requires trace_endpoint and metric_endpoint when enabled"
            )
        return self


__all__ = ["ObservabilityConfig"]
