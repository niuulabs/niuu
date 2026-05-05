"""Feature module service — merges config catalog with DB overrides."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from volundr.config import FeatureModuleConfig

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


@dataclass(frozen=True)
class FeatureModule:
    """A resolved feature module with admin override applied."""

    key: str
    label: str
    icon: str
    scope: str
    enabled: bool
    default_enabled: bool
    admin_only: bool
    order: int


@dataclass(frozen=True)
class UserFeaturePreference:
    """Per-user visibility and ordering for a feature module."""

    feature_key: str
    visible: bool
    sort_order: int


class FeatureService:
    """Manages the feature module catalog, admin toggles, and user preferences.

    The catalog is defined in config.yaml. Admin overrides and user preferences
    are persisted in PostgreSQL.
    """

    def __init__(
        self,
        pool,
        feature_configs: list[FeatureModuleConfig],
    ) -> None:
        self._pool = pool
        self._configs = {f.key: f for f in feature_configs}

    async def get_catalog(
        self,
        scope: str | None = None,
        include_disabled: bool = False,
    ) -> list[FeatureModule]:
        """Return the feature catalog, merging config defaults with DB overrides.

        Args:
            scope: Filter by scope ('admin' or 'user'). None returns all.
            include_disabled: If True, include admin-disabled features.
        """
        # Fetch admin overrides from DB
        overrides = await self._get_admin_overrides()

        modules: list[FeatureModule] = []
        for cfg in self._configs.values():
            if scope is not None and cfg.scope != scope:
                continue

            enabled = overrides.get(cfg.key, cfg.default_enabled)
            if not include_disabled and not enabled:
                continue

            modules.append(
                FeatureModule(
                    key=cfg.key,
                    label=cfg.label,
                    icon=cfg.icon,
                    scope=cfg.scope,
                    enabled=enabled,
                    default_enabled=cfg.default_enabled,
                    admin_only=cfg.admin_only,
                    order=cfg.order,
                )
            )

        modules.sort(key=lambda m: m.order)
        return modules

    async def set_feature_enabled(self, key: str, enabled: bool) -> None:
        """Admin toggle: enable or disable a feature globally."""
        if key not in self._configs:
            raise ValueError(f"Unknown feature key: {key}")

        await self._pool.execute(
            """
            INSERT INTO feature_toggles (feature_key, enabled, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (feature_key)
            DO UPDATE SET enabled = $2, updated_at = NOW()
            """,
            key,
            enabled,
        )
        logger.info(
            "Feature '%s' set to enabled=%s",
            _sanitize_log(key),
            _sanitize_log(enabled),
        )

    async def get_user_preferences(
        self,
        user_id: str,
    ) -> list[UserFeaturePreference]:
        """Get a user's feature layout preferences."""
        rows = await self._pool.fetch(
            """
            SELECT feature_key, visible, sort_order
            FROM user_feature_preferences
            WHERE user_id = $1
            ORDER BY sort_order
            """,
            user_id,
        )
        return [
            UserFeaturePreference(
                feature_key=row["feature_key"],
                visible=row["visible"],
                sort_order=row["sort_order"],
            )
            for row in rows
        ]

    async def update_user_preferences(
        self,
        user_id: str,
        preferences: list[UserFeaturePreference],
    ) -> list[UserFeaturePreference]:
        """Bulk update a user's feature layout preferences.

        Replaces all existing preferences for the user.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM user_feature_preferences WHERE user_id = $1",
                    user_id,
                )
                for pref in preferences:
                    await conn.execute(
                        """
                        INSERT INTO user_feature_preferences
                            (user_id, feature_key, visible, sort_order)
                        VALUES ($1, $2, $3, $4)
                        """,
                        user_id,
                        pref.feature_key,
                        pref.visible,
                        pref.sort_order,
                    )

        logger.info(
            "Updated %d feature preferences for user %s",
            len(preferences),
            user_id,
        )
        return await self.get_user_preferences(user_id)

    async def _get_admin_overrides(self) -> dict[str, bool]:
        """Fetch admin override map from DB."""
        rows = await self._pool.fetch(
            "SELECT feature_key, enabled FROM feature_toggles",
        )
        return {row["feature_key"]: row["enabled"] for row in rows}
