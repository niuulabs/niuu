"""Cited answers and the context pack (design section 5, contract C9).

Both cross layers that may live in different stores, so the gateway builds them
itself; gbrain's ``think`` is never used, since it would read across every layer.

**memory_ask.** The question is searched across the session's layers; the hits'
pages and sources are read in full from the layer each came from, and the facts
that hold now about each page's subject are recalled. That is the retrieved set.
One answer call goes to the configured model through Bifröst, made as whoever asked
(the session's own credential, charged to its owner; or a person's own login, charged
to them), with no output cap; a cut-off answer raises (``require_complete``). Every
citation must name a record of the retrieved set, and an answer that says something must cite it, or
the answer is refused and counted. Disagreements found in the retrieved set are
listed as gaps. An answer is never stored, so it is never evidence.

**Context pack.** Holds everything it selects, unbudgeted: the entities that the
project brief and the session's objective lead to (their pages, layer by layer,
and the facts that hold now), then what changed in the project and the org since
the owner's previous session.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn

from mimir.memory.gateway import (
    Disagreement,
    LabelledFact,
    LabelledPage,
    MemoryGateway,
    Session,
    fact_disagreements,
    page_disagreements,
)
from mimir.memory.models import HitKind, Layer, LayerKind, MemoryStoreError
from mimir.memory.review import subject_title
from mimir.ports.answer_model import AnswerModel, ModelCaller
from niuu.cap_telemetry import record_cap_hit, require_complete

ANSWER_KNOB = "memory.answer_model"
RETRIEVAL_KNOB = "memory.ask_retrieval_count"
CITATION_KNOB = "memory.ask.citations"

_SYSTEM = (
    "You answer questions from a team's memory. You are given numbered records, each "
    "headed by its layer and ref. Answer only from these records; never from your own "
    "knowledge. Cite every record your answer rests on by its exact layer and ref. When "
    "the records do not answer part of the question, or disagree, say so as a gap. "
    "Reply with one JSON object and nothing else: "
    '{"answer": "<the answer, or empty when the records do not answer it>", '
    '"citations": [{"layer": "<layer>", "ref": "<ref>"}], "gaps": ["<what is not known>"]}'
)
_FENCE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.DOTALL)


class AnswerRefusedError(MemoryStoreError):
    """The model's answer broke the rules (uncited, citing outside retrieval, malformed)."""


# ---------------------------------------------------------------------------
# memory_ask
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Retrieved:
    layer: Layer
    ref: str
    kind: str
    text: str
    trust: str
    attested: bool
    #: What a person sees for the record: its title, and where it came from when it has
    #: an origin (a source's URL).
    title: str = ""
    url: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.layer.id, self.ref)


@dataclass(frozen=True)
class Cited:
    """A record an answer cites, as a person opens it."""

    layer: str
    ref: str
    kind: str
    title: str
    url: str | None


@dataclass(frozen=True)
class Answer:
    answer: str
    citations: tuple[tuple[str, str], ...]
    gaps: tuple[str, ...]
    retrieved: tuple[tuple[str, str], ...] = ()
    cited: tuple[Cited, ...] = ()


# ---------------------------------------------------------------------------
# Context pack
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackItem:
    kind: str
    layer: str
    ref: str
    text: str


@dataclass(frozen=True)
class ContextPack:
    text: str


class MemoryAnswers:
    """Args:
    gateway: the memory gateway.
    model: the answer calls.
    answer_model: the model memory_ask answers with (``memory.answer_model``).
    retrieval_count: search hits memory_ask reads (``memory.ask_retrieval_count``).
    """

    def __init__(
        self,
        gateway: MemoryGateway,
        model: AnswerModel,
        *,
        answer_model: str,
        retrieval_count: int,
    ) -> None:
        if retrieval_count < 1:
            raise ValueError(f"{RETRIEVAL_KNOB} must be at least 1")
        self._gateway = gateway
        self._model = model
        self._answer_model = answer_model.strip()
        self._retrieval_count = retrieval_count

    @property
    def asks(self) -> bool:
        """Whether memory_ask is served (an answer model is configured)."""
        return bool(self._answer_model)

    async def close(self) -> None:
        await self._model.close()

    # -- retrieval -------------------------------------------------------

    async def retrieve(
        self, session: Session, question: str, layers: Sequence[str] | None
    ) -> tuple[list[Retrieved], list[Disagreement], list[str]]:
        """The records a question leads to, the disagreements among them, and gaps."""
        gaps: list[str] = []
        hits = await self._gateway.search(
            session, question, layers=layers, limit=self._retrieval_count
        )
        if len(hits) >= self._retrieval_count:
            message = (
                f"memory_ask read the top {self._retrieval_count} search hits; more records may "
                f"match. Raise {RETRIEVAL_KNOB} or ask a narrower question"
            )
            record_cap_hit(RETRIEVAL_KNOB, message)
            gaps.append(message)
        found: dict[tuple[str, str], Retrieved] = {}
        pages: list[LabelledPage] = []
        subjects: list[str] = []
        for hit in hits:
            if hit.hit.kind is HitKind.SOURCE:
                result = await self._gateway.read(
                    session, f"source:{hit.hit.ref}", layer=hit.layer.id
                )
                for labelled in result.sources:
                    record = Retrieved(
                        labelled.layer,
                        f"source:{labelled.source.id}",
                        "source",
                        f"{labelled.source.title}\n{labelled.source.content}",
                        labelled.source.attribution.trust.label,
                        labelled.source.attribution.attested,
                        title=labelled.source.title,
                        url=labelled.source.origin_url,
                    )
                    found.setdefault(record.key, record)
                continue
            result = await self._gateway.read(session, hit.hit.ref, layer=hit.layer.id)
            for labelled in result.pages:
                pages.append(labelled)
                page = labelled.page
                rows = "\n".join(f"- {row.on.isoformat()}: {row.summary}" for row in page.timeline)
                record = Retrieved(
                    labelled.layer,
                    page.slug,
                    "page",
                    f"{page.title}\n{page.compiled_truth}"
                    + (f"\nTimeline:\n{rows}" if rows else ""),
                    page.attribution.trust.label,
                    page.attribution.attested,
                    title=page.title or subject_title(page.slug),
                )
                found.setdefault(record.key, record)
                # Facts hang off entities of the org's own layers; an attached store has none.
                if labelled.layer.kind is not LayerKind.ATTACHED and page.slug not in subjects:
                    subjects.append(page.slug)
        facts: list[LabelledFact] = []
        for subject in subjects:
            recalled = await self._gateway.recall(session, subject)
            facts.extend(recalled.facts)
            gaps.extend(
                f"Left out of the facts about {subject}: {o.detail}" for o in recalled.omitted
            )
        for labelled in facts:
            fact = labelled.fact
            record = Retrieved(
                labelled.layer,
                f"fact:{fact.id}",
                "fact",
                f"about {fact.subject} ({fact.kind.value}): {fact.statement}",
                fact.trust.label,
                fact.provenance.attested,
                title=subject_title(fact.subject),
            )
            found.setdefault(record.key, record)
        disagreements = [*page_disagreements(pages), *fact_disagreements(facts)]
        return list(found.values()), disagreements, gaps

    # -- memory_ask ------------------------------------------------------

    async def ask(
        self, session: Session, question: str, *, layers: Sequence[str] | None, caller: ModelCaller
    ) -> Answer:
        if not self.asks:
            raise AnswerRefusedError("memory_ask is not enabled on this gateway")
        if not question.strip():
            raise AnswerRefusedError("memory_ask needs a question")
        records, disagreements, gaps = await self.retrieve(session, question, layers)
        gaps.extend(f"Disagreement: {d.note}" for d in disagreements)
        if not records:
            gaps.insert(0, "Nothing in your layers matches this question.")
            return Answer(answer="", citations=(), gaps=tuple(gaps))
        prompt = self._prompt(question, records)
        reply = await self._model.answer(
            model=self._answer_model, system=_SYSTEM, prompt=prompt, caller=caller
        )
        require_complete(
            reply, knob=ANSWER_KNOB, budget=None, model=self._answer_model, purpose="memory_ask"
        )
        answer, citations, model_gaps = self._parse(reply.content)
        retrieved = {r.key for r in records}
        outside = [c for c in citations if c not in retrieved]
        if outside:
            refs = ", ".join(f"{layer}:{ref}" for layer, ref in outside)
            self._refuse(f"the answer cites records that were not retrieved: {refs}")
        if answer.strip() and not citations:
            self._refuse("the answer says something but cites no record")
        by_key = {r.key: r for r in records}
        cited = tuple(dict.fromkeys(citations))
        return Answer(
            answer=answer,
            citations=cited,
            gaps=tuple([*model_gaps, *gaps]),
            retrieved=tuple(r.key for r in records),
            cited=tuple(
                Cited(
                    layer,
                    ref,
                    by_key[(layer, ref)].kind,
                    by_key[(layer, ref)].title,
                    by_key[(layer, ref)].url,
                )
                for layer, ref in cited
            ),
        )

    @staticmethod
    def _refuse(message: str) -> NoReturn:
        record_cap_hit(CITATION_KNOB, f"memory_ask answer refused: {message}")
        raise AnswerRefusedError(f"the answer was refused: {message}. Ask again")

    @staticmethod
    def _prompt(question: str, records: Sequence[Retrieved]) -> str:
        blocks = []
        for index, record in enumerate(records, start=1):
            flag = "" if record.attested else ", unattested"
            blocks.append(
                f"[{index}] layer={record.layer.id} ref={record.ref} "
                f"({record.kind}, {record.trust} trust{flag})\n{record.text}"
            )
        return f"Question: {question}\n\nRecords:\n\n" + "\n\n".join(blocks)

    def _parse(self, content: str) -> tuple[str, list[tuple[str, str]], list[str]]:
        text = content.strip()
        fenced = _FENCE.match(text)
        if fenced:
            text = fenced.group("body")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self._refuse("the model did not answer with the required JSON object")
        if not isinstance(data, dict):
            self._refuse("the model's answer is not a JSON object")
        answer = data.get("answer")
        citations = data.get("citations")
        gaps = data.get("gaps", [])
        if not isinstance(answer, str) or not isinstance(citations, list):
            self._refuse("the model's answer lacks 'answer' or 'citations'")
        if not isinstance(gaps, list) or not all(isinstance(g, str) for g in gaps):
            self._refuse("the model's 'gaps' is not a list of strings")
        cited: list[tuple[str, str]] = []
        for item in citations:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("layer"), str)
                or not isinstance(item.get("ref"), str)
            ):
                self._refuse(f"citation {item!r} is not {{layer, ref}}")
            cited.append((item["layer"].strip(), item["ref"].strip()))
        return answer, cited, list(gaps)

    # -- context pack ----------------------------------------------------

    async def context_pack(self, session: Session) -> ContextPack:
        items = await self._pack_items(session)
        header = (
            "Memory context for this session (layers: "
            + ", ".join(layer.id for layer in session.layers)
            + "). Read more with memory_entity, memory_search and memory_ask."
        )
        return ContextPack(text="\n".join([header, *(item.text for item in items)]))

    async def _pack_items(self, session: Session) -> list[PackItem]:
        context = session.agent.context
        items: list[PackItem] = []
        subjects: list[str] = []
        attached: list[tuple[str, str]] = []
        limit = self._gateway.default_result_count
        for label, text in (("objective", context.objective), ("brief", context.project_brief)):
            if not text:
                continue
            hits = await self._gateway.search(session, text, kinds=[HitKind.PAGE], limit=limit)
            if len(hits) >= limit:
                record_cap_hit(
                    "memory.default_result_count",
                    f"context pack: only the top {limit} pages the session's {label} leads to "
                    "were considered; raise memory.default_result_count to consider more",
                )
            for hit in hits:
                if hit.layer.kind is LayerKind.ATTACHED:
                    # An attached store's page is its own subject, read from that layer only.
                    if (hit.layer.id, hit.hit.ref) not in attached:
                        attached.append((hit.layer.id, hit.hit.ref))
                elif hit.hit.ref not in subjects:
                    subjects.append(hit.hit.ref)
        for layer_id, ref in attached:
            for labelled in (await self._gateway.read(session, ref, layer=layer_id)).pages:
                items.append(
                    PackItem(
                        "page",
                        layer_id,
                        ref,
                        f"## {ref} [{layer_id}]\n{labelled.page.compiled_truth}",
                    )
                )
        for subject in subjects:
            card = await self._gateway.entity(session, subject)
            for labelled in card.pages:
                items.append(
                    PackItem(
                        "page",
                        labelled.layer.id,
                        subject,
                        f"## {subject} [{labelled.layer.id}]\n{labelled.page.compiled_truth}",
                    )
                )
            for labelled in card.facts:
                fact = labelled.fact
                items.append(
                    PackItem(
                        "fact",
                        labelled.layer.id,
                        f"fact:{fact.id}",
                        f"- [{labelled.layer.id}, {fact.trust.label}] {fact.subject}: "
                        f"{fact.statement} (fact:{fact.id})",
                    )
                )
        if context.since is not None:
            long_lived = [
                layer
                for layer in session.system_layers
                if layer.kind in (LayerKind.PROJECT, LayerKind.ORG)
            ]
            changes = await self._gateway.changes(session, context.since, layers=long_lived)
            for layer, batch in changes:
                for page in batch.pages:
                    when = page.updated_at.isoformat() if page.updated_at else "since then"
                    items.append(
                        PackItem(
                            "change",
                            layer.id,
                            page.slug,
                            f"- changed [{layer.id}] {page.slug} ({page.title}), {when}",
                        )
                    )
                for labelled in batch.facts:
                    items.append(
                        PackItem(
                            "change",
                            layer.id,
                            f"fact:{labelled.fact.id}",
                            f"- new fact [{layer.id}] {labelled.fact.subject}: "
                            f"{labelled.fact.statement} (fact:{labelled.fact.id})",
                        )
                    )
        return items
