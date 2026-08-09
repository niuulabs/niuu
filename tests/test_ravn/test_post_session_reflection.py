"""Tests for PostSessionReflectionService and learnings injection (NIU-588)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.mimir import MimirPage, MimirPageMeta
from ravn.adapters.reflection.post_session import (
    PostSessionReflectionService,
    _build_candidate_content,
    _build_candidate_path,
    _build_page_content,
    _build_page_path,
    _insert_timeline_entry,
    _merge_timeline_entry,
    _strip_frontmatter,
    _titles_similar,
    fetch_relevant_learnings,
)
from ravn.config import PostSessionReflectionConfig
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.catalog import ravn_session_ended
from sleipnir.domain.events import SleipnirEvent

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> PostSessionReflectionConfig:
    defaults = {
        "enabled": True,
        "llm_alias": "fast",
        "max_tokens": 512,
        "learning_token_budget": 500,
        "max_learnings_injected": 5,
    }
    return PostSessionReflectionConfig(**{**defaults, **overrides})


def _make_llm_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.content = text
    return resp


def _make_mimir_page(path: str, title: str, category: str = "learnings") -> MimirPage:
    meta = MimirPageMeta(
        path=path,
        title=title,
        summary="",
        category=category,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    content = f'---\ntitle: "{title}"\ncategory: {category}\n---\n\n# {title}\nBody text.\n'
    return MimirPage(meta=meta, content=content)


# ---------------------------------------------------------------------------
# PostSessionReflectionService — start / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_subscribes_when_enabled():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    config = _make_config(enabled=True)

    svc = PostSessionReflectionService(bus, mimir, llm, config)
    await svc.start()

    assert svc._subscription is not None
    await svc.stop()


@pytest.mark.asyncio
async def test_start_does_not_subscribe_when_disabled():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    config = _make_config(enabled=False)

    svc = PostSessionReflectionService(bus, mimir, llm, config)
    await svc.start()

    assert svc._subscription is None


@pytest.mark.asyncio
async def test_stop_clears_subscription():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    config = _make_config(enabled=True)

    svc = PostSessionReflectionService(bus, mimir, llm, config)
    await svc.start()
    assert svc._subscription is not None
    await svc.stop()
    assert svc._subscription is None


# ---------------------------------------------------------------------------
# PostSessionReflectionService — LLM reflection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_writes_valid_reflection_as_untrusted_candidate():
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = []
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response(
        json.dumps(
            {
                "title": "Use asyncio_mode auto in pytest",
                "learning": "pytest-asyncio requires asyncio_mode=auto for async tests.",
                "type": "observation",
                "tags": ["testing", "pytest"],
                "evidence": "Session failed due to coroutine not awaited errors.",
            }
        )
    )
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    payload = {
        "session_id": "sess-abc",
        "persona": "ravn",
        "outcome": "failure",
        "token_count": 5000,
        "duration_s": 120.0,
        "repo_slug": "niuulabs/volundr",
    }
    await svc._process(payload)

    mimir.upsert_page.assert_awaited_once()
    call_args = mimir.upsert_page.call_args
    path = call_args[0][0]
    content = call_args[0][1]
    assert path.startswith("learning-candidates/")
    assert "asyncio_mode" in content or "pytest" in content
    assert "category: learning-candidates" in content
    assert "status: candidate" in content


@pytest.mark.asyncio
async def test_process_extracts_learning_from_fenced_json():
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = []
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response(
        """```json
{
  "title": "Treat BackoffLimitExceeded as recurring signal",
  "learning": "Repeated reconcile job backoffs should be grouped before paging.",
  "type": "observation",
  "tags": ["k8s", "openbao"],
  "evidence": "The same warning appeared across multiple reconcile jobs."
}
```"""
    )
    config = _make_config(llm_alias="Qwen/Qwen3.6-35B-A3B-FP8")
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "sess-fenced",
            "persona": "k8s-valkyrie",
            "outcome": "propose_action",
            "token_count": 8000,
            "duration_s": 42.0,
            "repo_slug": "niuulabs/volundr",
        }
    )

    mimir.upsert_page.assert_awaited_once()
    assert llm.generate.await_args.kwargs["model"] == "Qwen/Qwen3.6-35B-A3B-FP8"


@pytest.mark.asyncio
async def test_process_extracts_learning_from_prose_wrapped_json():
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = []
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response(
        """
Here is the learning I found:
{
  "title": "Verify cluster tools before proposing remediation",
  "learning": "When cluster inspection tools are unavailable, propose inspection first.",
  "type": "decision",
  "tags": ["k8s", "tools"],
  "evidence": "The session reasoned from the signal alone because direct tooling was unavailable."
}
Done.
"""
    )
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "sess-prose",
            "persona": "k8s-valkyrie",
            "outcome": "propose_action",
            "token_count": 8000,
            "duration_s": 42.0,
            "repo_slug": "niuulabs/volundr",
        }
    )

    mimir.upsert_page.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_skips_on_null_learning():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response("null")
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    payload = {
        "session_id": "sess-xyz",
        "persona": "ravn",
        "outcome": "success",
        "token_count": 1000,
        "duration_s": 30.0,
        "repo_slug": "",
    }
    await svc._process(payload)

    mimir.upsert_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_repo_reflection_prompt_is_session_neutral_and_evidence_bounded():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response("null")
    svc = PostSessionReflectionService(bus, mimir, llm, _make_config())

    await svc._run_reflection(
        {
            "persona": "workshop-steward",
            "outcome": "watch",
            "token_count": 400,
            "duration_s": 2.0,
            "repo_slug": "",
        }
    )

    prompt = llm.generate.await_args.kwargs["messages"][0]["content"]
    assert "coding session" not in prompt
    assert "repository" not in prompt
    assert "environment or subject" in prompt
    assert "no subject-matter context was recorded" in prompt
    assert "Do not infer facts absent from the record" in prompt


@pytest.mark.asyncio
async def test_reflection_prompt_includes_recorded_outcome_context():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response("null")
    svc = PostSessionReflectionService(bus, mimir, llm, _make_config())

    await svc._run_reflection(
        {
            "persona": "k8s-valkyrie",
            "outcome": "success",
            "token_count": 400,
            "duration_s": 2.0,
            "repo_slug": "niuulabs/volundr",
            "outcome_event_type": "valkyrie.judgment.proposed",
            "structured_outcome": {"decision": "watch", "rationale": "pod recovered"},
        }
    )

    prompt = llm.generate.await_args.kwargs["messages"][0]["content"]
    assert "repo_slug:   niuulabs/volundr" in prompt
    assert '"decision": "watch"' in prompt
    assert "future decision in this repository" in prompt


@pytest.mark.asyncio
async def test_reflection_prompt_bounds_recorded_outcome_context():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response("null")
    svc = PostSessionReflectionService(bus, mimir, llm, _make_config())

    await svc._run_reflection(
        {
            "structured_outcome": {"evidence": "x" * 20_000},
            "outcome": "success",
        }
    )

    prompt = llm.generate.await_args.kwargs["messages"][0]["content"]
    assert "recorded context truncated" in prompt
    assert "x" * 13_000 not in prompt


@pytest.mark.asyncio
async def test_process_skips_on_fenced_null_learning():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response("```json\nnull\n```")
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "sess-null-fence",
            "persona": "ravn",
            "outcome": "success",
            "token_count": 1000,
            "duration_s": 30.0,
            "repo_slug": "",
        }
    )

    mimir.upsert_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_treats_no_learning_prose_as_null():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response(
        "No actionable learning can be extracted from this session."
    )
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "sess-no-learning-prose",
            "persona": "ravn",
            "outcome": "success",
            "token_count": 1000,
            "duration_s": 30.0,
            "repo_slug": "",
        }
    )

    mimir.upsert_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_retries_empty_reflection_response():
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = []
    llm = AsyncMock()
    llm.generate.side_effect = [
        _make_llm_response(""),
        _make_llm_response(
            json.dumps(
                {
                    "title": "Retry empty local reflection responses",
                    "learning": "Empty local model responses should be retried once.",
                    "type": "observation",
                    "tags": ["local-llm", "reflection"],
                    "evidence": "The first reflection response was empty.",
                }
            )
        ),
    ]
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "sess-empty-retry",
            "persona": "k8s-valkyrie",
            "outcome": "success",
            "token_count": 8000,
            "duration_s": 42.0,
            "repo_slug": "niuulabs/volundr",
        }
    )

    assert llm.generate.await_count == 2
    mimir.upsert_page.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_extracts_learning_from_yamlish_json():
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = []
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response(
        """{
  "title": "Group repeated warning jobs",
  "learning": "Repeated warning jobs should be grouped before proposing escalation.",
  "type": "observation",
  "tags": ["k8s", "signals",],
  "evidence": "The same warning pattern appeared several times.",
}"""
    )
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "sess-yamlish",
            "persona": "k8s-valkyrie",
            "outcome": "propose_action",
            "token_count": 8000,
            "duration_s": 42.0,
            "repo_slug": "niuulabs/volundr",
        }
    )

    mimir.upsert_page.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_skips_on_llm_error():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.side_effect = RuntimeError("LLM unavailable")
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    payload = {
        "session_id": "sess-err",
        "persona": "ravn",
        "outcome": "error",
        "token_count": 100,
        "duration_s": 5.0,
        "repo_slug": "",
    }
    # Must not raise.
    await svc._process(payload)
    mimir.upsert_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_skips_on_malformed_json():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response("not valid json {{{")
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "sess-bad",
            "persona": "ravn",
            "outcome": "success",
            "token_count": 100,
            "duration_s": 5.0,
            "repo_slug": "",
        }
    )
    mimir.upsert_page.assert_not_awaited()


# ---------------------------------------------------------------------------
# Duplicate detection — update existing page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflection_cannot_mutate_an_existing_trusted_learning():
    existing = _make_mimir_page(
        "learnings/niuulabs-volundr/use-asyncio-mode-auto-in-pytest",
        "Learning: Use asyncio_mode auto in pytest",
    )

    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = [existing]
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response(
        json.dumps(
            {
                "title": "Use asyncio_mode auto in pytest",
                "learning": "pytest-asyncio requires asyncio_mode=auto.",
                "type": "observation",
                "tags": ["testing", "pytest"],
                "evidence": "Second session with same issue.",
            }
        )
    )
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "sess-2nd",
            "persona": "ravn",
            "outcome": "failure",
            "token_count": 3000,
            "duration_s": 80.0,
            "repo_slug": "niuulabs/volundr",
        }
    )

    mimir.upsert_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_third_distinct_candidate_observation_promotes_to_learning():
    candidate_content = _build_candidate_content(
        title="Use asyncio_mode auto in pytest",
        learning="pytest-asyncio requires asyncio_mode=auto.",
        page_type="observation",
        tags=["pytest"],
        evidence="First occurrence.",
        repo_slug="niuulabs/volundr",
        session_id="sess-1",
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    candidate_content = _merge_timeline_entry(
        candidate_content,
        session_id="sess-2",
        evidence="Second occurrence.",
        date=datetime(2026, 1, 2, tzinfo=UTC),
    )
    candidate = MimirPage(
        meta=MimirPageMeta(
            path="learning-candidates/niuulabs-volundr/use-asyncio-mode-auto-in-pytest.md",
            title="Learning candidate: Use asyncio_mode auto in pytest",
            summary="",
            category="learning-candidates",
            updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
        content=candidate_content,
    )
    mimir = AsyncMock()
    mimir.search.return_value = [candidate]
    svc = PostSessionReflectionService(InProcessBus(), mimir, AsyncMock(), _make_config())

    await svc._write_learning(
        {
            "title": "Use asyncio_mode auto in pytest",
            "learning": "pytest-asyncio requires asyncio_mode=auto.",
            "type": "observation",
            "tags": ["pytest"],
            "evidence": "Third occurrence.",
        },
        {
            "session_id": "sess-3",
            "repo_slug": "niuulabs/volundr",
            "outcome_valid": True,
        },
    )

    writes = mimir.upsert_page.await_args_list
    promoted = next(call for call in writes if call.args[0].startswith("learnings/"))
    assert 'promotion_reason: "repeated_evidence:3"' in promoted.args[1]
    assert "category: learnings" in promoted.args[1]
    assert "confidence: high" in promoted.args[1]


@pytest.mark.asyncio
async def test_verified_outcome_can_promote_first_candidate_with_reference():
    mimir = AsyncMock()
    mimir.search.return_value = []
    svc = PostSessionReflectionService(InProcessBus(), mimir, AsyncMock(), _make_config())

    await svc._write_learning(
        {
            "title": "Probe reports actual disk pressure",
            "learning": "Use the read-only probe before escalating.",
            "type": "observation",
            "tags": ["disk"],
            "evidence": "The probe result matched the incident outcome.",
        },
        {
            "session_id": "sess-verified",
            "repo_slug": "",
            "outcome_verified": True,
            "verification_refs": ["ci:run-42"],
        },
    )

    writes = mimir.upsert_page.await_args_list
    promoted = next(call for call in writes if call.args[0].startswith("learnings/"))
    assert 'promotion_reason: "verified_outcome"' in promoted.args[1]


@pytest.mark.asyncio
async def test_schema_valid_outcome_alone_does_not_promote_candidate():
    mimir = AsyncMock()
    mimir.search.return_value = []
    svc = PostSessionReflectionService(InProcessBus(), mimir, AsyncMock(), _make_config())

    await svc._write_learning(
        {
            "title": "One model observation",
            "learning": "A model inferred this once.",
            "type": "observation",
            "tags": [],
            "evidence": "The outcome block was syntactically valid.",
        },
        {
            "session_id": "sess-valid-only",
            "repo_slug": "",
            "outcome_valid": True,
        },
    )

    assert [call.args[0] for call in mimir.upsert_page.await_args_list] == [
        "learning-candidates/general/one-model-observation.md"
    ]


# ---------------------------------------------------------------------------
# Confidence upgrade
# ---------------------------------------------------------------------------


def test_merge_timeline_entry_upgrades_confidence_to_medium_at_two():
    content = _build_page_content(
        title="Pytest asyncio config",
        learning="Use asyncio_mode=auto.",
        page_type="observation",
        tags=["pytest"],
        evidence="First session.",
        repo_slug="niuulabs/volundr",
        session_id="sess-1",
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )

    updated = _merge_timeline_entry(
        content,
        session_id="sess-2",
        evidence="Second occurrence.",
        date=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert "confidence: medium" in updated


def test_merge_timeline_entry_upgrades_confidence_to_high_at_three():
    content = _build_page_content(
        title="Pytest asyncio config",
        learning="Use asyncio_mode=auto.",
        page_type="observation",
        tags=["pytest"],
        evidence="First session.",
        repo_slug="niuulabs/volundr",
        session_id="sess-1",
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # Add a second entry first.
    content = _merge_timeline_entry(
        content,
        session_id="sess-2",
        evidence="Second occurrence.",
        date=datetime(2026, 1, 2, tzinfo=UTC),
    )
    # Add a third entry.
    updated = _merge_timeline_entry(
        content,
        session_id="sess-3",
        evidence="Third occurrence.",
        date=datetime(2026, 1, 3, tzinfo=UTC),
    )

    assert "confidence: high" in updated


def test_merge_timeline_appends_new_entry():
    content = _build_page_content(
        title="Test entry",
        learning="Some learning.",
        page_type="observation",
        tags=[],
        evidence="First.",
        repo_slug="",
        session_id="sess-1",
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )

    updated = _merge_timeline_entry(
        content,
        session_id="sess-new",
        evidence="New evidence.",
        date=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert "sess-new" in updated
    assert "New evidence." in updated
    # Structural correctness: page must still start with frontmatter.
    assert updated.startswith("---\n")
    # New entry must appear inside the frontmatter block (before body).
    fm_close = updated.index("\n---\n", 3)  # closing delimiter
    assert "sess-new" in updated[:fm_close]


# ---------------------------------------------------------------------------
# Page content helpers
# ---------------------------------------------------------------------------


def test_build_page_path_with_repo_slug():
    path = _build_page_path("Use asyncio_mode auto in pytest", "niuulabs/volundr")
    assert path.startswith("learnings/niuulabs-volundr/")
    assert "asyncio" in path or "auto" in path or "pytest" in path


def test_build_page_path_without_repo_slug():
    path = _build_page_path("Generic learning", "")
    assert path.startswith("learnings/general/")


def test_build_candidate_path_is_outside_trusted_learning_namespace():
    path = _build_candidate_path("Generic learning", "")
    assert path.startswith("learning-candidates/general/")
    assert not path.startswith("learnings/")


def test_build_candidate_content_is_explicitly_untrusted():
    content = _build_candidate_content(
        title="Possible pattern",
        learning="This may help.",
        page_type="observation",
        tags=["candidate"],
        evidence="One session suggested it.",
        repo_slug="",
        session_id="session-one",
        date=datetime(2026, 4, 12, tzinfo=UTC),
    )

    assert "category: learning-candidates" in content
    assert "status: candidate" in content
    assert "evidence_count: 1" in content
    assert "do not inject as operational context" in content


def test_build_page_content_has_required_fields():
    content = _build_page_content(
        title="Test learning",
        learning="This is the learning.",
        page_type="observation",
        tags=["tag1", "tag2"],
        evidence="Evidence from session.",
        repo_slug="myorg/myrepo",
        session_id="sess-test",
        date=datetime(2026, 4, 12, tzinfo=UTC),
    )

    assert 'title: "Learning: Test learning"' in content
    assert "type: observation" in content
    assert "category: learnings" in content
    assert "confidence: low" in content
    assert "source: ravn_reflection" in content
    assert "sess-test" in content
    assert "This is the learning." in content
    assert "Evidence from session." in content


# ---------------------------------------------------------------------------
# Similarity detection
# ---------------------------------------------------------------------------


def test_titles_similar_returns_true_for_close_matches():
    assert _titles_similar(
        "Use asyncio_mode auto in pytest",
        "Learning: Use asyncio_mode auto in pytest",
    )


def test_titles_similar_returns_false_for_unrelated():
    assert not _titles_similar(
        "pytest async configuration",
        "kubernetes pod memory limits",
    )


def test_titles_similar_empty_returns_false():
    assert not _titles_similar("", "something")


# ---------------------------------------------------------------------------
# Sleipnir event integration — end-to-end via InProcessBus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_receives_ravn_session_ended_event():
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = []
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response("null")  # no learning extracted
    config = _make_config(enabled=True)

    svc = PostSessionReflectionService(bus, mimir, llm, config)
    await svc.start()

    event = ravn_session_ended(
        session_id="sess-e2e",
        persona="ravn",
        outcome="success",
        token_count=2000,
        duration_s=60.0,
        repo_slug="niuulabs/volundr",
        source="ravn:test",
    )
    await bus.publish(event)
    await bus.flush()

    # LLM was called (reflection ran).
    llm.generate.assert_awaited_once()
    await svc.stop()


@pytest.mark.asyncio
async def test_positive_feedback_promotes_referenced_candidate_without_reflection_call():
    bus = InProcessBus()
    candidate_path = "learning-candidates/general/evidence-backed-probe.md"
    candidate = _build_candidate_content(
        title="Evidence-backed probe",
        learning="Use the probe before escalation.",
        page_type="observation",
        tags=["probe"],
        evidence="One session observed the pattern.",
        repo_slug="",
        session_id="sess-one",
        date=datetime(2026, 7, 20, tzinfo=UTC),
    )
    mimir = AsyncMock()
    mimir.read_page.return_value = candidate
    llm = AsyncMock()
    svc = PostSessionReflectionService(bus, mimir, llm, _make_config())
    await svc.start()

    await bus.publish(
        SleipnirEvent(
            event_type="feedback.recorded",
            source="operator",
            payload={
                "feedback_type": "useful",
                "learning_candidate_path": candidate_path,
                "notes": "The probe prevented a false escalation.",
            },
            summary="Operator confirmed the candidate.",
            urgency=0.2,
            domain="code",
            timestamp=datetime(2026, 7, 20, tzinfo=UTC),
        )
    )
    await bus.flush()

    promoted = next(
        call
        for call in mimir.upsert_page.await_args_list
        if call.args[0] == "learnings/general/evidence-backed-probe.md"
    )
    assert 'promotion_reason: "external_feedback"' in promoted.args[1]
    assert "event_id:" in promoted.args[1]
    llm.generate.assert_not_awaited()
    await svc.stop()


@pytest.mark.asyncio
async def test_on_session_ended_never_raises_on_error():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.side_effect = Exception("catastrophic failure")
    config = _make_config(enabled=True)

    svc = PostSessionReflectionService(bus, mimir, llm, config)

    fake_event = ravn_session_ended(
        session_id="s",
        persona="p",
        outcome="error",
        token_count=0,
        duration_s=0.0,
        repo_slug="",
        source="test",
    )
    # Must not raise.
    await svc._on_session_ended(fake_event)


# ---------------------------------------------------------------------------
# fetch_relevant_learnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_returns_empty_on_no_pages():
    mimir = AsyncMock()
    mimir.list_pages.return_value = []

    result = await fetch_relevant_learnings(
        mimir, repo_slug="myrepo", max_pages=5, token_budget=500
    )
    assert result == ""


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_includes_page_content():
    meta = MimirPageMeta(
        path="learnings/myrepo/some-learning",
        title="Some learning",
        summary="",
        category="learnings",
        updated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    mimir = AsyncMock()
    mimir.list_pages.return_value = [meta]
    mimir.read_page.return_value = "---\ntitle: Some learning\n---\n\nThe body of the learning."

    result = await fetch_relevant_learnings(
        mimir, repo_slug="myrepo", max_pages=5, token_budget=500
    )

    assert "Past Learnings" in result
    assert "body of the learning" in result


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_raises_when_mimir_cannot_be_listed():
    """An unreadable Mímir is not the same as a resident with no learnings.

    Both used to return "". One means nothing has been promoted yet; the other
    means the resident has lost access to everything its flock ever promoted,
    and it would inject an empty block and carry on saying nothing was wrong.
    """
    mimir = AsyncMock()
    mimir.list_pages.side_effect = RuntimeError("storage error")

    with pytest.raises(RuntimeError, match="storage error"):
        await fetch_relevant_learnings(mimir, repo_slug="myrepo", max_pages=5, token_budget=500)


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_returns_empty_when_none_exist():
    """No learnings is an answer, and stays an empty string."""
    mimir = AsyncMock()
    mimir.list_pages.return_value = []

    result = await fetch_relevant_learnings(
        mimir, repo_slug="myrepo", max_pages=5, token_budget=500
    )
    assert result == ""


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_no_repo_slug_returns_all_pages():
    meta = MimirPageMeta(
        path="learnings/general/some-learning",
        title="Some learning",
        summary="",
        category="learnings",
        updated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    mimir = AsyncMock()
    mimir.list_pages.return_value = [meta]
    mimir.read_page.return_value = "---\ntitle: Some\n---\n\nBody text."

    result = await fetch_relevant_learnings(mimir, repo_slug="", max_pages=5, token_budget=500)

    assert "Body text" in result


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_returns_empty_when_no_matching_repo():
    meta = MimirPageMeta(
        path="learnings/other-repo/some-learning",
        title="Some learning",
        summary="",
        category="learnings",
        updated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    mimir = AsyncMock()
    mimir.list_pages.return_value = [meta]

    result = await fetch_relevant_learnings(
        mimir, repo_slug="my-repo", max_pages=5, token_budget=500
    )
    assert result == ""


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_raises_when_a_named_page_cannot_be_read():
    meta = MimirPageMeta(
        path="learnings/general/some-learning",
        title="Some learning",
        summary="",
        category="learnings",
        updated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    mimir = AsyncMock()
    mimir.list_pages.return_value = [meta]
    mimir.read_page.side_effect = RuntimeError("read error")

    # list_pages just named this page, so a failed read means the mount went
    # away mid-call — not that the page is absent. Skipping it silently
    # shrinks the injected learnings with nothing to show it happened.
    with pytest.raises(RuntimeError, match="read error"):
        await fetch_relevant_learnings(mimir, repo_slug="", max_pages=5, token_budget=500)


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_skips_pages_with_empty_body():
    meta = MimirPageMeta(
        path="learnings/general/some-learning",
        title="Some learning",
        summary="",
        category="learnings",
        updated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    mimir = AsyncMock()
    mimir.list_pages.return_value = [meta]
    # Body is only whitespace after stripping frontmatter.
    mimir.read_page.return_value = "---\ntitle: Some\n---\n\n   "

    result = await fetch_relevant_learnings(mimir, repo_slug="", max_pages=5, token_budget=500)
    assert result == ""


@pytest.mark.asyncio
async def test_fetch_relevant_learnings_stops_at_token_budget():
    metas = [
        MimirPageMeta(
            path=f"learnings/general/learning-{i}",
            title=f"Learning {i}",
            summary="",
            category="learnings",
            updated_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        for i in range(5)
    ]
    mimir = AsyncMock()
    mimir.list_pages.return_value = metas
    # Every page has a body that is 200 chars — total budget of 5 tokens (20 chars) exceeded
    mimir.read_page.return_value = "---\ntitle: foo\n---\n\n" + "X" * 200

    # token_budget=5 → char_budget=20; first entry already exceeds it.
    result = await fetch_relevant_learnings(mimir, repo_slug="", max_pages=5, token_budget=5)
    assert result == ""


# ---------------------------------------------------------------------------
# PostSessionReflectionService — lifecycle edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_when_subscription_is_none_is_noop():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    config = _make_config(enabled=True)
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    # Never called start(), so _subscription is None.
    await svc.stop()
    assert svc._subscription is None


@pytest.mark.asyncio
async def test_stop_handles_unsubscribe_exception():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    config = _make_config(enabled=True)
    svc = PostSessionReflectionService(bus, mimir, llm, config)
    await svc.start()

    # Make unsubscribe raise.
    svc._subscription.unsubscribe = AsyncMock(side_effect=RuntimeError("network fail"))

    # Must not raise; subscription should still be cleared.
    await svc.stop()
    assert svc._subscription is None


@pytest.mark.asyncio
async def test_on_session_ended_catches_exception_from_process():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    config = _make_config(enabled=True)
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    # Patch _process to raise directly, bypassing its own error handling.
    svc._process = AsyncMock(side_effect=RuntimeError("internal failure"))

    fake_event = ravn_session_ended(
        session_id="s",
        persona="p",
        outcome="error",
        token_count=0,
        duration_s=0.0,
        repo_slug="",
        source="test",
    )
    # Must not raise — _on_session_ended swallows all exceptions.
    await svc._on_session_ended(fake_event)


# ---------------------------------------------------------------------------
# _run_reflection — non-dict JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_skips_on_llm_returning_json_array():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    llm.generate.return_value = _make_llm_response('["a", "b"]')
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._process(
        {
            "session_id": "s",
            "persona": "p",
            "outcome": "ok",
            "token_count": 0,
            "duration_s": 0.0,
            "repo_slug": "",
        }
    )
    mimir.upsert_page.assert_not_awaited()


# ---------------------------------------------------------------------------
# _write_learning — edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_learning_skips_on_empty_title():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    await svc._write_learning(
        {"title": "   ", "learning": "foo", "type": "observation", "tags": [], "evidence": "e"},
        {"session_id": "s", "repo_slug": ""},
    )
    mimir.upsert_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_learning_handles_upsert_error_on_update():
    existing = _make_mimir_page("learnings/general/some-thing", "Learning: Some thing")
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = [existing]
    mimir.upsert_page.side_effect = RuntimeError("write error")
    llm = AsyncMock()
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    learning = {
        "title": "Some thing",
        "learning": "foo",
        "type": "observation",
        "tags": [],
        "evidence": "e",
    }
    # Must not raise.
    await svc._write_learning(learning, {"session_id": "s", "repo_slug": ""})


@pytest.mark.asyncio
async def test_write_learning_handles_upsert_error_on_create():
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = []
    mimir.upsert_page.side_effect = RuntimeError("write error")
    llm = AsyncMock()
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    learning = {
        "title": "New thing",
        "learning": "foo",
        "type": "observation",
        "tags": [],
        "evidence": "e",
    }
    # Must not raise.
    await svc._write_learning(learning, {"session_id": "s", "repo_slug": ""})


# ---------------------------------------------------------------------------
# _find_existing_page — edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_existing_page_returns_none_for_empty_title():
    bus = InProcessBus()
    mimir = AsyncMock()
    llm = AsyncMock()
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    result = await svc._find_existing_page("", "")
    assert result is None
    mimir.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_existing_page_returns_none_on_search_error():
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.side_effect = RuntimeError("search failure")
    llm = AsyncMock()
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    result = await svc._find_existing_page("some relevant title", "")
    assert result is None


@pytest.mark.asyncio
async def test_find_existing_page_skips_non_learnings_category():
    non_learning = _make_mimir_page("docs/some-doc", "Some doc", category="docs")
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = [non_learning]
    llm = AsyncMock()
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    result = await svc._find_existing_page("Some doc", "")
    assert result is None


@pytest.mark.asyncio
async def test_find_existing_page_returns_none_when_no_title_matches():
    # A learnings page in results that does NOT match the title (different topic).
    unrelated = _make_mimir_page(
        "learnings/general/kubernetes-pod-limits",
        "Learning: Kubernetes pod memory limits",
    )
    bus = InProcessBus()
    mimir = AsyncMock()
    mimir.search.return_value = [unrelated]
    llm = AsyncMock()
    config = _make_config()
    svc = PostSessionReflectionService(bus, mimir, llm, config)

    result = await svc._find_existing_page("pytest asyncio configuration", "")
    assert result is None


# ---------------------------------------------------------------------------
# _insert_timeline_entry — no frontmatter delimiter
# ---------------------------------------------------------------------------


def test_insert_timeline_entry_without_any_frontmatter():
    # Content with no "---" delimiters at all — entry appended at end.
    content = "# Some page\n\nSome body text without any dashes."
    result = _insert_timeline_entry(content, "new entry line")
    assert "new entry line" in result


def test_insert_timeline_entry_places_entry_inside_frontmatter():
    content = _build_page_content(
        title="Structural test",
        learning="The learning.",
        page_type="observation",
        tags=[],
        evidence="First evidence.",
        repo_slug="",
        session_id="sess-A",
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )

    result = _insert_timeline_entry(content, "  - source: ravn_reflection\n    session_id: sess-B")

    # Page must still open with frontmatter.
    assert result.startswith("---\n")
    # The new entry must be inside the frontmatter, not after the closing ---.
    fm_close_pos = result.index("\n---\n", 3)
    assert "sess-B" in result[:fm_close_pos]
    # Body section must still follow the closing delimiter.
    assert "# Learning: Structural test" in result[fm_close_pos:]


# ---------------------------------------------------------------------------
# _strip_frontmatter — edge cases
# ---------------------------------------------------------------------------


def test_strip_frontmatter_content_without_opening_delimiter():
    content = "# Just a heading\n\nNo frontmatter here."
    result = _strip_frontmatter(content)
    assert result == content


def test_strip_frontmatter_content_with_no_closing_delimiter():
    content = "---\ntitle: Missing closing\nNo closing delimiter anywhere."
    result = _strip_frontmatter(content)
    assert result == content
