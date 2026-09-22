"""Real Git worktree isolation and trusted verification for delivery workstreams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import NAMESPACE_URL, uuid5

from niuu.domain.delivery import (
    CandidateEvidence,
    IntegratedCandidateIdentity,
    IntegrationCandidateInspection,
    IntegrationReceipt,
    PublicationSource,
    VerificationReceipt,
    WorkspaceAllocation,
    WorkstreamSpec,
    evidence_payload,
)
from niuu.ports.delivery import EvidenceAuthenticator, WorkstreamRepository

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_IMAGE_DIGEST = re.compile(r"^.+@sha256:[a-f0-9]{64}$")
_GIT_CONFIG_DIGEST_VERSION = 2
_COMMIT_ATTRIBUTION_KEYS = frozenset({"user.name", "user.email"})


@dataclass(frozen=True)
class _TestContract:
    image: str
    argv: tuple[str, ...]
    pinned_inputs: dict[str, str]


class WorkstreamGitError(RuntimeError):
    """A workstream Git operation could not be completed safely."""


class WorkspaceSafetyError(WorkstreamGitError):
    """A repository, worktree, ref, or changed path is outside its grant."""


class IntegrationConflictError(WorkstreamGitError):
    """Approved commits conflict and require a coder-owned repair workstream."""


class _OutputCapExceededError(Exception):
    """Internal signal that a running process' combined output crossed the cap.

    Never escapes `_run`; always translated into `WorkstreamGitError`.
    """


class LocalGitWorkstreamRepository(WorkstreamRepository):
    """Allocate isolated worktrees and run only configured argv test contracts."""

    def __init__(
        self,
        *,
        workspace_root: str,
        evidence_root: str,
        repository_roots: tuple[str, ...] | list[str],
        repository_paths: dict[str, str] | None = None,
        test_contracts: dict[str, dict[str, object]],
        authenticator: EvidenceAuthenticator,
        producer_id: str,
        isolation_mode: str = "worktree",
        git_binary: str = "git",
        container_binary: str = "docker",
        container_memory: str = "2g",
        container_cpus: float = 2.0,
        container_pids_limit: int = 256,
        runtime_environment: dict[str, str] | None = None,
        command_timeout_seconds: float = 900.0,
        max_output_bytes: int = 16 * 1024 * 1024,
        max_inspection_patch_bytes: int = 1024 * 1024,
        output_read_chunk_bytes: int = 65536,
    ) -> None:
        if not repository_roots:
            raise ValueError("At least one repository root is required")
        if not test_contracts:
            raise ValueError("At least one verification contract is required")
        if not _SAFE_ID.fullmatch(producer_id):
            raise ValueError("Evidence producer ID is invalid")
        self._workspace_root = Path(workspace_root).resolve()
        self._evidence_root = Path(evidence_root).resolve()
        self._repository_roots = tuple(Path(path).resolve() for path in repository_roots)
        self._repository_paths = self._configured_repository_paths(repository_paths or {})
        self._test_contracts = self._contracts(test_contracts)
        if any(not _SAFE_ID.fullmatch(name) for name in self._test_contracts):
            raise ValueError("Verification contract IDs must be stable identifiers")
        self._authenticator = authenticator
        self._producer_id = producer_id
        if isolation_mode not in {"worktree", "clone"}:
            raise ValueError("Isolation mode must be 'worktree' or 'clone'")
        self._isolation_mode = isolation_mode
        self._git_binary = git_binary
        self._container_binary = container_binary
        if container_cpus <= 0 or container_pids_limit <= 0 or not container_memory:
            raise ValueError("Container resource limits must be positive")
        allowed_environment = {
            "PATH",
            "LANG",
            "LC_ALL",
            "DOCKER_CONFIG",
            "DOCKER_CONTEXT",
            "DOCKER_HOST",
            "CONTAINER_HOST",
        }
        supplied_environment = runtime_environment or {}
        if set(supplied_environment) - allowed_environment:
            raise ValueError("Runtime environment contains unsupported variables")
        self._runtime_environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            **supplied_environment,
        }
        self._container_memory = container_memory
        self._container_cpus = container_cpus
        self._container_pids_limit = container_pids_limit
        self._timeout = command_timeout_seconds
        self._max_output_bytes = max_output_bytes
        if max_inspection_patch_bytes <= 0 or max_inspection_patch_bytes > max_output_bytes:
            raise ValueError("Inspection patch limit must fit within the output limit")
        self._max_inspection_patch_bytes = max_inspection_patch_bytes
        if output_read_chunk_bytes <= 0:
            raise ValueError("Output read chunk size must be positive")
        self._output_read_chunk_bytes = output_read_chunk_bytes
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._evidence_root.mkdir(parents=True, exist_ok=True)

    async def allocate(self, spec: WorkstreamSpec) -> WorkspaceAllocation:
        repository = self._repository_for(spec)
        await self._git(repository, "cat-file", "-e", f"{spec.base_sha}^{{commit}}")
        await self._git(repository, "check-ref-format", "--branch", spec.branch_name)
        unknown_contracts = set(spec.test_contract_ids) - self._test_contracts.keys()
        if unknown_contracts:
            names = ", ".join(sorted(unknown_contracts))
            raise WorkspaceSafetyError(f"Unknown verification contracts: {names}")

        allocation_id = str(
            uuid5(
                NAMESPACE_URL,
                f"niuu-delivery:{spec.campaign_id}:{spec.workstream_key}:{spec.base_sha}",
            )
        )
        workspace = self._workspace_path(allocation_id)
        allocation = WorkspaceAllocation(
            allocation_id=allocation_id,
            campaign_id=spec.campaign_id,
            workstream_key=spec.workstream_key,
            repository=spec.repository,
            workspace_path=str(workspace),
            repository_path=str(repository),
            base_sha=spec.base_sha,
            branch_name=spec.branch_name,
            worker_id=spec.worker_id,
            allowed_paths=spec.allowed_paths,
            test_contract_ids=spec.test_contract_ids,
        )

        if workspace.exists():
            self._validate_allocation(allocation)
            head = await self._git(workspace, "rev-parse", "HEAD^{commit}")
            if not await self._is_ancestor(repository, spec.base_sha, head):
                raise WorkspaceSafetyError("Existing allocation no longer contains its base commit")
            return allocation

        if self._isolation_mode == "clone":
            await self._git(
                self._workspace_root,
                "clone",
                "--no-checkout",
                "--no-local",
                "--origin",
                "niuu-source",
                "--",
                str(repository),
                str(workspace),
            )
            await self._git(workspace, "checkout", "-b", spec.branch_name, spec.base_sha)
            alternates = workspace / ".git" / "objects" / "info" / "alternates"
            if alternates.exists():
                raise WorkspaceSafetyError(
                    "Isolated clone unexpectedly depends on external objects"
                )
        else:
            branch_exists = await self._git_ok(
                repository,
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{spec.branch_name}",
            )
            arguments = ["worktree", "add"]
            if branch_exists:
                branch_head = await self._git(
                    repository, "rev-parse", f"refs/heads/{spec.branch_name}"
                )
                if branch_head != spec.base_sha:
                    raise WorkspaceSafetyError(
                        "Requested workstream branch already exists at another commit"
                    )
                arguments.extend([str(workspace), spec.branch_name])
            else:
                arguments.extend(["-b", spec.branch_name, str(workspace), spec.base_sha])
            await self._git(repository, *arguments)
        self._write_allocation_manifest(allocation, workspace)
        self._validate_allocation(allocation)
        return allocation

    async def verify(
        self,
        allocation: WorkspaceAllocation,
        *,
        attempt_id: str,
        candidate_sha: str,
        contract_id: str,
    ) -> VerificationReceipt:
        workspace = self._validate_allocation(allocation)
        if contract_id not in allocation.test_contract_ids:
            raise WorkspaceSafetyError("Verification contract is not granted to this allocation")
        contract = self._test_contracts.get(contract_id)
        if contract is None:
            raise WorkspaceSafetyError("Verification contract is not configured")
        if not _SAFE_ID.fullmatch(attempt_id):
            raise WorkspaceSafetyError("Attempt ID is invalid")

        head = await self._git(workspace, "rev-parse", "HEAD^{commit}")
        if head != candidate_sha:
            raise WorkspaceSafetyError("Workspace head does not match the requested candidate")
        if not await self._is_ancestor(workspace, allocation.base_sha, candidate_sha):
            raise WorkspaceSafetyError("Candidate no longer descends from its allocated base")
        tree = await self._git(workspace, "rev-parse", "HEAD^{tree}")
        await self._check_changed_paths(workspace, allocation, candidate_sha)
        await self._check_pinned_inputs(workspace, candidate_sha, contract)

        started = datetime.now(UTC)
        receipt_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{allocation.allocation_id}:{attempt_id}:{contract_id}:{candidate_sha}",
            )
        )
        exit_code, stdout, stderr = await self._run_in_container(
            workspace,
            candidate_sha,
            receipt_id,
            contract,
        )
        completed = datetime.now(UTC)
        stdout_digest = hashlib.sha256(stdout).hexdigest()
        stderr_digest = hashlib.sha256(stderr).hexdigest()
        command_digest = hashlib.sha256(
            json.dumps(
                {
                    "image": contract.image,
                    "argv": contract.argv,
                    "pinned_inputs": contract.pinned_inputs,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._write_log(receipt_id, stdout, stderr)
        receipt = VerificationReceipt(
            receipt_id=receipt_id,
            campaign_id=allocation.campaign_id,
            workstream_key=allocation.workstream_key,
            attempt_id=attempt_id,
            repository=allocation.repository,
            candidate_sha=candidate_sha,
            candidate_tree=tree,
            base_sha=allocation.base_sha,
            contract_id=contract_id,
            command_digest=command_digest,
            exit_code=exit_code,
            stdout_digest=stdout_digest,
            stderr_digest=stderr_digest,
            started_at=started,
            completed_at=completed,
        )
        provenance = self._authenticator.sign(evidence_payload(receipt), self._producer_id)
        return receipt.model_copy(update={"provenance": provenance})

    async def integrate(
        self,
        integration: WorkspaceAllocation,
        *,
        expected_integration_head: str,
        evidence: CandidateEvidence,
    ) -> IntegrationReceipt:
        workspace = self._validate_allocation(integration)
        if evidence.campaign_id != integration.campaign_id:
            raise WorkspaceSafetyError("Candidate belongs to another campaign")
        if evidence.repository != integration.repository:
            raise WorkspaceSafetyError("Candidate belongs to another repository")
        current_head = await self._git(workspace, "rev-parse", "HEAD^{commit}")
        if current_head != expected_integration_head:
            raise WorkspaceSafetyError("Integration head moved; replan from the current head")
        current_tree = await self._git(workspace, "rev-parse", "HEAD^{tree}")
        if await self._git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
            raise WorkspaceSafetyError("Integration workspace must be clean before integration")

        candidate_workspace = self._candidate_workspace(evidence)
        if not await self._git_ok(
            workspace, "cat-file", "-e", f"{evidence.candidate_sha}^{{commit}}"
        ):
            await self._git(
                workspace,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--",
                str(candidate_workspace),
                evidence.candidate_sha,
            )
        if not await self._is_ancestor(workspace, evidence.base_sha, evidence.candidate_sha):
            raise WorkspaceSafetyError("Candidate does not descend from its evidence base")
        candidate_tree = await self._git(
            workspace, "rev-parse", f"{evidence.candidate_sha}^{{tree}}"
        )
        if candidate_tree != evidence.candidate_tree:
            raise WorkspaceSafetyError("Candidate tree does not match accepted evidence")
        commits_raw = await self._git(
            workspace,
            "rev-list",
            "--reverse",
            f"{evidence.base_sha}..{evidence.candidate_sha}",
        )
        commits = tuple(line for line in commits_raw.splitlines() if line)
        if not commits:
            raise WorkspaceSafetyError("Candidate contains no commits beyond its declared base")

        integrated: list[str] = []
        try:
            for commit in commits:
                await self._git(
                    workspace,
                    "-c",
                    "user.name=Niuu Delivery",
                    "-c",
                    "user.email=delivery@niuu.invalid",
                    "cherry-pick",
                    "--allow-empty",
                    "--keep-redundant-commits",
                    commit,
                )
                integrated.append(await self._git(workspace, "rev-parse", "HEAD^{commit}"))
        except WorkstreamGitError as exc:
            conflict = bool(await self._git(workspace, "ls-files", "--unmerged"))
            await self._git_ok(workspace, "cherry-pick", "--abort")
            await self._git(workspace, "reset", "--hard", current_head)
            restored_head = await self._git(workspace, "rev-parse", "HEAD^{commit}")
            restored_tree = await self._git(workspace, "rev-parse", "HEAD^{tree}")
            if restored_head != current_head or restored_tree != current_tree:
                raise WorkspaceSafetyError(
                    "Integration rollback did not restore the captured head and tree"
                ) from exc
            if conflict:
                raise IntegrationConflictError(
                    "Approved commits conflict; dispatch a coder repair workstream"
                ) from exc
            raise

        resulting_sha = await self._git(workspace, "rev-parse", "HEAD^{commit}")
        resulting_tree = await self._git(workspace, "rev-parse", "HEAD^{tree}")
        receipt = IntegrationReceipt(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"integrate:{integration.allocation_id}:{current_head}:{evidence.candidate_sha}",
                )
            ),
            campaign_id=integration.campaign_id,
            integration_allocation_id=integration.allocation_id,
            workstream_key=evidence.workstream_key,
            attempt_id=evidence.attempt_id,
            repository=integration.repository,
            base_sha=integration.base_sha,
            candidate_sha=evidence.candidate_sha,
            candidate_tree=evidence.candidate_tree,
            previous_integration_sha=current_head,
            integrated_commits=tuple(integrated),
            resulting_sha=resulting_sha,
            resulting_tree=resulting_tree,
            completed_at=datetime.now(UTC),
        )
        provenance = self._authenticator.sign(evidence_payload(receipt), self._producer_id)
        return receipt.model_copy(update={"provenance": provenance})

    async def publication_source(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
    ) -> PublicationSource:
        workspace, head, tree = await self._validated_integration_workspace(allocation, receipt)
        return PublicationSource(
            repository=allocation.repository,
            repository_path=str(workspace),
            candidate_sha=head,
            candidate_tree=tree,
        )

    async def inspect_integration(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
    ) -> IntegrationCandidateInspection:
        workspace, head, tree = await self._validated_integration_workspace(allocation, receipt)
        changed_raw = await self._git(
            workspace,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
            f"{receipt.base_sha}..{head}",
            "--",
        )
        changed_paths = tuple(item for item in changed_raw.split("\x00") if item)
        patch = await self._git(
            workspace,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "--full-index",
            f"{receipt.base_sha}..{head}",
            "--",
        )
        if len(patch.encode("utf-8")) > self._max_inspection_patch_bytes:
            raise WorkspaceSafetyError("Integration inspection patch exceeds its configured limit")
        return IntegrationCandidateInspection(
            receipt_id=receipt.receipt_id,
            campaign_id=receipt.campaign_id,
            integration_allocation_id=receipt.integration_allocation_id,
            repository=receipt.repository,
            base_sha=receipt.base_sha,
            candidate_sha=head,
            candidate_tree=tree,
            receipt_ids=(receipt.receipt_id,),
            integrated_candidates=(
                IntegratedCandidateIdentity(
                    workstream_key=receipt.workstream_key,
                    attempt_id=receipt.attempt_id,
                    candidate_sha=receipt.candidate_sha,
                    candidate_tree=receipt.candidate_tree,
                ),
            ),
            changed_paths=changed_paths,
            patch=patch,
            inspected_at=datetime.now(UTC),
        )

    async def _validated_integration_workspace(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
    ) -> tuple[Path, str, str]:
        workspace = self._validate_allocation(allocation)
        if (
            receipt.campaign_id != allocation.campaign_id
            or receipt.integration_allocation_id != allocation.allocation_id
            or receipt.repository != allocation.repository
            or receipt.base_sha != allocation.base_sha
        ):
            raise WorkspaceSafetyError("Integration receipt does not belong to this allocation")
        head = await self._git(workspace, "rev-parse", "HEAD^{commit}")
        tree = await self._git(workspace, "rev-parse", "HEAD^{tree}")
        if head != receipt.resulting_sha or tree != receipt.resulting_tree:
            raise WorkspaceSafetyError("Integration workspace moved after its receipt was issued")
        if not await self._is_ancestor(workspace, receipt.base_sha, head):
            raise WorkspaceSafetyError("Integration candidate no longer descends from its base")
        return workspace, head, tree

    def _repository(self, path: str) -> Path:
        repository = Path(path)
        if not repository.is_absolute():
            raise WorkspaceSafetyError("Repository path must be absolute")
        repository = repository.resolve()
        if not any(repository.is_relative_to(root) for root in self._repository_roots):
            raise WorkspaceSafetyError("Repository is outside configured roots")
        if not repository.is_dir() or not (repository / ".git").exists():
            raise WorkspaceSafetyError("Repository must be an existing non-bare Git checkout")
        return repository

    def _configured_repository_paths(self, raw: dict[str, str]) -> dict[str, Path]:
        repositories: dict[str, Path] = {}
        for identity, path in raw.items():
            if not isinstance(identity, str) or not identity or identity != identity.strip():
                raise ValueError("Repository path identities must be non-empty exact strings")
            if not isinstance(path, str) or not path:
                raise ValueError("Configured repository paths must be non-empty strings")
            repositories[identity] = self._repository(path)
        return repositories

    def _repository_for(self, spec: WorkstreamSpec) -> Path:
        configured = self._repository_paths.get(spec.repository)
        if spec.repository_path is None:
            if configured is None:
                raise WorkspaceSafetyError(
                    "Repository path is omitted; configure an exact repository_paths mapping "
                    "or supply repository_path"
                )
            return configured

        repository = self._repository(spec.repository_path)
        if configured is not None and repository != configured:
            raise WorkspaceSafetyError(
                "Explicit repository path does not match the configured repository identity"
            )
        return repository

    @staticmethod
    def _contracts(raw: dict[str, dict[str, object]]) -> dict[str, _TestContract]:
        contracts: dict[str, _TestContract] = {}
        for contract_id, value in raw.items():
            image = str(value.get("image") or "")
            argv_value = value.get("argv")
            pinned_value = value.get("pinned_inputs")
            if not _IMAGE_DIGEST.fullmatch(image):
                raise ValueError("Verification images must be pinned by sha256 digest")
            if not isinstance(argv_value, (list, tuple)) or not argv_value:
                raise ValueError("Verification contracts must contain a non-empty argv")
            argv = tuple(str(item) for item in argv_value)
            if any(not item for item in argv):
                raise ValueError("Verification contract argv items must not be empty")
            if not isinstance(pinned_value, dict) or not pinned_value:
                raise ValueError("Verification contracts must pin immutable input blobs")
            pinned_inputs = {str(path): str(digest) for path, digest in pinned_value.items()}
            for path, digest in pinned_inputs.items():
                pure = PurePosixPath(path)
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", digest)
                ):
                    raise ValueError("Pinned verification inputs must be safe paths and Git blobs")
            contracts[contract_id] = _TestContract(image, argv, pinned_inputs)
        return contracts

    def _workspace_path(self, allocation_id: str) -> Path:
        path = (self._workspace_root / allocation_id).resolve()
        if not path.is_relative_to(self._workspace_root):
            raise WorkspaceSafetyError("Allocation path escapes the workspace root")
        return path

    def _candidate_workspace(self, evidence: CandidateEvidence) -> Path:
        allocation_id = str(
            uuid5(
                NAMESPACE_URL,
                f"niuu-delivery:{evidence.campaign_id}:{evidence.workstream_key}:{evidence.base_sha}",
            )
        )
        workspace = self._workspace_path(allocation_id)
        if not workspace.is_dir() or not (workspace / ".git").exists():
            raise WorkspaceSafetyError("Candidate allocation is unavailable")
        return workspace

    def _validate_allocation(self, allocation: WorkspaceAllocation) -> Path:
        expected = self._workspace_path(allocation.allocation_id)
        supplied = Path(allocation.workspace_path).resolve()
        if supplied != expected:
            raise WorkspaceSafetyError("Allocation workspace path is not canonical")
        self._repository(allocation.repository_path)
        if not supplied.is_dir() or not (supplied / ".git").exists():
            raise WorkspaceSafetyError("Allocated Git worktree does not exist")
        manifest_path = self._allocation_manifest_path(allocation.allocation_id)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkspaceSafetyError("Trusted allocation manifest is missing or invalid") from exc
        if manifest.get("allocation") != allocation.model_dump(mode="json"):
            raise WorkspaceSafetyError("Allocation fields differ from the trusted manifest")
        if manifest.get("git_config_digest_version") != _GIT_CONFIG_DIGEST_VERSION:
            raise WorkspaceSafetyError("Workspace Git configuration digest version is unsupported")
        if manifest.get("git_config_digest") != self._git_config_digest(allocation, supplied):
            raise WorkspaceSafetyError("Workspace Git configuration changed after allocation")
        return supplied

    def _allocation_manifest_path(self, allocation_id: str) -> Path:
        directory = (self._evidence_root / ".allocations").resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = (directory / f"{allocation_id}.json").resolve()
        if not path.is_relative_to(directory):
            raise WorkspaceSafetyError("Allocation manifest path escapes its trusted root")
        return path

    def _write_allocation_manifest(
        self,
        allocation: WorkspaceAllocation,
        workspace: Path,
    ) -> None:
        path = self._allocation_manifest_path(allocation.allocation_id)
        data = {
            "allocation": allocation.model_dump(mode="json"),
            "git_config_digest_version": _GIT_CONFIG_DIGEST_VERSION,
            "git_config_digest": self._git_config_digest(allocation, workspace),
        }
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def _git_config_digest(
        self,
        allocation: WorkspaceAllocation,
        workspace: Path,
    ) -> str:
        marker = workspace / ".git"
        if marker.is_symlink():
            raise WorkspaceSafetyError("Workspace Git metadata must not be a symlink")
        if marker.is_dir():
            git_directory = marker.resolve()
            allowed_root = workspace.resolve()
            if not git_directory.is_relative_to(allowed_root):
                raise WorkspaceSafetyError("Clone Git metadata escapes its allocation")
            common = git_directory
        else:
            try:
                declaration = marker.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise WorkspaceSafetyError("Worktree Git metadata pointer is unreadable") from exc
            if not declaration.startswith("gitdir: "):
                raise WorkspaceSafetyError("Worktree Git metadata pointer is invalid")
            git_directory = Path(declaration.removeprefix("gitdir: "))
            if not git_directory.is_absolute():
                git_directory = marker.parent / git_directory
            git_directory = git_directory.resolve()
            repository_git = (Path(allocation.repository_path).resolve() / ".git").resolve()
            if not git_directory.is_relative_to(repository_git):
                raise WorkspaceSafetyError("Worktree Git metadata escapes its repository")
            common_file = git_directory / "commondir"
            common = (
                (git_directory / common_file.read_text(encoding="utf-8").strip()).resolve()
                if common_file.exists()
                else git_directory
            )
            if not common.is_relative_to(repository_git):
                raise WorkspaceSafetyError("Worktree common Git metadata escapes its repository")
        digest = hashlib.sha256()
        digest.update(f"v{_GIT_CONFIG_DIGEST_VERSION}\0".encode())
        for config in (common / "config", git_directory / "config.worktree"):
            digest.update(str(config.relative_to(common.parent)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(self._sealed_git_config(config))
            digest.update(b"\0")
        return digest.hexdigest()

    def _sealed_git_config(self, config: Path) -> bytes:
        """Return semantic config bytes while excluding commit attribution only.

        ``user.name`` and ``user.email`` identify commit authors; they never grant
        delivery authority or satisfy review independence. Agents may set those two
        repository-local values so Git can create commits. Every other effective
        local key remains sealed, and config includes are refused rather than
        trusting mutable content outside the allocation manifest.
        """
        if not config.exists():
            return b""
        if config.is_symlink() or not config.is_file():
            raise WorkspaceSafetyError("Workspace Git configuration must be a regular file")
        try:
            completed = subprocess.run(
                (
                    self._git_binary,
                    "config",
                    "--no-includes",
                    "--null",
                    "--file",
                    str(config),
                    "--list",
                ),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=self._runtime_environment,
                check=False,
                timeout=self._timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkspaceSafetyError("Workspace Git configuration cannot be parsed") from exc
        if completed.returncode:
            raise WorkspaceSafetyError("Workspace Git configuration cannot be parsed")

        sealed = bytearray()
        for record in completed.stdout.split(b"\0"):
            if not record:
                continue
            key, separator, _value = record.partition(b"\n")
            if not separator:
                raise WorkspaceSafetyError("Workspace Git configuration cannot be parsed")
            try:
                normalized_key = key.decode("ascii").casefold()
            except UnicodeDecodeError as exc:
                raise WorkspaceSafetyError(
                    "Workspace Git configuration contains an invalid key"
                ) from exc
            if normalized_key == "include.path" or (
                normalized_key.startswith("includeif.") and normalized_key.endswith(".path")
            ):
                raise WorkspaceSafetyError("Workspace Git configuration includes are not allowed")
            if normalized_key in _COMMIT_ATTRIBUTION_KEYS:
                continue
            sealed.extend(record)
            sealed.append(0)
        return bytes(sealed)

    async def _check_changed_paths(
        self,
        workspace: Path,
        allocation: WorkspaceAllocation,
        candidate_sha: str,
    ) -> None:
        raw = await self._git(
            workspace,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
            f"{allocation.base_sha}...{candidate_sha}",
        )
        allowed = tuple(PurePosixPath(item) for item in allocation.allowed_paths)
        for name in (item for item in raw.split("\x00") if item):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise WorkspaceSafetyError("Candidate contains an invalid changed path")
            if not any(path == grant or path.is_relative_to(grant) for grant in allowed):
                raise WorkspaceSafetyError(f"Candidate changed ungranted path: {name}")
            mode = (await self._git(workspace, "ls-tree", candidate_sha, "--", name)).split()
            if mode and mode[0] == "120000":
                target = await self._git(workspace, "show", f"{candidate_sha}:{name}")
                resolved = PurePosixPath(name).parent.joinpath(target)
                if target.startswith("/") or ".." in resolved.parts:
                    raise WorkspaceSafetyError(f"Candidate symlink escapes its checkout: {name}")

    async def _check_pinned_inputs(
        self,
        workspace: Path,
        candidate_sha: str,
        contract: _TestContract,
    ) -> None:
        for path, expected_blob in contract.pinned_inputs.items():
            actual = await self._git(workspace, "rev-parse", f"{candidate_sha}:{path}")
            if actual != expected_blob:
                raise WorkspaceSafetyError(f"Pinned verification input changed: {path}")

    async def _run_in_container(
        self,
        workspace: Path,
        candidate_sha: str,
        receipt_id: str,
        contract: _TestContract,
    ) -> tuple[int, bytes, bytes]:
        staging_parent = self._evidence_root / ".verification-workspaces"
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{receipt_id}-", dir=staging_parent))
        container_name = f"niuu-verify-{receipt_id}"
        original: BaseException | None = None
        try:
            await self._git(staging_parent, "clone", "--no-hardlinks", str(workspace), str(staging))
            await self._git(staging, "checkout", "--detach", candidate_sha)
            command = (
                self._container_binary,
                "run",
                "--rm",
                "--name",
                container_name,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec",
                "--memory",
                self._container_memory,
                "--cpus",
                str(self._container_cpus),
                "--pids-limit",
                str(self._container_pids_limit),
                "--mount",
                f"type=bind,src={staging},dst=/workspace,readonly",
                "--workdir",
                "/workspace",
                contract.image,
                *contract.argv,
            )
            return await self._run(command, staging_parent)
        except BaseException as exc:
            original = exc
            raise
        finally:
            await self._cleanup_container_run(container_name, staging, staging_parent, original)

    async def _cleanup_container_run(
        self,
        container_name: str,
        staging: Path,
        staging_parent: Path,
        original: BaseException | None,
    ) -> None:
        """Run both cleanup steps unconditionally so one failure never skips the other.

        A verification container leak or an orphaned staging clone are both real
        infrastructure problems, so a cleanup failure is never swallowed. If the
        verification itself already raised, that original exception is what
        propagates (a caller matching on WorkstreamGitError subtypes must not see
        its type change because cleanup also failed); the cleanup failure is
        chained onto it via `__cause__` so it is still visible in the traceback.
        If verification succeeded, the cleanup failure itself is raised.
        """
        cleanup_error: BaseException | None = None
        try:
            await self._run((self._container_binary, "rm", "-f", container_name), staging_parent)
        except BaseException as exc:  # noqa: BLE001 - deliberately typed below
            cleanup_error = exc
        try:
            shutil.rmtree(staging)
        except BaseException as exc:  # noqa: BLE001 - deliberately typed below
            if cleanup_error is not None:
                exc.__context__ = cleanup_error
            cleanup_error = exc
        if cleanup_error is None:
            return
        if original is not None:
            original.__cause__ = cleanup_error
            return
        raise cleanup_error

    async def _is_ancestor(self, repository: Path, base: str, candidate: str) -> bool:
        return await self._git_ok(repository, "merge-base", "--is-ancestor", base, candidate)

    async def _git_ok(self, cwd: Path, *arguments: str) -> bool:
        code, _, _ = await self._run(self._git_command(cwd, arguments), cwd)
        return code == 0

    async def _git(self, cwd: Path, *arguments: str) -> str:
        code, stdout, stderr = await self._run(self._git_command(cwd, arguments), cwd)
        if code:
            detail = stderr.decode("utf-8", errors="replace").strip()[:1000]
            raise WorkstreamGitError(f"Git operation failed ({code}): {detail}")
        return stdout.decode("utf-8", errors="strict").strip()

    def _git_command(self, cwd: Path, arguments: tuple[str, ...]) -> tuple[str, ...]:
        return (
            self._git_binary,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "-C",
            str(cwd),
            *arguments,
        )

    async def _pump_capped(self, stream: asyncio.StreamReader, sink: list[bytes]) -> None:
        """Read a stream incrementally, raising as soon as the running total exceeds the cap.

        Reading the whole stream via `communicate()` before checking a size limit
        lets a chatty, candidate-controlled process buffer arbitrarily much output
        in memory for up to the full command timeout. Checking after every chunk
        bounds memory to roughly one cap's worth per stream.
        """
        total = 0
        while chunk := await stream.read(self._output_read_chunk_bytes):
            sink.append(chunk)
            total += len(chunk)
            if total > self._max_output_bytes:
                raise _OutputCapExceededError()

    async def _run(self, arguments: tuple[str, ...], cwd: Path) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=str(cwd),
            env=self._runtime_environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def _consume() -> int:
            stdout_task = asyncio.ensure_future(self._pump_capped(process.stdout, stdout_chunks))
            stderr_task = asyncio.ensure_future(self._pump_capped(process.stderr, stderr_chunks))
            try:
                await asyncio.gather(stdout_task, stderr_task)
            finally:
                for task in (stdout_task, stderr_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            return await process.wait()

        try:
            returncode = await asyncio.wait_for(_consume(), self._timeout)
        except _OutputCapExceededError:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise WorkstreamGitError(
                "Delivery command output exceeded the configured limit"
            ) from None
        except (TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise WorkstreamGitError("Delivery command timed out") from None
        return returncode or 0, b"".join(stdout_chunks), b"".join(stderr_chunks)

    def _write_log(self, receipt_id: str, stdout: bytes, stderr: bytes) -> None:
        directory = (self._evidence_root / receipt_id).resolve()
        if not directory.is_relative_to(self._evidence_root):
            raise WorkspaceSafetyError("Evidence path escapes the evidence root")
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in (("stdout.log", stdout), ("stderr.log", stderr)):
            temporary = directory / f".{name}.tmp"
            destination = directory / name
            with temporary.open("wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
