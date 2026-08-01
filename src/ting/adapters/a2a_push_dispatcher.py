"""Durable A2A task push delivery."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from urllib.parse import urlsplit

import httpx
from a2a.types import TaskPushNotificationConfig
from google.protobuf.json_format import MessageToDict

from ting.domain.models import WorkflowCampaign
from ting.ports.a2a_push import A2APushConfigRepositoryPort, A2APushDelivery

logger = logging.getLogger(__name__)


def callback_origin(url: str) -> str:
    """Return a normalized HTTPS origin, rejecting unsafe callback URLs."""
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("A2A push callback URL must use HTTPS")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("A2A push callback URL must not contain userinfo or a fragment")
    host = parsed.hostname.lower()
    port = parsed.port
    return f"https://{host}{f':{port}' if port and port != 443 else ''}"


class A2APushDispatcher:
    """Queue the latest task state and retry callbacks from a durable outbox."""

    def __init__(
        self,
        *,
        repo: A2APushConfigRepositoryPort,
        allowed_callback_origins: list[str],
        timeout_seconds: float,
        poll_seconds: float,
        retry_initial_seconds: float,
        retry_max_seconds: float,
        claim_limit: int,
        lease_seconds: float,
        max_url_chars: int,
        max_credential_chars: int,
        max_configs_page_size: int,
    ) -> None:
        self._repo = repo
        self._allowed_origins = frozenset(
            callback_origin(origin) for origin in allowed_callback_origins
        )
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._poll_seconds = poll_seconds
        self._retry_initial_seconds = retry_initial_seconds
        self._retry_max_seconds = retry_max_seconds
        self._claim_limit = claim_limit
        self._lease_seconds = lease_seconds
        self._max_url_chars = max_url_chars
        self._max_credential_chars = max_credential_chars
        self._max_configs_page_size = max_configs_page_size
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return bool(self._allowed_origins)

    @property
    def max_configs_page_size(self) -> int:
        return self._max_configs_page_size

    def validate_config(self, config: TaskPushNotificationConfig) -> None:
        if len(config.url) > self._max_url_chars:
            raise ValueError(f"A2A push callback URL exceeds {self._max_url_chars} characters")
        if (
            len(config.token) > self._max_credential_chars
            or len(config.authentication.credentials) > self._max_credential_chars
        ):
            raise ValueError(
                f"A2A push callback credential exceeds {self._max_credential_chars} characters"
            )
        origin = callback_origin(config.url)
        if origin not in self._allowed_origins:
            raise ValueError(f"A2A push callback origin is not allowed: {origin}")
        scheme = config.authentication.scheme.strip().lower()
        if scheme and scheme != "bearer":
            raise ValueError("A2A push callback authentication supports bearer only")

    async def save_config(
        self,
        *,
        task_id: str,
        owner_id: str,
        config: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig:
        return await self._repo.save_config(task_id=task_id, owner_id=owner_id, config=config)

    async def get_config(self, *, task_id: str, owner_id: str, config_id: str):
        return await self._repo.get_for_owner(
            task_id=task_id,
            owner_id=owner_id,
            config_id=config_id,
        )

    async def list_configs(self, *, task_id: str, owner_id: str):
        return await self._repo.list_for_owner(task_id=task_id, owner_id=owner_id)

    async def delete_config(self, *, task_id: str, owner_id: str, config_id: str) -> bool:
        return await self._repo.delete_for_owner(
            task_id=task_id,
            owner_id=owner_id,
            config_id=config_id,
        )

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="a2a-push-dispatcher")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._client.aclose()

    async def queue_campaign(self, campaign: WorkflowCampaign) -> int:
        # Local import avoids making the domain projector depend on the API module.
        from a2a.utils.proto_utils import to_stream_response  # noqa: PLC0415

        from ting.api.a2a import campaign_to_task  # noqa: PLC0415

        payload = MessageToDict(to_stream_response(campaign_to_task(campaign)))
        return await self._repo.queue_event(campaign.slug, payload)

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    deliveries = await self._repo.claim_due(
                        limit=self._claim_limit,
                        lease_seconds=self._lease_seconds,
                    )
                    if deliveries:
                        await asyncio.gather(*(self._deliver(item) for item in deliveries))
                except Exception:
                    logger.exception("A2A push outbox pass failed")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    async def _deliver(self, delivery: A2APushDelivery) -> None:
        config = delivery.config
        try:
            self.validate_config(config)
            headers: dict[str, str] = {"A2A-Version": "1.0"}
            if config.token:
                headers["X-A2A-Notification-Token"] = config.token
            if config.authentication.scheme.lower() == "bearer":
                headers["Authorization"] = f"Bearer {config.authentication.credentials}"
            response = await self._client.post(config.url, json=delivery.payload, headers=headers)
            response.raise_for_status()
        except Exception as exc:
            attempts = delivery.attempt_count + 1
            delay = self._retry_initial_seconds
            for _attempt in range(max(0, attempts - 1)):
                if delay >= self._retry_max_seconds:
                    break
                delay = min(self._retry_max_seconds, delay * 2)
            await self._repo.retry_later(
                delivery,
                delay_seconds=delay,
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning(
                "A2A push delivery failed task=%s config=%s origin=%s attempt=%d",
                delivery.task_id,
                delivery.config_id,
                _safe_origin(config.url),
                attempts,
            )
            return
        await self._repo.mark_delivered(delivery)
        logger.info(
            "A2A push delivered task=%s config=%s origin=%s",
            delivery.task_id,
            delivery.config_id,
            _safe_origin(config.url),
        )


def _safe_origin(url: str) -> str:
    try:
        return callback_origin(url)
    except ValueError:
        return "invalid"
