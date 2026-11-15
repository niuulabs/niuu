"""The voices an agent of a team speaks with: its persona's, overridden by its launch's."""

import pytest

from volundr.domain.team import TEAM_MEMBER_KEY, member_voices, persona_voices


def _document(voice: object) -> dict:
    return {"id": "p", "definition": {"voice": voice}}


@pytest.mark.parametrize(
    "document",
    [
        None,
        "not a document",
        {"id": "p"},
        {"id": "p", "definition": "x"},
        {"id": "p", "definition": {}},
        _document("loud"),
        _document({}),
        _document({"voices": "eve"}),
    ],
)
def test_a_document_without_voices_has_none(document: object) -> None:
    assert persona_voices(document) == {}


def test_a_persona_voices_are_its_voice_voices() -> None:
    document = _document({"voices": {"grok-voice": "eve", "openai-realtime": "marin"}})

    assert persona_voices(document) == {"grok-voice": "eve", "openai-realtime": "marin"}


def test_the_launch_overrides_the_persona_key_by_key() -> None:
    config = {
        "persona_definition": _document(
            {"voices": {"grok-voice": "eve", "openai-realtime": "marin"}}
        ),
        TEAM_MEMBER_KEY: {"lead": True, "voices": {"grok-voice": "rex"}},
    }

    assert member_voices(config) == {"grok-voice": "rex", "openai-realtime": "marin"}


def test_the_override_does_not_change_the_stored_persona() -> None:
    document = _document({"voices": {"grok-voice": "eve"}})
    config = {"persona_definition": document, TEAM_MEMBER_KEY: {"voices": {"grok-voice": "rex"}}}

    member_voices(config)

    assert document["definition"]["voice"]["voices"] == {"grok-voice": "eve"}


def test_an_agent_with_neither_has_no_voices() -> None:
    assert member_voices({TEAM_MEMBER_KEY: {"lead": False}}) == {}
    assert member_voices({}) == {}
