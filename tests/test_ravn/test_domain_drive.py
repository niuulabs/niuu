from __future__ import annotations

from ravn.domain.domain_drive import choose_operator_contact, orient_domain_from_mandate
from ravn.domain.operator_contact import OperatorContactKind

KANUCK_MANDATE = (
    "Kanuck Valley Models is my small 3D printing company. "
    "You are its resident Ravn. "
    "Help it become easier to run, more creative, and more successful. "
    "Ask before spending money or operating physical machines."
)


def test_mandate_only_orientation_creates_useful_domain_work() -> None:
    orientation = orient_domain_from_mandate(KANUCK_MANDATE)

    assert "resident domain mandate" in orientation.orientation_summary
    assert orientation.selected_next_action.requires_human is False
    assert (
        orientation.selected_next_action.title
        == "Orient to the domain and map the first useful work"
    )

    work_titles = {item.title for item in orientation.self_authored_work}
    assert "Map what exists in the domain" in work_titles


def test_inventory_is_not_baked_into_python_orientation() -> None:
    assert "inventory" not in KANUCK_MANDATE.casefold()

    orientation = orient_domain_from_mandate(KANUCK_MANDATE)
    assert "inventory" not in repr(orientation.to_dict()).casefold()
    assert "operator described a 3D printing domain" in orientation.hypotheses[0].evidence


def test_orientation_marks_physical_and_spending_boundaries_as_mandate_evidence() -> None:
    orientation = orient_domain_from_mandate(KANUCK_MANDATE)

    assert any("operator gated spending" in gap.evidence for gap in orientation.capability_gaps)
    assert any(
        "operator gated physical operation" in gap.evidence for gap in orientation.capability_gaps
    )


def test_interactive_contact_uses_existing_ask_user_tool_shape() -> None:
    orientation = orient_domain_from_mandate(KANUCK_MANDATE)

    contact = choose_operator_contact(orientation, interactive=True)

    assert contact is not None
    assert contact.kind == OperatorContactKind.ASK_USER
    assert contact.tool_name == "ask_user"
    assert contact.tool_input == {"question": contact.question}
    assert "existing systems, files, or tools" in contact.question


def test_headless_contact_uses_help_needed_outcome_shape() -> None:
    orientation = orient_domain_from_mandate(KANUCK_MANDATE)

    contact = choose_operator_contact(orientation, interactive=False)

    assert contact is not None
    assert contact.kind == OperatorContactKind.HELP_NEEDED
    assert contact.help_needed_outcome["verdict"] == "help_needed"
    assert contact.help_needed_outcome["reason"] == "needs_context"
    assert contact.help_needed_outcome["summary"] == contact.question
    assert contact.help_needed_outcome["context"]["mandate"] == KANUCK_MANDATE
