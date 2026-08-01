"""Encrypted, durable A2A push configurations and delivery outbox."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import asyncpg
from a2a.types import TaskPushNotificationConfig
from cryptography.fernet import Fernet, InvalidToken
from google.protobuf.json_format import MessageToJson, Parse

from ting.ports.a2a_push import A2APushDelivery


class PostgresA2APushConfigRepository:
    """Persist encrypted callback credentials and retryable task events."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        encryption_key: str,
        *,
        max_error_chars: int,
    ) -> None:
        self._pool = pool
        self._max_error_chars = max_error_chars
        try:
            self._fernet = Fernet(encryption_key.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("A2A push encryption key must be a valid Fernet key") from exc

    async def save_config(
        self,
        *,
        task_id: str,
        owner_id: str,
        config: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig:
        saved = TaskPushNotificationConfig()
        saved.CopyFrom(config)
        saved.task_id = task_id
        if not saved.id:
            saved.id = str(uuid4())
        encrypted = self._encrypt(saved)
        await self._pool.execute(
            """
            INSERT INTO a2a_push_notification_configs (
                task_id, config_id, owner_id, config_data
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (task_id, config_id, owner_id) DO UPDATE SET
                config_data = EXCLUDED.config_data,
                updated_at = NOW()
            """,
            task_id,
            saved.id,
            owner_id,
            encrypted,
        )
        return saved

    async def list_for_owner(
        self,
        *,
        task_id: str,
        owner_id: str,
    ) -> list[TaskPushNotificationConfig]:
        rows = await self._pool.fetch(
            """
            SELECT config_data
            FROM a2a_push_notification_configs
            WHERE task_id = $1 AND owner_id = $2
            ORDER BY created_at, config_id
            """,
            task_id,
            owner_id,
        )
        return [self._decrypt(row["config_data"]) for row in rows]

    async def get_for_owner(
        self,
        *,
        task_id: str,
        owner_id: str,
        config_id: str,
    ) -> TaskPushNotificationConfig | None:
        raw = await self._pool.fetchval(
            """
            SELECT config_data
            FROM a2a_push_notification_configs
            WHERE task_id = $1 AND owner_id = $2 AND config_id = $3
            """,
            task_id,
            owner_id,
            config_id,
        )
        return self._decrypt(raw) if raw is not None else None

    async def delete_for_owner(
        self,
        *,
        task_id: str,
        owner_id: str,
        config_id: str,
    ) -> bool:
        result = await self._pool.execute(
            """
            DELETE FROM a2a_push_notification_configs
            WHERE task_id = $1 AND owner_id = $2 AND config_id = $3
            """,
            task_id,
            owner_id,
            config_id,
        )
        return result == "DELETE 1"

    async def queue_event(self, task_id: str, payload: dict[str, Any]) -> int:
        """Replace any older pending snapshot with the latest task state."""
        version = str(uuid4())
        result = await self._pool.execute(
            """
            UPDATE a2a_push_notification_configs
            SET pending_event = $2::jsonb,
                delivery_version = $3,
                next_attempt_at = NOW(),
                attempt_count = 0,
                last_error = NULL,
                delivered_at = NULL,
                updated_at = NOW()
            WHERE task_id = $1
            """,
            task_id,
            json.dumps(payload),
            version,
        )
        return int(result.rsplit(" ", 1)[-1])

    async def claim_due(
        self,
        *,
        limit: int,
        lease_seconds: float,
    ) -> list[A2APushDelivery]:
        """Lease due rows so multiple Ting replicas do not double-send them."""
        lease_until = datetime.now(UTC) + timedelta(seconds=max(1.0, lease_seconds))
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                SELECT task_id, config_id, owner_id, config_data, pending_event,
                       delivery_version, attempt_count
                FROM a2a_push_notification_configs
                WHERE pending_event IS NOT NULL
                  AND next_attempt_at <= NOW()
                ORDER BY next_attempt_at, updated_at
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                max(1, limit),
            )
            for row in rows:
                await conn.execute(
                    """
                    UPDATE a2a_push_notification_configs
                    SET next_attempt_at = $4
                    WHERE task_id = $1 AND config_id = $2 AND owner_id = $3
                    """,
                    row["task_id"],
                    row["config_id"],
                    row["owner_id"],
                    lease_until,
                )

        deliveries: list[A2APushDelivery] = []
        for row in rows:
            payload = row["pending_event"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            deliveries.append(
                A2APushDelivery(
                    task_id=row["task_id"],
                    config_id=row["config_id"],
                    owner_id=row["owner_id"],
                    config=self._decrypt(row["config_data"]),
                    payload=dict(payload or {}),
                    delivery_version=str(row["delivery_version"] or ""),
                    attempt_count=int(row["attempt_count"] or 0),
                )
            )
        return deliveries

    async def mark_delivered(self, delivery: A2APushDelivery) -> None:
        await self._pool.execute(
            """
            UPDATE a2a_push_notification_configs
            SET pending_event = NULL,
                next_attempt_at = NULL,
                last_error = NULL,
                delivered_at = NOW(),
                updated_at = NOW()
            WHERE task_id = $1 AND config_id = $2 AND owner_id = $3
              AND delivery_version = $4
            """,
            delivery.task_id,
            delivery.config_id,
            delivery.owner_id,
            delivery.delivery_version,
        )

    async def retry_later(
        self,
        delivery: A2APushDelivery,
        *,
        delay_seconds: float,
        error: str,
    ) -> None:
        next_attempt = datetime.now(UTC) + timedelta(seconds=max(1.0, delay_seconds))
        await self._pool.execute(
            """
            UPDATE a2a_push_notification_configs
            SET next_attempt_at = $5,
                attempt_count = attempt_count + 1,
                last_error = $6,
                updated_at = NOW()
            WHERE task_id = $1 AND config_id = $2 AND owner_id = $3
              AND delivery_version = $4
            """,
            delivery.task_id,
            delivery.config_id,
            delivery.owner_id,
            delivery.delivery_version,
            next_attempt,
            error[: self._max_error_chars],
        )

    def _encrypt(self, config: TaskPushNotificationConfig) -> bytes:
        return self._fernet.encrypt(MessageToJson(config).encode("utf-8"))

    def _decrypt(self, encrypted: bytes) -> TaskPushNotificationConfig:
        try:
            payload = self._fernet.decrypt(bytes(encrypted)).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("A2A push configuration could not be decrypted") from exc
        return Parse(payload, TaskPushNotificationConfig())
