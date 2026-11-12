"""How a gbrain MCP tool result is read (``mimir.adapters.gbrain_mcp``)."""

from __future__ import annotations

import pytest

from mimir.adapters.gbrain_mcp import result_payload


def _text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def test_structured_content_is_the_answer():
    assert result_payload({"structuredContent": {"a": 1}, **_text("[]")}) == {"a": 1}


def test_plain_json_text_is_parsed():
    assert result_payload(_text('[{"slug": "a"}]')) == [{"slug": "a"}]


def test_a_note_after_the_json_is_not_part_of_the_answer():
    # What gbrain's `query` answers for an empty page, seen live on gbrain 0.60.11.0.
    note = "[]\n0 results. retrieved 0 before trimming. degraded: embed_unavailable."
    assert result_payload(_text(note)) == []


@pytest.mark.parametrize(
    "text",
    ["3 pages found", "true story", "no results", ""],
)
def test_prose_is_not_read_as_json(text):
    assert result_payload(_text(text)) is None
