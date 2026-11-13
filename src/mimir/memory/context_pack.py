"""The launch context pack (design section 5, contract C9).

It crosses layers that may live in different stores, so the gateway builds it itself;
gbrain's ``think`` is never used, since it would read across every layer. The gateway
writes no answers with a model: each memory system already searches and synthesises on
its own, and Niuu only searches each one and collates the hits.

The pack holds everything it selects, unbudgeted: the entities that the project brief and
the session's objective lead to (their pages, layer by layer, and the facts that hold now),
then what changed in the project and the org since the owner's previous session.
"""

from __future__ import annotations

from dataclasses import dataclass

from mimir.memory.gateway import MemoryGateway, Session
from mimir.memory.models import HitKind, LayerKind


@dataclass(frozen=True)
class PackItem:
    kind: str
    layer: str
    ref: str
    text: str


@dataclass(frozen=True)
class ContextPack:
    text: str


class ContextPackBuilder:
    """Args:
    gateway: the memory gateway.
    """

    def __init__(self, gateway: MemoryGateway) -> None:
        self._gateway = gateway

    async def context_pack(self, session: Session) -> ContextPack:
        items = await self._pack_items(session)
        header = (
            "Memory context for this session (layers: "
            + ", ".join(layer.id for layer in session.layers)
            + "). Read more with memory_entity and memory_search."
        )
        return ContextPack(text="\n".join([header, *(item.text for item in items)]))

    async def _pack_items(self, session: Session) -> list[PackItem]:
        context = session.agent.context
        items: list[PackItem] = []
        subjects: list[str] = []
        attached: list[tuple[str, str]] = []
        for text in (context.objective, context.project_brief):
            if not text:
                continue
            hits = await self._gateway.search(session, text, kinds=[HitKind.PAGE], limit=None)
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
