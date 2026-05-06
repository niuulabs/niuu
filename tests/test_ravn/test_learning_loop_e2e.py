"""E2E test for the post-session learning loop (NIU-598).

Verifies the full cycle:
  ravn.session.ended event  →  learning written to Mímir  →  learning injected in next session
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


# ---------------------------------------------------------------------------
# Full learning loop: session.ended → Mímir write → injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_learning_loop_session_end_to_injection(tmp_path: Path) -> None:
    """Session ends → learning written to Mímir → new session injects it."""
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

    # --- Verify learning page was written to Mímir -------------------------
    pages = await mimir.list_pages(category="learnings")
    assert len(pages) > 0, "Expected at least one learning page after session ended"

    page_content = await mimir.read_page(pages[0].path)
    assert "OIDC" in page_content
    assert "## What was learned" in page_content
    assert "confidence: low" in page_content

    # --- Verify learning is injected in a new session ----------------------
    learnings_block = await fetch_relevant_learnings(
        mimir,
        repo_slug="niuulabs/volundr",
        max_pages=5,
        token_budget=500,
    )
    assert "Past Learnings" in learnings_block
    assert "OIDC" in learnings_block

    await writer.stop()


@pytest.mark.asyncio
async def test_learning_loop_confidence_escalation(tmp_path: Path) -> None:
    """Three sessions with the same pattern upgrade confidence to 'high'."""
    bus = InProcessBus()
    mimir = MarkdownMimirAdapter(root=tmp_path)
    config = _make_config()

    title = "Ruff must run before commit"
    learning_json = json.dumps(
        {
            "title": title,
            "learning": "Ruff lint + format must pass before committing.",
            "type": "observation",
            "tags": ["lint"],
            "evidence": "CI blocked on ruff failure.",
        }
    )
    llm = _make_llm(learning_json)

    writer = PostSessionReflectionService(subscriber=bus, mimir=mimir, llm=llm, config=config)
    await writer.start()

    for session_num in range(3):
        event = ravn_session_ended(
            session_id=f"sess-{session_num}",
            persona="coder",
            outcome="failure",
            token_count=1000,
            duration_s=20.0,
            repo_slug="niuulabs/volundr",
            source="ravn:test",
        )
        await bus.publish(event)
        await bus.flush()

    pages = await mimir.list_pages(category="learnings")
    assert len(pages) == 1, "Should have a single deduplicated learning page"

    content = await mimir.read_page(pages[0].path)
    assert "confidence: high" in content, "Third observation should upgrade confidence to high"

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
