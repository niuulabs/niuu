"""ApiTriggerSource — a resident's own poll of the durable trigger store.

Loads the resident's triggers from ``GET /api/v1/ravn/triggers`` (the same
workload-authenticated HTTP boundary ``RealmClient`` already uses to read
trust grants) instead of a direct database connection — a resident deployed
in a container gets the platform's base URL and workload identity from its
own config already, so this reuses that data path rather than shipping
Postgres credentials to every resident pod. See
``ravn.adapters.realm.client.RealmClient`` for the precedent this follows.

Two trigger kinds are executed, reusing the runtime engines that already
exist rather than inventing new ones:

- ``cron`` triggers are exposed as :class:`CronJobRecord` rows via
  :class:`ApiCronJobStore`, read every tick by a dedicated
  :class:`ravn.adapters.triggers.cron.CronTrigger` instance — the same
  due-detection, dedup, and delivery-routing logic the ``cron_create`` tool's
  jobs already use.
- ``event`` triggers are run directly: each becomes a
  :class:`ravn.adapters.triggers.sleipnir.SleipnirEventTrigger` subtask,
  started and cancelled as the API's trigger set changes, with a
  ``payload_filter`` bound to the trigger's own ``repo`` — the Sleipnir event
  bus is not tenant-scoped, so without this filter an event-kind trigger
  would fire on every tenant's matching events, not just this realm's.

Only triggers whose ``persona_name`` matches this resident are considered —
the API scopes the list to the whole tenant (so several residents in one
realm can share it), and this is the client-side narrowing to "my triggers".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ravn.adapters.triggers.cron import CronJobRecord
from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger
from ravn.domain.models import AgentTask
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

#: HTTP status meaning the request succeeded.
_HTTP_OK = 200
#: 4xx: the request itself is wrong (bad auth, bad URL, ...) — retrying the
#: exact same request will not fix it, so this is fatal, not transient.
_HTTP_CLIENT_ERROR_START = 400
_HTTP_CLIENT_ERROR_END = 500


class PermanentPollError(Exception):
    """A poll failed in a way retrying will not fix — auth, a 4xx, or a
    response this client cannot even parse. Escalates immediately instead of
    retrying forever with an ever-stale trigger cache."""


class ApiTriggerSource(TriggerPort):
    """Polls the durable trigger store and runs this resident's triggers."""

    def __init__(
        self,
        *,
        client: Any,
        base_url: str,
        persona_name: str,
        poll_interval_seconds: float,
        amqp_url: str,
        sleipnir_exchange: str,
        max_consecutive_poll_failures: int = 5,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._persona_name = persona_name
        self._poll_interval_seconds = poll_interval_seconds
        self._amqp_url = amqp_url
        self._sleipnir_exchange = sleipnir_exchange
        self._max_consecutive_poll_failures = max_consecutive_poll_failures
        self._cron_records: list[CronJobRecord] = []
        self._event_tasks: dict[str, asyncio.Task] = {}

    @property
    def name(self) -> str:
        return "api_trigger_source"

    @property
    def cron_records(self) -> list[CronJobRecord]:
        """Snapshot read by :class:`ApiCronJobStore` on every ``CronTrigger`` tick."""
        return list(self._cron_records)

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        consecutive_failures = 0
        try:
            while True:
                try:
                    await self._poll_once(enqueue)
                    consecutive_failures = 0
                except PermanentPollError:
                    raise  # not retryable — escalate immediately, no backoff
                except Exception as exc:  # noqa: BLE001 — bounded transient-network
                    # resilience, mirroring SleipnirEventTrigger's own reconnect loop —
                    # but capped: a store that stays unreachable must eventually surface
                    # as a real failure, not keep silently serving a stale trigger cache.
                    consecutive_failures += 1
                    if consecutive_failures >= self._max_consecutive_poll_failures:
                        raise RuntimeError(
                            f"api_trigger_source: {consecutive_failures} consecutive poll "
                            f"failures against {self._base_url} (last: {exc})"
                        ) from exc
                    logger.warning(
                        "api_trigger_source: poll failed (%s) — retrying in %.0fs (failure %d/%d)",
                        exc,
                        self._poll_interval_seconds,
                        consecutive_failures,
                        self._max_consecutive_poll_failures,
                    )
                await asyncio.sleep(self._poll_interval_seconds)
        finally:
            # On ANY exit (a permanent failure propagating, or cancellation),
            # drop the last-polled state rather than leave it for
            # ApiCronJobStore.list()/CronTrigger — or a caller that hasn't
            # yet noticed this coroutine died — to keep reading a frozen
            # snapshot that can include triggers since disabled or deleted.
            for task in self._event_tasks.values():
                task.cancel()
            self._event_tasks.clear()
            self._cron_records = []

    async def _poll_once(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        response = await self._client.get(f"{self._base_url}/api/v1/ravn/triggers")
        if _HTTP_CLIENT_ERROR_START <= response.status_code < _HTTP_CLIENT_ERROR_END:
            raise PermanentPollError(
                f"GET /triggers returned HTTP {response.status_code} against "
                f"{self._base_url} — check gateway.platform auth, not a transient failure"
            )
        if response.status_code != _HTTP_OK:
            raise ValueError(f"GET /triggers returned HTTP {response.status_code}")
        if not isinstance(response.body, list):
            raise PermanentPollError(
                f"/triggers returned a non-list body: {type(response.body).__name__}"
            )
        mine = [
            item
            for item in response.body
            if isinstance(item, dict) and item.get("persona_name") == self._persona_name
        ]
        self._cron_records = [
            _to_cron_record(item)
            for item in mine
            if item.get("kind") == "cron" and item.get("enabled")
        ]
        self._sync_event_triggers(mine, enqueue)

    def _sync_event_triggers(
        self,
        items: list[dict],
        enqueue: Callable[[AgentTask], Awaitable[bool]],
    ) -> None:
        active_ids = {
            str(item["id"]) for item in items if item.get("kind") == "event" and item.get("enabled")
        }
        for trigger_id in list(self._event_tasks):
            if trigger_id not in active_ids:
                self._event_tasks.pop(trigger_id).cancel()
        for item in items:
            trigger_id = str(item["id"])
            if trigger_id not in active_ids or trigger_id in self._event_tasks:
                continue
            repo = str(item.get("repo") or "")
            sub_trigger = SleipnirEventTrigger(
                name=trigger_id,
                pattern=str(item["spec"]),
                context_template=_event_context_template(item),
                persona=self._persona_name,
                amqp_url=self._amqp_url,
                exchange=self._sleipnir_exchange,
                payload_filter=_repo_filter(repo),
            )
            self._event_tasks[trigger_id] = asyncio.create_task(
                sub_trigger.run(enqueue), name=f"api_trigger_event:{trigger_id}"
            )


def _repo_filter(repo: str) -> Callable[[dict], bool]:
    """Match only events whose ``repo`` payload field is this trigger's repo.

    The dict this filter receives is the *whole wire envelope* — what
    ``SleipnirEvent.to_dict()``/``sleipnir.adapters.serialization.serialize``
    put on the AMQP message, decoded back by ``SleipnirEventTrigger``'s
    ``json.loads(message.body)`` — not the inner event payload. ``repo`` is
    therefore nested at ``envelope["payload"]["repo"]``, the field
    GitHub-webhook-derived Sleipnir events carry (see
    ``volundr.adapters.inbound.rest_webhooks._translate_pull_request`` et al,
    which set it from ``payload["repository"]["full_name"]``, and
    ``sleipnir.domain.catalog.github_pr_opened`` for the same shape built
    from the catalog); other event sources are expected to carry the same
    field when they should be trigger-filterable by repo.
    validate_trigger_spec requires a non-empty repo for every event-kind
    trigger, so this always narrows.
    """

    def _matches(envelope: dict) -> bool:
        inner = envelope.get("payload")
        if not isinstance(inner, dict):
            return False
        return str(inner.get("repo") or "") == repo

    return _matches


class ApiCronJobStore:
    """Read-only ``CronJobStore``-shaped view over an :class:`ApiTriggerSource`.

    Duck-typed to what ``CronTrigger.run()`` actually calls (``.list()``
    only, see ``ravn.adapters.triggers.cron.CronTrigger``) — it does not
    implement ``get``/``create``/``delete`` because API-sourced triggers are
    managed through ``/api/v1/ravn/triggers``, not the ``cron_create`` tool.
    """

    def __init__(self, source: ApiTriggerSource) -> None:
        self._source = source

    def list(self) -> list[CronJobRecord]:
        return self._source.cron_records


def _to_cron_record(item: dict) -> CronJobRecord:
    return CronJobRecord(
        job_id=str(item["id"]),
        name=f"api:{item['id']}",
        schedule=str(item["spec"]),
        context=_cron_context(item),
        delivery="platform",
        persona=str(item.get("persona_name") or "") or None,
        enabled=bool(item.get("enabled", True)),
    )


def _cron_context(item: dict) -> str:
    return (
        f"[trigger:cron:{item.get('spec', '')}] Scheduled check-in. Review your realm "
        "and do the standing work described in your charter."
    )


def _event_context_template(item: dict) -> str:
    return (
        f"[trigger:event:{item.get('spec', '')}] {{{{ payload }}}}\n\n"
        "An event you watch for just fired. Handle it per your charter."
    )
