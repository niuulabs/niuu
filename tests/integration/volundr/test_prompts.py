"""Integration tests for Volundr saved-prompts endpoints."""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/volundr"


async def test_create_and_list_prompts(volundr_client, auth_headers):
    """POST a prompt then GET /prompts should include it."""
    headers = auth_headers()
    payload = {
        "name": "integ-prompt",
        "content": "You are a helpful assistant.",
        "scope": "global",
        "tags": ["test"],
    }
    create_resp = await volundr_client.post(f"{API}/prompts", json=payload, headers=headers)
    assert create_resp.status_code == 201, create_resp.text
    prompt_id = create_resp.json()["id"]

    list_resp = await volundr_client.get(f"{API}/prompts", headers=headers)
    assert list_resp.status_code == 200, list_resp.text

    ids = {p["id"] for p in list_resp.json()}
    assert prompt_id in ids


async def test_delete_prompt(volundr_client, auth_headers):
    """Create a prompt then DELETE it — subsequent GET should not contain it."""
    headers = auth_headers()
    payload = {
        "name": "integ-prompt-del",
        "content": "Temporary prompt.",
        "scope": "global",
    }
    create_resp = await volundr_client.post(f"{API}/prompts", json=payload, headers=headers)
    assert create_resp.status_code == 201, create_resp.text
    prompt_id = create_resp.json()["id"]

    del_resp = await volundr_client.delete(f"{API}/prompts/{prompt_id}", headers=headers)
    assert del_resp.status_code == 204, del_resp.text

    list_resp = await volundr_client.get(f"{API}/prompts", headers=headers)
    assert list_resp.status_code == 200, list_resp.text

    ids = {p["id"] for p in list_resp.json()}
    assert prompt_id not in ids
