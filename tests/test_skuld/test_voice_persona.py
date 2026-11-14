"""Who speaks on a session's voice call, and what the voice model is told."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfoNotFoundError

import pytest

from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from personas.domain.persona_document import PersonaDocumentError
from skuld.voice.persona import (
    PROMPT_VERSION,
    UnsupportedProviderError,
    VoicePersona,
    VoicePersonaError,
    VoicePromptComposer,
    normalize_provider,
    resolve_voice_persona,
)
from skuld.voice.session_binding import CARD_TURN_KNOB, session_mode_rules
from skuld.voice.session_port import SessionTurn
from tests.test_skuld.voice_support import MODELS, make_composer, make_status, persona_document


@pytest.fixture(autouse=True)
def _fresh_cap_counts():
    reset_cap_hits()
    yield
    reset_cap_hits()


class TestResolveVoicePersona:
    def test_the_session_persona_with_a_voice_wins(self):
        session = persona_document("alice", voices={"openai-realtime": "coral"})
        default = persona_document("sidekick")

        persona = resolve_voice_persona(session, default)

        assert persona.name == "alice"
        assert persona.voices == {"openai-realtime": "coral"}

    def test_falls_back_to_the_default_when_the_session_persona_has_no_voice(self):
        session = persona_document("coder", with_voice=False)

        persona = resolve_voice_persona(session, persona_document("sidekick"))

        assert persona.name == "sidekick"

    def test_falls_back_to_the_default_when_the_session_plays_no_persona(self):
        persona = resolve_voice_persona(None, persona_document("sidekick"))

        assert persona.name == "sidekick"
        assert persona.display_name == "Sidekick"
        assert persona.description == "a helpful companion"
        assert persona.style == "Warm and brisk."

    def test_a_default_without_a_voice_section_is_an_error(self):
        with pytest.raises(VoicePersonaError, match="has no voice: section"):
            resolve_voice_persona(None, persona_document("plain", with_voice=False))

    def test_neither_persona_with_a_voice_is_an_error_naming_the_remedy(self):
        with pytest.raises(VoicePersonaError, match="SKULD__VOICE__ENABLED=false"):
            resolve_voice_persona(persona_document("coder", with_voice=False), None)

    def test_no_persona_at_all_is_an_error(self):
        with pytest.raises(VoicePersonaError, match="voice.default_persona"):
            resolve_voice_persona(None, None)

    def test_a_malformed_document_raises_instead_of_being_skipped(self):
        with pytest.raises(PersonaDocumentError, match="definition must be a mapping"):
            resolve_voice_persona(None, {"schema_version": 1, "id": "x", "definition": "oops"})

    def test_a_malformed_session_persona_raises_even_with_a_good_default(self):
        broken = {"schema_version": 1, "id": "x", "revision": "r", "definition": 3}

        with pytest.raises(PersonaDocumentError):
            resolve_voice_persona(broken, persona_document())

    def test_the_display_name_overrides_the_persona_name(self):
        persona = resolve_voice_persona(None, persona_document("sidekick"), display_name="Max")

        assert persona.display_name == "Max"
        assert persona.name == "sidekick"

    def test_grok_settings_are_carried(self):
        document = persona_document(grok={"turn_detection": {"type": "server_vad"}})

        persona = resolve_voice_persona(None, document)

        assert persona.grok_session == {"turn_detection": {"type": "server_vad"}}


class TestVoicePersona:
    def _persona(self, **fields) -> VoicePersona:
        values = {
            "name": "sidekick",
            "display_name": "Sidekick",
            "description": "",
            "style": "",
            "voices": {"openai-realtime": "marin"},
            "grok_session": {},
        }
        values.update(fields)
        return VoicePersona(**values)

    def test_voice_for_a_missing_key_raises_naming_the_remedy(self):
        with pytest.raises(UnsupportedProviderError, match="voice.voices"):
            self._persona().voice_for("grok")

    def test_voice_for_returns_the_configured_voice(self):
        assert self._persona().voice_for("gpt-realtime") == "marin"

    def test_default_voice_prefers_openai_then_the_first(self):
        assert self._persona().default_voice() == "marin"
        assert self._persona(voices={"grok-voice": "ara"}).default_voice() == "ara"

    def test_context_prompt_with_description_and_style(self):
        persona = self._persona(description=" a companion ", style=" Brisk. ")

        assert persona.context_prompt() == (
            "You are Sidekick. a companion\n\n## How you sound\n\nBrisk."
        )

    def test_context_prompt_without_description_or_style(self):
        assert self._persona().context_prompt() == "You are Sidekick."


class TestNormalizeProvider:
    @pytest.mark.parametrize("raw", ["grok", "xai", "GROK-VOICE", " xai "])
    def test_grok_spellings(self, raw):
        assert normalize_provider(raw) == "grok"

    @pytest.mark.parametrize(
        "raw", ["gpt-realtime", "gpt", "openai", "realtime", "openai-realtime"]
    )
    def test_realtime_spellings(self, raw):
        assert normalize_provider(raw) == "gpt-realtime"

    def test_an_unknown_provider_raises(self):
        with pytest.raises(UnsupportedProviderError, match="gemini"):
            normalize_provider("gemini")


class TestComposer:
    def test_missing_models_raises_naming_them(self):
        persona = resolve_voice_persona(None, persona_document())

        with pytest.raises(ValueError, match=r"voice.models is missing \['gpt-realtime'\]"):
            VoicePromptComposer(
                persona,
                models={"grok": "g"},
                time_zone="UTC",
            )

    def test_an_unknown_time_zone_raises(self):
        with pytest.raises(ZoneInfoNotFoundError, match="Nowhere/Land"):
            make_composer(time_zone="Nowhere/Land")

    def test_model_for_and_persona(self):
        composer = make_composer()

        assert composer.model_for("grok") == MODELS["grok"]
        assert composer.persona.name == "sidekick"


class TestCard:
    def test_shows_state_questions_description_and_turns(self):
        composer = make_composer()
        status = make_status(
            activity_state="awaiting_input",
            questions=("Which database?",),
            description="Moves billing to the new ledger",
        )
        turns = [
            SessionTurn("user", "fix the tests", author="Ada"),
            SessionTurn("assistant", "on it"),
            SessionTurn("user", "thanks"),
        ]

        card = composer.card(status, turns)

        assert "Session: Refactor billing (harness claude, model claude-sonnet)" in card
        assert "State: awaiting input, waiting for the person to answer" in card
        assert "About: Moves billing to the new ledger" in card
        assert "Asking the person: Which database?" in card
        assert "Turns:" in card
        assert "- Ada: fix the tests" in card
        assert "- agent: on it" in card
        assert "- person: thanks" in card

    def test_busy_state_reads_working_with_since_time(self):
        since = datetime(2026, 11, 14, 9, 30, tzinfo=UTC).timestamp()
        status = make_status(activity_state="active", busy=True, state_since=since)

        card = make_composer().card(status, [])

        assert "State: working since 09:30 UTC" in card

    def test_no_turns(self):
        card = make_composer().card(make_status(), [])

        assert card.endswith("No turns yet.")
        assert "Turns:" not in card

    def test_says_how_many_earlier_turns_are_left_out(self):
        composer = make_composer(card_recent_turns=2)
        turns = [SessionTurn("user", f"turn {n}") for n in range(5)]

        card = composer.card(make_status(), turns)

        assert "Latest 2 of 5 turns (3 earlier turns are not on this card" in card
        assert "turn 4" in card and "turn 3" in card
        assert "turn 2" not in card

    def test_a_long_turn_is_cut_with_a_marker_and_counted(self):
        composer = make_composer(card_turn_max_chars=10)

        card = composer.card(make_status(), [SessionTurn("assistant", "x" * 25)])

        assert "- agent: " + "x" * 10 + " […15 more characters;" in card
        assert "x" * 11 not in card
        assert cap_hit_counts() == {CARD_TURN_KNOB: 1}

    def test_a_turn_that_fits_is_not_counted(self):
        composer = make_composer(card_turn_max_chars=10)

        composer.card(make_status(), [SessionTurn("assistant", "x" * 10)])

        assert cap_hit_counts() == {}


class TestPrompt:
    def _turns(self):
        return [SessionTurn("user", "hello"), SessionTurn("assistant", "hi")]

    def test_grok_prompt_merges_session_settings_and_searches_natively(self):
        persona = persona_document(grok={"turn_detection": {"type": "server_vad"}})
        composer = make_composer(persona=persona, tool_names=["b_tool", "a_tool"])

        prompt = composer.prompt(
            "xai", make_status(), self._turns(), surface="ios", conversation_id="conv-1"
        )

        assert prompt["provider"] == "grok"
        assert prompt["persona"] == "sidekick"
        assert prompt["displayName"] == "Sidekick"
        assert prompt["surface"] == "ios"
        assert prompt["conversationId"] == "conv-1"
        assert prompt["session"] == {
            "model": MODELS["grok"],
            "voice": "ara",
            "turn_detection": {"type": "server_vad"},
        }
        assert prompt["webSearch"] == "native"
        assert prompt["providerTools"] == [{"type": "web_search"}]
        assert "conv-1" in prompt["instructions"]
        assert datetime.fromisoformat(prompt["generatedAt"])

    def test_gpt_realtime_prompt_has_no_native_search_or_grok_settings(self):
        persona = persona_document(grok={"turn_detection": {"type": "server_vad"}})
        composer = make_composer(persona=persona)

        prompt = composer.prompt(
            "openai", make_status(), self._turns(), surface="web", conversation_id="c"
        )

        assert prompt["provider"] == "gpt-realtime"
        assert prompt["session"] == {"model": MODELS["gpt-realtime"], "voice": "marin"}
        assert "webSearch" not in prompt
        assert "providerTools" not in prompt

    def test_fingerprint_fields(self):
        composer = make_composer(tool_names=["b_tool", "a_tool"])

        fingerprint = composer.prompt(
            "grok", make_status(), [], surface="mac", conversation_id="c"
        )["fingerprint"]

        assert fingerprint["version"] == PROMPT_VERSION
        assert fingerprint["persona"] == "sidekick"
        assert fingerprint["provider"] == "grok"
        assert fingerprint["surface"] == "mac"
        assert fingerprint["toolProfile"] == "voice"
        assert fingerprint["toolCount"] == 2
        assert len(fingerprint["stackSha"]) == 12
        assert len(fingerprint["toolsSha"]) == 12

    def test_the_tools_fingerprint_ignores_order_and_the_stack_one_follows_the_provider(self):
        a = make_composer(tool_names=["a", "b"])
        b = make_composer(tool_names=["b", "a"])

        first = a.prompt("grok", make_status(), [], surface="ios", conversation_id="c")
        second = b.prompt("grok", make_status(), [], surface="ios", conversation_id="c")
        other = a.prompt("gpt", make_status(), [], surface="ios", conversation_id="c")

        assert first["fingerprint"]["toolsSha"] == second["fingerprint"]["toolsSha"]
        assert first["fingerprint"]["stackSha"] != other["fingerprint"]["stackSha"]

    def test_an_unsupported_provider_raises(self):
        with pytest.raises(UnsupportedProviderError):
            make_composer().prompt("gemini", make_status(), [], surface="ios", conversation_id="c")

    def test_a_persona_without_the_providers_voice_raises(self):
        persona = persona_document(voices={"openai-realtime": "marin"})

        with pytest.raises(UnsupportedProviderError, match="grok-voice"):
            make_composer(persona=persona).prompt(
                "grok", make_status(), [], surface="ios", conversation_id="c"
            )

    def test_instructions_name_the_listener_surface_zone_and_session(self):
        composer = make_composer(time_zone="Europe/Brussels")

        text = composer.instructions(
            make_status(), self._turns(), surface="ios", conversation_id="conv-9"
        )

        assert text.startswith("You are Sidekick, speaking with the owner of the Forge session")
        assert "«Refactor billing»" in text
        assert "their iPhone" in text
        assert "(Europe/Brussels)" in text
        assert "conv-9" in text
        assert "## SESSION CARD" in text
        assert "## This call" in text

    def test_an_unknown_surface_reads_their_device(self):
        text = make_composer().instructions(make_status(), [], surface="watch", conversation_id="c")

        assert "from their device" in text

    def test_standing_instructions_have_no_per_call_preamble(self):
        text = make_composer().standing_instructions(make_status(), [])

        assert text.startswith("You are Sidekick.")
        assert "conversation's id" not in text


def test_session_mode_rules_name_the_session_and_its_tools():
    rules = session_mode_rules("Refactor billing")

    assert "«Refactor billing»" in rules
    for tool in ("send_to_this_session", "this_session_status", "read_this_session_in_full"):
        assert tool in rules
