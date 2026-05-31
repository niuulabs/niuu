"""Workload config contributor for controlled runtime value overrides."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from volundr.domain.models import Session
from volundr.domain.ports import (
    SessionContext,
    SessionContribution,
    SessionContributor,
)

_ALLOWED_BROKER_KEYS = {
    "agentTeams",
    "approvalPolicy",
    "sandbox",
    "skipPermissions",
}


def _filtered_broker_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key in _ALLOWED_BROKER_KEYS}


class WorkloadConfigContributor(SessionContributor):
    """Applies explicit runtime overrides from ``SessionContext.workload_config``.

    This intentionally accepts only a small set of top-level runtime knobs. The
    workload config bag is also used by higher-level flock/session MCP features,
    so it should not be blindly merged into Helm values.
    """

    @property
    def name(self) -> str:
        return "workload_config"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        values: dict[str, Any] = {}
        broker_values: dict[str, Any] = {}

        broker = context.workload_config.get("broker")
        if isinstance(broker, dict):
            broker_values.update(_filtered_broker_config(broker))

        if broker_values:
            values["broker"] = broker_values
        return SessionContribution(values=values)
