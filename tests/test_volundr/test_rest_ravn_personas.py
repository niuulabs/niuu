"""Tests for the Volundr-hosted Ravn persona routes."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personas.adapters.postgres_registry import PostgresPersonaRegistry, _normalize_payload
from volundr.adapters.inbound.rest_ravn_personas import create_ravn_personas_router
from volundr.domain.models import Principal, User, UserStatus


class _InMemoryPool:
    """The two statements the persona registry runs, over a dict, so the real registry is tested."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    async def execute(self, query: str, *args: object) -> str:
        statement = query.split()[0]
        if statement == "INSERT":
            owner_id, name, config_json = args[0], args[1], args[2]
            self.rows.pop((str(owner_id), str(name)), None)
            self.rows[(str(owner_id), str(name))] = str(config_json)
            return "INSERT 0 1"
        if statement == "DELETE":
            removed = self.rows.pop((str(args[0]), str(args[1])), None)
            return "DELETE 1" if removed is not None else "DELETE 0"
        raise AssertionError(f"unexpected statement: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, str]]:
        owner_id = args[0]
        name = args[1] if len(args) > 1 else None
        return [
            {"name": row_name, "config_json": config_json}
            for (row_owner, row_name), config_json in reversed(self.rows.items())
            if row_owner == owner_id and (name is None or row_name == name)
        ]

    def put(self, owner_id: str, payload: dict) -> None:
        self.rows[(owner_id, payload["name"])] = json.dumps(payload)


def _make_client() -> tuple[TestClient, PostgresPersonaRegistry]:
    registry = PostgresPersonaRegistry(_InMemoryPool())  # type: ignore[arg-type]
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
    def test_builtin_summaries_expose_all_outcome_events(self) -> None:
        client, _ = _make_client()
        response = client.get(
            "/api/v1/personas?source=builtin",
            headers={"Authorization": "Bearer token"},
        )
        assert response.status_code == 200
        reviewer = next(item for item in response.json() if item["name"] == "reviewer")
        assert reviewer["outcome_events"] == {
            "pass": "review.passed",
            "needs_changes": "review.changes_requested",
            "fail": "review.changes_requested",
        }

        detail = client.get(
            "/api/v1/personas/reviewer",
            headers={"Authorization": "Bearer token"},
        ).json()
        assert detail["produces"]["event_type_map"] == reviewer["outcome_events"]

    def test_detail_says_when_to_report_each_verdict(self) -> None:
        client, _ = _make_client()
        detail = client.get(
            "/api/v1/personas/reviewer",
            headers={"Authorization": "Bearer token"},
        ).json()
        when = detail["produces"]["when"]
        assert set(when) == set(detail["produces"]["event_type_map"])
        assert when["pass"].startswith("The committed change is ready to merge")

    def test_portable_source_is_owner_scoped_and_exactly_addressable(self) -> None:
        client, _ = _make_client()
        client.post(
            "/api/v1/personas",
            json=_make_payload("portable-agent"),
            headers={"Authorization": "Bearer token"},
        )

        current = client.get(
            "/api/v1/personas/portable-agent/portable",
            headers={"Authorization": "Bearer token"},
        )

        assert current.status_code == 200
        document = current.json()
        assert document["id"] == "portable-agent"
        assert document["revision"].startswith("content-")
        assert "executor" not in document["definition"]

        exact = client.get(
            f"/api/v1/personas/portable-agent/revisions/{document['revision']}",
            headers={"Authorization": "Bearer token"},
        )
        assert exact.status_code == 200
        assert exact.json() == document

        missing = client.get(
            "/api/v1/personas/portable-agent/revisions/content-0000000000000000",
            headers={"Authorization": "Bearer token"},
        )
        assert missing.status_code == 404

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
        payload = _make_payload("coder") | {
            "summary": "Overridden coding agent",
            "system_prompt_template": "Override built-in coding agent.",
        }

        put_resp = client.put(
            "/api/v1/ravn/personas/coder",
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
            "/api/v1/ravn/personas/coder",
            headers={"Authorization": "Bearer token"},
        )
        assert delete_resp.status_code == 204

        detail_resp = client.get(
            "/api/v1/ravn/personas/coder",
            headers={"Authorization": "Bearer token"},
        )
        assert detail_resp.status_code == 200
        restored = detail_resp.json()
        assert restored["is_builtin"] is True
        assert restored["has_override"] is False
        assert restored["override_source"] is None

    def test_user_scoping_isolated(self) -> None:
        client, registry = _make_client()
        pool = registry._pool
        pool.put("user-1", _normalize_payload(_make_payload("scoped-agent")))
        pool.put("user-2", _normalize_payload(_make_payload("other-agent")))

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


@pytest.mark.parametrize("key", ["allowed_tools", "forbidden_tools"])
def test_validate_refuses_a_tool_restriction(key: str) -> None:
    client, _ = _make_client()
    payload = _make_payload("validation-agent")
    payload[key] = ["file_read"]

    resp = client.post(
        "/api/v1/ravn/personas/validate",
        json=payload,
        headers={"Authorization": "Bearer token"},
    )

    assert resp.status_code == 422
    detail = str(resp.json()["detail"])
    assert f"persona validation-agent: {key} is not supported" in detail
    assert "per-harness tool restrictions are not available yet" in detail


@pytest.mark.parametrize("key", ["allowed_tools", "forbidden_tools"])
def test_create_and_replace_refuse_a_tool_restriction_before_saving(key: str) -> None:
    client, _ = _make_client()
    payload = _make_payload("restricted-agent")
    headers = {"Authorization": "Bearer token"}

    assert client.post("/api/v1/ravn/personas", json=payload, headers=headers).status_code == 201

    payload[key] = []
    created = client.post("/api/v1/ravn/personas", json=payload, headers=headers)
    replaced = client.put("/api/v1/ravn/personas/restricted-agent", json=payload, headers=headers)

    assert created.status_code == 422
    assert replaced.status_code == 422
    assert f"{key} is not supported" in created.json()["detail"][0]["msg"]
    assert f"{key} is not supported" in replaced.json()["detail"][0]["msg"]


def test_the_persona_answers_carry_no_tool_lists() -> None:
    client, _ = _make_client()
    headers = {"Authorization": "Bearer token"}
    client.post("/api/v1/ravn/personas", json=_make_payload("plain-agent"), headers=headers)

    detail = client.get("/api/v1/ravn/personas/plain-agent", headers=headers).json()
    summaries = client.get("/api/v1/ravn/personas", headers=headers).json()

    assert "allowed_tools" not in detail
    assert "forbidden_tools" not in detail
    assert all("allowed_tools" not in summary for summary in summaries)


_HEADERS = {"Authorization": "Bearer token"}

_MIGRATION_REVIEWER = """\
name: migration-reviewer
description: Checks a database migration before it runs.
system_prompt_template: |
  You review database migrations before they run.
  Never change files.
permission_mode: read_only
consumes:
  event_types:
  - migration.requested
produces:
  event_type: migration_reviewer.completed
  event_type_map:
    approve: migration_reviewer.approve
    request_changes: migration_reviewer.request_changes
  when:
    approve: The migration can run twice and the down undoes the up.
    request_changes: Something must change first; list each finding with file:line.
  schema:
    verdict:
      type: enum
      description: the outcome
      values:
      - approve
      - request_changes
    summary:
      type: string
      description: one-line summary
"""


class TestPersonaYamlRoutes:
    def test_create_keeps_every_field_the_yaml_declares(self) -> None:
        client, _ = _make_client()

        created = client.post(
            "/api/v1/personas/yaml", json={"yaml": _MIGRATION_REVIEWER}, headers=_HEADERS
        )

        assert created.status_code == 201
        detail = created.json()
        assert detail["name"] == "migration-reviewer"
        assert detail["is_builtin"] is False
        assert detail["summary"] == "Checks a database migration before it runs."
        assert detail["permission_mode"] == "safe"
        assert detail["produces"]["when"]["approve"].startswith("The migration can run twice")
        assert detail["consumes"]["events"][0]["name"] == "migration.requested"
        portable = client.get(
            "/api/v1/personas/migration-reviewer/portable", headers=_HEADERS
        ).json()
        assert portable["definition"] == yaml.safe_load(_MIGRATION_REVIEWER)

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("name: [unclosed", "Invalid persona YAML"),
            ("- just\n- a list\n", "YAML mapping"),
            ("description: no name\n", "needs a name"),
            ("name: Bad Name\n", "must start with"),
            ("name: dotted.name\n", "only letters, digits, dashes and underscores"),
            ("name: typo-agent\nsystem_prompt: hi\n", "system_prompt"),
            ("name: mode-agent\npermission_mode: anything\n", "permission_mode"),
            (
                "name: when-agent\nproduces:\n  event_type_map:\n    pass: a.passed\n"
                "  when:\n    fail: never\n",
                "fail",
            ),
        ],
    )
    def test_create_refuses_yaml_it_cannot_save_and_says_why(self, text: str, reason: str) -> None:
        client, _ = _make_client()

        response = client.post("/api/v1/personas/yaml", json={"yaml": text}, headers=_HEADERS)
        checked = client.post("/api/v1/personas/yaml/check", json={"yaml": text}, headers=_HEADERS)

        assert response.status_code == 422
        assert reason in response.json()["detail"]
        assert checked.json()["valid"] is False
        assert reason in checked.json()["errors"][0]

    def test_check_accepts_a_valid_persona_without_saving_it(self) -> None:
        client, _ = _make_client()

        checked = client.post(
            "/api/v1/ravn/personas/yaml/check", json={"yaml": _MIGRATION_REVIEWER}, headers=_HEADERS
        )

        assert checked.json() == {"valid": True, "errors": []}
        missing = client.get("/api/v1/personas/migration-reviewer", headers=_HEADERS)
        assert missing.status_code == 404

    def test_create_refuses_a_name_that_exists(self) -> None:
        client, _ = _make_client()
        client.post("/api/v1/personas/yaml", json={"yaml": _MIGRATION_REVIEWER}, headers=_HEADERS)

        again = client.post(
            "/api/v1/personas/yaml", json={"yaml": _MIGRATION_REVIEWER}, headers=_HEADERS
        )
        builtin = client.post(
            "/api/v1/personas/yaml", json={"yaml": "name: reviewer\n"}, headers=_HEADERS
        )

        assert again.status_code == 409
        assert "custom" in again.json()["detail"]
        assert builtin.status_code == 409
        assert "built-in" in builtin.json()["detail"]

    def test_replace_saves_the_new_document(self) -> None:
        client, _ = _make_client()
        client.post("/api/v1/personas/yaml", json={"yaml": _MIGRATION_REVIEWER}, headers=_HEADERS)
        changed = _MIGRATION_REVIEWER.replace("Never change files.", "Never touch production.")

        replaced = client.put(
            "/api/v1/personas/migration-reviewer/yaml", json={"yaml": changed}, headers=_HEADERS
        )

        assert replaced.status_code == 200
        assert "Never touch production." in replaced.json()["system_prompt_template"]
        portable = client.get(
            "/api/v1/personas/migration-reviewer/portable", headers=_HEADERS
        ).json()
        assert portable["definition"] == yaml.safe_load(changed)

    def test_replace_refuses_a_document_naming_another_persona(self) -> None:
        client, _ = _make_client()
        client.post("/api/v1/personas/yaml", json={"yaml": _MIGRATION_REVIEWER}, headers=_HEADERS)
        renamed = _MIGRATION_REVIEWER.replace("name: migration-reviewer", "name: other")

        response = client.put(
            "/api/v1/personas/migration-reviewer/yaml", json={"yaml": renamed}, headers=_HEADERS
        )
        missing = client.put(
            "/api/v1/personas/nobody/yaml", json={"yaml": "name: nobody\n"}, headers=_HEADERS
        )

        assert response.status_code == 422
        assert "fork migration-reviewer" in response.json()["detail"]
        assert missing.status_code == 404

    def test_replace_of_a_builtin_saves_an_override(self) -> None:
        client, _ = _make_client()
        builtin = client.get("/api/v1/personas/reviewer/portable", headers=_HEADERS).json()
        definition = dict(builtin["definition"])
        definition["description"] = "Reviews with our house rules."

        response = client.put(
            "/api/v1/personas/reviewer/yaml",
            json={"yaml": yaml.safe_dump(definition, sort_keys=False)},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["has_override"] is True
        assert response.json()["role"] == "review"
        assert response.json()["summary"] == "Reviews with our house rules."
        portable = client.get("/api/v1/personas/reviewer/portable", headers=_HEADERS).json()
        assert portable["definition"] == definition

    def test_a_form_save_after_a_yaml_save_replaces_the_document(self) -> None:
        client, _ = _make_client()
        client.post("/api/v1/personas/yaml", json={"yaml": _MIGRATION_REVIEWER}, headers=_HEADERS)
        payload = _make_payload("migration-reviewer")

        client.put("/api/v1/personas/migration-reviewer", json=payload, headers=_HEADERS)

        portable = client.get(
            "/api/v1/personas/migration-reviewer/portable", headers=_HEADERS
        ).json()
        assert portable["definition"]["system_prompt_template"] == payload["system_prompt_template"]
        assert "when" not in portable["definition"].get("produces", {})

    @pytest.mark.parametrize("source", ["migration-reviewer", "reviewer"])
    def test_fork_keeps_the_whole_document(self, source: str) -> None:
        client, _ = _make_client()
        client.post("/api/v1/personas/yaml", json={"yaml": _MIGRATION_REVIEWER}, headers=_HEADERS)
        original = client.get(f"/api/v1/personas/{source}/portable", headers=_HEADERS).json()

        forked = client.post(
            f"/api/v1/personas/{source}/fork", json={"new_name": "my-copy"}, headers=_HEADERS
        )

        assert forked.status_code == 201
        copy = client.get("/api/v1/personas/my-copy/portable", headers=_HEADERS).json()
        assert copy["definition"] == {**original["definition"], "name": "my-copy"}
        assert forked.json()["produces"]["when"] == original["definition"]["produces"]["when"]
