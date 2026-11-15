"""The typed-fact tools: one definition, served over MCP and to in-process agents.

``mimir.mcp.MimirMcpServer`` lists these for harness sessions and
``mimir.tools.build_mimir_fact_tools`` wraps them as ``ToolPort`` for a Ravn.
Both call :func:`run_fact_tool`, so the two surfaces cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mimir import fact_wire
from mimir.facts import FactService
from niuu.domain.mimir_facts import FactCaller, TrustError

_AS_OF = {
    "type": "string",
    "description": "ISO 8601 time to read the facts as of (default: now).",
}
_MIN_TRUST = {
    "type": "string",
    "enum": ["agent", "tool", "operator", "person"],
    "description": (
        "Lowest trust to return, from agent (anything) up to person (confirmed by a person). "
        "Agent-written facts are other agents' opinions; ask for 'tool' or above when you "
        "need something that was observed or vouched for."
    ),
}
_STRINGS = {"type": "array", "items": {"type": "string"}}


@dataclass(frozen=True)
class FactToolSpec:
    description: str
    schema: dict[str, Any]
    write: bool


FACT_TOOLS: dict[str, FactToolSpec] = {
    "mimir_fact_remember": FactToolSpec(
        "Record one fact about a subject with the evidence it came from. Trust is assigned by "
        "the platform from who you are (an agent's facts are the lowest level) and cannot be "
        "set here. A fact derived from other facts takes the lowest trust among them. To "
        "correct a fact, pass its id in replaces; the old one is retired and kept in history.",
        {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The entity this is about."},
                "statement": {"type": "string", "description": "One claim."},
                "evidence": {
                    **_STRINGS,
                    "description": "What it was learned from: page paths, source ids, a tool run.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["fact", "event", "preference", "commitment", "belief"],
                },
                "valid_from": {"type": "string", "description": "ISO 8601; default: now."},
                "valid_until": {"type": "string", "description": "ISO 8601; default: no expiry."},
                "replaces": {**_STRINGS, "description": "Ids of facts this one corrects."},
                "derived_from": {**_STRINGS, "description": "Ids of facts this is concluded from."},
            },
            "required": ["subject", "statement", "evidence"],
        },
        True,
    ),
    "mimir_fact_recall": FactToolSpec(
        "Recall facts valid at a time and at or above a trust level. Anything left out (too "
        "low in trust, not valid then, or cut by the backend) is listed under omitted with "
        "the reason.",
        {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "contains": {"type": "string", "description": "Only facts containing this text."},
                "min_trust": _MIN_TRUST,
                "as_of": _AS_OF,
            },
        },
        False,
    ),
    "mimir_fact_forget": FactToolSpec(
        "Retire a fact that is no longer true, saying why. It stays in history. You can only "
        "retire facts at or below your own trust level.",
        {
            "type": "object",
            "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
            "required": ["id", "why"],
        },
        True,
    ),
    "mimir_fact_entity": FactToolSpec(
        "Everything known about one entity: current facts, retired and expired history, and "
        "its card.",
        {"type": "object", "properties": {"subject": {"type": "string"}}, "required": ["subject"]},
        False,
    ),
    "mimir_fact_context_pack": FactToolSpec(
        "A ranked set of facts for a task, packed to a token budget. States what was left out "
        "and why (budget, trust, validity, backend cap).",
        {
            "type": "object",
            "properties": {
                "subjects": {**_STRINGS, "description": "Entities to gather facts about."},
                "task": {"type": "string", "description": "What the facts are for; ranks them."},
                "budget_tokens": {"type": "integer", "minimum": 1},
                "min_trust": _MIN_TRUST,
                "as_of": _AS_OF,
            },
            "required": ["subjects"],
        },
        False,
    ),
    "mimir_fact_share": FactToolSpec(
        "Package a fact to send to a peer. It carries its original author, evidence and trust.",
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        False,
    ),
    "mimir_fact_receive": FactToolSpec(
        "Store a fact a peer shared. It stays the original author's, and its trust cannot "
        "exceed yours, because a peer's claimed trust cannot be checked here.",
        {
            "type": "object",
            "properties": {"fact": {"type": "object", "description": "A peer's share result."}},
            "required": ["fact"],
        },
        True,
    ),
}


def _require_caller(caller: FactCaller | None, name: str) -> FactCaller:
    if caller is None:
        raise TrustError(
            f"{name} writes on someone's behalf and the request carries no authenticated "
            "identity; call it through an authenticated endpoint"
        )
    return caller


async def run_fact_tool(
    service: FactService, caller: FactCaller | None, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Run one fact tool; raises ``FactError`` subclasses with a remedy in the message."""
    match name:
        case "mimir_fact_remember":
            draft = fact_wire.parse_draft(args)
            outcome = await service.remember(_require_caller(caller, name), draft)
            return {"status": outcome.status, "fact": fact_wire.fact_json(outcome.fact)}
        case "mimir_fact_recall":
            fact_wire.reject_platform_fields(args)
            min_trust, as_of = fact_wire.parse_read_filters(args)
            recall = await service.recall(
                subject=args.get("subject"),
                contains=args.get("contains"),
                min_trust=min_trust,
                as_of=as_of,
            )
            return fact_wire.recall_json(recall)
        case "mimir_fact_forget":
            fact = await service.forget(
                _require_caller(caller, name), str(args.get("id", "")), str(args.get("why", ""))
            )
            return {"fact": fact_wire.fact_json(fact)}
        case "mimir_fact_entity":
            return fact_wire.entity_json(await service.entity(str(args.get("subject", ""))))
        case "mimir_fact_context_pack":
            min_trust, as_of = fact_wire.parse_read_filters(args)
            subjects = args.get("subjects")
            pack = await service.context_pack(
                list(subjects) if isinstance(subjects, list) else [],
                task=args.get("task"),
                budget_tokens=args.get("budget_tokens"),
                min_trust=min_trust,
                as_of=as_of,
            )
            return fact_wire.pack_json(pack)
        case "mimir_fact_share":
            return {"fact": fact_wire.shared_json(await service.share(str(args.get("id", ""))))}
        case "mimir_fact_receive":
            outcome = await service.receive(
                _require_caller(caller, name), fact_wire.parse_shared(args)
            )
            return {"status": outcome.status, "fact": fact_wire.fact_json(outcome.fact)}
    raise ValueError(f"Unknown fact tool: {name}")
