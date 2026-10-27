"""Learned-tool lineage and retirement, on the existing lifecycle and archive.

The defect these cover, as measured on the Kubernetes residents: tools were
built and mostly not used, a failing tool was answered with a new variant
instead of a repair (twelve ``list_pods_in_namespace*`` on one resident), and
nothing retired variants, debug probes or never-used adoptions. The reuse,
version-chain and usage mechanisms existed; these tests pin the repaired
behaviour of those mechanisms.
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
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.adapters.tools.build_tool import BuildTool
from ravn.adapters.tools.capability_catalog import CapabilityListTool
from ravn.ports.capability_matching import CapabilityDescriptor, CapabilityVerdict
from ravn.ports.tool_build_backend import ToolBuildResult
from ravn.skills.management import SkillLifecycle, SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import (
    LearnedToolError,
    LearnedToolResolver,
    learned_tool_artifact_path,
    learned_tool_path,
    learned_tool_storage,
    superseded_artifact_path,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest
from ravn.valkyrie_evolution.tool_curation import (
    CAPABILITY_MATCHED_KEY,
    RETIRED_DEBUG_LEFTOVER,
    RETIRED_SUPERSEDED,
    RETIRED_UNRUNNABLE,
    RETIRED_UNUSED,
    LearnedToolCurator,
    LifecycleSnapshot,
    find_installed_capability,
    learned_tool_health,
    migrate_legacy_curation_state,
    restore_learned_tool,
    retire_learned_tool,
    retire_superseded_predecessor,
    tool_inventory,
)

_CODE = "def run(payload):\n    return {'ok': True}\n"
_TEST = "import _verify_tool\n\ndef test_ok():\n    assert _verify_tool.run({})['ok']\n"


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _skills(state: Path) -> SkillManagementRegistry:
    skill_dir = state / "skills"
    return SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=state / "skill_management.json",
    )


def _set(skills: SkillManagementRegistry, name: str, **values: Any) -> None:
    """Fixture shortcut: give a lifecycle record the history a test needs."""
    meta = skills._metadata[name.lower()]  # noqa: SLF001 — test fixture
    for key, value in values.items():
        setattr(meta, key, value)
    skills._save()  # noqa: SLF001 — test fixture


async def _install(
    state: Path,
    skills: SkillManagementRegistry | None,
    name: str,
    *,
    capability: str = "",
    description: str = "",
    verified: bool = True,
    created_days_ago: float = 0.0,
    supersedes: str = "",
    **lifecycle: Any,
) -> LearnedToolArtifact:
    provenance: dict[str, Any] = (
        {"verification": {"ok": True, "logs": "fixture"}} if verified else {}
    )
    artifact = LearnedToolArtifact(
        artifact_id=f"learned-tool:{name}",
        manifest=LearnedToolManifest(
            name=name,
            description=description or f"Tool {name}.",
            input_schema={"type": "object", "properties": {"namespace": {"type": "string"}}},
            required_permission="tool:run",
        ),
        tool_code=_CODE,
        test_code=_TEST,
        source_gap_id=capability,
        provenance=provenance,
        supersedes=supersedes,
        created_at=_iso(created_days_ago),
    )
    code_dir, artifacts_dir = learned_tool_storage(state)
    write_learned_tool(tools_dir=code_dir, artifact=artifact)
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=artifact)
    if skills is not None:
        await skills.create(name=name, content=f"# learned tool: {name}\n\nTool {name}.")
        _set(skills, name, created_at=_iso(created_days_ago), **lifecycle)
    return artifact


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


def _dirs(state: Path) -> tuple[Path, Path]:
    return learned_tool_storage(state)


# ---------------------------------------------------------------------------
# Lifecycle usage evidence
# ---------------------------------------------------------------------------


class TestLifecycleUsage:
    async def test_first_use_is_recorded_and_prior_history_is_bounded(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "fresh")
        await _install(
            tmp_path,
            skills,
            "veteran",
            created_days_ago=30,
            run_count=500,
            success_count=500,
            last_used_at=_iso(2),
        )

        await skills.record_usage("fresh", success=True)
        await skills.record_usage("veteran", success=True)

        fresh = skills.lifecycle_records()["fresh"]
        veteran = skills.lifecycle_records()["veteran"]
        assert fresh.first_used_at
        evidence = fresh.first_use_evidence()
        assert evidence is not None and evidence[1] == "observed"
        # Used before first_used_at existed: the bound is frozen at the next run.
        assert veteran.first_used_at == ""
        assert veteran.first_use_wait_bound_seconds == pytest.approx(28 * 86400, rel=0.01)
        await skills.record_usage("veteran", success=True)
        assert skills.lifecycle_records()["veteran"].first_use_wait_bound_seconds == pytest.approx(
            28 * 86400, rel=0.01
        )

    async def test_archive_reasons_restore_counts_and_old_files_load(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "probe")
        await skills.archive("probe", reason=RETIRED_UNUSED, detail="never run", artifact_id="a1")
        record = skills.lifecycle_records()["probe"]
        assert (record.archived_reason, record.archived_artifact_id) == (RETIRED_UNUSED, "a1")
        await skills.restore("probe")
        record = skills.lifecycle_records()["probe"]
        assert (record.status, record.archived_reason, record.restore_count) == ("active", "", 1)

        payload = json.loads((tmp_path / "skill_management.json").read_text())
        payload["probe"]["field_from_a_newer_build"] = True
        (tmp_path / "skill_management.json").write_text(json.dumps(payload))
        assert _skills(tmp_path).status("probe") == "active"


# ---------------------------------------------------------------------------
# Retire and restore through the archive
# ---------------------------------------------------------------------------


class TestRetirement:
    async def test_retiring_archives_and_uninstalls_and_restoring_reinstalls(
        self, tmp_path: Path
    ) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "old_probe")
        code_dir, artifacts_dir = _dirs(tmp_path)

        await retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            name="old_probe",
            reason=RETIRED_UNUSED,
            detail="never run",
        )

        assert not learned_tool_path(code_dir, "old_probe").exists()
        assert not learned_tool_artifact_path(artifacts_dir, "old_probe").exists()
        archived = superseded_artifact_path(artifacts_dir, "old_probe", "learned-tool:old_probe")
        assert archived.is_file()
        assert skills.status("old_probe") == "archived"
        assert LearnedToolResolver(state_dir=tmp_path).list_artifacts() == []
        with pytest.raises(LearnedToolError, match="only archived versions"):
            LearnedToolResolver(state_dir=tmp_path).load("old_probe")
        retired = tool_inventory(
            tools_dir=code_dir, artifacts_dir=artifacts_dir, skills=skills, include_retired=True
        )
        assert [(entry.name, entry.retired, entry.retired_reason) for entry in retired] == [
            ("old_probe", True, RETIRED_UNUSED)
        ]

        record = await restore_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            name="old_probe",
            why="x",
        )

        assert record.archived_reason == RETIRED_UNUSED
        assert learned_tool_path(code_dir, "old_probe").read_text() == _CODE
        assert skills.status("old_probe") == "active"
        assert learned_tool_health(tmp_path, skills)["learned_tool_restores_total"] == 1

    async def test_retiring_and_restoring_refuse_what_they_cannot_record(
        self, tmp_path: Path
    ) -> None:
        skills = _skills(tmp_path)
        code_dir, artifacts_dir = _dirs(tmp_path)
        await _install(tmp_path, None, "unmanaged_tool")
        await _install(tmp_path, skills, "managed_tool")
        args = {"tools_dir": code_dir, "artifacts_dir": artifacts_dir, "skills": skills}

        with pytest.raises(LearnedToolError, match="unknown retirement reason"):
            await retire_learned_tool(**args, name="managed_tool", reason="bored", detail="")
        with pytest.raises(LearnedToolError, match="no lifecycle record"):
            await retire_learned_tool(
                **args, name="unmanaged_tool", reason=RETIRED_UNUSED, detail=""
            )
        with pytest.raises(LearnedToolError, match="not retired"):
            await restore_learned_tool(**args, name="managed_tool", why="")
        await retire_learned_tool(**args, name="managed_tool", reason=RETIRED_UNUSED, detail="")
        await _install(tmp_path, None, "managed_tool")
        with pytest.raises(LearnedToolError, match="already exists"):
            await restore_learned_tool(**args, name="managed_tool", why="")

    async def test_a_new_version_under_a_new_name_retires_its_predecessor(
        self, tmp_path: Path
    ) -> None:
        skills = _skills(tmp_path)
        old = await _install(tmp_path, skills, "list_k8s_pods", capability="k8s.pods.list")
        new = await _install(
            tmp_path,
            skills,
            "list_k8s_pod_names",
            capability="k8s.pods.list",
            supersedes=old.artifact_id,
        )
        code_dir, artifacts_dir = _dirs(tmp_path)
        args = {"tools_dir": code_dir, "artifacts_dir": artifacts_dir, "skills": skills}

        retired = await retire_superseded_predecessor(**args, artifact=new)

        assert retired == "list_k8s_pods"
        record = skills.lifecycle_records()["list_k8s_pods"]
        assert (record.archived_reason, record.superseded_by) == (
            RETIRED_SUPERSEDED,
            "list_k8s_pod_names",
        )
        assert await retire_superseded_predecessor(**args, artifact=new) == ""


# ---------------------------------------------------------------------------
# Lineage before commissioning
# ---------------------------------------------------------------------------


class TestLineage:
    async def test_a_shared_capability_resolves_to_the_tool_that_runs(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(
            tmp_path, skills, "k8s_pod_logs_since", capability="k8s.logs", verified=False
        )
        await _install(tmp_path, skills, "k8s_pod_logs_since_sa", capability="k8s.logs")
        code_dir, artifacts_dir = _dirs(tmp_path)

        found = await find_installed_capability(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            capability_id="k8s.logs",
            name="k8s_pod_logs_v2",
        )

        assert found is not None
        assert (found.entry.name, found.matched_by) == ("k8s_pod_logs_since_sa", "capability_id")

    async def test_spelling_meaning_and_superseded_successors(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "inspect_kubernetes_node_disk_pressure")
        await _install(tmp_path, skills, "list_pods_in_namespace_sa")
        await _install(tmp_path, skills, "list_pods_in_namespace_fixed")
        code_dir, artifacts_dir = _dirs(tmp_path)
        await retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            name="list_pods_in_namespace_fixed",
            reason=RETIRED_SUPERSEDED,
            detail="",
            superseded_by="list_pods_in_namespace_sa",
        )
        args = {"tools_dir": code_dir, "artifacts_dir": artifacts_dir, "skills": skills}

        spelled = await find_installed_capability(
            **args, capability_id="", name="inspect_k8s_node_disk_pressure"
        )
        retired_name = await find_installed_capability(
            **args, capability_id="", name="list_pods_in_namespace_fixed"
        )
        matcher = FakeMatcher({"pods_lister_v3", "list_pods_in_namespace_sa"})
        meant = await find_installed_capability(
            **args, capability_id="", name="pods_lister_v3", description="pods", matcher=matcher
        )
        unrelated = await find_installed_capability(
            **args, capability_id="", name="node_memory_pressure", matcher=FakeMatcher()
        )

        assert spelled is not None and spelled.matched_by == "spelling"
        assert retired_name is not None
        assert retired_name.entry.name == "list_pods_in_namespace_sa"
        assert "superseded" in retired_name.reason
        assert meant is not None
        assert (meant.entry.name, meant.matched_by) == ("list_pods_in_namespace_sa", "semantic")
        # A build request is judged as a possible next version of a tool.
        assert matcher.revisions == [True]
        assert unrelated is None


# ---------------------------------------------------------------------------
# Curation
# ---------------------------------------------------------------------------


class TestCurator:
    async def test_backfill_unifies_capabilities_and_records_it_on_the_envelope(
        self, tmp_path: Path
    ) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "list_pods_in_namespace_working")
        await _install(
            tmp_path,
            skills,
            "k8s_pods_list_namespace",
            success_count=58,
            run_count=58,
            last_used_at=_iso(1),
        )
        await _install(tmp_path, skills, "get_namespace_events", run_count=3, success_count=3)
        matcher = FakeMatcher({"list_pods_in_namespace_working", "k8s_pods_list_namespace"})
        curator = LearnedToolCurator(state_dir=tmp_path, skills=skills, matcher=matcher)

        plan = await curator.plan()
        calls = matcher.calls
        again = await curator.plan()

        assert [(d.name, d.reason, d.superseded_by) for d in plan.retire] == [
            ("list_pods_in_namespace_working", RETIRED_SUPERSEDED, "k8s_pods_list_namespace")
        ]
        assert plan.backfilled
        _, artifacts_dir = _dirs(tmp_path)
        envelope = json.loads(
            learned_tool_artifact_path(artifacts_dir, "list_pods_in_namespace_working").read_text()
        )
        assert CAPABILITY_MATCHED_KEY in envelope["provenance"]
        assert envelope["source_gap_id"]
        # Decided once: a second pass judges nothing, and needs no cache file.
        assert again.judged == 0
        assert matcher.calls == calls
        assert set(matcher.revisions) == {False}

    async def test_a_dry_plan_writes_nothing(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "a_tool")
        _, artifacts_dir = _dirs(tmp_path)
        before = learned_tool_artifact_path(artifacts_dir, "a_tool").read_text()

        await LearnedToolCurator(
            state_dir=tmp_path, skills=skills, matcher=FakeMatcher(), persist=False
        ).plan()

        assert learned_tool_artifact_path(artifacts_dir, "a_tool").read_text() == before

    async def test_debug_unrunnable_unused_and_kept_tools(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "debug_k8s_config")
        await _install(tmp_path, skills, "patch_node_labels_via_sa", verified=False)
        await _install(tmp_path, skills, "longhorn_engine_status", created_days_ago=8)
        await _install(tmp_path, skills, "prometheus_rules_query", created_days_ago=1)
        await _install(tmp_path, skills, "debug_but_useful", run_count=4, success_count=4)
        await _install(tmp_path, skills, "rolled_back_tool", run_count=3, failure_count=3)
        await skills.archive("rolled_back_tool")
        # One tool waited two days before its first use: that is the window.
        await _install(
            tmp_path,
            skills,
            "slow_starter",
            created_days_ago=4,
            run_count=1,
            success_count=1,
            first_used_at=_iso(2),
        )

        plan = await LearnedToolCurator(
            state_dir=tmp_path, skills=skills, leftover_name_prefixes=["debug", "test"]
        ).plan()

        reasons = {decision.name: decision.reason for decision in plan.retire}
        assert reasons == {
            "debug_k8s_config": RETIRED_DEBUG_LEFTOVER,
            "patch_node_labels_via_sa": RETIRED_UNRUNNABLE,
            "rolled_back_tool": RETIRED_UNRUNNABLE,
            "longhorn_engine_status": RETIRED_UNUSED,
        }
        assert plan.window.tool == "slow_starter"
        unused = next(d for d in plan.retire if d.reason == RETIRED_UNUSED)
        assert "slow_starter" in unused.detail
        assert sorted(plan.keep) == ["debug_but_useful", "prometheus_rules_query", "slow_starter"]

    async def test_no_evidence_no_unused_and_pinned_or_unmanaged_are_kept(
        self, tmp_path: Path
    ) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "ancient_never_used", created_days_ago=60)
        await _install(tmp_path, skills, "debug_pinned", pinned=True)
        await _install(tmp_path, None, "debug_unmanaged")

        plan = await LearnedToolCurator(
            state_dir=tmp_path, skills=skills, leftover_name_prefixes=["debug"]
        ).plan()

        assert plan.retire == []
        assert plan.window.seconds is None
        assert plan.unmanaged == ["debug_unmanaged"]

    async def test_explicit_supersedes_and_spelling_group(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        first = await _install(tmp_path, skills, "list_k8s_pods")
        await _install(tmp_path, skills, "list_k8s_pod_names", supersedes=first.artifact_id)
        await _install(tmp_path, skills, "inspect_k8s_node_disk_pressure")
        await _install(tmp_path, skills, "inspect_kubernetes_node_disk_pressure")

        plan = await LearnedToolCurator(state_dir=tmp_path, skills=skills).plan()

        assert len(plan.groups) == 2
        assert {decision.reason for decision in plan.retire} == {RETIRED_SUPERSEDED}

    async def test_curate_retires_and_undoes_what_the_window_no_longer_supports(
        self, tmp_path: Path
    ) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "debug_input")
        await _install(tmp_path, skills, "k8s_quantity_parse", created_days_ago=0.1)
        await _install(
            tmp_path,
            skills,
            "older",
            created_days_ago=9,
            run_count=1,
            success_count=1,
            first_used_at=_iso(2),
        )
        code_dir, artifacts_dir = _dirs(tmp_path)
        await retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            name="k8s_quantity_parse",
            reason=RETIRED_UNUSED,
            detail="retired on a thin window",
        )

        summary = await LearnedToolCurator(
            state_dir=tmp_path, skills=skills, leftover_name_prefixes=["debug"]
        ).curate()

        assert [item["name"] for item in summary["retired"]] == ["debug_input"]
        assert summary["restored"] == ["k8s_quantity_parse"]
        assert learned_tool_path(code_dir, "k8s_quantity_parse").is_file()
        health = learned_tool_health(tmp_path, skills)
        assert health["learned_tools_retired"] == 1
        assert health["learned_tools_retired.debug_leftover"] == 1
        assert health["learned_tools_installed"] == 2


async def test_legacy_curation_state_is_folded_into_archive_and_lifecycle(tmp_path: Path) -> None:
    """dev.352–dev.366 kept retirements and verdicts in their own files."""
    skills = _skills(tmp_path)
    await _install(tmp_path, skills, "keeper", run_count=9, success_count=9, last_used_at=_iso(1))
    await _install(tmp_path, skills, "twin")
    await _install(tmp_path, skills, "old_variant")
    code_dir, artifacts_dir = _dirs(tmp_path)
    await skills.archive("old_variant")
    (artifacts_dir / "_retired").mkdir()
    (code_dir / "_retired").mkdir()
    learned_tool_artifact_path(artifacts_dir, "old_variant").rename(
        artifacts_dir / "_retired" / "old_variant.json"
    )
    learned_tool_path(code_dir, "old_variant").rename(code_dir / "_retired" / "old_variant.py")
    (artifacts_dir / "_curation").mkdir()
    (artifacts_dir / "_curation" / "retirements.json").write_text(
        json.dumps(
            {
                "retired": {
                    "old_variant": {
                        "reason": RETIRED_SUPERSEDED,
                        "detail": "'keeper' serves the same capability",
                        "superseded_by": "keeper",
                    }
                }
            }
        )
    )
    await _install(tmp_path, skills, "triplet")
    (artifacts_dir / "_curation" / "verdicts.json").write_text(
        json.dumps(
            {
                "pairs": {
                    # Transitive: twin~keeper and triplet~twin are one capability.
                    "learned-tool:keeper|learned-tool:twin": {"same": True},
                    "learned-tool:triplet|learned-tool:twin": {"same": True},
                }
            }
        )
    )
    args = {"tools_dir": code_dir, "artifacts_dir": artifacts_dir, "skills": skills}

    moved = await migrate_legacy_curation_state(**args)

    assert moved == 1
    assert not (artifacts_dir / "_retired").exists()
    assert not (artifacts_dir / "_curation").exists()
    record = skills.lifecycle_records()["old_variant"]
    assert (record.archived_reason, record.superseded_by) == (RETIRED_SUPERSEDED, "keeper")
    capabilities = {
        name: json.loads(learned_tool_artifact_path(artifacts_dir, name).read_text())
        for name in ("keeper", "twin", "triplet")
    }
    assert len({envelope["source_gap_id"] for envelope in capabilities.values()}) == 1
    # Not marked: the dream judges them against today's library.
    assert all(CAPABILITY_MATCHED_KEY not in e["provenance"] for e in capabilities.values())
    assert await migrate_legacy_curation_state(**args) == 0
    plan = await LearnedToolCurator(state_dir=tmp_path, skills=skills).plan()
    assert sorted((d.name, d.superseded_by) for d in plan.retire) == [
        ("triplet", "keeper"),
        ("twin", "keeper"),
    ]


async def test_tools_marked_by_the_first_migration_are_judged_again(tmp_path: Path) -> None:
    """dev.369 marked migrated tools as matched, which left split families unjoined."""
    skills = _skills(tmp_path)
    await _install(
        tmp_path,
        skills,
        "list_pods_in_namespace_incluster",
        run_count=22,
        success_count=22,
        last_used_at=_iso(30),
    )
    await _install(
        tmp_path,
        skills,
        "k8s_pods_list_namespace",
        run_count=58,
        success_count=58,
        last_used_at=_iso(1),
    )
    _, artifacts_dir = _dirs(tmp_path)
    for name in ("list_pods_in_namespace_incluster", "k8s_pods_list_namespace"):
        path = learned_tool_artifact_path(artifacts_dir, name)
        envelope = json.loads(path.read_text())
        envelope["provenance"][CAPABILITY_MATCHED_KEY] = {"by": "migration"}
        path.write_text(json.dumps(envelope))
    matcher = FakeMatcher({"list_pods_in_namespace_incluster", "k8s_pods_list_namespace"})

    plan = await LearnedToolCurator(state_dir=tmp_path, skills=skills, matcher=matcher).plan()

    assert [(d.name, d.superseded_by) for d in plan.retire] == [
        ("list_pods_in_namespace_incluster", "k8s_pods_list_namespace")
    ]


async def test_a_lifecycle_snapshot_reads_without_writing(tmp_path: Path) -> None:
    skills = _skills(tmp_path)
    await _install(tmp_path, skills, "probe", pinned=True)
    snapshot = LifecycleSnapshot(tmp_path / "skill_management.json")

    assert snapshot.status("probe") == "active"
    metadata = snapshot.lifecycle_metadata("probe")
    assert metadata is not None and metadata["pinned"] is True
    assert isinstance(snapshot.lifecycle_records()["probe"], SkillLifecycle)
    assert snapshot.status("missing") is None
    assert snapshot.lifecycle_metadata("missing") is None


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
            {"pods: pods": [1.0, 0.0], "pods2: pods2": [0.9, 0.1], "events: events": [0.0, 1.0]}
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
        "answer", [{"same": 1}, {"same": ["one"]}, {"same": [4]}, {"same": [True]}]
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
            tool_code=f"# {request.name}\ndef run(payload):\n    return {{'ok': True, 'v': 2}}\n",
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
        skills = _skills(tmp_path)
        await _install(
            tmp_path,
            skills,
            "list_pods_in_namespace",
            capability="k8s.pods.list",
            consecutive_failures=1,
            run_count=1,
            failure_count=1,
        )
        backend = RecordingBackend()
        tool, registered = _build_tool(tmp_path, build_backend=backend, skill_manager=skills)

        result = await tool.execute(
            _request("list_pods_in_namespace_fixed", capability_id="k8s.pods.list")
        )

        summary = json.loads(result.content)
        assert result.is_error is False, result.content
        assert (summary["tool_name"], summary["version"]) == ("list_pods_in_namespace", 2)
        assert summary["repair_of"]["problems"] == ["its last 1 run(s) failed"]
        request = backend.requests[0]
        assert request.name == "list_pods_in_namespace"
        assert "REPAIRS the installed learned tool" in request.signal_context
        assert _CODE in request.signal_context
        _, artifacts_dir = _dirs(tmp_path)
        assert not learned_tool_artifact_path(
            artifacts_dir, "list_pods_in_namespace_fixed"
        ).exists()
        assert registered[0][1] is True

    async def test_a_semantic_match_repairs_and_a_new_capability_is_marked_matched(
        self, tmp_path: Path
    ) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "k8s_pods_list_namespace", verified=False)
        backend = RecordingBackend()
        tool, _ = _build_tool(
            tmp_path,
            build_backend=backend,
            skill_manager=skills,
            capability_matcher=FakeMatcher(
                {"list_pods_in_namespace_v2", "k8s_pods_list_namespace"}
            ),
        )

        repaired = json.loads((await tool.execute(_request("list_pods_in_namespace_v2"))).content)
        brand_new = await tool.execute(_request("count_cluster_nodes"))

        assert repaired["tool_name"] == "k8s_pods_list_namespace"
        assert repaired["repair_of"]["matched_by"] == "semantic"
        assert brand_new.is_error is False, brand_new.content
        _, artifacts_dir = _dirs(tmp_path)
        envelope = json.loads(
            learned_tool_artifact_path(artifacts_dir, "count_cluster_nodes").read_text()
        )
        assert envelope["provenance"][CAPABILITY_MATCHED_KEY]["by"] == "build_tool"

    async def test_a_retired_tool_is_restored_instead_of_rebuilt(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "longhorn_engine_status", capability="longhorn.engine")
        code_dir, artifacts_dir = _dirs(tmp_path)
        await retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            name="longhorn_engine_status",
            reason=RETIRED_UNUSED,
            detail="never run",
        )
        backend = RecordingBackend()
        tool, _ = _build_tool(tmp_path, build_backend=backend, skill_manager=skills)

        result = await tool.execute(
            _request("longhorn_engine_state", capability_id="longhorn.engine")
        )

        payload = json.loads(result.content)
        assert payload["status"] == "already_installed"
        assert payload["restored_from_retirement"]["reason"] == RETIRED_UNUSED
        assert backend.requests == []
        assert skills.status("longhorn_engine_status") == "active"
        assert learned_tool_path(code_dir, "longhorn_engine_status").is_file()

    async def test_a_restored_unrunnable_tool_is_repaired(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "inspect_persistentvolume", capability="k8s.pv")
        code_dir, artifacts_dir = _dirs(tmp_path)
        await retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            name="inspect_persistentvolume",
            reason=RETIRED_UNRUNNABLE,
            detail="rolled back",
        )
        backend = RecordingBackend()
        tool, _ = _build_tool(tmp_path, build_backend=backend, skill_manager=skills)

        result = await tool.execute(_request("inspect_pv", capability_id="k8s.pv"))

        assert json.loads(result.content)["tool_name"] == "inspect_persistentvolume"
        assert "retired as unrunnable" in backend.requests[0].signal_context

    async def test_a_judge_that_cannot_answer_builds_nothing(self, tmp_path: Path) -> None:
        skills = _skills(tmp_path)
        await _install(tmp_path, skills, "some_tool")

        class BrokenMatcher:
            async def same_capability(self, *_: Any, **__: Any) -> list[CapabilityVerdict]:
                raise LessonJudgeError("unusable judge answer")

        backend = RecordingBackend()
        tool, _ = _build_tool(
            tmp_path,
            build_backend=backend,
            skill_manager=skills,
            capability_matcher=BrokenMatcher(),
        )

        result = await tool.execute(_request("brand_new_capability"))

        assert result.is_error is True
        assert "could not decide" in result.content
        assert backend.requests == []

    async def test_a_failed_rebuild_never_replaces_the_working_envelope(
        self, tmp_path: Path
    ) -> None:
        installed = await _install(tmp_path, None, "echo_tool", capability="echo")
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
        _, artifacts_dir = _dirs(tmp_path)
        envelope = json.loads(learned_tool_artifact_path(artifacts_dir, "echo_tool").read_text())
        assert envelope["artifact_id"] == installed.artifact_id
        assert list((artifacts_dir / "_failed").glob("echo_tool__*.json"))
        assert registered == []


# ---------------------------------------------------------------------------
# Discovery, dream, health, adoption
# ---------------------------------------------------------------------------


async def test_the_catalog_shows_lifecycle_usage_and_retired_matches(tmp_path: Path) -> None:
    from ravn.cli.tool_builders import _retired_learned_tools

    skills = _skills(tmp_path)
    await _install(
        tmp_path, skills, "k8s.pods.list", run_count=34, success_count=34, last_used_at=_iso(1)
    )
    await _install(tmp_path, skills, "list_pods_in_namespace_fixed")
    await _install(tmp_path, skills, "longhorn_engine_status")
    code_dir, artifacts_dir = _dirs(tmp_path)
    args = {"tools_dir": code_dir, "artifacts_dir": artifacts_dir, "skills": skills}
    await retire_learned_tool(
        **args,
        name="list_pods_in_namespace_fixed",
        reason=RETIRED_SUPERSEDED,
        detail="",
        superseded_by="k8s.pods.list",
    )
    await retire_learned_tool(
        **args, name="longhorn_engine_status", reason=RETIRED_UNUSED, detail="never run"
    )
    catalog = CapabilityListTool(
        tools_provider=lambda: [],
        skill_manager=skills,
        learned_tools_provider=LearnedToolResolver(state_dir=tmp_path).list_artifacts,
        retired_learned_tools=_retired_learned_tools(tmp_path, skills),
    )

    pods = json.loads((await catalog.execute({"query": "pods"})).content)
    longhorn = json.loads((await catalog.execute({"query": "longhorn"})).content)
    unfiltered = json.loads((await catalog.execute({})).content)

    entry = next(item for item in pods["capabilities"] if item["name"] == "k8s.pods.list")
    assert (entry["usage"]["runs"], entry["usage"]["successes"]) == (34, 34)
    assert entry["usage"]["last_used"]
    assert pods["retired_matches"][0]["next_step"] == "Use 'k8s.pods.list' instead."
    assert longhorn["capabilities"] == []
    assert "restores this tool" in longhorn["retired_matches"][0]["next_step"]
    assert "retired_matches" not in unfiltered


async def test_the_dream_curates_tools_before_learnings(tmp_path: Path) -> None:
    """A Mímir outage in learning curation must not stop the tool library being kept."""
    from ravn.valkyrie_evolution.resident_learning import ResidentLearningIdentity
    from ravn.valkyrie_evolution.wakefulness import ResidentWakefulness
    from sleipnir.adapters.in_process import InProcessBus

    order: list[str] = []

    class ToolCurator:
        async def curate(self) -> dict[str, Any]:
            order.append("tools")
            return {"retired": [{"name": "debug_input"}]}

    class LearningCurator:
        def due(self) -> bool:
            return True

        async def curate(self, *, budget_seconds: float | None = None) -> Any:
            order.append("learnings")
            raise RuntimeError("Mímir mount failed during list_pages: 503")

    identity = ResidentLearningIdentity(
        environment_id="cluster-a", valkyrie_id="valkyrie:a", domain="k8s"
    )
    failing = ResidentWakefulness(
        identity=identity,
        skills=_skills(tmp_path),
        publisher=InProcessBus(),
        learning_curator=LearningCurator(),
        tool_curator=ToolCurator(),
    )

    with pytest.raises(RuntimeError, match="503"):
        await failing.dream()
    assert order == ["tools", "learnings"]

    healthy = ResidentWakefulness(
        identity=identity,
        skills=_skills(tmp_path),
        publisher=InProcessBus(),
        tool_curator=ToolCurator(),
    )
    summary = await healthy.dream()
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

    skills = _skills(tmp_path)
    await _install(tmp_path, skills, "k8s_pvc_status")
    runtime = ResidentRuntime(state=State())  # type: ignore[arg-type]
    runtime.bind_learned_tool_health(lambda: learned_tool_health(tmp_path, skills))

    snapshot = await runtime.refresh_health_snapshot()

    assert snapshot["learned_tools_installed"] == 1
    assert snapshot["learned_tools_never_used"] == 1
    assert snapshot["learned_tools_retired"] == 0


async def test_a_peer_copy_does_not_undo_a_retirement_here(tmp_path: Path) -> None:
    from ravn.valkyrie_evolution.resident_learning import (
        ResidentLearningArtifact,
        ResidentLearningIdentity,
        ResidentLearningRuntime,
    )
    from sleipnir.adapters.in_process import InProcessBus

    state = tmp_path / "resident"
    skills = _skills(state)
    await _install(state, skills, "list_pods_in_namespace_safe")
    await _install(state, skills, "k8s_quantity_parse")
    code_dir, artifacts_dir = _dirs(state)
    for name, reason in (
        ("list_pods_in_namespace_safe", RETIRED_SUPERSEDED),
        ("k8s_quantity_parse", RETIRED_UNUSED),
    ):
        await retire_learned_tool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            skills=skills,
            name=name,
            reason=reason,
            detail="",
        )
    bus = InProcessBus()
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="valhalla", valkyrie_id="valkyrie:valhalla", domain="k8s"
        ),
        skills=skills,
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
    semantic = Settings(reflection={"matching": {"mode": "semantic"}})

    assert _build_capability_matcher(Settings(), consolidation) is None
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


def test_the_plan_command_prints_the_plan_and_changes_nothing(tmp_path: Path) -> None:
    import asyncio
    from unittest.mock import patch

    from typer.testing import CliRunner

    from ravn.cli import commands
    from ravn.config import Settings

    skills = _skills(tmp_path)
    asyncio.run(_install(tmp_path, skills, "debug_input"))
    settings = Settings(state_dir=str(tmp_path))
    with (
        patch.object(commands, "Settings", return_value=settings),
        patch.object(commands, "_resolve_workspace", return_value=tmp_path),
        patch.object(commands, "_resident_ravn_state_dir", return_value=tmp_path),
        patch.object(commands, "_build_llm", return_value=None),
    ):
        result = CliRunner().invoke(commands.app, ["learned-tools-plan"])

    assert result.exit_code == 0, result.output
    assert [item["name"] for item in json.loads(result.output)["retire"]] == ["debug_input"]
    code_dir, _ = _dirs(tmp_path)
    assert learned_tool_path(code_dir, "debug_input").is_file()
    assert _skills(tmp_path).status("debug_input") == "active"


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


def test_versions_chain_through_the_artifact_writer(tmp_path: Path) -> None:
    artifact = LearnedToolArtifact(
        artifact_id="learned-tool:chained:v1",
        manifest=LearnedToolManifest(
            name="chained",
            description="Chained tool.",
            input_schema={"type": "object"},
            required_permission="tool:run",
        ),
        tool_code=_CODE,
    )
    _, artifacts_dir = _dirs(tmp_path)
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=artifact)
    replacement = LearnedToolArtifact.from_dict(
        {**artifact.to_dict(), "artifact_id": "learned-tool:chained:v2"}
    )
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=replacement)
    envelope = json.loads(learned_tool_artifact_path(artifacts_dir, "chained").read_text())
    assert (envelope["version"], envelope["supersedes"]) == (2, "learned-tool:chained:v1")
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=replacement)
    rewritten = json.loads(learned_tool_artifact_path(artifacts_dir, "chained").read_text())
    assert rewritten["version"] == 2
