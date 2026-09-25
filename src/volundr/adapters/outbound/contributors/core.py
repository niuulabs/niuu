"""Core session contributor — pure session data, labels, terminal config."""

from typing import Any

from niuu.observability import get_observability
from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor


class CoreSessionContributor(SessionContributor):
    """Sets session identity values, ingress host, and terminal restriction.

    This is the only contributor without a port — it sets pure session
    data plus the few lines of config that don't warrant their own class.

    Also carries the W3C trace context (if observability is enabled and a
    span is active on the creating request) into ``envVars`` as plain
    ``TRACEPARENT``/``TRACESTATE`` — the one place every session, of every
    pod type and local processes alike, picks up env (see
    ``LocalProcessPodManager._session_env``, which folds ``envVars`` into
    every runtime it spawns). Previously only ``ravn_flock`` sessions carried
    trace context, via a separate Ting-workflow-specific path
    (``provenance.trace_context``); this makes it universal.
    """

    def __init__(
        self,
        *,
        base_domain: str = "volundr.local",
        gateway_domain: str | None = None,
        ingress_enabled: bool = True,
        **_extra: object,
    ):
        self._base_domain = base_domain
        self._gateway_domain = gateway_domain
        self._ingress_enabled = ingress_enabled

    @property
    def name(self) -> str:
        return "core"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        session_id = str(session.id)
        values: dict[str, Any] = {
            "session": {
                "id": session_id,
                "name": session.name,
                "model": session.model,
            },
        }

        workload = context.workload_config
        card_url = str(workload.get("a2aCardUrl") or workload.get("a2a_card_url") or "").strip()
        if card_url:
            values["session"].update(
                {
                    "a2aCardUrl": card_url,
                    "a2aEndpointUrl": str(
                        workload.get("a2aEndpointUrl") or workload.get("a2a_endpoint_url") or ""
                    ).strip(),
                    "environmentId": str(
                        workload.get("environmentId") or workload.get("environment_id") or ""
                    ).strip(),
                    "a2aVisibility": str(
                        workload.get("a2aVisibility") or workload.get("a2a_visibility") or "user"
                    ).strip(),
                    "ownerId": session.owner_id or "",
                    "tenantId": session.tenant_id or "",
                }
            )

        if self._ingress_enabled:
            values["ingress"] = {
                "host": f"{session.name}.{self._base_domain}",
            }

        if context.terminal_restricted:
            values["localServices"] = {"terminal": {"restricted": True}}

        trace_carrier = get_observability().inject()
        if trace_carrier:
            values["envVars"] = [
                {"name": key.upper(), "value": value} for key, value in trace_carrier.items()
            ]

        return SessionContribution(values=values)
