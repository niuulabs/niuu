"""Bounded project context and atomic handoff files in an existing Git checkout."""

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from volundr.domain.project_ports import ProjectWorkspace
from volundr.domain.projects import ForgeProject, ProjectReceipt


class GitProjectWorkspace(ProjectWorkspace):
    def __init__(self, allowed_prefixes=(), context_bytes=8192, git_timeout=15.0, **_kwargs):
        self._prefixes = tuple(Path(p).resolve() for p in allowed_prefixes)
        self._context_bytes = context_bytes
        self._git_timeout = git_timeout

    def _root(self, project: ForgeProject) -> Path:
        return self._checkout_root(project.workspace_path)

    def _checkout_root(self, workspace_path: str) -> Path:
        if not workspace_path:
            raise ValueError("Project has no local checkout on this host")
        if not Path(workspace_path).is_absolute():
            raise ValueError("Use an absolute project folder path")
        root = Path(workspace_path).resolve()
        if not root.is_dir() or not (root / ".git").exists():
            raise ValueError("Project workspace must be an existing Git checkout")
        if self._prefixes and not any(root.is_relative_to(prefix) for prefix in self._prefixes):
            raise ValueError("Project checkout is outside the allowed workspace roots")
        return root

    @staticmethod
    def _canonical_remote(remote: str) -> str:
        # Validate before returning anything to the caller; credential-bearing
        # remotes must never become public project metadata or error text.
        ForgeProject.validate_repository_url(remote)
        value = remote
        if value.startswith("git@"):
            host, path = value[4:].split(":", 1)
            value = f"https://{host}/{path}"
        url = urlsplit(value)
        path = url.path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if not path or path == "/":
            raise ValueError("Git remote must identify a repository")
        if url.hostname == "github.com":
            path = path.lower()
        return urlunsplit(("https", url.netloc.lower(), path, "", ""))

    async def _git(self, root: Path, *arguments: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(root),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self._git_timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ValueError("Project Git lookup timed out") from None
        if proc.returncode:
            raise ValueError("Cannot inspect Git in this project folder")
        if len(out) > self._context_bytes:
            raise ValueError("Project Git metadata exceeds the configured context budget")
        return out.decode("utf-8").strip()

    async def discover(self, workspace_path: str) -> ForgeProject:
        root = self._checkout_root(workspace_path)
        output = await self._git(root, "remote", "-v")
        remotes: dict[str, set[str]] = {}
        for line in output.splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[2] == "(fetch)":
                remotes.setdefault(fields[0], set()).add(fields[1])
        choices = remotes.get("origin")
        if choices is None:
            choices = set().union(*remotes.values()) if remotes else set()
        if not choices:
            raise ValueError("This Git checkout has no remote; configure origin and retry")
        if len(choices) != 1:
            raise ValueError("This checkout has multiple remotes; configure one origin remote")
        try:
            remote = self._canonical_remote(next(iter(choices)))
        except ValueError:
            raise ValueError(
                "Use an HTTPS or git@ Git remote without embedded credentials"
            ) from None

        manifest = (root / "project.json").resolve()
        if not manifest.is_relative_to(root):
            raise ValueError("Project metadata cannot escape its checkout")
        metadata = {}
        if manifest.exists():
            if not manifest.is_file() or manifest.stat().st_size > self._context_bytes:
                raise ValueError("project.json exceeds the configured context budget")
            try:
                metadata = json.loads(await asyncio.to_thread(manifest.read_text, encoding="utf-8"))
                if not isinstance(metadata, dict) or "id" not in metadata:
                    raise ValueError("missing project identity")
                UUID(metadata["id"])
                if (
                    "repo_url" in metadata
                    and self._canonical_remote(metadata["repo_url"]) != remote
                ):
                    raise ValueError("repository mismatch")
            except (ValueError, TypeError, AttributeError):
                raise ValueError(
                    "project.json must contain a valid project UUID and matching repository"
                ) from None

        name = metadata.get("name", root.name)
        slug = metadata.get("slug", re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-")[:63])
        project = ForgeProject(
            id=metadata.get("id", uuid5(NAMESPACE_URL, "forge-project:" + remote)),
            name=name,
            slug=slug,
            description=metadata.get("description", ""),
            repo_url=metadata.get("repo_url", remote),
            workspace_path=str(root),
        )
        # Preview must validate the same checkout and context that registration uses.
        await self.context(project)
        return project

    def _read_bounded(self, path: Path) -> bytes:
        # Bound the read itself, even if a file grows after the initial size check.
        with path.open("rb") as stream:
            return stream.read(self._context_bytes + 1)

    async def _context_files(self, root: Path) -> tuple[tuple[str, ...], bool]:
        """Repository-selected instructions, not a Forge role/workflow template.

        Legacy repositories retain the optional PROJECT/CURRENT snapshot. An explicit
        list is required in full: silently omitting a declared instruction is unsafe.
        """
        defaults = ("PROJECT.md", "context/CURRENT.md")
        manifest = (root / "project.json").resolve()
        if not manifest.is_relative_to(root):
            raise ValueError("Project metadata cannot escape its checkout")
        if not manifest.exists():
            return defaults, False
        if not manifest.is_file() or manifest.stat().st_size > self._context_bytes:
            raise ValueError("project.json exceeds the configured context budget")
        try:
            raw = await asyncio.to_thread(self._read_bounded, manifest)
            if len(raw) > self._context_bytes:
                raise ValueError("manifest budget")
            metadata = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ValueError("project.json must contain bounded UTF-8 JSON") from None
        if not isinstance(metadata, dict):
            raise ValueError("project.json must contain a JSON object")
        if "context_files" not in metadata:
            return defaults, False
        names = metadata["context_files"]
        if not isinstance(names, list) or not 1 <= len(names) <= 16:
            raise ValueError("context_files must contain between 1 and 16 relative file paths")
        for name in names:
            if (
                not isinstance(name, str)
                or not name
                or len(name) > 255
                or "\\" in name
                or any(ord(char) < 32 for char in name)
                or Path(name).is_absolute()
                or any(part in ("", ".", "..", ".git") for part in name.split("/"))
            ):
                raise ValueError("context_files must contain safe checkout-relative file paths")
        if len(set(names)) != len(names):
            raise ValueError("context_files cannot contain duplicate paths")
        return tuple(names), True

    async def context(self, project: ForgeProject) -> tuple[str, str]:
        root = self._root(project)
        files, required = await self._context_files(root)
        chunks = []
        for name in files:
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Project context cannot escape its checkout")
            if ".git" in path.relative_to(root).parts:
                raise ValueError("Project context cannot include Git metadata")
            if not path.is_file():
                if required:
                    raise ValueError(f"Required project context file is missing: {name}")
                continue
            if path.stat().st_size > self._context_bytes:
                raise ValueError(
                    f"{name} exceeds the project context budget; shorten the checkpoint"
                )
            raw = await asyncio.to_thread(self._read_bounded, path)
            if len(raw) > self._context_bytes:
                raise ValueError(
                    f"{name} exceeds the project context budget; shorten the checkpoint"
                )
            try:
                content = raw.decode("utf-8")
            except UnicodeError:
                raise ValueError(f"Project context must be UTF-8 text: {name}") from None
            chunks.append(f"## {name}\n{content}")
        context = "\n\n".join(chunks)
        if len(context.encode()) > self._context_bytes:
            raise ValueError("Project context exceeds its budget; shorten the checkpoint")
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(root),
            "rev-parse",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self._git_timeout)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            raise ValueError("Project Git revision lookup timed out") from None
        if proc.returncode:
            raise ValueError("Project checkout has no readable Git revision")
        # Include the content hash: coordinators may have a checkpoint staged
        # but not committed yet. Never label dirty content as the Git commit alone.
        digest = hashlib.sha256(context.encode()).hexdigest()
        return context, f"{out.decode().strip()}@sha256:{digest}"

    async def archive_receipt(self, project: ForgeProject, receipt: ProjectReceipt) -> None:
        root = self._root(project)

        def write():
            directory = (root / "history" / "handoffs").resolve()
            if not directory.is_relative_to(root):
                raise ValueError("Project history cannot escape its checkout")
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{receipt.id}.json"
            content = json.dumps(receipt.model_dump(mode="json"), indent=2) + "\n"
            descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".handoff-")
            try:
                with os.fdopen(descriptor, "w") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

        await asyncio.to_thread(write)
