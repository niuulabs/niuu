"""Checkout-only project setup: real Git discovery and authenticated API boundaries."""

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from volundr.adapters.inbound.rest_projects import create_projects_router
from volundr.adapters.outbound.project_workspace import GitProjectWorkspace
from volundr.domain.models import Principal
from volundr.domain.project_ports import ProjectConflictError
from volundr.domain.projects import ForgeProject
from volundr.domain.services.projects import ProjectNotFoundError


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "kit"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=QA",
            "-c",
            "user.email=qa@example.test",
            "commit",
            "--allow-empty",
            "-qm",
            "Initial",
        ],
        check=True,
    )
    return root


def remote(root, name="origin", url="git@github.com:nvidia-dev/df-project-kit.git"):
    subprocess.run(["git", "-C", str(root), "remote", "add", name, url], check=True)


async def test_remote_discovery_is_stable_and_leaves_checkout_unchanged(repo, tmp_path):
    remote(repo)
    (repo / "unrelated.txt").write_text("keep my edits")
    workspace = GitProjectWorkspace(allowed_prefixes=[str(tmp_path)])
    first = await workspace.discover(str(repo))
    assert first.name == "kit" and first.workspace_path == str(repo)
    assert first.repo_url == "https://github.com/nvidia-dev/df-project-kit"
    clone = tmp_path / "other-folder"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "remote", "set-url", "origin", first.repo_url], check=True
    )
    assert (await workspace.discover(str(clone))).id == first.id
    assert not (repo / "project.json").exists()
    assert (
        subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"]).strip()
        == b"?? unrelated.txt"
    )


async def test_manifest_identity_and_existing_repository_url_are_preserved(repo):
    remote(repo)
    identity = str(uuid4())
    (repo / "project.json").write_text(
        json.dumps(
            {
                "id": identity,
                "name": "Kit",
                "slug": "kit",
                "repo_url": "git@github.com:nvidia-dev/df-project-kit.git",
            }
        )
    )
    project = await GitProjectWorkspace().discover(str(repo))
    assert str(project.id) == identity and project.name == "Kit"
    assert project.repo_url.startswith("git@")


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        "[]",
        "{}",
        '{"id":"invalid"}',
        json.dumps({"id": str(uuid4()), "repo_url": "https://github.com/wrong/repository"}),
    ],
)
async def test_invalid_manifest_is_not_silently_replaced(repo, content):
    remote(repo)
    (repo / "project.json").write_text(content)
    with pytest.raises(ValueError, match="project.json"):
        await GitProjectWorkspace().discover(str(repo))


async def test_origin_wins_over_upstream_and_unique_non_origin_is_allowed(repo):
    remote(repo, "upstream", "https://github.com/upstream/project")
    workspace = GitProjectWorkspace()
    assert (await workspace.discover(str(repo))).repo_url.endswith("upstream/project")
    remote(repo)
    assert (await workspace.discover(str(repo))).repo_url.endswith("nvidia-dev/df-project-kit")


async def test_missing_ambiguous_and_credential_bearing_remotes_fail_closed(repo):
    workspace = GitProjectWorkspace()
    with pytest.raises(ValueError, match="no remote"):
        await workspace.discover(str(repo))
    remote(repo, "first", "https://github.com/a/one")
    remote(repo, "second", "https://github.com/b/two")
    with pytest.raises(ValueError, match="multiple remotes"):
        await workspace.discover(str(repo))
    remote(repo, "origin", "https://test-secret@github.com/a/one")
    with pytest.raises(ValueError, match="without embedded credentials") as error:
        await workspace.discover(str(repo))
    assert "test-secret" not in str(error.value)


async def test_discovery_rejects_relative_path_escaped_metadata_and_budget(repo, tmp_path):
    remote(repo)
    with pytest.raises(ValueError, match="absolute"):
        await GitProjectWorkspace().discover("relative")
    with pytest.raises(ValueError, match="outside"):
        await GitProjectWorkspace(allowed_prefixes=[str(tmp_path / "other")]).discover(str(repo))
    private = tmp_path / "private.json"
    private.write_text("{}")
    (repo / "project.json").symlink_to(private)
    with pytest.raises(ValueError, match="escape"):
        await GitProjectWorkspace().discover(str(repo))
    (repo / "project.json").unlink()
    (repo / "project.json").write_text("x" * 200)
    with pytest.raises(ValueError, match="context budget"):
        await GitProjectWorkspace(context_bytes=180).discover(str(repo))
    with pytest.raises(ValueError, match="Git metadata"):
        await GitProjectWorkspace(context_bytes=10).discover(str(repo))


async def test_git_failure_timeout_and_cancellation_reap_process(repo, monkeypatch):
    proc = AsyncMock()
    proc.returncode = 1
    proc.communicate.return_value = (b"", None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    workspace = GitProjectWorkspace()
    with pytest.raises(ValueError, match="Cannot inspect"):
        await workspace._git(repo, "remote", "-v")
    for error in [TimeoutError(), asyncio.CancelledError()]:
        proc.returncode = None
        proc.kill = lambda: setattr(proc, "returncode", -9)
        proc.communicate.side_effect = [error, (b"", None)]
        with pytest.raises(
            type(error) if isinstance(error, asyncio.CancelledError) else ValueError
        ):
            await workspace._git(repo, "remote", "-v")
        assert proc.returncode == -9


async def test_discovery_supports_git_worktree(repo, tmp_path):
    remote(repo)
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(worktree)], check=True)
    assert (await GitProjectWorkspace().discover(str(worktree))).repo_url.endswith("df-project-kit")


async def test_api_connect_discovers_and_retries_one_registration(rig):
    service, _, _, _, _ = rig
    detected = ForgeProject(
        slug="kit",
        name="kit",
        repo_url="https://github.com/nvidia-dev/df-project-kit",
        workspace_path="/home/horde/projects/kit",
    )
    service.workspace.discover.return_value = detected
    principal_for = AsyncMock(return_value=None)
    app = FastAPI()
    app.include_router(create_projects_router(service, principal_for))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        preview = await client.post(
            "/projects/discover", json={"workspace_path": detected.workspace_path}
        )
        assert preview.status_code == 200 and preview.json()["repo_url"] == detected.repo_url
        assert await service.repository.get(detected.id) is None
        for _ in range(2):
            result = await client.post(
                "/projects/connect", json={"workspace_path": detected.workspace_path, "name": "Kit"}
            )
            assert result.status_code == 201 and result.json()["id"] == str(detected.id)
            assert result.json()["name"] == "Kit"
        assert len([p for p in await service.list(None) if p.id == detected.id]) == 1
        invalid = await client.post("/projects/connect", json={"workspace_path": ""})
        assert invalid.status_code == 422
        service.workspace.discover.side_effect = ValueError("Not a Git checkout")
        failure = await client.post("/projects/discover", json={"workspace_path": "/bad"})
        assert failure.status_code == 422 and failure.json()["detail"] == "Not a Git checkout"
    assert principal_for.await_count == 4


async def test_discovery_obeys_existing_ownership_and_local_checkout_identity(rig):
    service, _, project, _, _ = rig
    service.workspace.discover.return_value = project
    outsider = Principal("other", "other@example.test", "different", [])
    with pytest.raises(ProjectNotFoundError):
        await service.discover("/projects", outsider)
    service.workspace.discover.return_value = project.model_copy(
        update={"workspace_path": "/elsewhere"}
    )
    with pytest.raises(ProjectConflictError):
        await service.discover("/elsewhere", None)
