from __future__ import annotations

from ravn.adapters.personas.loader import FilesystemPersonaAdapter


def test_domain_drive_persona_is_available_and_uses_existing_human_input_paths() -> None:
    persona = FilesystemPersonaAdapter().load("domain-drive")

    assert persona is not None
    assert persona.stop_on_outcome is True
    assert "ask_user" in persona.allowed_tools
    assert "ask_user" not in persona.forbidden_tools
    assert persona.produces.event_type == "domain_drive.oriented"
    assert persona.produces.event_type_map["help_needed"] == "domain_drive.human_input.requested"
    assert persona.produces.schema["verdict"].enum_values == [
        "oriented",
        "help_needed",
        "blocked",
    ]
    assert "Do not require the operator to give a task" in persona.system_prompt_template
    assert "verdict: help_needed" in persona.system_prompt_template
