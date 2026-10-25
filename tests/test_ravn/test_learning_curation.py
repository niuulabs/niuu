"""Dream-time learning curation, the lesson index, and the judge.

Includes the backfill regression: Regin's three paraphrased GitHub-route
candidates (recorded before semantic matching existed) merge into one promoted
learning, and the contradicting "all routes 403" candidate is superseded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.reflection.learning_curation import (
    CurationReport,
    LearningCurator,
    _PassState,
    _timeline_entries,
)
from ravn.adapters.reflection.lesson_judge import LessonJudge, LessonJudgeError
from ravn.adapters.reflection.lessons import (
    LessonIndex,
    body_section,
    cosine,
    frontmatter_values,
    parse_lesson,
)
from ravn.adapters.reflection.post_session import (
    evidence_is_new_by_meaning,
    fetch_relevant_learnings,
)
from ravn.config import LearningCurationConfig, Settings
from ravn.domain.models import StopReason
from tests.ravn.fixtures.learning_fakes import (
    ALL_ROUTES_BLOCKED,
    ALTERNATE_ROUTES,
    DIFF_ROUTE,
    DISTINCT_HOSTS,
    PROCEDURE,
    UNRELATED,
    KeywordEmbedding,
    ScriptedLLM,
    candidate_page,
)

NOW = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)


def _judge(llm) -> LessonJudge:
    return LessonJudge(llm, model="judge", max_tokens=256, attempts=2, excerpt_chars=400)


def _curator(
    mimir,
    llm=None,
    *,
    embedding: KeywordEmbedding | None = None,
    clock=lambda: NOW,
    **config: object,
) -> LearningCurator:
    return LearningCurator(
        mimir=mimir,
        lessons=LessonIndex(embedding or KeywordEmbedding(), batch_size=64),
        judge=_judge(llm or ScriptedLLM()),
        config=LearningCurationConfig(enabled=True, **config),
        similarity_threshold=0.5,
        evidence_duplicate_similarity=0.9,
        min_repetitions=3,
        verified_recovery_promotes=True,
        clock=clock,
    )


async def _seed_regin(mimir: MarkdownMimirAdapter) -> None:
    """The candidates on Regin's PVC, 2026-10-23/24."""
    seeds = [
        (DIFF_ROUTE, "s-0856", "api.github.com check-runs returned 403 for PR 1160.", 23, 8),
        (DISTINCT_HOSTS, "s-2019", "Probed the web host: the diff came back after a 403.", 23, 20),
        (ALTERNATE_ROUTES, "s-0108", "The patch route for PR 1159 answered.", 24, 1),
        # Newer than every rediscovery, so recency alone would let it win.
        (ALL_ROUTES_BLOCKED, "s-0230", "Three GitHub requests all came back 403.", 24, 2),
    ]
    for lesson, session_id, evidence, day, hour in seeds:
        page = candidate_page(
            lesson,
            session_id=session_id,
            evidence=evidence,
            date=datetime(2026, 10, day, hour, tzinfo=UTC),
        )
        await mimir.upsert_page(f"learning-candidates/general/{session_id}.md", page)
    unrelated = candidate_page(
        UNRELATED,
        session_id="s-old",
        evidence="A migration never reached the cluster.",
        date=datetime(2026, 9, 1, tzinfo=UTC),
    )
    await mimir.upsert_page("learning-candidates/general/s-old.md", unrelated)


# ---------------------------------------------------------------------------
# The backfill regression
# ---------------------------------------------------------------------------


async def test_backfill_merges_regins_paraphrases_and_supersedes_the_403_claim(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await _seed_regin(mimir)
    curator = _curator(mimir)

    report = await curator.curate()

    learnings = await mimir.list_pages(category="learnings")
    assert len(learnings) == 1
    learning_path = learnings[0].path
    learning = await mimir.read_page(learning_path)
    assert 'promotion_reason: "repeated_evidence:3"' in learning
    assert "evidence_count: 3" in learning
    assert f"## Procedure\n{PROCEDURE}" in learning

    assert len(report.paths("merged")) == 2
    assert report.paths("superseded") == ["learning-candidates/general/s-0230.md"]
    assert report.paths("pruned") == ["learning-candidates/general/s-old.md"]
    assert report.paths("rewritten") == [learning_path]

    blocked = await mimir.read_page("learning-candidates/general/s-0230.md")
    assert "status: superseded" in blocked
    assert f'superseded_by: "{learning_path}"' in blocked
    assert "being a promoted learning" in blocked

    # The audit trail says what happened and why.
    audits = await mimir.list_pages(category="learning-curation")
    assert [page.path for page in audits] == [report.audit_path]
    audit = await mimir.read_page(report.audit_path)
    for heading in ("## Merged", "## Superseded", "## Pruned", "## Promoted", "## Rewritten"):
        assert heading in audit

    # And the next turn gets the rule, not the contradicted claim.
    injected = await fetch_relevant_learnings(mimir, repo_slug="", max_pages=5, token_budget=500)
    assert PROCEDURE in injected
    assert ALL_ROUTES_BLOCKED[1] not in injected


async def test_backfill_leaves_lesson_pages_another_writer_put_there(tmp_path: Path) -> None:
    """Regin's backfill stopped on a tool-build note under learnings/tools/.

    The note had no frontmatter, curation judged it the same lesson as a
    candidate, and retiring it raised: the pass died after 659 judgments.
    """
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await _seed_regin(mimir)
    note_path = "learnings/tools/github-diff-route-build.md"
    note = f"# github diff route build\n\n{DIFF_ROUTE[1]}\n"
    await mimir.upsert_page(note_path, note)

    report = await _curator(mimir).curate()

    assert await mimir.read_page(note_path) == note
    assert note_path not in report.paths("merged")
    assert len(report.paths("merged")) == 2


async def test_a_second_pass_finds_nothing_left_to_do(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await _seed_regin(mimir)
    llm = ScriptedLLM()
    curator = _curator(mimir, llm)
    await curator.curate()
    calls_after_first = llm.judge_calls

    report = await curator.curate()

    assert report.actions == []
    assert report.audit_path == ""
    assert llm.judge_calls == calls_after_first


async def test_verified_evidence_beats_more_observations(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    blocked = candidate_page(
        ALL_ROUTES_BLOCKED,
        session_id="s-1",
        evidence="403 everywhere.",
        date=datetime(2026, 10, 24, tzinfo=UTC),
    ).replace("evidence_count: 1", "evidence_count: 4")
    verified = candidate_page(
        DIFF_ROUTE,
        session_id="s-2",
        evidence="diff route answered.",
        date=datetime(2026, 10, 23, tzinfo=UTC),
    ).replace("evidence_count: 1", "evidence_count: 1\nverified: true")
    await mimir.upsert_page("learning-candidates/general/blocked.md", blocked)
    await mimir.upsert_page("learning-candidates/general/verified.md", verified)

    report = await _curator(mimir, max_rewrites=0).curate()

    assert report.paths("superseded") == ["learning-candidates/general/blocked.md"]
    assert "verified evidence" in report.actions[0].reason


async def test_newer_evidence_settles_an_otherwise_even_contradiction(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    for path, lesson, day in (
        ("blocked", ALL_ROUTES_BLOCKED, 23),
        ("diff", DIFF_ROUTE, 24),
    ):
        page = candidate_page(
            lesson,
            session_id=path,
            evidence=f"{path} note",
            date=datetime(2026, 10, day, tzinfo=UTC),
        )
        await mimir.upsert_page(f"learning-candidates/general/{path}.md", page)

    report = await _curator(mimir).curate()

    assert report.paths("superseded") == ["learning-candidates/general/blocked.md"]
    assert "newer evidence" in report.actions[0].reason


async def test_more_observations_settle_a_contradiction(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    blocked = candidate_page(
        ALL_ROUTES_BLOCKED,
        session_id="s-1",
        evidence="403 everywhere.",
        date=datetime(2026, 10, 24, tzinfo=UTC),
    )
    diff = candidate_page(
        DIFF_ROUTE, session_id="s-2", evidence="diff ok", date=datetime(2026, 10, 23, tzinfo=UTC)
    ).replace("evidence_count: 1", "evidence_count: 2")
    await mimir.upsert_page("learning-candidates/general/blocked.md", blocked)
    await mimir.upsert_page("learning-candidates/general/diff.md", diff)

    report = await _curator(mimir).curate()

    assert "more observations (2 vs 1)" in report.actions[0].reason


async def test_a_merge_carries_verification_and_promotes_on_it(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    plain = candidate_page(
        DIFF_ROUTE, session_id="s-1", evidence="diff ok", date=datetime(2026, 10, 24, tzinfo=UTC)
    )
    verified = candidate_page(
        ALTERNATE_ROUTES,
        session_id="s-2",
        evidence="route answered",
        date=datetime(2026, 10, 23, tzinfo=UTC),
    ).replace(
        "evidence_count: 1",
        'evidence_count: 1\nverified: true\nverification: "web_fetch: api 403; diff ok"',
    )
    await mimir.upsert_page("learning-candidates/general/plain.md", plain)
    await mimir.upsert_page("learning-candidates/general/verified.md", verified)

    report = await _curator(mimir, max_rewrites=0).curate()

    assert report.paths("merged") == ["learning-candidates/general/plain.md"]
    promoted = report.actions[-1]
    assert promoted.action == "promoted"
    assert promoted.reason == "verified_route_recovery"


async def test_restated_evidence_is_not_counted_as_a_new_observation(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    same_note = "api.github.com returned 403."
    for session_id, lesson in (("s-1", DIFF_ROUTE), ("s-2", ALTERNATE_ROUTES)):
        page = candidate_page(
            lesson,
            session_id=session_id,
            evidence=same_note,
            date=datetime(2026, 10, 24, tzinfo=UTC),
        )
        await mimir.upsert_page(f"learning-candidates/general/{session_id}.md", page)

    report = await _curator(mimir).curate()

    survivor = report.actions[0].target
    assert "evidence_count: 1" in await mimir.read_page(survivor)
    assert report.paths("promoted") == []


async def test_scoped_learnings_are_never_curated(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    shared = candidate_page(
        ALL_ROUTES_BLOCKED,
        session_id="s-1",
        evidence="403",
        date=datetime(2026, 10, 1, tzinfo=UTC),
    ).replace("category: learning-candidates", "category: learnings")
    await mimir.upsert_page("learnings/shared/blocked.md", shared)
    page = candidate_page(
        DIFF_ROUTE, session_id="s-2", evidence="diff", date=datetime(2026, 10, 24, tzinfo=UTC)
    )
    await mimir.upsert_page("learning-candidates/general/diff.md", page)

    report = await _curator(mimir).curate()

    assert report.lessons_reviewed == 1
    assert "status: candidate" in await mimir.read_page("learnings/shared/blocked.md")


async def test_judgments_are_bounded_per_pass(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await _seed_regin(mimir)
    llm = ScriptedLLM()

    report = await _curator(mimir, llm, max_judgments=1, max_rewrites=0).curate(max_judgments=1)

    assert report.pairs_judged == 1


async def test_a_failed_pass_is_audited_and_raised(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    await _seed_regin(mimir)
    llm = ScriptedLLM()
    curator = _curator(mimir, llm)

    async def broken_procedure(*_args, **_kwargs):
        raise LessonJudgeError("judge rewrite is not a rule")

    curator._judge.procedure = broken_procedure  # type: ignore[method-assign]

    with pytest.raises(LessonJudgeError):
        await curator.curate()

    audits = await mimir.list_pages(category="learning-curation")
    assert len(audits) == 1
    audit = await mimir.read_page(audits[0].path)
    assert "## Error" in audit and "## Merged" in audit


async def test_a_lesson_without_frontmatter_cannot_be_retired_silently() -> None:
    mimir = AsyncMock()
    curator = _curator(mimir)
    lesson = parse_lesson(
        "learning-candidates/general/x.md",
        "# no frontmatter\n## What was learned\nclaim\n",
        updated_at=NOW,
    )

    with pytest.raises(RuntimeError, match="cannot retire"):
        await curator._retire(lesson, status="pruned", field="", target="", why="test")


async def test_curation_runs_at_most_once_per_interval() -> None:
    times = [NOW]
    curator = _curator(AsyncMock(), clock=lambda: times[0], min_interval_seconds=3600.0)
    assert curator.due()
    curator._last_run = NOW
    times[0] = NOW + timedelta(minutes=30)
    assert not curator.due()
    times[0] = NOW + timedelta(hours=2)
    assert curator.due()


def test_report_summary_lists_every_action() -> None:
    report = CurationReport(started_at=NOW, lessons_reviewed=3, pairs_judged=2)
    report.record("merged", "a", "b", "same")
    summary = report.summary()
    assert summary["merged"] == ["a"]
    assert summary["superseded"] == []
    assert summary["lessons_reviewed"] == 3


def test_pass_state_follows_moves_and_drops_retired_lessons() -> None:
    first = parse_lesson("learning-candidates/general/a.md", "---\n---\n", updated_at=NOW)
    second = parse_lesson("learning-candidates/general/b.md", "---\n---\n", updated_at=NOW)
    state = _PassState({first.path: first, second.path: second})

    state.move(first.path, second.path)
    assert state.current(first.path) is second
    assert state.pair(first.path, second.path) is None
    state.retire(second.path)
    assert state.current(first.path) is None


def test_timeline_entries_parse_quoted_notes() -> None:
    page = candidate_page(DIFF_ROUTE, session_id="s-1", evidence='said "403"', date=NOW)
    entries = _timeline_entries(page)
    assert entries == [
        {
            "source": "ravn_reflection",
            "session_id": "s-1",
            "date": "2026-10-25T03:00:00Z",
            "note": 'said "403"',
        }
    ]
    assert _timeline_entries("no frontmatter") == []


# ---------------------------------------------------------------------------
# Lesson parsing and the index
# ---------------------------------------------------------------------------


def test_parse_lesson_reads_candidate_and_learning_pages() -> None:
    page = candidate_page(
        DIFF_ROUTE, session_id="s-1", evidence="403", date=datetime(2026, 10, 23, tzinfo=UTC)
    )
    candidate = parse_lesson("learning-candidates/general/x.md", page, updated_at=NOW)
    assert candidate.title == DIFF_ROUTE[0]
    assert candidate.claim.startswith("When api.github.com")
    assert candidate.status == "candidate"
    assert candidate.evidence_count == 1
    assert candidate.session_ids == frozenset({"s-1"})
    assert candidate.last_observed == datetime(2026, 10, 23, tzinfo=UTC)
    assert candidate.active and not candidate.is_learning

    learning = parse_lesson(
        "learnings/general/x.md",
        "---\ntitle: Learning: X\nlast_confirmed: 2026-10-24T00:00:00Z\n---\n"
        "## Summary\nsummary text\n",
        updated_at=datetime(2026, 1, 1),
    )
    assert learning.status == "promoted"
    assert learning.claim == "summary text"
    assert learning.last_observed == datetime(2026, 10, 24, tzinfo=UTC)

    bare = parse_lesson("learnings/general/some-lesson.md", "body", updated_at=datetime(2026, 1, 1))
    assert bare.title == "some lesson"
    assert bare.claim == ""
    assert bare.last_observed.tzinfo is UTC


def test_page_helpers() -> None:
    assert frontmatter_values("no frontmatter") == {}
    assert frontmatter_values("---\nunterminated") == {}
    assert frontmatter_values('---\na: "b"\n  - nested: x\n---\n') == {"a": "b"}
    assert body_section("## A\none\n## B\ntwo\n", "B") == "two"
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


async def test_index_reads_only_changed_pages_and_embeds_only_new_text(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    embedding = KeywordEmbedding()
    index = LessonIndex(embedding, batch_size=64)
    await mimir.upsert_page(
        "learning-candidates/general/a.md",
        candidate_page(DIFF_ROUTE, session_id="s-1", evidence="x", date=NOW),
    )
    lessons = await index.load(mimir)
    await index.rank("github", lessons)
    await index.rank("github", await index.load(mimir))
    assert len(embedding.batches) == 1

    await mimir.delete_page("learning-candidates/general/a.md")
    assert await index.load(mimir) == []
    assert await index.rank("github", []) == []
    assert await index.max_similarity("x", []) == 0.0


async def test_index_refuses_a_misaligned_embedding_batch() -> None:
    embedding = AsyncMock()
    embedding.embed_batch.return_value = [[1.0]]
    index = LessonIndex(embedding, batch_size=64)
    lesson = parse_lesson("learning-candidates/general/a.md", "---\n---\n", updated_at=NOW)
    other = parse_lesson(
        "learning-candidates/general/b.md", "---\ntitle: other\n---\n", updated_at=NOW
    )

    with pytest.raises(RuntimeError, match="misaligned"):
        await index.similar_pairs([lesson, other], threshold=0.5)
    with pytest.raises(RuntimeError, match="misaligned"):
        await index.max_similarity("x", ["a", "b"])


async def test_identical_lessons_pair_without_a_similarity_score() -> None:
    index = LessonIndex(KeywordEmbedding(), batch_size=64)
    content = "---\ntitle: same\n---\n## What was learned\nclaim\n"
    left = parse_lesson("learning-candidates/general/a.md", content, updated_at=NOW)
    right = parse_lesson("learning-candidates/general/b.md", content, updated_at=NOW)

    assert await index.similar_pairs([left, right], threshold=0.99) == [(left, right, 1.0)]


async def test_evidence_novelty_by_meaning() -> None:
    index = LessonIndex(KeywordEmbedding(), batch_size=64)
    page = candidate_page(DIFF_ROUTE, session_id="s-1", evidence="github 403", date=NOW)

    assert not await evidence_is_new_by_meaning(index, page, "github said 403", threshold=0.9)
    assert await evidence_is_new_by_meaning(index, page, "helm migration", threshold=0.9)
    assert not await evidence_is_new_by_meaning(index, page, "an", threshold=0.9)
    assert await evidence_is_new_by_meaning(index, "---\n---\n", "github", threshold=0.9)


# ---------------------------------------------------------------------------
# The judge
# ---------------------------------------------------------------------------


def _llm(*answers: str) -> AsyncMock:
    llm = AsyncMock()
    responses = []
    for answer in answers:
        response = MagicMock()
        response.content = answer
        responses.append(response)
    llm.generate.side_effect = responses
    return llm


def _lesson(title: str, claim: str = "claim") -> object:
    return parse_lesson(
        "learning-candidates/general/x.md",
        f'---\ntitle: "Learning candidate: {title}"\n---\n## What was learned\n{claim}\n',
        updated_at=NOW,
    )


async def test_judge_match_returns_the_named_lesson() -> None:
    judge = _judge(_llm('{"same": 2, "reason": "same"}'))
    match = await judge.match(title="t", claim="c", recorded=[_lesson("a"), _lesson("b")])
    assert match.index == 1


async def test_judge_match_accepts_no_match_and_skips_an_empty_shortlist() -> None:
    llm = _llm('{"same": null, "reason": "different"}')
    judge = _judge(llm)
    assert (await judge.match(title="t", claim="c", recorded=[_lesson("a")])).index is None
    assert (await judge.match(title="t", claim="c", recorded=[])).index is None
    assert llm.generate.await_count == 1


@pytest.mark.parametrize("same", ['"two"', "true", "3", "0"])
async def test_judge_match_rejects_answers_naming_no_shown_lesson(same: str) -> None:
    judge = _judge(_llm(f'{{"same": {same}}}'))
    with pytest.raises(LessonJudgeError):
        await judge.match(title="t", claim="c", recorded=[_lesson("a"), _lesson("b")])


async def test_judge_cut_off_by_its_budget_names_the_setting() -> None:
    response = MagicMock()
    response.content = ""
    response.stop_reason = StopReason.MAX_TOKENS
    llm = AsyncMock()
    llm.generate.return_value = response
    with pytest.raises(LessonJudgeError, match="reflection.matching.judge_max_tokens"):
        await _judge(llm).match(title="t", claim="c", recorded=[_lesson("a")])
    assert llm.generate.await_count == 1


async def test_judge_relate_and_procedure() -> None:
    judge = _judge(
        _llm(
            '{"relation": "Contradicts", "reason": "opposite"}',
            '{"relation": "maybe"}',
            json.dumps({"procedure": "When x, do y."}),
            json.dumps({"procedure": "Do y."}),
            json.dumps({"procedure": "When " + "x" * 300}),
        )
    )
    verified = _lesson("a")
    assert (await judge.relate(verified, _lesson("b"))).relation == "contradicts"
    with pytest.raises(LessonJudgeError, match="unknown relation"):
        await judge.relate(verified, _lesson("b"))
    assert await judge.procedure(verified, max_chars=100) == "When x, do y."
    with pytest.raises(LessonJudgeError, match="When X, do Y"):
        await judge.procedure(verified, max_chars=100)
    with pytest.raises(LessonJudgeError, match="limit"):
        await judge.procedure(verified, max_chars=100)


async def test_judge_procedure_mentions_verification() -> None:
    llm = _llm(json.dumps({"procedure": "When x, do y."}))
    lesson = parse_lesson("learnings/general/x.md", "---\nverified: true\n---\n", updated_at=NOW)
    await _judge(llm).procedure(lesson, max_chars=100)
    assert "recovered tool route" in llm.generate.await_args.kwargs["messages"][0]["content"]


async def test_judge_retries_a_malformed_answer_then_fails_loudly() -> None:
    recovered = _judge(_llm("not json", '{"relation": "same"}'))
    assert (await recovered.relate(_lesson("a"), _lesson("b"))).relation == "same"

    broken = _judge(_llm("", "still not json"))
    with pytest.raises(LessonJudgeError, match="judge_llm_alias"):
        await broken.relate(_lesson("a"), _lesson("b"))


def test_judge_needs_at_least_one_attempt() -> None:
    with pytest.raises(ValueError):
        LessonJudge(AsyncMock(), model="m", max_tokens=1, attempts=0, excerpt_chars=40)


# ---------------------------------------------------------------------------
# Wakefulness hook
# ---------------------------------------------------------------------------


def _machine(curator) -> object:
    from ravn.valkyrie_evolution.resident_learning import ResidentLearningIdentity
    from ravn.valkyrie_evolution.wakefulness import ResidentWakefulness

    skills = AsyncMock()
    skills.list_skills.return_value = []
    machine = ResidentWakefulness(
        identity=ResidentLearningIdentity(
            environment_id="nats",
            valkyrie_id="valkyrie:regin",
            domain="delivery",
            flock_ids=[],
            autonomy_mode="guarded",
            environment_type="k8s",
        ),
        skills=skills,
        publisher=AsyncMock(),
        learning_curator=curator,
    )
    return machine


async def test_consolidation_dream_curates_learnings_when_due() -> None:
    curator = MagicMock()
    curator.due.return_value = True
    report = CurationReport(started_at=NOW)
    report.record("merged", "a", "b", "same")
    curator.curate = AsyncMock(return_value=report)
    machine = _machine(curator)

    summary = await machine.dream()

    assert summary["learnings"]["merged"] == ["a"]
    assert machine.state == "watching"


async def test_consolidation_dream_skips_curation_between_intervals() -> None:
    curator = MagicMock()
    curator.due.return_value = False
    curator.curate = AsyncMock()
    machine = _machine(curator)

    summary = await machine.dream()

    assert summary["learnings"] == {}
    curator.curate.assert_not_awaited()


async def test_a_failed_dream_does_not_leave_the_resident_dreaming() -> None:
    curator = MagicMock()
    curator.due.return_value = True
    curator.curate = AsyncMock(side_effect=LessonJudgeError("judge down"))
    machine = _machine(curator)

    with pytest.raises(LessonJudgeError):
        await machine.dream()

    assert machine.state == "watching"


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _settings(**reflection: object) -> Settings:
    settings = Settings()
    for key, value in reflection.items():
        target = settings.reflection
        *parents, leaf = key.split("__")
        for parent in parents:
            target = getattr(target, parent)
        setattr(target, leaf, value)
    return settings


def test_lexical_reflection_builds_no_consolidation() -> None:
    from ravn.cli.runtime_builders import _build_learning_consolidation, _build_learning_curator

    settings = _settings()
    assert _build_learning_consolidation(settings, MagicMock()) is None
    assert _build_learning_curator(settings, mimir=MagicMock(), consolidation=None) is None


def test_semantic_matching_without_embeddings_is_fatal() -> None:
    from ravn.cli.runtime_builders import _build_learning_consolidation

    settings = _settings(matching__mode="semantic")
    with pytest.raises(RuntimeError, match="embedding.enabled is false"):
        _build_learning_consolidation(settings, MagicMock())


def test_curation_without_a_judge_model_is_fatal() -> None:
    from ravn.cli.runtime_builders import _build_learning_consolidation

    settings = _settings(curation__enabled=True)
    settings.embedding.enabled = True
    with pytest.raises(RuntimeError, match="native LLM"):
        _build_learning_consolidation(settings, None)


def test_semantic_consolidation_and_curator_are_built_from_config() -> None:
    from ravn.cli.runtime_builders import (
        _build_learning_consolidation,
        _build_learning_curator,
    )

    settings = _settings(matching__mode="semantic", curation__enabled=True)
    settings.embedding.enabled = True
    with patch("ravn.cli.runtime_builders._build_embedding_port", return_value=KeywordEmbedding()):
        consolidation = _build_learning_consolidation(settings, MagicMock())

    assert isinstance(consolidation.lessons, LessonIndex)
    assert consolidation.judge._model == settings.reflection.llm_alias
    curator = _build_learning_curator(settings, mimir=MagicMock(), consolidation=consolidation)
    assert isinstance(curator, LearningCurator)
    with pytest.raises(RuntimeError, match="no Mímir mount"):
        _build_learning_curator(settings, mimir=None, consolidation=consolidation)


def test_embedding_port_construction_failure_names_the_remedy() -> None:
    from ravn.cli.runtime_builders import _build_embedding_port

    settings = Settings()
    settings.embedding.adapter = "no.such.module.Adapter"
    with pytest.raises(RuntimeError, match="embedding.enabled=false"):
        _build_embedding_port(settings)


def test_learnings_curate_command_requires_curation() -> None:
    from ravn.cli.commands import app

    with patch.dict("os.environ", {"RAVN_CONFIG": "/nonexistent.yaml"}):
        result = CliRunner().invoke(app, ["learnings-curate"])

    assert result.exit_code == 1
    assert "curation.enabled is false" in result.output


def test_learnings_curate_command_runs_one_pass_with_overrides() -> None:
    from ravn.cli import commands

    report = CurationReport(started_at=NOW, lessons_reviewed=214, pairs_judged=300)
    report.record("merged", "a", "b", "same")
    curator = MagicMock()
    curator.curate = AsyncMock(return_value=report)
    settings = _settings(curation__enabled=True)
    with (
        patch.object(commands, "Settings", return_value=settings),
        patch.object(commands, "_build_mimir", return_value=MagicMock()),
        patch.object(commands, "_build_llm", return_value=MagicMock()),
        patch.object(commands, "_build_learning_consolidation", return_value=MagicMock()),
        patch.object(commands, "_build_learning_curator", return_value=curator),
    ):
        result = CliRunner().invoke(
            commands.app, ["learnings-curate", "--max-judgments", "500", "--max-rewrites", "0"]
        )

    assert result.exit_code == 0, result.output
    curator.curate.assert_awaited_once_with(max_judgments=500, max_rewrites=0)
    assert "lessons_reviewed : 214" in result.output
    assert "merged           : 1" in result.output


async def test_pairs_judged_distinct_are_not_judged_again(tmp_path: Path) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    lint = (
        "Helm migration lint runs on GitHub",
        "The helm migration lint job runs in github actions.",
    )
    for session_id, lesson in (("s-1", lint), ("s-2", UNRELATED)):
        page = candidate_page(lesson, session_id=session_id, evidence="seen", date=NOW)
        await mimir.upsert_page(f"learning-candidates/general/{session_id}.md", page)
    llm = ScriptedLLM()
    curator = _curator(mimir, llm)

    first = await curator.curate()
    second = await curator.curate()

    assert first.pairs_judged == 1
    assert second.pairs_judged == 0
    assert first.actions == second.actions == []


async def test_merging_into_a_learning_keeps_it_and_takes_the_verification(
    tmp_path: Path,
) -> None:
    mimir = MarkdownMimirAdapter(root=tmp_path)
    learning = candidate_page(
        DIFF_ROUTE, session_id="s-1", evidence="diff ok", date=datetime(2026, 10, 23, tzinfo=UTC)
    ).replace("category: learning-candidates", "category: learnings")
    await mimir.upsert_page(
        "learnings/general/diff.md", learning.replace("status: candidate", "status: promoted")
    )
    verified = candidate_page(
        ALTERNATE_ROUTES,
        session_id="s-1",
        evidence="route answered",
        date=datetime(2026, 10, 24, tzinfo=UTC),
    ).replace(
        "evidence_count: 1",
        'evidence_count: 1\nverified: true\nverification: "web_fetch: api 403; diff ok"',
    )
    await mimir.upsert_page("learning-candidates/general/verified.md", verified)

    report = await _curator(mimir, max_rewrites=0).curate()

    assert report.paths("merged") == ["learning-candidates/general/verified.md"]
    kept = await mimir.read_page("learnings/general/diff.md")
    assert "verified: true" in kept
    assert 'verification: "web_fetch: api 403; diff ok"' in kept
    # The shared session is one observation, not two.
    assert "evidence_count: 1" in kept


def test_unparseable_timeline_dates_fall_back_to_the_page_time() -> None:
    lesson = parse_lesson(
        "learning-candidates/general/x.md",
        "---\ntimeline:\n  - source: x\n    date: not-a-date\n---\n",
        updated_at=NOW,
    )
    assert lesson.last_observed == NOW


def test_embedding_port_is_built_from_the_configured_adapter() -> None:
    from ravn.cli.runtime_builders import _build_embedding_port

    settings = Settings()
    settings.embedding.adapter = "tests.ravn.fixtures.learning_fakes.KeywordEmbedding"
    assert isinstance(_build_embedding_port(settings), KeywordEmbedding)


def test_learnings_curate_command_reads_the_given_config(tmp_path: Path) -> None:
    from ravn.cli.commands import app

    config = tmp_path / "ravn.yaml"
    config.write_text("reflection:\n  curation:\n    enabled: false\n")
    with patch.dict("os.environ", {}, clear=False):
        result = CliRunner().invoke(app, ["learnings-curate", "--config", str(config)])

    assert result.exit_code == 1
    assert "curation.enabled is false" in result.output


async def test_index_embeds_in_configured_batches() -> None:
    embedding = KeywordEmbedding()
    index = LessonIndex(embedding, batch_size=2)
    lessons = [
        parse_lesson(
            f"learning-candidates/general/{n}.md", f"---\ntitle: lesson {n}\n---\n", updated_at=NOW
        )
        for n in range(5)
    ]

    await index.rank("lesson", lessons)

    assert [len(batch) for batch in embedding.batches] == [2, 2, 1]
    with pytest.raises(ValueError):
        LessonIndex(embedding, batch_size=0)
