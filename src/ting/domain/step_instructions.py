"""The text an offered workflow step carries to the members that take it up.

Ting owns the words of a step: which workflow and step this is, what the step consumed (the
payloads of the outcomes that led here, verdicts and notes included), what each role may end
it with, the shape of the outcome and the deadline. The text goes out once, in the offer, to
every member that takes the node's work, so it names no member and no activation: a member's
Skuld mints its own activation when it picks the offer up and appends the exact way to close
it with the ``workflow_step_close`` tool (``niuu.domain.workflow_step.render_close_howto``).

``render_offer_instructions`` is a pure function of the offer's parts. Skuld injects the
result as a user turn and interprets none of it; Ting never reads an answer back out of the
transcript, only out of the close call.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from niuu.domain.workflow_step import (
    WORKFLOW_STEP_CLOSE_TOOL,
    OfferInput,
    StepContract,
    json_block,
)

CLOSE_TOOL_NAME = WORKFLOW_STEP_CLOSE_TOOL

_ENDING_MEANINGS = {
    "verdict": "hand the work on: say which verdict describes how the step went",
    "waiting": "park the step until something outside you resolves (it stays yours)",
    "done": "finish without handing anything on",
    "failed": "give up on this step and say why",
}


def render_offer_instructions(
    *,
    workflow_name: str,
    node_id: str,
    node_label: str,
    run_id: UUID,
    attempt: int,
    generation: int,
    inputs: Sequence[OfferInput],
    contracts: Sequence[StepContract],
    deadline_at: datetime | None,
) -> str:
    """The instructions of one offer, as the members that take it up receive them."""
    sections = [
        _header(workflow_name, node_id, node_label, run_id, attempt, generation),
        _inputs(inputs),
        _endings(contracts),
        _deadline(deadline_at),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _header(
    workflow_name: str,
    node_id: str,
    node_label: str,
    run_id: UUID,
    attempt: int,
    generation: int,
) -> str:
    workflow = f'workflow "{workflow_name}"' if workflow_name else "workflow"
    lines = [
        f"# Workflow step: {node_label}",
        "",
        f'You are in the step "{node_label}" (node `{node_id}`) of the {workflow}. Run `{run_id}`.',
    ]
    if attempt > 1:
        lines.append(f"This is attempt {attempt} of this step.")
    if generation > 0:
        lines.append(
            f"The run is on rework generation {generation}: earlier work was sent "
            "back, and the inputs below carry what to change."
        )
    lines.append(
        "Do the work this step asks of your role, using your own judgment and tools. "
        "You hold this step until you close it."
    )
    return "\n".join(lines)


def _inputs(inputs: Sequence[OfferInput]) -> str:
    if not inputs:
        return "## Inputs\n\nThis step consumed no earlier outcome."
    blocks = ["## Inputs", "", "What this step consumed, oldest first:"]
    for item in inputs:
        blocks.append("")
        blocks.append(_input_block(item))
    return "\n".join(blocks)


def _input_block(item: OfferInput) -> str:
    source = item.node_id
    if item.member:
        source = f"{item.node_id} / {item.member}"
    payload = item.payload
    lines = [f"### `{item.event_type}` from {source}"]
    verdict = str(payload.get("verdict") or "").strip()
    if verdict:
        lines.append(f"- verdict: `{verdict}`")
    note = str(payload.get("note") or "").strip()
    if note:
        lines.append(f"- note: {note}")
    outcome = payload.get("outcome")
    if isinstance(outcome, Mapping) and outcome:
        lines.append("- outcome:")
        lines.append(json_block(outcome))
    evidence = payload.get("evidence")
    if evidence:
        lines.append("- evidence:")
        lines.append(json_block(evidence))
    rest = {
        key: value
        for key, value in payload.items()
        if key not in _PAYLOAD_KEYS_SHOWN and value not in (None, "", {}, [])
    }
    if rest:
        lines.append("- other fields:")
        lines.append(json_block(rest))
    return "\n".join(lines)


_PAYLOAD_KEYS_SHOWN = frozenset(
    {
        "node_id",
        "member",
        "verdict",
        "outcome",
        "note",
        "evidence",
        "attempt",
        "activation_id",
        "offer_id",
    }
)


def _endings(contracts: Sequence[StepContract]) -> str:
    """How the step may end, once for roles that agree and per role where they differ."""
    shared = len({_shape(contract) for contract in contracts}) == 1
    if shared:
        return "\n\n".join(
            part for part in (_endings_of(contracts[0]), _outcome_schema(contracts[0])) if part
        )
    sections = ["## How this step may end", "", "It depends on your role:"]
    for contract in contracts:
        sections.append("")
        sections.append(f"### As `{contract.persona.alias}`")
        sections.append("")
        sections.append(_endings_of(contract, heading=False))
        schema = _outcome_schema(contract, heading=False)
        if schema:
            sections.append("")
            sections.append(schema)
    return "\n".join(sections)


def _shape(contract: StepContract) -> str:
    return json_block(
        {
            "endings": sorted(contract.valid_endings),
            "verdicts": contract.verdict_events or sorted(contract.valid_verdicts),
            "default_event": contract.default_event,
            "schema": contract.outcome_schema,
        }
    )


def _endings_of(contract: StepContract, *, heading: bool = True) -> str:
    lines = ["## How this step may end", ""] if heading else []
    for ending in contract.valid_endings:
        meaning = _ENDING_MEANINGS.get(ending, "")
        lines.append(f"- `{ending}`: {meaning}" if meaning else f"- `{ending}`")
    if "verdict" in contract.valid_endings:
        lines.append("")
        lines.append(_verdicts(contract))
    if "waiting" in contract.valid_endings:
        lines.append("")
        lines.append(
            "A `waiting` ending needs `waiting_on` with a `kind` (`person`, `session`, "
            "`event`, `time` or `agent`) and a `ref` naming what you wait for; close the "
            "step again, with its real ending, once that resolves."
        )
    if "failed" in contract.valid_endings:
        lines.append("")
        lines.append("A `failed` ending needs `failure_reason`.")
    return "\n".join(lines)


def _verdicts(contract: StepContract) -> str:
    if contract.verdict_events:
        lines = ["A `verdict` ending needs `verdict`, one of these (each hands off its event):"]
        for verdict in contract.valid_verdicts:
            event = contract.verdict_events.get(verdict, "")
            lines.append(f"- `{verdict}`" + (f" produces `{event}`" if event else ""))
        return "\n".join(lines)
    if contract.default_event:
        return (
            "A `verdict` ending needs `verdict`, a short word saying how the step went. "
            f"Whatever you answer hands off `{contract.default_event}`."
        )
    return "A `verdict` ending needs `verdict`, a short word saying how the step went."


def _outcome_schema(contract: StepContract, *, heading: bool = True) -> str:
    schema: Mapping[str, Any] | None = contract.outcome_schema
    if not schema:
        return ""
    intro = "Give `outcome` as an object with these fields, as your role defines them:"
    body = intro + "\n\n" + json_block(schema)
    return f"## Outcome\n\n{body}" if heading else body


def _deadline(deadline_at: datetime | None) -> str:
    if deadline_at is None:
        return ""
    return (
        f"## Deadline\n\nClose this step by {deadline_at.isoformat()}. A step that "
        "is still open then is flagged for an operator."
    )
