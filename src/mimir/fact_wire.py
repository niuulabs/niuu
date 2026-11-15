"""JSON shapes for typed facts, shared by the MCP server and the agent tools.

Input parsing is strict on purpose. A request that names a trust level, an
author or a confidence is refused outright rather than ignored: those are set
by the platform from the caller's identity, and a caller that tried to set one
should be told it has no say.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from niuu.domain.mimir_facts import (
    ContextPack,
    EntityRecord,
    Fact,
    FactAuthor,
    FactDraft,
    FactKind,
    FactRecall,
    FactValidationError,
    Omission,
    SharedFact,
    SharedOrigin,
    TrustError,
    TrustLevel,
)

_PLATFORM_SET = ("trust", "author", "confidence", "trust_level", "provenance_author")


def reject_platform_fields(args: dict[str, Any]) -> None:
    claimed = [key for key in _PLATFORM_SET if key in args]
    if claimed:
        raise TrustError(
            f"{', '.join(claimed)} cannot be set by the caller: the platform assigns trust and "
            "authorship from who you are and from the evidence. Remove it from the request"
        )


def _check_keys(args: dict[str, Any], allowed: set[str], what: str) -> None:
    reject_platform_fields(args)
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise FactValidationError(
            f"{what} does not take {', '.join(unknown)}; accepted: {', '.join(sorted(allowed))}"
        )


def _text(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FactValidationError(f"{key} is required and must be a non-empty string")
    return value.strip()


def _strings(args: dict[str, Any], key: str) -> tuple[str, ...]:
    value = args.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise FactValidationError(f"{key} must be a list of strings")
    return tuple(value)


def parse_moment(value: Any, key: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        raise FactValidationError(f"{key} must be an ISO 8601 timestamp, got {value!r}") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def parse_draft(args: dict[str, Any]) -> FactDraft:
    _check_keys(
        args,
        {
            "subject",
            "statement",
            "evidence",
            "kind",
            "valid_from",
            "valid_until",
            "replaces",
            "derived_from",
        },
        "remember",
    )
    try:
        kind = FactKind(args.get("kind") or "fact")
    except ValueError:
        raise FactValidationError(
            f"kind {args.get('kind')!r} is not one of: {', '.join(k.value for k in FactKind)}"
        ) from None
    return FactDraft(
        subject=_text(args, "subject"),
        statement=_text(args, "statement"),
        evidence=_strings(args, "evidence"),
        kind=kind,
        valid_from=parse_moment(args.get("valid_from"), "valid_from"),
        valid_until=parse_moment(args.get("valid_until"), "valid_until"),
        replaces=_strings(args, "replaces"),
        derived_from=_strings(args, "derived_from"),
    )


def parse_read_filters(args: dict[str, Any]) -> tuple[TrustLevel, datetime | None]:
    min_trust = TrustLevel.parse(args["min_trust"]) if args.get("min_trust") else TrustLevel.AGENT
    return min_trust, parse_moment(args.get("as_of"), "as_of")


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def author_json(author: FactAuthor | None) -> str | None:
    return author.ref if author else None


def fact_json(fact: Fact) -> dict[str, Any]:
    provenance = fact.provenance
    data: dict[str, Any] = {
        "id": fact.id,
        "subject": fact.subject,
        "statement": fact.statement,
        "kind": fact.kind.value,
        "trust": fact.trust.label,
        "author": provenance.author.ref,
        "evidence": list(provenance.evidence),
        "attested": provenance.attested,
        "recorded_at": _iso(fact.recorded_at),
        "valid_from": _iso(fact.valid_from),
        "valid_until": _iso(fact.valid_until),
    }
    if provenance.derived_from:
        data["derived_from"] = list(provenance.derived_from)
    if provenance.replaces:
        data["replaces"] = list(provenance.replaces)
    if provenance.origin:
        data["origin"] = origin_json(provenance.origin)
        data["imported_by"] = author_json(provenance.imported_by)
    if fact.retired:
        data["retired"] = {
            "at": _iso(fact.retired.at),
            "by": fact.retired.by,
            "why": fact.retired.why,
            "superseded_by": fact.retired.superseded_by,
        }
    return data


def origin_json(origin: SharedOrigin) -> dict[str, Any]:
    return {
        "author": origin.author.ref,
        "claimed_trust": origin.claimed_trust.label,
        "fact_id": origin.fact_id,
        "source": origin.source,
        "evidence": list(origin.evidence),
    }


def omission_json(omission: Omission) -> dict[str, Any]:
    return {
        "reason": omission.reason.value,
        "count": omission.count,
        "detail": omission.detail,
        "fact_ids": list(omission.fact_ids),
    }


def recall_json(recall: FactRecall) -> dict[str, Any]:
    return {
        "facts": [fact_json(f) for f in recall.facts],
        "omitted": [omission_json(o) for o in recall.omitted],
    }


def entity_json(record: EntityRecord) -> dict[str, Any]:
    return {
        "subject": record.subject,
        "found": record.found,
        "facts": [fact_json(f) for f in record.facts],
        "history": [fact_json(f) for f in record.history],
        "card": record.card,
        "omitted": [omission_json(o) for o in record.omitted],
    }


def pack_json(pack: ContextPack) -> dict[str, Any]:
    return {
        "facts": [fact_json(f) for f in pack.facts],
        "omitted": [omission_json(o) for o in pack.omitted],
        "text": pack.text,
        "budget_tokens": pack.budget_tokens,
        "used_tokens": pack.used_tokens,
    }


def shared_json(shared: SharedFact) -> dict[str, Any]:
    data: dict[str, Any] = {
        "fact_id": shared.fact_id,
        "subject": shared.subject,
        "statement": shared.statement,
        "kind": shared.kind.value,
        "trust": shared.trust.label,
        "author": shared.author.ref,
        "evidence": list(shared.evidence),
        "valid_from": _iso(shared.valid_from),
        "valid_until": _iso(shared.valid_until),
        "source": shared.source,
    }
    if shared.origin:
        data["origin"] = origin_json(shared.origin)
    return data


def parse_shared(args: dict[str, Any]) -> SharedFact:
    """Read a shared fact. Its ``trust`` is the sender's claim, never believed as given."""
    bundle = args.get("fact")
    if not isinstance(bundle, dict):
        raise FactValidationError("fact must be the object a peer's share returned")
    required = ("fact_id", "subject", "statement", "trust", "author", "evidence", "valid_from")
    missing = [key for key in required if key not in bundle]
    if missing:
        raise FactValidationError(f"the shared fact is missing {', '.join(missing)}")
    origin = bundle.get("origin")
    parsed_origin = None
    if isinstance(origin, dict):
        parsed_origin = SharedOrigin(
            author=FactAuthor.parse(origin["author"]),
            claimed_trust=TrustLevel.parse(origin["claimed_trust"]),
            fact_id=origin["fact_id"],
            source=origin["source"],
            evidence=tuple(origin.get("evidence", ())),
        )
    valid_from = parse_moment(bundle["valid_from"], "valid_from")
    if valid_from is None:
        raise FactValidationError("the shared fact has an empty valid_from")
    return SharedFact(
        fact_id=str(bundle["fact_id"]),
        subject=str(bundle["subject"]),
        statement=str(bundle["statement"]),
        kind=FactKind(bundle.get("kind") or "fact"),
        trust=TrustLevel.parse(bundle["trust"]),
        author=FactAuthor.parse(bundle["author"]),
        evidence=_strings(bundle, "evidence"),
        valid_from=valid_from,
        valid_until=parse_moment(bundle.get("valid_until"), "valid_until"),
        source=str(bundle.get("source") or ""),
        origin=parsed_origin,
    )
