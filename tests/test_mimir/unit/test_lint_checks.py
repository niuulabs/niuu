"""Unit tests for MarkdownMimirAdapter lint checks L01–L12.

Each test exercises a single check in isolation using a real
MarkdownMimirAdapter backed by a tmp_path filesystem.  No mocking is required
because the adapter writes its own files and the checks read them back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mimir.adapters.markdown import MarkdownMimirAdapter
from niuu.domain.mimir import LintIssue, MimirLintReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _adapter(tmp_path: Path) -> MarkdownMimirAdapter:
    return MarkdownMimirAdapter(root=tmp_path / "mimir")


def _write_page(adapter: MarkdownMimirAdapter, path: str, content: str) -> None:
    """Write a page directly into the wiki, bypassing index update."""
    full = adapter._wiki / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _run(coro):
    return asyncio.run(coro)


def _issue_ids(report: MimirLintReport) -> list[str]:
    return [i.id for i in report.issues]


def _issues_by_id(report: MimirLintReport, check_id: str) -> list[LintIssue]:
    return [i for i in report.issues if i.id == check_id]


# ---------------------------------------------------------------------------
# L01 — orphan pages
# ---------------------------------------------------------------------------


def test_l01_orphan_page_not_in_index(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    # Write a page without adding to index
    _write_page(adapter, "technical/orphan.md", "# Orphan\nThis page has no index entry.")

    report = _run(adapter.lint())

    l01 = _issues_by_id(report, "L01")
    assert len(l01) == 1
    assert l01[0].severity == "warning"
    assert l01[0].page_path == "technical/orphan.md"
    assert not l01[0].auto_fixable


def test_l01_indexed_page_not_orphan(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/indexed.md", "# Indexed\nHas an entry.")
    # Add to index manually
    adapter._index.write_text(
        "# Mímir — content catalog\n\n- [Indexed](technical/indexed.md) — Has an entry.\n",
        encoding="utf-8",
    )

    report = _run(adapter.lint())

    l01 = _issues_by_id(report, "L01")
    assert all(i.page_path != "technical/indexed.md" for i in l01)


# ---------------------------------------------------------------------------
# L02 — contradictions
# ---------------------------------------------------------------------------


def test_l02_contradiction_flag(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(
        adapter,
        "technical/contradict.md",
        "# Contradiction\n[CONTRADICTION] This contradicts earlier claims.",
    )

    report = _run(adapter.lint())

    l02 = _issues_by_id(report, "L02")
    assert len(l02) == 1
    assert l02[0].severity == "warning"


def test_l02_emoji_contradiction(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(
        adapter,
        "technical/emoji.md",
        "# Emoji\n⚠️ Contradiction found here.",
    )

    report = _run(adapter.lint())

    l02 = _issues_by_id(report, "L02")
    assert len(l02) == 1


def test_l02_clean_page_no_flag(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/clean.md", "# Clean\nNo contradictions here.")

    report = _run(adapter.lint())

    assert "L02" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L04 — concept gaps
# ---------------------------------------------------------------------------


def test_l04_concept_mentioned_three_times(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "# Page A\nMentions [[some-concept]] here.\n"
        "Also [[some-concept]] again.\n"
        "And [[some-concept]] a third time.\n"
    )
    _write_page(adapter, "technical/a.md", content)

    report = _run(adapter.lint())

    l04 = _issues_by_id(report, "L04")
    assert any("some-concept" in i.page_path for i in l04)


def test_l04_concept_mentioned_twice_no_gap(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/b.md", "# B\n[[rare-concept]] and [[rare-concept]].\n")

    report = _run(adapter.lint())

    assert "L04" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L05 — broken wikilinks
# ---------------------------------------------------------------------------


def test_l05_broken_wikilink_detected(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/page.md", "# Page\nSee [[nonexistent-page]].\n")

    report = _run(adapter.lint())

    l05 = _issues_by_id(report, "L05")
    assert len(l05) == 1
    assert l05[0].severity == "warning"
    assert "nonexistent-page" in l05[0].message


def test_l05_valid_wikilink_no_issue(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/target.md", "# Target\nContent.")
    _write_page(adapter, "technical/source.md", "# Source\nSee [[target]].\n")

    report = _run(adapter.lint())

    assert "L05" not in _issue_ids(report)


def test_l05_autofix_with_close_match(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/ravn-tools.md", "# Ravn Tools\nContent.")
    _write_page(adapter, "technical/source.md", "# Source\nSee [[ravn-toolz]].\n")

    report = _run(adapter.lint(fix=True))

    # After fix, the broken wikilink should be gone
    fixed_content = (adapter._wiki / "technical/source.md").read_text(encoding="utf-8")
    assert "[[ravn-tools]]" in fixed_content
    # L05 issues should not remain after fix
    assert "L05" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L06 — missing source attribution
# ---------------------------------------------------------------------------


def test_l06_timeline_entry_without_source(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: person\n---\n"
        "# Person\nSome person.\n\n"
        "## Compiled Truth\n\nKnown facts.\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: Something happened without a source.\n"
    )
    _write_page(adapter, "entities/person.md", content)

    report = _run(adapter.lint())

    l06 = _issues_by_id(report, "L06")
    assert len(l06) == 1
    assert l06[0].severity == "error"
    assert not l06[0].auto_fixable


def test_l06_timeline_entry_with_source_ok(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: person\n---\n"
        "# Person\nSome person.\n\n"
        "## Compiled Truth\n\nKnown facts.\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: Something happened. [Source: Alice, Slack, 2026-01-01]\n"
    )
    _write_page(adapter, "entities/person.md", content)

    report = _run(adapter.lint())

    assert "L06" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L07 — thin pages
# ---------------------------------------------------------------------------


def test_l07_thin_compiled_truth(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: concept\n---\n"
        "# Concept\nA concept page.\n\n"
        "## Compiled Truth\n\n"
        "- Only one fact.\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: Created. [Source: Alice, Slack, 2026-01-01]\n"
    )
    _write_page(adapter, "entities/concept.md", content)

    report = _run(adapter.lint())

    l07 = _issues_by_id(report, "L07")
    assert len(l07) == 1
    assert l07[0].severity == "warning"


def test_l07_rich_compiled_truth_not_flagged(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: concept\n---\n"
        "# Concept\nA concept page.\n\n"
        "## Compiled Truth\n\n"
        "- Fact one.\n"
        "- Fact two.\n"
        "- Fact three.\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: Created. [Source: Alice, Slack, 2026-01-01]\n"
    )
    _write_page(adapter, "entities/concept.md", content)

    report = _run(adapter.lint())

    assert "L07" not in _issue_ids(report)


def test_l07_only_checks_mandatory_types(tmp_path: Path) -> None:
    """topic pages are not required to have Compiled Truth, so L07 doesn't apply."""
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: topic\n---\n# Topic\nA topic page.\n\n## Compiled Truth\n\n- Single fact.\n"
    )
    _write_page(adapter, "research/topic.md", content)

    report = _run(adapter.lint())

    assert "L07" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L08 — stale content
# ---------------------------------------------------------------------------


def test_l08_old_page_flagged(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/old.md", "# Old Page\nLast touched long ago.")
    # Back-date the file mtime by 90 days
    wiki_file = adapter._wiki / "technical/old.md"
    import time

    old_mtime = time.time() - 90 * 86400
    import os

    os.utime(wiki_file, (old_mtime, old_mtime))

    report = _run(adapter.lint())

    l08 = _issues_by_id(report, "L08")
    assert len(l08) == 1
    assert l08[0].severity == "info"
    assert l08[0].page_path == "technical/old.md"


def test_l08_recent_page_not_flagged(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/recent.md", "# Recent\nJust updated.")

    report = _run(adapter.lint())

    assert "L08" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L09 — timeline edit detection
# ---------------------------------------------------------------------------


def test_l09_clean_append_not_flagged(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: person\n---\n"
        "# Person\n\n"
        "## Compiled Truth\n\nFacts.\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: Initial entry. [Source: Alice, Slack, 2026-01-01]\n"
    )
    _write_page(adapter, "entities/person.md", content)
    # First lint — establishes baseline cache
    _run(adapter.lint())

    # Append a new entry (append-only, OK)
    appended = content + "- 2026-01-02: Follow-up. [Source: Bob, email, 2026-01-02]\n"
    _write_page(adapter, "entities/person.md", appended)
    report = _run(adapter.lint())

    assert "L09" not in _issue_ids(report)


def test_l09_edit_existing_entry_flagged(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: person\n---\n"
        "# Person\n\n"
        "## Compiled Truth\n\nFacts.\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: Original entry. [Source: Alice, Slack, 2026-01-01]\n"
    )
    _write_page(adapter, "entities/person.md", content)
    _run(adapter.lint())  # establish baseline

    # Edit the existing entry (violation!)
    edited = content.replace("Original entry", "Edited entry")
    _write_page(adapter, "entities/person.md", edited)
    report = _run(adapter.lint())

    l09 = _issues_by_id(report, "L09")
    assert len(l09) == 1
    assert l09[0].severity == "error"


def test_l09_no_cache_no_violation(tmp_path: Path) -> None:
    """First lint run (no cache) should never flag L09."""
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: person\n---\n"
        "# Person\n\n"
        "## Compiled Truth\n\nFacts.\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: Some entry. [Source: Alice, Slack, 2026-01-01]\n"
    )
    _write_page(adapter, "entities/person.md", content)

    report = _run(adapter.lint())

    assert "L09" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L10 — empty compiled truth
# ---------------------------------------------------------------------------


def test_l10_empty_compiled_truth(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: project\n---\n"
        "# Project\n\n"
        "## Compiled Truth\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: Created. [Source: Alice, Slack, 2026-01-01]\n"
    )
    _write_page(adapter, "projects/proj.md", content)

    report = _run(adapter.lint())

    l10 = _issues_by_id(report, "L10")
    assert len(l10) == 1
    assert l10[0].severity == "warning"


def test_l10_no_compiled_truth_section_not_flagged_by_l10(tmp_path: Path) -> None:
    """L10 only fires when the section is present but empty, not when absent."""
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: topic\n---\n# Topic\n\nJust a topic page with no Compiled Truth section.\n"
    )
    _write_page(adapter, "research/topic.md", content)

    report = _run(adapter.lint())

    assert "L10" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L11 — stale index
# ---------------------------------------------------------------------------


def test_l11_page_missing_from_index(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/missing.md", "# Missing\nNot in index.")
    # index.md has no entry for this page (only the header)

    report = _run(adapter.lint())

    l11 = _issues_by_id(report, "L11")
    assert len(l11) == 1
    assert l11[0].severity == "warning"
    assert l11[0].auto_fixable


def test_l11_autofix_rebuilds_index(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/new.md", "# New Page\nFresh content.")

    report = _run(adapter.lint(fix=True))

    # After fix, page should be in index
    index_content = adapter._index.read_text(encoding="utf-8")
    assert "technical/new.md" in index_content
    assert "L11" not in _issue_ids(report)


def test_l11_in_sync_index_not_flagged(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    # Empty wiki — index is in sync (both empty)
    report = _run(adapter.lint())

    assert "L11" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# L12 — invalid frontmatter
# ---------------------------------------------------------------------------


def test_l12_missing_type_field(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(
        adapter,
        "technical/no-type.md",
        "---\nconfidence: high\n---\n# No Type\nMissing the type field.",
    )

    report = _run(adapter.lint())

    l12 = _issues_by_id(report, "L12")
    assert len(l12) == 1
    assert l12[0].severity == "warning"
    assert l12[0].auto_fixable


def test_l12_no_frontmatter_at_all(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/bare.md", "# Bare\nNo frontmatter at all.")

    report = _run(adapter.lint())

    l12 = _issues_by_id(report, "L12")
    assert len(l12) == 1
    assert l12[0].auto_fixable


def test_l12_autofix_adds_type(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(
        adapter,
        "technical/no-type.md",
        "---\nconfidence: medium\n---\n# No Type\nMissing type.",
    )

    report = _run(adapter.lint(fix=True))

    # After fix, file should have the inferred type
    fixed = (adapter._wiki / "technical/no-type.md").read_text(encoding="utf-8")
    assert "type:" in fixed
    # L12 should be gone from the report
    assert "L12" not in _issue_ids(report)


def test_l12_valid_frontmatter_not_flagged(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(
        adapter,
        "technical/valid.md",
        "---\ntype: topic\n---\n# Valid\nHas a type.",
    )

    report = _run(adapter.lint())

    assert "L12" not in _issue_ids(report)


# ---------------------------------------------------------------------------
# Summary and structure
# ---------------------------------------------------------------------------


def test_report_has_pages_checked(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    _write_page(adapter, "technical/p1.md", "# P1\nContent.")
    _write_page(adapter, "technical/p2.md", "# P2\nContent.")

    report = _run(adapter.lint())

    assert report.pages_checked == 2


def test_report_summary_counts_by_severity(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    content = (
        "---\ntype: entity\nentity_type: person\n---\n"
        "# Person\n\n"
        "## Compiled Truth\n\nFacts.\n\n"
        "## Timeline\n\n"
        "- 2026-01-01: No source entry.\n"  # L06 error
    )
    _write_page(adapter, "entities/person.md", content)

    report = _run(adapter.lint())

    assert report.summary["error"] >= 1
    assert isinstance(report.summary["warning"], int)
    assert isinstance(report.summary["info"], int)


def test_report_issues_found_property(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    report = _run(adapter.lint())

    assert report.issues_found is False
    assert report.issues == []


# ---------------------------------------------------------------------------
# Router integration — lint endpoint returns new structure
# ---------------------------------------------------------------------------


def test_router_lint_returns_issues_list(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mimir.router import MimirRouter

    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    _write_page(adapter, "technical/orphan.md", "# Orphan\nNot indexed.")

    router = MimirRouter(adapter=adapter)
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    client = TestClient(app)

    resp = client.get("/mimir/lint")
    assert resp.status_code == 200
    data = resp.json()
    assert "issues" in data
    assert "pages_checked" in data
    assert "issues_found" in data
    assert "summary" in data
    assert isinstance(data["issues"], list)
    for issue in data["issues"]:
        assert "id" in issue
        assert "severity" in issue
        assert "message" in issue
        assert "page_path" in issue
        assert "auto_fixable" in issue


def test_router_lint_fix_endpoint(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mimir.router import MimirRouter

    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    _write_page(adapter, "technical/bare.md", "# Bare\nNo frontmatter.")

    router = MimirRouter(adapter=adapter)
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    client = TestClient(app)

    resp = client.post("/mimir/lint/fix")
    assert resp.status_code == 200
    data = resp.json()
    assert "issues" in data
