"""A persona projected onto the harness a model's session would run, as Skuld does at start."""

from __future__ import annotations

from typing import Any

from niuu.config_models import SessionDefinitionConfig
from personas.domain.persona_document import PortablePersonaDefinition
from volundr.adapters.outbound.persona_playability import SkuldPersonaPlayability

CLAUDE = "skuld.transports.sdk.SDKTransport"
MUSE = "skuld.transports.muse.MuseMSPTransport"


def _persona(**definition: Any) -> dict[str, Any]:
    body = {"name": "auditor", "system_prompt_template": "You audit.", **definition}
    return PortablePersonaDefinition(
        id="auditor", revision="content-0123456789abcdef", definition=body
    ).to_dict()


def _checker(**definitions: SessionDefinitionConfig) -> SkuldPersonaPlayability:
    catalog = {"claude-opus-5-5": "skuldClaude", "muse-1": "skuldMuse"}
    return SkuldPersonaPlayability(
        definitions=definitions,
        default_definition="",
        resolve_definition=catalog.get,
    )


def _definition(transport: str, **broker: Any) -> SessionDefinitionConfig:
    return SessionDefinitionConfig(defaults={"broker": {"transportAdapter": transport, **broker}})


def test_a_persona_the_harness_can_play_is_playable_on_its_models_transport() -> None:
    verdict = _checker(skuldClaude=_definition(CLAUDE)).check("claude-opus-5-5", _persona())

    assert verdict.playable
    assert (verdict.definition, verdict.transport) == ("skuldClaude", CLAUDE)


def test_the_harness_refusal_is_the_reason_skuld_would_give() -> None:
    verdict = _checker(skuldClaude=_definition(CLAUDE)).check(
        "claude-opus-5-5", _persona(permission_mode="prompt")
    )

    assert not verdict.playable
    assert any("permission_mode:prompt" in reason for reason in verdict.reasons)


def test_a_harness_that_takes_no_instructions_refuses_a_persona_with_them() -> None:
    verdict = _checker(skuldMuse=_definition(MUSE)).check("muse-1", _persona())

    assert not verdict.playable
    assert verdict.transport == MUSE


def test_an_explicit_definition_wins_over_the_models_catalog_entry() -> None:
    checker = _checker(skuldClaude=_definition(CLAUDE), skuldMuse=_definition(MUSE))

    verdict = checker.check("claude-opus-5-5", _persona(), definition="skuldMuse")

    assert verdict.transport == MUSE


def test_a_model_with_no_session_definition_is_refused_by_name() -> None:
    verdict = _checker().check("unknown-model", _persona())

    assert not verdict.playable
    assert "unknown-model" in verdict.reasons[0]
    assert "default_definition" in verdict.reasons[0]


def test_a_disabled_definition_is_refused() -> None:
    disabled = SessionDefinitionConfig(enabled=False, defaults={})

    verdict = _checker(skuldClaude=disabled).check("claude-opus-5-5", _persona())

    assert verdict.reasons == (
        "session definition 'skuldClaude' of model 'claude-opus-5-5' is not enabled",
    )


def test_a_persona_document_that_does_not_parse_is_refused_with_why() -> None:
    document = {**_persona(), "digest": "sha256:" + "0" * 64}

    verdict = _checker(skuldClaude=_definition(CLAUDE)).check("claude-opus-5-5", document)

    assert not verdict.playable
    assert "digest" in verdict.reasons[0]
