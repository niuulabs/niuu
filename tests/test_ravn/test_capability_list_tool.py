from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from niuu.domain.agent_directory import AgentDirectoryPage
from ravn.adapters.tools.capability_catalog import CapabilityListTool
from ravn.domain.capability_catalog import WorkflowCapability
from ravn.domain.models import Skill, ToolResult
from ravn.ports.capability import WorkflowLaunchRequest, WorkflowLaunchResult
from ravn.ports.tool import ToolPort


class FakeTool(ToolPort):
    @property
    def name(self) -> str:
        return "mimir_search"

    @property
    def description(self) -> str:
        return "Search Mimir."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    @property
    def required_permission(self) -> str:
        return "mimir:read"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content="ok")


class FakeSkillPort:
    async def list_skills(self, query: str | None = None) -> list[Skill]:
        return [
            Skill(
                skill_id="skill-1",
                name="Investigate pod crash",
                description="Gather pod failure context.",
                content="body",
                requires_tools=["kubernetes_inspect"],
                fallback_for_tools=[],
                source_episodes=[],
                created_at=datetime.now(UTC),
                success_count=2,
            )
        ]


class FakeWorkflowSource:
    async def list_workflows(self) -> list[WorkflowCapability]:
        return [
            WorkflowCapability(
                workflow_id="wf-build",
                name="Tool Builder",
                description="Build a missing tool.",
                tags=["tool-builder"],
            )
        ]

    async def launch_workflow(self, request: WorkflowLaunchRequest) -> WorkflowLaunchResult:
        raise AssertionError("capability_list must not launch workflows")


class FakeAgentDirectory:
    async def list_agents(self) -> AgentDirectoryPage:
        return AgentDirectoryPage.model_validate(
            {
                "items": [
                    {
                        "id": "agent-1",
                        "canonicalId": "source:observatory-a:agent-a",
                        "sourceAgentId": "agent-a",
                        "sourceInstanceId": "observatory-a",
                        "clusterId": "cluster-a",
                        "topologyNodeId": "runtime:agent-a",
                        "name": "Research peer",
                        "description": "Researches unfamiliar environments.",
                        "kind": "resident",
                        "cardUrl": "https://peer.example/card",
                        "cardVersion": "1.2",
                        "cardHash": "card-hash",
                        "skillIds": ["research"],
                        "skills": [
                            {
                                "id": "research",
                                "name": "Research environment",
                                "description": "Gather source-backed context.",
                                "tags": ["research"],
                            }
                        ],
                        "defaultInputModes": ["text/plain"],
                        "defaultOutputModes": ["application/json"],
                        "supportedInterfaces": [
                            {
                                "url": "https://peer.example/a2a",
                                "protocolBinding": "JSONRPC",
                                "protocolVersion": "1.0",
                            }
                        ],
                        "securityRequirements": [{"schemes": {"bearer": {}}}],
                        "observedStatus": "healthy",
                        "lastSeen": "2026-07-20T12:00:00Z",
                        "visibility": "tenant",
                    }
                ]
            }
        )

    async def get_agent(self, agent_id: str):
        page = await self.list_agents()
        return next((item for item in page.items if item.id == agent_id), None)


@pytest.mark.asyncio
async def test_capability_list_aggregates_tools_skills_and_workflows() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        skill_port=FakeSkillPort(),
        workflow_sources=[FakeWorkflowSource()],
    )

    result = await tool.execute({})

    assert not result.is_error
    payload = json.loads(result.content)
    assert [(item["kind"], item["name"]) for item in payload["capabilities"]] == [
        ("tool", "mimir_search"),
        ("skill", "Investigate pod crash"),
        ("workflow", "Tool Builder"),
    ]


@pytest.mark.asyncio
async def test_capability_list_filters_catalog_without_routing() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        skill_port=FakeSkillPort(),
        workflow_sources=[FakeWorkflowSource()],
    )

    result = await tool.execute({"kind": "workflow", "tags": ["tool-builder"]})

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["count"] == 1
    assert payload["capabilities"][0]["id"] == "workflow:wf-build"
    assert payload["catalog_total"] == 3
    assert payload["catalog_counts"] == {"tool": 1, "skill": 1, "workflow": 1}


@pytest.mark.asyncio
async def test_capability_list_returns_complete_catalog_when_unfiltered() -> None:
    # a67d376c removed the `query` filter because narrowing hid peer agent
    # skills. It is back — the catalog outgrew what can be returned whole — so
    # the guarantee that matters now is that an *unfiltered* call still shows
    # peers, and that a filtered miss says so rather than reading as empty.
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        agent_directory=FakeAgentDirectory(),
    )

    result = await tool.execute({})

    payload = json.loads(result.content)
    assert [item["name"] for item in payload["capabilities"]] == [
        "mimir_search",
        "Research environment",
    ]
    assert payload["total"] == 2
    assert payload["catalog_total"] == 2
    assert payload["catalog_counts"] == {"tool": 1, "agent_skill": 1}
    assert payload["catalog_preview"] == []


@pytest.mark.asyncio
async def test_capability_list_projects_agent_card_skills_with_invocation_metadata() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        agent_directory=FakeAgentDirectory(),
    )

    result = await tool.execute({"kind": "agent_skill"})

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["count"] == 1
    capability = payload["capabilities"][0]
    assert capability["id"] == "agent:agent-1:research"
    assert capability["source"] == "agent-card"
    # Enough to invoke the peer skill straight from the listing.
    assert capability["invoke_via"] == "a2a_task"
    assert capability["agent_id"] == "agent-1"
    assert capability["skill_id"] == "research"
    assert "metadata" not in capability

    detail = await tool.execute({"kind": "agent_skill", "names": ["Research environment"]})
    metadata = json.loads(detail.content)["capabilities"][0]["metadata"]
    assert metadata["last_seen"] == "2026-07-20T12:00:00Z"
    assert metadata["card_url"] == "https://peer.example/card"


@pytest.mark.asyncio
async def test_capability_list_rejects_unknown_kind() -> None:
    tool = CapabilityListTool(tools_provider=lambda: [FakeTool()])

    result = await tool.execute({"kind": "policy"})

    assert result.is_error
    assert "Unsupported capability kind" in result.content


def _learned_artifact(
    name: str,
    description: str = "Read a metric window.",
    *,
    supersedes: str = "",
):
    from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest

    return LearnedToolArtifact(
        artifact_id=f"learned-tool:{name}",
        manifest=LearnedToolManifest(
            name=name,
            description=description,
            input_schema={"type": "object"},
            required_permission="mimir:read",
            declared_reach=[],
        ),
        tool_code="def run(input):\n    return {'ok': True}\n",
        supersedes=supersedes,
    )


@pytest.mark.asyncio
async def test_capability_list_includes_learned_tools_without_loading_them() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        learned_tools_provider=lambda: [_learned_artifact("metric_window")],
    )

    result = await tool.execute({"kind": "tool"})

    assert not result.is_error
    payload = json.loads(result.content)
    learned = [item for item in payload["capabilities"] if item["name"] == "metric_window"]
    assert len(learned) == 1
    # The 'learned' tag is what tells the model to reach for learned_tool_run,
    # so it must survive into the compact listing.
    assert learned[0]["tags"] == ["tool", "learned"]

    # The full evidence still exists — it moved behind an explicit ask, so a
    # listing of 80 tools no longer drags 80 schemas along with it.
    detail = await tool.execute({"kind": "tool", "names": ["metric_window"]})
    entry = json.loads(detail.content)["capabilities"][0]
    assert entry["metadata"]["invoke_via"] == "learned_tool_run"
    assert entry["metadata"] == {
        "invoke_via": "learned_tool_run",
        "artifact_id": "learned-tool:metric_window",
        "artifact_type": "agent_tool",
        "verification": "unknown",
        "has_tests": False,
        "requirements_count": 0,
        "supersedes": "",
        "lifecycle": {"status": "unmanaged"},
    }


@pytest.mark.asyncio
async def test_capability_list_exposes_learned_tool_lifecycle_evidence() -> None:
    class ManagedSkillManager:
        def status(self, name: str) -> str | None:
            return "active"

        def lifecycle_metadata(self, name: str) -> dict[str, object] | None:
            return {
                "status": "active",
                "scope": "environment",
                "version": 2,
                "pinned": False,
                "run_count": 4,
                "success_count": 3,
                "failure_count": 1,
                "consecutive_failures": 0,
                "last_used_at": "2026-07-27T08:00:00+00:00",
            }

    tool = CapabilityListTool(
        tools_provider=lambda: [],
        learned_tools_provider=lambda: [
            _learned_artifact(
                "current_probe",
                supersedes="learned-tool:old_probe:abc123",
            )
        ],
        skill_manager=ManagedSkillManager(),  # type: ignore[arg-type]
    )

    listed = json.loads((await tool.execute({"kind": "tool"})).content)["capabilities"][0]
    # A compact entry still has to answer "is this one working?" — otherwise a
    # resident that cannot tell a healthy tool from a failing one rebuilds it,
    # which is the same waste by a different route.
    assert listed["usage"] == {"status": "active", "runs": 4, "failures": 1}
    assert "input_schema" not in listed

    result = await tool.execute({"kind": "tool", "names": ["current_probe"]})

    metadata = json.loads(result.content)["capabilities"][0]["metadata"]
    assert metadata["supersedes"] == "learned-tool:old_probe:abc123"
    assert metadata["lifecycle"] == {
        "status": "active",
        "scope": "environment",
        "version": 2,
        "pinned": False,
        "run_count": 4,
        "success_count": 3,
        "failure_count": 1,
        "consecutive_failures": 0,
        "last_used_at": "2026-07-27T08:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_capability_list_hides_archived_learned_tools() -> None:
    class ArchivedSkillManager:
        def status(self, name: str) -> str | None:
            return "archived" if name == "obsolete_probe" else None

        def lifecycle_metadata(self, name: str) -> dict[str, object] | None:
            return None

    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        learned_tools_provider=lambda: [
            _learned_artifact("obsolete_probe"),
            _learned_artifact("current_probe"),
        ],
        skill_manager=ArchivedSkillManager(),  # type: ignore[arg-type]
    )

    result = await tool.execute({"kind": "tool"})

    payload = json.loads(result.content)
    names = [item["name"] for item in payload["capabilities"]]
    assert "obsolete_probe" not in names
    assert "current_probe" in names


@pytest.mark.asyncio
async def test_capability_list_dedupes_learned_tools_against_native_names() -> None:
    # A learned tool already registered natively (legacy bulk mode, or freshly
    # built in-session) must not appear twice in the catalog.
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        learned_tools_provider=lambda: [_learned_artifact("mimir_search")],
    )

    result = await tool.execute({"kind": "tool"})

    payload = json.loads(result.content)
    names = [item["name"] for item in payload["capabilities"]]
    assert names.count("mimir_search") == 1
    assert payload["capabilities"][0]["tags"] != ["tool", "learned"]


@pytest.mark.asyncio
async def test_capability_list_records_learned_source_errors() -> None:
    def _boom():
        raise RuntimeError("artifact dir unreadable")

    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        learned_tools_provider=_boom,
    )

    result = await tool.execute({})

    payload = json.loads(result.content)
    assert {"kind": "learned_tool", "error": "artifact dir unreadable"} in payload["errors"]


class BigSchemaTool(ToolPort):
    """A tool whose schema is realistically large (kubectl-shaped, ~2KB)."""

    def __init__(self, index: int) -> None:
        self._index = index

    @property
    def name(self) -> str:
        return f"list_pods_in_namespace_v{self._index}"

    @property
    def description(self) -> str:
        return (
            f"List pods in a namespace and summarise their phase, restarts, and "
            f"age. Variant {self._index}. " + "Detail. " * 40
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                f"field_{n}": {
                    "type": "string",
                    "description": "A parameter documented at realistic length. " * 3,
                }
                for n in range(12)
            },
        }

    @property
    def required_permission(self) -> str:
        return "kubernetes:read"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content="ok")


@pytest.mark.asyncio
async def test_capability_list_result_stays_parseable_for_a_large_catalog() -> None:
    """The regression that drove this: a big catalog came back as broken JSON.

    On valhalla the catalog reached 151,007 chars against the agent's 100,000
    char cap, so a third was sliced off — mid-structure. The model asked what it
    owned, got malformed JSON back, could not find the tool, and built it again.
    """
    from ravn.agent import RavnAgent
    from ravn.domain.models import ToolCall

    tools = [BigSchemaTool(index) for index in range(200)]
    tool = CapabilityListTool(tools_provider=lambda: tools)

    result = await tool.execute({})

    # Without the compact projection this payload is ~1.2MB; the assertion that
    # matters is not its size but that it survives the agent's cap intact.
    # No prompt budget here, so only the flat cap applies.
    agent = SimpleNamespace(_max_tool_result_chars=100_000, _max_prompt_tokens=0)
    agent._budget_tool_result_char_limit = lambda: RavnAgent._budget_tool_result_char_limit(
        agent  # type: ignore[arg-type]
    )
    truncated = RavnAgent._truncate_oversized_tool_result(
        agent,  # type: ignore[arg-type]
        ToolCall(id="call-1", name="capability_list", input={}),
        result,
    )
    payload = json.loads(truncated.content)

    assert payload["truncated"] is True
    assert payload["total"] == 200
    assert payload["count"] < 200
    assert "next_step" in payload
    assert all("input_schema" not in item for item in payload["capabilities"])


@pytest.mark.asyncio
async def test_capability_list_query_narrows_by_substring() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        learned_tools_provider=lambda: [
            _learned_artifact("list_pods_in_namespace", "List pods in a namespace."),
            _learned_artifact("read_node_pressure", "Report node memory pressure."),
        ],
    )

    result = await tool.execute({"query": "pods"})

    payload = json.loads(result.content)
    assert [item["name"] for item in payload["capabilities"]] == ["list_pods_in_namespace"]
    assert payload["query"] == "pods"
    assert payload["catalog_total"] == 3


@pytest.mark.asyncio
async def test_capability_list_query_matches_description_not_only_name() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [],
        learned_tools_provider=lambda: [
            _learned_artifact("workload_census", "Counts pods per namespace.")
        ],
    )

    payload = json.loads((await tool.execute({"query": "POD"})).content)

    assert [item["name"] for item in payload["capabilities"]] == ["workload_census"]


@pytest.mark.asyncio
async def test_capability_list_names_returns_full_schema_and_reports_unknowns() -> None:
    tool = CapabilityListTool(tools_provider=lambda: [FakeTool()])

    payload = json.loads((await tool.execute({"names": ["mimir_search", "never_built"]})).content)

    assert payload["detail"] is True
    assert payload["capabilities"][0]["input_schema"] == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    assert payload["not_found"] == ["never_built"]


@pytest.mark.asyncio
async def test_capability_list_reports_a_limited_page_as_truncated() -> None:
    tool = CapabilityListTool(tools_provider=lambda: [BigSchemaTool(n) for n in range(5)])

    payload = json.loads((await tool.execute({"limit": 2})).content)

    assert (payload["count"], payload["total"], payload["truncated"]) == (2, 5, True)


@pytest.mark.asyncio
async def test_capability_list_does_not_claim_truncation_for_a_whole_page() -> None:
    tool = CapabilityListTool(tools_provider=lambda: [FakeTool()])

    payload = json.loads((await tool.execute({})).content)

    assert payload["truncated"] is False
    assert "next_step" not in payload


@pytest.mark.asyncio
async def test_capability_list_clips_long_descriptions_in_the_index() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [],
        learned_tools_provider=lambda: [_learned_artifact("verbose", "word " * 200)],
    )

    entry = json.loads((await tool.execute({})).content)["capabilities"][0]

    assert len(entry["description"]) <= 201
    assert entry["description"].endswith("…")


@pytest.mark.asyncio
async def test_capability_list_query_miss_previews_the_catalog() -> None:
    """A too-narrow query must not read as "there is nothing here".

    This is the failure a67d376c fixed by deleting `query` outright: a resident
    searched, saw an empty list, and concluded no peer capability existed. The
    filter is back for size reasons, so the preview has to carry that weight.
    """
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        agent_directory=FakeAgentDirectory(),
    )

    payload = json.loads((await tool.execute({"query": "nothing-matches-this"})).content)

    assert payload["capabilities"] == []
    assert payload["catalog_total"] == 2
    assert [item["name"] for item in payload["catalog_preview"]] == [
        "mimir_search",
        "Research environment",
    ]
