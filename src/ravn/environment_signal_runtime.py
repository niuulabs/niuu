"""Runtime wiring for Environment signal sources."""

from __future__ import annotations

import asyncio
import hashlib
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

from niuu.observability import get_observability
from ravn.config import Settings, SignalSourceConfig
from ravn.domain.environment import (
    Environment,
    FlockMembership,
    SignalSource,
    TopologyRef,
    apply_environment_metadata,
)
from ravn.domain.models import AgentTask, OutputMode
from ravn.domain.valkyrie_contracts import resident_outcome_template
from ravn.ports.signal_adapter import NormalizedSignal, SignalAdapter
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

logger = logging.getLogger(__name__)

EnqueueFn = Callable[[AgentTask], Awaitable[bool | None]]
ResidentSignalFn = Callable[[SleipnirEvent], Awaitable[dict[str, Any] | None]]


def _import_class(dotted_path: str) -> type:
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


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
    configured_topology = cfg.topology
    type_id = configured_topology.type_id or cfg.type
    topology = TopologyRef(
        node_id=configured_topology.node_id or f"{type_id}:{cfg.id}",
        type_id=type_id,
        parent_id=configured_topology.parent_id,
        realm_id=configured_topology.realm_id or cfg.id,
        cluster_id=configured_topology.cluster_id,
        host_id=configured_topology.host_id,
        zone=configured_topology.zone,
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
        durable_home_enabled: bool = False,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._enqueue = enqueue
        self._resident_signal_processor = resident_signal_processor
        self._persona = persona
        self._output_mode = output_mode
        self._owns_publisher = owns_publisher
        self._durable_home_enabled = durable_home_enabled
        self._environment = build_runtime_environment(settings)
        self._adapters: list[SignalAdapter] = []
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._tasks: list[asyncio.Task] = []
        self._untriaged: list[dict[str, Any]] = []
        self._last_idle_poll_event_at: dict[str, float] = {}

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
        triage_interval = self._settings.environment.idle_triage_interval_seconds
        if self._enqueue is not None and triage_interval > 0 and not self._durable_home_enabled:
            self._tasks.append(
                asyncio.create_task(
                    self._idle_triage_loop(triage_interval),
                    name=f"environment_triage:{self._environment.id}",
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
        telemetry = get_observability()
        attributes = {
            "ravn.environment.id": self._environment.id,
            "ravn.environment.type": self._environment.type,
            "ravn.signal.source": adapter.source_id,
            "ravn.signal.type": adapter.signal_type,
            "ravn.signal.durable": adapter.requires_commit,
        }
        started = perf_counter()
        with telemetry.span("ravn.environment.collect", attributes=attributes) as span:
            try:
                count = await self._collect_adapter_once_observed(adapter)
                span.set_attribute("ravn.signal.accepted_count", count)
                span.set_attribute("ravn.signal.poll.outcome", "success")
                return count
            except BaseException as exc:
                span.set_attribute("ravn.signal.poll.outcome", "error")
                telemetry.mark_error(span, type(exc).__name__)
                raise
            finally:
                telemetry.duration(
                    "ravn.environment.signal_poll.duration",
                    perf_counter() - started,
                    attributes=attributes,
                    description="Duration of one environment signal-source poll.",
                )

    async def _collect_adapter_once_observed(self, adapter: SignalAdapter) -> int:
        telemetry = get_observability()
        started = perf_counter()
        with telemetry.span(
            "ravn.signal.adapter.collect",
            attributes={"ravn.signal.source": adapter.source_id},
        ) as collect_span:
            collected = await adapter.collect()
            collect_span.set_attribute("ravn.signal.collected_count", len(collected))
            telemetry.event(
                "ravn.signal.collected",
                attributes={"ravn.signal.collected_count": len(collected)},
                content=[signal.model_dump(mode="json") for signal in collected],
            )
        with telemetry.span("ravn.signal.normalize") as normalize_span:
            signals = [signal for signal in collected if not self._is_seen(signal)]
            events = [
                signal.to_event(
                    source=f"adapter:{adapter.source_id}",
                    environment=self._environment,
                    tenant_id=self._environment.tenant_id,
                )
                for signal in signals
            ]
            normalize_span.set_attribute("ravn.signal.new_count", len(signals))
            normalize_span.set_attribute(
                "ravn.signal.duplicate_count",
                len(collected) - len(signals),
            )
            telemetry.event(
                "ravn.signal.normalized",
                attributes={
                    "ravn.signal.new_count": len(signals),
                    "ravn.signal.duplicate_count": len(collected) - len(signals),
                    "ravn.signal.refs": [event.event_id for event in events],
                },
                content=[event.payload for event in events],
            )
        enqueued_count = 0
        resident_results: list[dict[str, Any] | None] = []
        try:
            if events:
                with telemetry.span(
                    "ravn.signal.publish",
                    attributes={"ravn.signal.event_count": len(events)},
                ):
                    await self._publisher.publish_batch(events)
            with telemetry.span("ravn.signal.resident_projection") as projection_span:
                resident_results = await self._process_resident_learning(
                    events,
                    fail_on_error=adapter.requires_commit and self._durable_home_enabled,
                )
                projection_span.set_attribute(
                    "ravn.signal.resident_projection.required",
                    adapter.requires_commit and self._durable_home_enabled,
                )
            with telemetry.span("ravn.signal.enqueue") as enqueue_span:
                enqueued_count = await self._enqueue_signals(
                    adapter,
                    signals,
                    events,
                    resident_results,
                )
                enqueue_span.set_attribute("ravn.signal.enqueued_count", enqueued_count)
            with telemetry.span("ravn.signal.commit"):
                await adapter.commit()
        except BaseException as exc:
            try:
                with telemetry.span("ravn.signal.rollback"):
                    await adapter.rollback()
            except Exception as rollback_exc:
                telemetry.event(
                    "ravn.signal.rollback.failed",
                    attributes={"error.type": type(rollback_exc).__name__},
                )
                logger.warning(
                    "environment_signals: source=%s rollback failed",
                    adapter.source_id,
                    exc_info=True,
                )
            telemetry.event(
                "ravn.signal.processing.failed",
                attributes={"error.type": type(exc).__name__},
                content=str(exc),
            )
            raise
        for signal in signals:
            self._remember(signal)
            get_observability().count(
                "ravn.environment.signals",
                attributes={
                    "ravn.environment.id": signal.environment_id,
                    "ravn.environment.type": signal.environment_type,
                    "ravn.signal.source": signal.source_id,
                    "ravn.signal.type": signal.signal_type,
                    "ravn.signal.severity": signal.severity,
                },
                description="Environment signals accepted for resident judgment.",
            )
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

    async def _enqueue_signals(
        self,
        adapter: SignalAdapter,
        signals: list[NormalizedSignal],
        events: list[SleipnirEvent],
        resident_results: list[dict[str, Any] | None],
    ) -> int:
        if not signals:
            return 0
        if adapter.requires_commit:
            if self._durable_home_enabled:
                self._require_inbox_persistence(events, resident_results)
                # ResidentHomeTrigger is the sole queue producer for durable inbox
                # observations. It coalesces NEW records and keeps intake independent
                # of model latency.
                return 0
            if self._enqueue is None:
                raise RuntimeError("durable signal transport requires a resident task queue")
            batch = [
                self._window_entry(signal, event)
                for signal, event in zip(signals, events, strict=True)
            ]
            accepted = await self._enqueue(self._triage_task(batch, durable=True))
            if accepted is False:
                raise RuntimeError("resident task queue rejected durable signal window")
            return 1
        if self._enqueue is None:
            return 0
        enqueued = 0
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
                enqueued += 1
            elif not self._durable_home_enabled:
                self._remember_untriaged(signal, event)
        return enqueued

    def _require_inbox_persistence(
        self,
        events: list[SleipnirEvent],
        resident_results: list[dict[str, Any] | None],
    ) -> None:
        """Fail loudly when the inbox did not durably take every observation.

        With the inbox as the sole queue producer, an observation that failed to
        land there would simply never be judged. Raising keeps the signal out of
        the seen-cache, so a polling source re-collects it on the next pass and
        an acking source redelivers it.
        """
        if len(resident_results) == len(events) and all(
            result
            and result.get("residentAutonomySignalPersisted") is True
            and str(result.get("residentAutonomySignalRef") or "").strip()
            for result in resident_results
        ):
            return
        raise RuntimeError("durable signal transport requires every event in the resident inbox")

    def _remember_untriaged(self, signal: NormalizedSignal, event: SleipnirEvent) -> None:
        self._untriaged.append(self._window_entry(signal, event))
        max_signals = self._settings.environment.idle_triage_max_signals
        if len(self._untriaged) > max_signals:
            self._untriaged = self._untriaged[-max_signals:]

    async def _idle_triage_loop(self, interval: float) -> None:
        """Periodically give the resident a bounded window of accumulated signals."""
        if self._enqueue is None:
            return
        while True:
            await asyncio.sleep(interval)
            batch, self._untriaged = self._untriaged, []
            if not batch:
                continue
            try:
                await self._enqueue(self._triage_task(batch))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "environment_signals: idle triage enqueue failed: %s",
                    exc,
                )

    def _triage_task(self, batch: list[dict[str, Any]], *, durable: bool = False) -> AgentTask:
        env_cfg = self._settings.environment
        severity_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        for entry in batch:
            severity_counts[entry["severity"]] = severity_counts.get(entry["severity"], 0) + 1
            source_counts[entry["source_id"]] = source_counts.get(entry["source_id"], 0) + 1
        # The prompt stays bounded no matter how large the batch is: a capped
        # number of truncated sample lines plus an explicit overflow line; the
        # severity breakdown is what summarizes the whole batch (NIU-1118).
        sample_count = max(0, env_cfg.idle_triage_sample_signals)
        summary_max_chars = max(1, env_cfg.idle_triage_sample_summary_max_chars)
        sample_lines = "\n".join(
            f"- `{entry['signal_type']}` ({entry['severity']}, {entry['source_id']}): "
            f"observed_at={entry.get('observed_at', 'unknown')} payload="
            f"{_truncate(str(entry.get('payload', entry.get('summary', ''))), summary_max_chars)}"
            for entry in batch[:sample_count]
        )
        overflow = len(batch) - sample_count
        if overflow > 0:
            sample_lines += (
                f"\n- …and {overflow} more signal(s) not shown; "
                "the severity breakdown above covers the full batch."
            )
        severity_lines = "\n".join(
            f"- **{severity}**: {count}" for severity, count in sorted(severity_counts.items())
        )
        outcome_template = resident_outcome_template(
            signal_refs=[
                entry["signal_ref"] for entry in batch[: env_cfg.idle_triage_max_signal_refs]
            ],
            evidence_lines=["  - <evidence reference>"],
        )
        charter_section = ""
        charter = self._settings.environment.charter.strip()
        if charter:
            charter_section = f"\n## Charter\n\n{charter}\n"
        context = (
            f"# Signals since your last look — {len(batch)} observation(s)\n\n"
            "These observations are presented without a predetermined interpretation. "
            "Source severity labels and counts are context, not a decision.\n"
            f"{charter_section}\n"
            "## Severity breakdown\n\n"
            f"{severity_lines}\n\n"
            "## Observed signals (bounded payload excerpts)\n\n"
            f"{sample_lines}\n\n"
            "## Your task\n\n"
            "Decide what, if anything, these observations mean in this environment. "
            "Gather evidence with the available capabilities when that would improve "
            "the judgment, and ask the operator when their knowledge, intent, or "
            "authority is the best available way to progress. It is valid to conclude "
            "that no action is needed; "
            "ground whichever conclusion you reach in the observations.\n\n"
            "## Required outcome\n\n"
            "Finish with exactly one `valkyrie.judgment.proposed` block:\n\n"
            "```text\n"
            f"{outcome_template}\n"
            "```\n"
        )
        digest = hashlib.sha256(
            "\n".join(sorted(str(entry["signal_ref"]) for entry in batch)).encode()
        ).hexdigest()[:16]
        return AgentTask(
            task_id=f"task_{int(datetime.now(UTC).timestamp() * 1000):x}_{uuid.uuid4().hex[:8]}",
            title=(
                f"Resident signal window: {len(batch)} observation(s)"
                if durable
                else f"Signal window: {len(batch)} observation(s)"
            ),
            initiative_context=context,
            triggered_by="signal:durable_window" if durable else "signal:idle_triage",
            output_mode=self._output_mode,
            persona=self._persona,
            priority=9,
            root_correlation_id=f"signal-window:{self._environment.id}:{digest}",
            trace_context=get_observability().inject(),
        )

    @staticmethod
    def _window_entry(signal: NormalizedSignal, event: SleipnirEvent) -> dict[str, Any]:
        payload = getattr(signal, "normalized_payload", None)
        if payload is None:
            payload = event.summary
        return {
            "signal_ref": event.event_id or signal.provider_event_id or signal.dedupe_key,
            "signal_type": signal.signal_type,
            "severity": signal.severity,
            "source_id": signal.source_id,
            "observed_at": getattr(signal, "timestamp", event.timestamp).isoformat(),
            "payload": json.dumps(payload, sort_keys=True, default=str),
        }

    def _is_seen(self, signal: NormalizedSignal) -> bool:
        return self._signal_key(signal) in self._seen

    def _remember(self, signal: NormalizedSignal) -> bool:
        key = self._signal_key(signal)
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = None
        cache_size = self._settings.environment.signal_dedupe_cache_size
        while len(self._seen) > cache_size:
            self._seen.popitem(last=False)
        return True

    @staticmethod
    def _signal_key(signal: NormalizedSignal) -> str:
        return f"{signal.source_id}:{signal.provider_event_id or signal.dedupe_key}"

    async def _process_resident_learning(
        self,
        events: list[SleipnirEvent],
        *,
        fail_on_error: bool = False,
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
                if fail_on_error:
                    raise
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
        charter = self._settings.environment.charter.strip()
        if charter:
            resident_lines.append(f"- **Charter:** {charter}")

        # Cheap deterministic lookup is retrieval evidence for the model, never
        # a judgment or a mandatory route.
        learning_section = ""
        if resident_learning_result:
            lookup_hint = {
                key: value
                for key, value in resident_learning_result.items()
                if not key.startswith("residentAutonomy")
            }
            if lookup_hint:
                learning_section = (
                    "\n## Capability lookup hints\n\n"
                    "```json\n"
                    f"{json.dumps(lookup_hint, indent=2, sort_keys=True, default=str)}\n"
                    "```\n\n"
                    "This is a cheap catalog hint, not a prior judgment and not evidence "
                    "that a capability was executed. Compare it with the signal and the "
                    "live catalog before choosing what to do.\n"
                )

        workflow_section = ""
        if self._workflow_capability_sources_enabled():
            workflow_section = (
                "\n## Remote workflows\n\n"
                "Remote workflows are available as ordinary tools. Use "
                "`workflow_list` to inspect the catalog and `workflow_launch` only "
                "when the task genuinely needs a workflow-backed build, research, "
                "or operations run. Treat them like any other tool: choose based "
                "on the current task, not on a hidden signal policy. After launching, "
                "do not treat the launch receipt as the outcome; use `workflow_status` "
                "and then `workflow_events`, `workflow_artifacts`, or "
                "`workflow_artifact_read` to inspect what Ting reports.\n"
                "Peer Agent Card skills appear in `capability_list` as `agent_skill` "
                "entries and are invoked through `a2a_task`; preserve the returned "
                "agent and task ids when a peer requests input or continues later.\n"
            )

        payload_json = json.dumps(signal.normalized_payload, indent=2, sort_keys=True, default=str)
        object_json = json.dumps(signal.object_ref, sort_keys=True, default=str)
        # The literal block the agent must reproduce. Kept inside a fenced block
        # so the response parser's ---outcome---/---end--- contract is preserved
        # while the ticket renders it as a clean code block, not a stray card.
        outcome_template = resident_outcome_template(
            signal_refs=[signal_ref],
            evidence_lines=[
                f"  - event_id: {signal_ref}",
                f"    source_id: {signal.source_id}",
                f"    severity: {signal.severity}",
            ],
        )
        resident_block = "\n".join(resident_lines)
        context = (
            f"# Environment signal — {title}\n\n"
            "A resident Valkyrie received an observation from its environment. "
            "The signal is context to judge, not a predetermined conclusion or action.\n\n"
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
            "Judge what the signal means in this environment and gather any evidence "
            "needed for a responsible outcome. Available tools and `capability_list` "
            "are options, not a prescribed route. Reuse a suitable capability when "
            "one exists; use `build_tool` only after identifying a genuine capability "
            "gap. Ask the operator when their knowledge, intent, or authority is the "
            "best available way to progress. "
            "Do not claim an action or observation absent from the transcript.\n\n"
            "## Required outcome\n\n"
            "Finish with exactly one `valkyrie.judgment.proposed` block in this "
            "shape — keep the `---outcome---` / `---end---` delimiters, valid YAML "
            "between them, and no prose after it:\n\n"
            "```text\n"
            f"{outcome_template}\n"
            "```\n"
        )
        task_id = f"task_{int(datetime.now(UTC).timestamp() * 1000):x}_{uuid.uuid4().hex[:8]}"
        root_id = event.correlation_id or signal.correlation_id or task_id
        inbox_ref = str(
            (resident_learning_result or {}).get("residentAutonomySignalRef") or ""
        ).strip()
        task = AgentTask(
            task_id=task_id,
            title=title,
            initiative_context=context,
            triggered_by=f"signal:{event.event_type}",
            output_mode=self._output_mode,
            persona=self._persona,
            priority=3 if signal.severity == "critical" else 8,
            root_correlation_id=root_id,
            workflow_parent_event_id=event.event_id,
            resident_case_id=root_id if inbox_ref else "",
            resident_turn_index=1 if inbox_ref else 0,
            resident_started_at=datetime.now(UTC).isoformat() if inbox_ref else "",
            resident_inbox_refs=[inbox_ref] if inbox_ref else [],
            trace_context=get_observability().inject(),
        )
        if inbox_ref:
            task.resident_mandate = task.initiative_context
        return task

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
            "charter": self._settings.environment.charter.strip(),
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
        # Empty and duplicate-only polls are already represented by metrics and
        # spans. Persist them only at a bounded cadence for liveness projections.
        if (
            not published_events
            and not enqueued_count
            and not any(result is not None for result in resident_results)
        ):
            interval = self._settings.environment.signal_idle_poll_event_interval_seconds
            if interval <= 0:
                return
            now = perf_counter()
            previous = self._last_idle_poll_event_at.get(adapter.source_id)
            if previous is not None and now - previous < interval:
                return
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
        self._last_idle_poll_event_at[adapter.source_id] = perf_counter()

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
