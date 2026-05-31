"""Tests for the Volundr-hosted Ravn persona routes."""

from __future__ import annotations

from collections import defaultdict
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.adapters.personas.postgres_registry import (
    PersonaView,
    _config_to_payload,
    _normalize_payload,
    _payload_to_config,
)
from volundr.adapters.inbound.rest_ravn_personas import create_ravn_personas_router
from volundr.domain.models import Principal, User, UserStatus


class _InMemoryPersonaRegistry:
    def __init__(self) -> None:
        self._builtin_loader = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
        self._overrides: dict[str, dict[str, dict]] = defaultdict(dict)

    async def list_personas(self, owner_id: str, *, source: str = "all") -> list[PersonaView]:
        active_builtin_names = set(self._builtin_loader.list_builtin_names())
        custom_override_names = {
            name
            for name in self._overrides[owner_id]
            if not self._builtin_loader.is_builtin(name)
        }
        names = active_builtin_names | custom_override_names
        views: list[PersonaView] = []
        for name in sorted(names):
            view = await self.get_persona(owner_id, name)
            if view is None:
                continue
            if source == "builtin" and not view.is_builtin:
                continue
            if source == "custom" and view.is_builtin:
                continue
            views.append(view)
        return views

    async def get_persona(self, owner_id: str, name: str) -> PersonaView | None:
        override = self._overrides[owner_id].get(name)
        builtin_config = self._builtin_loader.load(name)
        is_builtin = builtin_config is not None

        if override is not None:
            payload = _normalize_payload(override, fallback=builtin_config)
            config = _payload_to_config(payload)
            return PersonaView(
                config=config,
                payload=payload,
                is_builtin=is_builtin,
                has_override=True,
                yaml_source="[built-in]" if is_builtin else f"[user:{owner_id}]",
                override_source=f"[user:{owner_id}]" if is_builtin else None,
            )

        if builtin_config is None:
            return None

        return PersonaView(
            config=builtin_config,
            payload=_config_to_payload(builtin_config),
            is_builtin=True,
            has_override=False,
            yaml_source="[built-in]",
            override_source=None,
        )

    async def save_persona(self, owner_id: str, payload: dict) -> None:
        normalized = _normalize_payload(payload)
        self._overrides[owner_id][normalized["name"]] = normalized

    async def delete_persona(self, owner_id: str, name: str) -> bool:
        return self._overrides[owner_id].pop(name, None) is not None

    async def get_persona_yaml(self, owner_id: str, name: str) -> str | None:
        view = await self.get_persona(owner_id, name)
        if view is None:
            return None
        return FilesystemPersonaAdapter.to_yaml(view.config)

    def is_builtin(self, name: str) -> bool:
        return self._builtin_loader.is_builtin(name)


def _make_client() -> tuple[TestClient, _InMemoryPersonaRegistry]:
    registry = _InMemoryPersonaRegistry()
    identity = AsyncMock()
    identity.validate_token.return_value = Principal(
        user_id="user-1",
        email="user1@example.com",
        tenant_id="default",
        roles=["volundr:developer"],
    )
    identity.get_or_provision_user.return_value = User(
        id="user-1",
        email="user1@example.com",
        status=UserStatus.ACTIVE,
    )

    app = FastAPI()
    app.state.identity = identity
    app.state.authorization = AsyncMock()
    app.include_router(create_ravn_personas_router(registry))
    return TestClient(app, raise_server_exceptions=True), registry


def _make_payload(name: str) -> dict:
    return {
        "name": name,
        "role": "build",
        "letter": "C",
        "color": "var(--color-accent-indigo)",
        "summary": "Custom coding persona",
        "description": "Custom coding persona for testing.",
        "system_prompt_template": "You are a custom coding persona.",
        "allowed_tools": ["read", "write"],
        "forbidden_tools": [],
        "permission_mode": "default",
        "iteration_budget": 12,
        "llm_thinking_enabled": True,
        "llm_max_tokens": 8192,
        "produces_event_type": "code.changed",
        "produces_schema": {"file": "string"},
        "consumes_events": [{"name": "code.requested", "injects": ["repo"]}],
        "consumes_schema": {"topic": "string", "max_sources": "number"},
        "fan_in_strategy": "merge",
        "fan_in_params": {},
        "mimir_write_routing": "local",
    }


class TestRavnPersonaRoutes:
    def test_create_get_list_and_yaml(self) -> None:
        client, _ = _make_client()

        create_resp = client.post(
            "/api/v1/personas",
            json=_make_payload("custom-agent"),
            headers={"Authorization": "Bearer token"},
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["name"] == "custom-agent"
        assert created["role"] == "build"
        assert created["mimir_write_routing"] == "local"
        assert created["consumes"]["events"][0]["name"] == "code.requested"
        assert created["consumes"]["schema_def"] == {
            "topic": "string",
            "max_sources": "number",
        }

        list_resp = client.get(
            "/api/v1/personas?source=custom",
            headers={"Authorization": "Bearer token"},
        )
        assert list_resp.status_code == 200
        assert [item["name"] for item in list_resp.json()] == ["custom-agent"]

        detail_resp = client.get(
            "/api/v1/personas/custom-agent",
            headers={"Authorization": "Bearer token"},
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["yaml_source"] == "[user:user-1]"

        yaml_resp = client.get(
            "/api/v1/personas/custom-agent/yaml",
            headers={"Authorization": "Bearer token"},
        )
        assert yaml_resp.status_code == 200
        assert "name: custom-agent" in yaml_resp.text
        assert "permission_mode: workspace-write" in yaml_resp.text

    def test_legacy_ravn_routes_still_work(self) -> None:
        client, _ = _make_client()
        client.post(
            "/api/v1/personas",
            json=_make_payload("legacy-agent"),
            headers={"Authorization": "Bearer token"},
        )

        resp = client.get(
            "/api/v1/ravn/personas/legacy-agent",
            headers={"Authorization": "Bearer token"},
        )

        assert resp.status_code == 200
        assert resp.json()["name"] == "legacy-agent"

    def test_builtin_override_and_delete_restore_builtin(self) -> None:
        client, _ = _make_client()
        payload = _make_payload("coding-agent") | {
            "summary": "Overridden coding agent",
            "system_prompt_template": "Override built-in coding agent.",
        }

        put_resp = client.put(
            "/api/v1/ravn/personas/coding-agent",
            json=payload,
            headers={"Authorization": "Bearer token"},
        )
        assert put_resp.status_code == 200
        updated = put_resp.json()
        assert updated["is_builtin"] is True
        assert updated["has_override"] is True
        assert updated["yaml_source"] == "[built-in]"
        assert updated["override_source"] == "[user:user-1]"

        delete_resp = client.delete(
            "/api/v1/ravn/personas/coding-agent",
            headers={"Authorization": "Bearer token"},
        )
        assert delete_resp.status_code == 204

        detail_resp = client.get(
            "/api/v1/ravn/personas/coding-agent",
            headers={"Authorization": "Bearer token"},
        )
        assert detail_resp.status_code == 200
        restored = detail_resp.json()
        assert restored["is_builtin"] is True
        assert restored["has_override"] is False
        assert restored["override_source"] is None

    def test_user_scoping_isolated(self) -> None:
        client, registry = _make_client()
        registry._overrides["user-1"]["scoped-agent"] = _normalize_payload(
            _make_payload("scoped-agent")
        )
        registry._overrides["user-2"]["other-agent"] = _normalize_payload(
            _make_payload("other-agent")
        )

        user_one = client.get(
            "/api/v1/ravn/personas?source=custom",
            headers={"Authorization": "Bearer token"},
        )
        assert user_one.status_code == 200
        assert {item["name"] for item in user_one.json()} == {"scoped-agent"}

        client.app.state.identity.validate_token.return_value = Principal(
            user_id="user-2",
            email="user2@example.com",
            tenant_id="default",
            roles=["volundr:developer"],
        )
        client.app.state.identity.get_or_provision_user.return_value = User(
            id="user-2",
            email="user2@example.com",
            status=UserStatus.ACTIVE,
        )

        user_two = client.get(
            "/api/v1/ravn/personas?source=custom",
            headers={"Authorization": "Bearer token"},
        )
        assert user_two.status_code == 200
        assert {item["name"] for item in user_two.json()} == {"other-agent"}

        missing = client.get(
            "/api/v1/ravn/personas/scoped-agent",
            headers={"Authorization": "Bearer token"},
        )
        assert missing.status_code == 404


def test_validate_rejects_overlapping_tool_lists() -> None:
    client, _ = _make_client()
    payload = _make_payload("validation-agent")
    payload["forbidden_tools"] = ["read"]

    resp = client.post(
        "/api/v1/ravn/personas/validate",
        json=payload,
        headers={"Authorization": "Bearer token"},
    )

    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert "allowed_tools and forbidden_tools overlap" in resp.json()["errors"][0]
