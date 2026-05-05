"""Integration tests for Volundr-hosted Ravn persona routes."""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/ravn"


def _payload(name: str) -> dict:
    return {
        "name": name,
        "role": "build",
        "letter": "C",
        "color": "var(--color-accent-indigo)",
        "summary": "Custom coding persona",
        "description": "Custom coding persona for integration testing.",
        "system_prompt_template": "You are a custom coding persona.",
        "allowed_tools": ["read", "write"],
        "forbidden_tools": [],
        "permission_mode": "default",
        "iteration_budget": 12,
        "llm_primary_alias": "claude-sonnet-4-6",
        "llm_thinking_enabled": True,
        "llm_max_tokens": 8192,
        "produces_event_type": "code.changed",
        "produces_schema": {"file": "string"},
        "consumes_events": [{"name": "code.requested", "injects": ["repo"]}],
        "fan_in_strategy": "merge",
        "fan_in_params": {},
        "mimir_write_routing": "local",
    }


async def test_create_get_list_and_yaml_persona(volundr_client, auth_headers):
    headers = auth_headers("persona-user-1", "persona1@example.com")
    payload = _payload("integ-persona")

    create_resp = await volundr_client.post(f"{API}/personas", json=payload, headers=headers)
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    assert created["name"] == "integ-persona"
    assert created["role"] == "build"
    assert created["permission_mode"] == "default"
    assert created["produces"]["schema_def"] == {"file": "string"}
    assert created["consumes"]["events"][0]["name"] == "code.requested"
    assert created["mimir_write_routing"] == "local"
    assert created["is_builtin"] is False
    assert created["has_override"] is True

    list_resp = await volundr_client.get(f"{API}/personas?source=custom", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    names = {item["name"] for item in list_resp.json()}
    assert "integ-persona" in names

    detail_resp = await volundr_client.get(f"{API}/personas/integ-persona", headers=headers)
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["yaml_source"] == "[user:persona-user-1]"

    yaml_resp = await volundr_client.get(f"{API}/personas/integ-persona/yaml", headers=headers)
    assert yaml_resp.status_code == 200, yaml_resp.text
    assert "name: integ-persona" in yaml_resp.text
    assert "permission_mode: workspace-write" in yaml_resp.text


async def test_builtin_override_can_be_restored(volundr_client, auth_headers):
    headers = auth_headers("persona-user-2", "persona2@example.com")
    payload = _payload("coding-agent") | {
        "summary": "Overridden built-in coding agent",
        "system_prompt_template": "Override built-in coding agent.",
    }

    put_resp = await volundr_client.put(
        f"{API}/personas/coding-agent", json=payload, headers=headers
    )
    assert put_resp.status_code == 200, put_resp.text
    updated = put_resp.json()
    assert updated["name"] == "coding-agent"
    assert updated["is_builtin"] is True
    assert updated["has_override"] is True
    assert updated["yaml_source"] == "[built-in]"
    assert updated["override_source"] == "[user:persona-user-2]"
    assert updated["summary"] == "Overridden built-in coding agent"

    delete_resp = await volundr_client.delete(f"{API}/personas/coding-agent", headers=headers)
    assert delete_resp.status_code == 204, delete_resp.text

    detail_resp = await volundr_client.get(f"{API}/personas/coding-agent", headers=headers)
    assert detail_resp.status_code == 200, detail_resp.text
    restored = detail_resp.json()
    assert restored["is_builtin"] is True
    assert restored["has_override"] is False
    assert restored["override_source"] is None


async def test_personas_are_user_scoped(volundr_client, auth_headers):
    owner_headers = auth_headers("persona-user-3", "persona3@example.com")
    other_headers = auth_headers("persona-user-4", "persona4@example.com")

    create_resp = await volundr_client.post(
        f"{API}/personas",
        json=_payload("scoped-persona"),
        headers=owner_headers,
    )
    assert create_resp.status_code == 201, create_resp.text

    owner_list = await volundr_client.get(f"{API}/personas?source=custom", headers=owner_headers)
    assert owner_list.status_code == 200, owner_list.text
    assert {item["name"] for item in owner_list.json()} == {"scoped-persona"}

    other_list = await volundr_client.get(f"{API}/personas?source=custom", headers=other_headers)
    assert other_list.status_code == 200, other_list.text
    assert "scoped-persona" not in {item["name"] for item in other_list.json()}

    other_get = await volundr_client.get(f"{API}/personas/scoped-persona", headers=other_headers)
    assert other_get.status_code == 404, other_get.text
