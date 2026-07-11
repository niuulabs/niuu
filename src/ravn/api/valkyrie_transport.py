"""Typed NATS transport composition for the resident Valkyrie API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from ravn.api.valkyrie_projection import ValkyrieDashboardProjection
from ravn.api.valkyrie_routes import (
    OdinReviewCommandPublisher,
    _CommandTarget,
    _FanoutSleipnirPublisher,
)
from ravn.config import Settings, ValkyrieCommandConfig, ValkyrieTelemetryConfig
from sleipnir.domain.events import SleipnirEvent

logger = logging.getLogger(__name__)


class ValkyrieTelemetrySubscription:
    """Feed live Sleipnir/NATS telemetry events into the dashboard projection."""

    def __init__(
        self,
        *,
        projection: ValkyrieDashboardProjection,
        subscribers: list[tuple[str, Any]],
        event_types: list[str],
        retry_interval_seconds: int = 30,
        startup_delay_seconds: float = 5.0,
        subscriber_start_timeout_seconds: float = 5.0,
        review_ingest: Any | None = None,
        history_ingest: Any | None = None,
        skills_ingest: Any | None = None,
    ) -> None:
        self._projection = projection
        self._review_ingest = review_ingest
        self._history_ingest = history_ingest
        self._skills_ingest = skills_ingest
        self._subscribers = subscribers
        self._event_types = event_types
        self._subscriptions: list[tuple[str, Any]] = []
        self._retry_interval_seconds = max(retry_interval_seconds, 1)
        self._startup_delay_seconds = max(startup_delay_seconds, 0.0)
        self._subscriber_start_timeout_seconds = max(subscriber_start_timeout_seconds, 1.0)
        self._bootstrap_task: asyncio.Task[None] | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._started_labels: set[str] = set()
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        self._bootstrap_task = asyncio.create_task(self._bootstrap_subscribers())
        await asyncio.sleep(0)

    async def stop(self) -> None:
        self._stopping = True
        if self._bootstrap_task is not None:
            self._bootstrap_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._bootstrap_task
            self._bootstrap_task = None
        if self._retry_task is not None:
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_task
            self._retry_task = None
        for _, subscription in self._subscriptions:
            with contextlib.suppress(Exception):
                await subscription.unsubscribe()
        self._subscriptions.clear()
        self._started_labels.clear()
        for _, subscriber in self._subscribers:
            if hasattr(subscriber, "stop"):
                with contextlib.suppress(Exception):
                    await subscriber.stop()

    async def _bootstrap_subscribers(self) -> None:
        if self._startup_delay_seconds > 0:
            await asyncio.sleep(self._startup_delay_seconds)
        if self._stopping:
            return
        results = await asyncio.gather(
            *(self._start_subscriber(label, subscriber) for label, subscriber in self._subscribers),
        )
        failed = [
            subscriber_spec
            for subscriber_spec, started in zip(self._subscribers, results, strict=True)
            if not started
        ]
        if failed:
            self._retry_task = asyncio.create_task(self._retry_failed(failed))
        if not self._subscriptions:
            logger.warning("valkyrie_dashboard: no telemetry streams subscribed yet")

    async def _handle(self, event: SleipnirEvent) -> None:
        self._projection.record_event(event)
        if self._history_ingest is not None:
            try:
                await self._history_ingest(event)
            except Exception:
                logger.exception(
                    "valkyrie_dashboard: history ingest failed for %s",
                    event.event_type,
                )
        if self._skills_ingest is not None:
            try:
                await self._skills_ingest(event)
            except Exception:
                logger.exception(
                    "valkyrie_dashboard: skill mirror ingest failed for %s",
                    event.event_type,
                )
        if self._review_ingest is None:
            return
        try:
            await self._review_ingest(event)
        except Exception:
            logger.exception(
                "valkyrie_dashboard: review queue ingest failed for %s",
                event.event_type,
            )

    async def _start_subscriber(self, label: str, subscriber: Any) -> bool:
        if label in self._started_labels:
            return True
        try:
            subscription = await asyncio.wait_for(
                self._start_subscriber_subscription(subscriber),
                timeout=self._subscriber_start_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "valkyrie_dashboard: telemetry stream %s did not start: %r",
                label,
                exc,
            )
            if hasattr(subscriber, "stop"):
                with contextlib.suppress(Exception):
                    await subscriber.stop()
            return False
        self._subscriptions.append((label, subscription))
        self._started_labels.add(label)
        logger.info(
            "valkyrie_dashboard: subscribed to %s telemetry events: %s",
            label,
            ", ".join(self._event_types),
        )
        return True

    async def _start_subscriber_subscription(self, subscriber: Any) -> Any:
        await subscriber.start()
        return await subscriber.subscribe(self._event_types, self._handle)

    async def _retry_failed(self, failed: list[tuple[str, Any]]) -> None:
        pending = list(failed)
        while pending and not self._stopping:
            await asyncio.sleep(self._retry_interval_seconds)
            if self._stopping:
                return
            results = await asyncio.gather(
                *(self._start_subscriber(label, subscriber) for label, subscriber in pending),
            )
            next_pending = [
                subscriber_spec
                for subscriber_spec, started in zip(pending, results, strict=True)
                if not started
            ]
            pending = next_pending


def _safe_consumer_suffix(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-").lower()


def _secret_value(name: str) -> str:
    """Resolve an explicitly named runtime secret.

    Behavioral configuration is typed; environment access remains only for secret
    indirection so credentials do not need to be stored in config files.
    """
    return os.environ.get(name, "") if name else ""


def _telemetry_stream_specs(config: ValkyrieTelemetryConfig) -> list[dict[str, str]]:
    streams_raw = config.nats_streams.strip()
    if not streams_raw:
        return [
            {
                "stream_name": config.nats_stream,
                "subject_prefix": config.subject_prefix,
                "user": config.nats_user,
                "password_env": config.nats_password_env,
            }
        ]

    specs: list[dict[str, str]] = []
    for raw_entry in streams_raw.replace("\n", ",").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        stream_name = parts[0]
        if not stream_name:
            continue
        specs.append(
            {
                "stream_name": stream_name,
                "subject_prefix": (
                    parts[1] if len(parts) > 1 and parts[1] else config.subject_prefix
                ),
                "user": parts[2] if len(parts) > 2 and parts[2] else config.nats_user,
                "password_env": (
                    parts[3]
                    if len(parts) > 3 and parts[3]
                    else config.nats_password_env
                ),
            }
        )
    return specs


def _command_stream_specs(
    config: ValkyrieCommandConfig,
    telemetry: ValkyrieTelemetryConfig,
) -> list[dict[str, str]]:
    streams_raw = config.nats_streams.strip()
    if not streams_raw:
        return []
    default_stream = config.nats_stream or telemetry.nats_stream
    default_prefix = config.subject_prefix or telemetry.subject_prefix
    default_user = config.nats_user or telemetry.nats_user
    default_password_env = (
        config.nats_password_env
        if _secret_value(config.nats_password_env)
        else telemetry.nats_password_env
    )

    specs: list[dict[str, str]] = []
    for raw_entry in streams_raw.replace("\n", ",").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        stream_name = parts[0] if parts and parts[0] else default_stream
        subject_prefix = parts[1] if len(parts) > 1 and parts[1] else default_prefix
        user = parts[2] if len(parts) > 2 and parts[2] else default_user
        password_env = parts[3] if len(parts) > 3 and parts[3] else default_password_env
        specs.append(
            {
                "stream_name": stream_name,
                "subject_prefix": subject_prefix,
                "user": user,
                "password_env": password_env,
                "mode": "core" if stream_name.lower() == "core" else "jetstream",
                "label": (
                    f"core/{subject_prefix}"
                    if stream_name.lower() == "core"
                    else f"{stream_name}/{subject_prefix}"
                ),
            }
        )
    return specs


def build_nats_telemetry_subscription_from_env(
    projection: ValkyrieDashboardProjection,
    review_ingest: Any | None = None,
    history_ingest: Any | None = None,
    skills_ingest: Any | None = None,
    config: ValkyrieTelemetryConfig | None = None,
) -> ValkyrieTelemetrySubscription | None:
    """Build optional dashboard telemetry from validated Ravn settings."""
    loaded = config or Settings().valkyrie.telemetry
    if not loaded.nats_url.strip():
        return None

    from sleipnir.adapters.nats_transport import NatsSubscriber  # noqa: PLC0415

    servers = [entry.strip() for entry in loaded.nats_url.split(",") if entry.strip()]
    replay_from_time = (
        datetime.now(UTC) - timedelta(seconds=loaded.replay_seconds)
        if loaded.replay_seconds > 0
        else None
    )
    subscribers = []
    for spec in _telemetry_stream_specs(loaded):
        stream_name = spec["stream_name"]
        consumer_suffix = _safe_consumer_suffix(stream_name)
        subscribers.append(
            (
                "{}/{}".format(stream_name, spec["subject_prefix"]),
                NatsSubscriber(
                    servers=servers,
                    stream_name=stream_name,
                    jetstream_domain=loaded.nats_jetstream_domain,
                    subject_prefix=spec["subject_prefix"],
                    consumer_group=f"{loaded.consumer_group}-{consumer_suffix}",
                    replay_from_time=replay_from_time,
                    connect_timeout_s=loaded.connect_timeout_seconds,
                    max_reconnect_attempts=loaded.nats_max_reconnect_attempts,
                    ensure_stream=False,
                    tls_ca_file=loaded.tls_ca_file,
                    tls_cert_file=loaded.tls_cert_file,
                    tls_key_file=loaded.tls_key_file,
                    tls_hostname=loaded.tls_hostname,
                    tls_handshake_first=loaded.tls_handshake_first,
                    tls_insecure_skip_verify=loaded.tls_insecure_skip_verify,
                    user=spec["user"],
                    password=_secret_value(spec["password_env"]),
                    token=_secret_value(loaded.nats_token_env),
                    nkeys_seed_file=loaded.nkeys_seed_file,
                    nkeys_seed=_secret_value(loaded.nkeys_seed_env),
                ),
            )
        )

    return ValkyrieTelemetrySubscription(
        projection=projection,
        review_ingest=review_ingest,
        history_ingest=history_ingest,
        skills_ingest=skills_ingest,
        subscribers=subscribers,
        retry_interval_seconds=loaded.retry_seconds,
        startup_delay_seconds=loaded.startup_delay_seconds,
        subscriber_start_timeout_seconds=loaded.start_timeout_seconds,
        event_types=["*"],
    )


def build_nats_review_command_publisher_from_env(
    config: ValkyrieCommandConfig | None = None,
    telemetry_config: ValkyrieTelemetryConfig | None = None,
) -> OdinReviewCommandPublisher:
    """Build the optional learning-command publisher from validated settings."""
    if config is None or telemetry_config is None:
        settings = Settings()
        loaded = config or settings.valkyrie.command
        telemetry = telemetry_config or settings.valkyrie.telemetry
    else:
        loaded = config
        telemetry = telemetry_config
    command_inherits_telemetry = not loaded.nats_url.strip()
    servers_raw = loaded.nats_url.strip() or telemetry.nats_url.strip()
    if not servers_raw:
        return OdinReviewCommandPublisher()

    from sleipnir.adapters.nats_transport import NatsCorePublisher, NatsPublisher  # noqa: PLC0415

    servers = [entry.strip() for entry in servers_raw.split(",") if entry.strip()]
    stream_specs = _command_stream_specs(loaded, telemetry)
    shared_options = {
        "servers": servers,
        "jetstream_domain": loaded.nats_jetstream_domain
        or (telemetry.nats_jetstream_domain if command_inherits_telemetry else ""),
        "ensure_stream": loaded.ensure_stream,
        "connect_timeout_s": loaded.connect_timeout_seconds,
        "max_reconnect_attempts": loaded.max_reconnect_attempts,
        "tls_ca_file": loaded.tls_ca_file
        or (telemetry.tls_ca_file if command_inherits_telemetry else ""),
        "tls_cert_file": loaded.tls_cert_file
        or (telemetry.tls_cert_file if command_inherits_telemetry else ""),
        "tls_key_file": loaded.tls_key_file
        or (telemetry.tls_key_file if command_inherits_telemetry else ""),
        "tls_hostname": loaded.tls_hostname
        or (telemetry.tls_hostname if command_inherits_telemetry else ""),
        "tls_handshake_first": (
            loaded.tls_handshake_first
            if loaded.tls_handshake_first is not None
            else telemetry.tls_handshake_first if command_inherits_telemetry else False
        ),
        "tls_insecure_skip_verify": (
            loaded.tls_insecure_skip_verify
            if loaded.tls_insecure_skip_verify is not None
            else telemetry.tls_insecure_skip_verify if command_inherits_telemetry else False
        ),
        "token": _secret_value(loaded.nats_token_env)
        or (_secret_value(telemetry.nats_token_env) if command_inherits_telemetry else ""),
        "nkeys_seed_file": loaded.nkeys_seed_file,
        "nkeys_seed": _secret_value(loaded.nkeys_seed_env),
    }
    if stream_specs:
        core_options = {
            key: value
            for key, value in shared_options.items()
            if key not in {"jetstream_domain", "ensure_stream"}
        }
        targets = [
            _CommandTarget(
                label=spec["label"],
                publisher=(
                    NatsCorePublisher(
                        **core_options,
                        subject_prefix=spec["subject_prefix"],
                        user=spec["user"],
                        password=_secret_value(spec["password_env"]),
                    )
                    if spec.get("mode") == "core"
                    else NatsPublisher(
                        **shared_options,
                        stream_name=spec["stream_name"],
                        subject_prefix=spec["subject_prefix"],
                        user=spec["user"],
                        password=_secret_value(spec["password_env"]),
                    )
                ),
            )
            for spec in stream_specs
        ]
        return OdinReviewCommandPublisher(
            _FanoutSleipnirPublisher(targets),
            start_timeout_seconds=loaded.start_timeout_seconds,
        )

    publisher = NatsPublisher(
        stream_name=loaded.nats_stream or telemetry.nats_stream,
        subject_prefix=loaded.subject_prefix or telemetry.subject_prefix,
        **shared_options,
        user=loaded.nats_user or telemetry.nats_user,
        password=_secret_value(loaded.nats_password_env)
        or (_secret_value(telemetry.nats_password_env) if command_inherits_telemetry else ""),
    )
    return OdinReviewCommandPublisher(
        publisher,
        start_timeout_seconds=loaded.start_timeout_seconds,
    )
