"""What a ticket coordinator may do is limited by its tools, not only by its wording."""

from __future__ import annotations

from ravn.adapters.personas.loader import FilesystemPersonaAdapter


def _persona():
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "ticket-coordinator"
    )
    assert persona is not None
    return persona


def test_a_coordinator_has_no_tool_that_changes_a_ticket() -> None:
    assert "tracker_issue" not in _persona().allowed_tools


def test_a_coordinator_is_told_to_check_before_speaking_and_never_to_choose() -> None:
    prompt = _persona().system_prompt_template

    assert "call a2a_task get for that run and say only what is true now" in prompt
    assert "Never choose a workflow yourself" in prompt
    assert "never change a ticket's tracker status" in prompt
