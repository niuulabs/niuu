"""The persona contract and the close how-to, as Ting and Skuld both read them."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

import personas
from niuu.domain.workflow_step import (
    PersonaContract,
    PersonaPin,
    StepContract,
    persona_contract_of,
    render_close_howto,
)
from ting.domain import step_graph, step_instructions

_BUILTIN = Path(personas.__file__).parent / "builtin"
_BUNDLED = sorted(path.stem for path in _BUILTIN.glob("*.yaml"))


def _document(name: str) -> dict[str, Any]:
    return yaml.safe_load((_BUILTIN / f"{name}.yaml").read_text())


def _legacy_persona_contract_of(definition: Mapping[str, Any]) -> dict[str, Any]:
    """``ting.domain.step_graph.persona_contract_of`` as it was before it moved, field for field."""
    body = definition.get("definition")
    persona = body if isinstance(body, Mapping) else definition
    produces = persona.get("produces")
    produces = produces if isinstance(produces, Mapping) else {}
    consumes = persona.get("consumes")
    consumes = consumes if isinstance(consumes, Mapping) else {}
    raw_map = produces.get("event_type_map")
    event_map = (
        {str(verdict): str(event) for verdict, event in raw_map.items()}
        if isinstance(raw_map, Mapping)
        else {}
    )
    raw_consumes = consumes.get("event_types")
    raw_schema = produces.get("schema")
    return {
        "produces_map": event_map,
        "produces_event": str(produces.get("event_type") or ""),
        "consumes": tuple(str(item) for item in raw_consumes)
        if isinstance(raw_consumes, list)
        else (),
        "outcome_schema": dict(raw_schema)
        if isinstance(raw_schema, Mapping) and raw_schema
        else None,
    }


def _fields(contract: PersonaContract) -> dict[str, Any]:
    return {
        "produces_map": dict(contract.produces_map),
        "produces_event": contract.produces_event,
        "consumes": contract.consumes,
        "outcome_schema": contract.outcome_schema,
    }


def test_ting_keeps_importing_the_moved_names_from_where_it_always_did() -> None:
    assert step_graph.persona_contract_of is persona_contract_of
    assert step_graph.PersonaContract is PersonaContract


@pytest.mark.parametrize("name", _BUNDLED)
def test_every_bundled_persona_derives_the_contract_it_derived_before_the_move(name: str) -> None:
    document = _document(name)
    expected = _legacy_persona_contract_of(document)

    assert _fields(persona_contract_of(document)) == expected
    # The portable form wraps the persona in ``definition``; it reads the same.
    assert _fields(persona_contract_of({"definition": document})) == expected


def test_there_are_bundled_personas_to_compare() -> None:
    assert {"coder", "reviewer", "security-auditor"} <= set(_BUNDLED)


def test_coder_contract() -> None:
    contract = persona_contract_of(_document("coder"))

    assert dict(contract.produces_map) == {
        "pass": "code.changed",
        "blocked": "code.blocked",
        "help_needed": "code.blocked",
        "fail": "code.blocked",
    }
    assert contract.produces_event == "code.completed"
    assert contract.consumes == (
        "review.changes_requested",
        "security.changes_requested",
        "code.requested",
        "bug.fix.requested",
        "feature.requested",
    )
    assert contract.valid_verdicts == ("blocked", "fail", "help_needed", "pass")
    assert contract.outcome_schema is not None
    assert "files_changed" in contract.outcome_schema


def test_reviewer_contract() -> None:
    contract = persona_contract_of(_document("reviewer"))

    assert dict(contract.produces_map) == {
        "pass": "review.passed",
        "needs_changes": "review.changes_requested",
        "fail": "review.changes_requested",
    }
    assert contract.produces_event == "review.completed"
    assert contract.consumes == ("code.changed", "review.requested")
    assert contract.valid_verdicts == ("fail", "needs_changes", "pass")
    assert contract.produced_events == {"review.passed", "review.changes_requested"}
    assert contract.event_for("pass") == "review.passed"
    assert contract.event_for("other") is None


def test_security_auditor_contract() -> None:
    contract = persona_contract_of(_document("security-auditor"))

    assert dict(contract.produces_map) == {
        "pass": "security.passed",
        "fail": "security.changes_requested",
        "needs_review": "security.changes_requested",
    }
    assert contract.produces_event == "security.completed"
    assert contract.consumes == ("code.changed",)
    assert contract.valid_verdicts == ("fail", "needs_review", "pass")


def test_a_persona_without_a_verdict_map_hands_off_one_event_whatever_the_verdict() -> None:
    contract = persona_contract_of({"produces": {"event_type": "doc.done"}})

    assert contract.valid_verdicts == ()
    assert contract.event_for("anything") == "doc.done"
    assert contract.produced_events == {"doc.done"}
    assert PersonaContract().event_for("anything") is None
    assert PersonaContract().produced_events == frozenset()


def test_a_document_with_no_contract_reads_as_an_empty_one() -> None:
    contract = persona_contract_of({"name": "bare", "produces": "nonsense", "consumes": []})

    assert _fields(contract) == {
        "produces_map": {},
        "produces_event": "",
        "consumes": (),
        "outcome_schema": None,
    }


@pytest.mark.parametrize("name", _BUNDLED)
def test_the_offered_contract_of_a_bundled_persona_matches_what_ting_activates_with(
    name: str,
) -> None:
    """Ting's engine fills an activation with ``sorted(produces_map)``, the map and the event."""
    contract = persona_contract_of(_document(name))
    pin = PersonaPin(alias=name, id=name, revision="1", digest="sha256:abc")

    offered = StepContract.from_persona(pin, contract)

    assert offered.valid_verdicts == list(sorted(contract.produces_map))
    assert offered.verdict_events == dict(contract.produces_map)
    assert offered.default_event == contract.produces_event
    assert offered.outcome_schema == contract.outcome_schema


_CLOSE_HOWTO = (
    "## Closing the step\n\n"
    "This step's activation id is `0b6ac9e6-0a5e-4b49-9b0e-7a3f4d6f4a10`. It is the "
    "`activation_id` the `workflow_step_close` tool takes; the run id is not it.\n\n"
    "When you are done, call `workflow_step_close` exactly once with that id, filling in your "
    "own verdict, outcome and note:\n\n```json\n{\n"
    '  "activation_id": "0b6ac9e6-0a5e-4b49-9b0e-7a3f4d6f4a10",\n'
    '  "ending": "verdict",\n'
    '  "note": "<what you did, in a sentence or two>",\n'
    '  "outcome": {},\n'
    '  "verdict": "%s"\n'
    "}\n```\n\n"
    "`evidence` (a list of `{kind, ref, summary}`) and `note` are optional on every ending. "
    "If the call is refused, the error says why and nothing was recorded; fix the call and make "
    "it again. Ending your turn without closing the step does not close it: the step stays open "
    "and is flagged. Do not close it by writing the answer in your reply."
)
_ACTIVATION = "0b6ac9e6-0a5e-4b49-9b0e-7a3f4d6f4a10"


def test_close_howto_names_the_steps_activation_id_as_a_fact_not_an_example() -> None:
    """Shown only inside an example, members took the id for a placeholder and passed the
    run id instead: the section states it as this step's id before the call."""
    assert render_close_howto(_ACTIVATION, ("blocked", "implemented")) == _CLOSE_HOWTO % "blocked"
    assert render_close_howto(_ACTIVATION, ()) == _CLOSE_HOWTO % "<verdict>"


def test_ting_offers_the_step_without_the_close_howto_that_each_skuld_writes() -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    from niuu.domain.workflow_step import PersonaPin, StepContract
    from niuu.domain.workflow_step import persona_contract_of as contract_of

    contract = StepContract.from_persona(
        PersonaPin(alias="coder", id="coder", revision="1", digest="sha256:" + "0" * 64),
        contract_of({"produces": {"event_type_map": {"implemented": "code.ready"}}}),
    )

    text = step_instructions.render_offer_instructions(
        workflow_name="Build",
        node_id="implement",
        node_label="Implement",
        run_id=UUID(int=1),
        attempt=1,
        generation=0,
        inputs=(),
        contracts=(contract,),
        deadline_at=datetime(2026, 11, 8, tzinfo=UTC),
    )

    assert "## Closing the step" not in text
    assert "`implemented` produces `code.ready`" in text
    assert step_instructions.CLOSE_TOOL_NAME == "workflow_step_close"
