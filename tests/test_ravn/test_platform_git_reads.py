"""volundr_git reads: commits, comparisons, branch CI and image provenance via the platform."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ravn.adapters.tools.platform_tools import VolundrGitTool

BASE_URL = "http://localhost:8080"
NIUU_REPOS_URL = f"{BASE_URL}/api/v1/niuu/repos"
REPO = "https://github.com/niuulabs/niuu"


@pytest.fixture
def tool() -> VolundrGitTool:
    return VolundrGitTool(base_url=BASE_URL)


def test_schema_offers_the_reads_with_shaping_arguments(tool: VolundrGitTool) -> None:
    properties = tool.input_schema["properties"]

    assert {"get_commit", "compare", "ref_checks"} <= set(properties["action"]["enum"])
    assert properties["path"]["type"] == "array"
    assert properties["include_patch"]["type"] == "boolean"
    for name in ("ref", "base", "head"):
        assert name in properties


def test_description_steers_the_model_off_anonymous_provider_calls(tool: VolundrGitTool) -> None:
    description = tool.description

    assert "api.github.com" in description
    assert "rate limited" in description
    for action in ("get_commit", "compare", "ref_checks"):
        assert action in description
    assert "include_patch" in description


@pytest.mark.asyncio
@respx.mock
async def test_get_commit_asks_for_the_file_list_by_default(tool: VolundrGitTool) -> None:
    route = respx.get(f"{NIUU_REPOS_URL}/commit").mock(
        return_value=httpx.Response(200, json={"sha": "a" * 40, "files": []})
    )

    result = await tool.execute({"action": "get_commit", "repo_url": REPO, "ref": "regin"})

    assert not result.is_error
    assert json.loads(result.content)["sha"] == "a" * 40
    params = route.calls.last.request.url.params
    assert params["repo_url"] == REPO
    assert params["ref"] == "regin"
    assert "include_patch" not in params
    assert "path" not in params


@pytest.mark.asyncio
@respx.mock
async def test_get_commit_passes_path_filters_and_patch_flag(tool: VolundrGitTool) -> None:
    route = respx.get(f"{NIUU_REPOS_URL}/commit").mock(
        return_value=httpx.Response(200, json={"sha": "a" * 40, "files": []})
    )

    await tool.execute(
        {
            "action": "get_commit",
            "repo_url": REPO,
            "ref": "regin",
            "path": ["src/a.py", "docs/"],
            "include_patch": True,
        }
    )

    params = route.calls.last.request.url.params
    assert params.get_list("path") == ["src/a.py", "docs/"]
    assert params["include_patch"] == "true"


@pytest.mark.asyncio
@respx.mock
async def test_a_single_path_string_is_accepted(tool: VolundrGitTool) -> None:
    route = respx.get(f"{NIUU_REPOS_URL}/compare").mock(
        return_value=httpx.Response(200, json={"files": []})
    )

    await tool.execute(
        {
            "action": "compare",
            "repo_url": REPO,
            "base": "main",
            "head": "regin",
            "path": "src/a.py",
        }
    )

    params = route.calls.last.request.url.params
    assert params.get_list("path") == ["src/a.py"]
    assert params["base"] == "main"
    assert params["head"] == "regin"


@pytest.mark.asyncio
@respx.mock
async def test_ref_checks_reads_the_checks_route(tool: VolundrGitTool) -> None:
    route = respx.get(f"{NIUU_REPOS_URL}/checks").mock(
        return_value=httpx.Response(200, json={"status": "failing", "checks": []})
    )

    result = await tool.execute({"action": "ref_checks", "repo_url": REPO, "ref": "regin"})

    assert json.loads(result.content)["status"] == "failing"
    params = route.calls.last.request.url.params
    assert params["repo_url"] == REPO
    assert params["ref"] == "regin"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input", "needle"),
    [
        ({"action": "get_commit", "repo_url": REPO}, "repo_url and ref are required"),
        ({"action": "get_commit", "ref": "x"}, "repo_url and ref are required"),
        ({"action": "compare", "repo_url": REPO, "base": "a"}, "repo_url, base and head"),
        ({"action": "ref_checks", "ref": "x"}, "repo_url and ref are required"),
    ],
)
async def test_missing_arguments_are_reported(
    tool: VolundrGitTool, input: dict, needle: str
) -> None:
    result = await tool.execute(input)

    assert result.is_error
    assert needle in result.content


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("input", "route", "label"),
    [
        ({"action": "get_commit", "repo_url": REPO, "ref": "ghost"}, "commit", "read commit"),
        (
            {"action": "compare", "repo_url": REPO, "base": "a", "head": "ghost"},
            "compare",
            "compare",
        ),
        ({"action": "ref_checks", "repo_url": REPO, "ref": "ghost"}, "checks", "read checks"),
    ],
)
async def test_failures_carry_the_platforms_explanation(
    tool: VolundrGitTool, input: dict, route: str, label: str
) -> None:
    respx.get(f"{NIUU_REPOS_URL}/{route}").mock(
        return_value=httpx.Response(404, json={"detail": "Not found: ref 'ghost'"})
    )

    result = await tool.execute(input)

    assert result.is_error
    assert label in result.content
    assert "Not found: ref 'ghost'" in result.content


# --- verify_image_provenance ---------------------------------------------------------

DIGEST = "sha256:" + "ab" * 32


def test_schema_and_description_teach_the_model_to_verify_provenance(tool: VolundrGitTool) -> None:
    properties = tool.input_schema["properties"]

    assert "verify_image_provenance" in properties["action"]["enum"]
    assert properties["digest"]["type"] == "string"
    description = tool.description
    assert "verify_image_provenance" in description
    assert "sha256:<64 hex>" in description
    # What an error means is part of the contract, so a model does not read it as "unknown".
    assert "NOT bound" in description
    assert "never fetch api.github.com or attestation storage yourself" in description


@pytest.mark.asyncio
@respx.mock
async def test_verify_image_provenance_returns_the_platforms_verified_facts(
    tool: VolundrGitTool,
) -> None:
    facts = {
        "repo_url": REPO,
        "digest": DIGEST,
        "verified": True,
        "attestations": [{"commit_sha": "a" * 40, "ref": "refs/heads/dev"}],
        "rejected": [],
    }
    route = respx.get(f"{NIUU_REPOS_URL}/provenance").mock(
        return_value=httpx.Response(200, json=facts)
    )

    result = await tool.execute(
        {"action": "verify_image_provenance", "repo_url": REPO, "digest": DIGEST}
    )

    assert not result.is_error
    assert json.loads(result.content) == facts
    params = route.calls.last.request.url.params
    assert params["repo_url"] == REPO
    assert params["digest"] == DIGEST


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input",
    [
        {"action": "verify_image_provenance", "repo_url": REPO},
        {"action": "verify_image_provenance", "digest": DIGEST},
    ],
)
async def test_verify_image_provenance_requires_repo_and_digest(
    tool: VolundrGitTool, input: dict
) -> None:
    result = await tool.execute(input)

    assert result.is_error
    assert "repo_url and digest are required for verify_image_provenance" in result.content


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (404, "No build provenance found for sha256:abab in niuulabs/niuu"),
        (422, "Provenance verification failed: DSSE: invalid signature"),
        (501, "GitLab has no build attestation API"),
        (503, "Image provenance verification is turned off"),
        (502, "The Git provider could not serve this read"),
    ],
)
async def test_unverified_provenance_is_an_error_that_says_not_bound(
    tool: VolundrGitTool, status: int, detail: str
) -> None:
    respx.get(f"{NIUU_REPOS_URL}/provenance").mock(
        return_value=httpx.Response(status, json={"detail": detail})
    )

    result = await tool.execute(
        {"action": "verify_image_provenance", "repo_url": REPO, "digest": DIGEST}
    )

    # An unverified digest is never a successful tool result a model could act on.
    assert result.is_error
    assert "NOT verified, do not treat it as bound" in result.content
    assert detail in result.content
