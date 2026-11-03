"""E0 replay must not invent artifact titles absent from the capture manifest."""

from __future__ import annotations

import copy

import pytest

from tests.support.artifacts import CATALOG, load, manifest_log_rows
from volundr.domain.artifacts import publications_from_entry

KEY = "QVimZJxaHtjyQfT1R497Kw"
AT = "2026-10-21T01:04:10.334238+00:00"


def _manifest():
    manifest = load("capture-manifest-v1.json")
    publication = next(
        pub for pub in manifest["publications"] if pub["provider_key"] == KEY and pub["at"] == AT
    )
    return {**manifest, "publications": [copy.deepcopy(publication)]}


def _publication(manifest):
    rows = manifest_log_rows(manifest, "thor")
    row = next(row for row in rows if row.seq == 20276)
    [candidate] = publications_from_entry(row, CATALOG)
    assert str(candidate.session_id) == "37436794-168a-4449-926a-6c1c6ac9dd9d"
    assert (candidate.provider_key, candidate.provider_version, candidate.provider_seq) == (
        KEY,
        "1792544649-ca7f",
        5,
    )
    assert candidate.at.isoformat() == AT
    return row.payload["payload"]["tool_response"], candidate


def test_exact_e0_counterexample_has_no_invented_title():
    manifest = _manifest()
    assert "title" not in manifest["publications"][0]
    response, candidate = _publication(manifest)
    assert "title" not in response
    assert candidate.title is None


@pytest.mark.parametrize("title", [None, "", "  ", 7])
def test_unknown_manifest_title_stays_unknown(title):
    manifest = _manifest()
    manifest["publications"][0]["title"] = title
    response, candidate = _publication(manifest)
    assert "title" not in response
    assert candidate.title is None


@pytest.mark.parametrize("title", ["T", "Week-1 lanes — complete project hand-off"])
def test_explicit_captured_title_survives_replay_and_extraction(title):
    manifest = _manifest()
    manifest["publications"][0]["title"] = title
    response, candidate = _publication(manifest)
    assert response["title"] == candidate.title == title


def test_all_122_e0_publications_have_unknown_titles_without_changing_capture():
    manifest = load("capture-manifest-v1.json")
    original = copy.deepcopy(manifest)
    candidates = [
        candidate
        for row in manifest_log_rows(manifest, "thor")
        for candidate in publications_from_entry(row, CATALOG)
    ]
    assert len(candidates) == 122
    assert len({candidate.provider_key for candidate in candidates}) == 12
    assert all(candidate.title is None for candidate in candidates)
    assert manifest == original
