"""Composition of the Guild push gateway from ``push.apns`` and ``push.gateway``.

Kept out of ``main.py`` so the wiring can be built (and tested) from config alone.
Everything the configuration asks for is checked here, at startup: an unreadable
signing key, a missing HTTP/2 library or an unreadable reader token raises with
the setting to fix. A gateway that is configured but cannot work never starts
half-working.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from volundr.adapters.outbound.apns_client import (
    ApnsClient,
    ApnsConfigurationError,
    load_signing_key,
)
from volundr.adapters.outbound.http_guild_feed import HttpGuildFeed
from volundr.adapters.outbound.postgres_push_gateway import PostgresPushDeliveryRepository
from volundr.config import ApnsConfig, PushGatewayConfig, PushNotificationConfig
from volundr.domain.ports import DeviceTokenRepository, EventBroadcaster, PushTokenRegistry
from volundr.domain.services.push_gateway import (
    AlertSettings,
    BackgroundSettings,
    LiveActivitySettings,
    PushGateway,
    PushGatewaySettings,
    WidgetSettings,
)


def create_apns_client(config: ApnsConfig) -> ApnsClient:
    """Build the APNs client; raises when the key or the HTTP/2 stack is unusable."""
    private_key = load_signing_key(config.private_key_path)
    try:
        return ApnsClient(
            team_id=config.team_id,
            key_id=config.key_id,
            private_key=private_key,
            production_url=config.production_url,
            sandbox_url=config.sandbox_url,
            token_ttl_seconds=config.token_ttl_seconds,
            request_timeout_seconds=config.request_timeout_seconds,
        )
    except ImportError as exc:
        raise ApnsConfigurationError(
            "APNs needs HTTP/2 but the 'h2' package is not installed: install "
            "'httpx[http2]' (it is a dependency of this project) or disable push.gateway"
        ) from exc


def read_reader_token(path: str) -> str:
    """The bearer token the gateway reads the Guild with; empty when none is set."""
    if not path:
        return ""
    try:
        token = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(
            f"push.gateway.reader_token_path {path} cannot be read ({type(exc).__name__}): "
            "fix the path or its permissions, or leave it empty for an AllowAll Guild"
        ) from None
    if not token:
        raise ValueError(f"push.gateway.reader_token_path {path} is empty")
    return token


def push_gateway_settings(push: PushNotificationConfig) -> PushGatewaySettings:
    gateway: PushGatewayConfig = push.gateway
    alerts, live, widgets = gateway.alerts, gateway.live_activity, gateway.widgets
    background = gateway.background
    return PushGatewaySettings(
        bundle_id=push.apns.bundle_id,
        poll_interval_seconds=gateway.poll_interval_seconds,
        stream_enabled=gateway.stream_enabled,
        stream_retry_seconds=gateway.stream_retry_seconds,
        page_size=gateway.page_size,
        feed_page_size=gateway.feed_page_size,
        catch_up_items=gateway.catch_up_items,
        discovery_interval_seconds=gateway.discovery_interval_seconds,
        node_retry_seconds=gateway.node_retry_seconds,
        feedless_recheck_seconds=gateway.feedless_recheck_seconds,
        retention_seconds=gateway.retention_seconds,
        public_api_url=gateway.public_api_url.strip(),
        origin_label=gateway.origin_label.strip(),
        alerts=AlertSettings(
            enabled=alerts.enabled,
            kinds=frozenset(kind.value for kind in alerts.kinds),
            min_severity=alerts.min_severity,
            sources=frozenset(source.value for source in alerts.sources),
            attention_kinds=frozenset(kind.value for kind in alerts.attention_kinds),
            low_priority_kinds=frozenset(kind.value for kind in alerts.low_priority_kinds),
            body_chars=alerts.body_chars,
            expiration_seconds=alerts.expiration_seconds,
            max_age_seconds=alerts.max_age_seconds,
            rate_limit=alerts.rate_limit,
            quiet_hours=alerts.quiet_hours,
            category=alerts.category,
            sound=alerts.sound,
            deep_link_base=alerts.deep_link_base,
            session_subtitle=alerts.session_subtitle,
            batch_size=alerts.batch_size,
            lease_seconds=alerts.lease_seconds,
            max_attempts=alerts.max_attempts,
            backoff_base_seconds=alerts.backoff_base_seconds,
            backoff_max_seconds=alerts.backoff_max_seconds,
        ),
        live_activity=LiveActivitySettings(
            enabled=live.enabled,
            priority=live.priority,
            stale_seconds=live.stale_seconds,
            start_enabled=live.start.enabled,
            start_min_interval_seconds=live.start.min_interval_seconds,
            relevance_score=live.relevance_score,
        ),
        widgets=WidgetSettings(
            enabled=widgets.enabled,
            priority=widgets.priority,
            min_interval_seconds=widgets.min_interval_seconds,
        ),
        background=BackgroundSettings(
            enabled=background.enabled,
            priority=background.priority,
            min_interval_seconds=background.min_interval_seconds,
        ),
    )


def create_push_gateway(
    push: PushNotificationConfig,
    pool: Any,
    device_repository: DeviceTokenRepository,
    *,
    local_events: EventBroadcaster | None = None,
) -> PushGateway | None:
    """Build the push gateway, or ``None`` when ``push.gateway`` is switched off.

    ``local_events`` is this node's own event broadcaster: its notifications then
    wake the gateway at once, without a connection to the Guild.
    """
    if not push.enabled or not push.gateway.enabled:
        return None
    if not isinstance(device_repository, PushTokenRegistry):
        raise ValueError(
            "push.gateway.enabled needs the device_tokens table (token kinds and push "
            "throttles); the push-device integrations hold alert tokens only. Set "
            "push.device_registry: postgres, or disable push.gateway"
        )
    gateway = push.gateway
    return PushGateway(
        HttpGuildFeed(
            guild_url=gateway.guild_url.strip(),
            timeout_seconds=gateway.request_timeout_seconds,
            reader_token=read_reader_token(gateway.reader_token_path.strip()),
        ),
        device_repository,
        PostgresPushDeliveryRepository(pool, max_detail_chars=gateway.max_detail_chars),
        create_apns_client(push.apns),
        push_gateway_settings(push),
        local_events=local_events,
    )
