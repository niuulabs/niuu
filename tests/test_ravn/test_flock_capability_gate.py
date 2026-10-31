"""The ``build_tool`` gate sees what flock peers run.

Ymir built ``k8s_pod_logs_time_window`` while Valhalla had run
``k8s_pod_logs_since_sa`` 27,274 times: tool proposals were one event each, the
Ymir stream dropped them, and the inventory that carries every tool as state
never left its cluster. These tests pin the repair: the inventory crosses the
flock, peers hold what they heard, and a request nothing here serves is judged
against it through the same matcher port.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.adapters.tools.build_tool import BuildTool, _artifact_from_input
from ravn.config import DecisionCapabilityMatchingConfig, ResidentEvolutionConfig
from ravn.ports.capability_matching import (
    CapabilityDescriptor,
    CapabilityMatchError,
    CapabilityVerdict,
)
from ravn.ports.peer_tools import PeerAdoption, PeerTool
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution import resident_learning
from ravn.valkyrie_evolution.learned_tools import (
    learned_tool_artifact_path,
    learned_tool_storage,
    read_learned_tool_artifact,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import (
    LearnedToolArtifact,
    LearnedToolManifest,
    ToolReachGrant,
)
from ravn.valkyrie_evolution.peer_tools import (
    PeerToolCatalog,
    PeerToolCatalogError,
    PeerToolError,
    distinct_peer_tools,
    peer_tool_from_inventory,
)
from ravn.valkyrie_evolution.resident_learning import (
    _SUBSCRIBED_EVENT_TYPES,
    EVOLUTION_SKILL_INVENTORY_EVENT,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from ravn.valkyrie_evolution.tool_curation import find_peer_capability
from ravn.valkyrie_evolution.tool_runtime import ToolRunResult
from ravn.valkyrie_evolution.tool_verification import VerificationResult
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import FLOCK_SCOPE_KEY, SleipnirEvent, flock_scope
from sleipnir.domain.registry import ODIN_REVIEW_DECIDED

_CODE = "def run(payload):\n    return {'ok': True}\n"
_TEST = "import _verify_tool\n\n\ndef test_ok():\n    assert _verify_tool.run({})['ok']\n"
_FLOCK = "flock:k8s-valkyries"
_ME = "valkyrie-ymir-k8s"
_PEER = "valkyrie-valhalla-k8s"


def _manifest(name: str, description: str = "") -> dict[str, Any]:
    return LearnedToolManifest(
        name=name,
        description=description or f"Tool {name}.",
        input_schema={"type": "object", "properties": {"namespace": {"type": "string"}}},
        required_permission="k8s:read",
        declared_reach=[ToolReachGrant(kind="pure_compute", access="read")],
    ).to_dict()


def _peer_tool(name: str = "k8s_pod_logs_since_sa", **overrides: Any) -> PeerTool:
    values: dict[str, Any] = {
        "name": name,
        "artifact_id": f"learned-tool:{name}",
        "valkyrie_id": _PEER,
        "environment_id": "valhalla",
        "manifest": _manifest(name),
        "tool_code": _CODE,
        "test_code": _TEST,
        "capability_id": "k8s.pod_logs_since",
        "runs": 27274,
        "successes": 27267,
    }
    return PeerTool(**{**values, **overrides})


def _inventory_payload(name: str = "k8s_pod_logs_since_sa", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "environment_id": "valhalla",
        "valkyrie_id": _PEER,
        "skill_name": name,
        "learning_id": f"learned-tool:{name}",
        "learned_tool_manifest": _manifest(name),
        "tool_code": _CODE,
        "test_code": _TEST,
        "requirements": [],
        "flock_ids": [_FLOCK],
        "verified": True,
        "capability_id": "k8s.pod_logs_since",
        "canary_sample": {"namespace": "kube-system"},
        "usage": {"runs": 12, "successes": 11},
        "reported_at": "2026-10-31T00:00:00+00:00",
    }
    return {**payload, **overrides}


def _inventory_event(**overrides: Any) -> SleipnirEvent:
    from datetime import UTC, datetime

    return SleipnirEvent(
        event_type=EVOLUTION_SKILL_INVENTORY_EVENT,
        source=_PEER,
        payload=_inventory_payload(**overrides),
        summary="inventory",
        urgency=0.1,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
    )


class _RecordingBus(InProcessBus):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[SleipnirEvent] = []
        self.failing = False

    async def publish(self, event: SleipnirEvent) -> None:
        if self.failing:
            raise ConnectionError("nats is away")
        self.events.append(event)
        await super().publish(event)

    def inventory(self) -> list[SleipnirEvent]:
        return [e for e in self.events if e.event_type == EVOLUTION_SKILL_INVENTORY_EVENT]

    def crossed(self) -> list[str]:
        return [e.payload["skill_name"] for e in self.inventory() if e.payload.get(FLOCK_SCOPE_KEY)]


class _Runner:
    """A learned-tool runner whose verification and canary outcomes a test picks."""

    def __init__(
        self,
        *,
        verified: bool = True,
        canary_ok: bool = True,
        refuse_reach: str = "",
        supports_verify: bool = True,
    ) -> None:
        self._verified = verified
        self._canary_ok = canary_ok
        self._refuse_reach = refuse_reach
        self.supports_verify = supports_verify
        self.canaries: list[dict[str, Any]] = []

    def check_reach(self, declared_reach: Any) -> None:
        if self._refuse_reach:
            from ravn.valkyrie_evolution.learned_tools import LearnedToolError

            raise LearnedToolError(self._refuse_reach)

    async def run(self, tool_path: Any, payload: dict[str, Any], **kwargs: Any) -> ToolRunResult:
        self.canaries.append(payload)
        return ToolRunResult(ok=self._canary_ok, error="" if self._canary_ok else "exit 1")

    async def verify(self, **kwargs: Any) -> VerificationResult:
        return VerificationResult(ok=self._verified, logs="ran here")


def _skills(state: Path) -> SkillManagementRegistry:
    skill_dir = state / "skills"
    return SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=state / "skill_management.json",
    )


def _runtime(
    state: Path,
    *,
    runner: _Runner | None = None,
    autonomy_mode: str = "yolo",
    flock_ids: Sequence[str] = (_FLOCK,),
    interval: float = 3600.0,
) -> tuple[ResidentLearningRuntime, _RecordingBus, PeerToolCatalog]:
    bus = _RecordingBus()
    catalog = PeerToolCatalog(state / "flock_peer_tools.json")
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="ymir",
            valkyrie_id=_ME,
            domain="k8s",
            flock_ids=list(flock_ids),
            autonomy_mode=autonomy_mode,
        ),
        skills=_skills(state),
        publisher=bus,
        subscriber=bus,
        tools_dir=state / "tools",
        learned_tool_runner=runner or _Runner(),  # type: ignore[arg-type]
        peer_catalog=catalog,
        flock_inventory_interval_seconds=interval,
    )
    return runtime, bus, catalog


def _install_local(state: Path, name: str, *, verified: bool = True) -> LearnedToolArtifact:
    artifact = LearnedToolArtifact(
        artifact_id=f"learned-tool:{name}",
        manifest=LearnedToolManifest.from_dict(_manifest(name)),
        tool_code=_CODE,
        test_code=_TEST,
        source_gap_id=f"k8s.{name}",
        provenance={
            **({"verification": {"ok": True}} if verified else {}),
            "canary_input": {"namespace": "default"},
        },
    )
    code_dir, artifacts_dir = learned_tool_storage(state)
    write_learned_tool(tools_dir=code_dir, artifact=artifact)
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=artifact)
    return artifact


class TestCatalog:
    def test_nothing_heard_yet_is_an_empty_answer(self, tmp_path: Path) -> None:
        assert PeerToolCatalog(tmp_path / "absent.json").tools() == []

    def test_what_was_heard_survives_a_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "peers.json"
        PeerToolCatalog(path).record(_peer_tool(requirements=("httpx",)))

        tool = PeerToolCatalog(path).tools()[0]

        assert tool == _peer_tool(requirements=("httpx",))

    def test_a_corrupt_catalog_refuses_to_read_as_no_peers(self, tmp_path: Path) -> None:
        path = tmp_path / "peers.json"
        path.write_text("{broken", encoding="utf-8")

        with pytest.raises(PeerToolCatalogError, match="next flock sweep rebuilds it"):
            PeerToolCatalog(path)

    def test_a_peer_inventory_forgets_what_it_no_longer_reports(self, tmp_path: Path) -> None:
        catalog = PeerToolCatalog(tmp_path / "peers.json")
        catalog.record(_peer_tool("retired_there"))
        catalog.record(_peer_tool("other_peers_tool", valkyrie_id="valkyrie-noatun-k8s"))

        catalog.record(_peer_tool("still_there"), inventory=["still_there"])

        assert {tool.name for tool in catalog.tools()} == {"still_there", "other_peers_tool"}

    def test_an_event_without_an_inventory_forgets_nothing(self, tmp_path: Path) -> None:
        catalog = PeerToolCatalog(tmp_path / "peers.json")
        catalog.record(_peer_tool("kept"))

        catalog.record(_peer_tool("also_kept"))

        assert len(catalog.tools()) == 2

    def test_only_a_material_change_rewrites_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "peers.json"
        catalog = PeerToolCatalog(path)
        catalog.record(_peer_tool())
        path.unlink()

        catalog.record(_peer_tool(runs=99999, successes=99998, reported_at="later"))
        assert not path.exists()
        assert catalog.tools()[0].runs == 99999

        catalog.record(_peer_tool(artifact_id="learned-tool:v2"))
        assert path.exists()

    def test_one_copy_per_tool_the_one_that_has_run_most(self) -> None:
        busy = _peer_tool(successes=500)
        idle = _peer_tool(valkyrie_id="valkyrie-noatun-k8s", successes=3)

        assert distinct_peer_tools([idle, busy]) == [busy]
        assert distinct_peer_tools([idle, busy], exclude=[" K8S_POD_LOGS_SINCE_SA "]) == []


class TestInventoryRecord:
    def test_a_verified_tool_is_read_in_full(self) -> None:
        tool = peer_tool_from_inventory(_inventory_payload())

        assert tool is not None
        assert (tool.name, tool.valkyrie_id, tool.runs) == ("k8s_pod_logs_since_sa", _PEER, 12)
        assert tool.canary_sample == {"namespace": "kube-system"}
        assert tool.inputs == ("namespace",)

    def test_an_unverified_tool_is_not_a_capability(self) -> None:
        assert peer_tool_from_inventory(_inventory_payload(verified=False)) is None

    def test_a_managed_skill_is_not_a_tool(self) -> None:
        assert peer_tool_from_inventory(_inventory_payload(learned_tool_manifest={})) is None

    def test_a_tool_without_code_is_declined_by_name(self) -> None:
        with pytest.raises(PeerToolError, match="k8s_pod_logs_since_sa"):
            peer_tool_from_inventory(_inventory_payload(tool_code=" "))


class TestCrossing:
    async def test_the_first_snapshot_carries_every_tool_to_the_flock(self, tmp_path: Path) -> None:
        runtime, bus, _ = _runtime(tmp_path)
        _install_local(tmp_path, "k8s_pods_list")
        _install_local(tmp_path, "k8s_pod_logs_time_window")

        await runtime.publish_skill_inventory()

        assert sorted(bus.crossed()) == ["k8s_pod_logs_time_window", "k8s_pods_list"]
        payload = bus.inventory()[0].payload
        assert payload[FLOCK_SCOPE_KEY] == flock_scope("k8s", "ymir")
        assert payload["inventory"] == ["k8s_pod_logs_time_window", "k8s_pods_list"]
        assert payload["flock_ids"] == [_FLOCK]
        assert payload["verified"] is True
        assert payload["capability_id"] == "k8s.k8s_pod_logs_time_window"
        assert payload["canary_sample"] == {"namespace": "default"}
        assert payload["usage"] == {"runs": 0, "successes": 0}

    async def test_a_heartbeat_stays_on_the_cluster(self, tmp_path: Path) -> None:
        runtime, bus, _ = _runtime(tmp_path)
        _install_local(tmp_path, "k8s_pods_list")
        await runtime.publish_skill_inventory()
        bus.events.clear()

        await runtime.publish_skill_inventory()

        assert len(bus.inventory()) == 1
        assert bus.crossed() == []

    async def test_only_what_changed_crosses_between_sweeps(self, tmp_path: Path) -> None:
        runtime, bus, _ = _runtime(tmp_path)
        _install_local(tmp_path, "k8s_pods_list")
        await runtime.publish_skill_inventory()
        bus.events.clear()

        _install_local(tmp_path, "k8s_pod_logs_time_window")
        await runtime.publish_skill_inventory()

        assert bus.crossed() == ["k8s_pod_logs_time_window"]

    async def test_the_sweep_carries_everything_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, bus, _ = _runtime(tmp_path, interval=3600.0)
        _install_local(tmp_path, "k8s_pods_list")
        clock = {"now": 1000.0}
        monkeypatch.setattr(resident_learning.time, "monotonic", lambda: clock["now"])
        await runtime.publish_skill_inventory()
        bus.events.clear()

        clock["now"] += 3599.0
        await runtime.publish_skill_inventory()
        assert bus.crossed() == []

        clock["now"] += 1.0
        await runtime.publish_skill_inventory()
        assert bus.crossed() == ["k8s_pods_list"]

    async def test_a_failed_crossing_is_tried_again(self, tmp_path: Path) -> None:
        runtime, bus, _ = _runtime(tmp_path)
        _install_local(tmp_path, "k8s_pods_list")
        bus.failing = True
        await runtime.publish_skill_inventory()
        bus.failing = False

        await runtime.publish_skill_inventory()

        assert bus.crossed() == ["k8s_pods_list"]

    @pytest.mark.parametrize(
        ("interval", "flocks"),
        [(0.0, (_FLOCK,)), (3600.0, ())],
        ids=["switched-off", "no-flock"],
    )
    async def test_nothing_crosses_without_a_flock_to_reach(
        self, tmp_path: Path, interval: float, flocks: Sequence[str]
    ) -> None:
        runtime, bus, _ = _runtime(tmp_path, interval=interval, flock_ids=flocks)
        _install_local(tmp_path, "k8s_pods_list")

        await runtime.publish_skill_inventory()

        assert len(bus.inventory()) == 1
        assert bus.crossed() == []


class TestHearing:
    def test_the_runtime_listens_for_inventories(self) -> None:
        assert EVOLUTION_SKILL_INVENTORY_EVENT in _SUBSCRIBED_EVENT_TYPES

    async def test_a_peer_in_the_flock_is_heard(self, tmp_path: Path) -> None:
        runtime, _, catalog = _runtime(tmp_path)

        await runtime._handle_learning_event(_inventory_event())

        assert [tool.name for tool in runtime.peer_tools()] == ["k8s_pod_logs_since_sa"]
        assert [tool.name for tool in catalog.tools()] == ["k8s_pod_logs_since_sa"]

    async def test_a_flock_written_without_the_prefix_is_the_same_flock(
        self, tmp_path: Path
    ) -> None:
        runtime, _, _ = _runtime(tmp_path)

        await runtime._handle_learning_event(_inventory_event(flock_ids=["k8s-valkyries"]))

        assert len(runtime.peer_tools()) == 1

    @pytest.mark.parametrize(
        "overrides",
        [
            {"valkyrie_id": _ME},
            {"flock_ids": ["flock:platform-valkyries"]},
            {"flock_ids": []},
        ],
        ids=["my-own-heartbeat", "another-flock", "an-older-image"],
    )
    async def test_what_is_not_a_peer_in_this_flock_is_dropped(
        self, tmp_path: Path, overrides: dict[str, Any]
    ) -> None:
        runtime, _, _ = _runtime(tmp_path)

        await runtime._handle_learning_event(_inventory_event(**overrides))

        assert runtime.peer_tools() == []

    async def test_a_tool_the_peer_no_longer_verifies_is_forgotten(self, tmp_path: Path) -> None:
        runtime, _, _ = _runtime(tmp_path)
        await runtime._handle_learning_event(_inventory_event())

        await runtime._handle_learning_event(_inventory_event(verified=False))

        assert runtime.peer_tools() == []

    async def test_a_malformed_record_is_declined_not_raised(self, tmp_path: Path) -> None:
        runtime, _, _ = _runtime(tmp_path)

        await runtime._handle_learning_event(_inventory_event(tool_code=""))

        assert runtime.peer_tools() == []

    async def test_without_a_catalog_there_is_nothing_to_hold(self, tmp_path: Path) -> None:
        bus = InProcessBus()
        runtime = ResidentLearningRuntime(
            identity=ResidentLearningIdentity(environment_id="ymir", valkyrie_id=_ME),
            skills=_skills(tmp_path),
            publisher=bus,
            subscriber=bus,
        )

        await runtime._handle_learning_event(_inventory_event())

        assert runtime.peer_tools() == []

    async def test_review_decisions_still_reach_the_review_path(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        runtime, _, _ = _runtime(tmp_path)
        called: list[SleipnirEvent] = []

        async def _decided(event: SleipnirEvent) -> None:
            called.append(event)

        runtime._handle_review_decision = _decided  # type: ignore[method-assign]
        event = SleipnirEvent(
            event_type=ODIN_REVIEW_DECIDED,
            source="odin",
            payload={},
            summary="decided",
            urgency=0.1,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
        )

        await runtime._handle_learning_event(event)

        assert called == [event]


class TestAdoption:
    async def test_a_peer_tool_installs_after_verifying_here(self, tmp_path: Path) -> None:
        runner = _Runner()
        runtime, _, _ = _runtime(tmp_path, runner=runner)
        tool = _peer_tool(canary_sample={"namespace": "kube-system"})

        adoption = await runtime.adopt_peer_tool(tool)

        assert adoption == PeerAdoption("adopted", adoption.detail, tool.name)
        assert runner.canaries == [{"namespace": "kube-system"}]
        _, artifacts_dir = learned_tool_storage(tmp_path)
        installed = read_learned_tool_artifact(learned_tool_artifact_path(artifacts_dir, tool.name))
        assert installed.provenance["verification"]["ok"] is True
        assert installed.provenance["builder_evidence"]["reported_by"] == _PEER

    async def test_a_tool_without_tests_is_refused_and_nothing_is_recorded(
        self, tmp_path: Path
    ) -> None:
        runtime, bus, _ = _runtime(tmp_path)

        adoption = await runtime.adopt_peer_tool(_peer_tool(test_code=""))

        assert adoption.outcome == "refused"
        assert adoption.attempted is False
        assert "no test_code" in adoption.detail
        assert bus.events == []
        assert runtime.decisions() == []

    async def test_reach_this_backend_cannot_enforce_is_refused(self, tmp_path: Path) -> None:
        runtime, _, _ = _runtime(tmp_path, runner=_Runner(refuse_reach="no egress for host x"))

        adoption = await runtime.adopt_peer_tool(_peer_tool())

        assert adoption.outcome == "refused"
        assert "no egress for host x" in adoption.detail

    async def test_a_backend_that_cannot_verify_refuses_rather_than_stops_the_build(
        self, tmp_path: Path
    ) -> None:
        runtime, _, _ = _runtime(tmp_path, runner=_Runner(supports_verify=False))

        adoption = await runtime.adopt_peer_tool(_peer_tool())

        assert adoption.outcome == "refused"
        assert "cannot verify" in adoption.detail

    async def test_failing_re_verification_here_is_a_refusal(self, tmp_path: Path) -> None:
        runtime, _, _ = _runtime(tmp_path, runner=_Runner(verified=False))

        adoption = await runtime.adopt_peer_tool(_peer_tool())

        assert adoption.outcome == "refused"
        assert adoption.attempted is True
        assert "re-verification failed" in adoption.detail

    async def test_a_failing_canary_here_is_a_refusal(self, tmp_path: Path) -> None:
        runtime, _, _ = _runtime(tmp_path, runner=_Runner(canary_ok=False))

        adoption = await runtime.adopt_peer_tool(_peer_tool())

        assert adoption.outcome == "refused"
        assert "Canary execution failed" in adoption.detail

    async def test_a_tool_that_needs_review_is_held(self, tmp_path: Path) -> None:
        runtime, _, _ = _runtime(tmp_path, autonomy_mode="guarded")

        adoption = await runtime.adopt_peer_tool(_peer_tool())

        assert adoption.outcome == "held"

    async def test_a_version_declined_here_is_not_tried_again(self, tmp_path: Path) -> None:
        from ravn.adapters.reflection.flock_learning import FlockLearningStore

        bus = _RecordingBus()
        runtime = ResidentLearningRuntime(
            identity=ResidentLearningIdentity(
                environment_id="ymir",
                valkyrie_id=_ME,
                domain="k8s",
                flock_ids=[_FLOCK],
                autonomy_mode="yolo",
            ),
            skills=_skills(tmp_path),
            publisher=bus,
            subscriber=bus,
            tools_dir=tmp_path / "tools",
            learned_tool_runner=_Runner(verified=False),  # type: ignore[arg-type]
            learning_store=FlockLearningStore(tmp_path / "flock_learning.json"),
        )
        tool = _peer_tool()
        assert (await runtime.adopt_peer_tool(tool)).outcome == "refused"

        second = await runtime.adopt_peer_tool(tool)

        assert second.outcome == "refused"
        assert "already declined" in second.detail


class _Matcher:
    """Answers with fixed probabilities, the way the decision matcher does."""

    def __init__(self, answers: dict[str, float], *, bar: float = 0.6) -> None:
        self._answers = answers
        self._bar = bar
        self.asked: list[list[str]] = []
        self.revisions: list[bool] = []

    async def same_capability(
        self,
        subject: CapabilityDescriptor,
        candidates: Sequence[CapabilityDescriptor],
        *,
        as_revision: bool = False,
    ) -> list[CapabilityVerdict]:
        self.asked.append([candidate.name for candidate in candidates])
        self.revisions.append(as_revision)
        return [
            CapabilityVerdict(
                candidate_key=candidate.key,
                same=self._answers[candidate.name] >= self._bar,
                similarity=0.9,
                reason=f"P={self._answers[candidate.name]}",
                probability=self._answers[candidate.name],
            )
            for candidate in candidates
            if candidate.name in self._answers
        ]


def _storage(state: Path, skills: SkillManagementRegistry | None = None) -> dict[str, Any]:
    code_dir, artifacts_dir = learned_tool_storage(state)
    return {"tools_dir": code_dir, "artifacts_dir": artifacts_dir, "skills": skills}


class TestPeerJudgment:
    async def test_exact_answers_need_no_judge(self, tmp_path: Path) -> None:
        matcher = _Matcher({})
        tools = [_peer_tool("k8s_pods.list", capability_id="k8s.pods")]

        by_id = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=tools,
            capability_id="k8s.pods",
            name="other",
            matcher=matcher,
        )
        by_name = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=tools,
            capability_id="",
            name="k8s_pods.list",
            matcher=matcher,
        )
        by_spelling = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=tools,
            capability_id="",
            name="k8s-pods-list",
            matcher=matcher,
        )

        assert by_id.same[0].matched_by == "capability_id"
        assert by_name.same[0].matched_by == "name"
        assert by_spelling.same[0].matched_by == "spelling"
        assert matcher.asked == []

    async def test_the_same_question_is_asked_of_peer_tools(self, tmp_path: Path) -> None:
        matcher = _Matcher({"k8s_pod_logs_since_sa": 0.72, "k8s_pod_logs_previous": 0.45})

        found = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=[
                _peer_tool(),
                _peer_tool("k8s_pod_logs_previous"),
                _peer_tool("longhorn_engine_status"),
            ],
            capability_id="",
            name="k8s_pod_logs_time_window",
            description="logs inside a window",
            inputs=("namespace",),
            matcher=matcher,
            near_probability=0.4,
        )

        assert [match.tool.name for match in found.same] == ["k8s_pod_logs_since_sa"]
        assert [match.tool.name for match in found.near] == ["k8s_pod_logs_previous"]
        assert found.same[0].probability == 0.72
        assert matcher.revisions == [True]

    async def test_the_strongest_answer_comes_first_and_runs_break_ties(
        self, tmp_path: Path
    ) -> None:
        matcher = _Matcher({"a_tool": 0.7, "b_tool": 0.9, "c_tool": 0.7})

        found = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=[
                _peer_tool("a_tool", successes=1),
                _peer_tool("b_tool", successes=1),
                _peer_tool("c_tool", successes=100),
            ],
            capability_id="",
            name="request",
            matcher=matcher,
        )

        assert [match.tool.name for match in found.same] == ["b_tool", "c_tool", "a_tool"]

    async def test_a_tool_held_here_is_not_judged_again(self, tmp_path: Path) -> None:
        _install_local(tmp_path, "k8s_pods_list")
        matcher = _Matcher({"k8s_pods_list": 0.9, "k8s_pod_logs_previous": 0.1})

        await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=[_peer_tool("k8s_pods_list"), _peer_tool("k8s_pod_logs_previous")],
            capability_id="",
            name="request",
            matcher=matcher,
        )

        assert matcher.asked == [["k8s_pod_logs_previous"]]

    async def test_nothing_is_near_below_the_bar(self, tmp_path: Path) -> None:
        found = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=[_peer_tool()],
            capability_id="",
            name="request",
            matcher=_Matcher({"k8s_pod_logs_since_sa": 0.39}),
            near_probability=0.4,
        )

        assert not found

    async def test_a_judge_with_no_probabilities_has_nothing_near(self, tmp_path: Path) -> None:
        class _Yes:
            async def same_capability(self, subject, candidates, *, as_revision=False):
                return [
                    CapabilityVerdict(candidate_key=c.key, same=False, similarity=0.9, reason="")
                    for c in candidates
                ]

        found = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=[_peer_tool()],
            capability_id="",
            name="request",
            matcher=_Yes(),
            near_probability=0.4,
        )

        assert not found

    async def test_without_a_judge_only_exact_answers_count(self, tmp_path: Path) -> None:
        found = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=[_peer_tool()],
            capability_id="",
            name="something_else",
        )

        assert not found

    async def test_no_peers_asks_nobody(self, tmp_path: Path) -> None:
        matcher = _Matcher({})

        found = await find_peer_capability(
            **_storage(tmp_path),
            peer_tools=[],
            capability_id="",
            name="request",
            matcher=matcher,
        )

        assert not found
        assert matcher.asked == []


class _Peers:
    def __init__(self, tools: Sequence[PeerTool], adoption: PeerAdoption | Exception) -> None:
        self._tools = list(tools)
        self._adoption = adoption
        self.adopted: list[PeerTool] = []

    def peer_tools(self) -> list[PeerTool]:
        return self._tools

    async def adopt_peer_tool(self, tool: PeerTool) -> PeerAdoption:
        self.adopted.append(tool)
        if isinstance(self._adoption, Exception):
            raise self._adoption
        return self._adoption


class _Backend:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    @property
    def name(self) -> str:
        return "recording"

    async def build(self, request: Any) -> Any:
        from ravn.ports.tool_build_backend import ToolBuildResult

        self.requests.append(request)
        return ToolBuildResult(
            manifest={
                "name": request.name,
                "description": request.description,
                "input_schema": {"type": "object"},
                "required_permission": "tool:run",
            },
            tool_code=_CODE,
            test_code=_TEST,
            requirements=[],
            provenance={},
        )


def _gate(state: Path, peers: _Peers, matcher: Any, **kwargs: Any) -> tuple[BuildTool, _Backend]:
    code_dir, artifacts_dir = learned_tool_storage(state)
    backend = _Backend()
    tool = BuildTool(
        tools_dir=code_dir,
        artifacts_dir=artifacts_dir,
        register_tool=lambda tool, replace=False: None,
        autonomy_mode="autonomous",
        build_backend=backend,
        capability_matcher=matcher,
        peer_capability_matcher=matcher,
        peer_tools=peers,
        peer_prior_art_probability=0.4,
        **kwargs,
    )
    return tool, backend


def _request(**extra: Any) -> dict[str, Any]:
    return {
        "manifest": {
            "name": "k8s_pod_logs_time_window",
            "description": "pod logs in a window",
            "input_schema": {"type": "object"},
            "required_permission": "tool:run",
        },
        "build_request": "logs between two timestamps",
        **extra,
    }


def _installed(state: Path, name: str) -> LearnedToolArtifact:
    _, artifacts_dir = learned_tool_storage(state)
    return read_learned_tool_artifact(learned_tool_artifact_path(artifacts_dir, name))


class TestGate:
    async def test_an_adopted_peer_tool_means_nothing_is_built(self, tmp_path: Path) -> None:
        peer = _peer_tool()
        peers = _Peers([peer], PeerAdoption("adopted", "ok", peer.name))
        tool, backend = _gate(tmp_path, peers, _Matcher({peer.name: 0.8}))

        result = await tool.execute(_request())

        summary = json.loads(result.content)
        assert result.is_error is False
        assert summary["status"] == "adopted_from_peer"
        assert summary["tool_name"] == peer.name
        assert summary["adopted_from"] == _PEER
        assert summary["requested_name"] == "k8s_pod_logs_time_window"
        assert summary["peer_usage"]["successes"] == 27267
        assert backend.requests == []

    async def test_a_held_adoption_means_nothing_is_built(self, tmp_path: Path) -> None:
        peer = _peer_tool()
        peers = _Peers([peer], PeerAdoption("held", "waits on an operator"))
        tool, backend = _gate(tmp_path, peers, _Matcher({peer.name: 0.8}))

        result = await tool.execute(_request())

        summary = json.loads(result.content)
        assert summary["status"] == "held_for_review"
        assert "waits on an operator" in summary["detail"]
        assert backend.requests == []

    async def test_a_refused_adoption_seeds_the_build(self, tmp_path: Path) -> None:
        peer = _peer_tool()
        peers = _Peers([peer], PeerAdoption("refused", "it has no test_code"))
        tool, backend = _gate(tmp_path, peers, _Matcher({peer.name: 0.8}))

        result = await tool.execute(_request())

        assert result.is_error is False, result.content
        request = backend.requests[0]
        assert request.name == "k8s_pod_logs_time_window"
        assert "PRIOR ART" in request.signal_context
        assert "it has no test_code" in request.signal_context
        assert _CODE in request.signal_context
        assert _TEST in request.signal_context
        artifact = _installed(tmp_path, "k8s_pod_logs_time_window")
        assert artifact.source_gap_id == "k8s.pod_logs_since"
        linked = artifact.provenance["peer_capability"]
        assert (linked["tool"], linked["same"]) == (peer.name, True)
        assert linked["adoption_refused"] == {peer.name: "it has no test_code"}

    async def test_a_near_match_never_stops_a_build(self, tmp_path: Path) -> None:
        peer = _peer_tool()
        peers = _Peers([peer], AssertionError("nothing to adopt"))
        tool, backend = _gate(tmp_path, peers, _Matcher({peer.name: 0.46}))

        result = await tool.execute(_request())

        assert result.is_error is False, result.content
        assert peers.adopted == []
        assert "is close to this request" in backend.requests[0].signal_context
        artifact = _installed(tmp_path, "k8s_pod_logs_time_window")
        assert artifact.source_gap_id != "k8s.pod_logs_since"
        assert artifact.provenance["peer_capability"]["same"] is False
        assert artifact.provenance["peer_capability"]["probability"] == 0.46

    async def test_one_real_attempt_decides_and_the_rest_are_named(self, tmp_path: Path) -> None:
        best = _peer_tool("best_tool")
        other = _peer_tool("second_tool")
        close = _peer_tool("close_tool")
        peers = _Peers([other, best, close], PeerAdoption("refused", "failed here"))
        tool, backend = _gate(
            tmp_path,
            peers,
            _Matcher({"best_tool": 0.9, "second_tool": 0.7, "close_tool": 0.5}),
        )

        await tool.execute(_request())

        assert [adopted.name for adopted in peers.adopted] == ["best_tool"]
        context = backend.requests[0].signal_context
        assert "'best_tool'" in context
        assert "- second_tool" in context
        assert "- close_tool" in context

    async def test_a_tool_refused_on_sight_does_not_cost_the_next_one_its_turn(
        self, tmp_path: Path
    ) -> None:
        """Live on Glitnir's real build: the top match had no tests, the next at 0.81 did."""
        untested = _peer_tool("list_pods_in_namespace_sa", test_code="")
        tested = _peer_tool("k8s.pods.list")

        class _Pipeline(_Peers):
            async def adopt_peer_tool(self, tool: PeerTool) -> PeerAdoption:
                self.adopted.append(tool)
                if tool is untested:
                    return PeerAdoption("refused", "no test_code", attempted=False)
                return PeerAdoption("adopted", "ok", tool.name)

        peers = _Pipeline([untested, tested], AssertionError("unused"))
        tool, backend = _gate(
            tmp_path, peers, _Matcher({"list_pods_in_namespace_sa": 0.85, "k8s.pods.list": 0.81})
        )

        result = await tool.execute(_request())

        assert json.loads(result.content)["tool_name"] == "k8s.pods.list"
        assert backend.requests == []

    async def test_when_none_can_be_tried_each_refusal_reaches_the_builder(
        self, tmp_path: Path
    ) -> None:
        first = _peer_tool("first_tool", test_code="")
        second = _peer_tool("second_tool", test_code="")

        class _NoTests(_Peers):
            async def adopt_peer_tool(self, tool: PeerTool) -> PeerAdoption:
                self.adopted.append(tool)
                return PeerAdoption("refused", f"{tool.name} has no tests", attempted=False)

        peers = _NoTests([first, second], AssertionError("unused"))
        tool, backend = _gate(tmp_path, peers, _Matcher({"first_tool": 0.9, "second_tool": 0.7}))

        await tool.execute(_request())

        assert [adopted.name for adopted in peers.adopted] == ["first_tool", "second_tool"]
        context = backend.requests[0].signal_context
        assert "could not be installed here: first_tool has no tests" in context
        assert "Not installed here: second_tool has no tests" in context

    async def test_the_brief_joins_what_the_resident_said(self, tmp_path: Path) -> None:
        peer = _peer_tool()
        tool, backend = _gate(
            tmp_path, _Peers([peer], PeerAdoption("refused", "no")), _Matcher({peer.name: 0.8})
        )

        await tool.execute(_request(signal_context="the operator wants this"))

        context = backend.requests[0].signal_context
        assert context.startswith("the operator wants this\n\nPRIOR ART")

    async def test_a_peer_without_tests_is_shown_without_them(self, tmp_path: Path) -> None:
        peer = _peer_tool(test_code="")
        tool, backend = _gate(
            tmp_path, _Peers([peer], PeerAdoption("refused", "no")), _Matcher({peer.name: 0.8})
        )

        await tool.execute(_request())

        assert "Its tests" not in backend.requests[0].signal_context

    async def test_a_tool_written_by_hand_is_linked_with_no_brief(self, tmp_path: Path) -> None:
        peer = _peer_tool()
        tool, backend = _gate(
            tmp_path, _Peers([peer], PeerAdoption("refused", "no")), _Matcher({peer.name: 0.8})
        )

        result = await tool.execute(_request(tool_code=_CODE, test_code=_TEST))

        assert result.is_error is False, result.content
        assert backend.requests == []
        artifact = _installed(tmp_path, "k8s_pod_logs_time_window")
        assert artifact.provenance["peer_capability"]["tool"] == peer.name

    async def test_a_tool_here_wins_and_peers_are_not_asked(self, tmp_path: Path) -> None:
        _install_local(tmp_path, "k8s_pod_logs_time_window")
        peers = _Peers([_peer_tool()], AssertionError("peers must not be asked"))
        matcher = _Matcher({})
        tool, backend = _gate(tmp_path, peers, matcher)

        result = await tool.execute(_request())

        assert json.loads(result.content)["status"] == "already_installed"
        assert backend.requests == []
        assert matcher.asked == []

    async def test_a_judge_that_cannot_answer_about_peers_stops_the_build(
        self, tmp_path: Path
    ) -> None:
        class _Down:
            async def same_capability(self, subject, candidates, *, as_revision=False):
                raise CapabilityMatchError("jev is down")

        tool, backend = _gate(
            tmp_path, _Peers([_peer_tool()], PeerAdoption("adopted", "ok")), _Down()
        )

        result = await tool.execute(_request())

        assert result.is_error is True
        assert "nothing was built" in result.content
        assert backend.requests == []

    async def test_an_adoption_that_cannot_decide_stops_the_build(self, tmp_path: Path) -> None:
        from ravn.valkyrie_evolution.learned_tools import LearnedToolError

        peer = _peer_tool()
        peers = _Peers([peer], LearnedToolError("the job could not start"))
        tool, backend = _gate(tmp_path, peers, _Matcher({peer.name: 0.8}))

        result = await tool.execute(_request())

        assert result.is_error is True
        assert "the job could not start" in result.content
        assert backend.requests == []

    async def test_peers_are_judged_by_their_own_matcher(self, tmp_path: Path) -> None:
        peer = _peer_tool()
        _install_local(tmp_path, "k8s_pods_list")
        local = _Matcher({"k8s_pods_list": 0.1})
        peer_matcher = _Matcher({peer.name: 0.8})
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        tool = BuildTool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            register_tool=lambda tool, replace=False: None,
            autonomy_mode="autonomous",
            build_backend=_Backend(),
            capability_matcher=local,
            peer_capability_matcher=peer_matcher,
            peer_tools=_Peers([peer], PeerAdoption("adopted", "ok", peer.name)),
            peer_prior_art_probability=0.4,
        )

        result = await tool.execute(_request())

        assert json.loads(result.content)["status"] == "adopted_from_peer"
        assert local.asked == [["k8s_pods_list"]]
        assert peer_matcher.asked == [[peer.name]]

    async def test_no_peer_match_builds_as_before(self, tmp_path: Path) -> None:
        peers = _Peers([_peer_tool()], AssertionError("nothing to adopt"))
        tool, backend = _gate(tmp_path, peers, _Matcher({"k8s_pod_logs_since_sa": 0.1}))

        await tool.execute(_request())

        assert "PRIOR ART" not in backend.requests[0].signal_context
        artifact = _installed(tmp_path, "k8s_pod_logs_time_window")
        assert "peer_capability" not in artifact.provenance
        assert artifact.provenance["capability_matched"]["by"] == "build_tool"

    async def test_a_resident_with_no_peers_port_builds_as_before(self, tmp_path: Path) -> None:
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        backend = _Backend()
        tool = BuildTool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            register_tool=lambda tool, replace=False: None,
            autonomy_mode="autonomous",
            build_backend=backend,
        )

        result = await tool.execute(_request())

        assert result.is_error is False, result.content
        assert len(backend.requests) == 1

    async def test_the_real_runtime_serves_the_gate(self, tmp_path: Path) -> None:
        runtime, _, catalog = _runtime(tmp_path)
        peer = _peer_tool()
        catalog.record(peer)
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        backend = _Backend()
        tool = BuildTool(
            tools_dir=code_dir,
            artifacts_dir=artifacts_dir,
            register_tool=lambda tool, replace=False: None,
            autonomy_mode="autonomous",
            build_backend=backend,
            peer_capability_matcher=_Matcher({peer.name: 0.8}),
            peer_tools=runtime,
            peer_prior_art_probability=0.4,
        )

        result = await tool.execute(_request())

        assert json.loads(result.content)["status"] == "adopted_from_peer"
        assert backend.requests == []
        assert _installed(tmp_path, peer.name).manifest.name == peer.name


def test_a_build_records_its_canary_for_peers() -> None:
    artifact = _artifact_from_input(
        {
            "manifest": _manifest("k8s_pods_list"),
            "tool_code": _CODE,
            "canary_input": {"namespace": "default"},
        }
    )

    assert artifact.provenance["canary_input"] == {"namespace": "default"}


def test_a_build_with_no_canary_records_none() -> None:
    artifact = _artifact_from_input({"manifest": _manifest("k8s_pods_list"), "tool_code": _CODE})

    assert "canary_input" not in artifact.provenance


def test_the_defaults_match_the_design() -> None:
    config = DecisionCapabilityMatchingConfig()

    assert (config.peer_min_same_probability, config.peer_prior_art_probability) == (0.6, 0.4)
    assert ResidentEvolutionConfig().flock_inventory_interval_seconds == 3600.0


def test_both_peer_bars_can_sit_together() -> None:
    config = DecisionCapabilityMatchingConfig(
        peer_min_same_probability=0.7, peer_prior_art_probability=0.7
    )

    assert config.peer_prior_art_probability == 0.7


def test_prior_art_above_adoption_refuses_to_load() -> None:
    with pytest.raises(ValidationError, match="lower it, or set the two equal"):
        DecisionCapabilityMatchingConfig(
            peer_min_same_probability=0.5, peer_prior_art_probability=0.6
        )


def _decision_settings(**matching: Any) -> Any:
    from types import SimpleNamespace

    reflection = SimpleNamespace(
        matching=SimpleNamespace(mode="semantic", similarity_threshold=0.65, judge_excerpt_chars=40)
    )
    return SimpleNamespace(
        decision=SimpleNamespace(
            capability_matching=DecisionCapabilityMatchingConfig(enabled=True, **matching)
        ),
        effective_post_session_reflection_config=lambda: reflection,
    )


def test_the_peer_gate_judges_at_its_own_configured_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from ravn.cli import runtime_builders

    monkeypatch.setattr(runtime_builders, "build_decision_port", lambda settings: object())
    settings = _decision_settings(min_same_probability=0.6, peer_min_same_probability=0.75)
    consolidation = SimpleNamespace(lessons=object())

    local = runtime_builders._build_capability_matcher(settings, consolidation)
    peer = runtime_builders._build_peer_capability_matcher(settings, consolidation)

    assert local._min_same_probability == 0.6
    assert peer._min_same_probability == 0.75
