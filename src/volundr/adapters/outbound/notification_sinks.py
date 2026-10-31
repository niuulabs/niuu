"""Notification sinks for the Forge outbox: configured named sinks plus integrations.

Named sinks come from ``notifications.sinks`` with the dynamic adapter rule::

    - name: ops-webhook                      # what a rule's ``sink`` refers to
      label: Ops webhook                     # shown by GET /notifications/sinks
      adapter: niuu.adapters.notifications.webhook.WebhookNotificationSink
      url: https://hooks.example.com/forge   # every other key is a kwarg
      secret_kwargs_env: {secret: FORGE_OPS_WEBHOOK_SECRET}

The sink receives ``name`` plus its kwargs. Volundr also passes the
dependencies a sink's constructor asks for by name (for the push sink:
``device_repository`` and ``push_channel``). A sink
that fails to build stops startup: a configured sink is never silently left out.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from typing import Any

from niuu.ports.notifications import (
    NotificationSink,
    NotificationSinkResolver,
    NotificationSinkUnavailableError,
)
from niuu.utils import import_class, resolve_secret_kwargs
from volundr.domain.notification_ports import NotificationSinkProvider
from volundr.domain.notifications import NotificationRule

logger = logging.getLogger(__name__)


class NotificationSinkBuildError(RuntimeError):
    """A configured ``notifications.sinks`` entry could not be built."""


class NotificationSinkSpecError(TypeError):
    """A sink entry is wrong before its constructor runs; the message holds no secrets."""


#: Config-entry keys that describe the sink rather than being passed to it.
_ENTRY_KEYS = frozenset({"label", "adapter", "secret_kwargs_env"})


def _requested(cls: type, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """The dependencies ``cls`` names as explicit constructor parameters."""
    parameters = inspect.signature(cls).parameters
    return {
        name: value
        for name, value in dependencies.items()
        if name in parameters and parameters[name].kind is not inspect.Parameter.VAR_KEYWORD
    }


def build_sink(entry: Mapping[str, Any], dependencies: Mapping[str, Any]) -> NotificationSink:
    """Build one configured sink from its ``notifications.sinks`` entry."""
    cls = import_class(str(entry["adapter"]))
    if not isinstance(cls, type) or not issubclass(cls, NotificationSink):
        raise NotificationSinkSpecError(f"{entry['adapter']} is not a NotificationSink")
    kwargs = {key: value for key, value in entry.items() if key not in _ENTRY_KEYS}
    kwargs = resolve_secret_kwargs(kwargs, dict(entry.get("secret_kwargs_env") or {}))
    arguments = {**_requested(cls, dependencies), **kwargs}
    missing = _missing_required(cls, arguments)
    if missing:
        raise NotificationSinkSpecError(
            f"{entry['adapter']} is missing required arguments: {', '.join(missing)}"
        )
    return cls(**arguments)


def _missing_required(cls: type, arguments: Mapping[str, Any]) -> list[str]:
    """Required keyword parameters of ``cls`` that ``arguments`` does not supply."""
    return [
        name
        for name, parameter in inspect.signature(cls).parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and name not in arguments
    ]


def build_configured_sinks(
    entries: list[dict[str, Any]], dependencies: Mapping[str, Any]
) -> dict[str, NotificationSink]:
    """Build every configured sink; one that fails to build raises."""
    sinks: dict[str, NotificationSink] = {}
    for entry in entries:
        name = str(entry["name"])
        adapter = str(entry["adapter"])
        try:
            sinks[name] = build_sink(entry, dependencies)
        except Exception as exc:
            # A constructor error can echo a secret kwarg's value: only the type
            # is reported and its chain is dropped. Import and pre-construction
            # errors name only the adapter path and parameters, so they are kept.
            safe = isinstance(exc, (NotificationSinkSpecError, ImportError, AttributeError))
            detail = str(exc) if safe else ""
            raise NotificationSinkBuildError(
                f"Notification sink {name!r} ({adapter}) could not be built: "
                f"{type(exc).__name__}{': ' + detail if detail else ''}. Fix or remove its "
                "notifications.sinks entry, or set notifications.dispatcher.enabled: false."
            ) from None
        logger.info("Notification sink %r: %s", name, adapter.rsplit(".", 1)[-1])
    return sinks


class ConfiguredNotificationSinks(NotificationSinkProvider):
    """Named sinks are shared for the process; integration sinks are per use."""

    def __init__(
        self,
        sinks: Mapping[str, NotificationSink],
        *,
        integration_resolver: NotificationSinkResolver | None = None,
    ) -> None:
        self._sinks = dict(sinks)
        self._shared = {id(sink) for sink in self._sinks.values()}
        self._resolver = integration_resolver

    @property
    def names(self) -> list[str]:
        return sorted(self._sinks)

    async def open(self, rule: NotificationRule) -> NotificationSink:
        if rule.integration_connection_id:
            if self._resolver is None:
                raise NotificationSinkUnavailableError(
                    "Delivery through messaging integrations is not available on this host",
                    retryable=False,
                )
            return await self._resolver.for_connection(
                rule.owner_id, rule.integration_connection_id
            )
        sink = self._sinks.get(rule.sink)
        if sink is None:
            # Operator configuration: a restart with the sink fixed lets retries succeed.
            raise NotificationSinkUnavailableError(
                f"Sink {rule.sink!r} is not configured on this host (notifications.sinks)",
                retryable=True,
            )
        return sink

    async def release(self, sink: NotificationSink) -> None:
        if id(sink) in self._shared:
            return
        await sink.close()

    async def close(self) -> None:
        """Close every named sink, then raise every close failure together."""
        failures: list[Exception] = []
        for sink in self._sinks.values():
            try:
                await sink.close()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise ExceptionGroup("Closing notification sinks failed", failures)
