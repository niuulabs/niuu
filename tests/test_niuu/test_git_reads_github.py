"""GitHub reads: one commit, a comparison, and CI for a ref with no pull request."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from niuu.adapters.outbound.github import GitHubProvider, _QueryStringRedactionFilter
from niuu.domain.delivery import CheckConclusion
from niuu.domain.models import CIStatus
from niuu.ports.git import (
    AttestationNotFoundError,
    GitAuthError,
    GitProviderResponseError,
    GitRepoNotFoundError,
)

REPO = "https://github.com/niuulabs/niuu"
API = "https://api.github.com/repos/niuulabs/niuu"
SHA = "a" * 40
PARENT = "b" * 40


@pytest.fixture
def provider() -> GitHubProvider:
    return GitHubProvider(name="GitHub", base_url="https://api.github.com", token="t")


def _commit_body(**overrides) -> dict:
    body = {
        "sha": SHA,
        "html_url": f"{REPO}/commit/{SHA}",
        "parents": [{"sha": PARENT}],
        "commit": {
            "message": "feat: add the thing\n\nbody text",
            "author": {
                "name": "Regin Coder",
                "email": "regin@example.test",
                "date": "2026-10-26T10:00:00Z",
            },
        },
        "stats": {"additions": 5, "deletions": 2, "total": 7},
        "files": [
            {
                "filename": "src/a.py",
                "status": "modified",
                "additions": 3,
                "deletions": 2,
                "patch": "@@ -1 +1 @@\n-old\n+new",
            },
            {"filename": "logo.png", "status": "added", "additions": 0, "deletions": 0},
            {
                "filename": "docs/b.md",
                "status": "renamed",
                "previous_filename": "docs/a.md",
                "additions": 0,
                "deletions": 0,
            },
            {"filename": "big.json", "status": "modified", "additions": 900, "deletions": 4},
        ],
    }
    body.update(overrides)
    return body


@respx.mock
async def test_get_commit_maps_identity_message_and_files(provider: GitHubProvider) -> None:
    route = respx.get(f"{API}/commits/regin").mock(
        return_value=httpx.Response(200, json=_commit_body())
    )

    commit = await provider.get_commit(REPO, "regin")

    assert route.called
    assert commit.sha == SHA
    assert commit.parents == (PARENT,)
    assert commit.author_name == "Regin Coder"
    assert commit.author_email == "regin@example.test"
    assert commit.authored_at == datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    assert commit.message.startswith("feat: add the thing")
    assert (commit.additions, commit.deletions) == (5, 2)
    assert commit.files_total == 4
    by_path = {f.path: f for f in commit.files}
    assert by_path["src/a.py"].patch == "@@ -1 +1 @@\n-old\n+new"
    assert by_path["src/a.py"].patch_omitted_reason is None
    assert by_path["docs/b.md"].previous_path == "docs/a.md"
    assert by_path["docs/b.md"].patch_omitted_reason == (
        "no line changes (rename, copy or mode change)"
    )
    assert by_path["big.json"].patch is None
    assert "too large" in by_path["big.json"].patch_omitted_reason
    assert by_path["big.json"].additions == 900


@respx.mock
async def test_get_commit_follows_file_pagination_without_duplicating(
    provider: GitHubProvider,
) -> None:
    next_url = f"{API}/commits/regin?per_page=100&page=2"
    respx.get(f"{API}/commits/regin", params={"page": 2}).mock(
        return_value=httpx.Response(
            200,
            json=_commit_body(
                files=[
                    {"filename": "src/a.py", "status": "modified", "additions": 3, "deletions": 2},
                    {"filename": "src/z.py", "status": "added", "additions": 1, "deletions": 0},
                ]
            ),
        )
    )
    respx.get(f"{API}/commits/regin").mock(
        return_value=httpx.Response(
            200,
            json=_commit_body(),
            headers={"link": f'<{next_url}>; rel="next"'},
        )
    )

    commit = await provider.get_commit(REPO, "regin")

    assert [f.path for f in commit.files] == [
        "src/a.py",
        "logo.png",
        "docs/b.md",
        "big.json",
        "src/z.py",
    ]
    assert commit.files_total == 5


@respx.mock
async def test_get_commit_quotes_slashes_in_branch_refs(provider: GitHubProvider) -> None:
    route = respx.get(f"{API}/commits/feat/x").mock(
        return_value=httpx.Response(200, json=_commit_body())
    )

    await provider.get_commit(REPO, "feat/x")

    assert route.called


@respx.mock
async def test_get_commit_pagination_that_never_advances_is_fatal(
    provider: GitHubProvider,
) -> None:
    loop = f"{API}/commits/regin?per_page=100&page=2"
    respx.get(f"{API}/commits/regin").mock(
        return_value=httpx.Response(
            200, json=_commit_body(), headers={"link": f'<{loop}>; rel="next"'}
        )
    )

    with pytest.raises(GitProviderResponseError, match="did not advance"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_get_commit_without_a_sha_is_fatal(provider: GitHubProvider) -> None:
    respx.get(f"{API}/commits/regin").mock(
        return_value=httpx.Response(200, json=_commit_body(sha=""))
    )

    with pytest.raises(GitProviderResponseError, match="omitted its SHA"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_get_commit_with_a_garbled_date_is_fatal(provider: GitHubProvider) -> None:
    body = _commit_body()
    body["commit"]["author"]["date"] = "yesterday"
    respx.get(f"{API}/commits/regin").mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(GitProviderResponseError, match="unparseable timestamp"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_get_commit_tolerates_a_commit_without_author_or_date(
    provider: GitHubProvider,
) -> None:
    respx.get(f"{API}/commits/regin").mock(
        return_value=httpx.Response(
            200, json={"sha": SHA, "commit": {}, "parents": [], "files": []}
        )
    )

    commit = await provider.get_commit(REPO, "regin")

    assert commit.authored_at is None
    assert commit.author_name == ""
    assert commit.files == ()


@pytest.mark.parametrize("status", [404, 422])
@respx.mock
async def test_unknown_ref_or_repo_is_not_found(provider: GitHubProvider, status: int) -> None:
    respx.get(f"{API}/commits/nope").mock(return_value=httpx.Response(status, json={}))

    with pytest.raises(GitRepoNotFoundError, match="nope"):
        await provider.get_commit(REPO, "nope")


@respx.mock
async def test_refused_credentials_are_an_auth_error_naming_the_remedy(
    provider: GitHubProvider,
) -> None:
    respx.get(f"{API}/commits/regin").mock(return_value=httpx.Response(403, json={}))

    with pytest.raises(GitAuthError, match="'repo' scope"):
        await provider.get_commit(REPO, "regin")


@pytest.mark.parametrize(
    ("status", "headers"),
    [(403, {"x-ratelimit-remaining": "0"}), (429, {})],
)
@respx.mock
async def test_rate_limit_is_a_provider_error_not_an_auth_error(
    provider: GitHubProvider, status: int, headers: dict
) -> None:
    respx.get(f"{API}/commits/regin").mock(
        return_value=httpx.Response(status, json={}, headers=headers)
    )

    with pytest.raises(httpx.HTTPStatusError, match="rate limit exhausted"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_provider_outage_surfaces_as_http_error(provider: GitHubProvider) -> None:
    respx.get(f"{API}/commits/regin").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_unexpected_success_status_is_fatal(provider: GitHubProvider) -> None:
    respx.get(f"{API}/commits/regin").mock(return_value=httpx.Response(204))

    with pytest.raises(GitProviderResponseError, match="Unexpected HTTP 204"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_later_page_failure_is_typed_like_the_first(provider: GitHubProvider) -> None:
    next_url = f"{API}/commits/regin?per_page=100&page=2"
    respx.get(f"{API}/commits/regin", params={"page": 2}).mock(return_value=httpx.Response(404))
    respx.get(f"{API}/commits/regin").mock(
        return_value=httpx.Response(
            200, json=_commit_body(), headers={"link": f'<{next_url}>; rel="next"'}
        )
    )

    with pytest.raises(GitRepoNotFoundError):
        await provider.get_commit(REPO, "regin")


@pytest.mark.parametrize("call", ["commit", "compare", "checks"])
async def test_unparseable_repo_url_is_not_found(provider: GitHubProvider, call: str) -> None:
    with pytest.raises(GitRepoNotFoundError, match="Cannot parse repository URL"):
        if call == "commit":
            await provider.get_commit("not a url", "x")
        elif call == "compare":
            await provider.compare_refs("not a url", "a", "b")
        else:
            await provider.get_ref_checks("not a url", "x")


def _compare_body(**overrides) -> dict:
    body = {
        "status": "diverged",
        "ahead_by": 2,
        "behind_by": 1,
        "total_commits": 2,
        "html_url": f"{REPO}/compare/main...regin",
        "merge_base_commit": {"sha": PARENT},
        "commits": [
            {
                "sha": "1" * 40,
                "html_url": f"{REPO}/commit/{'1' * 40}",
                "commit": {
                    "message": "feat: one\n\nlong body",
                    "author": {"name": "A", "date": "2026-10-25T09:00:00Z"},
                },
            },
            {
                "sha": "2" * 40,
                "html_url": f"{REPO}/commit/{'2' * 40}",
                "commit": {"message": "", "author": {"name": "B"}},
            },
        ],
        "files": [
            {
                "filename": "src/a.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ x @@",
            }
        ],
    }
    body.update(overrides)
    return body


@respx.mock
async def test_compare_maps_counts_commits_and_files(provider: GitHubProvider) -> None:
    respx.get(f"{API}/compare/main...regin").mock(
        return_value=httpx.Response(200, json=_compare_body())
    )

    comparison = await provider.compare_refs(REPO, "main", "regin")

    assert (comparison.status, comparison.ahead_by, comparison.behind_by) == ("diverged", 2, 1)
    assert comparison.merge_base_sha == PARENT
    assert comparison.url.endswith("/compare/main...regin")
    assert [c.subject for c in comparison.commits] == ["feat: one", ""]
    assert comparison.commits[0].authored_at == datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    assert comparison.commits[1].authored_at is None
    assert [f.path for f in comparison.files] == ["src/a.py"]
    assert comparison.files_total == 1


@respx.mock
async def test_compare_follows_pages_and_ignores_repeated_entries(
    provider: GitHubProvider,
) -> None:
    body = _compare_body()
    page_two = _compare_body(
        commits=[
            body["commits"][1],
            {
                "sha": "3" * 40,
                "html_url": "u",
                "commit": {"message": "three", "author": {"name": "C"}},
            },
        ],
        total_commits=3,
        files=[
            body["files"][0],
            {"filename": "src/b.py", "status": "added", "additions": 1, "deletions": 0},
        ],
    )
    body["total_commits"] = 3
    next_url = f"{API}/compare/main...regin?per_page=100&page=2"
    respx.get(f"{API}/compare/main...regin", params={"page": 2}).mock(
        return_value=httpx.Response(200, json=page_two)
    )
    respx.get(f"{API}/compare/main...regin").mock(
        return_value=httpx.Response(200, json=body, headers={"link": f'<{next_url}>; rel="next"'})
    )

    comparison = await provider.compare_refs(REPO, "main", "regin")

    assert [c.sha[0] for c in comparison.commits] == ["1", "2", "3"]
    assert [f.path for f in comparison.files] == ["src/a.py", "src/b.py"]


@respx.mock
async def test_compare_that_returns_fewer_commits_than_reported_is_fatal(
    provider: GitHubProvider,
) -> None:
    respx.get(f"{API}/compare/main...regin").mock(
        return_value=httpx.Response(200, json=_compare_body(total_commits=300))
    )

    with pytest.raises(GitProviderResponseError, match="incomplete"):
        await provider.compare_refs(REPO, "main", "regin")


@respx.mock
async def test_compare_with_an_unknown_ref_is_not_found(provider: GitHubProvider) -> None:
    respx.get(f"{API}/compare/main...ghost").mock(return_value=httpx.Response(404))

    with pytest.raises(GitRepoNotFoundError, match="ghost"):
        await provider.compare_refs(REPO, "main", "ghost")


def _status_body(statuses: list[dict] | None = None, sha: str = SHA) -> dict:
    return {"sha": sha, "state": "failure", "statuses": statuses or []}


@respx.mock
async def test_ref_checks_reports_runs_and_statuses_worst_first(
    provider: GitHubProvider,
) -> None:
    respx.get(f"{API}/commits/regin/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"name": "lint", "status": "completed", "conclusion": "success"},
                    {
                        "name": "backend",
                        "status": "completed",
                        "conclusion": "failure",
                        "details_url": "https://ci.test/backend",
                    },
                    {"name": "web", "status": "in_progress", "conclusion": None},
                ]
            },
        )
    )
    respx.get(f"{API}/commits/regin/status").mock(
        return_value=httpx.Response(
            200,
            json=_status_body(
                [{"context": "codecov/patch", "state": "success", "target_url": "https://c.test"}]
            ),
        )
    )

    result = await provider.get_ref_checks(REPO, "regin")

    assert result.sha == SHA
    assert result.ref == "regin"
    assert [c.name for c in result.checks] == ["backend", "web", "codecov/patch", "lint"]
    assert result.checks[0].conclusion is CheckConclusion.FAILING
    assert result.checks[0].details_url == "https://ci.test/backend"
    assert result.status is CIStatus.FAILING
    assert result.counts() == {"failing": 1, "pending": 1, "passing": 2}


@respx.mock
async def test_ref_checks_with_nothing_run_is_unknown_not_passing(
    provider: GitHubProvider,
) -> None:
    respx.get(f"{API}/commits/regin/check-runs").mock(
        return_value=httpx.Response(200, json={"check_runs": []})
    )
    respx.get(f"{API}/commits/regin/status").mock(
        return_value=httpx.Response(200, json=_status_body())
    )

    result = await provider.get_ref_checks(REPO, "regin")

    assert result.checks == ()
    assert result.status is CIStatus.UNKNOWN


@respx.mock
async def test_ref_checks_without_a_sha_is_fatal(provider: GitHubProvider) -> None:
    respx.get(f"{API}/commits/regin/check-runs").mock(
        return_value=httpx.Response(200, json={"check_runs": []})
    )
    respx.get(f"{API}/commits/regin/status").mock(
        return_value=httpx.Response(200, json={"statuses": []})
    )

    with pytest.raises(GitProviderResponseError, match="omitted the commit SHA"):
        await provider.get_ref_checks(REPO, "regin")


@pytest.mark.parametrize("failing", ["check-runs", "status"])
@respx.mock
async def test_ref_checks_unknown_ref_is_not_found(provider: GitHubProvider, failing: str) -> None:
    respx.get(f"{API}/commits/ghost/check-runs").mock(
        return_value=(
            httpx.Response(404)
            if failing == "check-runs"
            else httpx.Response(200, json={"check_runs": []})
        )
    )
    respx.get(f"{API}/commits/ghost/status").mock(return_value=httpx.Response(404))

    with pytest.raises(GitRepoNotFoundError, match="ghost"):
        await provider.get_ref_checks(REPO, "ghost")


@respx.mock
async def test_ref_checks_rate_limit_is_a_provider_error(provider: GitHubProvider) -> None:
    respx.get(f"{API}/commits/regin/check-runs").mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    )

    with pytest.raises(httpx.HTTPStatusError, match="rate limit exhausted"):
        await provider.get_ref_checks(REPO, "regin")


# --- Image attestations ------------------------------------------------------------

DIGEST = "sha256:" + "ab" * 32
ATTESTATIONS = f"{API}/attestations/{DIGEST}"
SLSA = "https://slsa.dev/provenance/v1"
# Pre-signed storage link: whoever holds it can read the blob, so it must never
# appear in a returned value or an error message.
BLOB_URL = "https://blob.example.test/attestations/1/9.json.sn?sig=SECRETSIGNATURE&se=2026"
BLOB_PATTERN = r"https://blob\.example\.test/.*"
# A real bundle exactly as GitHub's storage serves it: Snappy-compressed JSON.
SNAPPY_BUNDLE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "provenance"
    / "github-release.bundle.snappy"
).read_bytes()
INLINE_BUNDLE = {"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json", "inline": True}


def _blob_response(body: bytes = SNAPPY_BUNDLE, content_type: str = "application/x-snappy"):
    return httpx.Response(200, content=body, headers={"content-type": content_type})


def _linked(url: str = BLOB_URL) -> httpx.Response:
    return httpx.Response(200, json={"attestations": [{"bundle": None, "bundle_url": url}]})


@respx.mock
async def test_attestations_inline_bundle_is_returned_as_json_text(
    provider: GitHubProvider,
) -> None:
    route = respx.get(ATTESTATIONS).mock(
        return_value=httpx.Response(
            200, json={"attestations": [{"bundle": INLINE_BUNDLE, "repository_id": 7}]}
        )
    )

    result = await provider.get_image_attestations(REPO, DIGEST)

    assert [json.loads(a.bundle) for a in result] == [INLINE_BUNDLE]
    request = route.calls.last.request
    assert request.url.params["predicate_type"] == SLSA
    assert request.url.params["per_page"] == "100"
    # The provider's own credentials make this call.
    assert request.headers["authorization"] == "Bearer t"


@respx.mock
async def test_attestations_bundle_url_is_downloaded_without_credentials_and_decompressed(
    provider: GitHubProvider,
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=_linked())
    blob = respx.get(url__regex=BLOB_PATTERN).mock(return_value=_blob_response())

    result = await provider.get_image_attestations(REPO, DIGEST)

    bundle = json.loads(result[0].bundle)
    assert bundle["mediaType"] == "application/vnd.dev.sigstore.bundle.v0.3+json"
    # The pre-signed link grants access by itself; GitHub's token must not travel to it.
    assert "authorization" not in blob.calls.last.request.headers
    # The link is a capability: it is not part of anything returned.
    assert "SECRETSIGNATURE" not in repr(result)
    assert "blob.example.test" not in repr(result)


@respx.mock
async def test_attestations_bundle_url_serving_plain_json_is_accepted(
    provider: GitHubProvider,
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=_linked())
    respx.get(url__regex=BLOB_PATTERN).mock(
        return_value=_blob_response(json.dumps(INLINE_BUNDLE).encode(), "application/json")
    )

    result = await provider.get_image_attestations(REPO, DIGEST)

    assert json.loads(result[0].bundle) == INLINE_BUNDLE


@respx.mock
async def test_attestations_mix_inline_and_linked_bundles_across_pages(
    provider: GitHubProvider,
) -> None:
    respx.get(ATTESTATIONS, params={"per_page": "100", "predicate_type": SLSA}).mock(
        return_value=httpx.Response(
            200,
            json={"attestations": [{"bundle": INLINE_BUNDLE}]},
            headers={"link": f'<{ATTESTATIONS}?after=2>; rel="next"'},
        )
    )
    respx.get(ATTESTATIONS, params={"after": "2"}).mock(return_value=_linked())
    respx.get(url__regex=BLOB_PATTERN).mock(return_value=_blob_response())

    result = await provider.get_image_attestations(REPO, DIGEST)

    assert len(result) == 2
    assert json.loads(result[0].bundle) == INLINE_BUNDLE
    assert json.loads(result[1].bundle)["mediaType"].startswith("application/vnd.dev.sigstore")


@respx.mock
async def test_attestations_404_means_no_provenance_exists(provider: GitHubProvider) -> None:
    respx.get(ATTESTATIONS).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

    with pytest.raises(AttestationNotFoundError, match="No build provenance found") as caught:
        await provider.get_image_attestations(REPO, DIGEST)

    # Typed as not-found so every layer maps it to 404, but distinguishable.
    assert isinstance(caught.value, GitRepoNotFoundError)


@respx.mock
async def test_attestations_empty_list_means_no_provenance_exists(
    provider: GitHubProvider,
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=httpx.Response(200, json={"attestations": []}))

    with pytest.raises(AttestationNotFoundError):
        await provider.get_image_attestations(REPO, DIGEST)


@respx.mock
async def test_attestation_with_neither_bundle_nor_link_is_fatal(
    provider: GitHubProvider,
) -> None:
    respx.get(ATTESTATIONS).mock(
        return_value=httpx.Response(200, json={"attestations": [{"repository_id": 1}]})
    )

    with pytest.raises(GitProviderResponseError, match="neither a bundle nor a bundle_url"):
        await provider.get_image_attestations(REPO, DIGEST)


@respx.mock
async def test_attestation_link_that_is_not_https_is_never_fetched(
    provider: GitHubProvider,
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=_linked("http://169.254.169.254/latest?sig=SECRET"))

    with pytest.raises(GitProviderResponseError, match="not https") as caught:
        await provider.get_image_attestations(REPO, DIGEST)

    assert "SECRET" not in str(caught.value)
    assert len(respx.calls) == 1


@respx.mock
async def test_bundle_storage_refusal_is_fatal_and_does_not_leak_the_link(
    provider: GitHubProvider,
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=_linked())
    respx.get(url__regex=BLOB_PATTERN).mock(return_value=httpx.Response(403))

    with pytest.raises(GitProviderResponseError, match="HTTP 403") as caught:
        await provider.get_image_attestations(REPO, DIGEST)

    assert "SECRETSIGNATURE" not in str(caught.value)
    assert "blob.example.test" not in str(caught.value)


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
@respx.mock
async def test_bundle_download_failure_is_fatal_names_the_timeout_and_hides_the_link(
    failure: Exception,
) -> None:
    provider = GitHubProvider(
        name="GitHub",
        base_url="https://api.github.com",
        token="t",
        bundle_fetch_timeout_seconds=7.5,
    )
    respx.get(ATTESTATIONS).mock(return_value=_linked())
    respx.get(url__regex=BLOB_PATTERN).mock(side_effect=failure)

    with pytest.raises(
        GitProviderResponseError, match="bundle_fetch_timeout_seconds=7.5"
    ) as caught:
        await provider.get_image_attestations(REPO, DIGEST)

    assert type(failure).__name__ in str(caught.value)
    assert "SECRETSIGNATURE" not in str(caught.value)
    assert caught.value.__cause__ is None


@respx.mock
async def test_bundle_of_an_unexpected_content_type_is_fatal(provider: GitHubProvider) -> None:
    respx.get(ATTESTATIONS).mock(return_value=_linked())
    respx.get(url__regex=BLOB_PATTERN).mock(
        return_value=_blob_response(b"<html>", "text/html; charset=utf-8")
    )

    with pytest.raises(GitProviderResponseError, match="content type 'text/html'"):
        await provider.get_image_attestations(REPO, DIGEST)


@respx.mock
async def test_bundle_that_is_not_valid_snappy_is_fatal(provider: GitHubProvider) -> None:
    respx.get(ATTESTATIONS).mock(return_value=_linked())
    respx.get(url__regex=BLOB_PATTERN).mock(
        return_value=_blob_response(b"\xff\xff\xff\xff\xff\xff not snappy")
    )

    with pytest.raises(GitProviderResponseError, match="not valid Snappy-compressed JSON"):
        await provider.get_image_attestations(REPO, DIGEST)


@respx.mock
async def test_snappy_bundle_without_the_decoder_is_fatal_naming_the_extra(
    provider: GitHubProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=_linked())
    respx.get(url__regex=BLOB_PATTERN).mock(return_value=_blob_response())
    monkeypatch.setitem(sys.modules, "cramjam", None)

    with pytest.raises(GitProviderResponseError, match="uv sync --extra provenance"):
        await provider.get_image_attestations(REPO, DIGEST)


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
async def test_attestation_credentials_refusal_is_an_auth_error(
    provider: GitHubProvider, status: int
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=httpx.Response(status))

    with pytest.raises(GitAuthError, match=f"HTTP {status}"):
        await provider.get_image_attestations(REPO, DIGEST)


@respx.mock
async def test_attestation_rate_limit_is_a_provider_error(provider: GitHubProvider) -> None:
    respx.get(ATTESTATIONS).mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    )

    with pytest.raises(httpx.HTTPStatusError, match="rate limit exhausted"):
        await provider.get_image_attestations(REPO, DIGEST)


@respx.mock
async def test_attestation_provider_outage_surfaces_as_http_error(
    provider: GitHubProvider,
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=httpx.Response(502))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.get_image_attestations(REPO, DIGEST)


async def test_attestations_for_an_unparseable_repo_url_are_not_found(
    provider: GitHubProvider,
) -> None:
    with pytest.raises(GitRepoNotFoundError, match="Cannot parse repository URL"):
        await provider.get_image_attestations("not a url", DIGEST)


@respx.mock
async def test_the_storage_link_never_reaches_the_logs(
    provider: GitHubProvider, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(ATTESTATIONS).mock(return_value=_linked())
    respx.get(url__regex=BLOB_PATTERN).mock(return_value=_blob_response())
    # Other tests in the same worker reconfigure logging; pin httpx's own logger.
    httpx_logger = logging.getLogger("httpx")
    monkeypatch.setattr(httpx_logger, "disabled", False)
    monkeypatch.setattr(httpx_logger, "propagate", True)
    caplog.set_level(logging.INFO, logger="httpx")

    await provider.get_image_attestations(REPO, DIGEST)

    # httpx logs every request at INFO with its URL; the signature must not be in it.
    downloads = [
        r for r in caplog.records if r.name == "httpx" and "blob.example.test" in r.getMessage()
    ]
    assert downloads, "httpx should have logged the download, without its query string"
    assert all("sig=" not in r.getMessage() for r in downloads)
    assert "SECRETSIGNATURE" not in caplog.text


def test_query_redaction_strips_urls_and_leaves_other_arguments_alone() -> None:
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "%s %s %s %d",
        ("GET", httpx.URL("https://x.test/b?sig=S"), "https://x.test/a?sig=S", 200),
        None,
    )

    assert _QueryStringRedactionFilter().filter(record) is True

    assert record.args == ("GET", httpx.URL("https://x.test/b"), "https://x.test/a", 200)


def test_query_redaction_ignores_records_without_positional_arguments() -> None:
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "plain", None, None)

    assert _QueryStringRedactionFilter().filter(record) is True
