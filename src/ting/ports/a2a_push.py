"""Port for durable A2A task push registration and delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from a2a.types import TaskPushNotificationConfig

from ting.domain.models import WorkflowCampaign


@dataclass(frozen=True)
class A2APushDelivery:
    """One leased callback delivery from the durable outbox."""

    task_id: str
    config_id: str
    owner_id: str
    config: TaskPushNotificationConfig
    payload: dict[str, Any]
    delivery_version: str
    attempt_count: int


class A2APushConfigRepositoryPort(Protocol):
    async def save_config(
        self,
        *,
        task_id: str,
        owner_id: str,
        config: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig: ...

    async def get_for_owner(
        self,
        *,
        task_id: str,
        owner_id: str,
        config_id: str,
    ) -> TaskPushNotificationConfig | None: ...

    async def list_for_owner(
        self,
        *,
        task_id: str,
        owner_id: str,
    ) -> list[TaskPushNotificationConfig]: ...

    async def delete_for_owner(
        self,
        *,
        task_id: str,
        owner_id: str,
        config_id: str,
    ) -> bool: ...

    async def queue_event(self, task_id: str, payload: dict[str, Any]) -> int: ...

    async def claim_due(
        self,
        *,
        limit: int,
        lease_seconds: float,
    ) -> list[A2APushDelivery]: ...

    async def mark_delivered(self, delivery: A2APushDelivery) -> None: ...

    async def retry_later(
        self,
        delivery: A2APushDelivery,
        *,
        delay_seconds: float,
        error: str,
    ) -> None: ...


class A2APushDispatcherPort(Protocol):
    """Ownership-safe callback registry plus durable task-state outbox."""

    @property
    def enabled(self) -> bool: ...

    @property
    def max_configs_page_size(self) -> int: ...

    def validate_config(self, config: TaskPushNotificationConfig) -> None: ...

    async def save_config(
        self,
        *,
        task_id: str,
        owner_id: str,
        config: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig: ...

    async def get_config(
        self,
        *,
        task_id: str,
        owner_id: str,
        config_id: str,
    ) -> TaskPushNotificationConfig | None: ...

    async def list_configs(
        self,
        *,
        task_id: str,
        owner_id: str,
    ) -> list[TaskPushNotificationConfig]: ...

    async def delete_config(
        self,
        *,
        task_id: str,
        owner_id: str,
        config_id: str,
    ) -> bool: ...

    async def queue_campaign(self, campaign: WorkflowCampaign) -> int: ...
