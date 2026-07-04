"""Resident session contributor.

When ``workload_type == "resident"``, this contributor provisions a
long-lived *flock of one*: a Skuld broker in room mode plus a single named
ravn daemon (the resident) that owns the conversation. Untargeted browser
messages route to the resident via ``room.default_target_peer_id``, so the
standard session chat works unchanged in any client.

The pod/process shape is identical to a one-persona ravn flock, so this
composes :class:`RavnFlockContributor` rather than forking it, and layers
resident semantics on top:

- no workflow trigger — nothing auto-stops the session; the resident lives
  until its owner stops it
- ``SKULD__ROOM__DEFAULT_TARGET_PEER_ID`` routes plain chat to the resident
- the ravn node config gains a ``skuld`` channel (WebSocket to the broker's
  ``/ws/ravn`` endpoint) and an ``environment.resident_name`` identity
- resident identity lands in ``values["resident"]`` so listing surfaces can
  read it from the session record

Example workload_config::

    workload_config:
      persona: product-steward       # required — the resident's persona
      resident_name: "Muninn"        # display identity (defaults to persona)
      mimir: {...}                   # same shape the flock accepts
      sleipnir_publish_urls: [...]
      llm_config: {...}
      daily_budget_usd: 5.0
      platform: {enabled: true, base_url: "http://volundr:8080"}
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from volundr.adapters.outbound.contributors.ravn_flock import (
    _DEFAULT_MAX_CONCURRENT_TASKS,
    RavnFlockContributor,
    _normalize_mimir_workload_config,
    _normalize_personas,
)
from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution

logger = logging.getLogger(__name__)

_WORKLOAD_TYPE = "resident"
# In-pod broker endpoint the resident's ravn connects to. Local runs rewrite
# the port via _apply_local_flock_overrides; in k8s the broker listens on its
# default container port inside the shared pod.
_IN_POD_BROKER_WS_URL = "ws://127.0.0.1:8081/ws/ravn"


class ResidentContributor(RavnFlockContributor):
    """Contributes a resident (flock-of-one) spec when workload_type == 'resident'."""

    @property
    def name(self) -> str:
        return _WORKLOAD_TYPE

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if context.workload_type == _WORKLOAD_TYPE and context.workload_config:
            wc = context.workload_config
        else:
            source = self._resolve_source(context)
            if source is None or source.workload_type != _WORKLOAD_TYPE:
                return SessionContribution()
            wc = source.workload_config

        persona_name = str(wc.get("persona") or "").strip()
        if not persona_name:
            raise ValueError(
                "resident workload_config requires a 'persona' — refusing to "
                "spawn a resident session without one"
            )

        resident_name = str(wc.get("resident_name") or persona_name).strip()

        persona_entry: dict[str, Any] = {"name": persona_name}
        for key in (
            "llm",
            "system_prompt_extra",
            "iteration_budget",
            "consumes_event_types",
            "max_concurrent_tasks",
        ):
            if key in wc:
                persona_entry[key] = wc[key]
        persona_dicts = _normalize_personas([persona_entry])
        if not persona_dicts:
            raise ValueError(f"resident persona {persona_name!r} did not normalize")

        mimir_cfg = _normalize_mimir_workload_config(
            wc.get("mimir") if isinstance(wc.get("mimir"), dict) else None,
            wc.get("mimir_hosted_url"),
        )
        sleipnir_cfg: dict = wc.get("sleipnir", {}) if isinstance(wc.get("sleipnir"), dict) else {}
        sleipnir_publish_urls = list(
            sleipnir_cfg.get("publish_urls") or wc.get("sleipnir_publish_urls") or []
        )
        global_llm: dict | None = wc.get("llm_config") or None
        raw_daily_budget_usd = wc.get("daily_budget_usd")
        try:
            daily_budget_usd = float(raw_daily_budget_usd)
        except (TypeError, ValueError):
            daily_budget_usd = None

        values, pod_spec = self._build_flock_spec(
            session=session,
            persona_dicts=persona_dicts,
            mesh_transport=str(wc.get("mesh_transport") or "nng"),
            mimir_config=mimir_cfg,
            sleipnir_publish_urls=sleipnir_publish_urls,
            global_max_concurrent_tasks=int(
                wc.get("max_concurrent_tasks", _DEFAULT_MAX_CONCURRENT_TASKS)
            ),
            global_llm=global_llm,
            daily_budget_usd=daily_budget_usd,
            initiative_context="",
            persona_source_mode=self._persona_source_mode,
            persona_source_configmap_name=self._persona_source_configmap_name,
            persona_source_mount_path=self._persona_source_mount_path,
            persona_source_http_base_url=self._persona_source_http_base_url,
            workflow=None,
            extra_ravn_config=self._resident_ravn_config(resident_name, wc),
        )

        peer_id = f"flock-{persona_dicts[0]['name']}"
        pod_spec = replace(
            pod_spec,
            env=(
                *pod_spec.env,
                {"name": "SKULD__ROOM__DEFAULT_TARGET_PEER_ID", "value": peer_id},
            ),
        )

        values["resident"] = {
            "name": resident_name,
            "peer_id": peer_id,
            "persona": persona_dicts[0]["name"],
        }

        logger.info(
            "resident: session=%s name=%r persona=%s peer=%s",
            str(session.id)[:8],
            resident_name,
            persona_dicts[0]["name"],
            peer_id,
        )

        return SessionContribution(values=values, pod_spec=pod_spec)

    @staticmethod
    def _resident_ravn_config(resident_name: str, wc: dict[str, Any]) -> dict[str, Any]:
        """Resident-flavored overlay for the ravn node config.

        The skuld WebSocket channel is how the resident joins its own room
        (registration frame, directed messages, response frames). Local runs
        overwrite ``skuld.broker_url`` with the real broker port; the in-pod
        default covers k8s.
        """
        extra: dict[str, Any] = {
            "skuld": {
                "enabled": True,
                "broker_url": _IN_POD_BROKER_WS_URL,
                "display_name": resident_name,
            },
            "environment": {"resident_name": resident_name},
        }
        platform_cfg = wc.get("platform")
        if isinstance(platform_cfg, dict) and platform_cfg:
            extra["gateway"] = {"platform": dict(platform_cfg)}
        return extra
