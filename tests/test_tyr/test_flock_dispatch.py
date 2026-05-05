"""Tests for NIU-615 — flock dispatch extension.

Tests:
1. SpawnRequest construction with flock enabled → workload_type, workload_config, personas
2. SpawnRequest construction with flock disabled → solo behavior unchanged
3. Flock prompt assembly — repo, branch, mimir URL included
4. VolundrHTTPAdapter HTTP pass-through — body includes workload_type + workload_config
5. raid-executor persona YAML — event_type, schema, allowed_tools verified
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import yaml

from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from tyr.domain.models import (
    Saga,
    SagaStatus,
    TrackerIssue,
)
from tyr.domain.services.dispatch_service import (
    DispatchConfig,
    DispatchItem,
    DispatchService,
    build_flock_prompt,
)
from tyr.ports.volundr import SpawnRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PERSONAS_DIR = Path(__file__).parent.parent.parent / "src" / "ravn" / "personas"


def _make_saga(
    feature_branch: str = "feat/alpha",
    base_branch: str = "main",
    repos: list[str] | None = None,
) -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id="proj-1",
        tracker_type="linear",
        slug="alpha",
        name="Alpha",
        repos=repos or ["org/repo-a"],
        feature_branch=feature_branch,
        base_branch=base_branch,
        status=SagaStatus.ACTIVE,
        confidence=0.5,
        created_at=datetime.now(UTC),
        owner_id="user-1",
    )


def _make_issue(
    identifier: str = "ALPHA-1",
    title: str = "Setup CI",
    description: str = "Configure CI pipeline.",
) -> TrackerIssue:
    return TrackerIssue(
        id="i-1",
        identifier=identifier,
        title=title,
        description=description,
        status="Todo",
        url="https://linear.app/i-1",
    )


def _make_flock_config(**overrides) -> DispatchConfig:
    defaults = dict(
        flock_enabled=True,
        flock_default_personas=[{"name": "coordinator"}, {"name": "reviewer"}],
        flock_mimir_hosted_url="https://mimir.example.com",
        flock_mimir_registry_path="~/.ravn/mimir/.mimir-registry.json",
        flock_sleipnir_publish_urls=["nats://sleipnir.example.com"],
    )
    defaults.update(overrides)
    return DispatchConfig(**defaults)


# ---------------------------------------------------------------------------
# 1. SpawnRequest construction — flock enabled
# ---------------------------------------------------------------------------


class TestBuildSpawnRequestFlockEnabled:
    """_build_spawn_request returns a ravn_flock SpawnRequest when flock is on."""

    def test_workload_type_is_ravn_flock(self) -> None:
        config = _make_flock_config()
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert req.workload_type == "ravn_flock"

    def test_workload_config_contains_personas(self) -> None:
        config = _make_flock_config(
            flock_default_personas=[{"name": "coordinator"}, {"name": "reviewer"}]
        )
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert req.workload_config["personas"] == [{"name": "coordinator"}, {"name": "reviewer"}]

    def test_workload_config_contains_sleipnir_publish_urls(self) -> None:
        config = _make_flock_config(
            flock_sleipnir_publish_urls=["nats://sleipnir.example.com", "nats://backup.example.com"]
        )
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert req.workload_config["sleipnir_publish_urls"] == [
            "nats://sleipnir.example.com",
            "nats://backup.example.com",
        ]

    def test_workload_config_contains_mimir_hosted_url(self) -> None:
        config = _make_flock_config(flock_mimir_hosted_url="https://mimir.example.com")
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert req.workload_config["mimir_hosted_url"] == "https://mimir.example.com"
        assert req.workload_config["mimir"] == {"hosted_url": "https://mimir.example.com"}

    def test_no_sleipnir_key_when_urls_empty(self) -> None:
        config = _make_flock_config(flock_sleipnir_publish_urls=[])
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert "sleipnir_publish_urls" not in req.workload_config

    def test_no_mimir_key_when_url_empty(self) -> None:
        config = _make_flock_config(flock_mimir_hosted_url="")
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert "mimir_hosted_url" not in req.workload_config
        assert "mimir" not in req.workload_config

    def test_workload_config_contains_workflow_snapshot_mimir_resources(self) -> None:
        config = _make_flock_config(flock_mimir_hosted_url="")
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")
        workflow_snapshot = {
            "workflow_id": str(uuid4()),
            "name": "Knowledge Flow",
            "version": "1.0.0",
            "mimir": {
                "registry_refs": [
                    {
                        "resource_node_id": "mimir-1",
                        "registry_entry_id": "shared-team-mimir",
                        "mount_name": "shared-team-mimir",
                        "categories": ["entity"],
                    }
                ],
                "bindings": [
                    {
                        "resource_node_id": "mimir-1",
                        "mount_name": "shared-team-mimir",
                        "target_type": "workflow",
                        "target_id": "wf-1",
                        "access": "read_write",
                        "write_prefixes": ["project/"],
                        "read_priority": 3,
                    }
                ],
            },
            "graph": {
                "nodes": [
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                    }
                ]
            },
        }

        svc = MagicMock()
        svc._config = config
        svc._flow_provider = None
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            workflow_snapshot=workflow_snapshot,
        )

        assert req.workload_config["mimir"] == workflow_snapshot["mimir"]

    def test_workload_config_resolves_registry_backed_mimir_resources(self, tmp_path: Path) -> None:
        registry_path = tmp_path / ".mimir-registry.json"
        store = MimirRegistryStore(registry_path)
        entry = MimirRegistryEntry(
            id="shared-team-mimir",
            name="shared-team-mimir",
            kind="remote",
            role="shared",
            url="https://mimir.shared.test/api/v1",
            categories=["entity", "decision"],
        )
        store.save_entry(entry)

        config = _make_flock_config(
            flock_mimir_hosted_url="",
            flock_mimir_registry_path=str(registry_path),
        )
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")
        workflow_snapshot = {
            "workflow_id": str(uuid4()),
            "name": "Knowledge Flow",
            "version": "1.0.0",
            "mimir": {
                "registry_refs": [
                    {
                        "resource_node_id": "mimir-1",
                        "registry_entry_id": "shared-team-mimir",
                        "mount_name": "shared-team-mimir",
                    }
                ],
                "bindings": [],
            },
            "graph": {
                "nodes": [
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                    }
                ]
            },
        }

        svc = MagicMock()
        svc._config = config
        svc._flow_provider = None
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            workflow_snapshot=workflow_snapshot,
        )

        assert req.workload_config["mimir"]["registry_refs"] == [
            {
                "resource_node_id": "mimir-1",
                "registry_entry_id": "shared-team-mimir",
                "mount_name": "shared-team-mimir",
                "url": "https://mimir.shared.test/api/v1",
                "role": "shared",
                "categories": ["entity", "decision"],
                "default_read_priority": 10,
                "enabled": True,
            }
        ]

    def test_workload_config_contains_llm_config(self) -> None:
        llm = {
            "model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
            "max_tokens": 8192,
            "provider": {"adapter": "ravn.adapters.llm.openai.OpenAICompatibleAdapter"},
        }
        config = _make_flock_config(flock_llm_config=llm)
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert req.workload_config["llm_config"] == llm

    def test_no_llm_config_key_when_empty(self) -> None:
        config = _make_flock_config(flock_llm_config={})
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert "llm_config" not in req.workload_config


# ---------------------------------------------------------------------------
# 2. SpawnRequest construction — flock disabled
# ---------------------------------------------------------------------------


class TestBuildSpawnRequestFlockDisabled:
    """_build_spawn_request returns a default solo SpawnRequest when flock is off."""

    def test_workload_type_is_default(self) -> None:
        config = DispatchConfig(flock_enabled=False)
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert req.workload_type == "default"

    def test_workload_config_is_empty(self) -> None:
        config = DispatchConfig(flock_enabled=False)
        saga = _make_saga()
        issue = _make_issue()
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert req.workload_config == {}

    def test_uses_solo_prompt(self) -> None:
        config = DispatchConfig(flock_enabled=False, dispatch_prompt_template="")
        saga = _make_saga(feature_branch="feat/alpha")
        issue = _make_issue(description="do the thing")
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo-a")

        svc = MagicMock()
        svc._config = config
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        assert "do the thing" in req.initial_prompt
        assert "ravn_flock" not in req.initial_prompt


# ---------------------------------------------------------------------------
# 3. Prompt assembly — build_flock_prompt
# ---------------------------------------------------------------------------


class TestBuildFlockPrompt:
    """build_flock_prompt includes raid context and optional Mimir note."""

    def test_includes_issue_identifier_and_title(self) -> None:
        issue = _make_issue(identifier="NIU-99", title="Ship the product")
        prompt = build_flock_prompt(issue, "org/repo", "feat/ship")
        assert "NIU-99" in prompt
        assert "Ship the product" in prompt

    def test_includes_description(self) -> None:
        issue = _make_issue(description="Acceptance: deployed, tested, merged.")
        prompt = build_flock_prompt(issue, "org/repo", "feat/ship")
        assert "Acceptance: deployed, tested, merged." in prompt

    def test_includes_repo_and_branch(self) -> None:
        issue = _make_issue()
        prompt = build_flock_prompt(issue, "org/my-repo", "feat/my-branch")
        assert "org/my-repo" in prompt
        assert "feat/my-branch" in prompt

    def test_includes_mimir_url_when_provided(self) -> None:
        issue = _make_issue()
        prompt = build_flock_prompt(
            issue, "org/repo", "feat/x", mimir_hosted_url="https://mimir.example.com"
        )
        assert "https://mimir.example.com" in prompt

    def test_no_mimir_reference_when_url_empty(self) -> None:
        issue = _make_issue()
        prompt = build_flock_prompt(issue, "org/repo", "feat/x", mimir_hosted_url="")
        assert "mimir" not in prompt.lower()

    def test_includes_delegation_instructions(self) -> None:
        issue = _make_issue()
        prompt = build_flock_prompt(issue, "org/repo", "feat/x")
        assert "reviewer" in prompt.lower()
        assert "coder" in prompt.lower()


# ---------------------------------------------------------------------------
# 4. HTTP pass-through — VolundrHTTPAdapter sends workload_config
# ---------------------------------------------------------------------------


class TestVolundrHTTPAdapterFlockPassthrough:
    """VolundrHTTPAdapter sends workload_type + workload_config in POST body."""

    @pytest.mark.asyncio
    async def test_spawn_session_includes_workload_config(self) -> None:
        from tyr.adapters.volundr_http import VolundrHTTPAdapter

        captured: dict = {}

        async def _mock_post(url, *, headers, json, **kwargs):
            captured["body"] = json
            resp = MagicMock()
            resp.status_code = 201
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "id": "ses-1",
                "name": "test-session",
                "status": "running",
                "tracker_issue_id": "ALPHA-1",
                "source": {"repo": "org/repo", "branch": "feat/x", "base_branch": "main"},
            }
            return resp

        adapter = VolundrHTTPAdapter(base_url="http://volundr.local", api_key="tok")

        workload_cfg = {
            "personas": ["coordinator", "reviewer"],
            "mimir_hosted_url": "https://mimir.example.com",
            "initiative_context": "Do the raid.",
        }
        request = SpawnRequest(
            name="alpha-1",
            repo="org/repo",
            branch="feat/x",
            base_branch="main",
            model="claude-sonnet-4-6",
            tracker_issue_id="ALPHA-1",
            tracker_issue_url="",
            system_prompt="",
            initial_prompt="Do the raid.",
            workload_type="ravn_flock",
            workload_config=workload_cfg,
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=_mock_post)
            # list_repos is called for shorthand resolution — org/repo triggers it
            mock_client.get = AsyncMock(
                return_value=MagicMock(
                    status_code=200,
                    raise_for_status=MagicMock(),
                    json=MagicMock(return_value={}),
                )
            )
            mock_client_cls.return_value = mock_client

            await adapter.spawn_session(request, auth_token="tok")

        body = captured.get("body", {})
        assert body.get("workload_type") == "ravn_flock"
        assert body.get("workload_config") == workload_cfg

    @pytest.mark.asyncio
    async def test_spawn_session_default_workload_config_empty(self) -> None:
        from tyr.adapters.volundr_http import VolundrHTTPAdapter

        captured: dict = {}

        async def _mock_post(url, *, headers, json, **kwargs):
            captured["body"] = json
            resp = MagicMock()
            resp.status_code = 201
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "id": "ses-2",
                "name": "solo-session",
                "status": "running",
                "tracker_issue_id": "ALPHA-2",
                "source": {
                    "repo": "https://github.com/org/repo",
                    "branch": "feat/x",
                    "base_branch": "main",
                },
            }
            return resp

        adapter = VolundrHTTPAdapter(base_url="http://volundr.local", api_key="tok")
        request = SpawnRequest(
            name="alpha-2",
            repo="https://github.com/org/repo",
            branch="feat/x",
            base_branch="main",
            model="claude-sonnet-4-6",
            tracker_issue_id="ALPHA-2",
            tracker_issue_url="",
            system_prompt="",
            initial_prompt="Solo prompt.",
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=_mock_post)
            mock_client_cls.return_value = mock_client

            await adapter.spawn_session(request, auth_token="tok")

        body = captured.get("body", {})
        assert body.get("workload_type") == "default"
        assert body.get("workload_config") == {}


# ---------------------------------------------------------------------------
# 5. raid-executor persona YAML
# ---------------------------------------------------------------------------


class TestRaidExecutorPersona:
    """raid-executor.yaml has correct event_type, schema, and allowed_tools."""

    def _load(self) -> dict:
        path = _PERSONAS_DIR / "raid-executor.yaml"
        with path.open() as f:
            return yaml.safe_load(f)

    def test_file_exists(self) -> None:
        assert (_PERSONAS_DIR / "raid-executor.yaml").exists()

    def test_name(self) -> None:
        data = self._load()
        assert data["name"] == "raid-executor"

    def test_produces_event_type(self) -> None:
        data = self._load()
        assert data["produces"]["event_type"] == "ravn.task.completed"

    def test_produces_schema_has_required_fields(self) -> None:
        data = self._load()
        schema = data["produces"]["schema"]
        assert "verdict" in schema
        assert "tests_passing" in schema
        assert "scope_adherence" in schema
        assert "pr_url" in schema
        assert "summary" in schema

    def test_verdict_is_enum(self) -> None:
        data = self._load()
        verdict = data["produces"]["schema"]["verdict"]
        assert verdict["type"] == "enum"
        enum_values = verdict.get("enum_values") or verdict.get("values", [])
        assert "approve" in enum_values
        assert "retry" in enum_values
        assert "escalate" in enum_values

    def test_allowed_tools(self) -> None:
        data = self._load()
        tools = data["allowed_tools"]
        assert "task_create" in tools
        assert "task_collect" in tools
        assert "task_status" in tools

    def test_iteration_budget(self) -> None:
        data = self._load()
        assert data["iteration_budget"] == 40
