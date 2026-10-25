"""Friction capture, the self-improvement pass, and the known-resolution guard.

The acceptance replay at the top is Regin's 2026-10-23/24 history: repeated
"cannot read the commit diff" escalations while the github.com/.diff route was
found three times in different words, plus a recurring capability gap nothing
covered. Replayed through reflection, the dream, and the resident runtime, it
must leave one promoted procedural learning, no further escalation of the diff
gap, and a tool commission (not an operator question) for the capability gap.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.reflection.friction import (
    FrictionLedger,
    FrictionRecord,
    FrictionSignal,
    _route_target,
    friction_signals,
    parse_record,
    render_record,
)
from ravn.adapters.reflection.learning_curation import LearningCurator
from ravn.adapters.reflection.lesson_judge import LessonJudge, LessonJudgeError
from ravn.adapters.reflection.lessons import LessonIndex
from ravn.adapters.reflection.post_session import (
    PostSessionReflectionService,
    _addressed_friction,
    _render_friction,
)
from ravn.adapters.reflection.self_improvement import (
    ImprovementReport,
    SelfImprovementPass,
    _numbered,
)
from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.agent import _tool_friction
from ravn.config import (
    FrictionConfig,
    LearningCurationConfig,
    PostSessionReflectionConfig,
    SelfImprovementConfig,
    Settings,
)
from ravn.domain.models import AgentTask, ToolCall, ToolResult
from ravn.domain.resident_continuation import ContinuationDecisionKind
from ravn.ports.known_resolutions import KnownResolution, KnownResolutionPort
from ravn.resident_runtime import ResidentRuntime
from sleipnir.adapters.in_process import InProcessBus
from tests.ravn.fixtures.learning_fakes import (
    ALTERNATE_ROUTES,
    DIFF_ROUTE,
    DISTINCT_HOSTS,
    PROCEDURE,
    KeywordEmbedding,
    ScriptedLLM,
    candidate_page,
    reflection,
)
from tests.test_ravn.test_resident_runtime import _result, _task

NOW = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)
CI_GAP = "per-check CI route output for github checks"


def _index() -> LessonIndex:
    return LessonIndex(KeywordEmbedding(), batch_size=64)


def _judge(llm) -> LessonJudge:
    return LessonJudge(llm, model="judge", max_tokens=256, attempts=2, excerpt_chars=400)


def _ledger(mimir, *, clock=lambda: NOW, index: LessonIndex | None = None, **config):
    return FrictionLedger(
        mimir=mimir,
        lessons=index or _index(),
        config=FrictionConfig(enabled=True, **config),
        clock=clock,
    )


def _pass(
    mimir,
    ledger,
    llm,
    *,
    dispatch=None,
    clock=lambda: NOW,
    index: LessonIndex | None = None,
    **config,
) -> SelfImprovementPass:
    return SelfImprovementPass(
        mimir=mimir,
        ledger=ledger,
        lessons=index or _index(),
        judge=_judge(llm),
        config=SelfImprovementConfig(enabled=True, **config),
        procedure_max_chars=280,
        resident_id="regin",
        dispatch=dispatch,
        clock=clock,
    )


def _ask(question: str, **extra) -> dict:
    return {
        "continuation": "ask_operator",
        "next_action_timing": "operator_input",
        "decision": "blocked",
        "rationale": "the patch could not be read",
        "question": question,
        **extra,
    }


class _Queue:
    def __init__(self, accept: bool = True) -> None:
        self.tasks: list[AgentTask] = []
        self.accept = accept

    async def __call__(self, task: AgentTask) -> bool:
        self.tasks.append(task)
        return self.accept


# ---------------------------------------------------------------------------
# Acceptance: replay Regin's 2026-10-23/24 history
# ---------------------------------------------------------------------------


async def test_replaying_regins_history_ends_the_escalations(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    index = _index()
    llm = ScriptedLLM(
        [
            reflection(
                DIFF_ROUTE,
                "api.github.com check-runs returned 403 for PR 1160.",
                addresses_friction=[1],
            ),
            reflection(
                DISTINCT_HOSTS,
                "Probed the web host: the diff came back after a 403.",
                addresses_friction=[1],
            ),
            reflection(
                ALTERNATE_ROUTES,
                "The patch route for PR 1159 answered.",
                addresses_friction=[1],
            ),
        ]
    )
    judge = _judge(llm)
    ledger = _ledger(mimir, index=index)
    config = PostSessionReflectionConfig(
        enabled=True,
        llm_alias="fast",
        matching={"mode": "semantic", "similarity_threshold": 0.5},
        friction={"enabled": True},
    )
    reflection_service = PostSessionReflectionService(
        InProcessBus(), mimir, llm, config, lessons=index, judge=judge, friction=ledger
    )
    gap = {"working_state": {"capability_gaps": [CI_GAP]}}
    sessions = [
        ("s-0856", _ask("I cannot read the commit diff for PR 1160: api.github.com 403", **gap)),
        ("s-2019", _ask("Cannot read commit diff for PR 1159, the GitHub API says 403", **gap)),
        ("s-0108", _ask("cannot read the commit diff for PR 1158 (api.github.com 403)")),
    ]
    for session_id, outcome in sessions:
        await reflection_service._process(
            {"session_id": session_id, "persona": "regin", "structured_outcome": outcome}
        )

    # The dream: curation, then self-improvement.
    curator = LearningCurator(
        mimir=mimir,
        lessons=index,
        judge=judge,
        config=LearningCurationConfig(enabled=True),
        similarity_threshold=0.5,
        evidence_duplicate_similarity=0.9,
        min_repetitions=3,
        verified_recovery_promotes=True,
        clock=lambda: NOW,
    )
    await curator.curate()
    queue = _Queue()
    report = await _pass(mimir, ledger, llm, dispatch=queue, index=index).run()

    # One promoted, procedural learning.
    learnings = await mimir.list_pages(category="learnings")
    assert len(learnings) == 1
    learning = await mimir.read_page(learnings[0].path)
    assert f"## Procedure\n{PROCEDURE}" in learning

    # The recurring gap nothing covered became a build_tool commission.
    assert [task.triggered_by for task in queue.tasks] == ["self_improvement:dream"]
    assert "build_tool" in queue.tasks[0].initiative_context
    assert CI_GAP in queue.tasks[0].initiative_context
    assert sorted(action["kind"] for action in report.actions) == [
        "commission_tool",
        "learned",
    ]
    brief = await mimir.read_page(report.audit_path)
    assert "## What I changed" in brief

    # The next escalations of both are answered, not sent to the operator.
    state = LocalResidentState(tmp_path / "state")
    runtime = ResidentRuntime(state=state, known_resolutions=ledger)
    diff = await runtime.handle_completed_turn(
        task=_task(root_correlation_id="case-pr-1170"),
        prompt="p",
        result=_result(_ask("I cannot read the commit diff for PR 1170, api.github.com 403")),
        response_text="r",
    )
    ci = await runtime.handle_completed_turn(
        task=_task(root_correlation_id="case-ci"),
        prompt="p",
        result=_result(_ask("Can you fetch the per-check CI route output from github checks?")),
        response_text="r",
    )

    assert diff.kind is ContinuationDecisionKind.SLEEP
    assert ci.kind is ContinuationDecisionKind.SLEEP
    assert await state.list_operator_needed() == []
    wakes = sorted((tmp_path / "state").rglob("*.md"))
    wake_text = "\n".join(path.read_text() for path in wakes)
    assert PROCEDURE in wake_text
    assert queue.tasks[0].task_id in wake_text


# ---------------------------------------------------------------------------
# Friction capture
# ---------------------------------------------------------------------------


def test_friction_signals_come_from_the_outcome_and_the_tool_record() -> None:
    payload = {
        "structured_outcome": {
            "continuation": "ask_operator",
            "decision": "blocked",
            "rationale": "patch unreadable",
            "question": "",
            "open_questions": ["", "Can you paste the diff?"],
            "working_state": {
                "capability_gaps": ["read CI logs", {"need": "push tags"}, {"other": 1}, ""]
            },
        },
        "friction": [
            {
                "kind": "tool_failure",
                "tool": "web_fetch",
                "input": '{"url": "https://api.github.com/repos/x/check-runs?page=2"}',
                "detail": "HTTP 403",
            },
            {"kind": "budget_exhausted", "detail": "error"},
            {"kind": "nonsense", "tool": "x"},
            "not a dict",
        ],
        "route_recoveries": [
            {
                "tool": "web_fetch",
                "failed_input": '{"url": "https://api.github.com/a"}',
                "succeeded_input": '{"url": "https://github.com/a.diff"}',
            },
            "bad",
        ],
    }

    signals = friction_signals(payload, excerpt_chars=200)

    assert [(s.kind, s.subject) for s in signals] == [
        ("escalation", "Can you paste the diff?"),
        ("blocked", "patch unreadable"),
        ("capability_gap", "read CI logs"),
        ("capability_gap", "push tags"),
        ("tool_failure", "web_fetch: api.github.com/repos/x/check-runs"),
        ("budget_exhausted", "iteration budget exhausted"),
        ("route_recovery", "web_fetch: api.github.com/a"),
    ]
    assert signals[-1].detail == "worked instead: github.com/a.diff"


def test_escalate_decision_without_a_question_uses_the_rationale() -> None:
    signals = friction_signals(
        {"structured_outcome": {"decision": "escalate", "rationale": "deploy is stuck"}},
        excerpt_chars=200,
    )
    assert signals == [FrictionSignal("escalation", "deploy is stuck", "deploy is stuck")]
    assert friction_signals({}, excerpt_chars=200) == []
    assert _route_target("not json") == "not json"
    assert _route_target('{"path": "/tmp/x"}') == '{"path": "/tmp/x"}'


async def test_the_ledger_clusters_signals_by_meaning(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir, max_occurrences_kept=2)

    await ledger.record(
        [FrictionSignal("escalation", "cannot read the github diff (403)", "a")],
        session_id="s-1",
        links={0: "learning-candidates/general/a.md"},
    )
    await ledger.record(
        [
            FrictionSignal("escalation", "the GitHub diff is unreadable, 403 again", "b"),
            FrictionSignal("timeout", "web_fetch: example.com", "timed out"),
        ],
        session_id="s-2",
    )
    touched = await ledger.record(
        [FrictionSignal("escalation", "cannot read the github diff (403)", "c")],
        session_id="s-3",
        links={0: "learning-candidates/general/a.md"},
    )

    records = {record.kind: record for record in await ledger.load()}
    escalation = records["escalation"]
    assert escalation.total == 3
    assert [o["session_id"] for o in escalation.occurrences] == ["s-2", "s-3"]
    assert escalation.linked_lessons == ["learning-candidates/general/a.md"]
    assert touched[0].path == escalation.path
    assert escalation.path.startswith("friction/escalation/")
    assert records["timeout"].total == 1
    assert await ledger.record([], session_id="s-4") == []


async def test_a_new_subject_with_a_taken_slug_gets_its_own_page(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir, similarity_threshold=1.0)

    await ledger.record([FrictionSignal("blocked", "Deploy blocked!", "")], session_id="s-1")
    await ledger.record([FrictionSignal("blocked", "deploy blocked", "")], session_id="s-2")

    paths = {record.path for record in await ledger.load()}
    assert len(paths) == 2
    assert "friction/blocked/deploy-blocked.md" in paths


def test_friction_records_round_trip_through_their_page() -> None:
    record = FrictionRecord(
        path="friction/escalation/x.md",
        kind="escalation",
        subject='say "hi"',
        total=2,
        linked_lessons=["learnings/general/x.md"],
        actions=[
            {"kind": "learned", "status": "pending", "acted_at": "t", "expected_benefit": "b"}
        ],
    )
    page = render_record(record)

    assert parse_record(record.path, page) == record
    assert "## Improvement actions" in page and "## Linked lessons" in page
    with pytest.raises(ValueError, match="no ```json"):
        parse_record("friction/x.md", "# no record")


async def test_known_resolutions_answer_once_per_case(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    [record] = await ledger.record(
        [FrictionSignal("escalation", "cannot read the github diff (403)", "")], session_id="s"
    )
    assert await ledger.resolution_for("cannot read the github diff") is None

    record.status = "addressed"
    record.actions.append({"kind": "learned", "status": "pending", "instruction": "use .diff"})
    await ledger.save(record)

    assert await ledger.resolution_for("") is None
    assert await ledger.resolution_for("helm migration") is None
    resolution = await ledger.resolution_for("the github diff will not load: 403")
    assert resolution == KnownResolution(ref=record.path, instruction="use .diff")
    assert await ledger.note_redirect(record.path, "case-1")
    assert not await ledger.note_redirect(record.path, "case-1")
    await ledger.note_escalated_anyway(record.path, "case-1", "still stuck")
    reloaded = parse_record(record.path, await mimir.read_page(record.path))
    assert reloaded.redirected_cases == ["case-1"]
    assert reloaded.escalated_anyway[0]["question"] == "still stuck"
    assert reloaded.recurrences_since("2000") == 1


def _call(call_id: str, url: str, name: str = "web_fetch") -> ToolCall:
    return ToolCall(id=call_id, name=name, input={"url": url})


def test_tool_friction_reads_retries_timeouts_and_unrecovered_failures() -> None:
    calls = [
        _call("1", "https://a"),
        _call("2", "https://a"),
        _call("3", "https://slow"),
        _call("4", "https://b"),
        _call("5", "https://c", name="http_probe"),
        _call("6", "https://d", name="http_probe"),
    ]
    results = [
        ToolResult(tool_call_id="1", content="HTTP 500", is_error=True),
        ToolResult(tool_call_id="2", content="HTTP 500", is_error=True),
        ToolResult(tool_call_id="3", content="Request timed out after 30s", is_error=True),
        ToolResult(tool_call_id="4", content="ok"),
        ToolResult(tool_call_id="5", content="status=404", is_error=True),
        ToolResult(tool_call_id="6", content="status=200"),
    ]

    signals = _tool_friction(calls, results, limit=10, excerpt_chars=80)

    # web_fetch https://a and https://slow were recovered by https://b; the
    # identical second call to https://a is still a retry.
    assert [(s["kind"], s["tool"]) for s in signals] == [("retry", "web_fetch")]
    assert _tool_friction(calls, results, limit=0, excerpt_chars=80) == []
    unrecovered = _tool_friction(calls[:3], results[:3], limit=1, excerpt_chars=80)
    assert [s["kind"] for s in unrecovered] == ["tool_failure"]
    timeout = _tool_friction(calls[2:3], results[2:3], limit=5, excerpt_chars=80)
    assert [s["kind"] for s in timeout] == ["timeout"]


async def test_session_ended_carries_friction_and_an_exhausted_budget() -> None:
    from tests.test_ravn.test_learning_consolidation import _agent

    publisher = AsyncMock()
    budget = MagicMock()
    budget.exhausted = True
    agent = _agent(publisher, max_friction_signals=2, iteration_budget=budget)
    agent._record_route_recoveries(
        [_call("1", "https://a")],
        [ToolResult(tool_call_id="1", content="HTTP 500", is_error=True)],
    )

    await agent.emit_session_ended("error")

    payload = publisher.publish.call_args[0][0].payload
    assert [item["kind"] for item in payload["friction"]] == ["tool_failure", "budget_exhausted"]


# ---------------------------------------------------------------------------
# Reflection links friction to the lesson that would prevent it
# ---------------------------------------------------------------------------


async def test_reflection_records_friction_even_without_a_learning(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM([None])
    ledger = _ledger(mimir)
    config = PostSessionReflectionConfig(
        enabled=True,
        llm_alias="fast",
        matching={"mode": "semantic"},
        friction={"enabled": True},
    )
    service = PostSessionReflectionService(
        InProcessBus(), mimir, llm, config, lessons=_index(), judge=_judge(llm), friction=ledger
    )

    await service._process({"session_id": "s-1", "structured_outcome": _ask("why 403?")})

    records = {record.kind: record for record in await ledger.load()}
    assert set(records) == {"escalation", "blocked"}
    assert records["escalation"].linked_lessons == []
    assert "[1] escalation: why 403?" in llm.prompts[0]


def test_friction_capture_without_a_ledger_is_a_startup_error() -> None:
    config = PostSessionReflectionConfig(enabled=True, friction={"enabled": True})
    with pytest.raises(ValueError, match="friction ledger"):
        PostSessionReflectionService(InProcessBus(), AsyncMock(), AsyncMock(), config)


def test_addressed_friction_accepts_only_shown_numbers() -> None:
    learning = {"addresses_friction": [1, "2", 2, True, "x", 9, 0]}
    assert _addressed_friction(learning, 3) == [0, 1]
    assert _addressed_friction({"addresses_friction": "1"}, 3) == []
    assert _render_friction([]) == "(none recorded)"
    assert _render_friction([FrictionSignal("retry", "x", "")]) == "[1] retry: x"


# ---------------------------------------------------------------------------
# The self-improvement pass
# ---------------------------------------------------------------------------


async def _seed_friction(
    mimir, ledger, *, kind="capability_gap", subject=CI_GAP, times=2, links=None
) -> FrictionRecord:
    record = None
    for n in range(times):
        [record] = await ledger.record(
            [FrictionSignal(kind, subject, "")], session_id=f"s-{n}", links=links
        )
    return record


async def _seed_candidate(mimir, path="learning-candidates/general/diff.md") -> str:
    await mimir.upsert_page(
        path, candidate_page(DIFF_ROUTE, session_id="s-0", evidence="403", date=NOW)
    )
    return path


def test_dispatched_actions_need_a_drive_loop() -> None:
    with pytest.raises(ValueError, match="drive loop"):
        _pass(AsyncMock(), AsyncMock(), ScriptedLLM())
    # Without dispatched actions, no drive loop is needed.
    _pass(AsyncMock(), AsyncMock(), ScriptedLLM(), allowed_actions=["rewrite_learning"])


async def test_nothing_recurring_means_no_planner_call_and_no_brief(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    await _seed_friction(mimir, ledger, times=1)
    llm = ScriptedLLM()

    report = await _pass(mimir, ledger, llm, dispatch=_Queue()).run()

    assert llm.judge_calls == 0
    assert report.audit_path == ""
    assert report.summary()["actions"] == []


async def test_a_platform_fault_is_filed_as_a_defect_ticket(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    await _seed_friction(mimir, ledger, kind="tool_failure", subject="web_fetch: mimir/api")
    plan = {
        "actions": [
            {
                "friction": "F1",
                "action": "file_defect",
                "title": "Mimir API returns 500 on page reads",
                "body": "Seen twice; friction/tool_failure/... has the evidence.",
                "expected_benefit": "the platform team fixes reads",
                "rationale": "a platform fault I cannot work around",
            }
        ]
    }
    queue = _Queue()

    report = await _pass(mimir, ledger, ScriptedLLM(plan=plan), dispatch=queue).run()

    context = queue.tasks[0].initiative_context
    assert "tracker_issue" in context and "team NIU" in context and "platform-defect" in context
    [record] = await ledger.load()
    assert record.status == "addressed"
    assert "known platform defect" in record.actions[0]["instruction"]
    assert report.actions[0]["kind"] == "file_defect"


async def test_learning_actions_promote_rewrite_and_retire(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    candidate = await _seed_candidate(mimir)
    await _seed_friction(
        mimir, ledger, kind="escalation", subject="cannot read github diff", links={0: candidate}
    )
    plan = {
        "actions": [
            {
                "friction": "F1",
                "action": "promote_learning",
                "lesson": "L1",
                "expected_benefit": "the .diff rule is in my prompt",
                "rationale": "recurs",
            }
        ]
    }

    report = await _pass(mimir, ledger, ScriptedLLM(plan=plan), dispatch=_Queue()).run()

    [learning] = await mimir.list_pages(category="learnings")
    assert "recurring_friction:2" in await mimir.read_page(learning.path)
    assert report.actions[0]["target"] == learning.path

    # A promoted learning first answers its friction as "learned"; only when that
    # proves ineffective does the planner get to rewrite or retire it.
    async def reopened(subject: str) -> None:
        ledger_2 = _ledger(mimir, similarity_threshold=1.0)
        record = await _seed_friction(
            mimir, ledger_2, kind="blocked", subject=subject, links={0: learning.path}
        )
        record.actions.append({"kind": "learned", "status": "ineffective", "acted_at": "t"})
        await ledger_2.save(record)

    await reopened("diff route blocked")
    rewrite = {
        "actions": [
            {
                "friction": "F1",
                "action": "rewrite_learning",
                "lesson": "L1",
                "expected_benefit": "a rule I can act on",
            }
        ]
    }
    await _pass(mimir, ledger, ScriptedLLM(plan=rewrite), dispatch=_Queue()).run()
    assert f"## Procedure\n{PROCEDURE}" in await mimir.read_page(learning.path)

    await reopened("diff route still blocked after the rewrite")
    retire = {
        "actions": [
            {
                "friction": "F1",
                "action": "retire_belief",
                "lesson": "L1",
                "expected_benefit": "stop trusting a stale rule",
                "rationale": "newer evidence contradicts it",
            }
        ]
    }
    await _pass(mimir, ledger, ScriptedLLM(plan=retire), dispatch=_Queue()).run()
    retired = await mimir.read_page(learning.path)
    assert "status: superseded" in retired and "retired by self-improvement" in retired


@pytest.mark.parametrize(
    ("proposal", "reason"),
    [
        ("nonsense", "not an object"),
        ({"friction": "F9"}, "names no shown friction"),
        ({"friction": "F1", "action": "delete_everything"}, "not allowed"),
        ({"friction": "F1", "action": "commission_tool"}, "no expected benefit"),
        ({"friction": "F1", "action": "commission_tool", "expected_benefit": "b"}, "needs the"),
        ({"friction": "F1", "action": "file_defect", "expected_benefit": "b"}, "title and body"),
        ({"friction": "F1", "action": "promote_learning", "expected_benefit": "b"}, "candidate"),
        ({"friction": "F1", "action": "rewrite_learning", "expected_benefit": "b"}, "learning"),
        ({"friction": "F1", "action": "retire_belief", "expected_benefit": "b"}, "lesson"),
    ],
)
async def test_invalid_proposals_are_rejected_on_the_record(
    tmp_path: Path, proposal, reason
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    await _seed_friction(mimir, ledger)

    report = await _pass(
        mimir, ledger, ScriptedLLM(plan={"actions": [proposal]}), dispatch=_Queue()
    ).run()

    assert report.actions == []
    assert reason in report.rejected[0]["reason"]
    assert "## Proposals I did not act on" in await mimir.read_page(report.audit_path)


async def test_a_candidate_not_linked_to_the_friction_cannot_be_promoted(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    candidate = await _seed_candidate(mimir)
    await _seed_friction(mimir, ledger, links={0: candidate})
    other = FrictionRecord(path="x", kind="blocked", subject="y")
    pass_ = _pass(mimir, ledger, ScriptedLLM(), dispatch=_Queue())
    report = ImprovementReport(started_at=NOW)
    shown = [lesson for lesson in await _index().load(mimir) if lesson.path == candidate]

    problem = await pass_._act(
        {"friction": "F1", "action": "promote_learning", "lesson": "L1", "expected_benefit": "b"},
        [other],
        shown,
        report,
        now=NOW,
    )

    assert problem == "the candidate is not linked to this friction"


async def test_a_malformed_plan_is_audited_and_raised(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    await _seed_friction(mimir, ledger)

    with pytest.raises(LessonJudgeError, match="no 'actions'"):
        await _pass(mimir, ledger, ScriptedLLM(plan={"plan": []}), dispatch=_Queue()).run()

    [brief] = await mimir.list_pages(prefix="self-improvement/")
    assert "## Error" in await mimir.read_page(brief.path)


async def test_a_refused_dispatch_fails_the_pass(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    await _seed_friction(mimir, ledger)

    with pytest.raises(RuntimeError, match="refused"):
        await _pass(mimir, ledger, ScriptedLLM(), dispatch=_Queue(accept=False)).run()


async def test_an_ineffective_learning_is_demoted_and_not_retried(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    times = [NOW]
    ledger = _ledger(mimir, clock=lambda: times[0])
    learning_path = "learnings/general/diff.md"
    await mimir.upsert_page(
        learning_path,
        candidate_page(DIFF_ROUTE, session_id="s-0", evidence="403", date=NOW)
        .replace("category: learning-candidates", "category: learnings")
        .replace("status: candidate", "status: promoted"),
    )
    record = await _seed_friction(
        mimir, ledger, kind="escalation", subject="github diff 403", links={0: learning_path}
    )
    first = await _pass(
        mimir, ledger, ScriptedLLM(), dispatch=_Queue(), clock=lambda: times[0]
    ).run()
    assert [a["kind"] for a in first.actions] == ["learned"]

    times[0] = NOW + timedelta(hours=1)
    for case in ("case-1", "case-2"):
        await ledger.note_escalated_anyway(record.path, case, "still cannot read the diff")
    second = await _pass(
        mimir, ledger, ScriptedLLM(), dispatch=_Queue(), clock=lambda: times[0]
    ).run()

    assert second.evaluated[0]["verdict"] == "ineffective"
    assert "status: demoted" in await mimir.read_page(learning_path)
    [reloaded] = await ledger.load()
    assert reloaded.status == "open" and reloaded.tried() == {"learned"}
    brief = await mimir.read_page(second.audit_path)
    assert "## Did earlier improvements help?" in brief
    assert "## Recurring friction still open" in brief


async def test_an_effective_improvement_is_credited_to_its_learning(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    times = [NOW]
    ledger = _ledger(mimir, clock=lambda: times[0])
    learning_path = "learnings/general/diff.md"
    await mimir.upsert_page(
        learning_path,
        candidate_page(DIFF_ROUTE, session_id="s-0", evidence="403", date=NOW)
        .replace("category: learning-candidates", "category: learnings")
        .replace("status: candidate", "status: promoted"),
    )
    await _seed_friction(
        mimir, ledger, kind="retry", subject="web_fetch: api.github.com", links={0: learning_path}
    )
    await _pass(mimir, ledger, ScriptedLLM(), dispatch=_Queue(), clock=lambda: times[0]).run()

    times[0] = NOW + timedelta(days=4)
    report = await _pass(
        mimir, ledger, ScriptedLLM(), dispatch=_Queue(), clock=lambda: times[0]
    ).run()

    assert report.evaluated[0]["verdict"] == "effective"
    learning = await mimir.read_page(learning_path)
    assert "source: improvement_outcome" in learning and "evidence_count: 2" in learning
    [reloaded] = await ledger.load()
    assert reloaded.status == "resolved"


async def test_a_pending_action_inside_its_window_is_left_alone(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    record = await _seed_friction(mimir, ledger)
    record.actions.append(
        {
            "kind": "commission_tool",
            "status": "pending",
            "acted_at": NOW.isoformat(),
            "target": "task-1",
        }
    )
    record.status = "addressed"
    await ledger.save(record)

    report = await _pass(mimir, ledger, ScriptedLLM(), dispatch=_Queue()).run()

    assert report.evaluated == [] and report.actions == []


def test_numbered_references() -> None:
    assert _numbered("F2", "F", ["a", "b"]) == "b"
    assert _numbered("f1", "F", ["a"]) == "a"
    assert _numbered("F3", "F", ["a"]) is None
    assert _numbered(None, "L", ["a"]) is None


# ---------------------------------------------------------------------------
# The runtime guard
# ---------------------------------------------------------------------------


class _Resolutions(KnownResolutionPort):
    def __init__(self, resolution: KnownResolution | None) -> None:
        self.resolution = resolution
        self.redirected: set[str] = set()
        self.escalated: list[str] = []

    async def resolution_for(self, question: str) -> KnownResolution | None:
        return self.resolution

    async def note_redirect(self, ref: str, case_id: str) -> bool:
        if case_id in self.redirected:
            return False
        self.redirected.add(case_id)
        return True

    async def note_escalated_anyway(self, ref: str, case_id: str, question: str) -> None:
        self.escalated.append(case_id)


async def test_a_second_ask_in_the_same_case_reaches_the_operator(tmp_path: Path) -> None:
    resolutions = _Resolutions(KnownResolution(ref="friction/x.md", instruction="use .diff"))
    state = LocalResidentState(tmp_path)
    runtime = ResidentRuntime(state=state, known_resolutions=resolutions)

    first = await runtime.handle_completed_turn(
        task=_task(), prompt="p", result=_result(_ask("diff?")), response_text="r"
    )
    second = await runtime.handle_completed_turn(
        task=_task(), prompt="p", result=_result(_ask("diff?")), response_text="r"
    )

    assert first.kind is ContinuationDecisionKind.SLEEP
    assert "known resolution" in first.reason
    assert second.kind is ContinuationDecisionKind.ASK_OPERATOR
    assert resolutions.escalated == ["root-1"]


async def test_without_a_resolution_the_operator_is_asked(tmp_path: Path) -> None:
    runtime = ResidentRuntime(
        state=LocalResidentState(tmp_path), known_resolutions=_Resolutions(None)
    )
    disposition = await runtime.handle_completed_turn(
        task=_task(), prompt="p", result=_result(_ask("diff?")), response_text="r"
    )
    assert disposition.kind is ContinuationDecisionKind.ASK_OPERATOR


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


async def test_the_dream_runs_self_improvement_after_curation() -> None:
    from ravn.adapters.reflection.learning_curation import CurationReport
    from tests.test_ravn.test_learning_curation import _machine

    curator = MagicMock()
    curator.due.return_value = True
    curator.curate = AsyncMock(return_value=CurationReport(started_at=NOW))
    improvement = MagicMock()
    improvement.run = AsyncMock(return_value=ImprovementReport(started_at=NOW))
    machine = _machine(curator)
    machine._self_improvement = improvement

    summary = await machine.dream()

    assert summary["learnings"]["self_improvement"]["actions"] == []
    improvement.run.assert_awaited_once()


def _settings(**reflection) -> Settings:
    settings = Settings()
    for key, value in reflection.items():
        setattr(getattr(settings.reflection, key), "enabled", value)
    return settings


def test_friction_and_self_improvement_builders() -> None:
    from ravn.cli.runtime_builders import (
        LearningConsolidation,
        _build_friction_ledger,
        _build_learning_consolidation,
        _build_self_improvement,
    )

    consolidation = LearningConsolidation(lessons=_index(), judge=_judge(ScriptedLLM()))
    off = Settings()
    assert _build_friction_ledger(off, mimir=MagicMock(), consolidation=None) is None
    assert (
        _build_self_improvement(
            off, mimir=None, consolidation=None, ledger=None, drive_loop=None, resident_id="r"
        )
        is None
    )

    friction = _settings(friction=True)
    with pytest.raises(RuntimeError, match="embedding.enabled is false"):
        _build_learning_consolidation(friction, MagicMock())
    with pytest.raises(RuntimeError, match="no Mímir mount"):
        _build_friction_ledger(friction, mimir=None, consolidation=consolidation)
    ledger = _build_friction_ledger(friction, mimir=MagicMock(), consolidation=consolidation)
    assert isinstance(ledger, FrictionLedger)

    improving = _settings(friction=True, self_improvement=True)
    with pytest.raises(RuntimeError, match="reflection.curation"):
        _build_self_improvement(
            improving,
            mimir=MagicMock(),
            consolidation=consolidation,
            ledger=ledger,
            drive_loop=MagicMock(),
            resident_id="regin",
        )
    improving.reflection.curation.enabled = True
    built = _build_self_improvement(
        improving,
        mimir=MagicMock(),
        consolidation=consolidation,
        ledger=ledger,
        drive_loop=MagicMock(),
        resident_id="regin",
    )
    assert isinstance(built, SelfImprovementPass)
    with pytest.raises(ValueError, match="drive loop"):
        _build_self_improvement(
            improving,
            mimir=MagicMock(),
            consolidation=consolidation,
            ledger=ledger,
            drive_loop=None,
            resident_id="regin",
        )


def test_resident_runtime_wiring_passes_the_guard() -> None:
    from ravn.cli.resident_runtime_wiring import _build_resident_runtime

    settings = Settings()
    settings.reflection.self_improvement.redirect_delay_seconds = 5.0
    guard = _Resolutions(None)
    runtime = _build_resident_runtime(
        settings, state=MagicMock(), inbox=None, known_resolutions=guard
    )
    assert runtime._known_resolutions is guard
    assert runtime._known_resolution_redirect_seconds == 5.0


def test_brief_json_is_plain_data() -> None:
    report = ImprovementReport(started_at=NOW)
    report.actions.append({"kind": "learned", "ref": "friction/x.md"})
    report.evaluated.append({"ref": "friction/x.md", "verdict": "effective"})
    assert json.dumps(report.summary())


async def test_the_ledger_forgets_deleted_records(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    [record] = await ledger.record([FrictionSignal("retry", "x", "")], session_id="s")

    await mimir.delete_page(record.path)

    assert await ledger.load() == []


async def test_one_friction_gets_one_action_per_pass_and_tried_ones_are_refused(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    ledger = _ledger(mimir)
    record = await _seed_friction(mimir, ledger)
    record.actions.append({"kind": "file_defect", "status": "ineffective", "acted_at": "t"})
    await ledger.save(record)
    commission = {
        "friction": "F1",
        "action": "commission_tool",
        "need": CI_GAP,
        "expected_benefit": "a tool",
    }
    defect = {
        "friction": "F1",
        "action": "file_defect",
        "title": "t",
        "body": "b",
        "expected_benefit": "a fix",
    }
    queue = _Queue()

    report = await _pass(
        mimir,
        ledger,
        ScriptedLLM(plan={"actions": [defect, commission, commission]}),
        dispatch=queue,
        max_actions=3,
    ).run()

    assert [a["kind"] for a in report.actions] == ["commission_tool"]
    reasons = [item["reason"] for item in report.rejected]
    assert reasons == [
        "file_defect was already tried for this friction without effect",
        "this friction was already acted on in this pass",
    ]
    assert len(queue.tasks) == 1


async def test_only_owned_active_learnings_are_demoted_or_credited(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    pass_ = _pass(mimir, _ledger(mimir), ScriptedLLM(), dispatch=_Queue())
    shared = "learnings/shared/diff.md"
    page = (
        candidate_page(DIFF_ROUTE, session_id="s-0", evidence="403", date=NOW)
        .replace("category: learning-candidates", "category: learnings")
        .replace("status: candidate", "status: promoted")
    )
    await mimir.upsert_page(shared, page)
    by_path = {lesson.path: lesson for lesson in await _index().load(mimir)}
    record = FrictionRecord(path="friction/x.md", kind="retry", subject="x")

    await pass_._demote(shared, by_path, "did not help")
    await pass_._credit({"target": shared, "id": "a"}, record, by_path, "worked", now=NOW)
    await pass_._demote("learnings/general/missing.md", by_path, "did not help")

    assert await mimir.read_page(shared) == page


async def test_a_learning_without_frontmatter_cannot_be_demoted_silently(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    pass_ = _pass(mimir, _ledger(mimir), ScriptedLLM(), dispatch=_Queue())
    path = "learnings/general/bare.md"
    await mimir.upsert_page(path, "# Learning: bare\n\n## What was learned\nclaim\n")
    by_path = {lesson.path: lesson for lesson in await _index().load(mimir)}

    with pytest.raises(RuntimeError, match="cannot demote"):
        await pass_._demote(path, by_path, "did not help")
