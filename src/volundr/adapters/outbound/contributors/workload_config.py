"""Workload config contributor for controlled runtime value overrides."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from volundr.domain.launch_env import LAUNCH_ENV_KEY
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

    This intentionally accepts only a small set of top-level runtime knobs, plus the
    session's allowlisted ``launch_env``, which becomes the broker's (and so the CLI's) env. The
    workload config bag is also used by higher-level session MCP features,
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

        # LEX-2045: the session's launch env, checked against the allowlist when it was
        # stored (create, PUT /sessions/{id}) and again at start. Only its shape is checked here.
        launch_env = context.workload_config.get(LAUNCH_ENV_KEY)
        if launch_env is not None:
            if not isinstance(launch_env, Mapping) or not all(
                isinstance(name, str) and name and isinstance(value, str)
                for name, value in launch_env.items()
            ):
                raise ValueError("workload_config.launch_env must map names to string values")
            if launch_env:
                values["envVars"] = [
                    {"name": name, "value": value} for name, value in launch_env.items()
                ]
        return SessionContribution(values=values)
