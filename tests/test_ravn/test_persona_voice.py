"""The persona ``voice:`` section: parsing, round trips and portable documents."""

from __future__ import annotations

import pytest
import yaml

from ravn.adapters.personas.loader import (
    FilesystemPersonaAdapter,
    PersonaConfig,
    PersonaVoiceConfig,
)
from ravn.domain.persona_document import (
    PersonaDocumentError,
    parse_portable_persona,
    portable_persona_from_config,
    portable_persona_yaml,
)

_VOICE_PERSONA = """
name: speaker
system_prompt_template: You speak.
allowed_tools: [web]
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
    persona = FilesystemPersonaAdapter.parse(_VOICE_PERSONA)

    assert persona is not None
    assert persona.voice.enabled
    assert persona.voice.voices == {"grok-voice": "ara", "openai-realtime": "marin"}
    assert persona.voice.style == "Short sentences."
    assert persona.voice.tools == ["web_search"]
    assert persona.voice.grok == {"reasoning": {"effort": "high"}}


def test_persona_without_voice_takes_no_calls() -> None:
    persona = FilesystemPersonaAdapter.parse("name: quiet\nsystem_prompt_template: hi\n")

    assert persona is not None
    assert not persona.voice.enabled
    assert "voice" not in persona.to_dict()


def test_voice_section_round_trips_through_to_dict_and_yaml() -> None:
    persona = FilesystemPersonaAdapter.parse(_VOICE_PERSONA)
    assert persona is not None

    again = FilesystemPersonaAdapter.parse(FilesystemPersonaAdapter.to_yaml(persona))

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
        FilesystemPersonaAdapter.parse(f"name: speaker\nvoice: {voice}\n")


def test_merge_keeps_the_persona_voice() -> None:
    persona = FilesystemPersonaAdapter.parse(_VOICE_PERSONA)
    assert persona is not None

    from ravn.config import ProjectConfig

    merged = FilesystemPersonaAdapter.merge(persona, ProjectConfig())

    assert merged.voice == persona.voice


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


def test_sidekick_persona_offers_voice_with_only_allowed_tools() -> None:
    from ravn.cli.tool_builders import _expand_allowed_tools

    persona = FilesystemPersonaAdapter().load("sidekick")

    assert persona is not None
    assert persona.voice.voices == {"grok-voice": "ara", "openai-realtime": "marin"}
    allowed = _expand_allowed_tools(set(persona.allowed_tools))
    assert set(persona.voice.tools) <= allowed
