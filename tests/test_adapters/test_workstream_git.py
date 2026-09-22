"""Real-Git tests for isolated workstream allocation and integration."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from niuu.adapters.evidence_signing import RsaEvidenceAuthenticator
from niuu.domain.delivery import (
    CandidateEvidence,
    RequirementEvidence,
    WorkstreamSpec,
    evidence_payload,
)
from volundr.adapters.outbound.workstream_git import (
    IntegrationConflictError,
    LocalGitWorkstreamRepository,
    WorkspaceSafetyError,
    WorkstreamGitError,
)

REPOSITORY = "https://git.example/org/repo"


def _git_available() -> str:
    binary = shutil.which("git") or ""
    if not binary:
        pytest.skip("Git is unavailable")
    result = subprocess.run([binary, "--version"], capture_output=True, check=False)
    if result.returncode:
        pytest.skip("Git executable is not functional")
    return binary


def _git(binary: str, root: Path, *arguments: str) -> str:
    result = subprocess.run(
        [binary, "-C", str(root), *arguments],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(binary: str, root: Path, message: str, *flags: str) -> str:
    return _git(
        binary,
        root,
        "-c",
        "user.name=Worker",
        "-c",
        "user.email=worker@example.invalid",
        "commit",
        "-q",
        *flags,
        "-m",
        message,
    )


def _authenticator() -> RsaEvidenceAuthenticator:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return RsaEvidenceAuthenticator(
        key_id="test-key",
        private_key_pem=pem,
        producer_keys={"trusted-runner": ("test-key",)},
    )


@pytest.fixture
def repository(tmp_path: Path) -> tuple[str, Path, str]:
    binary = _git_available()
    root = tmp_path / "repo"
    root.mkdir()
    _git(binary, root, "init", "-q", "-b", "main")
    _git(binary, root, "config", "user.name", "Test")
    _git(binary, root, "config", "user.email", "test@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "value.txt").write_text("base\n")
    (root / "verification.lock").write_text("pinned\n")
    _git(binary, root, "add", "src/value.txt", "verification.lock")
    _git(binary, root, "commit", "-q", "-m", "base")
    return binary, root, _git(binary, root, "rev-parse", "HEAD")


def _adapter(
    tmp_path: Path,
    repository: Path,
    binary: str,
    authenticator: RsaEvidenceAuthenticator,
    *,
    isolation_mode: str = "worktree",
    repository_paths: dict[str, str] | None = None,
    repository_roots: tuple[str, ...] | None = None,
) -> LocalGitWorkstreamRepository:
    fake_container = tmp_path / "fake-container"
    fake_container.write_text(
        "#!/usr/bin/env python3\n"
        "import subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'rm': raise SystemExit(0)\n"
        "mount = args[args.index('--mount') + 1]\n"
        "source = mount.split('src=', 1)[1].split(',dst=', 1)[0]\n"
        "image = next(i for i, value in enumerate(args) if '@sha256:' in value)\n"
        "raise SystemExit(subprocess.run(args[image + 1:], cwd=source).returncode)\n"
    )
    fake_container.chmod(0o700)
    pinned_blob = _git(binary, repository, "rev-parse", "HEAD:verification.lock")
    assertion = "from pathlib import Path; assert Path('src/value.txt').read_text() == 'child\\n'"
    return LocalGitWorkstreamRepository(
        workspace_root=str(tmp_path / "worktrees"),
        evidence_root=str(tmp_path / "evidence"),
        repository_roots=repository_roots or (str(tmp_path),),
        repository_paths=repository_paths,
        test_contracts={
            "unit": {
                "image": f"runner@sha256:{'f' * 64}",
                "argv": (sys.executable, "-c", assertion),
                "pinned_inputs": {"verification.lock": pinned_blob},
            }
        },
        authenticator=authenticator,
        producer_id="trusted-runner",
        isolation_mode=isolation_mode,
        git_binary=binary,
        container_binary=str(fake_container),
    )


@pytest.mark.asyncio
async def test_allocates_from_exact_repository_mapping_when_path_is_omitted(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        _authenticator(),
        repository_paths={REPOSITORY: str(root)},
    )

    allocation = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="mapped",
            repository=REPOSITORY,
            base_sha=base,
            branch_name="campaign/mapped",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )

    assert allocation.repository_path == str(root.resolve())


@pytest.mark.asyncio
async def test_requires_exact_repository_mapping_when_path_is_omitted(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        _authenticator(),
        repository_paths={f"{REPOSITORY}.git": str(root)},
    )

    with pytest.raises(WorkspaceSafetyError, match="exact repository_paths mapping"):
        await adapter.allocate(
            WorkstreamSpec(
                campaign_id="campaign-1",
                workstream_key="unmapped",
                repository=REPOSITORY,
                base_sha=base,
                branch_name="campaign/unmapped",
                worker_id="worker-1",
                allowed_paths=("src",),
                test_contract_ids=("unit",),
            )
        )


def test_rejects_configured_repository_path_outside_roots(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, _base = repository
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()

    with pytest.raises(WorkspaceSafetyError, match="outside configured roots"):
        _adapter(
            tmp_path,
            root,
            binary,
            _authenticator(),
            repository_paths={REPOSITORY: str(root)},
            repository_roots=(str(allowed_root),),
        )


@pytest.mark.asyncio
async def test_explicit_repository_path_remains_supported_without_mapping(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(tmp_path, root, binary, _authenticator())

    allocation = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="explicit",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/explicit",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )

    assert allocation.repository_path == str(root.resolve())


@pytest.mark.asyncio
async def test_allocates_self_contained_clone_for_portable_runtime_mount(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        _authenticator(),
        isolation_mode="clone",
    )
    allocation = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="portable",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/portable",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    workspace = Path(allocation.workspace_path)
    assert (workspace / ".git").is_dir()
    assert not (workspace / ".git" / "objects" / "info" / "alternates").exists()
    assert _git(binary, workspace, "cat-file", "-t", base) == "commit"


@pytest.mark.asyncio
async def test_rejects_caller_modified_allocation_and_workspace_git_config(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        _authenticator(),
        isolation_mode="clone",
    )
    spec = WorkstreamSpec(
        campaign_id="campaign-1",
        workstream_key="portable",
        repository=REPOSITORY,
        repository_path=str(root),
        base_sha=base,
        branch_name="campaign/portable",
        worker_id="worker-1",
        allowed_paths=("src",),
        test_contract_ids=("unit",),
    )
    allocation = await adapter.allocate(spec)
    forged = allocation.model_copy(update={"allowed_paths": ("src", "outside")})
    with pytest.raises(WorkspaceSafetyError, match="trusted manifest"):
        await adapter.verify(
            forged,
            attempt_id="attempt-1",
            candidate_sha=base,
            contract_id="unit",
        )
    _git(binary, Path(allocation.workspace_path), "config", "alias.untrusted", "status")
    with pytest.raises(WorkspaceSafetyError, match="configuration changed"):
        await adapter.allocate(spec)


@pytest.mark.asyncio
async def test_allows_only_local_commit_attribution_to_change_after_allocation(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        _authenticator(),
        isolation_mode="clone",
    )
    spec = WorkstreamSpec(
        campaign_id="campaign-1",
        workstream_key="portable",
        repository=REPOSITORY,
        repository_path=str(root),
        base_sha=base,
        branch_name="campaign/portable",
        worker_id="worker-1",
        allowed_paths=("src",),
        test_contract_ids=("unit",),
    )
    allocation = await adapter.allocate(spec)
    workspace = Path(allocation.workspace_path)

    _git(binary, workspace, "config", "--local", "user.name", "Developer Workstream")
    _git(
        binary,
        workspace,
        "config",
        "--local",
        "user.email",
        "developer-workstream@example.invalid",
    )

    assert await adapter.allocate(spec) == allocation


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("core.hooksPath", "/tmp/untrusted-hooks"),
        ("remote.niuu-source.url", "/tmp/another-repository"),
        ("diff.external", "/tmp/untrusted-diff"),
        ("alias.untrusted", "status"),
    ],
)
@pytest.mark.asyncio
async def test_commit_attribution_does_not_mask_other_local_config_changes(
    tmp_path: Path,
    repository: tuple[str, Path, str],
    key: str,
    value: str,
) -> None:
    binary, root, base = repository
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        _authenticator(),
        isolation_mode="clone",
    )
    spec = WorkstreamSpec(
        campaign_id="campaign-1",
        workstream_key="portable",
        repository=REPOSITORY,
        repository_path=str(root),
        base_sha=base,
        branch_name="campaign/portable",
        worker_id="worker-1",
        allowed_paths=("src",),
        test_contract_ids=("unit",),
    )
    allocation = await adapter.allocate(spec)
    workspace = Path(allocation.workspace_path)
    _git(binary, workspace, "config", "--local", "user.name", "Developer Workstream")
    _git(binary, workspace, "config", "--local", key, value)

    with pytest.raises(WorkspaceSafetyError, match="configuration changed"):
        await adapter.allocate(spec)


@pytest.mark.asyncio
async def test_rejects_local_config_includes_and_symlinked_config(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        _authenticator(),
        isolation_mode="clone",
    )
    spec = WorkstreamSpec(
        campaign_id="campaign-1",
        workstream_key="portable",
        repository=REPOSITORY,
        repository_path=str(root),
        base_sha=base,
        branch_name="campaign/portable",
        worker_id="worker-1",
        allowed_paths=("src",),
        test_contract_ids=("unit",),
    )
    allocation = await adapter.allocate(spec)
    workspace = Path(allocation.workspace_path)
    included = workspace / ".git" / "included.config"
    included.write_text("[user]\n\tname = apparently-safe\n", encoding="utf-8")
    _git(binary, workspace, "config", "--local", "include.path", str(included))
    with pytest.raises(WorkspaceSafetyError, match="include"):
        await adapter.allocate(spec)

    _git(binary, workspace, "config", "--local", "--unset-all", "include.path")
    config = workspace / ".git" / "config"
    replacement = workspace / ".git" / "replacement.config"
    replacement.write_bytes(config.read_bytes())
    config.unlink()
    config.symlink_to(replacement.name)
    with pytest.raises(WorkspaceSafetyError, match="regular file"):
        await adapter.allocate(spec)


@pytest.mark.asyncio
async def test_old_git_config_digest_manifest_fails_closed(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        _authenticator(),
        isolation_mode="clone",
    )
    spec = WorkstreamSpec(
        campaign_id="campaign-1",
        workstream_key="portable",
        repository=REPOSITORY,
        repository_path=str(root),
        base_sha=base,
        branch_name="campaign/portable",
        worker_id="worker-1",
        allowed_paths=("src",),
        test_contract_ids=("unit",),
    )
    allocation = await adapter.allocate(spec)
    manifest_path = tmp_path / "evidence" / ".allocations" / f"{allocation.allocation_id}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("git_config_digest_version", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WorkspaceSafetyError, match="digest version"):
        await adapter.allocate(spec)


@pytest.mark.asyncio
async def test_allocates_isolated_worktree_and_runs_pinned_contract(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    authenticator = _authenticator()
    adapter = _adapter(tmp_path, root, binary, authenticator)
    allocation = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="child",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/child",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    assert (
        await adapter.allocate(
            WorkstreamSpec(
                campaign_id="campaign-1",
                workstream_key="child",
                repository=REPOSITORY,
                repository_path=str(root),
                base_sha=base,
                branch_name="campaign/child",
                worker_id="worker-1",
                allowed_paths=("src",),
                test_contract_ids=("unit",),
            )
        )
        == allocation
    )
    workspace = Path(allocation.workspace_path)
    assert workspace != root
    assert _git(binary, workspace, "rev-parse", "HEAD") == base
    (workspace / "src" / "value.txt").write_text("child\n")
    _git(binary, workspace, "add", "src/value.txt")
    _commit(binary, workspace, "child")
    candidate = _git(binary, workspace, "rev-parse", "HEAD")

    receipt = await adapter.verify(
        allocation,
        attempt_id="attempt-1",
        candidate_sha=candidate,
        contract_id="unit",
    )
    assert receipt.exit_code == 0
    assert receipt.provenance is not None
    assert authenticator.verify(evidence_payload(receipt), receipt.provenance)
    assert (tmp_path / "evidence" / receipt.receipt_id / "stdout.log").exists()


@pytest.mark.asyncio
async def test_trusted_git_operations_do_not_run_repository_hooks(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    marker = tmp_path / "untrusted-hook-ran"
    hook = root / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        f"#!/bin/sh\n: > {shlex.quote(str(marker))}\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)
    adapter = _adapter(tmp_path, root, binary, _authenticator())

    await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="hook-safe",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/hook-safe",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )

    assert not marker.exists()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"repository_roots": ()}, "repository root"),
        ({"test_contracts": {}}, "verification contract"),
        ({"producer_id": "bad producer"}, "producer ID"),
        ({"container_cpus": 0}, "resource limits"),
        ({"runtime_environment": {"PRIVATE_KEY": "secret"}}, "unsupported variables"),
    ],
)
def test_rejects_unsafe_runner_configuration(
    tmp_path: Path,
    override: dict[str, object],
    message: str,
) -> None:
    kwargs = {
        "workspace_root": str(tmp_path / "worktrees"),
        "evidence_root": str(tmp_path / "evidence"),
        "repository_roots": (str(tmp_path),),
        "test_contracts": {
            "unit": {
                "image": f"runner@sha256:{'f' * 64}",
                "argv": ("python", "-m", "pytest"),
                "pinned_inputs": {"verification.lock": "a" * 40},
            }
        },
        "authenticator": _authenticator(),
        "producer_id": "trusted-runner",
        **override,
    }
    with pytest.raises(ValueError, match=message):
        LocalGitWorkstreamRepository(**kwargs)


@pytest.mark.parametrize(
    "contract",
    [
        {"image": "runner:latest", "argv": ("test",), "pinned_inputs": {"x": "a" * 40}},
        {"image": f"runner@sha256:{'f' * 64}", "argv": (), "pinned_inputs": {"x": "a" * 40}},
        {
            "image": f"runner@sha256:{'f' * 64}",
            "argv": ("test",),
            "pinned_inputs": {},
        },
        {
            "image": f"runner@sha256:{'f' * 64}",
            "argv": ("test",),
            "pinned_inputs": {"../escape": "a" * 40},
        },
    ],
)
def test_rejects_mutable_or_unsafe_test_contracts(contract: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        LocalGitWorkstreamRepository._contracts({"unit": contract})


@pytest.mark.asyncio
async def test_rejects_changes_outside_granted_paths(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(tmp_path, root, binary, _authenticator())
    allocation = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="escape",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/escape",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    workspace = Path(allocation.workspace_path)
    (workspace / "outside.txt").write_text("escape\n")
    _git(binary, workspace, "add", "outside.txt")
    _commit(binary, workspace, "escape")
    candidate = _git(binary, workspace, "rev-parse", "HEAD")
    with pytest.raises(WorkspaceSafetyError, match="ungranted path"):
        await adapter.verify(
            allocation,
            attempt_id="attempt-1",
            candidate_sha=candidate,
            contract_id="unit",
        )


@pytest.mark.asyncio
async def test_rejects_changed_symlink_that_escapes_checkout(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(tmp_path, root, binary, _authenticator())
    allocation = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="symlink",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/symlink",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    workspace = Path(allocation.workspace_path)
    (workspace / "src" / "escape").symlink_to("../../outside")
    _git(binary, workspace, "add", "src/escape")
    _commit(binary, workspace, "escaping symlink")
    candidate = _git(binary, workspace, "rev-parse", "HEAD")
    with pytest.raises(WorkspaceSafetyError, match="symlink escapes"):
        await adapter.verify(
            allocation,
            attempt_id="attempt-1",
            candidate_sha=candidate,
            contract_id="unit",
        )


@pytest.mark.asyncio
async def test_rejects_changed_pinned_test_input(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(tmp_path, root, binary, _authenticator())
    allocation = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="test-edit",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/test-edit",
            worker_id="worker-1",
            allowed_paths=("src", "verification.lock"),
            test_contract_ids=("unit",),
        )
    )
    workspace = Path(allocation.workspace_path)
    (workspace / "verification.lock").write_text("worker changed test contract\n")
    _git(binary, workspace, "add", "verification.lock")
    _commit(binary, workspace, "change test input")
    candidate = _git(binary, workspace, "rev-parse", "HEAD")
    with pytest.raises(WorkspaceSafetyError, match="Pinned verification input changed"):
        await adapter.verify(
            allocation,
            attempt_id="attempt-1",
            candidate_sha=candidate,
            contract_id="unit",
        )


@pytest.mark.asyncio
async def test_rejects_candidate_that_no_longer_descends_from_allocated_base(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(tmp_path, root, binary, _authenticator())
    allocation = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="rewritten",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/rewritten",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    workspace = Path(allocation.workspace_path)
    _git(binary, workspace, "checkout", "--orphan", "unrelated")
    _git(binary, workspace, "rm", "-q", "-rf", ".")
    (workspace / "src").mkdir()
    (workspace / "src" / "value.txt").write_text("child\n")
    (workspace / "verification.lock").write_text("pinned\n")
    _git(binary, workspace, "add", "src/value.txt", "verification.lock")
    _commit(binary, workspace, "unrelated")
    candidate = _git(binary, workspace, "rev-parse", "HEAD")
    with pytest.raises(WorkspaceSafetyError, match="no longer descends"):
        await adapter.verify(
            allocation,
            attempt_id="attempt-1",
            candidate_sha=candidate,
            contract_id="unit",
        )


@pytest.mark.parametrize("isolation_mode", ["worktree", "clone"])
@pytest.mark.asyncio
async def test_integrates_approved_commit_with_expected_head_cas(
    tmp_path: Path,
    repository: tuple[str, Path, str],
    isolation_mode: str,
) -> None:
    binary, root, base = repository
    authenticator = _authenticator()
    adapter = _adapter(
        tmp_path,
        root,
        binary,
        authenticator,
        isolation_mode=isolation_mode,
    )
    child = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="child",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/child",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    integration = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="integration",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/integration",
            worker_id="integration-service",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    child_path = Path(child.workspace_path)
    (child_path / "src" / "value.txt").write_text("child\n")
    _git(binary, child_path, "add", "src/value.txt")
    _commit(binary, child_path, "child")
    candidate = _git(binary, child_path, "rev-parse", "HEAD")
    tree = _git(binary, child_path, "rev-parse", "HEAD^{tree}")
    evidence = CandidateEvidence(
        campaign_id="campaign-1",
        workstream_key="child",
        attempt_id="attempt-1",
        worker_id="worker-1",
        repository=REPOSITORY,
        base_sha=base,
        candidate_sha=candidate,
        candidate_tree=tree,
        requirements=(
            RequirementEvidence(
                requirement_id="REQ-1",
                implementation_paths=("src/value.txt",),
                verification_contract_ids=("unit",),
            ),
        ),
    )
    receipt = await adapter.integrate(
        integration,
        expected_integration_head=base,
        evidence=evidence,
    )
    assert receipt.previous_integration_sha == base
    assert receipt.resulting_sha != base
    assert (Path(integration.workspace_path) / "src" / "value.txt").read_text() == "child\n"
    source = await adapter.publication_source(integration, receipt)
    assert source.candidate_sha == receipt.resulting_sha
    assert source.candidate_tree == receipt.resulting_tree
    inspection = await adapter.inspect_integration(integration, receipt)
    assert inspection.integration_allocation_id == integration.allocation_id
    assert inspection.base_sha == base
    assert inspection.candidate_sha == receipt.resulting_sha
    assert inspection.changed_paths == ("src/value.txt",)
    assert "+child" in inspection.patch
    replayed = receipt.model_copy(update={"integration_allocation_id": "another-allocation"})
    with pytest.raises(WorkspaceSafetyError, match="does not belong"):
        await adapter.inspect_integration(integration, replayed)
    with pytest.raises(WorkspaceSafetyError, match="Integration head moved"):
        await adapter.integrate(
            integration,
            expected_integration_head=base,
            evidence=evidence,
        )


@pytest.mark.asyncio
async def test_integration_preserves_initially_empty_and_redundant_commits(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(tmp_path, root, binary, _authenticator(), isolation_mode="clone")
    child = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="child-empty",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/child-empty",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    integration = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="integration-empty",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/integration-empty",
            worker_id="integration-service",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    child_path = Path(child.workspace_path)
    (child_path / "src" / "value.txt").write_text("shared\n")
    _git(binary, child_path, "add", "src/value.txt")
    _commit(binary, child_path, "candidate content")
    _commit(binary, child_path, "attempt marker", "--allow-empty")
    candidate = _git(binary, child_path, "rev-parse", "HEAD")
    candidate_tree = _git(binary, child_path, "rev-parse", "HEAD^{tree}")

    integration_path = Path(integration.workspace_path)
    (integration_path / "src" / "value.txt").write_text("shared\n")
    _git(binary, integration_path, "add", "src/value.txt")
    _commit(binary, integration_path, "existing equivalent content")
    integration_head = _git(binary, integration_path, "rev-parse", "HEAD")
    integration_tree = _git(binary, integration_path, "rev-parse", "HEAD^{tree}")
    evidence = CandidateEvidence(
        campaign_id="campaign-1",
        workstream_key="child-empty",
        attempt_id="attempt-1",
        worker_id="worker-1",
        repository=REPOSITORY,
        base_sha=base,
        candidate_sha=candidate,
        candidate_tree=candidate_tree,
        requirements=(
            RequirementEvidence(
                requirement_id="REQ-1",
                implementation_paths=("src/value.txt",),
                verification_contract_ids=("unit",),
            ),
        ),
    )

    receipt = await adapter.integrate(
        integration,
        expected_integration_head=integration_head,
        evidence=evidence,
    )

    assert receipt.previous_integration_sha == integration_head
    assert len(receipt.integrated_commits) == 2
    assert receipt.resulting_tree == integration_tree == candidate_tree
    assert receipt.resulting_sha != integration_head
    assert _git(binary, integration_path, "status", "--porcelain=v1") == ""
    assert all(
        _git(binary, integration_path, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
        == ""
        for sha in receipt.integrated_commits
    )


@pytest.mark.asyncio
async def test_integration_conflict_rolls_back_all_prior_candidate_commits(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(tmp_path, root, binary, _authenticator(), isolation_mode="clone")
    child = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="child-conflict",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/child-conflict",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    integration = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="integration-conflict",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/integration-conflict",
            worker_id="integration-service",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    child_path = Path(child.workspace_path)
    (child_path / "src" / "first.txt").write_text("first\n")
    _git(binary, child_path, "add", "src/first.txt")
    _commit(binary, child_path, "first candidate change")
    (child_path / "src" / "value.txt").write_text("candidate\n")
    _git(binary, child_path, "add", "src/value.txt")
    _commit(binary, child_path, "conflicting candidate change")
    candidate = _git(binary, child_path, "rev-parse", "HEAD")
    candidate_tree = _git(binary, child_path, "rev-parse", "HEAD^{tree}")

    integration_path = Path(integration.workspace_path)
    (integration_path / "src" / "value.txt").write_text("integration\n")
    _git(binary, integration_path, "add", "src/value.txt")
    _commit(binary, integration_path, "integration-side change")
    integration_head = _git(binary, integration_path, "rev-parse", "HEAD")
    integration_tree = _git(binary, integration_path, "rev-parse", "HEAD^{tree}")
    evidence = CandidateEvidence(
        campaign_id="campaign-1",
        workstream_key="child-conflict",
        attempt_id="attempt-1",
        worker_id="worker-1",
        repository=REPOSITORY,
        base_sha=base,
        candidate_sha=candidate,
        candidate_tree=candidate_tree,
        requirements=(
            RequirementEvidence(
                requirement_id="REQ-1",
                implementation_paths=("src/first.txt", "src/value.txt"),
                verification_contract_ids=("unit",),
            ),
        ),
    )

    with pytest.raises(IntegrationConflictError, match="Approved commits conflict"):
        await adapter.integrate(
            integration,
            expected_integration_head=integration_head,
            evidence=evidence,
        )

    assert _git(binary, integration_path, "rev-parse", "HEAD") == integration_head
    assert _git(binary, integration_path, "rev-parse", "HEAD^{tree}") == integration_tree
    assert not (integration_path / "src" / "first.txt").exists()
    assert _git(binary, integration_path, "status", "--porcelain=v1") == ""


@pytest.mark.asyncio
async def test_integration_rejects_dirty_workspace_before_applying_candidate(
    tmp_path: Path,
    repository: tuple[str, Path, str],
) -> None:
    binary, root, base = repository
    adapter = _adapter(tmp_path, root, binary, _authenticator(), isolation_mode="clone")
    child = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="child-clean",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/child-clean",
            worker_id="worker-1",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    integration = await adapter.allocate(
        WorkstreamSpec(
            campaign_id="campaign-1",
            workstream_key="integration-clean",
            repository=REPOSITORY,
            repository_path=str(root),
            base_sha=base,
            branch_name="campaign/integration-clean",
            worker_id="integration-service",
            allowed_paths=("src",),
            test_contract_ids=("unit",),
        )
    )
    child_path = Path(child.workspace_path)
    (child_path / "src" / "value.txt").write_text("candidate\n")
    _git(binary, child_path, "add", "src/value.txt")
    _commit(binary, child_path, "candidate")
    candidate = _git(binary, child_path, "rev-parse", "HEAD")
    candidate_tree = _git(binary, child_path, "rev-parse", "HEAD^{tree}")
    integration_path = Path(integration.workspace_path)
    (integration_path / "local.txt").write_text("preexisting\n")
    evidence = CandidateEvidence(
        campaign_id="campaign-1",
        workstream_key="child-clean",
        attempt_id="attempt-1",
        worker_id="worker-1",
        repository=REPOSITORY,
        base_sha=base,
        candidate_sha=candidate,
        candidate_tree=candidate_tree,
        requirements=(
            RequirementEvidence(
                requirement_id="REQ-1",
                implementation_paths=("src/value.txt",),
                verification_contract_ids=("unit",),
            ),
        ),
    )

    with pytest.raises(WorkspaceSafetyError, match="must be clean"):
        await adapter.integrate(
            integration,
            expected_integration_head=base,
            evidence=evidence,
        )

    assert _git(binary, integration_path, "rev-parse", "HEAD") == base


def _minimal_repository(
    tmp_path: Path,
    *,
    git_binary: str,
    container_binary: str = "docker",
    **overrides: object,
) -> LocalGitWorkstreamRepository:
    return LocalGitWorkstreamRepository(
        workspace_root=str(tmp_path / "worktrees"),
        evidence_root=str(tmp_path / "evidence"),
        repository_roots=(str(tmp_path),),
        test_contracts={
            "unit": {
                "image": f"runner@sha256:{'f' * 64}",
                "argv": (sys.executable, "-c", "pass"),
                "pinned_inputs": {"verification.lock": "e" * 40},
            }
        },
        authenticator=_authenticator(),
        producer_id="trusted-runner",
        git_binary=git_binary,
        container_binary=container_binary,
        **overrides,
    )


@pytest.mark.asyncio
async def test_run_kills_process_and_raises_once_combined_output_exceeds_the_cap(
    tmp_path: Path,
):
    """A chatty candidate-controlled verification process must be bounded in memory.

    Reading everything via `communicate()` before checking a size limit lets the
    process buffer arbitrarily much output for the whole command timeout. The
    cap must instead be enforced while streaming, and the process killed as soon
    as it is crossed.
    """
    binary = _git_available()
    adapter = _minimal_repository(
        tmp_path,
        git_binary=binary,
        max_output_bytes=1024,
        max_inspection_patch_bytes=512,
        output_read_chunk_bytes=256,
        command_timeout_seconds=10,
    )
    chatty = (
        sys.executable,
        "-c",
        "import sys\nwhile True:\n    sys.stdout.write('x' * 4096)\n    sys.stdout.flush()\n",
    )

    with pytest.raises(WorkstreamGitError, match="exceeded the configured limit"):
        await adapter._run(chatty, tmp_path)


@pytest.mark.asyncio
async def test_run_returns_full_output_under_the_cap(tmp_path: Path):
    binary = _git_available()
    adapter = _minimal_repository(
        tmp_path, git_binary=binary, max_output_bytes=4096, max_inspection_patch_bytes=2048
    )
    quiet = (sys.executable, "-c", "import sys; sys.stdout.write('ok')")

    code, stdout, stderr = await adapter._run(quiet, tmp_path)

    assert code == 0
    assert stdout == b"ok"
    assert stderr == b""


@pytest.mark.asyncio
async def test_cleanup_container_run_removes_staging_even_when_container_rm_fails(
    tmp_path: Path,
):
    """Both cleanup steps must run independently: one failing must not skip the other."""
    binary = _git_available()
    adapter = _minimal_repository(
        tmp_path, git_binary=binary, container_binary=str(tmp_path / "does-not-exist")
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "file").write_text("leftover")

    with pytest.raises(FileNotFoundError):
        await adapter._cleanup_container_run("container", staging, tmp_path, None)

    assert not staging.exists()


@pytest.mark.asyncio
async def test_cleanup_container_run_preserves_original_exception_and_chains_cleanup_failure(
    tmp_path: Path,
):
    binary = _git_available()
    adapter = _minimal_repository(
        tmp_path, git_binary=binary, container_binary=str(tmp_path / "does-not-exist")
    )
    missing_staging = tmp_path / "missing-staging"
    original = WorkstreamGitError("verification failed")

    # The real caller (`_run_in_container`) re-raises `original` itself once this
    # returns; the helper's job is only to make sure the cleanup failure is
    # visible on it rather than disappearing, never to raise a second exception
    # in its place.
    await adapter._cleanup_container_run("container", missing_staging, tmp_path, original)

    assert isinstance(original.__cause__, FileNotFoundError)


@pytest.mark.asyncio
async def test_cleanup_container_run_raises_cleanup_failure_when_nothing_else_failed(
    tmp_path: Path,
):
    binary = _git_available()
    adapter = _minimal_repository(
        tmp_path, git_binary=binary, container_binary=str(tmp_path / "does-not-exist")
    )
    missing_staging = tmp_path / "missing-staging"

    with pytest.raises(FileNotFoundError):
        await adapter._cleanup_container_run("container", missing_staging, tmp_path, None)
