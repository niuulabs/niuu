"""The compile run an import starts, the bundled workflow it runs and its persona."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import pytest
import yaml

from personas.adapters.loader import FilesystemPersonaAdapter
from tests.test_ting.fake_workflow_runs import FakeWorkflowRuns
from tests.test_ting.memory_import_fakes import PROJECT_ID, issue
from tests.test_ting.test_memory_imports import Harness
from tests.test_ting.test_workflows_api import RecordingCampaignRepository
from ting.adapters.memory_compile_launcher import (
    COMPILE_SURFACE,
    WorkflowCompileLauncher,
)
from ting.adapters.memory_import_worker import MemoryImportWorker
from ting.config import MemoryImportsConfig, Settings
from ting.domain.memory_imports import (
    COMPILE_CONTEXT_SOURCE_IDS,
    COMPILE_PROMPT,
    MEMORY_COMPILE_WORKFLOW_ID,
    CompileLaunchError,
)
from ting.domain.models import WorkflowCampaignStatus
from ting.domain.services.run_thread import RUN_PROJECT_KEY
from ting.system_workflows import BUNDLED_SYSTEM_WORKFLOWS_PATH, load_system_workflows


def _bundled():
    return next(w for w in load_system_workflows() if w.id == MEMORY_COMPILE_WORKFLOW_ID)


# -- The bundled workflow and its persona ---------------------------------------------


def test_the_memory_compile_workflow_is_bundled_valid_and_has_the_contracts_shape() -> None:
    workflow = _bundled()
    document = yaml.safe_load((BUNDLED_SYSTEM_WORKFLOWS_PATH / "memory-compile.yaml").read_text())

    assert workflow.read_only
    assert workflow.origin == "bundled"
    assert workflow.id == UUID(document["id"])
    nodes = {node["id"]: node for node in workflow.graph["nodes"]}
    triggers = [n for n in nodes.values() if n["kind"] == "trigger"]
    stages = [n for n in nodes.values() if n["kind"] == "stage"]
    ends = [n for n in nodes.values() if n["kind"] == "end"]
    assert [t["dispatchEvent"] for t in triggers] == ["memory.compile.requested"]
    assert len(stages) == 1
    assert [m["personaId"] for m in stages[0]["stageMembers"]] == ["memory-compiler"]
    assert len(ends) == 1
    assert [(e["source"], e["target"]) for e in workflow.graph["edges"]] == [
        (triggers[0]["id"], stages[0]["id"]),
        (stages[0]["id"], ends[0]["id"]),
    ]


def test_the_workflow_pins_the_persona_that_ships() -> None:
    workflow = _bundled()
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load_current_portable(
        "memory-compiler"
    )

    pin = workflow.persona_definitions["memory-compiler"]

    assert persona is not None
    assert pin["id"] == "memory-compiler"
    assert pin["revision"] == persona.revision
    assert pin["definition"] == persona.definition
    document = yaml.safe_load((BUNDLED_SYSTEM_WORKFLOWS_PATH / "memory-compile.yaml").read_text())
    assert document["persona_dependencies"]["memory-compiler"]["digest"] == persona.digest


def test_the_compiler_only_adds_cites_its_sources_and_has_no_guessed_turn_cap() -> None:
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "memory-compiler"
    )
    prompt = persona.system_prompt_template

    for tool in ("memory_read", "memory_entity", "memory_search", "memory_write_page"):
        assert tool in prompt
    assert "memory_add_timeline" in prompt
    assert "Only add" in prompt
    assert "never retire" in prompt.lower() or "Never retire" in prompt
    assert "source:<id>" in prompt and "url:<" in prompt
    assert "proposed" in prompt
    assert persona.iteration_budget == 0
    assert persona.produces.event_type_map == {
        "pass": "memory.compiled",
        "fail": "memory.compile.failed",
    }
    assert persona.consumes.event_types == ["memory.compile.requested"]


# -- Starting the run -----------------------------------------------------------------


class _Workflows:
    def __init__(self, workflow) -> None:  # noqa: ANN001
        self._workflow = workflow

    async def get_workflow(self, workflow_id):  # noqa: ANN001
        return self._workflow if self._workflow and self._workflow.id == workflow_id else None


class _Forge:
    def __init__(self, role: str | None, target_id: str = "forge-local") -> None:
        self.role = role
        self.target_id = target_id
        self.asked: list[tuple[str, str]] = []

    async def get_project_role(self, project_id, *, auth_token=None, principal=None):  # noqa: ANN001
        self.asked.append((project_id, principal.user_id))
        return self.role


class _Factory:
    def __init__(self, *forges: _Forge) -> None:
        self.forges = list(forges)
        self.principals: list = []

    async def for_principal(self, principal):  # noqa: ANN001
        self.principals.append(principal)
        return self.forges


class Launch:
    def __init__(self, *forges: _Forge, workflow="bundled") -> None:  # noqa: ANN001
        self.campaigns = RecordingCampaignRepository()
        self.runs = FakeWorkflowRuns()
        self.factory = _Factory(*forges)
        self.launcher = WorkflowCompileLauncher(
            workflows=_Workflows(_bundled() if workflow == "bundled" else workflow),
            campaigns=self.campaigns,
            runs=lambda: self.runs,
            volundr_factory=self.factory,
            persona_source=FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True),
        )

    async def start(self, source_ids=("src-1", "src-2")):  # noqa: ANN001
        return await self.launcher.launch(
            project_id=PROJECT_ID, tenant_id="default", owner_id="jozef", source_ids=source_ids
        )


async def test_the_compile_run_starts_as_the_owner_in_the_project_with_the_source_ids() -> None:
    forge = _Forge("owner")
    launch = Launch(forge)

    run_id = await launch.start()

    ((campaign, payload),) = launch.runs.launched
    assert run_id == str(campaign.id)
    assert campaign.owner_id == "jozef"
    assert campaign.tenant_id == "default"
    assert campaign.workflow_id == MEMORY_COMPILE_WORKFLOW_ID
    assert campaign.workflow_name == "Memory compile"
    assert campaign.connection_id == "forge-local"
    assert campaign.status is WorkflowCampaignStatus.PENDING
    assert campaign.metadata[RUN_PROJECT_KEY] == str(PROJECT_ID)
    assert campaign.metadata["surface"] == COMPILE_SURFACE
    assert campaign.metadata["prompt"] == COMPILE_PROMPT
    assert campaign.workflow_snapshot["persona_definitions"]["memory-compiler"]
    assert payload["prompt"] == COMPILE_PROMPT == "Compile these sources into the project's memory"
    assert payload["context"][COMPILE_CONTEXT_SOURCE_IDS] == ["src-1", "src-2"]
    assert payload["context"]["layer"] == f"project:{PROJECT_ID}"
    assert "src-2" in payload["initiative_context"]
    assert forge.asked == [(str(PROJECT_ID), "jozef")]
    assert launch.factory.principals[0].user_id == "jozef"
    assert launch.campaigns.items[campaign.id] == campaign


async def test_the_forge_that_has_the_project_is_the_one_the_run_uses() -> None:
    elsewhere, here = _Forge(None, "forge-a"), _Forge("owner", "forge-b")
    launch = Launch(elsewhere, here)

    await launch.start()

    assert launch.runs.launched[0][0].connection_id == "forge-b"


@pytest.mark.parametrize(
    ("forges", "message"),
    [
        ([], "no Forge connection"),
        ([_Forge(None)], "not on any Forge"),
        ([_Forge("contributor")], "no longer an owner"),
    ],
    ids=["no forge", "project not found", "no longer an owner"],
)
async def test_a_run_that_cannot_be_started_as_the_owner_is_an_error_not_a_skip(
    forges: list[_Forge], message: str
) -> None:
    launch = Launch(*forges)

    with pytest.raises(CompileLaunchError, match=message):
        await launch.start()

    assert launch.runs.launched == []
    assert launch.campaigns.items == {}


async def test_a_workflow_missing_from_the_catalogue_names_the_setting() -> None:
    launch = Launch(_Forge("owner"), workflow=None)

    with pytest.raises(CompileLaunchError, match="seed_bundled"):
        await launch.start()


async def test_a_compile_without_sources_is_refused() -> None:
    with pytest.raises(CompileLaunchError, match="at least one source"):
        await Launch(_Forge("owner")).start(source_ids=())


async def test_a_run_the_engine_cannot_start_fails_its_campaign_with_the_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    launch = Launch(_Forge("owner"))
    launch.runs.launch_error = RuntimeError("no free session slot")

    with caplog.at_level(logging.ERROR), pytest.raises(CompileLaunchError, match="no free session"):
        await launch.start()

    (campaign,) = launch.campaigns.items.values()
    assert campaign.status is WorkflowCampaignStatus.FAILED
    assert campaign.metadata["failure_error"] == "no free session slot"
    assert any("could not start" in r.getMessage() for r in caplog.records)


async def test_an_import_with_new_sources_starts_the_real_launcher_end_to_end() -> None:
    harness = Harness(issue(1, minutes=10), issue(2, minutes=20))
    launch = Launch(_Forge("owner"))
    harness.service._compiler = launch.launcher
    record = await harness.create()

    result = await harness.service.run(record.id)

    ((campaign, payload),) = launch.runs.launched
    assert result.compile_run == str(campaign.id)
    assert payload["context"][COMPILE_CONTEXT_SOURCE_IDS] == list(result.new_source_ids)
    assert len(result.new_source_ids) == 2


# -- The loop -------------------------------------------------------------------------


async def test_the_loop_runs_every_import_at_once_and_then_every_interval() -> None:
    harness = Harness(issue(1, minutes=10))
    record = await harness.create()
    worker = MemoryImportWorker(service=harness.service, interval_seconds=0.01)

    await worker.start()
    assert worker.running
    for _ in range(200):
        if (await harness.record(record.id)).last_run_at is not None:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    await worker.stop()

    assert not worker.running
    assert (await harness.record(record.id)).cursor is not None
    assert len(harness.gateway.writes) == 1  # later passes found nothing new
    assert len(harness.tracker.reads) >= 2


async def test_a_pass_that_cannot_list_the_imports_is_logged_and_tried_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness()

    async def broken():
        raise ConnectionError("database down")

    harness.repository.list_all = broken  # type: ignore[method-assign]
    worker = MemoryImportWorker(service=harness.service, interval_seconds=0.01)

    with caplog.at_level(logging.ERROR):
        assert await worker.run_once() == (0, 0)

    assert any("could not run" in r.getMessage() for r in caplog.records)


async def test_stopping_a_worker_that_never_started_is_harmless() -> None:
    worker = MemoryImportWorker(service=Harness().service, interval_seconds=1)

    await worker.stop()
    await worker.start()
    await worker.start()
    await worker.stop()


def test_the_loop_needs_a_positive_interval() -> None:
    with pytest.raises(ValueError, match="poll_seconds"):
        MemoryImportWorker(service=Harness().service, interval_seconds=0)


# -- Configuration --------------------------------------------------------------------


def test_memory_imports_are_off_by_default_with_documented_knobs() -> None:
    config = Settings().memory_imports

    assert config.enabled is False
    assert config.gateway_url == ""
    assert config.poll_seconds == 300.0
    assert config.gateway_timeout_seconds == 30.0
    assert config.gateway_auth.adapter.endswith("NoAuthHeaderAdapter")
    for name, field in MemoryImportsConfig.model_fields.items():
        assert field.description, f"memory_imports.{name} has no description"


@pytest.mark.parametrize("key", ["poll_seconds", "gateway_timeout_seconds"])
def test_a_non_positive_interval_or_timeout_is_refused(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        MemoryImportsConfig(**{key: 0})


# -- Wiring ---------------------------------------------------------------------------


def _routes(**memory_imports) -> set[str]:
    from ting.main import create_app

    app = create_app(Settings(memory_imports=memory_imports))
    return set(app.openapi()["paths"])


def test_the_import_routes_exist_only_when_imports_are_enabled() -> None:
    off = _routes()
    on = _routes(enabled=True, gateway_url="http://mimir.test/api/v1/mimir")

    assert "/api/v1/ting/memory/imports" not in off
    assert {
        "/api/v1/ting/memory/imports",
        "/api/v1/ting/memory/imports/{import_id}",
        "/api/v1/ting/memory/imports/{import_id}/run",
    } <= on
