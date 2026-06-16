"""Resource contributor — translates user-friendly resource config to K8s primitives."""

import logging
from typing import Any

from volundr.domain.models import Session
from volundr.domain.ports import (
    ResourceProvider,
    SessionContext,
    SessionContribution,
    SessionContributor,
)

logger = logging.getLogger(__name__)


class ResourceContributor(SessionContributor):
    """Translates ad-hoc resource config into K8s-native Helm values.

    Reads ``resource_config`` from the SessionContext (user launch request)
    and calls ResourceProvider.translate() to produce K8s-native values.
    Launch-spec resource configs are already K8s-native and pass through
    directly via LaunchSpecContributor.
    """

    def __init__(
        self,
        *,
        resource_provider: ResourceProvider | None = None,
        **_extra: object,
    ) -> None:
        self._resource_provider = resource_provider

    @property
    def name(self) -> str:
        return "resource"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if self._resource_provider is None:
            return SessionContribution()

        # Ad-hoc resource config from the launch request takes priority
        resource_config = context.resource_config
        if not resource_config:
            return SessionContribution()

        # Validate
        errors = self._resource_provider.validate(resource_config)
        if errors:
            logger.warning(
                "Resource validation warnings for session %s: %s",
                session.id,
                "; ".join(errors),
            )

        # Translate to K8s primitives
        translated = self._resource_provider.translate(resource_config)

        values: dict[str, Any] = {}

        # Build per-container resource overrides matching the Helm chart layout.
        # CPU/memory requests+limits go to the workload containers (devrunner
        # gets the full user budget, skuld keeps defaults).
        # GPU limits always go to devrunner; when gpu_timeslice is enabled,
        # GPU limits are also set on skuld so both containers share the GPU
        # via NVIDIA time-slicing.
        gpu_timeslice = str(resource_config.get("gpu_timeslice", "")).lower() == "true"

        if translated.requests or translated.limits:
            # Separate CPU/memory from accelerator resources (GPU)
            cpu_mem_requests = {
                k: v for k, v in translated.requests.items() if k in ("cpu", "memory")
            }
            cpu_mem_limits = {k: v for k, v in translated.limits.items() if k in ("cpu", "memory")}
            gpu_limits = {k: v for k, v in translated.limits.items() if k not in ("cpu", "memory")}

            # Devrunner always gets user-specified CPU/memory + GPU
            devrunner_resources: dict[str, Any] = {}
            if cpu_mem_requests:
                devrunner_resources["requests"] = dict(cpu_mem_requests)
            if cpu_mem_limits or gpu_limits:
                devrunner_resources["limits"] = {**cpu_mem_limits, **gpu_limits}
            if devrunner_resources:
                values.setdefault("localServices", {}).setdefault("devrunner", {})["resources"] = (
                    devrunner_resources
                )

            # Top-level "resources" key (skuld broker)
            skuld_limits = dict(cpu_mem_limits)
            if gpu_timeslice and gpu_limits:
                skuld_limits.update(gpu_limits)

            if cpu_mem_requests or skuld_limits:
                values["resources"] = {}
                if cpu_mem_requests:
                    values["resources"]["requests"] = cpu_mem_requests
                if skuld_limits:
                    values["resources"]["limits"] = skuld_limits

        # Pod-level scheduling constraints
        if translated.node_selector:
            values["nodeSelector"] = translated.node_selector

        if translated.tolerations:
            values["tolerations"] = translated.tolerations

        if translated.runtime_class_name:
            values["runtimeClassName"] = translated.runtime_class_name

        if values:
            logger.info(
                "Resource contributor: session %s → %s",
                session.id,
                {k: v for k, v in values.items() if k not in ("resources", "localServices")},
            )

        return SessionContribution(values=values)
