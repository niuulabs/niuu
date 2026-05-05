"""Tests for Tyr settings compatibility endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.api.flock_config import create_flock_config_router
from tyr.api.settings import create_settings_router
from tyr.config import AuthConfig, FlockConfig, NotificationConfig, Settings, WatcherConfig


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(create_settings_router())
    app.include_router(create_flock_config_router())
    settings = Settings(
        auth=AuthConfig(allow_anonymous_dev=True),
        notification=NotificationConfig(enabled=True, confidence_threshold=0.4),
        watcher=WatcherConfig(batch_size=12),
    )
    settings.dispatch.flock = FlockConfig()
    app.state.settings = settings
    return TestClient(app)


class TestSettingsAPI:
    def test_get_flock_settings_returns_defaults(self) -> None:
        client = _make_client()

        response = client.get("/api/v1/tyr/settings/flock")

        assert response.status_code == 200
        body = response.json()
        assert body["flock_name"] == "default"
        assert body["default_base_branch"] == "main"
        assert body["default_tracker_type"] == "linear"
        assert body["auto_create_milestones"] is True

    def test_get_notifications_returns_frontend_valid_channel(self) -> None:
        client = _make_client()

        response = client.get("/api/v1/tyr/settings/notifications")

        assert response.status_code == 200
        assert response.json()["channel"] == "telegram"

    def test_patch_dispatch_defaults_updates_nested_retry_policy(self) -> None:
        client = _make_client()

        response = client.patch(
            "/api/v1/tyr/settings/dispatch",
            json={
                "confidence_threshold": 75,
                "auto_continue": True,
                "retry_policy": {
                    "max_retries": 5,
                    "retry_delay_seconds": 60,
                },
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["confidence_threshold"] == 75
        assert body["auto_continue"] is True
        assert body["retry_policy"]["max_retries"] == 5
        assert body["retry_policy"]["retry_delay_seconds"] == 60
        assert client.app.state.settings.notification.confidence_threshold == 75
        assert client.app.state.settings.watcher.batch_size == 12

    def test_patch_notification_settings_is_persisted_in_process(self) -> None:
        client = _make_client()

        patch_response = client.patch(
            "/api/v1/tyr/settings/notifications",
            json={
                "channel": "webhook",
                "on_dispatcher_error": False,
                "webhook_url": "https://hooks.example.test/tyr",
            },
        )
        get_response = client.get("/api/v1/tyr/settings/notifications")

        assert patch_response.status_code == 200
        assert get_response.status_code == 200
        body = get_response.json()
        assert body["channel"] == "webhook"
        assert body["on_dispatcher_error"] is False
        assert body["webhook_url"] == "https://hooks.example.test/tyr"

    def test_patch_notification_settings_requires_webhook_url_for_webhook_channel(self) -> None:
        client = _make_client()

        response = client.patch(
            "/api/v1/tyr/settings/notifications",
            json={"channel": "webhook"},
        )

        assert response.status_code == 422

    def test_patch_notification_settings_clears_webhook_when_channel_changes(self) -> None:
        client = _make_client()

        client.patch(
            "/api/v1/tyr/settings/notifications",
            json={
                "channel": "webhook",
                "webhook_url": "https://hooks.example.test/tyr",
            },
        )
        response = client.patch(
            "/api/v1/tyr/settings/notifications",
            json={"channel": "none"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["channel"] == "none"
        assert body["webhook_url"] is None
        assert client.app.state.settings.notification.enabled is False

    def test_operator_flock_settings_do_not_overwrite_runtime_flock_config(self) -> None:
        client = _make_client()

        patch_response = client.patch(
            "/api/v1/tyr/settings/flock",
            json={"flock_name": "Core Platform"},
        )
        runtime_response = client.get("/api/v1/tyr/flock/config")

        assert patch_response.status_code == 200
        assert runtime_response.status_code == 200
        assert runtime_response.json()["flock_enabled"] is False
