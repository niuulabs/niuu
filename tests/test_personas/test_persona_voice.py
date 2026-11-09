"""The persona ``voice:`` section: parsing, round trips and portable documents."""

from __future__ import annotations

import pytest
import yaml

from personas.domain.persona import (
    PersonaConfig,
    PersonaVoiceConfig,
    parse_persona,
    persona_to_yaml,
)
from personas.domain.persona_document import (
    PersonaDocumentError,
    parse_portable_persona,
    portable_persona_from_config,
    portable_persona_yaml,
)

_VOICE_PERSONA = """
name: speaker
system_prompt_template: You speak.
iteration_budget: 4
voice:
  voices:
    grok-voice: ara
    openai-realtime: marin
  style: Short sentences.
  tools: [web_search]
  grok:
    reasoning:
      effort: high
"""


def test_voice_section_parses_every_field() -> None:
    persona = parse_persona(_VOICE_PERSONA)

    assert persona is not None
    assert persona.voice.enabled
    assert persona.voice.voices == {"grok-voice": "ara", "openai-realtime": "marin"}
    assert persona.voice.style == "Short sentences."
    assert persona.voice.tools == ["web_search"]
    assert persona.voice.grok == {"reasoning": {"effort": "high"}}


def test_persona_without_voice_takes_no_calls() -> None:
    persona = parse_persona("name: quiet\nsystem_prompt_template: hi\n")

    assert persona is not None
    assert not persona.voice.enabled
    assert "voice" not in persona.to_dict()


def test_voice_section_round_trips_through_to_dict_and_yaml() -> None:
    persona = parse_persona(_VOICE_PERSONA)
    assert persona is not None

    again = parse_persona(persona_to_yaml(persona))

    assert again is not None
    assert again.voice == persona.voice


@pytest.mark.parametrize(
    ("voice", "message"),
    [
        ("[ara]", "must be a mapping"),
        ("{style: x}", "voice.voices"),
        ("{voices: {grok-voice: ''}}", "empty providers or voices"),
        ("{voices: {grok-voice: ara}, tools: web_search}", "voice.tools"),
        ("{voices: {grok-voice: ara}, grok: [high]}", "voice.grok"),
    ],
)
def test_malformed_voice_section_raises(voice: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_persona(f"name: speaker\nvoice: {voice}\n")


def test_portable_document_carries_the_voice_section() -> None:
    persona = PersonaConfig(
        name="speaker",
        system_prompt_template="You speak.",
        voice=PersonaVoiceConfig(voices={"grok-voice": "ara"}, tools=["web_search"]),
    )

    document = portable_persona_from_config(persona)
    parsed = parse_portable_persona(portable_persona_yaml(document))

    assert parsed.definition["voice"] == {"voices": {"grok-voice": "ara"}, "tools": ["web_search"]}


@pytest.mark.parametrize(
    ("voice", "message"),
    [
        ({"voices": {}}, "voice.voices"),
        ({"voices": {"grok-voice": "ara"}, "accent": "x"}, "unsupported field"),
        ({"voices": {"grok-voice": 3}}, "non-empty strings"),
        ({"voices": {"grok-voice": "ara"}, "grok": "high"}, "voice.grok"),
        ("ara", "must be a mapping"),
    ],
)
def test_portable_document_rejects_malformed_voice(voice: object, message: str) -> None:
    persona = PersonaConfig(name="speaker", system_prompt_template="You speak.")
    document = portable_persona_from_config(persona)
    raw = yaml.safe_load(portable_persona_yaml(document))
    raw["definition"]["voice"] = voice

    with pytest.raises(PersonaDocumentError, match=message):
        parse_portable_persona(yaml.safe_dump(raw))
