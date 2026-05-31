"""OpenTelemetry audit adapter.

Exports audit events as OTLP log records via the OpenTelemetry Logs SDK.

Requires the optional ``otel`` extra::

    pip install 'bifrost[otel]'

Each audit event is emitted as a single ``LogRecord`` with the event fields
serialised as log record attributes.  The ``outcome`` field maps to the OTel
severity level (``ERROR`` for 'error', ``WARN`` for 'rejected'/'quota_exceeded',
``INFO`` otherwise).

When ``otel_endpoint`` is set, records are exported to that OTLP gRPC endpoint.
When the endpoint is empty, the ``ConsoleLogExporter`` is used (useful for
local debugging).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from bifrost.ports.audit import AuditEvent, AuditPort

logger = logging.getLogger(__name__)

# OTel severity numbers per spec.
_SEVERITY_INFO = 9
_SEVERITY_WARN = 13
_SEVERITY_ERROR = 17


def _severity(outcome: str) -> tuple[int, str]:
    """Return (severity_number, severity_text) for *outcome*."""
    match outcome:
        case "error":
            return (_SEVERITY_ERROR, "ERROR")
        case "rejected" | "quota_exceeded":
            return (_SEVERITY_WARN, "WARN")
        case _:
            return (_SEVERITY_INFO, "INFO")
    raise AssertionError("Unreachable _severity fallthrough")


class OtelAuditAdapter(AuditPort):
    """OpenTelemetry Logs SDK audit adapter.

    Args:
        otel_endpoint: OTLP gRPC endpoint URL.  Empty string → ConsoleLogExporter.
        service_name:  ``service.name`` resource attribute value.
    """

    def __init__(
        self,
        otel_endpoint: str = "",
        service_name: str = "bifrost",
    ) -> None:
        try:
            from opentelemetry._logs import set_logger_provider
            from opentelemetry.sdk._logs import LoggerProvider
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from opentelemetry.sdk.resources import Resource
        except ImportError as exc:
            raise ImportError(
                "The opentelemetry-sdk package is required for OTel audit mode. "
                "Install it with: pip install 'bifrost[otel]'"
            ) from exc

        resource = Resource.create({"service.name": service_name})
        self._provider = LoggerProvider(resource=resource)

        if otel_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
                    OTLPLogExporter,
                )

                exporter = OTLPLogExporter(endpoint=otel_endpoint)
            except ImportError as exc:
                raise ImportError(
                    "The opentelemetry-exporter-otlp-proto-grpc package is required "
                    "for OTel OTLP export. Install it with: pip install 'bifrost[otel]'"
                ) from exc
        else:
            from opentelemetry.sdk._logs.export import ConsoleLogExporter

            exporter = ConsoleLogExporter()

        self._provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        set_logger_provider(self._provider)
        self._otel_logger = self._provider.get_logger("bifrost.audit")

    async def log(self, event: AuditEvent) -> None:
        try:
            from opentelemetry.sdk._logs import LogRecord
            from opentelemetry.trace import INVALID_SPAN_CONTEXT

            severity_number, severity_text = _severity(event.outcome)

            attributes = {
                "bifrost.request_id": event.request_id,
                "bifrost.agent_id": event.agent_id,
                "bifrost.tenant_id": event.tenant_id,
                "bifrost.model": event.model,
                "bifrost.provider": event.provider,
                "bifrost.outcome": event.outcome,
                "bifrost.status_code": event.status_code,
                "bifrost.latency_ms": event.latency_ms,
                "bifrost.tokens_input": event.tokens_input,
                "bifrost.tokens_output": event.tokens_output,
                "bifrost.cost_usd": event.cost_usd,
                "bifrost.cache_hit": event.cache_hit,
                "bifrost.session_id": event.session_id,
                "bifrost.saga_id": event.saga_id,
                "bifrost.rule_name": event.rule_name,
                "bifrost.rule_action": event.rule_action,
                "bifrost.error_message": event.error_message,
            }
            if event.tags:
                attributes["bifrost.tags"] = json.dumps(event.tags)
            if event.prompt_content:
                attributes["bifrost.prompt_content"] = event.prompt_content
            if event.response_content:
                attributes["bifrost.response_content"] = event.response_content

            record = LogRecord(
                timestamp=int(event.timestamp.timestamp() * 1e9),
                observed_timestamp=int(event.timestamp.timestamp() * 1e9),
                span_context=INVALID_SPAN_CONTEXT,
                trace_flags=None,
                severity_number=severity_number,
                severity_text=severity_text,
                body=f"audit: {event.outcome} model={event.model} agent={event.agent_id}",
                resource=self._provider.resource,
                attributes=attributes,
            )
            self._otel_logger.emit(record)
        except Exception:
            logger.exception("OtelAuditAdapter.log failed for %s", event.request_id)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        model: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
    ) -> list[AuditEvent]:
        # OTel is write-only; querying is not supported.
        logger.warning("OtelAuditAdapter.query is not supported; returning empty list")
        return []

    async def close(self) -> None:
        try:
            self._provider.shutdown()
        except Exception:
            logger.warning("OtelAuditAdapter: error during shutdown", exc_info=True)
