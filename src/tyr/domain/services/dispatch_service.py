"""Domain service for dispatch logic — find ready issues and spawn sessions.

Extracted from the API layer so it can be called programmatically by
auto-continue and future consumers without an HTTP request context.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import string
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from sleipnir.domain.catalog import tyr_saga_completed as _catalog_saga_completed
except ImportError:
    _catalog_saga_completed = None  # type: ignore[assignment]

from mimir.registry import MimirRegistryStore
from tyr.domain.flock_merge import build_flock_workload_config
from tyr.domain.models import (
    Phase,
    PhaseStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerProject,
    WorkflowScope,
)
from tyr.domain.templates import BUNDLED_TEMPLATES_DIR, TemplatePhase, load_template
from tyr.domain.utils import _slugify
from tyr.domain.workflow_snapshot import (
    workflow_mimir_from_snapshot,
    workflow_name_from_snapshot,
    workflow_personas_from_snapshot,
)
from tyr.ports.dispatcher_repository import DispatcherRepository
from tyr.ports.event_bus import EventBusPort, TyrEvent
from tyr.ports.flock_flow import FlockFlowProvider
from tyr.ports.saga_repository import SagaRepository
from tyr.ports.tracker import TrackerFactory, TrackerPort
from tyr.ports.volundr import SpawnRequest, VolundrFactory, VolundrPort
from tyr.ports.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

_READY_STATUSES = {"todo", "backlog", "triage"}
_READY_STATUS_TYPES = {"unstarted"}
_ACTIVE_SESSION_STATUSES = {"running", "starting", "creating"}
_COMPLETED_LINEAR_STATES = {"completed", "cancelled"}


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _normalize_mimir_workload_config(
    raw: dict[str, Any] | None = None,
    *,
    hosted_url: str = "",
) -> dict[str, Any]:
    if not raw and not hosted_url:
        return {}

    normalized = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    if hosted_url and not normalized.get("hosted_url"):
        normalized["hosted_url"] = hosted_url
    return normalized


def _resolve_mimir_registry_refs(
    raw: dict[str, Any] | None = None,
    *,
    registry_path: str = "",
) -> dict[str, Any]:
    """Hydrate registry-backed Mimir refs with concrete path/url metadata.

    Workflow snapshots currently preserve registry IDs, mount names, and binding
    metadata. Before dispatching a flock, resolve those IDs against the local
    Mimir registry so Volundr can materialize real mount instances.
    """
    normalized = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    registry_refs = normalized.get("registry_refs")
    if not isinstance(registry_refs, list) or not registry_refs or not registry_path.strip():
        return normalized

    registry_file = Path(registry_path).expanduser()
    store = MimirRegistryStore(registry_file)
    entries = store.list_entries()
    if not entries:
        return normalized

    by_id = {entry.id: entry for entry in entries}
    by_name = {entry.name: entry for entry in entries}

    resolved_refs: list[dict[str, Any]] = []
    for raw_ref in registry_refs:
        if not isinstance(raw_ref, dict):
            continue
        resolved = dict(raw_ref)
        lookup_key = str(
            raw_ref.get("registry_entry_id")
            or raw_ref.get("registryEntryId")
            or raw_ref.get("mount_name")
            or raw_ref.get("mountName")
            or ""
        ).strip()
        entry = by_id.get(lookup_key) or by_name.get(lookup_key)
        if entry is None:
            resolved_refs.append(resolved)
            continue

        if entry.path and not str(resolved.get("path") or "").strip():
            resolved["path"] = entry.path
        if entry.url and not str(resolved.get("url") or "").strip():
            resolved["url"] = entry.url
        if entry.role and not str(resolved.get("role") or "").strip():
            resolved["role"] = entry.role
        if entry.categories and not resolved.get("categories"):
            resolved["categories"] = list(entry.categories)
        if entry.auth_ref and not str(resolved.get("auth_ref") or "").strip():
            resolved["auth_ref"] = entry.auth_ref
        resolved.setdefault("default_read_priority", entry.default_read_priority)
        resolved.setdefault("enabled", entry.enabled)
        resolved_refs.append(resolved)

    normalized["registry_refs"] = resolved_refs
    return normalized


@dataclass(frozen=True)
class QueueItem:
    """An issue ready for dispatch."""

    saga_id: str
    saga_name: str
    saga_slug: str
    repos: list[str]
    feature_branch: str
    phase_name: str
    issue_id: str
    identifier: str
    title: str
    description: str
    status: str
    priority: int = 0
    priority_label: str = ""
    estimate: float | None = None
    url: str = ""
    workflow_id: str | None = None
    workflow: str | None = None
    workflow_version: str | None = None


@dataclass(frozen=True)
class DispatchResult:
    """Result of dispatching a single item."""

    issue_id: str
    session_id: str
    session_name: str
    status: str
    cluster_name: str = ""


@dataclass(frozen=True)
class DispatchItem:
    """A single item to dispatch."""

    saga_id: str
    issue_id: str
    repo: str
    connection_id: str | None = None
    workflow_id: str | None = None
    session_definition: str | None = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def is_ready(
    issue: TrackerIssue,
    active_issue_ids: set[str],
    blocked_identifiers: set[str],
) -> bool:
    """Check if an issue is ready for dispatch."""
    status_name = issue.status.lower()
    status_type = issue.status_type.lower()

    if status_type not in _READY_STATUS_TYPES and status_name not in _READY_STATUSES:
        return False
    if issue.identifier in active_issue_ids:
        return False
    if issue.identifier in blocked_identifiers:
        return False
    return True


def build_prompt(
    issue: TrackerIssue,
    repo: str,
    feature_branch: str,
    template: str = "",
) -> str:
    """Build the initial prompt for a session from a tracker issue.

    Uses the configurable template if provided, otherwise falls back to
    a minimal default.
    """
    raid_branch = issue.identifier.lower()

    if template:
        available = {
            "identifier": issue.identifier,
            "title": issue.title,
            "description": issue.description or "",
            "repo": repo,
            "feature_branch": feature_branch,
            "raid_branch": raid_branch,
        }
        used_fields = {
            fname for _, fname, _, _ in string.Formatter().parse(template) if fname is not None
        }
        return template.format(**{k: v for k, v in available.items() if k in used_fields})

    # Minimal fallback when no template is configured.
    parts = [
        f"# Task: {issue.identifier} — {issue.title}",
        "",
        issue.description or "",
        "",
        f"Repository: {repo}",
        f"Feature branch: {feature_branch}",
        f"Create a working branch: `{raid_branch}`",
        "",
        "Implement the task, write tests, create a PR against"
        f" `{feature_branch}`, and ensure CI passes.",
    ]
    return "\n".join(parts)


def build_flock_prompt(
    issue: TrackerIssue,
    repo: str,
    feature_branch: str,
    mimir_hosted_url: str = "",
) -> str:
    """Build the coordinator's initiative_context for a flock session.

    Includes raid title, description, saga context (repo, branch), and an
    optional note that Mimir is available for prior knowledge queries.
    """
    parts = [
        f"# Raid: {issue.identifier} — {issue.title}",
        "",
        issue.description or "",
        "",
        f"Repository: {repo}",
        f"Feature branch: {feature_branch}",
    ]

    if mimir_hosted_url:
        parts += [
            "",
            f"Prior knowledge is available via Mimir at: {mimir_hosted_url}",
            "Query it for relevant context about this repository and area before starting.",
        ]

    parts += [
        "",
        (
            "Decompose this raid into implementation tasks, delegate to the developer peer"
            + " (coder), collect results, delegate review to the reviewer peer,"
            + " iterate until acceptance criteria are met, then publish your final outcome."
        ),
    ]
    return "\n".join(parts)


def _format_persona_label(persona: dict) -> str:
    """Return a log-friendly label for one persona dict.

    Examples:
      ``coordinator`` → ``coordinator(inherit)``
      ``reviewer`` with ``llm.primary_alias=powerful, thinking_enabled=True``
        → ``reviewer(powerful/thinking)``
      ``security-auditor`` with ``llm.primary_alias=balanced``
        → ``security-auditor(balanced)``
    """
    name = persona.get("name", "?")
    llm = persona.get("llm", {})
    alias = llm.get("primary_alias", "")
    thinking = llm.get("thinking_enabled", False)
    if not alias:
        return f"{name}(inherit)"
    suffix = "/thinking" if thinking else ""
    return f"{name}({alias}{suffix})"


def _snapshot_hash(personas: list[dict]) -> str:
    """Return a short hash of the persona snapshot for log correlation."""
    raw = json.dumps(personas, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _promote_default_tyr_raid_personas(personas: list[dict]) -> list[dict]:
    """Prefer an explicit raid completion authority for Tyr's default flock path.

    Tyr's historical default was ``coordinator`` + ``reviewer``. For raid
    dispatches we want the completion-authority contract to be intentional, so
    the legacy default pair is upgraded in-memory to ``raid-executor`` +
    ``reviewer`` unless the caller explicitly chose another flow/composition.
    """
    names = [str(persona.get("name") or "") for persona in personas]
    if names != ["coordinator", "reviewer"]:
        return personas

    upgraded = copy.deepcopy(personas)
    upgraded[0]["name"] = "raid-executor"
    return upgraded


def resolve_target_adapter(
    connection_id: str | None,
    adapter_by_name: dict[str, VolundrPort],
    fallback: VolundrPort,
) -> VolundrPort:
    """Resolve the target Volundr adapter for a dispatch item."""
    if not connection_id:
        return fallback
    adapter = adapter_by_name.get(connection_id)
    if adapter is not None:
        return adapter
    return fallback


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class DispatchConfig:
    """Dispatch-related config values needed by the service.

    When *live_flock* is provided, flock fields are read from the live
    settings object so that in-memory API changes take effect immediately.
    When tests construct a ``DispatchConfig`` directly with ``flock_enabled``
    etc., those values are used as-is (no live reference needed).
    """

    default_system_prompt: str = ""
    default_model: str = "claude-sonnet-4-6"
    default_session_definition: str = ""
    dispatch_prompt_template: str = ""
    max_cached_issues: int = 10_000
    templates_dir: Path = BUNDLED_TEMPLATES_DIR
    initial_confidence: float = 0.5
    flock_enabled: bool = False
    flock_default_personas: list[dict] = field(
        default_factory=lambda: [
            {"name": "coordinator"},
            {"name": "coder"},
            {"name": "reviewer", "consumes_event_types": ["review.requested"]},
        ]
    )
    flock_default_workflow_name: str = ""
    flock_mimir_hosted_url: str = ""
    flock_mimir_registry_path: str = "~/.ravn/mimir/.mimir-registry.json"
    flock_sleipnir_publish_urls: list[str] = field(default_factory=list)
    flock_llm_config: dict = field(default_factory=dict)
    live_flock: object | None = field(default=None, repr=False)

    def __getattribute__(self, name: str) -> object:
        live = super().__getattribute__("live_flock")
        if live is None:
            return super().__getattribute__(name)
        if name == "flock_enabled":
            return live.enabled  # type: ignore[union-attr]
        if name == "flock_default_personas":
            return [p.to_dict() for p in live.default_personas]  # type: ignore[union-attr]
        if name == "flock_default_workflow_name":
            return live.default_workflow_name  # type: ignore[union-attr]
        if name == "flock_mimir_hosted_url":
            return live.mimir_hosted_url  # type: ignore[union-attr]
        if name == "flock_mimir_registry_path":
            return live.mimir_registry_path  # type: ignore[union-attr]
        if name == "flock_sleipnir_publish_urls":
            return list(live.sleipnir_publish_urls)  # type: ignore[union-attr]
        if name == "flock_llm_config":
            return dict(live.llm_config)  # type: ignore[union-attr]
        return super().__getattribute__(name)


class DispatchService:
    """Domain service for dispatch operations.

    Encapsulates the business logic for finding ready issues and spawning
    Volundr sessions, independent of any HTTP request context.
    """

    def __init__(
        self,
        tracker_factory: TrackerFactory,
        volundr_factory: VolundrFactory,
        saga_repo: SagaRepository,
        dispatcher_repo: DispatcherRepository,
        config: DispatchConfig,
        sleipnir_publisher: object | None = None,
        event_bus: EventBusPort | None = None,
        flow_provider: FlockFlowProvider | None = None,
        workflow_repo: WorkflowRepository | None = None,
    ) -> None:
        self._tracker_factory = tracker_factory
        self._volundr_factory = volundr_factory
        self._saga_repo = saga_repo
        self._dispatcher_repo = dispatcher_repo
        self._config = config
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sleipnir_publisher = sleipnir_publisher
        self._event_bus = event_bus
        self._flow_provider = flow_provider
        self._workflow_repo = workflow_repo

    async def find_ready_issues(
        self,
        owner_id: str,
        *,
        auth_token: str | None = None,
        saga_tracker_id: str | None = None,
    ) -> list[QueueItem]:
        """Find all dispatchable issues, optionally scoped to one saga."""
        adapters = await self._tracker_factory.for_owner(owner_id)
        volundr = await self._volundr_factory.primary_for_owner(owner_id)
        if volundr is None:
            logger.warning("No Volundr adapter for owner %s, returning empty queue", owner_id)
            return []

        sagas = await self._saga_repo.list_sagas(owner_id=owner_id)
        if saga_tracker_id:
            sagas = [s for s in sagas if s.tracker_id == saga_tracker_id]
        # Only process active sagas — completed/failed ones have no dispatchable work
        active_sagas = [s for s in sagas if s.status == SagaStatus.ACTIVE]
        if not active_sagas:
            return []

        # Get active sessions to exclude already-running issues
        sessions = await volundr.list_sessions(auth_token=auth_token)
        active_issue_ids = {
            s.tracker_issue_id
            for s in sessions
            if s.tracker_issue_id and s.status in _ACTIVE_SESSION_STATUSES
        }

        # Fetch all active sagas in parallel
        saga_results = await asyncio.gather(
            *[
                self._fetch_and_filter_saga(adapters, saga, active_issue_ids)
                for saga in active_sagas
            ]
        )

        queue: list[QueueItem] = []
        for saga, (items, should_complete) in zip(active_sagas, saga_results):
            if should_complete:
                await self._saga_repo.update_saga_status(saga.id, SagaStatus.COMPLETE)
                logger.info(
                    "Auto-archived saga %s — Linear project is completed/cancelled",
                    saga.slug,
                )
                # NIU-582: emit tyr.saga.completed (best-effort)
                if self._sleipnir_publisher is not None and _catalog_saga_completed is not None:
                    try:
                        _event = _catalog_saga_completed(
                            saga_id=str(saga.id),
                            outcome="auto_archived",
                            phases_completed=0,
                            source="tyr:dispatch",
                            correlation_id=str(saga.id),
                        )
                        await self._sleipnir_publisher.publish(_event)
                    except Exception:
                        logger.warning(
                            "Failed to emit tyr.saga.completed; continuing.", exc_info=True
                        )
                continue
            queue.extend(items)

        queue.sort(key=lambda q: (q.priority, q.identifier))
        return queue

    async def dispatch_issues(
        self,
        owner_id: str,
        items: list[DispatchItem],
        *,
        auth_token: str | None = None,
        model: str = "",
        system_prompt: str = "",
        connection_id: str | None = None,
        session_definition: str | None = None,
        persona_overrides: list[dict] | None = None,
    ) -> list[DispatchResult]:
        """Spawn Volundr sessions for the given items."""
        await self._dispatcher_repo.get_or_create(owner_id)

        effective_model = model or self._config.default_model
        effective_prompt = system_prompt or self._config.default_system_prompt
        # session_definition fallback chain: explicit per-item → request-level
        # → config default → None (lets volundr fall through to its own
        # defaultDefinition).
        effective_session_definition = (
            session_definition or self._config.default_session_definition or None
        )

        adapters = await self._tracker_factory.for_owner(owner_id)
        volundr = await self._volundr_factory.primary_for_owner(owner_id)
        if volundr is None:
            logger.error("No Volundr adapter for owner %s, cannot dispatch", owner_id)
            return [
                DispatchResult(
                    issue_id=item.issue_id,
                    session_id="",
                    session_name="",
                    status="failed",
                )
                for item in items
            ]

        # Query Volundr for the user's integration IDs
        integration_ids = await self._fetch_integration_ids(volundr, auth_token, owner_id)

        # Pre-resolve all Volundr adapters for connection_id targeting
        all_volundr = await self._volundr_factory.for_owner(owner_id)
        adapter_by_name: dict[str, VolundrPort] = {}
        for a in all_volundr:
            if a.name:
                adapter_by_name[a.name] = a

        # Build lookups
        sagas = await self._saga_repo.list_sagas(owner_id=owner_id)
        saga_map = {str(s.id): s for s in sagas}
        issue_cache = await self._build_issue_cache(adapters, sagas, self._config.max_cached_issues)

        results: list[DispatchResult] = []
        for item in items:
            saga = saga_map.get(item.saga_id)
            if saga is None:
                logger.warning("Saga not found: %s", item.saga_id)
                continue

            issue = issue_cache.get(item.issue_id)
            if issue is None:
                logger.warning("Issue not found: %s", item.issue_id)
                continue

            target_connection = item.connection_id or connection_id
            target_volundr = resolve_target_adapter(
                target_connection,
                adapter_by_name,
                volundr,
            )
            workflow_snapshot, workflow_error = await self._resolve_workflow_snapshot(item, saga)
            if workflow_error:
                logger.warning(
                    "Workflow resolution failed for issue %s: %s",
                    item.issue_id,
                    workflow_error,
                )
                results.append(
                    DispatchResult(
                        issue_id=item.issue_id,
                        session_id="",
                        session_name="",
                        status="failed",
                    )
                )
                continue

            result = await self._spawn_single(
                target_volundr=target_volundr,
                item=item,
                saga=saga,
                issue=issue,
                adapters=adapters,
                effective_model=effective_model,
                effective_prompt=effective_prompt,
                integration_ids=integration_ids,
                session_definition=item.session_definition or effective_session_definition,
                auth_token=auth_token,
                owner_id=owner_id,
                persona_overrides=persona_overrides,
                workflow_snapshot=workflow_snapshot,
            )
            results.append(result)

        return results

    async def try_auto_continue(
        self,
        owner_id: str,
        saga_tracker_id: str,
    ) -> list[DispatchResult]:
        """Dispatch newly unblocked issues if auto_continue is enabled.

        Called after a raid merges or a phase gate is unlocked. Uses a
        per-owner lock to prevent double-dispatch from concurrent merge
        events.
        """
        async with self._locks[owner_id]:
            state = await self._dispatcher_repo.get_or_create(owner_id)
            if not state.running or not state.auto_continue:
                logger.info(
                    "Auto-continue skipped for owner %s: running=%s, auto_continue=%s",
                    owner_id[:8],
                    state.running,
                    state.auto_continue,
                )
                return []

            adapters = await self._tracker_factory.for_owner(owner_id)
            if not adapters:
                logger.info("Auto-continue skipped for owner %s: no tracker adapters", owner_id[:8])
                return []

            running_raids = await adapters[0].list_raids_by_status(RaidStatus.RUNNING)
            available_slots = state.max_concurrent_raids - len(running_raids)
            if available_slots <= 0:
                logger.info(
                    "Auto-continue skipped for owner %s: no slots (%d running, max %d)",
                    owner_id[:8],
                    len(running_raids),
                    state.max_concurrent_raids,
                )
                return []

            ready = await self.find_ready_issues(owner_id, saga_tracker_id=saga_tracker_id)
            if not ready:
                logger.info(
                    "Auto-continue skipped for owner %s: no ready issues (saga=%s)",
                    owner_id[:8],
                    saga_tracker_id,
                )
                return []

            items = [
                DispatchItem(
                    saga_id=q.saga_id,
                    issue_id=q.issue_id,
                    repo=q.repos[0] if q.repos else "",
                )
                for q in ready[:available_slots]
            ]
            results = await self.dispatch_issues(owner_id, items)
            logger.info(
                "Auto-continue dispatched %d issue(s) for owner %s (saga=%s)",
                len(results),
                owner_id[:8],
                saga_tracker_id,
            )
            return results

    async def create_saga_from_template(
        self,
        template_name: str,
        payload: dict,
        owner_id: str,
        *,
        auto_start: bool = True,
    ) -> str:
        """Create a saga from a YAML template and dispatch Phase 1.

        Loads the named template, substitutes ``{event.*}`` placeholders from
        *payload*, persists the saga + phases + raids, and — when
        *auto_start* is True — spawns Volundr sessions for all raids in the
        first phase.

        :param template_name: Template name without extension (e.g. ``"ship"``).
        :param payload: Key/value pairs substituted into ``{event.field}`` placeholders.
        :param owner_id: Owner for the created saga.
        :param auto_start: Dispatch Phase 1 raids immediately when True.
        :returns: The new saga ID as a string.
        :raises FileNotFoundError: When the template cannot be found.
        :raises ValueError: When the template fails validation.
        """
        template = load_template(template_name, self._config.templates_dir, payload)

        if template.flock_flow and self._flow_provider is not None:
            if self._flow_provider.get(template.flock_flow) is None:
                raise ValueError(
                    f"flock_flow '{template.flock_flow}' not found — "
                    "register the flow before referencing it in a pipeline"
                )

        now = datetime.now(UTC)
        saga_id = uuid.uuid4()
        slug = _slugify(template.name)[:60]

        saga = Saga(
            id=saga_id,
            tracker_id=str(saga_id),
            tracker_type="native",
            slug=slug,
            name=template.name,
            repos=template.repos,
            feature_branch=template.feature_branch,
            base_branch=template.base_branch,
            status=SagaStatus.ACTIVE,
            confidence=self._config.initial_confidence,
            created_at=now,
            owner_id=owner_id,
        )
        await self._saga_repo.save_saga(saga)

        phases_data = []
        for phase_num, tpl_phase in enumerate(template.phases, start=1):
            phase_status = PhaseStatus.ACTIVE if phase_num == 1 else PhaseStatus.PENDING
            phase_id = uuid.uuid4()
            phase = Phase(
                id=phase_id,
                saga_id=saga_id,
                tracker_id=str(phase_id),
                number=phase_num,
                name=tpl_phase.name,
                status=phase_status,
                confidence=self._config.initial_confidence,
            )
            await self._saga_repo.save_phase(phase)

            raids: list[Raid] = []
            for tpl_raid in tpl_phase.raids:
                raid_id = uuid.uuid4()
                raid = Raid(
                    id=raid_id,
                    phase_id=phase_id,
                    tracker_id=str(raid_id),
                    name=tpl_raid.name,
                    description=tpl_raid.description,
                    acceptance_criteria=tpl_raid.acceptance_criteria,
                    declared_files=tpl_raid.declared_files,
                    estimate_hours=tpl_raid.estimate_hours,
                    status=RaidStatus.PENDING,
                    confidence=self._config.initial_confidence,
                    session_id=None,
                    branch=None,
                    chronicle_summary=None,
                    pr_url=None,
                    pr_id=None,
                    retry_count=0,
                    created_at=now,
                    updated_at=now,
                )
                await self._saga_repo.save_raid(raid)
                raids.append(raid)
            phases_data.append((phase, raids, tpl_phase))

        if self._event_bus is not None:
            await self._event_bus.emit(
                TyrEvent(
                    event="saga.created",
                    data={
                        "saga_id": str(saga_id),
                        "saga_name": saga.name,
                        "slug": slug,
                        "template": template_name,
                        "auto_start": auto_start,
                        "owner_id": owner_id,
                    },
                    owner_id=owner_id,
                )
            )

        logger.info(
            "DispatchService: created saga %s from template '%s' (phases=%d)",
            slug,
            template_name,
            len(template.phases),
        )

        if auto_start and phases_data:
            first_phase, first_raids, first_tpl = phases_data[0]
            await self._dispatch_template_phase(
                saga,
                first_phase,
                first_raids,
                first_tpl,
                owner_id,
                flock_flow_name=template.flock_flow or "",
            )

        return str(saga_id)

    async def _dispatch_template_phase(
        self,
        saga: Saga,
        phase: Phase,
        raids: list[Raid],
        tpl_phase: TemplatePhase,
        owner_id: str,
        flock_flow_name: str = "",
    ) -> None:
        """Spawn Volundr sessions for all raids in a template phase.

        When *flock_flow_name* is provided and a matching flow is registered,
        each raid is dispatched as a flock session with the flow's personas.
        Per-raid ``persona_overrides`` from the template YAML are merged onto
        the matching flow persona before dispatch.
        """
        volundr = await self._volundr_factory.primary_for_owner(owner_id)
        if volundr is None:
            logger.error(
                "DispatchService: no Volundr adapter for owner %s, cannot dispatch phase '%s'",
                owner_id,
                phase.name,
            )
            return

        integration_ids = await self._fetch_integration_ids(
            volundr,
            None,
            owner_id,
        )

        repo = saga.repos[0] if saga.repos else ""
        for raid, tpl_raid in zip(raids, tpl_phase.raids):
            session_name = re.sub(r"[^a-z0-9]+", "-", raid.name.lower()).strip("-")[:48]
            workload_config = build_flock_workload_config(
                flock_flow_name,
                tpl_raid,
                self._flow_provider,
                tpl_raid.prompt,
            )
            request = SpawnRequest(
                name=session_name,
                repo=repo,
                branch=saga.feature_branch,
                base_branch=saga.base_branch,
                model=self._config.default_model,
                tracker_issue_id=raid.tracker_id,
                tracker_issue_url="",
                system_prompt=self._config.default_system_prompt,
                initial_prompt=tpl_raid.prompt,
                profile=tpl_raid.persona or None,
                integration_ids=integration_ids,
                workload_type="ravn_flock" if workload_config else "default",
                workload_config=workload_config or {},
            )
            try:
                session = await volundr.spawn_session(request=request)
                updated = Raid(
                    id=raid.id,
                    phase_id=raid.phase_id,
                    tracker_id=raid.tracker_id,
                    name=raid.name,
                    description=raid.description,
                    acceptance_criteria=raid.acceptance_criteria,
                    declared_files=raid.declared_files,
                    estimate_hours=raid.estimate_hours,
                    status=RaidStatus.RUNNING,
                    confidence=raid.confidence,
                    session_id=session.id,
                    branch=raid.branch,
                    chronicle_summary=raid.chronicle_summary,
                    pr_url=raid.pr_url,
                    pr_id=raid.pr_id,
                    retry_count=raid.retry_count,
                    created_at=raid.created_at,
                    updated_at=datetime.now(UTC),
                )
                await self._saga_repo.save_raid(updated)
                logger.info(
                    "DispatchService: dispatched template raid %s → session %s"
                    " (persona=%s, flock=%s)",
                    raid.name,
                    session.id,
                    tpl_raid.persona or "(none)",
                    flock_flow_name or "(none)",
                )
            except Exception:
                logger.exception(
                    "DispatchService: failed to spawn session for template raid %s", raid.name
                )

    # -------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------

    @staticmethod
    async def _fetch_saga_data(
        adapter: TrackerPort, saga: Saga
    ) -> tuple[TrackerProject | None, list, list]:
        """Fetch project, milestones, and issues for a saga from the tracker."""
        if hasattr(adapter, "get_project_full"):
            project, milestones, issues = await adapter.get_project_full(saga.tracker_id)
            return project, milestones, issues
        milestones = await adapter.list_milestones(saga.tracker_id)
        issues = await adapter.list_issues(saga.tracker_id)
        return None, milestones, issues

    async def _fetch_and_filter_saga(
        self,
        adapters: list[TrackerPort],
        saga: Saga,
        active_issue_ids: set[str],
    ) -> tuple[list[QueueItem], bool]:
        """Fetch and filter dispatchable issues for a single saga.

        Returns (queue_items, should_mark_complete). should_mark_complete is True
        when the Linear project is completed/cancelled and the saga should be
        auto-archived.
        """
        phases = await self._saga_repo.get_phases_by_saga(saga.id)
        active_phase_tracker_ids = {
            phase.tracker_id for phase in phases if phase.status == PhaseStatus.ACTIVE
        }
        has_persisted_phases = bool(phases)

        for adapter in adapters:
            try:
                project, milestones, issues = await self._fetch_saga_data(adapter, saga)

                if project is not None and project.status in _COMPLETED_LINEAR_STATES:
                    return [], True

                milestone_names = {m.id: m.name for m in milestones}
                blocked_identifiers = await self._get_blocked_safe(adapter, saga)

                items: list[QueueItem] = []
                for issue in issues:
                    if has_persisted_phases and issue.milestone_id not in active_phase_tracker_ids:
                        continue
                    if not is_ready(issue, active_issue_ids, blocked_identifiers):
                        continue
                    items.append(
                        QueueItem(
                            saga_id=str(saga.id),
                            saga_name=saga.name,
                            saga_slug=saga.slug,
                            repos=saga.repos,
                            feature_branch=saga.feature_branch,
                            phase_name=milestone_names.get(issue.milestone_id or "", "Unassigned"),
                            issue_id=issue.id,
                            identifier=issue.identifier,
                            title=issue.title,
                            description=issue.description,
                            status=issue.status,
                            priority=issue.priority,
                            priority_label=issue.priority_label,
                            estimate=issue.estimate,
                            url=issue.url,
                            workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
                            workflow=workflow_name_from_snapshot(saga.workflow_snapshot),
                            workflow_version=(
                                str(saga.workflow_snapshot.get("version"))
                                if saga.workflow_snapshot
                                and saga.workflow_snapshot.get("version") is not None
                                else None
                            ),
                        )
                    )
                return items, False
            except Exception:
                logger.error("Failed to fetch issues for saga %s", saga.id, exc_info=True)
        return [], False

    @staticmethod
    async def _get_blocked_safe(adapter: TrackerPort, saga: Saga) -> set[str]:
        """Get blocked identifiers, returning empty set on failure."""
        try:
            return await adapter.get_blocked_identifiers(saga.tracker_id)
        except Exception:
            logger.warning(
                "Failed to fetch blocked identifiers for saga %s, skipping dependency filter",
                saga.id,
            )
            return set()

    @staticmethod
    async def _fetch_integration_ids(
        volundr: VolundrPort, auth_token: str | None, owner_id: str
    ) -> list[str]:
        """Fetch integration IDs from Volundr, returning empty on failure."""
        try:
            ids = await volundr.list_integration_ids(auth_token=auth_token)
            logger.info("Fetched %d Volundr integration IDs: %s", len(ids), ids)
            return ids
        except Exception:
            logger.warning(
                "Failed to fetch Volundr integrations for user %s",
                owner_id,
                exc_info=True,
            )
            return []

    @staticmethod
    async def _build_issue_cache(
        adapters: list[TrackerPort], sagas: list[Saga], max_cached_issues: int
    ) -> dict[str, TrackerIssue]:
        """Build a lookup of issue details for prompt generation."""
        issue_cache: dict[str, TrackerIssue] = {}
        for saga in sagas:
            for adapter in adapters:
                try:
                    issues = await adapter.list_issues(saga.tracker_id)
                    for issue in issues:
                        if len(issue_cache) >= max_cached_issues:
                            logger.warning(
                                "Issue cache limit reached (%d), skipping remaining issues",
                                max_cached_issues,
                            )
                            break
                        issue_cache[issue.id] = issue
                    break
                except Exception:
                    logger.warning("Failed to fetch issues for saga %s", saga.id, exc_info=True)
                    continue
        return issue_cache

    def _build_spawn_request(
        self,
        *,
        item: DispatchItem,
        saga: Saga,
        issue: TrackerIssue,
        effective_model: str,
        effective_prompt: str,
        integration_ids: list[str],
        session_definition: str | None = None,
        flock_flow: str = "",
        persona_overrides: list[dict] | None = None,
        workflow_snapshot: dict[str, Any] | None = None,
    ) -> SpawnRequest:
        """Build a SpawnRequest — flock or solo — based on config.

        When *flock_flow* names a registered flow, the flow is resolved via the
        ``FlockFlowProvider`` and **snapshotted** inline into the workload config.
        Per-dispatch *persona_overrides* take precedence over flow-level overrides
        which in turn take precedence over persona defaults.
        """
        session_name = issue.identifier.lower()
        workflow_personas = workflow_personas_from_snapshot(workflow_snapshot)
        use_workflow_flock = bool(workflow_snapshot and workflow_personas)

        if not self._config.flock_enabled and not use_workflow_flock:
            return SpawnRequest(
                name=session_name,
                repo=item.repo,
                branch=saga.feature_branch,
                base_branch=saga.base_branch,
                model=effective_model,
                tracker_issue_id=issue.identifier,
                tracker_issue_url=issue.url,
                system_prompt=effective_prompt,
                initial_prompt=build_prompt(
                    issue,
                    item.repo,
                    saga.feature_branch,
                    template=self._config.dispatch_prompt_template,
                ),
                definition=session_definition,
                workload_config={"workflow": workflow_snapshot} if workflow_snapshot else {},
                integration_ids=integration_ids,
            )

        # Resolve personas — flow snapshot takes precedence over config defaults
        personas = copy.deepcopy(self._config.flock_default_personas)
        flow_name_for_log = ""
        mimir_url = self._config.flock_mimir_hosted_url
        mimir_registry_path = self._config.flock_mimir_registry_path
        sleipnir_urls = list(self._config.flock_sleipnir_publish_urls)
        mimir_cfg = _normalize_mimir_workload_config(hosted_url=mimir_url)
        mesh_transport = "nng"

        if use_workflow_flock:
            personas = copy.deepcopy(workflow_personas)
            flow_name_for_log = str(workflow_snapshot.get("name") or "")
            mimir_cfg = _resolve_mimir_registry_refs(
                _normalize_mimir_workload_config(workflow_mimir_from_snapshot(workflow_snapshot)),
                registry_path=mimir_registry_path,
            )
        else:
            flow = (
                self._flow_provider.get(flock_flow)
                if flock_flow and self._flow_provider
                else None
            )
            if flow is None and flock_flow:
                logger.warning("Flock flow '%s' not found, using default personas", flock_flow)
            if flow is not None:
                flow_name_for_log = flow.name
                personas = [p.to_dict() for p in flow.personas]
                mimir_url = flow.mimir_hosted_url or mimir_url
                mimir_cfg = _resolve_mimir_registry_refs(
                    _normalize_mimir_workload_config(flow.mimir, hosted_url=mimir_url),
                    registry_path=mimir_registry_path,
                )
                mesh_transport = flow.mesh_transport or mesh_transport
                if flow.sleipnir_publish_urls:
                    sleipnir_urls = list(flow.sleipnir_publish_urls)
            else:
                personas = _promote_default_tyr_raid_personas(personas)

        # Apply per-dispatch persona overrides (precedence: dispatch > flow > defaults)
        if persona_overrides:
            override_map = {o["name"]: o for o in persona_overrides}
            merged: list[dict] = []
            for p in personas:
                name = p.get("name", p) if isinstance(p, dict) else p
                if name in override_map:
                    base = dict(p) if isinstance(p, dict) else {"name": p}
                    base.update(override_map.pop(name))
                    merged.append(base)
                else:
                    merged.append(p if isinstance(p, dict) else {"name": p})
            # Append any overrides that aren't already in the list
            for remaining in override_map.values():
                merged.append(remaining)
            personas = merged

        # Snapshot hash for log correlation
        snapshot_hash = _snapshot_hash(personas)
        if flow_name_for_log:
            logger.info(
                "flock dispatch session=%s flow=%s snapshot=%s personas=[%s]",
                _sanitize_log(session_name),
                _sanitize_log(flow_name_for_log),
                _sanitize_log(snapshot_hash),
                ", ".join(_sanitize_log(_format_persona_label(p)) for p in personas),
            )
        else:
            logger.info(
                "flock dispatch session=%s personas=[%s]",
                _sanitize_log(session_name),
                ", ".join(_sanitize_log(_format_persona_label(p)) for p in personas),
            )

        initiative_context = build_flock_prompt(
            issue,
            item.repo,
            saga.feature_branch,
            mimir_hosted_url=mimir_url,
        )
        workload_config: dict = {
            "personas": personas,
            "initiative_context": initiative_context,
            "mesh_transport": mesh_transport,
        }
        if workflow_snapshot:
            workload_config["workflow"] = workflow_snapshot
        if sleipnir_urls:
            workload_config["sleipnir_publish_urls"] = sleipnir_urls
        if mimir_cfg:
            workload_config["mimir"] = mimir_cfg
        if mimir_url:
            workload_config["mimir_hosted_url"] = mimir_url
        if self._config.flock_llm_config:
            workload_config["llm_config"] = self._config.flock_llm_config

        return SpawnRequest(
            name=session_name,
            repo=item.repo,
            branch=saga.feature_branch,
            base_branch=saga.base_branch,
            model=effective_model,
            tracker_issue_id=issue.identifier,
            tracker_issue_url=issue.url,
            system_prompt=effective_prompt,
            initial_prompt=workload_config["initiative_context"],
            definition=session_definition,
            workload_type="ravn_flock",
            workload_config=workload_config,
            integration_ids=integration_ids,
        )

    async def _spawn_single(
        self,
        *,
        target_volundr: VolundrPort,
        item: DispatchItem,
        saga: Saga,
        issue: TrackerIssue,
        adapters: list[TrackerPort],
        effective_model: str,
        effective_prompt: str,
        integration_ids: list[str],
        session_definition: str | None,
        auth_token: str | None,
        owner_id: str,
        persona_overrides: list[dict] | None = None,
        workflow_snapshot: dict[str, Any] | None = None,
    ) -> DispatchResult:
        """Spawn a single session and update raid progress."""
        try:
            request = self._build_spawn_request(
                item=item,
                saga=saga,
                issue=issue,
                effective_model=effective_model,
                effective_prompt=effective_prompt,
                integration_ids=integration_ids,
                session_definition=session_definition,
                persona_overrides=persona_overrides,
                workflow_snapshot=workflow_snapshot,
            )
            session = await target_volundr.spawn_session(
                request=request,
                auth_token=auth_token,
            )

            # Record raid progress and set tracker issue to In Progress
            logger.info(
                "Dispatch: updating %d tracker adapters for issue %s",
                len(adapters),
                issue.id,
            )
            for adapter in adapters:
                adapter_name = type(adapter).__name__
                await adapter.update_raid_progress(
                    issue.id,
                    status=RaidStatus.RUNNING,
                    session_id=session.id,
                    owner_id=owner_id,
                    phase_tracker_id=issue.milestone_id,
                    saga_tracker_id=saga.tracker_id,
                )
                logger.info(
                    "Dispatch: %s.update_raid_progress OK for %s",
                    adapter_name,
                    issue.id,
                )
                try:
                    await adapter.update_raid_state(issue.id, RaidStatus.RUNNING)
                    logger.info(
                        "Dispatch: %s.update_raid_state OK for %s → In Progress",
                        adapter_name,
                        issue.id,
                    )
                except Exception:
                    logger.error(
                        "FAILED: %s.update_raid_state for %s",
                        adapter_name,
                        issue.id,
                        exc_info=True,
                    )

            logger.info("Dispatched %s → session %s", issue.identifier, session.id)
            if self._event_bus is not None:
                await self._event_bus.emit(
                    TyrEvent(
                        event="raid.state_changed",
                        owner_id=owner_id,
                        data={
                            "session_id": session.id,
                            "owner_id": owner_id,
                            "tracker_id": issue.identifier,
                            "url": issue.url,
                            "status": "RUNNING",
                            "pr_id": None,
                            "pr_url": None,
                        },
                    )
                )
            return DispatchResult(
                issue_id=item.issue_id,
                session_id=session.id,
                session_name=session.name,
                status="spawned",
                cluster_name=session.cluster_name,
            )
        except Exception:
            logger.error("Failed to spawn session for %s", issue.identifier, exc_info=True)
            return DispatchResult(
                issue_id=item.issue_id,
                session_id="",
                session_name="",
                status="failed",
            )

    async def _resolve_workflow_snapshot(
        self,
        item: DispatchItem,
        saga: Saga,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if item.workflow_id is None:
            if saga.workflow_snapshot is not None:
                return saga.workflow_snapshot, None
            return await self._resolve_default_workflow_snapshot(saga)

        if self._workflow_repo is None:
            return None, "workflow repository not configured"

        try:
            workflow_uuid = uuid.UUID(item.workflow_id)
        except ValueError:
            return None, f"invalid workflow_id {item.workflow_id!r}"

        workflow = await self._workflow_repo.get_workflow(workflow_uuid)
        if workflow is None:
            return None, f"workflow {item.workflow_id!r} not found"
        if workflow.scope != WorkflowScope.SYSTEM and workflow.owner_id != saga.owner_id:
            return None, f"workflow {item.workflow_id!r} is not visible to this saga owner"
        from tyr.domain.workflow_snapshot import build_workflow_snapshot  # noqa: PLC0415

        return build_workflow_snapshot(workflow), None

    async def _resolve_default_workflow_snapshot(
        self,
        saga: Saga,
    ) -> tuple[dict[str, Any] | None, str | None]:
        workflow_name = str(self._config.flock_default_workflow_name or "").strip()
        if not workflow_name or self._workflow_repo is None:
            return None, None

        workflows = await self._workflow_repo.list_workflows(
            owner_id=saga.owner_id,
            scope=WorkflowScope.SYSTEM,
        )
        workflow = next(
            (candidate for candidate in workflows if candidate.name == workflow_name),
            None,
        )
        if workflow is None:
            return None, None

        from tyr.domain.workflow_snapshot import build_workflow_snapshot  # noqa: PLC0415

        snapshot = build_workflow_snapshot(workflow)
        await self._saga_repo.update_saga_workflow(
            saga.id,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workflow_snapshot=snapshot,
            owner_id=saga.owner_id,
        )
        return snapshot, None
