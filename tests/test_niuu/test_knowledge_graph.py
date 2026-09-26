from datetime import UTC, datetime

from niuu.domain.knowledge_graph import project_pages
from niuu.domain.mimir import MimirPage, MimirPageMeta, PageType


def page(path, content="", **kwargs):
    kwargs.setdefault("updated_at", datetime.now(UTC))
    return MimirPage(
        MimirPageMeta(
            path=path,
            title=path,
            category="research",
            summary="Retrieval experiments",
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


# ---------------------------------------------------------------------------
# updated_at / first_seen / confidence (3D memory UI graph fields)
# ---------------------------------------------------------------------------


def test_node_first_seen_equals_updated_at_without_timeline():
    updated = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    graph = project_pages([page("a.md", "no timeline here", updated_at=updated)])
    node = graph.nodes[0]
    assert node.updated_at == updated.isoformat()
    assert node.first_seen == updated.isoformat()
    assert node.confidence is None


def test_node_first_seen_from_dated_timeline_entry_older_than_updated_at():
    updated = datetime(2026, 3, 1, tzinfo=UTC)
    content = "## Timeline\n\n- 2020-01-01: Something happened. [Source: test]\n"
    graph = project_pages([page("a.md", content, updated_at=updated)])
    node = graph.nodes[0]
    assert node.first_seen == "2020-01-01T00:00:00+00:00"
    assert node.first_seen != node.updated_at


def test_node_first_seen_picks_the_earliest_of_several_timeline_entries():
    updated = datetime(2026, 3, 1, tzinfo=UTC)
    content = (
        "## Timeline\n\n"
        "- 2021-06-01: Middle. [Source: test]\n"
        "- 2019-12-25: Earliest. [Source: test]\n"
        "- 2022-01-01: Latest. [Source: test]\n"
    )
    graph = project_pages([page("a.md", content, updated_at=updated)])
    assert graph.nodes[0].first_seen == "2019-12-25T00:00:00+00:00"


def test_node_first_seen_falls_back_to_updated_at_when_timeline_entries_are_newer():
    updated = datetime(2019, 1, 1, tzinfo=UTC)
    content = "## Timeline\n\n- 2026-01-01: Newer than updated_at. [Source: test]\n"
    graph = project_pages([page("a.md", content, updated_at=updated)])
    node = graph.nodes[0]
    assert node.first_seen == updated.isoformat()


def test_node_first_seen_ignores_undated_timeline_entries():
    updated = datetime(2026, 3, 1, tzinfo=UTC)
    content = "## Timeline\n\n- Undated entry with no date prefix. [Source: test]\n"
    graph = project_pages([page("a.md", content, updated_at=updated)])
    assert graph.nodes[0].first_seen == updated.isoformat()


def test_node_confidence_reflects_frontmatter_value():
    from niuu.domain.mimir import PageConfidence

    updated = datetime(2026, 3, 1, tzinfo=UTC)
    graph = project_pages([page("a.md", updated_at=updated, confidence=PageConfidence.high)])
    assert graph.nodes[0].confidence == "high"


def test_node_first_seen_handles_naive_updated_at():
    """A naive updated_at (no tzinfo) is treated as UTC for comparison,

    matching niuu.domain.timeline's UTC-midnight Timeline dates."""
    updated = datetime(2026, 3, 1)  # naive
    content = "## Timeline\n\n- 2020-01-01: Older. [Source: test]\n"
    graph = project_pages([page("a.md", content, updated_at=updated)])
    node = graph.nodes[0]
    assert node.updated_at == "2026-03-01T00:00:00+00:00"
    assert node.first_seen == "2020-01-01T00:00:00+00:00"


def test_node_dates_are_utc_qualified_without_timeline():
    """A naive updated_at never reaches the client unqualified (it would read as local time)."""
    graph = project_pages([page("a.md", "no timeline", updated_at=datetime(2026, 3, 1, 9, 30))])
    node = graph.nodes[0]
    assert node.updated_at == "2026-03-01T09:30:00+00:00"
    assert node.first_seen == "2026-03-01T09:30:00+00:00"
