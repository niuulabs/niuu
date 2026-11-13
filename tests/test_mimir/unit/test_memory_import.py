"""Importing the retired Markdown wiki into a layer, through the backend at operator trust."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from mimir.__main__ import app as cli
from mimir.config import MimirServiceConfig
from mimir.memory.importer import WikiImportError, import_wiki
from mimir.memory.models import Layer, LayerKind
from niuu.domain.mimir import compute_content_hash, compute_source_id
from niuu.domain.mimir_facts import AuthorKind, FactAuthor, FactCaller, TrustLevel
from tests.test_mimir.unit.memory_fakes import MCP, SIGNING_KEY, FakeGBrain, gbrain_backend

OPERATOR = FactCaller(FactAuthor(AuthorKind.OPERATOR, "jozef"))
ORG = Layer(LayerKind.ORG)

PAGE = """---
type: entity
entity_type: person
source_ids: [{source_id}]
---

# Ines Moreau

## Compiled Truth

### Key Facts
- Owns the booking cut-over.

## Timeline

- 2026-03-10: Took over the cut-over. [Source: {source_id}, slack, 2026-03-10]
- 2026-04-01: Signed off the plan. [Source: Jozef, meeting, 2026-04-01]
"""


def _raw(content: str, **extra) -> dict:
    return {
        "source_id": compute_source_id(content),
        "title": "LIN-482",
        "content": content,
        "source_type": "web",
        "origin_url": "https://linear.app/acme/LIN-482",
        "content_hash": compute_content_hash(content),
        "ingested_at": datetime.now(UTC).isoformat(),
        **extra,
    }


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    root = tmp_path / "wiki-root"
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "raw").mkdir()
    raw = _raw("the ticket text")
    (root / "raw" / f"{raw['source_id']}.json").write_text(json.dumps(raw))
    tool = _raw("ls output", source_type="tool_output", origin_url=None)
    (root / "raw" / f"{tool['source_id']}.json").write_text(json.dumps(tool))
    (root / "wiki" / "entities" / "ines-moreau.md").write_text(
        PAGE.format(source_id=raw["source_id"])
    )
    (root / "wiki" / "plain.md").write_text("# Plain\n\nA page without zones.\n")
    (root / "wiki" / "index.md").write_text("- [x](x.md)\n")
    (root / "wiki" / "log.md").write_text("## log\n")
    (root / "wiki" / ".lint-cache.json").write_text("{}")
    (root / "wiki" / "threads").mkdir()
    (root / "wiki" / "threads" / "t.yaml").write_text("title: t\n")
    return root


@pytest.fixture
def brain():
    fake = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=fake.handle)
        yield fake


@pytest.fixture
async def backend(brain):
    store = gbrain_backend()
    yield store
    await store.close()


async def test_pages_timelines_and_sources_are_imported_at_operator_trust(
    brain, backend, wiki
) -> None:
    report = await import_wiki(backend, wiki, ORG, OPERATOR)
    assert (report.pages, report.timeline_rows, report.sources) == (2, 2, 1)
    assert sorted(report.not_imported) == [
        (
            "raw/" + _raw("ls output")["source_id"] + ".json",
            "tool_output is not a source; it is not carried over",
        ),
        ("wiki/.lint-cache.json", "hidden file"),
        ("wiki/index.md", "derived catalogue of the old wiki"),
        ("wiki/log.md", "derived catalogue of the old wiki"),
        ("wiki/threads/t.yaml", "not a Markdown page"),
    ]
    page = await backend.get_page("org", "entities/ines-moreau")
    assert page.title == "Ines Moreau"
    assert page.compiled_truth.startswith("### Key Facts")
    assert page.attribution.trust is TrustLevel.OPERATOR
    assert page.attribution.author == "operator:jozef"
    refs = [e.ref for e in page.attribution.evidence]
    assert refs[-1] == "import:wiki/entities/ines-moreau.md"
    assert refs[0].startswith("source:src_")
    first, second = page.timeline
    assert first.summary == "Took over the cut-over"
    assert first.attribution.evidence[0].ref.startswith("source:src_")
    assert second.detail == "Source: Jozef, meeting, 2026-04-01"
    assert [e.ref for e in second.attribution.evidence] == ["import:wiki/entities/ines-moreau.md"]
    plain = await backend.get_page("org", "plain")
    assert plain.compiled_truth == "# Plain\n\nA page without zones."
    sources = await backend.list_sources("org", limit=10)
    assert [s.title for s in sources] == ["LIN-482"]


async def test_running_it_again_skips_what_is_already_there(backend, wiki) -> None:
    await import_wiki(backend, wiki, ORG, OPERATOR)
    again = await import_wiki(backend, wiki, ORG, OPERATOR)
    assert (again.pages, again.pages_already_imported, again.sources_already_held) == (0, 2, 1)


async def test_a_page_that_differs_from_what_is_imported_stops_the_import(backend, wiki) -> None:
    await import_wiki(backend, wiki, ORG, OPERATOR)
    (wiki / "wiki" / "plain.md").write_text("# Plain\n\nChanged.\n")
    with pytest.raises(WikiImportError, match="already exists .* different content"):
        await import_wiki(backend, wiki, ORG, OPERATOR)


@pytest.mark.parametrize(
    ("name", "content", "match"),
    [
        (
            "undated.md",
            "## Compiled Truth\n\nx\n\n## Timeline\n\n- someday: no date\n",
            "without a date",
        ),
        ("Bad Name.md", "x\n", "could not be imported"),
        ("empty.md", "---\ntype: topic\n---\n", "no content"),
    ],
)
async def test_a_page_it_cannot_import_stops_the_import_naming_the_file(
    backend, wiki, name, content, match
) -> None:
    (wiki / "wiki" / name).write_text(content)
    with pytest.raises(WikiImportError, match=match) as caught:
        await import_wiki(backend, wiki, ORG, OPERATOR)
    assert name in str(caught.value)


async def test_a_raw_source_that_fails_its_integrity_check_stops_the_import(backend, wiki) -> None:
    bad = _raw("original")
    bad["content"] = "tampered"
    (wiki / "raw" / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(WikiImportError, match="integrity"):
        await import_wiki(backend, wiki, ORG, OPERATOR)


async def test_a_source_under_the_older_id_scheme_is_kept_and_its_citations_follow(
    backend, wiki
) -> None:
    """Before 2026-07-25 a source id was not its content hash; a real wiki holds 51 such."""
    old_id = "src_044764104fdb67c9"
    older = _raw("an older ticket text", source_id=old_id)
    (wiki / "raw" / f"{old_id}.json").write_text(json.dumps(older))
    (wiki / "wiki" / "entities" / "older.md").write_text(
        PAGE.format(source_id=old_id).replace("# Ines Moreau", "# Older")
    )

    report = await import_wiki(backend, wiki, ORG, OPERATOR)

    stored = compute_source_id("an older ticket text")
    assert report.rekeyed == {old_id: stored}
    assert "re-keyed" in report.summary()
    page = await backend.get_page("org", "entities/older")
    assert page.attribution.evidence[0].ref == f"source:{stored}"
    assert page.timeline[0].attribution.evidence[0].ref == f"source:{stored}"


async def test_an_older_id_without_a_content_hash_cannot_be_checked(backend, wiki) -> None:
    unchecked = _raw("unverifiable", source_id="src_0000000000000000", content_hash="")
    (wiki / "raw" / "unchecked.json").write_text(json.dumps(unchecked))
    with pytest.raises(WikiImportError, match="no content_hash"):
        await import_wiki(backend, wiki, ORG, OPERATOR)


async def test_a_root_without_a_wiki_and_a_session_layer_are_refused(backend, tmp_path) -> None:
    with pytest.raises(WikiImportError, match="no wiki/"):
        await import_wiki(backend, tmp_path, ORG, OPERATOR)
    with pytest.raises(WikiImportError, match="not session"):
        await import_wiki(backend, tmp_path, Layer(LayerKind.SESSION, "s"), OPERATOR)


def test_the_cli_imports_into_the_tenants_system(brain, wiki, tmp_path, monkeypatch) -> None:
    config = tmp_path / "mimir.yaml"
    config.write_text(
        "memory:\n"
        "  systems:\n"
        "    - name: acme-brain\n"
        "      adapter: mimir.backends.gbrain.GBrainBackend\n"
        f"      mcp_url: {MCP}\n"
        "      api_token: tok\n"
        f"      signing_key: {SIGNING_KEY}\n"
        "  tenants:\n"
        "    acme: acme-brain\n"
    )
    monkeypatch.setitem(MimirServiceConfig.model_config, "yaml_file", [config])
    runner = CliRunner()
    project = "9a0e0000-0000-4000-8000-0000000000aa"
    result = runner.invoke(
        cli,
        ["import-wiki", "--root", str(wiki), "--tenant", "acme", "--layer", f"project:{project}"],
    )
    assert result.exit_code == 0, result.output
    assert "2 page(s)" in result.output
    assert "not imported: wiki/index.md" in result.output
    assert f"l/project-{project}/entities/ines-moreau" in brain.pages

    listed = runner.invoke(
        cli, ["list-proposals", "--tenant", "acme", "--layer", "org", "--limit", "5"]
    )
    assert listed.exit_code == 0, listed.output
    assert "No pending proposals for org." in listed.output

    bad = runner.invoke(
        cli, ["import-wiki", "--root", str(wiki), "--tenant", "x", "--layer", "org"]
    )
    assert bad.exit_code == 1 and "memory.tenants" in bad.output
    session = runner.invoke(
        cli, ["import-wiki", "--root", str(wiki), "--tenant", "acme", "--layer", "session:s"]
    )
    assert session.exit_code != 0
    failing = runner.invoke(
        cli,
        ["import-wiki", "--root", str(tmp_path / "nowhere"), "--tenant", "acme", "--layer", "org"],
    )
    assert failing.exit_code == 1 and "import failed" in failing.output


async def test_an_older_free_form_source_type_is_kept_as_a_document(backend, wiki) -> None:
    paper = _raw("a paper on retrieval", source_type="paper")
    (wiki / "raw" / f"{paper['source_id']}.json").write_text(json.dumps(paper))
    report = await import_wiki(backend, wiki, ORG, OPERATOR)
    assert report.legacy_types == {"paper": 1}
    assert "paper (1)" in report.summary()


async def test_a_source_type_nobody_knows_names_where_to_add_it(backend, wiki) -> None:
    odd = _raw("odd", source_type="hologram")
    (wiki / "raw" / f"{odd['source_id']}.json").write_text(json.dumps(odd))
    with pytest.raises(WikiImportError, match="LEGACY_SOURCE_TYPES"):
        await import_wiki(backend, wiki, ORG, OPERATOR)
