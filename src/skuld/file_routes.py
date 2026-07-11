"""Workspace file-management and git-diff HTTP routes for Skuld."""

import asyncio
import logging
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from skuld.path_security import UnsafePathError, resolve_contained_path
from skuld.session_artifacts import _resolve_git_workspace_root

logger = logging.getLogger("skuld.file_routes")
router = APIRouter()


class _BrokerLike(Protocol):
    workspace_dir: str
    _settings: object


_broker_getter: Callable[[], _BrokerLike] | None = None


class _BrokerProxy:
    def __getattr__(self, name: str) -> object:
        if _broker_getter is None:
            raise RuntimeError("Skuld file routes have not been configured")
        return getattr(_broker_getter(), name)


broker = _BrokerProxy()


# --- Workspace File Listing API ---

# Directories that add noise and should be hidden from the file browser
_SKIP_NAMES = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        "venv",
        ".venv",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)

# Dotfiles/dotdirs that *should* be shown despite the general hidden-file rule
_SHOW_HIDDEN = frozenset({".github", ".claude", ".vscode"})


def _resolve_root(root: str) -> Path:
    """Return the resolved base directory for the given root name."""
    if root == "home":
        return Path(broker._settings.home_path).resolve()
    return Path(broker.workspace_dir).resolve()


def _resolve_user_path(
    base: str | Path,
    relative_path: str,
    *,
    allow_root: bool = True,
    strict: bool = False,
) -> Path:
    """Translate the shared path-boundary error into an HTTP 400 response."""
    try:
        return resolve_contained_path(
            base,
            relative_path,
            allow_root=allow_root,
            strict=strict,
        )
    except UnsafePathError as exc:
        raise HTTPException(400, "Path traversal not allowed") from exc


def _validate_root(root: str) -> None:
    """Validate root parameter is exactly 'workspace' or 'home'."""
    if root not in ("workspace", "home"):
        raise HTTPException(400, "root must be 'workspace' or 'home'")


@router.get("/api/files")
async def list_files(path: str = "", root: str = "workspace") -> dict:
    """List files and directories in a session root (workspace or home)."""
    _validate_root(root)
    base = _resolve_root(root)
    target = _resolve_user_path(base, path)
    if os.path.commonpath((os.fspath(base), os.fspath(target))) != os.fspath(base):
        raise HTTPException(400, "Path traversal not allowed")

    if not target.is_dir():
        raise HTTPException(404, "Directory not found")

    try:
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    entries: list[dict] = []
    for item in items:
        if item.name.startswith(".") and item.name not in _SHOW_HIDDEN:
            continue
        if item.name in _SKIP_NAMES:
            continue
        try:
            relative_item = item.relative_to(base).as_posix()
            canonical_item = _resolve_user_path(base, relative_item, strict=True)
            if os.path.commonpath((os.fspath(base), os.fspath(canonical_item))) != os.fspath(base):
                continue
        except HTTPException:
            # Hide symlinks that escape the selected root.
            continue
        stat = canonical_item.stat()
        entries.append(
            {
                "name": item.name,
                "path": str(item.relative_to(base)),
                "type": "directory" if canonical_item.is_dir() else "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )

    return {"entries": entries}


@router.get("/api/files/download")
async def download_file(path: str, root: str = "workspace") -> FileResponse:
    """Download a single file from the session."""
    _validate_root(root)
    base = _resolve_root(root)
    target = _resolve_user_path(base, path, allow_root=False)
    if os.path.commonpath((os.fspath(base), os.fspath(target))) != os.fspath(base):
        raise HTTPException(400, "Path traversal not allowed")

    if not target.is_file():
        raise HTTPException(404, "File not found")

    return FileResponse(
        path=target,
        filename=target.name,
        media_type="application/octet-stream",
    )


@router.post("/api/files/upload")
async def upload_files(
    files: list[UploadFile],
    path: str = "",
    root: str = "workspace",
) -> dict:
    """Upload files to a target directory in the session."""
    _validate_root(root)
    base = _resolve_root(root)
    target_dir = _resolve_user_path(base, path)
    if os.path.commonpath((os.fspath(base), os.fspath(target_dir))) != os.fspath(base):
        raise HTTPException(400, "Path traversal not allowed")

    if not target_dir.is_dir():
        raise HTTPException(404, "Target directory not found")

    max_size = broker._settings.max_upload_size_bytes
    uploaded: list[dict] = []
    for upload in files:
        if upload.filename is None:
            continue
        # Prevent path traversal in filenames
        safe_name = os.path.basename(upload.filename)
        destination = _resolve_user_path(target_dir, safe_name, allow_root=False)
        if os.path.commonpath((os.fspath(base), os.fspath(destination))) != os.fspath(base):
            raise HTTPException(400, "Path traversal not allowed")

        content = await upload.read()
        if len(content) > max_size:
            raise HTTPException(
                413,
                f"File {safe_name} exceeds maximum upload size ({max_size} bytes)",
            )
        with destination.open("wb") as destination_file:
            destination_file.write(content)
        stat = destination.stat()
        uploaded.append(
            {
                "name": safe_name,
                "path": destination.relative_to(base).as_posix(),
                "type": "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )

    return {"entries": uploaded}


@router.put("/api/files/upload")
async def upload_file_raw(
    request: Request,
    path: str,
    root: str = "workspace",
) -> dict:
    """Write raw request-body bytes to a single workspace-relative file.

    Raw-body mirror of ``download_file``: ``path`` is the destination *file*
    (not a directory), confined identically (reject absolute/.. + realpath guard).
    """
    _validate_root(root)
    base = _resolve_root(root)
    target = _resolve_user_path(base, path, allow_root=False)
    if os.path.commonpath((os.fspath(base), os.fspath(target))) != os.fspath(base):
        raise HTTPException(400, "Path traversal not allowed")
    if target.is_dir():
        raise HTTPException(400, "path must name a file, not a directory")

    body = await request.body()
    max_size = broker._settings.max_upload_size_bytes
    if len(body) > max_size:
        raise HTTPException(
            413,
            f"File exceeds maximum upload size ({max_size} bytes)",
        )

    parent = _resolve_user_path(base, target.parent.relative_to(base).as_posix())
    if os.path.commonpath((os.fspath(base), os.fspath(parent))) != os.fspath(base):
        raise HTTPException(400, "Path traversal not allowed")
    parent.mkdir(parents=True, exist_ok=True)

    with target.open("wb") as target_file:
        target_file.write(body)

    stat = target.stat()
    return {
        "name": target.name,
        "path": target.relative_to(base).as_posix(),
        "type": "file",
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


class MkdirRequest(BaseModel):
    path: str
    root: str = "workspace"


@router.post("/api/files/mkdir")
async def mkdir(body: MkdirRequest) -> dict:
    """Create a directory."""
    _validate_root(body.root)
    base = _resolve_root(body.root)
    target = _resolve_user_path(base, body.path, allow_root=False)
    if os.path.commonpath((os.fspath(base), os.fspath(target))) != os.fspath(base):
        raise HTTPException(400, "Path traversal not allowed")

    if target.exists():
        raise HTTPException(409, "Path already exists")

    try:
        target.mkdir(parents=True, exist_ok=False)
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    stat = target.stat()
    return {
        "name": target.name,
        "path": target.relative_to(base).as_posix(),
        "type": "directory",
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


@router.delete("/api/files")
async def delete_file(path: str, root: str = "workspace") -> dict:
    """Delete a file or directory."""
    _validate_root(root)
    if not path:
        raise HTTPException(400, "Cannot delete root directory")
    base = _resolve_root(root)
    target = _resolve_user_path(base, path, allow_root=False)
    if os.path.commonpath((os.fspath(base), os.fspath(target))) != os.fspath(base):
        raise HTTPException(400, "Path traversal not allowed")

    if not target.exists():
        raise HTTPException(404, "Path not found")

    try:
        if target.is_dir():
            shutil.rmtree(target)
            return {"deleted": target.relative_to(base).as_posix()}
        target.unlink()
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    return {"deleted": target.relative_to(base).as_posix()}


def _parse_diff_output(raw: str, file_path: str) -> dict:
    """Parse unified diff output into structured hunks."""
    hunks: list[dict] = []
    current_hunk: dict | None = None

    for line in raw.splitlines():
        # Hunk header: @@ -oldStart,oldCount +newStart,newCount @@
        if line.startswith("@@"):
            parts = line.split("@@")
            if len(parts) < 2:
                continue
            header = parts[1].strip()
            tokens = header.split()
            old_start, old_count = 0, 0
            new_start, new_count = 0, 0
            for token in tokens:
                if token.startswith("-"):
                    nums = token[1:].split(",")
                    old_start = int(nums[0])
                    old_count = int(nums[1]) if len(nums) > 1 else 1
                elif token.startswith("+"):
                    nums = token[1:].split(",")
                    new_start = int(nums[0])
                    new_count = int(nums[1]) if len(nums) > 1 else 1
            current_hunk = {
                "oldStart": old_start,
                "oldCount": old_count,
                "newStart": new_start,
                "newCount": new_count,
                "lines": [],
            }
            hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if line.startswith("+"):
            current_hunk["lines"].append(
                {
                    "type": "add",
                    "content": line[1:],
                    "newLine": new_start,
                }
            )
            new_start += 1
        elif line.startswith("-"):
            current_hunk["lines"].append(
                {
                    "type": "remove",
                    "content": line[1:],
                    "oldLine": old_start,
                }
            )
            old_start += 1
        elif line.startswith(" "):
            current_hunk["lines"].append(
                {
                    "type": "context",
                    "content": line[1:],
                    "oldLine": old_start,
                    "newLine": new_start,
                }
            )
            old_start += 1
            new_start += 1

    return {"filePath": file_path, "hunks": hunks}


@router.get("/api/diff")
async def get_diff(
    file: str = Query(..., description="File path relative to workspace"),
    base: str = Query(
        default="last-commit",
        description="Diff base: last-commit or default-branch",
    ),
) -> dict:
    """Return parsed git diff for a single file."""
    workspace = _resolve_git_workspace_root(broker.workspace_dir)
    target = _resolve_user_path(workspace, file, allow_root=False)
    safe_file = target.relative_to(workspace).as_posix()

    if base == "last-commit":
        cmd = ["git", "diff", "HEAD", "--", safe_file]
    elif base == "default-branch":
        cmd = ["git", "diff", "main...HEAD", "--", safe_file]
    else:
        raise HTTPException(400, f"Invalid base: {base}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        raise HTTPException(504, "git diff timed out")

    if proc.returncode not in (0, 1):
        detail = stderr.decode(errors="replace").strip()
        logger.warning(
            "git diff failed for %s: %s",
            repr(safe_file),
            repr(detail),
        )
        raise HTTPException(502, f"git diff failed: {detail}")

    raw = stdout.decode(errors="replace")
    return _parse_diff_output(raw, safe_file)


@router.get("/api/diff/files")
async def get_diff_files(
    base: str = Query(
        default="last-commit",
        description="Diff base: last-commit or default-branch",
    ),
) -> dict:
    """Return list of changed files with insertion/deletion counts."""
    workspace = _resolve_git_workspace_root(broker.workspace_dir)

    if base == "last-commit":
        cmd = ["git", "diff", "HEAD", "--numstat"]
    elif base == "default-branch":
        cmd = ["git", "diff", "main...HEAD", "--numstat"]
    else:
        raise HTTPException(400, f"Invalid base: {base}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        raise HTTPException(504, "git diff timed out")

    if proc.returncode not in (0, 1):
        detail = stderr.decode(errors="replace").strip()
        logger.warning("git diff --numstat failed: %s", repr(detail))
        raise HTTPException(502, f"git diff failed: {detail}")

    files = []
    for line in stdout.decode(errors="replace").strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        ins_str, del_str, path = parts
        ins = int(ins_str) if ins_str != "-" else 0
        del_ = int(del_str) if del_str != "-" else 0
        files.append({"path": path, "status": "mod", "ins": ins, "del": del_})

    return {"files": files}


def register_file_routes(app: FastAPI, broker_getter: Callable[[], _BrokerLike]) -> None:
    """Bind broker state and mount the cohesive file-management route group."""
    global _broker_getter
    _broker_getter = broker_getter
    app.include_router(router)
