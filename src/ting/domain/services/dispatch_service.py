"""Domain service for dispatch logic — find ready issues and spawn sessions.

Extracted from the API layer so it can be called programmatically by
auto-continue and future consumers without an HTTP request context.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
import string
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from sleipnir.domain.catalog import ting_saga_completed as _catalog_saga_completed
except ImportError:
    _catalog_saga_completed = None  # type: ignore[assignment]

from niuu.domain.models import Principal
from niuu.domain.tags import matches_tags, residual_selector, satisfiable, validate_match
from ravn.domain.persona_document import PortablePersonaSource
from ting.domain.exceptions import WorkflowStepsHaveNoRunnerError
from ting.domain.models import (
    DispatcherState,
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerProject,
    WorkflowScope,
)
from ting.domain.templates import BUNDLED_TEMPLATES_DIR, TemplatePhase, load_template
from ting.domain.tracker_routing import select_tracker_for_saga
from ting.domain.utils import _slugify
from ting.domain.workflow_snapshot import workflow_name_from_snapshot
from ting.ports.dispatcher_repository import DispatcherRepository
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerFactory, TrackerPort
from ting.ports.volundr import (
    HostTagSetsUnreadableError,
    SpawnRequest,
    VolundrFactory,
    VolundrPort,
)
from ting.ports.workflow_repository import WorkflowRepository

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


def _supports_keyword_arg(method: object, keyword: str) -> bool:
    try:
        return keyword in inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False


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
    status_type: str = ""
    priority: int = 0
    priority_label: str = ""
    estimate: float | None = None
    url: str = ""
    milestone_id: str | None = None
    workflow_id: str | None = None
    workflow: str | None = None
    workflow_version: str | None = None
    instance_id: str | None = None
    target_tags: tuple[str, ...] = ()
    target_match: str = "all"


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
    issue: TrackerIssue | None = None
    target_tags: tuple[str, ...] = ()
    target_match: str = "all"


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
    run_branch = issue.identifier.lower()

    if template:
        available = {
            "identifier": issue.identifier,
            "title": issue.title,
            "description": issue.description or "",
            "repo": repo,
            "feature_branch": feature_branch,
            "run_branch": run_branch,
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
        f"Create a working branch: `{run_branch}`",
        "",
        "Implement the task, write tests, create a PR against"
        f" `{feature_branch}`, and ensure CI passes.",
    ]
    return "\n".join(parts)


class TargetSelectionError(Exception):
    """Raised when no available Volundr backend satisfies the requested target."""


class HostTagsUnreadableError(TargetSelectionError):
    """No target matched, and some targets' host tags could not be read.

    Distinct from a plain miss: the unreadable targets may have the host the selector
    needs, so the message names each of them and why it could not be read.
    """


HOST_SELECTOR_KEY = "host_selector"


def _adapter_tags(adapter: VolundrPort) -> list[str]:
    return getattr(adapter, "tags", []) or []


async def _adapters_serving_tags(
    adapters: list[VolundrPort],
    tags: list[str] | tuple[str, ...],
    match: str,
    *,
    auth_token: str | None,
    principal: Principal | None,
) -> tuple[list[VolundrPort], dict[str, str]]:
    """Adapters whose tags, alone or with ONE of their host tag sets, satisfy the selector.

    Only an adapter whose own tags fall short is asked for its host tag sets. The second
    result maps the name of an adapter whose host tags could not be read to the reason:
    it is neither eligible nor dropped without a trace.
    """
    to_read = [a for a in adapters if not matches_tags(_adapter_tags(a), tags, match)]
    unreadable: dict[str, str] = {}
    serving = {id(a) for a in adapters} - {id(a) for a in to_read}

    async def read_one(adapter: VolundrPort) -> None:
        try:
            tag_sets = await adapter.host_tag_sets(auth_token=auth_token, principal=principal)
        except HostTagSetsUnreadableError as exc:
            unreadable[adapter.name or adapter.target_id] = str(exc)
            return
        if satisfiable(_adapter_tags(adapter), tag_sets, tags, match):
            serving.add(id(adapter))

    await asyncio.gather(*(read_one(a) for a in to_read))
    return [a for a in adapters if id(a) in serving], unreadable


async def select_adapter_by_tags(
    adapters: list[VolundrPort],
    tags: list[str] | tuple[str, ...] = (),
    match: str = "all",
    *,
    connection_id: str | None = None,
    auth_token: str | None = None,
    principal: Principal | None = None,
) -> VolundrPort:
    """Place new work uniformly across eligible targets, or honor an explicit pin.

    ``match="all"`` requires every tag; ``match="any"`` requires one. A target is
    eligible when its own tags satisfy the selector, or when its own tags together with
    ONE of its host tag sets do (``niuu.domain.tags.satisfiable``); the tag sets of
    different hosts are never combined. A target whose host tags cannot be read is not
    eligible, is logged, and, when nothing else is, is named in the raised error.
    Selection never retries another target after a failure.
    """
    if tags:
        try:
            validate_match(match)
        except ValueError as exc:
            raise TargetSelectionError(str(exc)) from exc
    candidates = adapters
    if connection_id:
        candidates = [a for a in adapters if connection_id in {a.target_id, a.name}]
    unreadable: dict[str, str] = {}
    eligible = candidates
    if tags:
        eligible, unreadable = await _adapters_serving_tags(
            candidates, tags, match, auth_token=auth_token, principal=principal
        )
    if unreadable:
        logger.error(
            "Host tags of %d Volundr target(s) could not be read for selector %s (match=%s) "
            "and they were not considered: %s",
            len(unreadable),
            sorted(set(tags)),
            match,
            unreadable,
        )
    if not eligible and unreadable:
        named = "; ".join(f"{name} ({reason})" for name, reason in sorted(unreadable.items()))
        raise HostTagsUnreadableError(
            f"No Volundr backend matches tags {sorted(set(tags))} (match={match}) by its own "
            f"tags, and the host tags of these targets could not be read, so they cannot be "
            f"ruled out: {named}"
        )
    if connection_id:
        if eligible:
            return eligible[0]
        raise TargetSelectionError(f"Volundr target not found or ineligible: {connection_id}")
    if not eligible:
        raise TargetSelectionError(
            f"No Volundr backend matches tags {sorted(set(tags))} (match={match})"
        )
    # ponytail: uniform placement; use capacity-aware scheduling when targets expose capacity.
    return random.choice(eligible)


def host_selector_for(
    adapter: VolundrPort,
    tags: list[str] | tuple[str, ...],
    match: str = "all",
) -> dict[str, Any] | None:
    """What the chosen target's Forge must still satisfy with a host, or ``None``.

    The value for ``workload_config.host_selector``: the selector minus what the
    target's own tags already cover (``niuu.domain.tags.residual_selector``).
    """
    if not tags:
        return None
    residual = residual_selector(_adapter_tags(adapter), tags, match)
    if residual is None:
        return None
    residual_tags, residual_match = residual
    return {"tags": list(residual_tags), "match": residual_match}


def host_selector_config(
    adapter: VolundrPort,
    tags: list[str] | tuple[str, ...],
    match: str = "all",
) -> dict[str, Any]:
    """``{"host_selector": ...}`` to merge into a ``workload_config``, or ``{}`` for none."""
    selector = host_selector_for(adapter, tags, match)
    if selector is None:
        return {}
    return {HOST_SELECTOR_KEY: selector}


def with_host_selector(
    request: SpawnRequest,
    adapter: VolundrPort,
    tags: list[str] | tuple[str, ...],
    match: str = "all",
) -> SpawnRequest:
    """The spawn request carrying the chosen target's residual host selector, if any.

    A request that already names its own ``host_selector`` while the placement also
    carries target tags is ambiguous and refused, never merged.
    """
    selector = host_selector_for(adapter, tags, match)
    if selector is None:
        return request
    if HOST_SELECTOR_KEY in request.workload_config:
        raise TargetSelectionError(
            f"The session already sets workload_config.{HOST_SELECTOR_KEY} and its placement "
            f"names target tags {sorted(set(tags))}; set only one of them"
        )
    return replace(
        request, workload_config={**request.workload_config, HOST_SELECTOR_KEY: selector}
    )


def base_branch_for_repo(saga: Saga, repo: str) -> str:
    """Return the saga base branch configured for *repo*."""
    return saga.repo_branches.get(repo, saga.base_branch)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class DispatchConfig:
    """Dispatch-related config values needed by the service."""

    default_system_prompt: str = ""
    default_model: str = "claude-sonnet-4-6"
    default_session_definition: str = ""
    dispatch_prompt_template: str = ""
    max_cached_issues: int = 10_000
    templates_dir: Path = BUNDLED_TEMPLATES_DIR


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
        workflow_repo: WorkflowRepository | None = None,
        persona_source: PortablePersonaSource | None = None,
    ) -> None:
        self._tracker_factory = tracker_factory
        self._volundr_factory = volundr_factory
        self._saga_repo = saga_repo
        self._dispatcher_repo = dispatcher_repo
        self._config = config
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sleipnir_publisher = sleipnir_publisher
        self._event_bus = event_bus
        self._workflow_repo = workflow_repo
        self._persona_source = persona_source

    async def _dispatch_capacity_snapshot(
        self,
        owner_id: str,
        *,
        volundrs: list[VolundrPort],
        trackers: list[TrackerPort] | None = None,
        auth_token: str | None = None,
    ) -> tuple[DispatcherState, int, int, int]:
        """Return dispatcher state plus active counts and free slots.

        Slot pressure is ultimately enforced by Volundr, so active Forge
        sessions are the source of truth. We also sample tracker-side RUNNING
        runs and take the max of both counts as a conservative fallback when
        subscriber reconciliation lags behind session reality.
        """
        state = await self._dispatcher_repo.get_or_create(owner_id)

        session_groups = await asyncio.gather(
            *(adapter.list_sessions(auth_token=auth_token) for adapter in volundrs)
        )
        active_sessions = sum(
            session.status in _ACTIVE_SESSION_STATUSES
            for sessions in session_groups
            for session in sessions
        )

        running_runs = 0
        if trackers:
            results = await asyncio.gather(
                *(tracker.list_runs_by_status(RunStatus.RUNNING) for tracker in trackers),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(
                        "Failed to list tracker RUNNING runs for owner %s "
                        "while checking capacity: %s",
                        owner_id[:8],
                        result,
                    )
                else:
                    running_runs += len(result)

        occupied_slots = max(active_sessions, running_runs)
        available_slots = max(state.max_concurrent_runs - occupied_slots, 0)
        return state, active_sessions, running_runs, available_slots

    async def find_ready_issues(
        self,
        owner_id: str,
        *,
        principal: Principal | None = None,
        auth_token: str | None = None,
        saga_tracker_id: str | None = None,
        tracker_connection_id: str | None = None,
    ) -> list[QueueItem]:
        """Find all dispatchable issues, optionally scoped to one saga."""
        adapters = await self._tracker_factory.for_owner(owner_id)
        if principal is not None and hasattr(self._volundr_factory, "for_principal"):
            all_volundr = await self._volundr_factory.for_principal(principal)
        else:
            all_volundr = await self._volundr_factory.for_owner(owner_id)
        if not all_volundr:
            logger.warning("No Volundr adapter for owner %s, returning empty queue", owner_id)
            return []

        sagas = await self._saga_repo.list_sagas(owner_id=owner_id)
        if saga_tracker_id:
            sagas = [
                saga
                for saga in sagas
                if saga.tracker_id == saga_tracker_id
                and (
                    not tracker_connection_id or saga.tracker_connection_id == tracker_connection_id
                )
            ]
        # Only process active sagas — completed/failed ones have no dispatchable work
        active_sagas = [s for s in sagas if s.status == SagaStatus.ACTIVE]
        if not active_sagas:
            return []

        # Get active sessions to exclude already-running issues
        session_groups = await asyncio.gather(
            *(adapter.list_sessions(auth_token=auth_token) for adapter in all_volundr)
        )
        active_issue_ids = {
            s.tracker_issue_id
            for sessions in session_groups
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
                # NIU-582: emit ting.saga.completed (best-effort)
                if self._sleipnir_publisher is not None and _catalog_saga_completed is not None:
                    try:
                        _event = _catalog_saga_completed(
                            saga_id=str(saga.id),
                            outcome="auto_archived",
                            phases_completed=0,
                            source="ting:dispatch",
                            correlation_id=str(saga.id),
                        )
                        await self._sleipnir_publisher.publish(_event)
                    except Exception:
                        logger.warning(
                            "Failed to emit ting.saga.completed; continuing.", exc_info=True
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
        principal: Principal | None = None,
        auth_token: str | None = None,
        model: str = "",
        system_prompt: str = "",
        connection_id: str | None = None,
        session_definition: str | None = None,
    ) -> list[DispatchResult]:
        """Spawn Volundr sessions for the given items.

        :raises WorkflowStepsHaveNoRunnerError: When any item belongs to a saga (or names a
            workflow) that carries a workflow. Raised before any session is spawned, so a
            refused batch has no side effects.
        """
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
        if principal is not None and hasattr(self._volundr_factory, "for_principal"):
            all_volundr = await self._volundr_factory.for_principal(principal)
        else:
            all_volundr = await self._volundr_factory.for_owner(owner_id)
        if not all_volundr:
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

        integration_ids_by_target: dict[int, list[str]] = {}

        # Build lookups
        sagas = await self._saga_repo.list_sagas(owner_id=owner_id)
        saga_map = {str(s.id): s for s in sagas}
        issue_cache = await self._build_issue_cache(adapters, sagas, self._config.max_cached_issues)

        snapshots: dict[int, tuple[dict[str, Any] | None, str | None]] = {}
        for index, item in enumerate(items):
            known_saga = saga_map.get(item.saga_id)
            if known_saga is None:
                continue
            snapshots[index] = await self._resolve_workflow_snapshot(item, known_saga)
            if snapshots[index][0]:
                logger.error(
                    "Dispatch of issue %s refused: its saga %s runs a workflow and "
                    "workflow steps have no runner",
                    _sanitize_log(item.issue_id),
                    _sanitize_log(known_saga.slug),
                )
                raise WorkflowStepsHaveNoRunnerError()

        results: list[DispatchResult] = []
        for index, item in enumerate(items):
            saga = saga_map.get(item.saga_id)
            if saga is None:
                logger.warning("Saga not found: %s", item.saga_id)
                continue

            issue = item.issue or issue_cache.get((str(saga.id), item.issue_id))
            if issue is None:
                logger.warning("Issue not found: %s", item.issue_id)
                continue

            target_connection = item.connection_id or connection_id
            # Saga tags replace its default instance; an explicit request pin still applies.
            if not target_connection and not item.target_tags:
                target_connection = saga.instance_id
            try:
                target_volundr = await select_adapter_by_tags(
                    all_volundr,
                    item.target_tags,
                    item.target_match,
                    connection_id=target_connection,
                    auth_token=auth_token,
                    principal=principal,
                )
            except TargetSelectionError as exc:
                logger.warning("Target selection failed for issue %s: %s", item.issue_id, exc)
                results.append(
                    DispatchResult(
                        issue_id=item.issue_id,
                        session_id="",
                        session_name="",
                        status="failed",
                    )
                )
                continue
            integration_cache_key = id(target_volundr)
            integration_ids = integration_ids_by_target.get(integration_cache_key)
            if integration_ids is None:
                integration_ids = await self._fetch_integration_ids(
                    target_volundr,
                    auth_token,
                    owner_id,
                    principal=principal,
                )
                integration_ids_by_target[integration_cache_key] = integration_ids
            workflow_snapshot, workflow_error = snapshots[index]
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
                adapter=select_tracker_for_saga(adapters, saga),
                effective_model=effective_model,
                effective_prompt=effective_prompt,
                integration_ids=integration_ids,
                session_definition=item.session_definition or effective_session_definition,
                auth_token=auth_token,
                principal=principal,
                owner_id=owner_id,
                workflow_snapshot=workflow_snapshot,
            )
            results.append(result)

        return results

    async def try_auto_continue(
        self,
        owner_id: str,
        saga_tracker_id: str,
        tracker_connection_id: str | None = None,
    ) -> list[DispatchResult]:
        """Dispatch newly unblocked issues if auto_continue is enabled.

        Called after a run merges or a phase gate is unlocked. Uses a
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
            if self._volundr_factory is None:
                return []
            all_volundr = await self._volundr_factory.for_owner(owner_id)
            if not all_volundr:
                logger.info("Auto-continue skipped for owner %s: no Volundr adapter", owner_id[:8])
                return []

            (
                state,
                active_sessions,
                running_runs,
                available_slots,
            ) = await self._dispatch_capacity_snapshot(
                owner_id,
                volundrs=all_volundr,
                trackers=adapters,
            )
            if available_slots <= 0:
                logger.info(
                    "Auto-continue skipped for owner %s: no slots "
                    "(%d active sessions, %d running runs, max %d)",
                    owner_id[:8],
                    active_sessions,
                    running_runs,
                    state.max_concurrent_runs,
                )
                return []

            ready = await self.find_ready_issues(
                owner_id,
                saga_tracker_id=saga_tracker_id,
                tracker_connection_id=tracker_connection_id,
            )
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
                    issue=TrackerIssue(
                        id=q.issue_id,
                        identifier=q.identifier,
                        title=q.title,
                        description=q.description,
                        status=q.status,
                        status_type=q.status_type,
                        priority=q.priority,
                        priority_label=q.priority_label,
                        estimate=q.estimate,
                        url=q.url,
                        milestone_id=q.milestone_id,
                    ),
                    target_tags=q.target_tags,
                    target_match=q.target_match,
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
        *payload*, persists the saga + phases + runs, and — when
        *auto_start* is True — spawns Volundr sessions for all runs in the
        first phase.

        :param template_name: Template name without extension (e.g. ``"ship"``).
        :param payload: Key/value pairs substituted into ``{event.field}`` placeholders.
        :param owner_id: Owner for the created saga.
        :param auto_start: Dispatch Phase 1 runs immediately when True.
        :returns: The new saga ID as a string.
        :raises FileNotFoundError: When the template cannot be found.
        :raises ValueError: When the template fails validation.
        """
        template = load_template(template_name, self._config.templates_dir, payload)

        if template.flock_flow:
            raise WorkflowStepsHaveNoRunnerError()

        now = datetime.now(UTC)
        saga_id = uuid.uuid4()
        slug = _slugify(template.name)[:60]

        saga = Saga(
            id=saga_id,
            tracker_id=str(saga_id),
            tracker_type="native",
            tracker_connection_id="native",
            slug=slug,
            name=template.name,
            repos=template.repos,
            feature_branch=template.feature_branch,
            base_branch=template.base_branch,
            status=SagaStatus.ACTIVE,
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
            )
            await self._saga_repo.save_phase(phase)

            runs: list[Run] = []
            for tpl_run in tpl_phase.runs:
                run_id = uuid.uuid4()
                run = Run(
                    id=run_id,
                    phase_id=phase_id,
                    tracker_id=str(run_id),
                    name=tpl_run.name,
                    description=tpl_run.description,
                    acceptance_criteria=tpl_run.acceptance_criteria,
                    declared_files=tpl_run.declared_files,
                    estimate_hours=tpl_run.estimate_hours,
                    status=RunStatus.PENDING,
                    session_id=None,
                    branch=None,
                    chronicle_summary=None,
                    pr_url=None,
                    pr_id=None,
                    retry_count=0,
                    created_at=now,
                    updated_at=now,
                )
                await self._saga_repo.save_run(run)
                runs.append(run)
            phases_data.append((phase, runs, tpl_phase))

        if self._event_bus is not None:
            await self._event_bus.emit(
                TingEvent(
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
            first_phase, first_runs, first_tpl = phases_data[0]
            await self._dispatch_template_phase(
                saga,
                first_phase,
                first_runs,
                first_tpl,
                owner_id,
            )

        return str(saga_id)

    async def _dispatch_template_phase(
        self,
        saga: Saga,
        phase: Phase,
        runs: list[Run],
        tpl_phase: TemplatePhase,
        owner_id: str,
    ) -> None:
        """Spawn one plain Volundr session for each run in a template phase."""
        all_volundr = await self._volundr_factory.for_owner(owner_id)
        if not all_volundr:
            logger.error(
                "DispatchService: no Volundr adapter for owner %s, cannot dispatch phase '%s'",
                owner_id,
                phase.name,
            )
            return

        integration_ids_by_target: dict[int, list[str]] = {}
        trackers = await self._tracker_factory.for_owner(owner_id)
        (
            state,
            active_sessions,
            running_runs,
            available_slots,
        ) = await self._dispatch_capacity_snapshot(
            owner_id,
            volundrs=all_volundr,
            trackers=trackers,
        )
        if available_slots < len(runs):
            logger.info(
                "DispatchService: phase '%s' has %d run(s) but only %d slot(s) "
                "are free for owner %s (%d active sessions, %d running runs, "
                "max %d); queueing overflow",
                phase.name,
                len(runs),
                available_slots,
                owner_id[:8],
                active_sessions,
                running_runs,
                state.max_concurrent_runs,
            )

        repo = saga.repos[0] if saga.repos else ""
        for index, (run, tpl_run) in enumerate(zip(runs, tpl_phase.runs)):
            if index >= available_slots:
                queued = Run(
                    id=run.id,
                    phase_id=run.phase_id,
                    tracker_id=run.tracker_id,
                    name=run.name,
                    description=run.description,
                    acceptance_criteria=run.acceptance_criteria,
                    declared_files=run.declared_files,
                    estimate_hours=run.estimate_hours,
                    status=RunStatus.QUEUED,
                    session_id=None,
                    branch=run.branch,
                    chronicle_summary=run.chronicle_summary,
                    pr_url=run.pr_url,
                    pr_id=run.pr_id,
                    retry_count=run.retry_count,
                    created_at=run.created_at,
                    updated_at=datetime.now(UTC),
                )
                await self._saga_repo.save_run(queued)
                logger.info(
                    "DispatchService: queued template run %s in phase '%s' until a slot frees up",
                    run.name,
                    phase.name,
                )
                continue
            volundr = await select_adapter_by_tags(
                all_volundr,
                saga.target_tags,
                saga.target_match,
                connection_id=saga.instance_id if not saga.target_tags else None,
            )
            if id(volundr) not in integration_ids_by_target:
                integration_ids_by_target[id(volundr)] = await self._fetch_integration_ids(
                    volundr, None, owner_id
                )
            integration_ids = integration_ids_by_target[id(volundr)]
            session_name = re.sub(r"[^a-z0-9]+", "-", run.name.lower()).strip("-")[:48]
            request = SpawnRequest(
                name=session_name,
                repo=repo,
                branch=saga.feature_branch,
                base_branch=base_branch_for_repo(saga, repo),
                model=self._config.default_model,
                tracker_issue_id=run.tracker_id,
                tracker_issue_url="",
                system_prompt=self._config.default_system_prompt,
                initial_prompt=tpl_run.prompt,
                profile=tpl_run.persona or None,
                integration_ids=integration_ids,
            )
            request = with_host_selector(request, volundr, saga.target_tags, saga.target_match)
            try:
                session = await volundr.spawn_session(request=request)
                updated = Run(
                    id=run.id,
                    phase_id=run.phase_id,
                    tracker_id=run.tracker_id,
                    name=run.name,
                    description=run.description,
                    acceptance_criteria=run.acceptance_criteria,
                    declared_files=run.declared_files,
                    estimate_hours=run.estimate_hours,
                    status=RunStatus.RUNNING,
                    session_id=session.id,
                    branch=run.branch,
                    chronicle_summary=run.chronicle_summary,
                    pr_url=run.pr_url,
                    pr_id=run.pr_id,
                    retry_count=run.retry_count,
                    created_at=run.created_at,
                    updated_at=datetime.now(UTC),
                )
                await self._saga_repo.save_run(updated)
                logger.info(
                    "DispatchService: dispatched template run %s → session %s (persona=%s)",
                    run.name,
                    session.id,
                    tpl_run.persona or "(none)",
                )
            except Exception:
                logger.exception(
                    "DispatchService: failed to spawn session for template run %s", run.name
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

        adapter = select_tracker_for_saga(adapters, saga)
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
                        status_type=issue.status_type,
                        priority=issue.priority,
                        priority_label=issue.priority_label,
                        estimate=issue.estimate,
                        url=issue.url,
                        milestone_id=issue.milestone_id,
                        workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
                        workflow=workflow_name_from_snapshot(saga.workflow_snapshot),
                        workflow_version=(
                            str(saga.workflow_snapshot.get("version"))
                            if saga.workflow_snapshot
                            and saga.workflow_snapshot.get("version") is not None
                            else None
                        ),
                        instance_id=saga.instance_id,
                        target_tags=tuple(saga.target_tags),
                        target_match=saga.target_match,
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
        volundr: VolundrPort,
        auth_token: str | None,
        owner_id: str,
        *,
        principal: Principal | None = None,
    ) -> list[str]:
        """Fetch integration IDs from Volundr, returning empty on failure."""
        try:
            kwargs: dict[str, Any] = {"auth_token": auth_token}
            if principal is not None and _supports_keyword_arg(
                volundr.list_integration_ids, "principal"
            ):
                kwargs["principal"] = principal
            ids = await volundr.list_integration_ids(**kwargs)
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
    ) -> dict[tuple[str, str], TrackerIssue]:
        """Build a lookup of issue details for prompt generation."""
        issue_cache: dict[tuple[str, str], TrackerIssue] = {}
        for saga in sagas:
            adapter = select_tracker_for_saga(adapters, saga)
            try:
                issues = await adapter.list_issues(saga.tracker_id)
                for issue in issues:
                    if len(issue_cache) >= max_cached_issues:
                        logger.warning(
                            "Issue cache limit reached (%d), skipping remaining issues",
                            max_cached_issues,
                        )
                        break
                    issue_cache[(str(saga.id), issue.id)] = issue
            except Exception:
                logger.warning("Failed to fetch issues for saga %s", saga.id, exc_info=True)
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
        workflow_snapshot: dict[str, Any] | None = None,
    ) -> SpawnRequest:
        """Build the SpawnRequest for one plain Forge session working on *issue*.

        A workflow is not a plain session: its steps need a runner, and there is
        none until they run on harness sessions, so a workflow snapshot is refused.

        :raises WorkflowStepsHaveNoRunnerError: When *workflow_snapshot* is set.
        """
        if workflow_snapshot:
            raise WorkflowStepsHaveNoRunnerError()
        return SpawnRequest(
            name=issue.identifier.lower(),
            repo=item.repo,
            branch=saga.feature_branch,
            base_branch=base_branch_for_repo(saga, item.repo),
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
            integration_ids=integration_ids,
        )

    async def _spawn_single(
        self,
        *,
        target_volundr: VolundrPort,
        item: DispatchItem,
        saga: Saga,
        issue: TrackerIssue,
        adapter: TrackerPort,
        effective_model: str,
        effective_prompt: str,
        integration_ids: list[str],
        session_definition: str | None,
        auth_token: str | None,
        principal: Principal | None,
        owner_id: str,
        workflow_snapshot: dict[str, Any] | None = None,
    ) -> DispatchResult:
        """Spawn a single session and update run progress.

        :raises WorkflowStepsHaveNoRunnerError: When the item carries a workflow. Never
            reported as a ``failed`` result: that would read as a transient failure.
        """
        try:
            request = self._build_spawn_request(
                item=item,
                saga=saga,
                issue=issue,
                effective_model=effective_model,
                effective_prompt=effective_prompt,
                integration_ids=integration_ids,
                session_definition=session_definition,
                workflow_snapshot=workflow_snapshot,
            )
            request = with_host_selector(
                request, target_volundr, item.target_tags, item.target_match
            )
            spawn_kwargs: dict[str, Any] = {
                "request": request,
                "auth_token": auth_token,
            }
            if principal is not None and _supports_keyword_arg(
                target_volundr.spawn_session, "principal"
            ):
                spawn_kwargs["principal"] = principal
            session = await target_volundr.spawn_session(**spawn_kwargs)

            # Record run progress and set tracker issue to In Progress
            adapter_name = type(adapter).__name__
            # Older external adapters may not yet accept tenant attribution.
            progress_scope = {}
            if _supports_keyword_arg(adapter.update_run_progress, "tenant_id"):
                progress_scope["tenant_id"] = principal.tenant_id if principal is not None else ""
            await adapter.update_run_progress(
                issue.id,
                status=RunStatus.RUNNING,
                session_id=session.id,
                owner_id=owner_id,
                **progress_scope,
                phase_tracker_id=issue.milestone_id,
                saga_tracker_id=saga.tracker_id,
            )
            logger.info(
                "Dispatch: %s.update_run_progress OK for %s",
                adapter_name,
                issue.id,
            )
            try:
                await adapter.update_run_state(issue.id, RunStatus.RUNNING)
                logger.info(
                    "Dispatch: %s.update_run_state OK for %s → In Progress",
                    adapter_name,
                    issue.id,
                )
            except Exception:
                logger.error(
                    "FAILED: %s.update_run_state for %s",
                    adapter_name,
                    issue.id,
                    exc_info=True,
                )

            logger.info("Dispatched %s → session %s", issue.identifier, session.id)
            if self._event_bus is not None:
                await self._event_bus.emit(
                    TingEvent(
                        event="run.state_changed",
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
        except WorkflowStepsHaveNoRunnerError:
            raise
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
            return saga.workflow_snapshot, None

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
        from ting.domain.workflow_snapshot import build_workflow_snapshot  # noqa: PLC0415

        try:
            snapshot = build_workflow_snapshot(workflow, persona_source=self._persona_source)
        except ValueError as exc:
            return None, str(exc)
        return snapshot, None
