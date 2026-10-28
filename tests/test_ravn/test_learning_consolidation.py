"""Semantic candidate matching, verified promotion, and budgeted injection.

The regression these exist for: Regin rediscovered "try the github.com web route
when api.github.com returns 403" in three different wordings on 2026-10-23/24.
Lexical matching recorded three one-observation candidates that never promoted,
so the next turn still believed it could not inspect the patch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.reflection.lesson_judge import LessonJudge
from ravn.adapters.reflection.lessons import LessonIndex
from ravn.adapters.reflection.post_session import (
    PostSessionReflectionService,
    _append_related_observation,
    _apply_verification,
    _cited_route_recovery,
    _promotion_reason,
    _reflection_owns,
    _render_route_recoveries,
    _route_recoveries,
    fetch_relevant_learnings,
    promote_candidate,
    set_procedure,
)
from ravn.agent import RavnAgent
from ravn.agent import _route_recoveries as agent_route_recoveries
from ravn.config import PostSessionReflectionConfig
from ravn.domain.models import ToolCall, ToolResult
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.catalog import ravn_session_ended
from tests.ravn.fixtures.learning_fakes import (
    ALL_ROUTES_BLOCKED,
    ALTERNATE_ROUTES,
    DIFF_ROUTE,
    DISTINCT_HOSTS,
    UNRELATED,
    KeywordEmbedding,
    ScriptedLLM,
    candidate_page,
    reflection,
)
from tests.test_ravn.conftest import AllowAllPermission, InMemoryChannel

RECOVERY = {
    "tool": "web_fetch",
    "failed_input": '{"url": "https://api.github.com/repos/niuulabs/niuu/check-runs"}',
    "failed_result": "HTTP 403: https://api.github.com/repos/niuulabs/niuu/check-runs",
    "succeeded_input": '{"url": "https://github.com/niuulabs/niuu/pull/1160.diff"}',
    "succeeded_result": "diff --git a/src/ravn/agent.py b/src/ravn/agent.py",
}


def _config(**overrides) -> PostSessionReflectionConfig:
    base = {
        "enabled": True,
        "llm_alias": "fast",
        "matching": {"mode": "semantic", "similarity_threshold": 0.5},
    }
    return PostSessionReflectionConfig(**{**base, **overrides})


def _service(
    mimir,
    llm: ScriptedLLM,
    config: PostSessionReflectionConfig | None = None,
    bus: InProcessBus | None = None,
) -> PostSessionReflectionService:
    config = config or _config()
    semantic = config.matching.mode == "semantic"
    return PostSessionReflectionService(
        bus or InProcessBus(),
        mimir,
        llm,
        config,
        lessons=LessonIndex(KeywordEmbedding(), batch_size=64) if semantic else None,
        judge=LessonJudge(
            llm,
            model="judge",
            max_tokens=256,
            attempts=2,
            excerpt_chars=400,
        )
        if semantic
        else None,
    )


def _session(session_id: str, **payload: object) -> dict:
    return {"session_id": session_id, "persona": "regin", "outcome": "success", **payload}


# ---------------------------------------------------------------------------
# The regression: three paraphrases become one promoted learning
# ---------------------------------------------------------------------------


async def test_three_paraphrased_rediscoveries_merge_into_one_promoted_learning(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [
            reflection(DIFF_ROUTE, "api.github.com check-runs returned 403 for PR 1160."),
            reflection(DISTINCT_HOSTS, "Probed the web host: the diff came back after a 403."),
            reflection(ALTERNATE_ROUTES, "The patch route for PR 1159 answered."),
        ]
    )
    service = _service(mimir, llm)

    for session_id in ("s-0856", "s-2019", "s-0108"):
        await service._process(_session(session_id))

    candidates = await mimir.list_pages(category="learning-candidates")
    learnings = await mimir.list_pages(category="learnings")
    assert len(candidates) == 1
    assert len(learnings) == 1

    candidate = await mimir.read_page(candidates[0].path)
    assert "evidence_count: 3" in candidate
    assert "status: promoted" in candidate
    # Every wording is kept as provenance on the one candidate.
    assert DISTINCT_HOSTS[0] in candidate
    assert ALTERNATE_ROUTES[0] in candidate

    learning = await mimir.read_page(learnings[0].path)
    assert 'promotion_reason: "repeated_evidence:3"' in learning
    injected = await fetch_relevant_learnings(mimir, repo_slug="", max_pages=5, token_budget=500)
    assert DIFF_ROUTE[0] in injected


async def test_lexical_matching_keeps_the_paraphrases_apart(tmp_path: Path) -> None:
    """The bug, pinned: word overlap never merges these, so nothing promotes."""
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [
            reflection(DIFF_ROUTE, "api.github.com check-runs returned 403 for PR 1160."),
            reflection(DISTINCT_HOSTS, "Probed the web host: the diff came back after a 403."),
            reflection(ALTERNATE_ROUTES, "The patch route for PR 1159 answered."),
        ]
    )
    service = _service(mimir, llm, _config(matching={"mode": "lexical"}))

    for session_id in ("s-0856", "s-2019", "s-0108"):
        await service._process(_session(session_id))

    assert len(await mimir.list_pages(category="learning-candidates")) == 3
    assert await mimir.list_pages(category="learnings") == []


async def test_a_lesson_tied_to_a_route_recovery_promotes_on_first_observation(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [reflection(DIFF_ROUTE, "The .diff route worked after a 403.", verified_by_recovery=1)]
    )
    service = _service(mimir, llm)

    await service._process(_session("s-1", route_recoveries=[RECOVERY]))

    learnings = await mimir.list_pages(category="learnings")
    assert len(learnings) == 1
    learning = await mimir.read_page(learnings[0].path)
    assert 'promotion_reason: "verified_route_recovery"' in learning
    assert "verified: true" in learning
    assert "pull/1160.diff" in learning
    # The reflection prompt showed the model the recovery it cited.
    assert "HTTP 403" in llm.prompts[0]


async def test_route_recovery_promotion_can_be_switched_off(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [reflection(DIFF_ROUTE, "The .diff route worked after a 403.", verified_by_recovery=1)]
    )
    config = _config(verified_promotion={"route_recovery_promotes": False})
    service = _service(mimir, llm, config)

    await service._process(_session("s-1", route_recoveries=[RECOVERY]))

    assert await mimir.list_pages(category="learnings") == []
    candidates = await mimir.list_pages(category="learning-candidates")
    # The evidence is still recorded as verified; only the policy differs.
    assert "verified: true" in await mimir.read_page(candidates[0].path)


async def test_a_verified_rediscovery_promotes_the_existing_candidate(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [
            reflection(DISTINCT_HOSTS, "github.com answered while api.github.com said 403."),
            reflection(DIFF_ROUTE, "The .diff route worked after a 403.", verified_by_recovery=1),
        ]
    )
    service = _service(mimir, llm)

    await service._process(_session("s-1"))
    await service._process(_session("s-2", route_recoveries=[RECOVERY]))

    assert len(await mimir.list_pages(category="learning-candidates")) == 1
    learnings = await mimir.list_pages(category="learnings")
    assert len(learnings) == 1
    assert "verified_route_recovery" in await mimir.read_page(learnings[0].path)


async def test_distinct_lessons_below_the_threshold_skip_the_judge(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [
            reflection(DIFF_ROUTE, "api.github.com returned 403."),
            reflection(UNRELATED, "A migration was missing from the configmap."),
        ]
    )
    service = _service(mimir, llm)

    await service._process(_session("s-1"))
    await service._process(_session("s-2"))

    assert len(await mimir.list_pages(category="learning-candidates")) == 2
    assert llm.judge_calls == 0


async def test_judge_saying_no_match_records_a_new_candidate(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [
            reflection(DIFF_ROUTE, "api.github.com returned 403."),
            reflection(ALL_ROUTES_BLOCKED, "Every GitHub request failed."),
        ]
    )
    service = _service(mimir, llm)

    await service._process(_session("s-1"))
    await service._process(_session("s-2"))

    assert len(await mimir.list_pages(category="learning-candidates")) == 2
    assert llm.judge_calls == 1


async def test_rediscovering_a_promoted_learning_confirms_it(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [
            reflection(DIFF_ROUTE, "The .diff route worked after a 403.", verified_by_recovery=1),
            reflection(ALTERNATE_ROUTES, "PR 1159 patch read via the .patch web route."),
        ]
    )
    service = _service(mimir, llm)

    await service._process(_session("s-1", route_recoveries=[RECOVERY]))
    await service._process(_session("s-2"))

    learnings = await mimir.list_pages(category="learnings")
    learning = await mimir.read_page(learnings[0].path)
    assert "last_confirmed:" in learning
    # A rediscovery confirms; it never opens a second candidate for a known lesson.
    assert len(await mimir.list_pages(category="learning-candidates")) == 1


async def test_a_scoped_learning_is_never_rewritten_by_reflection() -> None:
    mimir = AsyncMock()
    page = MagicMock()
    page.meta.category = "learnings"
    page.meta.path = "learnings/shared/github-routes.md"
    page.content = "---\ntitle: x\ntimeline:\n---\n"
    service = _service(mimir, ScriptedLLM())

    await service._confirm_learning(page, session_id="s-1", date=datetime.now(UTC))

    mimir.upsert_page.assert_not_awaited()


async def test_a_learning_without_a_timeline_is_left_as_written() -> None:
    mimir = AsyncMock()
    page = MagicMock()
    page.meta.category = "learnings"
    page.meta.path = "learnings/general/github-routes.md"
    page.content = "---\ntitle: x\n---\n\n# x\n"
    service = _service(mimir, ScriptedLLM())

    await service._confirm_learning(page, session_id="s-1", date=datetime.now(UTC))

    mimir.upsert_page.assert_not_awaited()


def test_semantic_mode_without_an_index_is_a_startup_error() -> None:
    with pytest.raises(ValueError, match="embedding.enabled"):
        PostSessionReflectionService(InProcessBus(), AsyncMock(), AsyncMock(), _config())


async def test_semantic_matching_failures_raise_instead_of_recording_a_duplicate(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [
            reflection(DIFF_ROUTE, "api.github.com returned 403."),
            reflection(ALTERNATE_ROUTES, "The .patch route worked."),
        ]
    )
    service = _service(mimir, llm)
    await service._process(_session("s-1"))

    async def broken_judge(**_kwargs):
        raise RuntimeError("judge offline")

    service._judge.classify = broken_judge  # type: ignore[union-attr,method-assign]
    with pytest.raises(RuntimeError, match="judge offline"):
        await service._process(_session("s-2"))
    assert len(await mimir.list_pages(category="learning-candidates")) == 1


async def test_semantic_matching_with_nothing_recorded_creates_a_candidate(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM([reflection(DIFF_ROUTE, "api.github.com returned 403.")])
    service = _service(mimir, llm)

    await service._process(_session("s-1"))

    assert len(await mimir.list_pages(category="learning-candidates")) == 1
    assert llm.judge_calls == 0


async def test_session_ended_events_carry_recoveries_into_reflection(tmp_path: Path) -> None:
    bus = InProcessBus()
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = ScriptedLLM(
        [reflection(DIFF_ROUTE, "The .diff route worked after a 403.", verified_by_recovery=1)]
    )
    service = _service(mimir, llm, bus=bus)
    await service.start()
    event = ravn_session_ended(
        session_id="s-1",
        persona="regin",
        outcome="success",
        token_count=10,
        duration_s=1.0,
        source="ravn:test",
    )
    event.payload["route_recoveries"] = [RECOVERY]

    await bus.publish(event)
    await bus.flush()
    await service.stop()

    assert len(await mimir.list_pages(category="learnings")) == 1


# ---------------------------------------------------------------------------
# Route recovery helpers
# ---------------------------------------------------------------------------


def test_route_recoveries_drop_malformed_entries() -> None:
    payload = {"route_recoveries": [RECOVERY, {"tool": "web_fetch"}, "nonsense"]}
    assert _route_recoveries(payload) == [RECOVERY]
    assert _route_recoveries({"route_recoveries": "not a list"}) == []
    assert _render_route_recoveries([]) == "(none recorded)"
    assert _render_route_recoveries([RECOVERY]).startswith("[1] web_fetch failed")


@pytest.mark.parametrize("cited", [None, True, "two", 0, 2, "7"])
def test_route_recovery_citations_must_name_a_recorded_recovery(cited) -> None:
    learning = {"verified_by_recovery": cited}
    assert _cited_route_recovery(learning, {"route_recoveries": [RECOVERY]}) is None


def test_route_recovery_citation_accepts_a_numeric_string() -> None:
    learning = {"verified_by_recovery": "1"}
    assert _cited_route_recovery(learning, {"route_recoveries": [RECOVERY]}) == RECOVERY


def test_verification_is_recorded_once() -> None:
    page = candidate_page(
        DIFF_ROUTE, session_id="s-1", evidence="403", date=datetime(2026, 10, 23, tzinfo=UTC)
    )
    verified = _apply_verification(page, RECOVERY)
    twice = _apply_verification(verified, RECOVERY)

    assert verified.count("## Verification") == 1
    assert twice.count("## Verification") == 1
    assert "verified: true" in twice


def test_related_observations_accumulate_without_duplicates() -> None:
    page = candidate_page(
        DIFF_ROUTE, session_id="s-1", evidence="403", date=datetime(2026, 10, 23, tzinfo=UTC)
    )
    when = datetime(2026, 10, 24, tzinfo=UTC)
    once = _append_related_observation(
        page, title="A", claim="claim a", session_id="s-2", date=when
    )
    again = _append_related_observation(
        once, title="A", claim="claim a", session_id="s-2", date=when
    )
    more = _append_related_observation(
        again + "\n## Trailing\nkept\n", title="B", claim="claim b", session_id="s-3", date=when
    )

    assert again == once
    assert more.count("## Related observations") == 1
    assert more.index("claim b") < more.index("## Trailing")


def test_promotion_reason_ranks_route_recovery_below_explicit_evidence() -> None:
    assert (
        _promotion_reason({"reviewed_promotion": True}, evidence_count=1, min_repetitions=3)
        == "explicit_reviewed_promotion"
    )
    assert (
        _promotion_reason({}, evidence_count=1, min_repetitions=3, verified_recovery=True)
        == "verified_route_recovery"
    )
    assert _promotion_reason({}, evidence_count=1, min_repetitions=3) == ""


def test_reflection_owns_only_its_own_lesson_pages() -> None:
    written = "---\nstatus: candidate\n---\n\n# Lesson\n"
    assert _reflection_owns("learning-candidates/general/x.md", written)
    assert _reflection_owns("learnings/general/x.md", written)
    assert not _reflection_owns("learnings/shared/x.md", written)
    assert not _reflection_owns("learnings/flock/flock-k8s/x.md", written)
    assert not _reflection_owns("technical/x.md", written)


def test_reflection_does_not_own_lesson_pages_another_writer_put_there() -> None:
    """A tool-build note under learnings/ carries no status for curation to mark."""
    note = "# patch_node_labels build\n\nBuilt and self-verified learned_tool.json.\n"
    assert not _reflection_owns("learnings/tools/patch_node_labels-build.md", note)
    assert not _reflection_owns("learnings/general/x.md", "---\ntitle: x\n---\n")


def test_set_procedure_inserts_then_replaces() -> None:
    page = "---\ntitle: x\n---\n\n# Learning: x\n\n## What was learned\nclaim\n"
    first = set_procedure(page, "When a, do b.")
    second = set_procedure(first, "When c, do d.")

    assert first.index("## Procedure") < first.index("## What was learned")
    assert "When c, do d." in second and "When a, do b." not in second
    assert set_procedure("no heading", "When e, do f.").endswith("## Procedure\nWhen e, do f.\n")


async def test_re_promotion_keeps_a_curated_procedure(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    page = candidate_page(
        DIFF_ROUTE, session_id="s-1", evidence="403", date=datetime(2026, 10, 23, tzinfo=UTC)
    )
    path = await promote_candidate(
        mimir,
        candidate_path="learning-candidates/general/diff.md",
        candidate_content=page,
        title=DIFF_ROUTE[0],
        repo_slug="",
        reason="verified_route_recovery",
    )
    await mimir.upsert_page(path, set_procedure(await mimir.read_page(path), "When x, do y."))
    promoted_candidate = await mimir.read_page("learning-candidates/general/diff.md")

    await promote_candidate(
        mimir,
        candidate_path="learning-candidates/general/diff.md",
        candidate_content=promoted_candidate,
        title=DIFF_ROUTE[0],
        repo_slug="",
        reason="external_feedback",
    )

    assert "When x, do y." in await mimir.read_page(path)


# ---------------------------------------------------------------------------
# Injection: active only, procedural, bounded, relevance-ranked
# ---------------------------------------------------------------------------


def _learning(title: str, claim: str, *, status: str = "promoted", procedure: str = "") -> str:
    lines = [
        "---",
        f'title: "Learning: {title}"',
        "category: learnings",
        f"status: {status}",
        "timeline:",
        "  - source: ravn_reflection",
        "    session_id: s-1",
        "    date: 2026-10-23T08:56:00Z",
        '    note: "seen"',
        "---",
        "",
        f"# Learning: {title}",
        "",
    ]
    if procedure:
        lines += ["## Procedure", procedure, ""]
    lines += ["## What was learned", claim, "", "## Evidence", "Long narrative " * 40]
    return "\n".join(lines) + "\n"


async def test_injection_skips_retired_learnings_and_prefers_the_procedure(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page(
        "learnings/general/diff.md",
        _learning(*DIFF_ROUTE, procedure="When api.github.com 403s, use the .diff route."),
    )
    await mimir.upsert_page(
        "learnings/general/blocked.md", _learning(*ALL_ROUTES_BLOCKED, status="superseded")
    )

    for lessons in (None, LessonIndex(KeywordEmbedding(), batch_size=64)):
        injected = await fetch_relevant_learnings(
            mimir,
            repo_slug="",
            max_pages=5,
            token_budget=500,
            lessons=lessons,
        )
        assert "use the .diff route" in injected
        assert "Long narrative" not in injected
        assert ALL_ROUTES_BLOCKED[1] not in injected


async def test_injection_bounds_each_entry(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page("learnings/general/long.md", _learning("Long", "word " * 400))

    injected = await fetch_relevant_learnings(
        mimir, repo_slug="", max_pages=5, token_budget=500, entry_max_chars=120
    )

    entry = injected.split("Long\n", 1)[1]
    assert len(entry.strip()) <= 120


async def test_injection_ranks_by_relevance_to_the_task(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page("learnings/general/diff.md", _learning(*DIFF_ROUTE))
    await mimir.upsert_page("learnings/general/helm.md", _learning(*UNRELATED))

    injected = await fetch_relevant_learnings(
        mimir,
        repo_slug="",
        max_pages=1,
        token_budget=500,
        query="add a migration and update the helm configmap",
        lessons=LessonIndex(KeywordEmbedding(), batch_size=64),
    )

    assert UNRELATED[0] in injected
    assert DIFF_ROUTE[0] not in injected


async def test_injection_with_an_index_and_no_query_orders_by_recency(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    older = _learning(*UNRELATED)
    newer = _learning(*DIFF_ROUTE).replace("2026-10-23T08:56:00Z", "2026-10-24T01:08:00Z")
    await mimir.upsert_page("learnings/general/helm.md", older)
    await mimir.upsert_page("learnings/general/diff.md", newer)

    injected = await fetch_relevant_learnings(
        mimir,
        repo_slug="",
        max_pages=1,
        token_budget=500,
        lessons=LessonIndex(KeywordEmbedding(), batch_size=64),
    )

    assert DIFF_ROUTE[0] in injected


async def test_injection_returns_empty_when_only_retired_learnings_match(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page(
        "learnings/general/blocked.md", _learning(*ALL_ROUTES_BLOCKED, status="merged")
    )

    assert await fetch_relevant_learnings(mimir, repo_slug="", max_pages=5, token_budget=500) == ""
    assert (
        await fetch_relevant_learnings(
            mimir,
            repo_slug="",
            max_pages=5,
            token_budget=500,
            lessons=LessonIndex(KeywordEmbedding(), batch_size=64),
        )
        == ""
    )


async def test_injection_returns_empty_when_no_prefix_matches(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page("learnings/other-repo/x.md", _learning(*DIFF_ROUTE))

    assert (
        await fetch_relevant_learnings(
            mimir, repo_slug="niuulabs/niuu", max_pages=5, token_budget=500
        )
        == ""
    )


# ---------------------------------------------------------------------------
# Agent: route recoveries come from the turn's own tool results
# ---------------------------------------------------------------------------


def _call(call_id: str, url: str) -> ToolCall:
    return ToolCall(id=call_id, name="web_fetch", input={"url": url})


def test_a_failure_then_success_with_other_input_is_a_recovery() -> None:
    calls = [
        _call("1", "https://api.github.com/check-runs"),
        _call("2", "https://api.github.com/check-runs"),
        ToolCall(id="3", name="echo", input={"message": "hi"}),
        _call("4", "https://github.com/pull/1.diff"),
    ]
    results = [
        ToolResult(tool_call_id="1", content="HTTP 403", is_error=True),
        ToolResult(tool_call_id="2", content="HTTP 403", is_error=True),
        ToolResult(tool_call_id="3", content="hi"),
        ToolResult(tool_call_id="4", content="diff --git"),
    ]

    recoveries = agent_route_recoveries(calls, results, limit=1, excerpt_chars=60)

    assert len(recoveries) == 1
    assert recoveries[0]["tool"] == "web_fetch"
    assert "check-runs" in recoveries[0]["failed_input"]
    assert "pull/1.diff" in recoveries[0]["succeeded_input"]
    assert agent_route_recoveries(calls, results, limit=0, excerpt_chars=60) == []


def test_retrying_the_same_input_is_not_a_recovery() -> None:
    calls = [_call("1", "https://x"), _call("2", "https://x")]
    results = [
        ToolResult(tool_call_id="1", content="timeout", is_error=True),
        ToolResult(tool_call_id="2", content="ok"),
    ]
    assert agent_route_recoveries(calls, results, limit=5, excerpt_chars=60) == []


def _agent(publisher: object, **kwargs: object) -> RavnAgent:
    return RavnAgent(
        llm=MagicMock(),
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="test",
        model="m",
        max_tokens=64,
        max_iterations=2,
        sleipnir_publisher=publisher,
        **kwargs,
    )


async def test_session_ended_carries_the_sessions_route_recoveries() -> None:
    publisher = AsyncMock()
    agent = _agent(publisher, max_route_recoveries=1)
    calls = [_call("1", "https://a"), _call("2", "https://b"), _call("3", "https://c")]
    results = [
        ToolResult(tool_call_id="1", content="HTTP 403", is_error=True),
        ToolResult(tool_call_id="2", content="HTTP 403", is_error=True),
        ToolResult(tool_call_id="3", content="ok"),
    ]
    agent._record_route_recoveries(calls, results)
    agent._record_route_recoveries(calls, results)  # already at the limit

    await agent.emit_session_ended("success")

    payload = publisher.publish.call_args[0][0].payload
    assert len(payload["route_recoveries"]) == 1
    json.dumps(payload)  # crosses the bus as plain data


async def test_session_ended_omits_recoveries_when_none_were_seen() -> None:
    publisher = AsyncMock()
    agent = _agent(publisher, max_route_recoveries=3)

    await agent.emit_session_ended("success")

    assert "route_recoveries" not in publisher.publish.call_args[0][0].payload


async def test_agent_injects_learnings_ranked_by_its_first_input(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page("learnings/general/diff.md", _learning(*DIFF_ROUTE))
    await mimir.upsert_page("learnings/general/helm.md", _learning(*UNRELATED))
    builder = MagicMock()
    agent = _agent(
        None,
        mimir=mimir,
        prompt_builder=builder,
        inject_learnings=True,
        max_learnings_injected=1,
        learning_index=LessonIndex(KeywordEmbedding(), batch_size=64),
        learning_entry_max_chars=200,
    )

    await agent._inject_learnings("why does api.github.com return 403 on check-runs?")

    injected = builder.set_learnings_context.call_args[0][0]
    assert DIFF_ROUTE[0] in injected
    assert UNRELATED[0] not in injected


def test_cited_route_recovery_from_page_helpers_is_plain_data() -> None:
    # The candidate's verification line is rendered from the recorded recovery,
    # not from model prose.
    page = _apply_verification(
        candidate_page(
            DIFF_ROUTE, session_id="s-1", evidence="403", date=datetime(2026, 10, 23, tzinfo=UTC)
        ),
        RECOVERY,
    )
    assert "web_fetch:" in page
    assert "pull/1160.diff" in page


async def test_re_promotion_without_an_existing_learning_page_writes_it(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    page = candidate_page(
        DIFF_ROUTE, session_id="s-1", evidence="403", date=datetime(2026, 10, 23, tzinfo=UTC)
    ).replace("evidence_count: 1", 'evidence_count: 1\npromoted_to: "learnings/general/gone.md"')

    path = await promote_candidate(
        mimir,
        candidate_path="learning-candidates/general/diff.md",
        candidate_content=page,
        title=DIFF_ROUTE[0],
        repo_slug="",
        reason="external_feedback",
    )

    assert path == "learnings/general/gone.md"
    assert "## Procedure" not in await mimir.read_page(path)


async def test_recency_injection_stops_at_max_pages(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page("learnings/general/diff.md", _learning(*DIFF_ROUTE))
    await mimir.upsert_page("learnings/general/helm.md", _learning(*UNRELATED))

    injected = await fetch_relevant_learnings(mimir, repo_slug="", max_pages=1, token_budget=500)

    assert injected.count("### ") == 1


# ---------------------------------------------------------------------------
# Consolidation on write: a paraphrase reinforces the learning it restates
# (noatun, 2026-10-26: 13 of 21 new learnings duplicated an existing one)
# ---------------------------------------------------------------------------


def _promoted(lesson: tuple[str, str], *, session_id: str, evidence: str, count: int = 3) -> str:
    return (
        candidate_page(
            lesson,
            session_id=session_id,
            evidence=evidence,
            date=datetime(2026, 10, 23, tzinfo=UTC),
        )
        .replace("category: learning-candidates", "category: learnings")
        .replace("status: candidate", "status: promoted")
        .replace("evidence_count: 1", f"evidence_count: {count}")
    )


async def test_a_paraphrase_folds_into_the_learning_not_a_sibling_candidate(
    tmp_path: Path,
) -> None:
    """The judge names a stray candidate and the learning; the learning survives."""
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page(
        "learnings/general/diff.md", _promoted(DIFF_ROUTE, session_id="s-0", evidence="diff ok")
    )
    stray = candidate_page(
        ALTERNATE_ROUTES,
        session_id="s-1",
        evidence="the patch route answered",
        date=datetime(2026, 10, 23, tzinfo=UTC),
    )
    await mimir.upsert_page("learning-candidates/general/stray.md", stray)
    llm = ScriptedLLM([reflection(DISTINCT_HOSTS, "the web host answered after a 403")])

    landed = await _service(mimir, llm)._write_learning(llm.reflections[0], _session("s-2"))

    assert landed == "learnings/general/diff.md"
    assert [page.path for page in await mimir.list_pages(category="learnings")] == [landed]
    learning = await mimir.read_page(landed)
    assert "evidence_count: 3" in learning  # s-0 plus s-1 and s-2
    assert "last_confirmed:" in learning
    assert DISTINCT_HOSTS[0] in learning and ALTERNATE_ROUTES[0] in learning
    folded = await mimir.read_page("learning-candidates/general/stray.md")
    assert "status: merged" in folded and 'merged_into: "learnings/general/diff.md"' in folded
    # No page was written for the new observation itself.
    assert [page.path for page in await mimir.list_pages(category="learning-candidates")] == [
        "learning-candidates/general/stray.md"
    ]


async def test_a_verified_paraphrase_does_not_mint_a_second_learning(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page(
        "learnings/general/diff.md", _promoted(DIFF_ROUTE, session_id="s-0", evidence="diff ok")
    )
    llm = ScriptedLLM(
        [reflection(ALTERNATE_ROUTES, "the .diff route worked after a 403", verified_by_recovery=1)]
    )

    await _service(mimir, llm)._process(_session("s-1", route_recoveries=[RECOVERY]))

    assert [p.path for p in await mimir.list_pages(category="learnings")] == [
        "learnings/general/diff.md"
    ]
    assert await mimir.list_pages(category="learning-candidates") == []
    assert "verified: true" in await mimir.read_page("learnings/general/diff.md")


async def test_a_paraphrase_of_a_lesson_another_writer_keeps_records_nothing(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await mimir.upsert_page(
        "learnings/shared/diff.md", _promoted(DIFF_ROUTE, session_id="s-0", evidence="diff ok")
    )
    llm = ScriptedLLM([reflection(ALTERNATE_ROUTES, "the patch route answered")])

    landed = await _service(mimir, llm)._write_learning(llm.reflections[0], _session("s-1"))

    assert landed == "learnings/shared/diff.md"
    assert await mimir.list_pages(category="learning-candidates") == []
    assert "s-1" not in await mimir.read_page("learnings/shared/diff.md")


async def test_learnings_get_their_own_share_of_the_neighbourhood() -> None:
    """A crowd of closer candidates cannot push the learning out of the judge's view."""
    from ravn.adapters.reflection.lesson_consolidation import judge_neighbourhood
    from ravn.adapters.reflection.lessons import parse_lesson

    def lesson(path: str, text: str) -> object:
        return parse_lesson(
            path,
            f'---\ntitle: "{text}"\nstatus: x\n---\n## What was learned\n{text}\n',
            updated_at=datetime(2026, 10, 26, tzinfo=UTC),
        )

    crowd = [lesson(f"learning-candidates/general/{n}.md", "github 403 diff") for n in range(6)]
    learning = lesson("learnings/general/l.md", "github 403 diff route host")
    judge = MagicMock()
    judge.classify = AsyncMock(return_value=MagicMock(same=(), contradicts=(), reason=""))

    await judge_neighbourhood(
        lessons=LessonIndex(KeywordEmbedding(), batch_size=64),
        judge=judge,
        title="github 403",
        claim="diff",
        pool=[*crowd, learning],
        threshold=0.5,
        per_category=2,
    )

    shown = judge.classify.await_args.kwargs["lessons"]
    assert shown[0] is learning
    assert len(shown) == 3


async def test_noatun_duplicate_learnings_collapse_and_stay_collapsed(tmp_path: Path) -> None:
    """Existing duplicates merge in the dream; the next paraphrase lands on the survivor."""
    from ravn.adapters.reflection.learning_curation import LearningCurator
    from ravn.config import LearningCurationConfig

    mimir = MarkdownMimirAdapter(root=tmp_path)
    for name, lesson, count in (
        ("diff", DIFF_ROUTE, 9),
        ("hosts", DISTINCT_HOSTS, 7),
        ("routes", ALTERNATE_ROUTES, 3),
    ):
        await mimir.upsert_page(
            f"learnings/general/{name}.md",
            _promoted(lesson, session_id=f"s-{name}", evidence=f"{name} worked", count=count),
        )
    index = LessonIndex(KeywordEmbedding(), batch_size=64)
    llm = ScriptedLLM([reflection(ALTERNATE_ROUTES, "the .patch route answered once more")])
    judge = LessonJudge(llm, model="judge", max_tokens=256, attempts=2, excerpt_chars=400)
    curator = LearningCurator(
        mimir=mimir,
        lessons=index,
        judge=judge,
        config=LearningCurationConfig(enabled=True),
        similarity_threshold=0.5,
        max_related=5,
        evidence_duplicate_similarity=0.9,
        min_repetitions=3,
        verified_recovery_promotes=True,
        procedure_max_chars=600,
        clock=lambda: datetime(2026, 10, 27, tzinfo=UTC),
    )

    report = await curator.curate()

    assert sorted(report.paths("merged")) == [
        "learnings/general/hosts.md",
        "learnings/general/routes.md",
    ]
    active = [
        page.path
        for page in await mimir.list_pages(category="learnings")
        if "status: promoted" in await mimir.read_page(page.path)
    ]
    assert active == ["learnings/general/diff.md"]

    service = PostSessionReflectionService(
        InProcessBus(), mimir, llm, _config(), lessons=index, judge=judge
    )
    landed = await service._write_learning(llm.reflections[0], _session("s-new"))
    assert landed == "learnings/general/diff.md"
    assert await mimir.list_pages(category="learning-candidates") == []
