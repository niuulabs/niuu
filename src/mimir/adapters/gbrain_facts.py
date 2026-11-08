"""GBrainFactAdapter — MimirFactPort over gbrain's memory verbs.

gbrain (pinned in ``containers/gbrain/Dockerfile``) already has a fact store
and a frozen memory protocol (``docs/protocol/MEMORY_VERBS_v1.md`` upstream):
``remember``, ``recall``, ``entity``, ``forget`` and ``context_pack``. A fact row
carries the statement, an entity, a kind, ``valid_from`` / ``valid_until``,
``expired_at`` (``forget`` expires, never deletes), ``superseded_by``, a
free-text ``source`` (the provenance) and ``visibility``. This adapter uses
those, and adds only what gbrain lacks:

========================  =====================================================
Needed                    How
========================  =====================================================
subject, statement, kind  ``remember`` ``entity`` / ``fact`` / ``kind``
validity (until)          ``remember`` ``ttl`` as an absolute timestamp
retire, history kept      ``forget`` (expires with an audit trail)
as-of reads               ``recall`` ``include_expired`` + validity filter here
contains                  ``recall`` ``grep``
trust, author, evidence   sealed into the ``provenance`` text (see below)
valid_from, replaces      sealed into the same text (``remember`` has no input)
who / why on forget       sealed into the ``reason``
min trust, ranking,       here, over what ``recall`` returns
budget, omissions
========================  =====================================================

**Why the envelope rides in ``provenance``.** gbrain's ``remember`` takes a
single free-text ``provenance`` (500 characters at most, stored verbatim and
returned by ``recall``). Putting the structure there keeps one store with no
parallel database or migration, and it travels with the row through backup and
replication. The envelope is ``mf1.<signature>.<json>``; the signature is an
HMAC over the JSON with a key only Mímir holds, so someone writing to gbrain
directly cannot forge a trust level: a row whose envelope is missing or does
not verify is read back as an unattested EXTERNAL fact at the lowest level.
An envelope that does not fit the 500 characters raises, naming the limit.

**Native supersession.** gbrain supersedes an older fact on its own when a new
one on the same entity and kind is similar enough. ``remember`` has no switch
for that, so a low-trust write can retire a high-trust fact behind the trust
check. The adapter looks for it after every ``superseded`` result and raises
loudly if a higher-trust fact was retired.

**The recall cap.** gbrain caps ``recall`` at 100 rows per call and does not
page. A call that returns exactly that many is reported as an omission
(``BACKEND_LIMIT``), counted as a cap hit, and ``get`` refuses to say "not
found" when it might only have been beyond the cap.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from mimir.adapters.gbrain import _parse_mcp_response, _text_result
from niuu.cap_telemetry import record_cap_hit
from niuu.domain.mimir_facts import (
    AuthorKind,
    EntityRecord,
    Fact,
    FactAuthor,
    FactBackendError,
    FactKind,
    FactNotFoundError,
    FactProvenance,
    FactRecall,
    FactValidationError,
    NewFact,
    Omission,
    OmissionReason,
    RememberOutcome,
    Retirement,
    SharedOrigin,
    TrustError,
    TrustLevel,
    select_facts,
)
from niuu.ports.mimir_facts import MimirFactPort

logger = logging.getLogger(__name__)

_TAG = "mf1"
_RECALL_KNOB = "mimir.facts.gbrain.recall_limit"
_PROVENANCE_KNOB = "mimir.facts.gbrain.provenance_max_chars"
_ID_SEPARATOR = "@"


def fact_id(raw_id: Any, entity_slug: str | None) -> str:
    """Mímir's id for a gbrain fact: ``<gbrain id>@<entity slug>``.

    gbrain has no read-by-id verb, so the entity is part of the id: ``get``
    needs it to find the row.
    """
    return f"{raw_id}{_ID_SEPARATOR}{entity_slug or ''}"


def split_fact_id(value: str) -> tuple[str, str]:
    raw, sep, entity = value.partition(_ID_SEPARATOR)
    if not sep or not raw.strip() or not entity.strip():
        raise FactValidationError(
            f"fact id {value!r} is not '<id>@<entity>'; use the id a recall or remember returned"
        )
    return raw.strip(), entity.strip()


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class GBrainFactAdapter(MimirFactPort):
    """Typed facts on a gbrain brain, reached over its MCP endpoint.

    Args:
        mcp_url: gbrain MCP endpoint (``https://brain/mcp``).
        api_token / api_token_file: bearer token with read and write scope.
        signing_key / signing_key_file: secret that seals fact envelopes. Required:
            without it a trust level could be forged by writing to gbrain directly.
        timeout_seconds: HTTP timeout per call.
        visibility: ``world`` (recallable by every agent on the brain) or ``private``.
        recall_limit: gbrain's documented per-call cap on ``recall`` rows (100).
            Set to what the pinned gbrain build enforces; hitting it is reported.
        provenance_max_chars: gbrain's documented limit on ``provenance`` (500).
    """

    def __init__(
        self,
        mcp_url: str,
        api_token: str = "",
        *,
        api_token_file: str = "",
        signing_key: str = "",
        signing_key_file: str = "",
        timeout_seconds: float = 30.0,
        visibility: str = "world",
        recall_limit: int = 100,
        provenance_max_chars: int = 500,
    ) -> None:
        if not mcp_url:
            raise ValueError("GBrainFactAdapter requires an MCP URL")
        if api_token_file:
            api_token = Path(api_token_file).expanduser().read_text().strip()
        if not api_token:
            raise ValueError(
                "GBrainFactAdapter requires an API token; create one with "
                "`gbrain auth create <name>` or set api_token_file"
            )
        if signing_key_file:
            signing_key = Path(signing_key_file).expanduser().read_text().strip()
        if not signing_key:
            raise ValueError(
                "GBrainFactAdapter requires a signing key (signing_key or signing_key_file): "
                "it seals each fact's trust level so a row written to gbrain directly "
                "cannot claim one. Generate a long random secret and keep it with Mímir only"
            )
        if visibility not in ("world", "private"):
            raise ValueError("visibility must be 'world' or 'private'")
        if recall_limit < 1 or provenance_max_chars < 1:
            raise ValueError("recall_limit and provenance_max_chars must be positive")
        self._mcp_url = mcp_url.rstrip("/")
        self._token = api_token
        self._key = signing_key.encode()
        self._timeout = float(timeout_seconds)
        self._visibility = visibility
        self._recall_limit = recall_limit
        self._provenance_max = provenance_max_chars
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _verb(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one gbrain memory verb; any failure is a FactBackendError."""
        try:
            response = await self._http().post(
                self._mcp_url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            response.raise_for_status()
            message = _parse_mcp_response(response.text)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            raise FactBackendError(
                f"gbrain {name} failed at {self._mcp_url}: {exc}. Check that the brain is "
                "running and the token has read and write scope"
            ) from exc
        if "error" in message:
            raise FactBackendError(f"gbrain {name} returned an MCP error: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise FactBackendError(f"gbrain {name} returned no result object: {message}")
        payload = self._payload(result)
        if result.get("isError") or ("error" in payload and "protocol_version" in payload):
            code = payload.get("error", "error")
            detail = payload.get("message") or _text_result(result)
            suggestion = payload.get("suggestion", "")
            text = f"gbrain {name} failed ({code}): {detail}" + (
                f" — {suggestion}" if suggestion else ""
            )
            if code == "not_found":
                raise FactNotFoundError(text)
            raise FactBackendError(text)
        return payload

    @staticmethod
    def _payload(result: dict[str, Any]) -> dict[str, Any]:
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        text = _text_result(result)
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # ------------------------------------------------------------------
    # Envelope: sealed provenance text
    # ------------------------------------------------------------------

    def _sign(self, body: str) -> str:
        digest = hmac.new(self._key, body.encode(), hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _seal(self, tag: str, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        return f"{tag}.{self._sign(body)}.{body}"

    def _open(self, tag: str, text: str | None) -> dict[str, Any] | None:
        """The payload of a sealed text, or None when it is not ours or does not verify."""
        if not text or not text.startswith(f"{tag}."):
            return None
        _, _, rest = text.partition(".")
        signature, sep, body = rest.partition(".")
        if not sep or not hmac.compare_digest(signature, self._sign(body)):
            logger.error(
                "gbrain fact carries a %s envelope whose signature does not verify; "
                "reading it as an unattested external fact",
                tag,
            )
            return None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _provenance_text(self, new: NewFact, written_at: datetime) -> str:
        draft = new.draft
        payload: dict[str, Any] = {
            "a": new.author.ref,
            "t": int(new.trust),
            "ev": list(draft.evidence),
            "w": written_at.isoformat(),
        }
        if draft.valid_from:
            payload["vf"] = draft.valid_from.isoformat()
        if draft.derived_from:
            payload["dr"] = list(draft.derived_from)
        if draft.replaces:
            payload["rp"] = list(draft.replaces)
        if new.imported_by:
            payload["ib"] = new.imported_by.ref
        if new.origin:
            payload["o"] = {
                "a": new.origin.author.ref,
                "t": int(new.origin.claimed_trust),
                "id": new.origin.fact_id,
                "s": new.origin.source,
                "ev": list(new.origin.evidence),
            }
        text = self._seal(_TAG, payload)
        if len(text) > self._provenance_max:
            record_cap_hit(
                _PROVENANCE_KNOB,
                f"a fact's sealed provenance is {len(text)} characters but gbrain stores at "
                f"most {self._provenance_max}; give fewer or shorter evidence references",
            )
            raise FactValidationError(
                f"the provenance for this fact is {len(text)} characters; gbrain keeps at most "
                f"{self._provenance_max} (knob {_PROVENANCE_KNOB}). Cite fewer or shorter "
                "evidence references (ids and paths, not quotations)"
            )
        return text

    def _provenance_of(
        self, row: dict[str, Any]
    ) -> tuple[FactProvenance, TrustLevel, datetime | None]:
        source = row.get("provenance") or row.get("source")
        payload = self._open(_TAG, source)
        if payload is None:
            return (
                FactProvenance(
                    author=FactAuthor(
                        AuthorKind.EXTERNAL, str(source or "unknown")[:80] or "unknown"
                    ),
                    evidence=(str(source),) if source else (),
                    attested=False,
                ),
                TrustLevel.AGENT,
                None,
            )
        origin = payload.get("o")
        provenance = FactProvenance(
            author=FactAuthor.parse(payload["a"]),
            evidence=tuple(payload.get("ev", ())),
            derived_from=tuple(payload.get("dr", ())),
            replaces=tuple(payload.get("rp", ())),
            origin=SharedOrigin(
                author=FactAuthor.parse(origin["a"]),
                claimed_trust=TrustLevel.parse(origin["t"]),
                fact_id=origin["id"],
                source=origin["s"],
                evidence=tuple(origin.get("ev", ())),
            )
            if origin
            else None,
            imported_by=FactAuthor.parse(payload["ib"]) if payload.get("ib") else None,
        )
        return provenance, TrustLevel.parse(payload["t"]), _moment(payload.get("vf"))

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _fact(self, row: dict[str, Any]) -> Fact:
        provenance, trust, claimed_from = self._provenance_of(row)
        slug = row.get("entity_slug")
        recorded = _moment(row.get("created_at")) or _moment(row.get("valid_from"))
        native_from = _moment(row.get("valid_from")) or recorded
        if recorded is None or native_from is None:
            raise FactBackendError(f"gbrain returned a fact without timestamps: {row.get('id')}")
        expired = _moment(row.get("expired_at"))
        retirement = None
        if expired is not None:
            note = self._open(f"{_TAG}r", row.get("context"))
            retirement = Retirement(
                at=expired,
                by=note["by"] if note else "unrecorded",
                why=note["why"] if note else str(row.get("context") or "not recorded"),
                superseded_by=(note or {}).get("sb")
                or (
                    fact_id(row["superseded_by"], slug)
                    if row.get("superseded_by") not in (None, "")
                    else None
                ),
            )
        return Fact(
            id=fact_id(row.get("fact_id") or row["id"], slug),
            subject=str(slug or ""),
            statement=str(row["fact"]),
            kind=FactKind(row.get("kind") or "fact"),
            trust=trust,
            provenance=provenance,
            recorded_at=recorded,
            valid_from=claimed_from or native_from,
            valid_until=_moment(row.get("valid_until")),
            retired=retirement,
        )

    async def _rows(
        self, entity: str | None, *, include_expired: bool, contains: str | None = None
    ) -> tuple[list[Fact], list[Omission]]:
        arguments: dict[str, Any] = {
            "limit": self._recall_limit,
            "include_expired": include_expired,
        }
        if entity:
            arguments["entity"] = entity
        if contains:
            arguments["grep"] = contains
        payload = await self._verb("recall", arguments)
        rows = payload.get("facts")
        if not isinstance(rows, list):
            raise FactBackendError(f"gbrain recall returned no facts list: {sorted(payload)}")
        facts = [self._fact(row) for row in rows]
        omitted: list[Omission] = []
        if len(rows) >= self._recall_limit:
            message = (
                f"gbrain returned {len(rows)} facts, its per-call cap; there may be more. "
                "Narrow the request (a subject or contains) or retire stale facts"
            )
            record_cap_hit(_RECALL_KNOB, message)
            omitted.append(Omission(OmissionReason.BACKEND_LIMIT, 0, message))
        return facts, omitted

    # ------------------------------------------------------------------
    # MimirFactPort
    # ------------------------------------------------------------------

    async def remember(self, new: NewFact) -> RememberOutcome:
        draft = new.draft
        now = datetime.now(UTC)
        arguments: dict[str, Any] = {
            "fact": draft.statement,
            "provenance": self._provenance_text(new, now),
            "entity": draft.subject,
            "kind": draft.kind.value,
            "visibility": self._visibility,
        }
        if draft.valid_until:
            arguments["ttl"] = draft.valid_until.astimezone(UTC).isoformat()
        payload = await self._verb("remember", arguments)
        raw_id, slug, status = payload.get("id"), payload.get("entity_slug"), payload.get("status")
        if (
            raw_id in (None, "")
            or not slug
            or status not in ("inserted", "duplicate", "superseded")
        ):
            raise FactBackendError(f"gbrain remember returned an unusable result: {payload}")
        stored = await self.get(fact_id(raw_id, slug))
        if status == "superseded":
            await self._refuse_trust_inversion(stored)
        return RememberOutcome(fact=stored, status=status)

    async def _refuse_trust_inversion(self, new: Fact) -> None:
        payload = await self._verb(
            "recall", {"entity": new.subject, "supersessions": True, "limit": self._recall_limit}
        )
        for row in payload.get("facts", []):
            retired = self._fact(row)
            if (
                row.get("superseded_by") is None
                or fact_id(row["superseded_by"], new.subject) != new.id
            ):
                continue
            if retired.trust > new.trust:
                raise TrustError(
                    f"gbrain retired {retired.trust.label}-level fact {retired.id} because the "
                    f"{new.trust.label}-level fact {new.id} is similar to it. The retirement "
                    "cannot be undone here: re-remember the old claim at its level through a "
                    "caller of that level, and write corrections with replaces=[...]"
                )

    async def get(self, fact_id_: str) -> Fact:
        raw, slug = split_fact_id(fact_id_)
        facts, omitted = await self._rows(slug, include_expired=True)
        for fact in facts:
            if fact.id == fact_id_ or fact.id.partition(_ID_SEPARATOR)[0] == raw:
                return fact
        if omitted:
            raise FactBackendError(
                f"fact {fact_id_} was not among the {len(facts)} facts gbrain returned for "
                f"{slug}, which is its per-call cap; it may exist beyond it"
            )
        raise FactNotFoundError(f"no fact {fact_id_}")

    async def recall(
        self,
        *,
        subject: str | None = None,
        contains: str | None = None,
        min_trust: TrustLevel = TrustLevel.AGENT,
        as_of: datetime | None = None,
    ) -> FactRecall:
        now = datetime.now(UTC)
        moment = as_of or now
        facts, omitted = await self._rows(subject, include_expired=moment < now, contains=contains)
        kept, dropped = select_facts(facts, min_trust=min_trust, as_of=moment)
        return FactRecall(facts=tuple(kept), omitted=(*omitted, *dropped))

    async def forget(
        self, fact_id_: str, *, by: str, why: str, superseded_by: str | None = None
    ) -> Fact:
        current = await self.get(fact_id_)
        if current.retired is not None:
            return current
        raw, _ = split_fact_id(fact_id_)
        note: dict[str, Any] = {"by": by, "why": why}
        if superseded_by:
            note["sb"] = superseded_by
        payload = await self._verb("forget", {"id": raw, "reason": self._seal(f"{_TAG}r", note)})
        if "expired" not in payload:
            raise FactBackendError(f"gbrain forget returned an unusable result: {payload}")
        return await self.get(fact_id_)

    async def entity(self, subject: str) -> EntityRecord:
        payload = await self._verb("entity", {"name": subject})
        card = payload.get("card") if payload.get("found") else None
        slug = ((card or {}).get("entity") or {}).get("slug") or subject
        facts, omitted = await self._rows(slug, include_expired=True)
        now = datetime.now(UTC)
        current = tuple(f for f in facts if f.valid_at(now))
        history = tuple(f for f in facts if not f.valid_at(now))
        return EntityRecord(
            subject=slug,
            found=bool(card) or bool(facts),
            facts=current,
            history=history,
            card=card,
            omitted=tuple(omitted),
        )
