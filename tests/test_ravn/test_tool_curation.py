"""Learned-tool usage evidence, lineage, and retirement.

The defect these cover, as measured on the Kubernetes residents: tools were
built and mostly not used, a failing tool was answered with a new variant
instead of a repair (twelve ``list_pods_in_namespace*`` on one resident), and
nothing retired variants, debug probes or never-used adoptions, so every one
stayed in ``capability_list``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ravn.adapters.reflection.capability_matching import SemanticCapabilityMatcher
from ravn.adapters.reflection.lesson_judge import LessonJudgeError
from ravn.adapters.tools.build_tool import BuildTool
from ravn.adapters.tools.capability_catalog import CapabilityListTool
from ravn.adapters.tools.learned_tool_run import LearnedToolRunTool
from ravn.ports.capability_matching import CapabilityDescriptor, CapabilityVerdict
from ravn.ports.tool_build_backend import ToolBuildResult
from ravn.valkyrie_evolution.learned_tools import (
    LearnedToolError,
    LearnedToolResolver,
    LearnedToolUsageError,
    learned_tool_artifact_path,
    learned_tool_path,
    learned_tool_storage,
    retired_artifact_path,
    retired_tool_path,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest
from ravn.valkyrie_evolution.tool_curation import (
    RETIRED_DEBUG_LEFTOVER,
    RETIRED_SUPERSEDED,
    RETIRED_UNRUNNABLE,
    RETIRED_UNUSED,
    LearnedToolCurator,
    LearnedToolRetirements,
    LearnedToolUsageLedger,
    LifecycleSnapshot,
    find_lineage,
    learned_tool_health,
    restore_learned_tool,
    retire_learned_tool,
)

_CODE = "def run(payload):\n    return {'ok': True}\n"
_TEST = "import _verify_tool\n\ndef test_ok():\n    assert _verify_tool.run({})['ok']\n"


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _install(
    state: Path,
    name: str,
    *,
    capability: str = "",
    description: str = "",
    verified: bool = True,
    created_days_ago: float = 0.0,
    supersedes: str = "",
    tool_code: str = _CODE,
) -> LearnedToolArtifact:
    artifact = LearnedToolArtifact(
        artifact_id=f"learned-tool:{name}",
        manifest=LearnedToolManifest(
            name=name,
            description=description or f"Tool {name}.",
            input_schema={"type": "object", "properties": {"namespace": {"type": "string"}}},
            required_permission="tool:run",
        ),
        tool_code=tool_code,
        test_code=_TEST,
        source_gap_id=capability,
        provenance={"verification": {"ok": True, "logs": "fixture"}} if verified else {},
        supersedes=supersedes,
        created_at=_iso(created_days_ago),
    )
    code_dir, artifacts_dir = learned_tool_storage(state)
    write_learned_tool(tools_dir=code_dir, artifact=artifact)
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=artifact)
    return artifact


def _ledger(state: Path) -> LearnedToolUsageLedger:
    return LearnedToolUsageLedger(learned_tool_storage(state)[1])


def _write_lifecycle(state: Path, rows: dict[str, dict[str, Any]]) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "skill_management.json").write_text(json.dumps(rows), encoding="utf-8")


class FakeMatcher:
    """Says two tools are the same capability when their names are in one set."""

    def __init__(self, *groups: set[str]) -> None:
        self._groups = groups
        self.calls = 0
        self.revisions: list[bool] = []

    async def same_capability(
        self,
        subject: CapabilityDescriptor,
        candidates: Sequence[CapabilityDescriptor],
        *,
        as_revision: bool = False,
    ) -> list[CapabilityVerdict]:
        self.calls += 1
        self.revisions.append(as_revision)
        return [
            CapabilityVerdict(
                candidate_key=candidate.key,
                same=any({subject.name, candidate.name} <= group for group in self._groups),
                similarity=0.8,
                reason="fake",
            )
            for candidate in candidates
            if candidate.key != subject.key
        ]


# ---------------------------------------------------------------------------
# Usage ledger
# ---------------------------------------------------------------------------


class TestUsageLedger:
    def test_first_load_seeds_history_from_the_lifecycle_registry(self, tmp_path: Path) -> None:
        """Without the seed every pre-ledger tool would read as never used."""
        _install(tmp_path, "busy", created_days_ago=30)
        _install(tmp_path, "once", created_days_ago=10)
        _install(tmp_path, "idle", created_days_ago=20)
        _write_lifecycle(
            tmp_path,
            {
                "busy": {
                    "run_count": 500,
                    "success_count": 498,
                    "failure_count": 2,
                    "last_used_at": _iso(0.1),
                    "created_at": _iso(31),
                },
                "once": {"run_count": 1, "success_count": 1, "last_used_at": _iso(9)},
            },
        )

        usage = _ledger(tmp_path).snapshot()

        assert usage["busy"].runs == 500
        assert usage["busy"].seeded_from == "skill_management"
        # The lineage install time, not the current version's creation time.
        assert usage["busy"].installed_at < _iso(30.5)
        assert usage["busy"].first_use_observed is False
        # One run: its only use is its first, so its wait is known.
        assert usage["once"].first_use_observed is True
        assert usage["idle"].runs == 0
        assert usage["idle"].seeded_from == "artifact"
        assert _ledger(tmp_path).path.is_file()

    def test_runs_record_first_use_versions_and_failure_streaks(self, tmp_path: Path) -> None:
        _install(tmp_path, "probe")
        ledger = _ledger(tmp_path)

        ledger.record_run("probe", "v1", True)
        ledger.record_run("probe", "v1", False)
        ledger.record_run("probe", "v1", False)
        usage = ledger.get("probe")
        assert usage is not None
        assert (usage.runs, usage.successes, usage.failures) == (3, 1, 2)
        assert usage.consecutive_failures == 2
        assert usage.first_use_observed is True
        assert usage.first_use_wait_seconds() is not None

        ledger.record_install("probe", "v2", repair=True)
        ledger.record_run("probe", "v2", True)
        usage = ledger.get("probe")
        assert usage is not None
        assert usage.repairs == 1
        assert (usage.version_artifact_id, usage.version_runs) == ("v2", 1)
        assert usage.consecutive_failures == 0
        assert usage.runs == 4

    def test_the_never_used_window_is_the_longest_observed_first_use(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        assert ledger.first_use_window().seconds is None

        ledger.record_install("quick", "a")
        ledger.record_run("quick", "a", True)
        path = ledger.path
        payload = json.loads(path.read_text())
        payload["tools"]["slow"] = {
            "name": "slow",
            "installed_at": _iso(5),
            "first_used_at": _iso(2),
            "first_use_observed": True,
            "runs": 1,
        }
        path.write_text(json.dumps(payload))

        window = ledger.first_use_window()
        assert window.tool == "slow"
        assert window.observations == 2
        assert window.seconds == pytest.approx(3 * 86400, rel=0.01)

    def test_a_read_only_ledger_refuses_to_record(self, tmp_path: Path) -> None:
        ledger = LearnedToolUsageLedger(learned_tool_storage(tmp_path)[1], persist=False)
        with pytest.raises(LearnedToolUsageError, match="read-only"):
            ledger.record_run("probe", "v1", True)
        assert not ledger.path.exists()

    def test_a_corrupt_ledger_is_never_silently_replaced(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        ledger.path.parent.mkdir(parents=True)
        ledger.path.write_text("{not json")
        with pytest.raises(LearnedToolUsageError, match="never silently replaced"):
            ledger.record_run("probe", "v1", True)
        ledger.path.write_text(json.dumps({"version": 7}))
        with pytest.raises(LearnedToolUsageError, match="unknown layout"):
            ledger.snapshot()


# ---------------------------------------------------------------------------
# Retirement
# ---------------------------------------------------------------------------


class TestRetirement:
    def test_retiring_moves_the_files_and_restoring_puts_them_back(self, tmp_path: Path) -> None:
        _install(tmp_path, "old_probe")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)

        record = retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            name="old_probe",
            reason=RETIRED_UNUSED,
            detail="never run",
        )

        assert record.artifact_id == "learned-tool:old_probe"
        assert not learned_tool_path(code_dir, "old_probe").exists()
        assert retired_tool_path(code_dir, "old_probe").is_file()
        assert retired_artifact_path(artifacts_dir, "old_probe").is_file()
        # The loader and the catalog no longer see it.
        assert LearnedToolResolver(state_dir=tmp_path).list_artifacts() == []
        with pytest.raises(LearnedToolError, match="is retired"):
            LearnedToolResolver(state_dir=tmp_path).load("old_probe")

        restore_learned_tool(
            tools_dir=code_dir, artifacts_dir=artifacts_dir, name="old_probe", why="asked"
        )

        assert learned_tool_path(code_dir, "old_probe").is_file()
        retirements = LearnedToolRetirements(artifacts_dir)
        assert retirements.get("old_probe") is None
        assert retirements.counts() == {"retired.unused": 1, "restored": 1}

    def test_retiring_needs_an_installed_tool_and_a_known_reason(self, tmp_path: Path) -> None:
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        with pytest.raises(LearnedToolError, match="unknown retirement reason"):
            retire_learned_tool(
                tools_dir=code_dir, artifacts_dir=artifacts_dir, name="x", reason="bored", detail=""
            )
        with pytest.raises(LearnedToolError, match="no installed envelope"):
            retire_learned_tool(
                tools_dir=code_dir,
                artifacts_dir=artifacts_dir,
                name="x",
                reason=RETIRED_UNUSED,
                detail="",
            )
        with pytest.raises(LearnedToolError, match="not retired"):
            restore_learned_tool(tools_dir=code_dir, artifacts_dir=artifacts_dir, name="x", why="")

    def test_restore_never_overwrites_an_installed_tool(self, tmp_path: Path) -> None:
        _install(tmp_path, "dup")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            name="dup",
            reason=RETIRED_UNUSED,
            detail="",
        )
        _install(tmp_path, "dup")
        with pytest.raises(LearnedToolError, match="already exists"):
            restore_learned_tool(
                tools_dir=code_dir, artifacts_dir=artifacts_dir, name="dup", why=""
            )
        with pytest.raises(LearnedToolError, match="retired copy already exists"):
            retire_learned_tool(
                tools_dir=code_dir,
                artifacts_dir=artifacts_dir,
                name="dup",
                reason=RETIRED_UNUSED,
                detail="",
            )


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


class TestLineage:
    async def test_declared_capability_wins_and_retired_superseded_resolves_to_keeper(
        self, tmp_path: Path
    ) -> None:
        _install(tmp_path, "k8s.pods.list", capability="k8s.pods.list")
        _install(tmp_path, "list_pods_in_namespace_fixed", capability="k8s.pods.list")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            name="list_pods_in_namespace_fixed",
            reason=RETIRED_SUPERSEDED,
            detail="",
            superseded_by="k8s.pods.list",
        )

        found = await find_lineage(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            capability_id="",
            name="list_pods_in_namespace_fixed",
            description="",
        )

        assert found is not None
        assert found.entry.name == "k8s.pods.list"
        assert found.entry.retired is None
        assert "superseded" in found.reason

    async def test_a_shared_capability_resolves_to_the_tool_that_runs(self, tmp_path: Path) -> None:
        _install(tmp_path, "k8s_pod_logs_since", capability="k8s.pod_logs_since", verified=False)
        _install(tmp_path, "k8s_pod_logs_since_sa", capability="k8s.pod_logs_since")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)

        found = await find_lineage(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            capability_id="k8s.pod_logs_since",
            name="k8s_pod_logs_since_v2",
            description="",
        )

        assert found is not None
        assert found.entry.name == "k8s_pod_logs_since_sa"

    async def test_meaning_links_names_that_spelling_cannot(self, tmp_path: Path) -> None:
        _install(tmp_path, "list_pods_in_namespace_sa")
        _install(tmp_path, "get_namespace_events")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        matcher = FakeMatcher({"list_pods_in_namespace_safe_v3", "list_pods_in_namespace_sa"})

        found = await find_lineage(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            capability_id="",
            name="list_pods_in_namespace_safe_v3",
            description="list pods",
            matcher=matcher,
        )

        assert found is not None
        assert found.entry.name == "list_pods_in_namespace_sa"
        assert found.matched_by == "semantic"
        # A build request is judged as a possible next version of a tool.
        assert matcher.revisions == [True]

    async def test_no_tools_means_no_lineage(self, tmp_path: Path) -> None:
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        assert (
            await find_lineage(
                tools_dir=code_dir,
                artifacts_dir=artifacts_dir,
                capability_id="x",
                name="y",
                description="",
                matcher=FakeMatcher(),
            )
            is None
        )


# ---------------------------------------------------------------------------
# Curation
# ---------------------------------------------------------------------------


class TestCurator:
    async def test_a_variant_family_keeps_the_tool_that_works(self, tmp_path: Path) -> None:
        _install(tmp_path, "list_pods_in_namespace_fixed", capability="k8s.pods.list")
        _install(tmp_path, "list_pods_in_namespace_safe", capability="k8s.pods.list")
        _install(tmp_path, "k8s.pods.list", capability="k8s.pods.list")
        ledger = _ledger(tmp_path)
        ledger.record_run("list_pods_in_namespace_fixed", "a", True)
        for _ in range(3):
            ledger.record_run("k8s.pods.list", "b", True)

        plan = await LearnedToolCurator(state_dir=tmp_path).plan()

        decisions = {decision.name: decision for decision in plan.retire}
        assert set(decisions) == {"list_pods_in_namespace_fixed", "list_pods_in_namespace_safe"}
        assert {decision.reason for decision in decisions.values()} == {RETIRED_SUPERSEDED}
        assert {decision.superseded_by for decision in decisions.values()} == {"k8s.pods.list"}
        assert plan.keep == ["k8s.pods.list"]

    async def test_an_explicit_supersedes_link_and_spelling_group_tools(
        self, tmp_path: Path
    ) -> None:
        first = _install(tmp_path, "list_k8s_pods")
        _install(tmp_path, "list_k8s_pod_names", supersedes=first.artifact_id)
        _install(tmp_path, "inspect_k8s_node_disk_pressure")
        _install(tmp_path, "inspect_kubernetes_node_disk_pressure")

        plan = await LearnedToolCurator(state_dir=tmp_path).plan()

        assert len(plan.groups) == 2
        assert {decision.reason for decision in plan.retire} == {RETIRED_SUPERSEDED}

    async def test_debug_leftovers_unrunnable_and_unused_tools_are_retired(
        self, tmp_path: Path
    ) -> None:
        _install(tmp_path, "debug_k8s_config")
        _install(tmp_path, "test_tool")
        _install(tmp_path, "patch_node_labels_via_sa", verified=False)
        _install(tmp_path, "longhorn_engine_status", created_days_ago=8)
        _install(tmp_path, "prometheus_rules_query", created_days_ago=1)
        _install(tmp_path, "debug_but_useful")
        ledger = _ledger(tmp_path)
        ledger.record_run("debug_but_useful", "x", True)
        # One tool waited two days before its first use: that is the window.
        payload = json.loads(ledger.path.read_text())
        payload["tools"]["slow_starter"] = {
            "name": "slow_starter",
            "installed_at": _iso(4),
            "first_used_at": _iso(2),
            "first_use_observed": True,
            "runs": 1,
        }
        ledger.path.write_text(json.dumps(payload))

        plan = await LearnedToolCurator(
            state_dir=tmp_path, leftover_name_prefixes=["debug", "test"]
        ).plan()

        reasons = {decision.name: decision.reason for decision in plan.retire}
        assert reasons == {
            "debug_k8s_config": RETIRED_DEBUG_LEFTOVER,
            "test_tool": RETIRED_DEBUG_LEFTOVER,
            "patch_node_labels_via_sa": RETIRED_UNRUNNABLE,
            "longhorn_engine_status": RETIRED_UNUSED,
        }
        unused = next(d for d in plan.retire if d.name == "longhorn_engine_status")
        assert "slow_starter" in unused.detail
        assert sorted(plan.keep) == ["debug_but_useful", "prometheus_rules_query"]

    async def test_without_an_observed_first_use_nothing_is_retired_as_unused(
        self, tmp_path: Path
    ) -> None:
        _install(tmp_path, "ancient_never_used", created_days_ago=60)

        plan = await LearnedToolCurator(state_dir=tmp_path).plan()

        assert plan.retire == []
        assert plan.window.seconds is None

    async def test_pinned_and_rolled_back_tools(self, tmp_path: Path) -> None:
        _install(tmp_path, "debug_pinned")
        _install(tmp_path, "rolled_back_tool")
        _write_lifecycle(
            tmp_path,
            {
                "debug_pinned": {"status": "active", "pinned": True},
                "rolled_back_tool": {"status": "archived"},
            },
        )
        skills = LifecycleSnapshot(tmp_path / "skill_management.json")

        plan = await LearnedToolCurator(
            state_dir=tmp_path, skills=skills, leftover_name_prefixes=["debug"]
        ).plan()

        assert [(d.name, d.reason) for d in plan.retire] == [
            ("rolled_back_tool", RETIRED_UNRUNNABLE)
        ]
        assert plan.installed == 1

    async def test_semantic_groups_are_judged_once_and_cached(self, tmp_path: Path) -> None:
        _install(tmp_path, "list_pods_in_namespace_working")
        _install(tmp_path, "k8s_pods_list_namespace")
        _install(tmp_path, "get_namespace_events")
        _ledger(tmp_path).record_run("k8s_pods_list_namespace", "x", True)
        _ledger(tmp_path).record_run("get_namespace_events", "y", True)
        matcher = FakeMatcher({"list_pods_in_namespace_working", "k8s_pods_list_namespace"})
        curator = LearnedToolCurator(state_dir=tmp_path, matcher=matcher)

        plan = await curator.plan()
        calls = matcher.calls
        again = await curator.plan()

        assert [(d.name, d.superseded_by) for d in plan.retire] == [
            ("list_pods_in_namespace_working", "k8s_pods_list_namespace")
        ]
        # Retiring needs a stand-in, not a possible next version.
        assert set(matcher.revisions) == {False}
        assert plan.judged_pairs > 0
        assert matcher.calls == calls
        assert again.judged_pairs == 0
        assert [d.name for d in again.retire] == ["list_pods_in_namespace_working"]

    async def test_a_dry_plan_writes_nothing(self, tmp_path: Path) -> None:
        _install(tmp_path, "a_tool")
        curator = LearnedToolCurator(state_dir=tmp_path, matcher=FakeMatcher(), persist=False)

        await curator.plan()

        artifacts_dir = learned_tool_storage(tmp_path)[1]
        assert not (artifacts_dir / "_curation").exists()

    async def test_curate_retires_and_archives_the_lifecycle(self, tmp_path: Path) -> None:
        _install(tmp_path, "debug_input")
        archived: list[str] = []

        class Skills:
            def status(self, name: str) -> str | None:
                return "archived" if name in archived else "active"

            def lifecycle_metadata(self, name: str) -> dict[str, Any]:
                return {"pinned": False}

            async def archive(self, name: str) -> None:
                archived.append(name)

        summary = await LearnedToolCurator(
            state_dir=tmp_path, skills=Skills(), leftover_name_prefixes=["debug"]
        ).curate()

        assert [item["name"] for item in summary["retired"]] == ["debug_input"]
        assert archived == ["debug_input"]
        health = learned_tool_health(tmp_path)
        assert health["learned_tools_installed"] == 0
        assert health["learned_tools_retired"] == 1
        assert health["learned_tool_retirements.debug_leftover"] == 1
        assert health["learned_tool_retirements_total"] == 1


# ---------------------------------------------------------------------------
# Semantic matcher adapter
# ---------------------------------------------------------------------------


class _Index:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors
        self.embedded: list[str] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [self._vectors[text] for text in texts]


class _Judge:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def ask_json(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.answer


def _descriptor(name: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(key=name, name=name, description=name, inputs=("namespace",))


class TestSemanticMatcher:
    def _matcher(self, judge: _Judge) -> tuple[SemanticCapabilityMatcher, _Index]:
        index = _Index(
            {
                "pods: pods": [1.0, 0.0],
                "pods2: pods2": [0.9, 0.1],
                "events: events": [0.0, 1.0],
            }
        )
        matcher = SemanticCapabilityMatcher(
            lessons=index,  # type: ignore[arg-type]
            judge=judge,  # type: ignore[arg-type]
            similarity_threshold=0.5,
            max_candidates=5,
            excerpt_chars=100,
        )
        return matcher, index

    async def test_only_the_shortlist_is_judged(self) -> None:
        judge = _Judge({"same": [1], "reason": "both list pods"})
        matcher, index = self._matcher(judge)

        verdicts = await matcher.same_capability(
            _descriptor("pods"), [_descriptor("pods2"), _descriptor("events")]
        )

        assert [(v.candidate_key, v.same) for v in verdicts] == [("pods2", True)]
        assert "events" not in judge.prompts[0]
        await matcher.same_capability(_descriptor("pods"), [_descriptor("pods2")])
        # Descriptions are embedded once per matcher, not once per comparison.
        assert sorted(index.embedded) == ["events: events", "pods2: pods2", "pods: pods"]

    async def test_a_build_request_is_judged_as_a_revision(self) -> None:
        judge = _Judge({"same": [1]})
        matcher, _ = self._matcher(judge)

        await matcher.same_capability(_descriptor("pods"), [_descriptor("pods2")])
        await matcher.same_capability(_descriptor("pods"), [_descriptor("pods2")], as_revision=True)

        assert "next version" not in judge.prompts[0]
        assert "next version should do" in judge.prompts[1]

    async def test_nothing_close_enough_makes_no_model_call(self) -> None:
        judge = _Judge({"same": []})
        matcher, _ = self._matcher(judge)

        assert await matcher.same_capability(_descriptor("pods"), [_descriptor("events")]) == []
        assert judge.prompts == []

    @pytest.mark.parametrize(
        "answer",
        [{"same": 1}, {"same": ["one"]}, {"same": [4]}, {"same": [True]}],
    )
    async def test_an_unusable_answer_raises(self, answer: dict[str, Any]) -> None:
        matcher, _ = self._matcher(_Judge(answer))
        with pytest.raises(LessonJudgeError):
            await matcher.same_capability(_descriptor("pods"), [_descriptor("pods2")])

    def test_it_needs_candidates(self) -> None:
        with pytest.raises(ValueError, match="max_candidates"):
            SemanticCapabilityMatcher(
                lessons=_Index({}),  # type: ignore[arg-type]
                judge=_Judge({}),  # type: ignore[arg-type]
                similarity_threshold=0.5,
                max_candidates=0,
                excerpt_chars=10,
            )


# ---------------------------------------------------------------------------
# build_tool: repair over variant
# ---------------------------------------------------------------------------


class RecordingBackend:
    """A commissioned build that remembers what it was asked for."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    @property
    def name(self) -> str:
        return "recording"

    async def build(self, request: Any) -> ToolBuildResult:
        self.requests.append(request)
        return ToolBuildResult(
            manifest={
                "name": request.name,
                "description": request.description,
                "input_schema": {"type": "object"},
                "required_permission": "tool:run",
            },
            tool_code="def run(payload):\n    return {'ok': True, 'v': 2}\n",
            test_code=_TEST,
            requirements=[],
            provenance={},
        )


def _build_tool(state: Path, **kwargs: Any) -> tuple[BuildTool, list[Any]]:
    registered: list[Any] = []
    code_dir, artifacts_dir = learned_tool_storage(state)
    tool = BuildTool(
        tools_dir=code_dir,
        artifacts_dir=artifacts_dir,
        register_tool=lambda tool, replace=False: registered.append((tool, replace)),
        autonomy_mode="autonomous",
        **kwargs,
    )
    return tool, registered


def _request(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "manifest": {
            "name": name,
            "description": "list pods in a namespace",
            "input_schema": {"type": "object"},
            "required_permission": "tool:run",
        },
        "build_request": "list the pods in a namespace",
        **extra,
    }


class TestRepairOverVariant:
    async def test_a_failing_tool_is_repaired_in_place_not_forked(self, tmp_path: Path) -> None:
        _install(tmp_path, "list_pods_in_namespace", capability="k8s.pods.list")
        ledger = _ledger(tmp_path)
        ledger.record_run("list_pods_in_namespace", "learned-tool:list_pods_in_namespace", False)
        backend = RecordingBackend()
        tool, registered = _build_tool(tmp_path, build_backend=backend)

        result = await tool.execute(
            _request("list_pods_in_namespace_fixed", capability_id="k8s.pods.list")
        )

        summary = json.loads(result.content)
        assert result.is_error is False, result.content
        assert summary["tool_name"] == "list_pods_in_namespace"
        assert summary["version"] == 2
        assert summary["repair_of"]["problems"] == ["its last 1 run(s) failed"]
        request = backend.requests[0]
        assert request.name == "list_pods_in_namespace"
        assert "REPAIRS the installed learned tool 'list_pods_in_namespace'" in (
            request.signal_context
        )
        assert _CODE in request.signal_context
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        assert not learned_tool_artifact_path(
            artifacts_dir, "list_pods_in_namespace_fixed"
        ).exists()
        assert registered[0][1] is True
        usage = _ledger(tmp_path).get("list_pods_in_namespace")
        assert usage is not None and usage.repairs == 1

    async def test_a_semantic_match_repairs_the_matched_tool(self, tmp_path: Path) -> None:
        _install(tmp_path, "k8s_pods_list_namespace", verified=False)
        backend = RecordingBackend()
        tool, _ = _build_tool(
            tmp_path,
            build_backend=backend,
            capability_matcher=FakeMatcher(
                {"list_pods_in_namespace_v2", "k8s_pods_list_namespace"}
            ),
        )

        result = await tool.execute(_request("list_pods_in_namespace_v2"))

        summary = json.loads(result.content)
        assert summary["tool_name"] == "k8s_pods_list_namespace"
        assert summary["repair_of"]["matched_by"] == "semantic"
        assert "Last verification" not in backend.requests[0].signal_context

    async def test_a_retired_tool_is_restored_instead_of_rebuilt(self, tmp_path: Path) -> None:
        _install(tmp_path, "longhorn_engine_status", capability="longhorn.engine.status")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            name="longhorn_engine_status",
            reason=RETIRED_UNUSED,
            detail="never run",
        )
        restored_lifecycle: list[str] = []

        class Skills:
            def status(self, name: str) -> str | None:
                return "active" if restored_lifecycle else "archived"

            async def restore(self, name: str) -> None:
                restored_lifecycle.append(name)

        backend = RecordingBackend()
        tool, _ = _build_tool(tmp_path, build_backend=backend, skill_manager=Skills())

        result = await tool.execute(
            _request("longhorn_engine_state", capability_id="longhorn.engine.status")
        )

        payload = json.loads(result.content)
        assert payload["status"] == "already_installed"
        assert payload["restored_from_retirement"]["reason"] == RETIRED_UNUSED
        assert backend.requests == []
        assert restored_lifecycle == ["longhorn_engine_status"]
        assert learned_tool_path(code_dir, "longhorn_engine_status").is_file()

    async def test_a_restored_unrunnable_tool_is_repaired(self, tmp_path: Path) -> None:
        _install(tmp_path, "inspect_persistentvolume", capability="k8s.pv.inspect")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            name="inspect_persistentvolume",
            reason=RETIRED_UNRUNNABLE,
            detail="rolled back",
        )
        backend = RecordingBackend()
        tool, _ = _build_tool(tmp_path, build_backend=backend)

        result = await tool.execute(_request("inspect_pv", capability_id="k8s.pv.inspect"))

        assert json.loads(result.content)["tool_name"] == "inspect_persistentvolume"
        assert len(backend.requests) == 1
        assert "retired as unrunnable" in backend.requests[0].signal_context

    async def test_a_judge_that_cannot_answer_builds_nothing(self, tmp_path: Path) -> None:
        _install(tmp_path, "some_tool")

        class BrokenMatcher:
            async def same_capability(self, *_: Any, **__: Any) -> list[CapabilityVerdict]:
                raise LessonJudgeError("unusable judge answer")

        backend = RecordingBackend()
        tool, _ = _build_tool(tmp_path, build_backend=backend, capability_matcher=BrokenMatcher())

        result = await tool.execute(_request("brand_new_capability"))

        assert result.is_error is True
        assert "could not decide" in result.content
        assert backend.requests == []

    async def test_a_failed_rebuild_never_replaces_the_working_envelope(
        self, tmp_path: Path
    ) -> None:
        installed = _install(tmp_path, "echo_tool", capability="echo")
        tool, registered = _build_tool(tmp_path)

        result = await tool.execute(
            {
                "manifest": {
                    "name": "echo_tool",
                    "description": "echo",
                    "input_schema": {"type": "object"},
                    "required_permission": "tool:run",
                },
                "tool_code": "def run(payload):\n    return {'ok': False}\n",
                "test_code": _TEST,
                "capability_id": "echo",
                "replace": True,
            }
        )

        assert result.is_error is True
        _, artifacts_dir = learned_tool_storage(tmp_path)
        envelope = json.loads(learned_tool_artifact_path(artifacts_dir, "echo_tool").read_text())
        assert envelope["artifact_id"] == installed.artifact_id
        assert envelope["provenance"]["verification"]["ok"] is True
        assert list((artifacts_dir / "_failed").glob("echo_tool__*.json"))
        assert registered == []

    async def test_the_canary_is_not_counted_as_a_use(self, tmp_path: Path) -> None:
        tool, registered = _build_tool(tmp_path)

        result = await tool.execute(
            {
                "manifest": {
                    "name": "fresh_tool",
                    "description": "fresh",
                    "input_schema": {"type": "object"},
                    "required_permission": "tool:run",
                },
                "tool_code": _CODE,
                "test_code": _TEST,
                "canary_input": {},
            }
        )

        assert result.is_error is False, result.content
        usage = _ledger(tmp_path).get("fresh_tool")
        assert usage is not None and usage.runs == 0
        await registered[0][0].execute({})
        usage = _ledger(tmp_path).get("fresh_tool")
        assert usage is not None and usage.runs == 1 and usage.first_use_observed


# ---------------------------------------------------------------------------
# Dispatch and discovery
# ---------------------------------------------------------------------------


class _Permission:
    def allows(self, permission: str) -> bool:
        return True

    async def check(self, permission: str) -> bool:
        return True


class TestDispatchAndCatalog:
    async def test_dispatch_records_usage_and_the_catalog_shows_it(self, tmp_path: Path) -> None:
        _install(tmp_path, "k8s_pvc_status")
        ledger = _ledger(tmp_path)
        resolver = LearnedToolResolver(state_dir=tmp_path, usage_recorder=ledger.record_run)
        run = LearnedToolRunTool(resolver=resolver, permission=_Permission())  # type: ignore[arg-type]

        result = await run.execute({"name": "k8s_pvc_status", "input": {}})

        assert result.is_error is False, result.content
        catalog = CapabilityListTool(
            tools_provider=lambda: [],
            learned_tools_provider=resolver.list_artifacts,
            learned_tool_usage=ledger.snapshot,
        )
        listing = json.loads((await catalog.execute({})).content)
        entry = next(item for item in listing["capabilities"] if item["name"] == "k8s_pvc_status")
        assert entry["usage"]["runs"] == 1
        assert entry["usage"]["successes"] == 1
        assert entry["usage"]["last_used"]
        detail = json.loads((await catalog.execute({"names": ["k8s_pvc_status"]})).content)
        metadata = detail["capabilities"][0]["metadata"]
        assert metadata["usage"]["version_runs"] == 1
        assert metadata["version"] == 1

    async def test_a_usage_write_failure_is_not_a_tool_failure(self, tmp_path: Path) -> None:
        _install(tmp_path, "k8s_pvc_status")

        def broken(name: str, artifact_id: str, succeeded: bool) -> None:
            raise LearnedToolUsageError("disk full")

        resolver = LearnedToolResolver(state_dir=tmp_path, usage_recorder=broken)
        run = LearnedToolRunTool(resolver=resolver, permission=_Permission())  # type: ignore[arg-type]

        with pytest.raises(LearnedToolUsageError, match="disk full"):
            await run.execute({"name": "k8s_pvc_status", "input": {}})


def test_versions_chain_through_the_artifact_writer(tmp_path: Path) -> None:
    first = _install(tmp_path, "chained")
    second = _install(tmp_path, "chained", tool_code="def run(payload):\n    return {'v': 2}\n")
    _, artifacts_dir = learned_tool_storage(tmp_path)

    # Same name, new id: archived predecessor, next version.
    assert second.artifact_id == first.artifact_id
    replacement = LearnedToolArtifact.from_dict(
        {**second.to_dict(), "artifact_id": "learned-tool:chained:v2"}
    )
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=replacement)
    envelope = json.loads(learned_tool_artifact_path(artifacts_dir, "chained").read_text())
    assert envelope["version"] == 2
    assert envelope["supersedes"] == first.artifact_id
    # Rewriting the same version keeps its number.
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=replacement)
    envelope = json.loads(learned_tool_artifact_path(artifacts_dir, "chained").read_text())
    assert envelope["version"] == 2


async def test_a_query_names_retired_tools_and_how_to_get_them_back(tmp_path: Path) -> None:
    _install(tmp_path, "k8s.pods.list")
    _install(tmp_path, "list_pods_in_namespace_fixed")
    _install(tmp_path, "longhorn_engine_status")
    code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    retire_learned_tool(
        tools_dir=code_dir,
        artifacts_dir=artifacts_dir,
        name="list_pods_in_namespace_fixed",
        reason=RETIRED_SUPERSEDED,
        detail="",
        superseded_by="k8s.pods.list",
    )
    retire_learned_tool(
        tools_dir=code_dir,
        artifacts_dir=artifacts_dir,
        name="longhorn_engine_status",
        reason=RETIRED_UNUSED,
        detail="never run",
    )

    def retired() -> list[Any]:
        from ravn.valkyrie_evolution.tool_curation import tool_inventory

        return [
            entry
            for entry in tool_inventory(
                tools_dir=code_dir, artifacts_dir=artifacts_dir, include_retired=True
            )
            if entry.retired is not None
        ]

    catalog = CapabilityListTool(
        tools_provider=lambda: [],
        learned_tools_provider=LearnedToolResolver(state_dir=tmp_path).list_artifacts,
        retired_learned_tools=retired,
    )

    pods = json.loads((await catalog.execute({"query": "pods"})).content)
    longhorn = json.loads((await catalog.execute({"query": "longhorn"})).content)
    unfiltered = json.loads((await catalog.execute({})).content)

    assert [item["name"] for item in pods["capabilities"]] == ["k8s.pods.list"]
    assert pods["retired_matches"][0]["next_step"] == "Use 'k8s.pods.list' instead."
    assert longhorn["capabilities"] == []
    assert "restores this tool" in longhorn["retired_matches"][0]["next_step"]
    assert "retired_matches" not in unfiltered


async def test_the_consolidation_dream_runs_tool_curation(tmp_path: Path) -> None:
    from ravn.adapters.skill.file_registry import FileSkillRegistry
    from ravn.skills.management import SkillManagementRegistry
    from ravn.valkyrie_evolution.resident_learning import ResidentLearningIdentity
    from ravn.valkyrie_evolution.wakefulness import ResidentWakefulness
    from sleipnir.adapters.in_process import InProcessBus

    class Curator:
        calls = 0

        async def curate(self) -> dict[str, Any]:
            Curator.calls += 1
            return {"retired": [{"name": "debug_input", "reason": RETIRED_DEBUG_LEFTOVER}]}

    skill_dir = tmp_path / "skills"
    machine = ResidentWakefulness(
        identity=ResidentLearningIdentity(
            environment_id="cluster-a", valkyrie_id="valkyrie:a", domain="k8s"
        ),
        skills=SkillManagementRegistry(
            FileSkillRegistry(
                skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False
            ),
            metadata_path=tmp_path / "skill_management.json",
        ),
        publisher=InProcessBus(),
        tool_curator=Curator(),
    )

    summary = await machine.dream()

    assert Curator.calls == 1
    assert summary["learned_tools"]["retired"][0]["name"] == "debug_input"


async def test_the_health_snapshot_carries_learned_tool_counts(tmp_path: Path) -> None:
    from ravn.resident_runtime import ResidentRuntime

    class State:
        async def count_cases(self) -> None:
            return None

        async def list_scheduled_wakes(self) -> list[Any]:
            return []

        async def read_decision_streak(self, resident_id: str) -> None:
            return None

    _install(tmp_path, "k8s_pvc_status")
    runtime = ResidentRuntime(state=State())  # type: ignore[arg-type]
    runtime.bind_learned_tool_health(lambda: learned_tool_health(tmp_path))

    snapshot = await runtime.refresh_health_snapshot()

    assert snapshot["learned_tools_installed"] == 1
    assert snapshot["learned_tools_never_used"] == 1
    assert snapshot["learned_tool_retirements_total"] == 0


async def test_a_peer_copy_does_not_undo_a_retirement_here(tmp_path: Path) -> None:
    from ravn.adapters.skill.file_registry import FileSkillRegistry
    from ravn.skills.management import SkillManagementRegistry
    from ravn.valkyrie_evolution.resident_learning import (
        ResidentLearningArtifact,
        ResidentLearningIdentity,
        ResidentLearningRuntime,
    )
    from sleipnir.adapters.in_process import InProcessBus

    state = tmp_path / "resident"
    _install(state, "list_pods_in_namespace_safe")
    _install(state, "k8s_quantity_parse")
    code_dir, artifacts_dir = learned_tool_storage(state)
    retire_learned_tool(
        tools_dir=code_dir,
        artifacts_dir=artifacts_dir,
        name="list_pods_in_namespace_safe",
        reason=RETIRED_SUPERSEDED,
        detail="",
        superseded_by="k8s.pods.list",
    )
    retire_learned_tool(
        tools_dir=code_dir,
        artifacts_dir=artifacts_dir,
        name="k8s_quantity_parse",
        reason=RETIRED_UNUSED,
        detail="never run",
    )
    skill_dir = state / "skills"
    bus = InProcessBus()
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="valhalla", valkyrie_id="valkyrie:valhalla", domain="k8s"
        ),
        skills=SkillManagementRegistry(
            FileSkillRegistry(
                skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False
            ),
            metadata_path=state / "skill_management.json",
        ),
        publisher=bus,
        subscriber=bus,
        tools_dir=state / "tools",
    )

    def proposal(name: str) -> ResidentLearningArtifact:
        return ResidentLearningArtifact(
            learning_id=f"learned-tool:{name}:peer",
            title=name,
            summary="",
            content="",
            artifact_type="agent_tool",
            scope="flock",
            confidence=0.74,
            source_valkyrie_id="valkyrie:noatun",
            learned_tool_manifest={"name": name},
        )

    ignored = await runtime._reconcile_retired_tool(proposal("list_pods_in_namespace_safe"))
    restored = await runtime._reconcile_retired_tool(proposal("k8s_quantity_parse"))

    assert ignored is not None and ignored.action == "ignored"
    assert "superseded" in ignored.rationale
    assert restored is None
    assert learned_tool_path(code_dir, "k8s_quantity_parse").is_file()
    assert not learned_tool_path(code_dir, "list_pods_in_namespace_safe").exists()


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_the_capability_matcher_follows_lesson_matching() -> None:
    from types import SimpleNamespace

    from ravn.cli.runtime_builders import _build_capability_matcher
    from ravn.config import Settings

    consolidation = SimpleNamespace(lessons=_Index({}), judge=_Judge({}))
    lexical = Settings()
    semantic = Settings(reflection={"matching": {"mode": "semantic"}})

    assert _build_capability_matcher(lexical, consolidation) is None
    assert _build_capability_matcher(semantic, None) is None
    assert isinstance(_build_capability_matcher(semantic, consolidation), SemanticCapabilityMatcher)


def test_the_curator_is_built_only_when_enabled(tmp_path: Path) -> None:
    from ravn.cli import commands
    from ravn.config import Settings

    enabled = Settings(state_dir=str(tmp_path))
    disabled = Settings(
        state_dir=str(tmp_path), resident_evolution={"tool_curation": {"enabled": False}}
    )

    curator = commands._build_learned_tool_curator(
        enabled, workspace=tmp_path, skills=None, matcher=None
    )

    assert isinstance(curator, LearnedToolCurator)
    assert (
        commands._build_learned_tool_curator(
            disabled, workspace=tmp_path, skills=None, matcher=None
        )
        is None
    )


def test_retired_tools_are_listed_fresh_for_the_catalog(tmp_path: Path) -> None:
    from ravn.cli.tool_builders import _retired_learned_tools

    _install(tmp_path, "debug_input")
    listing = _retired_learned_tools(tmp_path)
    assert listing() == []
    code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    retire_learned_tool(
        tools_dir=code_dir,
        artifacts_dir=artifacts_dir,
        name="debug_input",
        reason=RETIRED_DEBUG_LEFTOVER,
        detail="",
    )

    assert [entry.name for entry in listing()] == ["debug_input"]


def test_the_plan_command_prints_the_plan_and_changes_nothing(tmp_path: Path) -> None:
    from unittest.mock import patch

    from typer.testing import CliRunner

    from ravn.cli import commands
    from ravn.config import Settings

    _install(tmp_path, "debug_input")
    settings = Settings(state_dir=str(tmp_path))
    with (
        patch.object(commands, "Settings", return_value=settings),
        patch.object(commands, "_resolve_workspace", return_value=tmp_path),
        patch.object(commands, "_resident_ravn_state_dir", return_value=tmp_path),
        patch.object(commands, "_build_llm", return_value=None),
    ):
        result = CliRunner().invoke(commands.app, ["learned-tools-plan"])

    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    assert [item["name"] for item in plan["retire"]] == ["debug_input"]
    code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    assert learned_tool_path(code_dir, "debug_input").is_file()
    assert not (artifacts_dir / "_curation").exists()


def test_the_plan_command_refuses_when_curation_is_off(tmp_path: Path) -> None:
    from unittest.mock import patch

    from typer.testing import CliRunner

    from ravn.cli import commands
    from ravn.config import Settings

    settings = Settings(
        state_dir=str(tmp_path), resident_evolution={"tool_curation": {"enabled": False}}
    )
    with (
        patch.object(commands, "Settings", return_value=settings),
        patch.object(commands, "_resolve_workspace", return_value=tmp_path),
        patch.object(commands, "_build_llm", return_value=None),
    ):
        result = CliRunner().invoke(commands.app, ["learned-tools-plan"])

    assert result.exit_code == 1


def test_pre_ledger_history_bounds_the_window_so_one_fast_first_use_cannot_set_it(
    tmp_path: Path,
) -> None:
    """Live on vanaheim one 29-second first use made every older unused tool look dead."""
    _install(tmp_path, "busy", created_days_ago=30)
    _write_lifecycle(
        tmp_path,
        {"busy": {"run_count": 500, "last_used_at": _iso(2), "created_at": _iso(30)}},
    )
    ledger = _ledger(tmp_path)
    ledger.record_install("fresh", "f")
    ledger.record_run("fresh", "f", True)

    window = ledger.first_use_window()

    assert window.tool == "busy"
    assert window.basis == "bound"
    assert window.seconds == pytest.approx(28 * 86400, rel=0.01)
    # Frozen: more use later does not widen it.
    ledger.record_run("busy", "b", True)
    assert ledger.first_use_window().seconds == pytest.approx(28 * 86400, rel=0.01)


def test_a_ledger_seeded_before_bounds_existed_gets_them_on_load(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.path.parent.mkdir(parents=True)
    ledger.path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "busy": {
                        "name": "busy",
                        "installed_at": _iso(10),
                        "last_used_at": _iso(1),
                        "runs": 40,
                        "seeded_from": "skill_management",
                    }
                },
            }
        )
    )

    window = ledger.first_use_window()

    assert window.basis == "bound"
    assert window.seconds == pytest.approx(9 * 86400, rel=0.01)


async def test_an_unused_retirement_the_window_no_longer_supports_is_undone(
    tmp_path: Path,
) -> None:
    _install(tmp_path, "k8s_quantity_parse", created_days_ago=0.1)
    code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    retire_learned_tool(
        tools_dir=code_dir,
        artifacts_dir=artifacts_dir,
        name="k8s_quantity_parse",
        reason=RETIRED_UNUSED,
        detail="never run",
    )
    ledger = _ledger(tmp_path)
    ledger.record_install("k8s_quantity_parse", "learned-tool:k8s_quantity_parse")
    payload = json.loads(ledger.path.read_text())
    payload["tools"]["k8s_quantity_parse"]["installed_at"] = _iso(0.1)
    payload["tools"]["older"] = {
        "name": "older",
        "installed_at": _iso(9),
        "first_used_at": _iso(2),
        "first_use_observed": True,
        "runs": 1,
    }
    ledger.path.write_text(json.dumps(payload))
    restored: list[str] = []

    class Skills:
        def status(self, name: str) -> str | None:
            return "active" if restored else "archived"

        def lifecycle_metadata(self, name: str) -> dict[str, Any]:
            return {}

        async def archive(self, name: str) -> None:
            raise AssertionError("nothing should be retired")

        async def restore(self, name: str) -> None:
            restored.append(name)

    curator = LearnedToolCurator(state_dir=tmp_path, skills=Skills())
    plan = await curator.plan()
    summary = await curator.curate()

    assert plan.restore == ["k8s_quantity_parse"]
    assert summary["restored"] == ["k8s_quantity_parse"]
    assert restored == ["k8s_quantity_parse"]
    assert learned_tool_path(code_dir, "k8s_quantity_parse").is_file()
    assert LearnedToolRetirements(artifacts_dir).get("k8s_quantity_parse") is None


async def test_judged_verdicts_survive_a_pass_cut_short(tmp_path: Path) -> None:
    """A rollout part way through the first pass must not throw its judging away."""
    _install(tmp_path, "alpha_tool")
    _install(tmp_path, "beta_tool")
    _install(tmp_path, "gamma_tool")

    class RolloutError(Exception):
        pass

    class FlakyMatcher(FakeMatcher):
        async def same_capability(self, subject, candidates, *, as_revision=False):  # type: ignore[no-untyped-def]
            if self.calls == 1:
                raise RolloutError
            return await super().same_capability(subject, candidates, as_revision=as_revision)

    first = FlakyMatcher()
    with pytest.raises(RolloutError):
        await LearnedToolCurator(state_dir=tmp_path, matcher=first).plan()
    second = FakeMatcher()
    await LearnedToolCurator(state_dir=tmp_path, matcher=second).plan()

    # alpha's pairs were saved before the interruption; only the rest are judged.
    assert second.calls == 1
