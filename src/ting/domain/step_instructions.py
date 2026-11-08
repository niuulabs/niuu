"""The text a harness session is given when a workflow step is activated.

Ting owns the words of a step activation: which workflow and step this is, who the
member is, what the step consumed (the payloads of the outcomes that led here,
verdicts and notes included), the endings and verdicts the step may close with, the
shape of the outcome, the deadline, and the exact way to close the step with the
``workflow_step_close`` tool that Skuld offers while a step is open.

``render_step_instructions`` is a pure function of the activation. Skuld injects the
result as a user turn and interprets none of it; Ting never reads an answer back out
of the transcript, only out of the close call.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ting.ports.workflow_steps import DeliveredEvent, StepActivation

CLOSE_TOOL_NAME = "workflow_step_close"

_ENDING_MEANINGS = {
    "verdict": "hand the work on: say which verdict describes how the step went",
    "waiting": "park the step until something outside you resolves (it stays yours)",
    "done": "finish without handing anything on",
    "failed": "give up on this step and say why",
}


def render_step_instructions(activation: StepActivation) -> str:
    """The instructions for one activation, as the member's session receives them."""
    sections = [
        _header(activation),
        _inputs(activation.inputs),
        _endings(activation),
        _outcome_schema(activation.outcome_schema),
        _deadline(activation),
        _closing(activation),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _header(activation: StepActivation) -> str:
    workflow = f'workflow "{activation.workflow_name}"' if activation.workflow_name else "workflow"
    lines = [
        f"# Workflow step: {activation.node_label}",
        "",
        f'You are `{activation.member}` in the step "{activation.node_label}" '
        f"(node `{activation.node_id}`) of the {workflow}. "
        f"Run `{activation.run_id}`, activation `{activation.activation_id}`.",
    ]
    if activation.attempt > 1:
        lines.append(f"This is attempt {activation.attempt} of this step.")
    if activation.generation > 0:
        lines.append(
            f"The run is on rework generation {activation.generation}: earlier work was sent "
            "back, and the inputs below carry what to change."
        )
    lines.append(
        "Do the work this step asks of your role, using your own judgment and tools. "
        "You hold this step until you close it."
    )
    return "\n".join(lines)


def _inputs(inputs: tuple[DeliveredEvent, ...]) -> str:
    if not inputs:
        return "## Inputs\n\nThis step consumed no earlier outcome."
    blocks = ["## Inputs", "", "What this step consumed, oldest first:"]
    for item in inputs:
        blocks.append("")
        blocks.append(_input_block(item))
    return "\n".join(blocks)


def _input_block(item: DeliveredEvent) -> str:
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
        lines.append(_json_block(outcome))
    evidence = payload.get("evidence")
    if evidence:
        lines.append("- evidence:")
        lines.append(_json_block(evidence))
    rest = {
        key: value
        for key, value in payload.items()
        if key not in _PAYLOAD_KEYS_SHOWN and value not in (None, "", {}, [])
    }
    if rest:
        lines.append("- other fields:")
        lines.append(_json_block(rest))
    return "\n".join(lines)


_PAYLOAD_KEYS_SHOWN = frozenset(
    {"node_id", "member", "verdict", "outcome", "note", "evidence", "attempt", "activation_id"}
)


def _endings(activation: StepActivation) -> str:
    lines = ["## How this step may end", ""]
    for ending in activation.valid_endings:
        meaning = _ENDING_MEANINGS.get(ending, "")
        lines.append(f"- `{ending}`: {meaning}" if meaning else f"- `{ending}`")
    if "verdict" in activation.valid_endings:
        lines.append("")
        lines.append(_verdicts(activation))
    if "waiting" in activation.valid_endings:
        lines.append("")
        lines.append(
            "A `waiting` ending needs `waiting_on` with a `kind` (`person`, `session`, "
            "`event`, `time` or `agent`) and a `ref` naming what you wait for; close the "
            "step again, with its real ending, once that resolves."
        )
    if "failed" in activation.valid_endings:
        lines.append("")
        lines.append("A `failed` ending needs `failure_reason`.")
    return "\n".join(lines)


def _verdicts(activation: StepActivation) -> str:
    if activation.verdict_events:
        lines = ["A `verdict` ending needs `verdict`, one of these (each hands off its event):"]
        for verdict in activation.valid_verdicts:
            event = activation.verdict_events.get(verdict, "")
            lines.append(f"- `{verdict}`" + (f" produces `{event}`" if event else ""))
        return "\n".join(lines)
    if activation.default_event:
        return (
            "A `verdict` ending needs `verdict`, a short word saying how the step went. "
            f"Whatever you answer hands off `{activation.default_event}`."
        )
    return "A `verdict` ending needs `verdict`, a short word saying how the step went."


def _outcome_schema(schema: Mapping[str, Any] | None) -> str:
    if not schema:
        return ""
    return (
        "## Outcome\n\nGive `outcome` as an object with these fields, as your role defines "
        "them:\n\n" + _json_block(schema)
    )


def _deadline(activation: StepActivation) -> str:
    if activation.deadline_at is None:
        return ""
    return (
        f"## Deadline\n\nClose this step by {activation.deadline_at.isoformat()}. A step that "
        "is still open then is flagged for an operator."
    )


def _closing(activation: StepActivation) -> str:
    call = {
        "activation_id": str(activation.activation_id),
        "ending": "verdict",
        "verdict": activation.valid_verdicts[0] if activation.valid_verdicts else "<verdict>",
        "outcome": {},
        "note": "<what you did, in a sentence or two>",
    }
    return (
        "## Closing the step\n\n"
        f"When you are done, call the `{CLOSE_TOOL_NAME}` tool exactly once, with this "
        "activation's id. For example:\n\n"
        f"{_json_block(call)}\n\n"
        "`evidence` (a list of `{kind, ref, summary}`) and `note` are optional on every ending. "
        "If the call is refused, the error says why and nothing was recorded; fix the call "
        "and make it again. Ending your turn without closing the step does not close it: "
        "the step stays open and is flagged. Do not close it by writing the answer in "
        "your reply."
    )


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, sort_keys=True, default=str) + "\n```"
