"""Registry, service and REST routing of the commit / compare / checks / provenance reads."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_repos import create_repos_router
from niuu.adapters.outbound.git_registry import (
    GitProviderRegistry,
    create_git_registry,
    create_provenance_verifier,
)
from niuu.adapters.outbound.sigstore_provenance import SigstoreProvenanceVerifier
from niuu.config import (
    DEFAULT_BUNDLE_FETCH_TIMEOUT_SECONDS,
    GitConfig,
    GitHubConfig,
    ProvenanceConfig,
)
from niuu.domain.delivery import CheckConclusion, CheckRecord
from niuu.domain.models import (
    ChangedFile,
    CommitDetail,
    CommitSummary,
    InvalidImageDigestError,
    Principal,
    ProvenanceAttestation,
    RefChecks,
    RefComparison,
    VerifiedProvenance,
)
from niuu.domain.services.repo import RepoService
from niuu.ports.git import (
    AttestationNotFoundError,
    GitAuthError,
    GitOperationUnsupportedError,
    GitProviderResponseError,
    GitRepoNotFoundError,
)
from niuu.ports.provenance import (
    ProvenanceNotConfiguredError,
    ProvenanceVerificationError,
    ProvenanceVerifier,
    ProvenanceVerifierUnavailableError,
)
from tests.conftest import MockGitProvider

REPO = "https://github.com/niuulabs/niuu"
SHA = "a" * 40
WHEN = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)


def _files() -> tuple[ChangedFile, ...]:
    return (
        ChangedFile("src/a.py", "modified", 3, 1, patch="@@ a @@"),
        ChangedFile("src/pkg/b.py", "added", 5, 0, patch="@@ b @@"),
        ChangedFile(
            "docs/new.md",
            "renamed",
            0,
            0,
            previous_path="docs/old.md",
            patch_omitted_reason="no line changes (rename, copy or mode change)",
        ),
        ChangedFile("logo.png", "added", 0, 0, patch_omitted_reason="binary"),
    )


def _commit() -> CommitDetail:
    return CommitDetail(
        repo_url=REPO,
        sha=SHA,
        url=f"{REPO}/commit/{SHA}",
        parents=("b" * 40,),
        author_name="Regin",
        author_email="regin@example.test",
        authored_at=WHEN,
        message="feat: x",
        additions=8,
        deletions=1,
        files=_files(),
        files_total=4,
    )


def _comparison() -> RefComparison:
    return RefComparison(
        repo_url=REPO,
        base="main",
        head="regin",
        status="ahead",
        ahead_by=1,
        behind_by=0,
        merge_base_sha="b" * 40,
        url=f"{REPO}/compare/main...regin",
        commits=(CommitSummary(SHA, "feat: x", "Regin", WHEN, "u"),),
        files=_files(),
        files_total=4,
    )


def _checks() -> RefChecks:
    return RefChecks(
        repo_url=REPO,
        ref="regin",
        sha=SHA,
        checks=(
            CheckRecord(
                name="backend", conclusion=CheckConclusion.FAILING, details_url="https://ci"
            ),
            CheckRecord(name="lint", conclusion=CheckConclusion.PASSING),
        ),
    )


def _provider() -> MockGitProvider:
    provider = MockGitProvider()
    provider.commit_result = _commit()
    provider.comparison_result = _comparison()
    provider.ref_checks_result = _checks()
    return provider


def _registry(provider: MockGitProvider | None = None) -> GitProviderRegistry:
    registry = GitProviderRegistry()
    registry.register(provider or _provider())
    return registry


# --- Registry: routed to the provider that serves the URL, like list_branches ---


async def test_registry_routes_reads_to_the_serving_provider() -> None:
    provider = _provider()
    registry = _registry(provider)

    assert await registry.get_commit(REPO, "regin") == _commit()
    assert await registry.compare_refs(REPO, "main", "regin") == _comparison()
    assert await registry.get_ref_checks(REPO, "regin") == _checks()
    assert provider.read_calls == [
        ("get_commit", REPO, "regin"),
        ("compare_refs", REPO, "main", "regin"),
        ("get_ref_checks", REPO, "regin"),
    ]


@pytest.mark.parametrize("call", ["commit", "compare", "checks"])
async def test_registry_with_no_serving_provider_is_a_value_error(call: str) -> None:
    registry = GitProviderRegistry()

    with pytest.raises(ValueError, match="No git provider found"):
        if call == "commit":
            await registry.get_commit(REPO, "x")
        elif call == "compare":
            await registry.compare_refs(REPO, "a", "b")
        else:
            await registry.get_ref_checks(REPO, "x")


# --- Service: the caller's own account first, then shaping for an agent ---------


async def test_service_prefers_the_callers_own_provider() -> None:
    shared = _provider()
    personal = _provider()
    integrations = AsyncMock()
    integrations.find_git_provider_for = AsyncMock(return_value=personal)
    service = RepoService(_registry(shared), user_integration=integrations)

    await service.get_commit(REPO, "regin", user_id="u1")
    await service.compare_refs(REPO, "main", "regin", user_id="u1")
    await service.get_ref_checks(REPO, "regin", user_id="u1")

    assert len(personal.read_calls) == 3
    assert shared.read_calls == []


async def test_service_falls_to_the_shared_provider_only_when_the_caller_has_none() -> None:
    shared = _provider()
    integrations = AsyncMock()
    integrations.find_git_provider_for = AsyncMock(return_value=None)
    service = RepoService(_registry(shared), user_integration=integrations)

    await service.get_ref_checks(REPO, "regin", user_id="u1")
    await service.get_ref_checks(REPO, "regin")  # anonymous caller

    assert len(shared.read_calls) == 2


async def test_service_default_is_the_file_list_without_patches() -> None:
    service = RepoService(_registry())

    commit = await service.get_commit(REPO, "regin")

    assert commit.patch_included is False
    assert commit.files_total == 4
    assert [f.path for f in commit.files] == [f.path for f in _files()]
    assert all(f.patch is None for f in commit.files)
    # With no patch asked for there is nothing to explain about a missing one.
    assert all(f.patch_omitted_reason is None for f in commit.files)


async def test_service_patches_on_request_keep_the_providers_omission_reason() -> None:
    service = RepoService(_registry())

    commit = await service.get_commit(REPO, "regin", include_patch=True)

    by_path = {f.path: f for f in commit.files}
    assert commit.patch_included is True
    assert by_path["src/a.py"].patch == "@@ a @@"
    assert by_path["logo.png"].patch is None
    assert by_path["logo.png"].patch_omitted_reason == "binary"


async def test_service_path_filter_matches_files_directories_and_old_names() -> None:
    service = RepoService(_registry())

    commit = await service.get_commit(
        REPO,
        "regin",
        paths=["src/pkg/", "docs/old.md", "/src/a.py", "missing.py", "src/p"],
        include_patch=True,
    )

    assert [f.path for f in commit.files] == ["src/a.py", "src/pkg/b.py", "docs/new.md"]
    assert commit.files_total == 4
    # A mistyped path is reported, never read as "the commit did not touch it";
    # a bare name prefix is not a directory match.
    assert commit.unmatched_paths == ("missing.py", "src/p")


async def test_service_blank_path_matches_nothing() -> None:
    service = RepoService(_registry())

    commit = await service.get_commit(REPO, "regin", paths=["  "])

    assert commit.files == ()
    assert commit.unmatched_paths == ("  ",)


async def test_service_shapes_comparisons_the_same_way() -> None:
    service = RepoService(_registry())

    comparison = await service.compare_refs(
        REPO, "main", "regin", paths=["src/a.py"], include_patch=True
    )

    assert [f.path for f in comparison.files] == ["src/a.py"]
    assert comparison.files[0].patch == "@@ a @@"
    assert comparison.files_total == 4
    assert comparison.commits[0].subject == "feat: x"
    assert comparison.patch_included is True


# --- REST ------------------------------------------------------------------------


def _client(service: RepoService | None) -> TestClient:
    app = FastAPI()
    app.include_router(create_repos_router(service))
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="dev-user", email="dev@example.test", tenant_id="default", roles=[]
    )
    return TestClient(app)


def test_commit_route_returns_a_shaped_commit() -> None:
    client = _client(RepoService(_registry()))

    response = client.get(
        "/api/v1/niuu/repos/commit",
        params=[("repo_url", REPO), ("ref", "regin"), ("path", "src/a.py"), ("path", "nope")],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sha"] == SHA
    assert body["authored_at"] == WHEN.isoformat()
    assert body["files_total"] == 4
    assert [f["path"] for f in body["files"]] == ["src/a.py"]
    assert body["files"][0]["patch"] is None
    assert body["patch_included"] is False
    assert body["unmatched_paths"] == ["nope"]


def test_commit_route_includes_patches_on_request() -> None:
    client = _client(RepoService(_registry()))

    response = client.get(
        "/api/v1/niuu/repos/commit",
        params={"repo_url": REPO, "ref": "regin", "include_patch": "true"},
    )

    assert response.json()["files"][0]["patch"] == "@@ a @@"
    assert response.json()["patch_included"] is True


def test_commit_route_serialises_a_commit_without_a_date() -> None:
    provider = _provider()
    provider.commit_result = CommitDetail(**{**_commit().__dict__, "authored_at": None})
    client = _client(RepoService(_registry(provider)))

    response = client.get("/api/v1/niuu/repos/commit", params={"repo_url": REPO, "ref": "x"})

    assert response.json()["authored_at"] is None


def test_compare_route_returns_counts_commits_and_files() -> None:
    client = _client(RepoService(_registry()))

    response = client.get(
        "/api/v1/niuu/repos/compare",
        params={"repo_url": REPO, "base": "main", "head": "regin", "include_patch": "true"},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["ahead_by"], body["behind_by"]) == ("ahead", 1, 0)
    assert body["commits"][0] == {
        "sha": SHA,
        "subject": "feat: x",
        "author_name": "Regin",
        "authored_at": WHEN.isoformat(),
        "url": "u",
    }
    assert body["files_total"] == 4
    assert body["files"][0]["patch"] == "@@ a @@"


def test_checks_route_reports_status_counts_and_checks() -> None:
    client = _client(RepoService(_registry()))

    response = client.get("/api/v1/niuu/repos/checks", params={"repo_url": REPO, "ref": "regin"})

    assert response.status_code == 200
    body = response.json()
    assert body["sha"] == SHA
    assert body["status"] == "failing"
    assert body["counts"] == {"failing": 1, "passing": 1}
    assert body["checks"][0] == {
        "name": "backend",
        "conclusion": "failing",
        "details_url": "https://ci",
    }


READS = [
    ("/api/v1/niuu/repos/commit", {"repo_url": REPO, "ref": "r"}),
    ("/api/v1/niuu/repos/compare", {"repo_url": REPO, "base": "a", "head": "b"}),
    ("/api/v1/niuu/repos/checks", {"repo_url": REPO, "ref": "r"}),
]


@pytest.mark.parametrize(("path", "params"), READS)
@pytest.mark.parametrize(
    ("failure", "status", "needle"),
    [
        (ValueError("No git provider found for: x"), 404, "No connected Git host serves"),
        (GitRepoNotFoundError("Not found: commit 'r'"), 404, "Not found: commit 'r'"),
        (GitAuthError("token refused"), 403, "token refused"),
        (httpx.ConnectError("unreachable"), 502, "unreachable"),
        (GitProviderResponseError("incomplete comparison"), 502, "incomplete comparison"),
    ],
)
def test_read_failures_map_to_typed_http_errors(
    path: str, params: dict, failure: Exception, status: int, needle: str
) -> None:
    provider = _provider()
    for method in ("get_commit", "compare_refs", "get_ref_checks"):
        setattr(provider, method, AsyncMock(side_effect=failure))
    client = _client(RepoService(_registry(provider)))

    response = client.get(path, params=params)

    assert response.status_code == status
    assert needle in response.json()["detail"]


@pytest.mark.parametrize(("path", "params"), READS)
def test_reads_without_a_repo_service_are_unavailable(path: str, params: dict) -> None:
    response = _client(None).get(path, params=params)

    assert response.status_code == 503


@pytest.mark.parametrize(
    ("conclusions", "expected"),
    [
        ((), "unknown"),
        ((CheckConclusion.PASSING, CheckConclusion.SKIPPED), "passing"),
        ((CheckConclusion.PASSING, CheckConclusion.PENDING), "pending"),
        ((CheckConclusion.PASSING, CheckConclusion.UNKNOWN), "unknown"),
        ((CheckConclusion.PENDING, CheckConclusion.CANCELED), "failing"),
    ],
)
def test_ref_checks_aggregate_status(conclusions: tuple, expected: str) -> None:
    checks = tuple(CheckRecord(name=f"c{i}", conclusion=c) for i, c in enumerate(conclusions))

    assert RefChecks(REPO, "r", SHA, checks).status.value == expected


# --- Image provenance --------------------------------------------------------------

DIGEST = "sha256:" + "ab" * 32
WORKFLOW = "https://github.com/niuulabs/niuu/.github/workflows/reusable-container-build.yaml"


def _verified(commit: str = SHA, ref: str = "refs/heads/dev") -> VerifiedProvenance:
    return VerifiedProvenance(
        subject_name="ghcr.io/niuulabs/niuu",
        subject_digest=DIGEST,
        source_repository="https://github.com/niuulabs/niuu",
        commit_sha=commit,
        ref=ref,
        workflow=f"{WORKFLOW}@{ref}",
        run_url="https://github.com/niuulabs/niuu/actions/runs/7/attempts/1",
    )


class _ScriptedVerifier(ProvenanceVerifier):
    """A verifier that answers each bundle from a script: a result, or an error to raise."""

    def __init__(self, answers: dict[str, VerifiedProvenance | Exception]) -> None:
        self._answers = answers
        self.calls: list[tuple[str, str, str]] = []

    async def verify(self, bundle: str, *, subject_digest: str, repository: str):
        self.calls.append((bundle, subject_digest, repository))
        answer = self._answers[bundle]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _attesting_provider(*bundles: str) -> MockGitProvider:
    provider = MockGitProvider()
    provider.attestations_result = tuple(ProvenanceAttestation(bundle=b) for b in bundles)
    return provider


def _provenance_service(
    verifier: ProvenanceVerifier | None, provider: MockGitProvider | None = None, **kwargs
) -> RepoService:
    return RepoService(
        _registry(provider or _attesting_provider("b1")), provenance_verifier=verifier, **kwargs
    )


async def test_registry_routes_attestation_reads_to_the_serving_provider() -> None:
    provider = _attesting_provider("b1")

    result = await _registry(provider).get_image_attestations(REPO, DIGEST)

    assert result == (ProvenanceAttestation("b1"),)
    assert provider.read_calls == [("get_image_attestations", REPO, DIGEST)]


async def test_registry_attestation_read_with_no_serving_provider_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="No git provider found"):
        await GitProviderRegistry().get_image_attestations(REPO, DIGEST)


async def test_service_returns_only_what_verified_with_the_normalised_digest() -> None:
    verifier = _ScriptedVerifier({"b1": _verified()})
    service = _provenance_service(verifier)

    result = await service.verify_image_provenance(REPO, f"  sha256:{'AB' * 32}  ")

    assert result.digest == DIGEST
    assert result.attestations == (_verified(),)
    assert result.rejected == ()
    # The verifier is told the repository to pin the signer to, not the caller's URL spelling.
    assert verifier.calls == [("b1", DIGEST, "niuulabs/niuu")]


async def test_service_reports_each_refused_attestation_beside_the_verified_one() -> None:
    verifier = _ScriptedVerifier(
        {
            "stale": ProvenanceVerificationError("signing workflow 'x.yml' is not trusted"),
            "good": _verified(),
        }
    )
    service = _provenance_service(verifier, _attesting_provider("stale", "good"))

    result = await service.verify_image_provenance(REPO, DIGEST)

    assert result.attestations == (_verified(),)
    assert result.rejected == ("signing workflow 'x.yml' is not trusted",)


async def test_service_with_no_verified_attestation_is_a_failed_verification() -> None:
    verifier = _ScriptedVerifier(
        {
            "b1": ProvenanceVerificationError("DSSE: invalid signature"),
            "b2": ProvenanceVerificationError("wrong repository"),
        }
    )
    service = _provenance_service(verifier, _attesting_provider("b1", "b2"))

    with pytest.raises(ProvenanceVerificationError) as caught:
        await service.verify_image_provenance(REPO, DIGEST)

    # Never "unverified but returned": the error carries every reason instead.
    assert "None of the 2 attestation(s)" in caught.value.reason
    assert "DSSE: invalid signature; wrong repository" in caught.value.reason


async def test_service_does_not_turn_a_verifier_outage_into_a_verdict() -> None:
    verifier = _ScriptedVerifier({"b1": ProvenanceVerifierUnavailableError("TUF unreachable")})

    with pytest.raises(ProvenanceVerifierUnavailableError):
        await _provenance_service(verifier).verify_image_provenance(REPO, DIGEST)


async def test_service_without_a_verifier_refuses_before_reading_anything() -> None:
    provider = _attesting_provider("b1")

    with pytest.raises(ProvenanceNotConfiguredError, match="git.provenance.enabled"):
        await _provenance_service(None, provider).verify_image_provenance(REPO, DIGEST)

    assert provider.read_calls == []


@pytest.mark.parametrize(
    "digest", ["", "latest", "sha256:abc", "ab" * 32, "sha512:" + "ab" * 32, "sha256:" + "zz" * 32]
)
async def test_service_rejects_anything_that_is_not_a_full_sha256_digest(digest: str) -> None:
    provider = _attesting_provider("b1")
    service = _provenance_service(_ScriptedVerifier({"b1": _verified()}), provider)

    with pytest.raises(InvalidImageDigestError, match="not an image digest"):
        await service.verify_image_provenance(REPO, digest)

    assert provider.read_calls == []


async def test_service_prefers_the_callers_own_provider_for_attestations() -> None:
    shared = _attesting_provider("shared")
    personal = _attesting_provider("personal")
    integrations = AsyncMock()
    integrations.find_git_provider_for = AsyncMock(return_value=personal)
    verifier = _ScriptedVerifier({"personal": _verified()})
    service = _provenance_service(verifier, shared, user_integration=integrations)

    await service.verify_image_provenance(REPO, DIGEST, user_id="u1")

    assert [c[0] for c in personal.read_calls] == ["get_image_attestations"]
    assert shared.read_calls == []
    assert verifier.calls[0][0] == "personal"


async def test_service_with_no_serving_provider_is_a_value_error() -> None:
    service = RepoService(GitProviderRegistry(), provenance_verifier=_ScriptedVerifier({}))

    with pytest.raises(ValueError, match="No git provider found"):
        await service.verify_image_provenance(REPO, DIGEST)


async def test_service_with_an_unparseable_repo_url_is_a_value_error() -> None:
    provider = _attesting_provider("b1")
    provider.parse_repo = lambda url: None  # type: ignore[method-assign]
    service = _provenance_service(_ScriptedVerifier({"b1": _verified()}), provider)

    with pytest.raises(ValueError, match="Cannot parse repository URL"):
        await service.verify_image_provenance(REPO, DIGEST)


async def test_service_passes_through_a_provider_that_holds_nothing() -> None:
    provider = _provider()
    provider.get_image_attestations = AsyncMock(  # type: ignore[method-assign]
        side_effect=AttestationNotFoundError("No build provenance found for x")
    )

    with pytest.raises(AttestationNotFoundError):
        await _provenance_service(_ScriptedVerifier({}), provider).verify_image_provenance(
            REPO, DIGEST
        )


# --- Provenance REST ---------------------------------------------------------------

PROVENANCE_URL = "/api/v1/niuu/repos/provenance"
PROVENANCE_PARAMS = {"repo_url": REPO, "digest": DIGEST}


def test_provenance_route_returns_the_verified_facts() -> None:
    service = _provenance_service(_ScriptedVerifier({"b1": _verified()}))

    response = _client(service).get(PROVENANCE_URL, params=PROVENANCE_PARAMS)

    assert response.status_code == 200
    assert response.json() == {
        "repo_url": REPO,
        "digest": DIGEST,
        "verified": True,
        "attestations": [
            {
                "subject_name": "ghcr.io/niuulabs/niuu",
                "subject_digest": DIGEST,
                "source_repository": "https://github.com/niuulabs/niuu",
                "commit_sha": SHA,
                "ref": "refs/heads/dev",
                "workflow": f"{WORKFLOW}@refs/heads/dev",
                "run_url": "https://github.com/niuulabs/niuu/actions/runs/7/attempts/1",
            }
        ],
        "rejected": [],
    }


def test_provenance_route_reports_refused_attestations() -> None:
    verifier = _ScriptedVerifier(
        {"bad": ProvenanceVerificationError("wrong repository"), "good": _verified()}
    )
    service = _provenance_service(verifier, _attesting_provider("bad", "good"))

    response = _client(service).get(PROVENANCE_URL, params=PROVENANCE_PARAMS)

    assert response.status_code == 200
    assert response.json()["rejected"] == ["wrong repository"]
    assert len(response.json()["attestations"]) == 1


@pytest.mark.parametrize(
    ("failure", "status", "needle"),
    [
        (
            AttestationNotFoundError("No build provenance found for sha256:ab in x"),
            404,
            "No build provenance found",
        ),
        (
            ProvenanceVerificationError("None of the 1 attestation(s) verified: bad signature"),
            422,
            "Provenance verification failed: None of the 1 attestation(s) verified",
        ),
        (InvalidImageDigestError("'x' is not an image digest"), 422, "not an image digest"),
        (GitOperationUnsupportedError("GitLab has no build attestation API"), 501, "GitLab has no"),
        (ProvenanceNotConfiguredError("provenance is turned off"), 503, "turned off"),
        (ProvenanceVerifierUnavailableError("trust root unreachable"), 503, "trust root"),
        (ValueError("No git provider found for: x"), 404, "No connected Git host serves"),
        (GitRepoNotFoundError("Not found: repo"), 404, "Not found: repo"),
        (GitAuthError("token refused"), 403, "token refused"),
        (httpx.ConnectError("unreachable"), 502, "unreachable"),
        (GitProviderResponseError("bundle storage said HTTP 403"), 502, "bundle storage said"),
    ],
)
def test_provenance_failures_map_to_typed_http_errors(
    failure: Exception, status: int, needle: str
) -> None:
    service = RepoService(_registry(), provenance_verifier=_ScriptedVerifier({}))
    service.verify_image_provenance = AsyncMock(side_effect=failure)  # type: ignore[method-assign]

    response = _client(service).get(PROVENANCE_URL, params=PROVENANCE_PARAMS)

    assert response.status_code == status
    assert needle in response.json()["detail"]


def test_a_missing_digest_is_a_validation_error() -> None:
    response = _client(RepoService(_registry())).get(PROVENANCE_URL, params={"repo_url": REPO})

    assert response.status_code == 422


def test_provenance_without_a_repo_service_is_unavailable() -> None:
    assert _client(None).get(PROVENANCE_URL, params=PROVENANCE_PARAMS).status_code == 503


# --- Provenance wiring -------------------------------------------------------------


def test_provenance_is_on_by_default_with_the_workflows_that_sign_our_images() -> None:
    config = ProvenanceConfig()

    assert config.enabled is True
    assert config.kwargs == {
        "issuer": "https://token.actions.githubusercontent.com",
        "workflow_paths": [
            ".github/workflows/reusable-container-build.yaml",
            ".github/workflows/dev.yaml",
        ],
    }
    assert config.bundle_fetch_timeout_seconds == DEFAULT_BUNDLE_FETCH_TIMEOUT_SECONDS


def test_a_disabled_provenance_config_builds_no_verifier() -> None:
    assert create_provenance_verifier(ProvenanceConfig(enabled=False)) is None


def test_the_default_provenance_config_builds_the_sigstore_verifier() -> None:
    verifier = create_provenance_verifier(ProvenanceConfig())

    assert isinstance(verifier, SigstoreProvenanceVerifier)


def test_an_adapter_that_cannot_be_imported_is_fatal_and_names_the_remedy() -> None:
    config = ProvenanceConfig(adapter="niuu.adapters.outbound.no_such_module.Verifier")

    with pytest.raises(ImportError, match="git.provenance.enabled to false"):
        create_provenance_verifier(config)


def test_an_adapter_that_is_not_a_verifier_is_fatal() -> None:
    config = ProvenanceConfig(
        adapter="niuu.domain.models.VerifiedProvenance",
        kwargs={
            "subject_name": "a",
            "subject_digest": "b",
            "source_repository": "c",
            "commit_sha": "d",
            "ref": "e",
            "workflow": "f",
            "run_url": "g",
        },
    )

    with pytest.raises(TypeError, match="is not a ProvenanceVerifier"):
        create_provenance_verifier(config)


def test_adapter_kwargs_the_verifier_rejects_are_fatal() -> None:
    config = ProvenanceConfig(kwargs={"issuer": "https://x", "workers": 4})

    with pytest.raises(TypeError, match="workers"):
        create_provenance_verifier(config)


def test_the_configured_bundle_timeout_reaches_the_github_provider() -> None:
    config = GitConfig(
        github=GitHubConfig(
            instances=[{"name": "GitHub", "base_url": "https://api.github.com", "token": "t"}]
        ),
        provenance=ProvenanceConfig(bundle_fetch_timeout_seconds=12.5),
    )

    registry = create_git_registry(config)

    assert registry.providers[0]._bundle_fetch_timeout == 12.5
