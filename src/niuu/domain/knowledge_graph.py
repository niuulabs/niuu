"""Backend-neutral knowledge graph projected from stored pages, never inferred facts."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from niuu.domain.mimir import MimirPage

# Typed relationship edges (NIU-1058), an additive FORMAT.md extension:
#   - [[slug]] — rel: works_at — description
# Untyped relationship lines remain valid; the rel label is optional metadata.
_TYPED_EDGE_RE = re.compile(
    r"^\s*-\s*\[\[([^\[\]]+)\]\]\s*[—-]+\s*rel:\s*([a-zA-Z_]+)", re.MULTILINE
)


def extract_typed_edges(content: str) -> dict[str, str]:
    """Map wikilink slug → relationship type for typed edge lines."""
    return {
        slug.strip().lower(): rel_type.lower() for slug, rel_type in _TYPED_EDGE_RE.findall(content)
    }


@dataclass
class KnowledgeNode:
    id: str
    title: str
    category: str
    path: str
    kind: str = "page"
    summary: str = ""
    mount: str = ""
    source_ids: list[str] = field(default_factory=list)


@dataclass
class KnowledgeEdge:
    source: str
    target: str
    type: str = "shared_source"


@dataclass
class KnowledgeGraph:
    nodes: list[KnowledgeNode] = field(default_factory=list)
    edges: list[KnowledgeEdge] = field(default_factory=list)


def _key(path: str) -> str:
    return posixpath.normpath(unquote(path).strip().removesuffix(".md")).lstrip("/").casefold()


def project_pages(pages: list[MimirPage]) -> KnowledgeGraph:
    """Expose categories/types plus explicit links and common-source provenance.

    Bare wikilinks resolve only when unambiguous. External URLs and dangling
    references are not invented as pages. Code fences are not knowledge links.
    """
    graph = KnowledgeGraph()
    aliases: dict[str, set[str]] = {}
    sources: dict[str, list[str]] = {}
    for page in pages:
        meta = page.meta
        kind = meta.entity_type or meta.page_type or ("thread" if meta.is_thread else "page")
        graph.nodes.append(
            KnowledgeNode(
                id=meta.path,
                path=meta.path,
                title=meta.title,
                category=meta.category,
                kind=str(kind),
                summary=meta.summary,
                source_ids=meta.source_ids,
            )
        )
        for alias in {_key(meta.path), _key(posixpath.basename(meta.path)), _key(meta.title)}:
            aliases.setdefault(alias, set()).add(meta.path)
        for source in meta.source_ids:
            sources.setdefault(source, []).append(meta.path)

    seen: set[tuple[str, str, str]] = set()

    def add(source: str, target: str, kind: str) -> None:
        key = (source, target, kind)
        if source != target and key not in seen:
            seen.add(key)
            graph.edges.append(KnowledgeEdge(*key))

    exact = {_key(page.meta.path): page.meta.path for page in pages}
    for page in pages:
        body = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1[^\n]*$", "", page.content)
        typed = extract_typed_edges(body)
        links = [
            (m.group(1).split("|", 1)[0], typed.get(m.group(1).strip().lower(), "wikilink"))
            for m in re.finditer(r"\[\[([^\]]+)\]\]", body)
        ]
        links += [
            (m.group(1), "link")
            for m in re.finditer(r"(?<!!)\[[^\]]*\]\(([^\s)]+)(?:[^)]*)\)", body)
        ]
        links += [(target, "related_entity") for target in page.meta.related_entities]
        for target, kind in links:
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            key = _key(parsed.path)
            relative = _key(posixpath.join(posixpath.dirname(page.meta.path), parsed.path))
            resolved = exact.get(relative) or exact.get(key)
            candidates = aliases.get(key, set())
            if resolved is None and len(candidates) == 1:
                resolved = next(iter(candidates))
            if resolved is not None:
                add(page.meta.path, resolved, kind)
    for paths in sources.values():
        for i, source in enumerate(paths):
            for target in paths[i + 1 :]:
                a, b = sorted((source, target))
                add(a, b, "shared_source")
    return graph


def cross_mount_source_edges(page_sources: dict[str, tuple[str, list[str]]]) -> list[KnowledgeEdge]:
    """Connect distinct mounts only when their pages declare the same provenance."""
    sources: dict[str, list[tuple[str, str]]] = {}
    for node_id, (mount, source_ids) in page_sources.items():
        for source_id in source_ids:
            sources.setdefault(source_id, []).append((node_id, mount))
    edges: set[tuple[str, str]] = set()
    for group in sources.values():
        for index, (source, source_mount) in enumerate(group):
            for target, target_mount in group[index + 1 :]:
                if source_mount != target_mount:
                    edges.add(tuple(sorted((source, target))))
    return [KnowledgeEdge(source, target) for source, target in sorted(edges)]
