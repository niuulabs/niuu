"""The ``memory`` tools a session's harness gets (contract C2, design section 5).

:class:`MemoryTools` lists the tool definitions with the instructions filled in
with the session's real layers, and runs one call, answering an MCP
``CallToolResult``. A refusal by the write rules, a validation error, a missing
record or an unreachable layer is ``isError: true`` with a message that names
the cause; every write result names the layer it landed in, or the proposal and
who must approve it. The tools search and read; the gateway never writes an answer with a
model, since each memory system already searches and synthesises on its own.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from mimir.memory.gateway import (
    Disagreement,
    EntityCard,
    LabelledFact,
    LabelledPage,
    LayerUnavailableError,
    MemoryGateway,
    ReadResult,
    RecallResult,
    Session,
    WriteOutcome,
)
from mimir.memory.models import (
    HitKind,
    LayerKind,
    MemoryStoreError,
    Proposal,
    RecordNotFoundError,
    RevisionConflictError,
    SourceKind,
)
from mimir.memory.review import proposal_title
from mimir.memory.rules import RuleRefusalError, normalize_prefix
from mimir.ports.proposal_notifier import ProposalNotifier, ProposalNotifyError, WaitingProposal
from niuu.cap_telemetry import record_degraded
from niuu.domain.mimir_facts import Fact, FactError, FactKind, Omission


class UnknownToolError(Exception):
    """No memory tool has this name."""


class ToolArgumentError(Exception):
    """A tool was called with arguments that do not fit its schema."""


_EVIDENCE = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "What this was learned from, each as <kind>:<value>: source:<id>, "
        "session_event:<session>/<event>, tool_call:<id>, room_message:<room>/<message>, "
        "url:<http(s) url>, commit:<repo>@<sha>, statement:<id>, page:<ref>, fact:<id>. "
        "Never free text."
    ),
}
_LAYER = {
    "type": "string",
    "description": (
        "The layer to write: session (default), run, project or org, or a full id such as "
        "project:<id> or attached:<name>. Writing above the session follows the rules in "
        "the instructions."
    ),
}
_WHY = {
    "type": "string",
    "description": (
        "If this becomes a proposal: in plain words, what changed and why it is worth "
        "keeping, so its owner can decide in one read (e.g. 'The payments freeze moved a "
        "week, says LIN-501')."
    ),
}
_LAYERS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Only these layers (session, run, project, org or full ids).",
}
_FACT_KINDS = [k.value for k in FactKind if k is not FactKind.IDEA]


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "memory_search",
        "description": (
            "Ranked pages and sources across your memory layers, each labelled with its layer."
        ),
        "inputSchema": _schema(
            {
                "query": {"type": "string"},
                "layers": _LAYERS,
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": [k.value for k in HitKind]},
                },
                "limit": {"type": "integer", "minimum": 1},
            },
            ["query"],
        ),
    },
    {
        "name": "memory_read",
        "description": (
            "Read a page (its ref, e.g. services/booking-api), a source (source:<id>) or a "
            "fact (fact:<id>). A page that exists in several layers is returned from each, "
            "nearest layer first."
        ),
        "inputSchema": _schema({"ref": {"type": "string"}, "layer": {"type": "string"}}, ["ref"]),
    },
    {
        "name": "memory_entity",
        "description": (
            "The merged card of a subject across your layers: its pages, aliases, links and "
            "the facts that hold now, nearest layer first, each part labelled."
        ),
        "inputSchema": _schema({"name": {"type": "string"}}, ["name"]),
    },
    {
        "name": "memory_recall",
        "description": (
            "Facts about a subject that hold now, highest trust first, then the most recent."
        ),
        "inputSchema": _schema(
            {
                "about": {"type": "string"},
                "kinds": {"type": "array", "items": {"type": "string", "enum": _FACT_KINDS}},
            },
            ["about"],
        ),
    },
    {
        "name": "memory_add_source",
        "description": (
            "Store raw material as it arrived (a ticket, PR, thread, document, excerpt). "
            "Tool output is not a source: cite it as tool_call:<id>."
        ),
        "inputSchema": _schema(
            {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "kind": {"type": "string", "enum": [k.value for k in SourceKind]},
                "url": {"type": "string", "description": "Where it came from, if anywhere."},
                "evidence": _EVIDENCE,
                "layer": _LAYER,
                "why": _WHY,
            },
            ["title", "content", "kind"],
        ),
    },
    {
        "name": "memory_write_page",
        "description": (
            "Rewrite a page's compiled truth (its current state). The timeline is kept. "
            "Pass the revision you read as expected_revision; omit it only to create a page."
        ),
        "inputSchema": _schema(
            {
                "ref": {"type": "string"},
                "compiled_truth": {"type": "string"},
                "expected_revision": {"type": "string"},
                "title": {"type": "string"},
                "type": {"type": "string"},
                "evidence": _EVIDENCE,
                "layer": _LAYER,
                "why": _WHY,
            },
            ["ref", "compiled_truth"],
        ),
    },
    {
        "name": "memory_add_timeline",
        "description": "Append one cited entry to a page's timeline.",
        "inputSchema": _schema(
            {
                "ref": {"type": "string"},
                "entry": {"type": "string", "description": "One line: what happened."},
                "evidence": _EVIDENCE,
                "date": {"type": "string", "description": "YYYY-MM-DD; today when omitted."},
                "detail": {"type": "string"},
                "layer": _LAYER,
                "why": _WHY,
            },
            ["ref", "entry", "evidence"],
        ),
    },
    {
        "name": "memory_remember",
        "description": "Record one fact about a subject, with the evidence it rests on.",
        "inputSchema": _schema(
            {
                "fact": {"type": "string"},
                "about": {"type": "string", "description": "The subject's slug."},
                "kind": {"type": "string", "enum": _FACT_KINDS},
                "evidence": _EVIDENCE,
                "valid_until": {"type": "string", "description": "ISO 8601 with a timezone."},
                "replaces": {"type": "array", "items": {"type": "string"}},
                "layer": _LAYER,
                "why": _WHY,
            },
            ["fact", "about", "kind", "evidence"],
        ),
    },
    {
        "name": "memory_retire",
        "description": "Retire a fact you are allowed to retire, with the reason.",
        "inputSchema": _schema(
            {"fact": {"type": "string"}, "reason": {"type": "string"}}, ["fact", "reason"]
        ),
    },
    {
        "name": "memory_propose",
        "description": (
            "Propose a change to a layer you may not write; its owner approves it. The "
            "change names an action (add_source, write_page, add_timeline, remember, retire) "
            "and that action's arguments."
        ),
        "inputSchema": _schema(
            {
                "layer": {"type": "string"},
                "change": {
                    "type": "object",
                    "properties": {"action": {"type": "string"}},
                    "required": ["action"],
                },
                "evidence": _EVIDENCE,
                "why": {
                    "type": "string",
                    "description": (
                        "In plain words: what changed and why it is worth keeping, so the "
                        "layer's owner can decide in one read."
                    ),
                },
            },
            ["layer", "change", "evidence", "why"],
        ),
    },
)


def instructions(session: Session) -> str:
    """Design section 5's instructions, filled with this session's layers."""
    parts = [f"this session ({session.view.layers[0].id})"]
    run = next((lyr for lyr in session.layers if lyr.kind is LayerKind.RUN), None)
    if run is not None:
        parts.append(f"this workflow run ({run.id})")
    project = next((lyr for lyr in session.layers if lyr.kind is LayerKind.PROJECT), None)
    if project is not None:
        parts.append(f"project {project.key} ({project.id})")
    attached = [lyr for lyr in session.layers if lyr.kind is LayerKind.ATTACHED]
    parts += [f"attached memory {lyr.key} ({lyr.id})" for lyr in attached]
    parts.append("the org (org)")
    layers = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    text = (
        f"Your memory has layers: {layers}. memory_search reads all of them. "
        "Before working on a subject, read its entity card. Write what you find to the "
        "session layer with evidence."
    )
    if run is not None:
        text += " Share what the other sessions of this run need in the run layer."
    if project is not None:
        prefixes = [normalize_prefix(p) for p in session.agent.context.project_write_prefixes]
        allowed = ", ".join(p for p in prefixes if p) or "none are set, so propose every change"
        text += (
            f" Write to the project only under its write prefixes ({allowed}); otherwise propose."
            " Keeping what this session learned is your job: as you work, add timeline "
            "entries and sources for what changed; before you finish, propose to the project "
            "what others on it will need (decisions, corrections, findings), each with its "
            "evidence and a why in plain words. A person reviews your proposals one at a "
            "time; nobody sorts your session notes."
        )
    for lyr in attached:
        attachment = session.view.attachment(lyr)
        if attachment.access.value == "read":
            text += f" {lyr.id} is read-only."
            continue
        prefixes = [normalize_prefix(p) for p in attachment.write_prefixes]
        allowed = ", ".join(p for p in prefixes if p) or "none are set"
        text += f" {lyr.id} takes pages only under its write prefixes ({allowed})."
    text += " Never store your own conclusions as facts about the world without evidence."
    return text


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def _ok(text: str, structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": False,
    }


def tool_error(text: str, **structured: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"error": text, **structured},
        "isError": True,
    }


def _fact(labelled: LabelledFact) -> dict[str, Any]:
    fact: Fact = labelled.fact
    return {
        "layer": labelled.layer.id,
        "id": fact.id,
        "subject": fact.subject,
        "statement": fact.statement,
        "kind": fact.kind.value,
        "trust": fact.trust.label,
        "author": fact.provenance.author.ref,
        "evidence": list(fact.provenance.evidence),
        "valid_from": fact.valid_from.isoformat(),
        "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
        "retired": (
            {"at": fact.retired.at.isoformat(), "by": fact.retired.by, "why": fact.retired.why}
            if fact.retired
            else None
        ),
        "attested": fact.provenance.attested,
    }


def _page(labelled: LabelledPage) -> dict[str, Any]:
    page = labelled.page
    attribution = page.attribution
    return {
        "layer": labelled.layer.id,
        "ref": page.slug,
        "title": page.title,
        "type": page.page_type,
        "revision": page.revision,
        "compiled_truth": page.compiled_truth,
        "written_by": attribution.author,
        "trust": attribution.trust.label,
        "attested": attribution.attested,
        "evidence": [e.ref for e in attribution.evidence],
        "timeline": [
            {
                "date": row.on.isoformat(),
                "entry": row.summary,
                "detail": row.detail,
                "by": row.attribution.author,
                "trust": row.attribution.trust.label,
                "evidence": [e.ref for e in row.attribution.evidence],
                "attested": row.attribution.attested,
            }
            for row in page.timeline
        ],
        "timeline_cut": page.timeline_omitted or None,
    }


def _omitted(omitted: tuple[Omission, ...]) -> list[dict[str, Any]]:
    return [{"reason": o.reason.value, "count": o.count, "detail": o.detail} for o in omitted]


def _disagreements(items: tuple[Disagreement, ...]) -> list[dict[str, Any]]:
    return [{"about": d.about, "records": list(d.records), "note": d.note} for d in items]


def _notes(disagreements: tuple[Disagreement, ...], omitted: tuple[Omission, ...]) -> str:
    lines = [f"Disagreement: {d.note}." for d in disagreements]
    lines += [f"Left out: {o.detail}." for o in omitted]
    return ("\n" + "\n".join(lines)) if lines else ""


def _read_result(result: ReadResult) -> dict[str, Any]:
    pages = [_page(p) for p in result.pages]
    sources = [
        {
            "layer": s.layer.id,
            "id": s.source.id,
            "title": s.source.title,
            "kind": s.source.kind,
            "url": s.source.origin_url,
            "content": s.source.content,
            "trust": s.source.attribution.trust.label,
            "attested": s.source.attribution.attested,
        }
        for s in result.sources
    ]
    facts = [_fact(f) for f in result.facts]
    structured = {
        "pages": pages,
        "sources": sources,
        "facts": facts,
        "disagreements": _disagreements(result.disagreements),
    }
    lines = [f"[{p['layer']}] page {p['ref']} (revision {p['revision']})" for p in pages]
    lines += [f"[{s['layer']}] source {s['id']}: {s['title']}" for s in sources]
    lines += [f"[{f['layer']}] fact {f['id']}: {f['statement']}" for f in facts]
    text = "\n".join(lines) + _notes(result.disagreements, ())
    return _ok(text + "\n\n" + json.dumps(structured, ensure_ascii=False), structured)


def _entity_result(card: EntityCard) -> dict[str, Any]:
    structured = {
        "subject": card.subject,
        "pages": [_page(p) for p in card.pages],
        "aliases": [{"layer": lyr.id, "alias": a} for lyr, a in card.aliases],
        "links": [{"layer": lyr.id, "ref": link} for lyr, link in card.links],
        "facts": [_fact(f) for f in card.facts],
        "disagreements": _disagreements(card.disagreements),
        "omitted": _omitted(card.omitted),
    }
    if not card.pages and not card.facts:
        text = f"Nothing in your layers is about {card.subject} yet."
    else:
        text = f"{card.subject}: {len(card.pages)} page(s), {len(card.facts)} current fact(s)."
    text += _notes(card.disagreements, card.omitted)
    return _ok(text + "\n\n" + json.dumps(structured, ensure_ascii=False), structured)


def _recall_result(about: str, result: RecallResult) -> dict[str, Any]:
    facts = [_fact(f) for f in result.facts]
    structured = {
        "about": about,
        "facts": facts,
        "disagreements": _disagreements(result.disagreements),
        "omitted": _omitted(result.omitted),
    }
    lines = [f"[{f['layer']}, {f['trust']}] {f['statement']} ({f['id']})" for f in facts]
    text = ("\n".join(lines) or f"No current facts about {about}.") + _notes(
        result.disagreements, result.omitted
    )
    return _ok(text, structured)


def _write_result(outcome: WriteOutcome) -> dict[str, Any]:
    structured: dict[str, Any] = {"layer": outcome.layer.id, **outcome.record}
    if outcome.proposal is not None:
        structured["proposal"] = {
            "id": outcome.proposal.id,
            "status": outcome.proposal.status.value,
            "approver": outcome.proposal.approver,
            "reason": outcome.proposal.reason,
        }
        structured["written"] = False
    else:
        structured["written"] = True
    return _ok(outcome.summary, structured)


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Args:
    values: dict[str, Any]

    def text(self, key: str, *, required: bool = False) -> str:
        value = self.values.get(key)
        if value is None:
            if required:
                raise ToolArgumentError(f"{key} is required")
            return ""
        if not isinstance(value, str):
            raise ToolArgumentError(f"{key} must be a string")
        if required and not value.strip():
            raise ToolArgumentError(f"{key} must not be empty")
        return value

    def optional(self, key: str) -> str | None:
        return self.text(key) or None

    def strings(self, key: str) -> list[str]:
        value = self.values.get(key)
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ToolArgumentError(f"{key} must be a list of strings")
        return value

    def integer(self, key: str) -> int | None:
        value = self.values.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolArgumentError(f"{key} must be an integer")
        return value

    def day(self, key: str) -> date | None:
        value = self.optional(key)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ToolArgumentError(f"{key} must be a date as YYYY-MM-DD") from None

    def moment(self, key: str) -> datetime | None:
        value = self.optional(key)
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ToolArgumentError(f"{key} must be an ISO 8601 time with a timezone") from None

    def mapping(self, key: str) -> dict[str, Any]:
        value = self.values.get(key)
        if not isinstance(value, dict):
            raise ToolArgumentError(f"{key} must be an object")
        return value


def _waiting(proposal: Proposal, project_id: str) -> WaitingProposal:
    return WaitingProposal(
        proposal_id=proposal.id,
        project_id=project_id,
        title=proposal_title(proposal),
        why=proposal.why,
    )


class MemoryTools:
    """The C2 tool surface over one gateway.

    With a *notifier*, a write that became a proposal to a project layer is announced to
    the project's approvers and owners through Forge, with the session's own credential.
    """

    def __init__(
        self,
        gateway: MemoryGateway,
        notifier: ProposalNotifier | None = None,
    ) -> None:
        self._gateway = gateway
        self._notifier = notifier
        self._handlers: dict[
            str, Callable[[Session, _Args], Awaitable[dict[str, Any] | WriteOutcome]]
        ] = {
            "memory_search": self._search,
            "memory_read": self._read,
            "memory_entity": self._entity,
            "memory_recall": self._recall,
            "memory_add_source": self._add_source,
            "memory_write_page": self._write_page,
            "memory_add_timeline": self._add_timeline,
            "memory_remember": self._remember,
            "memory_retire": self._retire,
            "memory_propose": self._propose,
        }

    def definitions(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in TOOL_DEFINITIONS]

    def exists(self, name: str) -> bool:
        return any(tool["name"] == name for tool in self.definitions())

    async def call(
        self, session: Session, name: str, arguments: Any, *, credential: str = ""
    ) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            raise UnknownToolError(name)
        if not isinstance(arguments, dict):
            return tool_error(f"{name} takes a JSON object of arguments")
        allowed = set(
            next(t for t in TOOL_DEFINITIONS if t["name"] == name)["inputSchema"]["properties"]
        )
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            return tool_error(
                f"{name} has no argument {', '.join(unknown)}; "
                f"it takes {', '.join(sorted(allowed))}"
            )
        try:
            result = await handler(session, _Args(arguments))
            if isinstance(result, WriteOutcome):
                return await self._written(session, result, credential)
            return result
        except RuleRefusalError as exc:
            return tool_error(str(exc), rule=exc.rule.value)
        except ToolArgumentError as exc:
            return tool_error(f"{name}: {exc}")
        except RecordNotFoundError as exc:
            return tool_error(f"not found: {exc}")
        except RevisionConflictError as exc:
            return tool_error(f"revision conflict: {exc}")
        except LayerUnavailableError as exc:
            return tool_error(str(exc))
        except (MemoryStoreError, FactError) as exc:
            return tool_error(str(exc))

    async def _written(
        self, session: Session, outcome: WriteOutcome, credential: str
    ) -> dict[str, Any]:
        """The write's result; a proposal to a project is announced to its deciders first.

        The proposal is already recorded, so a failed announcement does not fail the call
        (a retry would record it twice): it is counted as degraded and said in the result.
        """
        result = _write_result(outcome)
        proposal = outcome.proposal
        if proposal is None or self._notifier is None:
            return result
        if outcome.layer.kind is not LayerKind.PROJECT:
            return result
        try:
            await self._notifier.waiting(
                session.agent.context.session_id,
                _waiting(proposal, outcome.layer.key),
                credential=credential,
            )
        except ProposalNotifyError as exc:
            message = (
                f"proposal {proposal.id} was recorded, but its approvers were not told: {exc}. "
                "It waits on their Review page"
            )
            record_degraded("memory.proposal_notify", message)
            result["content"][0]["text"] += f" {message}."
            result["structuredContent"]["notified"] = False
            return result
        result["structuredContent"]["notified"] = True
        return result

    # -- handlers --------------------------------------------------------

    async def _search(self, session: Session, args: _Args) -> dict[str, Any]:
        try:
            kinds = [HitKind(k) for k in args.strings("kinds")] or None
        except ValueError:
            raise ToolArgumentError("kinds are page and source") from None
        hits = await self._gateway.search(
            session,
            args.text("query", required=True),
            layers=args.strings("layers") or None,
            kinds=kinds,
            limit=args.integer("limit"),
        )
        items = [
            {
                "layer": h.layer.id,
                "kind": h.hit.kind.value,
                "ref": h.hit.ref if h.hit.kind is HitKind.PAGE else f"source:{h.hit.ref}",
                "title": h.hit.title,
                "snippet": h.hit.snippet,
            }
            for h in hits
        ]
        lines = [f"[{i['layer']}] {i['ref']}: {i['title']} — {i['snippet']}" for i in items]
        return _ok("\n".join(lines) or "No matches in your layers.", {"hits": items})

    async def _read(self, session: Session, args: _Args) -> dict[str, Any]:
        result = await self._gateway.read(
            session, args.text("ref", required=True), layer=args.optional("layer")
        )
        return _read_result(result)

    async def _entity(self, session: Session, args: _Args) -> dict[str, Any]:
        return _entity_result(await self._gateway.entity(session, args.text("name", required=True)))

    async def _recall(self, session: Session, args: _Args) -> dict[str, Any]:
        about = args.text("about", required=True)
        try:
            kinds = [FactKind(k) for k in args.strings("kinds")] or None
        except ValueError:
            raise ToolArgumentError(f"kinds are {', '.join(_FACT_KINDS)}") from None
        return _recall_result(about, await self._gateway.recall(session, about, kinds=kinds))

    async def _add_source(self, session: Session, args: _Args) -> WriteOutcome:
        outcome = await self._gateway.add_source(
            session,
            title=args.text("title", required=True),
            content=args.text("content", required=True),
            kind=args.text("kind", required=True),
            url=args.optional("url"),
            evidence=args.strings("evidence"),
            layer=args.optional("layer"),
            why=args.text("why"),
        )
        return outcome

    async def _write_page(self, session: Session, args: _Args) -> WriteOutcome:
        outcome = await self._gateway.write_page(
            session,
            args.text("ref", required=True),
            compiled_truth=args.text("compiled_truth", required=True),
            expected_revision=args.optional("expected_revision"),
            title=args.text("title"),
            page_type=args.text("type"),
            evidence=args.strings("evidence"),
            layer=args.optional("layer"),
            why=args.text("why"),
        )
        return outcome

    async def _add_timeline(self, session: Session, args: _Args) -> WriteOutcome:
        outcome = await self._gateway.add_timeline(
            session,
            args.text("ref", required=True),
            entry=args.text("entry", required=True),
            evidence=args.strings("evidence"),
            on=args.day("date"),
            detail=args.text("detail"),
            layer=args.optional("layer"),
            why=args.text("why"),
        )
        return outcome

    async def _remember(self, session: Session, args: _Args) -> WriteOutcome:
        outcome = await self._gateway.remember(
            session,
            fact=args.text("fact", required=True),
            about=args.text("about", required=True),
            kind=args.text("kind", required=True),
            evidence=args.strings("evidence"),
            valid_until=args.moment("valid_until"),
            replaces=args.strings("replaces"),
            layer=args.optional("layer"),
            why=args.text("why"),
        )
        return outcome

    async def _retire(self, session: Session, args: _Args) -> WriteOutcome:
        return await self._gateway.retire(
            session, args.text("fact", required=True), reason=args.text("reason", required=True)
        )

    async def _propose(self, session: Session, args: _Args) -> WriteOutcome:
        change = dict(args.mapping("change"))
        action = change.pop("action", None)
        if not isinstance(action, str):
            raise ToolArgumentError("change.action is required")
        outcome = await self._gateway.propose(
            session,
            layer=args.text("layer", required=True),
            action=action,
            change=change,
            evidence=args.strings("evidence"),
            why=args.text("why", required=True),
        )
        return outcome
