"""E2E test for evidence-gated post-session learning.

Verifies the full cycle:
  session record → isolated candidate → evidence gate → deliberate retrieval
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.reflection.post_session import (
    PostSessionReflectionService,
    fetch_relevant_learnings,
)
from ravn.config import PostSessionReflectionConfig
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.catalog import ravn_session_ended

# ---------------------------------------------------------------------------
# Helpers
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


def _make_llm(response_json: str) -> AsyncMock:
    """Return a mock LLM whose ``generate`` call returns *response_json*."""
    resp = MagicMock()
    resp.content = response_json
    llm = AsyncMock()
    llm.generate.return_value = resp
    return llm


def _make_llm_sequence(*response_jsons: str) -> AsyncMock:
    """Return a mock LLM that answers each call with the next response."""
    responses = []
    for payload in response_jsons:
        resp = MagicMock()
        resp.content = payload
        responses.append(resp)
    llm = AsyncMock()
    llm.generate.side_effect = responses
    return llm


# ---------------------------------------------------------------------------
# Full learning loop: record → candidate → verified promotion → retrieval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_learning_loop_requires_verified_evidence_before_retrieval(tmp_path: Path) -> None:
    bus = InProcessBus()
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = _make_llm(
        json.dumps(
            {
                "title": "Auth middleware uses OIDC correctly",
                "learning": "The auth middleware delegates to OIDC — no custom token layer.",
                "type": "observation",
                "tags": ["auth", "oidc"],
                "evidence": "Session confirmed OIDC flow is wired correctly in the middleware.",
            }
        )
    )
    config = _make_config()

    writer = PostSessionReflectionService(
        subscriber=bus,
        mimir=mimir,
        llm=llm,
        config=config,
    )
    await writer.start()

    # Simulate a session.ended event with a structured outcome.
    event = ravn_session_ended(
        session_id="s1",
        persona="reviewer",
        outcome="success",
        token_count=5000,
        duration_s=30.0,
        repo_slug="niuulabs/volundr",
        source="ravn:test",
    )
    event.payload["structured_outcome"] = {
        "verdict": "pass",
        "summary": "Auth middleware uses OIDC correctly",
    }

    await bus.publish(event)
    await bus.flush()

    candidates = await mimir.list_pages(category="learning-candidates")
    pages = await mimir.list_pages(category="learnings")
    assert len(candidates) == 1
    assert pages == []

    before_verification = await fetch_relevant_learnings(
        mimir,
        repo_slug="niuulabs/volundr",
        max_pages=5,
        token_budget=500,
    )
    assert before_verification == ""

    verified = ravn_session_ended(
        session_id="s2",
        persona="reviewer",
        outcome="success",
        token_count=5000,
        duration_s=30.0,
        repo_slug="niuulabs/volundr",
        source="ravn:test",
    )
    verified.payload["outcome_verified"] = True
    verified.payload["verification_refs"] = ["ci:auth-integration:42"]
    await bus.publish(verified)
    await bus.flush()

    pages = await mimir.list_pages(category="learnings")
    assert len(pages) == 1
    page_content = await mimir.read_page(pages[0].path)
    assert "OIDC" in page_content
    assert "## What was learned" in page_content
    assert 'promotion_reason: "verified_outcome"' in page_content

    # This helper models deliberate retrieval; Phase 0 keeps it out of the
    # automatic prompt path.
    learnings_block = await fetch_relevant_learnings(
        mimir,
        repo_slug="niuulabs/volundr",
        max_pages=5,
        token_budget=500,
    )
    assert "Past Learnings" in learnings_block
    assert "OIDC" in learnings_block

    await writer.stop()


def _ruff_learning(evidence: str) -> str:
    return json.dumps(
        {
            "title": "Ruff must run before commit",
            "learning": "Ruff lint + format must pass before committing.",
            "type": "observation",
            "tags": ["lint"],
            "evidence": evidence,
        }
    )


async def _publish_sessions(bus: InProcessBus, count: int) -> None:
    for session_num in range(count):
        await bus.publish(
            ravn_session_ended(
                session_id=f"sess-{session_num}",
                persona="coder",
                outcome="failure",
                token_count=1000,
                duration_s=20.0,
                repo_slug="niuulabs/volundr",
                source="ravn:test",
            )
        )
        await bus.flush()


@pytest.mark.asyncio
async def test_learning_loop_confidence_escalation(tmp_path: Path) -> None:
    """Three sessions each contributing distinct evidence reach 'high'."""
    bus = InProcessBus()
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = _make_llm_sequence(
        _ruff_learning("CI blocked the auth branch on an unformatted import block."),
        _ruff_learning("A release tag was cut with trailing whitespace in the changelog."),
        _ruff_learning("The nightly job failed on an unused variable in the migration script."),
    )

    writer = PostSessionReflectionService(
        subscriber=bus, mimir=mimir, llm=llm, config=_make_config()
    )
    await writer.start()

    await _publish_sessions(bus, 3)

    pages = await mimir.list_pages(category="learnings")
    assert len(pages) == 1, "Should have a single deduplicated learning page"

    content = await mimir.read_page(pages[0].path)
    assert "confidence: high" in content, "Third observation should upgrade confidence to high"

    await writer.stop()


@pytest.mark.asyncio
async def test_one_observation_reread_does_not_escalate_confidence(tmp_path: Path) -> None:
    """Re-reading one unchanged fact is a single observation, not corroboration.

    A resident polling the same fact on a schedule drove a claim to
    "high confidence, 34 sessions" purely by quoting itself, and that claim then
    kept the resident waiting. Repetition of identical evidence must not count.
    """
    bus = InProcessBus()
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = _make_llm(_ruff_learning("CI blocked on ruff failure."))

    writer = PostSessionReflectionService(
        subscriber=bus, mimir=mimir, llm=llm, config=_make_config()
    )
    await writer.start()

    await _publish_sessions(bus, 5)

    assert await mimir.list_pages(category="learnings") == []
    candidates = await mimir.list_pages(category="learning-candidates")
    assert len(candidates) == 1
    content = await mimir.read_page(candidates[0].path)
    assert "confidence: low" in content
    assert content.count("source: ravn_reflection") == 1

    await writer.stop()


@pytest.mark.asyncio
async def test_learning_loop_no_write_on_null_learning(tmp_path: Path) -> None:
    """When the LLM returns null, no page is written."""
    bus = InProcessBus()
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = _make_llm("null")
    config = _make_config()

    writer = PostSessionReflectionService(subscriber=bus, mimir=mimir, llm=llm, config=config)
    await writer.start()

    event = ravn_session_ended(
        session_id="s-null",
        persona="ravn",
        outcome="success",
        token_count=100,
        duration_s=5.0,
        repo_slug="",
        source="ravn:test",
    )
    await bus.publish(event)
    await bus.flush()

    pages = await mimir.list_pages(category="learnings")
    assert len(pages) == 0, "No learning should be written when LLM returns null"

    await writer.stop()


@pytest.mark.asyncio
async def test_learning_loop_token_budget_respected(tmp_path: Path) -> None:
    """fetch_relevant_learnings respects the token budget cap."""
    bus = InProcessBus()
    mimir = MarkdownMimirAdapter(root=tmp_path)
    llm = _make_llm(
        json.dumps(
            {
                "title": "Large learning page",
                "learning": "X" * 5000,
                "type": "observation",
                "tags": [],
                "evidence": "Session with lots of content.",
            }
        )
    )
    config = _make_config()

    writer = PostSessionReflectionService(subscriber=bus, mimir=mimir, llm=llm, config=config)
    await writer.start()

    event = ravn_session_ended(
        session_id="s-large",
        persona="ravn",
        outcome="success",
        token_count=9000,
        duration_s=120.0,
        repo_slug="",
        source="ravn:test",
    )
    await bus.publish(event)
    await bus.flush()

    # Very small budget — should return nothing.
    result = await fetch_relevant_learnings(
        mimir,
        repo_slug="",
        max_pages=5,
        token_budget=1,  # 1 token = 4 chars — far below the page size
    )
    assert result == ""

    await writer.stop()
