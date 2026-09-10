from datetime import UTC, datetime

from niuu.domain.knowledge_graph import project_pages
from niuu.domain.mimir import MimirPage, MimirPageMeta, PageType


def page(path, content="", **kwargs):
    return MimirPage(
        MimirPageMeta(
            path=path,
            title=path,
            category="research",
            summary="Retrieval experiments",
            updated_at=datetime.now(UTC),
            **kwargs,
        ),
        content,
    )


def test_graph_projects_real_relationships_and_metadata():
    graph = project_pages(
        [
            page(
                "research/a.md",
                "[[b|Experiment]] [details](../topics/c.md) [[missing]]",
                source_ids=["source"],
                page_type=PageType.observation,
            ),
            page("research/b.md", source_ids=["source"]),
            page("topics/c.md"),
        ]
    )
    assert graph.nodes[0].kind == "observation"
    assert graph.nodes[0].category == "research"
    assert graph.nodes[0].summary == "Retrieval experiments"
    assert {(e.source, e.target, e.type) for e in graph.edges} == {
        ("research/a.md", "research/b.md", "wikilink"),
        ("research/a.md", "topics/c.md", "link"),
        ("research/a.md", "research/b.md", "shared_source"),
    }


def test_ambiguous_links_external_urls_and_code_are_not_edges():
    graph = project_pages(
        [
            page("a.md", "[[b]] [remote](https://example.org/b)\n```md\n[[c]]\n```\n"),
            page("one/b.md"),
            page("two/b.md"),
            page("c.md"),
        ]
    )
    assert not graph.edges


def test_explicit_related_entities_and_duplicate_sources():
    graph = project_pages(
        [
            page("a.md", related_entities=["b"], source_ids=["x", "x"]),
            page("b.md", source_ids=["x"]),
        ]
    )
    assert [(e.source, e.target, e.type) for e in graph.edges] == [
        ("a.md", "b.md", "related_entity"),
        ("a.md", "b.md", "shared_source"),
    ]


def test_typed_relationships_survive_the_common_projection():
    graph = project_pages([page("a.md", "- [[b]] — rel: supports — evidence"), page("b.md")])
    assert [(e.target, e.type) for e in graph.edges] == [("b.md", "supports")]
