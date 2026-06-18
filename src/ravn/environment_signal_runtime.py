"""Runtime wiring for Environment signal sources."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from ravn.config import Settings, SignalSourceConfig
from ravn.domain.environment import (
    Environment,
    FlockMembership,
    SignalSource,
    TopologyRef,
    apply_environment_metadata,
)
from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.signal_adapter import NormalizedSignal, SignalAdapter
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

logger = logging.getLogger(__name__)

EnqueueFn = Callable[[AgentTask], Awaitable[None]]
ResidentSignalFn = Callable[[SleipnirEvent], Awaitable[dict[str, Any] | None]]


def _import_class(dotted_path: str) -> type:
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _inject_secrets(kwargs: dict[str, Any], secret_map: dict[str, str]) -> dict[str, Any]:
    merged = dict(kwargs)
    for kwarg_name, env_var in secret_map.items():
        value = os.environ.get(env_var, "")
        if value:
            merged[kwarg_name] = value
    return merged


def build_runtime_environment(settings: Settings) -> Environment:
    """Project runtime config into the shared Environment contract."""
    cfg = settings.environment
    if cfg.type == "k8s":
        type_id = "cluster"
    elif cfg.type.startswith("host"):
        type_id = "host"
    else:
        type_id = cfg.type
    topology = TopologyRef(
        node_id=f"{type_id}:{cfg.id}",
        type_id=type_id,
        realm_id=cfg.id,
        cluster_id=cfg.id if cfg.type == "k8s" else "",
        host_id=cfg.id if cfg.type.startswith("host") else "",
    )
    return Environment(
        id=cfg.id,
        name=cfg.name or cfg.id,
        type=cfg.type,  # type: ignore[arg-type]
        tenant_id=cfg.id,
        topology=topology,
        resident_valkyrie_ids=[settings.mesh.own_peer_id] if settings.mesh.own_peer_id else [],
        signal_sources=[
            SignalSource(
                id=source.id,
                name=source.name or source.id,
                kind=source.kind,  # type: ignore[arg-type]
                adapter=source.adapter,
                enabled=source.enabled,
                metadata=dict(source.kwargs.get("metadata", {})),
            )
            for source in cfg.signal_sources
        ],
        flock_memberships=[
            FlockMembership(flock_id=flock_id, role="resident") for flock_id in cfg.flocks
        ],
    )


class EnvironmentSignalRuntime:
    """Poll configured source adapters and publish deduped normalized signals."""

    def __init__(
        self,
        *,
        settings: Settings,
        publisher: SleipnirPublisher,
        enqueue: EnqueueFn | None = None,
        resident_signal_processor: ResidentSignalFn | None = None,
        persona: str | None = None,
        output_mode: OutputMode = OutputMode.AMBIENT,
        owns_publisher: bool = False,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._enqueue = enqueue
        self._resident_signal_processor = resident_signal_processor
        self._persona = persona
        self._output_mode = output_mode
        self._owns_publisher = owns_publisher
        self._environment = build_runtime_environment(settings)
        self._adapters: list[SignalAdapter] = []
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._tasks: list[asyncio.Task] = []

    @property
    def source_count(self) -> int:
        return len(
            [source for source in self._settings.environment.signal_sources if source.enabled]
        )

    async def start(self) -> None:
        if self._owns_publisher and hasattr(self._publisher, "start"):
            await self._publisher.start()  # type: ignore[attr-defined]
        self._adapters = self._build_adapters()
        await self._publish_runtime_started()
        for adapter in self._adapters:
            self._tasks.append(
                asyncio.create_task(
                    self._poll_loop(adapter),
                    name=f"environment_signal:{adapter.source_id}",
                )
            )

    async def wait(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._owns_publisher and hasattr(self._publisher, "stop"):
            await self._publisher.stop()  # type: ignore[attr-defined]

    async def collect_once(self) -> int:
        """Collect once from every enabled source. Intended for tests and probes."""
        if not self._adapters:
            self._adapters = self._build_adapters()
        published = 0
        for adapter in self._adapters:
            published += await self._collect_adapter_once(adapter)
        return published

    def _build_adapters(self) -> list[SignalAdapter]:
        adapters: list[SignalAdapter] = []
        for source in self._settings.environment.signal_sources:
            if not source.enabled:
                continue
            if not source.adapter:
                logger.warning("environment_signals: source %s has no adapter", source.id)
                continue
            adapters.append(self._build_adapter(source))
        return adapters

    def _build_adapter(self, source: SignalSourceConfig) -> SignalAdapter:
        cls = _import_class(source.adapter)
        kwargs = _inject_secrets(source.kwargs, source.secret_kwargs_env)
        kwargs.setdefault("environment", self._environment)
        kwargs.setdefault("source_id", source.id)
        adapter = cls(**kwargs)
        if not isinstance(adapter, SignalAdapter):
            raise TypeError(f"{source.adapter!r} does not implement SignalAdapter")
        return adapter

    async def _poll_loop(self, adapter: SignalAdapter) -> None:
        interval = self._settings.environment.signal_poll_interval_seconds
        while True:
            try:
                await self._collect_adapter_once(adapter)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "environment_signals: source=%s collect failed: %s",
                    adapter.source_id,
                    exc,
                )
                await self._publish_signal_poll_failed(adapter, exc)
            await asyncio.sleep(interval)

    async def _collect_adapter_once(self, adapter: SignalAdapter) -> int:
        started = perf_counter()
        collected = await adapter.collect()
        signals = [signal for signal in collected if self._remember(signal)]
        events: list[SleipnirEvent] = []
        enqueued_count = 0
        events = [
            signal.to_event(
                source=f"adapter:{adapter.source_id}",
                environment=self._environment,
                tenant_id=self._environment.tenant_id,
            )
            for signal in signals
        ]
        if events:
            await self._publisher.publish_batch(events)
        resident_results = await self._process_resident_learning(events)
        if self._enqueue is not None:
            for signal, event, resident_result in zip(
                signals,
                events,
                resident_results,
                strict=True,
            ):
                if signal.severity in self._settings.environment.signal_task_severities:
                    await self._enqueue(
                        self._task_from_signal(
                            signal,
                            event,
                            resident_learning_result=resident_result,
                        )
                    )
                    enqueued_count += 1
        duration_ms = int((perf_counter() - started) * 1000)
        await self._publish_signal_poll_completed(
            adapter,
            collected=collected,
            published_events=events,
            resident_results=resident_results,
            enqueued_count=enqueued_count,
            duration_ms=duration_ms,
        )
        if not events:
            return 0
        logger.info(
            "environment_signals: source=%s published=%d environment=%s",
            adapter.source_id,
            len(events),
            self._environment.id,
        )
        return len(events)

    def _remember(self, signal: NormalizedSignal) -> bool:
        key = f"{signal.source_id}:{signal.provider_event_id or signal.dedupe_key}"
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = None
        cache_size = self._settings.environment.signal_dedupe_cache_size
        while len(self._seen) > cache_size:
            self._seen.popitem(last=False)
        return True

    async def _process_resident_learning(
        self,
        events: list[SleipnirEvent],
    ) -> list[dict[str, Any] | None]:
        if self._resident_signal_processor is None:
            return [None for _ in events]
        results: list[dict[str, Any] | None] = []
        for event in events:
            try:
                results.append(await self._resident_signal_processor(event))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "environment_signals: resident learning failed for event=%s: %s",
                    event.event_id,
                    exc,
                )
                await self._publish_resident_learning_failed(event, exc)
                results.append(
                    {
                        "usedAdoptedLearning": False,
                        "decision": "resident_learning_failed",
                        "error": str(exc),
                    }
                )
        return results

    def _task_from_signal(
        self,
        signal: NormalizedSignal,
        event: SleipnirEvent,
        *,
        resident_learning_result: dict[str, Any] | None = None,
    ) -> AgentTask:
        title = f"{signal.signal_type} signal: {signal.severity}"
        obj = signal.object_ref or {}
        if obj.get("kind") and obj.get("name"):
            title = (
                f"{obj['kind']} {obj.get('namespace', 'default')}/{obj['name']}: {signal.severity}"
            )
        peer_id = self._settings.mesh.own_peer_id or "unknown"
        resident_name = self._resident_name()
        resident_personality = self._resident_personality()
        signal_ref = event.event_id or signal.provider_event_id or signal.dedupe_key

        resident_lines = [
            f"- **Valkyrie:** {resident_name}",
            f"- **Peer id:** `{peer_id}`",
        ]
        if resident_personality:
            resident_lines.append(f"- **Personality:** {resident_personality}")

        # Markdown section the agent reads about what resident learning already found.
        learning_section = ""
        closing_section = ""
        if resident_learning_result:
            decision = resident_learning_result.get("decision")
            capability = resident_learning_result.get("capabilityName")
            if resident_learning_result.get("usedAdoptedLearning"):
                learning_section = (
                    "\n## Resident learning\n\n"
                    f"- **Decision:** `{decision}`\n"
                    f"- **Skill:** `{resident_learning_result.get('skillName')}`\n"
                    f"- **Capability:** `{capability}`\n\n"
                    "Use the adopted skill context before proposing new tooling.\n"
                )
            elif decision == "defer_to_investigation_with_build_tool":
                learning_section = (
                    "\n## Resident learning\n\n"
                    f"- **Decision:** `{decision}`\n"
                    f"- **Capability:** `{capability}`\n"
                )
                closing_section = (
                    "\n## Required before you finish\n\n"
                    f"No installed instrument matches capability `{capability}`. "
                    "Decide for yourself which available skill or tool should handle "
                    "the signal. If no suitable capability exists, use `build_tool`: "
                    "author the `tool_code` yourself, or pass your spec as "
                    "`build_request` so the configured builder can produce it.\n\n"
                    "Guardrails, not recipes:\n\n"
                    "- Declare the tool's **real reach** — review gates on it, and "
                    "mutating reach is held for an operator.\n"
                    "- Never hardcode environment values or thresholds; take them "
                    "as `input_schema` parameters and fetch live state.\n"
                    "- When access is missing, return a clear error object instead "
                    "of raising, so the canary still passes in restricted "
                    "environments.\n\n"
                    "When you build or use a capability, cite the result in the "
                    "outcome. If the tool is held for operator review, say so in "
                    "the rationale.\n"
                )
            elif decision:
                learning_section = (
                    "\n## Resident learning\n\n"
                    f"- **Decision:** `{decision}`\n"
                    f"- **Capability:** `{capability}`\n"
                )

        workflow_section = ""
        if self._workflow_capability_sources_enabled():
            workflow_section = (
                "\n## Remote workflows\n\n"
                "Remote workflows are available as ordinary tools. Use "
                "`workflow_list` to inspect the catalog and `workflow_launch` only "
                "when the task genuinely needs a workflow-backed build, research, "
                "or operations run. Treat them like any other tool: choose based "
                "on the current task, not on a hidden signal policy.\n"
            )

        payload_json = json.dumps(signal.normalized_payload, indent=2, sort_keys=True, default=str)
        object_json = json.dumps(signal.object_ref, sort_keys=True, default=str)
        # The literal block the agent must reproduce. Kept inside a fenced block
        # so the response parser's ---outcome---/---end--- contract is preserved
        # while the ticket renders it as a clean code block, not a stray card.
        outcome_template = (
            "---outcome---\n"
            "decision: watch\n"
            f"environment_id: {signal.environment_id}\n"
            f"environment_type: {signal.environment_type}\n"
            f"valkyrie_id: {peer_id}\n"
            "signal_refs:\n"
            f"  - {signal_ref}\n"
            "tier: ambient\n"
            "confidence: 0.5\n"
            "operational_state: watching\n"
            "wakefulness: wakeful\n"
            "rationale: concise reason grounded in the signal\n"
            "evidence:\n"
            f"  - event_id: {signal_ref}\n"
            f"    source_id: {signal.source_id}\n"
            f"    severity: {signal.severity}\n"
            "recommended_action: what to do next, or none\n"
            "action_authority: autonomous\n"
            "action_capability: none\n"
            "target_surfaces: []\n"
            'expires_at: ""\n'
            "dissent_refs: []\n"
            "correlation_ids:\n"
            f"  root: {event.correlation_id or signal.correlation_id or ''}\n"
            f"  signal: {signal_ref}\n"
            f"  environment: {signal.environment_id}\n"
            "---end---"
        )
        resident_block = "\n".join(resident_lines)
        context = (
            f"# Signal investigation — {title}\n\n"
            "A resident Valkyrie received an environment signal and must decide "
            "what to do with it.\n\n"
            "## Resident\n\n"
            f"{resident_block}\n\n"
            "## Signal\n\n"
            f"- **Environment:** {signal.environment_id} (`{signal.environment_type}`)\n"
            f"- **Source:** `{signal.source_id}`\n"
            f"- **Type:** `{signal.signal_type}`\n"
            f"- **Severity:** **{signal.severity}**\n"
            f"- **Object:** `{object_json}`\n\n"
            "```json\n"
            f"{payload_json}\n"
            "```\n"
            f"{learning_section}\n"
            f"{workflow_section}\n"
            "## Your task\n\n"
            "Decide whether this is noise, needs a **watching** state, or "
            "**requires action**. Use existing tools, memory, and resident "
            "skills/runbooks before proposing new tooling; when `skill_list` and "
            "`skill_run` are available, inspect and load the relevant runbook "
            "first. If the investigation is blocked because the toolbox lacks a "
            "reusable instrument, call `build_tool` with a manifest, declared "
            "reach, and canary input; when a builder backend is configured, pass "
            "a `build_request` and let that backend produce the tool. Then use "
            "the newly registered tool before deciding — or note the review item "
            "when the tool requires approval.\n\n"
            "## Required outcome\n\n"
            "Finish with exactly one `valkyrie.judgment.proposed` block in this "
            "shape — keep the `---outcome---` / `---end---` delimiters, valid YAML "
            "between them, and no prose after it:\n\n"
            "```text\n"
            f"{outcome_template}\n"
            "```\n"
            f"{closing_section}"
        )
        return AgentTask(
            task_id=f"task_{int(datetime.now(UTC).timestamp() * 1000):x}_{uuid.uuid4().hex[:8]}",
            title=title,
            initiative_context=context,
            triggered_by=f"signal:{event.event_type}",
            output_mode=self._output_mode,
            persona=self._persona,
            priority=3 if signal.severity == "critical" else 8,
            root_correlation_id=event.correlation_id or signal.correlation_id,
            workflow_parent_event_id=event.event_id,
        )

    def _resident_name(self) -> str:
        return (
            self._settings.environment.resident_name
            or self._settings.mesh.own_peer_id
            or "Valkyrie"
        )

    def _resident_personality(self) -> str:
        return self._settings.environment.resident_personality.strip()

    def _workflow_capability_sources_enabled(self) -> bool:
        return any(
            source.enabled and source.adapter
            for source in self._settings.environment.capability_sources
        )

    async def _publish_runtime_started(self) -> None:
        sources = [
            {
                "id": adapter.source_id,
                "signal_type": adapter.signal_type,
            }
            for adapter in self._adapters
        ]
        payload = {
            "valkyrie_id": self._settings.mesh.own_peer_id,
            "valkyrie_name": self._resident_name(),
            "resident_personality": self._resident_personality(),
            "environment_id": self._environment.id,
            "source_count": len(sources),
            "sources": sources,
            "poll_interval_seconds": self._settings.environment.signal_poll_interval_seconds,
            "signal_task_severities": list(self._settings.environment.signal_task_severities),
            "drive_loop_enabled": self._enqueue is not None,
            "initiative_enabled": self._settings.initiative.enabled,
            "llm_model": self._settings.effective_model(),
            "reflection_model": self._settings.effective_memory_reflection_model(),
            "post_session_reflection_enabled": self._settings.reflection.enabled,
            "post_session_reflection_model": (
                self._settings.effective_post_session_reflection_config().llm_alias
            ),
        }
        event = SleipnirEvent(
            event_type="valkyrie.runtime.started",
            source=f"ravn:valkyrie:{self._environment.id}",
            payload=payload,
            summary=(
                f"Valkyrie runtime started in {self._environment.id} "
                f"with {len(sources)} signal source(s)"
            ),
            urgency=0.2,
            domain="infrastructure",
            timestamp=SleipnirEvent.now(),
            tenant_id=self._environment.tenant_id,
        )
        await self._publish_telemetry_event(event)

    async def _publish_signal_poll_completed(
        self,
        adapter: SignalAdapter,
        *,
            collected: list[NormalizedSignal],
            published_events: list[SleipnirEvent],
            resident_results: list[dict[str, Any] | None],
            enqueued_count: int,
            duration_ms: int,
    ) -> None:
        severity_counts: dict[str, int] = {}
        for signal in collected:
            severity_counts[signal.severity] = severity_counts.get(signal.severity, 0) + 1
        published_signal_ids = [event.event_id for event in published_events]
        duplicate_count = len(collected) - len(published_events)
        resident_checked_count = sum(result is not None for result in resident_results)
        resident_used_count = sum(
            1
            for result in resident_results
            if result is not None and result.get("usedAdoptedLearning") is True
        )
        payload = {
            "valkyrie_id": self._settings.mesh.own_peer_id,
            "valkyrie_name": self._resident_name(),
            "environment_id": self._environment.id,
            "source_id": adapter.source_id,
            "signal_type": adapter.signal_type,
            "collected_count": len(collected),
            "new_count": len(published_events),
            "duplicate_count": duplicate_count,
            "published_count": len(published_events),
            "enqueued_task_count": enqueued_count,
            "resident_learning_checked_count": resident_checked_count,
            "resident_learning_used_count": resident_used_count,
            "workflow_capability_source_count": len(
                [
                    source
                    for source in self._settings.environment.capability_sources
                    if source.enabled and source.adapter
                ]
            ),
            "drive_loop_enabled": self._enqueue is not None,
            "duration_ms": duration_ms,
            "severity_counts": severity_counts,
            "published_signal_event_ids": published_signal_ids[:25],
            "truncated_signal_event_ids": max(0, len(published_signal_ids) - 25),
        }
        event = SleipnirEvent(
            event_type="valkyrie.signal_poll.completed",
            source=f"ravn:valkyrie:{self._environment.id}",
            payload=payload,
            summary=(
                f"{adapter.source_id} poll collected {len(collected)} signal(s), "
                f"published {len(published_events)}, enqueued {enqueued_count}"
            ),
            urgency=0.4 if published_events else 0.1,
            domain="infrastructure",
            timestamp=SleipnirEvent.now(),
            tenant_id=self._environment.tenant_id,
        )
        await self._publish_telemetry_event(event)

    async def _publish_resident_learning_failed(
        self,
        signal_event: SleipnirEvent,
        exc: Exception,
    ) -> None:
        event = SleipnirEvent(
            event_type="valkyrie.resident_learning.failed",
            source=f"ravn:valkyrie:{self._environment.id}",
            payload={
                "valkyrie_id": self._settings.mesh.own_peer_id,
                "valkyrie_name": self._resident_name(),
                "environment_id": self._environment.id,
                "signal_event_id": signal_event.event_id,
                "signal_event_type": signal_event.event_type,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            summary=f"Resident learning failed for signal {signal_event.event_id}: {exc}",
            urgency=0.7,
            domain="infrastructure",
            timestamp=SleipnirEvent.now(),
            tenant_id=self._environment.tenant_id,
            correlation_id=signal_event.correlation_id,
            causation_id=signal_event.event_id,
        )
        await self._publish_telemetry_event(event)

    async def _publish_signal_poll_failed(
        self,
        adapter: SignalAdapter,
        exc: Exception,
    ) -> None:
        event = SleipnirEvent(
            event_type="valkyrie.signal_poll.failed",
            source=f"ravn:valkyrie:{self._environment.id}",
            payload={
                "valkyrie_id": self._settings.mesh.own_peer_id,
                "valkyrie_name": self._resident_name(),
                "environment_id": self._environment.id,
                "source_id": adapter.source_id,
                "signal_type": adapter.signal_type,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            summary=f"{adapter.source_id} poll failed: {exc}",
            urgency=0.8,
            domain="infrastructure",
            timestamp=SleipnirEvent.now(),
            tenant_id=self._environment.tenant_id,
        )
        await self._publish_telemetry_event(event)

    async def _publish_telemetry_event(self, event: SleipnirEvent) -> None:
        apply_environment_metadata(event, self._environment)
        try:
            await self._publisher.publish(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "environment_signals: failed to publish telemetry event %s: %s",
                event.event_type,
                exc,
            )
