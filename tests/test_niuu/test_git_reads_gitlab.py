"""GitLab reads: one commit, a comparison, and CI for a ref with no merge request."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from niuu.adapters.outbound.gitlab import GitLabProvider
from niuu.domain.delivery import CheckConclusion
from niuu.domain.models import CIStatus
from niuu.ports.git import (
    GitAuthError,
    GitOperationUnsupportedError,
    GitProviderResponseError,
    GitRepoNotFoundError,
)

REPO = "https://gitlab.com/niuulabs/niuu"
API = "https://gitlab.com/api/v4/projects/niuulabs%2Fniuu"
SHA = "c" * 40
PARENT = "d" * 40


@pytest.fixture
def provider() -> GitLabProvider:
    return GitLabProvider(name="GitLab", base_url="https://gitlab.com", token="t")


def _commit_body() -> dict:
    return {
        "id": SHA,
        "web_url": f"{REPO}/-/commit/{SHA}",
        "parent_ids": [PARENT],
        "author_name": "Regin Coder",
        "author_email": "regin@example.test",
        "authored_date": "2026-10-26T10:00:00.000+00:00",
        "message": "feat: add the thing\n\nbody",
        "stats": {"additions": 4, "deletions": 1, "total": 5},
    }


def _diffs() -> list[dict]:
    return [
        {
            "old_path": "src/a.py",
            "new_path": "src/a.py",
            "diff": "@@ -1,2 +1,3 @@\n keep\n-old\n+new\n+newer\n",
        },
        {"old_path": "logo.png", "new_path": "logo.png", "new_file": True, "diff": ""},
        {
            "old_path": "docs/a.md",
            "new_path": "docs/b.md",
            "renamed_file": True,
            "diff": "",
        },
        {"old_path": "old.txt", "new_path": "old.txt", "deleted_file": True, "diff": "@@\n-gone\n"},
        {"old_path": "big.json", "new_path": "big.json", "too_large": True, "diff": ""},
        {"old_path": "c.json", "new_path": "c.json", "collapsed": True, "diff": ""},
    ]


@respx.mock
async def test_get_commit_maps_identity_and_counts_lines_per_file(
    provider: GitLabProvider,
) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(
        return_value=httpx.Response(200, json=_commit_body())
    )
    respx.get(f"{API}/repository/commits/{SHA}/diff").mock(
        return_value=httpx.Response(200, json=_diffs())
    )

    commit = await provider.get_commit(REPO, "regin")

    assert commit.sha == SHA
    assert commit.parents == (PARENT,)
    assert commit.authored_at == datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    assert (commit.additions, commit.deletions) == (4, 1)
    assert commit.files_total == 6
    by_path = {f.path: f for f in commit.files}
    code = by_path["src/a.py"]
    assert (code.additions, code.deletions, code.status) == (2, 1, "modified")
    assert code.patch is not None and code.patch_omitted_reason is None
    assert by_path["logo.png"].status == "added"
    assert by_path["logo.png"].patch is None
    assert "binary" in by_path["logo.png"].patch_omitted_reason
    assert by_path["docs/b.md"].status == "renamed"
    assert by_path["docs/b.md"].previous_path == "docs/a.md"
    assert by_path["old.txt"].status == "removed"
    assert (by_path["old.txt"].additions, by_path["old.txt"].deletions) == (0, 1)
    for name in ("big.json", "c.json"):
        assert by_path[name].patch is None
        assert by_path[name].additions is None
        assert "too large or collapsed" in by_path[name].patch_omitted_reason


@respx.mock
async def test_get_commit_follows_diff_pagination(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(
        return_value=httpx.Response(200, json=_commit_body())
    )
    respx.get(f"{API}/repository/commits/{SHA}/diff", params={"page": 2}).mock(
        return_value=httpx.Response(
            200, json=[{"old_path": "z.py", "new_path": "z.py", "diff": "+z\n"}]
        )
    )
    respx.get(f"{API}/repository/commits/{SHA}/diff", params={"page": 1}).mock(
        return_value=httpx.Response(
            200,
            json=[{"old_path": "a.py", "new_path": "a.py", "diff": "+a\n"}],
            headers={"x-next-page": "2"},
        )
    )

    commit = await provider.get_commit(REPO, "regin")

    assert [f.path for f in commit.files] == ["a.py", "z.py"]


@respx.mock
async def test_get_commit_pagination_that_never_advances_is_fatal(
    provider: GitLabProvider,
) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(
        return_value=httpx.Response(200, json=_commit_body())
    )
    respx.get(f"{API}/repository/commits/{SHA}/diff").mock(
        return_value=httpx.Response(200, json=[], headers={"x-next-page": "1"})
    )

    with pytest.raises(GitProviderResponseError, match="did not advance"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_get_commit_without_an_id_is_fatal(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(
        return_value=httpx.Response(200, json={"message": "x"})
    )

    with pytest.raises(GitProviderResponseError, match="omitted its SHA"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_get_commit_with_a_garbled_date_is_fatal(provider: GitLabProvider) -> None:
    body = _commit_body() | {"authored_date": "yesterday"}
    respx.get(f"{API}/repository/commits/regin").mock(return_value=httpx.Response(200, json=body))
    respx.get(f"{API}/repository/commits/{SHA}/diff").mock(
        return_value=httpx.Response(200, json=[])
    )

    with pytest.raises(GitProviderResponseError, match="unparseable timestamp"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_get_commit_missing_ref_is_not_found(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/nope").mock(return_value=httpx.Response(404))

    with pytest.raises(GitRepoNotFoundError, match="nope"):
        await provider.get_commit(REPO, "nope")


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
async def test_get_commit_refused_credentials_are_an_auth_error(
    provider: GitLabProvider, status: int
) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(return_value=httpx.Response(status))

    with pytest.raises(GitAuthError, match="read_repository"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_get_commit_rate_limit_is_a_provider_error(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(return_value=httpx.Response(429))

    with pytest.raises(httpx.HTTPStatusError, match="rate limit exhausted"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_get_commit_outage_surfaces_as_http_error(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_unexpected_success_status_is_fatal(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(return_value=httpx.Response(204))

    with pytest.raises(GitProviderResponseError, match="Unexpected HTTP 204"):
        await provider.get_commit(REPO, "regin")


@pytest.mark.parametrize("call", ["commit", "compare", "checks"])
async def test_unparseable_repo_url_is_not_found(provider: GitLabProvider, call: str) -> None:
    with pytest.raises(GitRepoNotFoundError, match="Cannot parse repository URL"):
        if call == "commit":
            await provider.get_commit("not a url", "x")
        elif call == "compare":
            await provider.compare_refs("not a url", "a", "b")
        else:
            await provider.get_ref_checks("not a url", "x")


def _mock_compare(
    *,
    commits: list[dict] | None = None,
    behind_total: str | None = "1",
    timeout: bool = False,
    merge_base: dict | None = None,
) -> None:
    respx.get(f"{API}/repository/compare").mock(
        return_value=httpx.Response(
            200,
            json={
                "commits": commits
                if commits is not None
                else [
                    {
                        "id": "1" * 40,
                        "title": "feat: one",
                        "author_name": "A",
                        "authored_date": "2026-10-25T09:00:00Z",
                        "web_url": "u1",
                    },
                    {"id": "2" * 40, "title": "fix: two", "author_name": "B", "web_url": "u2"},
                ],
                "diffs": _diffs()[:1],
                "compare_timeout": timeout,
            },
        )
    )
    headers = {} if behind_total is None else {"x-total": behind_total}
    respx.get(f"{API}/repository/commits").mock(
        return_value=httpx.Response(200, json=[], headers=headers)
    )
    respx.get(f"{API}/repository/merge_base").mock(
        return_value=httpx.Response(200, json=merge_base or {"id": PARENT})
    )


@respx.mock
async def test_compare_derives_ahead_behind_and_status(provider: GitLabProvider) -> None:
    _mock_compare()

    comparison = await provider.compare_refs(REPO, "main", "regin")

    assert (comparison.ahead_by, comparison.behind_by) == (2, 1)
    assert comparison.status == "diverged"
    assert comparison.merge_base_sha == PARENT
    assert comparison.url == f"{REPO}/-/compare/main...regin"
    assert [c.subject for c in comparison.commits] == ["feat: one", "fix: two"]
    assert comparison.commits[0].authored_at == datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    assert comparison.commits[1].authored_at is None
    assert [f.path for f in comparison.files] == ["src/a.py"]
    assert comparison.files_total == 1


@pytest.mark.parametrize(
    ("commits", "behind", "expected"),
    [
        ([], "0", "identical"),
        ([], "3", "behind"),
        ([{"id": "1" * 40, "title": "t", "author_name": "A"}], "0", "ahead"),
    ],
)
@respx.mock
async def test_compare_status_follows_the_two_counts(
    provider: GitLabProvider, commits: list[dict], behind: str, expected: str
) -> None:
    _mock_compare(commits=commits, behind_total=behind)

    comparison = await provider.compare_refs(REPO, "main", "regin")

    assert comparison.status == expected


@respx.mock
async def test_compare_timeout_is_fatal_not_a_partial_diff(provider: GitLabProvider) -> None:
    _mock_compare(timeout=True)

    with pytest.raises(GitProviderResponseError, match="timed out"):
        await provider.compare_refs(REPO, "main", "regin")


@respx.mock
async def test_compare_without_a_behind_total_is_fatal(provider: GitLabProvider) -> None:
    _mock_compare(behind_total=None)

    with pytest.raises(GitProviderResponseError, match="did not report how many commits"):
        await provider.compare_refs(REPO, "main", "regin")


@respx.mock
async def test_compare_without_a_merge_base_sha_is_fatal(provider: GitLabProvider) -> None:
    _mock_compare(merge_base={"title": "x"})

    with pytest.raises(GitProviderResponseError, match="merge-base"):
        await provider.compare_refs(REPO, "main", "regin")


@respx.mock
async def test_compare_unknown_ref_is_not_found(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/compare").mock(return_value=httpx.Response(404))

    with pytest.raises(GitRepoNotFoundError, match="ghost"):
        await provider.compare_refs(REPO, "main", "ghost")


@pytest.mark.parametrize("failing", ["commits", "merge_base"])
@respx.mock
async def test_compare_secondary_read_failures_are_typed(
    provider: GitLabProvider, failing: str
) -> None:
    _mock_compare()
    respx.get(f"{API}/repository/{'commits' if failing == 'commits' else 'merge_base'}").mock(
        return_value=httpx.Response(403)
    )

    with pytest.raises(GitAuthError):
        await provider.compare_refs(REPO, "main", "regin")


def _mock_commit_lookup() -> None:
    respx.get(f"{API}/repository/commits/regin").mock(
        return_value=httpx.Response(200, json={"id": SHA})
    )


@respx.mock
async def test_ref_checks_reads_jobs_of_the_newest_pipeline_for_the_commit(
    provider: GitLabProvider,
) -> None:
    _mock_commit_lookup()
    pipelines = respx.get(f"{API}/pipelines").mock(
        return_value=httpx.Response(200, json=[{"id": 9, "status": "failed"}])
    )
    respx.get(f"{API}/pipelines/9/jobs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "lint", "status": "success", "web_url": "https://ci/lint"},
                {"name": "test", "status": "failed"},
            ],
        )
    )

    result = await provider.get_ref_checks(REPO, "regin")

    assert pipelines.calls.last.request.url.params["sha"] == SHA
    assert result.sha == SHA
    assert {c.name: c.conclusion for c in result.checks} == {
        "lint": CheckConclusion.PASSING,
        "test": CheckConclusion.FAILING,
    }
    assert result.status is CIStatus.FAILING


@respx.mock
async def test_ref_checks_with_no_pipeline_is_empty_and_unknown(
    provider: GitLabProvider,
) -> None:
    _mock_commit_lookup()
    respx.get(f"{API}/pipelines").mock(return_value=httpx.Response(200, json=[]))

    result = await provider.get_ref_checks(REPO, "regin")

    assert result.checks == ()
    assert result.status is CIStatus.UNKNOWN


@respx.mock
async def test_ref_checks_job_read_failure_is_a_typed_provider_error(
    provider: GitLabProvider,
) -> None:
    _mock_commit_lookup()
    respx.get(f"{API}/pipelines").mock(return_value=httpx.Response(200, json=[{"id": 9}]))
    respx.get(f"{API}/pipelines/9/jobs").mock(return_value=httpx.Response(500))

    with pytest.raises(GitProviderResponseError, match="pipeline jobs"):
        await provider.get_ref_checks(REPO, "regin")


@respx.mock
async def test_ref_checks_unknown_ref_is_not_found(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/ghost").mock(return_value=httpx.Response(404))

    with pytest.raises(GitRepoNotFoundError, match="ghost"):
        await provider.get_ref_checks(REPO, "ghost")


@respx.mock
async def test_ref_checks_commit_without_id_is_fatal(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GitProviderResponseError, match="omitted its SHA"):
        await provider.get_ref_checks(REPO, "regin")


@respx.mock
async def test_ref_checks_pipeline_listing_failure_is_typed(provider: GitLabProvider) -> None:
    _mock_commit_lookup()
    respx.get(f"{API}/pipelines").mock(return_value=httpx.Response(403))

    with pytest.raises(GitAuthError):
        await provider.get_ref_checks(REPO, "regin")


@respx.mock
async def test_get_commit_diff_page_failure_is_typed(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/regin").mock(
        return_value=httpx.Response(200, json=_commit_body())
    )
    respx.get(f"{API}/repository/commits/{SHA}/diff").mock(return_value=httpx.Response(404))

    with pytest.raises(GitRepoNotFoundError, match="diff of commit"):
        await provider.get_commit(REPO, "regin")


@respx.mock
async def test_image_attestations_are_unsupported_and_say_why(provider: GitLabProvider) -> None:
    with pytest.raises(GitOperationUnsupportedError, match="no build attestation API") as caught:
        await provider.get_image_attestations(REPO, "sha256:" + "ab" * 32)

    # An honest "this host cannot", never an empty result and never a request.
    assert REPO in str(caught.value)
    assert len(respx.calls) == 0
