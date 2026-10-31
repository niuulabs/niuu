"""The report a dream carries about a resident's learned tools, and its chosen headline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ravn.api.valkyrie_runtime_projection import _dream_evolution
from ravn.ports.decision import ChoiceAnswer, ChoiceQuestion, DecisionResult, NoulAnswer
from ravn.skills.management import SkillLifecycle
from ravn.valkyrie_evolution.evolution_report import EvolutionReporter
from ravn.valkyrie_evolution.tool_curation import ToolEntry

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
WEEK = 7 * 24 * 3600.0
SELF = "valkyrie-valhalla-k8s"


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _tool(
    name: str,
    *,
    created_days_ago: float = 30,
    version: int = 1,
    source: str = "",
    runs: int = 0,
    successes: int = 0,
    retired_days_ago: float | None = None,
    reason: str = "",
    replaced_by: str = "",
) -> ToolEntry:
    artifact: Any = SimpleNamespace(
        manifest=SimpleNamespace(name=name),
        created_at=_ago(created_days_ago),
        version=version,
        provenance={"source_valkyrie_id": source},
    )
    lifecycle = SkillLifecycle(
        skill_id=name,
        name=name,
        run_count=runs,
        success_count=successes,
        last_used_at=_ago(1),
        archived_at="" if retired_days_ago is None else _ago(retired_days_ago),
        archived_reason=reason,
        superseded_by=replaced_by,
    )
    return ToolEntry(artifact=artifact, lifecycle=lifecycle, retired=retired_days_ago is not None)


class _Decisions:
    def __init__(self, choice: str = "") -> None:
        self.choice = choice
        self.calls: list[tuple[Any, dict[str, ChoiceQuestion], str]] = []

    async def decide(
        self, state: Any, questions: dict[str, ChoiceQuestion], *, purpose: str
    ) -> DecisionResult:
        self.calls.append((state, questions, purpose))
        answer = ChoiceAnswer(choice=self.choice, confidence=0.9, probabilities={})
        return DecisionResult(model="jev-test", answers={"headline": answer}, input_tokens=100)


def _reporter(entries: list[ToolEntry], decisions: Any = None) -> EvolutionReporter:
    return EvolutionReporter(
        inventory=lambda: entries,
        valkyrie_id=SELF,
        window_seconds=WEEK,
        decisions=decisions,
        now=lambda: NOW,
    )


async def test_it_counts_what_the_resident_keeps() -> None:
    report = await _reporter(
        [
            _tool("logs", runs=10, successes=9),
            _tool("pods", source="valkyrie-ymir-k8s", runs=2, successes=2),
            _tool("mine", source=SELF),
            _tool("gone", runs=500, successes=500, retired_days_ago=20, reason="superseded"),
        ]
    ).report()

    assert report["tools"] == 3
    assert report["retired"] == 1
    assert report["from_peers"] == 1
    assert (report["runs"], report["successes"]) == (12, 11)


async def test_changes_are_the_recent_ones_newest_first() -> None:
    report = await _reporter(
        [
            _tool("old", created_days_ago=60),
            _tool("built", created_days_ago=1),
            _tool("fixed", created_days_ago=2, version=3, runs=5, successes=5),
            _tool("copied", created_days_ago=3, source="valkyrie-glitnir-k8s"),
            _tool(
                "dup",
                created_days_ago=60,
                retired_days_ago=0.5,
                reason="superseded",
                replaced_by="fixed",
            ),
            _tool("stale", created_days_ago=60, retired_days_ago=30, reason="unused"),
            _tool("probe", created_days_ago=60, retired_days_ago=1, reason="debug_leftover"),
        ]
    ).report()

    assert [(c["kind"], c["tool"]) for c in report["changes"]] == [
        ("retired", "dup"),
        ("built", "built"),
        ("repaired", "fixed"),
        ("picked_up", "copied"),
    ]
    assert report["changes"][0]["replaced_by"] == "fixed"
    assert report["changes"][2]["version"] == 3
    assert report["changes"][3]["peer"] == "valkyrie-glitnir-k8s"


async def test_a_tool_that_never_worked_needs_attention() -> None:
    report = await _reporter(
        [
            _tool("broken", runs=33),
            _tool("unused"),
            _tool("fine", runs=3, successes=1),
            _tool("gone", runs=9, retired_days_ago=30, reason="unused"),
        ]
    ).report()

    assert [(item["tool"], item["runs"]) for item in report["attention"]] == [("broken", 33)]
    assert report["headline"] == "broken failed all 33 of its runs and is still installed."


async def test_jev_picks_among_sentences_code_wrote() -> None:
    decisions = _Decisions("tidied")
    report = await _reporter(
        [
            _tool("built", created_days_ago=1),
            _tool("dup", retired_days_ago=1, reason="superseded"),
            _tool("dup2", retired_days_ago=1, reason="superseded"),
        ],
        decisions,
    ).report()

    assert report["headline"] == "Simpler now: 2 duplicate tools retired."
    state, questions, purpose = decisions.calls[0]
    assert purpose == "resident.evolution_headline"
    assert state["period"]["changes"] == {"built": 1, "retired": 2}
    assert dict(questions["headline"].options) == {
        "tidied": "Simpler now: 2 duplicate tools retired.",
        "grew": "Built 1 new tool.",
    }


async def test_one_true_sentence_needs_no_decision() -> None:
    decisions = _Decisions()
    report = await _reporter([_tool("built", created_days_ago=1)], decisions).report()

    assert report["headline"] == "Built 1 new tool."
    assert decisions.calls == []


async def test_without_a_decision_model_the_first_sentence_stands() -> None:
    report = await _reporter(
        [_tool("built", created_days_ago=1), _tool("copied", created_days_ago=1, source="a")]
    ).report()

    assert report["headline"] == "Picked up 1 tool from other residents."


@pytest.mark.parametrize(
    ("entries", "headline"),
    [
        ([_tool("logs", runs=2000, successes=1999)], "No changes to its tools: 1,999 of 2,000"),
        ([_tool("logs")], "No changes to its tools, and none have run yet."),
        ([_tool("fix", created_days_ago=1, version=2)], "Repaired 1 tool."),
    ],
)
async def test_a_quiet_period_still_says_something_true(
    entries: list[ToolEntry], headline: str
) -> None:
    report = await _reporter(entries).report()

    assert report["headline"].startswith(headline)


async def test_a_failed_decision_raises() -> None:
    class Down(_Decisions):
        async def decide(self, *_: Any, **__: Any) -> DecisionResult:
            raise TimeoutError("slow")

    entries = [_tool("a", created_days_ago=1), _tool("b", created_days_ago=1, version=2)]

    with pytest.raises(TimeoutError):
        await _reporter(entries, Down()).report()


async def test_a_mistyped_headline_raises() -> None:
    class Mistyped(_Decisions):
        async def decide(self, *_: Any, **__: Any) -> DecisionResult:
            return DecisionResult("jev", {"headline": NoulAnswer(0.5)}, None)

    entries = [_tool("a", created_days_ago=1), _tool("b", created_days_ago=1, version=2)]

    with pytest.raises(TypeError):
        await _reporter(entries, Mistyped()).report()


async def test_a_report_reads_on_the_console_in_its_own_terms() -> None:
    report = await _reporter(
        [
            _tool("broken", runs=33),
            _tool(
                "dup",
                retired_days_ago=1,
                reason="superseded",
                replaced_by="keeper",
                runs=4,
                successes=4,
            ),
        ]
    ).report()

    projected = _dream_evolution(report)

    assert projected is not None
    assert projected["fromPeers"] == 0
    assert projected["changes"][0]["replacedBy"] == "keeper"
    assert projected["attention"][0]["lastUsedAt"] == _ago(1)
    assert _dream_evolution({}) is None
